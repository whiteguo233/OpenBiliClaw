"""Shared helpers and error types for the M3 standard tool set.

Handlers never let a missing runtime component escape as an unexpected
exception: they raise ``ToolComponentUnavailableError`` (or return a
readable message for user-correctable input), and ``ToolRegistry.dispatch``
maps the raise to a machine-readable ``handler_error`` result.
"""

from __future__ import annotations

import inspect
import json
from typing import Any


class ToolComponentUnavailableError(RuntimeError):
    """Raised when a handler's required runtime component is not wired."""


def require_component(component: Any, name: str) -> Any:
    """Return ``component`` or raise a machine-diagnosable unavailability error."""
    if component is None:
        raise ToolComponentUnavailableError(f"组件不可用: {name} 未初始化")
    return component


async def maybe_await(value: Any) -> Any:
    """Await ``value`` when it is awaitable; pass plain values through.

    Production components are async while tests often substitute sync fakes;
    this keeps handlers agnostic to either shape.
    """
    if inspect.isawaitable(value):
        return await value
    return value


def clamp_int(value: Any, *, default: int, minimum: int = 1, maximum: int = 100) -> int:
    """Coerce an LLM-supplied number into a bounded int."""
    if isinstance(value, bool):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def truncate_text(text: str, max_chars: int) -> str:
    """Bound tool output size; append an explicit truncation marker."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n…（已截断，共 {len(text)} 字符）"


def short(value: Any, limit: int = 80) -> str:
    """One-line, length-bounded rendering of a row field."""
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def render_content_reference(row: dict[str, Any]) -> str:
    """Keep exact action identifiers beside a content row's readable summary.

    Native content IDs and signed URLs must survive read → save/feedback
    chains. Cache ``bvid`` is a storage key on non-Bilibili platforms, so
    prefer ``content_id`` and strip only a matching platform namespace as
    a legacy fallback. Pool rows have no recommendation ID; never invent one.
    """
    platform = str(row.get("source_platform") or "bilibili").strip().lower()
    content_id = str(row.get("content_id") or "").strip()
    if not content_id:
        content_id = str(row.get("bvid") or "").strip().removeprefix(f"{platform}:")
    reference: dict[str, Any] = {"source_platform": platform}
    if content_id:
        reference["content_id"] = content_id
    for key in ("content_url", "content_type", "title"):
        if row.get(key):
            reference[key] = str(row[key])
    author = row.get("author_name") or row.get("up_name")
    if author:
        reference["author_name"] = str(author)
    recommendation_id = row.get("recommendation_id")
    if (
        isinstance(recommendation_id, int)
        and not isinstance(recommendation_id, bool)
        and recommendation_id > 0
    ):
        reference["recommendation_id"] = recommendation_id
    return "\n    定位信息: " + json.dumps(reference, ensure_ascii=False, sort_keys=True)
