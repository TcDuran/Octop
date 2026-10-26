# openGauss 扩展真实业务场景验收

日期：2026-10-23。仅在 octop-opengauss worktree 执行；不提交或推送。

后续补充的本地 PG 共用路径回归与同模型双库知识库验证见 [PG/openGauss 对照验证](opengauss-20261023-pg-parity.md)。后续结果不覆盖本文保留的失败轮次和 RUNTIME-01。

## 两个已修复问题

1. 真实 Harness 的后台记忆维护调用依赖中的 to_regclass，openGauss 不支持。可重建 vendor 补丁将两处调用改为参数化系统目录查询，锁定 octop-memory 1.0.2+opengauss.2，不向既有数据库补全局函数。
2. Dashboard 记忆缓存替换、LRU 淘汰或失效时没有关闭 Memory/checkpointer/pool，造成连接残留。现按原资源关闭入口释放，缓存命中仍复用连接，并有单元及真实测试覆盖。

## 本轮方案与证据要求

先核实既有接口 → 添加 opt-in 真实场景回归 → 在随机独立库执行 → 核对落库、重启、连接释放和精确删库。模型调用、工具执行和落库分别断言，不能以模型口头回复代替工具成功。默认质量门不调用真实模型。

只读复用本地 PostgreSQL 的 provider 配置；不复制真实用户、会话或文件。凭证以 0600 私有文件短暂保存，结束删除。远端 plat 和持久 octop 库不参与测试写入；不启用现有 IM 渠道，不发送外部消息。现有 8088/18088 容器不用于本轮合成业务写入。

## 场景矩阵

| 编号 | 真实使用场景 | 必须验证的结果 | 验收状态 |
| --- | --- | --- | --- |
| S01 | 两名用户各自创建助手 | 列表和资源访问隔离，越权无写入 | 第 01 轮通过；第 04 轮初始化断连，未进入场景 |
| S02 | 两个会话同时问不同项目代号 | 真实 LLM 并发回复正确，历史不串线 | 第 01 轮 3 个并发请求通过；第 04 轮初始化中断 |
| S03 | 同名中文附件交给助手核算 | 上传与下载字节相同，真实 read_file 读取并给出正确合计 | 第 02/04 轮通过，合计 19.75 |
| S04 | 助手生成交付文件 | 真实 write_file 调用，下载内容与要求一致 | 第 02/04 轮通过，含工作区 ZIP 导出/删除/导入 |
| S05 | 会话命名和删除 | 变更持久化，删除后不可读取，另一会话仍可用 | 第 01 轮通过，含中文/emoji 标题和置顶；第 04 轮初始化中断 |
| S06 | 服务重启继续处理附件和会话 | 历史、工作区文件保持，真实模型可继续回答 | 第 01 轮断连失败；第 02/04 轮通过 |
| S07 | 创建定时日报并手动运行 | 真实 Harness 执行、last_status 和历史落库，不依赖 IM | 第 01/04 轮通过，task_type=agent、last_status=ok |
| S08 | 无效定时配置与暂停 | 拒绝无效 trigger，不损坏已有配置；暂停状态重启保留 | 第 01/04 轮通过，时区为 Asia/Shanghai |
| S09 | 知识库上传合成制度文档 | 真实 bge-m3 索引完成，文档状态和维度落库 | 第 01/04 轮通过，1024 维 |
| S10 | 助手查询制度并回答 | 真实 search_knowledge、正确答案及原文引用证据 | 第 01/04 轮通过，上限 683 元，历史含 doc_id 引用 |
| S11 | 修订知识文档后重新提问 | 索引更新，返回新值；删除后检索不再返回旧文档 | 第 01/04 轮通过，683→927，删除后检索为空 |
| S12 | 知识库授权和重启 | 非所有者不能修改私有库，重启后元数据和检索可用 | 第 01/04 轮通过，非所有者检索为空 |
| S13 | IM 入站、出站和重启续聊 | 独立机器人实际收发，确认没有与原消费者冲突 | 待独立 IM 配置/人工配合 |
| S14 | 备份恢复后继续真实聊天 | 同一临时库完整归档恢复后，上下文和文件可用，备份后的新增会话消失 | 第 02 轮初始化断连；第 03/04 轮通过 |
| S15 | 长时间及多进程并发 | 队列、连接、锁和内存稳定性 | 未覆盖 |

知识库沿用 Octop 当前实现：元数据保存到 openGauss，向量索引为每库本地 SQLite sidecar。本轮验收不代表向量全部存入 openGauss，也不引入新业务能力。

## 复现入口

仅向本用例提供管理连接环境变量，不要将远端地址填入其他 PostgreSQL 测试的 OCTOP_TEST_DATABASE_URL。需准备私有 provider JSON、匹配 openGauss 6.0.5 的原生客户端及一个全新 basetemp；若使用本地 Docker 客户端包装器，临时文件必须位于其已挂载的 data/opengauss 下。

    (
      set -a
      source .env.opengauss
      set +a
      export PATH="$PWD/data/opengauss/bin:$PATH"
      export OCTOP_TEST_OPENGAUSS=1
      export OCTOP_LIVE_PROVIDER_FILE=/absolute/private/providers.json
      export OCTOP_LIVE_PROVIDER_NAME=部门ai
      export OCTOP_LIVE_MODEL=deepseek-flash
      export OCTOP_LIVE_EMBED_PROVIDER_NAME=集团ai
      export OCTOP_LIVE_EMBED_MODEL=bge-m3
      export OCTOP_LIVE_RESULT_FILE=/absolute/private/results.jsonl
      uv run --frozen pytest tests/integration/test_opengauss_scenarios_live.py -q -m live --tb=short --basetemp=/absolute/new/private/basetemp
    )

管理连接只用于建库、目录核验和删除记录中同一 OID 的临时库。用例内部将应用连接切到新库；不提供目标已有库名参数，不按前缀搜索后批量删除。

## 实际结果

新增 tests/integration/test_opengauss_scenarios_live.py，分为用户/并发会话、文件/归档、定时任务、知识库、整机灾备五组。除真实记忆验收沿用前一份报告外，新建的场景 Agent 关闭可选记忆提取，避免将记忆抽取的不确定性混入文件/cron/RAG 断言；仍使用真实 Harness 和真实模型，不使用 FakeHarnessAgent。

第 01 轮：3 passed、1 failed、1 error、13 warnings，245.72 秒。附件重启后的下载接口在查询 Agent 的读事务 COMMIT 时被远端断开；不是文件内容断言失败。夹具看到残留连接后拒绝删库。进程退出后核对同名库 OID 128939，连接数为 0，再精确删除。

第 02 轮：附件组完整通过，灾备组在 provider 初始化 SELECT 阶段发生同类断连，尚未进入备份/恢复。总计 1 passed、1 failed、1 error、3 deselected、6 warnings，93.62 秒。清理同样保护性拒绝；进程退出后核对 OID 132375、零连接，再精确删除。两次均无强杀会话、无前缀批量删库。

两次连接中断的根因未定位，不能称作已修复，也未添加查询/写入自动重试或放宽清理条件来掩盖。只读诊断显示 enable_thread_pool=off，session_timeout、idle_in_transaction_session_timeout、statement_timeout 均为 0，max_connections=200；这些信息不足以认定根因。

第 03 轮仅执行灾备组：1 passed、4 deselected、4 warnings，115.55 秒。真实模型先记录代号和交付文件，停止服务后调用匹配版本 gs_dump 创建含聊天的系统归档；再次启动后修改文件并新增会话，再停服恢复归档。最终真实 HTTP/WS 证明原文件、原历史与上下文恢复，备份后的新增会话消失，真实模型能够续聊。临时库自动清理，pool leases 为空。

第 04 轮完整执行五组：4 passed、1 failed、1 error、13 warnings，331.64 秒，退出码 1。用户/并发组在 admin_create_provider 的读回 SELECT 阶段断连，尚未执行该组业务步骤；附件、cron、知识库和灾备组完整通过。不能将五组分别有过通过结果合并描述成整批通过。该轮失败库清理再次保护性拒绝；进程退出后核对同一名称/OID 记录、确认连接为 0，再精确删除。名称与 OID 保存在本地数据库清理审计的原始记录中。

关联服务端日志时，可使用本地 JUnit 的批次开始时间（Asia/Shanghai）：第 01 轮 09:48:57，第 02 轮 09:54:32，第 04 轮 10:00:35；具体异常栈保留在对应私有 pytest 日志/JUnit 中。三次中断尚未定位根因，作为 RUNTIME-01 保持未关闭。

定向单元回归：uv run --frozen pytest tests/unit/api/test_memory_client_cache.py tests/unit/db/test_opengauss.py -q，10 passed，0.52 秒。

本轮完整质量门 make all PYTEST_JOBS=4 通过：5118 passed、52 skipped、17 warnings，546.38 秒，包含格式、Ruff、mypy 与默认非 live 测试。它不调用真实模型，不能用这个绿色结果代替扩展 live 批次的稳定性验收。

尚未覆盖的组合包括：含知识库 sidecar 的整机灾备恢复后 RAG、多进程长期并发、真实 IM 收发。已有场景通过不代表正式发布验收全部完成。

定时日报本轮验证的是显式 run-now 后的真实 Agent 执行、暂停持久化和配置校验；真实时钟到期调度、IM 定时投递仍需单独验收。

## 最终清理与交付

- 本轮 12 个随机临时库均逐项核对不存在；持久开发库 octop 的 OID 仍为 88543。失败轮次保留原保护性错误，进程退出后核对各自记录的名称、OID 和零连接再删除，无强杀会话或按前缀批量删除。
- 四轮测试 home、原生备份归档、独立单元和质量门临时目录及 provider 凭证快照均已删除；保留原 .env.opengauss（0600）、脱敏结果、日志和清理审计。
- 65 个变更/新增源码路径的数据库/provider 凭证扫描为零命中，git diff --check、Ruff 与格式检查通过。原 octop 与独立 octop-opengauss 容器均保持 running/healthy，本轮未重建容器或改端口。
- 本地私有证据目录为 data/opengauss/scenarios-20261023，含 results-01..04.jsonl、junit-01..04.xml、pytest-01..04.log、make-all.log、database-ledger.json、cleanup-audit-final.json、private-artifact-cleanup.json 和 source-security-final.json。该目录 Git 忽略，源码/文档未提交或推送。
