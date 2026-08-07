# Dify 新旧双版本并行升级问题总结

## 1. 项目背景

本次升级在同一台服务器 `192.168.40.6` 上并行运行两个 Dify 版本：

| 版本 | 访问地址 | Compose 项目 | 说明 |
|---|---|---|---|
| 旧版 Dify 1.14 | `http://192.168.40.6:18080` | `docker-*` | 原生产版本，需保持功能不受影响 |
| 新版 Dify 1.16.1 | `http://192.168.40.6:18081` | `dify-company-*` | 新版测试及后续迁移目标 |

数据库使用同一个 PostgreSQL 容器，但数据库逻辑隔离：

- 旧版数据库：`dify`
- 新版业务数据库：`dify_company`
- 新版插件数据库：`dify_company_plugin`

新版 Redis、API、Worker、插件服务均为独立容器。旧版 PostgreSQL 容器仅额外接入新版专用数据库网络，没有迁移或删除旧数据。

## 2. 主要问题与根因

### 2.1 新旧 API 网络别名冲突

新版 API、Worker、WebSocket 等服务通过 `db_network` 接入了旧版的 `docker_default` 网络。旧版 API 和新版 API 都注册了 Docker 服务别名 `api`。

因此旧版 Nginx 请求 `http://api:5001` 时，可能解析到新版 API，而不是旧版 API，表现为：

- 旧版 API 间歇性 400/401/500
- 旧版页面接口返回异常
- 外部程序访问 `18080` 超时
- 旧版功能看起来像被新版“影响”

这不是旧版代码或 PostgreSQL 数据被修改，而是两个 Compose 项目共享网络导致的服务发现冲突。

### 2.2 新版 Worker 无法连接 PostgreSQL

新版 Worker 初始没有加入能够解析 `db_postgres` 的网络，日志出现：

```text
could not translate host name "db_postgres" to address
```

工作流测试因此出现 `Test Run (Stopped)`、`0 Tokens`，任务在读取工作流数据阶段就失败了。

### 2.3 插件服务互相访问

新旧版本都存在 `plugin_daemon`，早期网络配置下，旧版 API 可能请求到新版插件服务，因内部密钥和插件数据库不同，出现插件管理接口 `401` 或 `500`。

### 2.4 旧版 Web 服务端 API 地址为空

旧版 1.14 的 `.env` 中以下配置为空：

```text
CONSOLE_API_URL=
APP_API_URL=
```

旧版 Web 服务端渲染时退回访问容器内的 `localhost`，日志出现：

```text
GET http://localhost/console/api/system-features
ECONNREFUSED
```

这会导致旧版页面加载慢、页面卡住，部分外部调用表现为连接超时。

### 2.5 浏览器 Cookie 冲突

新旧版本都通过同一个 IP 访问，只是端口不同。浏览器 Cookie 不区分端口，旧版和新版使用相同的 Cookie 名称：

- `access_token`
- `refresh_token`
- `csrf_token`

登录新版后会覆盖旧版 Cookie，登录旧版又会覆盖新版 Cookie，导致：

- 旧版频繁跳回登录页
- `account/profile` 和 `refresh-token` 返回 `401`
- 工作空间列表显示不完整

数据库中的 `jigua`、`shuhang`、`yunlue` 均仍存在，工作空间缺失是会话冲突或当前账号成员关系造成的显示问题，不是数据被删除。

## 3. 已执行的修复

### 3.1 隔离新版数据库网络

已创建新版专用网络：

```text
dify-company-db
```

旧版 PostgreSQL 以附加网络方式接入该网络，新版 API、Worker、WebSocket、Beat 通过该网络访问 PostgreSQL。

新版核心容器已从旧版 `docker_default` 网络中移除并重新创建。旧版 API、旧版 Worker、旧版 Nginx 均未重启。

### 3.2 修复新版 Worker 数据库解析

已为新版 Worker 和 Worker Beat 配置正确的数据库网络，并重新创建这两个容器。之后新版工作流可以正常读取数据库并执行。

### 3.3 隔离新版插件服务

新版插件服务使用独立的插件数据库网络和插件服务网络，避免与旧版 `plugin_daemon` 互相发现。

### 3.4 修复旧版 Web API 地址

旧版 Web 配置已补充为旧版访问地址：

```text
CONSOLE_API_URL=http://192.168.40.6:18080
APP_API_URL=http://192.168.40.6:18080
```

只重新创建了旧版 `web` 容器；旧版 API、Worker、Redis、PostgreSQL 未重建。

原配置已备份到服务器：

```text
/opt/dify/docker/.env.bak-20260806-web-fix
```

## 4. 验证结果

### 4.1 网络验证

- 旧版 Nginx 解析 `api`：仅指向旧版 API `172.18.0.11`
- 新版 Nginx 解析 `api`：指向新版 API `172.22.0.10`
- 新版 Worker 可解析 `db_postgres`
- 新版 API 健康检查：`200`
- 旧版 API 自检：`200`

### 4.2 旧版访问验证

以下旧版地址从客户端访问均返回 `HTTP 200`：

```text
/signin
/apps
/console/api/system-features
```

### 4.3 新版工作流验证

使用面霜类目参数进行新版工作流 API 测试：

- HTTP 状态：`200`
- 工作流状态：`succeeded`
- 执行耗时：约 113 秒
- 已生成 `content_trend_json`
- 返回趋势、标签统计、评论样本和数据来源

## 5. 当前遗留项

网络冲突和新版 Worker 数据库连接问题已经修复。

当前仍需处理浏览器 Cookie 隔离，否则同一浏览器同时打开新旧版本时，仍可能出现旧版 `401`、跳登录页或空间列表不完整。

## 6. 推荐的最终访问方案

不需要公网域名，可在访问电脑的 hosts 文件中配置本地域名：

文件路径：

```text
C:\Windows\System32\drivers\etc\hosts
```

增加：

```text
192.168.40.6 dify-old.test
192.168.40.6 dify-new.test
```

然后分别访问：

```text
http://dify-old.test:18080
http://dify-new.test:18081
```

临时方案是使用两个独立的 Chrome 配置文件，或使用两个不同浏览器；仅使用不同端口不能隔离 Cookie。

## 7. 资源与其他服务说明

本次 API 和网络异常不是服务器资源不足导致的。检查时服务器负载较低，内存仍有可用空间，磁盘也有余量。

服务器上的 `/opt/datax` 是独立的 DataX 服务目录，不属于 Dify，也不是 Dify 工作流调用链的一部分。它会占用磁盘，但不能通过删除它来解决 Dify 网络或登录问题。`/var/cache/yum` 是系统软件包缓存，同样与 Dify 功能无关，清理前应先确认其使用方。

## 8. 运维注意事项

1. 新旧 Compose 项目不得共享带有 `api`、`worker`、`plugin_daemon` 等通用服务别名的网络。
2. 旧版 PostgreSQL 可以被新版复用，但应通过独立网络或明确服务别名访问。
3. 升级前先备份旧版 `.env`、Compose 配置和数据库；不要直接执行 `down -v`。
4. 新旧版本必须使用不同域名或不同浏览器配置，不能只依赖不同端口隔离会话。
5. 每次升级后至少验证：旧版登录、旧版工作空间列表、新版登录、新版工作流 API、两个版本的数据库连接和插件管理接口。

## 9. Docker 内部网段与现网网段冲突（2026-08-06）

### 9.1 现象与影响

部署新版后，来自 `172.21.0.0/16`、`172.23.0.0/16`、`172.28.0.0/16` 的机器无法 Ping `192.168.40.6`，也无法访问服务器上的服务。这不是某一个 Dify 端口未开放，而是这些网段到服务器的返回流量被错误路由。

### 9.2 根因

Docker 自动为新版项目分配了与现网重叠的 bridge 网段：

| Docker 网络 | 冲突前网段 | 使用服务 |
|---|---|---|
| `dify-company_ssrf_proxy_network` | `172.21.0.0/16` | API、Worker、Sandbox、SSRF Proxy、Plugin Daemon |
| `dify-company_agent_sandbox_network` | `172.23.0.0/16` | Agent Backend、Local Sandbox |
| `dify-company-plugin-db` | `172.28.0.0/16` | 新版 Plugin Daemon、旧版 PostgreSQL 的附加连接 |

服务器因此生成了指向 Docker bridge 的本地路由。例如 `172.28.0.0/16` 会被路由到 Docker bridge，而非默认网关。真实客户端位于这些网段时，服务器给客户端的响应被发往 Docker 网桥，造成 Ping 和 HTTP 都超时。

`DOCKER-ISOLATION-STAGE-*` 中的 `DROP` 规则是 Docker 的标准跨 bridge 隔离规则；它们反映了新增网桥，但并非本次外部机器无法访问的直接拦截点。直接原因是路由前缀重叠。

### 9.3 修复措施

为新版项目指定不与现网冲突的内部地址段：

| Docker 网络 | 修复后网段 |
|---|---|
| `dify-company_ssrf_proxy_network` | `10.250.21.0/24` |
| `dify-company_agent_sandbox_network` | `10.250.23.0/24` |
| `dify-company-plugin-db` | `10.250.28.0/24` |

具体操作：

1. 在新版 `docker-compose.yaml` 的 `ssrf_proxy_network` 和 `agent_sandbox_network` 中增加显式 `ipam` 子网配置。
2. 停止并重建 `dify-company` 新版项目容器及其项目网络，不删除数据卷。
3. 重建外部网络 `dify-company-plugin-db`，将旧版 PostgreSQL 重新连接到该网络，并保留网络别名 `db_postgres`。
4. 重新创建新版 `plugin_daemon`。如果缺失 `db_postgres` 别名，插件服务会因无法解析数据库主机名而循环重启。

新版 Compose 配置已备份：

```text
/root/dify-company-1.16.1/docker-compose.yaml.bak-20260806-network-fix
```

### 9.4 修复验证

修复后服务器路由确认如下，三段真实网段均通过物理网卡默认网关返回：

```text
172.21.1.1 via 192.168.40.253 dev eth0
172.23.1.1 via 192.168.40.253 dev eth0
172.28.1.1 via 192.168.40.253 dev eth0
```

同时验证：

- 新版页面 `http://192.168.40.6:18081/` 返回 `HTTP 200`。
- 新版 API 返回 `HTTP 200`，`dify-company-api-1` 为 healthy。
- 新版 `plugin_daemon` 正常运行并完成已安装插件初始化。
- 旧版入口 `http://192.168.40.6:18080/` 返回 `HTTP 200`，旧版 API 仍为 healthy。

### 9.5 后续约束

服务器需要同时接入办公网、VPN、WSL 或其他使用 `172.16.0.0/12` 的网络时，不应依赖 Docker 自动分配子网。新建 Compose 网络必须显式配置为已核对、不会与现网路由重叠的地址段；部署前应检查服务器 `ip route` 和客户端/VPN 的已使用网段。

### 9.6 运维网段规划建议（新增）

运维建议后续新建 Docker 网络时，不再使用 `172.x` 或 `192.168.x` 网段，优先从专用的 `10.x` 地址池中分配，例如：

```text
10.200.0.0/16
10.201.0.0/16
```

每个 `/16` 可提供 65,000+ 个地址，便于为不同项目、环境和中间件预留独立子网。使用前仍必须以服务器和调用方的实际 `ip route`、VPN 路由及云网络规划为准，不能仅凭地址段看起来空闲就直接使用。

这条建议的含义是：Docker bridge 网络是服务器上的真实三层网段。如果 Docker 网络误用服务器办公网、VPN 或调用方已经使用的地址段，Linux 会把本应发往真实网络的流量误判为容器网络流量，可能表现为宿主机和容器之间的 HTTP、SSH、数据库连接甚至 Ping 超时。本次 `172.21.x.x`、`172.23.x.x`、`172.28.x.x` 与现网路由重叠，就是 Docker 隔离规则和宿主机转发策略异常的重要风险来源。

规划时应将两个 `/16` 作为 Docker 专用地址池，再为每个 Compose 项目划分具体 `/24`，例如：

```text
项目 A：10.200.21.0/24
项目 B：10.200.23.0/24
项目 C：10.201.21.0/24
```

新建网络前仍要确认办公网、VPN、云专有网络和其他主机没有使用目标网段；`10.x` 不是天然安全，也不能看到网段“没有被占用”就直接使用。现有 `10.250.x.x` 网络属于本次部署已经存在的地址，除非单独制定迁移窗口、备份和回滚方案，否则不要为了统一规划而直接改动，以免再次影响运行中的服务。

本次已创建的 `10.250.x.x/24` 网络属于既有部署结果，后续新项目应统一登记网段，避免再次出现自动分配和现网重叠。

## 10. 1.14 升级至 1.16.1 的功能对比

以下内容依据 Dify 官方发布说明整理，覆盖本次跨越的 `1.15.0` 与 `1.16.1` 版本。功能是否已经被业务应用采用，仍需按具体应用、模型供应商和权限配置逐项验收。

| 能力方向 | 旧版 1.14 | 新版 1.16.1 可获得的提升 | 业务价值 |
|---|---|---|---|
| 命令行调用 | 主要依赖控制台和 HTTP API | 新增 `difyctl`，可从 Windows、Linux、macOS 的终端、脚本和 CI 调用应用及工作流 | 便于批处理、自动化发布和运维集成 |
| 工作流与 Chatflow 推理过程 | 运行结果与调试信息分散查看 | 可在 Workflow、Chatflow、CLI 的实时面板查看并保留模型思考过程 | 更容易定位模型输出和提示词问题 |
| 人工介入（HITL） | 以文本填写为主 | 人工输入表单支持下拉选择、文件和多文件上传 | 支持结构化审批、补充资料和人工校验场景 |
| 长任务模型 | 超长图像、视频等生成任务更容易受超时影响 | 支持轮询等待慢速、长时运行模型的最终结果 | 提升图像/视频生成等任务的稳定性 |
| 工作流工具输入 | 工具参数选择能力有限 | 工具节点支持多选下拉输入 | 减少复杂参数拼接，提升配置效率 |
| 工作流排障 | 从日志回到画布需要手工查找节点 | 运行日志或错误中的 `node_id` 可直接定位并高亮画布节点 | 缩短工作流故障定位时间 |
| 应用与 Agent 配置备份 | 主要通过应用级 DSL 操作 | Agent 可从侧边栏直接导出 DSL YAML | 便于备份、版本管理和跨环境迁移 |
| 知识库/RAG | 表格中的图片和检索过程可见性有限 | Excel 内嵌图片可在导入时提取；知识处理和检索链路增加追踪信息 | 改善图文表格知识入库与 RAG 可观测性 |
| 运行可观测性 | 追踪关联能力较弱 | 支持 Phoenix 自定义 trace session，补充文档检索与知识处理追踪 | 更容易关联业务会话和检索质量 |
| 插件安装 | 部分网络环境安装依赖不稳定 | 插件守护进程可按区域选择更合适的软件包镜像 | 改善受限网络环境的插件安装体验 |
| 首页与运行体验 | 首页和协作草稿存在较多性能、稳定性问题 | 最近应用接口轻量化；协作草稿保存、重连和错误提示得到修复 | 降低首页加载延迟，减少草稿丢失和误报 |

### 10.1 安全与部署变化

`1.16.1` 对 Agent 运行环境加强了隔离：新增 Agent 专用沙箱网络、转发代理和 API 到 Agent Backend 的令牌认证。生产环境应确认 `DIFY_AGENT_API_TOKEN` 与 `AGENT_BACKEND_API_TOKEN` 已配置为非默认值且两者一致；不要在文档、日志或聊天记录中记录其实际值。

上述网络隔离也是本次部署中出现额外 Docker bridge 网络的原因之一，因此必须与第 9 节的显式网段规划同时执行。

官方发布说明：

- <https://github.com/langgenius/dify/releases/tag/1.15.0>
- <https://github.com/langgenius/dify/releases/tag/1.16.1>

## 11. 无感切换入口至新版（2026-08-06）

### 11.1 目标与当前入口

调用方继续使用原地址，不需要改代码、域名或端口：

```text
http://192.168.40.6:18080
```

当前流量链路为：

```text
调用方 -> dify-edge-gateway:18080 -> 新版 Dify Nginx:18081 -> 新版 API
```

网关容器 `dify-edge-gateway` 使用 host 网络模式，配置文件为：

```text
/root/dify-edge-gateway/nginx.conf
```

网关已配置 WebSocket 升级、流式响应关闭缓冲、长连接读取超时，因此适用于 Dify 流式对话、工作流流式输出和 Socket.IO 通信。

### 11.2 旧版保留与回滚通道

旧版没有下线。仅将旧版 `docker-nginx-1` 的宿主机端口改为：

```text
127.0.0.1:18082 -> 旧版 Nginx:80
```

旧版 API、Worker、Redis、PostgreSQL 等容器仍运行。需要回滚时，将网关上游改为 `127.0.0.1:18082` 并执行 Nginx 平滑 reload 即可；调用方地址不变。

旧版端口配置备份：

```text
/opt/dify/docker/.env.bak-20260806-edge-gateway
```

### 11.3 入口防火墙与持久化

旧规则仅允许 `192.168.40.0/24` 访问 `18080`，其他来源会命中 `18080 DROP`。为确保既有调用方无感访问，已仅对 `18080/tcp` 放行：

- `192.168.40.0/24`
- `192.168.10.0/24`
- `172.21.0.0/16`
- `172.23.0.0/16`
- `172.28.0.0/16`

运行态规则已生效，并同步写入启动时由 `local-iptables-restore.service` 恢复的文件：

```text
/etc/sysconfig/iptables
```

同时已移除该持久化文件中的旧版 `18080 -> 172.18.0.11:80` DNAT，避免服务器重启后旧 Docker 规则重新抢占 `18080`。原规则备份：

```text
/etc/sysconfig/iptables.bak-20260806-edge-gateway
```

### 11.4 验证结果

- 调用侧访问 `http://192.168.40.6:18080/` 返回 `HTTP 200`。
- 调用侧访问 `http://192.168.40.6:18080/console/api/system-features` 返回 `HTTP 200`，响应版本为 `1.16.1`。
- 新版 `dify-company-api-1` 为 healthy。
- 旧版内部回滚地址 `127.0.0.1:18082` 返回版本 `1.14.0`。
- 前置网关、新版和旧版核心容器均保持运行。

## 12. 独立中间件切换计划与维护窗口（2026-08-07）

### 12.1 当前准备状态

独立 PostgreSQL、Redis、Weaviate 容器已经创建并运行。`dify_company` 和 `dify_company_plugin` 已完成首次复制，源库与独立库的 public 表数量分别为 `136` 和 `13`；Weaviate 数据目录已复制约 `636MB`，并通过认证查询验证。

但当前新版业务容器仍连接原来的 `dify-company-db`、`dify-company-plugin-db` 网络，并且新版自带的 Redis 容器仍在运行。因此“独立中间件已准备好”不等于“新版已经完成切换”。

### 12.2 中午切换前必须再次同步

首次复制之后新版仍可能产生用户、应用、工作流、运行记录和插件数据。只要期间有写入，独立 PostgreSQL 快照就可能落后；正式切换前必须在维护窗口内：

1. 暂停新版 API、Worker、Worker Beat、插件和 Agent 写入服务。
2. 对 `dify_company` 与 `dify_company_plugin` 做最终逻辑备份并恢复到独立 PostgreSQL。
3. 检查 Redis 队列是否为空；活动任务需要先排空或明确接受重试，缓存可按方案迁移或重建。
4. 校验 Weaviate 数据量和认证连接，确认新版 API、Worker、插件和 Agent Backend 都已加入独立中间件网络。
5. 仅重建新版相关容器，完成登录、工作空间、工作流、插件、知识库和 `18080` 网关冒烟测试。

### 12.3 风险控制与回滚

本次切换预计会造成新版短暂不可用，目标维护窗口为 3-5 分钟。切换前必须检查内存、磁盘、Docker 和数据库健康状态；不得在资源不足或日志异常时继续执行。

旧 PostgreSQL、旧 Redis、旧 Weaviate、旧版 Compose、旧版环境文件和网关回滚配置在验收前不得删除。若新版启动或数据校验失败，应恢复原 Compose/环境配置并将网关上游切回旧版 `127.0.0.1:18082`，确认旧版可用后再分析原因。

任何停服、数据库最终同步、Compose 重建、网络切换或防火墙变更，都必须在执行前明确告知影响、命令和回滚方式，并获得负责人确认；未确认时只允许进行只读检查。

### 12.4 本次独立中间件切换实际执行记录（2026-08-07）

本次切换已获得负责人明确授权，旧版容器、旧版数据库和旧版 Redis 均保留，未执行删除卷或 `docker compose down`。

1. 切换前检查服务器资源：可用内存约 `8.3GB`，根分区剩余约 `54GB`；独立 PostgreSQL 无业务连接，满足维护条件。
2. 将独立 PostgreSQL 数据库改名：`dify_company` 改为 `dify`，`dify_company_plugin` 改为 `dify_plugin`；新版 `.env` 同步修改 `DB_DATABASE` 和 `DB_PLUGIN_DATABASE`，并留存配置备份。
3. 暂停新版 `nginx`、`web`、`api`、`api_websocket`、`worker`、`worker_beat`、`plugin_daemon`、`agent_backend` 写入组件，旧版和中间件保持运行。
4. 对当前 1.16.1 源库生成最终逻辑备份：核心库约 `701MB`，插件库约 `180KB`；先恢复到临时库 `dify_final_20260807`、`dify_plugin_final_20260807`，校验通过后再交换为正式库名。原独立库保留为 `dify_pre_cutover_20260807`、`dify_plugin_pre_cutover_20260807` 回滚副本。
5. 发现新版配置中的 `REDIS_HOST=redis` 会优先解析到新版默认网络中的旧 Redis，且插件守护进程未加入独立 Redis 网络。修正为 `REDIS_HOST=dify-infra-redis`，并将 `plugin_daemon` 加入 `redis_network`，留存 Compose 配置备份后重建新版容器。
6. 新版容器已加入独立网络：PostgreSQL `dify-infra-core-db`/`dify-infra-plugin-db`、Redis `dify-infra-redis`、Weaviate `dify-infra-vector`。API、Worker、Worker Beat、插件守护进程、Agent Backend、Web、Nginx 均恢复运行。

切换后验证结果：

- 当前源库与独立目标库的 public 表行数全部一致，核心库 `136` 张表、插件库 `13` 张表；账号、应用、工作流、消息和工作流执行等关键表已完成最终同步。
- 新版 API、独立 PostgreSQL、独立 Weaviate 均为 healthy/ready；Weaviate 就绪检查返回 `200`。
- 独立 Redis 连接正常，未发现待执行的 List/Stream 队列。
- 网关 `http://192.168.40.6:18080` 返回登录跳转，`/console/api/system-features` 返回 `200`。
- 已登录验证应用、知识库和插件接口均返回 `200`。
- 旧版 API、Worker 和回滚入口 `18082` 仍保持运行，未影响旧版功能。

验收期间不得删除旧版 PostgreSQL、Redis、Weaviate、Compose、环境文件、回滚数据库和最终备份；确认新版持续稳定后，才可另行安排旧版资源清理。

## 13. 服务器重启后程序盘点与 Docker 异常时间线（2026-08-07）

### 13.1 当前已恢复的程序

服务器重启并重新启动 Docker 后，以下服务已确认运行：

- 新版 Dify：API、Web、Worker、Worker Beat、Plugin Daemon、Agent Backend、Sandbox、SSRF Proxy、Nginx。
- 旧版 Dify：API、Web、Worker、Worker Beat、Plugin Daemon、Sandbox、SSRF Proxy、Nginx、PostgreSQL、Redis、Weaviate。
- 独立中间件：独立 PostgreSQL、Redis、Weaviate。
- 前置网关：`dify-edge-gateway`，监听 `18080`。
- `dify-trace-kafka-bridge`，容器内部 Gunicorn 监听 `8013`。
- `jigua-mcp`，监听 `8765`。
- `shampoo-mcp`，宿主机服务监听 `127.0.0.1:8767`。

### 13.2 重启后未恢复的宿主机应用

`content-insight-mcp.service` 当前为 inactive，预期监听 `8766`，未发现对应监听端口。其 unit 文件存在格式错误：

```text
/etc/systemd/system/content-insight-mcp.service:1  Assignment outside of section
/etc/systemd/system/content-insight-mcp.service:12 Failed to parse output specifier
```

文件第一行异常为 `t]`，应为 `[Unit]`；`StandardOutput=append:` 也不适用于当前 CentOS 7.9 的 systemd 版本。该服务与 Dify 无关，修复前应先确认其业务用途和负责人，不能直接覆盖配置。

以下服务不应作为业务故障处理：

- `ecs_mq.service` 是一次性网卡多队列调优服务，启动后正常退出；日志显示已执行完成。
- `cloud-init-local`、`cloud-init`、`cloud-config`、`cloud-final` 失败的原因是系统缺少 `/usr/bin/cloud-init`，属于云初始化残留 unit，不是 Dify 服务。
- DataX 目录存在，但当前没有发现 DataX 常驻监听进程；不能据此判断 DataX 是否有定时任务，需另查其调度配置和日志。

### 13.3 Docker 停止与恢复时间线

本次启动周期日志显示：

```text
09:34:24  CentOS 完成启动，network.service 正常，eth0 获得 192.168.40.6
09:34:30  Docker 启动
09:34-09:36 Docker 清理大量旧网络 sandbox，并报告部分 172.31.0.1、10.250.x 网桥路由不存在
09:48:51  systemd 收到 Docker 停止请求
09:49:02  Docker 正常停止，所有容器退出
10:33:39  Docker 再次启动并完成初始化
```

这段日志显示 Docker 是收到正常 `SIGTERM` 后停止，不是本次启动周期内的内核 OOM 或 Docker 自发崩溃。停止请求来自一个通过 polkit 认证的进程，但现有审计记录没有保留具体执行命令，不能据此确认操作者。

Docker 启动时的 stale sandbox 和 `172.31.0.1` 路由清理警告说明此前 Docker 网络状态不完整；后续切换网络时必须先做只读路由核对，禁止直接删除不明网桥或路由。

### 13.4 复盘结论

本次服务器不可访问期间，主机网络服务并未显示 IP 丢失；主要业务中断点是 Docker daemon 停止，导致所有 Dify、网关和其他 Docker 应用同时不可用。恢复 Docker 后容器依靠 `restart: always` 重新启动。

后续任何服务器操作必须遵循：先只读检查、明确影响和回滚、获得负责人确认，再执行启停或配置修改。检查服务器异常时，需同时检查宿主机 systemd 服务和 Docker 容器，不能只看 Dify 页面。

## 14. 旧版 1.14 资源下线后的保留与清理记录（2026-08-07）

### 14.1 当前状态

在新版 1.16.1 已通过 `18080` 网关完成验证后，旧版 1.14 已停止并移除其运行容器及专用网络。当前生产流量只进入新版，旧版不再占用业务端口；这不等于旧版的恢复材料已经删除。

| 资源 | 当前状态 | 是否建议立即删除 | 用途/风险 |
|---|---|---|---|
| 旧版 1.14 容器 | 已停止并移除 | 已完成 | 释放运行时 CPU、内存和端口；不影响新版容器 |
| 旧版专用 Docker 网络 | 已移除 | 已完成 | 避免遗留网络和路由规则继续干扰主机 |
| 旧版 PostgreSQL、Redis、Weaviate 数据 | 保留 | 不建议立即删除 | 用于回滚、数据核对和问题追溯；删除后无法快速恢复旧版 |
| 旧版镜像 | 保留 | 观察期后可选择性删除 | 用于重建旧版容器；删除前需确认镜像没有被新版或独立中间件复用 |
| 旧版 Compose 文件及 `.env` | 保留 | 不建议删除 | 记录旧版拓扑、环境变量和回滚方式；文件本身几乎不占空间 |
| 旧版配置、备份和最终同步导出文件 | 保留 | 按备份策略归档 | 用于审计和灾难恢复；不得把含密钥的 `.env` 直接对外共享 |

### 14.2 不能整体删除的目录

`/opt/dify/docker/volumes` 不能直接整体删除。当前新版 `dify-company-plugin_daemon-1` 仍挂载其中的 `plugin_daemon` 数据目录；删除整个目录会破坏新版插件运行环境。任何数据清理必须先执行容器挂载检查，按“旧版专属、独立中间件、新版共享”逐项确认后再处理。

### 14.3 建议的清理顺序

1. 先保持旧版容器、网络已下线，连续观察新版和独立 PostgreSQL、Redis、Weaviate 至少一个完整业务周期。
2. 只读盘点旧版镜像、卷、宿主机绑定目录和 Compose 引用，确认没有被新版、网关或其他应用使用。
3. 先归档旧版 Compose、`.env`（脱敏）和数据库/向量数据备份，再删除明确属于旧版且不再需要的退出容器、镜像和数据目录。
4. 不使用 `docker system prune -a --volumes` 等全局清理命令；PostgreSQL、Redis、Weaviate 基础镜像和共享卷必须逐项核对。
5. 清理后重新验证 `18080` 网关、新版登录、工作空间、工作流、插件和知识库，确认新版无回归后再关闭回滚窗口。

### 14.4 回滚边界

旧版容器虽然已下线，但保留材料只能用于按步骤重建，不能保证数据自动追平到下线时刻。若需要回滚，必须在维护窗口内恢复旧版 Compose/配置，使用切换前保存的数据库快照或最终导出，并将网关上游切回旧版；不得直接删除新版独立中间件或覆盖当前新版数据。
