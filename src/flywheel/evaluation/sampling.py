"""Load JSONL datasets and materialize deterministic selections."""

from __future__ import annotations

import json
import random
from pathlib import Path

from flywheel.config.models import SelectionRequest
from flywheel.errors import EvaluationError


def load_dataset(files: tuple[Path, ...]) -> list[dict[str, object]]:
    """Load declared JSONL files and assign stable IDs when the source omits them."""

    if not files:
        raise EvaluationError("Dataset resource declares no JSONL files")
    records: list[dict[str, object]] = []
    for file_path in files:
        with file_path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise EvaluationError(f"Dataset row is not an object: {file_path}:{line_number}")
                if not isinstance(value.get("question"), str) or not isinstance(value.get("answer"), str):
                    raise EvaluationError(f"Dataset row lacks question/answer: {file_path}:{line_number}")
                row = dict(value)
                row.setdefault("sample_id", f"{file_path.stem}-{line_number:03d}")
                records.append(row)
    return records


def select_records(
    records: list[dict[str, object]], request: SelectionRequest
) -> list[dict[str, object]]:
    """Select records deterministically and preserve the sampled execution order."""

    if not request.replacement and request.count > len(records):
        raise EvaluationError(
            f"Cannot select {request.count} records without replacement from {len(records)} records"
        )
    generator = random.Random(request.seed)
    if request.replacement:
        return [dict(generator.choice(records)) for _ in range(request.count)]
    return [dict(record) for record in generator.sample(records, request.count)]


def write_selected_dataset(path: Path, records: list[dict[str, object]]) -> None:
    """Write the exact selected records consumed by the evaluator."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
