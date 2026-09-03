"""Commands for finding immutable Snapshots of one Flywheel Resource."""

from __future__ import annotations

from dataclasses import dataclass

import typer
from rich.console import Console
from rich.table import Table

from flywheel.errors import FlywheelError
from flywheel.presentation import emit
from flywheel.resource.registry import (
    ResourceLocation,
    SnapshotCandidate,
    find_resource_locations,
    list_snapshots,
)


app = typer.Typer(help="查询 Flywheel 资源的不可变 Snapshot。")
_console = Console()


@dataclass(frozen=True)
class SnapshotResult:
    """One immutable manifest digest and all current tags pointing to it."""

    manifest_digest: str
    tags: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        """Return the stable machine-readable representation."""

        return {
            "manifest_digest": self.manifest_digest,
            "tags": list(self.tags),
        }


def _resource_location(resource_id: str) -> ResourceLocation:
    result = find_resource_locations(resource_id)
    if not result.available:
        raise FlywheelError(f"无法查找资源：{result.reason}")
    locations = tuple(
        value for value in result.values if isinstance(value, ResourceLocation)
    )
    if not locations:
        raise FlywheelError(f"没有找到 Resource ID 为 {resource_id} 的资源")
    if len(locations) > 1:
        repos = "、".join(location.storage_repo for location in locations)
        raise FlywheelError(f"Resource ID {resource_id} 对应多个资源：{repos}")
    return locations[0]


def _snapshot_results(storage_repo: str) -> tuple[SnapshotResult, ...]:
    lookup = list_snapshots(storage_repo)
    if not lookup.available:
        raise FlywheelError(f"无法查询 Snapshot：{lookup.reason}")
    tags_by_digest: dict[str, set[str]] = {}
    for value in lookup.values:
        if not isinstance(value, SnapshotCandidate):
            continue
        tags = tags_by_digest.setdefault(value.digest, set())
        if value.tag:
            tags.add(value.tag)
    return tuple(
        SnapshotResult(
            manifest_digest=digest,
            tags=tuple(sorted(tags, key=lambda tag: (tag != "latest", tag))),
        )
        for digest, tags in sorted(
            tags_by_digest.items(),
            key=lambda item: ("latest" not in item[1], item[0]),
        )
    )


@app.command("search")
def search(
    resource_id: str,
    output: str = typer.Option("text", "--output", "-o"),
) -> None:
    """根据 Resource ID 查询当前可用的 Snapshot。"""

    if output not in {"text", "json"}:
        raise FlywheelError("Output format must be 'text' or 'json'")
    location = _resource_location(resource_id)
    snapshots = _snapshot_results(location.storage_repo)
    payload = {
        "resource_id": resource_id,
        "storage_repo": location.storage_repo,
        "snapshots": [snapshot.as_dict() for snapshot in snapshots],
        "history_complete": False,
    }
    if output == "json":
        emit(payload, output)
        return
    if not snapshots:
        typer.echo(f"资源 {resource_id} 当前没有可见的 Snapshot 指向。")
        return
    table = Table(title=f"{resource_id} 的 Snapshot")
    table.add_column("Manifest Digest", style="bold", overflow="fold")
    table.add_column("标签")
    for snapshot in snapshots:
        table.add_row(
            snapshot.manifest_digest,
            ", ".join(snapshot.tags) or "-",
        )
    _console.print(table)
    _console.print(f"[dim]Storage Repo: {location.storage_repo}[/dim]")
    _console.print(
        "[dim]当前结果来自 Wenyon Tag 指向；未打 Tag 的历史 Snapshot 不在此列表中。[/dim]"
    )
