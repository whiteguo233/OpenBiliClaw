"""Read-only public Web URL recipe probe contracts."""

from __future__ import annotations

import asyncio
from functools import partial
from typing import TYPE_CHECKING

import httpx
import pytest

import openbiliclaw.sources.web_public as web_public
from openbiliclaw.sources.web_probe import probe_public_web_url
from openbiliclaw.sources.web_public import SafeWebTransport, infer_html_listing_plan
from openbiliclaw.sources.web_recipe import build_web_recipe

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

PUBLIC_IP = "93.184.216.34"


async def _public_resolver(_host: str, _port: int) -> Sequence[str]:
    return [PUBLIC_IP]


def _transport_factory(
    handler: Callable[[httpx.Request], httpx.Response],
):
    return partial(
        SafeWebTransport,
        transport=httpx.MockTransport(handler),
        resolver=_public_resolver,
        respect_robots=False,
    )


def _rss(*, title: str = "First post") -> bytes:
    return f"""<?xml version="1.0"?>
    <rss version="2.0"><channel><title>Example journal</title>
      <item><title>{title}</title><link>https://news.example.com/posts/first</link>
      <author>Alice</author><pubDate>Mon, 10 Aug 2026 09:00:49 GMT</pubDate></item>
    </channel></rss>""".encode()


def _sitemap(*urls: str) -> bytes:
    rows = "".join(f"<url><loc>{url}</loc></url>" for url in urls)
    return (f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{rows}</urlset>').encode()


def _editable_recipe(candidate: dict[str, object]) -> dict[str, object]:
    recipe = candidate["recipe"]
    assert isinstance(recipe, dict)
    return build_web_recipe(recipe)


@pytest.mark.asyncio
async def test_probe_detects_a_direct_feed_and_returns_compiler_ready_recipe() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(
            200,
            content=_rss(),
            headers={"Content-Type": "application/rss+xml"},
        )

    result = await probe_public_web_url(
        "https://news.example.com/feed.xml",
        transport_factory=_transport_factory(handler),
    )
    payload = result.to_dict()

    assert payload["status"] == "ready"
    assert payload["requested_url"] == "https://news.example.com/feed.xml"
    assert payload["final_url"] == "https://news.example.com/feed.xml"
    assert requests == ["https://news.example.com/feed.xml"]
    assert len(payload["candidates"]) == 1
    candidate = payload["candidates"][0]
    assert candidate["strategy"] == "feed"
    assert candidate["discovery_method"] == "direct"
    assert candidate["confidence"] == "high"
    assert candidate["sample_items"] == [
        {
            "title": "First post",
            "content_url": "https://news.example.com/posts/first",
            "author_name": "Alice",
            "published_at": "2026-08-10T09:00:49Z",
        }
    ]
    assert _editable_recipe(candidate)["strategy"] == "feed"


@pytest.mark.asyncio
async def test_probe_treats_a_valid_empty_feed_as_high_confidence_structure_evidence() -> None:
    empty_feed = b"<rss version='2.0'><channel><title>Quiet journal</title></channel></rss>"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=empty_feed,
            headers={"Content-Type": "application/rss+xml"},
        )

    result = await probe_public_web_url(
        "https://news.example.com/feed.xml",
        transport_factory=_transport_factory(handler),
    )
    candidate = result.to_dict()["candidates"][0]

    assert result.status == "ready"
    assert candidate["confidence"] == "high"
    assert candidate["sample_items"] == []


@pytest.mark.asyncio
async def test_probe_detects_a_direct_sitemap_without_detail_fanout() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(
            200,
            content=_sitemap(
                "https://news.example.com/posts/alpha-story",
                "https://news.example.com/posts/beta-story",
            ),
            headers={"Content-Type": "application/xml"},
        )

    result = await probe_public_web_url(
        "https://news.example.com/sitemap.xml",
        transport_factory=_transport_factory(handler),
    )
    payload = result.to_dict()

    assert payload["status"] == "ready"
    assert requests == ["https://news.example.com/sitemap.xml"]
    candidate = payload["candidates"][0]
    assert candidate["strategy"] == "sitemap"
    assert candidate["discovery_method"] == "direct"
    assert [item["content_url"] for item in candidate["sample_items"]] == [
        "https://news.example.com/posts/alpha-story",
        "https://news.example.com/posts/beta-story",
    ]
    assert _editable_recipe(candidate)["strategy"] == "sitemap"


@pytest.mark.asyncio
async def test_probe_discovers_html_alternate_feed_and_keeps_requests_bounded() -> None:
    requests: list[str] = []
    page = b"""<!doctype html><html><head><title>Example News</title>
      <link rel="alternate" type="application/rss+xml" href="/feed.xml">
      <link rel="alternate" type="application/atom+xml" href="/broken.xml">
      <link rel="alternate" type="application/rss+xml" href="/third.xml">
      </head><body><main>Welcome</main></body></html>"""

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/journal":
            return httpx.Response(200, content=page, headers={"Content-Type": "text/html"})
        if request.url.path == "/feed.xml":
            return httpx.Response(
                200,
                content=_rss(),
                headers={"Content-Type": "application/rss+xml"},
            )
        return httpx.Response(404, content=b"SECRET-UPSTREAM-BODY")

    result = await probe_public_web_url(
        "https://news.example.com/journal",
        transport_factory=_transport_factory(handler),
    )
    payload = result.to_dict()

    assert payload["page_title"] == "Example News"
    alternate = [
        candidate
        for candidate in payload["candidates"]
        if candidate["discovery_method"] == "html_alternate"
    ]
    assert len(alternate) == 1
    assert alternate[0]["entry_url"] == "https://news.example.com/feed.xml"
    assert len(requests) <= 5
    assert "/third.xml" not in requests


@pytest.mark.asyncio
async def test_probe_checks_only_same_origin_well_known_sitemap() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path == "/blog":
            return httpx.Response(
                200,
                content=b"<html><head><title>Journal</title></head><body>Hi</body></html>",
                headers={"Content-Type": "text/html"},
            )
        if request.url.path == "/sitemap.xml":
            return httpx.Response(
                200,
                content=_sitemap(
                    "https://news.example.com/posts/one",
                    "https://news.example.com/posts/two",
                ),
                headers={"Content-Type": "application/xml"},
            )
        raise AssertionError(str(request.url))

    result = await probe_public_web_url(
        "https://news.example.com/blog",
        transport_factory=_transport_factory(handler),
    )
    candidate = result.to_dict()["candidates"][0]

    assert candidate["strategy"] == "sitemap"
    assert candidate["discovery_method"] == "well_known"
    assert requests == [
        "https://news.example.com/blog",
        "https://news.example.com/sitemap.xml",
    ]


@pytest.mark.asyncio
async def test_probe_infers_static_listing_only_with_two_distinct_same_origin_items() -> None:
    page = b"""<!doctype html><html><head><title>Engineering Notes</title></head><body>
      <main>
        <article class="post-card"><h2><a href="/notes/alpha">Alpha</a></h2></article>
        <article class="post-card"><h2><a href="/notes/beta">Beta</a></h2></article>
        <article class="post-card"><h2><a href="https://other.example.net/foreign">No</a></h2></article>
      </main></body></html>"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/notes":
            return httpx.Response(200, content=page, headers={"Content-Type": "text/html"})
        return httpx.Response(404)

    result = await probe_public_web_url(
        "https://news.example.com/notes",
        transport_factory=_transport_factory(handler),
    )
    listing = next(
        candidate
        for candidate in result.to_dict()["candidates"]
        if candidate["strategy"] == "listing"
    )

    assert listing["discovery_method"] == "html_structure"
    assert listing["confidence"] == "medium"
    assert [item["title"] for item in listing["sample_items"]] == ["Alpha", "Beta"]
    compiled = _editable_recipe(listing)
    assert compiled["config"]["listing"]["item_selector"]


@pytest.mark.asyncio
async def test_probe_does_not_infer_a_listing_from_one_usable_item() -> None:
    page = b"""<html><head><title>One item</title></head><body>
      <article class="post-card"><a href="/only">Only</a></article>
      <article class="post-card"><a href="https://foreign.example.net/no">Foreign</a></article>
      </body></html>"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(200, content=page, headers={"Content-Type": "text/html"})
        return httpx.Response(404)

    result = await probe_public_web_url(
        "https://news.example.com/",
        transport_factory=_transport_factory(handler),
    )

    assert result.status == "no_match"
    assert not result.candidates


@pytest.mark.asyncio
async def test_probe_does_not_treat_repeated_navigation_as_a_listing() -> None:
    page = b"""<html><head><title>Company</title></head><body>
      <header><nav><ul>
        <li class="menu-item"><a href="/about">About</a></li>
        <li class="menu-item"><a href="/contact">Contact</a></li>
      </ul></nav></header>
      <main><p>Welcome.</p></main>
      <footer>
        <article class="post-card"><a href="/privacy">Privacy</a></article>
        <article class="post-card"><a href="/terms">Terms</a></article>
      </footer>
      </body></html>"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(200, content=page, headers={"Content-Type": "text/html"})
        return httpx.Response(404)

    result = await probe_public_web_url(
        "https://news.example.com/",
        transport_factory=_transport_factory(handler),
    )

    assert result.status == "no_match"
    assert result.candidates == ()


@pytest.mark.asyncio
async def test_probe_listing_inference_fails_closed_on_excessive_signatures() -> None:
    rows = "".join(
        f'<div class="post-card-{index}"><a href="/posts/{index}">Post {index}</a></div>'
        for index in range(2_049)
    )
    page = f"<html><head><title>Adversarial</title></head><body>{rows}</body></html>".encode()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(200, content=page, headers={"Content-Type": "text/html"})
        return httpx.Response(404)

    result = await probe_public_web_url(
        "https://news.example.com/",
        transport_factory=_transport_factory(handler),
    )

    assert result.status == "no_match"
    assert result.candidates == ()


def test_listing_inference_parses_the_bounded_html_tree_only_once(monkeypatch) -> None:
    original = web_public._parse_html_tree
    parse_calls = 0

    def counting_parse(*args, **kwargs):
        nonlocal parse_calls
        parse_calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(web_public, "_parse_html_tree", counting_parse)
    filler = "<span>filler</span>" * 2_000
    candidates = "".join(
        f'<div class="post-card-{index}">No usable link</div>' * 2 for index in range(8)
    )

    inferred = infer_html_listing_plan(
        f"<html><body>{filler}{candidates}</body></html>",
        base_url="https://news.example.com/",
        allowed_origins={"https://news.example.com"},
    )

    assert inferred is None
    assert parse_calls == 1


def test_listing_inference_filters_overbroad_scope_before_plan_execution(monkeypatch) -> None:
    executions = 0

    def unexpected_execution(*_args, **_kwargs):
        nonlocal executions
        executions += 1
        raise AssertionError("an overbroad selector must not execute")

    monkeypatch.setattr(web_public, "_parse_html_listing_root", unexpected_execution)
    rows = "".join(f"<article>Row {index}</article>" for index in range(513))

    inferred = infer_html_listing_plan(
        f"<html><body>{rows}</body></html>",
        base_url="https://news.example.com/",
        allowed_origins={"https://news.example.com"},
    )

    assert inferred is None
    assert executions == 0


@pytest.mark.parametrize(
    ("wall_html", "secret_marker"),
    [
        ('<form id="login"><input type=password></form>', "password"),
        ('<form class="sign-in"><input type = "password"></form>', "password"),
        ("<form action='/login'><input name = 'password'></form>", "password"),
        ('<form aria-label="login"><input TYPE=PASS&#119;ORD></form>', "pass&#119;ord"),
        ('<div class="cf-chl-widget">Verify you are human</div>', "cf-chl-widget"),
        ("<h1>Verify <b>you are</b> human</h1>", "verify"),
        (
            "<p>Enable JavaScript <span>and cookies</span> to continue</p>",
            "javascript",
        ),
    ],
)
@pytest.mark.asyncio
async def test_probe_never_infers_a_listing_from_login_or_challenge_wall(
    wall_html: str,
    secret_marker: str,
) -> None:
    page = f"""<html><head><title>Access gate</title></head><body>
      {wall_html}
      <article class="post-card"><a href="/reset">Reset</a></article>
      <article class="post-card"><a href="/signup">Sign up</a></article>
      </body></html>""".encode()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(200, content=page, headers={"Content-Type": "text/html"})
        raise AssertionError("access walls must stop all follow-up probes")

    result = await probe_public_web_url(
        "https://news.example.com/",
        transport_factory=_transport_factory(handler),
    )
    payload = result.to_dict()

    assert payload["status"] == "no_match"
    assert payload["candidates"] == []
    assert secret_marker not in repr(payload).casefold()


@pytest.mark.asyncio
async def test_probe_keeps_public_feed_with_recaptcha_article_and_hidden_login_widget() -> None:
    page = b"""<html><head><title>Security journal</title>
      <link rel="alternate" type="application/rss+xml" href="/feed.xml">
      <script>// recaptcha integration docs</script></head><body>
      <form id="login" style="display: none"><input type=password></form>
      <article><a href="/captcha-explainer">How CAPTCHA works</a></article>
      </body></html>"""
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/":
            return httpx.Response(200, content=page, headers={"Content-Type": "text/html"})
        if request.url.path == "/feed.xml":
            return httpx.Response(
                200,
                content=_rss(),
                headers={"Content-Type": "application/rss+xml"},
            )
        return httpx.Response(404)

    result = await probe_public_web_url(
        "https://news.example.com/",
        transport_factory=_transport_factory(handler),
    )

    assert result.status == "ready"
    assert any(candidate.discovery_method == "html_alternate" for candidate in result.candidates)
    assert requests[:2] == ["/", "/feed.xml"]


@pytest.mark.asyncio
async def test_probe_rejects_cross_origin_redirect_without_following_it() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(
            302,
            headers={"Location": "https://cdn.example.net/private-feed.xml"},
        )

    result = await probe_public_web_url(
        "https://news.example.com/feed",
        transport_factory=_transport_factory(handler),
    )

    assert result.status == "no_match"
    assert requests == ["https://news.example.com/feed"]
    assert "cdn.example.net" not in result.detail


@pytest.mark.asyncio
async def test_probe_no_match_error_is_sanitized() -> None:
    secret_body = "TOKEN=top-secret internal=10.0.0.8"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=secret_body)

    result = await probe_public_web_url(
        "https://news.example.com/feed",
        transport_factory=_transport_factory(handler),
    )
    payload = result.to_dict()

    assert payload["status"] == "no_match"
    serialized = repr(payload)
    assert "top-secret" not in serialized
    assert "10.0.0.8" not in serialized
    assert payload["candidates"] == []


@pytest.mark.asyncio
async def test_probe_private_dns_answer_is_a_sanitized_no_match() -> None:
    requests = 0

    async def private_resolver(_host: str, _port: int) -> Sequence[str]:
        return ["127.0.0.1"]

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, content=_rss())

    factory = partial(
        SafeWebTransport,
        transport=httpx.MockTransport(handler),
        resolver=private_resolver,
        respect_robots=False,
    )
    result = await probe_public_web_url(
        "https://news.example.com/feed.xml",
        transport_factory=factory,
    )

    assert result.status == "no_match"
    assert requests == 0
    assert PUBLIC_IP not in result.detail
    assert "127.0.0.1" not in result.detail


@pytest.mark.asyncio
async def test_probe_timeout_is_one_shared_deadline_not_per_candidate_fetch() -> None:
    requests: list[str] = []
    page = b"""<html><head><title>Slow site</title>
      <link rel="alternate" type="application/rss+xml" href="/feed.xml">
      </head><body>Welcome</body></html>"""

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/":
            return httpx.Response(200, content=page, headers={"Content-Type": "text/html"})
        if request.url.path == "/feed.xml":
            await asyncio.sleep(0.3)
            return httpx.Response(
                200,
                content=_rss(),
                headers={"Content-Type": "application/rss+xml"},
            )
        raise AssertionError("deadline exhaustion must prevent later fetches")

    factory = partial(
        SafeWebTransport,
        transport=httpx.MockTransport(handler),
        resolver=_public_resolver,
        respect_robots=False,
    )
    result = await probe_public_web_url(
        "https://news.example.com/",
        timeout_seconds=0.1,
        transport_factory=factory,
    )

    assert result.status == "no_match"
    assert requests == ["/", "/feed.xml"]
