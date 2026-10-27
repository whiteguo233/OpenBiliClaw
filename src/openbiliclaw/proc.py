"""Spawn helper CLIs without flashing a console window on Windows.

The desktop / installer build runs the backend from a window-less parent
(``pythonw`` style). On Windows every console executable a window-less
process starts gets a **brand new console window** unless
``CREATE_NO_WINDOW`` is passed, so background probes such as
``rdt status``, ``git rev-parse`` or ``taskkill`` popped visible black
terminals in the user's face — several in a row whenever a settings save
re-ran the source probes.

Every ``subprocess`` / ``asyncio.create_subprocess_exec`` call that the
backend makes on its own initiative must therefore splat
``**no_window_kwargs()``. The only exceptions are commands the user
explicitly invoked from a terminal (``openbiliclaw`` CLI, ``codex login``)
— those already own a console and must keep it for interactive prompts.

The module also owns :class:`ChildProcessSupervisor`, which keeps the
desktop and CLI backend children alive for the parent's lifetime. A committed
API configuration generation also replaces their startup snapshots so repaired
providers reach background evaluation and expression-copy work.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

logger = logging.getLogger(__name__)

# Value of subprocess.CREATE_NO_WINDOW; defined here so non-Windows type
# checking and tests do not depend on the platform-only attribute.
CREATE_NO_WINDOW = 0x08000000


def backend_child_env(app: Any, **overrides: str) -> dict[str, str]:
    """Keep respawned children on the data directory locked by their parent."""
    env = {**os.environ, **overrides}
    active_path = getattr(getattr(app, "state", None), "active_runtime_data_path", None)
    if active_path is not None:
        env["OPENBILICLAW_RUNTIME_DATA_DIR"] = str(active_path)
    return env


def backend_config_generation(app: Any) -> int | None:
    """Read a committed API runtime generation, deferring while settings apply.

    Config saves may queue newer revisions or roll back after a failed handoff.
    Children read config.toml on spawn, so wait until that queue has settled.
    """
    state = getattr(app, "state", None)
    task = getattr(state, "config_apply_task", None)
    if task is not None and not task.done():
        return None
    return int(getattr(state, "backend_config_generation", 0))


def no_window_kwargs() -> dict[str, Any]:
    """Return Popen kwargs that keep a child process headless on Windows.

    Empty on POSIX, so call sites can splat it unconditionally. The value
    type is ``Any`` so the mapping can be splatted into the heavily
    overloaded ``subprocess`` / ``asyncio`` signatures under mypy strict.
    """

    if os.name != "nt":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", CREATE_NO_WINDOW)}


class ChildProcess(Protocol):
    """The ``subprocess.Popen`` surface the supervisor relies on."""

    @property
    def pid(self) -> int: ...

    @property
    def returncode(self) -> int | None: ...

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...


@dataclass
class ManagedChild:
    """One backend child the parent process keeps alive."""

    name: str
    spawn: Callable[[], ChildProcess]
    process: ChildProcess | None = None
    spawned_at: float = 0.0
    failures: int = 0
    restart_at: float = 0.0
    exit_handled: bool = False
    restart_requested: bool = False


class ChildProcessSupervisor:
    """Respawn crashed backend children for the parent's lifetime.

    The desktop four-process mode delegates every background loop to child
    processes, but the parent used to spawn them once and never look again:
    one crashed ``worker`` / ``discovery_worker`` silently stopped its loops
    (and, in windowed builds, could pop a bootloader error dialog) until the
    whole app was restarted. The supervisor polls every child and respawns a
    dead one with capped exponential backoff so a crash loop cannot spin.

    A changed ``config_generation_provider`` restarts children after confirming
    their previous processes exited. Returning ``None`` defers configuration
    restarts while the API is still applying or rolling back settings.

    ``check_once`` is the deterministic, injection-friendly core; ``start``
    only adds a daemon thread on top of it.
    """

    def __init__(
        self,
        children: Iterable[ManagedChild],
        *,
        poll_interval_seconds: float = 5.0,
        backoff_base_seconds: float = 2.0,
        backoff_max_seconds: float = 60.0,
        healthy_after_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
        config_generation_provider: Callable[[], int | None] | None = None,
    ) -> None:
        self._children = list(children)
        self._poll_interval = max(0.5, float(poll_interval_seconds))
        self._backoff_base = max(0.0, float(backoff_base_seconds))
        self._backoff_max = max(self._backoff_base, float(backoff_max_seconds))
        self._healthy_after = max(0.0, float(healthy_after_seconds))
        self._clock = clock
        self.config_generation_provider = config_generation_provider
        self._config_generation: int | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    @property
    def pids(self) -> dict[str, int]:
        """Currently managed PIDs keyed by child name."""
        with self._lock:
            return {
                child.name: int(child.process.pid)
                for child in self._children
                if child.process is not None
            }

    def start(self) -> None:
        """Spawn every child once, then start the background watchdog thread."""
        with self._lock:
            self._check_config_generation_locked()
            for child in self._children:
                self._spawn_locked(child)
        self._thread = threading.Thread(
            target=self._run,
            name="obc-child-supervisor",
            daemon=True,
        )
        self._thread.start()

    def check_once(self) -> None:
        """Detect dead children and respawn the ones whose backoff elapsed."""
        with self._lock:
            now = self._clock()
            self._check_config_generation_locked()
            for child in self._children:
                if not child.restart_requested:
                    continue
                if not self._terminate_child_locked(child, timeout=5.0):
                    continue
                child.process = None
                child.restart_requested = False
                child.failures = 0
                child.restart_at = 0.0
                child.exit_handled = False
            for child in self._children:
                process = child.process
                if child.restart_requested:
                    continue
                if process is None or process.poll() is None or child.exit_handled:
                    continue
                lifetime = max(0.0, now - child.spawned_at)
                if lifetime >= self._healthy_after:
                    child.failures = 0
                child.failures += 1
                delay = self._backoff_delay(child.failures)
                child.restart_at = now + delay
                child.exit_handled = True
                logger.error(
                    "Backend child %s exited (code=%s) after %.1fs; restarting in %.1fs",
                    child.name,
                    process.returncode,
                    lifetime,
                    delay,
                )
            for child in self._children:
                if child.restart_requested:
                    continue
                process = child.process
                if process is not None and process.poll() is None:
                    continue
                if child.exit_handled and now < child.restart_at:
                    continue
                self._spawn_locked(child)

    def stop(self, *, terminate_timeout_seconds: float = 5.0) -> None:
        """Stop the watchdog and terminate every managed child."""
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=max(1.0, float(terminate_timeout_seconds)))
        with self._lock:
            for child in reversed(self._children):
                if self._terminate_child_locked(child, timeout=terminate_timeout_seconds):
                    child.process = None

    def _check_config_generation_locked(self) -> None:
        provider = self.config_generation_provider
        if provider is None:
            return
        generation = provider()
        if generation is None:
            return
        previous = self._config_generation
        self._config_generation = generation
        if previous is None or generation == previous:
            return
        logger.info("Applied runtime configuration changed; restarting backend children")
        for child in self._children:
            child.restart_requested = True

    @staticmethod
    def _terminate_child_locked(child: ManagedChild, *, timeout: float) -> bool:
        process = child.process
        if process is None or process.poll() is not None:
            return True
        try:
            process.terminate()
            process.wait(timeout=timeout)
            return True
        except Exception:
            try:
                process.kill()
                process.wait(timeout=timeout)
                return True
            except Exception:
                # Do not start a replacement while its old claim owner lives.
                logger.exception("Failed to stop backend child %s", child.name)
                return False

    def _run(self) -> None:
        while not self._stop_event.wait(self._poll_interval):
            try:
                self.check_once()
            except Exception:
                logger.exception("Backend child supervisor sweep failed")

    def _backoff_delay(self, failures: int) -> float:
        exponent = min(max(0, failures - 1), 10)
        return min(self._backoff_base * (2.0**exponent), self._backoff_max)

    def _spawn_locked(self, child: ManagedChild) -> bool:
        if self._stop_event.is_set():
            return False
        now = self._clock()
        try:
            child.process = child.spawn()
        except Exception:
            child.exit_handled = True
            child.failures += 1
            delay = self._backoff_delay(child.failures)
            child.restart_at = now + delay
            logger.exception(
                "Failed to spawn backend child %s; retrying in %.1fs",
                child.name,
                delay,
            )
            return False
        child.spawned_at = now
        child.restart_at = 0.0
        child.exit_handled = False
        logger.info("Backend child %s running pid=%s", child.name, child.process.pid)
        return True
