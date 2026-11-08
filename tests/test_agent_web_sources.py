"""Conversation -> approved public-Web recipe -> durable discovery candidate."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from functools import partial
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from fastapi.testclient import TestClient

from openbiliclaw.agent.approvals import ApprovalStore
from openbiliclaw.agent.loop import AgentLoop
from openbiliclaw.agent.skill import load_skill_catalog
from openbiliclaw.agent.tasks import AgentTaskRunner
from openbiliclaw.agent.tools.source_tools import build_source_tool_registry
from openbiliclaw.api.app import create_app
from openbiliclaw.discovery.candidate_pipeline import DiscoveryCandidatePipeline
from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.runtime.web_producer import WebDiscoveryProducer
from openbiliclaw.sources.web_adapter import WebSourceAdapter
from openbiliclaw.sources.web_probe import probe_public_web_url
from openbiliclaw.sources.web_public import SafeWebTransport
from openbiliclaw.sources.web_recipe import is_compiled_public_web_recipe
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

FEED_URL = "https://news.example.com/feed.xml"
ARTICLE_URL = "https://news.example.com/posts/first"
RSS = f"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Example journal</title>
<item><title>First article</title><link>{ARTICLE_URL}</link>
<author>Alice</author><description>A public article about astronomy.</description>
<pubDate>Fri, 02 Oct 2026 09:00:49 GMT</pubDate></item>
</channel></rss>""".encode()


async def _public_resolver(_host: str, _port: int) -> Sequence[str]:
    return ["93.184.216.34"]


def _mock_probe_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> Any:
    """Keep real probe/parser/network validation while replacing HTTP only."""
    factory = partial(
        SafeWebTransport,
        transport=httpx.MockTransport(handler),
        resolver=_public_resolver,
        respect_robots=False,
    )
    monkeypatch.setattr(
        "openbiliclaw.agent.tools.source_tools.probe_public_web_url",
        partial(probe_public_web_url, transport_factory=factory),
    )
    return factory


def _database(tmp_path: Path) -> Database:
    database = Database(tmp_path / "web-agent.db")
    database.initialize()
    return database


class _RunningDialogue:
    """Pass the real API's chosen tool subset and approval context into the loop."""

    async def stream_agent_reply(
        self,
        agent_loop: Any,
        message: str,
        *,
        session: str = "",
        turn_id: str = "",
        session_id: str = "",
        tools: Any = None,
        **_kwargs: Any,
    ) -> Any:
        async for event in agent_loop.run(
            system_instruction="Help subscribe to public websites.",
            user_message=message,
            tools=tools,
            approval_context={"session": session, "session_id": session_id, "turn_id": turn_id},
        ):
            yield event

    async def respond(self, _message: str, **_kwargs: Any) -> str:
        return "legacy"


def _approval_app(tmp_path: Path, *, store: ApprovalStore | None = None) -> Any:
    database = _database(tmp_path)
    app = create_app(
        memory_manager=object(),
        database=database,
        soul_engine=object(),
        dialogue=_RunningDialogue(),
    )
    context = app.state.runtime_context
    context.chat_approval_store = store or ApprovalStore()
    context.agent_tool_registry = build_source_tool_registry(database)
    context.agent_loop = object()
    return app


def _parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for block in body.split("\n\n"):
        lines = block.splitlines()
        name = next((line[7:] for line in lines if line.startswith("event: ")), "")
        data = next((line[6:] for line in lines if line.startswith("data: ")), "")
        if name and data:
            events.append((name, json.loads(data)))
    return events


def _wait_for_approval_status(
    client: TestClient, approval_id: str, statuses: set[str]
) -> dict[str, Any]:
    deadline = time.monotonic() + 5
    while True:
        items = client.get("/api/chat/approvals").json()["items"]
        record = next(item for item in items if item["approval_id"] == approval_id)
        if record["status"] in statuses:
            return record
        assert time.monotonic() < deadline, record
        time.sleep(0.01)


class _ProbeThenCreateLLM:
    """Use the observed probe arguments instead of fabricating a recipe."""

    def __init__(self) -> None:
        self.calls = 0
        self.probe_payload: dict[str, Any] = {}

    async def complete_with_native_tools(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        **_kwargs: Any,
    ) -> LLMResponse:
        self.calls += 1
        names = {tool["function"]["name"] for tool in tools}
        assert {"probe_web_source", "create_source"} <= names
        if self.calls % 3 == 1:
            return LLMResponse(
                content="先看看这个网站提供哪些公开订阅入口。",
                tool_calls=[
                    {"id": "probe-web", "name": "probe_web_source", "arguments": {"url": FEED_URL}}
                ],
            )
        if self.calls % 3 == 2:
            self.probe_payload = json.loads(messages[-1]["content"])
            assert self.probe_payload["status"] == "ready"
            arguments = self.probe_payload["candidates"][0]["create_source_arguments"]
            return LLMResponse(
                content="找到了公开 RSS，可加入后续发现。请确认订阅。",
                tool_calls=[{"id": "create-web", "name": "create_source", "arguments": arguments}],
            )
        assert self.calls % 3 == 0
        return LLMResponse(content="已生成订阅确认卡，批准后开始发现。")


def test_chat_probe_approval_and_discovery_form_one_durable_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        assert not request.headers.get("cookie")
        assert not request.headers.get("authorization")
        if str(request.url) == FEED_URL:
            return httpx.Response(200, content=RSS, headers={"Content-Type": "application/rss+xml"})
        assert str(request.url) == ARTICLE_URL
        return httpx.Response(
            200,
            text="<html><body><article>A public article about astronomy.</article></body></html>",
            headers={"Content-Type": "text/html"},
        )

    factory = _mock_probe_transport(monkeypatch, handler)
    store_path = tmp_path / "web-approvals.json"
    store = ApprovalStore(store_path)
    app = _approval_app(tmp_path, store=store)
    context = app.state.runtime_context
    database = context.database
    registry = build_source_tool_registry(database)
    llm = _ProbeThenCreateLLM()
    context.agent_tool_registry = registry
    context.agent_loop = AgentLoop(llm, registry, approval_gate=store)
    turn_id = "web-subscribe-turn"
    message = f"帮我把 {FEED_URL} 加入内容订阅"

    with TestClient(app) as client:
        created = client.post(
            "/api/chat/turns",
            json={
                "turn_id": turn_id,
                "session": "desktop",
                "scope": "chat",
                "message": message,
                "streaming": True,
            },
        )
        assert created.status_code == 200
        response = client.post(
            "/api/chat/agent/stream",
            json={"turn_id": turn_id, "session": "desktop", "message": message},
        )
        assert response.status_code == 200
        events = _parse_sse(response.text)
        assert events[-1][0] == "done", events
        approval = next(data for name, data in events if name == "approval_request")
        approval_id = approval["approval_id"]
        assert approval["tool_name"] == "create_source"
        assert (
            approval["arguments"] == llm.probe_payload["candidates"][0]["create_source_arguments"]
        )
        assert database.get_all_recipes() == []
        assert requests == [FEED_URL]

        # History restoration reads the same durable card without re-running tools.
        replay = client.get(f"/api/chat/turns/{turn_id}")
        assert replay.status_code == 200
        replay_approvals = [
            event
            for event in replay.json()["payload"]["agent_events"]
            if event["type"] == "approval_request"
        ]
        assert [event["approval_id"] for event in replay_approvals] == [approval_id]
        # Retrying the durable SSE request must replay the same approval too.
        streamed_replay = client.post(
            "/api/chat/agent/stream",
            json={"turn_id": turn_id, "session": "desktop", "message": message},
        )
        assert streamed_replay.status_code == 200
        streamed_approvals = [
            data["approval_id"]
            for name, data in _parse_sse(streamed_replay.text)
            if name == "approval_request"
        ]
        assert streamed_approvals == [approval_id]
        assert len(store.list()) == 1
        assert llm.calls == 3
        assert database.get_all_recipes() == []
        reloaded = ApprovalStore(store_path).get(approval_id)
        assert reloaded is not None and reloaded.status == "pending"
        assert reloaded.arguments == approval["arguments"]

        approved = client.post(f"/api/chat/approvals/{approval_id}/approve")
        assert approved.status_code == 200
        settled = _wait_for_approval_status(client, approval_id, {"executed", "failed"})
        assert settled["status"] == "executed", settled
        recipes = database.get_all_recipes()
        assert len(recipes) == 1
        recipe = recipes[0]
        assert is_compiled_public_web_recipe(recipe)
        assert recipe["created_by"] == "agent"
        assert recipe["config"]["entry_url"] == FEED_URL
        assert recipe["config"]["allowed_origins"] == ["https://news.example.com"]
        assert requests == [FEED_URL]  # Creation does not perform another network request.

        again = client.post(f"/api/chat/approvals/{approval_id}/approve")
        assert again.status_code == 200
        assert again.json()["already_executed"] is True
        assert len(database.get_all_recipes()) == 1
        decided_replay = client.post(
            "/api/chat/agent/stream",
            json={"turn_id": turn_id, "session": "desktop", "message": message},
        )
        replayed_results = [
            data for name, data in _parse_sse(decided_replay.text) if name == "approval_result"
        ]
        assert len(replayed_results) == 1
        assert replayed_results[0]["approval_id"] == approval_id
        assert replayed_results[0]["ok"] is True
        assert len(store.list()) == 1
        assert llm.calls == 3
        assert requests == [FEED_URL]

        pipeline = DiscoveryCandidatePipeline(database=database, discovery_engine=SimpleNamespace())
        producer = WebDiscoveryProducer(
            database=database,
            adapter=WebSourceAdapter(llm_service=None, public_transport_factory=factory),
            candidate_pipeline=pipeline,
            candidate_evaluation_owned_by_coordinator=True,
        )
        produced = asyncio.run(producer.produce_if_due(limit=5, force=True))
        assert produced["enqueued"] == 1, produced
        assert database.count_discovery_candidates_by_status()["pending_eval"] == 1
        rows = database.claim_discovery_candidates_for_eval(limit=5)
        assert len(rows) == 1
        assert rows[0]["content_url"] == ARTICLE_URL
        assert rows[0]["title"] == "First article"
        assert rows[0]["source_platform"] == "web"
        assert rows[0]["source_strategy"] == "web-feed"
        raw_payload = json.loads(rows[0]["raw_payload"])
        assert raw_payload["source_recipe_id"] == recipe["id"]
        assert raw_payload["source_site"] == "news.example.com"
        assert database.get_web_source_state(recipe["id"])["status"] == "ok"

        repeated = asyncio.run(producer.produce_if_due(limit=5, force=True))
        assert repeated["enqueued"] == 0

    row = database.get_chat_turn(turn_id)
    assert row is not None
    result_events = [
        event for event in row["payload"]["agent_events"] if event["type"] == "approval_result"
    ]
    assert len(result_events) == 1
    assert result_events[0]["approval_id"] == approval_id
    assert result_events[0]["ok"] is True
    assert len(database.query_profile_ledger(write_point="agent.approval.create_source")) == 1
    persisted = ApprovalStore(store_path).get(approval_id)
    assert persisted is not None and persisted.status == "executed"


async def test_chat_web_probe_no_match_does_not_offer_a_creation_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream secret must not reach chat")

    _mock_probe_transport(monkeypatch, handler)
    database = _database(tmp_path)
    result = await build_source_tool_registry(database).dispatch(
        "probe_web_source", {"url": FEED_URL}
    )
    assert result.ok
    payload = json.loads(result.content)
    assert payload["status"] == "no_match"
    assert payload["candidates"] == []
    assert "create_source_arguments" not in result.content
    assert "upstream secret" not in result.content
    assert database.get_all_recipes() == []


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1/feed.xml", "http://169.254.169.254/latest/meta-data/"]
)
async def test_chat_web_probe_rejects_private_url_before_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(200, content=RSS)

    _mock_probe_transport(monkeypatch, handler)
    database = _database(tmp_path)
    result = await build_source_tool_registry(database).dispatch("probe_web_source", {"url": url})
    assert not result.ok
    assert requests == []
    assert database.get_all_recipes() == []


async def test_background_web_tools_can_probe_but_cannot_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=RSS, headers={"Content-Type": "application/rss+xml"})

    _mock_probe_transport(monkeypatch, handler)
    database = _database(tmp_path)
    runtime = SimpleNamespace(
        llm_service=object(),
        agent_tool_registry=build_source_tool_registry(database),
        skill_catalog=load_skill_catalog(),
    )
    runner = AgentTaskRunner(database, runtime=runtime)
    _loop, registry = runner._resolve_components({"skill": "taste-companion"})
    assert "probe_web_source" in registry
    assert "create_source" not in registry
    assert "toggle_source" not in registry
    probe = await registry.dispatch("probe_web_source", {"url": FEED_URL})
    assert probe.ok
    arguments = json.loads(probe.content)["candidates"][0]["create_source_arguments"]
    create = await registry.dispatch("create_source", arguments)
    assert not create.ok and create.error == "unknown_tool"
    assert database.get_all_recipes() == []


def test_rejecting_web_subscription_never_creates_a_recipe(tmp_path: Path) -> None:
    app = _approval_app(tmp_path)
    context = app.state.runtime_context
    record = context.chat_approval_store.submit(
        tool_name="create_source",
        arguments={"source_type": "web", "name": "Journal", "strategy": "feed", "url": FEED_URL},
        summary="订阅 Journal",
    )
    with TestClient(app) as client:
        rejected = client.post(f"/api/chat/approvals/{record.approval_id}/reject")
        assert rejected.status_code == 200
        assert rejected.json()["approval"]["status"] == "rejected"
        assert client.post(f"/api/chat/approvals/{record.approval_id}/approve").status_code == 409
        assert context.database.get_all_recipes() == []


def test_approved_web_recipe_still_requires_compiler_validation(tmp_path: Path) -> None:
    app = _approval_app(tmp_path)
    context = app.state.runtime_context
    record = context.chat_approval_store.submit(
        tool_name="create_source",
        arguments={
            "source_type": "web",
            "name": "Too many pages",
            "strategy": "feed",
            "url": FEED_URL,
            "max_items": 500,
        },
        summary="订阅 Journal",
    )
    with TestClient(app) as client:
        assert client.post(f"/api/chat/approvals/{record.approval_id}/approve").status_code == 200
        settled = _wait_for_approval_status(client, record.approval_id, {"executed", "failed"})
        assert settled["status"] == "failed"
        assert "max_items" in settled["result"]
        assert context.database.get_all_recipes() == []
        ledger = context.database.query_profile_ledger(write_point="agent.approval.create_source")
        assert len(ledger) == 1 and ledger[0]["outcome"] == "failed"


async def test_repeated_web_creation_preserves_disabled_subscription(tmp_path: Path) -> None:
    database = _database(tmp_path)
    registry = build_source_tool_registry(database)
    arguments = {"source_type": "web", "name": "Journal", "strategy": "feed", "url": FEED_URL}
    assert (await registry.dispatch("create_source", arguments)).ok
    original = database.get_all_recipes()[0]
    assert database.update_recipe(original["id"], enabled=False)
    # A retry may have a new display name; it must not undo the existing disabled state.
    repeated = await registry.dispatch("create_source", {**arguments, "name": "Renamed by retry"})
    assert repeated.ok
    rows = database.get_all_recipes()
    assert len(rows) == 1
    assert rows[0]["id"] == original["id"]
    assert rows[0]["name"] == original["name"]
    assert not rows[0]["enabled"]
    assert "toggle_source" in repeated.content


@pytest.mark.parametrize("strategy, expected_status", [("feed", "ready"), ("sitemap", "no_match")])
async def test_probe_honors_requested_strategy_without_inventing_a_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, strategy: str, expected_status: str
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=RSS, headers={"Content-Type": "application/rss+xml"})

    _mock_probe_transport(monkeypatch, handler)
    database = _database(tmp_path)
    result = await build_source_tool_registry(database).dispatch(
        "probe_web_source", {"url": FEED_URL, "strategy": strategy}
    )
    assert result.ok
    payload = json.loads(result.content)
    assert payload["status"] == expected_status
    assert payload["available_strategies"] == ["feed"]
    if expected_status == "ready":
        assert payload["candidates"][0]["create_source_arguments"]["strategy"] == strategy
    else:
        assert payload["candidates"] == []
    assert database.get_all_recipes() == []


async def test_long_feed_samples_keep_complete_executable_creation_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    long_article_url = f"https://news.example.com/posts/{'a' * 5500}"
    feed = RSS.replace(ARTICLE_URL.encode(), long_article_url.encode())

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=feed, headers={"Content-Type": "application/rss+xml"})

    _mock_probe_transport(monkeypatch, handler)
    database = _database(tmp_path)
    registry = build_source_tool_registry(database)
    probe = await registry.dispatch("probe_web_source", {"url": FEED_URL})
    assert probe.ok
    assert len(probe.content) <= 4000  # The default AgentLoop result budget must not cut the JSON.
    payload = json.loads(probe.content)
    assert payload["status"] == "ready"
    arguments = payload["candidates"][0]["create_source_arguments"]
    assert arguments["url"] == FEED_URL
    created = await registry.dispatch("create_source", arguments)
    assert created.ok, created.content
    recipes = database.get_all_recipes()
    assert len(recipes) == 1
    assert is_compiled_public_web_recipe(recipes[0])


@pytest.mark.parametrize("budget, expected_ok", [(200, False), (900, True)])
async def test_web_probe_respects_custom_agent_result_budget_without_cutting_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, budget: int, expected_ok: bool
) -> None:
    # This sample fits the default budget but cannot fit a 900-character tool response.
    article_url = f"https://news.example.com/posts/{'a' * 1500}"
    feed = RSS.replace(ARTICLE_URL.encode(), article_url.encode())

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=feed, headers={"Content-Type": "application/rss+xml"})

    _mock_probe_transport(monkeypatch, handler)
    database = _database(tmp_path)
    config = SimpleNamespace(agent=SimpleNamespace(tool_result_max_chars=budget))
    registry = build_source_tool_registry(database, config=config)
    model_observations: list[str] = []

    async def complete(*, messages: list[dict[str, Any]], **_kwargs: Any) -> LLMResponse:
        if messages[-1]["role"] == "user":
            return LLMResponse(
                content="检查公开订阅入口。",
                tool_calls=[
                    {
                        "id": "probe-budget",
                        "name": "probe_web_source",
                        "arguments": {"url": FEED_URL},
                    }
                ],
            )
        model_observations.append(messages[-1]["content"])
        return LLMResponse(content="已检查订阅入口。")

    loop = AgentLoop(
        SimpleNamespace(complete_with_native_tools=complete),
        registry,
        tool_result_max_chars=budget,
    )
    events = [
        event
        async for event in loop.run(system_instruction="探测公开订阅。", user_message=FEED_URL)
    ]
    result = next(event for event in events if event.type == "tool_result")
    assert result.ok is expected_ok
    assert result.truncated is False
    assert model_observations == [result.text]
    assert len(result.text) <= budget
    assert "结果已截断" not in result.text
    assert database.get_all_recipes() == []
    if not expected_ok:
        assert "展示上限" in result.text
        assert "create_source_arguments" not in result.text
    else:
        payload = json.loads(result.text)
        candidate = payload["candidates"][0]
        assert candidate["sample_items"] == []
        arguments = candidate["create_source_arguments"]
        assert arguments["url"] == FEED_URL
        created = await registry.dispatch("create_source", arguments)
        assert created.ok, created.content
        assert is_compiled_public_web_recipe(database.get_all_recipes()[0])


@pytest.mark.parametrize("worker_first", [True, False])
def test_poll_and_stream_competing_for_one_turn_create_one_web_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worker_first: bool
) -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(200, content=RSS, headers={"Content-Type": "application/rss+xml"})

    _mock_probe_transport(monkeypatch, handler)
    app = _approval_app(tmp_path)
    context = app.state.runtime_context
    coordinator = app.state.dialogue_execution_coordinator
    original_lease = coordinator.lease
    waiter_entered = threading.Event()
    first_call_started = threading.Event()
    release_first_call = threading.Event()
    lease_calls = 0

    @asynccontextmanager
    async def observed_lease(*, timeout: float | None = None) -> Any:
        nonlocal lease_calls
        lease_calls += 1
        if lease_calls == 2:
            waiter_entered.set()
        async with original_lease(timeout=timeout):
            yield

    monkeypatch.setattr(coordinator, "lease", observed_lease)

    class BlockingLLM(_ProbeThenCreateLLM):
        async def complete_with_native_tools(self, **kwargs: Any) -> LLMResponse:
            if self.calls == 0:
                first_call_started.set()
                while not release_first_call.is_set():
                    await asyncio.sleep(0.005)
            return await super().complete_with_native_tools(**kwargs)

    llm = BlockingLLM()
    context.agent_loop = AgentLoop(
        llm, context.agent_tool_registry, approval_gate=context.chat_approval_store
    )
    turn_id = "poll-races-agent-stream"
    message = f"订阅 {FEED_URL}"
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as executor:
        created = client.post(
            "/api/chat/turns",
            json={"turn_id": turn_id, "session": "popup", "message": message, "streaming": True},
        )
        assert created.status_code == 200
        request = {"turn_id": turn_id, "session": "popup", "message": message}
        try:
            if worker_first:
                assert client.get(f"/api/chat/turns/{turn_id}").json()["status"] == "pending"
                assert first_call_started.wait(timeout=5)
                streamed = executor.submit(client.post, "/api/chat/agent/stream", json=request)
            else:
                streamed = executor.submit(client.post, "/api/chat/agent/stream", json=request)
                assert first_call_started.wait(timeout=5)
                assert client.get(f"/api/chat/turns/{turn_id}").json()["status"] == "pending"
            assert waiter_entered.wait(timeout=5)
        finally:
            release_first_call.set()
        response = streamed.result(timeout=5)
        client.portal.call(lambda: app.state.chat_reply_scheduler.wait_idle(timeout=5))
        assert response.status_code == 200
        events = _parse_sse(response.text)
        assert events[-1][0] == "done", events
        approvals = context.chat_approval_store.list()
        assert len(approvals) == 1
        assert [data["approval_id"] for name, data in events if name == "approval_request"] == [
            approvals[0].approval_id
        ]
        assert llm.calls == 3
        assert requests == [FEED_URL]
        assert context.database.get_all_recipes() == []
        turn = client.get(f"/api/chat/turns/{turn_id}").json()
        assert turn["status"] == "completed"
        persisted_approvals = [
            event["approval_id"]
            for event in turn["payload"]["agent_events"]
            if event["type"] == "approval_request"
        ]
        assert persisted_approvals == [approvals[0].approval_id]


def test_failed_durable_agent_turn_replays_error_without_running_tools(tmp_path: Path) -> None:
    app = _approval_app(tmp_path)
    context = app.state.runtime_context
    llm = _ProbeThenCreateLLM()
    context.agent_loop = AgentLoop(
        llm, context.agent_tool_registry, approval_gate=context.chat_approval_store
    )
    with TestClient(app) as client:
        turn_id = "failed-web-turn"
        client.post(
            "/api/chat/turns",
            json={"turn_id": turn_id, "message": "订阅网站", "streaming": True},
        )
        context.database.store_chat_turn_agent_events(
            turn_id, events=[{"type": "thinking", "text": "检查网站入口。", "step": 1}]
        )
        context.database.fail_chat_turn(turn_id, error="当前服务不可用，请稍后重新发送。")
        response = client.post(
            "/api/chat/agent/stream", json={"turn_id": turn_id, "message": "订阅网站"}
        )
        events = _parse_sse(response.text)
        assert [name for name, _data in events] == ["thinking", "error"]
        assert events[-1][1]["error"] == "当前服务不可用，请稍后重新发送。"
        assert llm.calls == 0
        assert context.chat_approval_store.list() == []


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_approval_decided_before_final_reply_keeps_its_result_in_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, decision: str
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=RSS, headers={"Content-Type": "application/rss+xml"})

    _mock_probe_transport(monkeypatch, handler)
    app = _approval_app(tmp_path)
    context = app.state.runtime_context
    final_hop_started = threading.Event()
    release_final_hop = threading.Event()

    class FinalHopBlockingLLM(_ProbeThenCreateLLM):
        async def complete_with_native_tools(self, **kwargs: Any) -> LLMResponse:
            if self.calls == 2:
                final_hop_started.set()
                while not release_final_hop.is_set():
                    await asyncio.sleep(0.005)
            return await super().complete_with_native_tools(**kwargs)

    llm = FinalHopBlockingLLM()
    context.agent_loop = AgentLoop(
        llm, context.agent_tool_registry, approval_gate=context.chat_approval_store
    )
    turn_id = "approve-before-agent-final"
    request = {"turn_id": turn_id, "message": f"订阅 {FEED_URL}"}
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as executor:
        assert (
            client.post("/api/chat/turns", json={**request, "streaming": True}).status_code == 200
        )
        streamed = executor.submit(client.post, "/api/chat/agent/stream", json=request)
        try:
            assert final_hop_started.wait(timeout=5)
            pending = client.get("/api/chat/approvals").json()["items"]
            assert len(pending) == 1
            approval_id = pending[0]["approval_id"]
            decided = client.post(f"/api/chat/approvals/{approval_id}/{decision}")
            assert decided.status_code == 200
            expected_status = "executed" if decision == "approve" else "rejected"
            settled = _wait_for_approval_status(client, approval_id, {expected_status, "failed"})
            assert settled["status"] == expected_status
            pending_row = context.database.get_chat_turn(turn_id)
            assert pending_row["status"] == "pending"
            early_results = [
                event
                for event in pending_row["payload"]["agent_events"]
                if event["type"] == "approval_result"
            ]
            assert len(early_results) == 1
        finally:
            release_final_hop.set()
        assert streamed.result(timeout=5).status_code == 200
        final_row = context.database.get_chat_turn(turn_id)
        assert final_row["status"] == "completed"
        final_results = [
            event
            for event in final_row["payload"]["agent_events"]
            if event["type"] == "approval_result"
        ]
        assert final_results == early_results
        replay = client.post("/api/chat/agent/stream", json=request)
        replay_results = [
            data for name, data in _parse_sse(replay.text) if name == "approval_result"
        ]
        assert replay_results == early_results
        assert llm.calls == 3
        assert len(context.chat_approval_store.list()) == 1
