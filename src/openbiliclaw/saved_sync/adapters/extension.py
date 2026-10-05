"""Native-save adapters backed by the durable browser-extension broker."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..models import NativeSaveAction, NativeSaveCapability, NativeSaveResult

if TYPE_CHECKING:
    from ..extension_broker import ExtensionNativeSaveBroker
    from ..models import NativeSaveRoute, SavedItemInput


@dataclass(frozen=True, slots=True)
class ExtensionAdapterDefinition:
    """Static capability and target contract for one extension platform."""

    platform: str
    platform_slug: str
    favorite_target: str
    watch_later_target: str = ""
    supports_named_collection: bool = False


class ExtensionNativeSaveAdapter:
    """Delegate one platform's native save to the durable extension broker."""

    def __init__(
        self,
        definition: ExtensionAdapterDefinition,
        broker: ExtensionNativeSaveBroker,
    ) -> None:
        self._definition = definition
        self._broker = broker

    @property
    def capability(self) -> NativeSaveCapability:
        return NativeSaveCapability(
            platform=self._definition.platform,
            supports_favorite=True,
            supports_watch_later=bool(self._definition.watch_later_target),
            supports_named_collection=self._definition.supports_named_collection,
            requires_extension=True,
        )

    def target_label(self, action: NativeSaveAction) -> str:
        if action == "watch_later" and self._definition.watch_later_target:
            return self._definition.watch_later_target
        return self._definition.favorite_target

    async def save(self, item: SavedItemInput, route: NativeSaveRoute) -> NativeSaveResult:
        if item.platform != self._definition.platform:
            raise ValueError("saved item platform does not match extension adapter")
        return await self._broker.save(item, route)


_EXTENSION_ADAPTER_DEFINITIONS = (
    ExtensionAdapterDefinition("youtube", "yt", "OpenBiliClaw", "YouTube Watch Later", True),
    ExtensionAdapterDefinition("xiaohongshu", "xhs", "小红书收藏"),
    ExtensionAdapterDefinition("douyin", "dy", "抖音收藏"),
    ExtensionAdapterDefinition("twitter", "x", "X Bookmarks"),
    ExtensionAdapterDefinition("zhihu", "zhihu", "知乎收藏"),
    ExtensionAdapterDefinition("reddit", "reddit", "Reddit Saved"),
)


def build_extension_native_save_adapters(
    broker: ExtensionNativeSaveBroker,
) -> tuple[ExtensionNativeSaveAdapter, ...]:
    """Return the six production extension adapters in canonical source order."""
    return tuple(
        ExtensionNativeSaveAdapter(definition, broker)
        for definition in _EXTENSION_ADAPTER_DEFINITIONS
    )
