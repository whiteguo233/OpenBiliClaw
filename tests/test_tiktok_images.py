"""TikTok cover DNS, redirects, pinning, proxy routing and payload boundaries."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from openbiliclaw.runtime import tiktok_images
from openbiliclaw.runtime.image_cache import CoverFetchError, fetch_cover_bytes

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "198.18.0.1",
        "::1",
        "fe80::1",
        "::ffff:127.0.0.1",
        "64:ff9b::7f00:1",
        "224.0.0.1",
    ],
)
async def test_rejects_any_private_dns_answer(
    monkeypatch: pytest.MonkeyPatch, address: str
) -> None:
    resolver = AsyncMock(return_value=["1.1.1.1", address])
    monkeypatch.setattr(tiktok_images, "_dns_answers", resolver)
    with pytest.raises(CoverFetchError) as error:
        await tiktok_images._resolve_address(httpx.URL("https://p16.tiktokcdn.com/a"))
    assert error.value.status_code == 403


class FakeResponse:
    def __init__(self, status: int = 200, headers: dict[str, str] | None = None) -> None:
        self.status_code = status
        self.headers = headers or {"content-type": "image/jpeg"}
        self.closed = False
        self.quit_now = SimpleNamespace(set=Mock())
        self.body = b"image"

    async def aiter_content(self) -> AsyncIterator[bytes]:
        yield self.body

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    from curl_cffi import CurlOpt, ffi

    state = SimpleNamespace(sessions=[], calls=[], replies=[FakeResponse()])

    class Session:
        def __init__(self, **kwargs: object) -> None:
            options = kwargs["curl_options"]
            pointer = options[CurlOpt.CONNECT_TO]
            kwargs["curl_options"] = {CurlOpt.CONNECT_TO: [ffi.string(pointer.data).decode()]}
            state.sessions.append(kwargs)

        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        async def get(self, url: str, **kwargs: object) -> FakeResponse:
            state.calls.append((url, kwargs))
            return state.replies.pop(0)

    monkeypatch.setattr("curl_cffi.requests.AsyncSession", Session)
    state.resolve = AsyncMock(return_value="1.1.1.1")
    monkeypatch.setattr(tiktok_images, "_resolve_address", state.resolve)
    return state


@pytest.mark.parametrize("suffix", ["tiktokcdn.com", "tiktokcdn-us.com", "tiktokcdn-eu.com"])
async def test_pins_connection_and_retains_proxy_tls_and_host(
    monkeypatch: pytest.MonkeyPatch, transport: SimpleNamespace, suffix: str
) -> None:
    from curl_cffi import CurlOpt

    monkeypatch.setattr(
        tiktok_images,
        "_httpx_client_kwargs",
        lambda host: {"proxy": "http://proxy.invalid:8888", "trust_env": False},
    )
    url = f"https://p16.{suffix}/a"
    assert await fetch_cover_bytes(url) == (b"image", "image/jpeg")
    assert transport.sessions == [
        {
            "proxy": "http://proxy.invalid:8888",
            "curl_options": {CurlOpt.CONNECT_TO: [f"p16.{suffix}:443:1.1.1.1:443"]},
        }
    ]
    target, kwargs = transport.calls[0]
    assert target == url  # URL hostname remains the TLS/Host identity.
    assert kwargs["verify"] is True
    assert kwargs["allow_redirects"] is False
    assert kwargs["discard_cookies"] is True
    assert "Cookie" not in kwargs["headers"]


async def test_redirect_resolves_again_and_cannot_rebind_to_private(
    transport: SimpleNamespace,
) -> None:
    first = FakeResponse(302, {"location": "https://other.tiktokcdn.com/b"})
    transport.replies = [first]
    transport.resolve.side_effect = ["1.1.1.1", CoverFetchError(403, "private")]
    with pytest.raises(CoverFetchError):
        await fetch_cover_bytes("https://p16.tiktokcdn.com/a")
    assert transport.resolve.await_count == 2
    assert len(transport.calls) == 1
    assert first.closed


@pytest.mark.parametrize(
    "location",
    ["https://127.0.0.1/a", "https://evil.example/a", "https://p16.tiktokcdn.com:8443/a"],
)
async def test_redirect_cannot_escape_host_or_port_boundary(
    transport: SimpleNamespace, location: str
) -> None:
    transport.replies = [FakeResponse(302, {"location": location})]
    with pytest.raises(CoverFetchError):
        await fetch_cover_bytes("https://p16.tiktokcdn.com/a")
    assert len(transport.calls) == 1


async def test_stream_size_limit_closes_response(
    monkeypatch: pytest.MonkeyPatch, transport: SimpleNamespace
) -> None:
    monkeypatch.setattr(tiktok_images, "MAX_IMAGE_BYTES", 4)
    response = transport.replies[0]
    with pytest.raises(CoverFetchError) as error:
        await fetch_cover_bytes("https://p16.tiktokcdn.com/a")
    assert error.value.status_code == 413
    assert response.closed
    response.quit_now.set.assert_called_once()


@pytest.mark.parametrize("system", [False, True])
async def test_system_proxy_is_materialized_and_direct_remains_direct(
    monkeypatch: pytest.MonkeyPatch, transport: SimpleNamespace, system: bool
) -> None:
    monkeypatch.setattr(tiktok_images, "_httpx_client_kwargs", lambda host: {"trust_env": system})
    lookup = Mock(return_value="http://system-proxy.invalid:8888")
    monkeypatch.setattr("openbiliclaw.network.outbound_cli_proxy_url", lookup)
    await fetch_cover_bytes("https://p16.tiktokcdn.com/a")
    assert transport.sessions[0]["proxy"] == (lookup.return_value if system else "")
    assert lookup.call_count == int(system)


async def test_empty_image_body_is_not_success(transport: SimpleNamespace) -> None:
    response = transport.replies[0]
    response.body = b""
    with pytest.raises(CoverFetchError, match="Empty cover"):
        await fetch_cover_bytes("https://p16.tiktokcdn.com/a")
    assert response.closed


@pytest.mark.parametrize("valid", [True, False])
async def test_dns_lookup_sends_only_hostname_and_rejects_bad_envelopes(
    monkeypatch: pytest.MonkeyPatch, valid: bool
) -> None:
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "cloudflare-dns.com"
        assert dict(request.url.params) == {"name": "p16.tiktokcdn.com", "type": "A"}
        assert "cookie" not in request.headers
        return httpx.Response(
            200,
            json={
                "Status": 0 if valid else 2,
                "Answer": [{"type": 1, "data": "1.1.1.1"}],
            },
        )

    monkeypatch.setattr(tiktok_images, "_httpx_client_kwargs", lambda host: {"trust_env": False})
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    url = httpx.URL("https://p16.tiktokcdn.com/image?private-signature=not-for-dns")
    if valid:
        assert await tiktok_images._resolve_address(url) == "1.1.1.1"
    else:
        with pytest.raises(CoverFetchError):
            await tiktok_images._resolve_address(url)
