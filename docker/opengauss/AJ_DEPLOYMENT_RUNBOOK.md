# AJ 环境 Octop 发布手册

本手册记录 AJ 环境 Octop 的发布准备、部署、验收与回滚步骤，后续相同环境可按此复用。生产方案见 [DEPLOYMENT_AJ_CN.md](DEPLOYMENT_AJ_CN.md)，本次逐项操作流水见 [AJ_DEPLOYMENT_RECORD.md](AJ_DEPLOYMENT_RECORD.md)，部署配置为 [`docker-compose.opengauss-aj.yml`](../docker-compose.opengauss-aj.yml)。

## 安全边界

- 只新增 Octop 应用容器 `octop-opengauss-aj`，复用主机现有 openGauss；不得新增、重建或重启数据库容器。
- Octop 只连接管理员新建的独立 `octop` 数据库和非超级用户账号。不要连接现有业务库，不在其中运行 Octop migration、测试 SQL 或扩展安装 SQL。
- 不新增对外数据库端口。Octop 只绑定经现场确认的 Docker bridge gateway 地址和专用 `18088` 端口，由 Nginx 代理。
- 不覆盖整份 Nginx 配置。先备份，只追加 `/octop/` 路由，配置测试通过后才 reload。
- 账号口令及部署配置只放在 `/APP/DockerData/octop-aj/config/.env`；目录权限 `0700`、文件权限 `0600`。不得放入仓库、发布记录、命令行参数或截图。`docker compose config` 会展开密钥，输出不得贴入工单或日志。
- 回滚只恢复 Nginx 配置并停止新增 Octop 容器。保留 Octop 数据目录和新数据库供排查；禁止 `docker compose down -v`。

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

本次只读预检记录（2026-10-28）：

- 主机为 Kylin Linux Advanced Server V10 (Halberd)，`x86_64`；8 核、约 30 GiB 内存，约 17 GiB 可用。`/APP` 为 196 GiB，已用 29 GiB、可用 157 GiB。
- Docker Server `26.0.2`。默认 `bridge` 网络为 `172.17.0.0/16`，网关 `172.17.0.1`。
- 现有数据库容器名 `opengauss_test`，镜像标签 `opengauss:6.0.5`，容器地址 `172.17.0.2`，宿主机端口映射 `15434 -> 5432`；数据挂载为 `/APP/DockerData/opengauss/test:/var/lib/opengauss`。容器实际 image ID 为 `sha256:ae937cb57c350ee69df21fff0dbea8db0122fd79f6128d5bddf65317cd945e55`，与本地已验证 `gs_dump`/`gs_restore` 的客户端来源镜像 ID 一致；该镜像没有 RepoDigest。容器内 `gs_ctl`/`gs_dump` 不在默认 PATH，未查询运行中服务的 build 字符串。现有容器、库和挂载均未触碰。
- Nginx 容器镜像 `nginx:1.31.3`，位于同一默认 `bridge`，地址 `172.17.0.6`；配置目录挂载自 `/APP/DockerData/nginx/conf`，AJ 配置文件为 `http_aj.conf`。Nginx 当前 HTTP server 监听容器 `80`，主机通过 `8081 -> 80` 提供访问；配置含现有 `/spug/` 及 AJ 工作流路由，没有 `/octop/`。本次检查到的 Nginx listener 为 `80`、`8110`；未发现 `18088` 正在监听。
- 默认 `bridge` 不应依赖容器名 DNS。独立 `octop` 库和角色已创建，并已通过同一 bridge 网络中的临时驱动容器，使用 `172.17.0.1:15434` 完成认证和只读连接验证。后续 Octop 的 `18088` 仍只绑定 `172.17.0.1` 供 Nginx 访问；不得连接 `172.17.0.2` 作为固定地址。
- `/APP/DockerData/octop-aj` 与其 `data` 子目录在预检时不存在；后续已完成独立数据目录和配置准备，详见本次操作记录。外部访问 URL 配置为 `http://211.137.76.3:8081/octop`，与当前 Nginx HTTP 入口相符；TLS 终止位置仍需确认，开放 Octop 登录前必须确认该入口是否应提供 TLS。

因此现场应用发布前置条件仍未完全闭环：TLS 终止位置仍待确认；服务 build 字符串和数据库连通性已通过正式镜像的驱动只读查询确认。候选外部 URL 为 `http://211.137.76.3:8081/octop`。运行容器 image ID 已确认，但本地没有 RepoDigest。日志和 inspect 输出先检查是否含密钥再保存。

## 2. 构建与交付镜像

以审核通过的源码提交构建，不在生产机编译。构建前记录 Git 提交号和工作区状态；发布构建应从干净工作区开始。AJ 前端路径使用 `/octop/`，本地 embedding 关闭，架构为 `linux/amd64`，目标为 `opengauss-runtime`：

```sh
docker buildx build \
  --platform linux/amd64 \
  --target opengauss-runtime \
  --build-arg VITE_BASE_PATH=/octop/ \
  --build-arg INSTALL_LOCAL_EMBEDDING=false \
  --tag <审核过的镜像名:版本> \
  --push \
  -f docker/Dockerfile .
```

只有在批准的镜像仓库已确认可用时才推送。若通过离线文件交付，生成镜像归档并在传输两端校验 SHA-256，再在目标机加载。生产 Compose 必须引用不可变镜像：仓库交付使用 `仓库@sha256:...`，离线 `docker load` 交付可直接使用核对过的本地 `sha256:...` image ID，并设置 `pull_policy: never`。不能使用 `latest` 或可覆盖标签。

验证构建参数、镜像架构、PLAT 品牌、`/octop/` 静态资源、`gs_dump`/`gs_restore` 版本和 `/api/health`。镜像中的 openGauss 镜像仅用于构建阶段复制匹配版本的客户端工具；它不会作为数据库容器运行。

## 3. 配置数据库与主机

本次数据库实施记录必须写入 [AJ_DEPLOYMENT_RECORD.md](AJ_DEPLOYMENT_RECORD.md)。执行前先确认 `octop` 库和角色不存在，并确认操作者使用 `omm` 管理身份。`plat` 是现有业务数据库/账号，不作为 Octop 运行账号，也不在其中执行 Octop DDL/DML。

数据库操作只新增独立 `octop` 角色和同名数据库：角色不得拥有超级用户、建库或建角色权限；数据库由该角色拥有。先将口令安全生成并写入权限为 `0600` 的 `.env`，以 `NOLOGIN` 创建角色；通过标准输入执行 `ALTER ROLE ... PASSWORD ...`，确认 SQL 输出明确成功后再启用 LOGIN。不要把口令放进 SQL 文件、命令行参数、终端输出或操作记录。该环境的 `gsql` 不支持 PostgreSQL 的 `\\password` 命令，而且 SQL 报错时可能仍返回成功退出码，必须检查 SQL 输出本身。本次使用 PostgreSQL 风格的环境变量/`-w`方式未能完成认证；该版本明确支持 `gsql -2` 从标准输入接收连接口令，并已用此方式验证成功。部署时按现场 `gsql --help` 确认客户端参数。只读预检已确认 `omm` 有建库/建角色权限而 `plat` 没有；不得复用 `plat` 账号。若目标库或角色已存在，停止并调查归属，不能覆盖、改密或清理。

全新环境的数据库操作顺序示例（仅在确认库和角色不存在后执行；本次 AJ 环境已完成，不要重跑）：

```sql
-- 先只读确认，不要先运行下面的 DDL：
SELECT datname FROM pg_database WHERE datname = 'octop';
SELECT rolname, rolsuper, rolcreatedb, rolcreaterole
FROM pg_roles WHERE rolname IN ('omm', 'plat', 'octop');

-- 确认 octop 不存在后，按现场 openGauss 支持的角色属性语法创建：
CREATE ROLE octop NOLOGIN NOCREATEDB NOCREATEROLE PASSWORD '<由安全方式注入的强随机口令>';
CREATE DATABASE octop OWNER octop ENCODING 'UTF8' TEMPLATE template0;
```

`ALTER ROLE ... PASSWORD ...` 可用仅包含 SQL 的标准输入执行，不要将真实口令直接写在命令参数中。只有确认语句输出为成功、口令已经与 `.env` 同步后，才执行 `ALTER ROLE octop LOGIN;`。不要在登录未设置强口令时开放应用网络访问。AJ 当前 openGauss 6.0.5 接受的口令最长为 32 个字符，且至少包含三类字符。该环境的 `password_encryption_type=0`，配置来源为 `/var/lib/opengauss/data/postgresql.conf` 第 913 行，因此新口令摘要为 MD5，服务端会输出 MD5 安全提示；这是现有实例配置，本次部署未修改它。部署前还必须从应用将实际使用的网络路径验证账号认证成功并连接到 `octop` 库；仅确认 `rolcanlogin` 和口令字段非空不等于连接验收通过。openGauss 自带客户端使用 `gsql -2` 接收标准输入中的密码进行该测试。

验证发现，即使创建 `NOLOGIN` 角色，省略密码仍会被 openGauss 拒绝（`The password could not be NULL`）。密码长度超过 32 字符或复杂度不符合服务端要求也会被拒绝；由于 `gsql` 的退出码不能可靠表示 SQL 执行失败，脚本必须检查并匹配成功输出后再继续后续语句。上方 `PASSWORD` 值必须由安全流程提供，不能原样执行占位符；没有安全注入口令时，不执行建角色/建库。

该 openGauss 6.0.5 的 `\\h CREATE ROLE` 帮助不包含 PostgreSQL 的 `SUPERUSER/NOSUPERUSER` 选项；本方案使用该版本支持的属性，并确认 Octop 角色没有 `SYSADMIN` 等管理角色。建库成功后，连接新库检查数据库 owner、角色属性和连接权限；确认没有在 `plat` 数据库产生新表或其他对象。若当前 openGauss 版本不接受上述语法，停止并根据服务端 `gsql`/帮助输出修订脚本，不要在生产库反复试 SQL。

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

启动前再次确认服务名、容器名、端口、数据路径和数据库名；启动后确认只有新增 Octop 应用容器。执行 Compose 的操作由值班人记录开始/结束时间：

```sh
cd /APP/DockerData/octop-aj
docker compose --env-file config/.env -f docker-compose.opengauss-aj.yml up -d
docker ps --filter name=octop-opengauss-aj
docker inspect --format '{{.Config.Image}} {{json .NetworkSettings.Networks}} {{json .Mounts}}' octop-opengauss-aj
curl --fail http://<bridge-gateway>:18088/api/health
```

确认迁移只发生于新 `octop` 库，完成首次管理员设置并重启一次，验证登录、配置和数据目录持久化。备份 Nginx 配置后追加路由：

```nginx
location = /octop { return 308 /octop/; }
location ^~ /octop/ {
    proxy_pass http://<bridge-gateway>:18088/;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Host $http_host;
    proxy_buffering off;
    proxy_read_timeout 3600s;
}
```

先运行 Nginx 配置测试，通过后才 reload。确认 TLS 已由审核过的入口终止，再允许用户登录。

## 5. 功能、备份与恢复验收

- 健康检查、静态资源、`/octop/` 子路径、API、OIDC 登录和 cookie 路径。
- 新建 Agent、保存/读取 Memory、重启后继续读取；检查 Memory 与 checkpoint 只在新 `octop` 数据库中。
- 使用第三方 embedding 完成知识库上传、检索和引用验证；确认原文及 `index.sqlite` 落在 `/APP/DockerData/octop-aj/data/knowledge/`。不安装本地 embedding 或 DataVec。
- 仅在连接器已配置且业务方安排测试时执行真实 MCP 调用，单独记录只读/写入用例和结果。
- 使用匹配版本 `gs_dump`/`gs_restore` 备份恢复新数据库；同时备份完整 Octop 数据目录、`.env`（按密钥材料保护）和 Nginx 配置。SQLite 备份须暂停写入或采用一致性方式并包含 WAL。
- 恢复至隔离目标后重新验证登录、Memory 和知识库；记录归档摘要、恢复结果及数据完整性。

## 6. 回滚

恢复 Nginx 原配置并测试，通过后 reload；停止 Octop 应用容器并保留数据库、数据目录和镜像供调查。若 migration 已运行且需回到旧数据库状态，只能按经验证的备份恢复新 `octop` 数据库。不得删除或覆盖现有 openGauss 实例、现有数据库、其他容器或卷。

## 本次发布记录

日期：2026-10-28（Asia/Shanghai）  
状态：数据库、镜像导入、Compose、`.env` 和独立数据目录准备完成；正式 Octop 应用尚未启动，Nginx 尚未修改。

| 项目 | 记录 |
| --- | --- |
| 镜像源码分支 / 提交 | `main` / `ee6f5c7d3e88b6830c17646e63a6706944886881`，从干净工作区构建 |
| 远端预检与数据库 | 独立 `octop` 角色和数据库已创建，使用正式镜像及实际 Compose 配置只读连接成功；服务版本 6.0.5 build `5ba692ab`，`public` 基础表为 0；TLS 终止位置待确认 |
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
| 生产数据库 / 容器 / Nginx | 独立 `octop` 库和账号可连接；临时驱动容器已自动删除，正式应用未启动；现有 openGauss/Nginx 未重启，未在 `plat` 库执行 Octop DDL/DML |
| 验收 / 备份恢复 | 未执行 |

构建补充：首次使用 `octop-amd64-builder` 时因 Harbor 证书只含 DNS SAN、而 Dockerfile 用 IP 地址访问而失败；没有关闭 TLS 校验。第二次使用 `desktop-linux` 构建，复用了本机已有的 openGauss 6.0.5 缓存镜像作为客户端文件来源。远端运行容器的 image ID 与该本地镜像 ID 相同，确认客户端来源与当前数据库镜像文件完全一致；运行中的服务 build 字符串未直接查询。本次没有从生产主机拉取或启动 openGauss 镜像。候选镜像仅作审核；正式镜像已在合并后的 `main` 提交 `ee6f5c7d` 上构建、离线交付并导入，详见正式构建报告。
