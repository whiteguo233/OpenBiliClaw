"""SiliconFlow's protected transport behind the ordinary LLM instance interface."""

from __future__ import annotations

from contextlib import aclosing, contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, NoReturn, cast

from .base import (
    LLMAuthError,
    LLMProvider,
    LLMProviderError,
    LLMRateLimitError,
    LLMResponse,
    LLMStreamChunk,
)
from .sponsored_errors import SponsoredError, is_fallback_allowed
from .sponsored_profiles import CHAT_MODELS, DEFAULT_CHAT_MODEL
from .sponsored_router import shared_sponsored_provider

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Iterator

    from .sponsored_provider import SponsoredProvider

_CALLER: ContextVar[str] = ContextVar("siliconflow_caller", default="registry.direct")
_TASK: ContextVar[dict[str, Any] | None] = ContextVar("siliconflow_task", default=None)


@contextmanager
def sponsored_call(caller: str, *, task: dict[str, Any] | None = None) -> Iterator[None]:
    """Carry signed task input through ordinary routing without changing other adapters."""
    caller_token = _CALLER.set(caller)
    task_token = _TASK.set(task) if task is not None else None
    try:
        yield
    finally:
        _CALLER.reset(caller_token)
        if task_token is not None:
            _TASK.reset(task_token)


async def stream_in_sponsored_call(
    stream: AsyncIterator[LLMStreamChunk], caller: str
) -> AsyncGenerator[LLMStreamChunk, None]:
    """Scope request context to upstream work, never across a consumer yield."""
    try:
        while True:
            with sponsored_call(caller):
                try:
                    chunk = await anext(stream)
                except StopAsyncIteration:
                    return
            yield chunk
    finally:
        close = getattr(stream, "aclose", None)
        if close is not None:
            with sponsored_call(caller):
                await close()


def uses_sponsored_route(service: Any, caller: str) -> bool:
    """Apply small-model budgets only to the actual selected module chain."""
    from .base import LLMRegistry
    from .service import LLMService

    if isinstance(service, LLMService):
        if service.sponsored_provider is not None:
            return True
        registry = service.registry
        if not isinstance(registry, LLMRegistry):
            from .sponsored_router import sponsored_enabled

            return sponsored_enabled()
        chain = service._resolve_module_chain(caller)
        if chain is None:
            override = service._resolve_module_override(caller)
            chain = [override[0]] if override is not None else registry._fallback_order()
    elif isinstance(service, LLMRegistry):
        registry = service
        chain = registry._fallback_order()
    else:
        # Compatibility for standalone harnesses with explicitly installed SDK settings.
        from .sponsored_router import sponsored_enabled

        return sponsored_enabled()
    return any(registry.provider_type(item) == "siliconflow_free" for item in chain)


class SponsoredChatAdapter(LLMProvider):
    """One selected free model; ordering and fallback belong to LLMRegistry."""

    supports_tool_calling = True
    supports_image_input = False

    def __init__(self, *, model: str = DEFAULT_CHAT_MODEL, settings: Any = None) -> None:
        if model not in CHAT_MODELS:
            raise ValueError("请选择 Qwen3-8B 或 Qwen3.5-4B")
        self._model = model
        self._settings = settings

    @property
    def name(self) -> str:
        return "siliconflow_free"

    def _provider(self, model: str | None) -> SponsoredProvider:
        if model and model != self._model:
            raise LLMProviderError("请编辑实例模型，或在调用链中选择另一个免费实例")
        provider = shared_sponsored_provider(model=self._model, settings=self._settings)
        if provider is None:
            raise LLMProviderError("免费模型组件尚未就绪，请准备组件或更新安装包后重试")
        return provider

    @staticmethod
    def _raise(exc: SponsoredError) -> NoReturn:
        # Contract/security errors must never silently switch providers.
        if exc.code == "RATE_LIMITED":
            raise LLMRateLimitError("硅基流动免费实例被限流，请稍后重试") from exc
        if exc.code == "AUTH_FAILED":
            raise LLMAuthError(
                "免费方案凭据不可用，请更新版本或选择其他实例", provider_name="siliconflow_free"
            ) from exc
        if is_fallback_allowed(exc):
            raise LLMProviderError(f"硅基流动免费实例不可用：{exc.code}") from exc
        raise exc

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        provider = self._provider(model)
        try:
            task = _TASK.get()
            if task is not None:
                from .sponsored_tasks import DEFAULT_SPONSORED_REGISTRY

                if DEFAULT_SPONSORED_REGISTRY.resolve_caller(task["caller"]) is not None:
                    response = await provider.execute_task(**task)
                    response.provider = self.name
                    return response
            response = await provider.complete(
                cast("Any", messages), caller=_CALLER.get(), **kwargs
            )
            response.provider = self.name
            return response
        except SponsoredError as exc:
            self._raise(exc)

    async def complete_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        model: str | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        try:
            response = await self._provider(model).complete_with_tools(
                messages,
                tools,
                caller=_CALLER.get(),
                **kwargs,
            )
            response.provider = self.name
            return response
        except SponsoredError as exc:
            self._raise(exc)

    async def stream_complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[LLMStreamChunk]:
        try:
            async with aclosing(
                self._provider(model).stream_complete(
                    cast("Any", messages),
                    caller=_CALLER.get(),
                    **kwargs,
                )
            ) as stream:
                async for chunk in stream:
                    if chunk.response is not None:
                        chunk.response.provider = self.name
                    yield chunk
        except SponsoredError as exc:
            self._raise(exc)

    async def stream_complete_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        model: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[LLMStreamChunk]:
        try:
            async with aclosing(
                self._provider(model).stream_complete_with_tools(
                    messages,
                    tools,
                    caller=_CALLER.get(),
                    **kwargs,
                )
            ) as stream:
                async for chunk in stream:
                    if chunk.response is not None:
                        chunk.response.provider = self.name
                    yield chunk
        except SponsoredError as exc:
            self._raise(exc)
