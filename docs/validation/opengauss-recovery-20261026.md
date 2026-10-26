# openGauss 恢复后的隔离复验

日期：2026-10-26；本地时间使用 Asia/Shanghai。验证进行中，本报告会在清理和审计完成后更新最终结果。

## 环境与保护范围

- 在既有 octop-opengauss worktree、feature/opengauss-support 分支执行，未提交或推送。
- 08:28 的只读探测确认远端恢复：openGauss 6.0.5 build 5ba692ab；原生 gs_dump/gs_restore 也是相同版本和 build。
- 重新读取的既有 plat / octop OID 分别为 41901 / 88543。管理连接只读取目录、创建随机独立验证库，以及核对名称/OID/零会话后精确删除本轮库。
- 不对既有业务库执行 DDL/DML，不安装实例级扩展文件、不重启服务、不终止其他连接。不启动本地临时 openGauss 服务端或复用现有应用的数据目录。
- 本地 Docker PG 的 providers/settings/channels 只读，用于复用第三方模型配置和前后摘要审计。测试仅使用合成内容，没有连接 IM 或启用本地模型。
- 私有证据目录：data/opengauss/recovery-20261026/f449c595；连接口令和模型密钥不写入本文。

## 本轮结果

| 范围 | 当前结果 | 验证边界 |
| --- | --- | --- |
| 远端只读身份、数据库目录、类型与扩展目录 | 通过 | datavec 默认版本 0.7.2 可见，管理库未安装；目录可见不等于安装文件完整 |
| 第三方 bge-m3 embedding | 通过 | 4 条合成文本真实请求，每条返回 1024 维；不使用本地模型 |
| 控制面、Memory、checkpoint、原生备份、系统归档及 HTTP | 14 passed，597.22 秒 | 包含重复初始化、模拟升级失败回滚、空库/非空库恢复、函数/索引恢复和两种聊天归档策略；HTTP 用例使用 fake Harness |
| PG/openGauss baseline 结构对照 | 校正测试查询后 1 passed，28.68 秒 | 列与可空性、约束、索引、默认值、序列边界和种子一致；首次失败单独保留 |
| 共用批量写入与记忆资源生命周期 | 2 passed，92.20 秒 | executemany 错误整批回滚，真实 Memory 缓存释放、checkpoint 和 vacuum 路径 |
| 适配层与记忆缓存单元回归 | 12 passed，4.90 秒 | 99 个 warning 主要来自 pytest 自动清理旧系统临时目录；未手动清理这些其他批次目录 |
| 本轮 make all PYTEST_JOBS=4 | 5120 passed、89 skipped、17 warnings，退出码 0 | 格式、Ruff、mypy 和默认非 live 套件；pytest 421.34 秒、命令总计 435.17 秒，数据库 opt-in 已从子进程环境移除 |
| 真实 LLM 记忆与业务场景 | 5 passed，473.49 秒 | 记忆保存/提取/重启续聊，双用户并发与权限隔离，附件与文件工具/归档/重启，定时任务真实执行/重启，系统备份恢复后的真实续聊；不启用 IM |
| PG/OG 远程 embedding 知识库对照 | 2 passed，299.28 秒 | 两个独立库均完成真实引用、修订、重启、权限隔离和删除；两侧 embedding_dim=1024 |
| 有界并发契约 | 3 passed，102.74 秒 | secret、automation、一次性 SSO 登录/邀请码；未进行压力循环 |
| 原有断连问题 RUNTIME-01 | 待受控复测 | 一次成功不能证明偶发问题已修复 |
| datavec 扩展目录安装 | 受限 | `CREATE EXTENSION datavec` 因缺少 0.7.2 SQL 文件失败；该项只表示扩展目录不完整 |
| DataVec 内核向量完整验收 | 未完成 | 历史记录仅覆盖三维建表、插入和距离查询；更新/删除/回滚曾中断，索引创建、1024 维、重连及向量恢复仍未完成。本轮没有新增向量写入证据 |

## 执行方式

先执行单进程顺序测试，覆盖初始化、增量迁移失败回滚、运行 SQL、Memory/checkpoint、原生数据库与系统归档恢复；再执行 PG baseline 结构对照、共用生命周期路径、真实模型业务场景和有界并发。所有批次使用唯一 basetemp 和 JUnit，不覆盖既有证据。

原验证计划将 `CREATE EXTENSION datavec` 作为前置条件，因此在缺少 SQL 文件后停止。后续分析确认目标 build 已内置 DataVec，修正方案见 [部署手册](../opengauss-datavec-installation.md)：扩展目录安装与内核向量验收分开。后续先在独立临时实例逐条验证 DML、索引、重连及原生备份恢复，再考虑共享实例复验；首次意外错误立即停止，不自动重试写入。

知识库目前使用 SQLite sidecar 向量索引。知识库真实远程 embedding 场景通过，也不能替代 openGauss 向量表或 Agent Memory 产品向量接线的验收。既有 SDK 问题及产品接线边界见 [PG Memory/向量补测](postgres-memory-vector-20261024.md)。

## 本轮修正与环境观察

结构对照首次连接本地 PG 18 时失败：PG 的 pg_constraint 中单独包含 contype=n 的 NOT NULL 约束，openGauss 目录不包含同类条目，因此直接比较所有目录行会报告多出 267 项。第一段列定义比较已经核对 information_schema.columns.is_nullable，故本轮仅在约束比较查询中排除 n 类型；主键、唯一、外键、检查约束等仍完整比较，没有删除可空性验证，也没有修改产品 SQL、迁移或业务逻辑。失败保存在 schema.log/schema.xml，修正后结果在 schema-2.log/schema-2.xml，不能以成功覆盖首次失败。

08:33 的只读测量显示数据库时钟约比本机快 4107.56 秒，即 68 分 27.56 秒；往返约 0.054 秒。关联服务器日志应按各自时间基准处理。没有修改服务器时间，也没有据此认定它是断连原因。

## 最终清理与质量检查

本轮最终清理已完成：登记的 32 个临时库均已按名称/OID 核对、确认零会话后删除；08:58 复查 `octop_verify_*`、`octop_parity_*` 数据库和会话均为空。plat、持久 octop、PG 源库的目录、扩展、关系和 providers/settings/channels 摘要均与开始时一致。私有 provider 副本、pytest 临时目录和归档测试副本已删除；117 个文本文件凭证扫描无命中。

向量前置验证在 08:57 首条 `CREATE EXTENSION datavec` 处阻塞，SQLSTATE 58P01，缺失 `/usr/local/opengauss/share/postgresql/extension/datavec--0.7.2.sql`。因此本轮没有执行向量 DML、HNSW/IVFFlat 建索引或真实向量表回归。后续分析修正：这说明扩展目录安装路径失败，不能证明内核 DataVec 不可用，也不能证明向量完整可用；历史三维插入/距离查询证据仍有效，历史断连和未执行项也保留。原失败证据和清理记录保留在私有 `vector-summary.json`、`cleanup-audit.json`，没有修改远端安装文件。

测试入口均使用 uv run --frozen pytest 和 -n 0，并指定独立 basetemp/JUnit。控制面第一批排除并发和 PG 基线对照，随后分别执行修正后的结构对照；共用模块选择 opengauss and (executemany or cache)。真实 LLM 执行 test_opengauss_live.py 与 test_opengauss_scenarios_live.py，知识库场景另由 test_database_shared_live.py::test_same_remote_embedding_rag_both_databases 同时覆盖 PG/openGauss。并发组单独选择 secret、automation、一次性登录/邀请码三项，每组最多四个工作线程，不进行压力循环。

全部凭证从本地私有配置读取；实际 provider 副本仅用于本轮测试，清理后不保留可用密钥。完整质量门使用独立 quality-tmp，避免 pytest 自动扫描历史系统临时目录；该目录只在质量门子进程结束后清理。
