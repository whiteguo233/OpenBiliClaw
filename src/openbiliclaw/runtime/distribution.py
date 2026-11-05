"""Distribution-specific boundaries for the sandboxed Mac App Store host."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


def is_mac_app_store() -> bool:
    """Identify the immutable store payload, including re-executed workers."""
    if sys.platform != "darwin" or not getattr(sys, "frozen", False):
        return False
    resources = Path(sys.executable).parent.parent / "Resources"
    return (resources / "mac-app-store.json").is_file()


def store_profile_root(home: Path, environ: Mapping[str, str]) -> Path:
    """Keep store profiles inside the container, including through symlinks."""
    default = home / "Library" / "Application Support" / "OpenBiliClaw"
    override = environ.get("OPENBILICLAW_PROJECT_ROOT", "")
    if override:
        candidate = Path(override).resolve()
        if candidate.is_relative_to(default.resolve()):
            return candidate
    return default


def prepare_store_python() -> None:
    """Use Python's bundled MIME table rather than unreadable system files.

    macOS exposes /etc/apache2/mime.types to stat(), but App Sandbox refuses
    reading it. Python's default lazy MIME initialization then raises on the
    first Starlette static response (including /m/).
    """
    import mimetypes

    mimetypes.knownfiles = []
    mimetypes.init(files=[])


def start_store_parent_watchdog(request_shutdown: Callable[[], None]) -> None:
    """Gracefully stop the backend if its native host closes the lifetime pipe.

    Only the API parent calls this. A handler installed before Uvicorn also
    handles its signal replay: a default SIGTERM disposition would kill Python
    before the entry point's finally block could reap its children.
    """
    import signal
    import threading

    signal.signal(signal.SIGTERM, lambda _signum, _frame: request_shutdown())
    signal.signal(signal.SIGINT, lambda _signum, _frame: request_shutdown())

    def watch() -> None:
        try:
            while os.read(0, 1):
                pass
        except OSError:
            pass
        request_shutdown()

    threading.Thread(target=watch, name="obc-store-host-lifetime", daemon=True).start()
