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
