# Octop 产品向量接线验证

日期：2026-10-26，Asia/Shanghai。用户要求验证应用是否实际调用第三方 embedding，并向 openGauss 写入、检索向量。本轮验证当前代码行为，不实现新的产品后端。

后续范围调整：用户确认保留 SQLite sidecar，相关数据库向量接入试验已撤回。下文保留当时的观测和验收边界；当前不将未接入 DataVec 视为缺陷或部署阻塞，最终状态见[保留 SQLite 方案的复验](opengauss-retained-sidecar-20261026.md)。

## 已确认结果

知识库在 PG/openGauss 两侧均调用真实第三方 `bge-m3`，上传文档后生成 1024 维向量，实际 Agent 调用 `search_knowledge` 回答并引用文档；修订住宿上限从 683 元为 927 元后，重启仍读取新版本，跨用户访问被拒绝，删除后不再召回。

上传后及修订重启后均检查真实落盘文件：两侧各有一个 `knowledge/<kb_id>/index.sqlite`，对应文档有 1 个 chunk、4096 字节的 float32 embedding，`PRAGMA integrity_check` 为 `ok`。同时查询各自测试库系统目录，应用表中的 `vector`、`sparsevec`、`halfvec` 列清单均为空。

因此，当前知识库流程可用，但向量存储和相似度查询仍走 SQLite sidecar；这不是“产品已接入 openGauss DataVec”的验收通过。对已有知识库流程的测试成功数不能计为新增数据库向量后端的成功数。

真实 Agent Memory 的运行时观测为：`backend=OpenGaussMemoryBackend`、`vector_index_configured=false`、`embedding_provider_configured=false`。真实 `memory_save`、辅助模型提取、关系表持久化、重启续聊均通过，但这些成功不能解释为向量语义召回已启用。

| 检查 | 结果 | 覆盖边界 |
| --- | --- | --- |
| PostgreSQL 真实知识库 | 通过 | 第三方 embedding、真实 Agent 工具与引用、修订、重启、权限、删除；向量实际落在 SQLite |
| openGauss 真实知识库 | 通过 | 同上；两次落盘观测均无应用向量列 |
| openGauss 真实 Memory | 通过现有能力回归 | 保存、提取、关系持久化和续聊通过；向量索引和 embedding 依赖均未配置 |
| openGauss 产品向量接入验收 | **未通过：功能尚未接入** | 当前知识库索引固定 SQLite，Memory 没有接入向量依赖；不是 DataVec 执行 SQL 报错 |

三项 pytest 共 **3 passed、14 warnings，172.30 秒，退出码 0**，没有 skipped/xfail。各项耗时分别为 13.211、68.328、90.608 秒，包含夹具建立与清理。检查对象均为当前 worktree；不代表另一运行中的 Octop 镜像或上游新版本也已完成同一验收。

## 执行范围与证据

- worktree：`octop-opengauss`，分支 `feature/opengauss-support`。本轮仅新增测试观测和文档，没有修改产品逻辑。
- 依赖：Octop 1.0.2、octop-harness 1.0.4、octop-memory 1.0.2+opengauss.2。
- 私有脱敏证据：`data/opengauss/product-vector-20261026/0c7f4d29/`。只读复用本地 PG 现有模型配置，副本仅写入本轮独立测试库，不启用 IM，不下载本地模型。
- 所有应用 DDL/DML 仅在随机独立库执行；本轮没有对共享实例执行 DataVec 向量 DML 或补扩展安装文件。
- HTTP 使用真实 FastAPI/auth，`patch_llm=False`，第三方模型和 Harness 均为真实调用。没有注入 `PgTestIndex`、合成 embedding 或替代产品索引类。
- `knowledge_vector_destination` 记录真实文件、文档块数、维度、SQLite 完整性和数据库向量列；`memory_vector_wiring` 记录真实 Harness Memory 的依赖配置状态。

## 调用链定位

| 环节 | 当前代码 |
| --- | --- |
| 文档生成向量并存储 | `src/octop/infra/knowledge/jobs.py` 的 `process_document` 调用 `embed_knowledge_texts`，随后直接调用 `KnowledgeIndex.replace_doc_chunks` |
| 问题向量及检索 | `src/octop/infra/knowledge/retrieve.py` 的 `_retrieve_context_sync` 调用 embedding 后直接调用 `KnowledgeIndex.search` |
| 实际知识库索引 | `src/octop/infra/knowledge/index.py` 固定打开本地 `index.sqlite`；embedding 存为 BLOB，余弦相似度在 Python 中计算 |
| 第三方 embedding | `src/octop/infra/knowledge/embed.py` 的 remote 分支调用配置的 `/embeddings` API |
| Agent Memory 构造 | 已安装 Harness 的 `memory/runtime.py::_construct_memory` 只传 namespace、backend、backend_config；没有传入 vector_index / embedding_provider |

`source-wiring-audit.json` 核对了上述四个知识库产品文件的 SHA-256，它们均与本 worktree HEAD 完全一致。这是原有设计边界，不是此次 openGauss 适配把向量改到了 SQLite。

## 验证命令

凭证仅通过私有环境提供，运行时设置 `OCTOP_TEST_SHARED_DATABASES=1`、`OCTOP_TEST_OPENGAUSS=1`、本地 `OCTOP_TEST_PG_ADMIN_DSN`、`OCTOP_DATABASE_*` 和 `OCTOP_LIVE_PROVIDER_FILE`。provider 文件只包含获准用于测试的配置，权限 0600。chat 选择部门ai/deepseek-flash，embedding 选择集团ai/bge-m3。

```bash
uv run --frozen pytest \
  tests/integration/test_database_shared_live.py::test_same_remote_embedding_rag_both_databases \
  tests/integration/test_opengauss_live.py::test_real_llm_chat_memory_extraction_and_restart \
  -m live -n 0 -q -x --tb=short \
  --basetemp=<本轮独立临时目录> --junitxml=<本轮结果文件>

make all PYTEST_JOBS=4
```

## 清理与验证结果

三个临时库已按登记名称和 OID 核验不存在：

| 数据库 | OID | 结果 |
| --- | --- | --- |
| `octop_parity_3a43cd6428c84e6a` | 490781 | 已删除 |
| `octop_verify_631909ffdaa94cde` | 170637 | 已删除 |
| `octop_verify_ec593096e3904aa4` | 171497 | 已删除 |

清理审计核对两侧数据库名称/OID 目录、PG 源库 providers/settings/channels 完整行摘要、扩展清单，以及远端 plat/持久 octop 的 public 关系目录与扩展清单均未变化。测试 home/工作区/sidecar 和私有 provider 配置副本已删除；保留脱敏日志、JUnit、来源文件摘要及名称/OID 台账。首次凭证扫描 87 个文本文件无命中。

完整 `make all PYTEST_JOBS=4` 通过：**5120 passed、89 skipped、17 warnings，退出码 0**，pytest 454.26 秒、命令总计 466.75 秒。包含格式、Ruff、mypy 和默认非 live 套件。执行时移除了数据库/live opt-in 环境变量，避免质量门重复访问远端；89 项跳过不算通过的数据库验收。

测试观测代码、验证入口与本报告均保留在 worktree；未提交或推送。新增入口说明见 [本地验证目录](../../docker/opengauss/validation/README.md)。

## 后续实现边界

如果要求向量全部存入 openGauss，需要在现有知识库写入和检索接点增加数据库向量索引后端，并验证文档替换/删除、租户权限、模型维度切换及备份恢复。若 Agent Memory 也要求向量语义召回，还需同时配置实际向量索引和第三方 embedding provider。本轮没有自动修改这两项产品能力，也没有扩大修复原有 SDK 缺陷的范围。
