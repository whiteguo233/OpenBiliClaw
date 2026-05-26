"""Speculative avoidance lifecycle for proactive dislike-boundary exploration."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from openbiliclaw.llm.json_utils import DEFAULT_STRUCTURED_MAX_TOKENS, parse_llm_json_tolerant
from openbiliclaw.soul.speculator import (
    _build_event_text,
    _has_probe_term_overlap,
    _normalize_entry_load,
    _normalize_experience_mode,
    _normalize_probe_term,
    _text_matches_keywords,
    build_probe_axis,
    normalize_probe_feedback_history,
)

if TYPE_CHECKING:
    from pathlib import Path

    from openbiliclaw.soul.profile import OnionProfile

logger = logging.getLogger(__name__)


DENYING_AVOIDANCE_RESPONSES = {"reject", "chat_negative"}
CONFIRMING_AVOIDANCE_RESPONSES = {"confirm", "chat_positive"}
AVOIDANCE_SOURCE_MODE_ACTIVE_LIMIT = 2


def _normalize_avoidance_source_mode(value: object) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _avoidance_topic_markers(value: object) -> set[str]:
    text = str(value or "").strip().lower()
    normalized = _normalize_probe_term(text)
    if not normalized:
        return set()

    markers: set[str] = set()
    if re.search(r"\b(ai|aigc|llm|gpt)\b", text) or any(
        marker in normalized
        for marker in ("人工智能", "大模型", "机器学习", "深度学习", "提示词")
    ):
        markers.add("ai")
    if any(marker in normalized for marker in ("动漫", "番剧", "新番", "轻小说")):
        markers.add("anime")
    if any(marker in normalized for marker in ("游戏", "手游", "网游", "主机游戏")):
        markers.add("game")
    if any(marker in normalized for marker in ("时政", "国际", "社会新闻", "公共议题")):
        markers.add("news")
    if any(marker in normalized for marker in ("美食", "探店", "吃播")):
        markers.add("food")
    if any(marker in normalized for marker in ("营销", "带货", "广告")):
        markers.add("marketing")
    if any(marker in normalized for marker in ("标题党", "震惊体")):
        markers.add("clickbait")
    return markers


def _avoidance_domain_head(value: object) -> str:
    normalized = _normalize_probe_term(value)
    if not normalized:
        return ""
    for separator in ("里的", "中的", "里面的", "当中的", "内容", "话题"):
        if separator in normalized:
            head = normalized.split(separator, 1)[0]
            return head[:12]
    return normalized[:12]


def _avoidance_topic_keys(
    *,
    domain: object,
    source_signal: object = "",
) -> set[str]:
    markers = _avoidance_topic_markers(domain)
    if not markers:
        markers.update(_avoidance_topic_markers(source_signal))
    head = _avoidance_domain_head(domain)
    if head:
        markers.add(head)
    return markers


def _avoidance_source_topic_keys(
    *,
    domain: object,
    source_mode: object = "",
    source_signal: object = "",
) -> set[str]:
    mode = _normalize_avoidance_source_mode(source_mode)
    if not mode:
        return set()
    return {
        f"{mode}:{key}"
        for key in _avoidance_topic_keys(domain=domain, source_signal=source_signal)
    }


@dataclass
class SpeculativeAvoidanceSpecific:
    """A narrow avoided content pattern within a speculative avoidance domain."""

    name: str = ""
    confirmation_count: int = 0
    confirming_events: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "confirmation_count": self.confirmation_count,
            "confirming_events": list(self.confirming_events),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SpeculativeAvoidanceSpecific:
        return cls(
            name=str(data.get("name", "")),
            confirmation_count=int(data.get("confirmation_count", 0)),
            confirming_events=list(data.get("confirming_events") or []),
        )


@dataclass
class SpeculativeAvoidance:
    """A speculated avoidance direction awaiting confirmation."""

    domain: str = ""
    reason: str = ""
    source_mode: str = ""
    source_signal: str = ""
    experience_mode: str = ""
    entry_load: str = ""
    confidence: float = 0.4
    weight: float = 0.4
    created_at: str = ""
    ttl_days: int = 3
    confirmation_count: int = 0
    confirmation_threshold: int = 3
    status: str = "active"
    confirming_events: list[str] = field(default_factory=list)
    specifics: list[SpeculativeAvoidanceSpecific] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "reason": self.reason,
            "source_mode": self.source_mode,
            "source_signal": self.source_signal,
            "experience_mode": self.experience_mode,
            "entry_load": self.entry_load,
            "confidence": self.confidence,
            "weight": self.weight,
            "created_at": self.created_at,
            "ttl_days": self.ttl_days,
            "confirmation_count": self.confirmation_count,
            "confirmation_threshold": self.confirmation_threshold,
            "status": self.status,
            "confirming_events": list(self.confirming_events),
            "specifics": [item.to_dict() for item in self.specifics],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SpeculativeAvoidance:
        return cls(
            domain=str(data.get("domain", "")),
            reason=str(data.get("reason", "")),
            source_mode=str(data.get("source_mode", "")),
            source_signal=str(data.get("source_signal", "")),
            experience_mode=str(data.get("experience_mode", "")),
            entry_load=str(data.get("entry_load", "")),
            confidence=float(data.get("confidence", 0.4)),
            weight=float(data.get("weight", 0.4)),
            created_at=str(data.get("created_at", "")),
            ttl_days=int(data.get("ttl_days", 3)),
            confirmation_count=int(data.get("confirmation_count", 0)),
            confirmation_threshold=int(data.get("confirmation_threshold", 3)),
            status=str(data.get("status", "active")),
            confirming_events=list(data.get("confirming_events") or []),
            specifics=[
                SpeculativeAvoidanceSpecific.from_dict(item)
                for item in data.get("specifics", [])
                if isinstance(item, dict)
            ],
        )


@dataclass
class AvoidanceCooldownEntry:
    """A denied or expired avoidance candidate suppressed until cooldown ends."""

    domain: str = ""
    source_mode: str = ""
    rejected_at: str = ""
    cooldown_until: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "source_mode": self.source_mode,
            "rejected_at": self.rejected_at,
            "cooldown_until": self.cooldown_until,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AvoidanceCooldownEntry:
        return cls(
            domain=str(data.get("domain", "")),
            source_mode=str(data.get("source_mode", "")),
            rejected_at=str(data.get("rejected_at", "")),
            cooldown_until=str(data.get("cooldown_until", "")),
        )


@dataclass
class AvoidanceState:
    """Container for all speculative avoidance lifecycle state."""

    active: list[SpeculativeAvoidance] = field(default_factory=list)
    cooldown: list[AvoidanceCooldownEntry] = field(default_factory=list)
    last_generation_at: str = ""
    total_promoted: int = 0
    total_rejected: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "active": [item.to_dict() for item in self.active],
            "cooldown": [item.to_dict() for item in self.cooldown],
            "last_generation_at": self.last_generation_at,
            "total_promoted": self.total_promoted,
            "total_rejected": self.total_rejected,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AvoidanceState:
        return cls(
            active=[
                SpeculativeAvoidance.from_dict(item)
                for item in data.get("active", [])
                if isinstance(item, dict)
            ],
            cooldown=[
                AvoidanceCooldownEntry.from_dict(item)
                for item in data.get("cooldown", [])
                if isinstance(item, dict)
            ],
            last_generation_at=str(data.get("last_generation_at", "")),
            total_promoted=int(data.get("total_promoted", 0)),
            total_rejected=int(data.get("total_rejected", 0)),
        )


@dataclass
class AvoidanceTickResult:
    """Summary of one avoidance speculator tick."""

    generated: list[SpeculativeAvoidance] = field(default_factory=list)
    promoted: list[SpeculativeAvoidance] = field(default_factory=list)
    rejected: list[SpeculativeAvoidance] = field(default_factory=list)
    observed_matches: int = 0


@dataclass
class AvoidanceNoveltyGuard:
    """Local duplicate guard for speculative avoidance probes."""

    exact_terms: set[str] = field(default_factory=set)
    fuzzy_terms: set[str] = field(default_factory=set)
    source_topic_keys: set[str] = field(default_factory=set)

    @classmethod
    def from_profile_and_state(
        cls,
        profile: OnionProfile | None,
        state: AvoidanceState,
        *,
        probed_domains: set[str] | None = None,
        feedback_history: object | None = None,
    ) -> AvoidanceNoveltyGuard:
        exact_terms: set[str] = set()
        fuzzy_terms: set[str] = set()
        source_topic_keys: set[str] = set()

        def add_term(value: object, *, fuzzy: bool = True) -> None:
            text = str(value or "").strip()
            normalized = _normalize_probe_term(text)
            if not normalized:
                return
            exact_terms.add(normalized)
            if fuzzy:
                fuzzy_terms.add(text)

        def add_candidate_terms(
            domain: object,
            *,
            specifics: list[object] | None = None,
            source_mode: object = "",
            source_signal: object = "",
        ) -> None:
            add_term(domain)
            for specific in specifics or []:
                add_term(specific)
            source_topic_keys.update(
                _avoidance_source_topic_keys(
                    domain=domain,
                    source_mode=source_mode,
                    source_signal=source_signal,
                )
            )

        if profile is not None:
            interest = getattr(profile, "interest", None)
            for item in getattr(interest, "dislikes", []) or []:
                add_term(getattr(item, "domain", ""))
                for specific in getattr(item, "specifics", []) or []:
                    add_term(getattr(specific, "name", ""))
            for item in getattr(interest, "likes", []) or []:
                weight = float(getattr(item, "weight", 0.0) or 0.0)
                if weight < 0.7:
                    continue
                add_term(getattr(item, "domain", ""))
                for specific in getattr(item, "specifics", []) or []:
                    add_term(getattr(specific, "name", ""))
            preferences = getattr(profile, "preferences", None)
            for item in getattr(preferences, "disliked_topics", []) or []:
                add_term(item)

        for item in state.active:
            add_candidate_terms(
                item.domain,
                specifics=[specific.name for specific in item.specifics],
                source_mode=item.source_mode,
                source_signal=item.source_signal,
            )
        for item in state.cooldown:
            add_term(item.domain)
        for item in probed_domains or set():
            add_term(item)
        for item in normalize_probe_feedback_history(feedback_history):
            if str(item.get("response", "")).lower() not in DENYING_AVOIDANCE_RESPONSES:
                continue
            raw_specifics = item.get("specifics", [])
            specifics = raw_specifics if isinstance(raw_specifics, list) else []
            add_candidate_terms(
                item.get("domain", ""),
                specifics=specifics,
                source_mode=item.get("source_mode", ""),
                source_signal=item.get("source_signal", ""),
            )

        return cls(
            exact_terms=exact_terms,
            fuzzy_terms=fuzzy_terms,
            source_topic_keys=source_topic_keys,
        )

    def mark_seen(
        self,
        domain: str,
        *,
        specifics: list[str] | None = None,
        source_mode: str = "",
        source_signal: str = "",
    ) -> None:
        """Add an accepted avoidance candidate to this in-memory guard."""
        for value in [domain, *(specifics or [])]:
            text = str(value or "").strip()
            normalized = _normalize_probe_term(text)
            if not normalized:
                continue
            self.exact_terms.add(normalized)
            self.fuzzy_terms.add(text)
        self.source_topic_keys.update(
            _avoidance_source_topic_keys(
                domain=domain,
                source_mode=source_mode,
                source_signal=source_signal,
            )
        )

    def is_duplicate_domain(self, domain: str) -> bool:
        normalized = _normalize_probe_term(domain)
        if not normalized:
            return True
        if normalized in self.exact_terms:
            return True
        return any(_has_probe_term_overlap(domain, term) for term in self.fuzzy_terms)

    def is_duplicate_candidate(
        self,
        domain: str,
        *,
        specifics: list[str] | None = None,
        source_mode: str = "",
        source_signal: str = "",
    ) -> bool:
        if self.is_duplicate_domain(domain):
            return True
        candidate_keys = _avoidance_source_topic_keys(
            domain=domain,
            source_mode=source_mode,
            source_signal=source_signal,
        )
        return bool(candidate_keys & self.source_topic_keys)

    def filter_specifics(self, specifics: list[str]) -> list[str]:
        return [specific for specific in specifics if not self.is_duplicate_domain(specific)]


def load_avoidance_state(data_dir: Path) -> AvoidanceState:
    """Load avoidance state from disk."""
    path = data_dir / "memory" / "avoidance_state.json"
    if not path.exists():
        return AvoidanceState()
    try:
        with open(path, encoding="utf-8") as file:
            data = json.load(file)
        if isinstance(data, dict):
            return AvoidanceState.from_dict(data)
    except (json.JSONDecodeError, OSError):
        logger.debug("Failed to load avoidance state", exc_info=True)
    return AvoidanceState()


def save_avoidance_state(data_dir: Path, state: AvoidanceState) -> None:
    """Persist avoidance state to disk."""
    memory_dir = data_dir / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    with open(memory_dir / "avoidance_state.json", "w", encoding="utf-8") as file:
        json.dump(state.to_dict(), file, ensure_ascii=False, indent=2)


def promote_ready_avoidances(
    state: AvoidanceState,
) -> tuple[list[SpeculativeAvoidance], AvoidanceState]:
    """Extract avoidance candidates that are ready for external writeback."""
    promoted: list[SpeculativeAvoidance] = []
    remaining: list[SpeculativeAvoidance] = []
    for item in state.active:
        ready = (
            item.status == "active" and item.confirmation_count >= item.confirmation_threshold
        ) or item.status == "confirmed"
        if ready:
            item.status = "promoted"
            promoted.append(item)
            state.total_promoted += 1
        else:
            remaining.append(item)
    state.active = remaining
    return promoted, state


def expire_stale_avoidances(
    state: AvoidanceState,
    now: datetime,
    cooldown_days: int = 7,
) -> tuple[list[SpeculativeAvoidance], AvoidanceState]:
    """Expire stale active avoidance candidates and add cooldown entries."""
    rejected: list[SpeculativeAvoidance] = []
    remaining: list[SpeculativeAvoidance] = []
    for item in state.active:
        if item.status != "active":
            remaining.append(item)
            continue
        try:
            created = datetime.fromisoformat(item.created_at)
        except (TypeError, ValueError):
            remaining.append(item)
            continue
        if now > created + timedelta(days=item.ttl_days):
            item.status = "rejected"
            rejected.append(item)
            state.total_rejected += 1
            state.cooldown.append(
                AvoidanceCooldownEntry(
                    domain=item.domain,
                    source_mode=item.source_mode,
                    rejected_at=now.isoformat(),
                    cooldown_until=(now + timedelta(days=cooldown_days)).isoformat(),
                )
            )
        else:
            remaining.append(item)
    state.active = remaining

    valid_cooldown: list[AvoidanceCooldownEntry] = []
    for cooldown in state.cooldown:
        try:
            cooldown_until = datetime.fromisoformat(cooldown.cooldown_until)
        except (TypeError, ValueError):
            continue
        if now <= cooldown_until:
            valid_cooldown.append(cooldown)
    state.cooldown = valid_cooldown
    return rejected, state


def _avoidance_specific_names(item: SpeculativeAvoidance) -> list[str]:
    return [specific.name for specific in item.specifics if specific.name]


def _avoidance_priority(item: SpeculativeAvoidance) -> tuple[int, int, float, float]:
    return (
        int(item.confirmation_count or 0),
        len(item.specifics),
        float(item.confidence or 0.0),
        float(item.weight or 0.0),
    )


def _source_mode_counts(avoidances: list[SpeculativeAvoidance]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in avoidances:
        if item.status != "active":
            continue
        mode = _normalize_avoidance_source_mode(item.source_mode)
        if not mode:
            continue
        counts[mode] = counts.get(mode, 0) + 1
    return counts


_ALL_SOURCE_MODES = ("negative_signal", "positive_boundary", "style_boundary")


def _compute_source_mode_quota(
    active: list[SpeculativeAvoidance],
    *,
    slots: int,
    count: int,
) -> dict[str, int]:
    """Compute how many of each source_mode the LLM should generate.

    The goal: spread the ``count`` candidates across all 3 source_modes,
    biasing toward modes that are under-represented in the current active set.
    Modes already at ``AVOIDANCE_SOURCE_MODE_ACTIVE_LIMIT`` get 0.
    """
    current = _source_mode_counts(
        [item for item in active if item.status == "active"]
    )
    # Remaining capacity per mode before hitting the per-mode cap
    remaining: dict[str, int] = {}
    for mode in _ALL_SOURCE_MODES:
        remaining[mode] = max(0, AVOIDANCE_SOURCE_MODE_ACTIVE_LIMIT - current.get(mode, 0))

    total_remaining = sum(remaining.values())
    if total_remaining == 0:
        # All modes saturated — ask for 1 each anyway so quality gate can pick
        return {mode: 1 for mode in _ALL_SOURCE_MODES}

    # Distribute count proportionally to remaining capacity, minimum 1 per
    # mode that still has capacity
    quota: dict[str, int] = {}
    for mode in _ALL_SOURCE_MODES:
        if remaining[mode] <= 0:
            quota[mode] = 0
        else:
            quota[mode] = max(1, round(count * remaining[mode] / total_remaining))
    return quota


def compact_redundant_active_avoidances(
    state: AvoidanceState,
    now: datetime,
    cooldown_days: int = 7,
) -> tuple[list[SpeculativeAvoidance], AvoidanceState]:
    """Remove redundant active avoidance probes before they keep occupying slots."""
    active = [item for item in state.active if item.status == "active"]
    if len(active) < 2:
        return [], state

    guard = AvoidanceNoveltyGuard()
    mode_counts: dict[str, int] = {}
    kept_ids: set[int] = set()
    rejected: list[SpeculativeAvoidance] = []

    for item in sorted(active, key=_avoidance_priority, reverse=True):
        mode = _normalize_avoidance_source_mode(item.source_mode)
        if guard.is_duplicate_candidate(
            item.domain,
            specifics=_avoidance_specific_names(item),
            source_mode=mode,
            source_signal=item.source_signal,
        ):
            rejected.append(item)
            continue
        if mode and mode_counts.get(mode, 0) >= AVOIDANCE_SOURCE_MODE_ACTIVE_LIMIT:
            rejected.append(item)
            continue
        kept_ids.add(id(item))
        if mode:
            mode_counts[mode] = mode_counts.get(mode, 0) + 1
        guard.mark_seen(
            item.domain,
            specifics=_avoidance_specific_names(item),
            source_mode=mode,
            source_signal=item.source_signal,
        )

    if not rejected:
        return [], state

    rejected_ids = {id(item) for item in rejected}
    state.active = [
        item
        for item in state.active
        if item.status != "active" or id(item) in kept_ids or id(item) not in rejected_ids
    ]
    for item in rejected:
        item.status = "rejected"
        state.total_rejected += 1
        state.cooldown.append(
            AvoidanceCooldownEntry(
                domain=item.domain,
                source_mode=item.source_mode,
                rejected_at=now.isoformat(),
                cooldown_until=(now + timedelta(days=cooldown_days)).isoformat(),
            )
        )

    logger.info(
        "AvoidanceSpeculator compacted %d redundant active avoidance(s): %s",
        len(rejected),
        [item.domain for item in rejected],
    )
    return rejected, state


def _select_diverse_avoidance_candidates(
    candidates: list[SpeculativeAvoidance],
    *,
    slots: int,
    existing: list[SpeculativeAvoidance],
) -> list[SpeculativeAvoidance]:
    if slots <= 0:
        return []

    selected: list[SpeculativeAvoidance] = []
    guard = AvoidanceNoveltyGuard()
    for item in existing:
        if item.status != "active":
            continue
        guard.mark_seen(
            item.domain,
            specifics=_avoidance_specific_names(item),
            source_mode=item.source_mode,
            source_signal=item.source_signal,
        )
    mode_counts = _source_mode_counts(existing)

    ordered = sorted(candidates, key=lambda item: (item.confidence, item.weight), reverse=True)
    for candidate in ordered:
        mode = _normalize_avoidance_source_mode(candidate.source_mode)
        if guard.is_duplicate_candidate(
            candidate.domain,
            specifics=_avoidance_specific_names(candidate),
            source_mode=mode,
            source_signal=candidate.source_signal,
        ):
            continue
        if mode and mode_counts.get(mode, 0) >= AVOIDANCE_SOURCE_MODE_ACTIVE_LIMIT:
            continue
        selected.append(candidate)
        if mode:
            mode_counts[mode] = mode_counts.get(mode, 0) + 1
        guard.mark_seen(
            candidate.domain,
            specifics=_avoidance_specific_names(candidate),
            source_mode=mode,
            source_signal=candidate.source_signal,
        )
        if len(selected) >= slots:
            break
    return selected


def _is_explicit_negative_event(event: dict[str, Any]) -> bool:
    metadata = event.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    feedback_type = str(metadata.get("feedback_type", "")).strip().lower()
    reaction = str(metadata.get("reaction", "")).strip().lower()
    event_type = str(event.get("event_type", "")).strip().lower()
    return feedback_type == "dislike" or reaction == "thumbs_down" or event_type == "dislike"


def _event_matches_avoidance(event: dict[str, Any], item: SpeculativeAvoidance) -> bool:
    event_text = _build_event_text(event)
    if _text_matches_keywords(event_text, item.domain):
        return True
    return any(_text_matches_keywords(event_text, specific.name) for specific in item.specifics)


def observe_avoidance_events(
    events: list[dict[str, Any]],
    state: AvoidanceState,
) -> tuple[AvoidanceState, int]:
    """Observe explicit negative evidence against active avoidance candidates."""
    match_count = 0
    for event in events:
        if not isinstance(event, dict) or not _is_explicit_negative_event(event):
            continue
        event_text = _build_event_text(event)
        title_short = str(event.get("title", ""))[:80]
        for item in state.active:
            if item.status != "active" or not _event_matches_avoidance(event, item):
                continue
            item.confirmation_count += 1
            if title_short:
                item.confirming_events.append(title_short)
            for specific in item.specifics:
                if _text_matches_keywords(event_text, specific.name):
                    specific.confirmation_count += 1
                    if title_short:
                        specific.confirming_events.append(title_short)
            match_count += 1
    return state, match_count


def _denied_avoidance_domains(feedback_history: object) -> list[str]:
    return [
        str(item.get("domain", ""))
        for item in normalize_probe_feedback_history(feedback_history)
        if str(item.get("response", "")).lower() in DENYING_AVOIDANCE_RESPONSES
        and str(item.get("domain", "")).strip()
    ]


def _denied_avoidance_axes(feedback_history: object) -> set[str]:
    return {
        str(item.get("axis", "")).strip()
        for item in normalize_probe_feedback_history(feedback_history)
        if str(item.get("response", "")).lower() in DENYING_AVOIDANCE_RESPONSES
        and str(item.get("axis", "")).strip()
    }


def choose_next_avoidance_candidate(
    avoidances: list[Any],
    *,
    probed_domains: set[str] | None = None,
    probed_axes: set[str] | None = None,
    feedback_history: object | None = None,
) -> Any | None:
    """Choose the next avoidance probe to surface."""
    recent_domains = probed_domains or set()
    recent_axes = probed_axes or set()
    denied_domains = _denied_avoidance_domains(feedback_history)
    denied_axes = _denied_avoidance_axes(feedback_history)
    candidates: list[Any] = []
    for item in avoidances:
        domain = str(getattr(item, "domain", "")).strip().lower()
        if not domain or domain in recent_domains:
            continue
        if any(_has_probe_term_overlap(domain, denied) for denied in denied_domains):
            continue
        candidates.append(item)
    if not candidates:
        return None

    min_confirmation = min(int(getattr(item, "confirmation_count", 0) or 0) for item in candidates)
    same_pressure = [
        item
        for item in candidates
        if int(getattr(item, "confirmation_count", 0) or 0) == min_confirmation
    ]
    fresh_axis = [
        item
        for item in same_pressure
        if (
            axis := build_probe_axis(
                experience_mode=getattr(item, "experience_mode", ""),
                entry_load=getattr(item, "entry_load", ""),
            )
        )
        and axis not in recent_axes
    ]
    pool = fresh_axis or same_pressure
    return max(
        pool,
        key=lambda item: (
            build_probe_axis(
                experience_mode=getattr(item, "experience_mode", ""),
                entry_load=getattr(item, "entry_load", ""),
            )
            not in denied_axes,
            float(getattr(item, "weight", 0.0) or 0.0),
            float(getattr(item, "confidence", 0.0) or 0.0),
        ),
    )


def _parse_avoidance_generation_response(content: str) -> list[dict[str, Any]]:
    """Extract avoidance candidates from an LLM response."""
    data = parse_llm_json_tolerant(content)
    if isinstance(data, dict):
        avoidances = data.get("avoidances", [])
        if isinstance(avoidances, list):
            return [item for item in avoidances if isinstance(item, dict)]
        return []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


class AvoidanceSpeculator:
    """IO boundary for speculative avoidance lifecycle state."""

    def __init__(
        self,
        *,
        llm_service: Any | None,
        data_dir: Path | None,
        generation_interval_minutes: int = 10,
        default_ttl_days: int = 3,
        cooldown_days: int = 7,
        confirmation_threshold: int = 3,
        max_active: int = 5,
    ) -> None:
        self._llm_service = llm_service
        self._data_dir = data_dir
        self._generation_interval_minutes = generation_interval_minutes
        self._default_ttl_days = default_ttl_days
        self._cooldown_days = cooldown_days
        self._confirmation_threshold = confirmation_threshold
        self._max_active = max_active

    def _load_state(self) -> AvoidanceState:
        if self._data_dir is None:
            return AvoidanceState()
        return load_avoidance_state(self._data_dir)

    def _save_state(self, state: AvoidanceState) -> None:
        if self._data_dir is None:
            return
        save_avoidance_state(self._data_dir, state)

    def get_active_avoidances(self) -> list[SpeculativeAvoidance]:
        state = self._load_state()
        return [item for item in state.active if item.status == "active"]

    def user_confirm_avoidance(self, domain: str) -> SpeculativeAvoidance | None:
        """User explicitly confirmed an avoidance; remove it from active state."""
        state = self._load_state()
        remaining: list[SpeculativeAvoidance] = []
        confirmed: SpeculativeAvoidance | None = None
        for item in state.active:
            if item.domain.lower() == domain.lower() and item.status == "active":
                item.status = "promoted"
                item.confirmation_count = item.confirmation_threshold
                item.confirming_events.append("user_confirmed")
                confirmed = item
                state.total_promoted += 1
            else:
                remaining.append(item)
        if confirmed is not None:
            state.active = remaining
            self._save_state(state)
        return confirmed

    def user_reject_avoidance(self, domain: str, cooldown_days: int = 30) -> bool:
        """User rejected an avoidance hypothesis; move it to cooldown."""
        state = self._load_state()
        remaining: list[SpeculativeAvoidance] = []
        found = False
        now = datetime.now()
        for item in state.active:
            if item.domain.lower() == domain.lower() and item.status == "active":
                item.status = "rejected"
                state.total_rejected += 1
                state.cooldown.append(
                    AvoidanceCooldownEntry(
                        domain=item.domain,
                        source_mode=item.source_mode,
                        rejected_at=now.isoformat(),
                        cooldown_until=(now + timedelta(days=cooldown_days)).isoformat(),
                    )
                )
                found = True
            else:
                remaining.append(item)
        state.active = remaining
        if found:
            self._save_state(state)
        return found

    def observe(self, events: list[dict[str, Any]]) -> int:
        if not events:
            return 0
        state = self._load_state()
        if not any(item.status == "active" for item in state.active):
            return 0
        state, match_count = observe_avoidance_events(events, state)
        if match_count:
            self._save_state(state)
        return match_count

    async def tick(
        self,
        profile: OnionProfile,
        *,
        feedback_history: object | None = None,
    ) -> AvoidanceTickResult:
        now = datetime.now()
        state = self._load_state()
        result = AvoidanceTickResult()

        rejected, state = expire_stale_avoidances(state, now, self._cooldown_days)
        result.rejected = rejected
        promoted, state = promote_ready_avoidances(state)
        result.promoted = promoted
        compacted, state = compact_redundant_active_avoidances(
            state,
            now,
            self._cooldown_days,
        )
        result.rejected.extend(compacted)
        if result.promoted or result.rejected:
            self._save_state(state)

        if self._should_generate(state, now):
            pre_active_domains = {item.domain for item in state.active if item.status == "active"}
            state = await self._generate(profile, state, now, feedback_history=feedback_history)
            result.generated = [
                item
                for item in state.active
                if item.status == "active" and item.domain not in pre_active_domains
            ]

        self._save_state(state)

        if result.promoted:
            logger.info(
                "AvoidanceSpeculator promoted %d avoidances: %s",
                len(result.promoted),
                [item.domain for item in result.promoted],
            )
        if result.rejected:
            logger.info(
                "AvoidanceSpeculator rejected %d avoidances: %s",
                len(result.rejected),
                [item.domain for item in result.rejected],
            )
        if result.generated:
            logger.info(
                "AvoidanceSpeculator generated %d new avoidances: %s",
                len(result.generated),
                [item.domain for item in result.generated],
            )

        return result

    async def force_tick(
        self,
        profile: OnionProfile,
        *,
        feedback_history: object | None = None,
    ) -> AvoidanceTickResult:
        now = datetime.now()
        state = self._load_state()
        result = AvoidanceTickResult()

        rejected, state = expire_stale_avoidances(state, now, self._cooldown_days)
        result.rejected = rejected
        promoted, state = promote_ready_avoidances(state)
        result.promoted = promoted
        compacted, state = compact_redundant_active_avoidances(
            state,
            now,
            self._cooldown_days,
        )
        result.rejected.extend(compacted)
        if result.promoted or result.rejected:
            self._save_state(state)

        active_count = sum(1 for item in state.active if item.status == "active")
        if active_count < self._max_active and self._llm_service is not None:
            pre_active_domains = {item.domain for item in state.active if item.status == "active"}
            state = await self._generate(profile, state, now, feedback_history=feedback_history)
            result.generated = [
                item
                for item in state.active
                if item.status == "active" and item.domain not in pre_active_domains
            ]

        self._save_state(state)

        if result.generated or result.promoted or result.rejected:
            logger.info(
                "AvoidanceSpeculator force_tick: generated=%d, promoted=%d, rejected=%d",
                len(result.generated),
                len(result.promoted),
                len(result.rejected),
            )
        else:
            logger.debug(
                "AvoidanceSpeculator force_tick: no-op (active=%d/%d)",
                active_count,
                self._max_active,
            )

        return result

    def _should_generate(self, state: AvoidanceState, now: datetime) -> bool:
        if self._llm_service is None:
            return False
        if sum(1 for item in state.active if item.status == "active") >= self._max_active:
            return False
        if not state.last_generation_at:
            return True
        try:
            last = datetime.fromisoformat(state.last_generation_at)
        except (TypeError, ValueError):
            return True
        return now - last >= timedelta(minutes=self._generation_interval_minutes)

    async def _generate(
        self,
        profile: OnionProfile,
        state: AvoidanceState,
        now: datetime,
        *,
        feedback_history: object | None = None,
    ) -> AvoidanceState:
        from openbiliclaw.llm.prompts import build_avoidance_generation_prompt

        llm_service = self._llm_service
        if llm_service is None:
            return state

        slots = self._max_active - sum(1 for item in state.active if item.status == "active")
        if slots <= 0:
            return state

        to_context = getattr(profile, "to_llm_context", None)
        profile_summary: dict[str, object] = to_context() if callable(to_context) else {}

        interest = getattr(profile, "interest", None)
        confirmed_dislikes = [
            str(getattr(item, "domain", "")).strip()
            for item in getattr(interest, "dislikes", []) or []
            if str(getattr(item, "domain", "")).strip()
        ]
        confirmed_likes = [
            str(getattr(item, "domain", "")).strip()
            for item in getattr(interest, "likes", []) or []
            if str(getattr(item, "domain", "")).strip()
        ]
        active_items = [item for item in state.active if item.status == "active"]
        existing_avoidance_details: list[dict[str, object]] = [
            {
                "domain": item.domain,
                "source_mode": item.source_mode,
                "source_signal": item.source_signal,
                "experience_mode": item.experience_mode,
                "entry_load": item.entry_load,
                "specifics": [specific.name for specific in item.specifics],
            }
            for item in active_items
        ]
        source_mode_quota = _compute_source_mode_quota(
            active_items, slots=slots, count=min(max(slots * 2, 5), 7)
        )
        messages = build_avoidance_generation_prompt(
            profile_summary=profile_summary,
            existing_avoidances=[item.domain for item in state.active],
            existing_avoidance_details=existing_avoidance_details,
            cooldown_domains=[item.domain for item in state.cooldown],
            confirmed_dislikes=confirmed_dislikes,
            confirmed_likes=confirmed_likes,
            count=min(max(slots * 2, 5), 7),
            source_mode_quota=source_mode_quota,
        )

        try:
            response = await llm_service.complete_structured_task(
                system_instruction=messages[0]["content"],
                user_input=messages[1]["content"],
                max_tokens=DEFAULT_STRUCTURED_MAX_TOKENS,
                caller="soul.avoidance_speculate",
            )
            raw = _parse_avoidance_generation_response(response.content)
        except Exception:
            logger.warning("Avoidance generation failed", exc_info=True)
            return state

        guard = AvoidanceNoveltyGuard.from_profile_and_state(
            profile,
            state,
            feedback_history=feedback_history,
        )
        existing_domains = {item.domain.lower() for item in state.active}
        candidates: list[SpeculativeAvoidance] = []
        rejected_reasons: list[str] = []
        for item in raw:
            domain = str(item.get("domain", "")).strip()
            if not domain or domain.lower() in existing_domains:
                continue
            reason = str(item.get("reason", "")).strip()
            if len(reason) < 20:
                rejected_reasons.append(f"{domain} (reason<20chars)")
                continue
            raw_specifics = item.get("specifics") or []
            specific_names = [
                str(specific).strip()
                for specific in raw_specifics
                if isinstance(specific, str) and str(specific).strip()
            ]
            source_mode = str(item.get("source_mode", "")).strip()
            source_signal = str(item.get("source_signal", "")).strip()
            if guard.is_duplicate_candidate(
                domain,
                specifics=specific_names,
                source_mode=source_mode,
                source_signal=source_signal,
            ):
                rejected_reasons.append(f"{domain} (duplicate coverage)")
                continue
            specific_names = guard.filter_specifics(specific_names)
            specifics = [SpeculativeAvoidanceSpecific(name=name) for name in specific_names]
            if len(specifics) < 2:
                rejected_reasons.append(f"{domain} (specifics<2)")
                continue
            confidence = float(item.get("confidence", 0.4))
            if confidence < 0.3:
                rejected_reasons.append(f"{domain} (conf={confidence:.2f}<0.3)")
                continue
            candidates.append(
                SpeculativeAvoidance(
                    domain=domain,
                    reason=reason,
                    source_mode=source_mode,
                    source_signal=source_signal,
                    experience_mode=_normalize_experience_mode(item.get("experience_mode")),
                    entry_load=_normalize_entry_load(item.get("entry_load")),
                    confidence=confidence,
                    weight=confidence,
                    created_at=now.isoformat(),
                    ttl_days=self._default_ttl_days,
                    confirmation_threshold=self._confirmation_threshold,
                    specifics=specifics,
                )
            )

        if rejected_reasons:
            logger.info(
                "AvoidanceSpeculator quality gate dropped %d candidate(s): %s",
                len(rejected_reasons),
                "; ".join(rejected_reasons),
            )

        selected_candidates = _select_diverse_avoidance_candidates(
            candidates,
            slots=slots,
            existing=[item for item in state.active if item.status == "active"],
        )
        for candidate in selected_candidates:
            state.active.append(candidate)
            existing_domains.add(candidate.domain.lower())
        if selected_candidates:
            state.last_generation_at = now.isoformat()
        return state
