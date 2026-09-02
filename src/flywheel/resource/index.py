"""Client contract for Flywheel's derived resource index.

The Registry remains the source of truth.  This module only talks to the
single-writer service that maintains the two derived Lance tables.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from flywheel.auth.vouch import audience_token
from flywheel.errors import FlywheelError
from flywheel.resource.registration import ResourceType


_INDEX_URL_ENV = "FLYWHEEL_INDEX_URL"
_INDEX_TOKEN_ENV = "FLYWHEEL_INDEX_TOKEN"
_DEFAULT_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class SnapshotIndexRecord:
    """One immutable row in the Resource Snapshot logical table."""

    manifest_digest: str
    storage_repo: str
    resource_name: str
    resource_type: str
    description: str
    created_by: str
    resource_created_at: str
    snapshot_created_at: str
    parent_manifest_digest: str | None


@dataclass(frozen=True)
class TagIndexRecord:
    """One current pointer in the Resource Tag logical table."""

    storage_repo: str
    tag: str
    tag_kind: str
    manifest_digest: str
    updated_at: str


@dataclass(frozen=True)
class RegistrationIndexRequest:
    """The atomic, idempotent indexing unit after Registry publication."""

    snapshot: SnapshotIndexRecord
    tags: tuple[TagIndexRecord, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "snapshot": asdict(self.snapshot),
            "tags": [asdict(tag) for tag in self.tags],
        }


@dataclass(frozen=True)
class IndexedResource:
    """One Resource summary returned by a search query."""

    storage_repo: str
    resource_name: str
    resource_type: str
    description: str
    created_by: str
    created_at: str
    latest_manifest_digest: str
    snapshot_count: int
    tags: tuple[str, ...]

    @property
    def resource_id(self) -> str:
        """Derive the display ID from the governed Registry repo name."""

        leaf = self.storage_repo.rsplit("/", 1)[-1]
        prefix = f"{self.resource_type}-"
        if not leaf.startswith(prefix) or len(leaf) == len(prefix):
            raise FlywheelError(
                f"资源 Repo 不符合 Flywheel 命名规范：{self.storage_repo}"
            )
        return leaf[len(prefix):]

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["resource_id"] = self.resource_id
        return payload


@dataclass(frozen=True)
class IndexWriteResult:
    """Best-effort result; Registry success never depends on this value."""

    status: str
    reason: str | None = None


def _base_url() -> str | None:
    value = os.environ.get(_INDEX_URL_ENV, "").strip().rstrip("/")
    return value or None


def index_configured() -> bool:
    """Return whether the optional Flywheel resource index is configured."""

    return _base_url() is not None


def _authorization() -> str:
    configured = os.environ.get(_INDEX_TOKEN_ENV, "").strip()
    return configured or audience_token("wenyon-svc")


def _request(
    method: str,
    path: str,
    *,
    payload: dict[str, object] | None = None,
) -> Any:
    base_url = _base_url()
    if base_url is None:
        raise FlywheelError("资源搜索服务尚未配置")
    body = (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        if payload is not None
        else None
    )
    request = Request(
        f"{base_url}{path}",
        data=body,
        method=method,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {_authorization()}",
            **({"Content-Type": "application/json"} if body is not None else {}),
        },
    )
    try:
        with urlopen(request, timeout=_DEFAULT_TIMEOUT_SECONDS) as response:
            raw = response.read()
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace").strip()
        message = f"资源搜索服务返回 HTTP {error.code}"
        if detail:
            message += f"：{detail[:300]}"
        raise FlywheelError(message) from error
    except (URLError, TimeoutError, OSError) as error:
        raise FlywheelError(f"无法连接资源搜索服务：{error}") from error
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise FlywheelError(
            "资源搜索服务返回了无法识别的数据"
        ) from error


def index_registration(value: RegistrationIndexRequest) -> IndexWriteResult:
    """Best-effort write of one Snapshot plus its current tag pointers."""

    if _base_url() is None:
        return IndexWriteResult("pending", "资源搜索服务尚未配置")
    try:
        _request(
            "PUT",
            f"/v1/resource-snapshots/{value.snapshot.manifest_digest}",
            payload=value.as_dict(),
        )
    except FlywheelError as error:
        return IndexWriteResult("pending", str(error))
    return IndexWriteResult("synced")


def _text(record: dict[str, Any], key: str, default: str = "") -> str:
    value = record.get(key, default)
    if not isinstance(value, str):
        raise FlywheelError(f"资源搜索结果字段 {key} 格式无效")
    return value


def _resource(record: Any) -> IndexedResource:
    if not isinstance(record, dict):
        raise FlywheelError("资源搜索结果格式无效")
    tags = record.get("tags", [])
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        raise FlywheelError("资源搜索结果字段 tags 格式无效")
    snapshot_count = record.get("snapshot_count", 0)
    if not isinstance(snapshot_count, int) or snapshot_count < 0:
        raise FlywheelError("资源搜索结果字段 snapshot_count 格式无效")
    return IndexedResource(
        storage_repo=_text(record, "storage_repo"),
        resource_name=_text(record, "resource_name"),
        resource_type=_text(record, "resource_type"),
        description=_text(record, "description"),
        created_by=_text(record, "created_by"),
        created_at=_text(record, "created_at"),
        latest_manifest_digest=_text(record, "latest_manifest_digest"),
        snapshot_count=snapshot_count,
        tags=tuple(tags),
    )


def search_index(
    resource_name: str,
    resource_type: ResourceType | None = None,
) -> tuple[IndexedResource, ...]:
    """Search visible Resources, grouped by Resource rather than Snapshot."""

    query: dict[str, str] = {"resource_name": resource_name}
    if resource_type is not None:
        query["resource_type"] = resource_type.value
    payload = _request("GET", f"/v1/resources?{urlencode(query)}")
    records = payload.get("resources") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        raise FlywheelError("资源搜索服务响应缺少 resources")
    return tuple(_resource(record) for record in records)
