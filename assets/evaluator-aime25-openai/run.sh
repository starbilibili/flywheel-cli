#!/usr/bin/env bash
set -euo pipefail

resource_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python_bin="${FW_PYTHON:-python3}"

exec "${python_bin}" "${resource_dir}/scripts/run_vllm_pilot.py" "$@"
