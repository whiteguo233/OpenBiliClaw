"""Responses JSON validation at the actual preference-analysis boundary."""

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest

from openbiliclaw.llm.base import LLMProviderError, LLMRegistry
from openbiliclaw.llm.openai_provider import OpenAIProvider
from openbiliclaw.llm.service import LLMService
from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer


async def test_preference_analysis_satisfies_responses_input_json_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAIProvider(api_key="test", api_flavor="responses")
    registry = LLMRegistry()
    registry.register(provider, default=True)
    service = LLMService(registry=registry, memory=None)
    calls: list[dict[str, Any]] = []

    async def create(**kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        assert "json" in kwargs["instructions"].lower()
        if not any("json" in msg["content"].lower() for msg in kwargs["input"]):
            raise LLMProviderError(
                "HTTP 400: Response input messages must contain the word 'json' "
                "to use 'text.format' of type 'json_object'."
            )
        return SimpleNamespace(
            output_text='{"interests": [{"name": "编程", "category": "知识", "weight": 0.8}]}',
            model="test",
            usage=None,
        )

    monkeypatch.setattr(provider._client.responses, "create", create)
    result = await PreferenceAnalyzer(registry=service).analyze_events(
        events=[{"type": "view", "title": "Python 编程入门"}], existing_preference={}
    )
    assert result["interests"]
    assert len(calls) == 1


@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize("content", ["hello", "Return JSON.", "Return json."])
async def test_responses_json_contract_preserves_caller_messages(
    monkeypatch: pytest.MonkeyPatch,
    json_mode: bool,
    content: str,
) -> None:
    provider = OpenAIProvider(api_key="test", api_flavor="responses")
    messages = [{"role": "system", "content": "Return json."}, {"role": "user", "content": content}]
    original = deepcopy(messages)

    async def create(**kwargs: Any) -> SimpleNamespace:
        if json_mode:
            assert any("json" in msg["content"].lower() for msg in kwargs["input"])
        if not json_mode or "json" in content.lower():
            assert kwargs["input"] == original[1:]
        return SimpleNamespace(output_text='{"ok": true}', model="test", usage=None)

    monkeypatch.setattr(provider._client.responses, "create", create)
    await provider.complete(messages, json_mode=json_mode)
    assert messages == original
