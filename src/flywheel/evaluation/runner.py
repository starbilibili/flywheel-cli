"""Execute a planned evaluation through the stable Script protocol."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Callable

from flywheel.errors import EvaluationError
from flywheel.evaluation.planner import RunPlan
from flywheel.evaluation.store import read_json, update_status, write_json


ProgressSink = Callable[[dict[str, object]], None]


def _command(plan: RunPlan) -> list[str]:
    """Build the stable Script protocol invocation."""

    return [*plan.script_command, "--run-config", str(plan.effective_run_config)]


def expected_attempts(plan: RunPlan) -> int:
    """Return the planned number of Script attempt events."""

    return plan.expected_attempts


def _attempt_event(line: str) -> dict[str, object] | None:
    """Translate one Script JSON line into a terminal progress event."""

    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict) or value.get("event") != "attempt":
        return None
    outcome = value.get("outcome")
    if outcome not in {"succeeded", "failed", "invalid"}:
        return None
    return {
        "sample_id": value.get("sample_id"),
        "attempt_id": value.get("attempt_id"),
        "outcome": outcome,
    }


def _load_summary(
    path: Path, fallback: dict[str, int]
) -> tuple[dict[str, object], dict[str, int]]:
    """Validate one QA Script summary and return its terminal counters."""

    summary = read_json(path)
    if summary.get("schema_version") != "fw-qa-summary/v1":
        raise EvaluationError(f"Unsupported Script summary schema: {path}")
    counts = summary.get("counts")
    if not isinstance(counts, dict):
        raise EvaluationError(f"Script summary counts must be an object: {path}")
    counters = {
        "completed": int(counts.get("completed", fallback["completed"])),
        "succeeded": int(counts.get("succeeded", fallback["succeeded"])),
        "failed": int(counts.get("failed", fallback["failed"])),
        "invalid": int(counts.get("invalid", fallback["invalid"])),
    }
    return summary, counters


def execute_run(
    plan: RunPlan,
    progress: ProgressSink,
    *,
    worker_pid: int | None = None,
    worker_log: Path | None = None,
) -> dict[str, object]:
    """Run the Script synchronously and return the final result contract."""

    summary_path = plan.run_dir / "script-summary.json"
    log_path = plan.run_dir / "script.log"
    total = expected_attempts(plan)
    counters = {"completed": 0, "succeeded": 0, "failed": 0, "invalid": 0}
    observable = {
        "total": total,
        "script_log": str(log_path),
        # Record the owning process for both detached and --wait runs. Status
        # readers can then distinguish a slow run from an abandoned one.
        "worker_pid": worker_pid if worker_pid is not None else os.getpid(),
        **({"worker_log": str(worker_log)} if worker_log is not None else {}),
    }

    def report(event: dict[str, object]) -> None:
        """Persist and forward one completed-attempt event."""

        outcome = str(event.get("outcome", "invalid"))
        counters["completed"] += 1
        counters[outcome if outcome in {"succeeded", "failed"} else "invalid"] += 1
        update_status(plan.run_dir, plan.run_id, "running", **observable, **counters)
        progress(event)

    update_status(plan.run_dir, plan.run_id, "running", **observable, **counters)
    command = _command(plan)
    try:
        with log_path.open("w", encoding="utf-8") as log_stream:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            assert process.stdout is not None
            for line in process.stdout:
                log_stream.write(line)
                log_stream.flush()
                event = _attempt_event(line)
                if event is not None:
                    report(event)
            return_code = process.wait()
    except OSError as error:
        update_status(
            plan.run_dir, plan.run_id, "failed", error=str(error), **observable, **counters
        )
        raise EvaluationError(f"Unable to start Script: {error}") from error

    if return_code != 0 or not summary_path.is_file():
        message = f"Script failed with exit code {return_code}; see {log_path}"
        update_status(
            plan.run_dir, plan.run_id, "failed", error=message, **observable, **counters
        )
        raise EvaluationError(message)
    try:
        summary, counters = _load_summary(summary_path, counters)
    except (OSError, ValueError, EvaluationError) as error:
        message = f"Invalid Script summary; see {log_path}: {error}"
        update_status(
            plan.run_dir, plan.run_id, "failed", error=message, **observable, **counters
        )
        raise EvaluationError(message) from error
    run_spec = read_json(plan.run_dir / "run-spec.json")
    result = {
        "schema_version": "fw-qa-run-output/v1",
        "run_id": plan.run_id,
        "status": "succeeded",
        "resources": run_spec["resources"],
        "effective_run_config": str(plan.effective_run_config),
        "attempts": str(plan.run_dir / "attempts"),
        "summary": summary,
        "run_dir": str(plan.run_dir),
        "run_spec": str(plan.run_dir / "run-spec.json"),
        "selection": str(plan.run_dir / "selection.json"),
    }
    write_json(plan.run_dir / "result.json", result)
    update_status(
        plan.run_dir,
        plan.run_id,
        "succeeded",
        result=str(plan.run_dir / "result.json"),
        **observable,
        **counters,
    )
    return result
