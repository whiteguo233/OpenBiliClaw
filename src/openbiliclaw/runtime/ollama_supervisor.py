"""Shared helpers for supervising a local Ollama daemon."""

from __future__ import annotations

import ipaddress
import logging
import os
import threading
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse, urlunparse

import httpx
from rich.console import Console

from openbiliclaw.llm.registry import _ollama_is_chat_capable
from openbiliclaw.runtime import embedding_progress

if TYPE_CHECKING:
    import subprocess

    from openbiliclaw.config import Config

_DEFAULT_OLLAMA_ENDPOINT = "http://127.0.0.1:11434"
_DEFAULT_OLLAMA_KEEP_ALIVE = "24h"

console = Console()
logger = logging.getLogger(__name__)

# Guards watchdog counters and _managed_daemon transitions shared between the
# watchdog thread and the start/stop/restart entry points.
_supervisor_lock = threading.RLock()


@dataclass
class _ManagedDaemon:
    """Recorded spec of the Ollama daemon this process is responsible for.

    ``proc`` is the ``Popen`` handle when WE spawned it (signalable), or ``None``
    when we merely *adopted* an already-running daemon on our dedicated private
    port (recorded for probe-based watchdog recovery, but never signalled —
    invariant 1). ``base_url`` and ``models_dir`` pin the exact launch parameters
    so any restart reuses the recorded ``(host, OLLAMA_MODELS)`` — a private
    daemon never comes back on 11434 or with the wrong models dir (invariant 2).
    """

    proc: subprocess.Popen[bytes] | None
    base_url: str
    models_dir: str | None
    log_file: Any | None = None


# The single managed daemon this process tracks (None when we never started or
# adopted one). Lets ``stop_managed_ollama`` shut down only what we spawned on
# exit, leaving an externally-managed Ollama (official app / user daemon)
# untouched, and lets restart/watchdog routing reuse the recorded launch spec.
_managed_daemon: _ManagedDaemon | None = None


def _managed_ollama_log_file() -> Any | None:
    """Open the managed Ollama log in the runtime project's ``logs/`` dir.

    Packaged entry points set ``OPENBILICLAW_PROJECT_ROOT``; when absent (dev,
    CLI, tests) we keep the old ``DEVNULL`` behaviour so no stray log files are
    created under the user's home during normal operation. ``ollama serve``
    writes both its own logs and the llama-server runner crash detail here —
    previously both were discarded, which made ``0xc0000005`` field reports
    impossible to diagnose beyond the HTTP 500 snippet.
    """
    root = os.environ.get("OPENBILICLAW_PROJECT_ROOT", "").strip()
    if not root:
        return None
    log_dir = Path(root) / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        return open(  # noqa: SIM115 — held open for the daemon's lifetime
            log_dir / "ollama-managed.log",
            "a",
            buffering=1,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        logger.debug("Unable to open managed Ollama log under %s", log_dir, exc_info=True)
        return None


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


def _is_default_ollama_endpoint(endpoint: str) -> bool:
    """Return whether ``endpoint`` is the default loopback Ollama daemon."""
    parsed = urlparse(endpoint)
    host = (parsed.hostname or "").strip().lower()
    return host in {"localhost", "127.0.0.1", "::1"} and parsed.port == 11434


def _normalized_hostport(url: str) -> tuple[str, int | None]:
    """Return a scheme-insensitive (host, port) with loopback aliases collapsed."""
    parsed = urlparse(url if "://" in url else f"http://{url}")
    host = (parsed.hostname or "").strip().lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        host = "127.0.0.1"
    return host, parsed.port


def is_managed_endpoint(endpoint: str) -> bool:
    """Return whether ``endpoint`` is the daemon we recorded (default or private).

    Compares normalized host:port against the recorded daemon's ``base_url``
    (``localhost`` ≡ ``127.0.0.1`` ≡ ``::1``, scheme- and ``/v1``-path-insensitive).
    False when no daemon is recorded.
    """
    record = _managed_daemon
    if record is None:
        return False
    return _normalized_hostport(endpoint) == _normalized_hostport(record.base_url)


def may_manage_ollama_endpoint(endpoint: str) -> bool:
    """Return whether the supervisor may start/restart Ollama at ``endpoint``.

    True for the default loopback daemon (as before) OR a recorded managed daemon
    (e.g. the private ``with-embedding`` daemon on 11435). Shared by both repair
    gates so the private daemon is no longer excluded from self-heal.
    """
    return _is_default_ollama_endpoint(endpoint) or is_managed_endpoint(endpoint)


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


def _contains_non_ascii(text: str) -> bool:
    return any(ord(ch) > 127 for ch in text)


def ollama_models_relocation_candidate() -> str | None:
    """Return a safe model directory for managed Ollama relocation."""
    if os.name == "nt":
        root = os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
        candidate = os.path.join(root, "OpenBiliClaw", "ollama-models")
    else:
        candidate = "~/.openbiliclaw/ollama-models"
    path = os.path.abspath(os.path.expanduser(candidate))
    if _contains_non_ascii(path):
        return None
    return path


def managed_models_dir() -> str | None:
    """Return the durable managed Ollama model directory, if already migrated."""
    candidate = ollama_models_relocation_candidate()
    if candidate and os.path.isdir(candidate):
        return candidate
    return None


def _ollama_start_serve_background() -> bool:
    """Start ``ollama serve`` detached, waiting up to 15s for health."""
    import shutil
    import subprocess
    import time

    if _ollama_is_running():
        embedding_progress.report_ollama_phase("ready")
        return True
    embedding_progress.report_ollama_phase("starting")

    ollama = shutil.which("ollama")
    if ollama is None:
        embedding_progress.report_ollama_phase("down")
        return False

    log_file = _managed_ollama_log_file()
    try:
        env = os.environ.copy()
        env.setdefault("OLLAMA_KEEP_ALIVE", _DEFAULT_OLLAMA_KEEP_ALIVE)
        if models_dir := managed_models_dir():
            env.setdefault("OLLAMA_MODELS", models_dir)
        stdout = log_file if log_file is not None else subprocess.DEVNULL
        stderr = subprocess.STDOUT if log_file is not None else subprocess.DEVNULL
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
                stdout=stdout,
                stderr=stderr,
                stdin=subprocess.DEVNULL,
                env=env,
            )
        else:
            proc = subprocess.Popen(
                [ollama, "serve"],
                start_new_session=True,
                stdout=stdout,
                stderr=stderr,
                stdin=subprocess.DEVNULL,
                env=env,
            )
    except Exception as exc:
        if log_file is not None:
            with suppress(Exception):
                log_file.close()
        console.print(f"[red]启动 ollama serve 失败: {exc}[/red]")
        embedding_progress.report_ollama_phase("down")
        return False

    # Remember the daemon WE started so it can be cleanly stopped on exit (and
    # its model runner / llama-server child with it) instead of being orphaned.
    # Record the launch spec so restart routing reuses the default endpoint.
    global _managed_daemon
    _managed_daemon = _ManagedDaemon(proc, _DEFAULT_OLLAMA_ENDPOINT, managed_models_dir(), log_file)

    for _ in range(30):
        if _ollama_is_running():
            embedding_progress.report_ollama_phase("ready")
            reset_watchdog_backoff()
            start_ollama_watchdog()
            return True
        time.sleep(0.5)
    embedding_progress.report_ollama_phase("down")
    return False


def pick_free_port() -> int:
    """Pick a currently-free 127.0.0.1 TCP port for a private Ollama daemon."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def start_managed_ollama_at(models_dir: str, host: str) -> bool:
    """Start a PRIVATE ``ollama serve`` bound to ``host`` (``127.0.0.1:<port>``)
    reading models from ``models_dir`` (``OLLAMA_MODELS``).

    Used by the ``with-embedding`` desktop variant so the bundled model is
    served from our own ASCII, user-writable directory on a dedicated port,
    independent of any external/official Ollama the user may run on 11434.
    Records the launch spec in ``_managed_daemon`` so it is cleanly stopped on
    exit and any restart reuses this ``(host, models_dir)``.
    """
    import shutil
    import subprocess
    import time

    base_url = host if host.startswith("http") else f"http://{host}"
    hostport = base_url.removeprefix("http://").removeprefix("https://")
    abs_models_dir = os.path.abspath(os.path.expanduser(models_dir))
    global _managed_daemon
    if _ollama_is_running(base_url):
        # The private port is dedicated to OpenBiliClaw, so a daemon already
        # answering here at boot is a force-quit orphan of ours: record it
        # (proc=None → adopted, not signalable) so the watchdog can recover it
        # after it dies. We still never signal a process we don't own.
        _managed_daemon = _ManagedDaemon(None, base_url, abs_models_dir)
        embedding_progress.report_ollama_phase("ready")
        reset_watchdog_backoff()
        start_ollama_watchdog()
        return True
    embedding_progress.report_ollama_phase("starting")

    ollama = shutil.which("ollama")
    if ollama is None:
        embedding_progress.report_ollama_phase("down")
        return False

    log_file = _managed_ollama_log_file()
    try:
        env = os.environ.copy()
        # Hard-set (not setdefault): the private daemon is fully owned, so a
        # user-level OLLAMA_KEEP_ALIVE=0 must not degrade it into 5-min unloads.
        env["OLLAMA_KEEP_ALIVE"] = _DEFAULT_OLLAMA_KEEP_ALIVE
        env["OLLAMA_HOST"] = hostport
        env["OLLAMA_MODELS"] = abs_models_dir
        stdout = log_file if log_file is not None else subprocess.DEVNULL
        stderr = subprocess.STDOUT if log_file is not None else subprocess.DEVNULL
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
            )
            proc = subprocess.Popen(
                [ollama, "serve"],
                creationflags=creationflags,
                stdout=stdout,
                stderr=stderr,
                stdin=subprocess.DEVNULL,
                env=env,
            )
        else:
            proc = subprocess.Popen(
                [ollama, "serve"],
                start_new_session=True,
                stdout=stdout,
                stderr=stderr,
                stdin=subprocess.DEVNULL,
                env=env,
            )
    except Exception as exc:
        if log_file is not None:
            with suppress(Exception):
                log_file.close()
        console.print(f"[red]启动私有 ollama serve 失败: {exc}[/red]")
        embedding_progress.report_ollama_phase("down")
        return False

    _managed_daemon = _ManagedDaemon(proc, base_url, abs_models_dir, log_file)

    for _ in range(30):
        if _ollama_is_running(base_url):
            embedding_progress.report_ollama_phase("ready")
            reset_watchdog_backoff()
            start_ollama_watchdog()
            return True
        time.sleep(0.5)
    embedding_progress.report_ollama_phase("down")
    return False


def restart_managed_ollama_with_models_dir(models_dir: str) -> tuple[bool, str]:
    """Restart the default daemon we own so future pulls use ``models_dir``.

    This is the non-ASCII path-encoding migration tool and only applies to the
    default daemon; a recorded PRIVATE daemon (with-embedding) is refused with
    ``private_daemon`` — models migration does not apply to its bundled model.
    """
    record = _managed_daemon
    if record is not None and not _is_default_ollama_endpoint(record.base_url):
        return (False, "private_daemon")
    target = os.path.abspath(os.path.expanduser(models_dir))
    # Refuse an external daemon *before* creating the dir: ``managed_models_dir``
    # treats the dir's existence as the migration marker, so leaving an empty one
    # behind on a refused attempt would make a later managed start point
    # OLLAMA_MODELS at a modeless dir. Bail first, create only when we own it.
    if _ollama_is_running() and (record is None or record.proc is None):
        return (False, "external_ollama")
    os.makedirs(target, exist_ok=True)
    stop_managed_ollama()
    ok = _ollama_start_serve_background()
    return (ok, "" if ok else "start_failed")


def restart_managed_ollama() -> tuple[bool, str]:
    """Restart the daemon this process owns, reusing its recorded launch spec.

    Spec-aware: a recorded private daemon relaunches on its own ``(host,
    models_dir)`` via :func:`start_managed_ollama_at`; a default-daemon record
    keeps the default path. Refuses to touch a daemon we don't own — if the
    recorded endpoint still answers but we hold no owned handle, we return
    ``external_ollama`` (no record) or ``adopted_alive`` (adopted, proc=None).
    """
    global _restart_in_progress
    with _supervisor_lock:
        if _restart_in_progress:
            return (False, "restart_in_progress")
        _restart_in_progress = True
    try:
        record = _managed_daemon
        probe_url = record.base_url if record is not None else _DEFAULT_OLLAMA_ENDPOINT
        if _ollama_is_running(probe_url):
            if record is None:
                return (False, "external_ollama")
            if record.proc is None:
                return (False, "adopted_alive")
        if record is not None and not _is_default_ollama_endpoint(record.base_url):
            models_dir = record.models_dir or ""
            base_url = record.base_url
            stop_managed_ollama()
            ok = start_managed_ollama_at(models_dir, base_url)
            return (ok, "" if ok else "start_failed")
        stop_managed_ollama()
        ok = _ollama_start_serve_background()
        return (ok, "" if ok else "start_failed")
    finally:
        with _supervisor_lock:
            _restart_in_progress = False


def ensure_managed_ollama(endpoint: str) -> bool:
    """Start the managed daemon appropriate for ``endpoint``.

    Routes to the recorded private daemon's ``(models_dir, host)`` when
    ``endpoint`` matches a recorded private daemon; otherwise starts the default
    daemon. Used by the not_running repair action so the private daemon is
    started with its own spec instead of the default port.
    """
    record = _managed_daemon
    if (
        record is not None
        and is_managed_endpoint(endpoint)
        and not _is_default_ollama_endpoint(record.base_url)
    ):
        return start_managed_ollama_at(record.models_dir or "", record.base_url)
    return _ollama_start_serve_background()


# --- Watchdog: restart a crashed managed daemon automatically (invariant 3) ---

# Injectable seams so tests drive iterations synchronously with a fake clock.
_watchdog_thread: threading.Thread | None = None
_watchdog_failures = 0
_watchdog_gave_up = False
_restart_in_progress = False

_WATCHDOG_BACKOFF_BASE_SECONDS = 5.0
_WATCHDOG_BACKOFF_CAP_SECONDS = 300.0
_WATCHDOG_GIVE_UP_AFTER = 5


def _watchdog_sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)


def _watchdog_probe(base_url: str) -> bool:
    return _ollama_is_running(base_url)


def reset_watchdog_backoff() -> None:
    """Clear watchdog failure state; called on any successful start / manual repair."""
    global _watchdog_failures, _watchdog_gave_up
    with _supervisor_lock:
        _watchdog_failures = 0
        _watchdog_gave_up = False


def _watchdog_tick() -> None:
    """One watchdog iteration: probe the recorded daemon, restart it if it died.

    Never signals a process we did not start (restart routing enforces that);
    consecutive restart failures back off 5s → 300s and give up after 5 until
    :func:`reset_watchdog_backoff` (manual repair success) or process restart.
    """
    global _watchdog_failures, _watchdog_gave_up, _managed_daemon
    with _supervisor_lock:
        record = _managed_daemon
        if record is None or _watchdog_gave_up or _restart_in_progress:
            return
    if _watchdog_probe(record.base_url):
        with _supervisor_lock:
            _watchdog_failures = 0
        return
    proc = record.proc
    if proc is not None and proc.poll() is None:
        # Our process is alive but one probe failed — likely transient load;
        # do not restart-kill a living daemon on a single missed probe.
        return
    ok, reason = restart_managed_ollama()
    if ok:
        reset_watchdog_backoff()
        logger.warning("watchdog restarted managed ollama at %s", record.base_url)
        return
    if reason in {"external_ollama", "adopted_alive", "restart_in_progress"}:
        return  # daemon answering again / someone else handling it — nothing to heal
    with _supervisor_lock:
        _watchdog_failures += 1
        failures = _watchdog_failures
        if _managed_daemon is None:
            # A failed restart may have cleared the record; keep the spec so
            # the next attempt still knows (host, models_dir).
            _managed_daemon = _ManagedDaemon(None, record.base_url, record.models_dir)
    backoff = min(
        _WATCHDOG_BACKOFF_BASE_SECONDS * (2 ** (failures - 1)),
        _WATCHDOG_BACKOFF_CAP_SECONDS,
    )
    logger.warning(
        "watchdog restart of %s failed (%s), attempt %d/%d, backing off %.0fs",
        record.base_url,
        reason,
        failures,
        _WATCHDOG_GIVE_UP_AFTER,
        backoff,
    )
    _watchdog_sleep(backoff)
    if failures >= _WATCHDOG_GIVE_UP_AFTER:
        with _supervisor_lock:
            _watchdog_gave_up = True
        embedding_progress.report_ollama_phase("down")
        logger.error(
            "watchdog giving up on %s after %d consecutive restart failures; "
            "use the embedding repair action or restart the app to retry",
            record.base_url,
            failures,
        )


def start_ollama_watchdog(interval_seconds: float = 30.0) -> None:
    """Start the managed-Ollama watchdog thread (idempotent, daemon thread)."""
    global _watchdog_thread
    with _supervisor_lock:
        if _watchdog_thread is not None and _watchdog_thread.is_alive():
            return

        def _loop() -> None:
            import time

            while True:
                # Real sleep on purpose: tests drive iterations by calling
                # _watchdog_tick() directly; patching the _watchdog_sleep seam
                # must not turn this loop into a hot spin.
                time.sleep(interval_seconds)
                try:
                    _watchdog_tick()
                except Exception as exc:  # noqa: BLE001 — the watchdog must survive anything
                    logger.warning("ollama watchdog tick failed: %s", exc)

        thread = threading.Thread(target=_loop, name="obc-ollama-watchdog", daemon=True)
        _watchdog_thread = thread
        thread.start()


def stop_managed_ollama() -> bool:
    """Stop the ``ollama serve`` daemon this process started, if any.

    Only touches a daemon we spawned in :func:`_ollama_start_serve_background`;
    an Ollama that was already running when we started (official app / a daemon
    the user manages) is left alone. Kills the whole process tree so the model
    runner (``llama-server`` / ``ollama runner``) goes down with the parent
    rather than lingering as a resource-leaking orphan. Returns True when a
    managed daemon was actually stopped.
    """
    global _managed_daemon
    record = _managed_daemon
    _managed_daemon = None
    if record is not None:
        log_file = getattr(record, "log_file", None)
        if log_file is not None:
            with suppress(Exception):
                log_file.close()
    proc = record.proc if record is not None else None
    if proc is None or proc.poll() is not None:
        return False

    import signal
    import subprocess

    try:
        if os.name == "nt":
            # terminate() reaches only `ollama serve`; the model runner is a
            # child process, so use taskkill /T to take down the whole tree.
            from openbiliclaw.proc import no_window_kwargs

            subprocess.run(  # noqa: S603
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],  # noqa: S607
                capture_output=True,
                check=False,
                **no_window_kwargs(),
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
