# 保留 SQLite 知识库索引的 openGauss 复验

日期：2026-10-26，Asia/Shanghai。

## 最终范围

用户确认保留 Octop 原有知识库架构：业务元数据使用 openGauss，分块文本和 embedding 使用每个知识库的 `knowledge/<kb_id>/index.sqlite`，余弦相似度由 Python 计算。embedding 使用现有第三方服务，不下载本地模型。PostgreSQL 仍沿用同一 SQLite sidecar 方案。

因此本次交付只适配 openGauss 数据库兼容性，不新增知识库或 Memory 的数据库向量存储能力；DataVec 和 HNSW/IVFFLAT 不再是本次应用部署的前置条件。Agent Memory 保留此前的 openGauss 关系存储、checkpoint 和现有召回能力，不增加向量依赖。

## 撤回核对

- `knowledge/index.py`、`jobs.py`、`retrieve.py`、`service.py` 和 `agents/manager.py` 与本 worktree HEAD 字节一致。
- 删除新增 `OpenGaussKnowledgeIndex`、索引选择工厂及 `023_knowledge_vectors.sql`；openGauss 保留 baseline 22，没有本轮新增的升级步骤。
- 删除新增 Memory DataVec 索引、向量表 SQL、embedding 自动接线及 `1.0.2+opengauss.3` 包；依赖和可重建脚本恢复为 `1.0.2+opengauss.2`。该包 SHA-256 为 `bc8123739cdbd2dda9fa319d891adb0d609178ee2a9347bd9661502a4e9b2a8c`。
- 删除本轮为新增向量路径写的测试；保留原有知识库真实流程和 SQLite 落盘观测。此前的 openGauss SQL、初始化、Memory/checkpoint、备份兼容改动保留。
- SQLite/PG/openGauss 共用的既有 Memory 缓存连接释放修复保留；本轮新增的 SDK 空 occurred_at 修改随 `.3` 撤回，不扩大原有问题修复范围。

撤回审计保存在本地忽略目录 `data/opengauss/vector-wiring-20261026/final-revert-audit.json`。

## 复验范围

1. 重复初始化、模拟升级失败回滚、包含自定义函数/索引的原生备份与恢复。
2. PG/openGauss 两侧真实第三方 bge-m3（1024 维）及真实 Agent search_knowledge、引用、修订、权限、重启、删除。
3. openGauss 真实 Memory 保存、辅助模型提取和重启续聊，确认没有新增 vector_index/embedding_provider。
4. 新增混合存储灾备场景：备份文档元数据及 SQLite 向量，删除文档后确认不可检索，恢复完整系统备份，重启后核对 ready 状态、SQLite 完整性、维度和原文引用，并真实调用 embedding 检索。
5. `make all PYTEST_JOBS=4`，数据库/live opt-in 从质量门环境移除。

所有数据库写入只进入登记名称/OID 的临时库；既有 plat、持久 octop 以及本地 PG 配置源库不参与业务写入。

## 最终结果与清理

| 验证 | 结果 |
| --- | --- |
| openGauss 初始化、升级回滚、原生备份恢复 | 3 passed，197.98 秒，退出码 0；恢复后函数可调用、索引存在 |
| PG/openGauss 真实知识库、openGauss Memory、混合存储灾备 | 4 passed、14 warnings，270.35 秒，退出码 0；无 skipped/xfail |
| 完整质量门 `make all PYTEST_JOBS=4` | 5119 passed、90 skipped、17 warnings，退出码 0；pytest 447.79 秒，总耗时 460.20 秒 |
| 安装依赖核对 | 实际安装的 Memory core/openGauss 后端与 `.2` 包逐字节一致，新增向量模块不存在 |
| 最终清理 | 登记的 8 个临时库（7 个 openGauss、1 个 PG）均核对不存在，未强制终止未知会话 |

两侧真实知识库分别在上传后和修订重启后检查 SQLite 完整性、1024 维 embedding（每块 4096 字节）及数据库应用向量列为空。混合灾备恢复后重新检查同样的 SQLite 文件和 ready 文档状态，调用第三方 embedding 后成功检索 683 元原文及其 doc_id 引用。Memory 再次观测到 vector_index_configured=false、embedding_provider_configured=false。

数据库目录、PG 源库 providers/settings/channels 行摘要、双方扩展清单，以及远端 plat/持久 octop 的 public 关系目录均与本轮开始时一致。测试 home、SQLite sidecar、临时备份归档和私有 provider 副本均已删除；最终 94 个文本文件凭证扫描无命中。

质量门跳过项不计为数据库或模型验收通过。专项均实际执行；第三方 embedding 不依赖本地可选 fastembed 包。首次收尾与质量门临时目录清理相遇，出现目录非空异常；测试进程结束后重新执行清理和审计成功，首次异常仍保留在运行记录中。

本轮可复核证据目录为 `data/opengauss/retained-sidecar-20261026/50f4c6b1/`，包含两批 JUnit/脱敏日志、运行时观测、依赖核对、完整质量门日志、数据库名称/OID 台账及清理审计。新增测试入口为 `tests/integration/test_opengauss_scenarios_live.py::test_knowledge_sidecar_and_metadata_backup_restore`。未提交或推送。

## 已终止的 DataVec 试验

此前为数据库原生向量试验创建了独立临时实例，没有替换共享数据库容器或挂载共享数据目录。本机 ARM/x86 模拟实例启动失败；远端同镜像 x86 临时实例解决 Docker mbind 限制后成功启动。

远端临时实例的 1024 维插入、距离查询及 UPDATE/DELETE 事务回滚执行后，在创建 HNSW cosine 索引时发生 SIGILL。FFIC 指向 vector_norm → HnswCheckNorm，尚不能据此判定历史共享环境断连的原因。失败没有通过重试或跳过描述为成功，也没有在共享实例重放。日志保存在 `data/opengauss/vector-wiring-20261026/native-crash.log`。

本机与远端的 `octop-opengauss-vector-e31a77ab` 临时容器和同名独立数据卷均已核验并删除，SSH 转发已关闭；临时库随其所属独立卷删除。私有模型配置副本、临时实例口令文件和该试验的 pytest home 已清理。独立失败证据保留，不作为当前 SQLite 方案的验收阻塞。
