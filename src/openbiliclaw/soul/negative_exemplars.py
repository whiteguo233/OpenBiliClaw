"""Recency-weighted list of recent quick-exit / negative titles.

Downstream consumer of the v0.3.x ``inferred_satisfaction`` signal.
Used by ``discovery/engine.py``'s eval-batch call site to anchor the
LLM evaluator on concrete examples of content the user actually
disliked, so candidates resembling clickbait or course-pitch language
get downscored without depending on hand-curated regex blocklists.

Pure, deterministic, no LLM calls. Storage failures are swallowed —
the eval-batch must still run when the negative store is empty or
unavailable.
"""

from __future__ import annotations

import logging
import math
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterable  # noqa: F401  # documentation

logger = logging.getLogger(__name__)

MAX_LIMIT = 16
DEFAULT_HALF_LIFE_DAYS = 14
TITLE_MAX_CHARS = 80

# How many candidate rows to pull from the event store before recency
# weighting / dedup. Larger than ``limit`` so the scorer has material
# to discard.
_FETCH_LIMIT = 200

# Normalised prefix length used for dedup. 20 chars is enough to catch
# clickbait variants of the same hook without collapsing genuinely
# different titles.
_DEDUPE_PREFIX_CHARS = 20

# Characters stripped from titles before computing the dedup key. Hash /
# emoji / common punctuation noise is removed; alpha-numeric and CJK
# characters are kept verbatim.
_DEDUPE_STRIP_PATTERN = re.compile(r"[\s#​]+|[!！?？.。,，~～\-—•·]+")


class EventStore(Protocol):
    def query_events(
        self,
        *,
        satisfaction_modes: frozenset[str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]: ...


def recent_negative_exemplars(
    event_store: EventStore,
    *,
    limit: int = MAX_LIMIT,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Return up to ``limit`` recent negative exemplars, recency-weighted.

    Each record carries ``{"title": str, "reason": str, "age_days": int}``.
    Storage exceptions are swallowed and yield an empty list — the
    consumer (eval-batch) must always run.
    """
    capped_limit = min(int(limit), MAX_LIMIT)
    if capped_limit <= 0:
        return []
    now_ts = now or datetime.now()

    try:
        rows = event_store.query_events(
            satisfaction_modes=frozenset({"negative"}),
            limit=_FETCH_LIMIT,
        )
    except Exception:
        logger.debug("recent_negative_exemplars: query_events failed", exc_info=True)
        return []

    scored: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        age_days = _event_age_days(row, now_ts)
        weight = math.exp(-age_days / half_life_days) if half_life_days > 0 else 0.0
        scored.append(
            (
                weight,
                {
                    "title": _truncate_title(title),
                    "reason": str(row.get("satisfaction_reason") or "negative"),
                    "age_days": int(round(age_days)),
                    # Carry the raw weight + raw title for dedup decisions;
                    # both are stripped from the returned record below.
                    "_raw_title": title,
                    "_weight": weight,
                },
            )
        )

    # Sort by weight descending (newer wins); stable so ties prefer the
    # first row from the event store (already DESC by created_at).
    scored.sort(key=lambda pair: pair[0], reverse=True)

    deduped: list[dict[str, Any]] = []
    seen_prefixes: set[str] = set()
    for _weight, record in scored:
        prefix = _normalize_prefix(record["_raw_title"])
        if prefix in seen_prefixes:
            continue
        seen_prefixes.add(prefix)
        deduped.append(record)
        if len(deduped) >= capped_limit:
            break

    return [
        {
            "title": record["title"],
            "reason": record["reason"],
            "age_days": record["age_days"],
        }
        for record in deduped
    ]


def _event_age_days(row: dict[str, Any], now: datetime) -> float:
    """Compute age in days from a row's ``created_at`` or fallback fields."""
    raw = row.get("created_at") or row.get("timestamp")
    if isinstance(raw, str) and raw:
        try:
            created = datetime.fromisoformat(raw.replace(" ", "T"))
        except ValueError:
            return 0.0
    elif isinstance(raw, datetime):
        created = raw
    else:
        return 0.0
    delta = now - created
    return max(0.0, delta.total_seconds() / 86400.0)


def _truncate_title(title: str) -> str:
    if len(title) <= TITLE_MAX_CHARS:
        return title
    return title[: TITLE_MAX_CHARS - 1] + "…"


def _normalize_prefix(title: str) -> str:
    """Compute a dedup key. Strips whitespace, hashtags, punctuation."""
    lowered = title.lower()
    stripped = _DEDUPE_STRIP_PATTERN.sub("", lowered)
    return stripped[:_DEDUPE_PREFIX_CHARS]
