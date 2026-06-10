"""Shared helpers for supervising a local Ollama daemon."""

from __future__ import annotations

import ipaddress
import os
from contextlib import suppress
from typing import TYPE_CHECKING
from urllib.parse import urlparse, urlunparse

import httpx
from rich.console import Console

from openbiliclaw.llm.registry import _ollama_is_chat_capable

if TYPE_CHECKING:
    import subprocess

    from openbiliclaw.config import Config

_DEFAULT_OLLAMA_ENDPOINT = "http://localhost:11434"
_DEFAULT_OLLAMA_KEEP_ALIVE = "24h"

console = Console()

# Handle to an ``ollama serve`` daemon THIS process started (None if Ollama was
# already running and we merely adopted it, or if we never started one). Lets
# ``stop_managed_ollama`` shut down only what we spawned on exit, leaving an
# externally-managed Ollama (official app / user daemon) untouched.
_managed_proc: subprocess.Popen[bytes] | None = None


def _embedding_wants_ollama(config: Config) -> bool:
    embedding = config.llm.embedding
    return (
        str(embedding.provider).strip().lower() == "ollama"
        or str(embedding.fallback_provider).strip().lower() == "ollama"
    )


def ollama_required(config: Config) -> bool:
    """Return whether chat or embedding routing may call Ollama."""
    return _ollama_is_chat_capable(config) or _embedding_wants_ollama(config)


def _strip_openai_v1_suffix(url: str) -> str:
    text = url.strip().rstrip("/")
    if not text:
        return _DEFAULT_OLLAMA_ENDPOINT
    parsed = urlparse(text)
    path = parsed.path.rstrip("/")
    if path == "/v1":
        path = ""
    elif path.endswith("/v1"):
        path = path[: -len("/v1")]
    return urlunparse((parsed.scheme, parsed.netloc, path.rstrip("/"), "", "", "")).rstrip("/")


def effective_ollama_endpoint(config: Config) -> str:
    """Return the daemon root endpoint used for Ollama health probes.

    Chat and embedding providers use OpenAI-compatible ``/v1`` URLs in config, but
    Ollama's health API lives at daemon root ``/api/version``.
    """
    if _ollama_is_chat_capable(config):
        base_url = config.llm.ollama.base_url.strip() or f"{_DEFAULT_OLLAMA_ENDPOINT}/v1"
    elif _embedding_wants_ollama(config):
        base_url = (
            config.llm.embedding.base_url.strip()
            or config.llm.ollama.base_url.strip()
            or f"{_DEFAULT_OLLAMA_ENDPOINT}/v1"
        )
    else:
        base_url = config.llm.ollama.base_url.strip() or f"{_DEFAULT_OLLAMA_ENDPOINT}/v1"
    return _strip_openai_v1_suffix(base_url)


def is_loopback(url: str) -> bool:
    """Return whether a URL points at the local machine."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").strip().lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _ollama_is_running(host: str = _DEFAULT_OLLAMA_ENDPOINT) -> bool:
    """Probe Ollama's HTTP API; return True only on a healthy 200 response."""
    try:
        # trust_env=False — a localhost Ollama probe must not be hijacked by
        # HTTP_PROXY env (e.g. 127.0.0.1:7897 VPN client).
        with httpx.Client(timeout=2.0, trust_env=False) as client:
            response = client.get(f"{host.rstrip('/')}/api/version")
            return response.status_code == 200
    except Exception:
        return False


def _ollama_start_serve_background() -> bool:
    """Start ``ollama serve`` detached, waiting up to 15s for health."""
    import shutil
    import subprocess
    import time

    if _ollama_is_running():
        return True

    ollama = shutil.which("ollama")
    if ollama is None:
        return False

    try:
        env = os.environ.copy()
        env.setdefault("OLLAMA_KEEP_ALIVE", _DEFAULT_OLLAMA_KEEP_ALIVE)
        if os.name == "nt":
            # CREATE_NO_WINDOW (not DETACHED_PROCESS): give `ollama serve` a
            # hidden console that its child `ollama runner` inherits, so neither
            # flashes a window. DETACHED_PROCESS leaves the runner with no console
            # to inherit, so it allocates its own *visible* conhost — the window
            # flashing users saw on the packaged tray app.
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
            )
            proc = subprocess.Popen(
                [ollama, "serve"],
                creationflags=creationflags,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                env=env,
            )
        else:
            proc = subprocess.Popen(
                [ollama, "serve"],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                env=env,
            )
    except Exception as exc:
        console.print(f"[red]启动 ollama serve 失败: {exc}[/red]")
        return False

    # Remember the daemon WE started so it can be cleanly stopped on exit (and
    # its model runner / llama-server child with it) instead of being orphaned.
    global _managed_proc
    _managed_proc = proc

    for _ in range(30):
        if _ollama_is_running():
            return True
        time.sleep(0.5)
    return False


def stop_managed_ollama() -> bool:
    """Stop the ``ollama serve`` daemon this process started, if any.

    Only touches a daemon we spawned in :func:`_ollama_start_serve_background`;
    an Ollama that was already running when we started (official app / a daemon
    the user manages) is left alone. Kills the whole process tree so the model
    runner (``llama-server`` / ``ollama runner``) goes down with the parent
    rather than lingering as a resource-leaking orphan. Returns True when a
    managed daemon was actually stopped.
    """
    global _managed_proc
    proc = _managed_proc
    _managed_proc = None
    if proc is None or proc.poll() is not None:
        return False

    import signal
    import subprocess

    try:
        if os.name == "nt":
            # terminate() reaches only `ollama serve`; the model runner is a
            # child process, so use taskkill /T to take down the whole tree.
            subprocess.run(  # noqa: S603
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],  # noqa: S607
                capture_output=True,
                check=False,
            )
        else:
            # Started with start_new_session=True → it leads its own process
            # group; signal the group so the runner children stop too.
            with suppress(ProcessLookupError, PermissionError):
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        with suppress(Exception):
            proc.wait(timeout=5)
        return True
    except Exception as exc:  # noqa: BLE001 — best-effort shutdown, never raise on exit
        console.print(f"[yellow]停止托管 ollama 失败: {exc}[/yellow]")
        return False
