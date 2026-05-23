"""Runtime event broadcasting for popup live status updates."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RuntimeEventHub:
    """Broadcast lightweight runtime events to interested subscribers."""

    _subscribers: set[asyncio.Queue[dict[str, Any]]] = field(default_factory=set)

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        """Register one subscriber queue."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        """Remove one subscriber queue."""
        self._subscribers.discard(queue)

    async def publish(self, event: dict[str, Any]) -> bool:
        """Fan out one event to all current subscribers.

        Returns ``True`` when at least one subscriber queue accepted the
        event.  Callers that need delivery-sensitive side effects can avoid
        marking one-shot events as consumed when no runtime stream is open.
        """
        delivered = False
        for queue in list(self._subscribers):
            with suppress(asyncio.QueueFull):
                queue.put_nowait(dict(event))
                delivered = True
        return delivered
