# openGauss 部署与安装包

## 存储边界

- 控制面、Memory 关系数据和会话检查点使用 openGauss。
- 知识库原文及每库 index.sqlite 保留在 OCTOP_HOME/knowledge/；向量生成使用配置的第三方 embedding。
- 不需要 DataVec；本方案没有接入原生 openGauss 知识库向量索引。
- baseline22 保持不变；启动时顺序执行 openGauss 专用 023–025，将结构升级到当前版本。
- openGauss 6.0.5 不支持 ADD COLUMN IF NOT EXISTS；新增列使用受版本水位和事务控制的专用迁移，不直接复制 PG 历史 SQL。

## 源码与 Docker

源码安装执行 uv sync --frozen。Docker 使用 docker/docker-compose.opengauss.yml，其容器名为 octop-opengauss，只绑定 127.0.0.1:18088，数据目录独立。凭证通过忽略的环境文件载入，不写入镜像或仓库。

opengauss-runtime 构建目标包含 6.0.5 的 gs_dump/gs_restore。当前客户端基础镜像位于内部 Harbor，构建机器需要有权访问该镜像。Dockerfile 的默认目标仍为普通 runtime。

## Python wheel 成对交付

Octop 依赖定制的 octop-memory==1.0.2+opengauss.2；该版本没有发布到公共 PyPI。发布方必须同时交付两个 wheel，不能只发送 Octop wheel。

在仓库根目录执行：

    bash docker/opengauss/build-dist.sh

输出目录为 dist/opengauss，也可将输出路径作为脚本第一个参数。先完成 dashboard 构建，再构建实际发布包；单独构建 wheel 不生成前端资源。

在干净 Python 3.12+ 环境中安装两个明确的文件：

    python -m pip install /path/octop-1.0.3b1-py3-none-any.whl /path/octop_memory-1.0.2+opengauss.2-py3-none-any.whl

该方式仍需从配置的软件源取得其他第三方依赖。完全离线交付时，须在与目标系统匹配的联网构建环境下载完整依赖到 wheelhouse，再在目标执行：

    python -m pip install --no-index --find-links /path/wheelhouse octop==1.0.3b1

不直接执行公共 PyPI 上的 pip install octop 来获取此内部适配版本。若以后改为发布定制依赖，需要重新验证解析结果及安装包，不能只修改文档中的版本号。

## 升级与备份

升级前停止业务写入，使用系统备份保留数据库和完整 OCTOP_HOME。只执行 gs_dump 不会包含知识库 SQLite 索引和原文。

先在独立临时库验证升级和恢复，再升级业务库。不要把已有 PostgreSQL 数据库直接标记为 openGauss baseline，也不要在 plat 或其他共享业务库执行验证脚本。
