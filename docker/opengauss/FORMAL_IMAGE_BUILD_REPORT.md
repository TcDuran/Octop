# Octop openGauss 正式镜像构建报告

## 构建结论

AJ 当前运行的正式镜像基于主分支提交 `ee6f5c7d3e88b6830c17646e63a6706944886881` 构建完成。镜像只包含 Octop 应用和 openGauss 客户端工具，不启动或包含 openGauss 数据库服务；镜像未推送到任何仓库。

部署状态（2026-10-28）：镜像已离线导入 AJ 并运行，Nginx `/octop/` 已接入；健康、schema 和重启持久化及外网入口验收通过。当前为初始化向导，首次管理员、登录后的业务功能与隔离恢复演练尚未验收。2026-10-29 合并的白标品牌提交 `868e7d15` 尚未进入该运行镜像；发布前需要基于新提交重新构建并完成品牌、功能和迁移回归，不能只替换文档或重用旧镜像 ID。

| 项目 | 结果 |
| --- | --- |
| 镜像标签 | `octop-opengauss:main-ee6f5c7d` |
| 镜像 ID | `sha256:906f34bdcb57f3356da1dc0b7d1c823f4f2e410c24ca8f229f302a39b1a6a576` |
| 平台 | `linux/amd64` |
| 镜像大小 | `3,138,821,813` bytes（约 3.14 GB） |
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
  --tag octop-opengauss:main-ee6f5c7d \
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
- 临时核对容器已退出，未留下运行中的 Octop 容器。

## 交付与部署约束

2026-10-28 已通过 Spug 文件管理器将镜像离线归档上传至 `10.51.49.8`，完成分片及完整归档校验，并导入远端 Docker。镜像 ID 与本地一致；正式应用已启动并通过健康、schema 和受控重启持久化验证。首次启动发现独立 `octop` 库的 `public` schema 未授予运行账号 CREATE 权限，已仅在该库授予 `USAGE, CREATE` 后成功初始化。生产 Compose 已固定为该本地镜像 ID，设置 `pull_policy: never`。部署步骤、数据库边界、备份恢复和回滚要求见 [AJ_DEPLOYMENT_RUNBOOK.md](AJ_DEPLOYMENT_RUNBOOK.md)。

## 离线归档上传记录

| 项目 | 记录 |
| --- | --- |
| 目标主机 | `10.51.49.8` |
| 远端目录 | `/APP/DockerData/octop-aj/releases/main-ee6f5c7d-20261028/`，权限 `0700` |
| 归档文件 | `octop-opengauss-main-ee6f5c7d-20261028.tar.gz`，权限 `0600` |
| 归档大小 | `1,213,336,901` bytes（约 1.13 GiB） |
| 归档 SHA-256 | `99bd2891371da4e5ef8b1ad165afbc1cfe57421c07acf6c4c7c5dadd98737652` |
| 分片方式 | 每片 32 MiB，共 37 片，文件后缀 `.part000` 至 `.part036` |
| 校验清单 | `SHA256SUMS.parts`、`SHA256SUMS.archive`，均已上传 |
| 校验结果 | 37 片 SHA-256 全部通过；合并后 SHA-256 与本地一致；`gzip -t` 通过 |
| 远端 Docker 操作 | 已执行 `docker load`，ID 与本地一致；应用 health/schema/重启持久化通过；Nginx 已追加 `/octop/` 路由并 reload，外网入口验收通过，首次管理员与业务验收待完成 |

本地使用 `docker save octop-opengauss:main-ee6f5c7d | gzip -1` 生成归档后分片，再使用 Spug 的上传文件入口逐个上传到上述独立目录。远端校验、合并命令如下；仅适用于本次文件集，已有完整归档时不得重复覆盖：

```sh
cd /APP/DockerData/octop-aj/releases/main-ee6f5c7d-20261028
sha256sum --quiet -c SHA256SUMS.parts
# 所有分片校验成功后再执行；noclobber 防止覆盖已有归档。
(set -C; cat octop-opengauss-main-ee6f5c7d-20261028.tar.gz.part[0-9][0-9][0-9] > octop-opengauss-main-ee6f5c7d-20261028.tar.gz)
chmod 600 octop-opengauss-main-ee6f5c7d-20261028.tar.gz
sha256sum -c SHA256SUMS.archive
gzip -t octop-opengauss-main-ee6f5c7d-20261028.tar.gz
```

以上步骤已成功执行；分片和校验清单暂保留在同一发布目录，供后续核对。上传和合并仅新增本次发布文件，没有修改现有数据库、应用容器或 Nginx 配置。

## 镜像导入与配置准备结果

2026-10-28 再次核对归档 SHA-256 后执行 `docker load -i`，导入 `octop-opengauss:main-ee6f5c7d`。远端报告相同 image ID、`linux/amd64`，默认运行用户为空（root）；远端 Docker 26.0.2 报告大小为 `3,138,882,839` bytes，与本地 Docker 的存储大小统计略有差异，镜像身份以 image ID 为准。

Compose 已配置 `network_mode: bridge`，避免默认 `bridge` 不支持 Compose 服务别名的问题。远端已通过 `docker compose config -q`，且从渲染结果核对了镜像 ID、`pull_policy: never`、数据库名和账号 `octop`、数据库端点、数据卷及仅绑定 `172.17.0.1:18088`。启动后 schema watermark 为 25、public 基础表 36 张、管理员 0；首次启动的 schema ACL 修复仅作用于独立 `octop` 数据库。
