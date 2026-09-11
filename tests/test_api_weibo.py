"""HTTP-boundary contracts for anonymous and logged-in Weibo capabilities."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any

    import pytest


def _config_with_test_llm() -> Config:
    return Config(llm=LLMConfig(openai=LLMProviderConfig(api_key="sk-test-only")))


def test_weibo_status_is_local_anonymous_and_enablement_is_orthogonal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = Config()
    database = Database(tmp_path / "weibo-status.db")
    database.initialize()
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: cfg)

    with TestClient(
        create_app(memory_manager=object(), database=database, soul_engine=object())
    ) as client:
        disabled = client.get("/api/sources/status").json()["weibo"]
        cfg.sources.weibo.enabled = True
        enabled = client.get("/api/sources/status").json()["weibo"]

    assert disabled["enabled"] is False
    assert disabled["state"] == "no_auth"
    assert disabled["logged_in"] is True
    assert disabled["detail"] == "微博来源未启用。"
    assert disabled["feed_paused"] is False
    assert disabled["discovery_state"] == "disabled"

    assert enabled["enabled"] is True
    assert enabled["state"] == "no_auth"
    assert enabled["logged_in"] is True
    assert "初始化本人事件需要浏览器登录态" in enabled["detail"]
    assert enabled["feed_paused"] is False
    assert enabled["discovery_state"] == "unverified"
    assert enabled["auth"] == {
        "auth_required": False,
        "credential": "none",
        "credential_origin": "none",
        "verification": "unverified",
        "verify_method": "none",
        "verified_at": "",
        "verify_ttl_seconds": None,
        "can_verify_now": False,
        "detail": "微博公开发现可匿名；初始化本人收藏、关注和互动需要登录微博并连接插件。",
        "legacy_state": "no_auth",
        "legacy_logged_in": True,
        "capabilities": {
            "discover": {
                "mode": "anonymous",
                "required": True,
                "ready": True,
                "state": "ready",
                "readiness": "ready",
                "detail": "搜索、热搜和公开作者时间线无需登录。",
            },
            "profile": {
                "mode": "login-required",
                "required": True,
                "ready": False,
                "state": "login_required",
                "readiness": "login_required",
                "detail": "初始化本人收藏、关注和互动需要微博浏览器登录态。",
            },
            "bootstrap": {
                "mode": "login-required",
                "required": True,
                "ready": False,
                "state": "login_required",
                "readiness": "login_required",
                "detail": "个人事件只在微博同源浏览器任务中读取，后端不接收 Cookie。",
            },
            "cookie-sync": {
                "mode": "optional-credential",
                "required": False,
                "ready": False,
                "state": "login_required",
                "readiness": "login_required",
                "detail": "插件仅上报布尔登录状态；游客 SUB 不算登录凭据。",
            },
        },
    }


def test_weibo_status_keeps_auth_and_discovery_health_orthogonal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from openbiliclaw.runtime.weibo_producer import WeiboDiscoveryProducer

    cfg = Config()
    cfg.sources.weibo.enabled = True
    cfg.sources.weibo.source_modes = ("search",)
    database = Database(tmp_path / "weibo-health-status.db")
    database.initialize()
    producer = WeiboDiscoveryProducer(database=database, soul_engine=object(), client=object())
    producer.record_strategy_run(
        "search",
        units_used=0,
        discovered=0,
        reason="error",
        error_code="upstream_error",
    )
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: cfg)

    with TestClient(
        create_app(memory_manager=object(), database=database, soul_engine=object())
    ) as client:
        item = client.get("/api/sources/status").json()["weibo"]

    assert item["state"] == "no_auth"
    assert item["auth"]["auth_required"] is False
    assert item["discovery_state"] == "error"
    assert item["feed_paused"] is False
    assert (
        item["detail"]
        == "微博公开发现最近失败，将按节流策略自动重试。 初始化本人事件需要浏览器登录态。"
    )


def test_weibo_credentials_never_export_or_claim_a_stored_visitor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("OPENBILICLAW_DOUYIN_COOKIE", raising=False)
    monkeypatch.delenv("OPENBILICLAW_X_COOKIE", raising=False)
    cfg = Config()
    cfg.sources.weibo.enabled = True
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: cfg)
    database = Database(tmp_path / "weibo-credentials.db")
    database.initialize()

    with TestClient(
        create_app(memory_manager=object(), database=database, soul_engine=object())
    ) as client:
        item = client.get("/api/sources/credentials", params={"reveal_keys": "true"}).json()[
            "weibo"
        ]

    assert item["label"] == "微博浏览器登录态"
    assert item["value"] == ""
    assert item["available"] is False
    assert item["form"]["kind"] == "none"
    assert item["form"]["required_keys"] == []
    assert item["form"]["env_var"] is None
    assert item["summary"]
    assert "不读取或保存用户 Cookie" in item["detail"]


def test_weibo_browser_task_requires_enabled_source_and_claim_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from openbiliclaw.sources.weibo_tasks import WeiboTaskQueue

    cfg = _config_with_test_llm()
    cfg.sources.weibo.enabled = True
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: cfg)
    database = Database(tmp_path / "weibo-task-claim.db")
    database.initialize()
    queue = WeiboTaskQueue(database)
    task_id = queue.enqueue_with_id(
        "bootstrap_events",
        {"profile_update": False, "scopes": ["weibo_favorites"]},
    )
    assert task_id

    with TestClient(
        create_app(memory_manager=object(), database=database, soul_engine=object())
    ) as client:
        claimed = client.get("/api/sources/weibo/next-task")
        assert claimed.status_code == 200
        task = claimed.json()
        assert task["id"] == task_id
        assert task["claim_token"]

        stale = client.post(
            "/api/sources/weibo/task-result",
            json={"task_id": task_id, "status": "failed", "items": []},
        )
        assert stale.status_code == 409
        assert stale.json()["detail"] == "task_claim_conflict"

        accepted = client.post(
            "/api/sources/weibo/task-result",
            json={
                "task_id": task_id,
                "claim_token": task["claim_token"],
                "status": "failed",
                "items": [],
                "error": "weibo_login_required",
            },
        )
        assert accepted.status_code == 200
        assert queue.get(task_id)["status"] == "failed"

    cfg.sources.weibo.enabled = False
    with TestClient(
        create_app(memory_manager=object(), database=database, soul_engine=object())
    ) as client:
        assert client.get("/api/sources/weibo/next-task").status_code == 204


def test_weibo_config_api_get_put_and_disk_reload_round_trip_every_field(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import openbiliclaw.config as config_module
    from openbiliclaw.api.runtime_context import RuntimeContext

    cfg = _config_with_test_llm()
    config_path = tmp_path / "config.toml"
    real_load_config = config_module.load_config
    real_save_config = config_module.save_config
    real_save_config(cfg, config_path)
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(config_module, "load_config", lambda *_a, **_kw: cfg)
    monkeypatch.setattr(
        config_module,
        "save_config",
        lambda current, path=None: real_save_config(current, config_path),
    )

    async def _fake_rebuild(self: RuntimeContext, config: Config) -> None:
        self.config = config

    monkeypatch.setattr(RuntimeContext, "rebuild_from_config", _fake_rebuild)

    payload = {
        "sources": {
            "weibo": {
                "enabled": True,
                "source_modes": ["creator", "search", "hot", "search"],
                "daily_search_budget": 41,
                "daily_hot_budget": 12,
                "daily_creator_budget": 27,
                "request_interval_seconds": 5,
                "min_interval_minutes": 19,
            }
        },
        "scheduler": {"pool_source_shares": {"weibo": 7}},
    }
    with TestClient(
        create_app(memory_manager=object(), database=object(), soul_engine=object())
    ) as client:
        initial = client.get("/api/config").json()
        response = client.put("/api/config", json=payload)
        after = client.get("/api/config").json()

    assert initial["sources"]["weibo"] == {
        "recommendation_date_preset": "all",
        "recommendation_date_start": "",
        "recommendation_date_end": "",
        "recommendation_date_weight": 0.5,
        "enabled": False,
        "source_modes": ["search", "hot", "creator"],
        "daily_search_budget": 60,
        "daily_hot_budget": 10,
        "daily_creator_budget": 30,
        "request_interval_seconds": 3,
        "min_interval_minutes": 10,
    }
    assert response.status_code == 202
    expected = {
        "recommendation_date_preset": "all",
        "recommendation_date_start": "",
        "recommendation_date_end": "",
        "recommendation_date_weight": 0.5,
        "enabled": True,
        "source_modes": ["creator", "search", "hot"],
        "daily_search_budget": 41,
        "daily_hot_budget": 12,
        "daily_creator_budget": 27,
        "request_interval_seconds": 5,
        "min_interval_minutes": 19,
    }
    assert response.json()["config"]["sources"]["weibo"] == expected
    assert response.json()["config"]["scheduler"]["pool_source_shares"]["weibo"] == 7
    assert after["sources"]["weibo"] == expected
    assert after["scheduler"]["pool_source_shares"]["weibo"] == 7

    persisted = real_load_config(config_path)
    assert persisted.sources.weibo.enabled is True
    assert persisted.sources.weibo.source_modes == ("creator", "search", "hot")
    assert persisted.sources.weibo.daily_search_budget == 41
    assert persisted.sources.weibo.daily_hot_budget == 12
    assert persisted.sources.weibo.daily_creator_budget == 27
    assert persisted.sources.weibo.request_interval_seconds == 5
    assert persisted.sources.weibo.min_interval_minutes == 19
    assert persisted.scheduler.pool_source_shares["weibo"] == 7
    rendered = config_path.read_text(encoding="utf-8")
    assert "SUB=" not in rendered
    assert "weibo_cookie" not in rendered.lower()


def test_weibo_config_api_rejects_invalid_modes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = _config_with_test_llm()
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: cfg)
    database = Database(tmp_path / "weibo-invalid-modes.db")
    database.initialize()
    with TestClient(create_app(database=database)) as client:
        cases = [
            ({"source_modes": "search"}, "微博 source_modes 必须是数组"),
            ({"source_modes": []}, "微博 source_modes 包含不支持的值"),
            ({"source_modes": ["search", "feed"]}, "微博 source_modes 包含不支持的值"),
            (
                {"source_modes": ["creator"]},
                "微博 creator 模式需要同时启用 search 或 hot",
            ),
        ]
        for update, detail in cases:
            response = client.put("/api/config", json={"sources": {"weibo": update}})
            assert response.status_code == 400
            assert response.json()["detail"] == detail
    assert cfg.sources.weibo.source_modes == ("search", "hot", "creator")


def test_weibo_config_api_rejects_invalid_numbers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = _config_with_test_llm()
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: cfg)
    database = Database(tmp_path / "weibo-invalid-numbers.db")
    database.initialize()
    with TestClient(create_app(database=database)) as client:
        for field in (
            "daily_search_budget",
            "daily_hot_budget",
            "daily_creator_budget",
            "request_interval_seconds",
            "min_interval_minutes",
        ):
            original = getattr(cfg.sources.weibo, field)
            for value, detail_suffix in (
                (-1, "不能为负数"),
                ("not-an-integer", "必须是整数"),
                (1.9, "必须是整数"),
                (True, "必须是整数"),
            ):
                response = client.put(
                    "/api/config",
                    json={"sources": {"weibo": {field: value}}},
                )
                assert response.status_code == 400
                assert response.json()["detail"] == f"微博 {field} {detail_suffix}"
                assert getattr(cfg.sources.weibo, field) == original


def test_weibo_share_count_survives_recommendation_and_delight_http_serialization() -> None:
    class FakeDatabase:
        def get_recommendations(
            self,
            limit: int = 20,
            *,
            exclude_processed: bool = False,
        ) -> list[dict[str, Any]]:
            assert limit == 40
            assert exclude_processed is True
            return [
                {
                    "id": 91,
                    "bvid": "5023456789012345",
                    "content_id": "5023456789012345",
                    "content_url": "https://m.weibo.cn/detail/5023456789012345",
                    "source_platform": "weibo",
                    "content_type": "post",
                    "title": "一条公开微博",
                    "share_count": 321,
                    "franchise_key": "",
                }
            ]

        def get_delight_candidates(
            self,
            *,
            min_delight_score: float,
            limit: int,
            include_liked: bool = False,
            include_delivered: bool = False,
        ) -> list[dict[str, Any]]:
            del min_delight_score, limit, include_delivered
            assert include_liked is True
            return [
                {
                    "bvid": "5023456789012345",
                    "content_id": "5023456789012345",
                    "content_url": "https://m.weibo.cn/detail/5023456789012345",
                    "source_platform": "weibo",
                    "content_type": "post",
                    "title": "一条公开微博",
                    "share_count": 321,
                    "delight_score": 0.95,
                }
            ]

    client = TestClient(
        create_app(
            memory_manager=object(),
            database=FakeDatabase(),
            soul_engine=object(),
        )
    )
    recommendation = client.get("/api/recommendations").json()["items"][0]
    delight = client.get("/api/delight/pending-batch").json()["items"][0]
    client.close()

    assert recommendation["source_platform"] == "weibo"
    assert recommendation["content_type"] == "post"
    assert recommendation["share_count"] == 321
    assert delight["source_platform"] == "weibo"
    assert delight["content_type"] == "post"
    assert delight["share_count"] == 321


def test_weibo_share_count_survives_singular_delight_serialization() -> None:
    class FakeRuntimeController:
        def get_pending_delight(self) -> dict[str, object]:
            return {
                "bvid": "5023456789012345",
                "content_id": "5023456789012345",
                "content_url": "https://m.weibo.cn/detail/5023456789012345",
                "source_platform": "weibo",
                "content_type": "post",
                "title": "一条公开微博",
                "share_count": 321,
            }

    with TestClient(
        create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            runtime_controller=FakeRuntimeController(),
        )
    ) as client:
        item = client.get("/api/delight/pending").json()["item"]

    assert item["source_platform"] == "weibo"
    assert item["content_type"] == "post"
    assert item["share_count"] == 321


def test_weibo_candidate_reaches_scoped_recommendation_api_end_to_end(tmp_path: Path) -> None:
    from openbiliclaw.discovery.candidate_pipeline import (
        CandidateEvalOutcome,
        DiscoveryCandidatePipeline,
    )
    from openbiliclaw.discovery.engine import ContentDiscoveryEngine
    from openbiliclaw.memory.manager import MemoryManager
    from openbiliclaw.recommendation.engine import RecommendationEngine
    from openbiliclaw.soul.profile import InterestTag, PreferenceLayer, SoulProfile
    from openbiliclaw.sources.weibo import weibo_post_to_content

    database = Database(tmp_path / "weibo-recommendation-e2e.db")
    database.initialize()
    memory = MemoryManager(tmp_path / "data", database=database)
    memory.initialize()
    content = weibo_post_to_content(
        {
            "id": "5023456789012345",
            "bid": "P9Example",
            "text_raw": "一条用于来源接入回归的公开微博",
            "reposts_count": 321,
            "comments_count": 17,
            "attitudes_count": 88,
            "user": {"id": 1234567890, "screen_name": "公开作者"},
        },
        strategy="weibo-search",
    )
    assert content is not None

    discovery = ContentDiscoveryEngine(database=database)
    pipeline = DiscoveryCandidatePipeline(
        database=database,
        discovery_engine=discovery,
        pool_target_count=10,
    )
    assert pipeline.enqueue_candidates([content], source_context="weibo-search") == 1
    claim = pipeline.claim_batch(limit=1)
    assert claim is not None
    for candidate in claim.items:
        candidate.relevance_score = 0.99
        candidate.relevance_reason = "deterministic test evaluator acceptance"
        candidate.style_key = "social_chat"
        candidate.topic_key = "source integration"
        candidate.topic_group = "technology"

    class _NoLLM:
        async def complete_structured_task(self, **_kwargs: object) -> object:
            raise AssertionError("precomputed recommendation serve must not call an LLM")

    profile = SoulProfile(
        core_traits=["curious"],
        preferences=PreferenceLayer(
            interests=[InterestTag(name="source integration", category="technology", weight=0.9)]
        ),
    )
    recommendation_engine = RecommendationEngine(llm=_NoLLM(), database=database)

    async def _admit_and_serve() -> tuple[dict[str, int], list[object]]:
        admitted = await pipeline.complete_claim(
            CandidateEvalOutcome(claim=claim, scores=(0.99,), elapsed_seconds=0.0)
        )
        database.update_pool_copy(
            content.item_key,
            expression="微博来源链路回归",
            topic_label="来源接入",
        )
        recommendations = await recommendation_engine.serve(
            profile,
            limit=1,
            source_platform="weibo",
        )
        return admitted, recommendations

    admitted, recommendations = asyncio.run(_admit_and_serve())
    assert admitted["cached"] == 1
    assert len(recommendations) == 1

    class _Soul:
        async def get_profile(self) -> SoulProfile:
            return profile

    app = create_app(
        memory_manager=memory,
        database=database,
        soul_engine=_Soul(),
        recommendation_engine=recommendation_engine,
    )
    with TestClient(app) as client:
        response = client.get("/api/recommendations")
        assert response.status_code == 200
        assert len(response.json()["items"]) == 1
        item = response.json()["items"][0]
        click = client.post(
            "/api/recommendation-click",
            json={
                "recommendation_id": item["id"],
                "bvid": item["item_key"],
                "content_id": item["content_id"],
                "content_url": item["content_url"],
                "source_platform": item["source_platform"],
                "title": item["title"],
                "topic_label": item["topic_label"],
                "up_name": item["up_name"],
                "request_id": "click-weibo-source-e2e",
            },
        )

    assert item["item_key"] == "weibo:5023456789012345"
    assert item["content_id"] == "5023456789012345"
    assert item["content_url"] == "https://weibo.com/1234567890/P9Example"
    assert item["source_platform"] == "weibo"
    assert item["content_type"] == "post"
    assert item["share_count"] == 321
    assert click.status_code == 200
    assert click.json()["bvid"] == item["item_key"]
    assert click.json()["processing"] == "queued"

    events = memory.query_events(event_types=["click"], limit=10)
    assert len(events) == 1
    event = events[0]
    assert event["url"] == item["content_url"]
    assert "微博" in event["context"]
    metadata = json.loads(event["metadata"])
    assert metadata["bvid"] == item["item_key"]
    assert metadata["content_id"] == item["content_id"]
    assert metadata["source_platform"] == "weibo"
