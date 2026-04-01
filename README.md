# SkyPrompt

Minimal terminal CLI to send prompts into a real browser tab such as `https://chatgpt.com`.
Released under the MIT License. See [`LICENSE`](./LICENSE).

`sky` now defaults to a local `unchainedsky-cli` transport, so the normal path does not require a Sky API key or Sky agent. If you can open ChatGPT in Chrome and log in with your OpenAI account, `sky` can drive that browser session from the terminal.

## What It Does

- Drives a local Chrome session through `unchainedsky-cli`
- Navigates your connected browser to a URL
- Injects prompt text into visible chat input and optionally submits
- Supports one-shot mode and interactive shell mode
- Falls back to native browser actions if JS submit is not confirmed
- Shows a terminal thinking indicator and waits for render completion before final response capture
- Still supports the legacy Sky MCP transport with `--transport sky-mcp`

## Install

Requires Python 3.10+.

```bash
pip install sky-prompt
sky --setup
```

`sky --setup` will:

- install `unchainedsky-cli` and `pyreplab` if they are missing
- install a `sky` launcher in `~/.local/bin` when possible
- launch Chrome to `https://chatgpt.com`
- tell you the next `sky` command to run

If `pyreplab` install fails, setup still completes and interactive `/run` falls back to `local` until `pyreplab` is installed.

If you want to run from a git checkout instead of PyPI:

```bash
git clone https://github.com/protostatis/sky_prompt.git
cd sky_prompt
./sky --setup
```

Optional manual dependency installs:

```bash
pip install unchainedsky-cli pyreplab
```

Optional repo-local development install with `uv`:

```bash
uv sync
```

Custom alias setup if you want a second command name such as `sk`:

```bash
./sky --setup-alias sk
sk --help
```

## Quickstart

1. Run setup:

```bash
sky --setup
```

2. If ChatGPT is not already logged in for that browser session, finish the login in the opened browser tab.

3. Run a one-shot prompt:

```bash
sky -p "Explain MCP in one paragraph"
```

4. Run interactive mode:

```bash
sky -i
```

## Local Browser Flow

The default transport is:

```bash
sky --transport unchained
```

The simplest path is:

```bash
sky --setup
```

Manual flow if you want more control:

1. `unchained` is installed and available in `PATH`, or you pass `--unchained-cmd`.
2. `sky` will auto-launch Chrome on `--unchained-port` (default `9222`) if nothing is already listening there.
3. In the default profile mode, the selected Chrome profile is logged into the target site.

Useful flags:

```bash
sky --setup --chrome-profile "Profile 3"
sky --setup --incognito
sky --setup --guest
sky --setup --unchained-port 9333
sky --unchained-port 9333 "hello"
sky --browser-tab auto "hello"
sky --unchained-cmd "uvx unchainedsky-cli" "hello"
```

`--incognito` and `--guest` auto-launch a fresh Chrome session instead of reusing a named profile. Those modes usually require logging into ChatGPT again in that window.

If `~/.local/bin` is not on `PATH`, `sky --setup` will still work but your shell may not see the installed `sky` launcher until you add that directory to `PATH`.

## Legacy Sky MCP Flow

If you still want to use the hosted Sky MCP path:

```bash
sky --transport sky-mcp -p "Explain MCP in one paragraph"
```

Credentials are read from `~/sky-agent/.env` or the environment:

```bash
export SKY_API_KEY="uc_live_..."
export SKY_AGENT_ID="claude-xxxxxxxx"
```

`sky` still supports the first-run setup/import flow for that legacy transport.

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

## Quick Usage (Claude-Like)

One-shot prompt:

```bash
sky "Summarize MCP in one paragraph"
```

Interactive shell mode (default when no prompt is passed):

```bash
sky
```

Inside `-i`, Up/Down arrows recall your previous prompts (saved in `~/.sky_prompt_history`).

Explicit flags:

```bash
sky -prompt "Hello from prompt mode"
sky -chat
```

Read prompt from stdin:

```bash
echo "Write a haiku about browser automation" | \
sky
```

Fill only, do not submit:

```bash
sky --no-submit "Draft text only"
```

Tune response waiting:

```bash
sky --wait-timeout 240 --poll-interval 1.0 "Long response request"
```

Use another website:

```bash
sky --url https://chatgpt.com "What is MCP?"
```

Choose output formatting:

```bash
sky --output-format markdown "Explain MCP"
sky --output-format plain "Explain MCP"
sky --output-format json "Explain MCP"
```

`json` mode includes structured artifacts for automation:
- `artifacts.code_blocks` detected scripts
- `artifacts.command_blocks` runnable shell command groups
- `artifacts.output_blocks` detected output/result sections
- `artifacts.copy_items` copy-ready chunks
- `artifacts.tool_hints` suggested runner commands

Run built-in closed-loop self-tests:

```bash
./sky --self-test
```

Developer test loop (recommended while iterating):

```bash
./scripts/test_loop.sh
./scripts/test_loop.sh --watch
```

## Custom Alias Setup

`sky --setup` already tries to install `sky` into `~/.local/bin`.
Use this when you want a second command name instead, for example `sk`:

```bash
sky --setup-alias sk
```

Optional flags:

```bash
sky --setup-alias sk --alias-dir ~/.local/bin
sky --setup-alias sk --force-alias
```

Then run:

```bash
sk -p "something short"
```

## Interactive Commands

Inside interactive mode:

- `/help` show commands
- `/url <url>` navigate to another site
- `/submit on|off` toggle auto-submit
- `/format markdown|plain|json` change response rendering format
- `/backend [local|pyreplab]` choose `/run` execution backend (default: pyreplab, falls back to local if unavailable)
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

`pyreplab` is the default `/run` backend when it is available, and `sky` falls back to `local` if it is not.
`local` still handles bash and other non-Python cells directly in the current workspace, and shell cells get `python`/`python3`/`pip` shims that point at the same interpreter `sky` is using.
`/py`, `/pyfile`, and `/run` share the same pyreplab session, so pre-imports persist for later cell runs.
Each `-i` session uses its own isolated pyreplab session directory to avoid cross-project leakage.

Use an explicit `pyreplab` command path:

```bash
sky -i --run-backend pyreplab --pyreplab-cmd /path/to/pyreplab
# or set PYREPLAB_CMD and use /backend pyreplab inside -i
```

Fast launch shortcut:

```bash
sky -i --pyreplab
```

## Add To PATH

If `sky --setup` reported that `~/.local/bin` is missing from `PATH`, add it:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Then run:

```bash
sky "Explain what an MCP session id is"
```

## Notes

- For the default `unchained` transport, profile mode reuses the Chrome profile you launched. `--incognito` and `--guest` create a fresh browser session instead.
- `SKY_API_KEY`, `SKY_AGENT_ID`, and `~/sky-agent/.env` only matter when you opt into `--transport sky-mcp`.
- `sky` talks to a browser tab, not the OpenAI API directly. For ChatGPT usage, the only account dependency is your normal web login session.
- The main CLI is intended to work on macOS and Linux anywhere `unchainedsky-cli`, Chrome, and Python are available.
- Browser foregrounding and window parking are macOS-only conveniences. On Linux and other non-macOS platforms, `sky` now skips that focus management and leaves the browser/window state alone.
- On macOS, `SKY_FOREGROUND_BROWSER=submit` (the default) brings Chrome to the foreground for the submit sequence once and then returns focus to the terminal. Set `SKY_FOREGROUND_BROWSER=poll` for aggressive background-safe polling, `SKY_FOREGROUND_BROWSER=0` to disable, or `SKY_BROWSER_APP` to override the browser app name.
- `scripts/test_install_terminal.sh` is macOS-only because it drives Terminal through `osascript`; it is not the cross-platform install test path.
- Different sites use different input DOM patterns; this script targets common chat UIs and may need selector tweaks for edge cases.
- Use `--debug` to print tool and transport diagnostics.
- Interactive mode clears stale draft text on startup.
- Tool-path tags like `[js_eval]` / `[fallback]` are hidden by default and shown only with `--debug`.

## License

SkyPrompt is available under the MIT License. See [`LICENSE`](./LICENSE).
