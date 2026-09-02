"""Commands for planning, submitting, and reading local evaluation runs."""

from __future__ import annotations

import os
import shlex
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.live import Live

from flywheel.config import load_evaluation_request
from flywheel.errors import EvaluationError
from flywheel.evaluation.background import launch_background
from flywheel.evaluation.planner import create_run_plan
from flywheel.evaluation.runner import execute_run, expected_attempts
from flywheel.evaluation.store import read_json, write_json
from flywheel.presentation import EvaluationProgress, emit, render_run_status


app = typer.Typer(help="Plan, submit, and inspect evaluation runs.")


def _process_exists(pid: object) -> bool:
    """Return whether an integer PID still identifies a visible process."""

    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _current_status(path: Path) -> dict[str, object]:
    """Read status and repair a stale running state after process loss."""

    current = read_json(path)
    if str(current.get("status")) not in {"queued", "running"}:
        return current
    if _process_exists(current.get("worker_pid")):
        return current
    current["status"] = "failed"
    current["error"] = "评测进程已退出；详细信息请查看输出目录中的日志"
    write_json(path, current)
    return current


def _plan(config: Path):
    config_path = config.resolve()
    request = load_evaluation_request(config_path)
    output_root = Path(request.output_dir)
    if not output_root.is_absolute():
        output_root = config_path.parent / output_root
    return create_run_plan(request, config_path.parent, output_root.resolve())


@app.command("plan")
def plan(
    config: Path = typer.Option(..., "--config"),
    output: str = typer.Option("text", "--output", "-o"),
) -> None:
    """Resolve resources and sampling into an immutable local Run Spec."""

    run_plan = _plan(config)
    emit(
        {
            "run_id": run_plan.run_id,
            "status": "planned",
            "run_spec": str(run_plan.run_dir / "run-spec.json"),
        },
        output,
    )


@app.command("submit")
def submit(
    config: Path = typer.Option(..., "--config"),
    wait: bool = typer.Option(False, "--wait"),
) -> None:
    """Submit one local evaluation, detached by default or watched with --wait."""

    run_plan = _plan(config)
    if wait:
        display = EvaluationProgress(run_plan.run_id, run_plan.run_dir, expected_attempts(run_plan))
        with display:
            result = execute_run(run_plan, display.update)
            display.mark_succeeded(Path(str(result["run_dir"])) / "result.json")
        return

    worker_pid, worker_log = launch_background(run_plan)
    output_root = run_plan.run_dir.parent
    status_command = (
        f"fw eval status {shlex.quote(run_plan.run_id)} "
        f"--output-dir {shlex.quote(str(output_root))} --watch"
    )
    emit(
        {
            "run_id": run_plan.run_id,
            "status": "queued",
            "worker_pid": worker_pid,
            "output_dir": str(run_plan.run_dir),
            "worker_log": str(worker_log),
            "status_command": status_command,
        },
        "text",
    )


@app.command("status")
def status(
    run_id: str,
    output_dir: Path = typer.Option(Path("runs"), "--output-dir"),
    watch: bool = typer.Option(False, "--watch"),
    output: str = typer.Option("text", "--output", "-o"),
) -> None:
    """Read the latest state of one local run."""

    path = output_dir.resolve() / run_id / "status.json"
    if not path.is_file():
        raise EvaluationError(f"Run status does not exist: {path}")
    if not watch:
        emit(_current_status(path), output)
        return

    console = Console(stderr=output == "json")
    terminal = {"succeeded", "failed", "cancelled"}
    current = _current_status(path)
    with Live(render_run_status(current, path.parent), console=console, refresh_per_second=4) as live:
        while str(current.get("status")) not in terminal:
            time.sleep(0.25)
            current = _current_status(path)
            live.update(render_run_status(current, path.parent), refresh=True)
    if output == "json":
        emit(current, output)


@app.command("result")
def result(
    run_id: str,
    output_dir: Path = typer.Option(Path("runs"), "--output-dir"),
    output: str = typer.Option("text", "--output", "-o"),
) -> None:
    """Read the final result of one completed local run."""

    path = output_dir.resolve() / run_id / "result.json"
    if not path.is_file():
        raise EvaluationError(f"Run result does not exist: {path}")
    emit(read_json(path), output)
