"""Shared runtime helpers for the dedicated recommendation API process.

On POSIX systems the recommendation process is exposed through a Unix domain
socket.  Windows asyncio/uvicorn does not implement ``create_unix_server``, so
on Windows the same process listens on a loopback TCP port instead.  POSIX also
falls back to loopback TCP when the socket path derived from the data
directory would exceed the platform ``sun_path`` limit (about 104 bytes on
macOS), because binding such a path crashes the child with ``OSError:
AF_UNIX path too long``.

Whenever TCP is selected the parent probes for a free loopback port starting
at the configured base (explicit ``OPENBILICLAW_RECOMMENDATION_PORT`` or the
default), so a second instance on the same machine does not collide with the
first.  The chosen port is written back to the environment, keeping the
child process and the API proxy on the same transport endpoint.
"""

from __future__ import annotations

import logging
import os
import socket
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_RECOMMENDATION_PORT = 8423
RECOMMENDATION_SOCK_ENV = "OPENBILICLAW_RECOMMENDATION_SOCK"
RECOMMENDATION_PORT_ENV = "OPENBILICLAW_RECOMMENDATION_PORT"

# Number of consecutive ports probed when selecting the loopback TCP port
# (base port plus 20 increments).  Bounded so an exhausted range fails fast
# and loudly instead of scanning the whole port space.
RECOMMENDATION_PORT_SCAN_ATTEMPTS = 21

# Size of ``sockaddr_un.sun_path`` on the strictest supported POSIX platform
# (macOS/BSD: 104 bytes including the NUL terminator; Linux allows 108).  A
# path is only bindable when its encoded form plus the terminator fits.
UNIX_SOCKET_SUN_PATH_BYTES = 104


def recommendation_sock_from_data_path(data_path: Path) -> str:
    """Return the default Unix socket path for a data directory."""
    return str(Path(data_path) / "runtime" / "recommendation.sock")


def unix_socket_path_too_long(path: str) -> bool:
    """Whether a Unix socket path exceeds the portable ``sun_path`` limit."""
    return len(os.fsencode(path)) >= UNIX_SOCKET_SUN_PATH_BYTES


def find_free_loopback_port(base_port: int, max_attempts: int | None = None) -> int | None:
    """Return the first bindable 127.0.0.1 port at or above ``base_port``.

    Probes by actually binding each candidate and returns ``None`` when the
    whole bounded range is occupied.  There is an inherent race between this
    probe and the child process binding the selected port; the child logs a
    clear bind error if it loses that race.
    """
    attempts = max_attempts if max_attempts is not None else RECOMMENDATION_PORT_SCAN_ATTEMPTS
    for offset in range(max(1, attempts)):
        candidate = base_port + offset
        if candidate > 65535:
            break
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind(("127.0.0.1", candidate))
        except OSError:
            continue
        return candidate
    return None


def _select_tcp_port() -> str:
    """Pick the loopback TCP port, probing upward from the configured base.

    An explicitly configured port is probed like the default: the port is
    internal loopback IPC whose only consumer is our own API proxy reading
    the same environment, so hard-failing on an occupied explicit port would
    just recreate the outage this fallback exists to avoid.  A WARNING keeps
    the override visible when it cannot be honored exactly.
    """
    explicit = os.environ.get(RECOMMENDATION_PORT_ENV, "").strip()
    base = DEFAULT_RECOMMENDATION_PORT
    if explicit:
        try:
            base = int(explicit)
        except ValueError:
            logger.warning(
                "Ignoring invalid %s=%r; probing from default port %d",
                RECOMMENDATION_PORT_ENV,
                explicit,
                DEFAULT_RECOMMENDATION_PORT,
            )
    port = find_free_loopback_port(base)
    if port is None:
        logger.warning(
            "No free loopback port in %d..%d for the recommendation process; "
            "keeping %d so the child reports the bind failure with context",
            base,
            base + RECOMMENDATION_PORT_SCAN_ATTEMPTS - 1,
            base,
        )
        return str(base)
    if explicit and port != base:
        logger.warning(
            "Recommendation port %d from %s is occupied; using %d instead",
            base,
            RECOMMENDATION_PORT_ENV,
            port,
        )
    return str(port)


def ensure_recommendation_transport_env(data_path: Path) -> str:
    """Set the platform-appropriate recommendation transport environment.

    Returns a human-readable transport description for status/log output.
    """
    if os.name == "nt" or os.environ.get(RECOMMENDATION_PORT_ENV, "").strip():
        port = _select_tcp_port()
        os.environ[RECOMMENDATION_PORT_ENV] = port
        os.environ.pop(RECOMMENDATION_SOCK_ENV, None)
        return f"TCP 127.0.0.1:{port}"
    sock = os.environ.get(RECOMMENDATION_SOCK_ENV) or recommendation_sock_from_data_path(data_path)
    if unix_socket_path_too_long(sock):
        port = _select_tcp_port()
        os.environ[RECOMMENDATION_PORT_ENV] = port
        os.environ.pop(RECOMMENDATION_SOCK_ENV, None)
        logger.warning(
            "Recommendation Unix socket path %r is %d bytes, exceeding the "
            "%d-byte AF_UNIX sun_path limit; falling back to TCP 127.0.0.1:%s",
            sock,
            len(os.fsencode(sock)),
            UNIX_SOCKET_SUN_PATH_BYTES,
            port,
        )
        return f"TCP 127.0.0.1:{port}"
    os.environ[RECOMMENDATION_SOCK_ENV] = sock
    os.environ.pop(RECOMMENDATION_PORT_ENV, None)
    return f"Unix socket {os.environ[RECOMMENDATION_SOCK_ENV]}"


def recommendation_transport_enabled() -> bool:
    """Whether the main API is expected to proxy to a recommendation process."""
    return bool(
        os.environ.get(RECOMMENDATION_SOCK_ENV, "").strip()
        or os.environ.get(RECOMMENDATION_PORT_ENV, "").strip()
    )
