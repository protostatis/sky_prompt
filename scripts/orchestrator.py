#!/usr/bin/env python3
"""Tab-isolated Sky Prompt orchestrator.

This keeps ChatGPT prompting on one browser tab while mcporter tool execution
runs on a separate tab. The goal is to stop browser tools from disrupting the
ChatGPT UI that `sky -p` depends on.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional
from urllib.parse import urlparse


DEFAULT_CHAT_URL = "https://chatgpt.com"
DEFAULT_TOOL_URL = "about:blank"
DEFAULT_CHROME_PROFILE = "Default"
DEFAULT_UNCHAINED_PORT = 9222
DEFAULT_CHAT_TAB_ALIAS = "sky-chat"
DEFAULT_TOOL_TAB_ALIAS = "sky-tools"


@dataclass
class TabSetup:
    chat_alias: str
    chat_tab_id: str
    tool_alias: str
    tool_tab_id: str


class CommandError(RuntimeError):
    pass


def render_command(command: List[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def detail_from_process(proc: subprocess.CompletedProcess[str]) -> str:
    stdout_text = str(proc.stdout or "").strip()
    stderr_text = str(proc.stderr or "").strip()
    return stderr_text or stdout_text or f"exit code {proc.returncode}"


def extract_tab_id(payload: Dict[str, object]) -> str:
    for key in ("id", "tabId", "targetId"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    raise CommandError(f"Could not extract a tab id from payload: {payload}")


def is_chat_url(url: str, chat_url: str) -> bool:
    current = str(url or "").strip().lower()
    expected = str(chat_url or "").strip().lower()
    if not current or not expected:
        return False
    current_parts = urlparse(current)
    expected_parts = urlparse(expected)
    if current_parts.netloc and expected_parts.netloc:
        return current_parts.netloc == expected_parts.netloc
    return current.startswith(expected)


class UnchainedTabManager:
    def __init__(
        self,
        command: List[str],
        port: int = DEFAULT_UNCHAINED_PORT,
        chrome_profile: str = DEFAULT_CHROME_PROFILE,
        launch_mode: str = "profile",
        verbose: bool = False,
    ):
        self.command = list(command)
        self.port = int(port)
        self.chrome_profile = str(chrome_profile or DEFAULT_CHROME_PROFILE)
        self.launch_mode = str(launch_mode or "profile").strip().lower()
        self.verbose = bool(verbose)

    def _run(
        self,
        args: List[str],
        *,
        json_output: bool = False,
        timeout: int = 30,
    ) -> subprocess.CompletedProcess[str]:
        command = list(self.command)
        command.extend(["--port", str(self.port)])
        if json_output:
            command.append("--json")
        command.extend(args)
        if self.verbose:
            print(f"[unchained] {render_command(command)}", file=sys.stderr)
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise CommandError(
                f"Unchained CLI not found. Tried: {render_command(command[:1])}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise CommandError(
                f"Unchained command timed out after {timeout}s: {render_command(command)}"
            ) from exc
        if int(proc.returncode) != 0:
            raise CommandError(detail_from_process(proc))
        return proc

    def _run_json(self, args: List[str], *, timeout: int = 30) -> object:
        proc = self._run(args, json_output=True, timeout=timeout)
        try:
            return json.loads(str(proc.stdout or "").strip() or "null")
        except json.JSONDecodeError as exc:
            raise CommandError(
                f"Unchained returned invalid JSON for {render_command(args)}"
            ) from exc

    def ensure_browser(self) -> None:
        args = ["launch"]
        if self.launch_mode == "guest":
            args.append("--chrome-arg=--guest")
        elif self.launch_mode == "incognito":
            args.append("--chrome-arg=--incognito")
        else:
            args.extend(["--use-profile", "--profile", self.chrome_profile])
        args.append(DEFAULT_TOOL_URL)
        self._run(args, timeout=60)

    def list_tabs(self) -> List[Dict[str, object]]:
        payload = self._run_json(["tabs"])
        if not isinstance(payload, list):
            raise CommandError(f"Unexpected tabs payload: {payload}")
        return [item for item in payload if isinstance(item, dict)]

    def list_aliases(self) -> Dict[str, str]:
        proc = self._run(["alias", "list"], json_output=True)
        payload_text = str(proc.stdout or "").strip()
        if not payload_text or payload_text == "No aliases set.":
            return {}
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            raise CommandError("Unchained returned invalid JSON for alias list") from exc
        if not isinstance(payload, dict):
            raise CommandError(f"Unexpected alias payload: {payload}")
        return {
            str(name): str(tab_id)
            for name, tab_id in payload.items()
            if str(name).strip() and str(tab_id).strip()
        }

    def create_tab(self, url: str) -> Dict[str, object]:
        payload = self._run_json(["create_tab", url])
        if not isinstance(payload, dict):
            raise CommandError(f"Unexpected create_tab payload: {payload}")
        return payload

    def set_alias(self, name: str, tab_id: str) -> None:
        self._run(["alias", "set", name, tab_id])

    def _tab_by_id(self, tab_id: str, tabs: List[Dict[str, object]]) -> Optional[Dict[str, object]]:
        for tab in tabs:
            if str(tab.get("id") or "").strip() == tab_id:
                return tab
        return None

    def _reuse_chat_tab(
        self,
        alias: str,
        chat_url: str,
        tabs: List[Dict[str, object]],
        aliases: Dict[str, str],
    ) -> Optional[str]:
        tab_id = str(aliases.get(alias) or "").strip()
        if not tab_id:
            return None
        tab = self._tab_by_id(tab_id, tabs)
        if not tab:
            return None
        if is_chat_url(str(tab.get("url") or ""), chat_url):
            return tab_id
        return None

    def _reuse_tool_tab(
        self,
        alias: str,
        chat_tab_id: str,
        chat_url: str,
        tabs: List[Dict[str, object]],
        aliases: Dict[str, str],
    ) -> Optional[str]:
        tab_id = str(aliases.get(alias) or "").strip()
        if not tab_id or tab_id == chat_tab_id:
            return None
        tab = self._tab_by_id(tab_id, tabs)
        if not tab:
            return None
        if is_chat_url(str(tab.get("url") or ""), chat_url):
            return None
        return tab_id

    def ensure_isolated_tabs(
        self,
        *,
        chat_alias: str = DEFAULT_CHAT_TAB_ALIAS,
        tool_alias: str = DEFAULT_TOOL_TAB_ALIAS,
        chat_url: str = DEFAULT_CHAT_URL,
        tool_url: str = DEFAULT_TOOL_URL,
    ) -> TabSetup:
        self.ensure_browser()

        tabs = self.list_tabs()
        aliases = self.list_aliases()

        chat_tab_id = self._reuse_chat_tab(chat_alias, chat_url, tabs, aliases)
        if not chat_tab_id:
            chat_info = self.create_tab(chat_url)
            chat_tab_id = extract_tab_id(chat_info)
            self.set_alias(chat_alias, chat_tab_id)

        tabs = self.list_tabs()
        aliases = self.list_aliases()

        tool_tab_id = self._reuse_tool_tab(tool_alias, chat_tab_id, chat_url, tabs, aliases)
        if not tool_tab_id:
            tool_info = self.create_tab(tool_url)
            tool_tab_id = extract_tab_id(tool_info)
            self.set_alias(tool_alias, tool_tab_id)

        if tool_tab_id == chat_tab_id:
            tool_info = self.create_tab(tool_url)
            tool_tab_id = extract_tab_id(tool_info)
            self.set_alias(tool_alias, tool_tab_id)

        return TabSetup(
            chat_alias=chat_alias,
            chat_tab_id=chat_tab_id,
            tool_alias=tool_alias,
            tool_tab_id=tool_tab_id,
        )


class ProtocolDecoder:
    """Decode navigation protocol from agent responses."""

    PROTOCOL = {"NAVIGATE", "TYPE", "CLICK", "ENTER", "READ", "SCROLL"}

    def decode(self, response: str) -> Optional[Dict[str, str]]:
        for line in str(response or "").splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(" ", 1)
            tool = parts[0].upper()
            args = parts[1] if len(parts) > 1 else ""
            if tool in self.PROTOCOL:
                return {"tool": tool, "args": args}
        return None


class ToolExecutor:
    """Execute decoded tool calls via mcporter on a dedicated tool tab."""

    def __init__(self, mcporter_command: List[str], tool_tab_alias: str, verbose: bool = False):
        self.command = list(mcporter_command)
        self.tool_tab_alias = str(tool_tab_alias or DEFAULT_TOOL_TAB_ALIAS)
        self.verbose = bool(verbose)

    def _build_cmd(self, tool_call: Dict[str, str]) -> List[str]:
        tool = str(tool_call.get("tool") or "").upper()
        args = str(tool_call.get("args") or "").strip()
        base = list(self.command) + ["call"]
        tab_arg = f"tab_id:{self.tool_tab_alias}"

        if tool == "NAVIGATE":
            url = args
            if not url.startswith(("http://", "https://")):
                url = f"https://{url}"
            return base + ["unchainedsky.cdp_navigate", f"url:{url}", tab_arg]
        if tool == "TYPE":
            return base + ["unchainedsky.cdp_type", f"text:{args}", tab_arg]
        if tool == "CLICK":
            return base + ["unchainedsky.cdp_click", f"label:{args}", tab_arg]
        if tool == "ENTER":
            return base + ["unchainedsky.cdp_press_enter", tab_arg]
        if tool == "READ":
            return base + ["unchainedsky.ddm", "flags:--text --max 4000", tab_arg]
        if tool == "SCROLL":
            direction = args if args in {"up", "down"} else "down"
            return base + ["unchainedsky.cdp_scroll", f"direction:{direction}", "amount:500", tab_arg]
        raise ValueError(f"Unknown tool: {tool}")

    def execute(self, tool_call: Dict[str, str]) -> str:
        command = self._build_cmd(tool_call)
        if self.verbose:
            print(f"[tool] {render_command(command)}", file=sys.stderr)
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return f"Timeout: {tool_call.get('tool', '?')}"
        except FileNotFoundError as exc:
            return f"Error: {exc}"
        return str(proc.stdout or proc.stderr or "").strip()


class ReactiveAgent:
    """Prompt ChatGPT on one tab and execute browser actions on another."""

    def __init__(
        self,
        sky_command: List[str],
        executor: ToolExecutor,
        chat_tab_alias: str,
        unchained_port: int,
        verbose: bool = False,
    ):
        self.sky_command = list(sky_command)
        self.executor = executor
        self.chat_tab_alias = str(chat_tab_alias or DEFAULT_CHAT_TAB_ALIAS)
        self.unchained_port = int(unchained_port)
        self.decoder = ProtocolDecoder()
        self.history: List[Dict[str, str]] = []
        self.verbose = bool(verbose)

    def _build_prompt_cmd(self, question: str) -> List[str]:
        return list(self.sky_command) + [
            "--browser-tab",
            self.chat_tab_alias,
            "--unchained-port",
            str(self.unchained_port),
            "--output-format",
            "plain",
            "-p",
            question,
        ]

    def _prompt(self, question: str) -> str:
        command = self._build_prompt_cmd(question)
        if self.verbose:
            print(f"[prompt] {render_command(command)}", file=sys.stderr)
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return "ERROR: Agent timeout"
        except FileNotFoundError as exc:
            return f"ERROR: {exc}"
        return str(proc.stdout or proc.stderr or "").strip()

    def run(self, task: str, max_steps: int = 10) -> str:
        print(f"TASK: {task}")

        prompt = (
            f"Task: {task}\n"
            "Available tools: NAVIGATE <url>, TYPE <text>, CLICK <label>, ENTER, READ, SCROLL up/down\n"
            "What's your first action?"
        )

        for step in range(1, max_steps + 1):
            print(f"\nSTEP {step}: PROMPT")
            response = self._prompt(prompt)
            if response:
                print(response[:400])

            tool_call = self.decoder.decode(response)
            if not tool_call:
                redirect_prompt = (
                    "Please answer with a single tool call.\n"
                    "Tools: NAVIGATE <url>, TYPE <text>, CLICK <label>, ENTER, READ, SCROLL up/down\n"
                    f"Task: {task}"
                )
                response = self._prompt(redirect_prompt)
                tool_call = self.decoder.decode(response)
                if not tool_call:
                    print("No valid tool call returned; stopping.")
                    break

            print(f"STEP {step}: EXECUTE {tool_call['tool']} {tool_call['args']}".rstrip())
            result = self.executor.execute(tool_call)
            result_preview = result[:200]
            print(result_preview)

            self.history.append(
                {
                    "step": str(step),
                    "tool": tool_call["tool"],
                    "args": tool_call["args"],
                    "result": result_preview,
                }
            )

            prompt = (
                f"Task: {task}\n"
                f"Last tool: {tool_call['tool']} {tool_call['args']}\n"
                f"Tool result: {result_preview}\n"
                "What's the next action? Use exactly one tool call."
            )

        return self._format_history()

    def _format_history(self) -> str:
        lines: List[str] = []
        for item in self.history:
            lines.append(f"Step {item['step']}: {item['tool']} {item['args']}".rstrip())
            if item.get("result"):
                lines.append(f"  {item['result']}")
        return "\n".join(lines)


def split_command(raw: str) -> List[str]:
    parts = shlex.split(str(raw or "").strip())
    if not parts:
        raise CommandError("Command cannot be empty.")
    return parts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a tab-isolated Sky Prompt + mcporter orchestration loop.",
    )
    parser.add_argument("task", nargs="*", help="Task for the ChatGPT agent.")
    parser.add_argument("--max-steps", type=int, default=10, help="Maximum tool iterations (default: 10).")
    parser.add_argument("--sky-cmd", default="sky", help="Sky CLI command (default: sky).")
    parser.add_argument("--unchained-cmd", default="unchained", help="Unchained CLI command (default: unchained).")
    parser.add_argument("--mcporter-cmd", default="npx mcporter", help="mcporter command prefix (default: 'npx mcporter').")
    parser.add_argument(
        "--unchained-port",
        type=int,
        default=DEFAULT_UNCHAINED_PORT,
        help=f"Chrome debugging port (default: {DEFAULT_UNCHAINED_PORT}).",
    )
    parser.add_argument(
        "--chat-tab-alias",
        default=DEFAULT_CHAT_TAB_ALIAS,
        help=f"Alias used for ChatGPT prompting (default: {DEFAULT_CHAT_TAB_ALIAS}).",
    )
    parser.add_argument(
        "--tool-tab-alias",
        default=DEFAULT_TOOL_TAB_ALIAS,
        help=f"Alias used for tool execution (default: {DEFAULT_TOOL_TAB_ALIAS}).",
    )
    parser.add_argument(
        "--chat-url",
        default=DEFAULT_CHAT_URL,
        help=f"Chat tab URL (default: {DEFAULT_CHAT_URL}).",
    )
    parser.add_argument(
        "--tool-start-url",
        default=DEFAULT_TOOL_URL,
        help=f"Initial URL for the tool tab (default: {DEFAULT_TOOL_URL}).",
    )
    parser.add_argument(
        "--chrome-profile",
        default=DEFAULT_CHROME_PROFILE,
        help=f"Chrome profile used when launching in profile mode (default: {DEFAULT_CHROME_PROFILE}).",
    )
    launch_group = parser.add_mutually_exclusive_group()
    launch_group.add_argument(
        "--incognito",
        action="store_true",
        help="Launch the orchestrator browser in incognito mode.",
    )
    launch_group.add_argument(
        "--guest",
        action="store_true",
        help="Launch the orchestrator browser in guest mode.",
    )
    parser.add_argument(
        "--skip-tab-setup",
        action="store_true",
        help="Assume the tab aliases already exist and skip unchained tab preparation.",
    )
    parser.add_argument(
        "--prepare-tabs-only",
        action="store_true",
        help="Create or refresh the tab aliases and then exit.",
    )
    parser.add_argument("--verbose", action="store_true", help="Print executed commands.")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    task = " ".join(args.task).strip()
    if not task and not args.prepare_tabs_only:
        parser.error("task cannot be empty")

    launch_mode = "profile"
    if args.incognito:
        launch_mode = "incognito"
    elif args.guest:
        launch_mode = "guest"

    sky_command = split_command(args.sky_cmd)
    unchained_command = split_command(args.unchained_cmd)
    mcporter_command = split_command(args.mcporter_cmd)

    tab_setup = TabSetup(
        chat_alias=args.chat_tab_alias,
        chat_tab_id="",
        tool_alias=args.tool_tab_alias,
        tool_tab_id="",
    )
    if not args.skip_tab_setup:
        manager = UnchainedTabManager(
            command=unchained_command,
            port=args.unchained_port,
            chrome_profile=args.chrome_profile,
            launch_mode=launch_mode,
            verbose=args.verbose,
        )
        tab_setup = manager.ensure_isolated_tabs(
            chat_alias=args.chat_tab_alias,
            tool_alias=args.tool_tab_alias,
            chat_url=args.chat_url,
            tool_url=args.tool_start_url,
        )
        print(
            f"tabs ready: {tab_setup.chat_alias}={tab_setup.chat_tab_id} "
            f"{tab_setup.tool_alias}={tab_setup.tool_tab_id}"
        )
        if args.prepare_tabs_only:
            return 0
    elif args.prepare_tabs_only:
        parser.error("--prepare-tabs-only requires tab setup to be enabled")

    executor = ToolExecutor(
        mcporter_command=mcporter_command,
        tool_tab_alias=tab_setup.tool_alias or args.tool_tab_alias,
        verbose=args.verbose,
    )
    agent = ReactiveAgent(
        sky_command=sky_command,
        executor=executor,
        chat_tab_alias=tab_setup.chat_alias or args.chat_tab_alias,
        unchained_port=args.unchained_port,
        verbose=args.verbose,
    )
    final_response = agent.run(task, max_steps=max(1, int(args.max_steps)))
    print("\nFINAL RESULT")
    print(final_response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
