"""Shared state helpers for extension bootstrap source deduplication."""

from __future__ import annotations

from typing import Any

SOURCE_BOOTSTRAP_STATE_KEYS: dict[str, str] = {
    "xhs": "xhs_seen_note_keys",
    "xiaohongshu": "xhs_seen_note_keys",
    "dy": "dy_seen_video_keys",
    "douyin": "dy_seen_video_keys",
    "yt": "yt_seen_item_keys",
    "youtube": "yt_seen_item_keys",
}


def default_source_bootstrap_state() -> dict[str, object]:
    """Return the persisted-source bootstrap dedupe state shape."""
    return {
        "xhs_seen_note_keys": [],
        "dy_seen_video_keys": [],
        "yt_seen_item_keys": [],
        "last_source_bootstrap_sync_at": "",
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


def normalize_source_bootstrap_state(loaded: Any) -> dict[str, object]:
    """Coerce arbitrary JSON into the stable source-bootstrap state shape."""
    default = default_source_bootstrap_state()
    if not isinstance(loaded, dict):
        return default
    return {
        "xhs_seen_note_keys": as_string_list(loaded.get("xhs_seen_note_keys", [])),
        "dy_seen_video_keys": as_string_list(loaded.get("dy_seen_video_keys", [])),
        "yt_seen_item_keys": as_string_list(loaded.get("yt_seen_item_keys", [])),
        "last_source_bootstrap_sync_at": str(loaded.get("last_source_bootstrap_sync_at", "")),
    }
