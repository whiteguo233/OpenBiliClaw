"""Shared utilities and protocols for discovery strategies."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, TypeVar, cast, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from openbiliclaw.discovery.engine import DiscoveredContent
    from openbiliclaw.soul.profile import InterestDomain, OnionProfile, SoulProfile

_T = TypeVar("_T")

# Profile-summary truncation caps. Lists are weight-sorted before
# truncation so the strongest interests survive the cut, not whichever
# happened to be listed first.
_INTEREST_DOMAIN_CAP = 128
_SPECIFICS_PER_DOMAIN = 30
_INTEREST_TAG_CAP = 256
# Matches _DISLIKED_TOPICS_STORE_CAP so avoid-topics are NEVER cut from
# prompts: the store predates the recency-ordered union (v0.3.121), so
# legacy entries sit in alphabetical order and any cut below the store
# cap would drop topics by codepoint, not by relevance.
_DISLIKED_TOPICS_CAP = 128


@runtime_checkable
class SupportsIsoformat(Protocol):
    def isoformat(self) -> str: ...


async def _gather_bounded(
    awaitables: list[Awaitable[_T]],
    *,
    runner: Callable[[Awaitable[_T]], Awaitable[_T]] | None = None,
) -> list[object]:
    """Gather awaitables, optionally routing them through a bounded runner."""
    if runner is None:
        return cast(
            "list[object]",
            await asyncio.gather(*awaitables, return_exceptions=True),
        )
    return cast(
        "list[object]",
        await asyncio.gather(
            *(runner(awaitable) for awaitable in awaitables),
            return_exceptions=True,
        ),
    )


# ---------------------------------------------------------------------------
# Protocol classes
# ---------------------------------------------------------------------------


class SupportsSearchClient(Protocol):
    async def search(
        self,
        keyword: str,
        page: int = 1,
        page_size: int = 20,
        order: str = "totalrank",
    ) -> list[dict[str, object]]: ...


def search_cooldown_remaining(client: object) -> float:
    """Return process/client search cooldown seconds when the client exposes it."""
    remaining = getattr(client, "search_cooldown_remaining", None)
    if not callable(remaining):
        return 0.0
    try:
        return max(0.0, float(remaining()))
    except Exception:
        return 0.0


class SupportsRankingClient(Protocol):
    async def get_ranking(self, rid: int = 0) -> list[dict[str, object]]: ...


class SupportsMemoryManager(Protocol):
    def query_events(
        self,
        *,
        event_types: list[str] | None = None,
        start_time: object | None = None,
        end_time: object | None = None,
        keyword: str = "",
        limit: int = 100,
    ) -> list[dict[str, object]]: ...


class SupportsSeedStrategy(Protocol):
    async def discover(self, profile: SoulProfile, limit: int = 20) -> list[DiscoveredContent]: ...


class SupportsRelatedClient(Protocol):
    async def get_related_videos(self, bvid: str) -> list[dict[str, object]]: ...

    async def search(
        self,
        keyword: str,
        page: int = 1,
        page_size: int = 20,
        order: str = "totalrank",
    ) -> list[dict[str, object]]: ...


# ---------------------------------------------------------------------------
# Shared helper functions (extracted from SearchStrategy static methods)
# ---------------------------------------------------------------------------


def clean_text(value: str) -> str:
    """Strip HTML tags from *value*."""
    return re.sub(r"<[^>]+>", "", value).strip()


def to_int(raw_value: object) -> int:
    """Best-effort conversion of *raw_value* to ``int``."""
    if isinstance(raw_value, bool):
        return int(raw_value)
    if isinstance(raw_value, int):
        return raw_value
    if isinstance(raw_value, float):
        return int(raw_value)
    if isinstance(raw_value, str):
        digits = raw_value.replace(",", "").strip()
        if digits.isdigit():
            return int(digits)
    return 0


def parse_duration(raw_value: object) -> int:
    """Parse a duration value (int seconds or ``HH:MM:SS`` / ``MM:SS`` string)."""
    if isinstance(raw_value, int):
        return raw_value
    if isinstance(raw_value, str) and ":" in raw_value:
        parts = [part for part in raw_value.split(":") if part.isdigit()]
        if len(parts) == 2:
            minutes, seconds = parts
            return int(minutes) * 60 + int(seconds)
        if len(parts) == 3:
            hours, minutes, seconds = parts
            return int(hours) * 3600 + int(minutes) * 60 + int(seconds)
    return to_int(raw_value)


def normalize_published_at(*values: object) -> str:
    """Return a normalized ISO timestamp for source-provided publish times."""
    for value in values:
        normalized = _normalize_timestamp_value(value)
        if normalized:
            return normalized
    return ""


def _normalize_timestamp_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return ""
    if isinstance(value, int | float):
        return _epoch_to_iso(float(value))
    text = str(value).strip()
    if not text:
        return ""
    numeric_text = text.replace(".", "", 1)
    if numeric_text.isdigit():
        try:
            return _epoch_to_iso(float(text))
        except ValueError:
            return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def _epoch_to_iso(raw_seconds: float) -> str:
    if raw_seconds <= 0:
        return ""
    seconds = raw_seconds / 1000 if raw_seconds >= 10_000_000_000 else raw_seconds
    try:
        return datetime.fromtimestamp(seconds, UTC).isoformat()
    except (OSError, OverflowError, ValueError):
        return ""


def normalize_match_text(value: str) -> str:
    """Collapse whitespace and lowercase for fuzzy matching."""
    return re.sub(r"\s+", "", value).strip().lower()


def _format_profile_timestamp(value: object) -> str:
    """Serialize a profile timestamp-like value for JSON prompt summaries."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, SupportsIsoformat):
        return value.isoformat()
    return str(value)


def _coerce_profile_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _coerce_profile_str_list(value: object, limit: int = 5) -> list[str]:
    if not isinstance(value, list):
        return []
    values: list[str] = []
    for item in value[:limit]:
        text = str(item).strip()
        if text:
            values.append(text)
    return values


def _likes_by_weight(profile: OnionProfile) -> list[InterestDomain]:
    """Interest domains sorted by weight (desc), blanks dropped."""
    return sorted(
        (dom for dom in profile.interest.likes if dom.domain.strip()),
        key=lambda dom: dom.weight,
        reverse=True,
    )


def _entry_weight(entry: dict[str, object]) -> float:
    weight = entry.get("weight")
    return float(weight) if isinstance(weight, (int, float)) else 0.0


def _extract_interest_domains(profile: SoulProfile) -> list[dict[str, object]]:
    """Extract domain-level (一级) interest hierarchy from profile.

    Returns a list like:
    [{"domain": "AI/ML", "weight": 0.9, "specifics": ["强化学习", "ppo算法"]}, ...]

    This gives LLM prompts visibility into both broad domains AND
    specific sub-interests, enabling queries at different granularity.
    """
    from openbiliclaw.soul.profile import OnionProfile

    # OnionProfile has the tree structure directly
    if isinstance(profile, OnionProfile):
        return [
            {
                "domain": dom.domain,
                "weight": dom.weight,
                "specifics": [s.name for s in dom.specifics[:_SPECIFICS_PER_DOMAIN]],
                "first_seen": _format_profile_timestamp(dom.first_seen),
                "last_seen": _format_profile_timestamp(dom.last_seen),
                "source": dom.source,
            }
            for dom in _likes_by_weight(profile)[:_INTEREST_DOMAIN_CAP]
        ]

    # Flat SoulProfile: reconstruct domains from category grouping
    ranked_tags = sorted(profile.preferences.interests, key=lambda tag: tag.weight, reverse=True)
    domain_map: dict[str, dict[str, object]] = {}
    for tag in ranked_tags[:_INTEREST_TAG_CAP]:
        key = tag.category or tag.name
        if key not in domain_map:
            domain_map[key] = {
                "domain": key,
                "weight": tag.weight,
                "specifics": [],
                "first_seen": _format_profile_timestamp(tag.first_seen),
                "last_seen": _format_profile_timestamp(tag.last_seen),
                "source": tag.source,
            }
        existing = domain_map[key]
        if tag.name != key:
            specs = existing["specifics"]
            if isinstance(specs, list) and len(specs) < _SPECIFICS_PER_DOMAIN:
                specs.append(tag.name)
        existing_weight = existing.get("weight", 0)
        if tag.weight > (
            float(existing_weight) if isinstance(existing_weight, (int, float)) else 0
        ):
            existing["weight"] = tag.weight
            existing["source"] = tag.source
        if not existing.get("first_seen"):
            existing["first_seen"] = _format_profile_timestamp(tag.first_seen)
        existing["last_seen"] = _format_profile_timestamp(tag.last_seen) or existing.get(
            "last_seen", ""
        )
    return sorted(domain_map.values(), key=_entry_weight, reverse=True)[:_INTEREST_DOMAIN_CAP]


def _extract_interest_tags(profile: SoulProfile) -> list[dict[str, object]]:
    """Extract flat interest tags with provenance metadata."""
    from openbiliclaw.soul.profile import OnionProfile

    if isinstance(profile, OnionProfile):
        ranked = _likes_by_weight(profile)
        interests: list[dict[str, object]] = []
        seen_names: set[str] = set()
        # Domain tags first: every ranked domain keeps tag-level exposure
        # even when higher-weight domains carry many specifics.
        for dom in ranked:
            if len(interests) >= _INTEREST_TAG_CAP:
                break
            interests.append(
                {
                    "name": dom.domain,
                    "category": dom.domain,
                    "weight": dom.weight,
                    "first_seen": _format_profile_timestamp(dom.first_seen),
                    "last_seen": _format_profile_timestamp(dom.last_seen),
                    "source": dom.source,
                }
            )
            seen_names.add(dom.domain)
        # Remaining slots: specifics ranked by their OWN weight across all
        # domains. A per-domain quota here let umbrella domains (200+
        # specifics on real profiles) hide 0.8-weight tags behind their
        # top-5 while 0.4-weight tags from tiny domains got in. Per-domain
        # exposure is already guaranteed by the domain tags above and the
        # interest_domains section, so the flat list can be purely
        # weight-ranked.
        all_specifics = sorted(
            ((spec, dom) for dom in ranked for spec in dom.specifics if spec.name.strip()),
            key=lambda pair: pair[0].weight,
            reverse=True,
        )
        for spec, dom in all_specifics:
            if len(interests) >= _INTEREST_TAG_CAP:
                break
            if spec.name in seen_names:
                continue
            seen_names.add(spec.name)
            interests.append(
                {
                    "name": spec.name,
                    "category": dom.domain,
                    "weight": spec.weight,
                    "first_seen": _format_profile_timestamp(dom.first_seen),
                    "last_seen": _format_profile_timestamp(dom.last_seen),
                    "source": dom.source,
                }
            )
        return interests

    ranked_flat = sorted(
        (tag for tag in profile.preferences.interests if tag.name.strip()),
        key=lambda tag: tag.weight,
        reverse=True,
    )
    return [
        {
            "name": interest.name,
            "category": interest.category,
            "weight": interest.weight,
            "first_seen": _format_profile_timestamp(interest.first_seen),
            "last_seen": _format_profile_timestamp(interest.last_seen),
            "source": interest.source,
        }
        for interest in ranked_flat[:_INTEREST_TAG_CAP]
    ]


def _summarize_mbti(profile: SoulProfile) -> dict[str, object] | None:
    """Return compact MBTI context when available."""
    from openbiliclaw.soul.profile import OnionProfile

    if isinstance(profile, OnionProfile):
        mbti = profile.core.mbti
        if not mbti.type.strip():
            return None
        return {
            "type": mbti.type,
            "confidence": mbti.confidence,
            "dimensions": {
                key: {"pole": dim.pole, "strength": dim.strength}
                for key, dim in mbti.dimensions.items()
            },
            "inferred_from": mbti.inferred_from[:30],
        }

    raw_mbti = getattr(profile, "_raw_mbti", None)
    if not isinstance(raw_mbti, dict):
        return None
    raw_type = raw_mbti.get("type")
    mbti_type = raw_type if isinstance(raw_type, str) else ""
    if not mbti_type.strip():
        return None

    dimensions: dict[str, dict[str, object]] = {}
    raw_dimensions = raw_mbti.get("dimensions")
    if isinstance(raw_dimensions, dict):
        for key, raw_dimension in raw_dimensions.items():
            if not isinstance(key, str) or not isinstance(raw_dimension, dict):
                continue
            dimensions[key] = {
                "pole": str(raw_dimension.get("pole", "")),
                "strength": _coerce_profile_float(raw_dimension.get("strength", 0.5), 0.5),
            }

    return {
        "type": mbti_type,
        "confidence": _coerce_profile_float(raw_mbti.get("confidence", 0.0), 0.0),
        "dimensions": dimensions,
        "inferred_from": _coerce_profile_str_list(raw_mbti.get("inferred_from"), limit=30),
    }


def _summarize_recent_awareness(profile: SoulProfile) -> list[dict[str, str]]:
    notes: list[dict[str, str]] = []
    # The window is chronological oldest→newest, so the newest notes live
    # at the tail — [:5] would feed the LLM the *stalest* observations.
    for note in profile.recent_awareness[-30:]:
        item = {
            "date": note.date,
            "observation": note.observation,
            "trend": note.trend,
            "emotion_guess": note.emotion_guess,
        }
        if any(value.strip() for value in item.values()):
            notes.append(item)
    return notes


def _summarize_active_insights(profile: SoulProfile) -> list[dict[str, object]]:
    insights: list[dict[str, object]] = []
    # Chronological window: newest insights are at the tail.
    for insight in profile.active_insights[-30:]:
        item: dict[str, object] = {
            "hypothesis": insight.hypothesis,
            "evidence": insight.evidence[:30],
            "confidence": insight.confidence,
            "validated": insight.validated,
        }
        if insight.created_at:
            item["created_at"] = insight.created_at
        if insight.hypothesis.strip() or insight.evidence:
            insights.append(item)
    return insights


def build_profile_summary(
    profile: SoulProfile,
    *,
    interests: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """Build the canonical structured profile input shared by every prompt.

    This is the single profile representation fed to the LLM across all
    source-platform content calls — discovery (search / trending / explore /
    evaluation) and recommendation (evaluation / expression / reason) alike.

    The free-form ``personality_portrait`` narrative is deliberately excluded:
    the structured fields below already carry the same signal, and the prose
    summary only duplicated it (and biased query/expression generation with its
    decorative metaphors). The portrait is still generated and shown in the
    profile UI — it just no longer enters any LLM prompt.

    Includes both domain-level (一级) and specific (二级) interests so that
    discovery prompts can generate queries at different granularity levels.
    Pass ``interests`` to override the default weight-ranked tag list (e.g.
    recommendation's embedding-selected, content-relevant interests).
    """
    interest_domains = _extract_interest_domains(profile)
    summary: dict[str, object] = {
        "core_traits": profile.core_traits[:30],
        "cognitive_style": profile.cognitive_style[:30],
        "values": profile.values[:30],
        "motivational_drivers": profile.motivational_drivers[:30],
        "current_phase": profile.current_phase,
        "life_stage": profile.life_stage,
        "interest_domains": interest_domains,
        "interests": interests if interests is not None else _extract_interest_tags(profile),
        # favorite_up_users is intentionally excluded from the LLM-facing
        # profile output: "常看某创作者" ≠ "对该创作者内容类型感兴趣", and it
        # only invited the model to back-derive interests from creator names.
        # The user's UP list still lives in /api/profile-summary (their own
        # view) and seeds related_chain directly — just not here.
        "disliked_topics": profile.preferences.disliked_topics[:_DISLIKED_TOPICS_CAP],
        "deep_needs": profile.deep_needs[:30],
        "style": {
            "preferred_duration": profile.preferences.style.preferred_duration,
            "preferred_pace": profile.preferences.style.preferred_pace,
            "quality_sensitivity": profile.preferences.style.quality_sensitivity,
            "humor_preference": profile.preferences.style.humor_preference,
            "depth_preference": profile.preferences.style.depth_preference,
        },
        "context": {
            "weekday_patterns": profile.preferences.context.weekday_patterns,
            "weekend_patterns": profile.preferences.context.weekend_patterns,
            "time_of_day_patterns": profile.preferences.context.time_of_day_patterns,
            "session_type": profile.preferences.context.session_type,
        },
        "exploration_openness": profile.preferences.exploration_openness,
        "source_platform_mix": dict(profile.preferences.source_platform_mix),
        "recent_awareness": _summarize_recent_awareness(profile),
        "active_insights": _summarize_active_insights(profile),
    }
    mbti = _summarize_mbti(profile)
    if mbti:
        summary["mbti"] = mbti
    # Include active speculative interests if available
    speculations = getattr(profile, "_active_speculations", None)
    if speculations:
        summary["speculative_interests"] = [
            {
                "domain": s.domain if hasattr(s, "domain") else str(s.get("domain", "")),
                "reason": s.reason if hasattr(s, "reason") else str(s.get("reason", "")),
            }
            for s in speculations[:30]
        ]
    return summary


def interest_aliases(name: str) -> set[str]:
    """Return a set of normalised alias tokens for a given interest *name*."""
    cleaned = re.sub(r"\s+", "", name).strip().lower()
    if not cleaned:
        return set()
    aliases = {cleaned}
    stripped = re.sub(r"(系列|作品集|作品)$", "", cleaned).strip()
    if stripped:
        aliases.add(stripped)
    for token in re.split(r"[\s/&、，,+\-]+|与|和|及|之|的", cleaned):
        token = token.strip()
        if not token:
            continue
        if token.isascii():
            if len(token) >= 2:
                aliases.add(token)
            continue
        if len(token) >= 2:
            aliases.add(token)
    return aliases


def interest_anchors(profile: SoulProfile) -> list[tuple[str, float]]:
    """Build weighted interest anchor pairs from the top profile interests."""
    anchors: dict[str, float] = {}
    for interest_item in profile.preferences.interests[:5]:
        raw_name = str(interest_item.name).strip()
        if not raw_name:
            continue
        weight = max(0.0, min(1.0, float(interest_item.weight)))
        for alias in interest_aliases(raw_name):
            anchors[alias] = max(anchors.get(alias, 0.0), weight)
    return list(anchors.items())
