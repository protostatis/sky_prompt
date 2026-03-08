#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WATCH=0
INTERVAL=1
PYREPLAB_CMD="${PYREPLAB_CMD:-}"

usage() {
  cat <<'EOF'
Usage: ./scripts/test_loop.sh [--watch] [--interval N] [--pyreplab-cmd PATH]

Runs a fast local test loop for the CLI:
1) syntax compile check
2) built-in self tests
3) tool-call id uniqueness check
4) optional pyreplab state smoke test (if available)

Options:
  --watch              rerun when project files change
  --interval N         polling interval seconds for --watch (default: 1)
  --pyreplab-cmd PATH  explicit pyreplab command path
  -h, --help           show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --watch)
      WATCH=1
      shift
      ;;
    --interval)
      INTERVAL="${2:-}"
      if [[ -z "$INTERVAL" ]]; then
        echo "error: --interval requires a value" >&2
        exit 2
      fi
      shift 2
      ;;
    --pyreplab-cmd)
      PYREPLAB_CMD="${2:-}"
      if [[ -z "$PYREPLAB_CMD" ]]; then
        echo "error: --pyreplab-cmd requires a value" >&2
        exit 2
      fi
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

project_fingerprint() {
  (
    cd "$ROOT_DIR"
    {
      find . -type f \
        ! -path './.git/*' \
        ! -path './.claude/*' \
        ! -path './__pycache__/*' \
        ! -path './.sky_cells/*' \
        \( -name '*.py' -o -name '*.sh' -o -name 'README.md' -o -name 'pyproject.toml' \) \
        -print0 \
      | xargs -0 stat -f '%N:%m'
    } | LC_ALL=C sort | shasum | awk '{print $1}'
  )
}

run_once() {
  cd "$ROOT_DIR"
  export PYTHONDONTWRITEBYTECODE=1

  echo "[1/4] syntax check"
  python3 - <<'PY'
from pathlib import Path
compile(Path("unchained_prompt.py").read_text(encoding="utf-8"), "unchained_prompt.py", "exec")
print("syntax_ok")
PY

  echo "[2/4] built-in self tests"
  ./.sky --self-test

  echo "[3/4] tool-call id uniqueness smoke"
  python3 - <<'PY'
from unchained_prompt import MCPClient
c = MCPClient(endpoint="https://example.invalid/mcp", api_key="test")
a = c._next_rpc_id("tool-call-js_eval")
b = c._next_rpc_id("tool-call-js_eval")
assert a != b, "rpc ids should be unique"
print("rpc_id_unique_ok")
PY

  echo "[4/4] pyreplab state smoke (optional)"
  python3 - <<'PY'
import os
import subprocess
from pathlib import Path
import unchained_prompt as up

workdir = Path.cwd()
explicit_cmd = os.getenv("PYREPLAB_CMD", "").strip() or None
cmd = up.resolve_pyreplab_command(explicit_cmd)
if not cmd:
    print("pyreplab_skip (not found)")
    raise SystemExit(0)

session_dir = up.allocate_pyreplab_session_dir(workdir)
subprocess.run(cmd + ["stop-all"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
try:
    first = up.execute_pyreplab_code(
        "import math\npi_cache = math.pi",
        cmd,
        workdir,
        session_dir=session_dir,
        timeout_s=10,
    )
    second = up.execute_pyreplab_code(
        "print(round(pi_cache, 3))",
        cmd,
        workdir,
        session_dir=session_dir,
        timeout_s=10,
    )
    assert bool(first.get("ok")), f"first command failed: {first}"
    assert bool(second.get("ok")), f"second command failed: {second}"
    assert "3.142" in str(second.get("stdout") or ""), f"unexpected output: {second.get('stdout')!r}"
    print("pyreplab_state_ok")
finally:
    up.stop_pyreplab_session(cmd, workdir, session_dir=session_dir, timeout_s=6)
PY

  echo "all checks passed"
}

if [[ -n "$PYREPLAB_CMD" ]]; then
  export PYREPLAB_CMD
fi

if [[ "$WATCH" -eq 0 ]]; then
  run_once
  exit 0
fi

echo "watch mode enabled (interval=${INTERVAL}s)"
last_fp=""
while true; do
  current_fp="$(project_fingerprint)"
  if [[ "$current_fp" != "$last_fp" ]]; then
    echo
    date '+[%Y-%m-%d %H:%M:%S] change detected; running test loop'
    if run_once; then
      echo "loop status: PASS"
    else
      echo "loop status: FAIL"
    fi
    last_fp="$current_fp"
  fi
  sleep "$INTERVAL"
done
