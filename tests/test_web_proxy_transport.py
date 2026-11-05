"""Feed routing must honor configured proxies without surrendering DNS pins."""

from __future__ import annotations

import httpcore
import pytest

from openbiliclaw.runtime import image_network
from openbiliclaw.sources import web_public as web
from tests.test_image_network_boundary import Stream


@pytest.mark.parametrize("scheme", ["http", "https"])
@pytest.mark.parametrize("proxy_scheme", ["http", "socks5"])
async def test_feed_proxy_pins_every_hop_and_keeps_robots(monkeypatch, scheme, proxy_scheme):
    proxy = f"{proxy_scheme}://127.0.0.1:7897"
    monkeypatch.setattr("openbiliclaw.network._outbound_mode", "custom")
    monkeypatch.setattr("openbiliclaw.network._outbound_proxy", proxy)
    resolved, connections, streams = [], [], []

    async def resolve(host, *, proxy=None):
        assert host == "feeds.example.com" and proxy == f"{proxy_scheme}://127.0.0.1:7897"
        resolved.append(host)
        return ("1.1.1.1",)

    async def poisoned(*args):
        pytest.fail("Configured proxy must not use poisoned local destination DNS")

    async def connect(self, host, port, **kwargs):
        connections.append((host, port))
        assert (host, port) == ("127.0.0.1", 7897)
        body = b"User-agent: *\nAllow: /\n" if not streams else b"<rss><channel/></rss>"
        headers = (
            b"Content-Type: text/plain" if not streams else b"Content-Type: application/rss+xml"
        )
        handshake = (
            [b"HTTP/1.1 200 Connection established\r\n\r\n"]
            if proxy_scheme == "http"
            else [b"\x05\x00", b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00"]
        )
        stream = Stream(
            handshake
            + [
                b"HTTP/1.1 200 OK\r\n"
                + headers
                + b"\r\nContent-Length: "
                + str(len(body)).encode()
                + b"\r\n\r\n"
                + body
            ]
        )
        streams.append(stream)
        return stream

    monkeypatch.setattr(image_network, "resolve_public_addresses", resolve)
    monkeypatch.setattr(web, "_system_resolver", poisoned)
    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", connect)
    async with web.SafeWebTransport(allowed_origins=[f"{scheme}://feeds.example.com"]) as transport:
        result = await transport.fetch(f"{scheme}://feeds.example.com/feed.xml", expected="feed")
    assert result.status_code == 200
    assert len(connections) == 2 and len(resolved) == 2
    for stream in streams:
        payload = b"".join(stream.writes)
        port = 443 if scheme == "https" else 80
        if proxy_scheme == "http":
            assert f"CONNECT 1.1.1.1:{port} HTTP/1.1".encode() in payload
        else:
            assert b"\x05\x01\x00\x01\x01\x01\x01\x01" + port.to_bytes(2, "big") in payload
        assert b"Host: feeds.example.com" in payload
        assert b"Cookie:" not in payload and b"Authorization:" not in payload
        assert stream.names == (["feeds.example.com"] if scheme == "https" else [])
        assert stream.closed
    assert b"GET /robots.txt" in b"".join(streams[0].writes)


@pytest.mark.parametrize(
    "mode,scheme,bypass,expected",
    [
        ("direct", "https", False, None),
        ("system", "https", False, "http://system-proxy:3128"),
        ("system", "http", False, "http://http-proxy:3128"),
        ("system", "https", True, None),
        ("custom", "https", True, "http://custom-proxy:3128"),
    ],
)
def test_feed_proxy_policy_preserves_direct_system_and_custom(
    monkeypatch, mode, scheme, bypass, expected
):
    monkeypatch.setattr("openbiliclaw.network._outbound_mode", mode)
    monkeypatch.setattr(
        "openbiliclaw.network._outbound_proxy",
        "http://custom-proxy:3128" if mode == "custom" else None,
    )
    monkeypatch.setattr(
        web,
        "getproxies",
        lambda: {"https": "http://system-proxy:3128", "http": "http://http-proxy:3128"},
    )
    monkeypatch.setattr(web, "proxy_bypass", lambda host: bypass)
    _, origin = web._normalize_request_url(f"{scheme}://feeds.example.com")
    assert web._web_proxy_for_origin(origin) == expected


@pytest.mark.parametrize("addresses", [("127.0.0.1",), ("1.1.1.1", "169.254.169.254"), ()])
async def test_proxy_dns_never_grants_private_addresses_or_falls_back(monkeypatch, addresses):
    monkeypatch.setattr("openbiliclaw.network._outbound_mode", "custom")
    monkeypatch.setattr("openbiliclaw.network._outbound_proxy", "http://127.0.0.1:7897")

    async def resolve(host, *, proxy=None):
        return addresses

    async def forbidden(*args, **kwargs):
        pytest.fail("Unsafe proxy DNS must fail before a destination socket or local fallback")

    monkeypatch.setattr(image_network, "resolve_public_addresses", resolve)
    monkeypatch.setattr(web, "_system_resolver", forbidden)
    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", forbidden)
    async with web.SafeWebTransport(allowed_origins=["https://feeds.example.com"]) as transport:
        with pytest.raises(web.WebFetchError) as error:
            await transport.fetch("https://feeds.example.com/feed.xml", expected="feed")
    assert error.value.code in {"dns_error", "blocked_address"}
