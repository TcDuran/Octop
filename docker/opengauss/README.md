# openGauss 部署与安装包

## 文档入口与 AJ 部署状态

| 文档 | 用途 |
| --- | --- |
| [AJ 部署方案](DEPLOYMENT_AJ_CN.md) | 容器、数据库、网络和存储边界 |
| [AJ 发布手册](AJ_DEPLOYMENT_RUNBOOK.md) | 后续环境可复用的准备、部署、验证与回滚步骤 |
| [AJ 操作记录](AJ_DEPLOYMENT_RECORD.md) | 本次实际执行结果、权限修复、备份路径和待验收项目 |
| [正式镜像报告](FORMAL_IMAGE_BUILD_REPORT.md) | 源码提交、镜像 ID、构建参数与离线分片交付校验 |
| [产品验证入口](validation/README.md) | 隔离测试库中的知识库、Memory 和恢复验证 |

截至 2026-10-29：AJ 的独立 `octop` 数据库和账号、Octop 应用、Nginx `/octop/` 路由已部署。外网入口为 `http://211.137.76.3:8081/octop/`；健康、schema 25（36 张表）、重启持久化、308 跳转和 11 个页面资源验证通过。当前为受密码保护的初始化向导；首次管理员、登录后的聊天/WebSocket、Memory、知识库、业务 MCP 和隔离恢复演练尚未完成。GitHub `main` 已包含白标品牌提交 `868e7d15`，但 AJ 现运行镜像仍为旧提交 `ee6f5c7d`，需重新构建、验证并发布新镜像后才算完成现网品牌更新。TLS 终止位置仍待确认，不能把入口验收视为完整业务验收。

## 存储边界

- 控制面、Memory 关系数据和会话检查点使用 openGauss。
- 知识库原文及每库 index.sqlite 保留在 OCTOP_HOME/knowledge/；向量生成使用配置的第三方 embedding。
- 不需要 DataVec；本方案没有接入原生 openGauss 知识库向量索引。
- baseline22 保持不变；启动时顺序执行 openGauss 专用 023–025，将结构升级到当前版本。
- openGauss 6.0.5 不支持 ADD COLUMN IF NOT EXISTS；新增列使用受版本水位和事务控制的专用迁移，不直接复制 PG 历史 SQL。

## 源码与 Docker

源码安装执行 `uv sync --frozen`。Docker 按用途选择配置：

| 配置 | 用途与端口 | 数据目录 |
| --- | --- | --- |
| [`docker-compose.opengauss-local.yml`](../docker-compose.opengauss-local.yml) | 本地开发，Compose 项目名 `octop-opengauss`，绑定 `127.0.0.1:18088` | 仓库 `data/opengauss` |
| [`docker-compose.opengauss-aj.yml`](../docker-compose.opengauss-aj.yml) | AJ 生产，容器 `octop-opengauss-aj`，绑定 `172.17.0.1:18088`，经现有 Nginx 的 8081 入口访问 | `/APP/DockerData/octop-aj/data` |

两份配置都只运行 Octop，连接各自配置的现有 openGauss。AJ 的账号和配置保存在 `/APP/DockerData/octop-aj/config/.env`，目录 `0700`、文件 `0600`；本地凭证通过忽略的环境文件载入，不写入镜像或仓库。

opengauss-runtime 构建目标包含 6.0.5 的 gs_dump/gs_restore。当前客户端基础镜像位于内部 Harbor，构建机器需要有权访问该镜像。Dockerfile 的默认目标仍为普通 runtime。

该目标仅复制数据库备份恢复客户端及其依赖库，不包含 openGauss 数据库服务。新库初始化前还需检查运行账号对 `public` schema 的 `USAGE/CREATE` 权限；数据库 owner 身份不能替代这项核验，具体命令见发布手册。

## Python wheel 成对交付

Octop 依赖定制的 octop-memory==1.0.2+opengauss.2；该版本没有发布到公共 PyPI。发布方必须同时交付两个 wheel，不能只发送 Octop wheel。

在仓库根目录执行（脚本会自动构建前端、应用并校验 PLAT 品牌，再生成两个 wheel）：

    bash docker/opengauss/build-dist.sh

如果部署在 ODC 的 `/octop/` 子路径，使用 `VITE_BASE_PATH=/octop/ bash docker/opengauss/build-dist.sh`；该变量会传给前端构建并保留在 wheel 中。

输出目录为 dist/opengauss，也可将输出路径作为脚本第一个参数。不要直接执行 `uv build` 代替该脚本，否则可能拿到未应用品牌补丁的旧 dashboard 产物。

在干净 Python 3.12+ 环境中安装两个明确的文件：

    python -m pip install /path/octop-1.0.3b1-py3-none-any.whl /path/octop_memory-1.0.2+opengauss.2-py3-none-any.whl

该方式仍需从配置的软件源取得其他第三方依赖。完全离线交付时，须在与目标系统匹配的联网构建环境下载完整依赖到 wheelhouse，再在目标执行：

    python -m pip install --no-index --find-links /path/wheelhouse octop==1.0.3b1

不直接执行公共 PyPI 上的 pip install octop 来获取此内部适配版本。若以后改为发布定制依赖，需要重新验证解析结果及安装包，不能只修改文档中的版本号。

## 升级与备份

升级前停止业务写入，使用系统备份保留数据库和完整 OCTOP_HOME。只执行 gs_dump 不会包含知识库 SQLite 索引和原文。

先在独立临时库验证升级和恢复，再升级业务库。不要把已有 PostgreSQL 数据库直接标记为 openGauss baseline，也不要在 plat 或其他共享业务库执行验证脚本。
