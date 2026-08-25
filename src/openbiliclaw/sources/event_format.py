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

import json
import logging
import unicodedata
from datetime import UTC, datetime
from typing import Any, Literal

logger = logging.getLogger(__name__)

# --- Comment / danmaku text capture (event-capture-completion Phase 2/3) -----
#
# Users' own comment / danmaku text is the strongest first-person interest
# expression we capture. Both surfaces sanitize independently (invariant 5,
# two-layer defense): the extension truncates + strips control chars before
# send, and the server repeats it here as the authoritative final defense.
#
# Calibration: 200 chars keeps a full danmaku / short comment intact while
# capping a pasted-essay reply so a single event can't blow the prompt budget.
# Reopen on any provider/model swap (pitfall #3).
COMMENT_TEXT_MAX_CHARS = 200

# ``comment_kind`` whitelist (invariant 4). Out-of-range values are treated as
# missing ("") + logged at WARNING, matching the retracted_action guard.
VALID_COMMENT_KINDS = frozenset({"", "comment", "danmaku"})

# Evidence strength for a danmaku. Calibration: below a written comment (0.75)
# because bullet chatter is more casual / reactive, ties with follow (0.6).
# Reopen on any provider/model swap (pitfall #3).
DANMAKU_SIGNAL_STRENGTH = 0.6


def _strip_unicode_category_c(text: str) -> str:
    """Drop every Unicode category-C code point (control / format / surrogate /
    private-use / unassigned) — zero-width joiners, bidi marks, NUL, newlines.
    Ordinary whitespace (category Z) is preserved so interior spaces survive."""
    return "".join(ch for ch in text if not unicodedata.category(ch).startswith("C"))


def sanitize_comment_text(text: Any) -> str:
    """Truncate to ``COMMENT_TEXT_MAX_CHARS`` and strip Unicode category-C.

    The authoritative server-side half of the two-layer defense (invariant 5):
    never trusts the extension's pre-sanitization. Non-string input → "".
    """
    if not isinstance(text, str) or not text:
        return ""
    cleaned = _strip_unicode_category_c(text).strip()
    return cleaned[:COMMENT_TEXT_MAX_CHARS]


def normalize_comment_kind(kind: Any) -> str:
    """Return a whitelisted ``comment_kind`` ("" | "comment" | "danmaku").

    Out-of-range / non-string values are treated as missing ("") and logged at
    WARNING (invariant 4 — enum whitelist with coercion logging, pitfall #4).
    """
    if not isinstance(kind, str):
        return ""
    normalized = kind.strip().lower()
    if normalized in VALID_COMMENT_KINDS:
        return normalized
    logger.warning("normalize_comment_kind: out-of-range comment_kind %r → ''", kind)
    return ""


# --- Retraction discounting (event-capture-completion Phase 0) --------------
#
# A retraction (an unlike / unbookmark / unfollow / undo-retweet) neutralizes a
# prior positive event. The positive evidence is *discounted, never deleted*
# (invariant 3): its metadata is marked ``retracted`` and its evidence strength
# capped. Whitelisted actions map 1:1 to positive event_types.
RETRACTABLE_ACTIONS = frozenset({"like", "favorite", "share", "follow"})

# Post-retraction evidence strength. Calibration: mirrors the extension's
# explicit retraction ``signal_strength`` (see the feedback/retraction branch in
# ``default_signal_strength_for_event`` — both 0.2) so a retracted positive is
# downgraded to the same weak-evidence floor as the retraction signal itself.
# Reopen calibration on any provider/model swap (pitfall #3).
RETRACTION_DISCOUNTED_STRENGTH = 0.2

# Natural-language marker appended to a retracted event's rendered context so
# every reread LLM consumption face (preference / awareness) sees the undo, not
# just the structured ``retracted`` flag. Half-width parens match the existing
# ``format_event_context`` extra style.
RETRACTION_RENDER_MARKER = "(已撤销)"


def apply_retraction_discount(metadata: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``metadata`` marked retracted with capped strength.

    Idempotent: re-applying keeps ``signal_strength`` at the min (never
    re-inflates). A missing / unparseable strength is set to the floor.
    """
    result = dict(metadata)
    result["retracted"] = True
    raw = result.get("signal_strength")
    try:
        current = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        current = None
    result["signal_strength"] = (
        RETRACTION_DISCOUNTED_STRENGTH
        if current is None
        else min(current, RETRACTION_DISCOUNTED_STRENGTH)
    )
    return result


# A behaviour attributed to a confusion that resolved as proxy / misread is
# discounted the same way a retraction is — its evidence should no longer drive
# preference weight. Calibrated to the retraction floor (0.2) for symmetry;
# revisit if the retraction floor moves (pitfall #3).
CONFUSION_DISCOUNTED_STRENGTH = RETRACTION_DISCOUNTED_STRENGTH


def apply_confusion_discount(metadata: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``metadata`` flagged discounted-by-confusion.

    Mirrors :func:`apply_retraction_discount`: idempotent, never re-inflates
    ``signal_strength``. Marks ``discounted_by_confusion=true`` (distinct from
    the retraction flag so the two provenances stay auditable).
    """
    result = dict(metadata)
    result["discounted_by_confusion"] = True
    raw = result.get("signal_strength")
    try:
        current = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        current = None
    result["signal_strength"] = (
        CONFUSION_DISCOUNTED_STRENGTH
        if current is None
        else min(current, CONFUSION_DISCOUNTED_STRENGTH)
    )
    return result


def _metadata_is_retracted(metadata: Any) -> bool:
    """True when the event's metadata carries a truthy ``retracted`` flag.

    Handles both dict metadata (in-memory events) and the raw JSON-string
    metadata returned by ``Database.query_events`` for reread paths.
    """
    if isinstance(metadata, dict):
        return bool(metadata.get("retracted"))
    if isinstance(metadata, str) and metadata:
        try:
            parsed = json.loads(metadata)
        except (ValueError, TypeError):
            return False
        return isinstance(parsed, dict) and bool(parsed.get("retracted"))
    return False


def render_retraction_marked_events(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Append ``(已撤销)`` to the context of retracted events for rendering.

    Non-retracted events are returned as-is (identity), so rendering of an
    event set without any retraction is byte-for-byte unchanged (invariant 2).
    """
    marked: list[dict[str, Any]] = []
    for event in events:
        if not _metadata_is_retracted(event.get("metadata")):
            marked.append(event)
            continue
        context = str(event.get("context") or "")
        if RETRACTION_RENDER_MARKER in context:
            marked.append(event)
            continue
        copy = dict(event)
        copy["context"] = (
            context + RETRACTION_RENDER_MARKER if context else RETRACTION_RENDER_MARKER
        )
        marked.append(copy)
    return marked


def parse_event_timestamp(metadata: dict[str, Any] | None) -> datetime | None:
    """Extract a timezone-aware UTC event time from ``metadata.timestamp``.

    The extension folds ``item.timestamp`` (epoch milliseconds) into
    ``metadata.timestamp`` at ingest; account_sync / server events may carry an
    ISO string. Returns ``None`` when no usable timestamp is present so callers
    can conservatively skip causality-dependent decisions (Phase 0 rule: time
    unavailable → do not discount).
    """
    if not isinstance(metadata, dict):
        return None
    raw = metadata.get("timestamp")
    if isinstance(raw, bool):  # bool is an int subclass — never a timestamp
        return None
    if isinstance(raw, int | float):
        return _epoch_to_datetime(float(raw))
    if isinstance(raw, str) and raw.strip():
        text = raw.strip()
        try:
            return _epoch_to_datetime(float(text))
        except ValueError:
            pass
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _epoch_to_datetime(value: float) -> datetime:
    """Convert an epoch value to UTC, treating large magnitudes as milliseconds."""
    # Values above ~1e11 are milliseconds (year 5138 in seconds), so any real
    # ms epoch (~1.7e12 today) divides cleanly while second epochs pass through.
    seconds = value / 1000.0 if abs(value) >= 1e11 else value
    return datetime.fromtimestamp(seconds, UTC)


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

# Content pages (xhs note / zhihu answer / reddit post / X status) carry no
# video duration, so the ratio rule can't apply. A duration-less
# `content_page_exit` dwell is scored on visible reading time alone: >= 30s is
# engaged reading (positive); < 5s reuses the quick-exit negative; between is
# neutral.
_CONTENT_DWELL_POSITIVE_MIN_SECONDS = 30

# `view` rows (Bilibili history, account sync) report where the user stopped, so
# completion is real evidence of interest — but a *low* completion is not
# evidence of dislike: autoplay, misclicks, trailers and rewatches with reset
# progress all look identical to a bounce. So this rule is positive-only; a
# short watch stays `unknown` rather than becoming a negative exemplar.
#
# Calibrated 2026-07-27 on a real 500-row history (461 rows carried completion):
# median completion 0.071, 32% under five seconds. >= 0.8 marks 7% (30 rows) as
# deliberate finishes; 0.7 would mark 11% and 0.5 fully 19%, which is too loose
# against that median. 0.8 also matches ProfileBuilder._history_weight's
# existing "finished" bound, so the codebase keeps one definition of 看完了.
# Recalibrate if the source of watch_seconds changes.
_FINISHED_WATCH_MIN_RATIO = 0.8

# Explicit engagement event types (no dwell needed to read intent).
_EXPLICIT_POSITIVE_EVENT_TYPES = frozenset({"like", "coin", "favorite", "comment"})

# Feedback metadata vocabulary — set on `feedback` events emitted by the
# extension's "👍 / 👎" UI and the recommendation feedback endpoint.
_POSITIVE_FEEDBACK_TYPES = frozenset({"like"})
_NEUTRAL_FEEDBACK_TYPES = frozenset({"comment"})
_POSITIVE_REACTIONS = frozenset({"thumbs_up"})
_NEGATIVE_FEEDBACK_TYPES = frozenset({"dislike"})
_NEGATIVE_REACTIONS = frozenset({"thumbs_down"})

# Events that record passive browse — useful for context but never a
# direct signal of like / dislike.
_PASSIVE_BROWSE_EVENT_TYPES = frozenset({"snapshot", "scroll", "hover", "search", "reshuffle"})


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
        # Retraction (an unlike / unbookmark) is a neutralization, never a
        # negative preference — checked BEFORE any feedback-negative rule so an
        # incidental negative reaction can't flip it (invariant: retraction is
        # neutral).
        if feedback_type == "retraction":
            return ("neutral", "retraction")
        if feedback_type in _NEGATIVE_FEEDBACK_TYPES or reaction in _NEGATIVE_REACTIONS:
            return ("negative", "explicit_negative")
        if feedback_type in _POSITIVE_FEEDBACK_TYPES or reaction in _POSITIVE_REACTIONS:
            return ("positive", "explicit_engagement")
        if feedback_type in _NEUTRAL_FEEDBACK_TYPES:
            return ("neutral", "direct_feedback")
        return ("unknown", "fallback")

    if event_type == "click":
        return _classify_click_dwell(event, metadata)

    if event_type == "view":
        return _classify_view_completion(event, metadata)

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

    # Content-page dwell has no video duration — score on reading time alone.
    dwell_source = str(metadata.get("dwell_source") or "").strip()
    if dwell_source == "content_page_exit":
        if watch_seconds >= _CONTENT_DWELL_POSITIVE_MIN_SECONDS:
            return ("positive", "engaged_reading")
        return ("neutral", "shallow_view")

    duration = _read_dwell_field(event, metadata, "video_duration_seconds")
    if duration is None:
        # Legacy extension events use the `duration` key instead.
        duration = _read_dwell_field(event, metadata, "duration")

    meets_seconds = watch_seconds >= _MEANINGFUL_DWELL_MIN_SECONDS
    meets_ratio = (
        duration is not None
        and duration > 0
        and (watch_seconds / duration >= _MEANINGFUL_DWELL_MIN_RATIO)
    )

    if meets_seconds and (duration is None or meets_ratio):
        return ("positive", "meaningful_dwell")

    return ("neutral", "shallow_view")


def _classify_view_completion(
    event: dict[str, Any],
    metadata: dict[str, Any],
) -> tuple[SatisfactionCategory, str]:
    """Positive-only rule for history views: finishing something is evidence.

    Only an actual finish upgrades the row. Everything else — no completion
    data, a short watch, an unfinished one — stays ``unknown``, exactly as it
    was before this rule existed. See ``_FINISHED_WATCH_MIN_RATIO`` for why a
    low completion is deliberately not treated as a negative signal.
    """
    watch_seconds = _read_dwell_field(event, metadata, "watch_seconds")
    if watch_seconds is None:
        # Raw Bilibili history rows call it ``progress``.
        watch_seconds = _read_dwell_field(event, metadata, "progress")
    duration = _read_dwell_field(event, metadata, "video_duration_seconds")
    if duration is None:
        duration = _read_dwell_field(event, metadata, "duration")
    if watch_seconds is None or duration is None or duration <= 0:
        return ("unknown", "fallback")
    if watch_seconds < _MEANINGFUL_DWELL_MIN_SECONDS:
        # Guards clips so short that finishing one says nothing.
        return ("unknown", "fallback")
    if watch_seconds / duration >= _FINISHED_WATCH_MIN_RATIO:
        return ("positive", "finished_watch")
    return ("unknown", "fallback")


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
SOURCE_ZHIHU = "zhihu"
SOURCE_REDDIT = "reddit"
SOURCE_BANGUMI = "bangumi"
SOURCE_LINUXDO = "linuxdo"
SOURCE_WEIBO = "weibo"
SOURCE_V2EX = "v2ex"
SOURCE_INSTAGRAM = "instagram"

# Human-readable platform labels used to render the context string.
# Keys must match the source_platform values stored in event metadata.
_PLATFORM_LABELS: dict[str, str] = {
    SOURCE_BILIBILI: "B 站",
    SOURCE_XIAOHONGSHU: "小红书",
    SOURCE_DOUYIN: "抖音",
    SOURCE_WEB: "网页",
    SOURCE_YOUTUBE: "YouTube",
    SOURCE_TWITTER: "X",
    SOURCE_ZHIHU: "知乎",
    SOURCE_REDDIT: "Reddit",
    SOURCE_BANGUMI: "Bangumi",
    SOURCE_LINUXDO: "Linux.do",
    SOURCE_WEIBO: "微博",
    SOURCE_V2EX: "V2EX",
    SOURCE_INSTAGRAM: "Instagram",
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
    "dismiss": "忽略了",
    "click": "点开了",
    "dialogue": "聊到",
    "feedback": "反馈过",
    "comment": "评论过",
    "discussion_reply": "参与讨论了",
    "publish": "发布了",
    "share": "分享了",
    "reshuffle": "换了一批",
}

_DEFAULT_SIGNAL_STRENGTH_BY_EVENT_TYPE: dict[str, float] = {
    "favorite": 1.0,
    "coin": 0.95,
    "share": 0.85,
    "like": 0.85,
    "comment": 0.75,
    "discussion_reply": 0.75,
    "publish": 0.9,
    "dialogue": 0.65,
    "follow": 0.6,
    "view": 0.35,
    "click": 0.3,
    # Search is an explicit-intent signal (the user names a topic), weighted
    # above passive view. It stays satisfaction-neutral (_PASSIVE_BROWSE_EVENT_TYPES)
    # since a query says what they want, not whether they were satisfied.
    "search": 0.5,
    "hover": 0.1,
    "scroll": 0.1,
    "snapshot": 0.1,
    # One reshuffle describes a batch-level navigation choice, not ten
    # item-level dislikes. Keep it at the same weak, satisfaction-neutral
    # evidence strength as other passive browsing actions (2026-07-21 UX
    # correction: removing the old bulk-dismiss toggle).
    "reshuffle": 0.1,
    "dislike": 1.0,
}


def default_signal_strength_for_event(
    event_type: str,
    metadata: dict[str, Any] | None = None,
) -> float | None:
    """Return a cross-source fallback evidence strength for an event.

    Platform adapters may pass a more precise ``metadata.signal_strength``.
    This fallback only fills missing values; it describes evidence strength,
    not sentiment polarity or the final interest weight.
    """
    normalized_event_type = event_type.strip().lower()
    metadata = metadata or {}

    if normalized_event_type == "feedback":
        feedback_type = str(metadata.get("feedback_type") or "").strip().lower()
        reaction = str(metadata.get("reaction") or "").strip().lower()
        # A retraction is weak evidence (0.2) — defended here for server-built
        # or metadata-stripped events where the extension's explicit 0.2 is
        # absent and the plain feedback default (0.5) would otherwise apply.
        if feedback_type == "retraction":
            return 0.2
        if feedback_type == "dislike" or reaction == "thumbs_down":
            return 1.0
        if feedback_type == "like" or reaction == "thumbs_up":
            return 1.0
        if feedback_type == "comment":
            return 0.8
        if feedback_type == "dismiss":
            return 0.5
        return 0.5

    # A danmaku is a lighter comment sub-kind (0.6 vs a written comment's 0.75);
    # defended here for metadata-stripped / server-built events (mirrors the
    # retraction branch above).
    if normalized_event_type == "comment":
        comment_kind = str(metadata.get("comment_kind") or "").strip().lower()
        if comment_kind == "danmaku":
            return DANMAKU_SIGNAL_STRENGTH

    return _DEFAULT_SIGNAL_STRENGTH_BY_EVENT_TYPE.get(normalized_event_type)


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
    # Comment / danmaku text is the final sanitization defense (invariant 5) and
    # comment_kind the enum whitelist (invariant 4). Normalize BEFORE the
    # signal_strength fallback so a danmaku's 0.6 is derived from the cleaned
    # kind.
    if "comment_kind" in final_metadata:
        final_metadata["comment_kind"] = normalize_comment_kind(final_metadata["comment_kind"])
    if "comment_text" in final_metadata:
        final_metadata["comment_text"] = sanitize_comment_text(final_metadata["comment_text"])
    if "signal_strength" not in final_metadata:
        signal_strength = default_signal_strength_for_event(event_type, final_metadata)
        if signal_strength is not None:
            final_metadata["signal_strength"] = signal_strength

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

    # Surface the user's own comment / danmaku text in the human-readable
    # context so the preference LLM reads it directly (it's the strongest
    # first-person interest signal). The excerpt is already sanitized above.
    comment_excerpt = str(final_metadata.get("comment_text") or "")
    if event_type == "comment" and comment_excerpt and "评论:『" not in context:
        context = (
            f"{context},评论:『{comment_excerpt}』" if context else f"评论:『{comment_excerpt}』"
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
