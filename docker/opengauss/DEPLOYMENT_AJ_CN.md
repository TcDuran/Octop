# AJ 环境 Octop 部署方案

状态：数据库、当前正式镜像、Compose、`.env` 和数据目录已完成；应用健康、schema 和重启持久化验证通过，Nginx `/octop/` 路由已接入并完成外网入口验收。当前页面为受密码保护的初始化向导，首次管理员设置和完整业务验收待执行。GitHub `main` 已有更新的白标品牌提交，但当前 AJ 镜像尚未包含该提交，需重新构建并完成发布前验证。操作与备份路径见操作记录和发布手册。

## 方案结论

只新增一个 Octop 应用容器 `octop-opengauss-aj`，复用主机上现有 openGauss 服务，不新增或替换 openGauss 容器。由现有数据库管理员在该实例中新建数据库 `octop` 和同名、非超级用户业务账号 `octop`；Octop 只连接这个新数据库。现有数据库和表内容不做 DDL/DML 修改。

Agent Memory 的关系记忆和 checkpoint 继续使用现有 openGauss 后端；当前不使用 Memory 向量检索，因此不需要 embedding 模型或 DataVec。知识库向量仍保存在 SQLite sidecar。部署不执行 `CREATE EXTENSION datavec`，也不制作 openGauss 派生镜像。

## 存储边界

| 数据 | 存放位置 |
| --- | --- |
| 用户、Agent、连接器、知识库等控制面元数据，以及 Agent Memory 和 checkpoint | 现有 openGauss 实例中的新 `octop` 数据库 |
| 知识库原文 | `/APP/DockerData/octop-aj/data/knowledge/<kb_id>/docs/` |
| 知识库分块和第三方模型生成的向量 | `/APP/DockerData/octop-aj/data/knowledge/<kb_id>/index.sqlite` |
| Agent 工作区、配置和其他 Octop 文件 | `/APP/DockerData/octop-aj/data/` |

知识库使用第三方 embedding 模型，不下载或安装本地模型；本次 AJ 仍需在首次设置后配置业务模型和 embedding，尚未完成知识库验收。Octop 在 openGauss 控制面下默认将 Agent Memory 和 checkpoint 存入该控制面数据库，符合本方案，无需为 Memory 增加代码配置。

## 主机与连接前置条件

2026-10-28 经 Spug Web 终端完成主机与容器预检：Kylin Linux Advanced Server V10 (Halberd)、x86_64、8 核、约 30 GiB 内存（约 17 GiB 可用），`/APP` 196 GiB（可用 157 GiB），Docker Server 26.0.2。远端独立 `octop` 数据库已创建，应用驱动连接验证成功。开始应用部署前仍需复核资源和容器状态。

新增 Octop 数据目录为 `/APP/DockerData/octop-aj/data`，不复用 `/APP/DockerData/opengauss/test`。现有 openGauss 容器及其数据目录、配置、监听端口保持原样。

只读检查确认现有数据库容器为 `opengauss_test`，镜像标签 `opengauss:6.0.5`，运行容器 image ID 为 `sha256:ae937cb57c350ee69df21fff0dbea8db0122fd79f6128d5bddf65317cd945e55`，挂载 `/APP/DockerData/opengauss/test:/var/lib/opengauss`；默认 `bridge` 网关为 `172.17.0.1`，容器 IP 为 `172.17.0.2`，宿主机映射 `15434 -> 5432`。Nginx 容器同处默认 bridge，地址 `172.17.0.6`。默认 bridge 不应依赖容器名 DNS；后续方案使用 `172.17.0.1:15434` 连接，并让 Octop 的 `18088` 仅绑定 `172.17.0.1` 供 Nginx 访问。Octop 到数据库的 TCP 连通性已通过正式镜像和实际 Compose 配置只读验证。不得使用可能变化的数据库容器 IP，不得为此重建或重启现有 openGauss 容器，也不得新增公开数据库端口。

Nginx 容器镜像为 `nginx:1.31.3`，配置目录从 `/APP/DockerData/nginx/conf` 挂载，主配置从 `/APP/DockerData/nginx/nginx.conf` 挂载。宿主机 `8081 -> Nginx 80` 提供 HTTP。2026-10-28 已在 `http_aj.conf` 中追加 `/octop/` 路由，并经配置测试后 reload；外部入口为 `http://211.137.76.3:8081/octop/`。Octop 仅监听 bridge 网关 `172.17.0.1:18088`，Nginx 到应用、应用到数据库和外网健康检查均通过；原 Spug/OA/BPM 响应保持一致。TLS 终止位置仍待确认，首次管理员与业务验收待完成。

## 配置内容

Compose、现场连接参数、权限受限的 `.env` 和正式镜像已准备并核验，Nginx 路由已按下述配置边界实施：

1. `docker/docker-compose.opengauss-aj.yml` 只定义 Octop 服务，连接现有 openGauss 的新 `octop` 数据库和账号。Compose 中不定义 openGauss 服务、不挂载或更改现有数据库目录。数据库网络名、地址和 Nginx 可达的 bridge gateway 地址须由现场只读检查确认。
2. 将数据库口令保存在权限为 `0600` 的部署 `.env` 文件中，不纳入 Git。口令会作为环境变量传入容器，并可被有 Docker 管理权限的用户通过容器配置查看；若要求避免这一点，需要另行实现并审核 secret-file 读取支持。
3. 编译不可变 Octop 镜像，前端构建设置 `VITE_BASE_PATH=/octop/`，关闭本地 embedding 安装，验证品牌资源和子路径资源后再记录镜像摘要。构建使用审核过的干净提交，不在生产机现场构建。
4. 备份现有 Nginx 配置后增加 `/octop/` 代理；不覆盖整份配置。Octop 端口只允许代理入口访问，不对外公开数据库端口。

新数据库已由管理员在现有 openGauss 实例上创建。角色只拥有新 `octop` 数据库所需权限，没有超级用户、建库或建角色权限；已通过驱动连接确认认证成功且当前库为 `octop`。该操作只新增角色和数据库对象；没有对现有业务数据库或表执行初始化、升级、测试 SQL。应用启动后的 Octop migrations 仅作用于新 `octop` 数据库。

## 部署步骤

以下是供后续环境参考的部署顺序。本次数据库、应用和 Nginx 接入已完成，详见 [AJ_DEPLOYMENT_RECORD.md](AJ_DEPLOYMENT_RECORD.md)；不得在当前环境重复创建或重置账号、覆盖已有配置：

1. 只读确认现有 openGauss 容器、版本和镜像摘要、数据库连接网络/端口、磁盘和内存；确认 Octop 容器名、`18088` 端口和 `/APP/DockerData/octop-aj/data` 未被占用。新环境按发布手册创建独立库/账号并核验 `public` schema 的 USAGE/CREATE；当前 AJ 库已完成，不再重复建库建角色。
2. 创建 Octop 独立数据目录和权限受限的配置文件，部署单服务 Compose 与已审核的镜像；运行 Compose 配置检查后再启动 Octop 容器。
3. 检查 Octop 健康状态、迁移水位和 `/data/.octop` 持久化挂载，执行一次受控重启，验证健康和向导文件持久化。
4. 备份 Nginx 配置并追加 `/octop/` 路由。测试 Nginx 配置后 reload，确认静态资源从 `/octop/` 加载且 API 健康检查成功。
5. 通过初始化向导创建管理员，配置业务模型、第三方 embedding 及所需连接器，再验收登录、聊天、Memory 和知识库；首次设置完成后继续验证业务数据重启持久化。

Nginx 路由建议如下；`172.17.0.1` 部署前须按现场 Docker 网络核实：

```nginx
location = /octop { return 308 /octop/$is_args$args; }
location ^~ /octop/ {
    proxy_pass http://172.17.0.1:18088/;
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

## 验收

1. 确认只有新增的 Octop 应用容器；现有 openGauss 容器未重建、未重启，现有数据目录和业务数据库目录摘要不变。
2. 确认 Octop 成功连接新 `octop` 数据库并完成初始化；应用迁移和 CRUD 验证均限定在该新库。
3. 新建 Agent，实际保存一条 Memory，确认记忆关系和 checkpoint 写入新 `octop` 数据库；重启后继续读取，并确认这些表只存在于新库。
4. 配置第三方 embedding，上传知识库文档，使用 Agent 检索并核对引用；检查 `knowledge/<kb_id>/index.sqlite` 的完整性和落盘位置。
5. 通过 `/octop/` 验证登录、静态资源、API、知识库检索和 Memory；不把本地模拟测试当作真实部署验收。
6. 备份恢复演练：对新 `octop` 数据库执行匹配版本的 `gs_dump`/`gs_restore`，确认 Agent Memory 和 checkpoint 恢复；同时备份完整 Octop 数据目录，其中包含知识库原文和 SQLite 索引。恢复到隔离目标后重新验证登录、Memory 和知识库检索。
7. Compose 配置解析通过；镜像通过品牌、`/octop/` 子路径和健康检查验证。

真实业务 MCP 调用只在对应连接器已配置且测试流程已确认时执行，不纳入基础启动探针。

## 备份与回滚

备份范围包括新 `octop` 数据库、`/APP/DockerData/octop-aj/data`、数据库账号口令配置文件和当前 Nginx 配置。`gs_dump` 负责 Agent Memory 和 checkpoint；Octop 数据目录备份负责知识库 SQLite sidecar 及其他本地文件。备份 SQLite 前暂停 Octop 写入或使用一致性备份方式，避免漏掉 WAL 文件。

回滚时恢复 Nginx 配置并停止新增 Octop 容器。保留新 `octop` 数据库和 Octop 数据目录供排查；不删除、不重建、不恢复覆盖现有 openGauss 容器和数据目录。不得执行会移除数据卷的 `docker compose down -v`。

## 放行条件

应用、数据库、Nginx 路由和基础入口验收已实施，备份及验证证据见操作记录。首次管理员设置、登录、业务验收和隔离备份恢复演练尚未完成；TLS 终止位置仍待确认。
