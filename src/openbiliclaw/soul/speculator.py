"""Speculative Interest Lifecycle — proactive interest boundary exploration.

Periodically generates speculative interest directions via LLM, tracks
confirmation through user events, and promotes or rejects them with cooldown.

Lifecycle: Generate → Active → Promote (confirmed) / Reject + Cooldown (expired)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from openbiliclaw.llm.json_utils import (
    DEFAULT_STRUCTURED_MAX_TOKENS,
    parse_llm_json_tolerant,
)

if TYPE_CHECKING:
    from pathlib import Path

    from openbiliclaw.soul.profile import OnionProfile

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class SpeculativeSpecific:
    """A narrow interest topic within a speculative domain."""

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
    def from_dict(cls, data: dict[str, Any]) -> SpeculativeSpecific:
        return cls(
            name=str(data.get("name", "")),
            confirmation_count=int(data.get("confirmation_count", 0)),
            confirming_events=list(data.get("confirming_events") or []),
        )


@dataclass
class SpeculativeInterest:
    """A speculated interest direction (domain) with optional specifics."""

    domain: str = ""
    category: str = ""
    reason: str = ""
    experience_mode: str = ""
    entry_load: str = ""
    confidence: float = 0.4
    weight: float = 0.4
    created_at: str = ""
    ttl_days: int = 14
    confirmation_count: int = 0
    confirmation_threshold: int = 3
    status: str = "active"  # "active" | "promoted" | "rejected"
    confirming_events: list[str] = field(default_factory=list)
    specifics: list[SpeculativeSpecific] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "category": self.category,
            "reason": self.reason,
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
            "specifics": [s.to_dict() for s in self.specifics],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SpeculativeInterest:
        return cls(
            domain=str(data.get("domain", "")),
            category=str(data.get("category", "")),
            reason=str(data.get("reason", "")),
            experience_mode=str(data.get("experience_mode", "")),
            entry_load=str(data.get("entry_load", "")),
            confidence=float(data.get("confidence", 0.4)),
            weight=float(data.get("weight", 0.4)),
            created_at=str(data.get("created_at", "")),
            ttl_days=int(data.get("ttl_days", 14)),
            confirmation_count=int(data.get("confirmation_count", 0)),
            confirmation_threshold=int(data.get("confirmation_threshold", 3)),
            status=str(data.get("status", "active")),
            confirming_events=list(data.get("confirming_events") or []),
            specifics=[
                SpeculativeSpecific.from_dict(s)
                for s in (data.get("specifics") or [])
                if isinstance(s, dict)
            ],
        )


@dataclass
class CooldownEntry:
    """A rejected speculation that should not be re-guessed for a while."""

    domain: str = ""
    category: str = ""
    rejected_at: str = ""
    cooldown_until: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "category": self.category,
            "rejected_at": self.rejected_at,
            "cooldown_until": self.cooldown_until,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CooldownEntry:
        return cls(
            domain=str(data.get("domain", "")),
            category=str(data.get("category", "")),
            rejected_at=str(data.get("rejected_at", "")),
            cooldown_until=str(data.get("cooldown_until", "")),
        )


@dataclass
class SpeculativeState:
    """Container for all speculative interest lifecycle state."""

    active: list[SpeculativeInterest] = field(default_factory=list)
    cooldown: list[CooldownEntry] = field(default_factory=list)
    last_generation_at: str = ""
    total_promoted: int = 0
    total_rejected: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "active": [s.to_dict() for s in self.active],
            "cooldown": [c.to_dict() for c in self.cooldown],
            "last_generation_at": self.last_generation_at,
            "total_promoted": self.total_promoted,
            "total_rejected": self.total_rejected,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SpeculativeState:
        return cls(
            active=[
                SpeculativeInterest.from_dict(s)
                for s in (data.get("active") or [])
                if isinstance(s, dict)
            ],
            cooldown=[
                CooldownEntry.from_dict(c)
                for c in (data.get("cooldown") or [])
                if isinstance(c, dict)
            ],
            last_generation_at=str(data.get("last_generation_at", "")),
            total_promoted=int(data.get("total_promoted", 0)),
            total_rejected=int(data.get("total_rejected", 0)),
        )


@dataclass
class SpeculatorTickResult:
    """Summary of what happened during a speculator tick."""

    generated: list[SpeculativeInterest] = field(default_factory=list)
    promoted: list[SpeculativeInterest] = field(default_factory=list)
    rejected: list[SpeculativeInterest] = field(default_factory=list)
    observed_matches: int = 0


# ---------------------------------------------------------------------------
# Observation (keyword matching, no LLM)
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> set[str]:
    """Extract meaningful tokens from text for matching."""
    # Split on common delimiters, keep tokens >= 2 chars
    tokens: set[str] = set()
    for part in text.replace("·", " ").replace("、", " ").replace("/", " ").split():
        cleaned = part.strip().lower()
        if len(cleaned) >= 2:
            tokens.add(cleaned)
    return tokens


def _split_chinese_keywords(text: str) -> list[str]:
    """Split Chinese text into keyword segments by common delimiters."""
    import re

    # Split on conjunctions, punctuation, and particles
    parts = re.split(r"[与和·、/\s及]+", text)
    return [p.strip() for p in parts if len(p.strip()) >= 2]


def _build_event_text(event: dict[str, Any]) -> str:
    """Extract searchable text from an event."""
    title = str(event.get("title", "")).lower()
    tags = str(event.get("tags", "")).lower()
    category = str(event.get("category", "")).lower()
    return f"{title} {tags} {category}"


def _text_matches_keywords(event_text: str, name: str, category: str = "") -> bool:
    """Check if event_text matches a name/category via substring or token overlap."""
    name_lower = name.lower()
    cat_lower = category.lower()

    if name_lower and name_lower in event_text:
        return True
    if cat_lower and len(cat_lower) >= 2 and cat_lower in event_text:
        return True

    for keyword in _split_chinese_keywords(name):
        if keyword.lower() in event_text:
            return True

    spec_tokens = _tokenize(name) | _tokenize(category)
    if not spec_tokens:
        return False
    event_tokens = _tokenize(event_text)
    return len(spec_tokens & event_tokens) >= 2


def _event_matches_speculation(
    event: dict[str, Any],
    spec: SpeculativeInterest,
) -> bool:
    """Check if an event matches a speculative interest at domain level."""
    event_text = _build_event_text(event)
    return _text_matches_keywords(event_text, spec.domain, spec.category)


def _event_matches_specific(
    event_text: str,
    specific: SpeculativeSpecific,
) -> bool:
    """Check if event text matches a specific topic."""
    return _text_matches_keywords(event_text, specific.name)


def observe_events(
    events: list[dict[str, Any]],
    state: SpeculativeState,
) -> tuple[SpeculativeState, int]:
    """Check events against active speculations at both domain and specific levels.

    Matching works bottom-up: if a specific matches, the domain also gets
    credited. A direct domain match (without specific) still counts.
    """
    match_count = 0
    for spec in state.active:
        if spec.status != "active":
            continue
        for event in events:
            event_text = _build_event_text(event)
            title_short = str(event.get("title", ""))[:50]

            # Check specifics first (more granular)
            specific_matched = False
            for specific in spec.specifics:
                if _event_matches_specific(event_text, specific):
                    specific.confirmation_count += 1
                    specific.confirming_events.append(title_short)
                    specific_matched = True

            # Domain-level confirmation: either a specific matched or domain directly matches
            if specific_matched or _text_matches_keywords(event_text, spec.domain, spec.category):
                spec.confirmation_count += 1
                spec.confirming_events.append(title_short)
                match_count += 1
    return state, match_count


# ---------------------------------------------------------------------------
# Promotion and expiry (pure logic, no LLM)
# ---------------------------------------------------------------------------


def promote_ready(state: SpeculativeState) -> tuple[list[SpeculativeInterest], SpeculativeState]:
    """Extract speculations that have reached confirmation threshold."""
    promoted: list[SpeculativeInterest] = []
    remaining: list[SpeculativeInterest] = []
    for spec in state.active:
        if spec.status == "active" and spec.confirmation_count >= spec.confirmation_threshold:
            spec.status = "promoted"
            promoted.append(spec)
            state.total_promoted += 1
        else:
            remaining.append(spec)
    state.active = remaining
    return promoted, state


def expire_stale(
    state: SpeculativeState,
    now: datetime,
    cooldown_days: int = 30,
) -> tuple[list[SpeculativeInterest], SpeculativeState]:
    """Expire speculations past TTL, add to cooldown, clean expired cooldowns."""
    rejected: list[SpeculativeInterest] = []
    remaining: list[SpeculativeInterest] = []
    for spec in state.active:
        if spec.status != "active":
            remaining.append(spec)
            continue
        try:
            created = datetime.fromisoformat(spec.created_at)
        except (ValueError, TypeError):
            remaining.append(spec)
            continue
        if now > created + timedelta(days=spec.ttl_days):
            spec.status = "rejected"
            rejected.append(spec)
            state.total_rejected += 1
            state.cooldown.append(
                CooldownEntry(
                    domain=spec.domain,
                    category=spec.category,
                    rejected_at=now.isoformat(),
                    cooldown_until=(now + timedelta(days=cooldown_days)).isoformat(),
                )
            )
        else:
            remaining.append(spec)
    state.active = remaining

    # Clean expired cooldowns
    valid_cooldowns: list[CooldownEntry] = []
    for entry in state.cooldown:
        try:
            until = datetime.fromisoformat(entry.cooldown_until)
        except (ValueError, TypeError):
            continue
        if now <= until:
            valid_cooldowns.append(entry)
    state.cooldown = valid_cooldowns

    return rejected, state


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


def load_speculative_state(data_dir: Path) -> SpeculativeState:
    """Load speculative state from disk."""
    path = data_dir / "memory" / "speculative_state.json"
    if not path.exists():
        return SpeculativeState()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return SpeculativeState.from_dict(data)
    except (json.JSONDecodeError, OSError):
        return SpeculativeState()


def save_speculative_state(data_dir: Path, state: SpeculativeState) -> None:
    """Persist speculative state to disk."""
    memory_dir = data_dir / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    path = memory_dir / "speculative_state.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------


class InterestSpeculator:
    """Orchestrates the speculative interest lifecycle.

    Responsibilities:
    - Generate new speculations via LLM (periodic)
    - Observe events for confirmation (every ingest)
    - Promote confirmed speculations to official interests
    - Expire and cooldown rejected speculations
    """

    def __init__(
        self,
        *,
        llm_service: Any,
        data_dir: Path | None = None,
        generation_interval_minutes: int = 10,
        default_ttl_days: int = 3,
        cooldown_days: int = 7,
        confirmation_threshold: int = 3,
        max_active: int = 5,
        max_primary_interests: int = 15,
        max_secondary_interests: int = 60,
    ) -> None:
        self._llm_service = llm_service
        self._data_dir = data_dir
        self._generation_interval_minutes = generation_interval_minutes
        self._default_ttl_days = default_ttl_days
        self._cooldown_days = cooldown_days
        self._confirmation_threshold = confirmation_threshold
        self._max_active = max_active
        self._max_primary_interests = max_primary_interests
        self._max_secondary_interests = max_secondary_interests

    def _load_state(self) -> SpeculativeState:
        if self._data_dir:
            return load_speculative_state(self._data_dir)
        return SpeculativeState()

    def _save_state(self, state: SpeculativeState) -> None:
        if self._data_dir:
            save_speculative_state(self._data_dir, state)

    # -- Public API -----------------------------------------------------------

    async def tick(self, profile: OnionProfile) -> SpeculatorTickResult:
        """Main periodic entry point: expire → promote → generate → save."""
        now = datetime.now()
        state = self._load_state()
        result = SpeculatorTickResult()

        # 1. Expire stale speculations
        rejected, state = expire_stale(state, now, self._cooldown_days)
        result.rejected = rejected

        # 2. Promote confirmed speculations
        promoted, state = promote_ready(state)
        result.promoted = promoted

        # 3. Generate new speculations if interval elapsed and caps not reached
        if self._should_generate(state, now, profile):
            state = await self._generate(profile, state, now)
            result.generated = [s for s in state.active if s.status == "active"]

        self._save_state(state)

        if result.promoted:
            logger.info(
                "Speculator promoted %d interests: %s",
                len(result.promoted),
                [s.domain for s in result.promoted],
            )
        if result.rejected:
            logger.info(
                "Speculator rejected %d speculations: %s",
                len(result.rejected),
                [s.domain for s in result.rejected],
            )
        if result.generated:
            logger.info(
                "Speculator generated %d new speculations: %s",
                len(result.generated),
                [s.domain for s in result.generated],
            )

        return result

    async def force_tick(self, profile: OnionProfile) -> SpeculatorTickResult:
        """Force a speculator tick ignoring the interval timer.

        Used on init and process startup to ensure speculations exist immediately.
        Still respects interest tier caps and max_active.
        """
        now = datetime.now()
        state = self._load_state()
        result = SpeculatorTickResult()

        # Expire and promote as usual
        rejected, state = expire_stale(state, now, self._cooldown_days)
        result.rejected = rejected
        promoted, state = promote_ready(state)
        result.promoted = promoted

        # Generate regardless of interval (but respect caps).
        # The "primary interests" cap historically gated on
        # ``confirmed_domains + active_count``, which deadlocks the
        # whole probe pipeline once the user has more confirmed
        # interests than the cap (e.g. profile with 21 confirmed
        # likes vs cap=15 → no probe ever fires). The cap is meant
        # to bound *speculative* fanout, not punish well-mapped
        # users. Gate only on ``active_count`` so probes can still
        # flow regardless of how many interests are already
        # confirmed.
        active_count = sum(1 for s in state.active if s.status == "active")
        can_generate = (
            active_count < self._max_active
            and active_count < self._max_primary_interests
            and self._llm_service is not None
        )
        if can_generate:
            state = await self._generate(profile, state, now)
            result.generated = [s for s in state.active if s.status == "active"]

        self._save_state(state)
        logger.info(
            "Speculator force_tick: generated=%d, promoted=%d, rejected=%d",
            len(result.generated),
            len(result.promoted),
            len(result.rejected),
        )
        return result

    def observe(self, events: list[dict[str, Any]]) -> int:
        """Observe events against active speculations. Returns match count."""
        if not events:
            return 0
        state = self._load_state()
        active_count = sum(1 for s in state.active if s.status == "active")
        if active_count == 0:
            return 0

        state, match_count = observe_events(events, state)
        if match_count > 0:
            self._save_state(state)
            logger.debug("Speculator observed %d matches from %d events", match_count, len(events))
        return match_count

    def ingest_seeds(
        self,
        seeds: list[dict[str, Any]],
    ) -> int:
        """Ingest speculative interests from PreferenceAnalyzer as seed candidates."""
        if not seeds:
            return 0

        state = self._load_state()
        now = datetime.now()
        added = 0

        existing_domains = {s.domain.lower() for s in state.active}
        cooldown_domains = {c.domain.lower() for c in state.cooldown}

        for seed in seeds:
            domain = str(seed.get("domain") or seed.get("name", "")).strip()
            if not domain:
                continue
            if domain.lower() in existing_domains or domain.lower() in cooldown_domains:
                continue
            if len(state.active) >= self._max_active:
                break

            state.active.append(
                SpeculativeInterest(
                    domain=domain,
                    category=str(seed.get("category", "")),
                    reason=str(seed.get("reason", "")),
                    confidence=float(seed.get("confidence") or seed.get("weight", 0.4)),
                    weight=float(seed.get("weight", 0.4)),
                    created_at=now.isoformat(),
                    ttl_days=self._default_ttl_days,
                    confirmation_threshold=self._confirmation_threshold,
                )
            )
            existing_domains.add(domain.lower())
            added += 1

        if added > 0:
            self._save_state(state)
            logger.info("Speculator ingested %d seed speculations", added)
        return added

    def get_active_speculations(self) -> list[SpeculativeInterest]:
        """Return currently active speculations (for discovery integration)."""
        state = self._load_state()
        return [s for s in state.active if s.status == "active"]

    def user_confirm_speculation(self, domain: str) -> bool:
        """User explicitly confirmed a speculated interest. Force-promote it."""
        state = self._load_state()
        for spec in state.active:
            if spec.domain.lower() == domain.lower() and spec.status == "active":
                spec.confirmation_count = spec.confirmation_threshold  # Meet threshold
                spec.confirming_events.append("user_confirmed")
                self._save_state(state)
                return True
        return False

    def user_reject_speculation(self, domain: str, cooldown_days: int = 30) -> bool:
        """User explicitly rejected a speculated interest. Move to cooldown."""
        state = self._load_state()
        remaining = []
        found = False
        now = datetime.now()
        for spec in state.active:
            if spec.domain.lower() == domain.lower() and spec.status == "active":
                spec.status = "rejected"
                state.total_rejected += 1
                state.cooldown.append(
                    CooldownEntry(
                        domain=spec.domain,
                        category=spec.category,
                        rejected_at=now.isoformat(),
                        cooldown_until=(now + timedelta(days=cooldown_days)).isoformat(),
                    )
                )
                found = True
            else:
                remaining.append(spec)
        state.active = remaining
        if found:
            self._save_state(state)
        return found

    # -- Internal -------------------------------------------------------------

    def _should_generate(
        self,
        state: SpeculativeState,
        now: datetime,
        profile: OnionProfile | None = None,
    ) -> bool:
        """Check if generation should run.

        Skips if:
        - active speculations already at max_active
        - primary interests (confirmed domains + active speculations) at cap
        - secondary interests (confirmed specifics + active speculations) at cap
        - interval not yet elapsed
        """
        active_count = sum(1 for s in state.active if s.status == "active")
        if active_count >= self._max_active:
            return False

        # Check active speculation tier cap. We gate solely on
        # ``active_count`` here, not ``confirmed + active`` — see the
        # comment in ``force_tick``: a well-mapped user with many
        # confirmed interests was permanently deadlocked under the
        # old gate.
        if profile is not None:
            if active_count >= self._max_primary_interests:
                logger.debug(
                    "Speculation skipped: active speculations at primary cap (%d/%d)",
                    active_count,
                    self._max_primary_interests,
                )
                return False

            # Same fix for secondary cap — gate on active speculation
            # count alone, not ``confirmed_specifics + active``, so a
            # rich profile doesn't permanently silence the probe loop.
            if active_count >= self._max_secondary_interests:
                logger.debug(
                    "Speculation skipped: active speculations at secondary cap (%d/%d)",
                    active_count,
                    self._max_secondary_interests,
                )
                return False

        if not state.last_generation_at:
            return True
        try:
            last = datetime.fromisoformat(state.last_generation_at)
        except (ValueError, TypeError):
            return True
        return now > last + timedelta(minutes=self._generation_interval_minutes)

    async def _generate(
        self,
        profile: OnionProfile,
        state: SpeculativeState,
        now: datetime,
    ) -> SpeculativeState:
        """Use LLM to generate new speculative interest directions."""
        from openbiliclaw.llm.prompts import build_speculation_generation_prompt

        existing_domains = {s.domain.lower() for s in state.active}
        cooldown_domains = [c.domain for c in state.cooldown]
        confirmed_domains = [d.domain for d in profile.interest.likes]

        slots = self._max_active - sum(1 for s in state.active if s.status == "active")
        if slots <= 0:
            return state

        messages = build_speculation_generation_prompt(
            profile_summary=profile.to_llm_context(),
            existing_speculations=[s.domain for s in state.active],
            cooldown_domains=cooldown_domains,
            confirmed_domains=confirmed_domains,
            count=min(max(slots * 2, 5), 7),
        )

        try:
            from openbiliclaw.llm.base import LLMProviderError
            from openbiliclaw.llm.service import LLMServiceError

            response = await self._llm_service.complete_structured_task(
                system_instruction=messages[0]["content"],
                user_input=messages[1]["content"],
                max_tokens=DEFAULT_STRUCTURED_MAX_TOKENS,
            )
            raw = _parse_speculation_response(response.content)
        except (LLMProviderError, LLMServiceError):
            logger.warning("Speculation generation LLM call failed", exc_info=True)
            return state
        except Exception:
            logger.warning("Speculation generation failed", exc_info=True)
            return state

        candidates: list[SpeculativeInterest] = []
        for item in raw:
            domain = str(item.get("domain", "")).strip()
            if not domain or domain.lower() in existing_domains:
                continue

            raw_specifics = item.get("specifics") or []
            specifics = [
                SpeculativeSpecific(name=str(s).strip())
                for s in raw_specifics
                if isinstance(s, str) and str(s).strip()
            ]
            confidence = float(item.get("confidence", 0.4))
            candidates.append(
                SpeculativeInterest(
                    domain=domain,
                    category=str(item.get("category", "")),
                    reason=str(item.get("reason", "")),
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

        for candidate in _select_diverse_candidates(candidates, limit=slots):
            if len(state.active) >= self._max_active:
                break
            state.active.append(candidate)
            existing_domains.add(candidate.domain.lower())

        state.last_generation_at = now.isoformat()
        return state


def _parse_speculation_response(content: str) -> list[dict[str, Any]]:
    """Extract speculations list from an LLM response.

    Accepts either a raw list or a ``{"speculations": [...]}`` object, and
    falls back to truncation salvage for responses that were cut off mid-field.
    """
    data = parse_llm_json_tolerant(content)
    if isinstance(data, dict):
        speculations = data.get("speculations", [])
        if isinstance(speculations, list):
            return [item for item in speculations if isinstance(item, dict)]
        return []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def _normalize_experience_mode(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    allowed = {
        "knowledge",
        "aesthetic",
        "hands_on",
        "people_story",
        "wander_observe",
    }
    return text if text in allowed else ""


def _normalize_entry_load(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in {"light", "heavy"} else ""


def _candidate_priority(
    candidate: SpeculativeInterest,
    selected: list[SpeculativeInterest],
) -> tuple[float, float]:
    score = float(candidate.confidence)
    selected_modes = {item.experience_mode for item in selected if item.experience_mode}
    selected_loads = {item.entry_load for item in selected if item.entry_load}
    if candidate.experience_mode and candidate.experience_mode not in selected_modes:
        score += 0.08
    if candidate.entry_load and candidate.entry_load not in selected_loads:
        score += 0.05
    if candidate.entry_load == "light" and "light" not in selected_loads:
        score += 0.05
    if candidate.experience_mode and candidate.experience_mode != "knowledge":
        score += 0.05
    return (score, float(candidate.weight))


def _pick_best_candidate(
    candidates: list[SpeculativeInterest],
    selected: list[SpeculativeInterest],
    predicate: Any,
) -> SpeculativeInterest | None:
    matching = [
        candidate for candidate in candidates if candidate not in selected and predicate(candidate)
    ]
    if not matching:
        return None
    return max(matching, key=lambda candidate: _candidate_priority(candidate, selected))


def _select_diverse_candidates(
    candidates: list[SpeculativeInterest],
    *,
    limit: int,
) -> list[SpeculativeInterest]:
    if limit <= 0 or not candidates:
        return []
    if len(candidates) <= limit:
        return list(candidates)

    ordered = sorted(
        candidates,
        key=lambda item: (float(item.confidence), float(item.weight)),
        reverse=True,
    )
    selected: list[SpeculativeInterest] = []

    light_pick = _pick_best_candidate(
        ordered,
        selected,
        lambda item: item.entry_load == "light",
    )
    if light_pick is not None:
        selected.append(light_pick)

    if not any(item.experience_mode and item.experience_mode != "knowledge" for item in selected):
        non_knowledge_pick = _pick_best_candidate(
            ordered,
            selected,
            lambda item: item.experience_mode and item.experience_mode != "knowledge",
        )
        if non_knowledge_pick is not None:
            selected.append(non_knowledge_pick)

    while len(selected) < limit:
        remaining = [candidate for candidate in ordered if candidate not in selected]
        if not remaining:
            break
        selected.append(
            max(remaining, key=lambda candidate: _candidate_priority(candidate, selected))
        )
    return selected[:limit]


def build_probe_axis(*, experience_mode: Any, entry_load: Any) -> str:
    mode = _normalize_experience_mode(experience_mode)
    load = _normalize_entry_load(entry_load)
    if not mode and not load:
        return ""
    return f"{mode}|{load}"


def choose_next_probe_candidate(
    specs: list[Any],
    *,
    probed_domains: set[str] | None = None,
    probed_axes: set[str] | None = None,
) -> Any | None:
    recent_domains = probed_domains or set()
    recent_axes = probed_axes or set()
    candidates = []
    for candidate in specs:
        domain = str(getattr(candidate, "domain", "")).strip().lower()
        if not domain or domain in recent_domains:
            continue
        candidates.append(candidate)
    if not candidates:
        return None

    min_confirmation = min(
        int(getattr(candidate, "confirmation_count", 0) or 0) for candidate in candidates
    )
    same_pressure = [
        candidate
        for candidate in candidates
        if int(getattr(candidate, "confirmation_count", 0) or 0) == min_confirmation
    ]
    fresh_axis = [
        candidate
        for candidate in same_pressure
        if (
            axis := build_probe_axis(
                experience_mode=getattr(candidate, "experience_mode", ""),
                entry_load=getattr(candidate, "entry_load", ""),
            )
        )
        and axis not in recent_axes
    ]
    pool = fresh_axis or same_pressure
    return max(
        pool,
        key=lambda candidate: (
            float(getattr(candidate, "weight", 0.0) or 0.0),
            float(getattr(candidate, "confidence", 0.0) or 0.0),
        ),
    )
