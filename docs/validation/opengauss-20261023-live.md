# openGauss 真实 LLM 与 IM 验证

日期：2026-10-23。工作区：独立 worktree octop-opengauss，分支 feature/opengauss-support。

后续新增的多用户、文件工具、cron、真实 RAG 及灾备续聊结果见 [扩展真实业务场景](opengauss-20261023-scenarios.md)。扩展测试另发现尚未定位的运行期连接中断 RUNTIME-01；本报告第 10 轮通过属于该记忆/续聊用例的历史结果，不能据此认定后续整批场景也全部通过。

## 数据来源与隔离

- 本地 Docker PostgreSQL 容器为 imprv-pgvectorscale-18，现有 Octop 使用其中的 octop 库。
- 对源库使用 default_transaction_read_only=on 与 8 秒语句超时，只读取 providers、agents 的模型引用以及 channels 配置元数据；不复制现有用户、聊天、工作区或定时任务。
- provider 凭证仅在本地 Git 忽略目录中以 0600 权限暂存，日志同样使用私有权限；文档与测试源码不含凭证。
- 真实业务回归使用单独新建的临时 openGauss 库，沿用 isolated_database 夹具记录库名和 OID，结束前核验连接关闭并仅删除本轮库。
- 持久开发库 octop（OID 88543）、远端 plat 库以及原 octop 容器（8088）均不用于本轮测试写入。不启用任何现有 IM 渠道。

## 已完成的模型探测

| 配置 | 模型 | 路径 | 结果 |
| --- | --- | --- | --- |
| 部门ai | deepseek-flash | Octop provider chat probe，真实网络请求 | 通过，1735 ms |
| 集团ai | bge-m3 | Octop provider embedding probe，真实网络请求 | 通过，192 ms |

此表只证明 provider 连接及响应可用，不代表向量库、检索增强或完整 Agent 已通过。

## 完整业务回归

新增 tests/integration/test_opengauss_live.py，必须显式配置 live 凭证文件并启用 openGauss 后执行，默认测试不会调用真实模型。

覆盖目标：真实 HarnessAgent 经 HTTP/WebSocket 收发、memory_save 工具调用、合成会话捕获、真实辅助 LLM 提取/提升/页面生成、服务停启后的历史读取与上下文续聊。模型只处理本轮合成信息，文件 backend 限定为临时 home。

复现命令（先通过本地私有方式准备 provider rows JSON，不把凭证放入命令参数）：

    (
      set -a
      source .env.opengauss
      set +a
      export OCTOP_TEST_OPENGAUSS=1
      export OCTOP_LIVE_PROVIDER_FILE=/absolute/path/to/providers.private.json
      export OCTOP_LIVE_PROVIDER_NAME=部门ai
      export OCTOP_LIVE_MODEL=deepseek-flash
      export OCTOP_LIVE_RESULT_FILE=/absolute/path/to/private/results.jsonl
      uv run --frozen pytest tests/integration/test_opengauss_live.py -q -m live --tb=short
    )

测试夹具修正：前两轮误用了不支持的 backend type=local，现使用 filesystem；max_iters 从 6 调整到 80，以容纳真实 Harness 的中间件节点；WebSocket 工具事件按 tool_call_chunk 检查，落库查询使用后端归一化后的 namespace。这些是验收夹具问题，不能据此认定 openGauss 或 provider 不兼容。

### 真实运行新增发现

真实 Harness 启动了后台记忆维护定时器，触发依赖 vacuum.py 中两处 to_regclass 调用；这条路径此前没有被定向维护用例覆盖，openGauss 因缺少函数报错。

- 在 vendor/octop-memory/rebuild.py 的可重建补丁源中，将两处调用改为参数化的 pg_catalog.pg_class / pg_namespace 存在性查询；上游匹配数量必须为 2，否则重建失败。
- 不向远端 plat 或全局 schema 补函数，不编辑 site-packages。锁定依赖更新为 1.0.2+opengauss.2，并同步 pyproject.toml、uv.lock 和归档。
- 归档 SHA-256：bc8123739cdbd2dda9fa319d891adb0d609178ee2a9347bd9661502a4e9b2a8c。
- test_memory_maintenance_and_agent_cleanup 增加真实 nudge_vacuum 调用，并检查 checkpoint 可读及另一 namespace 数据保留；定向回归已通过。

### 已完成的业务步骤与清理保护

第 08 轮使用真实 Harness、真实 deepseek-flash 完成聊天、memory_save 工具调用和数据库落库检查。辅助模型从 2 条合成 raw events 提取 2 个 candidate，提升为 2 条 atom，生成 3 个页面（0 失败）；重启服务后历史和同一会话代号续聊通过，记忆统计为 3 atoms、3 entities、0 dirty pages。

第 08、09 轮整体 pytest 报告失败：业务步骤结束后，清理保护检查发现 1 个数据库连接未关闭，因此拒绝删除数据库，没有强杀会话。测试进程退出后分别重新核对同一库名与 OID，连接数为 0，再精确删除；审计保存在本地私有 live-08-cleanup.json、live-09-cleanup.json。不将业务步骤通过写成整轮通过。

第 09 轮诊断显示 Harness Memory 后端正常关闭，但记忆统计 HTTP 接口另行打开的 dashboard cache 仍持有一个 business pool lease。原 _MemoryCache 在 fingerprint 替换、LRU 淘汰、显式 invalidate 时仅丢弃引用，没有确定性关闭 Memory/checkpointer/pool。

已修复 src/octop/api/common/memory_client.py 的上述释放路径；缓存淘汰日志只记录 agent_id，避免输出包含 DSN 的缓存元组。新增单元回归覆盖缓存命中不关闭、替换、淘汰、单条/全部失效和重复失效；1 passed。真实验收在停止服务后显式失效自己打开的统计缓存，要求该临时库的 memory pool lease 为零，再交还建库夹具删除。没有为删除数据库放宽归属或连接保护。

### 最终真实业务结果

第 10 轮完整 live 测试退出码 0：1 passed、6 warnings，100.87 秒。真实聊天、memory_save 调用与 atom 持久化、真实辅助模型提取/提升/页面生成、重启后的历史/上下文/记忆统计均通过。该轮提取 2 个 candidate、提升 2 条 atom、生成 3 个页面（0 失败），episode 提取 1 条；停止服务并释放自己的 API 缓存后，目标库的 memory pool lease 列表为空，夹具自动删除数据库成功。

模型曾在第 07 轮只回复而未调用工具；验收最多允许一次明确提醒，仍同时要求真实 tool_call_chunk 和 atom 落库，不能仅靠文本承诺通过。最终第 10 轮第一次请求即实际调用工具。

最终只读审计逐个核对本日登记的 11 个临时数据库，均已不存在；持久开发库 octop 的 OID 仍为 88543。没有按前缀批量删库，也没有强制终止会话。数据库归属记录汇总到本地 cleanup-audit-final.json，已删除 10 轮临时测试 home、其中的配置/合成会话文件及 provider 凭证快照，保留脱敏结果和原有 .env.opengauss。

### 质量门与独立容器

- 缓存释放修复后的最终 make all PYTEST_JOBS=4：退出码 0，5118 passed、52 skipped、17 warnings，427.67 秒；格式、Ruff、mypy 和默认非 live 套件均通过。警告为依赖弃用等提示。使用独立 basetemp，避免扫描其他轮次的 pytest 临时目录。
- 此前 vacuum 修复后、缓存修复前的一轮质量门同样退出码 0：5116 passed、53 skipped、95 warnings，614.05 秒；较多警告来自 pytest 全局旧临时目录清理。该数字作为中途证据保留，不代表最终源码状态。
- 独立镜像 octop-opengauss:dev 重建成功，只重建 Compose 服务 octop-opengauss；保持 127.0.0.1:18088。
- 容器实际安装 octop-memory 1.0.2+opengauss.2，运行中 vacuum 模块含两处 catalog 查询且不再调用 to_regclass；gs_dump 客户端为 openGauss 6.0.5。
- 缓存修复后再次构建并更新同一独立服务，核验容器中的 _MemoryCache 包含 4 处资源关闭调用；最终容器再次通过 HTTP 和 Docker 健康检查。
- /api/health 返回 HTTP 200、ok=true、db=true；Docker health 为 healthy。原 octop 容器 ID 前后相同，8088 实例未被重建。
- 新增 live/缓存回归与 vendor 重建脚本的 Ruff 检查及格式检查通过，git diff --check 通过。凭证清理前对源文件的 provider key 检查无命中；最终对 63 个变更/新增路径的数据库密码检查无命中。未提交或推送代码。

## IM 验证

只读检查本地 PG 的 octop、octop_analysis_20261020、octop_recovery_20261022_144444，channels 均为空；本地 plat 没有 channels/providers 表。尚未找到可复用渠道配置，已请求用户提供配置位置或配合手动验收。

准备独立测试机器人/渠道后，按以下顺序验收：

1. 核对渠道类型和测试身份，确认不会与现有实例共用同一个长连接消费者；保留 18088 端口隔离。
2. 先做凭证探测；注意现有 probe 实现会短暂启动渠道，不能仅凭接口名称认定无消费副作用。
3. 用户向测试机器人发送唯一合成代号，验证入站落库、真实 LLM 回复和出站送达。
4. 重启测试服务后在同一会话追问代号，核对上下文、历史和渠道状态。
5. 记录实际收发结果与 openGauss 持久化证据；停用并清理专用测试渠道及合成数据。没有真实收发证据时保持“待人工验证”。

当前没有启动 IM 连接，也没有向任何真实联系人发送测试消息。
