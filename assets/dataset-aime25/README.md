# Asset Metadata

## Common metadata

- `name`: `aime25`
- `type`: `dataset`
- `version`: `opencompass-a6ad95f6`
- `status`: `candidate`
- `description`: AIME 2025 的 30 道原始题目及标准答案。
- `immutable`: `true`

## Dataset metadata

- `format`: `jsonl`
- `records`: `30`
- `schema`: `question:string, answer:string`
- `source`: `opencompass/AIME2025`
- `source_revision`: `a6ad95f611d72cf628a80b58bd0432ef6638f958`

| Split | 文件 | 记录数 |
| --- | --- | ---: |
| AIME 2025 I / test | `data/aime2025-I.jsonl` | 15 |
| AIME 2025 II / test | `data/aime2025-II.jsonl` | 15 |

## Payload integrity

| 文件 | SHA-256 |
| --- | --- |
| `data/aime2025-I.jsonl` | `b91b3c96f05d9635d2a0692b124ebe023c1ff59cb19c074275e6c4b349d0659e` |
| `data/aime2025-II.jsonl` | `16a2dcfbbf9db1b11f8a69a3ba5e4cac73e3641b19a37e2307e9c12240bbed5e` |

## Asset boundary

本 Asset 只包含与模型和评测器无关的规范化原始数据。Parquet 等特定执行器所需的派生格式不属于该 Asset。
