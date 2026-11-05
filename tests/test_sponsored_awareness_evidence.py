"""A fabricated citation must not be converted to seemingly supported free-model memory."""

from __future__ import annotations

import json
from typing import Any

import pytest

from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.soul.awareness_analyzer import AwarenessAnalyzer, AwarenessGenerationError


@pytest.mark.parametrize("with_confusions", [False, True])
@pytest.mark.parametrize("claimed", [[1, 99], [1]])
async def test_free_awareness_rejects_explicit_foreign_event_citations(
    with_confusions: bool, claimed: list[int]
) -> None:
    class Service:
        async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
            notes = [
                {"date": "2026-11-05", "observation": "关注原理讲解", "source_event_ids": claimed}
            ]
            return LLMResponse(
                content=json.dumps(
                    {"notes": notes, "confusions": []} if with_confusions else notes
                ),
                provider="sponsored",
            )

    analyzer = AwarenessAnalyzer(Service())
    call = analyzer.analyze_with_confusions if with_confusions else analyzer.analyze
    kwargs = {
        "events": [{"id": 1, "title": "原理课程", "event_type": "view"}],
        "preference": {},
        "soul_profile": {},
        "source_event_ids": [1],
    }
    if 99 in claimed:
        with pytest.raises(AwarenessGenerationError, match="outside"):
            await call(**kwargs)
    else:
        result = await call(**kwargs)
        notes = result[0] if with_confusions else result
        assert notes[0].source_event_ids == [1]
        assert not notes[0].source_event_ids_approximate


@pytest.mark.parametrize("with_confusions", [False, True])
@pytest.mark.parametrize("source_time", ["2026-11-01T10:00:00+00:00", None])
async def test_free_awareness_date_uses_only_cited_source_time(
    with_confusions: bool,
    source_time: str | None,
) -> None:
    class Service:
        async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
            notes = [{"date": "2026-03-08", "observation": "关注原理", "source_event_ids": [1]}]
            return LLMResponse(
                content=json.dumps(
                    {"notes": notes, "confusions": []} if with_confusions else notes,
                ),
                provider="sponsored",
            )

    analyzer = AwarenessAnalyzer(Service())
    call = analyzer.analyze_with_confusions if with_confusions else analyzer.analyze
    result = await call(
        events=[
            {"id": 1, "created_at": "2026-11-05T00:00:00Z", "metadata": {"timestamp": source_time}},
            {"id": 2, "metadata": {"timestamp": "2026-11-04T10:00:00Z"}},
        ],
        preference={},
        soul_profile={},
        source_event_ids=[1, 2],
    )
    notes = result[0] if with_confusions else result
    assert notes[0].date == ("2026-11-01" if source_time else "")


@pytest.mark.parametrize("second_day,accepted", [("2026-11-01", False), ("2026-11-02", True)])
async def test_free_awareness_cannot_call_two_same_day_events_two_consecutive_days(
    second_day: str,
    accepted: bool,
) -> None:
    class Service:
        async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
            return LLMResponse(
                provider="sponsored",
                content=json.dumps(
                    [
                        {
                            "date": "2026-03-08",
                            "observation": "连续两天收藏技术内容",
                            "source_event_ids": [1, 2],
                        }
                    ]
                ),
            )

    analyzer = AwarenessAnalyzer(Service())
    kwargs = {
        "events": [
            {"id": 1, "metadata": {"timestamp": "2026-11-01T10:00:00Z"}},
            {"id": 2, "metadata": {"timestamp": second_day + "T11:00:00Z"}},
        ],
        "preference": {},
        "soul_profile": {},
        "source_event_ids": [1, 2],
    }
    if accepted:
        notes = await analyzer.analyze(**kwargs)
        assert notes[0].date == second_day
    else:
        with pytest.raises(AwarenessGenerationError, match="day-count"):
            await analyzer.analyze(**kwargs)
