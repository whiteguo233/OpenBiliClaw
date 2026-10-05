"""Profile → prompt serialization views (the single serializer façade).

Every function that turns a profile object into prompt-bound text/dict lives
here (spec invariant V1 / CLAUDE.md prompt conventions). Content-pipeline code
in ``discovery`` / ``recommendation`` / ``runtime`` / ``sources`` consumes these
views instead of inventing private serializers; the legacy
``discovery/strategies/_utils.py`` import path re-exports the same objects for
backward compatibility.

The core public structured views:

* :func:`build_profile_summary` — canonical structured profile (portrait
  excluded); every source-platform content prompt feeds on this.
* :func:`compact_content_prompt_profile_summary` — caps a
  ``build_profile_summary`` dict for high-volume content prompts.
* :func:`build_query_generation_profile_summary` — query-trimmed taste shape for
  discovery keyword/domain generation (MMR-diversified, embedding-optional).
* :func:`build_cognition_profile_view_v1` — uncapped, deterministic cognition
  projection split into cache-stable soul/preference and volatile context.

This module was carved out of ``discovery/strategies/_utils.py`` verbatim
(Task 5, Wave B of the profile-views plan) — a mechanical move with zero
behaviour change, byte-equivalence-gated by ``tests/test_profile_views.py``.
Two leaf utilities that discovery-side callers still need
(:func:`normalize_match_text`, :func:`_coerce_query_embedding_vector`) live here
too and are re-exported from ``_utils``: the query-generation view depends on
them, and ``soul`` (this layer) must not import ``discovery``.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable

    from openbiliclaw.soul.profile import InterestDomain, OnionProfile, SoulProfile

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
_CONTENT_PROMPT_CORE_CAP = 20
_CONTENT_PROMPT_INTEREST_CAP = 48
_CONTENT_PROMPT_DOMAIN_CAP = 32
_CONTENT_PROMPT_SPECIFICS_PER_DOMAIN_CAP = 16
_CONTENT_PROMPT_RECENT_CAP = 12
_CONTENT_PROMPT_EVIDENCE_CAP = 8
_CONTENT_PROMPT_SPECULATION_CAP = 12
_RECENT_CONTEXT_VOLATILE_KEYS = {
    "created_at",
    "date",
    "session_context",
    "session_id",
    "timestamp",
    "updated_at",
}


def normalize_match_text(value: str) -> str:
    """Collapse whitespace and lowercase for fuzzy matching."""
    return re.sub(r"\s+", "", value).strip().lower()


@runtime_checkable
class SupportsIsoformat(Protocol):
    def isoformat(self) -> str: ...


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


def _format_profile_timestamp(value: object) -> str:
    """Serialize a profile timestamp-like value for JSON prompt summaries."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, SupportsIsoformat):
        return value.isoformat()
    return str(value)


_CHAT_EMPTY_PROFILE = "（尚未建立完整画像）"


@dataclass(frozen=True)
class ChatCoreMemory:
    """Chat core-memory split into a cache-stable prefix + a volatile tail.

    ``stable_block`` (portrait / identity / preference) is byte-stable across
    awareness churn, so it belongs in the system prompt where provider prompt
    caching keys on a byte-identical prefix. ``volatile_block`` (recent
    awareness + active insights) is rewritten every cognition cycle and moves to
    the user message, ahead of the turn content (most-stable-first ordering).
    """

    stable_block: str
    volatile_block: str


# Profile storage repeats the full interest tree under ``soul.interest`` even
# though cognition callers send the canonical preference layer beside it.  The
# compact cognition view removes that duplicate and storage-only revision
# markers, but deliberately does not cap active interests, dislikes, evidence,
# or unknown semantic fields.  Lifecycle evidence (state/count/first/last
# evidence times/parent) remains model-visible; it affects confidence and is
# not equivalent to a storage revision timestamp.
_COGNITION_PROFILE_INTERNAL_FIELDS = frozenset(
    {
        "_init_cognition_context",
        "awareness_candidates",
        "created_at",
        "insight_candidates",
        "profile_ready",
        "updated_at",
        "version",
    }
)
_COGNITION_SOUL_DUPLICATE_FIELDS = frozenset(
    {
        "active_insights",
        "interest",
        "preferences",
        "recent_awareness",
    }
)
# Record-level bookkeeping that must never reach a prompt. ``last_decay_at``
# is the incremental weight-decay cursor written by the preference analyzer:
# it changes on every merge, describes storage behaviour rather than user
# behaviour, and would otherwise spend prompt budget and shift the analyzer's
# ``max_prompt_chars`` chunking decision. The lifecycle evidence fields
# (``state`` / ``evidence_count`` / ``last_evidence_at`` / ``parent_topic``)
# stay model-visible on purpose; this one is a storage cursor.
_COGNITION_INTERNAL_RECORD_FIELDS = frozenset({"last_decay_at"})


def preference_prompt_payload(preference: Mapping[str, object]) -> dict[str, object]:
    """Return the LLM-facing projection of a persisted preference payload.

    Drops record-level bookkeeping (currently the ``last_decay_at`` decay
    cursor) from ``interests``. Every other field passes through unchanged, so
    a payload without the cursor renders byte-identically. Callers that
    serialize a preference payload outside
    :func:`build_cognition_profile_view_v1` — notably the legacy JSON dumps in
    ``llm.prompts`` — must run it through this helper first.
    """
    interests = preference.get("interests")
    if not isinstance(interests, list | tuple):
        return dict(preference)
    cleaned: list[object] = []
    changed = False
    for item in interests:
        if isinstance(item, Mapping) and any(
            key in item for key in _COGNITION_INTERNAL_RECORD_FIELDS
        ):
            item = {
                key: value
                for key, value in item.items()
                if key not in _COGNITION_INTERNAL_RECORD_FIELDS
            }
            changed = True
        cleaned.append(item)
    if not changed:
        return dict(preference)
    projected = dict(preference)
    projected["interests"] = cleaned
    return projected


def _cognition_value_is_empty(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return isinstance(value, (list, tuple, dict)) and not value


def _cognition_profile_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _cognition_profile_mapping(value, omit=_COGNITION_INTERNAL_RECORD_FIELDS)
    if isinstance(value, list | tuple):
        projected: list[object] = []
        for item in value:
            if _cognition_value_is_empty(item):
                continue
            detached = _cognition_profile_value(item)
            if not _cognition_value_is_empty(detached):
                projected.append(detached)
        return projected
    return value


def _cognition_profile_mapping(
    value: Mapping[str, object],
    *,
    omit: frozenset[str] = frozenset(),
) -> dict[str, object]:
    projected: dict[str, object] = {}
    for raw_key in sorted(value, key=str):
        key = str(raw_key)
        item = value[raw_key]
        if (
            key in omit
            or key in _COGNITION_PROFILE_INTERNAL_FIELDS
            or _cognition_value_is_empty(item)
        ):
            continue
        detached = _cognition_profile_value(item)
        if not _cognition_value_is_empty(detached):
            projected[key] = detached
    return projected


def _cognition_profile_sequence(value: object) -> list[object]:
    if not isinstance(value, list | tuple):
        return []
    projected: list[object] = []
    for item in value:
        if _cognition_value_is_empty(item):
            continue
        if isinstance(item, Mapping):
            state = str(item.get("state", "") or "").strip().lower()
            if state == "archived":
                continue
        detached = _cognition_profile_value(item)
        if not _cognition_value_is_empty(detached):
            projected.append(detached)
    return projected


def _cognition_preference_mapping(value: Mapping[str, object]) -> dict[str, object]:
    projected = _cognition_profile_mapping(value)
    # Only positive/active-interest collections apply lifecycle filtering.
    # Negative evidence is intentionally copied whole and never capped.
    for key in ("interest_domains", "interests", "likes", "speculative_interests"):
        raw_items = value.get(key)
        if isinstance(raw_items, list | tuple):
            items = _cognition_profile_sequence(raw_items)
            if items:
                projected[key] = items
            else:
                projected.pop(key, None)
    return projected


@dataclass(frozen=True)
class CognitionProfileViewV1:
    """Stable profile blocks plus uncapped volatile cognition context."""

    stable_soul: dict[str, object]
    stable_preference: dict[str, object]
    recent_awareness: tuple[object, ...]
    active_insights: tuple[object, ...]

    def volatile_cognition(self) -> dict[str, object]:
        """Return a detached JSON-ready volatile block."""

        result: dict[str, object] = {}
        if self.recent_awareness:
            result["recent_awareness"] = [
                _cognition_profile_value(item) for item in self.recent_awareness
            ]
        if self.active_insights:
            result["active_insights"] = [
                _cognition_profile_value(item) for item in self.active_insights
            ]
        return result


def build_cognition_profile_view_v1(
    *,
    soul_profile: Mapping[str, object] | None = None,
    preference_summary: Mapping[str, object] | None = None,
    recent_awareness: Sequence[object] | None = None,
    active_insights: Sequence[object] | None = None,
) -> CognitionProfileViewV1:
    """Build the named compact cognition profile view without mutating input.

    ``None`` for a volatile list means "derive it from the persisted soul
    snapshot".  Passing an explicit empty list suppresses that snapshot when a
    caller already supplies the authoritative current batch separately.
    """

    raw_soul = soul_profile or {}
    raw_preference = preference_summary or {}
    derived_awareness = raw_soul.get("recent_awareness")
    derived_insights = raw_soul.get("active_insights")
    awareness_source: object = derived_awareness if recent_awareness is None else recent_awareness
    insights_source: object = derived_insights if active_insights is None else active_insights
    return CognitionProfileViewV1(
        stable_soul=_cognition_profile_mapping(
            raw_soul,
            omit=_COGNITION_SOUL_DUPLICATE_FIELDS,
        ),
        stable_preference=_cognition_preference_mapping(raw_preference),
        recent_awareness=tuple(_cognition_profile_sequence(awareness_source)),
        active_insights=tuple(_cognition_profile_sequence(insights_source)),
    )


def _chat_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _chat_mapping(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _chat_dict_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def chat_core_memory(core_memory: Mapping[str, object]) -> ChatCoreMemory:
    """Render the chat core-memory dict into stable + volatile prompt blocks.

    Consumes the ``MemoryManager.get_core_memory()`` shape (``soul_summary`` /
    ``preference_summary`` / ``recent_awareness`` / ``active_insights``). That
    dict already reflects the *effective* profile (AI ⊕ user overrides): the
    manager applies overrides before calling this view (see
    ``MemoryManager.get_core_memory``), so chat honours manual profile edits.

    The stable block reproduces the pre-split render's portrait + 偏好摘要 verbatim
    and additionally surfaces the stable identity fields (核心特质 / 价值观 /
    深层需求 / MBTI) that are safe to cache; the volatile block carries exactly the
    近期观察 / 当前洞察 sections the pre-split render placed inline. Section titles
    match the historical ``## …`` headings to minimise model-visible drift.
    """
    soul = _chat_mapping(core_memory.get("soul_summary"))
    preference = _chat_mapping(core_memory.get("preference_summary"))
    recent_awareness = _chat_dict_list(core_memory.get("recent_awareness"))
    active_insights = _chat_dict_list(core_memory.get("active_insights"))

    top_interests = _chat_dict_list(preference.get("top_interests"))
    disliked_topics = _chat_str_list(preference.get("disliked_topics"))
    favorite_up_users = _chat_str_list(preference.get("favorite_up_users"))

    has_soul = any(soul.values())
    has_preference = bool(top_interests or disliked_topics or favorite_up_users)
    if not has_soul and not has_preference and not recent_awareness and not active_insights:
        return ChatCoreMemory(stable_block=_CHAT_EMPTY_PROFILE, volatile_block="")

    stable_sections: list[str] = []

    portrait = soul.get("personality_portrait")
    if portrait:
        stable_sections.append(f"## 用户画像\n{portrait}")

    identity_lines: list[str] = []
    core_traits = _chat_str_list(soul.get("core_traits"))
    if core_traits:
        identity_lines.append(f"核心特质: {', '.join(core_traits)}")
    values = _chat_str_list(soul.get("values"))
    if values:
        identity_lines.append(f"价值观: {', '.join(values)}")
    deep_needs = _chat_str_list(soul.get("deep_needs"))
    if deep_needs:
        identity_lines.append(f"深层需求: {', '.join(deep_needs)}")
    mbti_type = str(soul.get("mbti_type") or "").strip()
    if mbti_type:
        identity_lines.append(f"MBTI: {mbti_type}")
    if identity_lines:
        stable_sections.append("## 核心特质\n" + "\n".join(identity_lines))

    preference_lines: list[str] = []
    if top_interests:
        interest_text = ", ".join(str(item["name"]) for item in top_interests if item.get("name"))
        if interest_text:
            preference_lines.append(f"兴趣标签: {interest_text}")
    if disliked_topics:
        preference_lines.append(f"不喜欢: {', '.join(disliked_topics)}")
    if favorite_up_users:
        preference_lines.append(f"常看UP主: {', '.join(favorite_up_users)}")
    if preference_lines:
        stable_sections.append("## 偏好摘要\n" + "\n".join(preference_lines))

    volatile_sections: list[str] = []
    if recent_awareness:
        awareness_text = "\n".join(
            f"- [{item.get('date', '')}] {item.get('observation', '')}".strip()
            for item in recent_awareness
        )
        volatile_sections.append(f"## 近期观察\n{awareness_text}")
    if active_insights:
        insights_text = "\n".join(
            f"- {item.get('hypothesis', '')} "
            f"(置信度: {_coerce_profile_float(item.get('confidence', 0.0)):.0%})"
            for item in active_insights
        )
        volatile_sections.append(f"## 当前洞察\n{insights_text}")

    return ChatCoreMemory(
        stable_block="\n\n".join(stable_sections),
        volatile_block="\n\n".join(volatile_sections),
    )


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


# Topic-lifecycle serialization switch (Phase 4 / Task 8). Off by default so
# ``build_profile_summary`` stays byte-identical to the pre-lifecycle shape.
# When on, archived topics are excluded from the LLM-facing profile.
_TOPIC_LIFECYCLE_SERIALIZATION_ON = False


def set_topic_lifecycle_serialization(enabled: bool) -> None:
    """Toggle whether archived topics are excluded from profile serialization."""
    global _TOPIC_LIFECYCLE_SERIALIZATION_ON
    _TOPIC_LIFECYCLE_SERIALIZATION_ON = bool(enabled)


def topic_lifecycle_serialization_enabled() -> bool:
    """Return the current process-level archived-topic serialization switch."""
    return _TOPIC_LIFECYCLE_SERIALIZATION_ON


def _is_archived_state(value: object) -> bool:
    return str(value or "").strip().lower() == "archived"


def _likes_by_weight(
    profile: OnionProfile, *, exclude_archived: bool = False
) -> list[InterestDomain]:
    """Return non-blank interest domains in descending weight order."""
    return sorted(
        (
            dom
            for dom in profile.interest.likes
            if dom.domain.strip() and not (exclude_archived and _is_archived_state(dom.state))
        ),
        key=lambda dom: dom.weight,
        reverse=True,
    )


def _entry_weight(entry: dict[str, object]) -> float:
    weight = entry.get("weight")
    return float(weight) if isinstance(weight, (int, float)) else 0.0


def _extract_interest_domains(
    profile: SoulProfile, *, exclude_archived: bool = False
) -> list[dict[str, object]]:
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
            for dom in _likes_by_weight(profile, exclude_archived=exclude_archived)[
                :_INTEREST_DOMAIN_CAP
            ]
        ]

    # Flat SoulProfile: reconstruct domains from category grouping
    ranked_tags = sorted(
        (
            tag
            for tag in profile.preferences.interests
            if not (exclude_archived and _is_archived_state(getattr(tag, "state", "active")))
        ),
        key=lambda tag: tag.weight,
        reverse=True,
    )
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


def _extract_interest_tags(
    profile: SoulProfile, *, exclude_archived: bool = False
) -> list[dict[str, object]]:
    """Extract flat interest tags with provenance metadata."""
    from openbiliclaw.soul.profile import OnionProfile

    if isinstance(profile, OnionProfile):
        ranked = _likes_by_weight(profile, exclude_archived=exclude_archived)
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
        (
            tag
            for tag in profile.preferences.interests
            if tag.name.strip()
            and not (exclude_archived and _is_archived_state(getattr(tag, "state", "active")))
        ),
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
    exclude_archived_topics: bool | None = None,
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
    if exclude_archived_topics is None:
        exclude_archived_topics = _TOPIC_LIFECYCLE_SERIALIZATION_ON
    interest_domains = _extract_interest_domains(
        profile,
        exclude_archived=exclude_archived_topics,
    )
    summary: dict[str, object] = {
        "core_traits": profile.core_traits[:30],
        "cognitive_style": profile.cognitive_style[:30],
        "values": profile.values[:30],
        "motivational_drivers": profile.motivational_drivers[:30],
        "current_phase": profile.current_phase,
        "life_stage": profile.life_stage,
        "interest_domains": interest_domains,
        "interests": interests
        if interests is not None
        else _extract_interest_tags(profile, exclude_archived=exclude_archived_topics),
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


def _cap_profile_sequence(value: object, cap: int, *, newest: bool = False) -> object:
    if not isinstance(value, list):
        return value if value is not None else []
    if len(value) <= cap:
        return list(value)
    return list(value[-cap:] if newest else value[:cap])


def _strip_volatile_profile_entry_fields(value: object) -> object:
    if not isinstance(value, list):
        return value if value is not None else []
    compacted: list[object] = []
    for entry in value:
        if not isinstance(entry, dict):
            compacted.append(entry)
            continue
        compacted.append(
            {
                key: entry_value
                for key, entry_value in entry.items()
                if key not in _RECENT_CONTEXT_VOLATILE_KEYS
            }
        )
    return compacted


def _profile_weight(value: object) -> float:
    if not isinstance(value, dict):
        return 0.0
    try:
        return float(value.get("weight", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _cap_weighted_profile_dicts(value: object, cap: int) -> list[object]:
    if not isinstance(value, list):
        return []
    return sorted(list(value), key=_profile_weight, reverse=True)[:cap]


def _compact_interest_domains(value: object) -> list[object]:
    if not isinstance(value, list):
        return []
    domains = _cap_weighted_profile_dicts(value, _CONTENT_PROMPT_DOMAIN_CAP)
    compacted: list[object] = []
    for domain in domains:
        if not isinstance(domain, dict):
            compacted.append(domain)
            continue
        item = dict(domain)
        specifics = item.get("specifics")
        item["specifics"] = _cap_weighted_profile_dicts(
            specifics,
            _CONTENT_PROMPT_SPECIFICS_PER_DOMAIN_CAP,
        )
        compacted.append(item)
    return compacted


def _compact_active_insights(value: object) -> list[object]:
    if not isinstance(value, list):
        return []
    insights = list(value[-_CONTENT_PROMPT_RECENT_CAP:])
    compacted: list[object] = []
    for insight in insights:
        if not isinstance(insight, dict):
            compacted.append(insight)
            continue
        item = dict(insight)
        for key in _RECENT_CONTEXT_VOLATILE_KEYS:
            item.pop(key, None)
        evidence = item.get("evidence")
        if isinstance(evidence, list):
            item["evidence"] = list(evidence[:_CONTENT_PROMPT_EVIDENCE_CAP])
        compacted.append(item)
    return compacted


def compact_content_prompt_profile_summary(
    profile_summary: dict[str, object],
) -> dict[str, object]:
    """Return a smaller profile summary for high-volume content prompts.

    Discovery evaluation, recommendation expression, and pool classification
    all pay profile context repeatedly. Keep the highest-signal interests plus
    the newest awareness/insight windows, while preserving hard negatives such
    as ``disliked_topics`` unchanged.
    """

    compacted = dict(profile_summary)
    for key in ("core_traits", "cognitive_style", "values", "motivational_drivers", "deep_needs"):
        compacted[key] = _cap_profile_sequence(
            profile_summary.get(key),
            _CONTENT_PROMPT_CORE_CAP,
        )
    compacted["interests"] = _cap_weighted_profile_dicts(
        profile_summary.get("interests"),
        _CONTENT_PROMPT_INTEREST_CAP,
    )
    compacted["interest_domains"] = _compact_interest_domains(
        profile_summary.get("interest_domains"),
    )
    compacted["recent_awareness"] = _strip_volatile_profile_entry_fields(
        _cap_profile_sequence(
            profile_summary.get("recent_awareness"),
            _CONTENT_PROMPT_RECENT_CAP,
            newest=True,
        )
    )
    compacted["active_insights"] = _compact_active_insights(
        profile_summary.get("active_insights"),
    )
    compacted["speculative_interests"] = _cap_profile_sequence(
        profile_summary.get("speculative_interests"),
        _CONTENT_PROMPT_SPECULATION_CAP,
    )
    return compacted


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


def speculation(profile: SoulProfile | OnionProfile) -> str:
    """String-rendered profile context for the speculation-generation prompts.

    The interest speculator (``soul/speculator.py``) and the avoidance
    speculator (``soul/avoidance_speculator.py``) feed the profile to their LLM
    prompts as a natural-language ``## 段落`` block, not the structured dict the
    content pipeline consumes. This view collects that entry point into the
    façade (Task 7 / plan Wave C2): it delegates to the profile's own
    ``to_llm_context(include_portrait=False)`` renderer, so the output is
    section-for-section identical to the prior in-line call — a pure move with
    zero behaviour change. ``include_portrait=False`` keeps the free-form
    ``personality_portrait`` narrative out (portrait boundary, spec invariant
    V2); the guard suite pins both the exclusion and the byte-determinism.

    Both call sites pass an ``OnionProfile``; the flat legacy ``SoulProfile``
    renderer is covered too so the fork stays guarded across profile shapes.
    """
    return profile.to_llm_context(include_portrait=False)
