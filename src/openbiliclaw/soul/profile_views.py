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

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
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


def build_director_profile_view(profile: SoulProfile, *, fast: bool = False) -> dict[str, object]:
    """Recommendation-specific stable taste and recent concerns, without portrait.

    Engineering context caps (not calibrated): fast decisions retain 12 interests
    and 3 recent observations, slow planning 32 and 6. Explicit dislikes are never
    truncated. Caller payload limits must reject oversize inputs, not erase bans.
    """
    summary = compact_content_prompt_profile_summary(
        build_profile_summary(profile, exclude_archived_topics=True)
    )
    stable = {
        key: summary[key]
        for key in (
            "cognitive_style",
            "core_traits",
            "values",
            "deep_needs",
            "style",
            "context",
            "exploration_openness",
            "source_platform_mix",
        )
    }
    for key in ("cognitive_style", "core_traits", "values", "deep_needs"):
        stable[key] = _cap_profile_sequence(stable[key], 4 if fast else 8)
    stable["interests"] = _cap_weighted_profile_dicts(summary["interests"], 12 if fast else 32)
    stable["interest_domains"] = _cap_weighted_profile_dicts(
        summary["interest_domains"], 6 if fast else 12
    )
    stable["disliked_topics"] = list(profile.preferences.disliked_topics)
    recent = {
        "current_phase": summary["current_phase"],
        "life_stage": summary["life_stage"],
        "awareness": _cap_profile_sequence(
            summary["recent_awareness"], 3 if fast else 6, newest=True
        ),
        "insights": [
            {
                "hypothesis": insight.hypothesis,
                "evidence": insight.evidence[:4],
                "confidence": insight.confidence,
                "validated": insight.validated,
                "user_verdict": insight.user_verdict,
            }
            for insight in profile.active_insights
            if insight.user_verdict != "rejected"
        ][-(3 if fast else 6) :],
        "speculative_interests": _cap_profile_sequence(
            summary.get("speculative_interests", []), 3 if fast else 6
        ),
    }
    return {"schema_version": "director-profile.v1", "stable": stable, "recent": recent}


PROFILE_CATALOG_VERSION = "on-demand-profile-catalog.v1"
PROFILE_CONTEXT_VERSION = "on-demand-profile-context.v1"
# Engineering selection policy, not a calibrated relevance score: alternate
# task-related facts with two global taste anchors before filling spare space.
# These versions must change when projection/selection semantics change.
_PROFILE_GLOBAL_ANCHORS = 2
_PROFILE_OPTIONAL_FIELDS = (
    "core_traits",
    "cognitive_style",
    "values",
    "motivational_drivers",
    "deep_needs",
    "current_phase",
    "life_stage",
    "style",
    "context",
    "exploration_openness",
    "source_platform_mix",
)


def _profile_context_json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _profile_context_copy(value: dict[str, object]) -> dict[str, object]:
    copied: dict[str, object] = json.loads(_profile_context_json(value))
    return copied


def _profile_catalog_record(value: object) -> dict[str, object]:
    """Detach a semantic record, omitting only empty/storage-only fields."""
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    if not isinstance(value, dict):
        return {}
    return {
        str(key): item
        for key, item in value.items()
        if key not in {"last_decay_at", "favorite_up_users", "personality_portrait"}
        and item not in ("", None, [], {})
    }


def _profile_catalog_records(
    value: object, *, active_only: bool = False
) -> list[dict[str, object]]:
    if not isinstance(value, list | tuple):
        return []
    records: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in value:
        record = _profile_catalog_record(item)
        if not record:
            continue
        if active_only and (
            _is_archived_state(record.get("state"))
            or str(record.get("user_verdict", "")).casefold() == "rejected"
            or str(record.get("status", "")).casefold()
            in {"rejected", "deferred", "archived", "promoted"}
        ):
            continue
        identity = _profile_context_json(record)
        if identity not in seen:
            seen.add(identity)
            records.append(record)
    return records


def build_profile_catalog(profile: SoulProfile | OnionProfile) -> dict[str, object]:
    """Build an uncapped local retrieval catalog, never a whole-model prompt.

    Unlike the legacy compact views, this traverses all active positive records
    directly. Negative scopes stay intact, archived/rejected hypotheses cannot
    be resurrected, and portrait/creator lists never enter this content view.
    Callers must pass the effective profile (including user overrides).
    """
    from openbiliclaw.soul.profile import OnionProfile

    raw = profile.to_dict()
    preference = profile.preferences
    interests: list[dict[str, object]] = []
    mandatory: dict[str, object]
    if isinstance(profile, OnionProfile):
        interest_tree = raw.get("interest")
        assert isinstance(interest_tree, dict)
        # Do not use the compatibility shim's flattened disliked_topics: it
        # erases the relationship between a domain and its scoped specifics.
        mandatory = {"disliked_domains": _profile_catalog_records(interest_tree.get("dislikes"))}
        for domain in _profile_catalog_records(interest_tree.get("likes"), active_only=True):
            name = domain.get("domain")
            if not isinstance(name, str) or not name.strip():
                continue
            interests.append(
                {
                    **{key: value for key, value in domain.items() if key != "specifics"},
                    "name": name,
                    "category": name,
                }
            )
            interests[-1].pop("domain", None)
            for specific in _profile_catalog_records(domain.get("specifics"), active_only=True):
                if not isinstance(specific.get("name"), str) or not str(specific["name"]).strip():
                    continue
                # A specific inherits lifecycle from its parent, not evidence
                # counts that were measured only for the domain as a whole.
                interests.append(
                    {"category": name, "state": domain.get("state", "active"), **specific}
                )
    else:
        raw_preference = raw.get("preferences")
        assert isinstance(raw_preference, dict)
        interests = [
            item
            for item in _profile_catalog_records(raw_preference.get("interests"), active_only=True)
            if isinstance(item.get("name"), str) and str(item["name"]).strip()
        ]
        mandatory = {"disliked_topics": list(preference.disliked_topics)}
    core: dict[str, object] = {
        key: getattr(profile, key)
        for key in (
            "core_traits",
            "cognitive_style",
            "values",
            "motivational_drivers",
            "deep_needs",
            "current_phase",
            "life_stage",
        )
    }
    core.update(
        {
            "exploration_openness": preference.exploration_openness,
            "source_platform_mix": dict(preference.source_platform_mix),
        }
    )
    # Explicit duration/pace/session conditions cannot disappear merely
    # because the optional positive-interest budget is small.
    mandatory["style"] = _profile_catalog_record(preference.style)
    mandatory["context"] = _profile_catalog_record(preference.context)
    return _profile_context_copy(
        {
            "schema_version": PROFILE_CATALOG_VERSION,
            "mandatory": mandatory,
            "core": core,
            "interests": _profile_catalog_records(interests, active_only=True),
            "recent_awareness": _profile_catalog_records(raw.get("recent_awareness")),
            "active_insights": _profile_catalog_records(
                raw.get("active_insights"), active_only=True
            ),
            "speculative_interests": _profile_catalog_records(
                getattr(profile, "_active_speculations", None), active_only=True
            ),
        }
    )


@dataclass(frozen=True)
class ProfileContextProjection:
    """Immutable selected context; ``payload`` returns a detached JSON dict.

    Bytes count canonical UTF-8 JSON, not model tokens. ``fits=False`` retains
    the complete mandatory block for diagnosis; it must not be sent as-is.
    """

    _payload_json: str
    digest: str
    fits: bool
    used_bytes: int
    included_interest_names: tuple[str, ...]

    @property
    def payload(self) -> dict[str, object]:
        """Return a fresh copy so a caller cannot change evidence after hashing."""
        result: dict[str, object] = json.loads(self._payload_json)
        return result


def _profile_lexical_tokens(text: str) -> frozenset[str]:
    # Whole Latin terms plus Chinese bigrams: deterministic, dependency-free
    # retrieval only. This is not an embedding or a model-quality guarantee.
    tokens: set[str] = set()
    for part in re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]+", text.casefold()):
        if re.fullmatch(r"[\u3400-\u9fff]+", part):
            tokens.update(part[index : index + 2] for index in range(max(1, len(part) - 1)))
        else:
            tokens.add(part)
    return frozenset(tokens)


def _profile_task_relevance(value: object, query_tokens: frozenset[str]) -> float:
    text = value if isinstance(value, str) else _profile_context_json(value)
    tokens = _profile_lexical_tokens(text)
    return len(tokens & query_tokens) / max(1, len(tokens))


def select_profile_context(
    catalog: dict[str, object],
    *,
    query_texts: Sequence[str],
    max_bytes: int,
    purpose: str = "evaluation",
) -> ProfileContextProjection:
    """Pack complete facts by task relevance plus global taste, without I/O.

    Mandatory negative scopes are indivisible. Optional facts are added whole
    or omitted, never string-sliced or semantically summarized. Small budgets
    may therefore leave unused space. Query text is retrieval data, never an
    instruction; selection cannot promote a hypothesis to a confirmed fact.
    """
    if type(max_bytes) is not int or max_bytes < 0:
        raise ValueError("profile byte budget must be a nonnegative integer")
    if not isinstance(purpose, str) or not purpose.strip():
        raise ValueError("profile projection purpose must be nonempty")
    if isinstance(query_texts, str | bytes) or any(
        not isinstance(item, str) for item in query_texts
    ):
        raise ValueError("profile query texts must be a sequence of strings")
    if catalog.get("schema_version") != PROFILE_CATALOG_VERSION:
        raise ValueError("unsupported profile catalog schema")
    snapshot = _profile_context_copy(catalog)
    mandatory = snapshot.get("mandatory")
    core = snapshot.get("core")
    if not isinstance(mandatory, dict) or not isinstance(core, dict):
        raise ValueError("profile catalog requires mandatory and core mappings")
    if any(
        key not in {"disliked_topics", "disliked_domains", "style", "context"} for key in mandatory
    ):
        raise ValueError("unsupported mandatory profile field")
    payload: dict[str, object] = {
        "schema_version": PROFILE_CONTEXT_VERSION,
        "purpose": purpose,
        "view_is_partial": True,
        **mandatory,
        "interests": [],
    }
    used_bytes = len(_profile_context_json(payload).encode("utf-8"))
    names: list[str] = []

    def finish() -> ProfileContextProjection:
        serialized = _profile_context_json(payload)
        return ProfileContextProjection(
            _payload_json=serialized,
            digest=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
            fits=used_bytes <= max_bytes,
            used_bytes=used_bytes,
            included_interest_names=tuple(names),
        )

    if used_bytes > max_bytes:
        return finish()
    queries = frozenset().union(*(_profile_lexical_tokens(text) for text in query_texts))
    interests = _profile_catalog_records(snapshot.get("interests"), active_only=True)

    def ranked_key(item: dict[str, object]) -> tuple[float, float, str]:
        # Match semantic labels, not JSON keys/provenance bookkeeping.
        labels = " ".join(str(item.get(key, "")) for key in ("name", "category"))
        return (
            -_profile_task_relevance(labels, queries),
            -_profile_weight(item),
            _profile_context_json(item),
        )

    ranked = sorted(interests, key=ranked_key)
    related = [item for item in ranked if ranked_key(item)[0] < 0]
    anchors = sorted(
        interests, key=lambda item: (-_profile_weight(item), _profile_context_json(item))
    )[:_PROFILE_GLOBAL_ANCHORS]
    core_facts: list[tuple[str, object, bool]] = []
    for key in _PROFILE_OPTIONAL_FIELDS:
        value = core.get(key)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, list):
            core_facts.extend((key, item, True) for item in value)
        else:
            core_facts.append((key, value, False))
    core_facts.sort(key=lambda fact: -_profile_task_relevance(fact[1], queries))
    context_facts: list[tuple[str, object, bool]] = []
    for key in ("recent_awareness", "active_insights", "speculative_interests"):
        values = _profile_catalog_records(snapshot.get(key), active_only=True)
        # Stable sort retains newest-first order when lexical relevance ties.
        values.reverse()
        values.sort(key=lambda item: -_profile_task_relevance(item, queries))
        context_facts.extend((key, item, True) for item in values)
    context_facts.sort(key=lambda fact: -_profile_task_relevance(fact[1], queries))
    queues: list[list[tuple[str, object, bool]]] = [
        [("interests", item, True) for item in related],
        [("interests", item, True) for item in anchors],
        core_facts,
        context_facts,
    ]
    ordered: list[tuple[str, object, bool]] = []
    for index in range(max((len(queue) for queue in queues), default=0)):
        ordered.extend(queue[index] for queue in queues if index < len(queue))
    ordered.extend(("interests", item, True) for item in ranked)
    seen: set[tuple[str, str]] = set()
    for key, item, sequence in ordered:
        encoded = _profile_context_json(item)
        identity = (key, encoded)
        if identity in seen:
            continue
        seen.add(identity)
        if key in payload:
            current = payload[key]
            if not sequence or not isinstance(current, list):
                continue
            extra_bytes = len(encoded.encode("utf-8")) + bool(current)
        else:
            # Existing object is nonempty; account for comma, key, colon, and
            # optional list brackets without serializing the growing payload.
            extra_bytes = len(_profile_context_json(key).encode("utf-8")) + 2
            extra_bytes += len(encoded.encode("utf-8")) + (2 if sequence else 0)
        if used_bytes + extra_bytes > max_bytes:
            continue
        if sequence:
            current = payload.setdefault(key, [])
            assert isinstance(current, list)
            current.append(item)
        else:
            payload[key] = item
        used_bytes += extra_bytes
        if key == "interests" and isinstance(item, dict):
            name = item.get("name")
            if isinstance(name, str) and name not in names:
                names.append(name)
    return finish()


def build_shared_evaluation_profiles(
    contexts: dict[str, dict[str, object]],
) -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    """Factor identical mandatory facts once without changing any candidate view.

    Only exclusions, style and current context may move to the shared profile.
    Positive selections remain candidate-specific and never leak across peers.
    Empty contexts support the evaluator's local budget probes.
    """
    snapshots = {key: _profile_context_copy(value) for key, value in contexts.items()}
    shared: dict[str, object] = {}
    if snapshots:
        first = next(iter(snapshots.values()))
        for field in ("disliked_topics", "disliked_domains", "style", "context"):
            if field in first and all(
                field in value and value[field] == first[field] for value in snapshots.values()
            ):
                shared[field] = first[field]
                for value in snapshots.values():
                    del value[field]
    return shared, snapshots


def resize_profile_context(
    payload: dict[str, object],
    *,
    max_bytes: int,
    query_texts: Sequence[str] = (),
) -> ProfileContextProjection:
    """Repack only already-loaded facts for a narrower decision envelope.

    There is no source-profile lookup, summarization or I/O. Unknown top-level
    fields are mandatory so future conditions cannot be silently discarded.
    A zero budget returns the entire mandatory floor with ``fits=False``.
    """
    if payload.get("schema_version") != PROFILE_CONTEXT_VERSION:
        raise ValueError("unsupported profile context schema")
    if type(max_bytes) is not int or max_bytes < 0:
        raise ValueError("profile byte budget must be a nonnegative integer")
    snapshot = _profile_context_copy(payload)
    purpose = snapshot.get("purpose")
    if not isinstance(purpose, str) or not purpose.strip():
        raise ValueError("profile projection purpose must be nonempty")
    mandatory_keys = {"disliked_topics", "disliked_domains", "style", "context"}
    core_keys = set(_PROFILE_OPTIONAL_FIELDS) - mandatory_keys
    record_keys = {"recent_awareness", "active_insights", "speculative_interests"}
    optional_keys = core_keys | record_keys | {"interests"}
    preserved = {key: value for key, value in snapshot.items() if key not in optional_keys}
    catalog: dict[str, object] = {
        "schema_version": PROFILE_CATALOG_VERSION,
        "mandatory": {key: value for key, value in snapshot.items() if key in mandatory_keys},
        "core": {key: value for key, value in snapshot.items() if key in core_keys},
        "interests": snapshot.get("interests", []),
    }
    for key in record_keys:
        # Catalog selection reads chronological records newest-first. This
        # input is already a selected/ranked view, so retain its tie order.
        value = snapshot.get(key, [])
        catalog[key] = list(reversed(value)) if isinstance(value, list) else value
    minimum = select_profile_context(catalog, query_texts=(), max_bytes=0, purpose=purpose)
    preserved_minimum = {**minimum.payload, **preserved}
    mandatory_overhead = (
        len(_profile_context_json(preserved_minimum).encode("utf-8")) - minimum.used_bytes
    )
    selected = select_profile_context(
        catalog,
        query_texts=query_texts,
        max_bytes=max(0, max_bytes - mandatory_overhead),
        purpose=purpose,
    )
    serialized = _profile_context_json({**selected.payload, **preserved})
    used_bytes = len(serialized.encode("utf-8"))
    return ProfileContextProjection(
        _payload_json=serialized,
        digest=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        fits=used_bytes <= max_bytes,
        used_bytes=used_bytes,
        included_interest_names=selected.included_interest_names,
    )


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
