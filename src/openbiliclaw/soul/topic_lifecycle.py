"""Topic lifecycle state machine (Phase 4 / Task 8).

Interests carry lifecycle metadata layered on top of the existing
``{name, category, weight, first_seen, last_seen, source}`` shape:

- ``state`` ∈ {``trial``, ``active``, ``decaying``, ``archived``}
- ``evidence_count`` — how many times behavioural evidence reinforced it
- ``last_evidence_at`` — ISO timestamp of the most recent evidence
- ``parent_topic`` — the domain a subdivided child was split from (shadow only)

Flow: a brand-new topic enters as ``trial``; sustained evidence promotes it to
``active``; a long silence decays (weight halved) then archives it; fresh
evidence on an archived topic re-ignites it straight back to ``active``.
Dislikes archive the matching topic rather than deleting it. Archived topics
are never removed — they are excluded from the LLM-facing serialization only
when ``topic_lifecycle_serialization`` is on.

Calibration provenance (pitfall rule #3): every threshold below is a
FIRST-ROUND default. Re-open calibration after the first production month of
real profiles and after any provider/model swap that changes how much
behavioural evidence a topic accrues per 12h window.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

# -- States -------------------------------------------------------------------

TRIAL = "trial"
ACTIVE = "active"
DECAYING = "decaying"
ARCHIVED = "archived"

VALID_STATES = frozenset({TRIAL, ACTIVE, DECAYING, ARCHIVED})

# -- Thresholds (first-round calibration; see module docstring) ---------------

# A trial topic graduates once it has been reinforced 5 times: fewer hits than
# this is indistinguishable from a one-off click or an accidental dwell.
_TRIAL_EVIDENCE_THRESHOLD = 5
# ...or once it has simply persisted for 7 days with at least one reinforcement
# — a topic that keeps showing up for a week is real even below the hit count.
_TRIAL_DURATION_DAYS = 7
# A month of silence drops an active topic to ``decaying`` and halves its
# weight: recoverable, but no longer competing at full strength for prompt slots.
_DECAY_AFTER_DAYS = 30
_DECAY_WEIGHT_FACTOR = 0.5
# Another silent month after decaying archives it (60 days silent total).
_ARCHIVE_AFTER_DECAY_DAYS = 30
# A child sub-topic that dominates ≥60% of its parent domain's weight is a
# subdivision candidate — recorded as a shadow proposal only, never executed.
_SUBDIVISION_RATIO = 0.6


@dataclass
class LifecycleTransition:
    """One state change, recorded to the ledger by the caller."""

    name: str
    from_state: str
    to_state: str
    reason: str


@dataclass
class SubdivisionProposal:
    """A shadow-only proposal to split a dominant child out of its parent."""

    child: str
    parent: str
    ratio: float


# -- Field helpers (compat-safe reads) ----------------------------------------


def get_state(item: dict[str, Any]) -> str:
    """Read the lifecycle state, defaulting legacy rows (no field) to active."""
    state = str(item.get("state", "") or "").strip().lower()
    return state if state in VALID_STATES else ACTIVE


def _evidence_count(item: dict[str, Any]) -> int:
    raw = item.get("evidence_count", 0)
    if isinstance(raw, bool):
        return int(raw)
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            return 0
    return 0


def _key(item: dict[str, Any]) -> tuple[str, str]:
    name = str(item.get("name", "")).strip().lower()
    category = str(item.get("category", "")).strip().lower()
    return (name, category)


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _coerce_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _silence_reference(item: dict[str, Any]) -> datetime | None:
    """Most recent moment we saw *evidence* for this topic (decay clock).

    Keyed strictly on ``last_evidence_at`` — the evidence clock only starts once
    :func:`apply_evidence` reinforces a topic. Interests that predate the
    lifecycle feature (no ``last_evidence_at``) therefore never decay until they
    receive their first tracked reinforcement, so enabling the feature never
    mass-decays an existing profile on its first 12h scan (``无证据`` = we have
    no evidence timestamp to measure silence against yet).
    """
    return _parse_dt(item.get("last_evidence_at"))


# -- Evidence path (called from the profile write points) ---------------------


def apply_evidence(
    existing: list[dict[str, Any]],
    updated: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    evidence_keys: set[tuple[str, str]] | None = None,
) -> tuple[list[dict[str, Any]], list[LifecycleTransition]]:
    """Overlay lifecycle metadata onto a freshly analysed interest list.

    ``updated`` is the analyzer's new interest list (which already merged
    weights against ``existing``); this carries the lifecycle fields forward
    from ``existing`` by ``(name, category)`` key. By default each supplied
    topic counts as evidence for compatibility with direct callers. Merge
    pipelines that pass a full retained snapshot must provide ``evidence_keys``
    so untouched topics only preserve their lifecycle metadata. New topics
    always enter as ``trial``. Returns the mutated list and the transitions.
    """
    current = now or datetime.now()
    now_iso = current.isoformat()
    prior = {_key(item): item for item in existing if isinstance(item, dict)}

    result: list[dict[str, Any]] = []
    transitions: list[LifecycleTransition] = []
    for raw in updated:
        if not isinstance(raw, dict):
            result.append(raw)
            continue
        item = dict(raw)
        name = str(item.get("name", "")).strip()
        old = prior.get(_key(item))
        if old is None:
            # Brand-new topic → trial entry.
            item["state"] = TRIAL
            item["evidence_count"] = 1
            item["last_evidence_at"] = now_iso
            item.setdefault("parent_topic", str(raw.get("parent_topic", "")))
            transitions.append(LifecycleTransition(name, "", TRIAL, "new topic entered trial"))
            result.append(item)
            continue

        if evidence_keys is not None and _key(item) not in evidence_keys:
            for field in ("state", "evidence_count", "last_evidence_at", "parent_topic"):
                if field in old:
                    item[field] = old[field]
            if (
                not str(item.get("first_seen", "")).strip()
                and str(old.get("first_seen", "")).strip()
            ):
                item["first_seen"] = old["first_seen"]
            result.append(item)
            continue

        prev_state = get_state(old)
        count = _evidence_count(old) + 1
        item["evidence_count"] = count
        item["last_evidence_at"] = now_iso
        item["parent_topic"] = str(old.get("parent_topic", "") or item.get("parent_topic", ""))
        # Preserve the original first_seen so trial-duration promotion works.
        if not str(item.get("first_seen", "")).strip() and str(old.get("first_seen", "")).strip():
            item["first_seen"] = old["first_seen"]

        new_state = prev_state
        reason = ""
        if prev_state in (ARCHIVED, DECAYING):
            # Fresh evidence re-ignites a dormant topic straight to active.
            new_state = ACTIVE
            reason = "evidence revived dormant topic"
        elif prev_state == TRIAL:
            first_seen = _parse_dt(old.get("first_seen")) or _parse_dt(item.get("first_seen"))
            sustained = first_seen is not None and current - first_seen >= timedelta(
                days=_TRIAL_DURATION_DAYS
            )
            if count >= _TRIAL_EVIDENCE_THRESHOLD:
                new_state = ACTIVE
                reason = f"trial reached {_TRIAL_EVIDENCE_THRESHOLD} evidence hits"
            elif sustained:
                new_state = ACTIVE
                reason = f"trial persisted {_TRIAL_DURATION_DAYS}+ days"

        item["state"] = new_state
        if new_state != prev_state:
            transitions.append(LifecycleTransition(name, prev_state, new_state, reason))
        result.append(item)
    return result, transitions


def changed_interest_keys(
    existing: list[dict[str, Any]],
    updated: list[dict[str, Any]],
) -> set[tuple[str, str]]:
    """Return topics that are new or received fresh evidence in a merged snapshot."""
    prior = {_key(item): item for item in existing if isinstance(item, dict)}
    changed: set[tuple[str, str]] = set()
    for item in updated:
        if not isinstance(item, dict):
            continue
        key = _key(item)
        old = prior.get(key)
        if old is None or str(item.get("last_seen", "")) != str(old.get("last_seen", "")):
            changed.add(key)
    return changed


# -- Time-based scan (called from the 12h consolidation) ----------------------


def scan_lifecycle(
    interests: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[LifecycleTransition]]:
    """Apply the time-driven transitions (decay, archive, trial graduation).

    Mutates a copy of each interest dict. ``active`` topics silent for
    ``_DECAY_AFTER_DAYS`` drop to ``decaying`` with weight halved; ``decaying``
    topics silent for a further ``_ARCHIVE_AFTER_DECAY_DAYS`` are archived;
    ``trial`` topics that have persisted ``_TRIAL_DURATION_DAYS`` graduate to
    ``active``. Returns the new list and the transitions for the ledger.
    """
    current = now or datetime.now()
    result: list[dict[str, Any]] = []
    transitions: list[LifecycleTransition] = []
    for raw in interests:
        if not isinstance(raw, dict):
            result.append(raw)
            continue
        item = dict(raw)
        name = str(item.get("name", "")).strip()
        state = get_state(item)
        ref = _silence_reference(item)
        silent_days = (current - ref).total_seconds() / 86400.0 if ref is not None else None

        if state == TRIAL:
            first_seen = _parse_dt(item.get("first_seen"))
            if (
                first_seen is not None
                and current - first_seen >= timedelta(days=_TRIAL_DURATION_DAYS)
                and _evidence_count(item) >= 1
            ):
                item["state"] = ACTIVE
                transitions.append(
                    LifecycleTransition(
                        name, TRIAL, ACTIVE, f"trial persisted {_TRIAL_DURATION_DAYS}+ days"
                    )
                )
        elif state == ACTIVE:
            if silent_days is not None and silent_days >= _DECAY_AFTER_DAYS:
                item["state"] = DECAYING
                item["weight"] = round(_coerce_float(item.get("weight")) * _DECAY_WEIGHT_FACTOR, 4)
                transitions.append(
                    LifecycleTransition(
                        name, ACTIVE, DECAYING, f"silent {_DECAY_AFTER_DAYS}+ days (weight x0.5)"
                    )
                )
        elif state == DECAYING:
            if (
                silent_days is not None
                and silent_days >= _DECAY_AFTER_DAYS + _ARCHIVE_AFTER_DECAY_DAYS
            ):
                item["state"] = ARCHIVED
                transitions.append(
                    LifecycleTransition(
                        name,
                        DECAYING,
                        ARCHIVED,
                        f"silent {_DECAY_AFTER_DAYS + _ARCHIVE_AFTER_DECAY_DAYS}+ days",
                    )
                )
        result.append(item)
    return result, transitions


def archive_topics(
    interests: list[dict[str, Any]],
    topics: list[str],
    *,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[LifecycleTransition]]:
    """Archive (never delete) interests matching newly-disliked ``topics``.

    Used by the dislike path: a disliked topic is archived + added to the
    avoid list (避雷) elsewhere, so the interest survives for audit/revert but
    stops competing for prompt slots.
    """
    wanted = {str(t).strip().lower() for t in topics if str(t).strip()}
    if not wanted:
        return interests, []
    result: list[dict[str, Any]] = []
    transitions: list[LifecycleTransition] = []
    for raw in interests:
        if not isinstance(raw, dict):
            result.append(raw)
            continue
        item = dict(raw)
        name = str(item.get("name", "")).strip()
        if name.lower() in wanted and get_state(item) != ARCHIVED:
            prev = get_state(item)
            item["state"] = ARCHIVED
            transitions.append(
                LifecycleTransition(name, prev, ARCHIVED, "disliked topic archived (避雷)")
            )
        result.append(item)
    return result, transitions


def propose_subdivisions(interests: list[dict[str, Any]]) -> list[SubdivisionProposal]:
    """Shadow-only: child specifics dominating ≥60% of their parent's weight.

    Returns proposals for the ledger; the caller records them but never
    restructures the interest tree (subdivision execution is out of scope).
    """
    domain_weight: dict[str, float] = {}
    for item in interests:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        category = str(item.get("category", "")).strip()
        if name and category and name == category:
            domain_weight[category.lower()] = _coerce_float(item.get("weight"))

    proposals: list[SubdivisionProposal] = []
    for item in interests:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        category = str(item.get("category", "")).strip()
        if not name or not category or name == category:
            continue
        parent_weight = domain_weight.get(category.lower())
        if parent_weight is None or parent_weight <= 0:
            continue
        ratio = _coerce_float(item.get("weight")) / parent_weight
        if ratio >= _SUBDIVISION_RATIO:
            proposals.append(SubdivisionProposal(name, category, round(ratio, 4)))
    return proposals
