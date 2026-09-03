# AIME25 OpenAI 评测脚本

这是一个 Script 资源，唯一约定是资源根目录提供可执行的 `run.sh`。
脚本接收 `--run-config <path>`，由脚本自行解释数据集字段和评测协议。

当前实现使用 OpenAI-compatible API，并将每次尝试写入配置指定的输出目录；模型凭据只通过运行时环境变量读取，不写入资源包或结果文件。
