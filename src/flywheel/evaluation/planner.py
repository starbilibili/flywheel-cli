"""Resolve a user configuration into one immutable local Run Spec."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from flywheel.config.models import EvaluationRequest
from flywheel.errors import EvaluationError
from flywheel.evaluation.sampling import load_dataset, select_records, write_selected_dataset
from flywheel.evaluation.store import read_json, update_status, write_json
from flywheel.resource import (
    ResolvedResource,
    bind_config,
    bind_dataset,
    bind_model,
    bind_script,
    resolve_resource,
)


@dataclass(frozen=True)
class RunPlan:
    """All resolved paths and immutable inputs needed to execute one run."""

    run_id: str
    run_dir: Path
    script_command: tuple[str, ...]
    effective_run_config: Path
    expected_attempts: int


def _run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"run_{timestamp}_{secrets.token_hex(4)}"


def _resource_record(resource: ResolvedResource) -> dict[str, str]:
    """Return the immutable public identity of one resolved resource."""

    return {
        "ref": resource.reference,
        "type": resource.resource_type,
        "name": resource.name,
        "version": resource.version,
        "adapter": resource.adapter,
        "digest": resource.digest,
    }


def _expected_attempts(settings: dict, selected_count: int) -> int:
    samples = settings.get("samples") or {}
    if not isinstance(samples, dict):
        raise EvaluationError("Evaluation Config samples must be an object")
    seeds = samples.get("seeds", [0])
    if not isinstance(seeds, list) or not seeds or not all(isinstance(seed, int) for seed in seeds):
        raise EvaluationError("Evaluation Config samples.seeds must be a non-empty integer list")
    return selected_count * len(seeds)


def _write_execution_plan(plan: RunPlan) -> None:
    """Persist the resolved, credential-free inputs consumed by a worker."""

    write_json(
        plan.run_dir / "execution-plan.json",
        {
            "schema_version": "fw-execution-plan/v1",
            "run_id": plan.run_id,
            "script_command": list(plan.script_command),
            "effective_run_config": str(plan.effective_run_config),
            "expected_attempts": plan.expected_attempts,
        },
    )


def load_run_plan(run_dir: Path) -> RunPlan:
    """Load the immutable execution inputs for a detached worker."""

    resolved_run_dir = run_dir.resolve()
    value = read_json(resolved_run_dir / "execution-plan.json")
    if value.get("schema_version") != "fw-execution-plan/v1":
        raise EvaluationError("Unsupported execution plan schema")
    script_command = value.get("script_command")
    if not isinstance(script_command, list) or not script_command:
        raise EvaluationError("Execution plan script_command must be a non-empty list")
    if not all(isinstance(item, str) and item for item in script_command):
        raise EvaluationError("Execution plan script_command entries must be strings")
    try:
        return RunPlan(
            run_id=str(value["run_id"]),
            run_dir=resolved_run_dir,
            script_command=tuple(script_command),
            effective_run_config=Path(str(value["effective_run_config"])),
            expected_attempts=int(value["expected_attempts"]),
        )
    except KeyError as error:
        raise EvaluationError(f"Execution plan is missing {error.args[0]}") from error


def create_run_plan(
    request: EvaluationRequest,
    project_root: Path,
    output_root: Path,
) -> RunPlan:
    """Resolve resources, materialize sampling, and persist the exact Run Spec."""

    dataset = resolve_resource(request.resources.dataset, "dataset", project_root)
    model_resource = resolve_resource(request.resources.model, "model", project_root)
    config_resource = resolve_resource(request.resources.config, "config", project_root)
    script_resource = resolve_resource(request.resources.script, "script", project_root)
    dataset_binding = bind_dataset(dataset)
    model = bind_model(model_resource, project_root)
    settings = bind_config(config_resource)
    script = bind_script(script_resource)

    records = load_dataset(dataset_binding.files)
    selected = select_records(records, request.selection)
    run_id = _run_id()
    run_dir = output_root / run_id
    selected_dataset = run_dir / "inputs" / "selected-dataset.jsonl"
    write_selected_dataset(selected_dataset, selected)

    selection = {
        "strategy": request.selection.strategy,
        "count": request.selection.count,
        "seed": request.selection.seed,
        "replacement": request.selection.replacement,
        "sample_ids": [record["sample_id"] for record in selected],
    }
    resources = {
        "dataset": _resource_record(dataset),
        "model": _resource_record(model_resource),
        "config": _resource_record(config_resource),
        "script": _resource_record(script_resource),
    }
    run_spec = {
        "schema_version": "fw-run-spec/v2",
        "run_id": run_id,
        "resources": resources,
        "selection": selection,
    }
    write_json(run_dir / "run-spec.json", run_spec)
    write_json(run_dir / "selection.json", {"dataset_digest": dataset.digest, **selection})
    effective_run_config = run_dir / "effective-run-config.json"
    write_json(
        effective_run_config,
        {
            "schema_version": "fw-effective-run-config/v1",
            "run_id": run_id,
            "resources": resources,
            "dataset": {
                "format": "jsonl-question-answer/v1",
                "path": str(selected_dataset),
            },
            "model": {
                "protocol": "openai-compatible/v1",
                "name": model.name,
                "endpoint": model.endpoint,
                "model": model.model,
                "credential_env": model.credential_env,
            },
            "evaluation_config": settings,
            "output": {
                "directory": str(run_dir),
                "attempts": str(run_dir / "attempts"),
                "summary": str(run_dir / "script-summary.json"),
            },
        },
    )
    plan = RunPlan(
        run_id=run_id,
        run_dir=run_dir,
        script_command=script.command,
        effective_run_config=effective_run_config,
        expected_attempts=_expected_attempts(settings, len(selected)),
    )
    _write_execution_plan(plan)
    update_status(run_dir, run_id, "planned")
    return plan
