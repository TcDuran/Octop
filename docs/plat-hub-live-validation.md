# PLAT OIDC / Hub 本地联调记录

日期：2026-10-15。使用 `http://localhost:8088` 品牌实例，PLAT 前端 5777、后端 8080。

2026-10-16 更新：[嵌入复杂场景与 AJ-PT 临时依赖联调](plat-embed-complex-validation.md) 已补充真实 AJ-PT 宿主、长任务切公司、并发重连、依赖还原及旧入口清理。下文按阶段保留历史记录；最新状态以该补充为准。

## 已实测

- 从 Octop 登录页重新发起 OIDC，经过 PLAT 登录、授权和回调，进入 `/chat`；未复用历史 state。
- 可信地址未配置时只进行普通 OIDC 登录，不保存 PLAT 业务凭证；配置后重新登录，凭证加密保存到现有 secrets 存储，按 Octop 用户和 Provider 区分。
- 本地实际 Hub 入口为 `http://localhost:8080/service/aj-pt/mcp`，已配置为精确可信地址。设计示例 `/service/oa/mcp` 在此部署没有对应服务。
- 使用实际 OIDC Access Token 调用 PLAT `/service/ss/token/verify`，成功还原登录人员及 `currentCompanyId`、`currentOrgId`。
- MCP spec 注入 `X-Plat-Token` 后，原有 `X-App-Id` Header 保持原值；凭证没有写入连接器共享定义。
- 使用 Octop 实际 MCP 探测代码完成 initialize、tools/list，发现只读 `identity_context` 工具；通过 MCP SDK 执行工具成功，返回经 PLAT 校验的用户、公司、组织。
- 无效可信地址（域名通配符）经 OIDC 配置 HTTP 接口返回 400，拒绝修改现有配置。已补充接口回归测试。

工具闭环使用真实 Octop、PLAT OIDC 和 Hub，但 **AJ-PT 上游使用临时只读模拟服务**。模拟服务监听现有 Hub 配置的 `127.0.0.1:8082/mcp`，收到 Hub 转发的 `token` 后调用真实 PLAT 校验接口，再返回身份上下文；不执行业务写入、不打印 Token。验证后关闭模拟服务，不修改 AJ-PT 工程。

## 修复的部署偏差

8080 原运行包缺少源码中已有的 `HubTokenTransport` 和 `RequestTokenResolver`。重新构建 starter-web 并使用 Java 21 重启后，`X-Plat-Token` 正常进入 Hub 转发逻辑。保留本地 issuer 和已有签名密钥配置，discovery/JWKS 均已复核。

Octop 镜像已重新构建并部署，保留原数据目录和品牌资产，同时重建本地 OIDC loopback 转发容器。当前部署关闭 API Docs，因此 `/api/docs`、`/api/openapi.json` 为 404；配置接口已直接验证。

## 自动化检查

- `make all`：4687 passed、25 skipped；格式、Ruff、mypy 通过。
- `mvn -pl starter/starter-web -am package -DskipTests`：BUILD SUCCESS；这项仅证明平台打包成功，不代表执行过 Java 测试。
- 共享连接器按调用用户注入、保留 Headers、缺少凭证拒绝、可信地址边界已有单元测试；这些测试不能代替多用户实机验收。

## 后续真实业务与多用户验收（同日补充）

真实 AJ-PT 已通过其现有运行包启动于 8082，Hub 沿用 `http://127.0.0.1:8082/mcp` 上游配置。以下验证没有使用模拟 MCP 服务，没有修改 AJ-PT 源码，也没有执行申请、审批等业务写操作。

仅将 PLAT OAuth2 客户端 `octop` 的 `accessTokenValiditySeconds` 从 300 调整为 **86400（24 小时）**，其余客户端及该客户端其他字段保持原值。通过管理接口修改；操作审计确认 UPDATE，变更字段包含 `accessTokenValiditySeconds`，前值 300、后值 86400。重新 OIDC 登录后，实际个人凭证剩余时间约 86400 秒。旧 Token 不自动延长；这不是 Octop 登录 JWT 的有效期。

管理员维护一份共享 MCP 定义 `oidcqa_live_ajpt`，包含默认 Header `X-App-Id: oidcqa-live-default`，默认不开启。两名测试人员未建立个人连接器副本；调用时由 ConnectorService 获取同一个共享 server 名并注入各自凭证，再由 MCP SDK 调用真实 Hub 和 AJ-PT。该连接器并不限制为只读，业务权限仍由 AJ-PT 校验，本次只调用了只读工具。

| 实机用例 | 结果 |
| --- | --- |
| admin OIDC → Hub → AJ-PT | 发现 40 个真实 MCP 工具；`my_todos` 成功，返回真实待办（总数 22） |
| 人员 `oidcqa_a1015`，公司 A | 浏览器 OIDC 成功；`my_profile` 返回该账号，组织为安居集团 |
| 人员 `oidcqa_b1015`，公司 B | 独立浏览器 OIDC 成功；同一共享连接器返回该账号，组织为安欣置业成本招采部 |
| 两人凭证与共享定义 | 个人 Token 不同；默认 Header 保留；共享原始定义不含 `X-Plat-Token` |
| 无业务权限人员 | 两名测试人员均无业务角色，`my_profile` 可用；`my_todos` 均返回 Access Denied |
| 无 PLAT 凭证的本地 Octop 用户 | 装配可信 Hub 工具时报 `PLAT_LOGIN_REQUIRED`，未借用管理员或其他人员凭证 |
| 同账号、两个独立浏览器 A/B | 第二个浏览器切公司并重新 OIDC；Octop 用户 ID 仍为同一个。旧 A spec 发起调用返回 A，新装配 spec 返回 B |
| 非成员公司登录 | 显式指定未归属公司被 PLAT 拒绝，未签发该公司登录态 |
| Hub 缺少／伪造凭证 | 缺少凭证返回业务状态 401；伪造 `X-Plat-Token` 返回 HTTP 401 |
| 独立多公司确认页 | 实测发现默认公司直接授权的问题后已修复；选择 B、确认、OIDC 回调后，Token 校验和真实 `my_profile` 均为 B |
| 单公司 OIDC | 无额外确认页，直接完成授权并进入 Octop |
| 多公司选择当前公司 | 明确确认当前 B 公司后成功返回 `http://localhost:8088/chat`，无需切换公司 |

账号 `oidcqa_a1015` 有 A/B 两家公司归属，`oidcqa_b1015` 只有 B 公司归属；均标记为本任务测试账号，未授予业务角色。测试不重置现有人员密码。共享定义默认关闭，不自动加入现有聊天。

多公司确认补丁位于 PLAT 的 `sso-login.vue`：仅当前 `client_id=octop` 的 OIDC 授权流程读取成员公司并要求明确确认；单公司直通，其他 OIDC/OAuth2 客户端维持原流程。选择不同公司时复用 `/ss/auth/switch-company` 取得经服务端成员关系校验的新登录凭证，再提交 OIDC 授权；没有直接信任前端传入的组织 ID。普通 PLAT 账密登录规则未改。

补充检查：PLAT `pnpm check:type`、`pnpm build:ele`、`pnpm build:private-packages` 通过；确认页 4 项行为测试通过，涵盖确认前不授权、切公司失败不授权、单公司直通、其他 OIDC 客户端兼容。全量前端测试在仓库支持的 Node 24 下为 **84 文件、335 测试通过**。工作区默认 Node 26 首次运行有 13 项既有 localStorage 相关失败，切回支持的 Node 24 后全量通过；未改动这些测试。

## 保留的边界

- 首期不自动续期。24 小时减少长任务中途过期的概率，不保证任意时长任务都能跨过 Token 到期或撤销；到期后需要重新 PLAT 登录。
- 前一阶段 A/B 验证仅保留旧装配 spec 后再次调用真实业务；下方扩展验收已补充真实 LLM 聊天运行中切公司测试。
- 这些测试证明当前部署识别人员、公司组织及方法权限，不代表聊天、文件、记忆按组织隔离，也不代表所有 AJ-PT 业务表的数据范围都已逐一验收。
- 嵌入票据与宿主组织自动跟随仍属于后续集成，不在本轮独立登录验收内。

执行中 A/B 重载的既有实测结论仍仅适用于已经装配的当前轮工具：新装配使用新凭证；旧聊天的后续轮次不保证继续绑定旧凭证。

## 扩展账号与真实聊天验收（同日追加）

新增专用无业务角色账号 `oidcqa_c1015`（公司 A）、`oidcqa_d1015`（公司 B），均通过独立浏览器从 `localhost:8088` 实走 OIDC。连同已有 A/B 测试账号，共四名人员，各维护一个测试 Agent，共用同一个管理员维护的连接器定义。没有重置原有业务账号密码，没有执行业务写工具。

聊天测试通过运行中 Octop 的正式 Dashboard WebSocket 接口发起，使用已配置的真实模型、真实工具装配、Hub 和 AJ-PT；以工具返回消息中的人员及组织为判断依据，不以模型口头回答为依据。共完成 **14 个成功聊天轮次、23 次真实 `my_profile` 调用**，另外验证两种预期拒绝路径。直接 MCP 并发矩阵另有三轮、每轮四人，共 12 次调用，不能与聊天轮次混算。

| 扩展场景 | 实测结果 |
| --- | --- |
| 四名人员同时发起独立聊天 | 四个 Agent 分别返回各自账号和所属组织，全部成功 |
| 同一人员、同一运行中 Agent、两个并发聊天 | 两个不同 thread 均返回 C 的 A 组织，正常结束，没有串流 |
| 同账号 A 任务运行中，另一独立浏览器重新授权 B | A 当前轮串行调用 `my_profile` 10 次，全部仍为 A；B 浏览器成功回到 Octop，个人最新凭证校验为 B |
| 上述旧聊天的下一轮 | 使用原 thread、原 session 再发消息，实际重新调用工具返回 B |
| 上述重新登录后的新聊天 | 新 thread 的工具调用返回 B |
| 多人复用同一个共享运行中 Agent | C 临时共享其测试 Agent，D 调用 PLAT 工具被配置冲突错误拒绝；未取得 C 身份。C 自己随后仍可正常调用；测试后恢复为私人 Agent |
| 越权读取私人聊天 | D 读取 C 私人 Agent 的聊天历史返回 HTTP 403 |
| Octop 禁用测试人员 | C 被临时禁用后，原有效 JWT 访问 `/auth/me` 返回 403，凭证装配返回 `PLAT_LOGIN_REQUIRED`；恢复启用后访问及真实聊天调用均成功 |
| PLAT 管理端撤销 D 的 OAuth2 访问令牌 | 仅撤销 D 当前测试令牌；PLAT 校验拒绝，已有 Agent 的真实聊天调用失败，没有返回身份或借用其他人的凭证 |
| 撤销后重新 OIDC 登录 | D 浏览器重新授权、回调后，真实聊天再次返回 D 的 B 组织 |
| Octop 页面退出、重新登录 | C 点击退出返回 `/login`，再次点击 PLAT 登录成功返回 `/chat` |
| Octop 退出对已签发凭证的影响 | `/auth/logout` 返回 204，但原有效 JWT 访问 `/auth/me` 仍为 200，个人 PLAT 凭证仍保留；退出不等同于服务端令牌撤销 |

撤销测试当前给聊天返回通用“模型调用失败 / TaskGroup”错误，尚不是明确的“请重新 PLAT 登录”提示；手动重新 OIDC 后能恢复。这是可用性缺口，不应将其描述为自动重新授权已实现。共享 Agent 的拒绝提示也是通用配置冲突；共享连接器与共享运行中 Agent 的边界不同。

初次以 Ask 模式探测时，当前运行时只提供其允许的只读工具集合，未暴露该 MCP 工具。正式身份聊天验收使用 Craft 模式，并明确只调用 `my_profile`，未把“模型没有工具”当作身份认证测试通过。

本轮补充回归命令：

```bash
uv run pytest tests/unit/auth/test_sso_service.py tests/unit/auth/test_plat_mcp_auth.py tests/unit/auth/test_plat_credentials.py tests/integration/test_auth_oidc.py -q
```

结果 **48 passed**。本轮没有修改生产源码，仅扩展实机测试和本报告；此前完整 `make all` 结果见上文。测试账号与其私人 Agent 保留供后续复核，临时共享和临时禁用状态均已恢复。

## 普通 OIDC 兼容范围

- `SsoService.handle_callback` 只有在 OIDC Provider 配置了非空 `extra.plat_mcp_urls` 时，才调用 `complete_plat_login` 保存业务凭证及重载连接器；未配置则仍调用标准 `complete_login`，不要求公司组织信息。
- 普通 OIDC 回调回归覆盖只含 `id_token` 的模拟 Token 响应，登录成功；不要求对方提供 PLAT 业务凭证字段。本轮没有接入另一台真实外部 OIDC 服务商，兼容结论来自代码核对与上述回归。
- 当前数据库 `idx_sso_providers_kind` 唯一索引及管理接口均为每种认证类型一份配置，因此可以替换接入另一标准 OIDC，但不能同时保留 PLAT 并新增第二个 OIDC 登录入口。
- 换接普通 OIDC 时须清除 PLAT 专用 `extra.plat_mcp_urls`；仅修改 issuer/client 而保留旧配置会继续触发专用凭证处理。当前不扩展多 Provider 功能。

嵌入开发尚未开始。上述结果补齐了独立登录、共享连接器、多聊天和撤销恢复证据；仍不构成聊天记忆按公司隔离或超过 24 小时任务续期的保证。


## 平台统一嵌入验收（2026-10-15 补充）

已在同站 `localhost:5777` 宿主、品牌 `localhost:8088` Octop、8080 PLAT Provider/Hub 和 8082 真实 AJ-PT 上完成以下实测。票据机制新增，复用现有 OAuth2/OIDC 授权码、PKCE、客户端和 Redis，不新增数据表。AJ-PT 工程未升级平台包，也未删除其旧浮窗；当前实测宿主为 moudule-plat 统一壳。

| 用例 | 实测结果 |
| --- | --- |
| 宿主 A 公司打开助手 | 自动 OIDC 成功，宿主和 iframe 均显示 `oidcqa_a1015`，无需重复登录或公司选择 |
| A 公司真实工具 | 经共享定义、个人凭证、Hub 调用 AJ-PT `my_profile`，组织 `2090330301443215360` |
| 宿主切换公司 B | 原公司切换流程刷新整页，助手保持打开并重新授权；`my_profile` 返回 `2110000000000000006` |
| B 公司真实聊天 | 11.8 秒完成，工具实际返回 `oidcqa_a1015` 和 B 组织，正常 done |
| 独立与嵌入身份不同 | 同一浏览器独立 Octop 页签保持 `admin`；重连嵌入仍为宿主联调账号；重连和更换宿主账号后再次通过 `/api/auth/me` 确认独立页签仍为 `admin` |
| 收起／打开／关闭／重新打开 | 收起保留 iframe；关闭销毁，再次打开重新授权，账号保持宿主身份 |
| 同浏览器退出 A、登录 D | 宿主和重新挂载的助手均变为 `oidcqa_d1015`，没有沿用 A |
| 另一个独立浏览器 D | PLAT 宿主打开助手自动登录 D，未重复选择公司 |
| 匿名申请票据 | 平台 envelope `statusCode=401`，无票据 |
| 非白名单来源 | 平台失败 envelope，无票据；该平台通用异常映射仍为 `statusCode=500` |
| 票据单用 | 首次授权 HTTP 302，原票据重放 HTTP 400；签发响应 `Cache-Control: no-store` |
| 修改 state／PKCE | HTTP 400，不退回普通浏览器旧登录态 |
| 票据超过 60 秒 | 等待 61 秒后 HTTP 400 |
| 撤销 D 当前业务 Token | 真实下一轮请求失败，并显示要求重新通过 PLAT 登录的本地化提示；不再只显示 MCP 加载失败 |
| 已挂载工具后撤销 D | 真实工具调用阶段返回中文重新登录提示和 `error_code=PLAT_LOGIN_REQUIRED`，未输出 TaskGroup 通用错误 |
| D 重新授权恢复 | 嵌入重新授权后，真实聊天 13.8 秒完成，`my_profile` 返回 D 和 B 组织，正常 done |

失效提示实测暴露了 harness 在发现工具时吞掉传输异常的行为。最小修复是在现有工具加载调用外增加 PLAT ContextVar guard，收集本次加载 HTTP 401 后在返回工具前还原认证错误；并发任务上下文独立、下一次加载自动清理，不修改 harness。运行工具的嵌套 ExceptionGroup 同样保留重新登录提示，HTTP 403 仍保留业务无权限语义。

部署配置与完整时序见 PLAT `docs/design/assistant-hub-delegated-auth.md` 第 16 节，提供方与 module-demo 消费技能已同步。本次首期限定同身份域、同站 iframe；完全跨站 Cookie 策略和独立账号库身份映射未验证。嵌入 JWT 仅使用页签 sessionStorage；PLAT 业务凭证仍是同一 Octop 用户最新一份，聊天、文件、记忆不按组织隔离。

构建和检查：

- PLAT Maven package：BUILD SUCCESS；OIDC Provider/Embed 专项 9 tests passed。
- PLAT `pnpm check:type`：17 tasks 通过；前端全量 85 files / 337 tests passed（maxWorkers=4）。
- PLAT `pnpm build:private-packages` 成功，`pnpm build:ele` 14 tasks 成功。
- Octop `npx tsc -b` 通过；嵌入和 storage 专项 8 tests passed；i18n 80 passed。
- Octop 首轮 `make all`：4693 passed、25 skipped，格式、Ruff、mypy 通过。追加工具发现错误适配后的最终 `make all` 同样全部通过：**4694 passed、25 skipped**，Ruff、mypy、格式检查通过（402.18 秒）。
- 当前本地部署关闭 `/api/docs`，无法检查 Scalar 展示；实际 API 请求和流程已实测。

代码未提交、未推送。
