#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


DEFAULT_PROMPT_SUFFIX = "Let's think step by step and output the final answer within \\boxed{}."


def completion_url(endpoint: str) -> str:
    """Build a chat-completions URL from a service root or an OpenAI v1 root."""

    base = endpoint.rstrip("/")
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def load_jsonl_dataset(path: Path) -> list[dict]:
    """Load and minimally validate question/answer records from JSONL."""

    files = sorted(path.glob("*.jsonl")) if path.is_dir() else [path]
    if not files:
        raise FileNotFoundError(f"No JSONL files found: {path}")

    rows = []
    for file_path in files:
        with file_path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row.get("question"), str) or not isinstance(row.get("answer"), str):
                    raise ValueError(f"Invalid question/answer at {file_path}:{line_number}")
                rows.append(row)
    return rows


def extract_boxed_answer(text: str) -> Optional[int]:
    """Extract the last boxed non-negative integer from a model response."""

    matches = re.findall(r"\\boxed\{\s*(\d{1,3})\s*\}", text)
    if not matches:
        return None
    return int(matches[-1])


def build_request_payload(
    model: str,
    messages: list,
    max_tokens: int,
    temperature: float,
    top_p: float,
    top_k: int,
    seed: int,
    stream: bool = False,
) -> dict:
    """Build one OpenAI-compatible chat-completions payload."""

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
        "top_k": top_k,
        "seed": seed,
    }
    if stream:
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}
    return payload


def collect_stream_response(response) -> tuple[dict, dict]:
    """Collect an SSE response into the non-streaming response shape."""

    response_id = None
    response_model = None
    response_created = None
    system_fingerprint = None
    finish_reason = None
    usage = None
    role = "assistant"
    content_parts = []
    reasoning_parts = []
    raw_digest = hashlib.sha256()
    chunk_count = 0

    for raw_line in response:
        raw_digest.update(raw_line)
        line = raw_line.decode("utf-8").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        chunk = json.loads(data)
        chunk_count += 1
        response_id = response_id or chunk.get("id")
        response_model = response_model or chunk.get("model")
        response_created = response_created or chunk.get("created")
        system_fingerprint = system_fingerprint or chunk.get("system_fingerprint")
        usage = chunk.get("usage") or usage
        choices = chunk.get("choices") or []
        if not choices:
            continue
        choice = choices[0]
        delta = choice.get("delta") or {}
        role = delta.get("role") or role
        content = delta.get("content")
        reasoning = delta.get("reasoning_content")
        if content:
            content_parts.append(str(content))
        if reasoning:
            reasoning_parts.append(str(reasoning))
        finish_reason = choice.get("finish_reason") or finish_reason

    response_body = {
        "id": response_id,
        "object": "chat.completion",
        "created": response_created,
        "model": response_model,
        "system_fingerprint": system_fingerprint,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": role,
                    "content": "".join(content_parts),
                    "reasoning_content": "".join(reasoning_parts),
                },
                "finish_reason": finish_reason,
            }
        ],
        "usage": usage,
    }
    transport = {
        "stream": True,
        "chunk_count": chunk_count,
        "raw_sse_sha256": raw_digest.hexdigest(),
    }
    return response_body, transport


def main() -> None:
    """Execute and grade one problem/seed attempt."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--problem-index", type=int, required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-key-env", default="LLM_API_KEY")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-tokens", type=int, default=40960)
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--top-p", type=float, default=0.95)
    parser.add_argument("--top-k", type=int, default=-1)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--prompt-suffix", default=DEFAULT_PROMPT_SUFFIX)
    args = parser.parse_args()

    api_key = os.environ.get(args.api_key_env, "").strip()
    if not api_key:
        raise ValueError(f"API key environment variable is empty: {args.api_key_env}")

    if args.output.exists():
        raise FileExistsError(f"Output already exists: {args.output}")

    dataset = load_jsonl_dataset(args.dataset)
    if args.problem_index < 0 or args.problem_index >= len(dataset):
        raise IndexError(f"Problem index out of range: {args.problem_index}")

    row = dataset[args.problem_index]
    messages = [
        {
            "role": "user",
            "content": f"{row['question'].rstrip()}\n\n{args.prompt_suffix}",
        }
    ]
    expected_answer = int(row["answer"])
    payload = build_request_payload(
        args.model,
        messages,
        args.max_tokens,
        args.temperature,
        args.top_p,
        args.top_k,
        args.seed,
        args.stream,
    )

    request = urllib.request.Request(
        completion_url(args.endpoint),
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    started_at = time.monotonic()
    with opener.open(request, timeout=args.timeout) as response:
        if args.stream:
            response_body, transport = collect_stream_response(response)
        else:
            response_body = json.load(response)
            transport = {"stream": False}
    latency_sec = time.monotonic() - started_at

    response_model = response_body.get("model")
    if response_model != args.model:
        raise ValueError(f"Unexpected response model: {response_model!r}")

    choice = response_body["choices"][0]
    message = choice["message"]
    answer_text = "\n".join(
        str(message.get(key) or "") for key in ("reasoning_content", "content")
    )
    extracted_answer = extract_boxed_answer(answer_text)

    finish_reason = choice.get("finish_reason")
    eligible = finish_reason == "stop"
    correct = extracted_answer == expected_answer
    sample_id = str(row.get("sample_id", f"problem-{args.problem_index}"))
    attempt_id = f"{sample_id}:seed-{args.seed}"
    record = {
        "schema_version": "fw-qa-attempt/v1",
        "sample_id": sample_id,
        "attempt_id": attempt_id,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "status": "invalid" if not eligible else ("succeeded" if correct else "failed"),
        "model_input": messages,
        "inference_config": {
            key: value for key, value in payload.items() if key != "messages"
        },
        "raw_model_output": response_body,
        "parsed_answer": extracted_answer,
        "ground_truth": expected_answer,
        "scoring": {
            "scorer_id": "exact-integer-match/v1",
            "metrics": {
                "exact_integer_match": {
                    "score": 1 if correct else 0,
                    "eligible": eligible,
                }
            },
        },
        "usage": response_body.get("usage") or {},
        "latency_sec": latency_sec,
        "transport": transport,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.part")
    temporary.write_text(json.dumps(record, ensure_ascii=False, sort_keys=True))
    os.replace(temporary, args.output)

    print(
        json.dumps(
            {
                "response_id": response_body.get("id"),
                "response_model": response_model,
                "sample_id": sample_id,
                "attempt_id": attempt_id,
                "finish_reason": finish_reason,
                "eligible": eligible,
                "usage": response_body.get("usage"),
                "ground_truth": expected_answer,
                "parsed_answer": extracted_answer,
                "correct": correct,
                "output": str(args.output),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
