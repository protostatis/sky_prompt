#!/usr/bin/env python3
"""Simple terminal prompt CLI powered by Unchained MCP."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import difflib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

DEFAULT_ENDPOINT = "https://api.unchainedsky.com/mcp"
DEFAULT_URL = "https://chatgpt.com"
DEFAULT_AGENT_ENV_PATH = Path.home() / "unchained-agent" / ".env"
DEFAULT_REPL_HISTORY_PATH = Path.home() / ".sky_prompt_history"
DEFAULT_REPL_HISTORY_LIMIT = 1000
_FOREGROUND_BROWSER_CONTEXT_STACK: List[str] = []
DEFAULT_WAIT_TIMEOUT = 180
DEFAULT_POLL_INTERVAL = 1.0
DEFAULT_RENDER_STABLE_POLLS = 3
DEFAULT_RENDER_SETTLE_SECONDS = 1.2
DEFAULT_COMPOSER_SETTLE_SECONDS = 0.8
DEFAULT_ALIAS_DIR = Path.home() / ".local" / "bin"
DEFAULT_OUTPUT_FORMAT = "markdown"
DEFAULT_RUN_BACKEND = "pyreplab"
SUPPORTED_RUN_BACKENDS = ("local", "pyreplab")
SUPPORTED_OUTPUT_FORMATS = ("markdown", "plain", "json")
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
                "clientInfo": {"name": "unchained-prompt-cli", "version": "0.1.0"},
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
    snippet = str(content or "").strip()
    if not snippet:
        return False
    try:
        compile(snippet, "<inferred-python>", "exec")
        return True
    except SyntaxError:
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
    if stripped.startswith(("[", "]", "(", ")", "{", "}")) and "," in stripped:
        return True
    if stripped.endswith(("]", ")", "}", ",")) and "," in stripped:
        return True
    if stripped in {"pass", "break", "continue"}:
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
            if looks_like_python_code_line(current) or (collected and looks_like_python_continuation_line(current)):
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
                if looks_like_python_code_line(current) or (collected and looks_like_python_continuation_line(current)):
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
                        collected and looks_like_python_continuation_line(current)
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
    command_blocks: List[Dict[str, Any]] = []
    labeled_code_blocks: List[Dict[str, Any]] = []
    inferred_code_blocks: List[Dict[str, Any]] = []
    output_blocks: List[Dict[str, Any]] = []
    command_index = 1
    labeled_code_index = 1
    inferred_index = 1
    output_index = 1

    for segment in segments:
        if segment.get("type") != "text":
            continue
        segment_text = str(segment.get("text") or "")

        extracted_labeled_code, extracted_outputs = extract_labeled_code_and_output_blocks_from_text(
            segment_text,
            labeled_code_index,
            output_index,
        )
        labeled_code_blocks.extend(extracted_labeled_code)
        output_blocks.extend(extracted_outputs)
        labeled_code_index += len(extracted_labeled_code)
        output_index += len(extracted_outputs)

        extracted_commands = extract_command_blocks_from_text(segment_text, command_index)
        command_blocks.extend(extracted_commands)
        command_index += len(extracted_commands)

        extracted_python = extract_python_blocks_from_text(segment_text, inferred_index)
        inferred_code_blocks.extend(extracted_python)
        inferred_index += len(extracted_python)

    code_blocks = dedupe_code_blocks(fenced_code_blocks + labeled_code_blocks + inferred_code_blocks)
    command_blocks = dedupe_command_blocks(command_blocks)
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
            module_path = Path.cwd() / "_sky_prompt_local_import_probe.py"
            module_path.write_text("VALUE = 7\n", encoding="utf-8")
            try:
                cell = {
                    "id": "py3",
                    "language": "python",
                    "content": (
                        "import _sky_prompt_local_import_probe\n"
                        "print(_sky_prompt_local_import_probe.VALUE)"
                    ),
                }
                result = execute_cell_locally(cell, workdir=Path.cwd(), timeout_s=10)
            finally:
                module_path.unlink(missing_ok=True)

            self.assertTrue(bool(result.get("ok")), msg=json.dumps(result))
            self.assertEqual(int(result.get("exit_code", -1)), 0, msg=json.dumps(result))
            self.assertIn("7", str(result.get("stdout") or ""))

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

        def test_resolve_run_request_rejects_missing_current_cell(self) -> None:
            cell_id, timeout_s, error = resolve_run_request(["/run"], current_cell_id=None)
            self.assertIsNone(cell_id)
            self.assertIsNone(timeout_s)
            self.assertIn("no current cell", str(error or ""))

        def test_browser_foreground_mode_defaults_to_submit(self) -> None:
            self.assertEqual(browser_foreground_mode({}), "submit")
            self.assertEqual(browser_foreground_mode({"SKY_FOREGROUND_BROWSER": "0"}), "off")
            self.assertEqual(browser_foreground_mode({"SKY_FOREGROUND_BROWSER": "1"}), "poll")

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
    print(
        f"{cell.get('id')} [{cell.get('language')}] "
        f"rev={cell.get('revision')} turn={cell.get('turn')}"
    )
    print(str(cell.get("content") or ""))


def resolve_run_request(
    args: Sequence[str],
    current_cell_id: Optional[str],
    default_timeout_s: int = 30,
) -> Tuple[Optional[str], Optional[int], Optional[str]]:
    usage = "usage: /run [cell_id] [timeout_seconds]"
    if len(args) == 0 or len(args) > 3:
        return None, None, usage

    target = str(current_cell_id or "").strip()
    timeout_s = int(default_timeout_s)

    if len(args) == 2:
        token = str(args[1] or "").strip()
        if token and target and re.fullmatch(r"\d+", token):
            return target, max(1, int(token)), None
        if token:
            target = token
    elif len(args) == 3:
        target = str(args[1] or "").strip()
        raw_timeout = str(args[2] or "").strip()
        try:
            timeout_s = max(1, int(raw_timeout))
        except ValueError:
            return None, None, usage

    if target in {"current", ".", "last"}:
        target = str(current_cell_id or "").strip()

    if not target:
        return None, None, "cells: no current cell (use /cells or /show <cell_id>)"

    return target, timeout_s, None


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
    input_text: Optional[str] = None

    if language == "python":
        runner_cmd = ["python3", "-"]
        input_text = content
    elif language in {"bash", "javascript", "js", "typescript", "ts", "ruby", "perl"}:
        suffix = language_to_cell_extension(language)
        with tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False, encoding="utf-8") as handle:
            handle.write(content)
            script_path = Path(handle.name)
        if language == "bash":
            runner_cmd = ["bash", str(script_path)]
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
        Path.home() / "Projects" / "pyrepl" / "pyreplab",
        Path.home() / "pyrepl" / "pyreplab",
        Path.cwd() / "pyreplab",
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


def browser_foreground_mode(env: Optional[Mapping[str, str]] = None) -> str:
    values = env or os.environ
    raw = str(values.get("SKY_FOREGROUND_BROWSER", "submit") or "").strip().lower()
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
    if resolved_mode == "off":
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
        f'exec python3 "{target}" "$@"\n'
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

    api_key = api_key_arg or os.getenv("UNCHAINED_API_KEY") or env_file_values.get("UNCHAINED_API_KEY")
    agent_id = agent_id_arg or os.getenv("UNCHAINED_AGENT_ID") or env_file_values.get("UNCHAINED_AGENT_ID")

    source = "flags/env"
    if not api_key:
        return None, None, source

    if not agent_id:
        agent_id = fetch_agent_id(api_key=api_key, endpoint=endpoint, timeout=timeout)
        if agent_id:
            source = "auto-discovered-from-api"
        elif env_file_values.get("UNCHAINED_AGENT_ID"):
            source = "local-env-file"
    elif agent_id == env_file_values.get("UNCHAINED_AGENT_ID"):
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
    turn_index = 0
    cell_store: Dict[str, Dict[str, Any]] = {}
    cell_order: List[str] = []
    cell_counters: Dict[str, int] = {}
    current_cell_id: Optional[str] = None
    cell_workspace = Path.cwd() / ".sky_cells"
    readline_mod, readline_history_path = setup_repl_readline_history()
    current_layout_text = navigate_current_page(
        client, agent_id, current_url, navigate_tools, verbose=debug
    )
    print("interactive mode: /help for commands, /exit to quit, Ctrl-C cancels /run")

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
            echo_result=True,
            wait_for_response=submit_enabled,
            wait_timeout_s=wait_timeout_s,
            poll_interval_s=poll_interval_s,
            show_dispatch_details=debug,
        )
        turn_index += 1
        created_cells = register_turn_cells(
            turn_result=initial_result,
            turn_index=turn_index,
            cell_store=cell_store,
            cell_order=cell_order,
            cell_counters=cell_counters,
        )
        if created_cells:
            current_cell_id = str(created_cells[-1].get("id") or "") or current_cell_id
            summary = " ".join(
                f"{cell['id']}[{cell.get('language')}]"
                for cell in created_cells
            )
            current_preview = summarize_cell_preview(str(created_cells[-1].get("content") or ""), limit=120)
            if current_preview:
                print(f"cells> +{summary} current={current_cell_id} :: {current_preview}")
            else:
                print(f"cells> +{summary} current={current_cell_id}")
        history.append(
            {
                "prompt": first_prompt,
                "assistant_text": str(initial_result.get("assistant_text") or ""),
                "cell_ids": list(initial_result.get("cell_ids") or []),
            }
        )

    try:
        while True:
            try:
                line = input("> ").strip()
            except EOFError:
                print()
                break
            except KeyboardInterrupt:
                print()
                print("cancelled")
                continue
            if not line:
                continue
            add_repl_history_entry(readline_mod, line)
            lower = line.lower()
            if lower in {"quit", "exit", ":q", "/exit", "/quit"}:
                break
            if lower in {"/help", "/h"}:
                print(
                    "/url <url> | /submit on|off | /format markdown|plain|json | "
                    "/backend [local|pyreplab] | "
                    "/py <code> | /pyfile <path.py> | "
                    "/history [n] | /last | /cells [n|all] | /show [cell] | /run [cell] [timeout] | "
                    "/fork <src> [dst] | /edit <cell> | /save <cell> <path> | /diff <a> <b> | /ddm | /exit"
                )
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
                    print("assistant> (no response captured)")
                    continue
                rendered = format_assistant_output(
                    text=assistant_text,
                    output_format=output_mode,
                    snapshot=None,
                )
                if output_mode == "json":
                    print(rendered)
                else:
                    print("assistant>")
                    print(rendered)
                cell_ids = list(entry.get("cell_ids") or [])
                if cell_ids:
                    print("cells>")
                    print(" ".join(cell_ids))
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
                    target_cell_id = current_cell_id
                elif len(args) == 2:
                    target_cell_id = args[1]
                else:
                    print("usage: /show [cell_id]")
                    continue
                if not target_cell_id:
                    print("cells: no current cell (use /cells or /show <cell_id>)")
                    continue
                if target_cell_id in {"current", ".", "last"}:
                    target_cell_id = current_cell_id
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
                target_cell_id, timeout_s, run_error = resolve_run_request(args, current_cell_id=current_cell_id)
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
            if lower.startswith("/fork "):
                args, parse_error = split_repl_command_args(line)
                if parse_error:
                    print(f"command parse error: {parse_error}")
                    continue
                assert args is not None
                if len(args) not in {2, 3}:
                    print("usage: /fork <source_cell_id> [new_cell_id]")
                    continue
                source = cell_store.get(args[1])
                if not isinstance(source, dict):
                    print(f"cells: unknown id '{args[1]}'")
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
                    print("usage: /save <cell_id> <path>")
                    continue
                cell = cell_store.get(args[1])
                if not isinstance(cell, dict):
                    print(f"cells: unknown id '{args[1]}'")
                    continue
                target = save_cell_to_path(cell, args[2])
                print(f"saved {args[1]} -> {target}")
                continue
            if lower.startswith("/diff "):
                args, parse_error = split_repl_command_args(line)
                if parse_error:
                    print(f"command parse error: {parse_error}")
                    continue
                assert args is not None
                if len(args) != 3:
                    print("usage: /diff <cell_a> <cell_b>")
                    continue
                left = cell_store.get(args[1])
                right = cell_store.get(args[2])
                if not isinstance(left, dict):
                    print(f"cells: unknown id '{args[1]}'")
                    continue
                if not isinstance(right, dict):
                    print(f"cells: unknown id '{args[2]}'")
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
                    print("usage: /edit <cell_id>")
                    continue
                cell = cell_store.get(args[1])
                if not isinstance(cell, dict):
                    print(f"cells: unknown id '{args[1]}'")
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
                echo_result=True,
                wait_for_response=submit_enabled,
                wait_timeout_s=wait_timeout_s,
                poll_interval_s=poll_interval_s,
                show_dispatch_details=debug,
            )
            turn_index += 1
            created_cells = register_turn_cells(
                turn_result=result,
                turn_index=turn_index,
                cell_store=cell_store,
                cell_order=cell_order,
                cell_counters=cell_counters,
            )
            if created_cells:
                current_cell_id = str(created_cells[-1].get("id") or "") or current_cell_id
                summary = " ".join(
                    f"{cell['id']}[{cell.get('language')}]"
                    for cell in created_cells
                )
                current_preview = summarize_cell_preview(str(created_cells[-1].get("content") or ""), limit=120)
                if current_preview:
                    print(f"cells> +{summary} current={current_cell_id} :: {current_preview}")
                else:
                    print(f"cells> +{summary} current={current_cell_id}")
            history.append(
                {
                    "prompt": line,
                    "assistant_text": str(result.get("assistant_text") or ""),
                    "cell_ids": list(result.get("cell_ids") or []),
                }
            )
    finally:
        if resolved_pyreplab_cmd and pyreplab_session_dir:
            stop_pyreplab_session(
                pyreplab_cmd=resolved_pyreplab_cmd,
                workdir=Path.cwd(),
                session_dir=pyreplab_session_dir,
                timeout_s=6,
            )
        flush_repl_history(readline_mod, readline_history_path)


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
            if echo_result and show_dispatch_details:
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
                    if echo_result and show_dispatch_details:
                        print(f"[{tool}] {result_text}")
                        print(f"[fallback] {json.dumps(fallback_status)}")
                    status = fallback_status
                    fallback_used = True
                    turn_result["fallback_used"] = True
                else:
                    raise MCPError(f"Prompt dispatch failed: {status}")
            elif echo_result and show_dispatch_details:
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
                if echo_result and show_dispatch_details:
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
                    if echo_result and show_dispatch_details:
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
            print(rendered_output)
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
        description="Simple claude-like terminal prompt CLI for websites via Unchained MCP.",
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
        default=os.getenv("UNCHAINED_TARGET_URL", DEFAULT_URL),
        help="Target URL (default: https://chatgpt.com).",
    )
    parser.add_argument(
        "--setup-alias",
        help="Install a custom command alias (example: --setup-alias sky).",
    )
    parser.add_argument(
        "--alias-dir",
        default=str(DEFAULT_ALIAS_DIR),
        help="Directory used by --setup-alias (default: ~/.local/bin).",
    )
    parser.add_argument(
        "--force-alias",
        action="store_true",
        help="Overwrite existing alias path when using --setup-alias.",
    )
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="MCP server endpoint")
    parser.add_argument("--api-key", help="Unchained API key")
    parser.add_argument("--agent-id", help="Connected agent_id")
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
        help="Code runner backend used by /run in interactive mode (default: pyreplab).",
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

    resolved_api_key, resolved_agent_id, credential_source = resolve_credentials(
        api_key_arg=args.api_key,
        agent_id_arg=args.agent_id,
        endpoint=args.endpoint,
        timeout=args.timeout,
    )
    if not resolved_api_key:
        parser.error(
            "Missing API key. Pass --api-key, export UNCHAINED_API_KEY, "
            "or set UNCHAINED_API_KEY in ~/.unchained-agent/.env."
        )
    if not resolved_agent_id:
        parser.error(
            "Missing agent_id. Pass --agent-id, export UNCHAINED_AGENT_ID, "
            "set UNCHAINED_AGENT_ID in ~/.unchained-agent/.env, or ensure "
            "https://api.unchainedsky.com/api/agents returns a connected agent."
        )

    cli_prompt = " ".join(args.prompt_args).strip()
    explicit_prompt = (args.prompt or "").strip()
    stdin_prompt = read_prompt_from_stdin()
    merged_prompt = explicit_prompt or cli_prompt or stdin_prompt

    client = MCPClient(
        endpoint=args.endpoint,
        api_key=resolved_api_key,
        timeout=args.timeout,
        debug=args.debug,
    )

    client.initialize()
    available_tools = client.list_tools()
    if args.debug and available_tools:
        print(f"[debug] tools={available_tools}", file=sys.stderr)
        print(
            f"[debug] using agent_id={resolved_agent_id} credential_source={credential_source}",
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
