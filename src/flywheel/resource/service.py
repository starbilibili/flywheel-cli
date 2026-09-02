"""Local resource operations behind the resource command interface."""

from __future__ import annotations

from pathlib import Path

from flywheel.resource.refs import resolve_resource


def inspect_resource(reference: str, base_dir: Path) -> dict[str, object]:
    """Return a machine-readable description of one complete resource."""

    resource = resolve_resource(reference, None, base_dir)
    files = [resource.path] if resource.path.is_file() else sorted(
        item for item in resource.path.rglob("*") if item.is_file()
    )
    return {
        "reference": resource.reference,
        "type": resource.resource_type,
        "name": resource.name,
        "version": resource.version,
        "adapter": resource.adapter,
        "kind": "bundle",
        "path": str(resource.path),
        "digest": resource.digest,
        "file_count": len(files),
    }
