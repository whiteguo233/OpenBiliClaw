from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.llm.base import LLMResponse
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
    ) -> LLMResponse:
        self.calls.append(messages)
        return LLMResponse(content=self.content, provider="openai")


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
                    "这个人会反复在高信息密度内容里停留，也会主动寻找讲清原理的表达方式。"
                    * 8
                ),
                "core_traits": ["理性", "好奇", "克制"],
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
    saved = json.loads((tmp_path / "memory" / "soul.json").read_text(encoding="utf-8"))
    assert saved["core_traits"] == ["理性", "好奇", "克制"]
    assert saved["preferences"]["interests"][0]["name"] == "科技"


@pytest.mark.asyncio
async def test_get_profile_loads_saved_soul_profile(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    memory.get_layer("soul").data.update(
        {
            "personality_portrait": (
                "这是一个偏爱深度内容、对信息质量较敏感、做决定前会先观察的人。"
                * 8
            ),
            "core_traits": ["理性", "谨慎", "自驱"],
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
    assert profile.preferences.interests[0].name == "科技"


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
                "这是一个偏爱深度内容、会主动寻找原理解释、决策比较克制的人。"
                * 8
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
                "这是一个偏爱深度内容、会主动寻找原理解释、决策比较克制的人。"
                * 8
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
    ) -> dict[str, object]:
        assert len(events) == 3
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
    ) -> object:
        from openbiliclaw.soul.profile import SoulProfile

        return SoulProfile.from_dict(
            {
                "personality_portrait": "这是一个会主动追问世界运行逻辑的人。" * 20,
                "core_traits": ["理性", "主动"],
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
    assert memory.get_layer("soul").data["core_traits"] == ["理性", "主动"]
    cognition_updates = memory.load_cognition_updates()
    assert cognition_updates
    assert cognition_updates[0]["kind"] == "interest_added"
    assert "国际时事" in str(cognition_updates[0]["summary"])


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
    ) -> dict[str, object]:
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
    ) -> object:
        from openbiliclaw.soul.profile import SoulProfile

        assert history == []
        assert preference["interests"][0]["name"] == "纪录片"
        return SoulProfile(
            personality_portrait="这个人最近明显从科技内容转向更具体的人文叙事与纪录片表达。" * 8,
            core_traits=["理性", "耐心", "好奇"],
            values=["真实", "成长"],
            life_stage="处于结构调整阶段",
            deep_needs=["被理解", "看见更深的脉络"],
        )

    monkeypatch.setattr(engine._preference_analyzer, "analyze_events", fake_analyze_events)
    monkeypatch.setattr(engine._profile_builder, "build", fake_build)

    result = await engine.process_feedback_batch_if_needed()

    assert result["profile_rebuilt"] is True
    soul = memory.get_layer("soul").data
    assert soul["core_traits"] == ["理性", "耐心", "好奇"]
    assert "结构调整阶段" in soul["life_stage"]
    cognition_updates = memory.load_cognition_updates()
    kinds = {str(item["kind"]) for item in cognition_updates}
    assert "dislike_added" in kinds
    assert "profile_shift" in kinds
