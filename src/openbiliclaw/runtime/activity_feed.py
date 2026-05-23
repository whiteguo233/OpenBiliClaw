"""Build a compact popup activity feed from existing runtime signals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


class SupportsRecommendations(Protocol):
    def get_recommendations(self, limit: int = 20) -> list[dict[str, Any]]: ...


def _normalize_text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _as_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = _normalize_text(value)
    if not text:
        return 0
    try:
        return int(text)
    except ValueError:
        return 0


def _parse_timestamp(value: object) -> datetime:
    text = _normalize_text(value)
    if not text:
        return datetime.min
    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        try:
            return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return datetime.min


@dataclass(slots=True)
class ActivityFeedBuilder:
    """Aggregate recent user-visible runtime changes for popup display."""

    database: SupportsRecommendations

    def build(
        self,
        *,
        runtime_status: dict[str, object],
        cognition_updates: list[dict[str, object]],
        limit: int = 10,
        before: str = "",
    ) -> dict[str, object]:
        items: list[dict[str, object]] = []
        items.extend(self._cognition_items(cognition_updates))
        items.extend(self._feedback_items())
        items.extend(self._recommendation_items())
        pool_item = self._pool_update_item(runtime_status)
        if pool_item is not None:
            items.append(pool_item)

        items.sort(key=lambda item: _parse_timestamp(item.get("created_at", "")), reverse=True)

        # Cursor pagination by timestamp. ``before`` is an ISO datetime
        # string emitted as ``next_cursor`` in the prior page; we keep
        # only items strictly older than it.
        if before:
            cutoff = _parse_timestamp(before)
            items = [
                it for it in items
                if _parse_timestamp(it.get("created_at", "")) < cutoff
            ]

        page_size = max(1, min(50, int(limit)))
        page = items[:page_size]
        has_more = len(items) > page_size
        next_cursor = ""
        if has_more and page:
            next_cursor = str(page[-1].get("created_at", ""))

        live_summary = self._live_summary(runtime_status)
        headline = page[0]["summary"] if page else live_summary
        return {
            "live_summary": live_summary,
            "headline": headline,
            "items": page,
            "has_more": has_more,
            "next_cursor": next_cursor,
        }

    def _live_summary(self, runtime_status: dict[str, object]) -> str:
        manual_state = _normalize_text(runtime_status.get("manual_refresh_state", ""))
        manual_message = _normalize_text(runtime_status.get("manual_refresh_message", ""))
        if manual_state == "running" and manual_message:
            return manual_message
        if manual_state == "failed" and manual_message:
            return manual_message
        pending_events = _as_int(runtime_status.get("pending_signal_events", 0))
        if pending_events > 0:
            return f"阿B 还在盯着你刚刚的新动作，已经记下 {pending_events} 个信号。"
        pool_count = _as_int(runtime_status.get("pool_available_count", 0))
        return f"这会儿池子里还有 {pool_count} 条能换，阿B 先替你盯着。"

    def _cognition_items(self, updates: list[dict[str, object]]) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        for update in updates:
            summary = _normalize_text(update.get("summary"))
            if not summary:
                continue
            kind = _normalize_text(update.get("kind")) or "cognition"
            items.append(
                {
                    "id": _normalize_text(update.get("id")) or f"cognition-{len(items)}",
                    "kind": kind,
                    "summary": summary,
                    "detail": "",
                    "created_at": _normalize_text(update.get("created_at")),
                    "tone": "success",
                }
            )
        return items

    def _feedback_items(self) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        for row in self.database.get_recommendations(limit=20):
            feedback_type = _normalize_text(row.get("feedback_type"))
            if not feedback_type:
                continue
            title = _normalize_text(row.get("title")) or "这条推荐"
            note = _normalize_text(row.get("feedback_note"))
            if feedback_type == "like":
                summary = f"这条你点了多来点：{title}"
                tone = "success"
            elif feedback_type == "dislike":
                summary = f"这条你点了少来点：{title}"
                tone = "error"
            elif feedback_type == "dismiss":
                summary = f"这条你忽略了：{title}"
                tone = "info"
            else:
                summary = note or f"你刚给 {title} 写了一句反馈"
                tone = "info"
            detail = note if feedback_type != "comment" else "这句会继续影响后面的推荐。"
            items.append(
                {
                    "id": f"feedback-{row.get('id', len(items))}",
                    "kind": "feedback",
                    "summary": summary,
                    "detail": detail,
                    "created_at": _normalize_text(row.get("feedback_at") or row.get("created_at")),
                    "tone": tone,
                }
            )
        return items[:4]

    def _recommendation_items(self) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        for row in self.database.get_recommendations(limit=10):
            title = _normalize_text(row.get("title"))
            if not title:
                continue
            items.append(
                {
                    "id": f"recommendation-{row.get('id', len(items))}",
                    "kind": "recommendation",
                    "summary": f"这批先给你翻出来了：{title}",
                    "detail": (
                        _normalize_text(row.get("topic"))
                        or _normalize_text(row.get("expression"))
                    ),
                    "created_at": _normalize_text(row.get("created_at")),
                    "tone": "info",
                }
            )
        return items[:3]

    def _pool_update_item(self, runtime_status: dict[str, object]) -> dict[str, object] | None:
        replenished = _as_int(runtime_status.get("last_replenished_count", 0))
        if replenished <= 0:
            return None
        topics = runtime_status.get("recent_pool_topics", [])
        topic_text = ""
        if isinstance(topics, list):
            topic_values = [_normalize_text(item) for item in topics if _normalize_text(item)]
            if topic_values:
                topic_text = " / ".join(topic_values[:3])
        detail = f"最近主要在补：{topic_text}" if topic_text else "这轮先把池子补厚一点。"
        return {
            "id": "pool-update-latest",
            "kind": "pool_update",
            "summary": f"刚补进 {replenished} 条新的。",
            "detail": detail,
            "created_at": _normalize_text(runtime_status.get("last_refresh_at")),
            "tone": "success",
        }
