"""Adaptive tone profile helpers for bilibili-style phrasing."""

from __future__ import annotations

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


def build_tone_profile(
    *,
    profile: SoulProfile | None,
    preference_summary: dict[str, object] | None,
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

    portrait_text = " ".join(
        [
            profile.personality_portrait,
            " ".join(profile.core_traits),
            " ".join(profile.deep_needs),
        ]
    )
    if any(
        keyword in portrait_text
        for keyword in ("高信息密度", "想透", "复杂问题", "深度内容", "结构")
    ):
        tone["density"] = "dense"

    openness = 0.5
    raw_openness = (
        preference_summary.get("exploration_openness")
        if preference_summary
        else None
    )
    if isinstance(raw_openness, (int, float)):
        openness = float(raw_openness)
    else:
        openness = profile.preferences.exploration_openness
    if openness >= 0.8:
        tone["playfulness"] = "high"
    elif openness >= 0.65:
        tone["playfulness"] = "medium"
    else:
        tone["playfulness"] = "low"

    feedback_items = recent_feedback or []
    negative_feedback = sum(
        1
        for item in feedback_items
        if str(item.get("feedback_type", "")).strip() == "dislike"
    )
    if negative_feedback >= 2:
        tone["warmth"] = "companion"
        tone["directness"] = "soft"

    return tone
