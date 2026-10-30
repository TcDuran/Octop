# AJ 环境 Octop 发布手册

本手册记录 AJ 环境 Octop 的发布准备、部署、验收与回滚步骤，后续相同环境可按此复用。生产方案见 [DEPLOYMENT_AJ_CN.md](DEPLOYMENT_AJ_CN.md)，本次逐项操作流水见 [AJ_DEPLOYMENT_RECORD.md](AJ_DEPLOYMENT_RECORD.md)，部署配置为 [`docker-compose.opengauss-aj.yml`](../docker-compose.opengauss-aj.yml)。

截至 2026-10-29，AJ 已受控更新为 `main-b46b9722`，品牌登录页和基础验收通过；线上已存在管理员和运行中的 Agent。下面的创建、首次配置安装和路由追加步骤供新环境使用，不能在当前 AJ 环境重复执行。后续版本更新见第 7 节；登录后的完整业务验收和隔离恢复演练仍待执行。“本次发布记录”中的旧镜像信息保留为首次部署历史。

## 安全边界

- 只新增 Octop 应用容器 `octop-opengauss-aj`，复用主机现有 openGauss；不得新增、重建或重启数据库容器。
- Octop 只连接独立 `octop` 数据库及同名账号。AJ 已于 2026-10-29 按用户明确授权授予该账号 `SYSADMIN` 系统管理权限，用于现有代码设置 `lockwait_timeout`；这是实例级权限，不是仅限 `octop` 库的授权。应用仍不得连接现有业务库或在其中执行 Octop migration、测试 SQL、扩展安装 SQL。
- 不新增对外数据库端口。Octop 只绑定经现场确认的 Docker bridge gateway 地址和专用 `18088` 端口，由 Nginx 代理。
- 不覆盖整份 Nginx 配置。先备份，只追加 `/octop/` 路由，配置测试通过后才 reload。
- 账号口令及部署配置只放在 `/APP/DockerData/octop-aj/config/.env`；目录权限 `0700`、文件权限 `0600`。不得放入仓库、发布记录、命令行参数或截图。`docker compose config` 会展开密钥，输出不得贴入工单或日志。
- 首次部署回滚可恢复 Nginx 配置并停止新增 Octop 容器；版本更新回滚则恢复更新前 `.env` 和 Compose，再用旧 image ID 重建应用，不撤销已使用的 Nginx 路由。保留数据目录、数据库和旧镜像；禁止 `docker compose down -v`。
- 当前 AJ 的 OA/OIDC 入口仍需确认 TLS 终止位置；openGauss `octop` 账号具有为 Memory 所需的实例级 `SYSADMIN`，口令摘要为现有 MD5 配置；OA `octop` 客户端 token 有效期为约 50 年。上述三项都必须在生产放行记录中单独确认，不能由应用健康检查替代。

## 1. 发布前只读预检

通过 Spug Web 终端连接 `10.51.49.8`。本阶段只执行只读命令，不创建数据库、角色、目录或容器，不修改配置：

```sh
hostname
uname -m
cat /etc/os-release
free -h
df -h /APP
docker version --format '{{.Server.Version}}'
docker ps --no-trunc
docker network ls
ss -lntp
ip -4 route
```

从 `docker ps` 确认现有 openGauss 与 Nginx 容器名后，再分别检查镜像、网络和挂载。只输出所需字段，不运行完整 `docker inspect`（其中可能含环境变量口令）：

```sh
docker inspect --format '{{.Config.Image}} {{json .NetworkSettings.Networks}} {{json .Mounts}}' <opengauss容器名>
docker inspect --format '{{.Config.Image}} {{json .NetworkSettings.Networks}}' <nginx容器名>
```

本次部署前只读预检记录（2026-10-28；以下无路由、空端口和目录不存在均指部署前状态）：

- 主机为 Kylin Linux Advanced Server V10 (Halberd)，`x86_64`；8 核、约 30 GiB 内存，约 17 GiB 可用。`/APP` 为 196 GiB，已用 29 GiB、可用 157 GiB。
- Docker Server `26.0.2`。默认 `bridge` 网络为 `172.17.0.0/16`，网关 `172.17.0.1`。
- 现有数据库容器名 `opengauss_test`，镜像标签 `opengauss:6.0.5`，容器地址 `172.17.0.2`，宿主机端口映射 `15434 -> 5432`；数据挂载为 `/APP/DockerData/opengauss/test:/var/lib/opengauss`。容器实际 image ID 为 `sha256:ae937cb57c350ee69df21fff0dbea8db0122fd79f6128d5bddf65317cd945e55`，与本地已验证 `gs_dump`/`gs_restore` 的客户端来源镜像 ID 一致；该镜像没有 RepoDigest。容器内 `gs_ctl`/`gs_dump` 不在默认 PATH，未查询运行中服务的 build 字符串。现有容器、库和挂载均未触碰。
- Nginx 容器镜像 `nginx:1.31.3`，位于同一默认 `bridge`，地址 `172.17.0.6`；配置目录挂载自 `/APP/DockerData/nginx/conf`，AJ 配置文件为 `http_aj.conf`。Nginx 当前 HTTP server 监听容器 `80`，主机通过 `8081 -> 80` 提供访问；配置含现有 `/spug/` 及 AJ 工作流路由，没有 `/octop/`。本次检查到的 Nginx listener 为 `80`、`8110`；未发现 `18088` 正在监听。
- 默认 `bridge` 不应依赖容器名 DNS。独立 `octop` 库和角色已创建，并已通过同一 bridge 网络中的临时驱动容器，使用 `172.17.0.1:15434` 完成认证和只读连接验证。后续 Octop 的 `18088` 仍只绑定 `172.17.0.1` 供 Nginx 访问；不得连接 `172.17.0.2` 作为固定地址。
- `/APP/DockerData/octop-aj` 与其 `data` 子目录在预检时不存在；后续已完成独立数据目录和配置准备，详见本次操作记录。外部访问 URL 配置为 `http://211.137.76.3:8081/octop`，与当前 Nginx HTTP 入口相符；TLS 终止位置仍需确认，开放 Octop 登录前必须确认该入口是否应提供 TLS。

后续通过正式镜像的驱动只读查询已确认服务为 6.0.5 build `5ba692ab`，数据库认证和网络正常；应用及 Nginx 路由也已部署。实际外部入口为 `http://211.137.76.3:8081/octop/`，TLS 终止位置仍待确认。本次离线镜像没有 RepoDigest，以本地及远端一致的 image ID 核对身份。日志和 inspect 输出先检查是否含密钥再保存。

## 2. 构建与交付镜像

以审核通过的源码提交构建，不在生产机编译。构建前记录 Git 提交号和工作区状态；发布构建应从干净工作区开始。AJ 前端路径使用 `/octop/`，本地 embedding 关闭，架构为 `linux/amd64`，目标为 `opengauss-runtime`：

```sh
docker buildx build \
  --platform linux/amd64 \
  --target opengauss-runtime \
  --build-arg VITE_BASE_PATH=/octop/ \
  --build-arg INSTALL_LOCAL_EMBEDDING=false \
  --tag <审核过的镜像名:版本> \
  --load \
  -f docker/Dockerfile .
```

本次采用 `--load` 构建后离线交付，镜像保存、32 MiB 分片、Spug 上传、两端 SHA-256 和 gzip 校验步骤见 [正式镜像报告](FORMAL_IMAGE_BUILD_REPORT.md)。如以后使用已批准且可用的镜像仓库，再将 `--load` 改为 `--push`。生产 Compose 必须引用不可变镜像：仓库交付使用 `仓库@sha256:...`，离线 `docker load` 交付使用核对过的本地 `sha256:...` image ID，并设置 `pull_policy: never`。不能使用 `latest` 或可覆盖标签。

验证构建参数、镜像架构、PLAT 品牌、`/octop/` 静态资源、`gs_dump`/`gs_restore` 版本和 `/api/health`。镜像中的 openGauss 镜像仅用于构建阶段复制匹配版本的客户端工具；它不会作为数据库容器运行。

Docker 正式交付的最小文件集为：镜像归档及分片/完整归档 SHA-256 清单、Compose 文件、源码提交和 image ID 记录、本文档及操作记录、`operations/` 运维文件。wheel 是独立的 Python 离线安装交付物，不随 Docker 镜像部署；`.env`、OIDC 客户端 secret、签名私钥、数据库备份和业务数据不得进入发布归档。

## 3. 配置数据库与主机

本次数据库实施记录必须写入 [AJ_DEPLOYMENT_RECORD.md](AJ_DEPLOYMENT_RECORD.md)。执行前先确认 `octop` 库和角色不存在，并确认操作者使用 `omm` 管理身份。`plat` 是现有业务数据库/账号，不作为 Octop 运行账号，也不在其中执行 Octop DDL/DML。

数据库操作只新增独立 `octop` 角色和同名数据库，数据库由该角色拥有。初建角色使用 `NOCREATEDB NOCREATEROLE`；当前 AJ 随后已获批准授予 `SYSADMIN`，不再是普通业务账号。沿用当前镜像、且 Memory/checkpoint 继续存入 openGauss 的新环境，需要在启动验收前核对超时参数权限；如采用相同管理权限方案，应先获得该环境授权，再执行下文步骤。先将口令安全生成并写入权限为 `0600` 的 `.env`，以 `NOLOGIN` 创建角色；通过标准输入执行 `ALTER ROLE ... PASSWORD ...`，确认 SQL 输出明确成功后再启用 LOGIN。不要把口令放进 SQL 文件、命令行参数、终端输出或操作记录。该环境的 `gsql` 不支持 PostgreSQL 的 `\\password` 命令，而且 SQL 报错时可能仍返回成功退出码，必须检查 SQL 输出本身。本次使用 PostgreSQL 风格的环境变量/`-w`方式未能完成认证；该版本明确支持 `gsql -2` 从标准输入接收连接口令，并已用此方式验证成功。部署时按现场 `gsql --help` 确认客户端参数。只读预检已确认 `omm` 有建库/建角色权限而 `plat` 没有；不得复用 `plat` 账号。若目标库或角色已存在，停止并调查归属，不能覆盖、改密或清理。

全新环境的数据库操作顺序示例（仅在确认库和角色不存在后执行；本次 AJ 环境已完成，不要重跑）：

```sql
-- 先只读确认，不要先运行下面的 DDL：
SELECT datname FROM pg_database WHERE datname = 'octop';
SELECT rolname, rolsystemadmin, rolsuper, rolcreatedb, rolcreaterole
FROM pg_roles WHERE rolname IN ('omm', 'plat', 'octop');

-- 确认 octop 不存在后，按现场 openGauss 支持的角色属性语法创建：
CREATE ROLE octop NOLOGIN NOCREATEDB NOCREATEROLE PASSWORD '<由安全方式注入的强随机口令>';
CREATE DATABASE octop OWNER octop ENCODING 'UTF8' TEMPLATE template0;
```

`ALTER ROLE ... PASSWORD ...` 可用仅包含 SQL 的标准输入执行，不要将真实口令直接写在命令参数中。只有确认语句输出为成功、口令已经与 `.env` 同步后，才执行 `ALTER ROLE octop LOGIN;`。不要在登录未设置强口令时开放应用网络访问。AJ 当前 openGauss 6.0.5 接受的口令最长为 32 个字符，且至少包含三类字符。该环境的 `password_encryption_type=0`，配置来源为 `/var/lib/opengauss/data/postgresql.conf` 第 913 行，因此新口令摘要为 MD5，服务端会输出 MD5 安全提示；这是现有实例配置，本次部署未修改它。部署前还必须从应用将实际使用的网络路径验证账号认证成功并连接到 `octop` 库；仅确认 `rolcanlogin` 和口令字段非空不等于连接验收通过。openGauss 自带客户端使用 `gsql -2` 接收标准输入中的密码进行该测试。

验证发现，即使创建 `NOLOGIN` 角色，省略密码仍会被 openGauss 拒绝（`The password could not be NULL`）。密码长度超过 32 字符或复杂度不符合服务端要求也会被拒绝；由于 `gsql` 的退出码不能可靠表示 SQL 执行失败，脚本必须检查并匹配成功输出后再继续后续语句。上方 `PASSWORD` 值必须由安全流程提供，不能原样执行占位符；没有安全注入口令时，不执行建角色/建库。

该 openGauss 6.0.5 的 `\\h CREATE ROLE` 帮助不包含 PostgreSQL 的 `SUPERUSER/NOSUPERUSER` 选项；本方案使用该版本支持的属性；AJ 当前账号已授予 `SYSADMIN`，必须通过 `pg_roles.rolsystemadmin` 核验，不能仅检查 `rolsuper`。建库成功后，连接新库检查数据库 owner、角色属性和连接权限；确认没有在 `plat` 数据库产生新表或其他对象。若当前 openGauss 版本不接受上述语法，停止并根据服务端 `gsql`/帮助输出修订脚本，不要在生产库反复试 SQL。

建库后还必须核验 schema 权限。本次 `octop` 虽是数据库 owner，但 `public` schema 仍属于 `omm`，账号只有 USAGE、没有 CREATE，导致首次 migration 报 `permission denied for schema public`。修复仅在新 `octop` 库执行，不能扩大到共享实例或 `plat` 库：

```sh
docker exec -it opengauss_test su - omm -c 'gsql -p 5432 -d octop'
```

进入上述新库会话后先核对目标、schema owner 和 ACL，保存修改前 ACL；若仅缺少该账号所需权限，再执行授权并复查：

```sql
SELECT current_database(); -- 必须为 octop
SELECT nspname, pg_get_userbyid(nspowner), nspacl
FROM pg_namespace WHERE nspname = 'public';
SELECT has_schema_privilege('octop', 'public', 'USAGE') AS usage_ok,
       has_schema_privilege('octop', 'public', 'CREATE') AS create_ok;

-- 仅针对已确认的独立 octop 库和运行账号，不授予 PUBLIC 或管理权限：
GRANT USAGE, CREATE ON SCHEMA public TO octop;

SELECT has_schema_privilege('octop', 'public', 'USAGE') AS usage_ok,
       has_schema_privilege('octop', 'public', 'CREATE') AS create_ok;
```

两项权限必须均为 `t`，还需用实际应用账号连接。AJ 已完成 schema 授权和基础初始化；该授权不能解决下面的 `lockwait_timeout` 参数权限问题，无需重复建库或改密。

### Agent Memory 启动所需的 SYSADMIN 授权（AJ 已实施）

AJ openGauss 6.0.5 的 `lockwait_timeout` 在 `pg_settings` 中为 `superuser` context。现有 `octop-memory==1.0.2+opengauss.2` 的 openGauss Memory/checkpoint 初始化会执行 `SET LOCAL lockwait_timeout = 5000`，存储检查/清理也会设置该参数。普通账号启动专家会报 `permission denied to set parameter "lockwait_timeout"`；库 owner、schema USAGE/CREATE 或 `GRANT ... ON DATABASE` 都不能替代参数所需权限。

2026-10-29，用户明确选择不改代码，并授权给独立 `octop` 账号授予 `SYSADMIN`。本次仅执行以下角色属性变更，没有修改实例全局参数、口令或应用配置，也没有重新发布镜像或重启容器：

```sh
# 由已有管理员 omm 进入独立 octop 库，不使用 plat 业务账号：
docker exec -it opengauss_test su - omm -c 'gsql -p 5432 -d octop'
```

```sql
-- 已有 AJ 环境先核验，不必重复授权；新环境须先确认相应授权：
SELECT rolname, rolsystemadmin, rolsuper, rolcreatedb, rolcreaterole
FROM pg_roles WHERE rolname = 'octop';
ALTER ROLE octop SYSADMIN;
SELECT rolname, rolsystemadmin, rolsuper, rolcreatedb, rolcreaterole
FROM pg_roles WHERE rolname = 'octop';
```

AJ 实测结果为 `octop|t|f|f|f`。其中 `rolsystemadmin=t` 表示已具有系统管理权限；即使 `rolsuper=f`，也不能据此判为普通账号。`SYSADMIN` 的权限范围覆盖实例，独立数据库和连接配置仍指向 `octop`，但不构成该账号的权限隔离边界。

再使用实际应用账号 `octop` 建立连接，执行以下验证；不能用 `omm` 的成功结果替代运行账号测试：

```sql
SELECT current_user, current_database(); -- octop / octop
BEGIN;
SET LOCAL lockwait_timeout = 5000;
SHOW lockwait_timeout; -- AJ 返回 5s
SELECT 1;
ROLLBACK;
SHOW lockwait_timeout; -- AJ 恢复原值 20min
```

参数验证通过后，在专家页面对失败专家执行“重试启动”，同时核验 `/api/agents` 中的真实 `state` 和 `last_error`，不能只看 `/api/health` 的计数。AJ 专家 `main` 已恢复 `running`、`last_error=null`，Memory 存储检查返回 200，后端 `opengauss`、namespace `agent_main`。没有因此宣称聊天、checkpoint 读写、知识库或业务 MCP 全流程已经通过。

如后续决定收回权限，需先确认应用已不再依赖该权限，获得批准后由管理员执行以下命令并复验；当前未执行撤销，直接撤销可能使专家后续启动再次失败：

```sql
ALTER ROLE octop NOSYSADMIN;
SELECT rolname, rolsystemadmin FROM pg_roles WHERE rolname = 'octop';
```

本次实际操作与审计目录见 [AJ 操作记录](AJ_DEPLOYMENT_RECORD.md#2026-10-29-octop-账号-sysadmin-授权)。

在 `/APP/DockerData/octop-aj/` 放置 Compose 文件；账号口令和其他环境配置单独放在 `config/.env`。目录设为 `0700`、文件设为 `0600`。必填配置：

```dotenv
OCTOP_IMAGE=<核对过的本地 image ID 或仓库摘要引用>
OCTOP_DATABASE_NETWORK=bridge
OCTOP_DATABASE_HOST=172.17.0.1
OCTOP_DATABASE_PORT=15434
OCTOP_DATABASE_NAME=octop
OCTOP_DATABASE_USER=octop
OCTOP_DATABASE_PASSWORD=<只留在权限受限的 config/.env 中>
OCTOP_BIND_ADDRESS=172.17.0.1
OCTOP_PUBLIC_BASE_URL=http://211.137.76.3:8081/octop
```

TLS 边界确认后再确定公开 URL 使用 `http` 还是 `https`。口令只写入权限为 `0600` 的 `config/.env`，不进仓库、命令行参数或记录页；运行 `docker compose --env-file config/.env config -q` 校验时避免输出渲染后的完整配置。

### Compose 环境文件与 OIDC 客户端密钥的边界

Compose 插值变量来自当前 shell、显式 `--env-file`，以及 Compose 默认发现的 `.env`；`config/.env` 不属于默认发现位置。因此 AJ 的每次操作都必须显式指定配置文件：

```sh
cd /APP/DockerData/octop-aj
docker compose --env-file config/.env \
  -f docker-compose.opengauss-aj.yml config -q
docker compose --env-file config/.env \
  -f docker-compose.opengauss-aj.yml up -d
```

`OCTOP_*` 的 `${...}` 先由 Compose 在宿主机侧解析，再写入 `octop` 服务的 `environment`。其中 `:?` 表达式缺值即失败，`:-info` 仅对日志级别提供默认值；Compose 文件没有使用 `env_file:` 将文件整体注入容器。

`config/oidc-aj-client.env` 是 OIDC 客户端登记凭据的受限保管文件（目录 `0700`、文件 `0600`），与数据库运行时 `config/.env` 分开。它不在 Compose 文件中被读取，也没有挂载进 Octop 容器；OA 客户端和 Octop provider 的 secret 已分别写入各自受控配置/数据库。只有执行经审核的客户端登记、恢复或轮换操作时，才按需由受限 shell 显式读取该文件，不能把它误传给 `docker compose`，也不能把 client secret 写入 Compose、仓库或普通日志。
设置 `/APP/DockerData/octop-aj/data` 的属主和权限前，先依据镜像运行用户确认目标值；不得更改现有 openGauss 数据目录权限。将 Compose 文件部署至该目录，运行 `docker compose config -q`；避免打印完整渲染配置。每项主机变更前后都要在记录页登记。

AJ 使用默认 `bridge` 网络时，Compose 必须采用 `network_mode: ${OCTOP_DATABASE_NETWORK}`，不能以 `networks: ... external: true` 连接默认 bridge；后一种写法会附加服务别名，被 Docker 以 `network-scoped aliases are only supported for user-defined networks` 拒绝。当前部署配置已修正，并通过临时 Compose 驱动容器实际验证。

配置解析通过后，使用正式镜像运行覆盖为 Python 的临时容器，进行只读数据库连接核验；不得调用 Octop 启动入口。查询 `current_user/current_database`、服务版本、角色权限和 `public` 表数量，并确认容器退出后删除。AJ 已完成该验证，结果为 `octop|octop`、openGauss 6.0.5 build `5ba692ab`，初始化前业务表数量为 0。验证所用命令为：

```sh
cd /APP/DockerData/octop-aj
docker compose --env-file config/.env -f docker-compose.opengauss-aj.yml \
  run --rm --no-deps -T --name octop-aj-preflight-20261028 \
  --entrypoint python octop - \
  < releases/main-ee6f5c7d-20261028/octop-aj-db-readonly-20261028.py
```

上述文件是本次发布目录中已核对摘要的只读脚本，具体查询和结果见操作记录；后续环境执行前须先核对脚本内容，不能把未审阅脚本作为探针运行。

## 4. 启动与 Nginx 接入

启动前再次确认服务名、容器名、端口、数据路径和数据库名，并备份新库、完整数据目录、Compose 与受限 `.env`。AJ 启动前备份位于 `/APP/DockerData/octop-aj/backups/prestart-20261028T162340/`；空库 dump 834 bytes，`gs_restore --list` 通过，仅验证归档可读，尚未做实际恢复。启动后确认只有新增 Octop 应用容器。执行 Compose 的操作由值班人记录开始/结束时间：

```sh
cd /APP/DockerData/octop-aj
docker compose --env-file config/.env -f docker-compose.opengauss-aj.yml up -d
docker ps --filter name=octop-opengauss-aj
docker inspect --format '{{.Config.Image}} {{json .NetworkSettings.Networks}} {{json .Mounts}}' octop-opengauss-aj
curl --fail http://<bridge-gateway>:18088/api/health
```

确认迁移只发生于新 `octop` 库，检查 schema 水位和 baseline 标记；本次结果为 schema 25、36 张表。先受控重启应用，验证健康和向导文件持久化，再备份 Nginx 配置并追加路由：

```nginx
location = /octop { return 308 /octop/$is_args$args; }
location ^~ /octop/ {
    proxy_pass http://<bridge-gateway>:18088/;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $http_host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Host $http_host;
    proxy_buffering off;
    proxy_read_timeout 3600s;
}
```

先运行 Nginx 配置测试，通过后才 reload。确认 TLS 已由审核过的入口终止，再允许用户登录。

本次 AJ 已于 2026-10-28 实施该路由。备份目录为 `/APP/DockerData/octop-aj/backups/nginx-20261028T171128/`，包含原 `http_aj.conf` 和完整 Nginx 配置归档。现场采用以下命令检查并平滑加载：

```sh
docker exec nginx nginx -t
# 仅在上一步成功且配置差异核对无误后执行：
docker exec nginx nginx -s reload
```

改动必须仅限新增 location；备份目录放在 Nginx include 目录之外，避免误加载旧配置。修改前后分别检查现有 Spug/OA/BPM 入口，核对容器 ID、启动时间与重启次数。AJ 本次这些入口响应完全一致，容器均未重启。外网 `/octop/`、保留参数的 308 跳转、11 个页面资源和 health 已通过，页面处于受密码保护的初始化向导。无 token 的 WebSocket 握手返回 403；登录后的 WebSocket、聊天、Memory 和知识库业务验收仍待首次设置后执行。

未配置初始管理员密码时，首次入口为设置向导，管理员数量为 0 属于本次预期状态。向导口令文件在容器 `/data/.octop/octop-login.txt`，对应主机 `/APP/DockerData/octop-aj/data/octop-login.txt`，权限 `0600`；只由获授权管理员在受限终端读取，不复制到发布文档或普通日志。完成首次管理员设置后再验收登录、配置保存和业务数据持久化。模型、第三方 embedding、OIDC 和连接器均需按业务配置，不能因入口健康就视为这些功能已经验收。

## 5. 功能、备份与恢复验收

- 健康检查、静态资源、`/octop/` 子路径、API、OIDC 登录和 cookie 路径。
- 新建 Agent、保存/读取 Memory、重启后继续读取；检查 Memory 与 checkpoint 只在新 `octop` 数据库中。
- 使用第三方 embedding 完成知识库上传、检索和引用验证；确认原文及 `index.sqlite` 落在 `/APP/DockerData/octop-aj/data/knowledge/`。不安装本地 embedding 或 DataVec。
- 仅在连接器已配置且业务方安排测试时执行真实 MCP 调用，单独记录只读/写入用例和结果。
- 使用匹配版本 `gs_dump`/`gs_restore` 备份恢复新数据库；同时备份完整 Octop 数据目录、`.env`（按密钥材料保护）和 Nginx 配置。SQLite 备份须暂停写入或采用一致性方式并包含 WAL。
- 恢复至隔离目标后重新验证登录、Memory 和知识库；记录归档摘要、恢复结果及数据完整性。

隔离恢复不得覆盖线上 `octop` 库或 `/APP/DockerData/octop-aj/data`：由管理员创建带时间后缀的隔离库，使用匹配版本的 `gs_restore --exit-on-error` 恢复；复制 Compose 为临时文件，改用隔离库、临时容器名和未占用 loopback 端口，并先执行 `docker compose ... config -q`。数据库恢复后，再将配套数据目录复制到独立临时 `OCTOP_HOME`，检查 `knowledge/**/index.sqlite` 的 `PRAGMA integrity_check` 和原文摘要。验证 health、schema、Memory/checkpoint、知识库检索和只读 MCP；完成后停止临时实例，确认无连接后删除隔离库和临时目录，保留脱敏审计结果。

## 6. 回滚

恢复 Nginx 原配置并测试，通过后 reload；停止 Octop 应用容器并保留数据库、数据目录和镜像供调查。若 migration 已运行且需回到旧数据库状态，只能按经验证的备份恢复新 `octop` 数据库。不得删除或覆盖现有 openGauss 实例、现有数据库、其他容器或卷。

## 本次发布记录

日期：2026-10-28（Asia/Shanghai）  
状态：数据库、镜像导入、Compose、`.env` 和独立数据目录已完成；应用健康与重启持久化验证通过，Nginx 路由已接入并完成外网入口验收，当前等待首次管理员设置。

| 项目 | 记录 |
| --- | --- |
| 镜像源码分支 / 提交 | `main` / `ee6f5c7d3e88b6830c17646e63a6706944886881`，从干净工作区构建 |
| 远端预检与数据库 | 独立 `octop` 库和账号可连接，服务版本 6.0.5 build `5ba692ab`；初始化前 0 张表，初始化后 schema 25、36 张表，管理员 0；TLS 终止位置待确认 |
| 质量检查 | Ruff、mypy、`npx tsc -b` 通过；`PYTEST_JOBS=4 make test` 为 `5,279 passed, 93 skipped`。默认 8 worker 全量运行时 `test_backup_deletion_excludes_maintenance_and_restore` 一次 5 秒等待超时，单独重跑通过 |
| 镜像目标 | `linux/amd64`，`opengauss-runtime`，`VITE_BASE_PATH=/octop/`，`INSTALL_LOCAL_EMBEDDING=false` |
| 本地构建 | 已完成；首次 container builder 因 Harbor IP 证书 SAN 不匹配失败；后续改用本地 Docker builder 与已有缓存镜像 |
| 正式镜像 | `octop-opengauss:main-ee6f5c7d`，已离线导入远端 Docker |
| 本地及远端镜像 ID | `sha256:906f34bdcb57f3356da1dc0b7d1c823f4f2e410c24ca8f229f302a39b1a6a576` |
| 架构 / 镜像大小 | `linux/amd64` / 本地 3,138,821,813 bytes；远端统计见正式构建报告 |
| openGauss 客户端来源 | 本地缓存镜像 `10.24.1.80:2443/library/opengauss:6.0.5`，ID `sha256:ae937cb57c350ee69df21fff0dbea8db0122fd79f6128d5bddf65317cd945e55`；远端运行容器 image ID 相同；客户端版本 6.0.5 build `5ba692ab` |
| Compose 与网络 | 已安装并校验，固定正式 image ID，`pull_policy: never`，`network_mode: bridge`，仅绑定 `172.17.0.1:18088` |
| 镜像品牌 / 子路径 / 本地 embedding | 通过：PLAT 品牌、`/octop/`、未安装 `fastembed` |
| PLAT 嵌入界面 | 候选前端包含隐藏 Agent Profile/工作区/浏览器入口及欢迎页吉祥物的本轮改动 |
| 客户端验证 | `gs_dump` 与 `gs_restore` 均报告 openGauss 6.0.5 build `5ba692ab` |
| 镜像摘要 / 归档校验值 | 无仓库 `RepoDigest`；归档 SHA-256 `99bd2891371da4e5ef8b1ad165afbc1cfe57421c07acf6c4c7c5dadd98737652`，远端分片、完整归档及 gzip 校验通过 |
| 配置 / 数据目录 | `.env` 和 Compose 为 `0600`，`data` 为 root:root、`0700`；原口令保留，旧 `.env` 已在受限配置目录备份 |
| 生产数据库 / 容器 / Nginx | 独立 `octop` 库和账号可连接；应用容器仅绑定 `172.17.0.1:18088`；Nginx 仅追加 `/octop/` 路由并 reload，原入口响应一致；容器未重启，未在 `plat` 库执行 Octop DDL/DML |
| 验收 / 备份恢复 | 启动前数据库与数据目录备份完成；健康、schema 和受控重启持久化通过；隔离恢复、首次管理员、登录和完整业务验收待执行 |

构建补充：首次使用 `octop-amd64-builder` 时因 Harbor 证书只含 DNS SAN、而 Dockerfile 用 IP 地址访问而失败；没有关闭 TLS 校验。第二次使用 `desktop-linux` 构建，复用了本机已有的 openGauss 6.0.5 缓存镜像作为客户端文件来源。数据库容器与客户端来源镜像的 image ID 相同，服务 build 后续经数据库驱动查询确认为 `5ba692ab`。本次没有从生产主机拉取或启动 openGauss 镜像。正式镜像已在合并后的 `main` 提交 `ee6f5c7d` 上构建、离线交付并运行，详见正式构建报告。

## 7. 后续版本更新：标准操作 SOP

2026-10-29 已按该流程完成 `main-b46b9722` 更新，详见 [更新记录](AJ_DEPLOYMENT_RECORD.md#2026-10-29-白标镜像更新)。当前 AJ 已有管理员、OIDC 配置和运行中的 Agent，后续更新不能重复建库、创建管理员、追加 Nginx 路由或覆盖现有 `config/.env`。

### 7.1 先判断变更类型

- **文档或运维文件变更**：只更新仓库/现场文档，不重建镜像、不重启 Octop；生产运维脚本变更须单独审核和记录。
- **应用镜像变更**：按本节执行；默认只替换 `octop-opengauss-aj`，不重启 openGauss、Nginx、OA、BPM 或其他容器。
- **数据库迁移、依赖、OIDC、Nginx、网络规则或存储架构变更**：不能只按镜像更新处理。必须先在隔离库/隔离实例完成验证，并在发布记录中写明额外变更、影响、回滚和维护窗口。

本 SOP 的发布范围是“已审核源码提交构建的新 Octop 镜像 + 必要的 Compose 镜像引用更新”。数据库迁移由应用启动时执行，但升级前必须完成隔离库演练；任何降级都不能只回退镜像。

### 7.2 发布前冻结和基线

1. 确认发布版本、源码提交、变更单、维护窗口、回滚负责人和验收负责人；生成唯一发布编号，例如 `main-<short-sha>-YYYYMMDD`。
2. 在本地干净工作区操作：

   ```sh
   git status --short
   git rev-parse HEAD
   git describe --always --dirty
   ```

   `git status --short` 必须为空。不要从含有未提交代码、测试数据、provider 凭证或本地环境文件的工作区构建正式镜像。
3. 保存当前生产基线：旧 image ID、Compose SHA-256、Nginx 配置摘要、容器 ID/启动时间、`/api/health`、schema watermark、用户/Agent 数量和当前端口。健康检查失败、数据库连接异常或基线无法复现时停止发布。

### 7.3 本地构建和验证

在已审核提交上构建 `linux/amd64`、`opengauss-runtime` 镜像：

```sh
docker buildx build \
  --builder desktop-linux \
  --platform linux/amd64 \
  --target opengauss-runtime \
  --build-arg VITE_BASE_PATH=/octop/ \
  --build-arg INSTALL_LOCAL_EMBEDDING=false \
  --tag octop-opengauss:<release-id> \
  --load \
  -f docker/Dockerfile .
```

构建后必须核对：

- image ID、架构和 Dockerfile target；
- `/octop/` 子路径静态资源和 PLAT 品牌资源；
- `import octop`、`psycopg`、`gs_dump`/`gs_restore` 版本；
- `INSTALL_LOCAL_EMBEDDING=false` 时不存在 `fastembed`；
- 临时核对容器退出且没有数据卷、端口或临时容器残留。

应用源码质量门和前端检查按仓库 `AGENTS.md` 执行。正式发布至少保留 `make all`、前端构建和镜像核对结果；若本轮只发布已验证提交，也要记录复用的原始证据，不得把未运行的检查写成通过。

### 7.4 归档、校验和交付

1. 为该发布建立独立目录，不覆盖旧归档：

   ```sh
   docker save octop-opengauss:<release-id> | gzip -1 > octop-opengauss-<release-id>.tar.gz
   sha256sum octop-opengauss-<release-id>.tar.gz > SHA256SUMS.archive
   gzip -t octop-opengauss-<release-id>.tar.gz
   split -b 32m -d -a 3 octop-opengauss-<release-id>.tar.gz \
     octop-opengauss-<release-id>.tar.gz.part
   sha256sum octop-opengauss-<release-id>.tar.gz.part* > SHA256SUMS.parts
   ```

2. 通过审核的文件上传到远端发布目录：镜像分片、两个校验清单、Compose 文件、源码提交/image ID 记录、更新后的 `docker/opengauss` 文档和已审核的 `operations/` 文件。生产 `.env`、OIDC client secret、签名私钥、数据库备份、业务数据和测试 provider 文件不得进入上传目录。
3. 远端逐片执行校验，所有分片通过后再合并；合并使用 noclobber，禁止覆盖已存在的完整归档。合并后再次执行完整归档 SHA-256、`gzip -t`，然后 `docker load`。
4. `docker image inspect` 必须确认远端 image ID 与本地记录一致、平台为 `linux/amd64`；不使用 `latest`、可覆盖 tag 或未经校验的镜像。

### 7.5 远端替换前备份

在 `/APP/DockerData/octop-aj/` 执行，命令输出不得包含口令或完整渲染后的 Compose：

1. 备份当前 `docker-compose.opengauss-aj.yml`、`.env`、旧 image ID、Nginx 相关配置和操作记录；敏感副本权限为 `0600`，目录为 `0700`。
2. 用匹配版本的 `gs_dump` 取得更新前 `octop` 数据库归档，保存 SHA-256 并执行 `gs_restore --list`。更新后生成的 dump 不能冒充更新前恢复点。
3. 先停止业务写入，再停止 Octop 容器并复制完整 `/APP/DockerData/octop-aj/data`，确保包含 `knowledge/**/docs`、`index.sqlite`、SQLite WAL、workspace 和其他 `.octop` 文件；运行中直接复制目录不能作为一致性恢复点。
4. 若存在数据库迁移，必须已在隔离库完成相同版本升级和恢复验证。不能在 `plat` 库、共享业务库或现有 openGauss 数据目录执行测试 SQL。

### 7.6 远端配置检查和应用替换

1. 只更新受控 `.env` 中的 `OCTOP_IMAGE`，保留数据库、OIDC、公开 URL、网络和端口配置；不要把生产 secret 写进 Compose。
2. 先检查配置，不启动服务：

   ```sh
   cd /APP/DockerData/octop-aj
   docker compose --env-file config/.env \
     -f docker-compose.opengauss-aj.yml config -q
   ```

3. 核对渲染后的非敏感断言：容器名 `octop-opengauss-aj`、`network_mode=bridge`、数据库驱动 `opengauss`、数据库名 `octop`、绑定 `172.17.0.1:18088`、数据卷 `/APP/DockerData/octop-aj/data` 和 `pull_policy=never`。不要把完整 `docker compose config` 输出保存到普通日志。
4. 仅替换应用容器：

   ```sh
   docker compose --env-file config/.env \
     -f docker-compose.opengauss-aj.yml \
     up -d --force-recreate --no-deps
   ```

   不执行 `docker compose down -v`，不重启 openGauss/Nginx，不删除数据卷，不改 Nginx 路由。Spug 连接中断时先查询容器、image ID 和 health，再决定是否重试，不能盲目重复执行。

### 7.7 发布后验收

按以下顺序记录实际结果，任何一项失败都进入回滚判断：

1. 容器 `running`、Docker health 为 `healthy`，image ID 与新归档一致，端口仍只绑定 bridge gateway，数据卷和网络未漂移。
2. `/api/health` 返回 `ok=true`、`db=true`；检查 schema watermark、baseline marker、用户/Agent 加载和启动日志中的 migration 错误。
3. 通过内部入口和 `/octop/` 验证 308、静态资源、API、WebSocket 鉴权边界；不创建生产测试数据，不执行业务写操作。
4. 若本次涉及认证/连接器或前端相关变更，再验证 OIDC discovery/JWKS、Plat 内嵌票据、只读 MCP 和当前用户身份；OAuth token 有效期变化只对新签发令牌生效，需重新 OIDC 登录后测试。
5. 若本次包含数据库迁移，额外检查 Memory/checkpoint、知识库 SQLite sidecar 和已配置的只读业务场景；完整业务回归及恢复演练单独记录，不能由 health 通过替代。
6. 核对 Spug/OA/BPM 原有入口、Nginx 容器、openGauss 容器的 ID 和启动时间未被意外改变；保存命令返回码、摘要和去敏日志。

### 7.8 回滚和发布收尾

1. 应用未启动、health 失败或静态资源异常时，恢复更新前 `.env`/Compose 中的旧 image ID，执行同样的 `config -q` 后：

   ```sh
   docker compose --env-file config/.env \
     -f docker-compose.opengauss-aj.yml \
     up -d --force-recreate --no-deps
   ```

2. 如果新版本已经执行数据库迁移，不能只回滚镜像。只有在维护窗口、备份有效性和影响范围均确认后，才可将新 `octop` 库恢复到隔离验证过的备份；禁止覆盖 `plat` 或整个 openGauss 数据目录。
3. 回滚后重新验证旧 image ID、health、schema、`/octop/` 入口和数据卷；保留新镜像、归档、日志和失败原因供排查，不能立即清理。
4. 发布记录必须包含：发布编号、源码提交、镜像 ID、归档/分片 SHA-256、远端导入结果、备份位置、Compose SHA-256、开始/结束时间、验收结果、是否执行回滚和遗留风险。敏感值只记录文件路径和摘要，不记录明文。
5. OA OAuth2 客户端 `octop` 的 access token、refresh token、OIDC provider、MCP 白名单、NAT/systemd 规则等属于独立配置变更；若本次同时调整，必须在记录中单独写明生效时间、回滚方式和重新登录要求。不要把内嵌 `embed-ticket` 的约 60 秒一次性时效改成长效令牌。

发布放行前还必须明确 TLS 终止点、`SYSADMIN`/MD5 账号风险的批准记录，以及 50 年 token 的撤销和轮换负责人；缺任一项时只能标记为“技术部署完成”，不能标记为“生产安全放行”。

## 8. AJ OIDC 公网地址回访转发

2026-10-29 已按用户批准实施本节。用途是让 Octop 后台请求公网 OIDC 地址时，实际进入本机内网 Nginx，保持 issuer、HTTP Host 和浏览器回调地址一致。网络转发已通过；后续 OA OIDC、Octop provider/connector 和只读 Hub 工具也已完成配置，最终验收见第 9 节及 [AJ 操作记录](AJ_DEPLOYMENT_RECORD.md#2026-10-29-oidc-实际配置与验收)。

### 适用条件与边界

- 当前主机 `10.51.49.8`，默认 Docker bridge 接口 `docker0`、子网 `172.17.0.0/16`；Octop 容器为 `octop-opengauss-aj`。
- 公网目的地址 `211.137.76.3:8081`，内网目的地址 `10.51.49.8:8081`；该端口有实际运行的 docker-proxy，再进入现有 Nginx `80`。
- 已从 Octop 容器验证内网 OA 可达，携带公网 Host 也能到达同一 OA。Nginx 的 OA 代理路径保持原样。
- 仅增加一条带 `octop-aj-oidc-hairpin` 标记的 NAT PREROUTING 规则，限定当前 Octop 源 IP `/32`、入接口 `docker0`、公网目的 IP `/32` 和 TCP `8081`。不按 HTTP 路径区分，因此该容器访问此 IP/端口的其他路径同样走内网。
- 不修改原有规则和默认策略，不增加全局 OUTPUT 转发，不新增容器、不设置全局 HTTP_PROXY，也不修改应用代码或镜像。
- 不照抄到其他主机：需重新审核容器名、网络、内外地址、端口代理监听及防火墙管理方式。若未来移除 docker-proxy 或修改网络，本方案需重新核验。

### 初次安装

实施前，在受限操作目录保存 `iptables-save -t nat`、端口监听以及相关容器的镜像和启动时间。仅备份不意味着可以全量恢复；Docker 动态规则不得被旧快照覆盖。AJ 实施时 firewalld、iptables 和 nftables 管理服务均 inactive / disabled，本次没有启用它们。

首次规则先临时添加验证。以下是本次实际源 IP 的执行记录，不能作为未来容器 IP 的常量复用：

```sh
iptables -w 5 -t nat -I PREROUTING 1 \
  -i docker0 -s 172.17.0.8/32 -d 211.137.76.3/32 \
  -p tcp --dport 8081 \
  -m comment --comment octop-aj-oidc-hairpin \
  -j DNAT --to-destination 10.51.49.8:8081
```

临时验证通过后，上传并校验仓库 `docker/opengauss/operations/` 中三份文件，将它们安装到 `/APP/DockerData/octop-aj/operations/oidc-network/`，目录 0700、文件 0600，root 所有。它们是生产运维文件，不是 Octop 包内代码：

| 文件 | 用途 |
| --- | --- |
| `aj-oidc-network.py` | 查当前容器 IP，幂等维护自身标记规则；容器停止/查不到时清除旧规则；`--remove` 精确清理 |
| `octop-aj-oidc-network.service` | 以宿主机 root 运行一次规则核对，不启动/重启 Docker 或应用 |
| `octop-aj-oidc-network.timer` | 开机后 45 秒触发，其后约每 60 秒核对，调度精度 5 秒 |

在同类已批准部署中，上传并核对文件后执行：

```sh
systemd-analyze verify \
  /APP/DockerData/octop-aj/operations/oidc-network/octop-aj-oidc-network.service \
  /APP/DockerData/octop-aj/operations/oidc-network/octop-aj-oidc-network.timer
install -m 0644 \
  /APP/DockerData/octop-aj/operations/oidc-network/octop-aj-oidc-network.service \
  /etc/systemd/system/octop-aj-oidc-network.service
install -m 0644 \
  /APP/DockerData/octop-aj/operations/oidc-network/octop-aj-oidc-network.timer \
  /etc/systemd/system/octop-aj-oidc-network.timer
systemctl daemon-reload
systemctl start octop-aj-oidc-network.service
systemctl enable --now octop-aj-oidc-network.timer
```

如果目标目录、unit 或同标记规则已存在，先检查其内容与归属，不能直接覆盖。当前 AJ 已安装，不需要重复安装。timer 是持久化恢复机制，没有使用全量 `iptables-save` 文件恢复 Docker 规则。

### 验证与后续容器发布

```sh
systemctl is-enabled octop-aj-oidc-network.timer
systemctl is-active octop-aj-oidc-network.timer
systemctl show octop-aj-oidc-network.service -p Result -p ExecMainStatus
systemctl show octop-aj-oidc-network.timer -p LastTriggerUSec
iptables -t nat -S PREROUTING
iptables -t nat -L PREROUTING -n -v -x
```

预期 timer 为 enabled / active，最近一次任务 Result=success、ExecMainStatus=0；只有一条自身标记规则，源 IP 与当前容器一致。oneshot service 执行完成后为 inactive 是正常状态。重复执行 service 不应重建正确规则或重置其计数器。排查日志可使用 `journalctl -u octop-aj-oidc-network.service --since today --no-pager`。

从 Octop 容器，以原公网 URL 验证 OA 页面、discovery、JWKS 和 `/octop/api/health`；宿主机自身直接使用 `http://10.51.49.8:8081/oa/`。目前 discovery / JWKS 返回 HTTP 200 + 业务码 500，只说明不再连接超时，待 OA OIDC 配置补齐后必须再验证标准协议响应。

后续重建 Octop 容器后，在获取新 IP 并确认端口代理仍可用后，立即运行 `systemctl start octop-aj-oidc-network.service`，再验收 OIDC，避免等待下一次定时核对。系统重启或 Docker 规则重建后也可用该命令立即恢复。周期恢复正常调度下约一分钟，不是实时切换。生产没有做主机重启或容器重建演练，仅已验证配置校验、实际执行、自动触发与隔离模拟。

### 精确回滚

先停用 timer 防止重新添加，再清除本次规则和两个新增 unit；保留运维脚本与审计记录：

```sh
systemctl disable --now octop-aj-oidc-network.timer
systemctl stop octop-aj-oidc-network.service
/usr/bin/python3 /APP/DockerData/octop-aj/operations/oidc-network/aj-oidc-network.py --remove
rm -- /etc/systemd/system/octop-aj-oidc-network.timer \
  /etc/systemd/system/octop-aj-oidc-network.service
systemctl daemon-reload
```

仅删除本次标记规则，不执行 `iptables -F`、`iptables -t nat -F` 或整表恢复。回滚会使 Octop 重新面对原有公网回访阻塞。AJ 已生成权限 0700 的现场回滚脚本，位置见 [操作记录](AJ_DEPLOYMENT_RECORD.md#2026-10-29-oidc-公网地址回访转发)。当前未执行生产回滚。

## 9. OA OIDC / 只读 MCP 配置（已实施）

2026-10-29 已在获批范围内完成 OA OIDC、Octop provider/connector、OA 内嵌票据和一个只读身份 MCP 工具的配置。配置变更审核稿及 `oidc-aj/` 示例仍保留作后续环境模板，但示例含占位符或停用状态，不能直接上传后启用。

已生效的关键配置如下：

- OA issuer 为 `http://211.137.76.3:8081/oa/service`，RSA PKCS#8 签名密钥为 AJ 独立密钥，JWKS 已返回有效 RSA key。
- OA 客户端 `octop` 使用 authorization code、PKCE 和 `openid profile email`，回调为 `http://211.137.76.3:8081/octop/api/auth/oidc/callback`。
- Hub 已登记并发布 OA `oa.current-user` 只读 Operation，目录 `octop-readonly` 仅启用 MCP；本轮没有登记 BPM 业务工具，也没有调用业务写接口。
- Octop provider/连接器已启用，`plat_mcp_urls` 只登记 OA 只读 MCP 完整地址；实际探测返回 `oa_current_user`。
- OA 与 Octop embed origin 均为 `http://211.137.76.3:8081`；OA/BPM 上游 allowed-origins 为 `http://10.51.49.8:60001`、`http://10.51.49.8:60003`，不使用 `/*`。
- 2026-10-30 已将 OA OAuth2 客户端 `octop` 的访问令牌和刷新令牌有效期设置为 `1576800000` 秒（约 50 年）。该设置只影响新签发的 `X-Plat-Token`；已有 Octop 保存的 Plat 凭证仍按原到期时间判断，变更后必须重新登录一次。

协议和真实调用验收已通过：discovery/JWKS 正常，OA admin 完成 OIDC 登录并映射为 Octop 用户；新会话调用 `oa_current_user` 返回 OA 当前用户 `admin` 的身份信息。embed-ticket 使用完整 PKCE 参数消费成功。Octop health 保持 `ok=true`、`db=true`。详细标识、脱敏审计目录、回滚边界和未覆盖的普通账号/业务写工具验收见 [AJ 操作记录](AJ_DEPLOYMENT_RECORD.md#2026-10-29-oidc-实际配置与验收) 与 [OIDC / MCP 分析](AJ_OA_OIDC_MCP_ANALYSIS.md)。
