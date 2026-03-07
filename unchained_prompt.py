#!/usr/bin/env python3
"""Simple terminal prompt CLI powered by Unchained MCP."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

DEFAULT_ENDPOINT = "https://api.unchainedsky.com/mcp"
DEFAULT_URL = "https://chatgpt.com"
DEFAULT_AGENT_ENV_PATH = Path.home() / "unchained-agent" / ".env"
DEFAULT_WAIT_TIMEOUT = 180
DEFAULT_POLL_INTERVAL = 1.0
DEFAULT_RENDER_STABLE_POLLS = 3
DEFAULT_RENDER_SETTLE_SECONDS = 1.2
DEFAULT_ALIAS_DIR = Path.home() / ".local" / "bin"
DEFAULT_OUTPUT_FORMAT = "markdown"
SUPPORTED_OUTPUT_FORMATS = ("markdown", "plain", "json")
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

    def initialize(self) -> None:
        payload = {
            "jsonrpc": "2.0",
            "id": "init-1",
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
        payload = {"jsonrpc": "2.0", "id": "tools-list-1", "method": "tools/list", "params": {}}
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
            "id": f"tool-call-{name}",
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
    return String(el.textContent || "");
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

    el.textContent = text;
    el.dispatchEvent(new Event("input", {{ bubbles: true }}));
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

  const submitSelectorsRaw = [
    'button[data-testid*="send" i]',
    'button[aria-label*="send" i]',
    'button[aria-label*="prompt" i]',
    'button[title*="send" i]',
    'button[type="submit"]',
    '[aria-label*="send" i]',
    '[data-testid*="send" i]'
  ];
  const submitCandidates = [];
  for (const sel of submitSelectorsRaw) {{
    const found = Array.from(document.querySelectorAll(sel));
    for (const node of found) {{
      const actionable =
        node.matches("button,[role='button']") ? node : (node.closest("button,[role='button']") || node);
      if (actionable && !submitCandidates.includes(actionable)) {{
        submitCandidates.push(actionable);
      }}
    }}
  }}

  let submitButton = null;
  for (const candidate of submitCandidates) {{
    if (visible(candidate) && !isDisabled(candidate)) {{
      submitButton = candidate;
      break;
    }}
  }}

  if (submitButton) {{
    submitButton.dispatchEvent(new MouseEvent("click", {{ bubbles: true, cancelable: true }}));
    submitButton.click();
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

    return texts;
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
  const userTexts = extractUserTexts();
  const latestAssistant = assistantTexts.length ? assistantTexts[assistantTexts.length - 1] : "";
  const latestUser = userTexts.length ? userTexts[userTexts.length - 1] : "";

  return JSON.stringify({
    ok: true,
    assistant_count: assistantTexts.length,
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
      if (LANGUAGE_LINE_SET.has(lower)) {
        const prev = i > 0 ? lines[i - 1].trim() : "";
        const next = i + 1 < lines.length ? lines[i + 1].trim() : "";
        if (looksLikeCodeLine(prev) || looksLikeCodeLine(next)) {
          continue;
        }
      }
      filtered.push(line);
    }
    let joined = filtered.join("\\n");
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
      if (pendingSpace && parts.length) {
        const prev = parts[parts.length - 1];
        if (prev && !prev.endsWith("\\n") && !prev.endsWith(" ")) {
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
        turn.querySelector('.markdown, [class*="markdown"], pre, p, li, div');
      const message = nodeToMarkdownLikeText(candidate || turn);
      if (message && message.length >= 8) pushUnique(texts, message);
    }

    // 3) copy-button anchored blocks often map to assistant responses
    const copyAnchors = Array.from(
      document.querySelectorAll('main button[aria-label*="copy" i], main [data-testid*="copy" i]')
    );
    for (const anchor of copyAnchors) {
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
    base_args: List[Dict[str, Any]] = [{"expression": expression}]
    if any(tool_name.lower() == "execute_js" for tool_name in js_tools):
        base_args.extend([{"script": expression}, {"js": expression}, {"code": expression}])
    js_args = with_agent_variants(base_args, agent_id=agent_id)
    tool, result = call_tool_variants(client, js_tools, js_args, label)
    result_text = extract_text(result).strip()
    status = parse_dispatch_status_text(result_text)
    return tool, result, result_text, status


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
        payload: Dict[str, Any] = {
            "ok": bool(normalized_text),
            "format": "json",
            "text": normalized_text,
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

    return normalized_text


def history_preview(text: str, limit: int = 96) -> str:
    collapsed = " ".join(str(text or "").split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: max(8, limit - 1)] + "…"


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
        score = -1
        if label.startswith(">"):
            score = 100
        elif "ask anything" in lower:
            score = 95
        elif "message" in lower:
            score = 85
        elif "prompt" in lower and "send" not in lower:
            score = 70
        elif "textbox" in lower:
            score = 65
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
    first_prompt: str,
    wait_timeout_s: int,
    poll_interval_s: float,
    debug: bool,
) -> None:
    current_url = url
    submit_enabled = submit
    output_mode = output_format
    history: List[Dict[str, Any]] = []
    current_layout_text = navigate_current_page(
        client, agent_id, current_url, navigate_tools, verbose=debug
    )
    print("interactive mode: /help for commands, /exit to quit")

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
        history.append(
            {
                "prompt": first_prompt,
                "assistant_text": str(initial_result.get("assistant_text") or ""),
            }
        )

    while True:
        try:
            line = input("> ").strip()
        except EOFError:
            print()
            break
        if not line:
            continue
        lower = line.lower()
        if lower in {"quit", "exit", ":q", "/exit", "/quit"}:
            break
        if lower in {"/help", "/h"}:
            print(
                "/url <url> | /submit on|off | /format markdown|plain|json | "
                "/history [n] | /last | /ddm | /exit"
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
        if lower == "/ddm":
            maybe_show_ddm(client, agent_id, ddm_tools)
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
        history.append(
            {
                "prompt": line,
                "assistant_text": str(result.get("assistant_text") or ""),
            }
        )


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
        time.sleep(0.35)

    if not typed_ok:
        raise MCPError(
            "Fallback typing did not focus input; aborted submit to avoid sending stale prompt text."
        )

    submit_mode = "none"
    if submit:
        if send_point and click_tools:
            click_args = with_agent_variants(
                [{"x": send_point[0], "y": send_point[1]}],
                agent_id=agent_id,
            )
            click_tool, _ = call_tool_variants(client, click_tools, click_args, "cdp click submit")
            submit_mode = f"{type_tool}+{click_tool}"
        else:
            newline_args = with_agent_variants([{"text": "\n"}], agent_id=agent_id)
            enter_tool, _ = call_tool_variants(
                client, type_tools, newline_args, "cdp submit newline"
            )
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
    baseline_probe = None
    fallback_used = False
    if wait_for_response and submit:
        baseline_probe = read_assistant_probe(
            client=client,
            agent_id=agent_id,
            js_tools=js_tools,
            label="assistant baseline probe",
        )

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

    if submit and type_tools:
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
        if (not user_submitted) and (not response_text) and type_tools:
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
        else:
            if timed_out and fallback_used:
                print("assistant> (timed out waiting after fallback submit)")
            elif timed_out:
                print("assistant> (timed out waiting for render completion)")
            elif not render_complete:
                print("assistant> (response capture incomplete before timeout)")
            elif fallback_used:
                print("assistant> (timed out waiting after fallback submit)")
            else:
                print("assistant> (no final response captured before timeout)")
        return turn_result

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
