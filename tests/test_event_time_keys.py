"""Interaction-time keys are one contract, not one per platform.

Every producer fetched the real moment and then parked it under its own name:
``view_at`` (B 站历史), ``fav_time`` (收藏), ``mtime`` (关注), ``watched_at``
(YouTube Takeout), ``interaction_time`` (linux.do / 微博 / 知乎), ``starred_at``
(GitHub). The reader only looked at ``metadata.timestamp``, so those values were
persisted and invisible. These tests pin that the reader accepts every
interaction key and still refuses content-publication fields.
"""

from __future__ import annotations

import pytest

from openbiliclaw.soul.event_provenance import prepare_cognition_events
from openbiliclaw.soul.profile_builder import _history_timestamp
from openbiliclaw.sources.event_format import (
    INTERACTION_TIME_KEYS,
    parse_event_timestamp,
)

_EPOCH = 1_785_000_000  # 2026-07-09 UTC


@pytest.mark.parametrize("key", INTERACTION_TIME_KEYS)
def test_every_interaction_time_key_is_honoured(key: str) -> None:
    parsed = parse_event_timestamp({key: _EPOCH})

    assert parsed is not None
    assert parsed.year == 2026


@pytest.mark.parametrize("key", INTERACTION_TIME_KEYS)
def test_interaction_time_keys_accept_iso_text(key: str) -> None:
    parsed = parse_event_timestamp({key: "2026-08-01T10:00:00Z"})

    assert parsed is not None
    assert parsed.isoformat() == "2026-08-01T10:00:00+00:00"


def test_canonical_timestamp_wins_over_private_keys() -> None:
    parsed = parse_event_timestamp(
        {"timestamp": "2026-08-01T10:00:00Z", "view_at": "2020-01-01T00:00:00Z"}
    )

    assert parsed is not None
    assert parsed.year == 2026


@pytest.mark.parametrize("key", ("published_at", "pubtime", "created_at", "updated_at"))
def test_content_publication_time_is_not_event_time(key: str) -> None:
    """When the post was published is not when the user saved or watched it."""
    assert parse_event_timestamp({key: "2020-01-01T00:00:00Z"}) is None


@pytest.mark.parametrize("value", (0, -1, 0.0, True, False, "", "  ", "not-a-time"))
def test_unusable_values_are_ignored(value: object) -> None:
    assert parse_event_timestamp({"view_at": value}) is None


def test_private_key_time_is_promoted_into_the_prompt_shape() -> None:
    """The prompt only promises one time field; producers may not agree."""
    row = {
        "event_type": "like",
        "title": "微博点赞",
        "created_at": "2026-10-27 04:56:39",
        "metadata": {"interaction_time": "2026-08-01T10:00:00Z"},
    }

    [projected] = prepare_cognition_events([row])

    assert projected["metadata"]["timestamp"] == "2026-08-01T10:00:00+00:00"
    assert projected["metadata"]["time_quality"] == "source"
    assert "created_at" not in projected


def test_history_sampling_reads_private_key_times() -> None:
    """Init sampling must not fall back to arrival order for YouTube imports."""
    row = {"title": "某视频", "metadata": {"watched_at": "2026-07-01T02:00:00Z"}}

    assert _history_timestamp(row) == parse_event_timestamp(row["metadata"]).timestamp()  # type: ignore[union-attr]
