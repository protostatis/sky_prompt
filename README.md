# SkyPrompt

Minimal terminal CLI to send prompts into a real browser tab (for sites like `https://chatgpt.com`) through Unchained MCP.

## What It Does

- Connects to `https://api.unchainedsky.com/mcp`
- Performs MCP initialize + session handshake
- Navigates your connected browser to a URL
- Injects prompt text into visible chat input and optionally submits
- Supports one-shot mode and interactive shell mode
- Falls back to native MCP actions (`cdp_type` + `cdp_click`) if JS submit is not confirmed
- Shows a terminal thinking indicator and waits for render completion before final response capture

## Install

```bash
git clone https://github.com/protostatis/sky_prompt.git
cd sky_prompt
./unchained --help
```

Optional `pyreplab` backend dependency with `uv`:

```bash
uv sync --extra pyreplab
```

Optional alias setup (recommended):

```bash
./unchained --setup-alias sky
sky --help
```

## Quickstart

1. Start Unchained agent:

```bash
curl -fsSL https://api.unchainedsky.com/install.sh | bash
cd ~/unchained-agent
./start.sh --daemon
```

2. Export credentials:

```bash
export UNCHAINED_API_KEY="uc_live_..."
export UNCHAINED_AGENT_ID="claude-xxxxxxxx"
```

3. Run one-shot prompt:

```bash
sky -p "Explain MCP in one paragraph"
```

4. Run interactive mode:

```bash
sky -i
```

## Demo

One-shot on ChatGPT:

```bash
sky --url https://chatgpt.com -p "Write a 5-line Python script for a Poisson PMF"
```

Interactive demo with format switching:

```bash
sky -i
# then inside shell:
/format markdown
show me a numpy ascii chart for poisson(lambda=4)
/format plain
now summarize in 3 bullets
```

## Prerequisites

1. Install and run the Unchained agent on your Mac:

```bash
curl -fsSL https://api.unchainedsky.com/install.sh | bash
cd ~/unchained-agent
./start.sh --daemon
```

2. Export credentials (optional if `~/unchained-agent/.env` already has them):

```bash
export UNCHAINED_API_KEY="uc_live_..."
export UNCHAINED_AGENT_ID="claude-xxxxxxxx"
```

To find your agent id:

```bash
curl -sS https://api.unchainedsky.com/api/agents \
  -H "Authorization: Bearer $UNCHAINED_API_KEY"
```

## Quick Usage (Claude-Like)

One-shot prompt:

```bash
./unchained "Summarize MCP in one paragraph"
```

Interactive shell mode (default when no prompt is passed):

```bash
./unchained
```

Inside `-i`, Up/Down arrows recall your previous prompts (saved in `~/.sky_prompt_history`).

Explicit flags:

```bash
./unchained -prompt "Hello from prompt mode"
./unchained -chat
```

Read prompt from stdin:

```bash
echo "Write a haiku about browser automation" | \
./unchained
```

Fill only, do not submit:

```bash
./unchained --no-submit "Draft text only"
```

Tune response waiting:

```bash
./unchained --wait-timeout 240 --poll-interval 1.0 "Long response request"
```

Use another website:

```bash
./unchained --url https://chatgpt.com "What is MCP?"
```

Choose output formatting:

```bash
./unchained --output-format markdown "Explain MCP"
./unchained --output-format plain "Explain MCP"
./unchained --output-format json "Explain MCP"
```

`json` mode includes structured artifacts for automation:
- `artifacts.code_blocks` detected scripts
- `artifacts.command_blocks` runnable shell command groups
- `artifacts.output_blocks` detected output/result sections
- `artifacts.copy_items` copy-ready chunks
- `artifacts.tool_hints` suggested runner commands

Run built-in closed-loop self-tests:

```bash
./unchained --self-test
```

Developer test loop (recommended while iterating):

```bash
./scripts/test_loop.sh
./scripts/test_loop.sh --watch
```

## Custom Alias Setup

Install any command name you want (for example `sky`):

```bash
./unchained --setup-alias sky
```

Optional flags:

```bash
./unchained --setup-alias sky --alias-dir ~/.local/bin
./unchained --setup-alias sky --force-alias
```

Then run:

```bash
sky -p "something short"
```

## Interactive Commands

Inside interactive mode:

- `/help` show commands
- `/url <url>` navigate to another site
- `/submit on|off` toggle auto-submit
- `/format markdown|plain|json` change response rendering format
- `/backend [local|pyreplab]` choose `/run` execution backend (default: pyreplab)
- `/py <code>` passthrough inline Python directly into pyreplab session
- `/pyfile <path.py>` passthrough a local setup script into pyreplab session
- `/history [n]` show recent turns (default last 10)
- `/last` reprint the latest turn using current format mode
- `/cells [n|all]` list detected runnable code cells (`*` marks the current cell)
- `/show [cell_id]` print cell content (defaults to the current cell)
- `/run [cell_id] [timeout_seconds]` execute a cell with the active backend (defaults to the current cell and prints the source first)
- `/fork <source_cell_id> [new_cell_id]` clone a cell for mutation
- `/edit <cell_id>` open cell in `$EDITOR`/`$VISUAL`
- `/save <cell_id> <path>` save a cell to disk
- `/diff <cell_a> <cell_b>` show unified diff between cells
- `/ddm` run ddm read
- `/exit` quit

Press `Ctrl-C` during `/run` to cancel the active execution and stay inside `-i`.

Playground loop example:

```bash
sky -i
# ask for code, then:
/cells
/py import pandas as pd
/pyfile ./setup_lab.py
/run
/fork py1 py2
/edit py2
/diff py1 py2
/run py2
```

`pyreplab` is now the default `/run` backend. If it is unavailable, the CLI automatically falls back to `local`.
`/py`, `/pyfile`, and `/run` share the same pyreplab session, so pre-imports persist for later cell runs.
Each `-i` session uses its own isolated pyreplab session directory to avoid cross-project leakage.

Use an explicit `pyreplab` command path:

```bash
./unchained -i --run-backend pyreplab --pyreplab-cmd /Users/zhiminzou/Projects/pyrepl/pyreplab
# or set PYREPLAB_CMD and use /backend pyreplab inside -i
```

Fast launch shortcut:

```bash
./unchained -i --pyreplab
```

## Add To PATH

If you want it globally like `claude`:

```bash
ln -sf /Users/zhiminzou/Projects/test_unchainedsky_mcp/unchained ~/.local/bin/unchained
```

Then run:

```bash
unchained "Explain what an MCP session id is"
```

## Notes

- You must already be logged into the target site in your local Chrome session.
- Different sites use different input DOM patterns; this script targets common chat UIs and may need selector tweaks for edge cases.
- Use `--debug` to print tool and transport diagnostics.
- Interactive mode clears stale draft text on startup.
- Tool-path tags like `[js_eval]` / `[fallback]` are hidden by default and shown only with `--debug`.
