# Dify 问题与待办清单

> 用于持续记录 Dify 部署、功能、接口、配置和运行时问题。每个问题都应保留发现时间、影响范围、复现证据、根因判断、修复方案和验收结果，便于后续开发和回归验证。

## 记录规范

- **记录时间**：使用 `Asia/Shanghai`，写明首次发现或确认时间。
- **状态**：`待排查`、`已定位`、`待开发`、`开发中`、`待验证`、`已完成`、`暂不处理`。
- **问题描述**：说明用户可见现象和涉及的页面、接口或功能。
- **复现信息**：记录版本、环境、请求条件、关键参数名和可重复步骤；禁止写入真实密码、JWT、API Key、Cookie 等敏感值。
- **证据**：区分已实测结果、日志证据和推断，不把推断写成已确认事实。
- **修复方案**：写清修改边界、调用链影响和不应改变的行为。
- **验收标准**：列出可执行的接口、页面或回归检查。
- **后续检查清单**：用复选框跟踪开发进度。

## 问题索引

| 编号 | 问题 | 状态 | 记录时间 |
| --- | --- | --- | --- |
| DIFY-001 | MCP 动态 Header 导致工具列表刷新失败 | 待验证 | 2026-08-12 |

---

## DIFY-001：MCP 动态 Header 导致工具列表刷新失败

## 记录信息

- 记录时间：2026-08-12（Asia/Shanghai）
- 状态：待验证，已完成源码修改和定向测试，尚未构建、部署及执行生产接口验证
- Dify 部署版本：`1.16.1-company.full`
- MCP Provider ID：`6c0ff042-270f-4d35-9141-22d6c44ea891`
- MCP 服务地址：`https://mcp.yunluepro.com`
- 关联旧 Agent：`4678b6fe-f229-4edb-9253-a03cb5390eac`

## 业务背景

该 MCP 使用两类请求：

1. MCP 初始化和 `list_tools`：不要求业务认证。
2. 具体业务工具调用：要求调用方提供业务身份和链路信息。

Provider 中配置的运行时动态 Header 为：

```text
X-Biz-Token: Bearer {{request.headers.X-Biz-Token}}
X-Trace-Id: {{request.headers.X-Trace-Id}}
```

其中业务 Token 不应持久化到 Dify Provider 配置，而应由每次 Agent API 请求的 Header 注入。

## 问题现象

控制台 MCP 配置页面点击“更新”工具列表后显示 `Internal Server Error`，无法刷新工具定义。

对应接口：

```http
GET /console/api/workspaces/current/tool-provider/mcp/update/{provider_id}
```

MCP 工具调用事件中出现：

```text
Authentication retry failed: Failed to discover OAuth metadata from server
```

## 已确认的证据

### 1. MCP 服务本身允许未认证的工具发现

在 Dify API 容器内，使用不携带任何 Header 的 `MCPClient` 连接目标地址并调用 `list_tools`，成功返回 `23` 个工具。

结论：工具列表刷新不需要业务 Token，也不需要 OAuth。

### 2. 当前刷新路径错误地发送了空认证 Header

`core/mcp/mcp_client.py` 的 `MCPClient.__init__` 会匹配并替换：

```python
r"\{\{\s*request\.headers?\.(.+?)\s*\}\}"
```

只有 Flask request context 存在时，才能从 `request.headers` 读取实际值。控制台刷新请求没有业务 Header，动态表达式会被替换为空字符串，或在没有可用上下文的执行链路中保留为不可用值。

这导致刷新请求向 MCP 服务发送类似以下无效 Header：

```text
X-Biz-Token: Bearer <empty>
X-Trace-Id: <empty>
```

注意：目标 MCP 对“完全不传认证 Header”和“传空认证 Header”的处理不同。前者允许发现工具，后者触发认证挑战；MCP SDK 再尝试 OAuth 元数据发现，最终报错。

### 3. 刷新接口的对照测试

对同一 Provider 的刷新接口实测：

| 请求条件 | 结果 |
| --- | --- |
| 仅控制台会话 Header，不含 `X-Biz-Token`、`X-Trace-Id` | HTTP `500` |
| 额外提供有效业务 `X-Biz-Token`、`X-Trace-Id` | HTTP `200` |

该对照证明目前的控制台刷新路径会错误依赖运行时业务 Header；并不意味着 `list_tools` 本身需要认证。

## 根因

Provider 只有一份通用 Header 配置，当前 Dify 在两个语义不同的流程中复用了它：

1. 控制台工具发现/刷新。
2. Agent 运行时的受保护业务工具调用。

动态 Header 仅对第二类流程有意义。第一类流程不具备用户请求上下文，也不应发送用户 Token。复用同一份 Header 后，动态模板被解析为空值并作为空认证 Header 发出，导致 MCP 服务进入错误的认证分支。

## 推荐修复方案

只在“发现/刷新工具列表”调用路径中，在创建 `MCPClient` 前过滤动态 Header；运行时调用路径保持现状，不得过滤。

建议定义可复用的模板识别函数：

```python
REQUEST_HEADER_TEMPLATE = re.compile(
    r"\{\{\s*request\.headers?\..+?\s*\}\}",
    re.IGNORECASE,
)


def get_discovery_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        name: value
        for name, value in headers.items()
        if not REQUEST_HEADER_TEMPLATE.search(value)
    }
```

调用边界应严格如下：

| 调用场景 | Header 处理 |
| --- | --- |
| 控制台 `mcp/update/{provider_id}` 刷新工具 | 传入 `get_discovery_headers(headers)` |
| MCP Provider 编辑、保存、数据库加密存储 | 保留原始动态模板，不改变配置值 |
| Agent 运行时调用 MCP 工具 | 传完整 Header 配置，按入站请求 Header 解析动态模板 |

不要在 `MCPClient.__init__` 做全局过滤。否则 Agent 运行时会失去 `X-Biz-Token` 和 `X-Trace-Id` 透传能力。

## 实现注意事项

1. 必须在替换动态占位符之前过滤。仅过滤空字符串不够，因为 `Bearer {{request.headers.X-Biz-Token}}` 会变成 `Bearer `，仍会触发认证错误。
2. 静态 Header 必须保留。例如某些 MCP 使用服务级 API Key，刷新列表仍需要该静态凭证。
3. 模板识别应支持 `request.header` 和 `request.headers` 两种写法，且大小写不敏感，与现有解析正则一致。
4. 不要在文档、日志、测试代码或数据库中记录真实用户 JWT。
5. 本待办不解决旧 Agent 异步工具执行链路中的 request context 传递问题。该问题需要独立排查：运行时应确认动态 Header 最终是否得到真实值，而不是空值。

## 验收标准

1. 仅配置上述两个动态 Header 时，控制台点击“更新”返回 HTTP `200`，并显示/刷新 `23` 个工具。
2. 同时配置静态 Header 与动态 Header 时，刷新工具列表仅发送静态 Header。
3. Agent 调用 `/v1/chat-messages` 时提供：

   ```http
   X-Biz-Token: <业务 Token>
   X-Trace-Id: <链路 ID>
   ```

   受保护 MCP 工具收到对应的运行时 Header。
4. Agent 请求缺少业务 Header 时，返回明确的“缺少业务认证 Header”错误；不得误导为 OAuth 元数据发现错误。
5. 不使用动态 Header 的静态认证 MCP Provider，工具刷新和运行时调用均无回归。

## 后续开发检查清单

- [x] 定位控制台 `mcp/update/{provider_id}` 对应的 Resource/Service。
- [x] 在 `MCPToolManageService.list_provider_tools` 的发现路径过滤动态 Header。
- [x] 添加定向服务测试：动态 Header 被过滤，静态 Header 与 OAuth Header 保留。
- [ ] 构建公司 API 镜像后，调用实际刷新接口验证目标 Provider 在无业务 Header 下返回 `200` 并保留 23 个工具。
- [ ] 验证 Agent 运行时 Header 透传，不在此次修复中被回归破坏。
- [ ] 单独建立并排查“异步 Agent 工具执行的 request context/Header 传递”问题。

## 2026-08-14 开发记录

- 修改文件：`api/services/tools/mcp_tools_manage_service.py`。
- 改动：`list_provider_tools()` 在创建用于工具发现的 MCP Client 前，过滤匹配 `{{request.header.*}}` 或 `{{request.headers.*}}` 的 Header 值；不修改通用 `MCPClient` 和运行时工具调用路径。
- 新增测试：`api/tests/unit_tests/services/tools/test_mcp_tools_manage_service.py`。
- 已验证：服务级测试在临时容器中通过；运行时 `MCPClient` 的动态 Header 替换冒烟测试通过；Python 语法编译和 `git diff --check` 通过。
- 未执行：镜像构建、生产部署和控制台实际刷新接口验证。以上操作属于生产变更，需单独确认后执行。
