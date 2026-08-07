# Dify 公司版源码构建与持续升级方案

本文用于维护公司版 Dify Fork，覆盖官方版本同步、公司补丁、镜像构建、数据迁移、生产切换和回滚。历史上的 `1.14.0 -> 1.16.1` 双版本迁移过程见《dify-双版本并行升级问题总结.md》。

## 1. 当前基线与发布原则

| 项目 | 当前约定 |
|---|---|
| Dify Core Fork | `D:\CodexProjects\dify\core` |
| 公司维护分支 | `company` |
| 可写远程 | `origin`：公司 Fork |
| 只读上游 | `upstream`：`langgenius/dify` |
| 当前生产版本 | `1.16.1` 公司构建镜像 |
| 生产入口 | `http://192.168.40.6:18080` |
| 业务中间件 | 独立 PostgreSQL、Redis、Weaviate |

必须遵守以下原则：

1. 只基于官方发布 Tag 升级，不直接合并上游 `main`。
2. 公司定制必须进入 Fork 提交，不在生产容器内手工修改源码。
3. 镜像使用不可变版本号和 digest，不使用 `latest`。
4. Compose、环境变量和数据迁移分别管理；密钥不得进入 Git、文档、日志或聊天记录。
5. 测试环境与生产环境使用不同的 Compose 项目名、端口、网络、数据库和数据目录。
6. 生产变更前先做只读检查，说明影响和回滚方式，并获得负责人确认。

## 2. Git 分支与版本升级

`company` 是长期维护分支，每次发布创建不可移动的发布标签，例如 `release/1.16.2-company.0`。

首次配置冲突解决记录复用：

```bash
git config rerere.enabled true
git config rerere.autoupdate true
```

升级到新的官方 Tag：

```bash
git fetch upstream --tags
git switch company
git status                    # 必须为空
git merge --no-ff refs/tags/1.16.2 -m "merge upstream Dify 1.16.2"

# 逐项解决冲突并完成测试后
git tag -a release/1.16.2-company.0 -m "Dify 1.16.2 company release 0"
git push origin company release/1.16.2-company.0
```

公司补丁按功能拆分提交，并在提交或 PR 中记录测试结果。未合并的独立修复才使用 `cherry-pick`，验证后应回到 `company`，避免形成多条不可追溯的生产分支。

## 3. 公司定制与插件管理

### 3.1 Dify Core 定制

| 定制 | 维护要求 | 最小验收 |
|---|---|---|
| Agent runner trace | 保持为可配置扩展，失败不能阻断主请求 | AgentChat、CoT、工具循环 trace 不重不漏 |
| `llm_trace_hook.py` | 对敏感字段脱敏，Kafka 异常可降级 | API 与 Worker 路径均验证 |
| Workflow SSE 空闲超时 | 随上游事件协议变化重新审阅 | 长工作流、断线重连和超时验证 |

旧版本文件只能用于理解业务意图，禁止直接覆盖新版同名文件。升级时应基于目标版本源码重新移植最小差异。

### 3.2 Marketplace 插件定制

Anthropic、Moonshot 等插件不属于 Dify Core Fork。插件定制应在独立仓库中保存：

- 上游版本或 commit；
- 原始安装包与定制安装包 SHA-256；
- 可重放的 patch/overlay；
- Provider 标识兼容性说明；
- 凭据保存、模型列表、流式请求和工具调用测试结果。

不得把 Plugin Daemon 的运行目录、`@hash` 安装路径、`.venv`、API Key 或缓存提交到 Git。不要直接修改生产 volume；应生成可安装、可回滚的定制插件包。

当前需重点回归：Anthropic 自定义模型参数与 cache token 链路、Moonshot K3 凭据校验和 `429 engine_overloaded` 有限重试。若上游已实现同类修复，应删除对应定制，而不是重复叠加。

### 3.3 Agent Backend 安全配置

启用 Agent Backend 时，`DIFY_AGENT_API_TOKEN` 与 `AGENT_BACKEND_API_TOKEN` 必须使用同一个随机非默认值，并仅存放在受控环境文件中。Agent Backend、Sandbox 和 SSRF Proxy 必须保留专用网络隔离。

## 4. 镜像构建与发布包

Dify 的 API、Web 和 Agent Backend 镜像均以仓库根目录为 build context：

```bash
cd /mnt/d/CodexProjects/dify/core

docker buildx build --platform linux/amd64 --load \
  -f api/Dockerfile -t company/dify-api:1.16.2-company.0 .

docker buildx build --platform linux/amd64 --load \
  -f web/Dockerfile -t company/dify-web:1.16.2-company.0 .

docker buildx build --platform linux/amd64 --load \
  -f dify-agent/Dockerfile -t company/dify-agent-backend:1.16.2-company.0 .
```

`worker` 和 `worker_beat` 使用同一 API 镜像。构建前通过 `uname -m` 确认服务器架构，避免生成不匹配的镜像。

没有内部镜像仓库时，将镜像导出为离线包：

```bash
docker save \
  company/dify-api:1.16.2-company.0 \
  company/dify-web:1.16.2-company.0 \
  company/dify-agent-backend:1.16.2-company.0 \
  | gzip > dify-1.16.2-company.0.tar.gz

sha256sum dify-1.16.2-company.0.tar.gz > SHA256SUMS
```

发布包至少包含：镜像及 digest、SHA-256、Compose 差异、环境变量名称清单（不含值）、数据库迁移版本、插件清单、测试报告和回滚说明。

## 5. 升级前备份与隔离演练

生产升级前必须备份并校验：

- PostgreSQL 核心库 `dify` 和插件库 `dify_plugin`；
- Weaviate 数据；
- App storage；
- Plugin Daemon 数据和插件包；
- Compose、`.env`、网关配置和镜像清单。

PostgreSQL 使用逻辑备份，示例：

```bash
docker exec dify-infra-db_postgres-1 \
  pg_dump -U postgres -Fc --no-owner --no-privileges dify > dify.dump

docker exec dify-infra-db_postgres-1 \
  pg_dump -U postgres -Fc --no-owner --no-privileges dify_plugin > dify_plugin.dump

sha256sum dify.dump dify_plugin.dump > SHA256SUMS
```

演练环境必须使用独立数据副本。不要让测试 API、Worker 或 Plugin Daemon 连接生产 PostgreSQL、Redis、Weaviate 或生产数据目录。Redis 缓存通常重建；任务队列必须在切换前排空或明确接受重试。Weaviate 最终复制应在写入冻结后完成，或使用一致性存储快照。

## 6. Docker 网络要求

所有 Compose 网络必须显式配置子网，并在创建前与以下信息核对：

- 宿主机 `ip route`；
- 办公网、VPN、云 VPC 和调用方网段；
- 已存在的 Docker、Kubernetes、WSL 网络。

后续新网络优先从运维预留的 `10.200.0.0/16`、`10.201.0.0/16` 中划分 `/24`。`10.x` 也可能冲突，使用前仍需核对。当前正在运行的 `10.250.x.x` 网络不得为了统一规划而直接改动。

不同 Compose 项目不得共享带有 `api`、`worker`、`plugin_daemon`、`redis`、`db_postgres` 等通用别名的网络，除非该共享是经过设计的单一中间件入口。

## 7. 数据迁移与生产切换

生产切换按以下顺序执行：

1. 完成隔离演练和验收，确定维护窗口、负责人和回滚条件。
2. 检查磁盘、内存、Docker、数据库、Redis 队列和 Weaviate 健康状态。
3. 停止入口流量和所有写服务，等待或终止在途任务。
4. 生成最终 PostgreSQL dump；先恢复到临时库，核对 schema、迁移版本和关键表行数后再切换正式库名。
5. 完成 App storage、Plugin Daemon 和 Weaviate 的最终同步。
6. 更新 Compose/.env 后只重建受影响的服务，不删除数据卷。
7. 验证通过后将稳定入口切到新版，调用方地址保持不变。

关键数据至少核对：账号、租户/工作空间、应用、工作流、会话、消息、知识库、文档、插件安装记录和工作流运行记录。表数量一致不等于数据一致，应对关键表比较行数并抽样业务对象。

## 8. 验收清单

- 登录、Token 刷新、工作空间列表和成员权限；
- 应用列表、DSL 导入导出、发布与 Service API；
- Workflow/Chatflow 的同步、流式、长任务和失败路径；
- Worker、Worker Beat、Plugin Daemon 和 Agent Backend；
- 知识库检索、新文档入库和 Weaviate 就绪状态；
- 已启用模型供应商的凭据保存与真实短调用；
- 自定义 trace、超时补丁和插件定制；
- 网关、WebSocket、SSE、外部调用方和数据库只读访问；
- 容器健康、错误日志、CPU、内存和磁盘余量。

## 9. 回滚与清理

镜像降级不能自动回滚数据库 schema。回滚必须同时考虑应用镜像、Compose、数据库快照、向量数据和插件状态。

切换失败时应停止新写入、恢复切换前配置与数据，并将网关上游切回已验证的回滚环境。旧环境下线后产生的新数据不会自动同步回旧环境，回滚前必须明确数据取舍。

观察期结束前不要删除旧 Compose、环境文件、数据库/向量备份和上一版镜像。禁止使用 `docker system prune -a --volumes` 做生产清理；应先盘点容器挂载和镜像引用，再逐项删除。

## 10. 1.16.1 相对 1.14 的主要变化

| 方向 | 主要变化 |
|---|---|
| CLI | 增加 `difyctl`，便于脚本和 CI 调用应用及工作流 |
| 工作流 | 节点定位、长任务轮询、工具多选输入和 HITL 文件表单增强 |
| Agent | 增加 Agent Backend、专用 Sandbox/SSRF 网络和令牌认证 |
| 知识库 | Excel 图片提取及知识处理、检索链路追踪增强 |
| 可观测性 | Workflow/Chatflow 推理过程和 Phoenix trace session 能力增强 |
| 稳定性 | 首页、协作草稿、重连和插件安装链路得到修复 |

具体行为以目标版本发布说明和实际验收为准：

- <https://github.com/langgenius/dify/releases/tag/1.15.0>
- <https://github.com/langgenius/dify/releases/tag/1.16.1>
