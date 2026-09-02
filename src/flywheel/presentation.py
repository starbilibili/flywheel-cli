"""Stable human, JSON, and live evaluation output helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.text import Text

from flywheel.errors import FlywheelError


def emit(value: Any, output: str) -> None:
    """Write a final command result to stdout."""

    if output == "json":
        typer.echo(json.dumps(value, ensure_ascii=False, sort_keys=True))
        return
    if output != "text":
        raise FlywheelError("Output format must be 'text' or 'json'")
    if isinstance(value, dict):
        for key, item in value.items():
            typer.echo(f"{key}: {item}")
    elif isinstance(value, list):
        for item in value:
            typer.echo(json.dumps(item, ensure_ascii=False, sort_keys=True))
    else:
        typer.echo(str(value))


def render_run_status(value: dict[str, Any], run_dir: Path) -> RenderableType:
    """Render a compact status snapshot for a watched background run."""

    status = str(value.get("status", "unknown"))
    completed = int(value.get("completed", 0))
    total = int(value.get("total", 0))
    succeeded = int(value.get("succeeded", 0))
    failed = int(value.get("failed", 0))
    invalid = int(value.get("invalid", 0))
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]评测进度"),
        BarColumn(),
        TaskProgressColumn(),
        expand=True,
    )
    progress.add_task("evaluation", total=total or None, completed=completed)
    counters = Text.assemble(
        (f"成功 {succeeded}", "bold green"),
        "    ",
        (f"失败 {failed}", "bold red"),
        "    ",
        (f"运行无效 {invalid}", "bold yellow"),
    )
    if status in {"queued", "running"} and completed == 0:
        activity = Text("任务已启动，正在等待首批结果…", style="cyan")
    elif status in {"queued", "running"}:
        activity = Text("任务运行中，详细结果持续写入输出目录。", style="cyan")
    else:
        activity = Text("任务已结束。", style="dim")
    details = Text.from_markup(
        f"status: [bold]{status}[/bold]\n"
        f"run_id: {value.get('run_id', run_dir.name)}\n"
        f"output_dir: {run_dir}"
    )
    return Group(details, progress, counters, activity)


class EvaluationProgress:
    """Render bounded evaluation progress without exposing per-attempt payloads."""

    def __init__(self, run_id: str, output_dir: Path, total: int) -> None:
        self.run_id = run_id
        self.output_dir = output_dir
        self.total = total
        self.completed = 0
        self.succeeded = 0
        self.failed = 0
        self.invalid = 0
        self._final_status = "failed"
        self._result_path: Path | None = None
        self._console = Console(stderr=True)
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]评测进度"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=self._console,
            expand=True,
        )
        self._task_id = self._progress.add_task("evaluation", total=total)
        self._live = Live(self._render(), console=self._console, refresh_per_second=10)

    def _render(self) -> RenderableType:
        counters = Text.assemble(
            (f"成功 {self.succeeded}", "bold green"),
            "    ",
            (f"失败 {self.failed}", "bold red"),
            "    ",
            (f"运行无效 {self.invalid}", "bold yellow"),
        )
        activity = Text(
            "任务已启动，正在等待首批结果…"
            if self.completed == 0
            else "任务运行中，详细结果持续写入输出目录。",
            style="cyan",
        )
        return Group(self._progress, counters, activity)

    def __enter__(self) -> "EvaluationProgress":
        self._console.print(f"run_id: {self.run_id}")
        self._console.print(f"output_dir: {self.output_dir}")
        self._live.start()
        return self

    def update(self, event: dict[str, object]) -> None:
        """Apply one terminal attempt event to the visible counters."""

        outcome = event.get("outcome")
        if outcome == "succeeded":
            self.succeeded += 1
        elif outcome == "failed":
            self.failed += 1
        else:
            self.invalid += 1
        self.completed += 1
        self._progress.update(self._task_id, completed=min(self.completed, self.total))
        self._live.update(self._render(), refresh=True)

    def mark_succeeded(self, result_path: Path) -> None:
        """Record the successful terminal state rendered after the live view closes."""

        self._final_status = "succeeded"
        self._result_path = result_path

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self._live.stop()
        self._console.print(f"status: {self._final_status}")
        if self._result_path is not None:
            self._console.print(f"result: {self._result_path}")
        self._console.print(f"output_dir: {self.output_dir}")
