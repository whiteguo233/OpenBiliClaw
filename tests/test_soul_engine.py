from __future__ import annotations

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from types import MappingProxyType, SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.llm.service import ModuleOverride
from openbiliclaw.memory.manager import MemoryManager
from openbiliclaw.soul.engine import SoulEngine
from openbiliclaw.soul.overrides import ProfileOverrides, apply_edit
from openbiliclaw.soul.preference_analyzer import DEFAULT_PREFERENCE_EVENT_CHUNK_SIZE
from openbiliclaw.soul.profile import (
    CoreLayer,
    InterestDomain,
    InterestLayer,
    InterestSpecific,
    OnionProfile,
    SoulProfile,
)

if TYPE_CHECKING:
    from pathlib import Path


class FakeRegistry:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[list[dict[str, str]]] = []

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
        reasoning_effort: str | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        self.calls.append(messages)
        return LLMResponse(content=self.content, provider="openai")


def test_dialogue_llm_learning_has_no_whole_job_timeout_wrapper() -> None:
    """Task 1.3 keeps provider timeouts and never cancels mid-mutation locally."""
    import inspect

    source = inspect.getsource(SoulEngine.learn_from_dialogue)

    assert "asyncio.wait_for(" not in source
    assert "asyncio.timeout(" not in source


def test_soul_engine_wires_module_overrides_to_internal_service(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    overrides = {"soul": ModuleOverride(provider="claude", model="claude-sonnet")}

    engine = SoulEngine(
        llm=FakeRegistry("{}"),
        memory=memory,
        module_overrides=overrides,
    )

    assert engine._module_overrides == overrides
    assert engine._llm_service.module_overrides == overrides


def test_soul_engine_wires_scheduler_speculation_config(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()

    engine = SoulEngine(
        llm=FakeRegistry("{}"),
        memory=memory,
        speculation_interval_minutes=22,
        speculation_ttl_days=8,
        speculation_cooldown_days=9,
        speculation_confirmation_threshold=4,
        speculation_max_active=6,
        speculation_max_primary_interests=17,
        speculation_max_secondary_interests=66,
        speculator_idle_interval_minutes=11,
        avoidance_speculation_interval_minutes=12,
        avoidance_speculation_ttl_days=4,
        avoidance_speculation_cooldown_days=8,
        avoidance_speculation_confirmation_threshold=2,
        avoidance_speculation_max_active=5,
    )

    assert engine._speculator._generation_interval_minutes == 22
    assert engine._speculator._default_ttl_days == 8
    assert engine._speculator._cooldown_days == 9
    assert engine._speculator._confirmation_threshold == 4
    assert engine._speculator._max_active == 6
    assert engine._speculator._max_primary_interests == 17
    assert engine._speculator._max_secondary_interests == 66
    assert engine._avoidance_speculator._generation_interval_minutes == 12
    assert engine._avoidance_speculator._default_ttl_days == 4
    assert engine._avoidance_speculator._cooldown_days == 8
    assert engine._avoidance_speculator._confirmation_threshold == 2
    assert engine._avoidance_speculator._max_active == 5
    assert engine._pipeline._speculator_idle_min_interval == timedelta(minutes=11)
    assert engine._pipeline._avoidance_speculator is engine._avoidance_speculator


@pytest.mark.asyncio
async def test_prepared_owner_cutovers_use_nonblocking_fast_path_while_lanes_are_busy(
    tmp_path: Path,
) -> None:
    """Ingress preparation must not wait behind an already-prepared owner."""
    memory = MemoryManager(tmp_path)
    memory.initialize()
    memory.get_layer("soul").data.update({"profile_ready": True})
    engine = SoulEngine(
        llm=FakeRegistry("{}"),
        memory=memory,
        unified_interest_line=True,
    )

    profile_cutover = await engine.prepare_profile_event_owner_cutover()
    feedback_cutover = await engine.prepare_feedback_owner_cutover()
    assert profile_cutover["profile_event_owner_version"] == 1
    assert feedback_cutover["feedback_owner_version"] == 2

    await engine._profile_event_lock.acquire()
    await engine._feedback_batch_lock.acquire()
    try:
        profile_fast = await asyncio.wait_for(
            engine.prepare_profile_event_owner_cutover(),
            timeout=0.05,
        )
        feedback_fast = await asyncio.wait_for(
            engine.prepare_feedback_owner_cutover(),
            timeout=0.05,
        )
    finally:
        engine._feedback_batch_lock.release()
        engine._profile_event_lock.release()

    assert profile_fast["prepared"] is False
    assert profile_fast["profile_event_owner_version"] == 1
    assert feedback_fast["prepared"] is False
    assert feedback_fast["feedback_owner_version"] == 2


@pytest.mark.asyncio
async def test_generic_retraction_projection_failure_keeps_cursor_then_retry_repairs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Projection is part of the generic owner's cursor commit protocol."""
    memory = MemoryManager(tmp_path)
    memory.initialize()
    memory.get_layer("soul").data.update({"profile_ready": True})
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    await engine.prepare_profile_event_owner_cutover()
    t0 = datetime(2026, 8, 1, 12, 0, 0)
    first_ms = int(t0.timestamp() * 1000)
    retract_ms = int((t0 + timedelta(hours=1)).timestamp() * 1000)
    await memory.persist_events_with_receipts(
        [
            {
                "event_type": "like",
                "url": "https://x.com/i/status/123",
                "title": "owned positive",
                "metadata": {
                    "profile_update_owner": "generic",
                    "signal_strength": 0.85,
                    "timestamp": first_ms,
                },
                "ingest_key": "test:generic-like",
            },
            {
                "event_type": "view",
                "url": "https://example.com/unowned",
                "title": "strictly unowned",
                "metadata": {"timestamp": first_ms},
                "ingest_key": "test:unowned-view",
            },
            {
                "event_type": "feedback",
                "url": "https://x.com/user/status/123",
                "title": "owned retraction",
                "metadata": {
                    "profile_update_owner": "generic",
                    "feedback_type": "retraction",
                    "retracted_action": "like",
                    "timestamp": retract_ms,
                },
                "ingest_key": "test:generic-retraction",
            },
        ]
    )
    tick_calls = 0

    async def fake_tick() -> object:
        nonlocal tick_calls
        tick_calls += 1
        return SimpleNamespace(layers_updated=[])

    monkeypatch.setattr(engine._pipeline, "tick_if_buffered", fake_tick)
    real_projection = memory.apply_retraction_db_marks

    def fail_projection(_events: list[dict[str, object]]) -> int:
        raise RuntimeError("projection unavailable")

    monkeypatch.setattr(memory, "apply_retraction_db_marks", fail_projection)
    with pytest.raises(RuntimeError, match="projection unavailable"):
        await engine.process_profile_events_if_needed()
    assert engine._pipeline.consumer_checkpoint("profile_events")["cursor"] == 0
    assert tick_calls == 0

    monkeypatch.setattr(memory, "apply_retraction_db_marks", real_projection)
    repaired = await engine.process_profile_events_if_needed()

    assert repaired["scanned"] == 3
    assert repaired["enqueued"] == 2
    assert engine._pipeline.consumer_checkpoint("profile_events")["cursor"] == 3
    assert tick_calls == 1
    [positive] = memory.query_events(event_types=["like"], limit=10)
    metadata = json.loads(str(positive["metadata"]))
    assert metadata["retracted"] is True
    assert float(metadata["signal_strength"]) <= 0.2


@pytest.mark.asyncio
async def test_generic_owner_empty_recovery_skips_tick_maintenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A no-op durable cursor scan must not start periodic LLM maintenance."""
    memory = MemoryManager(tmp_path)
    memory.initialize()
    memory.get_layer("soul").data.update({"profile_ready": True})
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    await engine.prepare_profile_event_owner_cutover()
    maintenance_calls = 0

    async def unexpected_maintenance(*_args: object, **_kwargs: object) -> None:
        nonlocal maintenance_calls
        maintenance_calls += 1

    monkeypatch.setattr(
        engine._pipeline,
        "_run_tick_maintenance",
        unexpected_maintenance,
    )

    result = await engine.process_profile_events_if_needed()

    assert result["scanned"] == 0
    assert result["enqueued"] == 0
    assert maintenance_calls == 0


@pytest.mark.asyncio
async def test_replay_held_updates_applies_and_is_idempotent(tmp_path: Path) -> None:
    from openbiliclaw.soul.confusion import ConfusionManager, HeldUpdate
    from openbiliclaw.storage.database import Database

    memory = MemoryManager(tmp_path)
    memory.initialize()
    db = Database(tmp_path / "confusion.db")
    db.initialize()
    registry = FakeRegistry(
        json.dumps(
            {"interests": [{"name": "桌游", "category": "游戏", "weight": 0.7}]},
            ensure_ascii=False,
        )
    )
    engine = SoulEngine(llm=registry, memory=memory, database=db)

    mgr = ConfusionManager(db)
    cid = db.insert_confusion(topic="桌游", observation="x")
    db.update_confusion(
        cid,
        held_updates=[HeldUpdate(held_id="h1", topic="桌游", kind="upgrade", value=0.7).to_dict()],
    )
    mgr.resolve(cid, resolution="real_interest")  # → replaying (with receipt)

    result = await engine.replay_held_updates()
    assert result["confusions"] == 1
    assert mgr.get(cid).held_updates[0].state == "applied"

    # Idempotent: nothing left replaying.
    again = await engine.replay_held_updates()
    assert again["replayed"] == 0


@pytest.mark.asyncio
async def test_analyze_events_updates_preference_layer(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    registry = FakeRegistry(
        json.dumps(
            {
                "interests": [
                    {"name": "历史", "category": "知识", "weight": 0.82, "source": "events"}
                ],
                "favorite_up_users": ["小约翰可汗"],
                "exploration_openness": 0.63,
            },
            ensure_ascii=False,
        )
    )
    engine = SoulEngine(llm=registry, memory=memory)

    await engine.analyze_events(
        [
            {"event_type": "view", "title": "世界史解说"},
            {"event_type": "search", "title": "纪录片推荐", "metadata": {"keyword": "纪录片"}},
        ]
    )

    preference = memory.get_layer("preference").data
    assert preference["interests"][0]["name"] == "历史"
    assert preference["favorite_up_users"] == ["小约翰可汗"]

    saved = json.loads((tmp_path / "memory" / "preference.json").read_text(encoding="utf-8"))
    assert saved["interests"][0]["name"] == "历史"
    assert registry.calls


@pytest.mark.asyncio
async def test_build_initial_profile_reads_preference_and_saves_soul(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    memory.get_layer("preference").data.update(
        {
            "interests": [{"name": "科技", "category": "知识", "weight": 0.81}],
            "favorite_up_users": ["老师好我叫何同学"],
        }
    )
    registry = FakeRegistry(
        json.dumps(
            {
                "personality_portrait": (
                    "这个人会反复在高信息密度内容里停留，也会主动寻找讲清原理的表达方式。" * 8
                ),
                "core_traits": ["理性", "好奇", "克制"],
                "cognitive_style": ["会先看结构", "偏好把问题讲透"],
                "motivational_drivers": ["建立判断确定性", "扩大理解边界"],
                "current_phase": "最近更像在主动吸收复杂信息，并整理自己的判断框架。",
                "values": ["成长", "真实"],
                "life_stage": "处于探索与积累阶段",
                "deep_needs": ["被理解", "持续成长"],
            },
            ensure_ascii=False,
        )
    )
    engine = SoulEngine(llm=registry, memory=memory)
    probe_calls: list[str] = []

    async def _record_interest_probe(*_args: object, **_kwargs: object) -> None:
        probe_calls.append("interest")

    async def _record_avoidance_probe(*_args: object, **_kwargs: object) -> None:
        probe_calls.append("avoidance")

    monkeypatch.setattr(engine._speculator, "force_tick", _record_interest_probe)
    monkeypatch.setattr(
        engine._avoidance_speculator,
        "force_tick",
        _record_avoidance_probe,
    )

    profile = await engine.build_initial_profile(
        history=[
            {"title": "AI 工具实测", "author": "科技UP主"},
            {"title": "效率系统分享", "author": "知识UP主"},
        ]
    )

    assert profile.core_traits == ["理性", "好奇", "克制"]
    assert profile.cognitive_style == ["会先看结构", "偏好把问题讲透"]
    assert profile.motivational_drivers == ["建立判断确定性", "扩大理解边界"]
    assert profile.current_phase == "最近更像在主动吸收复杂信息，并整理自己的判断框架。"
    saved = json.loads((tmp_path / "memory" / "soul.json").read_text(encoding="utf-8"))
    assert saved["core"]["core_traits"] == ["理性", "好奇", "克制"]
    assert saved["surface"]["cognitive_style"] == ["会先看结构", "偏好把问题讲透"]
    assert saved["interest"]["likes"][0]["domain"] == "知识"
    assert saved["interest"]["likes"][0]["specifics"][0]["name"] == "科技"
    # Profile persistence is the strict init barrier. RuntimeContext schedules
    # optional probes only after guided init leaves the load-bearing stages.
    assert probe_calls == []


@pytest.mark.asyncio
async def test_init_cognition_context_leaves_preference_and_feeds_profile_build(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)

    async def fake_analyze_events(
        *,
        events: list[dict[str, object]],
        existing_preference: dict[str, object],
        event_chunk_size: int = 0,
        progress_callback: object | None = None,
        llm_concurrency: int | None = None,
    ) -> dict[str, object]:
        del events, existing_preference, event_chunk_size, progress_callback, llm_concurrency
        return {
            "interests": [{"name": "AI 工具链", "category": "科技", "weight": 0.81}],
            "style": {},
            "context": {},
            "exploration_openness": 0.5,
            "disliked_topics": [],
            "favorite_up_users": [],
            "_init_cognition_context": {
                "awareness": [
                    {
                        "observation": "初始化 chunk 观察到用户持续停留在工具链内容。",
                        "trend": "从泛泛探索转向工作流验证。",
                        "emotion_guess": "对掌控感有需求。",
                    }
                ],
                "insights": [
                    {
                        "hypothesis": "用户可能在寻找可支撑长期产出的工具化路径。",
                        "evidence": ["多个初始化 chunk 都指向工具链和长期项目。"],
                        "confidence": 0.73,
                    }
                ],
            },
        }

    captured: dict[str, object] = {}

    async def fake_build(
        *,
        history: list[dict[str, object]],
        preference: dict[str, object],
        awareness_notes: list[dict[str, object]],
        active_insights: list[dict[str, object]],
    ) -> SoulProfile:
        del history, preference
        captured["awareness_notes"] = awareness_notes
        captured["active_insights"] = active_insights
        return SoulProfile(
            personality_portrait="你会在复杂信息里寻找能落地的结构。" * 8,
            core_traits=["结构感强"],
            cognitive_style=["先看框架"],
            motivational_drivers=["把想法落地"],
            current_phase="正在验证长期工作流。",
            values=["真实有效"],
            life_stage="自我构建阶段",
            deep_needs=["可控的推进感"],
        )

    monkeypatch.setattr(engine._preference_analyzer, "analyze_events", fake_analyze_events)
    monkeypatch.setattr(engine._profile_builder, "build", fake_build)

    await engine.analyze_events([{"event_type": "view", "title": "AI 工具链实战"}])

    preference = memory.get_layer("preference").data
    assert "_init_cognition_context" not in preference
    saved_preference = json.loads(
        (tmp_path / "memory" / "preference.json").read_text(encoding="utf-8")
    )
    assert "_init_cognition_context" not in saved_preference

    await engine.build_initial_profile(history=[{"title": "AI 工具链实战"}])

    # Exactly one of each: the draft is persisted *and* still carried in the
    # in-memory context, so the profile builder must dedup rather than weigh the
    # same observation twice.
    notes = cast("list[dict[str, object]]", captured["awareness_notes"])
    insights = cast("list[dict[str, object]]", captured["active_insights"])
    assert [note["observation"] for note in notes] == [
        "初始化 chunk 观察到用户持续停留在工具链内容。"
    ]
    assert notes[0]["trend"] == "从泛泛探索转向工作流验证。"
    assert notes[0]["emotion_guess"] == "对掌控感有需求。"
    assert [item["hypothesis"] for item in insights] == [
        "用户可能在寻找可支撑长期产出的工具化路径。"
    ]
    assert insights[0]["evidence"] == ["多个初始化 chunk 都指向工具链和长期项目。"]
    assert insights[0]["confidence"] == 0.73
    assert insights[0]["validated"] is False
    # The context key must not survive inside the preference layer, but the
    # drafts themselves now reach the long-term layers — see
    # TestInitCognitionDraftsArePersisted for that contract. They used to be
    # dropped after shaping the first portrait, which left a fresh install with
    # nothing to ask the user about.
    assert [note["observation"] for note in memory.get_layer("awareness").data["notes"]] == [
        "初始化 chunk 观察到用户持续停留在工具链内容。"
    ]
    assert [item["hypothesis"] for item in memory.get_layer("insight").data["hypotheses"]] == [
        "用户可能在寻找可支撑长期产出的工具化路径。"
    ]


@pytest.mark.asyncio
async def test_get_profile_loads_saved_soul_profile(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    memory.get_layer("soul").data.update(
        {
            "personality_portrait": (
                "这是一个偏爱深度内容、对信息质量较敏感、做决定前会先观察的人。" * 8
            ),
            "core_traits": ["理性", "谨慎", "自驱"],
            "cognitive_style": ["偏好先看证据再判断"],
            "motivational_drivers": ["保持判断稳固"],
            "current_phase": "最近更像在稳住判断，不急着跟风。",
            "values": ["真实", "成长"],
            "life_stage": "稳定积累阶段",
            "deep_needs": ["被理解", "保持成长"],
            "preferences": {"interests": [{"name": "科技", "category": "知识", "weight": 0.8}]},
        }
    )
    memory.get_layer("soul").save()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)

    profile = await engine.get_profile()

    assert profile.core_traits == ["理性", "谨慎", "自驱"]
    assert profile.cognitive_style == ["偏好先看证据再判断"]
    assert profile.current_phase == "最近更像在稳住判断，不急着跟风。"
    interest_names = [i.name for i in profile.preferences.interests]
    assert "知识" in interest_names  # domain (一级)
    assert "科技" in interest_names  # specific (二级)


@pytest.mark.asyncio
async def test_get_profile_raises_when_soul_not_initialized(tmp_path: Path) -> None:
    from openbiliclaw.soul.engine import SoulProfileNotInitializedError

    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)

    with pytest.raises(SoulProfileNotInitializedError):
        await engine.get_profile()


@pytest.mark.asyncio
async def test_generate_awareness_note_saves_awareness_layer(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    await memory.propagate_event(
        {"event_type": "view", "title": "AI 工具实测", "metadata": {"keyword": "AI"}}
    )
    memory.get_layer("soul").data.update(
        {
            "personality_portrait": (
                "这是一个偏爱深度内容、会主动寻找原理解释、决策比较克制的人。" * 8
            ),
            "core_traits": ["理性", "谨慎", "自驱"],
        }
    )
    registry = FakeRegistry(
        json.dumps(
            [
                {
                    "date": "2026-03-08",
                    "observation": "最近连续浏览高信息密度内容。",
                    "trend": "更偏向深度解释。",
                    "emotion_guess": "可能处于主动吸收信息的阶段。",
                }
            ],
            ensure_ascii=False,
        )
    )
    engine = SoulEngine(llm=registry, memory=memory)

    note = await engine.generate_awareness_note()

    assert "高信息密度" in note
    awareness_data = memory.get_layer("awareness").data
    assert awareness_data["notes"][0]["observation"] == "最近连续浏览高信息密度内容。"


@pytest.mark.asyncio
async def test_generate_insight_saves_insight_layer(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    memory.get_layer("awareness").data.update(
        {
            "notes": [
                {
                    "date": "2026-03-08",
                    "observation": "最近连续浏览高信息密度内容。",
                    "trend": "更偏向深度解释。",
                    "emotion_guess": "专注",
                }
            ]
        }
    )
    memory.get_layer("soul").data.update(
        {
            "personality_portrait": (
                "这是一个偏爱深度内容、会主动寻找原理解释、决策比较克制的人。" * 8
            ),
            "core_traits": ["理性", "谨慎", "自驱"],
        }
    )
    registry = FakeRegistry(
        json.dumps(
            [
                {
                    "hypothesis": "用户可能通过深度内容获得掌控感。",
                    "evidence": ["最近连续浏览高信息密度内容。"],
                    "confidence": 0.62,
                }
            ],
            ensure_ascii=False,
        )
    )
    engine = SoulEngine(llm=registry, memory=memory)

    insight = await engine.generate_insight()

    assert "掌控感" in insight
    insight_data = memory.get_layer("insight").data
    assert insight_data["hypotheses"][0]["hypothesis"] == "用户可能通过深度内容获得掌控感。"
    assert insight_data["hypotheses"][0]["validated"] is False


@pytest.mark.asyncio
async def test_update_from_feedback_persists_feedback_and_marks_insight_validated(
    tmp_path: Path,
) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    memory.get_layer("insight").data.update(
        {
            "hypotheses": [
                {
                    "hypothesis": "用户可能通过深度内容获得掌控感。",
                    "evidence": ["最近连续浏览高信息密度内容。"],
                    "confidence": 0.62,
                    "validated": False,
                    "created_at": "2026-03-08",
                }
            ]
        }
    )
    engine = SoulEngine(llm=FakeRegistry("[]"), memory=memory)

    await engine.update_from_feedback(
        {"hypothesis": "用户可能通过深度内容获得掌控感。", "signal": "confirm"}
    )

    insight_data = memory.get_layer("insight").data
    assert insight_data["hypotheses"][0]["validated"] is True
    feedback_events = memory.query_events(event_types=["feedback"])
    assert feedback_events[0]["event_type"] == "feedback"


@pytest.mark.asyncio
async def test_process_feedback_batch_if_needed_skips_below_threshold(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    await memory.propagate_event(
        {
            "event_type": "feedback",
            "title": "讲透城市与建筑",
            "metadata": {"feedback_type": "dislike", "bvid": "BV1A"},
        }
    )

    result = await engine.process_feedback_batch_if_needed()

    assert result == {
        "triggered": False,
        "feedback_count": 1,
        "preference_updated": False,
        "profile_rebuilt": False,
    }


def test_record_immediate_feedback_cognition_adds_comment_update(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)

    engine.record_immediate_feedback_cognition(
        feedback_type="comment",
        title="讲透城市与建筑",
        note="这个方向对，但希望更深入一点。",
    )

    updates = memory.load_cognition_updates()
    assert len(updates) == 1
    assert updates[0]["kind"] == "profile_shift"
    assert "讲透城市与建筑" in str(updates[0]["summary"])
    assert "结合评论内容判断" in str(updates[0]["impact"])
    assert "中性直接反馈" in str(updates[0]["reasoning"])
    assert "讲透城市与建筑" in str(updates[0]["evidence"])
    assert "这个方向对，但希望更深入一点。" in str(updates[0]["evidence"])
    assert updates[0]["source"] == "feedback"
    assert updates[0]["context_line"] == "来自：《讲透城市与建筑》"
    assert updates[0]["source_label"] == "推荐反馈"
    assert updates[0]["expand_hint"] == "expandable"


def test_record_immediate_feedback_cognition_adds_dislike_update(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)

    engine.record_immediate_feedback_cognition(
        feedback_type="dislike",
        title="宏大叙事热榜内容",
        note="太浅了",
    )

    updates = memory.load_cognition_updates()
    assert len(updates) == 1
    assert updates[0]["kind"] == "dislike_added"
    assert "宏大叙事热榜内容" in str(updates[0]["summary"])
    assert "避雷" in str(updates[0]["impact"])
    assert "明确负反馈" in str(updates[0]["reasoning"])
    assert "太浅了" in str(updates[0]["evidence"])
    assert updates[0]["source"] == "feedback"
    assert updates[0]["context_line"] == "来自：《宏大叙事热榜内容》"
    assert updates[0]["source_label"] == "推荐反馈"
    assert updates[0]["expand_hint"] == "expandable"


def test_record_immediate_feedback_cognition_adds_like_update(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)

    engine.record_immediate_feedback_cognition(
        feedback_type="like",
        title="讲透城市与建筑",
        note="这条不错",
    )

    updates = memory.load_cognition_updates()
    assert len(updates) == 1
    assert updates[0]["kind"] == "interest_added"
    assert "讲透城市与建筑" in str(updates[0]["summary"])
    assert "偏好会更明确" in str(updates[0]["impact"])
    assert "明确正反馈" in str(updates[0]["reasoning"])
    assert "这条不错" in str(updates[0]["evidence"])
    assert updates[0]["source"] == "feedback"
    assert updates[0]["context_line"] == "来自：《讲透城市与建筑》"
    assert updates[0]["source_label"] == "推荐反馈"
    assert updates[0]["expand_hint"] == "expandable"


@pytest.mark.asyncio
async def test_process_feedback_batch_updates_preference_after_threshold(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    for index in range(3):
        await memory.propagate_event(
            {
                "event_type": "feedback",
                "title": f"反馈 {index}",
                "metadata": {"feedback_type": "dislike", "bvid": f"BV{index}"},
            }
        )

    async def fake_analyze_events(
        *,
        events: list[dict[str, object]],
        existing_preference: dict[str, object],
        event_chunk_size: int = 0,
    ) -> dict[str, object]:
        assert len(events) == 3
        assert event_chunk_size == DEFAULT_PREFERENCE_EVENT_CHUNK_SIZE
        return {
            "interests": [
                {"name": "纪录片", "category": "知识", "weight": 0.9, "source": "feedback"}
            ],
            "style": {},
            "context": {},
            "exploration_openness": 0.4,
            "disliked_topics": ["标题党"],
            "favorite_up_users": [],
        }

    monkeypatch.setattr(engine._preference_analyzer, "analyze_events", fake_analyze_events)

    result = await engine.process_feedback_batch_if_needed()

    assert result["triggered"] is True
    assert result["feedback_count"] == 3
    assert result["preference_updated"] is True
    assert memory.get_layer("preference").data["interests"][0]["name"] == "纪录片"
    assert memory.load_feedback_state()["last_processed_feedback_event_id"] > 0


@pytest.mark.asyncio
async def test_process_feedback_batch_ignores_retractions_for_threshold(tmp_path: Path) -> None:
    """3 retraction rows alone must NOT reach the feedback_batch_threshold —
    retraction is a neutralization, not preference-learning input."""
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    for index in range(3):
        await memory.propagate_event(
            {
                "event_type": "feedback",
                "title": f"withdrawn {index}",
                "metadata": {"feedback_type": "retraction", "retracted_action": "like"},
            }
        )

    result = await engine.process_feedback_batch_if_needed()

    assert result["triggered"] is False
    assert result["feedback_count"] == 0


@pytest.mark.asyncio
async def test_process_feedback_batch_excludes_retractions_from_count_and_input(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A mix of 2 dislikes + 2 retractions counts 2 (below threshold=3), and
    when it does fire the analysis input carries no retraction rows."""
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    # Interleave so a naive "last N" slice would include retractions.
    rows = [
        ("dislike", "dis 0"),
        ("retraction", "ret 0"),
        ("dislike", "dis 1"),
        ("retraction", "ret 1"),
    ]
    for feedback_type, title in rows:
        await memory.propagate_event(
            {
                "event_type": "feedback",
                "title": title,
                "metadata": {"feedback_type": feedback_type},
            }
        )

    # 2 real feedback rows < threshold(3) → below threshold.
    below = await engine.process_feedback_batch_if_needed()
    assert below["triggered"] is False
    assert below["feedback_count"] == 2

    # Add one more real dislike → 3 real rows, fires; input excludes retractions.
    await memory.propagate_event(
        {
            "event_type": "feedback",
            "title": "dis 2",
            "metadata": {"feedback_type": "dislike"},
        }
    )

    captured: list[dict[str, object]] = []

    async def fake_analyze_events(
        *,
        events: list[dict[str, object]],
        existing_preference: dict[str, object],
        event_chunk_size: int = 0,
    ) -> dict[str, object]:
        del existing_preference, event_chunk_size
        captured.extend(events)
        return {
            "interests": [],
            "style": {},
            "context": {},
            "exploration_openness": 0.4,
            "disliked_topics": [],
            "favorite_up_users": [],
        }

    monkeypatch.setattr(engine._preference_analyzer, "analyze_events", fake_analyze_events)

    fired = await engine.process_feedback_batch_if_needed()
    assert fired["triggered"] is True
    assert fired["feedback_count"] == 3
    assert len(captured) == 3
    for event in captured:
        metadata = event.get("metadata")
        if isinstance(metadata, dict):
            assert metadata.get("feedback_type") != "retraction"
        assert "ret " not in str(event.get("title", ""))


@pytest.mark.asyncio
async def test_process_feedback_batch_single_flights_concurrent_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    for index in range(3):
        await memory.propagate_event(
            {
                "event_type": "feedback",
                "title": f"反馈 {index}",
                "metadata": {"feedback_type": "dislike", "bvid": f"BV{index}"},
            }
        )

    calls = 0

    async def fake_analyze_events(
        *,
        events: list[dict[str, object]],
        existing_preference: dict[str, object],
        event_chunk_size: int = 0,
    ) -> dict[str, object]:
        nonlocal calls
        del events, existing_preference, event_chunk_size
        calls += 1
        await asyncio.sleep(0.02)
        return {
            "interests": [
                {"name": "纪录片", "category": "知识", "weight": 0.9, "source": "feedback"}
            ],
            "style": {},
            "context": {},
            "exploration_openness": 0.4,
            "disliked_topics": ["标题党"],
            "favorite_up_users": [],
        }

    monkeypatch.setattr(engine._preference_analyzer, "analyze_events", fake_analyze_events)

    results = await asyncio.gather(*(engine.process_feedback_batch_if_needed() for _ in range(5)))

    assert calls == 1
    assert sum(1 for item in results if item["triggered"] is True) == 1
    assert sum(1 for item in results if item.get("skipped") is True) == 4


@pytest.mark.asyncio
async def test_process_feedback_batch_compacts_noisy_metadata_before_llm(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    for index in range(3):
        await memory.propagate_event(
            {
                "event_type": "feedback",
                "title": f"反馈 {index}",
                "url": f"https://www.bilibili.com/video/BV{index}",
                "context": f"在 B 站踩了《反馈 {index}》",
                "metadata": {
                    "feedback_type": "dislike",
                    "reaction": "thumbs_down",
                    "bvid": f"BV{index}",
                    "source_platform": "bilibili",
                    "feedback_note": "太浅了",
                    "targetText": "x" * 5000,
                    "raw_context": {"viewport": {"width": 1920, "height": 1080}},
                    "href": "https://example.invalid/noisy",
                    "actionLabel": "不感兴趣",
                },
            }
        )

    captured_events: list[dict[str, object]] = []

    async def fake_analyze_events(
        *,
        events: list[dict[str, object]],
        existing_preference: dict[str, object],
        event_chunk_size: int = 0,
    ) -> dict[str, object]:
        del existing_preference, event_chunk_size
        captured_events.extend(events)
        return {
            "interests": [],
            "style": {},
            "context": {},
            "exploration_openness": 0.4,
            "disliked_topics": ["太浅了"],
            "favorite_up_users": [],
        }

    monkeypatch.setattr(engine._preference_analyzer, "analyze_events", fake_analyze_events)

    await engine.process_feedback_batch_if_needed()

    assert captured_events
    metadata = captured_events[0]["metadata"]
    assert isinstance(metadata, dict)
    assert metadata["feedback_type"] == "dislike"
    assert metadata["reaction"] == "thumbs_down"
    assert metadata["bvid"] == "BV0"
    assert metadata["source_platform"] == "bilibili"
    assert metadata["feedback_note"] == "太浅了"
    assert "targetText" not in metadata
    assert "raw_context" not in metadata
    assert "href" not in metadata
    assert "actionLabel" not in metadata


@pytest.mark.asyncio
async def test_process_feedback_batch_reads_all_incremental_feedback_events(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    for index in range(503):
        await memory.propagate_event(
            {
                "event_type": "feedback",
                "title": f"反馈 {index}",
                "metadata": {"feedback_type": "like", "bvid": f"BV{index:04d}"},
            }
        )

    captured_ids: list[int] = []

    async def fake_analyze_events(
        *,
        events: list[dict[str, object]],
        existing_preference: dict[str, object],
        event_chunk_size: int = 0,
    ) -> dict[str, object]:
        del existing_preference, event_chunk_size
        captured_ids.extend(int(event["id"]) for event in events)
        return {
            "interests": [],
            "style": {},
            "context": {},
            "exploration_openness": 0.4,
            "disliked_topics": [],
            "favorite_up_users": [],
        }

    monkeypatch.setattr(engine._preference_analyzer, "analyze_events", fake_analyze_events)

    await engine.process_feedback_batch_if_needed()

    assert len(captured_ids) == 503
    assert captured_ids == list(range(1, 504))
    assert memory.load_feedback_state()["last_processed_feedback_event_id"] == 503


@pytest.mark.asyncio
async def test_feedback_signal_strength_reaches_profile_update_prompt_and_profile_rebuild(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    registry = FakeRegistry(
        json.dumps(
            {
                "interests": [
                    {
                        "name": "城市建筑深度内容",
                        "category": "文化",
                        "weight": 0.86,
                        "source": "feedback",
                    }
                ],
                "style": {"depth_preference": 0.9},
                "context": {},
                "exploration_openness": 0.58,
                "disliked_topics": ["浅层推荐"],
                "favorite_up_users": [],
            },
            ensure_ascii=False,
        )
    )
    engine = SoulEngine(llm=registry, memory=memory)

    await memory.propagate_event(
        {
            "event_type": "feedback",
            "title": "讲透城市与建筑",
            "metadata": {
                "feedback_type": "comment",
                "feedback_note": "方向对，但我想看更深一点。",
            },
        }
    )
    await memory.propagate_event(
        {
            "event_type": "feedback",
            "title": "泛泛而谈的城市内容",
            "metadata": {"feedback_type": "dismiss"},
        }
    )
    await memory.propagate_event(
        {
            "event_type": "feedback",
            "title": "结构讲得很清楚的建筑分析",
            "metadata": {"feedback_type": "like"},
        }
    )

    async def fake_build(
        *,
        history: list[dict[str, object]],
        preference: dict[str, object],
        awareness_notes: list[dict[str, object]],
        active_insights: list[dict[str, object]],
    ) -> object:
        from openbiliclaw.soul.profile import SoulProfile

        assert history == []
        assert preference["interests"][0]["name"] == "城市建筑深度内容"
        assert preference["disliked_topics"] == ["浅层推荐"]
        return SoulProfile(
            personality_portrait="你最近更明显在找能把空间、城市和人的选择讲透的内容。" * 8,
            core_traits=["理性", "耐心"],
            cognitive_style=["更看重结构解释", "不满足于泛泛推荐"],
            motivational_drivers=["看见复杂内容背后的脉络"],
            current_phase="正在把推荐反馈收束成更明确的深度内容偏好。",
            values=["真实", "深度"],
            life_stage="持续校准内容口味",
            deep_needs=["被更准确地理解"],
        )

    monkeypatch.setattr(engine._profile_builder, "build", fake_build)

    result = await engine.process_feedback_batch_if_needed()

    assert result["triggered"] is True
    assert result["feedback_count"] == 3
    assert result["preference_updated"] is True
    assert result["profile_rebuilt"] is True

    prompt_text = "\n".join(message["content"] for call in registry.calls for message in call)
    assert '"feedback_type": "comment"' in prompt_text
    assert '"signal_strength": 0.8' in prompt_text
    assert '"feedback_type": "dismiss"' in prompt_text
    assert '"signal_strength": 0.5' in prompt_text
    assert '"feedback_type": "like"' in prompt_text
    assert '"signal_strength": 1.0' in prompt_text

    preference = memory.get_layer("preference").data
    assert preference["interests"][0]["name"] == "城市建筑深度内容"
    assert preference["interests"][0]["weight"] == 0.86
    assert preference["disliked_topics"] == ["浅层推荐"]
    soul = memory.get_layer("soul").data
    assert soul["core"]["core_traits"] == ["理性", "耐心"]
    assert "更明确的深度内容偏好" in soul["role"]["current_phase"]


@pytest.mark.asyncio
async def test_process_feedback_batch_new_dislikes_trigger_pool_purge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import openbiliclaw.soul.dislike_writeback as dislike_writeback

    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    for index in range(3):
        await memory.propagate_event(
            {
                "event_type": "feedback",
                "title": f"反馈 {index}",
                "metadata": {"feedback_type": "dislike", "bvid": f"BV{index}"},
            }
        )

    async def fake_analyze_events(
        *,
        events: list[dict[str, object]],
        existing_preference: dict[str, object],
        event_chunk_size: int = 0,
    ) -> dict[str, object]:
        return {
            "interests": [],
            "style": {},
            "context": {},
            "exploration_openness": 0.4,
            "disliked_topics": ["标题党"],
            "favorite_up_users": [],
        }

    calls: list[dict[str, object]] = []

    async def fake_purge_pool_for_new_dislikes(**kwargs: object) -> list[str]:
        calls.append(dict(kwargs))
        return []

    monkeypatch.setattr(engine._preference_analyzer, "analyze_events", fake_analyze_events)
    monkeypatch.setattr(
        dislike_writeback,
        "purge_pool_for_new_dislikes",
        fake_purge_pool_for_new_dislikes,
    )

    result = await engine.process_feedback_batch_if_needed()
    await engine.wait_for_pending_edits()

    assert result["triggered"] is True
    assert len(calls) == 1
    assert calls[0]["newly_added"] == ["标题党"]
    assert calls[0]["all_dislikes"] == ["标题党"]
    assert calls[0]["database"] is memory._database


@pytest.mark.asyncio
async def test_learn_from_dialogue_persists_event_and_candidate_below_threshold(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)

    async def fake_extract(
        *,
        user_message: str,
        assistant_reply: str,
        core_memory: dict[str, object],
        active_list: dict[str, object] | None = None,
    ) -> list[dict[str, object]]:
        assert core_memory["soul_summary"]["personality_portrait"] == ""
        return [
            {
                "kind": "goal",
                "content": "想更系统地理解国际局势",
                "confidence": 0.62,
                "evidence": user_message,
            }
        ]

    monkeypatch.setattr(engine._dialogue_insight_analyzer, "extract", fake_extract)

    result = await engine.learn_from_dialogue(
        user_message="我最近总想把国际新闻看得更透一点。",
        assistant_reply="听起来你不是只想知道发生了什么，而是想理解背后的结构。",
        session="cli",
    )

    assert result["event_logged"] is True
    assert result["candidate_count"] == 1
    assert result["profile_rebuilt"] is False
    dialogue_events = memory.query_events(event_types=["dialogue"])
    assert len(dialogue_events) == 1
    assert dialogue_events[0]["title"] == "我最近总想把国际新闻看得更透一点。"
    candidates = memory.load_insight_candidates()
    assert candidates[0]["occurrences"] == 1
    assert candidates[0]["kind"] == "goal"


@pytest.mark.asyncio
async def test_learn_from_dialogue_updates_preference_for_single_high_confidence_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)

    async def fake_extract(
        *,
        user_message: str,
        assistant_reply: str,
        core_memory: dict[str, object],
        active_list: dict[str, object] | None = None,
    ) -> list[dict[str, object]]:
        return [
            {
                "kind": "interest",
                "content": "国际新闻背后的因果链",
                "confidence": 0.91,
                "evidence": user_message,
            }
        ]

    async def fake_analyze_events(
        *,
        events: list[dict[str, object]],
        existing_preference: dict[str, object],
    ) -> dict[str, object]:
        assert events[0]["event_type"] == "dialogue_insight"
        assert events[0]["metadata"]["occurrences"] == 1
        return {
            "interests": [
                {
                    "name": "国际时事",
                    "category": "知识",
                    "weight": 0.5,
                    "source": "dialogue",
                }
            ],
            "style": {},
            "context": {},
            "exploration_openness": 0.5,
            "disliked_topics": [],
            "favorite_up_users": [],
        }

    monkeypatch.setattr(engine._dialogue_insight_analyzer, "extract", fake_extract)
    monkeypatch.setattr(engine._preference_analyzer, "analyze_events", fake_analyze_events)

    result = await engine.learn_from_dialogue(
        user_message="我很明确地想看国际新闻背后的因果链。",
        assistant_reply="你是在找能解释结构和因果的内容。",
        session="popup",
    )

    assert result["preference_updated"] is True
    assert result["profile_rebuilt"] is False
    candidates = memory.load_insight_candidates()
    assert candidates[0]["applied"] is True
    assert memory.get_layer("preference").data["interests"][0]["name"] == "国际时事"


@pytest.mark.asyncio
async def test_learn_from_dialogue_new_dislike_triggers_pool_purge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import openbiliclaw.soul.dislike_writeback as dislike_writeback

    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    profile_build_started = asyncio.Event()
    release_profile_build = asyncio.Event()
    purge_started = asyncio.Event()

    async def fake_extract(
        *,
        user_message: str,
        assistant_reply: str,
        core_memory: dict[str, object],
        active_list: dict[str, object] | None = None,
    ) -> list[dict[str, object]]:
        del assistant_reply, core_memory
        return [
            {
                "kind": "dislike",
                "content": "电脑使用技巧",
                "confidence": 0.95,
                "evidence": user_message,
            }
        ]

    async def fake_analyze_events(
        *,
        events: list[dict[str, object]],
        existing_preference: dict[str, object],
    ) -> dict[str, object]:
        del existing_preference
        assert events[0]["metadata"]["kind"] == "dislike"
        return {
            "interests": [],
            "style": {},
            "context": {},
            "exploration_openness": 0.5,
            "disliked_topics": ["电脑使用技巧"],
            "favorite_up_users": [],
        }

    async def fake_build(
        *,
        history: list[dict[str, object]],
        preference: dict[str, object],
        awareness_notes: list[dict[str, object]],
        active_insights: list[dict[str, object]],
    ) -> SoulProfile:
        del history, awareness_notes, active_insights
        profile_build_started.set()
        await release_profile_build.wait()
        return SoulProfile.from_dict(
            {
                "personality_portrait": "你明确希望避开重复的电脑技巧内容。" * 12,
                "core_traits": ["直接"],
                "cognitive_style": ["明确表达边界"],
                "motivational_drivers": ["减少重复内容"],
                "current_phase": "正在校准推荐边界。",
                "values": ["有效"],
                "life_stage": "持续校准内容口味",
                "deep_needs": ["推荐边界被尊重"],
                "preferences": preference,
            }
        )

    calls: list[dict[str, object]] = []

    async def fake_purge_pool_for_new_dislikes(**kwargs: object) -> list[str]:
        calls.append(dict(kwargs))
        purge_started.set()
        return []

    monkeypatch.setattr(engine._dialogue_insight_analyzer, "extract", fake_extract)
    monkeypatch.setattr(engine._preference_analyzer, "analyze_events", fake_analyze_events)
    monkeypatch.setattr(engine._profile_builder, "build", fake_build)
    monkeypatch.setattr(
        dislike_writeback,
        "purge_pool_for_new_dislikes",
        fake_purge_pool_for_new_dislikes,
    )

    learn_task = asyncio.create_task(
        engine.learn_from_dialogue(
            user_message="不要再给我推荐电脑使用技巧。",
            assistant_reply="明白，我会避开这类内容。",
            session="popup",
        )
    )
    await asyncio.wait_for(profile_build_started.wait(), timeout=1)
    await asyncio.wait_for(purge_started.wait(), timeout=1)
    assert not learn_task.done()
    release_profile_build.set()
    result = await learn_task
    await engine.wait_for_pending_edits()

    assert result["preference_updated"] is True
    assert result["profile_rebuilt"] is True
    assert calls == [
        {
            "newly_added": ["电脑使用技巧"],
            "all_dislikes": ["电脑使用技巧"],
            "database": memory._database,
            "embedding_service": engine._embedding_service,
            "llm_service": engine._llm_service,
        }
    ]


@pytest.mark.asyncio
async def test_learn_from_dialogue_updates_preference_for_repeated_lower_confidence_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    memory.save_insight_candidates(
        [
            {
                "id": "cand-1",
                "kind": "goal",
                "content": "想系统理解城市更新",
                "confidence": 0.64,
                "evidence": "之前聊过城市更新。",
                "occurrences": 1,
                "confirmed": False,
                "applied": False,
                "created_at": "2026-06-16T09:00:00",
                "updated_at": "2026-06-16T09:00:00",
            }
        ]
    )

    async def fake_extract(
        *,
        user_message: str,
        assistant_reply: str,
        core_memory: dict[str, object],
        active_list: dict[str, object] | None = None,
    ) -> list[dict[str, object]]:
        return [
            {
                "kind": "goal",
                "content": "想系统理解城市更新",
                "confidence": 0.67,
                "evidence": user_message,
            }
        ]

    async def fake_analyze_events(
        *,
        events: list[dict[str, object]],
        existing_preference: dict[str, object],
    ) -> dict[str, object]:
        assert events[0]["event_type"] == "dialogue_insight"
        assert events[0]["metadata"]["occurrences"] == 2
        return {
            "interests": [
                {
                    "name": "城市更新",
                    "category": "生活",
                    "weight": 0.5,
                    "source": "dialogue",
                }
            ],
            "style": {},
            "context": {},
            "exploration_openness": 0.5,
            "disliked_topics": [],
            "favorite_up_users": [],
        }

    monkeypatch.setattr(engine._dialogue_insight_analyzer, "extract", fake_extract)
    monkeypatch.setattr(engine._preference_analyzer, "analyze_events", fake_analyze_events)

    result = await engine.learn_from_dialogue(
        user_message="我还是想系统理解城市更新，不只是看单个案例。",
        assistant_reply="这个方向你已经重复提到过了。",
        session="popup",
    )

    assert result["preference_updated"] is True
    candidates = memory.load_insight_candidates()
    assert candidates[0]["applied"] is True
    assert memory.get_layer("preference").data["interests"][0]["name"] == "城市更新"


@pytest.mark.asyncio
async def test_learn_from_dialogue_records_immediate_cognition_for_strong_single_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)

    async def fake_extract(
        *,
        user_message: str,
        assistant_reply: str,
        core_memory: dict[str, object],
        active_list: dict[str, object] | None = None,
    ) -> list[dict[str, object]]:
        return [
            {
                "kind": "goal",
                "content": "想把国际新闻背后的因果链看明白",
                "confidence": 0.91,
                "evidence": user_message,
            }
        ]

    monkeypatch.setattr(engine._dialogue_insight_analyzer, "extract", fake_extract)

    result = await engine.learn_from_dialogue(
        user_message="我最近更想知道国际新闻到底是怎么一步步走成现在这样的。",
        assistant_reply="听起来你不是只看结果，更想看清背后的因果链。",
        session="popup",
    )

    assert result["preference_updated"] is True
    cognition_updates = memory.load_cognition_updates()
    assert len(cognition_updates) == 1
    assert cognition_updates[0]["kind"] == "profile_shift"
    assert "因果链" in str(cognition_updates[0]["summary"])
    assert "更靠前" in str(cognition_updates[0]["impact"])
    assert "聊天里主动提到" in str(cognition_updates[0]["reasoning"])
    assert "我最近更想知道国际新闻到底是怎么一步步走成现在这样的。" in str(
        cognition_updates[0]["evidence"]
    )
    assert (
        cognition_updates[0]["context_line"] == "来自最近这轮聊天：想把国际新闻背后的因果链看明白"
    )
    assert cognition_updates[0]["source_label"] == "聊天"
    assert cognition_updates[0]["expand_hint"] == "expandable"


@pytest.mark.asyncio
async def test_learn_from_dialogue_does_not_duplicate_same_immediate_cognition(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)

    async def fake_extract(
        *,
        user_message: str,
        assistant_reply: str,
        core_memory: dict[str, object],
        active_list: dict[str, object] | None = None,
    ) -> list[dict[str, object]]:
        return [
            {
                "kind": "dislike",
                "content": "太浅的热点复读",
                "confidence": 0.93,
                "evidence": user_message,
            }
        ]

    monkeypatch.setattr(engine._dialogue_insight_analyzer, "extract", fake_extract)

    await engine.learn_from_dialogue(
        user_message="那种太浅的热点复读我现在真有点看不下去。",
        assistant_reply="你现在明显更在意内容有没有真正往下挖。",
        session="popup",
    )
    await engine.learn_from_dialogue(
        user_message="那种太浅的热点复读我现在真有点看不下去。",
        assistant_reply="你现在明显更在意内容有没有真正往下挖。",
        session="popup",
    )

    cognition_updates = memory.load_cognition_updates()
    assert len(cognition_updates) == 1


@pytest.mark.asyncio
async def test_learn_from_dialogue_records_immediate_cognition_for_interest_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)

    async def fake_extract(
        *,
        user_message: str,
        assistant_reply: str,
        core_memory: dict[str, object],
        active_list: dict[str, object] | None = None,
    ) -> list[dict[str, object]]:
        return [
            {
                "kind": "interest",
                "content": "网络流行文化和梗的传播",
                "confidence": 0.8,
                "evidence": user_message,
            }
        ]

    monkeypatch.setattr(engine._dialogue_insight_analyzer, "extract", fake_extract)

    result = await engine.learn_from_dialogue(
        user_message="最近我还挺想知道 B 站这些梗都是怎么传起来的。",
        assistant_reply="你像是开始对这些梗背后的传播方式也有兴趣了。",
        session="popup",
    )

    assert result["preference_updated"] is True
    cognition_updates = memory.load_cognition_updates()
    assert len(cognition_updates) == 1
    assert cognition_updates[0]["kind"] == "interest_added"
    assert "网络流行文化和梗的传播" in str(cognition_updates[0]["summary"])
    assert cognition_updates[0]["context_line"] == "来自最近这轮聊天：网络流行文化和梗的传播"
    assert cognition_updates[0]["source_label"] == "聊天"
    assert cognition_updates[0]["expand_hint"] == "expandable"


@pytest.mark.asyncio
async def test_learn_from_dialogue_rebuilds_profile_after_candidate_reaches_threshold(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    memory.save_insight_candidates(
        [
            {
                "id": "cand-1",
                "kind": "goal",
                "content": "想更系统地理解国际局势",
                "confidence": 0.81,
                "evidence": "之前也提过想看更深的国际时事分析。",
                "occurrences": 1,
                "confirmed": False,
                "created_at": "2026-03-10T09:00:00",
                "updated_at": "2026-03-10T09:00:00",
            }
        ]
    )

    async def fake_extract(
        *,
        user_message: str,
        assistant_reply: str,
        core_memory: dict[str, object],
        active_list: dict[str, object] | None = None,
    ) -> list[dict[str, object]]:
        return [
            {
                "kind": "goal",
                "content": "想更系统地理解国际局势",
                "confidence": 0.86,
                "evidence": user_message,
            }
        ]

    async def fake_analyze_events(
        *,
        events: list[dict[str, object]],
        existing_preference: dict[str, object],
    ) -> dict[str, object]:
        assert events[0]["event_type"] == "dialogue_insight"
        return {
            "interests": [
                {"name": "国际时事", "category": "知识", "weight": 0.88, "source": "dialogue"}
            ],
            "style": {},
            "context": {},
            "exploration_openness": 0.5,
            "disliked_topics": [],
            "favorite_up_users": [],
        }

    async def fake_build(
        *,
        history: list[dict[str, object]],
        preference: dict[str, object],
        awareness_notes: list[dict[str, object]],
        active_insights: list[dict[str, object]],
    ) -> object:
        from openbiliclaw.soul.profile import SoulProfile

        return SoulProfile.from_dict(
            {
                "personality_portrait": "这是一个会主动追问世界运行逻辑的人。" * 20,
                "core_traits": ["理性", "主动"],
                "cognitive_style": ["会先看结构", "喜欢顺着因果继续追问"],
                "motivational_drivers": ["理解复杂世界"],
                "current_phase": "最近更像在主动搭建解释复杂事件的判断框架。",
                "values": ["真实"],
                "life_stage": "持续探索",
                "deep_needs": ["理解复杂世界"],
                "preferences": preference,
            }
        )

    monkeypatch.setattr(engine._dialogue_insight_analyzer, "extract", fake_extract)
    monkeypatch.setattr(engine._preference_analyzer, "analyze_events", fake_analyze_events)
    monkeypatch.setattr(engine._profile_builder, "build", fake_build)

    result = await engine.learn_from_dialogue(
        user_message="我还是更想知道国际新闻背后的结构和因果。",
        assistant_reply="你像是在寻找一种能把复杂事件看清楚的框架。",
        session="popup",
    )

    assert result["candidate_count"] == 1
    assert result["preference_updated"] is True
    assert result["profile_rebuilt"] is True
    assert memory.get_layer("preference").data["interests"][0]["name"] == "国际时事"
    assert memory.get_layer("soul").data["core"]["core_traits"] == ["理性", "主动"]
    cognition_updates = memory.load_cognition_updates()
    assert cognition_updates
    kinds = {str(item["kind"]) for item in cognition_updates}
    assert "interest_added" in kinds
    assert any("国际时事" in str(item["summary"]) for item in cognition_updates)
    interest_update = next(
        item for item in cognition_updates if str(item["kind"]) == "interest_added"
    )
    assert interest_update["context_line"] == "基于最近主题：国际时事"
    assert interest_update["source_label"] == "聊天"
    assert interest_update["expand_hint"] == "expandable"


@pytest.mark.asyncio
async def test_process_feedback_batch_rebuilds_profile_when_preference_changes_significantly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    memory.get_layer("preference").data.update(
        {
            "interests": [{"name": "科技", "category": "知识", "weight": 0.9}],
            "style": {},
            "context": {},
            "exploration_openness": 0.5,
            "disliked_topics": [],
            "favorite_up_users": [],
        }
    )
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    for index in range(3):
        await memory.propagate_event(
            {
                "event_type": "feedback",
                "title": f"反馈 {index}",
                "metadata": {"feedback_type": "dislike", "bvid": f"BV{index}"},
            }
        )

    async def fake_analyze_events(
        *,
        events: list[dict[str, object]],
        existing_preference: dict[str, object],
        event_chunk_size: int = 0,
    ) -> dict[str, object]:
        assert event_chunk_size == DEFAULT_PREFERENCE_EVENT_CHUNK_SIZE
        return {
            "interests": [
                {"name": "纪录片", "category": "知识", "weight": 0.95, "source": "feedback"},
                {"name": "建筑", "category": "人文", "weight": 0.74, "source": "feedback"},
            ],
            "style": {},
            "context": {},
            "exploration_openness": 0.7,
            "disliked_topics": ["标题党"],
            "favorite_up_users": [],
        }

    async def fake_build(
        *,
        history: list[dict[str, object]],
        preference: dict[str, object],
        awareness_notes: list[dict[str, object]],
        active_insights: list[dict[str, object]],
    ) -> object:
        from openbiliclaw.soul.profile import SoulProfile

        assert history == []
        assert preference["interests"][0]["name"] == "纪录片"
        return SoulProfile(
            personality_portrait="这个人最近明显从科技内容转向更具体的人文叙事与纪录片表达。" * 8,
            core_traits=["理性", "耐心", "好奇"],
            cognitive_style=["偏好从具体材料里建立判断", "会先看脉络再下结论"],
            motivational_drivers=["看见更深的脉络", "确认新的关注方向"],
            current_phase="最近更像从科技效率感转向更具体的人文叙事和结构观察。",
            values=["真实", "成长"],
            life_stage="处于结构调整阶段",
            deep_needs=["被理解", "看见更深的脉络"],
        )

    monkeypatch.setattr(engine._preference_analyzer, "analyze_events", fake_analyze_events)
    monkeypatch.setattr(engine._profile_builder, "build", fake_build)

    result = await engine.process_feedback_batch_if_needed()

    assert result["profile_rebuilt"] is True
    soul = memory.get_layer("soul").data
    assert soul["core"]["core_traits"] == ["理性", "耐心", "好奇"]
    assert "结构调整阶段" in soul["role"]["life_stage"]
    cognition_updates = memory.load_cognition_updates()
    kinds = {str(item["kind"]) for item in cognition_updates}
    assert "dislike_added" in kinds
    assert "profile_shift" in kinds
    dislike_update = next(
        item for item in cognition_updates if str(item["kind"]) == "dislike_added"
    )
    assert dislike_update["context_line"] == "基于最近主题：标题党"
    assert dislike_update["source_label"] == "推荐反馈"
    assert dislike_update["expand_hint"] == "expandable"
    profile_shift = next(item for item in cognition_updates if str(item["kind"]) == "profile_shift")
    assert "画像里" in str(profile_shift["impact"])
    assert "重复出现" in str(profile_shift["reasoning"])
    assert "纪录片" in str(profile_shift["evidence"])
    assert profile_shift["context_line"] == "基于最近主题：纪录片 / 建筑 / 标题党"
    assert profile_shift["source_label"] == "聚合观察"
    assert profile_shift["expand_hint"] == "expandable"


@pytest.mark.asyncio
async def test_process_feedback_batch_rebuild_blocked_by_enforce_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """P2: a feedback batch with a significant shift is now gated (access point ③).

    An enforce reject abandons the rebuild — the soul profile is NOT rewritten,
    even though preference changed significantly.
    """
    from openbiliclaw.soul.posture_gate import DOWNGRADE, GateDecision

    memory = MemoryManager(tmp_path)
    memory.initialize()
    memory.get_layer("preference").data.update(
        {
            "interests": [{"name": "科技", "category": "知识", "weight": 0.9}],
            "style": {},
            "context": {},
            "exploration_openness": 0.5,
            "disliked_topics": [],
            "favorite_up_users": [],
        }
    )
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    for index in range(3):
        await memory.propagate_event(
            {
                "event_type": "feedback",
                "title": f"反馈 {index}",
                "metadata": {"feedback_type": "dislike", "bvid": f"BV{index}"},
            }
        )

    async def fake_analyze_events(
        *,
        events: list[dict[str, object]],
        existing_preference: dict[str, object],
        event_chunk_size: int = 0,
    ) -> dict[str, object]:
        return {
            "interests": [
                {"name": "纪录片", "category": "知识", "weight": 0.95, "source": "feedback"},
            ],
            "style": {},
            "context": {},
            "exploration_openness": 0.7,
            "disliked_topics": ["标题党"],
            "favorite_up_users": [],
        }

    build_called = False

    async def fake_build(**kwargs: object) -> object:
        nonlocal build_called
        build_called = True
        raise AssertionError("rebuild must be abandoned by the enforce gate")

    monkeypatch.setattr(engine._preference_analyzer, "analyze_events", fake_analyze_events)
    monkeypatch.setattr(engine._profile_builder, "build", fake_build)

    class _BlockingGate:
        enabled = True

        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def evaluate(self, **kwargs: object) -> GateDecision:
            self.calls.append(kwargs)
            return GateDecision(verdict=DOWNGRADE, enforced=True)

    gate = _BlockingGate()
    engine._posture_gate = gate  # type: ignore[assignment]

    result = await engine.process_feedback_batch_if_needed()

    assert result["triggered"] is True
    assert result["profile_rebuilt"] is False
    assert build_called is False
    assert gate.calls, "feedback-batch rebuild must consult access point ③"
    assert gate.calls[0]["write_point"] == "feedback_soul_rebuild"


def test_build_cognition_updates_falls_back_to_generic_context_when_signals_are_too_thin(
    tmp_path: Path,
) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)

    updates = engine._build_cognition_updates(
        existing_preference={},
        updated_preference={},
        previous_profile={},
        current_profile={"personality_portrait": "我对你又对上了一点。"},
        source="profile_refresh",
    )

    assert len(updates) == 1
    assert updates[0]["kind"] == "profile_shift"
    assert updates[0]["context_line"] == "基于最近几条相关内容"
    assert updates[0]["source_label"] == "聚合观察"
    assert updates[0]["expand_hint"] == "expandable"


@pytest.mark.asyncio
async def test_soul_engine_passes_satisfaction_flag_to_preference_analyzer(
    tmp_path: Path,
) -> None:
    """SoulEngine kwarg threads through to the internal PreferenceAnalyzer."""
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine_default = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    assert engine_default._preference_analyzer.satisfaction_filter_enabled is True

    engine_off = SoulEngine(
        llm=FakeRegistry("{}"),
        memory=memory,
        satisfaction_filter_enabled=False,
    )
    assert engine_off._preference_analyzer.satisfaction_filter_enabled is False


def test_soul_engine_threads_task_scoped_prompt_views_to_each_analyzer(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()

    default_engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    assert default_engine._preference_prompt_view == "legacy"
    assert default_engine._awareness_prompt_view == "compact-v1"
    assert default_engine._insight_prompt_view == "legacy"
    assert default_engine._awareness_analyzer.plain_prompt_view == "legacy"
    assert default_engine._awareness_analyzer.confusions_prompt_view == "compact-v1"
    assert default_engine._insight_analyzer.cognition_prompt_view == "legacy"
    assert default_engine._preference_analyzer.cognition_prompt_view == "legacy"

    split_engine = SoulEngine(
        llm=FakeRegistry("{}"),
        memory=memory,
        preference_prompt_view="compact-v1",
        awareness_prompt_view="legacy",
        insight_prompt_view="compact-v1",
    )
    assert split_engine._preference_prompt_view == "compact-v1"
    assert split_engine._awareness_prompt_view == "legacy"
    assert split_engine._insight_prompt_view == "compact-v1"
    assert split_engine._awareness_analyzer.plain_prompt_view == "legacy"
    assert split_engine._awareness_analyzer.confusions_prompt_view == "legacy"
    assert split_engine._insight_analyzer.cognition_prompt_view == "compact-v1"
    assert split_engine._preference_analyzer.cognition_prompt_view == "compact-v1"

    for field_name in (
        "preference_prompt_view",
        "awareness_prompt_view",
        "insight_prompt_view",
    ):
        with pytest.raises(ValueError, match="compact-v1"):
            SoulEngine(
                llm=FakeRegistry("{}"),
                memory=memory,
                **{field_name: "future"},
            )


# --- profile overrides overlay (Task 4) -----------------------------------


def _overlay_profile(
    *, core_traits: tuple[str, ...] = (), dislikes: tuple[str, ...] = ()
) -> OnionProfile:
    return OnionProfile(
        core=CoreLayer(core_traits=list(core_traits)),
        interest=InterestLayer(dislikes=[InterestDomain(domain=d, weight=0.9) for d in dislikes]),
    )


def _seed_soul(memory: MemoryManager, profile: OnionProfile) -> None:
    layer = memory.get_layer("soul")
    layer.data.clear()
    layer.data.update(profile.to_dict())
    layer.save()


@pytest.mark.asyncio
async def test_get_profile_applies_overrides_get_raw_does_not(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    _seed_soul(memory, _overlay_profile(core_traits=("完美主义",)))

    new_ov, _ = apply_edit(
        memory.load_profile_overrides(), target="core.core_traits", op="add", value="务实"
    )
    memory.save_profile_overrides(new_ov)

    effective = await engine.get_profile()
    raw = await engine.get_raw_profile()
    assert "务实" in effective.core.core_traits
    assert "务实" not in raw.core.core_traits


@pytest.mark.asyncio
async def test_get_profile_overlays_flat_dislike_before_soul_rebuild(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    _seed_soul(memory, _overlay_profile(dislikes=("营销号",)))
    preference = memory.get_layer("preference")
    preference.data["disliked_topics"] = ["运动康复"]
    preference.save()

    effective = await engine.get_profile()
    raw = await engine.get_raw_profile()

    assert effective.preferences.disliked_topics == ["营销号", "运动康复"]
    assert raw.preferences.disliked_topics == ["营销号"]


@pytest.mark.asyncio
async def test_get_profile_overrides_survive_rebuild(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    _seed_soul(memory, _overlay_profile(core_traits=("完美主义", "好奇")))

    new_ov, _ = apply_edit(
        memory.load_profile_overrides(), target="core.core_traits", op="remove", value="完美主义"
    )
    memory.save_profile_overrides(new_ov)

    # Simulate a full profile rebuild that re-derives the removed trait.
    _seed_soul(memory, _overlay_profile(core_traits=("完美主义", "好奇", "新特质")))

    effective = await engine.get_profile()
    assert "完美主义" not in effective.core.core_traits
    assert "新特质" in effective.core.core_traits


def test_get_overrides_returns_stored(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    new_ov, _ = apply_edit(ProfileOverrides(), target="core.core_traits", op="add", value="务实")
    memory.save_profile_overrides(new_ov)
    assert engine.get_overrides().list_edits["core.core_traits"].add == ["务实"]


def test_effective_disliked_topics_base_then_overlay(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    _seed_soul(memory, _overlay_profile(dislikes=("营销号",)))
    preference = memory.get_layer("preference")
    preference.data["disliked_topics"] = ["标题党"]
    preference.save()

    # Remove the raw-preference dislike via overlay; must not be re-added by raw.
    new_ov, _ = apply_edit(
        memory.load_profile_overrides(), target="dislikes", op="remove", value="标题党"
    )
    memory.save_profile_overrides(new_ov)

    effective = engine.get_effective_disliked_topics()
    assert "标题党" not in effective
    assert "营销号" in effective

    new_ov, _ = apply_edit(
        memory.load_profile_overrides(), target="dislikes", op="add", value="钓鱼贴"
    )
    memory.save_profile_overrides(new_ov)
    assert "钓鱼贴" in engine.get_effective_disliked_topics()


# --- apply_user_edit orchestration (Task 7) -------------------------------


@pytest.mark.asyncio
async def test_apply_user_edit_persists_override_and_records_cognition(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    _seed_soul(memory, _overlay_profile(core_traits=("好奇",)))

    result = await engine.apply_user_edit(target="core.core_traits", op="add", value="务实")

    assert result["ok"] is True
    assert memory.load_profile_overrides().list_edits["core.core_traits"].add == ["务实"]
    cognition = memory.load_cognition_updates()
    assert cognition
    assert cognition[0]["source"] == "manual"
    assert cognition[0]["source_label"] == "手动编辑"
    effective = await engine.get_profile()
    assert "务实" in effective.core.core_traits


@pytest.mark.asyncio
async def test_apply_user_edit_dislike_add_triggers_purge_with_diff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import openbiliclaw.soul.dislike_writeback as dislike_writeback

    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    _seed_soul(memory, _overlay_profile())

    calls: list[list[str]] = []

    async def fake_purge(*, database, embedding_service, llm_service, newly_added, all_dislikes):  # type: ignore[no-untyped-def]
        calls.append(list(newly_added))
        return []

    monkeypatch.setattr(dislike_writeback, "purge_pool_for_new_dislikes", fake_purge)
    await engine.apply_user_edit(target="dislikes", op="add", value="营销号", database=object())
    # Purge runs detached so it never blocks the edit response — drain it.
    await engine.wait_for_pending_edits()
    assert len(calls) == 1
    assert "营销号" in calls[0]


@pytest.mark.asyncio
async def test_apply_user_edit_duplicate_dislike_does_not_purge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import openbiliclaw.soul.dislike_writeback as dislike_writeback

    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    # AI already dislikes 营销号 -> adding it is a no-op for effective dislikes
    _seed_soul(memory, _overlay_profile(dislikes=("营销号",)))

    calls: list[object] = []

    async def fake_purge(**kwargs):  # type: ignore[no-untyped-def]
        calls.append(kwargs)
        return []

    monkeypatch.setattr(dislike_writeback, "purge_pool_for_new_dislikes", fake_purge)
    await engine.apply_user_edit(target="dislikes", op="add", value="营销号", database=object())
    await engine.wait_for_pending_edits()
    assert calls == []


@pytest.mark.asyncio
async def test_apply_user_edit_dislike_add_does_not_block_on_purge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new dislike must return immediately; the LLM+embedding pool purge runs
    detached. Regression: the purge used to be awaited inline, blocking the edit
    response for tens of seconds so the UI looked like the add never saved.
    """
    import openbiliclaw.soul.dislike_writeback as dislike_writeback

    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    _seed_soul(memory, _overlay_profile())

    started = asyncio.Event()
    release = asyncio.Event()
    finished = False

    async def slow_purge(**kwargs: object) -> list[str]:
        nonlocal finished
        started.set()
        await release.wait()
        finished = True
        return []

    monkeypatch.setattr(dislike_writeback, "purge_pool_for_new_dislikes", slow_purge)
    await engine.apply_user_edit(target="dislikes", op="add", value="营销号", database=object())
    # apply_user_edit returned without awaiting the purge: it has started (the
    # task got scheduled) but is parked on `release`, not finished.
    await started.wait()
    assert finished is False
    # Letting it complete still runs the purge to the end.
    release.set()
    await engine.wait_for_pending_edits()
    assert finished is True


@pytest.mark.asyncio
async def test_apply_user_edit_syncs_both_speculators(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    _seed_soul(memory, _overlay_profile())

    seen: dict[str, str] = {}

    class _Spec:
        def user_confirm_speculation(self, domain: str, *, confirmation_source: str = "x") -> bool:
            seen["like"] = domain
            return True

        def user_reject_speculation(self, domain: str, cooldown_days: int = 30) -> bool:
            return True

    class _Avoid:
        def user_confirm_avoidance(self, domain: str) -> None:
            seen["dislike"] = domain

        def user_reject_avoidance(self, domain: str, cooldown_days: int = 30) -> bool:
            return True

    engine._speculator = _Spec()  # type: ignore[assignment]
    engine._avoidance_speculator = _Avoid()  # type: ignore[assignment]

    await engine.apply_user_edit(target="likes", op="add", value="户外")
    await engine.apply_user_edit(target="dislikes", op="add", value="营销号")

    assert seen["like"] == "户外"
    assert seen["dislike"] == "营销号"


@pytest.mark.asyncio
async def test_apply_user_edit_invalid_target_raises(tmp_path: Path) -> None:
    from openbiliclaw.soul.overrides import ProfileEditError

    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    _seed_soul(memory, _overlay_profile())

    with pytest.raises(ProfileEditError):
        await engine.apply_user_edit(target="core.bogus", op="add", value="x")


def test_effective_disliked_topics_honors_specific_removal(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    _seed_soul(
        memory,
        OnionProfile(
            interest=InterestLayer(
                dislikes=[
                    InterestDomain(
                        domain="低质内容", weight=0.9, specifics=[InterestSpecific(name="标题党")]
                    )
                ]
            )
        ),
    )
    assert "标题党" in engine.get_effective_disliked_topics()

    new_ov, _ = apply_edit(
        memory.load_profile_overrides(),
        target="dislikes",
        op="remove",
        value="标题党",
        parent="低质内容",
    )
    memory.save_profile_overrides(new_ov)

    effective = engine.get_effective_disliked_topics()
    assert "标题党" not in effective  # specific removal must reach the hard filter
    assert "低质内容" in effective


# ---------------------------------------------------------------------------
# Phase 1 settles (single ownership, whitelist, ledger turn_id, idempotency)
# ---------------------------------------------------------------------------


def _seed_active_speculation(engine: SoulEngine, tmp_path: Path, domain: str) -> None:
    from openbiliclaw.soul.speculator import SpeculativeInterest, save_speculative_state

    state = engine._speculator._load_state()
    state.active.append(SpeculativeInterest(domain=domain, category="游戏", status="active"))
    save_speculative_state(tmp_path, state)


def _settles_extract(settles: list[dict[str, object]]):  # type: ignore[no-untyped-def]
    async def fake_extract(
        *,
        user_message: str,
        assistant_reply: str,
        core_memory: dict[str, object],
        active_list: dict[str, object] | None = None,
    ) -> dict[str, list[dict[str, object]]]:
        del user_message, assistant_reply, core_memory, active_list
        return {"candidates": [], "settles": settles}

    return fake_extract


def _ledger_rows(memory: MemoryManager, write_point: str) -> list[dict[str, object]]:
    return memory._database.query_profile_ledger(days=30, write_point=write_point)


@asynccontextmanager
async def _test_dialogue_runtime(engine: SoulEngine):  # type: ignore[no-untyped-def]
    """Bind the real single worker to an engine without cutting production entries over."""
    from openbiliclaw.soul.dialogue_learn_queue import (
        AnchorPersisted,
        DialogueJob,
        DialogueJobKind,
        DialogueJobResult,
        DialogueSettlementQueue,
    )

    async def dispatch(job: DialogueJob) -> DialogueJobResult:
        snapshot = job.effective_anchor_snapshot
        assert snapshot is not None
        if job.kind is DialogueJobKind.LEARN:
            payload = dict(job.payload)
            if isinstance(snapshot, AnchorPersisted):
                payload["anchor_ref"] = snapshot.ref
                payload["anchor_generation"] = snapshot.generation
            else:
                payload["anchor_ref"] = ""
                payload["anchor_generation"] = 0
            result = await engine.learn_from_dialogue(**payload)
        elif job.kind is DialogueJobKind.SETTLE_HYPOTHESIS:
            raw_derived = job.payload.get("derived", [])
            derived = (
                [dict(item) for item in raw_derived if isinstance(item, dict)]
                if isinstance(raw_derived, list)
                else []
            )
            result = await engine._apply_hypothesis_settlement(
                ref=str(job.payload["ref"]),
                hypothesis=str(job.payload["hypothesis"]),
                requested_verdict=str(job.payload["requested_verdict"]),
                turn_id=str(job.payload["turn_id"]),
                source=str(job.payload["source"]),
                derived=derived,
                anchor_snapshot=snapshot,
            )
        elif job.kind is DialogueJobKind.SETTLE_CONFUSION:
            result = await engine._apply_confusion_answer_settlement(
                ref=str(job.payload["ref"]),
                confusion_id=int(job.payload["confusion_id"]),
                interpretation=str(job.payload["interpretation"]),
                note=str(job.payload["note"]),
                turn_id=str(job.payload["turn_id"]),
                source=str(job.payload["source"]),
                anchor_snapshot=snapshot,
            )
        else:  # pragma: no cover - helper is deliberately closed over Wave 2 kinds
            raise AssertionError(f"unexpected test settlement kind: {job.kind}")
        return DialogueJobResult(
            outcome=str(result.get("outcome", "completed")),
            settlement=MappingProxyType(dict(result)),
        )

    queue = DialogueSettlementQueue(
        dispatch,
        anchor_provider=engine._dialogue_anchor_manager.snapshot,
    )
    engine.bind_dialogue_settlement_queue(queue)
    try:
        yield queue
    finally:
        await queue.shutdown(timeout=2)


async def _learn_in_test_dialogue_runtime(
    queue: object,
    **payload: object,
) -> dict[str, object]:
    from openbiliclaw.soul.dialogue_learn_queue import (
        DialogueJobKind,
        DialogueSettlementQueue,
    )

    assert isinstance(queue, DialogueSettlementQueue)
    completion = await queue.submit_and_wait(DialogueJobKind.LEARN, payload)
    assert completion.settlement is not None
    return dict(completion.settlement)


async def _call_in_test_dialogue_worker(engine: SoulEngine, operation):  # type: ignore[no-untyped-def]
    """Execute a pre-cutover internal operation under the actual queue worker permit."""
    from openbiliclaw.soul.dialogue_learn_queue import (
        DialogueJob,
        DialogueJobKind,
        DialogueJobResult,
        DialogueSettlementQueue,
    )

    values: list[object] = []

    async def dispatch(_job: DialogueJob) -> DialogueJobResult:
        values.append(await operation())
        return DialogueJobResult(outcome="completed")

    queue = DialogueSettlementQueue(
        dispatch,
        anchor_provider=engine._dialogue_anchor_manager.snapshot,
    )
    try:
        await queue.submit_and_wait(DialogueJobKind.LEARN, {"test_operation": True})
    finally:
        await queue.shutdown(timeout=2)
    assert len(values) == 1
    return values[0]


@pytest.mark.asyncio
async def test_settles_confirm_speculation_on_chat_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    _seed_active_speculation(engine, tmp_path, "桌游")
    monkeypatch.setattr(
        engine._dialogue_insight_analyzer,
        "extract",
        _settles_extract([{"kind": "speculation", "ref": "桌游", "verdict": "confirm"}]),
    )

    async with _test_dialogue_runtime(engine) as queue:
        await _learn_in_test_dialogue_runtime(
            queue,
            user_message="最近确实在玩桌游",
            assistant_reply="不错",
            session="popup",
            scope="chat",
            turn_id="turn-42",
        )

    active = {s.domain for s in engine._speculator.get_active_speculations()}
    assert "桌游" not in active  # confirmed → no longer active
    rows = _ledger_rows(memory, "settle_speculation")
    assert len(rows) == 1
    assert rows[0]["turn_id"] == "turn-42"
    assert rows[0]["outcome"] == "success"
    receipt = memory._database.get_card_settlement("桌游")
    assert receipt is not None
    assert (receipt["verdict"], receipt["applied"]) == ("confirmed", 1)


@pytest.mark.asyncio
async def test_settles_skipped_for_non_chat_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    _seed_active_speculation(engine, tmp_path, "桌游")
    monkeypatch.setattr(
        engine._dialogue_insight_analyzer,
        "extract",
        _settles_extract([{"kind": "speculation", "ref": "桌游", "verdict": "confirm"}]),
    )

    await engine.learn_from_dialogue(
        user_message="关于桌游的探针回复",
        assistant_reply="ok",
        session="popup",
        scope="probe",
        turn_id="turn-probe",
    )

    # Non-chat scope → settles skipped (durable side-effect path owns it).
    active = {s.domain for s in engine._speculator.get_active_speculations()}
    assert "桌游" in active
    assert _ledger_rows(memory, "settle_speculation") == []


@pytest.mark.asyncio
async def test_settles_unknown_ref_dropped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    _seed_active_speculation(engine, tmp_path, "桌游")
    monkeypatch.setattr(
        engine._dialogue_insight_analyzer,
        "extract",
        _settles_extract([{"kind": "speculation", "ref": "不存在的域", "verdict": "confirm"}]),
    )

    await engine.learn_from_dialogue(
        user_message="随便说说",
        assistant_reply="ok",
        session="popup",
        scope="chat",
        turn_id="turn-x",
    )
    active = {s.domain for s in engine._speculator.get_active_speculations()}
    assert "桌游" in active  # untouched — ref not in injected list
    assert _ledger_rows(memory, "settle_speculation") == []


@pytest.mark.asyncio
async def test_settles_confirm_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    _seed_active_speculation(engine, tmp_path, "桌游")
    monkeypatch.setattr(
        engine._dialogue_insight_analyzer,
        "extract",
        _settles_extract([{"kind": "speculation", "ref": "桌游", "verdict": "confirm"}]),
    )
    kwargs = dict(
        user_message="确实在玩", assistant_reply="ok", session="popup", scope="chat", turn_id="t"
    )
    async with _test_dialogue_runtime(engine) as queue:
        await _learn_in_test_dialogue_runtime(queue, **kwargs)
        # Re-run same turn: state must not degrade (still confirmed, not resurrected).
        await _learn_in_test_dialogue_runtime(queue, **kwargs)
    active = {s.domain for s in engine._speculator.get_active_speculations()}
    assert "桌游" not in active


@pytest.mark.asyncio
async def test_plain_chat_confusion_settle_uses_ref_arbitrator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    confusion_id = memory._database.insert_confusion(topic="桌游", observation="连续浏览")
    ref = str(confusion_id)
    monkeypatch.setattr(
        engine._dialogue_insight_analyzer,
        "extract",
        _settles_extract([{"kind": "confusion", "ref": ref, "verdict": "confirm"}]),
    )

    async with _test_dialogue_runtime(engine) as queue:
        await _learn_in_test_dialogue_runtime(
            queue,
            user_message="这确实是兴趣",
            assistant_reply="收到",
            session="popup",
            scope="chat",
            turn_id="confusion-chat-settle",
        )

    confusion = memory._database.get_confusion(confusion_id)
    receipt = memory._database.get_card_settlement(ref)
    assert confusion is not None
    assert (confusion["status"], confusion["resolution"]) == ("resolved", "real_interest")
    assert receipt is not None
    assert (receipt["verdict"], receipt["applied"]) == ("answer:real_interest", 1)
    rows = _ledger_rows(memory, "settle_confusion")
    assert len(rows) == 1
    assert rows[0]["turn_id"] == "confusion-chat-settle"


@pytest.mark.asyncio
async def test_worker_nested_plain_chat_settle_does_not_reenter_queue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.soul.dialogue_learn_queue import DialogueJobKind
    from openbiliclaw.soul.identity import insight_hash8

    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    hypothesis = "用户重视可证伪的证据"
    _seed_insight(memory, hypothesis, validated=False, confidence=0.6)
    ref = insight_hash8(hypothesis)
    chat_entered_analyzer = asyncio.Event()
    release_chat_settle = asyncio.Event()

    async def interleaved_chat_extract(**_kwargs: object) -> dict[str, object]:
        chat_entered_analyzer.set()
        await release_chat_settle.wait()
        return {
            "candidates": [],
            "settles": [{"kind": "insight", "ref": ref, "verdict": "confirm"}],
        }

    outcomes: dict[str, str] = {}
    real_apply = engine._apply_hypothesis_settlement

    async def capture_settlement(**kwargs: object) -> dict[str, object]:
        result = await real_apply(**kwargs)  # type: ignore[arg-type]
        outcomes[str(kwargs.get("source", ""))] = str(result.get("outcome", ""))
        return result

    monkeypatch.setattr(engine._dialogue_insight_analyzer, "extract", interleaved_chat_extract)
    monkeypatch.setattr(engine, "_apply_hypothesis_settlement", capture_settlement)
    async with _test_dialogue_runtime(engine) as queue:
        chat_job = queue.submit(
            DialogueJobKind.LEARN,
            {
                "user_message": "其实我支持这个判断",
                "assistant_reply": "收到",
                "session": "popup",
                "scope": "chat",
                "turn_id": "plain-chat-confirm",
            },
            completion=True,
        )
        assert chat_job is not None and chat_job.completion is not None
        await asyncio.wait_for(chat_entered_analyzer.wait(), timeout=5)
        card_task = asyncio.create_task(
            engine.submit_hypothesis_settlement(
                ref=ref,
                hypothesis=hypothesis,
                requested_verdict="reject",
                turn_id="card-reject",
                source="card_action",
            )
        )
        await asyncio.sleep(0)
        assert queue.depth == 1
        assert memory._database.get_card_settlement(ref) is None
        release_chat_settle.set()
        await asyncio.wait_for(chat_job.completion, timeout=5)
        card_result = await asyncio.wait_for(card_task, timeout=5)

    stored = engine._load_insights()[0]
    receipt = memory._database.get_card_settlement(ref)
    assert card_result["outcome"] == "already_settled"
    assert outcomes == {"chat": "applied", "card_action": "already_settled"}
    assert stored.validated is True
    assert stored.confidence == 0.75
    assert receipt is not None
    assert (receipt["verdict"], receipt["turn_id"], receipt["applied"]) == (
        "confirmed",
        "plain-chat-confirm",
        1,
    )
    rows = _ledger_rows(memory, "settle_insight")
    assert len(rows) == 1
    assert rows[0]["turn_id"] == "plain-chat-confirm"


# ---------------------------------------------------------------------------
# Dialogue confirmation anchor: relation matrix + overlap defence
# ---------------------------------------------------------------------------


def _seed_dialogue_anchor_hypothesis(
    memory: MemoryManager,
    engine: SoulEngine,
    *,
    hypothesis: str,
    origin_turn_id: str = "anchor-card",
):  # type: ignore[no-untyped-def]
    from openbiliclaw.soul.dialogue_anchor import ENTRY_CARD_DISCUSS
    from openbiliclaw.soul.identity import insight_hash8

    _seed_insight(memory, hypothesis, validated=False, confidence=0.6)
    ref = insight_hash8(hypothesis)
    memory._database.create_chat_turn(
        turn_id=origin_turn_id,
        message="聊聊这个",
        scope="hypothesis",
        subject_id=ref,
        subject_title=hypothesis,
        payload={
            "type": "card",
            "kind": "hypothesis",
            "ref": ref,
            "title": hypothesis,
            "state": "discussing",
        },
    )
    return engine._dialogue_anchor_manager.establish(
        kind="hypothesis",
        ref=ref,
        origin_turn_id=origin_turn_id,
        entry=ENTRY_CARD_DISCUSS,
    )


def _anchor_extract(
    relation: str | None,
    *,
    interpretation: str = "",
    derived: list[dict[str, object]] | None = None,
    candidates: list[dict[str, object]] | None = None,
    settles: list[dict[str, object]] | None = None,
):  # type: ignore[no-untyped-def]
    async def fake_extract(
        *,
        user_message: str,
        assistant_reply: str,
        core_memory: dict[str, object],
        active_list: dict[str, object] | None = None,
        anchor: dict[str, object],
    ) -> dict[str, object]:
        del user_message, assistant_reply, core_memory, active_list
        assert anchor["kind"] in {"hypothesis", "confusion"}
        decision = None
        if relation is not None:
            decision = {
                "relation": relation,
                "interpretation": interpretation,
                "derived": list(derived or []),
            }
        return {
            "candidates": list(candidates or []),
            "settles": list(settles or []),
            "anchor": decision,
        }

    return fake_extract


def _two_round_anchor_extract(
    relation: str,
    *,
    interpretation: str = "",
    derived: list[dict[str, object]] | None = None,
):  # type: ignore[no-untyped-def]
    """Return one anchored decision followed by a normal unanchored analysis."""
    calls: list[str] = []
    first_round = _anchor_extract(
        relation,
        interpretation=interpretation,
        derived=derived,
    )

    async def fake_extract(
        *,
        user_message: str,
        assistant_reply: str,
        core_memory: dict[str, object],
        active_list: dict[str, object] | None = None,
        anchor: dict[str, object] | None = None,
    ) -> dict[str, object]:
        calls.append(user_message)
        if len(calls) == 1:
            assert anchor is not None
            return await first_round(
                user_message=user_message,
                assistant_reply=assistant_reply,
                core_memory=core_memory,
                active_list=active_list,
                anchor=anchor,
            )
        assert anchor is None
        return {"candidates": [], "settles": []}

    return fake_extract, calls


async def test_generation_zero_never_captures_future_anchor(tmp_path: Path) -> None:
    """Q3/F2: an admitted absent snapshot never captures a later non-reserved anchor."""
    from openbiliclaw.soul.dialogue_learn_queue import (
        DialogueJob,
        DialogueJobKind,
        DialogueJobResult,
        DialogueSettlementQueue,
    )
    from openbiliclaw.soul.identity import insight_hash8

    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    hypothesis = "用户重视可证伪的判断"
    _seed_insight(memory, hypothesis, validated=False, confidence=0.6)
    ref = insight_hash8(hypothesis)
    blocker_entered = asyncio.Event()
    release_blocker = asyncio.Event()

    async def dispatch(job: DialogueJob) -> DialogueJobResult:
        if job.kind is DialogueJobKind.LEARN:
            blocker_entered.set()
            await release_blocker.wait()
            return DialogueJobResult(outcome="completed")
        assert job.kind is DialogueJobKind.SETTLE_HYPOTHESIS
        assert job.effective_anchor_snapshot is not None
        result = await engine._apply_hypothesis_settlement(
            ref=str(job.payload["ref"]),
            hypothesis=str(job.payload["hypothesis"]),
            requested_verdict=str(job.payload["requested_verdict"]),
            turn_id=str(job.payload["turn_id"]),
            source=str(job.payload["source"]),
            anchor_snapshot=job.effective_anchor_snapshot,
        )
        return DialogueJobResult(outcome=str(result["outcome"]))

    queue = DialogueSettlementQueue(
        dispatch,
        anchor_provider=engine._dialogue_anchor_manager.snapshot,
    )
    queue.start()
    assert queue.submit(DialogueJobKind.LEARN, {"blocker": True})
    await asyncio.wait_for(blocker_entered.wait(), timeout=1)
    settlement = queue.submit(
        DialogueJobKind.SETTLE_HYPOTHESIS,
        {
            "ref": ref,
            "hypothesis": hypothesis,
            "requested_verdict": "confirm",
            "turn_id": "legacy-absent-admission",
            "source": "legacy_compatibility",
            "target_kind": "hypothesis",
            "target_ref": ref,
        },
        completion=True,
    )
    assert settlement is not None
    assert settlement.completion is not None

    # The request is already accepted with an absent tombstone. This establish
    # is deliberately not represented by an admission reservation.
    anchor = _seed_dialogue_anchor_hypothesis(
        memory,
        engine,
        hypothesis=hypothesis,
        origin_turn_id="future-anchor-card",
    )
    release_blocker.set()
    try:
        result = await asyncio.wait_for(settlement.completion, timeout=2)
    finally:
        await queue.shutdown(timeout=1)

    assert result.outcome == "stale_anchor"
    assert memory._database.get_card_settlement(anchor.ref) is None
    assert engine._dialogue_anchor_manager.current() == anchor
    stored = engine._load_insights()[0]
    assert stored.validated is False
    assert stored.confidence == 0.6


async def test_public_submit_uses_worker_only_apply_and_child_cannot_inherit_permit(
    tmp_path: Path,
) -> None:
    """Q4/F4: public admission submits once; only the actual worker may apply."""
    from openbiliclaw.soul.dialogue_learn_queue import (
        AnchorAbsent,
        DialogueJob,
        DialogueJobKind,
        DialogueJobResult,
        DialogueSettlementQueue,
    )
    from openbiliclaw.soul.dialogue_settlement_guard import (
        DialogueSettlementMutationOutsideWorker,
    )
    from openbiliclaw.soul.identity import insight_hash8

    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    hypothesis = "用户重视可复核的资料"
    _seed_insight(memory, hypothesis, validated=False, confidence=0.6)
    ref = insight_hash8(hypothesis)
    child_denials = 0
    dispatch_count = 0

    async def dispatch(job: DialogueJob) -> DialogueJobResult:
        nonlocal child_denials, dispatch_count
        dispatch_count += 1
        assert job.kind is DialogueJobKind.SETTLE_HYPOTHESIS
        assert job.effective_anchor_snapshot is not None

        async def child_apply() -> None:
            await engine._apply_hypothesis_settlement(
                ref=ref,
                hypothesis=hypothesis,
                requested_verdict="confirm",
                turn_id="child-must-fail",
                source="test",
                anchor_snapshot=job.effective_anchor_snapshot,
            )

        child = asyncio.create_task(child_apply())
        with pytest.raises(DialogueSettlementMutationOutsideWorker):
            await child
        child_denials += 1
        result = await engine._apply_hypothesis_settlement(
            ref=ref,
            hypothesis=hypothesis,
            requested_verdict=str(job.payload["requested_verdict"]),
            turn_id=str(job.payload["turn_id"]),
            source=str(job.payload["source"]),
            anchor_snapshot=job.effective_anchor_snapshot,
        )
        return DialogueJobResult(outcome=str(result["outcome"]))

    queue = DialogueSettlementQueue(
        dispatch,
        anchor_provider=engine._dialogue_anchor_manager.snapshot,
    )
    engine.bind_dialogue_settlement_queue(queue)
    with pytest.raises(DialogueSettlementMutationOutsideWorker):
        await engine._apply_hypothesis_settlement(
            ref=ref,
            hypothesis=hypothesis,
            requested_verdict="confirm",
            turn_id="outside-worker",
            source="test",
            anchor_snapshot=AnchorAbsent(
                target_kind="hypothesis",
                target_ref=ref,
                tombstone_epoch=1,
            ),
        )

    try:
        result = await engine.submit_hypothesis_settlement(
            ref=ref,
            hypothesis=hypothesis,
            requested_verdict="confirm",
            turn_id="public-submit",
            source="test",
        )
    finally:
        await queue.shutdown(timeout=1)

    assert result["outcome"] == "applied"
    assert dispatch_count == 1
    assert child_denials == 1
    assert memory._database.get_card_settlement(ref)["applied"] == 1


async def test_card_reconcile_apply_is_worker_only(tmp_path: Path) -> None:
    from openbiliclaw.soul.dialogue_settlement_guard import (
        DialogueSettlementMutationOutsideWorker,
    )

    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)

    with pytest.raises(DialogueSettlementMutationOutsideWorker):
        await engine._apply_card_reconcile(ref="worker-only-reconcile")


async def test_generation_change_after_analysis_before_effect_writes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Q3: replacing a generation in the post-analysis gap fences every effect."""
    from openbiliclaw.soul.dialogue_anchor import ENTRY_PENDING_OPEN
    from openbiliclaw.soul.dialogue_learn_queue import (
        DialogueJob,
        DialogueJobKind,
        DialogueJobResult,
        DialogueSettlementQueue,
    )

    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    hypothesis = "用户重视原始证据"
    old = _seed_dialogue_anchor_hypothesis(memory, engine, hypothesis=hypothesis)
    monkeypatch.setattr(
        engine._dialogue_insight_analyzer,
        "extract",
        _anchor_extract("support"),
    )

    settlement_entered = asyncio.Event()
    release_settlement = asyncio.Event()
    real_apply = engine._apply_hypothesis_settlement

    async def pause_before_first_settlement_effect(
        *,
        ref: str,
        hypothesis: str,
        requested_verdict: str,
        turn_id: str,
        source: str,
        derived: list[dict[str, object]] | None = None,
        anchor_snapshot: object,
    ) -> dict[str, object]:
        settlement_entered.set()
        await release_settlement.wait()
        return await real_apply(
            ref=ref,
            hypothesis=hypothesis,
            requested_verdict=requested_verdict,
            turn_id=turn_id,
            source=source,
            derived=derived,
            anchor_snapshot=anchor_snapshot,  # type: ignore[arg-type]
        )

    monkeypatch.setattr(
        engine,
        "_apply_hypothesis_settlement",
        pause_before_first_settlement_effect,
    )

    async def dispatch(job: DialogueJob) -> DialogueJobResult:
        assert job.kind is DialogueJobKind.LEARN
        await engine.learn_from_dialogue(**dict(job.payload))
        return DialogueJobResult(outcome="completed")

    queue = DialogueSettlementQueue(
        dispatch,
        anchor_provider=engine._dialogue_anchor_manager.snapshot,
    )
    queue.start()
    learning = queue.submit(
        DialogueJobKind.LEARN,
        {
            "user_message": "我支持这个判断",
            "assistant_reply": "收到",
            "session": "popup",
            "turn_id": "generation-effect-gap",
            "anchor_ref": old.ref,
            "anchor_generation": old.generation,
        },
        completion=True,
    )
    assert learning is not None
    assert learning.completion is not None
    try:
        await asyncio.wait_for(settlement_entered.wait(), timeout=1)
        assert (
            engine._dialogue_anchor_manager.release(
                reason="replaced",
                expected_generation=old.generation,
            )
            == old
        )
        replacement = engine._dialogue_anchor_manager.establish(
            kind="hypothesis",
            ref=old.ref,
            origin_turn_id=old.origin_turn_id,
            entry=ENTRY_PENDING_OPEN,
        )
    finally:
        release_settlement.set()
    await asyncio.wait_for(learning.completion, timeout=2)
    await queue.shutdown(timeout=1)

    assert engine._dialogue_anchor_manager.current() == replacement
    assert memory._database.get_card_settlement(old.ref) is None
    assert memory._database.query_events(event_types=["feedback"]) == []
    stored = engine._load_insights()[0]
    assert stored.validated is False
    assert stored.confidence == 0.6
    assert memory._database.get_chat_turn("anchor-card")["payload"]["state"] == "pending"
    assert _ledger_rows(memory, "settle_insight") == []


@pytest.mark.parametrize(
    ("relation", "validated", "confidence", "card_state", "outcome"),
    [
        ("support", True, 0.75, "confirmed", "confirmed"),
        ("contradict", False, 0.35, "rejected", "rejected"),
    ],
)
async def test_hypothesis_anchor_support_and_contradict_settle_via_feedback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relation: str,
    validated: bool,
    confidence: float,
    card_state: str,
    outcome: str,
) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    anchor = _seed_dialogue_anchor_hypothesis(
        memory,
        engine,
        hypothesis="用户重视深度内容",
    )
    extract, analyzer_calls = _two_round_anchor_extract(relation)
    monkeypatch.setattr(engine._dialogue_insight_analyzer, "extract", extract)

    async with _test_dialogue_runtime(engine) as queue:
        result = await _learn_in_test_dialogue_runtime(
            queue,
            user_message="这是我的明确回答",
            assistant_reply="明白了",
            session="popup",
            turn_id="anchor-turn",
        )
        next_result = await _learn_in_test_dialogue_runtime(
            queue,
            user_message="这是结算后的下一轮正常对话",
            assistant_reply="继续聊",
            session="popup",
            turn_id="post-anchor-turn",
        )

    stored = engine._load_insights()[0]
    assert stored.validated is validated
    assert stored.confidence == confidence
    settlement = memory._database.get_card_settlement(anchor.ref)
    assert settlement is not None
    assert settlement["applied"] == 1
    assert settlement["verdict"] == card_state
    assert memory._database.get_chat_turn("anchor-card")["payload"]["state"] == card_state
    assert engine._dialogue_anchor_manager.current() is None
    assert result["anchor_outcome"] == outcome
    assert analyzer_calls == ["这是我的明确回答", "这是结算后的下一轮正常对话"]
    assert "anchor_outcome" not in next_result


async def test_hypothesis_anchor_obeys_existing_object_arbitration_and_projects_all_cards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    hypothesis = "用户重视可证伪的证据"
    anchor = _seed_dialogue_anchor_hypothesis(memory, engine, hypothesis=hypothesis)
    memory._database.create_chat_turn(
        turn_id="anchor-card-webui",
        session="webui",
        scope="hypothesis",
        subject_id=anchor.ref,
        subject_title=hypothesis,
        message="阿b 的猜测",
        payload={
            "type": "card",
            "kind": "hypothesis",
            "ref": anchor.ref,
            "title": hypothesis,
            "state": "pending",
        },
    )
    assert memory._database.try_create_card_settlement(
        ref=anchor.ref,
        verdict="rejected",
        turn_id="card-reject-winner",
        payload={
            "kind": "hypothesis",
            "title": hypothesis,
            "action": "rejected",
            "anchor_generation": anchor.generation,
            "source": "card_action",
        },
    )
    monkeypatch.setattr(engine._dialogue_insight_analyzer, "extract", _anchor_extract("support"))

    async with _test_dialogue_runtime(engine) as queue:
        result = await _learn_in_test_dialogue_runtime(
            queue,
            user_message="其实我支持这个判断",
            assistant_reply="收到",
            session="popup",
            turn_id="anchor-support-loser",
        )

    stored = engine._load_insights()[0]
    receipt = memory._database.get_card_settlement(anchor.ref)
    assert receipt is not None
    assert (receipt["verdict"], receipt["turn_id"], receipt["applied"]) == (
        "rejected",
        "card-reject-winner",
        1,
    )
    assert stored.validated is False
    assert stored.confidence == 0.35
    assert result["anchor_outcome"] == "rejected"
    assert memory._database.get_chat_turn("anchor-card")["payload"]["state"] == "rejected"
    assert memory._database.get_chat_turn("anchor-card-webui")["payload"]["state"] == "rejected"


async def test_hypothesis_anchor_revise_rejects_original_and_persists_confirmed_derived(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    anchor = _seed_dialogue_anchor_hypothesis(
        memory,
        engine,
        hypothesis="用户只在意理论深度",
    )
    extract, analyzer_calls = _two_round_anchor_extract(
        "revise",
        derived=[
            {
                "content": "用户更看重能落地的深度",
                "confidence": 0.82,
                "evidence": "用户主动修正",
            }
        ],
    )
    monkeypatch.setattr(engine._dialogue_insight_analyzer, "extract", extract)

    async with _test_dialogue_runtime(engine) as queue:
        result = await _learn_in_test_dialogue_runtime(
            queue,
            user_message="不是只要理论，我更看重能落地",
            assistant_reply="这个修正很关键",
            session="popup",
        )
        next_result = await _learn_in_test_dialogue_runtime(
            queue,
            user_message="修正后的下一轮正常对话",
            assistant_reply="继续",
            session="popup",
        )

    by_text = {item.hypothesis: item for item in engine._load_insights()}
    assert by_text["用户只在意理论深度"].validated is False
    assert by_text["用户只在意理论深度"].confidence == 0.35
    assert by_text["用户更看重能落地的深度"].validated is True
    assert by_text["用户更看重能落地的深度"].confidence == 0.82
    settlement = memory._database.get_card_settlement(anchor.ref)
    assert settlement is not None
    assert (settlement["verdict"], settlement["applied"]) == ("revised", 1)
    assert result["anchor_outcome"] == "revised"
    # The card reads "已按你的修正记下", not "已标记不准": the user replaced the
    # wording and accepted the correction, and the derived hypothesis above was
    # persisted as validated. Projecting a revise as a plain rejection told the
    # user the opposite of what they had just agreed to.
    assert memory._database.get_chat_turn("anchor-card")["payload"]["state"] == "revised"
    assert analyzer_calls == ["不是只要理论，我更看重能落地", "修正后的下一轮正常对话"]
    assert "anchor_outcome" not in next_result


async def test_confusion_anchor_answer_resolves_matching_interpretation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.soul.dialogue_anchor import ENTRY_CONFUSION_PROMPT

    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    confusion_id = memory._database.insert_confusion(topic="桌游", observation="连续浏览")
    assert memory._database.claim_confusion_clarifying(
        confusion_id,
        ask_turn_id="question",
        asked_at="2026-07-22T01:00:00+00:00",
    )
    anchor = engine._dialogue_anchor_manager.establish(
        kind="confusion",
        ref=str(confusion_id),
        origin_turn_id="question",
        entry=ENTRY_CONFUSION_PROMPT,
    )
    extract, analyzer_calls = _two_round_anchor_extract(
        "answer",
        interpretation="real_interest",
    )
    monkeypatch.setattr(engine._dialogue_insight_analyzer, "extract", extract)

    async with _test_dialogue_runtime(engine) as queue:
        result = await _learn_in_test_dialogue_runtime(
            queue,
            user_message="是真的喜欢",
            assistant_reply="明白",
            session="popup",
        )
        next_result = await _learn_in_test_dialogue_runtime(
            queue,
            user_message="疑惑结算后的下一轮正常对话",
            assistant_reply="继续",
            session="popup",
        )

    assert memory._database.get_confusion(confusion_id)["status"] == "resolved"
    assert memory._database.get_confusion(confusion_id)["resolution"] == "real_interest"
    settlement = memory._database.get_card_settlement(anchor.ref)
    assert settlement is not None
    assert settlement["applied"] == 1
    assert settlement["verdict"] == "answer:real_interest"
    assert engine._dialogue_anchor_manager.current() is None
    assert result["anchor_outcome"] == "answered"
    assert analyzer_calls == ["是真的喜欢", "疑惑结算后的下一轮正常对话"]
    assert "anchor_outcome" not in next_result


async def test_completed_confusion_reply_scan_replays_attribution_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.api.runtime_context import _build_dialogue_settlement_dispatcher
    from openbiliclaw.soul.dialogue_anchor import ENTRY_CONFUSION_PROMPT
    from openbiliclaw.soul.dialogue_learn_queue import (
        DialogueJob,
        DialogueJobKind,
        DialogueSettlementQueue,
    )

    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    confusion_id = memory._database.insert_confusion(topic="桌游", observation="连续浏览")
    assert memory._database.claim_confusion_clarifying(
        confusion_id,
        ask_turn_id="question-receipt",
        asked_at="2026-07-21T01:00:00+00:00",
    )
    engine._dialogue_anchor_manager.establish(
        kind="confusion",
        ref=str(confusion_id),
        origin_turn_id="question-receipt",
        entry=ENTRY_CONFUSION_PROMPT,
    )
    memory._database.create_chat_turn(
        turn_id="completed-reply",
        session="popup",
        scope="confusion",
        subject_id=str(confusion_id),
        subject_title="桌游",
        message="这确实是我的兴趣",
    )
    memory._database.complete_chat_turn("completed-reply", reply="明白了")
    extract, analyzer_calls = _two_round_anchor_extract(
        "answer",
        interpretation="real_interest",
    )
    monkeypatch.setattr(engine._dialogue_insight_analyzer, "extract", extract)

    runtime_dispatch = _build_dialogue_settlement_dispatcher(engine, {})
    replay_builders: list[bool] = []

    async def observe_builder(job: DialogueJob):  # type: ignore[no-untyped-def]
        if job.kind is DialogueJobKind.CONFUSION_ATTRIBUTION_REPLAY:
            replay_builders.append(job.owned_anchor_reservation_id is not None)
        return await runtime_dispatch(job)

    queue = DialogueSettlementQueue(
        observe_builder,
        anchor_provider=engine._dialogue_anchor_manager.snapshot,
    )
    engine.bind_dialogue_settlement_queue(queue)
    try:
        assert await engine.replay_confusion_dialogue_attributions() == 1
        assert memory._database.get_confusion(confusion_id)["status"] == "resolved"
        assert memory._database.get_confusion(confusion_id)["replay_queue"] == []
        assert engine._dialogue_anchor_manager.current() is None
        assert await engine.replay_confusion_dialogue_attributions() == 0
        completion = await queue.submit_and_wait(
            DialogueJobKind.LEARN,
            {
                "user_message": "replay 结算后的下一轮正常对话",
                "assistant_reply": "继续",
                "session": "popup",
                "scope": "chat",
                "turn_id": "post-replay-turn",
            },
        )
        assert completion.outcome == "completed"
    finally:
        await queue.shutdown(timeout=2)
    assert replay_builders == [True]
    assert analyzer_calls == [
        "[关于我有点困惑的「桌游」的澄清] 这确实是我的兴趣",
        "replay 结算后的下一轮正常对话",
    ]


async def test_confusion_attribution_replay_dispatches_dedicated_kind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F1: the cognition hook submits only its dedicated typed command."""
    from openbiliclaw.api.runtime_context import _build_dialogue_settlement_dispatcher
    from openbiliclaw.soul.dialogue_learn_queue import (
        DialogueJob,
        DialogueJobKind,
        DialogueSettlementQueue,
    )

    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    confusion_id = memory._database.insert_confusion(topic="桌游", observation="连续浏览")
    assert memory._database.claim_confusion_clarifying(
        confusion_id,
        ask_turn_id="question-dedicated",
        asked_at="2026-07-21T01:00:00+00:00",
    )
    memory._database.create_chat_turn(
        turn_id="completed-dedicated",
        session="popup",
        scope="confusion",
        subject_id=str(confusion_id),
        subject_title="桌游",
        message="这确实是我的兴趣",
    )
    memory._database.complete_chat_turn("completed-dedicated", reply="明白了")
    monkeypatch.setattr(
        engine._dialogue_insight_analyzer,
        "extract",
        _anchor_extract("answer", interpretation="real_interest"),
    )
    observed: list[tuple[DialogueJobKind, bool]] = []
    queue: DialogueSettlementQueue
    runtime_dispatch = _build_dialogue_settlement_dispatcher(engine, {})

    async def observe(job: DialogueJob):  # type: ignore[no-untyped-def]
        observed.append((job.kind, asyncio.current_task() is queue.worker_task))
        return await runtime_dispatch(job)

    queue = DialogueSettlementQueue(
        observe,
        anchor_provider=engine._dialogue_anchor_manager.snapshot,
    )
    engine.bind_dialogue_settlement_queue(queue)
    try:
        assert await engine.replay_confusion_dialogue_attributions() == 1
    finally:
        await queue.shutdown(timeout=2)

    assert observed == [(DialogueJobKind.CONFUSION_ATTRIBUTION_REPLAY, True)]


async def test_confusion_attribution_replay_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F1/R3: ten same-identity jobs analyze/apply once and resolve every owner."""
    from openbiliclaw.api.runtime_context import _build_dialogue_settlement_dispatcher
    from openbiliclaw.soul.dialogue_learn_queue import (
        DialogueJobKind,
        DialogueSettlementQueue,
    )

    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    confusion_id = memory._database.insert_confusion(topic="桌游", observation="连续浏览")
    assert memory._database.claim_confusion_clarifying(
        confusion_id,
        ask_turn_id="question-idempotent",
        asked_at="2026-07-21T01:00:00+00:00",
    )
    memory._database.create_chat_turn(
        turn_id="completed-idempotent",
        session="popup",
        scope="confusion",
        subject_id=str(confusion_id),
        subject_title="桌游",
        message="这确实是我的兴趣",
    )
    memory._database.complete_chat_turn("completed-idempotent", reply="明白了")
    extract_calls = 0
    fake_extract = _anchor_extract("answer", interpretation="real_interest")

    async def counted_extract(**kwargs: object) -> dict[str, object]:
        nonlocal extract_calls
        extract_calls += 1
        return await fake_extract(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(engine._dialogue_insight_analyzer, "extract", counted_extract)
    queue = DialogueSettlementQueue(
        _build_dialogue_settlement_dispatcher(engine, {}),
        anchor_provider=engine._dialogue_anchor_manager.snapshot,
    )
    engine.bind_dialogue_settlement_queue(queue)
    jobs = [
        queue.submit(
            DialogueJobKind.CONFUSION_ATTRIBUTION_REPLAY,
            {
                "confusion_id": confusion_id,
                "turn_id": "completed-idempotent",
                "replay_id": "completed-idempotent",
                "ask_turn_id": "question-idempotent",
                "subject_id": str(confusion_id),
                "subject_title": "桌游",
                "message": "这确实是我的兴趣",
                "reply": "明白了",
                "has_replay_queue": False,
                "needs_anchor": True,
                "target_kind": "confusion",
                "target_ref": str(confusion_id),
                "producer_source": "cognition_cycle",
            },
            completion=True,
        )
        for _ in range(10)
    ]
    assert all(job is not None and job.completion is not None for job in jobs)
    try:
        results = await asyncio.gather(
            *[
                asyncio.shield(job.completion)
                for job in jobs
                if job is not None and job.completion is not None
            ]
        )
    finally:
        await queue.shutdown(timeout=2)

    assert results[0].outcome == "applied"
    assert [result.outcome for result in results[1:]] == ["already_terminal"] * 9
    assert extract_calls == 1
    assert memory._database.get_confusion(confusion_id)["status"] == "resolved"
    settlement = memory._database.get_card_settlement(str(confusion_id))
    assert settlement is not None and settlement["applied"] == 1


async def test_confusion_anchor_receipts_ambiguous_turn_before_durable_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.soul.dialogue_anchor import ENTRY_CONFUSION_PROMPT

    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    confusion_id = memory._database.insert_confusion(topic="长视频", observation="看不懂")
    assert memory._database.claim_confusion_clarifying(
        confusion_id,
        ask_turn_id="question",
        asked_at="2026-07-21T01:00:00+00:00",
    )
    anchor = engine._dialogue_anchor_manager.establish(
        kind="confusion",
        ref=str(confusion_id),
        origin_turn_id="question",
        entry=ENTRY_CONFUSION_PROMPT,
    )
    memory._database.create_chat_turn(
        turn_id="pending-ambiguous",
        session="popup",
        scope="confusion",
        subject_id=str(confusion_id),
        message="也许吧",
    )
    monkeypatch.setattr(
        engine._dialogue_insight_analyzer,
        "extract",
        _anchor_extract("ambiguous"),
    )

    result = await engine.learn_from_dialogue(
        user_message="也许吧",
        assistant_reply="可以再想想",
        session="popup",
        turn_id="pending-ambiguous",
        anchor_ref=anchor.ref,
        anchor_generation=anchor.generation,
    )

    assert result["anchor_outcome"] == "follow_up"
    pending = memory._database.get_chat_turn("pending-ambiguous")
    assert pending["status"] == "pending"
    assert pending["payload"]["confusion_anchor_processed"] == 1
    memory._database.complete_chat_turn("pending-ambiguous", reply="可以再想想")
    assert engine._confusion_manager.pending_dialogue_replays() == []


async def test_second_ambiguous_confusion_turn_defers_through_anchor_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.soul.dialogue_anchor import ENTRY_CONFUSION_PROMPT

    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    confusion_id = memory._database.insert_confusion(topic="长视频", observation="看不懂")
    assert memory._database.claim_confusion_clarifying(
        confusion_id,
        ask_turn_id="question",
        asked_at="2026-07-21T01:00:00+00:00",
    )
    anchor = engine._dialogue_anchor_manager.establish(
        kind="confusion",
        ref=str(confusion_id),
        origin_turn_id="question",
        entry=ENTRY_CONFUSION_PROMPT,
    )
    monkeypatch.setattr(
        engine._dialogue_insight_analyzer,
        "extract",
        _anchor_extract("ambiguous"),
    )
    kwargs = {
        "user_message": "说不准",
        "assistant_reply": "可以再想想",
        "session": "popup",
        "anchor_ref": anchor.ref,
        "anchor_generation": anchor.generation,
    }

    first = await engine.learn_from_dialogue(turn_id="ambiguous-1", **kwargs)  # type: ignore[arg-type]
    second = await engine.learn_from_dialogue(turn_id="ambiguous-2", **kwargs)  # type: ignore[arg-type]

    stored = memory._database.get_confusion(confusion_id)
    assert first["anchor_outcome"] == "follow_up"
    assert second["anchor_outcome"] == "deferred"
    assert stored["status"] == "open"
    assert stored["defer_count"] == 1
    assert stored["replay_queue"] == []
    assert engine._dialogue_anchor_manager.current() is None


async def test_second_ambiguous_hypothesis_turn_defers_after_one_follow_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    anchor = _seed_dialogue_anchor_hypothesis(memory, engine, hypothesis="用户偏好长视频")
    monkeypatch.setattr(
        engine._dialogue_insight_analyzer,
        "extract",
        _anchor_extract("ambiguous"),
    )
    kwargs = {
        "user_message": "也许吧",
        "assistant_reply": "你可以再想想",
        "session": "popup",
        "anchor_ref": anchor.ref,
        "anchor_generation": anchor.generation,
    }

    first = await engine.learn_from_dialogue(**kwargs)  # type: ignore[arg-type]
    current = engine._dialogue_anchor_manager.current()
    second = await engine.learn_from_dialogue(**kwargs)  # type: ignore[arg-type]

    assert first["anchor_outcome"] == "follow_up"
    assert current is not None and current.ambiguous_count == 1
    assert second["anchor_outcome"] == "deferred"
    assert engine._dialogue_anchor_manager.current() is None
    assert memory._database.get_chat_turn("anchor-card")["payload"]["state"] == "deferred"


async def test_two_unrelated_turns_release_anchor_and_restore_card(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    anchor = _seed_dialogue_anchor_hypothesis(memory, engine, hypothesis="用户偏好长视频")
    monkeypatch.setattr(
        engine._dialogue_insight_analyzer,
        "extract",
        _anchor_extract("unrelated"),
    )
    kwargs = {
        "user_message": "先问个别的",
        "assistant_reply": "好",
        "session": "popup",
        "anchor_ref": anchor.ref,
        "anchor_generation": anchor.generation,
    }

    first = await engine.learn_from_dialogue(**kwargs)  # type: ignore[arg-type]
    second = await engine.learn_from_dialogue(**kwargs)  # type: ignore[arg-type]

    assert first["anchor_outcome"] == "unrelated"
    assert second["anchor_outcome"] == "released_unrelated"
    assert memory._database.get_chat_turn("anchor-card")["payload"]["state"] == "pending"


@pytest.mark.parametrize(
    ("anchor_text", "candidate_text"),
    [
        ("用户喜欢深度技术内容", "喜欢深度技术内容"),
        ("prefers deep technical analysis", "deep technical analysis"),
    ],
)
async def test_anchor_overlap_candidate_is_dropped_with_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    anchor_text: str,
    candidate_text: str,
) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    anchor = _seed_dialogue_anchor_hypothesis(memory, engine, hypothesis=anchor_text)
    monkeypatch.setattr(
        engine._dialogue_insight_analyzer,
        "extract",
        _anchor_extract(
            "ambiguous",
            candidates=[
                {
                    "kind": "interest",
                    "content": candidate_text,
                    "confidence": 0.2,
                    "evidence": "same turn",
                }
            ],
        ),
    )

    with caplog.at_level("WARNING"):
        await engine.learn_from_dialogue(
            user_message="继续聊",
            assistant_reply="好",
            session="popup",
            anchor_ref=anchor.ref,
            anchor_generation=anchor.generation,
        )

    assert memory.load_insight_candidates() == []
    assert "overlaps dialogue anchor" in caplog.text


async def test_stopwords_do_not_false_positive_anchor_overlap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    anchor = _seed_dialogue_anchor_hypothesis(memory, engine, hypothesis="AI is a tool")
    monkeypatch.setattr(
        engine._dialogue_insight_analyzer,
        "extract",
        _anchor_extract(
            "ambiguous",
            candidates=[
                {
                    "kind": "state",
                    "content": "the tool is useful",
                    "confidence": 0.2,
                    "evidence": "旁支",
                }
            ],
        ),
    )

    await engine.learn_from_dialogue(
        user_message="顺便说一句",
        assistant_reply="收到",
        session="popup",
        anchor_ref=anchor.ref,
        anchor_generation=anchor.generation,
    )

    assert [item["content"] for item in memory.load_insight_candidates()] == ["the tool is useful"]


async def test_anchored_turn_skips_retrieval_settles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    anchor = _seed_dialogue_anchor_hypothesis(memory, engine, hypothesis="用户偏好长视频")
    _seed_active_speculation(engine, tmp_path, "桌游")
    monkeypatch.setattr(
        engine._dialogue_insight_analyzer,
        "extract",
        _anchor_extract(
            "ambiguous",
            settles=[{"kind": "speculation", "ref": "桌游", "verdict": "confirm"}],
        ),
    )

    await engine.learn_from_dialogue(
        user_message="桌游不错，但先聊原话题",
        assistant_reply="好",
        session="popup",
        anchor_ref=anchor.ref,
        anchor_generation=anchor.generation,
    )

    assert "桌游" in {item.domain for item in engine._speculator.get_active_speculations()}


async def test_missing_anchor_decision_keeps_anchor_active(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    anchor = _seed_dialogue_anchor_hypothesis(memory, engine, hypothesis="用户偏好长视频")
    monkeypatch.setattr(engine._dialogue_insight_analyzer, "extract", _anchor_extract(None))

    result = await engine.learn_from_dialogue(
        user_message="含糊输入",
        assistant_reply="收到",
        session="popup",
        anchor_ref=anchor.ref,
        anchor_generation=anchor.generation,
    )

    assert engine._dialogue_anchor_manager.current() == anchor
    assert result["anchor_outcome"] == "kept_invalid"


async def test_stale_anchor_snapshot_is_ignored_before_extraction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from openbiliclaw.soul.dialogue_anchor import ENTRY_PENDING_OPEN

    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    old = _seed_dialogue_anchor_hypothesis(memory, engine, hypothesis="旧假设")
    assert (
        engine._dialogue_anchor_manager.release(
            reason="replaced",
            expected_generation=old.generation,
        )
        == old
    )
    current = engine._dialogue_anchor_manager.establish(
        kind="hypothesis",
        ref=old.ref,
        origin_turn_id=old.origin_turn_id,
        entry=ENTRY_PENDING_OPEN,
    )
    assert current.ref == old.ref
    assert current.generation == old.generation + 1

    async def should_not_extract(**_kwargs: object) -> dict[str, object]:
        raise AssertionError("stale anchor must be discarded before LLM extraction")

    monkeypatch.setattr(engine._dialogue_insight_analyzer, "extract", should_not_extract)

    with caplog.at_level("WARNING"):
        result = await engine.learn_from_dialogue(
            user_message="排队中的旧轮",
            assistant_reply="旧回复",
            session="popup",
            anchor_ref=old.ref,
            anchor_generation=old.generation,
        )

    assert engine._dialogue_anchor_manager.current() == current
    assert result["anchor_outcome"] == "stale"
    rows = memory._database.query_profile_ledger(
        days=1,
        write_point="anchor_stale_generation_drop",
    )
    assert len(rows) == 1
    assert rows[0]["source"] == "pre_llm"
    assert "stale dialogue anchor snapshot" in caplog.text


async def test_anchor_generation_is_revalidated_after_llm_before_any_side_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from openbiliclaw.soul.dialogue_anchor import ENTRY_PENDING_OPEN

    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    hypothesis = "用户偏好原始研究"
    old = _seed_dialogue_anchor_hypothesis(memory, engine, hypothesis=hypothesis)
    replacements: list[object] = []

    async def replace_before_return(**_kwargs: object) -> dict[str, object]:
        assert (
            engine._dialogue_anchor_manager.release(
                reason="replaced",
                expected_generation=old.generation,
            )
            is not None
        )
        replacements.append(
            engine._dialogue_anchor_manager.establish(
                kind="hypothesis",
                ref=old.ref,
                origin_turn_id=old.origin_turn_id,
                entry=ENTRY_PENDING_OPEN,
            )
        )
        return {
            "candidates": [
                {
                    "kind": "interest",
                    "content": "不应落库的迟到候选",
                    "confidence": 0.99,
                    "evidence": "stale generation",
                }
            ],
            "settles": [],
            "anchor": {"relation": "support", "interpretation": "", "derived": []},
        }

    monkeypatch.setattr(engine._dialogue_insight_analyzer, "extract", replace_before_return)

    with caplog.at_level("WARNING"):
        result = await engine.learn_from_dialogue(
            user_message="支持旧锚",
            assistant_reply="迟到回复",
            session="popup",
            turn_id="stale-post-llm",
            anchor_ref=old.ref,
            anchor_generation=old.generation,
        )

    current = engine._dialogue_anchor_manager.current()
    stored = engine._load_insights()[0]
    assert replacements
    assert current is not None
    assert current.ref == old.ref
    assert current.generation == old.generation + 1
    assert result["anchor_outcome"] == "stale"
    assert stored.validated is False
    assert stored.confidence == 0.6
    assert memory._database.get_card_settlement(old.ref) is None
    assert memory.load_insight_candidates() == []
    assert memory._database.get_chat_turn("anchor-card")["payload"]["state"] == "pending"
    rows = memory._database.query_profile_ledger(
        days=1,
        write_point="anchor_stale_generation_drop",
    )
    assert len(rows) == 1
    assert rows[0]["source"] == "post_llm"
    assert rows[0]["turn_id"] == "stale-post-llm"
    assert "stale dialogue anchor result discarded before side effects" in caplog.text


async def test_bound_context_stale_event_keeps_one_digest_and_drops_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A frozen bound snapshot records A/stale and cannot write B effects."""
    from openbiliclaw.soul.dialogue_anchor import ENTRY_PENDING_OPEN
    from openbiliclaw.soul.dialogue_turn_context import DialogueTurnBinding, DialogueTurnContext

    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    old = _seed_dialogue_anchor_hypothesis(memory, engine, hypothesis="冻结 A")
    context = DialogueTurnContext(
        reply_to_turn_id="bound-stale-turn",
        source_type="card",
        kind="hypothesis",
        ref=old.ref,
        generation=old.generation,
        anchor_origin_turn_id=old.origin_turn_id,
        title="冻结 A",
        evidence_labels=("可读依据",),
        captured_at="2026-08-01T12:00:00+08:00",
    )
    binding = DialogueTurnBinding.from_context(context)

    async def replace_before_return(**_kwargs: object) -> dict[str, object]:
        assert (
            engine._dialogue_anchor_manager.release(
                reason="replaced",
                expected_generation=old.generation,
            )
            is not None
        )
        engine._dialogue_anchor_manager.establish(
            kind="hypothesis",
            ref=old.ref,
            origin_turn_id="replacement-card",
            entry=ENTRY_PENDING_OPEN,
        )
        return {
            "candidates": [{"kind": "interest", "content": "迟到 A 候选", "confidence": 0.99}],
            "settles": [],
            "anchor": {"relation": "support", "interpretation": "", "derived": []},
        }

    monkeypatch.setattr(engine._dialogue_insight_analyzer, "extract", replace_before_return)
    result = await engine.learn_from_dialogue(
        user_message="我支持 A",
        assistant_reply="收到",
        session="popup",
        turn_id="bound-stale-turn",
        dialogue_binding=binding,
    )

    assert result["anchor_outcome"] == "stale"
    assert result["context_digest"] == context.context_digest
    assert memory.load_insight_candidates() == []
    events = memory.query_events(event_types=["dialogue"])
    assert len(events) == 1
    stale = events[-1]
    metadata = json.loads(str(stale["metadata"]))
    assert metadata["binding_status"] == "stale"
    assert metadata["context_digest"] == context.context_digest
    assert metadata["anchor_ref"] == old.ref
    assert "冻结 A" in str(stale["context"])
    assert old.ref not in str(stale["context"])


def test_anchor_generation_cas_abandons_real_interleaving_before_first_side_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from openbiliclaw.soul.dialogue_anchor import ENTRY_PENDING_OPEN

    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    hypothesis = "用户偏好原始研究"
    old = _seed_dialogue_anchor_hypothesis(memory, engine, hypothesis=hypothesis)
    validation_returned = threading.Event()
    replacement_finished = threading.Event()
    real_validate = engine._dialogue_anchor_manager.validate_snapshot
    validate_calls = 0

    def pause_after_post_llm_validation(ref: str, generation: int):  # type: ignore[no-untyped-def]
        nonlocal validate_calls
        validated = real_validate(ref, generation)
        validate_calls += 1
        if validate_calls == 2:
            validation_returned.set()
            assert replacement_finished.wait(timeout=5)
        return validated

    async def extract_with_settlement(**_kwargs: object) -> dict[str, object]:
        return {
            "candidates": [
                {
                    "kind": "interest",
                    "content": "不应落库的旧代候选",
                    "confidence": 0.99,
                    "evidence": "generation race",
                }
            ],
            "settles": [],
            "anchor": {"relation": "support", "interpretation": "", "derived": []},
        }

    monkeypatch.setattr(
        engine._dialogue_anchor_manager,
        "validate_snapshot",
        pause_after_post_llm_validation,
    )
    monkeypatch.setattr(engine._dialogue_insight_analyzer, "extract", extract_with_settlement)

    def run_old_generation() -> dict[str, object]:
        return asyncio.run(
            engine.learn_from_dialogue(
                user_message="我支持旧代判断",
                assistant_reply="收到",
                session="popup",
                turn_id="generation-race",
                anchor_ref=old.ref,
                anchor_generation=old.generation,
            )
        )

    with ThreadPoolExecutor(max_workers=1) as pool, caplog.at_level("WARNING"):
        future = pool.submit(run_old_generation)
        assert validation_returned.wait(timeout=5)
        try:
            assert (
                engine._dialogue_anchor_manager.release(
                    reason="replaced",
                    expected_generation=old.generation,
                )
                == old
            )
            replacement = engine._dialogue_anchor_manager.establish(
                kind="hypothesis",
                ref=old.ref,
                origin_turn_id=old.origin_turn_id,
                entry=ENTRY_PENDING_OPEN,
            )
        finally:
            replacement_finished.set()
        result = future.result(timeout=5)

    current = engine._dialogue_anchor_manager.current()
    stored = engine._load_insights()[0]
    assert current == replacement
    assert replacement.generation == old.generation + 1
    assert result["anchor_outcome"] == "stale"
    assert stored.validated is False
    assert stored.confidence == 0.6
    assert memory._database.get_card_settlement(old.ref) is None
    assert memory.load_insight_candidates() == []
    assert memory._database.get_chat_turn("anchor-card")["payload"]["state"] == "pending"
    rows = memory._database.query_profile_ledger(
        days=1,
        write_point="anchor_stale_generation_drop",
    )
    assert len(rows) == 1
    assert rows[0]["source"] == "generation_cas"
    assert "dialogue anchor relation fenced" in caplog.text


# ===========================================================================
# Pending confirmed-hypotheses rebuild state machine (deep-line consolidation)
# ===========================================================================


def _seed_insight(
    memory: MemoryManager, hypothesis: str, *, validated: bool, confidence: float
) -> None:
    layer = memory.get_layer("insight")
    existing = layer.data.get("hypotheses", [])
    existing = list(existing) if isinstance(existing, list) else []
    existing.append(
        {
            "hypothesis": hypothesis,
            "evidence": ["e"],
            "confidence": confidence,
            "validated": validated,
            "created_at": "",
        }
    )
    layer.data["hypotheses"] = existing
    layer.save()


def _seed_preference(memory: MemoryManager) -> None:
    memory.get_layer("preference").data.update(
        {
            "interests": [{"name": "科技", "category": "知识", "weight": 0.9}],
            "style": {},
            "context": {},
            "exploration_openness": 0.5,
            "disliked_topics": [],
            "favorite_up_users": [],
        }
    )


class _DecisionGate:
    """Stub posture gate returning a fixed decision (records evaluate calls)."""

    def __init__(self, decision: object, *, enabled: bool = True) -> None:
        self._decision = decision
        self.enabled = enabled
        self.calls: list[dict[str, object]] = []

    async def evaluate(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return self._decision


async def _fake_soul_build(**kwargs: object) -> SoulProfile:
    return SoulProfile(
        personality_portrait="重建后的画像。" * 6,
        core_traits=["理性"],
        cognitive_style=["结构化"],
        motivational_drivers=["求真"],
        current_phase="稳定期",
        values=["真实"],
        life_stage="在职",
        deep_needs=["被理解"],
    )


@pytest.mark.asyncio
async def test_confirm_and_reject_both_mark_rebuild_pending(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    _seed_insight(memory, "用户重视深度内容", validated=False, confidence=0.5)

    await engine.update_from_feedback({"hypothesis": "用户重视深度内容", "signal": "confirm"})
    state = engine._load_rebuild_state()
    assert isinstance(state["pending"], dict)
    assert state["pending"]["retry_count"] == 0
    assert state["pending"]["trigger_refs"]

    # A reject on another hypothesis also marks pending (single-point ownership).
    _seed_insight(memory, "用户讨厌标题党", validated=True, confidence=0.9)
    await engine.update_from_feedback({"hypothesis": "用户讨厌标题党", "signal": "reject"})
    state2 = engine._load_rebuild_state()
    assert isinstance(state2["pending"], dict)
    assert len(state2["pending"]["trigger_refs"]) == 2


@pytest.mark.asyncio
async def test_rebuild_marker_write_failure_blocks_settlement_publication_and_cleans_tmp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pathlib import Path as FilePath

    from openbiliclaw.soul.identity import insight_hash8

    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    hypothesis = "用户重视可靠证据"
    _seed_insight(memory, hypothesis, validated=False, confidence=0.6)
    ref = insight_hash8(hypothesis)
    memory._database.create_chat_turn(
        turn_id="marker-failure-card",
        scope="hypothesis",
        subject_id=ref,
        subject_title=hypothesis,
        message="阿b 的猜测",
        payload={
            "type": "card",
            "kind": "hypothesis",
            "ref": ref,
            "title": hypothesis,
            "state": "pending",
        },
    )
    real_replace = FilePath.replace
    object_mutations = 0
    real_save_insights = engine._save_insights

    def count_object_mutation(hypotheses: list[object]) -> None:
        nonlocal object_mutations
        object_mutations += 1
        real_save_insights(hypotheses)  # type: ignore[arg-type]

    def fail_rebuild_marker_replace(self: FilePath, target: FilePath) -> FilePath:
        if self.name == "rebuild_pending_state.json.tmp":
            raise OSError("injected marker replace failure")
        return real_replace(self, target)

    monkeypatch.setattr(engine, "_save_insights", count_object_mutation)
    monkeypatch.setattr(FilePath, "replace", fail_rebuild_marker_replace)
    async with _test_dialogue_runtime(engine):
        with pytest.raises(OSError, match="injected marker replace failure"):
            await engine.submit_hypothesis_settlement(
                ref=ref,
                hypothesis=hypothesis,
                requested_verdict="confirm",
                turn_id="marker-failure-card",
                source="card_action",
            )

        partial = memory._database.get_card_settlement(ref)
        assert partial is not None
        assert partial["event_id"]
        assert partial["applied"] == 0
        assert partial["verdict"] == "confirmed"
        assert partial["payload"]["action"] == "confirmed"
        assert (
            memory._database.get_chat_turn("marker-failure-card")["payload"]["state"] == "pending"
        )
        marker_path = tmp_path / "memory" / "rebuild_pending_state.json"
        assert not marker_path.exists()
        assert not marker_path.with_name(f"{marker_path.name}.tmp").exists()

        monkeypatch.setattr(FilePath, "replace", real_replace)
        recovered = await engine.submit_hypothesis_settlement(
            ref=ref,
            hypothesis=hypothesis,
            requested_verdict="reject",
            turn_id="losing-contender",
            source="legacy_endpoint",
        )

    assert recovered["state"] == "confirmed"
    final = memory._database.get_card_settlement(ref)
    assert final is not None
    assert (final["verdict"], final["turn_id"], final["applied"]) == (
        "confirmed",
        "marker-failure-card",
        1,
    )
    assert final["payload"]["source"] == "card_action"
    assert object_mutations == 1
    assert len(memory.query_events(event_types=["feedback"])) == 1
    assert len(_ledger_rows(memory, "settle_insight")) == 1
    assert marker_path.exists()
    assert memory._database.get_chat_turn("marker-failure-card")["payload"]["state"] == "confirmed"


async def test_revise_retry_after_derived_checkpoint_keeps_one_object_and_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.soul.identity import insight_hash8

    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    hypothesis = "用户只在意理论深度"
    derived_content = "用户更看重能落地的深度"
    _seed_insight(memory, hypothesis, validated=False, confidence=0.6)
    ref = insight_hash8(hypothesis)
    injected = False

    def fail_once(checkpoint: str, settlement_ref: str) -> None:
        nonlocal injected
        assert settlement_ref == ref
        if checkpoint == "after_derived" and not injected:
            injected = True
            raise RuntimeError("injected after_derived")

    monkeypatch.setattr(engine, "_dialogue_settlement_checkpoint", fail_once)
    async with _test_dialogue_runtime(engine):
        with pytest.raises(RuntimeError, match="injected after_derived"):
            await engine.submit_hypothesis_settlement(
                ref=ref,
                hypothesis=hypothesis,
                requested_verdict="revise",
                turn_id="derived-winner",
                source="card_action",
                derived=[
                    {
                        "content": derived_content,
                        "confidence": 0.82,
                        "evidence": "用户主动修正",
                    }
                ],
            )

        partial = memory._database.get_card_settlement(ref)
        assert partial is not None
        assert partial["applied"] == 0
        recovered = await engine.submit_hypothesis_settlement(
            ref=ref,
            hypothesis=hypothesis,
            requested_verdict="confirm",
            turn_id="losing-contender",
            source="legacy_endpoint",
            derived=[
                {
                    "content": "竞争请求不应落库",
                    "confidence": 0.99,
                    "evidence": "loser",
                }
            ],
        )

    insights = engine._load_insights()
    matching = [item for item in insights if item.hypothesis == derived_content]
    assert len(matching) == 1
    assert matching[0].validated is True
    assert matching[0].confidence == 0.82
    assert all(item.hypothesis != "竞争请求不应落库" for item in insights)
    rows = _ledger_rows(memory, "anchor_revise_derived")
    assert len(rows) == 1
    final = memory._database.get_card_settlement(ref)
    assert final is not None
    assert (final["verdict"], final["turn_id"], final["applied"]) == (
        "revised",
        "derived-winner",
        1,
    )
    assert recovered["outcome"] == "applied"


_DIALOGUE_SETTLEMENT_CRASH_CHECKPOINTS = (
    "after_event",
    "after_object",
    "after_derived",
    "after_rebuild_marker",
    "after_applied_before_projection",
    "after_projection",
    "after_anchor_release",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("checkpoint", _DIALOGUE_SETTLEMENT_CRASH_CHECKPOINTS)
async def test_hypothesis_confirm_crash_gap_retry_has_one_semantic_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    checkpoint: str,
) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    hypothesis = f"用户重视可验证结论-{checkpoint}"
    anchor = _seed_dialogue_anchor_hypothesis(
        memory,
        engine,
        hypothesis=hypothesis,
        origin_turn_id=f"crash-card-{checkpoint}",
    )
    memory._database.create_chat_turn(
        turn_id=f"crash-card-web-{checkpoint}",
        session="webui",
        scope="hypothesis",
        subject_id=anchor.ref,
        subject_title=hypothesis,
        message="另一会话中的同一张卡",
        payload={
            "type": "card",
            "kind": "hypothesis",
            "ref": anchor.ref,
            "title": hypothesis,
            "state": "pending",
        },
    )
    object_mutations = 0
    real_save_insights = engine._save_insights

    def count_object_mutation(hypotheses: list[object]) -> None:
        nonlocal object_mutations
        object_mutations += 1
        real_save_insights(hypotheses)  # type: ignore[arg-type]

    successful_releases = 0
    real_release = engine._dialogue_anchor_manager.release

    def count_release(**kwargs: object) -> object:
        nonlocal successful_releases
        released = real_release(**kwargs)  # type: ignore[arg-type]
        if released is not None:
            successful_releases += 1
        return released

    injected = False

    def fail_once(current: str, settlement_ref: str) -> None:
        nonlocal injected
        assert settlement_ref == anchor.ref
        if current == checkpoint and not injected:
            injected = True
            raise RuntimeError(f"injected {checkpoint}")

    monkeypatch.setattr(engine, "_save_insights", count_object_mutation)
    monkeypatch.setattr(engine._dialogue_anchor_manager, "release", count_release)
    monkeypatch.setattr(engine, "_dialogue_settlement_checkpoint", fail_once)
    async with _test_dialogue_runtime(engine):
        with pytest.raises(RuntimeError, match=f"injected {checkpoint}"):
            await engine.submit_hypothesis_settlement(
                ref=anchor.ref,
                hypothesis=hypothesis,
                requested_verdict="confirm",
                turn_id=f"winner-{checkpoint}",
                source="card_action",
            )

        partial = memory._database.get_card_settlement(anchor.ref)
        assert partial is not None
        assert bool(partial["applied"]) is checkpoint.startswith(
            ("after_applied", "after_projection", "after_anchor")
        )
        if checkpoint == "after_applied_before_projection":
            assert (
                memory._database.get_chat_turn(f"crash-card-web-{checkpoint}")["payload"]["state"]
                == "pending"
            )
        marker_before = engine._load_rebuild_state().get("pending")
        set_at_before = (
            str(marker_before.get("set_at", "")) if isinstance(marker_before, dict) else ""
        )

        recovered = await engine.submit_hypothesis_settlement(
            ref=anchor.ref,
            hypothesis=hypothesis,
            requested_verdict="reject",
            turn_id=f"loser-{checkpoint}",
            source="legacy_endpoint",
        )

    receipt = memory._database.get_card_settlement(anchor.ref)
    assert receipt is not None
    assert (receipt["verdict"], receipt["turn_id"], receipt["applied"]) == (
        "confirmed",
        f"winner-{checkpoint}",
        1,
    )
    assert recovered["settlement_verdict"] == "confirmed"
    assert object_mutations == 1
    assert successful_releases == 1
    assert engine._dialogue_anchor_manager.current() is None
    assert len(memory.query_events(event_types=["feedback"])) == 1
    assert len(_ledger_rows(memory, "settle_insight")) == 1
    for turn_id in (
        f"crash-card-{checkpoint}",
        f"crash-card-web-{checkpoint}",
    ):
        assert memory._database.get_chat_turn(turn_id)["payload"]["state"] == "confirmed"
    marker = engine._load_rebuild_state()["pending"]
    assert len(marker["trigger_refs"]) == 1
    if set_at_before:
        assert marker["set_at"] == set_at_before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "checkpoint",
    ("after_object", "after_applied_before_projection"),
)
async def test_hypothesis_revise_crash_gap_retry_upserts_derived_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    checkpoint: str,
) -> None:
    from openbiliclaw.soul.identity import insight_hash8

    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    hypothesis = f"用户只在意理论-{checkpoint}"
    derived_content = f"用户也在意落地-{checkpoint}"
    _seed_insight(memory, hypothesis, validated=False, confidence=0.6)
    ref = insight_hash8(hypothesis)
    for session in ("popup", "webui"):
        memory._database.create_chat_turn(
            turn_id=f"revise-{session}-{checkpoint}",
            session=session,
            scope="hypothesis",
            subject_id=ref,
            subject_title=hypothesis,
            message="修正这张卡",
            payload={
                "type": "card",
                "kind": "hypothesis",
                "ref": ref,
                "title": hypothesis,
                "state": "pending",
            },
        )
    object_mutations = 0
    real_save_insights = engine._save_insights

    def count_object_mutation(hypotheses: list[object]) -> None:
        nonlocal object_mutations
        object_mutations += 1
        real_save_insights(hypotheses)  # type: ignore[arg-type]

    injected = False

    def fail_once(current: str, settlement_ref: str) -> None:
        nonlocal injected
        assert settlement_ref == ref
        if current == checkpoint and not injected:
            injected = True
            raise RuntimeError(f"injected {checkpoint}")

    monkeypatch.setattr(engine, "_save_insights", count_object_mutation)
    monkeypatch.setattr(engine, "_dialogue_settlement_checkpoint", fail_once)
    async with _test_dialogue_runtime(engine):
        with pytest.raises(RuntimeError, match=f"injected {checkpoint}"):
            await engine.submit_hypothesis_settlement(
                ref=ref,
                hypothesis=hypothesis,
                requested_verdict="revise",
                turn_id=f"revise-winner-{checkpoint}",
                source="card_action",
                derived=[
                    {
                        "content": derived_content,
                        "confidence": 0.84,
                        "evidence": "用户明确修正",
                    }
                ],
            )
        if checkpoint == "after_applied_before_projection":
            assert (
                memory._database.get_chat_turn(f"revise-webui-{checkpoint}")["payload"]["state"]
                == "pending"
            )
        recovered = await engine.submit_hypothesis_settlement(
            ref=ref,
            hypothesis=hypothesis,
            requested_verdict="confirm",
            turn_id=f"revise-loser-{checkpoint}",
            source="legacy_endpoint",
            derived=[
                {
                    "content": "loser-derived",
                    "confidence": 0.99,
                    "evidence": "不得落库",
                }
            ],
        )

    receipt = memory._database.get_card_settlement(ref)
    assert receipt is not None
    assert (receipt["verdict"], receipt["turn_id"], receipt["applied"]) == (
        "revised",
        f"revise-winner-{checkpoint}",
        1,
    )
    assert recovered["settlement_verdict"] == "revised"
    insights = engine._load_insights()
    assert len([item for item in insights if item.hypothesis == derived_content]) == 1
    assert all(item.hypothesis != "loser-derived" for item in insights)
    assert object_mutations == 2
    assert len(memory.query_events(event_types=["feedback"])) == 1
    assert len(_ledger_rows(memory, "settle_insight")) == 1
    assert len(_ledger_rows(memory, "anchor_revise_derived")) == 1
    marker = engine._load_rebuild_state()["pending"]
    assert len(marker["trigger_refs"]) == 2
    for session in ("popup", "webui"):
        # Cross-session projection of a revise is "revised" on every surface —
        # a revise is terminal but is not a rejection.
        assert (
            memory._database.get_chat_turn(f"revise-{session}-{checkpoint}")["payload"]["state"]
            == "revised"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "checkpoint",
    ("after_object", "after_applied_before_projection", "after_anchor_release"),
)
async def test_confusion_answer_crash_gap_retry_resolves_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    checkpoint: str,
) -> None:
    from openbiliclaw.soul.dialogue_anchor import ENTRY_CONFUSION_PROMPT

    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    confusion_id = memory._database.insert_confusion(
        topic=f"桌游-{checkpoint}",
        observation="连续浏览",
    )
    assert memory._database.claim_confusion_clarifying(
        confusion_id,
        ask_turn_id=f"confusion-question-{checkpoint}",
        asked_at="2026-07-22T01:00:00+00:00",
    )
    anchor = engine._dialogue_anchor_manager.establish(
        kind="confusion",
        ref=str(confusion_id),
        origin_turn_id=f"confusion-question-{checkpoint}",
        entry=ENTRY_CONFUSION_PROMPT,
    )
    successful_releases = 0
    real_release = engine._dialogue_anchor_manager.release

    def count_release(**kwargs: object) -> object:
        nonlocal successful_releases
        released = real_release(**kwargs)  # type: ignore[arg-type]
        if released is not None:
            successful_releases += 1
        return released

    injected = False

    def fail_once(current: str, settlement_ref: str) -> None:
        nonlocal injected
        assert settlement_ref == anchor.ref
        if current == checkpoint and not injected:
            injected = True
            raise RuntimeError(f"injected {checkpoint}")

    monkeypatch.setattr(engine._dialogue_anchor_manager, "release", count_release)
    monkeypatch.setattr(engine, "_dialogue_settlement_checkpoint", fail_once)
    async with _test_dialogue_runtime(engine):
        with pytest.raises(RuntimeError, match=f"injected {checkpoint}"):
            await engine.submit_confusion_answer_settlement(
                ref=anchor.ref,
                confusion_id=confusion_id,
                interpretation="real_interest",
                note="winner",
                turn_id=f"confusion-winner-{checkpoint}",
                source="dialogue_anchor",
            )
        recovered = await engine.submit_confusion_answer_settlement(
            ref=anchor.ref,
            confusion_id=confusion_id,
            interpretation="proxy_behavior",
            note="loser",
            turn_id=f"confusion-loser-{checkpoint}",
            source="card_action",
        )

    receipt = memory._database.get_card_settlement(anchor.ref)
    assert receipt is not None
    assert (receipt["verdict"], receipt["turn_id"], receipt["applied"]) == (
        "answer:real_interest",
        f"confusion-winner-{checkpoint}",
        1,
    )
    assert receipt["payload"]["note"] == "winner"
    assert recovered["settlement_verdict"] == "answer:real_interest"
    confusion = memory._database.get_confusion(confusion_id)
    assert confusion is not None
    assert (confusion["status"], confusion["resolution"]) == (
        "resolved",
        "real_interest",
    )
    assert successful_releases == 1
    assert engine._dialogue_anchor_manager.current() is None
    assert len(memory.query_events(event_types=["confusion_settlement"])) == 1
    assert len(_ledger_rows(memory, "confusion_resolve")) == 1
    assert len(_ledger_rows(memory, "settle_confusion")) == 1


@pytest.mark.asyncio
async def test_revise_ledger_failure_receipt_retry_fills_stable_effect_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.soul.identity import insight_hash8

    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    hypothesis = "用户不只在意理论"
    derived_content = "用户更在意可执行结论"
    _seed_insight(memory, hypothesis, validated=False, confidence=0.6)
    ref = insight_hash8(hypothesis)
    original_insert = memory._database.insert_profile_ledger

    def fail_stable_ledger(*, effect_key: str = "", **kwargs: object) -> int:
        if effect_key:
            raise OSError("injected stable ledger failure")
        return original_insert(effect_key=effect_key, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(memory._database, "insert_profile_ledger", fail_stable_ledger)
    async with _test_dialogue_runtime(engine):
        applied = await engine.submit_hypothesis_settlement(
            ref=ref,
            hypothesis=hypothesis,
            requested_verdict="revise",
            turn_id="ledger-winner",
            source="card_action",
            derived=[
                {
                    "content": derived_content,
                    "confidence": 0.83,
                    "evidence": "用户明确修正",
                }
            ],
        )
        assert applied["outcome"] == "applied"
        assert _ledger_rows(memory, "settle_insight") == []
        assert _ledger_rows(memory, "anchor_revise_derived") == []

        monkeypatch.setattr(memory._database, "insert_profile_ledger", original_insert)
        for contender in ("confirm", "reject"):
            replay = await engine.submit_hypothesis_settlement(
                ref=ref,
                hypothesis=hypothesis,
                requested_verdict=contender,
                turn_id=f"ledger-loser-{contender}",
                source="legacy_endpoint",
            )
            assert replay["outcome"] == "already_settled"

    assert len(_ledger_rows(memory, "settle_insight")) == 1
    assert len(_ledger_rows(memory, "anchor_revise_derived")) == 1
    assert (
        len([item for item in engine._load_insights() if item.hypothesis == derived_content]) == 1
    )


@pytest.mark.asyncio
async def test_unapplied_receipt_retry_after_runtime_restart_requires_explicit_submit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.soul.identity import insight_hash8

    first_memory = MemoryManager(tmp_path)
    first_memory.initialize()
    first_engine = SoulEngine(llm=FakeRegistry("{}"), memory=first_memory)
    hypothesis = "用户重视可复核事实"
    _seed_insight(first_memory, hypothesis, validated=False, confidence=0.6)
    ref = insight_hash8(hypothesis)
    injected = False

    def fail_after_event(current: str, settlement_ref: str) -> None:
        nonlocal injected
        assert settlement_ref == ref
        if current == "after_event" and not injected:
            injected = True
            raise RuntimeError("injected runtime exit")

    monkeypatch.setattr(
        first_engine,
        "_dialogue_settlement_checkpoint",
        fail_after_event,
    )
    async with _test_dialogue_runtime(first_engine):
        with pytest.raises(RuntimeError, match="injected runtime exit"):
            await first_engine.submit_hypothesis_settlement(
                ref=ref,
                hypothesis=hypothesis,
                requested_verdict="confirm",
                turn_id="restart-winner",
                source="card_action",
            )

    partial = first_memory._database.get_card_settlement(ref)
    assert partial is not None and partial["applied"] == 0
    assert first_engine._load_insights()[0].validated is False

    restarted_memory = MemoryManager(tmp_path)
    restarted_memory.initialize()
    restarted_engine = SoulEngine(llm=FakeRegistry("{}"), memory=restarted_memory)
    still_partial = restarted_memory._database.get_card_settlement(ref)
    assert still_partial is not None and still_partial["applied"] == 0
    assert restarted_engine._load_insights()[0].validated is False

    async with _test_dialogue_runtime(restarted_engine):
        recovered = await restarted_engine.submit_hypothesis_settlement(
            ref=ref,
            hypothesis=hypothesis,
            requested_verdict="reject",
            turn_id="restart-loser",
            source="legacy_endpoint",
        )

    receipt = restarted_memory._database.get_card_settlement(ref)
    assert receipt is not None
    assert (receipt["verdict"], receipt["turn_id"], receipt["applied"]) == (
        "confirmed",
        "restart-winner",
        1,
    )
    assert recovered["outcome"] == "applied"
    assert restarted_engine._load_insights()[0].validated is True
    assert len(restarted_memory.query_events(event_types=["feedback"])) == 1


@pytest.mark.asyncio
async def test_rebuild_input_filters_unvalidated_and_low_confidence(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    _seed_insight(memory, "已确认高置信", validated=True, confidence=0.9)
    _seed_insight(memory, "已确认但低置信", validated=True, confidence=0.6)
    _seed_insight(memory, "未验证高置信", validated=False, confidence=0.95)

    active = engine._rebuild_active_insights()
    texts = {str(a["hypothesis"]) for a in active}
    assert texts == {"已确认高置信"}


@pytest.mark.asyncio
async def test_pending_rebuild_debounced_within_window(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    await engine._mark_rebuild_pending(["ref:a"])

    result = await engine.run_pending_rebuild_if_due(now=datetime.now())
    assert result["ran"] is False
    assert result["reason"] == "debounced"


@pytest.mark.asyncio
async def test_duplicate_rebuild_trigger_preserves_pending_clock_and_retry(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    await engine._mark_rebuild_pending(["ref:a"])
    state = engine._load_rebuild_state()
    state["pending"]["set_at"] = "2026-01-01T00:00:00"
    state["pending"]["retry_count"] = 1
    engine._save_rebuild_state(state)
    marker_path = tmp_path / "memory" / "rebuild_pending_state.json"
    before = marker_path.read_bytes()

    await engine._mark_rebuild_pending(["ref:a"])

    pending = engine._load_rebuild_state()["pending"]
    assert pending["set_at"] == "2026-01-01T00:00:00"
    assert pending["retry_count"] == 1
    assert marker_path.read_bytes() == before


@pytest.mark.asyncio
async def test_pending_rebuild_accept_runs_and_clears(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from openbiliclaw.soul.posture_gate import ACCEPT, GateDecision

    memory = MemoryManager(tmp_path)
    memory.initialize()
    _seed_preference(memory)
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    _seed_insight(memory, "用户重视深度内容", validated=True, confidence=0.9)
    engine._posture_gate = _DecisionGate(GateDecision(verdict=ACCEPT, enforced=False))  # type: ignore[assignment]
    monkeypatch.setattr(engine._profile_builder, "build", _fake_soul_build)

    await engine._mark_rebuild_pending(["ref:a"])
    result = await engine.run_pending_rebuild_if_due(now=datetime.now() + timedelta(hours=7))

    assert result["ran"] is True
    assert result["outcome"] == "accept"
    # Marker cleared.
    assert engine._load_rebuild_state()["pending"] is None
    # Soul rebuilt.
    assert memory.get_layer("soul").data["core"]["core_traits"] == ["理性"]


@pytest.mark.asyncio
async def test_pending_rebuild_refusal_clears_and_records(
    tmp_path: Path,
) -> None:
    from openbiliclaw.soul.posture_gate import REJECT, GateDecision

    memory = MemoryManager(tmp_path)
    memory.initialize()
    _seed_preference(memory)
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    _seed_insight(memory, "用户重视深度内容", validated=True, confidence=0.9)
    # Real downgrade/reject verdict — NOT an error.
    engine._posture_gate = _DecisionGate(  # type: ignore[assignment]
        GateDecision(verdict=REJECT, enforced=True, is_error=False)
    )

    await engine._mark_rebuild_pending(["ref:a"])
    result = await engine.run_pending_rebuild_if_due(now=datetime.now() + timedelta(hours=7))

    assert result["outcome"] == "refusal"
    state = engine._load_rebuild_state()
    assert state["pending"] is None  # abandoned this batch
    assert "last_gate_refusal" in state


@pytest.mark.asyncio
async def test_pending_rebuild_error_keeps_marker_then_bounded_clear(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    _seed_preference(memory)
    registry = FakeRegistry("this is not valid JSON")
    engine = SoulEngine(
        llm=registry,
        memory=memory,
        posture_gate_mode="enforce",
    )
    _seed_insight(memory, "用户重视深度内容", validated=True, confidence=0.9)
    await engine._mark_rebuild_pending(["ref:a"])

    # The real PostureGate parser sees invalid provider output. Its conservative
    # downgrade must carry is_error=True so the marker is retained for retry.
    with caplog.at_level("WARNING"):
        r1 = await engine.run_pending_rebuild_if_due(now=datetime.now() + timedelta(hours=7))
    assert r1["outcome"] == "error"
    state1 = engine._load_rebuild_state()
    assert isinstance(state1["pending"], dict)
    assert state1["pending"]["retry_count"] == 1
    assert len(registry.calls) == 1
    assert "posture gate returned non-dict JSON" in caplog.text

    # Second run: retry_count reaches the bound → cleared with WARNING.
    r2 = await engine.run_pending_rebuild_if_due(now=datetime.now() + timedelta(hours=7))
    assert r2["outcome"] == "error"
    assert len(registry.calls) == 2
    assert engine._load_rebuild_state()["pending"] is None


@pytest.mark.asyncio
async def test_pending_rebuild_restart_recovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The marker persists; a fresh engine instance picks it up and rebuilds."""
    from openbiliclaw.soul.posture_gate import ACCEPT, GateDecision

    memory = MemoryManager(tmp_path)
    memory.initialize()
    _seed_preference(memory)
    engine1 = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    _seed_insight(memory, "用户重视深度内容", validated=True, confidence=0.9)
    await engine1._mark_rebuild_pending(["ref:a"])
    del engine1

    # Simulate restart: a new engine over the same data dir.
    memory2 = MemoryManager(tmp_path)
    memory2.initialize()
    engine2 = SoulEngine(llm=FakeRegistry("{}"), memory=memory2)
    assert engine2._rebuild_running is False
    assert isinstance(engine2._load_rebuild_state()["pending"], dict)
    engine2._posture_gate = _DecisionGate(GateDecision(verdict=ACCEPT, enforced=False))  # type: ignore[assignment]
    monkeypatch.setattr(engine2._profile_builder, "build", _fake_soul_build)

    result = await engine2.run_pending_rebuild_if_due(now=datetime.now() + timedelta(hours=7))
    assert result["outcome"] == "accept"
    assert engine2._load_rebuild_state()["pending"] is None


@pytest.mark.asyncio
async def test_new_migration_reopens_pending_and_resets_retry(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    await engine._mark_rebuild_pending(["ref:a"])
    # Simulate a prior failed attempt leaving retry_count high.
    state = engine._load_rebuild_state()
    state["pending"]["retry_count"] = 1
    state["pending"]["set_at"] = "2026-01-01T00:00:00"
    engine._save_rebuild_state(state)

    # A new confirm/reject migration re-stamps set_at and resets retry_count.
    _seed_insight(memory, "新证据", validated=True, confidence=0.9)
    await engine.update_from_feedback({"hypothesis": "新证据", "signal": "confirm"})
    reopened = engine._load_rebuild_state()["pending"]
    assert reopened["retry_count"] == 0
    assert reopened["set_at"] != "2026-01-01T00:00:00"
    assert "ref:a" in reopened["trigger_refs"]


class TestInitCognitionDraftsArePersisted:
    """init 的觉察/洞察草稿必须落库，而不是影响完画像就丢掉。

    此前 `_init_cognition_context` 只活在内存里：它塑造了首份画像，然后消失。
    加上 init 拉的历史当时也不入 events 表，认知循环连重新提炼的素材都没有。
    实际后果是全新装机跑完 init 后「待聊确认」是空的——系统刚刚形成了具体
    猜测，却一条都不问。
    """

    def _engine(self, tmp_path: Path):
        from openbiliclaw.memory.manager import MemoryManager
        from openbiliclaw.soul.engine import SoulEngine

        class _Registry:
            async def complete(self, *_a: object, **_k: object) -> object:
                from openbiliclaw.llm.base import LLMResponse

                return LLMResponse(content="[]", provider="fake")

            async def complete_structured_task(self, **_k: object) -> object:
                from openbiliclaw.llm.base import LLMResponse

                return LLMResponse(content="[]", provider="fake")

        memory = MemoryManager(tmp_path)
        memory.initialize()
        return SoulEngine(llm=_Registry(), memory=memory), memory

    def test_drafts_land_in_the_long_term_layers(self, tmp_path: Path) -> None:
        engine, memory = self._engine(tmp_path)
        engine._persist_init_cognition_drafts(
            {
                "awareness": [
                    {
                        "observation": "连续多日完整看完木工系列",
                        "trend": "系统学习",
                        "emotion_guess": "专注",
                    },
                    {
                        "observation": "短暂浏览财经内容即离开",
                        "trend": "浅尝",
                        "emotion_guess": "中性",
                    },
                ],
                "insights": [
                    {
                        "hypothesis": "用户可能正在系统学习木工",
                        "confidence": 0.72,
                        "evidence": ["完播率高", "收藏配套教程"],
                    },
                ],
            }
        )

        notes = memory.get_layer("awareness").data.get("notes", [])
        hyps = memory.get_layer("insight").data.get("hypotheses", [])
        assert len(notes) == 2, "觉察草稿必须落进 awareness 层"
        assert len(hyps) == 1, "洞察草稿必须落进 insight 层"
        assert hyps[0]["hypothesis"] == "用户可能正在系统学习木工"
        assert hyps[0]["confidence"] == 0.72
        assert hyps[0]["validated"] is False, "草稿是待确认的假设，不能自称已验证"
        assert hyps[0].get("user_verdict", "") == "", "用户还没表过态"

    def test_notes_cite_recorded_events_and_admit_approximation(self, tmp_path: Path) -> None:
        """挂的是 init 刚记进账本的真实事件；归属是按轮的，必须标近似。"""
        engine, memory = self._engine(tmp_path)
        for index in range(3):
            memory._database.insert_event("view", title=f"木工{index}", url=f"https://b.tv/{index}")

        engine._persist_init_cognition_drafts(
            {"awareness": [{"observation": "看了木工内容", "trend": "t", "emotion_guess": "e"}]}
        )

        note = memory.get_layer("awareness").data["notes"][0]
        real_ids = {
            int(row["id"]) for row in memory._database.conn.execute("SELECT id FROM events")
        }
        assert note["source_event_ids"], "应当挂上刚记录的事件"
        assert set(note["source_event_ids"]) <= real_ids, "不得引用不存在的事件"
        assert note["source_event_ids_approximate"] is True, (
            "init 的归属是按轮而非按条，必须诚实标注"
        )
        assert note["note_id"], "落库的觉察需要可追溯的 note_id"

    def test_persisting_drafts_is_ledgered(self, tmp_path: Path) -> None:
        engine, memory = self._engine(tmp_path)
        engine._persist_init_cognition_drafts(
            {"insights": [{"hypothesis": "假设一", "confidence": 0.6}]}
        )
        rows = [
            row
            for row in (memory._database.query_profile_ledger(days=1, limit=50) or [])
            if row.get("write_point") == "init_cognition_persist"
        ]
        assert rows, "落库动作必须进台账"

    def test_empty_context_is_a_noop(self, tmp_path: Path) -> None:
        engine, memory = self._engine(tmp_path)
        engine._persist_init_cognition_drafts({})
        assert memory.get_layer("awareness").data.get("notes", []) == []
        assert memory.get_layer("insight").data.get("hypotheses", []) == []

    def test_drafts_merge_rather_than_overwrite(self, tmp_path: Path) -> None:
        """重跑 init 不该冲掉既有认知，也不该重复同一条。"""
        engine, memory = self._engine(tmp_path)
        draft = {"awareness": [{"observation": "同一条观察", "trend": "t", "emotion_guess": "e"}]}
        engine._persist_init_cognition_drafts(draft)
        engine._persist_init_cognition_drafts(dict(draft))

        observations = [n["observation"] for n in memory.get_layer("awareness").data["notes"]]
        assert observations.count("同一条观察") == 1, "重复草稿应被合并去重"


class TestBehaviourEarnedDeepInfluence:
    """深层自主更新：行为佐证足够久的假设，无需用户确认也可参与门控重建。

    用户决策（2026-07-27）：「给模型一些自由度，不能所有的都让用户来确认」。
    约束：门槛全面高于用户确认路径（0.8 vs 0.75 / 7 天 / 3 条证据），
    用户拒绝过的永久排除，且仍要过态势门控。
    """

    @staticmethod
    def _hypothesis(**overrides):
        from datetime import datetime, timedelta

        from openbiliclaw.soul.profile import InsightHypothesis

        defaults = {
            "hypothesis": "用户可能是系统编程从业者",
            "evidence": ["证据一", "证据二", "证据三"],
            "confidence": 0.85,
            "validated": False,
            "created_at": (datetime.now() - timedelta(days=10)).isoformat(),
            "user_verdict": "",
        }
        defaults.update(overrides)
        return InsightHypothesis(**defaults)

    def test_a_seasoned_high_confidence_hypothesis_earns_autonomy(self) -> None:
        from openbiliclaw.soul.engine import SoulEngine

        assert SoulEngine._hypothesis_auto_validated(self._hypothesis())

    def test_timezone_aware_created_at_earns_autonomy_without_datetime_mismatch(self) -> None:
        from datetime import UTC

        from openbiliclaw.soul.engine import SoulEngine

        created_at = (datetime.now(UTC) - timedelta(days=10)).isoformat()

        assert SoulEngine._hypothesis_auto_validated(self._hypothesis(created_at=created_at))

    def test_a_user_rejection_blocks_autonomy_permanently(self) -> None:
        """哪怕置信度 0.99：用户说过「不准」，模型就永远无权自作主张。"""
        from openbiliclaw.soul.engine import SoulEngine

        rejected = self._hypothesis(confidence=0.99, user_verdict="rejected")
        assert not SoulEngine._hypothesis_auto_validated(rejected)

    def test_youth_low_confidence_or_thin_evidence_disqualify(self) -> None:
        from datetime import datetime, timedelta

        from openbiliclaw.soul.engine import SoulEngine

        young = self._hypothesis(created_at=(datetime.now() - timedelta(days=2)).isoformat())
        weak = self._hypothesis(confidence=0.79)
        thin = self._hypothesis(evidence=["只有一条"])
        undated = self._hypothesis(created_at="")

        assert not SoulEngine._hypothesis_auto_validated(young), "一个下午的热情不能改深层"
        assert not SoulEngine._hypothesis_auto_validated(weak)
        assert not SoulEngine._hypothesis_auto_validated(thin)
        assert not SoulEngine._hypothesis_auto_validated(undated), "没有日期等于没有资历"

    def test_near_certainty_waives_the_tenure_wait(self) -> None:
        """快速档（用户决策）：置信 >=0.95 不用等 7 天，其余守卫一样不少。"""
        from datetime import datetime, timedelta

        from openbiliclaw.soul.engine import SoulEngine

        yesterday = (datetime.now() - timedelta(days=1)).isoformat()
        certain = self._hypothesis(confidence=0.96, created_at=yesterday)
        merely_high = self._hypothesis(confidence=0.94, created_at=yesterday)
        certain_but_rejected = self._hypothesis(
            confidence=0.99, created_at=yesterday, user_verdict="rejected"
        )
        certain_but_thin = self._hypothesis(
            confidence=0.96, created_at=yesterday, evidence=["只有一条"]
        )

        assert SoulEngine._hypothesis_auto_validated(certain), "接近确定就不该再等资历"
        assert not SoulEngine._hypothesis_auto_validated(merely_high), "0.94 仍要等满 7 天"
        assert not SoulEngine._hypothesis_auto_validated(certain_but_rejected), "拒绝仍一票否决"
        assert not SoulEngine._hypothesis_auto_validated(certain_but_thin), "证据下限不因置信度豁免"

    def test_confirmed_hypotheses_stay_on_the_validated_path(self) -> None:
        from openbiliclaw.soul.engine import SoulEngine

        confirmed = self._hypothesis(validated=True, user_verdict="confirmed")
        assert not SoulEngine._hypothesis_auto_validated(confirmed), "已确认的走 validated 路径"

    def test_rebuild_inputs_include_behaviour_earned_hypotheses(self, tmp_path: Path) -> None:
        from openbiliclaw.soul.profile import insight_hypothesis_to_dict

        memory = MemoryManager(tmp_path)
        memory.initialize()
        engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
        earned = self._hypothesis()
        rejected = self._hypothesis(
            hypothesis="用户可能不爱看长视频", confidence=0.9, user_verdict="rejected"
        )
        memory.get_layer("insight").data["hypotheses"] = [
            insight_hypothesis_to_dict(earned),
            insight_hypothesis_to_dict(rejected),
        ]

        visible = [item["hypothesis"] for item in engine._rebuild_active_insights()]

        assert "用户可能是系统编程从业者" in visible
        assert "用户可能不爱看长视频" not in visible

    async def test_pending_rebuild_scan_marks_earned_hypotheses(self, tmp_path: Path) -> None:
        """消费检查点先扫自主达标的假设，进同一台去抖/门控状态机。"""
        import json as json_module

        from openbiliclaw.soul.profile import insight_hypothesis_to_dict

        memory = MemoryManager(tmp_path)
        memory.initialize()
        engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
        memory.get_layer("insight").data["hypotheses"] = [
            insight_hypothesis_to_dict(self._hypothesis())
        ]

        result = await engine.run_pending_rebuild_if_due()

        assert result["reason"] == "debounced", "刚标记的 pending 要吃满去抖，不立刻重建"
        state_path = tmp_path / "memory" / "rebuild_pending_state.json"
        state = json_module.loads(state_path.read_text(encoding="utf-8"))
        refs = state["pending"]["trigger_refs"]
        assert refs and all(ref.startswith("auto_hypothesis:") for ref in refs)

    async def test_consumed_auto_hypothesis_is_not_requeued(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """同一条自主假设成功消费后，后续扫描不能每 6 小时重建一次。"""
        from openbiliclaw.soul.profile import insight_hypothesis_to_dict

        memory = MemoryManager(tmp_path)
        memory.initialize()
        engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
        memory.get_layer("insight").data["hypotheses"] = [
            insight_hypothesis_to_dict(self._hypothesis())
        ]

        async def _accept(_trigger_refs: list[str]) -> str:
            return "accept"

        monkeypatch.setattr(engine, "_execute_pending_rebuild", _accept)
        current = datetime.now()
        first = await engine.run_pending_rebuild_if_due(now=current)
        assert first["reason"] == "debounced"

        state = engine._load_rebuild_state()
        state["pending"]["set_at"] = (current - timedelta(hours=7)).isoformat()
        engine._save_rebuild_state(state)
        accepted = await engine.run_pending_rebuild_if_due(now=current)
        assert accepted["outcome"] == "accept"

        repeated = await engine.run_pending_rebuild_if_due(now=current)
        assert repeated == {"ran": False, "reason": "not_pending"}

    async def test_gate_context_includes_behaviour_earned_hypotheses(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """门控必须看见将要影响深层画像的自动假设，而不只是它的哈希 ref。"""
        from openbiliclaw.soul.posture_gate import REJECT, GateDecision
        from openbiliclaw.soul.profile import insight_hypothesis_to_dict

        memory = MemoryManager(tmp_path)
        memory.initialize()
        engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
        memory.get_layer("insight").data["hypotheses"] = [
            insight_hypothesis_to_dict(self._hypothesis())
        ]
        captured: dict[str, object] = {}

        async def _capture_gate(**kwargs: object) -> GateDecision:
            captured.update(kwargs)
            return GateDecision(verdict=REJECT, enforced=True)

        monkeypatch.setattr(engine, "_gate_soul_rebuild", _capture_gate)

        outcome = await engine._execute_pending_rebuild(["auto_hypothesis:test"])

        assert outcome == "refusal"
        context = captured["context"]
        assert isinstance(context, dict)
        assert context["auto_validated_hypotheses"] == ["用户可能是系统编程从业者"]


class TestDialogueDeepFastLane:
    """对话是用户亲口说的：过门的深层自述当轮落 validated 假设 + 当轮重建。

    用户决策（2026-07-27）：对话不论兴趣还是深层都走快速通道——它是用户直接
    反馈，甚至是对画像的直接命令。此前过门的 goal/value/state 候选只喂一次
    偏好 prompt 就消失，且纯深层自述不动兴趣权重时根本触发不了重建。
    """

    @staticmethod
    def _engine(tmp_path, monkeypatch, *, kind: str = "state"):
        memory = MemoryManager(tmp_path)
        memory.initialize()
        engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)

        async def fake_extract(**kwargs):
            return [
                {
                    "kind": kind,
                    "content": "我最近其实处于职业转型期",
                    "confidence": 0.9,
                    "evidence": kwargs.get("user_message", ""),
                }
            ]

        async def fake_analyze_events(*, events, existing_preference, **kwargs):
            # 纯深层自述：偏好一个字都不动 —— 显著变化判定必为 False。
            return dict(existing_preference)

        monkeypatch.setattr(engine._dialogue_insight_analyzer, "extract", fake_extract)
        monkeypatch.setattr(engine._preference_analyzer, "analyze_events", fake_analyze_events)
        return engine, memory

    async def test_accepted_deep_statement_becomes_a_validated_hypothesis(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        engine, memory = self._engine(tmp_path, monkeypatch)

        await engine.learn_from_dialogue(
            user_message="跟你说，我最近其实处于职业转型期。",
            assistant_reply="明白，这解释了你最近看的内容。",
            session="popup",
        )

        hyps = memory.get_layer("insight").data.get("hypotheses", [])
        mine = [h for h in hyps if "职业转型期" in h.get("hypothesis", "")]
        assert mine, "过门的深层自述必须落成假设，不能喂一次 prompt 就消失"
        assert mine[0]["validated"] is True, "用户亲口说的就是确认"
        assert mine[0]["user_verdict"] == "confirmed"

    async def test_pure_deep_statement_triggers_a_same_turn_rebuild(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        engine, memory = self._engine(tmp_path, monkeypatch)
        rebuilt: list[bool] = []

        async def fake_build(**kwargs):
            rebuilt.append(True)
            return SoulProfile(
                personality_portrait="正在转型期的人。" * 20,
                core_traits=["求变"],
                cognitive_style=["先看框架"],
                motivational_drivers=["转型"],
                current_phase="转型期",
                values=["真实"],
                life_stage="转型",
                deep_needs=["方向感"],
            )

        monkeypatch.setattr(engine._profile_builder, "build", fake_build)

        result = await engine.learn_from_dialogue(
            user_message="我最近其实处于职业转型期。",
            assistant_reply="记下了。",
            session="popup",
        )

        assert rebuilt, "纯深层自述不动兴趣权重，也必须当轮重建"
        assert result["profile_rebuilt"] is True

    async def test_interest_only_turns_do_not_take_the_deep_lane(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        engine, memory = self._engine(tmp_path, monkeypatch, kind="interest")

        result = await engine.learn_from_dialogue(
            user_message="我喜欢看木工视频。",
            assistant_reply="记下了。",
            session="popup",
        )

        hyps = memory.get_layer("insight").data.get("hypotheses", [])
        assert not [h for h in hyps if h.get("user_verdict") == "confirmed"], (
            "interest 走快线，不该被落成深层假设"
        )
        assert result["profile_rebuilt"] is False, "偏好没显著变化、又无深层自述，不重建"


# ===========================================================================
# 统一兴趣更新线 Phase 0 — 反馈批线契约特征测试
#
# 这些测试钉死 ``_process_feedback_batch_if_needed_locked`` 今天的语义，作为
# 「反馈批合并进 pipeline 快线」的验收契约。每条标注该语义在统一线上的去向：
#   「统一线必须继承」    — 合并后行为必须逐条保留
#   「统一线有意变更」    — 合并后按 spec 不变量 4 改为折价，需 A/B 门证明
# 参见 docs/plans/2026-07-27-unified-interest-line-spec.md。
# ===========================================================================


class TestFeedbackBatchContract:
    """Characterization contract for the legacy feedback batch (Phase 0)."""

    @staticmethod
    async def _seed_feedback(
        memory: MemoryManager,
        rows: list[tuple[str, str]],
    ) -> None:
        for feedback_type, title in rows:
            await memory.propagate_event(
                {
                    "event_type": "feedback",
                    "title": title,
                    "metadata": {"feedback_type": feedback_type},
                }
            )

    @staticmethod
    def _stub_analyzer(
        monkeypatch: pytest.MonkeyPatch,
        engine: SoulEngine,
        payload: dict[str, object],
        captured: list[dict[str, object]] | None = None,
    ) -> None:
        async def fake_analyze_events(
            *,
            events: list[dict[str, object]],
            existing_preference: dict[str, object],
            event_chunk_size: int = 0,
            awareness_notes: list[dict[str, object]] | None = None,
            active_insights: list[dict[str, object]] | None = None,
        ) -> dict[str, object]:
            del existing_preference, event_chunk_size, awareness_notes, active_insights
            if captured is not None:
                captured.extend(events)
            return dict(payload)

        monkeypatch.setattr(engine._preference_analyzer, "analyze_events", fake_analyze_events)

    @pytest.mark.asyncio
    async def test_threshold_fires_on_third_feedback_not_second(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """契约①「统一线必须继承」: 第 3 条反馈落地才消费，2 条按兵不动。"""
        memory = MemoryManager(tmp_path)
        memory.initialize()
        engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
        self._stub_analyzer(
            monkeypatch,
            engine,
            {
                "interests": [],
                "style": {},
                "context": {},
                "exploration_openness": 0.4,
                "disliked_topics": [],
                "favorite_up_users": [],
            },
        )

        await self._seed_feedback(memory, [("dislike", "第一条"), ("dislike", "第二条")])
        below = await engine.process_feedback_batch_if_needed()
        assert below["triggered"] is False
        assert below["feedback_count"] == 2
        assert below["preference_updated"] is False

        await self._seed_feedback(memory, [("dislike", "第三条")])
        fired = await engine.process_feedback_batch_if_needed()
        assert fired["triggered"] is True
        assert fired["feedback_count"] == 3
        assert fired["preference_updated"] is True

    @pytest.mark.asyncio
    async def test_retraction_excluded_from_count_and_input_but_advances_cursor(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """契约②「统一线有意变更（retraction 排除→折价）」。

        旧批线把 retraction 从阈值计数和 LLM 输入里整条剔除，只推进游标。统一线
        改走 pipeline 既有折价（signal_strength≤0.2 + retracted 标记），必须由
        A/B 门 3 证明折价不产生新增 dislike / 权重上调。
        """
        memory = MemoryManager(tmp_path)
        memory.initialize()
        engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
        captured: list[dict[str, object]] = []
        self._stub_analyzer(
            monkeypatch,
            engine,
            {
                "interests": [],
                "style": {},
                "context": {},
                "exploration_openness": 0.4,
                "disliked_topics": [],
                "favorite_up_users": [],
            },
            captured,
        )

        await self._seed_feedback(
            memory,
            [
                ("dislike", "真反馈 0"),
                ("retraction", "撤回 0"),
                ("dislike", "真反馈 1"),
                ("retraction", "撤回 1"),
                ("dislike", "真反馈 2"),
            ],
        )

        result = await engine.process_feedback_batch_if_needed()

        assert result["triggered"] is True
        # retraction 不计数
        assert result["feedback_count"] == 3
        # retraction 不进 LLM 输入
        assert len(captured) == 3
        assert all("撤回" not in str(event.get("title", "")) for event in captured)
        # 但仍推进游标到扫描过的最大 id（含 retraction 行），避免每轮重扫
        assert memory.load_feedback_state()["last_processed_feedback_event_id"] == 5

    @pytest.mark.asyncio
    async def test_new_dislike_archives_matching_interest(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """契约③「统一线必须继承」: 新增 dislike 把同名兴趣归档（不是删除）。"""
        memory = MemoryManager(tmp_path)
        memory.initialize()
        engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
        self._stub_analyzer(
            monkeypatch,
            engine,
            {
                "interests": [
                    {"name": "标题党", "category": "内容", "weight": 0.7, "source": "feedback"},
                    {"name": "纪录片", "category": "知识", "weight": 0.8, "source": "feedback"},
                ],
                "style": {},
                "context": {},
                "exploration_openness": 0.4,
                "disliked_topics": ["标题党"],
                "favorite_up_users": [],
            },
        )
        await self._seed_feedback(
            memory,
            [("dislike", "反馈 0"), ("dislike", "反馈 1"), ("dislike", "反馈 2")],
        )

        result = await engine.process_feedback_batch_if_needed()
        await engine.wait_for_pending_edits()

        assert result["triggered"] is True
        interests = memory.get_layer("preference").data["interests"]
        by_name = {str(item["name"]): item for item in interests}
        assert by_name["标题党"].get("state") == "archived"
        assert by_name["纪录片"].get("state") != "archived"

    @pytest.mark.asyncio
    async def test_significant_change_rebuild_goes_through_access_point_three(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """契约④「统一线必须继承」: 显著变化的整份重建过接入点③门控。

        shadow/accept 放行 → 重建；enforce downgrade → 放弃重建。
        """
        from openbiliclaw.soul.posture_gate import ACCEPT, DOWNGRADE, GateDecision

        class _StubGate:
            def __init__(self, mode: str, decision: GateDecision) -> None:
                self._mode = mode
                self._decision = decision
                self.calls: list[dict[str, object]] = []

            @property
            def enabled(self) -> bool:
                return self._mode != "off"

            async def evaluate(self, **kwargs: object) -> GateDecision:
                self.calls.append(kwargs)
                return self._decision

        async def fake_build(**kwargs: object) -> SoulProfile:
            del kwargs
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

        significant = {
            "interests": [
                {"name": "城市建筑", "category": "文化", "weight": 0.86, "source": "feedback"},
                {"name": "结构力学", "category": "知识", "weight": 0.81, "source": "feedback"},
            ],
            "style": {},
            "context": {},
            "exploration_openness": 0.5,
            "disliked_topics": [],
            "favorite_up_users": [],
        }

        # shadow / accept → 重建发生，且门控被咨询
        shadow_memory = MemoryManager(tmp_path / "shadow")
        shadow_memory.initialize()
        shadow_engine = SoulEngine(llm=FakeRegistry("{}"), memory=shadow_memory)
        shadow_gate = _StubGate("shadow", GateDecision(verdict=ACCEPT, enforced=False))
        shadow_engine._posture_gate = shadow_gate  # type: ignore[assignment]
        self._stub_analyzer(monkeypatch, shadow_engine, significant)
        monkeypatch.setattr(shadow_engine._profile_builder, "build", fake_build)
        await self._seed_feedback(
            shadow_memory,
            [("like", "反馈 0"), ("like", "反馈 1"), ("like", "反馈 2")],
        )

        shadow_result = await shadow_engine.process_feedback_batch_if_needed()
        assert shadow_result["profile_rebuilt"] is True
        assert shadow_gate.calls, "显著变化必须咨询接入点③"
        assert shadow_gate.calls[0]["write_point"] == "feedback_soul_rebuild"
        assert shadow_gate.calls[0]["change"]["trigger"] == "feedback_batch"  # type: ignore[index]

        # enforce / downgrade → 放弃重建
        enforce_memory = MemoryManager(tmp_path / "enforce")
        enforce_memory.initialize()
        enforce_engine = SoulEngine(llm=FakeRegistry("{}"), memory=enforce_memory)
        enforce_gate = _StubGate("enforce", GateDecision(verdict=DOWNGRADE, enforced=True))
        enforce_engine._posture_gate = enforce_gate  # type: ignore[assignment]
        self._stub_analyzer(monkeypatch, enforce_engine, significant)
        monkeypatch.setattr(enforce_engine._profile_builder, "build", fake_build)
        await self._seed_feedback(
            enforce_memory,
            [("like", "反馈 0"), ("like", "反馈 1"), ("like", "反馈 2")],
        )

        enforce_result = await enforce_engine.process_feedback_batch_if_needed()
        assert enforce_result["preference_updated"] is True
        assert enforce_result["profile_rebuilt"] is False
        assert enforce_gate.calls

    @pytest.mark.asyncio
    async def test_held_replay_is_consumed_after_the_batch(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """契约⑤「统一线必须继承」: 批后消费 replaying 状态的搁置更新。"""
        from openbiliclaw.soul.confusion import ConfusionManager, HeldUpdate
        from openbiliclaw.storage.database import Database

        memory = MemoryManager(tmp_path)
        memory.initialize()
        db = Database(tmp_path / "confusion.db")
        db.initialize()
        engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory, database=db)
        self._stub_analyzer(
            monkeypatch,
            engine,
            {
                "interests": [{"name": "桌游", "category": "游戏", "weight": 0.7}],
                "style": {},
                "context": {},
                "exploration_openness": 0.4,
                "disliked_topics": [],
                "favorite_up_users": [],
            },
        )

        manager = ConfusionManager(db)
        confusion_id = db.insert_confusion(topic="桌游", observation="看不懂")
        db.update_confusion(
            confusion_id,
            held_updates=[
                HeldUpdate(held_id="h1", topic="桌游", kind="upgrade", value=0.7).to_dict()
            ],
        )
        manager.resolve(confusion_id, resolution="real_interest")
        assert manager.get(confusion_id).held_updates[0].state == "replaying"

        await self._seed_feedback(
            memory,
            [("dislike", "反馈 0"), ("dislike", "反馈 1"), ("dislike", "反馈 2")],
        )

        result = await engine.process_feedback_batch_if_needed()

        assert result["triggered"] is True
        assert manager.get(confusion_id).held_updates[0].state == "applied"

    @pytest.mark.asyncio
    async def test_cursor_advance_is_idempotent(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """契约⑥「统一线必须继承」: 游标推进后，同一批反馈不会被二次消费。"""
        memory = MemoryManager(tmp_path)
        memory.initialize()
        engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
        calls: list[int] = []

        async def fake_analyze_events(
            *,
            events: list[dict[str, object]],
            existing_preference: dict[str, object],
            event_chunk_size: int = 0,
            awareness_notes: list[dict[str, object]] | None = None,
            active_insights: list[dict[str, object]] | None = None,
        ) -> dict[str, object]:
            del existing_preference, event_chunk_size, awareness_notes, active_insights
            calls.append(len(events))
            return {
                "interests": [],
                "style": {},
                "context": {},
                "exploration_openness": 0.4,
                "disliked_topics": [],
                "favorite_up_users": [],
            }

        monkeypatch.setattr(engine._preference_analyzer, "analyze_events", fake_analyze_events)
        await self._seed_feedback(
            memory,
            [("dislike", "反馈 0"), ("dislike", "反馈 1"), ("dislike", "反馈 2")],
        )

        first = await engine.process_feedback_batch_if_needed()
        cursor_after_first = memory.load_feedback_state()["last_processed_feedback_event_id"]
        second = await engine.process_feedback_batch_if_needed()

        assert first["triggered"] is True
        assert second["triggered"] is False
        assert second["feedback_count"] == 0
        assert calls == [3]
        assert (
            memory.load_feedback_state()["last_processed_feedback_event_id"] == cursor_after_first
        )


class TestUnifiedInterestLineShim:
    """统一兴趣更新线 Wave B：``process_feedback_batch_if_needed`` 变 shim。

    开关关 → 旧批线逐字节不变（``TestFeedbackBatchContract`` 六条契约仍在钉它）。
    开关开 → 一次性幂等迁移旧游标之后的反馈 + 触发 pipeline flush。
    """

    _NEUTRAL_PREFERENCE: dict[str, object] = {
        "interests": [],
        "style": {},
        "context": {},
        "exploration_openness": 0.4,
        "disliked_topics": [],
        "favorite_up_users": [],
    }

    @staticmethod
    async def _seed_feedback(
        memory: MemoryManager,
        rows: list[tuple[str, str]],
        *,
        owner: str = "",
    ) -> None:
        for feedback_type, title in rows:
            metadata = {"feedback_type": feedback_type, "feedback_note": "备注"}
            if owner:
                metadata["profile_update_owner"] = owner
            await memory.propagate_event(
                {
                    "event_type": "feedback",
                    "title": title,
                    "metadata": metadata,
                }
            )

    @classmethod
    def _stub_analyzer(
        cls,
        monkeypatch: pytest.MonkeyPatch,
        engine: SoulEngine,
        analyzed: list[list[dict[str, object]]],
    ) -> None:
        async def fake_analyze_events(
            *,
            events: list[dict[str, object]],
            existing_preference: dict[str, object],
            event_chunk_size: int = 0,
            awareness_notes: list[dict[str, object]] | None = None,
            active_insights: list[dict[str, object]] | None = None,
        ) -> dict[str, object]:
            del existing_preference, event_chunk_size, awareness_notes, active_insights
            analyzed.append(list(events))
            return dict(cls._NEUTRAL_PREFERENCE)

        monkeypatch.setattr(engine._preference_analyzer, "analyze_events", fake_analyze_events)

    @staticmethod
    def _spy_enqueue(engine: SoulEngine, batches: list[list[object]]) -> None:
        """Record non-empty atomic checkpoint batches, then delegate."""
        real = engine._pipeline.checkpointed_enqueue_batch

        async def spy(signals: list[object], **kwargs: object) -> object:
            if signals:
                batches.append(list(signals))
            return await real(signals, **kwargs)  # type: ignore[arg-type]

        engine._pipeline.checkpointed_enqueue_batch = spy  # type: ignore[method-assign, assignment]

    def test_feedback_signal_requires_positive_durable_event_id(self) -> None:
        with pytest.raises(ValueError, match="positive id"):
            SoulEngine._feedback_event_to_signal(
                {"id": 0, "event_type": "feedback", "metadata": {"feedback_type": "like"}}
            )

    @pytest.mark.asyncio
    async def test_owner_v2_cutover_fences_v1_direct_owned_rows_then_claims_next_row(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Upgrade fence: old cursor 2 + direct-owned rows 3..5; v2 owns row 6+."""
        memory = MemoryManager(tmp_path)
        memory.initialize()
        await self._seed_feedback(
            memory,
            [
                ("like", "已在旧游标内 1"),
                ("dislike", "已在旧游标内 2"),
                ("like", "v1 direct-owned 3"),
                ("dislike", "v1 direct-owned 4"),
                ("comment", "v1 direct-owned 5"),
            ],
        )
        # Ownership follows durable row id, not event time. Simulate a browser
        # backfill whose newest inserted row carries the oldest created_at;
        # query_events(limit=1) would incorrectly fence only through row 4.
        memory._database.conn.execute(  # noqa: SLF001 - exact upgrade fixture
            "UPDATE events SET created_at = ? WHERE id = ?",
            ("2020-01-01 00:00:00", 5),
        )
        memory._database.conn.commit()  # noqa: SLF001 - exact upgrade fixture
        memory.save_feedback_state(
            {
                "last_processed_feedback_event_id": 2,
                "last_feedback_reanalyzed_at": "",
                "unified_interest_line_migrated_at": "2026-07-28T00:00:00",
            }
        )
        engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory, unified_interest_line=True)
        analyzed: list[list[dict[str, object]]] = []
        batches: list[list[object]] = []
        self._stub_analyzer(monkeypatch, engine, analyzed)
        self._spy_enqueue(engine, batches)

        cutover = await engine.prepare_feedback_owner_cutover()

        assert cutover["prepared"] is True
        assert cutover["fenced_feedback_event_id"] == 5
        state = memory.load_feedback_state()
        assert state["last_processed_feedback_event_id"] == 5
        assert state["feedback_owner_version"] == 2
        assert str(state["feedback_owner_cutover_at"]).strip()
        assert batches == [], "v1 direct-owned rows must not be enqueued again"

        await self._seed_feedback(
            memory,
            [("dislike", "v2 cursor-owned 6")],
            owner="content_feedback",
        )
        mirrored_writes: list[dict[str, object]] = []
        real_save_feedback_state = memory.save_feedback_state

        def capture_feedback_mirror(state: dict[str, object]) -> None:
            mirrored_writes.append(dict(state))
            real_save_feedback_state(state)

        monkeypatch.setattr(memory, "save_feedback_state", capture_feedback_mirror)
        result = await engine.process_feedback_batch_if_needed()

        assert result["enqueued_feedback_events"] == 1
        assert len(batches) == 1
        [signal] = batches[0]
        assert signal.id == "feedback-event-6"  # type: ignore[attr-defined]
        assert signal.payload["title"] == "v2 cursor-owned 6"  # type: ignore[attr-defined]
        assert memory.load_feedback_state()["last_processed_feedback_event_id"] == 6
        assert mirrored_writes[-1]["feedback_owner_version"] == 2
        assert str(mirrored_writes[-1]["feedback_owner_cutover_at"]).strip()

    @pytest.mark.asyncio
    async def test_cursor_behind_migrates_every_row_as_a_feedback_signal(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """不变量③：旧游标之后未消费的反馈一条不丢，且带 FEEDBACK 特权入线。

        必须用 ``signal_from_feedback`` 而非 ``signals_from_events``——后者永远
        不产 ``SignalType.FEEDBACK``，迁移行会静默丢掉全部反馈特权。
        """
        from openbiliclaw.soul.pipeline import SignalType

        memory = MemoryManager(tmp_path)
        memory.initialize()
        engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory, unified_interest_line=True)
        analyzed: list[list[dict[str, object]]] = []
        batches: list[list[object]] = []
        self._stub_analyzer(monkeypatch, engine, analyzed)
        self._spy_enqueue(engine, batches)

        await self._seed_feedback(
            memory, [("dislike", "旧反馈 0"), ("like", "旧反馈 1"), ("dislike", "旧反馈 2")]
        )

        result = await engine.process_feedback_batch_if_needed()

        assert result["unified_interest_line"] is True
        assert result["migrated_feedback_events"] == 3
        # 返回形状与旧批线兼容：迁移当轮就被消费，triggered/feedback_count 必须
        # 如实反映（迁移 ingest 触发的消费也算，不能只看 tick 的结果）。
        assert result["triggered"] is True
        assert result["feedback_count"] == 3
        assert result["preference_updated"] is True
        # 桩分析器返回的偏好与现状等价，所以「写了」为真而「变了」为假——两个语义
        # 分开报，`preference_updated` 保持旧批线含义（偏好层被重写过）。
        assert result["preference_changed"] is False
        assert len(batches) == 1
        assert len(batches[0]) == 3
        assert all(sig.signal_type is SignalType.FEEDBACK for sig in batches[0])  # type: ignore[attr-defined]
        assert [sig.payload["title"] for sig in batches[0]] == [  # type: ignore[attr-defined]
            "旧反馈 0",
            "旧反馈 1",
            "旧反馈 2",
        ]
        # 迁移必须触发消费（不再等 600s 最短间隔）
        assert analyzed, "迁移后缓冲达阈值必须立即消费"
        state = memory.load_feedback_state()
        assert state["last_processed_feedback_event_id"] == 3
        assert str(state["unified_interest_line_migrated_at"]).strip()

    @pytest.mark.asyncio
    async def test_second_run_migrates_nothing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """不变量③：重复迁移幂等——二次启动零重复。"""
        memory = MemoryManager(tmp_path)
        memory.initialize()
        engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory, unified_interest_line=True)
        analyzed: list[list[dict[str, object]]] = []
        batches: list[list[object]] = []
        self._stub_analyzer(monkeypatch, engine, analyzed)
        self._spy_enqueue(engine, batches)

        await self._seed_feedback(
            memory, [("dislike", "旧反馈 0"), ("like", "旧反馈 1"), ("dislike", "旧反馈 2")]
        )

        first = await engine.process_feedback_batch_if_needed()
        second = await engine.process_feedback_batch_if_needed()

        assert first["migrated_feedback_events"] == 3
        assert second["migrated_feedback_events"] == 0
        assert len(batches) == 1, "第二次不得再次入线"

    @pytest.mark.asyncio
    async def test_empty_feedback_owner_recovery_skips_tick_maintenance(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Empty startup/hot-reload recovery cannot trigger periodic LLM work."""
        memory = MemoryManager(tmp_path)
        memory.initialize()
        engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory, unified_interest_line=True)
        maintenance_calls = 0

        async def unexpected_maintenance(*_args: object, **_kwargs: object) -> None:
            nonlocal maintenance_calls
            maintenance_calls += 1

        monkeypatch.setattr(
            engine._pipeline,
            "_run_tick_maintenance",
            unexpected_maintenance,
        )

        result = await engine.process_feedback_batch_if_needed()

        assert result["migrated_feedback_events"] == 0
        assert result["feedback_count"] == 0
        assert maintenance_calls == 0

    @pytest.mark.asyncio
    async def test_cursor_continues_claiming_live_feedback_after_migration_marker(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """迁移 marker 只留作 provenance；新 durable 行继续由 cursor 领取。"""
        memory = MemoryManager(tmp_path)
        memory.initialize()
        engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory, unified_interest_line=True)
        analyzed: list[list[dict[str, object]]] = []
        batches: list[list[object]] = []
        self._stub_analyzer(monkeypatch, engine, analyzed)
        self._spy_enqueue(engine, batches)

        await self._seed_feedback(memory, [("dislike", "旧反馈 0"), ("like", "旧反馈 1")])
        first = await engine.process_feedback_batch_if_needed()
        assert first["migrated_feedback_events"] == 2
        assert len(batches) == 1

        # /api/feedback 只落账；同一个 cursor owner 领取之后的实时行。
        await self._seed_feedback(
            memory,
            [("dislike", "实时反馈 0"), ("like", "实时反馈 1")],
            owner="content_feedback",
        )

        second = await engine.process_feedback_batch_if_needed()

        assert second["migrated_feedback_events"] == 2
        assert second["enqueued_feedback_events"] == 2
        assert len(batches) == 2
        assert [sig.payload["title"] for sig in batches[1]] == [  # type: ignore[attr-defined]
            "实时反馈 0",
            "实时反馈 1",
        ]

    @pytest.mark.asyncio
    async def test_retractions_are_skipped_but_the_cursor_clears_them(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """迁移跳过 retraction（与旧批线一致），游标仍越过它们。

        历史 retraction 早已在写入当时抵消过对应正向行；此刻补一次折价只会对着
        一个「从未含那些正向行」的偏好层重放，纯粹是噪声。折价语义只对**将来**
        的实时信号生效，由 A/B 门 3 把关。
        """
        memory = MemoryManager(tmp_path)
        memory.initialize()
        engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory, unified_interest_line=True)
        analyzed: list[list[dict[str, object]]] = []
        batches: list[list[object]] = []
        self._stub_analyzer(monkeypatch, engine, analyzed)
        self._spy_enqueue(engine, batches)

        await self._seed_feedback(
            memory,
            [("dislike", "真反馈 0"), ("retraction", "撤回 0"), ("dislike", "真反馈 1")],
        )

        result = await engine.process_feedback_batch_if_needed()

        assert result["migrated_feedback_events"] == 2
        assert [sig.payload["title"] for sig in batches[0]] == ["真反馈 0", "真反馈 1"]  # type: ignore[attr-defined]
        assert memory.load_feedback_state()["last_processed_feedback_event_id"] == 3

    @pytest.mark.asyncio
    async def test_unrelated_feedback_namespaces_are_skipped_but_advance_cursor(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Hypothesis/Bangumi feedback already has another learning owner."""
        memory = MemoryManager(tmp_path)
        memory.initialize()
        engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory, unified_interest_line=True)
        analyzed: list[list[dict[str, object]]] = []
        batches: list[list[object]] = []
        self._stub_analyzer(monkeypatch, engine, analyzed)
        self._spy_enqueue(engine, batches)

        await memory.propagate_event(
            {
                "event_type": "feedback",
                "title": "某条画像假设",
                "metadata": {"hypothesis": "某条画像假设", "signal": "like"},
            }
        )
        await memory.propagate_event(
            {
                "event_type": "feedback",
                "title": "Bangumi 低分条目",
                "metadata": {
                    "feedback_type": "dislike",
                    "source_platform": "bangumi",
                    "import_source": "bangumi_public_collection",
                },
            }
        )

        result = await engine.process_feedback_batch_if_needed()

        assert result["enqueued_feedback_events"] == 0
        assert result["feedback_count"] == 0
        assert batches == []
        assert analyzed == []
        assert memory.load_feedback_state()["last_processed_feedback_event_id"] == 2

        await self._seed_feedback(
            memory,
            [("like", "下一条真实内容反馈")],
            owner="content_feedback",
        )
        next_result = await engine.process_feedback_batch_if_needed()

        assert next_result["enqueued_feedback_events"] == 1
        [signal] = batches[0]
        assert signal.id == "feedback-event-3"  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_enqueue_cursor_crash_retries_without_duplicate_buffer_entries(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """enqueue 已落盘但 cursor 未推进时，stable IDs 让重试幂等。"""
        memory = MemoryManager(tmp_path)
        memory.initialize()
        engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory, unified_interest_line=True)
        analyzed: list[list[dict[str, object]]] = []
        self._stub_analyzer(monkeypatch, engine, analyzed)

        await self._seed_feedback(
            memory, [("dislike", "旧反馈 0"), ("like", "旧反馈 1"), ("dislike", "旧反馈 2")]
        )
        await engine.prepare_feedback_owner_cutover()

        real_save = memory.save_feedback_state

        def crash_before_cursor(_state: dict[str, object]) -> None:
            raise RuntimeError("crash before cursor")

        monkeypatch.setattr(memory, "save_feedback_state", crash_before_cursor)
        with pytest.raises(RuntimeError, match="crash before cursor"):
            await engine.process_feedback_batch_if_needed()

        assert memory.load_feedback_state()["last_processed_feedback_event_id"] == 0
        for layer in ("interest", "surface"):
            assert [
                item["id"]
                for item in engine._pipeline._buffers[layer].signals  # noqa: SLF001
            ] == ["feedback-event-1", "feedback-event-2", "feedback-event-3"]

        monkeypatch.setattr(memory, "save_feedback_state", real_save)
        engine2 = SoulEngine(llm=FakeRegistry("{}"), memory=memory, unified_interest_line=True)
        self._stub_analyzer(monkeypatch, engine2, analyzed)
        accepted: list[int] = []
        real_enqueue = engine2._pipeline.checkpointed_enqueue_batch

        async def spy(signals: list[object], **kwargs: object) -> object:
            result = await real_enqueue(signals, **kwargs)  # type: ignore[arg-type]
            accepted.append(result.signals_accepted)
            return result

        engine2._pipeline.checkpointed_enqueue_batch = spy  # type: ignore[method-assign, assignment]

        claimed, _ = await engine2._migrate_legacy_feedback_cursor_if_needed()

        assert claimed == 0
        assert accepted == [0]
        assert memory.load_feedback_state()["last_processed_feedback_event_id"] == 3
        for layer in ("interest", "surface"):
            assert len(engine2._pipeline._buffers[layer].signals) == 3  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_cursor_tick_crash_recovers_persisted_buffer(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """cursor 已推进但 tick 未消费时，重建 owner 会消费持久缓冲。"""
        memory = MemoryManager(tmp_path)
        memory.initialize()
        engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory, unified_interest_line=True)
        await self._seed_feedback(memory, [("dislike", "等待后台分析")])

        async def crash_before_tick() -> object:
            raise RuntimeError("crash before tick")

        monkeypatch.setattr(engine._pipeline, "tick_if_buffered", crash_before_tick)
        with pytest.raises(RuntimeError, match="crash before tick"):
            await engine.process_feedback_batch_if_needed()

        assert memory.load_feedback_state()["last_processed_feedback_event_id"] == 1
        rebuilt = SoulEngine(
            llm=FakeRegistry("{}"),
            memory=memory,
            unified_interest_line=True,
        )
        analyzed: list[list[dict[str, object]]] = []
        self._stub_analyzer(monkeypatch, rebuilt, analyzed)

        async def skip_optional_maintenance(*_args: object, **_kwargs: object) -> None:
            return None

        monkeypatch.setattr(
            rebuilt._pipeline,
            "_run_tick_maintenance",
            skip_optional_maintenance,
        )
        for layer in ("interest", "surface"):
            [signal] = rebuilt._pipeline._buffers[layer].signals  # noqa: SLF001
            assert signal["id"] == "feedback-event-1"
            assert signal["signal_type"] == "feedback"

        recovered = await rebuilt.process_feedback_batch_if_needed()

        assert recovered["enqueued_feedback_events"] == 0
        assert analyzed, "persisted cursor→tick buffer must be consumed after restart"
        for layer in ("interest", "surface"):
            assert rebuilt._pipeline._buffers[layer].signals == []  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_flag_off_runs_the_legacy_batch_and_writes_no_marker(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """开关关 = 旧批线原样（回退路径）；迁移标记绝不落盘。"""
        memory = MemoryManager(tmp_path)
        memory.initialize()
        engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
        analyzed: list[list[dict[str, object]]] = []
        batches: list[list[object]] = []
        self._stub_analyzer(monkeypatch, engine, analyzed)
        self._spy_enqueue(engine, batches)

        await self._seed_feedback(
            memory, [("dislike", "反馈 0"), ("dislike", "反馈 1"), ("dislike", "反馈 2")]
        )

        result = await engine.process_feedback_batch_if_needed()

        assert result["triggered"] is True
        assert result["feedback_count"] == 3
        assert result["preference_updated"] is True
        assert "unified_interest_line" not in result
        assert batches == [], "开关关时反馈绝不进 pipeline"
        assert len(analyzed) == 1
        assert memory.load_feedback_state()["unified_interest_line_migrated_at"] == ""
