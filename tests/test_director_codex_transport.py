"""No-network checks at the temporary Codex CLI model transport boundary."""

from __future__ import annotations

import asyncio
import importlib
import json
import signal
from dataclasses import asdict
from pathlib import Path

import pytest


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("scripts.director_codex_transport")


def _jsonl(*events):
    return "\n".join(json.dumps(event, ensure_ascii=False) for event in events).encode()


def test_eval_wrapper_does_not_mislabel_real_snapshot_data_as_synthetic(transport):
    assert "synthetic" not in transport._WRAPPER
    assert "Use only the supplied data" in transport._WRAPPER


def _complete_events():
    return [
        {"type": "thread.started", "thread_id": "not-retained-thread-id"},
        {"type": "turn.started"},
        {
            "type": "item.completed",
            "item": {"id": "reason", "type": "reasoning", "text": "PRIVATE_REASONING"},
        },
        {
            "type": "item.completed",
            "item": {"id": "answer", "type": "agent_message", "text": '{"screens":[]}'},
        },
        {
            "type": "turn.completed",
            "usage": {"input_tokens": 100, "cached_input_tokens": 20, "output_tokens": 30},
        },
    ]


class FakeProcess:
    """The external process seam: a fixed synthetic JSONL transcript."""

    def __init__(self, stdout, *, stderr=b"PRIVATE_STDERR", returncode=0):
        self.output = stdout
        self.stderr_output = stderr
        self.returncode = returncode
        self.pid = 43210
        self.stdin_bytes = None

    async def communicate(self, input=None):
        self.stdin_bytes = input
        return self.output, self.stderr_output

    async def wait(self):
        return self.returncode


async def test_only_final_content_and_usage_are_returned(transport, monkeypatch):
    process = FakeProcess(_jsonl(*_complete_events()))

    async def fake_exec(*args, **kwargs):
        Path(args[args.index("--output-last-message") + 1]).write_text(
            '{"screens":[]}', encoding="utf-8"
        )
        return process

    monkeypatch.setattr(transport.asyncio, "create_subprocess_exec", fake_exec)
    response = await transport.CodexCLIService().complete_structured_task(
        system_instruction="固定编排规则",
        user_input='{"synthetic":true}',
        inject_core_memory=False,
    )
    assert response.content == '{"screens":[]}'
    assert response.model == "gpt-6-luna"
    assert response.usage == {
        "input_tokens": 100,
        "cached_input_tokens": 20,
        "output_tokens": 30,
    }
    persisted = json.dumps(asdict(response))
    assert "PRIVATE_REASONING" not in persisted
    assert "PRIVATE_STDERR" not in persisted
    assert "not-retained-thread-id" not in persisted
    assert response.tool_calls is None


async def test_reconnection_notice_before_valid_final_does_not_discard_completed_turn(
    transport, monkeypatch
):
    events = _complete_events()
    events.insert(2, {"type": "error", "message": "Reconnecting... 1/5"})
    _install_process(transport, monkeypatch, FakeProcess(_jsonl(*events)))
    response = await _request(transport.CodexCLIService())
    assert response.content == '{"screens":[]}'
    assert response.usage["output_tokens"] == 30


async def test_explicit_websocket_fallback_before_fresh_final_can_recover(transport, monkeypatch):
    events = _complete_events()
    events.insert(
        2,
        {"type": "error", "message": "Falling back from WebSockets to HTTPS transport. "},
    )
    _install_process(transport, monkeypatch, FakeProcess(_jsonl(*events)))
    service = transport.CodexCLIService()
    response = await _request(service)
    assert response.content == '{"screens":[]}'
    assert service.records[0]["https_fallback_notices"] == 1
    assert service.records[0]["stream_recovered"] is True


@pytest.mark.parametrize(
    "message",
    [
        "Falling back from WebSockets to HTTPS transport.PRIVATE",
        "Falling back from WebSockets to HTTPS transport.\rPRIVATE",
        "Falling back from WebSockets to HTTPS transport. \nPRIVATE",
        "Falling back from WebSockets to HTTPS transport. " + "x" * 4096,
    ],
)
async def test_malformed_https_fallback_is_not_recovery(transport, monkeypatch, message):
    events = _complete_events()
    events.insert(2, {"type": "error", "message": message})
    _install_process(transport, monkeypatch, FakeProcess(_jsonl(*events)))
    with pytest.raises(transport.CodexCLIError, match="^cli_turn_failed$"):
        await _request(transport.CodexCLIService())


def _install_process(transport, monkeypatch, process, *, final_text='{"screens":[]}'):
    captured = {}

    async def fake_exec(*args, **kwargs):
        captured.update(args=args, kwargs=kwargs)
        cwd = Path(kwargs["cwd"])
        captured["workspace_files"] = list(cwd.iterdir())
        configs = [args[index + 1] for index, arg in enumerate(args[:-1]) if arg == "-c"]
        for config in configs:
            if config.startswith("model_instructions_file="):
                instruction_path = Path(json.loads(config.split("=", 1)[1]))
                captured["instructions"] = instruction_path.read_text(encoding="utf-8")
        if final_text is not None:
            Path(args[args.index("--output-last-message") + 1]).write_text(
                final_text, encoding="utf-8"
            )
        return process

    monkeypatch.setattr(transport.asyncio, "create_subprocess_exec", fake_exec)
    return captured


async def _request(service, **changes):
    return await service.complete_structured_task(
        **{
            "system_instruction": "固定编排规则",
            "user_input": '{"synthetic":true}',
            "inject_core_memory": False,
            "max_tokens": 4096,
            "temperature": 0.3,
            **changes,
        }
    )


async def test_request_uses_isolated_tool_free_cli_and_stdin(transport, monkeypatch):
    from openbiliclaw.llm.service import LLMService

    monkeypatch.setenv("OPENAI_API_KEY", "PRIVATE_OPENAI_KEY")
    monkeypatch.setenv("CODEX_API_KEY", "PRIVATE_CODEX_KEY")
    monkeypatch.setenv("CODEX_THREAD_ID", "PRIVATE_THREAD")
    process = FakeProcess(_jsonl(*_complete_events()))
    captured = _install_process(transport, monkeypatch, process)
    service = transport.CodexCLIService()
    user_input = '{"literal":"$(echo DO_NOT_EXECUTE); `not-a-command`"}'
    await _request(service, user_input=user_input)
    args, options = captured["args"], captured["kwargs"]
    assert args[:2] == ("codex", "exec")
    assert args[-1] == "-"
    assert user_input not in args
    assert process.stdin_bytes == user_input.encode()
    for flag in ("--ignore-user-config", "--ignore-rules", "--ephemeral", "--json"):
        assert flag in args
    assert args[args.index("--model") + 1] == "gpt-6-luna"
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert options["start_new_session"] is True
    assert options["stdin"] == asyncio.subprocess.PIPE
    assert options["stdout"] == asyncio.subprocess.PIPE
    assert options["stderr"] == asyncio.subprocess.PIPE
    assert captured["workspace_files"] == []
    assert Path(options["cwd"]) != Path(__file__).resolve().parents[1]
    assert not Path(options["cwd"]).exists()
    assert args[args.index("--cd") + 1] == str(options["cwd"])
    for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_THREAD_ID"):
        assert key not in options["env"]
    # Parent credentials/config are not altered by the subprocess environment.
    import os

    assert os.environ["OPENAI_API_KEY"] == "PRIVATE_OPENAI_KEY"
    assert captured["instructions"] == LLMService._structured_json_contract("固定编排规则")
    configs = {args[index + 1] for index, arg in enumerate(args[:-1]) if arg == "-c"}
    assert {'approval_policy="never"', 'web_search="disabled"', "mcp_servers={}"} <= configs
    assert "project_doc_max_bytes=0" in configs
    disabled = {args[index + 1] for index, arg in enumerate(args[:-1]) if arg == "--disable"}
    assert {
        "shell_tool",
        "unified_exec",
        "apps",
        "plugins",
        "hooks",
        "multi_agent",
        "browser_use",
        "computer_use",
        "in_app_browser",
        "memories",
        "skill_search",
    } <= disabled
    record = service.records[0]
    assert record["tools_allowed"] is False
    assert record["max_tokens_and_temperature_enforced"] is False
    assert record["model_verification"] == "explicit_cli_flag_not_server_echo"
    assert "PRIVATE" not in json.dumps(record)


@pytest.mark.parametrize(
    "item_type", ["command_execution", "mcp_tool_call", "web_search", "todo_list"]
)
@pytest.mark.parametrize("event_kind", ["item.started", "item.updated", "item.completed"])
async def test_tool_or_unrecognized_item_is_rejected(transport, monkeypatch, item_type, event_kind):
    events = _complete_events()
    events.insert(2, {"type": event_kind, "item": {"type": item_type, "text": "PRIVATE_TOOL"}})
    process = FakeProcess(_jsonl(*events))
    _install_process(transport, monkeypatch, process)
    service = transport.CodexCLIService()
    with pytest.raises(transport.CodexCLIError, match="^unexpected_tool_or_item$") as caught:
        await _request(service)
    assert "PRIVATE" not in str(caught.value)
    assert "PRIVATE" not in json.dumps(service.records)


@pytest.mark.parametrize("failure_kind", ["error", "turn.failed", "unrecognized.event"])
async def test_failed_or_unknown_event_cannot_be_misreported_as_success(
    transport, monkeypatch, failure_kind
):
    events = _complete_events()
    events.append({"type": failure_kind, "message": "PRIVATE_ERROR_BODY"})
    _install_process(transport, monkeypatch, FakeProcess(_jsonl(*events)))
    service = transport.CodexCLIService()
    with pytest.raises(transport.CodexCLIError) as caught:
        await _request(service)
    assert "PRIVATE" not in str(caught.value)
    assert not service.records[0].get("completed")


@pytest.mark.parametrize("failure_kind", ["error", "turn.failed"])
async def test_exit_zero_fatal_error_retains_only_safe_markers_not_final_success(
    transport, monkeypatch, failure_kind
):
    events = _complete_events()
    payload = {
        "message": "PRIVATE_ENDPOINT?key=PRIVATE_SECRET: usage limit HTTP 429",
        "code": "PRIVATE_ERROR_CODE",
    }
    events.append(
        {"type": failure_kind, **(payload if failure_kind == "error" else {"error": payload})}
    )
    process = FakeProcess(
        _jsonl(*events), stderr=b"PRIVATE_STDERR stream disconnected", returncode=0
    )
    _install_process(transport, monkeypatch, process)
    service = transport.CodexCLIService()
    # A matching final file and turn.completed must never salvage a later fatal
    # event, even if the CLI process itself returned exit status zero.
    with pytest.raises(transport.CodexCLIError, match="^cli_turn_failed$") as caught:
        await _request(service)
    record = service.records[0]
    assert record["exit_code"] == 0
    assert record["failure_code"] == "cli_turn_failed"
    assert record["error_markers"] == ["429", "usage limit", "stream disconnected"]
    assert record["event_types"][failure_kind] == 1
    assert record["event_types"]["turn.completed"] == 1
    assert record["event_types"]["item.completed"] == 2
    assert record["event_type_counts_capped"] is False
    assert not record.get("completed")
    assert "PRIVATE" not in json.dumps(record)
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize("returncode", [0, 7])
async def test_markers_never_come_from_candidate_prompt_reasoning_or_final_text(
    transport, monkeypatch, returncode
):
    misleading = "PRIVATE_CANDIDATE discusses usage limit, rate limit, 429, quota"
    events = _complete_events()
    events[0]["prompt"] = misleading
    events[2]["item"]["text"] = misleading
    events[3]["item"]["text"] = misleading
    events.append(
        {
            "type": "error",
            "message": "PRIVATE_FATAL",
            "prompt": misleading,
            "candidates": [{"summary": misleading}],
        }
    )
    process = FakeProcess(_jsonl(*events), stderr=b"", returncode=returncode)
    _install_process(transport, monkeypatch, process, final_text=misleading)
    service = transport.CodexCLIService()
    with pytest.raises(transport.CodexCLIError):
        await _request(service, user_input=misleading)
    assert service.records[0]["error_markers"] == []
    assert "PRIVATE" not in json.dumps(service.records)


def test_event_diagnostic_names_and_counts_are_bounded_and_do_not_retain_unknown_data(transport):
    events = [
        {"type": "PRIVATE_UNKNOWN_EVENT", "message": "PRIVATE_BODY 429"},
        {"type": ["PRIVATE_BAD_KIND"]},
        {"type": "item.completed", "item": {"type": "error", "status_code": 429}},
    ]
    diagnostics = transport._safe_diagnostics(
        _jsonl(*events)
        + b"\n"
        + (b'{"type":"turn.started"}\n' * (transport._MAX_DIAGNOSTIC_EVENT_COUNT + 1)),
        b"",
    )
    assert diagnostics["event_types"] == {
        "item.completed": 1,
        "turn.started": transport._MAX_DIAGNOSTIC_EVENT_COUNT,
        "unknown": 2,
    }
    assert diagnostics["event_type_counts_capped"] is True
    assert diagnostics["error_markers"] == ["429"]
    assert "PRIVATE" not in json.dumps(diagnostics)


@pytest.mark.parametrize("failure", ["no_completion", "two_completions", "no_final", "commentary"])
async def test_exactly_one_completion_and_a_final_answer_are_required(
    transport, monkeypatch, failure
):
    events = _complete_events()
    if failure == "no_completion":
        events.pop()
    elif failure == "two_completions":
        events.append(events[-1])
    elif failure == "no_final":
        events.pop(3)
    else:
        events[3]["item"]["phase"] = "commentary"
    _install_process(transport, monkeypatch, FakeProcess(_jsonl(*events)))
    expected = (
        "event_after_cli_completion"
        if failure == "two_completions"
        else "missing_or_duplicate_cli_completion"
    )
    with pytest.raises(transport.CodexCLIError, match=f"^{expected}$"):
        await _request(transport.CodexCLIService())


@pytest.mark.parametrize("raw", [b"PRIVATE_NOT_JSON", b"\xff", b"[]", b'{"type":"item.completed"}'])
async def test_malformed_jsonl_is_rejected_without_returning_raw_output(
    transport, monkeypatch, raw
):
    _install_process(transport, monkeypatch, FakeProcess(raw))
    service = transport.CodexCLIService()
    with pytest.raises(transport.CodexCLIError) as caught:
        await _request(service)
    assert "PRIVATE" not in str(caught.value)
    assert "PRIVATE" not in json.dumps(service.records)


@pytest.mark.parametrize("failure", ["nonzero", "missing_file", "mismatch"])
async def test_process_and_final_file_must_agree_with_successful_jsonl(
    transport, monkeypatch, failure
):
    process = FakeProcess(_jsonl(*_complete_events()), returncode=7 if failure == "nonzero" else 0)
    _install_process(
        transport,
        monkeypatch,
        process,
        final_text=None if failure == "missing_file" else "PRIVATE_MISMATCH",
    )
    service = transport.CodexCLIService()
    expected = "cli_nonzero_exit" if failure == "nonzero" else "cli_final_message_mismatch"
    with pytest.raises(transport.CodexCLIError, match=f"^{expected}$"):
        await _request(service)
    assert "PRIVATE" not in json.dumps(service.records)
    assert not service.records[0].get("completed")


async def test_usage_is_numeric_whitelist_and_commentary_is_not_saved(transport, monkeypatch):
    events = _complete_events()
    events.insert(
        3,
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "phase": "commentary", "text": "PRIVATE_COMMENTARY"},
        },
    )
    events[-1]["usage"].update(
        {
            "private": "PRIVATE_USAGE",
            "input_tokens": True,
            "cached_input_tokens": -10,
            "reasoning_output_tokens": 5,
        }
    )
    _install_process(transport, monkeypatch, FakeProcess(_jsonl(*events)))
    service = transport.CodexCLIService()
    result = await _request(service)
    assert result.content == '{"screens":[]}'
    assert result.usage == {"output_tokens": 30, "reasoning_output_tokens": 5}
    assert "PRIVATE" not in json.dumps(asdict(result))
    assert "PRIVATE" not in json.dumps(service.records)


@pytest.mark.parametrize(
    "outcome", ["completed", "no_completion", "mismatch", "fatal_error", "tool", "nonzero"]
)
async def test_diagnostic_error_item_requires_independently_verified_final_completion(
    transport, monkeypatch, outcome
):
    events = _complete_events()
    events.insert(
        2,
        {
            "type": "item.completed",
            "item": {"type": "error", "message": "PRIVATE_DIAGNOSTIC_WARNING"},
        },
    )
    if outcome == "no_completion":
        events.pop()
    elif outcome == "fatal_error":
        events.append({"type": "error", "message": "PRIVATE_FATAL_ERROR"})
    elif outcome == "tool":
        events.insert(3, {"type": "item.started", "item": {"type": "command_execution"}})
    process = FakeProcess(_jsonl(*events), returncode=7 if outcome == "nonzero" else 0)
    _install_process(
        transport,
        monkeypatch,
        process,
        final_text="PRIVATE_WRONG_FINAL" if outcome == "mismatch" else '{"screens":[]}',
    )
    service = transport.CodexCLIService()
    if outcome == "completed":
        response = await _request(service)
        assert response.content == '{"screens":[]}'
        assert "PRIVATE" not in json.dumps(asdict(response))
        assert service.records[0]["completed"] is True
    else:
        with pytest.raises(transport.CodexCLIError) as caught:
            await _request(service)
        assert "PRIVATE" not in str(caught.value)
        assert not service.records[0].get("completed")
    assert service.records[0]["recoverable_diagnostic_present"] is True
    assert "error" in service.records[0]["item_types"]
    assert "PRIVATE" not in json.dumps(service.records)


@pytest.mark.parametrize("changes", [{"inject_core_memory": True}, {"history": [{"content": "x"}]}])
async def test_real_memory_and_history_are_rejected_before_subprocess(
    transport, monkeypatch, changes
):
    async def forbidden_exec(*args, **kwargs):
        pytest.fail("memory-bearing requests must not launch the CLI")

    monkeypatch.setattr(transport.asyncio, "create_subprocess_exec", forbidden_exec)
    with pytest.raises(transport.CodexCLIError, match="^eval_transport_does_not_accept_memory$"):
        await _request(transport.CodexCLIService(), **changes)


@pytest.mark.parametrize("requires_kill", [False, True])
async def test_cancellation_terminates_process_group_and_reaps_child(
    transport, monkeypatch, requires_kill
):
    started = asyncio.Event()
    reaped = asyncio.Event()
    exited = asyncio.Event()
    signals = []

    class BlockingProcess(FakeProcess):
        def __init__(self):
            super().__init__(b"", returncode=None)

        async def communicate(self, input=None):
            started.set()
            await asyncio.Event().wait()

        async def wait(self):
            await exited.wait()
            reaped.set()
            return self.returncode

    process = BlockingProcess()
    captured = _install_process(transport, monkeypatch, process)

    def killpg(pid, sig):
        signals.append((pid, sig))
        if sig == signal.SIGKILL or not requires_kill:
            process.returncode = -int(sig)
            exited.set()

    monkeypatch.setattr(transport.os, "killpg", killpg)
    service = transport.CodexCLIService()
    task = asyncio.create_task(_request(service))
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=3)
    assert signals[0] == (43210, signal.SIGTERM)
    assert (43210, signal.SIGKILL) in signals if requires_kill else len(signals) == 1
    assert reaped.is_set()
    assert service.records[0]["cancelled_and_reaped"] is True
    assert not Path(captured["kwargs"]["cwd"]).exists()


async def test_cancellation_signals_group_when_exited_leader_has_descendants_holding_pipes(
    transport, monkeypatch
):
    started = asyncio.Event()
    signals = []
    waited = []

    class ExitedLeader(FakeProcess):
        async def communicate(self, input=None):
            # The leader has exited, but a descendant still owns an output pipe.
            started.set()
            await asyncio.Event().wait()

        async def wait(self):
            waited.append(True)
            return 0

    process = ExitedLeader(b"", returncode=0)
    _install_process(transport, monkeypatch, process)
    monkeypatch.setattr(transport.os, "killpg", lambda pid, sig: signals.append((pid, sig)))
    service = transport.CodexCLIService()
    task = asyncio.create_task(_request(service))
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    assert signals == [(43210, signal.SIGTERM)]
    assert waited == [True]
    assert service.records[0]["cancelled_and_reaped"] is True
