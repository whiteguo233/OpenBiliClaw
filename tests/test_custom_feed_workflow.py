"""Feed authoring through the real compiler, HTTP parser, ledger and candidate queue."""

from __future__ import annotations

import json
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from openbiliclaw.api.custom_sources import public_draft
from openbiliclaw.discovery.candidate_pipeline import DiscoveryCandidatePipeline
from openbiliclaw.runtime.custom_workflow_producer import CustomWorkflowProducer
from openbiliclaw.sources.custom_sources import CustomSourceStore
from openbiliclaw.sources.web_adapter import WebSourceAdapter
from openbiliclaw.sources.web_probe import probe_public_web_url
from openbiliclaw.sources.web_public import (
    SafeWebTransport,
    WebParseError,
    parse_web_feed,
    parse_web_feed_page,
)
from openbiliclaw.sources.web_recipe import WebRecipeValidationError, build_web_recipe
from openbiliclaw.sources.workflow_service import WorkflowService, workflow_presentation
from openbiliclaw.storage.database import Database

FEED_URL = "https://feeds.example.com/journal.xml"
ARTICLE_URL = "https://journal.example.com/articles/one"
TEXT = "Real content from a titleless post about Python web applications."


def _feed(kind, *, article_url=ARTICLE_URL, title="A public article", extra=""):
    if kind == "json":
        return (
            json.dumps(
                {
                    "version": "https://jsonfeed.org/version/1.1",
                    "title": "Journal",
                    "items": [
                        {"id": "one", "url": article_url, "title": title, "content_text": TEXT}
                    ],
                }
            ).encode(),
            "application/feed+json",
        )
    if kind == "atom":
        return (
            (
                '<feed xmlns="http://www.w3.org/2005/Atom"><title>Journal</title>'
                f"<entry><id>{article_url}</id><title>{title}</title>"
                f'<link rel="alternate" href="{article_url}"/>'
                f'<content type="text">{TEXT}</content></entry>{extra}</feed>'
            ).encode(),
            "application/atom+xml",
        )
    return (
        (
            '<rss version="2.0"><channel><title>Journal</title>'
            f"<item><title>{title}</title><link>{article_url}</link>"
            f"<description><![CDATA[<p>{TEXT}</p>]]></description></item>{extra}"
            "</channel></rss>"
        ).encode(),
        "application/rss+xml",
    )


async def _resolver(_host, _port):
    return ["93.184.216.34"]


def _factory(handler, *, resolver=_resolver):
    return partial(
        SafeWebTransport,
        transport=httpx.MockTransport(handler),
        resolver=resolver,
        respect_robots=False,
    )


@pytest.fixture
def database(tmp_path):
    db = Database(tmp_path / "feed-workflow.db")
    db.initialize()
    yield db
    db.close()


def _service(database, monkeypatch, handler, *, llm_service=None):
    factory = _factory(handler)
    monkeypatch.setattr(
        "openbiliclaw.sources.workflow_service.WebSourceAdapter",
        partial(WebSourceAdapter, public_transport_factory=factory),
    )
    return WorkflowService(
        database,
        llm_service=llm_service,
        probe=partial(probe_public_web_url, transport_factory=factory),
    )


@pytest.mark.parametrize("kind", ["rss", "atom", "json"])
def test_titleless_feed_entry_uses_real_body_and_keeps_identity(kind):
    body, media_type = _feed(kind, article_url="https://feeds.example.com/one", title="")
    items = parse_web_feed(
        body,
        content_type=media_type,
        base_url=FEED_URL,
        allowed_origins=["https://feeds.example.com"],
    )
    assert len(items) == 1
    assert items[0].title == TEXT
    assert items[0].body_text == TEXT
    assert items[0].content_url == "https://feeds.example.com/one"
    assert items[0].source_platform == "web"


def test_titleless_json_post_never_invents_a_title_for_empty_content():
    body = json.dumps(
        {"version": "https://jsonfeed.org/version/1.1", "items": [{"url": FEED_URL + "/one"}]}
    )
    with pytest.raises(WebParseError, match="none were usable"):
        parse_web_feed(
            body,
            content_type="application/json",
            base_url=FEED_URL,
            allowed_origins=["https://feeds.example.com"],
        )


@pytest.mark.parametrize("kind", ["rss", "atom", "json"])
async def test_real_prepare_trial_activate_and_custom_queue_for_each_feed_format(
    database, monkeypatch, kind
):
    requests = []
    body, media_type = _feed(kind, title="")

    def handler(request):
        requests.append(str(request.url))
        assert str(request.url) == FEED_URL
        assert "cookie" not in request.headers and "authorization" not in request.headers
        return httpx.Response(
            200, content=body, headers={"Content-Type": media_type, "ETag": '"v1"'}
        )

    service = _service(database, monkeypatch, handler)
    draft = await service.prepare(
        url=FEED_URL, intent="Follow Python web application articles", name="Journal"
    )
    assert draft["status"] == "ready" and draft["trial_success"]
    assert service.sources.list_sources() == []
    summary = public_draft(draft)
    assert summary["kind"] == "feed"
    assert summary["request_origins"] == ["https://feeds.example.com"]
    assert summary["content_origins"] == [
        "https://feeds.example.com",
        "https://journal.example.com",
    ]
    assert summary["sample_items"][0]["title"] == TEXT
    assert "session_id" not in summary and "workflow" not in summary

    source = service.activate(draft["draft_id"], target_share=2, interval_minutes=5)
    assert source["enabled"] and source["target_share"] == 2
    assert service.activate(draft["draft_id"]) == source
    listed = service.list_sources()[0]
    assert listed["workflows"][0]["kind"] == "feed"
    assert listed["workflows"][0]["content_origins"] == summary["content_origins"]
    pipeline = DiscoveryCandidatePipeline(database, SimpleNamespace())
    producer = CustomWorkflowProducer(
        database,
        source["source_id"],
        pipeline,
        candidate_evaluation_owned_by_coordinator=True,
        service=service,
    )
    outcome = await producer.produce_if_due(limit=5)
    assert outcome["discovered"] == 1 and outcome["enqueued"] == 1
    row = database.conn.execute("SELECT * FROM discovery_candidates").fetchone()
    assert row["source_id"] == source["source_id"] and row["source_name"] == "Journal"
    assert row["source_platform"] == "web"
    assert row["candidate_key"].startswith("web:")
    assert requests == [FEED_URL, FEED_URL, FEED_URL]


@pytest.mark.parametrize("tag", ["alternate", "anchor"])
async def test_website_discovers_explicit_hosted_feed_with_separate_request_scope(tag):
    requests = []
    link = (
        f'<link rel="alternate" type="application/rss+xml" href="{FEED_URL}">'
        if tag == "alternate"
        else f'<a href="{FEED_URL}">RSS</a>'
    )
    body, media_type = _feed("rss")

    def handler(request):
        requests.append(str(request.url))
        if str(request.url) == "https://journal.example.com/":
            return httpx.Response(
                200,
                text=f"<html><title>Journal</title>{link}</html>",
                headers={"Content-Type": "text/html"},
            )
        if str(request.url) == FEED_URL:
            return httpx.Response(200, content=body, headers={"Content-Type": media_type})
        assert request.url.path == "/sitemap.xml"
        return httpx.Response(404)

    result = await probe_public_web_url(
        "https://journal.example.com/",
        transport_factory=_factory(handler),
        observe_feed_scopes=True,
    )
    assert result.status == "ready"
    recipe = build_web_recipe(result.candidates[0].recipe)
    assert recipe["config"]["allowed_origins"] == ["https://feeds.example.com"]
    assert recipe["config"]["content_origins"] == [
        "https://feeds.example.com",
        "https://journal.example.com",
    ]
    assert requests == [
        "https://journal.example.com/",
        FEED_URL,
        "https://journal.example.com/sitemap.xml",
    ]


async def test_observed_feed_private_dns_is_never_requested():
    requests = []

    async def resolver(host, _port):
        return ["127.0.0.1"] if host == "feeds.example.com" else ["93.184.216.34"]

    def handler(request):
        requests.append(str(request.url))
        if request.url.path == "/":
            return httpx.Response(
                200,
                text=f'<link rel="alternate" type="application/rss+xml" href="{FEED_URL}">',
                headers={"Content-Type": "text/html"},
            )
        return httpx.Response(404)

    result = await probe_public_web_url(
        "https://journal.example.com/", transport_factory=_factory(handler, resolver=resolver)
    )
    assert result.status == "no_match"
    assert FEED_URL not in requests


@pytest.mark.parametrize(
    "unsafe",
    [
        "https://localhost",
        "https://intranet.local",
        "http://127.0.0.1",
        "https://user:pass@example.com",
        "https://example.com/path",
        "https://example.com#secret",
    ],
)
def test_feed_reference_scope_rejects_unsafe_or_non_origin_authority(unsafe):
    with pytest.raises(WebRecipeValidationError):
        build_web_recipe(
            {
                "name": "Journal",
                "strategy": "feed",
                "config": {"entry_url": FEED_URL, "content_origins": [unsafe]},
            }
        )


def test_content_reference_permission_does_not_expand_fetch_permission():
    recipe = build_web_recipe(
        {
            "name": "Journal",
            "strategy": "feed",
            "config": {"entry_url": FEED_URL, "content_origins": ["https://github.com"]},
        }
    )
    assert recipe["config"]["allowed_origins"] == ["https://feeds.example.com"]
    assert recipe["config"]["content_origins"] == ["https://github.com"]
    with pytest.raises(WebRecipeValidationError, match="only supported for feeds"):
        build_web_recipe(
            {
                "name": "Journal",
                "strategy": "listing",
                "config": {"entry_url": FEED_URL, "content_origins": ["https://github.com"]},
            }
        )


async def test_unapproved_new_article_domain_keeps_accepted_candidates_without_committing_validator(
    database, monkeypatch
):
    changed = False
    requests = []

    def handler(request):
        requests.append(str(request.url))
        extra = (
            "<item><title>New domain</title><link>https://new.example.net/one</link></item>"
            if changed
            else ""
        )
        body, media_type = _feed("rss", extra=extra)
        return httpx.Response(
            200,
            content=body,
            headers={"Content-Type": media_type, "ETag": '"changed"' if changed else '"original"'},
        )

    service = _service(database, monkeypatch, handler)
    draft = await service.prepare(url=FEED_URL, intent="Follow articles", executor="http")
    source = service.activate(draft["draft_id"])
    workflow = service.store.list_workflows(source["source_id"])[0]
    changed = True
    result = await service.fetch(workflow, limit=1)
    assert result.status == "degraded" and result.error_code == "content_scope_changed"
    assert result.scope_complete is False
    assert len(result.items) == 1 and result.items[0].content_url == ARTICLE_URL
    assert result.etag == "" and result.validator_url == "" and result.cursor == {}
    pipeline = DiscoveryCandidatePipeline(database, SimpleNamespace())
    producer = CustomWorkflowProducer(
        database,
        source["source_id"],
        pipeline,
        candidate_evaluation_owned_by_coordinator=True,
        service=service,
    )
    outcome = await producer.produce_if_due(limit=5)
    assert outcome["enqueued"] == 1
    current = service.store.get_workflow(workflow["workflow_id"])
    assert current["status"] == "degraded" and current["cursor"] == {}
    assert all(url == FEED_URL for url in requests)


async def test_explicit_github_atom_uses_only_anonymous_feed_path(database, monkeypatch):
    url = "https://github.com/psf/requests/releases.atom"
    body, media_type = _feed(
        "atom", article_url="https://github.com/psf/requests/releases/tag/v2.0"
    )
    with pytest.raises(WebRecipeValidationError, match="dedicated source"):
        build_web_recipe({"name": "Releases", "strategy": "feed", "config": {"entry_url": url}})
    with pytest.raises(WebRecipeValidationError, match="dedicated source"):
        CustomSourceStore(database).upsert_source(
            name="Releases", entry_url=url, intent="Follow releases"
        )

    def handler(request):
        assert str(request.url) == url
        assert "authorization" not in request.headers and "cookie" not in request.headers
        return httpx.Response(200, content=body, headers={"Content-Type": media_type})

    service = _service(database, monkeypatch, handler)
    draft = await service.prepare(url=url, intent="Follow package releases")
    assert draft["status"] == "ready" and workflow_presentation(draft)["kind"] == "feed"
    source = service.activate(draft["draft_id"])
    assert source["origin"] == "https://github.com" and source["enabled"]
    result = await service.fetch(service.store.list_workflows(source["source_id"])[0])
    assert result.status == "ok" and len(result.items) == 1


@pytest.mark.parametrize("executor", ["auto", "http", "playwright"])
async def test_dedicated_platform_html_never_falls_back_to_browser_including_trial(
    database, monkeypatch, executor
):
    def handler(_request):
        return httpx.Response(
            200,
            text="<html><title>Repository</title><a href='/projects'>Projects</a></html>",
            headers={"Content-Type": "text/html"},
        )

    service = _service(database, monkeypatch, handler)
    service.observe_session = AsyncMock(
        side_effect=AssertionError("must never open a platform browser")
    )
    draft = await service.prepare(
        url="https://github.com/psf/requests", intent="Follow releases", executor=executor
    )
    assert draft["status"] == "needs_details" and not draft["trial_success"]
    retried = await service.trial(draft["draft_id"])
    assert not retried["trial_success"]
    service.observe_session.assert_not_called()
    assert service.sources.list_sources() == []


async def test_feed_goal_selection_failure_is_reported_without_browser_fallback(
    database, monkeypatch
):
    body, media_type = _feed("rss")
    service = _service(
        database,
        monkeypatch,
        lambda _: httpx.Response(200, content=body, headers={"Content-Type": media_type}),
        llm_service=object(),
    )
    service._json_task = AsyncMock(side_effect=RuntimeError("provider unavailable"))
    service.observe_session = AsyncMock(
        side_effect=AssertionError("must never reinterpret a feed as HTML")
    )
    draft = await service.prepare(url=FEED_URL, intent="Follow Python articles")
    assert draft["status"] == "failed"
    assert "目标匹配" in draft["detail"]
    await service.trial(draft["draft_id"])
    service.observe_session.assert_not_called()


def test_feed_scope_observation_is_bounded_and_never_includes_private_references():
    rows = [
        {
            "id": str(i),
            "title": "Post",
            "url": f"https://site{i}.example.com/post",
            "content_text": TEXT,
        }
        for i in range(12)
    ]
    rows += [{"title": "Private", "url": "https://intranet.local/secret", "content_text": TEXT}]
    result = parse_web_feed_page(
        json.dumps({"version": "https://jsonfeed.org/version/1.1", "items": rows}),
        content_type="application/json",
        base_url=FEED_URL,
        allowed_origins=["https://feeds.example.com"],
        observe_content_origins=True,
    )
    assert len(result.content_origins) == 8 and len(result.items) == 7
    assert result.unapproved_content_origins
    assert (
        "https://intranet.local" not in result.content_origins + result.unapproved_content_origins
    )
