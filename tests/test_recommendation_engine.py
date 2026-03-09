"""Tests for recommendation ranking engine."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.recommendation.engine import RecommendationEngine
from openbiliclaw.soul.profile import InterestTag, PreferenceLayer, SoulProfile
from openbiliclaw.storage.database import Database


class _DummyLLM:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        self.calls.append(
            {
                "system_instruction": system_instruction,
                "user_input": user_input,
                "history": history,
            }
        )
        return LLMResponse(
            content=json.dumps(
                {
                    "expression": "这条内容会接住你最近那种想把问题想透的状态。",
                    "topic_label": "你最近那种想把问题想透的状态",
                },
                ensure_ascii=False,
            ),
            provider="test",
            model="dummy",
            usage={},
        )


def _build_profile() -> SoulProfile:
    return SoulProfile(
        personality_portrait="一个偏好高信息密度、慢热但判断稳定的人。",
        core_traits=["理性", "克制"],
        preferences=PreferenceLayer(
            interests=[InterestTag(name="纪录片", category="知识", weight=0.9)]
        ),
    )


@pytest.mark.asyncio
async def test_generate_recommendations_ranks_discovered_and_records_history() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        discovered = [
            DiscoveredContent(bvid="BV1A", title="A", relevance_score=0.71),
            DiscoveredContent(bvid="BV1B", title="B", relevance_score=0.92),
            DiscoveredContent(bvid="BV1C", title="C", relevance_score=0.83),
        ]

        recommendations = await engine.generate_recommendations(
            discovered=discovered,
            profile=_build_profile(),
            limit=2,
        )

        assert [item.content.bvid for item in recommendations] == ["BV1B", "BV1C"]
        assert recommendations[0].confidence == 0.92

        history = db.get_recommendations(limit=10)
        assert [row["bvid"] for row in history] == ["BV1C", "BV1B"]


@pytest.mark.asyncio
async def test_generate_recommendations_reads_from_cache_when_discovered_missing() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        db.cache_content(
            "BV1A",
            title="A",
            up_name="UPA",
            source="search",
            view_count=10,
        )
        db.cache_content(
            "BV1B",
            title="B",
            up_name="UPB",
            source="search",
            view_count=20,
        )
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        recommendations = await engine.generate_recommendations(
            discovered=None,
            profile=_build_profile(),
            limit=1,
        )

        assert [item.content.bvid for item in recommendations] == ["BV1B"]


@pytest.mark.asyncio
async def test_generate_recommendations_does_not_repeat_history() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        db.cache_content(
            "BV1A",
            title="A",
            up_name="UPA",
            source="search",
            view_count=10,
        )
        db.cache_content(
            "BV1B",
            title="B",
            up_name="UPB",
            source="search",
            view_count=20,
        )
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        first = await engine.generate_recommendations(
            discovered=None,
            profile=_build_profile(),
            limit=1,
        )
        second = await engine.generate_recommendations(
            discovered=None,
            profile=_build_profile(),
            limit=1,
        )

        assert [item.content.bvid for item in first] == ["BV1B"]
        assert [item.content.bvid for item in second] == ["BV1A"]


@pytest.mark.asyncio
async def test_generate_recommendations_populates_expression_and_updates_history() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        recommendations = await engine.generate_recommendations(
            discovered=[
                DiscoveredContent(
                    bvid="BV1EXP",
                    title="讲透摄影构图的底层逻辑",
                    up_name="构图实验室",
                    description="从原理出发解释构图。",
                    relevance_score=0.91,
                )
            ],
            profile=_build_profile(),
            limit=1,
        )

        assert recommendations[0].expression == "这条内容会接住你最近那种想把问题想透的状态。"
        assert recommendations[0].topic_label == "你最近那种想把问题想透的状态"
        assert recommendations[0].recommendation_id > 0

        history = db.get_recommendations(limit=10)
        assert history[0]["expression"] == "这条内容会接住你最近那种想把问题想透的状态。"
        assert history[0]["topic"] == "你最近那种想把问题想透的状态"
        assert history[0]["presented"] == 0


@pytest.mark.asyncio
async def test_generate_expression_uses_old_friend_tone_prompt() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        llm = _DummyLLM()
        engine = RecommendationEngine(llm=llm, database=db)

        await engine.generate_expression(
            DiscoveredContent(
                bvid="BV1TONE",
                title="讲透贸易逆差的底层逻辑",
                up_name="经济观察",
                description="从历史和制度角度解释问题。",
                relevance_score=0.89,
            ),
            _build_profile(),
        )

        assert "老B友" in str(llm.calls[0]["system_instruction"])


@pytest.mark.asyncio
async def test_record_feedback_updates_recommendation_feedback_fields() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        recommendation_id = db.insert_recommendation(
            "BV1REC",
            confidence=0.83,
            presented=1,
        )

        await engine.record_feedback(
            recommendation_id,
            feedback_type="like",
            note="这个讲法很对胃口",
        )

        row = db.get_recommendation_by_id(recommendation_id)

        assert row is not None
        assert row["feedback_type"] == "like"
        assert row["feedback_note"] == "这个讲法很对胃口"
        assert row["feedback_at"] is not None


@pytest.mark.asyncio
async def test_record_feedback_accepts_comment_feedback_type() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        recommendation_id = db.insert_recommendation(
            "BV1REC",
            confidence=0.83,
            presented=1,
        )

        await engine.record_feedback(
            recommendation_id,
            feedback_type="comment",
            note="方向对，但讲得不够深。",
        )

        row = db.get_recommendation_by_id(recommendation_id)

        assert row is not None
        assert row["feedback_type"] == "comment"
        assert row["feedback_note"] == "方向对，但讲得不够深。"
        assert row["feedback_at"] is not None
