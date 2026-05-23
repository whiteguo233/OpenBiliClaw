#!/usr/bin/env python3
"""Agent-driven bootstrap script for OpenBiliClaw.

This script is intended to be invoked by an AI coding agent (Claude Code,
Codex CLI, OpenClaw, Cursor, etc.) after the user pastes the README "Agent
deployment prompt" into the agent. The agent parses the prompt, runs this
script with the appropriate flags, then handles any interactive follow-ups
(missing API key, missing Bilibili cookie, or explicit init source decisions)
that the script reports.

The script is intentionally non-interactive and machine-friendly:
- emits structured JSON status lines prefixed with ``BOOTSTRAP_STATUS:``
- exits 0 on success, non-zero on failure
- never prompts stdin (agent/user input is driven from outside the script)

Supported flows:
1. Docker path (preferred if Docker + docker compose are available)
2. Local Python path (uv preferred, pip fallback)
3. Reuse secrets from an existing OpenBiliClaw checkout

Typical agent workflow:

    1. Detect or clone repo into target directory.
    2. Run ``python scripts/agent_bootstrap.py --mode auto`` (add
       ``--reuse-from <path>`` when the user already has a working install).
    3. Parse ``BOOTSTRAP_STATUS`` JSON lines to decide next steps.
    4. If the final status says ``missing_llm_key`` or ``missing_cookie``,
       ask the user for the value and re-run with ``--llm-api-key`` or
       ``--bilibili-cookie``.
    5. Poll the emitted ``Health URL`` to confirm the service is ready.

All secrets accepted via flags are written directly to ``config.toml`` and
``data/bilibili_cookie.json``. Nothing is uploaded off the machine.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

# ---------------------------------------------------------------------------
# Constants

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8420
DEFAULT_REPO_URL = "https://github.com/whiteguo233/OpenBiliClaw.git"
DEFAULT_HEALTH_PATH = "/api/health"
HEALTH_TIMEOUT_SECONDS = 90
HEALTH_POLL_INTERVAL = 2.0
LOCAL_NO_PROXY_HOSTS = ("localhost", "127.0.0.1", "::1")
DOCKER_CONTAINER_NAME = "openbiliclaw-backend"
DOCKER_RUNTIME_ROOT = "/app/runtime"
DEFAULT_BILIBILI_FAVORITE_LIMIT = 300
DEFAULT_BILIBILI_FOLLOW_LIMIT = 100

SUPPORTED_PROVIDERS = ("openai", "claude", "gemini", "deepseek", "ollama", "openrouter")
REMOTE_PROVIDERS = ("openai", "claude", "gemini", "deepseek", "openrouter")

# Providers whose backend has no embeddings endpoint. When a user picks
# one of these as the primary LLM and doesn't explicitly configure
# embedding, we auto-wire local Ollama bge-m3 so the install actually
# pulls the embedding model (otherwise embeddings silently fall back at
# runtime to whatever the registry can find — see registry.py
# build_embedding_service).
PROVIDERS_WITHOUT_EMBED = ("claude", "deepseek", "openrouter")


def ensure_local_no_proxy() -> str:
    """Keep localhost backend checks out of user/global HTTP proxies."""

    parts: list[str] = []
    for key in ("NO_PROXY", "no_proxy"):
        raw = os.environ.get(key, "")
        for item in raw.split(","):
            value = item.strip()
            if value and value not in parts:
                parts.append(value)
    for host in LOCAL_NO_PROXY_HOSTS:
        if host not in parts:
            parts.append(host)
    value = ",".join(parts)
    os.environ["NO_PROXY"] = value
    os.environ["no_proxy"] = value
    return value


# Mirror of cli.py's _OPENAI_COMPAT_PRESETS for non-interactive (AI agent
# driven) installs. Keep the model defaults in sync with cli.py — when
# updating one, update the other. Each preset implies provider="openai"
# (the universal Bearer-auth + /v1/chat/completions client).
LLM_PRESETS: dict[str, dict[str, str]] = {
    "kimi": {
        "base_url": "https://api.moonshot.ai/v1",
        "model": "kimi-k2.6",
    },
    "minimax": {
        "base_url": "https://api.minimax.io/v1",
        "model": "MiniMax-M2.7",
    },
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
    },
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4.7-flash",
    },
    "yi": {
        "base_url": "https://api.lingyiwanwu.com/v1",
        "model": "yi-medium",
    },
    "self-hosted": {
        "base_url": "http://localhost:8000/v1",
        "model": "",  # user must specify
    },
    "relay": {
        "base_url": "",  # user must specify
        "model": "gpt-5-nano",
    },
    "azure": {
        "base_url": "",  # user must specify (per-deployment URL)
        "model": "",  # deployment name
    },
    "custom": {
        "base_url": "",
        "model": "",
    },
}


# ---------------------------------------------------------------------------
# Immutable status + exit codes


@dataclass(frozen=True)
class BootstrapResult:
    """Immutable result emitted to the agent."""

    status: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InitConfirmationAnswers:
    """Explicit user decisions required before auto-init may run."""

    embedding_provider: str
    embedding_model: str
    xhs: bool
    douyin: bool
    youtube: bool
    cookie_mode: str
    bilibili_favorite_limit: int = DEFAULT_BILIBILI_FAVORITE_LIMIT
    bilibili_follow_limit: int = DEFAULT_BILIBILI_FOLLOW_LIMIT
    bilibili_cookie: str = ""


def emit(result: BootstrapResult) -> None:
    """Emit a machine-parseable status line for the caller agent."""

    payload = {
        "status": result.status,
        "message": result.message,
        "details": result.details,
    }
    print(f"BOOTSTRAP_STATUS: {json.dumps(payload, ensure_ascii=False)}")
    sys.stdout.flush()


def info(message: str) -> None:
    """Human-readable log line that sits above BOOTSTRAP_STATUS events."""

    print(f"[bootstrap] {message}")
    sys.stdout.flush()


def confirmation_answers_to_bootstrap_args(answers: InitConfirmationAnswers) -> list[str]:
    """Convert interactive answers to the same explicit flags agents pass."""

    args = [
        "--embedding-provider",
        answers.embedding_provider,
        "--embedding-model",
        answers.embedding_model,
        "--yes-xhs" if answers.xhs else "--no-xhs",
        "--yes-douyin" if answers.douyin else "--no-douyin",
        "--yes-youtube" if answers.youtube else "--no-youtube",
        "--bilibili-favorite-limit",
        str(max(0, int(answers.bilibili_favorite_limit))),
        "--bilibili-follow-limit",
        str(max(0, int(answers.bilibili_follow_limit))),
    ]
    if answers.cookie_mode == "manual" and answers.bilibili_cookie:
        args.extend(["--bilibili-cookie", answers.bilibili_cookie])
    return args


def _ask_yes_no(
    input_func: Any,
    prompt: str,
    *,
    default: bool = False,
) -> bool:
    suffix = "Y/n" if default else "y/N"
    raw = str(input_func(f"{prompt} [{suffix}]: ")).strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes", "1", "true", "是", "好", "同意"}


def _ask_non_negative_int(
    input_func: Any,
    prompt: str,
    *,
    default: int,
) -> int:
    raw = str(input_func(f"{prompt} [{default}]: ")).strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def collect_interactive_confirmations(input_func: Any | None = input) -> InitConfirmationAnswers:
    """Ask the user for init decisions in human-run installer flows."""

    if input_func is None or (input_func is input and not sys.stdin.isatty()):
        raise RuntimeError("interactive confirmation requires a terminal")

    print("")
    print("OpenBiliClaw init choices")
    print("Embedding default: local Ollama bge-m3 (free/offline/no extra API key).")
    embedding_choice = str(
        input_func("Embedding provider [ollama] (enter to accept default): ")
    ).strip()
    embedding_provider = embedding_choice or "ollama"
    model_default = "bge-m3" if embedding_provider == "ollama" else ""
    embedding_model = (
        str(input_func(f"Embedding model [{model_default}] (enter to accept default): ")).strip()
        or model_default
    )

    print("")
    print(
        "Bilibili init signal limits default to 300 favorites / 100 follows; "
        "enter 0 to skip one signal."
    )
    bilibili_favorite_limit = _ask_non_negative_int(
        input_func,
        "Max Bilibili favorites to import during init",
        default=DEFAULT_BILIBILI_FAVORITE_LIMIT,
    )
    bilibili_follow_limit = _ask_non_negative_int(
        input_func,
        "Max Bilibili followed creators to import during init",
        default=DEFAULT_BILIBILI_FOLLOW_LIMIT,
    )

    print("")
    print("Optional source data is disabled by default unless you explicitly opt in.")
    xhs = _ask_yes_no(
        input_func,
        "Include Xiaohongshu likes/favorites in the initial profile?",
        default=False,
    )
    douyin = _ask_yes_no(
        input_func,
        "Include Douyin post/favorite/like/follow data in the initial profile?",
        default=False,
    )
    youtube = _ask_yes_no(
        input_func,
        "Include YouTube history/subscriptions/likes in the initial profile?",
        default=False,
    )

    print("")
    print("Bilibili auth default: browser extension sync.")
    cookie_mode_raw = (
        str(input_func("Bilibili cookie source: extension/manual/existing [extension]: "))
        .strip()
        .lower()
    )
    cookie_mode = cookie_mode_raw or "extension"
    bilibili_cookie = ""
    if cookie_mode == "manual":
        bilibili_cookie = str(input_func("Paste Bilibili Cookie header: ")).strip()
    elif cookie_mode not in {"extension", "existing"}:
        cookie_mode = "extension"

    return InitConfirmationAnswers(
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
        xhs=xhs,
        douyin=douyin,
        youtube=youtube,
        cookie_mode=cookie_mode,
        bilibili_favorite_limit=bilibili_favorite_limit,
        bilibili_follow_limit=bilibili_follow_limit,
        bilibili_cookie=bilibili_cookie,
    )


def apply_confirmation_answers_to_args(
    args: argparse.Namespace,
    answers: InitConfirmationAnswers,
) -> None:
    """Mutate parsed args with interactive choices where flags were omitted."""

    if args.embedding_provider is None:
        args.embedding_provider = answers.embedding_provider
    if args.embedding_model is None:
        args.embedding_model = answers.embedding_model
    if not args.yes_xhs and not args.no_xhs:
        args.yes_xhs = answers.xhs
        args.no_xhs = not answers.xhs
    if not args.yes_douyin and not args.no_douyin:
        args.yes_douyin = answers.douyin
        args.no_douyin = not answers.douyin
    if not args.yes_youtube and not args.no_youtube:
        args.yes_youtube = answers.youtube
        args.no_youtube = not answers.youtube
    if args.bilibili_favorite_limit is None:
        args.bilibili_favorite_limit = answers.bilibili_favorite_limit
    if args.bilibili_follow_limit is None:
        args.bilibili_follow_limit = answers.bilibili_follow_limit
    if answers.cookie_mode == "manual" and answers.bilibili_cookie and not args.bilibili_cookie:
        args.bilibili_cookie = answers.bilibili_cookie
    if answers.cookie_mode == "extension":
        args.wait_for_extension_cookie = True


# ---------------------------------------------------------------------------
# CLI argument parsing


def build_arg_parser() -> argparse.ArgumentParser:
    """Return the immutable argument parser."""

    parser = argparse.ArgumentParser(
        description="Automated OpenBiliClaw bootstrap for AI coding agents.",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--project-dir",
        default=".",
        help="Target project directory (default: current directory).",
    )
    parser.add_argument(
        "--mode",
        choices=("auto", "docker", "local"),
        default="auto",
        help="Deployment mode. 'auto' prefers Docker when available.",
    )
    parser.add_argument(
        "--repo-url",
        default=DEFAULT_REPO_URL,
        help="Git repository URL to clone when project-dir is empty.",
    )
    parser.add_argument(
        "--branch",
        default="main",
        help="Git branch to check out on fresh clones (default: main).",
    )
    parser.add_argument(
        "--reuse-from",
        default=None,
        help="Path to an existing OpenBiliClaw checkout whose secrets (API keys + Bilibili cookie) should be copied into the new install.",
    )
    parser.add_argument(
        "--provider",
        choices=SUPPORTED_PROVIDERS,
        default=None,
        help="Override default LLM provider.",
    )
    parser.add_argument(
        "--llm-api-key",
        default=None,
        help="LLM API key for the (current or overridden) provider. Stored in config.toml.",
    )
    parser.add_argument(
        "--llm-base-url",
        default=None,
        help=(
            "Override the chosen provider's base_url. Required for OpenAI-"
            "compatible gateways (Azure / vLLM / LMStudio / OneAPI / 任意 "
            "OpenAI 兼容服务). The 'openai' provider is a protocol family, "
            "not a vendor — point it anywhere that speaks /v1/chat/completions."
        ),
    )
    parser.add_argument(
        "--llm-model",
        default=None,
        help="Override the chosen provider's chat/generation model.",
    )
    parser.add_argument(
        "--llm-preset",
        choices=(
            "kimi",
            "minimax",
            "qwen",
            "zhipu",
            "yi",
            "self-hosted",
            "relay",
            "azure",
            "custom",
        ),
        default=None,
        help=(
            "Shortcut for OpenAI-protocol-compatible services. Picks the "
            "preset's canonical Base URL + default model so AI-agent-driven "
            "installs don't have to remember each vendor's endpoint. "
            "Implies --provider=openai. --llm-base-url / --llm-model still "
            "override the preset on a per-field basis. Presets: "
            "kimi (Moonshot), minimax (M2.7), qwen (DashScope), zhipu (GLM), "
            "yi (零一万物), self-hosted (vLLM/LMStudio), relay (中转站/OneAPI), "
            "azure (Azure OpenAI), custom (no preset)."
        ),
    )
    parser.add_argument(
        "--embedding-provider",
        default=None,
        choices=("", *SUPPORTED_PROVIDERS),
        help=(
            "Embedding provider override. Empty string = disable embedding. "
            "Use 'ollama' for local bge-m3, or pick any "
            "supported provider for a dedicated embedding endpoint."
        ),
    )
    parser.add_argument(
        "--embedding-model",
        default=None,
        help="Embedding model name (e.g. bge-m3, text-embedding-3-small).",
    )
    parser.add_argument(
        "--embedding-base-url",
        default=None,
        help="Custom base_url for the embedding provider (writes into the matching [llm.<provider>] block).",
    )
    parser.add_argument(
        "--embedding-api-key",
        default=None,
        help="Custom API key for the embedding provider (writes into the matching [llm.<provider>] block).",
    )
    parser.add_argument(
        "--module-override",
        action="append",
        default=None,
        metavar="MODULE=PROVIDER:MODEL",
        help=(
            "Per-module LLM override. Repeatable. MODULE ∈ {soul, "
            "discovery, recommendation, evaluation}. Example: "
            "--module-override discovery=deepseek:deepseek-v4-flash"
        ),
    )
    parser.add_argument(
        "--bilibili-cookie",
        default=None,
        help="Bilibili cookie string. Stored in config.toml and data/bilibili_cookie.json.",
    )
    parser.add_argument(
        "--bilibili-favorite-limit",
        type=int,
        default=None,
        help=(
            "Max Bilibili favorite events imported by auto-init. "
            "Default is openbiliclaw init's built-in 300; 0 skips favorites."
        ),
    )
    parser.add_argument(
        "--bilibili-follow-limit",
        type=int,
        default=None,
        help=(
            "Max Bilibili follow events imported by auto-init. "
            "Default is openbiliclaw init's built-in 300; 0 skips follows."
        ),
    )
    xhs_group = parser.add_mutually_exclusive_group()
    xhs_group.add_argument(
        "--yes-xhs",
        action="store_true",
        help=(
            "Explicitly opt in to Xiaohongshu liked/favorite data during auto-init. "
            "AI agents should only pass this after asking the user."
        ),
    )
    xhs_group.add_argument(
        "--no-xhs",
        action="store_true",
        help=(
            "Explicitly skip Xiaohongshu liked/favorite data during auto-init. "
            "Use this when the user says no or has not opted in."
        ),
    )
    douyin_group = parser.add_mutually_exclusive_group()
    douyin_group.add_argument(
        "--yes-douyin",
        action="store_true",
        help=(
            "Explicitly opt in to Douyin post/favorite/like/follow data during auto-init. "
            "AI agents should only pass this after asking the user."
        ),
    )
    douyin_group.add_argument(
        "--no-douyin",
        action="store_true",
        help=(
            "Explicitly skip Douyin data during auto-init. Use this when the user says no "
            "or has not opted in."
        ),
    )
    youtube_group = parser.add_mutually_exclusive_group()
    youtube_group.add_argument(
        "--yes-youtube",
        action="store_true",
        help=(
            "Explicitly opt in to YouTube history/subscription/like data during auto-init. "
            "AI agents should only pass this after asking the user."
        ),
    )
    youtube_group.add_argument(
        "--no-youtube",
        action="store_true",
        help=(
            "Explicitly skip YouTube data during auto-init. Use this when the user says no "
            "or has not opted in."
        ),
    )
    parser.add_argument(
        "--skip-ollama-setup",
        action="store_true",
        help=(
            "When --provider=ollama or --embedding-provider=ollama is "
            "selected, the bootstrap will by default detect, install (via "
            "brew/winget/install.sh), start, and pull the requested model. "
            "Pass this flag to opt out — useful if you manage Ollama "
            "yourself (e.g. inside a container with a custom image)."
        ),
    )
    parser.add_argument(
        "--skip-start",
        action="store_true",
        help="Prepare config and dependencies but do not start the backend.",
    )
    parser.add_argument(
        "--skip-init",
        action="store_true",
        help="Do not run 'openbiliclaw init' after the backend is healthy.",
    )
    parser.add_argument(
        "--interactive-confirm",
        action="store_true",
        help="Ask required init confirmations from the terminal before auto-init.",
    )
    parser.add_argument(
        "--wait-for-extension-cookie",
        action="store_true",
        help="After backend health, wait for the browser extension to sync Bilibili cookie.",
    )
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="Assume dependencies are already installed (local mode only).",
    )
    parser.add_argument(
        "--skip-health-check",
        action="store_true",
        help="Do not poll /api/health after starting the backend.",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help="API host to bind on local mode (default: 0.0.0.0).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="API port (default: 8420).",
    )
    parser.add_argument(
        "--install-cmd",
        default=None,
        help="Override the dependency install command. Default: 'uv sync' when uv is available, otherwise 'pip install -e .'.",
    )
    parser.add_argument(
        "--python",
        default=None,
        help="Path to the Python interpreter to use for the virtual environment. Default: current interpreter.",
    )
    return parser


# ---------------------------------------------------------------------------
# Environment detection


def which(binary: str) -> str | None:
    """Return the absolute path to a binary or None if unavailable."""

    return shutil.which(binary)


def detect_docker() -> bool:
    """Return True when Docker + docker compose V2 are usable."""

    docker = which("docker")
    if docker is None:
        return False
    probe = run_capture([docker, "compose", "version"], check=False)
    return probe.returncode == 0


def detect_uv() -> bool:
    """Return True when `uv` is available on PATH."""

    return which("uv") is not None


# ---------------------------------------------------------------------------
# Ollama auto-install helpers
#
# When the user picks ollama as their LLM and/or embedding provider, the
# install isn't really "done" until ollama itself is installed, the daemon
# is up, and the requested models are pulled. Without these helpers the
# user lands in a "config is fine but nothing works because ollama is
# missing" state, which defeats the one-line install promise.

OLLAMA_HOST = "http://localhost:11434"


def detect_ollama() -> str | None:
    """Return the ollama binary path, or None when not installed."""

    return which("ollama")


def ollama_running(timeout: float = 2.0) -> bool:
    """Probe Ollama's HTTP API. True iff /api/version returns 200."""

    try:
        with urllib.request.urlopen(f"{OLLAMA_HOST}/api/version", timeout=timeout) as resp:
            return bool(resp.status == 200)
    except Exception:
        return False


def install_ollama() -> bool:
    """Install Ollama using the platform-native package manager.

    macOS: prefer brew (most devs have it), fall back to printing the
        download URL.
    Windows: prefer winget (ships on Win 10 1803+), fall back to URL.
    Linux: pipe the official install.sh — it auto-detects systemd and
        sets up the service. Needs sudo for the systemd unit; users
        without sudo will see install.sh's own error message.
    """

    if sys.platform == "darwin":
        if which("brew"):
            try:
                run_streaming(["brew", "install", "ollama"], check=False)
                return detect_ollama() is not None
            except RuntimeError:
                pass
        info(
            "Could not auto-install via brew. Download the macOS app from "
            "https://ollama.com/download and re-run the bootstrap."
        )
        return False

    if os.name == "nt":
        if which("winget"):
            try:
                run_streaming(
                    [
                        "winget",
                        "install",
                        "-e",
                        "--id",
                        "Ollama.Ollama",
                        "--accept-source-agreements",
                        "--accept-package-agreements",
                    ],
                    check=False,
                )
                # winget puts ollama under %LOCALAPPDATA%\Programs\Ollama —
                # may not be on PATH in this shell session yet.
                local_app = os.environ.get("LOCALAPPDATA", "")
                if local_app:
                    candidate = Path(local_app) / "Programs" / "Ollama" / "ollama.exe"
                    if candidate.exists():
                        return True
                return detect_ollama() is not None
            except RuntimeError:
                pass
        info(
            "Could not auto-install via winget. Download the Windows "
            "installer from https://ollama.com/download and re-run."
        )
        return False

    # Linux: piped curl | sh. install.sh handles systemd setup itself.
    try:
        result = subprocess.run(
            "curl -fsSL https://ollama.com/install.sh | sh",
            shell=True,
            check=False,
        )
        return result.returncode == 0 and detect_ollama() is not None
    except Exception:
        return False


def start_ollama_serve(wait_seconds: float = 15.0) -> bool:
    """Spawn `ollama serve` in the background; wait for /api/version 200.

    Returns False if the process couldn't be spawned, or if the daemon
    isn't responding within ``wait_seconds``.
    """

    if ollama_running():
        return True

    ollama = detect_ollama()
    if ollama is None:
        return False

    devnull = subprocess.DEVNULL
    if os.name == "nt":
        creationflags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
        )
        subprocess.Popen(  # noqa: S603 — known binary path from PATH
            [ollama, "serve"],
            creationflags=creationflags,
            stdout=devnull,
            stderr=devnull,
            stdin=devnull,
        )
    else:
        subprocess.Popen(  # noqa: S603 — known binary path from PATH
            [ollama, "serve"],
            start_new_session=True,
            stdout=devnull,
            stderr=devnull,
            stdin=devnull,
        )

    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if ollama_running():
            return True
        time.sleep(0.5)
    return False


def ollama_has_model(model: str) -> bool:
    """Return True when the named model is already pulled.

    Matches both the bare name (``bge-m3``) and the tagged form
    (``bge-m3:latest``) so users who pulled with an explicit tag still
    pass the check.
    """

    try:
        with urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return False
    for tag in data.get("models", []):
        name = str(tag.get("name", "")).strip()
        if name == model or name.startswith(f"{model}:"):
            return True
    return False


def ollama_pull(model: str) -> bool:
    """Pull a model via the ollama CLI (streams progress to stdout)."""

    ollama = detect_ollama()
    if ollama is None:
        return False
    try:
        run_streaming([ollama, "pull", model], check=False)
    except RuntimeError:
        return False
    return ollama_has_model(model)


def ensure_ollama_ready(models: list[str]) -> dict[str, Any]:
    """Detect → install → start → pull. Drives all four phases.

    Returns a structured summary so the bootstrap can emit one
    consolidated event. Each individual phase also emits its own event
    (ollama_installed / ollama_serving / ollama_model_pulled / *_failed)
    so an AI agent watching the JSON stream gets fine-grained progress.
    """

    summary: dict[str, Any] = {
        "binary_path": "",
        "installed_now": False,
        "started_now": False,
        "pulled": [],
        "failed_pulls": [],
        "running": False,
    }

    # Phase 1 — detect / install
    binary = detect_ollama()
    if binary is None:
        info(
            "Ollama not detected. Auto-installing now — this can take 1–3 "
            "minutes depending on your network."
        )
        if not install_ollama():
            emit(
                BootstrapResult(
                    "error",
                    "ollama_install_failed",
                    {
                        "platform": sys.platform,
                        "hint": (
                            "Install Ollama manually from "
                            "https://ollama.com/download then re-run the "
                            "bootstrap. The rest of your config is already "
                            "saved — re-running won't lose progress."
                        ),
                    },
                )
            )
            return summary
        binary = detect_ollama()
        summary["installed_now"] = True
        emit(BootstrapResult("ok", "ollama_installed", {"binary": binary or ""}))

    summary["binary_path"] = binary or ""

    # Phase 2 — start the daemon if not already up
    if not ollama_running():
        info("Starting 'ollama serve' in the background...")
        if not start_ollama_serve():
            emit(
                BootstrapResult(
                    "warning",
                    "ollama_serve_failed",
                    {
                        "hint": (
                            "Run 'ollama serve' manually in another terminal, "
                            "then re-run the bootstrap."
                        )
                    },
                )
            )
            return summary
        summary["started_now"] = True
        emit(BootstrapResult("ok", "ollama_serving", {"host": OLLAMA_HOST}))

    summary["running"] = ollama_running()

    # Phase 3 — pull the requested models
    for model in models:
        if not model:
            continue
        if ollama_has_model(model):
            info(f"Ollama model '{model}' already pulled.")
            summary["pulled"].append(model)
            continue
        info(f"Pulling Ollama model '{model}' (first time can take a few minutes)...")
        if ollama_pull(model):
            summary["pulled"].append(model)
            emit(BootstrapResult("ok", "ollama_model_pulled", {"model": model}))
        else:
            summary["failed_pulls"].append(model)
            emit(
                BootstrapResult(
                    "warning",
                    "ollama_pull_failed",
                    {
                        "model": model,
                        "hint": f"Run 'ollama pull {model}' manually and re-check.",
                    },
                )
            )

    return summary


# ---------------------------------------------------------------------------
# Subprocess helpers


@dataclass(frozen=True)
class CommandResult:
    """Immutable result of a subprocess run."""

    returncode: int
    stdout: str
    stderr: str


def run_capture(cmd: list[str], *, check: bool = True, cwd: Path | None = None) -> CommandResult:
    """Run a command and capture its output."""

    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        check=False,
    )
    result = CommandResult(
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {shlex.join(cmd)}\n{result.stderr}"
        )
    return result


def run_streaming(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> int:
    """Run a command, streaming stdout/stderr to the parent process."""

    info(f"$ {shlex.join(cmd)}")
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        check=False,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"Command failed ({proc.returncode}): {shlex.join(cmd)}")
    return proc.returncode


def _init_progress_event(line: str) -> dict[str, str] | None:
    """Return structured progress metadata for high-signal init output lines."""

    text = line.strip()
    if not text:
        return None

    for phase in ("1/4", "2/4", "3/4", "4/4"):
        if text.startswith(phase):
            return {"phase": phase, "kind": "phase", "line": text}

    progress_prefixes = (
        "· ",
        "✓ ",
        "补货阶段",
        "当前池子",
        "阶段完成",
        "初始化摘要",
    )
    if text.startswith(progress_prefixes):
        return {"phase": "", "kind": "progress", "line": text}

    return None


def run_init_streaming(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> int:
    """Run init while echoing output and emitting machine-readable progress."""

    info(f"$ {shlex.join(cmd)}")
    merged_env = dict(os.environ)
    if env:
        merged_env.update(env)
    merged_env.setdefault("PYTHONUNBUFFERED", "1")
    start = time.monotonic()
    proc = subprocess.Popen(  # noqa: S603 — command is built by this bootstrap script.
        cmd,
        cwd=str(cwd) if cwd else None,
        env=merged_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        print(line, end="")
        sys.stdout.flush()
        details = _init_progress_event(line)
        if details is None:
            continue
        emit(
            BootstrapResult(
                "progress",
                "init_progress",
                {
                    **details,
                    "elapsed_seconds": round(time.monotonic() - start, 1),
                },
            )
        )

    returncode = proc.wait()
    if check and returncode != 0:
        raise RuntimeError(f"Command failed ({returncode}): {shlex.join(cmd)}")
    return returncode


# ---------------------------------------------------------------------------
# Repository preparation


def ensure_repo_checkout(project_dir: Path, repo_url: str, branch: str) -> Path:
    """Ensure a working OpenBiliClaw checkout exists at project_dir.

    Rules:
    * If project_dir already contains pyproject.toml + config.example.toml, assume it's already a checkout.
    * Otherwise, clone the repo into project_dir.
    * Refuses to clone into a non-empty directory that does not already look like OpenBiliClaw.
    """

    project_dir = project_dir.expanduser().resolve()
    if (project_dir / "pyproject.toml").exists() and (project_dir / "config.example.toml").exists():
        info(f"Using existing OpenBiliClaw checkout at {project_dir}")
        return project_dir

    project_dir.mkdir(parents=True, exist_ok=True)
    entries = [entry for entry in project_dir.iterdir() if entry.name != ".DS_Store"]
    if entries:
        raise RuntimeError(
            f"Target directory is not empty and does not look like OpenBiliClaw: {project_dir}"
        )

    git = which("git")
    if git is None:
        raise RuntimeError("git is required to clone OpenBiliClaw but was not found on PATH.")

    info(f"Cloning {repo_url} (branch {branch}) into {project_dir}")
    run_streaming([git, "clone", "--branch", branch, "--depth", "1", repo_url, str(project_dir)])
    return project_dir


# ---------------------------------------------------------------------------
# Config + secret handling


def ensure_config_toml(project_dir: Path) -> Path:
    """Ensure config.toml exists, creating it from the example when missing."""

    config_path = project_dir / "config.toml"
    example_path = project_dir / "config.example.toml"
    if not example_path.exists():
        raise RuntimeError(f"config.example.toml not found in {project_dir}")

    if not config_path.exists():
        info(f"Creating {config_path} from config.example.toml")
        config_path.write_text(example_path.read_text(encoding="utf-8"), encoding="utf-8")
    return config_path


def read_simple_toml(path: Path) -> dict[str, Any]:
    """Read a TOML file using the stdlib tomllib."""

    import tomllib

    with path.open("rb") as handle:
        return tomllib.load(handle)


def set_toml_string_value(content: str, section: str, key: str, value: str) -> str:
    """Rewrite ``key = "..."`` under ``[section]`` with the new value.

    This is a minimal line-based editor; it preserves the rest of the file as
    much as possible so operators can keep their own comments. It does not
    handle multi-line strings or inline tables, which is fine because the
    OpenBiliClaw config template uses only single-line string values for the
    fields we need to update.
    """

    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    new_line = f'{key} = "{escaped}"'
    section_header = f"[{section}]"

    lines = content.splitlines()
    in_section = False
    updated = False
    for index, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_section = stripped == section_header
            continue
        if not in_section:
            continue
        if stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        lhs = stripped.split("=", 1)[0].strip()
        if lhs == key:
            indent = raw_line[: len(raw_line) - len(raw_line.lstrip())]
            lines[index] = f"{indent}{new_line}"
            updated = True
            break

    if not updated:
        # Append the section if missing
        append_lines = []
        if not content.endswith("\n"):
            append_lines.append("")
        append_lines.append(section_header)
        append_lines.append(new_line)
        return content + "\n".join(append_lines) + "\n"

    trailing_newline = "\n" if content.endswith("\n") else ""
    return "\n".join(lines) + trailing_newline


def update_config_secret(config_path: Path, section: str, key: str, value: str) -> None:
    """Patch a single secret value inside config.toml."""

    original = config_path.read_text(encoding="utf-8")
    updated = set_toml_string_value(original, section, key, value)
    if updated != original:
        config_path.write_text(updated, encoding="utf-8")


def clear_toml_string_value(content: str, section: str, key: str) -> tuple[str, bool]:
    """Reset ``key = "..."`` under ``[section]`` to empty (``key = ""``).

    Returns ``(new_content, did_change)``. We **set to empty** rather than
    deleting the line because the rest of the codebase reads config via
    Pydantic models that expect every field to exist. Setting to empty
    string lets defaults take over (e.g. ``base_url=""`` → OpenAI SDK
    uses its built-in ``https://api.openai.com/v1``).
    """

    new_line = f'{key} = ""'
    section_header = f"[{section}]"
    lines = content.splitlines()
    in_section = False
    changed = False
    for index, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_section = stripped == section_header
            continue
        if not in_section:
            continue
        if stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        lhs = stripped.split("=", 1)[0].strip()
        if lhs == key:
            indent = raw_line[: len(raw_line) - len(raw_line.lstrip())]
            new_full = f"{indent}{new_line}"
            if new_full != raw_line:
                lines[index] = new_full
                changed = True
            break
    trailing_newline = "\n" if content.endswith("\n") else ""
    return "\n".join(lines) + trailing_newline, changed


def clear_config_value(config_path: Path, section: str, key: str) -> bool:
    """Reset a config field to empty in-place. Returns True if it changed."""

    original = config_path.read_text(encoding="utf-8")
    updated, changed = clear_toml_string_value(original, section, key)
    if changed:
        config_path.write_text(updated, encoding="utf-8")
    return changed


def reuse_config_secrets(project_dir: Path, source_dir: Path) -> dict[str, Any]:
    """Copy API keys + Bilibili cookie from an existing OpenBiliClaw checkout."""

    source_dir = source_dir.expanduser().resolve()
    if not source_dir.exists():
        raise RuntimeError(f"--reuse-from path does not exist: {source_dir}")

    source_config = source_dir / "config.toml"
    summary: dict[str, Any] = {"reused": [], "skipped": [], "source": str(source_dir)}
    if not source_config.exists():
        summary["skipped"].append("config.toml missing in source")
    else:
        source_data = read_simple_toml(source_config)
        llm_section = source_data.get("llm", {})
        provider = llm_section.get("default_provider")
        if provider:
            update_config_secret(project_dir / "config.toml", "llm", "default_provider", provider)
            summary["reused"].append("llm.default_provider")

        for name in REMOTE_PROVIDERS:
            provider_cfg = llm_section.get(name, {})
            api_key = str(provider_cfg.get("api_key", "")).strip()
            if api_key:
                update_config_secret(project_dir / "config.toml", f"llm.{name}", "api_key", api_key)
                summary["reused"].append(f"llm.{name}.api_key")

        gemini_cfg = llm_section.get("gemini", {})
        gemini_model = str(gemini_cfg.get("model", "")).strip()
        if gemini_model:
            update_config_secret(project_dir / "config.toml", "llm.gemini", "model", gemini_model)
            summary["reused"].append("llm.gemini.model")

        bilibili_section = source_data.get("bilibili", {})
        cookie_value = str(bilibili_section.get("cookie", "")).strip()
        if cookie_value:
            update_config_secret(project_dir / "config.toml", "bilibili", "cookie", cookie_value)
            summary["reused"].append("bilibili.cookie")

    source_cookie_file = source_dir / "data" / "bilibili_cookie.json"
    if source_cookie_file.exists():
        data_dir = project_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        target_cookie = data_dir / "bilibili_cookie.json"
        target_cookie.write_text(source_cookie_file.read_text(encoding="utf-8"), encoding="utf-8")
        summary["reused"].append("data/bilibili_cookie.json")
    else:
        summary["skipped"].append("data/bilibili_cookie.json missing in source")

    return summary


def persist_cookie_file(project_dir: Path, cookie: str) -> None:
    """Persist the cookie string in the on-disk Bilibili cookie file."""

    data_dir = project_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    cookie_path = data_dir / "bilibili_cookie.json"
    cookie_path.write_text(json.dumps({"cookie": cookie}, ensure_ascii=False), encoding="utf-8")


def apply_provider_override(project_dir: Path, provider: str) -> None:
    update_config_secret(project_dir / "config.toml", "llm", "default_provider", provider)


def apply_llm_api_key(project_dir: Path, provider: str, api_key: str) -> None:
    update_config_secret(project_dir / "config.toml", f"llm.{provider}", "api_key", api_key)


def apply_llm_base_url(project_dir: Path, provider: str, base_url: str) -> None:
    update_config_secret(project_dir / "config.toml", f"llm.{provider}", "base_url", base_url)


def apply_llm_model(project_dir: Path, provider: str, model: str) -> None:
    update_config_secret(project_dir / "config.toml", f"llm.{provider}", "model", model)


def apply_embedding_config(
    project_dir: Path,
    *,
    provider: str | None,
    model: str | None,
    base_url: str | None,
    api_key: str | None,
) -> dict[str, Any]:
    """Write [llm.embedding] + (optionally) provider creds.

    Returns a structured summary so the bootstrap can emit a single event
    listing exactly what was changed. Empty-string provider means
    "embedding disabled"; missing fields are left untouched.
    """

    config_path = project_dir / "config.toml"
    written: list[str] = []
    if provider is not None:
        update_config_secret(config_path, "llm.embedding", "provider", provider)
        written.append("llm.embedding.provider")
    if model is not None:
        update_config_secret(config_path, "llm.embedding", "model", model)
        written.append("llm.embedding.model")

    target_provider = (provider or "").strip()
    if base_url is not None:
        update_config_secret(config_path, "llm.embedding", "base_url", base_url)
        written.append("llm.embedding.base_url")
    if api_key is not None:
        update_config_secret(config_path, "llm.embedding", "api_key", api_key)
        written.append("llm.embedding.api_key")

    if target_provider == "ollama" and base_url is None:
        existing = read_simple_toml(config_path).get("llm", {}).get("embedding", {})
        if not str(existing.get("base_url", "")).strip():
            update_config_secret(
                config_path,
                "llm.embedding",
                "base_url",
                "http://localhost:11434/v1",
            )
            written.append("llm.embedding.base_url(seeded)")

    return {"written": written, "provider": target_provider}


def parse_module_override(spec: str) -> tuple[str, str, str]:
    """Parse --module-override values shaped like ``module=provider:model``.

    ``provider`` may be empty (= keep global), and ``model`` may be empty
    (= keep provider default). Raises ValueError on malformed input so
    argparse-style failures bubble up cleanly.
    """

    if "=" not in spec:
        raise ValueError(f"--module-override requires MODULE=PROVIDER:MODEL form, got: {spec!r}")
    module, _, rhs = spec.partition("=")
    module = module.strip().lower()
    if module not in {"soul", "discovery", "recommendation", "evaluation"}:
        raise ValueError(
            f"unknown module {module!r}; expected one of soul / discovery / recommendation / evaluation"
        )
    if ":" in rhs:
        provider, _, model = rhs.partition(":")
    else:
        provider, model = rhs, ""
    provider = provider.strip().lower()
    if provider and provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"unknown provider {provider!r} in --module-override {spec!r}")
    return module, provider, model.strip()


def apply_module_overrides(project_dir: Path, specs: list[str]) -> dict[str, Any]:
    """Write each --module-override into config.toml under [llm.<module>]."""

    config_path = project_dir / "config.toml"
    written: list[str] = []
    for spec in specs:
        module, provider, model = parse_module_override(spec)
        update_config_secret(config_path, f"llm.{module}", "provider", provider)
        update_config_secret(config_path, f"llm.{module}", "model", model)
        written.append(f"llm.{module}={provider or 'default'}:{model or 'default'}")
    return {"modules": written}


def detect_missing_secrets(project_dir: Path) -> dict[str, Any]:
    """Return a structured summary of missing secrets in config.toml."""

    config_path = project_dir / "config.toml"
    data = read_simple_toml(config_path)
    llm_section = data.get("llm", {})
    provider = str(llm_section.get("default_provider", "") or "").strip() or "openai"

    provider_cfg = llm_section.get(provider, {})
    api_key = str(provider_cfg.get("api_key", "") or "").strip()
    bilibili_section = data.get("bilibili", {})
    cookie_inline = str(bilibili_section.get("cookie", "") or "").strip()
    cookie_file = project_dir / "data" / "bilibili_cookie.json"
    cookie_on_disk = False
    if cookie_file.exists():
        try:
            cookie_data = json.loads(cookie_file.read_text(encoding="utf-8"))
            cookie_on_disk = bool(str(cookie_data.get("cookie", "")).strip())
        except json.JSONDecodeError:
            cookie_on_disk = False

    missing: list[str] = []
    if provider in REMOTE_PROVIDERS and not api_key:
        missing.append(f"llm.{provider}.api_key")
    if not (cookie_inline or cookie_on_disk):
        missing.append("bilibili.cookie")

    return {
        "provider": provider,
        "missing": missing,
        "has_cookie_inline": bool(cookie_inline),
        "has_cookie_file": cookie_on_disk,
    }


def _embedding_choice_from_config(project_dir: Path) -> dict[str, Any]:
    data = read_simple_toml(project_dir / "config.toml")
    raw = data.get("llm", {}).get("embedding", {})
    provider = str(raw.get("provider", "") or "").strip()
    model = str(raw.get("model", "") or "").strip()
    if provider or model:
        return {
            "source": "config",
            "provider": provider,
            "model": model,
            "explicit": True,
        }
    return {
        "source": "missing",
        "provider": provider,
        "model": model,
        "explicit": False,
    }


def detect_init_decisions(
    project_dir: Path,
    args: argparse.Namespace,
    *,
    embedding_touched: bool,
) -> dict[str, Any]:
    """Return user decisions required before non-interactive auto-init.

    ``agent_bootstrap.py`` never prompts. If the AI agent did not pass an
    explicit embedding choice or source opt-in/out, auto-init must pause
    and surface those decisions instead of silently choosing for the user.
    """

    missing: list[str] = []
    if embedding_touched:
        embedding = {
            "source": "flags",
            "provider": (args.embedding_provider or "").strip(),
            "model": (args.embedding_model or "").strip(),
            "explicit": True,
        }
    else:
        embedding = _embedding_choice_from_config(project_dir)
        if not embedding["explicit"]:
            missing.append("embedding")

    if args.yes_xhs:
        xhs = {
            "policy": "enabled",
            "flag": "--yes-xhs",
            "explicit": True,
            "source": "flag",
        }
    elif args.no_xhs or os.environ.get("OPENBILICLAW_NO_XHS", "").strip() == "1":
        xhs = {
            "policy": "disabled",
            "flag": "--no-xhs",
            "explicit": True,
            "source": "env" if not args.no_xhs else "flag",
        }
    else:
        missing.append("xhs")
        xhs = {
            "policy": "pending",
            "flag": "",
            "explicit": False,
            "source": "missing",
        }

    if args.yes_douyin:
        douyin = {
            "policy": "enabled",
            "flag": "--yes-douyin",
            "explicit": True,
            "source": "flag",
        }
    elif args.no_douyin or os.environ.get("OPENBILICLAW_NO_DOUYIN", "").strip() == "1":
        douyin = {
            "policy": "disabled",
            "flag": "--no-douyin",
            "explicit": True,
            "source": "env" if not args.no_douyin else "flag",
        }
    else:
        missing.append("douyin")
        douyin = {
            "policy": "pending",
            "flag": "",
            "explicit": False,
            "source": "missing",
        }

    if args.yes_youtube:
        youtube = {
            "policy": "enabled",
            "flag": "--yes-youtube",
            "explicit": True,
            "source": "flag",
        }
    elif args.no_youtube or os.environ.get("OPENBILICLAW_NO_YOUTUBE", "").strip() == "1":
        youtube = {
            "policy": "disabled",
            "flag": "--no-youtube",
            "explicit": True,
            "source": "env" if not args.no_youtube else "flag",
        }
    else:
        missing.append("youtube")
        youtube = {
            "policy": "pending",
            "flag": "",
            "explicit": False,
            "source": "missing",
        }

    return {
        "missing": missing,
        "embedding": embedding,
        "xhs": xhs,
        "douyin": douyin,
        "youtube": youtube,
    }


def build_init_command(
    mode: str,
    project_dir: Path,
    xhs_flag: str,
    douyin_flag: str,
    youtube_flag: str,
    *,
    bilibili_favorite_limit: int | None = None,
    bilibili_follow_limit: int | None = None,
) -> list[str]:
    """Build the non-interactive init command used after bootstrap health checks."""

    if mode == "docker":
        init_cmd = [
            "docker",
            "exec",
            "-i",
            "openbiliclaw-backend",
            "openbiliclaw",
            "init",
        ]
    elif detect_uv():
        init_cmd = ["uv", "run", "openbiliclaw", "init"]
    else:
        if os.name == "nt":
            venv_python = project_dir / ".venv" / "Scripts" / "python.exe"
        else:
            venv_python = project_dir / ".venv" / "bin" / "python"
        if venv_python.exists():
            init_cmd = [str(venv_python), "-m", "openbiliclaw.cli", "init"]
        else:
            init_cmd = [sys.executable, "-m", "openbiliclaw.cli", "init"]

    if xhs_flag:
        init_cmd.append(xhs_flag)
    if douyin_flag:
        init_cmd.append(douyin_flag)
    if youtube_flag:
        init_cmd.append(youtube_flag)
    if bilibili_favorite_limit is not None:
        init_cmd.extend(
            [
                "--bilibili-favorite-limit",
                str(max(0, int(bilibili_favorite_limit))),
            ]
        )
    if bilibili_follow_limit is not None:
        init_cmd.extend(
            [
                "--bilibili-follow-limit",
                str(max(0, int(bilibili_follow_limit))),
            ]
        )
    return init_cmd


# ---------------------------------------------------------------------------
# Local deployment


def local_install(project_dir: Path, install_cmd: str | None, python_override: str | None) -> None:
    """Install python dependencies using uv (preferred) or pip."""

    if install_cmd:
        run_streaming(shlex.split(install_cmd), cwd=project_dir)
        return

    if detect_uv():
        run_streaming(["uv", "sync"], cwd=project_dir)
        return

    venv_python = python_override or sys.executable
    venv_dir = project_dir / ".venv"
    if not venv_dir.exists():
        run_streaming([venv_python, "-m", "venv", str(venv_dir)])
    pip = venv_dir / ("Scripts/pip.exe" if os.name == "nt" else "bin/pip")
    run_streaming([str(pip), "install", "-e", ".[dev]"], cwd=project_dir)


def local_serve_command(project_dir: Path, host: str, port: int) -> list[str]:
    """Return the command used to start the API server in local mode."""

    if detect_uv():
        return ["uv", "run", "openbiliclaw", "serve-api", "--host", host, "--port", str(port)]

    venv_bin = project_dir / (".venv/Scripts" if os.name == "nt" else ".venv/bin")
    openbiliclaw = venv_bin / "openbiliclaw"
    if openbiliclaw.exists():
        return [str(openbiliclaw), "serve-api", "--host", host, "--port", str(port)]

    python = venv_bin / ("python.exe" if os.name == "nt" else "python")
    return [str(python), "-m", "openbiliclaw.cli", "serve-api", "--host", host, "--port", str(port)]


def _connect_host_for_bind_host(host: str) -> str:
    """Return a concrete local address for checks against a bind address."""
    value = str(host or "").strip().lower()
    if value in {"", "0.0.0.0", "::", "[::]"}:
        return "127.0.0.1"
    return str(host).strip()


def _health_url(host: str, port: int) -> str:
    return f"http://{_connect_host_for_bind_host(host)}:{port}{DEFAULT_HEALTH_PATH}"


def _probe_port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    """Return True if a TCP listener answers on host:port."""
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            return sock.connect_ex((host, port)) == 0
    except OSError:
        return False


def _probe_is_openbiliclaw(host: str, port: int) -> bool:
    """Confirm the listener on host:port responds to /api/health as OpenBiliClaw."""
    url = _health_url(host, port)
    try:
        with urllib.request.urlopen(url, timeout=2.0) as response:  # noqa: S310
            if not (200 <= response.status < 300):
                return False
            body = response.read().decode("utf-8", errors="replace")
            return "openbiliclaw" in body.lower()
    except Exception:
        return False


def _find_pids_on_port(port: int) -> list[int]:
    """Return PIDs of TCP listeners on the given port.

    On macOS/Linux/WSL2: uses ``lsof -tiTCP:<port> -sTCP:LISTEN``.
    On native Windows: parses ``netstat -ano`` for LISTEN entries on
    the port (lsof is not part of Windows). Returns ``[]`` when no
    suitable tool is available — callers fall back to socket-only
    detection.
    """
    if os.name == "nt":
        netstat = which("netstat")
        if netstat is None:
            return []
        try:
            proc = subprocess.run(
                [netstat, "-ano", "-p", "tcp"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (subprocess.TimeoutExpired, OSError):
            return []
        pids: list[int] = []
        # Format on Windows: "Proto  Local Address  Foreign Address  State  PID"
        # e.g.  "  TCP    127.0.0.1:8420         0.0.0.0:0    LISTENING       12345"
        for line in proc.stdout.splitlines():
            tokens = line.split()
            if len(tokens) < 5:
                continue
            if tokens[0].upper() != "TCP":
                continue
            local = tokens[1]
            state = tokens[3] if len(tokens) >= 4 else ""
            if not local.endswith(f":{port}"):
                continue
            if state.upper() != "LISTENING":
                continue
            try:
                pids.append(int(tokens[-1]))
            except ValueError:
                continue
        return pids

    lsof = which("lsof")
    if lsof is None:
        return []
    try:
        proc = subprocess.run(
            [lsof, "-tiTCP:" + str(port), "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    pids = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pids.append(int(line))
        except ValueError:
            continue
    return pids


def _stop_existing_obc_backend(host: str, port: int) -> bool:
    """Try to gracefully stop any OpenBiliClaw backend already on host:port.

    Returns True if the port is free after the stop attempt; False if a
    non-OpenBiliClaw service still holds the port (caller should abort).
    """
    if not _probe_port_open(host, port):
        return True

    if not _probe_is_openbiliclaw(host, port):
        return False

    pids = _find_pids_on_port(port)
    if not pids:
        info(f"port {port} answers as OpenBiliClaw but no PIDs visible via lsof — proceeding")
        return True

    info(f"existing OpenBiliClaw backend on port {port}: pids={pids} — stopping to replace")
    _terminate_pids(pids, force=False)

    # Wait for the port to actually free up
    for _ in range(20):
        if not _probe_port_open(host, port, timeout=0.2):
            return True
        time.sleep(0.3)

    # Last resort: force-kill stragglers
    _terminate_pids(pids, force=True)
    time.sleep(0.5)
    return not _probe_port_open(host, port, timeout=0.2)


def _terminate_pids(pids: list[int], *, force: bool) -> None:
    """Stop the listed PIDs cross-platform.

    On Unix, send SIGTERM (or SIGKILL when ``force`` is True). On
    Windows, where ``os.kill`` semantics differ and SIGTERM doesn't
    map cleanly, shell out to ``taskkill`` (``/T`` walks the process
    tree, ``/F`` is force).
    """
    if os.name == "nt":
        taskkill = which("taskkill") or "taskkill"
        for pid in pids:
            args = [taskkill, "/PID", str(pid), "/T"]
            if force:
                args.append("/F")
            try:
                subprocess.run(args, capture_output=True, timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                continue
        return

    sig = 9 if force else 15
    for pid in pids:
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            continue
        except PermissionError:
            info(f"  cannot signal pid {pid} (permission)")


def start_local_backend(project_dir: Path, host: str, port: int) -> subprocess.Popen[bytes]:
    """Start the local FastAPI backend as a detached subprocess.

    If something is already on the port:
      - if it's an OpenBiliClaw backend (likely a previous install's
        process), stop it and replace
      - if it's something else, raise so the caller surfaces a clear error
    """
    connect_host = _connect_host_for_bind_host(host)
    if _probe_port_open(connect_host, port):
        freed = _stop_existing_obc_backend(connect_host, port)
        if not freed:
            raise RuntimeError(
                f"port {port} on {connect_host} is in use by a non-OpenBiliClaw service. "
                f"Stop that service or set PORT=<free port> and retry."
            )

    cmd = local_serve_command(project_dir, host, port)
    log_dir = project_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = (log_dir / "agent-bootstrap.log").open("ab")
    info(f"Starting local backend: {shlex.join(cmd)} (logs -> {log_dir / 'agent-bootstrap.log'})")
    # Detach the backend so the installer can exit cleanly. The two
    # platforms need different mechanisms: POSIX ``start_new_session``
    # vs Windows ``creationflags=DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP``
    # (0x00000008 | 0x00000200).
    if os.name == "nt":
        return subprocess.Popen(
            cmd,
            cwd=str(project_dir),
            stdout=log_file,
            stderr=log_file,
            creationflags=0x00000008 | 0x00000200,
        )
    return subprocess.Popen(
        cmd,
        cwd=str(project_dir),
        stdout=log_file,
        stderr=log_file,
        start_new_session=True,
    )


# ---------------------------------------------------------------------------
# Docker deployment


def docker_compose_up(project_dir: Path) -> None:
    docker = which("docker")
    if docker is None:
        raise RuntimeError("docker is not available on PATH")
    run_streaming([docker, "compose", "up", "-d", "--build"], cwd=project_dir)


def build_docker_runtime_sync_commands(project_dir: Path) -> list[list[str]]:
    """Return docker commands that copy confirmed host config into runtime volume."""

    commands = [
        [
            "docker",
            "exec",
            DOCKER_CONTAINER_NAME,
            "mkdir",
            "-p",
            f"{DOCKER_RUNTIME_ROOT}/data",
        ],
        [
            "docker",
            "cp",
            str(project_dir / "config.toml"),
            f"{DOCKER_CONTAINER_NAME}:{DOCKER_RUNTIME_ROOT}/config.toml",
        ],
    ]
    cookie_file = project_dir / "data" / "bilibili_cookie.json"
    if cookie_file.exists():
        commands.append(
            [
                "docker",
                "cp",
                str(cookie_file),
                f"{DOCKER_CONTAINER_NAME}:{DOCKER_RUNTIME_ROOT}/data/bilibili_cookie.json",
            ]
        )
    return commands


def sync_docker_runtime_config(project_dir: Path) -> None:
    """Copy bootstrap-written config into the running Docker runtime volume."""

    for command in build_docker_runtime_sync_commands(project_dir):
        run_streaming(command, cwd=project_dir)


def build_docker_missing_secrets_command() -> list[str]:
    """Return command that inspects secrets inside the backend container."""

    script = r"""
import json
import tomllib
from pathlib import Path

config_path = Path("/app/runtime/config.toml")
cookie_path = Path("/app/runtime/data/bilibili_cookie.json")
data = tomllib.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
llm = data.get("llm", {})
provider = str(llm.get("default_provider", "") or "").strip() or "openai"
remote = {"openai", "claude", "gemini", "deepseek", "openrouter"}
provider_cfg = llm.get(provider, {})
api_key = str(provider_cfg.get("api_key", "") or "").strip()
bilibili = data.get("bilibili", {})
cookie_inline = str(bilibili.get("cookie", "") or "").strip()
cookie_on_disk = False
if cookie_path.exists():
    try:
        cookie_on_disk = bool(str(json.loads(cookie_path.read_text(encoding="utf-8")).get("cookie", "")).strip())
    except json.JSONDecodeError:
        cookie_on_disk = False
missing = []
if provider in remote and not api_key:
    missing.append(f"llm.{provider}.api_key")
if not (cookie_inline or cookie_on_disk):
    missing.append("bilibili.cookie")
print(json.dumps({
    "provider": provider,
    "missing": missing,
    "has_cookie_inline": bool(cookie_inline),
    "has_cookie_file": cookie_on_disk,
}))
""".strip()
    return ["docker", "exec", DOCKER_CONTAINER_NAME, "python", "-c", script]


def detect_docker_missing_secrets(_project_dir: Path) -> dict[str, Any]:
    """Return missing secrets from the running Docker runtime config."""

    proc = subprocess.run(
        build_docker_missing_secrets_command(),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "docker secret detection failed")
    return json.loads(proc.stdout)


# ---------------------------------------------------------------------------
# Health check


def wait_for_health(host: str, port: int, timeout: float = HEALTH_TIMEOUT_SECONDS) -> bool:
    """Poll /api/health until it returns 200 or timeout expires."""

    url = _health_url(host, port)
    deadline = time.monotonic() + timeout
    last_error: str | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=HEALTH_POLL_INTERVAL) as response:  # noqa: S310
                if 200 <= response.status < 300:
                    return True
                last_error = f"status={response.status}"
        except urllib.error.URLError as exc:
            last_error = str(exc)
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
        time.sleep(HEALTH_POLL_INTERVAL)
    info(f"health check timed out: {last_error}")
    return False


def wait_for_cookie_sync(
    project_dir: Path,
    *,
    timeout_seconds: float = 300.0,
    interval_seconds: float = 2.0,
    detector: Callable[[Path], dict[str, Any]] = detect_missing_secrets,
) -> bool:
    """Wait until Bilibili cookie arrives via extension sync."""

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() <= deadline:
        missing = detector(project_dir).get("missing", [])
        if "bilibili.cookie" not in missing:
            return True
        time.sleep(interval_seconds)
    return False


# ---------------------------------------------------------------------------
# Orchestration


def run(args: argparse.Namespace) -> int:
    ensure_local_no_proxy()
    project_dir = Path(args.project_dir)
    try:
        project_dir = ensure_repo_checkout(project_dir, args.repo_url, args.branch)
    except RuntimeError as exc:
        emit(BootstrapResult("error", str(exc), {"step": "clone"}))
        return 2

    emit(BootstrapResult("ok", "repo_ready", {"project_dir": str(project_dir)}))

    try:
        ensure_config_toml(project_dir)
    except RuntimeError as exc:
        emit(BootstrapResult("error", str(exc), {"step": "config"}))
        return 2

    if args.reuse_from:
        try:
            reuse_summary = reuse_config_secrets(project_dir, Path(args.reuse_from))
        except RuntimeError as exc:
            emit(BootstrapResult("error", str(exc), {"step": "reuse"}))
            return 2
        emit(BootstrapResult("ok", "secrets_reused", reuse_summary))

    if args.interactive_confirm:
        try:
            answers = collect_interactive_confirmations()
        except RuntimeError as exc:
            emit(BootstrapResult("error", str(exc), {"step": "interactive_confirm"}))
            return 2
        apply_confirmation_answers_to_args(args, answers)
        emit(
            BootstrapResult(
                "ok",
                "init_confirmations_set",
                {
                    "embedding_provider": args.embedding_provider,
                    "embedding_model": args.embedding_model,
                    "xhs": "yes" if args.yes_xhs else "no",
                    "douyin": "yes" if args.yes_douyin else "no",
                    "youtube": "yes" if args.yes_youtube else "no",
                    "cookie_mode": answers.cookie_mode,
                },
            )
        )

    if args.provider:
        apply_provider_override(project_dir, args.provider)
        emit(BootstrapResult("ok", "provider_set", {"provider": args.provider}))

    current_provider = detect_missing_secrets(project_dir)["provider"]
    if args.llm_api_key:
        provider = args.provider or current_provider
        apply_llm_api_key(project_dir, provider, args.llm_api_key)
        emit(BootstrapResult("ok", "api_key_set", {"provider": provider}))

    if args.llm_base_url is not None:
        provider = args.provider or current_provider
        apply_llm_base_url(project_dir, provider, args.llm_base_url)
        emit(
            BootstrapResult(
                "ok",
                "base_url_set",
                {"provider": provider, "base_url": args.llm_base_url},
            )
        )
    elif args.provider == "openai":
        # User picked OpenAI 官方 without a custom base_url. If a previous
        # run wrote a gateway URL into [llm.openai] base_url, it would
        # silently keep routing to that gateway.
        # Reset the field to "" so the OpenAI SDK falls back to its
        # built-in https://api.openai.com/v1.
        if clear_config_value(project_dir / "config.toml", "llm.openai", "base_url"):
            emit(
                BootstrapResult(
                    "ok",
                    "base_url_reset",
                    {
                        "provider": "openai",
                        "reason": (
                            "--provider openai given without --llm-base-url; "
                            "cleared stale value so SDK uses official OpenAI endpoint"
                        ),
                    },
                )
            )

    if args.llm_model is not None:
        provider = args.provider or current_provider
        apply_llm_model(project_dir, provider, args.llm_model)
        emit(
            BootstrapResult(
                "ok",
                "model_set",
                {"provider": provider, "model": args.llm_model},
            )
        )

    embedding_touched = (
        args.embedding_provider is not None
        or args.embedding_model is not None
        or args.embedding_base_url is not None
        or args.embedding_api_key is not None
    )
    if embedding_touched:
        summary = apply_embedding_config(
            project_dir,
            provider=args.embedding_provider,
            model=args.embedding_model,
            base_url=args.embedding_base_url,
            api_key=args.embedding_api_key,
        )
        emit(BootstrapResult("ok", "embedding_set", summary))

    auto_embedding_to_ollama = False

    if args.module_override:
        try:
            mod_summary = apply_module_overrides(project_dir, args.module_override)
        except ValueError as exc:
            emit(BootstrapResult("error", str(exc), {"step": "module_override"}))
            return 2
        emit(BootstrapResult("ok", "module_overrides_set", mod_summary))

    if args.bilibili_cookie:
        update_config_secret(
            project_dir / "config.toml", "bilibili", "cookie", args.bilibili_cookie
        )
        persist_cookie_file(project_dir, args.bilibili_cookie)
        emit(BootstrapResult("ok", "cookie_set", {}))

    status = detect_missing_secrets(project_dir)
    emit(BootstrapResult("ok", "config_summary", status))

    mode = args.mode
    if mode == "auto":
        mode = "docker" if detect_docker() else "local"
    emit(BootstrapResult("ok", "mode_selected", {"mode": mode}))

    # When the user picks ollama for either LLM or embedding, the install
    # isn't really "done" until ollama is installed, the daemon is running,
    # and the requested models are pulled. Without this step the user
    # would land in a "config saved but nothing works" state.
    #
    # Inside Docker we deliberately skip this — the container talks to
    # the *host's* ollama at host.docker.internal, and managing a host
    # service from inside a container would be wrong.
    ollama_models_needed: list[str] = []
    if (args.provider or status["provider"]) == "ollama":
        ollama_models_needed.append((args.llm_model or "llama3").strip())
    if (args.embedding_provider or "").strip() == "ollama":
        ollama_models_needed.append((args.embedding_model or "bge-m3").strip())
    # When we auto-wired Ollama for embedding (Claude / DeepSeek /
    # OpenRouter primary path), make sure bge-m3 is pulled so the
    # embedding service has a working backend at first run.
    if auto_embedding_to_ollama:
        ollama_models_needed.append("bge-m3")
    ollama_models_needed = [m for m in ollama_models_needed if m]
    # Dedupe while preserving order (chat model first, then embedding).
    deduped: list[str] = []
    for model_name in ollama_models_needed:
        if model_name not in deduped:
            deduped.append(model_name)
    ollama_models_needed = deduped
    if ollama_models_needed and not args.skip_ollama_setup and mode != "docker":
        ollama_summary = ensure_ollama_ready(ollama_models_needed)
        emit(BootstrapResult("ok", "ollama_ready", ollama_summary))

    if not args.skip_install and mode == "local":
        try:
            local_install(project_dir, args.install_cmd, args.python)
        except RuntimeError as exc:
            emit(BootstrapResult("error", str(exc), {"step": "install"}))
            return 3
        emit(BootstrapResult("ok", "dependencies_installed", {}))

    if args.skip_start:
        remaining = detect_missing_secrets(project_dir)
        skipped_label = "complete" if not remaining["missing"] else "needs_secrets"
        init_decisions = detect_init_decisions(
            project_dir,
            args,
            embedding_touched=embedding_touched or auto_embedding_to_ollama,
        )
        emit(
            BootstrapResult(
                skipped_label,
                "skipped_start",
                {**remaining, "init_decisions": init_decisions},
            )
        )
        return 0

    if mode == "docker":
        try:
            docker_compose_up(project_dir)
        except RuntimeError as exc:
            emit(BootstrapResult("error", str(exc), {"step": "docker_up"}))
            return 4
        emit(BootstrapResult("ok", "docker_started", {}))
    else:
        try:
            start_local_backend(project_dir, args.host, args.port)
        except RuntimeError as exc:
            emit(BootstrapResult("error", str(exc), {"step": "local_start"}))
            return 5
        emit(BootstrapResult("ok", "local_started", {"host": args.host, "port": args.port}))

    if args.skip_health_check:
        final_status = detect_missing_secrets(project_dir)
        final_label = "complete" if not final_status["missing"] else "needs_secrets"
        init_decisions = detect_init_decisions(
            project_dir,
            args,
            embedding_touched=embedding_touched or auto_embedding_to_ollama,
        )
        emit(
            BootstrapResult(
                final_label,
                "health_check_skipped",
                {**final_status, "init_decisions": init_decisions},
            )
        )
        return 0

    healthy = wait_for_health(args.host, args.port)
    if healthy:
        status_detector: Callable[[Path], dict[str, Any]] = detect_missing_secrets
        if mode == "docker":
            try:
                sync_docker_runtime_config(project_dir)
            except RuntimeError as exc:
                emit(BootstrapResult("error", str(exc), {"step": "docker_config_sync"}))
                return 4
            status_detector = detect_docker_missing_secrets

        final_status = status_detector(project_dir)
        if args.wait_for_extension_cookie and final_status["missing"] == ["bilibili.cookie"]:
            emit(
                BootstrapResult(
                    "progress",
                    "waiting_for_extension_cookie",
                    {
                        "timeout_seconds": 300,
                        "hint": "Install the browser extension and log in to bilibili.com.",
                    },
                )
            )
            if wait_for_cookie_sync(project_dir, detector=status_detector):
                final_status = status_detector(project_dir)
                emit(BootstrapResult("ok", "extension_cookie_synced", final_status))
            else:
                emit(
                    BootstrapResult(
                        "needs_secrets",
                        "extension_cookie_wait_timeout",
                        final_status,
                    )
                )

        init_decisions = detect_init_decisions(
            project_dir,
            args,
            embedding_touched=embedding_touched or auto_embedding_to_ollama,
        )
        label = "complete" if not final_status["missing"] else "running_with_missing_secrets"
        if not final_status["missing"] and init_decisions["missing"] and not args.skip_init:
            label = "needs_decisions"
        health_details = {
            "health_url": _health_url(args.host, args.port),
            **final_status,
            "init_decisions": init_decisions,
        }
        emit(
            BootstrapResult(
                label,
                "backend_healthy",
                health_details,
            )
        )

        # Auto-run init when all credentials are present and --skip-init is
        # not set. The user finished giving us their credentials — they
        # expect the system to be in a usable state when we hand control
        # back. init = pull history + generate soul profile + first
        # discovery pass. Without it the user opens the extension and
        # sees nothing.
        if not final_status["missing"] and not args.skip_init:
            if init_decisions["missing"]:
                emit(
                    BootstrapResult(
                        "needs_decisions",
                        "init_decisions_required",
                        health_details,
                    )
                )
                info(
                    "Credentials are present, but init was not run because "
                    "the agent has not supplied explicit choices for: "
                    + ", ".join(init_decisions["missing"])
                )
                return 0

            info(
                "All credentials present — running 'openbiliclaw init' to reach usable state... "
                "(this takes 2-5 minutes: real LLM calls + Bilibili history fetches)"
            )
            try:
                xhs_flag = str(init_decisions["xhs"]["flag"])
                douyin_flag = str(init_decisions["douyin"]["flag"])
                youtube_flag = str(init_decisions["youtube"]["flag"])
                init_cmd = build_init_command(
                    mode,
                    project_dir,
                    xhs_flag,
                    douyin_flag,
                    youtube_flag,
                    bilibili_favorite_limit=args.bilibili_favorite_limit,
                    bilibili_follow_limit=args.bilibili_follow_limit,
                )
                init_returncode = run_init_streaming(init_cmd, cwd=project_dir, check=False)
                if init_returncode != 0:
                    emit(
                        BootstrapResult(
                            "warning",
                            "init_failed",
                            {
                                "returncode": init_returncode,
                                "init_command": shlex.join(init_cmd),
                            },
                        )
                    )
                    info(
                        "Init exited with a non-zero status, but the backend is running. "
                        "You can run 'openbiliclaw init' manually later "
                        "(or 'docker exec -it openbiliclaw-backend openbiliclaw init' for Docker)."
                    )
                    return 0
                emit(
                    BootstrapResult(
                        "complete",
                        "init_complete",
                        {**health_details, "init_command": shlex.join(init_cmd)},
                    )
                )
            except Exception as exc:
                emit(BootstrapResult("warning", "init_failed", {"error": str(exc)}))
                info(
                    f"Init failed ({exc}), but the backend is running. "
                    "You can run 'openbiliclaw init' manually later "
                    "(or 'docker exec -it openbiliclaw-backend openbiliclaw init' for Docker)."
                )

        return 0

    final_status = detect_missing_secrets(project_dir)
    init_decisions = detect_init_decisions(
        project_dir,
        args,
        embedding_touched=embedding_touched or auto_embedding_to_ollama,
    )
    emit(
        BootstrapResult(
            "error",
            "health_check_failed",
            {
                "health_url": _health_url(args.host, args.port),
                **final_status,
                "init_decisions": init_decisions,
            },
        )
    )
    return 5


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    # Resolve --llm-preset before run() so the rest of the pipeline
    # sees concrete provider/base_url/model values. The preset implies
    # provider=openai but never overrides explicit user-provided
    # --llm-base-url / --llm-model.
    if getattr(args, "llm_preset", None):
        preset = LLM_PRESETS.get(args.llm_preset, {})
        if not args.provider:
            args.provider = "openai"
        elif args.provider != "openai":
            emit(
                BootstrapResult(
                    "error",
                    "preset_provider_conflict",
                    {
                        "reason": (
                            f"--llm-preset implies provider=openai but you "
                            f"passed --provider={args.provider}. Drop one of them."
                        )
                    },
                )
            )
            return 2
        if args.llm_base_url is None and preset.get("base_url"):
            args.llm_base_url = preset["base_url"]
        if args.llm_model is None and preset.get("model"):
            args.llm_model = preset["model"]
    try:
        return run(args)
    except KeyboardInterrupt:
        emit(BootstrapResult("error", "interrupted", {}))
        return 130
    except Exception as exc:  # noqa: BLE001
        emit(BootstrapResult("error", f"unexpected: {exc}", {}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
