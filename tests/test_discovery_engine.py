"""Tests for discovery engine orchestration."""

from __future__ import annotations

import asyncio
import json
import tempfile
from collections import OrderedDict
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import pytest

import openbiliclaw.llm.prompts as prompt_module
from openbiliclaw.discovery import engine as discovery_engine_module
from openbiliclaw.discovery.engine import (
    _DEFAULT_EVALUATION_CANDIDATE_TRANSPORT,
    ContentDiscoveryEngine,
    DiscoveredContent,
    DiscoveryConcurrencyController,
    _prompt_visible_content_fields,
    compact_evaluation_profile_summary,
    discovery_raw_candidate_mode_enabled,
    evaluation_profile_prompt_layers,
    llm_eval_candidate_limit,
)
from openbiliclaw.discovery.pool_snapshot import PoolDistributionSnapshot
from openbiliclaw.discovery.strategies._utils import build_profile_summary
from openbiliclaw.llm.prompt_cache import PromptLayerRenderCache
from openbiliclaw.llm.service import LLMProviderExecutionError
from openbiliclaw.soul.profile import (
    AwarenessNote,
    InsightHypothesis,
    InterestDomain,
    InterestSpecific,
    InterestTag,
    OnionProfile,
    SoulProfile,
)
from openbiliclaw.storage.database import Database

from .test_explore_strategy import (
    FakeBilibiliClient as FakeExploreBilibiliClient,
)
from .test_explore_strategy import (
    FakeLLMService as FakeExploreLLMService,
)
from .test_related_chain_strategy import (
    FakeLLMService as FakeRelatedLLMService,
)
from .test_related_chain_strategy import (
    FakeMemoryManager,
    FakeRelatedClient,
    _event,
)
from .test_search_strategy import FakeBilibiliClient, FakeLLMService, _build_profile
from .test_trending_strategy import (
    FakeLLMService as FakeTrendingLLMService,
)
from .test_trending_strategy import (
    FakeRankingClient,
    _first_rotating_rids,
)


@dataclass
class _SlowResponse:
    content: str


class _SlowLLMService:
    def __init__(self, delay: float = 0.01) -> None:
        self.delay = delay
        self.active_calls = 0
        self.max_active_calls = 0

    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        caller: str = "",
        reasoning_effort: str | None = None,
    ) -> object:
        self.active_calls += 1
        self.max_active_calls = max(self.max_active_calls, self.active_calls)
        await asyncio.sleep(self.delay)
        self.active_calls -= 1
        return _SlowResponse('{"score": 0.88, "reason": "still relevant"}')


def test_prompt_catalog_metrics_are_only_emitted_when_present() -> None:
    ordinary = _prompt_visible_content_fields(
        DiscoveredContent(bvid="BV1", title="普通视频", source_strategy="search")
    )
    catalog = _prompt_visible_content_fields(
        DiscoveredContent(
            bvid="326",
            title="目录条目",
            source_strategy="bangumi-ranked",
            rating_score=9.2,
            rating_count=9_959,
            source_rank=1,
        )
    )

    assert "rating_score" not in ordinary
    assert "rating_count" not in ordinary
    assert "source_rank" not in ordinary
    assert catalog["rating_score"] == 9.2
    assert catalog["rating_count"] == 9_959
    assert catalog["source_rank"] == 1


class _DynamicBatchLLMService:
    """Returns one score per item found in the batch prompt."""

    def __init__(self) -> None:
        self.user_inputs: list[str] = []
        self.max_tokens: list[int] = []

    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        caller: str = "",
        reasoning_effort: str | None = None,
    ) -> object:
        self.user_inputs.append(user_input)
        self.max_tokens.append(max_tokens)
        items = _batch_prompt_items(user_input)
        local_ids = _batch_prompt_uses_local_ids(user_input)
        identity_field = "id" if local_ids else "content_id"
        payload: list[dict[str, object]] = []
        for index, item in enumerate(items):
            identity = (
                item.get("id")
                if local_ids
                else item.get("content_id") or item.get("bvid") or str(index)
            )
            payload.append(
                {
                    identity_field: identity,
                    "score": 0.8,
                    "reason": "ok",
                    "style_key": "deep_dive",
                }
            )
        return _SlowResponse(json.dumps(payload, ensure_ascii=False))


class _CountingEmbeddingService:
    similarity_threshold = 0.82

    def __init__(
        self,
        vectors: dict[str, list[float]],
        *,
        failures: set[str] | None = None,
    ) -> None:
        self.vectors = vectors
        self.failures = failures or set()
        self.calls: list[str] = []

    async def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        if text in self.failures:
            raise RuntimeError(f"embedding failed for {text}")
        return self.vectors.get(text, [])

    def lookup_cached(self, text: str) -> list[float]:
        return self.vectors.get(text, [])


class _NamespacedEmbeddingService(_CountingEmbeddingService):
    def __init__(
        self,
        vectors: dict[str, list[float]],
        *,
        fingerprint: str,
        failures: set[str] | None = None,
    ) -> None:
        super().__init__(vectors, failures=failures)
        self.embedding_fingerprint = fingerprint


_MATCH_VEC = [1.0, 0.0]
_LOW_SIM_VEC = [0.1, 0.9949874371]


def _prefilter_vectors(*, low_texts: list[str] | None = None) -> dict[str, list[float]]:
    vectors = {
        "纪录片": _MATCH_VEC,
        "摄影": _MATCH_VEC,
        "知识": _MATCH_VEC,
        "创作": _MATCH_VEC,
        "匹配内容 深度纪录片解析": _MATCH_VEC,
    }
    for text in low_texts or []:
        vectors[text] = _LOW_SIM_VEC
    return vectors


def _batch_prompt_items(user_input: str) -> list[dict[str, object]]:
    batch_json = user_input.split("<content_batch>", 1)[1].split("</content_batch>", 1)[0]
    raw_items = json.loads(batch_json.strip())
    if isinstance(raw_items, list):
        return [item for item in raw_items if isinstance(item, dict)]
    assert isinstance(raw_items, dict)
    defaults = raw_items.get("defaults")
    items = raw_items.get("items")
    assert isinstance(defaults, dict)
    assert isinstance(items, list)
    return [{**defaults, **item} for item in items if isinstance(item, dict)]


def _batch_prompt_uses_local_ids(user_input: str) -> bool:
    batch_json = user_input.split("<content_batch>", 1)[1].split("</content_batch>", 1)[0]
    return isinstance(json.loads(batch_json.strip()), dict)


def _sparse_batch_prompt_envelope(user_input: str) -> dict[str, object]:
    batch_json = user_input.split("<content_batch>", 1)[1].split("</content_batch>", 1)[0]
    raw_envelope = json.loads(batch_json.strip())
    assert isinstance(raw_envelope, dict)
    assert set(raw_envelope) == {"defaults", "items"}
    return raw_envelope


def _single_prompt_content_summary(user_input: str) -> dict[str, object]:
    summary_json = user_input.split("<content_summary>", 1)[1].split(
        "</content_summary>",
        1,
    )[0]
    raw_summary = json.loads(summary_json.strip())
    assert isinstance(raw_summary, dict)
    return raw_summary


def _profile_block_prefix(user_input: str) -> str:
    return user_input.split("<source_platform>", 1)[0]


def _maxed_onion_profile() -> OnionProfile:
    profile = OnionProfile()
    profile.core.core_traits = [f"trait-{index}" for index in range(35)]
    profile.core.deep_needs = [f"need-{index}" for index in range(35)]
    profile.values_layer.values = [f"value-{index}" for index in range(35)]
    profile.values_layer.motivational_drivers = [f"driver-{index}" for index in range(35)]
    profile.surface.cognitive_style = [f"style-{index}" for index in range(35)]
    profile.recent_awareness = [
        AwarenessNote(
            date=f"2026-07-{(index % 28) + 1:02d}",
            observation=f"awareness-{index}-" + ("x" * 40),
            trend=f"trend-{index}",
        )
        for index in range(30)
    ]
    profile.active_insights = [
        InsightHypothesis(
            hypothesis=f"insight-{index}-" + ("y" * 40),
            evidence=[f"evidence-{index}-{item}-" + ("z" * 24) for item in range(30)],
            created_at=f"2026-07-{(index % 28) + 1:02d}T12:00:00",
        )
        for index in range(30)
    ]
    profile.interest.likes = [
        InterestDomain(
            domain=f"domain-{domain:02d}-" + ("wide" * 4),
            weight=1.0 - domain / 100,
            specifics=[
                InterestSpecific(
                    name=f"specific-{domain:02d}-{specific:02d}-" + ("tail" * 4),
                    weight=1.0 - (specific / 1000),
                )
                for specific in range(20)
            ],
            first_seen=f"2026-06-{(domain % 28) + 1:02d}",
            last_seen=f"2026-07-{(domain % 28) + 1:02d}",
            source="test",
        )
        for domain in range(40)
    ]
    profile.interest.dislikes = [
        InterestDomain(domain=f"avoid-{index}", weight=0.9) for index in range(110)
    ]
    return profile


def _profile_with_ranked_interests(count: int = 48) -> SoulProfile:
    profile = SoulProfile()
    profile.preferences.interests = [
        InterestTag(
            name=f"头部兴趣{index:02d}",
            category="共同领域",
            weight=1.0 - index / 1000,
        )
        for index in range(count)
    ]
    return profile


def _profile_with_tail_interest(*, category: str = "模型") -> SoulProfile:
    profile = _profile_with_ranked_interests()
    profile.preferences.interests.append(
        InterestTag(name="稀有铁路模型", category=category, weight=0.01)
    )
    return profile


class _SplitRetryBatchLLMService:
    """Controllable batch fake for split-retry tests."""

    def __init__(
        self,
        *,
        invalid_batch_calls: set[int] | None = None,
        invalid_all_batches: bool = False,
        count_mismatch_batch_calls: set[int] | None = None,
        rate_limit_batch_calls: set[int] | None = None,
    ) -> None:
        self.invalid_batch_calls = invalid_batch_calls or set()
        self.invalid_all_batches = invalid_all_batches
        self.count_mismatch_batch_calls = count_mismatch_batch_calls or set()
        self.rate_limit_batch_calls = rate_limit_batch_calls or set()
        self.batch_call_sizes: list[int] = []
        self.single_calls = 0

    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        caller: str = "",
        reasoning_effort: str | None = None,
    ) -> object:
        if "<content_batch>" not in user_input:
            self.single_calls += 1
            return _SlowResponse('{"score": 0.51, "reason": "single"}')

        batch_json = user_input.split("<content_batch>", 1)[1].split("</content_batch>", 1)[0]
        items = json.loads(batch_json.strip())
        self.batch_call_sizes.append(len(items))
        call_index = len(self.batch_call_sizes)
        if call_index in self.rate_limit_batch_calls:
            raise LLMProviderExecutionError("Provider returned 429 rate limit")
        if self.invalid_all_batches or call_index in self.invalid_batch_calls:
            return _SlowResponse("not json")

        payload_items = items
        include_ids = True
        if call_index in self.count_mismatch_batch_calls:
            payload_items = items[:-1]
            include_ids = False
        payload: list[dict[str, object]] = []
        style_keys = (
            "deep_focus",
            "quick_scan",
            "hands_on",
            "decision_support",
            "story_immersion",
            "opinion_sparring",
            "social_chat",
            "daily_wander",
            "mood_release",
            "aesthetic_browse",
            "ambient_companion",
            "live_pulse",
            "curiosity_spark",
        )
        for index, item in enumerate(payload_items):
            result: dict[str, object] = {
                "score": 0.73,
                "reason": "batch",
                "style_key": style_keys[index % len(style_keys)],
            }
            if include_ids:
                result["content_id"] = item.get("content_id") or item.get("bvid") or str(index)
            payload.append(result)
        return _SlowResponse(json.dumps(payload, ensure_ascii=False))


def _split_retry_contents(count: int, *, prefix: str) -> list[DiscoveredContent]:
    return [
        DiscoveredContent(
            bvid=f"BV{prefix}{index:04d}",
            title=f"candidate {index}",
            up_name="u",
            source_strategy="trending",
        )
        for index in range(count)
    ]


class _ConcurrentBatchLLMService(_DynamicBatchLLMService):
    def __init__(self, delay: float = 0.01) -> None:
        super().__init__()
        self.delay = delay
        self.active_calls = 0
        self.max_active_calls = 0

    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        caller: str = "",
        reasoning_effort: str | None = None,
    ) -> object:
        self.active_calls += 1
        self.max_active_calls = max(self.max_active_calls, self.active_calls)
        try:
            await asyncio.sleep(self.delay)
            return await super().complete_structured_task(
                system_instruction=system_instruction,
                user_input=user_input,
                history=history,
                temperature=temperature,
                max_tokens=max_tokens,
                caller=caller,
                reasoning_effort=reasoning_effort,
            )
        finally:
            self.active_calls -= 1


class _RecordingMultimodalBatchLLMService(_DynamicBatchLLMService):
    supports_image_input = True

    def __init__(self) -> None:
        super().__init__()
        self.image_inputs: list[list[dict[str, str]]] = []

    async def complete_structured_task(self, **_kwargs: object) -> object:
        raise AssertionError("multimodal batch should use image-aware LLM method")

    async def complete_multimodal_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        image_inputs: list[dict[str, str]],
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        caller: str = "",
        reasoning_effort: str | None = None,
    ) -> object:
        self.image_inputs.append(image_inputs)
        return await super().complete_structured_task(
            system_instruction=system_instruction,
            user_input=user_input,
            history=history,
            temperature=temperature,
            max_tokens=max_tokens,
            caller=caller,
            reasoning_effort=reasoning_effort,
        )


def test_compact_evaluation_profile_summary_keeps_high_signal_context() -> None:
    profile_summary = {
        "core_traits": [f"trait-{index}" for index in range(30)],
        "cognitive_style": [f"style-{index}" for index in range(30)],
        "values": [f"value-{index}" for index in range(30)],
        "motivational_drivers": [f"driver-{index}" for index in range(30)],
        "interest_domains": [
            {
                "domain": f"domain-{index}",
                "weight": 1.0 - index / 100,
                "specifics": [
                    {"name": f"specific-{index}-{item}", "weight": 1.0 - item / 100}
                    for item in range(20)
                ],
            }
            for index in range(40)
        ],
        "interests": [
            {"name": f"interest-{index}", "weight": 1.0 - index / 100} for index in range(100)
        ],
        "disliked_topics": [f"avoid-{index}" for index in range(20)],
        "recent_awareness": [{"observation": f"awareness-{index}"} for index in range(20)],
        "active_insights": [
            {
                "hypothesis": f"insight-{index}",
                "evidence": [f"evidence-{index}-{item}" for item in range(20)],
            }
            for index in range(20)
        ],
        "speculative_interests": [
            {"domain": f"spec-{index}", "reason": "maybe"} for index in range(20)
        ],
    }

    compacted = compact_evaluation_profile_summary(profile_summary)

    assert len(compacted["core_traits"]) == 20
    assert len(compacted["interests"]) == 48
    assert compacted["interests"][0]["name"] == "interest-0"
    assert compacted["interests"][-1]["name"] == "interest-47"
    assert compacted["disliked_topics"] == profile_summary["disliked_topics"]
    assert len(compacted["interest_domains"]) == 32
    assert len(compacted["interest_domains"][0]["specifics"]) == 16
    assert [item["observation"] for item in compacted["recent_awareness"][:2]] == [
        "awareness-8",
        "awareness-9",
    ]
    assert len(compacted["active_insights"]) == 12
    assert len(compacted["active_insights"][0]["evidence"]) == 8
    assert len(compacted["speculative_interests"]) == 12


def test_content_prompt_profile_compactor_is_eval_backcompat_alias() -> None:
    from openbiliclaw.discovery.strategies._utils import compact_content_prompt_profile_summary

    profile_summary = {
        "core_traits": [f"trait-{index}" for index in range(30)],
        "interests": [
            {"name": f"interest-{index}", "weight": 1.0 - index / 100} for index in range(110)
        ],
        "disliked_topics": ["avoid"],
    }

    assert compact_evaluation_profile_summary is compact_content_prompt_profile_summary
    assert compact_content_prompt_profile_summary(profile_summary) == (
        compact_evaluation_profile_summary(profile_summary)
    )


def test_evaluation_profile_summary_uses_compactor_and_preserves_dislikes() -> None:
    profile = _maxed_onion_profile()

    summary = ContentDiscoveryEngine._evaluation_profile_summary(profile)
    expected = compact_evaluation_profile_summary(build_profile_summary(profile))
    full_summary = build_profile_summary(profile)

    assert summary == expected
    assert summary["disliked_topics"] == full_summary["disliked_topics"]
    assert len(summary["disliked_topics"]) == len(full_summary["disliked_topics"])


def test_evaluation_profile_digest_covers_tail_recall_pool() -> None:
    engine = ContentDiscoveryEngine(llm_service=None)
    base = _profile_with_ranked_interests()
    tail = _profile_with_tail_interest(category="共同领域")

    assert engine._evaluation_profile_summary(base) == engine._evaluation_profile_summary(tail)
    assert engine._evaluation_profile_digest(base) != engine._evaluation_profile_digest(tail)


def test_evaluation_profile_digest_changes_when_compacted_domain_changes() -> None:
    engine = ContentDiscoveryEngine(llm_service=None)
    base = _profile_with_ranked_interests()
    changed = _profile_with_ranked_interests()
    changed.preferences.interests.append(
        InterestTag(name="新增高权重领域", category="新增高权重领域", weight=2.0)
    )

    assert engine._evaluation_profile_digest(base) != engine._evaluation_profile_digest(changed)


def test_evaluation_profile_digest_ignores_recent_context_timestamps() -> None:
    engine = ContentDiscoveryEngine(llm_service=None)
    profile_a = _profile_with_ranked_interests()
    profile_b = _profile_with_ranked_interests()
    profile_a.recent_awareness = [
        AwarenessNote(date="2026-07-05T10:00:00", observation="最近反复看铁路模型")
    ]
    profile_b.recent_awareness = [
        AwarenessNote(date="2026-07-05T11:30:00", observation="最近反复看铁路模型")
    ]
    profile_a.active_insights = [
        InsightHypothesis(
            hypothesis="偏好慢节奏结构拆解",
            evidence=["多次看完长视频"],
            created_at="2026-07-05T10:00:00",
        )
    ]
    profile_b.active_insights = [
        InsightHypothesis(
            hypothesis="偏好慢节奏结构拆解",
            evidence=["多次看完长视频"],
            created_at="2026-07-05T11:30:00",
        )
    ]

    assert engine._evaluation_profile_digest(profile_a) == engine._evaluation_profile_digest(
        profile_b
    )


def test_compact_evaluation_profile_summary_strips_recent_context_volatile_fields() -> None:
    compacted = compact_evaluation_profile_summary(
        {
            "recent_awareness": [
                {
                    "date": "2026-07-05T10:00:00",
                    "observation": "偏好铁路模型",
                    "session_context": "run-a",
                }
            ],
            "active_insights": [
                {
                    "created_at": "2026-07-05T10:00:00",
                    "hypothesis": "偏好慢节奏结构拆解",
                    "session_context": "run-a",
                    "evidence": ["看完长视频"],
                }
            ],
        }
    )

    recent = compacted["recent_awareness"][0]
    insight = compacted["active_insights"][0]
    assert isinstance(recent, dict)
    assert isinstance(insight, dict)
    assert "date" not in recent
    assert "session_context" not in recent
    assert "created_at" not in insight
    assert "session_context" not in insight


def test_evaluation_profile_prompt_block_shrinks_by_at_least_sixty_percent() -> None:
    profile = _maxed_onion_profile()
    full_summary = build_profile_summary(profile)
    compacted = ContentDiscoveryEngine._evaluation_profile_summary(profile)
    full_block = "\n\n".join(
        PromptLayerRenderCache().render_json_layers(evaluation_profile_prompt_layers(full_summary))
    )
    compact_block = "\n\n".join(
        PromptLayerRenderCache().render_json_layers(evaluation_profile_prompt_layers(compacted))
    )

    assert len(compact_block) <= len(full_block) * 0.40


class _RecentViewedDatabase:
    def __init__(
        self,
        viewed_bvids: set[str],
        *,
        viewed_content_keys: set[str] | None = None,
    ) -> None:
        self.viewed_bvids = set(viewed_bvids)
        self.viewed_content_keys = set(viewed_content_keys or viewed_bvids)

    def get_recent_viewed_bvids(self) -> set[str]:
        return set(self.viewed_bvids)

    def get_recent_viewed_content_keys(self) -> set[str]:
        return set(self.viewed_content_keys)

    def get_latest_event_id(self) -> int:
        return 0

    def query_events(
        self,
        *,
        satisfaction_modes: frozenset[str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        return []


class _RecordingCacheDatabase(_RecentViewedDatabase):
    def __init__(self, viewed_bvids: set[str]) -> None:
        super().__init__(viewed_bvids)
        self.cached_bvids: list[str] = []

    def count_pool_by_franchise(self) -> dict[str, int]:
        return {}

    def cache_content(self, bvid: str, **kwargs: object) -> None:
        self.cached_bvids.append(bvid)

    def pool_admission_threshold(
        self,
        source_strategy: str,
        requested_threshold: object | None = None,
    ) -> float:
        if source_strategy == "explore":
            return max(0.58, float(requested_threshold or 0.0))
        return max(0.60, float(requested_threshold or 0.0))


class _RawModeAwareStrategy:
    name = "raw_mode"

    def __init__(self) -> None:
        self.llm_evaluation = True
        self.raw_call_entered = asyncio.Event()
        self.release_raw_call = asyncio.Event()
        self.calls: list[tuple[bool, bool]] = []

    async def discover(
        self,
        profile: SoulProfile,
        limit: int = 20,
        *,
        pool_snapshot: object | None = None,
    ) -> list[DiscoveredContent]:
        raw_mode = discovery_raw_candidate_mode_enabled()
        self.calls.append((raw_mode, self.llm_evaluation))
        if raw_mode:
            self.raw_call_entered.set()
            await self.release_raw_call.wait()
        return [
            DiscoveredContent(
                bvid="BVRAW",
                title="raw mode candidate",
                source_strategy=self.name,
                relevance_score=0.9,
            )
        ][:limit]

    def create_backfill_strategy(self) -> None:
        return None


@pytest.mark.asyncio
async def test_produce_candidates_raw_mode_does_not_mutate_concurrent_discover() -> None:
    strategy = _RawModeAwareStrategy()
    engine = ContentDiscoveryEngine()
    engine.register_strategy(strategy)  # type: ignore[arg-type]
    profile = _build_profile()

    produce_task = asyncio.create_task(engine.produce_candidates(profile, limit=1))
    await strategy.raw_call_entered.wait()
    await engine.discover(profile, limit=1)
    strategy.release_raw_call.set()
    await produce_task

    assert (True, True) in strategy.calls
    assert (False, True) in strategy.calls
    assert strategy.llm_evaluation is True


async def _contend_llm_semaphore(
    controller: DiscoveryConcurrencyController,
    *,
    delay: float = 0.01,
) -> None:
    async def _job() -> str:
        await asyncio.sleep(delay)
        return "ok"

    await asyncio.gather(
        controller.run_llm(_job()),
        controller.run_llm(_job()),
    )


async def _contend_bilibili_semaphore(
    controller: DiscoveryConcurrencyController,
    *,
    delay: float = 0.01,
) -> None:
    async def _job() -> str:
        await asyncio.sleep(delay)
        return "ok"

    await asyncio.gather(
        controller.run_bilibili(_job()),
        controller.run_bilibili(_job()),
    )


def test_discovery_concurrency_controller_survives_multiple_event_loops() -> None:
    controller = DiscoveryConcurrencyController(
        bilibili_request_concurrency=1,
        llm_evaluation_concurrency=1,
    )

    asyncio.run(_contend_llm_semaphore(controller))
    asyncio.run(_contend_bilibili_semaphore(controller))
    asyncio.run(_contend_llm_semaphore(controller))
    asyncio.run(_contend_bilibili_semaphore(controller))


@pytest.mark.asyncio
async def test_discovery_engine_runs_registered_search_strategy() -> None:
    from openbiliclaw.discovery.strategies.strategies import SearchStrategy

    engine = ContentDiscoveryEngine()
    strategy = SearchStrategy(
        llm_service=FakeLLMService('{"queries": ["纪录片 原理"]}'),
        bilibili_client=FakeBilibiliClient(
            {"纪录片 原理": [{"bvid": "BV1A", "title": "纪录片", "author": "UP1", "mid": 1}]}
        ),
        llm_evaluation=False,
    )
    engine.register_strategy(strategy)

    results = await engine.discover(_build_profile())

    assert len(results) == 1
    assert results[0].bvid == "BV1A"
    assert results[0].source_strategy == "search"


@pytest.mark.asyncio
async def test_evaluate_content_passes_style_preferences_to_prompt() -> None:
    llm_service = FakeLLMService(
        '{"score": 0.82, "reason": "匹配", "topic_group": "摄影", "style_key": "light_chat"}'
    )
    engine = ContentDiscoveryEngine(llm_service=llm_service)
    profile = _build_profile()
    profile.preferences.style.preferred_duration = "short"
    profile.preferences.style.humor_preference = 0.8
    profile.preferences.style.depth_preference = 0.25

    await engine.evaluate_content(
        DiscoveredContent(
            bvid="BV1STYLE",
            title="摄影散步 vlog",
            description="轻松聊拍照",
            source_strategy="search",
        ),
        profile,
    )

    user_input = str(llm_service.calls[0]["user_input"])
    assert '"preferred_duration": "short"' in user_input
    assert '"humor_preference": 0.8' in user_input
    assert '"depth_preference": 0.25' in user_input


@pytest.mark.asyncio
async def test_evaluate_content_passes_disliked_topics_to_prompt() -> None:
    llm_service = FakeLLMService(
        '{"score": 0.52, "reason": "命中避雷", "topic_group": "混剪", "style_key": "light_chat"}'
    )
    engine = ContentDiscoveryEngine(llm_service=llm_service)
    profile = _build_profile()
    profile.preferences.disliked_topics = ["标题党", "低质混剪"]

    await engine.evaluate_content(
        DiscoveredContent(
            bvid="BV1DISLIKE",
            title="震惊体低质混剪",
            description="标题党式盘点",
            source_strategy="search",
        ),
        profile,
    )

    user_input = str(llm_service.calls[0]["user_input"])
    assert '"disliked_topics": [' in user_input


@pytest.mark.asyncio
async def test_evaluate_content_single_passes_text_metrics_and_tags_to_prompt() -> None:
    llm_service = FakeLLMService(
        '{"score": 0.82, "reason": "匹配", "topic_group": "系统", "style_key": "deep_dive"}'
    )
    engine = ContentDiscoveryEngine(llm_service=llm_service)

    await engine.evaluate_content(
        DiscoveredContent(
            content_id="tweet-1",
            title="正文首行",
            body_text="完整 thread 正文",
            published_at="2026-08-01T12:30:00+00:00",
            tags=["systems", "async"],
            source_platform="twitter",
            content_type="thread",
            view_count=1000,
            like_count=100,
            favorite_count=90,
            collect_count=80,
            comment_count=70,
            share_count=60,
            danmaku_count=50,
            reply_count=40,
            retweet_count=30,
            bookmark_count=20,
            source_strategy="x-search",
        ),
        _build_profile(),
    )

    user_input = str(llm_service.calls[0]["user_input"])
    assert '"body_text": "完整 thread 正文"' in user_input
    assert '"evaluated_at": "' in user_input
    assert '"published_at": "2026-08-01T12:30:00+00:00"' in user_input
    assert '"tags": [' in user_input
    assert '"like_count": 100' in user_input
    assert '"favorite_count": 90' in user_input
    assert '"collect_count": 80' in user_input
    assert '"comment_count": 70' in user_input
    assert '"share_count": 60' in user_input
    assert '"danmaku_count": 50' in user_input
    assert '"reply_count": 40' in user_input
    assert '"retweet_count": 30' in user_input
    assert '"bookmark_count": 20' in user_input


@pytest.mark.asyncio
async def test_evaluate_content_cache_invalidates_when_published_at_changes() -> None:
    llm_service = FakeLLMService(
        '{"score": 0.82, "reason": "匹配", "topic_group": "系统", "style_key": "deep_dive"}'
    )
    engine = ContentDiscoveryEngine(llm_service=llm_service)
    profile = _build_profile()

    await engine.evaluate_content(
        DiscoveredContent(bvid="BV1TIME", title="模型更新", source_strategy="search"),
        profile,
    )
    await engine.evaluate_content(
        DiscoveredContent(
            bvid="BV1TIME",
            title="模型更新",
            published_at="2026-08-04T08:00:00Z",
            source_strategy="search",
        ),
        profile,
    )

    assert len(llm_service.calls) == 2


@pytest.mark.asyncio
async def test_evaluate_content_single_preserves_full_body_text() -> None:
    llm_service = FakeLLMService(
        '{"score": 0.82, "reason": "匹配", "topic_group": "系统", "style_key": "deep_dive"}'
    )
    engine = ContentDiscoveryEngine(llm_service=llm_service)
    body_text = "H" * 300 + "T" * 200

    await engine.evaluate_content(
        DiscoveredContent(
            content_id="tweet-long",
            title="长文首行",
            body_text=body_text,
            source_platform="twitter",
            content_type="thread",
            source_strategy="x-search",
        ),
        _build_profile(),
    )

    user_input = str(llm_service.calls[0]["user_input"])
    assert body_text in user_input


@pytest.mark.asyncio
async def test_related_interests_returns_tail_match_and_degrades_without_embedding() -> None:
    profile = _profile_with_tail_interest()
    content = DiscoveredContent(
        bvid="BVTAIL",
        title="稀有铁路模型",
        description="开箱评测",
        source_strategy="search",
    )
    content_text = "稀有铁路模型 开箱评测"
    vectors = {
        content_text: _MATCH_VEC,
        "稀有铁路模型": _MATCH_VEC,
        **{f"头部兴趣{index:02d}": _LOW_SIM_VEC for index in range(48)},
    }
    engine = ContentDiscoveryEngine(
        llm_service=None,
        embedding_service=_CountingEmbeddingService(vectors),
    )

    related = await engine._related_interests_for_content(content, profile, top_k=3)

    assert related[0] == "稀有铁路模型"
    assert len(related) <= 3
    assert all(isinstance(entry, str) for entry in related)
    engine_without_init = ContentDiscoveryEngine.__new__(ContentDiscoveryEngine)
    assert await engine_without_init._related_interests_for_content(content, profile) == []


@pytest.mark.asyncio
async def test_related_interests_returns_empty_when_embedding_fails() -> None:
    profile = _profile_with_tail_interest()
    content = DiscoveredContent(
        bvid="BVFAIL",
        title="稀有铁路模型",
        description="开箱评测",
        source_strategy="search",
    )
    content_text = "稀有铁路模型 开箱评测"
    engine = ContentDiscoveryEngine(
        llm_service=None,
        embedding_service=_CountingEmbeddingService({}, failures={content_text}),
    )

    assert await engine._related_interests_for_content(content, profile) == []


@pytest.mark.asyncio
async def test_evaluate_batch_adds_related_interests_without_changing_profile_prefix() -> None:
    profile = _profile_with_tail_interest()
    content = DiscoveredContent(
        bvid="BVTAIL",
        title="稀有铁路模型",
        description="开箱评测",
        source_strategy="search",
    )
    content_text = "稀有铁路模型 开箱评测"
    vectors = {
        content_text: _MATCH_VEC,
        "稀有铁路模型": _MATCH_VEC,
        **{f"头部兴趣{index:02d}": _LOW_SIM_VEC for index in range(48)},
    }
    llm_with_recall = _DynamicBatchLLMService()
    engine_with_recall = ContentDiscoveryEngine(
        llm_service=llm_with_recall,
        embedding_service=_CountingEmbeddingService(vectors),
        eval_prefilter_mode="off",
    )
    llm_without_recall = _DynamicBatchLLMService()
    engine_without_recall = ContentDiscoveryEngine(
        llm_service=llm_without_recall,
        embedding_service=None,
        eval_prefilter_mode="off",
    )

    await engine_with_recall._evaluate_batch([content], profile)
    await engine_without_recall._evaluate_batch([content], profile)

    with_recall_input = llm_with_recall.user_inputs[0]
    without_recall_input = llm_without_recall.user_inputs[0]
    related = _batch_prompt_items(with_recall_input)[0]["related_interests"]
    assert isinstance(related, list)
    assert related[0] == "稀有铁路模型"
    assert len(related) <= 3
    assert "related_interests" not in _batch_prompt_items(without_recall_input)[0]
    assert _profile_block_prefix(with_recall_input) == _profile_block_prefix(without_recall_input)


@pytest.mark.asyncio
async def test_evaluate_content_single_adds_related_interests_to_content_summary() -> None:
    profile = _profile_with_tail_interest()
    content = DiscoveredContent(
        bvid="BVTAILSINGLE",
        title="稀有铁路模型",
        description="开箱评测",
        source_strategy="search",
    )
    content_text = "稀有铁路模型 开箱评测"
    llm_service = FakeLLMService(
        '{"score": 0.82, "reason": "匹配", "topic_group": "模型", "style_key": "deep_dive"}'
    )
    engine = ContentDiscoveryEngine(
        llm_service=llm_service,
        embedding_service=_CountingEmbeddingService(
            {
                content_text: _MATCH_VEC,
                "稀有铁路模型": _MATCH_VEC,
                **{f"头部兴趣{index:02d}": _LOW_SIM_VEC for index in range(48)},
            }
        ),
        eval_prefilter_mode="off",
    )

    await engine.evaluate_content(content, profile)

    user_input = str(llm_service.calls[0]["user_input"])
    content_summary = _single_prompt_content_summary(user_input)
    related = content_summary["related_interests"]
    assert isinstance(related, list)
    assert related[0] == "稀有铁路模型"
    assert len(related) <= 3


@pytest.mark.asyncio
async def test_evaluate_content_batch_skips_recently_viewed_before_llm() -> None:
    llm_service = FakeLLMService(
        json.dumps(
            [
                {
                    "bvid": "BV1FRESH",
                    "score": 0.88,
                    "reason": "fresh match",
                    "topic_group": "AI工具",
                    "style_key": "practical_guide",
                }
            ],
            ensure_ascii=False,
        )
    )
    engine = ContentDiscoveryEngine(
        llm_service=llm_service,
        database=_RecentViewedDatabase({"BV1VIEWED"}),  # type: ignore[arg-type]
    )

    scores = await engine.evaluate_content_batch(
        [
            DiscoveredContent(bvid="BV1VIEWED", title="已经看过", source_strategy="trending"),
            DiscoveredContent(bvid="BV1FRESH", title="新内容", source_strategy="trending"),
        ],
        _build_profile(),
    )

    assert scores == [0.0, 0.88]
    assert len(llm_service.calls) == 1
    user_input = str(llm_service.calls[0]["user_input"])
    assert "新内容" in user_input
    assert "BV1FRESH" not in user_input
    assert "BV1VIEWED" not in user_input
    assert "已经看过" not in user_input


@pytest.mark.asyncio
async def test_evaluate_content_batch_limits_llm_batch_concurrency_to_two_by_default() -> None:
    llm_service = _ConcurrentBatchLLMService()
    engine = ContentDiscoveryEngine(llm_service=llm_service)
    contents = [
        DiscoveredContent(
            bvid=f"BV_BATCH_CONCURRENCY_{index}",
            title=f"候选 {index}",
            up_name="UP",
            source_strategy="search",
        )
        for index in range(4)
    ]

    scores = await engine.evaluate_content_batch(contents, _build_profile(), batch_size=1)

    assert scores == [0.8, 0.8, 0.8, 0.8]
    assert llm_service.max_active_calls == 2


@pytest.mark.asyncio
async def test_evaluate_content_batch_skips_recently_viewed_non_bilibili_before_llm() -> None:
    llm_service = FakeLLMService(
        json.dumps(
            [
                {
                    "content_id": "fresh-yt",
                    "score": 0.82,
                    "reason": "fresh youtube match",
                    "topic_group": "AI工具",
                    "style_key": "practical_guide",
                }
            ],
            ensure_ascii=False,
        )
    )
    engine = ContentDiscoveryEngine(
        llm_service=llm_service,
        database=_RecentViewedDatabase(
            set(),
            viewed_content_keys={"youtube:seen-yt"},
        ),  # type: ignore[arg-type]
    )

    scores = await engine.evaluate_content_batch(
        [
            DiscoveredContent(
                content_id="seen-yt",
                source_platform="youtube",
                title="已经看过的 YouTube",
                source_strategy="youtube_search",
            ),
            DiscoveredContent(
                content_id="fresh-yt",
                source_platform="youtube",
                title="新的 YouTube",
                source_strategy="youtube_search",
            ),
        ],
        _build_profile(),
    )

    assert scores == [0.0, 0.82]
    assert len(llm_service.calls) == 1
    user_input = str(llm_service.calls[0]["user_input"])
    assert "新的 YouTube" in user_input
    assert "fresh-yt" not in user_input
    assert "seen-yt" not in user_input
    assert "已经看过的 YouTube" not in user_input


def test_candidate_view_keys_normalize_zhihu_alias() -> None:
    keys = ContentDiscoveryEngine._candidate_view_keys(
        DiscoveredContent(
            content_id="answer:42",
            source_platform="zh",
            title="知乎回答",
            source_strategy="zhihu-hot",
        )
    )

    assert "zhihu:answer:42" in keys


@pytest.mark.asyncio
async def test_evaluate_content_batch_omits_duplicate_text_description() -> None:
    llm_service = _DynamicBatchLLMService()
    engine = ContentDiscoveryEngine(llm_service=llm_service)
    summary = "知乎回答摘要，正文和描述来自同一段插件抓取文本。"

    scores = await engine.evaluate_content_batch(
        [
            DiscoveredContent(
                content_id="zhihu:answer:1",
                source_platform="zhihu",
                content_type="answer",
                title="知乎问题",
                description=summary,
                body_text=summary,
                source_strategy="zhihu-hot",
            ),
            DiscoveredContent(
                content_id="twitter:tweet:1",
                source_platform="twitter",
                content_type="tweet",
                title="Tweet first line",
                description="短描述补充",
                body_text="完整推文正文",
                source_strategy="x-feed",
            ),
        ],
        _build_profile(),
        batch_size=2,
    )

    assert scores == [0.8, 0.8]
    items = _batch_prompt_items(llm_service.user_inputs[0])
    assert items[0]["body_text"] == summary
    assert "description" not in items[0]
    assert items[1]["body_text"] == "完整推文正文"
    assert items[1]["description"] == "短描述补充"


@pytest.mark.asyncio
async def test_evaluate_content_batch_preserves_full_body_text() -> None:
    llm_service = _DynamicBatchLLMService()
    engine = ContentDiscoveryEngine(llm_service=llm_service)
    body_text = "H" * 300 + "T" * 200

    scores = await engine.evaluate_content_batch(
        [
            DiscoveredContent(
                content_id="twitter:tweet:long",
                source_platform="twitter",
                content_type="thread",
                title="Long tweet first line",
                body_text=body_text,
                source_strategy="x-feed",
            )
        ],
        _build_profile(),
        batch_size=1,
    )

    assert scores == [0.8]
    items = _batch_prompt_items(llm_service.user_inputs[0])
    assert items[0]["body_text"] == body_text


@pytest.mark.asyncio
async def test_evaluate_content_batch_prefilter_enforce_filters_cache_and_excludes_llm(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    low_text = "不相关内容 厨房技巧"
    embedding = _CountingEmbeddingService(_prefilter_vectors(low_texts=[low_text]))
    llm_service = _DynamicBatchLLMService()
    database = Database(tmp_path / "prefilter-enforce.db")
    database.initialize()
    engine = ContentDiscoveryEngine(
        llm_service=llm_service,
        database=database,
        embedding_service=embedding,
        eval_prefilter_mode="enforce",
    )
    profile = _build_profile()
    filtered = DiscoveredContent(
        bvid="BVFILTER",
        title="不相关内容",
        description="厨房技巧",
        source_strategy="trending",
    )
    relevant = DiscoveredContent(
        bvid="BVKEEP",
        title="匹配内容",
        description="深度纪录片解析",
        source_strategy="trending",
    )

    with caplog.at_level("INFO", logger="openbiliclaw.discovery.engine"):
        scores = await engine.evaluate_content_batch([filtered, relevant], profile, batch_size=2)

    assert scores == [0.05, 0.8]
    assert filtered.relevance_score == 0.05
    assert filtered.relevance_reason == ""
    assert len(llm_service.user_inputs) == 1
    llm_items = _batch_prompt_items(llm_service.user_inputs[0])
    assert [(item["id"], item["title"]) for item in llm_items] == [("0", "匹配内容")]

    profile_digest = engine._evaluation_profile_digest(profile)
    negative_digest = engine._negative_examples_digest(None)
    cache_key = engine._batch_eval_cache_key(
        filtered,
        profile_digest=profile_digest,
        negative_digest=negative_digest,
    )
    cached = engine._get_eval_cache_entry(cache_key)
    assert cached is not None
    assert cached[:2] == (0.05, "")
    assert database.prefilter_shadow_audit_counts() == {
        "total": 2,
        "joined": 1,
        "incomplete": 1,
    }
    assert any(
        "eval_batch embedding prefilter" in record.message
        and "in=2" in record.message
        and "prefiltered=1" in record.message
        and "to_llm=1" in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_enforce_prefilter_keeps_cold_and_warm_style_caps_identical(
    tmp_path: Path,
) -> None:
    llm_service = _DynamicBatchLLMService()
    database = Database(tmp_path / "prefilter-style-caps.db")
    database.initialize()
    engine = ContentDiscoveryEngine(
        llm_service=llm_service,
        database=database,
        embedding_service=_CountingEmbeddingService(_prefilter_vectors(low_texts=["候选 0"])),
        eval_prefilter_mode="enforce",
    )

    def candidates() -> list[DiscoveredContent]:
        return [
            DiscoveredContent(
                bvid="BVPREFILTER" if index == 0 else f"BVSTYLE{index}",
                title=f"候选 {index}",
                source_strategy="trending",
            )
            for index in range(11)
        ]

    cold = candidates()
    cold_scores = await engine.evaluate_content_batch(cold, _build_profile(), batch_size=10)
    warm = candidates()
    warm_scores = await engine.evaluate_content_batch(warm, _build_profile(), batch_size=10)

    assert cold_scores == [0.05, *([0.8] * 8), 0.0, 0.8]
    assert warm_scores == cold_scores
    assert len(llm_service.user_inputs) == 1


@pytest.mark.asyncio
async def test_batch_evaluation_empty_metadata_clears_stale_object_values_on_cold_and_warm() -> (
    None
):
    class _EmptyMetadataBatchLLMService(_DynamicBatchLLMService):
        async def complete_structured_task(self, **kwargs: object) -> object:
            user_input = str(kwargs["user_input"])
            self.user_inputs.append(user_input)
            items = _batch_prompt_items(user_input)
            local_ids = _batch_prompt_uses_local_ids(user_input)
            identity_field = "id" if local_ids else "content_id"
            return _SlowResponse(
                json.dumps(
                    [
                        {
                            identity_field: (
                                item.get("id")
                                if local_ids
                                else item.get("content_id") or item.get("bvid")
                            ),
                            "score": 0.8,
                            "reason": "内部诊断",
                            "topic_group": "",
                            "style_key": "",
                            "franchise_key": "",
                        }
                        for item in items
                    ],
                    ensure_ascii=False,
                )
            )

    llm_service = _EmptyMetadataBatchLLMService()
    engine = ContentDiscoveryEngine(llm_service=llm_service)

    def stale_candidate() -> DiscoveredContent:
        return DiscoveredContent(
            bvid="BVCLEAR",
            title="无分类候选",
            source_strategy="trending",
            topic_group="stale-topic",
            style_key="deep_dive",
            franchise_key="stale-franchise",
        )

    cold = stale_candidate()
    assert await engine.evaluate_content_batch([cold], _build_profile()) == [0.8]
    assert (cold.topic_group, cold.style_key, cold.franchise_key) == ("", "", "")

    warm = stale_candidate()
    assert await engine.evaluate_content_batch([warm], _build_profile()) == [0.8]
    assert (warm.topic_group, warm.style_key, warm.franchise_key) == ("", "", "")
    assert len(llm_service.user_inputs) == 1


@pytest.mark.asyncio
async def test_embedding_prefilter_clamps_negative_cosine_to_zero() -> None:
    profile = SoulProfile()
    profile.preferences.interests = [InterestTag(name="正向兴趣", category="测试", weight=1.0)]
    embedding = _CountingEmbeddingService(
        {
            "正向兴趣": [1.0, 0.0],
            "反向内容 完全相反": [-1.0, 0.0],
        }
    )
    engine = ContentDiscoveryEngine(
        llm_service=_DynamicBatchLLMService(),
        embedding_service=embedding,
        eval_prefilter_mode="enforce",
    )

    filtered = await engine._embedding_prefilter(  # noqa: SLF001
        [DiscoveredContent(bvid="BVNEG", title="反向内容", description="完全相反")],
        profile,
    )

    assert filtered == {0: 0.0}


@pytest.mark.asyncio
async def test_embedding_prefilter_includes_long_tail_recall_interests() -> None:
    profile = SoulProfile()
    profile.preferences.interests = [
        InterestTag(
            name=f"头部兴趣{index}",
            category="测试",
            weight=1.0 - index / 1000,
        )
        for index in range(48)
    ]
    profile.preferences.interests.append(
        InterestTag(name="第49项长尾", category="测试", weight=0.5)
    )
    vectors = {f"头部兴趣{index}": [1.0, 0.0] for index in range(48)}
    vectors.update(
        {
            "第49项长尾": [0.0, 1.0],
            "长尾命中 只匹配第49项": [0.0, 1.0],
        }
    )
    engine = ContentDiscoveryEngine(
        llm_service=_DynamicBatchLLMService(),
        embedding_service=_CountingEmbeddingService(vectors),
        eval_prefilter_mode="enforce",
    )

    filtered = await engine._embedding_prefilter(  # noqa: SLF001
        [DiscoveredContent(bvid="BVTAIL", title="长尾命中", description="只匹配第49项")],
        profile,
    )

    assert filtered == {}


@pytest.mark.asyncio
async def test_embedding_prefilter_required_set_matches_weight_ranked_top_256() -> None:
    profile = SoulProfile()
    profile.preferences.interests = [
        InterestTag(name="排序外低权重", category="测试", weight=0.0),
        *[
            InterestTag(name=f"中权重兴趣{index}", category="测试", weight=0.5)
            for index in range(255)
        ],
        InterestTag(name="末位高权重必需兴趣", category="测试", weight=1.0),
    ]
    vectors = {f"中权重兴趣{index}": [1.0, 0.0] for index in range(255)}
    vectors["低相似候选 厨房技巧"] = _LOW_SIM_VEC
    engine = ContentDiscoveryEngine(
        llm_service=_DynamicBatchLLMService(),
        embedding_service=_CountingEmbeddingService(vectors),
        eval_prefilter_mode="enforce",
    )

    filtered = await engine._embedding_prefilter(  # noqa: SLF001
        [DiscoveredContent(bvid="BVRANKED", title="低相似候选", description="厨房技巧")],
        profile,
    )

    assert filtered == {}


@pytest.mark.asyncio
async def test_evaluate_content_batch_prefilter_shadow_logs_but_sends_all(
    caplog: pytest.LogCaptureFixture,
) -> None:
    low_text = "不相关内容 厨房技巧"
    embedding = _CountingEmbeddingService(_prefilter_vectors(low_texts=[low_text]))
    llm_service = _DynamicBatchLLMService()
    engine = ContentDiscoveryEngine(llm_service=llm_service, embedding_service=embedding)
    content = DiscoveredContent(
        bvid="BVSHADOW",
        title="不相关内容",
        description="厨房技巧",
        source_strategy="trending",
    )

    with caplog.at_level("INFO", logger="openbiliclaw.discovery.engine"):
        scores = await engine.evaluate_content_batch([content], _build_profile())

    assert scores == [0.8]
    assert content.relevance_score == 0.8
    assert content.relevance_reason == "ok"
    assert len(llm_service.user_inputs) == 1
    assert _batch_prompt_items(llm_service.user_inputs[0])[0]["title"] == "不相关内容"
    assert any(
        "prefilter-shadow" in record.message
        and "不相关内容" in record.message
        and "max_sim=0.1000" in record.message
        and "strategy=trending" in record.message
        for record in caplog.records
    )
    assert any(
        "eval_batch embedding prefilter" in record.message
        and "would_filter=1" in record.message
        and "to_llm=1" in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_evaluate_content_batch_prefilter_off_still_sends_item_to_llm() -> None:
    low_text = "不相关内容 厨房技巧"
    embedding = _CountingEmbeddingService(_prefilter_vectors(low_texts=[low_text]))
    llm_service = _DynamicBatchLLMService()
    engine = ContentDiscoveryEngine(
        llm_service=llm_service,
        embedding_service=embedding,
        eval_prefilter_mode="off",
    )

    scores = await engine.evaluate_content_batch(
        [
            DiscoveredContent(
                bvid="BVOFF",
                title="不相关内容",
                description="厨房技巧",
                source_strategy="trending",
            )
        ],
        _build_profile(),
    )

    assert scores == [0.8]
    assert len(llm_service.user_inputs) == 1
    assert _batch_prompt_items(llm_service.user_inputs[0])[0]["title"] == "不相关内容"


@pytest.mark.asyncio
async def test_evaluate_content_batch_prefilter_exempts_explore_in_enforce() -> None:
    low_text = "跨域内容 厨房技巧"
    embedding = _CountingEmbeddingService(_prefilter_vectors(low_texts=[low_text]))
    llm_service = _DynamicBatchLLMService()
    engine = ContentDiscoveryEngine(
        llm_service=llm_service,
        embedding_service=embedding,
        eval_prefilter_mode="enforce",
    )
    content = DiscoveredContent(
        bvid="BVEXPLORE",
        title="跨域内容",
        description="厨房技巧",
        source_strategy="explore",
    )

    scores = await engine.evaluate_content_batch([content], _build_profile())

    assert scores == [0.8]
    assert len(llm_service.user_inputs) == 1
    assert _batch_prompt_items(llm_service.user_inputs[0])[0]["title"] == "跨域内容"


@pytest.mark.asyncio
async def test_evaluate_content_batch_prefilter_enforce_kill_rate_guard(
    caplog: pytest.LogCaptureFixture,
) -> None:
    low_texts = ["低相似 A 厨房技巧", "低相似 B 家务技巧"]
    embedding = _CountingEmbeddingService(_prefilter_vectors(low_texts=low_texts))
    llm_service = _DynamicBatchLLMService()
    engine = ContentDiscoveryEngine(
        llm_service=llm_service,
        embedding_service=embedding,
        eval_prefilter_mode="enforce",
    )
    contents = [
        DiscoveredContent(bvid="BVA", title="低相似 A", description="厨房技巧"),
        DiscoveredContent(bvid="BVB", title="低相似 B", description="家务技巧"),
        DiscoveredContent(bvid="BVC", title="匹配内容", description="深度纪录片解析"),
    ]

    with caplog.at_level("WARNING", logger="openbiliclaw.discovery.engine"):
        scores = await engine.evaluate_content_batch(contents, _build_profile(), batch_size=3)

    assert scores == [0.8, 0.8, 0.8]
    assert len(llm_service.user_inputs) == 1
    assert [item["title"] for item in _batch_prompt_items(llm_service.user_inputs[0])] == [
        "低相似 A",
        "低相似 B",
        "匹配内容",
    ]
    assert any("embedding prefilter kill-rate guard" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_evaluate_content_batch_prefilter_skips_without_service_or_interests() -> None:
    llm_without_service = _DynamicBatchLLMService()
    engine_without_service = ContentDiscoveryEngine(
        llm_service=llm_without_service,
        eval_prefilter_mode="enforce",
    )

    scores_without_service = await engine_without_service.evaluate_content_batch(
        [DiscoveredContent(bvid="BVNOSVC", title="不相关内容", description="厨房技巧")],
        _build_profile(),
    )

    assert scores_without_service == [0.8]
    assert len(llm_without_service.user_inputs) == 1

    low_text = "不相关内容 厨房技巧"
    embedding = _CountingEmbeddingService(_prefilter_vectors(low_texts=[low_text]))
    llm_empty_interests = _DynamicBatchLLMService()
    engine_empty_interests = ContentDiscoveryEngine(
        llm_service=llm_empty_interests,
        embedding_service=embedding,
        eval_prefilter_mode="enforce",
    )

    scores_empty_interests = await engine_empty_interests.evaluate_content_batch(
        [DiscoveredContent(bvid="BVEMPTY", title="不相关内容", description="厨房技巧")],
        SoulProfile(),
    )

    assert scores_empty_interests == [0.8]
    assert embedding.calls == []
    assert len(llm_empty_interests.user_inputs) == 1


@pytest.mark.asyncio
async def test_evaluate_content_batch_prefilter_item_embedding_failure_fails_open() -> None:
    failing_text = "异常内容 厨房技巧"
    embedding = _CountingEmbeddingService(
        _prefilter_vectors(),
        failures={failing_text},
    )
    llm_service = _DynamicBatchLLMService()
    engine = ContentDiscoveryEngine(
        llm_service=llm_service,
        embedding_service=embedding,
        eval_prefilter_mode="enforce",
    )

    scores = await engine.evaluate_content_batch(
        [
            DiscoveredContent(
                bvid="BVFAILOPEN",
                title="异常内容",
                description="厨房技巧",
                source_strategy="trending",
            )
        ],
        _build_profile(),
    )

    assert scores == [0.8]
    assert len(llm_service.user_inputs) == 1
    assert _batch_prompt_items(llm_service.user_inputs[0])[0]["title"] == "异常内容"


@pytest.mark.asyncio
async def test_evaluate_content_batch_prefilter_all_filtered_batch_of_one_skips_llm(
    tmp_path: Path,
) -> None:
    low_text = "不相关内容 厨房技巧"
    embedding = _CountingEmbeddingService(_prefilter_vectors(low_texts=[low_text]))
    llm_service = _DynamicBatchLLMService()
    database = Database(tmp_path / "prefilter-all-filtered.db")
    database.initialize()
    engine = ContentDiscoveryEngine(
        llm_service=llm_service,
        database=database,
        embedding_service=embedding,
        eval_prefilter_mode="enforce",
    )

    scores = await engine.evaluate_content_batch(
        [
            DiscoveredContent(
                bvid="BVONLY",
                title="不相关内容",
                description="厨房技巧",
                source_strategy="trending",
            )
        ],
        _build_profile(),
    )

    assert scores == [0.05]
    assert llm_service.user_inputs == []


@pytest.mark.asyncio
async def test_evaluate_content_prefilter_shadow_single_path_uses_llm() -> None:
    low_text = "不相关内容 厨房技巧"
    embedding = _CountingEmbeddingService(_prefilter_vectors(low_texts=[low_text]))
    llm_service = FakeLLMService('{"score": 0.84, "reason": "llm kept it"}')
    engine = ContentDiscoveryEngine(llm_service=llm_service, embedding_service=embedding)
    content = DiscoveredContent(
        bvid="BVSINGLE",
        title="不相关内容",
        description="厨房技巧",
        source_strategy="trending",
    )

    score = await engine.evaluate_content(content, _build_profile())

    assert score == 0.84
    assert content.relevance_reason == "llm kept it"
    assert len(llm_service.calls) == 1


@pytest.mark.asyncio
async def test_evaluate_content_prefilter_enforce_single_without_audit_sink_fails_open() -> None:
    low_text = "不相关内容 厨房技巧"
    llm_service = FakeLLMService('{"score": 0.84, "reason": "llm kept it"}')
    engine = ContentDiscoveryEngine(
        llm_service=llm_service,
        embedding_service=_CountingEmbeddingService(_prefilter_vectors(low_texts=[low_text])),
        eval_prefilter_mode="enforce",
    )
    content = DiscoveredContent(
        bvid="BVSINGLEFAILOPEN",
        title="不相关内容",
        description="厨房技巧",
        source_strategy="trending",
    )

    score = await engine.evaluate_content(content, _build_profile())

    assert score == 0.84
    assert content.relevance_reason == "llm kept it"
    assert len(llm_service.calls) == 1


@pytest.mark.asyncio
async def test_evaluate_content_prefilter_enforce_single_with_audit_sink_filters(
    tmp_path: Path,
) -> None:
    low_text = "不相关内容 厨房技巧"
    database = Database(tmp_path / "prefilter-single-enforce.db")
    database.initialize()
    llm_service = FakeLLMService('{"score": 0.84, "reason": "should not run"}')
    engine = ContentDiscoveryEngine(
        llm_service=llm_service,
        database=database,
        embedding_service=_CountingEmbeddingService(_prefilter_vectors(low_texts=[low_text])),
        eval_prefilter_mode="enforce",
    )
    content = DiscoveredContent(
        bvid="BVSINGLEFILTER",
        title="不相关内容",
        description="厨房技巧",
        source_strategy="trending",
    )

    score = await engine.evaluate_content(content, _build_profile())

    assert score == 0.05
    assert llm_service.calls == []
    assert database.prefilter_shadow_audit_counts() == {
        "total": 1,
        "joined": 0,
        "incomplete": 1,
    }


@pytest.mark.asyncio
async def test_multimodal_evaluation_uses_configured_smaller_batch_size() -> None:
    llm_service = _DynamicBatchLLMService()
    engine = ContentDiscoveryEngine(
        llm_service=llm_service,
        multimodal_evaluation_enabled=True,
        multimodal_batch_size=2,
        multimodal_vision_supported=True,
    )

    scores = await engine.evaluate_content_batch(
        [
            DiscoveredContent(
                content_id=f"cover-{idx}",
                title=f"cover item {idx}",
                cover_url=f"https://example.com/{idx}.jpg",
                source_platform="youtube",
                source_strategy="youtube_search",
            )
            for idx in range(5)
        ],
        _build_profile(),
        batch_size=30,
    )

    assert scores == [0.8, 0.8, 0.8, 0.8, 0.8]
    assert len(llm_service.user_inputs) == 3
    assert [len(_batch_prompt_items(prompt)) for prompt in llm_service.user_inputs] == [2, 2, 1]
    assert all("cover-" not in prompt for prompt in llm_service.user_inputs)
    assert engine.multimodal_unavailable_reason == ""


@pytest.mark.asyncio
async def test_text_batch_evaluation_bounds_declared_output_tokens() -> None:
    llm_service = _DynamicBatchLLMService()
    engine = ContentDiscoveryEngine(llm_service=llm_service)

    scores = await engine.evaluate_content_batch(
        [
            DiscoveredContent(
                content_id=f"text-{idx}",
                title=f"text item {idx}",
                source_platform="bilibili",
                source_strategy="trending",
            )
            for idx in range(8)
        ],
        _build_profile(),
        batch_size=8,
    )

    assert scores == [0.8] * 8
    assert llm_service.max_tokens == [16384]


@pytest.mark.asyncio
async def test_multimodal_evaluation_falls_back_to_text_batch_when_vision_unavailable() -> None:
    llm_service = _DynamicBatchLLMService()
    engine = ContentDiscoveryEngine(
        llm_service=llm_service,
        multimodal_evaluation_enabled=True,
        multimodal_batch_size=2,
        multimodal_vision_supported=False,
    )

    scores = await engine.evaluate_content_batch(
        [
            DiscoveredContent(
                content_id=f"cover-{idx}",
                title=f"cover item {idx}",
                cover_url=f"https://example.com/{idx}.jpg",
                source_platform="youtube",
                source_strategy="youtube_search",
            )
            for idx in range(5)
        ],
        _build_profile(),
        batch_size=30,
    )

    assert scores == [0.8, 0.8, 0.8, 0.8, 0.8]
    assert len(llm_service.user_inputs) == 1
    assert "vision-capable" in engine.multimodal_unavailable_reason


@pytest.mark.asyncio
async def test_multimodal_evaluation_sends_prepared_cover_images(monkeypatch) -> None:
    from openbiliclaw.discovery.multimodal import PreparedCoverImage

    async def fake_prepare_cover_image_inputs(*_args: object, **_kwargs: object) -> list[object]:
        return [
            PreparedCoverImage(
                content_id="cover-0",
                data_url="data:image/jpeg;base64,/9j/4AAQSkZJRg==",
                mime_type="image/jpeg",
            )
        ]

    monkeypatch.setattr(
        "openbiliclaw.discovery.multimodal.prepare_cover_image_inputs",
        fake_prepare_cover_image_inputs,
    )
    llm_service = _RecordingMultimodalBatchLLMService()
    engine = ContentDiscoveryEngine(
        llm_service=llm_service,
        multimodal_evaluation_enabled=True,
        multimodal_batch_size=2,
    )

    scores = await engine._evaluate_batch(
        [
            DiscoveredContent(
                content_id="cover-0",
                title="with cover",
                cover_url="https://i.ytimg.com/vi/demo/hqdefault.jpg",
                source_platform="youtube",
                source_strategy="youtube_search",
            )
        ],
        _build_profile(),
    )

    assert scores == [0.8]
    assert llm_service.image_inputs == [
        [
            {
                "content_id": "0",
                "data_url": "data:image/jpeg;base64,/9j/4AAQSkZJRg==",
                "mime_type": "image/jpeg",
            }
        ]
    ]
    assert '"cover_image_ref":"cover:0"' in llm_service.user_inputs[0]
    assert "cover-0" not in llm_service.user_inputs[0]
    assert llm_service.max_tokens == [8192]


@pytest.mark.asyncio
async def test_sparse_multimodal_evaluation_localizes_only_prepared_image_anchors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.discovery.multimodal import PreparedCoverImage

    prepared = [
        PreparedCoverImage(
            content_id="GLOBAL-C",
            data_url="data:image/jpeg;base64,third",
            mime_type="image/jpeg",
        ),
        PreparedCoverImage(
            content_id="GLOBAL-A",
            data_url="data:image/png;base64,first",
            mime_type="image/png",
        ),
    ]

    async def fake_prepare_cover_image_inputs(
        *_args: object,
        **_kwargs: object,
    ) -> list[PreparedCoverImage]:
        return prepared

    monkeypatch.setattr(
        "openbiliclaw.discovery.multimodal.prepare_cover_image_inputs",
        fake_prepare_cover_image_inputs,
    )

    class _SparseMultimodalLLMService:
        supports_image_input = True

        def __init__(self) -> None:
            self.user_inputs: list[str] = []
            self.image_inputs: list[list[dict[str, str]]] = []

        async def complete_structured_task(self, **_kwargs: object) -> object:
            raise AssertionError("prepared images must use the multimodal evaluator")

        async def complete_multimodal_structured_task(
            self,
            *,
            system_instruction: str,
            user_input: str,
            image_inputs: list[dict[str, str]],
            history: list[dict[str, str]] | None = None,
            temperature: float = 0.7,
            max_tokens: int = 4096,
            caller: str = "",
            reasoning_effort: str | None = None,
        ) -> object:
            self.user_inputs.append(user_input)
            self.image_inputs.append(image_inputs)
            envelope = _sparse_batch_prompt_envelope(user_input)
            items = envelope["items"]
            assert isinstance(items, list)
            return _SlowResponse(
                json.dumps(
                    [
                        {
                            "id": str(index),
                            "score": 0.7 + index / 100,
                            "reason": "ok",
                            "topic_group": "visual",
                            "style_key": "aesthetic_browse",
                            "franchise_key": "",
                        }
                        for index in range(len(items))
                    ]
                )
            )

    llm = _SparseMultimodalLLMService()
    engine = ContentDiscoveryEngine(
        llm_service=llm,
        evaluation_candidate_transport="sparse-json",
        multimodal_evaluation_enabled=True,
    )
    contents = [
        DiscoveredContent(
            content_id="GLOBAL-A",
            title="first",
            author_name="u",
            cover_url="https://example.com/a.jpg",
            source_platform="youtube",
            content_type="video",
            source_strategy="search",
        ),
        DiscoveredContent(
            content_id="GLOBAL-B",
            title="text fallback",
            author_name="u",
            cover_url="https://example.com/b.jpg",
            source_platform="youtube",
            content_type="video",
            source_strategy="search",
        ),
        DiscoveredContent(
            content_id="GLOBAL-C",
            title="third",
            author_name="u",
            cover_url="https://example.com/c.jpg",
            source_platform="youtube",
            content_type="video",
            source_strategy="search",
        ),
    ]

    scores = await engine._evaluate_batch(
        contents,
        _build_profile(),
        normal_cache_enabled=False,
        apply_batch_caps=False,
    )

    assert scores == [0.7, 0.71, 0.72]
    envelope = _sparse_batch_prompt_envelope(llm.user_inputs[0])
    items = envelope["items"]
    assert isinstance(items, list)
    assert items[0]["cover_image_ref"] == "cover:0"
    assert "cover_image_ref" not in items[1]
    assert items[2]["cover_image_ref"] == "cover:2"
    assert llm.image_inputs == [
        [
            {
                "content_id": "2",
                "data_url": "data:image/jpeg;base64,third",
                "mime_type": "image/jpeg",
            },
            {
                "content_id": "0",
                "data_url": "data:image/png;base64,first",
                "mime_type": "image/png",
            },
        ]
    ]
    candidate_wire = json.dumps(envelope, ensure_ascii=False)
    assert "GLOBAL-A" not in candidate_wire
    assert "GLOBAL-B" not in candidate_wire
    assert "GLOBAL-C" not in candidate_wire


async def test_multimodal_evaluation_e2e_binds_cached_cover_to_content_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from PIL import Image

    from openbiliclaw.llm.base import LLMResponse
    from openbiliclaw.llm.service import LLMService
    from openbiliclaw.runtime import image_cache

    cover_url = "https://i.ytimg.com/vi/openbiliclaw-e2e/hqdefault.jpg"
    image_content_id = "yt-e2e"
    text_content_id = "x-text"

    monkeypatch.setattr(image_cache, "_CACHE_DIR", tmp_path)
    image = Image.new("RGB", (640, 360), (18, 104, 166))
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=92)
    image_cache.save_image_bytes(cover_url, buffer.getvalue(), "image/jpeg")

    class _VisionProvider:
        _model = "gpt-4o-mini"

    class _CapturingRegistry:
        default_provider = "openai"

        def __init__(self) -> None:
            self.calls: list[list[dict[str, object]]] = []
            self.json_modes: list[bool] = []

        def get(self, _provider_key: str) -> object:
            return _VisionProvider()

        async def complete(
            self,
            messages: list[dict[str, object]],
            *,
            temperature: float = 0.7,
            max_tokens: int = 4096,
            json_mode: bool = False,
            reasoning_effort: str | None = None,
        ) -> LLMResponse:
            self.calls.append(messages)
            self.json_modes.append(json_mode)
            return LLMResponse(
                content=json.dumps(
                    [
                        {
                            "id": "0",
                            "score": 0.81,
                            "reason": "封面和标题都指向视觉向内容，和画像匹配。",
                            "topic_group": "视觉分析",
                            "style_key": "visual_showcase",
                            "franchise_key": "",
                        },
                        {
                            "id": "1",
                            "score": 0.42,
                            "reason": "纯文本候选只有正文线索，匹配度一般。",
                            "topic_group": "社交动态",
                            "style_key": "light_chat",
                            "franchise_key": "",
                        },
                    ],
                    ensure_ascii=False,
                ),
                provider="capturing",
            )

    registry = _CapturingRegistry()
    engine = ContentDiscoveryEngine(
        llm_service=LLMService(registry=registry, memory=None),  # type: ignore[arg-type]
        multimodal_evaluation_enabled=True,
        multimodal_batch_size=4,
        multimodal_image_max_px=384,
        multimodal_image_quality=72,
        multimodal_image_timeout_seconds=6,
    )

    scores = await engine._evaluate_batch(
        [
            DiscoveredContent(
                content_id=image_content_id,
                source_platform="youtube",
                source_strategy="yt_search",
                content_type="video",
                title="A visual analysis demo",
                description="A demo item with a real cached cover image.",
                cover_url=cover_url,
                view_count=1234,
                like_count=56,
                tags=["visual", "demo"],
            ),
            DiscoveredContent(
                content_id=text_content_id,
                source_platform="twitter",
                source_strategy="x-search",
                content_type="tweet",
                title="A text-only tweet",
                body_text="This candidate intentionally has no cover image.",
                like_count=7,
            ),
        ],
        SoulProfile(
            core_traits=["偏好视觉细节和结构化分析"],
            cognitive_style=["喜欢把图像线索和文字线索一起判断"],
            deep_needs=["快速判断内容是否值得点开"],
        ),
        source_context="e2e",
    )

    assert scores == [0.81, 0.42]
    assert registry.json_modes == [True]
    assert any(path.stem == image_cache.image_cache_key(cover_url) for path in tmp_path.glob("*.*"))

    messages = registry.calls[0]
    assert len(messages) == 2
    system_prompt = str(messages[0]["content"])
    assert "cover_image_ref" in system_prompt
    assert "没有 cover_image_ref" in system_prompt

    user_parts = messages[1]["content"]
    assert isinstance(user_parts, list)
    assert [part.get("type") for part in user_parts if isinstance(part, dict)] == [
        "text",
        "text",
        "image_url",
    ]
    user_text = str(user_parts[0]["text"])
    content_batch = _sparse_batch_prompt_envelope(user_text)
    items = content_batch["items"]
    assert isinstance(items, list)
    expected_ref = "cover:0"
    assert items[0]["cover_image_ref"] == expected_ref
    assert "cover_image_ref" not in items[1]
    assert image_content_id not in user_text
    assert text_content_id not in user_text

    assert user_parts[1] == {
        "type": "text",
        "text": (
            f"Cover image {expected_ref} maps to the content_batch item whose "
            f"cover_image_ref is {expected_ref}."
        ),
    }
    assert user_parts[2]["type"] == "image_url"
    assert user_parts[2]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_cache_results_skips_recently_viewed_items() -> None:
    database = _RecordingCacheDatabase({"BV1VIEWED"})
    engine = ContentDiscoveryEngine(database=database)  # type: ignore[arg-type]

    engine._cache_results(
        [
            DiscoveredContent(bvid="BV1VIEWED", title="已经看过", relevance_score=0.90),
            DiscoveredContent(bvid="BV1FRESH", title="新内容", relevance_score=0.90),
        ]
    )

    assert database.cached_bvids == ["BV1FRESH"]


def test_cache_results_skips_recently_viewed_non_bilibili_items() -> None:
    database = _RecordingCacheDatabase(set())
    database.viewed_content_keys = {"xiaohongshu:note-seen"}
    engine = ContentDiscoveryEngine(database=database)  # type: ignore[arg-type]

    engine._cache_results(
        [
            DiscoveredContent(
                content_id="note-seen",
                source_platform="xiaohongshu",
                title="已经看过的小红书",
                relevance_score=0.90,
            ),
            DiscoveredContent(
                content_id="note-fresh",
                source_platform="xiaohongshu",
                title="新小红书",
                relevance_score=0.90,
            ),
        ]
    )

    assert database.cached_bvids == ["xiaohongshu:note-fresh"]


def test_cache_results_rechecks_admission_before_writing() -> None:
    database = _RecordingCacheDatabase(set())
    engine = ContentDiscoveryEngine(database=database)  # type: ignore[arg-type]

    engine._cache_results(
        [
            DiscoveredContent(
                bvid="BV1TRENDLOW",
                title="不匹配的热门内容",
                source_strategy="trending",
                relevance_score=0.59,
                score_threshold=0.20,
            ),
            DiscoveredContent(
                bvid="BV1EXPLORE",
                title="合格的跨域发现",
                source_strategy="explore",
                relevance_score=0.58,
                score_threshold=0.55,
            ),
        ]
    )

    assert database.cached_bvids == ["BV1EXPLORE"]


def test_cache_results_cannot_bypass_temporal_admission_gate() -> None:
    database = _RecordingCacheDatabase(set())
    engine = ContentDiscoveryEngine(database=database)  # type: ignore[arg-type]
    stale = DiscoveredContent(
        bvid="BV1STALEBREAKING",
        title="过期突发内容",
        published_at="2000-01-01T00:00:00Z",
        relevance_score=0.95,
    )
    stale.temporal_class = "breaking"
    stale.temporal_confidence = 0.95
    stale.temporal_policy_version = "v1"
    durable = DiscoveredContent(
        bvid="BV1DURABLE",
        title="长期有效内容",
        published_at="2000-01-01T00:00:00Z",
        relevance_score=0.95,
    )
    durable.temporal_class = "evergreen"
    durable.temporal_confidence = 0.95

    engine._cache_results([stale, durable])

    assert database.cached_bvids == ["BV1DURABLE"]


@pytest.mark.asyncio
async def test_discovery_engine_handles_empty_strategy_results() -> None:
    from openbiliclaw.discovery.strategies.strategies import SearchStrategy

    engine = ContentDiscoveryEngine()
    engine.register_strategy(
        SearchStrategy(
            llm_service=FakeLLMService('{"queries": []}'),
            bilibili_client=FakeBilibiliClient({}),
            llm_evaluation=False,
        )
    )

    results = await engine.discover(SoulProfile())

    assert results == []


@pytest.mark.asyncio
async def test_discovery_engine_runs_registered_trending_strategy() -> None:
    from openbiliclaw.discovery.engine import ContentDiscoveryEngine
    from openbiliclaw.discovery.strategies.strategies import TrendingStrategy

    engine = ContentDiscoveryEngine(
        llm_service=FakeTrendingLLMService(
            [
                '{"score": 0.83, "reason": "符合你的深度内容偏好。"}',
            ]
        )
    )
    rids = _first_rotating_rids()
    engine.register_strategy(
        TrendingStrategy(
            bilibili_client=FakeRankingClient(
                {
                    0: [{"bvid": "BV1A", "title": "全站榜", "author": "UP1", "mid": 1}],
                    rids[1]: [],
                }
            ),
            llm_service=engine._llm_service,
            score_threshold=0.65,
        )
    )

    results = await engine.discover(_build_profile())

    assert len(results) == 1
    assert results[0].bvid == "BV1A"
    assert results[0].source_strategy == "trending"


@pytest.mark.asyncio
async def test_discovery_engine_runs_related_chain_strategy() -> None:
    from openbiliclaw.discovery.engine import ContentDiscoveryEngine
    from openbiliclaw.discovery.strategies.strategies import RelatedChainStrategy

    engine = ContentDiscoveryEngine(
        llm_service=FakeRelatedLLMService(['{"score": 0.84, "reason": "延续了近期观看兴趣。"}'])
    )
    engine.register_strategy(
        RelatedChainStrategy(
            bilibili_client=FakeRelatedClient(
                {
                    "BV1SEED": [
                        {
                            "bvid": "BV1REL",
                            "title": "相关推荐",
                            "owner": {"name": "UPR", "mid": 10},
                        }
                    ]
                }
            ),
            llm_service=engine._llm_service,
            memory_manager=FakeMemoryManager(events=[_event("BV1SEED")]),
        )
    )

    results = await engine.discover(_build_profile())

    assert len(results) == 1
    assert results[0].bvid == "BV1REL"
    assert results[0].source_strategy == "related_chain"


@pytest.mark.asyncio
async def test_discovery_engine_runs_explore_strategy() -> None:
    from openbiliclaw.discovery.engine import ContentDiscoveryEngine
    from openbiliclaw.discovery.strategies.strategies import ExploreStrategy

    engine = ContentDiscoveryEngine(
        llm_service=FakeExploreLLMService(
            [
                """
                {
                  "domains": [
                    {
                      "domain": "城市空间与建筑叙事",
                      "why_it_might_resonate": "你偏好理解复杂系统。",
                      "novelty_level": 0.7,
                      "queries": ["城市 建筑 纪录片"]
                    }
                  ]
                }
                """,
                '{"score": 0.84, "reason": "这个陌生主题仍然符合你的理解欲。"}',
            ]
        )
    )
    engine.register_strategy(
        ExploreStrategy(
            llm_service=engine._llm_service,
            bilibili_client=FakeExploreBilibiliClient(
                {
                    "城市 建筑 纪录片": [
                        {"bvid": "BV1EXP", "title": "城市建筑", "author": "UPX", "mid": 9}
                    ]
                }
            ),
        )
    )

    results = await engine.discover(_build_profile())

    assert len(results) == 1
    assert results[0].bvid == "BV1EXP"
    assert results[0].source_strategy == "explore"


class _RecordingStrategy:
    def __init__(
        self,
        name: str,
        result: list[DiscoveredContent],
        *,
        delay: float = 0.0,
        should_fail: bool = False,
        started: list[str] | None = None,
    ) -> None:
        self._name = name
        self._result = result
        self._delay = delay
        self._should_fail = should_fail
        self._started = started if started is not None else []
        self.limits: list[int] = []

    @property
    def name(self) -> str:
        return self._name

    async def discover(self, profile: SoulProfile, limit: int = 20) -> list[DiscoveredContent]:
        self._started.append(self._name)
        self.limits.append(limit)
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._should_fail:
            raise RuntimeError(f"boom: {self._name}")
        return self._result[:limit]


class _BackfillAwareStrategy(_RecordingStrategy):
    def __init__(
        self,
        name: str,
        result: list[DiscoveredContent],
        *,
        backfill_result: list[DiscoveredContent],
        started: list[str] | None = None,
        backfill_started: list[str] | None = None,
    ) -> None:
        super().__init__(name, result, started=started)
        self._backfill_result = backfill_result
        self._backfill_started = backfill_started if backfill_started is not None else []

    def create_backfill_strategy(self) -> _RecordingStrategy:
        return _RecordingStrategy(
            f"{self.name}-backfill",
            self._backfill_result,
            started=self._backfill_started,
        )


class _PoolSnapshotStrategy(_RecordingStrategy):
    def __init__(self) -> None:
        super().__init__(
            "snapshot-aware",
            [
                DiscoveredContent(
                    bvid="BV1SNAP",
                    relevance_score=0.9,
                    source_strategy="snapshot-aware",
                )
            ],
        )
        self.pool_snapshots: list[object | None] = []

    async def discover(
        self,
        profile: SoulProfile,
        limit: int = 20,
        *,
        pool_snapshot: object | None = None,
    ) -> list[DiscoveredContent]:
        self.pool_snapshots.append(pool_snapshot)
        return await super().discover(profile, limit=limit)


class _PoolSnapshotBackfillStrategy(_RecordingStrategy):
    def __init__(self, backfill_strategy: _PoolSnapshotStrategy) -> None:
        super().__init__(
            "snapshot-primary",
            [
                DiscoveredContent(
                    bvid="BV1PRIMARY",
                    relevance_score=0.9,
                    source_strategy="snapshot-primary",
                )
            ],
        )
        self._backfill_strategy = backfill_strategy

    def create_backfill_strategy(self) -> _PoolSnapshotStrategy:
        return self._backfill_strategy


@pytest.mark.asyncio
async def test_produce_candidates_does_not_evaluate_or_cache() -> None:
    strategy = _RecordingStrategy(
        "search",
        [DiscoveredContent(bvid="BV1", title="Raw", source_strategy="search")],
    )
    strategy.llm_evaluation = True  # type: ignore[attr-defined]
    db = _RecordingCacheDatabase(set())
    llm = _SlowLLMService()
    engine = ContentDiscoveryEngine(llm_service=llm, database=db)
    engine.register_strategy(strategy)

    items = await engine.produce_candidates(_build_profile(), strategies=["search"], limit=10)

    assert [item.bvid for item in items] == ["BV1"]
    assert db.cached_bvids == []
    assert llm.max_active_calls == 0
    assert strategy.llm_evaluation is True  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_produce_candidates_stamps_strategy_score_threshold() -> None:
    strategy = _RecordingStrategy(
        "related_chain",
        [DiscoveredContent(bvid="BVTHRESH", title="Raw", source_strategy="related_chain")],
    )
    strategy.score_threshold = 0.70  # type: ignore[attr-defined]
    engine = ContentDiscoveryEngine(
        llm_service=_SlowLLMService(), database=_RecordingCacheDatabase(set())
    )
    engine.register_strategy(strategy)

    items = await engine.produce_candidates(
        _build_profile(),
        strategies=["related_chain"],
        limit=10,
    )

    assert items[0].score_threshold == 0.70


@pytest.mark.asyncio
async def test_register_strategy_replaces_existing_strategy_with_same_name() -> None:
    started: list[str] = []
    engine = ContentDiscoveryEngine()
    engine.register_strategy(
        _RecordingStrategy(
            "douyin_direct",
            [DiscoveredContent(bvid="dy:old", source_strategy="old")],
            started=started,
        )
    )
    engine.register_strategy(
        _RecordingStrategy(
            "douyin_direct",
            [DiscoveredContent(bvid="dy:new", source_strategy="new")],
            started=started,
        )
    )

    results = await engine.discover(
        _build_profile(),
        strategies=["douyin_direct"],
        limit=20,
    )

    assert started == ["douyin_direct"]
    assert [item.bvid for item in results] == ["dy:new"]


@pytest.mark.asyncio
async def test_discovery_engine_passes_pool_snapshot_to_supported_strategy() -> None:
    pool_snapshot = object()
    strategy = _PoolSnapshotStrategy()
    engine = ContentDiscoveryEngine()
    engine.register_strategy(strategy)

    results = await engine.discover(
        _build_profile(),
        strategies=["snapshot-aware"],
        limit=1,
        pool_snapshot=pool_snapshot,
    )

    assert [item.bvid for item in results] == ["BV1SNAP"]
    assert strategy.pool_snapshots == [pool_snapshot]


@pytest.mark.asyncio
async def test_discovery_engine_keeps_legacy_strategy_signature() -> None:
    strategy = _RecordingStrategy(
        "legacy",
        [
            DiscoveredContent(
                bvid="BV1LEGACY",
                relevance_score=0.9,
                source_strategy="legacy",
            )
        ],
    )
    engine = ContentDiscoveryEngine()
    engine.register_strategy(strategy)

    results = await engine.discover(
        _build_profile(),
        strategies=["legacy"],
        limit=1,
        pool_snapshot=object(),
    )

    assert [item.bvid for item in results] == ["BV1LEGACY"]
    assert strategy.limits == [1]


@pytest.mark.asyncio
async def test_discovery_engine_passes_pool_snapshot_to_backfill_strategy() -> None:
    pool_snapshot = object()
    backfill_strategy = _PoolSnapshotStrategy()
    engine = ContentDiscoveryEngine(target_primary_count=2)
    engine.register_strategy(_PoolSnapshotBackfillStrategy(backfill_strategy))

    results = await engine.discover(
        _build_profile(),
        strategies=["snapshot-primary"],
        limit=2,
        pool_snapshot=pool_snapshot,
    )

    assert [item.bvid for item in results] == ["BV1PRIMARY", "BV1SNAP"]
    assert backfill_strategy.pool_snapshots == [pool_snapshot]


@pytest.mark.asyncio
async def test_pool_snapshot_soft_rerank_prefers_undercovered_topics_without_dropping_strong_matches(  # noqa: E501
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sat = DiscoveredContent(
        bvid="BVsat",
        title="AI",
        topic_group="AI 编程",
        style_key="deep_dive",
        relevance_score=0.82,
    )
    gap = DiscoveredContent(
        bvid="BVgap",
        title="纪录",
        topic_group="人物纪录",
        style_key="story_doc",
        relevance_score=0.79,
    )
    strong = DiscoveredContent(
        bvid="BVstrong",
        title="AI high",
        topic_group="AI 编程",
        relevance_score=0.96,
    )
    pool_snapshot = PoolDistributionSnapshot(
        pool_target_count=10,
        pool_available_count=10,
        source_targets={},
        source_counts={},
        source_deficits={},
        saturated_topics=("AI 编程",),
        undercovered_axes=("人物纪录",),
    )

    class _ThreeCandidateStrategy(_RecordingStrategy):
        async def discover(
            self,
            profile: SoulProfile,
            limit: int = 20,
        ) -> list[DiscoveredContent]:
            self.limits.append(limit)
            return [sat, gap, strong]

    strategy = _ThreeCandidateStrategy("search", [sat, gap, strong])
    engine = ContentDiscoveryEngine()
    engine.register_strategy(strategy)
    monkeypatch.setattr(
        ContentDiscoveryEngine,
        "_compress_topic_repeats",
        staticmethod(lambda results, *, limit: results[:limit]),
    )
    monkeypatch.setattr(engine, "_cache_results", lambda results: None)

    results = await engine.discover(
        _build_profile(),
        strategies=["search"],
        limit=2,
        pool_snapshot=pool_snapshot,
    )

    assert [item.bvid for item in results] == ["BVstrong", "BVgap"]
    assert gap.relevance_score == 0.79
    assert sat.relevance_score == 0.82


@pytest.mark.asyncio
async def test_pool_snapshot_soft_rerank_runs_before_real_compression() -> None:
    strong = DiscoveredContent(
        bvid="BVstrong",
        title="AI high",
        topic_group="AI 编程",
        style_key="tech_analysis",
        source_strategy="search",
        relevance_score=0.96,
    )
    weak_saturated = DiscoveredContent(
        bvid="BVsatweak",
        title="AI tool",
        topic_group="AI 工具",
        style_key="deep_dive",
        source_strategy="explore",
        relevance_score=0.82,
    )
    gap = DiscoveredContent(
        bvid="BVgap",
        title="人物纪录",
        topic_group="人物纪录",
        style_key="story_doc",
        source_strategy="related_chain",
        relevance_score=0.79,
    )
    pool_snapshot = PoolDistributionSnapshot(
        pool_target_count=10,
        pool_available_count=10,
        source_targets={},
        source_counts={},
        source_deficits={},
        saturated_topics=("AI 编程", "AI 工具"),
        undercovered_axes=("人物纪录",),
    )

    class _ThreeSourceStrategy(_RecordingStrategy):
        async def discover(
            self,
            profile: SoulProfile,
            limit: int = 20,
        ) -> list[DiscoveredContent]:
            self.limits.append(limit)
            return [strong, weak_saturated, gap]

    strategy = _ThreeSourceStrategy("search", [strong, weak_saturated, gap])
    engine = ContentDiscoveryEngine()
    engine.register_strategy(strategy)

    results = await engine.discover(
        _build_profile(),
        strategies=["search"],
        limit=2,
        pool_snapshot=pool_snapshot,
    )

    assert [item.bvid for item in results] == ["BVstrong", "BVgap"]
    assert "BVsatweak" not in {item.bvid for item in results}
    assert strong.relevance_score == 0.96
    assert gap.relevance_score == 0.79


def test_llm_eval_candidate_limit_uses_tighter_small_gap_window() -> None:
    assert llm_eval_candidate_limit(1) == 6
    assert llm_eval_candidate_limit(3) == 6
    assert llm_eval_candidate_limit(30) == 60


@pytest.mark.asyncio
async def test_discovery_engine_applies_strategy_specific_limits() -> None:
    started: list[str] = []
    search = _RecordingStrategy(
        "search",
        [
            DiscoveredContent(
                bvid=f"BVSEARCH{index}",
                relevance_score=0.9 - index * 0.01,
                source_strategy="search",
            )
            for index in range(5)
        ],
        started=started,
    )
    related = _RecordingStrategy(
        "related_chain",
        [
            DiscoveredContent(
                bvid=f"BVRELATED{index}",
                relevance_score=0.8 - index * 0.01,
                source_strategy="related_chain",
            )
            for index in range(5)
        ],
        started=started,
    )
    trending = _RecordingStrategy(
        "trending",
        [
            DiscoveredContent(
                bvid=f"BVTREND{index}",
                relevance_score=0.7 - index * 0.01,
                source_strategy="trending",
            )
            for index in range(5)
        ],
        started=started,
    )
    explore = _RecordingStrategy(
        "explore",
        [
            DiscoveredContent(
                bvid=f"BVEXPLORE{index}",
                relevance_score=0.6 - index * 0.01,
                source_strategy="explore",
            )
            for index in range(5)
        ],
        started=started,
    )
    engine = ContentDiscoveryEngine()
    for strategy in (search, related, trending, explore):
        engine.register_strategy(strategy)

    results = await engine.discover(
        _build_profile(),
        strategies=["search", "related_chain", "trending", "explore"],
        limit=5,
        strategy_limits={
            "search": 2,
            "related_chain": 1,
            "trending": 1,
            "explore": 1,
        },
    )

    assert started == ["search", "related_chain", "trending", "explore"]
    assert search.limits == [2]
    assert related.limits == [1]
    assert trending.limits == [1]
    assert explore.limits == [1]
    assert len(results) == 5


@pytest.mark.asyncio
async def test_discovery_engine_runs_strategies_concurrently_and_tolerates_failures() -> None:
    started: list[str] = []
    engine = ContentDiscoveryEngine()
    engine.register_strategy(
        _RecordingStrategy(
            "slow-search",
            [DiscoveredContent(bvid="BV1A", relevance_score=0.72, source_strategy="search")],
            delay=0.02,
            started=started,
        )
    )
    engine.register_strategy(
        _RecordingStrategy(
            "fast-failing",
            [],
            delay=0.0,
            should_fail=True,
            started=started,
        )
    )
    engine.register_strategy(
        _RecordingStrategy(
            "fast-trending",
            [DiscoveredContent(bvid="BV1B", relevance_score=0.81, source_strategy="trending")],
            delay=0.0,
            started=started,
        )
    )

    results = await engine.discover(_build_profile(), limit=20)

    assert started == ["slow-search", "fast-failing", "fast-trending"]
    assert [item.bvid for item in results] == ["BV1B", "BV1A"]


@pytest.mark.asyncio
async def test_discovery_engine_keeps_highest_scored_duplicate() -> None:
    engine = ContentDiscoveryEngine()
    engine.register_strategy(
        _RecordingStrategy(
            "search",
            [
                DiscoveredContent(
                    bvid="BV1DUP",
                    title="低分版本",
                    relevance_score=0.52,
                    source_strategy="search",
                )
            ],
        )
    )
    engine.register_strategy(
        _RecordingStrategy(
            "trending",
            [
                DiscoveredContent(
                    bvid="BV1DUP",
                    title="高分版本",
                    relevance_score=0.91,
                    source_strategy="trending",
                )
            ],
        )
    )

    results = await engine.discover(_build_profile(), limit=20)

    assert len(results) == 1
    assert results[0].title == "高分版本"
    assert results[0].source_strategy == "trending"


@pytest.mark.asyncio
async def test_discovery_engine_compresses_repeated_topic_keys_in_pool() -> None:
    class _UnlimitedStrategy(_RecordingStrategy):
        async def discover(self, profile: SoulProfile, limit: int = 20) -> list[DiscoveredContent]:
            self._started.append(self._name)
            return list(self._result)

    engine = ContentDiscoveryEngine()
    engine.register_strategy(
        _UnlimitedStrategy(
            "search",
            [
                DiscoveredContent(
                    bvid="BV1INTA",
                    title="中东局势 A",
                    relevance_score=0.96,
                    source_strategy="search",
                    topic_key="国际时事:地缘政治",
                ),
                DiscoveredContent(
                    bvid="BV1INTB",
                    title="中东局势 B",
                    relevance_score=0.95,
                    source_strategy="related_chain",
                    topic_key="国际时事:地缘政治",
                ),
                DiscoveredContent(
                    bvid="BV1AI",
                    title="模型能力边界",
                    relevance_score=0.9,
                    source_strategy="search",
                    topic_key="AI:大模型",
                ),
                DiscoveredContent(
                    bvid="BV1DOC",
                    title="城市纪录片",
                    relevance_score=0.89,
                    source_strategy="explore",
                    topic_key="纪录片:城市",
                ),
            ],
        )
    )

    results = await engine.discover(_build_profile(), limit=3)

    assert results[0].bvid == "BV1INTA"
    assert {item.bvid for item in results} == {"BV1INTA", "BV1AI", "BV1DOC"}


@pytest.mark.asyncio
async def test_discovery_engine_limits_explore_dominance_in_pool() -> None:
    class _UnlimitedStrategy(_RecordingStrategy):
        async def discover(self, profile: SoulProfile, limit: int = 20) -> list[DiscoveredContent]:
            self._started.append(self._name)
            return list(self._result)

    engine = ContentDiscoveryEngine()
    engine.register_strategy(
        _UnlimitedStrategy(
            "search",
            [
                DiscoveredContent(
                    bvid="BV1SEARCH",
                    title="搜索补进",
                    relevance_score=0.9,
                    source_strategy="search",
                    topic_key="搜索:1",
                    style_key="practical_guide",
                ),
                DiscoveredContent(
                    bvid="BV1TREND",
                    title="热榜补进",
                    relevance_score=0.89,
                    source_strategy="trending",
                    topic_key="热榜:1",
                    style_key="news_brief",
                ),
                DiscoveredContent(
                    bvid="BV1EXP1",
                    title="探索一",
                    relevance_score=0.96,
                    source_strategy="explore",
                    topic_key="探索:1",
                    style_key="story_doc",
                ),
                DiscoveredContent(
                    bvid="BV1EXP2",
                    title="探索二",
                    relevance_score=0.95,
                    source_strategy="explore",
                    topic_key="探索:2",
                    style_key="deep_dive",
                ),
                DiscoveredContent(
                    bvid="BV1EXP3",
                    title="探索三",
                    relevance_score=0.94,
                    source_strategy="explore",
                    topic_key="探索:3",
                    style_key="light_chat",
                ),
            ],
        )
    )

    results = await engine.discover(_build_profile(), limit=4)

    picked_sources = [item.source_strategy for item in results]

    assert picked_sources.count("explore") <= 2
    assert "search" in picked_sources
    assert "trending" in picked_sources


@pytest.mark.asyncio
async def test_discovery_engine_limits_source_and_style_dominance_for_larger_pool() -> None:
    class _UnlimitedStrategy(_RecordingStrategy):
        async def discover(self, profile: SoulProfile, limit: int = 20) -> list[DiscoveredContent]:
            self._started.append(self._name)
            return list(self._result)

    engine = ContentDiscoveryEngine()
    engine.register_strategy(
        _UnlimitedStrategy(
            "mixed",
            [
                DiscoveredContent(
                    bvid="BVEXP1",
                    title="探索纪录片 1",
                    relevance_score=0.99,
                    source_strategy="explore",
                    topic_key="探索:1",
                    style_key="story_doc",
                ),
                DiscoveredContent(
                    bvid="BVEXP2",
                    title="探索深挖 2",
                    relevance_score=0.98,
                    source_strategy="explore",
                    topic_key="探索:2",
                    style_key="deep_dive",
                ),
                DiscoveredContent(
                    bvid="BVEXP3",
                    title="探索轻聊 3",
                    relevance_score=0.97,
                    source_strategy="explore",
                    topic_key="探索:3",
                    style_key="light_chat",
                ),
                DiscoveredContent(
                    bvid="BVEXP4",
                    title="探索攻略 4",
                    relevance_score=0.96,
                    source_strategy="explore",
                    topic_key="探索:4",
                    style_key="practical_guide",
                ),
                DiscoveredContent(
                    bvid="BVREL1",
                    title="相关推荐机制拆解 1",
                    relevance_score=0.95,
                    source_strategy="related_chain",
                    topic_key="相关:1",
                    style_key="game_strategy",
                ),
                DiscoveredContent(
                    bvid="BVREL2",
                    title="相关推荐机制拆解 2",
                    relevance_score=0.94,
                    source_strategy="related_chain",
                    topic_key="相关:2",
                    style_key="game_strategy",
                ),
                DiscoveredContent(
                    bvid="BVREL3",
                    title="相关推荐故事向 3",
                    relevance_score=0.935,
                    source_strategy="related_chain",
                    topic_key="相关:3",
                    style_key="light_chat",
                ),
                DiscoveredContent(
                    bvid="BVSEA1",
                    title="搜索教程 1",
                    relevance_score=0.93,
                    source_strategy="search",
                    topic_key="搜索:1",
                    style_key="practical_guide",
                ),
                DiscoveredContent(
                    bvid="BVSEA2",
                    title="搜索快讯 2",
                    relevance_score=0.92,
                    source_strategy="search",
                    topic_key="搜索:2",
                    style_key="news_brief",
                ),
                DiscoveredContent(
                    bvid="BVTR1",
                    title="热榜纪录片 1",
                    relevance_score=0.91,
                    source_strategy="trending",
                    topic_key="热榜:1",
                    style_key="story_doc",
                ),
                DiscoveredContent(
                    bvid="BVTR2",
                    title="热榜视觉 2",
                    relevance_score=0.9,
                    source_strategy="trending",
                    topic_key="热榜:2",
                    style_key="visual_showcase",
                ),
            ],
        )
    )

    results = await engine.discover(_build_profile(), limit=10)

    picked_sources = [item.source_strategy for item in results]
    picked_styles = [item.style_key for item in results]

    assert picked_sources.count("explore") <= 3
    assert picked_sources.count("related_chain") <= 3
    assert len(results) == 10
    assert picked_styles.count("game_strategy") <= 3


def test_infer_style_key_classifies_hard_courses_and_documentaries() -> None:
    assert (
        ContentDiscoveryEngine.infer_style_key(
            title="【强化学习的数学原理】课程：从零开始到透彻理解",
            source_strategy="explore",
        )
        == "hands_on"
    )
    assert (
        ContentDiscoveryEngine.infer_style_key(
            title="精密加工的磨床纪录片",
            source_strategy="explore",
        )
        == "story_immersion"
    )
    assert (
        ContentDiscoveryEngine.infer_style_key(
            title="CPU芯片经显微镜放大到纳米级别",
            source_strategy="explore",
        )
        == "deep_focus"
    )
    assert (
        ContentDiscoveryEngine.infer_style_key(
            title="钛制造全过程，一般人没见过，工艺难度超乎你的想象",
            source_strategy="explore",
        )
        == "story_immersion"
    )
    assert (
        ContentDiscoveryEngine.infer_style_key(
            title="【从零看懂fsf】世界观/伪从者设定解析",
            source_strategy="explore",
        )
        == "deep_focus"
    )
    assert (
        ContentDiscoveryEngine.infer_style_key(
            title="囚犯盒子问题，史上最烧脑的逻辑谜题，超乎你的想象！",
            source_strategy="explore",
        )
        == "curiosity_spark"
    )


def test_infer_style_key_classifies_viewing_mode_examples() -> None:
    cases = [
        ("最新局势快讯，三分钟看懂发生了什么", "quick_scan"),
        ("耳机购买前必看，五款横向测评", "decision_support"),
        ("老友访谈：聊聊最近的创作状态", "social_chat"),
        ("下班后的日常 vlog，一起做饭收拾房间", "daily_wander"),
        ("高能整活名场面合集", "mood_release"),
        ("城市雨夜空镜混剪", "aesthetic_browse"),
        ("专注学习背景音乐 白噪音 两小时", "ambient_companion"),
        ("演唱会 live 现场高光", "live_pulse"),
        ("你知道吗？这些冷知识很反直觉", "curiosity_spark"),
    ]

    for title, expected in cases:
        assert ContentDiscoveryEngine.infer_style_key(title=title) == expected


@pytest.mark.asyncio
async def test_discovery_engine_keeps_non_explore_sources_when_style_repeats() -> None:
    class _UnlimitedStrategy(_RecordingStrategy):
        async def discover(self, profile: SoulProfile, limit: int = 20) -> list[DiscoveredContent]:
            self._started.append(self._name)
            return list(self._result)

    engine = ContentDiscoveryEngine()
    engine.register_strategy(
        _UnlimitedStrategy(
            "mixed",
            [
                DiscoveredContent(
                    bvid="BVEXP1",
                    title="探索深挖 1",
                    relevance_score=0.99,
                    source_strategy="explore",
                    topic_key="探索:1",
                    style_key="deep_dive",
                ),
                DiscoveredContent(
                    bvid="BVEXP2",
                    title="探索深挖 2",
                    relevance_score=0.98,
                    source_strategy="explore",
                    topic_key="探索:2",
                    style_key="story_doc",
                ),
                DiscoveredContent(
                    bvid="BVEXP3",
                    title="探索深挖 3",
                    relevance_score=0.97,
                    source_strategy="explore",
                    topic_key="探索:3",
                    style_key="visual_showcase",
                ),
                DiscoveredContent(
                    bvid="BVSEA1",
                    title="搜索杂谈 1",
                    relevance_score=0.96,
                    source_strategy="search",
                    topic_key="搜索:1",
                    style_key="light_chat",
                ),
                DiscoveredContent(
                    bvid="BVTR1",
                    title="热榜杂谈 1",
                    relevance_score=0.95,
                    source_strategy="trending",
                    topic_key="热榜:1",
                    style_key="light_chat",
                ),
            ],
        )
    )

    results = await engine.discover(_build_profile(), limit=3)

    picked_sources = [item.source_strategy for item in results]

    assert "search" in picked_sources
    assert "trending" in picked_sources
    assert picked_sources.count("explore") <= 1


@pytest.mark.asyncio
async def test_discovery_engine_caches_final_results() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()

        engine = ContentDiscoveryEngine(database=db)
        engine.register_strategy(
            _RecordingStrategy(
                "search",
                [
                    DiscoveredContent(
                        bvid="BV1A",
                        title="缓存内容 A",
                        up_name="UPA",
                        relevance_score=0.88,
                        source_strategy="search",
                    ),
                    DiscoveredContent(
                        bvid="BV1B",
                        title="缓存内容 B",
                        up_name="UPB",
                        relevance_score=0.74,
                        source_strategy="explore",
                    ),
                ],
            )
        )

        results = await engine.discover(_build_profile(), limit=20)
        cached = db.get_cached_content(limit=10)

        assert [item.bvid for item in results] == ["BV1A", "BV1B"]
        assert [item["bvid"] for item in cached] == ["BV1A", "BV1B"]
        assert cached[0]["source"] == "search"


@pytest.mark.asyncio
async def test_bilibili_discovery_cache_backfill_never_leaks_other_platforms() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        db.cache_content(
            "reddit:high-score",
            content_id="high-score",
            content_url="https://www.reddit.com/r/test/comments/high-score",
            title="Reddit cached item",
            source="reddit-hot",
            source_platform="reddit",
            relevance_score=0.99,
        )
        db.cache_content(
            "BV1ONLY",
            title="Bilibili cached item",
            source="trending",
            source_platform="bilibili",
            relevance_score=0.80,
        )

        engine = ContentDiscoveryEngine(database=db, target_primary_count=2)
        engine.register_strategy(_RecordingStrategy("trending", []))

        results = await engine.discover(
            _build_profile(),
            strategies=["trending"],
            limit=2,
        )

        assert [item.bvid for item in results] == ["BV1ONLY"]
        assert {item.source_platform for item in results} == {"bilibili"}


@pytest.mark.asyncio
async def test_discovery_engine_cache_results_preserves_multi_source_fields() -> None:
    """Regression: rescoring xhs rows must not overwrite source_platform.

    Previously `_cache_results` dropped `source_platform` / `content_id` /
    `content_url` on the cache_content call, so the upsert reverted xhs
    rows to the `bilibili` default — producing rows labeled with
    `source_platform='bilibili'` even though their bvid was an xhs note id.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()

        engine = ContentDiscoveryEngine(database=db)
        engine.register_strategy(
            _RecordingStrategy(
                "search",
                [
                    DiscoveredContent(
                        bvid="6613e9ac000000001a015e65",
                        title="鸡煲复刻",
                        up_name="作者A",
                        relevance_score=0.7,
                        source_strategy="xhs-extension-task",
                        content_id="6613e9ac000000001a015e65",
                        content_url="https://www.xiaohongshu.com/explore/6613e9ac000000001a015e65",
                        source_platform="xiaohongshu",
                    )
                ],
            )
        )

        await engine.discover(_build_profile(), limit=20)

        row = db.conn.execute(
            "SELECT source, source_platform, content_id, content_url "
            "FROM content_cache WHERE item_key=?",
            ("xiaohongshu:6613e9ac000000001a015e65",),
        ).fetchone()
        assert row is not None
        assert row["source_platform"] == "xiaohongshu"
        assert row["source"] == "xhs-extension-task"
        assert row["content_id"] == "6613e9ac000000001a015e65"
        assert row["content_url"].endswith("/6613e9ac000000001a015e65")


def test_merge_duplicates_uses_multi_source_content_identity() -> None:
    first = DiscoveredContent(
        content_id="yt-a",
        source_platform="youtube",
        title="YouTube A",
        relevance_score=0.6,
    )
    second = DiscoveredContent(
        content_id="yt-b",
        source_platform="youtube",
        title="YouTube B",
        relevance_score=0.5,
    )
    duplicate = DiscoveredContent(
        content_id="yt-a",
        source_platform="youtube",
        title="YouTube A better",
        relevance_score=0.9,
    )

    merged = ContentDiscoveryEngine._merge_duplicates([first, second, duplicate])

    assert [item.content_id for item in merged] == ["yt-a", "yt-b"]
    assert merged[0].title == "YouTube A better"


@pytest.mark.asyncio
async def test_discovery_engine_cache_results_preserves_relevance_fields() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()

        engine = ContentDiscoveryEngine(database=db)
        engine.register_strategy(
            _RecordingStrategy(
                "search",
                [
                    DiscoveredContent(
                        bvid="BV1A",
                        title="缓存内容 A",
                        up_name="UPA",
                        relevance_score=0.88,
                        relevance_reason="fits profile",
                        source_strategy="search",
                    )
                ],
            )
        )

        await engine.discover(_build_profile(), limit=20)
        cached = db.get_cached_content(limit=1)

        assert cached[0]["relevance_score"] == 0.88
        assert cached[0]["relevance_reason"] == "fits profile"
        assert cached[0]["candidate_tier"] == "primary"


@pytest.mark.asyncio
async def test_discovery_engine_backfills_when_primary_results_too_few() -> None:
    started: list[str] = []
    backfill_started: list[str] = []
    engine = ContentDiscoveryEngine()
    engine.register_strategy(
        _BackfillAwareStrategy(
            "search",
            [
                DiscoveredContent(
                    bvid="BV1PRIMARY",
                    title="主候选",
                    relevance_score=0.91,
                    candidate_tier="primary",
                    source_strategy="search",
                )
            ],
            backfill_result=[
                DiscoveredContent(
                    bvid="BV1BACK1",
                    title="补货 1",
                    relevance_score=0.73,
                    candidate_tier="backfill",
                    source_strategy="search",
                ),
                DiscoveredContent(
                    bvid="BV1BACK2",
                    title="补货 2",
                    relevance_score=0.68,
                    candidate_tier="backfill",
                    source_strategy="search",
                ),
            ],
            started=started,
            backfill_started=backfill_started,
        )
    )

    results = await engine.discover(_build_profile(), limit=18)

    assert started == ["search"]
    assert backfill_started == ["search-backfill"]
    assert [item.bvid for item in results] == ["BV1PRIMARY", "BV1BACK1", "BV1BACK2"]
    assert [item.candidate_tier for item in results] == ["primary", "backfill", "backfill"]


@pytest.mark.asyncio
async def test_discovery_engine_skips_backfill_when_primary_results_enough() -> None:
    started: list[str] = []
    backfill_started: list[str] = []
    engine = ContentDiscoveryEngine()
    primary_results = [
        DiscoveredContent(
            bvid=f"BV1{index:02d}",
            title=f"主候选 {index}",
            relevance_score=0.95 - index * 0.01,
            candidate_tier="primary",
            source_strategy="search",
        )
        for index in range(25)
    ]
    engine.register_strategy(
        _BackfillAwareStrategy(
            "search",
            primary_results,
            backfill_result=[
                DiscoveredContent(
                    bvid="BV1BACK",
                    title="补货",
                    relevance_score=0.5,
                    candidate_tier="backfill",
                    source_strategy="search",
                )
            ],
            started=started,
            backfill_started=backfill_started,
        )
    )

    results = await engine.discover(_build_profile(), limit=40)

    assert started == ["search"]
    assert backfill_started == []
    assert len(results) == 25
    assert all(item.candidate_tier == "primary" for item in results)


@pytest.mark.asyncio
async def test_discovery_engine_limits_llm_evaluation_concurrency() -> None:
    llm_service = _SlowLLMService(delay=0.02)
    engine = ContentDiscoveryEngine(
        llm_service=llm_service,
        concurrency=DiscoveryConcurrencyController(
            bilibili_request_concurrency=2,
            llm_evaluation_concurrency=2,
        ),
    )

    items = [
        DiscoveredContent(
            bvid=f"BV{i}",
            title=f"title-{i}",
            up_name=f"up-{i}",
            description="desc",
            source_strategy="test",
        )
        for i in range(4)
    ]

    await asyncio.gather(*(engine.evaluate_content(item, _build_profile()) for item in items))

    assert llm_service.max_active_calls == 2


@pytest.mark.asyncio
async def test_evaluate_batch_accepts_fenced_json_without_single_eval_fallback() -> None:
    """Batch responses wrapped in ```json fences should not explode into N calls."""

    class _FencedBatchLLMService:
        def __init__(self) -> None:
            self.calls = 0

        async def complete_structured_task(
            self,
            *,
            system_instruction: str,
            user_input: str,
            history: list[dict[str, str]] | None = None,
            temperature: float = 0.7,
            max_tokens: int = 4096,
            caller: str = "",
            reasoning_effort: str | None = None,
        ) -> object:
            self.calls += 1
            return _SlowResponse(
                """```json
[
  {"score": 0.82, "reason": "ok", "style_key": "deep_dive"},
  {"score": 0.76, "reason": "ok", "style_key": "story_doc"}
]
```"""
            )

    llm_service = _FencedBatchLLMService()
    engine = ContentDiscoveryEngine(
        llm_service=llm_service,
        evaluation_candidate_transport="production",
    )
    batch = [
        DiscoveredContent(bvid="BVF1", title="t1", up_name="u1", source_strategy="trending"),
        DiscoveredContent(bvid="BVF2", title="t2", up_name="u2", source_strategy="trending"),
    ]

    scores = await engine._evaluate_batch(batch, _build_profile())

    assert scores == [0.82, 0.76]
    assert llm_service.calls == 1


@pytest.mark.asyncio
async def test_evaluate_batch_propagates_provider_cooldown_without_single_fallback() -> None:
    class _CooldownLLMService:
        def __init__(self) -> None:
            self.calls = 0

        async def complete_structured_task(
            self,
            *,
            system_instruction: str,
            user_input: str,
            history: list[dict[str, str]] | None = None,
            temperature: float = 0.7,
            max_tokens: int = 4096,
            caller: str = "",
            reasoning_effort: str | None = None,
        ) -> object:
            self.calls += 1
            raise LLMProviderExecutionError(
                "All providers failed (gemini). Last error: "
                "Provider gemini is cooling down after rate limit."
            )

    llm_service = _CooldownLLMService()
    engine = ContentDiscoveryEngine(llm_service=llm_service)
    batch = [
        DiscoveredContent(bvid="BV_COOL_A", title="A", source_strategy="trending"),
        DiscoveredContent(bvid="BV_COOL_B", title="B", source_strategy="trending"),
    ]

    with pytest.raises(LLMProviderExecutionError):
        await engine._evaluate_batch(batch, _build_profile())

    assert llm_service.calls == 1


@pytest.mark.asyncio
async def test_evaluate_content_batch_splits_once_after_first_parse_failure() -> None:
    llm_service = _SplitRetryBatchLLMService(invalid_batch_calls={1})
    engine = ContentDiscoveryEngine(
        llm_service=llm_service,
        evaluation_candidate_transport="production",
    )
    contents = _split_retry_contents(45, prefix="SPLIT")

    scores = await engine.evaluate_content_batch(contents, _build_profile(), batch_size=45)

    assert llm_service.batch_call_sizes == [45, 22, 23]
    assert llm_service.single_calls == 0
    assert scores == [0.73] * 45
    assert all(content.relevance_score == 0.73 for content in contents)


@pytest.mark.asyncio
async def test_evaluate_content_batch_persistent_parse_failure_bounded_retry_zeroes() -> None:
    llm_service = _SplitRetryBatchLLMService(invalid_all_batches=True)
    engine = ContentDiscoveryEngine(
        llm_service=llm_service,
        evaluation_candidate_transport="production",
    )
    contents = _split_retry_contents(16, prefix="FAIL")

    scores = await engine.evaluate_content_batch(contents, _build_profile(), batch_size=16)

    # Missing-member retry is bounded (1 initial + max_extra_requests) and
    # never degrades into a per-item single-call storm; exhausted members
    # settle at 0.0 with an explicit reason.
    assert llm_service.batch_call_sizes[0] == 16
    assert len(llm_service.batch_call_sizes) == 7
    assert llm_service.single_calls == 0
    assert scores == [0.0] * 16
    assert all(c.relevance_reason == "evaluation_response_missing" for c in contents)


@pytest.mark.asyncio
async def test_evaluate_content_batch_rate_limit_propagates_without_split_retry() -> None:
    llm_service = _SplitRetryBatchLLMService(rate_limit_batch_calls={1})
    engine = ContentDiscoveryEngine(
        llm_service=llm_service,
        evaluation_candidate_transport="production",
    )
    contents = _split_retry_contents(16, prefix="LIMIT")

    with pytest.raises(LLMProviderExecutionError):
        await engine.evaluate_content_batch(contents, _build_profile(), batch_size=16)

    assert llm_service.batch_call_sizes == [16]
    assert llm_service.single_calls == 0


@pytest.mark.asyncio
async def test_evaluate_content_batch_splits_idless_count_mismatch() -> None:
    llm_service = _SplitRetryBatchLLMService(count_mismatch_batch_calls={1})
    engine = ContentDiscoveryEngine(
        llm_service=llm_service,
        evaluation_candidate_transport="production",
    )
    contents = _split_retry_contents(45, prefix="COUNT")

    scores = await engine.evaluate_content_batch(contents, _build_profile(), batch_size=45)

    assert llm_service.batch_call_sizes == [45, 22, 23]
    assert llm_service.single_calls == 0
    assert scores == [0.73] * 45


@pytest.mark.asyncio
async def test_evaluate_batch_ignores_echoed_prompt_before_result_array() -> None:
    """Some JSON-mode providers echo input JSON before the actual scored array."""

    class _EchoThenResultLLMService:
        def __init__(self) -> None:
            self.calls = 0

        async def complete_structured_task(
            self,
            *,
            system_instruction: str,
            user_input: str,
            history: list[dict[str, str]] | None = None,
            temperature: float = 0.7,
            max_tokens: int = 4096,
            caller: str = "",
            reasoning_effort: str | None = None,
        ) -> object:
            self.calls += 1
            return _SlowResponse(
                """{
  "source_context": "trending",
  "content_batch": [
    {"title": "echoed input without score"}
  ]
}
```json
[
  {"score": 0.81, "reason": "ok", "style_key": "deep_dive"},
  {"score": 0.74, "reason": "ok", "style_key": "story_doc"}
]
```"""
            )

    llm_service = _EchoThenResultLLMService()
    engine = ContentDiscoveryEngine(
        llm_service=llm_service,
        evaluation_candidate_transport="production",
    )
    batch = [
        DiscoveredContent(bvid="BVE1", title="t1", up_name="u1", source_strategy="trending"),
        DiscoveredContent(bvid="BVE2", title="t2", up_name="u2", source_strategy="trending"),
    ]

    scores = await engine._evaluate_batch(batch, _build_profile())

    assert scores == [0.81, 0.74]
    assert llm_service.calls == 1


@pytest.mark.asyncio
async def test_evaluate_batch_matches_results_by_bvid_when_response_reorders() -> None:
    class _ReorderedBatchLLMService:
        async def complete_structured_task(
            self,
            *,
            system_instruction: str,
            user_input: str,
            history: list[dict[str, str]] | None = None,
            temperature: float = 0.7,
            max_tokens: int = 4096,
            caller: str = "",
            reasoning_effort: str | None = None,
        ) -> object:
            return _SlowResponse(
                json.dumps(
                    [
                        {
                            "bvid": "BV_EVAL_C",
                            "score": 0.33,
                            "reason": "C 自己的理由",
                            "topic_group": "C 类",
                            "style_key": "story_doc",
                        },
                        {
                            "bvid": "BV_EVAL_A",
                            "score": 0.71,
                            "reason": "A 自己的理由",
                            "topic_group": "A 类",
                            "style_key": "deep_dive",
                        },
                        {
                            "bvid": "BV_EVAL_B",
                            "score": 0.52,
                            "reason": "B 自己的理由",
                            "topic_group": "B 类",
                            "style_key": "light_chat",
                        },
                    ],
                    ensure_ascii=False,
                )
            )

    engine = ContentDiscoveryEngine(
        llm_service=_ReorderedBatchLLMService(),
        evaluation_candidate_transport="production",
    )
    batch = [
        DiscoveredContent(bvid="BV_EVAL_A", title="A 视频", source_strategy="trending"),
        DiscoveredContent(bvid="BV_EVAL_B", title="B 视频", source_strategy="trending"),
        DiscoveredContent(bvid="BV_EVAL_C", title="C 视频", source_strategy="trending"),
    ]

    scores = await engine._evaluate_batch(batch, _build_profile())

    assert scores == [0.71, 0.52, 0.33]
    assert batch[0].relevance_reason == "A 自己的理由"
    assert batch[0].topic_group == "A 类"
    assert batch[1].relevance_reason == "B 自己的理由"
    assert batch[1].topic_group == "B 类"
    assert batch[2].relevance_reason == ""
    assert batch[2].topic_group == "C 类"


@pytest.mark.asyncio
async def test_evaluate_batch_normalizes_reason_before_caching_it() -> None:
    """Runtime enforces the reason diet even when model output ignores the prompt."""

    class _EmptyReasonLLM:
        def __init__(self) -> None:
            self.calls = 0

        async def complete_structured_task(self, *, user_input: str, **kwargs: object) -> object:
            self.calls += 1
            return _SlowResponse(
                json.dumps(
                    [
                        # A low-score reason is discarded even when the model emits one.
                        {
                            "bvid": "BV_EMPTY",
                            "score": 0.31,
                            "reason": "这段低分诊断不应被保留",
                            "style_key": "deep_dive",
                        },
                        # missing reason key entirely — parser must still tolerate it
                        {"bvid": "BV_MISSING", "score": 0.2, "style_key": "deep_dive"},
                        # A high-score reason is stripped and capped at 30 code points.
                        {
                            "bvid": "BV_KEPT",
                            "score": 0.72,
                            "reason": "  " + ("长" * 35) + "  ",
                            "style_key": "deep_dive",
                        },
                    ],
                    ensure_ascii=False,
                )
            )

    llm = _EmptyReasonLLM()
    engine = ContentDiscoveryEngine(
        llm_service=llm,
        evaluation_candidate_transport="production",
    )
    batch = [
        DiscoveredContent(bvid="BV_EMPTY", title="空理由", source_strategy="trending"),
        DiscoveredContent(bvid="BV_MISSING", title="缺理由", source_strategy="trending"),
        DiscoveredContent(bvid="BV_KEPT", title="入池", source_strategy="trending"),
    ]
    profile = _build_profile()

    scores = await engine._evaluate_batch(batch, profile)

    assert scores == [0.31, 0.2, 0.72]
    assert batch[0].relevance_reason == ""
    assert batch[1].relevance_reason == ""
    assert batch[2].relevance_reason == "长" * 30
    assert llm.calls == 1

    # The empty/missing-reason items are valid eval cache entries — reading the
    # stored tuple back must yield an empty reason string (not a rejected or
    # coerced value), so a later ``evaluate_content_batch`` pass reuses them.
    profile_digest = engine._evaluation_profile_digest(profile)
    negative_digest = engine._negative_examples_digest(None)
    for content, expected_reason in (
        (batch[0], ""),
        (batch[1], ""),
        (batch[2], "长" * 30),
    ):
        cache_key = engine._batch_eval_cache_key(
            content,
            profile_digest=profile_digest,
            negative_digest=negative_digest,
        )
        cached = engine._get_eval_cache_entry(cache_key)
        assert cached is not None
        assert cached[1] == expected_reason


@pytest.mark.asyncio
async def test_evaluate_content_normalizes_reason_before_caching_it() -> None:
    profile = _build_profile()

    high_llm = FakeLLMService(
        json.dumps(
            {
                "score": 0.5,
                "reason": "  " + ("好" * 35) + "  ",
                "topic_group": "systems",
                "style_key": "deep_dive",
            },
            ensure_ascii=False,
        )
    )
    high_engine = ContentDiscoveryEngine(llm_service=high_llm, eval_prefilter_mode="off")

    def high_candidate() -> DiscoveredContent:
        return DiscoveredContent(
            bvid="BV_SINGLE_REASON_HIGH",
            title="高分理由边界",
            source_strategy="search",
        )

    first = high_candidate()
    cached_copy = high_candidate()
    assert await high_engine.evaluate_content(first, profile) == 0.5
    assert await high_engine.evaluate_content(cached_copy, _build_profile()) == 0.5
    assert first.relevance_reason == "好" * 30
    assert cached_copy.relevance_reason == "好" * 30
    assert len(high_llm.calls) == 1

    low_llm = FakeLLMService('{"score": 0.49, "reason": "模型不应保留这段低分诊断"}')
    low_engine = ContentDiscoveryEngine(llm_service=low_llm, eval_prefilter_mode="off")
    low = DiscoveredContent(
        bvid="BV_SINGLE_REASON_LOW",
        title="低分理由边界",
        source_strategy="search",
    )

    assert await low_engine.evaluate_content(low, profile) == 0.49
    assert low.relevance_reason == ""


@pytest.mark.asyncio
async def test_evaluate_batch_retries_only_missing_keyed_members() -> None:
    class _PartialLLM:
        def __init__(self) -> None:
            self.request_ids: list[tuple[str, ...]] = []

        async def complete_structured_task(self, *, user_input: str, **kwargs: object) -> object:
            raw = user_input.split("<content_batch>", 1)[1].split("</content_batch>", 1)[0]
            items = json.loads(raw.strip())
            ids = tuple(str(item["bvid"]) for item in items)
            self.request_ids.append(ids)
            returned = ids[:2] if len(self.request_ids) == 1 else ids
            return _SlowResponse(
                json.dumps(
                    [
                        {
                            "bvid": content_id,
                            "score": 0.8,
                            "reason": f"ok {content_id}",
                            "style_key": "deep_dive",
                        }
                        for content_id in returned
                    ]
                )
            )

    llm = _PartialLLM()
    engine = ContentDiscoveryEngine(
        llm_service=llm,
        evaluation_candidate_transport="production",
    )
    batch = [
        DiscoveredContent(bvid=key, title=key, source_strategy="trending")
        for key in ("A", "B", "C", "D")
    ]
    assert await engine._evaluate_batch(batch, _build_profile()) == [0.8] * 4
    assert llm.request_ids == [("A", "B", "C", "D"), ("C", "D")]


@pytest.mark.asyncio
async def test_evaluate_batch_retries_duplicate_key_without_overwriting_valid_sibling() -> None:
    class _DuplicateKeyLLM:
        def __init__(self) -> None:
            self.request_ids: list[tuple[str, ...]] = []

        async def complete_structured_task(self, *, user_input: str, **kwargs: object) -> object:
            raw = user_input.split("<content_batch>", 1)[1].split("</content_batch>", 1)[0]
            ids = tuple(str(item["bvid"]) for item in json.loads(raw.strip()))
            self.request_ids.append(ids)
            if len(self.request_ids) == 1:
                payload = [
                    {"bvid": "A", "score": 0.1, "reason": "bad first"},
                    {"bvid": "A", "score": 0.2, "reason": "bad last"},
                    {"bvid": "B", "score": 0.8, "reason": "B once"},
                ]
            else:
                payload = [{"bvid": "A", "score": 0.7, "reason": "A retry"}]
            return _SlowResponse(json.dumps(payload))

    llm = _DuplicateKeyLLM()
    engine = ContentDiscoveryEngine(
        llm_service=llm,
        evaluation_candidate_transport="production",
    )
    batch = [DiscoveredContent(bvid=key, title=key) for key in ("A", "B")]
    assert await engine._evaluate_batch(batch, _build_profile()) == [0.7, 0.8]
    assert llm.request_ids == [("A", "B"), ("A",)]
    assert batch[0].relevance_reason == "A retry"
    assert batch[1].relevance_reason == "B once"


@pytest.mark.asyncio
async def test_evaluate_batch_accepts_newline_delimited_json_objects() -> None:
    """Some providers return one scored JSON object per line instead of an array."""

    class _NdjsonBatchLLMService:
        def __init__(self) -> None:
            self.calls = 0

        async def complete_structured_task(
            self,
            *,
            system_instruction: str,
            user_input: str,
            history: list[dict[str, str]] | None = None,
            temperature: float = 0.7,
            max_tokens: int = 4096,
            caller: str = "",
            reasoning_effort: str | None = None,
        ) -> object:
            self.calls += 1
            return _SlowResponse(
                "\n".join(
                    [
                        '{"score": 0.71, "reason": "ok", "style_key": "practical_guide"}',
                        '{"score": 0.68, "reason": "ok", "style_key": "story_doc"}',
                    ]
                )
            )

    llm_service = _NdjsonBatchLLMService()
    engine = ContentDiscoveryEngine(
        llm_service=llm_service,
        evaluation_candidate_transport="production",
    )
    batch = [
        DiscoveredContent(bvid="BVN1", title="t1", up_name="u1", source_strategy="trending"),
        DiscoveredContent(bvid="BVN2", title="t2", up_name="u2", source_strategy="trending"),
    ]

    scores = await engine._evaluate_batch(batch, _build_profile())

    assert scores == [0.71, 0.68]
    assert llm_service.calls == 1


@pytest.mark.asyncio
async def test_evaluate_batch_accepts_identifier_keyed_object_response() -> None:
    """JSON-object providers may return a bvid-keyed result map instead of an array."""

    class _KeyedObjectBatchLLMService:
        def __init__(self) -> None:
            self.calls = 0

        async def complete_structured_task(
            self,
            *,
            system_instruction: str,
            user_input: str,
            history: list[dict[str, str]] | None = None,
            temperature: float = 0.7,
            max_tokens: int = 4096,
            caller: str = "",
            reasoning_effort: str | None = None,
        ) -> object:
            self.calls += 1
            return _SlowResponse(
                json.dumps(
                    {
                        "BVKEY2": {
                            "score": 0.42,
                            "reason": "B reason",
                            "style_key": "story_doc",
                        },
                        "BVKEY1": {
                            "score": 0.88,
                            "reason": "A reason",
                            "style_key": "practical_guide",
                        },
                    },
                    ensure_ascii=False,
                )
            )

    llm_service = _KeyedObjectBatchLLMService()
    engine = ContentDiscoveryEngine(
        llm_service=llm_service,
        evaluation_candidate_transport="production",
    )
    batch = [
        DiscoveredContent(bvid="BVKEY1", title="t1", up_name="u1", source_strategy="trending"),
        DiscoveredContent(bvid="BVKEY2", title="t2", up_name="u2", source_strategy="trending"),
    ]

    scores = await engine._evaluate_batch(batch, _build_profile())

    assert scores == [0.88, 0.42]
    assert batch[0].relevance_reason == "A reason"
    assert batch[1].relevance_reason == ""
    assert llm_service.calls == 1


@pytest.mark.asyncio
async def test_evaluate_batch_intra_batch_franchise_cap() -> None:
    """v0.3.50: same-franchise items beyond the cap get their scores zeroed.

    Reproduces the production trigger: a single eval batch returning 6
    张雪机车 entries (or 7 风犬少年的天空, etc.) used to all stay
    kept=30, flooding the pool with one franchise. Cap is 4 — the top
    4 by score survive, the rest are zeroed (so the caller's
    ``score > 0`` filter drops them from the kept list).
    """
    import json

    class _FrancheseClumpLLMService:
        def __init__(self) -> None:
            self.calls = 0

        async def complete_structured_task(
            self,
            *,
            system_instruction: str,
            user_input: str,
            history: list[dict[str, str]] | None = None,
            temperature: float = 0.7,
            max_tokens: int = 4096,
            caller: str = "",
            reasoning_effort: str | None = None,
        ) -> object:
            self.calls += 1
            # 6 items, all "张雪机车" franchise, with descending scores.
            results = [
                {
                    "score": 0.95 - i * 0.05,
                    "reason": "好看",
                    "topic_group": "机车",
                    "style_key": "review_roundup",
                    "franchise_key": "张雪机车",
                }
                for i in range(6)
            ]
            return _SlowResponse(json.dumps(results, ensure_ascii=False))

    llm = _FrancheseClumpLLMService()
    engine = ContentDiscoveryEngine(
        llm_service=llm,
        evaluation_candidate_transport="production",
    )

    def candidates() -> list[DiscoveredContent]:
        return [
            DiscoveredContent(
                bvid=f"BVZX{i}",
                title=f"张雪机车第{i}集",
                up_name="张雪机车",
                description="d",
                source_strategy="related_chain",
            )
            for i in range(6)
        ]

    batch = candidates()

    scores = await engine._evaluate_batch(batch, _build_profile())

    # Cap is 4 — top-4 scoring entries kept (>0), the rest zeroed.
    nonzero = [s for s in scores if s > 0]
    assert len(nonzero) == 4
    assert sum(1 for s in scores if s == 0.0) == 2
    # Zeroed entries must also have their content's relevance_score reset
    # so downstream code that reads the content directly gets the same answer.
    zero_indices = [i for i, s in enumerate(scores) if s == 0.0]
    for idx in zero_indices:
        assert batch[idx].relevance_score == 0.0
        assert batch[idx].relevance_reason == ""

    # Cache entries retain raw per-item model scores, then reapply the current
    # sibling-dependent cap. A full cache hit must not resurrect the overflow.
    cached_batch = candidates()
    cached_scores = await engine.evaluate_content_batch(
        cached_batch,
        _build_profile(),
        batch_size=6,
    )
    assert cached_scores == scores
    assert llm.calls == 1
    assert sum(score > 0 for score in cached_scores) == 4
    assert all(
        cached_batch[index].relevance_reason == ""
        for index, score in enumerate(cached_scores)
        if score == 0.0
    )


@pytest.mark.asyncio
async def test_evaluate_batch_reapplies_style_cap_on_full_cache_hit() -> None:
    class _StyleClumpLLMService:
        def __init__(self) -> None:
            self.calls = 0

        async def complete_structured_task(self, **_kwargs: object) -> object:
            self.calls += 1
            return _SlowResponse(
                json.dumps(
                    [
                        {
                            "score": 0.99 - index * 0.01,
                            "reason": "高分内部诊断",
                            "topic_group": "剧情",
                            "style_key": "story_immersion",
                            "franchise_key": "",
                        }
                        for index in range(10)
                    ],
                    ensure_ascii=False,
                )
            )

    llm = _StyleClumpLLMService()
    engine = ContentDiscoveryEngine(
        llm_service=llm,
        evaluation_candidate_transport="production",
    )

    def candidates() -> list[DiscoveredContent]:
        return [
            DiscoveredContent(
                bvid=f"BVSTYLE{index}",
                title=f"剧情内容 {index}",
                source_strategy="trending",
            )
            for index in range(10)
        ]

    first = candidates()
    first_scores = await engine.evaluate_content_batch(first, _build_profile(), batch_size=10)
    cached = candidates()
    cached_scores = await engine.evaluate_content_batch(
        cached,
        _build_profile(),
        batch_size=10,
    )

    assert sum(score > 0 for score in first_scores) == 8
    assert cached_scores == first_scores
    assert llm.calls == 1
    assert all(
        cached[index].relevance_reason == ""
        for index, score in enumerate(cached_scores)
        if score == 0.0
    )


def test_count_pool_by_franchise_returns_lowercased_groups(tmp_path: Path) -> None:
    """v0.3.50: pool-quota query groups + lowercases franchise_key."""
    from openbiliclaw.storage.database import Database

    db = Database(tmp_path / "fk.db")
    db.initialize()
    # Two items sharing a franchise (case-different), one with empty,
    # one with a different franchise.
    db.cache_content(
        bvid="BV1A",
        title="A",
        up_name="up",
        source_platform="bilibili",
        source="search",
        franchise_key="张雪机车",
        relevance_score=0.90,
    )
    db.cache_content(
        bvid="BV1B",
        title="B",
        up_name="up",
        source_platform="bilibili",
        source="search",
        franchise_key="张雪机车",  # exact match
        relevance_score=0.90,
    )
    db.cache_content(
        bvid="BV1C",
        title="C",
        up_name="up",
        source_platform="bilibili",
        source="search",
        franchise_key="风犬少年的天空",
        relevance_score=0.90,
    )
    db.cache_content(
        bvid="BV1D",
        title="D",
        up_name="up",
        source_platform="bilibili",
        source="search",
        franchise_key="",
        relevance_score=0.90,
    )

    counts = db.count_pool_by_franchise()
    assert counts.get("张雪机车") == 2
    assert counts.get("风犬少年的天空") == 1
    assert "" not in counts


# ----------------------------------------------------------------------
# v0.3.x eval-batch negative-anchors wiring.


class _StubNegativeExemplarsDatabase:
    """Minimal database stub for the negative-exemplars wiring tests."""

    def __init__(
        self,
        rows: list[dict[str, object]],
        *,
        latest_event_id: int = 1,
    ) -> None:
        self._rows = rows
        self._latest_event_id = latest_event_id
        self.query_calls = 0

    def get_latest_event_id(self) -> int:
        return self._latest_event_id

    def bump_latest_event_id(self) -> None:
        self._latest_event_id += 1

    def query_events(self, **kwargs: object) -> list[dict[str, object]]:
        self.query_calls += 1
        return list(self._rows)


def _negative_row(idx: int, title: str) -> dict[str, object]:
    from datetime import datetime

    return {
        "id": idx,
        "title": title,
        "inferred_satisfaction": "negative",
        "satisfaction_reason": "quick_exit",
        "created_at": datetime(2026, 5, 16, 12, 0, 0).isoformat(sep=" "),
    }


class _RecordingBatchLLMService:
    """Captures the user_input sent to the batch evaluator for assertions."""

    def __init__(
        self,
        response: str = '[{"score": 0.7, "reason": "ok", "style_key": "deep_dive"}]',
    ) -> None:
        self.response = response
        self.user_inputs: list[str] = []

    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        caller: str = "",
        reasoning_effort: str | None = None,
    ) -> object:
        self.user_inputs.append(user_input)
        return _SlowResponse(self.response)


class _RecordingBatchKwargsLLMService(_RecordingBatchLLMService):
    def __init__(
        self,
        response: str = '[{"score": 0.7, "reason": "ok", "style_key": "deep_dive"}]',
    ) -> None:
        super().__init__(response)
        self.call_kwargs: list[dict[str, object]] = []

    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        caller: str = "",
        reasoning_effort: str | None = None,
        **kwargs: object,
    ) -> object:
        self.call_kwargs.append(dict(kwargs))
        return await super().complete_structured_task(
            system_instruction=system_instruction,
            user_input=user_input,
            history=history,
            temperature=temperature,
            max_tokens=max_tokens,
            caller=caller,
            reasoning_effort=reasoning_effort,
        )


@pytest.mark.asyncio
async def test_sparse_evaluation_uses_private_local_ids_and_binds_reordered_results() -> None:
    llm = _RecordingBatchLLMService(
        response=json.dumps(
            [
                {
                    "id": "1",
                    "score": 0.83,
                    "reason": "second",
                    "topic_group": "life",
                    "style_key": "daily_wander",
                    "franchise_key": "",
                },
                {
                    "id": "0",
                    "score": 0.61,
                    "reason": "first",
                    "topic_group": "tech",
                    "style_key": "deep_focus",
                    "franchise_key": "",
                },
            ],
            ensure_ascii=False,
        )
    )
    engine = ContentDiscoveryEngine(llm_service=llm)
    assert engine.evaluation_candidate_transport == "sparse-json"
    assert engine.evaluation_candidate_transport == _DEFAULT_EVALUATION_CANDIDATE_TRANSPORT
    contents = [
        DiscoveredContent(
            bvid="BV-PRIVATE-A",
            content_id="GLOBAL-PRIVATE-A",
            content_url="https://example.com/private-a",
            title="First candidate",
            up_name="duplicate author A",
            author_name="author A",
            source_platform="bilibili",
            content_type="video",
            source_strategy="search",
        ),
        DiscoveredContent(
            content_id="GLOBAL-PRIVATE-B",
            content_url="https://example.com/private-b",
            title="Second candidate",
            author_name="author B",
            source_platform="twitter",
            content_type="thread",
            source_strategy="feed",
        ),
    ]

    scores = await engine._evaluate_batch(
        contents,
        _build_profile(),
        source_context="mixed",
        normal_cache_enabled=False,
        apply_batch_caps=False,
    )

    assert scores == [0.61, 0.83]
    assert contents[0].relevance_reason == "first"
    assert contents[1].relevance_reason == "second"
    envelope = _sparse_batch_prompt_envelope(llm.user_inputs[0])
    assert envelope["defaults"] == {"mode": "normal"}
    assert envelope["items"] == [
        {
            "author": "author A",
            "content_type": "video",
            "id": "0",
            "source_platform": "bilibili",
            "title": "First candidate",
        },
        {
            "author": "author B",
            "content_type": "thread",
            "id": "1",
            "source_platform": "twitter",
            "title": "Second candidate",
        },
    ]
    candidate_wire = json.dumps(envelope, ensure_ascii=False)
    for private_value in (
        "BV-PRIVATE-A",
        "GLOBAL-PRIVATE-A",
        "GLOBAL-PRIVATE-B",
        "https://",
        "content_url",
        "source_strategy",
        "up_name",
    ):
        assert private_value not in candidate_wire


@pytest.mark.asyncio
async def test_row_wire_evaluation_uses_the_same_canonical_local_id_contract() -> None:
    from openbiliclaw.llm.evaluation_wire import decode_evaluation_row_wire

    class _RowWireLLMService:
        def __init__(self) -> None:
            self.candidate_blocks: list[str] = []
            self.system_instructions: list[str] = []

        async def complete_structured_task(
            self,
            *,
            system_instruction: str,
            user_input: str,
            history: list[dict[str, str]] | None = None,
            temperature: float = 0.7,
            max_tokens: int = 4096,
            caller: str = "",
            reasoning_effort: str | None = None,
        ) -> object:
            candidate_block = (
                user_input.split("<content_batch>", 1)[1]
                .split(
                    "</content_batch>",
                    1,
                )[0]
                .removeprefix("\n\n")
                .removesuffix("\n\n")
            )
            envelope = decode_evaluation_row_wire(candidate_block)
            items = envelope["items"]
            assert isinstance(items, list)
            self.candidate_blocks.append(candidate_block)
            self.system_instructions.append(system_instruction)
            return _SlowResponse(
                json.dumps(
                    [
                        {
                            "id": str(index),
                            "score": 0.76,
                            "reason": "ok",
                            "topic_group": "tech",
                            "style_key": "deep_focus",
                            "franchise_key": "",
                        }
                        for index in range(len(items))
                    ]
                )
            )

    llm = _RowWireLLMService()
    engine = ContentDiscoveryEngine(
        llm_service=llm,
        evaluation_candidate_transport="row-wire-v1",
    )

    scores = await engine._evaluate_batch(
        [
            DiscoveredContent(
                content_id="GLOBAL-ROW-PRIVATE",
                content_url="https://example.com/private-row",
                title="row candidate",
                author_name="author",
                source_platform="twitter",
                content_type="thread",
                source_strategy="search",
            )
        ],
        _build_profile(),
        normal_cache_enabled=False,
        apply_batch_caps=False,
    )

    assert scores == [0.76]
    assert llm.candidate_blocks[0].startswith("ROW-WIRE-V1\n")
    assert "GLOBAL-ROW-PRIVATE" not in llm.candidate_blocks[0]
    assert "https://" not in llm.candidate_blocks[0]
    assert "ROW-WIRE-V1" in llm.system_instructions[0]


@pytest.mark.asyncio
async def test_sparse_evaluation_repairs_multi_member_results_without_ids() -> None:
    class _IdlessThenSingletonLLMService:
        def __init__(self) -> None:
            self.call_sizes: list[int] = []
            self.ids_by_call: list[list[str]] = []

        async def complete_structured_task(
            self,
            *,
            system_instruction: str,
            user_input: str,
            history: list[dict[str, str]] | None = None,
            temperature: float = 0.7,
            max_tokens: int = 4096,
            caller: str = "",
            reasoning_effort: str | None = None,
        ) -> object:
            envelope = _sparse_batch_prompt_envelope(user_input)
            items = envelope["items"]
            assert isinstance(items, list)
            self.call_sizes.append(len(items))
            self.ids_by_call.append([str(item["id"]) for item in items if isinstance(item, dict)])
            if len(items) > 1:
                # Equal-length positional-looking output must not bind.
                return _SlowResponse(
                    json.dumps(
                        [
                            {
                                "score": 0.99,
                                "reason": "wrong positional first",
                                "topic_group": "wrong",
                                "style_key": "deep_focus",
                                "franchise_key": "",
                            },
                            {
                                "score": 0.01,
                                "reason": "",
                                "topic_group": "wrong",
                                "style_key": "deep_focus",
                                "franchise_key": "",
                            },
                        ]
                    )
                )
            item = items[0]
            assert isinstance(item, dict)
            score = 0.62 if item["title"] == "first" else 0.84
            return _SlowResponse(
                json.dumps(
                    [
                        {
                            "id": "0",
                            "score": score,
                            "reason": "repaired",
                            "topic_group": "valid",
                            "style_key": "deep_focus",
                            "franchise_key": "",
                        }
                    ]
                )
            )

    llm = _IdlessThenSingletonLLMService()
    engine = ContentDiscoveryEngine(
        llm_service=llm,
        evaluation_candidate_transport="sparse-json",
    )

    scores = await engine._evaluate_batch(
        [
            DiscoveredContent(
                bvid="BVrepair0",
                title="first",
                up_name="u",
                source_strategy="search",
            ),
            DiscoveredContent(
                bvid="BVrepair1",
                title="second",
                up_name="u",
                source_strategy="search",
            ),
        ],
        _build_profile(),
        normal_cache_enabled=False,
        apply_batch_caps=False,
    )

    assert scores == [0.62, 0.84]
    assert llm.call_sizes == [2, 1, 1]
    assert llm.ids_by_call == [["0", "1"], ["0"], ["0"]]


def test_sparse_evaluation_is_the_v6_cache_default_with_explicit_rollback_seams() -> None:
    default_engine = ContentDiscoveryEngine()
    explicit_production_engine = ContentDiscoveryEngine(evaluation_candidate_transport="production")
    sparse_engine = ContentDiscoveryEngine(evaluation_candidate_transport="sparse-json")
    row_engine = ContentDiscoveryEngine(evaluation_candidate_transport="row-wire-v1")

    def content(
        *,
        bvid: str = "BVcache-transport",
        content_id: str = "",
        content_url: str = "https://example.com/a",
        cover_url: str = "https://example.com/cover-a",
        body_text: str = "body",
        view_count: int = 10,
        source_strategy: str = "search",
    ) -> DiscoveredContent:
        return DiscoveredContent(
            bvid=bvid,
            content_id=content_id,
            content_url=content_url,
            cover_url=cover_url,
            title="candidate",
            up_name="u",
            body_text=body_text,
            view_count=view_count,
            source_strategy=source_strategy,
        )

    kwargs = {
        "profile_digest": "profile",
        "negative_digest": "negative",
        "source_context": "",
    }

    default_key = default_engine._batch_eval_cache_key(content(), **kwargs)
    explicit_production_key = explicit_production_engine._batch_eval_cache_key(
        content(),
        **kwargs,
    )
    sparse_key = sparse_engine._batch_eval_cache_key(content(), **kwargs)
    row_key = row_engine._batch_eval_cache_key(content(), **kwargs)

    assert _DEFAULT_EVALUATION_CANDIDATE_TRANSPORT == "sparse-json"
    assert default_engine.evaluation_candidate_transport == "sparse-json"
    assert default_key == sparse_key
    assert default_key.startswith("content-eval-v6:batch:")
    assert default_key.endswith(":transport:sparse-json")
    assert explicit_production_key.startswith("content-eval-v6:batch:")
    assert ":transport:" not in explicit_production_key
    assert explicit_production_key != default_key
    assert row_key.endswith(":transport:row-wire-v1")
    assert sparse_key.removesuffix(":transport:sparse-json") == row_key.removesuffix(
        ":transport:row-wire-v1"
    )

    missing_attribute_engine = ContentDiscoveryEngine()
    del missing_attribute_engine.evaluation_candidate_transport
    assert missing_attribute_engine._batch_eval_cache_key(content(), **kwargs) == default_key

    single_key = default_engine._single_eval_cache_key(
        content(),
        profile_digest="profile",
    )
    assert single_key.startswith("content-eval-v6:single:")
    old_batch_key = default_key.replace("content-eval-v6:", "content-eval-v5:", 1)
    default_engine._set_eval_cache_entry(
        old_batch_key,
        (0.9, "old", "old", "deep_focus", ""),
    )
    assert default_engine._get_eval_cache_entry(default_key) is None

    changed_global_identity = content(
        bvid="BVdifferent-global-id",
        content_id="different-global-content-id",
    )
    assert default_engine._batch_eval_cache_key(changed_global_identity, **kwargs) == default_key
    assert row_engine._batch_eval_cache_key(changed_global_identity, **kwargs) == row_key
    assert (
        explicit_production_engine._batch_eval_cache_key(
            changed_global_identity,
            **kwargs,
        )
        != explicit_production_key
    )

    changed_runtime_urls_key = sparse_engine._batch_eval_cache_key(
        content(
            content_url="https://different.example/item",
            cover_url="https://different.example/cover",
        ),
        **kwargs,
    )
    assert changed_runtime_urls_key == sparse_key
    assert (
        sparse_engine._batch_eval_cache_key(
            content(body_text="different body"),
            **kwargs,
        )
        != sparse_key
    )
    assert (
        sparse_engine._batch_eval_cache_key(
            content(view_count=11),
            **kwargs,
        )
        != sparse_key
    )
    assert (
        sparse_engine._batch_eval_cache_key(
            content(source_strategy="explore"),
            **kwargs,
        )
        != sparse_key
    )


def test_sparse_normal_cache_requires_homogeneous_content_types() -> None:
    contents = [
        DiscoveredContent(
            bvid="BVtype0",
            title="video",
            content_type="video",
            source_strategy="search",
        ),
        DiscoveredContent(
            bvid="BVtype1",
            title="thread",
            content_type="thread",
            source_strategy="search",
        ),
    ]

    assert ContentDiscoveryEngine(
        evaluation_candidate_transport="production"
    )._batch_normal_cache_eligible(
        contents,
        source_context="",
    )
    assert not ContentDiscoveryEngine()._batch_normal_cache_eligible(
        contents,
        source_context="",
    )
    assert not ContentDiscoveryEngine(
        evaluation_candidate_transport="sparse-json"
    )._batch_normal_cache_eligible(contents, source_context="")
    assert not ContentDiscoveryEngine(
        evaluation_candidate_transport="row-wire-v1"
    )._batch_normal_cache_eligible(contents, source_context="")


def test_evaluation_candidate_transport_rejects_unknown_values() -> None:
    with pytest.raises(ValueError, match="unsupported evaluation candidate transport"):
        ContentDiscoveryEngine(evaluation_candidate_transport="row-wire-v0")


@pytest.mark.asyncio
async def test_explicit_production_rollback_sends_historical_platform_metadata() -> None:
    llm = _RecordingBatchLLMService(
        response=json.dumps(
            [
                {
                    "content_id": "BV1",
                    "score": 0.8,
                    "reason": "ok",
                    "topic_group": "tech",
                    "style_key": "deep_dive",
                },
                {
                    "content_id": "xhs1",
                    "score": 0.7,
                    "reason": "ok",
                    "topic_group": "life",
                    "style_key": "lifestyle",
                },
            ]
        )
    )
    engine = ContentDiscoveryEngine(
        llm_service=llm,
        evaluation_candidate_transport="production",
    )
    assert engine.evaluation_candidate_transport == "production"

    await engine._evaluate_batch(
        [
            DiscoveredContent(
                bvid="BV1",
                title="Bili",
                published_at="2026-08-02T08:00:00+00:00",
                source_platform="bilibili",
                source_strategy="search",
            ),
            DiscoveredContent(
                content_id="xhs1",
                title="XHS",
                published_at="",
                source_platform="xiaohongshu",
                source_strategy="xhs-extension-search",
                content_url="https://www.xiaohongshu.com/explore/xhs1",
            ),
        ],
        _build_profile(),
        source_context="mixed",
    )

    user = llm.user_inputs[-1]
    candidate_block = user.split("<content_batch>", 1)[1].split("</content_batch>", 1)[0]
    assert candidate_block.startswith("\n\n[\n  {")
    assert '"bvid": "BV1"' in candidate_block
    assert '"content_id": "xhs1"' in candidate_block
    assert '"defaults"' not in candidate_block
    assert '"source_platform": "bilibili"' in user
    assert '"source_platform": "xiaohongshu"' in user
    assert '"published_at": "2026-08-02T08:00:00+00:00"' in user
    assert '"published_at": ""' in user
    assert '"evaluated_at": "' in user
    assert '"source_strategy": "xhs-extension-search"' in user
    assert '"content_type": "note"' in user
    assert "<source_platform>\n\nmixed\n\n</source_platform>" in user


@pytest.mark.asyncio
async def test_evaluate_batch_requests_no_core_memory_injection_when_supported() -> None:
    llm = _RecordingBatchKwargsLLMService()
    engine = ContentDiscoveryEngine(llm_service=llm)

    await engine._evaluate_batch(
        [DiscoveredContent(bvid="BVx", title="候选", up_name="u", source_strategy="search")],
        _build_profile(),
    )

    assert llm.call_kwargs == [{"inject_core_memory": False}]


def _json_prompt_block(user_input: str, tag: str) -> dict[str, object]:
    block = user_input.split(f"<{tag}>", 1)[1].split(f"</{tag}>", 1)[0]
    parsed = json.loads(block.strip())
    assert isinstance(parsed, dict)
    return parsed


@pytest.mark.asyncio
async def test_evaluate_batch_uses_layered_profile_prompt_with_compacted_interests() -> None:
    llm = _RecordingBatchLLMService()
    engine = ContentDiscoveryEngine(llm_service=llm)
    profile = _build_profile()
    profile.preferences.interests = [
        InterestTag(name=f"兴趣{index}", category="测试", weight=1.0 - index / 1000)
        for index in range(110)
    ]

    await engine._evaluate_batch(
        [DiscoveredContent(bvid="BVx", title="候选", up_name="u", source_strategy="search")],
        profile,
    )

    user_input = llm.user_inputs[0]
    assert "<profile_summary>" not in user_input
    assert user_input.index("<profile_core>") < user_input.index("<profile_life_context>")
    assert user_input.index("<profile_life_context>") < user_input.index("<profile_interests>")
    assert user_input.index("<profile_interests>") < user_input.index("<profile_style_context>")
    assert user_input.index("<profile_style_context>") < user_input.index(
        "<profile_recent_context>"
    )
    assert user_input.index("<profile_recent_context>") < user_input.index("<content_batch>")

    profile_interests = _json_prompt_block(user_input, "profile_interests")
    interests = profile_interests["interests"]
    assert isinstance(interests, list)
    assert len(interests) == 48
    assert interests[-1]["name"] == "兴趣47"


@pytest.mark.asyncio
async def test_evaluate_batch_compact_json_minifies_profile_and_content_blocks() -> None:
    llm = _RecordingBatchLLMService()
    engine = ContentDiscoveryEngine(llm_service=llm, compact_evaluation_json=True)

    await engine._evaluate_batch(
        [
            DiscoveredContent(
                bvid="BVcompact",
                title="保留 标题 内部 空格",
                up_name="u",
                source_strategy="search",
            )
        ],
        _build_profile(),
    )

    user_input = llm.user_inputs[0]
    for tag in (
        "profile_core",
        "profile_life_context",
        "profile_interests",
        "profile_style_context",
        "profile_recent_context",
        "content_batch",
    ):
        start = f"<{tag}>\n\n"
        end = f"\n\n</{tag}>"
        block = user_input.split(start, 1)[1].split(end, 1)[0]
        assert json.loads(block) is not None
        assert "\n  " not in block

    assert "保留 标题 内部 空格" in user_input


@pytest.mark.asyncio
async def test_evaluate_batch_only_updates_changed_profile_layers() -> None:
    llm = _RecordingBatchLLMService()
    engine = ContentDiscoveryEngine(llm_service=llm)
    profile = _build_profile()

    await engine._evaluate_batch(
        [DiscoveredContent(bvid="BVx", title="候选", up_name="u", source_strategy="search")],
        profile,
    )
    profile.recent_awareness.append(
        AwarenessNote(
            date="2026-06-27",
            observation="最近只改变近期觉察",
            trend="短期上下文变化",
            emotion_guess="专注",
        )
    )
    await engine._evaluate_batch(
        [DiscoveredContent(bvid="BVy", title="候选2", up_name="u", source_strategy="search")],
        profile,
    )

    first_input, second_input = llm.user_inputs
    for stable_tag in (
        "profile_core",
        "profile_life_context",
        "profile_interests",
        "profile_style_context",
    ):
        assert _json_prompt_block(first_input, stable_tag) == _json_prompt_block(
            second_input,
            stable_tag,
        )
    assert _json_prompt_block(first_input, "profile_recent_context") != _json_prompt_block(
        second_input,
        "profile_recent_context",
    )

    assert engine.evaluation_profile_prompt_cache_stats()["profile_core"]["hits"] == 1
    assert engine.evaluation_profile_prompt_cache_stats()["profile_recent_context"]["misses"] == 2


@pytest.mark.asyncio
async def test_evaluate_batch_sends_metrics_and_tags() -> None:
    llm = _RecordingBatchLLMService()
    engine = ContentDiscoveryEngine(llm_service=llm)

    await engine._evaluate_batch(
        [
            DiscoveredContent(
                bvid="BV1metrics",
                title="Metrics",
                tags=["tag-a", "tag-b"],
                source_strategy="search",
                view_count=1000,
                like_count=100,
                favorite_count=90,
                collect_count=80,
                comment_count=70,
                share_count=60,
                danmaku_count=50,
                reply_count=40,
                retweet_count=30,
                bookmark_count=20,
            )
        ],
        _build_profile(),
    )

    item = _batch_prompt_items(llm.user_inputs[0])[0]
    assert item["tags"] == ["tag-a", "tag-b"]
    assert item["like_count"] == 100
    assert item["favorite_count"] == 90
    assert item["collect_count"] == 80
    assert item["comment_count"] == 70
    assert item["share_count"] == 60
    assert item["danmaku_count"] == 50
    assert "reply_count" not in item
    assert "retweet_count" not in item
    assert "bookmark_count" not in item


@pytest.mark.asyncio
async def test_evaluate_batch_includes_negative_exemplars_in_user_prompt() -> None:
    """When the event store has negative rows, the eval batch user
    message must include the <negative_examples> block."""
    db = _StubNegativeExemplarsDatabase(rows=[_negative_row(1, "震惊！我刚发现的神器")])
    llm = _RecordingBatchLLMService()
    engine = ContentDiscoveryEngine(llm_service=llm, database=db)
    batch = [DiscoveredContent(bvid="BVx", title="候选", up_name="u", source_strategy="search")]

    await engine._evaluate_batch(batch, _build_profile())

    assert llm.user_inputs, "LLM should have been called once"
    user = llm.user_inputs[0]
    assert "<negative_examples>" in user
    assert "震惊！我刚发现的神器" in user


@pytest.mark.asyncio
async def test_evaluate_batch_omits_block_with_no_negative_rows() -> None:
    db = _StubNegativeExemplarsDatabase(rows=[])
    llm = _RecordingBatchLLMService()
    engine = ContentDiscoveryEngine(llm_service=llm, database=db)
    batch = [DiscoveredContent(bvid="BVx", title="候选", up_name="u", source_strategy="search")]

    await engine._evaluate_batch(batch, _build_profile())

    user = llm.user_inputs[0]
    assert "<negative_examples>" not in user


@pytest.mark.asyncio
async def test_evaluate_batch_runs_when_exemplar_helper_raises() -> None:
    """Storage failure inside _get_negative_exemplars must not abort the batch."""

    class _BrokenDatabase:
        def get_latest_event_id(self) -> int:
            raise RuntimeError("database is locked")

        def query_events(self, **kwargs: object) -> list[dict[str, object]]:
            raise RuntimeError("database is locked")

    llm = _RecordingBatchLLMService()
    engine = ContentDiscoveryEngine(llm_service=llm, database=_BrokenDatabase())
    batch = [DiscoveredContent(bvid="BVx", title="候选", up_name="u", source_strategy="search")]

    scores = await engine._evaluate_batch(batch, _build_profile())

    assert scores == [0.7], "batch should still produce a score"
    assert "<negative_examples>" not in llm.user_inputs[0]


@pytest.mark.asyncio
async def test_evaluate_batch_caches_exemplars_across_back_to_back_calls() -> None:
    """Two batches with the same latest_event_id should share one query."""
    db = _StubNegativeExemplarsDatabase(rows=[_negative_row(1, "震惊！我刚发现的神器")])
    llm = _RecordingBatchLLMService()
    engine = ContentDiscoveryEngine(llm_service=llm, database=db)
    batch = [DiscoveredContent(bvid="BVx", title="候选", up_name="u", source_strategy="search")]

    await engine._evaluate_batch(batch, _build_profile())
    first_query_count = db.query_calls

    await engine._evaluate_batch(batch, _build_profile())
    assert db.query_calls == first_query_count, "cache hit, no second query"


@pytest.mark.asyncio
async def test_evaluate_batch_refreshes_exemplars_on_new_event_id() -> None:
    """A new negative classified row should bust the cache on the next batch."""
    db = _StubNegativeExemplarsDatabase(rows=[_negative_row(1, "震惊！我刚发现的神器")])
    llm = _RecordingBatchLLMService()
    engine = ContentDiscoveryEngine(llm_service=llm, database=db)
    batch = [DiscoveredContent(bvid="BVx", title="候选", up_name="u", source_strategy="search")]

    await engine._evaluate_batch(batch, _build_profile())
    db.bump_latest_event_id()
    await engine._evaluate_batch(batch, _build_profile())

    assert db.query_calls >= 2, "new event id must invalidate the cache"


@pytest.mark.asyncio
async def test_eval_cache_rechecks_content_when_negative_exemplars_change() -> None:
    """Cached relevance scores must not bypass newly available negative anchors."""
    db = _StubNegativeExemplarsDatabase(rows=[])
    llm = _RecordingBatchLLMService()
    engine = ContentDiscoveryEngine(llm_service=llm, database=db)
    profile = _build_profile()
    content = DiscoveredContent(bvid="BVx", title="候选", up_name="u", source_strategy="search")

    await engine.evaluate_content_batch([content], profile)
    assert len(llm.user_inputs) == 1
    assert "<negative_examples>" not in llm.user_inputs[0]

    db._rows = [_negative_row(1, "震惊！我刚发现的神器")]  # noqa: SLF001
    db.bump_latest_event_id()
    await engine.evaluate_content_batch([content], profile)

    assert len(llm.user_inputs) == 2, "negative-anchor revision must invalidate eval cache"
    assert "<negative_examples>" in llm.user_inputs[1]


@pytest.mark.asyncio
async def test_batch_eval_cache_rechecks_content_when_published_at_changes() -> None:
    db = _StubNegativeExemplarsDatabase(rows=[])
    llm = _RecordingBatchLLMService()
    engine = ContentDiscoveryEngine(llm_service=llm, database=db)
    profile = _build_profile()

    await engine.evaluate_content_batch(
        [DiscoveredContent(bvid="BVtime", title="模型更新", source_strategy="search")],
        profile,
    )
    await engine.evaluate_content_batch(
        [
            DiscoveredContent(
                bvid="BVtime",
                title="模型更新",
                published_at="2026-08-04T08:00:00Z",
                source_strategy="search",
            )
        ],
        profile,
    )

    assert len(llm.user_inputs) == 2


@pytest.mark.asyncio
async def test_batch_eval_cache_expires_on_next_evaluation_hour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clocks = iter(
        [
            ("2026-08-04T09:47:00Z", "2026-08-04T09:00:00Z"),
            ("2026-08-04T10:02:00Z", "2026-08-04T10:00:00Z"),
        ]
    )
    monkeypatch.setattr(prompt_module, "content_evaluation_clock", lambda: next(clocks))
    llm = _RecordingBatchLLMService()
    engine = ContentDiscoveryEngine(llm_service=llm)
    profile = _build_profile()
    content = DiscoveredContent(
        bvid="BVhour",
        title="滚动热点",
        published_at="2026-08-04T08:30:00Z",
        source_strategy="trending",
    )

    await engine.evaluate_content_batch([content], profile)
    await engine.evaluate_content_batch([content], profile)

    assert len(llm.user_inputs) == 2


@pytest.mark.asyncio
async def test_eval_cache_hits_for_equivalent_profile_objects() -> None:
    """Equivalent profile content should reuse the same local eval result.

    The cache key must not depend on Python object identity, or every profile
    reload/rebuild turns into a cache miss.
    """
    db = _StubNegativeExemplarsDatabase(rows=[])
    llm = _RecordingBatchLLMService()
    engine = ContentDiscoveryEngine(llm_service=llm, database=db)
    content = DiscoveredContent(
        bvid="BVstable",
        title="候选",
        up_name="u",
        source_strategy="search",
    )

    await engine.evaluate_content_batch([content], _build_profile())
    await engine.evaluate_content_batch(
        [
            DiscoveredContent(
                bvid="BVstable",
                title="候选",
                up_name="u",
                source_strategy="search",
            )
        ],
        _build_profile(),
    )

    assert len(llm.user_inputs) == 1


@pytest.mark.asyncio
async def test_eval_cache_survives_unrelated_event_id_change() -> None:
    """A non-negative event should not invalidate exact eval results.

    Negative exemplar content changes still invalidate the cache; merely moving
    the global event waterline should not.
    """
    db = _StubNegativeExemplarsDatabase(rows=[])
    llm = _RecordingBatchLLMService()
    engine = ContentDiscoveryEngine(llm_service=llm, database=db)
    profile = _build_profile()

    await engine.evaluate_content_batch(
        [DiscoveredContent(bvid="BVsame", title="候选", up_name="u", source_strategy="search")],
        profile,
    )
    db.bump_latest_event_id()
    await engine.evaluate_content_batch(
        [DiscoveredContent(bvid="BVsame", title="候选", up_name="u", source_strategy="search")],
        profile,
    )

    assert len(llm.user_inputs) == 1


def _eval_cache_value(label: str) -> tuple[float, str, str, str, str]:
    return (0.7, f"reason-{label}", "topic", "deep_dive", "")


def test_eval_cache_lru_cap_evicts_least_recently_used() -> None:
    engine = ContentDiscoveryEngine()
    cache_max = discovery_engine_module._EVAL_CACHE_MAX_ENTRIES

    for index in range(cache_max):
        engine._set_eval_cache_entry(f"key-{index}", _eval_cache_value(str(index)))
    engine._set_eval_cache_entry("overflow", _eval_cache_value("overflow"))

    assert len(engine._eval_cache) == cache_max
    assert engine._get_eval_cache_entry("key-0") is None
    assert engine._get_eval_cache_entry("key-1") == _eval_cache_value("1")
    assert engine._get_eval_cache_entry("overflow") == _eval_cache_value("overflow")


def test_eval_cache_get_refreshes_lru_recency() -> None:
    engine = ContentDiscoveryEngine()
    cache_max = discovery_engine_module._EVAL_CACHE_MAX_ENTRIES

    for index in range(cache_max):
        engine._set_eval_cache_entry(f"key-{index}", _eval_cache_value(str(index)))

    assert engine._get_eval_cache_entry("key-0") == _eval_cache_value("0")
    engine._set_eval_cache_entry("overflow", _eval_cache_value("overflow"))

    assert engine._get_eval_cache_entry("key-1") is None
    assert engine._get_eval_cache_entry("key-0") == _eval_cache_value("0")


async def test_embedding_prefilter_tolerates_engine_built_without_init() -> None:
    """E2E tests construct engines via ``__new__``; prefilter must not AttributeError."""
    engine = ContentDiscoveryEngine.__new__(ContentDiscoveryEngine)
    profile = _build_profile()
    content = DiscoveredContent(bvid="BVNOINIT", title="t", up_name="u", source_strategy="search")

    assert await engine._embedding_prefilter([content], profile) == {}


def test_eval_cache_tolerates_plain_dict_assignment() -> None:
    """Tests reset the cache with ``engine._eval_cache = {}``; LRU must self-heal."""
    engine = ContentDiscoveryEngine()
    engine._eval_cache = {"seed": _eval_cache_value("seed")}  # type: ignore[assignment]

    engine._set_eval_cache_entry("fresh", _eval_cache_value("fresh"))

    assert engine._get_eval_cache_entry("seed") == _eval_cache_value("seed")
    assert engine._get_eval_cache_entry("fresh") == _eval_cache_value("fresh")
    assert isinstance(engine._eval_cache, OrderedDict)


@pytest.mark.asyncio
async def test_eval_cache_reads_legacy_four_tuple_entries() -> None:
    db = _StubNegativeExemplarsDatabase(rows=[])
    llm = _RecordingBatchLLMService()
    engine = ContentDiscoveryEngine(llm_service=llm, database=db)
    profile = _build_profile()
    content = DiscoveredContent(
        bvid="BVlegacy",
        title="候选",
        up_name="u",
        source_strategy="search",
    )
    cache_key = engine._batch_eval_cache_key(
        content,
        profile_digest=engine._evaluation_profile_digest(profile),
        negative_digest=engine._negative_examples_digest(None),
    )
    engine._set_eval_cache_entry(cache_key, (0.77, "legacy reason", "legacy-topic", "deep_focus"))

    scores = await engine.evaluate_content_batch([content], profile)

    assert scores == [0.77]
    assert content.relevance_score == 0.77
    assert content.relevance_reason == "legacy reason"
    assert content.topic_group == "legacy-topic"
    assert content.style_key == "deep_focus"
    assert llm.user_inputs == []


@pytest.mark.asyncio
async def test_eval_cache_reads_legacy_five_tuple_entries_with_neutral_temporal_data() -> None:
    db = _StubNegativeExemplarsDatabase(rows=[])
    llm = _RecordingBatchLLMService()
    engine = ContentDiscoveryEngine(llm_service=llm, database=db)
    profile = _build_profile()
    content = DiscoveredContent(
        bvid="BVlegacy5",
        title="候选",
        up_name="u",
        source_strategy="search",
        temporal_class="current",
        temporal_confidence=0.9,
        temporal_reason="stale value",
    )
    cache_key = engine._batch_eval_cache_key(
        content,
        profile_digest=engine._evaluation_profile_digest(profile),
        negative_digest=engine._negative_examples_digest(None),
    )
    engine._set_eval_cache_entry(
        cache_key,
        (0.78, "legacy reason", "legacy-topic", "deep_focus", "legacy-franchise"),
    )

    scores = await engine.evaluate_content_batch([content], profile)

    assert scores == [0.78]
    assert content.franchise_key == "legacy-franchise"
    assert content.temporal_class == "unknown"
    assert content.temporal_confidence == 0.0
    assert content.temporal_reason == ""
    assert content.temporal_policy_version == "v2"
    assert content.temporal_evaluated is False
    assert llm.user_inputs == []


@pytest.mark.asyncio
async def test_single_evaluation_parses_and_caches_temporal_metadata() -> None:
    llm = FakeLLMService(
        json.dumps(
            {
                "score": 0.82,
                "reason": "match",
                "topic_group": "systems",
                "style_key": "deep_focus",
                "franchise_key": "",
                "temporal_class": "versioned",
                "temporal_confidence": 0.88,
                "temporal_reason": "内容依赖具体产品版本",
                "temporal_validity_mode": "version_state",
                "temporal_valid_until": "",
                "temporal_scope": "core",
                "temporal_evidence": "当前仍受支持",
                "temporal_state": "active",
                "temporal_policy_version": "model-owned-value-is-ignored",
            },
            ensure_ascii=False,
        )
    )
    engine = ContentDiscoveryEngine(llm_service=llm, eval_prefilter_mode="off")

    def candidate() -> DiscoveredContent:
        return DiscoveredContent(
            content_id="single-temporal",
            title="Python 3.8 当前仍受支持安装教程",
            source_platform="youtube",
            source_strategy="search",
        )

    cold = candidate()
    warm = candidate()
    assert await engine.evaluate_content(cold, _build_profile()) == 0.82
    assert await engine.evaluate_content(warm, _build_profile()) == 0.82

    for item in (cold, warm):
        assert item.temporal_class == "versioned"
        assert item.temporal_confidence == 0.88
        assert item.temporal_reason == "内容依赖具体产品版本"
        assert item.temporal_policy_version == "v2"
        assert item.temporal_validity_mode == "version_state"
        assert item.temporal_scope == "core"
        assert item.temporal_evidence == "当前仍受支持"
        assert item.temporal_state == "active"
        assert item.temporal_next_review_at
        assert item.temporal_evaluated_at
        assert item.temporal_evidence_complete is True
        assert item.temporal_evaluated is True
    assert len(llm.calls) == 1
    assert {len(entry) for entry in engine._eval_cache.values()} == {17}
    assert {entry[8] for entry in engine._eval_cache.values()} == {"v2"}


@pytest.mark.asyncio
async def test_explicit_deadline_is_grounded_scheduled_and_preserved_on_cache_hit() -> None:
    llm = FakeLLMService(
        json.dumps(
            {
                "score": 0.83,
                "reason": "match",
                "topic_group": "活动",
                "style_key": "social_chat",
                "franchise_key": "",
                "temporal_class": "breaking",
                "temporal_confidence": 0.93,
                "temporal_reason": "报名入口有明确截止时间",
                "temporal_validity_mode": "explicit_deadline",
                "temporal_valid_until": "2026-08-13T12:00:00Z",
                "temporal_scope": "core",
                "temporal_evidence": "报名截止 2026-08-13 20:00 +08:00",
                "temporal_state": "unknown",
            },
            ensure_ascii=False,
        )
    )
    engine = ContentDiscoveryEngine(llm_service=llm, eval_prefilter_mode="off")

    def candidate() -> DiscoveredContent:
        return DiscoveredContent(
            content_id="grounded-deadline",
            title="报名截止 2026-08-13 20:00 +08:00",
            source_platform="bilibili",
            source_strategy="search",
        )

    cold = candidate()
    warm = candidate()
    assert await engine.evaluate_content(cold, _build_profile()) == 0.83
    assert await engine.evaluate_content(warm, _build_profile()) == 0.83

    for item in (cold, warm):
        assert item.temporal_validity_mode == "explicit_deadline"
        assert item.temporal_valid_until == "2026-08-13T12:00:00Z"
        assert item.temporal_next_review_at == "2026-08-13T12:00:00Z"
        assert item.temporal_scope == "core"
        assert item.temporal_evidence == "报名截止 2026-08-13 20:00 +08:00"
        assert item.temporal_state == "unknown"
        assert item.temporal_evaluated_at
        assert item.temporal_evidence_complete is True
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_ungrounded_deadline_downgrades_before_entering_eval_cache() -> None:
    llm = FakeLLMService(
        json.dumps(
            {
                "score": 0.8,
                "reason": "match",
                "topic_group": "活动",
                "style_key": "social_chat",
                "franchise_key": "",
                "temporal_class": "breaking",
                "temporal_confidence": 0.91,
                "temporal_reason": "模型声称有限时入口",
                "temporal_validity_mode": "explicit_deadline",
                "temporal_valid_until": "2026-08-13T12:00:00Z",
                "temporal_scope": "core",
                "temporal_evidence": "报名截止 2026-08-13",
                "temporal_state": "unknown",
            },
            ensure_ascii=False,
        )
    )
    engine = ContentDiscoveryEngine(llm_service=llm, eval_prefilter_mode="off")
    content = DiscoveredContent(
        content_id="ungrounded-deadline",
        title="普通活动介绍",
        source_platform="bilibili",
        source_strategy="search",
    )

    assert await engine.evaluate_content(content, _build_profile()) == 0.8

    assert content.temporal_validity_mode == "freshness_only"
    assert content.temporal_valid_until == ""
    assert content.temporal_next_review_at
    assert content.temporal_evidence_complete is True
    cached = next(iter(engine._eval_cache.values()))
    assert cached[9] == "freshness_only"
    assert cached[10] == ""


@pytest.mark.asyncio
async def test_eval_cache_reads_legacy_nine_tuple_without_fabricating_v2_evidence() -> None:
    db = _StubNegativeExemplarsDatabase(rows=[])
    llm = _RecordingBatchLLMService()
    engine = ContentDiscoveryEngine(llm_service=llm, database=db)
    profile = _build_profile()
    content = DiscoveredContent(
        bvid="BVlegacy9",
        title="旧缓存候选",
        up_name="u",
        source_strategy="search",
    )
    cache_key = engine._batch_eval_cache_key(
        content,
        profile_digest=engine._evaluation_profile_digest(profile),
        negative_digest=engine._negative_examples_digest(None),
    )
    engine._set_eval_cache_entry(
        cache_key,
        (0.76, "legacy", "topic", "deep_focus", "", "current", 0.9, "近期", "v1"),
    )

    assert await engine.evaluate_content_batch([content], profile) == [0.76]
    assert content.temporal_class == "current"
    assert content.temporal_policy_version == "v1"
    assert content.temporal_validity_mode == "none"
    assert content.temporal_evidence == ""
    assert content.temporal_evidence_complete is False
    assert content.temporal_evaluated is False
    assert llm.user_inputs == []


@pytest.mark.asyncio
async def test_single_evaluation_keeps_valid_relevance_when_temporal_fields_are_missing() -> None:
    llm = FakeLLMService(
        json.dumps(
            {
                "score": 0.79,
                "reason": "match",
                "topic_group": "systems",
                "style_key": "deep_focus",
                "franchise_key": "",
            }
        )
    )
    engine = ContentDiscoveryEngine(llm_service=llm, eval_prefilter_mode="off")
    content = DiscoveredContent(
        content_id="single-temporal-missing",
        title="模型仍给出有效相关性",
        source_platform="twitter",
        source_strategy="search",
        temporal_class="current",
        temporal_confidence=0.9,
        temporal_reason="stale metadata must be cleared",
    )

    assert await engine.evaluate_content(content, _build_profile()) == 0.79
    assert content.relevance_reason == "match"
    assert content.temporal_class == "unknown"
    assert content.temporal_confidence == 0.0
    assert content.temporal_reason == ""
    assert content.temporal_policy_version == "v2"
    assert content.temporal_evaluated is False


@pytest.mark.asyncio
async def test_explicit_unknown_temporal_cache_is_neutral_on_cold_and_warm_paths() -> None:
    llm = FakeLLMService(
        json.dumps(
            {
                "score": 0.79,
                "reason": "match",
                "topic_group": "systems",
                "style_key": "deep_focus",
                "franchise_key": "",
                "temporal_class": "unknown",
                "temporal_confidence": 0.0,
                "temporal_reason": "",
                "temporal_validity_mode": "none",
                "temporal_valid_until": "",
                "temporal_scope": "none",
                "temporal_evidence": "",
                "temporal_state": "unknown",
            }
        )
    )
    engine = ContentDiscoveryEngine(llm_service=llm, eval_prefilter_mode="off")

    def candidate() -> DiscoveredContent:
        return DiscoveredContent(
            content_id="explicit-unknown-temporal",
            title="证据不足但相关性有效",
            source_platform="twitter",
            source_strategy="search",
        )

    cold = candidate()
    warm = candidate()
    assert await engine.evaluate_content(cold, _build_profile()) == 0.79
    assert await engine.evaluate_content(warm, _build_profile()) == 0.79
    assert cold.temporal_evaluated is False
    assert warm.temporal_evaluated is False
    assert len(llm.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["production", "sparse-json"])
async def test_batch_evaluation_keeps_valid_relevance_when_temporal_fields_are_invalid(
    transport: str,
) -> None:
    llm = _RecordingBatchLLMService(
        response=json.dumps(
            [
                {
                    "id": "0",
                    "score": 0.81,
                    "reason": "匹配用户兴趣",
                    "topic_group": "人工智能",
                    "style_key": "deep_focus",
                    "franchise_key": "",
                    "temporal_class": "current",
                    "temporal_confidence": "high",
                    "temporal_reason": "依赖近期语境",
                    "temporal_validity_mode": "freshness_only",
                    "temporal_valid_until": "",
                    "temporal_scope": "core",
                    "temporal_evidence": "最新",
                    "temporal_state": "unknown",
                }
            ],
            ensure_ascii=False,
        )
    )
    engine = ContentDiscoveryEngine(
        llm_service=llm,
        eval_prefilter_mode="off",
        evaluation_candidate_transport=transport,
    )
    content = DiscoveredContent(
        bvid="BVTEMPORAL",
        title="标题含最新但不能单独决定分类",
        source_strategy="trending",
    )

    scores = await engine.evaluate_content_batch([content], _build_profile())

    assert scores == [0.81]
    assert content.relevance_reason == "匹配用户兴趣"
    assert content.temporal_class == "unknown"
    assert content.temporal_confidence == 0.0
    assert content.temporal_reason == ""
    assert content.temporal_policy_version == "v2"
    assert content.temporal_evaluated is False


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["production", "sparse-json"])
async def test_batch_temporal_grounding_uses_only_prompt_visible_text(
    transport: str,
) -> None:
    evidence = "赛事已经结束"
    llm = _RecordingBatchLLMService(
        response=json.dumps(
            [
                {
                    "id": "0",
                    "score": 0.81,
                    "reason": "匹配用户兴趣",
                    "topic_group": "赛事",
                    "style_key": "deep_focus",
                    "franchise_key": "",
                    "temporal_class": "current",
                    "temporal_confidence": 0.95,
                    "temporal_reason": "赛事终态会使核心信息失效",
                    "temporal_validity_mode": "event_state",
                    "temporal_valid_until": "",
                    "temporal_scope": "core",
                    "temporal_evidence": evidence,
                    "temporal_state": "expired",
                }
            ],
            ensure_ascii=False,
        )
    )
    engine = ContentDiscoveryEngine(
        llm_service=llm,
        eval_prefilter_mode="off",
        evaluation_candidate_transport=transport,
    )
    # Batch prompts expose only the first 400 description characters. The
    # terminal phrase exists in storage but was never visible to the model.
    content = DiscoveredContent(
        bvid="BVPROMPTVISIBLE",
        title="普通赛事介绍",
        description=("甲" * 401) + evidence,
        source_strategy="search",
    )

    scores = await engine.evaluate_content_batch([content], _build_profile())

    assert scores == [0.81]
    assert evidence not in llm.user_inputs[0]
    assert content.temporal_validity_mode == "freshness_only"
    assert content.temporal_state == "unknown"
    assert content.temporal_next_review_at


@pytest.mark.asyncio
async def test_single_eval_cache_covers_prompt_visible_content_and_source_context() -> None:
    llm = FakeLLMService(
        '{"score": 0.82, "reason": "match", "topic_group": "systems", "style_key": "deep_dive"}'
    )
    engine = ContentDiscoveryEngine(llm_service=llm, eval_prefilter_mode="off")
    profile = _build_profile()

    def candidate(*, body: str = "body-a", likes: int = 10) -> DiscoveredContent:
        return DiscoveredContent(
            content_id="same-single",
            title="same title",
            body_text=body,
            tags=["async", "systems"],
            like_count=likes,
            source_platform="twitter",
            content_type="thread",
            source_strategy="x-search",
        )

    await engine.evaluate_content(candidate(), profile, source_context="query:alpha")
    await engine.evaluate_content(candidate(), _build_profile(), source_context="query:alpha")
    assert len(llm.calls) == 1, "equivalent content/profile values should hit"

    await engine.evaluate_content(candidate(body="body-b"), profile, source_context="query:alpha")
    await engine.evaluate_content(candidate(body="body-b"), profile, source_context="query:beta")
    await engine.evaluate_content(
        candidate(body="body-b", likes=11),
        profile,
        source_context="query:beta",
    )

    assert len(llm.calls) == 4


@pytest.mark.asyncio
async def test_single_evaluation_empty_metadata_clears_stale_values_on_cold_and_warm() -> None:
    llm = FakeLLMService(
        '{"score": 0.82, "reason": "internal", "topic_group": "", '
        '"style_key": "", "franchise_key": ""}'
    )
    engine = ContentDiscoveryEngine(llm_service=llm, eval_prefilter_mode="off")

    def stale_candidate() -> DiscoveredContent:
        return DiscoveredContent(
            content_id="same-single-metadata",
            title="same title",
            source_platform="twitter",
            source_strategy="x-search",
            topic_group="stale-topic",
            style_key="deep_dive",
            franchise_key="stale-franchise",
        )

    cold = stale_candidate()
    assert await engine.evaluate_content(cold, _build_profile()) == 0.82
    assert (cold.topic_group, cold.style_key, cold.franchise_key) == ("", "", "")

    warm = stale_candidate()
    assert await engine.evaluate_content(warm, _build_profile()) == 0.82
    assert (warm.topic_group, warm.style_key, warm.franchise_key) == ("", "", "")
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_batch_eval_cache_covers_prompt_visible_content_and_source_context() -> None:
    llm = _DynamicBatchLLMService()
    engine = ContentDiscoveryEngine(llm_service=llm, eval_prefilter_mode="off")
    profile = _build_profile()

    def candidate(*, body: str = "body-a", views: int = 10) -> DiscoveredContent:
        return DiscoveredContent(
            content_id="same-batch",
            title="same title",
            body_text=body,
            tags=["async", "systems"],
            view_count=views,
            source_platform="twitter",
            content_type="thread",
            source_strategy="x-search",
        )

    await engine.evaluate_content_batch([candidate()], profile, source_context="query:alpha")
    await engine.evaluate_content_batch(
        [candidate()],
        _build_profile(),
        source_context="query:alpha",
    )
    assert len(llm.user_inputs) == 1, "equivalent content/profile values should hit"

    await engine.evaluate_content_batch(
        [candidate(body="body-b")],
        profile,
        source_context="query:alpha",
    )
    await engine.evaluate_content_batch(
        [candidate(body="body-b")],
        profile,
        source_context="query:beta",
    )
    await engine.evaluate_content_batch(
        [candidate(body="body-b", views=11)],
        profile,
        source_context="query:beta",
    )

    assert len(llm.user_inputs) == 4


@pytest.mark.asyncio
async def test_batch_eval_cache_bypasses_heterogeneous_outer_prompt_metadata() -> None:
    llm = _DynamicBatchLLMService()
    engine = ContentDiscoveryEngine(llm_service=llm, eval_prefilter_mode="off")
    profile = _build_profile()

    def twitter_candidate() -> DiscoveredContent:
        return DiscoveredContent(
            content_id="same-twitter",
            title="twitter item",
            source_platform="twitter",
            source_strategy="twitter_search",
        )

    def reddit_candidate() -> DiscoveredContent:
        return DiscoveredContent(
            content_id="same-reddit",
            title="reddit item",
            source_platform="reddit",
            source_strategy="reddit_search",
        )

    # Seed a valid homogeneous cache entry, then include it in a mixed call.
    # The mixed call must not partially hit and send a one-item prompt carrying
    # metadata derived from the original two-item batch.
    await engine.evaluate_content_batch([twitter_candidate()], profile)
    await engine.evaluate_content_batch([twitter_candidate(), reddit_candidate()], profile)
    await engine.evaluate_content_batch([twitter_candidate(), reddit_candidate()], profile)

    assert len(llm.user_inputs) == 3
    for prompt in llm.user_inputs[1:]:
        assert [item["title"] for item in _batch_prompt_items(prompt)] == [
            "twitter item",
            "reddit item",
        ]
        assert "<source_platform>\n\nmixed\n\n</source_platform>" in prompt


@pytest.mark.asyncio
async def test_batch_eval_cache_bypasses_actual_multimodal_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.discovery.multimodal import PreparedCoverImage

    async def fake_prepare_cover_image_inputs(*_args: object, **_kwargs: object) -> list[object]:
        return [
            PreparedCoverImage(
                content_id="vision-cache",
                data_url="data:image/jpeg;base64,/9j/4AAQSkZJRg==",
                mime_type="image/jpeg",
            )
        ]

    monkeypatch.setattr(
        "openbiliclaw.discovery.multimodal.prepare_cover_image_inputs",
        fake_prepare_cover_image_inputs,
    )
    llm = _RecordingMultimodalBatchLLMService()
    engine = ContentDiscoveryEngine(
        llm_service=llm,
        eval_prefilter_mode="off",
        multimodal_evaluation_enabled=True,
    )
    profile = _build_profile()

    def candidate() -> DiscoveredContent:
        return DiscoveredContent(
            content_id="vision-cache",
            title="vision item",
            cover_url="https://example.com/cover.jpg",
            source_platform="youtube",
            source_strategy="youtube_search",
        )

    await engine.evaluate_content_batch([candidate()], profile)
    await engine.evaluate_content_batch([candidate()], profile)

    assert len(llm.user_inputs) == 2
    assert len(llm.image_inputs) == 2


@pytest.mark.asyncio
async def test_eval_cache_embedding_namespace_change_invalidates_single_and_batch() -> None:
    embedding = _NamespacedEmbeddingService({}, fingerprint="embedding-v1")
    single_llm = FakeLLMService(
        '{"score": 0.82, "reason": "match", "topic_group": "systems", "style_key": "deep_dive"}'
    )
    batch_llm = _DynamicBatchLLMService()
    single_engine = ContentDiscoveryEngine(
        llm_service=single_llm,
        embedding_service=embedding,
        eval_prefilter_mode="off",
    )
    batch_engine = ContentDiscoveryEngine(
        llm_service=batch_llm,
        embedding_service=embedding,
        eval_prefilter_mode="off",
    )
    profile = _build_profile()

    def candidate() -> DiscoveredContent:
        return DiscoveredContent(
            bvid="BVnamespace",
            title="namespace candidate",
            source_strategy="search",
        )

    await single_engine.evaluate_content(candidate(), profile)
    await single_engine.evaluate_content(candidate(), profile)
    await batch_engine.evaluate_content_batch([candidate()], profile)
    await batch_engine.evaluate_content_batch([candidate()], profile)
    assert len(single_llm.calls) == 1
    assert len(batch_llm.user_inputs) == 1

    embedding.embedding_fingerprint = "embedding-v2"
    await single_engine.evaluate_content(candidate(), profile)
    await batch_engine.evaluate_content_batch([candidate()], profile)

    assert len(single_llm.calls) == 2
    assert len(batch_llm.user_inputs) == 2


@pytest.mark.asyncio
async def test_batch_eval_cache_preserves_exact_tail_interest_weight_in_digest() -> None:
    llm = _DynamicBatchLLMService()
    engine = ContentDiscoveryEngine(llm_service=llm, eval_prefilter_mode="off")
    first_profile = _profile_with_tail_interest()
    second_profile = _profile_with_tail_interest()
    first_profile.preferences.interests[-1].weight = 0.011
    second_profile.preferences.interests[-1].weight = 0.014

    def candidate() -> DiscoveredContent:
        return DiscoveredContent(
            bvid="BVtail-weight",
            title="tail-weight candidate",
            source_strategy="search",
        )

    await engine.evaluate_content_batch([candidate()], first_profile)
    await engine.evaluate_content_batch([candidate()], second_profile)

    assert len(llm.user_inputs) == 2


def _tail_recall_vectors(*content_texts: str) -> dict[str, list[float]]:
    return {
        "\u7a00\u6709\u94c1\u8def\u6a21\u578b": _MATCH_VEC,
        **{content_text: _MATCH_VEC for content_text in content_texts},
    }


@pytest.mark.asyncio
async def test_single_eval_recall_failure_is_not_cached_and_recovery_re_evaluates() -> None:
    profile = _profile_with_tail_interest()
    content_text = "\u7a00\u6709\u94c1\u8def\u6a21\u578b \u5f00\u7bb1\u8bc4\u6d4b"
    embedding = _NamespacedEmbeddingService(
        _tail_recall_vectors(content_text),
        fingerprint="stable-model",
        failures={content_text},
    )
    llm = FakeLLMService(
        '{"score": 0.82, "reason": "match", "topic_group": "models", "style_key": "deep_dive"}'
    )
    engine = ContentDiscoveryEngine(
        llm_service=llm,
        embedding_service=embedding,
        eval_prefilter_mode="off",
    )

    def candidate() -> DiscoveredContent:
        return DiscoveredContent(
            bvid="BVsingle-recovery",
            title="\u7a00\u6709\u94c1\u8def\u6a21\u578b",
            description="\u5f00\u7bb1\u8bc4\u6d4b",
            source_strategy="search",
        )

    await engine.evaluate_content(candidate(), profile)
    assert "related_interests" not in _single_prompt_content_summary(
        str(llm.calls[-1]["user_input"])
    )

    embedding.failures.clear()
    await engine.evaluate_content(candidate(), profile)
    assert _single_prompt_content_summary(str(llm.calls[-1]["user_input"]))[
        "related_interests"
    ] == ["\u7a00\u6709\u94c1\u8def\u6a21\u578b"]
    await engine.evaluate_content(candidate(), profile)

    assert len(llm.calls) == 2, "only the complete recovered result may populate cache"


@pytest.mark.asyncio
async def test_batch_eval_partial_recall_failure_only_skips_failed_item_cache() -> None:
    profile = _profile_with_tail_interest()
    healthy_text = "\u7a00\u6709\u94c1\u8def\u6a21\u578b \u5065\u5eb7\u5185\u5bb9"
    flaky_text = "\u7a00\u6709\u94c1\u8def\u6a21\u578b \u6682\u65f6\u5931\u8d25"
    embedding = _NamespacedEmbeddingService(
        _tail_recall_vectors(healthy_text, flaky_text),
        fingerprint="stable-model",
        failures={flaky_text},
    )
    llm = _DynamicBatchLLMService()
    engine = ContentDiscoveryEngine(
        llm_service=llm,
        embedding_service=embedding,
        eval_prefilter_mode="off",
    )

    def candidates() -> list[DiscoveredContent]:
        return [
            DiscoveredContent(
                bvid="BVhealthy-recall",
                title="\u7a00\u6709\u94c1\u8def\u6a21\u578b",
                description="\u5065\u5eb7\u5185\u5bb9",
                source_strategy="search",
            ),
            DiscoveredContent(
                bvid="BVflaky-recall",
                title="\u7a00\u6709\u94c1\u8def\u6a21\u578b",
                description="\u6682\u65f6\u5931\u8d25",
                source_strategy="search",
            ),
        ]

    await engine.evaluate_content_batch(candidates(), profile)
    first_items = _batch_prompt_items(llm.user_inputs[0])
    assert first_items[0]["related_interests"] == ["\u7a00\u6709\u94c1\u8def\u6a21\u578b"]
    assert "related_interests" not in first_items[1]

    embedding.failures.clear()
    await engine.evaluate_content_batch(candidates(), profile)
    second_items = _batch_prompt_items(llm.user_inputs[1])
    assert [(item["id"], item["description"]) for item in second_items] == [("0", "暂时失败")]
    assert second_items[0]["related_interests"] == ["\u7a00\u6709\u94c1\u8def\u6a21\u578b"]

    await engine.evaluate_content_batch(candidates(), profile)
    assert len(llm.user_inputs) == 2


@pytest.mark.asyncio
async def test_batch_eval_empty_interest_vector_is_not_a_stable_zero_recall() -> None:
    profile = _profile_with_tail_interest()
    content_text = "\u7a00\u6709\u94c1\u8def\u6a21\u578b \u5411\u91cf\u6062\u590d"
    vectors = _tail_recall_vectors(content_text)
    vectors.pop("\u7a00\u6709\u94c1\u8def\u6a21\u578b")
    embedding = _NamespacedEmbeddingService(vectors, fingerprint="stable-model")
    llm = _DynamicBatchLLMService()
    engine = ContentDiscoveryEngine(
        llm_service=llm,
        embedding_service=embedding,
        eval_prefilter_mode="off",
    )

    def candidate() -> DiscoveredContent:
        return DiscoveredContent(
            bvid="BVinterest-vector-recovery",
            title="\u7a00\u6709\u94c1\u8def\u6a21\u578b",
            description="\u5411\u91cf\u6062\u590d",
            source_strategy="search",
        )

    await engine.evaluate_content_batch([candidate()], profile)
    assert "related_interests" not in _batch_prompt_items(llm.user_inputs[0])[0]

    embedding.vectors["\u7a00\u6709\u94c1\u8def\u6a21\u578b"] = _MATCH_VEC
    await engine.evaluate_content_batch([candidate()], profile)
    await engine.evaluate_content_batch([candidate()], profile)

    assert len(llm.user_inputs) == 2, "partial interest-vector recall must not poison cache"


def test_cached_backfill_preserves_temporal_evaluation_metadata() -> None:
    class _BackfillDatabase:
        def get_unrecommended_content(self, **_kwargs: object) -> list[dict[str, object]]:
            return [
                {
                    "bvid": "BVBACKFILLTEMPORAL",
                    "title": "版本化内容",
                    "source": "search",
                    "source_platform": "bilibili",
                    "relevance_score": 0.9,
                    "temporal_class": "versioned",
                    "temporal_confidence": 0.91,
                    "temporal_reason": "依赖具体软件版本",
                    "temporal_policy_version": "v1",
                    "pool_expression": "已经生成的正式文案",
                    "pool_topic_label": "软件版本",
                }
            ]

    engine = ContentDiscoveryEngine(
        llm_service=None,
        database=_BackfillDatabase(),  # type: ignore[arg-type]
    )

    [item] = engine._load_cached_backfill(
        limit=1,
        exclude_bvids=set(),
        source_platforms={"bilibili"},
    )

    assert item.temporal_class == "versioned"
    assert item.temporal_confidence == pytest.approx(0.91)
    assert item.temporal_reason == "依赖具体软件版本"
    assert item.temporal_policy_version == "v1"
    assert item.pool_expression == "已经生成的正式文案"
    assert item.pool_topic_label == "软件版本"
