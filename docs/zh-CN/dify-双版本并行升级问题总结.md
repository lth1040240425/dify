# Dify 1.14 至 1.16.1 并行升级复盘

本文记录 2026-08-06 至 2026-08-07 在 `192.168.40.6` 上完成的 Dify 升级，重点保留最终架构、已确认根因、解决方案和后续约束。过程中的临时状态以本节“最终状态”为准。

## 1. 最终生产状态

### 1.1 流量与应用

```text
调用方
  -> 192.168.40.6:18080
  -> dify-edge-gateway（host 网络）
  -> 新版 Nginx:18081
  -> Dify 1.16.1 API / Web / Worker / Plugin / Agent
```

调用方继续使用原地址 `http://192.168.40.6:18080`，无需修改代码或端口。网关配置位于：

```text
/root/dify-edge-gateway/nginx.conf
```

网关已配置 WebSocket、SSE/流式响应和长连接超时。旧版 1.14 容器及专用网络已停止并移除，不再承担生产流量。

### 1.2 独立中间件

| 中间件 | 当前容器/数据库 | 网络 |
|---|---|---|
| PostgreSQL | `dify-infra-db_postgres-1`；`dify`、`dify_plugin` | `10.250.10.0/24`、`10.250.11.0/24` |
| Redis | `dify-infra-redis-1` | `10.250.12.0/24` |
| Weaviate | `dify-infra-weaviate-1` | `10.250.13.0/24` |

独立中间件 Compose 位于 `/opt/dify-infra/docker-compose.yaml`。新版业务配置使用：

```text
DB_HOST=db_postgres
DB_DATABASE=dify
DB_PLUGIN_DATABASE=dify_plugin
REDIS_HOST=dify-infra-redis
WEAVIATE_ENDPOINT=http://weaviate:8080
```

实际密钥只保存在受控 `.env`，不得写入本文。

### 1.3 PostgreSQL 局域网访问

独立 PostgreSQL 已映射到 `192.168.40.6:5432`。DataGrip 使用数据库 `dify`；PostgreSQL 角色 `shuhang` 为只读角色，只允许连接数据库、使用 `public` schema 和查询现有/后续表及序列，不具备写入、建库、建角色或超级用户权限。文档不记录其密码。

### 1.4 必须保留的现场约定与备份

| 项目 | 位置/约定 |
|---|---|
| 新版环境备份 | `/root/dify-company-1.16.1/.env.bak-before-independent-db-rename-20260807`、`.env.bak-before-redis-alias-20260807` |
| 新版 Compose 备份 | `/root/dify-company-1.16.1/docker-compose.yaml.bak-before-redis-network-20260807` |
| 独立中间件 Compose 备份 | `/opt/dify-infra/docker-compose.yaml.bak-before-lan-5432-20260807` |
| 最终数据库 dump | `/opt/dify-infra/final-sync-20260807/` |
| 网关配置 | `/root/dify-edge-gateway/nginx.conf` |
| 网关/iptables 历史备份 | `/etc/sysconfig/iptables.bak-20260806-edge-gateway` |

备份目录和配置文件中的密钥不得复制到 Git、工单或聊天记录。执行清理前必须先确认这些备份仍可读取，并重新生成 SHA-256 校验清单。

## 2. 数据迁移结果

### 2.1 PostgreSQL

正式切换时先停止新版写服务，再从当时的 1.16.1 源库生成最终逻辑备份，恢复到临时库并核对后交换为正式库名：

| 数据库 | 最终名称 | public 表数量 | 结果 |
|---|---|---:|---|
| 核心业务库 | `dify` | 136 | 源库与目标库逐表行数一致 |
| 插件库 | `dify_plugin` | 13 | 源库与目标库逐表行数一致 |

账号、工作空间、应用、工作流、消息、知识库和运行记录已同步。Dify 业务账号与工作空间 `shuhang` 已确认存在。PostgreSQL 登录角色属于集群全局对象，不包含在普通数据库 dump 中，因此按需单独创建并授权。

最终备份保存在：

```text
/opt/dify-infra/final-sync-20260807/dify_company.dump
/opt/dify-infra/final-sync-20260807/dify_company_plugin.dump
```

切换前的独立库副本保留为：

```text
dify_pre_cutover_20260807
dify_plugin_pre_cutover_20260807
```

### 2.2 Redis 与 Weaviate

- Redis 切换前未发现待执行的 List/Stream 队列；缓存和 Kombu binding 可重建。
- Weaviate 数据约 `636MB`，切换后就绪检查返回 `200`。
- 新版 API、Worker、Worker Beat、Plugin Daemon 和 Agent Backend 均已接入独立中间件网络。

## 3. 关键问题、根因与解决方案

| 问题 | 已确认根因 | 解决方案 |
|---|---|---|
| 旧版 API 间歇性 `400/401/500` | 新旧 Compose 共享网络，`api` 等通用别名解析到错误容器 | 隔离 Compose 网络和服务发现，不让新旧 API 共享通用别名 |
| 新版工作流 `Stopped`、`0 Tokens` | Worker 无法解析或访问 `db_postgres` | 将 API、Worker、Beat 加入正确数据库网络并验证 DNS |
| 插件管理接口 `401/500` | API 访问到错误 Plugin Daemon，或 Plugin Daemon 缺少插件数据库网络/别名 | 隔离插件网络，保证 `db_postgres` 别名和内部认证匹配 |
| 新版仍访问旧 Redis | `REDIS_HOST=redis` 命中新版默认网络中的内置 Redis | 改为 `dify-infra-redis`，并将 Plugin Daemon 加入独立 Redis 网络 |
| 旧版页面服务端请求 `localhost` | 旧版 `.env` 的 `CONSOLE_API_URL`、`APP_API_URL` 为空，SSR 回退到容器内 localhost | 并行期显式配置旧版入口地址；不要用新版地址覆盖旧版配置 |
| 页面持续加载 | 后端 API、Provider 或插件请求失败；不是单纯前端问题 | 先查浏览器失败请求，再结合 API/Plugin/Worker 日志定位 |
| 新旧版互相跳登录 | 浏览器 Cookie 不区分端口，相同 IP 下 Token Cookie 被覆盖 | 并行期使用不同主机名/浏览器配置；旧版下线后该冲突消失 |
| 部分网段 Ping、SSH、HTTP 超时 | Docker 自动分配的 bridge 子网与办公网/VPN 路由重叠 | 删除冲突网络并为 Compose 显式分配已核对的子网 |
| 所有 Docker 应用同时不可用 | Docker daemon 收到正常停止请求并退出 | 恢复 Docker，并检查 systemd 与容器自启动；现有日志不能确认操作者 |

服务器当时的内存和磁盘仍有余量，没有证据表明上述 API、登录和网络故障由资源不足直接引起。

## 4. Docker 网段冲突复盘

### 4.1 影响与根因

并行部署曾自动创建与真实客户端重叠的 Docker 网段：`172.21.0.0/16`、`172.23.0.0/16`、`172.28.0.0/16`。Linux 将这些目标路由到 Docker bridge，导致响应不能经物理网卡返回真实客户端，表现为 Ping、SSH 和 HTTP 超时。

`DOCKER-ISOLATION-STAGE-*` 中的 `DROP` 是 Docker 的跨 bridge 隔离规则，但本次外部客户端不可达的直接原因是路由前缀重叠，不能仅凭看到 `DROP` 就判断防火墙是根因。

### 4.2 当前与后续规划

当前生产使用的独立中间件网段：

```text
10.250.10.0/24  PostgreSQL 核心库网络
10.250.11.0/24  PostgreSQL 插件库网络
10.250.12.0/24  Redis 网络
10.250.13.0/24  Weaviate 网络
10.250.21.0/24  SSRF/Sandbox 相关网络
10.250.23.0/24  Agent Sandbox 网络
```

这些现有网段已经投入运行，不应为了统一命名直接修改。后续新网络优先从运维预留地址池划分：

```text
10.200.0.0/16
10.201.0.0/16
```

每个项目再分配独立 `/24`。创建前必须核对宿主机 `ip route`、办公网、VPN、云 VPC、WSL、Docker 和 Kubernetes 路由；`10.x` 不是天然无冲突。

## 5. 网关与防火墙约束

`18080` 是稳定业务入口，网关上游当前只指向新版。防火墙或 iptables 持久化规则变更必须同时核对：

- 当前监听进程和 Docker 端口映射；
- 允许访问 `18080` 的真实调用方网段；
- `/etc/sysconfig/iptables` 与运行态规则是否一致；
- 是否残留旧版 DNAT 或 FORWARD 规则；
- 重启 Docker/服务器后的恢复行为。

历史切换期间确认需要访问入口的来源包括 `192.168.40.0/24`、`192.168.10.0/24`、`172.21.0.0/16`、`172.23.0.0/16` 和 `172.28.0.0/16`。这不是永久白名单；每次变更前必须以当前调用方清单和运行态规则重新核对，不能照抄旧规则。

禁止根据单张 `iptables -L` 截图直接删除规则。任何网络改动应先备份、说明回滚命令并获得负责人确认。

## 6. 旧版下线与遗留资源

旧版 1.14 的容器和专用网络已移除；旧版镜像、数据、Compose、`.env` 和最终备份仍保留，用于数据核对和有限回滚。

以下约束仍然有效：

1. `/opt/dify/docker/volumes` 不能整体删除；新版 `dify-company-plugin_daemon-1` 仍挂载其中的 `plugin_daemon` 目录。
2. PostgreSQL、Redis、Weaviate、Nginx 等基础镜像可能被新版或独立中间件复用，不能按镜像名称批量删除。
3. 新版 Compose 中的内置 Redis 容器不再是生产 Redis，但删除前仍需核对容器连接、环境变量和队列。
4. 旧版数据在下线后不再接收新写入，不能把它视为随时可无损切回的实时副本。
5. 禁止执行 `docker system prune -a --volumes`；只能在挂载和依赖盘点后逐项清理。

容易误删的共享资源：

- `/opt/dify/docker/volumes/plugin_daemon` 仍被新版 Plugin Daemon 挂载，不能随旧版目录整体删除。
- PostgreSQL、Redis、Weaviate、Nginx 基础镜像可能被新版或独立中间件复用，不能按名称批量删除。
- 独立 PostgreSQL 的 `5432` 现在服务于 `dify`，不是旧版数据库；删除旧版容器时必须先确认端口归属。

建议至少经过一个完整业务观察周期，再归档配置和备份、删除旧版专属镜像及数据。清理后必须重新验证网关、登录、工作空间、工作流、插件和知识库。

## 7. 当前回滚边界

旧版容器已移除，回滚不再是简单切换到 `127.0.0.1:18082`。需要回滚时必须在维护窗口内：

1. 停止新版写入并评估切换后新增数据；
2. 恢复旧版 Compose、环境配置和已验证镜像；
3. 使用切换前数据库/向量数据快照重建旧环境；
4. 完成旧版健康和业务验证后再切换网关上游。

数据库 schema、插件状态和切换后新增数据都可能阻止无损降级。未经业务确认不得用旧快照覆盖当前独立中间件。

## 8. 验证基线

完成中间件、网络、网关或镜像变更后，至少验证：

- `http://192.168.40.6:18080/console/api/system-features` 返回 `200`；
- API 与独立 PostgreSQL 健康；Redis 可解析并认证；Weaviate ready 返回 `200`；
- 登录、Token 刷新和完整工作空间列表；
- 应用、工作流、插件、知识库接口；
- 至少一个真实工作流和一个已启用模型供应商短调用；
- 外部调用方的普通与流式 API；
- DataGrip 使用只读角色查询 `dify`，写入权限被拒绝；
- 宿主机路由、Docker 网络和业务来源网段无重叠；
- CPU、内存、磁盘和关键容器日志没有异常。

## 9. 运维执行纪律

1. 默认只读检查；修改服务器前必须说明目标、影响、命令范围和回滚方式。
2. 停服、数据库同步、Compose 重建、网络、防火墙和数据删除必须获得负责人确认。
3. 不停止整个 Docker daemon 来处理单个 Dify 服务。
4. 故障检查同时覆盖宿主机 systemd、路由/iptables、Docker daemon、容器健康和应用日志。
5. 未验证的信息不得写成已确认根因；保留命令输出、时间范围和验证结果。
