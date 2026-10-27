"""Native Jev HTTP adapter with bounded deadlines and strict evidence validation."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import math
import time
from contextlib import suppress
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from openbiliclaw.decision.models import (
    DecisionAnswer,
    DecisionEvidence,
    DecisionRequest,
    DecisionStatus,
    DecisionUsage,
    parse_jev_response,
)
from openbiliclaw.network import outbound_httpx_kwargs

_ENDPOINT = "https://api.typesafe.ai/v1/systemone"


def _loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    with suppress(ValueError):
        return ipaddress.ip_address(host).is_loopback
    return False


def validate_jev_endpoint_url(value: object) -> str:
    """Validate a full native endpoint without echoing credentials or URL input."""
    try:
        if not isinstance(value, str) or any(ord(char) <= 32 or ord(char) == 127 for char in value):
            raise ValueError
        url = httpx.URL(value)
        if not url.host or url.userinfo or "?" in value or "#" in value or url.path == "/":
            raise ValueError
        loopback = _loopback_host(url.host)
        if url.scheme != "https" and not (url.scheme == "http" and loopback):
            raise ValueError
        return str(url)
    except (ValueError, httpx.InvalidURL):
        raise ValueError(
            "Jev endpoint_url 必须是完整 HTTPS 接口地址；仅回环测试允许 HTTP。"
            "不能包含账号密码、查询参数、片段或空白。"
        ) from None


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    # Engineering backoff, not a model-quality threshold. A long Retry-After
    # is bounded by the enclosing total deadline, never retried early.
    delay = 0.1 * 2.0 ** min(attempt - 1, 5)
    header = response.headers.get("retry-after", "")
    try:
        supplied = float(header)
        if math.isfinite(supplied) and supplied >= 0:
            return max(delay, supplied)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(header)
            if retry_at.tzinfo is not None:
                return float(max(delay, (retry_at - datetime.now(UTC)).total_seconds()))
        except (ValueError, TypeError, OverflowError):
            pass
    return delay


class JevDecisionProvider:
    """Evaluate native typed questions without SDK or application-policy coupling.

    An injected client is borrowed and is never closed here. Otherwise this
    adapter owns an outbound-network-policy-aware client. Every request disables
    redirects and client auth overrides so Bearer credentials stay on the
    explicitly configured native endpoint. Only loopback may use plain HTTP.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "jev-1.13.0",
        timeout_seconds: float = 2.0,
        max_attempts: int = 2,
        client: httpx.AsyncClient | None = None,
        endpoint_url: str = _ENDPOINT,
    ) -> None:
        if (
            not isinstance(api_key, str)
            or not api_key
            or not api_key.isascii()
            or any(char.isspace() or ord(char) < 33 or ord(char) == 127 for char in api_key)
        ):
            raise ValueError("a nonempty valid Jev API key is required")
        if not isinstance(model, str) or not model or model != model.strip():
            raise ValueError("a nonempty Jev model is required")
        if (
            type(timeout_seconds) not in (int, float)
            or not math.isfinite(timeout_seconds)
            or (timeout_seconds <= 0)
        ):
            raise ValueError("timeout_seconds must be finite and positive")
        if type(max_attempts) is not int or max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max_attempts
        self._endpoint_url = validate_jev_endpoint_url(endpoint_url)
        self._owns_client = client is None
        network = (
            {"trust_env": False}
            if _loopback_host(httpx.URL(self._endpoint_url).host)
            else outbound_httpx_kwargs()
        )
        self._client = client or httpx.AsyncClient(
            timeout=timeout_seconds, follow_redirects=False, **network
        )
        self._closed = False

    async def aclose(self) -> None:
        """Close this adapter and its owned client, leaving borrowed clients open."""
        self._closed = True
        if self._owns_client:
            await self._client.aclose()

    async def evaluate(self, request: DecisionRequest) -> DecisionEvidence:
        """Return validated evidence, or a sanitized technical failure result."""
        started = time.monotonic()
        attempts = 0
        http_status: int | None = None
        # Snapshot before the first await, so mutations while HTTP is in flight
        # cannot change the whitelist, attribution or digest being validated.
        try:
            snapshot = DecisionRequest.model_validate(
                request.model_dump(mode="python", warnings="error")
            )
        except (ValueError, TypeError, RecursionError):
            raise ValueError("invalid decision request") from None
        digest = snapshot.input_digest

        def evidence(
            status: DecisionStatus,
            reason_code: str,
            *,
            actual_model: str | None = None,
            answers: dict[str, DecisionAnswer] | None = None,
            usage: DecisionUsage | None = None,
        ) -> DecisionEvidence:
            return DecisionEvidence(
                status=status,
                reason_code=reason_code,
                request_id=snapshot.request_id,
                purpose=snapshot.purpose,
                question_schema_version=snapshot.question_schema_version,
                input_digest=digest,
                requested_model=self._model,
                actual_model=actual_model,
                answers=answers or {},
                usage=usage,
                latency_ms=(time.monotonic() - started) * 1000,
                attempts=attempts,
                http_status=http_status,
            )

        if self._closed or self._client.is_closed:
            return evidence("UNAVAILABLE", "provider_closed")
        deadline = min(
            started + self._timeout_seconds,
            snapshot.deadline_monotonic if snapshot.deadline_monotonic is not None else math.inf,
        )
        if deadline <= time.monotonic():
            return evidence("UNAVAILABLE", "deadline_exceeded")
        payload = {
            "model": self._model,
            "state": snapshot.state,
            "questions": {
                key: question.model_dump(mode="json", exclude_none=True)
                for key, question in snapshot.questions.items()
            },
        }
        # Canonical wire order matches the digest's map-order invariance. This
        # also avoids treating differently ordered Choice options as one input.
        body = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        try:
            async with asyncio.timeout(deadline - time.monotonic()):
                while attempts < self._max_attempts:
                    attempts += 1
                    response = await self._client.request(
                        "POST",
                        self._endpoint_url,
                        content=body,
                        headers={
                            "Authorization": f"Bearer {self._api_key}",
                            "Content-Type": "application/json",
                        },
                        auth=None,
                        follow_redirects=False,
                        timeout=max(0.001, deadline - time.monotonic()),
                    )
                    http_status = response.status_code
                    if 200 <= http_status < 300:
                        try:
                            parsed = json.loads(response.content, object_pairs_hook=_unique_object)
                            model, answers, usage = parse_jev_response(parsed, snapshot)
                        except (ValueError, TypeError, UnicodeError, RecursionError):
                            return evidence("INVALID", "invalid_response")
                        if (
                            self._model not in ("jev-latest", "jev-preview")
                            and model != self._model
                        ):
                            return evidence(
                                "INVALID", "model_mismatch", actual_model=model, usage=usage
                            )
                        if time.monotonic() >= deadline:
                            return evidence("UNAVAILABLE", "deadline_exceeded")
                        return evidence(
                            "OK", "ok", actual_model=model, answers=answers, usage=usage
                        )
                    retryable = http_status in (429, 529) or 500 <= http_status <= 599
                    if not retryable or attempts >= self._max_attempts:
                        reason = self._http_reason(http_status)
                        return evidence("UNAVAILABLE", reason)
                    await asyncio.sleep(_retry_delay(response, attempts))
        except (TimeoutError, httpx.TimeoutException):
            return evidence("UNAVAILABLE", "deadline_exceeded")
        except httpx.RequestError:
            return evidence("UNAVAILABLE", "transport_error")
        # All loop branches return or retry; retained for static exhaustiveness.
        return evidence("UNAVAILABLE", "server_error")

    @staticmethod
    def _http_reason(status: int) -> str:
        if status in (401, 403):
            return "authentication_failed"
        if status == 429:
            return "rate_limited"
        if status == 529:
            return "overloaded"
        if 300 <= status < 400:
            return "redirect_blocked"
        if status >= 500:
            return "server_error"
        return "http_error"
