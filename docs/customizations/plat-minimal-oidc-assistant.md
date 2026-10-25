# 极简布局、PLAT 嵌入与 OIDC 默认助手

日期：2026-10-23；源码基线 main / d8207b94。

## 范围

复用现有极简布局、PLAT 嵌入登录及 general-assistant 专家模板。首版只调整默认值、指定入口的可见性和 OIDC 登录后的默认助手初始化，不增加菜单配置中心、数据库表或新模板副本。

## 实现

1. 未保存布局偏好时默认 minimal；已有用户保存的 classic/minimal 不覆盖。
2. PLAT 专用 sessionStorage 标记 plat_embed_session=1，且 window.parent !== window，才进入嵌入界面。普通 OIDC 登录不等于嵌入登录。
3. 嵌入强制 minimal + records，不保存覆盖用户的独立页面布局/任务面板偏好。侧栏保留对话、自动化和助手/会话列表，隐藏专家管理、Token 统计、设置切换、团队/云端分类及账号区域。
4. 正常 OIDC 登录成功、发放一次性登录码之前，确保用户有自己的专家。已有个人专家直接复用；没有则按现有 general-assistant 模板创建并启动，使用现有模型解析及当前用户的资源/连接器权限。
5. 模板 ID、SOUL.md、欢迎语、快捷问题及能力保持原样，只把中文显示名改为 PLAT·通用助手；英文显示名为 PLAT · General Assistant。没有批量改名已有用户创建的实例。
6. 登录初始化经 server 的小型回调接入，领域函数位于 infra/auth/sso/default_agent.py；调用 build_create_spec_from_expert + AgentManager.create，不从 infra 导入 HTTP router。检查与创建在同一进程锁内，重复及并发登录不重复创建。绑定已有账号的流程不触发创建。

首版的“首次”以没有个人专家为准，不新增已初始化标记；如果用户删光个人专家，下次 OIDC 登录会补建。已停用但仍存在的个人专家不会被自动启用或替换，已有用户的管理行为予以保留。

## 最小维护入口

| 内容 | 文件 |
| --- | --- |
| 默认值 | dashboard/src/layouts/layoutModeStorage.ts |
| 嵌入判断 | dashboard/src/utils/platEmbed.ts |
| 布局和偏好隔离 | dashboard/src/context/LayoutModeContext.tsx |
| 侧栏入口与账号区域 | dashboard/src/layouts/Sidebar.tsx |
| 分类切换 | dashboard/src/pages/Chat/components/MinimalAgentSessionNav.tsx |
| 默认专家初始化 | src/octop/infra/auth/sso/default_agent.py |
| 登录和运行时接入 | src/octop/infra/auth/sso/service.py、src/octop/infra/server.py |
| 显示名称 | src/octop/infra/agents/experts/library/general-assistant/manifest.json |

隐藏导航仅影响界面展示，现有 API 权限逻辑不变。无需 PLAT 增加参数；既有 PLAT 嵌入入口继续使用。

## 验证

2026-10-23 已完成源码检查、真实 OIDC 登录及原 8088 部署验收。

### 自动检查

| 命令 / 范围 | 结果 |
| --- | --- |
| make all PYTEST_JOBS=4 | 通过；Ruff、格式检查、mypy 608 个源文件、5152 passed / 34 skipped。存在既有依赖及临时目录清理警告 |
| npm run build（包含 tsc -b） | 通过；构建后已执行现有白标脚本 |
| 布局、侧栏、PLAT 嵌入与 OIDC 完成页 5 个前端测试文件 | 19 passed |
| 默认助手及 SSO service 后端专项 | 29 passed |
| 本轮修改的前端文件 ESLint | 0 errors；MinimalAgentSessionNav 既有 catalog 依赖提示 1 warning |
| 前端全量 Vitest | 未全绿：264 文件通过 / 7 文件失败；1408 测试通过 / 7 失败 |
| 前端全量 ESLint | 未全绿：3 errors / 70 warnings，错误均位于本轮未修改的文件 |

全量前端失败文件中，DocumentPreviewCore.docxSanitize、skillMarkdown、SkillDrawer、knowledgeBases、publishedExperts、ChatInput.prefill 的 6 个文件在 HEAD d8207b94 的隔离基线副本同样失败。AuthGuard 在全量时出现路由替换时序失败，当前和基线单独重跑均 3 passed；不将其表述为已修复。未夹带修改这些无关测试或业务代码。Node 26 本机验证使用 NODE_OPTIONS=--no-experimental-webstorage，避免 Node 原生 webstorage 干扰 jsdom。

新增后端回归覆盖原模板复用、已有助手保留、并发/重复登录、跨用户归属、模板缺失及登录初始化失败不发登录码。仓库 /tests/ 和 /docs/ 忽略规则保持原样；2026-10-25 经用户授权提交时，显式纳入新增 test_oidc_default_agent.py、本报告和定制维护 README。

### 2026-10-25 提交前复验

- 对隔离无关 Docker / 依赖改动后的实际提交内容执行 make all PYTEST_JOBS=4，最终 5151 passed / 34 skipped；Ruff、格式检查、mypy 608 个源文件全部通过。
- 首轮全量中 test_backup_deletion_excludes_maintenance_and_restore 出现一次 5 秒等待超时；该内存备份模块独立复跑 3 passed。使用独立临时目录再次执行完整 make all 后全绿；未修改该测试或放宽超时阈值。
- 本轮后端登录与默认助手专项 29 passed；前端布局、侧栏、嵌入与 OIDC 专项 5 个文件共 19 passed。前端专项使用 NODE_OPTIONS=--no-experimental-webstorage。
- 本轮涉及前端文件 ESLint 为 0 errors / 1 既有 warning；已知前端全量基线问题仍按前文记录。
- 提交钩子的 testmon 在既有 Docker 健康检查测试中发生内部异常；已通过最小复现确认由内联 Python 使用 Dockerfile 作为编译文件名引起。仅将该测试的动态编译文件名改为虚拟名称，保留原 12 个用例和执行逻辑，并在 testmon 模式下复验。
- 8088 健康检查通过。此前已完成的真实登录与对话验收继续有效；本次仅整理提交、补充文档，未重新部署运行服务。

### 实机场景

| 场景 | 真实结果 |
| --- | --- |
| 首次 OIDC、尚无个人专家 | 使用真实 PLAT 测试账号 octop_yj，在隔离 Octop 控制库通过真实 5777 / 8080 登录，自动创建 NS9M5W，归属该用户，template_name=general-assistant，名称为 PLAT·通用助手 |
| 首次创建后立即对话 | 浏览器发送首条消息，真实模型回复“PLAT助手首次对话验证通过。”，无手工创建助手步骤 |
| 退出并再次 OIDC 登录 | 同一用户仍只有 NS9M5W 一个个人助手；没有重复创建，历史会话可见 |
| 原 8088、存量用户 | 保留 octop_yj 的 NAF8Q4 / RY5RB3 两个原有助手及名称，未额外创建默认助手 |
| 原 5777 PLAT 内嵌 8088 | 使用正式嵌入票据登录；仅显示对话、自动化与助手/会话列表；专家管理、Token 统计、团队/云端分类和底部账号区域均不显示 |
| 内嵌真实聊天 | 在存量 REST 助手中新建测试会话，真实回复“PLAT内嵌聊天验证通过。”；不操作业务单据 |
| 独立 8088 | 专家管理、Token 统计、团队/云端分类及账号入口正常显示；未保存布局时采用 minimal |
| 偏好隔离 | 独立页测试保存 classic / settings 后，重新进入真实 PLAT iframe 仍呈现 minimal / records；回到独立页原 classic / settings 未被覆盖。完成后恢复测试浏览器原有未设置状态 |

另有一次在存量自然审批长会话中发送的纯界面测试消息等待过长，已停止该测试回合；最终聊天通过证据来自新建的 REST 助手会话，不把长会话等待描述为成功。原审批单据没有在本轮新增、修改或审批。

### 部署、回退与证据

- 原 8088 已运行 octop:plat-minimal-20261023；octop:latest 同步指向本轮已验证镜像。基于原运行镜像仅覆盖本轮 Python / 模板文件及重新构建的 dashboard，未重建或夹带工作区原有 Dockerfile / 依赖补丁改动。
- 运行数据仍使用原 PostgreSQL 控制库和 data 挂载，环境变量及挂载已与部署前逐项核对；本轮没有数据库结构迁移。
- 重新创建 Octop 时已重建 octop-plat-oidc-loopback，继续转发容器内 localhost:8080。后续重新创建 Octop 也需按 docker/docker-compose.oidc-local.yml 的说明重建该容器。
- 原镜像保留为 octop:plat-minimal-base-20261023；部署前 PostgreSQL 完整逻辑备份为本机证据目录下 octop-before.dump（私有备份，不纳入 Git）。回退镜像后仍需重建 loopback。
- 隔离验收使用临时 18088；临时 OAuth 允许回调以比较并更新方式恢复原值，未覆盖其他并发改动。QA 容器、两个专用浏览器、临时浏览器凭证和测试运行数据已清理；18088 已释放，恢复原允许回调后再次通过 8088 内嵌登录。
- 验收截图、脱敏快照、检查日志及文件哈希保存于：/Users/guixiaolong/.octop/backups/plat-minimal-20261023-231634。包含首次登录对话、内嵌完整侧栏、内嵌聊天通过、独立默认极简及独立经典布局截图。
- 已核对部署容器内 4 个运行时文件及 dashboard/index.html 的 SHA256 与本地一致；原 7 个无关脏文件哈希未变化。
- 2026-10-23 完成实现与实机验收；2026-10-25 用户授权提交并合并到 GitHub，提交检查与最终合并状态以本次 PR 和 Git 记录为准。集成路径为 feature/plat-minimal-oidc-assistant → develop，不修改版本号或创建生产标签。既有 Docker / 依赖补丁修改继续单独保留。

## 同步官方

- 默认布局只核对默认值及已有偏好是否仍保留。
- 嵌入模式复用既有登录标记；保留 iframe 判断，避免新窗口带着 sessionStorage 时误隐藏。
- Sidebar 只保留少量条件分支，不复制官方 Sidebar，不用脆弱的 CSS 文本或 nth-child 选择器隐藏。
- 默认助手继续从官方 general-assistant 模板构建，不复制 SOUL、工作区初始化或运行时创建逻辑。
- 合并后重新验证独立/嵌入显示差异、首次/重复登录、真实首条对话及跨用户归属。
- 保留工作区原有的 Docker/依赖探测未提交修改，本轮不夹带提交，不推送。
