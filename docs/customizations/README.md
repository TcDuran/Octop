# 定制改造维护索引

本目录统一记录本仓库相对官方 Octop 的改造、设计及升级检查点。代码继续遵循仓库原有模块边界，不要求搬到同一个运行时代码目录。

核对基线：个人 `develop` 提交 `a75b69fb`，本轮同步官方 `v1.0.3b1`（`890111b7`）；维护状态更新至 2026-10-26。官方权限 Tab 拆分、权限分组折叠及管理端建用户时选择默认专家均采用官方实现；PLAT 内嵌登录、极简默认布局、OIDC 首次登录助手和自动化分析保留。本轮同步不创建生产版本标签，历史实机记录保留原验收日期。

## 1. 改造目录

| 改造 | 状态 | 主要入口 | 验证与升级关注点 |
| --- | --- | --- | --- |
| PLAT OIDC 业务凭证、Hub 请求授权 | 已在当前基线集成 | `src/octop/infra/auth/plat_credentials.py`、`src/octop/infra/connectors/plat_auth.py`、SSO service/provider、connector service、agent manager | 身份与凭证归属、可信 URL、失效提示、共享 Agent 边界；记录见下方链接 |
| PLAT iframe 登录、页签 Token 隔离 | 已在当前基线集成 | `dashboard/src/pages/Login/PlatEmbed.tsx`、`OidcComplete.tsx`、`api/request.ts`、`App.tsx`、后端 auth 路由 | 独立网页与嵌入模式登录、来源校验、切公司、同标签页导航 |
| 极简布局、PLAT 嵌入与 OIDC 默认助手 | 已部署原 8088，完成首次/重复 OIDC、PLAT 真实内嵌与聊天验收 | [实现与验收记录](plat-minimal-oidc-assistant.md)；布局 Context、Sidebar、SSO service/server、general-assistant 模板 | 默认 minimal、保留已存偏好；嵌入标记与 iframe 双重判断；无个人专家时从原 general-assistant 模板幂等创建 PLAT·通用助手，仅改名，不新增表；make all 5152 passed，前端全量既有失败与时序波动见验收记录 |
| 跨会话推送 | 已在当前基线集成 | `src/octop/infra/gateway/tools.py`、agent manager、工具目录及 i18n | `list_sessions` / `send_to_session` 的权限、历史留存和目标 session 语义；新成果工具应复用可复用部分，不改变旧工具契约 |
| MCP 参数嵌套类型修复 | 已适配官方 octop-harness 1.0.5 | `docker/patches/patch_harness_mcp.py`、Dockerfile、MCP 探测与缓存相关代码 | 保留官方 enum、array、可选参数处理，仅补齐 LangChain 重建时丢失的嵌套 object properties；升级需检查补丁锚点及 tool_call_schema |
| 白标品牌 | 已在当前基线集成 | `brand/`、`brand/patch-octop-brand.sh`、Dockerfile | 官方静态资源路径变化、构建产物、镜像构建顺序 |
| 官方构建期部署子路径 | 已切换为官方 `v1.0.3b1` 实现 | `dashboard/vite.config.ts`、`dashboard/src/utils/publicUrl.ts`、`VITE_BASE_PATH` | 每个前端构建通过 `VITE_BASE_PATH=/octop/` 固定部署前缀；Nginx 转发到 Octop 时去掉该前缀；不再使用 `OCTOP_BASE_PATH` 或运行时 ASGI 前缀中间件 |
| AJ-PT REST → Hub MCP 配置迁移 | 33 项能力 / 35 个 operation 已全量真机通过 | [迁移与验收记录](ajpt-rest-mcp-migration.md)；Hub 服务/目录配置、AJ-PT 官方 SQL | 42 项发布、原 12 工具名保持；当前调用人权限、数据库版本、真实审批与幂等，非 AJ-PT 源码补丁 |
| Hub Operation 认证 Header 映射 | 已本地安装开发制品并完成 Octop 实机验证 | [使用与验收记录](hub-operation-auth-headers.md)；moudule-plat Hub 后端、AJ-PT local 配置 | 仅 Operation 显式映射可覆盖默认上游凭证；本地 common/Hub/plat-starter 开发版一致，未上传 Nexus；旧 42 工具保持，JWT 刷新与幂等限制见记录 |
| 审批工作流 REST → Hub MCP 挂载 | 19 项已发布，8 类审批动作与公文四级回调已实测；已办查询有上游故障 | [配置与实机验收记录](hub-workflow-rest-mcp.md)；现有 Hub 服务/目录配置 | 当前61工具，原42名称保持；显式 JWT Header、双层幂等、测试资源回收及 completed 空指针限制见记录；本轮无应用源码改动 |
| REST → MCP 五账号自然语言审批 | 三类闭环通过：合同 6 级、用印 5 级、用车退回补正及 2 级审批，业务均 approved | [2026-10-23 实测记录](approval-rest-chat-e2e-20261023.md)；独立测试目录及五账号真实 Chat | 仅系统模拟审批；六项临时 Operation、27 工具；保留失败样本，已办故障未修，无应用源码改动 |
| 本地 OIDC 联调容器 | 已在当前基线集成 | `docker/docker-compose.oidc-local.yml` | 属于本地联调设施；避免误当生产配置 |
| 日志保留排序回归 | 已在合并历史中处理 | 合并提交 `fce4f125`、`tests/unit/test_logging.py` | 以最新源码及上游行为为准，不恢复已不需要的旧补丁 |
| 自动化扫描与独立分析会话 | 已合并当前基线并部署 8088，已完成多轮实测；保留已知边界 | [方案文档](automation-analysis-dispatch.md) | 一次性 Cron 绑定事项会话；一张关联表、两个工具；总览下次扫描刷新；共享 Cron 执行入口、MCP 包装和数据库迁移需回归，分析指令长度问题见方案末节 |
| 原始附件访问链接 | 方案已确认，尚未实现和验收 | [提示词及附件方案](approval-automation-prompts.md) | AJ-PT MCP 返回可授权访问的原件预览、下载地址，Octop 复用 Markdown 链接；不新增 Octop 文件接收改造 |

自动化文档第 6 节对应 S0–S8 实施与验证，第 8 节记录首轮隔离验收，第 9 节补充真实字段变更、撤回闭环、嵌入登录、故障恢复，以及加急来源能力限制；第 10 节记录一小时连续验收、Token 与清理，第 11 节记录原 8088 部署和正常 PLAT 登录后的派发验收，第 12 节验证只描述工具用途的自然语言提示词及其呈现限制，第 13 节记录新闻/指标/会议三种定义、账号隔离，以及观察时间和材料版本的可靠性缺口。

本索引同时用于本机维护；历史业务联调报告及私有验收材料默认保留在本机。本轮极简 / OIDC 说明和本索引随代码纳入版本管理，业务数据库、备份、凭证和截图不进入 Git。

已有 PLAT 验证记录保留原位置：

- [PLAT OIDC / Hub 联调记录](../plat-hub-live-validation.md)
- [PLAT 嵌入复杂场景验证](../plat-embed-complex-validation.md)
- [AJ-PT 三类审批账号与 MCP 核验](ajpt-approval-accounts-validation.md)：按阶段记录最初四账号及实例指定审批人，随后补齐默认规则、第二审批人和业务 MCP；早期失败状态不代表最新结果。
- [自然语言审批全流程实测](approval-natural-language-e2e.md)：五账号在 Octop 的申请、预分析、首审、二审退回及材料变化校验。
- [业务化提示词与总览实测](approval-automation-prompts.md)：最新总览格式、三张云杉园区样本及连续扫描验证。

上述业务单据和任务状态均为文档所述时间的快照。2026-10-23 的五账号 REST → MCP Chat 验收已完成三条有效申请闭环，专用浏览器会话及本地临时登录凭证已清理；五条早期失败样本保留，已办查询故障未修，详见该轮报告。自动化派发的观察时间、版本依据及指令长度限制仍以原文档为准，不因后续审批验证通过而视为已修复。运行数据不随 Git 提交。

表中路径是维护入口，不是完整文件清单。升级时以 `git diff --name-only upstream/main...HEAD` 及各提交的实际差异补充核对。

## 2. 代码是否统一放在一起

建议统一管理入口、差异记录和验收场景；运行时代码按职责归属。

- PLAT 认证与 Token 行为留在 `infra/auth/`、`infra/connectors/` 及相应 HTTP / 登录页面。
- 自动化分析派发集中于 `infra/cron/analysis/`，SQL repo 留在 `infra/db/repos/`。
- 会话关联、消息写入复用 gateway/history 服务，通过小范围接入适配，不从 domain 导入 HTTP router。
- 通用聊天链接属于 dashboard Markdown 渲染，不能放入 PLAT 登录模块。
- 品牌与容器补丁沿用既有目录。
- 部署子路径遵循官方构建期方案：前端通过 `VITE_BASE_PATH` 生成资源路径，反向代理转发时去掉该前缀；业务 API 仍保持 `/api/...` 定义。

- 极简嵌入只在现有布局和侧栏作小范围条件渲染；OIDC 默认助手初始化独立放在 infra/auth/sso/default_agent.py，复用官方模板构建和 AgentManager.create。

本期不创建包罗所有业务的 `custom/` 或 `plat/` 核心目录，不搬迁已验证的认证代码，不引入新的插件框架。集中目录不能消除真实依赖，反而可能制造跨层导入和上游合并时的大量路径冲突。

## 3. 后续合并官方更新

1. 记录官方基线和本地改造提交；遵守仓库的分支与发布流程。
2. 先检查官方是否已提供等价能力，再决定保留、适配或移除对应补丁。
3. 重点审阅共用接入文件：agent manager、Cron delivery、历史写入入口、Markdown、认证请求及错误处理；部署子路径按官方 `VITE_BASE_PATH`、`publicUrl()`、PWA 和 Nginx 前缀规则回归。
4. 官方迁移编号优先：本轮保留官方 022–024，将本地自动化分析迁移改为 `025_octop_automation_analysis.sql` / `.pg.sql`，本地描述统一加 `octop_` 标记；已有本地 022 数据库通过幂等补齐官方 022 列并继续升级到 025。后续仍需核对官方新编号，标记本身不能避免数字冲突；不可只改文件名而忽略已有数据库的应用水位。
5. 执行各模块专项测试，再执行仓库交付检查；嵌入与独立网页均验证会话导航。
6. 更新本索引和各方案的验证记录，明确哪些是源码核对、哪些是实际运行结果。

新增改造按功能拆成可审阅提交，不为了整理目录夹带历史代码重构。


## 4. 2026-10-26 同步验证

本轮在隔离工作树合并官方 `v1.0.3b1`，保留主工作区原有 7 项 Docker 未提交改动，不将其夹带到同步提交。

- `make all`：5252 passed、35 skipped；Ruff 与 mypy 通过。
- `npm run build`：TypeScript 检查、Vite 生产构建和 PWA 生成通过；另以 `VITE_BASE_PATH=/octop/` 构建通过。
- 前端专项：登录与 Token 隔离、官方权限选择器、publicUrl、极简布局及侧栏，共 34 项通过。本机 Node 26 的 Vitest 使用 `NODE_OPTIONS=--no-experimental-webstorage`，避免 Node 自带 localStorage 遮蔽 jsdom。
- 使用旧提交的迁移脚本重建本地 022 数据库，升级到 025，确认用户数据保留、官方用户组与共享列补齐、重复迁移幂等；数据库专项 21 项通过。
- 实际 octop-harness 1.0.5 的 `StructuredTool.tool_call_schema` 保留嵌套 `req.orgId`、enum 与可选 array；校验后数据仍可序列化为 JSON。
- 隔离实例（18088）：浏览器完成滑块验证、密码登录、极简界面与用户管理验收；通过新建用户页面创建 `sync_member` 并自动生成 `PLAT·通用助手`。官方分组折叠和按 Tab 的权限项可见；`/api/docs` 返回可读 Scalar 文档，OpenAPI 包含用户组接口。
- 真实 Nginx（18089）：转发时去掉 `/octop/` 前缀，浏览器从 `/octop/login` 登录后进入 `/octop/chat`，健康检查正常。

以上使用独立测试数据库与临时账号，不修改现有 8088 容器数据，不代表本轮重新执行过 AJ-PT 审批全流程或 PLAT OIDC 真实登录；这些联调保留之前的验收记录。
