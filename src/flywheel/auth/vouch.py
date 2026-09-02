"""Read short-lived audience tokens from the shared Vouch session."""

from __future__ import annotations

import base64
import binascii
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from flywheel.errors import FlywheelError


_LOGIN_EXPIRED = "Flywheel 登录已失效，请运行 fw auth login"


def _trisol_executable() -> str:
    executable = shutil.which("trisol")
    if executable:
        return executable
    sibling = Path(sys.executable).with_name("trisol")
    if sibling.is_file() and os.access(sibling, os.X_OK):
        return str(sibling)
    raise FlywheelError("Flywheel 登录组件不可用，请重新安装 CLI")


def refresh_shared_session() -> None:
    """Refresh the shared multi-audience session before using a platform token."""

    completed = subprocess.run(
        (_trisol_executable(), "whoami", "-o", "json"),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if completed.returncode != 0:
        raise FlywheelError(_LOGIN_EXPIRED)


def shared_vouch_state_path() -> Path:
    """Return the state file used by Vouch-enabled infrastructure CLIs."""

    configured = os.environ.get("VOUCH_CONFIG_DIR")
    directory = Path(configured).expanduser() if configured else Path.home() / ".vouch"
    return directory / "state.json"


def audience_token(audience: str) -> str:
    """Load one short-lived access token without copying it to durable storage."""

    path = shared_vouch_state_path()
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        token = state["tokens"][audience]["access_token"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise FlywheelError(
            f"Shared Vouch session does not contain an {audience} token: {path}"
        ) from error
    if not isinstance(token, str) or not token:
        raise FlywheelError(f"Shared Vouch {audience} token is empty: {path}")
    return token


def _audience_claims(audience: str) -> dict[str, object]:
    """Decode trusted-session claims for non-authorization display metadata."""

    token = audience_token(audience)
    parts = token.split(".")
    if len(parts) != 3:
        raise FlywheelError(f"Shared Vouch {audience} token is not a JWT")
    encoded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(encoded))
    except (binascii.Error, json.JSONDecodeError, TypeError) as error:
        raise FlywheelError(
            f"Shared Vouch {audience} token does not contain valid claims"
        ) from error
    if not isinstance(payload, dict):
        raise FlywheelError(f"Shared Vouch {audience} claims are invalid")
    return payload


def audience_subject(audience: str) -> str:
    """Return the subject used only to partition local identity metadata."""

    subject = _audience_claims(audience).get("sub")
    if not isinstance(subject, str) or not subject:
        raise FlywheelError(f"Shared Vouch {audience} subject is empty")
    return subject


def audience_identity(audience: str) -> str:
    """Return a human-readable identity for resource provenance metadata.

    The JWT has already been accepted as a platform credential. Unverified claims
    are used only for display and provenance, never for authorization decisions.
    """

    claims = _audience_claims(audience)
    for key in ("name", "preferred_username", "email", "sub"):
        value = claims.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise FlywheelError(f"Shared Vouch {audience} identity is empty")


def wenyon_environment() -> dict[str, str]:
    """Build an ephemeral environment that lets Wenyon consume its Vouch token."""

    refresh_shared_session()
    environment = os.environ.copy()
    environment["WENYON_TOKEN"] = audience_token("wenyon-svc")
    return environment
