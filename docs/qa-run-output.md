# QA Run Output

A QA Script consumes `fw-effective-run-config/v1` and writes one `fw-qa-attempt/v1` JSON file per physical attempt plus one `fw-qa-summary/v1` JSON file.

Each attempt records the sample and attempt IDs, model input, inference configuration, raw model output, parsed answer, ground truth, scorer identity, metric score, status, token usage, and latency. The summary records terminal counts and aggregate metrics.

Flywheel wraps these files in `fw-qa-run-output/v1`, which also pins the Dataset, Model, Evaluation Config, and Script resource digests and links the immutable Effective Run Config. Model credentials are referenced by environment-variable name and are never persisted.
