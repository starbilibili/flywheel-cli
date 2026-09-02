"""Resource reference resolution."""

from flywheel.resource.adapters import (
    DatasetBinding,
    ScriptBinding,
    bind_config,
    bind_dataset,
    bind_model,
    bind_script,
)
from flywheel.resource.refs import ResolvedResource, resolve_resource

__all__ = [
    "DatasetBinding",
    "ResolvedResource",
    "ScriptBinding",
    "bind_config",
    "bind_dataset",
    "bind_model",
    "bind_script",
    "resolve_resource",
]
