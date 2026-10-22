from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest

from openbiliclaw.bilibili.api import (
    BilibiliAPIError,
    FavoriteFolder,
    FavoriteFolderWithItems,
    FollowingUser,
)
from openbiliclaw.llm.base import LLMProviderError, LLMResponseError


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
        max_total_items: int | None = None,
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


def test_account_sync_drops_dead_favorites_and_keeps_reach() -> None:
    """失效视频不是信号；播放量 / 发布时间是判断冷门偏好的依据，别丢。"""
    from openbiliclaw.runtime.account_sync import AccountSyncService

    service = AccountSyncService(
        memory_manager=_FakeMemoryManager(),
        bilibili_client=_FakeClient(history_items=[], favorites=[], following=[]),
        soul_engine=_FakeSoulEngine(),
    )
    folder = _favorite_folder_with_items(1, "BVALIVE")
    folder.items[0].update({"cnt_info": {"play": 812}, "pubtime": 1_780_000_000, "duration": 300})
    folder.items.append({"bvid": "BVDEAD", "title": "已失效视频", "attr": 1, "upper": {}})

    events = service._favorite_events([folder])

    assert [event["metadata"]["bvid"] for event in events] == ["BVALIVE"]
    assert events[0]["metadata"]["play_count"] == 812
    assert events[0]["metadata"]["pubtime"] == 1_780_000_000
    assert events[0]["metadata"]["video_duration_seconds"] == 300


def test_account_sync_event_builders_include_signal_strength() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    service = AccountSyncService(
        memory_manager=_FakeMemoryManager(),
        bilibili_client=_FakeClient(history_items=[], favorites=[], following=[]),
        soul_engine=_FakeSoulEngine(),
    )

    history_event = service._history_events([_history_item("BV1", 100)])[0]
    favorite_event = service._favorite_events([_favorite_folder_with_items(1, "BVF1")])[0]
    follow_event = service._following_events([FollowingUser(mid=1, uname="某 UP")])[0]

    assert history_event["metadata"]["signal_strength"] == 0.35
    assert favorite_event["metadata"]["signal_strength"] == 1.0
    assert follow_event["metadata"]["signal_strength"] == 0.6


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


class _SeenLedgerSpy:
    """Minimal Database stand-in for the seen-ledger contract."""

    def __init__(self) -> None:
        self.marked: list[tuple[str, list[str]]] = []
        self.seen: set[str] = set()

    def recent_event_urls(self, event_types: list[str], **kwargs: Any) -> set[str]:
        return set()

    def mark_items_seen(self, source_platform: str, content_ids: Any) -> int:
        ids = [str(value) for value in content_ids]
        self.marked.append((source_platform, ids))
        added = [item for item in ids if item not in self.seen]
        self.seen.update(added)
        return len(added)


@pytest.mark.asyncio
async def test_account_sync_marks_the_whole_favorites_snapshot_as_seen() -> None:
    """收藏夹里的旧内容不会再变成事件，只能靠快照进去重账本。

    只有「新增」收藏才产出事件，所以装 OpenBiliClaw 之前收藏的、以及旧版本收藏
    事件没带身份的那些，永远不会进 seen_items——用户明确存过的视频会被当新内容推回。
    """
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
    database = _SeenLedgerSpy()
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=_FakeClient(
            history_items=[],
            favorites=[_favorite_folder_with_items(7, "BVNEW", "BVOLD")],
            following=[],
        ),
        soul_engine=_FakeSoulEngine(),
        database=database,
    )

    await service.sync_now()

    assert database.marked, "每轮同步都要用完整收藏快照回补去重账本"
    platform, marked = database.marked[0]
    assert platform == "bilibili"
    assert sorted(marked) == ["BVNEW", "BVOLD"], "旧收藏不产出事件，只能靠快照补进去"
    # 事件仍然只认新增的那条，快照标记不会重复计入偏好信号。
    assert [event["metadata"]["bvid"] for event in memory.events] == ["BVNEW"]


@pytest.mark.asyncio
async def test_account_sync_survives_a_seen_ledger_failure() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    class _BrokenLedger(_SeenLedgerSpy):
        def mark_items_seen(self, source_platform: str, content_ids: Any) -> int:
            raise RuntimeError("ledger down")

    service = AccountSyncService(
        memory_manager=_FakeMemoryManager(),
        bilibili_client=_FakeClient(
            history_items=[],
            favorites=[_favorite_folder_with_items(7, "BVNEW")],
            following=[],
        ),
        soul_engine=_FakeSoulEngine(),
        database=_BrokenLedger(),
    )

    result = await service.sync_now()

    assert result["synced"] is True, "去重账本写失败不该拖垮整轮同步"


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
    # Phase 3: readiness is probed once per sync now (routing pipeline vs legacy),
    # but the bootstrap itself is still attempted at most once (bootstrap_calls).
    assert soul.ready_checks == 2


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
    assert memory.state["last_sync_issues"] == [
        {"stage": "bilibili_favorites", "kind": "unexpected_error"}
    ]
    status = service.get_runtime_status()
    assert "B 站收藏夹未同步" in str(status["last_account_sync_message"])
    assert "未分类异常" in str(status["last_account_sync_message"])
    assert "已成功的环节已保留" in str(status["last_account_sync_message"])
    assert {event["event_type"] for event in memory.events} == {"view", "follow"}


@pytest.mark.asyncio
async def test_account_sync_records_profile_analysis_error_without_advancing_cursor() -> None:
    """A chat-LLM failure during analyze_events must be diagnosable, not silent.

    Regression for the guided-init "stuck forever" report: analyze_events was
    a bare await, so a 404/401/timeout bubbled up with nothing written to the
    user-visible last_sync_error, and the whole tick was lost with no reason.
    """
    from openbiliclaw.runtime.account_sync import AccountSyncService

    class _FailingAnalyzeSoul(_BootstrapSoulEngine):
        async def analyze_events(self, events: list[dict[str, Any]]) -> None:
            self.calls.append(events)
            raise RuntimeError("model 'llama3' not found")

    memory = _FakeMemoryManager()
    soul = _FailingAnalyzeSoul(ready=False)
    client = _FakeClient(
        history_items=[_history_item("BVERR", 101, "seed")],
        favorites=[],
        following=[],
    )
    service = AccountSyncService(memory_manager=memory, bilibili_client=client, soul_engine=soul)

    with pytest.raises(RuntimeError):
        await service.sync_now()

    # Failure reason is now user-visible.
    assert "画像分析失败" in str(memory.state["last_sync_error"])
    assert "找不到所配置的模型" in str(memory.state["last_sync_error"])
    assert "ollama pull" in str(memory.state["last_sync_error"])
    # The LLM-unavailability classification rides along so the status surface
    # can render actionable copy instead of a false "稍后会自动重试" promise.
    assert memory.state["last_sync_error_kind"] == "model_not_found"
    # Cursor / throttle are NOT advanced → next tick retries the same events.
    assert memory.state["last_history_view_at"] == 0
    assert memory.state["last_account_sync_at"] == ""
    # The one-shot bootstrap chance is not burned on an unavailable model.
    # (_apply_profile_update probes readiness once up front for routing, but
    # the failed analyze means _auto_bootstrap_soul_profile never runs.)
    assert soul.bootstrap_calls == []
    assert soul.ready_checks == 1


@pytest.mark.asyncio
async def test_account_sync_persists_no_provider_kind_on_profile_analysis() -> None:
    """An empty LLM registry surfaces as no_provider, not a generic error."""
    from openbiliclaw.llm.base import LLMFallbackError
    from openbiliclaw.runtime.account_sync import AccountSyncService

    class _NoProviderSoul(_BootstrapSoulEngine):
        async def analyze_events(self, events: list[dict[str, Any]]) -> None:
            self.calls.append(events)
            raise LLMFallbackError("No provider was available to process the request.")

    memory = _FakeMemoryManager()
    soul = _NoProviderSoul(ready=False)
    client = _FakeClient(
        history_items=[_history_item("BVNOLLM", 101, "seed")],
        favorites=[],
        following=[],
    )
    service = AccountSyncService(memory_manager=memory, bilibili_client=client, soul_engine=soul)

    with pytest.raises(LLMFallbackError):
        await service.sync_now()

    assert memory.state["last_sync_error_kind"] == "no_provider"
    assert "画像分析失败" in str(memory.state["last_sync_error"])
    status = service.get_runtime_status()
    assert "设置" in str(status["last_account_sync_message"])
    assert "稍后会自动重试" not in str(status["last_account_sync_message"])


@pytest.mark.asyncio
async def test_account_sync_bounds_hung_profile_analysis_and_records_reason() -> None:
    """A provider's 300s retries must not hold account sync indefinitely."""
    from openbiliclaw.runtime.account_sync import AccountSyncService

    class _HangingAnalyzeSoul(_BootstrapSoulEngine):
        def __init__(self) -> None:
            super().__init__(ready=False)
            self.cancelled = False

        async def analyze_events(self, events: list[dict[str, Any]]) -> None:
            self.calls.append(events)
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled = True

    memory = _FakeMemoryManager()
    soul = _HangingAnalyzeSoul()
    client = _FakeClient(
        history_items=[_history_item("BVTIMEOUT", 101, "seed")],
        favorites=[],
        following=[],
    )
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=client,
        soul_engine=soul,
        profile_analysis_timeout_seconds=0.01,
    )

    with pytest.raises(TimeoutError, match="偏好分析等待模型服务超过 6 分钟"):
        await service.sync_now()

    assert soul.cancelled is True
    assert "画像分析失败" in str(memory.state["last_sync_error"])
    assert "超过 6 分钟" in str(memory.state["last_sync_error"])
    assert "Base URL" in str(memory.state["last_sync_error"])
    assert "模型名" in str(memory.state["last_sync_error"])
    assert memory.state["last_history_view_at"] == 0
    assert memory.state["last_account_sync_at"] == ""
    assert memory.state["last_sync_error_kind"] == "profile_analysis_timeout"
    assert memory.state["last_sync_issues"] == [{"stage": "profile_analysis", "kind": "timeout"}]
    status = service.get_runtime_status()
    assert "AI 画像分析超过 6 分钟" in str(status["last_account_sync_message"])
    assert "模型设置" in str(status["last_account_sync_message"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc", "status_kind", "issue_kind", "message_fragment"),
    [
        (
            RuntimeError("401 Unauthorized"),
            "llm_auth_failed",
            "auth_failed",
            "AI 服务鉴权失败",
        ),
        (
            RuntimeError("connection refused"),
            "llm_connection",
            "connection",
            "无法连接 AI 服务",
        ),
        (
            LLMProviderError("upstream error: insufficient_quota"),
            "llm_quota_exhausted",
            "quota_exhausted",
            "额度不足或已用尽",
        ),
        (
            LLMProviderError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"),
            "llm_ssl",
            "ssl",
            "SSL 证书验证失败",
        ),
        (
            RuntimeError("HTTP 503"),
            "llm_server_error",
            "server_error",
            "AI 服务返回服务器错误",
        ),
        (
            LLMResponseError("empty response"),
            "llm_invalid_response",
            "invalid_response",
            "空内容或无法解析",
        ),
        (
            LLMProviderError(
                "Error code: 500 - 根据相关法律法规，无法提供关于该内容的答案 (10013)"
            ),
            "llm_moderation",
            "moderation",
            "内容合规策略拒绝",
        ),
        (
            RuntimeError("opaque provider failure"),
            "profile_analysis_error",
            "unexpected_error",
            "画像分析遇到未分类异常",
        ),
    ],
)
async def test_account_sync_classifies_profile_analysis_failure(
    exc: Exception,
    status_kind: str,
    issue_kind: str,
    message_fragment: str,
) -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    class _FailingAnalyzeSoul(_BootstrapSoulEngine):
        async def analyze_events(self, events: list[dict[str, Any]]) -> None:
            self.calls.append(events)
            raise exc

    memory = _FakeMemoryManager()
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=_FakeClient(
            history_items=[_history_item("BVPROFILEFAIL", 101)],
            favorites=[],
            following=[],
        ),
        soul_engine=_FailingAnalyzeSoul(ready=False),
    )

    with pytest.raises(type(exc)):
        await service.sync_now()

    status = service.get_runtime_status()
    assert status["last_account_sync_error_kind"] == status_kind
    assert status["last_account_sync_issues"] == [{"stage": "profile_analysis", "kind": issue_kind}]
    assert message_fragment in str(status["last_account_sync_message"])
    assert status["last_account_sync_severity"] == "error"


@pytest.mark.asyncio
async def test_profile_failure_preserves_source_stage_issues_from_same_cycle() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    class _FailingAnalyzeSoul(_BootstrapSoulEngine):
        async def analyze_events(self, events: list[dict[str, Any]]) -> None:
            self.calls.append(events)
            raise RuntimeError("HTTP 503")

    memory = _FakeMemoryManager()
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=_KindClient(
            history_items=[_history_item("BVMIXEDFAIL", 101)],
            favorites_exc=BilibiliAPIError("upstream failed", code=-500),
        ),
        soul_engine=_FailingAnalyzeSoul(ready=False),
    )

    with pytest.raises(RuntimeError):
        await service.sync_now()

    status = service.get_runtime_status()
    assert status["last_account_sync_issues"] == [
        {"stage": "bilibili_favorites", "kind": "api_error"},
        {"stage": "profile_analysis", "kind": "server_error"},
    ]
    message = str(status["last_account_sync_message"])
    assert "B 站收藏夹" in message
    assert "AI 服务返回服务器错误" in message
    assert "2 类问题" in message


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
        max_total_items: int | None = None,
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
    gate = {"allowed": False}
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=client,
        soul_engine=soul,
        llm_work_allowed=lambda: gate["allowed"],
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
    assert service._last_seen_authenticated is False

    # The first allowed tick must still be treated as the auth-ready
    # transition and perform the fetch; a paused tick must not consume it.
    gate["allowed"] = True
    resumed = await service.sync_if_due()
    assert resumed["synced"] is True
    assert client.history_calls == 1
    assert service._last_seen_authenticated is True


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


async def _run_forever_once_with_error(
    exc: BaseException,
) -> tuple[Any, Any]:
    """Drive one run_forever iteration whose sync raises ``exc``, then cancel.

    Returns the ``AccountSyncService`` and lets callers inspect ``caplog``.
    """
    from openbiliclaw.runtime.account_sync import AccountSyncService

    service = AccountSyncService(
        memory_manager=_FakeMemoryManager(),
        bilibili_client=_FakeClient(history_items=[], favorites=[], following=[]),
        soul_engine=_FakeSoulEngine(),
        check_interval_seconds=1,
    )

    async def _raising_sync_if_due() -> dict[str, object]:
        raise exc

    async def _cancel_sleep(_: int) -> None:
        raise asyncio.CancelledError

    service.sync_if_due = _raising_sync_if_due  # type: ignore[method-assign]
    original_sleep = asyncio.sleep
    try:
        asyncio.sleep = _cancel_sleep
        with pytest.raises(asyncio.CancelledError):
            await service.run_forever()
    finally:
        asyncio.sleep = original_sleep
    return service, exc


@pytest.mark.asyncio
async def test_account_sync_run_forever_logs_info_when_no_provider(caplog) -> None:
    import logging

    from openbiliclaw.llm.base import LLMFallbackError
    from openbiliclaw.llm.service import LLMProviderExecutionError

    caplog.set_level(logging.INFO)
    try:
        raise LLMFallbackError("No provider was available to process the request.")
    except LLMFallbackError as inner:
        chained: BaseException = LLMProviderExecutionError(str(inner))
        chained.__cause__ = inner

    await _run_forever_once_with_error(chained)

    assert "no chat LLM provider configured yet" in caplog.text
    assert "Unexpected error in account sync loop" not in caplog.text
    # No scary ERROR/traceback for an expected-transient outage.
    assert not any(record.levelno >= logging.ERROR for record in caplog.records)


@pytest.mark.asyncio
async def test_account_sync_run_forever_logs_warning_when_rate_limited(caplog) -> None:
    import logging

    from openbiliclaw.llm.base import LLMFallbackError, LLMRateLimitError

    caplog.set_level(logging.INFO)
    try:
        raise LLMRateLimitError("429 rate limit exceeded")
    except LLMRateLimitError as inner:
        chained: BaseException = LLMFallbackError(
            "All providers failed (deepseek). Last error: rate limit"
        )
        chained.__cause__ = inner

    await _run_forever_once_with_error(chained)

    assert "rate-limited/cooling down" in caplog.text
    assert "Unexpected error in account sync loop" not in caplog.text
    assert not any(record.levelno >= logging.ERROR for record in caplog.records)


# --- Task 2: cross-source dedup against extension-observed events -----------


class _RecordingDedupDatabase:
    """Stub DB exposing ``recent_event_urls`` with recorded call kwargs.

    ``urls_by_type`` are treated as extension-observed URLs (source != the
    excluded one). ``account_sync_urls_by_type`` are only returned when the
    caller does NOT exclude ``account_sync`` — modeling the self-suppression
    guard where account_sync's own prior rows must never suppress.
    """

    def __init__(
        self,
        urls_by_type: dict[str, set[str]] | None = None,
        *,
        account_sync_urls_by_type: dict[str, set[str]] | None = None,
    ) -> None:
        self.urls_by_type = urls_by_type or {}
        self.account_sync_urls_by_type = account_sync_urls_by_type or {}
        self.calls: list[dict[str, Any]] = []

    def recent_event_urls(
        self,
        event_types: list[str],
        *,
        within_hours: int,
        exclude_source: str | None = None,
        limit: int = 2000,
    ) -> set[str]:
        self.calls.append(
            {
                "event_types": list(event_types),
                "within_hours": within_hours,
                "exclude_source": exclude_source,
                "limit": limit,
            }
        )
        result: set[str] = set()
        for event_type in event_types:
            result |= set(self.urls_by_type.get(event_type, set()))
            if exclude_source != "account_sync":
                result |= set(self.account_sync_urls_by_type.get(event_type, set()))
        return result


@pytest.mark.asyncio
async def test_account_sync_dedups_history_already_seen_by_extension() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _FakeMemoryManager()
    soul = _FakeSoulEngine()
    database = _RecordingDedupDatabase({"view": {"https://www.bilibili.com/video/BVX"}})
    client = _FakeClient(
        history_items=[
            _history_item("BVX", 200, "already seen by extension"),
            _history_item("BVY", 201, "new to backend"),
        ],
        favorites=[],
        following=[],
    )
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=client,
        soul_engine=soul,
        database=database,
    )

    result = await service.sync_now()

    assert result["new_event_count"] == 1
    assert [event["metadata"]["bvid"] for event in memory.events] == ["BVY"]
    # Watermark advances over BVX even though it was deduped (no cursor stall).
    assert memory.state["last_history_view_at"] == 201
    assert any(call["within_hours"] == 48 for call in database.calls)


@pytest.mark.asyncio
async def test_account_sync_dedup_passes_exclude_source_account_sync() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    database = _RecordingDedupDatabase()
    client = _FakeClient(
        history_items=[_history_item("BVA", 100)],
        favorites=[_favorite_folder_with_items(1, "BVB")],
        following=[FollowingUser(mid=5, uname="UP")],
    )
    service = AccountSyncService(
        memory_manager=_FakeMemoryManager(),
        bilibili_client=client,
        soul_engine=_FakeSoulEngine(),
        database=database,
    )

    await service.sync_now()

    assert database.calls
    assert all(call["exclude_source"] == "account_sync" for call in database.calls)


@pytest.mark.asyncio
async def test_account_sync_self_suppression_guard_keeps_history_rewatch() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _FakeMemoryManager()
    # A prior account_sync-emitted view for BVZ exists in the window, but it
    # must NOT suppress a re-watch that the history API surfaces again.
    database = _RecordingDedupDatabase(
        account_sync_urls_by_type={"view": {"https://www.bilibili.com/video/BVZ"}}
    )
    client = _FakeClient(
        history_items=[_history_item("BVZ", 300, "re-watch seen via history only")],
        favorites=[],
        following=[],
    )
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=client,
        soul_engine=_FakeSoulEngine(),
        database=database,
    )

    result = await service.sync_now()

    assert result["new_event_count"] == 1
    assert [event["metadata"]["bvid"] for event in memory.events] == ["BVZ"]


@pytest.mark.asyncio
async def test_account_sync_dedups_favorite_already_seen_by_extension() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _FakeMemoryManager()
    database = _RecordingDedupDatabase({"favorite": {"https://www.bilibili.com/video/BVFOLD"}})
    client = _FakeClient(
        history_items=[],
        favorites=[_favorite_folder_with_items(1, "BVFOLD", "BVFNEW")],
        following=[],
    )
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=client,
        soul_engine=_FakeSoulEngine(),
        database=database,
    )

    result = await service.sync_now()

    assert result["new_event_count"] == 1
    assert [event["metadata"]["bvid"] for event in memory.events] == ["BVFNEW"]


@pytest.mark.asyncio
async def test_account_sync_dedups_follow_already_seen_by_extension() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _FakeMemoryManager()
    database = _RecordingDedupDatabase({"follow": {"https://space.bilibili.com/123"}})
    client = _FakeClient(
        history_items=[],
        favorites=[],
        following=[FollowingUser(mid=123, uname="老UP"), FollowingUser(mid=456, uname="新UP")],
    )
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=client,
        soul_engine=_FakeSoulEngine(),
        database=database,
    )

    result = await service.sync_now()

    assert result["new_event_count"] == 1
    assert [event["metadata"]["up_mid"] for event in memory.events] == [456]


@pytest.mark.asyncio
async def test_account_sync_dedup_preserves_rewatch_outside_window(tmp_path) -> None:
    from datetime import UTC, datetime, timedelta

    from openbiliclaw.runtime.account_sync import AccountSyncService
    from openbiliclaw.storage.database import Database

    db = Database(tmp_path / "dedup.db")
    db.initialize()
    old_url = "https://www.bilibili.com/video/BVREWATCH"
    row_id = db.insert_event(
        "view", url=old_url, title="old view", context="", metadata={"source": "extension"}
    )
    created = (datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=72)).isoformat(sep=" ")
    db.conn.execute("UPDATE events SET created_at = ? WHERE id = ?", (created, row_id))
    db.conn.commit()

    memory = _FakeMemoryManager()
    client = _FakeClient(
        history_items=[_history_item("BVREWATCH", 400, "re-watch after window")],
        favorites=[],
        following=[],
    )
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=client,
        soul_engine=_FakeSoulEngine(),
        database=db,
    )

    result = await service.sync_now()

    # 72h old > 48h window → not suppressed.
    assert result["new_event_count"] == 1
    assert [event["metadata"]["bvid"] for event in memory.events] == ["BVREWATCH"]


@pytest.mark.asyncio
async def test_account_sync_logs_dedup_counts(caplog) -> None:
    import logging

    from openbiliclaw.runtime.account_sync import AccountSyncService

    database = _RecordingDedupDatabase({"view": {"https://www.bilibili.com/video/BVDROP"}})
    client = _FakeClient(
        history_items=[_history_item("BVDROP", 100), _history_item("BVKEEP", 101)],
        favorites=[],
        following=[],
    )
    service = AccountSyncService(
        memory_manager=_FakeMemoryManager(),
        bilibili_client=client,
        soul_engine=_FakeSoulEngine(),
        database=database,
    )

    with caplog.at_level(logging.INFO):
        await service.sync_now()

    assert "deduped" in caplog.text


# --- Task 3: unify account_sync into the profile pipeline when ready --------


class _PipelineSpy:
    def __init__(self, *, raise_on_ingest: bool = False) -> None:
        self.batches: list[list[Any]] = []
        self.raise_on_ingest = raise_on_ingest

    async def ingest_batch(self, signals: list[Any]) -> None:
        self.batches.append(list(signals))
        if self.raise_on_ingest:
            raise RuntimeError("ingest boom")


class _ReadyPipelineEngine:
    """Profile ready + pipeline present."""

    def __init__(self, pipeline: Any) -> None:
        self.pipeline = pipeline
        self.analyze_calls: list[list[dict[str, Any]]] = []
        self.bootstrap_calls: list[list[dict[str, Any]]] = []

    def is_profile_ready(self) -> bool:
        return True

    async def analyze_events(self, events: list[dict[str, Any]]) -> None:
        self.analyze_calls.append(events)

    async def build_initial_profile(self, history: list[dict[str, Any]]) -> object:
        self.bootstrap_calls.append(history)
        return object()


class _NoReadinessEngine:
    """No ``is_profile_ready`` attribute → treated as not ready (legacy path)."""

    def __init__(self) -> None:
        self.pipeline = _PipelineSpy()
        self.analyze_calls: list[list[dict[str, Any]]] = []

    async def analyze_events(self, events: list[dict[str, Any]]) -> None:
        self.analyze_calls.append(events)


class _ReadinessRaisesEngine:
    def __init__(self) -> None:
        self.pipeline = _PipelineSpy()
        self.analyze_calls: list[list[dict[str, Any]]] = []
        self.bootstrap_calls: list[list[dict[str, Any]]] = []

    def is_profile_ready(self) -> bool:
        raise RuntimeError("readiness boom")

    async def analyze_events(self, events: list[dict[str, Any]]) -> None:
        self.analyze_calls.append(events)

    async def build_initial_profile(self, history: list[dict[str, Any]]) -> object:
        self.bootstrap_calls.append(history)
        return object()


def _history_client() -> _FakeClient:
    return _FakeClient(
        history_items=[_history_item("BVP1", 100), _history_item("BVP2", 101)],
        favorites=[],
        following=[],
    )


@pytest.mark.asyncio
async def test_account_sync_ready_profile_uses_pipeline_only() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _FakeMemoryManager()
    pipeline = _PipelineSpy()
    soul = _ReadyPipelineEngine(pipeline)
    service = AccountSyncService(
        memory_manager=memory, bilibili_client=_history_client(), soul_engine=soul
    )

    result = await service.sync_now()

    assert result["new_event_count"] == 2
    # Pipeline received one signal per event; analyze_events NOT called.
    assert len(pipeline.batches) == 1
    assert len(pipeline.batches[0]) == 2
    assert soul.analyze_calls == []
    assert soul.bootstrap_calls == []
    # propagate_event persistence still happened first.
    assert len(memory.events) == 2


@pytest.mark.asyncio
async def test_account_sync_ready_but_pipeline_missing_falls_back(caplog) -> None:
    import logging

    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _FakeMemoryManager()
    soul = _ReadyPipelineEngine(pipeline=None)
    service = AccountSyncService(
        memory_manager=memory, bilibili_client=_history_client(), soul_engine=soul
    )

    with caplog.at_level(logging.WARNING):
        await service.sync_now()

    assert len(soul.analyze_calls) == 1
    assert soul.bootstrap_calls == []  # profile exists → no bootstrap
    assert len(memory.events) == 2
    assert any(record.levelno >= logging.WARNING for record in caplog.records)


@pytest.mark.asyncio
async def test_account_sync_ready_pipeline_raises_falls_back(caplog) -> None:
    import logging

    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _FakeMemoryManager()
    pipeline = _PipelineSpy(raise_on_ingest=True)
    soul = _ReadyPipelineEngine(pipeline)
    service = AccountSyncService(
        memory_manager=memory, bilibili_client=_history_client(), soul_engine=soul
    )

    with caplog.at_level(logging.WARNING):
        result = await service.sync_now()

    assert result["new_event_count"] == 2  # sync did not crash
    assert len(pipeline.batches) == 1  # attempted
    assert len(soul.analyze_calls) == 1  # fell back
    assert soul.bootstrap_calls == []
    assert any(record.levelno >= logging.WARNING for record in caplog.records)


@pytest.mark.asyncio
async def test_account_sync_not_ready_uses_legacy_path_with_bootstrap() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _FakeMemoryManager()
    soul = _BootstrapSoulEngine(ready=False)
    service = AccountSyncService(
        memory_manager=memory, bilibili_client=_history_client(), soul_engine=soul
    )

    await service.sync_now()

    assert len(soul.calls) == 1  # analyze_events legacy path
    assert soul.bootstrap_calls == [[]]  # bootstrap attempted
    assert len(memory.events) == 2


@pytest.mark.asyncio
async def test_account_sync_missing_readiness_is_treated_as_not_ready() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _FakeMemoryManager()
    soul = _NoReadinessEngine()
    service = AccountSyncService(
        memory_manager=memory, bilibili_client=_history_client(), soul_engine=soul
    )

    await service.sync_now()

    # Legacy analyze_events path; pipeline never engaged.
    assert len(soul.analyze_calls) == 1
    assert soul.pipeline.batches == []
    assert len(memory.events) == 2


@pytest.mark.asyncio
async def test_account_sync_readiness_raising_is_treated_as_not_ready() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _FakeMemoryManager()
    soul = _ReadinessRaisesEngine()
    service = AccountSyncService(
        memory_manager=memory, bilibili_client=_history_client(), soul_engine=soul
    )

    await service.sync_now()

    assert len(soul.analyze_calls) == 1  # conservative legacy path
    assert soul.pipeline.batches == []
    assert len(memory.events) == 2


# --- Task 4: surface sync failures (logging + error kind) -------------------


@dataclass
class _KindClient:
    """Client whose stages raise configurable exceptions for error-kind tests."""

    history_exc: Exception | None = None
    favorites_exc: Exception | None = None
    following_exc: Exception | None = None
    history_items: list[dict[str, Any]] | None = None

    async def get_user_history(self, max_items: int = 100) -> list[dict[str, Any]]:
        if self.history_exc is not None:
            raise self.history_exc
        return (self.history_items or [])[:max_items]

    async def get_all_favorites(
        self,
        *,
        max_folders: int = 10,
        max_items_per_folder: int = 50,
        max_total_items: int | None = None,
    ) -> list[FavoriteFolderWithItems]:
        if self.favorites_exc is not None:
            raise self.favorites_exc
        return []

    async def get_following(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
    ) -> list[FollowingUser]:
        if self.following_exc is not None:
            raise self.following_exc
        return []


@pytest.mark.asyncio
async def test_account_sync_logs_warning_on_stage_failure(caplog) -> None:
    import logging

    from openbiliclaw.runtime.account_sync import AccountSyncService

    service = AccountSyncService(
        memory_manager=_FakeMemoryManager(),
        bilibili_client=_KindClient(history_exc=RuntimeError("history boom")),
        soul_engine=_FakeSoulEngine(),
    )

    with caplog.at_level(logging.WARNING):
        await service.sync_now()

    assert any(record.levelno >= logging.WARNING for record in caplog.records)
    assert "history" in caplog.text


@pytest.mark.asyncio
async def test_account_sync_records_auth_expired_kind() -> None:
    from openbiliclaw.bilibili.api import BilibiliAuthExpiredError
    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _FakeMemoryManager()
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=_KindClient(history_exc=BilibiliAuthExpiredError("logged out")),
        soul_engine=_FakeSoulEngine(),
    )

    await service.sync_now()

    assert memory.state["last_sync_error_kind"] == "auth_expired"


@pytest.mark.asyncio
async def test_account_sync_records_generic_error_kind() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _FakeMemoryManager()
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=_KindClient(history_exc=RuntimeError("boom")),
        soul_engine=_FakeSoulEngine(),
    )

    await service.sync_now()

    assert memory.state["last_sync_error_kind"] == "error"
    assert memory.state["last_sync_issues"] == [
        {"stage": "bilibili_history", "kind": "unexpected_error"}
    ]
    status = service.get_runtime_status()
    assert status["last_account_sync_issues"] == memory.state["last_sync_issues"]
    assert "B 站观看历史未同步" in str(status["last_account_sync_message"])
    assert "未分类异常" in str(status["last_account_sync_message"])
    assert "账号同步出错" not in str(status["last_account_sync_message"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc", "issue_kind", "message_fragment", "severity"),
    [
        (
            BilibiliAPIError("rate limited", code=-429),
            "rate_limited",
            "B 站接口限流",
            "warning",
        ),
        (
            BilibiliAPIError("upstream failed", code=-500),
            "api_error",
            "B 站接口返回异常",
            "error",
        ),
        (
            httpx.ConnectError("connection refused"),
            "network",
            "无法连接 B 站",
            "error",
        ),
        (
            TimeoutError("request timed out"),
            "timeout",
            "请求超时",
            "error",
        ),
    ],
)
async def test_account_sync_classifies_bilibili_failure_reason(
    exc: Exception,
    issue_kind: str,
    message_fragment: str,
    severity: str,
) -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _FakeMemoryManager()
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=_KindClient(favorites_exc=exc),
        soul_engine=_FakeSoulEngine(),
    )

    await service.sync_now()

    status = service.get_runtime_status()
    assert status["last_account_sync_issues"] == [
        {"stage": "bilibili_favorites", "kind": issue_kind}
    ]
    assert message_fragment in str(status["last_account_sync_message"])
    assert status["last_account_sync_severity"] == severity


@pytest.mark.asyncio
async def test_account_sync_message_lists_multiple_failed_stages() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _FakeMemoryManager()
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=_KindClient(
            favorites_exc=RuntimeError("favorites boom"),
            following_exc=TimeoutError("following timed out"),
        ),
        soul_engine=_FakeSoulEngine(),
    )

    await service.sync_now()

    status = service.get_runtime_status()
    assert status["last_account_sync_issues"] == [
        {"stage": "bilibili_favorites", "kind": "unexpected_error"},
        {"stage": "bilibili_following", "kind": "timeout"},
    ]
    message = str(status["last_account_sync_message"])
    assert "2 类问题" in message
    assert "收藏夹" in message
    assert "关注列表" in message
    assert "未分类异常" in message
    assert "请求超时" in message


@pytest.mark.asyncio
async def test_account_sync_auth_expired_kind_wins_over_generic() -> None:
    from openbiliclaw.bilibili.api import BilibiliAuthExpiredError
    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _FakeMemoryManager()
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=_KindClient(
            favorites_exc=RuntimeError("generic favorites boom"),
            following_exc=BilibiliAuthExpiredError("logged out"),
        ),
        soul_engine=_FakeSoulEngine(),
    )

    await service.sync_now()

    assert memory.state["last_sync_error_kind"] == "auth_expired"


@pytest.mark.asyncio
async def test_account_sync_clean_sync_clears_error_kind() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _FakeMemoryManager(
        {
            **_FakeMemoryManager().state,
            "last_sync_error_kind": "error",
            "last_sync_issues": [{"stage": "bilibili_history", "kind": "unexpected_error"}],
        }
    )
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=_KindClient(history_items=[_history_item("BVOK", 100)]),
        soul_engine=_FakeSoulEngine(),
    )

    await service.sync_now()

    assert memory.state["last_sync_error_kind"] == ""
    assert memory.state["last_sync_issues"] == []


@pytest.mark.asyncio
async def test_account_sync_runtime_status_exposes_error_kind() -> None:
    from openbiliclaw.bilibili.api import BilibiliAuthExpiredError
    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _FakeMemoryManager()
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=_KindClient(history_exc=BilibiliAuthExpiredError("logged out")),
        soul_engine=_FakeSoulEngine(),
    )

    await service.sync_now()
    status = service.get_runtime_status()

    assert status["last_account_sync_error_kind"] == "auth_expired"


# --- Task 5: X (Twitter) scheduled incremental sync -------------------------


class _FakeXClient:
    def __init__(
        self,
        *,
        likes: list[dict[str, Any]] | None = None,
        bookmarks: list[dict[str, Any]] | None = None,
        likes_exc: Exception | None = None,
        bookmarks_exc: Exception | None = None,
    ) -> None:
        self._likes = likes or []
        self._bookmarks = bookmarks or []
        self.likes_exc = likes_exc
        self.bookmarks_exc = bookmarks_exc
        self.likes_calls = 0
        self.bookmarks_calls = 0

    async def likes(self, *, limit: int) -> list[dict[str, Any]]:
        self.likes_calls += 1
        if self.likes_exc is not None:
            raise self.likes_exc
        return self._likes[:limit]

    async def bookmarks(self, *, limit: int) -> list[dict[str, Any]]:
        self.bookmarks_calls += 1
        if self.bookmarks_exc is not None:
            raise self.bookmarks_exc
        return self._bookmarks[:limit]


class _FakeXHealth:
    def __init__(self, *, state: str = "ok", ready: bool = True) -> None:
        self.state = state
        self.ready = ready
        self.successes: list[str] = []
        self.errors: list[tuple[BaseException, str]] = []

    def is_ready(self) -> bool:
        return self.ready

    def get(self) -> dict[str, Any]:
        return {"state": self.state}

    def record_success(self, *, strategy: str = "") -> None:
        self.successes.append(strategy)
        self.state = "ok"
        self.ready = True

    def record_error(self, exc: BaseException, *, strategy: str = "") -> str:
        self.errors.append((exc, strategy))
        self.state = "rate_limited"
        self.ready = False
        return self.state


def _tweet(tid: str, *, text: str = "hello world", screen: str = "alice") -> dict[str, Any]:
    return {"id": tid, "text": text, "author": {"screenName": screen, "name": screen}}


def _empty_bili_client() -> _FakeClient:
    return _FakeClient(history_items=[], favorites=[], following=[])


@pytest.mark.asyncio
async def test_x_none_client_emits_no_x_events() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _FakeMemoryManager()
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=_FakeClient(
            history_items=[_history_item("BVONLY", 100)], favorites=[], following=[]
        ),
        soul_engine=_FakeSoulEngine(),
    )

    result = await service.sync_now()

    assert result["new_event_count"] == 1
    assert {e["event_type"] for e in memory.events} == {"view"}


@pytest.mark.asyncio
async def test_x_first_sync_seeds_from_persisted_events(tmp_path) -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService
    from openbiliclaw.storage.database import Database

    db = Database(tmp_path / "x.db")
    db.initialize()
    # init already persisted this like (x.com/<handle>/status/<id> form).
    db.insert_event(
        "like",
        url="https://x.com/alice/status/100",
        title="seeded",
        context="",
        metadata={"tweet_id": "100"},
    )

    memory = _FakeMemoryManager()
    x_client = _FakeXClient(likes=[_tweet("100"), _tweet("200")])
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=_empty_bili_client(),
        soul_engine=_FakeSoulEngine(),
        database=db,
        x_client=x_client,
    )

    result = await service.sync_now()

    # 100 was seeded from the events table → not re-emitted; 200 is post-init.
    assert result["new_event_count"] == 1
    like_events = [e for e in memory.events if e["event_type"] == "like"]
    assert len(like_events) == 1
    assert like_events[0]["metadata"]["tweet_id"] == "200"
    assert like_events[0]["metadata"]["source_platform"] == "twitter"
    assert like_events[0]["metadata"]["source"] == "account_sync"
    saved = set(memory.state["x_like_ids"])
    assert {"100", "200"} <= saved


@pytest.mark.asyncio
async def test_x_second_sync_emits_one_new_like_and_feeds_pipeline() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    base_state = _FakeMemoryManager().state
    memory = _FakeMemoryManager({**base_state, "x_like_ids": ["100"], "x_bookmark_ids": []})
    pipeline = _PipelineSpy()
    soul = _ReadyPipelineEngine(pipeline)
    x_client = _FakeXClient(likes=[_tweet("100"), _tweet("101")])
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=_empty_bili_client(),
        soul_engine=soul,
        x_client=x_client,
    )

    result = await service.sync_now()

    assert result["new_event_count"] == 1
    like_events = [e for e in memory.events if e["event_type"] == "like"]
    assert len(like_events) == 1
    assert like_events[0]["metadata"]["tweet_id"] == "101"
    assert like_events[0]["metadata"]["source_platform"] == "twitter"
    # Fed through the Task 3 pipeline branch.
    assert len(pipeline.batches) == 1
    assert len(pipeline.batches[0]) == 1


@pytest.mark.asyncio
async def test_x_bookmarks_map_to_favorite_event() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    base_state = _FakeMemoryManager().state
    memory = _FakeMemoryManager({**base_state, "x_bookmark_ids": ["seed"]})
    x_client = _FakeXClient(bookmarks=[_tweet("300")])
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=_empty_bili_client(),
        soul_engine=_FakeSoulEngine(),
        x_client=x_client,
    )

    await service.sync_now()

    fav_events = [e for e in memory.events if e["event_type"] == "favorite"]
    assert len(fav_events) == 1
    assert fav_events[0]["metadata"]["tweet_id"] == "300"
    assert fav_events[0]["metadata"]["source_platform"] == "twitter"


@pytest.mark.asyncio
async def test_x_id_set_capped_at_2000() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    base_state = _FakeMemoryManager().state
    existing = [str(i) for i in range(2001)]
    memory = _FakeMemoryManager({**base_state, "x_like_ids": existing, "x_bookmark_ids": ["x"]})
    x_client = _FakeXClient(likes=[])
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=_empty_bili_client(),
        soul_engine=_FakeSoulEngine(),
        x_client=x_client,
    )

    await service.sync_now()

    assert len(memory.state["x_like_ids"]) == 2000


@pytest.mark.asyncio
async def test_x_fetch_error_records_error_and_preserves_bilibili(caplog) -> None:
    import logging

    from openbiliclaw.runtime.account_sync import AccountSyncService

    base_state = _FakeMemoryManager().state
    memory = _FakeMemoryManager({**base_state, "x_like_ids": ["seed"], "x_bookmark_ids": ["seed"]})
    x_client = _FakeXClient(likes_exc=RuntimeError("x boom"))
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=_FakeClient(
            history_items=[_history_item("BVSAFE", 100)], favorites=[], following=[]
        ),
        soul_engine=_FakeSoulEngine(),
        x_client=x_client,
    )

    with caplog.at_level(logging.WARNING):
        result = await service.sync_now()

    # Bilibili view still flows despite the X failure.
    assert any(e["event_type"] == "view" for e in memory.events)
    assert result["new_event_count"] >= 1
    assert "x boom" in str(memory.state["last_sync_error"])
    assert memory.state["last_sync_error_kind"] == "error"
    assert "x likes" in caplog.text


@pytest.mark.asyncio
async def test_x_account_sync_honors_existing_source_cooldown(tmp_path: Path) -> None:
    """A source-level 429 cooldown must gate likes/bookmarks too, not just discovery."""
    from openbiliclaw.runtime.account_sync import AccountSyncService
    from openbiliclaw.sources.x_client import XRateLimitError
    from openbiliclaw.storage.database import Database
    from openbiliclaw.storage.x_health import XSourceHealthStore

    memory = _FakeMemoryManager()
    x_client = _FakeXClient(likes=[_tweet("100")], bookmarks=[_tweet("200")])
    db = Database(tmp_path / "x-health.db")
    db.initialize()
    health = XSourceHealthStore(db)
    health.record_error(XRateLimitError("429 Too Many Requests"), strategy="search")
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=_empty_bili_client(),
        soul_engine=_FakeSoulEngine(),
        x_client=x_client,
        x_health_store=health,
    )

    result = await service.sync_now()

    assert x_client.likes_calls == 0
    assert x_client.bookmarks_calls == 0
    assert memory.state["last_sync_error_kind"] == "x_rate_limited"
    assert "X 账号喜好同步因来源限流冷却而跳过" in result["errors"]
    status = service.get_runtime_status()
    assert status["last_account_sync_severity"] == "warning"
    assert "X 暂时限流" in str(status["last_account_sync_message"])
    assert "B 站等其他来源不受影响" in str(status["last_account_sync_message"])
    assert "无需操作" in str(status["last_account_sync_message"])
    assert "账号同步出错" not in str(status["last_account_sync_message"])


@pytest.mark.asyncio
async def test_x_live_rate_limit_opens_shared_cooldown_and_skips_second_request() -> None:
    """The first 429 in a pair records health and prevents an immediate second hit."""
    from openbiliclaw.runtime.account_sync import AccountSyncService
    from openbiliclaw.sources.x_client import XRateLimitError

    memory = _FakeMemoryManager()
    x_client = _FakeXClient(
        likes_exc=XRateLimitError("429 Too Many Requests"),
        bookmarks=[_tweet("200")],
    )
    health = _FakeXHealth()
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=_empty_bili_client(),
        soul_engine=_FakeSoulEngine(),
        x_client=x_client,
        x_health_store=health,
    )

    await service.sync_now()

    assert x_client.likes_calls == 1
    assert x_client.bookmarks_calls == 0
    assert [(strategy, type(exc).__name__) for exc, strategy in health.errors] == [
        ("likes", "XRateLimitError")
    ]
    assert memory.state["last_sync_error_kind"] == "x_rate_limited"


@pytest.mark.asyncio
async def test_x_success_updates_shared_health_for_both_account_paths() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    health = _FakeXHealth()
    service = AccountSyncService(
        memory_manager=_FakeMemoryManager(),
        bilibili_client=_empty_bili_client(),
        soul_engine=_FakeSoulEngine(),
        x_client=_FakeXClient(likes=[], bookmarks=[]),
        x_health_store=health,
    )

    await service.sync_now()

    assert health.successes == ["likes", "bookmarks"]


@pytest.mark.asyncio
async def test_non_x_error_prevents_misleading_x_only_status_copy() -> None:
    """Do not claim other sources were unaffected when Bilibili also failed."""
    from openbiliclaw.runtime.account_sync import AccountSyncService
    from openbiliclaw.sources.x_client import XAuthError

    memory = _FakeMemoryManager()
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=_KindClient(history_exc=RuntimeError("bilibili boom")),
        soul_engine=_FakeSoulEngine(),
        x_client=_FakeXClient(likes_exc=XAuthError("401 Unauthorized")),
    )

    await service.sync_now()

    status = service.get_runtime_status()
    assert status["last_account_sync_error_kind"] == "error"
    assert "其他来源不受影响" not in str(status["last_account_sync_message"])


@pytest.mark.asyncio
async def test_x_cross_source_dedup_keys_on_tweet_id(tmp_path) -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService
    from openbiliclaw.storage.database import Database

    db = Database(tmp_path / "xdedup.db")
    db.initialize()
    # Extension reported the like at the /i/status/<id> form.
    db.insert_event(
        "like",
        url="https://x.com/i/status/123",
        title="ext like",
        context="",
        metadata={"source": "extension"},
    )

    base_state = _FakeMemoryManager().state
    # Non-empty state disables seeding so only Task 2 dedup can suppress.
    memory = _FakeMemoryManager({**base_state, "x_like_ids": ["999"], "x_bookmark_ids": ["999"]})
    # account_sync fetches the same tweet under the /<handle>/status/<id> form.
    x_client = _FakeXClient(likes=[_tweet("123", screen="someuser")])
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=_empty_bili_client(),
        soul_engine=_FakeSoulEngine(),
        database=db,
        x_client=x_client,
    )

    result = await service.sync_now()

    # tweet 123 already observed by the extension → suppressed by cross-source dedup.
    assert result["new_event_count"] == 0
    assert memory.events == []


# --- Task 6: favorites budget + following pagination ------------------------


@dataclass
class _PaginatedClient:
    """Stub that paginates ``get_following`` and records fetch call args."""

    following_pages: list[list[FollowingUser]] = field(default_factory=list)
    favorites: list[FavoriteFolderWithItems] = field(default_factory=list)
    fail_following_page: int | None = None
    following_page_exc: Exception | None = None
    favorites_call_kwargs: dict[str, Any] | None = None
    following_calls: list[tuple[int, int]] = field(default_factory=list)

    async def get_user_history(self, max_items: int = 100) -> list[dict[str, Any]]:
        return []

    async def get_all_favorites(
        self,
        *,
        max_folders: int = 10,
        max_items_per_folder: int = 50,
        max_total_items: int | None = None,
    ) -> list[FavoriteFolderWithItems]:
        self.favorites_call_kwargs = {
            "max_folders": max_folders,
            "max_items_per_folder": max_items_per_folder,
            "max_total_items": max_total_items,
        }
        return list(self.favorites)

    async def get_following(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
    ) -> list[FollowingUser]:
        self.following_calls.append((page, page_size))
        if self.fail_following_page is not None and page == self.fail_following_page:
            raise self.following_page_exc or RuntimeError("following page boom")
        idx = page - 1
        if 0 <= idx < len(self.following_pages):
            return list(self.following_pages[idx])
        return []


def _following_page(mids: range | list[int]) -> list[FollowingUser]:
    return [FollowingUser(mid=mid, uname=f"up-{mid}") for mid in mids]


@pytest.mark.asyncio
async def test_account_sync_favorites_budget_passes_max_total_items() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    client = _PaginatedClient()
    service = AccountSyncService(
        memory_manager=_FakeMemoryManager(),
        bilibili_client=client,
        soul_engine=_FakeSoulEngine(),
    )

    await service.sync_now()

    # 每文件夹上限与总预算一致（与 init 同口径）：50 时一个 800 条的默认收藏夹
    # 有 750 条永远进不了去重账本，而 max_total_items 已经兜住了请求量。
    assert client.favorites_call_kwargs == {
        "max_folders": 200,
        "max_items_per_folder": 500,
        "max_total_items": 500,
    }


@pytest.mark.asyncio
async def test_account_sync_following_paginates_until_short_page() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _FakeMemoryManager()
    client = _PaginatedClient(
        following_pages=[
            _following_page(range(1, 101)),  # full page (100)
            _following_page(range(101, 201)),  # full page (100)
            _following_page(range(201, 231)),  # short page (30) -> stop
        ],
    )
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=client,
        soul_engine=_FakeSoulEngine(),
    )

    result = await service.sync_now()

    assert client.following_calls == [(1, 100), (2, 100), (3, 100)]
    follow_events = [e for e in memory.events if e["event_type"] == "follow"]
    assert len(follow_events) == 230
    assert result["new_event_count"] == 230


@pytest.mark.asyncio
async def test_account_sync_following_stops_at_max_pages() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _FakeMemoryManager()
    client = _PaginatedClient(
        following_pages=[_following_page(range(i * 100 + 1, i * 100 + 101)) for i in range(8)],
    )
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=client,
        soul_engine=_FakeSoulEngine(),
    )

    await service.sync_now()

    assert [page for page, _ in client.following_calls] == [1, 2, 3, 4, 5]
    follow_events = [e for e in memory.events if e["event_type"] == "follow"]
    assert len(follow_events) == 500


@pytest.mark.asyncio
async def test_account_sync_following_single_short_page_makes_one_call() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _FakeMemoryManager()
    client = _PaginatedClient(following_pages=[_following_page([1, 2, 3])])
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=client,
        soul_engine=_FakeSoulEngine(),
    )

    await service.sync_now()

    assert client.following_calls == [(1, 100)]
    follow_events = [e for e in memory.events if e["event_type"] == "follow"]
    assert len(follow_events) == 3


@pytest.mark.asyncio
async def test_account_sync_following_dedups_across_paginated_union() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    # State already saw mids 1..100 (a prior page-1). New sync pulls two pages;
    # only the genuinely-new mids (101..130) become follow events.
    base_state = _FakeMemoryManager().state
    memory = _FakeMemoryManager(
        {**base_state, "following_mids": [str(mid) for mid in range(1, 101)]}
    )
    client = _PaginatedClient(
        following_pages=[
            _following_page(range(1, 101)),  # all already seen
            _following_page(range(101, 131)),  # short page, all new
        ],
    )
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=client,
        soul_engine=_FakeSoulEngine(),
    )

    await service.sync_now()

    assert client.following_calls == [(1, 100), (2, 100)]
    follow_mids = {e["metadata"]["up_mid"] for e in memory.events if e["event_type"] == "follow"}
    assert follow_mids == set(range(101, 131))


@pytest.mark.asyncio
async def test_account_sync_following_partial_page_failure_still_imports() -> None:
    from openbiliclaw.bilibili.api import BilibiliAuthExpiredError
    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _FakeMemoryManager()
    client = _PaginatedClient(
        following_pages=[
            _following_page(range(1, 101)),  # full page -> continue
            [],  # unused; page 2 raises before returning
        ],
        fail_following_page=2,
        following_page_exc=BilibiliAuthExpiredError("logged out"),
    )
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=client,
        soul_engine=_FakeSoulEngine(),
    )

    result = await service.sync_now()

    # Page 1's follows are still imported despite page 2 failing.
    follow_events = [e for e in memory.events if e["event_type"] == "follow"]
    assert len(follow_events) == 100
    assert result["new_event_count"] == 100
    # The error is recorded, auth-expired precedence intact, timestamp stamped.
    assert memory.state["last_sync_error_kind"] == "auth_expired"
    assert "logged out" in str(memory.state["last_sync_error"])
    assert memory.state["last_account_sync_at"]


def test_merge_stage_errors_drops_repeated_cause() -> None:
    """One expired cookie fails three stages with the same message.

    history hits /x/web-interface/history/cursor; favorites and following each
    call get_nav_info() for the mid, so users saw one cause joined three times.
    """
    from openbiliclaw.runtime.account_sync import AccountSyncService

    expired = "Bilibili session expired on /x/web-interface/nav (-101)."
    cursor = "Bilibili session expired on /x/web-interface/history/cursor (-101)."

    merged = AccountSyncService._merge_stage_errors([cursor, expired, expired])

    assert merged == f"{cursor} | {expired}"


def test_user_facing_sync_message_guides_relogin_for_expired_cookie() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    message = AccountSyncService._user_facing_sync_message(
        "auth_expired",
        "Bilibili session expired on /x/web-interface/nav (-101).",
    )

    # Actionable Chinese copy, not the provider's English error.
    assert "重新登录" in message
    assert "-101" not in message
    assert "expired" not in message


def test_user_facing_sync_message_empty_when_healthy() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    assert AccountSyncService._user_facing_sync_message("", "") == ""


def test_user_facing_sync_message_labels_legacy_unclassified_state_honestly() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    message = AccountSyncService._user_facing_sync_message("error", "opaque legacy detail")

    assert "旧版或未分类异常" in message
    assert "无法确定具体环节" in message
    assert "账号同步出错" not in message


def test_runtime_status_normalizes_unknown_persisted_issue_codes() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _FakeMemoryManager(
        {
            **_FakeMemoryManager().state,
            "last_sync_error": "legacy detail",
            "last_sync_error_kind": "error",
            "last_sync_issues": [{"stage": "future_stage", "kind": "future_kind"}],
        }
    )
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=_FakeClient(history_items=[], favorites=[], following=[]),
        soul_engine=_FakeSoulEngine(),
    )

    status = service.get_runtime_status()

    assert status["last_account_sync_issues"] == [{"stage": "unknown", "kind": "unexpected_error"}]
    assert "未分类异常" in str(status["last_account_sync_message"])


def test_user_facing_sync_message_actionable_for_llm_unavailability() -> None:
    """no_provider / model_not_found never promise an automatic retry.

    Nothing recovers until the user fixes the model configuration, so the copy
    must point at settings instead of the generic "稍后会自动重试"."""
    from openbiliclaw.runtime.account_sync import AccountSyncService

    no_provider = AccountSyncService._user_facing_sync_message(
        "no_provider", "画像分析失败：AI 服务未配置"
    )
    assert "设置" in no_provider
    assert "稍后会自动重试" not in no_provider

    model_missing = AccountSyncService._user_facing_sync_message(
        "model_not_found", "画像分析失败：找不到所配置的模型"
    )
    assert "模型名" in model_missing
    assert "稍后会自动重试" not in model_missing

    # Rate limiting IS transient — the loop genuinely retries next cycle.
    rate_limited = AccountSyncService._user_facing_sync_message(
        "rate_limited", "画像分析失败：AI 服务限流"
    )
    assert "自动重试" in rate_limited

    x_rate_limited = AccountSyncService._user_facing_sync_message("x_rate_limited", "X 429")
    assert "X 暂时限流" in x_rate_limited
    assert "其他来源不受影响" in x_rate_limited
    assert "无需操作" in x_rate_limited


def test_runtime_status_severity_for_llm_kinds() -> None:
    """rate_limited reads as a warning (backoff), config faults as errors."""
    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _FakeMemoryManager()
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=_FakeClient(history_items=[], favorites=[], following=[]),
        soul_engine=_BootstrapSoulEngine(ready=True),
    )

    for kind, expected in (
        ("rate_limited", "warning"),
        ("x_rate_limited", "warning"),
        ("x_auth_expired", "warning"),
        ("x_blocked", "error"),
        ("no_provider", "error"),
        ("model_not_found", "error"),
    ):
        memory.state["last_sync_error"] = "画像分析失败：x"
        memory.state["last_sync_error_kind"] = kind
        status = service.get_runtime_status()
        assert status["last_account_sync_severity"] == expected, kind
        assert status["last_account_sync_message"]


def test_exclusive_file_lock_is_non_blocking_for_second_holder(tmp_path: Path) -> None:
    from openbiliclaw.memory.json_state import exclusive_file_lock

    lock_path = tmp_path / "account_sync.run.lock"
    with exclusive_file_lock(lock_path, blocking=False) as first:
        assert first is True
        # Same process, same file: a second non-blocking attempt from another
        # holder must lose rather than wait.
        import subprocess
        import sys

        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys;"
                    "from pathlib import Path;"
                    "from openbiliclaw.memory.json_state import exclusive_file_lock;"
                    f"p=Path({str(lock_path)!r});"
                    "ctx=exclusive_file_lock(p, blocking=False);"
                    "acquired=ctx.__enter__();"
                    "print('acquired' if acquired else 'busy');"
                    "ctx.__exit__(None, None, None)"
                ),
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
        )
        assert probe.stdout.strip() == "busy", probe.stderr

    # After the holder releases, the lock is free again.
    with exclusive_file_lock(lock_path, blocking=False) as reacquired:
        assert reacquired is True


def test_exclusive_file_lock_released_when_holder_process_dies(tmp_path: Path) -> None:
    import subprocess
    import sys

    from openbiliclaw.memory.json_state import exclusive_file_lock

    lock_path = tmp_path / "account_sync.run.lock"
    # A crashed holder must not strand the lock — the kernel releases it.
    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path;"
                "from openbiliclaw.memory.json_state import exclusive_file_lock;"
                f"p=Path({str(lock_path)!r});"
                "ctx=exclusive_file_lock(p, blocking=False);"
                "ctx.__enter__();"
                "import os; os._exit(1)"
            ),
        ],
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
    )

    with exclusive_file_lock(lock_path, blocking=False) as acquired:
        assert acquired is True


@dataclass
class _NavProbeClient(_FakeClient):
    """A client with a nav probe, as the real ``BilibiliAPIClient`` has."""

    nav_login: bool = True
    nav_error: Exception | None = None
    nav_calls: int = 0

    async def get_nav_info(self) -> Any:
        from openbiliclaw.bilibili.api import NavInfo

        self.nav_calls += 1
        if self.nav_error is not None:
            raise self.nav_error
        return NavInfo(is_login=self.nav_login, uname="tester", mid=42)


def _auth_expired_memory() -> _FakeMemoryManager:
    memory = _FakeMemoryManager()
    memory.state["last_sync_error"] = "账号登录已失效 (-101)"
    memory.state["last_sync_error_kind"] = "auth_expired"
    memory.state["last_sync_issues"] = [
        {"stage": "bilibili_history", "kind": "auth_expired"},
        {"stage": "profile_analysis", "kind": "timeout"},
    ]
    return memory


def test_clear_auth_expired_state_drops_persisted_issue() -> None:
    """A fresh cookie must retire the persisted auth_expired banner state."""
    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _auth_expired_memory()
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=_FakeClient(history_items=[], favorites=[], following=[]),
        soul_engine=_FakeSoulEngine(),
    )

    assert service.clear_auth_expired_state() is True
    assert memory.state["last_sync_error"] == ""
    assert memory.state["last_sync_error_kind"] == ""
    # Only the auth_expired entries go; unrelated issues stay.
    assert memory.state["last_sync_issues"] == [{"stage": "profile_analysis", "kind": "timeout"}]
    # Idempotent: nothing left to clear.
    assert service.clear_auth_expired_state() is False


def test_clear_auth_expired_state_noop_without_auth_expired() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _FakeMemoryManager()
    memory.state["last_sync_issues"] = [{"stage": "profile_analysis", "kind": "timeout"}]
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=_FakeClient(history_items=[], favorites=[], following=[]),
        soul_engine=_FakeSoulEngine(),
    )
    assert service.clear_auth_expired_state() is False
    assert memory.state["last_sync_issues"] == [{"stage": "profile_analysis", "kind": "timeout"}]


async def test_paused_tick_clears_stale_auth_expired_when_nav_logged_in() -> None:
    """Paused on LLM work, the tick still re-validates auth: a fixed cookie
    clears the stale banner instead of waiting for the first unpaused tick."""
    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _auth_expired_memory()
    client = _NavProbeClient(history_items=[], favorites=[], following=[], nav_login=True)
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=client,
        soul_engine=_FakeSoulEngine(),
        llm_work_allowed=lambda: False,
    )

    result = await service.sync_if_due()

    assert result["reason"] == "llm_paused"
    assert client.nav_calls == 1
    assert memory.state["last_sync_error_kind"] == ""
    assert memory.state["last_sync_issues"] == [{"stage": "profile_analysis", "kind": "timeout"}]


async def test_paused_tick_keeps_auth_expired_when_nav_logged_out() -> None:
    """A definitive 'still logged out' keeps the issue — it is not stale."""
    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _auth_expired_memory()
    client = _NavProbeClient(history_items=[], favorites=[], following=[], nav_login=False)
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=client,
        soul_engine=_FakeSoulEngine(),
        llm_work_allowed=lambda: False,
    )

    result = await service.sync_if_due()

    assert result["reason"] == "llm_paused"
    assert memory.state["last_sync_error_kind"] == "auth_expired"


async def test_paused_tick_keeps_auth_expired_when_probe_unreachable() -> None:
    """A transport failure is no verdict either way; state stays untouched."""
    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _auth_expired_memory()
    client = _NavProbeClient(
        history_items=[],
        favorites=[],
        following=[],
        nav_error=httpx.ConnectError("proxy down"),
    )
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=client,
        soul_engine=_FakeSoulEngine(),
        llm_work_allowed=lambda: False,
    )

    result = await service.sync_if_due()

    assert result["reason"] == "llm_paused"
    assert memory.state["last_sync_error_kind"] == "auth_expired"


async def test_paused_tick_without_auth_expired_does_not_probe() -> None:
    """No persisted auth_expired → no network call: the 5-minute poll loop
    must not probe bilibili on every paused tick."""
    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _FakeMemoryManager()
    client = _NavProbeClient(history_items=[], favorites=[], following=[])
    service = AccountSyncService(
        memory_manager=memory,
        bilibili_client=client,
        soul_engine=_FakeSoulEngine(),
        llm_work_allowed=lambda: False,
    )

    result = await service.sync_if_due()

    assert result["reason"] == "llm_paused"
    assert client.nav_calls == 0
