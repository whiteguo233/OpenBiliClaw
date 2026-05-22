from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from openbiliclaw.bilibili.api import FavoriteFolder, FavoriteFolderWithItems, FollowingUser


class _FakeMemoryManager:
    def __init__(self, state: dict[str, object] | None = None) -> None:
        self.state = state or {
            "last_history_view_at": 0,
            "last_history_bvid": "",
            "last_favorites_sync_at": "",
            "favorite_signature": "",
            "last_following_sync_at": "",
            "following_signature": "",
            "last_account_sync_at": "",
            "last_sync_error": "",
        }
        self.events: list[dict[str, Any]] = []

    def load_account_sync_state(self) -> dict[str, object]:
        return dict(self.state)

    def save_account_sync_state(self, state: dict[str, object]) -> None:
        self.state = dict(state)

    async def propagate_event(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class _FakeSoulEngine:
    def __init__(self) -> None:
        self.calls: list[list[dict[str, Any]]] = []

    async def analyze_events(self, events: list[dict[str, Any]]) -> None:
        self.calls.append(events)


class _BootstrapSoulEngine(_FakeSoulEngine):
    def __init__(self, *, ready: bool = False, fail_bootstrap: bool = False) -> None:
        super().__init__()
        self.ready = ready
        self.fail_bootstrap = fail_bootstrap
        self.ready_checks = 0
        self.bootstrap_calls: list[list[dict[str, Any]]] = []

    def is_profile_ready(self) -> bool:
        self.ready_checks += 1
        return self.ready

    async def build_initial_profile(self, history: list[dict[str, Any]]) -> object:
        self.bootstrap_calls.append(history)
        if self.fail_bootstrap:
            raise RuntimeError("bootstrap boom")
        self.ready = True
        return object()


@dataclass
class _FakeClient:
    history_items: list[dict[str, Any]]
    favorites: list[FavoriteFolderWithItems]
    following: list[FollowingUser]
    fail_history: bool = False
    fail_favorites: bool = False
    fail_following: bool = False

    async def get_user_history(self, max_items: int = 100) -> list[dict[str, Any]]:
        if self.fail_history:
            raise RuntimeError("history boom")
        return self.history_items[:max_items]

    async def get_all_favorites(
        self,
        *,
        max_folders: int = 10,
        max_items_per_folder: int = 50,
    ) -> list[FavoriteFolderWithItems]:
        if self.fail_favorites:
            raise RuntimeError("favorites boom")
        return self.favorites[:max_folders]

    async def get_following(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
    ) -> list[FollowingUser]:
        if self.fail_following:
            raise RuntimeError("following boom")
        return self.following[:page_size]


def _history_item(bvid: str, view_at: int, title: str = "视频") -> dict[str, Any]:
    return {
        "title": title,
        "author": "UP主",
        "history": {
            "bvid": bvid,
            "view_at": view_at,
        },
    }


def _favorite_item(bvid: str, title: str = "收藏视频") -> dict[str, Any]:
    return {
        "bvid": bvid,
        "title": title,
        "upper": {"name": "收藏UP"},
    }


def _favorite_folder_with_items(folder_id: int, *bvids: str) -> FavoriteFolderWithItems:
    return FavoriteFolderWithItems(
        folder=FavoriteFolder(
            media_id=folder_id,
            title=f"folder-{folder_id}",
            media_count=len(bvids),
        ),
        items=[_favorite_item(bvid) for bvid in bvids],
        truncated=False,
    )


@pytest.mark.asyncio
async def test_account_sync_imports_incremental_history_only() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _FakeMemoryManager(
        {
            "last_history_view_at": 100,
            "last_history_bvid": "BVOLD",
            "last_favorites_sync_at": "",
            "favorite_signature": "",
            "last_following_sync_at": "",
            "following_signature": "",
            "last_account_sync_at": "",
            "last_sync_error": "",
        }
    )
    soul = _FakeSoulEngine()
    client = _FakeClient(
        history_items=[
            _history_item("BVNEW2", 102, "更近的新视频"),
            _history_item("BVNEW1", 101, "新的视频"),
            _history_item("BVOLD", 100, "旧视频"),
        ],
        favorites=[],
        following=[],
    )

    service = AccountSyncService(memory_manager=memory, bilibili_client=client, soul_engine=soul)

    result = await service.sync_now()

    assert result["synced"] is True
    assert result["new_event_count"] == 2
    assert [event["metadata"]["bvid"] for event in memory.events] == ["BVNEW2", "BVNEW1"]
    assert soul.calls and len(soul.calls[0]) == 2
    assert memory.state["last_history_view_at"] == 102
    assert memory.state["last_history_bvid"] == "BVNEW2"


@pytest.mark.asyncio
async def test_account_sync_does_not_reimport_same_timestamp_history() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _FakeMemoryManager(
        {
            "last_history_view_at": 100,
            "last_history_bvid": "BVOLD2",
            "history_bvids_at_last_view_at": ["BVOLD1", "BVOLD2"],
            "last_favorites_sync_at": "",
            "favorite_signature": "",
            "last_following_sync_at": "",
            "following_signature": "",
            "last_account_sync_at": "",
            "last_sync_error": "",
        }
    )
    soul = _FakeSoulEngine()
    client = _FakeClient(
        history_items=[
            _history_item("BVOLD1", 100, "同秒旧视频 1"),
            _history_item("BVOLD2", 100, "同秒旧视频 2"),
            _history_item("BVOLDER", 99, "更早旧视频"),
        ],
        favorites=[],
        following=[],
    )

    service = AccountSyncService(memory_manager=memory, bilibili_client=client, soul_engine=soul)

    result = await service.sync_now()

    assert result["synced"] is False
    assert result["new_event_count"] == 0
    assert memory.events == []
    assert soul.calls == []
    assert memory.state["last_history_view_at"] == 100
    assert memory.state["history_bvids_at_last_view_at"] == ["BVOLD1", "BVOLD2"]


@pytest.mark.asyncio
async def test_account_sync_skips_favorites_and_following_when_signature_unchanged() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    favorites = [_favorite_folder_with_items(1, "BVF1", "BVF2")]
    following = [FollowingUser(mid=1, uname="影视飓风"), FollowingUser(mid=2, uname="何同学")]
    service = AccountSyncService(
        memory_manager=_FakeMemoryManager(
            {
                "last_history_view_at": 0,
                "last_history_bvid": "",
                "last_favorites_sync_at": "2026-03-14T12:00:00",
                "favorite_signature": "1:BVF1,BVF2",
                "last_following_sync_at": "2026-03-14T12:00:00",
                "following_signature": "1,2",
                "last_account_sync_at": "2026-03-14T12:00:00",
                "last_sync_error": "",
            }
        ),
        bilibili_client=_FakeClient(history_items=[], favorites=favorites, following=following),
        soul_engine=_FakeSoulEngine(),
    )

    result = await service.sync_now()

    assert result["synced"] is False
    assert result["new_event_count"] == 0
    assert service.memory_manager.events == []
    assert service.soul_engine.calls == []


@pytest.mark.asyncio
async def test_account_sync_imports_only_new_favorites_when_signature_changes() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _FakeMemoryManager(
        {
            "last_history_view_at": 0,
            "last_history_bvid": "",
            "last_favorites_sync_at": "2026-03-14T12:00:00",
            "favorite_signature": "7:BVOLD",
            "favorite_bvids": ["BVOLD"],
            "last_following_sync_at": "",
            "following_signature": "",
            "last_account_sync_at": "",
            "last_sync_error": "",
        }
    )
    soul = _FakeSoulEngine()
    client = _FakeClient(
        history_items=[],
        favorites=[_favorite_folder_with_items(7, "BVNEW", "BVOLD")],
        following=[],
    )

    service = AccountSyncService(memory_manager=memory, bilibili_client=client, soul_engine=soul)

    result = await service.sync_now()

    assert result["synced"] is True
    assert result["new_event_count"] == 1
    assert [event["metadata"]["bvid"] for event in memory.events] == ["BVNEW"]
    assert soul.calls and len(soul.calls[0]) == 1
    assert memory.state["favorite_bvids"] == ["BVNEW", "BVOLD"]
    assert memory.state["favorite_signature"] == "7:BVNEW,BVOLD"


@pytest.mark.asyncio
async def test_account_sync_imports_new_favorites_and_following() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _FakeMemoryManager()
    soul = _FakeSoulEngine()
    client = _FakeClient(
        history_items=[],
        favorites=[_favorite_folder_with_items(7, "BVFRESH")],
        following=[FollowingUser(mid=99, uname="半佛仙人")],
    )

    service = AccountSyncService(memory_manager=memory, bilibili_client=client, soul_engine=soul)

    result = await service.sync_now()

    assert result["new_event_count"] == 2
    assert {event["event_type"] for event in memory.events} == {"favorite", "follow"}
    assert memory.state["favorite_signature"] == "7:BVFRESH"
    assert memory.state["following_signature"] == "99"


@pytest.mark.asyncio
async def test_account_sync_auto_bootstraps_empty_soul_profile_after_events() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _FakeMemoryManager()
    soul = _BootstrapSoulEngine(ready=False)
    client = _FakeClient(
        history_items=[_history_item("BVPROFILE", 101, "profile seed")],
        favorites=[],
        following=[],
    )
    service = AccountSyncService(memory_manager=memory, bilibili_client=client, soul_engine=soul)

    result = await service.sync_now()

    assert result["synced"] is True
    assert soul.calls and len(soul.calls[0]) == 1
    assert soul.bootstrap_calls == [[]]
    assert soul.ready_checks == 1


@pytest.mark.asyncio
async def test_account_sync_auto_bootstrap_attempts_only_once_after_failure() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _FakeMemoryManager()
    soul = _BootstrapSoulEngine(ready=False, fail_bootstrap=True)
    client = _FakeClient(
        history_items=[_history_item("BVFIRST", 101, "first")],
        favorites=[],
        following=[],
    )
    service = AccountSyncService(memory_manager=memory, bilibili_client=client, soul_engine=soul)

    first_result = await service.sync_now()
    client.history_items = [_history_item("BVSECOND", 102, "second")]
    second_result = await service.sync_now()

    assert first_result["synced"] is True
    assert second_result["synced"] is True
    assert len(soul.calls) == 2
    assert soul.bootstrap_calls == [[]]
    assert soul.ready_checks == 1


@pytest.mark.asyncio
async def test_account_sync_returns_partial_success_when_one_source_fails() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _FakeMemoryManager()
    soul = _FakeSoulEngine()
    client = _FakeClient(
        history_items=[_history_item("BVOK", 101)],
        favorites=[],
        following=[FollowingUser(mid=7, uname="小约翰可汗")],
        fail_favorites=True,
    )

    service = AccountSyncService(memory_manager=memory, bilibili_client=client, soul_engine=soul)

    result = await service.sync_now()

    assert result["synced"] is True
    assert result["new_event_count"] == 2
    assert "favorites boom" in str(memory.state["last_sync_error"])
    assert {event["event_type"] for event in memory.events} == {"view", "follow"}


@dataclass
class _CookieAwareClient:
    """Client whose ``is_authenticated`` flips False→True after one tick.

    Models the production race where the daemon starts, the cookie
    arrives ~2s later via the extension push, and account_sync ticks
    in between fire ``get_user_history`` against an empty cookie.
    """

    history_items: list[dict[str, Any]]
    is_authenticated: bool = False
    history_calls: int = 0

    async def get_user_history(self, max_items: int = 100) -> list[dict[str, Any]]:
        self.history_calls += 1
        if not self.is_authenticated:
            return []
        return self.history_items[:max_items]

    async def get_all_favorites(
        self,
        *,
        max_folders: int = 10,
        max_items_per_folder: int = 50,
    ) -> list[FavoriteFolderWithItems]:
        return []

    async def get_following(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
    ) -> list[FollowingUser]:
        return []


@pytest.mark.asyncio
async def test_account_sync_skips_when_unauthenticated_without_burning_throttle() -> None:
    """v0.3.57+: when the bilibili client has no cookie yet (extension
    hasn't synced), sync_now must short-circuit WITHOUT stamping
    ``last_account_sync_at``. Otherwise the 6-hour interval would lock
    the next attempt out and history wouldn't get fetched until then.

    Reproduces the 2026-05-05 production gap: cookie arrived at 03:33:27
    but the first successful history fetch was 03:40:22 — 7 minutes
    later — because account_sync's first tick happened at 03:33:25 with
    an empty cookie, stamped the timestamp, and went silent.
    """
    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _FakeMemoryManager()
    client = _CookieAwareClient(
        history_items=[_history_item("BVAFTER", 200, "after cookie")],
        is_authenticated=False,
    )
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=client,
        soul_engine=_FakeSoulEngine(),
    )

    # 1st tick — no cookie yet.
    result = await service.sync_now()
    assert result == {
        "synced": False,
        "new_event_count": 0,
        "reason": "no_auth",
    }
    assert client.history_calls == 0  # Short-circuited before fetch.
    # Crucial: timestamp NOT stamped, so sync_if_due will try again.
    assert not memory.state.get("last_account_sync_at")

    # Cookie arrives.
    client.is_authenticated = True

    # 2nd tick (via sync_if_due, which is what run_forever calls) — fires.
    result = await service.sync_if_due()
    assert result["synced"] is True
    assert result["new_event_count"] == 1
    assert client.history_calls == 1
    # Now the timestamp gets stamped.
    assert memory.state.get("last_account_sync_at")


@pytest.mark.asyncio
async def test_account_sync_if_due_skips_without_fetching_when_llm_work_paused() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _FakeMemoryManager()
    client = _CookieAwareClient(
        history_items=[_history_item("BVPAUSED", 200, "paused")],
        is_authenticated=True,
    )
    soul = _FakeSoulEngine()
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=client,
        soul_engine=soul,
        llm_work_allowed=lambda: False,
    )

    result = await service.sync_if_due()

    assert result == {
        "synced": False,
        "new_event_count": 0,
        "reason": "llm_paused",
    }
    assert client.history_calls == 0
    assert soul.calls == []
    assert memory.events == []
    assert not memory.state.get("last_account_sync_at")


@pytest.mark.asyncio
async def test_account_sync_uses_short_retry_interval_until_first_fetch_succeeds() -> None:
    """v0.3.57+: until the first authenticated history fetch lands,
    the per-tick due-check should not be gated by the 6-hour interval.
    ``run_forever``'s 5-min ``check_interval_seconds`` becomes the de
    facto retry budget — way better than the 6h-after-stamped baseline.
    """
    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _FakeMemoryManager()
    client = _CookieAwareClient(history_items=[], is_authenticated=False)
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=client,
        soul_engine=_FakeSoulEngine(),
    )

    # 5 sequential sync_if_due ticks with no auth. None should burn
    # the throttle — every one stays ready to retry.
    for _ in range(5):
        result = await service.sync_if_due()
        assert result.get("reason") == "no_auth"
    assert client.history_calls == 0
    assert not memory.state.get("last_account_sync_at")


@pytest.mark.asyncio
async def test_account_sync_run_forever_recovers_from_iteration_error(caplog) -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    service = AccountSyncService(
        memory_manager=_FakeMemoryManager(),
        bilibili_client=_FakeClient(history_items=[], favorites=[], following=[]),
        soul_engine=_FakeSoulEngine(),
        check_interval_seconds=1,
    )

    async def _broken_sync_if_due() -> dict[str, object]:
        raise RuntimeError("boom")

    async def _cancel_sleep(_: int) -> None:
        raise asyncio.CancelledError

    service.sync_if_due = _broken_sync_if_due  # type: ignore[method-assign]

    original_sleep = asyncio.sleep
    try:
        asyncio.sleep = _cancel_sleep
        with pytest.raises(asyncio.CancelledError):
            await service.run_forever()
    finally:
        asyncio.sleep = original_sleep

    assert "Unexpected error in account sync loop" in caplog.text
