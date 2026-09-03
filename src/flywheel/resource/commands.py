"""Commands for local resources and registry integration."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from flywheel.errors import FlywheelError
from flywheel.auth.vouch import audience_identity
from flywheel.presentation import emit
from flywheel.resource.registration import (
    RegistrationPlan,
    ResourceType,
    prepare_registration,
    validate_resource_name,
)
from flywheel.resource.index import (
    RegistrationIndexRequest,
    SnapshotIndexRecord,
    TagIndexRecord,
    index_configured,
    index_registration,
    search_index,
)
from flywheel.resource.registry import (
    ResourceCandidate,
    SnapshotCandidate,
    list_snapshots,
    publish_resource_snapshot,
    resolve_registry_repo,
    search_resources,
)
from flywheel.resource.service import inspect_resource


app = typer.Typer(help="Plan, register, find, and inspect Flywheel resources.")
_console = Console()
_TYPE_CHOICES = (
    ("1", ResourceType.DATASET, "Dataset"),
    ("2", ResourceType.MODEL, "Model"),
    ("3", ResourceType.CONFIG, "Config"),
    ("4", ResourceType.SCRIPT, "Script"),
    ("5", ResourceType.TASK, "Task"),
)


def _root(path: Path) -> Path:
    return path.resolve()


@app.command("plan")
def plan(
    path: Path,
    output: str = typer.Option("text", "--output", "-o"),
) -> None:
    """Describe how a local file or directory will be registered."""

    resolved = path.resolve()
    if not resolved.is_dir():
        raise FlywheelError(f"Resource does not exist: {resolved}")
    details = inspect_resource(f"local:{resolved}", Path.cwd())
    emit(
        {
            "path": str(resolved),
            "resource_type": details["type"],
            "adapter": details["adapter"],
            "package_kind": "bundle",
            "provider": "wenyon",
            "action": "register",
        },
        output,
    )


def _choose_resource_type() -> ResourceType:
    table = Table(title="选择资源类型", show_header=False)
    table.add_column("序号", style="bold cyan", width=4)
    table.add_column("类型", style="bold")
    for number, _, label in _TYPE_CHOICES:
        table.add_row(number, label)
    _console.print(table)
    choices = {number: resource_type for number, resource_type, _ in _TYPE_CHOICES}
    while True:
        selected = typer.prompt("请输入序号", default="1")
        if selected in choices:
            return choices[selected]
        typer.echo("请输入 1、2、3、4 或 5。", err=True)


def _show_registration(plan: RegistrationPlan) -> None:
    table = Table(title="资源注册信息", show_header=False)
    table.add_column("字段", style="dim")
    table.add_column("内容", overflow="fold")
    table.add_row("本地路径", plan.path)
    table.add_row("资源类型", plan.resource_type.capitalize())
    table.add_row("资源名称", plan.resource_name)
    table.add_row("Resource ID", plan.resource_id)
    table.add_row("资源描述", plan.description)
    repo_label = "Storage Repo" if plan.status == "registered" else "Storage Repo（预估）"
    table.add_row(repo_label, plan.storage_repo)
    table.add_row("父 Snapshot", plan.parent_snapshot or "无（新资源）")
    table.add_row("Snapshot Ref", plan.snapshot_ref or "上传完成后生成")
    table.add_row("Tag", plan.tag or "未设置")
    if plan.status == "registered":
        search_status = (
            "可搜索" if plan.index_status == "synced" else "稍后可搜索"
        )
        table.add_row("搜索状态", search_status)
    table.add_row("文件数量", str(plan.file_count))
    table.add_row("总大小", f"{plan.size_bytes:,} bytes")
    _console.print(table)


def _resource_name(value: str | None) -> str:
    if value is not None:
        return validate_resource_name(value)
    _console.print("[dim]用于识别和检索这份资源。[/dim]")
    while True:
        candidate = typer.prompt("请输入资源名称")
        try:
            return validate_resource_name(candidate)
        except FlywheelError as error:
            typer.echo(f"输入无效：{error}", err=True)


def _choose_resource(candidates: tuple[ResourceCandidate, ...]) -> ResourceCandidate | None:
    table = Table(title="发现该任务下的同类型资源")
    table.add_column("序号", style="bold cyan", width=4)
    table.add_column("Resource ID", style="bold", overflow="fold")
    table.add_column("资源描述", overflow="fold")
    table.add_column("创建时间")
    table.add_row("0", "创建新资源", "-", "-")
    for index, candidate in enumerate(candidates, 1):
        table.add_row(
            str(index),
            candidate.resource_id,
            candidate.description or "未设置描述",
            candidate.created_at,
        )
    _console.print(table)
    while True:
        selected = typer.prompt("请选择资源", default="0")
        if selected == "0":
            return None
        if selected.isdigit() and 1 <= int(selected) <= len(candidates):
            return candidates[int(selected) - 1]
        typer.echo(f"请输入 0-{len(candidates)}。", err=True)


def _choose_snapshot(storage_repo: str) -> tuple[str | None, str]:
    result = list_snapshots(storage_repo)
    if not result.available:
        _console.print(f"[yellow]无法读取历史 Snapshot：{result.reason}[/yellow]")
        return None, "unavailable"
    snapshots = tuple(
        value for value in result.values if isinstance(value, SnapshotCandidate)
    )
    if not snapshots:
        return None, "available"
    ordered = tuple(sorted(snapshots, key=lambda item: item.tag != "latest"))
    table = Table(title="选择血缘版本")
    table.add_column("序号", style="bold cyan", width=4)
    table.add_column("Tag")
    table.add_column("Snapshot Digest")
    for index, snapshot in enumerate(ordered, 1):
        table.add_row(str(index), snapshot.tag or "-", snapshot.digest)
    _console.print(table)
    while True:
        selected = typer.prompt("请选择父 Snapshot", default="1")
        if selected.isdigit() and 1 <= int(selected) <= len(ordered):
            return ordered[int(selected) - 1].digest, "available"
        typer.echo(f"请输入 1-{len(ordered)}。", err=True)


def _registration_target(
    resource_type: ResourceType,
    resource_name: str,
    *,
    interactive: bool,
) -> tuple[ResourceCandidate | None, str | None, str]:
    result = search_resources(resource_type, resource_name)
    if not result.available:
        raise FlywheelError(f"无法查询远端资源历史：{result.reason}")
    resources = tuple(
        value for value in result.values if isinstance(value, ResourceCandidate)
    )
    if resources and not interactive:
        raise FlywheelError(
            "Existing resources require text mode to select a lineage parent"
        )
    selected = _choose_resource(resources) if resources else None
    if selected is None:
        return None, None, "available"
    parent, history_status = _choose_snapshot(selected.storage_repo)
    return selected, parent, history_status


def _validate_tag(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if normalized == "latest":
        raise FlywheelError("latest 是系统维护的标签，请使用其他标签")
    if any(character.isspace() for character in normalized):
        raise FlywheelError("版本标签不能包含空白字符")
    return normalized


def _prompt_tag(default: str = "") -> str | None:
    prompt = (
        f"版本标签（直接回车使用 {default}，输入其他内容可修改）"
        if default
        else "版本标签（可选，直接回车跳过）"
    )
    return _validate_tag(
        typer.prompt(prompt, default=default, show_default=bool(default))
    )


def _existing_tag(storage_repo: str, value: str) -> SnapshotCandidate | None:
    result = list_snapshots(storage_repo)
    if not result.available:
        raise FlywheelError(f"无法确认标签是否已存在：{result.reason}")
    return next(
        (
            candidate
            for candidate in result.values
            if isinstance(candidate, SnapshotCandidate) and candidate.tag == value
        ),
        None,
    )


def _tag_conflict_choice(value: str, existing: SnapshotCandidate) -> str:
    _console.print(
        f"[yellow]标签 {value} 已存在：{existing.digest}，"
        f"创建时间 {existing.created_at or '未知'}[/yellow]"
    )
    table = Table(title="请选择处理方式", show_header=False)
    table.add_column("序号", style="bold cyan", width=4)
    table.add_column("操作")
    table.add_row("1", "输入新的标签")
    table.add_row("2", "取消注册")
    _console.print(table)
    while True:
        selected = typer.prompt("请输入序号", default="2")
        if selected in {"1", "2"}:
            return selected
        typer.echo("请输入 1 或 2。", err=True)


def _new_tag() -> str:
    while True:
        value = _validate_tag(typer.prompt("请输入新的标签"))
        if value:
            return value
        typer.echo("标签不能为空。", err=True)


def _tag_value(provided: str | None, storage_repo: str | None) -> str | None:
    value = _prompt_tag(provided or "")
    while value and storage_repo:
        existing = _existing_tag(storage_repo, value)
        if existing is None:
            return value
        choice = _tag_conflict_choice(value, existing)
        if choice == "2":
            raise FlywheelError("已取消资源注册")
        value = _new_tag()
    return value


def _ensure_tag_available(storage_repo: str | None, tag: str | None) -> None:
    """Reject immutable-tag reuse in machine-readable registration."""

    if not storage_repo or not tag:
        return
    existing = _existing_tag(storage_repo, tag)
    if existing is not None:
        raise FlywheelError(
            f"标签 {tag} 已绑定 Snapshot {existing.digest}，不能转移；请使用新的标签"
        )


def _resource_metadata(
    selected: ResourceCandidate | None,
    resource_type: ResourceType,
    resource_name: str,
    provided_description: str | None,
    *,
    interactive: bool,
) -> tuple[str, str, str]:
    if selected is not None and selected.description:
        if provided_description and provided_description.strip() != selected.description:
            raise FlywheelError("资源描述属于 Resource，注册新版本时不能修改")
        return selected.description, selected.created_by, selected.created_at

    created_by = selected.created_by if selected else audience_identity("wenyon-svc")
    created_at = (
        selected.created_at
        if selected
        else datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        )
    )
    readable_time = created_at.replace("T", " ").replace("Z", " UTC")
    type_label = resource_type.value.capitalize()
    default = (
        f"{resource_name} 的 {type_label} 资源，创建于 {readable_time}。"
        if selected
        else f"{resource_name} 的 {type_label} 资源，由 {created_by} 于 {readable_time} 创建。"
    )
    if not interactive:
        return (provided_description or default).strip(), created_by, created_at
    description = typer.prompt(
        "资源描述（直接回车使用默认描述）",
        default=(provided_description or default).strip(),
        show_default=True,
    ).strip()
    if not description:
        description = default
    return description, created_by, created_at


def _manifest_annotations(plan: RegistrationPlan) -> dict[str, str]:
    return {
        "description": plan.description,
        "flywheel.resource_id": plan.resource_id,
        "flywheel.resource_type": plan.resource_type,
        "flywheel.resource_name": plan.resource_name,
        "flywheel.created_by": plan.created_by,
        "flywheel.created_at": plan.created_at,
    }


def _publish(plan: RegistrationPlan, *, new_resource: bool) -> RegistrationPlan:
    registry_repo = resolve_registry_repo(plan.registry_repo)
    with _console.status("正在注册资源…"):
        latest = publish_resource_snapshot(
            plan.path,
            registry_repo,
            parent_snapshot=plan.parent_snapshot,
            tag=plan.tag,
            annotations=_manifest_annotations(plan),
            new_resource=new_resource,
        )
    snapshot_created_at = datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")
    tags = [
        TagIndexRecord(
            storage_repo=latest.repo,
            tag="latest",
            tag_kind="system",
            manifest_digest=latest.digest,
            updated_at=snapshot_created_at,
        )
    ]
    if plan.tag:
        tags.append(
            TagIndexRecord(
                storage_repo=latest.repo,
                tag=plan.tag,
                tag_kind="user",
                manifest_digest=latest.digest,
                updated_at=snapshot_created_at,
            )
        )
    indexed = index_registration(
        RegistrationIndexRequest(
            snapshot=SnapshotIndexRecord(
                manifest_digest=latest.digest,
                storage_repo=latest.repo,
                resource_name=plan.resource_name,
                resource_type=plan.resource_type,
                description=plan.description,
                created_by=plan.created_by,
                resource_created_at=plan.created_at,
                snapshot_created_at=snapshot_created_at,
                parent_manifest_digest=plan.parent_snapshot,
            ),
            tags=tuple(tags),
        )
    )
    return replace(
        plan,
        registry_repo=latest.repo,
        storage_repo=latest.repo,
        snapshot_ref=f"{latest.repo}@{latest.digest}",
        status="registered",
        index_status=indexed.status,
    )


@app.command("register")
def register(
    path: Path,
    resource_type: ResourceType | None = typer.Option(None, "--type"),
    name: str | None = typer.Option(
        None,
        "--name",
        help="资源名称，用于识别和检索。",
    ),
    tag: str | None = typer.Option(None, "--tag", help="可选的用户识别标签。"),
    description: str | None = typer.Option(
        None, "--description", help="新资源的说明；不传则生成默认说明。"
    ),
    yes: bool = typer.Option(False, "--yes", "-y"),
    output: str = typer.Option("text", "--output", "-o"),
) -> None:
    """Prepare one file or directory as one versioned Flywheel resource."""

    if output not in {"text", "json"}:
        raise FlywheelError("Output format must be 'text' or 'json'")
    if output == "json" and (resource_type is None or name is None):
        raise FlywheelError(
            "JSON output requires --type and --name to avoid prompts"
        )
    selected_type = resource_type or _choose_resource_type()
    selected_resource_name = _resource_name(name)
    selected_resource, parent_snapshot, history_status = _registration_target(
        selected_type,
        selected_resource_name,
        interactive=output == "text",
    )
    selected_description, created_by, created_at = _resource_metadata(
        selected_resource,
        selected_type,
        selected_resource_name,
        description,
        interactive=output == "text",
    )
    selected_tag = (
        _tag_value(
            tag,
            selected_resource.storage_repo if selected_resource else None,
        )
        if output == "text"
        else _validate_tag(tag)
    )
    if output == "json":
        _ensure_tag_available(
            selected_resource.storage_repo if selected_resource else None,
            selected_tag,
        )
    plan = prepare_registration(
        path,
        selected_type,
        selected_resource_name,
        resource_id=selected_resource.resource_id if selected_resource else None,
        storage_repo=selected_resource.storage_repo if selected_resource else None,
        parent_snapshot=parent_snapshot,
        tag=selected_tag,
        history_status=history_status,
        description=selected_description,
        created_by=created_by,
        created_at=created_at,
    )
    if output == "text":
        _show_registration(plan)
        if not yes and not typer.confirm("确认注册以上资源？", default=True):
            typer.echo("已取消，未产生任何变更。")
            return
        published = _publish(plan, new_resource=selected_resource is None)
        _show_registration(published)
        typer.echo("资源注册成功。")
    else:
        if not yes:
            raise FlywheelError("JSON 输出执行注册时必须显式传入 --yes")
        emit(
            _publish(plan, new_resource=selected_resource is None).as_dict(),
            output,
        )


@app.command("inspect")
def inspect(
    reference: str,
    output: str = typer.Option("text", "--output", "-o"),
    base_dir: Path = typer.Option(Path.cwd(), "--base-dir"),
) -> None:
    """Inspect one self-describing resource."""

    emit(inspect_resource(reference, _root(base_dir)), output)


@app.command("search")
def search(
    resource_name: str,
    resource_type: ResourceType | None = typer.Option(None, "--type"),
    output: str = typer.Option("text", "--output", "-o"),
) -> None:
    """Search visible resources by their user-facing name."""

    if output not in {"text", "json"}:
        raise FlywheelError("Output format must be 'text' or 'json'")
    selected_name = validate_resource_name(resource_name)
    resources = ()
    if index_configured():
        try:
            resources = search_index(selected_name, resource_type)
        except FlywheelError:
            # FALLBACK: Registry search keeps the basic command usable while
            # the derived index is unavailable or still being rolled out.
            resources = ()
    if resources:
        if output == "json":
            emit([resource.as_dict() for resource in resources], output)
            return
        table = Table(title=f"{selected_name} 的资源")
        table.add_column("类型", style="bold cyan")
        table.add_column("Resource ID", style="bold")
        table.add_column("描述", overflow="fold")
        table.add_column("版本")
        table.add_column("标签", overflow="fold")
        for resource in resources:
            user_tags = [tag for tag in resource.tags if tag != "latest"]
            table.add_row(
                resource.resource_type.capitalize(),
                resource.resource_id,
                resource.description or "未设置描述",
                str(resource.snapshot_count),
                ", ".join(user_tags) or "-",
            )
        _console.print(table)
        return

    selected_types = (resource_type,) if resource_type else tuple(ResourceType)
    registry_resources: list[tuple[ResourceType, ResourceCandidate]] = []
    for selected_type in selected_types:
        result = search_resources(selected_type, selected_name)
        if not result.available:
            raise FlywheelError(f"无法搜索资源：{result.reason}")
        registry_resources.extend(
            (selected_type, candidate)
            for candidate in result.values
            if isinstance(candidate, ResourceCandidate)
        )
    if output == "json":
        emit(
            [
                {
                    "storage_repo": candidate.storage_repo,
                    "resource_id": candidate.resource_id,
                    "name": selected_name,
                    "resource_type": selected_type.value,
                    "description": candidate.description,
                    "created_by": candidate.created_by,
                    "created_at": candidate.created_at,
                }
                for selected_type, candidate in registry_resources
            ],
            output,
        )
        return
    if not registry_resources:
        typer.echo(f"没有找到名为 {selected_name} 的资源。")
        return
    table = Table(title=f"{selected_name} 的资源")
    table.add_column("类型", style="bold cyan")
    table.add_column("Resource ID", style="bold", overflow="fold")
    table.add_column("描述", overflow="fold")
    table.add_column("Storage Repo", overflow="fold")
    for selected_type, candidate in registry_resources:
        table.add_row(
            selected_type.value.capitalize(),
            candidate.resource_id,
            candidate.description or "未设置描述",
            candidate.storage_repo,
        )
    _console.print(table)
    _console.print(
        "[dim]当前显示基础资源信息；完整版本血缘和 Tag 指向将在索引可用后显示。[/dim]"
    )
