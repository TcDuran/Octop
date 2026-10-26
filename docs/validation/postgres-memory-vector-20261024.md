# PostgreSQL 记忆与向量补充验证

日期：2026-10-24，Asia/Shanghai。用户要求 openGauss 不可连接期间先完成其他可测工作。本轮只使用本地 PostgreSQL，不连接远端 openGauss，不启用已有 IM、不重启容器、不下载本地 embedding 模型。

## 结论与边界

本地 PG 的记忆关系存储、真实 HTTP 权限与重启读取、维护回滚、受控并发、合成向量检索，以及记忆/checkpoint/向量组合原生恢复均得到补充验证。当前正式专项为 **18 passed、3 xfailed**；3 项预期失败对应两类原有 SDK 缺陷，不计为通过，也没有修复。真实 bge-m3 在 17:30 的请求仍返回 HTTP 502。

`tests/integration/test_postgres_memory_vector_live.py` 中的 `PgTestIndex` 和 `ControlledEmbedding` 是显式测试夹具。当前 Harness 构造 Memory 仍未传入 `vector_index` / `embedding_provider`，SDK 也没有内置 PG/openGauss 向量索引适配器。本轮没有实现这些产品接线，没有修改产品源码、数据库部署配置或依赖。

## 实际批次

私有证据目录为 `data/opengauss/pg-memory-vector-20261024`，包含脱敏日志、源库只读摘要、名称/OID 登记和清理审计。以下为本轮实际结果，历史失败保留。

| 批次 | 结果 | 说明 |
| --- | --- | --- |
| first-pytest.log | 8 passed，退出 0 | 首轮生命周期、隔离、异常写入重建、重连/checkpoint、4 个 namespace 并发；旧 pytest 临时目录自动回收产生 93 条警告，后续改用本轮独立 basetemp，未另行手工清理旧目录。 |
| 5b56c654 | 12 passed、3 xfailed，退出 0 | 增加事务回滚、模拟 HTTP/返回体故障，以及废弃/删除和 null embedding 的缺陷探针。 |
| d10f3fdc | 17 passed、1 failed、3 xfailed，退出 1 | 新增真实 PG HTTP 测试；直接 SQL 的 namespace 断言未考虑 SDK 转为小写。修正测试预期，未改产品行为；该失败保留。 |
| 28004130 | **18 passed、3 xfailed、2 warnings，18.47 秒，退出 0** | 修正后的完整专项；警告原文保留在日志。 |
| regression-82e39961 | **13 passed、2 warnings，23.97 秒，退出 0** | 原有 PG 控制面初始化/迁移、仓库 CRUD、连接探测、系统备份恢复、首次设置绑定、知识库结构、checkpoint 历史和记忆维护 HTTP 作业。 |
| scale-restore-45a837ad | **2 项诊断脚本检查通过，退出 0** | 2048 条 1024 维合成向量精确/ANN 对照，以及记忆/checkpoint/向量/ANN 索引组合原生恢复。不是 pytest 用例数。 |
| 31ac60d9（原版 SDK） | **3 failed、18 deselected，11.57 秒，退出 1** | 从原工作区只读复制 octop-memory 1.0.2 到本轮临时目录，实际确认导入副本后，禁用 xfail 运行三个缺陷用例；同样在废弃、删除和 null embedding 处失败。副本已删除，原工作区未改动。 |
| quality-7d7c6346 | **5120 passed、89 skipped、17 warnings，退出 0** | `make all PYTEST_JOBS=4`：格式、Ruff、mypy 和默认非 live 套件通过；pytest 489.92 秒，命令总计 503.05 秒。未启用数据库 opt-in，专项的 21 项在此批次跳过；不把这一绿色结果当作缺陷或远程模型已通过。 |

## 本轮通过的内容

- SDK 写入与更正：`create_atom` 真实写入 PG 后索引；同实体重复 assertion 被拒绝；相同文本更正不新增 atom；更正产生 successor、废弃旧 atom、更新树引用、保留 lineage，向量索引只剩新版本。`atom_count` 保持 1。
- SDK 隔离：两个 namespace 互相不可读取 atom；索引层刻意使用相同带中文/引号的 atom_id，删除一个 namespace 不影响另一个。此处的 SQL 隔离由测试适配器实现，不能算产品向量权限已完成。
- 写入失败与恢复：embedding 超时、PG 维度错误、使用已关闭客户端连接时，已提交关系记录保留、索引缺失且有 warning；显式调用 SDK 私有索引方法两次后只有一条记录。此操作是诊断重放，不是产品自动重建或修复功能。已关闭客户端连接不等于服务端崩溃/网络中断验证。
- 事务边界：在写 journal 时主动抛异常，raw/candidate/atom/entity/tree/journal 六张关系表计数均回到操作前，且不写向量。与前项提交后索引失败的降级契约分开验证，不能要求两种情况具有相同原子性。
- 远程异常契约：使用真实 SDK OpenAI-compatible provider、模拟 HTTP 502、非法 JSON、缺失 embedding 字段，写入保留且向量召回降级；不会访问模拟域名。实际远程请求的 502 另行记录，没有用这些模拟结果替代服务可用性。
- 维度切换：测试适配器拒绝从 vector(3) 静默切为 1024 维，既有记录不丢失；这项 guard 属于测试夹具，不是 Octop 已实现的模型切换策略。
- 重连与受控并发：关闭并重开 Memory 后，节点、向量和 checkpoint 保留；4 个预先初始化的 namespace 并发写入共 48 条，结果互不混入。它不覆盖相同 key 的 secret/SSO 竞争，也不是压力或长期稳定性测试。
- 真实 HTTP：使用真实 PostgreSQL 控制面和记忆关系库、真实 FastAPI/auth 路由，两个普通用户各自创建并列出记忆；跨用户读取、更正、废弃被 403 拒绝，在自己的 Agent 下读取另一 Agent 的 atom_id 返回 404；停止再启动测试服务后重做这些检查，并直接核对 PG 记录。Harness 使用项目已有 fake，未请求 LLM；应用没有向测试向量表写入，不能称为向量召回或真实对话端到端验收。
- 维护契约：复用 5 个既有 PG 用例，覆盖另一专家仍可写、备份失败不删除、SQL 失败双表回滚且保留备份、namespace 锁和繁忙行跳过、单次清理上限；SDK 连接租约最终归零。

## 规模与组合恢复

规模检查使用固定随机种子的 **2048 条 1024 维合成向量**、4 个 namespace。8 个 query 分别执行全库及 namespace 过滤，共 16 次精确检索/HNSW 对照。ANN 检查显式设置 `hnsw.ef_search=200` 并关闭 seqscan；每次均命中 HNSW、返回 10 项、最近项正确，过滤结果无串 namespace。Recall@10 最低 0.9、均值 0.99375；这是合成几何上的近邻一致性，不是真实语义质量。

同时另取默认执行计划：全库检索 8 次均选 HNSW，按 namespace 过滤的 8 次均选 Seq Scan + Sort。不能宣称增加索引后所有过滤查询都会自动使用 ANN，也没有修改产品规划器参数。样本仍较小，不据此给出生产容量或延迟承诺。

组合恢复在源临时库中先创建/更正记忆、保存 checkpoint，并加入上述 ANN 表；关闭 Memory 后，原生 PG 18 的 `pg_dump` / `pg_restore` 恢复到另一空临时库。恢复后重新构造 Memory，核对 2 条 live atom、1 条 superseded 历史 atom、树节点、checkpoint 完整内容、测试向量排序、2048 行 ANN 表及 HNSW 索引，最近项仍正确。归档 7,967,251 字节，SHA-256 为 `bc8d64c7ae515670816d9790743426ce3b04106f83e5240e60442c979dd7c173`，验证后已删除。

此项验证 SDK 关系数据/checkpoint/测试向量可以组合原生恢复；它不是已上线 Agent 向量配置的全应用灾备。产品自己的系统归档路径另由原有 13 项 PG 回归覆盖。

## 两类原有 SDK 问题

### MEM-VEC-01：废弃/删除后的向量召回没有过滤

`Memory.deprecate_atom` 与 `Memory.delete` 将关系 atom 标记为 deprecated，默认关系列表也已排除它；但这两条路径没有删除对应索引记录，`_gather_vector_atoms` 又通过 `get_atom(id)` 直接返回该已废弃 atom，没有检查 `deprecated_at`。新增两个 strict xfail 分别复现废弃和删除路径；测试调用的是 SDK 向量来源，不是当前未接线的 Harness。

### MEM-VEC-02：远程 embedding=null 导致调用报错

SDK `OpenAIEmbeddingProvider.embed` 将返回值传给 `list(...)`。响应为 `{"data":[{"embedding":null}]}` 时抛 `TypeError`，它不在 `VECTOR_ERRORS` 中，提交后的关系记忆已经存在，但 `Memory.store` 向外抛错。新增 strict xfail 验证该异常，finally 另行确认关系记录已保留、向量未写入。

当前依赖与原工作区 SDK 的相关方法 AST 对照一致：core 的 delete/deprecate/index 方法、vector gather、embedding provider 以及 driver error 分组均相同；证据为 `sdk-baseline-comparison.json`。按用户“原有问题可以不管”的范围，只记录风险和复现用例，不扩大修复。

另用原版 octop-memory 1.0.2 源码副本实际运行三个探针，均在相同位置失败，见上表 31ac60d9 和 `original-sdk-probe.json`。该对照只替换 SDK，应用和其他依赖仍使用当前环境，不是完整原始发行包矩阵；没有修改原工作区的文件。

## 未完成项

- 真实第三方 embedding：本轮使用源库现有集团ai / bge-m3 配置，2 条真实中文输入调用返回 HTTP 502，未得到向量；源 provider 完整行摘要和扩展清单前后不变，见 `remote-embedding.json`。
- 产品向量接入与真实语义验收：PG/openGauss 索引适配、Harness 依赖传入、模型维度策略、自动补索引尚未实现；真实对话提取→入库→换表达召回、向量 HTTP 权限和全应用灾备仍需这些前置条件。
- openGauss 仍不可连接，本轮没有新证据，不将 PG 通过结果替代其初始化、升级、向量写入、原生恢复或稳定性验收。此前原有 PG secret/SSO 并发问题保持原状态。

## 复现与安全

专项需要显式 `OCTOP_TEST_PG_VECTORS=1` 和 `OCTOP_TEST_PG_ADMIN_DSN`，后者只能指向 loopback 管理连接。运行 `uv run pytest tests/integration/test_postgres_memory_vector_live.py -q -rx`；默认套件未启用时全部跳过。凭证只在内存/子进程环境读取，不放在命令参数或本报告中。

夹具复用 OID 登记的独立 PG 建库/删库流程；每个用例关闭自己的 Memory、checkpoint 和控制面池，确认连接租约归零，核对同名同 OID、零会话后精确删除。未知会话不会被强杀，源 octop 库不会被 reset。

本轮 71 个临时库（包含首轮 8 个默认 pytest 目录中的登记）均已核对最终不存在，源数据库名称/OID 目录、providers/settings/channels 整表摘要与源扩展清单保持一致。没有终止未知会话。已完成批次的测试 home、工作区和备份副本已删除，原版 SDK 临时源码副本和组合恢复归档也已删除，保留脱敏日志、摘要及合并名称/OID 台账。证据为 `cleanup-audit.json`。

变更文件和已保留证据的实际数据库/provider 凭证扫描未命中，计数及最终结果见 `credential-scan.json`；新增测试的 Ruff lint/format 和 `git diff --check` 通过。质量门临时目录已在运行完成后删除。现有 octop、PG 和 OIDC 辅助容器仍 healthy，未启动额外监听端口的 Octop 实例。所有变更保留在独立 worktree，未提交或推送。
