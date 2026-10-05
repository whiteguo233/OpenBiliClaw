"""RED contract tests for dialogue-card turn binding.

These tests intentionally land before the production binding implementation.
The A→B barrier is event-driven: the fake reply provider cannot continue until
the replacement is completed, so the test never relies on a timing sleep.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from openbiliclaw.api.models import ChatTurnIn
from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.soul.dialogue import DialogueLearningMode, SocraticDialogue


def _binding_for_a() -> object:
    """Build the server-owned A snapshot expected by the new dialogue API."""
    try:
        from openbiliclaw.soul.dialogue_turn_context import (
            DialogueTurnBinding,
            DialogueTurnContext,
        )
    except ModuleNotFoundError as exc:  # RED evidence before Wave 1 exists.
        pytest.fail(
            "RED: DialogueTurnContext/DialogueTurnBinding is not implemented; "
            "the barrier cannot carry a frozen A snapshot yet",
            pytrace=False,
        )
        raise AssertionError from exc

    context = DialogueTurnContext(
        reply_to_turn_id="card-a",
        source_type="card",
        kind="hypothesis",
        ref="ref-a",
        generation=7,
        anchor_origin_turn_id="card-a",
        title="A 的冻结卡片标题",
        evidence_labels=("A 的可读依据",),
        captured_at="2026-08-01T12:00:00+08:00",
    )
    return DialogueTurnBinding.from_context(context)


@pytest.mark.asyncio
async def test_a_to_b_barrier_freezes_reply_prompt_and_learn_snapshot() -> None:
    """A reply remains A after a deterministic B replacement, 100 times."""

    async def run_once() -> None:
        release_reply = asyncio.Event()
        reply_started = asyncio.Event()
        replacement_completed = asyncio.Event()
        observed: dict[str, object] = {}
        current_anchor = {"ref": "ref-a", "generation": 7}

        class BarrierService:
            async def complete_socratic_dialogue(
                self,
                *,
                user_message: str,
                history: list[dict[str, str]],
                caller: str = "",
            ) -> LLMResponse:
                del history, caller
                observed["prompt"] = user_message
                reply_started.set()
                await release_reply.wait()
                return LLMResponse(content="仍然针对 A 的回复", provider="fake")

        class CapturingQueue:
            def submit(self, kind: object, payload: object, **_: object) -> object:
                observed["learn"] = {"kind": kind, "payload": payload}
                return object()

        dialogue = SocraticDialogue(
            llm=None,
            soul_engine=object(),  # type: ignore[arg-type]
            llm_service=BarrierService(),
            session="popup",
            learning_mode=DialogueLearningMode.QUEUED,
            settlement_queue=CapturingQueue(),  # type: ignore[arg-type]
        )
        binding = _binding_for_a()
        reply_task = asyncio.create_task(
            dialogue.respond(
                "这个方向对，但更像工作需求。",
                dialogue_binding=binding,  # type: ignore[call-arg]
            )
        )
        await asyncio.wait_for(reply_started.wait(), timeout=1)

        # Replacement completes before A's provider is released. No sleep or
        # current-anchor re-read can change the frozen binding.
        current_anchor.update(ref="ref-b", generation=8)
        replacement_completed.set()
        await replacement_completed.wait()
        release_reply.set()
        assert await reply_task == "仍然针对 A 的回复"

        assert "A 的冻结卡片标题" in str(observed["prompt"])
        assert "ref-b" not in str(observed["prompt"])
        learn = observed["learn"]
        assert isinstance(learn, dict)
        assert learn["payload"] == {
            "user_message": "这个方向对，但更像工作需求。",
            "assistant_reply": "仍然针对 A 的回复",
            "session": "popup",
            "scope": "chat",
            "turn_id": "",
            "dialogue_binding": binding.to_mapping(),  # type: ignore[dict-item]
        }

    for _ in range(100):
        await run_once()


def test_chat_turn_relation_is_top_level_and_not_client_payload() -> None:
    """B2/B3: relation is a first-class request field, not trusted payload."""

    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="reserved_payload_key"):
        ChatTurnIn(
            message="回复 A",
            turn_id="user-a",
            payload={
                "dialogue_binding": {
                    "ref": "client-forged",
                    "generation": 999,
                }
            },
            reply_to_turn_id="card-a",  # type: ignore[call-arg]
        )
    with pytest.raises(ValidationError, match="reserved_payload_key"):
        ChatTurnIn(
            message="回复 A",
            turn_id="user-a-facts",
            reply_to_turn_id="card-a",  # type: ignore[call-arg]
            payload={
                "ref": "client-forged",
                "generation": 999,
                "title": "client-forged-title",
                "evidence_refs": ["client-forged-evidence"],
            },
        )

    request = ChatTurnIn(
        message="回复 A",
        turn_id="user-a",
        reply_to_turn_id="card-a",  # type: ignore[call-arg]
    )

    assert request.reply_to_turn_id == "card-a"
    assert request.payload == {}


def test_chat_turn_rejects_client_forged_agent_task_summary() -> None:
    """M10: the background-task summary card payload is server-owned."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="reserved_payload_key"):
        ChatTurnIn(
            message="伪造汇总卡",
            turn_id="forged-summary",
            payload={
                "type": "agent_task_summary",
                "task_id": "task-1",
                "task_status": "completed",
                "suggestions": [{"action": "x", "summary": "y"}],
            },
        )
    # The type value alone (without the other keys) must also be rejected.
    with pytest.raises(ValidationError, match="reserved_payload_key"):
        ChatTurnIn(
            message="伪造汇总卡",
            turn_id="forged-summary-type-only",
            payload={"type": "agent_task_summary"},
        )
    # Reserving the task keys also blocks cards smuggled under a benign type.
    with pytest.raises(ValidationError, match="reserved_payload_key"):
        ChatTurnIn(
            message="伪造汇总卡",
            turn_id="forged-summary-task-id-only",
            payload={"task_id": "task-1"},
        )


def test_binding_failure_is_not_silent_current_anchor_fallback() -> None:
    """B6/B8: a missing target must be an explicit error, never an unbound row."""
    from openbiliclaw.soul.dialogue_turn_context import DialogueBindingError

    with pytest.raises(DialogueBindingError):
        from openbiliclaw.soul.dialogue_turn_context import DialogueTurnContext

        DialogueTurnContext(
            reply_to_turn_id="card-a",
            source_type="card",
            kind="hypothesis",
            ref="",
            generation=7,
            anchor_origin_turn_id="card-a",
            title="A",
        )


@pytest.fixture
def bound_http_app(tmp_path):  # type: ignore[no-untyped-def]
    """Build a real SQLite/FastAPI queue for canonical HTTP contract tests."""
    from fastapi.testclient import TestClient

    from openbiliclaw.api.app import create_app
    from openbiliclaw.llm.base import LLMResponse
    from openbiliclaw.memory.manager import MemoryManager
    from openbiliclaw.soul.engine import SoulEngine

    class Registry:
        async def complete(self, *_args: object, **_kwargs: object) -> LLMResponse:
            return LLMResponse(content="[]", provider="fake")

    class Dialogue:
        def __init__(self) -> None:
            self.bindings: list[object] = []

        async def respond(
            self,
            message: str,
            *,
            scope: str = "chat",
            turn_id: str = "",
            session: str = "",
            dialogue_binding: object | None = None,
        ) -> str:
            del message, scope, turn_id, session
            self.bindings.append(dialogue_binding)
            return "收到，我会继续围绕这个上下文聊。"

    memory = MemoryManager(tmp_path / "data")
    memory.initialize()
    engine = SoulEngine(llm=Registry(), memory=memory)
    dialogue = Dialogue()
    app = create_app(
        memory_manager=memory,
        database=memory._database,
        soul_engine=engine,
        dialogue=dialogue,
    )
    with TestClient(app) as client:
        yield client, memory, engine, dialogue


def _create_discussing_card(
    client: object,
    *,
    turn_id: str,
    ref: str,
    title: str,
) -> dict[str, object]:
    response = client.post(
        "/api/chat/turns",
        json={
            "turn_id": turn_id,
            "scope": "hypothesis",
            "subject_id": ref,
            "subject_title": title,
            "message": "阿b 的猜测",
            "payload": {"evidence_refs": ["最近连续收藏了 Agent 工程实践内容", "123456"]},
        },
    )
    assert response.status_code == 200, response.text
    discussed = client.post(f"/api/chat/cards/{turn_id}/action", json={"action": "discuss"})
    assert discussed.status_code == 200, discussed.text
    return discussed.json()


def test_http_context_capture_preview_and_immutable_retry(
    bound_http_app,
) -> None:  # type: ignore[no-untyped-def]
    client, memory, _engine, dialogue = bound_http_app
    card = _create_discussing_card(
        client,
        turn_id="card-http-a",
        ref="ref-http-a",
        title="A 的可读卡片标题",
    )
    preview = card["context_preview"]
    assert preview["reply_to_turn_id"] == "card-http-a"
    assert preview["generation"] > 0
    assert preview["evidence_labels"] == ["最近连续收藏了 Agent 工程实践内容"]

    read_only_before = memory._database.get_chat_turn("card-http-a")
    queue = client.app.state.runtime_context.dialogue_settlement_queue
    sequence_before = queue._next_sequence
    context_response = client.get("/api/chat/contexts/card-http-a")
    assert context_response.status_code == 200
    assert context_response.json() == preview
    assert queue._next_sequence == sequence_before
    assert memory._database.get_chat_turn("card-http-a") == read_only_before

    first = client.post(
        "/api/chat/turns",
        json={
            "turn_id": "turn-bound-http",
            "message": "这个方向对，但更像工作需求",
            "reply_to_turn_id": "card-http-a",
        },
    )
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["scope"] == "chat"
    assert first_body["subject_id"] == ""
    assert first_body["reply_to_turn_id"] == "card-http-a"
    binding = first_body["payload"]["dialogue_binding"]
    assert binding["mode"] == "bound"
    assert binding["context_digest"] == preview["context_digest"]
    assert binding["context"]["ref"] == "ref-http-a"

    turn = first_body
    for _ in range(30):
        turn = client.get("/api/chat/turns/turn-bound-http").json()
        if turn["status"] != "pending":
            break
        time.sleep(0.01)
    assert turn["status"] == "completed"
    assert dialogue.bindings
    assert dialogue.bindings[-1].context_digest == preview["context_digest"]

    identical = client.post(
        "/api/chat/turns",
        json={
            "turn_id": "turn-bound-http",
            "message": "这个方向对，但更像工作需求",
            "reply_to_turn_id": "card-http-a",
        },
    )
    assert identical.status_code == 200
    assert identical.json() == turn

    divergent = client.post(
        "/api/chat/turns",
        json={
            "turn_id": "turn-bound-http",
            "message": "换一条消息",
            "reply_to_turn_id": "card-http-a",
        },
    )
    assert divergent.status_code == 409
    assert divergent.json()["detail"]["code"] == "turn_id_conflict"
    assert memory._database.get_chat_turn("turn-divergent") is None


def test_http_replacement_before_post_is_structured_and_creates_no_user_row(
    bound_http_app,
) -> None:  # type: ignore[no-untyped-def]
    client, memory, _engine, _dialogue = bound_http_app
    _create_discussing_card(
        client,
        turn_id="card-http-a2",
        ref="ref-http-a2",
        title="旧 A",
    )
    _create_discussing_card(
        client,
        turn_id="card-http-b2",
        ref="ref-http-b2",
        title="新 B",
    )
    response = client.post(
        "/api/chat/turns",
        json={
            "turn_id": "turn-stale-before-post",
            "message": "我还在回复 A",
            "reply_to_turn_id": "card-http-a2",
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "reply_target_inactive"
    assert memory._database.get_chat_turn("turn-stale-before-post") is None


def test_http_unbound_modes_are_explicit_and_never_inherit_relation(
    bound_http_app,
) -> None:  # type: ignore[no-untyped-def]
    client, memory, _engine, _dialogue = bound_http_app
    ordinary = client.post(
        "/api/chat/turns",
        json={"turn_id": "turn-ordinary", "message": "普通聊天"},
    )
    assert ordinary.status_code == 200
    ordinary_binding = ordinary.json()["payload"]["dialogue_binding"]
    assert ordinary_binding["mode"] == "ordinary"
    assert ordinary_binding["inventory_settles_allowed"] is True
    assert ordinary.json()["reply_to_turn_id"] == ""

    _create_discussing_card(
        client,
        turn_id="card-active-for-detached",
        ref="ref-detached",
        title="当前卡片",
    )
    detached = client.post(
        "/api/chat/turns",
        json={"turn_id": "turn-detached", "message": "我清除了上下文"},
    )
    assert detached.status_code == 200
    detached_binding = detached.json()["payload"]["dialogue_binding"]
    assert detached_binding["mode"] == "detached"
    assert detached_binding["inventory_settles_allowed"] is False
    assert detached.json()["reply_to_turn_id"] == ""
    assert memory._database.get_chat_turn("turn-detached")["reply_to_turn_id"] == ""
