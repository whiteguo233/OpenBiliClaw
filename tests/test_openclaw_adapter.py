"""Tests for the OpenClaw adapter contracts."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from openbiliclaw.discovery.candidate_pipeline import DiscoveryCandidatePipeline
from openbiliclaw.discovery.douyin import DouyinDiscoveryOptions, DouyinDiscoveryResult
from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.integrations.openclaw.bootstrap import (
    OpenClawAdapterServices,
    build_openclaw_adapter,
    build_openclaw_adapter_services,
)
from openbiliclaw.integrations.openclaw.errors import AdapterValidationError
from openbiliclaw.integrations.openclaw.operations import OpenClawAdapter
from openbiliclaw.integrations.openclaw.schemas import (
    AvoidanceProbeFeedbackRequest,
    AvoidanceProbeFeedbackResponse,
    AvoidanceProbeResponse,
    ChatRequest,
    ChatResponse,
    DelightFeedbackRequest,
    DelightItem,
    DelightResponse,
    FeedbackRequest,
    InterestProbeFeedbackRequest,
    InterestProbeResponse,
    PlatformAvailabilityResponse,
    ProfileResponse,
    RecommendationItem,
    RecommendationResponse,
    RuntimeStatusResponse,
    SavedItemRequest,
    SavedSyncRequest,
    SyncAccountResponse,
)
from openbiliclaw.recommendation.engine import Recommendation
from openbiliclaw.runtime.douyin_producer import DouyinDiscoveryProducer
from openbiliclaw.soul.profile import InterestTag, PreferenceLayer, SoulProfile
from openbiliclaw.storage.database import Database


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

    item = asdict(payload)["items"][0]
    assert item["recommendation_id"] == 7
    assert item["bvid"] == "BV1TEST"
    assert item["reason"] == "这条会对上你最近那股想把结构看明白的劲头。"
    assert item["source_platform"] == ""
    assert item["content_type"] == "video"


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
        "llm_refill_active": 0,
        "llm_refill_waiting": 0,
        "llm_maintenance_active": 0,
        "llm_maintenance_waiting": 0,
        "llm_refill_priority_active": False,
        "inventory_priority_state": "healthy",
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

    item = asdict(payload)["item"]
    assert item["bvid"] == "BV1DELIGHT"
    assert item["title"] == "跨域发现"
    assert item["delight_score"] == 0.92
    assert item["source_platform"] == ""


def test_delight_response_serializes_without_item() -> None:
    payload = DelightResponse(item=None)

    assert asdict(payload) == {"item": None}


def test_feedback_request_rejects_unsupported_feedback_type() -> None:
    with pytest.raises(AdapterValidationError):
        FeedbackRequest(
            recommendation_id=7,
            feedback_type="bookmark",
            request_id="openclaw-invalid-type",
        )


def test_feedback_request_rejects_comment_without_note() -> None:
    with pytest.raises(AdapterValidationError):
        FeedbackRequest(
            recommendation_id=7,
            feedback_type="comment",
            note="",
            request_id="openclaw-comment-without-note",
        )


@pytest.mark.parametrize("request_id", ("", " \t "))
def test_feedback_request_rejects_blank_request_id(request_id: str) -> None:
    with pytest.raises(AdapterValidationError, match="request_id is required"):
        FeedbackRequest(
            recommendation_id=7,
            feedback_type="like",
            request_id=request_id,
        )


def test_feedback_request_rejects_request_id_over_400_characters() -> None:
    with pytest.raises(AdapterValidationError, match="request_id is too long"):
        FeedbackRequest(
            recommendation_id=7,
            feedback_type="like",
            request_id="x" * 401,
        )


def test_feedback_request_normalizes_valid_payload() -> None:
    payload = FeedbackRequest(
        recommendation_id=7,
        feedback_type=" Like ",
        note=" 很对胃口 ",
        request_id=" openclaw-normalized ",
    )

    assert payload.feedback_type == "like"
    assert payload.note == "很对胃口"
    assert payload.request_id == "openclaw-normalized"


def test_feedback_request_accepts_dismiss_without_note() -> None:
    payload = FeedbackRequest(
        recommendation_id=7,
        feedback_type="dismiss",
        note="",
        request_id="openclaw-dismiss",
    )

    assert payload.feedback_type == "dismiss"
    assert payload.note == ""


def test_saved_sync_request_does_not_treat_string_false_as_authorization() -> None:
    with pytest.raises(AdapterValidationError, match="allow_state_changing"):
        SavedSyncRequest(list_kind="favorite", allow_state_changing="false")  # type: ignore[arg-type]


def test_saved_item_request_accepts_url_identity_without_content_id() -> None:
    payload = SavedItemRequest(
        list_kind="favorite",
        source_platform="youtube",
        content_url="https://www.youtube.com/watch?v=abc",
    )

    assert payload.content_id == ""
    assert payload.content_url.endswith("watch?v=abc")


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
        probe_mode: str = "near",
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
        self.probe_mode = probe_mode
        self.specifics = specifics or []


class _FakeSpeculativeSpecific:
    def __init__(self, name: str = "") -> None:
        self.name = name


class _FakeSpeculator:
    def __init__(self, specs: list[_FakeSpeculativeInterest] | None = None) -> None:
        self._specs = specs if specs is not None else [_FakeSpeculativeInterest()]
        self.confirmed: list[tuple[str, str]] = []
        self.rejected: list[str] = []
        self.deferred: list[str] = []

    def get_active_speculations(self) -> list[_FakeSpeculativeInterest]:
        return list(self._specs)

    def user_confirm_speculation(
        self,
        domain: str,
        *,
        confirmation_source: str = "probe_confirmed",
    ) -> bool:
        self.confirmed.append((domain, confirmation_source))
        return any(item.domain == domain for item in self._specs)

    def user_reject_speculation(self, domain: str) -> bool:
        self.rejected.append(domain)
        return any(item.domain == domain for item in self._specs)

    def user_defer_speculation(self, domain: str) -> SimpleNamespace:
        self.deferred.append(domain)
        return SimpleNamespace(outcome="deferred", deferred_until="2030-01-02", defer_count=1)


class _FakeSpeculativeAvoidance:
    """Minimal stand-in for ``avoidance_speculator.SpeculativeAvoidance``."""

    def __init__(
        self,
        domain: str = "浅层热点复读",
        reason: str = "用户可能想避开无信息增量的热点复读内容。",
        confidence: float = 0.55,
        weight: float = 0.55,
        confirmation_count: int = 0,
        source_mode: str = "negative_signal",
        source_signal: str = "thumbs_down",
        experience_mode: str = "knowledge",
        entry_load: str = "heavy",
        specifics: list[object] | None = None,
    ) -> None:
        self.domain = domain
        self.reason = reason
        self.confidence = confidence
        self.weight = weight
        self.confirmation_count = confirmation_count
        self.source_mode = source_mode
        self.source_signal = source_signal
        self.experience_mode = experience_mode
        self.entry_load = entry_load
        self.specifics = specifics or []


class _FakeAvoidanceSpeculator:
    def __init__(self, avoidances: list[_FakeSpeculativeAvoidance] | None = None) -> None:
        self._avoidances = avoidances if avoidances is not None else [_FakeSpeculativeAvoidance()]
        self.confirmed: list[str] = []
        self.rejected: list[tuple[str, int]] = []
        self.observed: list[list[dict[str, object]]] = []
        self.deferred: list[str] = []

    def get_active_avoidances(self) -> list[_FakeSpeculativeAvoidance]:
        return list(self._avoidances)

    def user_confirm_avoidance(self, domain: str) -> object | None:
        self.confirmed.append(domain)
        for item in self._avoidances:
            if item.domain == domain:
                return item
        return None

    def user_reject_avoidance(self, domain: str, cooldown_days: int = 30) -> bool:
        self.rejected.append((domain, cooldown_days))
        return any(item.domain == domain for item in self._avoidances)

    def observe(self, events: list[dict[str, object]]) -> int:
        self.observed.append(events)
        return len(events)

    def user_defer_avoidance(self, domain: str) -> SimpleNamespace:
        self.deferred.append(domain)
        return SimpleNamespace(outcome="deferred", deferred_until="2030-01-02", defer_count=1)


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
        self.feedback_owner_prepares = 0
        self.immediate_calls: list[tuple[str, str, str]] = []
        self._speculator = _FakeSpeculator()
        self._avoidance_speculator = _FakeAvoidanceSpeculator()
        self._llm = None  # Socratic dialogue falls through to llm_service
        self.dialogue_learn_calls: list[dict[str, object]] = []
        self.dialogue_learned = asyncio.Event()

    async def get_profile(self) -> SoulProfile:
        return self.profile

    async def learn_from_dialogue(
        self,
        *,
        user_message: str,
        assistant_reply: str,
        session: str,
        scope: str = "chat",
        turn_id: str = "",
    ) -> None:
        self.dialogue_learn_calls.append(
            {
                "user_message": user_message,
                "assistant_reply": assistant_reply,
                "session": session,
                "scope": scope,
                "turn_id": turn_id,
            }
        )
        self.dialogue_learned.set()

    def record_immediate_feedback_cognition(
        self,
        *,
        feedback_type: str,
        title: str,
        note: str,
    ) -> None:
        self.immediate_calls.append((feedback_type, title, note))

    async def prepare_feedback_owner_cutover(self) -> dict[str, object]:
        self.feedback_owner_prepares += 1
        return {"prepared": True, "feedback_owner_version": 2}

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
        self.event_rows: dict[int, dict[str, object]] = {}

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

    def query_event_rows_by_ids(self, event_ids: list[int]) -> list[dict[str, object]]:
        return [self.event_rows[event_id] for event_id in event_ids if event_id in self.event_rows]


class _FakeEventIngress:
    def __init__(
        self,
        memory: _FakeMemoryManager,
        database: _FakeDatabase,
        soul_engine: _FakeSoulEngine,
    ) -> None:
        self.memory = memory
        self.database = database
        self.soul_engine = soul_engine
        self.key_to_event_id: dict[tuple[str, str], int] = {}

    async def accept(self, event: dict[str, object], *, producer: str) -> SimpleNamespace:
        await self.soul_engine.prepare_feedback_owner_cutover()
        raw_key = str(event.get("ingest_key") or "")
        namespaced_key = (producer, raw_key)
        duplicate = bool(raw_key and namespaced_key in self.key_to_event_id)
        if duplicate:
            event_id = self.key_to_event_id[namespaced_key]
        else:
            event_id = len(self.database.event_rows) + 1
            if raw_key:
                self.key_to_event_id[namespaced_key] = event_id
            stored = dict(event)
            self.database.event_rows[event_id] = stored
            self.memory.events.append(stored)
        return SimpleNamespace(
            accepted=1,
            rejected=0,
            items=[
                SimpleNamespace(
                    event_id=event_id,
                    inserted=not duplicate,
                    duplicate=duplicate,
                )
            ],
        )


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
    event_ingress = _FakeEventIngress(memory_manager, database, soul_engine)
    services = SimpleNamespace(
        soul_engine=soul_engine,
        memory_manager=memory_manager,
        database=database,
        runtime_controller=runtime_controller,
        account_sync_service=account_sync_service,
        recommendation_engine=recommendation_engine,
        llm_service=llm_service,
        event_ingress=event_ingress,
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
async def test_get_profile_trims_traits_and_interests_to_five() -> None:
    """The external ProfileResponse caps each list at 5 items (operations:114-120).

    Guards the ``[:5]`` slices against silently returning the full profile lists;
    previously never exercised with more than 5 entries.
    """

    class _EightItemSoulEngine:
        async def get_profile(self) -> SoulProfile:
            return SoulProfile(
                personality_portrait="八项画像。",
                core_traits=[f"特质-{i}" for i in range(8)],
                deep_needs=[f"需求-{i}" for i in range(8)],
                preferences=PreferenceLayer(
                    interests=[
                        InterestTag(name=f"兴趣-{i}", category="知识", weight=1.0 - i * 0.1)
                        for i in range(8)
                    ]
                ),
            )

    services = SimpleNamespace(soul_engine=_EightItemSoulEngine())
    result = await OpenClawAdapter(services=services).get_profile()

    assert len(result.core_traits) == 5
    assert len(result.deep_needs) == 5
    assert len(result.top_interests) == 5
    assert result.core_traits == [f"特质-{i}" for i in range(5)]
    assert result.top_interests == [f"兴趣-{i}" for i in range(5)]


@pytest.mark.asyncio
async def test_openclaw_recommend_updates_gate_after_durable_pool_commit(tmp_path: Path) -> None:
    from openbiliclaw.llm.concurrency import InventoryPriorityState, LLMConcurrencyGate
    from openbiliclaw.recommendation.engine import RecommendationEngine
    from openbiliclaw.storage.database import Database

    database = Database(tmp_path / "openclaw-recommend.db")
    database.initialize()
    database.cache_content(
        "BV1OPENCLAW",
        title="OpenClaw durable recommendation",
        up_name="UP",
        source="search",
        relevance_score=0.95,
        pool_expression="durable expression",
        pool_topic_label="durable topic",
        style_key="tutorial",
        topic_group="durable",
    )
    gate = LLMConcurrencyGate(4)
    gate.update_inventory(available=1, target=1)

    class PrecomputedRecommendationEngine(RecommendationEngine):
        async def generate_recommendations(
            self,
            discovered: list[DiscoveredContent] | None,
            profile: SoulProfile,
            limit: int = 10,
        ) -> list[Recommendation]:
            return await self.serve(profile, limit=limit, expression_mode="precomputed")

    class Controller:
        pool_target_count = 1

        def _pool_readiness_counts(self) -> dict[str, int]:
            available = database.count_pool_candidates()
            gate.update_inventory(available=available, target=self.pool_target_count)
            return {"available": available}

    controller = Controller()
    engine = PrecomputedRecommendationEngine(llm=object(), database=database)  # type: ignore[arg-type]
    engine.set_pool_inventory_commit_callback(controller._pool_readiness_counts)
    services = SimpleNamespace(
        soul_engine=_FakeSoulEngine(),
        memory_manager=_FakeMemoryManager(),
        database=database,
        runtime_controller=controller,
        account_sync_service=_FakeAccountSyncService(),
        recommendation_engine=engine,
        llm_service=_FakeLLMService(),
    )

    result = await OpenClawAdapter(services=services).recommend(limit=1)
    assert len(result.items) == 1
    async with asyncio.timeout(1):
        while gate.inventory_priority_state is not InventoryPriorityState.EMPTY:
            await asyncio.sleep(0)
    assert database.count_pool_candidates() == 0
    database.close()


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

    assert result.items[0].recommendation_id == 31
    assert result.items[0].content_id == "BV1DB"
    assert result.items[0].source_platform == "bilibili"
    assert result.items[0].author_name == "复杂性观察者"
    assert result.items[0].expression == "这条更像是刚补完货后直接该拿给你看的那种。"
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
async def test_recommend_cached_fallback_honors_latest_effective_dislikes() -> None:
    adapter, soul_engine, _, database, _, _, recommendation_engine = _build_adapter()
    soul_engine.get_effective_disliked_topics = lambda: [  # type: ignore[attr-defined]
        "你最近那股想把系统想透的劲头"
    ]

    result = await adapter.recommend(limit=3, refresh_if_needed=True)

    assert result.items[0].recommendation_id == 11
    assert database.recommendation_list_calls == [3]
    assert recommendation_engine.calls == [(None, 3)]


@pytest.mark.asyncio
async def test_submit_feedback_records_event_and_cognition_returns_queued() -> None:
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
        FeedbackRequest(
            recommendation_id=7,
            feedback_type="like",
            note="很对胃口",
            request_id="openclaw-submit-like",
        )
    )

    assert asdict(result) == {
        "ok": True,
        "recommendation_id": 7,
        "feedback_type": "like",
        "event_id": 1,
        "duplicate": False,
        "processing": "queued",
    }
    assert database.updated == [(7, "like", "很对胃口")]
    assert len(memory_manager.events) == 1
    stored = memory_manager.events[0]
    assert stored["event_type"] == "feedback"
    assert stored["title"] == "把国际局势讲出结构感"
    assert stored["metadata"] == {
        "recommendation_id": 7,
        "bvid": "BV1REC",
        "feedback_type": "like",
        "feedback_note": "很对胃口",
        "event_namespace": "recommendation",
        "profile_update_owner": "content_feedback",
        "source_platform": "bilibili",
        "signal_strength": 1.0,
    }
    assert soul_engine.immediate_calls == [("like", "把国际局势讲出结构感", "很对胃口")]
    # Preference analysis + refresh are drained asynchronously by the runtime's
    # background schedulers, so submit_feedback no longer calls them here.



@pytest.mark.asyncio
async def test_submit_feedback_keeps_commit_success_when_cognition_fails() -> None:
    adapter, soul_engine, memory, database, *_ = _build_adapter()

    def fail_cognition(**_kwargs: object) -> None:
        raise RuntimeError("cognition unavailable")

    soul_engine.record_immediate_feedback_cognition = fail_cognition  # type: ignore[method-assign]

    result = await adapter.submit_feedback(
        FeedbackRequest(
            recommendation_id=7,
            feedback_type="dislike",
            note="别再推荐",
            request_id="openclaw-submit-failure-cognition",
        )
    )

    assert result.ok is True
    assert result.processing == "queued"
    assert result.event_id == 1
    assert database.updated == [(7, "dislike", "别再推荐")]
    assert len(memory.events) == 1


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


class _StopAfterSoulEngineError(Exception):
    """Sentinel: abort the bootstrap once the SoulEngine has been constructed."""


def test_build_openclaw_adapter_services_forwards_soul_engine_config_and_database(
    monkeypatch, tmp_path: Path
) -> None:
    """The OpenClaw surface matches the API's SoulEngine construction contract.

    Regression (found in unified-interest-line Wave A): the bootstrap never
    passed ``feedback_batch_threshold`` (so the adapter's feedback batch always
    ran at the hardcoded default 3) and Wave A only plumbed
    ``unified_interest_line`` through ``api/runtime_context.py``, which would
    have left OpenClaw on the legacy line after the flag flip. It also omitted
    the shared database and satisfaction filter, splitting persistence and event
    filtering semantics from the API runtime.

    The bootstrap is aborted with a sentinel right after ``SoulEngine`` is built
    (``LLMService`` is the very next construction) so the test needs no fakes for
    the discovery/recommendation half of the service bundle.
    """
    import openbiliclaw.integrations.openclaw.bootstrap as bootstrap_module
    from openbiliclaw.config import Config

    cfg = Config()
    cfg.data_dir = str(tmp_path)
    cfg.scheduler.feedback_batch_threshold = 6
    cfg.scheduler.unified_interest_line = True
    cfg.soul.preference.satisfaction_filter_enabled = False
    cfg.soul.preference_prompt_view = "compact-v1"
    cfg.soul.awareness_prompt_view = "legacy"
    cfg.soul.insight_prompt_view = "compact-v1"

    captured: dict[str, object] = {}

    class FakeDatabase:
        def __init__(self, path: object) -> None:
            self.path = path

        def initialize(self) -> None:
            return None

    class FakeMemoryManager:
        def __init__(self, data_path: object, database: object = None) -> None:
            self.data_path = data_path
            self.database = database

        def initialize(self) -> None:
            return None

    class FakeSoulEngine:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    def _stop(**_kwargs: object) -> None:
        raise _StopAfterSoulEngineError

    monkeypatch.setattr(bootstrap_module, "load_config", lambda: cfg)
    monkeypatch.setattr(bootstrap_module, "build_llm_registry", lambda _cfg: object())
    monkeypatch.setattr(bootstrap_module, "Database", FakeDatabase)
    monkeypatch.setattr(bootstrap_module, "MemoryManager", FakeMemoryManager)
    monkeypatch.setattr(bootstrap_module, "SoulEngine", FakeSoulEngine)
    monkeypatch.setattr(bootstrap_module, "LLMService", _stop)

    with pytest.raises(_StopAfterSoulEngineError):
        build_openclaw_adapter_services()

    assert captured["feedback_batch_threshold"] == 6
    assert captured["unified_interest_line"] is True
    assert captured["satisfaction_filter_enabled"] is False
    assert captured["preference_prompt_view"] == "compact-v1"
    assert captured["awareness_prompt_view"] == "legacy"
    assert captured["insight_prompt_view"] == "compact-v1"
    memory = captured["memory"]
    assert captured["database"] is memory.database


@pytest.mark.parametrize(
    ("configured_copy_target", "expected_copy_target"),
    [(47, 30), (0, 0)],
)
def test_build_openclaw_adapter_services_reuses_shared_database(
    monkeypatch,
    configured_copy_target: int,
    expected_copy_target: int,
) -> None:
    import openbiliclaw.integrations.openclaw.bootstrap as bootstrap_module

    created_databases: list[object] = []
    created_memories: list[object] = []
    registered_strategies: list[str] = []
    created_strategy_kwargs: list[dict[str, object]] = []
    producer_kwargs: list[dict[str, object]] = []
    startup_events: list[str] = []

    class FakeDatabase:
        def __init__(self, path: str) -> None:
            self.path = path
            self.initialized = 0
            self.pool_count = 30
            created_databases.append(self)

        def initialize(self) -> None:
            self.initialized += 1

        def count_pool_candidates(self, *, xhs_self_nickname: str = "") -> int:
            return self.pool_count

        def count_discovery_candidates_by_status(self) -> dict[str, int]:
            return {"pending_eval": 500, "evaluating": 60, "evaluated": 3}

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
        def __init__(
            self,
            *,
            registry: object,
            memory: object,
            usage_recorder: object | None = None,
            module_overrides=None,
            concurrency: int = 3,
            concurrency_gate: object | None = None,
        ) -> None:
            self.registry = registry
            self.memory = memory
            self.usage_recorder = usage_recorder
            self.module_overrides = module_overrides
            self.concurrency = concurrency
            self.concurrency_gate = concurrency_gate

    class FakeRecommendationEngine:
        def __init__(
            self,
            *,
            llm: object,
            database: object,
            curator: object = None,
            embedding_service: object = None,
            **_extras: object,
        ) -> None:
            self.llm = llm
            self.database = database
            self.kwargs = _extras
            self.pool_inventory_commit_callback = None

        def set_pool_inventory_commit_callback(self, callback) -> None:
            self.pool_inventory_commit_callback = callback

    class FakeBilibiliClient:
        def __init__(self, *, cookie: str, proxy: str | None = None) -> None:
            self.cookie = cookie
            self.proxy = proxy

    class FakeDiscoveryEngine:
        def __init__(
            self,
            *,
            llm_service: object,
            database: object,
            embedding_service: object = None,
            concurrency: object = None,
            eval_prefilter_mode: str = "shadow",
            bilibili_client: object = None,
            quality_gate: object = None,
        ) -> None:
            self.llm_service = llm_service
            self.database = database
            self.concurrency = concurrency
            self.eval_prefilter_mode = eval_prefilter_mode
            self.bilibili_client = bilibili_client
            self.quality_gate = quality_gate

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

        def run_startup_maintenance(self) -> None:
            startup_events.append("maintenance")

        def _pool_readiness_counts(self) -> dict[str, int]:
            available = created_databases[0].pool_count
            self.kwargs["llm_concurrency_gate"].update_inventory(
                available=available,
                target=self.kwargs["pool_target_count"],
            )
            return {"available": available, "admitted_pending_copy": 4}

    class FakeCandidatePipeline:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    class FakeAccountSyncService:
        def __init__(self, **kwargs) -> None:
            startup_events.append("account_sync")
            self.kwargs = kwargs

    fake_config = SimpleNamespace(
        data_path=Path("/tmp/openclaw-data"),
        llm=SimpleNamespace(
            concurrency=3,
            soul=SimpleNamespace(provider="claude", model="claude-sonnet"),
            discovery=SimpleNamespace(provider="deepseek", model="deepseek-chat"),
            recommendation=SimpleNamespace(provider="", model=""),
            evaluation=SimpleNamespace(provider="", model="gpt-4o-mini"),
        ),
        bilibili=SimpleNamespace(cookie="raw-cookie", proxy=""),
        sources=SimpleNamespace(
            xiaohongshu=SimpleNamespace(enabled=False),
            douyin=SimpleNamespace(enabled=True, mode="direct"),
            youtube=SimpleNamespace(enabled=True),
        ),
        discovery=SimpleNamespace(
            admission_min_score=0.60,
            eval_prefilter_mode="enforce",
            visual_profile_enabled=False,
            keyframe_enabled=False,
            keyframe_max_frames=8,
            keyframe_fetch_limit=50,
            danmaku_enabled=False,
            danmaku_fetch_limit=50,
            danmaku_max_chars=500,
        ),
        scheduler=SimpleNamespace(
            enabled=True,
            pause_on_extension_disconnect=False,
            pool_target_count=30,
            copy_ready_target_count=configured_copy_target,
            pool_source_shares={
                "bilibili": 8,
                "xiaohongshu": 3,
                "douyin": 2,
                "youtube": 1,
            },
            account_sync_interval_hours=6,
            refresh_check_interval_seconds=77,
            signal_event_threshold=9,
            trending_refresh_minutes=5,
            explore_refresh_minutes=18,
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
        quality_gate=SimpleNamespace(
            enabled=False,
            mode="reject",
            min_follower=1000,
            max_level=2,
            min_views=500,
            ban_franchise_accounts=True,
            allowlist_mids=[],
            clickbait_patterns=[],
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
    monkeypatch.setattr(
        bootstrap_module,
        "DiscoveryCandidatePipeline",
        FakeCandidatePipeline,
        raising=False,
    )
    monkeypatch.setattr(
        bootstrap_module,
        "build_youtube_discovery_producer",
        lambda **kwargs: producer_kwargs.append(kwargs) or SimpleNamespace(kind="youtube"),
    )
    import openbiliclaw.runtime.douyin_producer as douyin_producer_module

    monkeypatch.setattr(
        douyin_producer_module,
        "build_douyin_discovery_producer",
        lambda **kwargs: producer_kwargs.append(kwargs) or SimpleNamespace(kind="douyin"),
    )

    services = build_openclaw_adapter_services()

    assert isinstance(services, OpenClawAdapterServices)
    assert startup_events == ["maintenance", "account_sync"]
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
    assert services.soul_engine.kwargs["llm_concurrency"] == 3
    assert services.llm_service.module_overrides["discovery"].provider == "deepseek"
    assert services.llm_service.module_overrides["evaluation"].model == "gpt-4o-mini"
    assert services.llm_service.concurrency == 3
    assert services.discovery_engine.concurrency.llm_evaluation_concurrency == 2
    assert services.discovery_engine.eval_prefilter_mode == "enforce"
    assert services.recommendation_engine.kwargs["copy_ready_target_count"] == (
        expected_copy_target
    )
    assert services.recommendation_engine.kwargs["pool_available_target_count"] == 30
    assert (
        services.llm_service.concurrency_gate is services.soul_engine.kwargs["llm_concurrency_gate"]
    )
    assert services.runtime_controller.kwargs["llm_concurrency_gate"] is (
        services.llm_service.concurrency_gate
    )
    assert services.llm_service.concurrency_gate.status_payload()["inventory_priority_state"] == (
        "healthy"
    )
    assert services.recommendation_engine.pool_inventory_commit_callback is not None
    created_databases[0].pool_count = 0
    services.recommendation_engine.pool_inventory_commit_callback()
    assert (
        services.llm_service.concurrency_gate.status_payload()["inventory_priority_state"]
        == "empty"
    )
    assert registered_strategies == [
        "FakeStrategy",
        "FakeStrategy",
        "FakeStrategy",
        "FakeStrategy",
    ]
    assert services.runtime_controller.kwargs["pool_source_shares"] == {
        "bilibili": 8,
        "douyin": 2,
        "youtube": 1,
    }
    assert services.runtime_controller.kwargs["scheduler_config"] is fake_config.scheduler
    assert "presence" in services.runtime_controller.kwargs
    assert "youtube_producer" in services.runtime_controller.kwargs
    pipeline = services.runtime_controller.kwargs["discovery_candidate_pipeline"]
    assert isinstance(pipeline, FakeCandidatePipeline)
    assert pipeline.kwargs["database"] is created_databases[0]
    assert pipeline.kwargs["discovery_engine"] is services.discovery_engine
    assert pipeline.kwargs["admission_min_score"] == 0.60
    # OpenClaw has no daemon to consume a large raw backlog after returning.
    # Its direct refresh starts with one fixed small evaluation claim, while
    # the API runtime retains its oversampled continuous-refill settings.
    assert pipeline.kwargs["candidate_fetch_oversample"] == 1
    assert pipeline.kwargs["eval_batch_concurrency"] == 1
    assert pipeline.kwargs["min_eval_batch_size"] == 4
    assert services.runtime_controller.kwargs["one_shot_inline_eval_limit"] == 4
    assert len(producer_kwargs) == 2
    assert all(kwargs["candidate_pipeline"] is pipeline for kwargs in producer_kwargs)
    assert services.runtime_controller.kwargs["youtube_producer"].kind == "youtube"
    # The adapter is a one-shot composition: it never starts daemon owners.
    # Producer evaluation stays bounded inline and admission hands copy to the
    # awaited one-shot owner rather than an idle coordinator.
    assert getattr(services.runtime_controller, "candidate_eval_coordinator", None) is None
    assert getattr(services.runtime_controller, "expression_copy_coordinator", None) is None
    assert (
        getattr(
            services.runtime_controller.kwargs["douyin_producer"],
            "candidate_evaluation_owned_by_coordinator",
            False,
        )
        is False
    )
    assert (
        getattr(
            services.runtime_controller.kwargs["youtube_producer"],
            "candidate_evaluation_owned_by_coordinator",
            False,
        )
        is False
    )
    assert getattr(pipeline, "on_candidates_enqueued", None) is None
    assert callable(getattr(pipeline, "on_candidates_admitted", None))
    assert services.runtime_controller.kwargs["check_interval_seconds"] == 77
    assert services.runtime_controller.kwargs["signal_event_threshold"] == 9
    assert services.runtime_controller.kwargs["trending_refresh_minutes"] == 5
    assert services.runtime_controller.kwargs["explore_refresh_minutes"] == 18
    assert services.runtime_controller.kwargs["discovery_limit"] == 17
    assert services.runtime_controller.kwargs["proactive_push_interval_seconds"] == 155
    created_databases[0].pool_count = 7
    services.runtime_controller._pool_readiness_counts()
    assert services.llm_service.concurrency_gate.status_payload()["inventory_priority_state"] == (
        "refill"
    )


@pytest.mark.asyncio
async def test_openclaw_one_shot_producer_admits_inline_with_ninety_claim_cap(
    tmp_path: Path,
) -> None:
    """The non-daemon OpenClaw composition must not strand producer output."""

    class _Soul:
        async def get_profile(self) -> object:
            return object()

    class _InlineEvaluationEngine:
        _EVALUATE_BATCH_HARD_CAP = 90

        def __init__(self, database: Database) -> None:
            self.database = database
            self.max_evaluated_batch = 0

        async def evaluate_content_batch(
            self,
            contents: list[DiscoveredContent],
            _profile: object,
            **_kwargs: object,
        ) -> list[float]:
            self.max_evaluated_batch = max(self.max_evaluated_batch, len(contents))
            for item in contents:
                item.relevance_score = 0.91
                item.relevance_reason = "one-shot fit"
                item.topic_group = "OpenClaw direct"
                item.style_key = "deep_dive"
            return [0.91] * len(contents)

        def cache_evaluated_results(self, items: list[DiscoveredContent]) -> int:
            for item in items:
                content_id = item.content_id or item.bvid
                self.database.cache_content(
                    content_id,
                    title=item.title,
                    up_name=item.author_name or item.up_name,
                    source=item.source_strategy,
                    source_platform=item.source_platform,
                    content_id=content_id,
                    content_url=item.content_url,
                    relevance_score=item.relevance_score,
                    relevance_reason=item.relevance_reason,
                    topic_group=item.topic_group,
                    style_key=item.style_key,
                )
            return len(items)

    database = Database(tmp_path / "openclaw-one-shot.db")
    database.initialize()
    evaluation_engine = _InlineEvaluationEngine(database)
    pipeline = DiscoveryCandidatePipeline(
        database=database,
        discovery_engine=evaluation_engine,  # type: ignore[arg-type]
        pool_target_count=180,
    )
    raw_items = [
        DiscoveredContent(
            bvid=f"dy-openclaw-{index}",
            content_id=f"dy-openclaw-{index}",
            content_url=f"https://www.douyin.com/video/dy-openclaw-{index}",
            title=f"OpenClaw direct {index}",
            source_platform="douyin",
            source_strategy="search",
            author_name="creator",
        )
        for index in range(120)
    ]

    async def discover(
        _profile: object,
        _options: DouyinDiscoveryOptions,
    ) -> DouyinDiscoveryResult:
        return DouyinDiscoveryResult(
            items=raw_items,
            cached=False,
            source_counts={"search": len(raw_items)},
        )

    # This is the producer state OpenClaw bootstrap intentionally leaves in
    # place: no coordinator task exists, so the bounded inline path owns work.
    producer = DouyinDiscoveryProducer(
        soul_engine=_Soul(),
        discover=discover,
        enabled=True,
        min_interval_minutes=0,
        sources=("search",),
        candidate_pipeline=pipeline,
    )

    result = await producer.produce_if_due(limit=120)

    statuses = database.count_discovery_candidates_by_status()
    assert producer.candidate_evaluation_owned_by_coordinator is False
    assert result["enqueued"] == 120
    assert result["cached"] == 90
    assert statuses["cached"] == 90
    assert statuses["pending_eval"] == 30
    assert evaluation_engine.max_evaluated_batch == 90


@pytest.mark.asyncio
async def test_openclaw_controller_refresh_owns_post_admission_copy_once(
    tmp_path: Path,
) -> None:
    """A one-shot controller must not re-run copy already owned by admission."""

    from openbiliclaw.recommendation.engine import RecommendationEngine
    from openbiliclaw.runtime.refresh import ContinuousRefreshController

    profile = SoulProfile(
        core_traits=["好奇"],
        preferences=PreferenceLayer(
            interests=[InterestTag(name="并发控制", category="技术", weight=0.95)]
        ),
    )

    class Memory:
        def __init__(self) -> None:
            self.runtime_state: dict[str, object] = {}

        def get_layer(self, _name: str) -> SimpleNamespace:
            return SimpleNamespace(data={"ready": True})

        def load_discovery_runtime_state(self) -> dict[str, object]:
            return dict(self.runtime_state)

        def save_discovery_runtime_state(self, state: dict[str, object]) -> None:
            self.runtime_state = dict(state)

        def update_discovery_runtime_state(self, mutator) -> dict[str, object]:
            state = dict(self.runtime_state)
            result = mutator(state)
            self.runtime_state = dict(state if result is None else result)
            return dict(self.runtime_state)

    class Soul:
        async def get_profile(self) -> SoulProfile:
            return profile

        def get_effective_disliked_topics(self) -> list[str]:
            return []

    class EvaluationEngine:
        _EVALUATE_BATCH_HARD_CAP = 90

        def __init__(self, database: Database) -> None:
            self.database = database

        async def produce_candidates(
            self,
            _profile: SoulProfile,
            *,
            strategies: list[str],
            limit: int,
            **_kwargs: object,
        ) -> list[DiscoveredContent]:
            return [
                DiscoveredContent(
                    bvid=f"BV-openclaw-controller-{index}",
                    content_id=f"openclaw-controller-{index}",
                    content_url=(f"https://www.bilibili.com/video/BV-openclaw-controller-{index}"),
                    title=f"一次性补货链路 {index}",
                    source_platform="bilibili",
                    source_strategy=strategies[0] if strategies else "search",
                    author_name="系统实验室",
                )
                for index in range(limit)
            ]

        async def evaluate_content_batch(
            self,
            contents: list[DiscoveredContent],
            _profile: SoulProfile,
            **_kwargs: object,
        ) -> list[float]:
            for item in contents:
                item.relevance_score = 0.91
                item.relevance_reason = "OpenClaw controller fit"
                item.topic_group = f"并发补货-{item.content_id}"
                item.style_key = "deep_dive"
            return [0.91] * len(contents)

        def cache_evaluated_results(self, items: list[DiscoveredContent]) -> int:
            for item in items:
                self.database.cache_content(
                    item.bvid,
                    title=item.title,
                    up_name=item.author_name or item.up_name,
                    source=item.source_strategy,
                    source_platform=item.source_platform,
                    content_id=item.content_id or item.bvid,
                    content_url=item.content_url,
                    relevance_score=item.relevance_score,
                    relevance_reason=item.relevance_reason,
                    topic_group=item.topic_group,
                    style_key=item.style_key,
                )
            return len(items)

    class ExpressionLLM:
        def __init__(self) -> None:
            self.calls = 0

        async def complete_structured_task(
            self, *, caller: str, **_kwargs: object
        ) -> SimpleNamespace:
            assert caller == "recommendation.write_expression"
            self.calls += 1
            return SimpleNamespace(
                content=json.dumps(
                    [
                        {
                            "content_id": f"openclaw-controller-{index}",
                            "expression": f"第 {index} 条把补货并发讲得很清楚。",
                            "topic_label": "把补货跑稳",
                        }
                        for index in range(5)
                    ],
                    ensure_ascii=False,
                )
            )

    database = Database(tmp_path / "openclaw-controller-copy-once.db")
    database.initialize()
    evaluation_engine = EvaluationEngine(database)
    candidate_pipeline = DiscoveryCandidatePipeline(
        database=database,
        discovery_engine=evaluation_engine,  # type: ignore[arg-type]
        pool_target_count=5,
        min_eval_batch_size=1,
    )
    expression_llm = ExpressionLLM()
    recommendation_engine = RecommendationEngine(llm=expression_llm, database=database)
    copy_callback_calls = 0

    async def drain_one_shot_copy(callback_profile: SoulProfile) -> int:
        nonlocal copy_callback_calls
        copy_callback_calls += 1
        return await recommendation_engine.drain_pending_expression_copy(
            profile=callback_profile,
            limit=60,
        )

    candidate_pipeline.on_candidates_admitted = lambda callback_profile, _admitted: (
        drain_one_shot_copy(callback_profile)
    )
    controller = ContinuousRefreshController(
        memory_manager=Memory(),
        database=database,
        soul_engine=Soul(),
        discovery_engine=evaluation_engine,  # type: ignore[arg-type]
        recommendation_engine=recommendation_engine,
        discovery_candidate_pipeline=candidate_pipeline,
        one_shot_expression_copy_callback=drain_one_shot_copy,
        pool_target_count=5,
        pool_source_shares={"bilibili": 1},
        discovery_limit=5,
    )

    result = await controller.force_refresh()

    assert result["refreshed"] is True
    assert copy_callback_calls == 1
    assert expression_llm.calls == 1
    assert database.count_pool_candidates() == 5
    assert database.get_pool_candidates_needing_copy(limit=10) == []
    database.close()


@pytest.mark.asyncio
async def test_openclaw_bootstrap_one_shot_keeps_partial_copy_durable_without_split_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-daemon OpenClaw refill must finish the durable copy stage inline."""

    import openbiliclaw.integrations.openclaw.bootstrap as bootstrap_module
    import openbiliclaw.llm.registry as registry_module
    import openbiliclaw.runtime.douyin_producer as douyin_producer_module

    profile = SoulProfile(
        core_traits=["好奇"],
        preferences=PreferenceLayer(
            interests=[InterestTag(name="系统设计", category="技术", weight=0.95)]
        ),
    )

    class BootstrapMemory:
        def __init__(self, _data_path: Path, database: Database | None = None) -> None:
            self.database = database
            self.runtime_state: dict[str, object] = {}

        def initialize(self) -> None:
            pass

        def load_discovery_runtime_state(self) -> dict[str, object]:
            return dict(self.runtime_state)

        def save_discovery_runtime_state(self, state: dict[str, object]) -> None:
            self.runtime_state = dict(state)

        def update_discovery_runtime_state(self, mutator) -> dict[str, object]:
            state = dict(self.runtime_state)
            result = mutator(state)
            self.runtime_state = dict(state if result is None else result)
            return dict(self.runtime_state)

        def get_layer(self, name: str) -> SimpleNamespace:
            assert name == "soul"
            return SimpleNamespace(data={"ready": True})

    class BootstrapSoul:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def get_profile(self) -> SoulProfile:
            return profile

    class BootstrapLLM:
        expression_calls = 0
        expression_batch_sizes: list[int] = []

        def __init__(self, **_kwargs: object) -> None:
            pass

        async def complete_structured_task(
            self,
            *,
            caller: str,
            user_input: str,
            **_kwargs: object,
        ) -> SimpleNamespace:
            assert caller == "recommendation.write_expression"
            payload = user_input.split("<content_batch>", 1)[1].split("</content_batch>", 1)[0]
            batch = json.loads(payload)
            type(self).expression_calls += 1
            type(self).expression_batch_sizes.append(len(batch))
            return SimpleNamespace(
                content=json.dumps(
                    [
                        {
                            "content_id": str(item["content_id"]),
                            "expression": (
                                f"{item['content_id']} 把并发补货讲得很具体，"
                                "正好接住你想把系统跑稳的劲头。"
                            ),
                            "topic_label": "把补货链路跑稳",
                        }
                        # Deliberately return a valid subset.  The one-shot
                        # bridge must make this completed subset servable,
                        # rather than spend its interaction budget recursively
                        # split-retrying the remaining durable rows.
                        for item in batch[:2]
                    ],
                    ensure_ascii=False,
                )
            )

    class BootstrapDiscovery:
        _EVALUATE_BATCH_HARD_CAP = 90

        def __init__(self, *, database: Database, **_kwargs: object) -> None:
            self.database = database

        def register_strategy(self, _strategy: object) -> None:
            pass

        async def evaluate_content_batch(
            self,
            contents: list[DiscoveredContent],
            _profile: SoulProfile,
            **_kwargs: object,
        ) -> list[float]:
            for item in contents:
                item.relevance_score = 0.91
                item.relevance_reason = "OpenClaw one-shot fit"
                item.topic_group = f"系统设计-{item.content_id}"
                item.style_key = "deep_dive"
            return [0.91] * len(contents)

        def cache_evaluated_results(self, items: list[DiscoveredContent]) -> int:
            for item in items:
                content_id = item.content_id or item.bvid
                self.database.cache_content(
                    item.bvid,
                    title=item.title,
                    up_name=item.author_name or item.up_name,
                    source=item.source_strategy,
                    source_platform=item.source_platform,
                    content_id=content_id,
                    content_url=item.content_url,
                    relevance_score=item.relevance_score,
                    relevance_reason=item.relevance_reason,
                    topic_group=item.topic_group,
                    style_key=item.style_key,
                )
            return len(items)

    class BootstrapStrategy:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

    class BootstrapBilibiliClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

    class BootstrapAccountSync:
        def __init__(self, **_kwargs: object) -> None:
            pass

    async def discover(
        _profile: SoulProfile,
        _options: DouyinDiscoveryOptions,
    ) -> DouyinDiscoveryResult:
        return DouyinDiscoveryResult(
            items=[
                DiscoveredContent(
                    bvid=f"dy-openclaw-copy-{index}",
                    content_id=f"dy-openclaw-copy-{index}",
                    content_url=f"https://www.douyin.com/video/dy-openclaw-copy-{index}",
                    title=f"连续补货的并发控制 {index}",
                    source_platform="douyin",
                    source_strategy="search",
                    author_name="系统实验室",
                )
                for index in range(4)
            ],
            cached=False,
            source_counts={"search": 8},
        )

    def build_douyin(**kwargs: object) -> DouyinDiscoveryProducer:
        return DouyinDiscoveryProducer(
            soul_engine=kwargs["soul_engine"],
            discover=discover,
            enabled=True,
            min_interval_minutes=0,
            sources=("search",),
            candidate_pipeline=kwargs["candidate_pipeline"],
        )

    scheduler = SimpleNamespace(
        enabled=True,
        pause_on_extension_disconnect=False,
        pool_target_count=4,
        pool_source_shares={"douyin": 4},
        account_sync_interval_hours=6,
        refresh_check_interval_seconds=60,
        signal_event_threshold=6,
        trending_refresh_minutes=3,
        explore_refresh_minutes=12,
        discovery_limit=30,
        proactive_push_interval_seconds=120,
        speculation_interval_minutes=15,
        speculation_ttl_days=7,
        speculation_cooldown_days=7,
        speculation_confirmation_threshold=3,
        speculation_max_active=5,
        speculation_max_primary_interests=12,
        speculation_max_secondary_interests=48,
        speculator_idle_interval_minutes=10,
    )
    config = SimpleNamespace(
        data_path=tmp_path,
        llm=SimpleNamespace(concurrency=3),
        bilibili=SimpleNamespace(cookie="", proxy=""),
        discovery=SimpleNamespace(
            admission_min_score=0.60,
            visual_profile_enabled=False,
            keyframe_enabled=False,
            keyframe_max_frames=8,
            keyframe_fetch_limit=50,
            danmaku_enabled=False,
            danmaku_fetch_limit=50,
            danmaku_max_chars=500,
        ),
        scheduler=scheduler,
    )

    monkeypatch.setattr(bootstrap_module, "load_config", lambda: config)
    monkeypatch.setattr(bootstrap_module, "build_llm_registry", lambda _config: object())
    monkeypatch.setattr(bootstrap_module, "module_overrides_from_config", lambda _config: {})
    monkeypatch.setattr(bootstrap_module, "_llm_concurrency_from_config", lambda _config: 3)
    monkeypatch.setattr(bootstrap_module, "resolve_runtime_cookie", lambda **_kwargs: "")
    monkeypatch.setattr(bootstrap_module, "MemoryManager", BootstrapMemory)
    monkeypatch.setattr(bootstrap_module, "SoulEngine", BootstrapSoul)
    monkeypatch.setattr(bootstrap_module, "LLMService", BootstrapLLM)
    monkeypatch.setattr(bootstrap_module, "BilibiliAPIClient", BootstrapBilibiliClient)
    monkeypatch.setattr(bootstrap_module, "ContentDiscoveryEngine", BootstrapDiscovery)
    monkeypatch.setattr(bootstrap_module, "SearchStrategy", BootstrapStrategy)
    monkeypatch.setattr(bootstrap_module, "TrendingStrategy", BootstrapStrategy)
    monkeypatch.setattr(bootstrap_module, "RelatedChainStrategy", BootstrapStrategy)
    monkeypatch.setattr(bootstrap_module, "ExploreStrategy", BootstrapStrategy)
    monkeypatch.setattr(bootstrap_module, "AccountSyncService", BootstrapAccountSync)
    monkeypatch.setattr(
        bootstrap_module, "effective_pool_source_shares", lambda _config: {"douyin": 4}
    )
    monkeypatch.setattr(
        bootstrap_module, "build_youtube_discovery_producer", lambda **_kwargs: None
    )
    embedding_builder_calls = 0

    def disabled_embedding_builder(*_args: object) -> None:
        nonlocal embedding_builder_calls
        embedding_builder_calls += 1
        return None

    # ``build_openclaw_adapter_services`` imports this symbol inside its
    # factory, so patching the registry module verifies the same local-import
    # seam that the real opt-in harness uses to prohibit embedding providers.
    monkeypatch.setattr(registry_module, "build_embedding_service", disabled_embedding_builder)
    monkeypatch.setattr(douyin_producer_module, "build_douyin_discovery_producer", build_douyin)

    services = bootstrap_module.build_openclaw_adapter_services()
    assert embedding_builder_calls == 1
    producer = services.runtime_controller.douyin_producer

    copy_drain_calls: list[tuple[int, int]] = []
    original_drain = services.recommendation_engine.drain_pending_expression_copy

    async def monitored_drain(*args: object, **kwargs: object) -> int:
        copy_drain_calls.append(
            (
                int(kwargs.get("limit", 60) or 0),
                int(kwargs.get("max_extra_requests", 6) or 0),
            )
        )
        return await original_drain(*args, **kwargs)

    services.recommendation_engine.drain_pending_expression_copy = monitored_drain

    # The post-admission hook must delegate to the controller callback, rather
    # than capture a parallel copy closure.  This gives the structured receipt
    # a single observable owner and lets the live OpenClaw E2E monitor the
    # exact callback that would otherwise be used as the controller fallback.
    callback_calls = 0
    original_copy_callback = services.runtime_controller.one_shot_expression_copy_callback
    assert callable(original_copy_callback)

    async def counting_copy_callback(callback_profile: SoulProfile) -> int:
        nonlocal callback_calls
        callback_calls += 1
        return int(await original_copy_callback(callback_profile))

    services.runtime_controller.one_shot_expression_copy_callback = counting_copy_callback

    assert services.runtime_controller.expression_copy_coordinator is None
    assert producer is not None
    produced = await producer.produce_if_due(limit=4)

    assert produced["enqueued"] == 4
    assert produced["cached"] == 4
    assert services.runtime_controller._pool_readiness_counts()["available"] == 2  # noqa: SLF001
    assert len(services.database.get_pool_candidates_needing_copy(limit=10)) == 2
    assert callback_calls == 1
    assert BootstrapLLM.expression_calls == 1
    assert BootstrapLLM.expression_batch_sizes == [4]
    assert copy_drain_calls == [(4, 0)]

    adapter = OpenClawAdapter(services=services)
    response = await adapter.recommend(limit=1, refresh_if_needed=True)

    assert len(response.items) == 1
    assert response.items[0].bvid.startswith("dy-openclaw-copy-")
    assert response.items[0].reason
    assert BootstrapLLM.expression_calls == 1
    await asyncio.sleep(0)
    assert not [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task()
        and task.get_name() in {"expression_copy", "classify_pool_backlog_detached"}
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
        event_ingress=object(),
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
async def test_openclaw_chat_legacy_direct_mode_still_learns_without_queue() -> None:
    adapter, soul_engine, *_ = _build_adapter()

    result = await adapter.chat(ChatRequest(message="我最近对建筑很感兴趣", session="test"))
    await asyncio.wait_for(soul_engine.dialogue_learned.wait(), timeout=1)

    assert isinstance(result, ChatResponse)
    assert "底层结构" in result.reply
    assert result.session == "test"
    assert len(soul_engine.dialogue_learn_calls) == 1
    assert soul_engine.dialogue_learn_calls[0]["session"] == "test"


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


@pytest.mark.asyncio
async def test_get_next_probe_records_distance_bands_history() -> None:
    adapter, soul_engine, memory_manager, *_ = _build_adapter()
    soul_engine._speculator = _FakeSpeculator(
        specs=[
            _FakeSpeculativeInterest(
                domain="桥接方向",
                confirmation_count=0,
                weight=0.5,
                probe_mode="bridge",
            ),
        ]
    )

    result = await adapter.get_next_probe()

    assert result.probe is not None
    assert "bridge" in memory_manager.runtime_state["probed_distance_bands"]


@pytest.mark.asyncio
async def test_get_next_avoidance_probe_returns_top_candidate() -> None:
    adapter, *_ = _build_adapter()

    result = await adapter.get_next_avoidance_probe()

    assert isinstance(result, AvoidanceProbeResponse)
    assert result.probe is not None
    assert result.probe.domain == "浅层热点复读"
    assert result.probe.source_mode == "negative_signal"
    assert "避开" in result.probe.question or "不喜欢" in result.probe.question


@pytest.mark.asyncio
async def test_get_next_avoidance_probe_records_history_and_avoids_repeat() -> None:
    adapter, soul_engine, memory_manager, *_ = _build_adapter()
    soul_engine._avoidance_speculator = _FakeAvoidanceSpeculator(
        avoidances=[
            _FakeSpeculativeAvoidance(
                domain="浅层热点复读",
                confirmation_count=0,
                weight=0.9,
                experience_mode="knowledge",
                entry_load="heavy",
            ),
            _FakeSpeculativeAvoidance(
                domain="情绪化争吵切片",
                confirmation_count=0,
                weight=0.5,
                experience_mode="wander_observe",
                entry_load="light",
            ),
        ]
    )

    first = await adapter.get_next_avoidance_probe()
    second = await adapter.get_next_avoidance_probe()

    assert first.probe is not None
    assert second.probe is not None
    assert first.probe.domain == "浅层热点复读"
    assert second.probe.domain == "情绪化争吵切片"
    assert "浅层热点复读" in memory_manager.runtime_state["probed_avoidance_domains"]
    assert "knowledge|heavy" in memory_manager.runtime_state["probed_avoidance_axes"]


@pytest.mark.asyncio
async def test_respond_avoidance_probe_confirm_delegates_to_speculator() -> None:
    adapter, soul_engine, *_ = _build_adapter()

    result = await adapter.respond_avoidance_probe(
        AvoidanceProbeFeedbackRequest(domain="浅层热点复读", response="confirm")
    )

    assert result == AvoidanceProbeFeedbackResponse(
        ok=True,
        action="confirmed",
        domain="浅层热点复读",
    )
    assert soul_engine._avoidance_speculator.confirmed == ["浅层热点复读"]


@pytest.mark.asyncio
async def test_recommendation_projection_exposes_multi_source_fields() -> None:
    adapter, _, _, database, *_ = _build_adapter()

    database.get_recommendations = lambda limit=100, **_: [  # type: ignore[method-assign]
        {
            "id": 88,
            "bvid": "",
            "item_key": "zhihu:answer-88",
            "content_id": "answer-88",
            "content_url": "https://zhihu.com/question/1/answer/88",
            "source_platform": "zhihu",
            "content_type": "answer",
            "title": "把复杂问题讲清楚",
            "author_name": "结构研究者",
            "body_text": "正文摘要",
            "published_label": "昨天",
            "expression": "这条接住了你最近的追问。",
            "topic": "复杂系统",
            "confidence": 0.81,
            "view_count": 100,
            "like_count": 12,
            "comment_count": 3,
        }
    ]

    result = await adapter.recommend(limit=1, refresh_if_needed=True, source_platform="zhihu")

    item = result.items[0]
    assert item.item_key == "zhihu:answer-88"
    assert item.content_id == "answer-88"
    assert item.source_platform == "zhihu"
    assert item.author_name == "结构研究者"
    assert item.body_text == "正文摘要"
    assert item.comment_count == 3


@pytest.mark.asyncio
async def test_interest_and_avoidance_probes_support_defer_and_confirm() -> None:
    adapter, soul_engine, *_ = _build_adapter()

    confirmed = await adapter.respond_interest_probe(
        InterestProbeFeedbackRequest(
            domain="建筑美学",
            response="confirm",
            surface="profile",
        )
    )
    deferred = await adapter.respond_interest_probe(
        InterestProbeFeedbackRequest(domain="建筑美学", response="defer")
    )
    avoidance_deferred = await adapter.respond_avoidance_probe(
        AvoidanceProbeFeedbackRequest(domain="浅层热点复读", response="defer")
    )

    assert confirmed.ok is True
    assert confirmed.action == "confirmed"
    assert soul_engine._speculator.confirmed == [("建筑美学", "profile_confirmed")]
    assert deferred.action == "deferred"
    assert deferred.deferred_until == "2030-01-02"
    assert avoidance_deferred.action == "deferred"
    assert avoidance_deferred.defer_count == 1


@pytest.mark.asyncio
async def test_delight_feedback_is_durable_and_idempotent() -> None:
    adapter, *_ = _build_adapter()
    request = DelightFeedbackRequest(
        bvid="BV1DLRT",
        title="跨域惊喜视频",
        response="like",
        request_id="delight-like-1",
    )

    first = await adapter.respond_delight(request)
    second = await adapter.respond_delight(request)

    assert first.ok is True
    assert first.action == "liked"
    assert first.duplicate is False
    assert second.duplicate is True
    assert first.event_id == second.event_id


class _DurableChatDatabase(_FakeDatabase):
    def __init__(self) -> None:
        super().__init__()
        self.chat_turns: dict[str, dict[str, object]] = {}

    def get_chat_turn(self, turn_id: str) -> dict[str, object] | None:
        return self.chat_turns.get(turn_id)

    def create_chat_turn(self, **kwargs: object) -> dict[str, object]:
        row = {
            "turn_id": str(kwargs["turn_id"]),
            "session": str(kwargs.get("session", "openclaw")),
            "scope": str(kwargs.get("scope", "chat")),
            "subject_id": str(kwargs.get("subject_id", "")),
            "subject_title": str(kwargs.get("subject_title", "")),
            "reply_to_turn_id": str(kwargs.get("reply_to_turn_id", "")),
            "message": str(kwargs["message"]),
            "reply": "",
            "status": "pending",
            "error": "",
            "payload": {},
            "created_at": "2030-01-01T00:00:00",
            "updated_at": "2030-01-01T00:00:00",
        }
        self.chat_turns[str(kwargs["turn_id"])] = row
        return row

    def complete_chat_turn(self, turn_id: str, *, reply: str) -> bool:
        row = self.chat_turns[turn_id]
        row["reply"] = reply
        row["status"] = "completed"
        return True

    def fail_chat_turn(self, turn_id: str, *, error: str, reply: str = "") -> bool:
        row = self.chat_turns[turn_id]
        row["error"] = error
        row["reply"] = reply
        row["status"] = "failed"
        return True

    def list_chat_turns(self, *, session: str, scope: str, limit: int) -> list[dict[str, object]]:
        return [
            row
            for row in self.chat_turns.values()
            if row["session"] == session and (not scope or row["scope"] == scope)
        ][:limit]


@pytest.mark.asyncio
async def test_chat_writes_durable_turn_and_can_replay_completed_turn() -> None:
    adapter, *_ = _build_adapter()
    database = _DurableChatDatabase()
    adapter.services.database = database

    first = await adapter.chat(ChatRequest(message="聊聊城市观察", session="openclaw"))
    second = await adapter.chat(
        ChatRequest(message="这条不会重复生成", session="openclaw", turn_id=first.turn_id)
    )
    history = await adapter.get_chat_history(session="openclaw")

    assert first.turn_id
    assert first.status == "completed"
    assert second.reply == first.reply
    assert second.turn_id == first.turn_id
    assert len(history.items) == 1


@pytest.mark.asyncio
async def test_platform_availability_is_exposed_without_api_runtime() -> None:
    adapter, _, _, database, *_ = _build_adapter()

    async def load_availability() -> SimpleNamespace:
        return SimpleNamespace(total_available=7, by_platform={"bilibili": 4, "zhihu": 3})

    database.load_pool_platform_availability_async = load_availability  # type: ignore[attr-defined]
    result = await adapter.get_platform_availability()

    assert result == PlatformAvailabilityResponse(
        total_available=7,
        by_platform={"bilibili": 4, "zhihu": 3},
    )
