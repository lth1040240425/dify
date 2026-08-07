# Dify 源码构建、发布与持续升级方案（1.14.0 → 1.16.1）

## 1. 目标与结论

将现网“官方 1.14.0 Docker 镜像 + 宿主机运行时补丁”迁移为“本机固定 Dify 1.16.1 源码提交 + 本机构建 Linux 镜像 + 服务器离线加载镜像 + 独立生产数据目录”。

不使用 `main` 分支，不在生产容器中手工改文件，不复用旧版本的完整补丁文件覆盖新源码。

本方案要求先完成一次独立的测试演练；生产切换需要维护窗口。切换时新旧版本绝不能并行连接同一个 PostgreSQL、Weaviate、Redis 或 Plugin Daemon 存储目录。

### 1.1 1.14.0 与 1.16.1 的隔离硬约束

当前线上 `1.14.0` 是独立运行的生产栈。在完成验收和获得明确的生产切换授权前，`1.16.1` 只能作为独立测试栈启动，且不得对 `1.14.0` 执行停止、重建、升级、改写 Compose、修改 `.env`、替换镜像或写入数据卷等操作。

`1.16.1` 测试栈必须使用独立的 Compose 项目名、端口、网络、数据库、App storage、Weaviate、Redis 和 Plugin Daemon 存储目录。测试栈的数据只能来自 `1.14.0` 的备份或副本，绝不能直接挂载现网 `/opt/dify/docker/volumes`。禁止在本阶段执行 `docker compose down`、`docker compose up` 或 `docker compose pull` 针对现网 `1.14.0` 项目。

## 2. 当前盘点

| 项目 | 当前值 | 迁移处理 |
|---|---|---|
| Dify | `langgenius/dify-api:1.14.0` | 升级到源码固定的 `1.16.1` 提交 |
| PostgreSQL | `/opt/dify/docker/volumes/db/data`，约 1.4G | 逻辑备份 + 复制到新生产目录 |
| App storage | `/opt/dify/docker/volumes/app/storage`，约 1.3G | 完整复制 |
| Weaviate | `/opt/dify/docker/volumes/weaviate`，约 636M | 一致性复制 |
| Redis | `/opt/dify/docker/volumes/redis/data` | 仅备份，不导入新环境 |
| Plugin Daemon | `/opt/dify/docker/volumes/plugin_daemon` | 完整复制后验证插件 |

现网 active override 有 6 个文件挂载：

1. API 与 Worker：`core/agent/fc_agent_runner.py`、`core/agent/cot_agent_runner.py`、`core/agent/llm_trace_hook.py`。
2. 仅 API：`services/workflow_event_snapshot_service.py`。
3. Plugin Daemon：Anthropic 0.3.25 的 `models/llm/llm.py`、`models/llm/svip-claude-opus-4-8.yaml`。
4. Moonshot 0.1.11 的 `provider/moonshot.py`、`models/llm/llm.py`、`models/llm/kimi-k3.yaml` 是直接修改 Plugin Daemon 持久化卷的文件，不是 Compose 挂载。

## 3. 本机开发、构建与服务器运行的边界

本机负责源码 checkout、补丁移植、测试、镜像构建和发布包；服务器只保存 Compose/.env、加载已验证镜像、运行服务和保存持久化数据。服务器不保存 Dify Git 源码，也不执行 `docker build`。

| 位置 | 内容 |
|---|---|
| 本机 `D:\CodexProjects\dify\core` | Dify Core fork、公司补丁和测试代码 |
| 本机 `D:\CodexProjects\dify\plugins` | Anthropic、Moonshot 等独立插件源码、上游基线和运行态对照快照 |
| 本机 `D:\WSL\dify-artifacts\1.16.1-company.0` | 镜像 tar.gz、sha256、镜像 digest、Compose 发布包 |
| 服务器 `/opt/dify/releases/1.16.1-company.0` | 上传后的只读发布包 |
| 服务器 `/opt/dify/deploy/dify-1.16.1` | Compose/.env/新数据目录 |

发布前先运行 `ssh dify-linux "uname -m"` 确认服务器架构。`x86_64` 使用 `linux/amd64`，`aarch64` 使用 `linux/arm64`；下列示例以 `linux/amd64` 为例。

## 4. 本机源码和版本控制

```bash
export DIFY_RELEASE=1.16.1
export DIFY_SOURCE=/mnt/d/CodexProjects/dify/core

git clone git@github.com:<你的组织或账号>/dify.git "$DIFY_SOURCE"
cd "$DIFY_SOURCE"
git remote add upstream https://github.com/langgenius/dify.git
git fetch upstream --tags
git switch -c company refs/tags/1.16.1
git push -u origin company
```

将源码仓库推送至内部 Git 仓库，并为所有移植的改动创建独立、带测试说明的提交。不要把 `/home/administrator/dify/patches` 作为长期唯一来源。

本机推荐目录：

### 4.1 Fork 与后续同步策略

#### 版本升级与发布原则

官方仓库只作为只读 `upstream`，个人/团队 Fork 作为可写 `origin`。不要直接跟踪或合并官方 `main`；仅合并经过确认的官方发布 Tag。

`company` 是 Fork 唯一的公司长期维护分支，承载公司定制提交和官方 Tag 合并；Fork 的 `main` 不参与公司升级链路。每次实际部署都创建不可变的 `release/<官方版本>-company.<序号>` 标签，例如 `release/1.16.1-company.0`；发布标签是回滚和追溯依据，不能移动或覆盖。

后续官方发布新 Tag 时，直接将目标 Tag 合并到长期公司维护分支。Git 可利用上一次合并的共同祖先识别已同步的官方变更；与逐个 `cherry-pick` 定制提交相比，可避免在每次升级中重复重放同一组补丁。仍可能发生冲突，但只需在本次官方升级中集中解决并验证。

首次创建 Fork 后启用冲突解决记录复用：

```bash
git config rerere.enabled true
git config rerere.autoupdate true
```

例如由 `1.16.1` 升级到官方 `1.16.2`：

```bash
git fetch upstream --tags
git switch company
git status  # 必须确认工作区干净
git merge --no-ff refs/tags/1.16.2 -m "merge upstream Dify 1.16.2"

# 如有冲突：逐项确认业务语义后解决，再执行 git add <文件> 和 git commit
# 随后构建镜像、执行测试与数据库迁移演练

git tag -a release/1.16.2-company.0 -m "Dify 1.16.2 company release 0"
git push origin company release/1.16.2-company.0
```

只有未合并的独立功能或紧急修复，才单独使用 `cherry-pick`；在迁入后应立即测试并合并回 `company`。每个公司补丁单独提交并附测试说明，不提交 `.env`、数据库 dump、镜像包或密钥。

另建私有 `dify-deployment` 仓库保存 Compose、发布脚本、备份脚本、镜像清单和插件补丁清单。Anthropic、Moonshot 等 Marketplace 插件不属于 Dify 核心 Fork；长期修改应 Fork 对应插件源码或保存可重放 patch，并锁定插件版本、包 hash、补丁 commit 和验收用例。

本机推荐目录：

```text
D:\CodexProjects\dify\core\                # 受 Git 管理的 Dify Core fork
D:\CodexProjects\dify\plugins\             # 独立插件 Git 工作树和只读基线快照
D:\WSL\dify-artifacts\1.16.1-company.0\   # 镜像、sha256、发布清单
```

服务器目录保持为：

```text
/opt/dify/releases/1.16.1-company.0/       # 上传后的镜像和发布清单
/opt/dify/deploy/dify-1.16.1/               # Compose、.env、新环境独占数据
/opt/dify/backups/pre-1.16.1-*/             # 只读备份和校验清单
```

## 5. 本机源码构建与镜像发布

Dify 的 `api/Dockerfile`、`web/Dockerfile`、`dify-agent/Dockerfile` 都要求以仓库根目录作为 Docker build context。

```bash
cd "$DIFY_SOURCE"
docker buildx build --platform linux/amd64 --load -f api/Dockerfile \
  -t company/dify-api:1.16.1-company.0 .
docker buildx build --platform linux/amd64 --load -f web/Dockerfile \
  -t company/dify-web:1.16.1-company.0 .
docker buildx build --platform linux/amd64 --load -f dify-agent/Dockerfile \
  -t company/dify-agent-backend:1.16.1-company.0 .
```

`worker` 和 `worker_beat` 使用与 `api` 相同的自建 API 镜像。Dify 1.16.1 的 Plugin Daemon、Sandbox、Weaviate、PostgreSQL、Redis 先使用官方固定版本镜像；只有当 Anthropic cache usage 的链路测试证明 Plugin Daemon 本身仍需修改时，才单独 fork 并构建 Plugin Daemon。不要无依据地构建一个不匹配的 Plugin Daemon 版本。

在 `$DIFY_DEPLOY/docker-compose.source.yaml` 覆盖镜像标签：

```yaml
services:
  api:
    image: company/dify-api:1.16.1-company.0
  worker:
    image: company/dify-api:1.16.1-company.0
  worker_beat:
    image: company/dify-api:1.16.1-company.0
  web:
    image: company/dify-web:1.16.1-company.0
  agent_backend:
    image: company/dify-agent-backend:1.16.1-company.0
```

无内部镜像仓库时，在本机导出、校验并上传镜像；服务器只做 `docker load`：

```bash
# 本机
mkdir -p /mnt/d/WSL/dify-artifacts/1.16.1-company.0
docker save company/dify-api:1.16.1-company.0 \
  company/dify-web:1.16.1-company.0 \
  company/dify-agent-backend:1.16.1-company.0 \
  | gzip > /mnt/d/WSL/dify-artifacts/1.16.1-company.0/dify-1.16.1-company.0.tar.gz
sha256sum /mnt/d/WSL/dify-artifacts/1.16.1-company.0/dify-1.16.1-company.0.tar.gz \
  > /mnt/d/WSL/dify-artifacts/1.16.1-company.0/SHA256SUMS

# 上传到服务器后，在服务器执行
sha256sum -c SHA256SUMS
gzip -dc dify-1.16.1-company.0.tar.gz | docker load
docker image inspect company/dify-api:1.16.1-company.0
```

有内部镜像仓库时，改用 `docker push` 和服务器 `docker pull`，但仍记录 image digest。无论使用哪种发布方式，都不要使用 `latest` 标签。

从 1.16.1 源码的 `docker/` 目录复制 Compose 和 env 示例，不复制旧 Compose 文件覆盖新版。将现网 `.env` 的业务值逐项合并到新版 `.env.example`，并保留原有加密相关配置和服务密钥。任何密钥只写入受控 `.env`，不进入 Git、工单或终端输出。

## 5. 必须新增的 1.16.1 配置

若启用 Dify Agent Beta，使用官方 1.16.1 Compose 中的 `agent_backend`、`local_sandbox`、`agent_ssrf_proxy` 和专用网络定义。生成一个随机值，同时配置到两个变量：

```bash
openssl rand -base64 48
```

```env
DIFY_AGENT_API_TOKEN=<生成的随机值>
AGENT_BACKEND_API_TOKEN=<同一个随机值>
```

不得使用官方示例中的开发默认 Token。即使短期内不启用 Dify Agent，也保留新版 Compose 的服务定义和安全配置，是否启动 Agent 服务由功能开关和测试结果决定。

## 6. 补丁移植与插件处理

### 6.1 通用原则

先提取 1.14.0 原始文件与现网补丁的差异，再在 1.16.1 对应源码上手工移植最小逻辑，最后提交到 fork。禁止将旧补丁文件直接复制到新源码路径。

```bash
docker create --name dify-114-base langgenius/dify-api:1.14.0
docker cp dify-114-base:/app/api/core/agent/fc_agent_runner.py /tmp/fc_agent_runner.1.14.0.py
docker rm dify-114-base

diff -u /tmp/fc_agent_runner.1.14.0.py \
  /home/administrator/dify/patches/core/agent/fc_agent_runner.py \
  > /tmp/fc-agent-trace.patch || true
```

对每个文件重复该过程；以旧版差异表达“业务意图”，而不是让 `patch` 命令直接套用到 1.16.1。

### 6.2 独立插件的源码、补丁与升级

Anthropic、Moonshot 是由 Plugin Daemon 安装和运行的独立插件包，不属于 Dify Core Fork 的 `company` 分支。Dify Core 的升级提交与插件定制必须分开管理。

官方插件源码上游为 <https://github.com/langgenius/dify-official-plugins>。该仓库用于审阅上游实现和定位升级基线；现网精确安装版本则以已安装插件的 `manifest.yaml`、安装包校验值和 Plugin Daemon volume 中的文件为准。当前现场基线如下：

| 插件 | 现网版本 | 定制内容 | 现状风险 |
|---|---:|---|---|
| Anthropic | `0.3.25` | 覆盖 `models/llm/llm.py`，新增 `models/llm/svip-claude-opus-4-8.yaml` | 当前通过带版本 hash 的 Compose mount 注入；插件重装后路径会变化 |
| Moonshot | `0.1.11` | `provider/moonshot.py` 默认校验模型改为 `kimi-k3`、对 `429 engine_overloaded` 容错；`models/llm/llm.py` 有限退避重试；新增 `models/llm/kimi-k3.yaml` | 当前直接修改持久化 volume；重装或升级插件会丢失 |

在私有 `dify-deployment` 仓库中单独维护以下目录，不把插件文件提交进 Dify Core Fork：

```text
plugins/
  anthropic/
    0.3.25/
      overlay/models/llm/llm.py
      overlay/models/llm/svip-claude-opus-4-8.yaml
      README.md
  moonshot/
    0.1.11/
      patches/0001-retry-engine-overloaded.patch
      patches/0002-use-kimi-k3-for-validation.patch
      overlay/models/llm/kimi-k3.yaml
      README.md
```

每个插件版本目录必须记录：上游 Git commit 或来源包、`manifest.yaml` 版本、原始 `.difypkg` 的 SHA-256（若可获取）、定制补丁 commit、生成的定制包 SHA-256，以及验收结果。不要将 `.venv`、`__pycache__`、API Key、安装后的 `@hash` 目录或 Plugin Daemon volume 提交到 Git。

源码获取和基线确认步骤：

```bash
git clone https://github.com/langgenius/dify-official-plugins.git
cd dify-official-plugins

# 以 manifest 版本定位候选源码，再记录最终确认的 commit。
rg -n '^version: (0\\.3\\.25|0\\.1\\.11)$' --glob manifest.yaml .
git log --all -- <找到的 Anthropic 或 Moonshot 插件目录>
```

若上游仓库无法唯一对应现网安装包，先只读导出现网插件目录作为取证基线，再与上游源码比对并生成补丁；不得把运行目录当作长期源码仓库。现网目录示例为：

```text
/opt/dify/docker/volumes/plugin_daemon/cwd/langgenius/anthropic-0.3.25@<hash>/
/opt/dify/docker/volumes/plugin_daemon/cwd/langgenius/moonshot-0.1.11@<hash>/
```

插件升级的固定流程如下：

1. 在隔离工作目录获取目标官方插件版本，记录基线版本与 commit/包 hash。
2. 逐项审阅现有补丁；不能因为 `patch` 命令无冲突就认定语义仍正确。若官方已实现同一修复，删除对应定制；若文件或接口变化，按新源码重新移植最小逻辑。
3. 用“目标官方源码 + 已适配 overlay/patch”生成新的 `.difypkg`，并将构建输入、产物 hash 和测试结果写入该版本目录。不要在生产 volume 内打补丁。
4. 仅在 1.16.1 隔离测试环境安装定制包，验证模型列表、已有凭据可用性、凭据保存、短请求、流式请求和工具调用；Moonshot 额外验证 429 容错，Anthropic 额外验证 SVIP 自适应推理与 cache token 链路。
5. 测试通过后再发布生产，并保留上一版已验证 `.difypkg` 和其校验值用于回滚。生产发布使用 Dify 的插件安装/升级流程，不依赖 `cwd/...@hash` 的 bind mount。

默认保持既有插件身份和 Provider 配置兼容；若定制包必须改变插件 author/name 或 Provider 标识，先做“旧标识 → 新标识”的凭据、模型和应用配置迁移清单并在测试环境验证，不能假定已有配置会自动继承。

### 6.3 Dify Core 补丁

| 补丁 | 移植目标 | 验收 |
|---|---|---|
| `fc_agent_runner.py` | 1.16.1 的同名 Agent runner | 真正 AgentChat 请求产生一次 Kafka LLM trace |
| `cot_agent_runner.py` | 1.16.1 的同名 Agent runner | CoT/工具循环下 trace 不重复、不丢失 |
| `llm_trace_hook.py` | 作为公司扩展模块纳入 fork | `py_compile`、异常不影响主调用、敏感字段脱敏 |
| `workflow_event_snapshot_service.py` | 1.16.1 的同名服务 | Workflow SSE 空闲超时仍满足定制要求 |

建议将 LLM trace 改为一个可配置、可关闭的扩展：保留现有 `LLM_TRACE_*` 环境变量，默认关闭或以明确开关启用；API 和 Worker 使用同一自建镜像，不再使用 bind mount。Kafka topic `Kfk_JiGua_dify_llm_trace_log` 的连通性、脱敏和失败降级必须做真实验证。

### 6.4 Anthropic SVIP 插件补丁

1. 备份并复制 Plugin Daemon volume 后，确认 1.16.1 下实际安装的 Anthropic 插件版本和完整目录名。
2. 在该版本的 `models/llm/llm.py` 中仅移植 `svip-claude-opus-4-8` 的自适应推理参数处理。
3. 在该版本的预定义模型目录中重新建立 `svip-claude-opus-4-8.yaml`，复用标准 Opus 4.8 schema，但不误用官方定价。
4. 将插件补丁做成可重放的 Git patch 或内部插件 fork；若短期必须挂载，挂载目标路径必须来自新插件目录，不能沿用 `anthropic-0.3.25@...` 的 hash。
5. 用短提示做真实调用，确认不会携带不兼容的 `temperature`、`top_p`、`top_k`，并确认控制台把模型识别为预定义模型。

### 6.5 Moonshot Kimi K3 插件修改

在新 Plugin Daemon volume 的新 Moonshot 插件版本上检查：默认凭据校验模型是否仍引用已下线的 `kimi-k2-0711-preview`，以及是否已原生处理 `429 engine_overloaded`。

若官方仍未修复，重新应用最小修改：默认校验模型使用 `kimi-k3`；仅对 `429` 且正文含 `engine_overloaded` 进行有限退避重试（2 秒、5 秒、最多 3 次）。将差异保存为补丁，不再只改 volume 内文件。验收包括“保存官方 Key”和“真实 K3 调用”。

### 6.6 Anthropic cache token 历史 workaround

历史记录中的 cache usage 补丁目前不在 active Compose mount 中。升级时先做端到端测试：插件响应 JSON、Plugin Daemon 转发、API usage、`messages.message_metadata`。

只有当 `cache_read_input_tokens` 与 `cache_creation_input_tokens` 仍在 Plugin Daemon 转发中丢失时，才在 fork 的 API/Plugin Daemon 中正式补齐 LLMUsage schema 和序列化链路。`system_fingerprint` 编码/反解 workaround 仅作为过渡方案，不能默认带入新版。

## 7. 备份和测试副本

在开始任何迁移前执行，并将 sha256 清单与备份一起保存：

```bash
mkdir -p "$DIFY_BACKUP"
docker exec docker-db_postgres-1 pg_dump -U postgres -Fc \
  --no-owner --no-privileges dify > "$DIFY_BACKUP/dify.dump"
docker exec docker-db_postgres-1 pg_dump -U postgres -s dify \
  > "$DIFY_BACKUP/dify-schema.sql"

tar --xattrs --acls -C /opt/dify/docker/volumes -cpf \
  "$DIFY_BACKUP/app-storage.tar" app/storage
tar --xattrs --acls -C /opt/dify/docker/volumes -cpf \
  "$DIFY_BACKUP/plugin-daemon.tar" plugin_daemon
tar --xattrs --acls -C /opt/dify/docker/volumes -cpf \
  "$DIFY_BACKUP/weaviate.tar" weaviate
tar --xattrs --acls -C /opt/dify/docker/volumes -cpf \
  "$DIFY_BACKUP/redis.tar" redis/data
sha256sum "$DIFY_BACKUP"/* > "$DIFY_BACKUP/SHA256SUMS"
```

对生产在线 Weaviate 不能只做不一致的热拷贝。测试演练可以用数据库 dump 和存储副本；生产最终副本必须在写入冻结后完成，或使用底层存储快照。

创建隔离测试目录后，恢复数据库 dump 到独立 PostgreSQL，复制 App storage、Weaviate 和 Plugin Daemon 到测试的 `volumes/`。测试 Redis 使用空目录，不恢复旧队列和缓存，避免旧版本序列化的 Celery 任务在新 Worker 中执行。

```bash
mkdir -p "$DIFY_DEPLOY/volumes"
rsync -aHAX --numeric-ids /opt/dify/docker/volumes/app/storage/ \
  "$DIFY_DEPLOY/volumes/app/storage/"
rsync -aHAX --numeric-ids /opt/dify/docker/volumes/plugin_daemon/ \
  "$DIFY_DEPLOY/volumes/plugin_daemon/"
rsync -aHAX --numeric-ids /opt/dify/docker/volumes/weaviate/ \
  "$DIFY_DEPLOY/volumes/weaviate/"
```

测试 Compose 用独立项目名、端口、数据库名和卷目录，例如 `docker compose -p dify116test`；绝不连接现网 `dify` 数据库和现网 Weaviate 目录。

## 8. 数据库迁移与测试演练

1. 在测试 PostgreSQL 中恢复 `dify.dump`。
2. 将测试 `.env` 的 `DB_DATABASE` 指向测试库，存储与 Weaviate 指向测试副本。
3. 用 1.16.1 Compose 启动 `db_postgres`、`redis`、`weaviate`、`plugin_daemon`，确认健康后启动 `api`、`worker`、`web`。
4. 让 1.16.1 的迁移入口执行数据库升级；若采用源码命令，使用 API 容器或匹配依赖环境执行 `flask db upgrade`。迁移只对测试库执行。
5. 记录迁移前后 Alembic revision、容器镜像 digest 和插件版本。
6. 导出新库 schema，与旧库 schema 对比，确认预期新增迁移，没有意外删除或类型变更。

建议迁移前后执行以下只读数据核对：

```sql
SELECT 'apps' AS object, count(*) FROM apps
UNION ALL SELECT 'conversations', count(*) FROM conversations
UNION ALL SELECT 'messages', count(*) FROM messages
UNION ALL SELECT 'datasets', count(*) FROM datasets
UNION ALL SELECT 'documents', count(*) FROM documents;
```

## 9. 测试验收

- 管理员登录、控制台、应用发布、Web App 和 Service API。
- 现有 AgentChat 调用、工具调用、流式 SSE、会话续聊。
- 现有 Workflow 的发布、执行、失败重试、SSE 事件和空闲等待。
- 现有知识库的检索、文件下载和新文档入库。
- Anthropic SVIP、Moonshot K3、DeepSeek 等已启用供应商的凭据保存和一次真实短调用。
- Anthropic cache token 从模型响应到 `messages.message_metadata` 的落库验证。
- 自定义 Kafka trace 在 API 与 Worker 路径均可用，Kafka 不可用时不阻断用户请求。
- Plugin Daemon 重启后插件仍可用；重新安装 Anthropic/Moonshot 插件后补丁有明确的重放流程。
- 新增 Agent Backend/Sandbox 服务的 Token、网络隔离和最小权限验证。

## 10. 生产切换

1. 提前完成测试演练并签字确认，准备 60 至 120 分钟维护窗口。
2. 进入维护模式，停止入口流量，禁止发布、上传和工作流新运行；等待或人工终止正在执行的 Celery/Workflow 任务。
3. 在写入冻结后执行最终 PostgreSQL dump 和 App storage、Plugin Daemon、Weaviate 的最终增量复制。
4. 停止旧应用服务，但保留旧 Compose、旧容器定义和旧数据目录；禁止 `docker compose down -v`。
5. 用新目录启动 1.16.1：

```bash
cd "$DIFY_DEPLOY"
docker compose -p dify116 \
  -f docker-compose.yaml \
  -f docker-compose.source.yaml \
  -f docker-compose.company.yaml config --quiet
docker compose -p dify116 \
  -f docker-compose.yaml \
  -f docker-compose.source.yaml \
  -f docker-compose.company.yaml up -d
```

6. 等待数据库迁移、API/Worker/Plugin Daemon 健康，按第 9 节完成冒烟。
7. 恢复入口流量，前 24 小时重点监控 API/Worker/Plugin Daemon 日志、迁移异常、SSE、Kafka trace、模型调用错误率和 Weaviate 检索。

## 11. 回滚

镜像回滚本身不安全，因为 1.16.1 可能已升级数据库 schema。回滚必须是“停止新栈 + 将流量切回旧栈 + 使用未升级的旧数据目录或从切换前 dump 恢复旧数据库”。

因此生产切换时必须让 1.16.1 使用新的数据目录副本，保留 `/opt/dify/docker/volumes` 不被新版写入。这样在切换失败时，可直接恢复旧 Compose 和旧数据目录；代价是回滚后会丢失新栈运行期间产生的数据，应在窗口内控制写入并明确业务确认。

## 12. 交付物与责任

迁移完成前必须具备：固定 Git commit、镜像 digest、完整 `.env` 差异清单（不含密钥值）、数据库 dump 校验、文件/向量库校验、补丁 diff、测试报告、切换记录和回滚负责人。

官方版本与变更依据：<https://github.com/langgenius/dify/releases/tag/1.16.1>。
