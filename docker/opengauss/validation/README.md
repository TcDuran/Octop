# Octop 产品向量接线验证入口

本目录说明当前 worktree 可复现的产品验证入口，不提供或替代官方 `datavec--0.7.2.sql` 安装脚本。

这里记录的是隔离库测试，不能替代 AJ 生产验收。AJ 在 2026-10-28 已通过应用初始化、重启持久化和 Nginx 外网入口检查，2026-10-29 已更新白标镜像并完成 OA 只读 MCP/OIDC 链路验收；完整聊天、知识库、普通账号隔离和隔离恢复演练仍需单独完成，详见 [AJ 操作记录](../AJ_DEPLOYMENT_RECORD.md)。不要将下列 live 测试直接指向 AJ 的生产 `octop` 库或既有 `plat` 库。

完整记录见 [产品向量接线实测](../../../docs/validation/opengauss-product-vector-20261026.md)。本轮真实知识库使用第三方 embedding，实际向量索引为 SQLite；未实现 openGauss 向量存储产品后端。

最终交付按用户要求保留该架构，见[撤回复验记录](../../../docs/validation/opengauss-retained-sidecar-20261026.md)。额外的混合存储灾备用例为 `tests/integration/test_opengauss_scenarios_live.py::test_knowledge_sidecar_and_metadata_backup_restore`，需要 PATH 中存在同版本 `gs_dump` / `gs_restore`。

在仓库根目录，按报告准备私有 provider 文件和环境变量后运行：

```bash
uv run --frozen pytest \
  tests/integration/test_database_shared_live.py::test_same_remote_embedding_rag_both_databases \
  tests/integration/test_opengauss_live.py::test_real_llm_chat_memory_extraction_and_restart \
  -m live -n 0 -q -x --tb=short \
  --basetemp=<本轮独立临时目录> --junitxml=<本轮结果文件>
```

设置 `OCTOP_LIVE_RESULT_FILE` 可保存以下运行时观测：

- `knowledge_vector_destination`：真实 sidecar 路径、文档块数、embedding 维度、SQLite 完整性及测试库应用向量列清单。
- `memory_vector_wiring`：真实 Harness Memory 的关系后端、向量索引和 embedding provider 配置状态。

测试夹具只创建随机独立库，登记名称/OID，并在零会话条件下精确删除；不会终止未知会话。测试结束须额外复查登记库已删除，保留台账后清理本轮临时目录及私有 provider 副本。凭证和测试 home 不进入 Git。

这些测试通过表示原有知识库和 Memory 行为正常，不能单凭退出码 0 就认定向量已经存入 openGauss；必须同时审阅运行时观测与报告中的产品接线结论。
