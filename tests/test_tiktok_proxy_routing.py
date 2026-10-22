"""Exercise real libcurl routing against local origin/proxy stand-ins."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from openbiliclaw import network
from openbiliclaw.api.runtime_context import _build_tiktok_client

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@contextmanager
def endpoint(marker: bytes) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", str(len(marker)))
            self.end_headers()
            self.wfile.write(marker)

        def log_message(self, *_args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize(
    ("mode", "ambient", "bypass", "expected"),
    [
        ("system", False, False, b"proxy"),
        ("system", True, False, b"proxy"),
        ("system", True, True, b"origin"),
        ("direct", True, False, b"origin"),
        ("custom", True, True, b"proxy"),
    ],
)
def test_real_tiktok_transport_obeys_proxy_policy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mode: str,
    ambient: bool,
    bypass: bool,
    expected: bytes,
) -> None:
    for name in network._PROXY_ENV_VARS + network._NO_PROXY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(network, "_outbound_mode", "direct")
    monkeypatch.setattr(network, "_outbound_proxy", None)
    with endpoint(b"origin") as origin, endpoint(b"proxy") as proxy:
        if ambient:
            monkeypatch.setenv("http_proxy", proxy)
        if bypass:
            monkeypatch.setenv("no_proxy", "*")
        # OS-only settings are deliberately absent from the child environment.
        monkeypatch.setattr(
            network, "getproxies", lambda: {"http": proxy, "no": "*" if bypass else ""}
        )
        network.set_outbound_proxy(proxy if mode == "custom" else "", mode=mode)
        router = _build_tiktok_client(
            SimpleNamespace(data_path=tmp_path), SimpleNamespace(mode="web")
        )
        with router.web.identity._create_session() as session:
            response = session.get(origin, timeout=3)
        assert response.content == expected
