"""Unified cross-source event format for soul-pipeline consumption.

Every source adapter — Bilibili, Xiaohongshu, generic Web, future
platforms — emits events through ``build_event()``. The resulting
dict has a stable shape so downstream consumers (preference analyzer,
awareness analyzer, profile builder, memory layer) see one unified
contract regardless of where the signal came from.

Why this exists
---------------

Pre-v0.3.22 each producer hand-built its own event dict inline:
- B站 history → ``{event_type, title, url, metadata: {bvid, author}}``
- B站 收藏    → ``{event_type, title, metadata: {folder, upper}}``
- B站 关注    → ``{event_type, title, metadata: {up_name, sign}}``
- 小红书      → ``{event_type, title, url, context, metadata: {source_platform, ...}}``

Three problems:

1. Only Xiaohongshu populated the natural-language ``context`` field.
   Everything else dropped into the LLM prompt as a raw JSON blob, so
   the analyzer couldn't form a single readable description without
   schema-aware logic.
2. ``source_platform`` was only present on Xiaohongshu events;
   ``compute_source_platform_mix`` had to assume "missing = bilibili"
   which won't generalize to future sources.
3. Author / creator naming was scattered: ``author`` / ``up_name`` /
   ``upper`` / ``author_name`` — every consumer had to fall through a
   list.

The unified contract
--------------------

```python
{
    "event_type": str,         # "view" | "favorite" | "like" | "follow" | "dislike" | ...
    "title": str,
    "url": str,                 # optional, may be empty
    "context": str,             # natural-language sentence; primary input for LLM
    "metadata": {
        "source_platform": str,  # "bilibili" | "xiaohongshu" | "web" | ...
        "author": str,           # canonical creator/author name; empty when not applicable
        ...                      # source-specific extras (bvid / note_id / folder / ...)
    },
}
```

The ``context`` string is what matters for LLM prompts. It reads like
a Chinese sentence: who did what, on which platform, with which content,
optionally noting the author. Code that filters / weights events should
look at structured fields (``event_type`` / ``metadata.source_platform``);
the LLM consumes ``context``.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

logger = logging.getLogger(__name__)

SatisfactionCategory = Literal["positive", "neutral", "negative", "unknown"]

# Dwell thresholds for satisfaction inference on click events.
#
# - meaningful_dwell: at least 15s AND at least 30% of the video duration.
#   Below either bound the watch was probably exploratory, not engaged.
# - quick_exit: under 5s. Almost always a clickbait-baited tab close.
#
# Tuned conservatively: the goal is to feed the preference layer only
# the events we are highly confident reflect real interest, while still
# letting genuinely short clips count if the user watched the bulk of them.
_MEANINGFUL_DWELL_MIN_SECONDS = 15
_MEANINGFUL_DWELL_MIN_RATIO = 0.3
_QUICK_EXIT_MAX_SECONDS = 5

# Explicit engagement event types (no dwell needed to read intent).
_EXPLICIT_POSITIVE_EVENT_TYPES = frozenset({"like", "coin", "favorite", "comment"})

# Feedback metadata vocabulary — set on `feedback` events emitted by the
# extension's "👍 / 👎" UI and the recommendation feedback endpoint.
_POSITIVE_FEEDBACK_TYPES = frozenset({"like", "comment"})
_POSITIVE_REACTIONS = frozenset({"thumbs_up"})
_NEGATIVE_FEEDBACK_TYPES = frozenset({"dislike"})
_NEGATIVE_REACTIONS = frozenset({"thumbs_down"})

# Events that record passive browse — useful for context but never a
# direct signal of like / dislike.
_PASSIVE_BROWSE_EVENT_TYPES = frozenset({"snapshot", "scroll", "hover", "search"})


def classify_event_satisfaction(event: dict[str, Any]) -> tuple[SatisfactionCategory, str]:
    """Return ``(category, reason)`` describing whether the user enjoyed this event.

    Pure, deterministic, audit-friendly. Never raises — a malformed
    payload returns ``("unknown", "fallback")`` so the persistence path
    can always store *something* without a classification step crashing
    the request.

    The reason string is a short stable identifier (snake_case) suitable
    for storage and observability dashboards; see the design doc for the
    full list of values.
    """
    try:
        event_type = str(event.get("event_type") or event.get("type") or "").strip()
        metadata_raw = event.get("metadata")
    except (TypeError, AttributeError):
        logger.debug("classify_event_satisfaction: malformed event payload", exc_info=True)
        return ("unknown", "fallback")

    # A non-None, non-dict metadata is a contract violation (the rest of
    # the pipeline assumes dict-shaped metadata). Treat it as unreadable
    # rather than silently coercing to {} and emitting `missing_dwell`,
    # which would suggest the payload was well-formed but lacked dwell.
    if metadata_raw is None:
        metadata: dict[str, Any] = {}
    elif isinstance(metadata_raw, dict):
        metadata = metadata_raw
    else:
        logger.debug(
            "classify_event_satisfaction: metadata is %s (not dict); returning fallback",
            type(metadata_raw).__name__,
        )
        return ("unknown", "fallback")

    if event_type in _EXPLICIT_POSITIVE_EVENT_TYPES:
        return ("positive", "explicit_engagement")

    if event_type == "feedback":
        feedback_type = str(metadata.get("feedback_type") or "").strip().lower()
        reaction = str(metadata.get("reaction") or "").strip().lower()
        if feedback_type in _NEGATIVE_FEEDBACK_TYPES or reaction in _NEGATIVE_REACTIONS:
            return ("negative", "explicit_negative")
        if feedback_type in _POSITIVE_FEEDBACK_TYPES or reaction in _POSITIVE_REACTIONS:
            return ("positive", "explicit_engagement")
        return ("unknown", "fallback")

    if event_type == "click":
        return _classify_click_dwell(event, metadata)

    if event_type in _PASSIVE_BROWSE_EVENT_TYPES:
        return ("neutral", "passive_browse")

    return ("unknown", "fallback")


def _classify_click_dwell(
    event: dict[str, Any],
    metadata: dict[str, Any],
) -> tuple[SatisfactionCategory, str]:
    """Inner helper for click events — split out so the main rule table reads cleanly."""
    watch_seconds = _read_dwell_field(event, metadata, "watch_seconds")
    if watch_seconds is None:
        return ("unknown", "missing_dwell")

    if watch_seconds < _QUICK_EXIT_MAX_SECONDS:
        return ("negative", "quick_exit")

    duration = _read_dwell_field(event, metadata, "video_duration_seconds")
    if duration is None:
        # Legacy extension events use the `duration` key instead.
        duration = _read_dwell_field(event, metadata, "duration")

    meets_seconds = watch_seconds >= _MEANINGFUL_DWELL_MIN_SECONDS
    meets_ratio = duration is not None and duration > 0 and (
        watch_seconds / duration >= _MEANINGFUL_DWELL_MIN_RATIO
    )

    if meets_seconds and (duration is None or meets_ratio):
        return ("positive", "meaningful_dwell")

    return ("neutral", "shallow_view")


def _read_dwell_field(
    event: dict[str, Any],
    metadata: dict[str, Any],
    key: str,
) -> float | None:
    """Read a numeric field from either the top-level event or its metadata.

    Returns ``None`` if the field is absent or the stored value cannot
    be coerced to a float (e.g. ``"unknown"`` strings from older payloads).
    """
    raw = event.get(key)
    if raw is None:
        raw = metadata.get(key)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None

# Source platform constants — kept stable for analyzer mix calculations.
SOURCE_BILIBILI = "bilibili"
SOURCE_XIAOHONGSHU = "xiaohongshu"
SOURCE_DOUYIN = "douyin"
SOURCE_WEB = "web"
SOURCE_YOUTUBE = "youtube"
SOURCE_TWITTER = "twitter"

# Human-readable platform labels used to render the context string.
# Keys must match the source_platform values stored in event metadata.
_PLATFORM_LABELS: dict[str, str] = {
    SOURCE_BILIBILI: "B 站",
    SOURCE_XIAOHONGSHU: "小红书",
    SOURCE_DOUYIN: "抖音",
    SOURCE_WEB: "网页",
    SOURCE_YOUTUBE: "YouTube",
    SOURCE_TWITTER: "X",
}

# Action verbs per event_type. Designed so the rendered sentence reads
# naturally as "在<platform>上<verb>了《<title>》" — Chinese doesn't need
# articles, so this stays compact.
_EVENT_TYPE_LABELS: dict[str, str] = {
    "view": "看了",
    "favorite": "收藏了",
    "like": "点赞了",
    "follow": "关注了",
    "dislike": "标记不喜欢",
    "click": "点开了",
    "dialogue": "聊到",
    "feedback": "反馈过",
    "comment": "评论过",
    "share": "分享了",
}


def format_event_context(
    *,
    event_type: str,
    source_platform: str,
    title: str,
    author: str = "",
    extra: str = "",
) -> str:
    """Render a single-sentence Chinese description of an event.

    Examples
    --------
    >>> format_event_context(
    ...     event_type="favorite",
    ...     source_platform="bilibili",
    ...     title="讲透历史叙事",
    ...     author="历史实验室",
    ... )
    '在 B 站收藏了《讲透历史叙事》,作者:历史实验室'

    >>> format_event_context(
    ...     event_type="like",
    ...     source_platform="xiaohongshu",
    ...     title="手冲咖啡入门",
    ...     author="豆子老师",
    ... )
    '在小红书点赞了《手冲咖啡入门》,作者:豆子老师'

    >>> format_event_context(
    ...     event_type="follow",
    ...     source_platform="bilibili",
    ...     title="历史实验室",
    ...     extra="签名:专注于讲透中国近代史",
    ... )
    '在 B 站关注了《历史实验室》(签名:专注于讲透中国近代史)'

    The output is intentionally terse — LLM prompts pack many of these
    end-to-end, so verbose phrasing wastes context window.
    """
    platform_label = _PLATFORM_LABELS.get(source_platform, source_platform or "")
    action_label = _EVENT_TYPE_LABELS.get(event_type, "记录了")

    title = (title or "").strip()
    author = (author or "").strip()
    extra = (extra or "").strip()

    parts: list[str] = []
    if platform_label:
        parts.append(f"在{platform_label}")
    parts.append(action_label)
    parts.append(f"《{title}》" if title else "一条内容")
    if author:
        parts.append(f",作者:{author}")
    if extra:
        parts.append(f"({extra})")
    return "".join(parts).strip()


def build_event(
    *,
    event_type: str,
    source_platform: str,
    title: str = "",
    url: str = "",
    author: str = "",
    context: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Construct a unified event dict.

    Parameters
    ----------
    event_type
        Canonical action type. See ``_EVENT_TYPE_LABELS`` for the
        recognised set; unknown values fall through to the literal
        string in the rendered context.
    source_platform
        One of the ``SOURCE_*`` constants. Tagged into ``metadata``
        so analyzers' source-mix code can find it.
    title
        Content title (video / note / page name). Used in both the
        structured field and the natural-language context.
    url
        Optional canonical URL. Stored at top level so memory-layer
        dedup logic can match across events without having to look
        into metadata.
    author
        Canonical creator name. Stored in ``metadata.author``;
        producers should pass it here regardless of platform-native
        naming (``up_name`` / ``upper`` / ``nickname``) to keep the
        consumer side schema-free.
    context
        Pre-formatted natural-language sentence. If empty,
        ``format_event_context`` builds one from the structured fields.
        Producers that have richer context (e.g. xhs scope, B站 fold
        membership) can override.
    metadata
        Source-specific extras. ``source_platform`` is auto-populated
        from the parameter; explicit ``metadata.source_platform`` wins.
        ``author`` is also synced when not already present.

    Returns
    -------
    dict
        The unified event ready for ``MemoryManager.propagate_event``,
        ``SoulEngine.analyze_events``, etc.
    """
    final_metadata: dict[str, Any] = dict(metadata) if metadata else {}
    final_metadata.setdefault("source_platform", source_platform)
    if author and "author" not in final_metadata:
        final_metadata["author"] = author

    # Reuse the author from metadata if the caller didn't pass one
    # explicitly — handles producers that set author only inside metadata.
    effective_author = author or str(final_metadata.get("author", "") or "")

    if not context:
        context = format_event_context(
            event_type=event_type,
            source_platform=source_platform,
            title=title,
            author=effective_author,
        )

    event: dict[str, Any] = {
        "event_type": event_type,
        "title": title,
        "context": context,
        "metadata": final_metadata,
    }
    if url:
        event["url"] = url
    return event
