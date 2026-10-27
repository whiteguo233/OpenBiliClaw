"""Dedicated recommendation API process.

Uses a Unix domain socket on POSIX platforms.  Windows asyncio/uvicorn cannot
create Unix sockets, so the Windows build binds a loopback TCP port instead.

Like the full worker, this process now waits out a config that cannot build an
LLM instance (fresh install / broken key) instead of serving from a degraded
context forever: once the user saves a usable key it starts the real API, so
recommendation endpoints recover without restarting the desktop app.
"""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING

import uvicorn

from openbiliclaw.api.app import create_app
from openbiliclaw.config import load_config
from openbiliclaw.recommendation_runtime import (
    DEFAULT_RECOMMENDATION_PORT,
    RECOMMENDATION_PORT_ENV,
    RECOMMENDATION_SOCK_ENV,
    UNIX_SOCKET_SUN_PATH_BYTES,
    find_free_loopback_port,
    recommendation_sock_from_data_path,
    unix_socket_path_too_long,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import FastAPI

logger = logging.getLogger(__name__)

# Retry cadence while the config cannot build an LLM registry (construction
# only, no network). Re-reads config.toml on every attempt.
DEFAULT_LLM_PROBE_RETRY_SECONDS = 15.0


def _probe_llm_registry() -> None:
    """Build the LLM registry from the current on-disk config."""
    from openbiliclaw.llm.registry import build_llm_registry

    build_llm_registry(load_config())


def wait_for_buildable_llm(
    *,
    retry_interval_seconds: float = DEFAULT_LLM_PROBE_RETRY_SECONDS,
    probe: Callable[[], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Block until config.toml builds at least one usable LLM instance."""
    probe_fn = probe or _probe_llm_registry
    retry_interval = max(1.0, float(retry_interval_seconds))
    waiting_logged = False
    while True:
        try:
            probe_fn()
            if waiting_logged:
                logger.info("Recommendation server LLM configuration recovered; starting API")
            return
        except Exception as exc:
            if not waiting_logged:
                logger.warning(
                    "Recommendation server is waiting for a buildable LLM "
                    "configuration (%s); retrying every %.0fs",
                    exc,
                    retry_interval,
                )
                waiting_logged = True
            sleep(retry_interval)


def _run_tcp(app: FastAPI, port: int) -> None:
    """Bind the recommendation API on loopback TCP with a clear failure log."""
    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
    except OSError as exc:
        logger.error(
            "Recommendation server failed to bind TCP 127.0.0.1:%d: %s. The port "
            "was free when it was probed; another process took it in between, "
            "or the probe range was exhausted.",
            port,
            exc,
        )
        raise


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    wait_for_buildable_llm()
    app = create_app()
    recommendation_port = os.environ.get(RECOMMENDATION_PORT_ENV, "").strip()
    if os.name == "nt" or recommendation_port:
        port = int(recommendation_port or str(DEFAULT_RECOMMENDATION_PORT))
        _run_tcp(app, port)
        return

    sock = os.environ.get(RECOMMENDATION_SOCK_ENV)
    if not sock:
        sock = recommendation_sock_from_data_path(load_config().data_path)
        if unix_socket_path_too_long(sock):
            free_port = find_free_loopback_port(DEFAULT_RECOMMENDATION_PORT)
            fallback_port = free_port if free_port is not None else DEFAULT_RECOMMENDATION_PORT
            logger.warning(
                "Recommendation Unix socket path %r is %d bytes, exceeding the "
                "%d-byte AF_UNIX sun_path limit; falling back to TCP 127.0.0.1:%d",
                sock,
                len(os.fsencode(sock)),
                UNIX_SOCKET_SUN_PATH_BYTES,
                fallback_port,
            )
            _run_tcp(app, fallback_port)
            return
    os.makedirs(os.path.dirname(sock), mode=0o700, exist_ok=True)
    uvicorn.run(app, uds=sock, log_level="info")


if __name__ == "__main__":
    main()
