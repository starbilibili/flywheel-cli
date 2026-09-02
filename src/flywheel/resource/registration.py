"""Prepare one local path for resource registration."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
import re
import secrets
import time

from flywheel.errors import ResourceError


class ResourceType(str, Enum):
    """The resource types exposed by Flywheel."""

    DATASET = "dataset"
    MODEL = "model"
    CONFIG = "config"
    SCRIPT = "script"
    TASK = "task"


@dataclass(frozen=True)
class RegistrationPlan:
    """A confirmed local resource boundary awaiting registry publication."""

    path: str
    resource_type: str
    resource_name: str
    resource_id: str
    registry_repo: str
    storage_repo: str
    file_count: int
    size_bytes: int
    description: str
    created_by: str
    created_at: str
    parent_snapshot: str | None = None
    snapshot_ref: str | None = None
    tag: str | None = None
    index_status: str = "pending"
    history_status: str = "available"
    status: str = "prepared"

    def as_dict(self) -> dict[str, object]:
        """Return a stable machine-readable registration preview."""

        payload = asdict(self)
        payload["name"] = payload.pop("resource_name")
        return payload


_IGNORED_PARTS = {".git", ".venv", "__pycache__", "node_modules"}
_BLOCKED_NAMES = {".env", "credentials.json"}
_BLOCKED_SUFFIXES = {".key", ".p12", ".pem"}
_RESOURCE_NAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_CROCKFORD = "0123456789abcdefghjkmnpqrstvwxyz"


def validate_resource_name(value: str) -> str:
    """Validate the user-facing name segment used in registry paths."""

    resource_name = value.strip()
    if not _RESOURCE_NAME_PATTERN.fullmatch(resource_name):
        raise ResourceError(
            "资源名称只能包含 1-64 个小写字母、数字或连字符"
        )
    return resource_name


# Compatibility alias for callers that still validate task metadata.
validate_collection = validate_resource_name


def new_resource_id() -> str:
    """Generate a sortable, globally unique Flywheel resource identifier."""

    value = (int(time.time() * 1000) << 80) | int.from_bytes(
        secrets.token_bytes(10), "big"
    )
    encoded = ""
    for _ in range(26):
        encoded = _CROCKFORD[value & 31] + encoded
        value >>= 5
    return f"res_{encoded}"


def planned_storage_repo(
    resource_type: ResourceType, resource_name: str, resource_id: str
) -> str:
    """Return the user-visible repo path before Wenyon resolves the real uid."""

    return f"users/<uid>/{resource_type.value}/{resource_name}-{resource_id}"


def registry_repo_path(
    resource_type: ResourceType, resource_name: str, resource_id: str
) -> str:
    """Return the repo suffix below the user's personal namespace."""

    return f"{resource_type.value}/{resource_name}-{resource_id}"


def _credential_like(path: Path) -> bool:
    return (
        path.name in _BLOCKED_NAMES
        or path.name.startswith(".env.")
        or path.suffix.lower() in _BLOCKED_SUFFIXES
    )


def _payload_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(
        item
        for item in path.rglob("*")
        if item.is_file() and not any(part in _IGNORED_PARTS for part in item.parts)
    )


def prepare_registration(
    path: Path,
    resource_type: ResourceType,
    resource_name: str,
    *,
    resource_id: str | None = None,
    storage_repo: str | None = None,
    parent_snapshot: str | None = None,
    tag: str | None = None,
    history_status: str = "available",
    description: str,
    created_by: str,
    created_at: str,
) -> RegistrationPlan:
    """Validate one explicit resource boundary without inferring its type."""

    expanded = path.expanduser()
    if expanded.is_symlink():
        raise ResourceError(f"Resource path must not be a symbolic link: {expanded}")
    resolved = expanded.resolve()
    if not resolved.exists():
        raise ResourceError(f"Resource path does not exist: {resolved}")
    if not resolved.is_file() and not resolved.is_dir():
        raise ResourceError(f"Resource path must be a file or directory: {resolved}")

    files = _payload_files(resolved)
    if not files:
        raise ResourceError(f"Resource path contains no files: {resolved}")
    links = [item for item in files if item.is_symlink()]
    if links:
        names = ", ".join(item.relative_to(resolved).as_posix() for item in links)
        raise ResourceError(f"Resource contains symbolic links: {names}")
    blocked = [item for item in files if _credential_like(item)]
    if blocked:
        names = ", ".join(
            item.name if resolved.is_file() else item.relative_to(resolved).as_posix()
            for item in blocked
        )
        raise ResourceError(f"Resource contains credential-like files: {names}")

    validated_resource_name = validate_resource_name(resource_name)
    selected_id = resource_id or new_resource_id()
    selected_registry_repo = (
        storage_repo
        if storage_repo and not storage_repo.startswith("users/<uid>/")
        else registry_repo_path(resource_type, validated_resource_name, selected_id)
    )
    selected_repo = storage_repo or planned_storage_repo(
        resource_type, validated_resource_name, selected_id
    )
    return RegistrationPlan(
        path=str(resolved),
        resource_type=resource_type.value,
        resource_name=validated_resource_name,
        resource_id=selected_id,
        registry_repo=selected_registry_repo,
        storage_repo=selected_repo,
        file_count=len(files),
        size_bytes=sum(item.stat().st_size for item in files),
        description=description,
        created_by=created_by,
        created_at=created_at,
        parent_snapshot=parent_snapshot,
        tag=tag,
        history_status=history_status,
    )
