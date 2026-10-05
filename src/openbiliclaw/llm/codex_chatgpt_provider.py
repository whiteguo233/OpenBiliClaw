"""OpenAI Codex ChatGPT subscription transport (experimental).

ChatGPT / Codex OAuth tokens are NOT valid OpenAI Platform API keys: sending
them to ``api.openai.com/v1`` fails with ``Missing scopes:
api.responses.write``. The official Codex CLI routes ChatGPT-subscription
traffic through ``https://chatgpt.com/backend-api/codex/responses`` instead.
This module implements that same transport in Python:

- strict target-domain pinning (only ``chatgpt.com/backend-api``);
- Bearer token + ``chatgpt-account-id`` auth headers extracted from the
  Codex JWT, matching the official Codex CLI request shape;
- SSE (``text/event-stream``) response parsing;
- single 401 token-refresh retry via the Codex OAuth refresh endpoint.

This module never logs token values or full response bodies.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx

from openbiliclaw import network
from openbiliclaw.llm.codex_auth import (
    CodexAuthError,
    CodexProbeState,
    get_valid_codex_token,
    load_codex_credentials,
)

from .base import (
    DEFAULT_REASONING_EFFORT,
    LLMAuthError,
    LLMProvider,
    LLMProviderError,
    LLMRateLimitError,
    LLMResponse,
    LLMResponseError,
    LLMTimeoutError,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

_DEFAULT_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
_CODEX_MODELS_URL = "https://chatgpt.com/backend-api/codex/models?client_version=1.0.0"
# ChatGPT-subscription Codex backend accepts Codex-line models (gpt-5.4,
# gpt-5.5, gpt-5.6-*, gpt-5.3-codex-spark, ...). Platform-API models such as
# ``gpt-5-nano`` are rejected with HTTP 400 by this transport. When the user
# hasn't picked a model we default to a stable Codex-backend slug and, for
# probes, prefer live catalog discovery first.
_DEFAULT_CODEX_MODEL = "gpt-5.4"
_CODEX_PROBE_TIMEOUT_SECONDS = 60.0
_MAX_RETRIES = 3
_BASE_RETRY_DELAY = 0.25
# The endpoint answers with Cloudflare/edge status codes as well; 429 and
# retryable 5xx get a short bounded backoff before giving up.
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_ALLOWED_CODEX_BASE_URL_PATHS = {
    "",
    "/backend-api",
    "/backend-api/v1",
    "/backend-api/codex",
    "/backend-api/codex/v1",
    "/backend-api/codex/responses",
}
_OPENAI_REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}


@dataclass(frozen=True)
class CodexProbeResult:
    """Outcome of a live Codex ChatGPT transport probe."""

    ok: bool
    model: str = ""
    message: str = ""
    latency_ms: int = 0

    def to_probe_state(self) -> CodexProbeState:
        return CodexProbeState(
            ok=self.ok,
            checked_at=time.time(),
            model=self.model,
            message=self.message,
        )


def normalize_codex_base_url(base_url: str) -> str:
    """Normalize a user-supplied Codex base URL to the pinned endpoint.

    Only empty / official ``chatgpt.com/backend-api`` values are accepted;
    anything else raises :class:`LLMAuthError` so a ChatGPT token can never
    be sent to a third-party relay or to the Platform API host.
    """
    raw = (base_url or "").strip()
    if not raw:
        return _DEFAULT_CODEX_BASE_URL
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").rstrip("/").lower()
    if (
        parsed.scheme != "https"
        or host != "chatgpt.com"
        or path not in _ALLOWED_CODEX_BASE_URL_PATHS
        or parsed.query
        or parsed.fragment
    ):
        raise LLMAuthError(
            "Codex OAuth 只允许使用官方端点 https://chatgpt.com/backend-api；"
            "拒绝把 ChatGPT token 发送到第三方或 Platform API。",
            provider_name="openai",
            endpoint=raw,
        )
    return "https://chatgpt.com/backend-api/codex"


async def fetch_codex_models(
    *,
    access_token: str,
    account_id: str = "",
    timeout: float = 30.0,
) -> list[str]:
    """Return visible model slugs from the official Codex backend catalog.

    Uses the same ``chatgpt.com/backend-api/codex/models`` endpoint the
    official Codex CLI queries. ``visibility="hide"`` entries are filtered
    out; ``supported_in_api`` is intentionally ignored because it describes
    the public OpenAI Platform API, not this OAuth-backed Codex route.
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    if account_id.strip():
        headers["ChatGPT-Account-Id"] = account_id.strip()
    kwargs = network.httpx_kwargs_for_endpoint(_CODEX_MODELS_URL)
    kwargs["timeout"] = timeout
    try:
        async with httpx.AsyncClient(**kwargs) as client:
            response = await client.get(_CODEX_MODELS_URL, headers=headers)
        if response.status_code != 200:
            return []
        data = response.json()
        entries = data.get("models", []) if isinstance(data, dict) else []
    except Exception:
        logger.debug("Failed to fetch codex model catalog", exc_info=True)
        return []
    sortable: list[tuple[int, str]] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        slug = item.get("slug")
        if not isinstance(slug, str) or not slug.strip():
            continue
        visibility = str(item.get("visibility") or "")
        if visibility.strip().lower() in {"hide", "hidden"}:
            continue
        priority = item.get("priority")
        rank = int(priority) if isinstance(priority, int | float) else 10_000
        sortable.append((rank, slug.strip()))
    sortable.sort(key=lambda entry: (entry[0], entry[1]))
    return [slug for _rank, slug in sortable]


def codex_account_id_from_token(access_token: str) -> str:
    """Extract ``chatgpt_account_id`` from the Codex JWT payload.

    Returns "" when the claim is missing so callers can fall back to the
    credential record's stored account id.
    """
    from openbiliclaw.llm.codex_auth import _decode_jwt_payload

    payload = _decode_jwt_payload(access_token)
    auth_claim = payload.get("https://api.openai.com/auth")
    if isinstance(auth_claim, dict):
        raw = auth_claim.get("chatgpt_account_id")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return ""


class CodexChatGPTProvider(LLMProvider):
    """ChatGPT-subscription LLM provider via the official Codex backend.

    This adapter intentionally does NOT subclass
    :class:`openbiliclaw.llm.openai_provider.OpenAIProvider`: the wire
    contract is the Codex backend's SSE Responses stream, not the OpenAI
    Platform SDK surface, and sending a ChatGPT token through that SDK to
    ``api.openai.com`` is exactly the failure mode this class replaces.
    """

    supports_embedding = False

    def __init__(
        self,
        access_token: str,
        *,
        account_id: str = "",
        model: str = _DEFAULT_CODEX_MODEL,
        base_url: str = "",
        token_provider: Callable[[bool], Awaitable[str]] | None = None,
        timeout: float = 1200.0,
        reasoning_effort: str = DEFAULT_REASONING_EFFORT,
    ) -> None:
        self._model = (model or "").strip() or _DEFAULT_CODEX_MODEL
        self.base_url = normalize_codex_base_url(base_url)
        self._access_token = access_token.strip()
        self._account_id = (account_id or "").strip() or codex_account_id_from_token(
            self._access_token
        )
        self._token_provider = token_provider
        self._timeout = timeout
        self._reasoning_effort = reasoning_effort.strip()
        if not self._access_token:
            raise LLMAuthError(
                "Codex OAuth access token 为空，请先运行 `openbiliclaw login codex`。",
                provider_name="openai",
                endpoint=self.base_url,
            )

    @property
    def name(self) -> str:
        return "openai"

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
        """Send one completion through the Codex ChatGPT Responses stream.

        ``temperature`` is intentionally not forwarded: the Codex ChatGPT
        endpoint targets reasoning-first models (gpt-5 family) whose
        Responses contract rejects that parameter. Callers already pass
        ``temperature=0`` for deterministic probes; that intent is preserved
        by the request's ``store: false`` plus the system prompt contract.
        """
        del temperature
        effective_model = (model or "").strip() or self._model
        effective_reasoning_effort = (
            self._reasoning_effort if reasoning_effort is None else reasoning_effort
        ).strip()
        body = self._build_request_body(
            messages,
            effective_model=effective_model,
            max_tokens=max_tokens,
            json_mode=json_mode,
            reasoning_effort=effective_reasoning_effort,
        )

        try:
            status, response_text = await self._post_with_retry(body)
        except LLMProviderError:
            raise
        if status not in (200, 201):
            if (
                json_mode
                and "format" in response_text.lower()
                and "format" in str(body.get("text"))
            ):
                logger.warning(
                    "openai codex rejected text.format on /responses; retrying without it"
                )
                body["text"].pop("format", None)
                status, response_text = await self._post_with_retry(body)
            if status not in (200, 201):
                raise self._map_http_error(status, response_text)

        parsed = _parse_codex_sse_response(response_text)
        content = parsed.text_content.strip()
        if not content.strip() and json_mode:
            # Same flaky-gateway quirk as the OpenAI-protocol provider: HTTP
            # 200 with no visible text when an output-format constraint is
            # set. The prompt already demands JSON, so drop the constraint.
            logger.warning(
                "openai codex returned empty content with text.format; retrying without it"
            )
            body.pop("text", None)
            status, response_text = await self._post_with_retry(body)
            if status not in (200, 201):
                raise self._map_http_error(status, response_text)
            parsed = _parse_codex_sse_response(response_text)
            content = parsed.text_content.strip()
        if not content:
            raise LLMResponseError("openai codex returned empty content")

        return LLMResponse(
            content=content,
            model=parsed.model or effective_model,
            provider=self.name,
            usage=parsed.usage,
            raw=None,
        )

    async def capability_probe(
        self,
        *,
        timeout: float = _CODEX_PROBE_TIMEOUT_SECONDS,
    ) -> CodexProbeResult:
        """Run one tiny real completion and return a structured outcome."""
        started = time.perf_counter()
        try:
            response = await asyncio.wait_for(
                self.complete(
                    [
                        {"role": "system", "content": "Reply with only OK."},
                        {"role": "user", "content": "OpenBiliClaw Codex capability probe."},
                    ],
                    temperature=0,
                    max_tokens=16,
                    reasoning_effort="",
                ),
                timeout=timeout,
            )
            ok = bool(response.content.strip())
            return CodexProbeResult(
                ok=ok,
                model=response.model or self._model,
                message="" if ok else "Codex LLM 通道返回了空响应。",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        except Exception as exc:
            return CodexProbeResult(
                ok=False,
                model=self._model,
                message=str(exc) or exc.__class__.__name__,
                latency_ms=int((time.perf_counter() - started) * 1000),
            )

    def _build_request_body(
        self,
        messages: list[dict[str, str]],
        *,
        effective_model: str,
        max_tokens: int,
        json_mode: bool,
        reasoning_effort: str,
    ) -> dict[str, Any]:
        """Build a Codex Responses API body (streaming SSE contract)."""
        instructions = ""
        input_items: list[dict[str, Any]] = []
        for msg in messages:
            role = str(msg.get("role") or "").strip()
            content = str(msg.get("content") or "")
            if role == "system" and not instructions:
                instructions = content
                continue
            if role not in {"user", "assistant"}:
                continue
            content_type = "output_text" if role == "assistant" else "input_text"
            input_items.append(
                {
                    "role": role,
                    "content": [{"type": content_type, "text": content}],
                }
            )

        body: dict[str, Any] = {
            "model": effective_model,
            "store": False,
            "stream": True,
            "input": input_items,
            "text": {"verbosity": "medium"},
            "include": ["reasoning.encrypted_content"],
        }
        if instructions:
            body["instructions"] = instructions
        # ``max_output_tokens`` is deliberately NOT forwarded: the Codex
        # ChatGPT backend rejects it (HTTP 400 "Unsupported parameter:
        # max_output_tokens"), matching the official Codex CLI which lets
        # the backend apply each model's own output budget.
        del max_tokens
        if json_mode:
            body["text"]["format"] = {"type": "json_object"}
        if reasoning_effort in _OPENAI_REASONING_EFFORTS:
            body["reasoning"] = {"effort": reasoning_effort, "summary": "auto"}
        return body

    def _build_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "chatgpt-account-id": self._account_id,
            "OpenAI-Beta": "responses=experimental",
            "originator": "openbiliclaw",
            "User-Agent": "openbiliclaw (codex-chatgpt-transport)",
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        }

    async def _post_with_retry(self, body: dict[str, Any]) -> tuple[int, str]:
        """POST *body* to the Codex endpoint with bounded retry.

        A 401 triggers at most one forced token refresh + retry. The
        refresh error is surfaced with its real cause so a token that
        simply lacks LLM-call capability is reported as such instead of
        being swallowed as a generic transport failure.
        """
        url = f"{self.base_url}/responses"
        headers = self._build_headers()
        last_error: Exception | None = None
        refreshed_after_401 = False

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                status, response_text = await self._post_once(url, headers, body)
                if status in _RETRYABLE_STATUS_CODES and attempt < _MAX_RETRIES:
                    last_error = self._map_http_error(status, response_text)
                    await asyncio.sleep(_BASE_RETRY_DELAY * attempt)
                    continue
                return status, response_text
            except CodexAuthError:
                raise
            except Exception as exc:
                if self._is_unauthorized(exc) and self._token_provider is not None:
                    if refreshed_after_401:
                        unauthorized_status = self._status_code_int(
                            getattr(exc, "status_code", None)
                        )
                        raise self._map_http_error(
                            unauthorized_status or 401, _error_body_text(exc)
                        ) from exc
                    try:
                        await self._refresh_access_token()
                    except Exception as refresh_exc:
                        raise self._map_refresh_error(refresh_exc) from refresh_exc
                    refreshed_after_401 = True
                    headers = self._build_headers()
                    continue
                mapped = self._map_error(exc)
                last_error = mapped
                if not self._is_retryable(mapped) or attempt == _MAX_RETRIES:
                    raise mapped from exc
                await asyncio.sleep(_BASE_RETRY_DELAY * attempt)

        if last_error is None:
            raise LLMProviderError("openai codex request failed")
        raise last_error

    async def _post_once(
        self, url: str, headers: dict[str, str], body: dict[str, Any]
    ) -> tuple[int, str]:
        kwargs = network.httpx_kwargs_for_endpoint(self.base_url)
        kwargs["timeout"] = self._timeout
        async with httpx.AsyncClient(**kwargs) as client:
            response = await client.post(url, headers=headers, json=body)
        text = response.text
        if response.status_code == 401:
            # Preserve the upstream reason (e.g. Cloudflare / scope errors)
            # so the mapped error can explain whether the token is expired
            # or simply lacks LLM-call capability.
            raise _CodexHTTPError(
                _safe_error_excerpt(text, response.status_code),
                status_code=response.status_code,
                body=text,
            )
        return response.status_code, text

    async def _refresh_access_token(self) -> None:
        if self._token_provider is None:
            return
        token = await self._token_provider(True)
        if token and token.strip():
            self._access_token = token.strip()
            self._account_id = codex_account_id_from_token(self._access_token) or self._account_id

    @staticmethod
    def _is_unauthorized(exc: Exception) -> bool:
        return getattr(exc, "status_code", None) == 401

    @staticmethod
    def _status_code_int(status_code: object) -> int | None:
        if isinstance(status_code, int):
            return status_code
        if isinstance(status_code, str):
            try:
                return int(status_code.strip())
            except ValueError:
                return None
        return None

    def _map_error(self, exc: Exception) -> LLMProviderError:
        if isinstance(exc, LLMProviderError):
            return exc
        status = self._status_code_int(getattr(exc, "status_code", None))
        if status:
            body = _error_body_text(exc)
            return self._map_http_error(status, body)
        if isinstance(exc, TimeoutError):
            return LLMTimeoutError("openai codex request timed out")
        message = str(exc).lower()
        if "timeout" in message or "timed out" in message:
            return LLMTimeoutError(f"openai codex request timed out: {exc}")
        if any(marker in message for marker in ("connect", "resolve", "network")):
            return LLMProviderError(f"openai codex network error: {exc}")
        return LLMProviderError(f"openai codex request failed: {exc}")

    def _map_http_error(self, status: int, body_text: str) -> LLMProviderError:
        excerpt = _safe_error_excerpt(body_text, status)
        lower = excerpt.lower()
        if status == 401:
            detail = excerpt or "HTTP 401"
            if "missing scopes" in lower or "insufficient permissions" in lower:
                return LLMAuthError(
                    "Codex OAuth token 无法用于 LLM 调用：OpenAI 返回 "
                    f"HTTP 401 Missing scopes（{detail}）。该令牌只具备 Codex CLI 登录权限，"
                    '请改用 OpenAI Platform API Key 配置 `auth_mode = "api_key"`。',
                    provider_name="openai",
                    endpoint=self.base_url,
                )
            return LLMAuthError(
                f"openai codex authentication failed: HTTP 401: {detail}",
                provider_name="openai",
                endpoint=self.base_url,
            )
        if status == 429:
            return LLMRateLimitError(f"openai codex rate limit exceeded: {excerpt}")
        if status == 402:
            return LLMRateLimitError(f"openai codex provider backoff: HTTP {status}: {excerpt}")
        if status >= 500:
            return LLMProviderError(f"openai codex server error: HTTP {status}: {excerpt}")
        return LLMProviderError(f"openai codex request failed: HTTP {status}: {excerpt}")

    @staticmethod
    def _is_retryable(exc: LLMProviderError) -> bool:
        if isinstance(exc, (LLMAuthError, LLMRateLimitError)):
            return False
        return isinstance(exc, LLMProviderError)

    def _map_refresh_error(self, exc: Exception) -> LLMProviderError:
        if isinstance(exc, LLMProviderError):
            return exc
        return LLMAuthError(
            f"Codex OAuth token 刷新失败；请重新运行 `openbiliclaw login codex`。（原因: {exc}）",
            provider_name="openai",
            endpoint=self.base_url,
        )


class _CodexHTTPError(Exception):
    """Internal error carrying an HTTP status code and safe body excerpt."""

    def __init__(self, message: str, *, status_code: int, body: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self._body = body

    @property
    def body(self) -> str:
        return self._body


@dataclass(frozen=True)
class _ParsedCodexResponse:
    text_content: str
    model: str = ""
    usage: dict[str, int] | None = None


def _parse_codex_sse_response(body_text: str) -> _ParsedCodexResponse:
    """Parse a Codex backend SSE stream into text, model and usage.

    Accumulates ``response.output_text.delta`` events and falls back to the
    final response object's ``output_text`` when the backend skipped deltas.
    """
    text_parts: list[str] = []
    fallback_text = ""
    model = ""
    usage: dict[str, int] | None = None
    for event in _iter_sse_events(body_text):
        event_type = str(event.get("type") or "")
        if event_type == "error":
            message = _sse_error_message(event)
            raise LLMResponseError(f"openai codex stream error: {message}")
        if event_type == "response.output_text.delta":
            delta = event.get("delta")
            if isinstance(delta, str):
                text_parts.append(delta)
            continue
        if event_type in {"response.completed", "response.done", "response.incomplete"}:
            response = event.get("response")
            if isinstance(response, dict):
                model = str(response.get("model") or model)
                usage = _usage_from_response(response)
                fallback_text = _response_output_text(response) or fallback_text
    content = "".join(text_parts) or fallback_text
    return _ParsedCodexResponse(
        text_content=content,
        model=model,
        usage=usage,
    )


def _iter_sse_events(body_text: str) -> Any:
    """Yield parsed SSE ``data:`` JSON events from a response body."""
    import json as _json

    for raw_event in _split_sse_events(body_text):
        data_lines: list[str] = []
        for line in raw_event.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload:
                    data_lines.append(payload)
        for payload in data_lines:
            if payload == "[DONE]":
                return
            try:
                event = _json.loads(payload)
            except (ValueError, TypeError):
                logger.debug("Skipping unparseable codex SSE event")
                continue
            if isinstance(event, dict):
                yield event


def _split_sse_events(body_text: str) -> list[str]:
    """Split an SSE body into raw event blocks on blank-line boundaries."""
    events: list[str] = []
    current: list[str] = []
    for line in body_text.splitlines():
        if not line.strip():
            if current:
                events.append("\n".join(current))
                current = []
            continue
        current.append(line)
    if current:
        events.append("\n".join(current))
    return events


def _sse_error_message(event: dict[str, object]) -> str:
    nested = event.get("error")
    if isinstance(nested, dict):
        for key in ("message", "code"):
            value = nested.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    for key in ("message", "code"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "unknown codex stream error"


def _response_output_text(response: dict[str, object]) -> str:
    raw_text = response.get("output_text")
    if isinstance(raw_text, str) and raw_text.strip():
        return raw_text.strip()
    parts: list[str] = []
    raw_output = response.get("output")
    for item in raw_output if isinstance(raw_output, list) else []:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "") != "message":
            continue
        raw_content = item.get("content")
        for block in raw_content if isinstance(raw_content, list) else []:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
    return "".join(parts).strip()


def _usage_from_response(response: dict[str, object]) -> dict[str, int] | None:
    raw_usage = response.get("usage")
    if not isinstance(raw_usage, dict):
        return None
    try:
        input_tokens = int(raw_usage.get("input_tokens") or 0)
        output_tokens = int(raw_usage.get("output_tokens") or 0)
        total_tokens = int(raw_usage.get("total_tokens") or (input_tokens + output_tokens))
    except (TypeError, ValueError):
        return None
    usage: dict[str, int] = {
        "prompt_tokens": input_tokens,
        "completion_tokens": output_tokens,
        "total_tokens": total_tokens,
    }
    details = raw_usage.get("input_tokens_details")
    if isinstance(details, dict):
        cached = int(details.get("cached_tokens") or 0)
        if cached:
            usage["cached_input_tokens"] = cached
    return usage


def _safe_error_excerpt(body_text: str, status: int | None = None) -> str:
    """Compact, token-free excerpt of an upstream error body."""
    text = " ".join((body_text or "").split())
    # The Codex edge can echo the request as JSON. Drop anything that looks
    # like an Authorization header value before logging/returning.
    lowered = text.lower()
    text = text[:300] if "authorization" in lowered or "bearer" in lowered else text[:1000]
    return text or (f"HTTP {status}" if status else "")


def _error_body_text(exc: Exception) -> str:
    for candidate in (getattr(exc, "body", None), getattr(exc, "response", None)):
        if candidate is None:
            continue
        text = getattr(candidate, "text", None)
        if text:
            return str(text)
        if isinstance(candidate, (dict, list)):
            return json.dumps(candidate, ensure_ascii=False, sort_keys=True)
        if isinstance(candidate, str):
            return candidate
    return str(exc)


async def probe_codex_llm(
    *,
    model: str = "",
    base_url: str = "",
    token_path: str | None = None,
    timeout: float = _CODEX_PROBE_TIMEOUT_SECONDS,
) -> CodexProbeResult:
    """Run a live Codex ChatGPT capability probe and persist the outcome.

    The probe uses the same transport as ``complete()`` and writes its
    outcome back to the local Codex credential file so ``login codex
    --status`` and ``/api/health`` can distinguish "token imported" from
    "token actually callable".
    """
    from pathlib import Path

    credential_path = Path(token_path).expanduser() if token_path else None
    credentials = load_codex_credentials(token_path=credential_path)
    if credentials is None:
        return CodexProbeResult(
            ok=False,
            message="未找到 Codex OAuth 凭据，请先运行 `openbiliclaw login codex`。",
        )
    try:
        token = await get_valid_codex_token(token_path=credential_path)
    except CodexAuthError as exc:
        return CodexProbeResult(ok=False, message=str(exc))

    effective_model = (model or "").strip()
    if not effective_model:
        available = await fetch_codex_models(
            access_token=token,
            account_id=credentials.account_id,
            timeout=timeout,
        )
        effective_model = available[0] if available else _DEFAULT_CODEX_MODEL

    async def _token_provider(force_refresh: bool = False) -> str:
        return await get_valid_codex_token(
            force_refresh=force_refresh,
            token_path=credential_path,
        )

    provider = CodexChatGPTProvider(
        access_token=token,
        account_id=credentials.account_id,
        model=effective_model,
        base_url=base_url,
        token_provider=_token_provider,
        timeout=timeout,
        reasoning_effort="",
    )
    result = await provider.capability_probe(timeout=timeout)
    try:
        from openbiliclaw.llm.codex_auth import save_codex_probe_state

        latest = load_codex_credentials(token_path=credential_path)
        if latest is not None:
            save_codex_probe_state(latest, result.to_probe_state(), token_path=credential_path)
    except CodexAuthError:
        logger.debug("Unable to persist codex probe state", exc_info=True)
    return result
