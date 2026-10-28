# Octop openGauss 正式镜像构建报告

## 构建结论

正式镜像已基于主分支提交 `ee6f5c7d3e88b6830c17646e63a6706944886881` 构建完成。镜像只包含 Octop 应用和 openGauss 客户端工具，不启动或包含 openGauss 数据库服务；镜像未推送到任何仓库。

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

本报告对应的镜像仍只存在于构建机。交付到部署环境时，应采用经过审核的镜像仓库或离线归档方式，并在传输两端校验 SHA-256；生产 Compose 使用不可变镜像引用，不使用 `latest`。部署步骤、数据库边界、备份恢复和回滚要求见 [AJ_DEPLOYMENT_RUNBOOK.md](AJ_DEPLOYMENT_RUNBOOK.md)。
