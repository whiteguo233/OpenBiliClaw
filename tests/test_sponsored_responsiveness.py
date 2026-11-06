"""Foreground free-model work must not wait for a background IPC request."""

from __future__ import annotations

import asyncio
import shlex
import sys
from typing import Any

import pytest

from openbiliclaw.llm.base import LLMRegistry, LLMResponse, LLMStreamChunk
from openbiliclaw.llm.concurrency import LLMConcurrencyGate
from openbiliclaw.llm.service import LLMService
from openbiliclaw.llm.sponsored_errors import (
    SponsoredContractMismatchError,
    SponsoredProtocolError,
    SponsoredQuotaExceededError,
    SponsoredUnavailableError,
    SponsoredUpstreamError,
)
from openbiliclaw.llm.sponsored_profiles import approved_registry
from openbiliclaw.llm.sponsored_provider import SponsoredProvider
from openbiliclaw.llm.sponsored_router import (
    close_shared_provider,
    shared_sponsored_provider,
    stream_project_completion,
    try_sponsored_completion,
)
from openbiliclaw.sponsored_runtime_state import (
    SponsoredRuntimeSettings,
    current_sponsored_settings,
    install_sponsored_settings,
)


async def test_foreground_request_completes_while_background_ipc_is_busy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous = current_sponsored_settings()
    install_sponsored_settings(
        SponsoredRuntimeSettings(
            enabled=True, runtime_path="unused", fallback_to_other_free_model=False
        )
    )
    entered = asyncio.Event()
    release = asyncio.Event()

    async def execute(self: SponsoredProvider, **kwargs: Any) -> LLMResponse:
        # Model the actual child's serial IPC boundary, without network timing.
        async with self._request_lock:
            if kwargs["user_payload"].get("caller") == "discovery.evaluate_batch":
                entered.set()
                await release.wait()
            return LLMResponse(content="ok", provider="sponsored", model="Qwen/Qwen3-8B")

    monkeypatch.setattr(SponsoredProvider, "_execute_task_once", execute)

    async def started(self: SponsoredProvider) -> None:
        pass

    monkeypatch.setattr(SponsoredProvider, "_ensure_started", started)
    background = asyncio.create_task(
        try_sponsored_completion(
            [{"role": "user", "content": "evaluate candidates"}], caller="discovery.evaluate_batch"
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 1)
        response = await asyncio.wait_for(
            try_sponsored_completion([{"role": "user", "content": "hello"}], caller="agent.chat"),
            0.25,
        )
        assert response is not None and response.content == "ok"
        assert not background.done()
    finally:
        release.set()
        await background
        await close_shared_provider()
        install_sponsored_settings(previous)


async def test_closing_routed_stream_releases_the_reserved_worker() -> None:
    previous = current_sponsored_settings()
    install_sponsored_settings(
        SponsoredRuntimeSettings(
            enabled=True,
            runtime_path=shlex.join(
                [
                    sys.executable,
                    "-m",
                    "openbiliclaw.llm.sponsored_mock_runtime",
                    "--approved",
                    "--stream",
                ]
            ),
        )
    )

    async def unused() -> Any:
        raise AssertionError("paid fallback must never be used")
        yield

    stream = stream_project_completion(
        [{"role": "user", "content": "hi"}], fallback=unused, caller="agent.chat"
    )
    try:
        first = await anext(stream)
        assert first.delta
        await stream.aclose()
        response = await asyncio.wait_for(
            try_sponsored_completion([{"role": "user", "content": "next"}], caller="agent.chat"), 3
        )
        assert response is not None and response.provider == "sponsored"
        provider = shared_sponsored_provider()
        assert provider is not None and provider.policy_version > 0
    finally:
        await stream.aclose()
        await close_shared_provider()
        install_sponsored_settings(previous)


async def test_fixed_consolidation_respects_the_runtime_total_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def execute(self: SponsoredProvider, **kwargs: Any) -> LLMResponse:
        return LLMResponse(content='{"likes":[],"dislikes":[]}', provider="sponsored")

    monkeypatch.setattr(SponsoredProvider, "_execute_task_once", execute)
    gate = LLMConcurrencyGate(1)
    service = LLMService(
        registry=LLMRegistry(),
        memory=None,
        concurrency_gate=gate,
        sponsored_provider=SponsoredProvider(["unused"]),
    )
    async with gate.slot(caller="agent.chat"):
        pending = asyncio.create_task(
            service.execute_sponsored_task(
                caller="soul.consolidation",
                payload={"likes_clusters": [], "dislikes_clusters": []},
                fallback_system_instruction="unused",
                fallback_user_input="unused",
            )
        )
        done, _ = await asyncio.wait({pending}, timeout=0.05)
    try:
        assert not done, "fixed tasks must not bypass the application's total concurrency cap"
    finally:
        await pending
        await service.aclose()


async def test_transient_stream_failure_before_output_retries_same_selected_model() -> None:
    attempts = []

    class InterruptedOnce(SponsoredProvider):
        async def _ensure_started(self) -> None:
            self._streaming = True

        async def _stream_task_once(self, contract: Any, payload: Any) -> Any:
            attempts.append(contract.model)
            if len(attempts) == 1:
                raise SponsoredUpstreamError("stream ended without DONE")
            yield LLMStreamChunk(delta="ok")
            yield LLMStreamChunk(response=LLMResponse(content="ok", model=contract.model))

    sdk = InterruptedOnce(
        ["unused"], registry=approved_registry(), fallback_to_other_free_model=False
    )
    chunks = [
        chunk
        async for chunk in sdk.stream_complete(
            [{"role": "user", "content": "hello"}], caller="agent.chat"
        )
    ]
    assert attempts == ["Qwen/Qwen3-8B"] * 2
    assert "".join(chunk.delta for chunk in chunks) == "ok"


@pytest.mark.parametrize("error", [SponsoredContractMismatchError, SponsoredQuotaExceededError])
async def test_stream_does_not_retry_contract_or_quota_failures(error: Any) -> None:
    attempts = []

    class Rejected(SponsoredProvider):
        async def _ensure_started(self) -> None:
            self._streaming = True

        async def _stream_task_once(self, contract: Any, payload: Any) -> Any:
            attempts.append(contract.model)
            raise error("rejected")
            yield

    sdk = Rejected(["unused"], registry=approved_registry(), fallback_to_other_free_model=False)
    with pytest.raises(error):
        async for _ in sdk.stream_complete(
            [{"role": "user", "content": "hi"}], caller="agent.chat"
        ):
            pass
    assert len(attempts) == 1


async def test_short_interactive_stream_bounds_silence_before_first_delta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "openbiliclaw.llm.sponsored_provider.INTERACTIVE_FIRST_DELTA_TIMEOUT_SECONDS", 0.03
    )
    sdk = SponsoredProvider(
        [sys.executable, "-m", "openbiliclaw.llm.sponsored_mock_runtime", "--approved", "--stream"],
        registry=approved_registry(),
        request_timeout=5,
        env={"SPONSORED_MOCK_DELAY_SECONDS": "2"},
        fallback_to_other_free_model=False,
    )
    try:
        async with asyncio.timeout(2):
            with pytest.raises(SponsoredUnavailableError, match="timed out"):
                async for _ in sdk.stream_complete(
                    [{"role": "user", "content": "hi"}], caller="agent.chat"
                ):
                    pass
        assert not sdk.runtime_running
    finally:
        await sdk.aclose()


async def test_first_delta_deadline_is_not_reset_by_a_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "openbiliclaw.llm.sponsored_provider.INTERACTIVE_FIRST_DELTA_TIMEOUT_SECONDS", 0.04
    )

    class SlowRetry(SponsoredProvider):
        async def _ensure_started(self) -> None:
            self._streaming = True

        async def _stream_task_once(self, contract: Any, payload: Any) -> Any:
            await asyncio.sleep(0.025)
            if not getattr(self, "retried", False):
                self.retried = True
                raise SponsoredUpstreamError("transient silence")
            yield LLMStreamChunk(delta="late")

    sdk = SlowRetry(["unused"], registry=approved_registry(), fallback_to_other_free_model=False)
    start = asyncio.get_running_loop().time()
    with pytest.raises(SponsoredUnavailableError, match="first.*timed out"):
        async for _ in sdk.stream_complete(
            [{"role": "user", "content": "hi"}], caller="agent.chat"
        ):
            pass
    assert asyncio.get_running_loop().time() - start < 0.15


async def test_pool_retries_only_a_known_legacy_ledger_startup_collision(
    tmp_path: Any,
) -> None:
    from openbiliclaw.llm.sponsored_pool import SponsoredProviderPool

    wrapper = tmp_path / "start_once.py"
    marker = tmp_path / "started"
    wrapper.write_text(
        "import pathlib, runpy, sys\n"
        f"p = pathlib.Path({str(marker)!r})\n"
        "if not p.exists():\n"
        "    p.touch()\n"
        "    print('UNAVAILABLE: another runtime owns this installation ledger', file=sys.stderr)\n"
        "    sys.exit(1)\n"
        "runpy.run_module('openbiliclaw.llm.sponsored_mock_runtime', run_name='__main__')\n"
    )
    sdk = SponsoredProviderPool(
        [sys.executable, str(wrapper), "--approved", "--stream"],
        registry=approved_registry(),
        startup_lock=asyncio.Lock(),
        fallback_to_other_free_model=False,
    )
    try:
        result = await sdk.complete([{"role": "user", "content": "hi"}], caller="agent.chat")
        assert result.provider == "sponsored"
    finally:
        await sdk.aclose()


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("repair_valid", [False, True])
async def test_malformed_free_tool_json_gets_one_validated_repair(
    streaming: bool, repair_valid: bool
) -> None:
    broken = '<tool_call>{"name": "get_profile", "arguments": {}}]</tool_call>'
    fixed = '<tool_call>{"name": "get_profile", "arguments": {}}</tool_call>'
    requests = []

    class BrokenToolReply(SponsoredProvider):
        async def _ensure_started(self) -> None:
            self._streaming = True

        async def _execute_task_once(self, **kwargs: Any) -> LLMResponse:
            requests.append(kwargs["user_payload"])
            content = fixed if repair_valid and len(requests) == 2 else broken
            return LLMResponse(content=content, usage={"completion_tokens": 10})

        async def _stream_task_once(self, contract: Any, payload: Any) -> Any:
            requests.append(payload)
            yield LLMStreamChunk(delta=broken)
            yield LLMStreamChunk(
                response=LLMResponse(content=broken, usage={"completion_tokens": 10})
            )

    sdk = BrokenToolReply(["unused"], registry=approved_registry())
    tools = [{"type": "function", "function": {"name": "get_profile", "parameters": {}}}]
    deltas = []

    async def run() -> LLMResponse:
        if not streaming:
            return await sdk.complete_with_tools(
                [{"role": "user", "content": "查询画像"}], tools, caller="agent.chat"
            )
        response = None
        async for chunk in sdk.stream_complete_with_tools(
            [{"role": "user", "content": "查询画像"}], tools, caller="agent.chat"
        ):
            deltas.append(chunk.delta)
            response = chunk.response or response
        assert response is not None
        return response

    if repair_valid:
        response = await run()
        assert response.tool_calls and response.tool_calls[0]["name"] == "get_profile"
        assert response.content == ""
        assert response.usage == {"completion_tokens": 20}
    else:
        with pytest.raises(SponsoredProtocolError, match="tool"):
            await run()
    assert len(requests) == 2
    assert not any(deltas)
