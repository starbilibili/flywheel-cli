"""Launch a local evaluation worker independently from the submitting shell."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from flywheel.errors import EvaluationError
from flywheel.evaluation.planner import RunPlan
from flywheel.evaluation.runner import expected_attempts
from flywheel.evaluation.store import update_status


def launch_background(plan: RunPlan) -> tuple[int, Path]:
    """Start one detached worker and return its PID and log path."""

    worker_log = plan.run_dir / "worker.log"
    script_log = plan.run_dir / "script.log"
    command = [sys.executable, "-m", "flywheel.evaluation.worker", str(plan.run_dir)]
    try:
        with worker_log.open("a", encoding="utf-8") as log_stream:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=log_stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
    except OSError as error:
        update_status(plan.run_dir, plan.run_id, "failed", error=str(error))
        raise EvaluationError(f"Unable to start evaluation worker: {error}") from error

    update_status(
        plan.run_dir,
        plan.run_id,
        "queued",
        worker_pid=process.pid,
        worker_log=str(worker_log),
        script_log=str(script_log),
        total=expected_attempts(plan),
        completed=0,
        succeeded=0,
        failed=0,
        invalid=0,
    )
    return process.pid, worker_log
