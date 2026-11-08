"""Parallel grounding through the maintained configuration and provider factory."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from openbiliclaw.config import DiscoveryConfig, load_config
from openbiliclaw.discovery.inspiration import ExaPreviewItem
from openbiliclaw.discovery.inspiration_provider import (
    FallbackInspirationSearchProvider,
    ParallelInspirationProvider,
    build_inspiration_search_provider,
)


@pytest.mark.parametrize("sse", [False, True])
async def test_config_factory_anonymous_search(tmp_path, monkeypatch, sse):
    config_path = tmp_path / "config.toml"
    config_path.write_text('[discovery]\ninspiration_search_backends = ["parallel"]\n')
    config = load_config(config_path, consult_environment=False)
    provider = build_inspiration_search_provider(config.discovery.inspiration_search_backends)
    assert isinstance(provider, FallbackInspirationSearchProvider)
    (inner,) = provider.providers
    assert isinstance(inner, ParallelInspirationProvider)
    assert "parallel" not in DiscoveryConfig().inspiration_search_backends
    seen = []

    def handler(request):
        assert str(request.url) == "https://search.parallel.ai/mcp"
        assert request.headers["user-agent"] == "OpenBiliClaw (Parallel Search MCP)"
        assert "authorization" not in request.headers
        assert "x-api-key" not in request.headers
        payload = json.loads(request.content)
        seen.append(payload)
        if payload["method"] == "notifications/initialized":
            assert request.headers["mcp-session-id"] == "fixture-session"
            return httpx.Response(202)
        if payload["method"] == "initialize":
            result = {"protocolVersion": "2025-03-26"}
        else:
            assert request.headers["mcp-protocol-version"] == "2025-03-26"
            args = payload["params"]["arguments"]
            assert args["objective"] == "python async tutorial"
            assert args["search_queries"] == ["python async tutorial"]
            assert len(args["session_id"]) == 32
            result = {
                "structuredContent": {
                    "results": [
                        {
                            "title": "Async tutorial",
                            "url": "https://docs.python.org/3/library/asyncio.html",
                            "excerpts": ["Coroutines and tasks"],
                        },
                        {"title": "Bad link", "url": "javascript:alert(1)", "excerpts": []},
                    ]
                }
            }
        rpc = {"jsonrpc": "2.0", "id": payload["id"], "result": result}
        if sse:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream", "mcp-session-id": "fixture-session"},
                content='data: {"jsonrpc":"2.0","method":"ping"}\n\n'
                + "data: "
                + json.dumps(rpc)
                + "\r\n\r\n",
            )
        return httpx.Response(200, json=rpc, headers={"mcp-session-id": "fixture-session"})

    monkeypatch.setattr(inner, "_transport", httpx.MockTransport(handler))
    items = await provider.search("python async tutorial", limit=1)
    assert items == [
        ExaPreviewItem(
            title="Async tutorial",
            url="https://docs.python.org/3/library/asyncio.html",
            highlights=("Coroutines and tasks",),
        )
    ]
    session = seen[-1]["params"]["arguments"]["session_id"]
    await provider.search("python async tutorial", limit=1)
    assert seen[-1]["params"]["arguments"]["session_id"] == session
    provider.begin_stage()
    await provider.search("python async tutorial", limit=1)
    assert seen[-1]["params"]["arguments"]["session_id"] != session


@pytest.mark.parametrize("failure", ["http", "rpc", "tool", "malformed", "oversized"])
async def test_failure_uses_existing_fallback(failure):
    def handler(request):
        payload = json.loads(request.content)
        if payload["method"] == "initialize":
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-03-26"}}
            )
        if payload["method"] == "notifications/initialized":
            return httpx.Response(202)
        if failure == "http":
            return httpx.Response(429)
        if failure == "oversized":
            return httpx.Response(
                200, headers={"content-type": "application/json"}, content=b"x" * (1024 * 1024 + 1)
            )
        rpc = {"jsonrpc": "2.0", "id": 2, "result": {"structuredContent": {"results": []}}}
        if failure == "rpc":
            rpc["error"] = {"code": -1}
        elif failure == "tool":
            rpc["result"]["isError"] = True
        else:
            rpc["result"] = {"structuredContent": {"results": "invalid"}}
        return httpx.Response(200, json=rpc)

    class Backup:
        def begin_stage(self):
            pass

        async def search(self, query, *, limit):
            return [ExaPreviewItem(title="Backup", url="https://example.com", highlights=())]

    provider = ParallelInspirationProvider(transport=httpx.MockTransport(handler))
    chain = FallbackInspirationSearchProvider([provider, Backup()])
    assert (await chain.search("query", limit=1))[0].title == "Backup"


async def test_cancellation_and_wall_clock_timeout():
    started = asyncio.Event()

    async def handler(request):
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    provider = ParallelInspirationProvider(
        timeout_seconds=1, transport=httpx.MockTransport(handler)
    )
    task = asyncio.create_task(provider.search("query", limit=1))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(TimeoutError):
        await provider.search("query", limit=1)


async def test_blank_query_does_not_request():
    def handler(request):
        raise AssertionError("empty query must not request")

    provider = ParallelInspirationProvider(transport=httpx.MockTransport(handler))
    assert await provider.search(" ", limit=3) == []
    assert await provider.search("query", limit=0) == []


async def test_text_result_empty_and_network_routing(monkeypatch):
    from openbiliclaw import network

    observed = []
    original = httpx.AsyncClient

    def handler(request):
        payload = json.loads(request.content)
        if payload["method"] == "notifications/initialized":
            return httpx.Response(202)
        result = (
            {"protocolVersion": "2025-03-26"}
            if payload["id"] == 1
            else {"content": [{"type": "text", "text": '{"results":[]}'}]}
        )
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": result})

    def client(**kwargs):
        observed.append(kwargs.copy())
        kwargs.pop("proxy", None)
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(**kwargs)

    monkeypatch.setattr(
        network,
        "outbound_httpx_kwargs",
        lambda: {"proxy": "http://proxy.example:8080", "trust_env": False},
    )
    monkeypatch.setattr(httpx, "AsyncClient", client)
    provider = build_inspiration_search_provider(["parallel"])
    assert await provider.search("query", limit=2) == []
    assert observed[0]["proxy"] == "http://proxy.example:8080"
    assert observed[0]["trust_env"] is False
