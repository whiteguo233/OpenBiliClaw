"""Advanced ProfileUpdatePipeline tests.

Covers control flow (tick/flush/time-gate/eviction), persistence, error
handling, deep side effects (portrait regen, changelog, Core changed=True),
Surface compute path, speculator integration, and routing edge cases.

Companion to test_signal_channel_eval.py — that file proves channels route
correctly; this file proves the surrounding machinery is solid.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.memory.manager import MemoryManager
from openbiliclaw.soul.layer_updaters import _update_surface
from openbiliclaw.soul.pipeline import (
    _BUFFERED_LAYERS,
    DEFAULT_THRESHOLDS,
    LayerBuffer,
    LayerThreshold,
    OnionLayer,
    ProfileUpdatePipeline,
    SignalType,
    _serialize_signal,
    classify_signal,
    load_pipeline_state,
    save_pipeline_state,
    signal_from_feedback,
    signals_from_dialogue,
    signals_from_events,
)
from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer
from openbiliclaw.soul.profile import OnionProfile
from openbiliclaw.soul.profile_builder import ProfileBuilder

# ---------------------------------------------------------------------------
# Mock LLM service — returns rich responses for every layer
# ---------------------------------------------------------------------------


_PREF_RESP = json.dumps(
    {
        "interests": [
            {"name": "AI", "category": "知识", "weight": 0.85, "source": "events"},
        ],
        "style": {
            "preferred_duration": "long",
            "preferred_pace": "moderate",
            "quality_sensitivity": 0.7,
            "humor_preference": 0.3,
            "depth_preference": 0.85,
        },
        "context": {"session_type": "深度钻研型"},
        "exploration_openness": 0.6,
        "disliked_topics": [],
        "cognitive_style": ["系统化思考"],
        "favorite_up_users": [],
    },
    ensure_ascii=False,
)

_ROLE_CHANGED_RESP = json.dumps(
    {
        "changed": True,
        "life_stage": "互联网从业者在职期",
        "current_phase": "技能强化阶段",
        "reason": "证据表明用户处于技术学习期",
    },
    ensure_ascii=False,
)

_VALUES_CHANGED_RESP = json.dumps(
    {
        "changed": True,
        "values": ["持续学习", "创造价值"],
        "motivational_drivers": ["技术精进驱动"],
        "reason": "证据显示用户重视学习",
    },
    ensure_ascii=False,
)

_CORE_CHANGED_RESP = json.dumps(
    {
        "changed": True,
        "core_traits": ["好奇心强", "逻辑严谨", "深度探索"],
        "deep_needs": ["对原理的深层理解"],
        "mbti": {
            "type": "INTJ",
            "confidence": 0.7,
            "dimensions": {
                "EI": {"pole": "I", "strength": 0.7},
                "SN": {"pole": "N", "strength": 0.8},
                "TF": {"pole": "T", "strength": 0.6},
                "JP": {"pole": "J", "strength": 0.6},
            },
        },
        "reason": "证据强烈支持核心人格调整",
    },
    ensure_ascii=False,
)

_PORTRAIT_RESP = json.dumps(
    {
        # Must be >= 200 chars to pass ProfileBuilder._validate_payload —
        # otherwise regenerate_portrait raises and the success branch
        # (pipeline.py:625-626) never runs.
        "personality_portrait": (
            "这是一个热爱技术探索的用户，对知识有深度渴望，习惯系统化地理解新领域。"
            "他在职业发展中持续追求技能精进，重视持续学习与价值创造，"
            "倾向于深入理解事物的运作原理，避免浅尝辄止。"
            "在面对复杂概念时，他愿意花时间逐层拆解，把抽象的理论与日常经验联系起来；"
            "在与他人交流时，更倾向于通过提问与思辨的方式确认观点是否站得住脚。"
            "他对新技术保持开放态度，但不盲从潮流，愿意先弄清楚原理再决定是否投入。"
            "整体上呈现出稳定的求知节奏与不急不躁的探索姿态。"
        ),
        "core_traits": ["好奇心强", "逻辑严谨"],
        "cognitive_style": ["系统化思考"],
        "motivational_drivers": ["技术精进驱动"],
        "current_phase": "技术探索期",
        "values": ["持续学习", "创造价值"],
        "life_stage": "互联网从业者",
        "deep_needs": ["对原理的深层理解"],
        "mbti": {
            "type": "INTP",
            "confidence": 0.65,
            "dimensions": {},
        },
    },
    ensure_ascii=False,
)


class _RichFakeService:
    """LLM mock that returns layer-appropriate responses with changed=True."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        caller: str = "",
    ) -> LLMResponse:
        self.calls.append({"system_instruction": system_instruction, "user_input": user_input})
        # NOTE: portrait prompt also contains "价值观", so the portrait
        # branch must be checked FIRST to avoid getting shadowed.
        if "人格画像" in system_instruction and "<preference_summary>" in user_input:
            return LLMResponse(content=_PORTRAIT_RESP, provider="fake")
        if "生活阶段" in system_instruction:
            return LLMResponse(content=_ROLE_CHANGED_RESP, provider="fake")
        if "价值观" in system_instruction:
            return LLMResponse(content=_VALUES_CHANGED_RESP, provider="fake")
        if "核心人格特质" in system_instruction:
            return LLMResponse(content=_CORE_CHANGED_RESP, provider="fake")
        return LLMResponse(content=_PREF_RESP, provider="fake")

    @property
    def portrait_calls(self) -> list[dict[str, Any]]:
        return [
            c
            for c in self.calls
            if "人格画像" in c["system_instruction"] and "<preference_summary>" in c["user_input"]
        ]


class _RaisingFakeService:
    """LLM mock that raises on any structured task call."""

    async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
        raise RuntimeError("LLM intentionally broken for test")


# ---------------------------------------------------------------------------
# Pipeline factory helpers
# ---------------------------------------------------------------------------


def _make_low_threshold_pipeline(
    tmp_path: Path,
    *,
    service: Any = None,
    speculator: Any = None,
    avoidance_speculator: Any = None,
    speculator_idle_interval_minutes: int = 30,
) -> tuple[ProfileUpdatePipeline, _RichFakeService, MemoryManager]:
    """Pipeline with min_signals=1 thresholds — every signal triggers update."""
    svc = service or _RichFakeService()
    memory = MemoryManager(Path(tmp_path))
    memory.initialize()
    thresholds = {
        layer: LayerThreshold(min_signals=1, min_interval_seconds=0, max_buffer_size=200)
        for layer in _BUFFERED_LAYERS
    }
    pipeline = ProfileUpdatePipeline(
        memory=memory,
        preference_analyzer=PreferenceAnalyzer(registry=svc),
        profile_builder=ProfileBuilder(registry=svc),
        thresholds=thresholds,
        speculator=speculator,
        avoidance_speculator=avoidance_speculator,
        speculator_idle_interval_minutes=speculator_idle_interval_minutes,
    )
    return pipeline, svc, memory


def _make_gated_pipeline(
    tmp_path: Path,
    *,
    interval_seconds: int = 3600,
) -> tuple[ProfileUpdatePipeline, _RichFakeService, MemoryManager]:
    """Pipeline with a long min_interval — second update should be blocked."""
    svc = _RichFakeService()
    memory = MemoryManager(Path(tmp_path))
    memory.initialize()
    thresholds = {
        layer: LayerThreshold(
            min_signals=1,
            min_interval_seconds=interval_seconds,
            max_buffer_size=200,
        )
        for layer in _BUFFERED_LAYERS
    }
    pipeline = ProfileUpdatePipeline(
        memory=memory,
        preference_analyzer=PreferenceAnalyzer(registry=svc),
        profile_builder=ProfileBuilder(registry=svc),
        thresholds=thresholds,
    )
    return pipeline, svc, memory


# ===========================================================================
# 0. signals_from_events classification
# ===========================================================================


def test_signals_from_events_retraction_is_behavior_not_strong_signal() -> None:
    """A retraction feedback event must ride the plain BEHAVIOR_EVENT path,
    never the FEEDBACK strong-signal type — no min_signals=1 bypass into
    VALUES/CORE (invariant 2)."""
    signals = signals_from_events(
        [
            {
                "event_type": "feedback",
                "title": "a withdrawn like",
                "metadata": {"feedback_type": "retraction", "retracted_action": "like"},
            }
        ]
    )
    assert len(signals) == 1
    assert signals[0].signal_type == SignalType.BEHAVIOR_EVENT


def test_signals_from_events_engagement_types_still_strong() -> None:
    """Regression: like/favorite events remain ENGAGEMENT_EVENT signals."""
    signals = signals_from_events([{"event_type": "like", "title": "x"}])
    assert signals[0].signal_type == SignalType.ENGAGEMENT_EVENT


@pytest.mark.parametrize(
    "event_type",
    ["hover", "scroll", "snapshot", "reshuffle", "pause", "seek"],
)
def test_context_only_collector_events_are_not_buffered(tmp_path: Path, event_type: str) -> None:
    """Context-only collector events stay in the ledger but never become
    profile evidence.

    hover / scroll / snapshot / reshuffle / pause / seek are useful for the
    activity feed and diagnostics, yet they must not buffer a ProfileSignal,
    trigger a layer updater, or reach the speculators.
    """
    pipeline, _, _ = _make_low_threshold_pipeline(tmp_path)

    result, accepted = pipeline._enqueue_batch_locked(
        signals_from_events([{"event_type": event_type, "title": f"{event_type} 事件"}])
    )

    assert result.signals_accepted == 0
    assert result.layers_buffered == []
    assert accepted == []
    assert pipeline._total_ingested == 0
    assert all(not buf.signals for buf in pipeline._buffers.values())


def test_search_and_view_are_still_profile_evidence(tmp_path: Path) -> None:
    """The non-profile denylist must not swallow search or view.

    ``search`` is satisfaction-neutral but is a real intent signal, and
    ``view`` remains weak-to-medium profile evidence — both keep flowing.
    """
    pipeline, _, _ = _make_low_threshold_pipeline(tmp_path)

    result, accepted = pipeline._enqueue_batch_locked(
        signals_from_events(
            [
                {"event_type": "search", "title": "搜了 AI Agent"},
                {"event_type": "view", "title": "AI Agent 教程"},
            ]
        )
    )

    assert result.signals_accepted == 2
    assert len(accepted) == 2
    assert OnionLayer.INTEREST.value in result.layers_buffered


# ===========================================================================
# 1. Control flow tests
# ===========================================================================


@pytest.mark.asyncio
async def test_tick_processes_ready_buffer(tmp_path: Path) -> None:
    """tick() should drain ready buffers and update layers."""
    pipeline, _, _ = _make_low_threshold_pipeline(tmp_path)

    # Manually pre-populate the interest buffer (bypass ingest path)
    raw_signal = _serialize_signal(
        signals_from_events([{"event_type": "view", "title": "AI教程"}])[0]
    )
    pipeline._buffers[OnionLayer.INTEREST.value].signals.append(raw_signal)

    flush_result = await pipeline.tick()

    layers = {r.layer for r in flush_result.layers_updated}
    assert OnionLayer.INTEREST in layers, f"tick() should drain and update INTEREST. Got: {layers}"
    # Buffer must now be empty after drain
    assert pipeline._buffers[OnionLayer.INTEREST.value].signals == []


@pytest.mark.asyncio
async def test_flush_force_updates_all_layers(tmp_path: Path) -> None:
    """flush() with no layers arg should force-update every buffered layer."""
    pipeline, _, _ = _make_low_threshold_pipeline(tmp_path)

    # Seed two layers with raw signals
    sig = _serialize_signal(signals_from_events([{"event_type": "view", "title": "测试"}])[0])
    pipeline._buffers[OnionLayer.INTEREST.value].signals.append(sig)
    pipeline._buffers[OnionLayer.ROLE.value].signals.append(sig)

    flush_result = await pipeline.flush()

    layers = {r.layer for r in flush_result.layers_updated}
    assert OnionLayer.INTEREST in layers
    assert OnionLayer.ROLE in layers


@pytest.mark.asyncio
async def test_flush_with_layer_subset_only_updates_selected(tmp_path: Path) -> None:
    """flush(layers={INTEREST}) should ignore buffers in other layers."""
    pipeline, _, _ = _make_low_threshold_pipeline(tmp_path)

    sig = _serialize_signal(signals_from_events([{"event_type": "view", "title": "测试"}])[0])
    pipeline._buffers[OnionLayer.INTEREST.value].signals.append(sig)
    pipeline._buffers[OnionLayer.ROLE.value].signals.append(sig)

    flush_result = await pipeline.flush(layers=frozenset({OnionLayer.INTEREST}))

    layers = {r.layer for r in flush_result.layers_updated}
    assert OnionLayer.INTEREST in layers
    assert OnionLayer.ROLE not in layers, (
        f"flush(layers={{INTEREST}}) must NOT touch ROLE. Got: {layers}"
    )
    # Role buffer should be untouched
    assert len(pipeline._buffers[OnionLayer.ROLE.value].signals) == 1


@pytest.mark.asyncio
async def test_min_interval_seconds_blocks_second_update(tmp_path: Path) -> None:
    """Within min_interval_seconds, a non-strong signal must NOT re-update."""
    pipeline, _, _ = _make_gated_pipeline(tmp_path, interval_seconds=3600)

    # First behavior signal triggers update
    first = await pipeline.ingest(
        signals_from_events([{"event_type": "view", "title": "Python教程"}])[0]
    )
    assert any(r.layer == OnionLayer.INTEREST for r in first.layers_updated)

    # Second behavior signal within the interval window must be blocked
    second = await pipeline.ingest(
        signals_from_events([{"event_type": "view", "title": "Java教程"}])[0]
    )
    second_layers = {r.layer for r in second.layers_updated}
    assert OnionLayer.INTEREST not in second_layers, (
        f"Second behavior update must be gated by min_interval. Got: {second_layers}"
    )


@pytest.mark.asyncio
async def test_strong_signal_still_blocked_by_min_interval(tmp_path: Path) -> None:
    """Strong signals bypass min_signals but NOT min_interval (per is_ready logic)."""
    pipeline, _, _ = _make_gated_pipeline(tmp_path, interval_seconds=3600)

    # First feedback triggers update
    await pipeline.ingest(signal_from_feedback("like", "测试视频", ""))

    # Manually mark INTEREST buffer as just-updated to simulate the gate
    pipeline._buffers[OnionLayer.INTEREST.value].last_updated_at = datetime.now().isoformat()

    # Append another feedback signal directly and call tick
    second_sig = _serialize_signal(signal_from_feedback("like", "另一个视频", ""))
    pipeline._buffers[OnionLayer.INTEREST.value].signals.append(second_sig)

    flush_result = await pipeline.tick()
    interest_updated = [r for r in flush_result.layers_updated if r.layer == OnionLayer.INTEREST]
    assert not interest_updated, "Even strong signals must respect min_interval_seconds gate"


@pytest.mark.asyncio
async def test_concurrent_ingest_batches_do_not_overlap_llm_analysis(tmp_path: Path) -> None:
    """A burst of concurrent ingest_batch calls must serialize layer updates.

    Without the pipeline lock, two rapid feedback ingests each drain their own
    buffer and run parallel preference LLM passes (double cost + racing
    ``pipeline_state.json`` snapshots). The lock queues the second call until
    the first analysis finishes.
    """
    entered = asyncio.Event()
    release = asyncio.Event()
    in_flight = 0
    max_in_flight = 0

    class BlockingService(_RichFakeService):
        async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
            nonlocal in_flight, max_in_flight
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            entered.set()
            try:
                await asyncio.wait_for(release.wait(), timeout=10)
            finally:
                in_flight -= 1
            return await super().complete_structured_task(**kwargs)

    pipeline, _, _ = _make_low_threshold_pipeline(tmp_path, service=BlockingService())

    first = asyncio.create_task(pipeline.ingest(signal_from_feedback("like", "A", "")))
    await asyncio.wait_for(entered.wait(), timeout=5)
    second = asyncio.create_task(pipeline.ingest(signal_from_feedback("like", "B", "")))
    # Give the second call a real chance to start; the lock must keep it queued.
    await asyncio.sleep(0.05)
    assert max_in_flight == 1, "concurrent ingests must not overlap LLM analysis"
    release.set()
    results = await asyncio.gather(first, second)
    assert all(result.signals_accepted == 1 for result in results)
    assert max_in_flight == 1


@pytest.mark.asyncio
async def test_concurrent_ingest_and_tick_do_not_overlap_llm_analysis(tmp_path: Path) -> None:
    """The shared lock serializes an event ingest against a scheduler tick."""
    entered = asyncio.Event()
    release = asyncio.Event()
    in_flight = 0
    max_in_flight = 0

    class BlockingService(_RichFakeService):
        async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
            nonlocal in_flight, max_in_flight
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            entered.set()
            try:
                await asyncio.wait_for(release.wait(), timeout=10)
            finally:
                in_flight -= 1
            return await super().complete_structured_task(**kwargs)

    pipeline, _, _ = _make_low_threshold_pipeline(tmp_path, service=BlockingService())
    await pipeline.enqueue(signal_from_feedback("like", "tick 中的反馈", ""))

    ticking = asyncio.create_task(pipeline.tick())
    await asyncio.wait_for(entered.wait(), timeout=5)
    ingesting = asyncio.create_task(pipeline.ingest(signal_from_feedback("like", "并发事件", "")))
    await asyncio.sleep(0.05)
    assert max_in_flight == 1
    release.set()
    await asyncio.gather(ticking, ingesting)
    assert max_in_flight == 1


@pytest.mark.asyncio
async def test_max_buffer_size_evicts_oldest_signals(tmp_path: Path) -> None:
    """When buffer exceeds max_buffer_size, oldest signals should be dropped."""
    svc = _RichFakeService()
    memory = MemoryManager(Path(tmp_path))
    memory.initialize()
    # Tight cap: only 3 signals fit per buffer
    thresholds = {
        layer: LayerThreshold(min_signals=999, min_interval_seconds=0, max_buffer_size=3)
        for layer in _BUFFERED_LAYERS
    }
    pipeline = ProfileUpdatePipeline(
        memory=memory,
        preference_analyzer=PreferenceAnalyzer(registry=svc),
        profile_builder=ProfileBuilder(registry=svc),
        thresholds=thresholds,
    )

    # Push 5 view events; min_signals=999 prevents any update from firing
    signals = signals_from_events([{"event_type": "view", "title": f"视频{i}"} for i in range(5)])
    await pipeline.ingest_batch(signals)

    interest_buf = pipeline._buffers[OnionLayer.INTEREST.value]
    assert len(interest_buf.signals) == 3, (
        f"Buffer should be evicted to max_buffer_size=3. Got len={len(interest_buf.signals)}"
    )
    # Verify oldest were dropped — only 视频2/3/4 should remain
    remaining_titles = [s["payload"]["title"] for s in interest_buf.signals]
    assert remaining_titles == ["视频2", "视频3", "视频4"], remaining_titles


def test_layer_buffer_evict_method() -> None:
    """LayerBuffer.evict() should drop oldest signals when over max_size."""
    buf = LayerBuffer(layer=OnionLayer.INTEREST)
    for i in range(10):
        buf.signals.append({"id": str(i), "signal_type": "behavior_event"})
    buf.evict(max_size=4)
    assert len(buf.signals) == 4
    assert [s["id"] for s in buf.signals] == ["6", "7", "8", "9"]


# ===========================================================================
# 2. Persistence tests
# ===========================================================================


def test_save_and_load_pipeline_state_roundtrip(tmp_path: Path) -> None:
    """save_pipeline_state then load_pipeline_state should preserve buffers."""
    buffers: dict[str, LayerBuffer] = {}
    for layer in _BUFFERED_LAYERS:
        buf = LayerBuffer(layer=layer)
        if layer == OnionLayer.INTEREST:
            buf.signals.append({"id": "abc", "signal_type": "behavior_event", "payload": {}})
            buf.last_updated_at = "2026-04-08T10:00:00"
            buf.update_count = 7
        buffers[layer.value] = buf

    save_pipeline_state(tmp_path, buffers, total_ingested=42)

    state_file = tmp_path / "memory" / "pipeline_state.json"
    assert state_file.exists()

    loaded = load_pipeline_state(tmp_path)
    assert set(loaded.keys()) == {layer.value for layer in _BUFFERED_LAYERS}
    interest_buf = loaded[OnionLayer.INTEREST.value]
    assert len(interest_buf.signals) == 1
    assert interest_buf.signals[0]["id"] == "abc"
    assert interest_buf.last_updated_at == "2026-04-08T10:00:00"
    assert interest_buf.update_count == 7


def test_layer_buffer_from_dict_basic() -> None:
    """LayerBuffer.from_dict should reconstruct from a dict."""
    data = {
        "layer": "interest",
        "signals": [{"id": "x", "signal_type": "behavior_event"}],
        "last_updated_at": "2026-04-08T10:00:00",
        "update_count": 3,
    }
    buf = LayerBuffer.from_dict(data)
    assert buf.layer == OnionLayer.INTEREST
    assert len(buf.signals) == 1
    assert buf.last_updated_at == "2026-04-08T10:00:00"
    assert buf.update_count == 3


def test_layer_buffer_from_dict_invalid_layer_falls_back_to_surface() -> None:
    """Unknown layer string in serialized data should fall back to SURFACE."""
    buf = LayerBuffer.from_dict({"layer": "unknown_layer", "signals": []})
    assert buf.layer == OnionLayer.SURFACE


def test_layer_buffer_from_dict_drops_non_dict_signals() -> None:
    """Garbage entries in signals list must be filtered out."""
    buf = LayerBuffer.from_dict(
        {
            "layer": "interest",
            "signals": [{"id": "valid"}, "not-a-dict", 42, {"id": "also-valid"}],
        }
    )
    assert len(buf.signals) == 2
    assert all(isinstance(s, dict) for s in buf.signals)


def test_load_pipeline_state_missing_file_returns_empty_buffers(tmp_path: Path) -> None:
    """If pipeline_state.json doesn't exist, return fresh empty buffers."""
    loaded = load_pipeline_state(tmp_path)
    assert set(loaded.keys()) == {layer.value for layer in _BUFFERED_LAYERS}
    for buf in loaded.values():
        assert buf.signals == []


def test_load_pipeline_state_invalid_json_falls_back(tmp_path: Path) -> None:
    """Corrupt pipeline_state.json should not crash; returns empty buffers."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "pipeline_state.json").write_text("not json {{{", encoding="utf-8")

    loaded = load_pipeline_state(tmp_path)
    assert set(loaded.keys()) == {layer.value for layer in _BUFFERED_LAYERS}
    for buf in loaded.values():
        assert buf.signals == []


@pytest.mark.asyncio
async def test_pipeline_buffer_survives_restart(tmp_path: Path) -> None:
    """Buffered signals must persist across pipeline reconstruction."""
    svc = _RichFakeService()
    memory = MemoryManager(Path(tmp_path))
    memory.initialize()
    # High threshold so signals stay buffered
    thresholds = {
        layer: LayerThreshold(min_signals=999, min_interval_seconds=0, max_buffer_size=200)
        for layer in _BUFFERED_LAYERS
    }
    pipeline1 = ProfileUpdatePipeline(
        memory=memory,
        preference_analyzer=PreferenceAnalyzer(registry=svc),
        profile_builder=ProfileBuilder(registry=svc),
        thresholds=thresholds,
    )
    await pipeline1.ingest_batch(
        signals_from_events([{"event_type": "view", "title": "持久化测试"}])
    )

    # Recreate pipeline with same memory dir
    memory2 = MemoryManager(Path(tmp_path))
    memory2.initialize()
    pipeline2 = ProfileUpdatePipeline(
        memory=memory2,
        preference_analyzer=PreferenceAnalyzer(registry=svc),
        profile_builder=ProfileBuilder(registry=svc),
        thresholds=thresholds,
    )
    interest_buf = pipeline2._buffers[OnionLayer.INTEREST.value]
    assert len(interest_buf.signals) >= 1, (
        "Buffered signals must survive pipeline restart via state file"
    )
    assert interest_buf.signals[0]["payload"]["title"] == "持久化测试"


@pytest.mark.asyncio
async def test_enqueue_is_buffer_only_persistent_and_idempotent(tmp_path: Path) -> None:
    """Durable enqueue never analyzes and a stable ID is appended only once."""
    service = _RichFakeService()
    memory = MemoryManager(tmp_path)
    memory.initialize()
    pipeline = ProfileUpdatePipeline(
        memory=memory,
        preference_analyzer=PreferenceAnalyzer(registry=service),
        profile_builder=ProfileBuilder(registry=service),
    )
    signal = signal_from_feedback(
        "dislike",
        "需要重启恢复的反馈",
        "太浅",
        signal_id="feedback-event-41",
    )

    first = await pipeline.enqueue(signal)
    second = await pipeline.enqueue(signal)

    assert first.signals_accepted == 1
    assert second.signals_accepted == 0
    assert service.calls == [], "enqueue-only must never enter an LLM-backed updater"

    rebuilt_memory = MemoryManager(tmp_path)
    rebuilt_memory.initialize()
    rebuilt = ProfileUpdatePipeline(
        memory=rebuilt_memory,
        preference_analyzer=PreferenceAnalyzer(registry=service),
        profile_builder=ProfileBuilder(registry=service),
    )
    for layer in (OnionLayer.INTEREST, OnionLayer.SURFACE):
        matching = [
            item
            for item in rebuilt._buffers[layer.value].signals
            if item.get("id") == "feedback-event-41"
        ]
        assert len(matching) == 1
        assert matching[0]["signal_type"] == SignalType.FEEDBACK.value


@pytest.mark.asyncio
async def test_checkpoint_save_failure_rolls_back_buffer_cursor_and_observation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A failed atomic replace leaves no in-memory claim or speculative tick."""

    class SpySpeculator:
        def __init__(self) -> None:
            self.observed: list[object] = []

        def observe(self, events: object) -> None:
            self.observed.append(events)

    speculator = SpySpeculator()
    pipeline, _, _ = _make_low_threshold_pipeline(tmp_path, speculator=speculator)
    signal = signal_from_feedback(
        "dislike",
        "save failure must retry",
        "",
        signal_id="feedback-event-77",
    )

    def fail_save() -> None:
        raise OSError("atomic replace failed")

    monkeypatch.setattr(pipeline, "_save_state", fail_save)

    with pytest.raises(OSError, match="atomic replace failed"):
        await pipeline.checkpointed_enqueue_batch(
            [signal],
            consumer="content_feedback",
            cursor=77,
            owner_version=2,
            cutover_at="2026-08-01T00:00:00+00:00",
            cutover_event_id=70,
        )

    assert pipeline.consumer_checkpoint("content_feedback") == {
        "cursor": 0,
        "owner_version": 0,
        "cutover_at": "",
        "cutover_event_id": 0,
        "has_cutover_event_id": False,
    }
    assert pipeline._total_ingested == 0
    assert speculator.observed == []
    for layer in (OnionLayer.INTEREST, OnionLayer.SURFACE):
        assert pipeline._buffers[layer.value].signals == []


@pytest.mark.asyncio
async def test_tick_commits_drain_before_later_maintenance_failure(tmp_path: Path) -> None:
    """A failed speculator pass cannot make restart replay an applied layer update."""

    class RaisingSpeculator:
        def observe(self, _events: object) -> None:
            return None

        async def tick(self, *_args: object, **_kwargs: object) -> object:
            raise RuntimeError("maintenance failed")

    pipeline, _, _ = _make_low_threshold_pipeline(tmp_path)
    pipeline._speculator = RaisingSpeculator()  # type: ignore[assignment]
    signal = signal_from_feedback("like", "已经应用的反馈", "", signal_id="feedback-event-9")
    await pipeline.enqueue(signal)

    with pytest.raises(RuntimeError, match="maintenance failed"):
        await pipeline.tick()

    service = _RichFakeService()
    memory = MemoryManager(tmp_path)
    memory.initialize()
    rebuilt = ProfileUpdatePipeline(
        memory=memory,
        preference_analyzer=PreferenceAnalyzer(registry=service),
        profile_builder=ProfileBuilder(registry=service),
    )
    for layer in (OnionLayer.INTEREST, OnionLayer.SURFACE):
        assert all(
            item.get("id") != "feedback-event-9" for item in rebuilt._buffers[layer.value].signals
        )


# ===========================================================================
# 3. Error path tests
# ===========================================================================


@pytest.mark.asyncio
async def test_update_layer_exception_restores_signals(tmp_path: Path) -> None:
    """If an updater raises, drained signals must be put back in the buffer."""
    pipeline, _, _ = _make_low_threshold_pipeline(tmp_path)

    # Replace the registry on the analyzer with a raising service
    pipeline._preference_analyzer = PreferenceAnalyzer(registry=_RaisingFakeService())
    pipeline._profile_builder = ProfileBuilder(registry=_RaisingFakeService())

    # Use a behavior event so it routes to interest+role+surface
    sig = signals_from_events([{"event_type": "view", "title": "崩溃测试"}])[0]

    # _update_interest catches its own exception (returns changed=False),
    # but role/values use the broken profile_builder which DOES raise.
    # However the role updater also wraps in try/except. Let's force the
    # outer _update_layer error path by patching update_layer directly.
    from openbiliclaw.soul import layer_updaters as lu_mod

    original_update_layer = lu_mod.update_layer

    async def boom(**kwargs: Any) -> Any:
        raise RuntimeError("simulated layer update failure")

    lu_mod.update_layer = boom  # type: ignore[assignment]
    try:
        result = await pipeline.ingest(sig)
    finally:
        lu_mod.update_layer = original_update_layer  # type: ignore[assignment]

    # No layer should report an update because every call raised
    assert result.layers_updated == [], (
        f"All updates should have been swallowed. Got: {result.layers_updated}"
    )
    # Signals should be back in the buffers (target layers were interest+role+surface)
    interest_buf = pipeline._buffers[OnionLayer.INTEREST.value]
    role_buf = pipeline._buffers[OnionLayer.ROLE.value]
    assert len(interest_buf.signals) >= 1, (
        "Interest signal should be restored to buffer after exception"
    )
    assert len(role_buf.signals) >= 1, "Role signal should be restored to buffer after exception"


@pytest.mark.asyncio
async def test_feedback_no_longer_triggers_portrait_regen(tmp_path: Path) -> None:
    """FEEDBACK no longer routes to VALUES, so portrait regen is never attempted (P1).

    A patched-to-raise ``regenerate_portrait`` must never be called — the pipeline
    completes normally and only the fast-line INTEREST layer updates.
    """
    pipeline, svc, memory = _make_low_threshold_pipeline(tmp_path)

    from openbiliclaw.soul import layer_updaters as lu_mod

    original = lu_mod.regenerate_portrait
    called = False

    async def boom(**kwargs: Any) -> str:
        nonlocal called
        called = True
        raise RuntimeError("portrait broken")

    lu_mod.regenerate_portrait = boom  # type: ignore[assignment]
    try:
        result = await pipeline.ingest(signal_from_feedback("like", "测试", ""))
    finally:
        lu_mod.regenerate_portrait = original  # type: ignore[assignment]

    layers = {r.layer for r in result.layers_updated}
    assert OnionLayer.VALUES not in layers, "FEEDBACK must not update VALUES (P1 retired)"
    assert called is False, "portrait regen must never fire from a FEEDBACK signal now"


# ===========================================================================
# 4. Deep side-effect tests
# ===========================================================================


@pytest.mark.asyncio
async def test_feedback_does_not_regenerate_portrait(tmp_path: Path) -> None:
    """FEEDBACK no longer changes VALUES, so it must not regenerate the portrait (P1)."""
    pipeline, svc, _ = _make_low_threshold_pipeline(tmp_path)

    portrait_calls_before = len(svc.portrait_calls)

    await pipeline.ingest(signal_from_feedback("like", "深度内容", "强反馈"))

    portrait_calls_after = len(svc.portrait_calls)
    assert portrait_calls_after == portrait_calls_before, (
        "FEEDBACK routes only to the fast line now — no portrait regen. "
        f"Before={portrait_calls_before}, After={portrait_calls_after}"
    )


@pytest.mark.asyncio
async def test_portrait_not_regenerated_for_interest_only_change(
    tmp_path: Path,
) -> None:
    """Interest changes alone (no Core/Values) must NOT regenerate portrait."""
    pipeline, svc, _ = _make_low_threshold_pipeline(tmp_path)

    portrait_calls_before = len(svc.portrait_calls)

    # Pure behavior event: routes to surface+interest+role, never to values
    await pipeline.ingest(signals_from_events([{"event_type": "view", "title": "教程"}])[0])

    # Note: ROLE may also change with our mock, but ROLE is NOT a portrait trigger
    portrait_calls_after = len(svc.portrait_calls)
    assert portrait_calls_after == portrait_calls_before, (
        "Interest/Role changes alone should NOT trigger portrait regen"
    )


@pytest.mark.asyncio
async def test_changelog_recorded_on_layer_change(tmp_path: Path) -> None:
    """Each layer change should append a line to soul_changelog.md."""
    pipeline, _, _ = _make_low_threshold_pipeline(tmp_path)

    await pipeline.ingest(signal_from_feedback("like", "好内容", ""))

    changelog_path = Path(tmp_path) / "memory" / "soul_changelog.md"
    assert changelog_path.exists(), "Changelog file should be created"
    content = changelog_path.read_text(encoding="utf-8")
    assert "画像更新日志" in content
    # FEEDBACK now routes to the fast line (INTEREST/SURFACE), not VALUES (P1).
    assert "interest" in content.lower() or "兴趣" in content or "surface" in content.lower(), (
        content
    )


@pytest.mark.asyncio
async def test_state_insight_no_longer_updates_core(tmp_path: Path) -> None:
    """A state-kind DIALOGUE_INSIGHT is inert in the pipeline — CORE write retired (P1).

    The deep CORE layer changes only via a gated soul rebuild; the pipeline
    neither buffers nor updates it from a state self-report.
    """
    pipeline, svc, memory = _make_low_threshold_pipeline(tmp_path)

    seed_profile = OnionProfile()
    seed_profile.core.core_traits = ["旧特质"]
    seed_profile.core.deep_needs = ["旧需求"]
    soul_layer = memory.get_layer("soul")
    soul_layer.data.update(seed_profile.to_dict())
    soul_layer.save()

    insight_signals = signals_from_dialogue(
        [{"kind": "state", "content": "用户正在深度思考人生方向", "confidence": 0.9}]
    )
    result = await pipeline.ingest(insight_signals[0])

    core_results = [r for r in result.layers_updated if r.layer == OnionLayer.CORE]
    assert core_results == [], "CORE must never fire from a pipeline state insight now"

    # The seeded core is untouched — no traits/MBTI applied.
    reloaded = OnionProfile.from_dict(memory.get_layer("soul").data)
    assert reloaded.core.core_traits == ["旧特质"]
    assert reloaded.core.deep_needs == ["旧需求"]


@pytest.mark.asyncio
async def test_state_insight_does_not_regenerate_portrait(tmp_path: Path) -> None:
    """A state-kind insight is inert — no portrait regen (deep CORE write retired, P1)."""
    pipeline, svc, _ = _make_low_threshold_pipeline(tmp_path)

    portrait_calls_before = len(svc.portrait_calls)
    insight_signals = signals_from_dialogue(
        [{"kind": "state", "content": "深度思考状态", "confidence": 0.9}]
    )
    await pipeline.ingest(insight_signals[0])

    portrait_calls_after = len(svc.portrait_calls)
    assert portrait_calls_after == portrait_calls_before, (
        "state insight must not trigger portrait regen (pipeline deep write retired)"
    )


# ===========================================================================
# 5. Surface compute path tests
# ===========================================================================


@pytest.mark.asyncio
async def test_surface_view_and_search_increases_depth_preference() -> None:
    """High search:view ratio should push depth_preference upward."""
    profile = OnionProfile()
    profile.surface.style.depth_preference = 0.5

    signals = [
        {"payload": {"event_type": "view", "title": "v1"}},
        {"payload": {"event_type": "view", "title": "v2"}},
        {"payload": {"event_type": "view", "title": "v3"}},
        {"payload": {"event_type": "search", "title": "AI 原理"}},
        {"payload": {"event_type": "search", "title": "深度学习"}},
        {"payload": {"event_type": "search", "title": "transformer"}},
    ]
    result = await _update_surface(signals=signals, profile=profile)

    assert result.changed, f"Surface should change with view+search mix: {result.changes}"
    assert profile.surface.style.depth_preference > 0.5, (
        f"depth_preference should increase. Got {profile.surface.style.depth_preference}"
    )
    assert any("depth_preference" in c for c in result.changes)


@pytest.mark.asyncio
async def test_surface_no_change_when_view_count_below_threshold() -> None:
    """view_count<2 must NOT modify the profile (computation skipped)."""
    profile = OnionProfile()
    original_depth = profile.surface.style.depth_preference

    signals = [{"payload": {"event_type": "view", "title": "only one"}}]
    result = await _update_surface(signals=signals, profile=profile)

    assert not result.changed
    assert profile.surface.style.depth_preference == original_depth


@pytest.mark.asyncio
async def test_surface_no_change_when_delta_under_threshold() -> None:
    """If computed delta is < 0.05, do not write the change."""
    profile = OnionProfile()
    # Set to a value where the computed new_depth is very close
    profile.surface.style.depth_preference = 0.5

    # Pure views, no searches → depth_signal = 0.5
    # new_depth = 0.5*0.7 + 0.5*0.3 = 0.5 → delta=0 → no change
    signals = [{"payload": {"event_type": "view", "title": f"v{i}"}} for i in range(3)]
    result = await _update_surface(signals=signals, profile=profile)

    assert not result.changed, f"With 0 delta, surface should NOT change. Got: {result.changes}"
    assert profile.surface.style.depth_preference == 0.5


# ===========================================================================
# 6. Speculator integration tests
# ===========================================================================


class _SpeculatorSpy:
    """Minimal speculator stub that records observe/tick calls."""

    def __init__(self) -> None:
        self.observe_calls: list[list[dict[str, Any]]] = []
        self.tick_called = False

    def observe(self, events: list[dict[str, Any]]) -> int:
        self.observe_calls.append(events)
        return len(events)

    async def tick(self, profile: Any) -> Any:
        self.tick_called = True

        class _R:
            promoted: list[Any] = []
            rejected: list[Any] = []
            generated: list[Any] = []

        return _R()


@pytest.mark.asyncio
async def test_speculator_observe_called_on_ingest(tmp_path: Path) -> None:
    """ingest_batch should pass payloads to speculator.observe()."""
    spy = _SpeculatorSpy()
    pipeline, _, _ = _make_low_threshold_pipeline(tmp_path, speculator=spy)

    await pipeline.ingest(signals_from_events([{"event_type": "view", "title": "AI视频"}])[0])

    assert len(spy.observe_calls) == 1
    assert spy.observe_calls[0][0]["title"] == "AI视频"


@pytest.mark.asyncio
async def test_speculator_tick_called_during_pipeline_tick(tmp_path: Path) -> None:
    """pipeline.tick() should also trigger speculator.tick()."""
    spy = _SpeculatorSpy()
    pipeline, _, _ = _make_low_threshold_pipeline(tmp_path, speculator=spy)

    await pipeline.tick()
    assert spy.tick_called, "pipeline.tick() must invoke speculator.tick()"


@pytest.mark.asyncio
async def test_tick_if_buffered_skips_empty_periodic_maintenance(tmp_path: Path) -> None:
    """Event-owner empty recovery is O(1) and never invokes speculator work."""
    spy = _SpeculatorSpy()
    pipeline, _, _ = _make_low_threshold_pipeline(tmp_path, speculator=spy)

    result = await pipeline.tick_if_buffered()

    assert result.layers_updated == []
    assert spy.tick_called is False


def test_speculator_idle_interval_is_configurable(tmp_path: Path) -> None:
    spy = _SpeculatorSpy()
    pipeline, _, _ = _make_low_threshold_pipeline(
        tmp_path,
        speculator=spy,
        speculator_idle_interval_minutes=11,
    )

    assert pipeline._speculator_idle_min_interval == timedelta(minutes=11)


@pytest.mark.asyncio
async def test_speculator_promotion_records_layer_update(tmp_path: Path) -> None:
    """Promoted speculations should appear in tick result as INTEREST updates."""

    class _PromotingSpec:
        def __init__(self) -> None:
            self.domain = "AI伦理"
            self.confirmation_count = 5
            self.created_at = datetime.now().isoformat()

    class _PromotingSpeculator(_SpeculatorSpy):
        async def tick(self, profile: Any) -> Any:
            self.tick_called = True

            class _R:
                promoted = [_PromotingSpec()]
                rejected: list[Any] = []
                generated: list[Any] = []

            return _R()

    spy = _PromotingSpeculator()
    pipeline, _, memory = _make_low_threshold_pipeline(tmp_path, speculator=spy)

    flush_result = await pipeline.tick()
    promoted_updates = [r for r in flush_result.layers_updated if r.trigger == "猜测兴趣确认"]
    assert promoted_updates, "Promoted speculation must produce a LayerUpdateResult"
    assert promoted_updates[0].layer == OnionLayer.INTEREST
    assert "AI伦理" in promoted_updates[0].changes[0]


@pytest.mark.asyncio
async def test_confirm_speculation_promotes_with_source_weight(tmp_path: Path) -> None:
    """Confirmed speculation source should control written interest weight."""

    class _PromotingSpec:
        def __init__(self) -> None:
            self.domain = "建筑美学"
            self.confirmation_count = 3
            self.created_at = "2026-05-24T12:00:00"
            self.confirmed_at = "2026-05-24T12:30:00"
            self.confirmation_source = "profile_confirmed"
            self.specifics: list[Any] = []

    class _PromotingSpeculator(_SpeculatorSpy):
        async def tick(self, profile: Any) -> Any:
            self.tick_called = True

            class _R:
                promoted = [_PromotingSpec()]
                rejected: list[Any] = []
                generated: list[Any] = []

            return _R()

    spy = _PromotingSpeculator()
    pipeline, _, memory = _make_low_threshold_pipeline(tmp_path, speculator=spy)

    await pipeline.tick()

    profile = OnionProfile.from_dict(memory.get_layer("soul").data)
    promoted = next(item for item in profile.interest.likes if item.domain == "建筑美学")
    assert promoted.source == "profile_confirmed"
    assert promoted.weight == 0.60


@pytest.mark.asyncio
async def test_avoidance_speculator_observe_called_on_ingest(tmp_path: Path) -> None:
    """ingest_batch should pass payloads to the avoidance speculator too."""
    spy = _SpeculatorSpy()
    pipeline, _, _ = _make_low_threshold_pipeline(tmp_path, avoidance_speculator=spy)

    await pipeline.ingest(signals_from_events([{"event_type": "dislike", "title": "标题党"}])[0])

    assert len(spy.observe_calls) == 1
    assert spy.observe_calls[0][0]["title"] == "标题党"


@pytest.mark.asyncio
async def test_pipeline_auto_promoted_avoidance_uses_apply_new_dislikes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Observe-driven avoidance promotion must use the shared dislike writeback."""
    from openbiliclaw.soul import pipeline as pipeline_mod
    from openbiliclaw.soul.avoidance_speculator import (
        SpeculativeAvoidance,
        SpeculativeAvoidanceSpecific,
    )

    class _PromotingAvoidanceSpeculator(_SpeculatorSpy):
        async def tick(self, profile: Any, *, feedback_history: object | None = None) -> Any:
            self.tick_called = True

            class _R:
                promoted = [
                    SpeculativeAvoidance(
                        domain="浅层热点复读",
                        confirmation_count=3,
                        confirmation_threshold=3,
                        specifics=[
                            SpeculativeAvoidanceSpecific(name="标题党热点解读"),
                            SpeculativeAvoidanceSpecific(name="无信息增量复读"),
                        ],
                    )
                ]
                rejected: list[Any] = []
                generated: list[Any] = []

            return _R()

    calls: list[dict[str, Any]] = []

    async def fake_apply_new_dislikes(**kwargs: Any) -> list[str]:
        calls.append(kwargs)
        return [f"新增不喜欢方向: {topic}" for topic in kwargs["topics"]]

    monkeypatch.setattr(pipeline_mod, "apply_new_dislikes", fake_apply_new_dislikes, raising=False)

    spy = _PromotingAvoidanceSpeculator()
    pipeline, _, _ = _make_low_threshold_pipeline(tmp_path, avoidance_speculator=spy)

    flush_result = await pipeline.tick()

    assert spy.tick_called
    assert [call["topics"] for call in calls] == [["标题党热点解读", "无信息增量复读"]]
    promoted_updates = [r for r in flush_result.layers_updated if r.trigger == "避雷方向确认"]
    assert promoted_updates, "Promoted avoidance must produce a LayerUpdateResult"
    assert promoted_updates[0].layer == OnionLayer.INTEREST
    assert "标题党热点解读" in promoted_updates[0].changes[0]


# ===========================================================================
# 7. Boundary / routing edge cases
# ===========================================================================


def test_classify_dialogue_insight_unknown_kind_defaults_to_interest() -> None:
    """Unknown DIALOGUE_INSIGHT kind should fall back to INTEREST routing."""
    layers = classify_signal(
        SignalType.DIALOGUE_INSIGHT,
        {"kind": "totally_made_up", "content": "x"},
    )
    assert layers == frozenset({OnionLayer.INTEREST})


def test_classify_dialogue_insight_missing_kind_defaults_to_interest() -> None:
    """Missing 'kind' field should also fall back to INTEREST."""
    layers = classify_signal(SignalType.DIALOGUE_INSIGHT, {"content": "no kind here"})
    assert layers == frozenset({OnionLayer.INTEREST})


def test_default_thresholds_cover_all_buffered_layers() -> None:
    """DEFAULT_THRESHOLDS must define a threshold for every buffered layer."""
    for layer in _BUFFERED_LAYERS:
        assert layer in DEFAULT_THRESHOLDS, f"Missing default threshold for {layer}"


@pytest.mark.asyncio
async def test_dialogue_insight_unknown_kind_routes_to_interest(tmp_path: Path) -> None:
    """End-to-end: an unknown-kind insight should still update INTEREST."""
    pipeline, _, _ = _make_low_threshold_pipeline(tmp_path)
    sig = signals_from_dialogue(
        [{"kind": "garbage_kind", "content": "用户提到AI技术", "confidence": 0.9}]
    )[0]
    result = await pipeline.ingest(sig)

    assert "interest" in result.layers_buffered
    layers_updated = {r.layer for r in result.layers_updated}
    assert OnionLayer.INTEREST in layers_updated


# ===========================================================================
# 8. Layer updater edge cases (round out coverage)
# ===========================================================================


@pytest.mark.asyncio
async def test_update_layer_dispatch_unknown_layer_returns_unchanged(tmp_path: Path) -> None:
    """update_layer with PORTRAIT (not in dispatch table) returns changed=False."""
    from openbiliclaw.soul.layer_updaters import update_layer

    memory = MemoryManager(Path(tmp_path))
    memory.initialize()
    svc = _RichFakeService()
    profile = OnionProfile()

    # PORTRAIT is not in _LAYER_UPDATERS — should hit the None branch
    result = await update_layer(
        layer=OnionLayer.PORTRAIT,
        signals=[{"payload": {"event_type": "view", "title": "x"}}],
        profile=profile,
        memory=memory,
        preference_analyzer=PreferenceAnalyzer(registry=svc),
        profile_builder=ProfileBuilder(registry=svc),
    )
    assert result.layer == OnionLayer.PORTRAIT
    assert result.changed is False


@pytest.mark.asyncio
async def test_update_interest_with_empty_signals_returns_unchanged(tmp_path: Path) -> None:
    """_update_interest with no extractable events should return early."""
    from openbiliclaw.soul.layer_updaters import _update_interest

    memory = MemoryManager(Path(tmp_path))
    memory.initialize()
    svc = _RichFakeService()
    profile = OnionProfile()

    # Signals with non-dict payload → events list will be empty
    result = await _update_interest(
        signals=[{"payload": "not-a-dict"}, {"payload": None}],
        profile=profile,
        memory=memory,
        preference_analyzer=PreferenceAnalyzer(registry=svc),
        profile_builder=ProfileBuilder(registry=svc),
    )
    assert result.layer == OnionLayer.INTEREST
    assert result.changed is False


@pytest.mark.asyncio
async def test_update_interest_handles_analyzer_exception(tmp_path: Path) -> None:
    """_update_interest should swallow PreferenceAnalyzer exceptions."""
    from openbiliclaw.soul.layer_updaters import _update_interest

    memory = MemoryManager(Path(tmp_path))
    memory.initialize()
    raising_svc = _RaisingFakeService()
    profile = OnionProfile()

    result = await _update_interest(
        signals=[{"payload": {"event_type": "view", "title": "崩坏"}}],
        profile=profile,
        memory=memory,
        preference_analyzer=PreferenceAnalyzer(registry=raising_svc),
        profile_builder=ProfileBuilder(registry=raising_svc),
    )
    assert result.changed is False


@pytest.mark.asyncio
async def test_update_role_with_empty_evidence_returns_unchanged(tmp_path: Path) -> None:
    """_update_role with no title/content should return early without LLM call."""
    from openbiliclaw.soul.layer_updaters import _update_role

    memory = MemoryManager(Path(tmp_path))
    memory.initialize()
    svc = _RichFakeService()
    profile = OnionProfile()

    result = await _update_role(
        signals=[{"payload": {"event_type": "view"}}],  # no title, no content
        profile=profile,
        memory=memory,
        preference_analyzer=PreferenceAnalyzer(registry=svc),
        profile_builder=ProfileBuilder(registry=svc),
    )
    assert result.layer == OnionLayer.ROLE
    assert result.changed is False
    # No LLM call should have been made
    assert len(svc.calls) == 0


@pytest.mark.asyncio
async def test_update_role_handles_llm_exception(tmp_path: Path) -> None:
    """_update_role should catch LLM exceptions and return changed=False."""
    from openbiliclaw.soul.layer_updaters import _update_role

    memory = MemoryManager(Path(tmp_path))
    memory.initialize()
    raising = _RaisingFakeService()
    profile = OnionProfile()

    result = await _update_role(
        signals=[{"payload": {"event_type": "view", "title": "崩"}}],
        profile=profile,
        memory=memory,
        preference_analyzer=PreferenceAnalyzer(registry=raising),
        profile_builder=ProfileBuilder(registry=raising),
    )
    assert result.changed is False
    assert result.signals_consumed == 1


@pytest.mark.asyncio
async def test_update_values_with_empty_evidence_returns_unchanged(tmp_path: Path) -> None:
    """_update_values with no extractable evidence should return early."""
    from openbiliclaw.soul.layer_updaters import _update_values

    memory = MemoryManager(Path(tmp_path))
    memory.initialize()
    svc = _RichFakeService()
    profile = OnionProfile()

    result = await _update_values(
        signals=[{"payload": {"event_type": "view"}}],
        profile=profile,
        memory=memory,
        preference_analyzer=PreferenceAnalyzer(registry=svc),
        profile_builder=ProfileBuilder(registry=svc),
    )
    assert result.changed is False
    assert len(svc.calls) == 0


@pytest.mark.asyncio
async def test_update_values_handles_llm_exception(tmp_path: Path) -> None:
    """_update_values should catch LLM exceptions."""
    from openbiliclaw.soul.layer_updaters import _update_values

    memory = MemoryManager(Path(tmp_path))
    memory.initialize()
    raising = _RaisingFakeService()
    profile = OnionProfile()

    result = await _update_values(
        signals=[{"payload": {"event_type": "feedback", "title": "评论内容"}}],
        profile=profile,
        memory=memory,
        preference_analyzer=PreferenceAnalyzer(registry=raising),
        profile_builder=ProfileBuilder(registry=raising),
    )
    assert result.changed is False


@pytest.mark.asyncio
async def test_update_values_injects_user_context_from_profile(tmp_path: Path) -> None:
    """_update_values should prepend a 【用户背景】 line when profile has role/interests."""
    from openbiliclaw.soul.layer_updaters import _update_values
    from openbiliclaw.soul.profile import InterestDomain

    memory = MemoryManager(Path(tmp_path))
    memory.initialize()

    captured_inputs: list[str] = []

    class _CaptureService:
        async def complete_structured_task(
            self,
            *,
            system_instruction: str,
            user_input: str,
            **_: Any,
        ) -> LLMResponse:
            captured_inputs.append(user_input)
            return LLMResponse(content=_VALUES_CHANGED_RESP, provider="fake")

    svc = _CaptureService()
    profile = OnionProfile()
    profile.role.life_stage = "学生"
    profile.role.current_phase = "毕业季"
    profile.interest.likes = [
        InterestDomain(domain="AI", weight=0.9, source="events"),
        InterestDomain(domain="哲学", weight=0.7, source="events"),
    ]

    result = await _update_values(
        signals=[{"payload": {"event_type": "view", "title": "AI伦理思辨"}}],
        profile=profile,
        memory=memory,
        preference_analyzer=PreferenceAnalyzer(registry=svc),
        profile_builder=ProfileBuilder(registry=svc),
    )
    assert result.changed
    # The user_input should contain context block with role + interests
    combined = " ".join(captured_inputs)
    assert "用户背景" in combined
    assert "学生" in combined
    assert "AI" in combined


@pytest.mark.asyncio
async def test_update_core_with_empty_evidence_returns_unchanged(tmp_path: Path) -> None:
    """_update_core with no extractable evidence should return early."""
    from openbiliclaw.soul.layer_updaters import _update_core

    memory = MemoryManager(Path(tmp_path))
    memory.initialize()
    svc = _RichFakeService()
    profile = OnionProfile()

    result = await _update_core(
        signals=[{"payload": {"event_type": "view"}}],
        profile=profile,
        memory=memory,
        preference_analyzer=PreferenceAnalyzer(registry=svc),
        profile_builder=ProfileBuilder(registry=svc),
    )
    assert result.changed is False
    assert len(svc.calls) == 0


@pytest.mark.asyncio
async def test_update_core_handles_llm_exception(tmp_path: Path) -> None:
    """_update_core should catch LLM exceptions."""
    from openbiliclaw.soul.layer_updaters import _update_core

    memory = MemoryManager(Path(tmp_path))
    memory.initialize()
    raising = _RaisingFakeService()
    profile = OnionProfile()

    result = await _update_core(
        signals=[{"payload": {"event_type": "view", "title": "深度内容"}}],
        profile=profile,
        memory=memory,
        preference_analyzer=PreferenceAnalyzer(registry=raising),
        profile_builder=ProfileBuilder(registry=raising),
    )
    assert result.changed is False


@pytest.mark.asyncio
async def test_update_core_includes_existing_mbti_in_prompt(tmp_path: Path) -> None:
    """When profile has MBTI, _update_core should serialize it into the prompt."""
    from openbiliclaw.soul.layer_updaters import _update_core
    from openbiliclaw.soul.profile import MBTI, MBTIDimension

    memory = MemoryManager(Path(tmp_path))
    memory.initialize()

    captured_inputs: list[str] = []

    class _CaptureService:
        async def complete_structured_task(
            self,
            *,
            system_instruction: str,
            user_input: str,
            **_: Any,
        ) -> LLMResponse:
            captured_inputs.append(user_input)
            return LLMResponse(content=_CORE_CHANGED_RESP, provider="fake")

    svc = _CaptureService()
    profile = OnionProfile()
    profile.core.mbti = MBTI(
        type="INTP",
        confidence=0.7,
        dimensions={"EI": MBTIDimension(pole="I", strength=0.8)},
    )

    result = await _update_core(
        signals=[{"payload": {"event_type": "view", "title": "深度思考"}}],
        profile=profile,
        memory=memory,
        preference_analyzer=PreferenceAnalyzer(registry=svc),
        profile_builder=ProfileBuilder(registry=svc),
    )
    assert result.changed
    # MBTI INTJ from response should have replaced INTP
    assert profile.core.mbti.type == "INTJ"
    # Original INTP should be present in the prompt user_input
    combined = " ".join(captured_inputs)
    assert "INTP" in combined


@pytest.mark.asyncio
async def test_update_interest_syncs_cognitive_style(tmp_path: Path) -> None:
    """cognitive_style from PreferenceAnalyzer should write directly to surface."""
    from openbiliclaw.soul.layer_updaters import _update_interest

    memory = MemoryManager(Path(tmp_path))
    memory.initialize()
    svc = _RichFakeService()
    profile = OnionProfile()

    await _update_interest(
        signals=[{"payload": {"event_type": "view", "title": "AI"}}],
        profile=profile,
        memory=memory,
        preference_analyzer=PreferenceAnalyzer(registry=svc),
        profile_builder=ProfileBuilder(registry=svc),
    )
    # _PREF_RESP includes cognitive_style: ["系统化思考"]
    assert "系统化思考" in profile.surface.cognitive_style


@pytest.mark.asyncio
async def test_update_interest_ingests_speculative_seeds(tmp_path: Path) -> None:
    """speculative_interests from analyzer should be fed to InterestSpeculator."""
    from openbiliclaw.soul.layer_updaters import _update_interest
    from openbiliclaw.soul.speculator import load_speculative_state

    memory = MemoryManager(Path(tmp_path))
    memory.initialize()

    class _SeedingService:
        async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
            payload = json.loads(_PREF_RESP)
            # PreferenceAnalyzer normalizes speculative_interests on the
            # 'name' field (not 'domain'); ingest_seeds() then accepts either.
            payload["speculative_interests"] = [
                {"name": "量子计算", "category": "知识", "weight": 0.5},
                {"name": "认知科学", "category": "知识", "weight": 0.5},
            ]
            return LLMResponse(content=json.dumps(payload), provider="fake")

    svc = _SeedingService()
    profile = OnionProfile()

    result = await _update_interest(
        signals=[{"payload": {"event_type": "view", "title": "AI"}}],
        profile=profile,
        memory=memory,
        preference_analyzer=PreferenceAnalyzer(registry=svc),
        profile_builder=ProfileBuilder(registry=svc),
    )
    assert any("猜测兴趣种子" in c for c in result.changes), result.changes

    state = load_speculative_state(Path(tmp_path))
    domains = {s.domain for s in state.active}
    assert "量子计算" in domains
    assert "认知科学" in domains


@pytest.mark.asyncio
async def test_update_interest_dedupes_speculative_seeds_against_profile(
    tmp_path: Path,
) -> None:
    """PreferenceAnalyzer seeds should not restate existing profile specifics."""
    from openbiliclaw.soul.layer_updaters import _update_interest
    from openbiliclaw.soul.profile import InterestDomain, InterestLayer, InterestSpecific
    from openbiliclaw.soul.speculator import load_speculative_state

    memory = MemoryManager(Path(tmp_path))
    memory.initialize()

    class _SeedingService:
        async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
            payload = json.loads(_PREF_RESP)
            payload["speculative_interests"] = [
                {"name": "ComfyUI工作流拆解", "category": "AI", "weight": 0.5},
                {"name": "量子计算", "category": "知识", "weight": 0.5},
            ]
            return LLMResponse(content=json.dumps(payload), provider="fake")

    svc = _SeedingService()
    profile = OnionProfile(
        interest=InterestLayer(
            likes=[
                InterestDomain(
                    domain="AI",
                    specifics=[InterestSpecific(name="ComfyUI工作流")],
                )
            ]
        )
    )

    await _update_interest(
        signals=[{"payload": {"event_type": "view", "title": "AI"}}],
        profile=profile,
        memory=memory,
        preference_analyzer=PreferenceAnalyzer(registry=svc),
        profile_builder=ProfileBuilder(registry=svc),
    )

    state = load_speculative_state(Path(tmp_path))
    domains = {s.domain for s in state.active}
    assert "量子计算" in domains
    assert "ComfyUI工作流拆解" not in domains


@pytest.mark.asyncio
async def test_update_interest_detects_dislike_changes(tmp_path: Path) -> None:
    """New disliked_topics from analyzer should produce '新增讨厌' changes."""
    from openbiliclaw.soul.layer_updaters import _update_interest

    memory = MemoryManager(Path(tmp_path))
    memory.initialize()

    class _DislikeService:
        async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
            payload = json.loads(_PREF_RESP)
            payload["disliked_topics"] = ["标题党", "营销号"]
            return LLMResponse(content=json.dumps(payload), provider="fake")

    svc = _DislikeService()
    profile = OnionProfile()

    result = await _update_interest(
        signals=[{"payload": {"event_type": "view", "title": "v"}}],
        profile=profile,
        memory=memory,
        preference_analyzer=PreferenceAnalyzer(registry=svc),
        profile_builder=ProfileBuilder(registry=svc),
    )
    assert any("新增讨厌" in c for c in result.changes), result.changes


@pytest.mark.asyncio
async def test_apply_new_dislikes_persists_preference_and_soul_profile(tmp_path: Path) -> None:
    from openbiliclaw.soul.dislike_writeback import apply_new_dislikes

    memory = MemoryManager(Path(tmp_path))
    memory.initialize()
    profile = OnionProfile()
    soul_layer = memory.get_layer("soul")
    soul_layer.data.update(profile.to_dict())
    soul_layer.save()

    changes = await apply_new_dislikes(
        memory=memory,
        database=memory._database,
        embedding_service=None,
        llm_service=None,
        topics=["标题党", "标题党"],
    )

    assert memory.get_layer("preference").data["disliked_topics"] == ["标题党"]
    saved_profile = OnionProfile.from_dict(memory.get_layer("soul").data)
    assert [item.domain for item in saved_profile.interest.dislikes] == ["标题党"]
    assert "新增讨厌: 标题党" in changes


@pytest.mark.asyncio
async def test_purge_pool_for_new_dislikes_invokes_existing_pool_purge_paths(
    tmp_path: Path,
) -> None:
    from openbiliclaw.soul.dislike_writeback import purge_pool_for_new_dislikes

    memory = MemoryManager(Path(tmp_path))
    memory.initialize()
    db = memory._database
    db.cache_content(
        "BVtitlebait",
        title="标题党热点复读",
        up_name="热点UP",
        source="trending",
        topic_key="热点",
    )

    changes = await purge_pool_for_new_dislikes(
        database=db,
        embedding_service=None,
        llm_service=None,
        newly_added=["标题党"],
        all_dislikes=["标题党"],
    )

    rows = {row["bvid"]: row for row in db.get_cached_content(limit=10)}
    assert rows["BVtitlebait"]["pool_status"] == "purged_by_dislike"
    assert any("从候选池清除" in item for item in changes)


@pytest.mark.asyncio
async def test_layer_updater_uses_purge_helper_without_rewriting_preference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openbiliclaw.soul.layer_updaters as lu_mod
    from openbiliclaw.soul.layer_updaters import _update_interest

    calls: list[dict[str, object]] = []

    async def fake_purge_pool_for_new_dislikes(**kwargs: object) -> list[str]:
        calls.append(dict(kwargs))
        return ["从候选池清除 1 条相关内容"]

    async def forbidden_apply_new_dislikes(**kwargs: object) -> list[str]:
        raise AssertionError("analyzer path must not call apply_new_dislikes")

    monkeypatch.setattr(lu_mod, "purge_pool_for_new_dislikes", fake_purge_pool_for_new_dislikes)
    monkeypatch.setattr(lu_mod, "apply_new_dislikes", forbidden_apply_new_dislikes, raising=False)

    memory = MemoryManager(Path(tmp_path))
    memory.initialize()

    class _DislikeService:
        async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
            payload = json.loads(_PREF_RESP)
            payload["disliked_topics"] = ["标题党"]
            return LLMResponse(content=json.dumps(payload), provider="fake")

    result = await _update_interest(
        signals=[{"payload": {"event_type": "view", "title": "v"}}],
        profile=OnionProfile(),
        memory=memory,
        preference_analyzer=PreferenceAnalyzer(registry=_DislikeService()),
        profile_builder=ProfileBuilder(registry=_DislikeService()),
    )

    assert calls
    assert calls[0]["newly_added"] == ["标题党"]
    assert calls[0]["all_dislikes"] == ["标题党"]
    assert "从候选池清除 1 条相关内容" in result.changes


@pytest.mark.asyncio
async def test_new_dislike_purges_matching_pool_candidates(tmp_path: Path) -> None:
    """End-to-end: when a new dislike is learned, matching pool items must
    be moved to pool_status='purged_by_dislike' and a change line recorded.
    """
    from openbiliclaw.soul.layer_updaters import _update_interest

    memory = MemoryManager(Path(tmp_path))
    memory.initialize()

    # Seed the candidate pool with a mix of content: some matches "鬼畜",
    # some are unrelated knowledge content.
    db = memory._database
    db.cache_content(
        "BVghost1",
        title="鬼畜全明星混剪",
        up_name="鬼畜UP",
        source="trending",
        topic_key="鬼畜",
        pool_topic_label="娱乐",
    )
    db.cache_content(
        "BVghost2",
        title="跨年鬼畜合集",
        up_name="混剪UP",
        source="explore",
        topic_key="年终",  # title substring match
        pool_topic_label="娱乐",
    )
    db.cache_content(
        "BVkeep",
        title="Transformer深度解析",
        up_name="AI教程君",
        source="search",
        topic_key="AI技术",
        pool_topic_label="知识",
    )

    class _DislikeService:
        async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
            payload = json.loads(_PREF_RESP)
            payload["disliked_topics"] = ["鬼畜"]
            return LLMResponse(content=json.dumps(payload), provider="fake")

    svc = _DislikeService()
    profile = OnionProfile()

    result = await _update_interest(
        signals=[{"payload": {"event_type": "view", "title": "v"}}],
        profile=profile,
        memory=memory,
        preference_analyzer=PreferenceAnalyzer(registry=svc),
        profile_builder=ProfileBuilder(registry=svc),
    )

    # Report line must mention the purge
    joined_changes = " ".join(result.changes)
    assert "新增讨厌: 鬼畜" in joined_changes, result.changes
    assert "从候选池清除" in joined_changes, f"Expected purge change line. Got: {result.changes}"

    # Verify DB state
    rows = {row["bvid"]: row for row in db.get_cached_content(limit=10)}
    assert rows["BVghost1"]["pool_status"] == "purged_by_dislike"
    assert rows["BVghost2"]["pool_status"] == "purged_by_dislike"
    assert rows["BVkeep"]["pool_status"] == "fresh"


@pytest.mark.asyncio
async def test_unchanged_dislikes_do_not_trigger_purge(tmp_path: Path) -> None:
    """If disliked_topics list is identical to before, no purge should run."""
    from openbiliclaw.soul.layer_updaters import _update_interest

    memory = MemoryManager(Path(tmp_path))
    memory.initialize()

    # Pre-seed an existing dislike so the analyzer's response looks unchanged
    memory.get_layer("preference").data.update(
        {
            "disliked_topics": ["鬼畜"],
        }
    )

    db = memory._database
    db.cache_content(
        "BVghost",
        title="鬼畜经典",
        up_name="鬼畜UP",
        source="trending",
        topic_key="鬼畜",
    )

    class _StableDislikeService:
        async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
            payload = json.loads(_PREF_RESP)
            payload["disliked_topics"] = ["鬼畜"]  # same as before
            return LLMResponse(content=json.dumps(payload), provider="fake")

    svc = _StableDislikeService()
    profile = OnionProfile()

    result = await _update_interest(
        signals=[{"payload": {"event_type": "view", "title": "v"}}],
        profile=profile,
        memory=memory,
        preference_analyzer=PreferenceAnalyzer(registry=svc),
        profile_builder=ProfileBuilder(registry=svc),
    )
    joined = " ".join(result.changes)
    assert "新增讨厌" not in joined, "No new dislikes → no change line expected"
    assert "从候选池清除" not in joined, "No new dislikes → no purge should happen"

    # BVghost should still be fresh because purge was not triggered
    rows = db.get_cached_content(limit=10)
    assert rows[0]["pool_status"] == "fresh"


@pytest.mark.asyncio
async def test_purge_failure_does_not_break_interest_update(
    tmp_path: Path,
) -> None:
    """If purge raises, the interest update must still complete successfully."""
    from openbiliclaw.soul.layer_updaters import _update_interest

    memory = MemoryManager(Path(tmp_path))
    memory.initialize()

    # Install a broken purge that raises
    original_purge = memory._database.purge_pool_by_disliked_topics

    def broken_purge(*args: Any, **kwargs: Any) -> int:
        raise RuntimeError("db purge broken")

    memory._database.purge_pool_by_disliked_topics = broken_purge  # type: ignore[method-assign]

    class _DislikeService:
        async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
            payload = json.loads(_PREF_RESP)
            payload["disliked_topics"] = ["标题党"]
            return LLMResponse(content=json.dumps(payload), provider="fake")

    svc = _DislikeService()
    profile = OnionProfile()

    try:
        result = await _update_interest(
            signals=[{"payload": {"event_type": "view", "title": "v"}}],
            profile=profile,
            memory=memory,
            preference_analyzer=PreferenceAnalyzer(registry=svc),
            profile_builder=ProfileBuilder(registry=svc),
        )
    finally:
        memory._database.purge_pool_by_disliked_topics = original_purge  # type: ignore[method-assign]

    # Interest update should still report the dislike change
    assert any("新增讨厌" in c for c in result.changes)
    # But no purge-count line
    assert not any("从候选池清除" in c for c in result.changes)


# ===========================================================================
# 10. Semantic (embedding-based) pool purge
# ===========================================================================


class _FakeEmbeddingService:
    """Deterministic embedding stub for testing semantic purge.

    Maps a fixed vocabulary of Chinese keywords to orthogonal unit vectors
    plus a "semantically adjacent" partial overlap so we can control exact
    similarity scores without needing a real model.
    """

    def __init__(
        self,
        *,
        similarity_threshold: float = 0.78,
        vocabulary: dict[str, list[float]] | None = None,
    ) -> None:
        self.similarity_threshold = similarity_threshold
        self._vocab = vocabulary or {}
        self.embed_calls: list[str] = []

    async def embed(self, text: str) -> list[float]:
        self.embed_calls.append(text)
        # Use substring matching against the vocabulary keys so a single
        # fake entry covers many phrasings (e.g. "鬼畜" matches "鬼畜" and
        # "鬼畜全明星").
        text_lower = text.lower()
        for key, vec in self._vocab.items():
            if key.lower() in text_lower:
                return list(vec)
        # Unknown text → orthogonal-ish noise
        return [0.0, 0.0, 0.0, 1.0]


@pytest.mark.asyncio
async def test_semantic_purge_catches_semantically_close_candidates(
    tmp_path: Path,
) -> None:
    """Semantic purge should catch candidates the string-match missed."""
    from openbiliclaw.soul.layer_updaters import _update_interest

    memory = MemoryManager(Path(tmp_path))
    memory.initialize()
    db = memory._database

    # This candidate would be missed by string-match (no "鬼畜" in any field)
    # but is semantically close in our fake vocab.
    db.cache_content(
        "BVsemantic",
        title="沙雕视频鉴赏合集",
        up_name="沙雕UP",
        source="trending",
        topic_key="搞笑视频",
        pool_topic_label="娱乐",
    )
    # Unrelated candidate — orthogonal in vocab, should survive.
    db.cache_content(
        "BVkeep",
        title="Transformer原理",
        up_name="AI教程",
        source="search",
        topic_key="AI技术",
        pool_topic_label="知识",
    )

    # Fake embedding: both "鬼畜" and "沙雕" map to the same direction,
    # "AI技术" maps to an orthogonal direction. Similarity of co-located
    # vectors = 1.0 > 0.78 threshold → purge.
    fake_embed = _FakeEmbeddingService(
        similarity_threshold=0.78,
        vocabulary={
            "鬼畜": [1.0, 0.0, 0.0, 0.0],
            "沙雕": [1.0, 0.0, 0.0, 0.0],
            "AI技术": [0.0, 0.0, 1.0, 0.0],
            "Transformer": [0.0, 0.0, 1.0, 0.0],
        },
    )

    class _DislikeService:
        async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
            payload = json.loads(_PREF_RESP)
            payload["disliked_topics"] = ["鬼畜"]
            return LLMResponse(content=json.dumps(payload), provider="fake")

    profile = OnionProfile()
    svc = _DislikeService()

    result = await _update_interest(
        signals=[{"payload": {"event_type": "view", "title": "v"}}],
        profile=profile,
        memory=memory,
        preference_analyzer=PreferenceAnalyzer(registry=svc),
        profile_builder=ProfileBuilder(registry=svc),
        embedding_service=fake_embed,
    )

    joined = " ".join(result.changes)
    assert "从候选池语义清除" in joined, (
        f"Expected semantic purge change line. Got: {result.changes}"
    )

    rows = {row["bvid"]: row for row in db.get_cached_content(limit=10)}
    assert rows["BVsemantic"]["pool_status"] == "purged_by_dislike"
    assert rows["BVkeep"]["pool_status"] == "fresh"


@pytest.mark.asyncio
async def test_semantic_purge_respects_threshold(tmp_path: Path) -> None:
    """Candidates below the similarity threshold should not be purged."""
    from openbiliclaw.soul.layer_updaters import _update_interest

    memory = MemoryManager(Path(tmp_path))
    memory.initialize()
    db = memory._database
    db.cache_content(
        "BVweakly",
        title="只是一般的视频",
        up_name="普通UP",
        source="search",
        topic_key="日常",
        pool_topic_label="生活",
    )

    # Vocabulary where the dislike topic and candidate have LOW similarity
    # (different directions, below threshold).
    fake_embed = _FakeEmbeddingService(
        similarity_threshold=0.78,
        vocabulary={
            "鬼畜": [1.0, 0.0, 0.0, 0.0],
            "只是一般的视频": [0.1, 0.95, 0.0, 0.0],  # ~0.1 cosine with 鬼畜
        },
    )

    class _DislikeService:
        async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
            payload = json.loads(_PREF_RESP)
            payload["disliked_topics"] = ["鬼畜"]
            return LLMResponse(content=json.dumps(payload), provider="fake")

    profile = OnionProfile()
    svc = _DislikeService()

    result = await _update_interest(
        signals=[{"payload": {"event_type": "view", "title": "v"}}],
        profile=profile,
        memory=memory,
        preference_analyzer=PreferenceAnalyzer(registry=svc),
        profile_builder=ProfileBuilder(registry=svc),
        embedding_service=fake_embed,
    )
    joined = " ".join(result.changes)
    assert "从候选池语义清除" not in joined

    rows = db.get_cached_content(limit=10)
    assert rows[0]["pool_status"] == "fresh"


@pytest.mark.asyncio
async def test_semantic_purge_skipped_without_embedding_service(
    tmp_path: Path,
) -> None:
    """Without an embedding_service, only string-match purge runs."""
    from openbiliclaw.soul.layer_updaters import _update_interest

    memory = MemoryManager(Path(tmp_path))
    memory.initialize()
    db = memory._database
    db.cache_content(
        "BVsemantic",
        title="沙雕视频",
        up_name="u",
        source="s",
        topic_key="搞笑",
    )

    class _DislikeService:
        async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
            payload = json.loads(_PREF_RESP)
            payload["disliked_topics"] = ["鬼畜"]  # no literal match
            return LLMResponse(content=json.dumps(payload), provider="fake")

    profile = OnionProfile()
    svc = _DislikeService()
    result = await _update_interest(
        signals=[{"payload": {"event_type": "view", "title": "v"}}],
        profile=profile,
        memory=memory,
        preference_analyzer=PreferenceAnalyzer(registry=svc),
        profile_builder=ProfileBuilder(registry=svc),
        embedding_service=None,  # explicit
    )

    joined = " ".join(result.changes)
    assert "新增讨厌" in joined
    assert "从候选池语义清除" not in joined

    # BVsemantic should still be fresh because string match couldn't catch it
    rows = db.get_cached_content(limit=10)
    assert rows[0]["pool_status"] == "fresh"


@pytest.mark.asyncio
async def test_semantic_purge_failure_does_not_break_update(
    tmp_path: Path,
) -> None:
    """If the embedding service raises, interest update must still succeed."""
    from openbiliclaw.soul.layer_updaters import _update_interest

    memory = MemoryManager(Path(tmp_path))
    memory.initialize()

    class _BrokenEmbedding:
        similarity_threshold = 0.78

        async def embed(self, text: str) -> list[float]:
            raise RuntimeError("embedding service offline")

    class _DislikeService:
        async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
            payload = json.loads(_PREF_RESP)
            payload["disliked_topics"] = ["鬼畜"]
            return LLMResponse(content=json.dumps(payload), provider="fake")

    profile = OnionProfile()
    svc = _DislikeService()
    result = await _update_interest(
        signals=[{"payload": {"event_type": "view", "title": "v"}}],
        profile=profile,
        memory=memory,
        preference_analyzer=PreferenceAnalyzer(registry=svc),
        profile_builder=ProfileBuilder(registry=svc),
        embedding_service=_BrokenEmbedding(),
    )
    # Semantic purge failure should NOT propagate
    joined = " ".join(result.changes)
    assert "新增讨厌" in joined
    assert "从候选池语义清除" not in joined


@pytest.mark.asyncio
async def test_semantic_purge_module_directly(tmp_path: Path) -> None:
    """Test the semantic_purge_pool_by_disliked_topics function in isolation."""
    from openbiliclaw.soul.pool_purge import (
        DEFAULT_SEMANTIC_PURGE_THRESHOLD,
        semantic_purge_pool_by_disliked_topics,
    )
    from openbiliclaw.storage.database import Database

    db = Database(Path(tmp_path) / "test.db")
    db.initialize()
    db.cache_content("BV1", title="沙雕合集", up_name="u", source="s", topic_key="搞笑")
    db.cache_content("BV2", title="深度技术", up_name="u", source="s", topic_key="AI")

    fake = _FakeEmbeddingService(
        vocabulary={
            "鬼畜": [1.0, 0.0, 0.0, 0.0],
            "沙雕": [1.0, 0.0, 0.0, 0.0],
            "技术": [0.0, 1.0, 0.0, 0.0],
            "AI": [0.0, 1.0, 0.0, 0.0],
        },
    )

    purged = await semantic_purge_pool_by_disliked_topics(
        database=db,
        topics=["鬼畜"],
        embedding_service=fake,
        threshold=DEFAULT_SEMANTIC_PURGE_THRESHOLD,
    )
    assert purged == 1

    rows = {row["bvid"]: row for row in db.get_cached_content(limit=10)}
    assert rows["BV1"]["pool_status"] == "purged_by_dislike"
    assert rows["BV2"]["pool_status"] == "fresh"
    db.close()


@pytest.mark.asyncio
async def test_semantic_purge_empty_topics_is_noop(tmp_path: Path) -> None:
    """Empty topic list should short-circuit without calling the embedding service."""
    from openbiliclaw.soul.pool_purge import semantic_purge_pool_by_disliked_topics
    from openbiliclaw.storage.database import Database

    db = Database(Path(tmp_path) / "test.db")
    db.initialize()
    fake = _FakeEmbeddingService()
    purged = await semantic_purge_pool_by_disliked_topics(
        database=db,
        topics=[],
        embedding_service=fake,
    )
    assert purged == 0
    assert fake.embed_calls == []
    db.close()


@pytest.mark.asyncio
async def test_semantic_purge_all_topic_embeddings_fail_returns_zero(
    tmp_path: Path,
) -> None:
    """If every dislike embedding call raises, purge returns 0 without error."""
    from openbiliclaw.soul.pool_purge import semantic_purge_pool_by_disliked_topics
    from openbiliclaw.storage.database import Database

    db = Database(Path(tmp_path) / "test.db")
    db.initialize()
    db.cache_content("BV1", title="any", up_name="u", source="s", topic_key="any")

    class _AllFailEmbed:
        similarity_threshold = 0.78

        async def embed(self, text: str) -> list[float]:
            raise RuntimeError("embedding permanently broken")

    purged = await semantic_purge_pool_by_disliked_topics(
        database=db,
        topics=["鬼畜"],
        embedding_service=_AllFailEmbed(),
    )
    assert purged == 0
    db.close()


@pytest.mark.asyncio
async def test_semantic_purge_candidate_embed_failure_is_skipped(
    tmp_path: Path,
) -> None:
    """If embedding a specific candidate fails, only that candidate is skipped."""
    from openbiliclaw.soul.pool_purge import semantic_purge_pool_by_disliked_topics
    from openbiliclaw.storage.database import Database

    db = Database(Path(tmp_path) / "test.db")
    db.initialize()
    db.cache_content("BV_ok", title="沙雕合集", up_name="u", source="s", topic_key="搞笑")
    db.cache_content("BV_fail", title="FAIL_ME", up_name="u", source="s", topic_key="x")

    class _SelectiveFailEmbed:
        similarity_threshold = 0.78

        async def embed(self, text: str) -> list[float]:
            if "FAIL_ME" in text:
                raise RuntimeError("selectively broken")
            if "鬼畜" in text or "沙雕" in text:
                return [1.0, 0.0, 0.0, 0.0]
            return [0.0, 1.0, 0.0, 0.0]

    purged = await semantic_purge_pool_by_disliked_topics(
        database=db,
        topics=["鬼畜"],
        embedding_service=_SelectiveFailEmbed(),
    )
    assert purged == 1  # only BV_ok matches
    rows = {r["bvid"]: r for r in db.get_cached_content(limit=10)}
    assert rows["BV_ok"]["pool_status"] == "purged_by_dislike"
    assert rows["BV_fail"]["pool_status"] == "fresh"
    db.close()


@pytest.mark.asyncio
async def test_semantic_purge_candidate_with_empty_embedding_is_skipped(
    tmp_path: Path,
) -> None:
    """A candidate whose embed() returns [] should be skipped gracefully."""
    from openbiliclaw.soul.pool_purge import semantic_purge_pool_by_disliked_topics
    from openbiliclaw.storage.database import Database

    db = Database(Path(tmp_path) / "test.db")
    db.initialize()
    db.cache_content("BV_empty", title="empty-vec", up_name="u", source="s", topic_key="x")

    class _EmptyVecEmbed:
        similarity_threshold = 0.78

        async def embed(self, text: str) -> list[float]:
            if "鬼畜" in text:
                return [1.0, 0.0, 0.0, 0.0]
            return []  # candidate yields empty vector

    purged = await semantic_purge_pool_by_disliked_topics(
        database=db,
        topics=["鬼畜"],
        embedding_service=_EmptyVecEmbed(),
    )
    assert purged == 0
    db.close()


@pytest.mark.asyncio
async def test_semantic_purge_candidate_with_all_empty_fields_is_skipped(
    tmp_path: Path,
) -> None:
    """Candidates with no title/topic text should be skipped without embedding."""
    from openbiliclaw.soul.pool_purge import semantic_purge_pool_by_disliked_topics
    from openbiliclaw.storage.database import Database

    db = Database(Path(tmp_path) / "test.db")
    db.initialize()
    # Insert a minimal row with all the text fields empty
    db.conn.execute(
        """
        INSERT INTO content_cache (
            bvid,
            title,
            topic_key,
            topic_group,
            pool_topic_label,
            relevance_score,
            pool_status,
            source
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("BVempty", "", "", "", "", 0.90, "fresh", "test"),
    )
    db.conn.commit()

    fake = _FakeEmbeddingService(
        vocabulary={"鬼畜": [1.0, 0.0, 0.0, 0.0]},
    )
    purged = await semantic_purge_pool_by_disliked_topics(
        database=db,
        topics=["鬼畜"],
        embedding_service=fake,
    )
    assert purged == 0
    # Only the topic embedding should have been called — no candidate embeddings
    assert fake.embed_calls == ["鬼畜"]
    db.close()


@pytest.mark.asyncio
async def test_update_interest_semantic_purge_module_failure_is_swallowed(
    tmp_path: Path,
) -> None:
    """If the pool_purge module itself raises (not just embed), don't break."""
    import openbiliclaw.soul.pool_purge as pool_purge_mod
    from openbiliclaw.soul.layer_updaters import _update_interest

    memory = MemoryManager(Path(tmp_path))
    memory.initialize()

    async def boom(**kwargs: Any) -> int:
        raise RuntimeError("pool_purge module broken")

    original = pool_purge_mod.semantic_purge_pool_by_disliked_topics
    pool_purge_mod.semantic_purge_pool_by_disliked_topics = boom  # type: ignore[assignment]

    class _DislikeService:
        async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
            payload = json.loads(_PREF_RESP)
            payload["disliked_topics"] = ["鬼畜"]
            return LLMResponse(content=json.dumps(payload), provider="fake")

    profile = OnionProfile()
    svc = _DislikeService()
    fake_embed = _FakeEmbeddingService()
    try:
        result = await _update_interest(
            signals=[{"payload": {"event_type": "view", "title": "v"}}],
            profile=profile,
            memory=memory,
            preference_analyzer=PreferenceAnalyzer(registry=svc),
            profile_builder=ProfileBuilder(registry=svc),
            embedding_service=fake_embed,
        )
    finally:
        pool_purge_mod.semantic_purge_pool_by_disliked_topics = original  # type: ignore[assignment]

    # Must still report the dislike — only the semantic purge line is missing
    assert any("新增讨厌" in c for c in result.changes)
    assert not any("从候选池语义清除" in c for c in result.changes)


# ===========================================================================
# 11. CognitionCycle — throttled awareness + insight generation
# ===========================================================================


_AWARENESS_RESP = json.dumps(
    [
        {
            "date": "2026-04-09",
            "observation": "用户今天集中关注AI技术视频",
            "trend": "技术学习热度上升",
            "emotion_guess": "专注探索",
        },
    ],
    ensure_ascii=False,
)

_INSIGHT_RESP = json.dumps(
    [
        {
            "hypothesis": "用户正在系统性构建AI技术栈",
            "evidence": ["连续多日深度AI内容", "订阅多个技术UP"],
            "confidence": 0.75,
            "validated": False,
            "created_at": "2026-04-09T10:00:00",
        },
    ],
    ensure_ascii=False,
)


class _CognitionFakeService:
    """Fake LLM that discriminates awareness vs insight prompts."""

    def __init__(self) -> None:
        self.awareness_calls = 0
        self.insight_calls = 0

    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        caller: str = "",
    ) -> LLMResponse:
        # Match on distinct keywords present only in each prompt type
        if "awareness" in system_instruction.lower() or "观察" in system_instruction:
            self.awareness_calls += 1
            return LLMResponse(content=_AWARENESS_RESP, provider="fake")
        if (
            "insight" in system_instruction.lower()
            or "洞察" in system_instruction
            or "假设" in system_instruction
        ):
            self.insight_calls += 1
            return LLMResponse(content=_INSIGHT_RESP, provider="fake")
        # Fallback
        return LLMResponse(content=_INSIGHT_RESP, provider="fake")


def _seed_cognition_events(
    memory: MemoryManager, count: int = 4, *, prefix: str = "认知事件"
) -> None:
    """Insert view events so the cursor-based awareness pass has work to do.

    Awareness now reads events newer than its watermark; with zero events it
    short-circuits without calling the analyzer, so cognition tests must seed
    a few rows (directly via the DB so it's callable inside a running loop).
    """
    for i in range(count):
        memory._database.insert_event(  # noqa: SLF001 — test seeding
            "view",
            title=f"{prefix}{i}",
            context=f"在 B 站看了《{prefix}{i}》",
            metadata={"source_platform": "bilibili"},
        )


def _make_cognition_cycle(tmp_path: Path, *, min_interval_seconds: int = 43200):
    """Build a CognitionCycle with fake analyzers wired to the fake service."""
    from openbiliclaw.soul.awareness_analyzer import AwarenessAnalyzer
    from openbiliclaw.soul.cognition_cycle import CognitionCycle
    from openbiliclaw.soul.insight_analyzer import InsightAnalyzer

    svc = _CognitionFakeService()
    memory = MemoryManager(Path(tmp_path))
    memory.initialize()
    memory.get_layer("preference").data.update({"interests": []})
    _seed_cognition_events(memory)
    cycle = CognitionCycle(
        memory=memory,
        awareness_analyzer=AwarenessAnalyzer(registry=svc),
        insight_analyzer=InsightAnalyzer(registry=svc),
        min_interval_seconds=min_interval_seconds,
    )
    return cycle, svc, memory


@pytest.mark.asyncio
async def test_cognition_cycle_first_run_generates_both(tmp_path: Path) -> None:
    """First invocation should run awareness AND insight (no throttle yet)."""
    cycle, svc, memory = _make_cognition_cycle(tmp_path)

    # Seed the soul layer so the sync-to-profile step has something to update
    profile = OnionProfile()
    memory.get_layer("soul").data.update(profile.to_dict())
    memory.get_layer("soul").save()

    result = await cycle.run_if_due()

    assert result.ran is True
    assert result.throttled is False
    assert result.awareness_generated >= 1
    assert result.insight_generated >= 1
    assert svc.awareness_calls == 1
    assert svc.insight_calls == 1

    # Sync should have populated the profile
    reloaded = OnionProfile.from_dict(memory.get_layer("soul").data)
    assert len(reloaded.recent_awareness) >= 1
    assert "AI" in reloaded.recent_awareness[0].observation
    assert len(reloaded.active_insights) >= 1
    assert "AI" in reloaded.active_insights[0].hypothesis


@pytest.mark.asyncio
async def test_cognition_cycle_second_run_throttled(tmp_path: Path) -> None:
    """A second call within the throttle window must skip generation."""
    cycle, svc, memory = _make_cognition_cycle(tmp_path, min_interval_seconds=43200)

    memory.get_layer("soul").data.update(OnionProfile().to_dict())
    memory.get_layer("soul").save()

    first = await cycle.run_if_due()
    assert first.ran

    # Immediately run again — must be throttled
    second = await cycle.run_if_due()
    assert second.ran is False
    assert second.throttled is True
    # LLM should NOT have been called again
    assert svc.awareness_calls == 1
    assert svc.insight_calls == 1


@pytest.mark.asyncio
async def test_cognition_cycle_stale_state_retriggers(tmp_path: Path) -> None:
    """Once the throttle window elapses (simulated), the cycle runs again."""
    cycle, svc, memory = _make_cognition_cycle(tmp_path, min_interval_seconds=60)
    memory.get_layer("soul").data.update(OnionProfile().to_dict())
    memory.get_layer("soul").save()

    # First run at T0

    t0 = datetime(2026, 4, 9, 8, 0, 0)
    first = await cycle.run_if_due(now=t0)
    assert first.ran

    # Second run 30 seconds later — still throttled
    mid = await cycle.run_if_due(now=t0 + timedelta(seconds=30))
    assert mid.throttled

    # New events arrive so the next due run has fresh material to fold in
    # (the cursor would otherwise short-circuit with nothing new to observe).
    _seed_cognition_events(memory, prefix="新一批")

    # Third run 120 seconds later — past the 60s window → runs
    late = await cycle.run_if_due(now=t0 + timedelta(seconds=120))
    assert late.ran is True
    assert late.throttled is False
    assert svc.awareness_calls == 2


@pytest.mark.asyncio
async def test_cognition_cycle_state_persists_across_instances(
    tmp_path: Path,
) -> None:
    """State file should survive recreation of the CognitionCycle object."""
    from openbiliclaw.soul.awareness_analyzer import AwarenessAnalyzer
    from openbiliclaw.soul.cognition_cycle import CognitionCycle
    from openbiliclaw.soul.insight_analyzer import InsightAnalyzer

    svc = _CognitionFakeService()
    memory = MemoryManager(Path(tmp_path))
    memory.initialize()
    memory.get_layer("soul").data.update(OnionProfile().to_dict())
    memory.get_layer("soul").save()
    _seed_cognition_events(memory)

    cycle1 = CognitionCycle(
        memory=memory,
        awareness_analyzer=AwarenessAnalyzer(registry=svc),
        insight_analyzer=InsightAnalyzer(registry=svc),
        min_interval_seconds=43200,
    )
    await cycle1.run_if_due()

    # New instance with the same data_dir should see the throttle state
    cycle2 = CognitionCycle(
        memory=memory,
        awareness_analyzer=AwarenessAnalyzer(registry=svc),
        insight_analyzer=InsightAnalyzer(registry=svc),
        min_interval_seconds=43200,
    )
    second = await cycle2.run_if_due()
    assert second.throttled is True
    assert svc.awareness_calls == 1  # still 1, not 2


@pytest.mark.asyncio
async def test_cognition_cycle_awareness_failure_does_not_block_insight(
    tmp_path: Path,
) -> None:
    """If awareness analyzer raises, insight should still attempt to run."""
    from openbiliclaw.soul.awareness_analyzer import AwarenessAnalyzer
    from openbiliclaw.soul.cognition_cycle import CognitionCycle
    from openbiliclaw.soul.insight_analyzer import InsightAnalyzer

    memory = MemoryManager(Path(tmp_path))
    memory.initialize()
    memory.get_layer("soul").data.update(OnionProfile().to_dict())
    memory.get_layer("soul").save()

    # Pre-seed awareness notes so insight has something to analyze
    memory.get_layer("awareness").data.update(
        {
            "notes": [
                {
                    "date": "2026-04-08",
                    "observation": "existing",
                    "trend": "",
                    "emotion_guess": "",
                }
            ],
        }
    )
    memory.get_layer("awareness").save()
    # Events so the (broken) awareness pass actually attempts a call.
    _seed_cognition_events(memory)

    class _BrokenAwarenessSvc:
        async def complete_structured_task(
            self,
            *,
            system_instruction: str,
            user_input: str,
            **_: Any,
        ) -> LLMResponse:
            if "观察" in system_instruction or "awareness" in system_instruction.lower():
                raise RuntimeError("awareness LLM broken")
            return LLMResponse(content=_INSIGHT_RESP, provider="fake")

    svc = _BrokenAwarenessSvc()
    cycle = CognitionCycle(
        memory=memory,
        awareness_analyzer=AwarenessAnalyzer(registry=svc),
        insight_analyzer=InsightAnalyzer(registry=svc),
        min_interval_seconds=43200,
    )

    result = await cycle.run_if_due()
    assert result.ran is True
    assert result.awareness_generated == 0
    assert "awareness" in " ".join(result.errors).lower()
    # Insight should still have produced at least one hypothesis
    assert result.insight_generated >= 1


@pytest.mark.asyncio
async def test_cognition_cycle_skips_profile_sync_when_soul_layer_empty(
    tmp_path: Path,
) -> None:
    """Fresh install with no soul profile must not crash the cycle."""
    cycle, svc, memory = _make_cognition_cycle(tmp_path)
    # Do NOT seed the soul layer

    result = await cycle.run_if_due()
    assert result.ran is True
    assert result.awareness_generated >= 1
    # No profile → nothing to sync, but cycle should still succeed


@pytest.mark.asyncio
async def test_pipeline_tick_invokes_cognition_cycle(tmp_path: Path) -> None:
    """ProfileUpdatePipeline.tick() should call cognition_cycle.run_if_due()."""
    from openbiliclaw.soul.pipeline import (
        _BUFFERED_LAYERS,
        LayerThreshold,
        ProfileUpdatePipeline,
    )

    class _SpyCycle:
        def __init__(self) -> None:
            self.calls = 0

        async def run_if_due(self) -> Any:
            self.calls += 1

            from openbiliclaw.soul.cognition_cycle import CognitionCycleResult

            return CognitionCycleResult(
                ran=True,
                awareness_generated=2,
                insight_generated=1,
            )

    svc = _RichFakeService()
    memory = MemoryManager(Path(tmp_path))
    memory.initialize()
    spy = _SpyCycle()
    pipeline = ProfileUpdatePipeline(
        memory=memory,
        preference_analyzer=PreferenceAnalyzer(registry=svc),
        profile_builder=ProfileBuilder(registry=svc),
        thresholds={
            layer: LayerThreshold(min_signals=1, min_interval_seconds=0, max_buffer_size=200)
            for layer in _BUFFERED_LAYERS
        },
        cognition_cycle=spy,
    )

    flush_result = await pipeline.tick()

    assert spy.calls == 1
    # tick() should surface the cognition cycle as a virtual PORTRAIT update
    portrait_updates = [
        r
        for r in flush_result.layers_updated
        if r.layer == OnionLayer.PORTRAIT and r.trigger == "半日深度反思"
    ]
    assert portrait_updates, "Cognition cycle output should appear in tick() result"
    assert "观察 2" in portrait_updates[0].changes[0]
    assert "洞察 1" in portrait_updates[0].changes[0]


@pytest.mark.asyncio
async def test_pipeline_tick_drives_real_cognition_cursor(tmp_path: Path) -> None:
    """Full seam: pipeline.tick() → real CognitionCycle → cursor read → DB writes.

    The spy-based test above only proves tick() *calls* run_if_due(); this wires
    the real cycle (real analyzers, fake LLM, real SQLite + seeded events) so the
    cursor watermark, awareness notes, and insight hypotheses all land through
    the production tick path.
    """
    from openbiliclaw.soul.awareness_analyzer import AwarenessAnalyzer
    from openbiliclaw.soul.cognition_cycle import CognitionCycle
    from openbiliclaw.soul.insight_analyzer import InsightAnalyzer
    from openbiliclaw.soul.pipeline import (
        _BUFFERED_LAYERS,
        LayerThreshold,
        ProfileUpdatePipeline,
    )

    svc = _CognitionFakeService()
    memory = MemoryManager(Path(tmp_path))
    memory.initialize()
    memory.get_layer("preference").data.update({"interests": ["AI"]})
    memory.get_layer("soul").data.update(OnionProfile().to_dict())
    memory.get_layer("soul").save()
    _seed_cognition_events(memory, 6)

    cycle = CognitionCycle(
        memory=memory,
        awareness_analyzer=AwarenessAnalyzer(registry=svc),
        insight_analyzer=InsightAnalyzer(registry=svc),
        min_interval_seconds=43200,
    )
    pipeline = ProfileUpdatePipeline(
        memory=memory,
        preference_analyzer=PreferenceAnalyzer(registry=svc),
        profile_builder=ProfileBuilder(registry=svc),
        thresholds={
            layer: LayerThreshold(min_signals=1, min_interval_seconds=0, max_buffer_size=200)
            for layer in _BUFFERED_LAYERS
        },
        cognition_cycle=cycle,
    )

    await pipeline.tick()

    # Awareness pass ran through the cursor: watermark advanced past the 6 events.
    state = cycle._load_state()  # noqa: SLF001
    assert state["last_awareness_event_id"] == 6
    assert state["last_insight_awareness_index"] >= 1
    # Real analyzer output persisted to layers + synced onto the onion profile.
    assert memory.get_layer("awareness").data["notes"]
    assert memory.get_layer("insight").data["hypotheses"]
    profile = OnionProfile.from_dict(memory.get_layer("soul").data)
    assert profile.recent_awareness
    assert profile.active_insights


@pytest.mark.asyncio
async def test_pipeline_tick_cognition_throttled_does_not_produce_update(
    tmp_path: Path,
) -> None:
    """When cognition cycle is throttled (ran=False), no layer update is appended."""
    from openbiliclaw.soul.pipeline import (
        _BUFFERED_LAYERS,
        LayerThreshold,
        ProfileUpdatePipeline,
    )

    class _ThrottledCycle:
        async def run_if_due(self) -> Any:
            from openbiliclaw.soul.cognition_cycle import CognitionCycleResult

            return CognitionCycleResult(ran=False, throttled=True)

    svc = _RichFakeService()
    memory = MemoryManager(Path(tmp_path))
    memory.initialize()
    pipeline = ProfileUpdatePipeline(
        memory=memory,
        preference_analyzer=PreferenceAnalyzer(registry=svc),
        profile_builder=ProfileBuilder(registry=svc),
        thresholds={
            layer: LayerThreshold(min_signals=1, min_interval_seconds=0, max_buffer_size=200)
            for layer in _BUFFERED_LAYERS
        },
        cognition_cycle=_ThrottledCycle(),
    )

    flush_result = await pipeline.tick()
    portrait_updates = [r for r in flush_result.layers_updated if r.layer == OnionLayer.PORTRAIT]
    assert not portrait_updates


@pytest.mark.asyncio
async def test_pipeline_tick_cognition_exception_is_swallowed(
    tmp_path: Path,
) -> None:
    """A broken cognition cycle must not break pipeline.tick()."""
    from openbiliclaw.soul.pipeline import (
        _BUFFERED_LAYERS,
        LayerThreshold,
        ProfileUpdatePipeline,
    )

    class _BrokenCycle:
        async def run_if_due(self) -> Any:
            raise RuntimeError("cycle broken")

    svc = _RichFakeService()
    memory = MemoryManager(Path(tmp_path))
    memory.initialize()
    pipeline = ProfileUpdatePipeline(
        memory=memory,
        preference_analyzer=PreferenceAnalyzer(registry=svc),
        profile_builder=ProfileBuilder(registry=svc),
        thresholds={
            layer: LayerThreshold(min_signals=1, min_interval_seconds=0, max_buffer_size=200)
            for layer in _BUFFERED_LAYERS
        },
        cognition_cycle=_BrokenCycle(),
    )

    # Should NOT raise
    flush_result = await pipeline.tick()
    assert isinstance(flush_result.layers_updated, list)


@pytest.mark.asyncio
async def test_cognition_cycle_default_interval_is_12_hours() -> None:
    """Confirm the default throttle is 12 hours (user-specified requirement)."""
    from openbiliclaw.soul.cognition_cycle import DEFAULT_MIN_INTERVAL_SECONDS

    assert DEFAULT_MIN_INTERVAL_SECONDS == 12 * 60 * 60


@pytest.mark.asyncio
async def test_cognition_cycle_corrupt_state_falls_back_to_due(
    tmp_path: Path,
) -> None:
    """Corrupt state JSON should not prevent the cycle from running."""
    memory_dir = Path(tmp_path) / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "cognition_cycle_state.json").write_text("not json {{{", encoding="utf-8")

    cycle, svc, memory = _make_cognition_cycle(tmp_path)
    memory.get_layer("soul").data.update(OnionProfile().to_dict())
    memory.get_layer("soul").save()

    result = await cycle.run_if_due()
    assert result.ran is True


@pytest.mark.asyncio
async def test_cognition_cycle_invalid_timestamp_falls_back_to_due(
    tmp_path: Path,
) -> None:
    """Invalid last_awareness_at format should be treated as 'never ran'."""
    memory_dir = Path(tmp_path) / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "cognition_cycle_state.json").write_text(
        json.dumps({"last_awareness_at": "not-a-date", "last_insight_at": None}),
        encoding="utf-8",
    )

    cycle, svc, memory = _make_cognition_cycle(tmp_path)
    memory.get_layer("soul").data.update(OnionProfile().to_dict())
    memory.get_layer("soul").save()

    result = await cycle.run_if_due()
    assert result.ran is True


@pytest.mark.asyncio
async def test_cognition_cycle_insight_failure_is_recorded(
    tmp_path: Path,
) -> None:
    """If insight analyzer raises, cycle records error but awareness is kept."""
    from openbiliclaw.soul.awareness_analyzer import AwarenessAnalyzer
    from openbiliclaw.soul.cognition_cycle import CognitionCycle
    from openbiliclaw.soul.insight_analyzer import InsightAnalyzer

    memory = MemoryManager(Path(tmp_path))
    memory.initialize()
    memory.get_layer("soul").data.update(OnionProfile().to_dict())
    memory.get_layer("soul").save()
    _seed_cognition_events(memory)

    class _BrokenInsightSvc:
        async def complete_structured_task(
            self,
            *,
            system_instruction: str,
            user_input: str,
            **_: Any,
        ) -> LLMResponse:
            if "观察" in system_instruction or "awareness" in system_instruction.lower():
                return LLMResponse(content=_AWARENESS_RESP, provider="fake")
            raise RuntimeError("insight LLM broken")

    svc = _BrokenInsightSvc()
    cycle = CognitionCycle(
        memory=memory,
        awareness_analyzer=AwarenessAnalyzer(registry=svc),
        insight_analyzer=InsightAnalyzer(registry=svc),
    )
    result = await cycle.run_if_due()
    assert result.ran is True
    assert result.awareness_generated >= 1
    assert result.insight_generated == 0
    assert any("insight" in err.lower() for err in result.errors)


@pytest.mark.asyncio
async def test_cognition_cycle_sync_exception_does_not_propagate(
    tmp_path: Path,
) -> None:
    """If _sync_to_profile raises mid-flow, the cycle must still finish cleanly."""

    cycle, svc, memory = _make_cognition_cycle(tmp_path)
    memory.get_layer("soul").data.update(OnionProfile().to_dict())
    memory.get_layer("soul").save()

    # Patch the instance method to raise
    original_sync = cycle._sync_to_profile  # type: ignore[attr-defined]

    def boom(result_obj: Any) -> None:
        raise RuntimeError("sync broken")

    cycle._sync_to_profile = boom  # type: ignore[assignment]
    try:
        result = await cycle.run_if_due()
    finally:
        cycle._sync_to_profile = original_sync  # type: ignore[assignment]

    # Despite sync failure, cycle should report the generation succeeded
    assert result.ran is True
    assert result.awareness_generated >= 1


@pytest.mark.asyncio
async def test_cognition_cycle_empty_insight_response_returns_zero(
    tmp_path: Path,
) -> None:
    """If insight analyzer returns [], _run_insight should short-circuit to 0."""
    from openbiliclaw.soul.awareness_analyzer import AwarenessAnalyzer
    from openbiliclaw.soul.cognition_cycle import CognitionCycle
    from openbiliclaw.soul.insight_analyzer import InsightAnalyzer

    memory = MemoryManager(Path(tmp_path))
    memory.initialize()
    memory.get_layer("soul").data.update(OnionProfile().to_dict())
    memory.get_layer("soul").save()
    # Pre-seed awareness so the insight pass has input
    memory.get_layer("awareness").data.update(
        {
            "notes": [
                {
                    "date": "2026-04-08",
                    "observation": "seeded",
                    "trend": "",
                    "emotion_guess": "",
                }
            ],
        }
    )
    memory.get_layer("awareness").save()

    class _EmptyInsightSvc:
        async def complete_structured_task(
            self,
            *,
            system_instruction: str,
            user_input: str,
            **_: Any,
        ) -> LLMResponse:
            if "观察" in system_instruction or "awareness" in system_instruction.lower():
                return LLMResponse(content=_AWARENESS_RESP, provider="fake")
            return LLMResponse(content="[]", provider="fake")

    svc = _EmptyInsightSvc()
    cycle = CognitionCycle(
        memory=memory,
        awareness_analyzer=AwarenessAnalyzer(registry=svc),
        insight_analyzer=InsightAnalyzer(registry=svc),
    )
    result = await cycle.run_if_due()
    assert result.ran is True
    assert result.insight_generated == 0


@pytest.mark.asyncio
async def test_cognition_cycle_without_data_dir_still_runs(
    tmp_path: Path,
) -> None:
    """A memory manager with no _data_dir should not crash state load/save."""
    from openbiliclaw.soul.awareness_analyzer import AwarenessAnalyzer
    from openbiliclaw.soul.cognition_cycle import CognitionCycle
    from openbiliclaw.soul.insight_analyzer import InsightAnalyzer

    memory = MemoryManager(Path(tmp_path))
    memory.initialize()
    memory.get_layer("preference").data.update({"interests": []})
    # Strip the data_dir attribute to trigger the None-path
    delattr(memory, "_data_dir")

    svc = _CognitionFakeService()
    cycle = CognitionCycle(
        memory=memory,
        awareness_analyzer=AwarenessAnalyzer(registry=svc),
        insight_analyzer=InsightAnalyzer(registry=svc),
    )
    result = await cycle.run_if_due()
    # Should run (no state file → treated as fresh)
    assert result.ran is True


def test_pipeline_set_cognition_cycle_setter(tmp_path: Path) -> None:
    """The set_cognition_cycle setter should attach a new cycle reference."""
    from openbiliclaw.soul.pipeline import ProfileUpdatePipeline

    svc = _RichFakeService()
    memory = MemoryManager(Path(tmp_path))
    memory.initialize()
    pipeline = ProfileUpdatePipeline(
        memory=memory,
        preference_analyzer=PreferenceAnalyzer(registry=svc),
        profile_builder=ProfileBuilder(registry=svc),
    )
    assert pipeline._cognition_cycle is None

    marker = object()
    pipeline.set_cognition_cycle(marker)
    assert pipeline._cognition_cycle is marker


@pytest.mark.asyncio
async def test_cognition_cycle_insight_runs_without_awareness_notes(
    tmp_path: Path,
) -> None:
    """Insight pass should short-circuit to 0 when no awareness exists yet."""

    # Use a service that returns EMPTY awareness response so no notes get saved
    class _EmptyAwarenessService:
        async def complete_structured_task(
            self,
            *,
            system_instruction: str,
            user_input: str,
            **_: Any,
        ) -> LLMResponse:
            # Awareness returns [] → no notes; insight should therefore skip
            return LLMResponse(content="[]", provider="fake")

    from openbiliclaw.soul.awareness_analyzer import AwarenessAnalyzer
    from openbiliclaw.soul.cognition_cycle import CognitionCycle
    from openbiliclaw.soul.insight_analyzer import InsightAnalyzer

    memory = MemoryManager(Path(tmp_path))
    memory.initialize()
    memory.get_layer("preference").data.update({"interests": []})
    memory.get_layer("soul").data.update(OnionProfile().to_dict())
    memory.get_layer("soul").save()

    svc = _EmptyAwarenessService()
    cycle = CognitionCycle(
        memory=memory,
        awareness_analyzer=AwarenessAnalyzer(registry=svc),
        insight_analyzer=InsightAnalyzer(registry=svc),
    )

    result = await cycle.run_if_due()
    assert result.ran is True
    assert result.awareness_generated == 0
    assert result.insight_generated == 0  # no notes to derive from


@pytest.mark.asyncio
async def test_update_layer_no_signals_returns_none(tmp_path: Path) -> None:
    """_update_layer with empty buffer (drain=[]) should return None."""
    pipeline, _, _ = _make_low_threshold_pipeline(tmp_path)
    empty_buf = LayerBuffer(layer=OnionLayer.INTEREST)  # no signals
    result = await pipeline._update_layer(OnionLayer.INTEREST, empty_buf)
    assert result is None


def test_load_pipeline_state_with_non_dict_buffers_section(tmp_path: Path) -> None:
    """If 'buffers' key in state file is not a dict, fall back to empty buffers."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "pipeline_state.json").write_text(
        json.dumps({"version": 1, "buffers": "not-a-dict"}),
        encoding="utf-8",
    )
    loaded = load_pipeline_state(tmp_path)
    for buf in loaded.values():
        assert buf.signals == []


def test_layer_buffer_is_ready_handles_invalid_last_updated_at() -> None:
    """is_ready should ignore unparseable last_updated_at strings."""
    buf = LayerBuffer(layer=OnionLayer.INTEREST)
    buf.signals.append({"id": "x", "signal_type": "behavior_event"})
    buf.last_updated_at = "totally-invalid-date"
    threshold = LayerThreshold(min_signals=1, min_interval_seconds=3600, max_buffer_size=10)
    # Should not raise; should treat as if no last_updated_at
    assert buf.is_ready(threshold, datetime.now()) is True


# ===========================================================================
# 9. Last-mile coverage: defensive guards and rare branches
# ===========================================================================


@pytest.mark.asyncio
async def test_ingest_skips_non_buffered_target_layer(tmp_path: Path) -> None:
    """If a signal targets PORTRAIT (not in _BUFFERED_LAYERS), it should be skipped."""
    from openbiliclaw.soul.pipeline import ProfileSignal

    pipeline, _, _ = _make_low_threshold_pipeline(tmp_path)
    rogue_signal = ProfileSignal(
        id="rogue",
        signal_type=SignalType.BEHAVIOR_EVENT,
        timestamp=datetime.now().isoformat(),
        source="test",
        payload={"event_type": "view", "title": "x"},
        target_layers=frozenset({OnionLayer.PORTRAIT}),  # NOT buffered
    )
    result = await pipeline.ingest(rogue_signal)
    # No layers should have been buffered
    assert result.layers_buffered == []
    assert result.layers_updated == []


@pytest.mark.asyncio
async def test_ingest_skips_layer_with_missing_buffer(tmp_path: Path) -> None:
    """If self._buffers is missing a layer, ingest should not crash."""
    from openbiliclaw.soul.pipeline import ProfileSignal

    pipeline, _, _ = _make_low_threshold_pipeline(tmp_path)
    # Surgically remove the INTEREST buffer
    del pipeline._buffers[OnionLayer.INTEREST.value]

    sig = ProfileSignal(
        id="x",
        signal_type=SignalType.BEHAVIOR_EVENT,
        timestamp=datetime.now().isoformat(),
        source="test",
        payload={"event_type": "view", "title": "x"},
        target_layers=frozenset({OnionLayer.INTEREST}),
    )
    result = await pipeline.ingest(sig)
    # INTEREST is not in layers_buffered because the buf was None
    assert "interest" not in result.layers_buffered


@pytest.mark.asyncio
async def test_regenerate_portrait_returns_builder_output(tmp_path: Path) -> None:
    """regenerate_portrait still builds a portrait when called directly.

    The pipeline no longer dispatches deep layers (P1 retired) so this helper is
    only reachable directly / for rebuild tooling; verify it still works.
    """
    from openbiliclaw.soul.layer_updaters import regenerate_portrait

    pipeline, svc, memory = _make_low_threshold_pipeline(tmp_path)
    profile = OnionProfile()

    portrait = await regenerate_portrait(
        profile=profile,
        profile_builder=ProfileBuilder(registry=svc),
        memory=memory,
    )
    assert portrait, "regenerate_portrait should return the builder's portrait text"
    assert "热爱技术探索" in portrait


@pytest.mark.asyncio
async def test_update_interest_detects_weight_changes(tmp_path: Path) -> None:
    """Weight changes > 0.15 on existing interests should appear as a change line."""
    from openbiliclaw.soul.layer_updaters import _update_interest

    memory = MemoryManager(Path(tmp_path))
    memory.initialize()
    # Pre-seed preference layer with low-weight AI
    memory.get_layer("preference").data.update(
        {
            "interests": [
                {"name": "AI", "category": "知识", "weight": 0.3, "source": "events"},
            ],
        }
    )

    # Mock returns AI at 0.85 → delta of 0.55
    svc = _RichFakeService()
    profile = OnionProfile()

    result = await _update_interest(
        signals=[{"payload": {"event_type": "view", "title": "AI教程"}}],
        profile=profile,
        memory=memory,
        preference_analyzer=PreferenceAnalyzer(registry=svc),
        profile_builder=ProfileBuilder(registry=svc),
    )
    assert any("权重变化" in c for c in result.changes), result.changes


@pytest.mark.asyncio
async def test_update_values_records_removed_values_and_drivers(tmp_path: Path) -> None:
    """When new values/drivers REPLACE old ones, removal lines must be recorded."""
    from openbiliclaw.soul.layer_updaters import _update_values

    memory = MemoryManager(Path(tmp_path))
    memory.initialize()

    # Mock that returns NEW values, dropping the old ones
    class _ReplacingService:
        async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
            return LLMResponse(
                content=json.dumps(
                    {
                        "changed": True,
                        "values": ["全新价值A", "全新价值B"],
                        "motivational_drivers": ["全新驱动X"],
                        "reason": "整体替换",
                    },
                    ensure_ascii=False,
                ),
                provider="fake",
            )

    svc = _ReplacingService()
    profile = OnionProfile()
    # Seed old values/drivers so the diff produces removals
    profile.values_layer.values = ["旧价值1", "旧价值2"]
    profile.values_layer.motivational_drivers = ["旧驱动1"]

    result = await _update_values(
        signals=[{"payload": {"event_type": "feedback", "title": "深度内容"}}],
        profile=profile,
        memory=memory,
        preference_analyzer=PreferenceAnalyzer(registry=svc),
        profile_builder=ProfileBuilder(registry=svc),
    )
    assert result.changed
    joined = " ".join(result.changes)
    assert "移除价值观" in joined, f"Expected '移除价值观' in changes: {result.changes}"
    assert "移除驱动" in joined, f"Expected '移除驱动' in changes: {result.changes}"


@pytest.mark.asyncio
async def test_speculator_seed_ingestion_exception_is_swallowed(tmp_path: Path) -> None:
    """If speculator seed ingestion raises, _update_interest must continue gracefully."""
    from openbiliclaw.soul import speculator as spec_mod
    from openbiliclaw.soul.layer_updaters import _update_interest

    memory = MemoryManager(Path(tmp_path))
    memory.initialize()

    class _SeedingService:
        async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
            payload = json.loads(_PREF_RESP)
            payload["speculative_interests"] = [
                {"name": "x", "category": "y", "weight": 0.5},
            ]
            return LLMResponse(content=json.dumps(payload), provider="fake")

    svc = _SeedingService()
    profile = OnionProfile()

    # Patch InterestSpeculator class to a constructor that raises
    original = spec_mod.InterestSpeculator

    class _BoomSpeculator:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("speculator init broken")

    spec_mod.InterestSpeculator = _BoomSpeculator  # type: ignore[misc]
    try:
        result = await _update_interest(
            signals=[{"payload": {"event_type": "view", "title": "AI"}}],
            profile=profile,
            memory=memory,
            preference_analyzer=PreferenceAnalyzer(registry=svc),
            profile_builder=ProfileBuilder(registry=svc),
        )
    finally:
        spec_mod.InterestSpeculator = original  # type: ignore[misc]

    # The exception was swallowed; result still came back without seed-injection line
    assert not any("猜测兴趣种子" in c for c in result.changes)
    # But other changes (like new interest) should still be there
    assert result.changed


# ===========================================================================
# P1 retirement — deep-buffer migration (deep-line consolidation)
# ===========================================================================


def _write_pipeline_state_with_deep_buffers(data_dir: Path) -> None:
    """Persist a pipeline_state.json holding legacy VALUES/CORE buffer signals."""
    memory_dir = data_dir / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "version": 1,
        "buffers": {
            "values": {
                "layer": "values",
                "signals": [
                    {
                        "id": "sig1",
                        "signal_type": "feedback",
                        "timestamp": "2026-07-01T10:00:00",
                        "source": "feedback",
                        "payload": {"event_type": "like", "title": "深度学习纪录片", "id": 42},
                        "confidence": 0.9,
                    }
                ],
                "last_updated_at": "",
                "update_count": 0,
            },
            "core": {
                "layer": "core",
                "signals": [
                    {
                        "id": "sig2",
                        "signal_type": "dialogue_insight",
                        "timestamp": "2026-07-02T11:00:00",
                        "source": "dialogue",
                        "payload": {"content": "用户在反思人生方向"},
                        "confidence": 0.85,
                    }
                ],
                "last_updated_at": "",
                "update_count": 0,
            },
            "interest": {
                "layer": "interest",
                "signals": [],
                "last_updated_at": "",
                "update_count": 0,
            },
        },
        "last_saved_at": "2026-07-02T11:00:00",
        "total_signals_ingested": 2,
    }
    with open(memory_dir / "pipeline_state.json", "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


class _FakeLedger:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def record(self, **kwargs: Any) -> None:
        self.rows.append(dict(kwargs))


@pytest.mark.asyncio
async def test_migrate_pipeline_deep_buffers_converts_and_clears(tmp_path: Path) -> None:
    """Legacy VALUES/CORE buffer signals become awareness notes; keys cleared; ledger row."""
    from openbiliclaw.soul.pipeline import (
        _PIPELINE_DEEP_MIGRATION_MARKER,
        _PIPELINE_DEEP_MIGRATION_PREFIX,
        migrate_pipeline_deep_buffers,
    )

    data_dir = Path(tmp_path)
    _write_pipeline_state_with_deep_buffers(data_dir)
    memory = MemoryManager(data_dir)
    memory.initialize()
    ledger = _FakeLedger()

    added = migrate_pipeline_deep_buffers(data_dir, memory, ledger)
    assert added == 2

    notes = memory.get_layer("awareness").data.get("notes", [])
    observations = [str(n.get("observation", "")) for n in notes]
    assert all(o.startswith(_PIPELINE_DEEP_MIGRATION_PREFIX) for o in observations)
    assert any("深度学习纪录片" in o for o in observations)
    assert any("反思人生方向" in o for o in observations)
    # Best-effort source_event_id backfill: the values signal carried id=42.
    assert any(42 in n.get("source_event_ids", []) for n in notes)

    # State: deep keys cleared, marker set.
    with open(data_dir / "memory" / "pipeline_state.json", encoding="utf-8") as f:
        state = json.load(f)
    assert state[_PIPELINE_DEEP_MIGRATION_MARKER] is True
    assert state["buffers"]["values"]["signals"] == []
    assert state["buffers"]["core"]["signals"] == []

    # Ledger row recorded the migration.
    assert ledger.rows and ledger.rows[0]["write_point"] == "pipeline_deep_migration"


@pytest.mark.asyncio
async def test_migrate_pipeline_deep_buffers_is_idempotent(tmp_path: Path) -> None:
    """Re-running the migration (marker set) adds nothing and touches no notes."""
    from openbiliclaw.soul.pipeline import migrate_pipeline_deep_buffers

    data_dir = Path(tmp_path)
    _write_pipeline_state_with_deep_buffers(data_dir)
    memory = MemoryManager(data_dir)
    memory.initialize()

    first = migrate_pipeline_deep_buffers(data_dir, memory, None)
    assert first == 2
    notes_after_first = len(memory.get_layer("awareness").data.get("notes", []))

    # Marker short-circuits the re-run.
    second = migrate_pipeline_deep_buffers(data_dir, memory, None)
    assert second == 0
    assert len(memory.get_layer("awareness").data.get("notes", [])) == notes_after_first


@pytest.mark.asyncio
async def test_migrate_dedup_on_crash_rerun_without_marker(tmp_path: Path) -> None:
    """A crash before the marker write is safe: re-run dedups by content hash."""
    from openbiliclaw.soul.pipeline import (
        _PIPELINE_DEEP_MIGRATION_MARKER,
        migrate_pipeline_deep_buffers,
    )

    data_dir = Path(tmp_path)
    _write_pipeline_state_with_deep_buffers(data_dir)
    memory = MemoryManager(data_dir)
    memory.initialize()

    migrate_pipeline_deep_buffers(data_dir, memory, None)
    notes_after_first = len(memory.get_layer("awareness").data.get("notes", []))

    # Simulate a crash BEFORE the marker/clear persisted: restore the raw signals
    # and drop the marker, keeping the already-written awareness notes.
    _write_pipeline_state_with_deep_buffers(data_dir)
    state_path = data_dir / "memory" / "pipeline_state.json"
    with open(state_path, encoding="utf-8") as f:
        state = json.load(f)
    assert _PIPELINE_DEEP_MIGRATION_MARKER not in state

    added = migrate_pipeline_deep_buffers(data_dir, memory, None)
    assert added == 0, "content-hash dedup must prevent duplicate notes on re-run"
    assert len(memory.get_layer("awareness").data.get("notes", [])) == notes_after_first


def test_buffered_layers_excludes_deep(tmp_path: Path) -> None:
    """Reverse assertion: the pipeline no longer buffers VALUES/CORE at all."""
    assert OnionLayer.VALUES not in _BUFFERED_LAYERS
    assert OnionLayer.CORE not in _BUFFERED_LAYERS
    # classify_signal never yields a deep layer for feedback or value/state insights.
    assert OnionLayer.VALUES not in classify_signal(SignalType.FEEDBACK, {})
    assert classify_signal(SignalType.DIALOGUE_INSIGHT, {"kind": "value"}) == frozenset()
    assert classify_signal(SignalType.DIALOGUE_INSIGHT, {"kind": "state"}) == frozenset()


@pytest.mark.asyncio
async def test_update_layer_seals_values_and_core(tmp_path: Path) -> None:
    """update_layer(VALUES|CORE) is a defensive no-op (invariant 1, F3)."""
    from openbiliclaw.soul.layer_updaters import update_layer

    memory = MemoryManager(Path(tmp_path))
    memory.initialize()
    profile = OnionProfile()
    signals = [{"payload": {"event_type": "view", "title": "t", "content": "深度"}}]
    for layer in (OnionLayer.VALUES, OnionLayer.CORE):
        result = await update_layer(
            layer=layer,
            signals=signals,
            profile=profile,
            memory=memory,
            preference_analyzer=None,  # type: ignore[arg-type]
            profile_builder=None,  # type: ignore[arg-type]
        )
        assert result.changed is False
    assert profile.values_layer.values == []
    assert profile.core.core_traits == []


@pytest.mark.asyncio
async def test_update_interest_passes_the_recent_cognition_tail(tmp_path: Path) -> None:
    """快线的兴趣更新要带上近期觉察/洞察，让小批事件不在真空里被解读。"""
    from openbiliclaw.soul.layer_updaters import _update_interest
    from openbiliclaw.soul.profile import AwarenessNote, InsightHypothesis

    memory = MemoryManager(Path(tmp_path))
    memory.initialize()
    profile = OnionProfile()
    profile.recent_awareness = [AwarenessNote(observation="最近在深挖 Rust 底层")]
    profile.active_insights = [InsightHypothesis(hypothesis="可能是系统编程从业者")]

    captured: dict[str, object] = {}

    class _CapturingAnalyzer:
        async def analyze_events(self, **kwargs: object) -> dict[str, object]:
            captured.update(kwargs)
            return dict(memory.get_layer("preference").data)

    result = await _update_interest(
        signals=[{"payload": {"event_type": "view", "title": "Rust 异步运行时精读"}}],
        profile=profile,
        memory=memory,
        preference_analyzer=_CapturingAnalyzer(),  # type: ignore[arg-type]
        profile_builder=None,  # type: ignore[arg-type]
    )

    assert result.layer == OnionLayer.INTEREST
    notes = captured.get("awareness_notes")
    insights = captured.get("active_insights")
    assert notes and notes[0]["observation"] == "最近在深挖 Rust 底层"
    assert insights and insights[0]["hypothesis"] == "可能是系统编程从业者"


async def test_preference_chunks_keep_the_recent_cognition_context() -> None:
    """预算或显式分片不能把快线传入的认知语境静默丢掉。"""
    service = _RichFakeService()
    analyzer = PreferenceAnalyzer(registry=service, max_prompt_chars=0)

    await analyzer.analyze_events(
        events=[
            {"event_type": "view", "title": "Rust 异步运行时（一）"},
            {"event_type": "view", "title": "Rust 异步运行时（二）"},
        ],
        existing_preference={"interests": []},
        event_chunk_size=1,
        awareness_notes=[{"observation": "最近在深挖 Rust 底层"}],
        active_insights=[{"hypothesis": "可能是系统编程从业者"}],
    )

    assert len(service.calls) == 2
    for call in service.calls:
        assert "<recent_awareness>" in call["user_input"]
        assert "最近在深挖 Rust 底层" in call["user_input"]
        assert "<active_insights>" in call["user_input"]
        assert "可能是系统编程从业者" in call["user_input"]


# ===========================================================================
# 统一兴趣更新线 Phase 1 — 反馈优先级 flush（spec 不变量 1）
#
# 含 FEEDBACK 信号的 INTEREST 缓冲在信号数达 feedback_batch_threshold（默认 3，
# 复用旧批线 config 键）时立即消费，绕过 min_interval_seconds；普通事件维持
# 3 条 / 600s 不变。Wave A 默认关（scheduler.unified_interest_line=false）。
# ===========================================================================


def _feedback_buffer(count: int, *, now: datetime) -> LayerBuffer:
    buf = LayerBuffer(layer=OnionLayer.INTEREST)
    # 刚刚更新过 → min_interval 闸门是关着的
    buf.last_updated_at = now.isoformat()
    for index in range(count):
        buf.signals.append(_serialize_signal(signal_from_feedback("dislike", f"卡片{index}", "")))
    return buf


def test_feedback_priority_bypasses_min_interval_without_clock_advance() -> None:
    """3 条 FEEDBACK 信号在墙钟零推进下即 ready；关闭时维持今天的行为。"""
    threshold = LayerThreshold(min_signals=3, min_interval_seconds=600, max_buffer_size=200)
    now = datetime.now()
    buf = _feedback_buffer(3, now=now)

    assert buf.is_ready(threshold, now, has_strong_signal=True) is False
    assert (
        buf.is_ready(threshold, now, has_strong_signal=True, feedback_priority_threshold=3) is True
    )


def test_feedback_priority_needs_the_full_threshold() -> None:
    """2 条反馈不够——优先级规则复用 feedback_batch_threshold 的计数语义。"""
    threshold = LayerThreshold(min_signals=3, min_interval_seconds=600, max_buffer_size=200)
    now = datetime.now()
    buf = _feedback_buffer(2, now=now)

    assert (
        buf.is_ready(threshold, now, has_strong_signal=True, feedback_priority_threshold=3) is False
    )


def test_feedback_priority_does_not_speed_up_ordinary_events() -> None:
    """普通浏览事件即使 3 条也不绕过 600s——只有 FEEDBACK 有优先级。"""
    threshold = LayerThreshold(min_signals=3, min_interval_seconds=600, max_buffer_size=200)
    now = datetime.now()
    buf = LayerBuffer(layer=OnionLayer.INTEREST)
    buf.last_updated_at = now.isoformat()
    for index in range(3):
        buf.signals.append(
            _serialize_signal(
                signals_from_events([{"event_type": "view", "title": f"v{index}"}])[0]
            )
        )

    assert buf.is_ready(threshold, now, feedback_priority_threshold=3) is False


@pytest.mark.asyncio
async def test_pipeline_feedback_priority_flush_when_unified_line_on(tmp_path: Path) -> None:
    """开关打开时，第 3 条反馈立刻触发 INTEREST 消费（不等 min_interval）。"""
    svc = _RichFakeService()
    memory = MemoryManager(Path(tmp_path))
    memory.initialize()
    thresholds = {
        layer: LayerThreshold(min_signals=3, min_interval_seconds=3600, max_buffer_size=200)
        for layer in _BUFFERED_LAYERS
    }
    pipeline = ProfileUpdatePipeline(
        memory=memory,
        preference_analyzer=PreferenceAnalyzer(registry=svc),
        profile_builder=ProfileBuilder(registry=svc),
        thresholds=thresholds,
        unified_interest_line=True,
        feedback_batch_threshold=3,
    )
    # 模拟「刚刚消费过一次」，把 600s/3600s 最短间隔闸门关上。
    pipeline._buffers[OnionLayer.INTEREST.value].last_updated_at = datetime.now().isoformat()

    updated: list[OnionLayer] = []
    for index in range(3):
        result = await pipeline.ingest(signal_from_feedback("dislike", f"卡片{index}", ""))
        updated.extend(r.layer for r in result.layers_updated)

    assert OnionLayer.INTEREST in updated


@pytest.mark.asyncio
async def test_pipeline_feedback_priority_off_by_default(tmp_path: Path) -> None:
    """Wave A 默认关：反馈仍受 min_interval 约束，行为与今天逐字节一致。"""
    svc = _RichFakeService()
    memory = MemoryManager(Path(tmp_path))
    memory.initialize()
    thresholds = {
        layer: LayerThreshold(min_signals=3, min_interval_seconds=3600, max_buffer_size=200)
        for layer in _BUFFERED_LAYERS
    }
    pipeline = ProfileUpdatePipeline(
        memory=memory,
        preference_analyzer=PreferenceAnalyzer(registry=svc),
        profile_builder=ProfileBuilder(registry=svc),
        thresholds=thresholds,
    )
    pipeline._buffers[OnionLayer.INTEREST.value].last_updated_at = datetime.now().isoformat()

    updated: list[OnionLayer] = []
    for index in range(3):
        result = await pipeline.ingest(signal_from_feedback("dislike", f"卡片{index}", ""))
        updated.extend(r.layer for r in result.layers_updated)

    assert OnionLayer.INTEREST not in updated
    assert len(pipeline._buffers[OnionLayer.INTEREST.value].signals) == 3


# ===========================================================================
# 统一兴趣更新线 Phase 1 后半 — 消费侧继承批线特权（spec §Phase 1）
#
# 含 FEEDBACK 信号的那一批在 _update_interest 里额外获得：dislike 归档、显著
# 变化 → 接入点③门控重建（trigger=feedback_batch）、批后 held-replay、台账
# pipeline_layer_update(source="feedback")。不含反馈的批一律不受影响。
# ===========================================================================


class _StubGate:
    """Deterministic posture-gate stand-in (mirrors tests/test_posture_gate.py)."""

    def __init__(self, mode: str, decision: Any) -> None:
        self._mode = mode
        self._decision = decision
        self.calls: list[dict[str, Any]] = []

    @property
    def enabled(self) -> bool:
        return self._mode != "off"

    async def evaluate(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self._decision


_SIGNIFICANT_PREFERENCE: dict[str, Any] = {
    "interests": [
        {"name": "标题党", "category": "内容", "weight": 0.7, "source": "feedback"},
        {"name": "城市建筑", "category": "文化", "weight": 0.86, "source": "feedback"},
        {"name": "结构力学", "category": "知识", "weight": 0.81, "source": "feedback"},
    ],
    "style": {},
    "context": {},
    "exploration_openness": 0.5,
    "disliked_topics": ["标题党"],
    "favorite_up_users": [],
}


def _unified_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, MemoryManager]:
    """SoulEngine with the unified interest line ON and a stubbed analyzer."""
    from openbiliclaw.soul.engine import SoulEngine

    memory = MemoryManager(Path(tmp_path))
    memory.initialize()
    engine = SoulEngine(
        llm=_RichFakeService(),
        memory=memory,
        unified_interest_line=True,
        database=memory._database,
    )

    async def fake_analyze_events(**kwargs: Any) -> dict[str, Any]:
        del kwargs
        import copy

        return copy.deepcopy(_SIGNIFICANT_PREFERENCE)

    monkeypatch.setattr(engine._preference_analyzer, "analyze_events", fake_analyze_events)
    return engine, memory


@pytest.mark.asyncio
async def test_pipeline_feedback_batch_archives_new_dislikes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """含反馈的批必须继承批线的 dislike 归档（归档而非删除）。"""
    engine, memory = _unified_engine(tmp_path, monkeypatch)

    await engine.pipeline.ingest(signal_from_feedback("dislike", "标题党视频", "太浅了"))
    await engine.wait_for_pending_edits()

    interests = memory.get_layer("preference").data["interests"]
    by_name = {str(item["name"]): item for item in interests}
    assert by_name["标题党"].get("state") == "archived"
    assert by_name["城市建筑"].get("state") != "archived"


@pytest.mark.asyncio
async def test_pipeline_ordinary_batch_does_not_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """不含反馈的普通批不走归档路径（避免把浏览误当避雷证据）。"""
    engine, memory = _unified_engine(tmp_path, monkeypatch)

    await engine.pipeline.ingest_batch(
        signals_from_events([{"event_type": "view", "title": f"视频{i}"} for i in range(3)])
    )
    await engine.wait_for_pending_edits()

    interests = memory.get_layer("preference").data["interests"]
    by_name = {str(item["name"]): item for item in interests}
    assert by_name["标题党"].get("state") != "archived"


@pytest.mark.asyncio
async def test_pipeline_feedback_batch_rebuild_goes_through_access_point_three(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """含反馈 + 显著变化 → 走接入点③（trigger=feedback_batch）；shadow 放行重建。"""
    from openbiliclaw.soul.posture_gate import ACCEPT, GateDecision

    engine, memory = _unified_engine(tmp_path, monkeypatch)
    gate = _StubGate("shadow", GateDecision(verdict=ACCEPT, enforced=False))
    engine._posture_gate = gate

    built: list[bool] = []

    async def fake_build(**kwargs: Any) -> Any:
        del kwargs
        from openbiliclaw.soul.profile import SoulProfile

        built.append(True)
        return SoulProfile(
            personality_portrait="重建后的画像。" * 20,
            core_traits=["理性"],
            cognitive_style=["结构化"],
            motivational_drivers=["把事情讲透"],
            current_phase="持续校准",
            values=["深度"],
            life_stage="稳定期",
            deep_needs=["被理解"],
        )

    monkeypatch.setattr(engine._profile_builder, "build", fake_build)

    await engine.pipeline.ingest(signal_from_feedback("dislike", "标题党视频", "太浅了"))
    await engine.wait_for_pending_edits()

    assert gate.calls, "含反馈的显著变化必须咨询接入点③"
    assert gate.calls[0]["write_point"] == "feedback_soul_rebuild"
    assert gate.calls[0]["change"]["trigger"] == "feedback_batch"
    assert built, "shadow/accept 必须放行重建"
    assert memory.get_layer("soul").data["core"]["core_traits"] == ["理性"]


@pytest.mark.asyncio
async def test_pipeline_feedback_batch_enforce_downgrade_abandons_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """enforce downgrade → 放弃重建，但偏好写入和缓冲消费不受影响。"""
    from openbiliclaw.soul.posture_gate import DOWNGRADE, GateDecision

    engine, memory = _unified_engine(tmp_path, monkeypatch)
    engine._posture_gate = _StubGate("enforce", GateDecision(verdict=DOWNGRADE, enforced=True))

    built: list[bool] = []

    async def fake_build(**kwargs: Any) -> Any:
        del kwargs
        built.append(True)
        raise AssertionError("enforce downgrade 必须在重建前拦下")

    monkeypatch.setattr(engine._profile_builder, "build", fake_build)

    await engine.pipeline.ingest(signal_from_feedback("dislike", "标题党视频", "太浅了"))
    await engine.wait_for_pending_edits()

    assert built == []
    assert memory.get_layer("preference").data["disliked_topics"] == ["标题党"]
    assert engine.pipeline._buffers[OnionLayer.INTEREST.value].signals == []


@pytest.mark.asyncio
async def test_pipeline_feedback_batch_ledger_source_is_feedback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """台账连续性：反馈触发的消费记 pipeline_layer_update 且 source=feedback。"""
    engine, memory = _unified_engine(tmp_path, monkeypatch)

    await engine.pipeline.ingest(signal_from_feedback("dislike", "标题党视频", "太浅了"))
    await engine.wait_for_pending_edits()

    rows = memory._database.query_profile_ledger(days=30, write_point="pipeline_layer_update")
    sources = {str(row["source"]) for row in rows}
    assert "feedback" in sources, f"反馈触发的兴趣消费必须记 source=feedback，实际 {sources}"


@pytest.mark.asyncio
async def test_pipeline_ordinary_batch_keeps_pipeline_ledger_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """普通批的台账 source 不变（pipeline:interest）。"""
    engine, memory = _unified_engine(tmp_path, monkeypatch)

    await engine.pipeline.ingest_batch(
        signals_from_events([{"event_type": "view", "title": f"视频{i}"} for i in range(3)])
    )
    await engine.wait_for_pending_edits()

    rows = memory._database.query_profile_ledger(days=30, write_point="pipeline_layer_update")
    sources = {str(row["source"]) for row in rows}
    assert "feedback" not in sources
    assert "pipeline:interest" in sources


@pytest.mark.asyncio
async def test_pipeline_feedback_batch_consumes_held_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """含反馈的批后必须消费 replaying 的搁置更新（批线 held-replay 钩子）。"""
    from openbiliclaw.soul.confusion import ConfusionManager, HeldUpdate

    engine, memory = _unified_engine(tmp_path, monkeypatch)
    db = memory._database
    manager = ConfusionManager(db)
    confusion_id = db.insert_confusion(topic="桌游", observation="看不懂")
    db.update_confusion(
        confusion_id,
        held_updates=[HeldUpdate(held_id="h1", topic="桌游", kind="upgrade", value=0.7).to_dict()],
    )
    manager.resolve(confusion_id, resolution="real_interest")
    assert manager.get(confusion_id).held_updates[0].state == "replaying"

    await engine.pipeline.ingest(signal_from_feedback("dislike", "标题党视频", "太浅了"))
    await engine.wait_for_pending_edits()

    assert manager.get(confusion_id).held_updates[0].state == "applied"


# ===========================================================================
# 统一兴趣更新线 — Task 1 契约在统一线上的对照
#
# tests/test_soul_engine.py::TestFeedbackBatchContract 钉的是旧批线的 6 条语义。
# 它们在统一线上的去向：
#   ① 阈值 3          → LayerBuffer 的反馈优先级规则（test_feedback_priority_*）
#   ② retraction 排除 → **有意变更**：改走 pipeline 既有折价（下面第一条）
#   ③ dislike 归档    → test_pipeline_feedback_batch_archives_new_dislikes
#   ④ 门控重建        → test_pipeline_feedback_batch_rebuild_goes_through_...
#   ⑤ held-replay     → test_pipeline_feedback_batch_consumes_held_replay
#   ⑥ 游标幂等        → 缓冲 drain 幂等（下面第二条；游标本身 Wave B 才退役）
# ===========================================================================


@pytest.mark.asyncio
async def test_unified_line_retraction_is_discounted_not_a_feedback_privilege(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """契约②有意变更：retraction 不再被整条排除，但也拿不到反馈特权。

    它走 BEHAVIOR_EVENT 通道（折价而非删除），因此不触发 dislike 归档，也不会
    把 pipeline_layer_update 的 source 改成 feedback。
    """
    engine, memory = _unified_engine(tmp_path, monkeypatch)

    signals = signals_from_events(
        [
            {
                "event_type": "feedback",
                "title": f"撤回 {index}",
                "url": f"https://www.bilibili.com/video/BV{index}",
                "metadata": {"feedback_type": "retraction", "retracted_action": "like"},
            }
            for index in range(3)
        ]
    )
    assert all(sig.signal_type is SignalType.BEHAVIOR_EVENT for sig in signals)

    await engine.pipeline.ingest_batch(signals)
    await engine.wait_for_pending_edits()

    interests = memory.get_layer("preference").data["interests"]
    by_name = {str(item["name"]): item for item in interests}
    assert by_name["标题党"].get("state") != "archived", "撤回不是避雷证据"
    rows = memory._database.query_profile_ledger(days=30, write_point="pipeline_layer_update")
    assert "feedback" not in {str(row["source"]) for row in rows}


@pytest.mark.asyncio
async def test_unified_line_consumed_batch_is_not_reconsumed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """契约⑥的统一线对应物：消费过的信号被 drain，二次 tick 不重算。"""
    engine, memory = _unified_engine(tmp_path, monkeypatch)
    del memory

    calls: list[int] = []
    original = engine._preference_analyzer.analyze_events

    async def counting_analyze(**kwargs: Any) -> dict[str, Any]:
        calls.append(1)
        return await original(**kwargs)

    monkeypatch.setattr(engine._preference_analyzer, "analyze_events", counting_analyze)

    await engine.pipeline.ingest(signal_from_feedback("dislike", "标题党视频", "太浅了"))
    assert engine.pipeline._buffers[OnionLayer.INTEREST.value].signals == []

    await engine.pipeline.tick()
    await engine.wait_for_pending_edits()

    assert calls == [1], "已消费的反馈不得被第二轮重复分析"
