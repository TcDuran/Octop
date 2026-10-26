# Octop openGauss 兼容问题与适配方案

更新日期：2026-10-23。状态：首次部署适配已在隔离 worktree 实施，独立容器、真实 LLM 提取/重启续聊和临时库自动清理已有通过证据。外部 IM 和发布分发仍有待办。

2026-10-26 [产品向量接线复验](validation/opengauss-product-vector-20261026.md)：三个真实模型场景通过，但两侧知识库的 1024 维向量实际都存入 SQLite sidecar；openGauss Harness Memory 的 `vector_index` 和 `embedding_provider` 均未配置。现有业务回归通过与 openGauss 向量产品接入未完成同时成立，不能把前者当作后者的验收。

本文汇总源码分析、隔离临时库实测和 worktree 中的适配实现，区分已实现、已实测及仍待验收事项。未向现有 plat 库执行对象 DDL/DML；验证库按轮登记和清理，持久开发库 octop 保留。向量专项新增库曾因远端拒绝连接而清理受阻，恢复连接后已按登记 OID 精确删除。详细结果见 [隔离库实测记录](validation/opengauss-20261022/README.md)。本文不保存连接密码。

2026-10-23 的真实 provider 探测、后台 vacuum 兼容修复及业务验证结果见 [真实 LLM 与 IM 验证](validation/opengauss-20261023-live.md)。历史测试数字保留其原始日期和覆盖范围，不能代替最新质量门结果。

同日新增的多用户、并发会话、附件工具、定时日报、真实 embedding/RAG 和灾备续聊验收见 [扩展真实业务场景](validation/opengauss-20261023-scenarios.md)。该记录保留中途连接中断和保护性清理失败，不能仅凭后续单项通过忽略稳定性问题。

共用改动的本地 PostgreSQL 回归及同一远程 embedding 服务的双库验证见 [PG/openGauss 对照验证](validation/opengauss-20261023-pg-parity.md)。适配目标为 openGauss，但缓存资源释放、PG 连接代理、vendor vacuum 替换和依赖锁定均涉及共用路径，不能描述为只影响 openGauss。知识库沿用原有 remote 能力，未修改模型实现或禁用本地模型。

2026-10-26 远端恢复后的 14 项控制面、备份/归档和 HTTP 回归、2 项双库缓存/vacuum/batch、5 项真实 LLM 业务、2 项双库真实远程 RAG 及 3 项有界并发均通过。PG 的 secret 并发唯一冲突与 SSO 冲突后失效事务在原始 HEAD 仓库模块中同样复现，登记 PG-CONC-01/02，未扩展修复范围；本轮三项 openGauss 并发契约通过。长期并发和 RUNTIME-01 仍需服务端日志与压力窗口定位。

新增运行期观察 RUNTIME-01（未关闭）：多次在 provider 查询或 Agent 读取事务提交时出现 consuming input failed / server closed the connection unexpectedly。单项复测通过，整批重跑又出现中断，尚未证明根因是 SQL 兼容、客户端代码还是服务端/网络行为。需结合服务端同时间日志与连接生命周期继续定位；不添加盲目写入重试或降低删库保护。

2026-10-26[恢复复验记录](validation/opengauss-recovery-20261026.md)重新检查向量前置条件：`CREATE EXTENSION datavec` 仍因缺少 `/usr/local/opengauss/share/postgresql/extension/datavec--0.7.2.sql` 以 SQLSTATE 58P01 失败；这只阻塞扩展目录安装路径。openGauss 6.0.5 从 6.0.3 起将 DataVec 作为内核特性提供，独立库已有 `vector` 建表、DML 和基础距离计算通过证据；HNSW/IVFFlat、1024 维、重连和原生恢复仍按独立验收项记录。第三方 bge-m3 1024 维 embedding 本身和知识库 sidecar PG/OG 真实 RAG 已通过，但不等同于 Octop 已接入 openGauss 向量后端。

同日[本地 PostgreSQL 向量对照](validation/postgres-vector-20261023.md)：PG 18.1 / pgvector 0.8.1 的独立库数据库专项 19 项脚本检查通过，覆盖合成 1024 维 HNSW/IVFFlat、SQL 过滤、事务、重连及原生备份恢复；3 个临时库全部清理，源配置和扩展未改。真实远程 bge-m3 两次请求返回 HTTP 502，不能声称本轮真实语义检索或 Agent 记忆端到端通过；未新增产品向量后端适配。

## 1. 目标与范围

- 目标是首次部署到 openGauss，控制面、Agent 记忆和检查点均按使用 openGauss 评估。
- 缺失函数可以作为兼容层补齐，但仍需适配运行期 SQL、初始化入口、存储依赖及备份恢复。
- 首次部署采用当前完整结构的 baseline，无须移植历史 001–022 的全部升级过程。当前控制面最新迁移为 022；实施时重新确认基线版本，后续从该基线维护 openGauss 增量升级。
- baseline 必须包含完整表、索引、约束、必要种子数据和对应版本记录；不能只手工写入 `_schema_version`。
- 不包含已有 SQLite/PG 数据迁入 openGauss，也不修改当前核对环境的既有对象。

三层存储分别拥有初始化流程和版本管理，不能把控制面初始化成功等同于全部存储兼容：

| 层次 | 当前所有者 | 适配范围 |
| --- | --- | --- |
| 控制面 | Octop `infra/db` | 完整 baseline、启动 ensure、仓储 SQL、后续迁移 |
| Agent 记忆 | `octop-memory` | 自身 DDL、写入 SQL、全文检索、维护及后端工厂 |
| 检查点 | `langgraph-checkpoint-postgres`，由记忆模块构造 | 自身迁移、检查点写入及恢复 |

架构背景见 [ADR 002](adr/002-database-backends.md)。原工作区的 2026-10-21 发布前分析中核对的是另一套 PostgreSQL 18.3 环境，不能将其兼容结论套用到本文的 openGauss 环境；该历史本地分析未复制到本 worktree。

## 2. 已有核对证据与限制

### 环境与依赖快照

| 项目 | 核对结果 |
| --- | --- |
| 目标数据库内核 | `version()` 返回 openGauss 6.0.5 |
| 协议版本报告 | `server_version=9.2.4`；不代表原生 PostgreSQL 9.2 |
| 兼容模式 | `sql_compatibility=PG` |
| 存储引擎设置 | `enable_default_ustore_table=off`，`enable_ustore=on` |
| 本地依赖 | `octop-memory==1.0.2`、`langgraph-checkpoint-postgres==3.1.0`、`psycopg==3.3.4` |

前序探测连接设置了 `default_transaction_read_only=on` 和 `statement_timeout=5000`，并确认事务只读。验证边界如下：

| 状态 | 内容 | 证据边界 |
| --- | --- | --- |
| SELECT 已通过 | 现有 `PostgresPool` 连接、参数绑定、行映射、只读事务/SAVEPOINT、自动预编译、BYTEA、pipeline 只读查询 | 可以优先复用 psycopg；未证明写入与并发兼容 |
| SELECT 已通过 | JSONB 基础操作、`row_to_json`、`array_to_json`、全文检索基础函数、日期分桶、64 位锁键候选表达式 | 未证明完整备份、生成列或索引 DDL 可用 |
| 仅 EXPLAIN 已通过 | INSERT/UPDATE/DELETE RETURNING、`FOR UPDATE SKIP LOCKED`、原生 UPSERT 的更新/忽略/条件更新候选语句 | 未使用 ANALYZE；未实际写入，不能推断 rowcount、锁竞争和触发器行为 |
| 已确认不兼容 | 当前 `ON CONFLICT` 语句解析报 42601；原生 UPSERT 更新唯一索引列报错 | 需要 SQL 和调用路径适配 |
| 已确认缺失 | 当前相关路径使用的 `to_regclass`、`to_jsonb`、`hashtextextended`、`jsonb_path_query_array` 调用报 42883 | 需要兼容函数或调整调用 |
| 隔离库已实测 | CREATE TABLE/FUNCTION/INDEX、真实 DML、并发消费和持锁、原生 gs_dump/gs_restore | 基础能力和应用级初始化/恢复均有通过用例；兼容函数只实现已用到的有限语义 |
| 已实施 | 控制面 baseline、运行期 SQL、记忆/checkpoint openGauss 后端、原生备份/归档接入 | 位于独立 worktree，尚未提交、发布或与 fork 上游同步 |
| 回归已有通过证据 | 2026-10-26 `make all PYTEST_JOBS=4`：5120 passed、89 skipped、17 warnings；控制面/备份 14 passed；真实 LLM 业务 5 passed；同远程 embedding 双库 RAG 2 passed；有界并发 3 passed | DataVec 已内置，扩展目录安装缺文件不应作为内核验收前置条件；完整向量 DML/索引/恢复仍待验收；PG-CONC-01/02 在原始源码中复现；外部 IM、长期压力及外部 wheel 分发未完成；逐轮结果见恢复复验记录 |

其他探测到缺失、但尚未确认当前调用路径需要的函数，不列为首期必补项。不能依据 openGauss 最新版文档中的新语法，推断目标 6.0.5 已支持。

## 3. SQL 兼容问题清单

### SQL-01：兼容函数

| 函数 | 当前用途 | 建议处理与实现边界 |
| --- | --- | --- |
| `to_regclass` | 清理、维护前判断关系是否存在 | 保留 schema/名称解析和不存在时返回 NULL 的语义，验证大小写与限定名 |
| `to_jsonb` | 将记忆行转为 JSONB，用于清理前归档 | 当前整行场景可评估 `row_to_json(...)::jsonb`；兼容函数还需测试 NULL、数组、日期及实际参数类型 |
| `hashtextextended` | advisory lock 的锁键 | 实现稳定的 64 位锁键映射，明确 seed 规则，所有相关调用保持一致；不能声称与 PostgreSQL 原哈希算法等同 |
| `jsonb_path_query_array` | 记忆全文检索生成列表达式中的 `$[*]` | 仅实现已确认使用的路径与输入语义；不支持的路径明确报错，不冒充完整 JSONPath |

兼容函数的 schema 必须在调用方的 `search_path` 中可见。记忆模块会自行设置 `search_path`，不能只修改初始连接设置。生成列表达式使用的函数必须真实满足 IMMUTABLE 要求，不能为了建表通过而随意标记。

2026-10-23 实施补充：真实 Harness 的后台 vacuum 仍有两处 to_regclass 调用，已在可重建的 octop-memory 1.0.2+opengauss.2 补丁中改用参数化 schema/relation catalog 查询。该有限用途不需要实现完整名称解析函数；真实维护回归通过，容器中的实际模块也已核对。

源码依据：

- `src/octop/infra/agents/memory/postgres_storage.py:145`、`:154`。
- `src/octop/infra/agents/memory/thread_cleanup.py:824`、`:839`。
- 依赖 `octop_memory/storage/backends/postgres.py:323`、`:480`。

### SQL-02：UPSERT 语法与冲突语义

现有仓储和初始化辅助逻辑使用 `ON CONFLICT`。候选替代为 openGauss 的 `ON DUPLICATE KEY UPDATE`，但必须逐类核对，禁止全局文本替换。

| 场景 | 建议处理 | 必须保留的语义 |
| --- | --- | --- |
| 简单 KV 更新 | 对可证明等价的表使用原生 UPSERT | 正确命中业务键，只更新原有字段 |
| 无指定冲突目标的重复忽略 | 评估 `ON DUPLICATE KEY UPDATE NOTHING` | 不插入重复行，返回值仍符合调用方预期 |
| 带条件的更新 | 保留等价 WHERE 条件 | 较旧记录不得覆盖较新状态 |
| 指定冲突目标且存在多个唯一约束 | 使用专用事务路径，必要时按业务键加事务级锁 | 只处理预期冲突，其他唯一冲突仍应报错 |
| 更新唯一索引字段 | 使用专用事务更新/插入流程 | 不误更新其他账号或资源，不绕开唯一约束 |

事务方案须让相关写入路径遵循相同锁协议，并以数据库唯一约束兜底；必要时使用 SAVEPOINT 精确处理冲突。裸“先查再插”有并发竞争，`FOR UPDATE` 也无法锁住尚不存在的业务键。

重点业务点：

- 自动化分析：保留 `excluded.observed_at >= automation_analysis_items.observed_at`，防止旧事件覆盖新状态。见 `src/octop/infra/db/repos/automation_analysis.py:65`。
- SSO 关联：同时存在 `(user_id, provider_id)` 与 `(provider_id, subject)` 唯一约束，当前更新会修改 `subject`，不能用任意唯一冲突触发更新代替。见 `src/octop/infra/db/repos/users.py:180`、`src/octop/infra/db/migrations/015_sso_provider_kind.pg.sql`。
- 轨迹事件：同时有事件 ID 与线程序号唯一约束，append/upsert 的冲突范围和依赖 rowcount 的 bool 返回值须分别验证。见 `src/octop/infra/db/repos/trajectory_events.py:44`、`:69`。
- 一次性登录状态/登录码：保留 UPDATE 条件及 RETURNING 的原子消费语义；解析通过不代表并发下已验证。见 `src/octop/infra/db/repos/sso.py:309`、`:332`。

### SQL-03：DDL、自增、检索与外键

- 隔离库已确认原 IDENTITY 语法在 BY 处报 42601；显式 sequence + `DEFAULT nextval(...)`、序列归属和 RETURNING 已通过样例验证。正式 baseline 仍须保留原整数宽度并逐表验收。
- openGauss 6.0 文档支持生成列、GIN 和全文检索相关能力，不把它们预先列为不支持项；验证实际表达式、兼容函数及存储引擎组合。确有不兼容时，再评估普通列加触发器维护。
- 记忆表包含 `DEFERRABLE INITIALLY IMMEDIATE` 外键；openGauss 6.0 的 Ustore 不支持 DEFERRABLE，建议相关表显式使用 Astore，并验证实际 DDL，保留引用完整性。
- `CREATE INDEX CONCURRENTLY` 不预先删除；按独立迁移处理其不能位于事务块内的限制，并验证目标表支持情况。
- 不把可选的 vector 扩展作为控制面初始化前提；实际记忆全文检索使用内置 tsvector。

### SQL-04：隔离实测新增确认的问题

- `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 报 42601，阻断检查点迁移。元数据检查加普通 ADD COLUMN 的事务幂等样例已通过；独立版本化迁移可按水位执行普通加列。
- `SET LOCAL lock_timeout` 报 42704，阻断记忆初始化。目标提供 `lockwait_timeout`，单位毫秒；原型替换后可继续初始化，正式实现仍需核对超时语义。
- 检查点 `array_agg(ARRAY[...::bytea,...])` 报 42704，阻断读取。JSON/Base64 聚合与 bytes 还原的原型已通过保存/恢复/中断续跑；不能仅覆盖写入 SQL。
- 现有测试夹具 `GRANT ALL ON SCHEMA public TO CURRENT_USER` 报 42601，须增加 openGauss 夹具。原始集成测试多数因此未执行到业务 CRUD。
- gs_dump 的数据库名使用位置参数，不能照搬 pg_dump 的 -d/--dbname；备份工具需要独立构造命令参数。匹配版本工具的导出和空库恢复已实际通过。

## 4. 代码适配问题清单

代码改动主要集中在数据库基础设施、运行期 SQL 及相关依赖。应用启动后仍会发送 SQL，独立初始化文件不能覆盖这些执行路径。

| 编号 | 代码问题与位置 | 拟处理方式 |
| --- | --- | --- |
| CODE-01 | `src/octop/config.py:20` 仅允许 sqlite/postgresql；`:57`、`:65` 及 URL 解析也有 PG 精确判断 | 增加明确的 openGauss driver 与方言选择，复用已验证的连接能力，检查 DSN 解析、设置及配置校验入口 |
| CODE-02 | `src/octop/infra/db/factory.py:27`、`:41` 为二分分支；`pool.py:152` 固定 PG dialect | 接入 openGauss 方言，避免误入 SQLite 分支或继续发送不兼容 PG SQL |
| CODE-03 | `src/octop/infra/db/migrate.py:39` 按现有后缀发现迁移；`:2057` 启动时读取 022 DDL；`:2067` 继续执行 ensure/种子逻辑 | 接入独立 baseline 与未来升级路径，逐项适配启动 ensure；新增 SQL 后缀时明确排除其他方言，避免被 SQLite 发现器误收 |
| CODE-04 | `src/octop/infra/db/repos/` 和 migrate 辅助代码内嵌 UPSERT | 按 SQL-02 修改实际调用，保留参数绑定、事务范围和返回值，避免无关仓储重构 |
| CODE-05 | `src/octop/infra/agents/memory/backend.py:33`、`:54`、`:84` 的后端选择与参数传递仅识别现有类型 | 同步默认后端、显式配置、控制面 DSN 复用、记忆实例构造和后续维护分支 |
| CODE-06 | octop-memory 构造即运行 `_init_schema()` / `_migrate_legacy_schema()`，内部还有写入、维护 SQL 和后端工厂 | 在依赖源码增加 openGauss 支持并发布固定版本；不能靠预建表绕过，也不直接修改 site-packages 交付 |
| CODE-07 | 检查点 saver 自带迁移及 UPSERT；octop-memory 当前写死构造 `PostgresSaver` | 评估继承实现 `OpenGaussSaver`，覆盖必要写入 SQL、加列迁移及二进制聚合读取/解码，修改构造入口，保留检查点格式和独立版本水位；原型已通过有限图执行验证 |
| CODE-08 | 记忆清理、存储统计和维护含 backend 精确判断、兼容函数及 PG 调优项 | 同步 openGauss 分支；按能力应用调优，非必要调优不应阻断核心初始化，但不能吞掉实际 DDL/完整性错误 |
| CODE-09 | `src/octop/infra/backup/pg_dump.py:23`、`:47` 调用 PG 客户端；`system_archive.py:212`、`:467`、`:522` 按类型归档/恢复 | 接入匹配版本的 gs_dump/gs_restore，记录 openGauss 归档类型并保持跨引擎恢复校验，在隔离库演练 |
| CODE-10 | dashboard 记忆统计接口的 _MemoryCache 独立持有 Memory，原替换/淘汰/失效路径不确定性释放连接 | 已补齐关闭路径和单元测试；真实回归显式失效自身统计缓存，核验目标库 pool lease 归零后自动删库。此为资源生命周期问题，不是新增 SQL 语法问题 |

依赖代码依据为本地已安装版本，行号随后续版本变化：

- `octop_memory/storage/backends/postgres.py:122`：构造时初始化；`:190`：重设 search_path；`:232`：延迟外键；`:457`：journal 的 autovacuum 调优参数。
- `octop_memory/core.py:1765`：检查点构造；`:1791`：实例化 PostgresSaver；`:1816` 附近：记忆后端工厂。
- `langgraph/checkpoint/postgres/base.py:43`、`:131`、`:297`：独立迁移、UPSERT 和可覆盖类属性。

需要沿类型分支检查所有使用点，不能只增加配置枚举就视为完成。是否涉及 harness 的额外接线，以实施时锁定依赖的构造流程为准。

## 5. 已实施方案与剩余验收

下列事项已在当前 worktree 实施；测试范围和分发边界仍须按后文验收清单评估。

1. **兼容层与控制面 baseline。** 已接入驱动、独立 baseline/版本管理及所需函数；首次初始化、重启和 CRUD 有隔离库测试。
2. **控制面运行期 SQL。** 已按仓储语义适配 UPSERT、SSO、多唯一键与条件更新；测试覆盖真实写入和事务行为。
3. **记忆与检查点。** 已锁定依赖源码补丁和构造接入。上游 PostgreSQL 冲突写入方法由 openGauss 子类逐一覆盖：schema、alias、entity page、active entity、meta、digest；raw event、candidate、atom、entity、journal、episode CRUD 保持继承实现。baseline 已调整 JSONPath 表达式。维护/清理及检查点恢复有隔离库测试。
4. **维护、备份与回归。** 已接入原生 gs_dump/gs_restore 和系统归档；隔离库恢复用例通过。本轮真实 chat/embedding provider 探测通过，补齐后台 vacuum 路径的缺失函数调用及回归；完整 Agent 链路以最新实测记录为准。外部 IM、长时间并发及正式已发布版本升级仍待验收。

首个 baseline 发布后，后续每次结构变化再提供对应 openGauss 增量迁移；控制面版本不能替代记忆与检查点各自的版本管理。

## 6. 验证与环境保护要求

2026-10-26 远端恢复后的隔离复验、向量前置条件和最终清理证据见 [恢复复验记录](validation/opengauss-recovery-20261026.md)。首次部署、正式版本升级、知识库 sidecar 和数据库向量能力仍按各自边界验收。

### 当前核对环境

- 原有 plat 库的业务对象继续只读；用户已授权创建独立临时验证库，并要求验证结束清除。对象 DDL、真实 DML、迁移和恢复仅在本轮新建库执行；临时库已全部删除。
- 不将当前环境 DSN 配置给现有 PG 集成测试；测试中的 schema 清理可能执行 `DROP SCHEMA public CASCADE`。
- 不把连接信息、密码或包含密码的命令输出提交到仓库。

### 独立空测试库验收

- [ ] 三层完整初始化，表/列/索引/外键/种子数据与各自版本记录一致；重复启动保持幂等。
- [ ] 兼容函数覆盖实际入参、NULL、名称解析、search_path 和生成列表达式要求；不支持的 JSONPath 有明确行为。
- [ ] 序列自增和 RETURNING 正确，唯一约束与外键保持完整性。
- [ ] 各类 UPSERT 的插入、更新、重复忽略、条件拒绝、其他唯一冲突、rowcount 和并发结果符合原业务语义。
- [ ] SSO 不错误关联账号，登录状态/登录码只能成功消费一次，旧事件不能覆盖新状态。
- [ ] 记忆写入、更新、删除、全文检索和 namespace 隔离通过。
- [ ] 检查点保存、重启恢复、工具调用中断续跑、人工确认后恢复、重复写入及 thread/namespace 隔离通过。
- [ ] 维护/清理保留数据边界；完整备份能够在另一空库恢复，并包含三层数据库状态及必要工作区文件。
- [ ] 后续提供首个增量迁移时，验证 baseline 到新版本的升级与重复启动。
- [ ] 实施后的相关测试及仓库要求的 `make all` 通过；现有 SQLite/PG 行为不回归。

当前 worktree 已实现首次部署适配，独立容器和真实 LLM 业务链路已有通过证据；真实 embedding/RAG 与灾备续聊也已有单项通过结果，最新质量门数字以 2026-10-23 实测记录为准。扩展整批连接中断 RUNTIME-01 尚未关闭，外部 IM、长期并发和依赖外部分发仍未完成，不能据此宣称全部业务或发布验收通过。控制面 baseline 首次安装不是历史迁移；首个基线发布后再提供对应 openGauss 增量迁移。
