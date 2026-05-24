from __future__ import annotations

import json
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.llm.service import ModuleOverride
from openbiliclaw.memory.manager import MemoryManager
from openbiliclaw.soul.engine import SoulEngine

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
async def test_build_initial_profile_reads_preference_and_saves_soul(tmp_path: Path) -> None:
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
    assert "更明确" in str(updates[0]["impact"])
    assert "单条明确反馈" in str(updates[0]["reasoning"])
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
        assert event_chunk_size == 200
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
    ) -> list[dict[str, object]]:
        assert core_memory["soul_summary"]["personality_portrait"] == ""
        return [
            {
                "kind": "goal",
                "content": "想更系统地理解国际局势",
                "confidence": 0.82,
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

    assert result["preference_updated"] is False
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

    assert result["preference_updated"] is False
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
        assert event_chunk_size == 200
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
