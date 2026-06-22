"""Tests for closed style_key viewing-mode taxonomy."""

from openbiliclaw.discovery.style_keys import (
    LEGACY_STYLE_KEY_MAP,
    VALID_STYLE_KEYS,
    normalize_style_key,
)


def test_valid_style_keys_are_closed_viewing_modes() -> None:
    assert (
        frozenset(
            {
                "deep_focus",
                "quick_scan",
                "hands_on",
                "decision_support",
                "story_immersion",
                "opinion_sparring",
                "social_chat",
                "daily_wander",
                "mood_release",
                "aesthetic_browse",
                "ambient_companion",
                "live_pulse",
                "curiosity_spark",
            }
        )
        == VALID_STYLE_KEYS
    )


def test_legacy_style_keys_normalize_to_viewing_modes() -> None:
    expected = {
        "deep_dive": "deep_focus",
        "tech_analysis": "deep_focus",
        "music_analysis": "deep_focus",
        "news_brief": "quick_scan",
        "practical_guide": "hands_on",
        "tutorial_short": "hands_on",
        "game_strategy": "hands_on",
        "review_roundup": "decision_support",
        "unboxing_experience": "decision_support",
        "story_doc": "story_immersion",
        "emotional_narrative": "story_immersion",
        "true_crime": "story_immersion",
        "opinion_stand": "opinion_sparring",
        "light_chat": "social_chat",
        "lifestyle": "daily_wander",
        "fun_variety": "mood_release",
        "parody_remix": "mood_release",
        "visual_showcase": "aesthetic_browse",
        "audio_background": "ambient_companion",
        "music_live": "live_pulse",
        "live_moment": "live_pulse",
        "sports_highlight": "live_pulse",
        "sci_fact": "curiosity_spark",
    }

    assert expected == LEGACY_STYLE_KEY_MAP
    for legacy_key, viewing_mode in expected.items():
        assert normalize_style_key(legacy_key) == viewing_mode


def test_normalize_style_key_keeps_valid_modes_and_drops_unknowns() -> None:
    assert normalize_style_key(" Deep Focus ") == "deep_focus"
    assert normalize_style_key("not_a_real_style") == ""
