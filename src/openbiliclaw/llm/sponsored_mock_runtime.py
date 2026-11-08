"""Deterministic fake Sponsored Runtime used by tests and community builds.

It implements the same v2 IPC protocol and performs the same local contract
checks as the closed-source runtime, but has no network client and no API key.
Run it as::

    python -m openbiliclaw.llm.sponsored_mock_runtime

Environment knobs (tests only):

- ``SPONSORED_MOCK_ERROR_CODE``: force an error code for every execute;
- ``SPONSORED_MOCK_DELAY_SECONDS``: sleep before responding;
- ``SPONSORED_MOCK_RESPONSE``: override the JSON content string;
- ``SPONSORED_MOCK_POLICY_VERSION``: advertised policy version.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
from typing import Any

from .sponsored_protocol import PROTOCOL_VERSION, read_frame, write_frame
from .sponsored_tasks import DEFAULT_SPONSORED_REGISTRY

if "--approved" in sys.argv:
    from .sponsored_profiles import approved_registry

    DEFAULT_SPONSORED_REGISTRY = approved_registry()

MOCK_RUNTIME_VERSION = "mock-0.1.0"
_DEFAULT_RESPONSE = '{"likes": [], "dislikes": []}'


def _respond(request_id: str, payload: dict[str, Any]) -> None:
    write_frame(sys.stdout.buffer, {"type": "result", "id": request_id, **payload})


def _respond_error(request_id: str, code: str, message: str) -> None:
    _respond(
        request_id,
        {"ok": False, "error": {"code": code, "message": message}},
    )


def _forced_error_code() -> str:
    return str(os.environ.get("SPONSORED_MOCK_ERROR_CODE") or "").strip().upper()


def _mock_delay_seconds() -> float:
    raw = str(os.environ.get("SPONSORED_MOCK_DELAY_SECONDS") or "").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 0.0


def _validate_execute(frame: Mapping[str, Any]) -> tuple[str, str, str]:
    """Return ``(task_id, user_text, model)`` or raise ``ValueError(code)``."""

    task_id = str(frame.get("task_id") or "")
    contract_version = frame.get("contract_version")
    contract = DEFAULT_SPONSORED_REGISTRY.get(task_id)
    if contract is None:
        raise ValueError("UNSUPPORTED_TASK")
    if contract_version != contract.contract_version:
        raise ValueError("UNSUPPORTED_TASK")

    from .consolidation_prompt import render_consolidation_user
    from .sponsored_tasks import validate_task_input

    fields = {"type", "id", "task_id", "contract_version", "input"}
    if "--request-timeouts" in sys.argv and "timeout_ms" in frame:
        fields.add("timeout_ms")
        milliseconds = frame["timeout_ms"]
        if type(milliseconds) is not int or not 1 <= milliseconds <= 3_600_000:
            raise ValueError("CONTRACT_MISMATCH")
    if set(frame) != fields:
        raise ValueError("CONTRACT_MISMATCH")
    payload = frame.get("input")
    if not isinstance(payload, dict):
        raise ValueError("CONTRACT_MISMATCH")
    try:
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > contract.max_input_bytes:
            raise ValueError("REQUEST_TOO_LARGE")
        if contract.operation == "consolidation":
            validate_task_input(payload)
            user_text = render_consolidation_user(**payload)
        else:
            from .sponsored_operations import validate_operation_input

            validate_operation_input(
                payload, contract.operation, max_tokens=contract.max_output_tokens
            )
            user_text = encoded
    except (TypeError, KeyError, ValueError) as exc:
        if str(exc) == "REQUEST_TOO_LARGE":
            raise
        raise ValueError("CONTRACT_MISMATCH") from exc
    return contract.task_id, user_text, contract.model


def main() -> int:
    stdin = sys.stdin.buffer
    try:
        hello = read_frame(stdin)
    except (EOFError, OSError):
        return 2

    if hello.get("type") != "hello" or hello.get("protocol") != PROTOCOL_VERSION:
        write_frame(
            sys.stdout.buffer,
            {
                "type": "hello",
                "protocol": PROTOCOL_VERSION,
                "ok": False,
                "runtime": MOCK_RUNTIME_VERSION,
            },
        )
        return 2

    try:
        policy_version = int(str(os.environ.get("SPONSORED_MOCK_POLICY_VERSION") or "1"))
    except ValueError:
        policy_version = 1
    write_frame(
        sys.stdout.buffer,
        {
            "type": "hello",
            "protocol": PROTOCOL_VERSION,
            "ok": True,
            "runtime": MOCK_RUNTIME_VERSION,
            "core": str(hello.get("core") or ""),
            "policy_version": policy_version,
            "streaming": "--stream" in sys.argv,
            "request_timeouts": "--request-timeouts" in sys.argv,
            "contracts": {c.policy_key: c.system_prompt_sha256 for c in DEFAULT_SPONSORED_REGISTRY},
        },
    )

    while True:
        try:
            frame = read_frame(stdin)
        except EOFError:
            return 0
        except OSError:
            return 2
        except Exception:
            return 2

        message_type = frame.get("type")
        request_id = str(frame.get("id") or "")
        if message_type == "cancel":
            continue
        if message_type not in {"execute", "execute_stream"}:
            _respond_error(request_id, "PROTOCOL_ERROR", "unsupported message type")
            continue

        delay = _mock_delay_seconds()
        if delay:
            time.sleep(delay)

        forced = _forced_error_code()
        if forced:
            _respond_error(request_id, forced, f"mock forced {forced}")
            continue

        try:
            _task_id, _user_text, model = _validate_execute(frame)
        except ValueError as exc:
            _respond_error(
                request_id,
                str(exc) or "CONTRACT_MISMATCH",
                "mock contract check failed",
            )
            continue

        content = str(os.environ.get("SPONSORED_MOCK_RESPONSE") or _DEFAULT_RESPONSE)
        if model == os.environ.get("SPONSORED_MOCK_FAIL_MODEL"):
            _respond_error(request_id, "UNAVAILABLE", "synthetic model outage")
            continue
        if message_type == "execute_stream":
            for part in (content[: len(content) // 2], content[len(content) // 2 :]):
                write_frame(sys.stdout.buffer, {"type": "delta", "id": request_id, "content": part})
                if os.environ.get("SPONSORED_MOCK_STREAM_ERROR_AFTER_DELTA"):
                    _respond_error(request_id, "UNAVAILABLE", "synthetic stream disconnect")
                    break
            else:
                pass
            if os.environ.get("SPONSORED_MOCK_STREAM_ERROR_AFTER_DELTA"):
                continue
        _respond(
            request_id,
            {
                "ok": True,
                "content": content,
                "model": model,
                "embedding": ([1.0] + [0.0] * 1023)
                if _task_id in {"llm.embedding_text", "llm.embedding_image"}
                else None,
                "usage": {"input_tokens": 100, "output_tokens": 20},
                "error": None,
            },
        )


if __name__ == "__main__":
    raise SystemExit(main())
