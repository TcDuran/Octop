# openGauss 与当前 fork 的整合验证（2026-10-26）

## 基线与合并范围

- 功能提交：67f6d1ac，feature/opengauss-support。
- 当前 develop 基线：f6b895fc，包含 v1.0.3b1 官方同步及独立的 Docker/CPU 修复。
- 独立整合工作树：octop-opengauss-integration；分支 feature/opengauss-integration-20261026。
- 本地合并路径：功能分支 → develop → release/1.0.3b1-opengauss → main，再同步 develop，保持 main 为 develop 的祖先。
- 沿用当前 1.0.3b1 版本号；本轮不创建发布 tag、不推送远端、不重启服务。

## 整合修复与存储边界

1. 保留已应用的 openGauss baseline22，新增专用 023–025 迁移。补齐用户组、资源授权、图标/共享字段，以及官方 v22 新增的 skill_packages.shared。
2. baseline22 已有完整 automation_analysis_items 表，025 保留该表和数据，对齐官方水位；结构对照验证覆盖其约束、索引和序列。
3. 实机发现 ADD COLUMN IF NOT EXISTS 不被 openGauss 6.0.5 接受；已改为由版本水位和事务控制的普通 ADD COLUMN。升级失败可回滚、重复启动不重复加列。
4. 适配用户组成员幂等写入和官方新增的权限初始化。权限扩展执行一次，用户之后移除的权限不会在重启时重新授予。
5. 依赖保留新版 Harness，继续使用定制 octop-memory 1.0.2+opengauss.2，及 checkpoint-postgres 3.1.0。原定制源码包保持不变。
6. 新增 docker/opengauss/build-dist.sh，将 Octop 与定制 Memory 两个 wheel 成对交付，解决只交付 Octop wheel 时无法取得未公开依赖的问题。公共 PyPI 单包安装不是本适配版的交付方式。

相对当前 develop，infra/knowledge 和 agents/manager.py 没有新增差异。知识库继续用第三方 bge-m3 生成 1024 维向量，保存到每库 SQLite index.sqlite；业务元数据、Memory 关系数据和检查点使用 openGauss。没有新增原生 DataVec 知识库/Memory 向量后端，也没有下载本地模型权重。

## 验证结果

| 项目 | 结果 |
| --- | --- |
| 完整质量门 make all PYTEST_JOBS=4 | 5265 passed、94 skipped、17 warnings；退出码 0，总耗时 884.04 秒 |
| PG/openGauss 结构、用户组、22→25 升级、失败回滚、原生备份恢复与批量事务 | 10 passed；pytest 356.54 秒 |
| PG/openGauss 真实知识库、openGauss Memory、混合存储灾备 | 4 passed；pytest 333.18 秒 |
| 成对 wheel 在干净 Python 3.12 环境安装 | 通过；版本、迁移发现、SDK SQL 资源和前端资源均验证 |
| wheel 与当前 SQL 源码的一致性 | baseline22 和 023–025 共四份 SQL 的 SHA-256 全部一致 |

结构对照比较列类型/空值约束、约束、索引、默认值、序列边界及种子数据；不要求升级库与新库的物理列顺序一致，权限 JSON 数组按集合比较。

完整质量门使用 pytest-testmon 记录完整覆盖信息，marker 为 not live，不使用 --ignore 或跳过提交 hook。外部 IM 和长期压力测试不在本轮验证范围。新应用镜像未部署到现有服务。

## 隔离与清理

本轮成功验证共登记 16 个临时数据库，按名称/OID 归属核验清理，均已不存在。共享 plat、持久 octop 的结构和扩展目录、本地 PG 的 provider/settings/channels 哈希，以及两端数据库目录均核对未变化。provider 凭证副本和临时业务目录已删除。

原主目录三份 Git 忽略的旧部署前缀测试引用官方同步后已移除的实现；已校验 SHA-256 并原样归档到 data/local-test-archive/20261026，没有恢复过时代码或删除测试内容。最终整合工作树的完整检查未排除当前测试模块。

本地脱敏证据：data/opengauss/merge-20261026/942a7c21；质量门：data/opengauss/quality-merge-20261026；安装包验证：data/opengauss/wheel-dist-20261026。测试库归属记录、清理审计、JUnit XML 和安装验证 JSON 保留，凭证不进入 Git。

部署和离线安装说明见 ../../docker/opengauss/README.md。
