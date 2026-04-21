"""Adaptive tone profile helpers for bilibili-style phrasing."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Literal, TypedDict

if TYPE_CHECKING:
    from .profile import SoulProfile

Density = Literal["light", "balanced", "dense"]
Warmth = Literal["cool", "warm", "companion"]
Playfulness = Literal["low", "medium", "high"]
Directness = Literal["soft", "balanced", "direct"]


class ToneProfile(TypedDict):
    """Shared tone profile used across recommendation, profile, and chat prompts."""

    density: Density
    warmth: Warmth
    playfulness: Playfulness
    directness: Directness


def _style_summary_from_preferences(
    preference_summary: Mapping[str, object] | None,
) -> Mapping[str, object]:
    if preference_summary is None:
        return {}
    raw_style = preference_summary.get("style")
    if isinstance(raw_style, Mapping):
        return raw_style
    return {}


def build_tone_profile(
    *,
    profile: SoulProfile | None,
    preference_summary: Mapping[str, object] | None,
    recent_feedback: list[dict[str, object]] | None,
) -> ToneProfile:
    """Infer a lightweight tone profile from user understanding."""
    tone: ToneProfile = {
        "density": "balanced",
        "warmth": "warm",
        "playfulness": "medium",
        "directness": "balanced",
    }
    if profile is None:
        return tone

    style_summary = _style_summary_from_preferences(preference_summary)
    profile_style = profile.preferences.style

    raw_depth = style_summary.get("depth_preference", profile_style.depth_preference)
    depth_preference = (
        float(raw_depth) if isinstance(raw_depth, (int, float)) else profile_style.depth_preference
    )
    raw_humor = style_summary.get("humor_preference", profile_style.humor_preference)
    humor_preference = (
        float(raw_humor) if isinstance(raw_humor, (int, float)) else profile_style.humor_preference
    )
    raw_duration = style_summary.get("preferred_duration", profile_style.preferred_duration)
    preferred_duration = str(raw_duration).strip().lower()

    portrait_text = " ".join(
        [
            profile.personality_portrait,
            " ".join(profile.core_traits),
            " ".join(profile.deep_needs),
        ]
    )
    dense_keyword_hits = sum(
        1
        for keyword in ("高信息密度", "想透", "复杂问题", "深度内容", "结构")
        if keyword in portrait_text
    )
    if depth_preference <= 0.35 or preferred_duration == "short":
        tone["density"] = "light"
    elif depth_preference >= 0.72 or dense_keyword_hits >= 2:
        tone["density"] = "dense"

    openness = 0.5
    raw_openness = preference_summary.get("exploration_openness") if preference_summary else None
    if isinstance(raw_openness, (int, float)):
        openness = float(raw_openness)
    else:
        openness = profile.preferences.exploration_openness
    playfulness_signal = max(openness, min(1.0, humor_preference))
    if playfulness_signal >= 0.8:
        tone["playfulness"] = "high"
    elif playfulness_signal >= 0.65:
        tone["playfulness"] = "medium"
    else:
        tone["playfulness"] = "low"

    feedback_items = recent_feedback or []
    negative_feedback = sum(
        1 for item in feedback_items if str(item.get("feedback_type", "")).strip() == "dislike"
    )
    if negative_feedback >= 2:
        tone["warmth"] = "companion"
        tone["directness"] = "soft"

    return tone
