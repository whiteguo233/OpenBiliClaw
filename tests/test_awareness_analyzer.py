from __future__ import annotations

import json
from pathlib import Path

import pytest

from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.soul.profile import AwarenessNote


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
async def test_awareness_analyzer_builds_notes_from_recent_events() -> None:
    from openbiliclaw.soul.awareness_analyzer import AwarenessAnalyzer

    service = FakeStructuredService(
        json.dumps(
            [
                {
                    "date": "2026-03-08",
                    "observation": "最近连续浏览高信息密度内容。",
                    "trend": "更偏向深度解释而非轻量消遣。",
                    "emotion_guess": "可能处于主动吸收和整理信息的阶段。",
                }
            ],
            ensure_ascii=False,
        )
    )

    notes = await AwarenessAnalyzer(service).analyze(
        events=[{"event_type": "view", "title": "AI 工具实测"}],
        preference={},
        soul_profile={},
    )

    assert notes[0].observation.startswith("最近连续浏览")
    assert notes[0].trend.startswith("更偏向深度解释")
    assert service.calls


@pytest.mark.asyncio
async def test_awareness_analyzer_accepts_object_wrapped_notes() -> None:
    from openbiliclaw.soul.awareness_analyzer import AwarenessAnalyzer

    service = FakeStructuredService(
        json.dumps(
            {
                "results": [
                    {
                        "date": "2026-03-08",
                        "observation": "最近连续浏览高信息密度内容。",
                        "trend": "更偏向深度解释而非轻量消遣。",
                        "emotion_guess": "可能处于主动吸收和整理信息的阶段。",
                    }
                ]
            },
            ensure_ascii=False,
        )
    )

    notes = await AwarenessAnalyzer(service).analyze(
        events=[{"event_type": "view", "title": "AI 工具实测"}],
        preference={},
        soul_profile={},
    )

    assert notes == [
        AwarenessNote(
            date="2026-03-08",
            observation="最近连续浏览高信息密度内容。",
            trend="更偏向深度解释而非轻量消遣。",
            emotion_guess="可能处于主动吸收和整理信息的阶段。",
        )
    ]


@pytest.mark.asyncio
async def test_awareness_analyzer_raises_on_invalid_json() -> None:
    from openbiliclaw.soul.awareness_analyzer import (
        AwarenessAnalyzer,
        AwarenessGenerationError,
    )

    analyzer = AwarenessAnalyzer(FakeStructuredService("not-json"))
    with pytest.raises(AwarenessGenerationError, match="invalid JSON"):
        await analyzer.analyze(
            events=[{"event_type": "view", "title": "AI 工具实测"}],
            preference={},
            soul_profile={},
        )


def test_merge_awareness_notes_deduplicates_same_day_observation() -> None:
    from openbiliclaw.soul.awareness_analyzer import AwarenessAnalyzer

    analyzer = AwarenessAnalyzer(FakeStructuredService("[]"))
    existing = [
        AwarenessNote(
            date="2026-03-08",
            observation="最近连续浏览高信息密度内容。",
            trend="更偏向深度解释。",
            emotion_guess="专注",
        )
    ]
    incoming = [
        AwarenessNote(
            date="2026-03-08",
            observation="最近连续浏览高信息密度内容。",
            trend="更偏向深度解释而非轻量消遣。",
            emotion_guess="专注",
        )
    ]

    merged = analyzer.merge_notes(existing, incoming)

    assert len(merged) == 1


@pytest.mark.asyncio
async def test_awareness_analyzer_can_use_unified_service() -> None:
    from openbiliclaw.soul.awareness_analyzer import AwarenessAnalyzer

    service = FakeStructuredService(
        json.dumps(
            [
                {
                    "date": "2026-03-08",
                    "observation": "最近更专注。",
                    "trend": "更偏向深度浏览。",
                    "emotion_guess": "可能在主动整理信息。",
                }
            ],
            ensure_ascii=False,
        )
    )

    notes = await AwarenessAnalyzer(service).analyze(
        events=[{"event_type": "view", "title": "AI 视频"}],
        preference={},
        soul_profile={},
    )

    assert notes[0].observation == "最近更专注。"
    assert service.calls


def test_awareness_analyzer_requires_core_memory_task_service() -> None:
    from openbiliclaw.soul.awareness_analyzer import AwarenessAnalyzer

    with pytest.raises(TypeError, match="complete_structured_task"):
        AwarenessAnalyzer(FakeRegistry("[]"))


# --- _coerce_note_list parser tolerance (v0.3.x awareness resilience) ---


def test_coerce_note_list_wraps_singular_note_dict() -> None:
    """A bare single-note dict (MiMo reasoning-model shape) is wrapped into a list."""
    from openbiliclaw.soul.awareness_analyzer import AwarenessAnalyzer

    payload = {
        "date": "2026-03-08",
        "observation": "最近连续浏览硬核教程。",
        "trend": "更偏向深度解释。",
        "emotion_guess": "专注吸收信息。",
    }
    assert AwarenessAnalyzer._coerce_note_list(payload) == [payload]


def test_coerce_note_list_singular_note_requires_observation() -> None:
    """A dict that lacks the load-bearing `observation` field is NOT a single note."""
    from openbiliclaw.soul.awareness_analyzer import AwarenessAnalyzer

    # Has date/trend/emotion_guess but no observation — worthless, reject.
    payload = {"date": "2026-03-08", "trend": "x", "emotion_guess": "y"}
    assert AwarenessAnalyzer._coerce_note_list(payload) is None


@pytest.mark.parametrize(
    "wrapper_key",
    ["observations", "recent_observations", "latest", "latest_observations"],
)
def test_coerce_note_list_accepts_new_wrapper_keys(wrapper_key: str) -> None:
    """Expanded wrapper-key vocabulary covers shapes MiMo / reasoning models emit."""
    from openbiliclaw.soul.awareness_analyzer import AwarenessAnalyzer

    inner = [
        {
            "date": "2026-03-08",
            "observation": "最近连续浏览硬核教程。",
            "trend": "更偏向深度解释。",
            "emotion_guess": "专注吸收信息。",
        }
    ]
    assert AwarenessAnalyzer._coerce_note_list({wrapper_key: inner}) == inner


def test_coerce_note_list_accepts_dict_wrapped_singular_under_known_key() -> None:
    """A known wrapper key whose value is itself a single note dict is recovered."""
    from openbiliclaw.soul.awareness_analyzer import AwarenessAnalyzer

    inner = {
        "date": "2026-03-08",
        "observation": "最近连续浏览硬核教程。",
        "trend": "更偏向深度解释。",
        "emotion_guess": "专注吸收信息。",
    }
    assert AwarenessAnalyzer._coerce_note_list({"notes": inner}) == [inner]


@pytest.mark.parametrize(
    "garbage",
    [
        "scalar",
        42,
        None,
        {},
        {"unrelated_key": "value"},
        {"results": "not-a-list-or-note"},
    ],
)
def test_coerce_note_list_rejects_garbage_shapes(garbage: object) -> None:
    """Genuinely unrecoverable shapes still return None — no silent fabrication."""
    from openbiliclaw.soul.awareness_analyzer import AwarenessAnalyzer

    assert AwarenessAnalyzer._coerce_note_list(garbage) is None


@pytest.mark.asyncio
async def test_awareness_analyzer_consumes_singular_note_fixture() -> None:
    """End-to-end: a real-shape MiMo singular-note JSON is parsed into one AwarenessNote."""
    from openbiliclaw.soul.awareness_analyzer import AwarenessAnalyzer

    fixture_path = Path(__file__).parent / "fixtures" / "awareness_singular_note.json"
    raw = fixture_path.read_text(encoding="utf-8")

    notes = await AwarenessAnalyzer(FakeStructuredService(raw)).analyze(
        events=[{"event_type": "view", "title": "AI 工具实测"}],
        preference={},
        soul_profile={},
    )

    assert len(notes) == 1
    assert notes[0].observation.startswith("最近连续浏览")
    assert notes[0].date == "2026-03-08"


@pytest.mark.asyncio
async def test_awareness_analyzer_consumes_wrapped_notes_after_shared_parser_migration() -> None:
    """Regression guard: wrapper parsing survives the shared-helper migration."""
    from openbiliclaw.soul.awareness_analyzer import AwarenessAnalyzer

    raw = json.dumps(
        {
            "observations": [
                {
                    "date": "2026-03-08",
                    "observation": "最近连续浏览系统拆解内容。",
                    "trend": "偏好结构化解释。",
                    "emotion_guess": "专注",
                }
            ]
        },
        ensure_ascii=False,
    )

    notes = await AwarenessAnalyzer(FakeStructuredService(raw)).analyze(
        events=[{"event_type": "view", "title": "系统拆解"}],
        preference={},
        soul_profile={},
    )

    assert len(notes) == 1
    assert notes[0].observation == "最近连续浏览系统拆解内容。"
