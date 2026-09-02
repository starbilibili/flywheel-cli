# Asset Metadata

## Common metadata

- `name`: `aime25-avg4`
- `type`: `config`
- `version`: `v1`
- `status`: `candidate`
- `description`: 首次 AIME25 API 评测使用的 Avg@4 协议配置。
- `immutable`: `true`

## Evaluation protocol metadata

- `benchmark`: `AIME25`
- `metric`: `mean_pass_at_1`
- `logical_slots`: `30 × 4 = 120`
- `prompt_contract`: 原始题面加配置中定义的统一提示后缀
- `grading`: 提取最后一个 `\boxed{integer}`，与标准答案精确匹配
- `attempt_policy`: 仅 `stop` 状态计为有效尝试；无效尝试不进入分母，并在同一逻辑槽位补跑

## Payload integrity

| 文件 | 作用 | SHA-256 |
| --- | --- | --- |
| `evaluation.json` | 定义评测协议及默认参数 | `336b04a587f056451a324309c3887a6436bc986873fa2ddea214ce473fbc8984` |

## Asset boundary

本 Asset 只描述评测协议，不包含数据路径或 Asset ID、模型 API、凭证和输出目录。后续由 Bundle 将 Dataset、Evaluator、Evaluation Config 与运行时 API 绑定组合起来。
