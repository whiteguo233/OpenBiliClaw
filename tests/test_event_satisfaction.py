"""Tests for classify_event_satisfaction — deterministic rule table.

Part of the event-satisfaction signal work (see
``docs/plans/2026-05-16-event-satisfaction-signal.md``). The classifier
must be auditable and cheap — no LLM calls — so the test rules below
mirror the documented contract one-for-one.
"""

from __future__ import annotations

import pytest

from openbiliclaw.sources.event_format import classify_event_satisfaction

# --- Explicit positive types ---


@pytest.mark.parametrize("event_type", ["like", "coin", "favorite", "comment"])
def test_explicit_positive_engagement_types(event_type: str) -> None:
    category, reason = classify_event_satisfaction(
        {"event_type": event_type, "title": "X"}
    )
    assert category == "positive"
    assert reason == "explicit_engagement"


# --- Feedback events ---


def test_feedback_dislike_is_explicit_negative() -> None:
    category, reason = classify_event_satisfaction(
        {"event_type": "feedback", "metadata": {"feedback_type": "dislike"}}
    )
    assert (category, reason) == ("negative", "explicit_negative")


def test_feedback_thumbs_down_reaction_is_explicit_negative() -> None:
    category, reason = classify_event_satisfaction(
        {"event_type": "feedback", "metadata": {"reaction": "thumbs_down"}}
    )
    assert (category, reason) == ("negative", "explicit_negative")


@pytest.mark.parametrize("feedback_type", ["like", "comment"])
def test_feedback_like_or_comment_is_positive(feedback_type: str) -> None:
    category, reason = classify_event_satisfaction(
        {"event_type": "feedback", "metadata": {"feedback_type": feedback_type}}
    )
    assert (category, reason) == ("positive", "explicit_engagement")


def test_feedback_thumbs_up_reaction_is_positive() -> None:
    category, reason = classify_event_satisfaction(
        {"event_type": "feedback", "metadata": {"reaction": "thumbs_up"}}
    )
    assert (category, reason) == ("positive", "explicit_engagement")


# --- Click events with dwell ---


def test_click_meaningful_dwell_short_video() -> None:
    """18s on a 60s video is well above both thresholds (15s, 30%)."""
    category, reason = classify_event_satisfaction(
        {
            "event_type": "click",
            "metadata": {"watch_seconds": 18, "video_duration_seconds": 60},
        }
    )
    assert (category, reason) == ("positive", "meaningful_dwell")


def test_click_quick_exit_short_dwell_long_video() -> None:
    """2s on a 10min video is a clear quick-exit signal."""
    category, reason = classify_event_satisfaction(
        {
            "event_type": "click",
            "metadata": {"watch_seconds": 2, "video_duration_seconds": 600},
        }
    )
    assert (category, reason) == ("negative", "quick_exit")


def test_click_shallow_view_is_neutral() -> None:
    """10s on a 10min video — past quick-exit, short of meaningful dwell."""
    category, reason = classify_event_satisfaction(
        {
            "event_type": "click",
            "metadata": {"watch_seconds": 10, "video_duration_seconds": 600},
        }
    )
    assert (category, reason) == ("neutral", "shallow_view")


def test_click_with_no_watch_seconds_is_unknown() -> None:
    category, reason = classify_event_satisfaction(
        {"event_type": "click", "metadata": {"video_duration_seconds": 600}}
    )
    assert (category, reason) == ("unknown", "missing_dwell")


def test_click_reads_top_level_dwell_fields() -> None:
    """Producers may put watch_seconds at the top level; classifier reads both."""
    category, reason = classify_event_satisfaction(
        {
            "event_type": "click",
            "watch_seconds": 18,
            "video_duration_seconds": 60,
        }
    )
    assert (category, reason) == ("positive", "meaningful_dwell")


def test_click_falls_back_to_duration_key() -> None:
    """Legacy extension events use `duration` instead of `video_duration_seconds`."""
    category, reason = classify_event_satisfaction(
        {
            "event_type": "click",
            "metadata": {"watch_seconds": 18, "duration": 60},
        }
    )
    assert (category, reason) == ("positive", "meaningful_dwell")


# --- Passive browse ---


@pytest.mark.parametrize("event_type", ["snapshot", "scroll", "hover", "search"])
def test_passive_browse_events_are_neutral(event_type: str) -> None:
    category, reason = classify_event_satisfaction({"event_type": event_type})
    assert (category, reason) == ("neutral", "passive_browse")


# --- Unknown / fallback ---


def test_unknown_event_type_returns_fallback_without_raising() -> None:
    category, reason = classify_event_satisfaction(
        {"event_type": "totally_invented_action"}
    )
    assert (category, reason) == ("unknown", "fallback")


def test_malformed_event_does_not_raise() -> None:
    """A garbage payload (TypeError on metadata access) returns fallback."""
    # Non-dict metadata — accessing nested keys would raise on .get() if
    # the classifier didn't guard. Should return unknown/fallback silently.
    category, reason = classify_event_satisfaction(
        {"event_type": "click", "metadata": "not-a-dict"}
    )
    assert category == "unknown"
    assert reason == "fallback"


def test_twitter_engagement_scoring_v1() -> None:
    """X v1 mapping: like / bookmark→favorite / reply→comment score positive
    via the existing explicit set; retweet→share and follow stay context-tier.

    Regression guard — we must NEVER extend the global positive set just for X
    (that would silently change Bilibili/Douyin/YouTube follow/share scoring).
    """
    for event_type in ("like", "favorite", "comment"):
        category, _ = classify_event_satisfaction(
            {"event_type": event_type, "metadata": {"source_platform": "twitter"}}
        )
        assert category == "positive"
    for event_type in ("share", "follow"):
        category, _ = classify_event_satisfaction(
            {"event_type": event_type, "metadata": {"source_platform": "twitter"}}
        )
        assert category != "positive"
