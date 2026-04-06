"""Tests for the OpenClaw adapter contracts."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.integrations.openclaw.bootstrap import (
    OpenClawAdapterServices,
    build_openclaw_adapter,
    build_openclaw_adapter_services,
)
from openbiliclaw.integrations.openclaw.errors import AdapterValidationError
from openbiliclaw.integrations.openclaw.operations import OpenClawAdapter
from openbiliclaw.integrations.openclaw.schemas import (
    FeedbackRequest,
    ProfileResponse,
    RecommendationItem,
    RecommendationResponse,
    RuntimeStatusResponse,
    SyncAccountResponse,
)
from openbiliclaw.recommendation.engine import Recommendation
from openbiliclaw.soul.profile import InterestTag, PreferenceLayer, SoulProfile


def test_profile_response_serializes_only_public_fields() -> None:
    payload = ProfileResponse(
        initialized=True,
        personality_portrait="你喜欢顺着问题往深处钻。",
        core_traits=["好奇", "耐心"],
        deep_needs=["把问题想透"],
        top_interests=["国际时事", "城市观察"],
    )

    assert asdict(payload) == {
        "initialized": True,
        "personality_portrait": "你喜欢顺着问题往深处钻。",
        "core_traits": ["好奇", "耐心"],
        "deep_needs": ["把问题想透"],
        "top_interests": ["国际时事", "城市观察"],
    }


def test_recommendation_response_serializes_trimmed_items() -> None:
    payload = RecommendationResponse(
        items=[
            RecommendationItem(
                recommendation_id=7,
                bvid="BV1TEST",
                title="把城市结构讲透",
                up_name="城市观察局",
                cover_url="https://example.com/cover.jpg",
                reason="这条会对上你最近那股想把结构看明白的劲头。",
                topic_label="你最近那股想把结构看明白的劲头",
                confidence=0.87,
            )
        ]
    )

    assert asdict(payload) == {
        "items": [
            {
                "recommendation_id": 7,
                "bvid": "BV1TEST",
                "title": "把城市结构讲透",
                "up_name": "城市观察局",
                "cover_url": "https://example.com/cover.jpg",
                "reason": "这条会对上你最近那股想把结构看明白的劲头。",
                "topic_label": "你最近那股想把结构看明白的劲头",
                "confidence": 0.87,
            }
        ]
    }


def test_runtime_status_response_serializes_public_runtime_fields() -> None:
    payload = RuntimeStatusResponse(
        initialized=True,
        recommendation_count=5,
        pending_signal_events=3,
        unread_count=2,
        pool_available_count=18,
        pool_target_count=30,
        last_discovered_count=7,
        last_refresh_at="2026-03-15T12:00:00+08:00",
        last_account_sync_at="2026-03-15T12:05:00+08:00",
        last_account_sync_error="",
    )

    assert asdict(payload) == {
        "initialized": True,
        "recommendation_count": 5,
        "pending_signal_events": 3,
        "unread_count": 2,
        "pool_available_count": 18,
        "pool_target_count": 30,
        "last_discovered_count": 7,
        "last_refresh_at": "2026-03-15T12:00:00+08:00",
        "last_account_sync_at": "2026-03-15T12:05:00+08:00",
        "last_account_sync_error": "",
    }


def test_sync_account_response_serializes_summary() -> None:
    payload = SyncAccountResponse(synced=True, new_event_count=12, errors=["timeout"])

    assert asdict(payload) == {
        "synced": True,
        "new_event_count": 12,
        "errors": ["timeout"],
    }


def test_feedback_request_rejects_unsupported_feedback_type() -> None:
    with pytest.raises(AdapterValidationError):
        FeedbackRequest(recommendation_id=7, feedback_type="bookmark")


def test_feedback_request_rejects_comment_without_note() -> None:
    with pytest.raises(AdapterValidationError):
        FeedbackRequest(recommendation_id=7, feedback_type="comment", note="")


def test_feedback_request_normalizes_valid_payload() -> None:
    payload = FeedbackRequest(recommendation_id=7, feedback_type=" Like ", note=" 很对胃口 ")

    assert payload.feedback_type == "like"
    assert payload.note == "很对胃口"


class _FakeSoulEngine:
    def __init__(self) -> None:
        self.profile = SoulProfile(
            personality_portrait="你会反复追问问题背后的结构。",
            core_traits=["深究", "克制"],
            deep_needs=["把复杂问题想透"],
            preferences=PreferenceLayer(
                interests=[
                    InterestTag(name="国际时事", category="知识", weight=0.92),
                    InterestTag(name="城市观察", category="人文", weight=0.73),
                ]
            ),
        )
        self.feedback_batches = 0
        self.immediate_calls: list[tuple[str, str, str]] = []

    async def get_profile(self) -> SoulProfile:
        return self.profile

    def record_immediate_feedback_cognition(
        self,
        *,
        feedback_type: str,
        title: str,
        note: str,
    ) -> None:
        self.immediate_calls.append((feedback_type, title, note))

    async def process_feedback_batch_if_needed(self) -> dict[str, object]:
        self.feedback_batches += 1
        return {"processed": True}


class _FakeMemoryManager:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def propagate_event(self, event: dict[str, object]) -> None:
        self.events.append(event)


class _FakeDatabase:
    def __init__(self) -> None:
        self.updated: list[tuple[int, str, str]] = []
        self.recommendation_list_calls: list[int] = []

    def get_recommendation_by_id(self, recommendation_id: int) -> dict[str, object] | None:
        if recommendation_id == 404:
            return None
        return {
            "id": recommendation_id,
            "bvid": "BV1REC",
            "title": "把国际局势讲出结构感",
        }

    def get_recommendations(self, limit: int = 100) -> list[dict[str, object]]:
        self.recommendation_list_calls.append(limit)
        return [
            {
                "id": 31,
                "bvid": "BV1DB",
                "title": "从系统论看复杂问题",
                "up_name": "复杂性观察者",
                "cover_url": "https://example.com/db-cover.jpg",
                "expression": "这条更像是刚补完货后直接该拿给你看的那种。",
                "topic": "你最近那股想把系统想透的劲头",
                "confidence": 0.86,
            }
        ]

    def update_recommendation_feedback(
        self,
        recommendation_id: int,
        *,
        feedback_type: str,
        feedback_note: str = "",
    ) -> None:
        self.updated.append((recommendation_id, feedback_type, feedback_note))


class _FakeRuntimeController:
    def __init__(self) -> None:
        self.refresh_if_needed_calls = 0
        self.refresh_after_feedback_calls = 0

    def get_runtime_status(self) -> dict[str, object]:
        return {
            "initialized": True,
            "recommendation_count": 6,
            "pending_signal_events": 4,
            "unread_count": 3,
            "pool_available_count": 16,
            "pool_target_count": 30,
            "last_refresh_at": "2026-03-15T12:00:00+08:00",
        }

    async def refresh_if_needed(self) -> dict[str, object]:
        self.refresh_if_needed_calls += 1
        return {"refreshed": True}

    async def refresh_after_feedback(self) -> dict[str, object]:
        self.refresh_after_feedback_calls += 1
        return {"refreshed": True}


class _FakeAccountSyncService:
    def __init__(self) -> None:
        self.sync_calls = 0

    async def sync_now(self) -> dict[str, object]:
        self.sync_calls += 1
        return {"synced": True, "new_event_count": 9, "errors": []}

    def get_runtime_status(self) -> dict[str, object]:
        return {
            "last_account_sync_at": "2026-03-15T12:05:00+08:00",
            "last_account_sync_error": "",
        }


class _FakeRecommendationEngine:
    def __init__(self) -> None:
        self.calls: list[tuple[object, int]] = []

    async def generate_recommendations(
        self,
        discovered: list[DiscoveredContent] | None,
        profile: SoulProfile,
        limit: int = 10,
    ) -> list[Recommendation]:
        self.calls.append((discovered, limit))
        return [
            Recommendation(
                content=DiscoveredContent(
                    bvid="BV1REC",
                    title="把国际局势讲出结构感",
                    up_name="结构控",
                    cover_url="https://example.com/cover.jpg",
                    relevance_score=0.91,
                ),
                recommendation_id=11,
                expression="这条能接住你最近那股想把脉络捋顺的劲头。",
                topic_label="你最近那股想把脉络捋顺的劲头",
                confidence=0.91,
            )
        ]


def _build_adapter() -> tuple[
    OpenClawAdapter,
    _FakeSoulEngine,
    _FakeMemoryManager,
    _FakeDatabase,
    _FakeRuntimeController,
    _FakeAccountSyncService,
    _FakeRecommendationEngine,
]:
    soul_engine = _FakeSoulEngine()
    memory_manager = _FakeMemoryManager()
    database = _FakeDatabase()
    runtime_controller = _FakeRuntimeController()
    account_sync_service = _FakeAccountSyncService()
    recommendation_engine = _FakeRecommendationEngine()
    services = SimpleNamespace(
        soul_engine=soul_engine,
        memory_manager=memory_manager,
        database=database,
        runtime_controller=runtime_controller,
        account_sync_service=account_sync_service,
        recommendation_engine=recommendation_engine,
    )
    return (
        OpenClawAdapter(services=services),
        soul_engine,
        memory_manager,
        database,
        runtime_controller,
        account_sync_service,
        recommendation_engine,
    )


@pytest.mark.asyncio
async def test_get_profile_returns_trimmed_profile_response() -> None:
    adapter, *_ = _build_adapter()

    result = await adapter.get_profile()

    assert result == ProfileResponse(
        initialized=True,
        personality_portrait="你会反复追问问题背后的结构。",
        core_traits=["深究", "克制"],
        deep_needs=["把复杂问题想透"],
        top_interests=["国际时事", "城市观察"],
    )


@pytest.mark.asyncio
async def test_get_runtime_status_merges_refresh_and_account_sync_status() -> None:
    adapter, *_ = _build_adapter()

    result = await adapter.get_runtime_status()

    assert result == RuntimeStatusResponse(
        initialized=True,
        recommendation_count=6,
        pending_signal_events=4,
        unread_count=3,
        pool_available_count=16,
        pool_target_count=30,
        last_refresh_at="2026-03-15T12:00:00+08:00",
        last_account_sync_at="2026-03-15T12:05:00+08:00",
        last_account_sync_error="",
    )


@pytest.mark.asyncio
async def test_sync_account_delegates_to_account_sync_service() -> None:
    adapter, _, _, _, _, account_sync_service, _ = _build_adapter()

    result = await adapter.sync_account()

    assert result == SyncAccountResponse(synced=True, new_event_count=9, errors=[])
    assert account_sync_service.sync_calls == 1


@pytest.mark.asyncio
async def test_recommend_refreshes_then_returns_trimmed_items() -> None:
    adapter, _, _, database, runtime_controller, _, recommendation_engine = _build_adapter()

    result = await adapter.recommend(limit=3, refresh_if_needed=True)

    assert result == RecommendationResponse(
        items=[
            RecommendationItem(
                recommendation_id=31,
                bvid="BV1DB",
                title="从系统论看复杂问题",
                up_name="复杂性观察者",
                cover_url="https://example.com/db-cover.jpg",
                reason="这条更像是刚补完货后直接该拿给你看的那种。",
                topic_label="你最近那股想把系统想透的劲头",
                confidence=0.86,
            )
        ]
    )
    assert runtime_controller.refresh_if_needed_calls == 1
    assert database.recommendation_list_calls == [3]
    assert recommendation_engine.calls == []


@pytest.mark.asyncio
async def test_recommend_without_refresh_generates_new_recommendations() -> None:
    adapter, _, _, database, runtime_controller, _, recommendation_engine = _build_adapter()

    result = await adapter.recommend(limit=3, refresh_if_needed=False)

    assert result.items[0].recommendation_id == 11
    assert runtime_controller.refresh_if_needed_calls == 0
    assert database.recommendation_list_calls == []
    assert recommendation_engine.calls == [(None, 3)]


@pytest.mark.asyncio
async def test_recommend_falls_back_to_cached_rows_when_refresh_times_out() -> None:
    adapter, _, _, database, runtime_controller, _, recommendation_engine = _build_adapter()
    adapter = OpenClawAdapter(
        services=adapter.services,
        refresh_timeout_seconds=0.001,
    )

    async def slow_refresh() -> dict[str, object]:
        runtime_controller.refresh_if_needed_calls += 1
        await asyncio.sleep(0.02)
        return {"refreshed": True}

    runtime_controller.refresh_if_needed = slow_refresh  # type: ignore[method-assign]
    result = await adapter.recommend(limit=3, refresh_if_needed=True)

    assert result.items[0].recommendation_id == 31
    assert database.recommendation_list_calls == [3]
    assert recommendation_engine.calls == []


@pytest.mark.asyncio
async def test_submit_feedback_records_event_and_runs_post_feedback_hooks() -> None:
    (
        adapter,
        soul_engine,
        memory_manager,
        database,
        runtime_controller,
        _account_sync_service,
        _recommendation_engine,
    ) = _build_adapter()

    result = await adapter.submit_feedback(
        FeedbackRequest(recommendation_id=7, feedback_type="like", note="很对胃口")
    )

    assert asdict(result) == {
        "ok": True,
        "recommendation_id": 7,
        "feedback_type": "like",
    }
    assert database.updated == [(7, "like", "很对胃口")]
    assert memory_manager.events == [
        {
            "event_type": "feedback",
            "title": "把国际局势讲出结构感",
            "metadata": {
                "recommendation_id": 7,
                "bvid": "BV1REC",
                "feedback_type": "like",
                "feedback_note": "很对胃口",
            },
        }
    ]
    assert soul_engine.immediate_calls == [("like", "把国际局势讲出结构感", "很对胃口")]
    assert soul_engine.feedback_batches == 1
    assert runtime_controller.refresh_after_feedback_calls == 1


def test_build_openclaw_adapter_services_reuses_shared_database(monkeypatch) -> None:
    import openbiliclaw.integrations.openclaw.bootstrap as bootstrap_module

    created_databases: list[object] = []
    created_memories: list[object] = []
    registered_strategies: list[str] = []

    class FakeDatabase:
        def __init__(self, path: str) -> None:
            self.path = path
            self.initialized = 0
            created_databases.append(self)

        def initialize(self) -> None:
            self.initialized += 1

    class FakeMemoryManager:
        def __init__(self, data_path: str, database=None) -> None:
            self.data_path = data_path
            self.database = database
            self.initialized = 0
            created_memories.append(self)

        def initialize(self) -> None:
            self.initialized += 1

    class FakeSoulEngine:
        def __init__(self, *, llm: object, memory: object) -> None:
            self.llm = llm
            self.memory = memory

    class FakeLLMService:
        def __init__(self, *, registry: object, memory: object) -> None:
            self.registry = registry
            self.memory = memory

    class FakeRecommendationEngine:
        def __init__(self, *, llm: object, database: object, curator: object = None, embedding_service: object = None) -> None:
            self.llm = llm
            self.database = database

    class FakeBilibiliClient:
        def __init__(self, *, cookie: str) -> None:
            self.cookie = cookie

    class FakeDiscoveryEngine:
        def __init__(self, *, llm_service: object, database: object, embedding_service: object = None, concurrency: object = None) -> None:
            self.llm_service = llm_service
            self.database = database

        def register_strategy(self, strategy: object) -> None:
            registered_strategies.append(str(getattr(strategy, "name", "")))

    class FakeStrategy:
        def __init__(self, *args, **kwargs) -> None:
            self.args = args
            self.kwargs = kwargs
            self.name = self.__class__.__name__

    class FakeRuntimeController:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    class FakeAccountSyncService:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    fake_config = SimpleNamespace(
        data_path=Path("/tmp/openclaw-data"),
        bilibili=SimpleNamespace(cookie="raw-cookie"),
        scheduler=SimpleNamespace(
            pool_target_count=30,
            account_sync_interval_hours=6,
        ),
    )

    monkeypatch.setattr(bootstrap_module, "load_config", lambda: fake_config)
    monkeypatch.setattr(bootstrap_module, "build_llm_registry", lambda config: "registry")
    monkeypatch.setattr(bootstrap_module, "resolve_runtime_cookie", lambda **_: "cookie")
    monkeypatch.setattr(bootstrap_module, "Database", FakeDatabase)
    monkeypatch.setattr(bootstrap_module, "MemoryManager", FakeMemoryManager)
    monkeypatch.setattr(bootstrap_module, "SoulEngine", FakeSoulEngine)
    monkeypatch.setattr(bootstrap_module, "LLMService", FakeLLMService)
    monkeypatch.setattr(bootstrap_module, "RecommendationEngine", FakeRecommendationEngine)
    monkeypatch.setattr(bootstrap_module, "BilibiliAPIClient", FakeBilibiliClient)
    monkeypatch.setattr(bootstrap_module, "ContentDiscoveryEngine", FakeDiscoveryEngine)
    monkeypatch.setattr(bootstrap_module, "SearchStrategy", FakeStrategy)
    monkeypatch.setattr(bootstrap_module, "TrendingStrategy", FakeStrategy)
    monkeypatch.setattr(bootstrap_module, "RelatedChainStrategy", FakeStrategy)
    monkeypatch.setattr(bootstrap_module, "ExploreStrategy", FakeStrategy)
    monkeypatch.setattr(bootstrap_module, "ContinuousRefreshController", FakeRuntimeController)
    monkeypatch.setattr(bootstrap_module, "AccountSyncService", FakeAccountSyncService)

    services = build_openclaw_adapter_services()

    assert isinstance(services, OpenClawAdapterServices)
    assert len(created_databases) == 1
    assert created_databases[0].initialized == 1
    assert len(created_memories) == 1
    assert created_memories[0].initialized == 1
    assert created_memories[0].database is created_databases[0]
    assert services.database is created_databases[0]
    assert services.memory_manager is created_memories[0]
    assert registered_strategies == [
        "FakeStrategy",
        "FakeStrategy",
        "FakeStrategy",
        "FakeStrategy",
    ]


def test_build_openclaw_adapter_returns_ready_adapter(monkeypatch) -> None:
    import openbiliclaw.integrations.openclaw.bootstrap as bootstrap_module

    fake_services = OpenClawAdapterServices(
        config=object(),
        database=object(),
        memory_manager=object(),
        soul_engine=object(),
        llm_service=object(),
        bilibili_client=object(),
        discovery_engine=object(),
        recommendation_engine=object(),
        runtime_controller=object(),
        account_sync_service=object(),
    )

    monkeypatch.setattr(
        bootstrap_module,
        "build_openclaw_adapter_services",
        lambda: fake_services,
    )

    adapter = build_openclaw_adapter()

    assert isinstance(adapter, OpenClawAdapter)
    assert adapter.services is fake_services
