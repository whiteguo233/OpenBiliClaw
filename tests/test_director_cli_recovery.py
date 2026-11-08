"""Adversarial offline checks for recovered CLI turns, not planner quality."""

from __future__ import annotations

import json

import pytest

from openbiliclaw.recommendation.director.planner import HorizonPlanner
from tests.test_director_codex_transport import (
    FakeProcess,
    _complete_events,
    _install_process,
    _jsonl,
    _request,
)
from tests.test_director_codex_transport import transport as transport
from tests.test_director_horizon_modes import _planner_output
from tests.test_director_horizon_modes import _request as composition_request


def _recovered_events(*messages, final_text='{"screens":[]}'):
    events = _complete_events()
    events[3]["item"].update(text=final_text, phase="final_answer")
    events[2:2] = [{"type": "error", "message": message} for message in messages]
    return events


async def test_several_reconnects_require_fresh_final_and_preserve_safe_diagnostics(
    transport, monkeypatch
):
    events = _recovered_events(
        "Reconnecting... 1/5: PRIVATE_ENDPOINT stream disconnected before completion",
        "Reconnecting... 2/5 (PRIVATE_SECRET connection interrupted)",
        "Reconnecting... 3/5; PRIVATE_REASON",
    )
    _install_process(transport, monkeypatch, FakeProcess(_jsonl(*events)))
    service = transport.CodexCLIService()
    response = await _request(service)
    assert response.content == '{"screens":[]}'
    assert response.usage["output_tokens"] == 30
    record = service.records[0]
    assert record["completed"] is True
    assert record["stream_recovered"] is True
    assert record["reconnect_notices"] == record["event_types"]["error"] == 3
    assert record["error_markers"] == ["connection", "stream disconnected"]
    assert "PRIVATE" not in json.dumps(service.records)


@pytest.mark.parametrize("following", ["none", "commentary", "reasoning", "partial"])
async def test_stale_final_before_reconnect_is_not_salvaged_by_completion(
    transport, monkeypatch, following
):
    events = _complete_events()
    events.insert(4, {"type": "error", "message": "Reconnecting... 1/5"})
    if following != "none":
        item = {"type": "agent_message", "phase": "final_answer", "text": "PRIVATE_DRAFT"}
        kind = "item.completed"
        if following == "commentary":
            item["phase"] = "commentary"
        elif following == "reasoning":
            item["type"] = "reasoning"
        else:
            kind = "item.updated"
        events.insert(5, {"type": kind, "item": item})
    _install_process(transport, monkeypatch, FakeProcess(_jsonl(*events)))
    service = transport.CodexCLIService()
    with pytest.raises(transport.CodexCLIError, match="^missing_final_after_cli_reconnect$"):
        await _request(service)
    assert not service.records[0].get("completed")


@pytest.mark.parametrize(
    "message",
    [
        "PRIVATE_AUTH_FAILURE unauthorized",
        "stream disconnected before completion",
        "PRIVATE_PREFIX Reconnecting... 1/5",
        "Reconnecting... 0/5",
        "Reconnecting... 6/5",
        "Reconnecting... 1/0",
        "Reconnecting... 1/11",
        "Reconnecting... 01/5",
        "Reconnecting... 1/05",
        "Reconnecting... -1/5",
        "Reconnecting... 1/5PRIVATE_SUFFIX",
        "Reconnecting... 1/5\nPRIVATE_FATAL",
        "Reconnecting... 1/5\rPRIVATE_FATAL",
        "Reconnecting... 1/5: PRIVATE_RETRY\rPRIVATE_FATAL",
        "Reconnecting... 1/5 " + "x" * 4096,
        {"message": "Reconnecting... 1/5"},
        None,
    ],
)
async def test_unrecognized_or_unbounded_errors_fail_before_otherwise_valid_final(
    transport, monkeypatch, message
):
    events = _recovered_events(message)
    _install_process(transport, monkeypatch, FakeProcess(_jsonl(*events)))
    service = transport.CodexCLIService()
    with pytest.raises(transport.CodexCLIError, match="^cli_turn_failed$") as caught:
        await _request(service)
    assert not service.records[0].get("completed")
    assert "PRIVATE" not in str(caught.value)
    assert "PRIVATE" not in json.dumps(service.records)


@pytest.mark.parametrize("position", [0, 2, 5])
async def test_turn_failed_is_never_reclassified_as_reconnect(transport, monkeypatch, position):
    events = _complete_events()
    events.insert(position, {"type": "turn.failed", "message": "Reconnecting... 1/5"})
    _install_process(transport, monkeypatch, FakeProcess(_jsonl(*events)))
    service = transport.CodexCLIService()
    with pytest.raises(transport.CodexCLIError, match="^cli_turn_failed$"):
        await _request(service)
    assert not service.records[0].get("completed")


@pytest.mark.parametrize("position", [0, 1, 5])
async def test_reconnect_outside_active_turn_fails(transport, monkeypatch, position):
    events = _complete_events()
    events.insert(position, {"type": "error", "message": "Reconnecting... 1/5"})
    _install_process(transport, monkeypatch, FakeProcess(_jsonl(*events)))
    with pytest.raises(transport.CodexCLIError, match="^cli_turn_failed$"):
        await _request(transport.CodexCLIService())


@pytest.mark.parametrize("replay", ["thread", "turn", "full_transcript", "second_completion"])
async def test_duplicate_lifecycle_cannot_attach_a_stale_success(transport, monkeypatch, replay):
    events = _recovered_events("Reconnecting... 1/5")
    if replay == "thread":
        events.insert(2, {"type": "thread.started", "thread_id": "PRIVATE_REPLAY"})
    elif replay == "turn":
        events.insert(2, {"type": "turn.started"})
    elif replay == "full_transcript":
        events.extend(_complete_events())
    else:
        events.append(events[-1])
    _install_process(transport, monkeypatch, FakeProcess(_jsonl(*events)))
    service = transport.CodexCLIService()
    with pytest.raises(transport.CodexCLIError):
        await _request(service)
    assert not service.records[0].get("completed")


@pytest.mark.parametrize("item_type", ["reasoning", "agent_message", "error"])
async def test_even_diagnostic_items_after_terminal_completion_are_rejected(
    transport, monkeypatch, item_type
):
    events = _recovered_events("Reconnecting... 1/5")
    events.append({"type": "item.completed", "item": {"type": item_type, "text": "PRIVATE"}})
    _install_process(transport, monkeypatch, FakeProcess(_jsonl(*events)))
    with pytest.raises(transport.CodexCLIError, match="^event_after_cli_completion$"):
        await _request(transport.CodexCLIService())


@pytest.mark.parametrize("failure", ["no_completion", "nonzero_exit"])
async def test_retry_and_matching_final_do_not_replace_terminal_success(
    transport, monkeypatch, failure
):
    events = _recovered_events("Reconnecting... 1/5")
    if failure == "no_completion":
        events.pop()
    process = FakeProcess(_jsonl(*events), returncode=7 if failure == "nonzero_exit" else 0)
    _install_process(transport, monkeypatch, process)
    service = transport.CodexCLIService()
    with pytest.raises(transport.CodexCLIError):
        await _request(service)
    assert not service.records[0].get("completed")


@pytest.mark.parametrize("final_text", [None, '{"PRIVATE_STALE":true}'])
async def test_recovery_does_not_bypass_fresh_final_file_agreement(
    transport, monkeypatch, final_text
):
    events = _recovered_events("Reconnecting... 1/5")
    _install_process(transport, monkeypatch, FakeProcess(_jsonl(*events)), final_text=final_text)
    service = transport.CodexCLIService()
    with pytest.raises(transport.CodexCLIError, match="^cli_final_message_mismatch$"):
        await _request(service)
    assert not service.records[0].get("completed")


@pytest.mark.parametrize("final_text", ["{", "{}", '{"screens":[]}'])
async def test_recovered_transport_does_not_make_invalid_planner_output_valid(
    transport, monkeypatch, final_text
):
    events = _recovered_events("Reconnecting... 1/5", final_text=final_text)
    _install_process(transport, monkeypatch, FakeProcess(_jsonl(*events)), final_text=final_text)
    service = transport.CodexCLIService()
    result = await HorizonPlanner(service).plan(composition_request())
    assert service.records[0]["completed"] is True
    assert service.records[0]["stream_recovered"] is True
    assert result.plan is None
    assert result.reason_code == "invalid_planner_output"
    assert result.calls == len(service.records) == 2


async def test_recovered_valid_plan_crosses_actual_planner_boundary(transport, monkeypatch):
    request = composition_request()
    final_text = _planner_output(request)
    events = _recovered_events("Reconnecting... 2/5", final_text=final_text)
    _install_process(transport, monkeypatch, FakeProcess(_jsonl(*events)), final_text=final_text)
    service = transport.CodexCLIService()
    result = await HorizonPlanner(service).plan(request)
    assert service.records[0]["completed"] is True
    assert result.reason_code == "agent_horizon_prepared"
    assert result.plan is not None
    assert tuple(len(screen.card_refs) for screen in result.plan.screens) == (6, 6, 6)
    assert result.calls == 1


async def test_recovered_six_four_two_draft_still_fails_horizon_coverage(transport, monkeypatch):
    request = composition_request()
    output = json.loads(_planner_output(request))
    output["screens"][1]["pairs"] = output["screens"][1]["pairs"][:2]
    output["screens"][2]["pairs"] = output["screens"][2]["pairs"][:1]
    final_text = json.dumps(output)
    events = _recovered_events("Reconnecting... 2/5", final_text=final_text)
    _install_process(transport, monkeypatch, FakeProcess(_jsonl(*events)), final_text=final_text)
    service = transport.CodexCLIService()
    result = await HorizonPlanner(service).plan(request)
    assert service.records[0]["completed"] is True
    assert result.plan is None
    assert result.reason_code == "invalid_planner_output"
    assert result.calls == len(service.records) == 2
