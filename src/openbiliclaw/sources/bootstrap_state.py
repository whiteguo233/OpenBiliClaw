"""Shared state helpers for extension bootstrap source deduplication."""

from __future__ import annotations

import re
from typing import Any

# Per-source cap for the durable bootstrap dedupe projection.  Periodic
# account pulls only need the newest keys because each source bootstrap scope
# is itself bounded to recent account rows.
SOURCE_SEEN_KEY_CAP = 5000
_OPAQUE_ACCOUNT_KEY_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

SOURCE_BOOTSTRAP_STATE_KEYS: dict[str, str] = {
    "xhs": "xhs_seen_note_keys",
    "xiaohongshu": "xhs_seen_note_keys",
    "dy": "dy_seen_video_keys",
    "douyin": "dy_seen_video_keys",
    "yt": "yt_seen_item_keys",
    "youtube": "yt_seen_item_keys",
    "zhihu": "zhihu_seen_item_keys",
    "zh": "zhihu_seen_item_keys",
    "reddit": "reddit_seen_item_keys",
    "rdt": "reddit_seen_item_keys",
    "linuxdo": "linuxdo_seen_item_keys",
    "v2ex": "v2ex_seen_item_keys",
    "weibo": "weibo_seen_item_keys",
    "instagram": "instagram_seen_item_keys",
    "ig": "instagram_seen_item_keys",
}


def default_source_bootstrap_state() -> dict[str, object]:
    """Return the persisted-source bootstrap dedupe state shape."""
    return {
        "xhs_seen_note_keys": [],
        "dy_seen_video_keys": [],
        "yt_seen_item_keys": [],
        "zhihu_seen_item_keys": [],
        "reddit_seen_item_keys": [],
        "linuxdo_seen_item_keys": [],
        "linuxdo_account_key": "",
        "v2ex_seen_item_keys": [],
        "weibo_seen_item_keys": [],
        "weibo_account_key": "",
        "instagram_seen_item_keys": [],
        "instagram_account_key": "",
        "last_source_bootstrap_sync_at": "",
        "source_incremental": {
            "cursor": "",
            "last_attempt_at": {},
            "last_success_at": {},
            "active_task": None,
        },
    }


def source_bootstrap_state_key(source: str) -> str:
    """Return the state-list key for a short or platform source name."""
    normalized = str(source).strip().lower()
    try:
        return SOURCE_BOOTSTRAP_STATE_KEYS[normalized]
    except KeyError as exc:
        raise ValueError(f"unknown source bootstrap state: {source}") from exc


def as_string_list(value: Any) -> list[str]:
    """Normalize a persisted list-like value into non-empty strings."""
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def merge_seen_keys(
    existing: Any,
    new_keys: Any,
    *,
    cap: int = SOURCE_SEEN_KEY_CAP,
) -> list[str]:
    """Merge seen keys while refreshing recency and bounding the result.

    Existing and incoming keys are normalized to non-empty strings.  A key
    that is seen again is moved to the newest position, and the oldest keys
    are evicted once ``cap`` is exceeded.  Non-positive custom caps retain the
    historical uncapped helper behavior; production callers use the positive
    :data:`SOURCE_SEEN_KEY_CAP` default.
    """
    merged = as_string_list(existing)
    seen = set(merged)
    for raw_key in new_keys if isinstance(new_keys, list) else []:
        key = str(raw_key).strip()
        if not key:
            continue
        if key in seen:
            merged.remove(key)
            merged.append(key)
            continue
        seen.add(key)
        merged.append(key)
    if cap > 0 and len(merged) > cap:
        merged = merged[-cap:]
    return merged


def _normalize_source_incremental_state(value: Any) -> dict[str, object]:
    """Normalize the optional scheduler-owned nested state block."""
    if not isinstance(value, dict):
        value = {}

    raw_cursor = value.get("cursor", "")
    cursor = raw_cursor.strip() if isinstance(raw_cursor, str) else ""

    raw_attempts = value.get("last_attempt_at", {})
    last_attempt_at: dict[str, str] = {}
    if isinstance(raw_attempts, dict):
        for raw_source, raw_timestamp in raw_attempts.items():
            if not isinstance(raw_source, str) or not isinstance(raw_timestamp, str):
                continue
            source = raw_source.strip().lower()
            if source:
                last_attempt_at[source] = raw_timestamp.strip()

    raw_successes = value.get("last_success_at", {})
    last_success_at: dict[str, str] = {}
    if isinstance(raw_successes, dict):
        for raw_source, raw_timestamp in raw_successes.items():
            if not isinstance(raw_source, str) or not isinstance(raw_timestamp, str):
                continue
            source = raw_source.strip().lower()
            if source:
                last_success_at[source] = raw_timestamp.strip()

    raw_active_task = value.get("active_task")
    active_task: dict[str, object] | None = None
    if isinstance(raw_active_task, dict):
        active_task = {
            str(key): item for key, item in raw_active_task.items() if isinstance(key, str)
        }

    return {
        "cursor": cursor,
        "last_attempt_at": last_attempt_at,
        "last_success_at": last_success_at,
        "active_task": active_task,
    }


def normalize_source_bootstrap_state(loaded: Any) -> dict[str, object]:
    """Coerce arbitrary JSON into the stable source-bootstrap state shape."""
    default = default_source_bootstrap_state()
    if not isinstance(loaded, dict):
        return default
    raw_instagram_account_key = loaded.get("instagram_account_key", "")
    instagram_account_key = (
        raw_instagram_account_key.strip() if isinstance(raw_instagram_account_key, str) else ""
    )
    if not _OPAQUE_ACCOUNT_KEY_RE.fullmatch(instagram_account_key):
        instagram_account_key = ""
    return {
        "xhs_seen_note_keys": merge_seen_keys(loaded.get("xhs_seen_note_keys", []), []),
        "dy_seen_video_keys": merge_seen_keys(loaded.get("dy_seen_video_keys", []), []),
        "yt_seen_item_keys": merge_seen_keys(loaded.get("yt_seen_item_keys", []), []),
        "zhihu_seen_item_keys": merge_seen_keys(loaded.get("zhihu_seen_item_keys", []), []),
        "reddit_seen_item_keys": merge_seen_keys(loaded.get("reddit_seen_item_keys", []), []),
        "linuxdo_seen_item_keys": merge_seen_keys(loaded.get("linuxdo_seen_item_keys", []), []),
        "linuxdo_account_key": (
            loaded.get("linuxdo_account_key", "").strip()
            if isinstance(loaded.get("linuxdo_account_key", ""), str)
            else ""
        ),
        "v2ex_seen_item_keys": merge_seen_keys(loaded.get("v2ex_seen_item_keys", []), []),
        "weibo_seen_item_keys": merge_seen_keys(loaded.get("weibo_seen_item_keys", []), []),
        "weibo_account_key": (
            loaded.get("weibo_account_key", "").strip()
            if isinstance(loaded.get("weibo_account_key", ""), str)
            else ""
        ),
        "instagram_seen_item_keys": merge_seen_keys(loaded.get("instagram_seen_item_keys", []), []),
        "instagram_account_key": instagram_account_key,
        "last_source_bootstrap_sync_at": (
            loaded.get("last_source_bootstrap_sync_at", "").strip()
            if isinstance(loaded.get("last_source_bootstrap_sync_at", ""), str)
            else ""
        ),
        "source_incremental": _normalize_source_incremental_state(
            loaded.get("source_incremental", {})
        ),
    }
