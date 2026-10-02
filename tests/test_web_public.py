"""Contract tests for deterministic anonymous public-Web discovery."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest

from openbiliclaw.sources.web_public import (
    HTMLListingPlan,
    SafeWebTransport,
    WebFetchError,
    WebParseError,
    canonicalize_web_url,
    discover_feed_urls,
    parse_html_detail,
    parse_html_listing,
    parse_sitemap,
    parse_web_feed,
    web_content_id,
)


class _NetworkBackend:
    def __init__(self) -> None:
        self.hosts: list[str] = []

    async def connect_tcp(self, host, port, **_kwargs):
        self.hosts.append(host)
        return object()

    async def connect_unix_socket(self, path, **_kwargs):
        raise AssertionError(path)

    async def sleep(self, _seconds):
        return None


class _HTTPStream:
    def __init__(self, response: bytes) -> None:
        self.response = bytearray(response)
        self.writes: list[bytes] = []
        self.tls_server_names: list[str] = []

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        del timeout
        chunk = bytes(self.response[:max_bytes])
        del self.response[:max_bytes]
        return chunk

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        del timeout
        self.writes.append(bytes(buffer))

    async def aclose(self) -> None:
        return None

    async def start_tls(
        self,
        ssl_context,
        server_hostname: str,
        timeout: float | None = None,
    ):
        del ssl_context, timeout
        self.tls_server_names.append(server_hostname)
        return self

    def get_extra_info(self, info: str):
        del info
        return None


class _HTTPNetworkBackend:
    def __init__(self, response_body: bytes) -> None:
        self.response_body = response_body
        self.hosts: list[str] = []
        self.streams: list[_HTTPStream] = []

    async def connect_tcp(self, host, port, **_kwargs):
        del port
        self.hosts.append(str(host))
        stream = _HTTPStream(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/rss+xml\r\n"
            + f"Content-Length: {len(self.response_body)}\r\n".encode()
            + b"Connection: keep-alive\r\n\r\n"
            + self.response_body
        )
        self.streams.append(stream)
        return stream

    async def connect_unix_socket(self, path, **_kwargs):
        raise AssertionError(path)

    async def sleep(self, _seconds):
        return None


if TYPE_CHECKING:
    from collections.abc import Sequence

FIXTURES = Path(__file__).parent / "fixtures" / "web"
PUBLIC_IP = "93.184.216.34"


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


async def _public_resolver(_host: str, _port: int) -> Sequence[str]:
    return [PUBLIC_IP]


def test_canonical_url_removes_tracking_and_has_cross_recipe_hash_identity() -> None:
    first = canonicalize_web_url(
        "HTTPS://News.Example.com:443/posts/alpha?utm_source=rss&b=2&a=1#comments",
        allowed_origins={"https://news.example.com"},
    )
    second = canonicalize_web_url(
        "https://news.example.com/posts/alpha?a=1&b=2&fbclid=discard",
        allowed_origins={"https://news.example.com"},
    )

    assert first == "https://news.example.com/posts/alpha?a=1&b=2"
    assert second == first
    assert web_content_id(first) == web_content_id(second)
    assert web_content_id(first).startswith("url-")
    assert len(web_content_id(first)) == 28


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "https://user:secret@news.example.com/feed",
        "https://news.example.com:8443/feed",
        "https://127.0.0.1/feed",
        "https://[::1]/feed",
    ],
)
def test_canonical_url_rejects_non_public_shapes(url: str) -> None:
    with pytest.raises(WebFetchError, match="public Web URL") as caught:
        canonicalize_web_url(url, allowed_origins={"https://news.example.com"})

    assert caught.value.code == "invalid_url"


def test_canonical_url_requires_an_exact_recipe_origin() -> None:
    with pytest.raises(WebFetchError) as caught:
        canonicalize_web_url(
            "https://cdn.news.example.com/feed",
            allowed_origins={"https://news.example.com"},
        )

    assert caught.value.code == "origin_not_allowed"


@pytest.mark.asyncio
async def test_transport_is_anonymous_bounded_and_supports_conditional_304() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers.get("Cookie") is None
        assert request.headers.get("Authorization") is None
        assert request.headers.get("Referer") is None
        assert request.headers["If-None-Match"] == '"feed-v1"'
        assert request.headers["If-Modified-Since"] == "Mon, 10 Aug 2026 09:00:49 GMT"
        return httpx.Response(304, headers={"ETag": '"feed-v1"'})

    async with SafeWebTransport(
        allowed_origins={"https://news.example.com"},
        transport=httpx.MockTransport(handler),
        resolver=_public_resolver,
        respect_robots=False,
    ) as client:
        result = await client.fetch(
            "https://news.example.com/feed.xml",
            expected="feed",
            etag='"feed-v1"',
            last_modified="Mon, 10 Aug 2026 09:00:49 GMT",
        )

    assert result.not_modified is True
    assert result.body == b""
    assert result.status_code == 304
    assert result.etag == '"feed-v1"'
    assert result.last_modified == "Mon, 10 Aug 2026 09:00:49 GMT"
    assert len(requests) == 1


def test_sitemap_parser_keeps_entries_beyond_legacy_two_hundred_cap() -> None:
    entries = "".join(
        f"<url><loc>https://docs.example.com/post/{index}</loc></url>" for index in range(250)
    )
    sitemap = parse_sitemap(
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{entries}</urlset>',
        base_url="https://docs.example.com/sitemap.xml",
        allowed_origins=["https://docs.example.com"],
        limit=50_000,
    )

    assert len(sitemap.entries) == 250
    assert sitemap.entries[249].url == "https://docs.example.com/post/249"


@pytest.mark.asyncio
async def test_transport_revalidates_dns_and_origin_at_every_redirect() -> None:
    resolved: list[tuple[str, int]] = []
    conditionals: list[tuple[str, str | None]] = []

    async def resolver(host: str, port: int) -> Sequence[str]:
        resolved.append((host, port))
        return [PUBLIC_IP]

    def handler(request: httpx.Request) -> httpx.Response:
        conditionals.append((str(request.url.host), request.headers.get("If-None-Match")))
        if request.url.host == "news.example.com":
            return httpx.Response(302, headers={"Location": "https://feeds.example.com/main.xml"})
        assert request.url.host == "feeds.example.com"
        return httpx.Response(
            200,
            content=_fixture("rss2-valid.xml"),
            headers={"Content-Type": "application/rss+xml", "ETag": '"v2"'},
        )

    async with SafeWebTransport(
        allowed_origins={"https://news.example.com", "https://feeds.example.com"},
        transport=httpx.MockTransport(handler),
        resolver=resolver,
        respect_robots=False,
    ) as client:
        result = await client.fetch(
            "https://news.example.com/feed",
            expected="feed",
            etag='"old"',
            validator_url="https://feeds.example.com/main.xml",
        )

    assert result.final_url == "https://feeds.example.com/main.xml"
    assert result.redirect_chain == (
        "https://news.example.com/feed",
        "https://feeds.example.com/main.xml",
    )
    assert resolved == [("news.example.com", 443), ("feeds.example.com", 443)]
    assert conditionals == [("news.example.com", None), ("feeds.example.com", '"old"')]


@pytest.mark.asyncio
async def test_transport_blocks_private_or_mixed_dns_answers_before_request() -> None:
    request_count = 0

    async def resolver(_host: str, _port: int) -> Sequence[str]:
        return [PUBLIC_IP, "127.0.0.1"]

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, content=b"never")

    async with SafeWebTransport(
        allowed_origins={"https://news.example.com"},
        transport=httpx.MockTransport(handler),
        resolver=resolver,
        respect_robots=False,
    ) as client:
        with pytest.raises(WebFetchError) as caught:
            await client.fetch("https://news.example.com/feed", expected="feed")

    assert caught.value.code == "blocked_address"
    assert request_count == 0


@pytest.mark.parametrize(
    "address",
    [
        "224.0.0.1",
        "239.1.1.1",
        "ff02::1",
        "fec0::1",
        "64:ff9b::7f00:1",
        "64:ff9b:1::1",
        "::a00:1",
        "2002:7f00:1::",
    ],
)
@pytest.mark.asyncio
async def test_transport_blocks_non_unicast_and_embedded_private_addresses(
    address: str,
) -> None:
    requests = 0

    async def resolver(_host: str, _port: int) -> Sequence[str]:
        return [address]

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, content=b"never")

    async with SafeWebTransport(
        allowed_origins={"https://news.example.com"},
        transport=httpx.MockTransport(handler),
        resolver=resolver,
        respect_robots=False,
    ) as client:
        with pytest.raises(WebFetchError) as caught:
            await client.fetch("https://news.example.com/feed", expected="feed")

    assert caught.value.code == "blocked_address"
    assert requests == 0


@pytest.mark.asyncio
async def test_transport_total_deadline_includes_dns_resolution() -> None:
    async def slow_resolver(_host: str, _port: int) -> Sequence[str]:
        await asyncio.sleep(0.1)
        return [PUBLIC_IP]

    async with SafeWebTransport(
        allowed_origins={"https://news.example.com"},
        transport=httpx.MockTransport(lambda _request: httpx.Response(200)),
        resolver=slow_resolver,
        timeout_seconds=0.01,
        respect_robots=False,
    ) as client:
        with pytest.raises(WebFetchError) as caught:
            await client.fetch("https://news.example.com/feed", expected="feed")

    assert caught.value.code == "timeout"


@pytest.mark.asyncio
async def test_transport_fails_closed_on_an_unbounded_dns_answer_set() -> None:
    async def resolver(_host: str, _port: int) -> Sequence[str]:
        return [f"93.184.216.{index}" for index in range(1, 18)]

    async with SafeWebTransport(
        allowed_origins={"https://news.example.com"},
        transport=httpx.MockTransport(lambda _request: httpx.Response(200)),
        resolver=resolver,
        respect_robots=False,
    ) as client:
        with pytest.raises(WebFetchError) as caught:
            await client.fetch("https://news.example.com/feed", expected="feed")

    assert caught.value.code == "dns_error"


@pytest.mark.asyncio
async def test_socket_backend_connects_to_validated_ip_not_original_hostname() -> None:
    from openbiliclaw.sources.web_public import _PinnedNetworkBackend

    delegate = _NetworkBackend()
    backend = _PinnedNetworkBackend(delegate)  # type: ignore[arg-type]
    backend.pin("news.example.com", 443, [PUBLIC_IP])

    await backend.connect_tcp("news.example.com", 443)

    assert delegate.hosts == [PUBLIC_IP]


@pytest.mark.asyncio
async def test_default_transport_reconnects_to_each_new_pin_and_preserves_host_and_sni() -> None:
    from openbiliclaw.sources.web_public import (
        _PinnedAsyncHTTPTransport,
        _PinnedNetworkBackend,
    )

    body = _fixture("rss2-valid.xml")
    delegate = _HTTPNetworkBackend(body)
    backend = _PinnedNetworkBackend(delegate)  # type: ignore[arg-type]
    transport = _PinnedAsyncHTTPTransport(backend)
    answers = iter(["93.184.216.34", "93.184.216.35"])

    async def resolver(host: str, port: int) -> Sequence[str]:
        address = next(answers)
        backend.pin(host, port, [address])
        return [address]

    async with SafeWebTransport(
        allowed_origins={"https://news.example.com"},
        transport=transport,
        resolver=resolver,
        respect_robots=False,
    ) as client:
        await client.fetch("https://news.example.com/feed", expected="feed")
        await client.fetch("https://news.example.com/feed", expected="feed")

    assert delegate.hosts == ["93.184.216.34", "93.184.216.35"]
    assert [stream.tls_server_names for stream in delegate.streams] == [
        ["news.example.com"],
        ["news.example.com"],
    ]
    for stream in delegate.streams:
        wire = b"".join(stream.writes)
        assert b"Host: news.example.com" in wire


@pytest.mark.asyncio
async def test_transport_obeys_robots_and_caches_policy_per_origin() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200,
                content=b"User-agent: *\nDisallow: /private\n",
                headers={"Content-Type": "text/plain"},
            )
        return httpx.Response(
            200,
            content=_fixture("rss2-valid.xml"),
            headers={"Content-Type": "application/rss+xml"},
        )

    async with SafeWebTransport(
        allowed_origins={"https://news.example.com"},
        transport=httpx.MockTransport(handler),
        resolver=_public_resolver,
    ) as client:
        await client.fetch("https://news.example.com/feed.xml", expected="feed")
        with pytest.raises(WebFetchError) as caught:
            await client.fetch("https://news.example.com/private/feed.xml", expected="feed")

    assert caught.value.code == "robots_denied"
    assert requests == ["/robots.txt", "/feed.xml"]


@pytest.mark.asyncio
async def test_transport_obeys_product_specific_robots_group() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200,
                content=(
                    b"User-agent: *\nAllow: /\n\nUser-agent: OpenBiliClaw\nDisallow: /private\n"
                ),
                headers={"Content-Type": "text/plain"},
            )
        return httpx.Response(200, content=_fixture("rss2-valid.xml"))

    async with SafeWebTransport(
        allowed_origins={"https://news.example.com"},
        transport=httpx.MockTransport(handler),
        resolver=_public_resolver,
    ) as client:
        with pytest.raises(WebFetchError) as caught:
            await client.fetch("https://news.example.com/private", expected="feed")

    assert caught.value.code == "robots_denied"
    assert requests == ["/robots.txt"]


@pytest.mark.parametrize(
    ("robots", "url"),
    [
        (b"User-agent: *\nAllow: /\nDisallow: /private\n", "/private/feed.xml"),
        (
            b"User-agent: *\nAllow: /\nDisallow: /*?preview=true$\n",
            "/article?preview=true",
        ),
        (b"User-agent: *\nDisallow: /private\n", "/pri%76ate/feed.xml"),
    ],
)
@pytest.mark.asyncio
async def test_transport_uses_longest_match_and_wildcard_robots_rules(
    robots: bytes,
    url: str,
) -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, content=robots, headers={"Content-Type": "text/plain"})
        return httpx.Response(200, content=_fixture("rss2-valid.xml"))

    async with SafeWebTransport(
        allowed_origins={"https://news.example.com"},
        transport=httpx.MockTransport(handler),
        resolver=_public_resolver,
    ) as client:
        with pytest.raises(WebFetchError) as caught:
            await client.fetch(f"https://news.example.com{url}", expected="feed")

    assert caught.value.code == "robots_denied"
    assert requests == ["/robots.txt"]


@pytest.mark.asyncio
async def test_transport_matches_adversarial_robots_glob_without_backtracking() -> None:
    robots = ("User-agent: *\nDisallow: /" + "*a" * 28 + "b$\n").encode()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, content=robots, headers={"Content-Type": "text/plain"})
        return httpx.Response(
            200,
            content=_fixture("rss2-valid.xml"),
            headers={"Content-Type": "application/rss+xml"},
        )

    async with SafeWebTransport(
        allowed_origins={"https://news.example.com"},
        transport=httpx.MockTransport(handler),
        resolver=_public_resolver,
    ) as client:
        artifact = await client.fetch(
            "https://news.example.com/" + "a" * 28 + "c",
            expected="feed",
        )

    assert artifact.status_code == 200


@pytest.mark.asyncio
async def test_transport_does_not_treat_incomplete_allow_prefix_as_match() -> None:
    robots = b"User-agent: *\nDisallow: /private\nAllow: /private/public\n"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, content=robots, headers={"Content-Type": "text/plain"})
        return httpx.Response(200, content=_fixture("rss2-valid.xml"))

    async with SafeWebTransport(
        allowed_origins={"https://news.example.com"},
        transport=httpx.MockTransport(handler),
        resolver=_public_resolver,
    ) as client:
        with pytest.raises(WebFetchError) as caught:
            await client.fetch("https://news.example.com/private/pub", expected="feed")

    assert caught.value.code == "robots_denied"


@pytest.mark.asyncio
async def test_transport_honors_terminal_anchor_after_trailing_wildcard() -> None:
    robots = b"User-agent: *\nDisallow: /private*$\n"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, content=robots, headers={"Content-Type": "text/plain"})
        return httpx.Response(200, content=_fixture("rss2-valid.xml"))

    async with SafeWebTransport(
        allowed_origins={"https://news.example.com"},
        transport=httpx.MockTransport(handler),
        resolver=_public_resolver,
    ) as client:
        with pytest.raises(WebFetchError) as caught:
            await client.fetch("https://news.example.com/private/path", expected="feed")

    assert caught.value.code == "robots_denied"


@pytest.mark.parametrize(
    ("rule", "denied"),
    [
        ("$", False),
        ("*$", True),
    ],
)
@pytest.mark.asyncio
async def test_transport_distinguishes_empty_anchor_from_wildcard_anchor(
    rule: str,
    denied: bool,
) -> None:
    robots = f"User-agent: *\nDisallow: {rule}\n".encode()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, content=robots, headers={"Content-Type": "text/plain"})
        return httpx.Response(200, content=_fixture("rss2-valid.xml"))

    async with SafeWebTransport(
        allowed_origins={"https://news.example.com"},
        transport=httpx.MockTransport(handler),
        resolver=_public_resolver,
    ) as client:
        if denied:
            with pytest.raises(WebFetchError) as caught:
                await client.fetch("https://news.example.com/feed.xml", expected="feed")
            assert caught.value.code == "robots_denied"
        else:
            artifact = await client.fetch(
                "https://news.example.com/feed.xml",
                expected="feed",
            )
            assert artifact.status_code == 200


@pytest.mark.asyncio
async def test_transport_rejects_excessive_robots_glob_complexity() -> None:
    robots = ("User-agent: *\nDisallow: /" + "*segment" * 65 + "\n").encode()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, content=robots, headers={"Content-Type": "text/plain"})
        raise AssertionError("target request must remain fail-closed")

    async with SafeWebTransport(
        allowed_origins={"https://news.example.com"},
        transport=httpx.MockTransport(handler),
        resolver=_public_resolver,
    ) as client:
        with pytest.raises(WebFetchError) as caught:
            await client.fetch("https://news.example.com/feed.xml", expected="feed")

    assert caught.value.code == "robots_unavailable"


@pytest.mark.asyncio
async def test_transport_matches_long_repeated_prefix_glob_without_rescanning() -> None:
    robots = ("User-agent: *\nDisallow: /*" + "a" * 4_000 + "b$\n").encode()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, content=robots, headers={"Content-Type": "text/plain"})
        return httpx.Response(
            200,
            content=_fixture("rss2-valid.xml"),
            headers={"Content-Type": "application/rss+xml"},
        )

    async with SafeWebTransport(
        allowed_origins={"https://news.example.com"},
        transport=httpx.MockTransport(handler),
        resolver=_public_resolver,
    ) as client:
        artifact = await client.fetch(
            "https://news.example.com/" + "a" * 8_000 + "c",
            expected="feed",
        )

    assert artifact.status_code == 200


@pytest.mark.asyncio
async def test_transport_applies_supported_robots_pacing(monkeypatch) -> None:
    import openbiliclaw.sources.web_public as web_public

    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(web_public.asyncio, "sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200,
                content=b"User-agent: OpenBiliClaw\nCrawl-delay: 2\nAllow: /\n",
                headers={"Content-Type": "text/plain"},
            )
        return httpx.Response(
            200,
            content=_fixture("rss2-valid.xml"),
            headers={"Content-Type": "application/rss+xml"},
        )

    async with SafeWebTransport(
        allowed_origins={"https://news.example.com"},
        transport=httpx.MockTransport(handler),
        resolver=_public_resolver,
    ) as client:
        await client.fetch("https://news.example.com/first.xml", expected="feed")
        await client.fetch("https://news.example.com/second.xml", expected="feed")

    assert len(sleeps) == 1
    assert 1.9 <= sleeps[0] <= 2.0


@pytest.mark.asyncio
async def test_transport_fails_closed_when_robots_is_temporarily_unavailable() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(503)

    async with SafeWebTransport(
        allowed_origins={"https://news.example.com"},
        transport=httpx.MockTransport(handler),
        resolver=_public_resolver,
    ) as client:
        with pytest.raises(WebFetchError) as caught:
            await client.fetch("https://news.example.com/feed.xml", expected="feed")

    assert caught.value.code == "robots_unavailable"
    assert requests == ["/robots.txt"]


@pytest.mark.asyncio
async def test_transport_blocks_an_off_recipe_redirect_before_following_it() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://outside.example/feed"})

    async with SafeWebTransport(
        allowed_origins={"https://news.example.com"},
        transport=httpx.MockTransport(handler),
        resolver=_public_resolver,
        respect_robots=False,
    ) as client:
        with pytest.raises(WebFetchError) as caught:
            await client.fetch("https://news.example.com/feed", expected="feed")

    assert caught.value.code == "origin_not_allowed"
    assert requests == ["https://news.example.com/feed"]


@pytest.mark.asyncio
async def test_transport_rejects_content_encoding_before_stream_iteration() -> None:
    class NeverReadStream(httpx.AsyncByteStream):
        def __init__(self) -> None:
            self.iterated = False

        async def __aiter__(self):
            self.iterated = True
            yield b"compressed bytes must never be read"

        async def aclose(self) -> None:
            return None

    stream = NeverReadStream()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Accept-Encoding"] == "identity"
        return httpx.Response(
            200,
            stream=stream,
            headers={"Content-Type": "application/rss+xml", "Content-Encoding": "gzip"},
        )

    async with SafeWebTransport(
        allowed_origins={"https://news.example.com"},
        transport=httpx.MockTransport(handler),
        resolver=_public_resolver,
        respect_robots=False,
    ) as client:
        with pytest.raises(WebFetchError) as caught:
            await client.fetch("https://news.example.com/feed", expected="feed")

    assert caught.value.code == "unsupported_content_encoding"
    assert stream.iterated is False


@pytest.mark.asyncio
async def test_transport_rejects_html_when_a_feed_was_expected() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"<html><title>Sign in</title></html>",
            headers={"Content-Type": "text/html"},
        )

    async with SafeWebTransport(
        allowed_origins={"https://news.example.com"},
        transport=httpx.MockTransport(handler),
        resolver=_public_resolver,
        respect_robots=False,
    ) as client:
        with pytest.raises(WebFetchError) as caught:
            await client.fetch("https://news.example.com/feed", expected="feed")

    assert caught.value.code == "unsupported_content_type"


@pytest.mark.asyncio
async def test_transport_sniffs_a_feed_only_when_content_type_is_missing_or_generic() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_fixture("atom-valid.xml"))

    async with SafeWebTransport(
        allowed_origins={"https://blog.example.com"},
        transport=httpx.MockTransport(handler),
        resolver=_public_resolver,
        respect_robots=False,
    ) as client:
        result = await client.fetch("https://blog.example.com/feed", expected="feed")

    assert result.content_type == ""
    assert result.body.startswith(b"<?xml")


@pytest.mark.asyncio
async def test_transport_exposes_safe_rate_limit_and_timeout_taxonomy() -> None:
    def rate_limited(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "120"}, content=b"secret body")

    async with SafeWebTransport(
        allowed_origins={"https://news.example.com"},
        transport=httpx.MockTransport(rate_limited),
        resolver=_public_resolver,
        respect_robots=False,
    ) as client:
        with pytest.raises(WebFetchError) as caught:
            await client.fetch("https://news.example.com/feed", expected="feed")

    assert caught.value.code == "rate_limited"
    assert caught.value.status_code == 429
    assert caught.value.retry_after_seconds == 120
    assert "secret" not in str(caught.value)

    def times_out(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("upstream included a secret", request=request)

    async with SafeWebTransport(
        allowed_origins={"https://news.example.com"},
        transport=httpx.MockTransport(times_out),
        resolver=_public_resolver,
        respect_robots=False,
    ) as client:
        with pytest.raises(WebFetchError) as timed_out:
            await client.fetch("https://news.example.com/feed", expected="feed")

    assert timed_out.value.code == "timeout"
    assert "secret" not in str(timed_out.value)


def test_rss_parser_normalizes_content_date_url_and_url_identity() -> None:
    items = parse_web_feed(
        _fixture("rss2-valid.xml"),
        content_type="application/rss+xml",
        base_url="https://news.example.com/feed.xml",
        allowed_origins={"https://news.example.com"},
        limit=10,
    )

    assert len(items) == 1
    item = items[0]
    assert item.title == "Alpha & Omega"
    assert item.content_url == "https://news.example.com/posts/alpha?a=1&b=2"
    assert item.content_id == web_content_id(item.content_url)
    assert item.author_name == "Alice"
    assert item.body_text == "A compact summary."
    assert item.published_at == "2026-08-10T09:00:49Z"
    assert item.source_platform == "web"
    assert item.source_strategy == "web-feed"
    assert item.content_type == "article"


def test_atom_parser_prefers_alternate_link_and_nested_author() -> None:
    items = parse_web_feed(
        _fixture("atom-valid.xml"),
        content_type="application/atom+xml",
        base_url="https://blog.example.com/atom/everything/",
        allowed_origins={"https://blog.example.com"},
    )

    assert len(items) == 1
    assert items[0].content_url == "https://blog.example.com/posts/beta"
    assert items[0].author_name == "Bob"
    assert items[0].body_text == "Beta details."
    assert items[0].published_at == "2026-08-11T23:48:35Z"


def test_json_feed_parser_is_deterministic_and_does_not_use_feed_id_as_identity() -> None:
    items = parse_web_feed(
        _fixture("json-feed-valid.json"),
        content_type="application/feed+json",
        base_url="https://journal.example.com/feed.json",
        allowed_origins={"https://journal.example.com"},
    )

    assert len(items) == 1
    assert items[0].content_url == "https://journal.example.com/posts/gamma"
    assert items[0].content_id == web_content_id(items[0].content_url)
    assert items[0].content_id != "post-gamma"
    assert items[0].author_name == "Carol"


@pytest.mark.parametrize(
    ("payload", "content_type"),
    [
        (
            b"<rss><channel><item><title>No link</title></item></channel></rss>",
            "application/rss+xml",
        ),
        (
            b'{"version":"https://jsonfeed.org/version/1.1","items":'
            b'[{"id":"post-1","title":"No link"}]}',
            "application/feed+json",
        ),
    ],
)
def test_feed_missing_item_url_never_falls_back_to_feed_document(
    payload: bytes,
    content_type: str,
) -> None:
    with pytest.raises(WebParseError) as caught:
        parse_web_feed(
            payload,
            content_type=content_type,
            base_url="https://news.example.com/feed.xml",
            allowed_origins={"https://news.example.com"},
        )

    assert caught.value.code == "schema_changed"


def test_json_feed_accepts_http_item_id_as_explicit_url_fallback() -> None:
    items = parse_web_feed(
        b'{"version":"https://jsonfeed.org/version/1.1","items":'
        b'[{"id":"https://news.example.com/post-1","title":"Has URL identity"}]}',
        content_type="application/feed+json",
        base_url="https://news.example.com/feed.json",
        allowed_origins={"https://news.example.com"},
    )

    assert [item.content_url for item in items] == ["https://news.example.com/post-1"]


@pytest.mark.parametrize(
    ("payload", "content_type", "code"),
    [
        (
            b"<!DOCTYPE rss [<!ENTITY xxe SYSTEM 'file:///etc/passwd'>]><rss/>",
            "application/xml",
            "unsafe_xml",
        ),
        (b"<html><title>Cloud challenge</title></html>", "text/html", "unsupported_content_type"),
        (b"<not-a-feed />", "application/xml", "schema_changed"),
    ],
)
def test_feed_parser_rejects_unsafe_or_wrong_envelopes(
    payload: bytes, content_type: str, code: str
) -> None:
    with pytest.raises(WebParseError) as caught:
        parse_web_feed(
            payload,
            content_type=content_type,
            base_url="https://news.example.com/feed",
            allowed_origins={"https://news.example.com"},
        )

    assert caught.value.code == code


@pytest.mark.parametrize("encoding", ["utf-16", "utf-32"])
def test_feed_parser_rejects_non_utf8_hidden_doctype_and_entity(encoding: str) -> None:
    payload = (
        f'<?xml version="1.0" encoding="{encoding.upper()}"?>'
        '<!DOCTYPE rss [<!ENTITY expanded "EXPANDED">]>'
        "<rss version='2.0'><channel><item>"
        "<title>&expanded;</title><link>https://news.example.com/posts/one</link>"
        "</item></channel></rss>"
    ).encode(encoding)

    with pytest.raises(WebParseError) as caught:
        parse_web_feed(
            payload,
            content_type="application/rss+xml",
            base_url="https://news.example.com/feed.xml",
            allowed_origins={"https://news.example.com"},
        )

    assert caught.value.code == "unsafe_xml"


def test_sitemap_parser_keeps_lastmod_as_scheduling_metadata_only() -> None:
    sitemap = parse_sitemap(
        _fixture("sitemap-urlset-valid.xml"),
        base_url="https://docs.example.com/sitemap.xml",
        allowed_origins={"https://docs.example.com"},
    )

    assert sitemap.kind == "urlset"
    assert [entry.url for entry in sitemap.entries] == [
        "https://docs.example.com/news/alpha",
        "https://docs.example.com/news/beta",
    ]
    assert sitemap.entries[0].last_modified == "2026-08-10"
    assert sitemap.entries[1].last_modified == ""
    assert not hasattr(sitemap.entries[0], "published_at")


def test_sitemap_index_parser_returns_bounded_child_references() -> None:
    sitemap = parse_sitemap(
        _fixture("sitemap-index-valid.xml"),
        base_url="https://docs.example.com/sitemap.xml",
        allowed_origins={"https://docs.example.com"},
        limit=1,
    )

    assert sitemap.kind == "sitemapindex"
    assert len(sitemap.entries) == 1
    assert sitemap.entries[0].url == "https://docs.example.com/sitemaps/news.xml"


def test_sitemap_missing_loc_never_falls_back_to_sitemap_document() -> None:
    with pytest.raises(WebParseError) as caught:
        parse_sitemap(
            b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            b"<url><loc /></url></urlset>",
            base_url="https://docs.example.com/sitemap.xml",
            allowed_origins={"https://docs.example.com"},
        )

    assert caught.value.code == "schema_changed"


def test_html_feed_discovery_honors_exact_origins() -> None:
    urls = discover_feed_urls(
        _fixture("html-list-valid.html"),
        page_url="https://news.example.com/weblog/",
        allowed_origins={"https://news.example.com", "https://feeds.example.com"},
    )

    assert urls == [
        "https://news.example.com/feeds/news.xml",
        "https://feeds.example.com/all.atom",
    ]


def test_html_listing_parser_uses_restricted_declarative_selectors() -> None:
    items = parse_html_listing(
        _fixture("html-list-valid.html"),
        base_url="https://news.example.com/weblog/",
        allowed_origins={"https://news.example.com"},
        plan=HTMLListingPlan(
            item_selector="ul.list-news li.entry",
            link_selector="a.headline",
            summary_selector="p.summary",
            author_selector="span.byline",
            published_selector="time",
            published_attribute="datetime",
            empty_selector="p.no-results",
        ),
    )

    assert [item.title for item in items] == ["Alpha launch", "Beta launch"]
    assert items[0].content_url == "https://news.example.com/news/alpha"
    assert items[0].description == "A small, useful launch."
    assert items[0].author_name == "Dana"
    assert items[0].published_at == "2026-08-10T01:00:00Z"
    assert items[1].published_at == ""
    assert all(item.source_strategy == "web-html-list" for item in items)


def test_html_listing_distinguishes_affirmative_empty_from_schema_drift() -> None:
    plan = HTMLListingPlan(
        item_selector="ul.list-news li.entry",
        link_selector="a.headline",
        empty_selector="p.no-results",
    )
    assert (
        parse_html_listing(
            _fixture("html-list-empty.html"),
            base_url="https://news.example.com/weblog/",
            allowed_origins={"https://news.example.com"},
            plan=plan,
        )
        == []
    )

    with pytest.raises(WebParseError) as caught:
        parse_html_listing(
            _fixture("html-schema-drift.html"),
            base_url="https://news.example.com/weblog/",
            allowed_origins={"https://news.example.com"},
            plan=plan,
        )

    assert caught.value.code == "schema_changed"


def test_html_listing_missing_href_never_falls_back_to_listing_document() -> None:
    with pytest.raises(WebParseError) as caught:
        parse_html_listing(
            "<article><a>Title without href</a></article>",
            base_url="https://news.example.com/listing/",
            allowed_origins={"https://news.example.com"},
            plan=HTMLListingPlan(item_selector="article", link_selector="a"),
        )

    assert caught.value.code == "schema_changed"


def test_html_listing_rejects_unsupported_css_features() -> None:
    with pytest.raises(WebParseError) as caught:
        parse_html_listing(
            _fixture("html-list-valid.html"),
            base_url="https://news.example.com/weblog/",
            allowed_origins={"https://news.example.com"},
            plan=HTMLListingPlan(item_selector="li:nth-child(1)", link_selector="a"),
        )

    assert caught.value.code == "invalid_selector"


def test_html_listing_rejects_excessive_nested_scope_depth() -> None:
    document = "<article>" * 300 + '<a href="/post">Title</a>' + "</article>" * 300

    with pytest.raises(WebParseError) as caught:
        parse_html_listing(
            document,
            base_url="https://news.example.com/",
            allowed_origins={"https://news.example.com"},
            plan=HTMLListingPlan(item_selector="article", link_selector="a"),
        )

    assert caught.value.code == "document_too_complex"


def test_html_listing_rejects_excessive_matched_scopes() -> None:
    document = "".join(
        f'<article><a href="/post-{index}">Title {index}</a></article>' for index in range(513)
    )

    with pytest.raises(WebParseError) as caught:
        parse_html_listing(
            document,
            base_url="https://news.example.com/",
            allowed_origins={"https://news.example.com"},
            plan=HTMLListingPlan(item_selector="article", link_selector="a"),
        )

    assert caught.value.code == "document_too_complex"


def test_html_listing_preserves_descendant_selector_semantics_across_item_scope() -> None:
    items = parse_html_listing(
        """
        <section class="channel">
          <article class="entry">
            <div><a class="headline" href="/post">Indexed title</a></div>
          </article>
        </section>
        """,
        base_url="https://news.example.com/listing/",
        allowed_origins={"https://news.example.com"},
        plan=HTMLListingPlan(
            item_selector="section.channel article.entry",
            # The first selector component is outside the item scope.  The
            # restricted DSL has historically matched it as an ancestor.
            link_selector="section.channel a.headline",
        ),
    )

    assert [item.title for item in items] == ["Indexed title"]
    assert [item.content_url for item in items] == ["https://news.example.com/post"]


def test_html_listing_nested_scope_text_work_fails_closed_deterministically() -> None:
    # Every article is a valid, unique item, but asking each nested item for
    # its own subtree text would repeatedly traverse the same deep tail.  The
    # selector index keeps lookup linear and the aggregate text-work budget
    # rejects this structural amplification without relying on wall-clock
    # timing assertions.
    nested = "".join(f'<article><a href="/post-{index}">Title {index}</a>' for index in range(250))
    filler = "<span>filler</span>" * 1_000
    document = nested + filler + "</article>" * 250

    with pytest.raises(WebParseError) as caught:
        parse_html_listing(
            document,
            base_url="https://news.example.com/",
            allowed_origins={"https://news.example.com"},
            plan=HTMLListingPlan(
                item_selector="article",
                link_selector="a",
                summary_selector="article",
            ),
            limit=200,
        )

    assert caught.value.code == "document_too_complex"


def test_html_detail_parser_uses_same_origin_canonical_and_structured_date() -> None:
    item = parse_html_detail(
        _fixture("html-detail-valid.html"),
        page_url="https://news.example.com/news/original",
        allowed_origins={"https://news.example.com"},
    )

    assert item is not None
    assert item.title == "A deterministic web article"
    assert item.content_url == "https://news.example.com/news/deterministic"
    assert item.content_id == web_content_id(item.content_url)
    assert item.description == "Metadata summary from Open Graph."
    assert item.author_name == "Frances"
    assert item.published_at == "2026-08-08T07:30:00Z"
    assert item.source_strategy == "web-detail"


def test_html_detail_never_turns_discovery_or_sitemap_time_into_publication_time() -> None:
    item = parse_html_detail(
        b"<html><head><title>Undated article</title></head></html>",
        page_url="https://news.example.com/news/undated",
        allowed_origins={"https://news.example.com"},
    )

    assert item is not None
    assert item.published_at == ""
    assert item.published_label == ""
