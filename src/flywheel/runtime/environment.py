"""Load local runtime bindings without exposing credentials."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class ModelBinding:
    """Resolved model endpoint, model identifier, and credential variable name."""

    name: str
    endpoint: str
    model: str
    credential_env: str


def load_dotenv(path: Path) -> None:
    """Load KEY=VALUE entries from path without overriding the process environment."""

    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)

