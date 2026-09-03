"""Resolve self-describing resources without exposing their internal layout."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from flywheel.errors import ResourceError
from flywheel.resource.materialization import materialize_snapshot


@dataclass(frozen=True)
class ResolvedResource:
    """One immutable resource and its typed internal manifest."""

    reference: str
    path: Path
    resource_type: str
    name: str
    version: str
    adapter: str
    spec: dict[str, Any]
    digest: str


def _tree_digest(path: Path) -> str:
    digest = hashlib.sha256()
    files = [path] if path.is_file() else sorted(item for item in path.rglob("*") if item.is_file())
    for file_path in files:
        relative = file_path.name if path.is_file() else file_path.relative_to(path).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        with file_path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _manifest(root: Path) -> dict[str, Any]:
    manifest_path = root / "resource.yaml"
    if not manifest_path.is_file():
        raise ResourceError(f"Resource manifest does not exist: {manifest_path}")
    try:
        value = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ResourceError(f"Invalid resource manifest {manifest_path}: {error}") from error
    if not isinstance(value, dict):
        raise ResourceError(f"Resource manifest must be an object: {manifest_path}")
    return value


def _manifest_string(manifest: dict[str, Any], field: str, path: Path) -> str:
    value = manifest.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ResourceError(f"Resource manifest {path} requires non-empty {field}")
    return value.strip()


def _remote_files(manifest: dict[str, Any], root: Path) -> tuple[str, ...]:
    records = manifest.get("files")
    if not isinstance(records, list):
        raise ResourceError("Wenyon Manifest files 必须是列表")
    paths: list[str] = []
    for record in records:
        value = record.get("path") if isinstance(record, dict) else None
        if not isinstance(value, str) or not value.strip():
            raise ResourceError("Wenyon Manifest 文件条目缺少 path")
        relative = Path(value)
        if relative.is_absolute() or ".." in relative.parts:
            raise ResourceError(f"Wenyon Manifest 包含不安全路径：{value}")
        if not (root / relative).is_file():
            raise ResourceError(f"Snapshot 文件不存在：{value}")
        paths.append(relative.as_posix())
    return tuple(paths)


def _remote_contract(
    resource_type: str, files: tuple[str, ...]
) -> tuple[str, dict[str, Any]]:
    if resource_type == "dataset":
        data_files = [
            path for path in files
            if Path(path).suffix.lower() in {".jsonl", ".json", ".csv", ".tsv"}
        ]
        if not data_files:
            raise ResourceError("Dataset Snapshot 不包含可采样的数据文件（JSONL、JSON、CSV 或 TSV）")
        return "jsonl/v1", {"files": data_files}
    if resource_type == "config":
        config_files = [path for path in files if path.lower().endswith(".json")]
        if len(config_files) != 1:
            raise ResourceError("Config Snapshot 必须包含且仅包含一个 JSON 文件")
        return "json/v1", {"file": config_files[0]}
    if resource_type == "model":
        return "openai-compatible-api/v1", {
            "endpoint_env": "LLM_BASE_URL",
            "model_env": "LLM_PRO",
            "credential_env": "LLM_API_KEY",
        }
    if resource_type == "script":
        if "run.sh" not in files:
            raise ResourceError("Script Snapshot 根目录缺少 run.sh")
        return "python-qa-script/v1", {"protocol": "fw-qa-script/v1"}
    if resource_type == "task":
        return "task/v1", {"refs": "registry"}
    raise ResourceError(f"不支持的资源类型：{resource_type}")


def _resolve_remote(reference: str, expected_type: str | None) -> ResolvedResource:
    snapshot = materialize_snapshot(reference)
    annotations = snapshot.manifest.get("annotations")
    if not isinstance(annotations, dict):
        raise ResourceError("Wenyon Manifest annotations 必须是对象")
    resource_type = annotations.get("flywheel.resource_type")
    if not isinstance(resource_type, str) or not resource_type:
        raise ResourceError("Wenyon Manifest 缺少 flywheel.resource_type")
    if expected_type is not None and resource_type != expected_type:
        raise ResourceError(
            f"Expected {expected_type} resource, got {resource_type}: {reference}"
        )
    files = _remote_files(snapshot.manifest, snapshot.path)
    if resource_type == "task":
        task_file = snapshot.path / "task.yaml"
        try:
            task_data = yaml.safe_load(task_file.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as error:
            raise ResourceError(f"Task Snapshot 缺少有效 task.yaml：{reference}") from error
        if not isinstance(task_data, dict) or not isinstance(task_data.get("resources"), dict):
            raise ResourceError(f"Task Snapshot 的 resources 字段无效：{reference}")
        spec = {"resources": task_data["resources"], "task_type": task_data.get("task_type", "eval")}
    else:
        spec = None
    adapter, contract_spec = _remote_contract(resource_type, files)
    if resource_type == "model":
        for key in ("endpoint_env", "model_env", "credential_env"):
            annotation_key = f"flywheel.{key}"
            value = annotations.get(annotation_key)
            if isinstance(value, str) and value.strip():
                contract_spec[key] = value.strip()
    if spec is None:
        spec = contract_spec
    name = annotations.get("flywheel.resource_id")
    if not isinstance(name, str) or not name:
        name = snapshot.storage_repo.rsplit("/", 1)[-1]
    return ResolvedResource(
        reference=reference,
        path=snapshot.path,
        resource_type=resource_type,
        name=name,
        version=snapshot.digest,
        adapter=adapter,
        spec=spec,
        digest=snapshot.digest,
    )


def resolve_resource(
    reference: str, expected_type: str | None, base_dir: Path
) -> ResolvedResource:
    """Resolve and type-check one resource reference."""

    if reference.startswith("local://"):
        raise ResourceError(
            "Implicit local:// name lookup is not supported; use local:<explicit-path>"
        )
    if not reference.startswith("local:"):
        return _resolve_remote(reference, expected_type)
    declared_path = reference.removeprefix("local:")
    if not declared_path:
        raise ResourceError("Local resource reference must include a path")
    path = Path(declared_path)
    if not path.is_absolute():
        path = base_dir / path

    resolved = path.resolve()
    if not resolved.is_dir():
        raise ResourceError(f"Local resource does not exist: {resolved}")
    manifest = _manifest(resolved)
    manifest_path = resolved / "resource.yaml"
    if manifest.get("schema_version") != "fw-resource/v1":
        raise ResourceError(f"Unsupported resource manifest schema: {manifest_path}")
    resource_type = _manifest_string(manifest, "type", manifest_path)
    if expected_type is not None and resource_type != expected_type:
        raise ResourceError(
            f"Expected {expected_type} resource, got {resource_type}: {reference}"
        )
    spec = manifest.get("spec")
    if not isinstance(spec, dict):
        raise ResourceError(f"Resource manifest {manifest_path} requires object spec")
    return ResolvedResource(
        reference=reference,
        path=resolved,
        resource_type=resource_type,
        name=_manifest_string(manifest, "name", manifest_path),
        version=_manifest_string(manifest, "version", manifest_path),
        adapter=_manifest_string(manifest, "adapter", manifest_path),
        spec=spec,
        digest=_tree_digest(resolved),
    )
