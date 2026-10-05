"""Tests for the shared LLM service facade."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

import openbiliclaw.llm.base as llm_base
from openbiliclaw.llm.base import (
    LLMAuthError,
    LLMFallbackError,
    LLMProviderError,
    LLMRateLimitError,
    LLMRegistry,
    LLMResponse,
    LLMResponseError,
    LLMTimeoutError,
    classify_llm_failure_kind,
    classify_llm_unavailability,
    describe_llm_failure,
    is_llm_moderation_error,
    is_reasoning_budget_exhausted,
)
from openbiliclaw.llm.openai_provider import OpenAIProvider
from openbiliclaw.llm.service import (
    MIN_STRUCTURED_MAX_TOKENS,
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
        self.calls: list[list[dict[str, object]]] = []
        self.provider_calls: list[dict[str, object]] = []
        self.json_modes: list[bool] = []
        self.reasoning_efforts: list[str | None] = []
        self.max_tokens_seen: list[int] = []

    async def complete(
        self,
        messages: list[dict[str, object]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        self.calls.append(messages)
        self.json_modes.append(json_mode)
        self.reasoning_efforts.append(reasoning_effort)
        self.max_tokens_seen.append(max_tokens)
        if self.error is not None:
            raise self.error
        return self.response or LLMResponse(content="", provider="openai")

    async def complete_provider(
        self,
        provider_name: str,
        messages: list[dict[str, object]],
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
        self.max_tokens_seen.append(max_tokens)
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


class FakeSplitMemoryManager:
    """Memory double exposing the stable/volatile split API (real manager shape)."""

    def __init__(self, stable: str, volatile: str) -> None:
        self.stable = stable
        self.volatile = volatile

    def render_core_memory_blocks(self) -> tuple[str, str]:
        return self.stable, self.volatile

    def render_core_memory_prompt(self) -> str:
        return "\n\n".join(part for part in (self.stable, self.volatile) if part)


def _safe_llm_failure_message(exc: BaseException) -> str:
    helper = getattr(llm_base, "safe_llm_failure_message", None)
    assert helper is not None, "safe_llm_failure_message() is not implemented"
    return str(helper(exc))


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
    assert is_llm_rate_limit_error(
        LLMProviderExecutionError("Provider deepseek failed: HTTP 402: Insufficient Balance")
    )
    assert not is_llm_rate_limit_error(ValueError("Expected scored JSON array"))


def test_classify_llm_unavailability_rate_limited_through_fallback_chain() -> None:
    with pytest.raises(LLMFallbackError) as exc_info:
        try:
            try:
                raise RuntimeError("openai RateLimitError: HTTP 429")
            except RuntimeError as base_err:
                raise LLMRateLimitError("rate limit; cooling down") from base_err
        except LLMRateLimitError as rl_err:
            raise LLMFallbackError(
                "All providers failed (deepseek, openai). Last error: rate limit"
            ) from rl_err
    assert classify_llm_unavailability(exc_info.value) == "rate_limited"


def test_classify_llm_unavailability_no_provider_message() -> None:
    assert (
        classify_llm_unavailability(
            LLMFallbackError("No provider was available to process the request.")
        )
        == "no_provider"
    )
    # Wrapped in the service-layer execution error the way production does.
    try:
        try:
            raise LLMFallbackError("No provider was available to process the request.")
        except LLMFallbackError as inner:
            raise LLMProviderExecutionError(str(inner)) from inner
    except LLMProviderExecutionError as wrapped:
        assert classify_llm_unavailability(wrapped) == "no_provider"


def test_classify_llm_unavailability_returns_none_for_unrelated_error() -> None:
    assert classify_llm_unavailability(ValueError("Expected scored JSON array")) is None


def test_classify_llm_unavailability_model_not_found_through_chain() -> None:
    # Real shape from the guided-init log: ollama 404 for a model never pulled,
    # wrapped up through the preference-analysis path. Loops must treat this as
    # an expected, calmly-logged deferral — not an "Unexpected error" traceback.
    try:
        try:
            raise LLMProviderError(
                "ollama request failed: HTTP 404: "
                '{"message": "model \'llama3\' not found, try pulling it first", '
                '"type": "not_found_error"}'
            )
        except LLMProviderError as inner:
            raise LLMProviderExecutionError(str(inner)) from inner
    except LLMProviderExecutionError as wrapped:
        assert classify_llm_unavailability(wrapped) == "model_not_found"


def test_classify_llm_unavailability_rate_limit_wins_over_no_provider() -> None:
    with pytest.raises(LLMRateLimitError) as exc_info:
        try:
            raise LLMFallbackError("No provider was available to process the request.")
        except LLMFallbackError as np_err:
            raise LLMRateLimitError("rate limit hit") from np_err
    assert classify_llm_unavailability(exc_info.value) == "rate_limited"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (LLMProviderError("HTTP 401 unauthorized: invalid api key"), "auth_failed"),
        (
            LLMProviderError(
                "ollama request failed: HTTP 404: "
                '{"code": null, "message": "model \'llama3\' not found, try pulling '
                'it first", "param": null, "type": "not_found_error"}'
            ),
            "model_not_found",
        ),
        (
            LLMProviderError("The model `gpt-x` does not exist or you do not have access to it."),
            "model_not_found",
        ),
        (ConnectionError("connection reset by peer"), "connection"),
        (OSError("network is unreachable"), "connection"),
        (
            LLMProviderError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"),
            "connection",
        ),
        (LLMProviderError("openai_compatible request failed: Connection error."), "connection"),
        (LLMTimeoutError("request timed out"), "timeout"),
        (LLMProviderExecutionError("upstream returned HTTP 503"), "server_error"),
        (LLMResponseError("empty completion"), "invalid_response"),
        (ValueError("unrelated local failure"), None),
    ],
)
def test_classify_llm_failure_kind(error: BaseException, expected: str | None) -> None:
    assert classify_llm_failure_kind(error) == expected


def test_classify_llm_failure_kind_walks_wrapped_chain() -> None:
    try:
        try:
            raise LLMTimeoutError("provider timeout")
        except LLMTimeoutError as inner:
            raise LLMFallbackError("all providers failed") from inner
    except LLMFallbackError as wrapped:
        assert classify_llm_failure_kind(wrapped) == "timeout"


def test_classify_llm_failure_kind_preserves_auth_precedence_over_wrapper_text() -> None:
    try:
        raise LLMProviderError("HTTP 401 unauthorized: invalid api key")
    except LLMProviderError as inner:
        wrapped = LLMProviderExecutionError("connection reset while reporting failure")
        wrapped.__cause__ = inner
    assert classify_llm_failure_kind(wrapped) == "auth_failed"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        # Qualified status forms every gateway we've seen actually emits.
        (LLMProviderError("openai_compatible request failed: HTTP 401: {}"), "auth_failed"),
        (LLMProviderError("Error code: 401 - permission denied"), "auth_failed"),
        (LLMProviderError('request failed: {"code":401,"msg":"bad token"}'), "auth_failed"),
        (LLMProviderError("openai request failed: status_code=401"), "auth_failed"),
        (LLMAuthError("openai_compatible authentication failed"), "auth_failed"),
        # A bare "401" inside a request id / trace id is NOT a credential
        # rejection: auth outranks every other bucket, so a false positive here
        # sends the user to re-check a key that was fine all along.
        (LLMProviderError("openai request failed: req id req-1401ab timed out"), "timeout"),
        (LLMProviderError("gateway trace 401-7de2 connection reset"), "connection"),
        (LLMProviderError("HTTP 4011 unknown gateway status"), None),
    ],
)
def test_classify_llm_failure_kind_requires_qualified_401(
    error: BaseException, expected: str | None
) -> None:
    assert classify_llm_failure_kind(error) == expected


def test_classify_llm_failure_kind_keeps_billing_402_out_of_auth_bucket() -> None:
    """A 402 body carrying an unrelated "401" must still read as quota.

    ``auth`` is checked before ``rate_limited``, so a bare-substring match on
    "401" would tell a user with an empty balance to go fix their API key.
    """
    error = LLMRateLimitError(
        "deepseek provider backoff: HTTP 402: "
        '{"error": {"message": "Insufficient Balance"}, "request_id": "r-401f2"}'
    )
    assert classify_llm_failure_kind(error) == "rate_limited"


def test_describe_llm_failure_auth_names_the_rejected_provider() -> None:
    """Users running two providers need to know which key to fix."""
    try:
        raise LLMAuthError(
            "openai_compatible authentication failed: HTTP 401: invalid token",
            provider_name="openai_compatible",
            endpoint="https://api.sensenova.cn/compatible-mode/v1",
        )
    except LLMAuthError as inner:
        wrapped = LLMProviderExecutionError("preference analysis failed")
        wrapped.__cause__ = inner

    reason = describe_llm_failure(wrapped)

    assert reason is not None
    assert "openai_compatible" in reason
    assert "api.sensenova.cn" in reason
    # Temporary AK/SK-derived tokens expire mid-run, which reads as "the key
    # worked, why 401?" — the copy has to name that case.
    assert "临时 token" in reason


def test_describe_llm_failure_auth_never_leaks_inline_credentials() -> None:
    reason = describe_llm_failure(
        LLMAuthError(
            "openai_compatible authentication failed",
            provider_name="openai_compatible",
            endpoint="https://user:s3cret@gateway.internal/v1",
        )
    )
    assert reason is not None
    assert "s3cret" not in reason
    assert "gateway.internal" in reason


def test_describe_llm_failure_auth_without_identity_stays_generic() -> None:
    reason = describe_llm_failure(LLMProviderError("HTTP 401 unauthorized: invalid api key"))
    assert reason is not None
    assert "所配置的 AI 服务" in reason


@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError("missing local file"),
        PermissionError("local disk denied"),
        OSError("disk full"),
    ],
)
def test_classify_llm_failure_kind_does_not_treat_local_os_errors_as_connection(
    error: OSError,
) -> None:
    assert classify_llm_failure_kind(error) is None


def test_describe_llm_failure_content_moderation_500() -> None:
    # A Chinese compat gateway returns a compliance refusal *as a 500*; the
    # cause chain carries the 法律法规 text. Guided init must show "switch model"
    # advice, not the raw traceback fragment.
    try:
        try:
            raise RuntimeError(
                "Error code: 500 - 非常抱歉，根据相关法律法规，"
                "我们无法提供关于以下内容的答案 (code 10013)"
            )
        except RuntimeError as upstream:
            raise LLMProviderError("openai_compatible request failed") from upstream
    except LLMProviderError as exc:
        reason = describe_llm_failure(exc)
    assert reason is not None
    assert "内容合规" in reason


def test_describe_llm_failure_deepseek_content_exists_risk() -> None:
    error = LLMProviderError(
        "deepseek request failed: HTTP 400: "
        '{"code": "invalid_request_error", "message": "Content Exists Risk"}'
    )
    assert is_llm_moderation_error(error)
    reason = describe_llm_failure(error)
    assert reason is not None
    assert "内容合规" in reason


def test_describe_llm_failure_model_not_found() -> None:
    reason = describe_llm_failure(
        LLMProviderError(
            "ollama request failed: HTTP 404: "
            '{"message": "model \'llama3\' not found, try pulling it first", '
            '"type": "not_found_error"}'
        )
    )
    assert reason is not None
    assert "404" in reason
    assert "ollama pull" in reason.lower() or "拉取" in reason


def test_describe_llm_failure_no_provider() -> None:
    reason = describe_llm_failure(
        LLMFallbackError("No provider was available to process the request.")
    )
    assert reason is not None
    assert "没有可用的 AI 服务" in reason


def test_describe_llm_failure_rate_limit_wins_over_no_provider() -> None:
    try:
        try:
            raise LLMFallbackError("No provider was available to process the request.")
        except LLMFallbackError as np_err:
            raise LLMRateLimitError("rate limit hit") from np_err
    except LLMRateLimitError as exc:
        reason = describe_llm_failure(exc)
    assert reason is not None
    assert "限流" in reason


def test_describe_llm_failure_authentication_chain() -> None:
    try:
        try:
            raise RuntimeError("HTTP 401 unauthorized: invalid api key")
        except RuntimeError as upstream:
            raise LLMProviderError("authentication failed") from upstream
    except LLMProviderError as exc:
        reason = describe_llm_failure(exc)
    assert reason is not None
    assert "鉴权失败" in reason
    assert "API key" in reason


def test_describe_llm_failure_insufficient_quota() -> None:
    reason = describe_llm_failure(LLMProviderError("upstream error: insufficient_quota"))
    assert reason is not None
    assert "额度用尽或被限流" in reason


def test_describe_llm_failure_http_429() -> None:
    reason = describe_llm_failure(LLMProviderError("upstream returned HTTP 429"))
    assert reason is not None
    assert "额度用尽或被限流" in reason


def test_describe_llm_failure_timeout_and_empty_response() -> None:
    assert "超时" in (describe_llm_failure(LLMTimeoutError("request timed out")) or "")
    assert "超时" in (describe_llm_failure(TimeoutError("socket stalled")) or "")
    assert "空响应" in (describe_llm_failure(LLMResponseError("empty completion")) or "")
    assert "空响应" in (
        describe_llm_failure(LLMResponseContentError("LLM returned an empty response")) or ""
    )


def test_describe_llm_failure_ssl_certificate_chain() -> None:
    # Issue #113: a local proxy / antivirus MITMs HTTPS, so the openai_compatible
    # provider's request dies with an SSL cert-verify failure. httpx raises
    # ConnectError, the SDK wraps it as a connection error, and analyze_events
    # surfaces it as "All providers failed". Guided init must show the proxy /
    # cert hint, not a generic "稍后重试".
    try:
        try:
            raise RuntimeError(
                "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
                "unable to get local issuer certificate (_ssl.c:1010)"
            )
        except RuntimeError as ssl_err:
            raise LLMProviderError(
                "openai_compatible request failed: Connection error."
            ) from ssl_err
    except LLMProviderError as exc:
        reason = describe_llm_failure(exc)
    assert reason is not None
    assert "SSL" in reason
    assert "代理" in reason


def test_describe_llm_failure_connection_error() -> None:
    # OpenAI SDK's APIConnectionError stringifies as "Connection error." with no
    # SSL / errno detail; still must map to the network-failure copy.
    reason = describe_llm_failure(
        LLMProviderError("openai_compatible request failed: Connection error.")
    )
    assert reason is not None
    assert "无法连接" in reason


def test_describe_llm_failure_ssl_wins_over_no_provider() -> None:
    # SSL cause is more actionable than the coarse "all providers failed" wrapper.
    try:
        try:
            raise RuntimeError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
        except RuntimeError as ssl_err:
            raise LLMFallbackError("No provider was available to process the request.") from ssl_err
    except LLMFallbackError as exc:
        reason = describe_llm_failure(exc)
    assert reason is not None
    assert "SSL" in reason


@pytest.mark.parametrize(
    ("exc", "expected_fragment"),
    [
        (LLMProviderError("上游内容审查拒绝了请求"), "内容合规"),
        (LLMProviderError("HTTP 401 unauthorized: invalid api key"), "鉴权失败"),
        (LLMRateLimitError("HTTP 429 insufficient_quota"), "额度用尽或被限流"),
        (LLMTimeoutError("provider request timed out"), "响应超时"),
        (TimeoutError("socket stalled"), "响应超时"),
        (LLMFallbackError("No provider was available"), "没有可用的 AI 服务"),
        (LLMResponseError("empty completion"), "空响应"),
        (LLMResponseContentError("LLM returned an empty response"), "空响应"),
        (
            RuntimeError("secret-upstream-detail"),
            "AI 服务暂时不可用；请稍后重试，或检查设置中的模型与网络。",
        ),
    ],
)
def test_safe_llm_failure_message_classifies_without_leaking_unknown_detail(
    exc: BaseException,
    expected_fragment: str,
) -> None:
    message = _safe_llm_failure_message(exc)

    assert expected_fragment in message
    assert "secret-upstream-detail" not in message


def test_safe_llm_failure_message_never_returns_raw_unknown_detail() -> None:
    message = _safe_llm_failure_message(RuntimeError("secret-upstream-detail"))

    assert message == "AI 服务暂时不可用；请稍后重试，或检查设置中的模型与网络。"
    assert "secret-upstream-detail" not in message


def test_describe_llm_failure_returns_none_for_unrelated_error() -> None:
    # A non-LLM failure (e.g. bad history data) must not be mislabeled as an
    # LLM outage — callers fall back to their own generic message.
    assert describe_llm_failure(ValueError("history parse failed")) is None


def test_classify_llm_unavailability_is_cycle_safe() -> None:
    first = ValueError("boom a")
    second = ValueError("boom b")
    first.__cause__ = second
    second.__cause__ = first  # deliberate cycle
    assert classify_llm_unavailability(first) is None


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
async def test_complete_with_core_memory_splits_stable_into_system_volatile_into_user() -> None:
    # Stable identity/preference stays in the system prefix (prompt-cache safe);
    # the volatile awareness/insight block moves to the user message, ahead of the
    # turn content (most-stable-first ordering).
    registry = FakeRegistry(LLMResponse(content="ok", provider="openai"))
    memory = FakeSplitMemoryManager(
        stable="## 用户画像\nportrait",
        volatile="## 近期观察\n- [2026-03-08] 更专注",
    )
    service = LLMService(registry=registry, memory=memory)  # type: ignore[arg-type]

    await service.complete_with_core_memory(
        system_instruction="你是内容评估助手。",
        user_input="请评估这个视频。",
    )

    system_content = str(registry.calls[0][0]["content"])
    user_content = str(registry.calls[0][1]["content"])
    assert "## 用户画像" in system_content
    assert "## 近期观察" not in system_content
    assert "## 近期观察" in user_content
    assert user_content.endswith("请评估这个视频。")
    assert user_content.index("## 近期观察") < user_content.index("请评估这个视频。")


@pytest.mark.asyncio
async def test_complete_with_core_memory_can_skip_core_memory_for_cacheable_eval() -> None:
    registry = FakeRegistry(LLMResponse(content="ok", provider="openai"))
    memory = FakeMemoryManager(core_prompt="## 用户画像\nportrait")
    service = LLMService(registry=registry, memory=memory)  # type: ignore[arg-type]

    await service.complete_with_core_memory(
        system_instruction="你是内容评估助手。",
        user_input="请评估这个视频。",
        caller="discovery.evaluate_batch",
        inject_core_memory=False,
    )

    system_content = str(registry.calls[0][0]["content"])
    assert "你是内容评估助手。" in system_content
    assert "## 用户画像" not in system_content
    assert registry.calls[0][1]["content"] == "请评估这个视频。"
    assert registry.reasoning_efforts == [None]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("caller", "requested", "expected"),
    [
        ("discovery.x.keyword_gen", None, None),
        ("recommendation.expression", None, None),
        ("sources.xhs.keyword_gen", None, None),
        ("yt_search.generate_queries", None, None),
        ("runtime.bilibili_extension_search.queries", None, None),
        ("eval.query_quality", None, None),
        ("eval.scenario_gen", None, None),
        ("soul.profile_build", None, None),
        ("discovery.evaluate_batch", "max", "max"),
    ],
)
async def test_channel_callers_default_to_follow_model_reasoning(
    caller: str,
    requested: str | None,
    expected: str | None,
) -> None:
    registry = FakeRegistry(LLMResponse(content="ok", provider="openai"))
    service = LLMService(
        registry=registry,
        memory=FakeMemoryManager(core_prompt=""),
    )  # type: ignore[arg-type]

    await service.complete_with_core_memory(
        system_instruction="A",
        user_input="B",
        caller=caller,
        reasoning_effort=requested,
    )

    assert registry.reasoning_efforts == [expected]


@pytest.mark.asyncio
async def test_complete_with_core_memory_does_not_normalize_nonstructured_json_text() -> None:
    registry = FakeRegistry(LLMResponse(content="ok", provider="openai"))
    memory = FakeMemoryManager(core_prompt="## 用户画像\nportrait")
    service = LLMService(registry=registry, memory=memory)  # type: ignore[arg-type]

    await service.complete_with_core_memory(
        system_instruction="输出 JSON。",
        user_input="普通对话请求。",
    )

    assert registry.json_modes == [False]
    assert registry.calls[0][0]["content"] == (
        "输出 JSON。\n\n以下是当前用户的 core memory，请作为理解背景：\n\n## 用户画像\nportrait"
    )
    assert registry.calls[0][1]["content"] == "普通对话请求。"


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
    assert registry.calls[0][0]["content"] == (
        "输出 json。\n\n以下是当前用户的 core memory，请作为理解背景：\n\n## 用户画像\nportrait"
    )


@pytest.mark.asyncio
async def test_structured_task_adds_json_contract_when_absent() -> None:
    registry = FakeRegistry(LLMResponse(content='{"ok": true}', provider="openai"))
    service = LLMService(
        registry=registry,
        memory=FakeMemoryManager(core_prompt=""),
    )  # type: ignore[arg-type]

    await service.complete_structured_task(
        system_instruction="请返回结构化结果。",
        user_input="请求。",
    )

    assert registry.calls[0][0]["content"] == "请返回结构化结果。\n\njson"


@pytest.mark.asyncio
async def test_complete_multimodal_structured_task_sends_text_and_images() -> None:
    registry = FakeRegistry(LLMResponse(content='[{"score": 0.8}]', provider="openai"))
    memory = FakeMemoryManager(core_prompt="## 用户画像\nportrait")
    service = LLMService(registry=registry, memory=memory)  # type: ignore[arg-type]

    await service.complete_multimodal_structured_task(
        system_instruction="输出 JSON。",
        user_input="请评估候选。",
        image_inputs=[
            {
                "content_id": "yt-demo",
                "data_url": "data:image/jpeg;base64,/9j/4AAQSkZJRg==",
                "mime_type": "image/jpeg",
            }
        ],
        caller="discovery.evaluate_batch",
    )

    assert registry.json_modes == [True]
    assert registry.reasoning_efforts == [None]
    assert registry.calls[0][0]["content"] == (
        "输出 json。\n\n以下是当前用户的 core memory，请作为理解背景：\n\n## 用户画像\nportrait"
    )
    user_message = registry.calls[0][1]
    assert user_message["role"] == "user"
    assert isinstance(user_message["content"], list)
    parts = user_message["content"]
    assert parts[0] == {"type": "text", "text": "请评估候选。"}
    assert parts[1] == {
        "type": "text",
        "text": (
            "Cover image cover:yt-demo maps to the content_batch item whose "
            "cover_image_ref is cover:yt-demo."
        ),
    }
    assert parts[2] == {
        "type": "image_url",
        "image_url": {"url": "data:image/jpeg;base64,/9j/4AAQSkZJRg=="},
    }


def test_resolve_priority_longest_prefix_wins() -> None:
    """write_expression beats the catch-all default; soul-level prefix matches."""
    assert LLMService._resolve_priority("recommendation.write_expression") == 1
    assert LLMService._resolve_priority("discovery.evaluate_batch") == 1
    assert (
        LLMService._resolve_priority("recommendation.background_score")
        == LLMService._DEFAULT_PRIORITY
    )
    assert LLMService._resolve_priority("soul.preference") == 2
    assert LLMService._resolve_priority("xhs.classify") == 2
    assert LLMService._resolve_priority("unrelated.tag") == LLMService._DEFAULT_PRIORITY
    assert LLMService._resolve_priority("") == LLMService._DEFAULT_PRIORITY


def test_route_bucket_for_caller_covers_actual_callers() -> None:
    assert LLMService._route_bucket_for_caller("soul.profile_builder") == "soul"
    assert LLMService._route_bucket_for_caller("soul.preference") == "soul"
    assert LLMService._route_bucket_for_caller("pool_purge.llm_agent") == "soul"
    assert LLMService._route_bucket_for_caller("api.sentiment") == "soul"
    assert LLMService._route_bucket_for_caller("discovery.search.query") == "discovery"
    assert LLMService._route_bucket_for_caller("discovery.keyword_planner") == "discovery"
    assert LLMService._route_bucket_for_caller("discovery.keyword_inspiration") == "discovery"
    assert LLMService._route_bucket_for_caller("discovery.x.keyword_gen") == "discovery"
    assert LLMService._route_bucket_for_caller("discovery.douyin.keyword_gen") == "discovery"
    assert (
        LLMService._route_bucket_for_caller("runtime.bilibili_extension_search.queries")
        == "discovery"
    )
    assert LLMService._route_bucket_for_caller("discovery.evaluate_batch") == "evaluation"
    assert LLMService._route_bucket_for_caller("recommendation.evaluate_batch") == "evaluation"
    assert LLMService._route_bucket_for_caller("recommendation.write_batch") == "recommendation"
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
        caller="recommendation.evaluate_batch",
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
                caller="soul.dialogue",
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


# -- Reasoning-budget classifier + structured max_tokens floor -------------


def test_is_reasoning_budget_exhausted_matches_wrapped_provider_message() -> None:
    message = (
        "openai_compatible returned reasoning but no final content "
        "(finish_reason=length); disable thinking/reasoning or increase max_tokens"
    )
    assert is_reasoning_budget_exhausted(RuntimeError(message))

    try:
        try:
            raise LLMResponseError(message)
        except LLMResponseError as err:
            raise LLMProviderExecutionError(f"All providers failed. Last error: {err}") from err
    except LLMProviderExecutionError as wrapped:
        assert is_reasoning_budget_exhausted(wrapped)


def test_is_reasoning_budget_exhausted_ignores_other_failures() -> None:
    assert not is_reasoning_budget_exhausted(RuntimeError("429 rate limited"))
    assert not is_reasoning_budget_exhausted(
        RuntimeError("openai_compatible returned empty content")
    )
    # Both markers must land on the same link: a bare "reasoning but no final
    # content" without the finish_reason marker is a different failure shape.
    assert not is_reasoning_budget_exhausted(
        RuntimeError("returned reasoning but no final content")
    )


@pytest.mark.asyncio
async def test_reasoning_budget_exhausted_from_responses_flavor_survives_service_wrapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Integration: a responses-flavor instance that burns the whole output
    budget on thinking raises an error the evaluation batch-halving self-heal
    recognizes even after registry fallback + service-layer wrapping."""
    provider = OpenAIProvider(api_key="test-key", api_flavor="responses")
    registry = LLMRegistry()
    registry.register(provider)
    service = LLMService(registry=registry, memory=object())  # type: ignore[arg-type]
    calls = {"count": 0}

    async def fake_create(**_: object) -> SimpleNamespace:
        calls["count"] += 1
        return SimpleNamespace(
            model="gpt-5-mini",
            status="incomplete",
            incomplete_details=SimpleNamespace(reason="max_output_tokens"),
            output=[SimpleNamespace(type="reasoning", summary=[])],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=15),
        )

    monkeypatch.setattr(provider._client.responses, "create", fake_create)

    with pytest.raises(LLMProviderExecutionError) as exc_info:
        await service.complete_with_core_memory(
            system_instruction="sys",
            user_input="hi",
            caller="discovery.eval",
            max_tokens=512,
            inject_core_memory=False,
        )

    assert is_reasoning_budget_exhausted(exc_info.value)
    # The doubled-budget retry fired before the error propagated.
    assert calls["count"] == 2


@pytest.mark.asyncio
async def test_structured_task_floors_small_max_tokens() -> None:
    registry = FakeRegistry(response=LLMResponse(content='{"ok": true}', provider="openai"))
    service = LLMService(registry=registry, memory=FakeMemoryManager("core"))  # type: ignore[arg-type]

    await service.complete_structured_task(
        system_instruction="return json",
        user_input="hi",
        max_tokens=256,
        caller="eval.relevance",
    )

    assert registry.max_tokens_seen == [MIN_STRUCTURED_MAX_TOKENS]


@pytest.mark.asyncio
async def test_structured_task_keeps_budgets_above_the_floor() -> None:
    registry = FakeRegistry(response=LLMResponse(content='{"ok": true}', provider="openai"))
    service = LLMService(registry=registry, memory=FakeMemoryManager("core"))  # type: ignore[arg-type]

    await service.complete_structured_task(
        system_instruction="return json",
        user_input="hi",
        max_tokens=16384,
        caller="discovery.evaluate_batch",
    )

    assert registry.max_tokens_seen == [16384]


@pytest.mark.asyncio
async def test_multimodal_structured_task_floors_small_max_tokens() -> None:
    registry = FakeRegistry(response=LLMResponse(content='{"ok": true}', provider="openai"))
    service = LLMService(registry=registry, memory=FakeMemoryManager("core"))  # type: ignore[arg-type]

    await service.complete_multimodal_structured_task(
        system_instruction="return json",
        user_input="hi",
        image_inputs=[{"content_id": "1", "data_url": "data:image/png;base64,AA=="}],
        max_tokens=512,
        caller="discovery.evaluate_batch",
    )

    assert registry.max_tokens_seen == [MIN_STRUCTURED_MAX_TOKENS]


@pytest.mark.asyncio
async def test_llm_service_forwards_reply_style_into_dialogue_prompt(tmp_path: Path) -> None:
    """issue #255: soul.reply_style reaches the dialogue tone block via LLMService."""
    memory = MemoryManager(tmp_path)
    registry = FakeRegistry(LLMResponse(content="好。", provider="openai"))
    service = LLMService(registry=registry, memory=memory, reply_style="像损友一样毒舌")

    await service.complete_socratic_dialogue(user_message="最近看点啥？", history=[])

    system_prompt = registry.calls[0][0]["content"]
    assert "- 回复风格: 像损友一样毒舌" in system_prompt


@pytest.mark.asyncio
async def test_llm_service_default_reply_style_leaves_dialogue_prompt_untouched(
    tmp_path: Path,
) -> None:
    memory = MemoryManager(tmp_path)
    registry = FakeRegistry(LLMResponse(content="好。", provider="openai"))
    service = LLMService(registry=registry, memory=memory)

    await service.complete_socratic_dialogue(user_message="最近看点啥？", history=[])

    assert "- 回复风格:" not in registry.calls[0][0]["content"]


@pytest.mark.asyncio
async def test_llm_service_forwards_dialogue_tone_prompt_into_dialogue_prompt(
    tmp_path: Path,
) -> None:
    """soul.dialogue_tone_prompt replaces the dialogue tone block via LLMService."""
    memory = MemoryManager(tmp_path)
    registry = FakeRegistry(LLMResponse(content="好。", provider="openai"))
    service = LLMService(
        registry=registry,
        memory=memory,
        reply_style="像损友一样毒舌",
        dialogue_tone_prompt="像一个老朋友：\n- 多用短句",
    )

    await service.complete_socratic_dialogue(user_message="最近看点啥？", history=[])

    system_prompt = registry.calls[0][0]["content"]
    assert "像一个老朋友：\n- 多用短句" in system_prompt
    assert "- 信息密度" not in system_prompt
    assert "- 回复风格:" not in system_prompt
    assert "你是 OpenBiliClaw，一个像朋友一样理解用户的 AI 伙伴。" in system_prompt


@pytest.mark.asyncio
async def test_llm_service_default_dialogue_tone_prompt_leaves_prompt_untouched(
    tmp_path: Path,
) -> None:
    memory = MemoryManager(tmp_path)
    registry = FakeRegistry(LLMResponse(content="好。", provider="openai"))
    service = LLMService(registry=registry, memory=memory)

    await service.complete_socratic_dialogue(user_message="最近看点啥？", history=[])

    assert "- 信息密度" in registry.calls[0][0]["content"]
