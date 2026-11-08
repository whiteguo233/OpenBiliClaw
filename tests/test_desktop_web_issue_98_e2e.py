from __future__ import annotations

import base64
import json
import mimetypes
import threading
import time
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")
Page = playwright_api.Page
expect = playwright_api.expect
sync_playwright = playwright_api.sync_playwright

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[1]
ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _recommendations(
    count: int = 3,
    *,
    image_backed: bool = False,
) -> list[dict[str, Any]]:
    return [
        {
            "id": index,
            "bvid": f"BV1ISSUE98{index}",
            "content_id": f"BV1ISSUE98{index}",
            "content_url": f"https://www.bilibili.com/video/BV1ISSUE98{index}",
            "source_platform": "bilibili",
            "title": f"稳定卡片 {index}",
            "up_name": f"UP {index}",
            "topic_label": "交互测试",
            "expression": f"第 {index} 张卡片的推荐理由。",
            "cover_url": (
                f"https://synthetic.invalid/covers/issue-98-{index}.png" if image_backed else ""
            ),
        }
        for index in range(1, count + 1)
    ]


def _delights() -> list[dict[str, Any]]:
    return [
        {
            "bvid": f"BV1DELIGHT{index}",
            "content_id": f"BV1DELIGHT{index}",
            "content_url": f"https://www.bilibili.com/video/BV1DELIGHT{index}",
            "source_platform": "bilibili",
            "title": f"惊喜卡片 {index}",
            "delight_reason": f"第 {index} 张惊喜卡片的推荐理由。",
            "delight_score": 0.9,
            "state": "pending",
        }
        for index in range(1, 3)
    ]


class Issue98Stub:
    def __init__(self) -> None:
        self.health_delay_seconds = 0.0
        self.recommendation_delay_seconds = 0.0
        self.recommendation_reads = 0
        self.runtime_reads = 0
        self.runtime_first_delay_seconds = 0.0
        self.runtime_reread_status = 200
        self.runtime_reread_received = threading.Event()
        self.recommendations = _recommendations()
        self.delights = _delights()
        self.image_proxy_reads = 0
        self.append_posts: list[dict[str, Any]] = []
        self.append_received = threading.Event()
        self.feedback_posts: list[dict[str, Any]] = []
        self.feedback_received = threading.Event()
        self.feedback_delay_seconds = 0.0
        self.feedback_status = 200
        self.probe_posts: list[dict[str, Any]] = []
        self.probe_received = threading.Event()
        self.probe_status = 200
        self.delight_response_status = 200
        self.delight_response_received = threading.Event()
        self.saved_list_reads: list[str] = []


def _json_response(
    handler: BaseHTTPRequestHandler,
    payload: dict[str, Any] | list[dict[str, Any]],
    status: int = 200,
) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    with suppress(BrokenPipeError):
        handler.wfile.write(body)


def _binary_response(
    handler: BaseHTTPRequestHandler,
    payload: bytes,
    content_type: str,
) -> None:
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    with suppress(BrokenPipeError):
        handler.wfile.write(payload)


@pytest.fixture()
def issue_98_server() -> tuple[str, Issue98Stub]:
    state = Issue98Stub()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path.startswith("/shared/"):
                # The desktop page and the setup wizard both load the shared
                # source-status module from the backend's /shared mount. Without
                # this route it 404s, app.js dies on the missing global, and the
                # failure surfaces as an unrelated test timing out.
                rel = path.removeprefix("/shared/")
                return self._serve_file(ROOT / "src/openbiliclaw/web/shared" / rel)
            if path in {"/web", "/web/", "/web/index.html"}:
                return self._serve_file(
                    ROOT / "src/openbiliclaw/web/desktop/index.html",
                    "text/html",
                )
            if path.startswith("/web/assets/"):
                rel = path.removeprefix("/web/assets/")
                return self._serve_file(ROOT / "src/openbiliclaw/web/desktop/assets" / rel)
            if path == "/api/ping":
                return _json_response(self, {"ok": True})
            if path == "/api/health":
                if state.health_delay_seconds:
                    time.sleep(state.health_delay_seconds)
                return _json_response(self, {"ok": True, "embedding_ready": True})
            if path == "/api/auth/status":
                return _json_response(self, {"enabled": False, "authenticated": True})
            if path == "/api/saved/watch_later":
                state.saved_list_reads.append("watch_later")
                return _json_response(self, {"items": [], "total": 3})
            if path == "/api/saved/favorite":
                state.saved_list_reads.append("favorite")
                return _json_response(self, {"items": [], "total": 2})
            if path == "/api/recommendations":
                state.recommendation_reads += 1
                if state.recommendation_delay_seconds:
                    time.sleep(state.recommendation_delay_seconds)
                return _json_response(self, {"items": state.recommendations})
            if path == "/api/runtime-status":
                state.runtime_reads += 1
                runtime_read = state.runtime_reads
                if runtime_read >= 2:
                    state.runtime_reread_received.set()
                if runtime_read == 1 and state.runtime_first_delay_seconds:
                    time.sleep(state.runtime_first_delay_seconds)
                if runtime_read >= 2 and state.runtime_reread_status >= 400:
                    return _json_response(
                        self,
                        {"error": "runtime reread failed"},
                        state.runtime_reread_status,
                    )
                available = 30 if runtime_read == 1 else 27
                return _json_response(
                    self,
                    {
                        "initialized": True,
                        "pool_available_count": available,
                        "pool_size": 30,
                        "pool_refresh_state": "idle",
                        "pool_source_shares": {"bilibili": 1.0},
                        "configured_sources": {"bilibili": {"enabled": True}},
                        "unread_count": 0,
                    },
                )
            if path == "/api/image-proxy":
                state.image_proxy_reads += 1
                return _binary_response(self, ONE_PIXEL_PNG, "image/png")
            if path == "/api/init-status":
                return _json_response(
                    self,
                    {
                        "initialized": True,
                        "running": False,
                        "can_start": False,
                        "reason": "already_initialized",
                        "stages": [],
                        "prerequisites": {
                            "bilibili_logged_in": True,
                            "llm_ready": True,
                            "embedding_ready": True,
                            "enabled_platforms": ["bilibili"],
                        },
                    },
                )
            if path == "/api/profile-summary":
                return _json_response(
                    self,
                    {
                        "initialized": True,
                        "core_traits": [],
                        "likes": [],
                        "dislikes": [],
                        "speculative_interests": [
                            {
                                "domain": "系统设计",
                                "reason": "你常看复杂系统的拆解。",
                                "status": "active",
                                "confidence": 0.8,
                            }
                        ],
                        "speculative_avoidances": [
                            {
                                "domain": "标题党",
                                "reason": "你会快速退出夸张标题内容。",
                                "status": "active",
                                "confidence": 0.7,
                            }
                        ],
                    },
                )
            if path == "/api/config":
                return _json_response(
                    self,
                    {
                        "config": {
                            "sources": {"bilibili": {"enabled": True}},
                            "scheduler": {},
                            "llm": {"default_provider": "ollama", "ollama": {}},
                        }
                    },
                )
            if path == "/api/activity-feed":
                return _json_response(
                    self,
                    {"items": [], "has_more": False, "next_cursor": ""},
                )
            if path == "/api/delight/pending-batch":
                return _json_response(self, {"items": state.delights})
            if path == "/api/notifications/pending":
                return _json_response(self, {"items": []})
            if path == "/api/chat/turns":
                return _json_response(self, {"items": []})
            if path == "/api/qr-info":
                return _json_response(self, {"lan_ip": "127.0.0.1"})
            return _json_response(self, {}, 404)

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                payload = {}
            if path == "/api/recommendations/append":
                state.append_posts.append(payload)
                state.append_received.set()
                return _json_response(self, {"items": []})
            if path == "/api/feedback":
                state.feedback_posts.append(payload)
                state.feedback_received.set()
                if state.feedback_delay_seconds:
                    time.sleep(state.feedback_delay_seconds)
                return _json_response(
                    self,
                    {"ok": state.feedback_status < 400},
                    state.feedback_status,
                )
            if path in {"/api/interest-probes/respond", "/api/avoidance-probes/respond"}:
                state.probe_posts.append({"path": path, **payload})
                state.probe_received.set()
                return _json_response(
                    self,
                    {
                        "ok": state.probe_status < 400,
                        "action": (
                            "confirmed" if payload.get("response") == "confirm" else "deferred"
                        ),
                    },
                    state.probe_status,
                )
            if path == "/api/delight/respond":
                state.delight_response_received.set()
                return _json_response(
                    self,
                    {"ok": state.delight_response_status < 400},
                    state.delight_response_status,
                )
            return _json_response(self, {"ok": True})

        def _serve_file(self, path: Path, content_type: str | None = None) -> None:
            if not path.exists():
                return _json_response(self, {"error": "not_found"}, 404)
            body = path.read_bytes()
            self.send_response(200)
            self.send_header(
                "Content-Type",
                content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture()
def chromium_page() -> Page:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.add_init_script(
            """
            // Production keeps undo available for 10 seconds. Use a shorter but
            // still human-sized window so Chromium can auto-scroll a tall card
            // under full-suite load without racing the delayed commit.
            window.__OBC_TEST_UNDO_WINDOW_MS = 5000;
            window.WebSocket = class FakeWebSocket {
              static OPEN = 1;
              constructor() {
                this.readyState = FakeWebSocket.OPEN;
                this.listeners = new Map();
                setTimeout(() => this.dispatch("open", { type: "open" }), 0);
              }
              addEventListener(type, handler) {
                const handlers = this.listeners.get(type) || [];
                handlers.push(handler);
                this.listeners.set(type, handlers);
              }
              removeEventListener(type, handler) {
                const handlers = this.listeners.get(type) || [];
                this.listeners.set(type, handlers.filter((item) => item !== handler));
              }
              dispatch(type, event) {
                if (typeof this[`on${type}`] === "function") this[`on${type}`](event);
                for (const handler of this.listeners.get(type) || []) handler(event);
              }
              close() { this.readyState = 3; }
            };
            """
        )
        yield page
        browser.close()


def _card_position(card: Any) -> tuple[float, float]:
    position = card.evaluate(
        """element => {
          const rect = element.getBoundingClientRect();
          return [rect.x + window.scrollX, rect.y + window.scrollY];
        }"""
    )
    return float(position[0]), float(position[1])


def _assert_position_stable(actual: tuple[float, float], expected: tuple[float, float]) -> None:
    assert actual[0] == pytest.approx(expected[0], abs=2.0)
    assert actual[1] == pytest.approx(expected[1], abs=2.0)


def _rect(locator: Any) -> dict[str, float]:
    return locator.evaluate(
        """el => { const r = el.getBoundingClientRect();
        return {left: r.left, right: r.right, top: r.top, width: r.width}; }"""
    )


def _start_horizontal_drag(page: Page, locator: Any, delta_x: int) -> None:
    box = locator.bounding_box()
    assert box is not None
    x = box["x"] + box["width"] / 2
    y = box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + delta_x, y)


def _drag_horizontally(page: Page, locator: Any, delta_x: int) -> None:
    _start_horizontal_drag(page, locator, delta_x)
    page.mouse.up()


def _load_desktop_with_closed_drawer(
    page: Page,
    base_url: str,
    *,
    width: int,
) -> None:
    page.set_viewport_size({"width": width, "height": 900})
    page.add_init_script("window.localStorage.setItem('openbiliclaw.sideDrawerOpen', '0')")
    page.goto(f"{base_url}/web/")
    expect(page.locator("#delightCount")).to_have_text("1/2")


def _delight_geometry(page: Page) -> dict[str, Any]:
    return page.evaluate(
        """() => {
          const layout = document.querySelector('.layout').getBoundingClientRect();
          const delight = document.querySelector('#delightBanner').getBoundingClientRect();
          return {
            layoutRight: layout.right,
            delightRight: delight.right,
            docClient: document.documentElement.clientWidth,
            docScroll: document.documentElement.scrollWidth,
            columns: getComputedStyle(document.querySelector('#delightBanner')).gridTemplateColumns,
          };
        }"""
    )


def _assert_delight_fits_available_width(geometry: dict[str, Any]) -> None:
    assert geometry["delightRight"] <= geometry["layoutRight"] + 1
    assert geometry["docScroll"] <= geometry["docClient"] + 1


def test_saved_badges_hydrate_before_tabs_are_opened(
    issue_98_server: tuple[str, Issue98Stub],
    chromium_page: Page,
) -> None:
    base_url, stub = issue_98_server

    chromium_page.goto(f"{base_url}/web/", wait_until="domcontentloaded")

    watch_later_badge = chromium_page.locator("#watchLaterCountBadge")
    favorites_badge = chromium_page.locator("#favoritesCountBadge")
    # The two independent totals hydrate while the content library is hidden;
    # opening it reveals the per-child badges without summing them.
    expect(watch_later_badge).to_have_text("3", timeout=3000)
    expect(favorites_badge).to_have_text("2", timeout=3000)
    assert set(stub.saved_list_reads) == {"watch_later", "favorite"}
    chromium_page.locator("#contentLibraryBtn").click()
    expect(chromium_page.locator("#contentLibraryPage")).to_be_visible()
    expect(chromium_page.locator("#contentLibraryWatchLaterTab")).to_be_visible()
    expect(watch_later_badge).to_be_visible()
    expect(favorites_badge).to_be_visible()


def test_saved_badge_hydration_failure_does_not_block_home(
    issue_98_server: tuple[str, Issue98Stub],
    chromium_page: Page,
) -> None:
    base_url, _ = issue_98_server
    failed_reads: list[str] = []

    def fail_saved_list(route: Any) -> None:
        if "/status?" in route.request.url:
            route.continue_()
            return
        failed_reads.append(route.request.url)
        route.abort("failed")

    chromium_page.route("**/api/saved/**", fail_saved_list)
    chromium_page.goto(f"{base_url}/web/", wait_until="domcontentloaded")

    expect(chromium_page.locator("#videoGrid .video-card")).to_have_count(3, timeout=3000)
    chromium_page.wait_for_timeout(1000)
    expect(chromium_page.locator("#watchLaterCountBadge")).to_be_hidden()
    expect(chromium_page.locator("#favoritesCountBadge")).to_be_hidden()
    assert len(failed_reads) == 2


def test_opening_saved_page_prevents_older_badge_hydration_from_winning(
    issue_98_server: tuple[str, Issue98Stub],
    chromium_page: Page,
) -> None:
    base_url, _ = issue_98_server
    chromium_page.add_init_script(
        """
        window.__obcWatchLaterListReads = 0;
        window.__obcResolveInitialWatchLater = null;
        const realFetch = window.fetch.bind(window);
        window.fetch = (input, init) => {
          const url = typeof input === "string" ? input : input.url;
          if (!url.includes("/api/saved/watch_later?limit=")) return realFetch(input, init);
          window.__obcWatchLaterListReads += 1;
          const response = (total) => new Response(
            JSON.stringify({ items: [], total }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
          if (window.__obcWatchLaterListReads === 1) {
            return new Promise((resolve) => {
              window.__obcResolveInitialWatchLater = () => resolve(response(3));
            });
          }
          return Promise.resolve(response(4));
        };
        """
    )

    chromium_page.goto(f"{base_url}/web/", wait_until="domcontentloaded")
    chromium_page.wait_for_function(
        "() => window.__obcWatchLaterListReads === 1 "
        "&& typeof window.__obcResolveInitialWatchLater === 'function'"
    )
    expect(chromium_page.locator("#videoGrid .video-card")).to_have_count(3, timeout=3000)

    chromium_page.locator("#contentLibraryBtn").click()
    expect(chromium_page.locator("#watchLaterCountBadge")).to_have_text("4", timeout=3000)

    chromium_page.evaluate("window.__obcResolveInitialWatchLater()")
    chromium_page.wait_for_timeout(300)
    expect(chromium_page.locator("#watchLaterCountBadge")).to_have_text("4")


def test_older_full_saved_refresh_cannot_overwrite_newer_badge(
    issue_98_server: tuple[str, Issue98Stub],
    chromium_page: Page,
) -> None:
    base_url, _ = issue_98_server
    chromium_page.add_init_script(
        """
        window.__obcWatchLaterListReads = 0;
        window.__obcResolveFirstFullWatchLater = null;
        const realFetch = window.fetch.bind(window);
        window.fetch = (input, init) => {
          const url = typeof input === "string" ? input : input.url;
          if (!url.includes("/api/saved/watch_later?limit=")) return realFetch(input, init);
          window.__obcWatchLaterListReads += 1;
          const response = (total) => new Response(
            JSON.stringify({ items: [], total }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
          if (window.__obcWatchLaterListReads === 2) {
            return new Promise((resolve) => {
              window.__obcResolveFirstFullWatchLater = () => resolve(response(4));
            });
          }
          if (window.__obcWatchLaterListReads === 3) return Promise.resolve(response(5));
          return realFetch(input, init);
        };
        """
    )

    chromium_page.goto(f"{base_url}/web/", wait_until="domcontentloaded")
    chromium_page.wait_for_function("() => window.__obcWatchLaterListReads === 1")
    chromium_page.evaluate(
        """
        () => {
          document.getElementById("contentLibraryBtn").click();
          document.getElementById("contentLibraryWatchLaterTab").click();
        }
        """
    )
    chromium_page.wait_for_function(
        "() => window.__obcWatchLaterListReads === 3 "
        "&& typeof window.__obcResolveFirstFullWatchLater === 'function'"
    )
    expect(chromium_page.locator("#watchLaterCountBadge")).to_have_text("5", timeout=3000)

    chromium_page.evaluate("window.__obcResolveFirstFullWatchLater()")
    chromium_page.wait_for_timeout(300)
    expect(chromium_page.locator("#watchLaterCountBadge")).to_have_text("5")


def test_default_classic_notice_stays_out_of_operational_toast_stack(
    issue_98_server: tuple[str, Issue98Stub],
    chromium_page: Page,
) -> None:
    base_url, _ = issue_98_server

    chromium_page.goto(f"{base_url}/web/")

    expect(chromium_page.locator("#themeNotice")).to_be_visible()
    expect(chromium_page.locator("#toastContainer .toast-item")).to_have_count(0)
    assert chromium_page.evaluate("localStorage.getItem('obc.accentStyle')") == "classic"

    chromium_page.evaluate("window.showToast('业务提示')")
    toast = chromium_page.locator("#toastContainer .toast-item")
    expect(toast).to_have_count(1)
    expect(toast).to_have_text("业务提示")
    notice_box = chromium_page.locator("#themeNotice").bounding_box()
    toast_box = toast.bounding_box()
    assert notice_box is not None
    assert toast_box is not None
    assert notice_box["y"] + notice_box["height"] < toast_box["y"]


def test_existing_custom_hue_migrates_to_modern_without_classic_notice(
    issue_98_server: tuple[str, Issue98Stub],
    chromium_page: Page,
) -> None:
    base_url, _ = issue_98_server
    chromium_page.add_init_script("localStorage.setItem('obc.themeHue', '210')")

    chromium_page.goto(f"{base_url}/web/")

    expect(chromium_page.locator("html")).not_to_have_attribute("data-accent", "classic")
    expect(chromium_page.locator("#themeNotice")).to_be_hidden()
    assert chromium_page.evaluate("localStorage.getItem('obc.accentStyle')") == "modern"


def test_liked_delight_keeps_nonduplicate_desktop_actions_available(
    issue_98_server: tuple[str, Issue98Stub],
    chromium_page: Page,
) -> None:
    base_url, stub = issue_98_server
    stub.delights = [
        {
            **stub.delights[0],
            "state": "liked",
            "response_message": "好，这类多来点。",
        }
    ]

    chromium_page.goto(f"{base_url}/web/")
    expect(chromium_page.locator("#delightCount")).to_have_text("1/1")
    expect(chromium_page.locator("#delightStatus")).to_contain_text("好，这类多来点。")
    like = chromium_page.locator('[data-delight="like"]')
    expect(like).to_have_attribute("aria-pressed", "true")
    expect(like).to_be_disabled()
    for action in ("view", "dislike", "dismiss", "watch-later", "favorite", "chat"):
        expect(chromium_page.locator(f'[data-delight="{action}"]')).to_be_enabled()


def test_failed_desktop_delight_like_restores_unselected_action(
    issue_98_server: tuple[str, Issue98Stub],
    chromium_page: Page,
) -> None:
    base_url, stub = issue_98_server
    stub.delight_response_status = 500
    chromium_page.goto(f"{base_url}/web/")
    like = chromium_page.locator('[data-delight="like"]')

    like.click()
    assert stub.delight_response_received.wait(timeout=2)
    expect(like).to_have_attribute("aria-pressed", "false")
    expect(like).to_be_enabled()
    expect(chromium_page.locator("#delightStatus")).to_have_text("")


def test_side_drawer_aria_and_flex_allocation_stay_synchronized(
    issue_98_server: tuple[str, Issue98Stub],
    chromium_page: Page,
) -> None:
    base_url, _ = issue_98_server
    _load_desktop_with_closed_drawer(chromium_page, base_url, width=860)

    button = chromium_page.locator("#sideDrawerBtn")
    drawer = chromium_page.locator("#sideDrawer")
    layout = chromium_page.locator(".layout")
    expect(button).to_have_attribute("aria-expanded", "false")
    expect(drawer).to_have_attribute("aria-hidden", "true")
    closed_width = _rect(layout)["width"]
    button.click()
    expect(button).to_have_attribute("aria-expanded", "true")
    expect(drawer).to_have_attribute("aria-hidden", "false")
    chromium_page.wait_for_timeout(400)
    assert _rect(layout)["width"] < closed_width


@pytest.mark.parametrize("delta_x", [9, 10, 49, 50])
def test_delight_drag_and_switch_thresholds_in_chromium(
    issue_98_server: tuple[str, Issue98Stub],
    chromium_page: Page,
    delta_x: int,
) -> None:
    base_url, _ = issue_98_server
    _load_desktop_with_closed_drawer(chromium_page, base_url, width=1440)
    banner = chromium_page.locator("#delightBanner")
    count = chromium_page.locator("#delightCount")

    if delta_x in {9, 10}:
        _start_horizontal_drag(chromium_page, banner, -delta_x)
        classes = (banner.get_attribute("class") or "").split()
        if delta_x == 9:
            assert "is-dragging" not in classes
        else:
            assert "is-dragging" in classes
        chromium_page.mouse.up()
    else:
        _drag_horizontally(chromium_page, banner, -delta_x)

    if delta_x < 50:
        expect(count).to_have_text("1/2")
    else:
        expect(count).to_have_text("2/2")


def test_delight_uses_available_layout_width_without_horizontal_overflow(
    issue_98_server: tuple[str, Issue98Stub],
    chromium_page: Page,
) -> None:
    base_url, _ = issue_98_server
    _load_desktop_with_closed_drawer(chromium_page, base_url, width=860)

    closed_geometry = _delight_geometry(chromium_page)
    _assert_delight_fits_available_width(closed_geometry)

    chromium_page.locator("#sideDrawerBtn").click()
    chromium_page.wait_for_timeout(400)
    open_geometry = _delight_geometry(chromium_page)
    _assert_delight_fits_available_width(open_geometry)
    assert len(str(open_geometry["columns"]).split()) == 1


def test_delight_keeps_two_columns_when_wide_and_drawer_is_closed(
    issue_98_server: tuple[str, Issue98Stub],
    chromium_page: Page,
) -> None:
    base_url, _ = issue_98_server
    _load_desktop_with_closed_drawer(chromium_page, base_url, width=1440)

    geometry = _delight_geometry(chromium_page)
    _assert_delight_fits_available_width(geometry)
    tracks = str(geometry["columns"]).split()
    assert len(tracks) == 2
    assert all(track.endswith("px") for track in tracks)


def test_recommendation_feedback_is_immediate_stable_and_undoable(
    issue_98_server: tuple[str, Issue98Stub],
    chromium_page: Page,
) -> None:
    base_url, stub = issue_98_server
    chromium_page.goto(f"{base_url}/web/")
    cards = chromium_page.locator("#videoGrid .video-card")
    expect(cards).to_have_count(3)
    chromium_page.evaluate("() => document.fonts.ready")
    chromium_page.wait_for_timeout(500)
    second = cards.nth(1)
    second_identity = second.get_attribute("data-bvid")
    second_position = _card_position(second)

    first = cards.first
    first.locator('[data-action="like"]').click()

    expect(first.locator(".status-line")).to_contain_text("撤销")
    assert stub.feedback_posts == []
    assert second.get_attribute("data-bvid") == second_identity
    _assert_position_stable(_card_position(second), second_position)

    first.locator("[data-feedback-undo]").click()
    chromium_page.wait_for_timeout(500)
    assert stub.feedback_posts == []
    expect(first.locator('[data-action="like"]')).to_be_enabled()
    expect(first.locator(".status-line")).to_have_text("")

    stub.feedback_delay_seconds = 0.8
    first.locator('[data-action="dislike"]').click()
    assert stub.feedback_received.wait(timeout=8)
    expect(first.locator(".status-line")).to_contain_text("正在保存")
    second.locator('[data-action="like"]').click()
    expect(second.locator(".status-line")).to_contain_text("撤销")
    assert second.get_attribute("data-bvid") == second_identity
    _assert_position_stable(_card_position(second), second_position)
    second.locator("[data-feedback-undo]").click()
    assert len(stub.feedback_posts) == 1


def test_recommendation_feedback_failure_rolls_back_current_card(
    issue_98_server: tuple[str, Issue98Stub],
    chromium_page: Page,
) -> None:
    base_url, stub = issue_98_server
    stub.feedback_status = 500
    chromium_page.goto(f"{base_url}/web/")
    first = chromium_page.locator("#videoGrid .video-card").first
    like = first.locator('[data-action="like"]')

    like.click()
    assert stub.feedback_received.wait(timeout=8)

    expect(like).to_be_enabled(timeout=3000)
    expect(like).not_to_have_class("is-active")
    expect(first.locator(".status-line")).to_have_text("")
    expect(chromium_page.locator("#toastContainer .toast-item").first).to_contain_text("已恢复")


def test_recommendations_and_runtime_recover_without_leaving_init_gate(
    issue_98_server: tuple[str, Issue98Stub],
    chromium_page: Page,
) -> None:
    base_url, stub = issue_98_server
    request_counts = {"recommendations": 0, "runtime": 0}

    def fail_first_recommendation(route: Any) -> None:
        request_counts["recommendations"] += 1
        if request_counts["recommendations"] == 1:
            route.abort("failed")
            return
        route.continue_()

    def fail_initial_runtime_reads(route: Any) -> None:
        request_counts["runtime"] += 1
        if request_counts["runtime"] <= 2:
            route.abort("failed")
            return
        route.continue_()

    chromium_page.route("**/api/recommendations", fail_first_recommendation)
    chromium_page.route("**/api/runtime-status", fail_initial_runtime_reads)
    chromium_page.goto(f"{base_url}/web/")

    expect(chromium_page.locator("#videoGrid .video-card")).to_have_count(3, timeout=5000)
    expect(chromium_page.locator("#videoGrid .init-onboarding")).to_have_count(0)
    # The first successful snapshot has 30 items; reconciliation after delivery
    # rereads the runtime and must render the remaining 27, not that old value.
    assert stub.runtime_reread_received.wait(timeout=1.5)
    expect(chromium_page.locator("#poolAvailable")).to_contain_text("27")
    assert request_counts["recommendations"] >= 2
    assert request_counts["runtime"] >= 3


def test_fast_recommendations_render_before_slow_health_and_runtime_reconciles(
    issue_98_server: tuple[str, Issue98Stub],
    chromium_page: Page,
) -> None:
    base_url, stub = issue_98_server
    stub.health_delay_seconds = 4.0
    chromium_page.goto(f"{base_url}/web/", wait_until="domcontentloaded")

    expect(chromium_page.locator("#videoGrid .video-card")).to_have_count(3, timeout=1500)
    expect(chromium_page.locator("#poolAvailable")).to_contain_text("27", timeout=3000)
    assert stub.recommendation_reads == 1
    assert stub.runtime_reads >= 2


def test_runtime_reread_does_not_wait_for_slow_initial_runtime(
    issue_98_server: tuple[str, Issue98Stub],
    chromium_page: Page,
) -> None:
    base_url, stub = issue_98_server
    stub.runtime_first_delay_seconds = 4.0
    chromium_page.goto(f"{base_url}/web/", wait_until="domcontentloaded")

    assert stub.runtime_reread_received.wait(timeout=1.5), (
        f"runtime reread did not start: recommendations={stub.recommendation_reads}, "
        f"runtime={stub.runtime_reads}"
    )
    expect(chromium_page.locator("#videoGrid .video-card")).to_have_count(3, timeout=500)
    expect(chromium_page.locator("#poolAvailable")).to_contain_text("27", timeout=3000)
    assert stub.runtime_reads >= 2


def test_delayed_recommendations_schedule_autoload_after_failed_runtime_reread(
    issue_98_server: tuple[str, Issue98Stub],
    chromium_page: Page,
) -> None:
    base_url, stub = issue_98_server
    stub.recommendation_delay_seconds = 0.8
    stub.runtime_reread_status = 500
    chromium_page.set_viewport_size({"width": 1440, "height": 2200})
    chromium_page.goto(f"{base_url}/web/", wait_until="domcontentloaded")

    cards = chromium_page.locator("#videoGrid .video-card")
    expect(cards).to_have_count(3, timeout=3000)
    assert stub.runtime_reread_received.wait(timeout=1.5)
    assert chromium_page.evaluate(
        """() => {
          const sentinel = document.getElementById('loadMoreSentinel');
          const rect = sentinel.getBoundingClientRect();
          return rect.top <= window.innerHeight + 50 && rect.bottom >= -50;
        }"""
    )

    assert stub.append_received.wait(timeout=1.5), (
        "recommendation render did not re-check auto-load geometry"
    )
    assert stub.append_posts == [{"excluded_bvids": ["BV1ISSUE981", "BV1ISSUE982", "BV1ISSUE983"]}]


def test_recommendation_cover_requests_are_bounded_before_scroll(
    issue_98_server: tuple[str, Issue98Stub],
    chromium_page: Page,
) -> None:
    base_url, stub = issue_98_server
    stub.recommendations = _recommendations(80, image_backed=True)
    unexpected_upstream_requests: list[str] = []

    def block_synthetic_upstream(route: Any) -> None:
        unexpected_upstream_requests.append(route.request.url)
        route.abort()

    chromium_page.route("https://synthetic.invalid/**", block_synthetic_upstream)
    chromium_page.goto(f"{base_url}/web/", wait_until="domcontentloaded")

    cards = chromium_page.locator("#videoGrid .video-card")
    expect(cards).to_have_count(80)
    for index in range(4):
        expect(cards.nth(index).locator("img")).to_have_attribute("loading", "eager")
        expect(cards.nth(index).locator("img")).to_have_attribute("fetchpriority", "high")
    for index in range(4, 80):
        expect(cards.nth(index).locator("img")).to_have_attribute("loading", "lazy")
        expect(cards.nth(index).locator("img")).to_have_attribute("fetchpriority", "low")
    for index in range(80):
        src = cards.nth(index).locator("img").evaluate("image => image.src")
        assert src.startswith(f"{base_url}/api/image-proxy?")

    chromium_page.wait_for_timeout(400)
    assert unexpected_upstream_requests == []
    assert 4 <= stub.image_proxy_reads < 80


def test_interest_and_avoidance_probe_actions_are_immediate_and_undoable(
    issue_98_server: tuple[str, Issue98Stub],
    chromium_page: Page,
) -> None:
    base_url, stub = issue_98_server
    chromium_page.goto(f"{base_url}/web/")

    chromium_page.locator("#messagesBtn").click()
    drawer = chromium_page.locator("#messagesDrawer")
    expect(drawer).to_be_visible()
    interest = drawer.locator(".message-item.is-interest-probe")
    expect(interest).to_have_count(1)
    expect(interest.locator('[data-probe="confirm"]')).to_have_text("确认喜欢")
    expect(interest.locator('[data-probe="defer"]')).to_have_text("暂时搁置")
    expect(interest.locator('[data-probe="reject"]')).to_have_text("确认不喜欢")
    avoidance_message = drawer.locator(".message-item.is-avoidance-probe")
    expect(avoidance_message).to_have_count(1)
    expect(avoidance_message.locator('[data-probe="confirm"]')).to_have_text("确认避雷")
    expect(avoidance_message.locator('[data-probe="defer"]')).to_have_text("搁置避雷")
    expect(avoidance_message.locator('[data-probe="reject"]')).to_have_text("不是雷点")
    interest.locator('[data-probe="confirm"]').click()
    expect(interest).to_contain_text("撤销")
    assert stub.probe_posts == []
    interest.locator("[data-probe-undo]").click()
    expect(interest.locator('[data-probe="confirm"]')).to_be_visible()
    chromium_page.wait_for_timeout(500)
    assert stub.probe_posts == []

    drawer.locator('[data-close="messagesDrawer"]').first.click()
    chromium_page.evaluate("() => document.querySelector('#profileBtn').click()")
    profile = chromium_page.locator("#profilePage")
    expect(profile).to_be_visible()
    avoidance = profile.locator('[data-spec-domain="标题党"]')
    avoidance.locator('[data-spec-response="confirm"]').click()
    expect(avoidance).to_contain_text("撤销")
    # The browser fixture gives probe actions a 5s undo window. Allow the
    # delayed commit to cross that boundary even on a busy full-suite run.
    assert stub.probe_received.wait(timeout=8)
    assert stub.probe_posts == [
        {
            "path": "/api/avoidance-probes/respond",
            "domain": "标题党",
            "response": "confirm",
            "message": "",
        }
    ]
    expect(avoidance).to_contain_text("已确认避雷「标题党」")

    stub.probe_status = 500
    stub.probe_received.clear()
    interest_row = profile.locator('[data-spec-domain="系统设计"]')
    interest_row.locator('[data-spec-response="reject"]').click()
    assert stub.probe_received.wait(timeout=8)
    expect(interest_row.locator('[data-spec-response="reject"]')).to_be_visible()
    expect(chromium_page.locator("#toastContainer .toast-item").first).to_contain_text("已恢复")


def test_committed_probe_toast_names_its_domain(
    issue_98_server: tuple[str, Issue98Stub],
    chromium_page: Page,
) -> None:
    base_url, stub = issue_98_server
    chromium_page.goto(f"{base_url}/web/")

    chromium_page.locator("#messagesBtn").click()
    interest = chromium_page.locator("#messagesDrawer .message-item.is-interest-probe")
    expect(interest).to_have_count(1)
    interest.locator('[data-probe="confirm"]').click()

    assert stub.probe_received.wait(timeout=8)
    expect(chromium_page.locator("#toastContainer .toast-item").first).to_contain_text("系统设计")
