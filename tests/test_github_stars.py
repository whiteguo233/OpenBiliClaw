"""Tests for rate-limit-safe GitHub project statistics."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import pytest

from openbiliclaw.runtime.github_stars import GitHubStarCountService

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_star_count_uses_cache_and_etag_revalidation(tmp_path: Path) -> None:
    now = [1_000_000.0]
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={"stargazers_count": 321},
                headers={"ETag": '"repo-v1"'},
            )
        assert request.headers["If-None-Match"] == '"repo-v1"'
        return httpx.Response(304)

    transport = httpx.MockTransport(handler)
    service = GitHubStarCountService(
        cache_path=tmp_path / "github-stars.json",
        ttl_seconds=60,
        client_factory=lambda: httpx.AsyncClient(transport=transport),
        clock=lambda: now[0],
    )

    assert await service.get_snapshot() == {
        "github_stars": 321,
        "stale": False,
        "source": "github",
    }
    assert (await service.get_snapshot())["source"] == "cache"
    assert len(requests) == 1

    now[0] += 61
    refreshed = await service.get_snapshot()

    assert refreshed == {"github_stars": 321, "stale": False, "source": "github"}
    assert len(requests) == 2
    persisted = json.loads((tmp_path / "github-stars.json").read_text(encoding="utf-8"))
    assert persisted["github_stars"] == 321
    assert persisted["etag"] == '"repo-v1"'


@pytest.mark.asyncio
async def test_rate_limit_serves_stale_cache_and_backs_off(tmp_path: Path) -> None:
    cache_path = tmp_path / "github-stars.json"
    cache_path.write_text(
        json.dumps(
            {
                "github_stars": 123,
                "fetched_at": 1_000.0,
                "retry_at": 0,
                "etag": '"old"',
            }
        ),
        encoding="utf-8",
    )
    now = [2_000.0]
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(
            403,
            headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "3000"},
        )

    service = GitHubStarCountService(
        cache_path=cache_path,
        ttl_seconds=60,
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        clock=lambda: now[0],
    )

    first = await service.get_snapshot()
    second = await service.get_snapshot()

    assert first == {"github_stars": 123, "stale": True, "source": "cache"}
    assert second == first
    # First call: API (403) + one HTML-scrape fallback (also 403) = 2 requests.
    # Second call: the API is skipped inside its backoff window, but the
    # HTML fallback is intentionally NOT gated by the API backoff, so it
    # retries once more (+1) = 3 total.
    assert requests == 3


@pytest.mark.asyncio
async def test_rate_limit_falls_back_to_shields(tmp_path: Path) -> None:
    cache_path = tmp_path / "github-stars.json"
    cache_path.write_text(
        json.dumps({"github_stars": 123, "fetched_at": 1_000.0, "retry_at": 0, "etag": ""}),
        encoding="utf-8",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.github.com":
            return httpx.Response(403, headers={"X-RateLimit-Reset": "3000"})
        return httpx.Response(200, json={"value": "1.9k", "message": "1.9k"})

    service = GitHubStarCountService(
        cache_path=cache_path,
        ttl_seconds=60,
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        clock=lambda: 2_000.0,
    )

    snapshot = await service.get_snapshot()

    # shields.io rounds ("1.9k" -> 1900); the exact count isn't required here.
    assert snapshot == {"github_stars": 1900, "stale": False, "source": "github"}
    persisted = json.loads(cache_path.read_text(encoding="utf-8"))
    assert persisted["github_stars"] == 1900


@pytest.mark.asyncio
async def test_upstream_failure_without_cache_returns_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "60"})

    service = GitHubStarCountService(
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        clock=lambda: 10_000.0,
    )

    assert await service.get_snapshot() == {
        "github_stars": None,
        "stale": True,
        "source": "unavailable",
    }
