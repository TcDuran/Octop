# openGauss DataVec 能力核查与离线部署手册

## 适用范围

本文用于 openGauss 6.0.5、x86_64、Docker 部署环境。当前远端共享环境的基础镜像为 `harbor.whodc.cn/library/opengauss:6.0.5`。实际镜像摘要应在部署时重新读取，不能只依据标签。

2026-10-26 最终范围：用户确认知识库保留原有 SQLite sidecar，openGauss 保存业务元数据，不新增数据库向量后端。本手册的 DataVec 核查作为可选能力资料，当前 Octop 部署无需安装 DataVec 扩展或创建向量索引。相关接入试验已撤回，见[保留 SQLite 方案的复验](validation/opengauss-retained-sidecar-20261026.md)。

## 当前环境检查结论

现有 6.0.5 镜像中已经包含：

```text
/usr/local/opengauss/lib/postgresql/datavec.so
/usr/local/opengauss/share/postgresql/extension/datavec.control
```

动态库 SHA-256 为 `3f0fcf8f484f28322ece2f5594bd3ab02b6ecdeb268800e965b8c522f452e499`。

镜像还缺少：

```text
/usr/local/opengauss/share/postgresql/extension/datavec--0.7.2.sql
```

所以 `pg_available_extensions` 能显示 `datavec 0.7.2`，但 `CREATE EXTENSION datavec` 会失败并返回 SQLSTATE 58P01。这说明镜像带有一个不完整的扩展目录项。

已于 2026-10-26 实际下载并核查官方 openEuler22.03/x86_64 的 6.0.5 服务端发行包，结果如下：

| 核查项 | 结果 |
| --- | --- |
| 官方安装包 | `openGauss-Server-6.0.5-openEuler22.03-x86_64.tar.bz2`，78,533,901 字节 |
| 安装包 SHA-256 | `f81e592f9a503a71f57471506b10db585e7be674aa260030eb618c5167d3a6fd`，与官方 `.sha256` 一致 |
| `version.cfg` | `openGauss-Server-6.0.5`、`5ba692ab`、`release`，与目标 build 一致 |
| 包内 DataVec 文件 | 只有 `datavec.so`、`datavec.control`；没有 `datavec--0.7.2.sql` 或其他 DataVec 安装 SQL |
| 动态库核对 | 包内 `.so` SHA-256 与现有镜像记录完全一致 |

因此不能承诺能从该官方发行包取得所缺安装 SQL，也不能把当前现象归因为 Harbor 构建漏复制。官方包本身同样没有提供该文件；若仍需扩展安装路径，必须另行确认供应方是否有匹配交付物。该文件不存在的检查不等同于完整向量运行验收通过。

[官方安装包](https://opengauss.obs.cn-south-1.myhuaweicloud.com/6.0.5/openEuler22.03/x86/openGauss-Server-6.0.5-openEuler22.03-x86_64.tar.bz2)与[官方校验值](https://opengauss.obs.cn-south-1.myhuaweicloud.com/6.0.5/openEuler22.03/x86/openGauss-Server-6.0.5-openEuler22.03-x86_64.sha256)已下载到本 worktree 的 `data/opengauss/official-packages/6.0.5/`，该目录用于本地离线材料且由 Git 忽略。[核查清单](../docker/opengauss/datavec/official-package-manifest.json)保留来源、文件大小、版本内容和校验值，作为仓库内可追溯材料；不把 78 MB 二进制包加入 Git。

这条失败不能直接等同于 DataVec 不可用。官方文档说明 openGauss 6.0.3 起将 DataVec 作为内核特性提供。与远端 build 一致的 `5ba692ab` 源码也直接定义了 `vector` 类型、HNSW、IVFFLAT 访问方法及其操作符类，不以 `CREATE EXTENSION datavec` 为前置条件。

本轮只读检查确认远端存在 `pg_catalog.vector`、`pg_catalog.sparsevec`，以及 HNSW、IVFFLAT 和对应的 `vector_l2_ops`、`vector_ip_ops`、`vector_cosine_ops`。历史独立库验证只证明三维向量建表、插入和三种距离查询通过；更新/删除/回滚阶段曾遇到断连，索引创建、真实 1024 维向量、重连及向量备份恢复仍未完成。目录存在和内核源码不能替代这些运行验证。

因此当前结论分为两层：

1. **DataVec 内核组件**：基础镜像已经包含，当前没有证据表明需要另装组件；先验收现有镜像，不急于制作“增加 datavec”的镜像。
2. **扩展目录完整性**：如果交付标准明确要求 `CREATE EXTENSION datavec` 成功，仍需向 openGauss 供应方取得与 6.0.5 build 匹配的完整扩展包；在拿到官方文件前不能自行补写 SQL，也不能用 PostgreSQL/pgvector 文件替代。

当前 Octop 初始化脚本没有执行 `CREATE EXTENSION datavec`，知识库默认使用 SQLite sidecar；不能把扩展目录问题混入 Octop 控制面迁移。

## 安装包要求

只有在必须支持 `CREATE EXTENSION datavec` 时，才需要取得与当前 openGauss 版本和动态库匹配的官方 datavec 0.7.2 交付物。完整交付物至少应包含：

```text
datavec.control
datavec--0.7.2.sql
datavec.so
```

如果交付物包含版本升级 SQL、许可证或其他运行库，也必须按官方目录结构一并保留。不能根据 PostgreSQL/pgvector 0.7.2 文件手写 datavec SQL，也不能从 openGauss 7.x 或其他 build 复制动态库。

## 何时制作派生镜像

当前目标只是使用 DataVec 向量能力时，先在隔离实例验收 `harbor.whodc.cn/library/opengauss:6.0.5`，通过后固定并记录其镜像 digest。不要因为 `CREATE EXTENSION datavec` 的诊断失败就覆盖运行中的容器文件。

只有供应方确认缺失文件属于该 build 的正式交付物，或者部署规范必须让 `CREATE EXTENSION datavec` 成功时，才制作派生镜像。

## 推荐镜像方式（仅适用于扩展目录修复）

不要直接进入正在运行的容器复制文件。应基于匹配的基础镜像制作不可变派生镜像：

```dockerfile
FROM harbor.whodc.cn/library/opengauss:6.0.5

COPY datavec.control /usr/local/opengauss/share/postgresql/extension/datavec.control
COPY datavec--0.7.2.sql /usr/local/opengauss/share/postgresql/extension/datavec--0.7.2.sql
COPY datavec.so /usr/local/opengauss/lib/postgresql/datavec.so

RUN chown omm:omm \
      /usr/local/opengauss/share/postgresql/extension/datavec.control \
      /usr/local/opengauss/share/postgresql/extension/datavec--0.7.2.sql \
      /usr/local/opengauss/lib/postgresql/datavec.so \
    && chmod 0644 \
      /usr/local/opengauss/share/postgresql/extension/datavec.control \
      /usr/local/opengauss/share/postgresql/extension/datavec--0.7.2.sql \
    && chmod 0755 /usr/local/opengauss/lib/postgresql/datavec.so \
    && test -s /usr/local/opengauss/share/postgresql/extension/datavec--0.7.2.sql \
    && test -s /usr/local/opengauss/lib/postgresql/datavec.so
```

实际拿到官方包后，应以包内文件和权限为准，不要无条件覆盖基础镜像已有动态库。若包内动态库与当前镜像 SHA-256 不同，必须先确认它确实对应 openGauss 6.0.5 build，再重新做完整验证。

建议镜像标签包含扩展版本和构建摘要，例如 `harbor.whodc.cn/library/opengauss:6.0.5-datavec072`。推送前保存基础镜像 digest、扩展文件 SHA-256、构建 Dockerfile 和安装包 SHA-256。新环境只引用固定 digest，不使用可变的 `latest`。

## 离线导入流程

在可联网机器准备并校验安装包：

```bash
sha256sum datavec.control datavec--0.7.2.sql datavec.so
docker build --platform linux/amd64 -t harbor.whodc.cn/library/opengauss:6.0.5-datavec072 .
docker push harbor.whodc.cn/library/opengauss:6.0.5-datavec072
```

如果构建机不能直接访问 Harbor，可以导出后导入：

```bash
docker save harbor.whodc.cn/library/opengauss:6.0.5-datavec072 | gzip > opengauss-6.0.5-datavec072.tar.gz
sha256sum opengauss-6.0.5-datavec072.tar.gz
scp opengauss-6.0.5-datavec072.tar.gz root@target:/tmp/
ssh root@target 'gunzip -c /tmp/opengauss-6.0.5-datavec072.tar.gz | docker load'
```

在共享服务器上，先保存现有容器 inspect、镜像 digest、挂载目录和 compose 配置，再用新镜像创建替代容器。数据目录 `/APP/DockerData/opengauss` 必须保持原挂载，不能用空目录覆盖。替换前应取得维护窗口，并准备按原镜像回滚。

## 验证顺序

先确认内核版本，不连接业务库执行写入：

```bash
docker run --rm --platform linux/amd64 --entrypoint sh \
  harbor.whodc.cn/library/opengauss:6.0.5 -c '
    export LD_LIBRARY_PATH=/usr/local/opengauss/lib
    /usr/local/opengauss/bin/gaussdb --version
  '
```

只有制作了扩展目录修复镜像时，才补做以下文件校验：

```bash
docker run --rm --entrypoint sh harbor.whodc.cn/library/opengauss:6.0.5-datavec072 -c '
  sha256sum /usr/local/opengauss/lib/postgresql/datavec.so
  test -s /usr/local/opengauss/share/postgresql/extension/datavec.control
  test -s /usr/local/opengauss/share/postgresql/extension/datavec--0.7.2.sql
'
```

容器启动后，使用随机独立数据库先验证 DataVec 内核能力：

```sql
SELECT version();
CREATE TABLE datavec_probe (id bigint, embedding vector(3))
  WITH (storage_type = ASTORE);
INSERT INTO datavec_probe VALUES
  (1, '[1,0,0]'), (2, '[0,1,0]'), (3, '[0,0,1]');
SELECT id FROM datavec_probe ORDER BY embedding <-> '[1,0,0]' LIMIT 1;
CREATE INDEX datavec_probe_hnsw
  ON datavec_probe USING hnsw (embedding vector_l2_ops);
CREATE INDEX datavec_probe_ivfflat
  ON datavec_probe USING ivfflat (embedding vector_l2_ops);
```

只有供应方确认需要扩展安装、且匹配包已经进入隔离镜像后，才在独立库执行以下扩展安装检查；这不作为 DataVec 内核验收前置条件：

```sql
SELECT version();
SELECT name, default_version, installed_version
FROM pg_available_extensions
WHERE name = 'datavec';

CREATE EXTENSION datavec;

SELECT extname, extversion
FROM pg_extension
WHERE extname = 'datavec';
```

随后按顺序验证真实 1024 维向量的建表、写入、更新、删除、事务回滚、错误维度拒绝、HNSW/IVFFlat、重连、gs_dump/gs_restore。第一条异常立即停止，不自动重试潜在触发实例问题的写入。验证库按名称和 OID 登记，确认零会话后精确删除。

鉴于历史向量 DML 阶段曾出现断连并随后端口拒绝连接，下一轮完整验证先使用同镜像、独立数据目录的临时实例。容器名带 `opengauss`、端口避开现有服务，不挂载 `/APP/DockerData/opengauss`。逐条记录 SQL 和服务端日志，完整通过后再向共享实例的随机新库复验；独立数据库仍共享服务端进程，不能隔离内核故障对其他库的影响。

## 验证后的本地交付物

验证成功后必须将可复现内容补到当前 `octop-opengauss` worktree，不能只保留在临时目录或远端容器中：

- 验证 SQL 和执行/清理入口放入 `docker/opengauss/validation/`；明确独立实例、独立库的使用条件。
- 脱敏的验证结果、目标 build、基础镜像 digest、验证命令及已知限制补入 `docs/validation/`，并由本文链接。
- 如果取得且验证了供应方匹配的扩展安装 SQL，将原始文件保存到 `docker/opengauss/datavec/`，附来源、适用 build、许可证与 SHA-256；需要的镜像构建文件也一并保存。尚未取得文件时不创建同名占位 SQL，不将自行编写的验证 SQL 标为官方安装脚本。
- 私有数据库口令、provider 密钥和含凭证的原始日志不进入上述交付目录。临时验证库和实例清理后，仍保留不含凭证的复现材料。

## 回滚与故障处理

- `CREATE EXTENSION` 仍报 58P01：若 DataVec 内核能力验证已通过，这只表示扩展目录项仍不完整；不要因此覆盖数据目录或反复在业务库重试。若部署规范要求该命令成功，停止发布并取得供应方匹配包。
- 报动态库加载错误：不要替换为其他版本 `.so`；恢复旧镜像，重新取得匹配 build 的官方包。
- 报符号不存在或服务器崩溃：立即停止写入，保留容器日志、镜像 digest、扩展文件 SHA-256 和 SQL 记录，回滚镜像并交由 openGauss 厂商分析。
- 不要在 `plat`、持久 `octop` 或其他业务库中反复尝试安装；扩展对象的首次验证只使用独立库。

## Octop 部署边界

DataVec 是数据库内核能力，不应由 Octop 首次初始化 SQL 自动下载或安装。Octop 只在需要数据库向量表能力的部署检查中验证内核类型、DML、索引和备份恢复；知识库当前可继续使用第三方 embedding + SQLite sidecar。即使 DataVec 验证通过，Octop 仍需要单独实现向量表后端、embedding 传入和权限/维度策略，不能仅靠换数据库镜像宣称知识库已切换到 openGauss 向量检索。

第三方 embedding 服务负责生成向量，DataVec 负责存储和检索向量；数据库无需安装本地模型。Agent Memory 的 Harness 当前未传入 `vector_index` / `embedding_provider`，PG/openGauss 向量索引适配器、模型维度切换和补索引机制仍是独立的产品工作。现有知识库 sidecar 路径已验证，不因本次镜像核查而改写。

2026-10-26 已再次完成 [产品向量接线实测](validation/opengauss-product-vector-20261026.md)：PG/openGauss 的真实知识库流程和 openGauss 真实 Memory 共 3 passed。实际知识库向量落在 SQLite，真实 Harness Memory 未配置向量索引或 embedding provider；本轮支持现有业务流程可用，不能记为 openGauss 产品向量接入完成。

## 结论依据（2026-10-26）

- [官方 DataVec 概述](https://gitcode.com/opengauss/openGauss-server/blob/master/src/gausskernel/storage/access/datavec/docs/zh/datavec_overview.md)：说明自 6.0.3 引入且属于内核特性。master 文档只用于说明架构，具体功能以目标 build 为准。
- [目标 build 类型目录](https://gitcode.com/opengauss/openGauss-server/blob/5ba692ab/src/include/catalog/pg_type.h)：内置 `vector`，OID 8305。
- [目标 build 索引访问方法](https://gitcode.com/opengauss/openGauss-server/blob/5ba692ab/src/include/catalog/pg_am.h)：HNSW OID 8300、IVFFLAT OID 8301。
- [目标 build 操作符类](https://gitcode.com/opengauss/openGauss-server/blob/5ba692ab/src/include/catalog/pg_opclass.h)：内置相应向量操作符类。
- [历史运行记录](validation/opengauss-20261023-vector.md)：基础向量通过和后续断连、索引未执行的原始边界。

镜像/源码核查阶段未安装扩展文件、推送镜像、替换共享容器或执行数据库 DDL/DML，仅检查本地现有镜像、官方安装包和远端系统目录。后续产品接线验证在随机独立库执行了应用初始化和业务流程，并通过 `make all PYTEST_JOBS=4`（5120 passed、89 skipped）；完整范围与清理结果见上文链接的产品向量接线实测。
