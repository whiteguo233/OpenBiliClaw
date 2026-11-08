"""HTTPS relay transport reusing the complete sponsored task/tool/embedding layer."""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any

import httpx

from openbiliclaw.runtime.relay_release import validate_relay_url

from .base import LLMResponse, LLMStreamChunk
from .relay_auth import load_device_key, signed_headers, wire_json
from .sponsored_errors import SponsoredProtocolError, SponsoredUnavailableError, error_from_payload
from .sponsored_provider import SponsoredProvider, _coerce_usage

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Mapping
    from pathlib import Path

    from .sponsored_contracts import SponsoredTaskContract


class SponsoredRelayProvider(SponsoredProvider):
    """Each installation has its own identity; the SiliconFlow key stays remote."""

    def __init__(self, url: str, device_path: Path, **kwargs: Any) -> None:
        self.url = validate_relay_url(url)
        super().__init__(["relay", self.url], **kwargs)
        self._device = load_device_key(device_path)
        self._http = httpx.AsyncClient(
            timeout=self._request_timeout, follow_redirects=False, trust_env=False
        )
        self._token = ""
        self._expires = 0.0
        self._session_lock = asyncio.Lock()
        self._parallel = asyncio.Semaphore(3)
        self._streaming = True

    @property
    def runtime_running(self) -> bool:
        return bool(self._token) and not self._http.is_closed

    async def _ensure_started(self) -> None:
        async with self._session_lock:
            if self._expires > time.monotonic() + 60:
                return
            body = b"{}"
            try:
                response = await self._http.post(
                    self.url + "/v1/session",
                    content=body,
                    headers=signed_headers(self._device, "/v1/session", body),
                )
                if response.status_code != 200:
                    self._reject(response)
                data = response.json()
                token = data.get("token")
                if (
                    not isinstance(token, str)
                    or not 32 <= len(token) <= 128
                    or data.get("expires_in") != 300
                ):
                    raise SponsoredProtocolError("invalid relay session")
                self._token = token
                self._expires = time.monotonic() + 300
                self._policy_version = 1
            except (httpx.HTTPError, ValueError) as exc:
                raise SponsoredUnavailableError("relay session unavailable") from exc

    @staticmethod
    def _reject(response: httpx.Response) -> None:
        codes = {401: "AUTH_FAILED", 429: "RATE_LIMITED"}
        raise error_from_payload({"code": codes.get(response.status_code, "UPSTREAM_ERROR")})

    async def _frames(
        self, contract: SponsoredTaskContract, payload: Mapping[str, Any], *, stream: bool
    ) -> AsyncGenerator[dict[str, Any], None]:
        self._serialize_payload(payload, contract, None)
        body = wire_json(
            {
                "task_id": contract.task_id,
                "contract_version": contract.contract_version,
                "input": dict(payload),
                "stream": stream,
            }
        )
        async with self._parallel, self._observe_task(contract):
            await self._ensure_started()
            headers = signed_headers(self._device, "/v1/task", body, self._token)
            try:
                async with (
                    asyncio.timeout(self._operation_timeout(contract.operation)),
                    self._http.stream(
                        "POST",
                        self.url + "/v1/task",
                        content=body,
                        headers=headers,
                        timeout=self._operation_timeout(contract.operation),
                    ) as response,
                ):
                    if response.status_code != 200:
                        # Bounded error parsing, no upstream body in messages/logs.
                        raw = bytearray()
                        async for chunk in response.aiter_bytes():
                            raw.extend(chunk)
                            if len(raw) > 4096:
                                self._reject(response)
                        try:
                            error = json.loads(raw).get("error", {})
                        except (ValueError, AttributeError):
                            error = {}
                        if isinstance(error, dict) and isinstance(error.get("code"), str):
                            raise error_from_payload({"code": error["code"]})
                        self._reject(response)
                    pending = bytearray()
                    terminal = False
                    async for chunk in response.aiter_bytes():
                        pending.extend(chunk)
                        if len(pending) > 16 * 1024 * 1024:
                            raise SponsoredProtocolError("relay response too large")
                        while b"\n" in pending:
                            line, _, pending = pending.partition(b"\n")
                            frame = json.loads(line)
                            if (
                                terminal
                                or not isinstance(frame, dict)
                                or frame.get("type") not in {"delta", "result"}
                            ):
                                raise SponsoredProtocolError("invalid relay frame")
                            if frame["type"] == "result":
                                terminal = True
                                if frame.get("ok") is not True:
                                    error = frame.get("error", {})
                                    raise error_from_payload(
                                        {
                                            "code": error.get("code", "UPSTREAM_ERROR")
                                            if isinstance(error, dict)
                                            else "UPSTREAM_ERROR"
                                        }
                                    )
                            yield frame
                    if pending or not terminal:
                        raise SponsoredProtocolError("relay response ended before terminal")
            except (httpx.HTTPError, TimeoutError) as exc:
                raise SponsoredUnavailableError("relay connection interrupted") from exc
            except (ValueError, TypeError) as exc:
                raise SponsoredProtocolError("invalid relay response") from exc

    @staticmethod
    def _response(frame: dict[str, Any], contract: SponsoredTaskContract) -> LLMResponse:
        return LLMResponse(
            content=str(frame.get("content") or ""),
            model=str(frame.get("model") or contract.model),
            instance_id="sponsored",
            provider="sponsored",
            usage=_coerce_usage(frame.get("usage")),
            raw=frame,
        )

    async def _execute_task_once(
        self,
        *,
        caller: str = "",
        task_id: str = "",
        contract_version: int | None = None,
        user_payload: Mapping[str, Any],
        user_text: str | None = None,
        system_prompt: str | None = None,
    ) -> LLMResponse:
        from .sponsored_errors import SponsoredContractMismatchError

        contract = self._resolve_contract(
            caller=caller, task_id=task_id, contract_version=contract_version
        )
        if system_prompt is not None and system_prompt != contract.system_prompt:
            raise SponsoredContractMismatchError("system prompt does not match contract")
        self._serialize_payload(user_payload, contract, user_text)
        result = None
        async for frame in self._frames(contract, user_payload, stream=False):
            if frame["type"] != "result":
                raise SponsoredProtocolError("unexpected delta")
            result = self._response(frame, contract)
        if result is None:
            raise SponsoredProtocolError("missing relay result")
        return result

    async def _stream_task_once(
        self, contract: SponsoredTaskContract, payload: Mapping[str, Any]
    ) -> AsyncGenerator[LLMStreamChunk, None]:
        frames = self._frames(contract, payload, stream=True)
        try:
            async for frame in frames:
                if frame["type"] == "delta":
                    if not isinstance(frame.get("content"), str):
                        raise SponsoredProtocolError("invalid relay delta")
                    yield LLMStreamChunk(delta=frame["content"])
                else:
                    yield LLMStreamChunk(response=self._response(frame, contract))
        finally:
            await frames.aclose()

    async def aclose(self) -> None:
        self._token = ""
        self._expires = 0
        await self._http.aclose()
