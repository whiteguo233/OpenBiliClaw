"""Temporal-semantics contract for content evaluation.

The evaluation model classifies *why* a candidate's value may expire.  This
module validates that wire contract and owns the deterministic policy shared
by discovery admission, recommendation ranking, and final serving.
"""

from __future__ import annotations

import logging
import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from types import MappingProxyType

logger = logging.getLogger(__name__)

TEMPORAL_POLICY_VERSION = "v2"
TEMPORAL_ELIGIBILITY_POLICY_VERSION = "temporal-eligibility-v2"
TEMPORAL_CLASSES = frozenset(
    {
        "breaking",
        "current",
        "versioned",
        "evergreen",
        "historical",
        "unknown",
    }
)
TEMPORAL_VALIDITY_MODES = frozenset(
    {
        "none",
        "explicit_deadline",
        "event_state",
        "version_state",
        "freshness_only",
    }
)
TEMPORAL_SCOPES = frozenset({"none", "core", "hook"})
TEMPORAL_DISPOSITIONS = frozenset({"eligible", "review_due", "expired"})
TEMPORAL_STATES = frozenset({"unknown", "active", "expired", "superseded"})

# The confidence bands and class curves were calibrated against the 2026-08
# historical candidate/discovery-pool replay.  A hard eligibility gate is more
# conservative than ranking: only the full-confidence band can reject content.
TEMPORAL_CONFIDENCE_FULL = 0.80
TEMPORAL_CONFIDENCE_HALF = 0.60
PUBLICATION_CLOCK_SKEW_TOLERANCE = timedelta(minutes=5)
TEMPORAL_REVIEW_INTERVALS: MappingProxyType[str, timedelta] = MappingProxyType(
    {
        "breaking": timedelta(days=1),
        "current": timedelta(days=14),
        # 2026-08: synced to the versioned bonus half-life (60d) so the review
        # clock re-evaluates version-anchored content at the same cadence its
        # freshness bonus decays, instead of the old 120d mismatch.
        "versioned": timedelta(days=60),
    }
)
_ALLOWED_VALIDITY_MODES_BY_CLASS: MappingProxyType[str, frozenset[str]] = MappingProxyType(
    {
        "breaking": frozenset({"explicit_deadline", "event_state", "freshness_only"}),
        "current": frozenset(
            {"explicit_deadline", "event_state", "version_state", "freshness_only"}
        ),
        "versioned": frozenset({"explicit_deadline", "version_state", "freshness_only"}),
        "evergreen": frozenset({"none", "explicit_deadline", "freshness_only"}),
        "historical": frozenset({"none", "explicit_deadline", "freshness_only"}),
        "unknown": frozenset({"none"}),
    }
)


def is_complete_temporal_evidence_marker(value: object) -> bool:
    """Accept only the code-owned boolean or its exact SQLite integer form."""

    return value is True or (type(value) is int and value == 1)


@dataclass(frozen=True)
class TemporalClassPolicy:
    """Deterministic ranking and eligibility policy for one temporal class."""

    bonus_half_life_days: float
    bonus_cap: float
    admission_ttl_days: float | None = None


# The old 3/60 day windows remain only as legacy review schedules.  They no
# longer assert that content expired.  V2 hard expiry requires grounded,
# high-confidence, core-value evidence with an explicit deadline.
#
# 2026-08 calibration: versioned half-life shortened 120d -> 60d because
# version-anchored tech content (AI tools, frameworks, hardware, games)
# loses value faster than the old curve implied; its 120d admission TTL is
# a deterministic eligibility floor between current (60d) and permanent.
TEMPORAL_CLASS_POLICIES: MappingProxyType[str, TemporalClassPolicy] = MappingProxyType(
    {
        "breaking": TemporalClassPolicy(1.0, 0.85, 3.0),
        "current": TemporalClassPolicy(14.0, 0.60, 60.0),
        "versioned": TemporalClassPolicy(60.0, 0.30, 120.0),
    }
)


@dataclass(frozen=True)
class TemporalEvaluation:
    """Validated temporal metadata returned by the evaluation agent."""

    temporal_class: str = "unknown"
    temporal_confidence: float = 0.0
    temporal_reason: str = ""
    temporal_validity_mode: str = "none"
    temporal_valid_until: str = ""
    temporal_scope: str = "none"
    temporal_evidence: str = ""
    temporal_state: str = "unknown"
    temporal_next_review_at: str = ""
    temporal_evaluated_at: str = ""
    temporal_policy_version: str = TEMPORAL_POLICY_VERSION
    # Runtime provenance, never a model/storage field.  A valid explicit
    # ``unknown/0/''`` result is complete; malformed or missing metadata is
    # neutral but incomplete and therefore may not overwrite older evidence.
    evidence_complete: bool = field(default=False, compare=False, repr=False)


@dataclass(frozen=True)
class TemporalEligibilityDecision:
    """Explain whether a candidate may enter or remain in the servable pool."""

    disposition: str = "eligible"
    temporal_class: str = "unknown"
    temporal_confidence: float = 0.0
    temporal_validity_mode: str = "none"
    temporal_state: str = "unknown"
    age_days: float | None = None
    ttl_days: float | None = None
    trigger_at: str = ""
    policy_version: str = TEMPORAL_ELIGIBILITY_POLICY_VERSION

    @property
    def eligible(self) -> bool:
        """Whether the item may be served without another temporal review."""

        return self.disposition == "eligible"

    @property
    def hard_expired(self) -> bool:
        """Whether deterministic evidence proves that the core value expired."""

        return self.disposition == "expired"

    @property
    def needs_review(self) -> bool:
        """Whether the item must be held until it is evaluated again."""

        return self.disposition == "review_due"

    @property
    def reason(self) -> str:
        """Return a stable, content-free diagnostic for non-eligible decisions."""

        if self.disposition == "expired":
            prefix = (
                "temporal_expired:"
                f"class={self.temporal_class}:"
                f"mode={self.temporal_validity_mode}:"
                f"state={self.temporal_state}:"
            )
            if self.temporal_validity_mode == "explicit_deadline":
                prefix += f"valid_until={self.trigger_at}:"
            return prefix + f"policy={self.policy_version}"
        if self.disposition == "review_due":
            if self.age_days is not None and self.ttl_days is not None:
                return (
                    "temporal_review_due:"
                    f"class={self.temporal_class}:"
                    f"age_days={self.age_days:.3f}:"
                    f"review_days={self.ttl_days:g}:"
                    f"policy={self.policy_version}"
                )
            return (
                "temporal_review_due:"
                f"class={self.temporal_class}:"
                f"mode={self.temporal_validity_mode}:"
                f"review_at={self.trigger_at}:"
                f"policy={self.policy_version}"
            )
        return ""

    @property
    def rejection_reason(self) -> str:
        """Backward-compatible alias for :attr:`reason`."""

        return self.reason


def normalize_temporal_class(value: object) -> str:
    """Return a supported temporal class, or ``unknown`` for invalid input."""

    if not isinstance(value, str):
        return "unknown"
    normalized = value.strip().lower()
    return normalized if normalized in TEMPORAL_CLASSES else "unknown"


def normalize_temporal_confidence(value: object) -> float:
    """Return a finite confidence in ``[0, 1]``, or zero when invalid."""

    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    try:
        confidence = float(value)
    except (OverflowError, ValueError):
        return 0.0
    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        return 0.0
    return confidence


def normalize_temporal_validity_mode(value: object) -> str:
    """Return a supported validity mode, or ``none`` for invalid input."""

    if not isinstance(value, str):
        return "none"
    normalized = value.strip().lower()
    return normalized if normalized in TEMPORAL_VALIDITY_MODES else "none"


def normalize_temporal_scope(value: object) -> str:
    """Return a supported temporal scope, or ``none`` for invalid input."""

    if not isinstance(value, str):
        return "none"
    normalized = value.strip().lower()
    return normalized if normalized in TEMPORAL_SCOPES else "none"


def normalize_temporal_state(value: object) -> str:
    """Return a supported state, or ``unknown`` for invalid input."""

    if not isinstance(value, str):
        return "unknown"
    normalized = value.strip().lower()
    return normalized if normalized in TEMPORAL_STATES else "unknown"


def _aware_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (OverflowError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    try:
        return parsed.astimezone(UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _grounding_text(value: object) -> str:
    """Flatten prompt-visible strings without stringifying opaque objects."""

    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return "\n".join(_grounding_text(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        return "\n".join(_grounding_text(item) for item in value)
    return ""


def _normalized_grounding_text(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", _grounding_text(value)).casefold()
    return " ".join(normalized.split())


_EVIDENCE_DEADLINE_PATTERN = re.compile(
    r"(?<!\d)"
    r"(?P<year>\d{4})(?:[-/](?P<month_numeric>\d{1,2})[-/]"
    r"(?P<day_numeric>\d{1,2})|年\s*(?P<month_chinese>\d{1,2})月\s*"
    r"(?P<day_chinese>\d{1,2})日)"
    r"[T\s]+(?P<hour>\d{1,2}):(?P<minute>\d{2})"
    r"(?::(?P<second>\d{2}))?"
    r"\s*(?P<timezone>Z|(?:UTC|GMT)\s*[+-]\s*\d{1,2}(?::?\d{2})?|"
    r"[+-]\d{2}:?\d{2}|北京时间|中国标准时间)",
    re.IGNORECASE,
)

_ACTIVE_STATE_ASSERTIONS: MappingProxyType[str, tuple[re.Pattern[str], ...]] = MappingProxyType(
    {
        "event_state": (
            re.compile(r"(?:尚未|还未|仍未|并未|没有|没|未)(?:真正)?(?:结束|截止|到期|失效|取消)"),
            re.compile(r"(?:仍|依然|尚|还|正)(?:在)?(?:进行|开放|报名|有效)"),
            re.compile(
                r"\b(?:not|hasn['’]t|has\s+not|isn['’]t|is\s+not)\s+"
                r"(?:ended|over|closed|expired|cancelled|canceled)\b",
                re.IGNORECASE,
            ),
            re.compile(
                r"\b(?:still\s+(?:ongoing|active|open|valid|in\s+progress)|"
                r"remains?\s+(?:active|open|valid))\b",
                re.IGNORECASE,
            ),
        ),
        "version_state": (
            re.compile(
                r"(?:尚未|还未|仍未|并未|没有|没|未)(?:被)?"
                r"(?:替代|取代|淘汰|弃用|废弃|停止支持|终止支持)"
            ),
            re.compile(r"(?:仍|依然|尚|还|继续|当前)(?:在)?(?:受支持|支持|维护|可用)"),
            re.compile(r"(?:仍为|仍是|依然是|当前是)(?:最新|当前)(?:版本)?"),
            re.compile(
                r"\b(?:not|hasn['’]t|has\s+not|isn['’]t|is\s+not)\s+"
                r"(?:superseded|replaced|deprecated|unsupported)\b",
                re.IGNORECASE,
            ),
            re.compile(
                r"\b(?:still|remains?)\s+(?:supported|maintained|current|usable)\b",
                re.IGNORECASE,
            ),
        ),
    }
)

_TERMINAL_STATE_ASSERTIONS: MappingProxyType[str, tuple[re.Pattern[str], ...]] = MappingProxyType(
    {
        "event_state": (
            re.compile(r"(?:已|已经|现已|早已)(?:经)?(?:结束|截止|到期|失效|取消|关闭)"),
            re.compile(r"(?:结束|截止|到期|失效|取消|关闭)(?:了|完毕)"),
            re.compile(r"(?:不再|停止)(?:进行|开放|报名|有效)"),
            re.compile(
                r"\b(?:has\s+ended|ended|is\s+over|closed|expired|"
                r"cancelled|canceled|no\s+longer\s+(?:active|open|valid))\b",
                re.IGNORECASE,
            ),
        ),
        "version_state": (
            re.compile(
                r"(?:已|已经|现已|早已)(?:被)?(?:替代|取代|淘汰|弃用|废弃|停止支持|终止支持)"
            ),
            re.compile(r"(?:不再|停止|终止)(?:受)?支持"),
            re.compile(r"(?:由|被).{0,24}(?:替代|取代)"),
            re.compile(
                r"\b(?:superseded|replaced\s+by|deprecated|unsupported|"
                r"end[- ]of[- ]life|no\s+longer\s+supported)\b",
                re.IGNORECASE,
            ),
        ),
    }
)

_NONASSERTIVE_STATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:如果|若|假如|假设|倘若|一旦|只要|除非|是否|可能|也许|或许|预计|计划|将会|将)"),
    re.compile(
        r"\b(?:if|unless|whether|assuming|may|might|could|would|will|"
        r"planned|expected|possibly|perhaps)\b",
        re.IGNORECASE,
    ),
)


def _deadline_timezone(value: str) -> tzinfo | None:
    """Parse only explicit, deterministic timezone markers from evidence."""

    normalized = value.strip().upper().replace(" ", "")
    if normalized in {"北京时间", "中国标准时间"}:
        return timezone(timedelta(hours=8))
    if normalized == "Z":
        return UTC
    if normalized.startswith(("UTC", "GMT")):
        normalized = normalized[3:]
    match = re.fullmatch(r"(?P<sign>[+-])(?P<hour>\d{1,2})(?::?(?P<minute>\d{2}))?", normalized)
    if match is None:
        return None
    hour = int(match.group("hour"))
    minute = int(match.group("minute") or "0")
    if hour > 23 or minute > 59:
        return None
    try:
        offset = timedelta(hours=hour, minutes=minute)
        if match.group("sign") == "-":
            offset = -offset
        return timezone(offset)
    except ValueError:
        return None


def _evidence_anchors_deadline(evidence: str, deadline: datetime) -> bool:
    """Require an exact, timezone-explicit timestamp equal to the deadline.

    Date-only and timezone-naive excerpts are intentionally insufficient for
    hard expiry.  Guessing either local time or an end-of-day convention could
    silently move the boundary by hours, so those cases remain reviewable.
    """

    normalized = unicodedata.normalize("NFKC", evidence)
    expected = deadline.astimezone(UTC)
    for match in _EVIDENCE_DEADLINE_PATTERN.finditer(normalized):
        timezone = _deadline_timezone(match.group("timezone"))
        if timezone is None:
            continue
        try:
            candidate = datetime(
                int(match.group("year")),
                int(match.group("month_numeric") or match.group("month_chinese")),
                int(match.group("day_numeric") or match.group("day_chinese")),
                int(match.group("hour")),
                int(match.group("minute")),
                int(match.group("second") or "0"),
                tzinfo=timezone,
            )
        except ValueError:
            continue
        if candidate.astimezone(UTC) == expected:
            return True
    return False


def _evidence_affirms_terminal_state(*, mode: str, evidence: str) -> bool:
    """Accept terminal state evidence only when its polarity is unambiguous."""

    normalized = unicodedata.normalize("NFKC", evidence).casefold()
    if any(pattern.search(normalized) for pattern in _NONASSERTIVE_STATE_PATTERNS):
        return False
    contradictions = _ACTIVE_STATE_ASSERTIONS.get(mode, ())
    if any(pattern.search(normalized) for pattern in contradictions):
        return False
    assertions = _TERMINAL_STATE_ASSERTIONS.get(mode, ())
    return any(pattern.search(normalized) for pattern in assertions)


def _evidence_affirms_active_state(*, mode: str, evidence: str) -> bool:
    """Accept active state evidence only when it positively states the current state."""

    normalized = unicodedata.normalize("NFKC", evidence).casefold()
    if any(pattern.search(normalized) for pattern in _NONASSERTIVE_STATE_PATTERNS):
        return False
    contradictions = _TERMINAL_STATE_ASSERTIONS.get(mode, ())
    if any(pattern.search(normalized) for pattern in contradictions):
        return False
    assertions = _ACTIVE_STATE_ASSERTIONS.get(mode, ())
    return any(pattern.search(normalized) for pattern in assertions)


def trusted_publication_datetime(value: object, *, now: datetime) -> datetime | None:
    """Return a timezone-aware publication clock, or ``None`` when untrusted.

    Missing, malformed, timezone-naive, and clearly future timestamps fail
    neutral.  Source clocks may be a few minutes ahead, so values within the
    shared skew tolerance remain usable and their effective age is clamped to
    zero by callers.
    """

    if not isinstance(value, str) or not value.strip():
        return None
    try:
        published = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (OverflowError, ValueError):
        return None
    if published.tzinfo is None:
        return None
    effective_now = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    try:
        normalized = published.astimezone(effective_now.tzinfo)
        future_offset = normalized - effective_now
    except (OverflowError, OSError, ValueError):
        return None
    if future_offset > PUBLICATION_CLOCK_SKEW_TOLERANCE:
        return None
    return normalized


def evaluate_temporal_eligibility(
    *,
    temporal_class: object,
    temporal_confidence: object,
    published_at: object,
    temporal_validity_mode: object = "none",
    temporal_valid_until: object = "",
    temporal_scope: object = "none",
    temporal_evidence: object = "",
    temporal_state: object = "unknown",
    temporal_next_review_at: object = "",
    temporal_evaluated_at: object = "",
    temporal_policy_version: object = "v1",
    evidence_complete: object | None = None,
    now: datetime | None = None,
) -> TemporalEligibilityDecision:
    """Return the deterministic v2 serve disposition.

    V2 only hard-expires high-confidence, complete, core-value evidence with
    a grounded explicit deadline.  A scheduled non-deadline check becomes
    ``review_due`` instead.  Calls using only the old three semantic fields
    retain the old 3/60 day windows, but those windows now schedule review
    rather than claiming factual expiry.  Every malformed or uncertain input
    fails neutral.
    """

    effective_now = now or datetime.now(UTC)
    if effective_now.tzinfo is None:
        effective_now = effective_now.replace(tzinfo=UTC)
    normalized_class = normalize_temporal_class(temporal_class)
    confidence = normalize_temporal_confidence(temporal_confidence)
    policy = TEMPORAL_CLASS_POLICIES.get(normalized_class)
    ttl_days = policy.admission_ttl_days if policy is not None else None
    neutral = TemporalEligibilityDecision(
        temporal_class=normalized_class,
        temporal_confidence=confidence,
        ttl_days=ttl_days,
    )

    is_legacy_call = not (
        isinstance(temporal_policy_version, str)
        and temporal_policy_version.strip().lower() == TEMPORAL_POLICY_VERSION
    )
    if is_legacy_call:
        if ttl_days is None or confidence < TEMPORAL_CONFIDENCE_FULL:
            return neutral
        published = trusted_publication_datetime(published_at, now=effective_now)
        if published is None:
            return neutral
        try:
            age_days = max(0.0, (effective_now - published).total_seconds() / 86400.0)
        except (OverflowError, OSError, ValueError):
            return neutral
        return TemporalEligibilityDecision(
            disposition="review_due" if age_days >= ttl_days else "eligible",
            temporal_class=normalized_class,
            temporal_confidence=confidence,
            temporal_validity_mode="freshness_only",
            age_days=age_days,
            ttl_days=ttl_days,
            trigger_at=_utc_text(published + timedelta(days=ttl_days)),
        )

    mode = normalize_temporal_validity_mode(temporal_validity_mode)
    scope = normalize_temporal_scope(temporal_scope)
    state = normalize_temporal_state(temporal_state)
    neutral = replace(neutral, temporal_validity_mode=mode, temporal_state=state)
    if (
        evidence_complete is not True
        or confidence < TEMPORAL_CONFIDENCE_FULL
        or scope != "core"
        or normalized_class == "unknown"
        or not isinstance(temporal_evidence, str)
        or not temporal_evidence.strip()
    ):
        return neutral
    if not isinstance(temporal_validity_mode, str) or (
        temporal_validity_mode.strip().lower() not in TEMPORAL_VALIDITY_MODES
    ):
        return neutral
    if not isinstance(temporal_scope, str) or temporal_scope.strip().lower() not in TEMPORAL_SCOPES:
        return neutral
    if not isinstance(temporal_state, str) or temporal_state.strip().lower() not in TEMPORAL_STATES:
        return neutral
    if mode not in _ALLOWED_VALIDITY_MODES_BY_CLASS.get(normalized_class, frozenset()):
        return neutral
    allowed_states = {
        "none": {"unknown"},
        "freshness_only": {"unknown"},
        "explicit_deadline": {"unknown"},
        "event_state": {"active", "expired"},
        "version_state": {"active", "superseded"},
    }
    if state not in allowed_states[mode]:
        return neutral

    evaluated = _aware_datetime(temporal_evaluated_at)
    if evaluated is None:
        return neutral

    if mode == "explicit_deadline":
        deadline = _aware_datetime(temporal_valid_until)
        if deadline is None or not _evidence_anchors_deadline(temporal_evidence, deadline):
            return neutral
        trigger_at = _utc_text(deadline)
        return TemporalEligibilityDecision(
            disposition="expired" if effective_now >= deadline else "eligible",
            temporal_class=normalized_class,
            temporal_confidence=confidence,
            temporal_validity_mode=mode,
            temporal_state=state,
            trigger_at=trigger_at,
        )

    if mode not in {"event_state", "version_state", "freshness_only"}:
        return neutral
    terminal_state = (mode == "event_state" and state == "expired") or (
        mode == "version_state" and state == "superseded"
    )
    if terminal_state:
        if not _evidence_affirms_terminal_state(mode=mode, evidence=temporal_evidence):
            return neutral
        return TemporalEligibilityDecision(
            disposition="expired",
            temporal_class=normalized_class,
            temporal_confidence=confidence,
            temporal_validity_mode=mode,
            temporal_state=state,
        )
    active_state = mode in {"event_state", "version_state"} and state == "active"
    if active_state and not _evidence_affirms_active_state(
        mode=mode,
        evidence=temporal_evidence,
    ):
        return neutral
    review_at = _aware_datetime(temporal_next_review_at)
    if review_at is None or (evaluated is not None and review_at < evaluated):
        return neutral
    trigger_at = _utc_text(review_at)
    return TemporalEligibilityDecision(
        disposition="review_due" if effective_now >= review_at else "eligible",
        temporal_class=normalized_class,
        temporal_confidence=confidence,
        temporal_validity_mode=mode,
        temporal_state=state,
        trigger_at=trigger_at,
    )


def temporal_bonus_component(
    *,
    temporal_class: object,
    temporal_confidence: object,
    published_at: object,
    now: datetime | None = None,
) -> float:
    """Return the unweighted, publication-based bonus for one candidate."""

    effective_now = now or datetime.now(UTC)
    if effective_now.tzinfo is None:
        effective_now = effective_now.replace(tzinfo=UTC)
    normalized_class = normalize_temporal_class(temporal_class)
    policy = TEMPORAL_CLASS_POLICIES.get(normalized_class)
    if policy is None:
        return 0.0
    confidence = normalize_temporal_confidence(temporal_confidence)
    if confidence >= TEMPORAL_CONFIDENCE_FULL:
        confidence_weight = 1.0
    elif confidence >= TEMPORAL_CONFIDENCE_HALF:
        confidence_weight = 0.5
    else:
        return 0.0
    published = trusted_publication_datetime(published_at, now=effective_now)
    if published is None:
        return 0.0
    try:
        age_days = max(0.0, (effective_now - published).total_seconds() / 86400.0)
    except (OverflowError, OSError, ValueError):
        return 0.0
    freshness = 2.0 ** (-age_days / policy.bonus_half_life_days)
    return float(policy.bonus_cap * confidence_weight * freshness)


def ground_temporal_evaluation(
    evaluation: TemporalEvaluation,
    *,
    content_text: object,
) -> TemporalEvaluation:
    """Downgrade ungrounded hard claims and neutralize invented freshness evidence.

    ``temporal_evidence`` is a short verbatim excerpt.  NFKC/case/whitespace
    normalization is permitted, but fuzzy or semantic matching is not: a hard
    expiry claim must be auditable in the prompt-visible content itself.
    """

    if not evaluation.evidence_complete or evaluation.temporal_validity_mode not in {
        "explicit_deadline",
        "event_state",
        "version_state",
        "freshness_only",
    }:
        return evaluation
    evidence = _normalized_grounding_text(evaluation.temporal_evidence)
    content = _normalized_grounding_text(content_text)
    grounded = bool(evidence and evidence in content)
    if evaluation.temporal_validity_mode == "explicit_deadline":
        deadline = _aware_datetime(evaluation.temporal_valid_until)
        grounded = bool(
            grounded
            and deadline is not None
            and _evidence_anchors_deadline(evaluation.temporal_evidence, deadline)
        )
    terminal_state = (
        evaluation.temporal_validity_mode == "event_state"
        and evaluation.temporal_state == "expired"
    ) or (
        evaluation.temporal_validity_mode == "version_state"
        and evaluation.temporal_state == "superseded"
    )
    if terminal_state:
        grounded = bool(
            grounded
            and _evidence_affirms_terminal_state(
                mode=evaluation.temporal_validity_mode,
                evidence=evaluation.temporal_evidence,
            )
        )
    active_state = (
        evaluation.temporal_validity_mode in {"event_state", "version_state"}
        and evaluation.temporal_state == "active"
    )
    if active_state:
        grounded = bool(
            grounded
            and _evidence_affirms_active_state(
                mode=evaluation.temporal_validity_mode,
                evidence=evaluation.temporal_evidence,
            )
        )
    if grounded:
        return evaluation
    if evaluation.temporal_validity_mode == "freshness_only":
        # Unlike a hard-state claim, freshness-only has no weaker active mode
        # to fall back to.  Keeping a hallucinated excerpt would still create
        # a code-owned review clock and could later erase grounded evidence.
        return TemporalEvaluation()
    return replace(
        evaluation,
        temporal_validity_mode="freshness_only",
        temporal_valid_until="",
        temporal_state="unknown",
    )


def schedule_temporal_evaluation(
    evaluation: TemporalEvaluation,
    *,
    evaluated_at: object,
) -> TemporalEvaluation:
    """Attach code-owned evaluation and next-review clocks to valid evidence."""

    evaluated = _aware_datetime(evaluated_at)
    if evaluated is None:
        return replace(
            evaluation,
            temporal_next_review_at="",
            temporal_evaluated_at="",
            evidence_complete=False,
        )
    evaluated_text = _utc_text(evaluated)
    if not evaluation.evidence_complete:
        return replace(
            evaluation,
            temporal_next_review_at="",
            temporal_evaluated_at=evaluated_text,
        )
    if (
        evaluation.temporal_scope == "hook"
        or evaluation.temporal_class in {"evergreen", "historical", "unknown"}
        or evaluation.temporal_state in {"expired", "superseded"}
    ):
        return replace(
            evaluation,
            temporal_next_review_at="",
            temporal_evaluated_at=evaluated_text,
        )
    if evaluation.temporal_validity_mode == "explicit_deadline":
        deadline = _aware_datetime(evaluation.temporal_valid_until)
        if deadline is None:
            return replace(
                evaluation,
                temporal_next_review_at="",
                temporal_evaluated_at=evaluated_text,
                evidence_complete=False,
            )
        next_review = deadline
    else:
        interval = TEMPORAL_REVIEW_INTERVALS.get(evaluation.temporal_class)
        if interval is None:
            return replace(
                evaluation,
                temporal_next_review_at="",
                temporal_evaluated_at=evaluated_text,
            )
        next_review = evaluated + interval
    return replace(
        evaluation,
        temporal_next_review_at=_utc_text(next_review),
        temporal_evaluated_at=evaluated_text,
    )


def parse_temporal_evaluation(payload: Mapping[str, object]) -> TemporalEvaluation:
    """Validate all model-owned v2 temporal fields as one atomic result.

    Missing, malformed, or internally inconsistent metadata becomes the
    neutral ``unknown/none`` value without invalidating relevance.  Review
    clocks and the policy version are code-owned and never read from payload.
    """

    required = {
        "temporal_class",
        "temporal_confidence",
        "temporal_reason",
        "temporal_validity_mode",
        "temporal_valid_until",
        "temporal_scope",
        "temporal_evidence",
        "temporal_state",
    }
    if not required.issubset(payload):
        missing = ",".join(sorted(required.difference(payload)))
        logger.warning(
            "Temporal evaluation metadata missing fields (%s); using unknown",
            missing,
        )
        return TemporalEvaluation()

    raw_class = payload["temporal_class"]
    raw_confidence = payload["temporal_confidence"]
    raw_reason = payload["temporal_reason"]
    raw_mode = payload["temporal_validity_mode"]
    raw_until = payload["temporal_valid_until"]
    raw_scope = payload["temporal_scope"]
    raw_evidence = payload["temporal_evidence"]
    raw_state = payload["temporal_state"]
    if (
        not isinstance(raw_class, str)
        or not isinstance(raw_reason, str)
        or not isinstance(raw_mode, str)
        or not isinstance(raw_until, str)
        or not isinstance(raw_scope, str)
        or not isinstance(raw_evidence, str)
        or not isinstance(raw_state, str)
    ):
        logger.warning("Temporal evaluation text field has invalid type; using unknown")
        return TemporalEvaluation()
    if isinstance(raw_confidence, bool) or not isinstance(raw_confidence, int | float):
        logger.warning("Temporal evaluation confidence has invalid type; using unknown")
        return TemporalEvaluation()

    temporal_class = normalize_temporal_class(raw_class)
    mode = normalize_temporal_validity_mode(raw_mode)
    scope = normalize_temporal_scope(raw_scope)
    state = normalize_temporal_state(raw_state)
    try:
        raw_confidence_float = float(raw_confidence)
    except (OverflowError, ValueError):
        logger.warning("Temporal evaluation confidence is not representable; using unknown")
        return TemporalEvaluation()
    confidence = normalize_temporal_confidence(raw_confidence)
    confidence_is_valid = math.isfinite(raw_confidence_float) and 0.0 <= raw_confidence_float <= 1.0
    reason = raw_reason.strip()
    until = raw_until.strip()
    evidence = raw_evidence.strip()

    explicit_unknown = raw_class.strip().lower() == "unknown"
    neutral_wire = (
        explicit_unknown
        and confidence_is_valid
        and raw_confidence_float == 0.0
        and not reason
        and raw_mode.strip().lower() == "none"
        and not until
        and raw_scope.strip().lower() == "none"
        and not evidence
        and raw_state.strip().lower() == "unknown"
    )
    if neutral_wire:
        return TemporalEvaluation(evidence_complete=True)
    if temporal_class == "unknown":
        logger.warning("Temporal evaluation class is invalid or non-neutral unknown; using unknown")
        return TemporalEvaluation()
    if not confidence_is_valid:
        logger.warning("Temporal evaluation confidence is outside [0, 1]; using unknown")
        return TemporalEvaluation()
    if not reason:
        logger.warning("Temporal evaluation reason is empty for a classified item; using unknown")
        return TemporalEvaluation()
    if raw_mode.strip().lower() not in TEMPORAL_VALIDITY_MODES:
        logger.warning("Temporal evaluation validity mode is invalid; using unknown")
        return TemporalEvaluation()
    if raw_scope.strip().lower() not in TEMPORAL_SCOPES:
        logger.warning("Temporal evaluation scope is invalid; using unknown")
        return TemporalEvaluation()
    if raw_state.strip().lower() not in TEMPORAL_STATES:
        logger.warning("Temporal evaluation state is invalid; using unknown")
        return TemporalEvaluation()
    if mode not in _ALLOWED_VALIDITY_MODES_BY_CLASS[temporal_class]:
        logger.warning("Temporal evaluation class/mode pair is invalid; using unknown")
        return TemporalEvaluation()
    allowed_states = {
        "none": {"unknown"},
        "freshness_only": {"unknown"},
        "explicit_deadline": {"unknown"},
        "event_state": {"active", "expired"},
        "version_state": {"active", "superseded"},
    }
    if state not in allowed_states[mode]:
        logger.warning("Temporal evaluation mode/state pair is invalid; using unknown")
        return TemporalEvaluation()

    if mode == "none":
        if scope != "none" or until or evidence:
            logger.warning("Temporal evaluation none mode has active fields; using unknown")
            return TemporalEvaluation()
        normalized_until = ""
    else:
        if scope not in {"core", "hook"} or not evidence:
            logger.warning("Temporal evaluation active mode lacks scope/evidence; using unknown")
            return TemporalEvaluation()
        if temporal_class in {"evergreen", "historical"} and scope != "hook":
            logger.warning("Durable content can only carry hook-scoped freshness; using unknown")
            return TemporalEvaluation()
        if mode == "explicit_deadline":
            parsed_until = _aware_datetime(until)
            if parsed_until is None:
                logger.warning("Temporal evaluation deadline is invalid; using unknown")
                return TemporalEvaluation()
            normalized_until = _utc_text(parsed_until)
        else:
            if until:
                logger.warning("Temporal evaluation non-deadline has valid_until; using unknown")
                return TemporalEvaluation()
            normalized_until = ""

    return TemporalEvaluation(
        temporal_class=temporal_class,
        temporal_confidence=confidence,
        temporal_reason=reason,
        temporal_validity_mode=mode,
        temporal_valid_until=normalized_until,
        temporal_scope=scope,
        temporal_evidence=evidence,
        temporal_state=state,
        evidence_complete=True,
    )
