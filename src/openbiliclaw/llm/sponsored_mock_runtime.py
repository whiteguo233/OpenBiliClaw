"""Deterministic fake Sponsored Runtime used by tests and community builds.

It implements the same v1 IPC protocol and performs the same local contract
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
from collections.abc import Mapping
from typing import Any

from .sponsored_protocol import PROTOCOL_VERSION, read_frame, write_frame
from .sponsored_tasks import DEFAULT_SPONSORED_REGISTRY

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

    messages = frame.get("messages")
    if not isinstance(messages, list) or len(messages) != 2:
        raise ValueError("CONTRACT_MISMATCH")
    system_message, user_message = messages
    if not isinstance(system_message, Mapping) or not isinstance(user_message, Mapping):
        raise ValueError("CONTRACT_MISMATCH")
    if system_message.get("role") != "system" or user_message.get("role") != "user":
        raise ValueError("CONTRACT_MISMATCH")
    if system_message.get("content") != contract.system_prompt:
        raise ValueError("CONTRACT_MISMATCH")

    user_text = user_message.get("content")
    if not isinstance(user_text, str):
        raise ValueError("CONTRACT_MISMATCH")
    if len(user_text.encode("utf-8")) > contract.max_input_bytes:
        raise ValueError("REQUEST_TOO_LARGE")
    if contract.request_schema.get("type") == "string":
        if not user_text.strip():
            raise ValueError("CONTRACT_MISMATCH")
        return contract.task_id, user_text, contract.model
    try:
        payload = json.loads(user_text)
    except json.JSONDecodeError as exc:
        raise ValueError("CONTRACT_MISMATCH") from exc
    if not isinstance(payload, dict):
        raise ValueError("CONTRACT_MISMATCH")
    required = contract.request_schema.get("required")
    if isinstance(required, list) and any(key not in payload for key in required):
        raise ValueError("CONTRACT_MISMATCH")
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
        if message_type != "execute":
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
        _respond(
            request_id,
            {
                "ok": True,
                "content": content,
                "model": model,
                "usage": {"input_tokens": 100, "output_tokens": 20},
                "error": None,
            },
        )


if __name__ == "__main__":
    raise SystemExit(main())
