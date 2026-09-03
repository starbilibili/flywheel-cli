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
from flywheel.evaluation.lbg import LbgClient, LbgSettings, submit as submit_lbg
from flywheel.presentation import EvaluationProgress, emit, render_run_status


app = typer.Typer(help="Plan, submit, and inspect Task runs.")


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
    backend: str = typer.Option("lbg", "--backend", help="运行后端：lbg（默认）或 local。"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅构建 LBG 提交参数，不创建远程 Job。"),
) -> None:
    """Submit one Task, using LBG by default or the local development backend."""
    if backend not in {"lbg", "local"}:
        raise EvaluationError("--backend 只能是 lbg 或 local")
    if backend == "lbg":
        run_plan = _plan(config)
        settings = LbgSettings.from_environment(config.resolve().parent)
        command = submit_lbg(run_plan, settings, dry_run=dry_run)
        if dry_run:
            emit({"status": "dry-run", "backend": "lbg", "command": command, "run_dir": str(run_plan.run_dir)}, "text")
        else:
            submission = command
            write_json(run_plan.run_dir / "remote-submission.json", {
                "schema_version": "fw-lbg-submission/v1",
                "backend": "lbg",
                "run_id": run_plan.run_id,
                "submission": submission,
            })
            write_json(run_plan.run_dir / "status.json", {
                **read_json(run_plan.run_dir / "status.json"),
                "status": "submitted",
                "backend": "lbg",
                "remote_submission": str(run_plan.run_dir / "remote-submission.json"),
            })
            emit({"status": "submitted", "backend": "lbg", "submission": submission, "run_dir": str(run_plan.run_dir)}, "text")
        return

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
        f"fw task status {shlex.quote(run_plan.run_id)} "
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
    lbg_job_id: str | None = typer.Option(None, "--lbg-job-id", help="查询 Lebesgue Job ID。"),
    bohr_job_id: str | None = typer.Option(None, "--bohr-job-id", help="查询 Bohrium Job ID。"),
) -> None:
    """Read the latest state of one local run or a remote LBG Job."""

    if lbg_job_id and bohr_job_id:
        raise EvaluationError("--lbg-job-id 和 --bohr-job-id 只能选择一个")
    if lbg_job_id or bohr_job_id:
        settings = LbgSettings.from_environment()
        client = LbgClient(settings)
        remote = client.detail(bohr_job_id) if bohr_job_id else client.find_job(lbg_job_id or "")
        emit({"run_id": run_id, "backend": "lbg", "remote": remote}, output)
        return

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
