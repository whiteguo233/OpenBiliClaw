"""Public API contract for typed Web discovery recipes."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.storage.database import Database


def _client(tmp_path) -> tuple[TestClient, Database]:
    database = Database(tmp_path / "web-api.db")
    database.initialize()
    app = create_app(memory_manager=object(), database=database, soul_engine=object())
    return TestClient(app), database


def test_create_web_source_compiles_backend_owned_recipe(tmp_path) -> None:
    client, _database = _client(tmp_path)
    response = client.post(
        "/api/sources",
        json={
            "source_type": "web",
            "name": "Example blog",
            "strategy": "feed",
            "config": {"entry_url": "https://example.com/feed.xml"},
        },
    )
    assert response.status_code == 201
    recipe = response.json()["recipe"]
    assert recipe["source_type"] == "web"
    assert recipe["created_by"] == "user"
    assert recipe["config"]["allowed_origins"] == ["https://example.com"]
    assert recipe["config"]["plan_version"] == 1

    listed = client.get("/api/sources").json()["items"]
    assert listed[0]["runtime"] == {"status": "never_run"}


def test_create_web_source_rejects_identity_and_state_injection(tmp_path) -> None:
    client, _database = _client(tmp_path)
    response = client.post(
        "/api/sources",
        json={
            "id": "chosen-id",
            "source_type": "web",
            "created_by": "system",
            "name": "bad",
            "strategy": "feed",
            "config": {"entry_url": "https://example.com/feed.xml", "etag": "inject"},
        },
    )
    assert response.status_code == 422
    assert "client-owned" in response.json()["detail"]


def test_create_web_source_rejects_private_and_dedicated_urls(tmp_path) -> None:
    client, _database = _client(tmp_path)
    for entry_url in (
        "http://127.0.0.1/feed",
        "http://metadata.google.internal/",
        "https://www.bilibili.com/read/cv1",
    ):
        response = client.post(
            "/api/sources",
            json={
                "source_type": "web",
                "name": "bad",
                "strategy": "feed",
                "config": {"entry_url": entry_url},
            },
        )
        assert response.status_code == 422


def test_update_web_source_revalidates_complete_plan_and_preserves_owner(tmp_path) -> None:
    client, _database = _client(tmp_path)
    created = client.post(
        "/api/sources",
        json={
            "source_type": "web",
            "name": "Before",
            "strategy": "listing",
            "config": {
                "entry_url": "https://example.com/blog",
                "listing": {"item_selector": "article", "link_selector": "a"},
            },
        },
    ).json()["recipe"]

    response = client.put(
        f"/api/sources/{created['id']}",
        json={
            "name": "After",
            "strategy": "sitemap",
            "config": {"entry_url": "https://example.com/sitemap.xml"},
            "enabled": False,
        },
    )
    assert response.status_code == 200
    recipe = response.json()["recipe"]
    assert recipe["created_by"] == "user"
    assert recipe["strategy"] == "sitemap"
    assert recipe["enabled"] is False

    injected = client.put(f"/api/sources/{created['id']}", json={"status": "ok"})
    assert injected.status_code == 422


def test_list_web_source_reports_runtime_health_without_lease_token(tmp_path) -> None:
    client, database = _client(tmp_path)
    recipe = client.post(
        "/api/sources",
        json={
            "source_type": "web",
            "name": "Example",
            "strategy": "feed",
            "config": {"entry_url": "https://example.com/feed.xml"},
        },
    ).json()["recipe"]
    claimed = database.claim_due_web_recipes(limit=1)[0]
    listed = client.get("/api/sources").json()["items"][0]
    assert listed["runtime"]["status"] == "running"
    assert "lease_token" not in listed["runtime"]
    assert claimed["lease_token"]
    assert recipe["id"] == listed["id"]


def test_list_legacy_web_extract_does_not_project_public_web_runtime(tmp_path) -> None:
    client, database = _client(tmp_path)
    database.save_source_recipe(
        {
            "id": "legacy",
            "source_type": "web",
            "name": "Legacy extractor",
            "strategy": "web_extract",
            "config": {"url": "https://example.com/article"},
            "enabled": True,
        }
    )

    listed = client.get("/api/sources").json()["items"]

    assert listed[0]["id"] == "legacy"
    assert "runtime" not in listed[0]

    toggled = client.put("/api/sources/legacy", json={"enabled": False})
    assert toggled.status_code == 200
    legacy = next(item for item in database.get_all_recipes() if item["id"] == "legacy")
    assert legacy["enabled"] is False

    rejected = client.put("/api/sources/legacy", json={"strategy": "feed"})
    assert rejected.status_code == 422
    assert "legacy web_extract" in rejected.json()["detail"]


def test_probe_web_source_is_read_only_and_available_during_init(tmp_path, monkeypatch) -> None:
    client, database = _client(tmp_path)
    calls: list[tuple[str, dict[str, object]]] = []

    class FakeResult:
        def to_dict(self) -> dict[str, object]:
            return {
                "status": "ready",
                "detail": "Found one feed.",
                "requested_url": "https://news.example.com/",
                "final_url": "https://news.example.com/",
                "page_title": "Example News",
                "candidates": [
                    {
                        "strategy": "feed",
                        "entry_url": "https://news.example.com/feed.xml",
                        "discovery_method": "html_alternate",
                        "confidence": "high",
                        "detail": "The page advertises a supported feed.",
                        "sample_items": [
                            {
                                "title": "First post",
                                "content_url": "https://news.example.com/posts/first",
                                "author_name": "Alice",
                                "published_at": "2026-08-10T09:00:49Z",
                            }
                        ],
                        "recipe": {
                            "name": "Example News · Feed",
                            "strategy": "feed",
                            "config": {"entry_url": "https://news.example.com/feed.xml"},
                            "target_share": 4,
                            "enabled": True,
                        },
                    }
                ],
            }

    async def fake_probe(url: str, **kwargs: object) -> FakeResult:
        calls.append((url, kwargs))
        return FakeResult()

    monkeypatch.setattr(
        "openbiliclaw.sources.web_probe.probe_public_web_url",
        fake_probe,
    )
    client.app.state.runtime_context._init_coordinator = SimpleNamespace(init_active=lambda: True)
    client.app.state.runtime_context.config = SimpleNamespace(
        sources=SimpleNamespace(
            web=SimpleNamespace(
                enabled=False,
                request_timeout_seconds=9,
                max_response_bytes=123_456,
                max_redirects=2,
            )
        )
    )
    before = database.get_all_recipes()

    response = client.post(
        "/api/sources/web/probe",
        json={"url": "https://news.example.com/"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["candidates"][0]["recipe"]["strategy"] == "feed"
    assert calls == [
        (
            "https://news.example.com/",
            {
                "timeout_seconds": 9.0,
                "max_response_bytes": 123_456,
                "max_redirects": 2,
            },
        )
    ]
    assert database.get_all_recipes() == before


def test_probe_web_source_is_available_in_degraded_recovery_mode(tmp_path, monkeypatch) -> None:
    client, _database = _client(tmp_path)

    class FakeResult:
        def to_dict(self) -> dict[str, object]:
            return {
                "status": "no_match",
                "detail": "No supported public source was detected.",
                "requested_url": "https://news.example.com/",
                "final_url": "https://news.example.com/",
                "page_title": "",
                "candidates": [],
            }

    async def fake_probe(_url: str, **_kwargs) -> FakeResult:
        return FakeResult()

    monkeypatch.setattr(
        "openbiliclaw.sources.web_probe.probe_public_web_url",
        fake_probe,
    )
    ctx = client.app.state.runtime_context
    ctx.degraded = True
    ctx.degraded_reason = "llm_registry_unavailable"

    response = client.post(
        "/api/sources/web/probe",
        json={"url": "https://news.example.com/"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "no_match"


def test_probe_web_source_rejects_unknown_fields_and_private_urls(tmp_path) -> None:
    client, _database = _client(tmp_path)

    unknown = client.post(
        "/api/sources/web/probe",
        json={"url": "https://news.example.com/", "save": True},
    )
    private = client.post(
        "/api/sources/web/probe",
        json={"url": "http://127.0.0.1/private"},
    )

    assert unknown.status_code == 422
    assert private.status_code == 422
    for unicode_local_url in (
        "http://①②⑦.⓪.⓪.①/x",
        "https://foo.ⓛⓞⓒⓐⓛ/feed",
        "https://foo.ｌａｎ/feed",
    ):
        rejected = client.post(
            "/api/sources/web/probe",
            json={"url": unicode_local_url},
        )
        assert rejected.status_code == 422


@pytest.mark.asyncio
async def test_probe_web_source_fails_fast_at_two_concurrent_requests_and_recovers(
    tmp_path, monkeypatch
) -> None:
    sync_client, database = _client(tmp_path)
    release = asyncio.Event()
    two_started = asyncio.Event()
    calls: list[str] = []

    class FakeResult:
        def __init__(self, url: str) -> None:
            self.url = url

        def to_dict(self) -> dict[str, object]:
            return {
                "status": "no_match",
                "detail": "No supported public source was detected.",
                "requested_url": self.url,
                "final_url": self.url,
                "page_title": "",
                "candidates": [],
            }

    async def fake_probe(url: str, **_kwargs: object) -> FakeResult:
        calls.append(url)
        if len(calls) == 2:
            two_started.set()
        await release.wait()
        return FakeResult(url)

    monkeypatch.setattr(
        "openbiliclaw.sources.web_probe.probe_public_web_url",
        fake_probe,
    )
    before = database.get_all_recipes()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=sync_client.app),
        base_url="http://testserver",
    ) as client:
        first = asyncio.create_task(
            client.post(
                "/api/sources/web/probe",
                json={"url": "https://one.example.com/"},
            )
        )
        second = asyncio.create_task(
            client.post(
                "/api/sources/web/probe",
                json={"url": "https://two.example.com/"},
            )
        )
        await asyncio.wait_for(two_started.wait(), timeout=1)
        third = asyncio.create_task(
            client.post(
                "/api/sources/web/probe",
                json={"url": "https://three.example.com/"},
            )
        )
        done, _pending = await asyncio.wait({third}, timeout=0.25)
        try:
            assert third in done, "over-capacity probes must fail without queueing"
            overloaded = third.result()
            assert overloaded.status_code == 429
            assert overloaded.headers["retry-after"] == "1"
            assert overloaded.json()["detail"]["code"] == "web_probe_busy"
            assert calls == [
                "https://one.example.com/",
                "https://two.example.com/",
            ]
        finally:
            release.set()
            await asyncio.gather(first, second, third, return_exceptions=True)

        assert first.result().status_code == 200
        assert second.result().status_code == 200
        recovered = await client.post(
            "/api/sources/web/probe",
            json={"url": "https://three.example.com/"},
        )

    assert recovered.status_code == 200
    assert calls == [
        "https://one.example.com/",
        "https://two.example.com/",
        "https://three.example.com/",
    ]
    assert database.get_all_recipes() == before


@pytest.mark.parametrize("method", ["get", "post"])
def test_source_share_suggestion_tracks_web_recipes_without_restart(
    tmp_path, monkeypatch, method
) -> None:
    from openbiliclaw.config import Config

    config = Config()
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: config)
    client, database = _client(tmp_path)

    def suggestion():
        if method == "post":
            return client.post(
                "/api/config/source-share-suggestion", json={"enabled_sources": {"bilibili": True}}
            ).json()
        return client.get("/api/config/source-share-suggestion").json()

    before = suggestion()
    assert before["enabled_sources"]["web"] is False
    assert "web" not in before["suggested_shares"]

    recipe = client.post(
        "/api/sources",
        json={
            "source_type": "web",
            "name": "Example blog",
            "strategy": "feed",
            "config": {"entry_url": "https://example.com/feed.xml"},
        },
    ).json()["recipe"]
    after = suggestion()
    assert after["enabled_sources"]["web"] is True
    assert after["suggested_shares"]["web"] == 1

    database.update_recipe(recipe["id"], enabled=False)
    disabled = suggestion()
    assert disabled["enabled_sources"]["web"] is False
    assert "web" not in disabled["suggested_shares"]
