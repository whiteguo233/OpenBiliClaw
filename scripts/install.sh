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
#     HOST             API host  (default: 0.0.0.0)
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
HOST="${HOST:-0.0.0.0}"

extend_no_proxy_for_localhost() {
    local current="${NO_PROXY:-${no_proxy:-}}"
    local host
    for host in localhost 127.0.0.1 ::1; do
        case ",$current," in
            *",$host,"*) ;;
            *) current="${current:+$current,}$host" ;;
        esac
    done
    export NO_PROXY="$current"
    export no_proxy="$current"
}

extend_no_proxy_for_localhost

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
        # Auto-update when safe: clean working tree + fast-forward available.
        # The previous behaviour silently kept any stale ref, so a user who
        # installed weeks ago and re-ran the one-liner thought they got the
        # latest while still running old code.
        if [ -d "$INSTALL_DIR/.git" ]; then
            (
                cd "$INSTALL_DIR" || exit 0
                git fetch --quiet origin "$BRANCH" 2>/dev/null || {
                    log "${C_YELLOW}git fetch failed; skipping update check.${C_RESET}"
                    exit 0
                }
                local_sha=$(git rev-parse HEAD 2>/dev/null)
                remote_sha=$(git rev-parse "origin/$BRANCH" 2>/dev/null)
                if [ -z "$local_sha" ] || [ -z "$remote_sha" ] || [ "$local_sha" = "$remote_sha" ]; then
                    return 0
                fi
                behind=$(git rev-list --count "$local_sha..$remote_sha" 2>/dev/null || echo "?")
                dirty=$(git status --porcelain 2>/dev/null)
                if [ -n "$dirty" ]; then
                    log "${C_YELLOW}⚠ Existing checkout is $behind commits behind origin/$BRANCH but has local changes — skipping auto-update.${C_RESET}"
                    log "  To update manually: cd $INSTALL_DIR && git stash && git pull && git stash pop"
                    return 0
                fi
                log "Updating existing checkout: $behind commits behind origin/$BRANCH — pulling…"
                if git pull --ff-only --quiet origin "$BRANCH"; then
                    log "${C_GREEN}✓ Updated to $(git rev-parse --short HEAD)${C_RESET}"
                else
                    log "${C_YELLOW}git pull failed (non-fast-forward?); keeping current checkout.${C_RESET}"
                    log "  To force a fresh install: rm -rf $INSTALL_DIR && rerun this installer"
                fi
            )
        fi
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
    local interactive_bootstrap=0
    if [ -z "${OPENBILICLAW_NONINTERACTIVE:-}" ] && [ -z "${CI:-}" ]; then
        if [ -t 0 ] || [ -r /dev/tty ]; then
            args+=(--interactive-confirm --wait-for-extension-cookie)
            interactive_bootstrap=1
        fi
    fi

    BOOTSTRAP_LOG=$(mktemp -t openbiliclaw-bootstrap.XXXXXX)
    log "Running bootstrap: python3 $bootstrap ${args[*]}"

    # Stream stdout to the terminal AND capture it for post-run parsing.
    # Use PIPESTATUS so `set -e` still sees the real bootstrap exit code.
    set +e
    if [ "$interactive_bootstrap" = "1" ] && [ -r /dev/tty ]; then
        python3 "$bootstrap" "${args[@]}" </dev/tty 2>&1 | tee "$BOOTSTRAP_LOG"
    else
        python3 "$bootstrap" "${args[@]}" 2>&1 | tee "$BOOTSTRAP_LOG"
    fi
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
init_decisions = details.get("init_decisions") or {}
decision_missing = init_decisions.get("missing") or []
xhs_flag = ((init_decisions.get("xhs") or {}).get("flag") or "")
douyin_flag = ((init_decisions.get("douyin") or {}).get("flag") or "")
youtube_flag = ((init_decisions.get("youtube") or {}).get("flag") or "")
print(f"STATUS={final.get('status', 'unknown')}")
print(f"HEALTH_URL={details.get('health_url', '')}")
print(f"MISSING={','.join(missing)}")
print(f"DECISIONS={','.join(decision_missing)}")
print(f"XHS_FLAG={xhs_flag}")
print(f"DOUYIN_FLAG={douyin_flag}")
print(f"YOUTUBE_FLAG={youtube_flag}")
PY
)
    # Parse the KEY=VALUE lines back into shell variables.
    local status health_url missing decisions xhs_flag douyin_flag youtube_flag
    status=$(echo "$summary" | awk -F= '/^STATUS=/{sub(/^STATUS=/, ""); print; exit}')
    health_url=$(echo "$summary" | awk -F= '/^HEALTH_URL=/{sub(/^HEALTH_URL=/, ""); print; exit}')
    missing=$(echo "$summary" | awk -F= '/^MISSING=/{sub(/^MISSING=/, ""); print; exit}')
    decisions=$(echo "$summary" | awk -F= '/^DECISIONS=/{sub(/^DECISIONS=/, ""); print; exit}')
    xhs_flag=$(echo "$summary" | awk -F= '/^XHS_FLAG=/{sub(/^XHS_FLAG=/, ""); print; exit}')
    douyin_flag=$(echo "$summary" | awk -F= '/^DOUYIN_FLAG=/{sub(/^DOUYIN_FLAG=/, ""); print; exit}')
    youtube_flag=$(echo "$summary" | awk -F= '/^YOUTUBE_FLAG=/{sub(/^YOUTUBE_FLAG=/, ""); print; exit}')
    if [ -z "$xhs_flag" ]; then
        xhs_flag="--no-xhs"
    fi
    if [ -z "$douyin_flag" ]; then
        douyin_flag="--no-douyin"
    fi
    if [ -z "$youtube_flag" ]; then
        youtube_flag="--no-youtube"
    fi

    if [ -z "$health_url" ]; then
        if [ "$HOST" = "0.0.0.0" ] || [ "$HOST" = "::" ] || [ "$HOST" = "[::]" ]; then
            health_url="http://127.0.0.1:${PORT}/api/health"
        else
            health_url="http://${HOST}:${PORT}/api/health"
        fi
    fi

    # Distinguish "only Bilibili cookie missing" (the expected state for
    # users on the recommended browser-extension auto-sync path) from
    # "still need an LLM key" (a genuinely missing prerequisite). The
    # YELLOW + "partial / credentials still missing" wording on the
    # cookie-only case used to read like an install failure to users.
    local missing_only_cookie=0
    if [ -n "$missing" ] && [ "$missing" = "bilibili.cookie" ]; then
        missing_only_cookie=1
    fi

    echo ""
    echo "================================================================"
    if [ "$status" = "complete" ]; then
        printf '%s OpenBiliClaw install complete%s\n' "$C_GREEN" "$C_RESET"
    elif [ "$status" = "needs_decisions" ]; then
        printf '%s OpenBiliClaw backend ready — waiting for init choices%s\n' "$C_GREEN" "$C_RESET"
    elif [ "$missing_only_cookie" = "1" ]; then
        # Backend is up and configured; the extension will deliver the
        # cookie when the user installs it. This is the happy path, not
        # a failure — show it green-ish.
        printf '%s OpenBiliClaw backend ready — waiting for browser extension to sync B站 Cookie%s\n' "$C_GREEN" "$C_RESET"
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
        echo "             ⓘ Reused API keys / B站 cookie are NOT validated."
        echo "               B站 cookies expire within weeks; if the previous"
        echo "               install's cookie is stale, init will silently"
        echo "               return 0 history items and the soul profile will"
        echo "               be hollow. Agents: surface this reuse to the user"
        echo "               (HARD RULE 4 in docs/agent-install.md) before"
        echo "               proceeding — do not skip the cookie question just"
        echo "               because the file already exists."
        echo "               To skip auto-reuse and supply fresh credentials:"
        echo "               REUSE_FROM= curl -fsSL ... | bash"
    fi
    echo "Health URL:  $health_url"
    if [ -n "$missing" ]; then
        echo "Missing:     $missing"
    else
        echo "Missing:     (none)"
    fi
    if [ -n "$decisions" ]; then
        echo "Init choices needed: $decisions"
    fi
    echo ""

    if [ "$status" = "needs_decisions" ]; then
        echo "Next steps — ask the user before running init:"
        echo ""
        echo "  1. Embedding service (default recommendation):"
        echo "       Local Ollama bge-m3 — free/offline/no extra API key."
        echo "       If they choose Gemini/OpenAI/custom instead, replace the"
        echo "       --embedding-* flags below with their chosen provider/model."
        echo ""
        echo "  2. Source bootstrap data (privacy choice):"
        echo "       Ask whether to include Xiaohongshu likes/favorites and"
        echo "       Douyin post/favorite/like/follow and YouTube history/"
        echo "       subscriptions/likes in the initial profile."
        echo "       Default is NO unless they explicitly opt in per source."
        echo ""
        echo "  3. Re-run bootstrap with explicit choices (DO NOT add --skip-init):"
        echo ""
        echo "     python3 $INSTALL_DIR/scripts/agent_bootstrap.py \\"
        echo "         --project-dir $INSTALL_DIR \\"
        case "$decisions" in
            *embedding*)
                echo "         --embedding-provider ollama \\"
                echo "         --embedding-model bge-m3 \\"
                ;;
        esac
        echo "         $xhs_flag \\"
        echo "         $douyin_flag \\"
        echo "         $youtube_flag \\"
        echo "         --port $PORT --host $HOST"
        echo ""
        echo "     Use --yes-xhs / --yes-douyin / --yes-youtube only after"
        echo "     the user says yes; otherwise keep the matching --no-* flag."
        echo "     This then runs init: B站 history, soul profile, first discovery."
    elif [ "$missing_only_cookie" = "1" ]; then
        echo "Next step — get your B站 Cookie to the backend (pick ONE):"
        echo ""
        echo "  (A) [recommended, zero config]"
        echo "      Install the browser extension and log in to bilibili.com."
        echo "      It auto-syncs your cookie to this backend within seconds."
        echo "        Extension: https://github.com/whiteguo233/OpenBiliClaw/releases"
        echo "      Once the cookie arrives, ask the init choices below and re-run"
        echo "      bootstrap so it can run 'openbiliclaw init'."
        echo ""
        echo "      Required before init:"
        echo "        - Embedding model/service (default: Ollama bge-m3)"
        echo "        - Xiaohongshu likes/favorites? (default: no; yes only on opt-in)"
        echo "        - Douyin post/favorite/like/follow? (default: no; yes only on opt-in)"
        echo "        - YouTube history/subscriptions/likes? (default: no; yes only on opt-in)"
        echo ""
        echo "  (B) [manual fallback]"
        echo "      F12 → Network → copy the 'Cookie' header from any"
        echo "      bilibili.com request, then run:"
        echo "        python3 $INSTALL_DIR/scripts/agent_bootstrap.py \\"
        echo "            --project-dir $INSTALL_DIR \\"
        echo "            --bilibili-cookie '<YOUR_COOKIE>' \\"
        case "$decisions" in
            *embedding*)
                echo "            --embedding-provider ollama \\"
                echo "            --embedding-model bge-m3 \\"
                ;;
        esac
        echo "            $xhs_flag \\"
        echo "            $douyin_flag \\"
        echo "            $youtube_flag \\"
        echo "            --port $PORT --host $HOST"
        echo "      Use --yes-xhs / --yes-douyin / --yes-youtube only after"
        echo "      the user opts in; otherwise keep the matching --no-* flag."
        echo ""
        echo "  Verify the backend is healthy any time:"
        echo "      curl -sS $health_url"
    elif [ -n "$missing" ]; then
        echo "Next steps (credentials are missing):"
        echo ""
        echo "  1. Choose your LLM provider (default: deepseek):"
        echo "     Supported: deepseek | openai | gemini | claude | openrouter | ollama"
        echo ""
        echo "  2. Ask which embedding service to use:"
        echo "     Default: local Ollama bge-m3 (free/offline/no extra API key)."
        echo "     Alternatives: Gemini embedding, OpenAI text-embedding-3-small,"
        echo "     or a custom OpenAI-compatible embedding endpoint."
        echo ""
        echo "  3. Ask whether to include source bootstrap data:"
        echo "     Xiaohongshu likes/favorites, Douyin post/favorite/like/follow,"
        echo "     and YouTube history/subscriptions/likes."
        echo "     Default: no. Use --yes-* flags only after explicit opt-in."
        echo ""
        echo "  4. Prepare the missing values:"
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
                echo "     - Bilibili cookie. Two ways to provide it (pick ONE):"
                echo ""
                echo "       (A) [recommended] Install the browser extension and let"
                echo "           it auto-sync your cookie — no F12, no copy/paste."
                echo "           Extension: https://github.com/whiteguo233/OpenBiliClaw/releases"
                echo "           After install, log in to bilibili.com if you aren't already;"
                echo "           the extension pushes the cookie to this backend within seconds."
                echo "           If you go this route, you can SKIP the --bilibili-cookie flag"
                echo "           below after the extension syncs."
                echo ""
                echo "       (B) Manually paste the cookie:"
                echo "           a. Log in at https://www.bilibili.com"
                echo "           b. Open DevTools (F12) → Network tab"
                echo "           c. Refresh the page, click any request"
                echo "           d. Copy the full 'Cookie' header value"
                echo "           Then proceed with step 5 below using --bilibili-cookie."
                ;;
        esac
        echo ""
        echo "  5. Run with your values filled in (DO NOT add --skip-init):"
        echo ""
        # Build the command dynamically — only show flags for what's missing.
        echo "     python3 $INSTALL_DIR/scripts/agent_bootstrap.py \\"
        echo "         --project-dir $INSTALL_DIR \\"
        echo "         --provider <YOUR_PROVIDER> \\"
        case "$missing" in
            *llm.*api_key*) echo "         --llm-api-key '<YOUR_API_KEY>' \\" ;;
        esac
        case "$decisions" in
            *embedding*)
                echo "         --embedding-provider ollama \\"
                echo "         --embedding-model bge-m3 \\"
                ;;
        esac
        echo "         $xhs_flag \\"
        echo "         $douyin_flag \\"
        echo "         $youtube_flag \\"
        case "$missing" in
            *bilibili.cookie*) echo "         --bilibili-cookie '<YOUR_COOKIE>' \\" ;;
        esac
        echo "         --port $PORT --host $HOST"
        echo ""
        echo "     Replace the embedding/source flags according to the"
        echo "     user's answers before running the command."
        echo ""
        echo "     This auto-runs 'openbiliclaw init' once credentials check out:"
        echo "       - pulls your Bilibili history"
        echo "       - generates the soul profile"
        echo "       - runs the first content discovery pass"
        echo "     Takes 2-5 minutes. Without this step the extension shows nothing."
        echo "     During init, relay BOOTSTRAP_STATUS init_progress events to the user."
        echo ""
        echo "  6. Verify the backend is healthy:"
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
