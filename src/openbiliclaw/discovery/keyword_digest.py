"""Stable, quantized digest of the keyword-shaping profile fields (P1.2).

``profile_kw_digest`` decides WHEN the unified keyword cache is invalidated
(see ``docs/plans/2026-06-14-discover-backpressure-refactor-design.md`` §8). It
is **path-agnostic** — it hashes the *current* profile regardless of which path
(chat / feedback event / 12h consolidation) changed it — and it is deliberately:

- **covering** the slow-moving fields that actually shape search keywords
  (interests, dislikes, traits, values, drivers, phase, cognitive style, style);
- **quantizing** interest / style weights into coarse buckets so per-event
  weight drift does NOT churn the cache;
- **excluding** high-churn / low-keyword-impact state (``recent_awareness``,
  ``active_insights``) and per-tag timestamps (``first_seen`` / ``last_seen``),
  which move constantly as the user browses but barely change search terms.

The digest is NOT the freshness mechanism (generation always reads the live
profile). It only proactively flushes stale ``pending`` keywords when the
profile materially changes — see the spec for the full rationale.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openbiliclaw.soul.profile import SoulProfile

# Coarse bucket so a single feedback event nudging a weight by <0.1 collapses to
# the same digest. 0.1 steps == the same granularity discovery already truncates
# interests at.
_WEIGHT_BUCKET = 0.1
# Cap the interests that feed the digest: the strongest interests dominate
# keyword generation, so churn in the long tail below this cap must not flip the
# digest. Generous enough to capture the meaningful head.
_TOP_INTERESTS = 64


def _bucket(weight: object) -> float:
    try:
        value = float(weight)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return round(round(value / _WEIGHT_BUCKET) * _WEIGHT_BUCKET, 1)


def _clean_sorted(values: object) -> list[str]:
    if not isinstance(values, (list, tuple, set, frozenset)):
        return []
    return sorted({str(v).strip() for v in values if str(v).strip()})


def profile_kw_digest(profile: SoulProfile) -> str:
    """Return a short stable hex digest of the keyword-shaping profile fields."""
    prefs = profile.preferences
    ranked = sorted(prefs.interests, key=lambda tag: float(tag.weight or 0.0), reverse=True)
    interests = sorted(
        (str(tag.name).strip(), str(tag.category or "").strip(), _bucket(tag.weight))
        for tag in ranked[:_TOP_INTERESTS]
        if str(tag.name).strip()
    )
    style = prefs.style
    payload: dict[str, object] = {
        "interests": interests,
        "disliked_topics": _clean_sorted(prefs.disliked_topics),
        "core_traits": _clean_sorted(profile.core_traits),
        "values": _clean_sorted(profile.values),
        "motivational_drivers": _clean_sorted(profile.motivational_drivers),
        "cognitive_style": _clean_sorted(profile.cognitive_style),
        "current_phase": str(profile.current_phase or "").strip(),
        "life_stage": str(profile.life_stage or "").strip(),
        "style": {
            "preferred_duration": str(style.preferred_duration or "").strip(),
            "preferred_pace": str(style.preferred_pace or "").strip(),
            "depth": _bucket(style.depth_preference),
            "humor": _bucket(style.humor_preference),
        },
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
