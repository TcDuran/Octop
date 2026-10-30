# openGauss 部署与安装包

## 文档入口与 AJ 部署状态

| 文档 | 用途 |
| --- | --- |
| [AJ 部署方案](DEPLOYMENT_AJ_CN.md) | 容器、数据库、网络和存储边界 |
| [AJ 发布手册](AJ_DEPLOYMENT_RUNBOOK.md) | 后续环境可复用的准备、部署、验证与回滚步骤 |
| [AJ 操作记录](AJ_DEPLOYMENT_RECORD.md) | 本次实际执行结果、权限修复、备份路径和待验收项目 |
| [AJ OA OIDC / MCP 分析](AJ_OA_OIDC_MCP_ANALYSIS.md) | 接入配置、各层白名单、已实施的网络转发和待完成的业务对接 |
| [AJ OA OIDC 配置审核稿](AJ_OA_OIDC_CHANGE_REVIEW.md) | OIDC、OA 内嵌、OA/BPM 上游 origin、只读工具登记体及回滚的历史审核模板；实际结果见操作记录 |
| [正式镜像报告](FORMAL_IMAGE_BUILD_REPORT.md) | 源码提交、镜像 ID、构建参数与离线分片交付校验 |
| [产品验证入口](validation/README.md) | 隔离测试库中的知识库、Memory 和恢复验证 |

截至 2026-10-29：AJ 已更新为 `octop-opengauss:main-b46b9722`，包含白标提交 `868e7d15`。外网入口 `http://211.137.76.3:8081/octop/` 显示“PLAT 助手”登录页；17 项资源/API 检查、8 个品牌文件逐一比对和 health 均通过，schema 25（36 张表）保持不变。线上已有 1 个管理员，health 显示 1 个运行中的 Agent；本轮未创建或重置账号。登录后的聊天/WebSocket、Memory、知识库、业务 MCP 和隔离恢复演练仍待验收。Nginx、openGauss 未重启，旧镜像和回滚配置保留。TLS 终止位置仍待确认，入口检查不代表完整业务验收。

OA OIDC / MCP 接入进度：2026-10-29 已按用户批准配置仅 Octop 来源的公网 `211.137.76.3:8081` 至内网 `10.51.49.8:8081` 转发，增加宿主机运维脚本与 systemd timer 维护规则；没有修改应用业务代码或镜像。OA OIDC、Octop provider/connector、内嵌票据和 OA 只读身份 MCP 已完成实机验收；BPM 仅加入 allowed-origins，未登记业务工具。详见[发布手册第 9 节](AJ_DEPLOYMENT_RUNBOOK.md#9-oa-oidc--只读-mcp-配置已实施)、[实际操作记录](AJ_DEPLOYMENT_RECORD.md#2026-10-29-oidc-实际配置与验收)和 [OIDC / MCP 分析](AJ_OA_OIDC_MCP_ANALYSIS.md)。

2026-10-30：OA OAuth2 客户端 `octop` 的访问令牌和刷新令牌有效期已调整为 `1576800000` 秒（约 50 年）。这只影响调整后新签发的令牌；Octop 已保存的旧 `X-Plat-Token` 不会回溯延长，调整后需重新完成一次 Plat/OA → Octop 登录。内嵌 `embed-ticket` 仍保持约 60 秒、一次性消费。

当前发布仍有三项需要单独确认的安全边界：公网入口是否由 TLS 终止；openGauss 账号的 `SYSADMIN` 实例级权限和现有 MD5 口令摘要是否符合生产安全要求；50 年 OA token 的撤销、轮换和异常账号处置流程。它们不由 `/api/health` 或容器 healthy 自动证明。

## AJ 数据库账号权限

`octop` 已于 2026-10-29 按用户授权执行 `ALTER ROLE octop SYSADMIN`，用于解决 openGauss 专家启动时设置 `lockwait_timeout` 被拒绝的问题。它现在具有实例级系统管理权限，不能再描述为仅有独立库权限的普通账号。核验字段是 `pg_roles.rolsystemadmin`（AJ 为 `t`），不是仅检查 `rolsuper`（AJ 仍为 `f`）。

本次没有修改应用代码或镜像、全局数据库参数，也没有重启 Octop、openGauss 或 Nginx。实际账号参数设置、专家 `main` 启动（`running`、无 last_error）及 openGauss Memory 存储检查均通过。后续同类部署的授权、验证和撤销步骤见 [AJ 发布手册](AJ_DEPLOYMENT_RUNBOOK.md#agent-memory-启动所需的-sysadmin-授权aj-已实施)，执行记录见 [AJ 操作记录](AJ_DEPLOYMENT_RECORD.md#2026-10-29-octop-账号-sysadmin-授权)。完整聊天及业务回归仍按原验收清单进行。

## 存储边界

- 控制面、Memory 关系数据和会话检查点使用 openGauss。
- 知识库原文及每库 index.sqlite 保留在 OCTOP_HOME/knowledge/；向量生成使用配置的第三方 embedding。
- 不需要 DataVec；本方案没有接入原生 openGauss 知识库向量索引。
- baseline22 保持不变；启动时顺序执行 openGauss 专用 023–025，将结构升级到当前版本。
- openGauss 首次初始化入口在应用启动迁移器中：使用镜像内的 `022_baseline.sql`，随后按顺序执行 `src/octop/infra/db/opengauss/migrations/023_*.sql` 至当前版本；不单独上传或执行 PostgreSQL 的 001–022 历史迁移，也不在 `plat` 库运行这些 SQL。
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

Docker 部署使用不可变镜像归档，不需要把 wheel 放进生产容器。每次正式交付的最小文件集应包括：镜像 tar.gz 及分片校验清单、`docker-compose.opengauss-aj.yml`、对应源码提交和镜像 ID 记录、`docker/opengauss` 发布手册/操作记录，以及经审核的 `operations/` 运维文件。生产 `config/.env`、`oidc-aj-client.env`、OIDC 私钥、数据库备份和业务数据只留在受限现场目录，不进入归档、Git 或上传目录。

## 升级与备份

升级前停止业务写入，使用系统备份保留数据库和完整 OCTOP_HOME。只执行 gs_dump 不会包含知识库 SQLite 索引和原文。

先在独立临时库验证升级和恢复，再升级业务库。不要把已有 PostgreSQL 数据库直接标记为 openGauss baseline，也不要在 plat 或其他共享业务库执行验证脚本。

恢复演练必须使用新库名、新数据目录和临时应用端口，不能覆盖线上 `octop` 库或 `/APP/DockerData/octop-aj/data`：使用匹配版本的 `gs_dump` 导出数据库并执行 `sha256sum`、`gs_restore --list` 校验；在一致性窗口复制完整 `data` 目录（包括 knowledge 原文、`index.sqlite` 和 WAL）。由管理员创建带时间后缀的隔离库，使用 `gs_restore --exit-on-error` 恢复；复制 Compose 为临时文件，使用隔离库、临时容器名和未占用 loopback 端口，验证 health、schema、Memory/checkpoint、知识库检索和只读 MCP。完成后停止临时实例，确认无连接后删除隔离库和临时目录，并保留脱敏审计结果。
