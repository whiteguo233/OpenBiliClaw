"""Privacy-safe learned-vs-LLM evaluator shadow evidence and quality gate.

The audit contract deliberately stores no candidate text, URL, author, or raw
provider response.  A shadow decision is joined to its eventual evaluator score
by a random decision id and identifies the candidate only through a
domain-separated SHA-256 digest.  ``learned_score`` is the opt-in learned
relevance score; ``llm_score`` is the existing LLM evaluator score running in
shadow.  The gate never mutates runtime configuration.
"""

from __future__ import annotations

import hashlib
import math
import re
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# Thirty days mirrors the prefilter shadow calibration horizon.  The row
# ceiling bounds an unattended daemon that appends a learned-vs-LLM pair per
# evaluated batch.
LEARNED_AUDIT_RETENTION_DAYS: Final = 30
LEARNED_AUDIT_MAX_ROWS: Final = 20_000

LEARNED_GATE_MIN_JOINABLE: Final = 100
LEARNED_GATE_MIN_SPEARMAN: Final = 0.5
LEARNED_GATE_MAX_ADMISSION_DELTA: Final = 0.02
LEARNED_GATE_MIN_COVERAGE: Final = 0.9

LEARNED_OK_PLATFORMS: Final[frozenset[str]] = frozenset(
    {
        "bangumi",
        "bilibili",
        "douyin",
        "reddit",
        "twitter",
        "unknown",
        "weibo",
        "web",
        "xiaohongshu",
        "youtube",
        "zhihu",
    }
)

LEARNED_OK_CONTEXTS: Final[frozenset[str]] = frozenset(
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

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_CLASS_TOKEN_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, kw_only=True)
class LearnedShadowDecision:
    """One learned-vs-LLM shadow decision, safe to persist before LLM I/O."""

    content_index: int
    candidate_hash: str
    platform_class: str
    context_class: str
    learned_score: float | None
    llm_score: float
    admission_threshold: float
    admission_result: bool
    features_digest: str
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
            "learned_score": self.learned_score,
            "llm_score": self.llm_score,
            "admission_threshold": self.admission_threshold,
            "admission_result": self.admission_result,
            "features_digest": self.features_digest,
        }


@dataclass(frozen=True)
class LearnedGateReport:
    """Pure verdict; it never changes runtime configuration."""

    passed: bool
    reasons: tuple[str, ...]
    total_decisions: int
    joinable_candidates: int
    spearman: float | None
    admission_delta: float | None
    coverage: float | None
    admitted_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "contract": "evaluator-learned-scorer-gate-v1",
            "passed": self.passed,
            "production_mode_change": "none",
            "required_mode_on_failure": "llm",
            "reasons": list(self.reasons),
            "counts": {
                "total_decisions": self.total_decisions,
                "joinable_candidates": self.joinable_candidates,
                "admitted_count": self.admitted_count,
            },
            "metrics": {
                "spearman": self.spearman,
                "admission_delta": self.admission_delta,
                "coverage": self.coverage,
            },
            "gate_constants": {
                "min_joinable_candidates": LEARNED_GATE_MIN_JOINABLE,
                "min_spearman": LEARNED_GATE_MIN_SPEARMAN,
                "max_admission_delta": LEARNED_GATE_MAX_ADMISSION_DELTA,
                "min_coverage": LEARNED_GATE_MIN_COVERAGE,
                "retention_days": LEARNED_AUDIT_RETENTION_DAYS,
                "max_rows": LEARNED_AUDIT_MAX_ROWS,
            },
        }


def hash_learned_candidate_identity(identity: str) -> str:
    """Hash one canonical candidate identity with an audit domain separator."""

    payload = f"openbiliclaw:evaluator-learned:v1\0{identity}".encode()
    return hashlib.sha256(payload).hexdigest()


def classify_learned_context(source_context: object, source_strategy: object) -> str:
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


def sanitize_learned_platform(value: object) -> str:
    """Return a bounded platform class, never arbitrary source text."""

    token = _CLASS_TOKEN_RE.sub("_", str(value or "").strip().lower()).strip("_")
    return token if token in LEARNED_OK_PLATFORMS else "unknown"


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
    learned = _as_finite_float(row.get("learned_score"))
    llm = _as_finite_float(row.get("llm_score"))
    threshold = _as_finite_float(row.get("admission_threshold"))
    admitted = _as_bool(row.get("admission_result"))
    if not (
        decision_id
        and _HASH_RE.fullmatch(candidate_hash)
        and platform in LEARNED_OK_PLATFORMS
        and context in LEARNED_OK_CONTEXTS
        and llm is not None
        and threshold is not None
        and admitted is not None
    ):
        return False
    if not (0.0 <= llm <= 1.0 and 0.0 <= threshold <= 1.0):
        return False
    if admitted != (llm >= threshold):
        return False
    return not (learned is not None and not (0.0 <= learned <= 1.0))


def _rank(values: Sequence[float]) -> list[float]:
    """Average-rank ranks (ties get the average of the tied positions)."""

    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        average_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = average_rank
        i = j + 1
    return ranks


def _spearman(pairs: Sequence[tuple[float, float]]) -> float | None:
    if len(pairs) < 2:
        return None
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    rx = _rank(xs)
    ry = _rank(ys)
    n = len(pairs)
    mean_x = sum(rx) / n
    mean_y = sum(ry) / n
    cov = sum((rx[i] - mean_x) * (ry[i] - mean_y) for i in range(n))
    var_x = sum((rx[i] - mean_x) ** 2 for i in range(n))
    var_y = sum((ry[i] - mean_y) ** 2 for i in range(n))
    if var_x <= 0.0 or var_y <= 0.0:
        return None
    return cov / math.sqrt(var_x * var_y)


def evaluate_learned_scorer_gate(
    rows: Sequence[Mapping[str, object]],
) -> LearnedGateReport:
    """Evaluate the learned-vs-LLM gate without mutating runtime state."""

    all_rows = list(rows)
    joinable = [row for row in all_rows if _is_joinable(row)]
    total = len(all_rows)
    admitted = sum(_as_bool(row.get("admission_result")) is True for row in joinable)

    learned_pairs = [
        (learned, llm)
        for row in joinable
        if (learned := _as_finite_float(row.get("learned_score"))) is not None
        and (llm := _as_finite_float(row.get("llm_score"))) is not None
    ]
    spearman = _spearman(learned_pairs)

    scored: list[tuple[float, float | None, float]] = []
    for row in joinable:
        llm = _as_finite_float(row.get("llm_score"))
        thr = _as_finite_float(row.get("admission_threshold"))
        if llm is None or thr is None:
            continue
        learned = _as_finite_float(row.get("learned_score"))
        scored.append((llm, learned, thr))

    admitted_by_llm = sum(1 for llm, _lrn, thr in scored if llm >= thr)
    admitted_by_learned = sum(
        1 for _llm, lrn, thr in scored if lrn is not None and lrn >= thr
    )
    admission_delta = (admitted_by_learned / admitted_by_llm - 1.0) if admitted_by_llm else None

    llm_admitted = [(llm, _lrn, thr) for llm, _lrn, thr in scored if llm >= thr]
    learned_covered = sum(
        1 for _llm, lrn, thr in llm_admitted if lrn is not None and lrn >= thr
    )
    coverage = (learned_covered / len(llm_admitted)) if llm_admitted else None

    reasons: list[str] = []
    if len(joinable) < LEARNED_GATE_MIN_JOINABLE:
        reasons.append("joinable_candidates_below_100")
    if spearman is None or spearman < LEARNED_GATE_MIN_SPEARMAN:
        reasons.append("spearman_below_0.5_or_missing")
    if admission_delta is not None and abs(admission_delta) > LEARNED_GATE_MAX_ADMISSION_DELTA:
        reasons.append("admission_delta_above_0.02")
    if coverage is not None and coverage < LEARNED_GATE_MIN_COVERAGE:
        reasons.append("coverage_below_0.9")

    return LearnedGateReport(
        passed=not reasons,
        reasons=tuple(reasons),
        total_decisions=total,
        joinable_candidates=len(joinable),
        spearman=spearman,
        admission_delta=admission_delta,
        coverage=coverage,
        admitted_count=admitted,
    )
