# AJ Octop 本次部署操作记录

本页是本次生产实施的时间线和结果记录。执行一项操作后立即填写，不预填为成功。口令、令牌、完整 `.env`、带凭据的连接串及敏感命令输出不得写入此页。

截至 2026-10-29：AJ 已更新为 `main-b46b9722` 白标镜像，容器 healthy，公开“PLAT 助手”登录页、资源和数据库连接核验通过，schema 25（36 张表）保持不变。线上已存在 1 个用户/管理员，health 显示 1 个运行中的 Agent；本轮未创建或重置账号。登录后的完整业务和隔离恢复演练仍待验收。更新过程见文末“2026-10-29 白标镜像更新”；下文早期向导状态和“未启动”均指该操作当时状态。账号当前已获 `SYSADMIN`，专家启动与 Memory 存储检查已通过，详见文末授权记录；早期“无管理权限”描述仅指当时状态。

## 变更边界

- 目标主机：`10.51.49.8`，Kylin Linux Advanced Server V10，`x86_64`。
- 已验收公开入口：`http://211.137.76.3:8081/octop/`；TLS 终止位置待确认。
- 新增内容：Octop 应用容器、独立 Octop 主机数据目录、openGauss 中独立 `octop` 数据库及同名账号（初建为普通角色，2026-10-29 已按用户授权授予 `SYSADMIN`，见文末记录）。
- 复用内容：现有 openGauss 容器 `opengauss_test`、现有 Nginx 容器 `nginx`。
- 禁止触碰：既有 `plat` 数据库及其表/数据、openGauss 数据目录、现有容器定义和其他业务容器。
- 回滚原则：首次部署可撤销新增路由并停止新增容器；版本更新恢复旧 `.env` 和 Compose，以旧 image ID 重建应用，保留已使用的路由。保留数据库与数据目录用于调查，禁止删除卷或操作既有业务库。

## 操作前检查

| 检查项 | 结果 | 时间 / 操作人 | 证据或备注 |
| --- | --- | --- | --- |
| Spug 登录和主机身份复核 | 已完成，只读 | 2026-10-28 / Codex | 主机 `10.51.49.8` |
| OS、架构、Docker、磁盘空间 | 已完成，只读 | 2026-10-28 / Codex | Kylin V10，x86_64，Docker 26.0.2，`/APP` 可用约 157 GiB |
| openGauss / Nginx 容器、网络和挂载 | 已完成，只读 | 2026-10-28 / Codex | 详见 `AJ_DEPLOYMENT_RUNBOOK.md`；未执行 DDL/DML |
| 运行中 openGauss build 版本 | 已确认 | 2026-10-28 / Codex | `gsql` 与 `gs_dump` 均为 6.0.5 build `5ba692ab` |
| `gs_dump --version`（以 `omm` 身份） | 已完成，只读 | 2026-10-28 / Codex | `gs_dump (openGauss 6.0.5 build 5ba692ab)` |
| `octop` 库和角色存在性及权限查询 | 已完成，只读 | 2026-10-28 / Codex | 库/角色均不存在；`omm` 有 superuser/createdb/createrole，`plat` 三项均否 |
| 数据库/TLS/外部 URL 最终确认 | 部分确认 | 2026-10-28 / Codex | 公开 URL 后续已确认并通过入口验收；TLS 终止仍待确认 |
| 正式镜像提交、镜像摘要、品牌和子路径验收 | 旧镜像已完成；新品牌待发布 | 2026-10-28 / Codex；2026-10-29 文档核对 | 现网镜像基于 `ee6f5c7d`，镜像 `octop-opengauss:main-ee6f5c7d`，ID `sha256:906f34bdcb57f3356da1dc0b7d1c823f4f2e410c24ca8f229f302a39b1a6a576`；`/octop/` 和 openGauss 客户端已核对。白标提交 `868e7d15` 已在 `main`，尚未构建新镜像 |
| 生产操作审批 | 已获用户分阶段确认 | 2026-10-28 / 用户 | 会话中分别确认数据库/应用实施及 Nginx 追加路由和 reload；未把基础入口确认扩展为完整业务验收 |

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
| DB-ACL-01 | 仅在新库授予运行账号 schema 权限 | `public` owner=`omm`，`octop` USAGE=t、CREATE=f；失败初始化后仍为 0 张表 | `omm`，连接 `octop` 库 | 2026-10-28 16:30 / Codex | 保存原 ACL 后执行 `GRANT USAGE, CREATE ON SCHEMA public TO octop`，两项复查均为 t；随后初始化成功 | 不改变 schema owner、实例认证或管理权限，不授权给 PUBLIC；原 ACL 保存在发布目录 |

执行结果更正（2026-10-28）：最初创建角色的尝试先因 openGauss 不支持 PostgreSQL 的 `NOSUPERUSER` 选项失败，随后因空口令失败；最终改用版本支持的角色属性并提供非空口令后，角色创建成功。`CREATE DATABASE octop OWNER octop` 随后成功。口令设置过程中，超长口令和仅含十六进制字符的口令均被 openGauss 拒绝；由于该版本 `gsql` 在 SQL 报错时仍可能返回成功退出码，不能只依赖 shell `$?` 判断。失败尝试后均将角色设回 `NOLOGIN`；最终口令满足长度和复杂度要求，设置语句输出成功后才启用 LOGIN。

账号配置阶段核验状态：`octop` 库 owner 为 `octop`；角色没有超级用户、建库或建角色权限，LOGIN 已启用且口令字段非空；`plat` 角色属性与部署前只读检查一致。使用 `.env` 中口令并通过 openGauss `gsql -2` 管道认证，成功连接新库，查询结果为 `octop|octop`。此前的探测采用不适用于本次 `gsql` 的 `PGPASSWORD`/PostgreSQL 风格方式，未能传入口令；失败反映验证命令不兼容，不代表凭据错误。`/APP/DockerData/octop-aj/config` 权限为 `0700`，`.env` 为 `0600`，目录内无残留临时口令文件。`password_encryption_type=0`，来源为 `/var/lib/opengauss/data/postgresql.conf` 第 913 行（`password_encryption_type = 0`）；`octop` 摘要类型为 `md5`。openGauss 输出 `The encrypted password contains MD5 ciphertext, which is not secure.`；本次没有修改实例认证配置。未在 `plat` 库的表或数据上执行 Octop 初始化、migration 或业务 DML；角色及建库语句虽通过连接 `plat` 的管理会话执行，但目标分别是集群角色和独立 `octop` 库。未启动 Octop 容器，也未修改 Nginx。

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
| APP-04 | 启动 `octop-opengauss-aj` | 已完成 | 2026-10-28 / Codex | 首次启动发现账号没有 `public` schema 的 CREATE 权限；仅在独立 `octop` 库授予 USAGE, CREATE 后启动成功，容器运行中 | 回滚时停止新容器，保留数据库和数据目录 |
| APP-05 | 健康、登录、数据持久化验收 | 部分完成 | 2026-10-28 / Codex | health、schema 25、36 张表和受控重启持久化通过；管理员设置和登录待业务方完成 | Nginx 后续操作单独记录于 NGINX-01/02 |
| NGINX-01 | 备份并追加 `/octop/` 路由 | 已完成 | 2026-10-28 17:11 / Codex | 备份目录 `backups/nginx-20261028T171128/`；仅向 `http_aj.conf` 的 80 端口 server 追加带标记的两段 location，其他字节保持一致 | 恢复该目录的 `http_aj.conf`，测试后 reload |
| NGINX-02 | `nginx -t` 后 reload 和外部验收 | 已完成（入口验收） | 2026-10-28 / Codex | 修改前后 `nginx -t` 均通过；执行 `nginx -s reload`；外网向导页面、308 跳转保留参数、11 个资源及 health 通过；原 Spug/OA/BPM 响应未变化，容器未重启 | 管理员、登录和业务功能验收仍待执行 |

## 备份恢复与最终验收

| 项目 | 结果 | 时间 / 操作人 | 证据或归档摘要 |
| --- | --- | --- | --- |
| 新 `octop` 库备份 | 已完成（启动前） | 2026-10-28 / Codex | `/APP/DockerData/octop-aj/backups/prestart-20261028T162340/`；dump 834 bytes，SHA-256 `f698091ffa20d5b4a378cdc2707023a9abd44ac07383c6a6648ca8a5060ae0dc`；仅检查归档列表，未恢复 |
| Octop 文件数据备份 | 已完成（启动前） | 2026-10-28 / Codex | 同一目录保存 `data-before-start.tar.gz`、Compose 和受限配置副本，目录内校验通过；配置文件不进入公开记录 |
| 隔离环境恢复演练 | 待执行 |  |  |
| `/octop/` 与基础 API 入口验收 | 已完成 | 2026-10-28 / Codex | 初始化向导、health、setup/status、308 跳转、11 个页面资源通过；无 token 的 WebSocket 握手返回 403，仅验证入口和拒绝鉴权 |
| 首次管理员、登录/OIDC、聊天/WebSocket、Agent Memory、知识库验收 | 待执行 |  | 需完成管理员及相应模型、embedding、OIDC/连接器配置后再验收；不以基础入口通过代替 |
| 完整业务/MCP 测试（按已配置连接器执行） | 待执行 |  |  |
| 生产变更完成确认 | 待执行 |  |  |

## 正式镜像构建

2026-10-28 在已合并并推送的 `main` 提交 `ee6f5c7d3e88b6830c17646e63a6706944886881` 上构建 `octop-opengauss:main-ee6f5c7d`。构建目标为 `linux/amd64`、`opengauss-runtime`，参数为 `VITE_BASE_PATH=/octop/`、`INSTALL_LOCAL_EMBEDDING=false`。镜像 ID 为 `sha256:906f34bdcb57f3356da1dc0b7d1c823f4f2e410c24ca8f229f302a39b1a6a576`，大小 `3,138,821,813` bytes。

临时容器核对了 Octop 导入、psycopg 3.3.4、`gs_dump`/`gs_restore` 6.0.5 build `5ba692ab`、`/octop/` 资源和未安装 `fastembed`；核对容器已自动删除。镜像构建时加载到本地，随后经离线交付在远端导入并运行；未推送镜像仓库，暂无 RepoDigest。详细报告见 [FORMAL_IMAGE_BUILD_REPORT.md](FORMAL_IMAGE_BUILD_REPORT.md)。

## 正式镜像离线交付

| 序号 | 操作 | 时间 / 操作人 | 结果 / 证据 |
| --- | --- | --- | --- |
| GIT-01 | 通过本地 Clash 代理推送正式构建报告 | 2026-10-28 / Codex | `2313d69c` 已推送至 GitHub `origin/main` |
| IMAGE-01 | 导出正式镜像、压缩并生成校验清单 | 2026-10-28 / Codex | 归档 `1,213,336,901` bytes；SHA-256 为 `99bd2891371da4e5ef8b1ad165afbc1cfe57421c07acf6c4c7c5dadd98737652` |
| IMAGE-02 | 建立独立发布目录并通过 Spug 上传文件 | 2026-10-28 / Codex | 新目录 `/APP/DockerData/octop-aj/releases/main-ee6f5c7d-20261028/` 权限 `0700`；37 个 32 MiB 以内的分片及两份校验清单上传完成 |
| IMAGE-03 | 远端逐片校验、合并、完整归档校验 | 2026-10-28 / Codex | 37 片 SHA-256 全部通过；完整归档与本地 SHA-256 一致；`gzip -t` 通过；归档权限 `0600` |
| IMAGE-04 | 导入镜像 | 2026-10-28 / Codex | 再次校验归档后执行 `docker load`；image ID 与本地正式镜像完全一致；当时尚未启动应用，后续结果见 APP-04 与 NGINX-01/02 |

归档文件位于上述发布目录，文件名为 `octop-opengauss-main-ee6f5c7d-20261028.tar.gz`。分片与校验清单暂保留。完整步骤和校验值见 [FORMAL_IMAGE_BUILD_REPORT.md](FORMAL_IMAGE_BUILD_REPORT.md)。

## 启动前配置与连接核验

2026-10-28 完成以下步骤，正式 Octop 应用已启动并完成基础健康与持久化验证：

1. 上传修订后的 Compose 到发布目录，SHA-256 为 `be6747c88789c20c91cf26284e48151e01c41d7559dd0304878adb2a7c81f701`，然后安装到 `/APP/DockerData/octop-aj/docker-compose.opengauss-aj.yml`。仅将网络声明改为 `network_mode: bridge`；默认 bridge 不支持 Compose 服务别名的问题已在本地 Docker 复现。应用镜像无需重建。
2. 先以 `0600` 备份旧 `.env`，保留原数据库口令，原子更新非敏感配置值；`OCTOP_IMAGE` 固定为正式 image ID，数据库端点为 `172.17.0.1:15434`、库和账号均为 `octop`，公开入口配置为 `http://211.137.76.3:8081/octop`。
3. 创建 `/APP/DockerData/octop-aj/data`，root:root、`0700`；现有 `config` 目录为 `0700`，`.env`、备份和 Compose 为 `0600`。
4. 执行 `docker compose --env-file config/.env -f docker-compose.opengauss-aj.yml config -q`，并在内存中核对渲染配置，只输出断言结果；镜像 ID、`pull_policy: never`、网络、数据库、入口、`172.17.0.1:18088` 绑定和数据卷均正确。
5. 使用该 Compose 运行 `--rm --no-deps -T --entrypoint python` 临时容器 `octop-aj-preflight-20261028`，执行 `BEGIN READ ONLY` 下的身份、版本、表数量和角色属性查询。结果为 `octop|octop`、openGauss 6.0.5 build `5ba692ab`、`public` 基础表数量 0、无 superuser/createdb/createrole 权限；事务已回滚，容器已自动删除。未执行 Octop 入口或 migrations。
6. 预检时没有残留 Octop 容器；随后启动 `octop-opengauss-aj`，仅绑定 `172.17.0.1:18088`。现有 `opengauss_test` 的启动时间仍为 `2026-10-09T06:51:35.736230935Z`，Nginx 仍为 `2026-10-12T01:55:52.518555208Z`，均未重启。

本次准备脚本与只读探针保存在远端该发布目录，均不包含凭据。启动后探针验证 `public._schema_version=25`、baseline marker、36 张表和 0 个管理员，受控重启后 health、schema 和向导文件校验均通过。首次启动因 schema ACL 不足失败，已在新库执行 `GRANT USAGE, CREATE ON SCHEMA public TO octop` 后重试成功。Nginx 接入已完成，首次管理员设置、登录及完整业务验收仍未执行。

品牌发布说明（2026-10-29）：品牌源码提交 `868e7d15` 已推送到 `origin/main`，包含上游吉祥物/默认品牌内容清理和 PLAT 助手品牌资源处理；本次只更新维护文档，未重建或替换现网容器。新镜像发布前必须记录新源码提交、镜像 ID、归档 SHA-256，并重新验证品牌、`/octop/` 子路径、健康检查、schema 与回滚路径。

## Nginx 路由实施与外网验证

2026-10-28 17:11（Asia/Shanghai）在用户审核通过后执行：

1. 核对 `/APP/DockerData/nginx/conf/http_aj.conf` 只有一个 `server`，监听容器 80；确认没有 `/octop` 路由。修改前执行 `docker exec nginx nginx -t`，结果通过。
2. 创建受限目录 `/APP/DockerData/octop-aj/backups/nginx-20261028T171128/`，保存原 `http_aj.conf`、包含主配置和整个 `conf` 目录的 `nginx-config-before.tar.gz`、原入口响应摘要及容器身份、SHA-256 清单。目录 `0700`，新建归档/记录文件 `0600`；原配置副本保留原模式，受目录权限保护。
3. 在原 `location /` 前追加 `BEGIN OCTOP AJ` / `END OCTOP AJ` 标记之间的两个 location；通过去除新增块后的逐字节比较，确认原配置保持一致。`/octop` 以 308 跳转并保留查询参数，`/octop/` 转发到 `http://172.17.0.1:18088/`，启用 WebSocket upgrade 和关闭代理缓冲。
4. 再次执行 `docker exec nginx nginx -t`，通过后执行 `docker exec nginx nginx -s reload`。没有重启或替换 Nginx、openGauss、Octop 容器。
5. 主机经 Nginx 与外网分别验证页面、11 个 HTML 引用资源和 `/octop/api/health`；health 返回 `ok=true, db=true`。`/octop/api/setup/status` 确认 `database_driver=opengauss`、数据库已绑定、初始化向导需要密码且密码文件存在。浏览器实际显示中文初始化向导，未报告脚本错误；没有读取口令或创建管理员。
6. 修改前后 `/spug/`、`/spug/home`、`/oa/`、`/bpm/` 均为 200，响应内容摘要、类型和跳转信息完全一致；`/` 原有 403 保持不变。外网 Spug/OA/BPM 入口也为 200。对 `/octop/api/notifications/ws` 发起缺少 token 的 WebSocket 握手，返回预期 403；该项仅验证入口与鉴权拒绝，不能替代登录后的聊天/WebSocket 业务测试。

执行脚本 `octop-aj-nginx-route-20261028.py`、`nginx-route-execution.log`、`nginx-route-verification.json`、`nginx-backup-path.txt` 保存在本次发布目录，脚本 SHA-256 为 `20aa2bc160bf255e296acd386b7620b730043c7baa286c5bc117584044c03dd0`。外网检查结果另保存为 `octop-aj-external-nginx-20261028.json`。后续继续首次管理员、登录和业务验收；本次没有配置模型或调用外部业务 MCP。

## 2026-10-29 白标镜像更新

本轮经用户确认发布，构建和上传均固定主分支提交 `b46b9722`，包含品牌提交 `868e7d15`。之后出现的本地未提交源码修改不在本次镜像中。未打生产 Git 标签。

| 操作 | 结果与证据 |
| --- | --- |
| 本地镜像验收 | `octop-opengauss:main-b46b9722`，image ID `sha256:b283255f0889e739d667c79d74429670f13c285d352396bf190ede19a2ce0223`，amd64；品牌校验脚本通过，`import octop` 成功，未安装 fastembed，gs_dump/gs_restore 均为 6.0.5 |
| 离线交付 | Spug 上传至 `/APP/DockerData/octop-aj/releases/main-b46b9722-20261029/`，37 个 32 MiB 以内分片；远端分片及完整归档 SHA-256、`gzip -t` 全部通过 |
| 完整归档 | `octop-opengauss-main-b46b9722-20261029.tar.gz`，1,209,496,010 bytes；SHA-256 `fb28da2daa0e80801fffb92f888a8906882182850bcf55edacd874900fbd5f96` |
| 更新前副本 | `backups/pre-main-b46b9722-20261029/` 保存旧 image ID、Compose、受限 `env` 及 data 文件副本。目录 0700、配置 0600；文件复制期间应用仍在运行，未验证为 SQLite 一致性恢复点 |
| 导入和配置 | `docker load` 后远端 image ID 与本地一致；仅更新 `.env` 的 OCTOP_IMAGE 和安装已审核 Compose，`docker compose config -q` 通过，未打印密码 |
| 应用替换 | `docker compose --env-file config/.env -f docker-compose.opengauss-aj.yml up -d --force-recreate --no-deps` 返回 0；容器 running/healthy，仅绑定 `172.17.0.1:18088`，未再次重启 |
| 数据库只读核验 | 在应用容器中使用运行账号执行 `BEGIN READ ONLY`：`current_database=octop`、`current_user=octop`、schema 25、public 表 36、用户 1、管理员 1；事务回滚 |
| 持久数据加载 | health `ok=true, db=true, users_loaded=1, agents_running=1`；setup/status 为 `setup_required=false, database_driver=opengauss, database_bound=true`。本轮未创建或重置账号 |
| 品牌及入口 | 公开 `/octop/` 返回 200，标题和 manifest 均为“PLAT 助手”；17 项资源/API 检查通过，8 个品牌文件 SHA-256 与本地镜像一致；浏览器展示登录页，未报告脚本错误 |
| 其他入口 | `/spug/`、`/oa/`、`/bpm/` 均返回 200；仅入口检查，不代表这些系统的完整业务回归 |
| 既有容器 | openGauss 启动时间仍为 `2026-10-09T06:51:35.736230935Z`；Nginx 仍为 `2026-10-12T01:55:52.518555208Z`，均未重启；本轮未修改 Nginx，未在 plat 执行 DDL/DML |
| 更新后数据库备份 | `backups/post-main-b46b9722-20261029/octop-verified.dump`，116,056 bytes，0600；SHA-256 `bdce701edbc6096f0c7014294a9b75d7dcc70bccd829363233fc29eebfc3bcfe`，6.0.5 `gs_restore -l` 通过，未恢复 |

备份操作使用 `gs_dump -p 5432 -F c -f <临时文件> octop`，随后复制到受限备份目录，使用同版本 `gs_restore -l <文件>` 检查。本次客户端需要指定文件，早期未指定文件的尝试不作为成功备份。本轮更新前未重新生成数据库全量备份；上述 dump 为更新后备份，不能当作更新前恢复点。数据目录副本同样不能替代经过一致性验证的 SQLite 备份或隔离恢复演练。

当前验收范围为应用更新、品牌、入口及数据库只读核验。已有管理员并不代表本轮执行了登录测试；聊天/WebSocket、Agent Memory、知识库、业务 MCP 和隔离恢复演练仍待执行。本轮没有配置模型、调用外部业务或修改原有存储架构。

版本回滚使用 `backups/pre-main-b46b9722-20261029/env` 与旧 Compose，恢复到 `config/.env` 和应用根目录后，先 `config -q`，再 `up -d --no-deps`，核对旧 image ID `sha256:906f34bdcb57f3356da1dc0b7d1c823f4f2e410c24ca8f229f302a39b1a6a576` 和 health。不得删除数据卷、恢复覆盖生产数据库或撤销正在使用的 Nginx 路由。旧镜像、旧归档和新归档均保留。


## 2026-10-29 octop 账号 SYSADMIN 授权

用户在本次会话明确要求不改代码，并指示“给 octop 账号授予 SYSADMIN 系统管理权限”。此前拟议的代码、依赖和测试修复已全部还原，未发布修复镜像；本地及远端暂存修复镜像、交付包已清理，生产始终使用 `main-b46b9722` 原镜像。

专家 `main` 原启动错误为 `permission denied to set parameter "lockwait_timeout"`。AJ 的该参数为 superuser context；普通库或 schema 授权不满足其设置要求。本次以现有 `omm` 管理身份连接独立 `octop` 库执行：

```sh
docker exec opengauss_test su - omm -c "gsql -p 5432 -d octop -v ON_ERROR_STOP=1 -c 'ALTER ROLE octop SYSADMIN;'"
```

返回 `ALTER ROLE`。随后查询：

```sql
SELECT rolname, rolsystemadmin, rolsuper, rolcreatedb, rolcreaterole
FROM pg_roles WHERE rolname = 'octop';
-- octop|t|f|f|f
```

本版本 `SYSADMIN` 对应 `rolsystemadmin`；只检查 `rolsuper` 会漏掉此次授权。系统管理权限作用于实例，不能描述为仅限独立 `octop` 库；应用连接配置仍只使用 `octop` 库，本次未在 `plat` 库执行 DDL/DML。

| 项目 | 实际结果 |
| --- | --- |
| 操作时间与审计目录 | 主机记录为 `20261029T120311`；`/APP/DockerData/octop-aj/operations/octop-sysadmin-20261029T120311/`，目录 0700、文件 0600 |
| 留存文件 | `grant.sql`、`grant.result`、`role.before`、`role.after`、`role.after.sysadmin`、`revoke-if-approved.sql`；前两份角色核验只含 rolsuper/createdb/createrole，系统权限以 `role.after.sysadmin` 的 rolsystemadmin 为准 |
| 实际运行账号验证 | 从现有 Octop 容器读取既有连接配置，身份 `octop / octop`；`SET LOCAL lockwait_timeout = 5000` 成功，SHOW 返回 `5s`，SELECT 1 成功；回滚后恢复原值 `20min` |
| 专家启动 | 使用现有管理员登录，调用 `POST /api/agents/main/start` 返回 204；随后 `GET /api/agents` 中 `main.state=running`、`main.last_error=null`；专家页面开关开启、工作区和对话入口可用 |
| Memory 只读存储检查 | `GET /api/agents/main/memory/storage/check` 返回 200；backend=`opengauss`、namespace=`agent_main`、maintenance_supported=true，返回 11 张 namespace 数据表的检查结果；未执行清理或删除 |
| 代码及镜像 | 未修改代码、依赖或配置，未重新发布；生产 image ID 仍为 `sha256:b283255f0889e739d667c79d74429670f13c285d352396bf190ede19a2ce0223` |
| 容器 | Octop StartedAt 仍为 `2026-10-29T02:13:39.863157264Z`；openGauss 仍为 `2026-10-09T06:51:35.736230935Z`；Nginx 仍为 `2026-10-12T01:55:52.518555208Z`；均未重启 |
| 验收范围 | 本次确认权限问题解除、专家成功启动及 Memory 存储检查通过；未进行真实 LLM 聊天、checkpoint 读写、知识库、业务 MCP 或恢复演练，不扩大验收结论 |

撤销语句为 `ALTER ROLE octop NOSYSADMIN;`，已留存为待批准操作，**当前未执行**。撤销前应先确认当前应用不再依赖此权限并重新获得授权，否则专家后续启动和相关存储操作可能再次报错；撤销不涉及删除数据库、恢复覆盖数据或重新建用户。

## 2026-10-29 OIDC 公网地址回访转发

用户批准仅通过网络配置解决 Octop 容器回访公网 OA 地址的问题，并要求更新 `docker/opengauss` 文档。本轮没有修改 Octop 业务代码、OA/Octop 认证配置、数据库或业务数据，没有登记客户端和工具，也没有替换镜像。新增的 Python 文件是已部署的生产运维脚本，不只是测试；本地已有的 `manager.py` 及对应测试改动不属于本轮。

### 实际操作

1. 只读核对主机：Octop bridge IP `172.17.0.8`，Nginx `172.17.0.6`；`ss -lntp 'sport = :8081'` 确认宿主机有 docker-proxy 监听。firewalld、iptables、nftables 管理服务均 inactive / disabled，未启用或调整它们。
2. 初检公网请求从容器超时；内网 `10.51.49.8:8081` 可达，使用公网 `Host: 211.137.76.3:8081` 也能到达 OA。保存 NAT 规则、监听和四个容器的镜像/启动时间基线。
3. 添加唯一注释 `octop-aj-oidc-hairpin` 的 NAT PREROUTING 规则：`docker0` 入站、源 `172.17.0.8/32`、目的 `211.137.76.3/32`、TCP `8081`，DNAT 至 `10.51.49.8:8081`。没有更改既有 `8110` OUTPUT 规则，也没有追加宿主机全局 OUTPUT 转发。
4. 临时规则实测通过后，将仓库 `docker/opengauss/operations/` 中的运维脚本和两个 systemd 文件安装到远端。执行 `systemd-analyze verify`、`systemctl daemon-reload`、首次维护任务和 `systemctl enable --now octop-aj-oidc-network.timer`。
5. 再次核验规则、周期触发、接口可达性和四个容器基线，生成受限回滚脚本。没有重启主机、Docker 或应用。

现场最终规则为：

```text
-A PREROUTING -s 172.17.0.8/32 -d 211.137.76.3/32 -i docker0 -p tcp -m tcp --dport 8081 -m comment --comment octop-aj-oidc-hairpin -j DNAT --to-destination 10.51.49.8:8081
```

### 文件与持久化

| 位置 | 实际内容 |
| --- | --- |
| `/APP/DockerData/octop-aj/operations/oidc-network/` | `aj-oidc-network.py`、`.service`、`.timer`；目录 0700、文件 0600，root 所有 |
| `/etc/systemd/system/octop-aj-oidc-network.service` | 以 root 执行一次规则核对，TimeoutStartSec=60，UMask=0077 |
| `/etc/systemd/system/octop-aj-oidc-network.timer` | 开机后 45 秒触发，上次任务结束 60 秒后再次核对，AccuracySec=5 秒，已 enabled / active |
| `/APP/DockerData/octop-aj/operations/octop-oidc-network-20261029T142042-bdjE/` | 审计目录 0700，文件按 umask 0077 保存；本机时间标识 `20261029T142042` |

审计目录包含 `nat.before`、`nat.after`、`containers.before.json`、`listener.before`、`prerouting.after`、`network.result`、`rule.counter`、`persistence-manifest.json`、`persistence.installed`、`final-check.result`、`final-network.result`，以及权限 0700 的 `rollback-temporary.sh` 和 `rollback-persistent.sh`。

维护脚本实时读取 Octop 的 bridge IP，使用锁避免重叠，已有正确规则时保持不变；容器停止/查不到时清除自身旧规则，IP 变化时替换自身规则。其他 NAT 规则不参与维护。若正常调度，重建后的自动恢复约一分钟；正式发布应立即手工触发一次 service 再验收，不依赖等待 timer。

### 核验结果与边界

| 核验 | 结果 |
| --- | --- |
| NAT 基线对比 | 规则列表仅增加 1 条带本次标记的规则；无原规则删除或重排 |
| 规则计数 | 初次探针 4 次命中、240 字节；最终探针后 8 次命中、480 字节（当时快照） |
| 幂等性 | 重复启动维护 service，规则及计数器保持不变，没有重复规则 |
| 定时配置 | timer enabled / active；任务 Result=success、ExecMainStatus=0；核验到自动触发时间 `2026-10-29 14:25:04 CST` |
| 公网 URL 从 Octop 容器访问 | OA 页面、discovery、JWKS、`/octop/api/health` 均 HTTP 200，不再连接超时 |
| OA OIDC 协议状态 | discovery / JWKS 正文仍是 `isSuccess=false`、`statusCode=500`、`系统异常`，尚未配置可用的 OIDC provider，不能判为登录成功 |
| 内网 URL | 容器及宿主机访问 `http://10.51.49.8:8081/oa/` 均 HTTP 200 |
| 容器/镜像 | Octop、OA、Nginx、openGauss 的 StartedAt 和 image ID 全部与操作前相同，均仍 running |
| 运维文件验证 | 本地 Ruff check/format check 通过，远端 systemd-analyze verify 通过；四组隔离模拟覆盖安装及幂等、IP 更换/停止清理、查询失败不删现有规则、仅清理标记规则的回滚 |
| 未做的验证 | 未重启生产主机或重建容器、未执行生产回滚，未验证真实 OIDC 登录/换码、MCP 或业务写工具 |

临时模拟验证文件为本地 `/tmp/test_aj_oidc_network.py`，不属于交付文件。未运行会自动格式化现有工作区的 `make all`，本轮验证限于新增运维文件、网络和文档，没有扩展为应用功能回归。

若需回滚，执行审计目录的 `rollback-persistent.sh`：先停 timer，再清除自身标记规则，删除两个新增 systemd unit 并 daemon-reload；保留审计和运维脚本。当前没有执行回滚。详细可复用步骤见 [AJ 发布手册第 8 节](AJ_DEPLOYMENT_RUNBOOK.md#8-aj-oidc-公网地址回访转发)，后续认证配置方案见 [OIDC / MCP 分析](AJ_OA_OIDC_MCP_ANALYSIS.md)。

## 2026-10-29 OIDC 配置审核材料（仅本地准备）

用户同意先准备下一阶段配置材料供审核。本轮新增 [配置变更审核稿](AJ_OA_OIDC_CHANGE_REVIEW.md) 及 `oidc-aj/` 下 2 份 YAML、3 份 JSON 示例，列明 OA 外置配置增量、独立签名密钥文件结构、停用状态的 OIDC 客户端/Octop provider，以及仅查询当前调用者自身身份的 Hub 工具和目录。

本轮没有登录或修改远端，没有生成真实密钥或 secret，没有新建客户端、授权角色、发布工具、创建连接器，也没有重启 OA 或调整已安装的网络脚本。示例中的试点服务编码 `oa`、目录 `octop-readonly` 和权限/账号安排仍需审核及实施前复核。只有未来配置变更阶段需要一次 OA 后端重启；本记录不表示该重启已发生。

准备阶段验证范围为 YAML / JSON 解析、模板间 issuer/回调/MCP URL/密钥占位符一致性、管理接口字段与本地参考定义核对，以及文档链接和空白检查。未进行真实 OIDC 登录和 MCP 调用，不把准备结果作为业务验收结论。

后续本地方案修订：用户要求本次启用内嵌，并将 OA/BPM 两个后端加入 allowed-origins。已将 OA embed-origins 与 Octop plat_embed_origins 补为 `http://211.137.76.3:8081`；OA 上游目的清单为 `http://10.51.49.8:60001`、`http://10.51.49.8:60003`，不带 `/*`。消息中的 `600001` / `600003` 按前述已核验端口处理。内嵌所需 Redis/Redisson 开关及客户端 openid/profile/email 自动授权范围一并补齐；增加 OA 前端构建变量示例，现为 6 份配置示例。尚未构建或发布前端、修改生产配置或执行 OIDC / 内嵌验收；BPM 目前只纳入目的允许清单，不表示已登记工具或验证令牌兼容性。

## 2026-10-29 OIDC 实际配置与验收

前面的“配置审核材料”是实施前记录，保留用于说明当时的待办和风险。本节记录用户批准后在 AJ 生产环境实际完成的配置及验收，未打印 client secret、签名私钥、访问令牌或口令。

### 实际变更

1. OA 外置配置加载 AJ 独立 RSA PKCS#8 签名密钥，issuer 为 `http://211.137.76.3:8081/oa/service`；OA 后端完成一次受控重启。标准 discovery 和 `/connect/jwks` 已返回成功响应及 `kid=aj-oa-oidc-20261029`。
2. 通过 OA 管理能力创建并启用 `client_id=octop`，仅启用 authorization code，scope 为 `openid profile email`，回调精确为 `http://211.137.76.3:8081/octop/api/auth/oidc/callback`。
3. Hub 登记并发布服务 `oa`、只读 Operation `oa.current-user` 和目录 `octop-readonly`；目录启用 MCP、未启用 REST。上游调用采用 caller relay，权限为 `system:assistant:use`。本轮没有登记 BPM 业务工具，也没有调用业务写接口。
4. Octop 已启用对应 OIDC provider 和个人只读 MCP 连接器，`plat_mcp_urls` 只包含 OA 只读 MCP 完整地址；未扩大为 OA API 通配路径。OA/BPM 上游 allowed-origins 保持为 `http://10.51.49.8:60001`、`http://10.51.49.8:60003`。
5. OA 与 Octop embed origin 均为 `http://211.137.76.3:8081`。既有 Redis/Redisson 用于 embed-ticket，客户端自动授权范围仅为 `openid/profile/email`，没有新增 Redis 容器。
6. 之前沟通中曾将 `1576800000` 秒描述为 Octop 自身 OAuth2/JWT 会话有效期；本次截图核对确认，已实际调整的是 OA OAuth2 客户端 `octop` 的令牌配置。它与 Octop 本地 JWT 的 `OCTOP_ACCESS_TOKEN_TTL` 是两套配置，不能混用，也不能据此推断 OA 令牌会自动刷新。
7. 2026-10-30 通过 OA OAuth2 客户端管理将 `client_id=octop` 的访问令牌和刷新令牌有效期调整为 `1576800000` 秒（约 50 年）。该配置只作用于后续新签发的 OA access token，即 Octop MCP 调用使用的 `X-Plat-Token`；已有令牌不会回溯延长，需重新完成一次 Plat/OA → Octop 登录。内嵌 `embed-ticket` 仍为约 60 秒的一次性票据。

### 验收结果

| 项目 | 结果 |
| --- | --- |
| OIDC discovery / JWKS | 通过；issuer、RSA key、kid 与 provider 配置一致 |
| OA OIDC 登录 | admin 首次登录成功，Octop 创建 provider + subject 映射用户 `admin_2`；本地管理员数据保留 |
| MCP 探测 | 通过；连接器仅返回 `oa_current_user` |
| 真实 MCP 调用 | 通过；新会话调用返回 OA 当前调用者 `userId=1`、`username=admin` |
| 内嵌票据 | 通过；`embed-ticket` 使用完整 PKCE 参数一次消费成功，票据有效期按 OA 返回值约 60 秒 |
| Octop 健康状态 | `ok=true`、`db=true`；生产容器未重建 |
| 业务写操作 | 未执行；没有修改 OA、BPM、审批或流程数据 |

远端审计目录为 `/APP/DockerData/octop-aj/operations/oidc-apply-20261029T151642`，包含配置变更前后副本、受限密钥副本、前端基线、SHA-256 摘要和脱敏操作摘要。后续普通 OA 账号的权限隔离、过期/重复票据拒绝、BPM 业务工具和完整业务回归仍需单独安排，不能由 admin 只读调用代替。
