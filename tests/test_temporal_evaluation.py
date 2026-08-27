from __future__ import annotations

import logging
import math
from datetime import UTC, datetime, timedelta

import pytest

from openbiliclaw.discovery.temporal import (
    TEMPORAL_ELIGIBILITY_POLICY_VERSION,
    TEMPORAL_POLICY_VERSION,
    TemporalEvaluation,
    evaluate_temporal_eligibility,
    ground_temporal_evaluation,
    is_complete_temporal_evidence_marker,
    normalize_temporal_class,
    normalize_temporal_confidence,
    parse_temporal_evaluation,
    schedule_temporal_evaluation,
    temporal_bonus_component,
)

NOW = datetime(2026, 8, 12, 12, tzinfo=UTC)


@pytest.mark.parametrize("value", ["false", "0", "1", 0, 2, False, None])
def test_evidence_complete_marker_rejects_truthy_wire_and_corrupt_values(value: object) -> None:
    assert is_complete_temporal_evidence_marker(value) is False


@pytest.mark.parametrize("value", [True, 1])
def test_evidence_complete_marker_accepts_only_code_and_sqlite_forms(value: object) -> None:
    assert is_complete_temporal_evidence_marker(value) is True


def _wire(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "temporal_class": "current",
        "temporal_confidence": 0.91,
        "temporal_reason": "报名资格依赖活动截止时间",
        "temporal_validity_mode": "explicit_deadline",
        "temporal_valid_until": "2026-08-12T22:00:00+08:00",
        "temporal_scope": "core",
        "temporal_evidence": "报名截止：2026-08-12 22:00 +08:00",
        "temporal_state": "unknown",
    }
    payload.update(overrides)
    return payload


def _scheduled(**overrides: object) -> TemporalEvaluation:
    parsed = parse_temporal_evaluation(_wire(**overrides))
    grounded = ground_temporal_evaluation(
        parsed,
        content_text={"title": "活动报名", "body": str(_wire(**overrides)["temporal_evidence"])},
    )
    return schedule_temporal_evaluation(grounded, evaluated_at=NOW.isoformat())


@pytest.mark.parametrize(
    "value",
    ["breaking", "current", "versioned", "evergreen", "historical", "unknown"],
)
def test_normalize_temporal_class_accepts_contract_values(value: str) -> None:
    assert normalize_temporal_class(value.upper()) == value


@pytest.mark.parametrize("value", [None, 3, "recent", "", "breaking-news"])
def test_normalize_temporal_class_fails_neutral(value: object) -> None:
    assert normalize_temporal_class(value) == "unknown"


@pytest.mark.parametrize("value", [True, "0.8", -0.1, 1.1, math.inf, math.nan, None])
def test_normalize_temporal_confidence_fails_neutral(value: object) -> None:
    assert normalize_temporal_confidence(value) == 0.0


def test_parser_accepts_and_canonicalizes_complete_v2_deadline() -> None:
    parsed = parse_temporal_evaluation(_wire())

    assert parsed == TemporalEvaluation(
        temporal_class="current",
        temporal_confidence=0.91,
        temporal_reason="报名资格依赖活动截止时间",
        temporal_validity_mode="explicit_deadline",
        temporal_valid_until="2026-08-12T14:00:00Z",
        temporal_scope="core",
        temporal_evidence="报名截止：2026-08-12 22:00 +08:00",
        temporal_state="unknown",
        temporal_policy_version=TEMPORAL_POLICY_VERSION,
    )
    assert parsed.evidence_complete is True


def test_parser_accepts_exact_neutral_unknown_wire_without_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="openbiliclaw.discovery.temporal")

    parsed = parse_temporal_evaluation(
        _wire(
            temporal_class="unknown",
            temporal_confidence=0.0,
            temporal_reason="",
            temporal_validity_mode="none",
            temporal_valid_until="",
            temporal_scope="none",
            temporal_evidence="",
            temporal_state="unknown",
        )
    )

    assert parsed == TemporalEvaluation()
    assert parsed.evidence_complete is True
    assert caplog.records == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"temporal_evidence": None},
        {"temporal_confidence": "high"},
        {"temporal_confidence": math.nan},
        {"temporal_class": "recent"},
        {"temporal_state": "ended"},
        {"temporal_reason": ""},
        {"temporal_validity_mode": "deadline"},
        {"temporal_valid_until": "2026-08-12T22:00:00"},
        {"temporal_scope": "whole"},
        {"temporal_scope": "core", "temporal_evidence": ""},
        {"temporal_validity_mode": "freshness_only", "temporal_valid_until": "2026-08-13Z"},
        {"temporal_class": "evergreen", "temporal_validity_mode": "freshness_only"},
        {"temporal_validity_mode": "explicit_deadline", "temporal_state": "active"},
        {
            "temporal_validity_mode": "event_state",
            "temporal_valid_until": "",
            "temporal_state": "superseded",
        },
        {
            "temporal_validity_mode": "version_state",
            "temporal_valid_until": "",
            "temporal_state": "expired",
        },
        {
            "temporal_class": "unknown",
            "temporal_confidence": 0.5,
            "temporal_reason": "不确定",
            "temporal_validity_mode": "none",
            "temporal_valid_until": "",
            "temporal_scope": "none",
            "temporal_evidence": "",
            "temporal_state": "unknown",
        },
    ],
)
def test_parser_atomically_fails_neutral_for_invalid_v2_matrix(
    overrides: dict[str, object],
) -> None:
    parsed = parse_temporal_evaluation(_wire(**overrides))

    assert parsed == TemporalEvaluation()
    assert parsed.evidence_complete is False


def test_parser_requires_every_model_owned_v2_field() -> None:
    payload = _wire()
    del payload["temporal_scope"]

    parsed = parse_temporal_evaluation(payload)

    assert parsed == TemporalEvaluation()
    assert parsed.evidence_complete is False


def test_grounding_preserves_verbatim_deadline_evidence_after_normalization() -> None:
    parsed = parse_temporal_evaluation(_wire())

    grounded = ground_temporal_evaluation(
        parsed,
        content_text={
            "title": "活动报名",
            "body": "  报名截止：2026-08-12   22:00   +08:00  ",
        },
    )

    assert grounded == parsed
    assert grounded.evidence_complete is True


def test_grounding_downgrades_ungrounded_deadline_but_keeps_audit_excerpt() -> None:
    parsed = parse_temporal_evaluation(_wire())

    grounded = ground_temporal_evaluation(parsed, content_text="正文只说近期报名，没有截止时间")

    assert grounded.temporal_validity_mode == "freshness_only"
    assert grounded.temporal_valid_until == ""
    assert grounded.temporal_evidence == parsed.temporal_evidence
    assert grounded.evidence_complete is True


def test_grounding_preserves_verbatim_freshness_only_evidence() -> None:
    parsed = parse_temporal_evaluation(
        _wire(
            temporal_validity_mode="freshness_only",
            temporal_valid_until="",
            temporal_evidence="标题写着今天最新",
        )
    )

    grounded = ground_temporal_evaluation(parsed, content_text="标题写着今天最新")

    assert grounded == parsed


def test_grounding_fails_neutral_for_invented_freshness_only_evidence() -> None:
    parsed = parse_temporal_evaluation(
        _wire(
            temporal_validity_mode="freshness_only",
            temporal_valid_until="",
            temporal_evidence="正文不存在的今日价格",
        )
    )

    grounded = ground_temporal_evaluation(parsed, content_text="普通教程正文")

    assert grounded == TemporalEvaluation()
    assert grounded.evidence_complete is False


@pytest.mark.parametrize(
    "evidence",
    [
        "报名截止：2026-08-12 22:00 +08:00",
        "报名截止：2026/8/12 22:00 +0800",
        "报名截止：2026年8月12日 22:00 北京时间",
        "报名截止：2026-08-12T14:00:00Z",
    ],
)
def test_grounding_accepts_supported_full_date_anchors(evidence: str) -> None:
    parsed = parse_temporal_evaluation(_wire(temporal_evidence=evidence))

    grounded = ground_temporal_evaluation(parsed, content_text=f"活动说明：{evidence}")

    assert grounded.temporal_validity_mode == "explicit_deadline"
    assert grounded.temporal_valid_until == "2026-08-12T14:00:00Z"


@pytest.mark.parametrize(
    "evidence",
    [
        "报名截止：2026-08-12",
        "报名截止：2026-08-12 22:00",
        "报名截止：2026年8月12日 22:00",
    ],
)
def test_grounding_rejects_deadline_without_explicit_time_and_timezone(evidence: str) -> None:
    parsed = parse_temporal_evaluation(_wire(temporal_evidence=evidence))

    grounded = ground_temporal_evaluation(parsed, content_text=evidence)

    assert grounded.temporal_validity_mode == "freshness_only"
    assert grounded.temporal_valid_until == ""


def test_grounding_rejects_timestamp_that_is_not_same_instant_as_valid_until() -> None:
    evidence = "报名截止：2026-08-12 22:00 +08:00"
    parsed = parse_temporal_evaluation(
        _wire(
            temporal_evidence=evidence,
            temporal_valid_until="2026-08-12T01:00:00Z",
        )
    )

    grounded = ground_temporal_evaluation(parsed, content_text=evidence)

    assert grounded.temporal_validity_mode == "freshness_only"
    assert grounded.temporal_valid_until == ""


def test_grounding_rejects_invented_deadline_even_when_generic_evidence_is_verbatim() -> None:
    parsed = parse_temporal_evaluation(
        _wire(temporal_evidence="限时活动", temporal_valid_until="2030-01-01T00:00:00Z")
    )

    grounded = ground_temporal_evaluation(parsed, content_text="这是一个限时活动，欢迎参加")

    assert grounded.temporal_validity_mode == "freshness_only"
    assert grounded.temporal_valid_until == ""


@pytest.mark.parametrize(
    ("temporal_class", "mode", "state", "evidence"),
    [
        ("current", "event_state", "expired", "赛事已经结束"),
        ("versioned", "version_state", "superseded", "3.8 已停止支持"),
    ],
)
def test_grounding_preserves_verbatim_terminal_state_evidence(
    temporal_class: str,
    mode: str,
    state: str,
    evidence: str,
) -> None:
    parsed = parse_temporal_evaluation(
        _wire(
            temporal_class=temporal_class,
            temporal_validity_mode=mode,
            temporal_valid_until="",
            temporal_state=state,
            temporal_evidence=evidence,
        )
    )

    grounded = ground_temporal_evaluation(parsed, content_text=f"正文明确说明：{evidence}")

    assert grounded.temporal_validity_mode == mode
    assert grounded.temporal_state == state


def test_grounding_downgrades_ungrounded_terminal_state_claim() -> None:
    parsed = parse_temporal_evaluation(
        _wire(
            temporal_validity_mode="event_state",
            temporal_valid_until="",
            temporal_state="expired",
            temporal_evidence="活动已经结束",
        )
    )

    grounded = ground_temporal_evaluation(parsed, content_text="正文只说活动正在报名")

    assert grounded.temporal_validity_mode == "freshness_only"
    assert grounded.temporal_state == "unknown"
    assert grounded.temporal_valid_until == ""


@pytest.mark.parametrize(
    ("temporal_class", "mode", "state", "evidence"),
    [
        ("current", "event_state", "expired", "赛事尚未结束"),
        ("current", "event_state", "expired", "活动仍在进行"),
        ("versioned", "version_state", "superseded", "3.8 未被替代"),
        ("versioned", "version_state", "superseded", "3.8 当前仍受支持"),
    ],
)
def test_grounding_downgrades_contradictory_terminal_state_evidence(
    temporal_class: str,
    mode: str,
    state: str,
    evidence: str,
) -> None:
    parsed = parse_temporal_evaluation(
        _wire(
            temporal_class=temporal_class,
            temporal_validity_mode=mode,
            temporal_valid_until="",
            temporal_state=state,
            temporal_evidence=evidence,
        )
    )

    grounded = ground_temporal_evaluation(parsed, content_text=evidence)

    assert grounded.temporal_validity_mode == "freshness_only"
    assert grounded.temporal_state == "unknown"


@pytest.mark.parametrize(
    ("temporal_class", "mode", "evidence"),
    [
        ("current", "event_state", "活动仍在进行，报名入口仍然开放"),
        ("versioned", "version_state", "Temporal V2 仍受支持"),
    ],
)
def test_grounding_preserves_affirmative_active_state_evidence(
    temporal_class: str,
    mode: str,
    evidence: str,
) -> None:
    parsed = parse_temporal_evaluation(
        _wire(
            temporal_class=temporal_class,
            temporal_validity_mode=mode,
            temporal_valid_until="",
            temporal_state="active",
            temporal_evidence=evidence,
        )
    )

    grounded = ground_temporal_evaluation(parsed, content_text=evidence)

    assert grounded.temporal_validity_mode == mode
    assert grounded.temporal_state == "active"


@pytest.mark.parametrize(
    ("temporal_class", "mode", "evidence"),
    [
        ("current", "event_state", "如果报名状态变化，请重新查看活动页面"),
        ("current", "event_state", "如果活动仍在进行，就继续接受报名"),
        (
            "versioned",
            "version_state",
            "如果支持版本发生变化，本文的迁移命令和字段契约就必须重新核验",
        ),
        ("versioned", "version_state", "如果 Temporal V2 当前仍受支持，就继续使用"),
        ("versioned", "version_state", "Temporal V2 may still be supported"),
    ],
)
def test_grounding_downgrades_nonaffirmative_active_state_evidence(
    temporal_class: str,
    mode: str,
    evidence: str,
) -> None:
    parsed = parse_temporal_evaluation(
        _wire(
            temporal_class=temporal_class,
            temporal_validity_mode=mode,
            temporal_valid_until="",
            temporal_state="active",
            temporal_evidence=evidence,
        )
    )

    content_text = evidence
    if mode == "version_state":
        # A valid neighbouring sentence cannot rescue a wrongly quoted excerpt.
        content_text += "。Temporal V2 仍是当前受支持版本"
    grounded = ground_temporal_evaluation(parsed, content_text=content_text)

    assert grounded.temporal_validity_mode == "freshness_only"
    assert grounded.temporal_state == "unknown"


@pytest.mark.parametrize(
    ("temporal_class", "mode", "expected"),
    [
        ("breaking", "freshness_only", "2026-08-13T12:00:00Z"),
        ("current", "event_state", "2026-08-26T12:00:00Z"),
        ("versioned", "version_state", "2026-10-11T12:00:00Z"),
    ],
)
def test_schedule_uses_class_review_intervals(
    temporal_class: str,
    mode: str,
    expected: str,
) -> None:
    evaluation = parse_temporal_evaluation(
        _wire(
            temporal_class=temporal_class,
            temporal_validity_mode=mode,
            temporal_valid_until="",
            temporal_state=("active" if mode in {"event_state", "version_state"} else "unknown"),
        )
    )

    scheduled = schedule_temporal_evaluation(evaluation, evaluated_at=NOW.isoformat())

    assert scheduled.temporal_evaluated_at == "2026-08-12T12:00:00Z"
    assert scheduled.temporal_next_review_at == expected


def test_schedule_uses_core_deadline_as_next_review_boundary() -> None:
    scheduled = _scheduled()

    assert scheduled.temporal_next_review_at == scheduled.temporal_valid_until
    assert scheduled.temporal_next_review_at == "2026-08-12T14:00:00Z"


@pytest.mark.parametrize(
    "overrides",
    [
        {"temporal_scope": "hook"},
        {
            "temporal_class": "evergreen",
            "temporal_validity_mode": "none",
            "temporal_valid_until": "",
            "temporal_scope": "none",
            "temporal_evidence": "",
        },
        {
            "temporal_class": "historical",
            "temporal_validity_mode": "none",
            "temporal_valid_until": "",
            "temporal_scope": "none",
            "temporal_evidence": "",
        },
    ],
)
def test_schedule_does_not_review_hook_or_durable_content(overrides: dict[str, object]) -> None:
    evaluation = parse_temporal_evaluation(_wire(**overrides))

    scheduled = schedule_temporal_evaluation(evaluation, evaluated_at=NOW.isoformat())

    assert scheduled.temporal_next_review_at == ""
    assert scheduled.temporal_evaluated_at == "2026-08-12T12:00:00Z"


def test_evergreen_latest_hook_is_recorded_but_never_held() -> None:
    evaluation = parse_temporal_evaluation(
        _wire(
            temporal_class="evergreen",
            temporal_reason="标题强调最新，但正文是通用教程",
            temporal_validity_mode="freshness_only",
            temporal_valid_until="",
            temporal_scope="hook",
            temporal_evidence="2025 最新",
        )
    )
    scheduled = schedule_temporal_evaluation(evaluation, evaluated_at=NOW.isoformat())

    decision = _decision(scheduled, now=NOW + timedelta(days=3650))

    assert scheduled.evidence_complete is True
    assert scheduled.temporal_next_review_at == ""
    assert decision.disposition == "eligible"


def test_schedule_invalid_clock_disables_policy_without_erasing_semantics() -> None:
    evaluation = parse_temporal_evaluation(_wire())

    scheduled = schedule_temporal_evaluation(evaluation, evaluated_at="not-a-date")

    assert scheduled.temporal_class == "current"
    assert scheduled.temporal_evaluated_at == ""
    assert scheduled.temporal_next_review_at == ""
    assert scheduled.evidence_complete is False


def _decision(evaluation: TemporalEvaluation, *, now: datetime) -> object:
    return evaluate_temporal_eligibility(
        temporal_class=evaluation.temporal_class,
        temporal_confidence=evaluation.temporal_confidence,
        published_at="2000-01-01T00:00:00Z",
        temporal_validity_mode=evaluation.temporal_validity_mode,
        temporal_valid_until=evaluation.temporal_valid_until,
        temporal_scope=evaluation.temporal_scope,
        temporal_evidence=evaluation.temporal_evidence,
        temporal_state=evaluation.temporal_state,
        temporal_next_review_at=evaluation.temporal_next_review_at,
        temporal_evaluated_at=evaluation.temporal_evaluated_at,
        temporal_policy_version=evaluation.temporal_policy_version,
        evidence_complete=evaluation.evidence_complete,
        now=now,
    )


def test_v2_deadline_expires_at_exact_boundary() -> None:
    scheduled = _scheduled()
    deadline = datetime(2026, 8, 12, 14, tzinfo=UTC)

    inside = _decision(scheduled, now=deadline - timedelta(microseconds=1))
    at_boundary = _decision(scheduled, now=deadline)

    assert inside.disposition == "eligible"
    assert inside.eligible is True
    assert at_boundary.disposition == "expired"
    assert at_boundary.eligible is False
    assert at_boundary.hard_expired is True
    assert at_boundary.needs_review is False
    assert at_boundary.trigger_at == "2026-08-12T14:00:00Z"
    assert at_boundary.policy_version == TEMPORAL_ELIGIBILITY_POLICY_VERSION
    assert at_boundary.reason.startswith("temporal_expired:class=current")
    assert at_boundary.rejection_reason == at_boundary.reason


def test_v2_non_deadline_becomes_review_due_at_exact_boundary() -> None:
    evaluation = parse_temporal_evaluation(
        _wire(
            temporal_validity_mode="event_state",
            temporal_valid_until="",
            temporal_state="active",
            temporal_evidence="活动仍在进行，报名入口仍然开放",
        )
    )
    scheduled = schedule_temporal_evaluation(evaluation, evaluated_at=NOW.isoformat())
    review_at = NOW + timedelta(days=14)

    inside = _decision(scheduled, now=review_at - timedelta(microseconds=1))
    at_boundary = _decision(scheduled, now=review_at)

    assert inside.disposition == "eligible"
    assert at_boundary.disposition == "review_due"
    assert at_boundary.eligible is False
    assert at_boundary.hard_expired is False
    assert at_boundary.needs_review is True
    assert at_boundary.trigger_at == "2026-08-26T12:00:00Z"
    assert at_boundary.reason.startswith("temporal_review_due:class=current")


@pytest.mark.parametrize(
    ("temporal_class", "mode", "state", "evidence"),
    [
        ("current", "event_state", "expired", "赛事已经结束"),
        ("versioned", "version_state", "superseded", "3.8 已停止支持"),
    ],
)
def test_grounded_terminal_event_or_version_state_hard_expires(
    temporal_class: str,
    mode: str,
    state: str,
    evidence: str,
) -> None:
    parsed = parse_temporal_evaluation(
        _wire(
            temporal_class=temporal_class,
            temporal_validity_mode=mode,
            temporal_valid_until="",
            temporal_state=state,
            temporal_evidence=evidence,
        )
    )
    grounded = ground_temporal_evaluation(parsed, content_text=evidence)
    scheduled = schedule_temporal_evaluation(grounded, evaluated_at=NOW.isoformat())

    decision = _decision(scheduled, now=NOW)

    assert scheduled.temporal_next_review_at == ""
    assert decision.disposition == "expired"
    assert decision.hard_expired is True
    assert decision.temporal_state == state
    assert f"state={state}" in decision.reason


@pytest.mark.parametrize(
    ("evidence", "valid_until"),
    [
        ("报名截止：2026-08-12", "2026-08-12T14:00:00Z"),
        ("报名截止：2026-08-12 22:00", "2026-08-12T01:00:00Z"),
        ("报名截止：2026-08-12 22:00 +08:00", "2026-08-12T01:00:00Z"),
    ],
)
def test_evaluator_does_not_trust_ambiguous_or_mismatched_deadline_evidence(
    evidence: str,
    valid_until: str,
) -> None:
    evaluation = parse_temporal_evaluation(
        _wire(
            temporal_evidence=evidence,
            temporal_valid_until=valid_until,
        )
    )
    scheduled = schedule_temporal_evaluation(evaluation, evaluated_at=NOW.isoformat())

    decision = _decision(scheduled, now=NOW + timedelta(days=1))

    assert decision.disposition == "eligible"
    assert decision.hard_expired is False


@pytest.mark.parametrize(
    ("temporal_class", "mode", "state", "evidence"),
    [
        ("current", "event_state", "expired", "赛事尚未结束"),
        ("current", "event_state", "expired", "活动仍在进行"),
        ("versioned", "version_state", "superseded", "3.8 未被替代"),
        ("versioned", "version_state", "superseded", "3.8 当前仍受支持"),
    ],
)
def test_evaluator_does_not_trust_contradictory_terminal_evidence(
    temporal_class: str,
    mode: str,
    state: str,
    evidence: str,
) -> None:
    evaluation = parse_temporal_evaluation(
        _wire(
            temporal_class=temporal_class,
            temporal_validity_mode=mode,
            temporal_valid_until="",
            temporal_state=state,
            temporal_evidence=evidence,
        )
    )
    scheduled = schedule_temporal_evaluation(evaluation, evaluated_at=NOW.isoformat())

    decision = _decision(scheduled, now=NOW + timedelta(days=365))

    assert decision.disposition == "eligible"
    assert decision.hard_expired is False


def test_evaluator_does_not_schedule_review_from_conditional_active_evidence() -> None:
    evidence = "如果支持版本发生变化，本文的迁移命令和字段契约就必须重新核验"
    evaluation = parse_temporal_evaluation(
        _wire(
            temporal_class="versioned",
            temporal_validity_mode="version_state",
            temporal_valid_until="",
            temporal_state="active",
            temporal_evidence=evidence,
        )
    )
    scheduled = schedule_temporal_evaluation(evaluation, evaluated_at=NOW.isoformat())

    decision = _decision(scheduled, now=NOW + timedelta(days=365))

    assert decision.disposition == "eligible"
    assert decision.needs_review is False


def test_grounding_downgrades_conditional_terminal_state_evidence() -> None:
    evidence = "如果 V1 已停止支持，就迁移到 V2"
    parsed = parse_temporal_evaluation(
        _wire(
            temporal_class="versioned",
            temporal_validity_mode="version_state",
            temporal_valid_until="",
            temporal_state="superseded",
            temporal_evidence=evidence,
        )
    )

    grounded = ground_temporal_evaluation(parsed, content_text=evidence)
    scheduled = schedule_temporal_evaluation(grounded, evaluated_at=NOW.isoformat())
    decision = _decision(scheduled, now=NOW + timedelta(days=365))

    assert grounded.temporal_validity_mode == "freshness_only"
    assert grounded.temporal_state == "unknown"
    assert decision.disposition == "review_due"
    assert decision.hard_expired is False


@pytest.mark.parametrize(
    "changes",
    [
        {"temporal_confidence": 0.799999},
        {"temporal_scope": "hook"},
        {"evidence_complete": False},
        {"temporal_evidence": ""},
        {"temporal_valid_until": "invalid"},
        {"temporal_evaluated_at": ""},
        {"temporal_state": "active"},
    ],
)
def test_v2_uncertain_or_malformed_evidence_fails_eligible(changes: dict[str, object]) -> None:
    scheduled = _scheduled()
    kwargs: dict[str, object] = {
        "temporal_class": scheduled.temporal_class,
        "temporal_confidence": scheduled.temporal_confidence,
        "published_at": "2000-01-01T00:00:00Z",
        "temporal_validity_mode": scheduled.temporal_validity_mode,
        "temporal_valid_until": scheduled.temporal_valid_until,
        "temporal_scope": scheduled.temporal_scope,
        "temporal_evidence": scheduled.temporal_evidence,
        "temporal_state": scheduled.temporal_state,
        "temporal_next_review_at": scheduled.temporal_next_review_at,
        "temporal_evaluated_at": scheduled.temporal_evaluated_at,
        "temporal_policy_version": scheduled.temporal_policy_version,
        "evidence_complete": scheduled.evidence_complete,
        "now": NOW + timedelta(days=365),
    }
    kwargs.update(changes)

    decision = evaluate_temporal_eligibility(**kwargs)

    assert decision.disposition == "eligible"
    assert decision.hard_expired is False
    assert decision.needs_review is False


@pytest.mark.parametrize(
    ("temporal_class", "review_days"),
    [("breaking", 3), ("current", 60), ("versioned", 120)],
)
def test_legacy_v1_age_window_now_schedules_review_instead_of_expiry(
    temporal_class: str,
    review_days: int,
) -> None:
    published = NOW - timedelta(days=review_days)

    decision = evaluate_temporal_eligibility(
        temporal_class=temporal_class,
        temporal_confidence=0.8,
        published_at=published.isoformat(),
        temporal_validity_mode="none",
        temporal_scope="none",
        temporal_policy_version="v1",
        evidence_complete=False,
        now=NOW,
    )

    assert decision.disposition == "review_due"
    assert decision.eligible is False
    assert decision.needs_review is True
    assert decision.hard_expired is False
    assert decision.age_days == float(review_days)
    assert decision.ttl_days == float(review_days)
    assert decision.trigger_at == _iso(published + timedelta(days=review_days))


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


@pytest.mark.parametrize(
    "published_at",
    ["", "not-a-date", "2026-01-01T00:00:00", "2026-08-13T00:00:00Z"],
)
def test_legacy_review_fails_open_for_untrusted_publication_time(published_at: str) -> None:
    decision = evaluate_temporal_eligibility(
        temporal_class="breaking",
        temporal_confidence=1.0,
        published_at=published_at,
        temporal_policy_version="v1",
        now=NOW,
    )

    assert decision.disposition == "eligible"
    assert decision.age_days is None


def test_temporal_ranking_bonus_is_unchanged_by_v2_policy() -> None:
    assert (
        temporal_bonus_component(
            temporal_class="breaking",
            temporal_confidence=0.8,
            published_at=(NOW + timedelta(minutes=5)).isoformat(),
            now=NOW,
        )
        == 0.85
    )
