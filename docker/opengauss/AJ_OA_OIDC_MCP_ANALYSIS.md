# AJ 生产 OA 与 Octop 的 OIDC / MCP 对接分析

核验日期：2026-10-29。状态：分析与获批实施已完成。网络转发、OA OIDC、Octop provider/connector、OA 内嵌票据及一个只读身份 MCP 已完成实机验收；本文前半部分保留实施前的风险分析和基线快照，最终结果见第 6 节及 [AJ 操作记录](AJ_DEPLOYMENT_RECORD.md#2026-10-29-oidc-实际配置与验收)。

原先的 [配置变更审核稿](AJ_OA_OIDC_CHANGE_REVIEW.md) 仍作为后续环境的模板，包含分离密钥文件、客户端/provider、只读 MCP、OA 内嵌入口及回滚步骤。按用户要求，内嵌已纳入本次范围，OA/BPM 两个后端均加入 Hub 目的 origin 清单；本次生产配置已应用并记录。

本次范围为 `http://211.137.76.3:8081/oa/` 与 `/octop/`。最初通过已授权的 Spug 会话登录 `10.51.49.8`，只读检查配置、运行包、数据库和网络；随后按批准实施网络规则、OA OIDC、Hub 只读工具、Octop provider/connector 和内嵌票据配置。没有修改 OA / Octop 业务代码或镜像，没有登记 BPM 业务工具，也没有调用业务写工具。OA 后端按配置变更重启一次；Octop、Nginx、openGauss 未重启。正常登录和探针可能产生访问日志、登录会话和审计记录。

## 1. 实施前结论（历史快照）

现有 Octop 已实现 OIDC 登录及按当前调用用户自动注入 `X-Plat-Token`；生产 OA 包也包含 OIDC、Hub 和 `HubTokenTransport`。优先采用现有能力，通过配置和必要的客户端、权限、Hub 能力登记完成对接，目前没有证据要求修改 Octop 业务代码。

但生产还不具备直接启用的条件：

1. OA 当前 OIDC discovery 和 JWKS 均失败。外置与内嵌 YAML 未见 OIDC 配置，相关环境变量、JVM 参数也未见 OIDC 覆盖；运行日志出现 issuer 未配置路径及 RSA PKCS#8 签名密钥配置异常。
2. OA 没有 `client_id=octop` 的客户端。Hub 服务、目录、Operation 均为零。
3. Octop 的 SSO provider 和 connector 均为空。
4. 初检时 Octop 容器和宿主机访问公网 `211.137.76.3:8081` 均超时。已通过限定 Octop 来源的 DNAT 解除容器回访阻塞；宿主机直接使用 `10.51.49.8:8081`，未为宿主机追加全局 OUTPUT 规则。OIDC discovery / JWKS 目前能到达 OA，但仍返回原有配置错误。
5. OA 当前页面没有助手入口，运行时前端配置没有助手启用项。本地参考实现使用构建时变量；本次要求启用内嵌助手，必须核对生产前端构建产物和必要的现有构建开关，不能仅修改运行时 JS 配置便认定入口可用。

本次范围为“普通 OIDC 登录 + 一个只读 MCP 工具 + OA 内嵌助手”，执行时先验收协议及普通登录，再验收票据嵌入。完整方案包含网络、OA 外置配置、客户端和权限登记、Hub 服务及工具登记、Octop provider/connector 配置及 OA 现有前端构建开关；不仅是填写 Octop issuer。BPM 本轮先加入上游 origin 清单，具体工具需另行明确并验证认证兼容性。

## 2. 实施前生产只读核验结果（历史快照）

| 项目 | 当前结果 |
| --- | --- |
| OA 后端容器 | `spring-boot-oa-jdk21`，宿主机 `60001` 映射容器 `8080` |
| OA 外置配置 | `/APP/DockerData/spring-boot-jdk21/oa/config/application-test.yml`，profile 为 `test`，目录只读挂载 |
| OA 公网 API 前缀 | `/oa/service`；Nginx 转发为后端 `/service/` |
| Octop 容器 | `octop-opengauss-aj`，宿主机 bridge 地址 `172.17.0.1:18088` 映射容器 `8088` |
| Octop public_base_url | `http://211.137.76.3:8081/octop`，已经配置 |
| Octop 数据目录 | `/APP/DockerData/octop-aj/` |
| Nginx 配置 | `/APP/DockerData/nginx/conf/http_aj.conf`，已有 `/oa/service/`、`/octop/` 代理规则 |
| OA 运行包 | `/APP/DockerData/spring-boot-jdk21/oa/app.jar`，含 `OidcProviderService`、`PlatAssistantAccessPolicy`、`HubTokenTransport`、`HubMapping`、`HubMcpService`、`HubAdminController` 类 |
| OIDC 数据结构 | 存在 `ss.ss_oauth2_client`、`ss.ss_oidc_authorization_code`、`ss.ss_oidc_audit_log`；尚未做全部列、约束和索引与发布包的逐项比对 |
| OIDC 客户端 | `octop` 为 0 条；其他现有客户端保持原样 |
| 助手权限项 | `system:assistant:use` 已存在且未逻辑删除；尚未验证目标普通账号实际获得该权限 |
| Hub 数据 | `hub_service=0`、`hub_catalog=0`、`hub_operation=0`；`hub_settings=1`，含请求/响应大小、超时与幂等结果保留配置键 |
| Octop 配置表 | `sso_providers=0`、`connectors=0` |
| OA 前端 | `/oa/`，API `/oa/service`，hash 路由；当前未见助手入口 |

数据库查询在 `BEGIN READ ONLY` 与 `ROLLBACK` 之间执行，只取所需字段、存在性或计数。没有输出客户端 secret、签名私钥、访问令牌或完整配置。

网络和协议初检结果（实施前快照，实施后结果见 3.2 节）：

| 来源 | 目标 | 结果及含义 |
| --- | --- | --- |
| 浏览器 | 公网 OA discovery、JWKS | HTTP 200，但正文是 `isSuccess=false`、`statusCode=500` 的业务错误，不是成功的 OIDC 响应 |
| Octop 容器 | `http://211.137.76.3:8081/oa/service/.well-known/openid-configuration` | `ConnectTimeout`；关闭 httpx 环境代理后仍超时，容器未配置 HTTP 代理变量 |
| Octop 容器 | 同一公网前缀下 `/connect/jwks` | `ConnectTimeout` |
| Octop 容器 | `http://172.17.0.1:8081/oa/service` 下 discovery、JWKS | HTTP 200 + 业务错误 500；网络可达，OIDC 尚未正确配置 |
| Octop 容器 | `http://172.17.0.1:60001/service` 下 discovery、JWKS | 同上，直达后端网络可达 |
| Octop 容器 | `http://10.51.49.8:8081/oa/` | HTTP 200，OA HTML 可达；可采用该宿主机内网地址作为后台入口 |
| Octop 容器 | `http://10.51.49.8:8081/oa/service/.well-known/openid-configuration` | HTTP 200 + 业务错误 500；内网可达，仍需补齐 OIDC 配置 |
| 宿主机 | 公网 OA discovery | curl 退出码 28、HTTP code 000，连接超时 |

这些结果只能证明回访公网地址失败，尚不能断定是 NAT 回流、路由、防火墙或其他出口策略的哪一项。不能用浏览器能打开 OA 代替容器后台可达性验证。

## 3. 原拟采用的配置和身份链路（已按批准方案实施）

### 3.1 OIDC

以下表格是实施前的目标值快照；实际已应用结果以第 6 节和操作记录为准：

| 配置 | 建议值 |
| --- | --- |
| OA issuer / Octop issuer | `http://211.137.76.3:8081/oa/service` |
| OA `frontend-base-url` | `http://211.137.76.3:8081/oa/` |
| OA client_id / Octop client_id | `octop` |
| OA 客户端类型 | `OIDC`，启用 `authorization_code` |
| OA 精确 redirect URI | `http://211.137.76.3:8081/octop/api/auth/oidc/callback` |
| scopes | `openid profile email` |
| Octop dashboard_origin | `http://211.137.76.3:8081`，不包含 `/octop` |
| Octop public_base_url | 保留现有 `http://211.137.76.3:8081/octop` |
| OA 签名密钥 | AJ 专用 RSA PKCS#8 密钥，独立 key-id；先确认是否已有合法密钥来源，再决定生成；不复用其他环境的私钥 |
| client secret | AJ 专用随机 secret，通过受限配置/密钥注入和 Octop 管理接口登记，不写入仓库文档 |

按现有实现使用 authorization code、PKCE S256、state、nonce 和 RS256 校验。OA discovery 当前声明 `client_secret_post`。本次票据嵌入需要 client autoApproveScopes 包含 openid/profile/email，审核模板已精确配置这三项，不扩大其他客户端或 scope。票据同时依赖 `spring.redis.redisson.enabled=true` 和可用的既有 Redis；不新增 Redis 容器。

`system:assistant:use` 不只控制前端按钮：OA 参考实现也在授权/换码时校验。应为参与试点的用户角色最小授权，验证实际效果，不能仅因账号名为 admin 就假设通过。

OA admin 与 Octop 本地 admin 不会仅按同名自动合并。保留 Octop 本地管理登录；普通用户通过 OA OIDC 建立按 provider + subject 识别的账户。如需连接已有 Octop 账户，走产品的身份绑定流程，不直接修改身份表。

### 3.2 先解决公网回访

本次已采用下述仅 Octop 来源的 DNAT，保持现有公网 URL、issuer 和回调一致，不调整全局防火墙或放开所有出口。

如果现网无法支持回访，可评估稳定域名加内外分离解析：浏览器和后台使用相同域名 issuer，内网解析到现有 Nginx 可达地址。此方案涉及域名、Nginx、OA 前端地址、issuer、回调等统一调整，需要另行审核。

已确认 `10.51.49.8:8081` 是可达的宿主机内网入口，后台 MCP 优先采用此地址。OIDC 对外 issuer 仍需与浏览器使用的地址保持一致；如后台也要求实际走此内网入口，应通过经审核的网络/代理配置实现，并覆盖 discovery、token 和 JWKS 请求。

当前 Octop 没有单独配置内部 discovery/token/JWKS 地址的现成选项，discovery 还要求返回 issuer 与配置值匹配。因此不能简单把 Octop issuer 改成 `10.51.49.8` 或 `172.17.0.1`、OA issuer 保留公网地址。`extra_hosts` 也不能直接把一个字面量公网 IP 当作域名重映射。

#### 已实施：仅 Octop 来源的 DNAT

追加只读核验：Octop 当前 bridge IP 为 `172.17.0.8`，Nginx 为 `172.17.0.6`；宿主机 `8081` 已发布到 Nginx `80`，存在对应 docker-proxy 进程；`net.ipv4.ip_forward=1`。宿主机还已有一条公网 IP `8110` 转内网 `8110` 的 OUTPUT 规则，本次不修改该规则。

已在宿主机 NAT PREROUTING 中添加一条定向规则：入接口 `docker0`、源为运行中的 Octop 容器 IP `/32`、目的为 `211.137.76.3/32`、TCP 目的端口 `8081`，仅将目的改写为 `10.51.49.8:8081`。由现有宿主机端口监听和 docker-proxy 接入 Nginx。不能假设 DNAT 到宿主机后 Docker NAT 会再次执行；本次已通过 `ss` 确认 docker-proxy 实际监听宿主机 `8081`，并实测转发成功。

这样应用仍请求公网 issuer，HTTP Host、URL、回调及令牌 issuer 均保持公网值，连接实际进入内网同一套 Nginx；返回流量由连接跟踪处理。规则只匹配 IP/端口，不按 URL 路径筛选，因此会覆盖该 Octop 容器访问此站点 `8081` 的所有 HTTP 路径。浏览器访问与其他容器不匹配此源规则。

实施前保存了 NAT 基线和容器镜像/启动时间。规则唯一注释为 `octop-aj-oidc-hairpin`；对比前后 NAT 规则确认仅新增这一条，原规则顺序不变。未清空规则、全量恢复防火墙或更改默认策略。宿主机的 firewalld、iptables、nftables 管理服务均为 inactive / disabled，本次没有启用它们或保存 Docker 全量动态规则。

持久化使用新增的 `octop-aj-oidc-network.service` / `.timer`：开机后 45 秒触发，之后在上次任务结束约 60 秒后核对一次（另有 5 秒调度精度）。脚本实时读取 `octop-opengauss-aj` 的 bridge IP，仅维护自身标记的规则；已有正确规则时不重建，保留计数器；容器停止或查不到时删除旧规则。容器重建或 Docker 重启后的恢复是周期性的，正常调度下约一分钟，不是实时事件响应。

新增的是生产运维脚本和系统配置，不只是临时测试文件：仓库来源为 [operations/aj-oidc-network.py](operations/aj-oidc-network.py) 及同目录下两个 systemd 文件；远端副本在 `/APP/DockerData/octop-aj/operations/oidc-network/`，脚本目录 0700、文件 0600。systemd unit 安装在 `/etc/systemd/system/`。没有修改 Octop 业务代码或镜像。临时模拟验证文件位于本地 `/tmp`。

实际审计目录为 `/APP/DockerData/octop-aj/operations/octop-oidc-network-20261029T142042-bdjE/`，包括规则基线、探针结果、容器基线、安装文件摘要、精确回滚脚本。操作和命令详见 [AJ 操作记录](AJ_DEPLOYMENT_RECORD.md#2026-10-29-oidc-公网地址回访转发) 与 [AJ 发布手册](AJ_DEPLOYMENT_RUNBOOK.md#8-aj-oidc-公网地址回访转发)。宿主机核验直接使用内网 URL，没有额外增加全局 OUTPUT 转发。

已验证：Octop 容器以原公网 URL 请求 discovery、JWKS、`/oa/`、`/octop/api/health` 均 HTTP 200，规则最后核验命中 8 次；宿主机内网 OA 返回 200。discovery / JWKS 的正文仍为 `isSuccess=false`、业务码 500，故只判定网络通过，不判定 OIDC 登录成功。重复运行维护任务没有改变规则及计数器；timer 为 enabled / active，任务 Result=success、ExecMainStatus=0。Octop、OA、Nginx、openGauss 的启动时间和镜像与基线完全一致。没有通过重启生产主机或重建容器来验证恢复；IP 变化、停止清理和精确回滚行为只做了隔离模拟。

### 3.3 MCP / X-Plat-Token

推荐链路：当前用户 OA OIDC 登录 → Octop 加密保存该用户的 OA access_token → 匹配指定 MCP 地址 → 自动加入 `X-Plat-Token` → OA Hub 校验当前身份 → 以 `caller_relay` 按服务配置传递至上游 → 上游继续做用户及数据权限校验。

生产 Hub 没有已登记的服务，不能把其他环境的 `aj` 服务或 `/mcp` 路径直接当作生产现状。

1. 先选择一个已确认无业务写入副作用的 OA REST 查询接口，登记为 Hub 的 REST Operation，发布到一个专用 MCP 目录。后续按需扩展 BPM/工作流。
2. 服务 base_url 必须是不含路径的 origin。本次 Hub 目的清单配置 OA `http://10.51.49.8:60001` 和 BPM `http://10.51.49.8:60003`，不写 `/*`；路径放在 Operation path 中。加入清单不代表已登记 BPM 服务或工具。工作流端口为 `60002`，本轮不加入目的清单，具体路径与认证方式另核验。
3. 上游采用 `caller_relay` 的前提是该系统接受此次 OA OIDC 签发的当前用户令牌。OA 自身、BPM、工作流需分别验证，不能因同主机部署便认定共享认证。若不接受，再评估现有受控凭证适配器，不改成共享管理员令牌。
4. Octop MCP 地址建议走现有 Nginx 的内网入口：`http://10.51.49.8:8081/oa/service/<service-code>/<catalog-code>/mcp`，已验证该 IP/端口的 OA 入口可达；`172.17.0.1:8081` 也可达。这只是待登记的路径模板，实际编码确定后再写入白名单和连接器。
5. 连接器采用 HTTP / Streamable HTTP，URL 与 `plat_mcp_urls` 精确一致。优先只暴露选定目录，避免全服务通配符。
6. 连接器不手填 `X-Plat-Token`、`Authorization`、`token`。Octop 会按调用用户自动注入，重复手填会触发冲突。也不把普通 OA REST URL 当成该令牌传输机制的通用入口。

必须在真实 OIDC 登录之前配置 `plat_mcp_urls`；现有实现只有在名单非空时才保存供 MCP 使用的 OA 凭证。若登录后才补名单，需要重新 OIDC 登录。当前没有自动刷新 OA 凭证的接线，到期需重新 OA/OIDC 登录；不能承诺长期无人值守调用始终有效。

## 4. 白名单及入口边界

| 层次 | 配置/控制 | 此次要求 |
| --- | --- | --- |
| OA OIDC 客户端 | redirect_uris | 只登记上述 `/octop/api/auth/oidc/callback` 精确 URL |
| OA OIDC | issuer、frontend-base-url | 使用相同生产站点的稳定地址，issuer 带 `/oa/service`，前端地址带 `/oa/` |
| OA 用户权限 | `system:assistant:use` | 按角色授权并验证目标普通用户；不授予 Hub 管理权限来替代运行权限 |
| OA 匿名端点 | discovery、authorize、token、JWKS | 当前代码已有 `@PermitAll`；先验证真实请求，不扩大到 `/service/**`、Hub 管理或业务接口 |
| OA Hub | `plat.hub.allowed-origins` | **上游目的地址清单，不是浏览器 CORS**；仅加入已核验的业务 upstream origin |
| OA Hub | 服务、目录、发布 Operation、业务权限 | 只公开所需工具；token 有效并不等于有权执行所有业务 |
| Octop OIDC extra | `plat_mcp_urls` | 精确登记可信内网 MCP 完整 URL；避免整个 OA API 前缀通配 |
| Octop MCP URL 安全策略 | 内网 HTTP / 公网 HTTPS 校验 | 既有策略会拒绝公网明文 HTTP MCP；采用可达的内网 HTTP 入口，不为此次对接全局降级校验 |
| OA 内嵌（本次范围） | `plat.ss.oidc.embed-origins` | `http://211.137.76.3:8081`，origin 不带路径 |
| OA 内嵌（本次范围） | `embed-client-id` | `octop`；该客户端自动授权范围仅 openid/profile/email |
| Octop 内嵌（本次范围） | `extra.plat_embed_origins` | `http://211.137.76.3:8081`，拒绝通配与任意来源 |
| OA 内嵌票据 | 既有 Redis / RedissonClient | 开启所需的 redisson.enabled，验证票据一次消费和 60 秒有效期；不新增 Redis 服务 |
| 浏览器 iframe | postMessage、CSP、X-Frame-Options | 核验现有响应头及严格 source/origin/state 检查，必要时仅放行本 origin |
| OA XSS 过滤 | `plat.web.xssExcludeUrls` | 参考过滤器按后端 requestURI 匹配；只在协议需要时排除实际 MCP 精确路径，仍保留认证；不直接照搬 `/mcp/**`，也不使用公网 `/oa/` 前缀作为后端匹配路径 |

`X-Plat-Token` 在参考实现中只适用于 Hub runtime 路由，例如后端 `/service/<service>/mcp`、`/service/<service>/<catalog>/mcp` 等；不适用于 Hub 管理接口或任意 OA REST。Octop 的该类客户端不跟随重定向，以避免令牌发送到其他地址。

OA 与 Octop 在当前公网部署同 origin。正常浏览器 OIDC 跳转不要求额外全站 CORS，MCP 是后台调用，也不靠浏览器 CORS 放行。

## 5. 实施顺序、影响和回滚（历史计划）

1. **备份并记录基线。** 保存 OA 外置配置、相关容器启动定义、Nginx 配置和即将新增记录的标识；敏感备份放服务器受限目录，不回填到仓库。按现有备份流程准备可恢复点。
2. **网络闭环（已完成网络层）。** 本次已配置仅 Octop 来源的转发并验证公网 URL 可达；继续补齐 OA 协议配置后再验收标准 discovery/JWKS。域名方案作为未来备选。
3. **结构 preflight 与 OA 配置。** 比对现有 OIDC/Hub 所需列、约束和索引；只补确实缺少的增量，不执行参考环境全量初始化 SQL。AJ 已存在 Hub schema，而参考首次初始化脚本会拒绝这种现状。准备 issuer、独立签名密钥、Hub enabled 和精确 upstream 清单，合并到现有配置而非覆盖 YAML。
4. **安排 OA 配置生效窗口。** 外置 YAML 改动通常需要重启 OA 后端，会短暂影响 OA；Spug 与 OA 共用入口时不得重启整个 Nginx/宿主机。仅在实际修改 Nginx 时先 `nginx -t`，再平滑 reload。
5. **登记客户端及最小权限。** 优先使用 OA 已有管理能力，核对 secret、回调、scope、有效期和角色权限；若必须使用 SQL，先提供带精确条件、可回滚的增量脚本。考虑客户端权限缓存，不能默认直接写库后即刻生效。
6. **登记一个只读 MCP 能力。** 核验 upstream 路径、token header、`caller_relay`、业务权限和发布范围；只发布试点目录，不批量导入其他环境服务。
7. **配置 Octop。** 通过产品管理 API/UI 登记 provider、extra 白名单和连接器，先测试 discovery/JWKS，再由用户真实 OIDC 登录，验证 MCP。该部分优先使用动态配置，无需为此重打 Octop 镜像。
8. **本次启用 OA 内嵌入口。** 本地参考代码使用 `VITE_PLAT_ASSISTANT_ENABLED=true` 和 `VITE_PLAT_ASSISTANT_URL=http://211.137.76.3:8081/octop`，对应构建变量已提供审核示例。先确认生产前端含相应布局、OIDC 登录/授权页面和票据协议能力；如仅未启用，则按 OA 原构建发布流程准备新前端，不能仅修改 `_app-config-*.js` 便宣称完成。核验 Redisson、客户端自动授权范围、内嵌来源、助手权限、聊天和切换账号隔离；如缺业务代码能力，报告缺项而不擅自开发。

回滚按影响面处理：停用本次新增 Octop provider/connector 和 OA Hub 试点入口；恢复本次修改的外置配置并在窗口内重启 OA；如改了网络或 Nginx，恢复本次规则并验证；前端回切原产物。数据库只撤回本次登记记录和精确角色授权，不删除现有 OIDC/Hub 表、不还原整库覆盖其他用户的新数据。已经建立的 Octop 用户和身份不批量删除，停用 provider 不等于撤销所有已签发的会话，需要另行评估试点凭证和会话撤销。

## 6. 实际实施与验收

2026-10-29 已按批准方案完成生产配置，实际结果如下：

- OA OIDC issuer、独立 RSA PKCS#8 签名密钥、`octop` authorization-code 客户端和精确回调已生效；discovery/JWKS 通过。
- Hub 已发布 OA `oa.current-user` 只读 Operation 和 `octop-readonly` MCP 目录；没有登记 BPM 业务工具或调用业务写接口。
- Octop provider/连接器已启用，OA 只读 MCP 探测返回 `oa_current_user`；OA admin 登录后新会话真实调用返回自身身份 `userId=1`、`username=admin`。
- OA/Octop 内嵌票据链路已用完整 PKCE 参数验收成功；embed origin 为 `http://211.137.76.3:8081`，OA/BPM 上游 origin 为 `http://10.51.49.8:60001`、`http://10.51.49.8:60003`。
- Octop health 保持 `ok=true`、`db=true`。生产未重建 Octop、Nginx 或 openGauss 容器；OA 后端仅按配置变更重启一次。

详细操作标识和审计目录见 [AJ 操作记录](AJ_DEPLOYMENT_RECORD.md#2026-10-29-oidc-实际配置与验收)。普通账号权限隔离、令牌过期/重复消费拒绝、BPM 业务工具和完整业务回归仍属于后续验收。

## 7. 后续验收清单

| 验证 | 通过标准 |
| --- | --- |
| 协议与网络 | 浏览器和 Octop 后台均可获得标准 discovery/JWKS；issuer 一致，JWKS 有正确 RSA 公钥；HTTP 200 + 业务错误不算成功 |
| 普通 OIDC 登录 | 首次、重复登录成功；回调保留 `/octop`；拒绝错误回调、state/nonce/PKCE 不匹配；本地管理员仍可登录 |
| 权限 | 具有助手权限的普通用户可登录；缺少权限的用户被拒绝；不要求普通用户成为 OA/Octop 管理员 |
| MCP 身份 | tools/list 成功；一个明确只读的 tools/call 成功；Hub/上游审计归属实际用户，不是共享 admin |
| 用户隔离 | 两个普通 OA 用户独立调用，仅返回各自被授权的数据；不能取得对方令牌；非可信 URL 不携带 OA 凭证 |
| 失效处理 | 未 OIDC 登录、令牌过期/撤销、重复认证头时失败且不会回退使用管理员凭证；重新登录可恢复 |
| 内嵌（本次必验） | OA 助手入口、票据握手、已有聊天能力可用；切换/退出 OA 后不沿用上一用户上下文；不可信 origin、票据过期与重复消费被拒绝 |
| 发布回归 | OA 原登录、原业务页面、Octop 本地管理入口保持可用；业务写工具待单独明确场景后再测 |

本次已完成管理员 OIDC、只读 MCP 和内嵌票据的实机链路验收；普通账号实际授权隔离、上游业务工具的 token 兼容性、令牌过期/重复消费拒绝、生产前端切换账号隔离及完整业务回归仍需在后续阶段确认。原公网回访失败的底层根因尚未查明，当前通过限定来源的本机转发绕过。

本轮新增分析文档、生产运维脚本和两个 systemd 配置，并更新相关手册、操作记录与索引，未改业务代码。运维脚本的隔离模拟、Ruff 检查、远端 systemd 配置校验及网络实测通过；未运行会自动格式化现有工作区的 `make all`。文档完成后检查空白错误。

## 8. 参考位置

- Octop：`src/octop/infra/auth/sso/discovery.py`、`providers/oidc.py`、`service.py`，`src/octop/infra/auth/plat_credentials.py`、`src/octop/infra/connectors/plat_auth.py`、`src/octop/infra/users/manager.py`。
- 平台源码：`/Users/guixiaolong/File/projectSpace/odc/plat/moudule-plat` 下的 `OidcProperties`、`OidcProviderService`、`PlatAssistantAccessPolicy`、`HubMapping`、`HubProperties`、`HubTokenTransport`、`HubRuntimeRoutes` 及前端 `layouts/assistant.ts`、`layouts/basic.vue`、`components/plat-assistant`。
- 配置参考：`/Users/guixiaolong/File/projectSpace/odc/plat/multi-plat/backend/composite-host/src/main/resources/application-remote.yaml` 与 `docs/remote-hub-oidc-init-20261022.md`；只借鉴结构，不复制其密钥、账号或环境地址。

知识库继续使用原 SQLite，Agent Memory 保留既有 openGauss 方案；本次认证和连接器对接不调整存储架构。
