# AJ OA → Octop OIDC / MCP 配置变更审核稿

> 历史审核模板：本稿记录实施前的目标值、风险和回滚边界。获批后的实际配置与验收见 [AJ 操作记录](AJ_DEPLOYMENT_RECORD.md#2026-10-29-oidc-实际配置与验收)；示例中的占位符和停用状态不能直接用于生产。

日期：2026-10-29。状态：仅本地准备，未上传或应用本稿中的配置，未登记客户端、权限、工具和连接器，未重启 OA。已实施的网络转发保持不变，见 [分析及网络实测](AJ_OA_OIDC_MCP_ANALYSIS.md)。

本稿采用现有产品能力，不修改 Octop / OA 业务代码，不重建后端镜像，不新增容器或运维程序。本轮新增文件均为文档、YAML / JSON 及前端构建变量示例；没有再修改已安装的网络维护脚本。按用户最新要求，本次范围已包含 OA 内嵌助手及 OA/BPM 上游 origin；如现有 OA 前端构建未启用助手开关，需要使用现有源码和构建配置准备、审核前端产物，不修改业务逻辑。

## 1. 本次拟实施范围

| 项目 | 待审核的具体安排 |
| --- | --- |
| 公网地址 | OA `http://211.137.76.3:8081/oa/`，Octop `http://211.137.76.3:8081/octop` |
| issuer | OA 与 Octop 均为 `http://211.137.76.3:8081/oa/service` |
| OIDC 回调 | 仅 `http://211.137.76.3:8081/octop/api/auth/oidc/callback`，精确匹配，不加通配符 |
| 客户端 | `client_id=octop`、`authType=OIDC`，只启用 authorization_code，scope 为 openid/profile/email |
| 授权方式 | 为内嵌票据登录，将 autoApproveScopes 精确配置为 openid/profile/email；不启用永久令牌和 refresh_token grant，不为其他客户端修改授权范围 |
| 令牌有效期 | 本历史模板曾建议 access token 3600 秒；AJ 实际于 2026-10-30 调整为 `1576800000` 秒（约 50 年）。refresh 有效期即使填写也不代表启用 refresh grant 或 Octop 自动刷新；新环境必须单独审核并验收 |
| 试点用户 | 使用经确认的普通账号和现有/专用试点角色，授予已有 `system:assistant:use` 权限；不扩大管理员权限或批量授权全体用户 |
| Hub 试点 | 拟新建 service `oa`、目录 `octop-readonly`，仅一个读取自身身份的工具 `oa_current_user` |
| MCP 内网入口 | `http://10.51.49.8:8081/oa/service/oa/octop-readonly/mcp`；这是拟登记路径，当前尚不存在有效发布 |
| Hub 上游允许清单 | OA `http://10.51.49.8:60001`、BPM `http://10.51.49.8:60003`，均为精确 origin，不带路径或 `/*` |
| Hub 首个实际工具 | 仍只调用 OA `GET /service/ss/user/profile/get`；caller_relay 保持当前用户身份。BPM 本轮加入上游允许清单，具体服务/工具与 OA 令牌兼容验证另行明确，不因加白名单便认定 BPM 已接通 |
| 内嵌入口 | 本次启用 OA 内嵌助手；OA embed-origins 与 Octop plat_embed_origins 均为 `http://211.137.76.3:8081`，不带 `/oa` 或 `/bpm` |
| 内嵌票据 | 复用现有 Redis，启用 `spring.redis.redisson.enabled=true`；核验现有 Redis 连接配置和 RedissonClient，无新增 Redis 容器 |
| 其他业务 | 暂不登记 BPM/工作流业务工具，不执行审批、发起流程、增删改业务等调用；不默认修改 BPM 前端 |

选择自身身份接口的依据：本地参考 `UserProfileController.getUserProfile()` 从已认证上下文取 userId 和组织，不接受调用方传入用户编号；拟定响应映射只返回 id、username。运行包对应路由、认证头、租户和组织归属仍须实施前核验，不能仅凭源码认定实机已通过。

## 2. 配置文件与密钥存放

| 本地审核文件 | 计划用途 |
| --- | --- |
| [OA 配置增量](oidc-aj/oa-application-test.merge.example.yml) | 合并到宿主机 `/APP/DockerData/spring-boot-jdk21/oa/config/application-test.yml`；不能整文件覆盖 |
| [签名密钥文件结构](oidc-aj/oa-signing-key.example.yml) | 实际文件仅在服务器生成，宿主机名为同目录 `oidc-aj-signing-key.yml`，容器内 `/opt/app/config/oidc-aj-signing-key.yml` |
| [OA 客户端登记体](oidc-aj/oa-octop-client.create.example.json) | OA 管理接口创建客户端，初始 status=0，确认无冲突后再创建 |
| [Octop provider 登记体](oidc-aj/octop-oidc-provider.example.json) | Octop 管理接口写入配置，初始 enabled=false，先验证再开放入口 |
| [Hub 三份请求体](oidc-aj/hub-readonly.example.json) | 分别取 service、operation、catalog 作为独立请求体，不把整个文件直接发送给一个接口 |
| [OA 前端构建变量](oidc-aj/oa-frontend.build.example.env) | 启用现有 OA 前端的助手入口，目标为公网 `/octop`；是构建变量，不能仅修改运行时配置 JS |

OA 增量中的 `spring.config.import` 导入同一现有只读挂载目录内的签名密钥文件，不依赖新增容器环境变量，也不假设 OA 会自动读取 Octop 的 `.env`。实施前核验运行包 Spring Boot 支持该配置方式；保留已有 import、plat 和 XSS 排除项，只追加所需键，避免 YAML 重复键或列表覆盖。密钥文件必须先安装并确认 OA 进程可读，再切换配置和重启；不使用 optional import 掩盖缺失文件。

所有 `<...>` 都是占位符，禁止直接应用示例。审核后先确认 AJ 是否已有合法签名密钥来源；如无，现场使用 OpenSSL 生成独立 RSA 3072 位私钥，以 PKCS#8 DER 的单行 Base64 写入密钥文件，校验私钥格式和公钥指纹但不显示私钥。key-id 拟为 `aj-oa-oidc-20261029`，不得复用其他环境私钥或覆盖已有 AJ 密钥。

签名密钥文件以 OA 进程可读的有效 UID/GID、0600 权限存放；先核对进程身份，不递归改变整个配置目录的属主和权限。现有目录只读挂载不妨碍从宿主机安装文件，不需要为导入文件重建容器。

client secret 与 RSA 私钥是两个不同秘密。client secret 拟在服务器随机生成 32 字节后做 URL-safe 编码，保存到 `/APP/DockerData/octop-aj/config/oidc-aj-client.env`（0600，root 所有），只作为受限登记凭据的保管文件，不自动注入应用。OA 客户端和 Octop provider 使用同一 secret。敏感请求体在受限目录暂存，登记后按现场保管要求清理；不放进命令行、终端回显、Git 或普通日志。

## 3. 白名单与权限的具体增量

1. OA 客户端只登记上述完整回调；不更改其他已有客户端。
2. OA `plat.hub.allowed-origins` 加入 OA `http://10.51.49.8:60001` 和 BPM `http://10.51.49.8:60003`。这是上游目的 origin 清单，不是 CORS，也不是路径通配规则；代码按 scheme/host/port 比较，`/*` 不提供路径限制，不采用这种写法。用户消息中的 `600001` / `600003` 按已核验的 `60001` / `60003` 处理，前者超出 TCP 端口范围。暂不加入工作流端口。
3. OA XSS 排除清单只追加实际后端 MCP 路径 `/service/oa/octop-readonly/mcp`，避免 JSON-RPC 正文被改写；保留该路由原有认证。不能使用公网 `/oa/service/...` 作为后端 requestURI，不全局关闭 XSS。
4. 不扩大 OA permitAllUrls。OIDC 标准公开端点已有对应注解，Hub 管理和 MCP 调用继续鉴权。
5. Octop extra.plat_mcp_urls 精确为上述 MCP 内网完整 URL，不加全站通配符。连接器不手填 X-Plat-Token、Authorization 或 token。
6. 双方 embed origin 清单均为 `http://211.137.76.3:8081`，使用 client `octop`。同 origin 不按 `/oa/`、`/bpm/` 路径区分，不能把该清单作为两应用路径隔离机制；如未来需要不同信任边界，应采用不同 origin。只核验现有 iframe 响应头，确需改动时限于该 origin，不全局放宽 CORS。
7. 试点用户需要真实获得 `system:assistant:use`；Hub Operation 同时要求该权限，上游仍校验用户及组织。管理登记使用现有获授权管理员，普通账号不授予 `hub:manage` 或 OAuth2 客户端管理权限。

登录的身份以 OA provider + subject 建立。保留 Octop 本地 admin，不按同名将 OA admin 自动合并到本地 admin，不直接改库绑定身份。

## 4. 审核后的执行步骤

### A. 只读预检和备份

- 复核现场配置未被他人修改、容器和镜像仍符合基线、网络 timer 正常，公网 issuer 从 Octop 容器可达。
- 核对实际运行包的相关 API、表列、约束及索引，确认 OAuth2 客户端 `octop` 和 Hub service `oa` / catalog `octop-readonly` 仍无冲突；存在即停止调查，不能覆盖或重复创建。
- 核验 OA 运行包的 Spring Config import 支持、进程 UID/GID、目录挂载及配置读取权限；从 OA 后台实际网络验证 `10.51.49.8:60001` 可达，核对平台认证头是否仍为 token。
- 核验 BPM `10.51.49.8:60003` 可达，但不调用业务写接口；加入目的清单不改变 BPM 认证。核对 OA 既有 Redis 连接参数、票据所需的 Redisson 初始化条件和现有 Redis 服务可用性，不覆盖 Redis 地址/口令，不新建服务。
- 确认两名普通试点账号及试点角色、组织和租户归属；未确认前不做角色授予。确认 OA 前端已有普通 OIDC 登录/授权路由；缺失时停止普通登录验收，不通过自动授权绕过。
- 核验 OA 已部署前端是否包含助手布局、票据协议及所需开关；如未启用，核对当前发布源码版本、构建变量、静态目录挂载及回滚产物。只在现有能力完整时按构建配置启用，不为接入临时修改前端业务代码。
- 备份本次会修改的 OA 外置配置、相关登记前状态和精确角色授权差异到受限操作目录；记录现场文件摘要和运行状态。不导出或打印无关客户端 secret，不执行全量数据库初始化。

### B. 安装配置并安排一次 OA 后端重启

先生成/安装签名密钥文件，再按增量合并 OA 外置 YAML，执行无重复键解析和文件权限检查。确认没有占位符、原配置无遗漏且回滚文件可用后，在获批窗口只执行：

```sh
docker restart spring-boot-oa-jdk21
```

这是实际生效阶段的待执行命令，本轮未执行。预计 OA 后端在重启及就绪期间短暂不可用，时长以现场启动结果为准；不重启 Nginx、openGauss、BPM、工作流、Octop 或主机。若启动失败或 OA 原有登录异常，立即按第 6 节恢复配置，不继续开放 OIDC。

从浏览器入口及 Octop 容器验证标准 discovery / JWKS：issuer、authorize/token/jwks 地址准确，RSA 公钥含正确 kid，S256 与 client_secret_post 存在；不是 HTTP 200 + 业务错误。还需核验其他原有 OA 接口保持正常。

同时确认 RedissonClient 使用现有 Redis 正常初始化；票据端点后续仍要求真实 OA 登录、匹配 origin、助手权限和有效组织上下文，不能加入匿名白名单。

### C. 通过管理能力登记客户端和只读工具

优先使用已有 OA 管理 UI/API，不直接写数据库，避免遗漏校验、租户字段及缓存处理。API 路径以运行包再次核对结果为准，本地源码对应的公网接口如下：

| 操作 | 方法及路径 | 请求体/结果 |
| --- | --- | --- |
| 创建客户端 | POST `/oa/service/ss/oauth2-client/create` | OA client 示例，先保持 status=0；保存返回的实际 id |
| 启用/停用客户端 | PUT `/oa/service/ss/oauth2-client/update` | 带实际 id 的完整当前配置，status 改为 1/0；不能把分页返回的脱敏 secret 当真实 secret |
| 注册服务 | POST `/oa/service/hub/services` | Hub 示例 service 部分，初始 status=0；保存实际 service id |
| 校验工具定义 | POST `/oa/service/hub/operations/validate` | `{serviceId, definition}`，不调用上游；通过后保存草稿 |
| 保存 Operation | POST `/oa/service/hub/operations` | Hub 示例 operation 部分，填真实 serviceId，记录 operation 主键和 versionId |
| 启用服务 | POST `/oa/service/hub/services` | 按 serviceCode 保存已核对配置，将 status 改为 1 |
| 发布版本 | POST `/oa/service/hub/operations/{id}/versions/{versionId}/publish` | 填本次实际主键/版本；首次发布使用 `expectedCurrentVersionId=null` 防止覆盖并发发布 |
| 保存目录 | POST `/oa/service/hub/catalogs` | Hub 示例 catalog 部分，填真实 serviceId，先 mcpEnabled=false；发布前提是服务启用且 Operation 已发布 |

目录中的 operationIds 使用稳定业务编码 `oa.current-user`，不是数据库主键。该工具只接受空对象参数，按当前用户请求自身资料，并把响应缩减为 userId、username；API 的 `isSuccess=false` 被映射为调用失败。

先验证管理端的本地定义校验，再按获批验收执行只读调用；不调用“实际调试工具”来代替定义校验，也不发布用户列表、审批和流程写工具。若运行包返回字段或接口和模板不一致，停止并修订登记体，不在线修改业务代码。配置保留 capabilityMode=off，是因为本试点直接使用当前用户权限和自身资料接口，不代表移除 Operation 授权与上游认证。

### D. 配置 Octop，最后开放试点

用现有 Octop 管理员，向 PUT `/octop/api/auth/oidc/config` 提交 provider 示例，填入真实 client secret，先保持 enabled=false；调用 POST `/octop/api/auth/oidc/config/test`，须 ok=true。该测试只验证发现和公钥可达，不证明客户端密钥、登录或 MCP 成功。

管理配置读取接口会隐藏 extra 和 client secret，不能据此误判白名单丢失；保存受限登记结果，必要时对 provider 做只读核验，只输出白名单和 secret 是否配置，不输出密文或明文。

按产品界面建立 HTTP / Streamable HTTP MCP 连接器，URL 为 `http://10.51.49.8:8081/oa/service/oa/octop-readonly/mcp`，不手填认证头、不共享管理员 token；使用普通试点用户绑定自己的专家。启用前工具测试若提示“需要 OA 登录”属于预期，不能通过填管理员 token 绕过。

当所有配置已核对后，启用 OA 客户端、目录 MCP 和 Octop provider；只在白名单已写入、只读工具已准备完成后让试点用户真实 OIDC 登录。若先登录再补名单，必须重新登录才能保存供 MCP 使用的 OA 凭据。用户首次 OIDC 登录可能创建自身 Octop 账号、身份绑定和默认个人专家，这是预期配置/账户数据变化。

### E. 启用 OA 内嵌助手

OA 后端 `embed-origins`、Octop `plat_embed_origins` 和客户端 autoApproveScopes 必须先配齐。参考 `OidcEmbedService.consume()` 会以 autoApprove=true 完成票据授权，若 autoApproveScopes 不含此次请求 scope，则失败；因此本稿已将该范围精确补为 openid/profile/email，不能继续沿用此前空清单。

OA 现有前端通过构建变量 `VITE_PLAT_ASSISTANT_ENABLED=true`、`VITE_PLAT_ASSISTANT_URL=http://211.137.76.3:8081/octop` 启用。若部署包未含这些值，先按 OA 现有构建流程产出候选静态包，保留 `/oa/` 与 `/oa/service` 等原配置；对照已发布源码版本核验后再发布，不使用其他环境整包覆盖，也不只修改 `_app-config-*.js`。若既有代码缺少助手协议能力，暂停并报告，不擅自开发兼容代码。

启用后的 iframe URL 由现有组件构造为 `/octop/login/plat-embed?parent_origin=http%3A%2F%2F211.137.76.3%3A8081` 等参数形式。父页面在真实 OA 登录下向 `/oa/service/connect/embed-ticket` 申请一次性票据，按严格 source/origin/state 完成消息握手。票据依赖现有 Redis，参考实现有效期 60 秒、消费后删除；不把 OA access_token 直接放进 iframe URL。

前端发布只在候选产物和原静态包备份均已核对后执行，并记录实际目录、资源摘要和切换方式。若无需更换已部署前端，只记录现有开关及入口已生效的证据。当前尚未构建或上传 OA 前端。

## 5. 分阶段验收及留存

| 验收 | 通过标准 |
| --- | --- |
| OA 与本地管理入口 | 原 OA 登录/页面可用；Octop 本地 admin 仍可登录；其他容器和镜像不变 |
| 协议 | discovery / JWKS 为标准响应且 issuer 一致，Octop test ok=true |
| OIDC | 普通用户首次/再次登录成功，真实精确回调可用；错误 redirect/state/nonce/PKCE 被拒绝 |
| 权限 | 有助手权限的普通用户可用；无权限用户被拒绝；不需要用户拥有管理员角色 |
| MCP | tools/list 仅含试点工具；tools/call 以 `{}` 查询自身，userId/username 与实际 OA 用户对应 |
| 隔离 | 两名普通用户独立调用结果分别对应自身；手动伪造其他 userId 参数被拒绝 |
| OA 内嵌 | 有权限用户在 OA 看到并打开助手，无需再输入 Octop 密码，握手后聊天可用；首次/重复打开及刷新可恢复 |
| 内嵌隔离与票据 | 无权限/错误 origin 被拒绝；票据过期或重复消费失败；切换 OA 用户/组织、退出后不沿用原用户上下文，MCP 仍归属实际调用者 |
| 凭据 | 非白名单目标不注入 OA 凭据；过期/撤销后失败并提示重新登录，不回退共享管理员身份 |

本次计划会新增配置/授权记录、用户会话、OIDC 授权码/令牌与审计记录、Hub 发布记录及 Octop provider/连接器/密钥记录；真实登录还可能创建默认专家及其内部存储。只读业务调用不修改 OA 审批、流程或其他业务单据。留存新增记录标识、文件摘要、脱敏结果、时间和回滚命令，不保存明文 token、授权码、口令或私钥到报告。

## 6. 回滚和影响控制

1. 若本次更新了 OA 前端，先恢复原静态产物/原助手开关并确认原 OA 页面正常；再关闭本次 Octop provider、停用 OA `octop` 客户端和本次 MCP 目录，下线该 Operation、停用该服务。只操作本次登记的实际 id/code，保留记录以便审计，不删表或恢复整库。
2. 通过现有受控能力撤销试点 OA 令牌/Octop 会话；关闭 provider 本身不等于撤销既有会话。只撤回本次增加的角色绑定，不能覆盖用户之前的角色权限。
3. 如需回退 OA 配置，对比现场变更后恢复本次改动的键和 import，在窗口内再次重启 OA；若期间他人已修改配置，先合并回退而非用旧整文件覆盖。确认不再引用后，按保管要求处理新增密钥文件，不破坏其他签名密钥。
4. 已实施的网络转发与业务配置分开回滚，普通 OIDC 回滚不必自动撤销网络规则。确需撤销时使用既有精确回滚步骤，见 [发布手册第 8 节](AJ_DEPLOYMENT_RUNBOOK.md#8-aj-oidc-公网地址回访转发)，不清空 NAT 表。
5. 不删除试点用户已有 Octop 专家、聊天、知识库或 Memory 数据，不触碰其他客户端、Hub 服务或账号。

## 7. 本轮准备结果

已在本地核对 API 请求字段、配置键和模板间地址一致性，并检查 YAML / JSON 语法及占位符。没有生成真实密钥、联网调用管理写接口或改动生产文件。本轮仅准备配置和文档，未运行会自动格式化已有业务代码改动的 `make all`；不将静态检查当作实机 OIDC / MCP 验收。

审核后实施范围建议为：上述 OA 外置配置/独立密钥、现有 Redis 票据支持、一个受限 OIDC 客户端、确认后的试点角色绑定、一个只读 Hub 工具及目录、Octop provider/连接器、OA 内嵌入口的现有前端构建配置及必要静态产物发布，和一次 OA 后端重启。OA/BPM 目的 origin 均加入清单；不包含 BPM/工作流工具登记、业务写测试或数据库结构变更。若 preflight 发现结构或前端能力缺失，应先报告精确缺项，不把本方案扩展成业务代码开发。
