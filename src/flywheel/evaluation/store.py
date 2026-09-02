"""Persist local run state and results atomically."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def write_json(path: Path, value: Any) -> None:
    """Atomically write one JSON document."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.part")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def read_json(path: Path) -> dict[str, Any]:
    """Read one JSON object from disk."""

    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def update_status(run_dir: Path, run_id: str, status: str, **fields: Any) -> None:
    """Write the latest observable state for one local run."""

    write_json(run_dir / "status.json", {"run_id": run_id, "status": status, **fields})

