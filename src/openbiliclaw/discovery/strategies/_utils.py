"""Shared utilities and protocols for discovery strategies."""

from __future__ import annotations

import asyncio
import math
import re
from dataclasses import dataclass
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
_QUERY_PROFILE_LIST_CAP = 8
_QUERY_INTEREST_DOMAIN_CAP = 16
_QUERY_SPECIFICS_PER_DOMAIN = 8
_QUERY_INTEREST_TAG_CAP = 64
_QUERY_INTEREST_CANDIDATE_POOL_CAP = 128
_QUERY_DISLIKED_TOPICS_CAP = 64
_QUERY_DISLIKED_TOPIC_CANDIDATE_POOL_CAP = 128
_QUERY_SPECULATIVE_INTEREST_CAP = 8


@dataclass(frozen=True)
class _QueryInterestCandidate:
    output: dict[str, object]
    text: str
    category: str
    weight: float
    priority: float
    vector: list[float]


@dataclass(frozen=True)
class _QueryTextCandidate:
    text: str
    priority: float
    vector: list[float]


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


def _compact_query_interest_domains(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    compacted: list[dict[str, object]] = []
    for item in value[:_QUERY_INTEREST_DOMAIN_CAP]:
        if not isinstance(item, dict):
            continue
        specifics = item.get("specifics")
        if not isinstance(specifics, list):
            specifics = []
        domain = str(item.get("domain", "")).strip()
        if not domain:
            continue
        compacted.append(
            {
                "domain": domain,
                "weight": item.get("weight", 0),
                "specifics": [
                    str(spec).strip()
                    for spec in specifics[:_QUERY_SPECIFICS_PER_DOMAIN]
                    if str(spec).strip()
                ],
            }
        )
    return compacted


def cached_embedding_lookup(
    embedding_service: object | None,
) -> Callable[[str], list[float]] | None:
    """Return a safe cache-only embedding lookup for prompt shaping.

    Query-generation prompts must not trigger fresh embedding API calls; that
    would move cost from chat completion to embedding and add latency to every
    planner/search cycle. ``lookup_cached`` keeps this helper opportunistic:
    use semantic diversity when cache is warm, otherwise preserve the old
    deterministic order.
    """
    lookup = getattr(embedding_service, "lookup_cached", None)
    if not callable(lookup):
        return None

    def _lookup(text: str) -> list[float]:
        try:
            return _coerce_query_embedding_vector(lookup(text))
        except Exception:
            return []

    return _lookup


def _coerce_query_embedding_vector(value: object) -> list[float]:
    if not isinstance(value, list):
        return []
    vector: list[float] = []
    for item in value:
        if not isinstance(item, (int, float)):
            return []
        number = float(item)
        if not math.isfinite(number):
            return []
        vector.append(number)
    return vector


def _lookup_query_embedding(
    text: str,
    embedding_lookup: Callable[[str], list[float] | None] | None,
) -> list[float]:
    if embedding_lookup is None:
        return []
    try:
        return _coerce_query_embedding_vector(embedding_lookup(text))
    except Exception:
        return []


def _clamp_similarity(value: float) -> float:
    return max(0.0, min(1.0, value))


def _cosine_similarity_safe(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    from openbiliclaw.llm.embedding import cosine_similarity

    return _clamp_similarity(cosine_similarity(a, b))


def _char_bigrams(text: str) -> set[str]:
    normalized = normalize_match_text(text)
    if not normalized:
        return set()
    if len(normalized) == 1:
        return {normalized}
    return {normalized[index : index + 2] for index in range(len(normalized) - 1)}


def _lexical_similarity(left: str, right: str) -> float:
    left_norm = normalize_match_text(left)
    right_norm = normalize_match_text(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    if left_norm in right_norm or right_norm in left_norm:
        return 0.88
    left_bigrams = _char_bigrams(left_norm)
    right_bigrams = _char_bigrams(right_norm)
    if not left_bigrams or not right_bigrams:
        return 0.0
    overlap = len(left_bigrams & right_bigrams)
    if overlap <= 0:
        return 0.0
    return min(0.75, overlap / max(len(left_bigrams), len(right_bigrams)))


def _interest_similarity(
    left: _QueryInterestCandidate,
    right: _QueryInterestCandidate,
) -> float:
    semantic = _cosine_similarity_safe(left.vector, right.vector)
    lexical = _lexical_similarity(left.text, right.text)
    category = (
        0.62
        if left.category
        and right.category
        and normalize_match_text(left.category) == normalize_match_text(right.category)
        else 0.0
    )
    return max(semantic, lexical, category)


def _interest_to_text_similarity(
    interest: _QueryInterestCandidate,
    topic: _QueryTextCandidate,
) -> float:
    semantic = _cosine_similarity_safe(interest.vector, topic.vector)
    lexical = _lexical_similarity(interest.text, topic.text)
    return max(semantic, lexical)


def _text_candidate_similarity(left: _QueryTextCandidate, right: _QueryTextCandidate) -> float:
    semantic = _cosine_similarity_safe(left.vector, right.vector)
    lexical = _lexical_similarity(left.text, right.text)
    return max(semantic, lexical)


def _normalized_weight(
    candidate: _QueryInterestCandidate, candidates: list[_QueryInterestCandidate]
) -> float:
    weights = [item.weight for item in candidates]
    max_weight = max(weights, default=0.0)
    min_weight = min(weights, default=0.0)
    span = max_weight - min_weight
    if span <= 1e-9:
        return candidate.priority
    return (candidate.weight - min_weight) / span


def _select_diverse_query_interests(
    candidates: list[_QueryInterestCandidate],
    *,
    disliked_topics: list[_QueryTextCandidate],
    cap: int,
) -> list[_QueryInterestCandidate]:
    if len(candidates) <= cap:
        return candidates
    if not any(candidate.vector for candidate in candidates) and not any(
        topic.vector for topic in disliked_topics
    ):
        return candidates[:cap]

    weights = [item.weight for item in candidates]
    max_weight = max(weights, default=0.0)
    min_weight = min(weights, default=0.0)
    span = max_weight - min_weight
    weight_scores = [
        candidate.priority if span <= 1e-9 else (candidate.weight - min_weight) / span
        for candidate in candidates
    ]
    dislike_penalties = [
        max(
            (_interest_to_text_similarity(candidate, topic) for topic in disliked_topics),
            default=0.0,
        )
        for candidate in candidates
    ]
    nearest_selected = [0.0 for _ in candidates]
    selected: list[_QueryInterestCandidate] = []
    remaining_indexes = list(range(len(candidates)))
    while remaining_indexes and len(selected) < cap:
        selected_categories = {
            normalize_match_text(item.category) for item in selected if item.category.strip()
        }

        def score_index(
            index: int,
            selected_categories: set[str] = selected_categories,
        ) -> tuple[float, float, float]:
            candidate = candidates[index]
            weight_score = weight_scores[index]
            dislike_penalty = dislike_penalties[index]
            category_key = normalize_match_text(candidate.category)
            category_novelty = (
                0.5 if not category_key else float(category_key not in selected_categories)
            )
            if not selected:
                mmr = (
                    0.72 * weight_score
                    + 0.18 * category_novelty
                    + 0.10 * candidate.priority
                    - 0.55 * dislike_penalty
                )
                return (mmr, weight_score, candidate.priority)

            novelty = 1.0 - nearest_selected[index]
            mmr = (
                0.46 * novelty
                + 0.27 * weight_score
                + 0.19 * category_novelty
                + 0.08 * candidate.priority
                - 0.48 * dislike_penalty
            )
            return (mmr, weight_score, candidate.priority)

        best_index = max(remaining_indexes, key=score_index)
        best = candidates[best_index]
        selected.append(best)
        remaining_indexes.remove(best_index)
        for index in remaining_indexes:
            nearest_selected[index] = max(
                nearest_selected[index],
                _interest_similarity(candidates[index], best),
            )
    return selected


def _select_diverse_query_texts(
    candidates: list[_QueryTextCandidate],
    *,
    cap: int,
) -> list[_QueryTextCandidate]:
    if len(candidates) <= cap:
        return candidates
    if not any(candidate.vector for candidate in candidates):
        return candidates[:cap]

    selected: list[_QueryTextCandidate] = []
    nearest_selected = [0.0 for _ in candidates]
    remaining_indexes = list(range(len(candidates)))
    while remaining_indexes and len(selected) < cap:

        def score_index(index: int) -> tuple[float, float]:
            candidate = candidates[index]
            if not selected:
                return (candidate.priority, candidate.priority)
            novelty = 1.0 - nearest_selected[index]
            return (0.72 * novelty + 0.28 * candidate.priority, candidate.priority)

        best_index = max(remaining_indexes, key=score_index)
        best = candidates[best_index]
        selected.append(best)
        remaining_indexes.remove(best_index)
        for index in remaining_indexes:
            nearest_selected[index] = max(
                nearest_selected[index],
                _text_candidate_similarity(candidates[index], best),
            )
    return selected


def _compact_query_interests(
    value: object,
    *,
    disliked_topics: list[str],
    embedding_lookup: Callable[[str], list[float] | None] | None,
) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    candidates: list[_QueryInterestCandidate] = []
    pool = value[:_QUERY_INTEREST_CANDIDATE_POOL_CAP]
    pool_size = max(1, len(pool) - 1)
    for index, item in enumerate(pool):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        category = str(item.get("category", "")).strip()
        weight = _coerce_profile_float(item.get("weight"), 0.0)
        output = {
            "name": name,
            "category": category,
            "weight": item.get("weight", 0),
        }
        candidates.append(
            _QueryInterestCandidate(
                output=output,
                text=name,
                category=category,
                weight=weight,
                priority=1.0 - index / pool_size,
                vector=_lookup_query_embedding(name, embedding_lookup),
            )
        )

    disliked_candidates = _query_text_candidates(
        disliked_topics,
        cap=_QUERY_DISLIKED_TOPIC_CANDIDATE_POOL_CAP,
        embedding_lookup=embedding_lookup,
    )
    return [
        candidate.output
        for candidate in _select_diverse_query_interests(
            candidates,
            disliked_topics=disliked_candidates,
            cap=_QUERY_INTEREST_TAG_CAP,
        )
    ]


def _query_text_candidates(
    values: list[str],
    *,
    cap: int,
    embedding_lookup: Callable[[str], list[float] | None] | None,
) -> list[_QueryTextCandidate]:
    pool = values[:cap]
    pool_size = max(1, len(pool) - 1)
    candidates: list[_QueryTextCandidate] = []
    for index, text in enumerate(pool):
        clean = str(text).strip()
        if not clean:
            continue
        candidates.append(
            _QueryTextCandidate(
                text=clean,
                priority=1.0 - index / pool_size,
                vector=_lookup_query_embedding(clean, embedding_lookup),
            )
        )
    return candidates


def _compact_query_disliked_topics(
    value: object,
    *,
    embedding_lookup: Callable[[str], list[float] | None] | None,
) -> list[str]:
    if not isinstance(value, list):
        return []
    candidates = _query_text_candidates(
        [str(item).strip() for item in value if str(item).strip()],
        cap=_QUERY_DISLIKED_TOPIC_CANDIDATE_POOL_CAP,
        embedding_lookup=embedding_lookup,
    )
    return [
        candidate.text
        for candidate in _select_diverse_query_texts(
            candidates,
            cap=_QUERY_DISLIKED_TOPICS_CAP,
        )
    ]


def _compact_query_speculations(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    compacted: list[dict[str, object]] = []
    for item in value[:_QUERY_SPECULATIVE_INTEREST_CAP]:
        if not isinstance(item, dict):
            continue
        domain = str(item.get("domain", "")).strip()
        if domain:
            compacted.append({"domain": domain})
    return compacted


def _compact_query_str_list(value: object, cap: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for text in (str(item).strip() for item in value[:cap]) if text]


def build_query_generation_profile_summary(
    profile: SoulProfile,
    *,
    embedding_lookup: Callable[[str], list[float] | None] | None = None,
) -> dict[str, object]:
    """Build compact, stable profile context for discovery query generation.

    Search keywords, trending RIDs, explore domains, and keyword-planner batches
    need the user's stable taste shape, not the full high-churn profile state.
    This deliberately excludes recent awareness, active insights, timestamps,
    source provenance, and session context to keep prompt cost bounded and cache
    keys stable while preserving the fields that actually shape search terms.
    """
    full = build_profile_summary(profile)
    disliked_topic_candidates = _compact_query_str_list(
        full.get("disliked_topics"),
        _QUERY_DISLIKED_TOPIC_CANDIDATE_POOL_CAP,
    )
    summary: dict[str, object] = {
        "core_traits": _compact_query_str_list(full.get("core_traits"), _QUERY_PROFILE_LIST_CAP),
        "cognitive_style": _compact_query_str_list(
            full.get("cognitive_style"), _QUERY_PROFILE_LIST_CAP
        ),
        "values": _compact_query_str_list(full.get("values"), _QUERY_PROFILE_LIST_CAP),
        "motivational_drivers": _compact_query_str_list(
            full.get("motivational_drivers"), _QUERY_PROFILE_LIST_CAP
        ),
        "current_phase": full.get("current_phase", ""),
        "life_stage": full.get("life_stage", ""),
        "interest_domains": _compact_query_interest_domains(full.get("interest_domains")),
        "interests": _compact_query_interests(
            full.get("interests"),
            disliked_topics=disliked_topic_candidates,
            embedding_lookup=embedding_lookup,
        ),
        "disliked_topics": _compact_query_disliked_topics(
            disliked_topic_candidates,
            embedding_lookup=embedding_lookup,
        ),
        "deep_needs": _compact_query_str_list(full.get("deep_needs"), _QUERY_PROFILE_LIST_CAP),
        "style": full.get("style", {}),
        "exploration_openness": full.get("exploration_openness", 0.0),
    }
    speculations = _compact_query_speculations(full.get("speculative_interests"))
    if speculations:
        summary["speculative_interests"] = speculations
    mbti = full.get("mbti")
    if isinstance(mbti, dict) and mbti.get("type"):
        summary["mbti"] = {
            "type": mbti.get("type", ""),
            "confidence": mbti.get("confidence", 0.0),
            "dimensions": mbti.get("dimensions", {}),
        }
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
