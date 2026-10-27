"""Public Exa MCP search contract, bounds and explicit failure handling."""

from __future__ import annotations

import asyncio
import gzip
import json
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from openbiliclaw.agent.tools import AgentToolContext, ToolRegistry
from openbiliclaw.agent.tools import web_search_tools as search

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable


def _envelope(text: str, **extra: Any) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"content": [{"type": "text", "text": text}], **extra},
    }


def _row(index: int = 1, *, title: str = "Python asyncio", snippet: str = "Task groups.") -> str:
    return (
        f"Title: {title}\nURL: https://docs.python.org/{index}/asyncio.html\n"
        f"Published: N/A\nAuthor: N/A\nHighlights:\n{snippet}"
    )


def _registry(monkeypatch: pytest.MonkeyPatch, handler: Callable[..., Any]) -> ToolRegistry:
    monkeypatch.setattr(
        search,
        "outbound_httpx_kwargs",
        lambda: {"transport": httpx.MockTransport(handler), "trust_env": False},
    )
    registry = ToolRegistry()
    for tool in search.build_web_search_tools(AgentToolContext()):
        registry.register(tool)
    return registry


async def test_search_uses_fixed_public_endpoint_and_only_explicit_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_envelope(_row()))

    registry = _registry(monkeypatch, respond)
    result = await registry.dispatch("search_web", {"query": "  asyncio official docs  "})
    assert result.ok
    output = json.loads(result.content)
    assert output["provider"] == "Exa public MCP"
    assert output["partial"] is False
    assert output["results"] == [
        {
            "title": "Python asyncio",
            "url": "https://docs.python.org/1/asyncio.html",
            "snippet": "Task groups.",
            "source": "docs.python.org",
        }
    ]
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == search.EXA_MCP_URL
    assert json.loads(request.content) == {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "web_search_exa",
            "arguments": {"query": "asyncio official docs", "numResults": 3},
        },
    }
    assert "authorization" not in request.headers
    assert "x-api-key" not in request.headers


async def test_builder_never_reads_local_context(monkeypatch: pytest.MonkeyPatch) -> None:
    class PrivateContext:
        def __getattribute__(self, name: str) -> Any:
            raise AssertionError(f"Local context must not be read: {name}")

    registry = _registry(monkeypatch, lambda _: httpx.Response(200, json=_envelope(_row())))
    tool = search.build_web_search_tools(PrivateContext())[0]  # type: ignore[arg-type]
    assert tool.permission_level == "read"
    assert await tool.handler({"query": "explicit public keyword"})
    assert registry


@pytest.mark.parametrize("limit, expected", [(0, 1), (-9, 1), (2, 2), (999, 5)])
async def test_limit_is_enforced_on_request_and_response(
    monkeypatch: pytest.MonkeyPatch, limit: int, expected: int
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["params"]["arguments"]["numResults"] == expected
        return httpx.Response(200, json=_envelope("\n\n---\n\n".join(_row(i) for i in range(8))))

    result = await _registry(monkeypatch, respond).dispatch(
        "search_web", {"query": "python", "limit": limit}
    )
    assert result.ok
    output = json.loads(result.content)
    assert len(output["results"]) == expected
    assert output["truncated"]
    assert all("---" not in row["snippet"] for row in output["results"])


@pytest.mark.parametrize("arguments", [{}, {"query": " "}, {"query": "a" * 501}, {"query": 1}])
async def test_invalid_query_does_not_contact_service(
    monkeypatch: pytest.MonkeyPatch, arguments: dict[str, Any]
) -> None:
    def unexpected(_: httpx.Request) -> httpx.Response:
        raise AssertionError("Invalid input must not leave the process")

    result = await _registry(monkeypatch, unexpected).dispatch("search_web", arguments)
    assert not result.ok


async def test_official_empty_response_is_distinct_from_unparseable_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _registry(
        monkeypatch,
        lambda _: httpx.Response(200, json=_envelope(search._EMPTY_RESULT)),
    )
    result = await registry.dispatch("search_web", {"query": "no matching page"})
    assert result.ok
    assert json.loads(result.content)["results"] == []


@pytest.mark.parametrize(
    "body",
    [
        {"jsonrpc": "2.0", "id": 1, "error": {"message": "private upstream detail"}},
        _envelope("private upstream detail", isError=True),
        _envelope("Rate limit exceeded: private upstream detail"),
        _envelope("Title: Missing URL\nHighlights:\nprivate upstream detail"),
        _envelope(""),
        {"jsonrpc": "2.0", "id": 1, "result": {}},
        {"jsonrpc": "2.0", "id": 99, "result": {"content": []}},
        {"message": "private upstream detail"},
    ],
)
async def test_protocol_and_tool_failures_are_not_successful_empty_results(
    monkeypatch: pytest.MonkeyPatch, body: dict[str, Any]
) -> None:
    result = await _registry(monkeypatch, lambda _: httpx.Response(200, json=body)).dispatch(
        "search_web", {"query": "public query"}
    )
    assert not result.ok
    assert result.error == "handler_error"
    assert "private upstream detail" not in result.content


@pytest.mark.parametrize("status", [302, 403, 429, 500])
async def test_http_failure_does_not_follow_redirect_or_disclose_body(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            status,
            text="private upstream detail",
            headers={"Location": "http://127.0.0.1/private"},
        )

    result = await _registry(monkeypatch, respond).dispatch("search_web", {"query": "public"})
    assert not result.ok
    assert f"HTTP {status}" in result.content
    assert "private upstream detail" not in result.content
    assert len(requests) == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="private HTML page", headers={"Content-Type": "text/html"}),
        httpx.Response(200, content=b"{broken", headers={"Content-Type": "application/json"}),
    ],
)
async def test_invalid_response_format_is_failure(
    monkeypatch: pytest.MonkeyPatch, response: httpx.Response
) -> None:
    result = await _registry(monkeypatch, lambda _: response).dispatch(
        "search_web", {"query": "public"}
    )
    assert not result.ok


@pytest.mark.parametrize("error_type", [httpx.ConnectError, httpx.ProxyError])
async def test_network_error_omits_proxy_credentials(
    monkeypatch: pytest.MonkeyPatch, error_type: type[httpx.HTTPError]
) -> None:
    def fail(_: httpx.Request) -> httpx.Response:
        raise error_type("https://user:private_password@proxy.example")

    result = await _registry(monkeypatch, fail).dispatch("search_web", {"query": "public"})
    assert not result.ok
    assert error_type.__name__ in result.content
    assert "代理设置" in result.content
    assert "private_password" not in result.content
    assert "proxy.example" not in result.content


async def test_partial_results_are_reported_and_text_fallback_is_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _row().replace("Highlights:\nTask groups.", "Text: Full text fallback.")
    raw += "\n\nTitle: Missing URL\nHighlights:\nBroken entry"
    result = await _registry(
        monkeypatch, lambda _: httpx.Response(200, json=_envelope(raw))
    ).dispatch("search_web", {"query": "python"})
    assert result.ok
    output = json.loads(result.content)
    assert output["partial"]
    assert len(output["results"]) == 1
    assert output["results"][0]["snippet"] == "Full text fallback."


@pytest.mark.parametrize(
    "url",
    ["javascript:alert(1)", "https://user:password@example.com", "https://exa.ai/" + "a" * 2048],
)
async def test_invalid_urls_are_omitted_with_partial_flag(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    raw = _row() + f"\n\nTitle: Bad entry\nURL: {url}\nHighlights:\nUnusable URL"
    result = await _registry(
        monkeypatch, lambda _: httpx.Response(200, json=_envelope(raw))
    ).dispatch("search_web", {"query": "python"})
    assert result.ok
    output = json.loads(result.content)
    assert output["partial"]
    assert len(output["results"]) == 1


async def test_large_results_remain_valid_bounded_json_with_complete_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    urls = [f"https://example.com/{i}/" + "x" * 1500 for i in range(5)]
    raw = "\n\n".join(
        f"Title: {'长标题' * 200}\nURL: {url}\nHighlights:\n" + '\\"摘要' * 1000 for url in urls
    )
    result = await _registry(
        monkeypatch, lambda _: httpx.Response(200, json=_envelope(raw))
    ).dispatch("search_web", {"query": "public", "limit": 5})
    assert result.ok
    assert len(result.content) <= search.MAX_RESULT_CHARS == 3500
    output = json.loads(result.content)
    assert output["truncated"]
    assert output["results"]
    assert all(row["url"] in urls for row in output["results"])
    assert output["results"][0]["snippet"]


async def test_unrenderable_record_is_not_reported_as_no_search_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = f"Title: {chr(0) * 240}\nURL: https://example.com/{'x' * 2000}\nHighlights:\nFound"
    result = await _registry(
        monkeypatch, lambda _: httpx.Response(200, json=_envelope(raw))
    ).dispatch("search_web", {"query": "public"})
    assert not result.ok
    assert "大小限制" in result.content


class _Stream(httpx.AsyncByteStream):
    def __init__(self, iterator: AsyncIterator[bytes]) -> None:
        self.iterator = iterator
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self.iterator:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True
        await self.iterator.aclose()  # type: ignore[attr-defined]


async def test_sse_finishes_on_rpc_response_without_waiting_for_stream_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def chunks() -> AsyncIterator[bytes]:
        yield b": keepalive\r\n\r\n"
        yield b'data: {"jsonrpc":"2.0","method":"notifications/progress"}\n\n'
        event = ("data: " + json.dumps(_envelope(_row())) + "\r\n\r\n").encode()
        yield event[:50]
        yield event[50:]
        raise AssertionError("Search should close once its complete result arrives")

    stream = _Stream(chunks())
    registry = _registry(
        monkeypatch,
        lambda _: httpx.Response(200, stream=stream, headers={"Content-Type": "text/event-stream"}),
    )
    result = await registry.dispatch("search_web", {"query": "asyncio"})
    assert result.ok
    assert stream.closed


async def test_wall_clock_timeout_bounds_slow_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(search, "SEARCH_TIMEOUT_SECONDS", 0.02)

    async def chunks() -> AsyncIterator[bytes]:
        while True:
            await asyncio.sleep(0.005)
            yield b": keepalive\n\n"

    stream = _Stream(chunks())
    registry = _registry(
        monkeypatch,
        lambda _: httpx.Response(200, stream=stream, headers={"Content-Type": "text/event-stream"}),
    )
    result = await asyncio.wait_for(registry.dispatch("search_web", {"query": "asyncio"}), 1)
    assert not result.ok
    assert "超时" in result.content
    assert stream.closed


async def test_body_cap_applies_after_decompression(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = json.dumps(_envelope(_row(snippet="x" * search.MAX_RESPONSE_BYTES))).encode()
    registry = _registry(
        monkeypatch,
        lambda _: httpx.Response(
            200,
            content=gzip.compress(raw),
            headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
        ),
    )
    result = await registry.dispatch("search_web", {"query": "public"})
    assert not result.ok
    assert "大小限制" in result.content


async def test_cancellation_is_not_converted_to_success_or_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def cancel(_: httpx.Request) -> httpx.Response:
        raise asyncio.CancelledError

    registry = _registry(monkeypatch, cancel)
    with pytest.raises(asyncio.CancelledError):
        await registry.dispatch("search_web", {"query": "public"})
