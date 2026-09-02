# Asset Metadata

## Common metadata

- `name`: `aime25-openai-evaluator`
- `type`: `evaluator`
- `version`: `v2`
- `status`: `candidate`
- `description`: 通过 OpenAI-compatible API 执行 AIME25 Avg@n 评测，并按整数答案精确判分。
- `immutable`: `true`

## Evaluator metadata

- `runtime`: `python3`
- `dependencies`: Python 标准库
- `protocol`: `fw-qa-script/v1`
- `dataset_contract`: 单个 JSONL 文件或包含 JSONL 的目录；每条记录必须包含 `question` 和 `answer`
- `inference_protocol`: `POST {endpoint}/v1/chat/completions`
- `input_contract`: 接收一份 Effective Run Config，自行加载 Dataset、Model 和 Evaluation Config
- `credentials`: 通过 Model 资源声明的环境变量读取，不写入配置和结果
- `answer_extraction`: 提取模型输出中最后一个 `\boxed{integer}`
- `grading`: 与标准整数答案精确匹配
- `outputs`: 每个题目与 seed 的请求、响应、答案和得分，以及整体聚合结果
- `attempt_eligibility`: 仅 `finish_reason=stop` 的输出进入最终分数；其他输出标记为 `operational_invalid`

## Default parameters

| 参数 | 默认值 |
| --- | ---: |
| `problem_count` | 30 |
| `seeds` | `0,1,2,3` |
| `concurrency` | 8 |
| `max_tokens` | 40960 |
| `temperature` | 1.0 |
| `top_p` | 0.95 |
| `top_k` | -1 |
| `timeout_sec` | 1800 |
| `stream` | `true` |

## Payload integrity

| 文件 | 作用 | SHA-256 |
| --- | --- | --- |
| `run.sh` | Script 资源统一入口，将 Effective Task Config 交给内部评测实现 | `d2c1f5b71029ddc68ab048f8559ff0794a0d93d7c41cb7ae7b2bf75c615e7141` |
| `scripts/run_vllm_pilot.py` | 调度所有题目与采样槽位，并聚合完成状态 | `ec8871d3b79255b830b6a70701c893116718c25208bb2c7afc2ad007306998c0` |
| `scripts/run_vllm_canary.py` | 执行单题请求、抽取答案并记录判分结果 | `c15c4e9b40653ad2c96ac304b9f91afcca922b6ed5221af0f3ae38967cc0e56a` |

## Asset boundary

本资源定义评测行为，不包含模型、凭证、数据集或运行结果。CLI 按 Script 资源合同执行资源根目录下的 `./run.sh`，该约定不暴露给 Task Spec。
