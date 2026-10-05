"""Synthetic Instagram surface acceptance; never calls Instagram or a real backend.

The public seam is recommendation JSON -> production page -> visible, actionable
cards. The local HTTP stub also acts as a deny-by-default browser proxy for manual
installed-extension popup/native-side-panel checks. Run this file directly to keep
that stub alive; it prints its URL. This is UI evidence, not upstream/LLM evidence.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import threading
from contextlib import contextmanager, suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.integration


def synthetic_cards() -> list[dict[str, Any]]:
    """Deliberately synthetic DTOs, covering the three coverless rendering risks."""
    samples = [
        ("SHORT", "[SYNTHETIC] 无封面 Instagram 帖子", "没有封面，也应能阅读正文和操作按钮。"),
        (
            "LONG",
            "[SYNTHETIC] 长标题与长正文：" + "从初始化到内容发现的端到端验收" * 12,
            "这是一条模拟 Instagram 长正文，不来自任何真实账号。\n" * 90,
        ),
        (
            "TOKEN",
            "[SYNTHETIC] " + "UNBROKEN_LONG_TITLE_" * 40,
            "UNBROKEN_LONG_BODY_" * 220,
        ),
    ]
    return [
        {
            "id": index,
            "bvid": f"instagram:SYNTHETIC_{suffix}",
            "content_id": f"SYNTHETIC_{suffix}",
            "content_url": f"https://www.instagram.com/p/SYNTHETIC_{suffix}/",
            "source_platform": "instagram",
            "content_type": "post",
            "title": title,
            "body_text": body,
            "author_name": f"合成作者_{suffix}",
            # RecommendationOut retains its compatibility field; saved DTOs use
            # author_name. Match the real recommendation HTTP contract here.
            "up_name": f"合成作者_{suffix}",
            "cover_url": "",
            "topic_label": "模拟 UI 验收",
            "expression": "合成推荐理由，仅用于布局与交互验证。",
        }
        for index, (suffix, title, body) in enumerate(samples, 910001)
    ]


class SurfaceStub:
    """In-memory fixture only: no SQLite, credentials, LLM, or upstream writes."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.denied_destinations: list[str] = []


@contextmanager
def surface_server(port: int = 0) -> Iterator[tuple[str, SurfaceStub]]:
    state = SurfaceStub()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

        def _reply(self, payload: Any, status: int = 200) -> None:
            self._bytes(json.dumps(payload).encode(), "application/json", status)

        def _bytes(self, body: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.end_headers()
            with suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(body)

        def _path(self) -> str | None:
            parsed = urlsplit(self.path)
            if parsed.netloc and (
                parsed.hostname != "127.0.0.1" or parsed.port != server.server_port
            ):
                state.denied_destinations.append(parsed.netloc)
                self._reply({"error": "synthetic_surface_proxy_denied"}, 403)
                return None
            return parsed.path

        def do_CONNECT(self) -> None:  # noqa: N802
            state.denied_destinations.append(self.path)
            self._reply({"error": "synthetic_surface_proxy_denied"}, 403)

        def do_OPTIONS(self) -> None:  # noqa: N802
            if self._path() is not None:
                self._reply({})

        def do_GET(self) -> None:  # noqa: N802
            path = self._path()
            if path is None:
                return
            web = ROOT / "src/openbiliclaw/web"
            for prefix, directory in (
                ("/web/", web / "desktop"),
                ("/m/", web),
                ("/shared/", web / "shared"),
            ):
                if path.startswith(prefix):
                    candidate = (directory / (path.removeprefix(prefix) or "index.html")).resolve()
                    if candidate.is_relative_to(directory.resolve()) and candidate.is_file():
                        self._bytes(
                            candidate.read_bytes(),
                            mimetypes.guess_type(candidate.name)[0] or "application/octet-stream",
                        )
                    else:
                        self._reply({}, 404)
                    return
            if path == "/surface-evidence":
                self._reply(
                    {
                        "synthetic": True,
                        "posts": state.posts,
                        "denied_destinations": state.denied_destinations,
                    }
                )
            elif path == "/api/recommendations":
                self._reply({"items": synthetic_cards()})
            elif path == "/api/recommendations/platform-availability":
                self._reply({"total_available": 3, "by_platform": {"instagram": 3}})
            elif path == "/api/runtime-status":
                self._reply(
                    {
                        "initialized": True,
                        "pool_available_count": 3,
                        "pool_size": 3,
                        "pool_refresh_state": "idle",
                        "pool_source_shares": {"instagram": 1},
                        "configured_sources": {"instagram": {"enabled": True}},
                        "unread_count": 0,
                    }
                )
            elif path == "/api/init-status":
                self._reply(
                    {
                        "initialized": True,
                        "running": False,
                        "can_start": False,
                        "reason": "already_initialized",
                        "stages": [],
                    }
                )
            elif path == "/api/config":
                self._reply(
                    {
                        "config": {
                            "sources": {"instagram": {"enabled": True}},
                            "scheduler": {"enabled": False},
                            "llm": {"default_provider": "none"},
                        }
                    }
                )
            elif path == "/api/auth/status":
                self._reply({"enabled": False, "authenticated": True})
            elif path == "/api/profile-summary":
                self._reply({"initialized": True})
            elif path == "/api/qr-info":
                self._reply({"lan_ip": "127.0.0.1"})
            elif path.endswith("/next-task"):
                self._bytes(b"", "application/json", 204)
            elif path in {"/api/ping", "/api/health"}:
                self._reply({"ok": True, "embedding_ready": True})
            elif path.startswith("/api/"):
                self._reply({"items": [], "has_more": False})
            else:
                self._reply({}, 404)

        def do_POST(self) -> None:  # noqa: N802
            path = self._path()
            if path is None:
                return
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            payload = json.loads(raw or b"{}")
            state.posts.append((path, payload))
            self._reply({"ok": True, "saved": True, "items": []})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize(
    "surface,width,height",
    [("web", 1440, 1000), ("m", 375, 844), ("m", 390, 844), ("m", 768, 844)],
)
def test_instagram_coverless_cards_keep_actions_visible(
    surface: str, width: int, height: int
) -> None:
    playwright_api = pytest.importorskip("playwright.sync_api")
    with surface_server() as (base_url, state), playwright_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(viewport={"width": width, "height": height})
        context.route(
            "**/*",
            lambda route: (
                route.continue_() if route.request.url.startswith(base_url + "/") else route.abort()
            ),
        )
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script("localStorage.setItem('openbiliclaw.webui.autoLoadOnScroll', '0')")
        page.goto(f"{base_url}/{surface}/")
        selector = ".video-card" if surface == "web" else ".card"
        cards = page.locator(selector)
        playwright_api.expect(cards).to_have_count(3)
        for index, item in enumerate(synthetic_cards()):
            card = cards.nth(index)
            playwright_api.expect(card).to_contain_text(item["author_name"])
            playwright_api.expect(card).to_contain_text("Instagram")
            assert card.locator("img").count() == 0
            assert card.locator(".is-text-card").count() == 1
            actions = (
                ["喜欢", "不感兴趣", "忽略", "稍后再看", "收藏", "聊一聊"]
                if surface == "web"
                else ["打开", "喜欢", "不感兴趣", "稍后再看", "收藏", "聊一聊"]
            )
            for name in actions:
                button = card.get_by_role("button", name=name, exact=True)
                playwright_api.expect(button).to_have_count(1)
                playwright_api.expect(button).to_be_visible()
                button.scroll_into_view_if_needed()
                assert button.evaluate(
                    """el => {
                      const r = el.getBoundingClientRect();
                      const hit = document.elementFromPoint(r.x + r.width/2, r.y + r.height/2);
                      return r.width > 0 && r.height > 0 && r.left >= 0 &&
                        r.right <= innerWidth && !!hit && el.contains(hit);
                    }"""
                )
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        with page.expect_request(
            lambda request: (
                request.url == base_url + "/api/saved/favorite" and request.method == "POST"
            )
        ) as saved_request:
            cards.first.get_by_role("button", name="收藏", exact=True).click()
        saved = saved_request.value.post_data_json
        assert saved["source_platform"] == "instagram"
        assert saved["content_id"] == "SYNTHETIC_SHORT"
        assert saved["content_url"] == "https://www.instagram.com/p/SYNTHETIC_SHORT/"
        assert not errors
        assert not state.denied_destinations
        context.close()
        browser.close()


@pytest.mark.parametrize("width", [375, 390, 768])
def test_instagram_mobile_scroll_keeps_chat_action_above_floating_control(width: int) -> None:
    """A completed scroll must not let the back-to-top button steal the tap."""
    playwright_api = pytest.importorskip("playwright.sync_api")
    with surface_server() as (base_url, _state), playwright_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(viewport={"width": width, "height": 844})
        context.route(
            "**/*",
            lambda route: (
                route.continue_() if route.request.url.startswith(base_url + "/") else route.abort()
            ),
        )
        page = context.new_page()
        page.goto(base_url + "/m/")
        cards = page.locator(".card")
        playwright_api.expect(cards).to_have_count(3)
        cards.nth(2).scroll_into_view_if_needed()
        playwright_api.expect(page.get_by_role("button", name="回到顶部")).to_be_visible()
        button = cards.nth(2).get_by_role("button", name="聊一聊", exact=True)
        assert button.evaluate(
            """el => {
              const r = el.getBoundingClientRect();
              const floating = document.getElementById('backToTop').getBoundingClientRect();
              const overlaps = r.left < floating.right && r.right > floating.left &&
                r.top < floating.bottom && r.bottom > floating.top;
              const hit = document.elementFromPoint(r.x + r.width/2, r.y + r.height/2);
              return !overlaps && !!hit && el.contains(hit);
            }"""
        ), "The visible chat action must receive the tap, not the floating back-to-top button"
        page.set_viewport_size({"width": 390 if width == 768 else 768, "height": 844})
        page.locator("#app").evaluate("el => { el.scrollTop = el.scrollHeight; }")
        top_button = page.get_by_role("button", name="回到顶部")
        playwright_api.expect(top_button).to_be_visible()
        assert top_button.evaluate(
            """el => {
              const r = el.getBoundingClientRect();
              const nav = document.getElementById('tab-bar').getBoundingClientRect();
              const hit = document.elementFromPoint(r.x + r.width/2, r.y + r.height/2);
              return r.bottom < nav.top && r.top >= 0 && !!hit && el.contains(hit);
            }"""
        )
        top_button.click()
        page.wait_for_function("document.getElementById('app').scrollTop === 0")
        playwright_api.expect(top_button).to_be_hidden()
        context.close()
        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    with surface_server(parser.parse_args().port) as (url, _state):
        print(url, flush=True)
        threading.Event().wait()
