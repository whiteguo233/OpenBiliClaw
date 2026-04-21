"""Soul-driven xhs search task producer.

Runs on the same loop as the continuous refresh controller. Once per
throttle window (default 4h) it:
  1. Reads the current SoulProfile
  2. Asks an LLM to rewrite interest tags into xhs-flavored keywords
  3. Enqueues one ``search`` task per keyword into ``XhsTaskQueue``

The extension's background dispatcher polls the queue, opens each
search page in a hidden tab, and reports results back — closing the
Soul → Discovery loop for xiaohongshu.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from openbiliclaw.sources.xhs_keyword_gen import generate_xhs_keywords

if TYPE_CHECKING:
    from openbiliclaw.llm.service import LLMService
    from openbiliclaw.sources.xhs_tasks import XhsTaskQueue

logger = logging.getLogger(__name__)


@dataclass
class XhsTaskProducer:
    """Enqueues xhs search tasks from the SoulProfile on a throttle.

    The producer respects two limits:
    - ``daily_budget`` — enforced by ``XhsTaskQueue.enqueue`` per type
    - ``min_interval_hours`` — enforced here by inspecting the newest
      task's ``created_at`` before running
    """

    task_queue: XhsTaskQueue
    soul_engine: Any
    llm_service: LLMService
    enabled: bool = True
    daily_budget: int = 30
    min_interval_hours: int = 4
    keywords_per_cycle: int = 5
    _last_skip_reason: str = field(default="", init=False)

    async def produce_if_due(self) -> dict[str, object]:
        """Run one producer cycle if enough time has passed.

        Returns a summary dict for diagnostics. When the producer is
        disabled, throttled, or has nothing useful to enqueue, the result
        carries ``enqueued: 0`` and a ``reason`` string — callers should
        treat it as a no-op.
        """
        if not self.enabled:
            return self._skip("disabled")

        if not self._is_due():
            return self._skip("throttled")

        try:
            profile = await self.soul_engine.get_profile()
        except Exception as exc:
            logger.warning("xhs producer: soul profile unavailable: %s", exc)
            return self._skip("no_profile")

        if profile is None:
            return self._skip("no_profile")

        keywords = await generate_xhs_keywords(
            self.llm_service,
            profile,
            count=self.keywords_per_cycle,
        )
        if not keywords:
            return self._skip("no_keywords")

        enqueued = 0
        for keyword in keywords:
            ok = self.task_queue.enqueue(
                "search",
                {"keyword": keyword},
                daily_budget=self.daily_budget,
            )
            if ok:
                enqueued += 1
            else:
                break  # budget exhausted — stop early
        logger.info(
            "xhs producer enqueued %d/%d search tasks",
            enqueued,
            len(keywords),
        )
        return {"enqueued": enqueued, "attempted": len(keywords), "reason": "ok"}

    def _is_due(self) -> bool:
        """Return False if the newest search task was enqueued recently."""
        if self.min_interval_hours <= 0:
            return True
        row = self.task_queue._db.conn.execute(
            "SELECT created_at FROM xhs_tasks "
            "WHERE type = 'search' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return True
        created_at_str = str(row["created_at"] if "created_at" in row else row[0])
        last = _parse_sqlite_timestamp(created_at_str)
        if last is None:
            return True
        return datetime.now(UTC) - last >= timedelta(
            hours=self.min_interval_hours
        )

    def _skip(self, reason: str) -> dict[str, object]:
        self._last_skip_reason = reason
        return {"enqueued": 0, "attempted": 0, "reason": reason}


def _parse_sqlite_timestamp(value: str) -> datetime | None:
    """Parse SQLite CURRENT_TIMESTAMP (``YYYY-MM-DD HH:MM:SS``) as UTC."""
    if not value:
        return None
    try:
        dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt
