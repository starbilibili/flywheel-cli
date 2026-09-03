"""Read resource lineage from Wenyon without exposing platform details to users."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
from dataclasses import dataclass
import tempfile
from typing import Any

from flywheel.auth.vouch import audience_subject, wenyon_environment
from flywheel.errors import FlywheelError
from flywheel.resource.manifest import put_manifest, read_manifest
from flywheel.resource.registration import ResourceType


@dataclass(frozen=True)
class ResourceCandidate:
    """One visible Resource returned by the registry."""

    resource_id: str
    storage_repo: str
    description: str | None
    created_by: str
    created_at: str


@dataclass(frozen=True)
class ResourceLocation:
    """The exact registry repo that owns one Flywheel Resource ID."""

    resource_id: str
    storage_repo: str


@dataclass(frozen=True)
class SnapshotLocation:
    """The registry repo that owns one exact immutable Manifest Digest."""

    digest: str
    storage_repo: str


@dataclass(frozen=True)
class SnapshotCandidate:
    """One immutable snapshot and its optional human-readable tag."""

    digest: str
    tag: str | None = None
    created_at: str | None = None


@dataclass(frozen=True)
class PublishedSnapshot:
    """One successfully published immutable registry snapshot."""

    repo: str
    digest: str
    tag: str


@dataclass(frozen=True)
class LookupResult:
    """A registry lookup that can explicitly report an unavailable backend."""

    available: bool
    values: tuple[
        ResourceCandidate | ResourceLocation | SnapshotLocation | SnapshotCandidate,
        ...,
    ] = ()
    reason: str | None = None


_PERSONAL_REPO = re.compile(
    r"^users/([a-z0-9](?:[a-z0-9._-]*[a-z0-9])?)/"
)
_CACHE_NAME = "registry-identities.json"


def _command_error(completed: subprocess.CompletedProcess[str]) -> str:
    lines = [
        line.strip()
        for line in completed.stderr.splitlines()
        if line.strip() and not line.startswith("[wenyon-cli]")
    ]
    authentication_markers = ("401", "not authenticated", "unauthorized")
    if any(
        marker in line.lower()
        for line in lines
        for marker in authentication_markers
    ):
        return "Flywheel 登录已失效，请运行 fw auth login"
    errors = [line for line in lines if line.lower().startswith("error:")]
    if errors:
        return errors[-1]
    return lines[-1] if lines else f"exit {completed.returncode}"


def _wenyon_executable() -> str | None:
    executable = shutil.which("wenyon-cli")
    if executable:
        return executable
    sibling = Path(sys.executable).with_name("wenyon-cli")
    if sibling.is_file() and os.access(sibling, os.X_OK):
        return str(sibling)
    return None


def _run_json(*arguments: str) -> tuple[Any | None, str | None]:
    executable = _wenyon_executable()
    if executable is None:
        return None, "当前环境未安装 wenyon-cli"
    try:
        environment = wenyon_environment()
    except FlywheelError as error:
        return None, str(error)
    completed = subprocess.run(
        (executable, *arguments, "-o", "json"),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
        check=False,
    )
    if completed.returncode != 0:
        return None, _command_error(completed)
    try:
        return json.loads(completed.stdout), None
    except json.JSONDecodeError:
        return None, "资源历史查询返回了无法识别的数据"


def _require_json(*arguments: str) -> Any:
    payload, reason = _run_json(*arguments)
    if reason:
        raise FlywheelError(reason)
    return payload


def _run_text(*arguments: str) -> str | None:
    executable = _wenyon_executable()
    if executable is None:
        return "当前环境未安装 wenyon-cli"
    try:
        environment = wenyon_environment()
    except FlywheelError as error:
        return str(error)
    completed = subprocess.run(
        (executable, *arguments),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
        check=False,
    )
    if completed.returncode == 0:
        return None
    return _command_error(completed)


def publish_manifest(
    repo: str, tag: str, manifest: dict[str, Any]
) -> PublishedSnapshot:
    """Publish a complete manifest under one mutable tag through Wenyon HTTP."""

    return _published(put_manifest(repo, tag, manifest), repo, tag)


def publish_composed_snapshot(
    repo: str,
    manifest: dict[str, Any],
    *,
    tag: str | None,
    new_resource: bool,
) -> PublishedSnapshot:
    """Publish a ref-only Resource Snapshot without a staging blob upload."""

    target_created = False
    try:
        if new_resource:
            _require_json("registry", "repo", "create", repo)
            target_created = True
        return _publish_resource_tags(repo, manifest, tag)
    except FlywheelError as error:
        cleanup_error = (
            _run_text("registry", "repo", "delete", repo, "--yes")
            if target_created
            else None
        )
        message = "Task Snapshot 发布失败"
        if cleanup_error:
            message += f"，且新建 Repo 清理失败：{repo}（{cleanup_error}）"
        raise FlywheelError(message) from error


def _identity_cache_path() -> Path:
    return Path.home() / ".config" / "flywheel" / _CACHE_NAME


def _discard_temporary(path: str) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        # BEST-EFFORT: preserve the primary cache-write error for the user.
        return


def _read_identity_cache() -> dict[str, str]:
    path = _identity_cache_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as error:
        raise FlywheelError(f"无法读取 Flywheel Registry 身份缓存：{path}") from error
    if not isinstance(payload, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in payload.items()
    ):
        raise FlywheelError(f"Flywheel Registry 身份缓存格式无效：{path}")
    return payload


def _write_identity_cache(values: dict[str, str]) -> None:
    path = _identity_cache_path()
    temporary_name: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as stream:
            temporary_name = stream.name
            json.dump(values, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, path)
    except OSError as error:
        if temporary_name:
            _discard_temporary(temporary_name)
        raise FlywheelError(f"无法写入 Flywheel Registry 身份缓存：{path}") from error


def _personal_namespace_from_records(payload: Any, probe_name: str) -> str | None:
    suffix = f"/{probe_name}"
    for record in _records(payload, ("items", "results", "resources", "repos")):
        repo = _repo_name(record)
        if not repo or not repo.endswith(suffix):
            continue
        match = _PERSONAL_REPO.match(repo)
        if match:
            return f"users/{match.group(1)}"
    return None


def _probe_personal_namespace() -> str:
    probe_name = f"fw-namespace-probe-{secrets.token_hex(8)}"
    _require_json("registry", "repo", "create", probe_name)
    namespace: str | None = None
    failure: FlywheelError | None = None
    try:
        namespace = _personal_namespace_from_records(
            _require_json("registry", "list"), probe_name
        )
        if namespace is None:
            raise FlywheelError(
                f"文渊未返回临时 Registry Repo 的完整路径：{probe_name}"
            )
    except FlywheelError as error:
        failure = error
    finally:
        cleanup_target = f"{namespace}/{probe_name}" if namespace else probe_name
        cleanup_error = _run_text(
            "registry", "repo", "delete", cleanup_target, "--yes"
        )
    if cleanup_error:
        raise FlywheelError(
            f"无法清理 namespace 探针 Repo {cleanup_target}：{cleanup_error}"
        ) from failure
    if failure:
        raise failure
    if namespace is None:
        raise FlywheelError("文渊 Registry namespace 解析失败")
    return namespace


def personal_namespace() -> str:
    """Resolve and cache the current user's Wenyon Registry namespace."""

    subject = audience_subject("wenyon-svc")
    cache = _read_identity_cache()
    cached = cache.get(subject)
    if cached and _PERSONAL_REPO.match(f"{cached}/placeholder"):
        return cached
    namespace = _probe_personal_namespace()
    cache[subject] = namespace
    _write_identity_cache(cache)
    return namespace


def resolve_registry_repo(repo: str) -> str:
    """Expand a Flywheel repo suffix into the current personal namespace."""

    if repo.startswith("users/"):
        return repo
    return f"{personal_namespace()}/{repo.strip('/')}"


def _records(payload: Any, keys: tuple[str, ...]) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            return list(value.items())
    data = payload.get("data")
    return _records(data, keys) if data is not None else []


def _repo_name(record: Any) -> str | None:
    if isinstance(record, str):
        return record
    if not isinstance(record, dict):
        return None
    for key in ("repo", "repository", "name", "id"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _record_text(record: Any, *keys: str) -> str | None:
    if not isinstance(record, dict):
        return None
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _resource_annotations(repo: str) -> dict[str, str]:
    manifest = read_manifest(repo)
    annotations = manifest.get("annotations", {})
    if not isinstance(annotations, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in annotations.items()
    ):
        raise FlywheelError(f"资源 Manifest annotations 格式无效：{repo}")
    return annotations


def search_resources(
    resource_type: ResourceType, resource_name: str
) -> LookupResult:
    """Find visible resources matching one type and resource name."""

    payload, reason = _run_json("registry", "search", resource_name)
    if reason:
        return LookupResult(False, reason=reason)
    markers = (
        f"/{resource_type.value}/{resource_name}-",
        # Compatibility with resources registered before the resource-name
        # path migration, where the name occupied the first path segment.
        f"/{resource_name}/{resource_type.value}-",
    )
    candidates: list[ResourceCandidate] = []
    for record in _records(payload, ("items", "results", "resources", "repos")):
        repo = _repo_name(record)
        normalized = f"/{repo.strip('/')}" if repo else ""
        if any(marker in normalized for marker in markers):
            leaf = normalized.rsplit("/", 1)[-1]
            prefix = f"{resource_type.value}-"
            resource_id = leaf[len(prefix):] if leaf.startswith(prefix) else ""
            if resource_id:
                try:
                    annotations = _resource_annotations(repo)
                except FlywheelError as error:
                    return LookupResult(False, reason=str(error))
                created_at = annotations.get("flywheel.created_at") or _record_text(
                    record, "created_at", "createdAt"
                )
                created_by = annotations.get("flywheel.created_by") or _record_text(
                    record, "owner_id", "ownerId"
                )
                description = annotations.get("description")
                candidates.append(
                    ResourceCandidate(
                        resource_id,
                        repo,
                        description,
                        created_by or "未知用户",
                        created_at or "未知时间",
                    )
                )
    unique = {candidate.storage_repo: candidate for candidate in candidates}
    return LookupResult(True, tuple(unique.values()))


def find_resource_locations(resource_id: str) -> LookupResult:
    """Resolve an exact Flywheel Resource ID to its visible registry repo.

    Wenyon search is substring-based, so this function applies an exact leaf-name
    check before returning a result. A Resource ID is expected to be globally
    unique, while multiple matches remain visible to the caller as an error case.
    """

    selected_id = resource_id.strip()
    if not selected_id.startswith("res_"):
        raise FlywheelError("Resource ID 必须以 res_ 开头")
    payload, reason = _run_json("registry", "search", selected_id)
    if reason:
        return LookupResult(False, reason=reason)
    locations: dict[str, ResourceLocation] = {}
    for record in _records(payload, ("items", "results", "resources", "repos")):
        repo = _repo_name(record)
        if not repo:
            continue
        parts = repo.strip("/").split("/")
        leaf = parts[-1]
        # Current layout: <type>/<resource-name>-<resource-id>.
        current_layout = (
            len(parts) >= 2
            and parts[-2] in {value.value for value in ResourceType}
            and leaf.endswith(f"-{selected_id}")
        )
        # Legacy layout: <type>-<resource-id> (kept for existing repos).
        legacy_layout = leaf in {
            f"{value.value}-{selected_id}" for value in ResourceType
        }
        if not (current_layout or legacy_layout):
            continue
        locations[repo] = ResourceLocation(selected_id, repo)
    return LookupResult(True, tuple(locations.values()))


def find_snapshot_locations(digest: str) -> LookupResult:
    """Resolve one exact Manifest Digest to its visible registry repo."""

    selected_digest = digest.strip().lower()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", selected_digest):
        raise FlywheelError("Bundle ID 必须是完整的 sha256 Manifest Digest")
    payload, reason = _run_json("registry", "search", selected_digest)
    if reason:
        return LookupResult(False, reason=reason)
    locations: dict[str, SnapshotLocation] = {}
    for record in _records(
        payload,
        # Wenyon returns several sibling result groups. A digest lookup is
        # represented by ``manifests`` while ``repos`` may legitimately be an
        # empty list. Since _records selects the first present list, inspect
        # the digest-specific group before the generic repository group.
        ("manifests", "matches", "items", "results", "resources", "repos"),
    ):
        repo = _repo_name(record)
        record_digest = _record_text(record, "digest", "manifest_digest")
        if not repo or (record_digest and record_digest.lower() != selected_digest):
            continue
        locations[repo] = SnapshotLocation(selected_digest, repo)
    return LookupResult(True, tuple(locations.values()))


def _snapshot(record: Any) -> SnapshotCandidate | None:
    if isinstance(record, tuple) and len(record) == 2:
        tag, digest = record
        if isinstance(tag, str) and isinstance(digest, str):
            return SnapshotCandidate(digest=digest, tag=tag)
    if not isinstance(record, dict):
        return None
    digest = record.get("digest") or record.get("manifest_digest")
    tag = record.get("tag") or record.get("name")
    created_at = (
        record.get("created_at")
        or record.get("createdAt")
        or record.get("updated_at")
        or record.get("updatedAt")
    )
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        return None
    return SnapshotCandidate(
        digest,
        tag if isinstance(tag, str) else None,
        created_at if isinstance(created_at, str) else None,
    )


def list_snapshots(storage_repo: str) -> LookupResult:
    """List immutable snapshots for one visible resource version line."""

    payload, reason = _run_json("registry", "tags", storage_repo)
    if reason:
        return LookupResult(False, reason=reason)
    values = tuple(
        candidate
        for record in _records(payload, ("items", "results", "tags", "snapshots"))
        if (candidate := _snapshot(record)) is not None
    )
    return LookupResult(True, values)


def pull_snapshot(storage_repo: str, digest: str, output_dir: Path) -> None:
    """Pull and verify one exact Snapshot into an otherwise empty directory."""

    executable = _wenyon_executable()
    if executable is None:
        raise FlywheelError("当前环境未安装 wenyon-cli")
    try:
        environment = wenyon_environment()
    except FlywheelError:
        raise
    completed = subprocess.run(
        (
            executable,
            "registry",
            "pull",
            f"{storage_repo}@{digest}",
            "--output-dir",
            str(output_dir),
            "--output",
            "json",
        ),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
        check=False,
    )
    if completed.returncode != 0:
        raise FlywheelError(f"无法下载 Resource Snapshot：{_command_error(completed)}")


def _published(payload: Any, requested_repo: str, tag: str) -> PublishedSnapshot:
    record = payload.get("data", payload) if isinstance(payload, dict) else payload
    if not isinstance(record, dict):
        raise FlywheelError("资源上传返回了无法识别的数据")
    repo = record.get("repo") or record.get("repository") or requested_repo
    digest = record.get("digest") or record.get("manifest_digest")
    returned_tag = record.get("tag") or tag
    if not isinstance(repo, str) or not repo:
        raise FlywheelError("资源上传结果缺少 repo")
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        raise FlywheelError("资源上传结果缺少 Manifest Digest")
    if not isinstance(returned_tag, str) or not returned_tag:
        raise FlywheelError("资源上传结果缺少 tag")
    return PublishedSnapshot(repo, digest, returned_tag)


def push_bundle(
    path: str,
    repo: str,
    *,
    tag: str,
    parent_snapshot: str | None,
) -> PublishedSnapshot:
    """Publish one local resource and its optional parent ref through Wenyon CLI."""

    arguments = ["registry", "push", path, repo, "--tag", tag]
    if parent_snapshot:
        parent_ref = f"{repo}@{parent_snapshot}"
        arguments.extend(("--ref", f"flywheel_parent=registry:{parent_ref}"))
    return _published(_require_json(*arguments), repo, tag)


def _complete_resource_manifest(
    base: PublishedSnapshot,
    repo: str,
    parent_snapshot: str | None,
    annotations: dict[str, str],
) -> dict[str, Any]:
    manifest = read_manifest(base.repo, base.digest)
    current = manifest.get("annotations", {})
    refs = manifest.get("refs", [])
    if not isinstance(current, dict):
        raise FlywheelError("文渊 Manifest annotations 格式无效")
    if not isinstance(refs, list):
        raise FlywheelError("文渊 Manifest refs 格式无效")
    manifest["annotations"] = {**current, **annotations}
    if parent_snapshot:
        refs.append(
            {
                "name": "flywheel_parent",
                "kind": "registry",
                "ref": f"{repo}@{parent_snapshot}",
            }
        )
    manifest["refs"] = refs
    return manifest


def _publish_resource_tags(
    repo: str, manifest: dict[str, Any], tag: str | None
) -> PublishedSnapshot:
    latest = publish_manifest(repo, "latest", manifest)
    if tag:
        labelled = publish_manifest(repo, tag, manifest)
        if labelled.digest != latest.digest:
            raise FlywheelError("同一资源的 latest 与用户标签指向了不同 Digest")
    return latest


def publish_resource_snapshot(
    path: str,
    repo: str,
    *,
    parent_snapshot: str | None,
    tag: str | None,
    annotations: dict[str, str],
    new_resource: bool,
) -> PublishedSnapshot:
    """Upload resource blobs and publish one annotated immutable snapshot.

    Wenyon CLI uploads blobs into a temporary repo. Flywheel then publishes only
    the annotated manifest into the permanent Resource repo, so one registration
    creates exactly one Resource Snapshot there.
    """

    staging_repo = f"fw-staging-{secrets.token_hex(8)}"
    base = push_bundle(
        path,
        staging_repo,
        tag="staging",
        parent_snapshot=None,
    )
    target_created = False
    result: PublishedSnapshot | None = None
    failure: FlywheelError | None = None
    try:
        manifest = _complete_resource_manifest(
            base, repo, parent_snapshot, annotations
        )
        if new_resource:
            _require_json("registry", "repo", "create", repo)
            target_created = True
        result = _publish_resource_tags(repo, manifest, tag)
    except FlywheelError as error:
        failure = error
    staging_cleanup = _run_text(
        "registry", "repo", "delete", base.repo, "--yes"
    )
    if failure:
        target_cleanup = (
            _run_text("registry", "repo", "delete", repo, "--yes")
            if target_created
            else None
        )
        cleanup_detail = staging_cleanup or target_cleanup
        message = "Resource Snapshot 发布失败"
        if cleanup_detail:
            message += f"，且临时数据清理失败：{cleanup_detail}"
        raise FlywheelError(message) from failure
    if staging_cleanup:
        raise FlywheelError(
            f"Resource Snapshot 已发布，但临时 Repo 清理失败：{base.repo}"
        )
    if result is None:
        raise FlywheelError("Resource Snapshot 发布未返回结果")
    return result
