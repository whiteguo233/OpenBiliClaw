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


def test_merge_insights_collapses_near_duplicate_wording() -> None:
    from openbiliclaw.soul.insight_analyzer import InsightAnalyzer

    analyzer = InsightAnalyzer(FakeStructuredService("[]"))
    existing = [
        InsightHypothesis(
            hypothesis="用户很可能将抖音作为习惯性界面操作而非内容消费渠道，用于消遣或等待状态",
            evidence=["旧证据"],
            confidence=0.90,
            validated=False,
            created_at="2026-08-18",
        )
    ]
    incoming = [
        InsightHypothesis(
            hypothesis="用户很可能将抖音作为习惯性界面操作而非内容消费渠道，用于消遣、等待间隙或低能量状态",
            evidence=["新一轮又提出相近结论"],
            confidence=0.89,
            validated=False,
            created_at="2026-08-22",
        )
    ]

    merged = analyzer.merge_insights(existing, incoming)

    assert len(merged) == 1
    assert merged[0].confidence == 0.89
    assert "新一轮又提出相近结论" in merged[0].evidence
    assert merged[0].created_at == "2026-08-18"


def test_hypothesis_context_includes_user_verdict_and_validated() -> None:
    from openbiliclaw.soul.insight_analyzer import InsightAnalyzer

    context = InsightAnalyzer._hypothesis_to_context_dict(
        InsightHypothesis(
            hypothesis="用户可能更在意工具是否真能落地",
            confidence=0.8,
            validated=True,
            user_verdict="confirmed",
        )
    )

    assert context["validated"] is True
    assert context["user_verdict"] == "confirmed"


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


class TestUserVerdictSurvivesLaterAnalysis:
    """A 「不准」must not be erased by the next 12h insight pass.

    ``merge_insights`` took ``max(old, new)`` on confidence, and a reject only
    capped the score at 0.35 without leaving any trace that the user had judged
    it. So the next pass — the same model re-reading the same kind of behaviour
    — could hand the hypothesis 0.8 again, `max(0.35, 0.8)` restored it, and it
    reappeared in the 待聊 list (threshold 0.60) asking the user about something
    they had already rejected. Confidence could also only ever climb: events
    could raise it, never lower it.
    """

    def _analyzer(self):
        from openbiliclaw.soul.insight_analyzer import InsightAnalyzer

        class _Stub:
            async def complete_structured_task(self, **_kwargs: object) -> object:
                raise AssertionError("merge_insights must not call the LLM")

        return InsightAnalyzer(registry=_Stub())

    def test_reject_is_not_undone_by_a_later_high_confidence_pass(self) -> None:
        from openbiliclaw.soul.profile import InsightHypothesis

        rejected = InsightHypothesis(
            hypothesis="用户只在意理论深度",
            evidence=["旧证据"],
            confidence=0.35,
            validated=False,
            user_verdict="rejected",
        )
        incoming = InsightHypothesis(
            hypothesis="用户只在意理论深度",
            evidence=["新一轮又提炼出同一条"],
            confidence=0.80,
            validated=False,
        )

        merged = self._analyzer().merge_insights([rejected], [incoming])

        assert len(merged) == 1
        item = merged[0]
        assert item.user_verdict == "rejected", "用户的判断必须被保留"
        assert item.confidence <= 0.35, f"被否定的假设不该被推回 {item.confidence}"
        assert item.confidence < 0.60, "更不该重新进入待聊列表阈值"

    def test_unjudged_hypothesis_tracks_the_latest_evidence_both_ways(self) -> None:
        """未经用户评价的假设应当双向跟随最新证据，而不是只涨不跌。"""
        from openbiliclaw.soul.profile import InsightHypothesis

        analyzer = self._analyzer()
        old = InsightHypothesis(hypothesis="用户偏好长视频", confidence=0.80)

        weaker = analyzer.merge_insights(
            [old], [InsightHypothesis(hypothesis="用户偏好长视频", confidence=0.45)]
        )[0]
        assert weaker.confidence == 0.45, "反向证据必须能把置信度拉低"

        stronger = analyzer.merge_insights(
            [weaker], [InsightHypothesis(hypothesis="用户偏好长视频", confidence=0.72)]
        )[0]
        assert stronger.confidence == 0.72, "正向证据仍然能推高"

    def test_confirmed_hypothesis_keeps_its_validated_floor(self) -> None:
        """用户点过「准」的假设不被后续一次弱分析降级。"""
        from openbiliclaw.soul.profile import InsightHypothesis

        confirmed = InsightHypothesis(
            hypothesis="用户在自学木工",
            confidence=0.85,
            validated=True,
            user_verdict="confirmed",
        )
        merged = self._analyzer().merge_insights(
            [confirmed], [InsightHypothesis(hypothesis="用户在自学木工", confidence=0.40)]
        )[0]

        assert merged.validated is True
        assert merged.user_verdict == "confirmed"
        assert merged.confidence >= 0.75, "已确认的假设不该被一次弱分析打回"

    def test_legacy_hypotheses_without_verdict_still_load(self) -> None:
        """旧数据没有 user_verdict 字段，必须按「未评价」处理。"""
        from openbiliclaw.soul.profile import insight_hypothesis_from_dict

        item = insight_hypothesis_from_dict(
            {"hypothesis": "旧假设", "confidence": 0.7, "validated": False}
        )
        assert item.user_verdict == ""
