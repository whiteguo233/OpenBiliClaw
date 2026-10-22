"""SSRF regressions at the real shared cover-fetch entry point."""

from __future__ import annotations

import socket
import ssl
from typing import Any

import httpcore
import httpx
import pytest

from openbiliclaw.runtime import image_cache
from openbiliclaw.runtime import image_network as network


@pytest.fixture(autouse=True)
def isolated_network_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("openbiliclaw.network._outbound_mode", "direct")
    monkeypatch.setattr("openbiliclaw.network._outbound_proxy", None)


@pytest.mark.parametrize("suffix", ["", "."])
async def test_proxied_cover_resolves_through_proxy_without_using_poisoned_local_dns(
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
) -> None:
    sent: list[str] = []

    def poisoned(*args: Any, **kwargs: Any) -> list[Any]:
        pytest.fail("Proxied CDN must not use the poisoned/fake-IP system resolver")

    async def send(_self: Any, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        sent.append(request.url.host)
        if request.url.host == "1.1.1.1":
            kind = int(request.url.params["type"])
            return httpx.Response(
                200,
                request=request,
                json={
                    "Status": 0,
                    "TC": False,
                    "Question": [{"name": "media.cdninstagram.com" + suffix, "type": kind}],
                    "Answer": [
                        {
                            "name": "media.cdninstagram.com.",
                            "type": kind,
                            "data": "157.240.209.63"
                            if kind == 1
                            else "2a03:2880:f10b:83:face:b00c:0:25de",
                        }
                    ],
                },
            )
        assert request.extensions["image_addresses"] == (
            "157.240.209.63",
            "2a03:2880:f10b:83:face:b00c:0:25de",
        )
        assert request.extensions["image_proxy"] == "http://127.0.0.1:7897"
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=b"demo")

    monkeypatch.setattr(socket, "getaddrinfo", poisoned)
    monkeypatch.setattr(network, "getproxies", lambda: {"https": "http://127.0.0.1:7897"})
    monkeypatch.setattr(network, "proxy_bypass", lambda host: False)
    monkeypatch.setattr("openbiliclaw.network.outbound_httpx_kwargs", lambda: {"trust_env": True})
    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    assert await image_cache.fetch_cover_bytes("https://media.cdninstagram.com/a.jpg") == (
        b"demo",
        "image/jpeg",
    )
    assert sent == ["1.1.1.1", "1.1.1.1", "media.cdninstagram.com"]


@pytest.mark.parametrize(
    "bad",
    [
        "private",
        "mixed",
        "private_ipv6",
        "wrong_question",
        "truncated",
        "nxdomain",
        "boolean_status",
        "oversized",
        "malformed",
        "redirect",
        "empty",
    ],
)
async def test_proxy_dns_fails_closed_without_local_or_hostname_fallback(
    monkeypatch: pytest.MonkeyPatch,
    bad: str,
) -> None:
    client_class = httpx.AsyncClient
    queried: list[str] = []

    def reply(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "1.1.1.1" and request.url.path == "/dns-query"
        assert "cookie" not in request.headers and "authorization" not in request.headers
        assert "user-agent" not in request.headers
        queried.append(str(request.url.params["type"]))
        kind = int(request.url.params["type"])
        data: dict[str, Any] = {
            "Status": 0,
            "TC": False,
            "Question": [{"name": "media.cdninstagram.com", "type": kind}],
            "Answer": [{"type": 1, "data": "157.240.209.63"}],
        }
        if bad == "redirect":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/"})
        if bad == "oversized":
            return httpx.Response(200, content=b"x" * 65537)
        if bad == "malformed":
            return httpx.Response(200, content=b"not-json")
        if bad == "private":
            data["Answer"] = [{"type": 1, "data": "127.0.0.1"}]
        if bad == "mixed":
            data["Answer"].append({"type": 1, "data": "169.254.169.254"})
        if bad == "private_ipv6" and kind == 28:
            data["Answer"] = [{"type": 28, "data": "::ffff:127.0.0.1"}]
        if bad == "wrong_question":
            data["Question"][0]["name"] = "unrelated.example"
        if bad == "truncated":
            data["TC"] = True
        if bad == "nxdomain":
            data["Status"] = 3
        if bad == "boolean_status":
            data["Status"] = False
        if bad == "empty":
            data["Answer"] = []
        return httpx.Response(200, json=data)

    def client(**kwargs: Any) -> httpx.AsyncClient:
        assert kwargs.pop("proxy") == "http://127.0.0.1:7897"
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return client_class(transport=httpx.MockTransport(reply), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: pytest.fail("No DNS fallback"))
    with pytest.raises((network.UnsafeImageAddressError, httpx.HTTPError)):
        await network.resolve_public_addresses(
            "media.cdninstagram.com", proxy="http://127.0.0.1:7897"
        )
    assert 1 <= len(queried) <= 2


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "198.18.0.1"])
async def test_allowed_hostname_cannot_connect_to_nonpublic_ip(
    monkeypatch: pytest.MonkeyPatch, address: str
) -> None:
    sent: list[str] = []

    def resolve(*args: Any, **kwargs: Any) -> list[Any]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]

    async def send(_self: Any, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        sent.append(str(request.url))
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=b"x")

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    with pytest.raises(image_cache.CoverFetchError) as error:
        await image_cache.fetch_cover_bytes("https://media.cdninstagram.com/image.jpg")
    assert error.value.status_code == 403
    assert sent == []


@pytest.mark.parametrize(
    "address",
    [
        "::1",
        "fe80::1",
        "fc00::1",
        "ff02::1",
        "::ffff:127.0.0.1",
        "64:ff9b::a00:1",
        "2002:7f00:1::",
        "2001::1",
        "0.0.0.0",
        "224.0.0.1",
        "100.64.0.1",
        "192.0.2.1",
        "2001:db8::1",
        "255.255.255.255",
    ],
)
def test_special_address_ranges_are_not_public(address: str) -> None:
    assert not network.is_public_address(address)


class Stream(httpcore.AsyncNetworkStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.writes: list[bytes] = []
        self.names: list[str | None] = []
        self.closed = False

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        if not self.chunks:
            return b""
        chunk = self.chunks.pop(0)
        if len(chunk) > max_bytes:
            self.chunks.insert(0, chunk[max_bytes:])
        return chunk[:max_bytes]

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self.writes.append(buffer)

    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> Stream:
        assert ssl_context.verify_mode == ssl.CERT_REQUIRED and ssl_context.check_hostname
        self.names.append(server_hostname)
        return self

    async def aclose(self) -> None:
        self.closed = True


_IMAGE = b"HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\nContent-Length: 4\r\n\r\ndemo"


@pytest.mark.parametrize(
    "proxy",
    [
        None,
        "http://127.0.0.1:7897",
        "https://127.0.0.1:7897",
        "socks5://127.0.0.1:7897",
        "socks5h://127.0.0.1:7897",
    ],
)
async def test_real_transport_pins_destination_and_keeps_tls_and_host(
    monkeypatch: pytest.MonkeyPatch, proxy: str | None
) -> None:
    connections: list[tuple[str, int]] = []
    resolutions: list[str] = []
    handshake = (
        [b"\x05\x00", b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00"]
        if proxy and proxy.startswith("socks")
        else [b"HTTP/1.1 200 Connection established\r\n\r\n"]
        if proxy
        else []
    )
    stream = Stream([*handshake, _IMAGE])

    def resolve(host: str, *args: Any, **kwargs: Any) -> list[Any]:
        resolutions.append(host)
        # Rebinding on a second lookup would be private. The dialer must only
        # see the numeric first answer, never perform that second hostname lookup.
        ip = "1.1.1.1" if len(resolutions) == 1 else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))]

    async def connect(_self: Any, host: str, port: int, **kwargs: Any) -> Stream:
        connections.append((host, port))
        return stream

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", connect)

    async def proxy_resolve(host: str, selected_proxy: str) -> tuple[str, ...]:
        assert selected_proxy == proxy
        return await network.resolve_public_addresses(host)

    monkeypatch.setattr(network, "_resolve_proxy_addresses", proxy_resolve)
    monkeypatch.setattr(image_cache, "image_proxy_for_host", lambda *args, **kwargs: proxy)
    assert await image_cache.fetch_cover_bytes("https://media.cdninstagram.com/a.jpg") == (
        b"demo",
        "image/jpeg",
    )
    assert resolutions == ["media.cdninstagram.com"]
    assert connections == [("127.0.0.1", 7897)] if proxy else connections == [("1.1.1.1", 443)]
    assert stream.names[-1] == "media.cdninstagram.com"
    assert b"Host: media.cdninstagram.com" in b"".join(stream.writes)
    if proxy and proxy.startswith("http"):
        assert stream.writes[0].startswith(b"CONNECT 1.1.1.1:443 HTTP/1.1")
    if proxy and proxy.startswith("socks"):
        assert stream.writes[1] == b"\x05\x01\x00\x01\x01\x01\x01\x01\x01\xbb"
    assert stream.closed


async def test_redirect_rechecks_dns_and_never_connects_private_hop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[str] = []
    resolved: list[str] = []

    def resolve(host: str, *args: Any, **kwargs: Any) -> list[Any]:
        resolved.append(host)
        ips = ["1.1.1.1"] if host == "media.cdninstagram.com" else ["1.1.1.1", "10.0.0.1"]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in ips]

    async def send(_self: Any, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        sent.append(request.url.host)
        return httpx.Response(302, headers={"location": "https://other.fbcdn.net/a.jpg"})

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    with pytest.raises(image_cache.CoverFetchError) as error:
        await image_cache.fetch_cover_bytes("https://media.cdninstagram.com/a.jpg")
    assert error.value.status_code == 403
    assert sent == ["media.cdninstagram.com"]
    assert resolved == ["media.cdninstagram.com", "other.fbcdn.net"]


@pytest.mark.parametrize("direct", [True, False])
@pytest.mark.parametrize("mode", ["direct", "system", "custom"])
def test_original_host_owns_proxy_policy(
    monkeypatch: pytest.MonkeyPatch, direct: bool, mode: str
) -> None:
    from openbiliclaw import network as policy

    monkeypatch.setattr(policy, "_outbound_mode", mode)
    monkeypatch.setattr(policy, "_outbound_proxy", "http://127.0.0.1:9998")
    monkeypatch.setattr(network, "getproxies", lambda: {"https": "http://127.0.0.1:9999"})
    monkeypatch.setattr(network, "proxy_bypass", lambda host: False)
    result = network.image_proxy_for_host("media.cdninstagram.com", direct=direct)
    expected = (
        None
        if direct or mode == "direct"
        else ("http://127.0.0.1:9998" if mode == "custom" else "http://127.0.0.1:9999")
    )
    assert result == expected


@pytest.mark.parametrize(
    "url", ["https://media.cdninstagram.com:22/x", "https://media.fbcdn.net:8443/x"]
)
async def test_nonstandard_ports_rejected_without_dns(url: str) -> None:
    with pytest.raises(image_cache.CoverFetchError) as error:
        await image_cache.fetch_cover_bytes(url)
    assert error.value.status_code == 400
