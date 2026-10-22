"""Tests for the local image proxy endpoint."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.runtime.image_cache import image_cache_key
from openbiliclaw.runtime.image_fetch import ImageFetchResult
from openbiliclaw.runtime.refresh import ContinuousRefreshController

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_runtime_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep create_app() route tests independent from local credentials."""

    from openbiliclaw.config import Config, save_config

    project_root = tmp_path / "runtime"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(project_root))
    # image_cache_dir() caches its resolved path in a module global; reset it so
    # each test resolves under its own tmp project root (no cross-test leakage).
    monkeypatch.setattr("openbiliclaw.runtime.image_cache._CACHE_DIR", None)
    cfg = Config()
    cfg.llm.default_provider = "ollama"
    cfg.llm.ollama.model = "llama3"
    save_config(cfg, project_root / "config.toml")


class FakeImageResponse:
    def __init__(
        self,
        *,
        status_code: int,
        headers: dict[str, str] | None = None,
        chunks: list[bytes] | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = httpx.Headers(headers or {})
        self.chunks = chunks or []
        self.read_count = 0
        self.closed = False

    @property
    def is_redirect(self) -> bool:
        return self.status_code in {301, 302, 303, 307, 308}

    async def aiter_bytes(self) -> Any:
        for chunk in self.chunks:
            self.read_count += 1
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


class FakeHTTPX:
    def __init__(self) -> None:
        self.responses: dict[str, FakeImageResponse] = {}
        self.timeout_urls: set[str] = set()

    def add(
        self,
        url: str,
        *,
        status_code: int,
        headers: dict[str, str] | None = None,
        chunks: list[bytes] | None = None,
    ) -> None:
        self.responses[url] = FakeImageResponse(
            status_code=status_code,
            headers=headers,
            chunks=chunks,
        )

    def client_class(self) -> type:
        fake = self

        class FakeAsyncClient:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            async def __aenter__(self) -> FakeAsyncClient:
                return self

            async def __aexit__(self, *_args: object) -> None:
                return None

            def build_request(
                self,
                method: str,
                url: str,
                *,
                headers: dict[str, str] | None = None,
            ) -> httpx.Request:
                return httpx.Request(method, url, headers=headers)

            async def send(
                self,
                request: httpx.Request,
                *,
                stream: bool = False,
            ) -> FakeImageResponse:
                assert stream is True
                url = str(request.url)
                if url in fake.timeout_urls:
                    raise httpx.TimeoutException("timed out", request=request)
                return fake.responses.get(
                    url,
                    FakeImageResponse(status_code=404, headers={}, chunks=[]),
                )

        return FakeAsyncClient


@pytest.fixture
def fake_httpx(monkeypatch: pytest.MonkeyPatch) -> FakeHTTPX:
    fake = FakeHTTPX()
    monkeypatch.setattr(httpx, "AsyncClient", fake.client_class())
    monkeypatch.setattr(
        "openbiliclaw.runtime.image_cache.resolve_public_addresses",
        AsyncMock(return_value=("1.1.1.1",)),
    )
    return fake


@pytest.fixture
def client() -> TestClient:
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())
    return TestClient(app)


def test_bilibili_image_success(client: TestClient, fake_httpx: FakeHTTPX) -> None:
    fake_httpx.add(
        "https://i1.hdslb.com/bfs/archive/demo.jpg",
        status_code=200,
        headers={"content-type": "image/jpeg", "content-length": "4"},
        chunks=[b"demo"],
    )
    resp = client.get(
        "/api/image-proxy",
        params={"url": "https://i1.hdslb.com/bfs/archive/demo.jpg"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/jpeg")
    assert resp.headers["cache-control"] == "public, max-age=86400"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.content == b"demo"


def test_image_proxy_serves_cache_first_on_second_request(
    client: TestClient, fake_httpx: FakeHTTPX
) -> None:
    # Regression: covers were re-downloaded on every request (cache only read on
    # upstream failure), so cached images stayed ~2s slow. The success path must
    # serve the cached copy first.
    url = "https://i1.hdslb.com/bfs/archive/cached.jpg"
    fake_httpx.add(
        url,
        status_code=200,
        headers={"content-type": "image/jpeg", "content-length": "4"},
        chunks=[b"demo"],
    )

    first = client.get("/api/image-proxy", params={"url": url})
    assert first.status_code == 200
    assert first.headers.get("x-image-cache") == "miss"
    assert first.content == b"demo"

    # Drop the upstream entirely: a second request must NOT re-fetch — if it did,
    # the fake returns 404 and the proxy would 404 (no cache fallback for <500).
    fake_httpx.responses.clear()

    second = client.get("/api/image-proxy", params={"url": url})
    assert second.status_code == 200
    assert second.headers.get("x-image-cache") == "hit"
    assert second.content == b"demo"


def test_xhscdn_image_success(client: TestClient, fake_httpx: FakeHTTPX) -> None:
    fake_httpx.add(
        "https://sns-webpic-qc.xhscdn.com/demo.jpg",
        status_code=200,
        headers={"content-type": "image/webp"},
        chunks=[b"webp"],
    )
    resp = client.get(
        "/api/image-proxy",
        params={"url": "https://sns-webpic-qc.xhscdn.com/demo.jpg"},
    )
    assert resp.status_code == 200
    assert resp.content == b"webp"


@pytest.mark.parametrize(
    ("url", "expected_status"),
    [
        ("https://example.com/image.jpg", 403),
        ("https://evilhdslb.com/image.jpg", 403),
        ("ftp://i1.hdslb.com/image.jpg", 400),
        ("not-a-url", 400),
        ("https://user:pass@i1.hdslb.com/image.jpg", 400),
    ],
)
def test_url_validation(client: TestClient, url: str, expected_status: int) -> None:
    resp = client.get("/api/image-proxy", params={"url": url})
    assert resp.status_code == expected_status


def test_redirect_to_non_whitelisted_domain_rejected(
    client: TestClient,
    fake_httpx: FakeHTTPX,
) -> None:
    fake_httpx.add(
        "https://i1.hdslb.com/redirect.jpg",
        status_code=302,
        headers={"location": "https://example.com/image.jpg"},
        chunks=[],
    )
    resp = client.get("/api/image-proxy", params={"url": "https://i1.hdslb.com/redirect.jpg"})
    assert resp.status_code == 403


def test_redirect_loop_returns_502(client: TestClient, fake_httpx: FakeHTTPX) -> None:
    fake_httpx.add(
        "https://i1.hdslb.com/a.jpg",
        status_code=302,
        headers={"location": "https://i1.hdslb.com/a.jpg"},
        chunks=[],
    )
    resp = client.get("/api/image-proxy", params={"url": "https://i1.hdslb.com/a.jpg"})
    assert resp.status_code == 502


def test_non_image_content_type_rejected(client: TestClient, fake_httpx: FakeHTTPX) -> None:
    fake_httpx.add(
        "https://i1.hdslb.com/page",
        status_code=200,
        headers={"content-type": "text/html"},
        chunks=[b"<html>"],
    )
    resp = client.get("/api/image-proxy", params={"url": "https://i1.hdslb.com/page"})
    assert resp.status_code == 400


def test_content_length_over_limit_rejected_before_body(
    client: TestClient,
    fake_httpx: FakeHTTPX,
) -> None:
    fake_httpx.add(
        "https://i1.hdslb.com/large.jpg",
        status_code=200,
        headers={"content-type": "image/jpeg", "content-length": str(10 * 1024 * 1024 + 1)},
        chunks=[b"should-not-read"],
    )
    resp = client.get("/api/image-proxy", params={"url": "https://i1.hdslb.com/large.jpg"})
    assert resp.status_code == 413
    assert fake_httpx.responses["https://i1.hdslb.com/large.jpg"].read_count == 0


def test_actual_body_over_limit_rejected_without_content_length(
    client: TestClient,
    fake_httpx: FakeHTTPX,
) -> None:
    fake_httpx.add(
        "https://i1.hdslb.com/large-stream.jpg",
        status_code=200,
        headers={"content-type": "image/jpeg"},
        chunks=[b"x" * (10 * 1024 * 1024), b"x"],
    )
    resp = client.get("/api/image-proxy", params={"url": "https://i1.hdslb.com/large-stream.jpg"})
    assert resp.status_code == 413


def test_timeout_returns_504(client: TestClient, fake_httpx: FakeHTTPX) -> None:
    fake_httpx.timeout_urls.add("https://i1.hdslb.com/slow.jpg")
    resp = client.get("/api/image-proxy", params={"url": "https://i1.hdslb.com/slow.jpg"})
    assert resp.status_code == 504


def test_proxy_failure_logs_use_host_and_cache_hash_not_signed_url(
    client: TestClient,
    fake_httpx: FakeHTTPX,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.runtime import image_cache

    signed_url = (
        "https://sns-webpic-qc.xhscdn.com/202608010101/"
        "deadbeefdeadbeefdeadbeefdeadbeef/spectrum/log-cover!webp"
    )
    monkeypatch.setattr(image_cache, "_failure_log_state", {})
    fake_httpx.timeout_urls.add(signed_url)
    caplog.set_level("DEBUG", logger="openbiliclaw.api.app")
    caplog.set_level("WARNING", logger="openbiliclaw.runtime.image_cache")

    response = client.get("/api/image-proxy", params={"url": signed_url})

    assert response.status_code == 504
    relevant_logs = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name in {"openbiliclaw.api.app", "openbiliclaw.runtime.image_cache"}
    )
    assert signed_url not in relevant_logs
    assert "deadbeefdeadbeefdeadbeefdeadbeef" not in relevant_logs
    assert "host=sns-webpic-qc.xhscdn.com" in relevant_logs
    assert f"cache={image_cache_key(signed_url)[:12]}" in relevant_logs


async def test_api_proxy_and_refresh_prefetch_share_app_coordinator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_url = "https://i1.hdslb.com/bfs/archive/shared-api.jpg"
    prefetch_url = "https://i1.hdslb.com/bfs/archive/shared-prefetch.jpg"

    class CoverDatabase:
        def iter_servable_cover_urls(self, *, recent_hours: int, limit: int) -> list[str]:
            assert recent_hours > 0
            assert limit > 0
            return [prefetch_url]

    database = CoverDatabase()
    runtime = ContinuousRefreshController(
        memory_manager=object(),
        database=database,
        soul_engine=object(),
        discovery_engine=object(),
        recommendation_engine=object(),
    )
    app = create_app(
        memory_manager=object(),
        database=database,
        soul_engine=object(),
        runtime_controller=runtime,
    )
    coordinator = app.state.image_fetch_coordinator
    assert runtime.image_fetch_coordinator is coordinator

    calls: list[tuple[str, str]] = []

    async def fake_fetch(url: str, *, priority: str = "foreground") -> ImageFetchResult:
        calls.append(("fetch", url))
        assert priority == "foreground"
        return ImageFetchResult(b"api", "image/jpeg", cache_hit=False, stored=True)

    async def fake_prefetch(url: str) -> bool:
        calls.append(("prefetch", url))
        return True

    monkeypatch.setattr(coordinator, "fetch", fake_fetch)
    monkeypatch.setattr(coordinator, "prefetch", fake_prefetch)

    response = TestClient(app).get("/api/image-proxy", params={"url": api_url})
    assert response.status_code == 200
    assert response.headers["x-image-cache"] == "miss"
    assert await runtime._prefetch_uncached_covers(max_fetch=1) == 1
    assert calls == [("fetch", api_url), ("prefetch", prefetch_url)]


async def test_hot_reload_rebinds_new_controller_to_same_app_coordinator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from openbiliclaw.api.runtime_context import RuntimeContext
    from openbiliclaw.config import Config

    old_controller = SimpleNamespace()
    new_controller = SimpleNamespace()
    app = create_app(
        memory_manager=object(),
        database=object(),
        soul_engine=object(),
        runtime_controller=old_controller,
    )
    coordinator = app.state.image_fetch_coordinator
    assert old_controller.image_fetch_coordinator is coordinator

    async def fake_rebuild(self: RuntimeContext, new_config: Config) -> None:
        self.config = new_config
        self.runtime_controller = new_controller

    restart_bindings: list[object] = []

    async def fake_restart(
        self: RuntimeContext,
        _app: object,
        *,
        run_post_reload_llm_work: bool = True,
    ) -> None:
        assert run_post_reload_llm_work is True
        restart_bindings.append(self.runtime_controller.image_fetch_coordinator)

    monkeypatch.setattr(RuntimeContext, "rebuild_from_config", fake_rebuild)
    monkeypatch.setattr(RuntimeContext, "restart_background_tasks", fake_restart)

    await app.state._rebuild_runtime_with_lane_handoff(
        Config(),
        resume_execution_lanes=False,
    )

    assert new_controller.image_fetch_coordinator is coordinator
    assert restart_bindings == [coordinator]
