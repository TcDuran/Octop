# 嵌入复杂场景与 AJ-PT 临时依赖联调

日期：2026-10-15 至 2026-10-16。此记录补充 `plat-hub-live-validation.md`；其中早期“AJ-PT 尚未接入、旧入口未清理”的描述是历史状态。

## 环境与测试范围

使用品牌 Octop `http://localhost:8088`、PLAT 前端 5777 / 后端 8080，以及 AJ-PT 前端 5779 / 后端 8082。Hub 转发到真实 AJ-PT `/mcp`。账号沿用专用 A/C/D 测试人员：A 可选两家公司，C 仅 A 公司，D 仅 B 公司。只调用 `my_profile` 读取身份，未执行申请、审批或其他业务写工具。

浏览器通过 agent-browser 操作实际宿主登录、公司切换、助手重连和退出。聊天通过正式 Dashboard WebSocket 接口，使用真实模型、工具装配和 Hub。断言以 MCP 工具返回的账号、组织为准；不是仅检查模型回答。未将 Token、密码或签名密钥写入本文。

## 复杂场景结果

| 场景 | 结果 |
| --- | --- |
| 票据请求延迟 1800ms，0/500/1000ms 连续重连 | 三次请求均返回，仅最后一轮进入 ready；只有一个助手 iframe，没有错误 |
| 票据请求中关闭助手 | 响应到达后仍不重新挂载 iframe；再次打开可正常登录 |
| 伪造消息来源 | 错误 window source 和错误 origin 的授权消息均未额外触发票据申请 |
| 阻断真实票据请求 | 助手显示失败，未挂载已登录 iframe；解除阻断并重连后恢复 C/A |
| C/D 两人并发直接调用真实 MCP | 三轮、共六次，均返回各自账号和 A/B 组织 |
| A 当前轮运行时，另一浏览器宿主切到 B 并完成嵌入授权 | A 原轮 12 次串行 `my_profile` 全部为 A 组织，24.6 秒正常结束；新个人凭证变为 B |
| 切到 B 后新开聊天 | 返回 B 组织，正常结束 |
| 上述 A 旧聊天的下一轮 | 复用原 session/thread，实际工具返回 B 组织，正常结束 |
| AJ-PT 原生登录 A 后打开统一助手 | 宿主和助手均为 A；真实聊天返回 A 组织 |
| AJ-PT 宿主 A→B→A | 公司切换刷新后助手重新授权；B 真实聊天返回 B，切回 A 后真实 MCP 返回 A |
| AJ-PT 退出 A，再登录 D | 宿主与 iframe 均显示 D；真实聊天返回 D/B，正常结束 |
| 独立 Octop 页签 | 完成上述嵌入测试后，独立页签 `/api/auth/me` 仍为 HTTP 200 / admin |

同账号的个人业务凭证仍是最新一份。A/B 两个浏览器不具备业务凭证隔离；即使某宿主还显示 A，同账号另一浏览器授权 B 后，新装配工具也可能使用 B。当前轮工具保留 A 不等于聊天永久绑定 A。聊天、记忆、文件均不因此获得组织隔离。

## 临时依赖方式与还原

为避免污染正常 Maven 缓存，先将既有本地仓库克隆到独立临时目录，再用该目录执行离线安装和 AJ-PT 打包：

```bash
# moudule-plat/backend，Java 21
mvn -o -Dmaven.repo.local=<临时仓库> -pl plat/plat-starter -am install -DskipTests

# aj-pt/backend，Java 21
mvn -o -Dmaven.repo.local=<临时仓库> -pl starter/starter-web -am test
mvn -o -Dmaven.repo.local=<临时仓库> -pl starter/starter-web -am package -DskipTests
```

平台 install 的 22 个模块成功。AJ-PT 后端测试汇总 438 项，失败 0、错误 0、跳过 3。初次 package 复用了已有 fat jar，嵌入接口返回 404；移除该生成 jar 后重新打包，核对嵌套 `module-ss-biz`、`framework-web` 的 SHA-256 与临时仓库完全一致，才进行正式嵌入验收。

前端将本地 `@plat/api`、`@plat/core`、`@plat/web` 打成 tgz，再临时链接到解包后的制品及原缓存传递依赖。未直接使用平台工作树源码别名，未修改依赖版本、package.json、锁文件或 Maven settings。

验收后已还原：

- AJ-PT 原有三个 `@plat/*` 链接；原始运行 jar 及 8082 原启动参数。
- Octop 嵌入来源白名单中临时增加的 5779，恢复为测试前来源配置。
- 关闭测试专用 5779 服务。还原 8082 后，再经 Hub 调用真实 AJ-PT，返回 D/B。
- POM、application.yaml、application-local.yaml、package.json、锁文件的 SHA-256 全部与测试前一致。没有覆盖原有业务源码变更。

原有依赖不含本次新嵌入能力；还原后不能把 AJ-PT 当前运行包当作新能力已上线。正式启用需发布并升级平台制品，配置助手 URL、Provider 及可信来源。

## 永久清理

按用户要求，AJ-PT `frontend/src/main.ts` 去掉旧 `mountPlatAssistant` 调用，并删除旧 `src/plat-assistant/` 下的 Vue 浮窗、启动器、布局函数及其测试，共四个文件。这些是有意保留的源码变更，不随临时依赖还原。新版入口由平台统一壳提供，业务工程不重复挂载。

## 构建、基线与未通过项

| 检查 | 结果 |
| --- | --- |
| 临时平台制品 `pnpm build:dev` | 成功 |
| 临时平台制品 `pnpm build:combined:dev` | 构建命令成功；不等于实际 combined 入口已验证 |
| AJ-PT 前端 Vitest（临时制品） | 321 passed / 12 failed；5 个失败文件 |
| 还原依赖后 Vitest，保留旧入口删除 | 307 passed / 12 failed；失败测试名称与临时制品下完全一致；减少的 14 个成功项属于已删除旧布局测试 |
| 临时制品 `pnpm typecheck` | 失败，166 个 TS 错误，包含 Router 4/5 类型冲突、样式及第三方类型声明、平台源码别名等 |
| 还原依赖后 `pnpm typecheck` | 失败，169 个 TS 错误，退出码 2 |
| 还原依赖后 `pnpm build:dev` | 成功，40.24 秒；保留旧入口清理 |

12 个既有失败来自 meeting-ui、oa-scope-contract、platform-auth-contract、sup-presentation、supervision-create。未为本次清理修改无关业务测试。类型检查两组均未通过；按路径归一后对比，新版临时制品额外出现 extend 声明缺失一项、platform-sidebar 的 path 隐式 any 两项；原依赖多出六项 file-viewer 包解析错误，其他主要问题两组均存在（含 Router 4/5、样式声明、别名与业务类型）。一项 AttachmentField 联合类型错误仅枚举顺序不同。不能把类型检查报告为通过，也不能把所有类型错误归为本次升级引入。

核对时曾在进程完成前读取空日志，错误报告“还原后类型检查通过”；等待进程最终退出后已更正为上述结果，并重新对比两组错误。正式升级前仍需处理消费工程类型检查和实际 combined 入口问题。

运行 `dev:combined` 后，HTTP HTML 中实际 module-federation 代理入口仍为 `/src/main.ts`。因此本次仅确认构建脚本退出成功，不声称完成 combined 入口实机验收；正式合并入口需进一步核对 HTML 入口替换与 federation 插件顺序。

本轮 Octop / PLAT 生产源码没有新增修改，未重复运行全量 `make all`；前一轮已完成 4694 passed / 25 skipped，详见主记录。本轮新增内容为实测记录及 AJ-PT 旧入口清理。原始本机日志保留在 `/tmp/octop-hub-verify/deep-embed/`，其中包括独立 Maven 安装、后端测试、两种构建、类型检查基线，以及匿名化的身份聊天结果；临时目录不提交。

未提交、未推送。
