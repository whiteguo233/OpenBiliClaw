"""Real-browser director identity/visibility checks on desktop and mobile."""

from __future__ import annotations

import json
import mimetypes
import threading
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def director_asset_server() -> Any:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

        def do_GET(self) -> None:  # noqa: N802
            route = self.path.split("?", 1)[0]
            if route in {"/web", "/web/"}:
                path = ROOT / "src/openbiliclaw/web/desktop/index.html"
            elif route.startswith("/web/assets/"):
                path = (
                    ROOT
                    / "src/openbiliclaw/web/desktop/assets"
                    / route.removeprefix("/web/assets/")
                )
            elif route in {"/m", "/m/"}:
                path = ROOT / "src/openbiliclaw/web/index.html"
            elif route.startswith("/m/"):
                path = ROOT / "src/openbiliclaw/web" / route.removeprefix("/m/")
            elif route.startswith("/shared/"):
                path = ROOT / "src/openbiliclaw/web/shared" / route.removeprefix("/shared/")
            else:
                self.send_error(404)
                return
            if not path.is_file():
                self.send_error(404)
                return
            content = path.read_bytes()
            self.send_response(200)
            self.send_header(
                "Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            )
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            with suppress(BrokenPipeError):
                self.wfile.write(content)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        worker.join(timeout=5)


def _items() -> list[dict[str, object]]:
    return [
        {
            "id": index,
            "bvid": f"BV1DIR{index}",
            "content_id": f"BV1DIR{index}",
            "item_key": f"bilibili:BV1DIR{index}",
            "batch_ref": "batch_director_fixture",
            "stream_ref": "default",
            "source_platform": "bilibili",
            "title": f"导演测试卡片 {index}",
            "up_name": "合成作者",
            "expression": "实际可见的卡片才记为曝光；离屏预取不算曝光。",
            "content_url": f"https://www.bilibili.com/video/BV1DIR{index}",
            "presented": False,
        }
        for index in range(1, 11)
    ]


def _route_api(
    page: Any, *, needs_generation: bool, hide_on_restore: bool = False
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def route_api(route: Any) -> None:
        path = route.request.url.split("/api", 1)[1].split("?", 1)[0]
        if route.request.method == "POST":
            body = route.request.post_data_json or {}
            calls.append({"path": path, "body": body})
            if path == "/recommendations/director/generate":
                payload: object = {
                    "items": _items(),
                    "director": {
                        "mode": "enforce",
                        "strategy": "agent_only",
                        "stream_ref": "default",
                        "batch_ref": "batch_director_fixture",
                        "requires_generation": False,
                    },
                }
            else:
                payload = {"ok": True, "accepted": True}
        elif path == "/recommendations":
            if hide_on_restore:
                page.evaluate("window.__directorHidden = true")
            payload = {
                "items": [] if needs_generation else _items(),
                "director": {
                    "mode": "enforce",
                    "strategy": "agent_only",
                    "stream_ref": "default",
                    "batch_ref": None if needs_generation else "batch_director_fixture",
                    "requires_generation": needs_generation,
                },
            }
        elif path in {"/ping", "/health"}:
            payload = {"ok": True, "embedding_ready": True}
        elif path == "/auth/status":
            payload = {"enabled": False, "authenticated": True}
        elif path == "/runtime-status":
            payload = {
                "initialized": True,
                "pool_available_count": 40,
                "pool_size": 40,
                "pool_refresh_state": "idle",
                "configured_sources": {"bilibili": {"enabled": True}},
            }
        elif path == "/init-status":
            payload = {
                "initialized": True,
                "running": False,
                "can_start": False,
                "reason": "already_initialized",
                "stages": [],
                "prerequisites": {
                    "bilibili_logged_in": True,
                    "llm_ready": True,
                    "embedding_ready": True,
                },
            }
        elif path == "/config":
            payload = {
                "config": {
                    "sources": {"bilibili": {"enabled": True}},
                    "scheduler": {},
                    "llm": {"default_provider": "ollama", "ollama": {}},
                }
            }
        elif path == "/recommendations/platform-availability":
            payload = {"total_available": 40, "by_platform": {"bilibili": 40}}
        elif path == "/profile-summary":
            payload = {"initialized": True}
        else:
            payload = {"items": [], "has_more": False}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    page.route("**/api/**", route_api)
    return calls


_INIT = """
window.__directorHidden = false;
Object.defineProperty(document, 'hidden', {configurable:true,get:()=>window.__directorHidden});
window.WebSocket = class FakeWebSocket {
  static OPEN=1;
  constructor(){
    this.readyState=1;this.handlers={};
    setTimeout(()=>{this.onopen?.({});for(const fn of this.handlers.open||[])fn({});},0);
  }
  addEventListener(type,fn){(this.handlers[type]||=[]).push(fn)}
  removeEventListener(){} close(){this.readyState=3}
};
"""


@pytest.mark.parametrize("surface", ["web", "m"])
def test_initial_generation_and_exact_batch_feedback_in_real_browser(
    director_asset_server: str, surface: str
) -> None:
    with playwright_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(
            viewport={"width": 1200 if surface == "web" else 390, "height": 760}
        )
        page.add_init_script(_INIT)
        calls = _route_api(page, needs_generation=True)
        try:
            page.goto(f"{director_asset_server}/{surface}/")
            cards = page.locator('[data-batch-ref="batch_director_fixture"]')
            playwright_api.expect(cards).to_have_count(10, timeout=20000)
            playwright_api.expect(cards.first).to_be_visible()
            page.wait_for_timeout(500)
            generated = [call for call in calls if call["path"].endswith("/generate")]
            assert len(generated) == 1
            assert generated[0]["body"]["request_id"]
            presented = [call for call in calls if call["path"].endswith("/present")]
            assert 0 < len(presented) < 10
            assert all(call["body"]["batch_ref"] == "batch_director_fixture" for call in presented)
            cards.first.get_by_role("button", name="喜欢", exact=True).click()
            page.wait_for_timeout(10500 if surface == "web" else 300)
            feedback = [call for call in calls if call["path"] == "/feedback"]
            assert len(feedback) == 1
            assert feedback[0]["body"]["recommendation_id"] == 1
            assert feedback[0]["body"]["batch_ref"] == "batch_director_fixture"
        finally:
            browser.close()


@pytest.mark.parametrize("surface", ["web", "m"])
def test_hidden_restored_cards_not_acknowledged_until_foreground(
    director_asset_server: str, surface: str
) -> None:
    with playwright_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(
            viewport={"width": 1200 if surface == "web" else 390, "height": 760}
        )
        page.add_init_script(_INIT)
        calls = _route_api(page, needs_generation=False, hide_on_restore=True)
        try:
            page.goto(f"{director_asset_server}/{surface}/")
            cards = page.locator('[data-batch-ref="batch_director_fixture"]')
            playwright_api.expect(cards).to_have_count(10, timeout=20000)
            page.wait_for_timeout(500)
            assert not [call for call in calls if call["path"].endswith("/present")]
            page.evaluate(
                "window.__directorHidden=false;"
                "document.dispatchEvent(new Event('visibilitychange'))"
            )
            page.wait_for_timeout(500)
            assert [call for call in calls if call["path"].endswith("/present")]
            assert not [call for call in calls if call["path"].endswith("/generate")]
        finally:
            browser.close()
