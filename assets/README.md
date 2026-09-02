# AIME25 first-evaluation assets

This directory contains the four complete resources required by the first API-based evaluation flow. Each subdirectory is self-describing and can be registered or retrieved independently.

- `dataset-aime25/`: the canonical AIME25 JSONL dataset.
- `evaluator-aime25-openai/`: the evaluator implementation for an OpenAI-compatible model API.
- `evaluation-config-aime25-avg4/`: the immutable evaluation protocol for the first Avg@4 run.
- `model-v4-pro/`: the credential-free OpenAI-compatible API model binding.

Model credentials remain runtime bindings and are never stored in the resource. Historical runs, logs, reports, and platform status are Run Outputs rather than evaluation inputs.
