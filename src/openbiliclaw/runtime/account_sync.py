"""Periodic account-side sync for long-term Bilibili signals."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, cast

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


class SupportsAccountSyncState(Protocol):
    def load_account_sync_state(self) -> dict[str, object]: ...
    def save_account_sync_state(self, state: dict[str, object]) -> None: ...
    async def propagate_event(self, event: dict[str, Any]) -> None: ...


class SupportsAccountClient(Protocol):
    async def get_user_history(self, max_items: int = 100) -> list[dict[str, Any]]: ...
    async def get_all_favorites(
        self,
        *,
        max_folders: int = 10,
        max_items_per_folder: int = 50,
    ) -> list[Any]: ...
    async def get_following(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
    ) -> list[Any]: ...


def _client_is_authenticated(client: Any) -> bool:
    """True when the client either has no auth concept or reports authed.

    Tests pass plain stubs that don't expose ``is_authenticated``; for
    those, we conservatively assume "authenticated" so behavior matches
    pre-v0.3.57. Production ``BilibiliAPIClient`` exposes the real flag,
    which is what gates the cookie-race short-circuit.
    """
    if not hasattr(client, "is_authenticated"):
        return True
    return bool(client.is_authenticated)


class SupportsSoulAnalyzer(Protocol):
    async def analyze_events(self, events: list[dict[str, Any]]) -> None: ...


@dataclass
class AccountSyncService:
    """Incrementally import account-side history, favorites, and following."""

    memory_manager: SupportsAccountSyncState
    bilibili_client: SupportsAccountClient
    soul_engine: SupportsSoulAnalyzer
    sync_interval_hours: int = 6
    history_max_items: int = 200
    max_folders: int = 10
    max_items_per_folder: int = 50
    following_page_size: int = 100
    check_interval_seconds: int = 300
    llm_work_allowed: Callable[[], bool] | None = None
    _auto_bootstrap_attempted: bool = False
    # v0.3.57+: tracks the cookie-not-ready → ready transition so
    # ``sync_if_due`` only emits the "auth ready" INFO log once per
    # session. Reset path is via fresh AccountSyncService instance,
    # which is what ``rebuild_from_config`` already produces.
    _last_seen_authenticated: bool = False

    async def sync_if_due(self) -> dict[str, object]:
        """Run one account sync only when the configured interval has elapsed."""
        # v0.3.57+: skip the throttle check entirely while the cookie
        # hasn't arrived. ``sync_now`` will short-circuit too — checking
        # here just keeps the no-auth signal visible in run_forever logs
        # without "not_due" noise on every tick of the 5-min poll loop.
        authed = _client_is_authenticated(self.bilibili_client)
        if not authed:
            return {
                "synced": False,
                "new_event_count": 0,
                "reason": "no_auth",
            }
        if not self._last_seen_authenticated:
            self._last_seen_authenticated = True
            logger.info(
                "account_sync: bilibili cookie now ready — first history "
                "fetch will run on this tick"
            )
        if self.llm_work_allowed is not None and not self.llm_work_allowed():
            return {
                "synced": False,
                "new_event_count": 0,
                "reason": "llm_paused",
            }
        state = self.memory_manager.load_account_sync_state()
        if not self._is_due(str(state.get("last_account_sync_at", ""))):
            return {
                "synced": False,
                "new_event_count": 0,
                "reason": "not_due",
            }
        return await self.sync_now()

    async def sync_now(self) -> dict[str, object]:
        """Run one immediate incremental account sync."""
        # v0.3.57+: cookie race short-circuit. Daemon often starts before
        # the extension cookie sync arrives; without this gate, the first
        # tick fetches with empty cookies, gets 0 items, stamps
        # last_account_sync_at, and locks the next attempt out for
        # ``sync_interval_hours`` (default 6h). Bail out before touching
        # the network OR the timestamp so the next ``sync_if_due`` tick
        # (5 min) still re-tries.
        if not _client_is_authenticated(self.bilibili_client):
            return {
                "synced": False,
                "new_event_count": 0,
                "reason": "no_auth",
            }
        state = self.memory_manager.load_account_sync_state()
        events: list[dict[str, Any]] = []
        errors: list[str] = []

        try:
            history = await self.bilibili_client.get_user_history(max_items=self.history_max_items)
            previous_history_view_at = self._to_int(state.get("last_history_view_at", 0))
            previous_history_bvids = self._string_set(
                state.get("history_bvids_at_last_view_at", [])
            )
            new_history, last_view_at, last_bvid = self._filter_new_history(
                history,
                last_view_at=previous_history_view_at,
                last_bvid=str(state.get("last_history_bvid", "")),
                seen_bvids_at_last_view_at=previous_history_bvids,
            )
            events.extend(self._history_events(new_history))
            state["last_history_view_at"] = last_view_at
            state["last_history_bvid"] = last_bvid
            state["history_bvids_at_last_view_at"] = self._history_cursor_bvids(
                history,
                last_view_at,
                fallback_bvid=last_bvid,
                previous_seen=(
                    previous_history_bvids if last_view_at == previous_history_view_at else set()
                ),
            )
        except Exception as exc:
            errors.append(str(exc))

        try:
            favorites = await self.bilibili_client.get_all_favorites(
                max_folders=self.max_folders,
                max_items_per_folder=self.max_items_per_folder,
            )
            current_signature = self._favorite_signature(favorites)
            previous_signature = str(state.get("favorite_signature", ""))
            previous_bvids = self._favorite_bvids_from_state(state)
            if current_signature and current_signature != previous_signature:
                new_favorites = self._filter_favorite_folders(favorites, previous_bvids)
                events.extend(self._favorite_events(new_favorites))
                state["favorite_signature"] = current_signature
                state["favorite_bvids"] = self._favorite_bvids(favorites)
                state["last_favorites_sync_at"] = self._now().isoformat()
            elif current_signature and not state.get("favorite_bvids"):
                state["favorite_bvids"] = self._favorite_bvids(favorites)
        except Exception as exc:
            errors.append(str(exc))

        try:
            following = await self.bilibili_client.get_following(
                page=1,
                page_size=self.following_page_size,
            )
            current_signature = self._following_signature(following)
            previous_signature = str(state.get("following_signature", ""))
            previous_mids = self._following_mids_from_state(state)
            if current_signature and current_signature != previous_signature:
                new_following = self._filter_following(following, previous_mids)
                events.extend(self._following_events(new_following))
                state["following_signature"] = current_signature
                state["following_mids"] = self._following_mids(following)
                state["last_following_sync_at"] = self._now().isoformat()
            elif current_signature and not state.get("following_mids"):
                state["following_mids"] = self._following_mids(following)
        except Exception as exc:
            errors.append(str(exc))

        if events:
            for event in events:
                await self.memory_manager.propagate_event(event)
            await self.soul_engine.analyze_events(events)
            await self._auto_bootstrap_soul_profile(len(events))

        state["last_account_sync_at"] = self._now().isoformat()
        state["last_sync_error"] = " | ".join(errors)
        self.memory_manager.save_account_sync_state(state)
        return {
            "synced": bool(events),
            "new_event_count": len(events),
            "errors": errors,
        }

    def get_runtime_status(self) -> dict[str, object]:
        """Expose lightweight account sync runtime fields."""
        state = self.memory_manager.load_account_sync_state()
        return {
            "last_account_sync_at": str(state.get("last_account_sync_at", "")),
            "last_account_sync_error": str(state.get("last_sync_error", "")),
        }

    async def _auto_bootstrap_soul_profile(self, event_count: int) -> None:
        """Build the first soul profile after account sync learns preferences."""
        if self._auto_bootstrap_attempted:
            return

        is_ready_candidate = getattr(self.soul_engine, "is_profile_ready", None)
        if not callable(is_ready_candidate):
            return
        is_ready_fn = cast("Callable[[], bool]", is_ready_candidate)

        try:
            if is_ready_fn():
                return
        except Exception:
            logger.debug("Auto-bootstrap soul profile readiness check failed", exc_info=True)
            return

        build_candidate = getattr(self.soul_engine, "build_initial_profile", None)
        if not callable(build_candidate):
            self._auto_bootstrap_attempted = True
            return
        build_fn = cast("Callable[[list[dict[str, Any]]], Awaitable[Any]]", build_candidate)

        self._auto_bootstrap_attempted = True
        try:
            logger.info(
                "Auto-bootstrapping soul profile after account sync (%d new events)",
                event_count,
            )
            await build_fn([])
        except Exception:
            logger.warning(
                "Auto-bootstrap of soul profile failed; run 'openbiliclaw init' "
                "manually for a richer profile",
                exc_info=True,
            )

    # v0.3.57+: tighter retry while cookie hasn't arrived. The default
    # ``check_interval_seconds`` of 300 is right for steady-state polling
    # but stretches the cookie-race symptom — daemon up, cookie arrives
    # ~2s later, but next history fetch waits up to 5 min. Drop to 15s
    # until first auth, restore to ``check_interval_seconds`` after.
    _UNAUTH_RETRY_INTERVAL_SECONDS: ClassVar[int] = 15

    async def run_forever(self) -> None:
        """Run account sync loop until cancelled."""
        while True:
            authed_before = self._last_seen_authenticated
            try:
                await self.sync_if_due()
            except Exception:
                logger.exception("Unexpected error in account sync loop")
            interval = (
                self.check_interval_seconds
                if self._last_seen_authenticated or authed_before
                else self._UNAUTH_RETRY_INTERVAL_SECONDS
            )
            await asyncio.sleep(interval)

    def _filter_new_history(
        self,
        items: list[dict[str, Any]],
        *,
        last_view_at: int,
        last_bvid: str,
        seen_bvids_at_last_view_at: set[str] | None = None,
    ) -> tuple[list[dict[str, Any]], int, str]:
        newest_view_at = last_view_at
        newest_bvid = last_bvid
        seen_at_cursor = set(seen_bvids_at_last_view_at or set())
        if last_bvid:
            seen_at_cursor.add(last_bvid)
        accepted: list[dict[str, Any]] = []
        for item in items:
            history_meta = item.get("history", {})
            if not isinstance(history_meta, dict):
                history_meta = {}
            view_at = self._to_int(history_meta.get("view_at", item.get("view_at", 0)))
            bvid = str(history_meta.get("bvid", "")).strip()
            if view_at < last_view_at:
                continue
            if view_at == last_view_at and bvid and bvid in seen_at_cursor:
                continue
            accepted.append(item)
            if view_at > newest_view_at:
                newest_view_at = view_at
                newest_bvid = bvid
            elif view_at == newest_view_at and bvid:
                newest_bvid = bvid
        return accepted, newest_view_at, newest_bvid

    def _history_events(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for item in items:
            history_meta = item.get("history", {})
            if not isinstance(history_meta, dict):
                history_meta = {}
            bvid = str(history_meta.get("bvid", "")).strip()
            events.append(
                {
                    "event_type": "view",
                    "title": str(item.get("title", "")).strip(),
                    "url": f"https://www.bilibili.com/video/{bvid}" if bvid else "",
                    "metadata": {
                        "bvid": bvid,
                        "author": str(item.get("author", "")).strip(),
                        "view_at": self._to_int(
                            history_meta.get("view_at", item.get("view_at", 0))
                        ),
                        "source": "account_sync",
                    },
                }
            )
        return events

    def _history_cursor_bvids(
        self,
        items: list[dict[str, Any]],
        view_at: int,
        *,
        fallback_bvid: str = "",
        previous_seen: set[str] | None = None,
    ) -> list[str]:
        bvids: set[str] = set()
        if view_at > 0:
            for item in items:
                history_meta = item.get("history", {})
                if not isinstance(history_meta, dict):
                    history_meta = {}
                item_view_at = self._to_int(history_meta.get("view_at", item.get("view_at", 0)))
                if item_view_at != view_at:
                    continue
                bvid = str(history_meta.get("bvid", "")).strip()
                if bvid:
                    bvids.add(bvid)
        if previous_seen:
            bvids.update(str(item).strip() for item in previous_seen if str(item).strip())
        if fallback_bvid:
            bvids.add(fallback_bvid)
        return sorted(bvids)

    def _favorite_signature(self, folders: list[Any]) -> str:
        parts: list[str] = []
        for folder in folders:
            folder_id = str(getattr(getattr(folder, "folder", None), "media_id", ""))
            item_ids = sorted(
                str(item.get("bvid", "")).strip()
                for item in getattr(folder, "items", [])
                if isinstance(item, dict) and str(item.get("bvid", "")).strip()
            )
            if folder_id and item_ids:
                parts.append(f"{folder_id}:{','.join(item_ids)}")
        return "|".join(sorted(parts))

    def _favorite_bvids(self, folders: list[Any]) -> list[str]:
        bvids = {
            str(item.get("bvid", "")).strip()
            for folder in folders
            for item in getattr(folder, "items", [])
            if isinstance(item, dict) and str(item.get("bvid", "")).strip()
        }
        return sorted(bvids)

    def _favorite_bvids_from_state(self, state: dict[str, object]) -> set[str]:
        stored = self._string_set(state.get("favorite_bvids", []))
        if stored:
            return stored
        return self._bvids_from_signature(str(state.get("favorite_signature", "")))

    def _filter_favorite_folders(self, folders: list[Any], seen_bvids: set[str]) -> list[Any]:
        if not seen_bvids:
            return folders
        filtered: list[Any] = []
        for folder in folders:
            items = [
                item
                for item in getattr(folder, "items", [])
                if isinstance(item, dict)
                and str(item.get("bvid", "")).strip()
                and str(item.get("bvid", "")).strip() not in seen_bvids
            ]
            if not items:
                continue
            filtered.append(
                SimpleNamespace(
                    folder=getattr(folder, "folder", None),
                    items=items,
                    truncated=bool(getattr(folder, "truncated", False)),
                )
            )
        return filtered

    def _favorite_events(self, folders: list[Any]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for folder in folders:
            folder_obj = getattr(folder, "folder", None)
            folder_title = str(getattr(folder_obj, "title", "")).strip()
            folder_id = int(getattr(folder_obj, "media_id", 0) or 0)
            for item in getattr(folder, "items", []):
                if not isinstance(item, dict):
                    continue
                bvid = str(item.get("bvid", "")).strip()
                upper = item.get("upper", {})
                if not isinstance(upper, dict):
                    upper = {}
                events.append(
                    {
                        "event_type": "favorite",
                        "title": str(item.get("title", "")).strip(),
                        "url": f"https://www.bilibili.com/video/{bvid}" if bvid else "",
                        "metadata": {
                            "bvid": bvid,
                            "folder_id": folder_id,
                            "folder_title": folder_title,
                            "up_name": str(upper.get("name", "")).strip(),
                            "source": "account_sync",
                        },
                    }
                )
        return events

    def _following_signature(self, following: list[Any]) -> str:
        return ",".join(self._following_mids(following))

    def _following_mids(self, following: list[Any]) -> list[str]:
        mids = {
            str(getattr(user, "mid", "")).strip()
            for user in following
            if str(getattr(user, "mid", "")).strip()
        }
        return sorted(mids)

    def _following_mids_from_state(self, state: dict[str, object]) -> set[str]:
        stored = self._string_set(state.get("following_mids", []))
        if stored:
            return stored
        return {
            item.strip()
            for item in str(state.get("following_signature", "")).split(",")
            if item.strip()
        }

    def _filter_following(self, following: list[Any], seen_mids: set[str]) -> list[Any]:
        if not seen_mids:
            return following
        return [
            user
            for user in following
            if str(getattr(user, "mid", "")).strip()
            and str(getattr(user, "mid", "")).strip() not in seen_mids
        ]

    def _following_events(self, following: list[Any]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for user in following:
            mid = int(getattr(user, "mid", 0) or 0)
            uname = str(getattr(user, "uname", "")).strip()
            events.append(
                {
                    "event_type": "follow",
                    "title": uname,
                    "url": f"https://space.bilibili.com/{mid}" if mid else "",
                    "metadata": {
                        "up_mid": mid,
                        "up_name": uname,
                        "sign": str(getattr(user, "sign", "")).strip(),
                        "source": "account_sync",
                    },
                }
            )
        return events

    def _is_due(self, last_sync_at: str) -> bool:
        parsed = self._parse_iso_datetime(last_sync_at)
        if parsed is None:
            return True
        return self._now() - parsed >= timedelta(hours=self.sync_interval_hours)

    def _parse_iso_datetime(self, value: str) -> datetime | None:
        if not value:
            return None
        with_timezone = value.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(with_timezone)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _now(self) -> datetime:
        return datetime.now(tz=UTC)

    @staticmethod
    def _to_int(value: object) -> int:
        if value is None:
            return 0
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                return 0
        return 0

    @staticmethod
    def _string_set(value: object) -> set[str]:
        if not isinstance(value, list):
            return set()
        return {str(item).strip() for item in value if str(item).strip()}

    @staticmethod
    def _bvids_from_signature(signature: str) -> set[str]:
        bvids: set[str] = set()
        for folder_part in signature.split("|"):
            _, sep, item_part = folder_part.partition(":")
            if not sep:
                continue
            bvids.update(item.strip() for item in item_part.split(",") if item.strip())
        return bvids
