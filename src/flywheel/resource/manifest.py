"""Read and publish Wenyon manifests through the documented HTTP API."""

from __future__ import annotations

from functools import lru_cache
import json
import shutil
import subprocess
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from flywheel.auth.vouch import audience_token, wenyon_environment
from flywheel.errors import FlywheelError


def _command_error(completed: subprocess.CompletedProcess[str]) -> str:
    return next(
        (
            line.strip()
            for line in reversed(completed.stderr.splitlines())
            if line.strip() and not line.startswith("[wenyon-cli]")
        ),
        f"exit {completed.returncode}",
    )


@lru_cache(maxsize=1)
def _registry_server() -> str:
    executable = shutil.which("wenyon-cli")
    if executable is None:
        raise FlywheelError("当前环境未安装 wenyon-cli")
    completed = subprocess.run(
        (executable, "config", "get", "server"),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=wenyon_environment(),
        check=False,
    )
    if completed.returncode != 0:
        raise FlywheelError(f"无法读取文渊服务地址：{_command_error(completed)}")
    server = completed.stdout.strip().rstrip("/")
    if not server.startswith("https://"):
        raise FlywheelError("文渊服务地址必须使用 HTTPS")
    return server


def _request_json(request: Request, *, authenticated: bool) -> Any:
    if authenticated:
        request.add_header(
            "Authorization", f"Bearer {audience_token('wenyon-svc')}"
        )
    request.add_header("Accept", "application/json")
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        raise FlywheelError(
            f"文渊 Manifest 接口返回 HTTP {error.code}"
        ) from error
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
        raise FlywheelError("无法读取文渊 Manifest 数据") from error


def _manifest_endpoint(repo: str, ref: str) -> str:
    encoded_repo = quote(repo.strip("/"), safe="/")
    encoded_ref = quote(ref, safe=":")
    return f"{_registry_server()}/registry/repos/{encoded_repo}/manifests/{encoded_ref}"


def read_manifest(repo: str, ref: str = "latest") -> dict[str, Any]:
    """Read one manifest without downloading its resource files."""

    pointer = _request_json(Request(_manifest_endpoint(repo, ref)), authenticated=True)
    if not isinstance(pointer, dict) or not isinstance(pointer.get("url"), str):
        raise FlywheelError("文渊 Manifest 响应缺少下载地址")
    manifest = _request_json(Request(pointer["url"]), authenticated=False)
    if not isinstance(manifest, dict):
        raise FlywheelError("文渊 Manifest 内容格式无效")
    return manifest


def put_manifest(repo: str, tag: str, manifest: dict[str, Any]) -> Any:
    """Publish a complete manifest under one mutable tag."""

    body = json.dumps(
        manifest, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    request = Request(
        _manifest_endpoint(repo, tag),
        data=body,
        method="PUT",
        headers={"Content-Type": "application/json"},
    )
    return _request_json(request, authenticated=True)
