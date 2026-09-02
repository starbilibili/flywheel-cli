"""User-facing Flywheel authentication presentation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from flywheel.presentation import emit


_progress_console = Console(stderr=True)
_result_console = Console()
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_URL = re.compile(r"https://[^\s)\]]+")
_CODE = re.compile(r"[0-9][0-9 ]{4,8}[0-9]")


def auth_header(action: str) -> None:
    """Open a single Flywheel authentication flow."""

    if action == "login":
        title = "Flywheel 登录"
        description = "请按照提示在浏览器中完成身份验证。"
    else:
        title = "Flywheel 登出"
        description = "正在清理本机登录状态。"
    _progress_console.print(
        Panel(description, title=f"[bold cyan]{title}[/bold cyan]", border_style="cyan")
    )


@dataclass
class LoginTranscript:
    """Translate native authentication output into a small Flywheel prompt."""

    base_url: str | None = None
    code: str | None = None
    shown: bool = False
    waiting_shown: bool = False

    def consume(self, raw_line: str) -> None:
        """Consume one native output line without exposing provider details."""

        line = _ANSI.sub("", raw_line).strip()
        urls = _URL.findall(line)
        if urls:
            url = urls[0]
            if "#" in url:
                self._show_prompt(url)
            elif self.base_url is None:
                self.base_url = url
            return
        if _CODE.fullmatch(line):
            self.code = line.replace(" ", "")
            return
        if "Waiting for confirmation" in line:
            self._show_fallback()
            if not self.waiting_shown:
                _progress_console.print("等待浏览器确认…")
                self.waiting_shown = True

    def _show_prompt(self, url: str) -> None:
        if self.shown:
            return
        if self.code is None and "#" in url:
            fragment = url.rsplit("#", 1)[1].replace(" ", "")
            if fragment.isdigit():
                self.code = fragment
        login_url = self.base_url or url.split("#", 1)[0]
        self._emit_login_prompt(login_url)

    def _show_fallback(self) -> None:
        if self.shown or self.base_url is None:
            return
        self._emit_login_prompt(self.base_url)

    def _emit_login_prompt(self, login_url: str) -> None:
        """Render URL and code on their own lines so each can be double-click copied."""

        _progress_console.print()
        _progress_console.print("请在浏览器中打开登录页面：")
        _progress_console.print(
            Text(login_url, style=f"bold blue underline link={login_url}")
        )
        if self.code:
            _progress_console.print()
            _progress_console.print("验证码：")
            _progress_console.print(Text(self.code, style="bold"))
        self.shown = True


def emit_auth_result(value: dict[str, Any], output: str) -> None:
    """Render one Flywheel result while retaining stable JSON output."""

    if output == "json":
        emit(value, output)
        return
    status = str(value["status"])
    title = "登录成功" if status == "authenticated" else "已退出登录"
    _result_console.print()
    _result_console.print(Text.assemble(("✓ ", "bold green"), (title, "bold")))


def emit_auth_status(value: dict[str, Any], output: str) -> None:
    """Render only the aggregate Flywheel login state."""

    if output == "json":
        emit(value, output)
        return
    authenticated = value["status"] == "authenticated"
    marker = ("✓ ", "bold green") if authenticated else ("○ ", "bold yellow")
    label = "Flywheel 已登录" if authenticated else "Flywheel 未登录"
    _result_console.print(Text.assemble(marker, (label, "bold")))
