"""Continuous refresh controller for the local API runtime."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol


class SupportsRuntimeState(Protocol):
    def load_discovery_runtime_state(self) -> dict[str, object]: ...
    def save_discovery_runtime_state(self, state: dict[str, object]) -> None: ...
    def get_layer(self, name: str) -> Any: ...


class SupportsEventDatabase(Protocol):
    def query_events_since(
        self,
        *,
        after_event_id: int,
        event_types: list[str],
    ) -> list[dict[str, Any]]: ...
    def get_latest_event_id(self) -> int: ...
    def count_recommendations(self) -> int: ...
    def count_unread_recommendations(self) -> int: ...
    def get_notification_candidate(
        self,
        *,
        min_confidence: float = 0.82,
    ) -> dict[str, Any] | None: ...
    def mark_notification_sent(self, bvid: str) -> None: ...


class SupportsProfileEngine(Protocol):
    async def get_profile(self) -> Any: ...


class SupportsDiscoveryEngine(Protocol):
    async def discover(
        self,
        profile: Any,
        strategies: list[str] | None = None,
        limit: int = 30,
    ) -> list[Any]: ...


class SupportsRecommendationEngine(Protocol):
    async def generate_recommendations(
        self,
        discovered: list[Any] | None,
        profile: Any,
        limit: int = 10,
    ) -> list[Any]: ...


@dataclass
class ContinuousRefreshController:
    """Keep discovery cache and recommendations fresh during API runtime."""

    memory_manager: SupportsRuntimeState
    database: SupportsEventDatabase
    soul_engine: SupportsProfileEngine
    discovery_engine: SupportsDiscoveryEngine
    recommendation_engine: SupportsRecommendationEngine
    signal_event_threshold: int = 6
    event_refresh_minutes: int = 0
    trending_refresh_hours: int = 3
    explore_refresh_hours: int = 12
    notification_cooldown_hours: int = 2
    check_interval_seconds: int = 60
    discovery_limit: int = 18
    _manual_refresh_task: asyncio.Task[None] | None = None
    _manual_refresh_state: str = "idle"
    _manual_refresh_message: str = ""
    _manual_refresh_started_at: str = ""
    _manual_refresh_finished_at: str = ""

    _signal_event_types = [
        "view",
        "search",
        "favorite",
        "like",
        "coin",
        "comment",
        "feedback",
    ]

    def get_runtime_status(self) -> dict[str, object]:
        """Build a lightweight runtime summary for popup or diagnostics."""
        state = self.memory_manager.load_discovery_runtime_state()
        refresh_values = [
            str(state.get("last_event_refresh_at", "")),
            str(state.get("last_trending_refresh_at", "")),
            str(state.get("last_explore_refresh_at", "")),
        ]
        parsed_refresh_values: list[datetime] = []
        for value in refresh_values:
            parsed = self._parse_iso_datetime(value)
            if parsed is not None:
                parsed_refresh_values.append(parsed)
        last_refresh_at = (
            max(parsed_refresh_values).isoformat() if parsed_refresh_values else ""
        )
        return {
            "initialized": self._is_initialized(),
            "recommendation_count": self.database.count_recommendations(),
            "pending_signal_events": self._pending_signal_events_count(state),
            "last_refresh_at": last_refresh_at,
            "last_notification_at": str(state.get("last_notification_at", "")),
            "unread_count": self.database.count_unread_recommendations(),
            "manual_refresh_state": self._manual_refresh_state,
            "manual_refresh_message": self._manual_refresh_message,
        }

    async def refresh_if_needed(self) -> dict[str, object]:
        """Refresh candidates and recommendations when thresholds are met."""
        state = self.memory_manager.load_discovery_runtime_state()
        if not self._is_initialized():
            return {"refreshed": False, "strategies": [], "reason": "not_initialized"}

        profile = await self.soul_engine.get_profile()
        strategies = self._select_refresh_strategies(state)
        if not strategies:
            return {"refreshed": False, "strategies": [], "reason": "below_threshold"}

        return await self._run_refresh(
            state=state,
            profile=profile,
            strategies=strategies,
            reason="triggered",
        )

    async def force_refresh(self) -> dict[str, object]:
        """Run a full refresh immediately, bypassing runtime thresholds."""
        state = self.memory_manager.load_discovery_runtime_state()
        if not self._is_initialized():
            return {"refreshed": False, "strategies": [], "reason": "not_initialized"}

        profile = await self.soul_engine.get_profile()
        strategies = ["search", "related_chain", "trending", "explore"]
        return await self._run_refresh(
            state=state,
            profile=profile,
            strategies=strategies,
            reason="manual",
        )

    async def trigger_manual_refresh(self) -> dict[str, object]:
        """Schedule one background manual refresh without blocking the caller."""
        if not self._is_initialized():
            return {"accepted": False, "state": "idle", "reason": "not_initialized"}
        if self._manual_refresh_task is not None and not self._manual_refresh_task.done():
            return {"accepted": True, "state": "running", "reason": "already_running"}

        self._manual_refresh_state = "running"
        self._manual_refresh_message = "正在补货…"
        self._manual_refresh_started_at = self._now().isoformat()
        self._manual_refresh_finished_at = ""
        self._manual_refresh_task = asyncio.create_task(self._complete_manual_refresh())
        return {"accepted": True, "state": "running", "reason": "started"}

    def get_pending_notification(self) -> dict[str, object] | None:
        """Return one recommendation candidate for browser notification."""
        state = self.memory_manager.load_discovery_runtime_state()
        last_notification_at = self._parse_iso_datetime(
            str(state.get("last_notification_at", ""))
        )
        if last_notification_at is not None and self._now() - last_notification_at < timedelta(
            hours=self.notification_cooldown_hours
        ):
            return None
        candidate = self.database.get_notification_candidate(min_confidence=0.82)
        if candidate is None:
            return None
        return {
            "recommendation_id": int(candidate["id"]),
            "bvid": str(candidate.get("bvid", "")),
            "title": str(candidate.get("title", "")),
            "reason": str(candidate.get("expression", "")),
        }

    def mark_notification_sent(self, bvid: str) -> None:
        """Persist notification delivery markers."""
        self.database.mark_notification_sent(bvid)
        state = self.memory_manager.load_discovery_runtime_state()
        state["last_notification_at"] = self._now().isoformat()
        self.memory_manager.save_discovery_runtime_state(state)

    async def run_forever(self) -> None:
        """Run the refresh loop until cancelled."""
        while True:
            with suppress(Exception):
                await self.refresh_if_needed()
            await asyncio.sleep(self.check_interval_seconds)

    def _pending_signal_events_count(self, state: dict[str, object]) -> int:
        return len(
            self.database.query_events_since(
                after_event_id=self._int_state_value(state, "last_processed_event_id"),
                event_types=self._signal_event_types,
            )
        )

    def _select_refresh_strategies(self, state: dict[str, object]) -> list[str]:
        strategies: list[str] = []
        if self._pending_signal_events_count(state) >= self.signal_event_threshold:
            strategies.extend(["search", "related_chain"])
        if self._is_due(
            str(state.get("last_trending_refresh_at", "")),
            hours=self.trending_refresh_hours,
        ):
            strategies.append("trending")
        if self._is_due(
            str(state.get("last_explore_refresh_at", "")),
            hours=self.explore_refresh_hours,
        ):
            strategies.append("explore")
        seen: set[str] = set()
        ordered: list[str] = []
        for strategy in strategies:
            if strategy in seen:
                continue
            seen.add(strategy)
            ordered.append(strategy)
        return ordered

    async def refresh_after_event_ingest(self) -> dict[str, object]:
        """Opportunistically refresh after new events arrive."""
        return await self.refresh_if_needed()

    async def refresh_after_feedback(self) -> dict[str, object]:
        """Opportunistically refresh after explicit feedback."""
        return await self.refresh_if_needed()

    async def refresh_after_init(self) -> dict[str, object]:
        """Allow callers to trigger a refresh immediately after initialization."""
        return await self.refresh_if_needed()

    async def _complete_manual_refresh(self) -> None:
        try:
            await self.force_refresh()
        except Exception as exc:
            self._manual_refresh_state = "failed"
            self._manual_refresh_message = f"这次补货没跑通：{exc}"
            self._manual_refresh_finished_at = self._now().isoformat()
            return
        self._manual_refresh_state = "success"
        self._manual_refresh_message = "刚给你补了一批新的。"
        self._manual_refresh_finished_at = self._now().isoformat()

    async def _run_refresh(
        self,
        *,
        state: dict[str, object],
        profile: Any,
        strategies: list[str],
        reason: str,
    ) -> dict[str, object]:
        discovered = await self.discovery_engine.discover(
            profile,
            strategies=strategies,
            limit=self.discovery_limit,
        )
        recommendations = await self.recommendation_engine.generate_recommendations(
            discovered,
            profile,
            limit=10,
        )

        now = self._now().isoformat()
        latest_event_id = self.database.get_latest_event_id()
        if "search" in strategies or "related_chain" in strategies:
            state["last_event_refresh_at"] = now
            state["last_processed_event_id"] = latest_event_id
        if "trending" in strategies:
            state["last_trending_refresh_at"] = now
        if "explore" in strategies:
            state["last_explore_refresh_at"] = now
        self.memory_manager.save_discovery_runtime_state(state)
        return {
            "refreshed": True,
            "strategies": strategies,
            "reason": reason,
            "recommendation_count": len(recommendations),
        }

    def _is_initialized(self) -> bool:
        try:
            soul_layer = self.memory_manager.get_layer("soul")
        except Exception:
            return False
        data = getattr(soul_layer, "data", {})
        return isinstance(data, dict) and bool(data)

    @staticmethod
    def _parse_iso_datetime(value: str) -> datetime | None:
        if not value:
            return None
        with suppress(ValueError):
            return datetime.fromisoformat(value)
        return None

    @staticmethod
    def _int_state_value(state: dict[str, object], key: str) -> int:
        value = state.get(key, 0)
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            with suppress(ValueError):
                return int(value)
        return 0

    def _is_due(self, value: str, *, hours: int) -> bool:
        if hours <= 0:
            return True
        last_run = self._parse_iso_datetime(value)
        if last_run is None:
            return True
        return self._now() - last_run >= timedelta(hours=hours)

    @staticmethod
    def _now() -> datetime:
        return datetime.now()
