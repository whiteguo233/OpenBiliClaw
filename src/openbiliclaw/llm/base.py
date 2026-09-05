"""LLM base interfaces and provider registry.

Defines the abstract LLM provider interface and a registry for
dynamically selecting and switching between providers.
"""

from __future__ import annotations

import errno
import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from openbiliclaw.diagnostics_alerts import record_diagnostics_alert

logger = logging.getLogger(__name__)


def _classify_llm_error_code(exc: BaseException) -> str:
    """Map a provider failure to a stable diagnostics alert code."""
    if isinstance(exc, LLMRateLimitError):
        return "rate_limited"
    if isinstance(exc, LLMAuthError):
        return "auth_failed"
    if isinstance(exc, LLMTimeoutError):
        return "timeout"
    if isinstance(exc, LLMResponseError):
        return "bad_response"
    return "provider_error"


LLM_CONNECTIVITY_PROBE_MAX_TOKENS = 4096
# Balanced default for provider-native reasoning controls.  Channel-facing
# callers still pass ``""`` explicitly through LLMService; each adapter then
# disables reasoning or selects the cheapest supported approximation.
DEFAULT_REASONING_EFFORT = "medium"


class LLMProviderError(Exception):
    """Base exception for provider request failures."""


class LLMRateLimitError(LLMProviderError):
    """Raised when a provider rate-limits a request."""


class LLMAuthError(LLMProviderError):
    """Raised when a provider rejects our credentials (HTTP 401).

    Carries the configured endpoint identity so user-facing copy can name
    *which* key to fix. A user running SenseNova as ``openai_compatible``
    alongside a stale ``openai`` key otherwise gets a bare "check your API
    key" and no way to tell the two apart.

    Retrying is futile until the user edits config, so provider retry loops
    treat this as terminal — retrying a 401 three times per shard only
    multiplies the failed requests visible in the provider's console (which
    then reads as "my token has usage records, why 401?") and stretches the
    perceived hang.
    """

    def __init__(
        self,
        message: str,
        *,
        provider_name: str = "",
        endpoint: str = "",
    ) -> None:
        super().__init__(message)
        self.provider_name = provider_name
        self.endpoint = endpoint


class LLMTimeoutError(LLMProviderError):
    """Raised when a provider request times out."""


class LLMResponseError(LLMProviderError):
    """Raised when a provider returns an invalid or empty response."""


class LLMFallbackError(LLMProviderError):
    """Raised when all candidate providers fail."""


def classify_llm_unavailability(exc: BaseException) -> str | None:
    """Classify an exception chain as an expected-transient LLM outage.

    Walks the ``__cause__`` / ``__context__`` chain (cycle-safe) and returns:

    - ``"rate_limited"`` when any link is an :class:`LLMRateLimitError` or
      carries a "rate limit" message — a provider is cooling down and the
      caller should simply retry on its next cycle.
    - ``"no_provider"`` when any :class:`LLMFallbackError` /
      ``LLMProviderExecutionError`` in the chain reports that no provider was
      available (typically during guided init, before a chat LLM is
      configured).
    - ``"model_not_found"`` when the provider reachably answered but the
      configured model does not exist (a local Ollama model never pulled → 404
      ``not_found_error``, or a wrong/inaccessible model name). Retrying won't
      help until the user pulls/renames the model, but the loop should log one
      calm actionable line rather than a full traceback.
    - ``None`` for anything else — a genuine error the caller should keep
      logging loudly.

    ``rate_limited`` wins when both apply: an "all providers failed … rate
    limit" fallback wraps a rate-limit cause and should read as backoff, not a
    missing provider.
    """
    kind = classify_llm_failure_kind(exc)
    return kind if kind in {"rate_limited", "no_provider", "model_not_found"} else None


# Substrings that mark an upstream content-moderation / compliance refusal.
# Chinese compat gateways (e.g. iFlytek code 10013) return the refusal *as a
# 500*, so we cannot key off the HTTP status — we sniff the message instead.
_LLM_MODERATION_MARKERS = (
    "法律法规",
    "健康和谐",
    "无法提供关于",
    "内容审查",
    "content policy",
    "content_filter",
    "content management",
    "content exists risk",
    "risk_control",
    "10013",
)

_LLM_AUTH_MARKERS = (
    "authentication",
    "unauthorized",
    "unauthenticated",
    "invalid api key",
    "api key not valid",
)
# The bare status number is deliberately NOT a marker: these run against
# free-form upstream error bodies, where a request id (``req-1401ab``) or a
# 402 billing payload that happens to contain "401" would be misread as a bad
# API key — and auth outranks ``rate_limited`` in both classifiers below, so a
# quota problem would surface as "your key is wrong". Require the number to be
# qualified as a status: ``HTTP 401``, ``Error code: 401``, ``"code":401``,
# ``status_code=401``. ``(?!\d)`` keeps 4010 / 4011 out.
_LLM_AUTH_STATUS_RE = re.compile(r"(?:http|status|status_code|code|error code)\W{0,3}401(?!\d)")


def _is_auth_failure(exc: BaseException, message: str) -> bool:
    """Whether one link of an exception chain is a credential rejection."""
    if isinstance(exc, LLMAuthError):
        return True
    if any(marker in message for marker in _LLM_AUTH_MARKERS):
        return True
    return bool(_LLM_AUTH_STATUS_RE.search(message))


def _endpoint_host(endpoint: str) -> str:
    """Return the bare host of *endpoint*, or "" when it has none.

    Goes through ``urlsplit().hostname`` so a base_url carrying inline
    credentials (``https://user:secret@host/v1``) never reaches user-facing
    copy.
    """
    raw = (endpoint or "").strip()
    if not raw:
        return ""
    host = urlsplit(raw if "//" in raw else f"//{raw}").hostname or ""
    return host


def _auth_failure_target(auth_error: LLMAuthError | None) -> str:
    """Name the provider whose key was rejected, for user-facing copy."""
    provider = (auth_error.provider_name if auth_error else "").strip()
    host = _endpoint_host(auth_error.endpoint if auth_error else "")
    if provider and host:
        return f"{provider}（{host}）"
    if provider:
        return provider
    if host:
        return host
    return "所配置的 AI 服务"


# The provider host was reachable and answered, but the configured *model* is
# missing: a local Ollama model that was never pulled returns HTTP 404 with
# ``{"type": "not_found_error", "message": "model 'x' not found, try pulling it
# first"}``; OpenAI-compat 404s say ``the model 'x' does not exist``. Distinct
# from ``no_provider`` (no chat provider configured at all) and from auth (401):
# retrying is futile until the user pulls/renames the model, so callers should
# surface an actionable "pull the model / fix the name" hint, not a traceback.
_LLM_MODEL_NOT_FOUND_MARKERS = (
    "not_found_error",
    "try pulling it first",
    "no such model",
    "does not exist or you do not have access",
    "model does not exist",
)
_LLM_QUOTA_MARKERS = (
    "rate limit",
    "insufficient_quota",
    "insufficient quota",
    "quota",
    "exhausted",
    "429",
)

_LLM_TIMEOUT_MARKERS = ("timeout", "timed out", "deadline exceeded")
# SSL / certificate verification failures. Kept distinct from the generic
# connection markers because the actionable cause differs: a cert-verify
# failure on an otherwise-reachable host almost always means a local proxy /
# antivirus / firewall is doing HTTPS interception (or the endpoint uses a
# self-signed cert), so the user-facing hint points at the proxy, not the
# network. httpx raises ``ConnectError: [SSL: CERTIFICATE_VERIFY_FAILED]`` and
# the OpenAI SDK wraps it as ``APIConnectionError`` — neither subclasses
# Python's ``ConnectionError``, so we sniff the message.
_LLM_SSL_MARKERS = (
    "ssl:",
    "certificate verify failed",
    "certificate_verify_failed",
    "unable to get local issuer",
    "self-signed certificate",
    "self signed certificate",
    "sslcertverificationerror",
    "ssl handshake",
)
_LLM_CONNECTION_MARKERS = (
    "connection reset",
    "connection refused",
    "connection error",
    "connection aborted",
    "network is unreachable",
    "name resolution",
    "temporary failure in name resolution",
    "failed to establish a new connection",
    "max retries exceeded",
    "getaddrinfo failed",
)
_LLM_SERVER_ERROR_MARKERS = (
    "http 500",
    "http 502",
    "http 503",
    "http 504",
    "status 500",
    "status 502",
    "status 503",
    "status 504",
)
_LLM_INVALID_RESPONSE_MARKERS = (
    "empty response",
    "empty completion",
    "invalid response",
    "expected scored json",
)


def classify_llm_failure_kind(exc: BaseException) -> str | None:
    """Return a machine-readable LLM failure kind from an exception chain.

    The chain walk is cycle-safe. Specific provider throttling and missing
    provider states win over coarser timeout/response classifications.
    """

    # Lazily imported to avoid a circular import (service imports this module).
    from openbiliclaw.llm.service import LLMProviderExecutionError

    seen: set[int] = set()
    current: BaseException | None = exc
    rate_limited = no_provider = auth_failed = model_not_found = False
    timed_out = invalid_response = connection = server_error = False
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        message = str(current).lower()
        if isinstance(current, LLMRateLimitError) or any(
            marker in message for marker in _LLM_QUOTA_MARKERS
        ):
            rate_limited = True
        if isinstance(current, LLMFallbackError | LLMProviderExecutionError) and (
            "no provider was available" in message
        ):
            no_provider = True
        if any(marker in message for marker in _LLM_MODEL_NOT_FOUND_MARKERS) or (
            "model" in message and "not found" in message
        ):
            model_not_found = True
        if _is_auth_failure(current, message):
            auth_failed = True
        if isinstance(current, (LLMTimeoutError, TimeoutError)) or any(
            marker in message for marker in _LLM_TIMEOUT_MARKERS
        ):
            timed_out = True
        network_errno = isinstance(current, OSError) and current.errno in {
            errno.ECONNABORTED,
            errno.ECONNREFUSED,
            errno.ECONNRESET,
            errno.ENETDOWN,
            errno.ENETUNREACH,
            errno.EHOSTDOWN,
            errno.EHOSTUNREACH,
            errno.ETIMEDOUT,
        }
        if (
            isinstance(current, ConnectionError)
            or network_errno
            or any(marker in message for marker in _LLM_CONNECTION_MARKERS)
            or any(marker in message for marker in _LLM_SSL_MARKERS)
        ):
            connection = True
        if any(marker in message for marker in _LLM_SERVER_ERROR_MARKERS):
            server_error = True
        if isinstance(current, LLMResponseError) or any(
            marker in message for marker in _LLM_INVALID_RESPONSE_MARKERS
        ):
            invalid_response = True
        current = current.__cause__ or current.__context__
    if rate_limited:
        return "rate_limited"
    if no_provider:
        return "no_provider"
    if model_not_found:
        return "model_not_found"
    if auth_failed:
        return "auth_failed"
    if timed_out:
        return "timeout"
    if connection:
        return "connection"
    if server_error:
        return "server_error"
    if invalid_response:
        return "invalid_response"
    return None


def is_llm_moderation_error(exc: BaseException) -> bool:
    """Return True when an exception chain carries a content-moderation refusal.

    Callers such as the preference analyzer use this to tell a content-local
    refusal (split the batch, isolate the offending event, and skip only that
    event) apart from a genuine provider/configuration failure (raise).
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        message = str(current).lower()
        if any(marker in message for marker in _LLM_MODERATION_MARKERS):
            return True
        current = current.__cause__ or current.__context__
    return False


def describe_llm_failure(exc: BaseException) -> str | None:
    """Translate an LLM exception chain into a short, human-readable Chinese
    reason suitable for page-side display during guided init.

    Walks the ``__cause__`` / ``__context__`` chain (cycle-safe) and returns a
    one-line explanation the user can act on — a content-moderation refusal,
    authentication failure, exhausted provider/fallback chain, rate limiting,
    a timeout, or an empty response. Returns ``None`` when the chain carries no
    recognizable LLM signal, so callers can fall back to their own generic
    message.

    Ordering is by specificity: a moderation refusal is the most actionable
    (switch models), so it wins over the coarser transient buckets.
    """
    # Lazily imported to avoid a circular import (service imports this module).
    from openbiliclaw.llm.service import LLMProviderExecutionError, LLMResponseContentError

    seen: set[int] = set()
    current: BaseException | None = exc
    moderation = auth_failed = rate_limited = False
    timed_out = no_provider = empty_response = False
    ssl_failed = connect_failed = model_not_found = False
    auth_error: LLMAuthError | None = None
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        message = str(current).lower()
        if any(marker.lower() in message for marker in _LLM_MODERATION_MARKERS):
            moderation = True
        if any(marker in message for marker in _LLM_MODEL_NOT_FOUND_MARKERS) or (
            "model" in message and "not found" in message
        ):
            model_not_found = True
        if _is_auth_failure(current, message):
            auth_failed = True
            # First typed error along the chain. Wrappers (fallback / service)
            # are never LLMAuthError, so this is the adapter that actually got
            # the 401 — and when a fallback chain had several, it is the last
            # instance tried, which is the one the user just watched fail.
            if auth_error is None and isinstance(current, LLMAuthError):
                auth_error = current
        if isinstance(current, LLMRateLimitError) or any(
            marker in message for marker in _LLM_QUOTA_MARKERS
        ):
            rate_limited = True
        if isinstance(current, LLMTimeoutError | TimeoutError) or "timed out" in message:
            timed_out = True
        if any(marker in message for marker in _LLM_SSL_MARKERS):
            ssl_failed = True
        network_errno = isinstance(current, OSError) and current.errno in {
            errno.ECONNABORTED,
            errno.ECONNREFUSED,
            errno.ECONNRESET,
            errno.ENETDOWN,
            errno.ENETUNREACH,
            errno.EHOSTDOWN,
            errno.EHOSTUNREACH,
            errno.ETIMEDOUT,
        }
        if (
            isinstance(current, ConnectionError)
            or network_errno
            or any(marker in message for marker in _LLM_CONNECTION_MARKERS)
        ):
            connect_failed = True
        if isinstance(current, LLMFallbackError | LLMProviderExecutionError) and (
            "no provider was available" in message
        ):
            no_provider = True
        if isinstance(current, LLMResponseError | LLMResponseContentError):
            empty_response = True
        current = current.__cause__ or current.__context__

    if moderation:
        return (
            "AI 服务上游因内容合规策略拒绝了本次请求；可更换一个不带内容审查的模型 / 服务商后重试。"
        )
    if model_not_found:
        return (
            "AI 服务找不到所配置的模型（HTTP 404）。本地 Ollama 模型可能尚未拉取"
            "（先执行 `ollama pull <模型名>`），或模型名 / 访问权限填错。"
            "请到设置页核对对话模型名称后重试。"
        )
    if auth_failed:
        target = _auth_failure_target(auth_error)
        return (
            f"AI 服务鉴权失败（HTTP 401）：{target}拒绝了当前 API key。"
            "key 可能填错、已失效，或是有有效期的临时 token（过期后需重新生成）。"
            "请到设置页检查该 provider 的 API key 后重试。"
        )
    if rate_limited:
        return (
            "AI 服务额度用尽或被限流（HTTP 429）。请检查 LLM provider 的余额 / 套餐，"
            "或在设置里配置一个备选 provider 兜底后重试。"
        )
    if timed_out:
        return "AI 服务响应超时；请检查网络连通性或稍后重试。"
    if ssl_failed:
        return (
            "无法与 AI 服务建立安全连接（SSL 证书验证失败）。"
            "常见原因是本地代理 / 杀毒 / 防火墙对 HTTPS 做了中间人拦截，"
            "或接口地址使用了自签证书。请关闭代理（或把该接口地址加入直连白名单）后重试。"
        )
    if connect_failed:
        return (
            "无法连接到 AI 服务（网络连接失败）。"
            "请检查网络、接口地址是否正确，以及代理 / 防火墙设置后重试。"
        )
    if no_provider:
        return (
            "没有可用的 AI 服务：主 Provider 与备用 Provider 都调用失败，"
            "请检查 LLM 配置、密钥与网络。"
        )
    if empty_response:
        return "AI 服务返回了空响应或无法解析的内容；请更换模型或稍后重试。"
    return None


def safe_llm_failure_message(exc: BaseException) -> str:
    """Return actionable LLM failure copy without exposing upstream detail."""
    return describe_llm_failure(exc) or (
        "AI 服务暂时不可用；请稍后重试，或检查设置中的模型与网络。"
    )


@dataclass
class LLMResponse:
    """Standardized response from any LLM provider."""

    content: str = ""
    model: str = ""
    # Stable configured endpoint identity. ``provider`` remains the adapter /
    # pricing type (openai, deepseek, ...), while instance_id distinguishes two
    # accounts or gateways that use the same adapter.
    instance_id: str = ""
    provider: str = ""
    usage: dict[str, int] | None = None  # token counts
    raw: Any = None  # Raw provider response
    tool_calls: list[dict[str, Any]] | None = None  # Phase 4: function calling


@dataclass
class HealthCheckResult:
    """Availability result for one provider."""

    available: bool
    is_default: bool = False
    error: str | None = None


class LLMProvider(ABC):
    """Abstract base class for LLM providers.

    All providers must implement a unified interface so the agent
    can switch between them transparently.
    """

    # Subclasses set True if they implement an ``async embed()`` method
    # backed by a working embeddings endpoint. Used by
    # ``build_embedding_service`` to pick a fallback when the user's
    # primary provider has no embedding API (e.g. Anthropic Claude,
    # DeepSeek). ``hasattr(provider, "embed")`` is unreliable because
    # subclassing OpenAIProvider auto-inherits ``embed`` even for
    # vendors whose backend doesn't actually expose it.
    supports_embedding: bool = False

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name identifier."""
        ...

    @abstractmethod
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
        """Send a chat completion request.

        Args:
            messages: Chat messages in OpenAI format [{role, content}].
            temperature: Sampling temperature.
            max_tokens: Maximum tokens in response.
            json_mode: Whether to request structured JSON output.
            reasoning_effort: Per-call override for the provider's
                reasoning control. ``None`` means "use the provider's
                configured default" (``medium`` for adapters with a portable
                effort control); ``""`` requests no reasoning for this call.
                Providers whose models cannot fully disable thinking use the
                lowest supported level instead.
            model: Optional per-call model override. Empty/whitespace
                values fall back to the provider's configured default
                without mutating provider state.

        Returns:
            Standardized LLMResponse.
        """
        ...

    async def health_check(self) -> bool:
        """Check if the provider is accessible.

        Returns:
            True if the provider is available.
        """
        try:
            # Reasoning-first OpenAI-compatible backends may spend the
            # initial output budget on reasoning before emitting content.
            # Keep the connectivity probe small, but not so tiny that those
            # providers get truncated before they can return visible content.
            resp = await self.complete(
                [{"role": "user", "content": "hi"}],
                max_tokens=LLM_CONNECTIVITY_PROBE_MAX_TOKENS,
                # A connectivity probe only needs one visible response.
                # Letting DeepSeek inherit reasoning_effort="max" turns
                # this tiny probe into a 32K-token thinking request, which
                # commonly exceeds the guided-init timeout and falsely marks
                # a healthy provider unavailable.
                reasoning_effort="",
            )
            return bool(resp.content)
        except Exception:
            logger.exception("Health check failed for %s", self.name)
            return False


class LLMRegistry:
    """Registry for LLM providers.

    Supports dynamic registration and selection of providers.
    """

    _RATE_LIMIT_COOLDOWN_SECONDS = 60.0
    _RATE_LIMIT_MAX_COOLDOWN_SECONDS = 600.0

    def __init__(self) -> None:
        self._providers: dict[str, LLMProvider] = {}
        self._provider_types: dict[str, str] = {}
        self._default: str = ""
        self._rate_limited_until: dict[str, float] = {}
        self._rate_limit_attempts: dict[str, int] = {}
        # A non-empty fallback_provider IS the enable switch — there is no
        # separate boolean (the legacy [llm].fallback_enabled flag was never
        # consulted and has been removed; empty provider = fallback off).
        self.fallback_provider: str = ""
        # v2 ordered route. When non-empty it is authoritative and may contain
        # any number of configured endpoint instances.
        self.fallback_chain: list[str] = []
        # Names of providers that should NOT appear in the chat-completion
        # fallback chain — for example a legacy/injected Ollama instance
        # registered solely for embedding diagnostics.
        self._chat_disabled: set[str] = set()

    def register(
        self,
        provider: LLMProvider,
        *,
        name: str | None = None,
        provider_type: str | None = None,
        default: bool = False,
        chat_capable: bool = True,
    ) -> None:
        """Register a provider.

        Args:
            provider: LLM provider instance.
            default: Whether to set as default provider.
            chat_capable: When False, the provider is registered for
                non-chat use and will NOT appear in the chat-completion
                fallback chain. Default True for backward compatibility.

                Modern embedding builds a dedicated provider outside this
                registry. The flag remains a defensive boundary for legacy
                or injected non-chat providers: a local ``bge-m3`` instance
                must never receive ``/api/chat`` fallback requests.
        """
        registry_name = str(name or provider.name).strip().lower()
        adapter_name = str(provider_type or provider.name).strip().lower()
        self._providers[registry_name] = provider
        self._provider_types[registry_name] = adapter_name
        if not chat_capable:
            self._chat_disabled.add(registry_name)
        else:
            self._chat_disabled.discard(registry_name)
        if default or not self._default:
            self._default = registry_name
        logger.info(
            "Registered LLM instance: %s (provider_type=%s)%s%s",
            registry_name,
            adapter_name,
            " (default)" if default else "",
            "" if chat_capable else " [embedding-only]",
        )

    def configure_chain(self, instance_ids: list[str]) -> None:
        """Set the ordered global route without silently adding providers."""
        seen: set[str] = set()
        chain: list[str] = []
        for raw_name in instance_ids:
            name = str(raw_name or "").strip().lower()
            if not name or name in seen:
                continue
            seen.add(name)
            chain.append(name)
        self.fallback_chain = chain
        if chain and chain[0] in self._providers:
            self._default = chain[0]
        self.fallback_provider = chain[1] if len(chain) > 1 else ""

    def get(self, name: str | None = None) -> LLMProvider:
        """Get a provider by name, or the default.

        Args:
            name: Provider name. If None, returns the default.

        Returns:
            LLM provider instance.

        Raises:
            KeyError: If the provider is not registered.
        """
        target = name or self._default
        if target not in self._providers:
            available = ", ".join(self._providers.keys())
            raise KeyError(f"LLM provider '{target}' not found. Available: {available}")
        return self._providers[target]

    @property
    def available_providers(self) -> list[str]:
        """List of registered provider names."""
        return list(self._providers.keys())

    @property
    def default_provider(self) -> str:
        """Name of the default provider."""
        return self._default

    def provider_type(self, name: str | None = None) -> str:
        """Return the adapter/pricing type for an instance."""
        target = str(name or self._default).strip().lower()
        return self._provider_types.get(target, "")

    def is_chat_capable(self, name: str) -> bool:
        """Return whether *name* is registered for chat completions."""
        target = name.strip().lower()
        return bool(target and target in self._providers and target not in self._chat_disabled)

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        """Execute a completion request with sequential provider fallback."""
        return await self.complete_chain(
            self._fallback_order(),
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
            reasoning_effort=reasoning_effort,
        )

    async def complete_chain(
        self,
        instance_ids: list[str] | tuple[str, ...],
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        """Execute one explicit ordered instance chain."""
        last_error: Exception | None = None
        attempted: list[str] = []
        seen: set[str] = set()
        order: list[str] = []
        for raw_name in instance_ids:
            instance_id = str(raw_name or "").strip().lower()
            if not instance_id or instance_id in seen or not self.is_chat_capable(instance_id):
                continue
            seen.add(instance_id)
            order.append(instance_id)

        for position, provider_name in enumerate(order):
            has_next = position + 1 < len(order)
            attempted.append(provider_name)
            if self._provider_on_cooldown(provider_name):
                last_error = LLMRateLimitError(
                    f"Provider {provider_name} is cooling down after rate limit."
                )
                logger.warning("Provider %s is cooling down after rate limit.", provider_name)
                continue
            provider = self.get(provider_name)
            try:
                response = await provider.complete(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    json_mode=json_mode,
                    reasoning_effort=reasoning_effort,
                )
                self._rate_limited_until.pop(provider_name, None)
                self._rate_limit_attempts.pop(provider_name, None)
                response.instance_id = provider_name
                return response
            except LLMRateLimitError as exc:
                last_error = exc
                self._mark_rate_limited(provider_name)
                self._log_provider_failure(provider_name, has_next=has_next)
                record_diagnostics_alert(
                    category="llm",
                    code="rate_limited",
                    message=str(exc) or "LLM provider returned HTTP 429 (rate limited).",
                    source=provider_name,
                )
            # LLMResponseError (empty/malformed content — flaky gateways
            # commonly die by returning 200 with no content) falls through to
            # the next provider like any other failure: the provider already
            # did its own single in-place retry, and a different provider may
            # well answer the same prompt.
            except (LLMProviderError, LLMTimeoutError) as exc:
                last_error = exc
                self._log_provider_failure(provider_name, has_next=has_next)
                record_diagnostics_alert(
                    category="llm",
                    code=_classify_llm_error_code(exc),
                    message=str(exc),
                    source=provider_name,
                )

        attempted_list = ", ".join(attempted)
        if last_error is None:
            raise LLMFallbackError("No provider was available to process the request.")
        record_diagnostics_alert(
            category="llm",
            code="all_providers_failed",
            message=f"所有 LLM 实例均请求失败（{attempted_list}），最后错误：{last_error}",
            source=attempted_list,
            severity="error",
        )
        raise LLMFallbackError(
            f"All providers failed ({attempted_list}). Last error: {last_error}"
        ) from last_error

    @staticmethod
    def _log_provider_failure(provider_name: str, *, has_next: bool) -> None:
        if has_next:
            logger.warning("Provider %s failed, trying next fallback.", provider_name)
        else:
            logger.warning("Provider %s failed; no fallback provider left to try.", provider_name)

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
        """Execute a completion against one exact chat-capable provider.

        Unlike ``complete()``, this method intentionally has no fallback
        chain. It is used for explicit per-module overrides where
        falling back to a different provider would violate user intent.
        """
        target = provider_name.strip().lower()
        if not self.is_chat_capable(target):
            available = ", ".join(self._fallback_order())
            raise LLMFallbackError(
                f"LLM provider '{target or provider_name}' is not registered "
                f"or not chat-capable. Chat-capable providers: {available}"
            )
        if self._provider_on_cooldown(target):
            logger.warning("Provider %s is cooling down after rate limit.", target)
            raise LLMRateLimitError(f"Provider {target} is cooling down after rate limit.")

        provider = self.get(target)
        try:
            response = await provider.complete(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=json_mode,
                reasoning_effort=reasoning_effort,
                model=model,
            )
            self._rate_limited_until.pop(target, None)
            self._rate_limit_attempts.pop(target, None)
            response.instance_id = target
            return response
        except LLMRateLimitError:
            self._mark_rate_limited(target)
            logger.warning("Provider %s rate-limited exact routed call.", target)
            record_diagnostics_alert(
                category="llm",
                code="rate_limited",
                message=f"LLM 实例 {target} 被限流（HTTP 429），精确路由调用失败。",
                source=target,
            )
            raise

    async def health_check_all(self) -> dict[str, HealthCheckResult]:
        """Run health checks for all registered chat-capable providers."""
        results: dict[str, HealthCheckResult] = {}
        for provider_name in self.available_providers:
            # ``health_check`` is a real chat completion. Never send one to
            # an embedding-only registration: that used to probe an implicit
            # ``llama3`` model and produced recurring Ollama 404s.
            if not self.is_chat_capable(provider_name):
                continue
            provider = self.get(provider_name)
            try:
                available = await provider.health_check()
                results[provider_name] = HealthCheckResult(
                    available=available,
                    is_default=provider_name == self._default,
                    error=None if available else "health check returned false",
                )
            except Exception as exc:
                results[provider_name] = HealthCheckResult(
                    available=False,
                    is_default=provider_name == self._default,
                    error=str(exc),
                )
        return results

    def _fallback_order(self) -> list[str]:
        """Return the sequential CHAT-fallback provider order.

        Skips providers registered with ``chat_capable=False`` (the
        embedding-only Ollama case). The default provider is honored
        whenever it's chat-capable. A fallback provider is included only
        when ``fallback_provider`` names a registered chat provider; no
        automatic provider walk is performed.
        """
        chat_pool = [name for name in self.available_providers if name not in self._chat_disabled]
        if not chat_pool:
            # Edge case: every provider is embedding-only. Surface the
            # problem rather than silently doing nothing — complete()
            # will raise LLMFallbackError("No provider was available
            # to process the request.").
            return []
        if self.fallback_chain:
            return [name for name in self.fallback_chain if name in chat_pool]
        if self._default and self._default in chat_pool:
            ordered = [
                self._default,
                *[name for name in chat_pool if name != self._default],
            ]
        else:
            ordered = chat_pool
        fallback_provider = self.fallback_provider.strip().lower()
        if not fallback_provider:
            return ordered[:1]
        if fallback_provider == ordered[0] or fallback_provider not in chat_pool:
            return ordered[:1]
        return [ordered[0], fallback_provider]

    def _provider_on_cooldown(self, provider_name: str) -> bool:
        until = self._rate_limited_until.get(provider_name)
        if until is None:
            return False
        if until > time.monotonic():
            return True
        self._rate_limited_until.pop(provider_name, None)
        return False

    def _mark_rate_limited(self, provider_name: str) -> None:
        attempts = self._rate_limit_attempts.get(provider_name, 0) + 1
        self._rate_limit_attempts[provider_name] = attempts
        cooldown = min(
            self._RATE_LIMIT_MAX_COOLDOWN_SECONDS,
            self._RATE_LIMIT_COOLDOWN_SECONDS * (2 ** (attempts - 1)),
        )
        self._rate_limited_until[provider_name] = time.monotonic() + cooldown
        logger.warning(
            "Provider %s marked rate-limited; cooldown=%.0fs (attempt=%d)",
            provider_name,
            cooldown,
            attempts,
        )
