"""Prompt-level guards for event provenance.

A prompt that shows 40 "关注了《X》" lines plus one shared ``created_at`` is an
invitation to invent "followed 40 creators in one day". These tests pin the
prompt shape: inventory arrives as one row, ingest time never arrives at all.
"""

from __future__ import annotations

from openbiliclaw.llm.prompts import (
    _INSIGHT_SYSTEM_PROMPT,
    _PREFERENCE_ANALYSIS_SYSTEM_PROMPT,
    build_awareness_prompt,
    build_preference_analysis_prompt,
    build_soul_profile_prompt,
)


def _snapshot_follow(name: str, mtime: int) -> dict[str, object]:
    return {
        "event_type": "follow",
        "title": name,
        "created_at": "2026-10-27 04:56:39",
        "metadata": {
            "up_name": name,
            "snapshot": True,
            "timestamp": mtime,
            "source": "account_sync",
        },
    }


def test_preference_prompt_collapses_a_follow_import() -> None:
    events = [_snapshot_follow(f"UP{i}", 1_584_271_945 + i * 86_400) for i in range(40)]

    messages = build_preference_analysis_prompt(events=events, existing_preference={})
    user_prompt = messages[1]["content"]

    assert user_prompt.count("账号导入存量") == 1
    assert "关注了 40 位创作者" in user_prompt
    assert "UP0" in user_prompt  # names survive for creator affinity
    assert "created_at" not in user_prompt
    assert "2026-10-27 04:56:39" not in user_prompt


def test_preference_prompt_marks_undated_rows_as_time_unknown() -> None:
    events = [
        {
            "event_type": "view",
            "title": "某视频",
            "created_at": "2026-08-01 02:28:16",
            "metadata": {"bvid": "BV1"},
        }
    ]

    messages = build_preference_analysis_prompt(events=events, existing_preference={})
    user_prompt = messages[1]["content"]

    assert '"time_quality": "ingest"' in user_prompt
    assert "created_at" not in user_prompt


def test_compact_view_also_collapses_and_redacts() -> None:
    events = [_snapshot_follow(f"UP{i}", 1_584_271_945 + i * 86_400) for i in range(5)]

    messages = build_preference_analysis_prompt(
        events=events,
        existing_preference={},
        input_view="compact-v1",
    )
    user_prompt = messages[1]["content"]

    assert user_prompt.count("账号导入存量") == 1
    assert "created_at" not in user_prompt


def test_awareness_prompt_collapses_a_follow_import() -> None:
    events = [_snapshot_follow(f"UP{i}", 1_584_271_945 + i * 86_400) for i in range(12)]

    messages = build_awareness_prompt(
        events=events,
        preference_summary={},
        soul_profile={},
    )
    user_prompt = messages[1]["content"]

    assert user_prompt.count("账号导入存量") == 1
    assert "created_at" not in user_prompt


def test_preference_system_prompt_states_the_time_contract() -> None:
    assert "时间字段契约" in _PREFERENCE_ANALYSIS_SYSTEM_PROMPT
    assert "存量不是行为" in _PREFERENCE_ANALYSIS_SYSTEM_PROMPT
    assert "time_quality" in _PREFERENCE_ANALYSIS_SYSTEM_PROMPT


def test_soul_profile_prompt_forbids_inventory_as_extraversion_evidence() -> None:
    messages = build_soul_profile_prompt(
        preference_summary={},
        history_summary={},
        tone_profile=None,
        source_platform_mix={},
    )
    system_prompt = messages[0]["content"]

    assert "EI 只能由用户自己的表达支撑" in system_prompt
    assert "库存和配置" in system_prompt
    assert "0.50~0.58" in system_prompt


def test_insight_prompt_forbids_mbti_as_evidence() -> None:
    assert "MBTI 不是证据" in _INSIGHT_SYSTEM_PROMPT
