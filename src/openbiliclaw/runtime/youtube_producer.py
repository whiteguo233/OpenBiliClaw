"""Runtime YouTube discovery producer.

YouTube steady-state discovery is backend-direct: the runtime can call
scrapetube / yt-dlp backed strategies itself and does not need the
browser-extension task queue used by bootstrap imports.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

YOUTUBE_DISCOVERY_STRATEGIES = ("yt_search", "yt_trending", "yt_channel")


@dataclass(frozen=True)
class YoutubeStrategyRunResult:
    """Result summary for one YouTube strategy execution."""

    items: list[Any]
    units_used: int
    source_counts: dict[str, int]


YoutubeDiscoverCallable = Callable[..., Awaitable[YoutubeStrategyRunResult]]


@dataclass
class YoutubeDiscoveryProducer:
    """Throttle and invoke YouTube discovery from the runtime loop."""

    database: Any
    soul_engine: Any
    discover: YoutubeDiscoverCallable
    enabled: bool = True
    min_interval_minutes: int = 60
    daily_search_budget: int = 6
    daily_trending_budget: int = 50
    daily_channel_budget: int = 10
    strategies: tuple[str, ...] = YOUTUBE_DISCOVERY_STRATEGIES
    _last_run_at: datetime | None = field(default=None, init=False)
    _last_skip_reason: str = field(default="", init=False)

    async def produce_if_due(self, *, limit: int | None = None) -> dict[str, object]:
        """Run one YouTube discovery cycle if enabled, due, and under budget."""
        if not self.enabled:
            return self._skip("disabled")
        if not self._is_due():
            return self._skip("throttled")

        try:
            profile = await self.soul_engine.get_profile()
        except Exception as exc:
            logger.debug("youtube producer: soul profile unavailable: %s", exc)
            return self._skip("no_profile")
        if profile is None:
            return self._skip("no_profile")

        remaining = self.remaining_budgets()
        runnable = [strategy for strategy in self.strategies if int(remaining.get(strategy, 0)) > 0]
        if not runnable:
            return self._skip("budget_exhausted")

        requested_limit = max(1, int(limit or 10))
        discovered_total = 0
        source_counts: Counter[str] = Counter()
        error_count = 0

        for strategy in runnable:
            unit_budget = max(0, int(remaining.get(strategy, 0)))
            if unit_budget <= 0:
                continue
            try:
                result = await self.discover(
                    profile,
                    strategy=strategy,
                    unit_budget=unit_budget,
                    result_limit=requested_limit,
                )
            except Exception as exc:
                error_count += 1
                logger.warning(
                    "youtube producer strategy failed: strategy=%s error=%s",
                    strategy,
                    exc,
                )
                self.record_strategy_run(
                    strategy,
                    units_used=0,
                    discovered=0,
                    reason="error",
                )
                continue

            units_used = max(0, min(unit_budget, int(result.units_used)))
            discovered = len(result.items)
            self.record_strategy_run(
                strategy,
                units_used=units_used,
                discovered=discovered,
                reason="ok",
            )
            discovered_total += discovered
            source_counts.update(result.source_counts)

        self._last_run_at = datetime.now(UTC)
        if discovered_total <= 0 and error_count >= len(runnable):
            return {"discovered": 0, "reason": "error"}
        return {
            "discovered": discovered_total,
            "source_counts": dict(source_counts),
            "reason": "ok",
        }

    def remaining_budgets(self) -> dict[str, int]:
        """Return remaining execution units by YouTube strategy for today."""
        configured = {
            "yt_search": max(0, int(self.daily_search_budget)),
            "yt_trending": max(0, int(self.daily_trending_budget)),
            "yt_channel": max(0, int(self.daily_channel_budget)),
        }
        return {
            strategy: max(0, budget - self.consumed_today(strategy))
            for strategy, budget in configured.items()
        }

    def consumed_today(self, strategy: str) -> int:
        """Return today's successful execution units for one strategy."""
        self._ensure_ledger_table()
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        row = self.database.conn.execute(
            """
            SELECT COALESCE(SUM(units), 0)
            FROM youtube_discovery_runs
            WHERE strategy = ? AND created_at >= ? AND reason = 'ok'
            """,
            (strategy, today),
        ).fetchone()
        return int(row[0] if row is not None else 0)

    def record_strategy_run(
        self,
        strategy: str,
        *,
        units_used: int,
        discovered: int,
        reason: str,
    ) -> None:
        """Record one strategy execution in the daily budget ledger."""
        self._ensure_ledger_table()
        self.database.conn.execute(
            """
            INSERT INTO youtube_discovery_runs(strategy, units, discovered, reason)
            VALUES (?, ?, ?, ?)
            """,
            (
                strategy,
                max(0, int(units_used)),
                max(0, int(discovered)),
                reason,
            ),
        )
        self.database.conn.commit()

    def _ensure_ledger_table(self) -> None:
        self.database.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS youtube_discovery_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy TEXT NOT NULL,
                units INTEGER NOT NULL DEFAULT 0,
                discovered INTEGER NOT NULL DEFAULT 0,
                reason TEXT NOT NULL DEFAULT 'ok',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_youtube_discovery_runs_strategy_created
                ON youtube_discovery_runs(strategy, created_at);
            """
        )
        self.database.conn.commit()

    def _is_due(self) -> bool:
        if self.min_interval_minutes <= 0:
            return True
        if self._last_run_at is None:
            return True
        return datetime.now(UTC) - self._last_run_at >= timedelta(minutes=self.min_interval_minutes)

    def _skip(self, reason: str) -> dict[str, object]:
        if reason != self._last_skip_reason:
            logger.info("youtube producer skip: reason=%s", reason)
        self._last_skip_reason = reason
        return {"discovered": 0, "reason": reason}
