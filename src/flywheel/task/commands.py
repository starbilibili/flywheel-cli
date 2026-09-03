"""Create Task resources from four immutable resource Snapshot digests."""

from __future__ import annotations

from datetime import datetime, timezone
import tempfile
from pathlib import Path
import re

import typer
import yaml
from rich.console import Console
from rich.table import Table

from flywheel.auth.vouch import audience_identity
from flywheel.errors import FlywheelError
from flywheel.presentation import emit
from flywheel.resource.index import RegistrationIndexRequest, SnapshotIndexRecord, TagIndexRecord, index_registration
from flywheel.resource.registration import ResourceType, new_resource_id, prepare_registration, registry_repo_path, validate_resource_name
from flywheel.resource.registry import ResourceCandidate, SnapshotCandidate, list_snapshots, publish_resource_snapshot, resolve_registry_repo, search_resources
from flywheel.evaluation.commands import result as _result_command
from flywheel.evaluation.commands import status as _status_command
from flywheel.evaluation.commands import submit as _submit_command
from flywheel.evaluation.commands import plan as _plan_command


app = typer.Typer(help="引导创建并注册 Task 资源。")
_console = Console()
_KINDS = (("dataset", ResourceType.DATASET), ("config", ResourceType.CONFIG), ("script", ResourceType.SCRIPT), ("model", ResourceType.MODEL))

# Execution commands share the Task namespace. Their implementation remains in
# evaluation.commands until the LBG executor replaces the local runner.
app.command("submit")(_submit_command)
app.command("status")(_status_command)
app.command("result")(_result_command)
app.command("plan")(_plan_command)


def _step(number: int, title: str) -> None:
    _console.print(f"\n[bold cyan][{number}/5][/bold cyan] {title}")


def _digest(value: str) -> str:
    value = value.strip().lower()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise FlywheelError("Snapshot Digest 必须是 sha256:<64位十六进制摘要>")
    return value


def _choose_resource(kind: ResourceType, name: str) -> ResourceCandidate:
    result = search_resources(kind, name)
    if not result.available:
        raise FlywheelError(f"无法搜索 {kind.value} 资源：{result.reason}")
    values = tuple(item for item in result.values if isinstance(item, ResourceCandidate))
    if not values:
        raise FlywheelError(f"没有找到名为 {name} 的 {kind.value} 资源")
    table = Table(title=f"选择 {kind.value.capitalize()} 资源")
    table.add_column("序号", style="bold cyan")
    table.add_column("Resource ID")
    table.add_column("描述", overflow="fold")
    for index, item in enumerate(values, 1):
        table.add_row(str(index), item.resource_id, item.description or "未设置描述")
    _console.print(table)
    while True:
        selected = typer.prompt("请选择资源", default="1")
        if selected.isdigit() and 1 <= int(selected) <= len(values):
            return values[int(selected) - 1]
        typer.echo(f"请输入 1-{len(values)}。", err=True)


def _snapshot(repo: str) -> str:
    result = list_snapshots(repo)
    if not result.available:
        raise FlywheelError(f"无法读取资源版本：{result.reason}")
    values = tuple(item for item in result.values if isinstance(item, SnapshotCandidate))
    if not values:
        return _digest(typer.prompt("该资源没有可见 Tag，请输入 Manifest Digest"))
    ordered = tuple(sorted(values, key=lambda item: item.tag != "latest"))
    table = Table(title="选择资源版本")
    table.add_column("序号", style="bold cyan")
    table.add_column("Tag")
    table.add_column("Manifest Digest", overflow="fold")
    for index, item in enumerate(ordered, 1):
        table.add_row(str(index), item.tag or "-", item.digest)
    _console.print(table)
    selected = typer.prompt("请选择版本，或直接输入 Manifest Digest", default="1")
    if selected.isdigit() and 1 <= int(selected) <= len(ordered):
        return _digest(ordered[int(selected) - 1].digest)
    return _digest(selected)


def _dependency(label: str, kind: ResourceType, provided: str | None) -> str:
    if provided:
        return _digest(provided)
    name = validate_resource_name(typer.prompt(f"请输入 {label} 资源名称"))
    return _snapshot(_choose_resource(kind, name).storage_repo)


def _existing_task(name: str) -> ResourceCandidate | None:
    result = search_resources(ResourceType.TASK, name)
    if not result.available:
        raise FlywheelError(f"无法查询已有 Task：{result.reason}")
    values = tuple(item for item in result.values if isinstance(item, ResourceCandidate))
    if not values:
        return None
    if len(values) == 1 and not typer.confirm("发现同名 Task，是否为它追加新 Snapshot？", default=True):
        raise FlywheelError("已取消 Task 资源注册")
    table = Table(title="选择要追加版本的 Task")
    table.add_column("序号", style="bold cyan")
    table.add_column("Resource ID")
    table.add_column("描述", overflow="fold")
    for index, item in enumerate(values, 1):
        table.add_row(str(index), item.resource_id, item.description or "未设置描述")
    _console.print(table)
    selected = typer.prompt("请选择 Task", default="1")
    if not selected.isdigit() or not 1 <= int(selected) <= len(values):
        raise FlywheelError("Task 选择无效")
    return values[int(selected) - 1]


def _tag(value: str | None) -> str | None:
    normalized = (value or "").strip()
    if not normalized:
        return None
    if normalized == "latest" or any(char.isspace() for char in normalized):
        raise FlywheelError("Tag 不能是 latest，也不能包含空白字符")
    return normalized


def _task_manifest(task_type: str, refs: dict[str, str]) -> str:
    return yaml.safe_dump(
        {"schema_version": "fw-task/v1", "task_type": task_type, "resources": refs},
        allow_unicode=True,
        sort_keys=False,
    )


@app.command("create")
def create(
    task_type: str | None = typer.Option(None, "--type", help="eval 或 train。"),
    name: str | None = typer.Option(None, "--name", help="Task 名称。"),
    dataset: str | None = typer.Option(None, "--dataset", help="Dataset Manifest Digest。"),
    config: str | None = typer.Option(None, "--config", help="Config Manifest Digest。"),
    script: str | None = typer.Option(None, "--script", help="Script Manifest Digest。"),
    model: str | None = typer.Option(None, "--model", help="Model Manifest Digest。"),
    tag: str | None = typer.Option(None, "--tag", help="可选 Tag。"),
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过最终确认。"),
    output: str = typer.Option("text", "--output", "-o"),
) -> None:
    """Interactively create one Task Resource, or run fully with options."""
    supplied = (task_type, name, dataset, config, script, model)
    interactive = not any(supplied)
    if any(supplied) and not all(supplied):
        raise FlywheelError("带参模式必须同时提供 --type、--name 和四个资源 Digest")
    if output == "json" and interactive:
        raise FlywheelError("JSON 输出必须使用带参模式")
    _step(1, "设置任务类型")
    selected_type = (task_type or typer.prompt("任务类型（eval/train）", default="eval")).strip().lower()
    if selected_type not in {"eval", "train"}:
        raise FlywheelError("任务类型只能是 eval 或 train")
    _step(2, "设置任务名称")
    task_name = validate_resource_name(name or typer.prompt("请输入 Task 名称"))
    _step(3, "选择依赖资源")
    refs = {label: _dependency(label, kind, provided) for (label, kind), provided in zip(_KINDS, (dataset, config, script, model))}
    _console.print("[green]四类依赖资源已选择。[/green]")
    _step(4, "设置版本标签")
    selected_tag = _tag(tag if tag is not None else (typer.prompt("版本 Tag（可选，直接回车跳过）", default="") if interactive else None))
    resource_name = f"{selected_type}-{task_name}"
    existing_task = _existing_task(resource_name) if interactive else None
    if existing_task and selected_tag:
        history = list_snapshots(existing_task.storage_repo)
        if not history.available:
            raise FlywheelError(f"无法确认 Task Tag 是否已存在：{history.reason}")
        if any(isinstance(item, SnapshotCandidate) and item.tag == selected_tag for item in history.values):
            raise FlywheelError(f"标签 {selected_tag} 已绑定旧 Snapshot，不能转移；请使用新的标签")
    created_by = audience_identity("wenyon-svc")
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    description = f"{resource_name} 的 Task 资源，由 {created_by} 于 {created_at.replace('T', ' ').replace('Z', ' UTC')} 创建。"
    resource_id = existing_task.resource_id if existing_task else new_resource_id()
    repo = resolve_registry_repo(existing_task.storage_repo if existing_task else registry_repo_path(ResourceType.TASK, resource_name, resource_id))
    manifest_text = _task_manifest(selected_type, refs)
    _step(5, "确认并注册")
    _console.print(f"\n[bold]Task 资源预览：{resource_name}[/bold]")
    preview = Table(show_header=False)
    preview.add_column("字段", style="dim")
    preview.add_column("内容", overflow="fold")
    preview.add_row("任务类型", selected_type)
    for label, digest in refs.items():
        preview.add_row(label, digest)
    preview.add_row("Tag", selected_tag or "未设置")
    _console.print(preview)
    _console.print("[bold]即将注册的 task.yaml[/bold]")
    _console.print(manifest_text, end="")
    if not yes and not typer.confirm("确认注册 Task 资源？", default=True):
        raise FlywheelError("已取消 Task 资源注册")
    with tempfile.TemporaryDirectory(prefix="fw-task-") as temporary:
        path = Path(temporary) / "task.yaml"
        path.write_text(manifest_text, encoding="utf-8")
        plan = prepare_registration(path, ResourceType.TASK, resource_name, description=description, created_by=created_by, created_at=created_at, tag=selected_tag)
        published = publish_resource_snapshot(plan.path, repo, parent_snapshot=None, tag=selected_tag, annotations={"description": existing_task.description if existing_task and existing_task.description else description, "flywheel.resource_id": resource_id, "flywheel.resource_type": "task", "flywheel.resource_name": resource_name, "flywheel.created_by": existing_task.created_by if existing_task else created_by, "flywheel.created_at": existing_task.created_at if existing_task else created_at, "flywheel.task_type": selected_type}, new_resource=existing_task is None)
    indexed_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    tags = [TagIndexRecord(published.repo, "latest", "system", published.digest, indexed_at)]
    if selected_tag:
        tags.append(TagIndexRecord(published.repo, selected_tag, "user", published.digest, indexed_at))
    index_registration(RegistrationIndexRequest(SnapshotIndexRecord(published.digest, published.repo, resource_name, "task", description, created_by, created_at, indexed_at, None), tuple(tags)))
    result = {"resource_type": "task", "resource_name": resource_name, "resource_id": resource_id, "storage_repo": published.repo, "snapshot_ref": f"{published.repo}@{published.digest}", "tag": selected_tag}
    emit(result, output)
