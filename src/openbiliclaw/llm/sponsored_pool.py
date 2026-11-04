"""Bounded IPC workers with a channel reserved for interactive requests."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from .concurrency import is_interactive_caller
from .sponsored_errors import SponsoredStartupBusyError
from .sponsored_provider import SponsoredProvider

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator

    from .base import LLMResponse, LLMStreamChunk


class SponsoredProviderPool(SponsoredProvider):
    """Share three serial Rust children, preserving quota and request isolation.

    The runtime-wide LLM gate still bounds admitted calls. Two children serve
    background/embedding traffic; one remains available for chat and probes.
    Each child uses the same signed policy and installation quota ledger.
    """

    def __init__(self, *args: Any, startup_lock: asyncio.Lock, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._startup_lock = startup_lock
        self._children = [SponsoredProvider(*args, **kwargs) for _ in range(3)]
        self._background: asyncio.Queue[SponsoredProvider] = asyncio.Queue()
        self._interactive: asyncio.Queue[SponsoredProvider] = asyncio.Queue()
        for child in self._children[:2]:
            self._background.put_nowait(child)
        self._interactive.put_nowait(self._children[2])

    @property
    def runtime_running(self) -> bool:
        return any(child.runtime_running for child in self._children)

    @property
    def policy_version(self) -> int:
        return max(child.policy_version for child in self._children)

    @asynccontextmanager
    async def _worker(self, caller: str) -> AsyncIterator[SponsoredProvider]:
        queue = self._interactive if is_interactive_caller(caller) else self._background
        child = await queue.get()
        try:
            # Cold children initialize the shared ledger/keystore. Serialize
            # handshakes only; never hold this lock during upstream inference.
            if not child.runtime_running:
                async with self._startup_lock:
                    for attempt in range(3):
                        try:
                            await child._ensure_started()
                            break
                        except SponsoredStartupBusyError:
                            if attempt == 2:
                                raise
                            # Compatibility with already-installed SDKs. A
                            # known ledger collision happens before any HTTP
                            # request; every other startup failure propagates.
                            await asyncio.sleep(0.05 * (attempt + 1))
            yield child
        finally:
            queue.put_nowait(child)

    async def execute_task(self, **kwargs: Any) -> LLMResponse:
        payload = kwargs.get("user_payload", {})
        caller = str(kwargs.get("caller") or payload.get("caller") or "")
        async with self._worker(caller) as child:
            return await child.execute_task(**kwargs)

    async def stream_complete(
        self, messages: list[dict[str, Any]], **kwargs: Any
    ) -> AsyncGenerator[LLMStreamChunk, None]:
        async with self._worker(str(kwargs.get("caller") or "")) as child:
            stream = child.stream_complete(messages, **kwargs)
            try:
                async for chunk in stream:
                    yield chunk
            finally:
                await stream.aclose()

    async def stream_complete_with_tools(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], **kwargs: Any
    ) -> AsyncGenerator[LLMStreamChunk, None]:
        async with self._worker(str(kwargs.get("caller") or "")) as child:
            stream = child.stream_complete_with_tools(messages, tools, **kwargs)
            try:
                async for chunk in stream:
                    yield chunk
            finally:
                await stream.aclose()

    async def aclose(self) -> None:
        await asyncio.gather(*(child.aclose() for child in self._children))
