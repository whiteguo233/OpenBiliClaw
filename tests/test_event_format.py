"""Regression tests for the unified cross-source event format.

The v0.3.22 unification consolidated B站 / 小红书 / future-source
event producers behind ``build_event()`` so the soul-pipeline LLM
analyzers see one consistent shape — including a natural-language
``context`` field they can read directly. These tests pin the
contract so future regressions don't silently re-fragment it.
"""

from __future__ import annotations

from openbiliclaw.cli import _history_item_to_event
from openbiliclaw.sources.event_format import (
    COMMENT_TEXT_MAX_CHARS,
    SOURCE_BILIBILI,
    SOURCE_WEIBO,
    SOURCE_XIAOHONGSHU,
    SOURCE_YOUTUBE,
    build_event,
    default_signal_strength_for_event,
    format_event_context,
    normalize_comment_kind,
    sanitize_comment_text,
)
from openbiliclaw.sources.xhs_tasks import xhs_bootstrap_notes_to_events

# ---------------------------------------------------------------------------
# format_event_context: deterministic Chinese sentence builder


def test_format_context_bilibili_view_with_author() -> None:
    text = format_event_context(
        event_type="view",
        source_platform=SOURCE_BILIBILI,
        title="讲透历史叙事",
        author="历史实验室",
    )
    assert text == "在B 站看了《讲透历史叙事》,作者:历史实验室"


def test_format_context_xiaohongshu_like_with_author() -> None:
    text = format_event_context(
        event_type="like",
        source_platform=SOURCE_XIAOHONGSHU,
        title="手冲咖啡入门",
        author="豆子老师",
    )
    assert text == "在小红书点赞了《手冲咖啡入门》,作者:豆子老师"


def test_format_context_weibo_share_with_author() -> None:
    text = format_event_context(
        event_type="share",
        source_platform=SOURCE_WEIBO,
        title="开源项目进展",
        author="项目作者",
    )
    assert text == "在微博分享了《开源项目进展》,作者:项目作者"


def test_format_context_unknown_event_type_falls_back() -> None:
    """Unknown event_type strings shouldn't crash — they fall through
    to a generic verb so the rendered sentence is still readable."""
    text = format_event_context(
        event_type="custom_action",
        source_platform=SOURCE_BILIBILI,
        title="一个新行为",
    )
    assert "B 站" in text
    assert "《一个新行为》" in text
    assert "记录了" in text  # generic fallback verb


def test_format_context_missing_title_uses_placeholder() -> None:
    text = format_event_context(
        event_type="favorite",
        source_platform=SOURCE_BILIBILI,
        title="",
    )
    assert text == "在B 站收藏了一条内容"


# ---------------------------------------------------------------------------
# build_event: shape contract (the actual unification point)


def test_build_event_emits_unified_shape() -> None:
    event = build_event(
        event_type="favorite",
        source_platform=SOURCE_BILIBILI,
        title="某个 UP 主的视频",
        url="https://www.bilibili.com/video/BVxxxx",
        author="某 UP 主",
        metadata={"folder": "技术", "bvid": "BVxxxx"},
    )
    # Required keys
    assert event["event_type"] == "favorite"
    assert event["title"] == "某个 UP 主的视频"
    assert event["url"] == "https://www.bilibili.com/video/BVxxxx"
    assert event["context"]  # non-empty natural-language description
    assert event["source_platform"] == SOURCE_BILIBILI
    assert event["content_id"] == "BVxxxx"
    assert event["source_confidence"] == "exact"
    # Metadata invariants
    assert event["metadata"]["source_platform"] == SOURCE_BILIBILI
    assert event["metadata"]["author"] == "某 UP 主"
    # Source-specific extras preserved
    assert event["metadata"]["folder"] == "技术"
    assert event["metadata"]["bvid"] == "BVxxxx"


def test_build_event_explicit_context_wins_over_auto_generated() -> None:
    event = build_event(
        event_type="favorite",
        source_platform=SOURCE_XIAOHONGSHU,
        title="手冲咖啡入门",
        author="豆子老师",
        context="自定义描述",
    )
    assert event["context"] == "自定义描述"


def test_build_event_url_omitted_when_empty() -> None:
    """URL is optional — events without one (e.g. follow events) shouldn't
    carry a key with empty-string value."""
    event = build_event(
        event_type="follow",
        source_platform=SOURCE_BILIBILI,
        title="某 UP",
        author="某 UP",
    )
    assert "url" not in event


def test_build_event_without_source_does_not_claim_exact_attribution() -> None:
    event = build_event(event_type="view", source_platform="", title="未知来源")

    assert event["source_platform"] == ""
    assert event["source_confidence"] == "legacy_unknown"


def test_build_event_top_level_source_platform_wins_over_metadata() -> None:
    """The shared resolver uses one priority order at every ingestion layer."""
    event = build_event(
        event_type="view",
        source_platform=SOURCE_BILIBILI,
        title="...",
        metadata={"source_platform": "web"},
    )
    assert event["metadata"]["source_platform"] == SOURCE_BILIBILI
    assert event["source_platform"] == SOURCE_BILIBILI


def test_build_event_infers_url_before_legacy_fallback() -> None:
    event = build_event(
        event_type="view",
        source_platform="",
        legacy_platform=SOURCE_BILIBILI,
        url="https://www.youtube.com/watch?v=video-42",
        title="YouTube 视频",
    )

    assert event["source_platform"] == SOURCE_YOUTUBE
    assert event["source_confidence"] == "inferred"


def test_build_event_cannot_upgrade_url_inference_to_exact() -> None:
    event = build_event(
        event_type="view",
        source_platform="",
        source_confidence="exact",
        url="https://x.com/example/status/123",
        title="X 帖子",
    )

    assert event["source_platform"] == "twitter"
    assert event["source_confidence"] == "inferred"


def test_build_event_keeps_top_level_content_identity_in_sync() -> None:
    event = build_event(
        event_type="view",
        source_platform=SOURCE_XIAOHONGSHU,
        title="一条笔记",
        metadata={"note_id": "note-42"},
    )

    assert event["content_id"] == "note-42"
    assert event["metadata"]["note_id"] == "note-42"


def test_build_event_adds_default_signal_strength_without_overriding_source_value() -> None:
    favorite = build_event(
        event_type="favorite",
        source_platform=SOURCE_BILIBILI,
        title="收藏视频",
    )
    assert favorite["metadata"]["signal_strength"] == 1.0

    passive_view = build_event(
        event_type="view",
        source_platform=SOURCE_BILIBILI,
        title="浏览历史",
    )
    assert passive_view["metadata"]["signal_strength"] == 0.35

    youtube_subscription = build_event(
        event_type="follow",
        source_platform="youtube",
        title="某频道",
        metadata={"signal_strength": 1.0},
    )
    assert youtube_subscription["metadata"]["signal_strength"] == 1.0

    negative_feedback = build_event(
        event_type="feedback",
        source_platform=SOURCE_BILIBILI,
        title="不合口味的视频",
        metadata={"feedback_type": "dislike"},
    )
    assert negative_feedback["metadata"]["signal_strength"] == 1.0


# ---------------------------------------------------------------------------
# Producers all converge on the unified shape


def _has_unified_shape(event: dict) -> bool:
    """Every cross-source event must satisfy these invariants."""
    if not isinstance(event, dict):
        return False
    for key in ("event_type", "title", "context", "metadata"):
        if key not in event:
            return False
    if not isinstance(event["metadata"], dict):
        return False
    if not event["metadata"].get("source_platform"):
        return False
    return isinstance(event["context"], str) and bool(event["context"])


def test_bilibili_history_event_has_unified_shape() -> None:
    """v0.3.22+: B站 history events must carry context + source_platform
    just like 小红书 events did from day one."""
    item = {
        "history": {"bvid": "BV1A", "view_at": 1710000000},
        "title": "讲透历史叙事",
        "author_name": "历史实验室",
    }
    event = _history_item_to_event(item)
    assert _has_unified_shape(event)
    assert event["metadata"]["source_platform"] == SOURCE_BILIBILI
    assert event["event_type"] == "view"
    assert "历史实验室" in event["context"]
    assert "讲透历史叙事" in event["context"]
    assert event["url"].endswith("/BV1A")
    # Author canonical-name field is consistent with xhs events
    assert event["metadata"]["author"] == "历史实验室"


def test_xiaohongshu_bootstrap_events_have_unified_shape() -> None:
    notes = [
        {
            "scope": "saved",
            "title": "手冲咖啡入门",
            "url": "https://www.xiaohongshu.com/explore/abc",
            "author": "豆子老师",
        },
        {
            "scope": "liked",
            "title": "意式拉花教程",
            "url": "https://www.xiaohongshu.com/explore/def",
            "author": "拿铁猫",
        },
    ]
    events = xhs_bootstrap_notes_to_events(notes)
    assert len(events) == 2
    for event in events:
        assert _has_unified_shape(event)
        assert event["metadata"]["source_platform"] == SOURCE_XIAOHONGSHU
        assert "小红书" in event["context"]
        assert event["metadata"]["author"]
    # The two scopes map to distinct event_types
    assert {e["event_type"] for e in events} == {"favorite", "like"}
    # Scope-specific natural-language label is preserved
    assert "收藏" in events[0]["context"]
    assert "点赞" in events[1]["context"]


def test_bilibili_and_xiaohongshu_events_share_consumer_contract() -> None:
    """A consumer reading {event_type, title, context, metadata.source_platform,
    metadata.author} should not need to special-case which source produced the
    event. This is the core unification invariant."""
    bili = _history_item_to_event(
        {
            "history": {"bvid": "BV1", "view_at": 1},
            "title": "B站标题",
            "author_name": "B站作者",
        }
    )
    xhs = xhs_bootstrap_notes_to_events(
        [{"scope": "saved", "title": "小红书标题", "author": "小红书作者"}]
    )[0]

    consumer_view_keys = {"event_type", "title", "context"}
    consumer_metadata_keys = {"source_platform", "author"}

    for event in (bili, xhs):
        assert consumer_view_keys.issubset(event.keys())
        assert consumer_metadata_keys.issubset(event["metadata"].keys())


def test_extension_ingested_events_normalise_dict_context() -> None:
    """Pre-v0.3.22 the /api/events ingest endpoint passed item.context
    through verbatim, so dict-shaped context (extension-collected click
    metadata) ended up in the database as a JSON blob and corrupted
    LLM prompts. v0.3.22+ coerces non-string context to "" and folds
    the original payload into metadata.raw_context for diagnostics.

    This test covers the coercion logic by replicating what
    ``ingest_events`` in api/app.py does (importing build_event from
    sources.event_format) — keeps the contract pinned even if the
    endpoint is later refactored.
    """
    raw_context_dict = {"video_id": "BV1", "ts": 12345}
    metadata: dict = {"timestamp": 1700000000}
    if not isinstance(raw_context_dict, str) and raw_context_dict:
        metadata.setdefault("raw_context", raw_context_dict)
    event = build_event(
        event_type="click",
        source_platform=SOURCE_BILIBILI,
        title="一个视频",
        url="https://www.bilibili.com/video/BV1",
        author="某 UP",
        context="",  # coerced from dict
        metadata=metadata,
    )
    # Context is now a non-empty natural-language string
    assert isinstance(event["context"], str)
    assert event["context"]
    assert "B 站" in event["context"]
    # Original dict payload preserved for diagnostics
    assert event["metadata"]["raw_context"] == raw_context_dict


def test_event_db_round_trip_preserves_context_string_verbatim(tmp_path) -> None:
    """v0.3.23+: regression for the JSON-double-encoding bug uncovered
    after v0.3.22 unification.

    Pre-v0.3.23 the database layer unconditionally json.dumps()'d the
    context column. With the new string contract from build_event(),
    a context like ``"在 B 站看了《讲透历史叙事》,作者:历史实验室"``
    became ``'"在 B 站看了《讲透历史叙事》,作者:历史实验室"'`` in the
    DB (literal outer quotes). When that round-tripped back through
    json.dumps to the LLM prompt it became triple-escaped — visible
    noise the model had to ignore.

    This test pins the round-trip: build an event, insert via
    propagate_event, query back, assert the context column value is
    byte-identical to what build_event emitted.
    """
    import asyncio

    from openbiliclaw.memory.manager import MemoryManager
    from openbiliclaw.sources.event_format import (
        SOURCE_BILIBILI,
        SOURCE_XIAOHONGSHU,
        build_event,
    )
    from openbiliclaw.storage.database import Database

    db_path = tmp_path / "events.db"
    db = Database(db_path)
    db.initialize()
    manager = MemoryManager(data_dir=tmp_path, database=db)

    bili_event = build_event(
        event_type="favorite",
        source_platform=SOURCE_BILIBILI,
        title="讲透历史叙事",
        url="https://www.bilibili.com/video/BV1A",
        author="历史实验室",
        metadata={"bvid": "BV1A"},
    )
    xhs_event = build_event(
        event_type="like",
        source_platform=SOURCE_XIAOHONGSHU,
        title="手冲咖啡入门",
        url="https://www.xiaohongshu.com/explore/abc",
        author="豆子老师",
        context="小红书点赞:手冲咖啡入门 作者:豆子老师",
    )
    asyncio.run(manager.propagate_event(bili_event))
    asyncio.run(manager.propagate_event(xhs_event))

    rows = db.get_recent_events(limit=10)
    assert len(rows) == 2
    by_title = {row["title"]: row for row in rows}

    # Critical: context column is the raw natural-language string,
    # NOT a JSON-quoted version of it. This is what fixes the
    # triple-encoding noise in LLM prompts.
    assert by_title["讲透历史叙事"]["context"] == bili_event["context"]
    assert by_title["手冲咖啡入门"]["context"] == xhs_event["context"]
    # Sanity: no leading literal quote (was the bug signature)
    assert not by_title["讲透历史叙事"]["context"].startswith('"')
    assert not by_title["手冲咖啡入门"]["context"].startswith('"')


def test_event_db_round_trip_legacy_dict_context_still_works(tmp_path) -> None:
    """Backward compat: pre-v0.3.22 callers occasionally passed
    dict-shaped context (extension click events with structured
    payload). insert_event must still accept those — JSON-encoding
    them on storage so the data isn't lost. Consumers reading the
    column see a JSON-string they can json.loads if needed.
    """
    from openbiliclaw.storage.database import Database

    db_path = tmp_path / "events.db"
    db = Database(db_path)
    db.initialize()

    legacy_dict_context = {"video_id": "BV1", "ts": 12345}
    db.insert_event(
        "click",
        title="legacy click",
        context=legacy_dict_context,
        metadata={"source_platform": "bilibili"},
    )
    rows = db.get_recent_events(limit=5)
    assert rows
    stored = rows[0]["context"]
    # Stored as JSON-encoded string (not the raw dict, since SQLite
    # column is TEXT). Consumer can json.loads to recover the dict.
    import json

    decoded = json.loads(stored)
    assert decoded == legacy_dict_context


def test_feedback_event_uses_natural_language_context() -> None:
    """v0.3.22+: /api/feedback now builds a custom context with the
    feedback verb (点赞/踩/评论) instead of leaving context empty.
    Replicates the api/app.py logic so the contract stays pinned."""
    feedback_label = {"like": "点赞了", "dislike": "踩了", "comment": "评论了"}["dislike"]
    rec_title = "某个视频"
    note = "封面太花哨"
    feedback_context = f"在 B 站{feedback_label}《{rec_title}》"
    if note:
        feedback_context = f"{feedback_context},备注:{note}"

    event = build_event(
        event_type="feedback",
        source_platform=SOURCE_BILIBILI,
        title=rec_title,
        context=feedback_context,
        metadata={
            "recommendation_id": "rec-1",
            "bvid": "BV1",
            "feedback_type": "dislike",
            "feedback_note": note,
        },
    )
    assert "踩了" in event["context"]
    assert "封面太花哨" in event["context"]
    assert event["metadata"]["feedback_type"] == "dislike"
    assert event["metadata"]["source_platform"] == SOURCE_BILIBILI


def test_default_signal_strength_search_is_explicit_intent() -> None:
    """Search is weighted 0.5 (explicit-intent), above passive view (0.35)."""
    assert default_signal_strength_for_event("search", {}) == 0.5
    assert default_signal_strength_for_event("view", {}) == 0.35
    assert default_signal_strength_for_event("favorite", {}) == 1.0
    assert default_signal_strength_for_event("reshuffle", {}) == 0.1


def test_default_signal_strength_retraction_is_low() -> None:
    """A retraction is weak evidence — 0.2 — even when metadata was stripped
    of the extension-supplied signal_strength (server-built / re-ingested)."""
    assert default_signal_strength_for_event("feedback", {"feedback_type": "retraction"}) == 0.2
    # Plain feedback default is unchanged.
    assert default_signal_strength_for_event("feedback", {"feedback_type": "dislike"}) == 1.0
    assert default_signal_strength_for_event("feedback", {}) == 0.5


def test_build_event_retraction_keeps_low_signal_strength() -> None:
    event = build_event(
        event_type="feedback",
        source_platform="twitter",
        title="a withdrawn like",
        metadata={"feedback_type": "retraction", "retracted_action": "like"},
    )
    assert event["metadata"]["signal_strength"] == 0.2


def test_twitter_label_and_context_render() -> None:
    """X (twitter) source renders with the 'X' platform label."""
    rendered = format_event_context(
        event_type="favorite",
        source_platform="twitter",
        title="A thread on systems",
        author="@handle",
    )
    assert rendered.startswith("在X")  # _PLATFORM_LABELS["twitter"] == "X"
    assert "《A thread on systems》" in rendered


def test_bangumi_label_and_context_render() -> None:
    rendered = format_event_context(
        event_type="favorite",
        source_platform="bangumi",
        title="Cowboy Bebop",
    )

    assert rendered.startswith("在Bangumi")
    assert "《Cowboy Bebop》" in rendered


# ---------------------------------------------------------------------------
# Comment-text capture (event-capture-completion Phase 2/3)


def test_sanitize_comment_text_truncates_to_200_chars() -> None:
    assert COMMENT_TEXT_MAX_CHARS == 200
    long = "字" * 250
    cleaned = sanitize_comment_text(long)
    assert len(cleaned) == 200
    assert cleaned == "字" * 200


def test_sanitize_comment_text_strips_unicode_category_c() -> None:
    # Control chars (Cc: \n \t \x00), format chars (Cf: zero-width space,
    # left-to-right mark) are category-C and must be stripped; ordinary
    # whitespace (Zs) at the ends is trimmed but interior spaces survive.
    dirty = "hello\n\tworld\u200b \u200e ok\x00"
    cleaned = sanitize_comment_text(dirty)
    assert cleaned == "helloworld  ok"
    assert "\n" not in cleaned
    assert "\u200b" not in cleaned


def test_sanitize_comment_text_non_string_returns_empty() -> None:
    assert sanitize_comment_text(None) == ""
    assert sanitize_comment_text(123) == ""
    assert sanitize_comment_text("") == ""


def test_normalize_comment_kind_whitelist() -> None:
    assert normalize_comment_kind("comment") == "comment"
    assert normalize_comment_kind("danmaku") == "danmaku"
    assert normalize_comment_kind("") == ""
    # Out-of-range values are treated as missing ("").
    assert normalize_comment_kind("bullet") == ""
    assert normalize_comment_kind("DANMAKU") == "danmaku"
    assert normalize_comment_kind(None) == ""


def test_default_signal_strength_danmaku_is_0_6() -> None:
    # Danmaku is a comment sub-kind weighted below a written comment (0.75)
    # because bullet chatter is more casual; ties with follow (0.6).
    assert default_signal_strength_for_event("comment", {"comment_kind": "danmaku"}) == 0.6
    # A plain comment stays 0.75.
    assert default_signal_strength_for_event("comment", {"comment_kind": "comment"}) == 0.75
    assert default_signal_strength_for_event("comment", {}) == 0.75


def test_build_event_comment_renders_excerpt_and_sanitizes() -> None:
    event = build_event(
        event_type="comment",
        source_platform="bilibili",
        title="一个视频",
        metadata={"comment_kind": "danmaku", "comment_text": "太强了\n666" + "x" * 300},
    )
    # Server is the final sanitization defense: category-C stripped + truncated.
    assert "\n" not in event["metadata"]["comment_text"]
    assert len(event["metadata"]["comment_text"]) == 200
    # Danmaku strength applied via the normalized comment_kind.
    assert event["metadata"]["signal_strength"] == 0.6
    # The excerpt is rendered into the human-readable context.
    assert "评论:『" in event["context"]


def test_build_event_comment_kind_out_of_range_cleared() -> None:
    event = build_event(
        event_type="comment",
        source_platform="bilibili",
        title="一个视频",
        metadata={"comment_kind": "bullet", "comment_text": "hi"},
    )
    assert event["metadata"]["comment_kind"] == ""
