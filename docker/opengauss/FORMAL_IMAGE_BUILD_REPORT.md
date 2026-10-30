# Octop openGauss 正式镜像构建报告

## 构建结论

本次已发布正式镜像基于主分支提交 `b46b9722` 构建完成。镜像只包含 Octop 应用和 openGauss 客户端工具，不启动或包含 openGauss 数据库服务；镜像未推送到任何仓库。

部署状态（2026-10-29）：AJ 已运行 `main-b46b9722` 新镜像，包含白标提交 `868e7d15`。容器 healthy，公开登录页标题为“PLAT 助手”，17 项资源/API 检查通过，8 个品牌文件与本地镜像逐一校验一致，浏览器未报告脚本错误。独立 `octop` 库仍为 schema 25、36 张 public 表，已有 1 个用户/管理员，health 显示 1 个运行中的 Agent；本轮未创建、重置账号或执行业务写操作。登录后的完整业务和隔离恢复演练尚未验收。

| 项目 | 结果 |
| --- | --- |
| 镜像标签 | `octop-opengauss:main-b46b9722` |
| 镜像 ID | `sha256:b283255f0889e739d667c79d74429670f13c285d352396bf190ede19a2ce0223` |
| 平台 | `linux/amd64` |
| 镜像大小 | `3,134,870,544` bytes（约 3.13 GB） |
| Dockerfile target | `opengauss-runtime` |
| 前端路径 | `VITE_BASE_PATH=/octop/` |
| 本地 embedding | `INSTALL_LOCAL_EMBEDDING=false`，未安装 `fastembed` |
| openGauss 客户端 | 6.0.5 build `5ba692ab`，包含 `gs_dump` 和 `gs_restore` |
| psycopg | 3.3.4 |
| 仓库摘要 | 无；本次未执行 `--push` |

## 构建命令

在仓库 `main` 分支、工作区干净状态下执行：

```sh
docker buildx build \
  --builder desktop-linux \
  --platform linux/amd64 \
  --target opengauss-runtime \
  --build-arg VITE_BASE_PATH=/octop/ \
  --build-arg INSTALL_LOCAL_EMBEDDING=false \
  --tag octop-opengauss:main-b46b9722 \
  --load \
  -f docker/Dockerfile .
```

构建使用本机已缓存的 `10.24.1.80:2443/library/opengauss:6.0.5` 作为客户端文件来源，复制的内容仅为 `gs_dump`、`gs_restore` 及其运行库。没有从生产主机拉取或启动 openGauss 镜像。

## 构建后核对

使用临时容器执行以下核对，容器执行完即删除，没有数据卷和端口映射：

- `import octop` 成功，`psycopg` 版本为 3.3.4。
- `gs_dump --version` 与 `gs_restore --version` 均为 openGauss 6.0.5 build `5ba692ab`。
- dashboard 产物包含 `/octop/` 路径资源。
- `fastembed` 不存在，镜像未安装本地 embedding 模型。
- 品牌验收通过：标题、manifest、静态品牌资源和构建后的 JS 均包含 `PLAT 助手`，未发现上游 Octop 品牌标题。
- 临时核对容器已退出，未留下运行中的 Octop 容器。

## 交付与部署约束

历史镜像 `main-ee6f5c7d` 的 AJ 运行记录保留在下文。本次新镜像已使用独立发布目录完成上传、校验和导入，确认 image ID 一致、备份现有配置后受控替换应用容器；旧镜像及旧发布目录保留用于回滚。部署步骤、数据库边界、备份恢复和回滚要求见 [AJ_DEPLOYMENT_RUNBOOK.md](AJ_DEPLOYMENT_RUNBOOK.md)。

## 离线归档上传记录

| 项目 | 记录 |
| --- | --- |
| 目标主机 | `10.51.49.8` |
| 远端目录 | `/APP/DockerData/octop-aj/releases/main-b46b9722-20261029/`，权限 `0700` |
| 归档文件 | `octop-opengauss-main-b46b9722-20261029.tar.gz`，权限 `0600` |
| 归档大小 | `1,209,496,010` bytes（约 1.13 GiB） |
| 归档 SHA-256 | `fb28da2daa0e80801fffb92f888a8906882182850bcf55edacd874900fbd5f96` |
| 分片方式 | 每片 32 MiB，共 37 片，后缀 `.part000` 至 `.part036` |
| 校验清单 | 已上传并验证：`SHA256SUMS.parts`、`SHA256SUMS.archive` |
| 校验结果 | 本地和远端 37 片校验通过，合并归档 SHA-256 一致，`gzip -t` 通过；历史 `main-ee6f5c7d-20261028` 归档保留 |
| 远端 Docker 操作 | 已完成 `docker load`、image ID 核对、Compose 配置校验和受控替换；容器 running/healthy |

本地使用 `docker save octop-opengauss:main-b46b9722 | gzip -1` 生成归档后分片，再使用 Spug 的上传文件入口逐个上传到上述独立目录。远端校验、合并命令如下；仅适用于本次文件集，已有完整归档时不得重复覆盖：

```sh
cd /APP/DockerData/octop-aj/releases/main-b46b9722-20261029
sha256sum --quiet -c SHA256SUMS.parts
# 所有分片校验成功后再执行；noclobber 防止覆盖已有归档。
(set -C; cat octop-opengauss-main-b46b9722-20261029.tar.gz.part[0-9][0-9][0-9] > octop-opengauss-main-b46b9722-20261029.tar.gz)
chmod 600 octop-opengauss-main-b46b9722-20261029.tar.gz
sha256sum -c SHA256SUMS.archive
gzip -t octop-opengauss-main-b46b9722-20261029.tar.gz
```

以上步骤已成功执行；分片和校验清单暂保留在同一发布目录，供后续核对。上传和合并仅新增本次发布文件，没有修改现有数据库、应用容器或 Nginx 配置。

## 2026-10-29 更新结果

远端 Docker 报告 image ID 与本地一致、amd64、大小 `3,134,931,570` bytes；两端大小统计差异不影响 image ID。Compose 保留 `pull_policy: never`、默认 bridge 和 `172.17.0.1:18088` 绑定，仅更新镜像引用。受控重建返回码为 0，应用重建后的数据库与 Agent 加载正常。

更新前配置和文件副本在 `backups/pre-main-b46b9722-20261029/`，包括旧 image ID、旧 Compose、受限 `env` 和文件数据副本。文件副本是在服务运行时复制，未验证为 SQLite 一致性恢复点。本轮更新前没有重新执行数据库全量备份；更新后补充的 `backups/post-main-b46b9722-20261029/octop-verified.dump` 为 `116,056` bytes，SHA-256 `bdce701edbc6096f0c7014294a9b75d7dcc70bccd829363233fc29eebfc3bcfe`，权限 `0600`，已用 6.0.5 `gs_restore -l` 检查目录；未执行恢复，不将其作为更新前数据库恢复点。

Nginx 和 openGauss 启动时间分别仍为 `2026-10-12T01:55:52.518555208Z`、`2026-10-09T06:51:35.736230935Z`。本轮未修改 Nginx 配置，未在 `plat` 库执行 DDL/DML；Spug/OA/BPM 外网入口均返回 200（仅入口检查）。未进行第二次重启，以保留当前业务运行状态；本轮受控重建已验证启动和持久数据加载。完整过程见 [操作记录](AJ_DEPLOYMENT_RECORD.md#2026-10-29-白标镜像更新)。

## 历史：2026-10-28 镜像导入与配置准备结果

2026-10-28 再次核对归档 SHA-256 后执行 `docker load -i`，导入 `octop-opengauss:main-ee6f5c7d`。远端报告相同 image ID、`linux/amd64`，默认运行用户为空（root）；远端 Docker 26.0.2 报告大小为 `3,138,882,839` bytes，与本地 Docker 的存储大小统计略有差异，镜像身份以 image ID 为准。

Compose 已配置 `network_mode: bridge`，避免默认 `bridge` 不支持 Compose 服务别名的问题。远端已通过 `docker compose config -q`，且从渲染结果核对了镜像 ID、`pull_policy: never`、数据库名和账号 `octop`、数据库端点、数据卷及仅绑定 `172.17.0.1:18088`。启动后 schema watermark 为 25、public 基础表 36 张、管理员 0；首次启动的 schema ACL 修复仅作用于独立 `octop` 数据库。

## 2026-10-30 ODC 镜像发布记录

本轮构建并发布普通 PostgreSQL 镜像 `octop:odc-20261030-21462180-amd64` 至 ODC 主机 `221.232.69.57`。该镜像基于应用提交 `21462180`；其后 `e74415ec` 仅补充发布 SOP 文档，不改变镜像内容。部署保留现有 `/apps/octop/data`、PostgreSQL、Nginx 和回环端口配置。

| 项目 | 结果 |
| --- | --- |
| 应用源码提交 | `21462180` (`feat: finalize AJ openGauss OIDC deployment package`) |
| 发布记录文档提交 | `e74415ec`（仅更新文档） |
| 镜像标签 | `octop:odc-20261030-21462180-amd64` |
| 镜像 ID | `sha256:2101b0dbff5686e92b8ba12685b2dad165ab2bd79630f05507fce65b80ae6938` |
| 平台与大小 | `linux/amd64`，约 3.03 GB |
| 构建参数 | 正式 `docker/Dockerfile`；`VITE_BASE_PATH=/octop/`；`INSTALL_LOCAL_EMBEDDING=false`；腾讯 Debian mirror |
| 本地应用核验 | 临时 amd64 容器 `/api/health` 返回 `ok=true`；空数据卷下 `db=false`；临时容器已清理 |
| 交付归档 | `/tmp/octop-release-21462180/octop-odc-20261030-21462180-amd64.tar.gz`，约 1.1 GB |
| 归档 SHA-256 | `6c9e44608b7223af93b47889f94bd0168123b1b6dea40a941cc5fc06efa192dc` |
| 远端交付目录 | `/apps/octop/releases/21462180/`；35 个 32 MiB 分片逐片校验通过，远端 `gzip -t` 和完整 SHA-256 通过 |
| 远端镜像核验 | `docker load` 成功；远端 image ID 与本地一致，平台 `linux/amd64` |
| 更新前备份 | Compose、`.env`、容器和镜像 inspect 信息、健康基线、PostgreSQL custom dump（8.7 MB）及 `pg_restore --list` 输出保存在 `pre-deploy-backup/` |
| 部署结果 | `octop-odc` 已切换新镜像并 healthy；`/api/health` 为 `ok=true, db=true, users_loaded=18, agents_running=15` |
| 外部入口 | `/octop/` 返回 200，页面标题 `PLAT 助手`；`/octop` 仍返回 308 到 `/octop/` |
| 保留状态 | 旧镜像保留；PostgreSQL、Nginx、BPM、PLAT 容器未重启；数据挂载仍为 `/apps/octop/data`，宿主机端口仍仅绑定 `127.0.0.1:8088` |

本轮构建命令和远端替换步骤见 [AJ_DEPLOYMENT_RUNBOOK.md](AJ_DEPLOYMENT_RUNBOOK.md)。本记录说明镜像技术部署及基础入口验收，不代替 OIDC、登录后 WebSocket、聊天和完整业务场景验收。
