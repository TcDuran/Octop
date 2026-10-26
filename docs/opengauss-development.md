# openGauss 独立开发工作区与官方同步方案

更新日期：2026-10-26。当前阶段：v1.0.3b1 整合、实机回归和成对 wheel 安装验证已完成；按本地 release 流程合回 main。最新结果见 validation/opengauss-integration-20261026.md，部署说明见 docker/opengauss/README.md。

最新供应商探测、依赖修复和真实业务结果见 [2026-10-23 验证记录](validation/opengauss-20261023-live.md)。本文中的 2026-10-22 验证数字作为历史证据保留。

2026-10-26 架构确认：用户要求保留原有知识库 SQLite sidecar，只将业务元数据适配到 openGauss。新增 DataVec 存储接入已撤回，部署无需安装向量扩展。见[撤回及复验记录](validation/opengauss-retained-sidecar-20261026.md)。

## 1. 工作区基线

| 项目 | 当前值 |
| --- | --- |
| 原工作区 | `/Users/guixiaolong/Environment/octop` |
| 新工作区 | `/Users/guixiaolong/Environment/octop-opengauss` |
| 开发分支 | `feature/opengauss-support` |
| fork 基线 | `cc623be1af9aea6a550ecf622a1a59508c46de1f` |
| 已集成官方基线 | 本地 `upstream/main`：`dd9dce470844c25d59effb50615bb3c667ea25cd`，为 fork 基线的祖先 |
| Python | 新 worktree 自己的 `.venv`，通过 `uv sync --frozen` 安装 |
| 运行时 | 新 worktree 的 `data/opengauss/home` |
| 后端地址 | `127.0.0.1:18088`；独立 Compose 服务已完成首次启动及重启验收 |
| 应用容器 / Compose 项目 | `octop-opengauss`；使用独立 `docker/docker-compose.opengauss.yml` |
| 应用镜像 / Docker 数据 | `octop-opengauss:dev` / 本 worktree 的 `data/opengauss/docker-home` |

原始适配基于 cc623be1 创建独立工作树，保留当时已有 PLAT、自动化分析及部署路径能力。2026-10-26 当前 fork 已有 develop；通过 feature/opengauss-integration-20261026 工作树整合，保留功能分支提交与官方同步历史。主目录现有 Docker/CPU 兼容改动单独提交，不混入原始适配提交。

worktree 隔离文件、索引和 HEAD，但 Git 对象、远端、分支引用和通常的仓库配置共享。不要在新 worktree 执行仓库级 remote/config 改写并误以为只影响自己；不要同时在两个 worktree 检出同一分支。

## 2. 数据库与本地配置

已在获准的目标 openGauss 6.0.5 实例上，以 template0、PG 兼容模式新建独立 `octop` 库，由现有 plat 角色拥有。建库前检查同名库不存在；没有使用原 plat 库作为模板，没有创建角色或修改原业务对象。

- `octop` 是本 worktree 的持久开发库；截至 2026-10-22，库已创建但尚无 Octop 表或 `_schema_version`，首次应用启动将安装适配后的 baseline、兼容函数、记忆表和检查点表。
- `data/opengauss/database.json` 记录数据库 OID、创建时间、worktree、基线和用途；不保存密码。重连、写入、未来清理前应核验库名及 OID。数据库名或 OID 不匹配时停止操作，不能接管同名库。
- `.env.opengauss` 为 Git 忽略的本地凭证文件，权限 0600；同时设置独立运行目录、虚拟环境、地址与端口。
- 应用配置明确指定 `OCTOP_DATABASE_DRIVER=opengauss`，代码提供显式 openGauss driver 和连接池方言。
- 不使用 JDBC 的 stringtype 查询参数代替 psycopg 配置。后续参数绑定按真实业务回归验证。
- 新库仍位于同一数据库实例，CPU、存储和连接资源并未隔离；验证按有限连接、超时和合成数据执行，不做未经约定的压力测试。

准备好的环境可在子 shell 中读取，避免污染原项目 shell：

```bash
cd /Users/guixiaolong/Environment/octop-opengauss
( source .env.opengauss; uv run --frozen python -c 'import octop; print(octop.__file__)' )
```

当前代码已支持在该隔离环境初始化和运行。不复制原项目 config.json、用户数据、令牌、Agent 工作区或后台服务配置。

### 端口与容器隔离

现有应用容器 `octop` 使用 8088；独立服务只使用 `docker/docker-compose.opengauss.yml`，固定容器名和 Compose 项目名为 `octop-opengauss`，镜像为 `octop-opengauss:dev`，仅映射 `127.0.0.1:18088:18088`。不能直接使用默认 Compose，否则会复用 `octop` 容器名、`octop:latest` 镜像名和默认数据路径。

Docker 数据挂载到本 worktree 的 `data/opengauss/docker-home`；原生进程仍用 `data/opengauss/home`。二者均连接独立 octop 开发库，不同时启动。启动前检查 18088 未被占用，不自动停止其他服务。

知识库混合存储必须同时保留两部分：openGauss 中的知识库/文档元数据，以及 `OCTOP_HOME/knowledge/` 下的原文和 `index.sqlite`。现有 Compose 挂载整个 OCTOP_HOME，覆盖这部分文件。完整灾备应使用包含 knowledge 的系统备份；单独 gs_dump 只覆盖数据库，不能恢复 SQLite 向量或原文文件。

核对初始化最终结构的临时 PostgreSQL 容器 `octop-opengauss-baseline-pg` 曾使用本机端口 49323；该对照容器已在验收结束后删除。

上述应用 Compose 已通过配置校验，镜像 `octop-opengauss:dev` 已构建；应用容器已启动并通过健康检查。复现启动命令：

```bash
( source .env.opengauss; docker compose -f docker/docker-compose.opengauss.yml up -d --build )
```

### 开发库与销毁式测试库分离

现有 PG 集成测试读取 `OCTOP_TEST_DATABASE_URL` 并执行 `DROP SCHEMA public CASCADE`。此变量既不能指向 plat，也不能指向持久开发库 octop；本地环境文件清除此变量，不把开发连接自动映射为测试连接。

每轮集成测试、升级或恢复演练应新建带随机后缀的 `octop_verify_*` 库，登记 OID，确认写入目标后运行，并在 finally 中关闭连接、清理本轮创建的库。恢复演练使用另一空库。无法确认归属或仍有未知连接时，不强杀其他会话、不删除库。

先前用户要求验证后清除的临时库已全部清除；本次新建的 octop 按最新指示保留供独立开发，临时验证库仍按轮清理。结束整个适配工作区时再按归属记录清理开发库，不能按名称前缀批量删除数据库。

## 3. 适配实现与边界

具体 SQL 问题、已验证能力和剩余边界见 [兼容评估](opengauss-compatibility-assessment.md) 与 [实测记录](validation/opengauss-20261022/README.md)。

| 阶段 | 实现范围 | 当前证据 |
| --- | --- | --- |
| 1. 控制面 | 显式 driver、独立完整 baseline、有限兼容函数、启动初始化接入 | 隔离库初始化、重启、CRUD/约束和 SQLite/PG 回归通过 |
| 2. 运行期 SQL | 按仓储语义适配 UPSERT；SSO、多唯一键、条件更新使用精确事务处理 | openGauss 控制面集成和事务用例通过 |
| 3. 记忆与检查点 | 锁定的 octop-memory 源码补丁、openGauss 后端和 saver | CRUD、namespace、维护清理、interrupt/reopen、历史读取及真实 LLM 提取/重启续聊通过 |
| 4. 运维与升级 | 原生 gs_dump/gs_restore、归档 driver 校验、baseline 后增量迁移 | 数据库和系统归档恢复通过；尚无已发布版本间的正式升级迁移 |

差异集中在 infra/db、记忆接入和备份模块；不改业务 API、前端或无关业务。对确需特殊 SQL 的仓储保留少量显式方言分支，不做全局正则 SQL 翻译、不做运行期 monkeypatch、不重构官方目录。

首次部署安装保留的 baseline22，再执行 openGauss 专用 023–025，将结构对齐当前控制面 v25。baseline22 已含 fork 的自动化分析表，025 保留该表和数据；023 同时补齐官方 v22 新增的 skill_packages.shared。不移植 PG 历史 001–022 执行链，也不只改版本号绕过缺失结构。memory 和 checkpoint 各自维护版本，不能共用控制面水位。

octop-memory 的正式改动需在其源码中维护，经验证后通过固定发布版本或不可变 Git commit 引用交付，并更新 uv.lock。依赖开发 checkout 放在本 worktree 的忽略运行目录或另建独立 worktree；不可提交机器绝对路径，不可编辑 site-packages 作为交付。优先向依赖上游贡献可独立合并的改动，尚未获接收时保留最小补丁及基线说明。

## 4. 后续同步官方代码

保留现有 origin（用户 fork）、upstream（官方）与 gitlab 远端关系。本轮先合入 develop，再按用户要求经 release/1.0.3b1-opengauss 合入本地 main，并同步 develop 保持祖先关系；不 push、打 tag 或重启部署。2026-10-22 记录的官方 SHA 仅是当时的本地引用，不能视为此刻远端最新版本。

原始适配已提交到 feature/opengauss-support；当前整合通过独立分支保留两个父提交，新增迁移和回归结果单独记录在 validation/opengauss-integration-20261026.md。提交前不得包含本地环境文件、业务数据或测试 provider 凭证。

同步时使用以下流程，不直接在部署 main 上拉取或变基：

1. 在适配改动已提交、工作区干净后 fetch upstream；只更新远端引用，不在原 main 自动合并。
2. 从当前 fork 集成基线创建 `integration/upstream-<date>` 分支及独立 worktree，merge 官方目标发布 tag 或明确 commit，保留祖先关系，不 squash 整个官方历史、不强推已共享分支。
3. 核查官方变化对配置、连接池、仓储、迁移、memory/checkpoint 与备份的影响，解决冲突并运行回归。当前 fork 的功能集成目标是 develop；生产 main 按 release 或 hotfix 流程推进，不直接覆盖 main。
4. 适配尚未合入 fork 时，将已验证的同步集成结果 merge 到本特性分支，再验证；适配已合入 fork 时，直接在同步分支解决差异并走审核流程。
5. 审核并合入后再同步部署分支；不覆盖 main、不把本地配置和运行数据带入合并。

特别核查官方新迁移：不能只更新代码后沿用旧 openGauss baseline。已发布 baseline 保持不变，为新增表/列/索引/种子编写对应 openGauss 增量脚本；尚未发布时可按仓库规则折叠当前未发布版本，但必须考虑已有本地开发库会跳过同版本变化，必要时重建本工作区的可丢弃测试库验证。不得为方便同步改写已应用迁移的历史语义。

官方升级 memory/checkpoint 时需同时核对依赖补丁、DDL、水位、读写 SQL 与序列化格式；先在临时库演练升级及恢复，再更新锁文件。能自动 merge 仅说明文本无冲突，不能证明数据库兼容。

## 5. 2026-10-22 验证快照

2026-10-26 远端恢复后的最新隔离复验见 [恢复复验记录](validation/opengauss-recovery-20261026.md)。下列内容保留为 2026-10-22 历史快照，不能替代该次复验结果。

- worktree 分支与基线、原目录未提交改动保留、独立虚拟环境及 editable 导入位置：已核查。
- 独立 octop 库按数据库名/OID核验，PG 兼容模式；2026-10-22 只读核验确认 OID `88543`。首次启动后有 33 张 public 表、102 个索引，`_schema_version=22`；未创建管理员，等待设置向导。
- 本地凭证和运行目录被 Git 忽略；正式评估、方案和前序验证资料通过限定 .gitignore 例外保留，未整体开放 docs/tests 目录。
- 最近 `make all PYTEST_JOBS=4` 的格式、Ruff、mypy 均通过；pytest 结果 **5116 passed、52 skipped、1 failed、98 warnings**。唯一失败是 `test_backup_deletion_excludes_maintenance_and_restore` 的并行线程启动 5 秒超时，单独串行重跑 **1 passed**。之前 pool/snapshot 修复后的归档与 checkpoint 历史定向回归为 3 passed。
- openGauss 隔离库控制面、记忆 CRUD/namespace、维护与 Agent 清理、人工中断恢复、HTTP/WebSocket 对话、服务重启读取、legacy pending sends/delta history、原生备份恢复均有通过记录。未测试真实 LLM 或外部 IM。
- Docker 镜像已构建（linux/amd64，客户端 6.0.5）；独立 Compose 应用 `octop-opengauss` 已在 `127.0.0.1:18088` 启动，首次初始化与容器重启后的 `/api/health` 均返回 200，Docker health 为 healthy。临时 PostgreSQL 对照容器已移除；按登记名和 OID 清理本轮残留验证库，持久开发库 `octop` 保留。系统归档最近一轮为 3 passed。
- 三份前序验证 Python 脚本的 Ruff 检查及格式检查通过，`git diff --check` 通过；格式化没有产生额外源码改动。此为当前 fork 的工作区基线检查，不代表正式 openGauss 业务回归已经通过。
- 真实 LLM、外部 IM、多进程长期并发/压力测试和 vendor 依赖的外部分发方案仍待后续验证或决策。

## 6. 2026-10-23 补充结果

本轮只读复用本地 PG 的 provider 配置，真实 chat/embedding 探测通过。真实 Harness 的 HTTP/WebSocket 聊天、memory_save 落库、辅助模型提取/提升/页面生成及服务重启续聊完成；最终 live 回归为 1 passed，目标库连接池引用归零并自动删库。

新增修复包括依赖后台 vacuum 的 catalog 查询替代，以及 dashboard 记忆缓存替换/淘汰/失效时释放其 Memory 资源。独立容器更新到 1.0.2+opengauss.2，保持 18088 和 octop-opengauss 命名；未改动原 octop 容器。完整质量门和容器最终证据以 [本日验证记录](validation/opengauss-20261023-live.md) 为准。

2026-10-26 恢复复验登记的 32 个临时库均已核验不存在；持久开发库 octop（OID 88543）保留。控制面/备份、真实 LLM 业务、PG/OG 远程 embedding 知识库和三组有界并发均通过；`CREATE EXTENSION datavec` 因远端缺少 `datavec--0.7.2.sql` 受限，但 openGauss 6.0.5 的 DataVec 内核能力已有独立库建表、DML 和基础距离计算证据，不能把扩展目录问题当成内核向量功能缺失。临时 provider 凭证快照和各轮合成测试 home 已删除，保留脱敏结果与数据库归属审计。IM、长期压力和外部 wheel 分发仍不在已通过范围。
