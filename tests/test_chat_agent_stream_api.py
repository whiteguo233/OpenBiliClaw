"""API tests for the true-streaming agent chat endpoint (M2).

``POST /api/chat/agent/stream`` consumes ``AgentLoop.run`` and forwards one
SSE event per ``AgentEvent``; durable turns persist the loop's events into
``payload.agent_events`` for history replay. The legacy fake-streaming
``/api/chat/stream`` path must stay untouched.
"""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from fastapi.testclient import TestClient

from openbiliclaw.agent.loop import AgentEvent
from openbiliclaw.api import app as app_module
from openbiliclaw.api.app import create_app
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


class FakeAgentDialogue:
    """Dialogue double streaming a scripted sequence of agent events."""

    def __init__(self, script: list[AgentEvent | Exception]) -> None:
        self._script = script
        self.agent_calls: list[dict[str, Any]] = []
        self.legacy_calls: list[str] = []

    async def stream_agent_reply(
        self,
        agent_loop: Any,
        message: str,
        *,
        session: str = "",
        scope: str = "chat",
        turn_id: str = "",
        session_id: str = "",
        skill: Any = None,
        tools: Any = None,
        skill_switch_guide: str = "",
        persona_id: str = "natural",
    ) -> Any:
        self.agent_calls.append(
            {
                "agent_loop": agent_loop,
                "message": message,
                "session": session,
                "scope": scope,
                "turn_id": turn_id,
                "session_id": session_id,
                "skill": skill,
                "tools": tools,
                "skill_switch_guide": skill_switch_guide,
                "persona_id": persona_id,
            }
        )
        for item in self._script:
            if isinstance(item, Exception):
                raise item
            yield item

    async def respond(self, message: str, **kwargs: Any) -> str:
        del kwargs
        self.legacy_calls.append(message)
        return "legacy 单跳回复"


def _database(tmp_path: Path) -> Database:
    database = Database(tmp_path / "openbiliclaw.db")
    database.initialize()
    return database


def _parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for block in body.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event = ""
        data = ""
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[len("event: ") :]
            elif line.startswith("data: "):
                data = line[len("data: ") :]
        events.append((event, json.loads(data)))
    return events


def _multi_hop_script() -> list[AgentEvent]:
    return [
        AgentEvent(type="thinking", step=1, text="我先看看你的订阅"),
        AgentEvent(
            type="tool_call",
            step=1,
            tool_name="list_sources",
            arguments={},
            summary="list_sources()",
        ),
        AgentEvent(type="tool_result", step=1, tool_name="list_sources", text="当前没有订阅"),
        AgentEvent(type="final", step=2, text="你还没有订阅任何内容源。"),
    ]


def _app(tmp_path: Path, dialogue: FakeAgentDialogue) -> Any:
    database = _database(tmp_path)
    app = create_app(
        memory_manager=object(),
        database=database,
        soul_engine=object(),
        dialogue=dialogue,
    )
    app.state.runtime_context.agent_loop = object()
    return app


def test_agent_stream_multi_hop_events_and_turn_persisted(tmp_path: Path) -> None:
    dialogue = FakeAgentDialogue(_multi_hop_script())
    app = _app(tmp_path, dialogue)

    with TestClient(app) as client:
        created = client.post(
            "/api/chat/turns",
            json={
                "turn_id": "agent-turn-1",
                "session": "desktop",
                "scope": "chat",
                "message": "我订阅了什么？",
                "streaming": True,
            },
        )
        assert created.status_code == 200
        assert created.json()["status"] == "pending"

        response = client.post(
            "/api/chat/agent/stream",
            json={
                "turn_id": "agent-turn-1",
                "session": "desktop",
                "message": "我订阅了什么？",
            },
        )

    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert [event for event, _data in events] == [
        "thinking",
        "tool_call",
        "tool_result",
        "final",
        "done",
    ]
    assert events[1][1]["tool_name"] == "list_sources"
    assert events[1][1]["summary"] == "list_sources()"
    assert events[2][1]["ok"] is True
    assert events[3][1]["text"] == "你还没有订阅任何内容源。"
    done = events[4][1]
    assert done["reply"] == "你还没有订阅任何内容源。"
    assert done["turn_id"] == "agent-turn-1"
    assert done["skill"] == "taste-companion"

    # The loop ran under the dialogue lease with the turn context threaded.
    assert len(dialogue.agent_calls) == 1
    call = dialogue.agent_calls[0]
    assert call["turn_id"] == "agent-turn-1"
    assert call["scope"] == "chat"
    assert call["message"] == "我订阅了什么？"

    # Durable completion + persisted event stream for history replay.
    row = app.state.runtime_context.database.get_chat_turn("agent-turn-1")
    assert row is not None
    assert row["status"] == "completed"
    assert row["reply"] == "你还没有订阅任何内容源。"
    persisted = row["payload"]["agent_events"]
    assert [event["type"] for event in persisted] == [
        "thinking",
        "tool_call",
        "tool_result",
        "final",
    ]
    assert persisted[1]["tool_name"] == "list_sources"


def test_agent_stream_step_limit_reached_then_final(tmp_path: Path) -> None:
    dialogue = FakeAgentDialogue(
        [
            AgentEvent(
                type="step_limit_reached",
                step=64,
                text="已达到本次任务的步数上限（64 跳）。",
            ),
            AgentEvent(type="final", step=64, text="目前进展如下……"),
        ]
    )
    app = _app(tmp_path, dialogue)

    with TestClient(app) as client:
        response = client.post(
            "/api/chat/agent/stream",
            json={"turn_id": "", "message": "整理一下我的观看历史"},
        )

    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert [event for event, _data in events] == ["step_limit_reached", "final", "done"]
    assert events[0][1]["step"] == 64
    assert events[2][1]["reply"] == "目前进展如下……"


def test_agent_stream_llm_error_maps_to_error_event_and_fails_turn(tmp_path: Path) -> None:
    dialogue = FakeAgentDialogue(
        [
            AgentEvent(type="thinking", step=1, text="我先试试"),
            RuntimeError("provider exploded"),
        ]
    )
    app = _app(tmp_path, dialogue)

    with TestClient(app) as client:
        client.post(
            "/api/chat/turns",
            json={
                "turn_id": "agent-turn-err",
                "session": "popup",
                "message": "你好",
                "streaming": True,
            },
        )
        response = client.post(
            "/api/chat/agent/stream",
            json={"turn_id": "agent-turn-err", "message": "你好"},
        )

    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert [event for event, _data in events] == ["thinking", "error"]
    assert events[1][1]["error"]

    row = app.state.runtime_context.database.get_chat_turn("agent-turn-err")
    assert row["status"] == "failed"
    assert row["error"] == events[1][1]["error"]
    # Partial events up to the failure are persisted for replay/audit.
    persisted = row["payload"]["agent_events"]
    assert [event["type"] for event in persisted] == ["thinking"]


def test_agent_stream_without_turn_id_is_ephemeral(tmp_path: Path) -> None:
    dialogue = FakeAgentDialogue([AgentEvent(type="final", step=1, text="你好呀")])
    app = _app(tmp_path, dialogue)

    with TestClient(app) as client:
        response = client.post(
            "/api/chat/agent/stream",
            json={"message": "你好"},
        )

    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert [event for event, _data in events] == ["final", "done"]
    assert events[1][1]["turn_id"] == ""
    assert dialogue.agent_calls[0]["scope"] == "chat"


def test_agent_stream_disabled_by_config(tmp_path: Path) -> None:
    dialogue = FakeAgentDialogue([AgentEvent(type="final", step=1, text="不应到达")])
    app = _app(tmp_path, dialogue)
    app.state.runtime_context.config = SimpleNamespace(agent=SimpleNamespace(loop_enabled=False))

    with TestClient(app) as client:
        response = client.post(
            "/api/chat/agent/stream",
            json={"message": "你好"},
        )

    assert response.status_code == 503
    assert dialogue.agent_calls == []


def test_agent_stream_unavailable_without_loop(tmp_path: Path) -> None:
    dialogue = FakeAgentDialogue([AgentEvent(type="final", step=1, text="不应到达")])
    app = _app(tmp_path, dialogue)
    app.state.runtime_context.agent_loop = None

    with TestClient(app) as client:
        response = client.post(
            "/api/chat/agent/stream",
            json={"message": "你好"},
        )

    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert [event for event, _data in events] == ["error"]


def test_legacy_chat_endpoints_unaffected(tmp_path: Path) -> None:
    dialogue = FakeAgentDialogue([])
    app = _app(tmp_path, dialogue)

    with TestClient(app) as client:
        legacy = client.post("/api/chat", json={"message": "你好"})
        assert legacy.status_code == 200
        assert legacy.json() == {"reply": "legacy 单跳回复"}

        legacy_stream = client.post("/api/chat/stream", json={"message": "你好"})
        assert legacy_stream.status_code == 200
        events = _parse_sse(legacy_stream.text)
        assert events[0][0] == "phase"
        assert events[-1] == ("done", {"reply": "legacy 单跳回复"})

    assert dialogue.agent_calls == []
    assert dialogue.legacy_calls == ["你好", "你好"]


def test_streaming_turn_persists_agent_markers(tmp_path: Path) -> None:
    dialogue = FakeAgentDialogue([AgentEvent(type="final", step=1, text="hi")])
    app = _app(tmp_path, dialogue)

    with TestClient(app) as client:
        client.post(
            "/api/chat/turns",
            json={
                "turn_id": "marked-turn",
                "session": "desktop",
                "message": "你好",
                "streaming": True,
                "skill": "system-steward",
            },
        )
        client.post(
            "/api/chat/turns",
            json={"turn_id": "plain-turn", "session": "desktop", "message": "在吗"},
        )

    database = app.state.runtime_context.database
    marked = database.get_chat_turn("marked-turn")
    assert marked is not None
    assert marked["payload"]["agent_stream"] is True
    assert marked["payload"]["agent_skill"] == "system-steward"
    plain = database.get_chat_turn("plain-turn")
    assert plain is not None
    assert "agent_stream" not in plain["payload"]


def test_agent_stream_lease_timeout_errors_and_fallback_completes_turn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Hot reload holds the lane: the stream errors fast, the durable
    fallback later re-runs the agent loop and persists ``agent_events``."""
    monkeypatch.setattr(app_module, "_AGENT_STREAM_LEASE_TIMEOUT_SECONDS", 0.05)
    dialogue = FakeAgentDialogue(_multi_hop_script())
    app = _app(tmp_path, dialogue)
    coordinator = app.state.dialogue_execution_coordinator

    with TestClient(app) as client:
        created = client.post(
            "/api/chat/turns",
            json={
                "turn_id": "reload-turn",
                "session": "desktop",
                "scope": "chat",
                "message": "我订阅了什么？",
                "streaming": True,
            },
        )
        assert created.status_code == 200

        # Pause the lane exactly like the hot-reload handoff does.
        client.portal.call(lambda: coordinator.pause_and_drain(timeout=1.0))
        try:
            response = client.post(
                "/api/chat/agent/stream",
                json={"turn_id": "reload-turn", "session": "desktop", "message": "我订阅了什么？"},
            )
            assert response.status_code == 200
            events = _parse_sse(response.text)
            assert [name for name, _data in events] == ["error"]
            assert "重载配置" in events[0][1]["error"]

            # The loop never ran and the turn was NOT failed: it stays
            # pending for the durable fallback worker (already re-woken).
            row = app.state.runtime_context.database.get_chat_turn("reload-turn")
            assert row is not None
            assert row["status"] == "pending"
            assert dialogue.agent_calls == []
        finally:
            client.portal.call(coordinator.resume)

        # The fallback worker re-runs the agent loop for the streaming turn
        # and persists the full process stream for history replay.
        deadline = time.monotonic() + 5
        row = None
        while time.monotonic() < deadline:
            row = app.state.runtime_context.database.get_chat_turn("reload-turn")
            if row is not None and row["status"] in {"completed", "failed"}:
                break
            time.sleep(0.01)
        assert row is not None
        assert row["status"] == "completed"
        assert row["reply"] == "你还没有订阅任何内容源。"
        persisted = row["payload"]["agent_events"]
        assert [event["type"] for event in persisted] == [
            "thinking",
            "tool_call",
            "tool_result",
            "final",
        ]
        # The fallback path ran the agent loop (not the legacy single hop).
        assert len(dialogue.agent_calls) == 1
        assert dialogue.agent_calls[0]["turn_id"] == "reload-turn"
        assert dialogue.legacy_calls == []


def test_durable_fallback_reruns_agent_loop_for_orphaned_streaming_turn(
    tmp_path: Path,
) -> None:
    """A streaming turn whose client disconnected is completed by the
    durable worker through the agent loop, with ``agent_events`` persisted."""
    dialogue = FakeAgentDialogue(_multi_hop_script())
    app = _app(tmp_path, dialogue)

    with TestClient(app) as client:
        created = client.post(
            "/api/chat/turns",
            json={
                "turn_id": "orphan-turn",
                "session": "desktop",
                "scope": "chat",
                "message": "我订阅了什么？",
                "streaming": True,
            },
        )
        assert created.status_code == 200
        assert created.json()["status"] == "pending"

        # The stream was never consumed (client died). A non-streaming
        # retry of the same turn wakes the durable reply worker.
        retry = client.post(
            "/api/chat/turns",
            json={
                "turn_id": "orphan-turn",
                "session": "desktop",
                "scope": "chat",
                "message": "我订阅了什么？",
            },
        )
        assert retry.status_code == 200

        deadline = time.monotonic() + 5
        row = None
        while time.monotonic() < deadline:
            row = app.state.runtime_context.database.get_chat_turn("orphan-turn")
            if row is not None and row["status"] in {"completed", "failed"}:
                break
            time.sleep(0.01)
        assert row is not None
        assert row["status"] == "completed"
        assert row["reply"] == "你还没有订阅任何内容源。"
        persisted = row["payload"]["agent_events"]
        assert [event["type"] for event in persisted] == [
            "thinking",
            "tool_call",
            "tool_result",
            "final",
        ]
        assert len(dialogue.agent_calls) == 1
        assert dialogue.legacy_calls == []


def _parse_sse_tolerant(body: str) -> list[tuple[str, dict[str, Any]]]:
    """Parse SSE frames while skipping heartbeat comment blocks (``: ping``)."""
    frames = [
        block for block in body.split("\n\n") if block.strip() and not block.strip().startswith(":")
    ]
    return _parse_sse("\n\n".join(frames))


class SlowAgentDialogue(FakeAgentDialogue):
    """Dialogue double with a silent gap between two agent events."""

    def __init__(self, gap_seconds: float) -> None:
        super().__init__([])
        self._gap_seconds = gap_seconds

    async def stream_agent_reply(self, *args: Any, **kwargs: Any) -> Any:
        self.agent_calls.append({"args": args, "kwargs": kwargs})
        yield AgentEvent(type="thinking", step=1, text="先想想")
        await asyncio.sleep(self._gap_seconds)
        yield AgentEvent(type="final", step=2, text="想好了")


def test_agent_stream_emits_heartbeat_during_silent_gap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Silence longer than the heartbeat interval yields ``: ping`` comments.

    The real events must still arrive intact around the heartbeat lines.
    """
    monkeypatch.setattr(app_module, "_SSE_HEARTBEAT_INTERVAL_SECONDS", 0.05)
    app = _app(tmp_path, SlowAgentDialogue(gap_seconds=0.3))

    with TestClient(app) as client:
        response = client.post("/api/chat/agent/stream", json={"message": "你好"})

    assert response.status_code == 200
    assert ": ping" in response.text
    events = _parse_sse_tolerant(response.text)
    assert [event for event, _data in events] == ["thinking", "final", "done"]
    assert events[2][1]["reply"] == "想好了"


def test_chat_agent_ping_endpoint_streams_numbered_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``GET /api/chat/agent/ping`` is a stateless SSE liveness probe."""
    monkeypatch.setattr(app_module, "_SSE_PING_INTERVAL_SECONDS", 0.01)
    app = _app(tmp_path, FakeAgentDialogue([]))

    with TestClient(app) as client:
        response = client.get("/api/chat/agent/ping")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(response.text)
    assert [event for event, _data in events] == ["ping"] * app_module._SSE_PING_EVENT_COUNT
    assert [data["seq"] for _event, data in events] == list(
        range(1, app_module._SSE_PING_EVENT_COUNT + 1)
    )


def test_agent_stream_retry_replays_completed_turn_without_reexecuting(tmp_path: Path) -> None:
    dialogue = FakeAgentDialogue(_multi_hop_script())
    app = _app(tmp_path, dialogue)
    payload = {"turn_id": "retry-turn", "message": "我订阅了什么？", "streaming": True}
    with TestClient(app) as client:
        assert client.post("/api/chat/turns", json=payload).status_code == 200
        first = client.post("/api/chat/agent/stream", json=payload)
        second = client.post("/api/chat/agent/stream", json=payload)
    assert _parse_sse(second.text) == _parse_sse(first.text)
    assert len(dialogue.agent_calls) == 1


def test_agent_stream_rejects_unknown_or_conflicting_turn_identity(tmp_path: Path) -> None:
    dialogue = FakeAgentDialogue(_multi_hop_script())
    app = _app(tmp_path, dialogue)
    with TestClient(app) as client:
        missing = client.post(
            "/api/chat/agent/stream", json={"turn_id": "missing", "message": "你好"}
        )
        assert missing.status_code == 404
        client.post(
            "/api/chat/turns",
            json={"turn_id": "identity-turn", "message": "原始问题", "streaming": True},
        )
        conflict = client.post(
            "/api/chat/agent/stream",
            json={"turn_id": "identity-turn", "message": "另一个问题"},
        )
        assert conflict.status_code == 409
    assert dialogue.agent_calls == []


def test_agent_stream_uses_durable_skill_when_retry_omits_it(tmp_path: Path) -> None:
    dialogue = FakeAgentDialogue([AgentEvent(type="final", text="好的")])
    app = _app(tmp_path, dialogue)
    with TestClient(app) as client:
        client.post(
            "/api/chat/turns",
            json={
                "turn_id": "skill-turn",
                "message": "你好",
                "streaming": True,
                "skill": "system-steward",
            },
        )
        response = client.post(
            "/api/chat/agent/stream", json={"turn_id": "skill-turn", "message": "你好"}
        )
    assert _parse_sse(response.text)[-1][1]["skill"] == "system-steward"
    assert dialogue.agent_calls[0]["skill"].name == "system-steward"


def test_agent_persona_is_frozen_for_stream_and_retry_without_changing_skill(
    tmp_path: Path,
) -> None:
    from openbiliclaw.agent.tools import Tool, ToolRegistry

    dialogue = FakeAgentDialogue([AgentEvent(type="final", text="好的")])
    app = _app(tmp_path, dialogue)
    app.state.runtime_context.agent_tool_registry = ToolRegistry(
        [
            Tool(name="get_profile", description="读画像", handler=lambda _: ""),
            Tool(name="update_config", description="修改配置", handler=lambda _: ""),
        ]
    )
    payload = {
        "turn_id": "styled-turn",
        "message": "你好",
        "streaming": True,
        "session_id": "styled",
    }
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/chat/sessions",
                json={
                    "session_id": "styled",
                    "metadata": {"persona": "warm"},
                },
            ).status_code
            == 200
        )
        first = client.post("/api/chat/turns", json=payload)
        assert first.json()["payload"]["agent_persona"] == "warm"
        assert (
            client.patch("/api/chat/sessions/styled", json={"persona": "concise"}).status_code
            == 200
        )
        # POST retry reuses the accepted turn rather than re-freezing the style.
        assert (
            client.post("/api/chat/turns", json=payload).json()["payload"]["agent_persona"]
            == "warm"
        )
        streamed = client.post("/api/chat/agent/stream", json=payload)
        assert _parse_sse(streamed.text)[-1][0] == "done"
        replayed = client.post("/api/chat/agent/stream", json=payload)
        assert _parse_sse(replayed.text) == _parse_sse(streamed.text)
        assert len(dialogue.agent_calls) == 1
        first_call = dialogue.agent_calls[0]
        assert first_call["persona_id"] == "warm"
        assert first_call["skill"].name == "taste-companion"
        # Ephemeral sends snapshot the currently selected style instead.
        client.post("/api/chat/agent/stream", json={"session_id": "styled", "message": "谢谢"})
        assert dialogue.agent_calls[-1]["persona_id"] == "concise"
        assert dialogue.agent_calls[-1]["skill"] == first_call["skill"]
        assert dialogue.agent_calls[-1]["tools"].names == first_call["tools"].names
        assert "update_config" not in first_call["tools"].names
        next_turn = client.post("/api/chat/turns", json={**payload, "turn_id": "next-turn"})
        assert next_turn.json()["payload"]["agent_persona"] == "concise"


def test_agent_worker_recovery_uses_persisted_persona_after_restart(tmp_path: Path) -> None:
    dialogue = FakeAgentDialogue([AgentEvent(type="final", text="恢复完成")])
    app = _app(tmp_path, dialogue)
    payload = {"turn_id": "orphan-style", "message": "你好", "streaming": True}
    with TestClient(app) as client:
        client.patch("/api/chat/sessions/default", json={"persona": "analytical"})
        created = client.post("/api/chat/turns", json=payload)
        assert created.json()["payload"]["agent_persona"] == "analytical"
        client.patch("/api/chat/sessions/default", json={"persona": "playful"})
    assert dialogue.agent_calls == []
    recovered_dialogue = FakeAgentDialogue([AgentEvent(type="final", text="恢复完成")])
    recovered_app = _app(tmp_path, recovered_dialogue)
    with TestClient(recovered_app) as client:
        assert (
            client.post("/api/chat/turns", json={**payload, "streaming": False}).status_code == 200
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            row = recovered_app.state.runtime_context.database.get_chat_turn("orphan-style")
            if row["status"] != "pending":
                break
            time.sleep(0.01)
        assert row["status"] == "completed"
        assert row["payload"]["agent_persona"] == "analytical"
    assert len(recovered_dialogue.agent_calls) == 1
    assert recovered_dialogue.agent_calls[0]["persona_id"] == "analytical"
    assert recovered_dialogue.legacy_calls == []


def test_old_turn_uses_natural_and_clients_cannot_forge_persona(tmp_path: Path) -> None:
    dialogue = FakeAgentDialogue([AgentEvent(type="final", text="好的")])
    app = _app(tmp_path, dialogue)
    database = app.state.runtime_context.database
    database.create_chat_turn(turn_id="old-style", message="旧消息", payload={"agent_stream": True})
    with TestClient(app) as client:
        client.patch("/api/chat/sessions/default", json={"persona": "warm"})
        client.post("/api/chat/agent/stream", json={"turn_id": "old-style", "message": "旧消息"})
        assert dialogue.agent_calls[0]["persona_id"] == "natural"
        for endpoint in ("/api/chat/turns", "/api/chat/agent/stream"):
            forged = client.post(
                endpoint,
                json={
                    "turn_id": "forged-style",
                    "message": "你好",
                    "streaming": True,
                    "payload": {"agent_persona": "warm"},
                },
            )
            assert forged.status_code == 422
        assert database.get_chat_turn("forged-style") is None
        assert len(dialogue.agent_calls) == 1
        legacy = client.post("/api/chat/turns", json={"message": "旧入口"}).json()
        assert "agent_persona" not in legacy["payload"]
        scoped = client.post(
            "/api/chat/turns",
            json={
                "message": "非普通聊天",
                "scope": "probe",
                "subject_id": "test",
                "streaming": True,
            },
        ).json()
        assert "agent_persona" not in scoped["payload"]


async def test_agent_stream_disconnect_keeps_single_execution_running(tmp_path: Path) -> None:
    from openbiliclaw.api.app import ChatTurnIn

    dialogue = SlowAgentDialogue(gap_seconds=0.05)
    app = _app(tmp_path, dialogue)
    endpoints = {
        getattr(route, "path", ""): route.endpoint
        for route in app.routes
        if "POST" in getattr(route, "methods", ())
    }
    payload = ChatTurnIn(turn_id="disconnect-turn", message="你好", streaming=True)
    await endpoints["/api/chat/turns"](payload)
    response = await endpoints["/api/chat/agent/stream"](payload)
    iterator = response.body_iterator
    first = await anext(iterator)
    assert "thinking" in first
    await iterator.aclose()
    await asyncio.sleep(0.15)
    row = app.state.runtime_context.database.get_chat_turn("disconnect-turn")
    assert row["status"] == "completed"
    assert row["reply"] == "想好了"
    assert len(dialogue.agent_calls) == 1


async def test_concurrent_agent_streams_execute_durable_turn_once(tmp_path: Path) -> None:
    from openbiliclaw.api.app import ChatTurnIn

    dialogue = SlowAgentDialogue(gap_seconds=0.05)
    app = _app(tmp_path, dialogue)
    endpoints = {
        route.path: route.endpoint
        for route in app.routes
        if "POST" in getattr(route, "methods", ())
    }
    payload = ChatTurnIn(turn_id="concurrent-turn", message="你好", streaming=True)
    await endpoints["/api/chat/turns"](payload)

    async def consume() -> list[str]:
        response = await endpoints["/api/chat/agent/stream"](payload)
        return [frame async for frame in response.body_iterator]

    first, second = await asyncio.gather(consume(), consume())
    assert first == second
    assert len(dialogue.agent_calls) == 1


def test_streaming_turn_rejects_invalid_skill_before_persisting(tmp_path: Path) -> None:
    app = _app(tmp_path, FakeAgentDialogue([]))
    with TestClient(app) as client:
        response = client.post(
            "/api/chat/turns",
            json={"turn_id": "bad-skill", "message": "你好", "streaming": True, "skill": "missing"},
        )
        assert response.status_code == 422
    assert app.state.runtime_context.database.get_chat_turn("bad-skill") is None


async def test_agent_stream_preserves_approval_result_during_execution(tmp_path: Path) -> None:
    from openbiliclaw.agent.approvals import ApprovalStore
    from openbiliclaw.api.app import ChatTurnIn

    app = _app(tmp_path, SlowAgentDialogue(gap_seconds=0.05))
    endpoints = {
        route.path: route.endpoint
        for route in app.routes
        if "POST" in getattr(route, "methods", ())
    }
    payload = ChatTurnIn(turn_id="approval-stream", message="你好", streaming=True)
    await endpoints["/api/chat/turns"](payload)
    store = ApprovalStore()
    app.state.runtime_context.chat_approval_store = store
    approval = store.submit(
        tool_name="toggle_source",
        arguments={"id": "test-source", "enabled": False},
        summary="关闭来源",
        reason="用户请求",
        impact="来源停用",
        session="popup",
        session_id="default",
        turn_id=payload.turn_id,
    )
    response = await endpoints["/api/chat/agent/stream"](payload)
    iterator = response.body_iterator
    assert "thinking" in await anext(iterator)
    await endpoints["/api/chat/approvals/{approval_id}/reject"](approval.approval_id, None)
    frames = [frame async for frame in iterator]
    assert "done" in frames[-1]
    row = app.state.runtime_context.database.get_chat_turn(payload.turn_id)
    assert [event["type"] for event in row["payload"]["agent_events"]] == [
        "thinking",
        "approval_result",
        "final",
    ]


def test_agent_stream_applies_scoped_success_effects_once(tmp_path: Path) -> None:
    app = _app(tmp_path, FakeAgentDialogue([AgentEvent(type="final", text="谢谢反馈")]))
    updates: list[dict[str, Any]] = []
    app.state.runtime_context.memory_manager = SimpleNamespace(
        load_cognition_updates=lambda: list(updates),
        save_cognition_updates=lambda items: updates.__setitem__(slice(None), items),
    )
    payload = {
        "turn_id": "delight-stream",
        "message": "这个不错",
        "streaming": True,
        "scope": "delight",
        "subject_id": "video-1",
        "subject_title": "安静科普",
    }
    with TestClient(app) as client:
        assert client.post("/api/chat/turns", json=payload).status_code == 200
        assert client.post("/api/chat/agent/stream", json=payload).status_code == 200
        assert client.post("/api/chat/agent/stream", json=payload).status_code == 200
    assert len(updates) == 1
    assert "安静科普" in updates[0]["summary"]
