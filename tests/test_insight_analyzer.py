from __future__ import annotations

import json

import pytest

from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.soul.profile import AwarenessNote, InsightHypothesis


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


class FakeStructuredService:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict[str, object]] = []

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
        return LLMResponse(content=self.content, provider="openai")


@pytest.mark.asyncio
async def test_insight_analyzer_builds_hypotheses_from_awareness() -> None:
    from openbiliclaw.soul.insight_analyzer import InsightAnalyzer

    service = FakeStructuredService(
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

    insights = await InsightAnalyzer(service).analyze(
        awareness_notes=[
            AwarenessNote(
                date="2026-03-08",
                observation="最近连续浏览高信息密度内容。",
                trend="更偏向深度解释。",
                emotion_guess="专注",
            )
        ],
        preference={},
        soul_profile={},
    )

    assert insights[0].hypothesis.startswith("用户可能通过深度内容")
    assert insights[0].validated is False
    assert insights[0].confidence == 0.62
    assert service.calls


@pytest.mark.asyncio
async def test_insight_analyzer_raises_on_invalid_json() -> None:
    from openbiliclaw.soul.insight_analyzer import InsightAnalyzer, InsightGenerationError

    analyzer = InsightAnalyzer(FakeStructuredService("not-json"))
    with pytest.raises(InsightGenerationError, match="invalid JSON"):
        await analyzer.analyze(
            awareness_notes=[],
            preference={},
            soul_profile={},
        )


def test_merge_insights_combines_matching_hypotheses() -> None:
    from openbiliclaw.soul.insight_analyzer import InsightAnalyzer

    analyzer = InsightAnalyzer(FakeStructuredService("[]"))
    existing = [
        InsightHypothesis(
            hypothesis="用户可能通过深度内容获得掌控感。",
            evidence=["最近连续浏览高信息密度内容。"],
            confidence=0.55,
            validated=False,
            created_at="2026-03-08",
        )
    ]
    incoming = [
        InsightHypothesis(
            hypothesis="用户可能通过深度内容获得掌控感。",
            evidence=["偏好层显示 depth_preference 很高。"],
            confidence=0.68,
            validated=False,
            created_at="2026-03-08",
        )
    ]

    merged = analyzer.merge_insights(existing, incoming)

    assert len(merged) == 1
    assert "偏好层显示 depth_preference 很高。" in merged[0].evidence
    assert merged[0].confidence == 0.68
    assert merged[0].validated is False


@pytest.mark.asyncio
async def test_insight_analyzer_can_use_unified_service() -> None:
    from openbiliclaw.soul.insight_analyzer import InsightAnalyzer

    service = FakeStructuredService(
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

    insights = await InsightAnalyzer(service).analyze(
        awareness_notes=[],
        preference={},
        soul_profile={},
    )

    assert insights[0].hypothesis == "用户可能通过深度内容获得掌控感。"
    assert service.calls


@pytest.mark.asyncio
async def test_insight_analyzer_accepts_results_wrapper() -> None:
    from openbiliclaw.soul.insight_analyzer import InsightAnalyzer

    raw = json.dumps(
        {
            "results": [
                {
                    "hypothesis": "用户在通过系统化内容寻找掌控感。",
                    "evidence": ["连续浏览系统拆解类内容。"],
                    "confidence": 0.6,
                }
            ]
        },
        ensure_ascii=False,
    )

    insights = await InsightAnalyzer(FakeStructuredService(raw)).analyze(
        awareness_notes=[],
        preference={},
        soul_profile={},
    )

    assert len(insights) == 1
    assert insights[0].hypothesis == "用户在通过系统化内容寻找掌控感。"
    assert insights[0].confidence == 0.6


@pytest.mark.asyncio
async def test_insight_analyzer_accepts_jsonl_hypotheses() -> None:
    from openbiliclaw.soul.insight_analyzer import InsightAnalyzer

    raw = "\n".join(
        [
            json.dumps(
                {
                    "hypothesis": "用户偏好可复盘的知识密度。",
                    "evidence": ["最近收藏结构化教程。"],
                    "confidence": 0.61,
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "hypothesis": "用户会被跨领域类比触发兴趣。",
                    "evidence": ["最近点击多个跨学科解释视频。"],
                    "confidence": 0.58,
                },
                ensure_ascii=False,
            ),
        ]
    )

    insights = await InsightAnalyzer(FakeStructuredService(raw)).analyze(
        awareness_notes=[],
        preference={},
        soul_profile={},
    )

    assert [item.hypothesis for item in insights] == [
        "用户偏好可复盘的知识密度。",
        "用户会被跨领域类比触发兴趣。",
    ]


@pytest.mark.asyncio
async def test_insight_analyzer_ignores_echoed_schema_before_final_fenced_array() -> None:
    from openbiliclaw.soul.insight_analyzer import InsightAnalyzer

    raw = (
        '{"type":"object","properties":{"hypothesis":{"type":"string"}}}\n'
        "```json\n"
        '[{"hypothesis":"用户正在寻找系统解释。","evidence":["连续观看结构拆解内容。"],'
        '"confidence":0.63}]\n'
        "```"
    )

    insights = await InsightAnalyzer(FakeStructuredService(raw)).analyze(
        awareness_notes=[],
        preference={},
        soul_profile={},
    )

    assert len(insights) == 1
    assert insights[0].hypothesis == "用户正在寻找系统解释。"


@pytest.mark.asyncio
async def test_insight_analyzer_accepts_malformed_mimo_array_root() -> None:
    from openbiliclaw.soul.insight_analyzer import InsightAnalyzer

    raw = """
{
  [
    {
      "hypothesis": "用户对系统结构有持续兴趣。",
      "evidence": ["连续浏览系统思维内容。"],
      "confidence": 0.6
    }
  ]
}
"""

    insights = await InsightAnalyzer(FakeStructuredService(raw)).analyze(
        awareness_notes=[],
        preference={},
        soul_profile={},
    )

    assert len(insights) == 1
    assert insights[0].hypothesis == "用户对系统结构有持续兴趣。"


def test_insight_analyzer_requires_core_memory_task_service() -> None:
    from openbiliclaw.soul.insight_analyzer import InsightAnalyzer

    with pytest.raises(TypeError, match="complete_structured_task"):
        InsightAnalyzer(FakeRegistry("[]"))
