"""Temporary Codex CLI transport for isolated director evaluation only.

No production route, user config, auth import, candidate lookup or model tools.
Saved CLI authentication is handled by Codex itself. CLI overhead and token
accounting differ from the production HTTP adapter; do not pool their latency.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import signal
import tempfile
from pathlib import Path
from typing import Any

from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.llm.service import LLMService

_DISABLED_FEATURES = (
    "shell_tool",
    "unified_exec",
    "shell_snapshot",
    "apps",
    "plugins",
    "remote_plugin",
    "hooks",
    "multi_agent",
    "multi_agent_v2",
    "browser_use",
    "browser_use_external",
    "computer_use",
    "in_app_browser",
    "image_generation",
    "skill_search",
    "memories",
    "code_mode",
    "code_mode_host",
    "workspace_dependencies",
    "tool_suggest",
    "goals",
)
_WRAPPER = (
    "This is a self-contained recommendation evaluation. "
    "Use only the supplied data. Do not use tools, files, browsing or external knowledge. "
    "Return only the requested final JSON, without commentary or reasoning transcripts."
)
_ERROR_MARKERS = (
    "not supported",
    "not available",
    "does not exist",
    "model_not_found",
    "rate limit",
    "429",
    "usage limit",
    "quota",
    "unauthorized",
    "401",
    "refresh token",
    "expired",
    "connection",
    "stream disconnected",
    "error parsing",
    "invalid value",
    "unknown variant",
    "unknown field",
    "sandbox",
    "permission denied",
    "no such file",
    "account deactivated",
)
_EVENT_TYPES = frozenset(
    {
        "thread.started",
        "turn.started",
        "item.started",
        "item.updated",
        "item.completed",
        "turn.completed",
        "turn.failed",
        "error",
    }
)
# Diagnostic output bounds only; not provider throughput or quality thresholds.
_MAX_DIAGNOSTIC_EVENT_COUNT = 10_000
# Locally observed CLI progress text, not an OpenAI wire-protocol guarantee.
# Accept only this bounded notice, and only with independently verified success
# later in the same turn. Unrecognized error variants still fail closed.
_RECONNECT_NOTICE = re.compile(r"Reconnecting\.\.\. ([1-9][0-9]?)/([1-9][0-9]?)(?:[ :;(][^\r\n]*)?")
_HTTPS_FALLBACK_NOTICE = "Falling back from WebSockets to HTTPS transport."


def _reconnect_progress(event: dict[str, Any]) -> tuple[int, int] | None:
    message = event.get("message")
    if event.get("type") != "error" or not isinstance(message, str) or len(message) > 4096:
        return None
    match = _RECONNECT_NOTICE.fullmatch(message)
    if match is None:
        return None
    attempt, limit = (int(value) for value in match.groups())
    # Engineering recognition bound, not a change to the CLI retry budget.
    return (attempt, limit) if attempt <= limit <= 10 else None


def _is_https_fallback(event: dict[str, Any]) -> bool:
    message = event.get("message")
    return (
        event.get("type") == "error"
        and isinstance(message, str)
        and len(message) <= 4096
        and "\n" not in message
        and "\r" not in message
        and (message == _HTTPS_FALLBACK_NOTICE or message.startswith(_HTTPS_FALLBACK_NOTICE + " "))
    )


class CodexCLIError(RuntimeError):
    """Fixed safe error code; never include stderr or a CLI exception body."""


def _error_payload_text(value: object) -> str:
    """Inspect only error fields, never candidate, prompt, or final-answer text."""
    if isinstance(value, str):
        return value
    if type(value) is int:
        return str(value)
    if isinstance(value, dict):
        return " ".join(
            _error_payload_text(value[key])
            for key in ("message", "error", "code", "status", "status_code")
            if key in value
        )
    return ""


def _safe_diagnostics(stdout: bytes, stderr: bytes) -> dict[str, Any]:
    """Retain finite vocabulary counts/markers, never raw diagnostics or text."""
    item_types: set[str] = set()
    event_types: dict[str, int] = {}
    markers: set[str] = set()
    counts_capped = False
    reconnect_notices = 0
    https_fallback_notices = 0

    def count(kind: str) -> None:
        nonlocal counts_capped
        previous = event_types.get(kind, 0)
        event_types[kind] = min(previous + 1, _MAX_DIAGNOSTIC_EVENT_COUNT)
        counts_capped |= previous >= _MAX_DIAGNOSTIC_EVENT_COUNT

    def inspect_error(text: str) -> None:
        lower = text.lower()
        markers.update(marker for marker in _ERROR_MARKERS if marker in lower)

    inspect_error(stderr.decode("utf-8", errors="replace"))
    for line in stdout.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            count("invalid_jsonl")
            continue
        if not isinstance(event, dict):
            count("invalid_event")
            continue
        kind = event.get("type")
        count(kind if isinstance(kind, str) and kind in _EVENT_TYPES else "unknown")
        if isinstance(kind, str) and kind in {"error", "turn.failed"}:
            inspect_error(_error_payload_text(event))
            if _reconnect_progress(event) is not None:
                reconnect_notices = min(reconnect_notices + 1, _MAX_DIAGNOSTIC_EVENT_COUNT)
            if _is_https_fallback(event):
                https_fallback_notices = min(
                    https_fallback_notices + 1, _MAX_DIAGNOSTIC_EVENT_COUNT
                )
        if kind in ("item.started", "item.updated", "item.completed"):
            item = event.get("item")
            if isinstance(item, dict):
                item_type = item.get("type")
                if isinstance(item_type, str) and re.fullmatch(r"[a-z_]{1,64}", item_type):
                    item_types.add(item_type)
                if item_type == "error":
                    inspect_error(_error_payload_text(item))
    return {
        "item_types": sorted(item_types),
        "event_types": dict(sorted(event_types.items())),
        "event_type_counts_capped": counts_capped,
        "error_markers": [marker for marker in _ERROR_MARKERS if marker in markers],
        "reconnect_notices": reconnect_notices,
        "https_fallback_notices": https_fallback_notices,
    }


def _parse_events(stdout: bytes) -> tuple[str, dict[str, int], int]:
    final_messages: list[str] = []
    usage: dict[str, int] = {}
    completed = 0
    thread_started = False
    turn_started = False
    needs_fresh_final = False
    event_count = 0
    try:
        for line in stdout.decode("utf-8").splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if not isinstance(event, dict):
                raise CodexCLIError("invalid_cli_event")
            event_count += 1
            kind = event.get("type")
            if kind == "turn.failed":
                raise CodexCLIError("cli_turn_failed")
            if kind == "error":
                recognized = _reconnect_progress(event) is not None or _is_https_fallback(event)
                if completed or not turn_started or not recognized:
                    raise CodexCLIError("cli_turn_failed")
                needs_fresh_final = True
                continue
            if completed:
                raise CodexCLIError("event_after_cli_completion")
            if kind == "thread.started":
                if thread_started or turn_started:
                    raise CodexCLIError("invalid_cli_lifecycle")
                thread_started = True
                continue
            if kind == "turn.started":
                if not thread_started or turn_started:
                    raise CodexCLIError("invalid_cli_lifecycle")
                turn_started = True
                continue
            if kind in {"item.started", "item.updated", "item.completed"}:
                item = event.get("item")
                if not isinstance(item, dict) or item.get("type") not in {
                    "agent_message",
                    "reasoning",
                    "error",
                }:
                    raise CodexCLIError("unexpected_tool_or_item")
                if (
                    kind == "item.completed"
                    and item.get("type") == "agent_message"
                    and item.get("phase") in {None, "final_answer"}
                ):
                    if not turn_started:
                        raise CodexCLIError("invalid_cli_lifecycle")
                    content = item.get("text")
                    if not isinstance(content, str) or not content.strip():
                        raise CodexCLIError("empty_cli_message")
                    final_messages.append(content)
                    needs_fresh_final = False
            elif kind == "turn.completed":
                if not turn_started:
                    raise CodexCLIError("invalid_cli_lifecycle")
                completed += 1
                raw_usage = event.get("usage", {})
                if not isinstance(raw_usage, dict):
                    raise CodexCLIError("invalid_cli_usage")
                usage = {
                    key: value
                    for key, value in raw_usage.items()
                    if key
                    in {
                        "input_tokens",
                        "cached_input_tokens",
                        "output_tokens",
                        "reasoning_output_tokens",
                    }
                    and type(value) is int
                    and value >= 0
                }
            else:
                raise CodexCLIError("unknown_cli_event")
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CodexCLIError("invalid_cli_jsonl") from exc
    if completed != 1 or not final_messages:
        raise CodexCLIError("missing_or_duplicate_cli_completion")
    if needs_fresh_final:
        raise CodexCLIError("missing_final_after_cli_reconnect")
    return final_messages[-1], usage, event_count


async def _terminate(proc: asyncio.subprocess.Process) -> None:
    # A leader can exit while descendants still hold its output pipes open.
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGTERM)
    try:
        await asyncio.wait_for(proc.wait(), timeout=1.0)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        await proc.wait()


class CodexCLIService:
    """One ephemeral CLI turn per planner call, with cancellation cleanup."""

    def __init__(
        self, *, binary: str = "codex", model: str = "gpt-6-luna", reasoning_effort: str = "low"
    ) -> None:
        self.binary = binary
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.records: list[dict[str, Any]] = []

    async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
        if kwargs.get("inject_core_memory", False) or kwargs.get("history"):
            raise CodexCLIError("eval_transport_does_not_accept_memory")
        record: dict[str, Any] = {
            "requested_model": self.model,
            "model_verification": "explicit_cli_flag_not_server_echo",
            "reasoning_effort": self.reasoning_effort,
            "tools_allowed": False,
            "max_tokens_and_temperature_enforced": False,
            "wrapper_sha256": hashlib.sha256(_WRAPPER.encode()).hexdigest(),
        }
        self.records.append(record)
        # These generated request/response files exist only for this invocation.
        with tempfile.TemporaryDirectory(prefix="director-codex-eval-") as temp:
            root = Path(temp)
            work = root / "empty-workspace"
            work.mkdir()
            instructions = root / "instructions.txt"
            final_path = root / "final.txt"
            system = LLMService._structured_json_contract(kwargs["system_instruction"])
            instructions.write_text(system, encoding="utf-8")
            args = [
                self.binary,
                "exec",
                "--ignore-user-config",
                "--ignore-rules",
                "--ephemeral",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "--json",
                "--color",
                "never",
                "--model",
                self.model,
                "--cd",
                str(work),
                "--output-last-message",
                str(final_path),
                "-c",
                f"model_instructions_file={json.dumps(str(instructions))}",
                "-c",
                f"model_reasoning_effort={json.dumps(self.reasoning_effort)}",
                "-c",
                f"developer_instructions={json.dumps(_WRAPPER)}",
                "-c",
                'approval_policy="never"',
                "-c",
                'web_search="disabled"',
                "-c",
                "project_doc_max_bytes=0",
                "-c",
                "mcp_servers={}",
            ]
            for feature in _DISABLED_FEATURES:
                args.extend(("--disable", feature))
            args.append("-")
            env = dict(os.environ)
            # Use existing CLI login, not an unrelated inherited API key.
            for name in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_THREAD_ID"):
                env.pop(name, None)
            proc = await asyncio.create_subprocess_exec(
                *args,
                cwd=work,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            try:
                stdout, stderr = await proc.communicate(kwargs["user_input"].encode("utf-8"))
            except BaseException:
                await _terminate(proc)
                record["cancelled_and_reaped"] = True
                raise
            record.update(exit_code=proc.returncode, stderr_bytes=len(stderr))
            diagnostics = _safe_diagnostics(stdout, stderr)
            record.update(
                item_types=diagnostics["item_types"],
                event_types=diagnostics["event_types"],
                event_type_counts_capped=diagnostics["event_type_counts_capped"],
                reconnect_notices=diagnostics["reconnect_notices"],
                https_fallback_notices=diagnostics["https_fallback_notices"],
            )
            # Error items are diagnostic; a specifically recognized reconnect
            # notice can recover only with a later final and terminal success.
            # Unknown errors / turn.failed remain fatal, including after success.
            record["recoverable_diagnostic_present"] = "error" in diagnostics["item_types"]
            if proc.returncode != 0:
                record["error_markers"] = diagnostics["error_markers"]
                raise CodexCLIError("cli_nonzero_exit")
            try:
                content, usage, event_count = _parse_events(stdout)
            except CodexCLIError as exc:
                record["failure_code"] = str(exc)
                record["error_markers"] = diagnostics["error_markers"]
                raise
            if (
                not final_path.exists()
                or final_path.read_text(encoding="utf-8").strip() != content.strip()
            ):
                raise CodexCLIError("cli_final_message_mismatch")
            record.update(event_count=event_count, completed=True)
            record["stream_recovered"] = (
                diagnostics["reconnect_notices"] + diagnostics["https_fallback_notices"] > 0
            )
            if record["stream_recovered"]:
                record["error_markers"] = diagnostics["error_markers"]
            return LLMResponse(
                content=content,
                model=self.model,
                provider="codex_cli",
                instance_id="temporary-codex-cli",
                usage=usage,
            )
