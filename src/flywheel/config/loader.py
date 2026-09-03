"""Parse and validate the fw evaluation YAML contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from flywheel.config.models import (
    EvaluationRequest,
    ResourceReferences,
    SelectionRequest,
)
from flywheel.errors import ConfigError


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{field} must be a mapping")
    return value


def _string(mapping: dict[str, Any], field: str) -> str:
    value = mapping.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{field} must be a non-empty string")
    return value.strip()


def load_evaluation_request(path: Path) -> EvaluationRequest:
    """Load one YAML file into the stable evaluation request contract."""

    if not path.is_file():
        raise ConfigError(f"Configuration file does not exist: {path}")
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ConfigError(f"Invalid YAML in {path}: {error}") from error

    root = _mapping(document, "config")
    schema_version = _string(root, "schema_version")
    if schema_version not in {"fw-eval/v2", "fw-task/v1"}:
        raise ConfigError(f"Unsupported schema_version: {schema_version!r}")
    task_type = _string(root, "task_type") if schema_version == "fw-task/v1" else "eval"
    if task_type not in {"eval", "train"}:
        raise ConfigError("task_type must be eval or train")

    task_ref = _string(root, "task") if schema_version == "fw-task/v1" else None
    resources = _mapping(root.get("resources"), "resources") if schema_version == "fw-eval/v2" else None
    selection_data = _mapping(root.get("selection"), "selection")
    strategy = _string(selection_data, "strategy")
    if strategy != "random":
        raise ConfigError("selection.strategy currently supports only 'random'")
    count = selection_data.get("count")
    seed = selection_data.get("seed")
    replacement = selection_data.get("replacement", False)
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise ConfigError("selection.count must be a positive integer")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ConfigError("selection.seed must be an integer")
    if not isinstance(replacement, bool):
        raise ConfigError("selection.replacement must be a boolean")

    return EvaluationRequest(
        schema_version=schema_version,
        task_type=task_type,
        task_ref=task_ref,
        output_dir=_string(root, "output_dir"),
        resources=ResourceReferences(
            dataset=_string(resources, "dataset") if resources else "",
            model=_string(resources, "model") if resources else "",
            config=_string(resources, "config") if resources else "",
            script=_string(resources, "script") if resources else "",
        ),
        selection=SelectionRequest(
            strategy=strategy,
            count=count,
            seed=seed,
            replacement=replacement,
        ),
    )
