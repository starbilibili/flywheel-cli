# 文渊 Blob Registry 与原生 Dataset：Flywheel 接入事实

> 调研范围：依据文渊官方飞书文档与本机平台发布的 `wenyon-cli 1.16.0 (d6981e0)` 命令合同，记录 Flywheel 设计资源注册时可以依赖的平台事实。文档来源为[《模型评测任务要素存管方案（文渊侧）》](https://dptechnology.feishu.cn/wiki/L8VgwIUhTiSNFwkZSqhcclpBnng)（revision 32）和[《文渊指南 · 用户手册》](https://dptechnology.feishu.cn/wiki/PmEtwuXxqiD8DxkccOCc1ZCbn9b)（revision 375），核对时间为 2026-09-01。

## 核心结论

文档没有要求 Flywheel 的所有数据资源都必须使用文渊原生 `dataset`。相反，[存管方案「0. 对象存放概览」](https://dptechnology.feishu.cn/wiki/L8VgwIUhTiSNFwkZSqhcclpBnng)明确区分了三类存储：

- Model、Evaluation Config 等由文件和引用组成的任务要素，进入 Blob Registry，以 `repo:tag` 或 `repo@sha256:<digest>` 引用。
- Harbor 任务集等大文件集合，进入 file dataset，以 `dataset@version` 引用。
- 评分及样本级明细等多行表格数据，进入共享 Lance dataset，以 SQL 或 retrieve 消费。

因此，“dataset”需要分成两个层次理解：

1. Flywheel 的 `dataset` 是任务领域里的资源类型，表示任务所消费的数据。
2. 文渊原生 dataset 是一种存储和治理能力。它适合大批量数据、SQL、跨表查询、高吞吐读写和训练挂载，但不是 Flywheel dataset 唯一可能的存储后端。

当前文档给出的平台侧推荐边界很清楚：小型、文件化、希望和其他任务资源使用同一套 manifest/ref 组合模型的数据，可以放进 Blob Registry；大批量评测集应走 file dataset。若 Flywheel 首版把数据集统一做成 Blob Registry bundle，这只能算 Flywheel 的 MVP 约定，不能表述为文渊的唯一或推荐数据集模型。[存管方案 §0、FAQ「大文件能放吗」](https://dptechnology.feishu.cn/wiki/L8VgwIUhTiSNFwkZSqhcclpBnng)

## 1. Blob Registry 的对象模型

[存管方案「1. Blob Registry」](https://dptechnology.feishu.cn/wiki/L8VgwIUhTiSNFwkZSqhcclpBnng)定义了四个基础概念：

| 概念 | 文档定义 | 版本语义 |
|---|---|---|
| `blob` | 以 SHA-256 内容寻址的字节，全局去重 | blob digest 永久且不可变 |
| `manifest` | 一组 blob 与 refs 的清单；manifest 自身也是 blob | manifest digest 是 bundle snapshot 的不可变版本指纹 |
| `tag` | 指向某个 manifest digest 的可变版本指针 | 可覆盖、可删除，不适合作为可复现任务的最终引用 |
| `repo` | 一条版本线，也是所有权和授权的挂载点 | 一个 repo 下可有多个 manifest snapshot 和 tag |

文档自身把 asset 映射为 blob、bundle 映射为 manifest、bundle snapshot 映射为 manifest digest。目录不是单个 blob：CLI 接收文件或目录，目录内文件分别成为 blob，再由 manifest 的 `files` 记录逻辑路径、digest、大小和可选 annotations。

### 1.1 Manifest 合同

manifest 顶层固定为四个字段，额外字段会在 push 时被拒绝：[存管方案「manifest 示例」](https://dptechnology.feishu.cn/wiki/L8VgwIUhTiSNFwkZSqhcclpBnng)

```json
{
  "version": "v1",
  "files": [
    {
      "path": "data/test.jsonl",
      "digest": "sha256:<64-hex>",
      "size": 12345,
      "annotations": {
        "source_mtime": "1756100000"
      }
    }
  ],
  "refs": [],
  "annotations": {
    "description": "AIME 2025 evaluation dataset",
    "owner": "eval-team"
  }
}
```

- `version`：manifest 信封版本，当前恒为 `"v1"`；服务端拒绝未知版本。
- `files`：bundle 自有的字节负载。pull 时按 `path` 物化到本地，并默认校验 SHA-256。
- `refs`：对其他 registry 对象或外部系统的对象级引用，形状为 `name` / `kind` / `ref`。
- `annotations`：展示性 `string -> string` 元数据。

配置文件名称、文件格式和业务字段不属于 registry 信封。自定义语义应写成普通文件并进入 `files`，例如 `bundle.yaml` 或 `task.toml`。文件修改时间不参与 blob 内容寻址；CLI 将其写入单文件的 `annotations.source_mtime`。[存管方案「manifest 示例」](https://dptechnology.feishu.cn/wiki/L8VgwIUhTiSNFwkZSqhcclpBnng)

### 1.2 Refs 的准确语义

[存管方案「manifest 示例」「files 与 refs 的分工」](https://dptechnology.feishu.cn/wiki/L8VgwIUhTiSNFwkZSqhcclpBnng)对 refs 有几项硬约束：

- `kind: "registry"` 只接受不可变引用 `repo@sha256:<hex>`，tag 会被拒绝。
- `trisol`、`image` 等外部 kind 是透明字符串；kind 词表开放，服务端不解释其业务语义。
- 服务端只校验 ref 的形状，不验证目标是否存在，也不验证调用者是否有权限读取目标。
- 服务端不递归展开 refs。pack 只展开当前 manifest 第一层的 `files`；消费方负责继续解析嵌套 bundle，并实现深度限制和环路检测。
- 文件级复用无需 refs。全局 blob digest 可以直接进入另一个 manifest 的 `files`；refs 用于表达对象级组合。
- pull 会把 `files` 物化为文件，但不会物化 refs。

这意味着评测 Task bundle 可以用 refs 钉住 dataset、model、config、script 等依赖，但所有 registry 依赖都必须落到 manifest digest，不能把 `latest` 之类的 tag 写入任务 snapshot。

## 2. Blob Registry 的 CLI 接口

### 2.1 Push

```bash
wenyon-cli registry push <path|-> <repo> [--tag v1] \
  [--ref name=kind:ref] [--output json]
```

事实来源：[存管方案「上手三步」「命令面速查」](https://dptechnology.feishu.cn/wiki/L8VgwIUhTiSNFwkZSqhcclpBnng)。

- `<path>` 可以是文件或目录；`-` 表示没有本地文件的纯引用 bundle。
- repo 不存在时自动创建。
- 裸 repo 名自动落在调用者的 `users/<uid>/` 个人命名空间，用户不需要填写 uid。
- `--tag` 参数可省略，但当前 `wenyon-cli 1.16.0` 会在省略时使用 `latest`。反复 push 会产生新的 manifest digest，并移动该 tag。
- push 输出 manifest digest，即本次 bundle 内容的永久指纹。
- 文档说明 registry 命令都支持 `--output json`，但没有给出 `registry push --output json` 的完整响应 schema。

示例：

```bash
wenyon-cli registry push ./my-bundle my-bundle --tag v1 \
  --ref model_artifact=trisol:<id> \
  --ref benchmark=registry:eval/coding@sha256:<hex>
```

### 2.2 Pull

```bash
wenyon-cli registry pull <repo>:<tag> -o ./out
wenyon-cli registry pull <repo>@sha256:<manifest-hex> -o ./out
```

pull 先取得 manifest 和全部第一层文件的时效下载 URL，然后逐文件下载并默认验证 SHA-256。tag 适合交互便利；需要复现的任务必须保存 digest。[存管方案「上手三步」「命令面速查」](https://dptechnology.feishu.cn/wiki/L8VgwIUhTiSNFwkZSqhcclpBnng)

### 2.3 Search、List 与 Tags

```bash
wenyon-cli registry list
wenyon-cli registry search <query>
wenyon-cli registry tags <repo>
wenyon-cli registry untag <repo> <tag>
```

- `registry list` 列出调用者可见的 repo。
- `registry search <query>` 按名称、tag 或 digest 搜索可见 repo。
- `registry tags <repo>` 列出某条版本线的 tag。
- 覆盖或删除已有 tag 仅 owner 或平台 admin 可执行；有 write grant 的协作者可以 push 新 blob、manifest 和新 tag，但不能改写已有 tag。

这些事实见[存管方案「命令面速查」「4. 授权方式」](https://dptechnology.feishu.cn/wiki/L8VgwIUhTiSNFwkZSqhcclpBnng)。文档没有给出 list/search/tags 的 JSON 响应 schema，因此接入前仍需用实际 CLI 输出确认字段。

## 3. Blob Registry 的 HTTP API

[存管方案「2. HTTP API」](https://dptechnology.feishu.cn/wiki/L8VgwIUhTiSNFwkZSqhcclpBnng)说明：不使用 CLI 的服务集成方可以直接调用 HTTP API；完整且权威的 schema 位于文渊仓库 `docs/registry-openapi.yaml`。

| 方法 | 路径 | 用途 |
|---|---|---|
| `POST` | `/registry/repos` | 创建 repo |
| `GET` | `/registry/repos` | 列出可见 repo |
| `GET` / `DELETE` | `/registry/repos/{repo}` | repo 详情 / 删除 |
| `GET` | `/registry/search?q=` | 按名称、tag、digest 搜索 |
| `POST` | `/registry/repos/{repo}/blobs` | 登记 blob，取得 presigned PUT URL |
| `POST` | `/registry/repos/{repo}/blobs/complete` | 登记上传完成 |
| `GET` | `/registry/repos/{repo}/blobs/{digest}` | 取得 blob 下载 URL |
| `PUT` | `/registry/repos/{repo}/manifests/{tag}` | 发布 manifest 并写 tag |
| `GET` | `/registry/repos/{repo}/manifests/{ref}` | 按 tag 或 digest 读取 manifest |
| `GET` | `/registry/repos/{repo}/pack/{ref}` | 取得 manifest 和第一层全部文件的下载 URL |
| `GET` / `DELETE` | `/registry/repos/{repo}/tags[/{tag}]` | 列出 / 删除 tag |
| `GET` / `PUT` / `DELETE` | `/registry/repos/{repo}/grants[...]` | 授权管理 |
| `POST` | `/registry/repos/{repo}/share` | 签发整包时效 URL |
| `POST` | `/registry/repos/{repo}/publish` | 发布到 Catalog |
| `GET` | `/datasets/{id}/pack` | 按 Catalog dataset 权限读取已发布的 registry bundle |

所有接口使用 Bearer token。字节不经过业务 API 中转，而是通过 presigned URL 直连对象存储。[存管方案 §2](https://dptechnology.feishu.cn/wiki/L8VgwIUhTiSNFwkZSqhcclpBnng)

一个 blob 的写入是三步协议：

1. `POST /registry/repos/{repo}/blobs`，提交 digest、size、Content-MD5，取得 presigned PUT URL 和签名所需 headers。
2. 客户端直接 PUT 对象存储，必须原样携带服务端返回的 headers。
3. `POST /registry/repos/{repo}/blobs/complete` 登记完成。

随后使用 `PUT /registry/repos/{repo}/manifests/{tag}` 发布 manifest。示例响应包含归一化后的 `repo`、`tag` 和 manifest `digest`。读取整包使用 `GET /registry/repos/{repo}/pack/{ref}`。[存管方案「典型调用示例」](https://dptechnology.feishu.cn/wiki/L8VgwIUhTiSNFwkZSqhcclpBnng)

## 4. 文渊原生 Dataset

### 4.1 用户可见接口

[用户手册「用 SQL 查数据」「找数据集、看字段」「自建数据集」](https://dptechnology.feishu.cn/wiki/PmEtwuXxqiD8DxkccOCc1ZCbn9b)列出了这些命令：

```bash
wenyon-cli dataset list
wenyon-cli dataset search <query>
wenyon-cli dataset get <dataset-id>
wenyon-cli dataset schema <dataset-id>
wenyon-cli dataset peek <dataset-id>
wenyon-cli dataset query <sql-file|->

wenyon-cli dataset upload <dataset-id> <file-or-directory>
wenyon-cli dataset download <dataset-id> -o <output-dir>
```

- `dataset list` 面向数据目录，展示 ID、Name、Domain、Visibility、Tier、Rows、Size、Quality 等信息。
- `dataset search` 按关键词查找数据集。
- `dataset query` 支持 SQL 和跨数据集 JOIN。
- 自建 dataset 可上传文件或递归上传目录，格式不受限，文档列举了 Parquet、图像和原始下载包。
- CLI 和网页都支持上传、下载；数据达到几十 GB 或需要高吞吐持续读写时，文档建议使用 Sandbox 挂载。

### 4.2 文档公开的版本信息

存管方案把 file dataset 的稳定引用写作 `dataset@version`，并要求评分表记录 `benchmark_dataset@version`。[存管方案 §0、§5](https://dptechnology.feishu.cn/wiki/L8VgwIUhTiSNFwkZSqhcclpBnng)

两份飞书文档没有说明：

- `dataset upload` 是创建新版本还是覆盖当前版本；
- version 的格式、生成规则和返回字段；
- 如何列出某个 dataset 的历史版本；
- 如何指定血缘父版本；
- `dataset download` 如何选择特定 version；
- 原生 file dataset 上传对应的 HTTP API 与请求/响应 schema。

不过，本机平台发布的 `wenyon-cli 1.16.0` 已提供更完整的命令合同：

```bash
wenyon-cli dataset create <id> --name <name> [--from <id[:version],...>] [--via <note>]
wenyon-cli dataset upload <id> <paths...> --output json
wenyon-cli dataset versions <id> --output json
wenyon-cli dataset files <id> [--version <version>] --output json
wenyon-cli dataset download <id> [--version <version>] --output-dir <dir>
wenyon-cli dataset lineage <id>
```

- 一次 `dataset upload` 会产生一个新的不可变版本；同一次调用中的全部文件属于同一版本。
- upload 是“新增或覆盖”，不是目录同步；本地删除的文件不会从 dataset 中删除。
- 每个版本都是完整物理副本，不使用 Registry 的 blob 全局去重模型。
- `dataset versions` 按从新到旧列出版本，并给出每版文件数、字节数及 warm/parse 状态。
- `dataset download --version`、`dataset files --version` 可以精确读取指定版本。
- `dataset create --from` 可声明上游 dataset 及版本的 lineage；这是数据集之间的来源血缘，不是“同一 dataset 相邻版本的父版本”。

因此，原生 file dataset 的版本读取能力实际上已经具备；仍需实测其 JSON 字段和错误结构。当前命令面没有提供“为新版本指定父版本”的参数。

### 4.3 原生 Dataset 与 Registry Publish 不是同一件事

[存管方案「3. 与 Catalog 的关系」](https://dptechnology.feishu.cn/wiki/L8VgwIUhTiSNFwkZSqhcclpBnng)还描述了另一条路径：

```bash
wenyon-cli registry publish <repo> <ref> --dataset <id>
```

它把 registry 的某个 manifest digest 钉成 Catalog 中 `kind=blob` 的指针条目，不搬运字节。发布后，Catalog 读者可通过 `GET /datasets/{id}/pack` 下载；registry repo 和 Catalog dataset 的授权、读入口彼此独立。

这条“将 registry bundle 发布到 Catalog”的路径不能直接等同于 `wenyon-cli dataset upload` 创建的 file dataset：文档把前者明确描述为 `kind=blob` 指针条目，把后者列为承载大文件集合的 file dataset。两者都会出现在 dataset/catalog 侧，但底层内容模型不同。

## 5. 大小限制与适用边界

[存管方案 FAQ「大文件能放吗」](https://dptechnology.feishu.cn/wiki/L8VgwIUhTiSNFwkZSqhcclpBnng)明确写的是：**单个 blob 上限为 5 GB**，原因是单次 presigned PUT 的协议上限。

这不是下面这些对象的总大小上限：

- 不是一个 manifest 的总大小上限；
- 不是一个 repo 的总大小上限；
- 不是一个 bundle / Flywheel resource 的总大小上限。

因此，一个总量超过 5 GB、但每个文件都小于 5 GB 的目录，从字面协议上不违反单 blob 限制。不过同一 FAQ 仍明确建议大批量数据、评测集和样本库走 file dataset；用户手册也建议几十 GB 或高吞吐持续读写场景使用 Sandbox 挂载。这个建议不能只按“单文件是否超过 5 GB”判断。[用户手册「自建数据集」](https://dptechnology.feishu.cn/wiki/PmEtwuXxqiD8DxkccOCc1ZCbn9b)

## 6. 命名空间、权限与生命周期

[存管方案「命名空间与可见性」「4. 授权方式」「FAQ」](https://dptechnology.feishu.cn/wiki/L8VgwIUhTiSNFwkZSqhcclpBnng)给出的 registry 规则如下：

- `users/<uid>/...` 是个人空间，push 裸名时服务端自动补全。
- `eval/` 等共享前缀由文渊侧分配。
- repo 默认 `private`；`public` 表示内部全员可读。可见性和命名空间前缀无关。
- registry 当前没有 team 概念；协作通过 repo 级 read/write grant 实现。
- owner 与 write grant 持有者都能 push 新 blob、manifest 和新 tag；覆盖/删除已有 tag、删除 repo、发 grant 仅 owner 或平台 admin 可以执行。
- registry 当前只对内部用户开放。
- 草稿 repo 长期无活动会按未公开的 TTL 回收；发布过的 repo 豁免。存在 Catalog 引用的 repo 不可直接删除。
- 单个 manifest snapshot 没有删除入口；下线方式是 untag 或删除 repo。
- presigned URL 默认有效 1 小时。过期时重新签发整个 pack；不支持只续签单个 blob。CLI 的 `registry pull` 和 `dataset download` 已处理重试。

Registry publish 后，Catalog 使用 `visibility + team + grants` 的 dataset 治理模型。两侧授权完全独立：获得 dataset 读取权限不代表获得 repo 权限，反之亦然。[存管方案 §3、§4](https://dptechnology.feishu.cn/wiki/L8VgwIUhTiSNFwkZSqhcclpBnng)

## 7. 对 Flywheel 接口选择的启示

以下是基于上述文档事实得出的设计判断，不是文渊文档的强制要求：

1. **Flywheel 不应把领域类型与存储类型画等号。** 用户继续选择 `dataset`，内部再由 adapter 决定使用 `registry_bundle`、`wenyon_file_dataset`，以后也可接其他后端。
2. **首版真实接入 Blob Registry 时，优先封装 `wenyon-cli registry push/pull/search/tags --output json`。** CLI 已封装哈希、presigned URL、上传完成登记、下载校验和 URL 过期重试；这能让 Flywheel 先验证完整用户链路。面向长期服务化和无 CLI 运行环境时，再切到文档指定的 HTTP API，并以 `docs/registry-openapi.yaml` 为权威 schema。
3. **小型问答评测集可以先走 Blob Registry。** 前提是单文件小于 5 GB、无需 SQL / JOIN、无需高吞吐持续访问，并接受其 repo/tag/digest 版本模型。
4. **大批量评测集和训练数据应保留原生 file dataset adapter。** 文渊存管方案本身就把 Harbor 任务集等大文件集合放在 file dataset；只实现 registry 会在数据规模扩大时形成迁移压力。
5. **Task snapshot 应保存不可变后端引用。** Registry 资源保存 `repo@sha256:<manifest-digest>`；file dataset 保存文档所称的 `dataset@version`。tag 和“最新版”只能用于人机交互时查找，不能成为已提交任务的最终依赖。
6. **不要在 `config.yaml` 中泄漏底层存储差异。** 用户面向 Flywheel resource ID；解析阶段将其转换成 registry digest 或 dataset version，再交给运行时。

## 8. 接入前仍需确认

1. `wenyon-cli registry push/list/search/tags --output json` 的真实响应字段、退出码和错误结构。
2. Registry push 默认移动 `latest` 已由 CLI help 确认；仍需确认 JSON 返回中 tag 如何表达。
3. manifest 的 `path` 规范，包括 `..`、绝对路径、重复路径和软链接处理。
4. 原生 file dataset 的 JSON 合同：create/upload/status/versions/files 的返回字段、version 格式和错误结构。
5. `dataset upload` 的 HTTP API 或 SDK；两份飞书文档未提供其请求/响应 schema。
6. Registry 草稿回收 TTL 的具体数值。
7. Flywheel 数据量或访问模式达到什么阈值时，从 Blob Registry 切换到 file dataset；文档只给出“几十 GB、高吞吐持续读写、大批量数据”等定性建议。
