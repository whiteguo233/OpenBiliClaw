from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock

import pytest

from openbiliclaw.saved_sync.adapters.extension import (
    ExtensionNativeSaveAdapter,
    build_extension_native_save_adapters,
)
from openbiliclaw.saved_sync.extension_broker import ExtensionNativeSaveBroker
from openbiliclaw.saved_sync.models import NativeSaveAction, SavedItemInput
from openbiliclaw.saved_sync.router import NativeSaveRouter
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def broker() -> AsyncMock:
    return AsyncMock()


@pytest.mark.parametrize(
    ("platform", "intent", "resolved", "target"),
    [
        ("youtube", "favorite", "favorite", "OpenBiliClaw"),
        ("youtube", "watch_later", "watch_later", "YouTube Watch Later"),
        ("xiaohongshu", "watch_later", "favorite", "小红书收藏"),
        ("douyin", "watch_later", "favorite", "抖音收藏"),
        ("twitter", "watch_later", "favorite", "X Bookmarks"),
        ("zhihu", "watch_later", "favorite", "知乎收藏"),
        ("reddit", "favorite", "favorite", "Reddit Saved"),
    ],
)
def test_extension_adapter_route_matrix(
    platform: str,
    intent: str,
    resolved: str,
    target: str,
    broker: AsyncMock,
) -> None:
    router = NativeSaveRouter(build_extension_native_save_adapters(broker))

    adapter, route = router.route(platform, cast("NativeSaveAction", intent))

    assert adapter.capability.requires_extension is True
    assert route.resolved_action == resolved
    assert route.resolved_target == target


def test_extension_adapter_factory_is_ordered_and_immutable(broker: AsyncMock) -> None:
    adapters = build_extension_native_save_adapters(broker)

    assert isinstance(adapters, tuple)
    assert [adapter.capability.platform for adapter in adapters] == [
        "youtube",
        "xiaohongshu",
        "douyin",
        "twitter",
        "zhihu",
        "reddit",
    ]
    assert [adapter.capability.supports_named_collection for adapter in adapters] == [
        True,
        False,
        False,
        False,
        False,
        False,
    ]


async def test_extension_adapter_delegates_to_broker(broker: AsyncMock) -> None:
    broker.save.return_value = object()
    adapter = build_extension_native_save_adapters(broker)[0]
    item = SavedItemInput("youtube", "video-1")
    _, route = NativeSaveRouter([adapter]).route("youtube", "favorite")

    result = await adapter.save(item, route)

    assert result is broker.save.return_value
    broker.save.assert_awaited_once_with(item, route)


async def test_extension_adapter_rejects_cross_platform_item_before_enqueue(
    broker: AsyncMock,
) -> None:
    adapter: ExtensionNativeSaveAdapter = build_extension_native_save_adapters(broker)[0]
    item = SavedItemInput("reddit", "post-1")
    _, route = NativeSaveRouter([adapter]).route("youtube", "favorite")

    with pytest.raises(ValueError, match="platform does not match"):
        await adapter.save(item, route)

    broker.save.assert_not_awaited()


@pytest.mark.parametrize(
    ("platform", "platform_slug", "content_id", "content_url"),
    [
        ("youtube", "yt", "video-1", "https://www.youtube.com/watch?v=video-1"),
        (
            "xiaohongshu",
            "xhs",
            "note-1",
            "https://www.xiaohongshu.com/explore/note-1",
        ),
        ("douyin", "dy", "video-2", "https://www.douyin.com/video/video-2"),
        ("twitter", "x", "123", "https://x.com/example/status/123"),
        ("zhihu", "zhihu", "456", "https://www.zhihu.com/question/456"),
        ("reddit", "reddit", "t3_abc", "https://www.reddit.com/comments/abc/demo/"),
    ],
)
async def test_adapter_definition_slug_matches_broker_wake_slug(
    tmp_path: Path,
    platform: str,
    platform_slug: str,
    content_id: str,
    content_url: str,
) -> None:
    database = Database(tmp_path / f"{platform}-wake.db")
    database.initialize()
    wake_slugs: list[str] = []

    async def wake(slug: str) -> None:
        wake_slugs.append(slug)

    actual_broker = ExtensionNativeSaveBroker(
        database,
        wake_platform=wake,
        dispatch_deadline_seconds=0.005,
        execution_deadline_seconds=0.01,
        poll_interval_seconds=0.001,
    )
    adapter, route = NativeSaveRouter(build_extension_native_save_adapters(actual_broker)).route(
        platform, "favorite"
    )

    await adapter.save(SavedItemInput(platform, content_id, content_url), route)

    definition = cast("ExtensionNativeSaveAdapter", adapter)._definition
    assert definition.platform_slug == platform_slug
    assert wake_slugs == [platform_slug]
