"""Account sync must record the real moment, not the import moment.

The ledger's ``created_at`` is written by the database at insert time, so a
page of 50 rows shares one second. Bilibili already tells us when a follow /
view / favourite happened (``mtime`` / ``view_at`` / ``fav_time``); these tests
pin that those values reach ``metadata.timestamp`` and that a first import is
flagged as an inventory snapshot instead of a burst of behaviour.
"""

from __future__ import annotations

from typing import Any

import pytest

from openbiliclaw.bilibili.api import FollowingUser
from openbiliclaw.runtime.account_sync import AccountSyncService
from tests.test_account_sync import (
    _FakeClient,
    _FakeMemoryManager,
    _FakeSoulEngine,
    _favorite_folder_with_items,
    _history_item,
)


def _service(**kwargs: Any) -> AccountSyncService:
    return AccountSyncService(
        memory_manager=kwargs.pop("memory_manager", _FakeMemoryManager()),
        bilibili_client=kwargs.pop("bilibili_client", _FakeClient([], [], [])),
        soul_engine=kwargs.pop("soul_engine", _FakeSoulEngine()),
        **kwargs,
    )


def test_follow_event_carries_bilibili_mtime_as_event_time() -> None:
    service = _service()

    [event] = service._following_events([FollowingUser(mid=7, uname="某UP", mtime=1_584_271_945)])

    assert event["metadata"]["timestamp"] == 1_584_271_945
    assert event["metadata"]["mtime"] == 1_584_271_945


def test_follow_without_mtime_never_invents_a_time() -> None:
    """Upstream omission must stay "unknown" — an invented time is worse."""
    service = _service()

    [event] = service._following_events([FollowingUser(mid=7, uname="某UP")])

    assert "timestamp" not in event["metadata"]
    assert "mtime" not in event["metadata"]


def test_history_event_mirrors_view_at_into_canonical_timestamp() -> None:
    service = _service()

    [event] = service._history_events([_history_item("BV1", 1_785_532_358)])

    assert event["metadata"]["timestamp"] == 1_785_532_358
    assert event["metadata"]["view_at"] == 1_785_532_358


def test_favorite_event_mirrors_fav_time_into_canonical_timestamp() -> None:
    service = _service()
    folder = _favorite_folder_with_items(1, "BVF1")
    folder.items[0]["fav_time"] = 1_700_000_000

    [event] = service._favorite_events([folder])

    assert event["metadata"]["timestamp"] == 1_700_000_000
    assert event["metadata"]["fav_time"] == 1_700_000_000


@pytest.mark.asyncio
async def test_first_following_sync_is_marked_as_snapshot() -> None:
    memory = _FakeMemoryManager()
    client = _FakeClient(
        history_items=[],
        favorites=[],
        following=[
            FollowingUser(mid=i, uname=f"up-{i}", mtime=1_600_000_000 + i) for i in range(1, 41)
        ],
    )
    service = _service(memory_manager=memory, bilibili_client=client)

    await service.sync_now()

    follow_events = [event for event in memory.events if event["event_type"] == "follow"]
    assert len(follow_events) == 40
    assert all(event["metadata"].get("snapshot") is True for event in follow_events)
    # Real follow times are months apart, not the import second.
    assert len({event["metadata"]["timestamp"] for event in follow_events}) == 40


@pytest.mark.asyncio
async def test_steady_state_following_sync_is_not_a_snapshot() -> None:
    memory = _FakeMemoryManager(
        {
            "last_history_view_at": 0,
            "last_history_bvid": "",
            "last_favorites_sync_at": "2026-01-01T00:00:00+00:00",
            "favorite_signature": "",
            "last_following_sync_at": "2026-01-01T00:00:00+00:00",
            "following_signature": "1",
            "following_mids": ["1"],
            "last_account_sync_at": "",
            "last_sync_error": "",
        }
    )
    client = _FakeClient(
        history_items=[],
        favorites=[],
        following=[
            FollowingUser(mid=1, uname="old", mtime=1_600_000_000),
            FollowingUser(mid=2, uname="just-followed", mtime=1_790_000_000),
        ],
    )
    service = _service(memory_manager=memory, bilibili_client=client)

    await service.sync_now()

    follow_events = [event for event in memory.events if event["event_type"] == "follow"]
    assert [event["title"] for event in follow_events] == ["just-followed"]
    assert "snapshot" not in follow_events[0]["metadata"]
    assert follow_events[0]["metadata"]["timestamp"] == 1_790_000_000
