#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TERMINAL_APP="${TERMINAL_APP:-Terminal}"
TEMP_HOME=""
KEEP_HOME=0
RUN_INSTALL=1
PRINT_ONLY=0
NO_PAUSE=0
SEED_API_KEY=""
SEED_AGENT_ID=""
PROMPT_TEXT='Reply with exactly SKY_INSTALL_OK and nothing else.'

usage() {
  cat <<'EOF'
Usage: ./scripts/test_install_terminal.sh [options]

Launches a real macOS Terminal session via osascript with an isolated HOME to
exercise the first-run sky install/setup flow from a clean environment.

Options:
  --temp-home PATH       Reuse a specific temp HOME instead of mktemp
  --keep-home            Do not delete the temp HOME when the Terminal run ends
  --skip-install         Do not auto-confirm installer launch inside ./sky
  --seed-api-key VALUE   Pre-seed ~/sky-agent/.env with SKY_API_KEY
  --seed-agent-id VALUE  Pre-seed ~/sky-agent/.env with SKY_AGENT_ID
  --prompt TEXT          Prompt to pass to ./sky -p (default: smoke prompt)
  --terminal-app NAME    macOS terminal app name (default: Terminal)
  --print-only           Create the temp HOME + runner script and print details
  --no-pause             Do not wait for Enter before closing Terminal
  -h, --help             Show this help

Examples:
  ./scripts/test_install_terminal.sh
  ./scripts/test_install_terminal.sh --skip-install --seed-api-key uc_demo_fake
  ./scripts/test_install_terminal.sh --temp-home /tmp/sky-install-home --keep-home
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --temp-home)
      TEMP_HOME="${2:-}"
      if [[ -z "$TEMP_HOME" ]]; then
        echo "error: --temp-home requires a value" >&2
        exit 2
      fi
      shift 2
      ;;
    --keep-home)
      KEEP_HOME=1
      shift
      ;;
    --skip-install)
      RUN_INSTALL=0
      shift
      ;;
    --seed-api-key)
      SEED_API_KEY="${2:-}"
      if [[ -z "$SEED_API_KEY" ]]; then
        echo "error: --seed-api-key requires a value" >&2
        exit 2
      fi
      shift 2
      ;;
    --seed-agent-id)
      SEED_AGENT_ID="${2:-}"
      if [[ -z "$SEED_AGENT_ID" ]]; then
        echo "error: --seed-agent-id requires a value" >&2
        exit 2
      fi
      shift 2
      ;;
    --prompt)
      PROMPT_TEXT="${2:-}"
      if [[ -z "$PROMPT_TEXT" ]]; then
        echo "error: --prompt requires a value" >&2
        exit 2
      fi
      shift 2
      ;;
    --terminal-app)
      TERMINAL_APP="${2:-}"
      if [[ -z "$TERMINAL_APP" ]]; then
        echo "error: --terminal-app requires a value" >&2
        exit 2
      fi
      shift 2
      ;;
    --print-only)
      PRINT_ONLY=1
      shift
      ;;
    --no-pause)
      NO_PAUSE=1
      shift
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

if [[ -z "$TEMP_HOME" ]]; then
  TEMP_HOME="$(mktemp -d /tmp/sky-install-terminal.XXXXXX)"
fi

mkdir -p "$TEMP_HOME/sky-agent"
ENV_FILE="$TEMP_HOME/sky-agent/.env"
rm -f "$ENV_FILE"

if [[ -n "$SEED_API_KEY" || -n "$SEED_AGENT_ID" ]]; then
  {
    if [[ -n "$SEED_API_KEY" ]]; then
      printf 'SKY_API_KEY="%s"\n' "$SEED_API_KEY"
    fi
    if [[ -n "$SEED_AGENT_ID" ]]; then
      printf 'SKY_AGENT_ID="%s"\n' "$SEED_AGENT_ID"
    fi
  } > "$ENV_FILE"
fi

RUNNER_PATH="$TEMP_HOME/run_sky_install_test.sh"
LOG_PATH="$TEMP_HOME/sky_install_terminal.log"
LOG_PATH="/tmp/sky_install_terminal.$(basename "$TEMP_HOME").log"
RUN_INSTALL_VALUE="$RUN_INSTALL"
KEEP_HOME_VALUE="$KEEP_HOME"
NO_PAUSE_VALUE="$NO_PAUSE"
CURRENT_PATH="${PATH:-/usr/bin:/bin:/usr/sbin:/sbin}"
CURRENT_TERM="${TERM:-xterm-256color}"

cat > "$RUNNER_PATH" <<EOF
#!/usr/bin/env bash
set -euo pipefail

export HOME=$(printf '%q' "$TEMP_HOME")
export PATH=$(printf '%q' "$CURRENT_PATH")
export TERM=$(printf '%q' "$CURRENT_TERM")
export PYTHONDONTWRITEBYTECODE=1
unset SKY_API_KEY SKY_AGENT_ID SKY_TARGET_URL PYREPLAB_CMD
unset SKY_SETUP_ASSUME_YES

LOG_PATH=$(printf '%q' "$LOG_PATH")
ROOT_DIR=$(printf '%q' "$ROOT_DIR")
RUN_INSTALL_VALUE=$(printf '%q' "$RUN_INSTALL_VALUE")
KEEP_HOME_VALUE=$(printf '%q' "$KEEP_HOME_VALUE")
NO_PAUSE_VALUE=$(printf '%q' "$NO_PAUSE_VALUE")
PROMPT_TEXT=$(printf '%q' "$PROMPT_TEXT")

echo "[sky-install-test] home=\$HOME"
echo "[sky-install-test] env_file=\$HOME/sky-agent/.env"
if [[ -f "\$HOME/sky-agent/.env" ]]; then
  echo "[sky-install-test] initial_env:"
  sed 's/^/  /' "\$HOME/sky-agent/.env"
else
  echo "[sky-install-test] initial_env: <missing>"
fi

cd "\$ROOT_DIR"

if [[ "\$RUN_INSTALL_VALUE" == "1" ]]; then
  export SKY_SETUP_ASSUME_YES=1
  echo "[sky-install-test] sky will auto-launch install.sh"
else
  echo "[sky-install-test] sky will not auto-launch install.sh"
fi

echo "[sky-install-test] running ./sky"
set +e
./sky -p "\$PROMPT_TEXT"
status=\$?
set -e
echo "[sky-install-test] sky exit=\$status"

if [[ -f "\$HOME/sky-agent/.env" ]]; then
  echo "[sky-install-test] final_env:"
  sed 's/^/  /' "\$HOME/sky-agent/.env"
fi

echo
echo "[sky-install-test] log saved at \$LOG_PATH"
if [[ "\$NO_PAUSE_VALUE" != "1" ]]; then
  echo "[sky-install-test] press Enter to close this Terminal session"
  read -r _
fi
if [[ "\$KEEP_HOME_VALUE" != "1" ]]; then
  rm -rf "\$HOME"
fi
exit "\$status"
EOF

chmod +x "$RUNNER_PATH"

echo "temp_home=$TEMP_HOME"
echo "env_file=$ENV_FILE"
echo "runner=$RUNNER_PATH"
echo "log=$LOG_PATH"

if [[ "$PRINT_ONLY" -eq 1 ]]; then
  echo "print_only=1"
  exit 0
fi

python3 - <<'PY' "$TERMINAL_APP" "$RUNNER_PATH" "$LOG_PATH"
import shlex
import subprocess
import sys

terminal_app = sys.argv[1]
runner_path = sys.argv[2]
log_path = sys.argv[3]
command = f"script -q {shlex.quote(log_path)} bash {shlex.quote(runner_path)}"
script = (
    f'tell application "{terminal_app}"\n'
    "activate\n"
    f'do script "{command.replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"\n'
    "end tell\n"
)
subprocess.run(["osascript", "-e", script], check=True)
PY
