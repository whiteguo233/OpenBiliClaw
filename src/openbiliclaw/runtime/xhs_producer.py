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

from openbiliclaw.runtime.keyword_fetch import PLATFORM_XIAOHONGSHU as _PLATFORM_XIAOHONGSHU
from openbiliclaw.sources.xhs_keyword_gen import generate_xhs_keywords

if TYPE_CHECKING:
    from openbiliclaw.llm.service import LLMService
    from openbiliclaw.sources.xhs_tasks import XhsTaskQueue

logger = logging.getLogger(__name__)


@dataclass
class XhsTaskProducer:
    """Enqueues xhs search tasks from the SoulProfile on a throttle.

    The producer respects two limits:
    - ``daily_budget`` — enforced by ``XhsTaskQueue.enqueue`` per type;
      ``0`` disables the daily cap
    - ``min_interval_hours`` — enforced here by inspecting the newest
      task's ``created_at`` before running
    """

    task_queue: XhsTaskQueue
    soul_engine: Any
    llm_service: LLMService
    enabled: bool = True
    daily_budget: int = 0
    # Unified keyword planner fetch coordinator (P1.7). When wired AND the flag
    # is on, the producer claims words from the keyword store, enqueues one xhs
    # search task per word carrying its ``source_keyword_id``, and marks the word
    # ``executing`` (NOT ``used`` — XHS is truly async; the task-result handler
    # marks the terminal ``used`` / ``failed``). Budget rejection on enqueue
    # (``ok=False``) rolls the word back to ``pending``. ``None`` (default / flag
    # off) → legacy self-generated, lifecycle-free enqueue.
    keyword_fetch: Any | None = None
    # v0.3.53+: lowered 4 → 1. Production logs (2026-05-05) showed
    # the producer firing only once per 43-minute session because the
    # 4-hour throttle is way too long for pool freshness — XHS pool
    # was effectively static while user kept reshuffling. 1-hour
    # cadence with daily_budget=30 caps at 24/30 enqueues per day,
    # leaves 6 head room for manual / refresh-tick triggers.
    min_interval_hours: int = 1
    keywords_per_cycle: int = 5
    _last_skip_reason: str = field(default="", init=False)

    async def produce_if_due(
        self,
        *,
        limit: int | None = None,
        keywords: list[str] | None = None,
    ) -> dict[str, object]:
        """Run one producer cycle if enough time has passed.

        Returns a summary dict for diagnostics. When the producer is
        disabled, throttled, or has nothing useful to enqueue, the result
        carries ``enqueued: 0`` and a ``reason`` string — callers should
        treat it as a no-op.

        Args:
            limit: Optional cap on the number of keywords generated this cycle.
            keywords: Optional caller-supplied keywords (unified keyword planner
                injection point). When provided (non-None), they are enqueued
                directly and the internal ``generate_xhs_keywords`` LLM call is
                skipped. When ``None``, the producer generates its own keywords
                from the profile as before.
        """
        if not self.enabled:
            return self._skip("disabled")

        if not self._is_due():
            return self._skip("throttled")

        keyword_count = min(
            self.keywords_per_cycle,
            max(1, int(limit or self.keywords_per_cycle)),
        )

        # Unified keyword planner fetch path (P1.7, flag-gated). Takes priority
        # over both external injection and self-generation: claim words from the
        # store, enqueue each as a task carrying its ``source_keyword_id``, and
        # mark it ``executing`` (XHS is truly async — terminal ``used`` /
        # ``failed`` is the task-result handler's job). The deficit gate is
        # upstream; the distinct floor is ``min_interval`` / ``_is_due`` above.
        coordinator = self.keyword_fetch
        if (
            keywords is None
            and coordinator is not None
            and bool(getattr(coordinator, "should_claim", lambda: False)())
        ):
            claimed = coordinator.claim(_PLATFORM_XIAOHONGSHU, keyword_count)
            if not claimed:
                # Flag on but the store has no claimable pending words → skip
                # this cycle (the planner will refill).
                return self._skip("no_keywords")
            return self._enqueue_claimed_keywords(claimed)

        if keywords is not None:
            resolved_keywords = _dedupe_keywords(keywords)[:keyword_count]
            if not resolved_keywords:
                return self._skip("no_keywords")
            return self._enqueue_keywords(resolved_keywords)

        is_ready_fn = getattr(self.soul_engine, "is_profile_ready", None)
        if callable(is_ready_fn) and not is_ready_fn():
            # Init's first ~7 minutes — every minute the producer ticks
            # would otherwise WARN. Silent skip; we'll retry next tick.
            logger.debug("xhs producer: soul profile not ready yet")
            return self._skip("no_profile")
        try:
            profile = await self.soul_engine.get_profile()
        except Exception as exc:
            logger.warning("xhs producer: soul profile unavailable: %s", exc)
            return self._skip("no_profile")

        if profile is None:
            return self._skip("no_profile")

        resolved_keywords = await generate_xhs_keywords(
            self.llm_service,
            profile,
            count=keyword_count,
        )
        if not resolved_keywords:
            return self._skip("no_keywords")
        return self._enqueue_keywords(resolved_keywords)

    def _enqueue_keywords(self, keywords: list[str]) -> dict[str, object]:
        """Enqueue one ``search`` task per keyword, stopping when budget is hit."""

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

    def _enqueue_claimed_keywords(self, claimed: list[Any]) -> dict[str, object]:
        """Enqueue one task per claimed word (carrying its ``source_keyword_id``).

        XHS is truly async: enqueuing only hands the search to the extension, so
        each enqueued word is marked ``executing`` — NOT ``used`` (the terminal
        is the task-result handler's job). A word whose enqueue is refused by
        budget (``enqueue_with_id`` returns ``None``) is rolled back to
        ``pending`` rather than burned; enqueueing stops early at the budget
        wall. ``source_keyword_id`` is the lifecycle correlation that the
        task-result handler reads back to mark the terminal (P1.8 extends it
        onto candidates for yield).
        """
        coordinator = self.keyword_fetch
        enqueued = 0
        for item in claimed:
            task_id = self.task_queue.enqueue_with_id(
                "search",
                {"keyword": item.keyword, "source_keyword_id": int(item.id)},
                daily_budget=self.daily_budget,
            )
            if task_id is not None:
                enqueued += 1
                if coordinator is not None:
                    coordinator.mark_executing(item)
            else:
                # Budget exhausted: roll this word (and every still-unclaimed
                # one after it) back to pending so none are burned.
                if coordinator is not None:
                    coordinator.rollback(item)
                break
        # Roll back any words we never reached (the loop broke at the budget wall).
        if coordinator is not None and enqueued < len(claimed):
            for item in claimed[enqueued + 1 :]:
                coordinator.rollback(item)
        logger.info(
            "xhs producer enqueued %d/%d claimed search tasks (executing)",
            enqueued,
            len(claimed),
        )
        return {"enqueued": enqueued, "attempted": len(claimed), "reason": "ok"}

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
        return datetime.now(UTC) - last >= timedelta(hours=self.min_interval_hours)

    def _skip(self, reason: str) -> dict[str, object]:
        # v0.3.53+: log skip reason on transition (not every minute) so
        # operators can grep for why the producer isn't firing without
        # drowning the log in identical-reason WARNINGs. Reasons:
        #   disabled       — explicitly turned off in config
        #   throttled      — last enqueue within ``min_interval_hours``
        #   no_profile     — soul profile not built yet (init window)
        #   no_keywords    — LLM keyword generation returned 0 items
        if reason != self._last_skip_reason:
            logger.info("xhs producer skip: reason=%s", reason)
        self._last_skip_reason = reason
        return {"enqueued": 0, "attempted": 0, "reason": reason}


def _dedupe_keywords(keywords: list[str]) -> list[str]:
    """Strip + dedupe caller-injected keywords (unified planner injection)."""
    seen: set[str] = set()
    out: list[str] = []
    for item in keywords:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


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
