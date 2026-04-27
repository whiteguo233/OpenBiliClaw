#!/usr/bin/env bash
#
# OpenBiliClaw one-command installer.
#
# Usage:
#     curl -fsSL https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.sh | bash
#
# Environment overrides:
#     INSTALL_DIR      Target directory (default: $HOME/OpenBiliClaw)
#     REUSE_FROM       Reuse API keys/cookie from another OpenBiliClaw checkout
#                      (default: auto-detected under $HOME)
#     OPENBILICLAW_REPO_URL  Git repository URL (default: public GitHub)
#     OPENBILICLAW_BRANCH    Git branch to clone (default: main)
#     SKIP_START       Set to any non-empty value to skip starting the backend
#     MODE             auto | docker | local (default: auto)
#     PORT             API port (default: 8420)
#     HOST             API host  (default: 127.0.0.1)
#
# Examples:
#     INSTALL_DIR=$HOME/obc curl -fsSL .../install.sh | bash
#     REUSE_FROM=$HOME/workspace/OpenBiliClaw curl -fsSL .../install.sh | bash
#     SKIP_START=1 curl -fsSL .../install.sh | bash      # prepare only
#
# Works on macOS, Linux, and WSL2. Requires git and python3 (3.11+).
# Native Windows is not supported — use WSL2.

set -euo pipefail

readonly DEFAULT_REPO_URL="https://github.com/whiteguo233/OpenBiliClaw.git"
readonly DEFAULT_BRANCH="main"
readonly DEFAULT_INSTALL_DIR="${HOME}/OpenBiliClaw"
readonly CANDIDATE_SOURCES=(
    "${HOME}/workspace/OpenBiliClaw"
    "${HOME}/OpenBiliClaw"
    "${HOME}/projects/OpenBiliClaw"
    "${HOME}/code/OpenBiliClaw"
)

REPO_URL="${OPENBILICLAW_REPO_URL:-$DEFAULT_REPO_URL}"
BRANCH="${OPENBILICLAW_BRANCH:-$DEFAULT_BRANCH}"
INSTALL_DIR="${INSTALL_DIR:-$DEFAULT_INSTALL_DIR}"
# Distinguish "user explicitly set REUSE_FROM=" from "not set at all".
if [ "${REUSE_FROM+set}" = "set" ]; then
    _REUSE_FROM_EXPLICIT=1
else
    _REUSE_FROM_EXPLICIT=0
    REUSE_FROM=""
fi
SKIP_START="${SKIP_START:-}"
MODE="${MODE:-auto}"
PORT="${PORT:-8420}"
HOST="${HOST:-127.0.0.1}"

# ---------------------------------------------------------------------------
# Logging helpers (ANSI colours only when stdout is a tty)

if [ -t 1 ]; then
    readonly C_CYAN=$'\033[1;36m'
    readonly C_GREEN=$'\033[1;32m'
    readonly C_RED=$'\033[1;31m'
    readonly C_YELLOW=$'\033[1;33m'
    readonly C_RESET=$'\033[0m'
else
    readonly C_CYAN=""
    readonly C_GREEN=""
    readonly C_RED=""
    readonly C_YELLOW=""
    readonly C_RESET=""
fi

log()  { printf '%s[openbiliclaw]%s %s\n' "$C_CYAN"   "$C_RESET" "$*"; }
ok()   { printf '%s[openbiliclaw]%s %s\n' "$C_GREEN"  "$C_RESET" "$*"; }
warn() { printf '%s[openbiliclaw]%s %s\n' "$C_YELLOW" "$C_RESET" "$*" >&2; }
err()  { printf '%s[openbiliclaw]%s %s\n' "$C_RED"    "$C_RESET" "$*" >&2; }

# ---------------------------------------------------------------------------
# Prerequisite checks

require_command() {
    local cmd="$1"
    if ! command -v "$cmd" >/dev/null 2>&1; then
        err "Missing required command: $cmd"
        case "$cmd" in
            git)     err "  Install: https://git-scm.com/downloads" ;;
            python3) err "  Install Python 3.11+: https://www.python.org/downloads/" ;;
        esac
        exit 1
    fi
}

check_python_version() {
    local version
    version=$(python3 -c 'import sys; print("{}.{}".format(sys.version_info[0], sys.version_info[1]))')
    local major minor
    major=${version%.*}
    minor=${version#*.}
    if (( major < 3 )) || (( major == 3 && minor < 11 )); then
        err "Python 3.11+ required, found $version"
        exit 1
    fi
}

check_platform() {
    case "$(uname -s)" in
        Darwin|Linux) ;;
        MINGW*|MSYS*|CYGWIN*)
            err "Native Windows is not supported. Please install WSL2 and re-run this command."
            exit 1
            ;;
        *)
            warn "Unrecognised platform: $(uname -s). Proceeding anyway."
            ;;
    esac
}

# ---------------------------------------------------------------------------
# Source discovery (auto-reuse existing install)

auto_detect_reuse_source() {
    # If the user explicitly set REUSE_FROM (even to ""), skip auto-detection.
    if [ "$_REUSE_FROM_EXPLICIT" = "1" ]; then
        if [ -n "$REUSE_FROM" ]; then
            log "REUSE_FROM explicitly set to ${C_GREEN}${REUSE_FROM}${C_RESET}"
        else
            log "REUSE_FROM explicitly set to empty — skipping auto-detection."
        fi
        return
    fi
    local cand
    for cand in "${CANDIDATE_SOURCES[@]}"; do
        if [ "$cand" = "$INSTALL_DIR" ]; then
            continue
        fi
        if [ ! -d "$cand" ]; then
            continue
        fi
        # Valid if it has a config.toml OR a bilibili_cookie.json
        if [ -f "$cand/config.toml" ] || [ -f "$cand/data/bilibili_cookie.json" ]; then
            REUSE_FROM="$cand"
            log "Found existing OpenBiliClaw at ${C_GREEN}${REUSE_FROM}${C_RESET} — will reuse API keys and cookie."
            return
        fi
    done
}

# ---------------------------------------------------------------------------
# Main install steps

ensure_checkout() {
    if [ -f "$INSTALL_DIR/pyproject.toml" ] && [ -f "$INSTALL_DIR/config.example.toml" ]; then
        log "Using existing checkout at $INSTALL_DIR"
        return
    fi

    if [ -e "$INSTALL_DIR" ] && [ -n "$(ls -A "$INSTALL_DIR" 2>/dev/null || true)" ]; then
        err "Target directory is not empty and not an OpenBiliClaw checkout: $INSTALL_DIR"
        err "Set INSTALL_DIR to an empty or non-existent path, or remove the existing one first."
        exit 1
    fi

    mkdir -p "$(dirname "$INSTALL_DIR")"
    log "Cloning ${REPO_URL} (branch ${BRANCH}) into ${INSTALL_DIR}"
    git clone --branch "$BRANCH" --depth 1 "$REPO_URL" "$INSTALL_DIR"
}

BOOTSTRAP_LOG=""

cleanup_bootstrap_log() {
    if [ -n "$BOOTSTRAP_LOG" ] && [ -f "$BOOTSTRAP_LOG" ]; then
        rm -f "$BOOTSTRAP_LOG"
    fi
}
trap cleanup_bootstrap_log EXIT

run_bootstrap() {
    local bootstrap="$INSTALL_DIR/scripts/agent_bootstrap.py"
    if [ ! -f "$bootstrap" ]; then
        err "Bootstrap script missing: $bootstrap"
        err "Your checkout may be stale — run 'git pull' inside $INSTALL_DIR and retry."
        exit 1
    fi

    local args=(
        --project-dir "$INSTALL_DIR"
        --mode "$MODE"
        --host "$HOST"
        --port "$PORT"
    )
    if [ -n "$REUSE_FROM" ]; then
        args+=(--reuse-from "$REUSE_FROM")
    fi
    if [ -n "$SKIP_START" ]; then
        args+=(--skip-start)
    fi

    BOOTSTRAP_LOG=$(mktemp -t openbiliclaw-bootstrap.XXXXXX)
    log "Running bootstrap: python3 $bootstrap ${args[*]}"

    # Stream stdout to the terminal AND capture it for post-run parsing.
    # Use PIPESTATUS so `set -e` still sees the real bootstrap exit code.
    set +e
    python3 "$bootstrap" "${args[@]}" 2>&1 | tee "$BOOTSTRAP_LOG"
    local rc=${PIPESTATUS[0]}
    set -e

    if [ "$rc" -ne 0 ]; then
        err "Bootstrap exited with code $rc."
        err "The log above contains [bootstrap] lines and BOOTSTRAP_STATUS JSON events; the 'error' event tells you which step failed."
        err "Once the underlying issue is fixed, re-run this installer."
        exit "$rc"
    fi
}

# Print a human-readable install summary with next-step guidance.
print_install_summary() {
    local summary
    summary=$(python3 - "$BOOTSTRAP_LOG" "$INSTALL_DIR" "$PORT" "$HOST" "$REUSE_FROM" <<'PY'
import json
import sys
from pathlib import Path

log_path, install_dir, port, host, reuse_from = sys.argv[1:6]
final = None
for raw in Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines():
    marker = "BOOTSTRAP_STATUS:"
    if marker in raw:
        try:
            final = json.loads(raw.split(marker, 1)[1].strip())
        except json.JSONDecodeError:
            pass

if final is None:
    print("STATUS=unknown")
    print("HEALTH_URL=")
    print("MISSING=")
    sys.exit(0)

details = final.get("details") or {}
missing = details.get("missing") or []
print(f"STATUS={final.get('status', 'unknown')}")
print(f"HEALTH_URL={details.get('health_url', '')}")
print(f"MISSING={','.join(missing)}")
PY
)
    # Parse the three KEY=VALUE lines back into shell variables.
    local status health_url missing
    status=$(echo "$summary" | awk -F= '/^STATUS=/{sub(/^STATUS=/, ""); print; exit}')
    health_url=$(echo "$summary" | awk -F= '/^HEALTH_URL=/{sub(/^HEALTH_URL=/, ""); print; exit}')
    missing=$(echo "$summary" | awk -F= '/^MISSING=/{sub(/^MISSING=/, ""); print; exit}')

    if [ -z "$health_url" ]; then
        health_url="http://${HOST}:${PORT}/api/health"
    fi

    echo ""
    echo "================================================================"
    if [ "$status" = "complete" ]; then
        printf '%s OpenBiliClaw install complete%s\n' "$C_GREEN" "$C_RESET"
    elif [ "$status" = "running_with_missing_secrets" ] || [ "$status" = "needs_secrets" ]; then
        printf '%s OpenBiliClaw install partial (credentials still missing)%s\n' "$C_YELLOW" "$C_RESET"
    else
        printf '%s OpenBiliClaw install status: %s%s\n' "$C_YELLOW" "$status" "$C_RESET"
    fi
    echo "================================================================"
    echo "Status:      $status"
    echo "Checkout:    $INSTALL_DIR"
    if [ -n "$REUSE_FROM" ]; then
        echo "Reused from: $REUSE_FROM"
    fi
    echo "Health URL:  $health_url"
    if [ -n "$missing" ]; then
        echo "Missing:     $missing"
    else
        echo "Missing:     (none)"
    fi
    echo ""

    if [ -n "$missing" ]; then
        echo "Next steps (credentials are missing):"
        echo ""
        echo "  1. Choose your LLM provider (default: openai):"
        echo "     Supported: openai | gemini | claude | deepseek | openrouter | ollama"
        echo ""
        echo "  2. Prepare the missing values:"
        case "$missing" in
            *llm.*api_key*)
                echo "     - LLM API key — get one from your chosen provider:"
                echo "         OpenAI:     https://platform.openai.com/api-keys"
                echo "         Gemini:     https://aistudio.google.com/apikey"
                echo "         DeepSeek:   https://platform.deepseek.com/api_keys"
                echo "         Claude:     https://console.anthropic.com/settings/keys"
                echo "         OpenRouter: https://openrouter.ai/keys"
                echo "         Ollama:     (no key needed, just install and run)"
                ;;
        esac
        case "$missing" in
            *bilibili.cookie*)
                echo "     - Bilibili cookie:"
                echo "         a. Log in at https://www.bilibili.com"
                echo "         b. Open DevTools (F12) → Network tab"
                echo "         c. Refresh the page, click any request"
                echo "         d. Copy the full 'Cookie' header value"
                ;;
        esac
        echo ""
        echo "  3. Run with your values filled in:"
        echo ""
        # Build the command dynamically — only show flags for what's missing.
        echo "     python3 $INSTALL_DIR/scripts/agent_bootstrap.py \\"
        echo "         --project-dir $INSTALL_DIR \\"
        echo "         --provider <YOUR_PROVIDER> \\"
        case "$missing" in
            *llm.*api_key*) echo "         --llm-api-key '<YOUR_API_KEY>' \\" ;;
        esac
        case "$missing" in
            *bilibili.cookie*) echo "         --bilibili-cookie '<YOUR_COOKIE>' \\" ;;
        esac
        echo "         --port $PORT --host $HOST"
        echo ""
        echo "  3. Verify the backend is healthy:"
        echo "      curl -sS $health_url"
    else
        echo "All credentials present — init has been run automatically."
        echo ""
        echo "  - Verify the backend is healthy:"
        echo "      curl -sS $health_url"
        echo "  - See recommendations:"
        echo "      cd $INSTALL_DIR && uv run openbiliclaw recommend"
        echo "  - View your soul profile:"
        echo "      cd $INSTALL_DIR && uv run openbiliclaw profile"
        echo "  - Re-run init manually if needed:"
        echo "      cd $INSTALL_DIR && uv run openbiliclaw init"
    fi
    echo ""
    echo "Optional: enable local Ollama as the embedding fallback"
    echo "  (no extra API key needed; useful when your remote embedding quota runs out)"
    echo "      Mac:     brew install ollama && ollama serve &"
    echo "      Windows: install from https://ollama.com/download then start the app"
    echo "      Linux:   curl -fsSL https://ollama.com/install.sh | sh && ollama serve &"
    echo "  Then:"
    echo "      cd $INSTALL_DIR && uv run openbiliclaw setup-embedding"
    echo ""
    echo "Reference docs:"
    echo "  - $INSTALL_DIR/docs/agent-install.md     (install guide)"
    echo "  - $INSTALL_DIR/docs/agent-deployment.md  (troubleshooting)"
    echo "================================================================"
}

main() {
    log "OpenBiliClaw one-command installer"
    check_platform
    require_command git
    require_command python3
    check_python_version

    auto_detect_reuse_source
    ensure_checkout
    run_bootstrap
    print_install_summary
}

main "$@"
