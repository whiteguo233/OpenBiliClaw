"""End-to-end tests for config-backed LLM module routing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from openbiliclaw.llm.base import LLMProvider, LLMRegistry, LLMResponse


@dataclass
class RecordingProvider(LLMProvider):
    provider_name: str
    calls: list[dict[str, Any]] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.provider_name

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
        self.calls.append(
            {
                "messages": messages,
                "json_mode": json_mode,
                "model": model,
                "reasoning_effort": reasoning_effort,
            }
        )
        return LLMResponse(
            content='{"ok": true}',
            provider=self.provider_name,
            model=model or f"{self.provider_name}-default",
        )


@pytest.mark.asyncio
async def test_runtime_context_routes_configured_module_overrides(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    import openbiliclaw.llm as llm_pkg
    from openbiliclaw.api.runtime_context import build_runtime_context
    from openbiliclaw.config import Config

    openai = RecordingProvider("openai")
    deepseek = RecordingProvider("deepseek")
    registry = LLMRegistry()
    registry.register(openai, default=True)
    registry.register(deepseek)
    monkeypatch.setattr(llm_pkg, "build_llm_registry", lambda _config: registry)

    config = Config(data_dir=str(tmp_path / "data"))
    config.llm.default_provider = "openai"
    config.llm.evaluation.provider = "deepseek"
    config.llm.evaluation.model = "deepseek-eval"
    config.llm.recommendation.provider = "openai"
    config.llm.recommendation.model = "gpt-rec"
    config.llm.discovery.provider = "deepseek"
    config.llm.discovery.model = "deepseek-discovery"

    ctx = build_runtime_context(config)

    await ctx.llm_service.complete_structured_task(
        system_instruction="Return JSON.",
        user_input="score this",
        caller="recommendation.delight_score",
    )
    await ctx.llm_service.complete_structured_task(
        system_instruction="Return JSON.",
        user_input="write this",
        caller="recommendation.write_expression",
    )
    await ctx.llm_service.complete_structured_task(
        system_instruction="Return JSON.",
        user_input="keywords",
        caller="sources.xhs.keyword_gen",
    )

    assert [call["model"] for call in deepseek.calls] == [
        "deepseek-eval",
        "deepseek-discovery",
    ]
    assert [call["model"] for call in openai.calls] == ["gpt-rec"]
