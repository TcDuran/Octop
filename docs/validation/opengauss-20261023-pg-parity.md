# PostgreSQL / openGauss 共用改动对照验证

## 范围与隔离

本轮验证共用连接代理 executemany、记忆缓存资源释放、补丁依赖中的 checkpoint / vacuum，以及现有远程 embedding 知识库流程。验证代码位于独立 worktree，原工作区和现有容器配置不参与修改。

本地 Docker PostgreSQL 的 octop 库仅只读取得 provider 配置。已确认可用的远程 embedding 配置为集团ai / bge-m3；PG 与 openGauss 复用同一配置，不下载或修复本地模型。

每个新增对照用例创建随机独立库，记录名称和 OID；清理只删除记录中同名、同 OID 且连接数为零的库。现有 PG 业务库、远端 plat 和持久 octop 库不进行测试写入，不终止未知连接，不连接现有 IM。

## 验证步骤

1. 现有 PG 初始化、仓库 CRUD、checkpoint 历史和记忆维护用例 → 在新建的专用 PG 库执行。
2. 双库共用路径 → 真实批量写入与回滚、缓存命中/替换/LRU/失效、checkpoint 重开、vacuum、连接租约归零。
3. 共用仓库契约 → settings/secrets/care、trajectory 冲突目标、SSO/用户策略、automation 并发抢占、一次性登录/邀请码、会话与消息投影。
4. 双库远程知识库 → 使用同一 embedding 服务上传、索引、检索引用、权限隔离、修订重建、重启续聊及删除后检索。
5. 质量门与清理 → 记录各轮真实结果，保留失败，核验临时库和凭证清理。

知识库沿用现有本地 SQLite sidecar 向量索引；本轮不代表向量全部存入 PG / openGauss。此前 openGauss 偶发断连 RUNTIME-01 保持未关闭，不能凭本轮单次通过认定已解决。

## 实际结果

本地 Docker PostgreSQL 实测版本为 18.1。原有 PG 回归在一个新建且记录 OID 的专用库执行；其中会重置 public schema 的测试只指向这个临时库，不指向 octop 配置来源库。

| 批次 | 结果 | 实际覆盖 |
| --- | --- | --- |
| postgres-regression-01 | 13 passed、2 warnings，15.17 秒，退出码 0 | PG 初始化/迁移、控制面仓库 CRUD、连接探测、原生 pg_dump/pg_restore 系统归档、首次设置绑定、知识库结构、图历史读取、记忆维护范围/回滚/锁/HTTP 作业 |
| shared-core-01 | 7 passed、2 deselected，54.13 秒，退出码 0 | 两库各 2 项真实测试，加 SQLite/PG/openGauss 缓存生命周期单元参数化 3 项 |
| remote-rag-01 | 2 passed、4 deselected、10 warnings，82.81 秒，退出码 0 | PG/openGauss 各一次完整真实远程知识库流程 |
| repo-contracts-01 | 9 passed、3 failed、1 error、6 deselected，200.12 秒，退出码 1 | 六组共用仓库契约在 PG/openGauss 各执行一次；两项 PG 并发失败及一项 openGauss 断连，清理保护触发 |
| pg-baseline-01 | 2 failed，15.96 秒，退出码 1 | 将 secrets / sso 仓库模块替换为 HEAD cc623be1 原始源码，同两项 PG 并发失败均复现 |
| make all PYTEST_JOBS=4 | 5120 passed、68 skipped、17 warnings，pytest 594.88 秒，命令总计 608.04 秒，退出码 0 | 格式、Ruff、mypy 与默认非 live 套件；数据库 opt-in 变量未启用，不能替代上述真实数据库验收 |

两种数据库的真实 embedding 维度均为 1024，均通过真实 search_knowledge 调用、683→927 修订后的新值断言、原文 doc_id 引用、私有库权限、重启检索与删除后检索为空。使用同一部门ai / deepseek-flash 真实 Harness 完成问答；不是 FakeHarnessAgent 或模拟 embedding。

真实缓存测试覆盖命中不新增资源、fingerprint 替换、LRU 淘汰、单个/全部失效及重复失效；检查实际 postgres_pool registry 的引用计数，最终归零，并在重新打开后确认 checkpoint 和记忆候选未丢失。vacuum 在真实 journal 与三张 checkpoint 表执行；PG 与 openGauss 均通过。

本轮未修改知识库模型实现、默认 ONNX 策略或本地模型依赖；测试显式配置 backend=remote。修改限于验证测试、测试辅助断言和文档；没有新增产品代码修复。

## 未关闭的问题

### PG-CONC-01：并发获取或生成 secret

当前和原始 SecretRepo.get_or_create 都采用先 SELECT、事务内再次 SELECT、再 INSERT。PG 上两个事务仍可同时看到不存在，随后一个 INSERT 抛 secrets_pkey 的 UniqueViolation。openGauss 分支已有事务键锁；PG 路径没有进入该新增分支。

在原始 HEAD 的 secrets.py 替换对照中复现相同失败，支持将其判定为既有仓库级并发问题，而非本次 openGauss 分支引入。建议另项修复为 PG 原子插入忽略冲突后读取胜出值，必须保持所有并发调用返回同一个 secret，不能以覆盖式 UPSERT 轮换已创建的值。本轮未修改该产品行为。

### PG-CONC-02：并发创建同种 SSO provider

四个并发 upsert_by_kind('oidc') 中，唯一约束冲突被捕获后，代码继续在已经 aborted 的 PG 事务内 SELECT，产生 InFailedSqlTransaction。原始 HEAD 的 sso.py 替换对照同样复现。建议另项采用 PG 原子 UPSERT，或为可恢复的冲突操作设置 SAVEPOINT 后回滚再读，不能只捕获异常后继续使用失效事务。

基线对照仅替换这两个仓库模块，连接池和依赖仍使用当前测试环境；不是声称完整原始发行包/依赖矩阵已经验证。组合用例在 provider 创建处中断，不能将其后的一次性登录与邀请码断言描述为该 PG 批次已通过。

### RUNTIME-01：openGauss 并发事务断连再次复现

automation 的并发 reserve 在进入事务时出现 server closed the connection unexpectedly。该组 teardown 发现四个 idle 连接，按保护规则拒绝终止连接或删库。测试进程退出后核对登记名称 octop_verify_ed50c172142a4994、OID 143831 和连接数 0，才精确删除；未放宽夹具保护。

该失败发生在 BEGIN 相关调用，不能定性为 SQL 语法错误，也未定位服务端、网络或连接生命周期根因。没有添加盲目重试，不将本轮其他通过结果拼接成完整并发验收通过。

## 复现与证据

新增测试入口：tests/integration/test_database_shared_live.py。显式设置 OCTOP_TEST_SHARED_DATABASES=1、OCTOP_TEST_PG_ADMIN_DSN（本机 PG 管理连接），以及原有 openGauss 管理连接环境变量。PG 管理连接在夹具中强制使用 postgres 库；测试应用连接由夹具切换到随机新库。

非 live 检查：uv run --frozen pytest tests/integration/test_database_shared_live.py tests/unit/api/test_memory_client_cache.py -q -m "not live"。

真实 RAG 额外提供 0600 的 OCTOP_LIVE_PROVIDER_FILE，并设置 OCTOP_LIVE_PROVIDER_NAME、OCTOP_LIVE_MODEL、OCTOP_LIVE_EMBED_PROVIDER_NAME、OCTOP_LIVE_EMBED_MODEL；执行同一入口并使用 -m live。不得把已有业务库的地址放入会重置 public 的 OCTOP_TEST_DATABASE_URL。

本地私有证据目录：data/opengauss/pg-parity-20261023，包含分轮 pytest 日志/JUnit、results.jsonl、summary.json、database-ledger.json 和数据库目录前后快照。凭证不写入本文或测试源码。

## 最终清理与范围

- 本轮共登记 21 个随机临时库，逐一确认不存在；已有两端数据库名称/OID 清单均保留，持久 openGauss octop 库的 OID 仍为 88543。失败库的保护性清理错误保留在原日志中，进程退出后仅对同名、同 OID、零连接的登记库补做清理，无强杀会话。
- PG 配置来源库的 provider 完整行摘要与测试前一致。私有 provider 凭证快照、五轮测试 home/工作区/备份归档、质量门临时目录和管理库临时记录目录均删除；保留脱敏日志、JUnit、复制出的数据库登记/清理审计及原有 0600 的 .env.opengauss。
- 67 个变更/新增路径的数据库/provider 凭证扫描零命中，日志/JUnit 也未命中这些真实凭证。现有 octop 与本地 PG 容器保持 healthy，本轮未重建、重启容器或调整端口。
- 本轮没有修改产品源码，也没有修复或移除本地 embedding 模型。已新增 opt-in 双库回归，并把缓存单元扩展为 SQLite/postgres/openGauss 参数化。所有改动留在 worktree，未提交或推送。
- 质量门绿色与真实并发批次失败同时成立；PG-CONC-01/02 和 RUNTIME-01 保持未关闭，不能称为完整数据库兼容及发布验收通过。
