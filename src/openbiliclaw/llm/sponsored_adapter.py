"""Registry presence/readiness adapter for installations without user model keys."""

from __future__ import annotations

from typing import Any

from .base import LLMProvider, LLMProviderError, LLMResponse
from .sponsored_errors import SponsoredError, is_fallback_allowed
from .sponsored_router import shared_sponsored_provider, sponsored_bypassed


class SponsoredChatAdapter(LLMProvider):
    # The frontend FC interface is implemented using the project simulation adapter.
    supports_tool_calling = True

    @property
    def name(self) -> str:
        return "sponsored"

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
        reasoning_effort: str | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        if sponsored_bypassed():
            raise LLMProviderError("sponsored already attempted; using configured fallback")
        if model:
            raise LLMProviderError("sponsored models are selected by signed operation policy")
        provider = shared_sponsored_provider()
        if provider is None:
            raise LLMProviderError("sponsored runtime unavailable")
        try:
            return await provider.complete(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=json_mode,
                reasoning_effort=reasoning_effort,
                caller="registry.adapter",
            )
        except SponsoredError as exc:
            if is_fallback_allowed(exc):
                raise LLMProviderError(f"sponsored unavailable: {exc.code}") from exc
            raise

    async def complete_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        if sponsored_bypassed() or model:
            raise LLMProviderError("sponsored already attempted or model override requested")
        provider = shared_sponsored_provider()
        if provider is None:
            raise LLMProviderError("sponsored runtime unavailable")
        try:
            return await provider.complete_with_tools(
                messages,
                tools,
                temperature=temperature,
                max_tokens=max_tokens,
                reasoning_effort=reasoning_effort,
                caller="registry.tools",
            )
        except SponsoredError as exc:
            if is_fallback_allowed(exc):
                raise LLMProviderError(f"sponsored unavailable: {exc.code}") from exc
            raise
