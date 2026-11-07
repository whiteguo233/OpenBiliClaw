"""Bounded runtime workers shared by service, raw registry and embedding."""

from __future__ import annotations

import asyncio
import os
import shlex
import weakref
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from .base import LLMResponse, LLMStreamChunk
    from .sponsored_provider import SponsoredProvider

from openbiliclaw.sponsored_runtime_state import current_sponsored_settings

from .sponsored_errors import SponsoredError, SponsoredUnavailableError, is_fallback_allowed

_BYPASS: ContextVar[bool] = ContextVar("sponsored_bypass", default=False)
_PROVIDERS: weakref.WeakKeyDictionary[Any, dict[tuple[str, float, str, bool], Any]] = (
    weakref.WeakKeyDictionary()
)
_STARTUP_LOCKS: weakref.WeakKeyDictionary[Any, asyncio.Lock] = weakref.WeakKeyDictionary()


@contextmanager
def bypass_sponsored() -> Any:
    token = _BYPASS.set(True)
    try:
        yield
    finally:
        _BYPASS.reset(token)


def sponsored_enabled() -> bool:
    return current_sponsored_settings().enabled or os.environ.get(
        "OPENBILICLAW_SPONSORED_ENABLED", ""
    ).lower() in {"true", "1", "yes", "on"}


def shared_sponsored_provider(
    *,
    model: str | None = None,
    settings: Any = None,
) -> SponsoredProvider | None:
    from dataclasses import replace

    from .sponsored_pool import SponsoredProviderPool
    from .sponsored_profiles import approved_registry

    settings = settings or current_sponsored_settings()
    if model is None and not settings.enabled and not sponsored_enabled():
        return None
    if model is not None:
        # An instance is one model. All cross-model fallback belongs to its chain.
        settings = replace(settings, model=model, fallback_to_other_free_model=False)
    if settings.relay_url:
        from openbiliclaw.sponsored_runtime_state import relay_device_path

        from .sponsored_relay_client import SponsoredRelayProvider

        cache = _PROVIDERS.setdefault(asyncio.get_running_loop(), {})
        relay_key = (
            f"relay:{settings.relay_url}:{relay_device_path()}",
            settings.request_timeout_seconds,
            settings.model,
            settings.fallback_to_other_free_model,
        )
        if relay_key not in cache:
            cache[relay_key] = SponsoredRelayProvider(
                settings.relay_url,
                relay_device_path(),
                registry=approved_registry(),
                request_timeout=settings.request_timeout_seconds,
                model=settings.model,
                fallback_to_other_free_model=settings.fallback_to_other_free_model,
            )
        return cast("SponsoredProvider", cache[relay_key])
    command = settings.runtime_path or os.environ.get("OPENBILICLAW_SPONSORED_RUNTIME", "")
    if not command.strip():
        from openbiliclaw.runtime.sponsored_bundle import installed_runtime_command

        command = installed_runtime_command()
        if not command:
            return None
    loop = asyncio.get_running_loop()
    cache = _PROVIDERS.setdefault(loop, {})
    key = (
        command,
        settings.request_timeout_seconds,
        settings.model,
        settings.fallback_to_other_free_model,
    )
    if key not in cache:
        try:
            args = shlex.split(command)
            cache[key] = SponsoredProviderPool(
                args,
                startup_lock=_STARTUP_LOCKS.setdefault(loop, asyncio.Lock()),
                registry=approved_registry(),
                request_timeout=settings.request_timeout_seconds,
                model=settings.model,
                fallback_to_other_free_model=settings.fallback_to_other_free_model,
            )
        except ValueError:
            return None
    return cast("SponsoredProvider", cache[key])


async def try_sponsored_completion(
    messages: list[dict[str, Any]], *, provider: Any = None, **kwargs: Any
) -> LLMResponse | None:
    """Return None only for disabled mode or an explicitly permitted availability fallback."""
    if _BYPASS.get() or (provider is None and not sponsored_enabled()):
        return None
    settings = current_sponsored_settings()
    try:
        provider = provider or shared_sponsored_provider()
        if provider is None:
            raise SponsoredUnavailableError("sponsored runtime is not configured")
        tools = kwargs.pop("tools", None)
        if tools is not None:
            kwargs.pop("json_mode", None)
            return cast(
                "LLMResponse", await provider.complete_with_tools(messages, tools, **kwargs)
            )
        return cast("LLMResponse", await provider.complete(messages, **kwargs))
    except SponsoredError as exc:
        if not is_fallback_allowed(exc) or not settings.fallback_to_user_provider:
            raise
        _notify_fallback(exc, caller=kwargs.get("caller", ""))
        return None


def _notify_fallback(exc: SponsoredError, *, caller: str) -> None:
    from openbiliclaw.diagnostics_alerts import record_diagnostics_alert

    if current_sponsored_settings().notify_on_fallback:
        record_diagnostics_alert(
            category="llm",
            code=f"sponsored_{exc.code.lower()}",
            message=f"Sponsored {caller} unavailable: {exc.code}",
            source="sponsored",
            severity="warning",
        )


async def close_shared_provider() -> None:
    for provider in _PROVIDERS.pop(asyncio.get_running_loop(), {}).values():
        await provider.aclose()


def sponsored_bypassed() -> bool:
    return _BYPASS.get()


async def complete_project_task(
    llm: Any,
    messages: list[dict[str, Any]],
    *,
    caller: str = "eval.raw",
    **kwargs: Any,
) -> LLMResponse:
    """Cover evaluation callers that may receive a bare provider instead of a registry."""
    response = await try_sponsored_completion(messages, caller=caller, **kwargs)
    if response is not None:
        return response
    with bypass_sponsored():
        return cast("LLMResponse", await llm.complete(messages, **kwargs))


async def stream_project_completion(
    messages: list[dict[str, Any]],
    *,
    fallback: Callable[[], AsyncIterator[LLMStreamChunk]],
    provider: Any = None,
    **kwargs: Any,
) -> AsyncIterator[LLMStreamChunk]:
    """Respect the main stream contract; fall back only before any emitted content."""
    emitted = False
    if not _BYPASS.get() and (provider is not None or sponsored_enabled()):
        settings = current_sponsored_settings()
        try:
            provider = provider or shared_sponsored_provider()
            if provider is None:
                raise SponsoredUnavailableError("sponsored runtime is not configured")
            sdk_kwargs = dict(kwargs)
            tools = sdk_kwargs.pop("tools", None)
            if tools is not None:
                sdk_kwargs.pop("json_mode", None)
                stream = provider.stream_complete_with_tools(messages, tools, **sdk_kwargs)
            else:
                stream = provider.stream_complete(messages, **sdk_kwargs)
            try:
                async for chunk in stream:
                    emitted = emitted or bool(chunk.delta) or chunk.response is not None
                    yield chunk
            finally:
                close = getattr(stream, "aclose", None)
                if close is not None:
                    await close()
            return
        except SponsoredError as exc:
            if emitted or not is_fallback_allowed(exc) or not settings.fallback_to_user_provider:
                raise
            _notify_fallback(exc, caller=kwargs.get("caller", ""))
    stream = fallback().__aiter__()
    try:
        while True:
            try:
                with bypass_sponsored():
                    chunk = await anext(stream)
            except StopAsyncIteration:
                break
            yield chunk
    finally:
        close = getattr(stream, "aclose", None)
        if close is not None:
            await close()
