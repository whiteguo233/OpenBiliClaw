"""Tests for the shared LLM service facade."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.llm.base import LLMProviderError, LLMRateLimitError, LLMResponse
from openbiliclaw.llm.service import (
    LLMProviderExecutionError,
    LLMResponseContentError,
    LLMService,
    ModuleOverride,
    PrioritySemaphore,
    is_llm_rate_limit_error,
    module_overrides_from_config,
)
from openbiliclaw.memory.manager import MemoryManager

if TYPE_CHECKING:
    from pathlib import Path


class FakeRegistry:
    """Minimal fake registry for service tests."""

    def __init__(
        self,
        response: LLMResponse | None = None,
        error: Exception | None = None,
        *,
        chat_capable: set[str] | None = None,
        default_provider: str = "openai",
        provider_error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.provider_error = provider_error
        self.chat_capable = {name.lower() for name in (chat_capable or {"openai"})}
        self.default_provider = default_provider
        self.calls: list[list[dict[str, str]]] = []
        self.provider_calls: list[dict[str, object]] = []
        self.json_modes: list[bool] = []

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        self.calls.append(messages)
        self.json_modes.append(json_mode)
        if self.error is not None:
            raise self.error
        return self.response or LLMResponse(content="", provider="openai")

    async def complete_provider(
        self,
        provider_name: str,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
        reasoning_effort: str | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        self.provider_calls.append(
            {
                "provider_name": provider_name,
                "messages": messages,
                "json_mode": json_mode,
                "model": model,
                "reasoning_effort": reasoning_effort,
            }
        )
        if self.provider_error is not None:
            raise self.provider_error
        return self.response or LLMResponse(content="ok", provider=provider_name)

    def is_chat_capable(self, name: str) -> bool:
        return name.strip().lower() in self.chat_capable


class FakeMemoryManager:
    def __init__(self, core_prompt: str) -> None:
        self.core_prompt = core_prompt

    def render_core_memory_prompt(self) -> str:
        return self.core_prompt


def test_is_llm_rate_limit_error_detects_wrapped_provider_backoff() -> None:
    try:
        try:
            raise LLMRateLimitError("429 Too Many Requests")
        except LLMRateLimitError as err:
            raise LLMProviderExecutionError("All providers failed") from err
    except LLMProviderExecutionError as wrapped:
        assert is_llm_rate_limit_error(wrapped)

    assert is_llm_rate_limit_error(
        LLMProviderExecutionError("Provider gemini is cooling down after 429")
    )
    assert not is_llm_rate_limit_error(ValueError("Expected scored JSON array"))


@pytest.mark.asyncio
async def test_llm_service_calls_registry_with_memory_context(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.get_layer("soul").update("personality_portrait", "喜欢深度叙事和结构化表达")
    registry = FakeRegistry(LLMResponse(content="当然，我们继续聊。", provider="openai"))
    service = LLMService(registry=registry, memory=memory)

    response = await service.complete_socratic_dialogue(
        user_message="我最近特别喜欢看长视频。",
        history=[{"role": "user", "content": "我喜欢能讲透的内容"}],
    )

    assert response.content == "当然，我们继续聊。"
    assert len(registry.calls) == 1
    assert registry.calls[0][0]["role"] == "system"
    assert "结构化表达" in registry.calls[0][0]["content"]
    assert "老B友" in registry.calls[0][0]["content"]


@pytest.mark.asyncio
async def test_llm_service_injects_empty_memory_placeholder(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    registry = FakeRegistry(LLMResponse(content="我们可以慢慢聊。", provider="openai"))
    service = LLMService(registry=registry, memory=memory)

    await service.complete_socratic_dialogue(
        user_message="我最近想看点新东西。",
        history=[],
    )

    assert "尚未建立完整画像" in registry.calls[0][0]["content"]


@pytest.mark.asyncio
async def test_llm_service_raises_on_empty_response_content(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    registry = FakeRegistry(LLMResponse(content="", provider="openai"))
    service = LLMService(registry=registry, memory=memory)

    with pytest.raises(LLMResponseContentError):
        await service.complete_socratic_dialogue(
            user_message="我想聊聊为什么我总在熬夜看视频。",
            history=[],
        )


@pytest.mark.asyncio
async def test_llm_service_wraps_provider_failures(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    registry = FakeRegistry(error=LLMProviderError("provider down"))
    service = LLMService(registry=registry, memory=memory)

    with pytest.raises(LLMProviderExecutionError):
        await service.complete_socratic_dialogue(
            user_message="我最近总在重复看同一类视频。",
            history=[],
        )


@pytest.mark.asyncio
async def test_complete_with_core_memory_injects_core_memory() -> None:
    registry = FakeRegistry(LLMResponse(content="ok", provider="openai"))
    memory = FakeMemoryManager(core_prompt="## 用户画像\nportrait")
    service = LLMService(registry=registry, memory=memory)  # type: ignore[arg-type]

    await service.complete_with_core_memory(
        system_instruction="你是内容评估助手。",
        user_input="请评估这个视频。",
    )

    assert "## 用户画像" in registry.calls[0][0]["content"]
    assert "你是内容评估助手。" in registry.calls[0][0]["content"]
    assert registry.calls[0][1]["content"] == "请评估这个视频。"


@pytest.mark.asyncio
async def test_complete_structured_task_enables_json_mode() -> None:
    registry = FakeRegistry(LLMResponse(content='{"ok": true}', provider="openai"))
    memory = FakeMemoryManager(core_prompt="## 用户画像\nportrait")
    service = LLMService(registry=registry, memory=memory)  # type: ignore[arg-type]

    await service.complete_structured_task(
        system_instruction="输出 JSON。",
        user_input="请返回结构化结果。",
    )

    assert registry.calls
    assert registry.json_modes == [True]


def test_resolve_priority_longest_prefix_wins() -> None:
    """write_expression beats the catch-all default; soul-level prefix matches."""
    assert LLMService._resolve_priority("recommendation.write_expression") == 1
    assert LLMService._resolve_priority("discovery.evaluate_batch") == 1
    assert LLMService._resolve_priority("recommendation.delight_score") == 2
    assert LLMService._resolve_priority("soul.preference") == 2
    assert LLMService._resolve_priority("xhs.classify") == 2
    assert LLMService._resolve_priority("unrelated.tag") == LLMService._DEFAULT_PRIORITY
    assert LLMService._resolve_priority("") == LLMService._DEFAULT_PRIORITY


def test_route_bucket_for_caller_covers_actual_callers() -> None:
    assert LLMService._route_bucket_for_caller("soul.profile_builder") == "soul"
    assert LLMService._route_bucket_for_caller("discovery.search.query") == "discovery"
    assert LLMService._route_bucket_for_caller("discovery.evaluate_batch") == "evaluation"
    assert LLMService._route_bucket_for_caller("recommendation.delight_score") == "evaluation"
    assert (
        LLMService._route_bucket_for_caller("recommendation.write_expression") == "recommendation"
    )
    assert LLMService._route_bucket_for_caller("sources.xhs.classify") == "discovery"
    assert LLMService._route_bucket_for_caller("eval.batch") == "evaluation"
    assert LLMService._route_bucket_for_caller("unrelated.tag") is None


def test_module_overrides_from_config_normalizes_non_empty_blocks() -> None:
    from openbiliclaw.config import Config

    config = Config()
    config.llm.soul.provider = " Claude "
    config.llm.soul.model = " claude-sonnet "
    config.llm.discovery.model = " gpt-4o-mini "

    overrides = module_overrides_from_config(config)

    assert overrides == {
        "soul": ModuleOverride(provider="claude", model="claude-sonnet"),
        "discovery": ModuleOverride(provider="", model="gpt-4o-mini"),
    }


@pytest.mark.asyncio
async def test_complete_with_core_memory_routes_module_override() -> None:
    registry = FakeRegistry(
        LLMResponse(content="ok", provider="claude"),
        chat_capable={"openai", "claude"},
    )
    memory = FakeMemoryManager(core_prompt="## 用户画像\nportrait")
    service = LLMService(
        registry=registry,
        memory=memory,  # type: ignore[arg-type]
        module_overrides={"soul": ModuleOverride(provider="claude", model="claude-sonnet")},
    )

    await service.complete_with_core_memory(
        system_instruction="A",
        user_input="B",
        caller="soul.profile_builder",
    )

    assert registry.calls == []
    assert registry.provider_calls[0]["provider_name"] == "claude"
    assert registry.provider_calls[0]["model"] == "claude-sonnet"


@pytest.mark.asyncio
async def test_route_bucket_specific_prefix_beats_broad_recommendation() -> None:
    registry = FakeRegistry(
        LLMResponse(content="ok", provider="deepseek"),
        chat_capable={"openai", "deepseek"},
    )
    service = LLMService(
        registry=registry,
        memory=FakeMemoryManager(core_prompt=""),  # type: ignore[arg-type]
        module_overrides={
            "recommendation": ModuleOverride(provider="openai", model="gpt-4o-mini"),
            "evaluation": ModuleOverride(provider="deepseek", model="deepseek-v4-flash"),
        },
    )

    await service.complete_with_core_memory(
        system_instruction="A",
        user_input="B",
        caller="recommendation.delight_score",
    )

    assert registry.provider_calls[0]["provider_name"] == "deepseek"
    assert registry.provider_calls[0]["model"] == "deepseek-v4-flash"


@pytest.mark.asyncio
async def test_model_only_module_override_uses_default_provider() -> None:
    registry = FakeRegistry(
        LLMResponse(content="ok", provider="openai"),
        chat_capable={"openai"},
        default_provider="openai",
    )
    service = LLMService(
        registry=registry,
        memory=FakeMemoryManager(core_prompt=""),  # type: ignore[arg-type]
        module_overrides={"soul": ModuleOverride(model="gpt-4.1-mini")},
    )

    await service.complete_with_core_memory(
        system_instruction="A",
        user_input="B",
        caller="soul.preference",
    )

    assert registry.calls == []
    assert registry.provider_calls[0]["provider_name"] == "openai"
    assert registry.provider_calls[0]["model"] == "gpt-4.1-mini"


@pytest.mark.asyncio
async def test_unknown_module_override_provider_falls_back_and_logs_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    registry = FakeRegistry(
        LLMResponse(content="ok", provider="openai"),
        chat_capable={"openai"},
    )
    service = LLMService(
        registry=registry,
        memory=FakeMemoryManager(core_prompt=""),  # type: ignore[arg-type]
        module_overrides={"soul": ModuleOverride(provider="claud", model="expensive")},
    )

    with caplog.at_level(logging.INFO, logger="openbiliclaw.llm.service"):
        await service.complete_with_core_memory(
            system_instruction="A",
            user_input="B",
            caller="soul.preference",
        )
        await service.complete_with_core_memory(
            system_instruction="A",
            user_input="C",
            caller="soul.profile_builder",
        )

    assert registry.provider_calls == []
    assert len(registry.calls) == 2
    ignored = [r for r in caplog.records if "LLM module override ignored" in r.getMessage()]
    assert len(ignored) == 1


@pytest.mark.asyncio
async def test_override_provider_error_does_not_spill_to_default() -> None:
    registry = FakeRegistry(
        LLMResponse(content="ok", provider="openai"),
        chat_capable={"openai", "claude"},
        provider_error=LLMProviderError("override down"),
    )
    service = LLMService(
        registry=registry,
        memory=FakeMemoryManager(core_prompt=""),  # type: ignore[arg-type]
        module_overrides={"soul": ModuleOverride(provider="claude")},
    )

    with pytest.raises(LLMProviderExecutionError):
        await service.complete_with_core_memory(
            system_instruction="A",
            user_input="B",
            caller="soul.preference",
        )

    assert len(registry.provider_calls) == 1
    assert registry.calls == []


@pytest.mark.asyncio
async def test_priority_semaphore_orders_waiters_by_priority() -> None:
    """When multiple coroutines queue while the slot is held, lower-number priorities run first."""
    sem = PrioritySemaphore(capacity=1)
    log: list[str] = []
    blocker_release = asyncio.Event()

    async def blocker() -> None:
        async with sem.slot(priority=1):
            log.append("blocker.start")
            await blocker_release.wait()
            log.append("blocker.end")

    async def worker(name: str, priority: int) -> None:
        async with sem.slot(priority=priority):
            log.append(name)

    blocker_task = asyncio.create_task(blocker())
    # Give the blocker time to acquire the slot before the contenders queue up.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    low = asyncio.create_task(worker("low", priority=3))
    medium = asyncio.create_task(worker("medium", priority=2))
    high = asyncio.create_task(worker("high", priority=1))
    # Let all three workers reach the queue.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    blocker_release.set()
    await asyncio.gather(blocker_task, low, medium, high)

    assert log[0] == "blocker.start"
    assert log[1] == "blocker.end"
    # Highest priority (lowest number) should be served first after the blocker frees the slot.
    assert log[2:] == ["high", "medium", "low"]


@pytest.mark.asyncio
async def test_complete_with_core_memory_defaults_to_three_concurrent_calls() -> None:
    """The shared LLM gate should allow three requests by default and queue the fourth."""
    memory = FakeMemoryManager(core_prompt="## 用户画像\nportrait")
    in_flight = 0
    peak = 0
    release = asyncio.Event()

    class TrackingRegistry:
        async def complete(
            self,
            messages: list[dict[str, str]],
            *,
            temperature: float = 0.7,
            max_tokens: int = 4096,
            json_mode: bool = False,
            reasoning_effort: str | None = None,
        ) -> LLMResponse:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            try:
                await release.wait()
            finally:
                in_flight -= 1
            return LLMResponse(content="ok", provider="openai")

    service = LLMService(registry=TrackingRegistry(), memory=memory)  # type: ignore[arg-type]

    tasks = [
        asyncio.create_task(
            service.complete_with_core_memory(
                system_instruction=str(index),
                user_input=str(index),
                caller="recommendation.write_expression",
            )
        )
        for index in range(4)
    ]

    try:
        for _ in range(5):
            await asyncio.sleep(0)
        observed = in_flight
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)

    assert observed == 3
    assert peak == 3
