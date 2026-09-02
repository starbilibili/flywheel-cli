#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def parse_seeds(value: str) -> list[int]:
    """Parse the unique inference seeds used for each selected problem."""

    seeds = [int(item) for item in value.split(",") if item.strip()]
    if not seeds or len(seeds) != len(set(seeds)):
        raise ValueError("Seeds must be a non-empty unique comma-separated list")
    return seeds


def run_one(
    canary_script: Path,
    dataset: Path,
    endpoint: str,
    model: str,
    api_key_env: str,
    output_dir: Path,
    problem_index: int,
    seed: int,
    max_tokens: int,
    temperature: float,
    top_p: float,
    top_k: int,
    stream: bool,
    timeout: int,
    prompt_suffix: str,
) -> dict:
    """Execute or resume one logical problem/seed attempt."""

    output = output_dir / f"p{problem_index:02d}-s{seed:02d}.json"
    if output.exists():
        record = json.loads(output.read_text())
        finish_reason = record["raw_model_output"]["choices"][0].get("finish_reason")
        metric = record["scoring"]["metrics"]["exact_integer_match"]
        return {
            "status": "skipped",
            "problem_index": problem_index,
            "seed": seed,
            "sample_id": record["sample_id"],
            "attempt_id": record["attempt_id"],
            "finish_reason": finish_reason,
            "eligible": bool(metric["eligible"]),
            "correct": bool(metric["score"]),
            "usage": record.get("usage") or {},
        }

    command = [
        sys.executable,
        str(canary_script),
        "--dataset",
        str(dataset),
        "--problem-index",
        str(problem_index),
        "--endpoint",
        endpoint,
        "--model",
        model,
        "--api-key-env",
        api_key_env,
        "--output",
        str(output),
        "--max-tokens",
        str(max_tokens),
        "--temperature",
        str(temperature),
        "--top-p",
        str(top_p),
        "--top-k",
        str(top_k),
        "--seed",
        str(seed),
        "--timeout",
        str(timeout),
        "--prompt-suffix",
        prompt_suffix,
    ]
    if stream:
        command.append("--stream")
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(
            f"problem={problem_index} seed={seed}: {completed.stderr.strip()}"
        )
    summary = json.loads(completed.stdout.strip().splitlines()[-1])
    summary.update(
        {
            "status": "completed",
            "problem_index": problem_index,
            "seed": seed,
            "eligible": summary.get("finish_reason") == "stop",
        }
    )
    return summary


def load_run_config(path: Path) -> dict:
    """Load the Effective Run Config supplied by Flywheel."""

    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != "fw-effective-run-config/v1":
        raise ValueError(f"Unsupported Effective Run Config: {path}")
    return value


def count_records(path: Path) -> int:
    """Count selected JSONL records without interpreting Task Spec details."""

    with path.open(encoding="utf-8") as stream:
        return sum(1 for line in stream if line.strip())


def main() -> None:
    """Run all selected problems and aggregate their attempt results."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--run-config", type=Path, required=True)
    args = parser.parse_args()

    run_config = load_run_config(args.run_config)
    dataset = Path(run_config["dataset"]["path"])
    model = run_config["model"]
    settings = run_config["evaluation_config"]
    output = run_config["output"]
    request = settings.get("request") or {}
    execution = settings.get("execution") or {}
    samples = settings.get("samples") or {}
    problem_count = count_records(dataset)
    concurrency = int(execution.get("concurrency", 1))
    if problem_count < 1 or concurrency < 1:
        raise ValueError("Problem count and concurrency must be positive")
    seed_values = samples.get("seeds", [0])
    if not isinstance(seed_values, list):
        raise ValueError("Evaluation Config samples.seeds must be a list")
    seeds = parse_seeds(",".join(str(seed) for seed in seed_values))
    output_dir = Path(output["attempts"])
    summary_output = Path(output["summary"])
    output_dir.mkdir(parents=True, exist_ok=True)
    canary_script = Path(__file__).with_name("run_vllm_canary.py")
    tasks = [
        (problem_index, seed)
        for problem_index in range(problem_count)
        for seed in seeds
    ]

    results = []
    errors = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {
            executor.submit(
                run_one,
                canary_script,
                dataset,
                str(model["endpoint"]),
                str(model["model"]),
                str(model["credential_env"]),
                output_dir,
                problem_index,
                seed,
                int(request.get("max_output_tokens", 40960)),
                float(request.get("temperature", 1.0)),
                float(request.get("top_p", 0.95)),
                int(request.get("top_k", -1)),
                bool(request.get("stream", False)),
                int(execution.get("timeout_sec", 1800)),
                str(request.get("prompt_suffix", "")),
            ): (problem_index, seed)
            for problem_index, seed in tasks
        }
        for future in as_completed(futures):
            problem_index, seed = futures[future]
            try:
                result = future.result()
                results.append(result)
                outcome = (
                    "invalid"
                    if not result.get("eligible")
                    else ("succeeded" if result.get("correct") else "failed")
                )
                print(
                    json.dumps(
                        {
                            "event": "attempt",
                            "sample_id": result.get("sample_id"),
                            "attempt_id": result.get("attempt_id"),
                            "outcome": outcome,
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
            except Exception as error:
                failure = {
                    "status": "error",
                    "problem_index": problem_index,
                    "seed": seed,
                    "error": str(error),
                }
                errors.append(failure)
                print(
                    json.dumps(
                        {
                            "event": "attempt",
                            "attempt_id": f"problem-{problem_index}:seed-{seed}",
                            "outcome": "invalid",
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )

    eligible_results = [result for result in results if result.get("eligible")]
    operational_invalid = [
        {
            "problem_index": result["problem_index"],
            "seed": result["seed"],
            "finish_reason": result.get("finish_reason"),
        }
        for result in results
        if not result.get("eligible")
    ]
    correct = sum(bool(result["correct"]) for result in eligible_results)
    total_tokens = sum(int((result.get("usage") or {}).get("total_tokens") or 0) for result in results)
    failed = len(eligible_results) - correct
    invalid = len(tasks) - len(eligible_results)
    summary = {
        "schema_version": "fw-qa-summary/v1",
        "counts": {
            "expected": len(tasks),
            "completed": len(results) + len(errors),
            "succeeded": correct,
            "failed": failed,
            "invalid": invalid,
        },
        "metrics": {
            "exact_integer_match": {
                "scorer_id": "exact-integer-match/v1",
                "score": correct / len(eligible_results)
                if len(eligible_results) == len(tasks)
                else None,
                "numerator": correct,
                "denominator": len(eligible_results),
            }
        },
        "errors": errors,
        "operational_invalid": operational_invalid,
        "total_tokens": total_tokens,
    }
    summary_output.parent.mkdir(parents=True, exist_ok=True)
    temporary = summary_output.with_name(f".{summary_output.name}.part")
    temporary.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, summary_output)
    print(json.dumps(summary, sort_keys=True), flush=True)
    if errors or operational_invalid or len(results) != len(tasks):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
