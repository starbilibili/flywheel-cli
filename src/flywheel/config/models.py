"""Typed configuration contract for one evaluation submission."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResourceReferences:
    """The four complete resources selected by one Task Spec."""

    dataset: str
    model: str
    config: str
    script: str


@dataclass(frozen=True)
class SelectionRequest:
    """A deterministic request for selecting dataset records."""

    strategy: str
    count: int
    seed: int
    replacement: bool


@dataclass(frozen=True)
class EvaluationRequest:
    """Complete user input required to plan one local evaluation run."""

    schema_version: str
    task_type: str
    task_ref: str | None
    output_dir: str
    resources: ResourceReferences | None
    selection: SelectionRequest
