"""Structured public-Web adapter outcomes for producer/state integration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from openbiliclaw.runtime.web_producer import WebDiscoveryProducer
from openbiliclaw.sources.protocol import SourceRecipe
from openbiliclaw.sources.web_adapter import WebSourceAdapter
from openbiliclaw.sources.web_public import WebFetchArtifact, WebFetchError
from openbiliclaw.sources.web_recipe import build_web_recipe
from openbiliclaw.storage.database import Database

FIXTURES = Path(__file__).parent / "fixtures" / "web"


class _Transport:
    artifacts: dict[str, WebFetchArtifact | BaseException] = {}
    requests: list[tuple[str, dict[str, Any]]] = []
    constructor: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        type(self).constructor = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def fetch(self, url: str, **kwargs: Any) -> WebFetchArtifact:
        type(self).requests.append((url, dict(kwargs)))
        artifact = type(self).artifacts[url]
        if isinstance(artifact, BaseException):
            raise artifact
        return artifact


def _artifact(url: str, fixture: str, content_type: str) -> WebFetchArtifact:
    return WebFetchArtifact(
        requested_url=url,
        final_url=url,
        status_code=200,
        content_type=content_type,
        body=(FIXTURES / fixture).read_bytes(),
        etag='"next"',
        last_modified="Wed, 12 Aug 2026 10:00:00 GMT",
    )


def _recipe(strategy: str, entry_url: str, **config: Any) -> SourceRecipe:
    return SourceRecipe(
        id="web-a",
        source_type="web",
        name="Example",
        strategy=strategy,
        config={
            "entry_url": entry_url,
            "allowed_origins": ["https://news.example.com"],
            "interval_minutes": 30,
            "max_items": 20,
            "max_pages": 2,
            "detail_fetch_limit": 10,
            "plan_version": 1,
            **config,
        },
    )


@pytest.fixture(autouse=True)
def _reset_transport() -> None:
    _Transport.artifacts = {}
    _Transport.requests = []
    _Transport.constructor = {}


@pytest.mark.asyncio
async def test_feed_result_is_structured_and_forwards_committed_validators() -> None:
    url = "https://news.example.com/feed.xml"
    _Transport.artifacts[url] = _artifact(url, "rss2-valid.xml", "application/rss+xml")
    adapter = WebSourceAdapter(
        llm_service=None,
        public_transport_factory=_Transport,
        request_timeout_seconds=9,
        max_response_bytes=999_999,
        max_redirects=2,
    )

    result = await adapter.fetch_result(
        _recipe("feed", url),
        profile=None,  # type: ignore[arg-type]
        limit=5,
        state={
            "etag": '"previous"',
            "last_modified": "yesterday",
            "validator_url": url,
        },
    )

    assert result.status == "ok"
    assert result.scope_complete is True
    assert len(result.items) == 1
    assert all(item.source_strategy == "web-feed" for item in result.items)
    assert result.etag == '"next"'
    assert result.validator_url == url
    assert _Transport.requests == [
        (
            url,
            {
                "expected": "feed",
                "etag": '"previous"',
                "last_modified": "yesterday",
                "validator_url": url,
            },
        )
    ]
    assert _Transport.constructor["allowed_origins"] == ["https://news.example.com"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("strategy", "content_type", "body"),
    [
        (
            "feed",
            "application/rss+xml",
            b"<rss><channel><item><title>Outside</title>"
            b"<link>https://outside.example/post</link></item></channel></rss>",
        ),
        (
            "feed",
            "application/feed+json",
            b'{"version":"https://jsonfeed.org/version/1.1","items":'
            b'[{"url":"https://news.example.com/post"}]}',
        ),
        (
            "feed",
            "application/feed+json",
            b'{"version":"https://jsonfeed.org/version/1.1","items":[null,42,"bad"]}',
        ),
        (
            "sitemap",
            "application/xml",
            b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            b"<url><loc>https://outside.example/post</loc></url></urlset>",
        ),
    ],
)
async def test_nonempty_envelope_with_no_usable_rows_is_not_committable_empty(
    strategy: str,
    content_type: str,
    body: bytes,
) -> None:
    url = "https://news.example.com/source"
    _Transport.artifacts[url] = WebFetchArtifact(
        requested_url=url,
        final_url=url,
        status_code=200,
        content_type=content_type,
        body=body,
        etag='"must-not-commit"',
    )

    result = await WebSourceAdapter(
        llm_service=None, public_transport_factory=_Transport
    ).fetch_result(_recipe(strategy, url), profile=None, limit=5)  # type: ignore[arg-type]

    assert result.status == "degraded"
    assert result.scope_complete is False
    assert result.error_code == "schema_changed"
    assert result.etag == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("strategy", "content_type", "body"),
    [
        ("feed", "application/rss+xml", b"<rss><channel></channel></rss>"),
        (
            "feed",
            "application/feed+json",
            b'{"version":"https://jsonfeed.org/version/1.1","items":[]}',
        ),
        (
            "sitemap",
            "application/xml",
            b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>',
        ),
    ],
)
async def test_genuinely_empty_envelope_remains_affirmative_empty(
    strategy: str,
    content_type: str,
    body: bytes,
) -> None:
    url = "https://news.example.com/source"
    _Transport.artifacts[url] = WebFetchArtifact(
        requested_url=url,
        final_url=url,
        status_code=200,
        content_type=content_type,
        body=body,
        etag='"safe-empty"',
    )

    result = await WebSourceAdapter(
        llm_service=None, public_transport_factory=_Transport
    ).fetch_result(_recipe(strategy, url), profile=None, limit=5)  # type: ignore[arg-type]

    assert result.status == "empty"
    assert result.scope_complete is True
    assert result.etag == '"safe-empty"'


@pytest.mark.asyncio
async def test_not_modified_is_success_without_parser_or_items() -> None:
    url = "https://news.example.com/feed.xml"
    _Transport.artifacts[url] = WebFetchArtifact(
        requested_url=url,
        final_url=url,
        status_code=304,
        content_type="",
        body=b"",
        etag='"same"',
        not_modified=True,
    )
    result = await WebSourceAdapter(
        llm_service=None, public_transport_factory=_Transport
    ).fetch_result(_recipe("feed", url), profile=None, limit=5)  # type: ignore[arg-type]
    assert result.status == "not_modified"
    assert result.items == []
    assert result.scope_complete is True


@pytest.mark.asyncio
async def test_listing_affirmative_empty_is_not_schema_failure() -> None:
    url = "https://news.example.com/weblog/"
    _Transport.artifacts[url] = _artifact(url, "html-list-empty.html", "text/html")
    result = await WebSourceAdapter(
        llm_service=None, public_transport_factory=_Transport
    ).fetch_result(
        _recipe(
            "listing",
            url,
            detail_fetch_limit=0,
            listing={
                "item_selector": "ul.list-news li.entry",
                "link_selector": "a.headline",
                "empty_selector": "p.no-results",
            },
        ),
        profile=None,  # type: ignore[arg-type]
        limit=5,
    )
    assert result.status == "empty"
    assert result.scope_complete is True


@pytest.mark.asyncio
async def test_sitemap_preserves_detail_success_when_later_detail_fails() -> None:
    root = "https://docs.example.com/sitemap.xml"
    first = "https://docs.example.com/news/alpha"
    second = "https://docs.example.com/news/beta"
    _Transport.artifacts[root] = _artifact(root, "sitemap-urlset-valid.xml", "application/xml")
    detail = (
        (FIXTURES / "html-detail-valid.html")
        .read_bytes()
        .replace(b"https://news.example.com/news/deterministic", first.encode())
    )
    _Transport.artifacts[first] = WebFetchArtifact(
        requested_url=first,
        final_url=first,
        status_code=200,
        content_type="text/html",
        body=detail,
    )
    _Transport.artifacts[second] = WebFetchError("timeout", "timed out")

    recipe = _recipe("sitemap", root)
    recipe.config["allowed_origins"] = ["https://docs.example.com"]
    result = await WebSourceAdapter(
        llm_service=None, public_transport_factory=_Transport
    ).fetch_result(recipe, profile=None, limit=5)  # type: ignore[arg-type]

    assert result.status == "degraded"
    assert result.scope_complete is False
    assert len(result.items) == 1
    assert result.items[0].source_strategy == "web-sitemap"
    assert result.error_code == "timeout"


@pytest.mark.asyncio
async def test_sitemap_continues_after_bad_detail_to_independent_url() -> None:
    root = "https://docs.example.com/sitemap.xml"
    first = "https://docs.example.com/news/alpha"
    second = "https://docs.example.com/news/beta"
    _Transport.artifacts[root] = _artifact(root, "sitemap-urlset-valid.xml", "application/xml")
    _Transport.artifacts[first] = WebFetchArtifact(
        requested_url=first,
        final_url=first,
        status_code=200,
        content_type="text/html",
        body=b"<html><body>Missing title</body></html>",
    )
    _Transport.artifacts[second] = WebFetchArtifact(
        requested_url=second,
        final_url=second,
        status_code=200,
        content_type="text/html",
        body=b"<html><head><title>Still reachable</title></head></html>",
    )
    recipe = _recipe("sitemap", root)
    recipe.config["allowed_origins"] = ["https://docs.example.com"]

    result = await WebSourceAdapter(
        llm_service=None, public_transport_factory=_Transport
    ).fetch_result(recipe, profile=None, limit=5)  # type: ignore[arg-type]

    assert result.status == "degraded"
    assert result.scope_complete is False
    assert result.error_code == "detail_missing_title"
    assert [item.content_url for item in result.items] == [second]
    assert any(url == second for url, _kwargs in _Transport.requests)


@pytest.mark.asyncio
async def test_sitemap_index_continues_after_bad_child_to_independent_child() -> None:
    root = "https://docs.example.com/sitemap.xml"
    bad_child = "https://docs.example.com/bad.xml"
    good_child = "https://docs.example.com/good.xml"
    detail = "https://docs.example.com/news/good"
    _Transport.artifacts[root] = WebFetchArtifact(
        requested_url=root,
        final_url=root,
        status_code=200,
        content_type="application/xml",
        body=(
            '<?xml version="1.0"?><sitemapindex '
            'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"<sitemap><loc>{bad_child}</loc></sitemap>"
            f"<sitemap><loc>{good_child}</loc></sitemap>"
            "</sitemapindex>"
        ).encode(),
    )
    _Transport.artifacts[bad_child] = WebFetchError("timeout", "timed out")
    _Transport.artifacts[good_child] = WebFetchArtifact(
        requested_url=good_child,
        final_url=good_child,
        status_code=200,
        content_type="application/xml",
        body=(
            '<?xml version="1.0"?><urlset '
            'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"<url><loc>{detail}</loc></url></urlset>"
        ).encode(),
    )
    _Transport.artifacts[detail] = WebFetchArtifact(
        requested_url=detail,
        final_url=detail,
        status_code=200,
        content_type="text/html",
        body=b"<html><head><title>Still reachable</title></head></html>",
    )
    recipe = _recipe("sitemap", root, max_pages=2, detail_fetch_limit=2)
    recipe.config["allowed_origins"] = ["https://docs.example.com"]

    result = await WebSourceAdapter(
        llm_service=None, public_transport_factory=_Transport
    ).fetch_result(recipe, profile=None, limit=5)  # type: ignore[arg-type]

    assert result.status == "degraded"
    assert result.scope_complete is False
    assert result.error_code == "timeout"
    assert [item.content_url for item in result.items] == [detail]
    assert any(url == good_child for url, _kwargs in _Transport.requests)


@pytest.mark.asyncio
async def test_sitemap_retry_cursor_advances_past_bad_detail_between_runs() -> None:
    root = "https://docs.example.com/sitemap.xml"
    bad = "https://docs.example.com/news/alpha"
    good = "https://docs.example.com/news/beta"
    _Transport.artifacts[root] = _artifact(root, "sitemap-urlset-valid.xml", "application/xml")
    _Transport.artifacts[bad] = WebFetchArtifact(
        requested_url=bad,
        final_url=bad,
        status_code=200,
        content_type="text/html",
        body=b"<html><body>Missing title</body></html>",
    )
    _Transport.artifacts[good] = WebFetchArtifact(
        requested_url=good,
        final_url=good,
        status_code=200,
        content_type="text/html",
        body=b"<html><head><title>Reachable next run</title></head></html>",
    )
    recipe = _recipe("sitemap", root, detail_fetch_limit=1)
    recipe.config["allowed_origins"] = ["https://docs.example.com"]
    adapter = WebSourceAdapter(llm_service=None, public_transport_factory=_Transport)

    first = await adapter.fetch_result(recipe, profile=None, limit=1)  # type: ignore[arg-type]
    second = await adapter.fetch_result(  # type: ignore[arg-type]
        recipe,
        profile=None,
        limit=1,
        state={"retry_cursor": first.retry_cursor},
    )
    _Transport.artifacts[bad] = WebFetchArtifact(
        requested_url=bad,
        final_url=bad,
        status_code=200,
        content_type="text/html",
        body=b"<html><head><title>Recovered on clean sweep</title></head></html>",
    )
    third = await adapter.fetch_result(  # type: ignore[arg-type]
        recipe,
        profile=None,
        limit=1,
        state={"retry_cursor": second.retry_cursor},
    )

    assert first.status == "degraded"
    assert first.cursor == {
        "sitemap_child_offset": 0,
        "sitemap_entry_offset": 1,
        "sitemap_root_kind": "urlset",
        "sitemap_topology": first.cursor["sitemap_topology"],
    }
    assert first.retry_cursor == {
        "sitemap_child_offset": 0,
        "sitemap_entry_offset": 1,
        "sitemap_root_kind": "urlset",
        "sitemap_topology": first.retry_cursor["sitemap_topology"],
        "sitemap_recovery_active": True,
        "sitemap_recovery_topology": first.retry_cursor["sitemap_recovery_topology"],
        "sitemap_recovery_remaining": 2,
    }
    assert [item.content_url for item in second.items] == [good]
    assert second.status == "degraded"
    assert second.error_code == "sitemap_recovery_pending"
    assert second.retry_cursor["sitemap_recovery_remaining"] == 1
    assert [item.content_url for item in third.items] == [bad]
    assert third.status == "ok"
    assert third.scope_complete is True
    assert third.retry_cursor == {}


@pytest.mark.asyncio
async def test_sitemap_retry_cursor_advances_past_bad_child_between_runs() -> None:
    root = "https://docs.example.com/sitemap.xml"
    bad_child = "https://docs.example.com/bad.xml"
    good_child = "https://docs.example.com/good.xml"
    detail = "https://docs.example.com/news/good"
    _Transport.artifacts[root] = WebFetchArtifact(
        requested_url=root,
        final_url=root,
        status_code=200,
        content_type="application/xml",
        body=(
            '<?xml version="1.0"?><sitemapindex '
            'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"<sitemap><loc>{bad_child}</loc></sitemap>"
            f"<sitemap><loc>{good_child}</loc></sitemap>"
            "</sitemapindex>"
        ).encode(),
    )
    _Transport.artifacts[bad_child] = WebFetchError("timeout", "timed out")
    _Transport.artifacts[good_child] = WebFetchArtifact(
        requested_url=good_child,
        final_url=good_child,
        status_code=200,
        content_type="application/xml",
        body=(
            '<?xml version="1.0"?><urlset '
            'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"<url><loc>{detail}</loc></url></urlset>"
        ).encode(),
    )
    _Transport.artifacts[detail] = WebFetchArtifact(
        requested_url=detail,
        final_url=detail,
        status_code=200,
        content_type="text/html",
        body=b"<html><head><title>Reachable next run</title></head></html>",
    )
    recipe = _recipe("sitemap", root, max_pages=1, detail_fetch_limit=1)
    recipe.config["allowed_origins"] = ["https://docs.example.com"]
    adapter = WebSourceAdapter(llm_service=None, public_transport_factory=_Transport)

    first = await adapter.fetch_result(recipe, profile=None, limit=1)  # type: ignore[arg-type]
    second = await adapter.fetch_result(  # type: ignore[arg-type]
        recipe,
        profile=None,
        limit=1,
        state={"retry_cursor": first.retry_cursor},
    )

    assert first.status == "degraded"
    assert first.retry_cursor["sitemap_child_offset"] == 1
    assert first.retry_cursor["sitemap_entry_offset"] == 0
    assert first.retry_cursor["sitemap_root_kind"] == "sitemapindex"
    assert first.retry_cursor["sitemap_recovery_active"] is True
    assert first.retry_cursor["sitemap_recovery_remaining"] == 2
    assert [item.content_url for item in second.items] == [detail]
    assert second.status == "degraded"
    assert second.error_code == "sitemap_recovery_pending"
    assert second.retry_cursor["sitemap_recovery_remaining"] == 1


@pytest.mark.asyncio
async def test_sitemap_index_partial_child_requires_clean_wrap_before_commit() -> None:
    root = "https://docs.example.com/sitemap.xml"
    child = "https://docs.example.com/child.xml"
    bad = "https://docs.example.com/news/bad"
    good = "https://docs.example.com/news/good"
    _Transport.artifacts[root] = WebFetchArtifact(
        requested_url=root,
        final_url=root,
        status_code=200,
        content_type="application/xml",
        body=(
            '<?xml version="1.0"?><sitemapindex '
            'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"<sitemap><loc>{child}</loc></sitemap>"
            "</sitemapindex>"
        ).encode(),
        etag='"root-v1"',
    )
    _Transport.artifacts[child] = WebFetchArtifact(
        requested_url=child,
        final_url=child,
        status_code=200,
        content_type="application/xml",
        body=(
            '<?xml version="1.0"?><urlset '
            'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"<url><loc>{bad}</loc></url><url><loc>{good}</loc></url>"
            "</urlset>"
        ).encode(),
    )
    _Transport.artifacts[bad] = WebFetchArtifact(
        requested_url=bad,
        final_url=bad,
        status_code=200,
        content_type="text/html",
        body=b"<html><body>Missing title</body></html>",
    )
    _Transport.artifacts[good] = WebFetchArtifact(
        requested_url=good,
        final_url=good,
        status_code=200,
        content_type="text/html",
        body=b"<html><head><title>Good suffix</title></head></html>",
    )
    recipe = _recipe("sitemap", root, max_pages=1, detail_fetch_limit=1)
    recipe.config["allowed_origins"] = ["https://docs.example.com"]
    adapter = WebSourceAdapter(llm_service=None, public_transport_factory=_Transport)

    first = await adapter.fetch_result(recipe, profile=None, limit=1)  # type: ignore[arg-type]
    second = await adapter.fetch_result(  # type: ignore[arg-type]
        recipe,
        profile=None,
        limit=1,
        state={"retry_cursor": first.retry_cursor},
    )
    _Transport.artifacts[bad] = WebFetchArtifact(
        requested_url=bad,
        final_url=bad,
        status_code=200,
        content_type="text/html",
        body=b"<html><head><title>Recovered prefix</title></head></html>",
    )
    third = await adapter.fetch_result(  # type: ignore[arg-type]
        recipe,
        profile=None,
        limit=1,
        state={"retry_cursor": second.retry_cursor},
    )

    assert first.status == "degraded"
    assert first.retry_cursor["sitemap_recovery_child_remaining"] == 2
    assert [item.content_url for item in second.items] == [good]
    assert second.status == "degraded"
    assert second.error_code == "sitemap_recovery_pending"
    assert second.retry_cursor["sitemap_recovery_child_remaining"] == 1
    assert [item.content_url for item in third.items] == [bad]
    assert third.status == "ok"
    assert third.scope_complete is True
    assert third.retry_cursor == {}


@pytest.mark.asyncio
async def test_sitemap_index_partial_child_stays_on_child_until_clean_sweep() -> None:
    root = "https://docs.example.com/sitemap.xml"
    child_a = "https://docs.example.com/a.xml"
    child_b = "https://docs.example.com/b.xml"
    bad = "https://docs.example.com/news/a-bad"
    good = "https://docs.example.com/news/a-good"
    other = "https://docs.example.com/news/b-good"
    _Transport.artifacts[root] = WebFetchArtifact(
        requested_url=root,
        final_url=root,
        status_code=200,
        content_type="application/xml",
        body=(
            '<?xml version="1.0"?><sitemapindex '
            'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"<sitemap><loc>{child_a}</loc></sitemap>"
            f"<sitemap><loc>{child_b}</loc></sitemap>"
            "</sitemapindex>"
        ).encode(),
    )
    _Transport.artifacts[child_a] = WebFetchArtifact(
        requested_url=child_a,
        final_url=child_a,
        status_code=200,
        content_type="application/xml",
        body=(
            '<?xml version="1.0"?><urlset '
            'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"<url><loc>{bad}</loc></url><url><loc>{good}</loc></url>"
            "</urlset>"
        ).encode(),
    )
    _Transport.artifacts[child_b] = WebFetchArtifact(
        requested_url=child_b,
        final_url=child_b,
        status_code=200,
        content_type="application/xml",
        body=(
            '<?xml version="1.0"?><urlset '
            'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"<url><loc>{other}</loc></url>"
            "</urlset>"
        ).encode(),
    )
    _Transport.artifacts[bad] = WebFetchArtifact(
        requested_url=bad,
        final_url=bad,
        status_code=200,
        content_type="text/html",
        body=b"<html><body>Missing title</body></html>",
    )
    _Transport.artifacts[good] = WebFetchArtifact(
        requested_url=good,
        final_url=good,
        status_code=200,
        content_type="text/html",
        body=b"<html><head><title>A suffix</title></head></html>",
    )
    _Transport.artifacts[other] = WebFetchArtifact(
        requested_url=other,
        final_url=other,
        status_code=200,
        content_type="text/html",
        body=b"<html><head><title>B item</title></head></html>",
    )
    recipe = _recipe("sitemap", root, max_pages=1, detail_fetch_limit=1)
    recipe.config["allowed_origins"] = ["https://docs.example.com"]
    adapter = WebSourceAdapter(llm_service=None, public_transport_factory=_Transport)

    first = await adapter.fetch_result(recipe, profile=None, limit=1)  # type: ignore[arg-type]
    second = await adapter.fetch_result(  # type: ignore[arg-type]
        recipe, profile=None, limit=1, state={"retry_cursor": first.retry_cursor}
    )
    _Transport.artifacts[bad] = WebFetchArtifact(
        requested_url=bad,
        final_url=bad,
        status_code=200,
        content_type="text/html",
        body=b"<html><head><title>A prefix recovered</title></head></html>",
    )
    third = await adapter.fetch_result(  # type: ignore[arg-type]
        recipe, profile=None, limit=1, state={"retry_cursor": second.retry_cursor}
    )
    fourth = await adapter.fetch_result(  # type: ignore[arg-type]
        recipe, profile=None, limit=1, state={"retry_cursor": third.retry_cursor}
    )

    assert first.status == "degraded"
    assert first.retry_cursor["sitemap_child_offset"] == 0
    assert first.retry_cursor["sitemap_entry_offset"] == 1
    assert first.retry_cursor["sitemap_recovery_remaining"] == 2
    assert first.retry_cursor["sitemap_recovery_child_remaining"] == 2
    assert [item.content_url for item in second.items] == [good]
    assert second.status == "degraded"
    assert second.retry_cursor["sitemap_child_offset"] == 0
    assert second.retry_cursor["sitemap_entry_offset"] == 0
    assert second.retry_cursor["sitemap_recovery_remaining"] == 2
    assert second.retry_cursor["sitemap_recovery_child_remaining"] == 1
    assert [item.content_url for item in third.items] == [bad]
    assert third.status == "degraded"
    assert third.retry_cursor["sitemap_child_offset"] == 1
    assert third.retry_cursor["sitemap_entry_offset"] == 0
    assert third.retry_cursor["sitemap_recovery_remaining"] == 1
    assert "sitemap_recovery_child_remaining" not in third.retry_cursor
    assert [item.content_url for item in fourth.items] == [other]
    assert fourth.status == "ok"
    assert fourth.scope_complete is True
    assert fourth.retry_cursor == {}


@pytest.mark.asyncio
async def test_sitemap_recovery_does_not_commit_on_empty_child_window() -> None:
    root = "https://docs.example.com/sitemap.xml"
    bad_child = "https://docs.example.com/bad.xml"
    empty_child = "https://docs.example.com/empty.xml"
    good_child = "https://docs.example.com/good.xml"
    detail = "https://docs.example.com/news/good"
    _Transport.artifacts[root] = WebFetchArtifact(
        requested_url=root,
        final_url=root,
        status_code=200,
        content_type="application/xml",
        body=(
            '<?xml version="1.0"?><sitemapindex '
            'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"<sitemap><loc>{bad_child}</loc></sitemap>"
            f"<sitemap><loc>{empty_child}</loc></sitemap>"
            f"<sitemap><loc>{good_child}</loc></sitemap>"
            "</sitemapindex>"
        ).encode(),
        etag='"root-v1"',
    )
    _Transport.artifacts[bad_child] = WebFetchError("timeout", "timed out")
    _Transport.artifacts[empty_child] = WebFetchArtifact(
        requested_url=empty_child,
        final_url=empty_child,
        status_code=200,
        content_type="application/xml",
        body=(
            b'<?xml version="1.0"?><urlset '
            b'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>'
        ),
    )
    _Transport.artifacts[good_child] = WebFetchArtifact(
        requested_url=good_child,
        final_url=good_child,
        status_code=200,
        content_type="application/xml",
        body=(
            '<?xml version="1.0"?><urlset '
            'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"<url><loc>{detail}</loc></url></urlset>"
        ).encode(),
    )
    _Transport.artifacts[detail] = WebFetchArtifact(
        requested_url=detail,
        final_url=detail,
        status_code=200,
        content_type="text/html",
        body=b"<html><head><title>Good article</title></head></html>",
    )
    recipe = _recipe("sitemap", root, max_pages=1, detail_fetch_limit=1)
    recipe.config["allowed_origins"] = ["https://docs.example.com"]
    adapter = WebSourceAdapter(llm_service=None, public_transport_factory=_Transport)

    first = await adapter.fetch_result(recipe, profile=None, limit=1)  # type: ignore[arg-type]
    second = await adapter.fetch_result(  # type: ignore[arg-type]
        recipe,
        profile=None,
        limit=1,
        state={"retry_cursor": first.retry_cursor},
    )
    third = await adapter.fetch_result(  # type: ignore[arg-type]
        recipe,
        profile=None,
        limit=1,
        state={"retry_cursor": second.retry_cursor},
    )

    assert first.status == "degraded"
    assert second.status == "degraded"
    assert second.error_code == "sitemap_recovery_pending"
    assert second.retry_cursor["sitemap_child_offset"] == 2
    assert second.retry_cursor["sitemap_recovery_remaining"] == 2
    assert [item.content_url for item in third.items] == [detail]
    assert third.status == "degraded"
    assert third.retry_cursor["sitemap_child_offset"] == 0
    assert third.retry_cursor["sitemap_recovery_remaining"] == 1


@pytest.mark.asyncio
async def test_sitemap_recovery_commits_validator_only_after_clean_full_sweep(tmp_path) -> None:
    root = "https://docs.example.com/sitemap.xml"
    bad = "https://docs.example.com/news/alpha"
    good = "https://docs.example.com/news/beta"
    _Transport.artifacts[root] = WebFetchArtifact(
        requested_url=root,
        final_url=root,
        status_code=200,
        content_type="application/xml",
        body=(
            '<?xml version="1.0"?><urlset '
            'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"<url><loc>{bad}</loc></url>"
            f"<url><loc>{good}</loc></url>"
            "</urlset>"
        ).encode(),
        etag='"root-v1"',
    )
    _Transport.artifacts[bad] = WebFetchArtifact(
        requested_url=bad,
        final_url=bad,
        status_code=200,
        content_type="text/html",
        body=b"<html><body>Missing title</body></html>",
    )
    _Transport.artifacts[good] = WebFetchArtifact(
        requested_url=good,
        final_url=good,
        status_code=200,
        content_type="text/html",
        body=b"<html><head><title>Good article</title></head></html>",
    )
    database = Database(tmp_path / "sitemap-recovery.db")
    database.initialize()
    database.save_source_recipe(
        build_web_recipe(
            {
                "name": "Recovery",
                "strategy": "sitemap",
                "config": {
                    "entry_url": root,
                    "interval_minutes": 30,
                    "max_items": 2,
                    "max_pages": 1,
                    "detail_fetch_limit": 1,
                },
            },
            recipe_id="web-recovery",
        )
    )

    class _Pipeline:
        def __init__(self) -> None:
            self.urls: list[str] = []

        def enqueue_candidates(self, items, *, source_context: str) -> int:
            assert source_context == "web-sitemap"
            self.urls.extend(item.content_url for item in items)
            return len(items)

    pipeline = _Pipeline()
    producer = WebDiscoveryProducer(
        database=database,
        adapter=WebSourceAdapter(llm_service=None, public_transport_factory=_Transport),
        candidate_pipeline=pipeline,
        enabled=True,
    )

    first = await producer.produce_if_due(limit=1, force=True)
    first_state = database.get_web_source_state("web-recovery")
    second = await producer.produce_if_due(limit=1, force=True)
    second_state = database.get_web_source_state("web-recovery")

    assert first["recipe_results"]["web-recovery"]["status"] == "degraded"
    assert second["recipe_results"]["web-recovery"]["status"] == "degraded"
    assert first_state["etag"] == ""
    assert second_state["etag"] == ""
    assert second_state["retry_cursor"]["sitemap_recovery_remaining"] == 1
    assert pipeline.urls == [good]

    _Transport.artifacts[bad] = WebFetchArtifact(
        requested_url=bad,
        final_url=bad,
        status_code=200,
        content_type="text/html",
        body=b"<html><head><title>Recovered article</title></head></html>",
    )
    third = await producer.produce_if_due(limit=1, force=True)
    final_state = database.get_web_source_state("web-recovery")

    assert third["recipe_results"]["web-recovery"]["status"] == "ok"
    assert pipeline.urls == [good, bad]
    assert final_state["etag"] == '"root-v1"'
    assert final_state["validator_url"] == root
    assert final_state["retry_cursor"] == {}


@pytest.mark.asyncio
async def test_sitemap_recovery_completes_when_changed_limit_scans_across_origin() -> None:
    root = "https://docs.example.com/sitemap.xml"
    first_url = "https://docs.example.com/news/first"
    second_url = "https://docs.example.com/news/second"
    _Transport.artifacts[root] = WebFetchArtifact(
        requested_url=root,
        final_url=root,
        status_code=200,
        content_type="application/xml",
        body=(
            '<?xml version="1.0"?><urlset '
            'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"<url><loc>{first_url}</loc></url>"
            f"<url><loc>{second_url}</loc></url>"
            "</urlset>"
        ).encode(),
    )
    _Transport.artifacts[first_url] = WebFetchArtifact(
        requested_url=first_url,
        final_url=first_url,
        status_code=200,
        content_type="text/html",
        body=b"<html><body>Missing title</body></html>",
    )
    _Transport.artifacts[second_url] = WebFetchArtifact(
        requested_url=second_url,
        final_url=second_url,
        status_code=200,
        content_type="text/html",
        body=b"<html><head><title>Second</title></head></html>",
    )
    recipe = _recipe("sitemap", root, detail_fetch_limit=2)
    recipe.config["allowed_origins"] = ["https://docs.example.com"]
    adapter = WebSourceAdapter(llm_service=None, public_transport_factory=_Transport)

    first = await adapter.fetch_result(recipe, profile=None, limit=1)  # type: ignore[arg-type]
    _Transport.artifacts[first_url] = WebFetchArtifact(
        requested_url=first_url,
        final_url=first_url,
        status_code=200,
        content_type="text/html",
        body=b"<html><head><title>First recovered</title></head></html>",
    )
    second = await adapter.fetch_result(  # type: ignore[arg-type]
        recipe,
        profile=None,
        limit=2,
        state={"retry_cursor": first.retry_cursor},
    )
    third = await adapter.fetch_result(  # type: ignore[arg-type]
        recipe,
        profile=None,
        limit=2,
        state={"retry_cursor": second.retry_cursor},
    )

    assert first.status == "degraded"
    assert first.retry_cursor["sitemap_recovery_remaining"] == 2
    assert second.status == "degraded"
    assert second.retry_cursor["sitemap_recovery_remaining"] == 1
    assert third.status == "ok"
    assert third.scope_complete is True
    assert third.retry_cursor == {}
    assert {item.content_url for item in third.items} == {first_url, second_url}


@pytest.mark.asyncio
async def test_sitemap_recovery_rebases_when_index_topology_shrinks() -> None:
    root = "https://docs.example.com/sitemap.xml"
    child_a = "https://docs.example.com/a.xml"
    child_b = "https://docs.example.com/b.xml"
    _Transport.artifacts[root] = WebFetchArtifact(
        requested_url=root,
        final_url=root,
        status_code=200,
        content_type="application/xml",
        body=(
            '<?xml version="1.0"?><sitemapindex '
            'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"<sitemap><loc>{child_a}</loc></sitemap>"
            f"<sitemap><loc>{child_b}</loc></sitemap>"
            "</sitemapindex>"
        ).encode(),
    )
    empty_child = (
        b'<?xml version="1.0"?><urlset '
        b'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>'
    )
    for child in (child_a, child_b):
        _Transport.artifacts[child] = WebFetchArtifact(
            requested_url=child,
            final_url=child,
            status_code=200,
            content_type="application/xml",
            body=empty_child,
        )
    recipe = _recipe("sitemap", root, max_pages=2, detail_fetch_limit=1)
    recipe.config["allowed_origins"] = ["https://docs.example.com"]

    adapter = WebSourceAdapter(
        llm_service=None,
        public_transport_factory=_Transport,
    )
    first = await adapter.fetch_result(
        recipe,
        profile=None,
        limit=1,
        state={
            "retry_cursor": {
                "sitemap_child_offset": 3,
                "sitemap_entry_offset": 0,
                "sitemap_recovery_active": True,
                # A pre-counter recovery record from the old five-child
                # topology must safely rebase onto this two-child snapshot.
                "sitemap_recovery_origin_child_offset": 3,
                "sitemap_recovery_origin_entry_offset": 0,
                "sitemap_recovery_wrapped": False,
            }
        },
    )  # type: ignore[arg-type]

    second = await adapter.fetch_result(  # type: ignore[arg-type]
        recipe,
        profile=None,
        limit=1,
        state={"retry_cursor": first.retry_cursor},
    )

    assert first.status == "degraded"
    assert first.error_code == "sitemap_recovery_pending"
    assert first.retry_cursor["sitemap_child_offset"] == 1
    assert first.retry_cursor["sitemap_entry_offset"] == 0
    assert second.status == "empty"
    assert second.scope_complete is True
    assert second.retry_cursor == {}
    assert {url for url, _kwargs in _Transport.requests} == {root, child_a, child_b}


@pytest.mark.asyncio
async def test_sitemap_index_topology_change_rebases_child_entry_offset() -> None:
    root = "https://docs.example.com/sitemap.xml"
    child = "https://docs.example.com/current.xml"
    prefix = "https://docs.example.com/news/prefix"
    suffix = "https://docs.example.com/news/suffix"
    _Transport.artifacts[root] = WebFetchArtifact(
        requested_url=root,
        final_url=root,
        status_code=200,
        content_type="application/xml",
        body=(
            '<?xml version="1.0"?><sitemapindex '
            'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"<sitemap><loc>{child}</loc></sitemap>"
            "</sitemapindex>"
        ).encode(),
    )
    _Transport.artifacts[child] = WebFetchArtifact(
        requested_url=child,
        final_url=child,
        status_code=200,
        content_type="application/xml",
        body=(
            '<?xml version="1.0"?><urlset '
            'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"<url><loc>{prefix}</loc></url><url><loc>{suffix}</loc></url>"
            "</urlset>"
        ).encode(),
    )
    for detail in (prefix, suffix):
        _Transport.artifacts[detail] = WebFetchArtifact(
            requested_url=detail,
            final_url=detail,
            status_code=200,
            content_type="text/html",
            body=f"<html><head><title>{detail}</title></head></html>".encode(),
        )
    recipe = _recipe("sitemap", root, max_pages=1, detail_fetch_limit=1)
    recipe.config["allowed_origins"] = ["https://docs.example.com"]

    result = await WebSourceAdapter(
        llm_service=None,
        public_transport_factory=_Transport,
    ).fetch_result(
        recipe,
        profile=None,
        limit=1,
        state={
            "retry_cursor": {
                "sitemap_child_offset": 0,
                "sitemap_entry_offset": 1,
                "sitemap_root_kind": "sitemapindex",
                "sitemap_recovery_active": True,
                "sitemap_recovery_topology": "stale-root-topology",
                "sitemap_recovery_remaining": 1,
                "sitemap_recovery_child_topology": "stale-child-topology",
                "sitemap_recovery_child_remaining": 1,
            }
        },
    )  # type: ignore[arg-type]

    assert [item.content_url for item in result.items] == [prefix]
    assert result.status == "degraded"
    assert result.error_code == "sitemap_recovery_pending"
    assert result.retry_cursor["sitemap_entry_offset"] == 1
    assert result.retry_cursor["sitemap_recovery_child_remaining"] == 1


@pytest.mark.asyncio
async def test_sitemap_index_cursor_rotates_across_bounded_child_pages() -> None:
    root = "https://news.example.com/sitemap.xml"
    child_a = "https://news.example.com/a.xml"
    child_b = "https://news.example.com/b.xml"
    detail_a = "https://news.example.com/a"
    detail_b = "https://news.example.com/b"
    _Transport.artifacts[root] = WebFetchArtifact(
        requested_url=root,
        final_url=root,
        status_code=200,
        content_type="application/xml",
        body=(
            '<?xml version="1.0"?><sitemapindex '
            'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"<sitemap><loc>{child_a}</loc></sitemap>"
            f"<sitemap><loc>{child_b}</loc></sitemap>"
            "</sitemapindex>"
        ).encode(),
        etag='"root-v1"',
    )
    for child, detail in ((child_a, detail_a), (child_b, detail_b)):
        _Transport.artifacts[child] = WebFetchArtifact(
            requested_url=child,
            final_url=child,
            status_code=200,
            content_type="application/xml",
            body=(
                '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                f"<url><loc>{detail}</loc></url></urlset>"
            ).encode(),
        )
        _Transport.artifacts[detail] = WebFetchArtifact(
            requested_url=detail,
            final_url=detail,
            status_code=200,
            content_type="text/html",
            body=f"<html><head><title>{detail}</title></head></html>".encode(),
        )
    adapter = WebSourceAdapter(llm_service=None, public_transport_factory=_Transport)
    recipe = _recipe("sitemap", root, max_pages=1, detail_fetch_limit=1)

    first = await adapter.fetch_result(recipe, profile=None, limit=1)  # type: ignore[arg-type]
    second = await adapter.fetch_result(  # type: ignore[arg-type]
        recipe,
        profile=None,
        limit=1,
        state={
            "cursor": first.cursor,
            "etag": first.etag,
            "validator_url": first.validator_url,
        },
    )
    await adapter.fetch_result(  # type: ignore[arg-type]
        recipe,
        profile=None,
        limit=1,
        state={
            "cursor": second.cursor,
            "etag": second.etag,
            "validator_url": second.validator_url,
        },
    )

    assert [item.content_url for item in first.items] == [detail_a]
    assert first.cursor == {
        "sitemap_child_offset": 1,
        "sitemap_entry_offset": 0,
        "sitemap_root_kind": "sitemapindex",
        "sitemap_topology": first.cursor["sitemap_topology"],
    }
    assert [item.content_url for item in second.items] == [detail_b]
    assert second.cursor == {
        "sitemap_child_offset": 0,
        "sitemap_entry_offset": 0,
        "sitemap_root_kind": "sitemapindex",
        "sitemap_topology": first.cursor["sitemap_topology"],
    }
    root_requests = [kwargs for url, kwargs in _Transport.requests if url == root]
    assert root_requests == [
        {"expected": "sitemap", "etag": "", "last_modified": "", "validator_url": ""},
        {"expected": "sitemap", "etag": "", "last_modified": "", "validator_url": ""},
        {"expected": "sitemap", "etag": "", "last_modified": "", "validator_url": ""},
    ]


@pytest.mark.asyncio
async def test_sitemap_index_refetches_children_under_stable_root_etag() -> None:
    root = "https://news.example.com/sitemap.xml"
    child = "https://news.example.com/child.xml"
    old_detail = "https://news.example.com/old"
    new_detail = "https://news.example.com/new"
    _Transport.artifacts[root] = WebFetchArtifact(
        requested_url=root,
        final_url=root,
        status_code=200,
        content_type="application/xml",
        body=(
            '<?xml version="1.0"?><sitemapindex '
            'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"<sitemap><loc>{child}</loc></sitemap>"
            "</sitemapindex>"
        ).encode(),
        etag='"stable-root"',
    )

    def set_child(detail_url: str) -> None:
        _Transport.artifacts[child] = WebFetchArtifact(
            requested_url=child,
            final_url=child,
            status_code=200,
            content_type="application/xml",
            body=(
                '<?xml version="1.0"?><urlset '
                'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                f"<url><loc>{detail_url}</loc></url></urlset>"
            ).encode(),
        )
        _Transport.artifacts[detail_url] = WebFetchArtifact(
            requested_url=detail_url,
            final_url=detail_url,
            status_code=200,
            content_type="text/html",
            body=f"<html><head><title>{detail_url}</title></head></html>".encode(),
        )

    set_child(old_detail)
    adapter = WebSourceAdapter(llm_service=None, public_transport_factory=_Transport)
    recipe = _recipe("sitemap", root, max_pages=1, detail_fetch_limit=1)
    first = await adapter.fetch_result(recipe, profile=None, limit=1)  # type: ignore[arg-type]
    set_child(new_detail)
    second = await adapter.fetch_result(  # type: ignore[arg-type]
        recipe,
        profile=None,
        limit=1,
        state={
            "cursor": first.cursor,
            "etag": first.etag,
            "last_modified": first.last_modified,
            "validator_url": first.validator_url,
        },
    )

    assert [item.content_url for item in first.items] == [old_detail]
    assert [item.content_url for item in second.items] == [new_detail]
    root_requests = [kwargs for url, kwargs in _Transport.requests if url == root]
    assert root_requests[-1] == {
        "expected": "sitemap",
        "etag": "",
        "last_modified": "",
        "validator_url": "",
    }


@pytest.mark.asyncio
async def test_sitemap_urlset_rebases_positional_cursor_after_head_insert() -> None:
    root = "https://news.example.com/sitemap.xml"
    old_head = "https://news.example.com/old-head"
    old_tail = "https://news.example.com/old-tail"
    new_head = "https://news.example.com/new-head"

    def set_root(urls: list[str]) -> None:
        rows = "".join(f"<url><loc>{url}</loc></url>" for url in urls)
        _Transport.artifacts[root] = WebFetchArtifact(
            requested_url=root,
            final_url=root,
            status_code=200,
            content_type="application/xml",
            body=(
                '<?xml version="1.0"?><urlset '
                f'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{rows}</urlset>'
            ).encode(),
            etag='"root"',
        )

    for detail in (old_head, old_tail, new_head):
        _Transport.artifacts[detail] = WebFetchArtifact(
            requested_url=detail,
            final_url=detail,
            status_code=200,
            content_type="text/html",
            body=f"<html><head><title>{detail}</title></head></html>".encode(),
        )
    adapter = WebSourceAdapter(llm_service=None, public_transport_factory=_Transport)
    recipe = _recipe("sitemap", root, detail_fetch_limit=1)

    set_root([old_head, old_tail])
    first = await adapter.fetch_result(recipe, profile=None, limit=1)  # type: ignore[arg-type]
    set_root([new_head, old_head, old_tail])
    second = await adapter.fetch_result(  # type: ignore[arg-type]
        recipe,
        profile=None,
        limit=1,
        state={
            "cursor": first.cursor,
            "etag": first.etag,
            "validator_url": first.validator_url,
        },
    )

    assert [item.content_url for item in first.items] == [old_head]
    assert first.cursor["sitemap_entry_offset"] == 1
    assert [item.content_url for item in second.items] == [new_head]
    assert second.cursor["sitemap_entry_offset"] == 1
    assert second.cursor["sitemap_topology"] != first.cursor["sitemap_topology"]
    assert _Transport.requests[-2:] == [
        (
            root,
            {
                "expected": "sitemap",
                "etag": "",
                "last_modified": "",
                "validator_url": "",
            },
        ),
        (new_head, {"expected": "html"}),
    ]


@pytest.mark.asyncio
async def test_sitemap_urlset_producer_discovers_head_insert(tmp_path) -> None:
    root = "https://news.example.com/sitemap.xml"
    old_head = "https://news.example.com/old-head"
    old_tail = "https://news.example.com/old-tail"
    new_head = "https://news.example.com/new-head"

    def set_root(urls: list[str]) -> None:
        rows = "".join(f"<url><loc>{url}</loc></url>" for url in urls)
        _Transport.artifacts[root] = WebFetchArtifact(
            requested_url=root,
            final_url=root,
            status_code=200,
            content_type="application/xml",
            body=(
                '<?xml version="1.0"?><urlset '
                f'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{rows}</urlset>'
            ).encode(),
            etag='"root"',
        )

    for detail in (old_head, old_tail, new_head):
        _Transport.artifacts[detail] = WebFetchArtifact(
            requested_url=detail,
            final_url=detail,
            status_code=200,
            content_type="text/html",
            body=f"<html><head><title>{detail}</title></head></html>".encode(),
        )
    database = Database(tmp_path / "sitemap-head-insert.db")
    database.initialize()
    database.save_source_recipe(
        build_web_recipe(
            {
                "name": "Newest first",
                "strategy": "sitemap",
                "config": {
                    "entry_url": root,
                    "interval_minutes": 30,
                    "max_items": 3,
                    "max_pages": 1,
                    "detail_fetch_limit": 1,
                },
            },
            recipe_id="web-head-insert",
        )
    )

    class _Pipeline:
        def __init__(self) -> None:
            self.urls: list[str] = []

        def enqueue_candidates(self, items, *, source_context: str) -> int:
            assert source_context == "web-sitemap"
            self.urls.extend(item.content_url for item in items)
            return len(items)

    pipeline = _Pipeline()
    producer = WebDiscoveryProducer(
        database=database,
        adapter=WebSourceAdapter(llm_service=None, public_transport_factory=_Transport),
        candidate_pipeline=pipeline,
        enabled=True,
    )

    set_root([old_head, old_tail])
    first = await producer.produce_if_due(limit=1, force=True)
    first_state = database.get_web_source_state("web-head-insert")
    set_root([new_head, old_head, old_tail])
    second = await producer.produce_if_due(limit=1, force=True)
    second_state = database.get_web_source_state("web-head-insert")

    assert first["recipe_results"]["web-head-insert"]["status"] == "ok"
    assert second["recipe_results"]["web-head-insert"]["status"] == "ok"
    assert pipeline.urls == [old_head, new_head]
    assert first_state is not None
    assert second_state is not None
    assert first_state["cursor"]["sitemap_entry_offset"] == 1
    assert second_state["cursor"]["sitemap_entry_offset"] == 1
    assert first_state["cursor"]["sitemap_topology"] != second_state["cursor"]["sitemap_topology"]


@pytest.mark.asyncio
async def test_sitemap_index_rebases_only_current_child_after_head_insert() -> None:
    root = "https://news.example.com/sitemap.xml"
    child_a = "https://news.example.com/a.xml"
    child_b = "https://news.example.com/b.xml"
    item_a = "https://news.example.com/a-item"
    old_head = "https://news.example.com/b-old-head"
    old_tail = "https://news.example.com/b-old-tail"
    new_head = "https://news.example.com/b-new-head"
    _Transport.artifacts[root] = WebFetchArtifact(
        requested_url=root,
        final_url=root,
        status_code=200,
        content_type="application/xml",
        body=(
            '<?xml version="1.0"?><sitemapindex '
            'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"<sitemap><loc>{child_a}</loc></sitemap>"
            f"<sitemap><loc>{child_b}</loc></sitemap>"
            "</sitemapindex>"
        ).encode(),
    )
    _Transport.artifacts[child_a] = WebFetchArtifact(
        requested_url=child_a,
        final_url=child_a,
        status_code=200,
        content_type="application/xml",
        body=(
            '<?xml version="1.0"?><urlset '
            'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"<url><loc>{item_a}</loc></url></urlset>"
        ).encode(),
    )

    def set_child_b(urls: list[str]) -> None:
        rows = "".join(f"<url><loc>{url}</loc></url>" for url in urls)
        _Transport.artifacts[child_b] = WebFetchArtifact(
            requested_url=child_b,
            final_url=child_b,
            status_code=200,
            content_type="application/xml",
            body=(
                '<?xml version="1.0"?><urlset '
                f'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{rows}</urlset>'
            ).encode(),
        )

    for detail in (item_a, old_head, old_tail, new_head):
        _Transport.artifacts[detail] = WebFetchArtifact(
            requested_url=detail,
            final_url=detail,
            status_code=200,
            content_type="text/html",
            body=f"<html><head><title>{detail}</title></head></html>".encode(),
        )
    set_child_b([old_head, old_tail])
    adapter = WebSourceAdapter(llm_service=None, public_transport_factory=_Transport)
    recipe = _recipe("sitemap", root, max_pages=1, detail_fetch_limit=1)

    first = await adapter.fetch_result(recipe, profile=None, limit=1)  # type: ignore[arg-type]
    second = await adapter.fetch_result(  # type: ignore[arg-type]
        recipe, profile=None, limit=1, state={"cursor": first.cursor}
    )
    set_child_b([new_head, old_head, old_tail])
    third = await adapter.fetch_result(  # type: ignore[arg-type]
        recipe, profile=None, limit=1, state={"cursor": second.cursor}
    )

    assert [item.content_url for item in first.items] == [item_a]
    assert first.cursor["sitemap_child_offset"] == 1
    assert [item.content_url for item in second.items] == [old_head]
    assert second.cursor["sitemap_child_offset"] == 1
    assert second.cursor["sitemap_entry_offset"] == 1
    assert [item.content_url for item in third.items] == [new_head]
    assert third.cursor["sitemap_child_offset"] == 1
    assert third.cursor["sitemap_entry_offset"] == 1
    assert third.cursor["sitemap_topology"] == second.cursor["sitemap_topology"]
    assert third.cursor["sitemap_child_topology"] != second.cursor["sitemap_child_topology"]


@pytest.mark.asyncio
async def test_sitemap_index_producer_discovers_current_child_head_insert(tmp_path) -> None:
    root = "https://news.example.com/sitemap.xml"
    child_a = "https://news.example.com/a.xml"
    child_b = "https://news.example.com/b.xml"
    item_a = "https://news.example.com/a-item"
    old_head = "https://news.example.com/b-old-head"
    old_tail = "https://news.example.com/b-old-tail"
    new_head = "https://news.example.com/b-new-head"
    _Transport.artifacts[root] = WebFetchArtifact(
        requested_url=root,
        final_url=root,
        status_code=200,
        content_type="application/xml",
        body=(
            '<?xml version="1.0"?><sitemapindex '
            'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"<sitemap><loc>{child_a}</loc></sitemap>"
            f"<sitemap><loc>{child_b}</loc></sitemap>"
            "</sitemapindex>"
        ).encode(),
    )
    _Transport.artifacts[child_a] = WebFetchArtifact(
        requested_url=child_a,
        final_url=child_a,
        status_code=200,
        content_type="application/xml",
        body=(
            '<?xml version="1.0"?><urlset '
            'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"<url><loc>{item_a}</loc></url></urlset>"
        ).encode(),
    )

    def set_child_b(urls: list[str]) -> None:
        rows = "".join(f"<url><loc>{url}</loc></url>" for url in urls)
        _Transport.artifacts[child_b] = WebFetchArtifact(
            requested_url=child_b,
            final_url=child_b,
            status_code=200,
            content_type="application/xml",
            body=(
                '<?xml version="1.0"?><urlset '
                f'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{rows}</urlset>'
            ).encode(),
        )

    for detail in (item_a, old_head, old_tail, new_head):
        _Transport.artifacts[detail] = WebFetchArtifact(
            requested_url=detail,
            final_url=detail,
            status_code=200,
            content_type="text/html",
            body=f"<html><head><title>{detail}</title></head></html>".encode(),
        )
    database = Database(tmp_path / "sitemap-child-head-insert.db")
    database.initialize()
    database.save_source_recipe(
        build_web_recipe(
            {
                "name": "Index newest first",
                "strategy": "sitemap",
                "config": {
                    "entry_url": root,
                    "interval_minutes": 30,
                    "max_items": 4,
                    "max_pages": 1,
                    "detail_fetch_limit": 1,
                },
            },
            recipe_id="web-child-head-insert",
        )
    )

    class _Pipeline:
        def __init__(self) -> None:
            self.urls: list[str] = []

        def enqueue_candidates(self, items, *, source_context: str) -> int:
            assert source_context == "web-sitemap"
            self.urls.extend(item.content_url for item in items)
            return len(items)

    pipeline = _Pipeline()
    producer = WebDiscoveryProducer(
        database=database,
        adapter=WebSourceAdapter(llm_service=None, public_transport_factory=_Transport),
        candidate_pipeline=pipeline,
        enabled=True,
    )

    set_child_b([old_head, old_tail])
    await producer.produce_if_due(limit=1, force=True)
    await producer.produce_if_due(limit=1, force=True)
    before_insert = database.get_web_source_state("web-child-head-insert")
    set_child_b([new_head, old_head, old_tail])
    third = await producer.produce_if_due(limit=1, force=True)
    after_insert = database.get_web_source_state("web-child-head-insert")

    assert third["recipe_results"]["web-child-head-insert"]["status"] == "ok"
    assert pipeline.urls == [item_a, old_head, new_head]
    assert before_insert is not None
    assert after_insert is not None
    assert before_insert["cursor"]["sitemap_child_offset"] == 1
    assert after_insert["cursor"]["sitemap_child_offset"] == 1
    assert before_insert["cursor"]["sitemap_topology"] == after_insert["cursor"]["sitemap_topology"]
    assert (
        before_insert["cursor"]["sitemap_child_topology"]
        != after_insert["cursor"]["sitemap_child_topology"]
    )


@pytest.mark.asyncio
async def test_sitemap_urlset_preserves_conditional_304_after_clean_sweep() -> None:
    root = "https://news.example.com/sitemap.xml"
    _Transport.artifacts[root] = WebFetchArtifact(
        requested_url=root,
        final_url=root,
        status_code=304,
        content_type="",
        body=b"",
        etag='"same"',
        not_modified=True,
    )
    cursor = {
        "sitemap_child_offset": 0,
        "sitemap_entry_offset": 0,
        "sitemap_root_kind": "urlset",
    }

    result = await WebSourceAdapter(
        llm_service=None,
        public_transport_factory=_Transport,
    ).fetch_result(
        _recipe("sitemap", root),
        profile=None,
        limit=1,
        state={
            "cursor": cursor,
            "etag": '"same"',
            "last_modified": "yesterday",
            "validator_url": root,
        },
    )  # type: ignore[arg-type]

    assert result.status == "not_modified"
    assert result.cursor == cursor
    assert _Transport.requests[-1] == (
        root,
        {
            "expected": "sitemap",
            "etag": '"same"',
            "last_modified": "yesterday",
            "validator_url": root,
        },
    )


@pytest.mark.asyncio
async def test_429_maps_to_rate_limited_with_retry_after() -> None:
    url = "https://news.example.com/feed.xml"
    _Transport.artifacts[url] = WebFetchError(
        "rate_limited", "slow down", status_code=429, retry_after_seconds=120
    )
    result = await WebSourceAdapter(
        llm_service=None, public_transport_factory=_Transport
    ).fetch_result(_recipe("feed", url), profile=None, limit=5)  # type: ignore[arg-type]
    assert result.status == "rate_limited"
    assert result.retry_after_seconds == 120
    assert result.scope_complete is False


@pytest.mark.asyncio
async def test_sitemap_child_429_stops_recipe_and_preserves_retry_after() -> None:
    root = "https://news.example.com/sitemap.xml"
    limited_child = "https://news.example.com/limited.xml"
    later_child = "https://news.example.com/later.xml"
    _Transport.artifacts[root] = WebFetchArtifact(
        requested_url=root,
        final_url=root,
        status_code=200,
        content_type="application/xml",
        body=(
            '<?xml version="1.0"?><sitemapindex '
            'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"<sitemap><loc>{limited_child}</loc></sitemap>"
            f"<sitemap><loc>{later_child}</loc></sitemap>"
            "</sitemapindex>"
        ).encode(),
    )
    _Transport.artifacts[limited_child] = WebFetchError(
        "rate_limited",
        "slow down",
        status_code=429,
        retry_after_seconds=3_600,
    )
    _Transport.artifacts[later_child] = AssertionError("later child must not be fetched")

    result = await WebSourceAdapter(
        llm_service=None,
        public_transport_factory=_Transport,
    ).fetch_result(
        _recipe("sitemap", root, max_pages=2),
        profile=None,
        limit=2,
    )  # type: ignore[arg-type]

    assert result.status == "rate_limited"
    assert result.retry_after_seconds == 3_600
    assert result.scope_complete is False
    assert [url for url, _kwargs in _Transport.requests] == [root, limited_child]


@pytest.mark.asyncio
async def test_listing_detail_robots_failure_stops_recipe() -> None:
    listing = "https://news.example.com/weblog/"
    detail = "https://news.example.com/posts/alpha"
    _Transport.artifacts[listing] = WebFetchArtifact(
        requested_url=listing,
        final_url=listing,
        status_code=200,
        content_type="text/html",
        body=(b'<html><body><article><a href="/posts/alpha">Alpha</a></article></body></html>'),
    )
    _Transport.artifacts[detail] = WebFetchError(
        "robots_unavailable",
        "robots temporarily unavailable",
    )
    recipe = _recipe(
        "listing",
        listing,
        detail_fetch_limit=1,
        listing={"item_selector": "article", "link_selector": "a"},
    )

    result = await WebSourceAdapter(
        llm_service=None,
        public_transport_factory=_Transport,
    ).fetch_result(recipe, profile=None, limit=1)  # type: ignore[arg-type]

    assert result.status == "failed"
    assert result.error_code == "robots_unavailable"
    assert result.scope_complete is False


@pytest.mark.asyncio
async def test_password_wall_is_login_required_not_empty() -> None:
    url = "https://news.example.com/weblog/"
    _Transport.artifacts[url] = WebFetchArtifact(
        requested_url=url,
        final_url=url,
        status_code=200,
        content_type="text/html",
        body=b'<html><form><input type="password" name="password"></form></html>',
    )
    result = await WebSourceAdapter(
        llm_service=None, public_transport_factory=_Transport
    ).fetch_result(
        _recipe(
            "listing",
            url,
            listing={"item_selector": "article", "link_selector": "a"},
        ),
        profile=None,  # type: ignore[arg-type]
        limit=5,
    )
    assert result.status == "login_required"
    assert result.items == []


@pytest.mark.asyncio
async def test_invalid_listing_plan_fails_before_any_request() -> None:
    url = "https://news.example.com/weblog/"
    result = await WebSourceAdapter(
        llm_service=None, public_transport_factory=_Transport
    ).fetch_result(_recipe("listing", url), profile=None, limit=5)  # type: ignore[arg-type]

    assert result.status == "failed"
    assert result.error_code == "invalid_selector"
    assert _Transport.requests == []
    assert result.scope_complete is False
