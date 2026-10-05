"""Contract tests for the main-API -> recommendation-process HTTP proxy.

Regression background
---------------------
``/api/recommendations/*`` is proxied to a dedicated recommendation process
(Unix socket on POSIX, loopback TCP on Windows) that runs the *same* auth
middleware. Its CSRF check compares the request ``Origin`` with the effective
``(scheme, host, port)``. If the proxy drops the original ``Host`` header, httpx
synthesises one from the backend URL (``localhost`` / ``127.0.0.1:<port>``), so a
same-origin browser POST authenticated by session cookie can never match its
``Origin`` and every write is rejected with ``403 {"error":"csrf"}``.
"""

from __future__ import annotations

import http.server
import threading
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from openbiliclaw.api.app import create_app
from openbiliclaw.api.auth import AuthGate
from openbiliclaw.config import ApiAuthConfig, ApiConfig, Config
from openbiliclaw.recommendation_runtime import (
    RECOMMENDATION_PORT_ENV,
    RECOMMENDATION_SOCK_ENV,
)

if TYPE_CHECKING:
    from pathlib import Path

_ORIGIN = "http://127.0.0.1:8420"
_APPEND_PATH = "/api/recommendations/append"
_TCP_PORT = "18424"
_SOCK_PATH = "/tmp/openbiliclaw-test/recommendation.sock"


class _FakeAsyncHTTPTransport:
    """Records the transport kwargs the proxy builds (uds vs loopback TCP)."""

    last_kwargs: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        type(self).last_kwargs = kwargs

    @classmethod
    def reset(cls) -> None:
        cls.last_kwargs = {}


class _FakeAsyncClient:
    """Captures exactly what the proxy would put on the wire.

    ``Host`` is filled in from the request URL when absent, mirroring httpx, so
    a regression that strips the header shows up as ``host: localhost``.
    """

    forwards: list[dict[str, Any]] = []

    def __init__(self, **kwargs: Any) -> None:
        # The proxy builds the client with transport/timeout kwargs; the
        # transport kwargs are asserted via _FakeAsyncHTTPTransport instead.
        self.kwargs = kwargs

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        content: bytes | None = None,
    ) -> httpx.Response:
        sent = {key.lower(): value for key, value in (headers or {}).items()}
        sent.setdefault("host", urlsplit(url).netloc)
        type(self).forwards.append(
            {"method": method, "url": url, "headers": sent, "content": content}
        )
        return httpx.Response(200, json={"items": [], "pool_status": None})


@pytest.fixture(autouse=True)
def _reset_capture() -> None:
    _FakeAsyncClient.forwards = []
    _FakeAsyncHTTPTransport.reset()


def _build_proxied_app(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    config: Config | None = None,
    fake_httpx: bool = True,
) -> Any:
    """Build the main API app with the recommendation proxy enabled.

    ``fake_httpx=False`` keeps the real async client in place, so the test
    exercises the actual on-the-wire header assembly.
    """
    config = config or Config(data_dir=str(tmp_path))
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: config)
    # ``create_app`` persists the whole config when it has to generate a session
    # secret for an enabled auth gate. Point the project root at tmp_path so that
    # write can never land in the checkout (or in a real deployment's config).
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("OPENBILICLAW_RECOMMENDATION_ONLY", raising=False)
    if fake_httpx:
        monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
        monkeypatch.setattr(httpx, "AsyncHTTPTransport", _FakeAsyncHTTPTransport)
    return create_app(
        memory_manager=SimpleNamespace(load_discovery_runtime_state=lambda: {}),
        database=SimpleNamespace(),
        soul_engine=SimpleNamespace(),
        runtime_controller=SimpleNamespace(event_hub=None),
        recommendation_engine=SimpleNamespace(),
    )


def _forwarded_request(
    headers: dict[str, str], *, client: tuple[str, int] | None = None
) -> Request:
    """Rebuild the request the recommendation process receives from the proxy.

    ``client`` defaults to ``None`` (a Unix-socket peer has no address); pass a
    loopback tuple to model the Windows loopback-TCP transport instead.
    """
    scope: dict[str, Any] = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": _APPEND_PATH,
        "raw_path": _APPEND_PATH.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(key.encode(), value.encode()) for key, value in headers.items()],
        "client": client,
        "server": ("localhost", 80),
    }
    return Request(scope)


def _post_append(
    client: TestClient,
    *,
    origin: str = _ORIGIN,
    extra_headers: dict[str, str] | None = None,
) -> Any:
    return client.post(
        _APPEND_PATH,
        json={"excluded_bvids": []},
        headers={
            "X-OBC-Auth": "1",
            "Origin": origin,
            "Cookie": "obc_session=test-token",
            **(extra_headers or {}),
        },
    )


def _recommendation_process_gate() -> AuthGate:
    return AuthGate(ApiAuthConfig(enabled=True, session_secret="test-secret"), None)


def test_proxy_forwards_original_host_and_origin(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(RECOMMENDATION_SOCK_ENV, _SOCK_PATH)
    monkeypatch.delenv(RECOMMENDATION_PORT_ENV, raising=False)
    app = _build_proxied_app(monkeypatch, tmp_path)

    response = _post_append(TestClient(app, base_url=_ORIGIN))

    assert response.status_code == 200
    assert len(_FakeAsyncClient.forwards) == 1
    forwarded = _FakeAsyncClient.forwards[0]
    # The recommendation process must see the browser's own authority, not the
    # backend URL httpx would otherwise invent.
    assert forwarded["headers"]["host"] == "127.0.0.1:8420"
    assert forwarded["headers"]["origin"] == _ORIGIN


def test_proxy_hops_do_not_forward_length_or_connection(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(RECOMMENDATION_SOCK_ENV, _SOCK_PATH)
    monkeypatch.delenv(RECOMMENDATION_PORT_ENV, raising=False)
    app = _build_proxied_app(monkeypatch, tmp_path)

    _post_append(TestClient(app, base_url=_ORIGIN))

    headers = _FakeAsyncClient.forwards[0]["headers"]
    assert "content-length" not in headers
    assert "connection" not in headers


def test_proxy_uses_unix_socket_transport_on_posix(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(RECOMMENDATION_SOCK_ENV, _SOCK_PATH)
    monkeypatch.delenv(RECOMMENDATION_PORT_ENV, raising=False)
    app = _build_proxied_app(monkeypatch, tmp_path)

    _post_append(TestClient(app, base_url=_ORIGIN))

    assert _FakeAsyncClient.forwards[0]["url"] == f"http://localhost{_APPEND_PATH}"
    # The socket path (not just the URL shape) is what routes the hop to the
    # recommendation process on POSIX.
    assert _FakeAsyncHTTPTransport.last_kwargs == {"uds": _SOCK_PATH}


def test_proxy_uses_loopback_tcp_transport_when_port_is_configured(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(RECOMMENDATION_SOCK_ENV, raising=False)
    monkeypatch.setenv(RECOMMENDATION_PORT_ENV, _TCP_PORT)
    app = _build_proxied_app(monkeypatch, tmp_path)

    _post_append(TestClient(app, base_url=_ORIGIN))

    forwarded = _FakeAsyncClient.forwards[0]
    assert forwarded["url"] == f"http://127.0.0.1:{_TCP_PORT}{_APPEND_PATH}"
    assert forwarded["headers"]["host"] == "127.0.0.1:8420"
    # Loopback TCP carries no Unix socket.
    assert _FakeAsyncHTTPTransport.last_kwargs == {}


@pytest.mark.parametrize("transport", ["unix", "tcp"])
def test_proxied_request_passes_recommendation_process_csrf(
    monkeypatch, tmp_path: Path, transport: str
) -> None:
    """The whole point of forwarding Host: the rec process accepts the request."""
    if transport == "unix":
        monkeypatch.setenv(RECOMMENDATION_SOCK_ENV, _SOCK_PATH)
        monkeypatch.delenv(RECOMMENDATION_PORT_ENV, raising=False)
    else:
        monkeypatch.delenv(RECOMMENDATION_SOCK_ENV, raising=False)
        monkeypatch.setenv(RECOMMENDATION_PORT_ENV, _TCP_PORT)
    app = _build_proxied_app(monkeypatch, tmp_path)

    _post_append(TestClient(app, base_url=_ORIGIN))

    gate = AuthGate(ApiAuthConfig(enabled=True, session_secret="test-secret"), None)
    forwarded = _FakeAsyncClient.forwards[0]["headers"]
    assert gate.csrf_ok(_forwarded_request(forwarded)) is True


def test_recommendation_process_csrf_rejects_synthesised_backend_host(
    monkeypatch, tmp_path: Path
) -> None:
    """Guard: dropping Host reproduces the reported 403 (the pre-fix behaviour)."""
    gate = AuthGate(ApiAuthConfig(enabled=True, session_secret="test-secret"), None)

    broken = {
        "host": "localhost",  # what httpx invents once Host is stripped
        "origin": _ORIGIN,
        "x-obc-auth": "1",
        "cookie": "obc_session=test-token",
    }

    assert gate.csrf_ok(_forwarded_request(broken)) is False


_TERMINATOR_HEADERS = {
    "X-Forwarded-Proto": "https",
    "X-Forwarded-Host": "sushe:8443",
    "X-Forwarded-For": "203.0.113.7",
    "X-Real-IP": "203.0.113.8",
    "Forwarded": "for=203.0.113.7",
}


def _post_through_external_tls_terminator(app: Any, *, origin: str) -> Any:
    """A browser request that arrives via Caddy / any external TLS terminator."""
    return _post_append(
        TestClient(app, base_url="https://sushe:8443"),
        origin=origin,
        extra_headers=_TERMINATOR_HEADERS,
    )


def test_https_origin_is_normalised_for_the_plain_http_hop(monkeypatch, tmp_path: Path) -> None:
    """An https same-origin page must still pass the plain-HTTP hop's CSRF check."""
    monkeypatch.setenv(RECOMMENDATION_SOCK_ENV, _SOCK_PATH)
    monkeypatch.delenv(RECOMMENDATION_PORT_ENV, raising=False)
    app = _build_proxied_app(monkeypatch, tmp_path)

    _post_through_external_tls_terminator(app, origin="https://sushe:8443")

    forwarded = _FakeAsyncClient.forwards[0]["headers"]
    assert forwarded["host"] == "sushe:8443"
    assert forwarded["origin"] == "http://sushe:8443"
    assert _recommendation_process_gate().csrf_ok(_forwarded_request(forwarded)) is True


def test_cross_site_origin_is_never_normalised(monkeypatch, tmp_path: Path) -> None:
    """Normalisation is gated on the ingress verdict, so CSRF is not weakened."""
    monkeypatch.setenv(RECOMMENDATION_SOCK_ENV, _SOCK_PATH)
    monkeypatch.delenv(RECOMMENDATION_PORT_ENV, raising=False)
    app = _build_proxied_app(monkeypatch, tmp_path)

    _post_through_external_tls_terminator(app, origin="https://evil.example")

    forwarded = _FakeAsyncClient.forwards[0]["headers"]
    assert forwarded["origin"] == "https://evil.example"
    assert _recommendation_process_gate().csrf_ok(_forwarded_request(forwarded)) is False


def test_origin_with_mismatched_scheme_is_never_normalised(monkeypatch, tmp_path: Path) -> None:
    """Same authority but a different scheme is not same-origin either."""
    monkeypatch.setenv(RECOMMENDATION_SOCK_ENV, _SOCK_PATH)
    monkeypatch.delenv(RECOMMENDATION_PORT_ENV, raising=False)
    app = _build_proxied_app(monkeypatch, tmp_path)

    _post_append(TestClient(app, base_url=_ORIGIN), origin="https://127.0.0.1:8420")

    forwarded = _FakeAsyncClient.forwards[0]["headers"]
    assert forwarded["origin"] == "https://127.0.0.1:8420"
    assert _recommendation_process_gate().csrf_ok(_forwarded_request(forwarded)) is False


def test_forwarded_context_headers_are_not_relayed(monkeypatch, tmp_path: Path) -> None:
    """The hop must not inherit scheme/host claims it cannot verify."""
    monkeypatch.setenv(RECOMMENDATION_SOCK_ENV, _SOCK_PATH)
    monkeypatch.delenv(RECOMMENDATION_PORT_ENV, raising=False)
    app = _build_proxied_app(monkeypatch, tmp_path)

    _post_through_external_tls_terminator(app, origin="https://sushe:8443")

    forwarded = _FakeAsyncClient.forwards[0]["headers"]
    assert "x-forwarded-proto" not in forwarded
    assert "x-forwarded-host" not in forwarded
    # Client identity is deliberately NOT dropped: auth_core treats the presence
    # of any of these on a loopback peer as fail-closed, and stripping them here
    # would widen the local-exemption decision on the loopback-TCP transport.
    assert forwarded["x-forwarded-for"] == "203.0.113.7"
    assert forwarded["x-real-ip"] == "203.0.113.8"
    assert forwarded["forwarded"] == "for=203.0.113.7"


def test_relayed_client_identity_keeps_loopback_fail_closed(monkeypatch, tmp_path: Path) -> None:
    """Regression guard: a forwarded client IP must still block the local bypass."""
    monkeypatch.setenv(RECOMMENDATION_SOCK_ENV, _SOCK_PATH)
    monkeypatch.delenv(RECOMMENDATION_PORT_ENV, raising=False)
    app = _build_proxied_app(monkeypatch, tmp_path)

    _post_append(
        TestClient(app, base_url=_ORIGIN),
        extra_headers={"X-Forwarded-For": "203.0.113.7"},
    )

    forwarded = _FakeAsyncClient.forwards[0]["headers"]
    gate = _recommendation_process_gate()
    loopback_tcp = {"client": ("127.0.0.1", 51000)}
    assert gate.is_trusted_local(_forwarded_request(forwarded, **loopback_tcp)) is False
    # Without any forwarding header the same loopback peer is exempt, which is
    # exactly why these headers have to survive the hop.
    del forwarded["x-forwarded-for"]
    assert gate.is_trusted_local(_forwarded_request(forwarded, **loopback_tcp)) is True


def test_https_scheme_from_trusted_proxy_is_normalised(monkeypatch, tmp_path: Path) -> None:
    """High fidelity: https arrives via X-Forwarded-Proto from a trusted peer."""
    monkeypatch.setenv(RECOMMENDATION_SOCK_ENV, _SOCK_PATH)
    monkeypatch.delenv(RECOMMENDATION_PORT_ENV, raising=False)
    config = Config(
        data_dir=str(tmp_path),
        api=ApiConfig(
            auth=ApiAuthConfig(
                enabled=True,
                session_secret="test-secret",
                trusted_proxies=["127.0.0.1"],
            )
        ),
    )
    app = _build_proxied_app(monkeypatch, tmp_path, config=config)

    _post_append(
        TestClient(app, base_url="http://sushe:8443", client=("127.0.0.1", 51000)),
        origin="https://sushe:8443",
        extra_headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "sushe:8443"},
    )

    forwarded = _FakeAsyncClient.forwards[0]["headers"]
    assert forwarded["origin"] == "http://sushe:8443"
    assert "x-forwarded-proto" not in forwarded
    assert _recommendation_process_gate().csrf_ok(_forwarded_request(forwarded)) is True


class _RecordingHTTPHandler(http.server.BaseHTTPRequestHandler):
    """Records the request headers a real httpx client put on the wire."""

    records: list[dict[str, str]] = []

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers.get("content-length") or 0)
        self.rfile.read(length)
        type(self).records.append({key.lower(): value for key, value in self.headers.items()})
        payload = b'{"items": [], "pool_status": null}'
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args: object) -> None:
        pass


def test_real_httpx_forwards_explicit_host_over_loopback_tcp(monkeypatch, tmp_path: Path) -> None:
    """The contract above assumes real httpx keeps a caller-supplied ``Host``.

    Every other test swaps ``httpx.AsyncClient`` for a fake that implements that
    assumption itself. This one runs the real client against a real loopback
    server, so a future httpx change cannot silently void the fix while the
    suite stays green.
    """
    _RecordingHTTPHandler.records = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RecordingHTTPHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.delenv(RECOMMENDATION_SOCK_ENV, raising=False)
        monkeypatch.setenv(RECOMMENDATION_PORT_ENV, str(server.server_address[1]))
        app = _build_proxied_app(monkeypatch, tmp_path, fake_httpx=False)

        response = _post_append(TestClient(app, base_url=_ORIGIN))

        assert response.status_code == 200
        assert len(_RecordingHTTPHandler.records) == 1
        received = _RecordingHTTPHandler.records[0]
        assert received["host"] == "127.0.0.1:8420"
        assert received["origin"] == _ORIGIN
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
