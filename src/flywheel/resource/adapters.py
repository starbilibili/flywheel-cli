"""Typed adapters that hide resource-specific files and runtime bindings."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from flywheel.errors import ResourceError
from flywheel.resource.refs import ResolvedResource
from flywheel.runtime.environment import ModelBinding, load_dotenv


@dataclass(frozen=True)
class DatasetBinding:
    """Normalized question-answer dataset files."""

    files: tuple[Path, ...]


@dataclass(frozen=True)
class ScriptBinding:
    """Standard launcher and protocol bound from one Script resource."""

    command: tuple[str, ...]
    protocol: str


def _relative_path(resource: ResolvedResource, declared: str, field: str) -> Path:
    path = Path(declared)
    if path.is_absolute():
        raise ResourceError(f"{resource.resource_type} {field} must be relative: {declared}")
    resolved = (resource.path / path).resolve()
    try:
        resolved.relative_to(resource.path)
    except ValueError as error:
        raise ResourceError(
            f"{resource.resource_type} {field} escapes its resource: {declared}"
        ) from error
    if not resolved.is_file():
        raise ResourceError(f"{resource.resource_type} {field} does not exist: {resolved}")
    return resolved


def _spec_string(resource: ResolvedResource, field: str) -> str:
    value = resource.spec.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ResourceError(f"{resource.resource_type} resource requires non-empty spec.{field}")
    return value.strip()


def bind_dataset(resource: ResolvedResource) -> DatasetBinding:
    """Bind a JSONL question-answer Dataset resource."""

    if resource.adapter != "jsonl-question-answer/v1":
        raise ResourceError(f"Unsupported Dataset adapter: {resource.adapter}")
    declared_files = resource.spec.get("files")
    if not isinstance(declared_files, list) or not declared_files:
        raise ResourceError("Dataset resource requires a non-empty spec.files list")
    if not all(isinstance(item, str) and item.strip() for item in declared_files):
        raise ResourceError("Dataset spec.files entries must be non-empty strings")
    return DatasetBinding(
        files=tuple(_relative_path(resource, item, "file") for item in declared_files)
    )


def bind_config(resource: ResolvedResource) -> dict[str, Any]:
    """Load one JSON Evaluation Config resource."""

    if resource.adapter != "json/v1":
        raise ResourceError(f"Unsupported Config adapter: {resource.adapter}")
    path = _relative_path(resource, _spec_string(resource, "file"), "file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ResourceError(f"Invalid Config resource JSON {path}: {error}") from error
    if not isinstance(value, dict):
        raise ResourceError(f"Config resource must contain one JSON object: {path}")
    return value


def bind_script(resource: ResolvedResource) -> ScriptBinding:
    """Bind one Python Script resource implementing the QA protocol."""

    if resource.adapter != "python-qa-script/v1":
        raise ResourceError(f"Unsupported Script adapter: {resource.adapter}")
    protocol = _spec_string(resource, "protocol")
    if protocol != "fw-qa-script/v1":
        raise ResourceError(f"Unsupported Script protocol: {protocol}")
    command = _relative_path(resource, "run.sh", "run.sh")
    return ScriptBinding(command=(str(command),), protocol=protocol)


def bind_model(resource: ResolvedResource, project_root: Path) -> ModelBinding:
    """Bind one OpenAI-compatible API Model resource without loading secrets into files."""

    if resource.adapter != "openai-compatible-api/v1":
        raise ResourceError(f"Unsupported Model adapter: {resource.adapter}")
    load_dotenv(project_root / ".env")
    endpoint_env = _spec_string(resource, "endpoint_env")
    model_env = _spec_string(resource, "model_env")
    credential_env = _spec_string(resource, "credential_env")
    endpoint = os.environ.get(endpoint_env, "").strip()
    model = os.environ.get(model_env, "").strip()
    credential = os.environ.get(credential_env, "").strip()
    missing = [
        name
        for name, value in (
            (endpoint_env, endpoint),
            (model_env, model),
            (credential_env, credential),
        )
        if not value
    ]
    if missing:
        raise ResourceError(f"Missing Model runtime variables: {', '.join(missing)}")
    return ModelBinding(
        name=resource.name,
        endpoint=endpoint,
        model=model,
        credential_env=credential_env,
    )
