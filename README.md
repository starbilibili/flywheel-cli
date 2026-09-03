# Flywheel CLI

Flywheel（命令名 `fw`）把 Dataset、Config、Script、Model 组合成可复现的 Task，并支持本地或 LBG 远端执行。

Flywheel 不保存资源文件：资源和不可变 Snapshot 由 Wenyon Registry 管理；Trisol 负责登录、身份检查和刷新共享 Vouch 会话。

## 安装

```bash
cd /personal/fw
python -m pip install .
fw version
fw --help
```

依赖命令：`trisol`、`wenyon-cli`。检查安装：

```bash
trisol --help
wenyon-cli --help
```

## 命令职责

| 命令 | 用途 | 底层调用 |
| --- | --- | --- |
| `fw auth login` | 登录 Flywheel | `trisol login`、`trisol whoami`、`wenyon-cli registry list` |
| `fw auth status` | 检查登录状态 | `trisol whoami`、`wenyon-cli registry list` |
| `fw auth logout` | 退出登录 | `wenyon-cli auth logout`、`trisol logout` |
| `fw resource plan <path>` | 注册前检查本地资源 | 无平台调用 |
| `fw register <path>` | 注册资源（`resource register` 的快捷方式） | Wenyon Registry CLI + Manifest REST API |
| `fw resource search <name>` | 按名称搜索资源 | Flywheel Index；回退到 `wenyon-cli registry search` |
| `fw resource inspect <ref>` | 查看本地资源或远端 Snapshot | 远端使用 Wenyon search + pull |
| `fw snapshot search <resource-id>` | 查询资源的可见 Snapshot | Wenyon search + tags |
| `fw task create` | 创建并注册 Task Resource | Wenyon search + tags + 注册流程 |
| `fw task plan --config <file>` | 下载依赖并生成运行计划 | Wenyon search + pull |
| `fw task submit --config <file> --backend local` | 本地运行 | 资源准备使用 Wenyon；执行阶段无平台调用 |
| `fw task submit --config <file>` | LBG 远端提交（默认后端） | Wenyon + LBG OpenAPI |
| `fw task status <run-id>` | 查询本地或 LBG 状态 | 本地文件或 LBG OpenAPI |
| `fw task result <run-id>` | 读取本地结果 | 本地文件 |

完整参数：`fw <command> --help`。

## 资源、Snapshot 和 Tag

支持的资源类型是 `dataset`、`config`、`script`、`model` 和 `task`。

Resource ID 示例：

```text
res_01m1edkq9byehp949vj96ywmhx
```

Storage Repo 规范：

```text
users/<uid>/<resource-type>/<resource-name>-<resource-id>
```

例如：`users/<uid>/dataset/aime25-res_01m1edkq9byehp949vj96ywmhx`。

Snapshot 由完整的 Manifest Digest 标识，例如：

```text
sha256:5a34c6c2286f57bc2c06778598aa24ff741112e6acbe9cd96d84a32ec734ca5d
```

Task 和运行配置使用 Digest，不依赖可变 Tag。Tag（如 `v1`）只能绑定一次，不能转移到新的 Digest；`latest` 由 Flywheel 维护。

## 登录与认证

```bash
fw auth login
fw auth status
fw auth logout
```

登录时 Flywheel 运行 `trisol login`，然后用 `trisol whoami -o json` 刷新共享会话，再从会话中读取 `wenyon-svc` JWT，以临时环境变量 `WENYON_TOKEN` 调用 Wenyon。JWT 不会写入项目文件、Manifest 或资源包。

当前 Flywheel 直连 LBG Job 使用 Vouch 的 `lbg` audience JWT；不使用 `BOHRIUM_ACCESS_KEY`。后者只属于官方 `lbg` CLI / Sandbox API 的另一套认证方式，不在当前任务执行链路中。

## 注册资源

交互式：

```bash
fw register ./assets/dataset-aime25
```

带参（适合 Agent）：

```bash
fw register ./assets/dataset-aime25 \
  --type dataset --name aime25 --tag v1 --yes --output json
```

注册时的 Wenyon 调用顺序：

1. `wenyon-cli registry search <name> -o json`：查找同名 Resource。
2. `wenyon-cli registry tags <repo> -o json`：选择父 Snapshot。
3. `wenyon-cli registry push <path> <staging-repo> --tag staging -o json`：上传文件到临时 Repo。
4. 新 Resource 执行 `wenyon-cli registry repo create <repo> -o json`。
5. 通过 Manifest REST API 读取基础 Manifest，并以 `PUT /registry/repos/<repo>/manifests/<tag>` 发布完整 Manifest。
6. `wenyon-cli registry repo delete <staging-repo> --yes`：清理临时 Repo。

一次注册上传的是整个文件或目录。Script Snapshot 会包含 `run.sh` 以及同一资源中的其他脚本，不会只上传入口脚本。

首次注册需要解析个人 namespace。Flywheel 会创建临时探针 Repo，通过 `wenyon-cli registry list -o json` 解析 `users/<uid>`，随后删除探针，并把非敏感 UID 缓存到 `~/.config/flywheel/registry-identities.json`（权限 `0600`）。

## 搜索与下载

按名称：

```bash
fw resource search aime25
fw resource search aime25 --type dataset -o json
```

按 Resource ID 查询 Snapshot：

```bash
fw snapshot search res_01m1edkq9byehp949vj96ywmhx
```

底层依次调用：

```text
wenyon-cli registry search <resource-id> -o json
wenyon-cli registry tags <storage-repo> -o json
```

`registry tags` 只能看到当前有 Tag 指向的 Snapshot；未打 Tag 且没有 `latest` 指向的历史 Snapshot，需要通过 Flywheel Index 或完整 Digest 查询。

远端 Snapshot 解析时，Flywheel 先用 `wenyon-cli registry search` 反查 Repo，再用：

```text
wenyon-cli registry pull <repo>@<digest> --output-dir <dir> --output json
```

完整内容缓存到 `~/.cache/flywheel/snapshots/<digest>/content`，后续运行复用该缓存。

## Task 创建与运行

```bash
fw task create
```

交互流程是：选择 `eval` 或 `train` → 输入名称 → 选择 Dataset、Config、Script、Model 及各自 Snapshot → 设置可选 Tag → 预览并确认自动生成的 `task.yaml`。

最终 Task 名称是 `<task-type>-<task-name>`，例如 `eval-aime25`。生成的 Task 只保存四个 Digest，不复制四类资源文件：

```yaml
schema_version: fw-task/v1
task_type: eval
resources:
  dataset: sha256:<dataset-digest>
  config: sha256:<config-digest>
  script: sha256:<script-digest>
  model: sha256:<model-digest>
```

带参模式：

```bash
fw task create --type eval --name aime25 \
  --dataset sha256:<dataset-digest> \
  --config sha256:<config-digest> \
  --script sha256:<script-digest> \
  --model sha256:<model-digest> --yes --output json
```

运行配置只指定 Task Digest：

```yaml
schema_version: fw-task/v1
task_type: eval
task: sha256:<task-manifest-digest>
output_dir: ./runs
selection:
  strategy: random
  count: 20
  seed: 20260828
  replacement: false
```

生成计划：

```bash
fw task plan --config config.yaml
```

Flywheel 下载 Task 及四类依赖，按选择策略生成样本，并写出 `run-spec.json`、`selection.json` 和 `effective-run-config.json`。脚本最终执行：

```text
./run.sh --run-config ./effective-run-config.json
```

Flywheel 不假定数据必须是 `question + answer`；数据格式和评测逻辑由 Script Resource 负责。

本地运行：

```bash
fw task submit --config config.yaml --backend local
fw task submit --config config.yaml --backend local --wait
```

LBG 运行：

```bash
fw task submit --config config.yaml
fw task submit --config config.yaml --backend lbg --dry-run
```

当前 LBG Job API 的资源打包、创建、上传、提交和状态查询使用 Vouch JWT，已经接通。为验证端到端流程，当前实现会把 `model.credential_env` 对应的值以内联 `export NAME=value && ./run.sh ...` 放入 Job 的 `cmd`；这仅用于测试，凭据可能出现在远端命令元数据或进程信息中，不适合作为生产方案。后续应切换到 LBG Sandbox 的官方环境变量/Secret 注入能力。

LBG 的 `lbg sdbx create --env KEY=VALUE` 已确认支持安全注入，但切换到 Sandbox 后，任务 ID、状态、日志和结果接口需要单独适配。

## 状态与结果

本地：

```bash
fw task status <run-id> --output-dir ./runs --watch
fw task result <run-id> --output-dir ./runs
```

LBG：

```bash
fw task status <run-id> --bohr-job-id <bohrium-job-id>
fw task status <run-id> --lbg-job-id <lebesgue-job-id>
```

`--bohr-job-id` 与 `--lbg-job-id` 不能同时使用。当前 `task result` 只读取本地 `result.json`；LBG 结果下载和解包尚未接入。

## 环境变量

| 变量 | 用途 |
| --- | --- |
| `VOUCH_CONFIG_DIR` | 指定共享 Vouch 状态目录 |
| `WENYON_TOKEN` | Flywheel 临时传给 Wenyon 的 audience JWT（通常自动生成） |
| `FLYWHEEL_INDEX_URL` | 可选的资源索引服务地址 |
| `FLYWHEEL_INDEX_TOKEN` | 可选的资源索引服务 Token |
| `FLYWHEEL_LBG_PROJECT_ID` | LBG 项目数字 ID；未设置时回退到 `BOHRIUM_PROJECT_ID` |
| `FLYWHEEL_LBG_IMAGE` | LBG Job 使用的容器镜像 |
| `FLYWHEEL_LBG_SKU` | LBG Job 使用的计算规格 |
| `FLYWHEEL_LBG_ENDPOINT` | LBG OpenAPI 地址，默认 `https://open.bohrium.com` |
| `FLYWHEEL_LBG_AUDIENCE` | LBG 请求使用的 Vouch audience，默认 `wenyon-svc`；仅当网关明确提供其他 audience 时覆盖 |
| `BOHRIUM_ACCESS_KEY` | 当前 Flywheel 流程不使用；仅在未来切换官方 `lbg` CLI / Sandbox 方案时涉及 |
| `BOHRIUM_PROJECT_ID` | LBG 项目数字 ID |

查看任意命令的实际参数：

```bash
fw <command> --help
```
