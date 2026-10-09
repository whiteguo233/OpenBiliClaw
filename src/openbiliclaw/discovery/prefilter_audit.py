"""Privacy-safe evaluator prefilter shadow evidence and quality gate.

The audit contract deliberately stores no candidate text, URL, author, or raw
provider response.  A shadow decision is joined to its eventual evaluator
score by a random decision id and identifies the candidate only through a
domain-separated SHA-256 digest.
"""

from __future__ import annotations

import hashlib
import math
import re
import secrets
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# Thirty days covers the multi-day production shadow calibration requested by
# the Phase 2 spec.  The row ceiling is the second, traffic-independent bound:
# at the evaluator hard cap (90 candidates) it retains more than 220 complete
# waves while preventing an unattended daemon from growing this audit forever.
PREFILTER_AUDIT_RETENTION_DAYS: Final = 30
PREFILTER_AUDIT_MAX_ROWS: Final = 20_000

PREFILTER_GATE_MIN_JOINABLE: Final = 100
PREFILTER_GATE_MIN_ADMISSION_RECALL: Final = 0.99
PREFILTER_GATE_MAX_FALSE_NEGATIVES: Final = 1
PREFILTER_GATE_PLATFORM_MIN_OBSERVATIONS: Final = 20
PREFILTER_GATE_PLATFORM_MIN_RECALL: Final = 0.95

PREFILTER_OK_STATUS: Final = "ok"
PREFILTER_EXPLORE_EXEMPT_STATUS: Final = "explore_exempt"
PREFILTER_NO_INTERESTS_STATUS: Final = "profile_interests_missing"
PREFILTER_DEGRADED_STATUSES: Final[frozenset[str]] = frozenset(
    {
        PREFILTER_NO_INTERESTS_STATUS,
        "embedding_service_missing",
        "interest_embedding_error",
        "interest_embedding_missing",
        "interest_embedding_invalid",
        "content_text_missing",
        "content_embedding_error",
        "content_embedding_missing",
        "content_embedding_invalid",
        "similarity_error",
    }
)
PREFILTER_PLATFORM_CLASSES: Final[frozenset[str]] = frozenset(
    {
        "bangumi",
        "bilibili",
        "douyin",
        "instagram",
        "github",
        "reddit",
        "tiktok",
        "twitter",
        "unknown",
        "weibo",
        "web",
        "xiaohongshu",
        "youtube",
        "zhihu",
    }
)
PREFILTER_CONTEXT_CLASSES: Final[frozenset[str]] = frozenset(
    {
        "catalog",
        "creator",
        "direct",
        "explore",
        "feed",
        "other",
        "related",
        "search",
        "trending",
    }
)
PREFILTER_EMBEDDING_STATUSES: Final[frozenset[str]] = frozenset(
    {
        PREFILTER_OK_STATUS,
        PREFILTER_EXPLORE_EXEMPT_STATUS,
        *PREFILTER_DEGRADED_STATUSES,
    }
)

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_CLASS_TOKEN_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, kw_only=True)
class PrefilterShadowDecision:
    """One model-free prefilter decision, safe to persist before LLM I/O."""

    content_index: int
    candidate_hash: str
    platform_class: str
    context_class: str
    similarity: float | None
    threshold: float
    explore: bool
    embedding_namespace: str
    profile_digest: str
    would_filter: bool
    embedding_status: str
    fail_open: bool
    explicit_strong_interest: bool
    decision_id: str = ""

    def __post_init__(self) -> None:
        if not self.decision_id:
            object.__setattr__(self, "decision_id", secrets.token_hex(16))

    def as_storage_record(self) -> dict[str, object]:
        """Return the durable record without the request-local content index."""

        return {
            "decision_id": self.decision_id,
            "candidate_hash": self.candidate_hash,
            "platform_class": self.platform_class,
            "context_class": self.context_class,
            "similarity": self.similarity,
            "threshold": self.threshold,
            "explore": self.explore,
            "embedding_namespace": self.embedding_namespace,
            "profile_digest": self.profile_digest,
            "would_filter": self.would_filter,
            "embedding_status": self.embedding_status,
            "fail_open": self.fail_open,
            "explicit_strong_interest": self.explicit_strong_interest,
        }


@dataclass(frozen=True, kw_only=True)
class PrefilterShadowOutcome:
    """Eventual evaluator result joined back to one shadow decision."""

    decision_id: str
    llm_score: float
    admission_threshold: float
    admission_result: bool

    def as_storage_record(self) -> dict[str, object]:
        return {
            "decision_id": self.decision_id,
            "llm_score": self.llm_score,
            "admission_threshold": self.admission_threshold,
            "admission_result": self.admission_result,
        }


@dataclass(frozen=True)
class PrefilterGateStratum:
    """Admission-recall metrics for one privacy-safe class."""

    name: str
    observations: int
    admitted: int
    false_negatives: int
    admission_recall: float

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "observations": self.observations,
            "admitted": self.admitted,
            "false_negatives": self.false_negatives,
            "admission_recall": self.admission_recall,
        }


@dataclass(frozen=True)
class PrefilterGateReport:
    """Pure §6.4 verdict; it never changes runtime configuration."""

    passed: bool
    reasons: tuple[str, ...]
    total_decisions: int
    joinable_candidates: int
    telemetry_coverage: float
    admitted_candidates: int
    would_filter_candidates: int
    high_score_false_negatives: int
    admission_recall: float
    explicit_strong_interest_false_negatives: int
    explore_candidates: int
    explore_false_negatives: int
    degraded_embedding_cases: int
    fail_open_cases: int
    fail_open_violations: int
    status_counts: tuple[tuple[str, int], ...]
    platform_strata: tuple[PrefilterGateStratum, ...]
    context_strata: tuple[PrefilterGateStratum, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "contract": "evaluator-prefilter-gate-v1",
            "passed": self.passed,
            "production_mode_change": "none",
            "required_mode_on_failure": "shadow",
            "reasons": list(self.reasons),
            "counts": {
                "total_decisions": self.total_decisions,
                "joinable_candidates": self.joinable_candidates,
                "admitted_candidates": self.admitted_candidates,
                "would_filter_candidates": self.would_filter_candidates,
                "high_score_false_negatives": self.high_score_false_negatives,
                "explicit_strong_interest_false_negatives": (
                    self.explicit_strong_interest_false_negatives
                ),
                "explore_candidates": self.explore_candidates,
                "explore_false_negatives": self.explore_false_negatives,
                "degraded_embedding_cases": self.degraded_embedding_cases,
                "fail_open_cases": self.fail_open_cases,
                "fail_open_violations": self.fail_open_violations,
            },
            "metrics": {
                "telemetry_coverage": self.telemetry_coverage,
                "admission_recall": self.admission_recall,
            },
            "gate_constants": {
                "min_joinable_candidates": PREFILTER_GATE_MIN_JOINABLE,
                "min_admission_recall": PREFILTER_GATE_MIN_ADMISSION_RECALL,
                "max_high_score_false_negatives": PREFILTER_GATE_MAX_FALSE_NEGATIVES,
                "platform_min_observations": PREFILTER_GATE_PLATFORM_MIN_OBSERVATIONS,
                "platform_min_admission_recall": PREFILTER_GATE_PLATFORM_MIN_RECALL,
                "retention_days": PREFILTER_AUDIT_RETENTION_DAYS,
                "max_rows": PREFILTER_AUDIT_MAX_ROWS,
            },
            "status_counts": dict(self.status_counts),
            "platform_strata": [item.to_dict() for item in self.platform_strata],
            "context_strata": [item.to_dict() for item in self.context_strata],
        }


def hash_prefilter_candidate_identity(identity: str) -> str:
    """Hash one canonical candidate identity with an audit domain separator."""

    payload = f"openbiliclaw:evaluator-prefilter:v1\0{identity}".encode()
    return hashlib.sha256(payload).hexdigest()


def classify_prefilter_context(source_context: object, source_strategy: object) -> str:
    """Reduce possibly sensitive context text to a bounded source class."""

    context = str(source_context or "").strip().lower()
    strategy = str(source_strategy or "").strip().lower()
    strategy_token = _CLASS_TOKEN_RE.sub("_", strategy).strip("_")
    if "explore" in strategy_token:
        return "explore"
    raw = strategy if not context or context == "mixed" else context
    prefix = re.split(r"[:=|/]", raw, maxsplit=1)[0]
    token = _CLASS_TOKEN_RE.sub("_", prefix).strip("_")

    if "explore" in token:
        return "explore"
    if "search" in token or "query" in token or "keyword" in token:
        return "search"
    if "related" in token or "chain" in token:
        return "related"
    if "creator" in token or "up_track" in token or "follow" in token:
        return "creator"
    if "trend" in token or "rank" in token or "popular" in token or "hot" in token:
        return "trending"
    if "feed" in token or "timeline" in token:
        return "feed"
    if "bangumi" in token or "catalog" in token:
        return "catalog"
    if "direct" in token or "bootstrap" in token or "import" in token:
        return "direct"
    return "other"


def sanitize_prefilter_platform(value: object) -> str:
    """Return a bounded platform class, never arbitrary source text."""

    token = _CLASS_TOKEN_RE.sub("_", str(value or "").strip().lower()).strip("_")
    return token if token in PREFILTER_PLATFORM_CLASSES else "unknown"


def is_explicit_strong_interest_context(
    *,
    context_class: str,
    source_keyword_id: object,
) -> bool:
    """Return a conservative protected-interest marker for the gate.

    A durable keyword provenance id is an explicit profile-directed search.
    Search, related-chain, and creator contexts are also protected so an
    ambiguous producer cannot make the gate easier by omitting that id.
    """

    if isinstance(source_keyword_id, int | float | str) and not isinstance(source_keyword_id, bool):
        try:
            if int(source_keyword_id) > 0:
                return True
        except (TypeError, ValueError):
            pass
    return context_class in {"search", "related", "creator"}


def _as_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    return None


def _as_finite_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if not isinstance(value, int | float | str):
        return None
    try:
        result = float(value)
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def _is_joinable(row: Mapping[str, object]) -> bool:
    decision_id = str(row.get("decision_id") or "")
    candidate_hash = str(row.get("candidate_hash") or "")
    platform = str(row.get("platform_class") or "")
    context = str(row.get("context_class") or "")
    namespace = str(row.get("embedding_namespace") or "")
    profile_digest = str(row.get("profile_digest") or "")
    status = str(row.get("embedding_status") or "")
    threshold = _as_finite_float(row.get("threshold"))
    llm_score = _as_finite_float(row.get("llm_score"))
    admission_threshold = _as_finite_float(row.get("admission_threshold"))
    explore = _as_bool(row.get("explore"))
    would_filter = _as_bool(row.get("would_filter"))
    fail_open = _as_bool(row.get("fail_open"))
    strong_interest = _as_bool(row.get("explicit_strong_interest"))
    admitted = _as_bool(row.get("admission_result"))
    if not (
        decision_id
        and _HASH_RE.fullmatch(candidate_hash)
        and platform in PREFILTER_PLATFORM_CLASSES
        and context in PREFILTER_CONTEXT_CLASSES
        and re.fullmatch(r"[0-9a-f]{16,64}", namespace)
        and re.fullmatch(r"[0-9a-f]{16,64}", profile_digest)
        and status in PREFILTER_EMBEDDING_STATUSES
        and threshold is not None
        and llm_score is not None
        and admission_threshold is not None
        and explore is not None
        and would_filter is not None
        and fail_open is not None
        and strong_interest is not None
        and admitted is not None
    ):
        return False
    if not (0.0 <= threshold <= 1.0 and 0.0 <= llm_score <= 1.0):
        return False
    if not (0.0 <= admission_threshold <= 1.0):
        return False
    if admitted != (llm_score >= admission_threshold):
        return False
    similarity = _as_finite_float(row.get("similarity"))
    if status == PREFILTER_OK_STATUS:
        if similarity is None or not 0.0 <= similarity <= 1.0:
            return False
        if would_filter != (similarity < threshold):
            return False
    return True


def _recall(*, admitted: int, false_negatives: int) -> float:
    if admitted <= 0:
        return 1.0
    return max(0.0, min(1.0, (admitted - false_negatives) / admitted))


def _strata(
    rows: Sequence[Mapping[str, object]],
    *,
    key: str,
) -> tuple[PrefilterGateStratum, ...]:
    buckets: dict[str, list[Mapping[str, object]]] = {}
    for row in rows:
        name = str(row.get(key) or "unknown")
        buckets.setdefault(name, []).append(row)
    result: list[PrefilterGateStratum] = []
    for name in sorted(buckets):
        bucket = buckets[name]
        admitted = sum(_as_bool(row.get("admission_result")) is True for row in bucket)
        false_negatives = sum(
            _as_bool(row.get("admission_result")) is True
            and _as_bool(row.get("would_filter")) is True
            for row in bucket
        )
        result.append(
            PrefilterGateStratum(
                name=name,
                observations=len(bucket),
                admitted=admitted,
                false_negatives=false_negatives,
                admission_recall=_recall(
                    admitted=admitted,
                    false_negatives=false_negatives,
                ),
            )
        )
    return tuple(result)


def evaluate_prefilter_gate(rows: Sequence[Mapping[str, object]]) -> PrefilterGateReport:
    """Evaluate the Phase 2 §6.4 gate without mutating runtime state."""

    all_rows = list(rows)
    joinable = [row for row in all_rows if _is_joinable(row)]
    total = len(all_rows)
    telemetry_coverage = len(joinable) / total if total else 0.0
    admitted = sum(_as_bool(row.get("admission_result")) is True for row in joinable)
    would_filter = sum(_as_bool(row.get("would_filter")) is True for row in joinable)
    false_negative_rows = [
        row
        for row in joinable
        if _as_bool(row.get("admission_result")) is True
        and _as_bool(row.get("would_filter")) is True
    ]
    false_negatives = len(false_negative_rows)
    strong_false_negatives = sum(
        _as_bool(row.get("explicit_strong_interest")) is True for row in false_negative_rows
    )
    explore_rows = [row for row in joinable if _as_bool(row.get("explore")) is True]
    explore_false_negatives = sum(
        _as_bool(row.get("would_filter")) is True and _as_bool(row.get("admission_result")) is True
        for row in explore_rows
    )
    degraded_rows = [
        row
        for row in joinable
        if str(row.get("embedding_status") or "") in PREFILTER_DEGRADED_STATUSES
    ]
    fail_open_rows = [
        row
        for row in degraded_rows
        if _as_bool(row.get("fail_open")) is True and _as_bool(row.get("would_filter")) is False
    ]
    fail_open_violations = len(degraded_rows) - len(fail_open_rows)
    admission_recall = _recall(admitted=admitted, false_negatives=false_negatives)
    platform_strata = _strata(joinable, key="platform_class")
    context_strata = _strata(joinable, key="context_class")

    reasons: list[str] = []
    if len(joinable) < PREFILTER_GATE_MIN_JOINABLE:
        reasons.append("joinable_candidates_below_100")
    if telemetry_coverage < 1.0:
        reasons.append("telemetry_coverage_below_1.0")
    if admitted <= 0:
        reasons.append("admitted_candidates_missing")
    if admission_recall < PREFILTER_GATE_MIN_ADMISSION_RECALL:
        reasons.append("admission_recall_below_0.99")
    if false_negatives > PREFILTER_GATE_MAX_FALSE_NEGATIVES:
        reasons.append("high_score_false_negatives_above_1")
    if strong_false_negatives > 0:
        reasons.append("explicit_strong_interest_false_negative")
    if not explore_rows:
        reasons.append("explore_stratum_missing")
    if explore_false_negatives > 0:
        reasons.append("explore_false_negative")
    for stratum in platform_strata:
        if (
            stratum.observations >= PREFILTER_GATE_PLATFORM_MIN_OBSERVATIONS
            and stratum.admission_recall < PREFILTER_GATE_PLATFORM_MIN_RECALL
        ):
            reasons.append(f"platform_recall_below_0.95:{stratum.name}")
    if not degraded_rows:
        reasons.append("embedding_fail_open_evidence_missing")
    if fail_open_violations > 0:
        reasons.append("embedding_fail_open_violation")

    status_counts = tuple(
        sorted(Counter(str(row.get("embedding_status") or "missing") for row in joinable).items())
    )
    return PrefilterGateReport(
        passed=not reasons,
        reasons=tuple(reasons),
        total_decisions=total,
        joinable_candidates=len(joinable),
        telemetry_coverage=telemetry_coverage,
        admitted_candidates=admitted,
        would_filter_candidates=would_filter,
        high_score_false_negatives=false_negatives,
        admission_recall=admission_recall,
        explicit_strong_interest_false_negatives=strong_false_negatives,
        explore_candidates=len(explore_rows),
        explore_false_negatives=explore_false_negatives,
        degraded_embedding_cases=len(degraded_rows),
        fail_open_cases=len(fail_open_rows),
        fail_open_violations=fail_open_violations,
        status_counts=status_counts,
        platform_strata=platform_strata,
        context_strata=context_strata,
    )


def validate_prefilter_storage_record(record: Mapping[str, Any]) -> None:
    """Reject malformed or accidentally raw audit fields before persistence."""

    decision_id = str(record.get("decision_id") or "")
    candidate_hash = str(record.get("candidate_hash") or "")
    platform = str(record.get("platform_class") or "")
    context = str(record.get("context_class") or "")
    namespace = str(record.get("embedding_namespace") or "")
    profile_digest = str(record.get("profile_digest") or "")
    status = str(record.get("embedding_status") or "")
    threshold = _as_finite_float(record.get("threshold"))
    similarity = _as_finite_float(record.get("similarity"))
    if not re.fullmatch(r"[0-9a-f]{32}", decision_id):
        raise ValueError("prefilter audit decision_id must be random hex")
    if not _HASH_RE.fullmatch(candidate_hash):
        raise ValueError("prefilter audit candidate identity must be a SHA-256 digest")
    if platform not in PREFILTER_PLATFORM_CLASSES:
        raise ValueError("prefilter audit platform_class must be a fixed class")
    if context not in PREFILTER_CONTEXT_CLASSES:
        raise ValueError("prefilter audit context_class must be a fixed class")
    if not re.fullmatch(r"[0-9a-f]{16,64}", namespace):
        raise ValueError("prefilter audit embedding namespace must be a digest")
    if not re.fullmatch(r"[0-9a-f]{16,64}", profile_digest):
        raise ValueError("prefilter audit profile must be a digest")
    if status not in PREFILTER_EMBEDDING_STATUSES:
        raise ValueError("prefilter audit embedding status must be a fixed token")
    if threshold is None or not 0.0 <= threshold <= 1.0:
        raise ValueError("prefilter audit threshold must be finite in [0, 1]")
    if similarity is not None and not 0.0 <= similarity <= 1.0:
        raise ValueError("prefilter audit similarity must be finite in [0, 1]")
    for field_name in (
        "explore",
        "would_filter",
        "fail_open",
        "explicit_strong_interest",
    ):
        if _as_bool(record.get(field_name)) is None:
            raise ValueError(f"prefilter audit {field_name} must be boolean")
