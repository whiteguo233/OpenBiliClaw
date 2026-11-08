"""Keenable grounding through the maintained configuration and provider factory."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import pytest

from openbiliclaw import network
from openbiliclaw.config import (
    DiscoveryConfig,
    load_config,
    load_config_with_diagnostics,
)
from openbiliclaw.discovery.inspiration import ExaPreviewItem
from openbiliclaw.discovery.inspiration_provider import (
    FallbackInspirationSearchProvider,
    InspirationGroundingKeys,
    KeenableInspirationProvider,
    build_inspiration_search_provider,
    parse_keenable_search_payload,
)

if TYPE_CHECKING:
    from pathlib import Path


def _route_through(monkeypatch: pytest.MonkeyPatch, handler: object) -> list[dict[str, object]]:
    """Serve the provider's real ``[network]`` routing path from a mock transport."""

    observed: list[dict[str, object]] = []

    def outbound() -> dict[str, object]:
        kwargs: dict[str, object] = {"trust_env": False}
        observed.append(dict(kwargs))
        kwargs["transport"] = httpx.MockTransport(handler)  # type: ignore[arg-type]
        return kwargs

    monkeypatch.setattr(network, "outbound_httpx_kwargs", outbound)
    return observed


async def test_config_factory_keyless_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('[discovery]\ninspiration_search_backends = ["keenable"]\n')
    config = load_config(config_path, consult_environment=False)
    assert "keenable" not in DiscoveryConfig().inspiration_search_backends
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url) == "https://api.keenable.ai/v1/search/public"
        assert request.headers["x-keenable-title"] == "OpenBiliClaw"
        assert "x-api-key" not in request.headers
        assert "authorization" not in request.headers
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://docs.python.org/3/library/asyncio.html",
                        "title": "asyncio",
                        "description": "",
                        "snippet": "Coroutines and tasks",
                        "published_at": "2026-10-01T00:00:00Z",
                    },
                    {"url": "", "title": "No URL", "snippet": "dropped"},
                ]
            },
        )

    observed = _route_through(monkeypatch, handler)
    provider = build_inspiration_search_provider(
        config.discovery.inspiration_search_backends,
        grounding_keys=InspirationGroundingKeys.from_discovery_config(config.discovery),
    )
    assert isinstance(provider, FallbackInspirationSearchProvider)
    assert [inner.backend_alias for inner in provider.providers] == ["keenable"]
    assert observed == [{"trust_env": False}]

    items = await provider.search("python async tutorial", limit=3)
    assert provider.last_search_provider == "keenable"

    assert seen == [{"query": "python async tutorial", "max_results": 3}]
    assert items == [
        ExaPreviewItem(
            title="asyncio",
            url="https://docs.python.org/3/library/asyncio.html",
            highlights=("Coroutines and tasks",),
        )
    ]


def test_removed_api_key_field_surfaces_a_notice_instead_of_silently_dropping(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('[discovery]\nkeenable_api_key = "stale-key"\n')

    config, diagnostics = load_config_with_diagnostics(config_path, consult_environment=False)

    assert not hasattr(config.discovery, "keenable_api_key")
    assert any(issue.field == "discovery.keenable_api_key" for issue in diagnostics.issues), [
        issue.field for issue in diagnostics.issues
    ]


def test_parse_keenable_search_payload_caps_snippets() -> None:
    long_snippet = "word " * 200
    payload = {
        "results": [
            {"url": "https://example.test/long", "title": "Long", "snippet": long_snippet},
            {
                "url": "https://example.test/fallback",
                "title": "<b>Fallback</b>",
                "snippet": "",
                "description": "From description.",
            },
            {"url": "https://example.test/bare", "title": "Bare"},
            {"url": "https://example.test/no-title", "title": ""},
            "not-a-dict",
        ]
    }

    items = parse_keenable_search_payload(payload)

    assert [item.title for item in items] == ["Long", "Fallback", "Bare"]
    assert len(items[0].highlights[0]) <= 400
    assert items[0].highlights[0].startswith("word word")
    assert items[1].highlights == ("From description.",)
    assert items[2].highlights == ()
    assert parse_keenable_search_payload(json.dumps({"results": payload["results"][2:3]})) == [
        ExaPreviewItem(title="Bare", url="https://example.test/bare")
    ]
    assert parse_keenable_search_payload("not json") == []
    assert parse_keenable_search_payload({"results": "invalid"}) == []


@pytest.mark.parametrize("failure", ["rate_limited", "bad_request", "server", "malformed"])
async def test_failure_uses_existing_fallback(failure: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if failure == "rate_limited":
            return httpx.Response(429)
        if failure == "bad_request":
            return httpx.Response(400, json={"detail": "missing X-Keenable-Title"})
        if failure == "server":
            return httpx.Response(503)
        return httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"})

    class Backup:
        def begin_stage(self) -> None:
            pass

        async def search(self, query: str, *, limit: int) -> list[ExaPreviewItem]:
            return [ExaPreviewItem(title="Backup", url="https://example.com", highlights=())]

    provider = KeenableInspirationProvider(transport=httpx.MockTransport(handler))
    chain = FallbackInspirationSearchProvider([provider, Backup()])

    assert (await chain.search("query", limit=1))[0].title == "Backup"


async def test_blank_query_does_not_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("empty query must not request")

    provider = KeenableInspirationProvider(transport=httpx.MockTransport(handler))

    assert await provider.search(" ", limit=3) == []


async def test_single_backend_failure_cools_down_before_retrying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(503)

    _route_through(monkeypatch, handler)
    provider = build_inspiration_search_provider(["keenable"])

    with pytest.raises(httpx.HTTPStatusError):
        await provider.search("query", limit=1)
    assert await provider.search("second query", limit=1) == []
    assert len(calls) == 1
