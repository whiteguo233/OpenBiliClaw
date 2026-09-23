"""IPC client for the closed-source ``obc-sponsored-runtime`` child process.

The provider speaks a deliberately narrow protocol: spawn the runtime, say
hello, execute one contract-validated task at a time, and map runtime error
codes to typed exceptions. It never sees the sponsored API key and never
opens a TCP port of its own.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import uuid
from collections.abc import Mapping, Sequence
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from openbiliclaw import __version__
from openbiliclaw.proc import no_window_kwargs

from .base import LLMResponse
from .sponsored_errors import (
    SponsoredContractMismatchError,
    SponsoredError,
    SponsoredProtocolError,
    SponsoredRequestTooLargeError,
    SponsoredUnavailableError,
    SponsoredUnsupportedTaskError,
    error_from_payload,
)
from .sponsored_protocol import (
    PROTOCOL_VERSION,
    encode_frame,
    read_frame_async,
    write_frame_async,
)
from .sponsored_tasks import DEFAULT_SPONSORED_REGISTRY

if TYPE_CHECKING:
    from .sponsored_contracts import SponsoredContractRegistry, SponsoredTaskContract

logger = logging.getLogger(__name__)

DEFAULT_REQUEST_TIMEOUT_SECONDS = 180.0
DEFAULT_HANDSHAKE_TIMEOUT_SECONDS = 10.0
_STDERR_TAIL_BYTES = 4096

SPONSORED_ENABLED_ENV = "OPENBILICLAW_SPONSORED_ENABLED"
SPONSORED_RUNTIME_ENV = "OPENBILICLAW_SPONSORED_RUNTIME"
_TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "on"})


class SponsoredProvider:
    """Execute Sponsored tasks through a policy-validating local runtime.

    One provider instance owns one long-lived child process. Requests are
    serialized per process; callers that need concurrency should run several
    provider instances, each with its own child.
    """

    def __init__(
        self,
        runtime_command: Sequence[str],
        *,
        registry: SponsoredContractRegistry = DEFAULT_SPONSORED_REGISTRY,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        handshake_timeout: float = DEFAULT_HANDSHAKE_TIMEOUT_SECONDS,
        env: Mapping[str, str] | None = None,
    ) -> None:
        if not runtime_command:
            raise ValueError("runtime_command must not be empty")
        if request_timeout <= 0 or handshake_timeout <= 0:
            raise ValueError("timeouts must be positive")
        self._runtime_command = tuple(str(part) for part in runtime_command)
        self._registry = registry
        self._request_timeout = float(request_timeout)
        self._handshake_timeout = float(handshake_timeout)
        self._extra_env = dict(env) if env is not None else None
        self._process: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr_tail = bytearray()
        self._start_lock = asyncio.Lock()
        self._request_lock = asyncio.Lock()
        self._policy_version = 0

    @property
    def policy_version(self) -> int:
        return self._policy_version

    @property
    def runtime_running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def execute_task(
        self,
        *,
        caller: str = "",
        task_id: str = "",
        contract_version: int | None = None,
        user_payload: Mapping[str, Any],
        user_text: str | None = None,
        system_prompt: str | None = None,
    ) -> LLMResponse:
        """Run one Sponsored task and return the standardized response.

        Object-schema contracts serialize ``user_payload`` to canonical JSON.
        String-schema contracts (the pilot) require ``user_text`` so the caller
        can send the unchanged legacy prompt message. ``system_prompt`` is
        optional and exists for tests and drift checks; production callers must
        omit it so the contract's canonical prompt is the only prompt the
        runtime ever sees.
        """

        contract = self._resolve_contract(
            caller=caller,
            task_id=task_id,
            contract_version=contract_version,
        )
        if system_prompt is not None and system_prompt != contract.system_prompt:
            raise SponsoredContractMismatchError(
                "system prompt does not match the sponsored contract",
                task_id=contract.task_id,
            )
        payload_text = self._serialize_payload(user_payload, contract, user_text)

        async with self._request_lock:
            await self._ensure_started()
            process = self._process
            if process is None or process.stdin is None or process.stdout is None:
                raise SponsoredUnavailableError("runtime pipes are unavailable")

            request_id = uuid.uuid4().hex
            request = {
                "type": "execute",
                "id": request_id,
                "task_id": contract.task_id,
                "contract_version": contract.contract_version,
                "messages": [
                    {"role": "system", "content": contract.system_prompt},
                    {"role": "user", "content": payload_text},
                ],
                "requested": {"max_tokens": contract.max_output_tokens},
            }

            try:
                await write_frame_async(process.stdin, request)
            except (SponsoredError, OSError) as exc:
                await self._stop_process()
                raise SponsoredUnavailableError(
                    "failed to write to sponsored runtime",
                    task_id=contract.task_id,
                ) from exc

            try:
                response = await asyncio.wait_for(
                    read_frame_async(process.stdout),
                    self._request_timeout,
                )
            except TimeoutError as exc:
                self._cancel_in_flight(process, request_id)
                await self._stop_process()
                raise SponsoredUnavailableError(
                    "sponsored runtime timed out",
                    task_id=contract.task_id,
                ) from exc
            except SponsoredError as exc:
                await self._stop_process()
                raise SponsoredUnavailableError(
                    "sponsored runtime IPC failed",
                    task_id=contract.task_id,
                ) from exc
            except asyncio.CancelledError:
                self._abort_process_sync(process)
                raise

            if response.get("id") != request_id:
                await self._stop_process()
                raise SponsoredProtocolError(
                    "sponsored runtime returned a mismatched response id",
                    task_id=contract.task_id,
                )
            if response.get("ok") is not True:
                error_payload = response.get("error")
                if not isinstance(error_payload, Mapping):
                    error_payload = {}
                raise error_from_payload(error_payload, task_id=contract.task_id)

            return LLMResponse(
                content=str(response.get("content") or ""),
                model=str(response.get("model") or contract.model),
                instance_id="sponsored",
                provider="sponsored",
                usage=_coerce_usage(response.get("usage")),
                raw=response,
            )

    async def aclose(self) -> None:
        """Terminate the runtime child and release IPC resources."""

        await self._stop_process()

    async def __aenter__(self) -> SponsoredProvider:
        await self._ensure_started()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    def _resolve_contract(
        self,
        *,
        caller: str,
        task_id: str,
        contract_version: int | None,
    ) -> SponsoredTaskContract:
        by_task = self._registry.get(task_id) if task_id else None
        by_caller = self._registry.resolve_caller(caller) if caller else None
        if by_task is None and by_caller is None:
            raise SponsoredUnsupportedTaskError(
                "task is not present in the sponsored policy",
                task_id=task_id or caller,
            )
        if by_task is not None and by_caller is not None and by_task is not by_caller:
            raise SponsoredContractMismatchError(
                "caller and task_id resolve to different contracts",
                task_id=task_id,
            )
        contract = by_task or by_caller
        assert contract is not None
        if contract_version is not None and contract_version != contract.contract_version:
            raise SponsoredUnsupportedTaskError(
                f"contract version {contract_version} is not supported",
                task_id=contract.task_id,
            )
        return contract

    @staticmethod
    def _serialize_payload(
        payload: Mapping[str, Any],
        contract: SponsoredTaskContract,
        user_text: str | None = None,
    ) -> str:
        schema_type = contract.request_schema.get("type")
        if schema_type == "string":
            if not isinstance(user_text, str) or not user_text.strip():
                raise SponsoredContractMismatchError(
                    "string-schema sponsored task requires a non-empty user_text",
                    task_id=contract.task_id,
                )
            text = user_text
        else:
            if not isinstance(payload, Mapping):
                raise SponsoredContractMismatchError(
                    "sponsored user payload must be a JSON object",
                    task_id=contract.task_id,
                )
            try:
                text = json.dumps(
                    dict(payload),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            except (TypeError, ValueError) as exc:
                raise SponsoredContractMismatchError(
                    f"sponsored user payload is not JSON-serializable: {exc}",
                    task_id=contract.task_id,
                ) from exc
        if len(text.encode("utf-8")) > contract.max_input_bytes:
            raise SponsoredRequestTooLargeError(
                "sponsored user payload exceeds the contract input budget",
                task_id=contract.task_id,
            )
        return text

    async def _ensure_started(self) -> None:
        async with self._start_lock:
            if self.runtime_running:
                return
            await self._stop_process()
            try:
                process = await asyncio.create_subprocess_exec(
                    *self._runtime_command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=self._process_env(),
                    **no_window_kwargs(),
                )
            except (OSError, ValueError) as exc:
                raise SponsoredUnavailableError("sponsored runtime is not available") from exc
            self._process = process
            if process.stderr is not None:
                self._stderr_task = asyncio.create_task(self._drain_stderr(process.stderr))
            try:
                await self._handshake(process)
            except SponsoredUnavailableError:
                await self._stop_process()
                raise
            except SponsoredError as exc:
                await self._stop_process()
                raise SponsoredUnavailableError("sponsored runtime handshake failed") from exc
            except TimeoutError as exc:
                await self._stop_process()
                raise SponsoredUnavailableError("sponsored runtime handshake timed out") from exc

    async def _handshake(self, process: asyncio.subprocess.Process) -> None:
        if process.stdin is None or process.stdout is None:
            raise SponsoredUnavailableError("runtime pipes are unavailable")
        await write_frame_async(
            process.stdin,
            {
                "type": "hello",
                "protocol": PROTOCOL_VERSION,
                "core": __version__,
            },
        )
        hello = await asyncio.wait_for(
            read_frame_async(process.stdout),
            self._handshake_timeout,
        )
        if (
            hello.get("type") != "hello"
            or hello.get("protocol") != PROTOCOL_VERSION
            or hello.get("ok") is not True
        ):
            raise SponsoredUnavailableError(
                "runtime handshake was rejected (protocol/policy mismatch)"
            )
        try:
            self._policy_version = int(hello.get("policy_version") or 0)
        except (TypeError, ValueError):
            self._policy_version = 0

    async def _stop_process(self) -> None:
        process, self._process = self._process, None
        stderr_task, self._stderr_task = self._stderr_task, None
        if process is not None:
            if process.stdin is not None:
                with suppress(Exception):
                    process.stdin.close()
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 2.0)
                except (TimeoutError, ProcessLookupError):
                    with suppress(ProcessLookupError):
                        process.kill()
                    with suppress(Exception):
                        await process.wait()
        if stderr_task is not None:
            stderr_task.cancel()
            with suppress(asyncio.CancelledError):
                await stderr_task

    async def _drain_stderr(self, stream: asyncio.StreamReader) -> None:
        try:
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    return
                self._stderr_tail.extend(chunk)
                if len(self._stderr_tail) > _STDERR_TAIL_BYTES:
                    del self._stderr_tail[: len(self._stderr_tail) - _STDERR_TAIL_BYTES]
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive pipe cleanup
            logger.debug("sponsored runtime stderr drain stopped", exc_info=True)

    def _cancel_in_flight(
        self,
        process: asyncio.subprocess.Process,
        request_id: str,
    ) -> None:
        if process.stdin is None or process.stdin.is_closing():
            return
        with suppress(Exception):
            process.stdin.write(encode_frame({"type": "cancel", "id": request_id}))

    @staticmethod
    def _abort_process_sync(process: asyncio.subprocess.Process) -> None:
        with suppress(ProcessLookupError):
            process.kill()

    def _process_env(self) -> dict[str, str] | None:
        if self._extra_env is None:
            return None
        env = {key: str(value) for key, value in os.environ.items()}
        env.update({key: str(value) for key, value in self._extra_env.items()})
        return env


_USAGE_KEY_ALIASES = {
    "input_tokens": "prompt_tokens",
    "output_tokens": "completion_tokens",
}


def _coerce_usage(raw: Any) -> dict[str, int] | None:
    """Normalize runtime usage keys to the project's provider convention."""

    if not isinstance(raw, Mapping):
        return None
    usage: dict[str, int] = {}
    for key, value in raw.items():
        normalized_key = _USAGE_KEY_ALIASES.get(str(key), str(key))
        try:
            usage[normalized_key] = int(value)
        except (TypeError, ValueError):
            continue
    if (
        "prompt_tokens" in usage
        and "completion_tokens" in usage
        and "total_tokens" not in usage
    ):
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
    return usage or None


def build_sponsored_provider_from_env(
    env: Mapping[str, str] | None = None,
) -> SponsoredProvider | None:
    """Build a provider from environment variables, default-off.

    This is the bootstrap seam until ``[llm.sponsored]`` config lands. Both
    variables are required:

    - ``OPENBILICLAW_SPONSORED_ENABLED``: ``1`` / ``true`` / ``yes`` / ``on``;
    - ``OPENBILICLAW_SPONSORED_RUNTIME``: command line for the runtime binary
      (or the Python mock runtime during development).
    """

    source = env if env is not None else os.environ
    enabled = str(source.get(SPONSORED_ENABLED_ENV) or "").strip().lower()
    if enabled not in _TRUTHY_ENV_VALUES:
        return None
    command_text = str(source.get(SPONSORED_RUNTIME_ENV) or "").strip()
    if not command_text:
        return None
    try:
        command = shlex.split(command_text)
    except ValueError:
        logger.warning("invalid %s command line", SPONSORED_RUNTIME_ENV)
        return None
    if not command:
        return None
    try:
        return SponsoredProvider(command)
    except ValueError:
        logger.warning("could not build sponsored provider from environment", exc_info=True)
        return None
