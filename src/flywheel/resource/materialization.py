"""Materialize immutable Wenyon Snapshots into the standard user cache."""

from __future__ import annotations

from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any

from flywheel.errors import ResourceError
from flywheel.resource.registry import (
    SnapshotLocation,
    find_snapshot_locations,
    pull_snapshot,
)


@dataclass(frozen=True)
class MaterializedSnapshot:
    """One verified immutable Snapshot available as local files."""

    reference: str
    digest: str
    storage_repo: str
    path: Path
    manifest: dict[str, Any]


def _cache_root() -> Path:
    configured = os.environ.get("XDG_CACHE_HOME", "").strip()
    base = Path(configured).expanduser() if configured else Path.home() / ".cache"
    return base / "flywheel" / "snapshots"


def _read_object(path: Path, description: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ResourceError(f"无法读取{description}：{path}") from error
    if not isinstance(value, dict):
        raise ResourceError(f"{description}必须是 JSON 对象：{path}")
    return value


def _cached_snapshot(target: Path, digest: str) -> MaterializedSnapshot | None:
    metadata_path = target / "metadata.json"
    content = target / "content"
    manifest_path = content / "manifest.json"
    if not metadata_path.is_file() or not manifest_path.is_file():
        return None
    metadata = _read_object(metadata_path, "Snapshot 缓存元数据")
    if metadata.get("digest") != digest:
        return None
    repo = metadata.get("storage_repo")
    if not isinstance(repo, str) or not repo:
        return None
    return MaterializedSnapshot(
        reference=f"{repo}@{digest}",
        digest=digest,
        storage_repo=repo,
        path=content,
        manifest=_read_object(manifest_path, "Wenyon Manifest"),
    )


def _location(digest: str) -> SnapshotLocation:
    result = find_snapshot_locations(digest)
    if not result.available:
        raise ResourceError(f"无法解析 Bundle ID：{result.reason}")
    locations = tuple(
        value for value in result.values if isinstance(value, SnapshotLocation)
    )
    if not locations:
        raise ResourceError(f"没有找到 Bundle ID：{digest}")
    if len(locations) > 1:
        repos = "、".join(location.storage_repo for location in locations)
        raise ResourceError(f"Bundle ID {digest} 对应多个 Storage Repo：{repos}")
    return locations[0]


def _discard(path: Path) -> None:
    try:
        shutil.rmtree(path)
    except OSError:
        # BEST-EFFORT: failed materialization remains isolated in its staging dir.
        return


def _publish_cache(
    temporary: Path, target: Path, location: SnapshotLocation
) -> MaterializedSnapshot:
    content = temporary / "content"
    manifest_path = content / "manifest.json"
    manifest = _read_object(manifest_path, "Wenyon Manifest")
    launcher = content / "run.sh"
    if launcher.is_file():
        launcher.chmod(0o755)
    (temporary / "metadata.json").write_text(
        json.dumps(
            {
                "digest": location.digest,
                "storage_repo": location.storage_repo,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, target)
    return MaterializedSnapshot(
        reference=f"{location.storage_repo}@{location.digest}",
        digest=location.digest,
        storage_repo=location.storage_repo,
        path=target / "content",
        manifest=manifest,
    )


def materialize_snapshot(digest: str) -> MaterializedSnapshot:
    """Resolve, download, verify, and cache one Manifest Digest."""

    normalized = digest.strip().lower()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", normalized):
        raise ResourceError("Bundle ID 必须是完整的 sha256 Manifest Digest")
    cache_root = _cache_root()
    target = cache_root / normalized.removeprefix("sha256:")
    cache_root.mkdir(parents=True, exist_ok=True)
    lock_path = cache_root / f".{target.name}.lock"
    with lock_path.open("a+", encoding="utf-8") as lock_stream:
        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
        try:
            cached = _cached_snapshot(target, normalized)
        except ResourceError:
            cached = None
        if cached is not None:
            return cached
        if target.exists():
            _discard(target)
        location = _location(normalized)
        temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=cache_root))
        try:
            pull_snapshot(location.storage_repo, normalized, temporary / "content")
            return _publish_cache(temporary, target, location)
        except BaseException:
            _discard(temporary)
            raise
