"""Detached local worker entrypoint."""

from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

from flywheel.evaluation.planner import load_run_plan
from flywheel.evaluation.runner import execute_run
from flywheel.evaluation.store import read_json, update_status


def _wait_until_registered(run_dir: Path, worker_pid: int) -> None:
    """Avoid racing the parent's initial queued-state write."""

    status_path = run_dir / "status.json"
    for _ in range(500):
        try:
            status = read_json(status_path)
        except (OSError, ValueError):
            status = {}
        if status.get("worker_pid") == worker_pid:
            return
        time.sleep(0.01)


def _failure_fields(run_dir: Path, worker_pid: int, worker_log: Path) -> dict[str, object]:
    """Preserve observable progress when a worker terminates with an error."""

    try:
        current = read_json(run_dir / "status.json")
    except (OSError, ValueError):
        current = {}
    fields = {
        key: value
        for key, value in current.items()
        if key not in {"run_id", "status", "error"}
    }
    fields.setdefault("worker_pid", worker_pid)
    fields.setdefault("worker_log", str(worker_log))
    return fields


def main() -> int:
    """Load and execute one immutable run plan."""

    if len(sys.argv) != 2:
        print("usage: python -m flywheel.evaluation.worker RUN_DIR", file=sys.stderr)
        return 2
    run_dir = Path(sys.argv[1]).resolve()
    worker_pid = os.getpid()
    worker_log = run_dir / "worker.log"
    try:
        _wait_until_registered(run_dir, worker_pid)
        plan = load_run_plan(run_dir)
        execute_run(
            plan,
            lambda event: None,
            worker_pid=worker_pid,
            worker_log=worker_log,
        )
    except Exception as error:
        update_status(
            run_dir,
            run_dir.name,
            "failed",
            error=str(error),
            **_failure_fields(run_dir, worker_pid, worker_log),
        )
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
