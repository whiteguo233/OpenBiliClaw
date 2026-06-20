"""Tests for ``normalize_tweet`` — tweet_to_dict dict → DiscoveredContent.

The input contract is the output of ``twitter_cli.serialization.tweet_to_dict``
(see ``src/openbiliclaw/sources/x_client.py``), NOT raw GraphQL JSON. Fixtures
under ``tests/fixtures/x/`` are authored as realistic ``tweet_to_dict`` dicts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.discovery.x_normalize import normalize_tweet

_FIXTURES = Path(__file__).parent / "fixtures" / "x"


def _load(name: str) -> dict[str, Any]:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


# ── plain single tweet ──────────────────────────────────────────────────


def test_plain_tweet_maps_core_fields() -> None:
    content = normalize_tweet(_load("plain_tweet.json"))
    assert content is not None
    assert isinstance(content, DiscoveredContent)
    assert content.content_id == "1790000000000000001"
    assert content.content_url == "https://x.com/janedoe/status/1790000000000000001"
    assert content.source_platform == "twitter"
    assert content.author_name == "@janedoe"
    # title is the (truncated) first line of the text
    assert (
        content.title == "Rust's async story finally clicked for me today. "
        "The borrow checker is a teacher, not a jailer. #rustlang #async"
    )
    # body_text is the full tweet text when there is no note_tweet long-form
    assert content.body_text.startswith("Rust's async story finally clicked")
    assert content.content_type == "tweet"
    assert content.view_count == 98765
    assert content.like_count == 1234
    assert content.reply_count == 78
    assert content.comment_count == 78
    assert content.retweet_count == 56
    assert content.share_count == 65
    assert content.bookmark_count == 321
    assert content.favorite_count == 321
    # media thumbnail becomes cover_url
    assert content.cover_url == "https://pbs.twimg.com/media/plain_cover.jpg"
    # hashtags from the text
    assert "rustlang" in content.tags
    assert "async" in content.tags


def test_plain_tweet_title_truncates_long_first_line() -> None:
    raw = _load("plain_tweet.json")
    raw["text"] = "x" * 400
    content = normalize_tweet(raw)
    assert content is not None
    assert len(content.title) < 400
    # full text preserved in body_text
    assert content.body_text == "x" * 400


def test_plain_tweet_title_is_first_line_only() -> None:
    raw = _load("plain_tweet.json")
    raw["text"] = "First line headline\nSecond line detail\nThird line"
    content = normalize_tweet(raw)
    assert content is not None
    assert content.title == "First line headline"
    assert content.body_text == "First line headline\nSecond line detail\nThird line"


# ── note_tweet long-form ────────────────────────────────────────────────


def test_note_tweet_uses_article_text_as_body() -> None:
    content = normalize_tweet(_load("note_tweet.json"))
    assert content is not None
    assert content.content_id == "1790000000000000002"
    assert content.source_platform == "twitter"
    assert content.author_name == "@systemsperson"
    # body_text is the long-form article text, not the short headline tweet
    assert content.body_text.startswith("Consensus is the heart")
    assert "leader election" in content.body_text
    # the short headline tweet text is NOT the body
    assert "Read the whole thing" not in content.body_text
    # long-form note_tweet is treated as a thread-shaped item
    assert content.content_type == "thread"


def test_note_tweet_title_prefers_article_title() -> None:
    content = normalize_tweet(_load("note_tweet.json"))
    assert content is not None
    assert content.title == "A Practical Guide to Consensus"


# ── thread (multi-tweet self-thread) ────────────────────────────────────


def test_thread_marker_sets_thread_content_type() -> None:
    content = normalize_tweet(_load("thread.json"))
    assert content is not None
    assert content.content_type == "thread"
    assert content.content_id == "1790000000000000003"
    # title is the first line (with the thread marker), body is the full text
    assert content.title.startswith("1/")
    assert "Most slowness is not the test runner" in content.body_text


# ── retweet / quote ─────────────────────────────────────────────────────


def test_quote_tweet_normalizes_and_keeps_own_id() -> None:
    content = normalize_tweet(_load("quote_tweet.json"))
    assert content is not None
    # we normalize the outer (quoting) tweet, keeping its own id/author
    assert content.content_id == "1790000000000000004"
    assert content.author_name == "@amplifier"
    assert content.content_url == "https://x.com/amplifier/status/1790000000000000004"
    # a quote/retweet without thread markers is a plain tweet
    assert content.content_type == "tweet"
    # video media thumbnail still becomes the cover
    assert content.cover_url == "https://pbs.twimg.com/amplify_video_thumb/quote_thumb.jpg"
    assert "signal" in content.tags


# ── tombstone / unavailable ─────────────────────────────────────────────


def test_tombstone_returns_none() -> None:
    assert normalize_tweet(_load("tombstone.json")) is None


def test_missing_id_returns_none() -> None:
    assert normalize_tweet({"text": "no id here", "author": {"screenName": "x"}}) is None


def test_empty_dict_returns_none() -> None:
    assert normalize_tweet({}) is None


# ── source_strategy pass-through ────────────────────────────────────────


def test_source_strategy_is_passed_through() -> None:
    content = normalize_tweet(_load("plain_tweet.json"), source_strategy="x-search")
    assert content is not None
    assert content.source_strategy == "x-search"


def test_source_strategy_defaults_empty() -> None:
    content = normalize_tweet(_load("plain_tweet.json"))
    assert content is not None
    assert content.source_strategy == ""


# ── purity: no media → empty cover, content survives ────────────────────


def test_text_only_tweet_has_empty_cover() -> None:
    content = normalize_tweet(_load("note_tweet.json"))
    assert content is not None
    assert content.cover_url == ""
