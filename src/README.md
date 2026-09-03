# Source layout

- `flywheel/auth/`: 平台登录和凭证状态。
- `flywheel/resource/`: 四类资源 manifest、typed adapter 和未来的注册表接入点。
- `flywheel/config/`: `config.yaml` 的读取与结构校验。
- `flywheel/evaluation/`: 数据采样、Run Spec、评测执行和结果持久化。
- `flywheel/runtime/`: `.env` 与模型运行时绑定。
- `flywheel/cli.py`: 只负责组装 `fw` 命令树，不承载业务实现。

当前完整链路：

```bash
fw task submit --config config.yaml
fw task status <run-id> --output-dir ./runs --watch
```

`flywheel/evaluation/background.py` 负责启动本机后台 worker，`worker.py` 负责执行已经固化的执行计划。
