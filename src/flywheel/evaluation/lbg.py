"""LBG submission adapter for Flywheel Task runs."""

from __future__ import annotations

from dataclasses import dataclass
import json
import base64
import os
import shlex
from pathlib import Path
import shutil
import tempfile
import zipfile
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flywheel.auth.vouch import audience_token, refresh_shared_session
from flywheel.errors import EvaluationError
from flywheel.evaluation.planner import RunPlan
from flywheel.runtime.environment import load_dotenv


_GENERATED_INPUTS = frozenset({"effective-run-config.json", "inputs", "outputs"})


def _copy_script_snapshot(launcher: Path, bundle: Path) -> None:
    resolved_launcher = launcher.resolve()
    if not resolved_launcher.is_file() or resolved_launcher.name != "run.sh":
        raise EvaluationError(f"Script launcher is not a valid run.sh: {resolved_launcher}")
    script_root = resolved_launcher.parent
    collisions = sorted(
        name for name in _GENERATED_INPUTS if (script_root / name).exists()
    )
    if collisions:
        raise EvaluationError(
            "Script Snapshot 使用了 Flywheel 保留文件名：" + ", ".join(collisions)
        )
    for source in sorted(script_root.rglob("*")):
        relative = source.relative_to(script_root)
        if relative == Path("manifest.json"):
            continue
        if source.is_symlink():
            raise EvaluationError(f"Script Snapshot 不允许符号链接：{relative}")
        target = bundle / relative
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


@dataclass(frozen=True)
class LbgSettings:
    """Deployment defaults required by the LBG OpenAPI gateway."""

    project_id: str
    image: str
    sku: str
    endpoint: str = "https://open.bohrium.com"
    audience: str = "lbg"

    @classmethod
    def from_environment(cls, project_root: Path | None = None) -> "LbgSettings":
        """Load deployment settings and Vouch audience from environment."""
        if project_root is not None:
            load_dotenv(project_root / ".env")
        load_dotenv(Path.cwd() / ".env")
        values = {
            "project_id": os.environ.get("FLYWHEEL_LBG_PROJECT_ID", os.environ.get("BOHRIUM_PROJECT_ID", "")).strip(),
            "image": os.environ.get("FLYWHEEL_LBG_IMAGE", "").strip(),
            "sku": os.environ.get("FLYWHEEL_LBG_SKU", "").strip(),
        }
        missing = [key for key, value in values.items() if not value]
        if missing:
            raise EvaluationError(
                "LBG 配置不完整，请设置 "
                + ", ".join(f"FLYWHEEL_LBG_{key.upper()}" for key in missing)
            )
        return cls(
            **values,
            endpoint=os.environ.get("FLYWHEEL_LBG_ENDPOINT", "https://open.bohrium.com").rstrip("/"),
            audience=os.environ.get("FLYWHEEL_LBG_AUDIENCE", "lbg").strip() or "lbg",
        )


class LbgClient:
    """Small Vouch-authenticated client for the documented LBG OpenAPI."""

    def __init__(self, settings: LbgSettings) -> None:
        self.settings = settings

    def _request(self, method: str, path: str, payload: dict[str, Any] | None) -> bytes:
        """Send one authenticated request after refreshing the shared session."""

        refresh_shared_session()
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            self.settings.endpoint + path,
            data=body,
            method=method,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {audience_token(self.settings.audience)}",
                **({"Content-Type": "application/json"} if body is not None else {}),
            },
        )
        with urlopen(request, timeout=60) as response:
            return response.read()

    def _json(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> Any:
        try:
            raw = self._request(method, path, payload)
        except HTTPError as error:
            if error.code == 401:
                return self._check_result(
                    self._decode(self._retry_request(method, path, payload))
                )
            detail = error.read().decode("utf-8", errors="replace")[:500]
            raise EvaluationError(f"LBG 请求失败 HTTP {error.code}: {detail}") from error
        except (URLError, TimeoutError, OSError) as error:
            raise EvaluationError(f"无法连接 LBG：{error}") from error
        return self._check_result(self._decode(raw))

    @staticmethod
    def _check_result(value: Any) -> Any:
        """Turn an LBG business-level error code into a Flywheel error."""

        if isinstance(value, dict) and value.get("code") not in (None, 0):
            error = value.get("error")
            message = error.get("msg") if isinstance(error, dict) else error
            raise EvaluationError(f"LBG 请求被拒绝（code={value['code']}）：{message or value}")
        return value

    def _retry_request(
        self, method: str, path: str, payload: dict[str, Any] | None
    ) -> bytes:
        """Refresh once and retry after an authentication failure."""

        try:
            return self._request(method, path, payload)
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:500]
            raise EvaluationError(
                f"LBG 鉴权失败（刷新后仍为 HTTP {error.code}）：{detail}"
            ) from error
        except (URLError, TimeoutError, OSError) as error:
            raise EvaluationError(f"刷新 LBG 鉴权后请求失败：{error}") from error

    @staticmethod
    def _decode(raw: bytes) -> Any:
        """Decode one JSON response body."""

        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as error:
            raise EvaluationError("LBG 返回了无效 JSON") from error

    def create_job(self, *, name: str, command: str) -> dict[str, Any]:
        """Create an unsubmitted container job and return its gateway payload."""
        try:
            project_id: int = int(self.settings.project_id)
        except ValueError as error:
            raise EvaluationError("FLYWHEEL_LBG_PROJECT_ID 必须是数字") from error
        result = self._json("POST", "/openapi/v4/job/create", {
            "projectId": project_id,
            "jobType": "container",
            "jobName": name,
            "imageName": self.settings.image,
            "scassType": self.settings.sku,
            "cmd": command,
        })
        return result if isinstance(result, dict) else {"data": result}

    def add_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Submit a fully populated job payload to the scheduler."""
        result = self._json("POST", "/openapi/v4/job/add", payload)
        return result if isinstance(result, dict) else {"data": result}

    def upload_bundle(self, bundle: Path, create_result: dict[str, Any]) -> str:
        """Upload a bundle using the Tiefblue token returned by job/create."""
        data = create_result.get("data", create_result)
        if not isinstance(data, dict):
            raise EvaluationError("LBG create 返回缺少上传信息")
        host, prefix, token = (data.get(key) for key in ("storeHost", "storePath", "token"))
        if not all(isinstance(value, str) and value for value in (host, prefix, token)):
            raise EvaluationError("LBG create 返回缺少 storeHost/storePath/token")
        descriptor, archive_name = tempfile.mkstemp(prefix="fw-lbg-", suffix=".zip")
        os.close(descriptor)
        archive = Path(archive_name)
        try:
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
                for path in sorted(bundle.rglob("*")):
                    if path.is_file():
                        output.write(path, path.relative_to(bundle).as_posix())
            object_key = f"{prefix.rstrip('/')}/input/{archive.name}"
            params = base64.b64encode(json.dumps({"path": object_key}).encode()).decode()
            request = Request(
                host.rstrip("/") + "/api/upload/binary",
                data=archive.read_bytes(),
                method="POST",
                headers={"Authorization": f"Bearer {token}", "X-Storage-Param": params},
            )
            with urlopen(request, timeout=300) as response:
                result = self._decode(response.read())
            if isinstance(result, dict) and result.get("code") not in (None, 0):
                raise EvaluationError(f"LBG 输入包上传失败：{result}")
            return object_key
        except (OSError, HTTPError, URLError) as error:
            raise EvaluationError(f"LBG 输入包上传失败：{error}") from error
        finally:
            archive.unlink(missing_ok=True)

    def list_jobs(self, *, project_id: str | None = None) -> Any:
        """List jobs; callers map the gateway's numeric status to Run status."""

        suffix = "" if project_id is None else f"?projectId={project_id}"
        return self._json("GET", f"/openapi/v4/job/list{suffix}")

    def find_job(self, lebesgue_job_id: str) -> Any:
        """Find one job by the Lebesgue-side identifier from job/add."""

        payload = self.list_jobs(project_id=self.settings.project_id)
        candidates: Any = payload.get("data", payload) if isinstance(payload, dict) else payload
        if isinstance(candidates, dict):
            for key in ("items", "results", "jobs", "list"):
                if isinstance(candidates.get(key), list):
                    candidates = candidates[key]
                    break
        if not isinstance(candidates, list):
            candidates = []
        wanted = str(lebesgue_job_id)
        for record in candidates:
            if not isinstance(record, dict):
                continue
            identifiers = (
                record.get("id"),
                record.get("jobId"),
                record.get("lebesgueJobId"),
                record.get("thirdpartyId"),
            )
            if any(str(value) == wanted for value in identifiers if value is not None):
                return {"code": 0, "data": record}
        raise EvaluationError(f"LBG Job 不存在或不属于当前项目：{lebesgue_job_id}")

    def logs(self, lebesgue_job_id: str) -> Any:
        """Fetch logs using the Lebesgue job identifier."""
        return self._json("GET", f"/openapi/v4/job/{lebesgue_job_id}/log")

    def detail(self, bohr_job_id: str) -> Any:
        """Fetch result metadata using the Bohrium job identifier."""
        return self._json("GET", f"/openapi/v4/job/detail/{bohr_job_id}")

    def terminate(self, job_id: str) -> Any:
        """Stop a queued or running job."""
        return self._json("POST", f"/openapi/v4/job/terminate/{job_id}")

    def delete(self, job_id: str) -> Any:
        """Delete a stopped or completed job record."""
        return self._json("POST", f"/openapi/v4/job/del/{job_id}")


def build_input_bundle(plan: RunPlan) -> Path:
    """Build the minimal remote input tree without credentials."""

    bundle = Path(tempfile.mkdtemp(prefix=f"fw-lbg-{plan.run_id}-"))
    try:
        _copy_script_snapshot(Path(plan.script_command[0]), bundle)
        shutil.copy2(plan.effective_run_config, bundle / "effective-run-config.json")
        selected = plan.run_dir / "inputs" / "selected-dataset.jsonl"
        if not selected.is_file():
            raise EvaluationError(f"Selected dataset does not exist: {selected}")
        selected_target = bundle / "inputs" / "selected-dataset.jsonl"
        selected_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(selected, selected_target)
        (bundle / "run.sh").chmod(0o755)
        return bundle
    except BaseException:
        shutil.rmtree(bundle, ignore_errors=True)
        raise


def _runtime_environment(plan: RunPlan) -> dict[str, str]:
    """Resolve declared runtime secrets without writing their values to disk."""

    try:
        config = json.loads(plan.effective_run_config.read_text(encoding="utf-8"))
        credential_env = config["model"]["credential_env"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise EvaluationError("Effective Run Config 缺少 model.credential_env") from error
    if not isinstance(credential_env, str) or not credential_env:
        raise EvaluationError("model.credential_env 必须是非空环境变量名")
    credential = os.environ.get(credential_env, "")
    if not credential:
        raise EvaluationError(f"模型凭据环境变量未设置：{credential_env}")
    return {credential_env: credential}


def submit(
    plan: RunPlan,
    settings: LbgSettings,
    *,
    dry_run: bool = False,
) -> Any:
    """Submit one run through LBG create, upload, and add phases."""

    credential_env: str | None = None
    credential_value: str | None = None
    if not dry_run:
        runtime_environment = _runtime_environment(plan)
        credential_env, credential_value = next(iter(runtime_environment.items()))

    bundle = build_input_bundle(plan)
    command = "./run.sh --run-config ./effective-run-config.json > stdout.log 2>&1"
    if credential_env and credential_value is not None:
        command = f"export {credential_env}={shlex.quote(credential_value)} && {command}"
    argv = [
        "lbg", "job", "submit", "--project_id", settings.project_id,
        "--image_name", settings.image, "--scass_type", settings.sku,
        "--job_name", plan.run_id, "--cmd", command,
        "--input", str(bundle), "--only_job_id",
    ]
    if dry_run:
        shutil.rmtree(bundle, ignore_errors=True)
        return argv
    client = LbgClient(settings)
    try:
        created = client.create_job(name=plan.run_id, command=command)
        data = created.get("data", created)
        if not isinstance(data, dict):
            raise EvaluationError("LBG create 返回格式无效")
        oss_path = client.upload_bundle(bundle, created)
        payload = {
            "projectId": int(settings.project_id),
            "jobType": "container",
            "jobName": plan.run_id,
            "imageName": settings.image,
            "scassType": settings.sku,
            "nnode": 1,
            "cmd": command,
            "jobId": data.get("jobId"),
            "ossPath": [oss_path],
            "inputFileType": 3,
            "inputFileMethod": 1,
            "logFiles": ["stdout.log"],
            "outFiles": ["outputs/script-summary.json", "outputs/attempts"],
        }
        added = client.add_job(payload)
        safe_created = json.loads(json.dumps(created))
        for container in (safe_created, safe_created.get("data") if isinstance(safe_created, dict) else None):
            if isinstance(container, dict):
                container.pop("token", None)
        return {"created": safe_created, "submitted": added, "run_id": plan.run_id}
    finally:
        shutil.rmtree(bundle, ignore_errors=True)
