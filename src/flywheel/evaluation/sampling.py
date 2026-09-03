"""Load JSONL datasets and materialize deterministic selections."""

from __future__ import annotations

import json
import random
import csv
from pathlib import Path

from flywheel.config.models import SelectionRequest
from flywheel.errors import EvaluationError


def load_dataset(files: tuple[Path, ...]) -> list[dict[str, object]]:
    """Load common tabular formats and assign stable IDs when omitted.

    Dataset shape belongs to the selected Script, not to Flywheel.  Flywheel
    only needs to preserve each JSON object while selecting records; a script
    may use ``question``/``answer``, ``question``/``gt`` or another schema.
    """

    if not files:
        raise EvaluationError("Dataset resource declares no data files")
    records: list[dict[str, object]] = []
    for file_path in files:
        suffix = file_path.suffix.lower()
        try:
            if suffix == ".jsonl":
                with file_path.open(encoding="utf-8") as stream:
                    values = [json.loads(line) for line in stream if line.strip()]
            elif suffix == ".json":
                values = json.loads(file_path.read_text(encoding="utf-8"))
                if isinstance(values, dict):
                    values = values.get("records", values.get("data", values))
                if not isinstance(values, list):
                    raise EvaluationError(f"JSON Dataset must contain a list of records: {file_path}")
            elif suffix in {".csv", ".tsv"}:
                with file_path.open(newline="", encoding="utf-8") as stream:
                    values = list(csv.DictReader(stream, delimiter="\t" if suffix == ".tsv" else ","))
            else:
                raise EvaluationError(f"Unsupported Dataset file format: {file_path.suffix or file_path.name}")
        except (OSError, json.JSONDecodeError, csv.Error) as error:
            raise EvaluationError(f"Unable to read Dataset file: {file_path}: {error}") from error
        for offset, value in enumerate(values, 1):
            if not isinstance(value, dict):
                raise EvaluationError(f"Dataset row is not an object: {file_path}:{offset}")
            row = dict(value)
            row.setdefault("sample_id", f"{file_path.stem}-{offset:03d}")
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
