"""Free cognition separates historical context and corrects rejected evidence once."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from typing import Any

import pytest

from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.soul.awareness_analyzer import AwarenessAnalyzer
from openbiliclaw.soul.cognition_cycle import CognitionCycle
from openbiliclaw.sponsored_runtime_state import (
    SponsoredRuntimeSettings,
    install_sponsored_settings,
    reset_sponsored_settings,
)


@pytest.fixture(autouse=True)
def free_mode():
    install_sponsored_settings(SponsoredRuntimeSettings(enabled=True))
    yield
    reset_sponsored_settings()


class RecordingService:
    def __init__(self, *, invalid_until_feedback: bool = False):
        self.calls: list[dict[str, Any]] = []
        self.invalid_until_feedback = invalid_until_feedback

    async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(kwargs)
        cited = (
            17
            if self.invalid_until_feedback and "<validation_feedback>" not in kwargs["user_input"]
            else 101
        )
        return LLMResponse(
            provider="sponsored",
            content=json.dumps(
                {
                    "notes": [
                        {"observation": "本轮浏览了 Rust 实践。", "source_event_ids": [cited]}
                    ],
                    "confusions": [],
                }
            ),
        )


def _events():
    return [
        {"id": 17, "event_type": "view", "context": "历史音乐浏览，仅供上下文"},
        {"id": 101, "event_type": "view", "context": "本轮 Rust 实践浏览"},
    ]


async def test_new_evidence_and_read_only_lookback_are_not_mixed_in_recent_events():
    service = RecordingService()
    await AwarenessAnalyzer(service).analyze_with_confusions(
        events=_events(), preference={}, soul_profile={}, source_event_ids=[101]
    )
    prompt = service.calls[0]["user_input"]
    recent = json.loads(
        re.search(r"<recent_events>\s*(.*?)\s*</recent_events>", prompt, re.S).group(1)
    )
    assert [row["id"] for row in recent] == [101]
    assert "read_only_lookback" in prompt and "历史音乐浏览" in prompt
    assert "101" not in service.calls[0]["system_instruction"]


async def test_grounding_retry_uses_feedback_without_adding_attempts(monkeypatch):
    monkeypatch.setattr("openbiliclaw.soul.cognition_cycle._AWARENESS_RETRY_BACKOFF_SECONDS", 0)
    service = RecordingService(invalid_until_feedback=True)
    cycle = object.__new__(CognitionCycle)
    cycle._awareness_analyzer = AwarenessAnalyzer(service)
    cycle._cognition_max_tokens = 2048
    cycle._confusion_manager = lambda: SimpleNamespace(list_for_generation_context=lambda: [])
    notes, _ = await cycle._awareness_with_retry(_events(), {}, {}, [101])
    assert len(service.calls) == 2 and notes[0].source_event_ids == [101]
    assert service.calls[0]["system_instruction"] == service.calls[1]["system_instruction"]
    assert "<validation_feedback>" in service.calls[1]["user_input"]
