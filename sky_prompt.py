#!/usr/bin/env python3
"""Simple terminal prompt CLI powered by Sky MCP."""

from __future__ import annotations

import argparse
import bisect
import codeop
from contextlib import contextmanager
import difflib
import errno
import json
import os
import pty
import re
import select
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

DEFAULT_ENDPOINT = "https://api.unchainedsky.com/mcp"
DEFAULT_URL = "https://chatgpt.com"
DEFAULT_TRANSPORT = "unchained"
SUPPORTED_TRANSPORTS = ("unchained", "sky-mcp")
DEFAULT_AGENT_ENV_PATH = Path.home() / "sky-agent" / ".env"
LEGACY_INSTALL_ENV_PATH = Path.home() / "unchained-agent" / ".env"
DEFAULT_REPL_HISTORY_PATH = Path.home() / ".sky_prompt_history"
DEFAULT_REPL_HISTORY_LIMIT = 1000
_FOREGROUND_BROWSER_CONTEXT_STACK: List[str] = []
DEFAULT_WAIT_TIMEOUT = 180
DEFAULT_POLL_INTERVAL = 1.0
DEFAULT_INSTALL_TIMEOUT = 600
DEFAULT_LOCAL_SETUP_TIMEOUT = 900
DEFAULT_AGENT_DISCOVERY_GRACE_SECONDS = 20
DEFAULT_AGENT_DISCOVERY_POLL_SECONDS = 2.0
DEFAULT_RENDER_STABLE_POLLS = 3
DEFAULT_RENDER_SETTLE_SECONDS = 1.2
DEFAULT_COMPOSER_SETTLE_SECONDS = 0.8
DEFAULT_ALIAS_DIR = Path.home() / ".local" / "bin"
DEFAULT_OUTPUT_FORMAT = "markdown"
DEFAULT_RUN_BACKEND = "pyreplab"
DEFAULT_UNCHAINED_PORT = int(os.getenv("UNCHAINED_PORT") or "9222")
DEFAULT_BROWSER_TAB = "auto"
DEFAULT_CHROME_PROFILE = str(os.getenv("SKY_CHROME_PROFILE") or "Default").strip() or "Default"
SUPPORTED_RUN_BACKENDS = ("local", "pyreplab")
SUPPORTED_OUTPUT_FORMATS = ("markdown", "plain", "json")
ANSI_RESET = "\033[0m"
ANSI_BOLD = "\033[1m"
ANSI_DIM = "\033[2m"
ANSI_CYAN = "\033[36m"
PRIMARY_API_KEY_ENV = "SKY_API_KEY"
PRIMARY_AGENT_ID_ENV = "SKY_AGENT_ID"
PRIMARY_TARGET_URL_ENV = "SKY_TARGET_URL"
SETUP_ASSUME_YES_ENV = "SKY_SETUP_ASSUME_YES"
LEGACY_INSTALL_API_KEY_ENV = "UNCHAINED_API_KEY"
INSTALL_SCRIPT_URL = "https://api.unchainedsky.com/install.sh"
LANGUAGE_ANSI_BY_LANGUAGE: Dict[str, str] = {
    "python": "\033[38;5;75m",
    "bash": "\033[38;5;78m",
    "sql": "\033[38;5;214m",
    "postgresql": "\033[38;5;214m",
    "mysql": "\033[38;5;214m",
    "sqlite": "\033[38;5;214m",
    "javascript": "\033[38;5;220m",
    "js": "\033[38;5;220m",
    "typescript": "\033[38;5;117m",
    "ts": "\033[38;5;117m",
    "json": "\033[38;5;141m",
    "yaml": "\033[38;5;109m",
    "yml": "\033[38;5;109m",
    "html": "\033[38;5;208m",
    "css": "\033[38;5;177m",
    "text": "\033[38;5;244m",
}
REPL_COMMAND_SPECS: Tuple[Dict[str, Any], ...] = (
    {"name": "/url", "usage": "/url <url>", "summary": "navigate the connected browser target"},
    {"name": "/submit", "usage": "/submit on|off", "summary": "toggle prompt submission after fill"},
    {"name": "/format", "usage": "/format markdown|plain|json", "summary": "set assistant output format"},
    {"name": "/backend", "usage": "/backend [local|pyreplab]", "summary": "show or switch the /run backend"},
    {"name": "/py", "usage": "/py <code>", "summary": "run Python directly in the pyreplab session"},
    {"name": "/pyfile", "usage": "/pyfile <path.py>", "summary": "run a Python file in the pyreplab session"},
    {"name": "/history", "usage": "/history [n]", "summary": "show recent interactive turns"},
    {"name": "/last", "usage": "/last", "summary": "reprint the last turn with active refs"},
    {"name": "/cells", "usage": "/cells [n|all]", "summary": "list stored runnable cells"},
    {"name": "/show", "usage": "/show [cell|@ref]", "summary": "print a cell or response ref"},
    {"name": "/run", "usage": "/run [cell|@ref] [timeout]", "summary": "execute a cell or response ref"},
    {"name": "/focus", "usage": "/focus <cell|@ref>", "summary": "set the current focused cell/ref"},
    {"name": "/fork", "usage": "/fork <src|@ref> [dst]", "summary": "clone a cell for mutation"},
    {"name": "/edit", "usage": "/edit <cell|@ref>", "summary": "open a cell in $EDITOR"},
    {"name": "/save", "usage": "/save <cell|@ref> <path>", "summary": "write a cell to disk"},
    {"name": "/diff", "usage": "/diff <a|@ref> <b|@ref>", "summary": "show a unified diff between two cells"},
    {"name": "/ddm", "usage": "/ddm", "summary": "inspect the current page with ddm"},
    {"name": "/help", "usage": "/help", "summary": "show interactive commands"},
    {"name": "/exit", "usage": "/exit", "summary": "leave interactive mode"},
)
REPL_REF_COMMANDS: Tuple[str, ...] = ("/run", "/show", "/edit", "/fork", "/save", "/diff", "/focus")
LANGUAGE_ALIASES: Dict[str, str] = {
    "py": "python",
    "python3": "python",
    "shell": "bash",
    "sh": "bash",
    "zsh": "bash",
    "console": "bash",
}
RUNNER_BY_LANGUAGE: Dict[str, str] = {
    "python": "python",
    "bash": "bash",
    "javascript": "node",
    "js": "node",
    "typescript": "ts-node",
    "ts": "ts-node",
    "ruby": "ruby",
    "perl": "perl",
}
CELL_PREFIX_BY_LANGUAGE: Dict[str, str] = {
    "python": "py",
    "bash": "sh",
    "javascript": "js",
    "js": "js",
    "typescript": "ts",
    "ts": "ts",
    "ruby": "rb",
    "perl": "pl",
}
CELL_EXTENSION_BY_LANGUAGE: Dict[str, str] = {
    "python": ".py",
    "bash": ".sh",
    "javascript": ".js",
    "js": ".js",
    "typescript": ".ts",
    "ts": ".ts",
    "ruby": ".rb",
    "perl": ".pl",
}
SHELL_PREFIXES: Tuple[str, ...] = (
    "pip ",
    "python ",
    "python3 ",
    "uv ",
    "npm ",
    "pnpm ",
    "yarn ",
    "brew ",
    "apt ",
    "apt-get ",
    "conda ",
    "git ",
    "curl ",
    "wget ",
    "chmod ",
    "chown ",
    "ls ",
    "cd ",
    "mkdir ",
    "rm ",
    "cp ",
    "mv ",
    "echo ",
    "./",
    "bash ",
    "sh ",
    "zsh ",
    "node ",
    "npx ",
    "docker ",
    "kubectl ",
    "poetry ",
    "pytest ",
    "make ",
    "cargo ",
    "go ",
)
SHELL_BINARIES: set = {
    "pip",
    "python",
    "python3",
    "uv",
    "npm",
    "pnpm",
    "yarn",
    "brew",
    "apt",
    "apt-get",
    "conda",
    "git",
    "curl",
    "wget",
    "chmod",
    "chown",
    "ls",
    "cd",
    "mkdir",
    "rm",
    "cp",
    "mv",
    "echo",
    "bash",
    "sh",
    "zsh",
    "node",
    "npx",
    "docker",
    "kubectl",
    "poetry",
    "pytest",
    "make",
    "cargo",
    "go",
}
LANGUAGE_LABEL_LINE_MAP: Dict[str, str] = {
    "python": "python",
    "python3": "python",
    "py": "python",
    "bash": "bash",
    "shell": "bash",
    "sh": "bash",
    "zsh": "bash",
    "javascript": "javascript",
    "js": "javascript",
    "typescript": "typescript",
    "ts": "typescript",
    "json": "json",
    "csv": "csv",
    "sql": "sql",
    "plain text": "text",
}
UI_NOISE_LINES: set = {
    "run",
    "copy",
    "copy code",
}
OUTPUT_SECTION_LABELS: set = {
    "result",
    "output",
    "stdout",
    "response",
}
PREFERRED_NAVIGATE_TOOLS = ("cdp_navigate", "navigate")
PREFERRED_JS_TOOLS = ("js_eval", "execute_js")
PREFERRED_CLICK_TOOLS = ("cdp_click", "click")
PREFERRED_TYPE_TOOLS = ("cdp_type", "type_text")
PREFERRED_DDM_TOOLS = ("ddm",)


class MCPError(RuntimeError):
    """Raised when an MCP request fails."""


class MCPClient:
    def __init__(self, endpoint: str, api_key: str, timeout: int = 45, debug: bool = False):
        self.endpoint = endpoint
        self.api_key = api_key
        self.timeout = timeout
        self.debug = debug
        self.session_id: Optional[str] = None
        self._rpc_seq = 0

    def _next_rpc_id(self, prefix: str) -> str:
        self._rpc_seq += 1
        safe_prefix = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(prefix or "rpc"))
        return f"{safe_prefix}-{int(time.time() * 1000)}-{self._rpc_seq}"

    def initialize(self) -> None:
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_rpc_id("init"),
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "sky-prompt-cli", "version": "0.1.0"},
            },
        }
        response = self._rpc_request(payload, include_session=False, allow_empty=False)
        if not self.session_id and isinstance(response.get("result"), dict):
            maybe_sid = response["result"].get("sessionId")
            if isinstance(maybe_sid, str) and maybe_sid:
                self.session_id = maybe_sid

        if not self.session_id:
            raise MCPError(
                "initialize succeeded but no MCP session id was returned in headers."
            )

        notification = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }
        self._rpc_request(notification, include_session=True, allow_empty=True)

    def list_tools(self) -> List[str]:
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_rpc_id("tools-list"),
            "method": "tools/list",
            "params": {},
        }
        try:
            response = self._rpc_request(payload, include_session=True, allow_empty=False)
        except MCPError:
            return []

        result = response.get("result")
        if not isinstance(result, dict):
            return []

        tools = result.get("tools")
        if not isinstance(tools, list):
            return []

        names: List[str] = []
        for item in tools:
            if isinstance(item, dict):
                name = item.get("name")
                if isinstance(name, str):
                    names.append(name)
        return names

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_rpc_id(f"tool-call-{name}"),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        response = self._rpc_request(payload, include_session=True, allow_empty=False)
        result = response.get("result")
        if isinstance(result, dict) and result.get("isError"):
            message_parts: List[str] = []
            content = result.get("content")
            if isinstance(content, list):
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        message_parts.append(text.strip())
            message = "\n".join(message_parts).strip() if message_parts else json.dumps(result)
            raise MCPError(message)
        if isinstance(result, dict):
            return result
        return {"raw_result": result}

    def _rpc_request(
        self,
        payload: Dict[str, Any],
        include_session: bool,
        allow_empty: bool,
    ) -> Dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self.api_key}",
        }
        if include_session and self.session_id:
            headers["mcp-session-id"] = self.session_id

        request = urllib.request.Request(
            self.endpoint,
            data=body,
            method="POST",
            headers=headers,
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw_body = response.read().decode("utf-8", "replace")
                sid = response.headers.get("Mcp-Session-Id") or response.headers.get(
                    "mcp-session-id"
                )
                if sid:
                    self.session_id = sid
                if self.debug:
                    print(
                        f"[debug] status={response.status} sid={self.session_id} body={raw_body[:400]}",
                        file=sys.stderr,
                    )
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", "replace")
            raise MCPError(f"HTTP {exc.code}: {error_body}") from exc
        except urllib.error.URLError as exc:
            raise MCPError(f"Network error: {exc.reason}") from exc

        parsed = parse_rpc_response(raw_body)
        if parsed is None:
            if allow_empty:
                return {}
            raise MCPError(f"Unexpected non-JSON response body: {raw_body[:400]}")

        if "error" in parsed:
            raise MCPError(str(parsed["error"]))
        return parsed


def resolve_unchained_command(explicit_command: Optional[str] = None) -> Optional[List[str]]:
    raw = str(
        explicit_command
        or os.getenv("SKY_UNCHAINED_CMD")
        or os.getenv("UNCHAINED_CMD")
        or ""
    ).strip()
    if raw:
        try:
            parts = shlex.split(raw)
        except ValueError:
            return None
        return parts if parts else None

    discovered = shutil.which("unchained")
    if discovered:
        return [discovered]
    common_candidates = [
        Path.cwd() / ".venv" / "bin" / "unchained",
        Path.home() / ".local" / "bin" / "unchained",
    ]
    for candidate in common_candidates:
        try:
            if candidate.is_file() and os.access(str(candidate), os.X_OK):
                return [str(candidate)]
        except OSError:
            continue
    return None


def resolve_uv_command() -> Optional[List[str]]:
    discovered = shutil.which("uv")
    if discovered:
        return [discovered]
    common_candidates = [
        Path.home() / ".local" / "bin" / "uv",
    ]
    for candidate in common_candidates:
        try:
            if candidate.is_file() and os.access(str(candidate), os.X_OK):
                return [str(candidate)]
        except OSError:
            continue
    return None


def build_text_tool_result(stdout_text: str) -> Dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": str(stdout_text or ""),
            }
        ]
    }


class LocalCLIClient:
    def __init__(
        self,
        command: Sequence[str],
        port: int = DEFAULT_UNCHAINED_PORT,
        tab: str = DEFAULT_BROWSER_TAB,
        chrome_profile: str = DEFAULT_CHROME_PROFILE,
        startup_url: str = DEFAULT_URL,
        auto_launch: bool = False,
        timeout: int = 45,
        debug: bool = False,
    ):
        self.command = list(command)
        self.port = int(port)
        self.tab = str(tab or DEFAULT_BROWSER_TAB)
        self.chrome_profile = str(chrome_profile or DEFAULT_CHROME_PROFILE)
        self.startup_url = str(startup_url or DEFAULT_URL)
        self.auto_launch = bool(auto_launch)
        self.timeout = int(timeout)
        self.debug = debug
        self.did_auto_launch = False
        self.last_launch_output = ""

    def initialize(self) -> None:
        if not self.command:
            raise MCPError(
                "unchained CLI not found. Run `./sky --setup`, install it with "
                "`uv tool install unchainedsky-cli`, or pass --unchained-cmd."
            )
        try:
            self._run(["status"], timeout_s=min(10, max(3, self.timeout)))
        except MCPError as exc:
            if self.auto_launch:
                launch_output = launch_chatgpt_with_unchained(
                    self.command,
                    port=self.port,
                    profile=self.chrome_profile,
                    url=self.startup_url,
                    timeout_s=max(20, self.timeout),
                )
                self.did_auto_launch = True
                self.last_launch_output = str(launch_output or "").strip()
                self._run(["status"], timeout_s=min(10, max(3, self.timeout)))
                return
            launch_hint = (
                f"Start a browser first with: {' '.join(self.command)} --port {self.port} "
                f"launch --use-profile --profile {self.chrome_profile} {self.startup_url}"
            )
            raise MCPError(f"{exc}\n{launch_hint}") from exc

    def list_tools(self) -> List[str]:
        return [
            "cdp_navigate",
            "navigate",
            "js_eval",
            "execute_js",
            "cdp_click",
            "click",
            "cdp_type",
            "type_text",
            "ddm",
        ]

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        tool_name = str(name or "").strip().lower()
        args = dict(arguments or {})
        if tool_name in {"cdp_navigate", "navigate"}:
            url = str(args.get("url") or args.get("target_url") or "").strip()
            if not url:
                raise MCPError("navigate requires a url argument")
            stdout_text = self._run(["navigate", url])
            return build_text_tool_result(stdout_text)

        if tool_name in {"js_eval", "execute_js"}:
            expression = (
                args.get("expression")
                or args.get("script")
                or args.get("js")
                or args.get("code")
            )
            if expression is None:
                raise MCPError("js tool requires an expression argument")
            stdout_text = self._run(["js", str(expression)])
            return build_text_tool_result(stdout_text)

        if tool_name in {"cdp_click", "click"}:
            selector = str(args.get("selector") or "").strip()
            if selector:
                stdout_text = self._run(["click", "--selector", selector])
                return build_text_tool_result(stdout_text)
            if "x" not in args or "y" not in args:
                raise MCPError("click requires x and y coordinates")
            try:
                x = int(args.get("x"))
                y = int(args.get("y"))
            except (TypeError, ValueError) as exc:
                raise MCPError("click coordinates must be integers") from exc
            stdout_text = self._run(["click", "--x", str(x), "--y", str(y)])
            return build_text_tool_result(stdout_text)

        if tool_name in {"cdp_type", "type_text"}:
            if "text" not in args:
                raise MCPError("type tool requires a text argument")
            text = str(args.get("text") or "")
            if text == "\n":
                stdout_text = self._run(["press_enter"])
            else:
                stdout_text = self._run(["type", text])
            return build_text_tool_result(stdout_text)

        if tool_name == "ddm":
            raw_flags = str(args.get("flags") or "").strip()
            try:
                ddm_flags = shlex.split(raw_flags) if raw_flags else []
            except ValueError as exc:
                raise MCPError(f"invalid ddm flags: {exc}") from exc
            stdout_text = self._run(["ddm", *ddm_flags], include_tab=not any(flag == "--tabs" for flag in ddm_flags))
            return build_text_tool_result(stdout_text)

        raise MCPError(f"Unsupported local tool: {name}")

    def _run(
        self,
        argv: Sequence[str],
        timeout_s: Optional[int] = None,
        include_tab: bool = True,
    ) -> str:
        command = list(self.command)
        command.extend(["--port", str(self.port)])
        if include_tab:
            command.extend(["--tab", self.tab])
        command.extend(str(part) for part in argv)
        if self.debug:
            rendered = " ".join(shlex.quote(part) for part in command)
            print(f"[debug] local-cli {rendered}", file=sys.stderr)
        try:
            proc = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=max(3, int(timeout_s or self.timeout)),
                check=False,
            )
        except FileNotFoundError as exc:
            raise MCPError(
                "unchained CLI not found. Run `./sky --setup`, install it with "
                "`uv tool install unchainedsky-cli`, or pass --unchained-cmd."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise MCPError(f"unchained command timed out after {int(timeout_s or self.timeout)}s") from exc

        stdout_text = str(proc.stdout or "")
        stderr_text = str(proc.stderr or "")
        if int(proc.returncode) != 0:
            detail = stderr_text.strip() or stdout_text.strip() or f"exit code {proc.returncode}"
            raise MCPError(detail)
        return stdout_text


def install_python_tool_with_uv(
    uv_cmd: Sequence[str],
    package: str,
    timeout_s: int = DEFAULT_LOCAL_SETUP_TIMEOUT,
) -> None:
    if not uv_cmd:
        raise MCPError(
            "uv is not installed. Install uv first, then rerun `./sky --setup`."
        )
    command = list(uv_cmd) + [
        "tool",
        "install",
        "--force",
        "--python",
        "3.10",
        str(package),
    ]
    try:
        proc = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(30, int(timeout_s)),
            check=False,
        )
    except FileNotFoundError as exc:
        raise MCPError(
            "uv is not installed. Install uv first, then rerun `./sky --setup`."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise MCPError(f"uv tool install timed out after {int(timeout_s)}s") from exc
    if int(proc.returncode) != 0:
        detail = str(proc.stderr or proc.stdout or "").strip() or f"exit code {proc.returncode}"
        raise MCPError(f"uv tool install failed: {detail}")


def install_unchained_with_uv(
    uv_cmd: Sequence[str],
    timeout_s: int = DEFAULT_LOCAL_SETUP_TIMEOUT,
) -> None:
    install_python_tool_with_uv(uv_cmd=uv_cmd, package="unchainedsky-cli", timeout_s=timeout_s)


def install_pyreplab_with_uv(
    uv_cmd: Sequence[str],
    timeout_s: int = DEFAULT_LOCAL_SETUP_TIMEOUT,
) -> None:
    install_python_tool_with_uv(uv_cmd=uv_cmd, package="pyreplab", timeout_s=timeout_s)


def ensure_local_setup_tooling(
    uv_cmd: Sequence[str],
    *,
    unchained_cmd: Optional[str] = None,
    pyreplab_cmd: Optional[str] = None,
    timeout_s: int = DEFAULT_LOCAL_SETUP_TIMEOUT,
) -> Tuple[List[str], Optional[List[str]], bool, List[str]]:
    resolved_unchained_cmd = resolve_unchained_command(unchained_cmd)
    resolved_pyreplab_cmd = resolve_pyreplab_command(pyreplab_cmd)
    installed_now = False
    warnings: List[str] = []

    if not resolved_unchained_cmd:
        print("setup: installing unchainedsky-cli with uv")
        install_unchained_with_uv(uv_cmd, timeout_s=timeout_s)
        installed_now = True
        resolved_unchained_cmd = resolve_unchained_command(unchained_cmd)
    if not resolved_unchained_cmd:
        raise MCPError(
            "setup completed but unchained was still not found. "
            "Try `./sky --unchained-cmd ~/.local/bin/unchained`."
        )

    if not resolved_pyreplab_cmd:
        try:
            print("setup: installing pyreplab with uv")
            install_pyreplab_with_uv(uv_cmd, timeout_s=timeout_s)
            installed_now = True
        except MCPError as exc:
            warnings.append(f"setup: pyreplab install skipped: {exc}")
        resolved_pyreplab_cmd = resolve_pyreplab_command(pyreplab_cmd)
    if not resolved_pyreplab_cmd:
        warnings.append(
            "setup: pyreplab unavailable; interactive /run will fall back to local until it is installed."
        )

    return list(resolved_unchained_cmd), resolved_pyreplab_cmd, installed_now, warnings


def launch_chatgpt_with_unchained(
    unchained_cmd: Sequence[str],
    port: int,
    profile: str,
    url: str = DEFAULT_URL,
    timeout_s: int = 60,
) -> str:
    if not unchained_cmd:
        raise MCPError("unchained CLI not found after setup")
    command = list(unchained_cmd) + [
        "--port",
        str(int(port)),
        "launch",
        "--use-profile",
        "--profile",
        str(profile or DEFAULT_CHROME_PROFILE),
        str(url or DEFAULT_URL),
    ]
    try:
        proc = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(10, int(timeout_s)),
            check=False,
        )
    except FileNotFoundError as exc:
        raise MCPError("unchained CLI not found after setup") from exc
    except subprocess.TimeoutExpired as exc:
        raise MCPError(f"unchained launch timed out after {int(timeout_s)}s") from exc
    if int(proc.returncode) != 0:
        detail = str(proc.stderr or proc.stdout or "").strip() or f"exit code {proc.returncode}"
        raise MCPError(f"unable to launch Chrome through unchained: {detail}")
    return str(proc.stdout or "").strip()


def parse_rpc_response(raw_body: str) -> Optional[Dict[str, Any]]:
    text = raw_body.strip()
    if not text:
        return None

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    sse_events = parse_sse_json_events(raw_body)
    for event in reversed(sse_events):
        if isinstance(event, dict):
            return event
    return None


def parse_sse_json_events(raw_body: str) -> List[Any]:
    events: List[Any] = []
    buffer: List[str] = []
    for line in raw_body.splitlines():
        if line.startswith("data:"):
            chunk = line[5:].lstrip()
            if chunk == "[DONE]":
                continue
            buffer.append(chunk)
            continue
        if line.strip():
            continue
        if buffer:
            joined = "\n".join(buffer)
            buffer.clear()
            try:
                events.append(json.loads(joined))
            except json.JSONDecodeError:
                events.append(joined)

    if buffer:
        joined = "\n".join(buffer)
        try:
            events.append(json.loads(joined))
        except json.JSONDecodeError:
            events.append(joined)
    return events


def with_agent_variants(
    base_variants: Sequence[Dict[str, Any]],
    agent_id: str,
) -> List[Dict[str, Any]]:
    variants: List[Dict[str, Any]] = []
    seen: set = set()
    for base in base_variants:
        with_agent = dict(base)
        if agent_id:
            with_agent["agent_id"] = agent_id
        for candidate in (with_agent, dict(base)):
            key = tuple(sorted((k, str(v)) for k, v in candidate.items()))
            if key in seen:
                continue
            seen.add(key)
            variants.append(candidate)
    return variants


def select_tool_candidates(
    preferred: Sequence[str],
    available: Sequence[str],
    explicit: Optional[str],
    keyword: str,
) -> List[str]:
    if explicit:
        return [explicit]

    candidates: List[str] = []
    if available:
        for tool in preferred:
            if tool in available and tool not in candidates:
                candidates.append(tool)
        for tool in available:
            if keyword in tool.lower() and tool not in candidates:
                candidates.append(tool)
    else:
        candidates.extend(preferred)

    if not candidates:
        candidates.extend(preferred)
    return candidates


def call_tool_variants(
    client: MCPClient,
    tool_candidates: Iterable[str],
    argument_variants: Iterable[Dict[str, Any]],
    label: str,
) -> Tuple[str, Dict[str, Any]]:
    errors: List[str] = []
    for tool_name in tool_candidates:
        for arguments in argument_variants:
            try:
                return tool_name, client.call_tool(tool_name, arguments)
            except MCPError as exc:
                errors.append(f"{tool_name} {list(arguments.keys())}: {exc}")
    joined = "; ".join(errors[-6:])
    raise MCPError(f"Unable to call {label}. Recent errors: {joined}")


def extract_text(result: Dict[str, Any]) -> str:
    if not isinstance(result, dict):
        return json.dumps(result, indent=2)

    content = result.get("content")
    if isinstance(content, list):
        texts: List[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str):
                texts.append(text)
        if texts:
            return "\n".join(texts)

    if "structuredContent" in result:
        return json.dumps(result["structuredContent"], indent=2)

    return json.dumps(result, indent=2)


def build_prompt_expression(prompt: str, submit: bool) -> str:
    prompt_json = json.dumps(prompt)
    submit_json = "true" if submit else "false"
    return f"""
(() => {{
  const promptText = {prompt_json};
  const shouldSubmit = {submit_json};

  function visible(el) {{
    if (!el) return false;
    const style = window.getComputedStyle(el);
    if (!style || style.display === "none" || style.visibility === "hidden") return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }}

  function encode(out) {{
    try {{ return JSON.stringify(out); }} catch (_) {{ return String(out); }}
  }}

  function isDisabled(el) {{
    if (!el) return true;
    if ("disabled" in el && !!el.disabled) return true;
    return String(el.getAttribute("aria-disabled") || "").toLowerCase() === "true";
  }}

  function currentInputText(el) {{
    if (!el) return "";
    if ("value" in el) return String(el.value || "");
    return String(el.innerText || el.textContent || "");
  }}

  function setTextValue(el, text) {{
    if (!el) return;
    if ("value" in el) {{
      const proto = Object.getPrototypeOf(el);
      const desc = proto ? Object.getOwnPropertyDescriptor(proto, "value") : null;
      if (desc && typeof desc.set === "function") {{
        desc.set.call(el, text);
      }} else {{
        el.value = text;
      }}
      try {{
        el.dispatchEvent(new InputEvent("input", {{ bubbles: true, inputType: "insertText", data: text }}));
      }} catch (_) {{
        el.dispatchEvent(new Event("input", {{ bubbles: true }}));
      }}
      el.dispatchEvent(new Event("change", {{ bubbles: true }}));
      return;
    }}

    const isContentEditable = String(el.getAttribute("contenteditable") || "").toLowerCase() === "true";
    if (isContentEditable) {{
      try {{
        el.focus();
      }} catch (_) {{}}
      try {{
        const selection = window.getSelection();
        if (selection) {{
          const range = document.createRange();
          range.selectNodeContents(el);
          selection.removeAllRanges();
          selection.addRange(range);
        }}
      }} catch (_) {{}}

      let inserted = false;
      try {{
        inserted = document.execCommand("insertText", false, text);
      }} catch (_) {{}}
      if (!inserted) {{
        try {{
          el.dispatchEvent(new InputEvent("beforeinput", {{
            bubbles: true,
            cancelable: true,
            inputType: "insertText",
            data: text
          }}));
        }} catch (_) {{}}
        el.textContent = text;
      }}
      try {{
        el.dispatchEvent(new InputEvent("input", {{
          bubbles: true,
          inputType: "insertText",
          data: text
        }}));
      }} catch (_) {{
        el.dispatchEvent(new Event("input", {{ bubbles: true }}));
      }}
      el.dispatchEvent(new Event("change", {{ bubbles: true }}));
      return;
    }}

    el.textContent = text;
    try {{
      el.dispatchEvent(new InputEvent("input", {{ bubbles: true, inputType: "insertText", data: text }}));
    }} catch (_) {{
      el.dispatchEvent(new Event("input", {{ bubbles: true }}));
    }}
    el.dispatchEvent(new Event("change", {{ bubbles: true }}));
  }}

  function dispatchSubmitClick(el) {{
    if (!el) return;
    try {{
      el.dispatchEvent(new PointerEvent("pointerdown", {{ bubbles: true, cancelable: true }}));
      el.dispatchEvent(new MouseEvent("mousedown", {{ bubbles: true, cancelable: true }}));
      el.dispatchEvent(new PointerEvent("pointerup", {{ bubbles: true, cancelable: true }}));
      el.dispatchEvent(new MouseEvent("mouseup", {{ bubbles: true, cancelable: true }}));
    }} catch (_) {{}}
    try {{
      el.dispatchEvent(new MouseEvent("click", {{ bubbles: true, cancelable: true }}));
    }} catch (_) {{}}
    try {{
      el.click();
    }} catch (_) {{}}
  }}

  const inputSelectors = [
    'textarea[placeholder*="message" i]',
    'textarea',
    '[contenteditable="true"][role="textbox"]',
    'div[role="textbox"][contenteditable="true"]',
    'div[contenteditable="true"]'
  ];

  let input = null;
  for (const sel of inputSelectors) {{
    const candidates = Array.from(document.querySelectorAll(sel));
    input = candidates.find(visible);
    if (input) break;
  }}

  if (!input) {{
    return encode({{ ok: false, error: "no_visible_chat_input" }});
  }}

  input.focus();
  setTextValue(input, promptText);

  if (!shouldSubmit) {{
    return encode({{ ok: true, submitted: false }});
  }}

  const submitCandidates = [];
  for (const node of Array.from(document.querySelectorAll("button,[role='button']"))) {{
    const label = String(
      node.getAttribute("aria-label") ||
      node.innerText ||
      node.getAttribute("title") ||
      ""
    ).toLowerCase();
    if (!/(send prompt|send|submit)/.test(label)) continue;
    if (!submitCandidates.includes(node)) {{
      submitCandidates.push(node);
    }}
  }}

  let submitButton = null;
  let fallbackSubmitButton = null;
  for (const candidate of submitCandidates) {{
    if (!visible(candidate)) continue;
    if (!fallbackSubmitButton) {{
      fallbackSubmitButton = candidate;
    }}
    if (!isDisabled(candidate)) {{
      submitButton = candidate;
      break;
    }}
  }}
  if (!submitButton) {{
    submitButton = fallbackSubmitButton;
  }}

  if (submitButton) {{
    dispatchSubmitClick(submitButton);
    return encode({{
      ok: true,
      submitted: true,
      mode: "button_click",
      text_after: currentInputText(input),
    }});
  }}

  const form = input.closest("form");
  let requestedSubmit = false;
  if (form) {{
    try {{
      if (typeof form.requestSubmit === "function") {{
        form.requestSubmit();
      }} else {{
        form.dispatchEvent(new Event("submit", {{ bubbles: true, cancelable: true }}));
      }}
      requestedSubmit = true;
    }} catch (_) {{}}
  }}

  input.dispatchEvent(new KeyboardEvent("keydown", {{
    key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true
  }}));
  input.dispatchEvent(new KeyboardEvent("keypress", {{
    key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true
  }}));
  input.dispatchEvent(new KeyboardEvent("keyup", {{
    key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true
  }}));

  const after = currentInputText(input);
  const maybeSubmitted = after.trim() === "";

  return encode({{
    ok: maybeSubmitted,
    submitted: maybeSubmitted,
    mode: requestedSubmit ? "form_submit_or_enter" : "enter_key_only",
    error: maybeSubmitted ? null : "submit_not_confirmed",
    text_after: after
  }});
}})()
""".strip()


def parse_dispatch_status_text(text: str) -> Optional[Dict[str, Any]]:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def build_input_state_expression() -> str:
    return """
(() => {
  function visible(el) {
    if (!el) return false;
    const style = window.getComputedStyle(el);
    if (!style || style.display === "none" || style.visibility === "hidden") return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }

  function currentInputText(el) {
    if (!el) return "";
    if ("value" in el) return String(el.value || "");
    return String(el.textContent || "");
  }

  const inputSelectors = [
    'textarea[placeholder*="message" i]',
    'textarea',
    '[contenteditable="true"][role="textbox"]',
    'div[role="textbox"][contenteditable="true"]',
    'div[contenteditable="true"]'
  ];

  let input = null;
  for (const sel of inputSelectors) {
    const candidates = Array.from(document.querySelectorAll(sel));
    input = candidates.find(visible);
    if (input) break;
  }

  if (!input) {
    return JSON.stringify({ ok: false, text_after: "" });
  }
  return JSON.stringify({
    ok: true,
    text_after: currentInputText(input),
    focused: document.activeElement === input
  });
})()
""".strip()


def build_send_button_state_expression() -> str:
    return """
(() => {
  function visible(el) {
    if (!el) return false;
    const style = window.getComputedStyle(el);
    if (!style || style.display === "none" || style.visibility === "hidden") return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }

  function isDisabled(el) {
    if (!el) return true;
    if ("disabled" in el && !!el.disabled) return true;
    return String(el.getAttribute("aria-disabled") || "").toLowerCase() === "true";
  }

  const send = Array.from(document.querySelectorAll("button,[role='button']")).find((node) => {
    if (!visible(node)) return false;
    const label = String(
      node.getAttribute("aria-label") ||
      node.innerText ||
      node.getAttribute("title") ||
      ""
    ).toLowerCase();
    return /(send prompt|send|submit)/.test(label);
  });

  if (!send) {
    return JSON.stringify({ ok: false, visible: false });
  }

  const rect = send.getBoundingClientRect();
  return JSON.stringify({
    ok: true,
    visible: true,
    enabled: !isDisabled(send),
    label: String(send.getAttribute("aria-label") || send.innerText || send.getAttribute("title") || ""),
    x: Math.round(rect.left + rect.width / 2),
    y: Math.round(rect.top + rect.height / 2)
  });
})()
""".strip()


def build_network_request_spy_install_expression() -> str:
    return """
(() => {
  if (window.__skyNetworkSpyInstalled) {
    return JSON.stringify({ ok: true, installed: true, already: true });
  }

  window.__skyNetworkSpyInstalled = true;
  window.__skyNetworkSpyLog = [];

  function push(entry) {
    try {
      const row = Object.assign({ ts: Date.now() }, entry || {});
      window.__skyNetworkSpyLog.push(row);
      if (window.__skyNetworkSpyLog.length > 40) {
        window.__skyNetworkSpyLog.splice(0, window.__skyNetworkSpyLog.length - 40);
      }
    } catch (_) {}
  }

  const originalFetch = window.fetch;
  if (typeof originalFetch === "function") {
    window.fetch = function(...args) {
      const input = args.length ? args[0] : null;
      const init = args.length > 1 ? args[1] : null;
      let url = "";
      try {
        url = typeof input === "string" ? input : String((input && input.url) || "");
      } catch (_) {}
      let body = "";
      try {
        body = init && typeof init.body === "string" ? init.body : "";
      } catch (_) {}
      push({
        kind: "fetch",
        phase: "request",
        method: String((init && init.method) || "GET"),
        url: url,
        body: body.slice(0, 400)
      });
      const result = originalFetch.apply(this, args);
      if (result && typeof result.then === "function") {
        return result.then((response) => {
          push({
            kind: "fetch",
            phase: "response",
            method: String((init && init.method) || "GET"),
            url: url,
            status: Number(response && response.status || 0),
            ok: !!(response && response.ok)
          });
          return response;
        });
      }
      return result;
    };
  }

  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(method, url, ...rest) {
    this.__skySpyMethod = method;
    this.__skySpyUrl = url;
    return originalOpen.call(this, method, url, ...rest);
  };
  XMLHttpRequest.prototype.send = function(body) {
    push({
      kind: "xhr",
      phase: "request",
      method: String(this.__skySpyMethod || ""),
      url: String(this.__skySpyUrl || ""),
      body: typeof body === "string" ? body.slice(0, 400) : ""
    });
    this.addEventListener("loadend", () => {
      push({
        kind: "xhr",
        phase: "response",
        method: String(this.__skySpyMethod || ""),
        url: String(this.__skySpyUrl || ""),
        status: Number(this.status || 0)
      });
    }, { once: true });
    return originalSend.call(this, body);
  };

  return JSON.stringify({ ok: true, installed: true, already: false });
})()
""".strip()


def build_network_request_spy_dump_expression(limit: int = 12) -> str:
    safe_limit = max(1, int(limit))
    return f"""
(() => {{
  const rows = Array.isArray(window.__skyNetworkSpyLog) ? window.__skyNetworkSpyLog.slice(-{safe_limit}) : [];
  return JSON.stringify({{ ok: true, log: rows }});
}})()
""".strip()


def build_assistant_probe_expression() -> str:
    return """
(() => {
  function cleanText(value) {
    let text = String(value || "");
    text = text.replace(/\\r\\n/g, "\\n").replace(/\\u00a0/g, " ");
    text = text.replace(/[ \\t]+/g, " ");
    text = text.replace(/\\n{3,}/g, "\\n\\n");
    return text.trim();
  }

  function simpleHash(value) {
    let hash = 0;
    for (let i = 0; i < value.length; i++) {
      hash = ((hash << 5) - hash + value.charCodeAt(i)) | 0;
    }
    return String(hash);
  }

  function visible(el) {
    if (!el) return false;
    if (String(el.getAttribute("aria-hidden") || "").toLowerCase() === "true") return false;
    const style = window.getComputedStyle(el);
    if (!style || style.display === "none" || style.visibility === "hidden") return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }

  function pushUnique(target, value) {
    if (!value) return;
    const normalized = cleanText(value);
    if (!normalized) return;
    if (!target.includes(normalized)) target.push(normalized);
  }

  function nodeText(node) {
    if (!node) return "";
    return cleanText(node.innerText || node.textContent || "");
  }

  function assistantActionAnchors() {
    const labels = new Set([
      "copy",
      "good response",
      "bad response",
      "share",
      "more actions",
      "read aloud",
    ]);
    return Array.from(document.querySelectorAll('main button, main [role="button"]')).filter((node) => {
      if (!visible(node)) return false;
      const label = cleanText(
        node.innerText ||
        node.getAttribute("aria-label") ||
        node.getAttribute("title") ||
        ""
      ).toLowerCase();
      return labels.has(label);
    });
  }

  function extractAssistantTexts() {
    const texts = [];

    for (const node of Array.from(document.querySelectorAll('[data-message-author-role="assistant"]'))) {
      const t = nodeText(node);
      if (t.length >= 8) pushUnique(texts, t);
    }

    if (!texts.length) {
      for (const turn of Array.from(document.querySelectorAll('[data-testid^="conversation-turn-"], main article, main [role="article"]'))) {
        const explicitAssistant = turn.querySelector('[data-message-author-role="assistant"]');
        const explicitUser = turn.querySelector('[data-message-author-role="user"]');
        if (explicitUser && !explicitAssistant) continue;
        const candidate = explicitAssistant || turn;
        const t = nodeText(candidate);
        if (t.length >= 8) pushUnique(texts, t);
      }
    }

    if (!texts.length) {
      for (const anchor of assistantActionAnchors()) {
        const block = anchor.closest('[data-testid^="conversation-turn-"], article, section, div');
        const t = nodeText(block || anchor.parentElement || anchor);
        if (t.length >= 8) pushUnique(texts, t);
      }
    }

    return texts;
  }

  function countAssistantShells() {
    return Array.from(document.querySelectorAll('[data-message-author-role="assistant"]')).filter((node) => {
      const el = node instanceof Element ? node : null;
      return visible(el);
    }).length;
  }

  function countResponseNavButtons() {
    return Array.from(document.querySelectorAll('button, [role="button"]')).filter((node) => {
      const el = node instanceof Element ? node : null;
      if (!visible(el)) return false;
      const label = cleanText(
        (el && (el.innerText || el.getAttribute("aria-label") || el.getAttribute("title"))) || ""
      ).toLowerCase();
      return label === "previous response" || label === "next response";
    }).length;
  }

  function extractUserTexts() {
    const texts = [];

    for (const node of Array.from(document.querySelectorAll('[data-message-author-role="user"]'))) {
      const t = nodeText(node);
      if (t.length >= 1) pushUnique(texts, t);
    }

    if (!texts.length) {
      for (const turn of Array.from(document.querySelectorAll('[data-testid^="conversation-turn-"], main article, main [role="article"]'))) {
        const explicitUser = turn.querySelector('[data-message-author-role="user"]');
        if (!explicitUser) continue;
        const t = nodeText(explicitUser);
        if (t.length >= 1) pushUnique(texts, t);
      }
    }

    return texts;
  }

  function isGenerating() {
    const stopSelectors = [
      'button[data-testid*="stop" i]',
      'button[aria-label*="stop" i]',
      'button[title*="stop" i]',
      '[aria-label*="stop generating" i]'
    ];
    for (const sel of stopSelectors) {
      const candidates = Array.from(document.querySelectorAll(sel));
      if (candidates.some((el) => visible(el))) {
        return true;
      }
    }
    return false;
  }

  const assistantTexts = extractAssistantTexts();
  const assistantShellCount = countAssistantShells();
  const responseNavCount = countResponseNavButtons();
  const userTexts = extractUserTexts();
  const latestAssistant = assistantTexts.length ? assistantTexts[assistantTexts.length - 1] : "";
  const latestUser = userTexts.length ? userTexts[userTexts.length - 1] : "";

  return JSON.stringify({
    ok: true,
    assistant_count: assistantTexts.length,
    assistant_shell_count: assistantShellCount,
    response_nav_count: responseNavCount,
    empty_assistant_shell: assistantShellCount > 0 && assistantTexts.length === 0,
    latest_text: latestAssistant,
    latest_hash: simpleHash(latestAssistant),
    user_count: userTexts.length,
    latest_user_text: latestUser,
    latest_user_hash: simpleHash(latestUser),
    generating: isGenerating(),
    extractor: "probe_v1"
  });
})()
""".strip()


def build_assistant_snapshot_expression() -> str:
    return """
(() => {
  const DROP_LINE_SET = new Set([
    "run",
    "copy",
    "copy code",
    "share",
    "good response",
    "bad response",
    "more actions",
    "edit message",
    "open conversation options",
    "read aloud",
  ]);

  const LANGUAGE_LINE_SET = new Set([
    "python",
    "bash",
    "shell",
    "javascript",
    "typescript",
    "json",
    "csv",
    "sql",
    "plain text",
  ]);

  function cleanText(value) {
    let text = String(value || "");
    text = text.replace(/\\r\\n/g, "\\n").replace(/\\u00a0/g, " ");
    text = text.replace(/[ \\t]+\\n/g, "\\n");
    text = text.replace(/\\n{3,}/g, "\\n\\n");
    return text.trim();
  }

  function looksLikeCodeLine(value) {
    const line = String(value || "").trim();
    if (!line) return false;
    if (/^[\\[\\]{}();,:.=<>+\\-*/%|&!~]+$/.test(line)) return true;
    if (/^(from|import|def|class|return|for|while|if|elif|else|print|const|let|var|function)\\b/.test(line)) return true;
    if (/[:=(){}\\[\\];]/.test(line)) return true;
    return false;
  }

  function normalizeRenderedLines(value) {
    const lines = String(value || "")
      .replace(/\\r\\n/g, "\\n")
      .replace(/\\u00a0/g, " ")
      .split("\\n");
    const filtered = [];
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i].replace(/[ \\t]+$/g, "");
      const trimmed = line.trim();
      const lower = trimmed.toLowerCase();
      if (!trimmed) {
        filtered.push("");
        continue;
      }
      if (DROP_LINE_SET.has(lower)) {
        continue;
      }
      const languageTagMatch = lower.match(/^(python|bash|shell|javascript|typescript|json|csv|sql|plain text)[\\s:.-]*$/);
      if (LANGUAGE_LINE_SET.has(lower) || !!languageTagMatch) {
        const prev = i > 0 ? lines[i - 1].trim() : "";
        const next = i + 1 < lines.length ? lines[i + 1].trim() : "";
        if (looksLikeCodeLine(prev) || looksLikeCodeLine(next)) {
          continue;
        }
      }
      filtered.push(line);
    }
    let joined = filtered.join("\\n");
    // Merge list markers that can be split by nested block elements.
    joined = joined.replace(/(^|\\n)([-*]|\\d+\\.)\\s*\\n(?=\\S)/g, "$1$2 ");
    // Preserve spacing between adjacent inline fragments that lost whitespace.
    joined = joined.replace(/([A-Za-z0-9\\)])\\(/g, "$1 (");
    joined = joined.replace(/\\n{3,}/g, "\\n\\n");
    return cleanText(joined);
  }

  function simpleHash(value) {
    let hash = 0;
    for (let i = 0; i < value.length; i++) {
      hash = ((hash << 5) - hash + value.charCodeAt(i)) | 0;
    }
    return String(hash);
  }

  function isVisibleElement(el) {
    if (!el) return false;
    if (String(el.getAttribute("aria-hidden") || "").toLowerCase() === "true") return false;
    const style = window.getComputedStyle(el);
    if (!style) return false;
    if (style.display === "none" || style.visibility === "hidden") return false;
    if (Number(style.opacity || "1") === 0) return false;
    const rect = el.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0) return true;
    return String(el.textContent || "").trim().length > 0;
  }

  function shouldSkipElement(el) {
    if (!el) return true;
    const tag = String(el.tagName || "").toLowerCase();
    if (["script", "style", "noscript", "svg", "path", "button", "nav", "aside", "footer"].includes(tag)) {
      return true;
    }
    const role = String(el.getAttribute("role") || "").toLowerCase();
    if (["button", "menu", "toolbar", "tooltip"].includes(role)) return true;
    const className = String(el.className || "").toLowerCase();
    if (className.includes("sr-only") || className.includes("visually-hidden")) return true;
    const testId = String(el.getAttribute("data-testid") || "").toLowerCase();
    if (testId.includes("copy") || testId.includes("thumb") || testId.includes("toolbar")) return true;
    const label = String(el.getAttribute("aria-label") || el.getAttribute("title") || "").toLowerCase();
    if (!label) return false;
    return [
      "copy",
      "share",
      "good response",
      "bad response",
      "more actions",
      "edit message",
      "conversation options",
      "read aloud",
    ].some((needle) => label.includes(needle));
  }

  function isBlockElement(el) {
    if (!el) return false;
    const tag = String(el.tagName || "").toLowerCase();
    if (
      [
        "article",
        "section",
        "div",
        "p",
        "pre",
        "blockquote",
        "ul",
        "ol",
        "li",
        "table",
        "thead",
        "tbody",
        "tr",
        "td",
        "th",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
      ].includes(tag)
    ) {
      return true;
    }
    const style = window.getComputedStyle(el);
    if (!style) return false;
    return ["block", "list-item", "table", "flex", "grid", "flow-root"].includes(style.display);
  }

  function renderWithLayout(node) {
    if (!node) return "";
    const parts = [];
    let pendingSpace = false;

    function ensureNewlines(minCount) {
      if (!parts.length) return;
      while (parts.length && /[ \\t]+$/.test(parts[parts.length - 1])) {
        parts[parts.length - 1] = parts[parts.length - 1].replace(/[ \\t]+$/g, "");
        if (!parts[parts.length - 1]) parts.pop();
      }
      const tail = parts.join("").match(/\\n*$/);
      const existing = tail ? tail[0].length : 0;
      if (existing < minCount) {
        parts.push("\\n".repeat(minCount - existing));
      }
    }

    function pushInlineText(raw) {
      if (!raw) return;
      const collapsed = String(raw).replace(/[ \\t\\f\\v]+/g, " ");
      if (!collapsed.trim()) {
        pendingSpace = pendingSpace || /\\s/.test(raw);
        return;
      }
      let chunk = collapsed.trim();
      if (parts.length) {
        const prev = parts[parts.length - 1];
        const needsGapFromBoundary =
          !!prev &&
          !prev.endsWith("\\n") &&
          !prev.endsWith(" ") &&
          /[A-Za-z0-9\\)]$/.test(prev) &&
          /^[A-Za-z0-9\\(]/.test(chunk);
        if ((pendingSpace || needsGapFromBoundary) && prev && !prev.endsWith("\\n") && !prev.endsWith(" ")) {
          chunk = " " + chunk;
        }
      }
      parts.push(chunk);
      pendingSpace = /\\s$/.test(raw);
    }

    function walk(current) {
      if (!current) return;

      if (current.nodeType === Node.TEXT_NODE) {
        const parent = current.parentElement;
        if (!parent || !isVisibleElement(parent) || shouldSkipElement(parent)) return;
        pushInlineText(current.nodeValue || "");
        return;
      }

      if (current.nodeType !== Node.ELEMENT_NODE) return;
      const el = current;
      if (!isVisibleElement(el) || shouldSkipElement(el)) return;

      const tag = String(el.tagName || "").toLowerCase();
      if (tag === "br") {
        ensureNewlines(1);
        pendingSpace = false;
        return;
      }

      if (tag === "pre") {
        const codeText = String(el.innerText || el.textContent || "")
          .replace(/\\r\\n/g, "\\n")
          .replace(/\\u00a0/g, " ")
          .replace(/\\n{3,}/g, "\\n\\n")
          .trimEnd();
        if (codeText) {
          ensureNewlines(2);
          parts.push(codeText);
          ensureNewlines(2);
        }
        pendingSpace = false;
        return;
      }

      const block = isBlockElement(el);
      if (block) ensureNewlines(1);

      if (/^h[1-6]$/.test(tag)) {
        const level = Number(tag.slice(1)) || 1;
        parts.push("#".repeat(Math.max(1, Math.min(6, level))) + " ");
      } else if (tag === "li") {
        parts.push("- ");
      } else if (tag === "blockquote") {
        parts.push("> ");
      }

      for (const child of Array.from(el.childNodes)) {
        walk(child);
      }

      if (block) ensureNewlines(1);
    }

    walk(node);
    return normalizeRenderedLines(parts.join(""));
  }

  function renderWithLegacyClone(node) {
    if (!node) return "";
    const clone = node.cloneNode(true);
    clone.querySelectorAll('button, [role="button"], nav, aside, footer, svg, path').forEach((el) => el.remove());
    clone.querySelectorAll('[data-testid*="copy" i], [data-testid*="thumb" i]').forEach((el) => el.remove());
    const text = String(clone.innerText || clone.textContent || "");
    return normalizeRenderedLines(text);
  }

  function nodeToMarkdownLikeText(node) {
    const layoutText = renderWithLayout(node);
    const legacyText = renderWithLegacyClone(node);
    if (!layoutText) return legacyText;
    if (!legacyText) return layoutText;

    // Prefer the richer capture, but avoid obvious over-capture.
    const layoutLines = layoutText.split("\\n").length;
    const legacyLines = legacyText.split("\\n").length;
    const layoutScore = layoutText.length + layoutLines * 2;
    const legacyScore = legacyText.length + legacyLines;
    if (layoutScore >= legacyScore * 0.8 && layoutScore <= legacyScore * 2.4) {
      return layoutText;
    }
    return legacyText;
  }

  function pushUnique(target, value) {
    if (!value) return;
    const normalized = value.trim();
    if (!normalized) return;
    if (!target.includes(normalized)) target.push(normalized);
  }

  function assistantActionAnchors() {
    const labels = new Set([
      "copy",
      "good response",
      "bad response",
      "share",
      "more actions",
      "read aloud",
    ]);
    return Array.from(document.querySelectorAll('main button, main [role="button"]')).filter((node) => {
      if (!isVisibleElement(node)) return false;
      const label = cleanText(
        node.innerText ||
        node.getAttribute("aria-label") ||
        node.getAttribute("title") ||
        ""
      ).toLowerCase();
      return labels.has(label);
    });
  }

  function extractAssistantTexts() {
    const texts = [];

    // 1) direct assistant role nodes
    for (const node of Array.from(document.querySelectorAll('[data-message-author-role="assistant"]'))) {
      const message = nodeToMarkdownLikeText(node);
      if (message && message.length >= 8) pushUnique(texts, message);
    }

    // 2) conversation turn containers across different chat UI variants
    const turnContainers = Array.from(
      document.querySelectorAll('[data-testid^="conversation-turn-"], main article, main [role="article"]')
    );
    for (const turn of turnContainers) {
      const explicitAssistant = turn.querySelector('[data-message-author-role="assistant"]');
      const explicitUser = turn.querySelector('[data-message-author-role="user"]');
      if (explicitUser && !explicitAssistant) continue;

      const candidate =
        explicitAssistant ||
        turn.querySelector('.markdown, [class*="markdown"], pre, p, li');
      const message = nodeToMarkdownLikeText(candidate || turn);
      if (message && message.length >= 8) pushUnique(texts, message);
    }

    // 3) copy-button anchored blocks often map to assistant responses
    for (const anchor of assistantActionAnchors()) {
      const block = anchor.closest('[data-testid^="conversation-turn-"], article, section, div');
      const message = nodeToMarkdownLikeText(block || anchor.parentElement || anchor);
      if (message && message.length >= 8) pushUnique(texts, message);
    }

    // 4) last-resort fallback for plain-text rendering variants
    if (!texts.length) {
      const main = document.querySelector('main');
      if (main) {
        const chunks = Array.from(
          main.querySelectorAll('.markdown, [class*="markdown"], pre, p, li')
        )
          .map((node) => nodeToMarkdownLikeText(node))
          .filter((txt) => txt && txt.length >= 20);
        if (chunks.length) {
          pushUnique(texts, chunks[chunks.length - 1]);
        }
      }
    }
    return texts;
  }

  function extractUserTexts() {
    const texts = [];

    for (const node of Array.from(document.querySelectorAll('[data-message-author-role="user"]'))) {
      const t = nodeToMarkdownLikeText(node);
      if (t && t.length >= 1) pushUnique(texts, t);
    }

    if (!texts.length) {
      const turnContainers = Array.from(
        document.querySelectorAll('[data-testid^="conversation-turn-"], main article, main [role="article"]')
      );
      for (const turn of turnContainers) {
        const explicitUser = turn.querySelector('[data-message-author-role="user"]');
        if (!explicitUser) continue;
        const t = nodeToMarkdownLikeText(explicitUser);
        if (t && t.length >= 1) pushUnique(texts, t);
      }
    }
    return texts;
  }

  function isGenerating() {
    const stopSelectors = [
      'button[data-testid*="stop" i]',
      'button[aria-label*="stop" i]',
      'button[title*="stop" i]',
      '[aria-label*="stop generating" i]'
    ];
    for (const sel of stopSelectors) {
      const candidates = Array.from(document.querySelectorAll(sel));
      if (candidates.some((el) => {
        const style = window.getComputedStyle(el);
        if (!style) return false;
        const hidden = style.display === "none" || style.visibility === "hidden";
        const rect = el.getBoundingClientRect();
        return !hidden && rect.width > 0 && rect.height > 0;
      })) {
        return true;
      }
    }
    return false;
  }

  const texts = extractAssistantTexts();
  const userTexts = extractUserTexts();
  const cappedTexts = texts.slice(-8);
  const latest = cappedTexts.length ? cappedTexts[cappedTexts.length - 1] : "";
  const latestUser = userTexts.length ? userTexts[userTexts.length - 1] : "";
  const assistantHashes = cappedTexts.map((item) => simpleHash(item));
  return JSON.stringify({
    ok: true,
    assistant_count: texts.length,
    assistant_texts: cappedTexts,
    assistant_hashes: assistantHashes,
    latest_text: latest,
    latest_hash: simpleHash(latest),
    user_count: userTexts.length,
    latest_user_text: latestUser,
    latest_user_hash: simpleHash(latestUser),
    generating: isGenerating(),
    extractor: "v8_layout_v1"
  });
})()
""".strip()


def call_js_expression(
    client: MCPClient,
    js_tools: Sequence[str],
    agent_id: str,
    expression: str,
    label: str,
) -> Tuple[str, Dict[str, Any], str, Optional[Dict[str, Any]]]:
    context_mode = current_foreground_browser_context_mode()
    browser_app: Optional[str] = None
    restore_app: Optional[str] = None
    if context_mode == "pulse":
        browser_app = browser_application_name_from_env()
        original_frontmost_app = current_frontmost_application_name() or terminal_application_name_from_env()
        if original_frontmost_app and str(original_frontmost_app).strip() != browser_app:
            restore_app = str(original_frontmost_app).strip()
        if not original_frontmost_app or str(original_frontmost_app).strip() != browser_app:
            activate_application(browser_app)
    try:
        base_args: List[Dict[str, Any]] = [{"expression": expression}]
        if any(tool_name.lower() == "execute_js" for tool_name in js_tools):
            base_args.extend([{"script": expression}, {"js": expression}, {"code": expression}])
        js_args = with_agent_variants(base_args, agent_id=agent_id)
        tool, result = call_tool_variants(client, js_tools, js_args, label)
        result_text = extract_text(result).strip()
        status = parse_dispatch_status_text(result_text)
        return tool, result, result_text, status
    finally:
        if browser_app and restore_app and restore_app != browser_app:
            activate_application(restore_app)


def read_assistant_probe(
    client: MCPClient,
    agent_id: str,
    js_tools: Sequence[str],
    label: str,
) -> Dict[str, Any]:
    try:
        _, _, probe_text, probe = call_js_expression(
            client=client,
            js_tools=js_tools,
            agent_id=agent_id,
            expression=build_assistant_probe_expression(),
            label=label,
        )
    except MCPError:
        return {}

    if probe is None:
        probe = parse_dispatch_status_text(probe_text) or {}
    if not isinstance(probe, dict):
        return {}
    return probe


def read_visible_input_text(
    client: MCPClient,
    agent_id: str,
    js_tools: Sequence[str],
) -> str:
    try:
        _, _, state_text, state = call_js_expression(
            client=client,
            js_tools=js_tools,
            agent_id=agent_id,
            expression=build_input_state_expression(),
            label="input state probe",
        )
    except MCPError:
        return ""

    if state is None:
        state = parse_dispatch_status_text(state_text)
    if not isinstance(state, dict):
        return ""
    return str(state.get("text_after") or "")


def read_visible_send_button_state(
    client: MCPClient,
    agent_id: str,
    js_tools: Sequence[str],
) -> Dict[str, Any]:
    try:
        _, _, state_text, state = call_js_expression(
            client=client,
            js_tools=js_tools,
            agent_id=agent_id,
            expression=build_send_button_state_expression(),
            label="send button probe",
        )
    except MCPError:
        return {}

    if state is None:
        state = parse_dispatch_status_text(state_text)
    if not isinstance(state, dict):
        return {}
    return state


def wait_for_visible_send_button_state(
    client: MCPClient,
    agent_id: str,
    js_tools: Sequence[str],
    timeout_s: float = DEFAULT_COMPOSER_SETTLE_SECONDS,
    poll_interval_s: float = 0.1,
) -> Dict[str, Any]:
    deadline = time.time() + max(0.0, float(timeout_s))
    last_state: Dict[str, Any] = {}
    while True:
        last_state = read_visible_send_button_state(
            client=client,
            agent_id=agent_id,
            js_tools=js_tools,
        )
        if bool(last_state.get("visible")):
            return last_state
        if time.time() >= deadline:
            return last_state
        time.sleep(max(0.05, float(poll_interval_s)))


def install_page_network_request_spy(
    client: MCPClient,
    agent_id: str,
    js_tools: Sequence[str],
) -> None:
    try:
        call_js_expression(
            client=client,
            js_tools=js_tools,
            agent_id=agent_id,
            expression=build_network_request_spy_install_expression(),
            label="network spy install",
        )
    except MCPError:
        return


def read_page_network_request_spy_log(
    client: MCPClient,
    agent_id: str,
    js_tools: Sequence[str],
    limit: int = 12,
) -> List[Dict[str, Any]]:
    try:
        _, _, state_text, state = call_js_expression(
            client=client,
            js_tools=js_tools,
            agent_id=agent_id,
            expression=build_network_request_spy_dump_expression(limit=limit),
            label="network spy dump",
        )
    except MCPError:
        return []

    if state is None:
        state = parse_dispatch_status_text(state_text)
    if not isinstance(state, dict):
        return []
    rows = state.get("log")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def capture_final_assistant_text(
    client: MCPClient,
    agent_id: str,
    js_tools: Sequence[str],
    fallback_text: Optional[str] = None,
    baseline_hash: str = "",
) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    try:
        _, _, snapshot_text, snapshot = call_js_expression(
            client=client,
            js_tools=js_tools,
            agent_id=agent_id,
            expression=build_assistant_snapshot_expression(),
            label="assistant final view",
        )
    except MCPError:
        return (fallback_text or "").strip() or None, None

    if snapshot is None:
        snapshot = parse_dispatch_status_text(snapshot_text)
    snapshot_dict: Optional[Dict[str, Any]] = snapshot if isinstance(snapshot, dict) else None

    if isinstance(snapshot, dict):
        texts_value = snapshot.get("assistant_texts")
        hashes_value = snapshot.get("assistant_hashes")
        if isinstance(texts_value, list) and texts_value:
            text_entries = [str(item).strip() for item in texts_value if str(item).strip()]
            hash_entries: List[str] = []
            if isinstance(hashes_value, list):
                hash_entries = [str(item or "") for item in hashes_value][: len(text_entries)]
            if baseline_hash and text_entries:
                for idx in range(len(text_entries) - 1, -1, -1):
                    candidate_text = text_entries[idx]
                    candidate_hash = (
                        hash_entries[idx]
                        if idx < len(hash_entries)
                        else str(snapshot.get("latest_hash") or "")
                    )
                    if candidate_hash and candidate_hash == baseline_hash:
                        continue
                    return candidate_text, snapshot_dict
            if text_entries:
                return text_entries[-1], snapshot_dict

        latest_text = str(snapshot.get("latest_text") or "").strip()
        if latest_text:
            return latest_text, snapshot_dict

    raw_text = snapshot_text.strip()
    if raw_text and not raw_text.startswith("{"):
        return raw_text, snapshot_dict
    return (fallback_text or "").strip() or None, snapshot_dict


def markdown_to_plain_text(markdown_text: str) -> str:
    lines: List[str] = []
    for raw_line in str(markdown_text or "").splitlines():
        line = raw_line
        line = re.sub(r"^\s{0,3}#{1,6}\s*", "", line)
        line = re.sub(r"^\s*>\s?", "", line)
        line = re.sub(r"^\s*[-*+]\s+", "* ", line)
        line = re.sub(r"^\s*\d+\.\s+", "", line)
        lines.append(line)
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_code_language(language: str) -> str:
    raw = str(language or "").strip().lower()
    if not raw:
        return ""
    return LANGUAGE_ALIASES.get(raw, raw)


def parse_language_label_line(raw_line: str) -> str:
    line = str(raw_line or "").strip().lower()
    line = re.sub(r"^\s{0,3}#{1,6}\s*", "", line)
    line = re.sub(r"^\s*[-*]\s*", "", line)
    line = re.sub(r"^\s*\d+\.\s*", "", line)
    if not line:
        return ""
    compact = re.sub(r"[\s:.\-]+$", "", line)
    compact = re.sub(r"\s+code$", "", compact)
    if compact in LANGUAGE_LABEL_LINE_MAP:
        return normalize_code_language(LANGUAGE_LABEL_LINE_MAP[compact])
    return ""


def is_output_label_line(raw_line: str) -> bool:
    line = str(raw_line or "").strip().lower()
    line = re.sub(r"^\s{0,3}#{1,6}\s*", "", line)
    line = re.sub(r"^\s*[-*]\s*", "", line)
    line = re.sub(r"^\s*\d+\.\s*", "", line)
    if not line:
        return False
    normalized = re.sub(r"[\s:.\-]+$", "", line)
    return normalized in OUTPUT_SECTION_LABELS


def infer_language_from_code(content: str) -> str:
    stripped = str(content or "").strip()
    if not stripped:
        return ""
    first_line = stripped.splitlines()[0].strip()
    if first_line.startswith("#!/") and "python" in first_line:
        return "python"
    if first_line.startswith("#!/") and any(x in first_line for x in ("bash", "sh", "zsh")):
        return "bash"

    lowered = stripped.lower()
    if any(lowered.startswith(prefix) for prefix in SHELL_PREFIXES):
        return "bash"

    python_markers = (
        "import ",
        "from ",
        "def ",
        "class ",
        "print(",
        "np.",
        "pd.",
    )
    if any(marker in lowered for marker in python_markers):
        return "python"
    return ""


def python_snippet_is_valid(content: str) -> bool:
    snippet = str(content or "").strip("\n")
    if not snippet:
        return False
    try:
        compile(snippet, "<inferred-python>", "exec")
        return True
    except SyntaxError:
        return False


def python_snippet_is_incomplete(content: str) -> bool:
    snippet = str(content or "").strip("\n")
    if not snippet:
        return False
    try:
        return codeop.compile_command(snippet, "<inferred-python>", "exec") is None
    except (OverflowError, SyntaxError, ValueError):
        return False


def trim_trailing_heading_comment_lines(lines: List[str]) -> None:
    while lines:
        stripped = str(lines[-1] or "").strip()
        if not stripped:
            lines.pop()
            continue
        if re.match(r"^#\s+[A-Za-z].*", stripped):
            lines.pop()
            continue
        break


def language_runner(language: str) -> Optional[str]:
    normalized = normalize_code_language(language)
    return RUNNER_BY_LANGUAGE.get(normalized)


def looks_like_indented_code_tail_line(language: str, line: str) -> bool:
    candidate = str(line or "").strip()
    if not candidate:
        return False
    normalized = normalize_code_language(language)
    if normalized == "python":
        return looks_like_python_code_line(candidate) or looks_like_python_continuation_line(candidate)
    if normalized == "bash":
        return looks_like_shell_command_line(candidate)
    return looks_like_generic_code_line(candidate)


def consume_fenced_indented_tail(
    markdown_text: str,
    start_offset: int,
    language: str,
    seeded: bool = False,
) -> Tuple[str, int]:
    tail = markdown_text[start_offset:]
    if not tail:
        return "", 0

    lines = tail.splitlines(keepends=True)
    consumed = 0
    collected: List[str] = []
    saw_code = bool(seeded)

    for raw in lines:
        line = raw.rstrip("\r\n")
        stripped = line.strip()
        if not stripped:
            if not saw_code and not collected:
                consumed += len(raw)
                continue
            collected.append("")
            consumed += len(raw)
            continue

        if stripped.startswith("```"):
            break
        if parse_language_label_line(stripped):
            break
        if is_output_label_line(stripped):
            break

        match = re.match(r"^(?: {4}|\t)(.+)$", line)
        if match:
            code_line = match.group(1).rstrip()
            code_line_for_check = code_line
            line_to_store = line.rstrip()
        else:
            code_line = line.rstrip()
            code_line_for_check = code_line
            line_to_store = code_line

        if not looks_like_indented_code_tail_line(language, code_line_for_check):
            break
        if normalize_code_language(language) == "bash":
            line_to_store = normalize_shell_candidate_line(code_line)
            if not line_to_store:
                break
            collected.append(line_to_store)
        else:
            # Preserve leading indentation so loop/function bodies remain runnable.
            collected.append(line_to_store)
        consumed += len(raw)
        saw_code = True

    if not saw_code:
        return "", 0

    while collected and not collected[0].strip():
        collected.pop(0)
    while collected and not collected[-1].strip():
        collected.pop()
    trim_trailing_heading_comment_lines(collected)
    if not collected:
        return "", 0
    return "\n".join(collected), consumed


def split_markdown_with_fenced_blocks(markdown_text: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    pattern = re.compile(r"```([A-Za-z0-9_.+-]*)[ \t]*\n(.*?)```", re.DOTALL)
    segments: List[Dict[str, Any]] = []
    code_blocks: List[Dict[str, Any]] = []
    last_end = 0
    text_id = 1
    code_id = 1

    for match in pattern.finditer(markdown_text):
        prefix = markdown_text[last_end : match.start()]
        if prefix.strip():
            segments.append({"id": f"text_{text_id}", "type": "text", "text": prefix.strip()})
            text_id += 1

        declared = normalize_code_language(match.group(1))
        content = str(match.group(2) or "").strip("\n")
        inferred = infer_language_from_code(content)
        language = declared or inferred or "text"
        tail_content, tail_consumed = consume_fenced_indented_tail(
            markdown_text,
            match.end(),
            language,
            seeded=bool(content.strip()),
        )
        if tail_content:
            content = (content.rstrip() + "\n" + tail_content).strip("\n")
        syntax_valid = True
        if language == "python":
            syntax_valid = python_snippet_is_valid(content)
        runner = language_runner(language)
        block = {
            "id": f"code_{code_id}",
            "type": "code_block",
            "source": "fenced",
            "language": language,
            "declared_language": declared or None,
            "inferred_language": inferred or None,
            "runner": runner,
            "syntax_valid": syntax_valid,
            "executable": bool(runner) and syntax_valid,
            "content": content,
        }
        code_blocks.append(block)
        segments.append(
            {
                "id": block["id"],
                "type": "code",
                "language": language,
                "text": content,
            }
        )
        code_id += 1
        last_end = match.end() + tail_consumed

    suffix = markdown_text[last_end:]
    if suffix.strip():
        segments.append({"id": f"text_{text_id}", "type": "text", "text": suffix.strip()})

    if not segments and markdown_text.strip():
        segments.append({"id": "text_1", "type": "text", "text": markdown_text.strip()})
    return segments, code_blocks


def normalize_shell_candidate_line(raw_line: str) -> str:
    line = str(raw_line or "").strip()
    line = re.sub(r"^\s*[-*]\s+", "", line)
    line = re.sub(r"^\s*\d+\.\s+", "", line)
    if line.startswith("$ "):
        line = line[2:].strip()
    return line


def looks_like_shell_command_line(raw_line: str) -> bool:
    raw = str(raw_line or "")
    line = normalize_shell_candidate_line(raw)
    if not line:
        return False
    if line.startswith("#"):
        return False
    lower = line.lower()

    parts = line.split()
    if not parts:
        return False
    first = parts[0].lower()
    has_prompt_prefix = raw.lstrip().startswith("$ ")
    if not has_prompt_prefix and line.endswith(":") and re.fullmatch(r"[A-Za-z][A-Za-z0-9 _.+-]*:\s*", line):
        # Treat heading-like labels (e.g., "Python loop:") as prose, not shell commands.
        return False
    # Avoid classifying standalone language labels like "Python" as shell commands.
    if len(parts) == 1 and not has_prompt_prefix and parse_language_label_line(first):
        return False
    if any(lower.startswith(prefix) for prefix in SHELL_PREFIXES):
        return True

    if first in SHELL_BINARIES:
        return True
    if first.startswith("./") or "/" in first:
        return True
    if re.fullmatch(r"[A-Za-z0-9_.-]+", first) and len(parts) > 1 and first in SHELL_BINARIES:
        return True
    return False


def extract_command_blocks_from_text(text: str, start_index: int) -> List[Dict[str, Any]]:
    lines = str(text or "").splitlines()
    blocks: List[Dict[str, Any]] = []
    i = 0
    index = start_index
    while i < len(lines):
        if not looks_like_shell_command_line(lines[i]):
            i += 1
            continue
        commands: List[str] = []
        while i < len(lines) and looks_like_shell_command_line(lines[i]):
            candidate = normalize_shell_candidate_line(lines[i])
            if candidate:
                commands.append(candidate)
            i += 1
        if not commands:
            continue
        block = {
            "id": f"command_{index}",
            "type": "command_block",
            "language": "bash",
            "runner": "bash",
            "executable": True,
            "commands": commands,
            "content": "\n".join(commands),
        }
        blocks.append(block)
        index += 1
    return blocks


def looks_like_python_code_line(raw_line: str) -> bool:
    line = str(raw_line or "").rstrip()
    stripped = line.strip()
    if not stripped:
        return False
    if re.match(r"^#{2,6}\s+\S", stripped):
        return False
    if stripped.startswith("#"):
        return True
    if re.match(r"^(from|import|def|class|for|while|if|elif|else|try|except|with|return|yield|assert)\b", stripped):
        return True
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*(\s*,\s*[A-Za-z_][A-Za-z0-9_]*)+\s*=", stripped):
        return True
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*(?:[\+\-\*/%@]?=)", stripped):
        return True
    if re.match(
        r"^[A-Za-z_][A-Za-z0-9_]*(?:\[[^\n]+\]|\.[A-Za-z_][A-Za-z0-9_]*)+\s*(?:[\+\-\*/%@]?=)",
        stripped,
    ):
        return True
    if re.match(r"^print\s*\(", stripped):
        return True
    if re.match(r"^[A-Za-z_][A-Za-z0-9_.]*\s*\(", stripped) and stripped.endswith(")"):
        return True
    if re.search(r"\b(np|pd|plt)\.[A-Za-z_]", stripped):
        return True
    if stripped.endswith(":") and re.match(r"^(for|while|if|elif|else|def|class|try|except|with)\b", stripped):
        return True
    return False


def looks_like_python_continuation_line(raw_line: str) -> bool:
    stripped = str(raw_line or "").strip()
    if not stripped:
        return False
    if re.fullmatch(r"[\])}\s,]+", stripped):
        return True
    if re.match(r"^[\])}]+(?:\.[A-Za-z_][A-Za-z0-9_]*.*)?$", stripped):
        return True
    if stripped.startswith(("[", "]", "(", ")", "{", "}")) and "," in stripped:
        return True
    if stripped.endswith(("]", ")", "}", ",")) and "," in stripped:
        return True
    if re.match(r"^(?:['\"][^'\"]+['\"]|[A-Za-z_][A-Za-z0-9_]*)\s*:\s*.+,?$", stripped):
        return True
    if stripped in {"pass", "break", "continue"}:
        return True
    return False


def looks_like_incomplete_python_continuation_line(
    raw_line: str,
    previous_lines: Sequence[str],
) -> bool:
    stripped = str(raw_line or "").strip()
    if not stripped:
        return False
    if parse_language_label_line(stripped) or is_output_label_line(stripped):
        return False
    if re.match(r"^#{2,6}\s+\S", stripped):
        return False
    if not python_snippet_is_incomplete("\n".join(str(line or "") for line in previous_lines)):
        return False
    if looks_like_python_continuation_line(stripped):
        return True
    if raw_line[:1].isspace():
        return True
    if stripped.startswith(("'", '"', "(", "[", "{")):
        return True
    if re.match(r"^[+\-*/%@]", stripped):
        return True
    return False


def looks_like_generic_code_line(raw_line: str) -> bool:
    stripped = str(raw_line or "").strip()
    if not stripped:
        return False
    if stripped.startswith("#"):
        return True
    if re.search(r"[{}()[\];=]", stripped):
        return True
    if re.match(r"^[A-Za-z_][A-Za-z0-9_.-]*\s+\S+", stripped):
        return True
    return False


def looks_like_runtime_output_line(raw_line: str) -> bool:
    stripped = str(raw_line or "").strip()
    if not stripped:
        return False
    if stripped.startswith(("Traceback", "Error", "Exception")):
        return True
    if re.match(r"^[\[\]()<>{}\-+*/|_\\0-9.,\s]+$", stripped):
        return True
    if re.match(r"^[A-Za-z0-9 _.-]+:\s*[-+0-9.\[\]()<>{},\s]+$", stripped):
        return True
    return False


def extract_python_blocks_from_text(text: str, start_index: int) -> List[Dict[str, Any]]:
    lines = str(text or "").splitlines()
    blocks: List[Dict[str, Any]] = []
    i = 0
    index = start_index

    while i < len(lines):
        if not looks_like_python_code_line(lines[i]):
            i += 1
            continue
        start = i
        collected: List[str] = []
        strong_markers = 0
        while i < len(lines):
            current = lines[i]
            if looks_like_python_code_line(current) or (
                collected
                and (
                    looks_like_python_continuation_line(current)
                    or looks_like_incomplete_python_continuation_line(current, collected)
                )
            ):
                collected.append(current.rstrip())
                trimmed = current.strip()
                if re.match(r"^(from|import|def|class)\b", trimmed) or "=" in trimmed or "print" in trimmed:
                    strong_markers += 1
                i += 1
                continue
            if current.strip() == "" and collected:
                collected.append("")
                i += 1
                continue
            break

        while collected and not collected[0].strip():
            collected.pop(0)
        while collected and not collected[-1].strip():
            collected.pop()
        trim_trailing_heading_comment_lines(collected)
        non_empty = [line for line in collected if line.strip()]
        content = "\n".join(collected)
        if len(non_empty) >= 1 and strong_markers >= 1 and python_snippet_is_valid(content):
            blocks.append(
                {
                    "id": f"code_inferred_{index}",
                    "type": "code_block",
                    "source": "inferred_text",
                    "language": "python",
                    "declared_language": None,
                    "inferred_language": "python",
                    "runner": "python",
                    "syntax_valid": True,
                    "executable": True,
                    "content": content,
                }
            )
            index += 1
            continue

        i = start + 1
    return blocks


def extract_labeled_code_and_output_blocks_from_text(
    text: str,
    start_code_index: int,
    start_output_index: int,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    lines = str(text or "").splitlines()
    code_blocks: List[Dict[str, Any]] = []
    output_blocks: List[Dict[str, Any]] = []
    i = 0
    code_index = start_code_index
    output_index = start_output_index

    while i < len(lines):
        declared_language = parse_language_label_line(lines[i])
        if not declared_language or declared_language == "text":
            i += 1
            continue

        language = normalize_code_language(declared_language)
        j = i + 1
        while j < len(lines) and lines[j].strip().lower() in UI_NOISE_LINES:
            j += 1

        collected: List[str] = []
        strong_markers = 0
        while j < len(lines):
            current = lines[j]
            stripped = current.strip()
            if not stripped:
                if collected:
                    collected.append("")
                j += 1
                continue
            if is_output_label_line(stripped):
                break

            match_language = False
            if language == "python":
                if looks_like_python_code_line(current) or (
                    collected
                    and (
                        looks_like_python_continuation_line(current)
                        or looks_like_incomplete_python_continuation_line(current, collected)
                    )
                ):
                    match_language = True
            elif language == "bash":
                if looks_like_shell_command_line(current):
                    current = normalize_shell_candidate_line(current)
                    match_language = True
            else:
                if looks_like_generic_code_line(current):
                    match_language = True

            if not match_language:
                break

            collected.append(current.rstrip())
            if (
                re.match(r"^(from|import|def|class)\b", stripped)
                or "=" in stripped
                or "print" in stripped
                or (language == "bash" and looks_like_shell_command_line(stripped))
            ):
                strong_markers += 1
            j += 1

        while collected and not collected[0].strip():
            collected.pop(0)
        while collected and not collected[-1].strip():
            collected.pop()
        if language == "python":
            trim_trailing_heading_comment_lines(collected)

        non_empty = [line for line in collected if line.strip()]
        min_lines = 1
        has_valid_code = len(non_empty) >= min_lines and (strong_markers >= 1 or language in {"bash", "python"})
        content = "\n".join(collected)
        syntax_valid = True
        if language == "python":
            syntax_valid = python_snippet_is_valid(content)
            has_valid_code = has_valid_code and syntax_valid
        if not has_valid_code:
            i += 1
            continue

        runner = language_runner(language)
        code_blocks.append(
            {
                "id": f"code_labeled_{code_index}",
                "type": "code_block",
                "source": "labeled_text",
                "language": language,
                "declared_language": language,
                "inferred_language": language,
                "runner": runner,
                "syntax_valid": syntax_valid,
                "executable": bool(runner) and syntax_valid,
                "content": content,
            }
        )
        code_index += 1

        i = j
        while i < len(lines) and not lines[i].strip():
            i += 1

        if i < len(lines) and is_output_label_line(lines[i]):
            label_raw = re.sub(r"^\s{0,3}#{1,6}\s*", "", lines[i].strip().lower())
            label_raw = re.sub(r"^\s*[-*]\s*", "", label_raw)
            label_raw = re.sub(r"^\s*\d+\.\s*", "", label_raw)
            label = re.sub(r"[\s:.\-]+$", "", label_raw)
            i += 1
            output_lines: List[str] = []
            consecutive_blanks = 0
            while i < len(lines):
                current = lines[i]
                stripped = current.strip()
                if not stripped:
                    if output_lines:
                        consecutive_blanks += 1
                        if consecutive_blanks >= 2:
                            break
                        output_lines.append("")
                    i += 1
                    continue

                consecutive_blanks = 0
                if output_lines and parse_language_label_line(stripped):
                    break
                if output_lines and re.match(r"^\s{0,3}#{1,6}\s+", current):
                    break
                if output_lines and not looks_like_runtime_output_line(current):
                    break
                output_lines.append(current.rstrip())
                i += 1

            while output_lines and not output_lines[0].strip():
                output_lines.pop(0)
            while output_lines and not output_lines[-1].strip():
                output_lines.pop()
            if output_lines:
                output_blocks.append(
                    {
                        "id": f"output_{output_index}",
                        "type": "output_block",
                        "source": "labeled_text",
                        "label": label or "result",
                        "language": "text",
                        "content": "\n".join(output_lines),
                    }
                )
                output_index += 1
        continue

    return code_blocks, output_blocks


def extract_response_items_from_text(
    text: str,
    start_text_index: int,
    start_labeled_code_index: int,
    start_output_index: int,
    start_command_index: int,
    start_inferred_index: int,
    segment_id: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], int, int, int, int, int]:
    lines = str(text or "").splitlines()
    items: List[Dict[str, Any]] = []
    i = 0
    text_index = start_text_index
    labeled_code_index = start_labeled_code_index
    output_index = start_output_index
    command_index = start_command_index
    inferred_index = start_inferred_index
    text_buffer: List[str] = []

    def flush_text_buffer() -> None:
        nonlocal text_index
        if not text_buffer:
            return
        while text_buffer and not text_buffer[0].strip():
            text_buffer.pop(0)
        while text_buffer and not text_buffer[-1].strip():
            text_buffer.pop()
        if not text_buffer:
            return
        chunk = "\n".join(text_buffer).strip("\n")
        text_buffer.clear()
        if not chunk.strip():
            return
        item: Dict[str, Any] = {
            "id": f"text_item_{text_index}",
            "type": "text",
            "source": "text",
            "text": chunk,
        }
        if segment_id:
            item["segment_id"] = segment_id
        items.append(item)
        text_index += 1

    while i < len(lines):
        declared_language = parse_language_label_line(lines[i])
        if declared_language and declared_language != "text":
            language = normalize_code_language(declared_language)
            j = i + 1
            while j < len(lines) and lines[j].strip().lower() in UI_NOISE_LINES:
                j += 1

            collected: List[str] = []
            strong_markers = 0
            while j < len(lines):
                current = lines[j]
                stripped = current.strip()
                if not stripped:
                    if collected:
                        collected.append("")
                    j += 1
                    continue
                if is_output_label_line(stripped):
                    break

                render_line = current.rstrip()
                matched = False
                if language == "python":
                    matched = looks_like_python_code_line(current) or (
                        collected
                        and (
                            looks_like_python_continuation_line(current)
                            or looks_like_incomplete_python_continuation_line(current, collected)
                        )
                    )
                elif language == "bash":
                    matched = looks_like_shell_command_line(current)
                    if matched:
                        render_line = normalize_shell_candidate_line(current)
                else:
                    matched = looks_like_generic_code_line(current)
                if not matched:
                    break

                collected.append(render_line)
                if (
                    re.match(r"^(from|import|def|class)\b", stripped)
                    or "=" in stripped
                    or "print" in stripped
                    or (language == "bash" and looks_like_shell_command_line(stripped))
                ):
                    strong_markers += 1
                j += 1

            while collected and not collected[0].strip():
                collected.pop(0)
            while collected and not collected[-1].strip():
                collected.pop()
            if language == "python":
                trim_trailing_heading_comment_lines(collected)

            non_empty = [line for line in collected if line.strip()]
            content = "\n".join(collected)
            syntax_valid = True
            has_valid_code = len(non_empty) >= 1 and (strong_markers >= 1 or language in {"bash", "python"})
            if language == "python":
                syntax_valid = python_snippet_is_valid(content)
                has_valid_code = has_valid_code and syntax_valid
            if has_valid_code:
                flush_text_buffer()
                runner = language_runner(language)
                code_item: Dict[str, Any] = {
                    "id": f"code_labeled_{labeled_code_index}",
                    "type": "code_block",
                    "source": "labeled_text",
                    "language": language,
                    "declared_language": language,
                    "inferred_language": language,
                    "runner": runner,
                    "syntax_valid": syntax_valid,
                    "executable": bool(runner) and syntax_valid,
                    "content": content,
                }
                if segment_id:
                    code_item["segment_id"] = segment_id
                items.append(code_item)
                labeled_code_index += 1

                i = j
                while i < len(lines) and not lines[i].strip():
                    i += 1

                if i < len(lines) and is_output_label_line(lines[i]):
                    label_raw = re.sub(r"^\s{0,3}#{1,6}\s*", "", lines[i].strip().lower())
                    label_raw = re.sub(r"^\s*[-*]\s*", "", label_raw)
                    label_raw = re.sub(r"^\s*\d+\.\s*", "", label_raw)
                    label = re.sub(r"[\s:.\-]+$", "", label_raw)
                    i += 1
                    output_lines: List[str] = []
                    consecutive_blanks = 0
                    while i < len(lines):
                        current = lines[i]
                        stripped = current.strip()
                        if not stripped:
                            if output_lines:
                                consecutive_blanks += 1
                                if consecutive_blanks >= 2:
                                    break
                                output_lines.append("")
                            i += 1
                            continue

                        consecutive_blanks = 0
                        if output_lines and parse_language_label_line(stripped):
                            break
                        if output_lines and re.match(r"^\s{0,3}#{1,6}\s+", current):
                            break
                        if output_lines and not looks_like_runtime_output_line(current):
                            break
                        output_lines.append(current.rstrip())
                        i += 1

                    while output_lines and not output_lines[0].strip():
                        output_lines.pop(0)
                    while output_lines and not output_lines[-1].strip():
                        output_lines.pop()
                    if output_lines:
                        output_item: Dict[str, Any] = {
                            "id": f"output_{output_index}",
                            "type": "output_block",
                            "source": "labeled_text",
                            "label": label or "result",
                            "language": "text",
                            "content": "\n".join(output_lines),
                        }
                        if segment_id:
                            output_item["segment_id"] = segment_id
                        items.append(output_item)
                        output_index += 1
                continue

        if looks_like_shell_command_line(lines[i]):
            commands: List[str] = []
            j = i
            while j < len(lines) and looks_like_shell_command_line(lines[j]):
                candidate = normalize_shell_candidate_line(lines[j])
                if candidate:
                    commands.append(candidate)
                j += 1
            if commands:
                flush_text_buffer()
                command_item: Dict[str, Any] = {
                    "id": f"command_{command_index}",
                    "type": "command_block",
                    "source": "command_text",
                    "language": "bash",
                    "runner": "bash",
                    "executable": True,
                    "commands": commands,
                    "content": "\n".join(commands),
                }
                if segment_id:
                    command_item["segment_id"] = segment_id
                items.append(command_item)
                command_index += 1
                i = j
                continue

        if looks_like_python_code_line(lines[i]):
            start = i
            collected = []
            strong_markers = 0
            while i < len(lines):
                current = lines[i]
                if looks_like_python_code_line(current) or (
                    collected
                    and (
                        looks_like_python_continuation_line(current)
                        or looks_like_incomplete_python_continuation_line(current, collected)
                    )
                ):
                    collected.append(current.rstrip())
                    trimmed = current.strip()
                    if re.match(r"^(from|import|def|class)\b", trimmed) or "=" in trimmed or "print" in trimmed:
                        strong_markers += 1
                    i += 1
                    continue
                if current.strip() == "" and collected:
                    collected.append("")
                    i += 1
                    continue
                break

            while collected and not collected[0].strip():
                collected.pop(0)
            while collected and not collected[-1].strip():
                collected.pop()
            trim_trailing_heading_comment_lines(collected)
            non_empty = [line for line in collected if line.strip()]
            content = "\n".join(collected)
            if len(non_empty) >= 1 and strong_markers >= 1 and python_snippet_is_valid(content):
                flush_text_buffer()
                inferred_item: Dict[str, Any] = {
                    "id": f"code_inferred_{inferred_index}",
                    "type": "code_block",
                    "source": "inferred_text",
                    "language": "python",
                    "declared_language": None,
                    "inferred_language": "python",
                    "runner": "python",
                    "syntax_valid": True,
                    "executable": True,
                    "content": content,
                }
                if segment_id:
                    inferred_item["segment_id"] = segment_id
                items.append(inferred_item)
                inferred_index += 1
                continue

            i = start

        text_buffer.append(lines[i].rstrip())
        i += 1

    flush_text_buffer()
    return (
        items,
        text_index,
        labeled_code_index,
        output_index,
        command_index,
        inferred_index,
    )


def dedupe_code_blocks(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: List[Dict[str, Any]] = []
    seen: set = set()
    for block in blocks:
        language = normalize_code_language(str(block.get("language") or ""))
        content = str(block.get("content") or "").strip()
        if not content:
            continue
        key = (language, content)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(block)
    return deduped


def dedupe_command_blocks(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: List[Dict[str, Any]] = []
    seen: set = set()
    for block in blocks:
        content = str(block.get("content") or "").strip()
        if not content:
            continue
        if content in seen:
            continue
        seen.add(content)
        deduped.append(block)
    return deduped


def clean_section_label(raw_line: str) -> str:
    line = str(raw_line or "").strip().lower()
    line = re.sub(r"^\s{0,3}#{1,6}\s*", "", line)
    line = re.sub(r"^\s*[-*]\s*", "", line)
    line = re.sub(r"^\s*\d+\.\s*", "", line)
    return re.sub(r"[\s:.\-]+$", "", line)


def is_probable_noise_digit_line(lines: Sequence[str], index: int) -> bool:
    if index < 0 or index >= len(lines):
        return False
    current = str(lines[index] or "").strip()
    if not re.fullmatch(r"\d", current):
        return False

    prev_non_empty = ""
    for j in range(index - 1, -1, -1):
        candidate = str(lines[j] or "").strip()
        if candidate:
            prev_non_empty = candidate
            break
    next_non_empty = ""
    for j in range(index + 1, len(lines)):
        candidate = str(lines[j] or "").strip()
        if candidate:
            next_non_empty = candidate
            break
    if not prev_non_empty or not next_non_empty:
        return False
    if re.match(r"^\s{0,3}#{1,6}\s+\S", prev_non_empty):
        if re.match(r"^\d+[\).]?\s*", next_non_empty):
            return False
        if next_non_empty.startswith(("-", "*")):
            return False
        return True
    return False


def rewrite_labeled_sections_as_fenced_markdown(text: str) -> str:
    lines = str(text or "").splitlines()
    if not lines:
        return ""
    out: List[str] = []
    i = 0

    while i < len(lines):
        if is_probable_noise_digit_line(lines, i):
            i += 1
            continue

        language = parse_language_label_line(lines[i])
        if language and language != "text":
            j = i + 1
            while j < len(lines) and lines[j].strip().lower() in UI_NOISE_LINES:
                j += 1

            collected: List[str] = []
            strong_markers = 0
            while j < len(lines):
                current = lines[j]
                stripped = current.strip()
                if not stripped:
                    if collected:
                        collected.append("")
                    j += 1
                    continue
                if is_output_label_line(stripped):
                    break

                matched = False
                render_line = current.rstrip()
                if language == "python":
                    matched = looks_like_python_code_line(current) or (
                        collected
                        and (
                            looks_like_python_continuation_line(current)
                            or looks_like_incomplete_python_continuation_line(current, collected)
                        )
                    )
                elif language == "bash":
                    matched = looks_like_shell_command_line(current)
                    if matched:
                        render_line = normalize_shell_candidate_line(current)
                else:
                    matched = looks_like_generic_code_line(current)

                if not matched:
                    break

                collected.append(render_line)
                if (
                    re.match(r"^(from|import|def|class)\b", stripped)
                    or "=" in stripped
                    or "print" in stripped
                    or (language == "bash" and looks_like_shell_command_line(stripped))
                ):
                    strong_markers += 1
                j += 1

            while collected and not collected[0].strip():
                collected.pop(0)
            while collected and not collected[-1].strip():
                collected.pop()
            if language == "python":
                trim_trailing_heading_comment_lines(collected)

            non_empty = [line for line in collected if line.strip()]
            min_lines = 1
            has_valid_code = len(non_empty) >= min_lines and (strong_markers >= 1 or language in {"bash", "python"})
            if has_valid_code and language == "python":
                has_valid_code = python_snippet_is_valid("\n".join(collected))
            if has_valid_code:
                out.append(f"```{language}")
                out.extend(collected)
                out.append("```")

                i = j
                while i < len(lines) and not lines[i].strip():
                    i += 1

                if i < len(lines) and is_output_label_line(lines[i]):
                    label = clean_section_label(lines[i]) or "output"
                    i += 1
                    output_lines: List[str] = []
                    consecutive_blanks = 0
                    while i < len(lines):
                        current = lines[i]
                        stripped = current.strip()
                        if not stripped:
                            if output_lines:
                                consecutive_blanks += 1
                                if consecutive_blanks >= 2:
                                    break
                                output_lines.append("")
                            i += 1
                            continue

                        consecutive_blanks = 0
                        if output_lines and parse_language_label_line(stripped):
                            break
                        if output_lines and re.match(r"^\s{0,3}#{1,6}\s+", current):
                            break
                        if output_lines and not looks_like_runtime_output_line(current):
                            break
                        output_lines.append(current.rstrip())
                        i += 1

                    while output_lines and not output_lines[0].strip():
                        output_lines.pop(0)
                    while output_lines and not output_lines[-1].strip():
                        output_lines.pop()
                    if output_lines:
                        out.append(f"{label.capitalize()}:")
                        out.append("```text")
                        out.extend(output_lines)
                        out.append("```")
                continue

        out.append(lines[i].rstrip())
        i += 1

    rendered = "\n".join(out)
    rendered = re.sub(r"\n{3,}", "\n\n", rendered)
    return rendered.strip()


def prettify_markdown_response(markdown_text: str) -> str:
    text = str(markdown_text or "").strip()
    if not text:
        return ""
    segments, _ = split_markdown_with_fenced_blocks(text)
    rendered_segments: List[str] = []
    for segment in segments:
        segment_type = str(segment.get("type") or "")
        if segment_type == "code":
            language = normalize_code_language(str(segment.get("language") or "")) or "text"
            body = str(segment.get("text") or "").strip("\n")
            rendered_segments.append(f"```{language}\n{body}\n```".rstrip())
            continue
        if segment_type == "text":
            rewritten = rewrite_labeled_sections_as_fenced_markdown(str(segment.get("text") or ""))
            if rewritten:
                rendered_segments.append(rewritten)

    joined = "\n\n".join(part for part in rendered_segments if part.strip())
    joined = re.sub(r"\n{3,}", "\n\n", joined)
    return joined.strip()


def build_response_artifacts(markdown_text: str) -> Dict[str, Any]:
    text = str(markdown_text or "").strip()
    segments, fenced_code_blocks = split_markdown_with_fenced_blocks(text)
    response_items: List[Dict[str, Any]] = []
    text_item_index = 1
    labeled_code_index = 1
    output_index = 1
    command_index = 1
    inferred_index = 1
    fenced_code_by_id = {
        str(block.get("id") or ""): block
        for block in fenced_code_blocks
        if str(block.get("id") or "")
    }

    for segment in segments:
        segment_type = str(segment.get("type") or "")
        segment_id = str(segment.get("id") or "")
        if segment_type == "code":
            block = fenced_code_by_id.get(segment_id)
            if isinstance(block, dict):
                response_items.append(dict(block))
            continue
        if segment_type != "text":
            continue
        extracted_items, text_item_index, labeled_code_index, output_index, command_index, inferred_index = (
            extract_response_items_from_text(
                str(segment.get("text") or ""),
                start_text_index=text_item_index,
                start_labeled_code_index=labeled_code_index,
                start_output_index=output_index,
                start_command_index=command_index,
                start_inferred_index=inferred_index,
                segment_id=segment_id or None,
            )
        )
        response_items.extend(extracted_items)

    code_blocks = dedupe_code_blocks(
        [dict(item) for item in response_items if str(item.get("type") or "") == "code_block"]
    )
    command_blocks = dedupe_command_blocks(
        [dict(item) for item in response_items if str(item.get("type") or "") == "command_block"]
    )
    output_blocks = [dict(item) for item in response_items if str(item.get("type") or "") == "output_block"]
    bash_code_contents = {
        str(block.get("content") or "").strip()
        for block in code_blocks
        if normalize_code_language(str(block.get("language") or "")) == "bash"
    }
    command_blocks = [block for block in command_blocks if str(block.get("content") or "").strip() not in bash_code_contents]

    copy_items: List[Dict[str, Any]] = []
    for block in code_blocks:
        copy_items.append(
            {
                "id": block["id"],
                "kind": "code_block",
                "language": block.get("language"),
                "content": block.get("content", ""),
            }
        )
    for block in command_blocks:
        copy_items.append(
            {
                "id": block["id"],
                "kind": "command_block",
                "language": "bash",
                "content": block.get("content", ""),
                "commands": block.get("commands", []),
            }
        )
    for block in output_blocks:
        copy_items.append(
            {
                "id": block["id"],
                "kind": "output_block",
                "language": "text",
                "label": block.get("label"),
                "content": block.get("content", ""),
            }
        )

    tool_hints: List[Dict[str, Any]] = []
    for block in code_blocks:
        runner = str(block.get("runner") or "").strip()
        if not runner:
            continue
        command = ""
        if runner == "python":
            command = "python <script.py>"
        elif runner == "bash":
            command = "bash <script.sh>"
        elif runner == "node":
            command = "node <script.js>"
        if command:
            tool_hints.append(
                {
                    "kind": "script",
                    "source_id": block["id"],
                    "runner": runner,
                    "command": command,
                }
            )
    for block in command_blocks:
        for command in block.get("commands", []):
            tool_hints.append(
                {
                    "kind": "shell_command",
                    "source_id": block["id"],
                    "runner": "bash",
                    "command": command,
                }
            )

    return {
        "segments": segments,
        "response_items": response_items,
        "code_blocks": code_blocks,
        "command_blocks": command_blocks,
        "output_blocks": output_blocks,
        "copy_items": copy_items,
        "tool_hints": tool_hints[:64],
    }


def format_assistant_output(
    text: Optional[str],
    output_format: str,
    snapshot: Optional[Dict[str, Any]] = None,
) -> str:
    mode = (output_format or DEFAULT_OUTPUT_FORMAT).lower().strip()
    normalized_text = str(text or "").strip()

    if mode == "plain":
        return markdown_to_plain_text(normalized_text)

    if mode == "json":
        artifacts = build_response_artifacts(normalized_text)
        payload: Dict[str, Any] = {
            "ok": bool(normalized_text),
            "format": "json",
            "text": normalized_text,
            "artifacts": artifacts,
        }
        if isinstance(snapshot, dict):
            for key in (
                "extractor",
                "assistant_count",
                "latest_hash",
                "user_count",
                "latest_user_hash",
                "assistant_texts",
                "assistant_hashes",
            ):
                if key in snapshot:
                    payload[key] = snapshot.get(key)
        return json.dumps(payload, indent=2)

    return prettify_markdown_response(normalized_text)


def format_cell_action_footer(action_ref: Dict[str, Any]) -> str:
    handle = str(action_ref.get("handle") or "").strip()
    cell_id = str(action_ref.get("cell_id") or "").strip()
    language = normalize_code_language(str(action_ref.get("language") or "")) or "text"
    return (
        f"[{handle} {cell_id} {language}] "
        f"/run {handle} /show {handle} /edit {handle} /fork {handle}"
    ).strip()


def render_response_items_with_action_refs(
    response_items: Sequence[Dict[str, Any]],
    action_ref_by_source_id: Dict[str, Dict[str, Any]],
) -> str:
    lines, _ = build_response_render_lines(response_items, action_ref_by_source_id)
    rendered = "\n".join(lines)
    rendered = re.sub(r"\n{3,}", "\n\n", rendered)
    return rendered.strip()


def build_response_render_lines(
    response_items: Sequence[Dict[str, Any]],
    action_ref_by_source_id: Dict[str, Dict[str, Any]],
) -> Tuple[List[str], Dict[str, Dict[str, Any]]]:
    lines: List[str] = []
    ref_spans: Dict[str, Dict[str, Any]] = {}

    def append_blank_line() -> None:
        if lines and lines[-1] != "":
            lines.append("")

    def append_text_block(block_text: str) -> None:
        text = str(block_text or "").strip()
        if not text:
            return
        append_blank_line()
        lines.extend(text.splitlines())

    def register_ref_span(action_ref: Dict[str, Any], start_line: int, end_line: int) -> None:
        handle = str(action_ref.get("handle") or "").strip()
        cell_id = str(action_ref.get("cell_id") or "").strip()
        payload = {
            "handle": handle,
            "cell_id": cell_id,
            "language": normalize_code_language(str(action_ref.get("language") or "")) or "text",
            "start_line": int(start_line),
            "end_line": int(end_line),
        }
        if handle:
            ref_spans[handle] = dict(payload)
        if cell_id:
            ref_spans[cell_id] = dict(payload)

    for item in response_items:
        item_type = str(item.get("type") or "")
        if item_type == "text":
            append_text_block(str(item.get("text") or ""))
            continue

        if item_type in {"code_block", "command_block"}:
            language = normalize_code_language(str(item.get("language") or "")) or "text"
            body = str(item.get("content") or "").strip("\n")
            block_start = len(lines)
            block_end = len(lines)
            if body:
                append_blank_line()
                block_start = len(lines)
                lines.append(f"```{language}")
                lines.extend(body.splitlines())
                lines.append("```")
                block_end = len(lines) - 1
            action_ref = action_ref_by_source_id.get(str(item.get("id") or ""))
            if action_ref:
                footer = format_cell_action_footer(action_ref)
                append_blank_line()
                lines.append(footer)
                if body:
                    register_ref_span(action_ref, block_start, block_end)
            continue

        if item_type == "output_block":
            label = str(item.get("label") or "output").strip() or "output"
            body = str(item.get("content") or "").strip("\n")
            if body:
                append_blank_line()
                lines.append(f"{label.capitalize()}:")
                lines.append("```text")
                lines.extend(body.splitlines())
                lines.append("```")

    while lines and not lines[-1].strip():
        lines.pop()
    return lines, ref_spans


def build_interactive_turn_view(
    assistant_text: Optional[str],
    output_format: str,
    artifacts: Optional[Dict[str, Any]] = None,
    action_refs: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    normalized_text = str(assistant_text or "").strip()
    mode = (output_format or DEFAULT_OUTPUT_FORMAT).lower().strip()
    artifact_payload = artifacts if isinstance(artifacts, dict) else build_response_artifacts(normalized_text)
    refs = list(action_refs or [])
    action_ref_by_source_id = {
        str(ref.get("source_id") or ""): ref
        for ref in refs
        if str(ref.get("source_id") or "")
    }
    response_items = list(artifact_payload.get("response_items") or [])
    preview_lines: List[str] = []
    ref_spans: Dict[str, Dict[str, Any]] = {}
    if response_items:
        preview_lines, ref_spans = build_response_render_lines(response_items, action_ref_by_source_id)
    preview_text = "\n".join(preview_lines).strip()

    if mode == "json":
        payload: Dict[str, Any] = {
            "ok": bool(normalized_text),
            "format": "json",
            "text": normalized_text,
            "artifacts": artifact_payload,
        }
        if refs:
            payload["action_refs"] = refs
        rendered = json.dumps(payload, indent=2)
    elif preview_text:
        rendered = preview_text
    else:
        rendered = format_assistant_output(normalized_text, output_format=mode, snapshot=None)
    if mode == "plain":
        rendered = markdown_to_plain_text(rendered)
    return {
        "artifacts": artifact_payload,
        "action_refs": refs,
        "output_format": mode,
        "rendered_output": rendered,
        "preview_lines": preview_lines,
        "ref_spans": ref_spans,
    }


def render_interactive_turn_output(
    assistant_text: Optional[str],
    output_format: str,
    artifacts: Optional[Dict[str, Any]] = None,
    action_refs: Optional[Sequence[Dict[str, Any]]] = None,
) -> str:
    turn_view = build_interactive_turn_view(
        assistant_text=assistant_text,
        output_format=output_format,
        artifacts=artifacts,
        action_refs=action_refs,
    )
    return str(turn_view.get("rendered_output") or "")


def run_self_tests() -> int:
    import unittest
    from unittest import mock

    class ResponseFormattingSelfTests(unittest.TestCase):
        def test_labeled_python_result_to_json_artifacts(self) -> None:
            sample = """Python
a = np.array ([[1,2,3],
              [4,5,6]])

b = np.array ([10,20,30])

print (a + b)

Result:

[[11 22 33]
 [14 25 36]]
"""
            artifacts = build_response_artifacts(sample)
            self.assertEqual(len(artifacts.get("code_blocks", [])), 1)
            self.assertEqual(len(artifacts.get("command_blocks", [])), 0)
            self.assertEqual(len(artifacts.get("output_blocks", [])), 1)
            code_block = artifacts["code_blocks"][0]
            self.assertEqual(code_block.get("language"), "python")
            self.assertIn("print (a + b)", code_block.get("content", ""))
            output_block = artifacts["output_blocks"][0]
            self.assertEqual(output_block.get("label"), "result")
            self.assertIn("[11 22 33]", output_block.get("content", ""))

        def test_plain_commands_stay_command_blocks(self) -> None:
            sample = """pip install numpy
python ascii_normal.py
"""
            artifacts = build_response_artifacts(sample)
            self.assertEqual(len(artifacts.get("code_blocks", [])), 0)
            self.assertEqual(len(artifacts.get("command_blocks", [])), 1)
            self.assertEqual(len(artifacts.get("output_blocks", [])), 0)

        def test_labeled_bash_becomes_code_block(self) -> None:
            sample = """Bash
pip install numpy
python ascii_normal.py
"""
            artifacts = build_response_artifacts(sample)
            self.assertEqual(len(artifacts.get("code_blocks", [])), 1)
            self.assertEqual(len(artifacts.get("command_blocks", [])), 0)
            block = artifacts["code_blocks"][0]
            self.assertEqual(block.get("language"), "bash")
            self.assertIn("pip install numpy", block.get("content", ""))

        def test_markdown_prettify_wraps_labeled_python_and_output(self) -> None:
            sample = """## What Makes NumPy Special?
4
Example:

Python
a = np.array ([1,2,3])
b = 10
print (a + b)

Output:

[11 12 13]
"""
            rendered = format_assistant_output(sample, "markdown")
            self.assertIn("```python", rendered)
            self.assertIn("```text", rendered)
            self.assertNotIn("\n4\n", rendered)

        def test_colorize_markdown_text_for_terminal_styles_code_blocks(self) -> None:
            rendered = colorize_markdown_text_for_terminal(
                "```python\nprint('x')\n```\n\n```bash\necho hi\n```",
                enable_color=True,
            )
            self.assertIn(ANSI_RESET, rendered)
            self.assertIn(LANGUAGE_ANSI_BY_LANGUAGE["python"], rendered)
            self.assertIn(LANGUAGE_ANSI_BY_LANGUAGE["bash"], rendered)

        def test_colorize_command_help_lines_for_terminal_styles_usage(self) -> None:
            lines = colorize_command_help_lines_for_terminal(
                ["/run [cell|@ref] [timeout] :: execute a cell or response ref"],
                enable_color=True,
            )
            self.assertEqual(len(lines), 1)
            self.assertIn(ANSI_CYAN, lines[0])
            self.assertIn(ANSI_DIM, lines[0])

        def test_output_heading_label_parsed(self) -> None:
            sample = """Python
import numpy as np
print(np.arange(3))

### Output

[0 1 2]
"""
            artifacts = build_response_artifacts(sample)
            self.assertEqual(len(artifacts.get("output_blocks", [])), 1)
            self.assertEqual(artifacts["output_blocks"][0].get("label"), "output")

        def test_json_mode_contains_output_blocks(self) -> None:
            sample = """Python
import numpy as np
print(np.array([1,2,3]))

Result:

[1 2 3]
"""
            raw_json = format_assistant_output(sample, "json")
            payload = json.loads(raw_json)
            self.assertEqual(payload.get("format"), "json")
            artifacts = payload.get("artifacts", {})
            self.assertEqual(len(artifacts.get("output_blocks", [])), 1)

        def test_single_line_labeled_python_is_captured(self) -> None:
            sample = """Python
result = a + b
"""
            artifacts = build_response_artifacts(sample)
            self.assertEqual(len(artifacts.get("code_blocks", [])), 1)
            block = artifacts["code_blocks"][0]
            self.assertEqual(block.get("language"), "python")
            self.assertIn("result = a + b", block.get("content", ""))

        def test_python_snippet_is_valid_rejects_unexpected_indent(self) -> None:
            self.assertFalse(
                python_snippet_is_valid(
                    "    print('x')\n\ndef main():\n    pass\n"
                )
            )

        def test_ascii_plot_script_keeps_full_python_block(self) -> None:
            sample = """Here’s a simple Python script that:
- pulls the last year of stock data
- converts it into an ASCII chart (no GUI needed)
### 📜 Full script (ASCII stock plot)

Python
import yfinance as yf
import numpy as np
from datetime import datetime, timedelta

def ascii_plot (data, width=80, height=20):
    # Normalize data to fit chart height
    min_val = np.min (data)
    max_val = np.max (data)
    scaled = (data - min_val) / (max_val - min_val + 1e-9)
    scaled = (scaled * (height - 1)).astype (int)

    # Downsample to fit width
    if len (scaled) > width:
        idx = np.linspace (0, len (scaled) - 1, width).astype (int)
        scaled = scaled[idx]

    # Create empty canvas
    canvas = [[" " for _ in range (len (scaled))] for _ in range (height)]

    # Plot points
    for x, y in enumerate (scaled):
        canvas[height - 1 - y][x] = "*"

    # Print chart
    for row in canvas:
        print ("".join (row))

    print (f"\\nMin: {min_val:.2f} | Max: {max_val:.2f}")

def main ():
    ticker = "AAPL"

    end_date = datetime.today ()
    start_date = end_date - timedelta (days=365)

    # Download data
    df = yf.download (ticker, start=start_date, end=end_date)

    # Use closing prices
    close_prices = df["Close"].values

    # Plot ASCII chart
    ascii_plot (close_prices)

if __name__ == "__main__":
    main ()
"""
            artifacts = build_response_artifacts(sample)
            self.assertEqual(len(artifacts.get("code_blocks", [])), 1)
            block = artifacts["code_blocks"][0]
            content = str(block.get("content") or "")
            self.assertIn("import yfinance as yf", content)
            self.assertIn('canvas[height - 1 - y][x] = "*"', content)
            self.assertIn('print ("".join (row))', content)
            self.assertIn('print (f"\\nMin: {min_val:.2f} | Max: {max_val:.2f}")', content)
            self.assertTrue(bool(block.get("syntax_valid")))

        def test_weekly_ascii_candles_script_keeps_helper_function(self) -> None:
            sample = """Nice—this is a fun one. Let’s turn stock data into weekly ASCII candlestick charts.
We’ll:
- Pull daily data
- Resample to weekly OHLC
- Render candles using text
## 🐍 Full Python Script (ASCII Weekly Candles)

Python
import yfinance as yf
import pandas as pd

def get_weekly_data (ticker):
    data = yf.download (ticker, period="1y", interval="1d")

    # Convert to weekly OHLC
    weekly = data.resample ('W').agg ({
        'Open': 'first',
        'High': 'max',
        'Low': 'min',
        'Close': 'last'
    }).dropna ()

    return weekly

def ascii_candles (data, height=20):
    max_price = data['High'].max ()
    min_price = data['Low'].min ()
    price_range = max_price - min_price

    def scale (price):
        return int ((price - min_price) / price_range * (height - 1))

    canvas = [[" " for _ in range (len (data))] for _ in range (height)]

    for i, (_, row) in enumerate (data.iterrows ()):
        o, h, l, c = row['Open'], row['High'], row['Low'], row['Close']

        high = scale (h)
        low = scale (l)
        open_ = scale (o)
        close = scale (c)

        # Draw wick
        for y in range (low, high + 1):
            canvas[height - 1 - y][i] = "|"

        # Draw body
        top = max (open_, close)
        bottom = min (open_, close)

        for y in range (bottom, top + 1):
            canvas[height - 1 - y][i] = "█" if c >= o else "░"

    # Print chart
    for row in canvas:
        print ("".join (row))

# Run it
ticker = "AAPL"
weekly_data = get_weekly_data (ticker)
ascii_candles (weekly_data)
"""
            artifacts = build_response_artifacts(sample)
            self.assertEqual(len(artifacts.get("code_blocks", [])), 1)
            block = artifacts["code_blocks"][0]
            content = str(block.get("content") or "")
            self.assertIn("import yfinance as yf", content)
            self.assertIn("def get_weekly_data (ticker):", content)
            self.assertIn("'Close': 'last'", content)
            self.assertIn("}).dropna ()", content)
            self.assertIn('canvas[height - 1 - y][i] = "█" if c >= o else "░"', content)
            self.assertIn("weekly_data = get_weekly_data (ticker)", content)
            self.assertTrue(bool(block.get("syntax_valid")))

        def test_mixed_fenced_and_labeled_nlp_walkthrough_keeps_all_python_blocks(self) -> None:
            sample = """Here’s a simple, practical NLP algorithm you can actually build and run. I’ll keep it minimal but real.
## 🧠 Example: Basic Sentiment Classifier (from scratch)
This is a classic NLP pipeline:
- Clean text
- Convert to numbers
- Train a simple model
- Predict sentiment
## ⚙️ Step-by-step algorithm
### 1. Preprocess text
- Lowercase
- Remove punctuation
- Tokenize (split into words)

```python
import re

def preprocess (text):
    text = text.lower ()
    text = re.sub (r'[^a-z\\s]', '', text)
    tokens = text.split ()
    return tokens
```
### 2. Build vocabulary
Map each word → index

```python
def build_vocab (dataset):
    vocab = {}
    idx = 0
    for text in dataset:
        for word in preprocess (text):
            if word not in vocab:
                vocab[word] = idx
                idx += 1
    return vocab
```
### 3. Convert text → vector (Bag of Words)

Python
import numpy as np

def vectorize (text, vocab):
    vec = np.zeros (len (vocab))
    for word in preprocess (text):
        if word in vocab:
            vec[vocab[word]] += 1
    return vec

### 4. Train a simple model (Naive Bayes)

Python
class NaiveBayes:
    def fit (self, X, y):
        self.classes = np.unique (y)
        self.mean = {}
        self.var = {}
        self.priors = {}

        for c in self.classes:
            X_c = X[y == c]
            self.mean[c] = X_c.mean (axis=0)
            self.var[c] = X_c.var (axis=0) + 1e-6
            self.priors[c] = X_c.shape[0] / X.shape[0]

    def predict (self, X):
        return [self._predict (x) for x in X]

    def _predict (self, x):
        posteriors = []

        for c in self.classes:
            prior = np.log (self.priors[c])
            likelihood = np.sum (
                -0.5 * np.log (2 * np.pi * self.var[c]) -
                ((x - self.mean[c]) ** 2) / (2 * self.var[c])
            )
            posteriors.append (prior + likelihood)

        return self.classes[np.argmax (posteriors)]

### 5. Train + test

Python
texts = [
    "I love this product",
    "This is amazing",
    "I hate this",
    "This is terrible"
]

labels = np.array ([1, 1, 0, 0])  # 1 = positive, 0 = negative

vocab = build_vocab (texts)

X = np.array ([vectorize (t, vocab) for t in texts])

model = NaiveBayes ()
model.fit (X, labels)

test = vectorize ("I love this", vocab)
print (model.predict ([test]))
"""
            artifacts = build_response_artifacts(sample)
            self.assertEqual(len(artifacts.get("code_blocks", [])), 5)
            contents = [str(block.get("content") or "") for block in artifacts["code_blocks"]]
            self.assertTrue(any("vec[vocab[word]] += 1" in content for content in contents))
            self.assertTrue(any("class NaiveBayes:" in content for content in contents))
            self.assertTrue(any('print (model.predict ([test]))' in content for content in contents))

        def test_fenced_python_merges_indented_tail(self) -> None:
            sample = """```python
result = []
for i in range (len (a)):
```
    result.append (a[i] + b[i])
"""
            artifacts = build_response_artifacts(sample)
            self.assertEqual(len(artifacts.get("code_blocks", [])), 1)
            block = artifacts["code_blocks"][0]
            content = block.get("content", "")
            self.assertIn("for i in range (len (a)):", content)
            self.assertIn("result.append (a[i] + b[i])", content)
            self.assertIn("\n    result.append (a[i] + b[i])", content)

        def test_fenced_python_merges_unindented_tail(self) -> None:
            sample = """```python
import numpy as np
```
# Example data
x = np.array([1, 2, 3])
print(x)
"""
            artifacts = build_response_artifacts(sample)
            self.assertEqual(len(artifacts.get("code_blocks", [])), 1)
            block = artifacts["code_blocks"][0]
            content = block.get("content", "")
            self.assertIn("import numpy as np", content)
            self.assertIn("x = np.array([1, 2, 3])", content)
            self.assertIn("print(x)", content)

        def test_fenced_python_tail_trims_markdown_heading_comment(self) -> None:
            sample = """```python
theta = [1, 2]
```
b, w = theta
print(b, w)

# Why NumPy Is Good For This
NumPy makes math easy.
"""
            artifacts = build_response_artifacts(sample)
            self.assertEqual(len(artifacts.get("code_blocks", [])), 1)
            content = artifacts["code_blocks"][0].get("content", "")
            self.assertIn("b, w = theta", content)
            self.assertIn("print(b, w)", content)
            self.assertNotIn("# Why NumPy Is Good For This", content)

        def test_tuple_assignment_is_detected_as_python(self) -> None:
            sample = "a, b = theta\nprint(a)"
            artifacts = build_response_artifacts(sample)
            self.assertEqual(len(artifacts.get("code_blocks", [])), 1)
            self.assertIn("a, b = theta", artifacts["code_blocks"][0].get("content", ""))

        def test_inferred_python_keeps_imports_and_array_closer_lines(self) -> None:
            sample = """import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score

# Example dataset
X = np.array([
    [1, 2],
    [2, 1],
    [3, 5],
    [6, 7],
    [7, 8],
    [8, 9]
])

y = np.array([0, 0, 0, 1, 1, 1])  # labels

# Split data
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.3, random_state=42
)
"""
            artifacts = build_response_artifacts(sample)
            self.assertEqual(len(artifacts.get("code_blocks", [])), 1)
            content = str(artifacts["code_blocks"][0].get("content") or "")
            self.assertIn("import numpy as np", content)
            self.assertIn("X = np.array([", content)
            self.assertIn("])", content)
            self.assertIn("y = np.array([0, 0, 0, 1, 1, 1])", content)

        def test_response_items_preserve_text_code_output_and_command_order(self) -> None:
            sample = """Intro

Python
x = 1
print(x)

Output:
1

pip install numpy
python app.py

Done
"""
            artifacts = build_response_artifacts(sample)
            items = list(artifacts.get("response_items") or [])
            self.assertEqual(
                [str(item.get("type") or "") for item in items],
                ["text", "code_block", "output_block", "command_block", "text"],
            )
            self.assertEqual(str(items[0].get("text") or ""), "Intro")
            self.assertIn("print(x)", str(items[1].get("content") or ""))
            self.assertEqual(str(items[2].get("label") or ""), "output")
            self.assertIn("python app.py", str(items[3].get("content") or ""))
            self.assertEqual(str(items[4].get("text") or ""), "Done")

        def test_invalid_math_equation_not_executable_python_cell(self) -> None:
            sample = "y = 2x + 1"
            artifacts = build_response_artifacts(sample)
            cells = collect_executable_cells_from_artifacts(
                artifacts=artifacts,
                turn_index=1,
                cell_counters={},
            )
            self.assertEqual(len(cells), 0)

        def test_heading_like_python_loop_label_is_not_command(self) -> None:
            sample = """Example comparison:
Python loop:

Python
result = a + b
"""
            artifacts = build_response_artifacts(sample)
            self.assertEqual(len(artifacts.get("command_blocks", [])), 0)

        def test_register_turn_cells_creates_runnable_python_cell(self) -> None:
            turn_result: Dict[str, Any] = {
                "assistant_text": "Python\nx = 1\nprint(x)\n",
                "artifacts": build_response_artifacts("Python\nx = 1\nprint(x)\n"),
            }
            store: Dict[str, Dict[str, Any]] = {}
            order: List[str] = []
            counters: Dict[str, int] = {}
            new_cells = register_turn_cells(turn_result, 1, store, order, counters)
            self.assertEqual(len(new_cells), 1)
            self.assertEqual(new_cells[0].get("id"), "py1")
            self.assertIn("print(x)", str(new_cells[0].get("content") or ""))

        def test_build_turn_action_refs_uses_response_item_order(self) -> None:
            turn_result: Dict[str, Any] = {
                "assistant_text": "Python\nx = 1\nprint(x)\n\npip install numpy\npython app.py\n",
                "artifacts": build_response_artifacts(
                    "Python\nx = 1\nprint(x)\n\npip install numpy\npython app.py\n"
                ),
            }
            store: Dict[str, Dict[str, Any]] = {}
            order: List[str] = []
            counters: Dict[str, int] = {}
            new_cells = register_turn_cells(turn_result, 1, store, order, counters)
            refs = build_turn_action_refs(turn_result, new_cells)
            self.assertEqual([str(ref.get("handle") or "") for ref in refs], ["@1", "@2"])
            self.assertEqual([str(ref.get("cell_id") or "") for ref in refs], ["py1", "sh1"])

        def test_render_interactive_turn_output_inlines_action_refs(self) -> None:
            assistant_text = "Python\nx = 1\nprint(x)\n"
            turn_result: Dict[str, Any] = {
                "assistant_text": assistant_text,
                "artifacts": build_response_artifacts(assistant_text),
            }
            store: Dict[str, Dict[str, Any]] = {}
            order: List[str] = []
            counters: Dict[str, int] = {}
            new_cells = register_turn_cells(turn_result, 1, store, order, counters)
            refs = build_turn_action_refs(turn_result, new_cells)
            rendered = render_interactive_turn_output(
                assistant_text=assistant_text,
                output_format="markdown",
                artifacts=turn_result.get("artifacts"),
                action_refs=refs,
            )
            self.assertIn("```python", rendered)
            self.assertIn("[@1 py1 python] /run @1 /show @1 /edit @1 /fork @1", rendered)

        def test_build_interactive_turn_view_tracks_ref_line_spans(self) -> None:
            assistant_text = "Python\nx = 1\nprint(x)\n\nBash\necho hi\n"
            turn_result: Dict[str, Any] = {
                "assistant_text": assistant_text,
                "artifacts": build_response_artifacts(assistant_text),
            }
            store: Dict[str, Dict[str, Any]] = {}
            order: List[str] = []
            counters: Dict[str, int] = {}
            new_cells = register_turn_cells(turn_result, 1, store, order, counters)
            refs = build_turn_action_refs(turn_result, new_cells)
            turn_view = build_interactive_turn_view(
                assistant_text=assistant_text,
                output_format="markdown",
                artifacts=turn_result.get("artifacts"),
                action_refs=refs,
            )
            ref_spans = dict(turn_view.get("ref_spans") or {})
            self.assertIn("@1", ref_spans)
            self.assertIn("py1", ref_spans)
            self.assertGreaterEqual(int(ref_spans["@1"].get("end_line") or 0), int(ref_spans["@1"].get("start_line") or 0))

        def test_build_live_repl_panel_lines_shows_ref_preview_for_run(self) -> None:
            assistant_text = "Python\nx = 1\nprint(x)\n\nDone\n"
            turn_result: Dict[str, Any] = {
                "assistant_text": assistant_text,
                "artifacts": build_response_artifacts(assistant_text),
            }
            store: Dict[str, Dict[str, Any]] = {}
            order: List[str] = []
            counters: Dict[str, int] = {}
            new_cells = register_turn_cells(turn_result, 1, store, order, counters)
            refs = build_turn_action_refs(turn_result, new_cells)
            turn_view = build_interactive_turn_view(
                assistant_text=assistant_text,
                output_format="markdown",
                artifacts=turn_result.get("artifacts"),
                action_refs=refs,
            )
            panel = build_live_repl_panel_lines(
                "/run @1",
                current_cell_id="py1",
                action_refs=refs,
                action_ref_index=build_action_ref_index(refs),
                turn_view=turn_view,
            )
            self.assertTrue(panel)
            self.assertIn("preview>", panel[0])
            self.assertIn("print(x)", "\n".join(panel))

        def test_build_live_repl_panel_lines_shows_help_list_for_slash(self) -> None:
            panel = build_live_repl_panel_lines(
                "/r",
                current_cell_id=None,
                action_refs=[],
                action_ref_index={},
                turn_view=None,
            )
            self.assertTrue(panel)
            self.assertIn("help>", panel[0])
            self.assertTrue(any("/run" in line for line in panel[1:]))

        def test_repl_completion_candidates_offer_ref_handles_for_run(self) -> None:
            refs = [
                {"handle": "@1", "cell_id": "py1", "language": "python"},
                {"handle": "@2", "cell_id": "sh1", "language": "bash"},
            ]
            context = repl_completion_candidates(
                "/run @",
                len("/run @"),
                current_cell_id="py1",
                action_refs=refs,
            )
            self.assertIsNotNone(context)
            suggestions = list((context or {}).get("suggestions") or [])
            self.assertIn("@1", suggestions)
            self.assertIn("@2", suggestions)

        def test_apply_repl_completion_state_cycles_run_ref_slot(self) -> None:
            refs = [
                {"handle": "@1", "cell_id": "py1", "language": "python"},
                {"handle": "@2", "cell_id": "py2", "language": "python"},
            ]
            buffer = "/run @"
            cursor = len(buffer)
            state = None
            buffer, cursor, state = apply_repl_completion_state(
                buffer,
                cursor,
                state,
                current_cell_id="py1",
                action_refs=refs,
            )
            self.assertEqual(buffer, "/run @1 ")
            buffer, cursor, state = apply_repl_completion_state(
                buffer,
                cursor,
                state,
                current_cell_id="py1",
                action_refs=refs,
            )
            self.assertEqual(buffer, "/run @2 ")

        def test_execute_cell_locally_python(self) -> None:
            cell = {
                "id": "py1",
                "language": "python",
                "content": "print('PLAYGROUND_OK')",
            }
            result = execute_cell_locally(cell, timeout_s=10)
            self.assertTrue(bool(result.get("ok")), msg=json.dumps(result))
            self.assertEqual(int(result.get("exit_code", -1)), 0, msg=json.dumps(result))
            self.assertIn("PLAYGROUND_OK", str(result.get("stdout") or ""))

        def test_execute_cell_locally_python_failure_sets_ok_false(self) -> None:
            cell = {
                "id": "py2",
                "language": "python",
                "content": "raise RuntimeError('PLAYGROUND_FAIL')",
            }
            result = execute_cell_locally(cell, timeout_s=10)
            self.assertFalse(bool(result.get("ok")), msg=json.dumps(result))
            self.assertNotEqual(int(result.get("exit_code", 0)), 0, msg=json.dumps(result))
            self.assertIn("PLAYGROUND_FAIL", str(result.get("error") or ""))
            self.assertIn("PLAYGROUND_FAIL", str(result.get("stderr") or ""))

        def test_execute_cell_locally_python_can_import_workspace_module(self) -> None:
            module_name = f"_sky_prompt_local_import_probe_{os.getpid()}_{int(time.time() * 1000)}"
            module_path = Path.cwd() / f"{module_name}.py"
            module_path.write_text("VALUE = 7\n", encoding="utf-8")
            try:
                cell = {
                    "id": "py3",
                    "language": "python",
                    "content": (
                        f"import {module_name}\n"
                        f"print({module_name}.VALUE)"
                    ),
                }
                result = execute_cell_locally(cell, workdir=Path.cwd(), timeout_s=10)
            finally:
                module_path.unlink(missing_ok=True)

            self.assertTrue(bool(result.get("ok")), msg=json.dumps(result))
            self.assertEqual(int(result.get("exit_code", -1)), 0, msg=json.dumps(result))
            self.assertIn("7", str(result.get("stdout") or ""))

        def test_create_local_python_tool_shims_creates_pip_wrapper(self) -> None:
            shim_dir = create_local_python_tool_shims("/tmp/python with spaces/bin/python3")
            try:
                pip_path = shim_dir / "pip"
                python3_path = shim_dir / "python3"
                self.assertTrue(pip_path.exists())
                self.assertTrue(python3_path.exists())
                pip_content = pip_path.read_text(encoding="utf-8")
                python_content = python3_path.read_text(encoding="utf-8")
            finally:
                shutil.rmtree(shim_dir, ignore_errors=True)

            self.assertIn("-m pip", pip_content)
            self.assertIn("python3", python_content)
            self.assertIn("python with spaces/bin/python3", pip_content)

        def test_execute_cell_locally_bash_uses_python_shims_when_path_missing(self) -> None:
            cell = {
                "id": "sh1",
                "language": "bash",
                "content": "python3 -c \"print('BASH_OK')\"",
            }
            with mock.patch.dict(os.environ, {"PATH": ""}, clear=False):
                result = execute_cell_locally(cell, timeout_s=10)
            self.assertTrue(bool(result.get("ok")), msg=json.dumps(result))
            self.assertEqual(int(result.get("exit_code", -1)), 0, msg=json.dumps(result))
            self.assertIn("BASH_OK", str(result.get("stdout") or ""))

        def test_execute_cell_locally_keyboard_interrupt_returns_cancelled(self) -> None:
            cell = {
                "id": "py4",
                "language": "python",
                "content": "print('slow')",
            }
            with mock.patch("subprocess.run", side_effect=KeyboardInterrupt):
                result = execute_cell_locally(cell, timeout_s=10)
            self.assertFalse(bool(result.get("ok")), msg=json.dumps(result))
            self.assertTrue(bool(result.get("cancelled")), msg=json.dumps(result))
            self.assertIn("cancelled", str(result.get("error") or "").lower())

        def test_resolve_credentials_prefers_sky_env_names(self) -> None:
            module_name = resolve_credentials.__module__
            with mock.patch.dict(
                os.environ,
                {
                    PRIMARY_API_KEY_ENV: "sky-key",
                    PRIMARY_AGENT_ID_ENV: "sky-agent",
                },
                clear=False,
            ):
                with mock.patch(f"{module_name}.parse_env_file", return_value={}):
                    api_key, agent_id, source = resolve_credentials(
                        api_key_arg=None,
                        agent_id_arg=None,
                        endpoint="https://example.invalid/mcp",
                        timeout=5,
                    )
            self.assertEqual(api_key, "sky-key")
            self.assertEqual(agent_id, "sky-agent")
            self.assertEqual(source, "flags/env")

        def test_resolve_credentials_supports_legacy_install_api_key(self) -> None:
            module_name = resolve_credentials.__module__
            with mock.patch.dict(
                os.environ,
                {
                    PRIMARY_API_KEY_ENV: "",
                    PRIMARY_AGENT_ID_ENV: "",
                },
                clear=False,
            ):
                with mock.patch(f"{module_name}.parse_env_file", return_value={}):
                    with mock.patch(
                        f"{module_name}.load_legacy_install_values",
                        return_value={LEGACY_INSTALL_API_KEY_ENV: "legacy-key"},
                    ):
                        with mock.patch(f"{module_name}.fetch_agent_id", return_value="agent-legacy"):
                            api_key, agent_id, source = resolve_credentials(
                                api_key_arg=None,
                                agent_id_arg=None,
                                endpoint="https://example.invalid/mcp",
                                timeout=5,
                            )
            self.assertEqual(api_key, "legacy-key")
            self.assertEqual(agent_id, "agent-legacy")
            self.assertEqual(source, "legacy-install-env+auto-discovered-from-api")

        def test_run_upstream_install_script_feeds_daemon_choice(self) -> None:
            module_name = run_upstream_install_script.__module__
            response_mock = mock.MagicMock()
            response_mock.read.return_value = b"#!/usr/bin/env bash\necho install\n"
            response_context = mock.MagicMock()
            response_context.__enter__.return_value = response_mock
            response_context.__exit__.return_value = False
            with mock.patch("urllib.request.urlopen", return_value=response_context):
                with mock.patch(
                    f"{module_name}.run_command_with_tty_reply",
                    return_value=(0, "Agent ID: agent-123\nAPI key: uc_live_demo\n"),
                ) as run_mock:
                    ok, detail, api_key, agent_id = run_upstream_install_script(timeout_s=30, daemon_choice="d")
            self.assertTrue(ok)
            self.assertEqual(detail, "installed")
            self.assertEqual(api_key, "uc_live_demo")
            self.assertEqual(agent_id, "agent-123")
            self.assertEqual(run_mock.call_args.kwargs.get("reply"), "d\n")


        def test_extract_agent_choices_prefers_connected_agents(self) -> None:
            payload = {
                "agents": [
                    {"agent_id": "agent-offline", "name": "Offline", "status": "offline"},
                    {"agent_id": "agent-online", "name": "Online", "status": "connected"},
                ]
            }
            choices = extract_agent_choices(payload)
            self.assertEqual(len(choices), 2)
            self.assertEqual(str(choices[0].get("agent_id") or ""), "agent-online")
            self.assertTrue(bool(choices[0].get("connected")))

        def test_upsert_env_file_value_replaces_existing_assignment(self) -> None:
            with tempfile.TemporaryDirectory() as tmpdir:
                env_path = Path(tmpdir) / ".env"
                env_path.write_text('SKY_API_KEY="old"\nSKY_AGENT_ID="old-agent"\n', encoding="utf-8")
                upsert_env_file_value(env_path, PRIMARY_AGENT_ID_ENV, "new-agent")
                payload = env_path.read_text(encoding="utf-8")
            self.assertIn('SKY_API_KEY="old"', payload)
            self.assertIn('SKY_AGENT_ID="new-agent"', payload)
            self.assertNotIn('SKY_AGENT_ID="old-agent"', payload)

        def test_maybe_migrate_primary_env_writes_missing_values(self) -> None:
            with tempfile.TemporaryDirectory() as tmpdir:
                env_path = Path(tmpdir) / ".env"
                changed = maybe_migrate_primary_env(
                    api_key="sky-key",
                    agent_id="agent-123",
                    env_path=env_path,
                )
                payload = env_path.read_text(encoding="utf-8")
            self.assertTrue(changed)
            self.assertIn('SKY_API_KEY="sky-key"', payload)
            self.assertIn('SKY_AGENT_ID="agent-123"', payload)

        def test_wait_for_agent_choices_retries_until_available(self) -> None:
            module_name = wait_for_agent_choices.__module__
            fetch_side_effect = [
                [],
                [
                    {
                        "agent_id": "agent-123",
                        "label": "MacBook",
                        "status": "connected",
                        "connected": True,
                    }
                ],
            ]
            with mock.patch(f"{module_name}.fetch_agent_choices", side_effect=fetch_side_effect):
                with mock.patch("time.sleep") as sleep_mock:
                    choices, error = wait_for_agent_choices(
                        api_key="sky-key",
                        endpoint="https://example.invalid/mcp",
                        timeout=5,
                        wait_s=1,
                        poll_s=0.1,
                    )
            self.assertIsNone(error)
            self.assertEqual(str(choices[0].get("agent_id") or ""), "agent-123")
            sleep_mock.assert_called_once()

        def test_wait_for_agent_connection_retries_until_connected(self) -> None:
            module_name = wait_for_agent_connection.__module__
            fetch_side_effect = [
                [{"agent_id": "agent-123", "connected": False}],
                [{"agent_id": "agent-123", "connected": True}],
            ]
            with mock.patch(f"{module_name}.fetch_agent_choices", side_effect=fetch_side_effect):
                with mock.patch("time.sleep") as sleep_mock:
                    connected, saw_agent, error = wait_for_agent_connection(
                        api_key="sky-key",
                        agent_id="agent-123",
                        endpoint="https://example.invalid/mcp",
                        timeout=5,
                        wait_s=1,
                        poll_s=0.1,
                    )
            self.assertTrue(connected)
            self.assertTrue(saw_agent)
            self.assertIsNone(error)
            sleep_mock.assert_called_once()

        def test_maybe_run_first_call_setup_writes_single_connected_agent(self) -> None:
            module_name = maybe_run_first_call_setup.__module__
            env_path = Path("/tmp/sky-setup-test.env")
            with mock.patch.object(sys.stdin, "isatty", return_value=True):
                with mock.patch.object(sys.stdout, "isatty", return_value=True):
                    with mock.patch(f"{module_name}.fetch_agent_choices", return_value=[
                        {
                            "agent_id": "agent-123",
                            "label": "MacBook",
                            "status": "connected",
                            "connected": True,
                        }
                    ]):
                        with mock.patch(f"{module_name}.upsert_env_file_value") as write_mock:
                            with mock.patch("builtins.print"):
                                api_key, agent_id, source = maybe_run_first_call_setup(
                                    api_key="sky-key",
                                    agent_id=None,
                                    endpoint="https://example.invalid/mcp",
                                    timeout=5,
                                    env_path=env_path,
                                )
            self.assertEqual(api_key, "sky-key")
            self.assertEqual(agent_id, "agent-123")
            self.assertEqual(source, "setup-wizard")
            write_mock.assert_called_once_with(env_path, PRIMARY_AGENT_ID_ENV, "agent-123")

        def test_maybe_run_first_call_setup_runs_installer_when_api_key_missing(self) -> None:
            module_name = maybe_run_first_call_setup.__module__
            env_path = Path("/tmp/sky-setup-install.env")
            with mock.patch.object(sys.stdin, "isatty", return_value=True):
                with mock.patch.object(sys.stdout, "isatty", return_value=True):
                    with mock.patch(f"{module_name}.prompt_for_confirmation", return_value=True):
                        with mock.patch(
                            f"{module_name}.run_upstream_install_script",
                            return_value=(True, "installed", None, None),
                        ):
                            with mock.patch(
                                f"{module_name}.resolve_credentials",
                                return_value=("sky-key", "agent-456", "auto-discovered-from-api"),
                            ):
                                with mock.patch(f"{module_name}.maybe_migrate_primary_env", return_value=True) as migrate_mock:
                                    with mock.patch("builtins.print"):
                                        api_key, agent_id, source = maybe_run_first_call_setup(
                                            api_key=None,
                                            agent_id=None,
                                            endpoint="https://example.invalid/mcp",
                                            timeout=5,
                                            env_path=env_path,
                                        )
            self.assertEqual(api_key, "sky-key")
            self.assertEqual(agent_id, "agent-456")
            self.assertEqual(source, "setup-wizard")
            migrate_mock.assert_called_once_with(api_key="sky-key", agent_id="agent-456", env_path=env_path)

        def test_maybe_run_first_call_setup_honors_assume_yes_env(self) -> None:
            module_name = maybe_run_first_call_setup.__module__
            env_path = Path("/tmp/sky-setup-auto-install.env")
            with mock.patch.object(sys.stdin, "isatty", return_value=True):
                with mock.patch.object(sys.stdout, "isatty", return_value=True):
                    with mock.patch.dict(os.environ, {SETUP_ASSUME_YES_ENV: "1"}, clear=False):
                        with mock.patch(f"{module_name}.prompt_for_confirmation") as prompt_mock:
                            with mock.patch(
                                f"{module_name}.run_upstream_install_script",
                                return_value=(True, "installed", None, None),
                            ):
                                with mock.patch(
                                    f"{module_name}.resolve_credentials",
                                    return_value=("sky-key", "agent-456", "auto-discovered-from-api"),
                                ):
                                    with mock.patch(f"{module_name}.maybe_migrate_primary_env", return_value=True):
                                        with mock.patch("builtins.print"):
                                            api_key, agent_id, source = maybe_run_first_call_setup(
                                                api_key=None,
                                                agent_id=None,
                                                endpoint="https://example.invalid/mcp",
                                                timeout=5,
                                                env_path=env_path,
                                            )
            self.assertEqual(api_key, "sky-key")
            self.assertEqual(agent_id, "agent-456")
            self.assertEqual(source, "setup-wizard")
            prompt_mock.assert_not_called()

        def test_maybe_run_first_call_setup_uses_installer_agent_id_when_available(self) -> None:
            module_name = maybe_run_first_call_setup.__module__
            env_path = Path("/tmp/sky-setup-install-wait.env")
            with mock.patch.object(sys.stdin, "isatty", return_value=True):
                with mock.patch.object(sys.stdout, "isatty", return_value=True):
                    with mock.patch(f"{module_name}.prompt_for_confirmation", return_value=True):
                        with mock.patch(
                            f"{module_name}.run_upstream_install_script",
                            return_value=(True, "installed", "sky-key", "agent-789"),
                        ):
                            with mock.patch(
                                f"{module_name}.resolve_credentials",
                                return_value=("sky-key", None, "legacy-install-env"),
                            ):
                                with mock.patch(f"{module_name}.fetch_agent_choices", return_value=[]):
                                    with mock.patch(
                                        f"{module_name}.wait_for_agent_choices",
                                        return_value=([], None),
                                    ) as wait_mock:
                                        with mock.patch(f"{module_name}.upsert_env_file_value") as write_mock:
                                            with mock.patch(f"{module_name}.maybe_migrate_primary_env", return_value=True) as migrate_mock:
                                                with mock.patch("builtins.print"):
                                                    api_key, agent_id, source = maybe_run_first_call_setup(
                                                        api_key=None,
                                                        agent_id=None,
                                                        endpoint="https://example.invalid/mcp",
                                                        timeout=5,
                                                        env_path=env_path,
                                                    )
            self.assertEqual(api_key, "sky-key")
            self.assertEqual(agent_id, "agent-789")
            self.assertEqual(source, "setup-wizard")
            wait_mock.assert_not_called()
            write_mock.assert_not_called()
            migrate_mock.assert_called_once_with(api_key="sky-key", agent_id="agent-789", env_path=env_path)

        def test_install_alias_launcher_sets_cli_name(self) -> None:
            with tempfile.TemporaryDirectory() as tmpdir:
                alias_path, created = install_alias_launcher(
                    alias_name="sk",
                    alias_dir=Path(tmpdir),
                    target_script=Path.cwd() / "sky_prompt.py",
                    force=False,
                )
                content = alias_path.read_text(encoding="utf-8")
            self.assertTrue(created)
            self.assertIn('SKY_CLI_NAME="sk" exec python3 "', content)

        def test_resolve_unchained_command_explicit(self) -> None:
            resolved = resolve_unchained_command("uvx unchainedsky-cli")
            self.assertEqual(resolved, ["uvx", "unchainedsky-cli"])

        def test_resolve_uv_command_prefers_path(self) -> None:
            with mock.patch("shutil.which", return_value="/usr/local/bin/uv"):
                resolved = resolve_uv_command()
            self.assertEqual(resolved, ["/usr/local/bin/uv"])

        def test_install_unchained_with_uv_runs_tool_install(self) -> None:
            with mock.patch("subprocess.run") as run_mock:
                run_mock.return_value = mock.Mock(returncode=0, stdout="ok", stderr="")
                install_unchained_with_uv(["uv"], timeout_s=30)
            command = run_mock.call_args.args[0]
            self.assertEqual(
                command,
                ["uv", "tool", "install", "--force", "--python", "3.10", "unchainedsky-cli"],
            )

        def test_install_pyreplab_with_uv_runs_tool_install(self) -> None:
            with mock.patch("subprocess.run") as run_mock:
                run_mock.return_value = mock.Mock(returncode=0, stdout="ok", stderr="")
                install_pyreplab_with_uv(["uv"], timeout_s=30)
            command = run_mock.call_args.args[0]
            self.assertEqual(
                command,
                ["uv", "tool", "install", "--force", "--python", "3.10", "pyreplab"],
            )

        def test_ensure_local_setup_tooling_allows_missing_pyreplab(self) -> None:
            module_name = ensure_local_setup_tooling.__module__
            with mock.patch("builtins.print"):
                with mock.patch(f"{module_name}.resolve_unchained_command", side_effect=[None, ["unchained"]]):
                    with mock.patch(f"{module_name}.resolve_pyreplab_command", side_effect=[None, None]):
                        with mock.patch(f"{module_name}.install_unchained_with_uv") as install_unchained_mock:
                            with mock.patch(
                                f"{module_name}.install_pyreplab_with_uv",
                                side_effect=MCPError("uv tool install failed: missing wheel"),
                            ) as install_pyreplab_mock:
                                (
                                    resolved_unchained,
                                    resolved_pyreplab,
                                    installed_now,
                                    warnings,
                                ) = ensure_local_setup_tooling(
                                    ["uv"],
                                    timeout_s=30,
                                )
            install_unchained_mock.assert_called_once()
            install_pyreplab_mock.assert_called_once()
            self.assertEqual(resolved_unchained, ["unchained"])
            self.assertIsNone(resolved_pyreplab)
            self.assertTrue(installed_now)
            self.assertTrue(any("pyreplab install skipped" in warning for warning in warnings))
            self.assertTrue(any("fall back to local" in warning for warning in warnings))

        def test_launch_chatgpt_with_unchained_builds_launch_command(self) -> None:
            with mock.patch("subprocess.run") as run_mock:
                run_mock.return_value = mock.Mock(returncode=0, stdout="Chrome started\n", stderr="")
                output = launch_chatgpt_with_unchained(
                    ["unchained"],
                    port=9333,
                    profile="Profile 3",
                    url="https://chatgpt.com",
                    timeout_s=10,
                )
            command = run_mock.call_args.args[0]
            self.assertEqual(
                command,
                [
                    "unchained",
                    "--port",
                    "9333",
                    "launch",
                    "--use-profile",
                    "--profile",
                    "Profile 3",
                    "https://chatgpt.com",
                ],
            )
            self.assertIn("Chrome started", output)

        def test_local_cli_client_type_newline_uses_press_enter(self) -> None:
            client = LocalCLIClient(["unchained"], port=9333, tab="chatgpt", timeout=5)
            with mock.patch("subprocess.run") as run_mock:
                run_mock.return_value = mock.Mock(returncode=0, stdout="Pressed Enter\n", stderr="")
                result = client.call_tool("cdp_type", {"text": "\n"})
            command = run_mock.call_args.args[0]
            self.assertEqual(command[:5], ["unchained", "--port", "9333", "--tab", "chatgpt"])
            self.assertEqual(command[-1], "press_enter")
            self.assertIn("Pressed Enter", extract_text(result))

        def test_local_cli_client_initialize_requires_running_browser(self) -> None:
            client = LocalCLIClient(["unchained"], port=9222, tab="auto", timeout=5)
            with mock.patch("subprocess.run") as run_mock:
                run_mock.return_value = mock.Mock(returncode=1, stdout="", stderr="Chrome not running on port 9222\n")
                with self.assertRaises(MCPError) as exc:
                    client.initialize()
            self.assertIn("Chrome not running on port 9222", str(exc.exception))
            self.assertIn("launch --use-profile", str(exc.exception))

        def test_local_cli_client_initialize_auto_launches_browser(self) -> None:
            module_name = LocalCLIClient.__module__
            client = LocalCLIClient(
                ["unchained"],
                port=9224,
                tab="auto",
                chrome_profile="Profile 3",
                startup_url="https://chatgpt.com",
                auto_launch=True,
                timeout=5,
            )
            with mock.patch.object(
                client,
                "_run",
                side_effect=[MCPError("Chrome not running on port 9224"), "Chrome -> Chrome"],
            ) as run_mock:
                with mock.patch(
                    f"{module_name}.launch_chatgpt_with_unchained",
                    return_value="Chrome started\n",
                ) as launch_mock:
                    client.initialize()
            self.assertTrue(client.did_auto_launch)
            self.assertEqual(client.last_launch_output, "Chrome started")
            self.assertEqual(run_mock.call_count, 2)
            launch_mock.assert_called_once_with(
                ["unchained"],
                port=9224,
                profile="Profile 3",
                url="https://chatgpt.com",
                timeout_s=20,
            )

        def test_local_cli_client_missing_command_mentions_setup(self) -> None:
            client = LocalCLIClient([], port=9222, tab="auto", timeout=5)
            with self.assertRaises(MCPError) as exc:
                client.initialize()
            self.assertIn("./sky --setup", str(exc.exception))

        def test_resolve_pyreplab_command_explicit(self) -> None:
            resolved = resolve_pyreplab_command("pyreplab --workdir /tmp/demo")
            self.assertEqual(resolved, ["pyreplab", "--workdir", "/tmp/demo"])

        def test_start_pyreplab_session_empty_command(self) -> None:
            result = start_pyreplab_session([], Path.cwd(), timeout_s=1)
            self.assertFalse(bool(result.get("ok")))
            self.assertIn("empty", str(result.get("error") or ""))

        def test_stop_pyreplab_session_empty_command(self) -> None:
            result = stop_pyreplab_session([], Path.cwd(), timeout_s=1)
            self.assertFalse(bool(result.get("ok")))
            self.assertIn("empty", str(result.get("error") or ""))

        def test_allocate_pyreplab_session_dir_has_prefix(self) -> None:
            session_dir = allocate_pyreplab_session_dir(Path.cwd())
            self.assertIn("sky_prompt_", session_dir)

        def test_start_pyreplab_session_accepts_session_dir(self) -> None:
            with mock.patch("subprocess.run") as run_mock:
                run_mock.return_value = mock.Mock(returncode=0)
                result = start_pyreplab_session(
                    ["pyreplab"],
                    Path.cwd(),
                    session_dir="/tmp/pyreplab/fixed_session",
                    timeout_s=1,
                )
            self.assertTrue(bool(result.get("ok")))
            kwargs = run_mock.call_args.kwargs
            env = kwargs.get("env") or {}
            self.assertEqual(env.get("PYREPLAB_DIR"), "/tmp/pyreplab/fixed_session")

        def test_resolve_pyreplab_session_dir_empty_command(self) -> None:
            self.assertIsNone(resolve_pyreplab_session_dir([], Path.cwd(), timeout_s=1))

        def test_execute_pyreplab_code_empty_code(self) -> None:
            result = execute_pyreplab_code("", ["pyreplab"], Path.cwd(), timeout_s=1)
            self.assertFalse(bool(result.get("ok")))
            self.assertIn("empty code", str(result.get("error") or ""))

        def test_execute_pyreplab_code_uses_explicit_session_dir(self) -> None:
            with mock.patch(__name__ + ".resolve_pyreplab_session_dir") as resolve_mock:
                with mock.patch(__name__ + ".start_pyreplab_session") as start_mock:
                    with mock.patch("subprocess.run") as run_mock:
                        resolve_mock.return_value = "/tmp/pyreplab/derived"
                        start_mock.return_value = {"ok": True, "command": ["pyreplab", "start"], "exit_code": 0}
                        run_mock.return_value = mock.Mock(returncode=0, stdout="", stderr="")
                        result = execute_pyreplab_code(
                            "print('ok')",
                            ["pyreplab"],
                            Path.cwd(),
                            session_dir="/tmp/pyreplab/fixed",
                            timeout_s=2,
                        )
            self.assertTrue(bool(result.get("ok")))
            resolve_mock.assert_not_called()
            start_kwargs = start_mock.call_args.kwargs
            self.assertEqual(start_kwargs.get("session_dir"), "/tmp/pyreplab/fixed")
            run_env = run_mock.call_args.kwargs.get("env") or {}
            self.assertEqual(run_env.get("PYREPLAB_DIR"), "/tmp/pyreplab/fixed")

        def test_execute_pyreplab_code_keyboard_interrupt_returns_cancelled(self) -> None:
            with mock.patch(__name__ + ".start_pyreplab_session") as start_mock:
                with mock.patch("subprocess.run", side_effect=KeyboardInterrupt):
                    start_mock.return_value = {"ok": True, "command": ["pyreplab", "start"], "exit_code": 0}
                    result = execute_pyreplab_code(
                        "print('ok')",
                        ["pyreplab"],
                        Path.cwd(),
                        timeout_s=2,
                    )
            self.assertFalse(bool(result.get("ok")), msg=json.dumps(result))
            self.assertTrue(bool(result.get("cancelled")), msg=json.dumps(result))
            self.assertIn("cancelled", str(result.get("error") or "").lower())

        def test_execute_cell_with_pyreplab_requires_python(self) -> None:
            cell = {"id": "sh1", "language": "bash", "content": "echo hi"}
            result = execute_cell_with_pyreplab(
                cell=cell,
                pyreplab_cmd=["pyreplab"],
                workdir=Path.cwd(),
                timeout_s=5,
            )
            self.assertFalse(bool(result.get("ok")))
            self.assertIn("only supports python", str(result.get("error") or ""))

        def test_execute_cell_with_pyreplab_requires_command(self) -> None:
            cell = {"id": "py1", "language": "python", "content": "print('hi')"}
            result = execute_cell_with_pyreplab(
                cell=cell,
                pyreplab_cmd=[],
                workdir=Path.cwd(),
                timeout_s=5,
            )
            self.assertFalse(bool(result.get("ok")))
            self.assertIn("not configured", str(result.get("error") or ""))

        def test_extract_cell_preview_line_uses_last_non_empty_line(self) -> None:
            content = "import numpy as np\nx = np.array([1,2,3])\n\nprint(x)\n"
            self.assertEqual(extract_cell_preview_line(content), "print(x)")

        def test_summarize_cell_preview_uses_head_and_tail_lines(self) -> None:
            content = (
                "import numpy as np\n"
                "from sklearn.ensemble import RandomForestClassifier\n"
                "\n"
                "pred = model.predict(X_test)\n"
                "print('Accuracy', pred)\n"
            )
            preview = summarize_cell_preview(content, max_head_lines=2, limit=160)
            self.assertIn("import numpy as np", preview)
            self.assertIn("from sklearn.ensemble import RandomForestClassifier", preview)
            self.assertIn("print('Accuracy', pred)", preview)
            self.assertIn("...", preview)

        def test_resolve_run_request_uses_current_cell_when_omitted(self) -> None:
            cell_id, timeout_s, error = resolve_run_request(["/run"], current_cell_id="py2")
            self.assertEqual(cell_id, "py2")
            self.assertEqual(timeout_s, 30)
            self.assertIsNone(error)

        def test_resolve_run_request_allows_timeout_without_cell_id(self) -> None:
            cell_id, timeout_s, error = resolve_run_request(["/run", "45"], current_cell_id="py2")
            self.assertEqual(cell_id, "py2")
            self.assertEqual(timeout_s, 45)
            self.assertIsNone(error)

        def test_resolve_run_request_accepts_response_ref(self) -> None:
            cell_id, timeout_s, error = resolve_run_request(
                ["/run", "@1"],
                current_cell_id="py2",
                action_ref_index={"@1": "py7"},
            )
            self.assertEqual(cell_id, "py7")
            self.assertEqual(timeout_s, 30)
            self.assertIsNone(error)

        def test_format_repl_help_lines_includes_focus_command(self) -> None:
            lines = format_repl_help_lines("/foc")
            self.assertTrue(lines)
            self.assertTrue(any("/focus <cell|@ref>" in line for line in lines))

        def test_format_repl_input_line_history_view_prefers_start(self) -> None:
            line, cursor_col = format_repl_input_line(
                "> ",
                "abcdefghijklmnopqrstuvwxyz",
                cursor=len("abcdefghijklmnopqrstuvwxyz"),
                width=12,
                prefer_start=True,
            )
            self.assertEqual(line, "> abcdefg...")
            self.assertEqual(cursor_col, len(line))

        def test_format_repl_input_line_default_shows_tail_near_cursor(self) -> None:
            line, cursor_col = format_repl_input_line(
                "> ",
                "abcdefghijklmnopqrstuvwxyz",
                cursor=len("abcdefghijklmnopqrstuvwxyz"),
                width=12,
            )
            self.assertEqual(line, "> ...tuvwxyz")
            self.assertEqual(cursor_col, len(line))

        def test_normalize_repl_pasted_text_preserves_multiline_traceback(self) -> None:
            raw = (
                "run error: boom\r\n"
                "Traceback (most recent call last):\r\n"
                "  File \"<stdin>\", line 1, in <module>\r\n"
                "ValueError: bad value\r\n"
            )
            normalized = normalize_repl_pasted_text(raw)
            self.assertIn("run error: boom", normalized)
            self.assertIn("\nTraceback (most recent call last):", normalized)
            self.assertIn("\nValueError: bad value", normalized)
            self.assertNotIn("\r", normalized)

        def test_format_repl_input_line_displays_newlines_as_escaped_markers(self) -> None:
            line, cursor_col = format_repl_input_line(
                "> ",
                "first line\nsecond line",
                cursor=len("first line\nsecond line"),
                width=80,
            )
            self.assertEqual(line, "> first line\\nsecond line")
            self.assertEqual(cursor_col, len(line))

        def test_read_bracketed_paste_stops_at_terminator(self) -> None:
            payload = "first line\nsecond line"
            read_fd, write_fd = os.pipe()
            try:
                os.write(write_fd, (payload + "\x1b[201~tail").encode("utf-8"))
                os.close(write_fd)
                write_fd = -1
                result = read_bracketed_paste(read_fd)
            finally:
                os.close(read_fd)
                if write_fd >= 0:
                    os.close(write_fd)
            self.assertEqual(result, payload)

        def test_resolve_run_request_rejects_missing_current_cell(self) -> None:
            cell_id, timeout_s, error = resolve_run_request(["/run"], current_cell_id=None)
            self.assertIsNone(cell_id)
            self.assertIsNone(timeout_s)
            self.assertIn("no current cell", str(error or ""))

        def test_browser_foreground_mode_defaults_by_platform(self) -> None:
            with mock.patch.object(sys, "platform", "darwin"):
                self.assertEqual(browser_foreground_mode({}), "submit")
                self.assertEqual(browser_foreground_mode({"SKY_FOREGROUND_BROWSER": "0"}), "off")
                self.assertEqual(browser_foreground_mode({"SKY_FOREGROUND_BROWSER": "1"}), "poll")
            with mock.patch.object(sys, "platform", "linux"):
                self.assertEqual(browser_foreground_mode({}), "off")
                self.assertEqual(browser_foreground_mode({"SKY_FOREGROUND_BROWSER": "1"}), "off")

        def test_browser_window_parking_enabled_defaults_on_darwin(self) -> None:
            with mock.patch.object(sys, "platform", "darwin"):
                self.assertTrue(browser_window_parking_enabled({}))
                self.assertFalse(browser_window_parking_enabled({"SKY_FOREGROUND_BROWSER_PARK": "0"}))

        def test_offscreen_window_bounds_keeps_window_size(self) -> None:
            self.assertEqual(
                offscreen_window_bounds((100, 50, 500, 450), margin=120),
                (-520, 50, -120, 450),
            )

        def test_terminal_application_name_from_env_maps_apple_terminal(self) -> None:
            self.assertEqual(
                terminal_application_name_from_env({"TERM_PROGRAM": "Apple_Terminal"}),
                "Terminal",
            )

        def test_foreground_browser_context_hold_restores_original_frontmost_app(self) -> None:
            with mock.patch(__name__ + ".activate_application", return_value=True) as activate_mock:
                with mock.patch(__name__ + ".current_frontmost_application_name", return_value="Terminal"):
                    with mock.patch(__name__ + ".browser_window_parking_enabled", return_value=False):
                        with mock.patch.dict(
                            os.environ,
                            {"TERM_PROGRAM": "Apple_Terminal", "SKY_BROWSER_APP": "Google Chrome"},
                            clear=False,
                        ):
                            with foreground_browser_context("hold"):
                                pass
            self.assertEqual(
                activate_mock.call_args_list,
                [mock.call("Google Chrome"), mock.call("Terminal")],
            )

        def test_foreground_browser_context_hold_does_not_restore_when_browser_already_frontmost(self) -> None:
            with mock.patch(__name__ + ".activate_application", return_value=True) as activate_mock:
                with mock.patch(__name__ + ".current_frontmost_application_name", return_value="Google Chrome"):
                    with mock.patch.dict(
                        os.environ,
                        {"TERM_PROGRAM": "Apple_Terminal", "SKY_BROWSER_APP": "Google Chrome"},
                        clear=False,
                    ):
                        with foreground_browser_context("hold"):
                            pass
            self.assertEqual(activate_mock.call_args_list, [])

        def test_foreground_browser_context_hold_parks_window_offscreen_when_focus_is_stolen(self) -> None:
            with mock.patch(__name__ + ".activate_application", return_value=True) as activate_mock:
                with mock.patch(__name__ + ".current_frontmost_application_name", return_value="Terminal"):
                    with mock.patch(__name__ + ".browser_window_parking_enabled", return_value=True):
                        with mock.patch(__name__ + ".front_window_bounds", return_value=(100, 50, 500, 450)):
                            with mock.patch(__name__ + ".set_front_window_bounds", return_value=True) as bounds_mock:
                                with mock.patch.dict(
                                    os.environ,
                                    {"TERM_PROGRAM": "Apple_Terminal", "SKY_BROWSER_APP": "Google Chrome"},
                                    clear=False,
                                ):
                                    with foreground_browser_context("hold"):
                                        pass
            self.assertEqual(
                bounds_mock.call_args_list,
                [
                    mock.call("Google Chrome", (-520, 50, -120, 450)),
                    mock.call("Google Chrome", (100, 50, 500, 450)),
                ],
            )
            self.assertEqual(
                activate_mock.call_args_list,
                [mock.call("Google Chrome"), mock.call("Terminal")],
            )

        def test_foreground_browser_context_pulse_scope_does_not_activate_apps(self) -> None:
            with mock.patch(__name__ + ".activate_application", return_value=True) as activate_mock:
                with mock.patch(__name__ + ".current_frontmost_application_name", return_value="Google Chrome"):
                    with mock.patch.dict(
                        os.environ,
                        {"TERM_PROGRAM": "Apple_Terminal", "SKY_BROWSER_APP": "Google Chrome"},
                        clear=False,
                    ):
                        with foreground_browser_context("pulse"):
                            pass
            self.assertEqual(activate_mock.call_args_list, [])

        def test_foreground_browser_context_is_noop_off_darwin(self) -> None:
            with mock.patch.object(sys, "platform", "linux"):
                with mock.patch(__name__ + ".activate_application", return_value=True) as activate_mock:
                    with foreground_browser_context("hold"):
                        pass
            self.assertEqual(activate_mock.call_args_list, [])

        def test_call_js_expression_pulses_browser_in_pulse_context(self) -> None:
            client = MCPClient(endpoint="https://example.invalid/mcp", api_key="test")
            fake_result = {"content": [{"type": "text", "text": "{\"ok\":true}"}]}
            with mock.patch(__name__ + ".activate_application", return_value=True) as activate_mock:
                with mock.patch(__name__ + ".call_tool_variants", return_value=("js_eval", fake_result)):
                    with mock.patch(__name__ + ".current_frontmost_application_name", return_value="Terminal"):
                        with mock.patch.dict(
                            os.environ,
                            {"TERM_PROGRAM": "Apple_Terminal", "SKY_BROWSER_APP": "Google Chrome"},
                            clear=False,
                        ):
                            with foreground_browser_context("pulse"):
                                call_js_expression(
                                    client=client,
                                    js_tools=["js_eval"],
                                    agent_id="agent-test",
                                    expression="1 + 1",
                                    label="js test",
                                )
            self.assertEqual(
                activate_mock.call_args_list,
                [mock.call("Google Chrome"), mock.call("Terminal")],
            )

        def test_call_js_expression_does_not_pulse_browser_in_hold_context(self) -> None:
            client = MCPClient(endpoint="https://example.invalid/mcp", api_key="test")
            fake_result = {"content": [{"type": "text", "text": "{\"ok\":true}"}]}
            with mock.patch(__name__ + ".activate_application", return_value=True) as activate_mock:
                with mock.patch(__name__ + ".call_tool_variants", return_value=("js_eval", fake_result)):
                    with mock.patch(__name__ + ".current_frontmost_application_name", return_value="Terminal"):
                        with mock.patch(__name__ + ".browser_window_parking_enabled", return_value=False):
                            with mock.patch.dict(
                                os.environ,
                                {"TERM_PROGRAM": "Apple_Terminal", "SKY_BROWSER_APP": "Google Chrome"},
                                clear=False,
                            ):
                                with foreground_browser_context("hold"):
                                    call_js_expression(
                                        client=client,
                                        js_tools=["js_eval"],
                                        agent_id="agent-test",
                                        expression="1 + 1",
                                        label="js test",
                                    )
            self.assertEqual(
                activate_mock.call_args_list,
                [mock.call("Google Chrome"), mock.call("Terminal")],
            )

        def test_probe_expression_includes_assistant_action_anchor_fallback(self) -> None:
            expr = build_assistant_probe_expression()
            self.assertIn("assistantActionAnchors", expr)
            self.assertIn("good response", expr)
            self.assertIn("more actions", expr)
            self.assertIn("assistant_shell_count", expr)
            self.assertIn("response_nav_count", expr)
            self.assertIn("empty_assistant_shell", expr)

        def test_snapshot_expression_uses_action_anchors_and_avoids_generic_div_candidate(self) -> None:
            expr = build_assistant_snapshot_expression()
            self.assertIn("assistantActionAnchors", expr)
            self.assertIn("good response", expr)
            self.assertIn(".markdown, [class*=\"markdown\"], pre, p, li", expr)
            self.assertNotIn(".markdown, [class*=\"markdown\"], pre, p, li, div", expr)

        def test_prompt_expression_uses_contenteditable_insert_text(self) -> None:
            expr = build_prompt_expression("hello", submit=True)
            self.assertIn('document.execCommand("insertText", false, text)', expr)
            self.assertIn('range.selectNodeContents(el)', expr)
            self.assertIn('inputType: "insertText"', expr)
            self.assertNotIn("range.collapse(false)", expr)
            self.assertIn("fallbackSubmitButton", expr)
            self.assertIn("/(send prompt|send|submit)/", expr)

        def test_send_button_state_expression_targets_send_button(self) -> None:
            expr = build_send_button_state_expression()
            self.assertIn('button,[role=\'button\']', expr)
            self.assertIn('/(send prompt|send|submit)/', expr)
            self.assertIn('enabled: !isDisabled(send)', expr)

        def test_network_request_spy_expression_wraps_fetch_and_xhr(self) -> None:
            expr = build_network_request_spy_install_expression()
            self.assertIn("window.fetch =", expr)
            self.assertIn("XMLHttpRequest.prototype.open", expr)
            self.assertIn("window.__skyNetworkSpyLog", expr)

        def test_read_page_network_request_spy_log_returns_dict_rows(self) -> None:
            with mock.patch(
                __name__ + ".call_js_expression",
                return_value=(
                    "js_eval",
                    {},
                    '{"ok":true,"log":[{"kind":"fetch","url":"https://example.invalid"},{"bad":true},"nope"]}',
                    {"ok": True, "log": [{"kind": "fetch", "url": "https://example.invalid"}, {"bad": True}, "nope"]},
                ),
            ):
                rows = read_page_network_request_spy_log(
                    client=mock.Mock(),
                    agent_id="agent-test",
                    js_tools=["js_eval"],
                )
            self.assertEqual(
                rows,
                [{"kind": "fetch", "url": "https://example.invalid"}, {"bad": True}],
            )

        def test_wait_for_visible_send_button_state_retries_until_visible(self) -> None:
            with mock.patch(
                __name__ + ".read_visible_send_button_state",
                side_effect=[
                    {"ok": False, "visible": False},
                    {"ok": True, "visible": True, "enabled": True, "x": 10, "y": 20},
                ],
            ) as read_mock:
                with mock.patch(__name__ + ".time.sleep") as sleep_mock:
                    state = wait_for_visible_send_button_state(
                        client=mock.Mock(),
                        agent_id="agent-test",
                        js_tools=["js_eval"],
                        timeout_s=0.2,
                        poll_interval_s=0.01,
                    )
            self.assertTrue(state.get("visible"))
            self.assertEqual(read_mock.call_count, 2)
            sleep_mock.assert_called_once()

        def test_dispatch_prompt_prefers_native_submit_when_click_tools_exist(self) -> None:
            client = MCPClient(endpoint="https://example.invalid/mcp", api_key="test")
            with mock.patch(__name__ + ".read_assistant_probe", return_value={}) as probe_mock:
                with mock.patch(
                    __name__ + ".cdp_fallback_submit",
                    return_value={"ok": True, "submitted": True, "mode": "js_fill+cdp_click"},
                ) as native_submit_mock:
                    with mock.patch(__name__ + ".call_js_expression") as call_js_mock:
                        with mock.patch(
                            __name__ + ".wait_for_assistant_response",
                            return_value=(None, True, False, True),
                        ):
                            with mock.patch(
                                __name__ + ".capture_final_assistant_text",
                                return_value=(None, None),
                            ):
                                with mock.patch(
                                    __name__ + ".summarize_missing_assistant_response",
                                    return_value="assistant> (timed out waiting after fallback submit)",
                                ):
                                    with mock.patch("builtins.print"):
                                        dispatch_prompt(
                                            client=client,
                                            agent_id="agent-test",
                                            prompt="hello",
                                            js_tools=["js_eval"],
                                            click_tools=["cdp_click"],
                                            type_tools=["cdp_type"],
                                            ddm_tools=["ddm"],
                                            submit=True,
                                            layout_text="",
                                            wait_for_response=True,
                                        )
            native_submit_mock.assert_called_once()
            call_js_mock.assert_not_called()
            self.assertGreaterEqual(probe_mock.call_count, 2)

        def test_summarize_missing_assistant_response_prefers_empty_shell_messages(self) -> None:
            self.assertEqual(
                summarize_missing_assistant_response(
                    {"empty_assistant_shell": True, "response_nav_count": 2},
                    timed_out=True,
                    fallback_used=True,
                    render_complete=False,
                ),
                "assistant> (ChatGPT created empty response variants without rendered text)",
            )
            self.assertEqual(
                summarize_missing_assistant_response(
                    {"empty_assistant_shell": True, "response_nav_count": 0},
                    timed_out=True,
                    fallback_used=False,
                    render_complete=False,
                ),
                "assistant> (ChatGPT created an empty assistant turn without rendered text)",
            )

        def test_mcp_client_next_rpc_id_is_unique(self) -> None:
            client = MCPClient(endpoint="https://example.invalid/mcp", api_key="test")
            ids = {client._next_rpc_id("tool-call-js_eval") for _ in range(8)}
            self.assertEqual(len(ids), 8)

        def test_mcp_client_call_tool_uses_unique_ids(self) -> None:
            client = MCPClient(endpoint="https://example.invalid/mcp", api_key="test")
            client.session_id = "sid-test"
            seen_ids: List[str] = []

            def fake_rpc(payload: Dict[str, Any], include_session: bool, allow_empty: bool) -> Dict[str, Any]:
                seen_ids.append(str(payload.get("id") or ""))
                self.assertTrue(include_session)
                self.assertFalse(allow_empty)
                return {"result": {"content": []}}

            with mock.patch.object(client, "_rpc_request", side_effect=fake_rpc):
                client.call_tool("js_eval", {"expression": "1+1"})
                client.call_tool("js_eval", {"expression": "2+2"})

            self.assertEqual(len(seen_ids), 2)
            self.assertNotEqual(seen_ids[0], seen_ids[1])
            self.assertTrue(seen_ids[0].startswith("tool-call-js_eval-"))
            self.assertTrue(seen_ids[1].startswith("tool-call-js_eval-"))

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ResponseFormattingSelfTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def history_preview(text: str, limit: int = 96) -> str:
    collapsed = " ".join(str(text or "").split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: max(8, limit - 1)] + "…"


def language_to_cell_prefix(language: str) -> str:
    normalized = normalize_code_language(language)
    return CELL_PREFIX_BY_LANGUAGE.get(normalized, "code")


def language_to_cell_extension(language: str) -> str:
    normalized = normalize_code_language(language)
    return CELL_EXTENSION_BY_LANGUAGE.get(normalized, ".txt")


def next_cell_id(cell_counters: Dict[str, int], language: str) -> str:
    prefix = language_to_cell_prefix(language)
    next_index = int(cell_counters.get(prefix, 0)) + 1
    cell_counters[prefix] = next_index
    return f"{prefix}{next_index}"


def collect_executable_cells_from_artifacts(
    artifacts: Dict[str, Any],
    turn_index: int,
    cell_counters: Dict[str, int],
) -> List[Dict[str, Any]]:
    cells: List[Dict[str, Any]] = []

    for block in list(artifacts.get("code_blocks", []) or []):
        content = str(block.get("content") or "").strip("\n")
        if not content:
            continue
        runner = str(block.get("runner") or "").strip()
        language = normalize_code_language(str(block.get("language") or ""))
        executable = bool(block.get("executable")) and bool(runner)
        if not executable:
            continue
        cell_id = next_cell_id(cell_counters, language)
        cells.append(
            {
                "id": cell_id,
                "kind": "code_block",
                "source_id": str(block.get("id") or ""),
                "language": language,
                "runner": runner,
                "content": content,
                "turn": int(turn_index),
                "revision": 1,
                "parent_id": None,
            }
        )

    for block in list(artifacts.get("command_blocks", []) or []):
        content = str(block.get("content") or "").strip("\n")
        if not content:
            continue
        cell_id = next_cell_id(cell_counters, "bash")
        cells.append(
            {
                "id": cell_id,
                "kind": "command_block",
                "source_id": str(block.get("id") or ""),
                "language": "bash",
                "runner": "bash",
                "content": content,
                "turn": int(turn_index),
                "revision": 1,
                "parent_id": None,
            }
        )

    return cells


def register_turn_cells(
    turn_result: Dict[str, Any],
    turn_index: int,
    cell_store: Dict[str, Dict[str, Any]],
    cell_order: List[str],
    cell_counters: Dict[str, int],
) -> List[Dict[str, Any]]:
    assistant_text = str(turn_result.get("assistant_text") or "").strip()
    if not assistant_text:
        turn_result["cell_ids"] = []
        return []

    artifacts = turn_result.get("artifacts")
    if not isinstance(artifacts, dict):
        artifacts = build_response_artifacts(assistant_text)
        turn_result["artifacts"] = artifacts

    new_cells = collect_executable_cells_from_artifacts(
        artifacts=artifacts,
        turn_index=turn_index,
        cell_counters=cell_counters,
    )
    for cell in new_cells:
        cell_store[cell["id"]] = cell
        cell_order.append(cell["id"])
    turn_result["cell_ids"] = [str(cell.get("id") or "") for cell in new_cells]
    return new_cells


def build_turn_action_refs(
    turn_result: Dict[str, Any],
    created_cells: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    artifacts = turn_result.get("artifacts")
    if not isinstance(artifacts, dict):
        artifacts = build_response_artifacts(str(turn_result.get("assistant_text") or ""))
        turn_result["artifacts"] = artifacts

    source_to_cell: Dict[str, Dict[str, Any]] = {}
    for cell in created_cells:
        source_id = str(cell.get("source_id") or "").strip()
        if source_id and source_id not in source_to_cell:
            source_to_cell[source_id] = cell

    refs: List[Dict[str, Any]] = []
    next_index = 1
    for item in list(artifacts.get("response_items") or []):
        if str(item.get("type") or "") not in {"code_block", "command_block"}:
            continue
        if not bool(item.get("executable")):
            continue
        source_id = str(item.get("id") or "").strip()
        cell = source_to_cell.get(source_id)
        if not isinstance(cell, dict):
            continue
        refs.append(
            {
                "handle": f"@{next_index}",
                "cell_id": str(cell.get("id") or ""),
                "source_id": source_id,
                "language": normalize_code_language(str(item.get("language") or "")) or "text",
                "kind": str(item.get("type") or ""),
            }
        )
        next_index += 1
    turn_result["action_refs"] = refs
    return refs


def build_action_ref_index(action_refs: Sequence[Dict[str, Any]]) -> Dict[str, str]:
    index: Dict[str, str] = {}
    for ref in action_refs:
        handle = str(ref.get("handle") or "").strip()
        cell_id = str(ref.get("cell_id") or "").strip()
        if handle and cell_id:
            index[handle] = cell_id
    return index


def resolve_cell_reference(
    raw_target: Optional[str],
    current_cell_id: Optional[str],
    action_ref_index: Optional[Dict[str, str]] = None,
) -> Tuple[Optional[str], Optional[str]]:
    target = str(raw_target or "").strip()
    if not target or target in {"current", ".", "last"}:
        resolved = str(current_cell_id or "").strip()
        if not resolved:
            return None, "cells: no current cell (use /cells or /show <cell_id>)"
        return resolved, None
    if target.startswith("@"):
        resolved = str((action_ref_index or {}).get(target) or "").strip()
        if resolved:
            return resolved, None
        return None, f"cells: unknown response ref '{target}'"
    return target, None


def print_cell_catalog(
    cell_store: Dict[str, Dict[str, Any]],
    cell_order: Sequence[str],
    limit: Optional[int] = None,
    current_cell_id: Optional[str] = None,
) -> None:
    if not cell_order:
        print("cells: empty")
        return
    selected_order = list(cell_order)
    if isinstance(limit, int) and limit > 0:
        selected_order = selected_order[-limit:]
    for cell_id in selected_order:
        cell = cell_store.get(cell_id)
        if not isinstance(cell, dict):
            continue
        lines = len(str(cell.get("content") or "").splitlines())
        language = str(cell.get("language") or "text")
        turn = int(cell.get("turn") or 0)
        revision = int(cell.get("revision") or 1)
        preview = summarize_cell_preview(str(cell.get("content") or ""), limit=96)
        marker = "*" if current_cell_id and cell_id == current_cell_id else " "
        if preview:
            print(f"{marker} {cell_id} [{language}] lines={lines} turn={turn} rev={revision} :: {preview}")
        else:
            print(f"{marker} {cell_id} [{language}] lines={lines} turn={turn} rev={revision}")


def extract_cell_preview_line(content: str) -> str:
    for raw in reversed(str(content or "").splitlines()):
        stripped = raw.strip()
        if stripped:
            return stripped
    return ""


def summarize_cell_preview(
    content: str,
    max_head_lines: int = 2,
    limit: int = 96,
) -> str:
    non_empty = [line.strip() for line in str(content or "").splitlines() if line.strip()]
    if not non_empty:
        return ""
    parts = non_empty[: max(1, int(max_head_lines))]
    if len(non_empty) > len(parts):
        tail = non_empty[-1]
        if tail not in parts:
            parts.extend(["...", tail])
    return history_preview(" | ".join(parts), limit=limit)


def save_cell_to_path(cell: Dict[str, Any], raw_path: str) -> Path:
    target = Path(raw_path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(str(cell.get("content") or ""), encoding="utf-8")
    return target


def diff_cell_contents(
    left: Dict[str, Any],
    right: Dict[str, Any],
) -> str:
    left_id = str(left.get("id") or "left")
    right_id = str(right.get("id") or "right")
    left_lines = str(left.get("content") or "").splitlines()
    right_lines = str(right.get("content") or "").splitlines()
    diff = difflib.unified_diff(
        left_lines,
        right_lines,
        fromfile=left_id,
        tofile=right_id,
        lineterm="",
    )
    return "\n".join(diff)


def print_cell_content(cell: Dict[str, Any]) -> None:
    print(colorize_cell_text_for_terminal(cell))


def resolve_run_request(
    args: Sequence[str],
    current_cell_id: Optional[str],
    action_ref_index: Optional[Dict[str, str]] = None,
    default_timeout_s: int = 30,
) -> Tuple[Optional[str], Optional[int], Optional[str]]:
    usage = "usage: /run [cell_id|@ref] [timeout_seconds]"
    if len(args) == 0 or len(args) > 3:
        return None, None, usage

    target, target_error = resolve_cell_reference(
        None,
        current_cell_id=current_cell_id,
        action_ref_index=action_ref_index,
    )
    if target_error:
        target = ""
    timeout_s = int(default_timeout_s)

    if len(args) == 2:
        token = str(args[1] or "").strip()
        if token and target and re.fullmatch(r"\d+", token):
            return target, max(1, int(token)), None
        if token:
            target, target_error = resolve_cell_reference(
                token,
                current_cell_id=current_cell_id,
                action_ref_index=action_ref_index,
            )
            if target_error:
                return None, None, target_error
    elif len(args) == 3:
        target, target_error = resolve_cell_reference(
            str(args[1] or "").strip(),
            current_cell_id=current_cell_id,
            action_ref_index=action_ref_index,
        )
        if target_error:
            return None, None, target_error
        raw_timeout = str(args[2] or "").strip()
        try:
            timeout_s = max(1, int(raw_timeout))
        except ValueError:
            return None, None, usage

    if not target:
        if target_error:
            return None, None, target_error
        return None, None, "cells: no current cell (use /cells or /show <cell_id>)"

    return target, timeout_s, None


def resolve_local_python_command() -> str:
    executable = str(getattr(sys, "executable", "") or "").strip()
    if executable:
        try:
            if Path(executable).exists():
                return executable
        except OSError:
            pass
    for candidate in ("python3", "python"):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return "python3"


def create_local_python_tool_shims(python_cmd: str) -> Path:
    shim_dir = Path(tempfile.mkdtemp(prefix="sky_prompt_shims_"))
    quoted_python = shlex.quote(str(python_cmd))
    wrappers = {
        "python": f"#!/bin/sh\nexec {quoted_python} \"$@\"\n",
        "python3": f"#!/bin/sh\nexec {quoted_python} \"$@\"\n",
        "pip": f"#!/bin/sh\nexec {quoted_python} -m pip \"$@\"\n",
        "pip3": f"#!/bin/sh\nexec {quoted_python} -m pip \"$@\"\n",
    }
    for name, body in wrappers.items():
        path = shim_dir / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)
    return shim_dir


def build_local_run_env(shim_dir: Optional[Path] = None) -> Dict[str, str]:
    env = dict(os.environ)
    if shim_dir is None:
        return env
    current_path = str(env.get("PATH") or "")
    env["PATH"] = str(shim_dir) if not current_path else f"{shim_dir}{os.pathsep}{current_path}"
    return env


def execute_cell_locally(
    cell: Dict[str, Any],
    workdir: Optional[Path] = None,
    timeout_s: int = 30,
) -> Dict[str, Any]:
    language = normalize_code_language(str(cell.get("language") or ""))
    content = str(cell.get("content") or "")
    if not content.strip():
        return {"ok": False, "backend": "local", "error": "empty cell content"}
    workdir_path = Path(workdir or Path.cwd())
    script_path: Optional[Path] = None
    shim_dir: Optional[Path] = None
    input_text: Optional[str] = None
    python_cmd = resolve_local_python_command()

    if language == "python":
        runner_cmd = [python_cmd, "-"]
        input_text = content
    elif language in {"bash", "javascript", "js", "typescript", "ts", "ruby", "perl"}:
        suffix = language_to_cell_extension(language)
        with tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False, encoding="utf-8") as handle:
            handle.write(content)
            script_path = Path(handle.name)
        if language == "bash":
            shim_dir = create_local_python_tool_shims(python_cmd)
            runner_cmd = [shutil.which("bash") or "/bin/bash", str(script_path)]
        elif language in {"javascript", "js"}:
            runner_cmd = ["node", str(script_path)]
        elif language in {"typescript", "ts"}:
            runner_cmd = ["ts-node", str(script_path)]
        elif language == "ruby":
            runner_cmd = ["ruby", str(script_path)]
        else:
            runner_cmd = ["perl", str(script_path)]
    else:
        return {"ok": False, "backend": "local", "error": f"unsupported language for local run: {language}"}

    try:
        completed = subprocess.run(
            runner_cmd,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=max(1, int(timeout_s)),
            cwd=str(workdir_path),
            env=build_local_run_env(shim_dir),
            check=False,
        )
        exit_code = int(completed.returncode)
        stderr_text = str(completed.stderr or "")
        stdout_text = str(completed.stdout or "")
        error_message = ""
        if exit_code != 0:
            error_message = stderr_text.strip() or stdout_text.strip() or f"command failed with exit code {exit_code}"
        return {
            "ok": exit_code == 0,
            "backend": "local",
            "command": runner_cmd,
            "exit_code": exit_code,
            "stdout": stdout_text,
            "stderr": stderr_text,
            "error": error_message,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "backend": "local",
            "command": runner_cmd,
            "error": f"execution timed out after {int(timeout_s)}s",
            "stdout": str(exc.stdout or ""),
            "stderr": str(exc.stderr or ""),
        }
    except KeyboardInterrupt:
        return {
            "ok": False,
            "backend": "local",
            "command": runner_cmd,
            "error": "execution cancelled by user",
            "stdout": "",
            "stderr": "",
            "cancelled": True,
        }
    except FileNotFoundError:
        return {
            "ok": False,
            "backend": "local",
            "command": runner_cmd,
            "error": f"runner not found: {runner_cmd[0]}",
        }
    finally:
        if script_path is not None:
            try:
                script_path.unlink(missing_ok=True)
            except Exception:
                pass
        if shim_dir is not None:
            try:
                shutil.rmtree(shim_dir, ignore_errors=True)
            except Exception:
                pass


def resolve_pyreplab_command(explicit_command: Optional[str] = None) -> Optional[List[str]]:
    raw = str(explicit_command or os.getenv("PYREPLAB_CMD") or "").strip()
    if raw:
        try:
            parts = shlex.split(raw)
        except ValueError:
            return None
        return parts if parts else None
    discovered = shutil.which("pyreplab")
    if discovered:
        return [discovered]
    common_candidates = [
        Path.home() / ".local" / "bin" / "pyreplab",
        Path.home() / "Projects" / "pyrepl" / "pyreplab",
        Path.home() / "pyrepl" / "pyreplab",
        Path.cwd() / "pyreplab",
        Path.cwd() / ".venv" / "bin" / "pyreplab",
        Path.cwd().parent / "pyrepl" / "pyreplab",
    ]
    for candidate in common_candidates:
        try:
            if candidate.is_file() and os.access(str(candidate), os.X_OK):
                return [str(candidate)]
        except OSError:
            continue
    return None


def start_pyreplab_session(
    pyreplab_cmd: Sequence[str],
    workdir: Path,
    session_dir: Optional[str] = None,
    timeout_s: int = 8,
) -> Dict[str, Any]:
    if not pyreplab_cmd:
        return {"ok": False, "error": "pyreplab command is empty"}
    env = os.environ.copy()
    if session_dir:
        env["PYREPLAB_DIR"] = str(session_dir)
    command = list(pyreplab_cmd) + ["start", "--workdir", str(workdir)]
    try:
        proc = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=max(3, int(timeout_s)),
            cwd=str(workdir),
            env=env,
            check=False,
        )
    except FileNotFoundError:
        return {"ok": False, "command": command, "error": f"runner not found: {pyreplab_cmd[0]}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "command": command, "error": "pyreplab start timed out"}
    except KeyboardInterrupt:
        return {"ok": False, "command": command, "error": "pyreplab start cancelled by user", "cancelled": True}
    return {"ok": int(proc.returncode) == 0, "command": command, "exit_code": int(proc.returncode)}


def stop_pyreplab_session(
    pyreplab_cmd: Sequence[str],
    workdir: Path,
    session_dir: Optional[str] = None,
    timeout_s: int = 6,
) -> Dict[str, Any]:
    if not pyreplab_cmd:
        return {"ok": False, "error": "pyreplab command is empty"}
    env = os.environ.copy()
    if session_dir:
        env["PYREPLAB_DIR"] = str(session_dir)
    command = list(pyreplab_cmd) + ["stop"]
    try:
        proc = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=max(3, int(timeout_s)),
            cwd=str(workdir),
            env=env,
            check=False,
        )
    except FileNotFoundError:
        return {"ok": False, "command": command, "error": f"runner not found: {pyreplab_cmd[0]}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "command": command, "error": "pyreplab stop timed out"}
    except KeyboardInterrupt:
        return {"ok": False, "command": command, "error": "pyreplab stop cancelled by user", "cancelled": True}
    return {"ok": int(proc.returncode) == 0, "command": command, "exit_code": int(proc.returncode)}


def allocate_pyreplab_session_dir(workdir: Path) -> str:
    base_dir = Path(str(os.getenv("PYREPLAB_BASE") or "/tmp/pyreplab")).expanduser()
    safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(workdir.name or "workspace")).strip("_") or "workspace"
    return str(base_dir / f"sky_prompt_{safe_name}_{os.getpid()}_{int(time.time())}")


def resolve_pyreplab_session_dir(
    pyreplab_cmd: Sequence[str],
    workdir: Path,
    timeout_s: int = 6,
) -> Optional[str]:
    if not pyreplab_cmd:
        return None
    command = list(pyreplab_cmd) + ["dir", "--workdir", str(workdir)]
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=max(3, int(timeout_s)),
            cwd=str(workdir),
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if int(proc.returncode) != 0:
        return None
    session_dir = str(proc.stdout or "").strip()
    return session_dir or None


def execute_pyreplab_code(
    code: str,
    pyreplab_cmd: Sequence[str],
    workdir: Path,
    session_dir: Optional[str] = None,
    timeout_s: int = 30,
) -> Dict[str, Any]:
    content = str(code or "")
    if not content.strip():
        return {"ok": False, "backend": "pyreplab", "error": "empty code"}
    if not pyreplab_cmd:
        return {
            "ok": False,
            "backend": "pyreplab",
            "error": "pyreplab command not configured (set --pyreplab-cmd or PYREPLAB_CMD)",
        }

    base_cmd = list(pyreplab_cmd)
    resolved_session_dir = str(session_dir or "").strip()
    env = os.environ.copy()
    env["PYREPLAB_TIMEOUT"] = str(max(1, int(timeout_s)))
    if resolved_session_dir:
        env["PYREPLAB_DIR"] = str(resolved_session_dir)

    warmup = start_pyreplab_session(
        pyreplab_cmd=base_cmd,
        workdir=workdir,
        session_dir=resolved_session_dir,
        timeout_s=max(5, min(20, int(timeout_s))),
    )
    if not warmup.get("ok"):
        return {
            "ok": False,
            "backend": "pyreplab",
            "command": warmup.get("command") or (base_cmd + ["start", "--workdir", str(workdir)]),
            "exit_code": int(warmup.get("exit_code", -1)),
            "error": str(warmup.get("error") or "pyreplab start failed"),
        }

    run_cmd = base_cmd + ["run"]
    try:
        run_proc = subprocess.run(
            run_cmd,
            input=content,
            capture_output=True,
            text=True,
            timeout=max(5, int(timeout_s) + 10),
            cwd=str(workdir),
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "backend": "pyreplab",
            "command": run_cmd,
            "error": f"pyreplab run timed out after {int(timeout_s)}s",
            "stdout": str(exc.stdout or ""),
            "stderr": str(exc.stderr or ""),
        }
    except KeyboardInterrupt:
        return {
            "ok": False,
            "backend": "pyreplab",
            "command": run_cmd,
            "error": "execution cancelled by user",
            "stdout": "",
            "stderr": "",
            "cancelled": True,
        }
    except FileNotFoundError:
        return {
            "ok": False,
            "backend": "pyreplab",
            "command": run_cmd,
            "error": f"runner not found: {base_cmd[0]}",
        }

    stdout_text = str(run_proc.stdout or "")
    stderr_text = str(run_proc.stderr or "")
    exit_code = int(run_proc.returncode)
    command_used = run_cmd

    if exit_code == 2:
        wait_cmd = base_cmd + ["wait"]
        try:
            wait_proc = subprocess.run(
                wait_cmd,
                capture_output=True,
                text=True,
                timeout=max(5, int(timeout_s) + 10),
                cwd=str(workdir),
                env=env,
                check=False,
            )
            stdout_text += str(wait_proc.stdout or "")
            stderr_text += str(wait_proc.stderr or "")
            exit_code = int(wait_proc.returncode)
            command_used = wait_cmd
        except subprocess.TimeoutExpired as exc:
            stdout_text += str(exc.stdout or "")
            stderr_text += str(exc.stderr or "")
            return {
                "ok": False,
                "backend": "pyreplab",
                "command": wait_cmd,
                "error": f"pyreplab wait timed out after {int(timeout_s)}s",
                "stdout": stdout_text,
                "stderr": stderr_text,
            }
        except KeyboardInterrupt:
            return {
                "ok": False,
                "backend": "pyreplab",
                "command": wait_cmd,
                "error": "execution cancelled by user",
                "stdout": stdout_text,
                "stderr": stderr_text,
                "cancelled": True,
            }

    error_message = ""
    if exit_code != 0:
        stderr_preview = stderr_text.strip()
        error_message = stderr_preview or f"pyreplab command failed with exit code {exit_code}"

    return {
        "ok": exit_code == 0,
        "backend": "pyreplab",
        "command": command_used,
        "exit_code": exit_code,
        "stdout": stdout_text,
        "stderr": stderr_text,
        "error": error_message,
    }


def execute_cell_with_pyreplab(
    cell: Dict[str, Any],
    pyreplab_cmd: Sequence[str],
    workdir: Path,
    session_dir: Optional[str] = None,
    timeout_s: int = 30,
) -> Dict[str, Any]:
    language = normalize_code_language(str(cell.get("language") or ""))
    content = str(cell.get("content") or "")
    if language != "python":
        return {
            "ok": False,
            "backend": "pyreplab",
            "error": f"pyreplab backend only supports python (got {language or 'unknown'})",
        }
    if not content.strip():
        return {"ok": False, "backend": "pyreplab", "error": "empty cell content"}
    if not pyreplab_cmd:
        return {
            "ok": False,
            "backend": "pyreplab",
            "error": "pyreplab command not configured (set --pyreplab-cmd or PYREPLAB_CMD)",
        }
    return execute_pyreplab_code(
        code=content,
        pyreplab_cmd=pyreplab_cmd,
        workdir=workdir,
        session_dir=session_dir,
        timeout_s=timeout_s,
    )


def edit_cell_in_editor(
    cell: Dict[str, Any],
    workspace_dir: Path,
) -> Tuple[bool, str]:
    workspace_dir.mkdir(parents=True, exist_ok=True)
    cell_id = str(cell.get("id") or "cell")
    suffix = language_to_cell_extension(str(cell.get("language") or ""))
    draft_path = workspace_dir / f"{cell_id}{suffix}"
    draft_path.write_text(str(cell.get("content") or ""), encoding="utf-8")
    editor = os.getenv("EDITOR") or os.getenv("VISUAL") or "vi"
    try:
        completed = subprocess.run([editor, str(draft_path)], check=False)
    except FileNotFoundError:
        return False, f"editor not found: {editor}"
    updated = draft_path.read_text(encoding="utf-8")
    changed = updated != str(cell.get("content") or "")
    if changed:
        cell["content"] = updated
        cell["revision"] = int(cell.get("revision") or 1) + 1
        return True, f"updated {cell_id} via {editor} ({draft_path})"
    if completed.returncode != 0:
        return False, f"editor exited with code {completed.returncode}"
    return True, f"no changes ({draft_path})"


def split_repl_command_args(raw_line: str) -> Tuple[Optional[List[str]], Optional[str]]:
    try:
        return shlex.split(raw_line), None
    except ValueError as exc:
        return None, str(exc)


def find_repl_command_spec(name: str) -> Optional[Dict[str, Any]]:
    target = str(name or "").strip().lower()
    if not target:
        return None
    for spec in REPL_COMMAND_SPECS:
        if str(spec.get("name") or "").lower() == target:
            return dict(spec)
    return None


def format_repl_help_lines(filter_text: str = "", limit: Optional[int] = None) -> List[str]:
    query = str(filter_text or "").strip().lower()
    lines: List[str] = []
    for spec in REPL_COMMAND_SPECS:
        name = str(spec.get("name") or "")
        usage = str(spec.get("usage") or name)
        summary = str(spec.get("summary") or "")
        if query and not (name.lower().startswith(query) or usage.lower().startswith(query)):
            continue
        lines.append(f"{usage} :: {summary}".rstrip())
    if isinstance(limit, int) and limit > 0:
        return lines[:limit]
    return lines


def live_buffer_tokens(buffer: str) -> List[str]:
    return [token for token in re.findall(r"\S+", str(buffer or "")) if token]


def terminal_colors_enabled(
    stream: Optional[Any] = None,
    env: Optional[Mapping[str, str]] = None,
) -> bool:
    values = env or os.environ
    if str(values.get("NO_COLOR") or "").strip():
        return False
    if str(values.get("CLICOLOR_FORCE") or "").strip() not in {"", "0"}:
        return True
    if os.name == "nt":
        return False
    target = stream if stream is not None else sys.stdout
    try:
        if not bool(target.isatty()):
            return False
    except Exception:
        return False
    term = str(values.get("TERM") or "").strip().lower()
    if term in {"", "dumb"}:
        return False
    return True


def language_ansi_color(language: str) -> str:
    normalized = normalize_code_language(language)
    if normalized in LANGUAGE_ANSI_BY_LANGUAGE:
        return LANGUAGE_ANSI_BY_LANGUAGE[normalized]
    return LANGUAGE_ANSI_BY_LANGUAGE.get("text", "")


def ansi_wrap(text: str, *styles: str, enabled: bool = True) -> str:
    payload = str(text or "")
    if not enabled:
        return payload
    codes = "".join(style for style in styles if style)
    if not codes:
        return payload
    return f"{codes}{payload}{ANSI_RESET}"


def line_fence_language(line: str) -> Optional[str]:
    match = re.fullmatch(r"```([A-Za-z0-9_.+-]*)\s*", str(line or "").strip())
    if not match:
        return None
    return normalize_code_language(str(match.group(1) or "").strip()) or "text"


def line_footer_language(line: str) -> Optional[str]:
    match = re.search(r"\[[^\]]+\s+([A-Za-z0-9_.+-]+)\]\s*(?:/run|/show|/edit|/fork)", str(line or ""))
    if not match:
        return None
    return normalize_code_language(str(match.group(1) or "").strip()) or "text"


def colorize_markdown_lines_for_terminal(
    lines: Sequence[str],
    *,
    enable_color: Optional[bool] = None,
) -> List[str]:
    colored: List[str] = []
    active_language = ""
    in_code_block = False
    use_color = terminal_colors_enabled() if enable_color is None else bool(enable_color)
    for raw_line in lines:
        line = str(raw_line or "")
        fence_language = line_fence_language(line)
        if fence_language is not None:
            color = language_ansi_color(active_language if in_code_block else fence_language)
            colored.append(ansi_wrap(line, ANSI_BOLD, color, enabled=use_color))
            if in_code_block:
                in_code_block = False
                active_language = ""
            else:
                in_code_block = True
                active_language = fence_language
            continue

        if in_code_block:
            color = language_ansi_color(active_language)
            colored.append(ansi_wrap(line, color, enabled=use_color))
            continue

        footer_language = line_footer_language(line)
        if footer_language:
            color = language_ansi_color(footer_language)
            colored.append(ansi_wrap(line, ANSI_DIM, color, enabled=use_color))
            continue

        colored.append(line)
    return colored


def colorize_markdown_text_for_terminal(
    text: str,
    *,
    enable_color: Optional[bool] = None,
) -> str:
    lines = str(text or "").splitlines()
    return "\n".join(colorize_markdown_lines_for_terminal(lines, enable_color=enable_color))


def colorize_command_help_lines_for_terminal(
    lines: Sequence[str],
    *,
    enable_color: Optional[bool] = None,
) -> List[str]:
    use_color = terminal_colors_enabled() if enable_color is None else bool(enable_color)
    colored: List[str] = []
    for raw_line in lines:
        line = str(raw_line or "")
        if " :: " not in line:
            colored.append(line)
            continue
        usage, summary = line.split(" :: ", 1)
        colored.append(
            ansi_wrap(usage, ANSI_CYAN, ANSI_BOLD, enabled=use_color)
            + " :: "
            + ansi_wrap(summary, ANSI_DIM, enabled=use_color)
        )
    return colored


def colorize_cell_text_for_terminal(
    cell: Dict[str, Any],
    *,
    enable_color: Optional[bool] = None,
) -> str:
    language = normalize_code_language(str(cell.get("language") or "")) or "text"
    use_color = terminal_colors_enabled() if enable_color is None else bool(enable_color)
    header = (
        f"{cell.get('id')} [{cell.get('language')}] "
        f"rev={cell.get('revision')} turn={cell.get('turn')}"
    )
    styled_header = ansi_wrap(header, ANSI_BOLD, language_ansi_color(language), enabled=use_color)
    content_lines = str(cell.get("content") or "").splitlines()
    styled_content = "\n".join(
        ansi_wrap(line, language_ansi_color(language), enabled=use_color) for line in content_lines
    )
    if styled_content:
        return styled_header + "\n" + styled_content
    return styled_header


def setup_repl_readline_history(
    history_path: Path = DEFAULT_REPL_HISTORY_PATH,
) -> Tuple[Optional[Any], Optional[Path]]:
    try:
        import readline  # type: ignore
    except Exception:
        return None, None

    try:
        doc = str(getattr(readline, "__doc__", "") or "").lower()
        if "libedit" in doc:
            readline.parse_and_bind("bind ^[[A ed-search-prev-history")
            readline.parse_and_bind("bind ^[[B ed-search-next-history")
        else:
            readline.parse_and_bind('"\e[A": previous-history')
            readline.parse_and_bind('"\e[B": next-history')
    except Exception:
        pass

    try:
        if history_path.is_file():
            readline.read_history_file(str(history_path))
    except Exception:
        pass
    return readline, history_path


def add_repl_history_entry(readline_mod: Optional[Any], line: str) -> None:
    if not readline_mod:
        return
    entry = str(line or "").strip()
    if not entry:
        return
    try:
        current_len = int(readline_mod.get_current_history_length())
        if current_len > 0:
            last_entry = readline_mod.get_history_item(current_len)
            if isinstance(last_entry, str) and last_entry.strip() == entry:
                return
        readline_mod.add_history(entry)
    except Exception:
        return


def flush_repl_history(
    readline_mod: Optional[Any],
    history_path: Optional[Path],
    limit: int = DEFAULT_REPL_HISTORY_LIMIT,
) -> None:
    if not readline_mod or not history_path:
        return
    try:
        readline_mod.set_history_length(int(limit))
    except Exception:
        pass
    try:
        history_path.parent.mkdir(parents=True, exist_ok=True)
        readline_mod.write_history_file(str(history_path))
    except Exception:
        return


def load_repl_history_entries(
    history_path: Path = DEFAULT_REPL_HISTORY_PATH,
    limit: int = DEFAULT_REPL_HISTORY_LIMIT,
) -> List[str]:
    if not history_path.is_file():
        return []
    try:
        lines = [line.rstrip("\n") for line in history_path.read_text(encoding="utf-8").splitlines()]
    except OSError:
        return []
    entries = [line for line in lines if line.strip()]
    if limit > 0:
        return entries[-limit:]
    return entries


def add_repl_history_entry_to_list(entries: List[str], line: str) -> None:
    entry = str(line or "").strip()
    if not entry:
        return
    if entries and entries[-1].strip() == entry:
        return
    entries.append(entry)


def flush_repl_history_entries(
    entries: Sequence[str],
    history_path: Path = DEFAULT_REPL_HISTORY_PATH,
    limit: int = DEFAULT_REPL_HISTORY_LIMIT,
) -> None:
    trimmed = [str(entry or "").rstrip("\n") for entry in entries if str(entry or "").strip()]
    if limit > 0:
        trimmed = trimmed[-limit:]
    try:
        history_path.parent.mkdir(parents=True, exist_ok=True)
        payload = "\n".join(trimmed)
        if payload:
            payload += "\n"
        history_path.write_text(payload, encoding="utf-8")
    except OSError:
        return


def repl_ref_completion_items(action_refs: Sequence[Dict[str, Any]]) -> List[Tuple[str, str]]:
    items: List[Tuple[str, str]] = []
    seen: set = set()
    for ref in action_refs:
        handle = str(ref.get("handle") or "").strip()
        cell_id = str(ref.get("cell_id") or "").strip()
        language = normalize_code_language(str(ref.get("language") or "")) or "text"
        preview = f"{cell_id} {language}".strip()
        if handle and handle not in seen:
            items.append((handle, preview))
            seen.add(handle)
        if cell_id and cell_id not in seen:
            items.append((cell_id, preview))
            seen.add(cell_id)
    return items


def format_repl_ref_suggestion_lines(
    action_refs: Sequence[Dict[str, Any]],
    current_cell_id: Optional[str] = None,
    limit: int = 8,
) -> List[str]:
    lines: List[str] = []
    for ref in list(action_refs)[: max(1, int(limit))]:
        handle = str(ref.get("handle") or "").strip()
        cell_id = str(ref.get("cell_id") or "").strip()
        language = normalize_code_language(str(ref.get("language") or "")) or "text"
        marker = "*" if current_cell_id and cell_id == current_cell_id else " "
        lines.append(f"{marker} {handle} -> {cell_id} [{language}]")
    return lines


def build_live_ref_preview_lines(
    raw_target: str,
    current_cell_id: Optional[str],
    action_ref_index: Mapping[str, str],
    turn_view: Optional[Dict[str, Any]],
    *,
    context_lines: int = 20,
) -> List[str]:
    if not isinstance(turn_view, dict):
        return []
    resolved_target, target_error = resolve_cell_reference(
        raw_target,
        current_cell_id=current_cell_id,
        action_ref_index=dict(action_ref_index),
    )
    if target_error:
        return [f"preview> {target_error}"]
    ref_spans = turn_view.get("ref_spans")
    preview_lines = list(turn_view.get("preview_lines") or [])
    if not isinstance(ref_spans, dict) or not preview_lines:
        return []
    span = ref_spans.get(str(raw_target or "").strip()) or ref_spans.get(str(resolved_target or "").strip())
    if not isinstance(span, dict):
        return [f"preview> no rendered preview for {raw_target}"]
    start_line = max(0, int(span.get("start_line") or 0))
    end_line = max(start_line, int(span.get("end_line") or start_line))
    preview_end = min(len(preview_lines), max(end_line + 1, start_line + max(4, int(context_lines))))
    header = (
        f"preview> {span.get('handle') or raw_target} "
        f"{span.get('cell_id') or resolved_target} "
        f"[{span.get('language') or 'text'}]"
    )
    body = preview_lines[start_line:preview_end]
    if preview_end < len(preview_lines):
        body = list(body) + ["..."]
    return [header] + body


def build_live_repl_panel_lines(
    buffer: str,
    *,
    current_cell_id: Optional[str],
    action_refs: Sequence[Dict[str, Any]],
    action_ref_index: Mapping[str, str],
    turn_view: Optional[Dict[str, Any]],
) -> List[str]:
    raw = str(buffer or "")
    stripped = raw.strip()
    if not stripped:
        return []
    tokens = live_buffer_tokens(raw)
    if not tokens:
        return []
    first = str(tokens[0] or "").lower()
    if not first.startswith("/"):
        return []

    if first in {"/help", "/h"}:
        query = str(tokens[1] or "") if len(tokens) > 1 else ""
        help_lines = format_repl_help_lines(query, limit=12)
        return ["help> commands"] + (help_lines or ["help> no matching commands"])

    if len(tokens) == 1 and not raw.endswith(" "):
        help_lines = format_repl_help_lines(first, limit=12)
        if help_lines:
            return ["help> commands"] + help_lines

    if first in REPL_REF_COMMANDS:
        if first == "/diff":
            if len(tokens) >= 2:
                preview = build_live_ref_preview_lines(
                    tokens[1],
                    current_cell_id=current_cell_id,
                    action_ref_index=action_ref_index,
                    turn_view=turn_view,
                )
                if len(tokens) >= 3:
                    second_preview = build_live_ref_preview_lines(
                        tokens[2],
                        current_cell_id=current_cell_id,
                        action_ref_index=action_ref_index,
                        turn_view=turn_view,
                    )
                    return preview + [""] + second_preview if second_preview else preview
                if preview:
                    return preview
            return ["refs> available refs"] + format_repl_ref_suggestion_lines(action_refs, current_cell_id=current_cell_id)

        raw_target = ""
        if len(tokens) >= 2:
            raw_target = str(tokens[1] or "")
        elif raw.endswith(" "):
            raw_target = ""
        if raw_target:
            preview = build_live_ref_preview_lines(
                raw_target,
                current_cell_id=current_cell_id,
                action_ref_index=action_ref_index,
                turn_view=turn_view,
            )
            if preview:
                return preview
        return ["refs> available refs"] + format_repl_ref_suggestion_lines(action_refs, current_cell_id=current_cell_id)

    help_lines = format_repl_help_lines(first, limit=10)
    if help_lines:
        return ["help> commands"] + help_lines
    return []


def ref_completion_suggestions(
    ref_suggestions: Sequence[str],
    token: str,
    *,
    include_exact_cycle: bool = False,
) -> List[str]:
    candidate = str(token or "").strip()
    values = [str(value or "").strip() for value in ref_suggestions if str(value or "").strip()]
    if not candidate:
        return values
    if candidate.startswith("@"):
        scoped = [value for value in values if value.startswith("@")]
    else:
        scoped = [value for value in values if not value.startswith("@")]
    if not scoped:
        scoped = values
    if include_exact_cycle and candidate in scoped:
        return scoped
    return [value for value in scoped if value.startswith(candidate)]


def repl_completion_candidates(
    buffer: str,
    cursor: int,
    *,
    current_cell_id: Optional[str],
    action_refs: Sequence[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if cursor != len(buffer):
        return None
    raw = str(buffer or "")
    if not raw.startswith("/"):
        return None
    tokens = live_buffer_tokens(raw)
    trailing_space = raw.endswith(" ")
    if not tokens:
        suggestions = [str(spec.get("name") or "") for spec in REPL_COMMAND_SPECS]
        return {"start": 0, "end": 0, "suggestions": suggestions, "append_space": True}

    first = str(tokens[0] or "")
    if len(tokens) == 1 and not trailing_space:
        suggestions = [
            str(spec.get("name") or "")
            for spec in REPL_COMMAND_SPECS
            if str(spec.get("name") or "").startswith(first)
        ]
        return {"start": 0, "end": cursor, "suggestions": suggestions, "append_space": True}

    first_lower = first.lower()
    ref_items = repl_ref_completion_items(action_refs)
    ref_suggestions = [value for value, _ in ref_items]
    if current_cell_id and current_cell_id not in ref_suggestions:
        ref_suggestions.append(current_cell_id)

    if first_lower in {"/run", "/show", "/edit", "/focus"}:
        if len(tokens) > 2:
            return None
        prefix = ""
        start = cursor
        end = cursor
        if len(tokens) >= 2:
            prefix = str(tokens[1] or "")
            start = raw.rfind(prefix)
            end = start + len(prefix) if trailing_space else cursor
            suggestions = ref_completion_suggestions(
                ref_suggestions,
                prefix,
                include_exact_cycle=bool(trailing_space or prefix in ref_suggestions),
            )
        else:
            suggestions = list(ref_suggestions)
        return {
            "start": start,
            "end": end,
            "suggestions": suggestions,
            "append_space": True,
            "cycle_key": (first_lower, "ref-slot", tuple(suggestions)),
        }

    if first_lower in {"/fork", "/save"}:
        if trailing_space and len(tokens) >= 2:
            return None
        prefix = ""
        start = cursor
        end = cursor
        if len(tokens) >= 2 and not trailing_space:
            prefix = str(tokens[1] or "")
            start = raw.rfind(prefix)
            end = cursor
        suggestions = [value for value in ref_suggestions if value.startswith(prefix)]
        return {"start": start, "end": end, "suggestions": suggestions, "append_space": True}

    if first_lower == "/diff":
        if len(tokens) >= 3 and trailing_space:
            return None
        if len(tokens) >= 3:
            prefix = str(tokens[2] or "")
        elif len(tokens) >= 2 and trailing_space:
            prefix = ""
        elif len(tokens) >= 2:
            prefix = str(tokens[1] or "")
        else:
            prefix = ""
        start = cursor - len(prefix)
        end = cursor
        suggestions = [value for value in ref_suggestions if value.startswith(prefix)]
        return {"start": start, "end": end, "suggestions": suggestions, "append_space": True}

    return None


def apply_repl_completion_state(
    buffer: str,
    cursor: int,
    completion_state: Optional[Dict[str, Any]],
    *,
    current_cell_id: Optional[str],
    action_refs: Sequence[Dict[str, Any]],
) -> Tuple[str, int, Optional[Dict[str, Any]]]:
    context = repl_completion_candidates(
        buffer,
        cursor,
        current_cell_id=current_cell_id,
        action_refs=action_refs,
    )
    if not context or not list(context.get("suggestions") or []):
        return buffer, cursor, completion_state

    suggestions = list(context.get("suggestions") or [])
    start = int(context.get("start") or 0)
    end = int(context.get("end") or cursor)
    cycle_key = context.get("cycle_key")
    if cycle_key is not None:
        base_key = cycle_key
    else:
        base_key = (buffer, cursor, start, end, tuple(suggestions))

    if completion_state and completion_state.get("key") == base_key:
        index = (int(completion_state.get("index") or 0) + 1) % len(suggestions)
    else:
        index = 0

    suggestion = suggestions[index]
    next_buffer = buffer[:start] + suggestion + buffer[end:]
    if bool(context.get("append_space")) and not next_buffer.endswith(" "):
        next_buffer += " "
    next_cursor = len(next_buffer)
    next_state = {"key": base_key, "index": index}
    return next_buffer, next_cursor, next_state


def format_repl_input_line(
    prompt: str,
    buffer: str,
    cursor: int,
    width: int,
    *,
    prefer_start: bool = False,
) -> Tuple[str, int]:
    prompt_text = str(prompt or "")
    content = str(buffer or "")
    display_content, display_offsets = render_repl_buffer_for_display(content)
    available = max(8, int(width) - len(prompt_text))
    safe_cursor = max(0, min(len(content), int(cursor)))
    display_cursor = display_offsets[safe_cursor]
    if len(display_content) <= available:
        return prompt_text + display_content, len(prompt_text) + min(len(display_content), display_cursor)

    if prefer_start:
        window_start = 0
    else:
        window_start = min(max(0, display_cursor - available + 1), max(0, len(display_content) - available))
        window_start = snap_repl_display_offset(display_offsets, window_start)
    window_end = min(len(display_content), window_start + available)
    snapped_end = snap_repl_display_offset(display_offsets, window_end)
    if snapped_end > window_start:
        window_end = snapped_end
    prefix = "..." if window_start > 0 else ""
    suffix = "..." if window_end < len(display_content) else ""
    visible = display_content[window_start:window_end]
    if prefix:
        visible = prefix + visible[3:]
    if suffix and len(visible) >= 3:
        visible = visible[:-3] + suffix
    cursor_col = len(prompt_text) + max(0, display_cursor - window_start)
    cursor_col = min(len(prompt_text) + len(visible), cursor_col)
    return prompt_text + visible, cursor_col


def render_repl_buffer_for_display(text: str) -> Tuple[str, List[int]]:
    raw = str(text or "")
    rendered_parts: List[str] = []
    offsets: List[int] = [0]
    for char in raw:
        if char == "\n":
            token = "\\n"
        elif char == "\r":
            token = "\\r"
        elif char == "\t":
            token = "    "
        else:
            token = char
        rendered_parts.append(token)
        offsets.append(offsets[-1] + len(token))
    return "".join(rendered_parts), offsets


def snap_repl_display_offset(offsets: Sequence[int], target: int) -> int:
    if not offsets:
        return 0
    safe_target = max(0, min(int(target), int(offsets[-1])))
    index = bisect.bisect_right(list(offsets), safe_target) - 1
    if index < 0:
        return 0
    return int(offsets[index])


def truncate_repl_panel_line(text: str, width: int) -> str:
    raw = str(text or "")
    if width <= 0 or len(raw) <= width:
        return raw
    if width <= 3:
        return raw[:width]
    return raw[: width - 3] + "..."


def read_escape_sequence(fd: int) -> str:
    chunks = ["\x1b"]
    try:
        import select
    except Exception:
        return "\x1b"
    for _ in range(4):
        ready, _, _ = select.select([fd], [], [], 0.01)
        if not ready:
            break
        piece = os.read(fd, 1)
        if not piece:
            break
        chunks.append(piece.decode("utf-8", "ignore"))
        if chunks[-1].isalpha() or chunks[-1] == "~":
            break
    return "".join(chunks)


def normalize_repl_pasted_text(raw_text: str) -> str:
    text = str(raw_text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not text:
        return ""
    lines = text.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return ""
    return "\n".join(line.replace("\t", "    ") for line in lines)


def read_bracketed_paste(fd: int) -> str:
    terminator = "\x1b[201~"
    chunks: List[str] = []
    recent = ""
    while True:
        piece = os.read(fd, 1)
        if not piece:
            break
        char = piece.decode("utf-8", "ignore")
        chunks.append(char)
        recent = (recent + char)[-len(terminator) :]
        if recent == terminator:
            joined = "".join(chunks)
            return joined[: -len(terminator)]
    return "".join(chunks)


def read_live_repl_input(
    prompt: str,
    *,
    history_entries: List[str],
    panel_builder: Optional[Any] = None,
    current_cell_id: Optional[str] = None,
    action_refs: Optional[Sequence[Dict[str, Any]]] = None,
) -> str:
    if not sys.stdin.isatty() or not sys.stdout.isatty() or os.name == "nt":
        return input(prompt).strip()

    try:
        import termios
        import tty
    except Exception:
        return input(prompt).strip()

    fd = sys.stdin.fileno()
    original = termios.tcgetattr(fd)
    buffer = ""
    cursor = 0
    history_index = len(history_entries)
    history_draft = ""
    last_block_lines = 1
    last_panel_hidden = False
    completion_state: Optional[Dict[str, Any]] = None
    history_view_from_start = False

    def reset_completion_state() -> None:
        nonlocal completion_state
        completion_state = None

    def insert_text(text: str) -> None:
        nonlocal buffer
        nonlocal cursor
        nonlocal history_index
        nonlocal history_view_from_start
        chunk = str(text or "")
        if not chunk:
            return
        buffer = buffer[:cursor] + chunk + buffer[cursor:]
        cursor += len(chunk)
        history_index = len(history_entries)
        history_view_from_start = False
        reset_completion_state()

    def move_to_input_origin() -> None:
        if last_block_lines > 1:
            sys.stdout.write(f"\x1b[{last_block_lines - 1}A")
        sys.stdout.write("\r")

    def render(panel_hidden: bool = False) -> None:
        nonlocal last_block_lines
        nonlocal last_panel_hidden
        width = shutil.get_terminal_size((100, 24)).columns
        input_line, cursor_col = format_repl_input_line(
            prompt,
            buffer,
            cursor,
            width,
            prefer_start=history_view_from_start,
        )
        panel_lines: List[str] = []
        if not panel_hidden and callable(panel_builder):
            try:
                raw_panel_lines = list(panel_builder(buffer, cursor) or [])
            except Exception:
                raw_panel_lines = []
            panel_lines = [truncate_repl_panel_line(line, width) for line in raw_panel_lines[:24]]
            if raw_panel_lines and str(raw_panel_lines[0] or "").startswith("help>"):
                panel_lines = colorize_command_help_lines_for_terminal(panel_lines)
            else:
                panel_lines = colorize_markdown_lines_for_terminal(panel_lines)
        move_to_input_origin()
        sys.stdout.write("\x1b[J")
        sys.stdout.write(input_line)
        for line in panel_lines:
            sys.stdout.write("\r\n" + line)
        if panel_lines:
            sys.stdout.write(f"\x1b[{len(panel_lines)}A")
        sys.stdout.write("\r")
        if cursor_col > 0:
            sys.stdout.write(f"\x1b[{cursor_col}C")
        sys.stdout.flush()
        last_block_lines = 1 + len(panel_lines)
        last_panel_hidden = panel_hidden

    def finalize_line() -> None:
        move_to_input_origin()
        sys.stdout.write("\x1b[J")
        sys.stdout.write(f"{prompt}{buffer}\r\n")
        sys.stdout.flush()

    def apply_completion() -> None:
        nonlocal buffer
        nonlocal cursor
        nonlocal completion_state
        buffer, cursor, completion_state = apply_repl_completion_state(
            buffer,
            cursor,
            completion_state,
            current_cell_id=current_cell_id,
            action_refs=list(action_refs or []),
        )

    try:
        tty.setraw(fd)
        sys.stdout.write("\x1b[?2004h")
        sys.stdout.flush()
        render(panel_hidden=False)
        while True:
            raw = os.read(fd, 1)
            if not raw:
                raise EOFError
            ch = raw.decode("utf-8", "ignore")
            if ch in {"\r", "\n"}:
                finalize_line()
                return buffer.strip()
            if ch == "\x03":
                move_to_input_origin()
                sys.stdout.write("\x1b[J\r\n")
                sys.stdout.flush()
                raise KeyboardInterrupt
            if ch == "\x04":
                if not buffer:
                    move_to_input_origin()
                    sys.stdout.write("\x1b[J\r\n")
                    sys.stdout.flush()
                    raise EOFError
                if cursor < len(buffer):
                    buffer = buffer[:cursor] + buffer[cursor + 1 :]
                    reset_completion_state()
                    render(panel_hidden=False)
                continue
            if ch == "\t":
                apply_completion()
                render(panel_hidden=False)
                continue
            if ch in {"\x7f", "\x08"}:
                if cursor > 0:
                    buffer = buffer[: cursor - 1] + buffer[cursor:]
                    cursor -= 1
                    reset_completion_state()
                render(panel_hidden=False)
                continue
            if ch == "\x01":
                cursor = 0
                render(panel_hidden=False)
                continue
            if ch == "\x05":
                cursor = len(buffer)
                render(panel_hidden=False)
                continue
            if ch == "\x1b":
                sequence = read_escape_sequence(fd)
                if sequence == "\x1b[200~":
                    pasted = normalize_repl_pasted_text(read_bracketed_paste(fd))
                    insert_text(pasted)
                    render(panel_hidden=False)
                    continue
                if sequence == "\x1b":
                    reset_completion_state()
                    render(panel_hidden=True)
                    continue
                if sequence in {"\x1b[A", "\x1bOA"}:
                    if history_entries:
                        if history_index == len(history_entries):
                            history_draft = buffer
                        history_index = max(0, history_index - 1)
                        buffer = history_entries[history_index]
                        cursor = len(buffer)
                        history_view_from_start = True
                        reset_completion_state()
                    render(panel_hidden=False)
                    continue
                if sequence in {"\x1b[B", "\x1bOB"}:
                    if history_entries:
                        if history_index < len(history_entries) - 1:
                            history_index += 1
                            buffer = history_entries[history_index]
                        else:
                            history_index = len(history_entries)
                            buffer = history_draft
                        cursor = len(buffer)
                        history_view_from_start = True
                        reset_completion_state()
                    render(panel_hidden=False)
                    continue
                if sequence in {"\x1b[C", "\x1bOC"}:
                    history_view_from_start = False
                    cursor = min(len(buffer), cursor + 1)
                    render(panel_hidden=False)
                    continue
                if sequence in {"\x1b[D", "\x1bOD"}:
                    history_view_from_start = False
                    cursor = max(0, cursor - 1)
                    render(panel_hidden=False)
                    continue
                if sequence in {"\x1b[H", "\x1bOH"}:
                    history_view_from_start = False
                    cursor = 0
                    render(panel_hidden=False)
                    continue
                if sequence in {"\x1b[F", "\x1bOF"}:
                    history_view_from_start = False
                    cursor = len(buffer)
                    render(panel_hidden=False)
                    continue
                if sequence == "\x1b[3~":
                    if cursor < len(buffer):
                        buffer = buffer[:cursor] + buffer[cursor + 1 :]
                        history_view_from_start = False
                        reset_completion_state()
                    render(panel_hidden=False)
                    continue
                render(panel_hidden=last_panel_hidden)
                continue
            if ch and ch >= " ":
                insert_text(ch)
                render(panel_hidden=False)
    finally:
        sys.stdout.write("\x1b[?2004l")
        sys.stdout.flush()
        termios.tcsetattr(fd, termios.TCSADRAIN, original)


def browser_foreground_mode(env: Optional[Mapping[str, str]] = None) -> str:
    values = env or os.environ
    default_mode = "submit" if sys.platform == "darwin" else "off"
    raw = str(values.get("SKY_FOREGROUND_BROWSER", default_mode) or "").strip().lower()
    if sys.platform != "darwin":
        return "off"
    if raw in {"0", "false", "off", "no", "none"}:
        return "off"
    if raw in {"1", "true", "on", "yes", "poll", "always", "aggressive"}:
        return "poll"
    if raw in {"submit", "once", "minimal"}:
        return "submit"
    return "submit"


def browser_foreground_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    return browser_foreground_mode(env) != "off"


def browser_application_name_from_env(env: Optional[Mapping[str, str]] = None) -> str:
    values = env or os.environ
    return str(values.get("SKY_BROWSER_APP") or "Google Chrome").strip() or "Google Chrome"


def browser_window_parking_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    values = env or os.environ
    default = "1" if sys.platform == "darwin" else "0"
    raw = str(values.get("SKY_FOREGROUND_BROWSER_PARK", default) or "").strip().lower()
    if raw in {"0", "false", "off", "no", "none"}:
        return False
    if raw in {"1", "true", "on", "yes", "auto"}:
        return sys.platform == "darwin"
    return sys.platform == "darwin"


def terminal_application_name_from_env(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    values = env or os.environ
    term_program = str(values.get("TERM_PROGRAM") or "").strip()
    mapping = {
        "Apple_Terminal": "Terminal",
        "iTerm.app": "iTerm2",
        "WarpTerminal": "Warp",
        "Warp": "Warp",
        "vscode": "Visual Studio Code",
        "WezTerm": "WezTerm",
        "Hyper": "Hyper",
    }
    return mapping.get(term_program) or None


def activate_application(application_name: Optional[str], timeout_s: int = 3) -> bool:
    name = str(application_name or "").strip()
    if not name or sys.platform != "darwin":
        return False
    script = f'tell application "{name.replace(chr(34), chr(92) + chr(34))}" to activate'
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=max(1, int(timeout_s)),
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return int(proc.returncode) == 0


def current_frontmost_application_name(timeout_s: int = 3) -> Optional[str]:
    if sys.platform != "darwin":
        return None
    script = 'tell application "System Events" to get name of first application process whose frontmost is true'
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=max(1, int(timeout_s)),
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if int(proc.returncode) != 0:
        return None
    name = str(proc.stdout or "").strip()
    return name or None


def front_window_bounds(application_name: Optional[str], timeout_s: int = 3) -> Optional[Tuple[int, int, int, int]]:
    name = str(application_name or "").strip()
    if not name or sys.platform != "darwin":
        return None
    escaped_name = name.replace(chr(34), chr(92) + chr(34))
    script = (
        f'tell application "{escaped_name}"\n'
        'if it is not running then return ""\n'
        'if (count of windows) = 0 then return ""\n'
        'set winBounds to bounds of front window\n'
        'return (item 1 of winBounds as string) & "," & (item 2 of winBounds as string) & "," & (item 3 of winBounds as string) & "," & (item 4 of winBounds as string)\n'
        "end tell"
    )
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=max(1, int(timeout_s)),
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if int(proc.returncode) != 0:
        return None
    values = [part.strip() for part in str(proc.stdout or "").split(",")]
    if len(values) != 4:
        return None
    try:
        left, top, right, bottom = (int(values[0]), int(values[1]), int(values[2]), int(values[3]))
    except ValueError:
        return None
    if right <= left or bottom <= top:
        return None
    return (left, top, right, bottom)


def set_front_window_bounds(
    application_name: Optional[str],
    bounds: Optional[Tuple[int, int, int, int]],
    timeout_s: int = 3,
) -> bool:
    name = str(application_name or "").strip()
    if not name or sys.platform != "darwin" or not bounds:
        return False
    left, top, right, bottom = bounds
    escaped_name = name.replace(chr(34), chr(92) + chr(34))
    script = (
        f'tell application "{escaped_name}"\n'
        'if it is not running then return false\n'
        'if (count of windows) = 0 then return false\n'
        f"set bounds of front window to {{{int(left)}, {int(top)}, {int(right)}, {int(bottom)}}}\n"
        "return true\n"
        "end tell"
    )
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=max(1, int(timeout_s)),
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return int(proc.returncode) == 0


def offscreen_window_bounds(bounds: Tuple[int, int, int, int], margin: int = 120) -> Tuple[int, int, int, int]:
    left, top, right, bottom = bounds
    width = max(1, int(right) - int(left))
    safe_margin = max(40, int(margin))
    return (-width - safe_margin, int(top), -safe_margin, int(bottom))


def current_foreground_browser_context_mode() -> str:
    if _FOREGROUND_BROWSER_CONTEXT_STACK:
        return str(_FOREGROUND_BROWSER_CONTEXT_STACK[-1] or "off")
    return "off"


@contextmanager
def foreground_browser_context(mode: str) -> Iterable[None]:
    resolved_mode = str(mode or "off").strip().lower()
    if resolved_mode not in {"off", "hold", "pulse"}:
        resolved_mode = "off"
    if resolved_mode == "off" or sys.platform != "darwin":
        yield
        return
    browser_app = browser_application_name_from_env()
    terminal_app = terminal_application_name_from_env()
    original_frontmost_app = current_frontmost_application_name() or terminal_app
    restore_app = (
        str(original_frontmost_app or "").strip()
        if original_frontmost_app and str(original_frontmost_app).strip() != browser_app
        else None
    )
    parked_bounds: Optional[Tuple[int, int, int, int]] = None
    if resolved_mode == "hold":
        if restore_app and browser_window_parking_enabled():
            current_bounds = front_window_bounds(browser_app)
            if current_bounds:
                parked_target = offscreen_window_bounds(current_bounds)
                if set_front_window_bounds(browser_app, parked_target):
                    parked_bounds = current_bounds
        if not original_frontmost_app or str(original_frontmost_app).strip() != browser_app:
            activate_application(browser_app)
    _FOREGROUND_BROWSER_CONTEXT_STACK.append(resolved_mode)
    try:
        yield
    finally:
        if _FOREGROUND_BROWSER_CONTEXT_STACK:
            _FOREGROUND_BROWSER_CONTEXT_STACK.pop()
        if resolved_mode == "hold" and restore_app:
            activate_application(restore_app)
        if parked_bounds:
            set_front_window_bounds(browser_app, parked_bounds)


def normalize_for_match(value: str) -> str:
    return " ".join(str(value or "").lower().split())


def probe_has_new_user_turn(
    baseline_probe: Optional[Dict[str, Any]],
    probe: Optional[Dict[str, Any]],
    expected_prompt: str,
) -> bool:
    if not probe:
        return False
    baseline_user_count = int((baseline_probe or {}).get("user_count") or 0)
    baseline_user_hash = str((baseline_probe or {}).get("latest_user_hash") or "")
    current_user_count = int(probe.get("user_count") or 0)
    current_user_hash = str(probe.get("latest_user_hash") or "")
    current_user_text = str(probe.get("latest_user_text") or "")

    changed = (current_user_count > baseline_user_count) or (
        current_user_hash and baseline_user_hash and current_user_hash != baseline_user_hash
    )
    if not changed:
        return False

    expected_norm = normalize_for_match(expected_prompt)
    current_norm = normalize_for_match(current_user_text)
    if not expected_norm:
        return True
    if not current_norm:
        return False
    return expected_norm[:80] in current_norm or current_norm[:80] in expected_norm


def probe_indicates_submit(
    baseline_probe: Optional[Dict[str, Any]],
    probe: Optional[Dict[str, Any]],
    expected_prompt: str,
) -> bool:
    if not probe:
        return False
    if probe_has_new_user_turn(baseline_probe, probe, expected_prompt):
        return True

    baseline_count = int((baseline_probe or {}).get("assistant_count") or 0)
    baseline_hash = str((baseline_probe or {}).get("latest_hash") or "")
    current_count = int((probe or {}).get("assistant_count") or 0)
    current_hash = str((probe or {}).get("latest_hash") or "")
    generating = bool((probe or {}).get("generating"))
    if generating:
        return True
    return (current_count > baseline_count) or (
        current_hash and baseline_hash and current_hash != baseline_hash
    )


def wait_for_assistant_response(
    client: MCPClient,
    agent_id: str,
    js_tools: Sequence[str],
    baseline_count: int,
    baseline_hash: str,
    baseline_user_count: int,
    baseline_user_hash: str,
    expected_prompt: str,
    timeout_s: int,
    poll_interval_s: float,
    debug: bool = False,
) -> Tuple[Optional[str], bool, bool, bool]:
    spinner = ["|", "/", "-", "\\"]
    spin_i = 0
    start = time.time()
    last_text = ""
    stable_polls = 0
    last_hash_seen = ""
    stable_required = max(2, int(DEFAULT_RENDER_STABLE_POLLS))
    settle_seconds = max(0.2, float(DEFAULT_RENDER_SETTLE_SECONDS))
    user_submitted = False
    saw_generating = False
    generating_stopped_at: Optional[float] = None
    previous_generating: Optional[bool] = None

    while True:
        elapsed = time.time() - start
        if elapsed >= timeout_s:
            if debug:
                print(
                    f"\n[debug] render timeout elapsed={elapsed:.1f}s "
                    f"user_submitted={user_submitted} stable={stable_polls}",
                    file=sys.stderr,
                )
            print("\rthinking... timeout reached                       ")
            return (last_text or None), user_submitted, False, True

        probe = read_assistant_probe(
            client=client,
            agent_id=agent_id,
            js_tools=js_tools,
            label="assistant probe",
        )
        if probe is None:
            probe = {}

        current_count = int(probe.get("assistant_count") or 0)
        current_hash = str(probe.get("latest_hash") or "")
        current_text = str(probe.get("latest_text") or "").strip()
        current_user_count = int(probe.get("user_count") or 0)
        current_user_hash = str(probe.get("latest_user_hash") or "")
        current_user_text = str(probe.get("latest_user_text") or "")
        changed = (current_count > baseline_count) or (
            current_hash and baseline_hash and current_hash != baseline_hash
        )
        if current_text and changed:
            last_text = current_text
        generating = bool(probe.get("generating"))
        if generating:
            saw_generating = True
            generating_stopped_at = None
        elif changed and current_text and generating_stopped_at is None:
            generating_stopped_at = time.time()

        user_changed = (current_user_count > baseline_user_count) or (
            current_user_hash and baseline_user_hash and current_user_hash != baseline_user_hash
        )
        if user_changed:
            if current_user_count > baseline_user_count:
                user_submitted = True
            else:
                expected_norm = normalize_for_match(expected_prompt)
                user_norm = normalize_for_match(current_user_text)
                if not expected_norm:
                    user_submitted = True
                elif user_norm and (
                    expected_norm[:80] in user_norm or user_norm[:80] in expected_norm
                ):
                    user_submitted = True

        # Some pages don't expose user turns reliably; when generation clearly started and
        # assistant output changed, treat prompt as submitted to avoid false negatives.
        if saw_generating and changed and current_text and not user_submitted and elapsed >= 6:
            user_submitted = True
        if (not user_submitted) and changed and current_text and elapsed >= 8:
            user_submitted = True

        if current_hash and current_hash == last_hash_seen:
            stable_polls += 1
        else:
            stable_polls = 0
        last_hash_seen = current_hash or last_hash_seen

        if debug and previous_generating is not None and previous_generating != generating:
            print(
                f"\n[debug] render generating={generating} stable={stable_polls} "
                f"assistant_count={current_count}",
                file=sys.stderr,
            )
        previous_generating = generating

        settled_for = 0.0
        if generating_stopped_at is not None:
            settled_for = max(0.0, time.time() - generating_stopped_at)

        render_complete = user_submitted and changed and current_text and (not generating) and (
            (stable_polls >= stable_required and settled_for >= settle_seconds) or elapsed >= 35
        )
        if render_complete:
            if debug:
                print(
                    f"\n[debug] render complete settled={settled_for:.2f}s stable={stable_polls} "
                    f"assistant_count={current_count}",
                    file=sys.stderr,
                )
            print("\rthinking... done                                 ")
            return current_text, user_submitted, True, False

        if not user_submitted and (not generating) and (not changed) and elapsed >= min(14.0, float(timeout_s)):
            if debug:
                print(
                    f"\n[debug] submit not confirmed elapsed={elapsed:.1f}s",
                    file=sys.stderr,
                )
            print("\rthinking... submit not confirmed                  ")
            return (last_text or None), False, False, False

        spinner_char = spinner[spin_i % len(spinner)]
        spin_i += 1
        print(
            f"\rthinking {spinner_char} ({int(elapsed)}s)",
            end="",
            flush=True,
        )
        time.sleep(max(0.1, poll_interval_s))


def summarize_missing_assistant_response(
    probe: Optional[Dict[str, Any]],
    *,
    timed_out: bool,
    fallback_used: bool,
    render_complete: bool,
) -> str:
    details = probe or {}
    empty_assistant_shell = bool(details.get("empty_assistant_shell"))
    response_nav_count = int(details.get("response_nav_count") or 0)

    if empty_assistant_shell and response_nav_count > 0:
        return "assistant> (ChatGPT created empty response variants without rendered text)"
    if empty_assistant_shell:
        return "assistant> (ChatGPT created an empty assistant turn without rendered text)"
    if timed_out and fallback_used:
        return "assistant> (timed out waiting after fallback submit)"
    if timed_out:
        return "assistant> (timed out waiting for render completion)"
    if not render_complete:
        return "assistant> (response capture incomplete before timeout)"
    if fallback_used:
        return "assistant> (timed out waiting after fallback submit)"
    return "assistant> (no final response captured before timeout)"


def parse_layout_points(layout_text: str) -> List[Tuple[str, int, int]]:
    points: List[Tuple[str, int, int]] = []
    if not layout_text:
        return points

    for label, x_raw, y_raw in re.findall(r"([^|\n@]+)@(\d+),(\d+)", layout_text):
        cleaned_label = label.strip()
        if "page:" in cleaned_label:
            cleaned_label = cleaned_label.split("page:", 1)[1].strip()
        if not cleaned_label:
            continue
        try:
            x = int(x_raw)
            y = int(y_raw)
        except ValueError:
            continue
        points.append((cleaned_label, x, y))
    return points


def choose_send_point(points: Sequence[Tuple[str, int, int]]) -> Optional[Tuple[int, int]]:
    ranked: List[Tuple[int, int, int]] = []
    for label, x, y in points:
        lower = label.lower()
        score = -1
        if "send prompt" in lower:
            score = 100
        elif "send" in lower:
            score = 70
        elif "submit" in lower:
            score = 60
        if score >= 0:
            ranked.append((score, x, y))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (item[0], item[2]), reverse=True)
    _, x, y = ranked[0]
    return x, y


def choose_input_point(points: Sequence[Tuple[str, int, int]]) -> Optional[Tuple[int, int]]:
    ranked: List[Tuple[int, int, int]] = []
    for label, x, y in points:
        lower = label.lower()
        if any(
            blocked in lower
            for blocked in (
                "edit message",
                "copy",
                "share",
                "good response",
                "bad response",
                "open conversation options",
                "previous response",
                "next response",
                "send",
                "cancel",
            )
        ):
            continue
        score = -1
        if label.startswith(">"):
            score = 100
        elif "ask anything" in lower:
            score = 95
        elif "textarea" in lower or "textbox" in lower:
            score = 90
        elif lower.startswith("message") or "message chatgpt" in lower:
            score = 85
        elif "prompt" in lower and "send" not in lower:
            score = 70
        if score >= 0:
            ranked.append((score, x, y))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (item[0], item[2]), reverse=True)
    _, x, y = ranked[0]
    return x, y


def read_prompt_from_stdin() -> str:
    if sys.stdin.isatty():
        return ""
    return sys.stdin.read().strip()


def parse_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def getenv_first(*names: str) -> Optional[str]:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


def load_legacy_install_values(path: Path = LEGACY_INSTALL_ENV_PATH) -> Dict[str, str]:
    return parse_env_file(path)


def maybe_migrate_primary_env(
    api_key: Optional[str],
    agent_id: Optional[str],
    env_path: Path = DEFAULT_AGENT_ENV_PATH,
) -> bool:
    if not api_key and not agent_id:
        return False
    current_values = parse_env_file(env_path)
    changed = False
    if api_key and not current_values.get(PRIMARY_API_KEY_ENV):
        upsert_env_file_value(env_path, PRIMARY_API_KEY_ENV, api_key)
        changed = True
    if agent_id and not current_values.get(PRIMARY_AGENT_ID_ENV):
        upsert_env_file_value(env_path, PRIMARY_AGENT_ID_ENV, agent_id)
        changed = True
    return changed


def infer_agents_endpoint(endpoint: str) -> str:
    clean = endpoint.rstrip("/")
    if clean.endswith("/mcp"):
        return clean[: -len("/mcp")] + "/api/agents"
    return "https://api.unchainedsky.com/api/agents"


def extract_first_agent_id(payload: Any) -> Optional[str]:
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                agent_id = item.get("agent_id") or item.get("id")
                if isinstance(agent_id, str) and agent_id:
                    return agent_id
        return None

    if isinstance(payload, dict):
        agent_id = payload.get("agent_id") or payload.get("id")
        if isinstance(agent_id, str) and agent_id:
            return agent_id
        agents = payload.get("agents")
        if isinstance(agents, list):
            return extract_first_agent_id(agents)
    return None


def fetch_agent_id(api_key: str, endpoint: str, timeout: int) -> Optional[str]:
    url = infer_agents_endpoint(endpoint)
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
    except Exception:
        return None

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None
    return extract_first_agent_id(payload)


def extract_agent_choices(payload: Any) -> List[Dict[str, Any]]:
    items: List[Any]
    if isinstance(payload, list):
        items = list(payload)
    elif isinstance(payload, dict):
        agents = payload.get("agents")
        if isinstance(agents, list):
            items = list(agents)
        else:
            items = [payload]
    else:
        return []

    choices: List[Dict[str, Any]] = []
    seen_ids: set = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        agent_id = item.get("agent_id") or item.get("id")
        if not isinstance(agent_id, str) or not agent_id.strip():
            continue
        agent_id = agent_id.strip()
        if agent_id in seen_ids:
            continue
        seen_ids.add(agent_id)
        label = (
            item.get("name")
            or item.get("label")
            or item.get("display_name")
            or item.get("title")
            or agent_id
        )
        status_raw = str(item.get("status") or item.get("state") or "").strip()
        connected_value = item.get("connected")
        if isinstance(connected_value, bool):
            connected = connected_value
        else:
            connected = status_raw.lower() in {"connected", "online", "active", "ready"}
        choices.append(
            {
                "agent_id": agent_id,
                "label": str(label or agent_id).strip() or agent_id,
                "status": status_raw,
                "connected": connected,
            }
        )
    choices.sort(key=lambda item: (bool(item.get("connected")), str(item.get("label") or "")), reverse=True)
    return choices


def fetch_agent_choices(api_key: str, endpoint: str, timeout: int) -> List[Dict[str, Any]]:
    url = infer_agents_endpoint(endpoint)
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8", "replace")
    payload = json.loads(body)
    return extract_agent_choices(payload)


def format_env_assignment(key: str, value: str) -> str:
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'{key}="{escaped}"'


def upsert_env_file_value(path: Path, key: str, value: str) -> None:
    existing_lines: List[str] = []
    if path.is_file():
        existing_lines = path.read_text(encoding="utf-8").splitlines()
    updated_lines: List[str] = []
    replaced = False
    for raw_line in existing_lines:
        line = raw_line.strip()
        if line and not line.startswith("#") and "=" in raw_line:
            current_key, _ = raw_line.split("=", 1)
            if current_key.strip() == key:
                updated_lines.append(format_env_assignment(key, value))
                replaced = True
                continue
        updated_lines.append(raw_line)
    if not replaced:
        if updated_lines and updated_lines[-1].strip():
            updated_lines.append("")
        updated_lines.append(format_env_assignment(key, value))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(updated_lines).rstrip() + "\n", encoding="utf-8")


def prompt_for_choice(prompt: str, count: int, default_index: int = 1) -> Optional[int]:
    while True:
        try:
            raw = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if not raw:
            return default_index
        if raw.lower() in {"q", "quit", "exit"}:
            return None
        try:
            selected = int(raw)
        except ValueError:
            print(f"Enter a number between 1 and {count}, or q to cancel.")
            continue
        if 1 <= selected <= count:
            return selected
        print(f"Enter a number between 1 and {count}, or q to cancel.")


def prompt_for_confirmation(prompt: str, default: bool = True) -> Optional[bool]:
    while True:
        try:
            raw = input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if not raw:
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        if raw in {"q", "quit", "exit"}:
            return None
        print("Enter y or n, or q to cancel.")


def run_command_with_tty_reply(
    argv: Sequence[str],
    reply: str,
    prompt_markers: Sequence[str],
    timeout_s: int,
) -> Tuple[int, str]:
    if os.name != "posix":
        completed = subprocess.run(
            list(argv),
            input=reply,
            text=True,
            timeout=max(30, int(timeout_s)),
            check=False,
        )
        return int(completed.returncode), ""

    pid, master_fd = pty.fork()
    if pid == 0:
        try:
            os.execvp(str(argv[0]), list(argv))
        except FileNotFoundError:
            os._exit(127)
        except Exception:
            os._exit(1)

    prompt_seen = ""
    output_chunks: List[str] = []
    reply_sent = False
    deadline = time.monotonic() + max(30, int(timeout_s))
    status: Optional[int] = None
    try:
        while True:
            now = time.monotonic()
            if now >= deadline:
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                raise subprocess.TimeoutExpired(list(argv), int(timeout_s))

            timeout_window = min(0.2, max(0.0, deadline - now))
            ready, _, _ = select.select([master_fd], [], [], timeout_window)
            if ready:
                try:
                    chunk = os.read(master_fd, 4096)
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        chunk = b""
                    else:
                        raise
                if chunk:
                    text = chunk.decode("utf-8", "replace")
                    sys.stdout.write(text)
                    sys.stdout.flush()
                    output_chunks.append(text)
                    prompt_seen = (prompt_seen + text)[-1024:]
                    if not reply_sent and any(marker in prompt_seen for marker in prompt_markers):
                        os.write(master_fd, reply.encode("utf-8"))
                        reply_sent = True
                elif status is not None:
                    break

            if status is None:
                done_pid, done_status = os.waitpid(pid, os.WNOHANG)
                if done_pid == pid:
                    status = done_status
                    if not ready:
                        break

        if status is None:
            _, status = os.waitpid(pid, 0)
    except KeyboardInterrupt:
        try:
            os.kill(pid, signal.SIGINT)
        except ProcessLookupError:
            pass
        raise
    finally:
        os.close(master_fd)

    if os.WIFEXITED(status):
        return int(os.WEXITSTATUS(status)), "".join(output_chunks)
    if os.WIFSIGNALED(status):
        return 128 + int(os.WTERMSIG(status)), "".join(output_chunks)
    return 1, "".join(output_chunks)


def extract_installer_output_values(output: str) -> Tuple[Optional[str], Optional[str]]:
    agent_matches = re.findall(r"^\s*Agent ID:\s+(\S+)\s*$", output, flags=re.MULTILINE)
    api_key_matches = re.findall(r"^\s*API key:\s+(\S+)\s*$", output, flags=re.MULTILINE)
    agent_id = agent_matches[-1].strip() if agent_matches else None
    api_key = api_key_matches[-1].strip() if api_key_matches else None
    return api_key, agent_id


def run_upstream_install_script(
    install_url: str = INSTALL_SCRIPT_URL,
    timeout_s: int = DEFAULT_INSTALL_TIMEOUT,
    daemon_choice: str = "d",
) -> Tuple[bool, str, Optional[str], Optional[str]]:
    try:
        with urllib.request.urlopen(install_url, timeout=max(30, int(timeout_s))) as response:
            script_body = response.read().decode("utf-8", "replace")
    except Exception as exc:
        return False, f"install download failed: {exc}", None, None

    script_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".sh", encoding="utf-8") as handle:
            handle.write(script_body)
            script_path = Path(handle.name)
        script_path.chmod(0o700)
        returncode, output = run_command_with_tty_reply(
            ["bash", str(script_path)],
            reply=f"{daemon_choice}\n",
            prompt_markers=("Start now? [d]aemon / [f]oreground / [N]o:",),
            timeout_s=max(30, int(timeout_s)),
        )
    except FileNotFoundError:
        return False, "bash not found", None, None
    except subprocess.TimeoutExpired:
        return False, f"install timed out after {int(timeout_s)}s", None, None
    except KeyboardInterrupt:
        return False, "install cancelled", None, None
    finally:
        if script_path is not None:
            script_path.unlink(missing_ok=True)

    api_key, agent_id = extract_installer_output_values(output)
    if int(returncode) != 0:
        return False, f"install exited with code {returncode}", api_key, agent_id
    return True, "installed", api_key, agent_id


def wait_for_agent_choices(
    api_key: str,
    endpoint: str,
    timeout: int,
    wait_s: float = DEFAULT_AGENT_DISCOVERY_GRACE_SECONDS,
    poll_s: float = DEFAULT_AGENT_DISCOVERY_POLL_SECONDS,
) -> Tuple[List[Dict[str, Any]], Optional[Exception]]:
    deadline = time.monotonic() + max(0.0, float(wait_s))
    last_error: Optional[Exception] = None
    while True:
        try:
            choices = fetch_agent_choices(api_key=api_key, endpoint=endpoint, timeout=timeout)
        except Exception as exc:
            last_error = exc
            choices = []
        else:
            last_error = None
            if choices:
                return choices, None

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return [], last_error
        time.sleep(min(max(0.1, float(poll_s)), remaining))


def wait_for_agent_connection(
    api_key: str,
    agent_id: str,
    endpoint: str,
    timeout: int,
    wait_s: float = DEFAULT_AGENT_DISCOVERY_GRACE_SECONDS,
    poll_s: float = DEFAULT_AGENT_DISCOVERY_POLL_SECONDS,
) -> Tuple[bool, bool, Optional[Exception]]:
    deadline = time.monotonic() + max(0.0, float(wait_s))
    last_error: Optional[Exception] = None
    saw_agent = False
    while True:
        try:
            choices = fetch_agent_choices(api_key=api_key, endpoint=endpoint, timeout=timeout)
        except Exception as exc:
            last_error = exc
            choices = []
        else:
            last_error = None
            for item in choices:
                if str(item.get("agent_id") or "").strip() != agent_id:
                    continue
                saw_agent = True
                if bool(item.get("connected")):
                    return True, True, None

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False, saw_agent, last_error
        time.sleep(min(max(0.1, float(poll_s)), remaining))


def maybe_run_first_call_setup(
    api_key: Optional[str],
    agent_id: Optional[str],
    endpoint: str,
    timeout: int,
    env_path: Path = DEFAULT_AGENT_ENV_PATH,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    installed_now = False
    if api_key and agent_id:
        return api_key, agent_id, None
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return api_key, agent_id, None

    print("sky setup")
    print(f"  API key: {'found' if api_key else 'missing'}")
    print(f"  Agent id: {'found' if agent_id else 'missing'}")

    if not api_key:
        assume_yes_raw = os.getenv(SETUP_ASSUME_YES_ENV, "").strip().lower()
        if assume_yes_raw in {"1", "true", "yes", "y", "on"}:
            install_now = True
            print(f"{SETUP_ASSUME_YES_ENV}=1 -> running installer")
        else:
            install_now = prompt_for_confirmation("Run Sky installer now? [Y/n]: ", default=True)
        if install_now is None:
            print("setup cancelled")
            return api_key, agent_id, "setup-cancelled"
        if not install_now:
            print(f"Run: curl -fsSL https://api.unchainedsky.com/install.sh | bash")
            print(f"Then rerun sky. Expected config path: {env_path}")
            return api_key, agent_id, "setup-instructions"
        ok, detail, installer_api_key, installer_agent_id = run_upstream_install_script(
            timeout_s=max(DEFAULT_INSTALL_TIMEOUT, int(timeout))
        )
        if not ok:
            print(f"Installer failed: {detail}")
            return api_key, agent_id, "setup-instructions"
        installed_now = True
        resolved_api_key, resolved_agent_id, _ = resolve_credentials(
            api_key_arg=None,
            agent_id_arg=None,
            endpoint=endpoint,
            timeout=timeout,
        )
        api_key = resolved_api_key or installer_api_key
        agent_id = resolved_agent_id or installer_agent_id
        if api_key:
            maybe_migrate_primary_env(api_key=api_key, agent_id=agent_id, env_path=env_path)
        print(f"  API key: {'found' if api_key else 'missing'}")
        print(f"  Agent id: {'found' if agent_id else 'missing'}")
        if api_key and agent_id:
            return api_key, agent_id, "setup-wizard"
        if not api_key:
            print("Installer completed but SKY_API_KEY is still missing.")
            return api_key, agent_id, "setup-instructions"

    if agent_id:
        return api_key, agent_id, None

    print("Looking for connected agents...")
    try:
        agent_choices = fetch_agent_choices(api_key=api_key, endpoint=endpoint, timeout=timeout)
    except Exception as exc:
        if not installed_now:
            print(f"Agent discovery failed: {exc}")
            print("Start the Sky agent and rerun sky.")
            return api_key, agent_id, "setup-instructions"
        print("Waiting for the new agent to register...")
        agent_choices, wait_error = wait_for_agent_choices(api_key=api_key, endpoint=endpoint, timeout=timeout)
        if not agent_choices:
            print(f"Agent discovery failed: {wait_error or exc}")
            print("Start the Sky agent and rerun sky.")
            return api_key, agent_id, "setup-instructions"
    else:
        if installed_now and not agent_choices:
            print("Waiting for the new agent to register...")
            waited_choices, wait_error = wait_for_agent_choices(api_key=api_key, endpoint=endpoint, timeout=timeout)
            if waited_choices:
                agent_choices = waited_choices
            elif wait_error is not None:
                print(f"Agent discovery failed: {wait_error}")
                print("Start the Sky agent and rerun sky.")
                return api_key, agent_id, "setup-instructions"

    connected_choices = [item for item in agent_choices if bool(item.get("connected"))]
    active_choices = connected_choices or agent_choices
    if not active_choices:
        print("No agents found.")
        print("Start the Sky agent and rerun sky.")
        return api_key, agent_id, "setup-instructions"

    if len(active_choices) == 1:
        chosen = active_choices[0]
        selected_agent_id = str(chosen.get("agent_id") or "")
        print(f"Using agent: {selected_agent_id}")
    else:
        print("Available agents:")
        for idx, item in enumerate(active_choices, start=1):
            label = str(item.get("label") or item.get("agent_id") or "").strip()
            current_agent_id = str(item.get("agent_id") or "").strip()
            status = str(item.get("status") or "").strip()
            suffix = f" [{status}]" if status else ""
            if label and label != current_agent_id:
                print(f"  {idx}. {label} ({current_agent_id}){suffix}")
            else:
                print(f"  {idx}. {current_agent_id}{suffix}")
        selection = prompt_for_choice(f"Select agent [1-{len(active_choices)}] (default 1): ", len(active_choices))
        if selection is None:
            print("setup cancelled")
            return api_key, agent_id, "setup-cancelled"
        chosen = active_choices[selection - 1]
        selected_agent_id = str(chosen.get("agent_id") or "")

    if not selected_agent_id:
        return api_key, agent_id, "setup-cancelled"

    upsert_env_file_value(env_path, PRIMARY_AGENT_ID_ENV, selected_agent_id)
    print(f"Wrote {PRIMARY_AGENT_ID_ENV} to {env_path}")
    return api_key, selected_agent_id, "setup-wizard"


def install_alias_launcher(
    alias_name: str,
    alias_dir: Path,
    target_script: Path,
    force: bool = False,
) -> Tuple[Path, bool]:
    name = alias_name.strip()
    if not name:
        raise MCPError("Alias name cannot be empty.")
    if "/" in name or "\\" in name:
        raise MCPError("Alias name must not include path separators.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
        raise MCPError(
            "Alias name may only contain letters, numbers, '.', '_', and '-'."
        )

    alias_dir.mkdir(parents=True, exist_ok=True)
    alias_path = alias_dir / name
    target = target_script.resolve()
    launcher_body = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f'SKY_CLI_NAME="{name}" exec python3 "{target}" "$@"\n'
    )

    if alias_path.exists() or alias_path.is_symlink():
        if alias_path.is_file() and not alias_path.is_symlink():
            try:
                current = alias_path.read_text(encoding="utf-8")
                if current == launcher_body:
                    return alias_path, False
            except OSError:
                pass
        if alias_path.is_symlink():
            try:
                current_target = alias_path.resolve(strict=False)
                if current_target == target:
                    return alias_path, False
            except OSError:
                pass
        if not force:
            raise MCPError(
                f"Alias path already exists: {alias_path}. Use --force-alias to overwrite."
            )
        if alias_path.is_dir() and not alias_path.is_symlink():
            raise MCPError(f"Alias path is a directory: {alias_path}")
        alias_path.unlink()

    alias_path.write_text(launcher_body, encoding="utf-8")
    alias_path.chmod(0o755)
    return alias_path, True


def path_contains_dir(path_dir: Path) -> bool:
    target = path_dir.expanduser().resolve()
    raw_path = os.getenv("PATH", "")
    for entry in raw_path.split(os.pathsep):
        if not entry:
            continue
        try:
            if Path(entry).expanduser().resolve() == target:
                return True
        except OSError:
            continue
    return False


def resolve_credentials(
    api_key_arg: Optional[str],
    agent_id_arg: Optional[str],
    endpoint: str,
    timeout: int,
) -> Tuple[Optional[str], Optional[str], str]:
    env_file_values = parse_env_file(DEFAULT_AGENT_ENV_PATH)
    legacy_install_values = load_legacy_install_values()
    env_file_agent_id = env_file_values.get(PRIMARY_AGENT_ID_ENV)

    api_key = (
        api_key_arg
        or getenv_first(PRIMARY_API_KEY_ENV)
        or env_file_values.get(PRIMARY_API_KEY_ENV)
        or legacy_install_values.get(LEGACY_INSTALL_API_KEY_ENV)
    )
    agent_id = agent_id_arg or getenv_first(PRIMARY_AGENT_ID_ENV) or env_file_agent_id

    source = "flags/env"
    if not api_key:
        return None, None, source

    if not env_file_values.get(PRIMARY_API_KEY_ENV) and legacy_install_values.get(LEGACY_INSTALL_API_KEY_ENV):
        source = "legacy-install-env"

    if not agent_id:
        agent_id = fetch_agent_id(api_key=api_key, endpoint=endpoint, timeout=timeout)
        if agent_id:
            source = "auto-discovered-from-api" if source == "flags/env" else f"{source}+auto-discovered-from-api"
        elif env_file_agent_id:
            source = "local-env-file"
    elif agent_id == env_file_agent_id:
        source = "local-env-file"

    return api_key, agent_id, source


def navigate_current_page(
    client: MCPClient,
    agent_id: str,
    url: str,
    navigate_tools: Sequence[str],
    verbose: bool = False,
) -> str:
    nav_args = with_agent_variants(
        [{"url": url}, {"target_url": url}],
        agent_id=agent_id,
    )
    nav_tool, nav_result = call_tool_variants(client, navigate_tools, nav_args, "navigate")
    if verbose:
        print(f"navigate tool: {nav_tool}")
    nav_text = extract_text(nav_result).strip()
    if verbose and nav_text:
        print(nav_text[:1200])
    return nav_text


def run_single_prompt(
    client: MCPClient,
    agent_id: str,
    url: str,
    prompt: str,
    navigate_tools: Sequence[str],
    js_tools: Sequence[str],
    click_tools: Sequence[str],
    type_tools: Sequence[str],
    ddm_tools: Sequence[str],
    submit: bool,
    output_format: str,
    wait_timeout_s: int,
    poll_interval_s: float,
    debug: bool,
) -> None:
    nav_layout_text = navigate_current_page(
        client, agent_id, url, navigate_tools, verbose=debug
    )

    dispatch_prompt(
        client=client,
        agent_id=agent_id,
        prompt=prompt,
        js_tools=js_tools,
        click_tools=click_tools,
        type_tools=type_tools,
        ddm_tools=ddm_tools,
        submit=submit,
        output_format=output_format,
        layout_text=nav_layout_text,
        echo_result=True,
        wait_for_response=submit,
        wait_timeout_s=wait_timeout_s,
        poll_interval_s=poll_interval_s,
        show_dispatch_details=debug,
    )


def run_repl(
    client: MCPClient,
    agent_id: str,
    url: str,
    navigate_tools: Sequence[str],
    js_tools: Sequence[str],
    click_tools: Sequence[str],
    type_tools: Sequence[str],
    ddm_tools: Sequence[str],
    submit: bool,
    output_format: str,
    run_backend: str,
    pyreplab_cmd: Optional[str],
    first_prompt: str,
    wait_timeout_s: int,
    poll_interval_s: float,
    debug: bool,
) -> None:
    current_url = url
    submit_enabled = submit
    output_mode = output_format
    run_backend_mode = (run_backend or DEFAULT_RUN_BACKEND).strip().lower()
    resolved_pyreplab_cmd = resolve_pyreplab_command(pyreplab_cmd)
    pyreplab_session_dir: Optional[str] = None
    if resolved_pyreplab_cmd:
        pyreplab_session_dir = allocate_pyreplab_session_dir(Path.cwd())
    if run_backend_mode not in SUPPORTED_RUN_BACKENDS:
        run_backend_mode = DEFAULT_RUN_BACKEND
    if run_backend_mode == "pyreplab" and not resolved_pyreplab_cmd:
        print("backend: pyreplab unavailable; falling back to local")
        print("hint: install pyreplab, set PYREPLAB_CMD, or pass --pyreplab-cmd '/path/to/pyreplab'")
        run_backend_mode = "local"
    elif run_backend_mode == "pyreplab":
        warmup = start_pyreplab_session(
            pyreplab_cmd=resolved_pyreplab_cmd or [],
            workdir=Path.cwd(),
            session_dir=pyreplab_session_dir,
            timeout_s=8,
        )
        if not warmup.get("ok"):
            print("backend: pyreplab failed to start; falling back to local")
            print(f"detail: {warmup.get('error')}")
            run_backend_mode = "local"
        else:
            print("backend: pyreplab ready")
    history: List[Dict[str, Any]] = []
    history_entries = load_repl_history_entries()
    turn_index = 0
    cell_store: Dict[str, Dict[str, Any]] = {}
    cell_order: List[str] = []
    cell_counters: Dict[str, int] = {}
    current_cell_id: Optional[str] = None
    cell_workspace = Path.cwd() / ".sky_cells"
    current_layout_text = navigate_current_page(
        client, agent_id, current_url, navigate_tools, verbose=debug
    )
    print("interactive mode: /help for commands, /exit to quit, Ctrl-C cancels /run")
    active_action_refs: List[Dict[str, Any]] = []
    active_action_ref_index: Dict[str, str] = {}
    active_turn_view: Optional[Dict[str, Any]] = None

    def record_interactive_turn(turn_result: Dict[str, Any], prompt_text: str) -> None:
        nonlocal turn_index
        nonlocal current_cell_id
        nonlocal active_action_refs
        nonlocal active_action_ref_index
        nonlocal active_turn_view

        turn_index += 1
        created_cells = register_turn_cells(
            turn_result=turn_result,
            turn_index=turn_index,
            cell_store=cell_store,
            cell_order=cell_order,
            cell_counters=cell_counters,
        )
        action_refs = build_turn_action_refs(turn_result, created_cells)
        active_action_refs = list(action_refs)
        active_action_ref_index = build_action_ref_index(active_action_refs)
        if active_action_refs:
            current_cell_id = str(active_action_refs[-1].get("cell_id") or "") or current_cell_id
        elif created_cells:
            current_cell_id = str(created_cells[-1].get("id") or "") or current_cell_id

        assistant_text = str(turn_result.get("assistant_text") or "").strip()
        if assistant_text:
            active_turn_view = build_interactive_turn_view(
                assistant_text=assistant_text,
                output_format=output_mode,
                artifacts=turn_result.get("artifacts"),
                action_refs=active_action_refs,
            )
            rendered_output = str(active_turn_view.get("rendered_output") or "")
            if output_mode == "json":
                print(rendered_output)
            else:
                print("assistant>")
                print(colorize_markdown_text_for_terminal(rendered_output))
            turn_result["rendered_output"] = rendered_output
        else:
            active_turn_view = None

        history.append(
            {
                "prompt": prompt_text,
                "assistant_text": assistant_text,
                "cell_ids": list(turn_result.get("cell_ids") or []),
                "action_refs": list(active_action_refs),
                "artifacts": turn_result.get("artifacts"),
            }
        )

    def rebuild_active_turn_view_from_entry(entry: Dict[str, Any]) -> None:
        nonlocal active_turn_view
        assistant_text = str(entry.get("assistant_text") or "").strip()
        if not assistant_text:
            active_turn_view = None
            return
        active_turn_view = build_interactive_turn_view(
            assistant_text=assistant_text,
            output_format=output_mode,
            artifacts=entry.get("artifacts"),
            action_refs=list(entry.get("action_refs") or []),
        )

    def live_panel_builder(buffer: str, cursor: int) -> List[str]:
        del cursor
        return build_live_repl_panel_lines(
            buffer,
            current_cell_id=current_cell_id,
            action_refs=active_action_refs,
            action_ref_index=active_action_ref_index,
            turn_view=active_turn_view,
        )

    if not first_prompt:
        # Clear stale draft text from previous runs so the interactive prompt starts clean.
        dispatch_prompt(
            client=client,
            agent_id=agent_id,
            prompt="",
            js_tools=js_tools,
            click_tools=click_tools,
            type_tools=type_tools,
            ddm_tools=ddm_tools,
            submit=False,
            output_format=output_mode,
            layout_text=current_layout_text,
            echo_result=False,
            wait_for_response=False,
            wait_timeout_s=wait_timeout_s,
            poll_interval_s=poll_interval_s,
            show_dispatch_details=debug,
        )

    if first_prompt:
        initial_result = dispatch_prompt(
            client=client,
            agent_id=agent_id,
            prompt=first_prompt,
            js_tools=js_tools,
            click_tools=click_tools,
            type_tools=type_tools,
            ddm_tools=ddm_tools,
            submit=submit_enabled,
            output_format=output_mode,
            layout_text=current_layout_text,
            echo_result=False,
            wait_for_response=submit_enabled,
            wait_timeout_s=wait_timeout_s,
            poll_interval_s=poll_interval_s,
            show_dispatch_details=debug,
        )
        record_interactive_turn(initial_result, first_prompt)

    try:
        while True:
            try:
                line = read_live_repl_input(
                    "> ",
                    history_entries=history_entries,
                    panel_builder=live_panel_builder,
                    current_cell_id=current_cell_id,
                    action_refs=active_action_refs,
                ).strip()
            except EOFError:
                print()
                break
            except KeyboardInterrupt:
                print()
                print("cancelled")
                continue
            if not line:
                continue
            add_repl_history_entry_to_list(history_entries, line)
            lower = line.lower()
            if lower in {"quit", "exit", ":q", "/exit", "/quit"}:
                break
            if lower in {"/help", "/h"}:
                print("\n".join(colorize_command_help_lines_for_terminal(format_repl_help_lines())))
                continue
            if lower.startswith("/url "):
                next_url = line[5:].strip()
                if not next_url:
                    print("usage: /url https://example.com")
                    continue
                current_url = next_url
                current_layout_text = navigate_current_page(
                    client, agent_id, current_url, navigate_tools, verbose=debug
                )
                continue
            if lower.startswith("/submit "):
                mode = line.split(None, 1)[1].strip().lower()
                if mode in {"on", "true", "1"}:
                    submit_enabled = True
                    print("submit: on")
                elif mode in {"off", "false", "0"}:
                    submit_enabled = False
                    print("submit: off")
                else:
                    print("usage: /submit on|off")
                continue
            if lower == "/backend" or lower.startswith("/backend "):
                parts = line.split()
                if len(parts) == 1:
                    availability = "available" if resolved_pyreplab_cmd else "unavailable"
                    print(f"backend: {run_backend_mode} (pyreplab {availability})")
                    if resolved_pyreplab_cmd:
                        print("pyreplab_cmd: " + " ".join(resolved_pyreplab_cmd))
                        if pyreplab_session_dir:
                            print(f"pyreplab_session: {pyreplab_session_dir}")
                    else:
                        print("hint: --pyreplab-cmd '/path/to/pyreplab' or set PYREPLAB_CMD")
                    continue
                if len(parts) != 2:
                    print("usage: /backend [local|pyreplab]")
                    continue
                requested = parts[1].strip().lower()
                if requested not in SUPPORTED_RUN_BACKENDS:
                    print("usage: /backend [local|pyreplab]")
                    continue
                if requested == "pyreplab":
                    if not resolved_pyreplab_cmd:
                        print("backend: pyreplab unavailable")
                        print("hint: --pyreplab-cmd '/path/to/pyreplab' or set PYREPLAB_CMD")
                        continue
                    warmup = start_pyreplab_session(
                        pyreplab_cmd=resolved_pyreplab_cmd,
                        workdir=Path.cwd(),
                        session_dir=pyreplab_session_dir,
                        timeout_s=8,
                    )
                    if not warmup.get("ok"):
                        print("backend: pyreplab failed to start")
                        print(f"detail: {warmup.get('error')}")
                        continue
                    run_backend_mode = "pyreplab"
                    print("backend: pyreplab ready")
                else:
                    run_backend_mode = "local"
                    print("backend: local")
                continue
            if lower == "/ddm":
                maybe_show_ddm(client, agent_id, ddm_tools)
                continue
            if lower.startswith("/pyfile "):
                args, parse_error = split_repl_command_args(line)
                if parse_error:
                    print(f"command parse error: {parse_error}")
                    continue
                assert args is not None
                if len(args) != 2:
                    print("usage: /pyfile <path.py>")
                    continue
                if not resolved_pyreplab_cmd:
                    print("pyreplab passthrough unavailable")
                    print("hint: --pyreplab-cmd '/path/to/pyreplab' or set PYREPLAB_CMD")
                    continue
                script_path = Path(args[1]).expanduser()
                try:
                    code_text = script_path.read_text(encoding="utf-8")
                except OSError as exc:
                    print(f"pyreplab passthrough error: cannot read {script_path}: {exc}")
                    continue
                result = execute_pyreplab_code(
                    code=code_text,
                    pyreplab_cmd=resolved_pyreplab_cmd,
                    workdir=Path.cwd(),
                    session_dir=pyreplab_session_dir,
                    timeout_s=30,
                )
                if not result.get("ok"):
                    print(f"pyreplab passthrough error: {result.get('error')}")
                command = " ".join(result.get("command") or [])
                exit_code = int(result.get("exit_code", 0))
                print(f"pyreplab> file={script_path} exit={exit_code} cmd={command}")
                stdout_text = str(result.get("stdout") or "").strip()
                stderr_text = str(result.get("stderr") or "").strip()
                print("stdout>")
                print(stdout_text if stdout_text else "(empty)")
                print("stderr>")
                print(stderr_text if stderr_text else "(empty)")
                continue
            if lower.startswith("/py "):
                code_text = line[4:].strip()
                if not code_text:
                    print("usage: /py <python code>")
                    continue
                if not resolved_pyreplab_cmd:
                    print("pyreplab passthrough unavailable")
                    print("hint: --pyreplab-cmd '/path/to/pyreplab' or set PYREPLAB_CMD")
                    continue
                result = execute_pyreplab_code(
                    code=code_text,
                    pyreplab_cmd=resolved_pyreplab_cmd,
                    workdir=Path.cwd(),
                    session_dir=pyreplab_session_dir,
                    timeout_s=30,
                )
                if not result.get("ok"):
                    print(f"pyreplab passthrough error: {result.get('error')}")
                command = " ".join(result.get("command") or [])
                exit_code = int(result.get("exit_code", 0))
                print(f"pyreplab> exit={exit_code} cmd={command}")
                stdout_text = str(result.get("stdout") or "").strip()
                stderr_text = str(result.get("stderr") or "").strip()
                print("stdout>")
                print(stdout_text if stdout_text else "(empty)")
                print("stderr>")
                print(stderr_text if stderr_text else "(empty)")
                continue
            if lower.startswith("/format "):
                mode = line.split(None, 1)[1].strip().lower()
                if mode in SUPPORTED_OUTPUT_FORMATS:
                    output_mode = mode
                    print(f"format: {output_mode}")
                    if history:
                        rebuild_active_turn_view_from_entry(history[-1])
                else:
                    print("usage: /format markdown|plain|json")
                continue
            if lower == "/last":
                if not history:
                    print("history: empty")
                    continue
                entry = history[-1]
                print("user>")
                print(str(entry.get("prompt") or ""))
                assistant_text = str(entry.get("assistant_text") or "").strip()
                if not assistant_text:
                    active_action_refs = []
                    active_action_ref_index = {}
                    active_turn_view = None
                    print("assistant> (no response captured)")
                    continue
                active_action_refs = list(entry.get("action_refs") or [])
                active_action_ref_index = build_action_ref_index(active_action_refs)
                if active_action_refs:
                    current_cell_id = str(active_action_refs[-1].get("cell_id") or "") or current_cell_id
                rebuild_active_turn_view_from_entry(entry)
                rendered = str((active_turn_view or {}).get("rendered_output") or "")
                if output_mode == "json":
                    print(rendered)
                else:
                    print("assistant>")
                    print(colorize_markdown_text_for_terminal(rendered))
                continue
            if lower == "/history" or lower.startswith("/history "):
                parts = line.split()
                limit = 10
                if len(parts) == 2:
                    try:
                        limit = max(1, int(parts[1]))
                    except ValueError:
                        print("usage: /history [n]")
                        continue
                elif len(parts) > 2:
                    print("usage: /history [n]")
                    continue

                if not history:
                    print("history: empty")
                    continue
                start = max(0, len(history) - limit)
                for idx, entry in enumerate(history[start:], start=start + 1):
                    user_preview = history_preview(str(entry.get("prompt") or ""), limit=72)
                    assistant_preview = history_preview(str(entry.get("assistant_text") or ""), limit=88)
                    if not assistant_preview:
                        assistant_preview = "(no response captured)"
                    print(f"[{idx}] user: {user_preview}")
                    print(f"    assistant: {assistant_preview}")
                    action_refs = list(entry.get("action_refs") or [])
                    if action_refs:
                        ref_preview = " ".join(
                            f"{ref.get('handle')}->{ref.get('cell_id')}"
                            for ref in action_refs
                            if str(ref.get("handle") or "").strip() and str(ref.get("cell_id") or "").strip()
                        )
                        if ref_preview:
                            print(f"    refs: {ref_preview}")
                            continue
                    cell_ids = list(entry.get("cell_ids") or [])
                    if cell_ids:
                        print(f"    cells: {' '.join(cell_ids)}")
                continue
            if lower == "/cells" or lower.startswith("/cells "):
                parts = line.split()
                limit: Optional[int] = 20
                if len(parts) == 2:
                    maybe_limit = parts[1].strip().lower()
                    if maybe_limit == "all":
                        limit = None
                    else:
                        try:
                            limit = max(1, int(maybe_limit))
                        except ValueError:
                            print("usage: /cells [n|all]")
                            continue
                elif len(parts) > 2:
                    print("usage: /cells [n|all]")
                    continue
                print_cell_catalog(
                    cell_store,
                    cell_order,
                    limit=limit,
                    current_cell_id=current_cell_id,
                )
                continue
            if lower == "/show" or lower.startswith("/show "):
                args, parse_error = split_repl_command_args(line)
                if parse_error:
                    print(f"command parse error: {parse_error}")
                    continue
                assert args is not None
                if len(args) == 1:
                    target_cell_id, target_error = resolve_cell_reference(
                        None,
                        current_cell_id=current_cell_id,
                        action_ref_index=active_action_ref_index,
                    )
                elif len(args) == 2:
                    target_cell_id, target_error = resolve_cell_reference(
                        args[1],
                        current_cell_id=current_cell_id,
                        action_ref_index=active_action_ref_index,
                    )
                else:
                    print("usage: /show [cell_id|@ref]")
                    continue
                if target_error:
                    print(target_error)
                    continue
                if not target_cell_id:
                    print("cells: no current cell (use /cells or /show <cell_id>)")
                    continue
                cell = cell_store.get(target_cell_id)
                if not isinstance(cell, dict):
                    print(f"cells: unknown id '{target_cell_id}'")
                    continue
                current_cell_id = str(cell.get("id") or "") or current_cell_id
                print_cell_content(cell)
                continue
            if lower == "/run" or lower.startswith("/run "):
                args, parse_error = split_repl_command_args(line)
                if parse_error:
                    print(f"command parse error: {parse_error}")
                    continue
                assert args is not None
                target_cell_id, timeout_s, run_error = resolve_run_request(
                    args,
                    current_cell_id=current_cell_id,
                    action_ref_index=active_action_ref_index,
                )
                if run_error:
                    print(run_error)
                    continue
                assert timeout_s is not None
                assert target_cell_id is not None
                cell = cell_store.get(target_cell_id)
                if not isinstance(cell, dict):
                    print(f"cells: unknown id '{target_cell_id}'")
                    continue
                current_cell_id = str(cell.get("id") or "") or current_cell_id
                print("source>")
                print_cell_content(cell)
                language = normalize_code_language(str(cell.get("language") or ""))
                if run_backend_mode == "pyreplab" and language == "python":
                    result = execute_cell_with_pyreplab(
                        cell=cell,
                        pyreplab_cmd=resolved_pyreplab_cmd or [],
                        workdir=Path.cwd(),
                        session_dir=pyreplab_session_dir,
                        timeout_s=timeout_s,
                    )
                elif run_backend_mode == "pyreplab" and language != "python":
                    print(f"backend: pyreplab supports python only; using local for {language or 'unknown'}")
                    result = execute_cell_locally(cell, workdir=Path.cwd(), timeout_s=timeout_s)
                else:
                    result = execute_cell_locally(cell, workdir=Path.cwd(), timeout_s=timeout_s)
                if not result.get("ok"):
                    print(f"run error: {result.get('error')}")
                    stderr_text = str(result.get("stderr") or "").strip()
                    stdout_text = str(result.get("stdout") or "").strip()
                    if stdout_text:
                        print("stdout>")
                        print(stdout_text)
                    if stderr_text:
                        print("stderr>")
                        print(stderr_text)
                    continue
                command = " ".join(result.get("command") or [])
                exit_code = int(result.get("exit_code", 0))
                backend_used = str(result.get("backend") or run_backend_mode)
                print(f"run> {cell.get('id')} backend={backend_used} exit={exit_code} cmd={command}")
                stdout_text = str(result.get("stdout") or "").strip()
                stderr_text = str(result.get("stderr") or "").strip()
                print("stdout>")
                print(stdout_text if stdout_text else "(empty)")
                print("stderr>")
                print(stderr_text if stderr_text else "(empty)")
                continue
            if lower == "/focus" or lower.startswith("/focus "):
                args, parse_error = split_repl_command_args(line)
                if parse_error:
                    print(f"command parse error: {parse_error}")
                    continue
                assert args is not None
                if len(args) != 2:
                    print("usage: /focus <cell_id|@ref>")
                    continue
                target_cell_id, target_error = resolve_cell_reference(
                    args[1],
                    current_cell_id=current_cell_id,
                    action_ref_index=active_action_ref_index,
                )
                if target_error:
                    print(target_error)
                    continue
                assert target_cell_id is not None
                cell = cell_store.get(target_cell_id)
                if not isinstance(cell, dict):
                    print(f"cells: unknown id '{target_cell_id}'")
                    continue
                current_cell_id = str(cell.get("id") or "") or current_cell_id
                print(f"focus: {current_cell_id}")
                continue
            if lower.startswith("/fork "):
                args, parse_error = split_repl_command_args(line)
                if parse_error:
                    print(f"command parse error: {parse_error}")
                    continue
                assert args is not None
                if len(args) not in {2, 3}:
                    print("usage: /fork <source_cell_id|@ref> [new_cell_id]")
                    continue
                source_id, source_error = resolve_cell_reference(
                    args[1],
                    current_cell_id=current_cell_id,
                    action_ref_index=active_action_ref_index,
                )
                if source_error:
                    print(source_error)
                    continue
                source = cell_store.get(source_id)
                if not isinstance(source, dict):
                    print(f"cells: unknown id '{source_id}'")
                    continue
                new_id = args[2] if len(args) == 3 else next_cell_id(cell_counters, str(source.get("language") or ""))
                if new_id in cell_store:
                    print(f"cells: id already exists '{new_id}'")
                    continue
                forked = dict(source)
                forked["id"] = new_id
                forked["parent_id"] = str(source.get("id") or "")
                forked["revision"] = 1
                cell_store[new_id] = forked
                cell_order.append(new_id)
                current_cell_id = new_id
                print(f"cells> forked {source.get('id')} -> {new_id}")
                continue
            if lower.startswith("/save "):
                args, parse_error = split_repl_command_args(line)
                if parse_error:
                    print(f"command parse error: {parse_error}")
                    continue
                assert args is not None
                if len(args) != 3:
                    print("usage: /save <cell_id|@ref> <path>")
                    continue
                target_cell_id, target_error = resolve_cell_reference(
                    args[1],
                    current_cell_id=current_cell_id,
                    action_ref_index=active_action_ref_index,
                )
                if target_error:
                    print(target_error)
                    continue
                cell = cell_store.get(target_cell_id)
                if not isinstance(cell, dict):
                    print(f"cells: unknown id '{target_cell_id}'")
                    continue
                target = save_cell_to_path(cell, args[2])
                print(f"saved {target_cell_id} -> {target}")
                continue
            if lower.startswith("/diff "):
                args, parse_error = split_repl_command_args(line)
                if parse_error:
                    print(f"command parse error: {parse_error}")
                    continue
                assert args is not None
                if len(args) != 3:
                    print("usage: /diff <cell_a|@ref> <cell_b|@ref>")
                    continue
                left_id, left_error = resolve_cell_reference(
                    args[1],
                    current_cell_id=current_cell_id,
                    action_ref_index=active_action_ref_index,
                )
                if left_error:
                    print(left_error)
                    continue
                right_id, right_error = resolve_cell_reference(
                    args[2],
                    current_cell_id=current_cell_id,
                    action_ref_index=active_action_ref_index,
                )
                if right_error:
                    print(right_error)
                    continue
                left = cell_store.get(left_id)
                right = cell_store.get(right_id)
                if not isinstance(left, dict):
                    print(f"cells: unknown id '{left_id}'")
                    continue
                if not isinstance(right, dict):
                    print(f"cells: unknown id '{right_id}'")
                    continue
                diff_text = diff_cell_contents(left, right)
                if diff_text:
                    print(diff_text)
                else:
                    print("diff: no differences")
                continue
            if lower.startswith("/edit "):
                args, parse_error = split_repl_command_args(line)
                if parse_error:
                    print(f"command parse error: {parse_error}")
                    continue
                assert args is not None
                if len(args) != 2:
                    print("usage: /edit <cell_id|@ref>")
                    continue
                target_cell_id, target_error = resolve_cell_reference(
                    args[1],
                    current_cell_id=current_cell_id,
                    action_ref_index=active_action_ref_index,
                )
                if target_error:
                    print(target_error)
                    continue
                cell = cell_store.get(target_cell_id)
                if not isinstance(cell, dict):
                    print(f"cells: unknown id '{target_cell_id}'")
                    continue
                current_cell_id = str(cell.get("id") or "") or current_cell_id
                ok, message = edit_cell_in_editor(cell, cell_workspace)
                if ok:
                    print(f"cells> {message}")
                else:
                    print(f"edit error: {message}")
                continue
            result = dispatch_prompt(
                client=client,
                agent_id=agent_id,
                prompt=line,
                js_tools=js_tools,
                click_tools=click_tools,
                type_tools=type_tools,
                ddm_tools=ddm_tools,
                submit=submit_enabled,
                output_format=output_mode,
                layout_text=current_layout_text,
                echo_result=False,
                wait_for_response=submit_enabled,
                wait_timeout_s=wait_timeout_s,
                poll_interval_s=poll_interval_s,
                show_dispatch_details=debug,
            )
            record_interactive_turn(result, line)
    finally:
        if resolved_pyreplab_cmd and pyreplab_session_dir:
            stop_pyreplab_session(
                pyreplab_cmd=resolved_pyreplab_cmd,
                workdir=Path.cwd(),
                session_dir=pyreplab_session_dir,
                timeout_s=6,
            )
        flush_repl_history_entries(history_entries)


def cdp_fallback_submit(
    client: MCPClient,
    agent_id: str,
    prompt: str,
    js_tools: Sequence[str],
    click_tools: Sequence[str],
    type_tools: Sequence[str],
    ddm_tools: Sequence[str],
    layout_text: str,
    submit: bool,
    baseline_probe: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    fresh_layout_text = layout_text
    if ddm_tools:
        ddm_args = with_agent_variants(
            [{"flags": "--llm-2pass --cols 60"}, {"flags": "--text --max 1500"}],
            agent_id=agent_id,
        )
        try:
            _, ddm_result = call_tool_variants(
                client, ddm_tools, ddm_args, "ddm refresh for cdp fallback"
            )
            maybe_layout = extract_text(ddm_result).strip()
            if maybe_layout:
                fresh_layout_text = maybe_layout
        except MCPError:
            pass

    points = parse_layout_points(fresh_layout_text)
    input_point = choose_input_point(points)
    send_point = choose_send_point(points)

    typed_ok = False
    type_tool = "unknown"

    # First try deterministic JS fill to avoid sending stale drafts.
    if js_tools:
        _, _, _, fill_status = call_js_expression(
            client=client,
            js_tools=js_tools,
            agent_id=agent_id,
            expression=build_prompt_expression(prompt, submit=False),
            label="fallback js fill",
        )
        if fill_status is None or bool(fill_status.get("ok")):
            input_after_fill = read_visible_input_text(
                client=client,
                agent_id=agent_id,
                js_tools=js_tools,
            )
            expected_norm = normalize_for_match(prompt)
            actual_norm = normalize_for_match(input_after_fill)
            if expected_norm and actual_norm and (
                expected_norm[:120] in actual_norm or actual_norm[:120] in expected_norm
            ):
                typed_ok = True
                type_tool = "js_fill"

    # If JS fill is unavailable/failed, try coordinate click + cdp_type.
    if not typed_ok:
        if input_point:
            click_args = with_agent_variants(
                [{"x": input_point[0], "y": input_point[1]}],
                agent_id=agent_id,
            )
            call_tool_variants(client, click_tools, click_args, "cdp click input")

        type_args = with_agent_variants([{"text": prompt}], agent_id=agent_id)
        type_tool, type_result = call_tool_variants(client, type_tools, type_args, "cdp type prompt")
        typed_text = extract_text(type_result).lower()
        typed_ok = "no input focused" not in typed_text
        if typed_ok:
            input_after_type = read_visible_input_text(
                client=client,
                agent_id=agent_id,
                js_tools=js_tools,
            )
            expected_norm = normalize_for_match(prompt)
            actual_norm = normalize_for_match(input_after_type)
            if expected_norm and actual_norm:
                typed_ok = (
                    expected_norm[:120] in actual_norm or actual_norm[:120] in expected_norm
                )
            elif expected_norm and (not actual_norm):
                typed_ok = False
        time.sleep(0.35)

    if not typed_ok:
        raise MCPError(
            "Fallback typing did not focus input; aborted submit to avoid sending stale prompt text."
        )

    submit_mode = "none"
    if submit:
        send_button_state = wait_for_visible_send_button_state(
            client=client,
            agent_id=agent_id,
            js_tools=js_tools,
        )
        dom_send_point: Optional[Tuple[int, int]] = None
        try:
            if bool(send_button_state.get("visible")):
                dom_send_point = (
                    int(send_button_state.get("x") or 0),
                    int(send_button_state.get("y") or 0),
                )
                if dom_send_point[0] <= 0 or dom_send_point[1] <= 0:
                    dom_send_point = None
        except (TypeError, ValueError):
            dom_send_point = None
        if dom_send_point:
            send_point = dom_send_point

        def submit_via_newline(newline_label: str) -> Tuple[str, str]:
            current_input_text = read_visible_input_text(
                client=client,
                agent_id=agent_id,
                js_tools=js_tools,
            ).strip()
            should_refocus = not current_input_text
            if should_refocus and input_point and click_tools:
                try:
                    focus_args = with_agent_variants(
                        [{"x": input_point[0], "y": input_point[1]}],
                        agent_id=agent_id,
                    )
                    call_tool_variants(client, click_tools, focus_args, "cdp refocus input")
                    time.sleep(0.15)
                except MCPError:
                    pass
            newline_args_local = with_agent_variants([{"text": "\n"}], agent_id=agent_id)
            enter_tool_local, _ = call_tool_variants(
                client, type_tools, newline_args_local, newline_label
            )
            return type_tool, enter_tool_local

        if send_point and click_tools:
            try:
                click_args = with_agent_variants(
                    [{"x": send_point[0], "y": send_point[1]}],
                    agent_id=agent_id,
                )
                click_tool, _ = call_tool_variants(client, click_tools, click_args, "cdp click submit")
                submit_mode = f"{type_tool}+{click_tool}"
                time.sleep(0.35)

                input_text_after_click = read_visible_input_text(
                    client=client,
                    agent_id=agent_id,
                    js_tools=js_tools,
                ).strip()
                submit_confirmed = False
                if baseline_probe:
                    probe_after_click = read_assistant_probe(
                        client=client,
                        agent_id=agent_id,
                        js_tools=js_tools,
                        label="fallback post-click probe",
                    )
                    submit_confirmed = probe_indicates_submit(
                        baseline_probe=baseline_probe,
                        probe=probe_after_click,
                        expected_prompt=prompt,
                    )
                if (not submit_confirmed) and input_text_after_click:
                    _, enter_tool = submit_via_newline("cdp submit newline fallback")
                    submit_mode = f"{submit_mode}+{enter_tool}"
            except MCPError:
                _, enter_tool = submit_via_newline("cdp submit newline fallback")
                submit_mode = f"{type_tool}+{enter_tool}"
        else:
            _, enter_tool = submit_via_newline("cdp submit newline")
            submit_mode = f"{type_tool}+{enter_tool}"

    return {
        "ok": True,
        "submitted": bool(submit),
        "mode": submit_mode if submit else type_tool,
        "fallback": "cdp_actions",
    }


def dispatch_prompt(
    client: MCPClient,
    agent_id: str,
    prompt: str,
    js_tools: Sequence[str],
    click_tools: Sequence[str],
    type_tools: Sequence[str],
    ddm_tools: Sequence[str],
    submit: bool,
    layout_text: str,
    output_format: str = DEFAULT_OUTPUT_FORMAT,
    echo_result: bool = True,
    wait_for_response: bool = True,
    wait_timeout_s: int = DEFAULT_WAIT_TIMEOUT,
    poll_interval_s: float = DEFAULT_POLL_INTERVAL,
    show_dispatch_details: bool = False,
) -> Dict[str, Any]:
    turn_result: Dict[str, Any] = {
        "prompt": prompt,
        "submitted": bool(submit),
        "assistant_text": "",
        "output_format": (output_format or DEFAULT_OUTPUT_FORMAT).lower().strip(),
        "render_complete": False,
        "timed_out": False,
        "fallback_used": False,
    }
    response_text: Optional[str] = None
    final_snapshot: Optional[Dict[str, Any]] = None
    render_complete = False
    timed_out = False
    baseline_probe = None
    fallback_used = False
    native_submit_default = bool(submit and click_tools)
    foreground_mode = browser_foreground_mode() if submit else "off"
    submit_focus_mode = "hold" if (submit and foreground_mode in {"submit", "poll"}) else "off"
    poll_focus_mode = "pulse" if (submit and foreground_mode == "poll") else "off"
    final_focus_mode = "pulse" if (submit and foreground_mode == "poll") else "off"
    with foreground_browser_context(submit_focus_mode):
        if wait_for_response and submit:
            baseline_probe = read_assistant_probe(
                client=client,
                agent_id=agent_id,
                js_tools=js_tools,
                label="assistant baseline probe",
            )
        if submit and show_dispatch_details:
            install_page_network_request_spy(
                client=client,
                agent_id=agent_id,
                js_tools=js_tools,
            )

        if native_submit_default:
            status = cdp_fallback_submit(
                client=client,
                agent_id=agent_id,
                prompt=prompt,
                js_tools=js_tools,
                click_tools=click_tools,
                type_tools=type_tools,
                ddm_tools=ddm_tools,
                layout_text=layout_text,
                submit=submit,
                baseline_probe=baseline_probe,
            )
            fallback_used = True
            turn_result["fallback_used"] = True
            if show_dispatch_details:
                print(f"[native-submit] {json.dumps(status)}")
        else:
            expression = build_prompt_expression(prompt, submit=submit)
            tool, _, result_text, status = call_js_expression(
                client=client,
                js_tools=js_tools,
                agent_id=agent_id,
                expression=expression,
                label="js prompt dispatch",
            )
            if status and status.get("ok") is False:
                if type_tools:
                    fallback_status = cdp_fallback_submit(
                        client=client,
                        agent_id=agent_id,
                        prompt=prompt,
                        js_tools=js_tools,
                        click_tools=click_tools,
                        type_tools=type_tools,
                        ddm_tools=ddm_tools,
                        layout_text=layout_text,
                        submit=submit,
                        baseline_probe=baseline_probe,
                    )
                    if show_dispatch_details:
                        print(f"[{tool}] {result_text}")
                        print(f"[fallback] {json.dumps(fallback_status)}")
                    status = fallback_status
                    fallback_used = True
                    turn_result["fallback_used"] = True
                else:
                    raise MCPError(f"Prompt dispatch failed: {status}")
            elif show_dispatch_details:
                print(f"[{tool}] {result_text}")

        if submit and type_tools and (not native_submit_default):
            post_dispatch_probe = read_assistant_probe(
                client=client,
                agent_id=agent_id,
                js_tools=js_tools,
                label="post-submit probe",
            )
            if not probe_has_new_user_turn(baseline_probe, post_dispatch_probe, prompt):
                retry_status = cdp_fallback_submit(
                    client=client,
                    agent_id=agent_id,
                    prompt=prompt,
                    js_tools=js_tools,
                    click_tools=click_tools,
                    type_tools=type_tools,
                    ddm_tools=ddm_tools,
                    layout_text=layout_text,
                    submit=submit,
                    baseline_probe=post_dispatch_probe,
                )
                fallback_used = True
                turn_result["fallback_used"] = True
                if show_dispatch_details:
                    print(f"[retry-fallback] {json.dumps(retry_status)}")

    if wait_for_response and submit:
        baseline_count = int((baseline_probe or {}).get("assistant_count") or 0)
        baseline_hash = str((baseline_probe or {}).get("latest_hash") or "")
        capture_baseline_hash = baseline_hash
        baseline_user_count = int((baseline_probe or {}).get("user_count") or 0)
        baseline_user_hash = str((baseline_probe or {}).get("latest_user_hash") or "")
        with foreground_browser_context(poll_focus_mode):
            response_text, user_submitted, render_complete, timed_out = wait_for_assistant_response(
                client=client,
                agent_id=agent_id,
                js_tools=js_tools,
                baseline_count=baseline_count,
                baseline_hash=baseline_hash,
                baseline_user_count=baseline_user_count,
                baseline_user_hash=baseline_user_hash,
                expected_prompt=prompt,
                timeout_s=wait_timeout_s,
                poll_interval_s=poll_interval_s,
                debug=show_dispatch_details,
            )
        if (not user_submitted) and type_tools and (not native_submit_default):
            with foreground_browser_context(submit_focus_mode):
                pre_retry_probe = read_assistant_probe(
                    client=client,
                    agent_id=agent_id,
                    js_tools=js_tools,
                    label="submit verification probe",
                )
                if probe_indicates_submit(
                    baseline_probe=baseline_probe,
                    probe=pre_retry_probe,
                    expected_prompt=prompt,
                ):
                    user_submitted = True
                    maybe_latest = str((pre_retry_probe or {}).get("latest_text") or "").strip()
                    if maybe_latest:
                        response_text = maybe_latest
                else:
                    retry_status = cdp_fallback_submit(
                        client=client,
                        agent_id=agent_id,
                        prompt=prompt,
                        js_tools=js_tools,
                        click_tools=click_tools,
                        type_tools=type_tools,
                        ddm_tools=ddm_tools,
                        layout_text=layout_text,
                        submit=submit,
                        baseline_probe=pre_retry_probe,
                    )
                    fallback_used = True
                    turn_result["fallback_used"] = True
                    if show_dispatch_details:
                        print(f"[final-retry] {json.dumps(retry_status)}")

                retry_baseline = read_assistant_probe(
                    client=client,
                    agent_id=agent_id,
                    js_tools=js_tools,
                    label="final-retry baseline",
                )
            if not user_submitted and "retry_baseline" in locals():
                with foreground_browser_context(poll_focus_mode):
                    response_text, user_submitted, render_complete, timed_out = wait_for_assistant_response(
                        client=client,
                        agent_id=agent_id,
                        js_tools=js_tools,
                        baseline_count=int((retry_baseline or {}).get("assistant_count") or 0),
                        baseline_hash=str((retry_baseline or {}).get("latest_hash") or ""),
                        baseline_user_count=int((retry_baseline or {}).get("user_count") or 0),
                        baseline_user_hash=str((retry_baseline or {}).get("latest_user_hash") or ""),
                        expected_prompt=prompt,
                        timeout_s=min(20, max(8, wait_timeout_s)),
                        poll_interval_s=poll_interval_s,
                        debug=show_dispatch_details,
                    )
                capture_baseline_hash = str((retry_baseline or {}).get("latest_hash") or capture_baseline_hash)
        if not user_submitted:
            raise MCPError(
                "Prompt submit was not confirmed (no new user turn detected). "
                "Retry once or run with --debug for details."
            )
        with foreground_browser_context(final_focus_mode):
            final_text, final_snapshot = capture_final_assistant_text(
                client=client,
                agent_id=agent_id,
                js_tools=js_tools,
                fallback_text=response_text,
                baseline_hash=capture_baseline_hash,
            )
        if final_text:
            response_text = final_text
        turn_result["assistant_text"] = str(response_text or "")
        turn_result["render_complete"] = bool(render_complete)
        turn_result["timed_out"] = bool(timed_out)
        turn_result["artifacts"] = build_response_artifacts(str(response_text or ""))

    if response_text:
        output_mode = (output_format or DEFAULT_OUTPUT_FORMAT).lower().strip()
        rendered_output = format_assistant_output(
            text=response_text,
            output_format=output_mode,
            snapshot=final_snapshot,
        )
        if output_mode == "json":
            print(rendered_output)
        else:
            print("assistant>")
            print(colorize_markdown_text_for_terminal(rendered_output))
        turn_result["rendered_output"] = rendered_output
    elif wait_for_response and submit:
        if show_dispatch_details:
            network_rows = read_page_network_request_spy_log(
                client=client,
                agent_id=agent_id,
                js_tools=js_tools,
            )
            if network_rows:
                print(f"[network-log] {json.dumps(network_rows)}")
        diagnosis_probe = read_assistant_probe(
            client=client,
            agent_id=agent_id,
            js_tools=js_tools,
            label="missing response diagnosis probe",
        )
        print(
            summarize_missing_assistant_response(
                diagnosis_probe,
                timed_out=bool(timed_out),
                fallback_used=bool(fallback_used),
                render_complete=bool(render_complete),
            )
        )
    return turn_result


def maybe_show_ddm(
    client: MCPClient,
    agent_id: str,
    ddm_tools: Sequence[str],
) -> None:
    ddm_args = with_agent_variants(
        [{"flags": "--llm-2pass --cols 60"}, {"flags": "--text --max 1500"}],
        agent_id=agent_id,
    )
    tool, result = call_tool_variants(client, ddm_tools, ddm_args, "ddm")
    print(f"ddm tool: {tool}")
    print(extract_text(result).strip())


def main() -> int:
    parser = argparse.ArgumentParser(
        prog=os.getenv("SKY_CLI_NAME") or Path(sys.argv[0] or "sky").name,
        description="Simple terminal prompt CLI for websites via unchained or Sky MCP.",
    )
    parser.add_argument("prompt_args", nargs="*", help="Prompt text (one-shot mode).")
    parser.add_argument(
        "-p",
        "--prompt",
        "-prompt",
        help="Explicit prompt text. If omitted, positional args or stdin are used.",
    )
    parser.add_argument(
        "--url",
        default=getenv_first(PRIMARY_TARGET_URL_ENV) or DEFAULT_URL,
        help="Target URL (default: https://chatgpt.com).",
    )
    parser.add_argument(
        "--setup-alias",
        help="Install a custom command alias (example: --setup-alias sk).",
    )
    parser.add_argument(
        "--alias-dir",
        default=str(DEFAULT_ALIAS_DIR),
        help="Directory used by --setup-alias and the default sky launcher during --setup (default: ~/.local/bin).",
    )
    parser.add_argument(
        "--force-alias",
        action="store_true",
        help="Overwrite existing alias path when using --setup or --setup-alias.",
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Install unchainedsky-cli and pyreplab, add a sky launcher, and launch ChatGPT in Chrome.",
    )
    parser.add_argument(
        "--transport",
        choices=SUPPORTED_TRANSPORTS,
        default=(os.getenv("SKY_TRANSPORT") or DEFAULT_TRANSPORT),
        help="Browser transport to use (default: unchained).",
    )
    parser.add_argument(
        "--unchained-cmd",
        help="Command used for the local unchained transport (example: 'unchained' or 'uvx unchainedsky-cli').",
    )
    parser.add_argument(
        "--unchained-port",
        type=int,
        default=DEFAULT_UNCHAINED_PORT,
        help="Chrome remote debugging port for the unchained transport (default: 9222).",
    )
    parser.add_argument(
        "--browser-tab",
        default=DEFAULT_BROWSER_TAB,
        help="Target browser tab id or alias for the unchained transport (default: auto).",
    )
    parser.add_argument(
        "--chrome-profile",
        default=DEFAULT_CHROME_PROFILE,
        help="Chrome profile used by --setup and automatic local browser launch (default: Default).",
    )
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="Sky MCP endpoint (transport=sky-mcp).")
    parser.add_argument("--api-key", help="Sky API key (transport=sky-mcp).")
    parser.add_argument("--agent-id", help="Connected agent_id (transport=sky-mcp).")
    parser.add_argument(
        "--no-submit",
        action="store_true",
        help="Only fill the prompt into the input field; do not submit.",
    )
    parser.add_argument(
        "-i",
        "--interactive",
        "-chat",
        action="store_true",
        help="Interactive shell mode.",
    )
    parser.add_argument(
        "--pyreplab",
        action="store_true",
        help="Shortcut for --run-backend pyreplab (auto-launch in interactive mode).",
    )
    parser.add_argument("--show-ddm", action="store_true", help="Print a ddm read after prompt dispatch.")
    parser.add_argument("--navigate-tool", help="Override navigate tool name.")
    parser.add_argument("--js-tool", help="Override JS tool name.")
    parser.add_argument("--ddm-tool", help="Override ddm tool name.")
    parser.add_argument(
        "--output-format",
        choices=SUPPORTED_OUTPUT_FORMATS,
        default=DEFAULT_OUTPUT_FORMAT,
        help="Assistant response output format (default: markdown).",
    )
    parser.add_argument(
        "--run-backend",
        choices=SUPPORTED_RUN_BACKENDS,
        default=DEFAULT_RUN_BACKEND,
        help="Code runner backend used by /run in interactive mode (default: pyreplab; falls back to local if unavailable).",
    )
    parser.add_argument(
        "--pyreplab-cmd",
        help="Optional pyreplab command or path (example: '/path/to/pyreplab').",
    )
    parser.add_argument("--self-test", action="store_true", help="Run local parser/formatter regression tests.")
    parser.add_argument("--timeout", type=int, default=45, help="HTTP timeout in seconds.")
    parser.add_argument(
        "--wait-timeout",
        type=int,
        default=DEFAULT_WAIT_TIMEOUT,
        help="Seconds to wait for assistant response after submit.",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL,
        help="Seconds between response polling checks.",
    )
    parser.add_argument("--debug", action="store_true", help="Print raw request/response diagnostics.")
    args = parser.parse_args()

    if args.self_test:
        return run_self_tests()

    if args.pyreplab:
        args.run_backend = "pyreplab"

    if args.setup_alias:
        target_script = Path(__file__).resolve()
        alias_dir = Path(args.alias_dir).expanduser()
        alias_path, created = install_alias_launcher(
            alias_name=args.setup_alias,
            alias_dir=alias_dir,
            target_script=target_script,
            force=args.force_alias,
        )
        if created:
            print(f"installed alias: {alias_path} -> python3 {target_script}")
        else:
            print(f"alias already installed: {alias_path} -> python3 {target_script}")
        if not path_contains_dir(alias_dir):
            print(f"PATH missing {alias_dir}")
            print(f'add this to your shell rc: export PATH="{alias_dir}:$PATH"')
        print(f'try it: {args.setup_alias} -p "hello"')
        return 0

    if args.setup:
        uv_cmd = resolve_uv_command()
        (
            resolved_unchained_cmd,
            resolved_pyreplab_cmd,
            installed_now,
            setup_warnings,
        ) = ensure_local_setup_tooling(
            uv_cmd,
            unchained_cmd=args.unchained_cmd,
            pyreplab_cmd=args.pyreplab_cmd,
            timeout_s=DEFAULT_LOCAL_SETUP_TIMEOUT,
        )
        alias_dir = Path(args.alias_dir).expanduser()
        target_script = Path(__file__).resolve()
        alias_path: Optional[Path] = None
        alias_created = False
        alias_error: Optional[str] = None
        try:
            alias_path, alias_created = install_alias_launcher(
                alias_name="sky",
                alias_dir=alias_dir,
                target_script=target_script,
                force=args.force_alias,
            )
        except MCPError as exc:
            alias_error = str(exc)
        print("setup: using " + " ".join(resolved_unchained_cmd))
        if resolved_pyreplab_cmd:
            print("setup: using " + " ".join(resolved_pyreplab_cmd))
        for warning in setup_warnings:
            print(warning)
        if alias_path is not None:
            status_label = "installed" if alias_created else "already installed"
            print(f"setup: {status_label} sky launcher at {alias_path}")
        elif alias_error:
            print(f"setup: skipped sky launcher: {alias_error}")
        alias_on_path = bool(alias_path) and path_contains_dir(alias_dir)
        if alias_path is not None and not alias_on_path:
            print(f"setup: PATH missing {alias_dir}")
            print(f'setup: add this to your shell rc: export PATH="{alias_dir}:$PATH"')
        print(
            f"setup: launching ChatGPT on port {args.unchained_port} with profile {args.chrome_profile}"
        )
        launch_output = launch_chatgpt_with_unchained(
            resolved_unchained_cmd,
            port=args.unchained_port,
            profile=args.chrome_profile,
            url=args.url,
            timeout_s=60,
        )
        if launch_output:
            print(launch_output)
        print("setup complete")
        if installed_now:
            print("next: finish logging into ChatGPT in the opened browser if needed")
        if alias_on_path:
            print('then run: sky -p "hello"')
        else:
            print('then run: ./sky -p "hello"')
            if alias_path is not None:
                print('after PATH update, you can use: sky -p "hello"')
        return 0

    cli_prompt = " ".join(args.prompt_args).strip()
    explicit_prompt = (args.prompt or "").strip()
    stdin_prompt = read_prompt_from_stdin()
    merged_prompt = explicit_prompt or cli_prompt or stdin_prompt

    transport = str(args.transport or DEFAULT_TRANSPORT).strip().lower()
    resolved_agent_id = ""
    credential_source = transport
    if transport == "sky-mcp":
        resolved_api_key, resolved_agent_id, credential_source = resolve_credentials(
            api_key_arg=args.api_key,
            agent_id_arg=args.agent_id,
            endpoint=args.endpoint,
            timeout=args.timeout,
        )
        setup_source: Optional[str] = None
        if not resolved_api_key or not resolved_agent_id:
            resolved_api_key, resolved_agent_id, setup_source = maybe_run_first_call_setup(
                api_key=resolved_api_key,
                agent_id=resolved_agent_id,
                endpoint=args.endpoint,
                timeout=args.timeout,
            )
            if setup_source:
                credential_source = setup_source
        if not resolved_api_key:
            if setup_source in {"setup-instructions", "setup-cancelled"}:
                return 2
            parser.error(
                "Missing API key. Pass --api-key, export SKY_API_KEY, "
                "or set SKY_API_KEY in ~/.sky-agent/.env."
            )
        if not resolved_agent_id:
            if setup_source in {"setup-instructions", "setup-cancelled"}:
                return 2
            parser.error(
                "Missing agent_id. Pass --agent-id, export SKY_AGENT_ID, set SKY_AGENT_ID "
                "in ~/.sky-agent/.env, or ensure "
                "https://api.unchainedsky.com/api/agents returns a connected agent."
            )
        maybe_migrate_primary_env(
            api_key=resolved_api_key,
            agent_id=resolved_agent_id,
        )
        if setup_source == "setup-wizard":
            print(f"Waiting for agent {resolved_agent_id} to connect...")
            connected, saw_agent, connect_error = wait_for_agent_connection(
                api_key=resolved_api_key,
                agent_id=resolved_agent_id,
                endpoint=args.endpoint,
                timeout=args.timeout,
            )
            if not connected:
                if connect_error is not None:
                    print(f"Agent connection check failed: {connect_error}")
                elif saw_agent:
                    print(f"Agent {resolved_agent_id} is not connected yet.")
                else:
                    print(f"Agent {resolved_agent_id} has not appeared yet.")
                print("Rerun sky in a few seconds if the agent just started.")
                return 2

        client: Any = MCPClient(
            endpoint=args.endpoint,
            api_key=resolved_api_key,
            timeout=args.timeout,
            debug=args.debug,
        )
    else:
        resolved_unchained_cmd = resolve_unchained_command(args.unchained_cmd)
        client = LocalCLIClient(
            command=resolved_unchained_cmd or [],
            port=args.unchained_port,
            tab=args.browser_tab,
            chrome_profile=args.chrome_profile,
            startup_url=args.url,
            auto_launch=True,
            timeout=args.timeout,
            debug=args.debug,
        )

    client.initialize()
    if transport == "unchained" and getattr(client, "did_auto_launch", False):
        print(
            f"browser: launched {args.url} on port {args.unchained_port} with profile {args.chrome_profile}"
        )
        launch_output = str(getattr(client, "last_launch_output", "") or "").strip()
        if launch_output:
            print(launch_output)
    available_tools = client.list_tools()
    if args.debug and available_tools:
        print(f"[debug] tools={available_tools}", file=sys.stderr)
        if transport == "sky-mcp":
            print(
                f"[debug] transport=sky-mcp using agent_id={resolved_agent_id} credential_source={credential_source}",
                file=sys.stderr,
            )
        else:
            resolved_cmd = " ".join(getattr(client, "command", []) or [])
            print(
                f"[debug] transport=unchained cmd={resolved_cmd} port={args.unchained_port} tab={args.browser_tab}",
                file=sys.stderr,
            )

    navigate_tools = select_tool_candidates(
        preferred=PREFERRED_NAVIGATE_TOOLS,
        available=available_tools,
        explicit=args.navigate_tool,
        keyword="navigate",
    )
    js_tools = select_tool_candidates(
        preferred=PREFERRED_JS_TOOLS,
        available=available_tools,
        explicit=args.js_tool,
        keyword="js",
    )
    click_tools = select_tool_candidates(
        preferred=PREFERRED_CLICK_TOOLS,
        available=available_tools,
        explicit=None,
        keyword="click",
    )
    type_tools = select_tool_candidates(
        preferred=PREFERRED_TYPE_TOOLS,
        available=available_tools,
        explicit=None,
        keyword="type",
    )
    if available_tools:
        click_tools = [name for name in click_tools if name in available_tools]
        type_tools = [name for name in type_tools if name in available_tools]
    ddm_tools = select_tool_candidates(
        preferred=PREFERRED_DDM_TOOLS,
        available=available_tools,
        explicit=args.ddm_tool,
        keyword="ddm",
    )

    interactive_mode = args.interactive or not merged_prompt
    if interactive_mode:
        run_repl(
            client=client,
            agent_id=resolved_agent_id,
            url=args.url,
            navigate_tools=navigate_tools,
            js_tools=js_tools,
            click_tools=click_tools,
            type_tools=type_tools,
            ddm_tools=ddm_tools,
            submit=not args.no_submit,
            output_format=args.output_format,
            run_backend=args.run_backend,
            pyreplab_cmd=args.pyreplab_cmd,
            first_prompt=merged_prompt,
            wait_timeout_s=args.wait_timeout,
            poll_interval_s=args.poll_interval,
            debug=args.debug,
        )
    else:
        run_single_prompt(
            client=client,
            agent_id=resolved_agent_id,
            url=args.url,
            prompt=merged_prompt,
            navigate_tools=navigate_tools,
            js_tools=js_tools,
            click_tools=click_tools,
            type_tools=type_tools,
            ddm_tools=ddm_tools,
            submit=not args.no_submit,
            output_format=args.output_format,
            wait_timeout_s=args.wait_timeout,
            poll_interval_s=args.poll_interval,
            debug=args.debug,
        )

    if args.show_ddm:
        maybe_show_ddm(client, resolved_agent_id, ddm_tools)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MCPError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
