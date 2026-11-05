"""The one-off repair must be conservative, reversible and idempotent.

It only touches facts that are provably contaminated: MBTI cited as evidence,
a 「同一天」 claim contradicted by the cited events' real interaction times, and
dialogue topics that are compound product-operations phrases.
"""

from __future__ import annotations

from typing import Any

from openbiliclaw.soul.provenance_repair import plan_provenance_repair

_DAY = 86_400


def _plan(
    *,
    awareness: dict[str, Any] | None = None,
    insights: dict[str, Any] | None = None,
    preference: dict[str, Any] | None = None,
    event_times: dict[int, float] | None = None,
    mbti_type: str = "ENTP",
):
    return plan_provenance_repair(
        awareness=awareness or {"notes": []},
        insights=insights or {"hypotheses": []},
        preference=preference or {"interests": [], "disliked_topics": []},
        event_times=event_times or {},
        mbti_type=mbti_type,
        now_iso="2026-10-27T00:00:00+00:00",
    )


def test_mbti_evidence_is_removed_and_the_rest_survives() -> None:
    insights = {
        "hypotheses": [
            {
                "hypothesis": "用户可能在交叉领域找灵感",
                "evidence": [
                    "ENTP类型具有联想综合的认知风格，跨领域内容可能激发新视角。",
                    "近期连续观看强化学习与游戏设计内容。",
                ],
                "confidence": 0.62,
                "validated": False,
            }
        ]
    }

    plan = _plan(insights=insights)

    hypothesis = plan.insights["hypotheses"][0]
    assert hypothesis["evidence"] == ["近期连续观看强化学习与游戏设计内容。"]
    assert plan.summary["insight_evidence_removed"] == 1
    assert plan.summary["insights_repaired"] == 1
    assert hypothesis["provenance_repaired_at"] == "2026-10-27T00:00:00+00:00"


def test_hypothesis_resting_only_on_mbti_is_invalidated() -> None:
    insights = {
        "hypotheses": [
            {
                "hypothesis": "用户喜欢跨领域",
                "evidence": ["用户是 ENTP，天生喜欢跨领域。"],
                "confidence": 0.7,
                "validated": True,
            }
        ]
    }

    plan = _plan(insights=insights)

    hypothesis = plan.insights["hypotheses"][0]
    assert hypothesis["evidence"] == []
    assert hypothesis["validated"] is False
    assert hypothesis["confidence"] <= 0.2
    assert "MBTI" in hypothesis["invalidated_reason"]
    assert plan.summary["insights_invalidated"] == 1


def test_clean_hypotheses_are_untouched() -> None:
    insights = {
        "hypotheses": [
            {"hypothesis": "喜欢纪录片", "evidence": ["连续看了三部纪录片。"], "confidence": 0.6}
        ]
    }

    plan = _plan(insights=insights)

    assert plan.insights["hypotheses"][0] == insights["hypotheses"][0]
    assert not plan.changed


def _note(observation: str, ids: list[int]) -> dict[str, Any]:
    return {
        "date": "2026-08-05",
        "observation": observation,
        "trend": "",
        "emotion_guess": "",
        "source_event_ids": ids,
    }


def test_same_day_claim_contradicted_by_real_times_is_quarantined() -> None:
    awareness = {
        "notes": [
            _note("同一天内连续浏览了 AI、美食、动漫等多个领域。", [1, 2, 3]),
            _note("最近在追一部番剧。", [4]),
        ]
    }
    event_times = {1: 1_780_000_000.0, 2: 1_780_000_000.0 + 5 * _DAY, 3: 1_780_000_000.0 + 9 * _DAY}

    plan = _plan(awareness=awareness, event_times=event_times)

    assert len(plan.awareness["notes"]) == 1
    assert plan.awareness["notes"][0]["observation"] == "最近在追一部番剧。"
    quarantined = plan.awareness["quarantined_notes"]
    assert len(quarantined) == 1
    assert "contradicted" in quarantined[0]["quarantined_reason"]
    assert plan.summary["awareness_quarantined"] == 1


def test_same_day_claim_verified_within_36h_is_kept() -> None:
    awareness = {"notes": [_note("同一天内连续看了三个视频。", [1, 2, 3])]}
    event_times = {
        1: 1_780_000_000.0,
        2: 1_780_000_000.0 + 1800,
        3: 1_780_000_000.0 + 3600,
    }

    plan = _plan(awareness=awareness, event_times=event_times)

    assert len(plan.awareness["notes"]) == 1
    assert plan.summary["awareness_quarantined"] == 0


def test_unverifiable_note_is_kept_but_counted() -> None:
    """No real times ⇒ cannot prove it wrong; never rewrite history."""
    awareness = {"notes": [_note("同一天刷了很多领域。", [1, 2])]}

    plan = _plan(awareness=awareness)

    assert len(plan.awareness["notes"]) == 1
    assert plan.summary["awareness_unverified"] == 1


def test_note_without_temporal_claim_is_untouched_even_if_events_span_days() -> None:
    awareness = {"notes": [_note("最近在学做饭。", [1, 2])]}
    event_times = {1: 1_780_000_000.0, 2: 1_780_000_000.0 + 30 * _DAY}

    plan = _plan(awareness=awareness, event_times=event_times)

    assert len(plan.awareness["notes"]) == 1
    assert not plan.changed


def test_dialogue_meta_usage_interests_are_archived_not_deleted() -> None:
    preference = {
        "interests": [
            {"name": "系统配置与测试", "source": "dialogue", "weight": 0.85},
            {"name": "软件测试", "source": "对话洞察", "weight": 0.68},
            {"name": "纪录片", "source": "view", "weight": 0.7},
            {"name": "系统配置与测试", "source": "view", "weight": 0.5},
        ],
        "disliked_topics": ["测试后配置未干净还原", "标题党"],
    }

    plan = _plan(preference=preference)

    names = [tag["name"] for tag in plan.preference["interests"]]
    assert names == ["软件测试", "纪录片", "系统配置与测试"]
    archived = plan.preference["archived_interests"]
    assert [tag["name"] for tag in archived] == ["系统配置与测试"]
    assert "meta-usage" in archived[0]["archived_reason"]
    assert plan.summary["interests_archived"] == 1
    assert plan.summary["interests_review"] == 1  # 软件测试: single operational word
    assert plan.preference["disliked_topics"] == ["标题党"]
    assert plan.preference["archived_disliked_topics"] == ["测试后配置未干净还原"]


def test_repair_is_idempotent() -> None:
    preference = {
        "interests": [{"name": "系统配置与测试", "source": "dialogue", "weight": 0.85}],
        "disliked_topics": ["测试后配置未干净还原"],
    }
    awareness = {"notes": [_note("同一天刷了很多领域。", [1, 2])]}
    insights = {
        "hypotheses": [
            {"hypothesis": "跨领域", "evidence": ["ENTP 型都喜欢跨领域。"], "confidence": 0.6}
        ]
    }
    event_times = {1: 1_780_000_000.0, 2: 1_780_000_000.0 + 10 * _DAY}

    first = _plan(
        awareness=awareness,
        insights=insights,
        preference=preference,
        event_times=event_times,
    )
    second = plan_provenance_repair(
        awareness=first.awareness,
        insights=first.insights,
        preference=first.preference,
        event_times=event_times,
        mbti_type="ENTP",
        now_iso="2026-10-27T00:00:00+00:00",
    )

    assert not second.changed
    assert second.awareness == first.awareness
    assert second.insights == first.insights
    assert second.preference == first.preference


def test_mbti_evidence_is_detected_next_to_chinese_text() -> None:
    """`\\b` fails before CJK; the label must still be recognised.

    The real corpus reads "ENTP类型具有联想综合的认知风格" while the current type
    is ISTP, so the exact-type shortcut cannot rescue the match.
    """
    insights = {
        "hypotheses": [
            {
                "hypothesis": "用户ENTP式的联想",
                "evidence": ["ENTP类型具有联想综合的认知风格，跨领域内容可能激发新视角。"],
                "confidence": 0.6,
            }
        ]
    }

    plan = _plan(insights=insights, mbti_type="ISTP")

    assert plan.summary["insight_evidence_removed"] == 1
    assert plan.summary["insights_invalidated"] == 1


def test_approximate_event_ids_never_cause_quarantine() -> None:
    """Approximate ids may be a superset; their span cannot refute the claim."""
    note = _note("同一天内刷了很多领域。", [1, 2])
    note["source_event_ids_approximate"] = True
    event_times = {1: 1_780_000_000.0, 2: 1_780_000_000.0 + 20 * _DAY}

    plan = _plan(awareness={"notes": [note]}, event_times=event_times)

    assert len(plan.awareness["notes"]) == 1
    assert plan.summary["awareness_quarantined"] == 0


class _FakeLayer:
    """Mirrors MemoryLayer: `data` is a read-only property, `save()` persists."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._data = payload
        self.saved = 0

    @property
    def data(self) -> dict[str, Any]:
        return self._data

    def save(self) -> None:
        self.saved += 1


def test_replace_layer_payload_works_against_a_read_only_data_property() -> None:
    """Assigning `layer.data` raises on the real class; clear+update does not."""
    from openbiliclaw.soul.provenance_repair import replace_layer_payload

    layer = _FakeLayer({"interests": ["old"], "keep": 1})

    replace_layer_payload(layer, {"interests": ["new"]})

    assert layer.data == {"interests": ["new"]}
    assert layer.saved == 1


def test_mbti_evidence_without_the_word_typename_is_detected() -> None:
    """The real corpus also says "ENTP认知风格…" with no 型/MBTI suffix."""
    insights = {
        "hypotheses": [
            {
                "hypothesis": "跨领域联想",
                "evidence": ["ENTP认知风格擅长联想综合，跨领域内容可能激发新视角。"],
                "confidence": 0.6,
            }
        ]
    }

    plan = _plan(insights=insights, mbti_type="ISTP")

    assert plan.summary["insight_evidence_removed"] == 1
