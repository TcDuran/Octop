# AJ Octop 本次部署操作记录

本页是本次生产实施的时间线和结果记录。执行一项操作后立即填写，不预填为成功。口令、令牌、完整 `.env`、带凭据的连接串及敏感命令输出不得写入此页。

## 变更边界

- 目标主机：`10.51.49.8`，Kylin Linux Advanced Server V10，`x86_64`。
- 公开入口候选：`http://211.137.76.3:8081/octop`；TLS 终止位置待确认。
- 新增内容：Octop 应用容器、独立 Octop 主机数据目录、openGauss 中独立 `octop` 数据库及非超级用户 `octop`。
- 复用内容：现有 openGauss 容器 `opengauss_test`、现有 Nginx 容器 `nginx`。
- 禁止触碰：既有 `plat` 数据库及其表/数据、openGauss 数据目录、现有容器定义和其他业务容器。
- 回滚原则：先撤销新增 Nginx 路由并停止新 Octop 容器；保留新数据库与数据目录用于调查。禁止删除卷或操作现有数据库。

## 操作前检查

| 检查项 | 结果 | 时间 / 操作人 | 证据或备注 |
| --- | --- | --- | --- |
| Spug 登录和主机身份复核 | 已完成，只读 | 2026-10-28 / Codex | 主机 `10.51.49.8` |
| OS、架构、Docker、磁盘空间 | 已完成，只读 | 2026-10-28 / Codex | Kylin V10，x86_64，Docker 26.0.2，`/APP` 可用约 157 GiB |
| openGauss / Nginx 容器、网络和挂载 | 已完成，只读 | 2026-10-28 / Codex | 详见 `AJ_DEPLOYMENT_RUNBOOK.md`；未执行 DDL/DML |
| 运行中 openGauss build 版本 | 已确认 | 2026-10-28 / Codex | `gsql` 与 `gs_dump` 均为 6.0.5 build `5ba692ab` |
| `gs_dump --version`（以 `omm` 身份） | 已完成，只读 | 2026-10-28 / Codex | `gs_dump (openGauss 6.0.5 build 5ba692ab)` |
| `octop` 库和角色存在性及权限查询 | 已完成，只读 | 2026-10-28 / Codex | 库/角色均不存在；`omm` 有 superuser/createdb/createrole，`plat` 三项均否 |
| 数据库/TLS/外部 URL 最终确认 | 部分确认 | 2026-10-28 / Codex | 候选 URL `http://211.137.76.3:8081/octop`；TLS 终止仍待确认 |
| 正式镜像提交、镜像摘要、品牌和子路径验收 | 已完成 | 2026-10-28 / Codex | 主分支 `ee6f5c7d`；镜像 `octop-opengauss:main-ee6f5c7d`，ID `sha256:906f34bdcb57f3356da1dc0b7d1c823f4f2e410c24ca8f229f302a39b1a6a576`；品牌、`/octop/` 和 openGauss 客户端已核对 |
| 生产操作审批 | 待补 |  |  |

## 数据库与账号

此节按每条语句分别记录。执行身份、命令摘要和结果必须可追溯；禁止记录口令。

| 序号 | 操作 | 执行前状态 | 执行身份 | 时间 / 操作人 | 结果 / 证据 | 回滚或后续动作 |
| --- | --- | --- | --- | --- | --- | --- |
| DB-RO-01 | 只读查询 `gs_dump --version` | 已完成 | `omm` | 2026-10-28 / Codex | openGauss 6.0.5 build `5ba692ab` | 不涉及写入 |
| DB-RO-02 | 只读检查 `octop` 数据库是否存在 | 不存在 | `omm` | 2026-10-28 / Codex | 查询 0 rows | 不涉及写入 |
| DB-RO-03 | 只读检查 `omm`、`plat`、`octop` 角色属性 | `octop` 不存在 | `omm` | 2026-10-28 / Codex | `omm` 为 superuser/createdb/createrole；`plat` 均否 | 不涉及写入 |
| DB-DDL-01 | 创建最小权限 `octop` 角色 | 首次语法和空口令尝试均失败，后续成功 | `omm` | 2026-10-28 / Codex | 使用该版本支持的 `NOLOGIN NOCREATEDB NOCREATEROLE` 创建；角色无超级用户、建库、建角色权限 | 已完成；登录在口令设置成功后启用 |
| DB-DDL-02 | 创建 `octop` 数据库并指定 owner | 数据库不存在 | `omm` | 2026-10-28 / Codex | `CREATE DATABASE` 成功；只创建独立空库 `octop`，owner 为 `octop` | 已完成；不涉及 `plat` 库对象 |
| DB-RO-04 | 复核库 owner、角色属性及 `plat` 角色属性 | 已完成 | `omm` | 2026-10-28 / Codex | `octop` owner=`octop`；角色非 superuser/createdb/createrole，LOGIN 已启用且口令非空；`plat` 角色属性与此前只读检查一致 | 只核对角色属性和新库 owner；未对 `plat` 执行 DDL/DML |
| DB-SEC-01 | 设置口令并启用 `octop` 登录 | 初次设置因超长及复杂度不足失败，失败后已禁用登录 | `omm` | 2026-10-28 / Codex | 最终 32 字符随机口令设置成功，随后启用 LOGIN；使用 `gsql -2` 密码管道验证，`octop` 成功连接 `octop` 库；口令只存于 `.env`，未写入记录 | 已完成；实例认证配置未修改，MD5 风险需纳入后续安全评估 |

执行结果更正（2026-10-28）：最初创建角色的尝试先因 openGauss 不支持 PostgreSQL 的 `NOSUPERUSER` 选项失败，随后因空口令失败；最终改用版本支持的角色属性并提供非空口令后，角色创建成功。`CREATE DATABASE octop OWNER octop` 随后成功。口令设置过程中，超长口令和仅含十六进制字符的口令均被 openGauss 拒绝；由于该版本 `gsql` 在 SQL 报错时仍可能返回成功退出码，不能只依赖 shell `$?` 判断。失败尝试后均将角色设回 `NOLOGIN`；最终口令满足长度和复杂度要求，设置语句输出成功后才启用 LOGIN。

当前核验状态：`octop` 库 owner 为 `octop`；角色没有超级用户、建库或建角色权限，LOGIN 已启用且口令字段非空；`plat` 角色属性与部署前只读检查一致。使用 `.env` 中口令并通过 openGauss `gsql -2` 管道认证，成功连接新库，查询结果为 `octop|octop`。此前的探测采用不适用于本次 `gsql` 的 `PGPASSWORD`/PostgreSQL 风格方式，未能传入口令；失败反映验证命令不兼容，不代表凭据错误。`/APP/DockerData/octop-aj/config` 权限为 `0700`，`.env` 为 `0600`，目录内无残留临时口令文件。`password_encryption_type=0`，来源为 `/var/lib/opengauss/data/postgresql.conf` 第 913 行（`password_encryption_type = 0`）；`octop` 摘要类型为 `md5`。openGauss 输出 `The encrypted password contains MD5 ciphertext, which is not secure.`；本次没有修改实例认证配置。未在 `plat` 库的表或数据上执行 Octop 初始化、migration 或业务 DML；角色及建库语句虽通过连接 `plat` 的管理会话执行，但目标分别是集群角色和独立 `octop` 库。未启动 Octop 容器，也未修改 Nginx。

应用驱动验证补充（2026-10-28）：远端 openGauss 容器内没有 `psycopg` Python 包；本地现有 `octop-opengauss-aj:review-20261028` 镜像来自含未提交改动的工作区，没有用于生产库测试。另构建仅含 `psycopg[binary]==3.3.4` 的小型测试镜像，从 Spug 文件管理器上传到远端 `/tmp`；远端归档 SHA-256 与本地一致。测试镜像在现有 `bridge` 网络以 `--rm` 运行，无端口映射和数据卷，通过受限 `.env` 连接新库并执行只读 `SELECT current_user, current_database()`，返回 `octop|octop`。未启动 Octop，也未触发初始化或 migration。验证后删除远端归档、临时镜像，并删除本地临时 psycopg 测试镜像；独立 Octop smoke 构建镜像仍只保留在本地。

Smoke 镜像构建补充（2026-10-28）：从干净提交 `1c009695`（`git archive`）本地构建 `linux/amd64` 镜像 `octop-opengauss:db-smoke-1c009695`，image ID `sha256:729f3a3c33b0053df8c3021dfc98d28552fc2608973ad0a5bad3f19f7a0e808f`，大小约 3.13 GB。构建目标 `opengauss-runtime`，前端 base path `/octop/`，未安装本地 embedding。镜像内 `psycopg` 为 3.3.4，`gs_dump`/`gs_restore` 为 openGauss 6.0.5 build `5ba692ab`。构建仅使用本地 `--load`，没有 `--push`，没有启动容器或 openGauss 服务。该完整 Octop smoke 镜像仍只在本地，未传远端；远端驱动验证使用另一个仅含 psycopg 的小型临时测试镜像，完成后已清理。完整镜像仅作测试，不是发布镜像。

完整应用镜像 review 构建补充（2026-10-28）：使用当前工作区构建 `linux/amd64` 镜像 `octop-opengauss:aj-review-20261028`，image ID `sha256:20bd8145bd26680c5690811b4708b1a84d71fc2772f75138fb1759aeddc09abc`，大小约 3.14 GB；与先前本地 review 镜像内容相同。构建目标 `opengauss-runtime`，`VITE_BASE_PATH=/octop/`，`INSTALL_LOCAL_EMBEDDING=false`。验证镜像内 `psycopg 3.3.4`、Octop Python 模块导入、`gs_dump` 6.0.5 客户端均成功。仅本地 `--load`，未推送、未传远端、未启动应用。当前工作区有未提交改动，此镜像是审核候选，不作为正式发布镜像。

推荐的只读查询模板（先通过 Spug 以 `omm` 进入容器执行；完整输出检查敏感信息后再摘录）：

```sh
docker exec opengauss_test su - omm -c 'gs_dump --version'
docker exec -it opengauss_test su - omm -c 'gsql -p 5432 -d plat'
```

进入 `gsql` 后只读检查：

```sql
SELECT datname FROM pg_database WHERE datname = 'octop';
SELECT rolname, rolsuper, rolcreatedb, rolcreaterole
FROM pg_roles WHERE rolname IN ('omm', 'plat', 'octop');
```

部署前只读检查确认目标库和角色均不存在，并确认只有 `omm` 可执行建库/建角色。其后已由 `omm` 创建独立 `octop` 角色和数据库；不要重复执行创建语句，也不要使用现有 `plat` 账号作为 Octop 的运行账号。

## 应用与入口变更

| 序号 | 操作 | 执行前状态 | 时间 / 操作人 | 结果 / 证据 | 回滚或后续动作 |
| --- | --- | --- | --- | --- | --- |
| APP-01 | 创建 `/APP/DockerData/octop-aj/` 和独立数据目录 | 已完成 | 2026-10-28 / Codex | `data` 为 root:root、`0700`；镜像默认以 root 运行 | 只操作本次专用路径 |
| APP-02 | 安装单应用 Compose 与权限受限 `.env` | 已完成 | 2026-10-28 / Codex | Compose 及 `.env` 均为 `0600`；保留既有数据库口令，新增配置值；旧 `.env` 已备份 | 备份位于 `config/.env.before-prepare-20261028T155427`，权限 `0600` |
| APP-03 | `docker compose config -q` 校验 | 已完成 | 2026-10-28 / Codex | 渲染配置校验通过；实际 Compose 临时驱动探针只读连接新库成功 | 未打印密钥或完整渲染配置，未启动正式应用 |
| APP-04 | 启动 `octop-opengauss-aj` | 未执行 |  |  | 停止新容器，保留数据和数据库 |
| APP-05 | 健康、登录、数据持久化验收 | 未执行 |  |  | 失败则撤销入口并停止新容器 |
| NGINX-01 | 备份并追加 `/octop/` 路由 | 未执行 |  |  | 恢复本次修改前配置 |
| NGINX-02 | `nginx -t` 后 reload 和外部验收 | 未执行 |  |  | 先恢复配置、测试，再 reload |

## 备份恢复与最终验收

| 项目 | 结果 | 时间 / 操作人 | 证据或归档摘要 |
| --- | --- | --- | --- |
| 新 `octop` 库备份 | 待执行 |  |  |
| Octop 文件数据备份 | 待执行 |  |  |
| 隔离环境恢复演练 | 待执行 |  |  |
| `/octop/`、API、OIDC、Agent Memory、知识库验收 | 待执行 |  |  |
| 完整业务/MCP 测试（按已配置连接器执行） | 待执行 |  |  |
| 生产变更完成确认 | 待执行 |  |  |

## 正式镜像构建

2026-10-28 在已合并并推送的 `main` 提交 `ee6f5c7d3e88b6830c17646e63a6706944886881` 上构建 `octop-opengauss:main-ee6f5c7d`。构建目标为 `linux/amd64`、`opengauss-runtime`，参数为 `VITE_BASE_PATH=/octop/`、`INSTALL_LOCAL_EMBEDDING=false`。镜像 ID 为 `sha256:906f34bdcb57f3356da1dc0b7d1c823f4f2e410c24ca8f229f302a39b1a6a576`，大小 `3,138,821,813` bytes。

临时容器核对了 Octop 导入、psycopg 3.3.4、`gs_dump`/`gs_restore` 6.0.5 build `5ba692ab`、`/octop/` 资源和未安装 `fastembed`；核对容器已自动删除。镜像只加载到本地，未推送仓库，暂无 RepoDigest。详细报告见 [FORMAL_IMAGE_BUILD_REPORT.md](FORMAL_IMAGE_BUILD_REPORT.md)。

## 正式镜像离线交付

| 序号 | 操作 | 时间 / 操作人 | 结果 / 证据 |
| --- | --- | --- | --- |
| GIT-01 | 通过本地 Clash 代理推送正式构建报告 | 2026-10-28 / Codex | `2313d69c` 已推送至 GitHub `origin/main` |
| IMAGE-01 | 导出正式镜像、压缩并生成校验清单 | 2026-10-28 / Codex | 归档 `1,213,336,901` bytes；SHA-256 为 `99bd2891371da4e5ef8b1ad165afbc1cfe57421c07acf6c4c7c5dadd98737652` |
| IMAGE-02 | 建立独立发布目录并通过 Spug 上传文件 | 2026-10-28 / Codex | 新目录 `/APP/DockerData/octop-aj/releases/main-ee6f5c7d-20261028/` 权限 `0700`；37 个 32 MiB 以内的分片及两份校验清单上传完成 |
| IMAGE-03 | 远端逐片校验、合并、完整归档校验 | 2026-10-28 / Codex | 37 片 SHA-256 全部通过；完整归档与本地 SHA-256 一致；`gzip -t` 通过；归档权限 `0600` |
| IMAGE-04 | 导入镜像 | 2026-10-28 / Codex | 再次校验归档后执行 `docker load`；image ID 与本地正式镜像完全一致；正式应用和 Nginx 接入仍未执行 |

归档文件位于上述发布目录，文件名为 `octop-opengauss-main-ee6f5c7d-20261028.tar.gz`。分片与校验清单暂保留。完整步骤和校验值见 [FORMAL_IMAGE_BUILD_REPORT.md](FORMAL_IMAGE_BUILD_REPORT.md)。

## 启动前配置与连接核验

2026-10-28 完成以下步骤，尚未启动正式 Octop 应用：

1. 上传修订后的 Compose 到发布目录，SHA-256 为 `be6747c88789c20c91cf26284e48151e01c41d7559dd0304878adb2a7c81f701`，然后安装到 `/APP/DockerData/octop-aj/docker-compose.opengauss-aj.yml`。仅将网络声明改为 `network_mode: bridge`；默认 bridge 不支持 Compose 服务别名的问题已在本地 Docker 复现。应用镜像无需重建。
2. 先以 `0600` 备份旧 `.env`，保留原数据库口令，原子更新非敏感配置值；`OCTOP_IMAGE` 固定为正式 image ID，数据库端点为 `172.17.0.1:15434`、库和账号均为 `octop`，公开入口配置为 `http://211.137.76.3:8081/octop`。
3. 创建 `/APP/DockerData/octop-aj/data`，root:root、`0700`；现有 `config` 目录为 `0700`，`.env`、备份和 Compose 为 `0600`。
4. 执行 `docker compose --env-file config/.env -f docker-compose.opengauss-aj.yml config -q`，并在内存中核对渲染配置，只输出断言结果；镜像 ID、`pull_policy: never`、网络、数据库、入口、`172.17.0.1:18088` 绑定和数据卷均正确。
5. 使用该 Compose 运行 `--rm --no-deps -T --entrypoint python` 临时容器 `octop-aj-preflight-20261028`，执行 `BEGIN READ ONLY` 下的身份、版本、表数量和角色属性查询。结果为 `octop|octop`、openGauss 6.0.5 build `5ba692ab`、`public` 基础表数量 0、无 superuser/createdb/createrole 权限；事务已回滚，容器已自动删除。未执行 Octop 入口或 migrations。
6. 复核 `docker ps -a --filter name=octop` 没有残留 Octop 容器，`18088` 没有监听。现有 `opengauss_test` 的启动时间仍为 `2026-10-09T06:51:35.736230935Z`，Nginx 仍为 `2026-10-12T01:55:52.518555208Z`，均未重启。

本次准备脚本 `octop-aj-prepare-20261028.py` 与只读探针 `octop-aj-db-readonly-20261028.py` 保存在远端该发布目录；两者不包含凭据，SHA-256 分别为 `6d5941c8fbc3386ff829dd5ebcdb097c8109cdcef0b2bf9c913713b869c15127`、`1302cef40ddb5baa5f04323f3b904d051078114573c03e8d419bf9834df362f2`。准备脚本要求数据目录尚不存在，禁止在当前环境重复执行。后续步骤是备份新库后启动 Octop、检查初始化，再接入 Nginx；这些步骤本阶段未执行。
