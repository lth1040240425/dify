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

### 1.1 固定目录与发布证据

| 用途 | 路径/要求 |
|---|---|
| Dify Core Fork | `D:\CodexProjects\dify\core` |
| 插件源码与上游基线 | `D:\CodexProjects\dify\plugins` |
| 本机构建产物 | `D:\WSL\dify-artifacts\<release>` |
| 当前新版 Compose | `/root/dify-company-1.16.1` |
| 独立中间件 Compose | `/opt/dify-infra` |
| 前置网关 | `/root/dify-edge-gateway` |
| 发布备份 | 使用带版本和时间的独立目录，禁止覆盖上一版 |

每个发布必须能够追溯到以下证据：Git commit、官方 Tag、公司发布 Tag、镜像 digest、Compose 差异、数据库迁移 revision、插件版本及包 hash、网络分配、备份校验值、验收结果和回滚负责人。缺少其中任一关键项时不得进入生产切换。

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

| 定制文件/能力 | 维护要求 | 最小验收 |
|---|---|---|
| `core/agent/fc_agent_runner.py` | 保留函数调用 Agent trace 逻辑 | AgentChat 工具调用产生一次完整 trace |
| `core/agent/cot_agent_runner.py` | 保留 CoT/工具循环 trace 逻辑 | 多轮工具循环 trace 不重不漏 |
| `core/agent/llm_trace_hook.py` | 可配置、敏感字段脱敏、Kafka 异常可降级 | API 与 Worker 路径均验证 |
| `services/workflow_event_snapshot_service.py` | 随上游 SSE 事件协议重新审阅空闲超时 | 长工作流、断线重连和超时验证 |

旧版本文件只能用于理解业务意图，禁止直接覆盖新版同名文件。升级时应基于目标版本源码重新移植最小差异。

### 3.2 Marketplace 插件定制

Anthropic、Moonshot 等插件不属于 Dify Core Fork。插件定制应在独立仓库中保存：

- 上游版本或 commit；
- 原始安装包与定制安装包 SHA-256；
- 可重放的 patch/overlay；
- Provider 标识兼容性说明；
- 凭据保存、模型列表、流式请求和工具调用测试结果。

当前需要保留的现场基线：

| 插件 | 基线版本 | 公司定制 | 升级风险 |
|---|---:|---|---|
| Anthropic | `0.3.25` | 自定义 Opus 4.8 模型参数、自适应推理及 cache token 链路 | 插件重装后 `@hash` 路径变化；不能依赖旧挂载路径 |
| Moonshot | `0.1.11` | Kimi K3 凭据校验、`429 engine_overloaded` 有限退避 | 直接修改 volume 会在重装或升级时丢失 |

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

## 10. 易错项与硬约束

| 易错点 | 必须遵守的约束 |
|---|---|
| Docker build context 选错 | API、Web、Agent Backend 都以仓库根目录为 build context，不能在子目录直接构建 |
| 复制旧版 `.env`/Compose 覆盖新版 | 以目标版本示例为基线逐项合并业务值；保留新版服务和安全变量 |
| Compose 项目名不一致 | 所有命令显式使用正确项目名；执行前先用 `docker compose ls`、`docker ps` 核对目标 |
| 共享 `api`、`redis`、`db_postgres` 等别名 | 只有同一设计边界内的服务才能共享网络；新旧栈必须隔离 |
| `REDIS_HOST=redis` 命中错误容器 | 使用独立且唯一的网络别名，并在 API、Worker、Plugin Daemon 内分别验证解析结果 |
| 认为 `pg_dump` 会备份数据库用户 | 数据库级 dump 不包含 PostgreSQL 全局角色；角色和授权需单独导出或重建 |
| 只比较表数量 | 还要比较迁移 revision、关键表行数、租户成员关系并抽样业务对象 |
| 在线复制 Weaviate 目录 | 热拷贝可能不一致；最终复制必须冻结写入或使用一致性快照 |
| 直接复制 Redis 数据 | 旧缓存和 Celery/Kombu 队列可能不兼容；先排空队列，缓存优先重建 |
| 直接修改 Plugin Daemon volume | `@hash` 路径随重装变化；定制必须生成可重放 patch 或安装包 |
| 只用 IP 的不同端口隔离新旧登录 | Cookie 不区分端口；并行期必须使用不同主机名或浏览器配置 |
| 让 Docker 自动分配网段 | 新建网络前核对真实路由并显式指定子网，避免覆盖办公网/VPN |
| 为单个服务故障停止 Docker daemon | 只重建受影响的 Compose 服务；停止 Docker 会中断本机全部容器应用 |
| 直接清理旧卷和镜像 | 先核对挂载和镜像引用，禁止 `docker system prune -a --volumes` |
| 未经确认修改生产 | 停服、数据库、Compose、网络、防火墙和删除操作必须先说明影响与回滚并获得确认 |

## 11. 1.16.1 相对 1.14 的主要变化

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
