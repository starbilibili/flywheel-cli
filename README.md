# Flywheel

在已激活的 Python/Conda 环境中安装：

```bash
cd /personal/fw
python -m pip install .
fw --help
```

不要使用 `--user`；否则命令可能被安装到不在当前环境 `PATH` 中的用户目录。

后台提交本地评测：

```bash
fw eval submit --config config.yaml
```

命令会立即返回任务 ID、输出目录和状态查询命令。查看持续进度：

```bash
fw eval status <run-id> --output-dir ./runs --watch
```

需要占用当前终端并直接显示进度条时，使用 `fw eval submit --config config.yaml --wait`。每次请求的 JSON、日志与最终摘要均保存在该次运行的输出目录中。

Task Spec 只选择 Dataset、Model、Evaluation Config 和 Script 四个完整资源：

```yaml
resources:
  dataset: local:./assets/dataset-aime25
  model: local:./assets/model-v4-pro
  config: local:./assets/evaluation-config-aime25-avg4
  script: local:./assets/evaluator-aime25-openai
```

资源内部文件和执行命令由资源自身的 manifest 与 adapter 解释，不属于用户配置。

交互式注册一个本地资源：

```bash
fw register ./assets/dataset-aime25
```

每次只处理一个文件或目录。CLI 不猜测资源类型，而是让用户从 Dataset、Model、Config、Script 中选择；随后输入资源名称（例如 `aime25`），CLI 会生成小写 `res_<ulid>` 形式的 Resource ID，并通过 Wenyon Blob Registry 创建不可变 Snapshot。资源路径采用 `users/<uid>/<resource-type>/<resource-name>-<resource-id>`，例如 `users/<uid>/dataset/aime25-res_<ulid>`。Tag 是可选识别标签，不代表版本序号。

创建新 Resource 时可以输入一段描述，直接回车则由 Flywheel 根据任务、资源类型、创建人和创建时间生成默认描述。该描述保存在 Manifest 中，后续注册新 Snapshot 时自动继承；选择已有 Resource 时，CLI 会同时展示描述和创建时间。

首次注册时，Flywheel 会解析文渊个人 Registry namespace，并把非敏感 UID 按登录主体缓存在 `~/.config/flywheel/registry-identities.json`。临时探针 Repo 会立即删除；探针清理失败时，注册会终止并报告残留 Repo。

机器可读注册需要显式给出资源类型、资源名称和确认参数：

```bash
fw register ./assets/dataset-aime25 \
  --type dataset \
  --name aime25 \
  --description "AIME 2025 评测集" \
  --tag v2 \
  --yes \
  --output json
```
