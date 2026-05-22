"""Tests for the OpenClaw adapter contracts."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime
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
    ChatRequest,
    ChatResponse,
    DelightItem,
    DelightResponse,
    FeedbackRequest,
    InterestProbeResponse,
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


def test_delight_response_serializes_with_item() -> None:
    payload = DelightResponse(
        item=DelightItem(
            bvid="BV1DELIGHT",
            title="跨域发现",
            delight_reason="你之前聊到过想搞明白复杂系统。",
            delight_score=0.92,
            delight_hook="深层共鸣",
            cover_url="https://example.com/cover.jpg",
        ),
    )

    assert asdict(payload) == {
        "item": {
            "bvid": "BV1DELIGHT",
            "title": "跨域发现",
            "delight_reason": "你之前聊到过想搞明白复杂系统。",
            "delight_score": 0.92,
            "delight_hook": "深层共鸣",
            "cover_url": "https://example.com/cover.jpg",
        },
    }


def test_delight_response_serializes_without_item() -> None:
    payload = DelightResponse(item=None)

    assert asdict(payload) == {"item": None}


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


class _FakeSpeculativeInterest:
    """Minimal stand-in for ``speculator.SpeculativeInterest``."""

    def __init__(
        self,
        domain: str = "建筑美学",
        category: str = "人文",
        reason: str = "你最近看了很多关于结构和空间的内容。",
        confidence: float = 0.45,
        weight: float = 0.4,
        confirmation_count: int = 0,
        experience_mode: str = "knowledge",
        entry_load: str = "heavy",
        specifics: list[object] | None = None,
    ) -> None:
        self.domain = domain
        self.category = category
        self.reason = reason
        self.confidence = confidence
        self.weight = weight
        self.confirmation_count = confirmation_count
        self.experience_mode = experience_mode
        self.entry_load = entry_load
        self.specifics = specifics or []


class _FakeSpeculativeSpecific:
    def __init__(self, name: str = "") -> None:
        self.name = name


class _FakeSpeculator:
    def __init__(self, specs: list[_FakeSpeculativeInterest] | None = None) -> None:
        self._specs = specs if specs is not None else [_FakeSpeculativeInterest()]

    def get_active_speculations(self) -> list[_FakeSpeculativeInterest]:
        return list(self._specs)


class _FakeLLMService:
    """Minimal LLM service that returns a canned Socratic reply."""

    async def complete_socratic_dialogue(
        self,
        *,
        user_message: str,
        history: list[dict[str, str]] | None = None,
        caller: str = "",
    ) -> SimpleNamespace:
        return SimpleNamespace(
            content=("你说的这个方向我有个猜测——你是不是其实更在意底层结构而不只是结论？")
        )


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
        self._speculator = _FakeSpeculator()
        self._llm = None  # Socratic dialogue falls through to llm_service

    async def get_profile(self) -> SoulProfile:
        return self.profile

    async def learn_from_dialogue(
        self,
        *,
        user_message: str,
        assistant_reply: str,
        session: str,
    ) -> None:
        pass  # no-op for tests

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
        self.runtime_state: dict[str, object] = {}

    async def propagate_event(self, event: dict[str, object]) -> None:
        self.events.append(event)

    def load_discovery_runtime_state(self) -> dict[str, object]:
        return dict(self.runtime_state)

    def save_discovery_runtime_state(self, state: dict[str, object]) -> None:
        self.runtime_state = dict(state)


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
        self.delight_candidate: dict[str, object] | None = {
            "bvid": "BV1DLRT",
            "title": "跨域惊喜视频",
            "delight_reason": "这条会戳到你一直想搞明白的那个方向。",
            "delight_score": 0.93,
            "delight_hook": "跨域惊喜",
            "cover_url": "https://example.com/delight.jpg",
        }

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

    def get_pending_delight(self) -> dict[str, object] | None:
        return self.delight_candidate

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
    llm_service = _FakeLLMService()
    services = SimpleNamespace(
        soul_engine=soul_engine,
        memory_manager=memory_manager,
        database=database,
        runtime_controller=runtime_controller,
        account_sync_service=account_sync_service,
        recommendation_engine=recommendation_engine,
        llm_service=llm_service,
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


@pytest.mark.asyncio
async def test_get_delight_returns_candidate_when_available() -> None:
    adapter, _, _, _, runtime_controller, _, _ = _build_adapter()

    result = await adapter.get_delight()

    assert result.item is not None
    assert result.item.bvid == "BV1DLRT"
    assert result.item.delight_hook == "跨域惊喜"
    assert result.item.delight_score == 0.93


@pytest.mark.asyncio
async def test_get_delight_returns_none_when_no_candidate() -> None:
    adapter, _, _, _, runtime_controller, _, _ = _build_adapter()
    runtime_controller.delight_candidate = None

    result = await adapter.get_delight()

    assert result.item is None


def test_build_openclaw_adapter_services_reuses_shared_database(monkeypatch) -> None:
    import openbiliclaw.integrations.openclaw.bootstrap as bootstrap_module

    created_databases: list[object] = []
    created_memories: list[object] = []
    registered_strategies: list[str] = []
    created_strategy_kwargs: list[dict[str, object]] = []

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
        def __init__(self, *, llm: object, memory: object, module_overrides=None, **kwargs) -> None:
            self.llm = llm
            self.memory = memory
            self.module_overrides = module_overrides
            self.kwargs = kwargs

    class FakeLLMService:
        def __init__(self, *, registry: object, memory: object, module_overrides=None) -> None:
            self.registry = registry
            self.memory = memory
            self.module_overrides = module_overrides

    class FakeRecommendationEngine:
        def __init__(
            self,
            *,
            llm: object,
            database: object,
            curator: object = None,
            embedding_service: object = None,
        ) -> None:
            self.llm = llm
            self.database = database

    class FakeBilibiliClient:
        def __init__(self, *, cookie: str) -> None:
            self.cookie = cookie

    class FakeDiscoveryEngine:
        def __init__(
            self,
            *,
            llm_service: object,
            database: object,
            embedding_service: object = None,
            concurrency: object = None,
        ) -> None:
            self.llm_service = llm_service
            self.database = database

        def register_strategy(self, strategy: object) -> None:
            registered_strategies.append(str(getattr(strategy, "name", "")))

    class FakeStrategy:
        def __init__(self, *args, **kwargs) -> None:
            self.args = args
            self.kwargs = kwargs
            self.name = self.__class__.__name__
            created_strategy_kwargs.append(kwargs)

    class FakeRuntimeController:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    class FakeAccountSyncService:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    fake_config = SimpleNamespace(
        data_path=Path("/tmp/openclaw-data"),
        llm=SimpleNamespace(
            soul=SimpleNamespace(provider="claude", model="claude-sonnet"),
            discovery=SimpleNamespace(provider="deepseek", model="deepseek-chat"),
            recommendation=SimpleNamespace(provider="", model=""),
            evaluation=SimpleNamespace(provider="", model="gpt-4o-mini"),
        ),
        bilibili=SimpleNamespace(cookie="raw-cookie"),
        sources=SimpleNamespace(
            xiaohongshu=SimpleNamespace(enabled=False),
            douyin=SimpleNamespace(enabled=False),
            youtube=SimpleNamespace(enabled=False),
        ),
        scheduler=SimpleNamespace(
            enabled=True,
            pause_on_extension_disconnect=False,
            pool_target_count=30,
            pool_source_shares={
                "bilibili": 8,
                "xiaohongshu": 3,
                "douyin": 2,
                "youtube": 1,
            },
            account_sync_interval_hours=6,
            refresh_check_interval_seconds=77,
            signal_event_threshold=9,
            trending_refresh_hours=5,
            explore_refresh_hours=18,
            discovery_limit=17,
            proactive_push_interval_seconds=155,
            speculation_interval_minutes=22,
            speculation_ttl_days=8,
            speculation_cooldown_days=9,
            speculation_confirmation_threshold=4,
            speculation_max_active=6,
            speculation_max_primary_interests=17,
            speculation_max_secondary_interests=66,
            speculator_idle_interval_minutes=11,
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
    assert [kwargs.get("database") for kwargs in created_strategy_kwargs] == [
        created_databases[0],
        created_databases[0],
        created_databases[0],
        created_databases[0],
    ]
    assert services.soul_engine.module_overrides["soul"].provider == "claude"
    assert services.soul_engine.kwargs["speculation_interval_minutes"] == 22
    assert services.soul_engine.kwargs["speculation_ttl_days"] == 8
    assert services.soul_engine.kwargs["speculation_cooldown_days"] == 9
    assert services.soul_engine.kwargs["speculation_confirmation_threshold"] == 4
    assert services.soul_engine.kwargs["speculation_max_active"] == 6
    assert services.soul_engine.kwargs["speculation_max_primary_interests"] == 17
    assert services.soul_engine.kwargs["speculation_max_secondary_interests"] == 66
    assert services.soul_engine.kwargs["speculator_idle_interval_minutes"] == 11
    assert services.llm_service.module_overrides["discovery"].provider == "deepseek"
    assert services.llm_service.module_overrides["evaluation"].model == "gpt-4o-mini"
    assert registered_strategies == [
        "FakeStrategy",
        "FakeStrategy",
        "FakeStrategy",
        "FakeStrategy",
    ]
    assert services.runtime_controller.kwargs["pool_source_shares"] == {
        "bilibili": 8,
    }
    assert services.runtime_controller.kwargs["scheduler_config"] is fake_config.scheduler
    assert "presence" in services.runtime_controller.kwargs
    assert "youtube_producer" in services.runtime_controller.kwargs
    assert services.runtime_controller.kwargs["youtube_producer"] is None
    assert services.runtime_controller.kwargs["check_interval_seconds"] == 77
    assert services.runtime_controller.kwargs["signal_event_threshold"] == 9
    assert services.runtime_controller.kwargs["trending_refresh_hours"] == 5
    assert services.runtime_controller.kwargs["explore_refresh_hours"] == 18
    assert services.runtime_controller.kwargs["discovery_limit"] == 17
    assert services.runtime_controller.kwargs["proactive_push_interval_seconds"] == 155


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


@pytest.mark.asyncio
async def test_chat_delegates_to_socratic_dialogue() -> None:
    adapter, soul_engine, *_ = _build_adapter()

    result = await adapter.chat(ChatRequest(message="我最近对建筑很感兴趣", session="test"))

    assert isinstance(result, ChatResponse)
    assert "底层结构" in result.reply
    assert result.session == "test"


@pytest.mark.asyncio
async def test_chat_rejects_empty_message() -> None:
    from openbiliclaw.integrations.openclaw.errors import AdapterValidationError

    with pytest.raises(AdapterValidationError):
        ChatRequest(message="   ", session="test")


@pytest.mark.asyncio
async def test_get_next_probe_returns_top_speculation() -> None:
    adapter, soul_engine, *_ = _build_adapter()

    result = await adapter.get_next_probe()

    assert isinstance(result, InterestProbeResponse)
    assert result.probe is not None
    assert result.probe.domain == "建筑美学"
    assert result.probe.category == "人文"
    assert "建筑美学" in result.probe.question
    assert "认不认" in result.probe.question


@pytest.mark.asyncio
async def test_get_next_probe_returns_none_when_no_speculations() -> None:
    adapter, soul_engine, *_ = _build_adapter()
    soul_engine._speculator = _FakeSpeculator(specs=[])

    result = await adapter.get_next_probe()

    assert result.probe is None


@pytest.mark.asyncio
async def test_get_next_probe_picks_lowest_confirmation_count() -> None:
    adapter, soul_engine, *_ = _build_adapter()
    soul_engine._speculator = _FakeSpeculator(
        specs=[
            _FakeSpeculativeInterest(domain="量子物理", confirmation_count=2, weight=0.9),
            _FakeSpeculativeInterest(domain="分子料理", confirmation_count=0, weight=0.3),
        ]
    )

    result = await adapter.get_next_probe()

    assert result.probe is not None
    assert result.probe.domain == "分子料理"


@pytest.mark.asyncio
async def test_get_next_probe_includes_specifics_in_question() -> None:
    adapter, soul_engine, *_ = _build_adapter()
    soul_engine._speculator = _FakeSpeculator(
        specs=[
            _FakeSpeculativeInterest(
                domain="建筑美学",
                reason="结构和空间让你着迷。",
                specifics=[
                    _FakeSpeculativeSpecific(name="参数化设计"),
                    _FakeSpeculativeSpecific(name="混凝土美学"),
                ],
            ),
        ]
    )

    result = await adapter.get_next_probe()

    assert result.probe is not None
    assert "参数化设计" in result.probe.question
    assert "混凝土美学" in result.probe.question
    assert result.probe.specifics == ["参数化设计", "混凝土美学"]


@pytest.mark.asyncio
async def test_get_next_probe_prefers_fresher_experience_axis() -> None:
    adapter, soul_engine, memory_manager, *_ = _build_adapter()
    memory_manager.runtime_state = {
        "probed_axes": {
            "knowledge|heavy": datetime.now().isoformat(),
        }
    }
    soul_engine._speculator = _FakeSpeculator(
        specs=[
            _FakeSpeculativeInterest(
                domain="量子物理",
                confirmation_count=0,
                weight=0.9,
                experience_mode="knowledge",
                entry_load="heavy",
            ),
            _FakeSpeculativeInterest(
                domain="城市漫游",
                confirmation_count=0,
                weight=0.5,
                experience_mode="wander_observe",
                entry_load="light",
            ),
        ]
    )

    result = await adapter.get_next_probe()

    assert result.probe is not None
    assert result.probe.domain == "城市漫游"


@pytest.mark.asyncio
async def test_get_next_probe_records_history_and_avoids_repeat() -> None:
    adapter, soul_engine, memory_manager, *_ = _build_adapter()
    soul_engine._speculator = _FakeSpeculator(
        specs=[
            _FakeSpeculativeInterest(
                domain="量子物理",
                confirmation_count=0,
                weight=0.9,
                experience_mode="knowledge",
                entry_load="heavy",
            ),
            _FakeSpeculativeInterest(
                domain="城市漫游",
                confirmation_count=0,
                weight=0.5,
                experience_mode="wander_observe",
                entry_load="light",
            ),
        ]
    )

    first = await adapter.get_next_probe()
    second = await adapter.get_next_probe()

    assert first.probe is not None
    assert second.probe is not None
    assert first.probe.domain == "量子物理"
    assert second.probe.domain == "城市漫游"
    assert "量子物理" in memory_manager.runtime_state["probed_domains"]
    assert "knowledge|heavy" in memory_manager.runtime_state["probed_axes"]
