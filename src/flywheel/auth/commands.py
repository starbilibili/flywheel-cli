"""Unified authentication commands for the platforms used by Flywheel."""

from __future__ import annotations

import codecs
import errno
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Mapping

import typer

from flywheel.errors import FlywheelError
from flywheel.auth.presentation import (
    LoginTranscript,
    auth_header,
    emit_auth_result,
    emit_auth_status,
)
from flywheel.auth.vouch import wenyon_environment


app = typer.Typer(help="管理 Flywheel 登录状态。")


@dataclass(frozen=True)
class PlatformCommand:
    """One platform command invoked by the authentication orchestrator."""

    platform: str
    argv: tuple[str, ...]


TRISOL_LOGIN = PlatformCommand("trisol", ("trisol", "login"))
TRISOL_WHOAMI = PlatformCommand("trisol", ("trisol", "whoami", "-o", "json"))
WENYON_PROBE = PlatformCommand(
    "wenyon", ("wenyon-cli", "registry", "list", "-o", "json")
)
WENYON_LOGOUT = PlatformCommand("wenyon", ("wenyon-cli", "auth", "logout"))
TRISOL_LOGOUT = PlatformCommand("trisol", ("trisol", "logout"))


def _resolve(command: PlatformCommand) -> tuple[str, ...]:
    """Resolve a platform executable before making any authentication change."""

    executable = shutil.which(command.argv[0])
    if executable is None:
        raise FlywheelError(
            f"Required platform command is not installed or not on PATH: {command.argv[0]}"
        )
    return (executable, *command.argv[1:])


def _run(
    command: PlatformCommand,
    *,
    environment: Mapping[str, str] | None = None,
) -> None:
    """Run one internal platform command without exposing native output."""

    completed = subprocess.run(
        _resolve(command),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environment,
        check=False,
    )
    if completed.returncode != 0:
        raise FlywheelError(
            f"{command.platform} command failed with exit code {completed.returncode}: "
            f"{' '.join(command.argv)}"
        )


def _available(command: PlatformCommand) -> bool:
    return shutil.which(command.argv[0]) is not None


def _authenticated(
    command: PlatformCommand, environment: Mapping[str, str] | None = None
) -> bool:
    """Probe a platform identity without printing its native response."""

    if not _available(command):
        return False
    completed = subprocess.run(
        _resolve(command),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environment,
        check=False,
    )
    return completed.returncode == 0


def _validate_output(output: str) -> None:
    """Reject an invalid output mode before starting an interactive login."""

    if output not in {"text", "json"}:
        raise FlywheelError("Output format must be 'text' or 'json'")


def _wenyon_authenticated() -> bool:
    """Check Wenyon access through the token already minted by Trisol login."""

    try:
        environment = wenyon_environment()
    except FlywheelError:
        return False
    return _authenticated(WENYON_PROBE, environment)


def _read_pty(master_fd: int) -> bytes:
    """Read one PTY chunk, treating Linux end-of-stream EIO as EOF."""

    try:
        return os.read(master_fd, 4096)
    except OSError as error:
        if error.errno == errno.EIO:
            return b""
        raise


def _run_login() -> None:
    """Run the native login in a PTY while presenting the Flywheel device flow."""

    argv = _resolve(TRISOL_LOGIN)
    try:
        master_fd, slave_fd = os.openpty()
    except OSError as error:
        raise FlywheelError("Unable to prepare the Flywheel login terminal") from error
    try:
        process = subprocess.Popen(
            argv,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
        )
    except OSError as error:
        os.close(master_fd)
        os.close(slave_fd)
        raise FlywheelError("Unable to start Flywheel login") from error
    os.close(slave_fd)
    transcript = LoginTranscript()
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    buffered = ""
    try:
        # Select the native command's default login method without exposing its
        # provider-level choice to the Flywheel user.
        os.write(master_fd, b"\n")
        while True:
            chunk = _read_pty(master_fd)
            if not chunk:
                break
            buffered += decoder.decode(chunk)
            while "\n" in buffered:
                line, buffered = buffered.split("\n", 1)
                transcript.consume(line.rstrip("\r"))
        buffered += decoder.decode(b"", final=True)
        if buffered:
            transcript.consume(buffered.rstrip("\r"))
        return_code = process.wait()
    except BaseException:
        if process.poll() is None:
            process.terminate()
            process.wait()
        raise
    finally:
        os.close(master_fd)
    if return_code != 0:
        raise FlywheelError(f"Flywheel login failed with exit code {return_code}")


@app.command("login")
def login(output: str = typer.Option("text", "--output", "-o")) -> None:
    """登录 Flywheel。"""

    _validate_output(output)
    # Resolve both CLIs first so a missing dependency cannot leave a partial login.
    _resolve(TRISOL_LOGIN)
    _resolve(WENYON_PROBE)
    try:
        trisol_authenticated = _authenticated(TRISOL_WHOAMI)
        wenyon_authenticated = (
            _wenyon_authenticated() if trisol_authenticated else False
        )
        if not trisol_authenticated or not wenyon_authenticated:
            auth_header("login")
            _run_login()
        _run(TRISOL_WHOAMI)
        environment = wenyon_environment()
        _run(WENYON_PROBE, environment=environment)
    except FlywheelError:
        raise
    emit_auth_result(
        {"status": "authenticated"},
        output,
    )


@app.command("logout")
def logout(output: str = typer.Option("text", "--output", "-o")) -> None:
    """退出 Flywheel。"""

    _validate_output(output)
    auth_header("logout")
    errors: list[str] = []
    for command in (WENYON_LOGOUT, TRISOL_LOGOUT):
        try:
            _run(command)
        except FlywheelError as error:
            errors.append(str(error))
    if errors:
        raise FlywheelError("Flywheel logout could not be completed")
    emit_auth_result(
        {"status": "logged_out"},
        output,
    )


@app.command("status")
def status(
    output: str = typer.Option("text", "--output", "-o"),
) -> None:
    """查看 Flywheel 登录状态。"""

    _validate_output(output)
    trisol = _authenticated(TRISOL_WHOAMI)
    wenyon = _wenyon_authenticated() if trisol else False
    emit_auth_status(
        {"status": "authenticated" if wenyon and trisol else "not_authenticated"},
        output,
    )
