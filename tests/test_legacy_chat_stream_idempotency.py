"""Legacy SSE retries share the durable worker's once-only execution lease."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.api.app import ChatTurnIn, create_app
from openbiliclaw.llm.base import LLMResponse, LLMStreamChunk
from openbiliclaw.soul.dialogue import DialogueLearningMode, SocraticDialogue
from openbiliclaw.soul.dialogue_learn_queue import ANCHOR_NOT_APPLICABLE, AnchorNotApplicable
from openbiliclaw.sources.link_ingest import IngestedLink, LinkIngestResult
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


class RecordingIngestor:
    def __init__(self) -> None:
        self.shares: list[str] = []

    async def ingest(self, message: str) -> LinkIngestResult:
        self.shares.append(message)
        return LinkIngestResult(
            links=[
                IngestedLink(
                    original_url="https://example.com",
                    resolved_url="https://example.com",
                    platform="web",
                    content_id="example",
                    title="Example Domain",
                    status="ok",
                )
            ],
            prompt_block="Example Domain",
        )


class RecordingLearningQueue:
    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []

    def peek_anchor(self, **kwargs: Any) -> AnchorNotApplicable:
        return ANCHOR_NOT_APPLICABLE

    def submit(self, kind: Any, payload: dict[str, object], **kwargs: Any) -> object:
        self.payloads.append(payload)
        return object()


class GatedService:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def complete_socratic_dialogue(self, **kwargs: Any) -> LLMResponse:
        self.started.set()
        await self.release.wait()
        return LLMResponse(content="已读链接")

    async def stream_socratic_dialogue(self, **kwargs: Any) -> AsyncIterator[LLMStreamChunk]:
        self.started.set()
        yield LLMStreamChunk(delta="已读")
        await self.release.wait()
        yield LLMStreamChunk(delta="链接")
        yield LLMStreamChunk(response=LLMResponse(content="已读链接"))


def _app(tmp_path: Path) -> tuple[Any, GatedService, RecordingIngestor, RecordingLearningQueue]:
    service = GatedService()
    ingestor = RecordingIngestor()
    queue = RecordingLearningQueue()
    dialogue = SocraticDialogue(
        llm=None,
        soul_engine=object(),
        llm_service=service,
        learning_mode=DialogueLearningMode.QUEUED,
        settlement_queue=queue,  # type: ignore[arg-type]
        link_ingestor=ingestor,  # type: ignore[arg-type]
    )
    database = Database(tmp_path / "openbiliclaw.db")
    database.initialize()
    app = create_app(
        memory_manager=object(), database=database, soul_engine=object(), dialogue=dialogue
    )
    return app, service, ingestor, queue


def _endpoints(app: Any) -> dict[tuple[str, str], Any]:
    return {
        (method, route.path): route.endpoint
        for route in app.routes
        for method in getattr(route, "methods", ())
    }


@pytest.mark.parametrize("first_owner", ["worker", "stream"])
async def test_legacy_stream_and_polled_worker_ingest_and_learn_once(
    tmp_path: Path, first_owner: str
) -> None:
    app, service, ingestor, queue = _app(tmp_path)
    endpoints = _endpoints(app)
    payload = ChatTurnIn(turn_id="shared-link", message="看看 https://example.com", streaming=True)
    await endpoints["POST", "/api/chat/turns"](payload)
    # Capture the pending request before its competing owner completes.
    response = await endpoints["POST", "/api/chat/stream"](payload)

    async def consume() -> list[str]:
        return [str(frame) async for frame in response.body_iterator]

    scheduler = app.state.chat_reply_scheduler
    try:
        if first_owner == "worker":
            await endpoints["GET", "/api/chat/turns/{turn_id}"](payload.turn_id)
            await asyncio.wait_for(service.started.wait(), timeout=2)
            consumer = asyncio.create_task(consume())
        else:
            consumer = asyncio.create_task(consume())
            await asyncio.wait_for(service.started.wait(), timeout=2)
            await endpoints["GET", "/api/chat/turns/{turn_id}"](payload.turn_id)
        service.release.set()
        frames = await asyncio.wait_for(consumer, timeout=2)
        await scheduler.wait_idle(timeout=2)

        assert ingestor.shares == [payload.message]
        assert [item["user_message"] for item in queue.payloads] == [payload.message]
        assert '"reply": "已读链接"' in frames[-1]
        row = app.state.runtime_context.database.get_chat_turn(payload.turn_id)
        assert row["status"] == "completed"
        assert row["reply"] == "已读链接"
    finally:
        await scheduler.close()


async def test_legacy_stream_retry_replays_without_ingesting_or_learning_again(
    tmp_path: Path,
) -> None:
    app, service, ingestor, queue = _app(tmp_path)
    endpoints = _endpoints(app)
    payload = ChatTurnIn(turn_id="retried-link", message="看看 https://example.com", streaming=True)
    await endpoints["POST", "/api/chat/turns"](payload)
    service.release.set()
    replies = []
    for _ in range(2):
        response = await endpoints["POST", "/api/chat/stream"](payload)
        replies.append([str(frame) async for frame in response.body_iterator][-1])

    assert replies[0] == replies[1]
    assert ingestor.shares == [payload.message]
    assert [item["user_message"] for item in queue.payloads] == [payload.message]


async def test_concurrent_legacy_streams_share_one_generation(tmp_path: Path) -> None:
    app, service, ingestor, queue = _app(tmp_path)
    endpoints = _endpoints(app)
    payload = ChatTurnIn(
        turn_id="concurrent-link", message="看看 https://example.com", streaming=True
    )
    await endpoints["POST", "/api/chat/turns"](payload)

    async def consume() -> str:
        response = await endpoints["POST", "/api/chat/stream"](payload)
        return [str(frame) async for frame in response.body_iterator][-1]

    first = asyncio.create_task(consume())
    await asyncio.wait_for(service.started.wait(), timeout=2)
    second = asyncio.create_task(consume())
    service.release.set()
    first_reply, second_reply = await asyncio.wait_for(asyncio.gather(first, second), timeout=2)

    assert first_reply == second_reply
    assert ingestor.shares == [payload.message]
    assert len(queue.payloads) == 1


async def test_failed_legacy_turn_replays_error_without_side_effects(tmp_path: Path) -> None:
    app, _service, ingestor, queue = _app(tmp_path)
    endpoints = _endpoints(app)
    payload = ChatTurnIn(turn_id="failed-link", message="看看 https://example.com", streaming=True)
    await endpoints["POST", "/api/chat/turns"](payload)
    database = app.state.runtime_context.database
    database.fail_chat_turn(payload.turn_id, error="本轮已失败")

    response = await endpoints["POST", "/api/chat/stream"](payload)
    frames = [str(frame) async for frame in response.body_iterator]

    assert '"reply": "本轮已失败"' in frames[-1]
    assert database.get_chat_turn(payload.turn_id)["status"] == "failed"
    assert ingestor.shares == []
    assert queue.payloads == []


@pytest.mark.parametrize("conflict", ["missing", "message", "session_id"])
async def test_legacy_stream_rejects_unknown_or_conflicting_turn_identity(
    tmp_path: Path, conflict: str
) -> None:
    from fastapi import HTTPException

    app, _service, ingestor, queue = _app(tmp_path)
    endpoints = _endpoints(app)
    payload = ChatTurnIn(
        turn_id="identity-link", message="看看 https://example.com", streaming=True
    )
    if conflict != "missing":
        await endpoints["POST", "/api/chat/turns"](payload)
        payload = payload.model_copy(update={conflict: "different"})

    with pytest.raises(HTTPException) as error:
        await endpoints["POST", "/api/chat/stream"](payload)

    assert error.value.status_code == (404 if conflict == "missing" else 409)
    assert ingestor.shares == []
    assert queue.payloads == []


@pytest.mark.parametrize("durable", [False, True])
async def test_legacy_stream_admission_failure_keeps_sse_envelope_and_pending_turn(
    tmp_path: Path, durable: bool
) -> None:
    app, _service, ingestor, queue = _app(tmp_path)
    endpoints = _endpoints(app)
    payload = ChatTurnIn(
        turn_id="admission-link" if durable else "",
        message="看看 https://example.com",
        streaming=True,
    )
    if durable:
        await endpoints["POST", "/api/chat/turns"](payload)
    app.state.runtime_context.dialogue = None

    response = await endpoints["POST", "/api/chat/stream"](payload)
    frames = [str(frame) async for frame in response.body_iterator]

    assert "event: content" in frames[-2]
    assert "event: done" in frames[-1]
    assert "Dialogue service is not configured" not in "".join(frames)
    assert ingestor.shares == []
    assert queue.payloads == []
    if durable:
        row = app.state.runtime_context.database.get_chat_turn(payload.turn_id)
        assert row["status"] == "pending"
        assert row["reply"] == ""
