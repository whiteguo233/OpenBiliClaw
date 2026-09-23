"""Tests for LLMService Sponsored routing and fallback semantics."""

from __future__ import annotations

import sys
from typing import Any

import pytest

from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.llm.service import LLMProviderExecutionError, LLMService
from openbiliclaw.llm.sponsored_provider import (
    SPONSORED_ENABLED_ENV,
    SPONSORED_RUNTIME_ENV,
    SponsoredProvider,
    build_sponsored_provider_from_env,
)
from openbiliclaw.llm.task_options import execute_sponsored_or_structured

MOCK_RUNTIME_COMMAND = (
    sys.executable,
    "-m",
    "openbiliclaw.llm.sponsored_mock_runtime",
)
VALID_PAYLOAD = {"likes_clusters": [], "dislikes_clusters": []}
VALID_USER_TEXT = "<likes_clusters>[]</likes_clusters>\n<dislikes_clusters>[]</dislikes_clusters>"


class FakeRegistry:
    def __init__(self, response: LLMResponse) -> None:
        self.response = response
        self.calls: list[list[dict[str, Any]]] = []

    async def complete(self, messages: list[dict[str, Any]], **_kwargs: Any) -> LLMResponse:
        self.calls.append(messages)
        return self.response


class FakeMemory:
    def render_core_memory_prompt(self) -> str:
        return ""


async def test_sponsored_path_bypasses_configured_registry() -> None:
    registry = FakeRegistry(LLMResponse(content="legacy", provider="openai"))
    provider = SponsoredProvider(MOCK_RUNTIME_COMMAND, request_timeout=15.0)
    service = LLMService(
        registry=registry,  # type: ignore[arg-type]
        memory=FakeMemory(),  # type: ignore[arg-type]
        sponsored_provider=provider,
    )
    try:
        response = await service.execute_sponsored_task(
            caller="soul.consolidation",
            payload=VALID_PAYLOAD,
            user_text=VALID_USER_TEXT,
            fallback_system_instruction="legacy system",
            fallback_user_input="legacy user",
        )
    finally:
        await service.aclose()

    assert response.provider == "sponsored"
    assert registry.calls == []


async def test_disabled_sponsored_falls_back_to_configured_registry() -> None:
    registry = FakeRegistry(LLMResponse(content="legacy", provider="openai"))
    service = LLMService(
        registry=registry,  # type: ignore[arg-type]
        memory=FakeMemory(),  # type: ignore[arg-type]
    )
    response = await service.execute_sponsored_task(
        caller="soul.consolidation",
        payload=VALID_PAYLOAD,
        user_text=VALID_USER_TEXT,
        fallback_system_instruction="legacy system",
        fallback_user_input="legacy user",
    )

    assert response.content == "legacy"
    assert len(registry.calls) == 1
    assert registry.calls[0][0] == {"role": "system", "content": "legacy system\n\njson"}


async def test_availability_error_falls_back_without_hiding_contract_bugs() -> None:
    registry = FakeRegistry(LLMResponse(content="legacy", provider="openai"))
    provider = SponsoredProvider(
        MOCK_RUNTIME_COMMAND,
        env={"SPONSORED_MOCK_ERROR_CODE": "QUOTA_EXCEEDED"},
        request_timeout=15.0,
    )
    service = LLMService(
        registry=registry,  # type: ignore[arg-type]
        memory=FakeMemory(),  # type: ignore[arg-type]
        sponsored_provider=provider,
    )
    try:
        response = await service.execute_sponsored_task(
            caller="soul.consolidation",
            payload=VALID_PAYLOAD,
            user_text=VALID_USER_TEXT,
            fallback_system_instruction="legacy system",
            fallback_user_input="legacy user",
        )
    finally:
        await service.aclose()

    assert response.content == "legacy"
    assert len(registry.calls) == 1


async def test_contract_mismatch_fails_loudly_and_does_not_spend_byok() -> None:
    registry = FakeRegistry(LLMResponse(content="legacy", provider="openai"))
    provider = SponsoredProvider(
        MOCK_RUNTIME_COMMAND,
        env={"SPONSORED_MOCK_ERROR_CODE": "CONTRACT_MISMATCH"},
        request_timeout=15.0,
    )
    service = LLMService(
        registry=registry,  # type: ignore[arg-type]
        memory=FakeMemory(),  # type: ignore[arg-type]
        sponsored_provider=provider,
    )
    try:
        with pytest.raises(LLMProviderExecutionError, match="contract mismatch"):
            await service.execute_sponsored_task(
                caller="soul.consolidation",
                payload=VALID_PAYLOAD,
                user_text=VALID_USER_TEXT,
                fallback_system_instruction="legacy system",
                fallback_user_input="legacy user",
            )
    finally:
        await service.aclose()

    assert registry.calls == []


def test_env_bootstrap_is_default_off() -> None:
    assert build_sponsored_provider_from_env({}) is None
    assert build_sponsored_provider_from_env({SPONSORED_ENABLED_ENV: "1"}) is None
    assert (
        build_sponsored_provider_from_env(
            {SPONSORED_ENABLED_ENV: "true", SPONSORED_RUNTIME_ENV: "'"}
        )
        is None
    )


def test_env_bootstrap_builds_provider_when_enabled() -> None:
    provider = build_sponsored_provider_from_env(
        {
            SPONSORED_ENABLED_ENV: "yes",
            SPONSORED_RUNTIME_ENV: f"{sys.executable} -m openbiliclaw.llm.sponsored_mock_runtime",
        }
    )
    assert isinstance(provider, SponsoredProvider)


class _LegacyOnlyService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        caller: str = "",
        inject_core_memory: bool = True,
    ) -> LLMResponse:
        self.calls.append(
            {
                "system_instruction": system_instruction,
                "user_input": user_input,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "caller": caller,
                "inject_core_memory": inject_core_memory,
            }
        )
        return LLMResponse(content="legacy", provider="openai")


async def test_helper_keeps_legacy_doubles_working() -> None:
    legacy = _LegacyOnlyService()
    response = await execute_sponsored_or_structured(
        legacy,
        caller="soul.consolidation",
        payload=VALID_PAYLOAD,
        user_text=VALID_USER_TEXT,
        fallback_system_instruction="legacy system",
        fallback_user_input="legacy user",
        temperature=0.2,
        max_tokens=1234,
        inject_core_memory=False,
    )
    assert response.content == "legacy"
    assert legacy.calls == [
        {
            "system_instruction": "legacy system",
            "user_input": "legacy user",
            "temperature": 0.2,
            "max_tokens": 1234,
            "caller": "soul.consolidation",
            "inject_core_memory": False,
        }
    ]


async def test_sponsored_success_records_usage_for_cost_ledger() -> None:
    class FakeRecorder:
        def __init__(self) -> None:
            self.calls: list[tuple[LLMResponse, str]] = []

        def record(self, response: LLMResponse, *, caller: str = "") -> None:
            self.calls.append((response, caller))

    registry = FakeRegistry(LLMResponse(content="legacy", provider="openai"))
    recorder = FakeRecorder()
    provider = SponsoredProvider(MOCK_RUNTIME_COMMAND, request_timeout=15.0)
    service = LLMService(
        registry=registry,  # type: ignore[arg-type]
        memory=FakeMemory(),  # type: ignore[arg-type]
        sponsored_provider=provider,
        usage_recorder=recorder,
    )
    try:
        response = await service.execute_sponsored_task(
            caller="soul.consolidation",
            payload=VALID_PAYLOAD,
            user_text=VALID_USER_TEXT,
            fallback_system_instruction="legacy system",
            fallback_user_input="legacy user",
        )
    finally:
        await service.aclose()

    assert response.provider == "sponsored"
    assert recorder.calls
    recorded_response, recorded_caller = recorder.calls[0]
    assert recorded_caller == "soul.consolidation"
    assert recorded_response.model == "THUDM/GLM-4-9B-0414"
    assert recorded_response.usage == {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
    }
