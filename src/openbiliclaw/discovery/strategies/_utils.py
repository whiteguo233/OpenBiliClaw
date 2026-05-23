"""Shared utilities and protocols for discovery strategies."""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Protocol, TypeVar, cast, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from openbiliclaw.discovery.engine import DiscoveredContent
    from openbiliclaw.soul.profile import SoulProfile

_T = TypeVar("_T")


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
                "specifics": [s.name for s in dom.specifics[:5]],
                "first_seen": _format_profile_timestamp(dom.first_seen),
                "last_seen": _format_profile_timestamp(dom.last_seen),
                "source": dom.source,
            }
            for dom in profile.interest.likes[:8]
            if dom.domain.strip()
        ]

    # Flat SoulProfile: reconstruct domains from category grouping
    domain_map: dict[str, dict[str, object]] = {}
    for tag in profile.preferences.interests[:15]:
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
            if isinstance(specs, list) and len(specs) < 5:
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
    return list(domain_map.values())[:8]


def _extract_interest_tags(profile: SoulProfile) -> list[dict[str, object]]:
    """Extract flat interest tags with provenance metadata."""
    from openbiliclaw.soul.profile import OnionProfile

    if isinstance(profile, OnionProfile):
        interests: list[dict[str, object]] = []
        for dom in profile.interest.likes[:8]:
            if dom.domain.strip():
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
            for spec in dom.specifics[:5]:
                if len(interests) >= 10:
                    break
                if not spec.name.strip():
                    continue
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
            if len(interests) >= 10:
                break
        return interests[:10]

    return [
        {
            "name": interest.name,
            "category": interest.category,
            "weight": interest.weight,
            "first_seen": _format_profile_timestamp(interest.first_seen),
            "last_seen": _format_profile_timestamp(interest.last_seen),
            "source": interest.source,
        }
        for interest in profile.preferences.interests[:10]
        if interest.name.strip()
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
            "inferred_from": mbti.inferred_from[:5],
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
        "inferred_from": _coerce_profile_str_list(raw_mbti.get("inferred_from"), limit=5),
    }


def _summarize_recent_awareness(profile: SoulProfile) -> list[dict[str, str]]:
    notes: list[dict[str, str]] = []
    for note in profile.recent_awareness[:5]:
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
    for insight in profile.active_insights[:5]:
        item: dict[str, object] = {
            "hypothesis": insight.hypothesis,
            "evidence": insight.evidence[:5],
            "confidence": insight.confidence,
            "validated": insight.validated,
        }
        if insight.created_at:
            item["created_at"] = insight.created_at
        if insight.hypothesis.strip() or insight.evidence:
            insights.append(item)
    return insights


def build_profile_summary(profile: SoulProfile) -> dict[str, object]:
    """Build a compact summary dict from a :class:`SoulProfile`.

    Includes both domain-level (一级) and specific (二级) interests so that
    discovery prompts can generate queries at different granularity levels.
    """
    interest_domains = _extract_interest_domains(profile)
    summary: dict[str, object] = {
        "personality_portrait": profile.personality_portrait,
        "core_traits": profile.core_traits[:5],
        "cognitive_style": profile.cognitive_style[:5],
        "values": profile.values[:5],
        "motivational_drivers": profile.motivational_drivers[:5],
        "current_phase": profile.current_phase,
        "life_stage": profile.life_stage,
        "interest_domains": interest_domains,
        "interests": _extract_interest_tags(profile),
        "favorite_up_users": profile.preferences.favorite_up_users[:5],
        "disliked_topics": profile.preferences.disliked_topics[:8],
        "deep_needs": profile.deep_needs[:5],
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
            for s in speculations[:5]
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
