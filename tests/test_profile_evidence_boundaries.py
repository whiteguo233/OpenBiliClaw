"""Replay the free-model portrait and reversed/undated-history failures."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.soul.profile_builder import ProfileBuilder


@pytest.mark.parametrize("verdict", ["confirmed", "rejected", ""])
async def test_only_user_confirmed_insights_restore_personal_evidence(verdict: str) -> None:
    class Service:
        async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
            return LLMResponse(
                content=json.dumps(
                    {
                        "personality_portrait": (
                            "你提到自己是一名老师，这份职业信息来自你的明确确认，"
                            "其他生活经历仍需要你补充。"
                        ),
                        "life_stage": "教师",
                    }
                ),
                provider="sponsored",
            )

    profile = await ProfileBuilder(Service()).build(
        history=[],
        preference={},
        awareness_notes=[],
        active_insights=[
            {
                "hypothesis": "用户是一名老师",
                "user_verdict": verdict,
                "validated": True,
                "confidence": 0.95,
            }
        ],
    )
    assert profile.life_stage == ("教师" if verdict == "confirmed" else "暂无明确自述")


async def test_free_profile_rebuild_reads_original_user_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.memory.manager import MemoryManager
    from openbiliclaw.soul.engine import SoulEngine

    class Registry:
        async def complete(self, *args: Any, **kwargs: Any) -> LLMResponse:
            return LLMResponse(content="{}")

    memory = MemoryManager(tmp_path)
    memory.initialize()
    await memory.propagate_event(
        {
            "event_type": "dialogue",
            "title": "用户自述",
            "metadata": {"user_message": "我是一名老师", "assistant_message": "你是程序员"},
        }
    )
    engine = SoulEngine(llm=Registry(), memory=memory)
    monkeypatch.setattr("openbiliclaw.llm.sponsored_router.sponsored_enabled", lambda: True)
    summary = ProfileBuilder._summarize_history(engine._profile_rebuild_history())
    assert summary["authored_expressions"] == ["我是一名老师"]


async def test_free_self_description_excludes_assistant_and_unconfirmed_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Service:
        async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
            evidence = kwargs["user_input"]
            assert "我是一名老师" in evidence
            assert "医生" not in evidence and "程序员" not in evidence
            assert kwargs["reasoning_effort"] == ""
            return LLMResponse(
                content=json.dumps(
                    {
                        "personality_portrait": (
                            "你明确说过自己是一名老师，其他性格和生活经历还需要听你自己补充。"
                        ),
                        "life_stage": "老师",
                    }
                ),
                provider="sponsored",
            )

    monkeypatch.setattr("openbiliclaw.llm.sponsored_router.sponsored_enabled", lambda: True)
    profile = await ProfileBuilder(Service()).build(
        history=[
            {
                "event_type": "dialogue",
                "title": "我是医生",
                "metadata": {"user_message": "我是一名老师", "assistant_message": "你是程序员"},
            }
        ],
        preference={},
        awareness_notes=[],
        active_insights=[{"hypothesis": "用户是医生", "confidence": 0.99, "validated": True}],
    )
    assert profile.life_stage == "老师"


def test_recent_history_uses_newest_source_dates_not_first_rows() -> None:
    rows = [
        {"title": "旧课程", "view_at": 1_700_000_000},
        {"title": "新课程", "view_at": 1_730_000_000},
        {"title": "没有日期的收藏", "created_at": "2026-11-05T00:00:00"},
    ]
    summary = ProfileBuilder._summarize_history(rows)
    assert summary["recent_titles"] == ["新课程"]
    assert summary["older_titles"] == ["旧课程"]
    assert "没有日期的收藏" in summary["titles"]


def test_undated_or_simultaneous_events_do_not_invent_a_change_over_time() -> None:
    for timestamp in (None, 1_730_000_000):
        rows = [{"title": name, "view_at": timestamp} for name in ("理论教程", "操作教程")]
        summary = ProfileBuilder._summarize_history(rows)
        assert not summary.get("older_titles")
        if timestamp is None:
            assert not summary.get("recent_titles")


class _RecordedPortrait:
    async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
        # Shape/overconfidence observed in the actual Qwen3-8B passive-history run.
        return LLMResponse(
            provider="sponsored",
            model="Qwen/Qwen3-8B",
            content=json.dumps(
                {
                    "personality_portrait": "你把阳台番茄当实验对象，对生活充满掌控欲。" * 8,
                    "information_preferences": [
                        {"kind": "steps", "evidence": "阳台番茄教程"},
                        {"kind": "principles", "evidence": "不存在的标题"},
                        {"kind": "你亲手种菜", "evidence": "阳台番茄教程"},
                    ],
                    "core_traits": ["沉得住气"],
                    "deep_needs": ["对技术解释准确性的执念"],
                    "values": ["追求确定性"],
                    "motivational_drivers": ["知识体系构建需求"],
                    "life_stage": "工程师",
                    "current_phase": "你正在阳台种菜",
                    "mbti": {
                        "type": "INTP",
                        "confidence": 0.85,
                        "dimensions": {
                            axis: {"pole": pole, "strength": 0.7}
                            for axis, pole in zip(("EI", "SN", "TF", "JP"), "INTP", strict=True)
                        },
                    },
                },
                ensure_ascii=False,
            ),
        )


async def test_passive_free_profile_does_not_persist_unsupported_identity_or_traits() -> None:
    profile = await ProfileBuilder(_RecordedPortrait()).build(
        history=[{"event_type": "favorite", "title": "阳台番茄教程"}],
        preference={},
        awareness_notes=[],
        active_insights=[],
    )
    assert profile.life_stage == "暂无明确自述"
    assert profile.current_phase == "暂无明确自述"
    assert not profile.core_traits and not profile.deep_needs
    assert not profile.values and not profile.motivational_drivers
    assert "实验对象" not in profile.personality_portrait
    assert "掌控欲" not in profile.personality_portrait
    assert "按步骤展开" in profile.personality_portrait
    assert profile.cognitive_style == ["按步骤展开的内容"]
    assert profile._raw_mbti["dimensions"]["EI"]["strength"] == 0.5
    assert profile._raw_mbti["confidence"] <= 0.55


async def test_grounded_short_portrait_does_not_need_invented_padding() -> None:
    class Service:
        async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
            return LLMResponse(
                content=json.dumps(
                    {
                        "personality_portrait": (
                            "从这些内容选择看，你似乎偏爱讲清原理、带有具体例子的解释。"
                            "现有线索还不足以判断你的生活经历。"
                        ),
                    }
                ),
                provider="sponsored",
            )

    profile = await ProfileBuilder(Service()).build(
        history=[{"title": "原理教程"}],
        preference={},
        awareness_notes=[],
        active_insights=[],
    )
    assert "现有线索还不足" in profile.personality_portrait


def test_only_user_authored_text_enters_self_description_evidence() -> None:
    summary = ProfileBuilder._summarize_history(
        [
            {"event_type": "view", "title": "我是程序员的一天"},
            {
                "event_type": "dialogue",
                "metadata": {"user_message": "我是一名老师", "assistant_message": "你是程序员"},
            },
            {
                "event_type": "comment",
                "title": "教程",
                "metadata": {"comment_text": "我更喜欢例子"},
            },
        ]
    )
    assert summary["authored_expressions"] == ["我是一名老师", "我更喜欢例子"]


@pytest.mark.parametrize(
    "source_index,accepted", [(0, True), (True, False), (-1, False), (1, False)]
)
async def test_information_preference_source_must_belong_to_this_request(
    source_index: object, accepted: bool
) -> None:
    class Service:
        async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
            return LLMResponse(
                content=json.dumps(
                    {
                        "personality_portrait": (
                            "只看内容选择还不足以确定现实生活和性格，"
                            "需要更多用户自己的表达作为依据。"
                        ),
                        "information_preferences": [
                            {"kind": "principles", "source_index": source_index}
                        ],
                    }
                ),
                provider="sponsored",
            )

    profile = await ProfileBuilder(Service()).build(
        history=[{"title": "光合作用原理"}], preference={}, awareness_notes=[], active_insights=[]
    )
    assert bool(profile.cognitive_style) is accepted
