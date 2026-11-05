"""Failure paths must exercise free-model recovery without replaying output."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from openbiliclaw.llm.base import LLMResponse, LLMStreamChunk
from openbiliclaw.llm.sponsored_errors import (
    SponsoredError,
    SponsoredRateLimitedError,
    error_from_payload,
)
from openbiliclaw.llm.sponsored_profiles import approved_registry
from openbiliclaw.llm.sponsored_provider import SponsoredProvider


async def test_silent_selected_stream_leaves_time_for_the_other_free_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "openbiliclaw.llm.sponsored_provider.INTERACTIVE_FIRST_DELTA_TIMEOUT_SECONDS", 0.08
    )
    attempts: list[str] = []
    closed: list[str] = []

    class SilentPrimary(SponsoredProvider):
        async def _ensure_started(self) -> None:
            self._streaming = True

        async def _stream_task_once(self, contract: Any, payload: Any) -> Any:
            attempts.append(contract.model)
            try:
                if contract.model == "Qwen/Qwen3-8B":
                    await asyncio.Event().wait()
                yield LLMStreamChunk(delta="recovered")
                yield LLMStreamChunk(
                    response=LLMResponse(content="recovered", model=contract.model)
                )
            finally:
                closed.append(contract.model)

    sdk = SilentPrimary(["unused"], registry=approved_registry())
    start = asyncio.get_running_loop().time()
    chunks = [
        chunk
        async for chunk in sdk.stream_complete(
            [{"role": "user", "content": "hello"}], caller="agent.chat"
        )
    ]
    assert attempts == ["Qwen/Qwen3-8B", "Qwen/Qwen3.5-4B"]
    assert closed == attempts
    assert "".join(chunk.delta for chunk in chunks) == "recovered"
    assert chunks[-1].response.raw["free_model_fallback"] is True
    assert asyncio.get_running_loop().time() - start < 0.16


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    "code", ["AUTH_FAILED", "POLICY_INVALID", "BALANCE_LOW", "QUOTA_EXCEEDED", "LEDGER_TAMPERED"]
)
async def test_shared_failure_does_not_try_a_second_model(streaming: bool, code: str) -> None:
    attempts: list[str] = []

    class InvalidKey(SponsoredProvider):
        async def _ensure_started(self) -> None:
            self._streaming = True

        async def _execute_task_once(self, **kwargs: Any) -> LLMResponse:
            attempts.append(kwargs["task_id"])
            raise error_from_payload({"code": code})

        async def _stream_task_once(self, contract: Any, payload: Any) -> Any:
            attempts.append(contract.task_id)
            raise error_from_payload({"code": code})
            yield

    sdk = InvalidKey(["unused"], registry=approved_registry())
    with pytest.raises(SponsoredError) as error:
        if streaming:
            async for _ in sdk.stream_complete(
                [{"role": "user", "content": "hello"}], caller="agent.chat"
            ):
                pass
        else:
            await sdk.complete([{"role": "user", "content": "hello"}], caller="agent.chat")
    assert len(attempts) == 1
    assert error.value.code == code


async def test_stream_429_retries_once_then_uses_the_other_free_model() -> None:
    attempts: list[str] = []

    class RateLimitedPrimary(SponsoredProvider):
        async def _ensure_started(self) -> None:
            self._streaming = True

        async def _stream_task_once(self, contract: Any, payload: Any) -> Any:
            attempts.append(contract.model)
            if contract.model == "Qwen/Qwen3-8B":
                raise SponsoredRateLimitedError("429")
            yield LLMStreamChunk(response=LLMResponse(content="ok", model=contract.model))

    sdk = RateLimitedPrimary(["unused"], registry=approved_registry())
    chunks = [
        c
        async for c in sdk.stream_complete([{"role": "user", "content": "hi"}], caller="agent.chat")
    ]
    assert attempts == ["Qwen/Qwen3-8B", "Qwen/Qwen3-8B", "Qwen/Qwen3.5-4B"]
    assert chunks[-1].response.content == "ok"
