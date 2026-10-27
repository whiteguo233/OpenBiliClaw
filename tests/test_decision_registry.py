"""Independent decision-provider configuration, credentials, and client ownership."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping

import httpx
import pytest

from openbiliclaw.config import Config, DecisionConfig
from openbiliclaw.decision import (
    DecisionConfigurationError,
    DecisionRequest,
    NoulQuestion,
    build_decision_provider,
)


class UnreadableEnvironment(Mapping[str, str]):
    """A disabled provider must not even inspect environment variables."""

    def __getitem__(self, key: str) -> str:
        raise AssertionError("environment should not be read")

    def __iter__(self) -> Iterator[str]:
        raise AssertionError("environment should not be read")

    def __len__(self) -> int:
        raise AssertionError("environment should not be read")


def test_disabled_provider_does_not_read_environment() -> None:
    assert build_decision_provider(DecisionConfig(), environ=UnreadableEnvironment()) is None


def test_unknown_provider_fails_even_when_jev_disabled() -> None:
    with pytest.raises(DecisionConfigurationError, match="Unsupported decision provider"):
        build_decision_provider(DecisionConfig(), provider_name="unknown")


def test_enabled_provider_does_not_borrow_chat_key() -> None:
    config = Config()
    config.llm.openai.api_key = "a-chat-key-that-must-not-be-used"
    config.decision.providers.jev.enabled = True
    with pytest.raises(DecisionConfigurationError, match="environment variable"):
        build_decision_provider(config.decision, environ={"OPENAI_API_KEY": "another-chat-key"})


def test_explicit_env_name_has_no_default_key_fallback() -> None:
    config = DecisionConfig()
    config.providers.jev.enabled = True
    config.providers.jev.api_key_env = "MY_JEV_KEY"
    with pytest.raises(DecisionConfigurationError, match="environment variable"):
        build_decision_provider(config, environ={"TYPESAFE_API_KEY": "unused-key"})


@pytest.mark.parametrize("key", ["", "   ", "key\r\nheader", "key with spaces", "秘密", "x\x7fy"])
def test_bad_credentials_fail_without_echoing_value(key: str) -> None:
    config = DecisionConfig()
    config.providers.jev.enabled = True
    with pytest.raises(DecisionConfigurationError) as error:
        build_decision_provider(config, environ={"TYPESAFE_API_KEY": key})
    if key.strip():
        assert key not in str(error.value)


async def test_factory_reads_only_selected_key_and_does_not_send_until_evaluate() -> None:
    config = DecisionConfig()
    config.providers.jev.enabled = True
    config.providers.jev.api_key_env = "MY_JEV_KEY"
    config.providers.jev.model = "jev-pinned-fixture"
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "model": "jev-pinned-fixture",
                "answers": {"keep": {"type": "noul", "noul": 0.7}},
                "usage": {"input_tokens": 12, "output_tokens": 1},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = build_decision_provider(
            config,
            client=client,
            environ={"MY_JEV_KEY": "  the-selected-key  ", "TYPESAFE_API_KEY": "wrong-key"},
        )
        assert provider is not None
        assert seen == []
        evidence = await provider.evaluate(
            DecisionRequest(
                request_id="filter-1",
                purpose="FILTER",
                question_schema_version="test-filter.v1",
                state={"card": "中文本地模型内容"},
                questions={"keep": NoulQuestion(instructions="Does card match the intent?")},
            )
        )
        assert evidence.status == "OK"
        assert evidence.requested_model == "jev-pinned-fixture"
        assert len(seen) == 1
        assert str(seen[0].url) == "https://api.typesafe.ai/v1/systemone"
        assert seen[0].headers["authorization"] == "Bearer the-selected-key"
        body = json.loads(seen[0].content)
        assert body["model"] == "jev-pinned-fixture"
        assert body["state"] == {"card": "中文本地模型内容"}
        await provider.aclose()
        assert not client.is_closed


def test_mutated_configuration_is_validated_at_factory() -> None:
    config = DecisionConfig()
    config.providers.jev.timeout_seconds = float("nan")
    with pytest.raises(ValueError):
        build_decision_provider(config, environ={})
