"""Tests for the local image proxy endpoint."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import pytest
from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_runtime_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep create_app() route tests independent from local credentials."""

    from openbiliclaw.config import Config, save_config

    project_root = tmp_path / "runtime"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(project_root))
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
