"""Selectable signed profiles, real IPC streaming and free-only fallback contracts."""

from __future__ import annotations

import shlex
import sys
from dataclasses import replace
from typing import Any

import pytest

from openbiliclaw.config import Config, _collect_config_issues
from openbiliclaw.llm.base import LLMProvider, LLMRegistry, LLMResponse
from openbiliclaw.llm.registry import build_llm_registry
from openbiliclaw.llm.service import LLMService
from openbiliclaw.llm.sponsored_errors import (
    SponsoredRequestTooLargeError,
    SponsoredUnavailableError,
)
from openbiliclaw.llm.sponsored_profiles import (
    ALTERNATE_CHAT_MODEL,
    CHAT_MODELS,
    DEFAULT_CHAT_MODEL,
    EMBEDDING_MODEL,
    approved_registry,
)
from openbiliclaw.llm.sponsored_provider import SponsoredProvider
from openbiliclaw.llm.sponsored_router import close_shared_provider, shared_sponsored_provider
from openbiliclaw.sponsored_runtime_state import (
    SponsoredRuntimeSettings,
    install_sponsored_settings,
    reset_sponsored_settings,
)

COMMAND = [
    sys.executable,
    "-m",
    "openbiliclaw.llm.sponsored_mock_runtime",
    "--approved",
    "--stream",
]
MESSAGES = [{"role": "user", "content": "只总结文本证据，不补充日期。"}]


@pytest.fixture(autouse=True)
async def clean_settings(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.delenv("OPENBILICLAW_SPONSORED_ENABLED", raising=False)
    reset_sponsored_settings()
    yield
    await close_shared_provider()
    reset_sponsored_settings()


def provider(model: str = DEFAULT_CHAT_MODEL, **kwargs: Any) -> SponsoredProvider:
    return SponsoredProvider(COMMAND, registry=approved_registry(), model=model, **kwargs)


def test_approved_profile_is_text_embedding_only_and_has_both_signed_models() -> None:
    contracts = list(approved_registry())
    assert len(contracts) == 10
    assert {c.model for c in contracts} == {*CHAT_MODELS, EMBEDDING_MODEL}
    assert {c.model for c in contracts if c.operation == "vision"} == {ALTERNATE_CHAT_MODEL}
    assert not any(c.operation == "embedding_image" for c in contracts)
    assert all(c.response_format == "text" for c in contracts if c.operation == "structured")
    assert (
        next(c for c in contracts if c.operation == "embedding_text").embedding_dimensions == 1024
    )


@pytest.mark.parametrize(
    ("model", "expected"), [(DEFAULT_CHAT_MODEL, "low"), (ALTERNATE_CHAT_MODEL, "")]
)
async def test_free_structured_business_calls_bound_implicit_thinking(
    model: str, expected: str
) -> None:
    """Production callers omit effort; upstream defaults can exhaust the timeout."""
    captured = []

    class Capture(SponsoredProvider):
        async def _execute_task_once(self, **kwargs: Any) -> LLMResponse:
            captured.append(kwargs["user_payload"])
            return LLMResponse(content='{"interests": []}', provider="sponsored", model=model)

    sdk = Capture(["unused"], registry=approved_registry(), model=model)
    service = LLMService(registry=LLMRegistry(), memory=None, sponsored_provider=sdk)
    await service.complete_structured_task(
        system_instruction="Return json preferences.",
        user_input="收藏了 Rust 所有权教程。",
        caller="soul.preference",
        max_tokens=16384,
    )
    assert captured[0]["reasoning_effort"] == expected
    await service.complete_structured_task(
        system_instruction="Return json preferences.",
        user_input="明确要求深入思考。",
        caller="soul.preference",
        reasoning_effort="high",
    )
    assert captured[1]["reasoning_effort"] == "high"


@pytest.mark.parametrize("model", CHAT_MODELS)
async def test_selected_model_streams_multiple_deltas_and_reports_actual_model(model: str) -> None:
    async with provider(model, env={"SPONSORED_MOCK_RESPONSE": "流式文本正常"}) as sdk:
        chunks = [chunk async for chunk in sdk.stream_complete(MESSAGES)]
        assert len([c for c in chunks if c.delta]) == 2
        assert "".join(c.delta for c in chunks) == "流式文本正常"
        response = chunks[-1].response
        assert response is not None and response.model == model
        assert response.raw["requested_model"] == model
        assert not response.raw["free_model_fallback"]
        assert len(await sdk.embedding("历史记录去重")) == 1024


@pytest.mark.parametrize(("max_tokens", "expected"), [(64, ""), (1024, ""), (2048, "low")])
async def test_implicit_thinking_preserves_a_small_final_answer_budget(
    max_tokens: int, expected: str
) -> None:
    captured = []

    class Capture(SponsoredProvider):
        async def _execute_task_once(self, **kwargs: Any) -> LLMResponse:
            captured.append(kwargs["user_payload"])
            return LLMResponse(content="{}", model=DEFAULT_CHAT_MODEL)

    sdk = Capture(["unused"], registry=approved_registry(), model=DEFAULT_CHAT_MODEL)
    await sdk.complete(MESSAGES, json_mode=True, max_tokens=max_tokens)
    assert captured[0]["reasoning_effort"] == expected
    assert captured[0]["max_tokens"] == max_tokens


async def test_free_agent_reply_streams_real_ipc_deltas_before_terminal() -> None:
    async with provider(env={"SPONSORED_MOCK_RESPONSE": "你的兴趣是编程和园艺。"}) as sdk:
        service = LLMService(registry=LLMRegistry(), memory=None, sponsored_provider=sdk)
        chunks = [
            chunk
            async for chunk in service.stream_complete_with_native_tools(
                messages=MESSAGES, tools=[], caller="agent.chat"
            )
        ]
    assert len([chunk for chunk in chunks if chunk.delta]) == 2
    assert "".join(chunk.delta for chunk in chunks) == "你的兴趣是编程和园艺。"
    assert chunks[-1].response is not None


async def test_free_agent_stream_keeps_tool_json_out_of_visible_text() -> None:
    content = '{"tool_call":{"name":"get_profile","arguments":{}}}'
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_profile",
                "description": "Read the user profile",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    async with provider(env={"SPONSORED_MOCK_RESPONSE": content}) as sdk:
        chunks = [chunk async for chunk in sdk.stream_complete_with_tools(MESSAGES, tools)]
    assert not any(chunk.delta for chunk in chunks)
    response = chunks[-1].response
    assert response is not None and response.content == "" and response.tool_calls
    assert response.tool_calls[0]["name"] == "get_profile"


async def test_free_agent_literal_json_answer_is_delivered_after_classification() -> None:
    content = '{"answer":"这只是用户要的 JSON 答案"}'
    async with provider(env={"SPONSORED_MOCK_RESPONSE": content}) as sdk:
        chunks = [chunk async for chunk in sdk.stream_complete_with_tools(MESSAGES, [])]
    assert "".join(chunk.delta for chunk in chunks) == content
    assert chunks[-1].response is not None
    assert not chunks[-1].response.tool_calls


async def test_free_fallback_recalculates_implicit_thinking_for_its_model() -> None:
    captured = []

    class Fallback(SponsoredProvider):
        async def _execute_task_once(self, **kwargs: Any) -> LLMResponse:
            captured.append(kwargs["user_payload"]["reasoning_effort"])
            if len(captured) == 1:
                raise SponsoredUnavailableError("transient upstream failure")
            return LLMResponse(content="{}", model=ALTERNATE_CHAT_MODEL)

    sdk = Fallback(["unused"], registry=approved_registry(), model=DEFAULT_CHAT_MODEL)
    await sdk.complete(MESSAGES, json_mode=True)
    assert captured == ["low", ""]


async def test_closing_free_agent_stream_releases_the_runtime_request() -> None:
    async with provider(env={"SPONSORED_MOCK_RESPONSE": "部分回复后关闭"}) as sdk:
        stream = sdk.stream_complete_with_tools(MESSAGES, [])
        assert (await anext(stream)).delta
        await stream.aclose()
        assert not sdk.runtime_running
        response = await sdk.complete(MESSAGES, reasoning_effort="")
        assert response.content == "部分回复后关闭"


async def test_free_agent_stream_disconnect_does_not_replay_after_visible_text() -> None:
    async with provider(
        env={
            "SPONSORED_MOCK_RESPONSE": "编程和园艺是你的兴趣。",
            "SPONSORED_MOCK_STREAM_ERROR_AFTER_DELTA": "1",
        }
    ) as sdk:
        chunks = []
        with pytest.raises(SponsoredUnavailableError):
            async for chunk in sdk.stream_complete_with_tools(MESSAGES, []):
                chunks.append(chunk)
        assert len(chunks) == 1 and chunks[0].delta
        assert not sdk.runtime_running


@pytest.mark.parametrize("stream", [False, True])
async def test_free_fallback_before_output_uses_other_signed_task(stream: bool) -> None:
    async with provider(env={"SPONSORED_MOCK_FAIL_MODEL": DEFAULT_CHAT_MODEL}) as sdk:
        if stream:
            chunks = [chunk async for chunk in sdk.stream_complete(MESSAGES)]
            response = chunks[-1].response
        else:
            response = await sdk.complete(MESSAGES)
        assert response is not None and response.model == ALTERNATE_CHAT_MODEL
        assert response.raw["free_model_fallback"] is True
        assert response.raw["requested_model"] == DEFAULT_CHAT_MODEL


async def test_stream_disconnect_after_output_is_not_replayed() -> None:
    async with provider(env={"SPONSORED_MOCK_STREAM_ERROR_AFTER_DELTA": "1"}) as sdk:
        deltas = []
        with pytest.raises(SponsoredUnavailableError):
            async for chunk in sdk.stream_complete(MESSAGES):
                deltas.append(chunk.delta)
        assert len(deltas) == 1
        assert not sdk.runtime_running


async def test_free_fallback_can_be_disabled() -> None:
    async with provider(
        fallback_to_other_free_model=False, env={"SPONSORED_MOCK_FAIL_MODEL": DEFAULT_CHAT_MODEL}
    ) as sdk:
        with pytest.raises(SponsoredUnavailableError):
            await sdk.complete(MESSAGES)


async def test_context_overflow_switches_to_larger_approved_window_without_clipping() -> None:
    messages = [{"role": "user", "content": "a" * 135000}]
    async with provider() as sdk:
        result = await sdk.complete(messages, max_tokens=128)
        assert result.model == ALTERNATE_CHAT_MODEL
        assert len(messages[0]["content"]) == 135000
    async with provider(fallback_to_other_free_model=False) as sdk:
        with pytest.raises(SponsoredRequestTooLargeError):
            await sdk.complete(messages, max_tokens=128)


async def test_hot_model_selection_changes_next_request_but_keeps_embedding_space() -> None:
    settings = SponsoredRuntimeSettings(enabled=True, runtime_path=shlex.join(COMMAND))
    install_sponsored_settings(settings)
    first = shared_sponsored_provider()
    assert first is not None
    assert (await first.complete(MESSAGES)).model == DEFAULT_CHAT_MODEL
    vector = await first.embedding("兴趣")
    install_sponsored_settings(replace(settings, model=ALTERNATE_CHAT_MODEL))
    second = shared_sponsored_provider()
    assert second is not None and second is not first
    assert (await second.complete(MESSAGES)).model == ALTERNATE_CHAT_MODEL
    assert await second.embedding("兴趣") == vector


async def test_default_free_scheme_cannot_charge_a_registered_user_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.llm import sponsored_router

    class Paid(LLMProvider):
        @property
        def name(self) -> str:
            return "openai"

        async def complete(self, *_args: Any, **_kwargs: Any) -> LLMResponse:
            raise AssertionError("paid provider must not run")

    class Down:
        async def complete(self, *_args: Any, **_kwargs: Any) -> LLMResponse:
            raise SponsoredUnavailableError("synthetic outage")

    cfg = Config()
    cfg.llm.sponsored = SponsoredRuntimeSettings(enabled=True, runtime_path=shlex.join(COMMAND))
    registry = build_llm_registry(cfg, provider_overrides={"openai": Paid()})
    assert registry._fallback_order() == ["sponsored"]
    monkeypatch.setattr(sponsored_router, "shared_sponsored_provider", lambda: Down())
    with pytest.raises(SponsoredUnavailableError):
        await registry.complete(MESSAGES)


def test_unknown_model_is_a_blocking_configuration_error() -> None:
    cfg = Config()
    cfg.llm.sponsored = SponsoredRuntimeSettings(model="unapproved/model")
    assert any(
        i.field == "llm.sponsored.model" and i.severity == "blocking"
        for i in _collect_config_issues(cfg)
    )
