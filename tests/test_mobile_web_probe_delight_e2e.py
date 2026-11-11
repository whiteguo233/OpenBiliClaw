from __future__ import annotations

import json
import mimetypes
import threading
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")
Page = playwright_api.Page
expect = playwright_api.expect
sync_playwright = playwright_api.sync_playwright

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[1]


class MobileWebStub:
    def __init__(self) -> None:
        self.notifications = [
            {
                "type": "interest.probe",
                "domain": "系统设计",
                "message": "你似乎常看系统设计。",
            },
            {
                "type": "avoidance.probe",
                "domain": "标题党",
                "message": "你似乎会避开标题党。",
            },
        ]
        self.post_counts = {"interest.probe": 0, "avoidance.probe": 0}
        self.post_received = {
            "interest.probe": threading.Event(),
            "avoidance.probe": threading.Event(),
        }
        self.response_gates = {
            "interest.probe": threading.Event(),
            "avoidance.probe": threading.Event(),
        }
        self.response_status = {"interest.probe": 200, "avoidance.probe": 200}
        self.delight = {
            "bvid": "BV1DELIGHTLIKED",
            "content_url": "https://www.bilibili.com/video/BV1DELIGHTLIKED",
            "source_platform": "bilibili",
            "title": "会让你意外喜欢的一条",
            "delight_reason": "它和你最近的兴趣有一条不明显的连接。",
            "delight_score": 0.9,
            "state": "pending",
            "response_message": "",
        }
        self.delight_response_status = 200
        self.delight_posts: list[dict[str, Any]] = []
        self.delight_post_received = threading.Event()
        self.recommendations: list[dict[str, Any]] = []
        self.reshuffle_posts: list[dict[str, Any]] = []
        self.runtime_status: dict[str, Any] = {
            "initialized": True,
            "pool_available_count": 0,
            "pool_size": 0,
            "pool_refresh_state": "idle",
            "unread_count": 2,
        }
        self.lock = threading.Lock()

    def release(self, probe_type: str, *, status: int) -> None:
        self.response_status[probe_type] = status
        self.response_gates[probe_type].set()

    def release_all(self) -> None:
        for gate in self.response_gates.values():
            gate.set()


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


@pytest.fixture()
def mobile_web_server() -> tuple[str, MobileWebStub]:
    state = MobileWebStub()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in {"/m", "/m/", "/m/index.html"}:
                return self._serve_file(ROOT / "src/openbiliclaw/web/index.html", "text/html")
            if path.startswith("/m/css/"):
                relative = path.removeprefix("/m/css/")
                return self._serve_file(ROOT / "src/openbiliclaw/web/css" / relative)
            if path.startswith("/m/js/"):
                relative = path.removeprefix("/m/js/")
                return self._serve_file(ROOT / "src/openbiliclaw/web/js" / relative)
            if path.startswith("/shared/"):
                relative = path.removeprefix("/shared/")
                return self._serve_file(ROOT / "src/openbiliclaw/web/shared" / relative)
            if path == "/m/manifest.json":
                return self._serve_file(
                    ROOT / "src/openbiliclaw/web/manifest.json",
                    "application/manifest+json",
                )
            if path == "/api/auth/status":
                return _json_response(self, {"enabled": False, "authenticated": True})
            if path == "/api/health":
                return _json_response(self, {"ok": True})
            if path == "/api/recommendations":
                with state.lock:
                    recommendations = list(state.recommendations)
                return _json_response(self, {"items": recommendations})
            if path == "/api/runtime-status":
                with state.lock:
                    runtime_status = dict(state.runtime_status)
                return _json_response(self, runtime_status)
            if path == "/api/activity-feed":
                return _json_response(self, {"items": [], "has_more": False})
            if path == "/api/notifications/pending":
                return _json_response(self, {"items": []})
            if path == "/api/interest-probes/pending":
                return _json_response(
                    self,
                    {"items": [state.notifications[0]]},
                )
            if path == "/api/avoidance-probes/pending":
                return _json_response(
                    self,
                    {"items": [state.notifications[1]]},
                )
            if path == "/api/delight/pending-batch":
                with state.lock:
                    delight = dict(state.delight)
                return _json_response(self, {"items": [delight]})
            if path == "/api/chat/turns":
                return _json_response(self, {"items": []})
            return _json_response(self, {}, 404)

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                payload = {}
            if path == "/api/delight/respond":
                with state.lock:
                    state.delight_posts.append(payload)
                    status = state.delight_response_status
                    if status < 400 and payload.get("response") == "like":
                        state.delight["state"] = "liked"
                        state.delight["response_message"] = "好，这类多来点。"
                state.delight_post_received.set()
                return _json_response(self, {"ok": status < 400}, status)
            if path == "/api/recommendations/reshuffle":
                with state.lock:
                    state.reshuffle_posts.append(payload)
                return _json_response(self, {"items": []})
            probe_type = {
                "/api/interest-probes/respond": "interest.probe",
                "/api/avoidance-probes/respond": "avoidance.probe",
            }.get(path)
            if probe_type is None:
                return _json_response(self, {"ok": True})

            with state.lock:
                state.post_counts[probe_type] += 1
            state.post_received[probe_type].set()
            if not state.response_gates[probe_type].wait(timeout=10):
                return _json_response(self, {"ok": False, "error": "gate_timeout"}, 504)
            status = state.response_status[probe_type]
            return _json_response(
                self,
                {"ok": status < 400, "action": "confirmed"},
                status,
            )

        def _serve_file(self, path: Path, content_type: str | None = None) -> None:
            if not path.is_file():
                return _json_response(self, {"error": "not_found"}, 404)
            body = path.read_bytes()
            self.send_response(200)
            self.send_header(
                "Content-Type",
                content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            with suppress(BrokenPipeError):
                self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        state.release_all()
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture()
def chromium_page() -> Page:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.add_init_script(
            """
            window.WebSocket = class FakeWebSocket {
              static OPEN = 1;
              constructor() {
                this.readyState = FakeWebSocket.OPEN;
                window.__fakeSockets = window.__fakeSockets || [];
                window.__fakeSockets.push(this);
                setTimeout(() => this.onopen?.({ type: "open" }), 0);
              }
              close() { this.readyState = 3; }
            };
            window.__openedUrls = [];
            window.open = (url) => { window.__openedUrls.push(url); return null; };
            window.__emitRuntimeEvent = (payload) => {
              for (const socket of window.__fakeSockets || []) {
                if (socket.readyState === window.WebSocket.OPEN) {
                  socket.onmessage?.({ data: JSON.stringify(payload) });
                }
              }
            };
            """
        )
        yield page
        browser.close()


def _probe_card(page: Page, domain: str) -> Any:
    return page.locator(f'[data-probe-domain="{domain}"]')


def _open_messages(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/m/#/chat")
    expect(page.locator(".badge-count")).to_have_attribute("data-count", "2")
    page.get_by_role("button", name="查看消息").click()
    expect(page.locator(".messages-overlay")).to_have_class("messages-overlay open")


def _assert_all_probe_actions_disabled(card: Any, disabled: bool) -> None:
    buttons = card.locator("button")
    expect(buttons).to_have_count(4)
    for index in range(4):
        if disabled:
            expect(buttons.nth(index)).to_be_disabled()
        else:
            expect(buttons.nth(index)).to_be_enabled()


def _assert_liked_delight(page: Page) -> None:
    like = page.locator('.delight-actions [data-delight-action="like"]')
    expect(page.locator(".delight-result-state")).to_contain_text("好，这类多来点。")
    expect(page.locator(".delight-actions")).to_be_visible()
    expect(like).to_have_attribute("aria-pressed", "true")
    expect(like).to_be_disabled()
    for action in ("view", "watch-later", "favorite", "reject", "chat"):
        expect(page.locator(f'[data-delight-action="{action}"]')).to_be_enabled()


def test_mobile_empty_reshuffle_preserves_visible_recommendations(
    mobile_web_server: tuple[str, MobileWebStub],
    chromium_page: Page,
) -> None:
    base_url, stub = mobile_web_server
    stub.recommendations = [
        {
            "id": 1,
            "bvid": "BV1KEEP1",
            "title": "正在看的推荐一",
            "up_name": "UP 甲",
            "expression": "第一条推荐理由。",
            "topic_label": "主题一",
            "source_platform": "bilibili",
        },
        {
            "id": 2,
            "bvid": "BV1KEEP2",
            "title": "正在看的推荐二",
            "up_name": "UP 乙",
            "expression": "第二条推荐理由。",
            "topic_label": "主题二",
            "source_platform": "bilibili",
        },
    ]
    stub.runtime_status = {
        **stub.runtime_status,
        "initialized": True,
        "pool_available_count": 12,
    }

    chromium_page.goto(f"{base_url}/m/#/recommend")
    cards = chromium_page.locator(".card")
    expect(cards).to_have_count(2)
    titles_before = cards.locator(".card-title").all_inner_texts()

    chromium_page.locator(".recommend-refresh-btn").click()

    expect(chromium_page.locator(".recommend-action-note")).to_have_text(
        "这次暂时没换出新内容，已保留当前推荐。"
    )
    expect(cards).to_have_count(2)
    assert cards.locator(".card-title").all_inner_texts() == titles_before
    assert stub.reshuffle_posts == [{"excluded_bvids": ["BV1KEEP1", "BV1KEEP2"]}]


@pytest.mark.parametrize(
    ("probe_type", "domain", "button_action"),
    [
        ("interest.probe", "系统设计", "confirm"),
        ("avoidance.probe", "标题党", "confirm"),
    ],
)
def test_mobile_probe_stays_busy_when_overlay_is_rebuilt(
    mobile_web_server: tuple[str, MobileWebStub],
    chromium_page: Page,
    probe_type: str,
    domain: str,
    button_action: str,
) -> None:
    base_url, stub = mobile_web_server
    _open_messages(chromium_page, base_url)
    card = _probe_card(chromium_page, domain)
    card.locator(f'[data-probe="{button_action}"]').click()
    assert stub.post_received[probe_type].wait(timeout=2)

    chromium_page.locator(".messages-close").click()
    chromium_page.get_by_role("button", name="查看消息").click()
    rebuilt = _probe_card(chromium_page, domain)
    expect(rebuilt).to_be_visible()
    _assert_all_probe_actions_disabled(rebuilt, True)
    expect(rebuilt).to_have_attribute("aria-busy", "true")

    rebuilt.locator(f'[data-probe="{button_action}"]').click(force=True)
    chromium_page.wait_for_timeout(200)
    assert stub.post_counts[probe_type] == 1

    stub.release(probe_type, status=200)
    expect(_probe_card(chromium_page, domain)).to_have_count(0)


@pytest.mark.parametrize(
    ("probe_type", "domain", "button_action"),
    [
        ("interest.probe", "系统设计", "confirm"),
        ("avoidance.probe", "标题党", "confirm"),
    ],
)
def test_mobile_probe_failure_restores_rebuilt_card_for_retry(
    mobile_web_server: tuple[str, MobileWebStub],
    chromium_page: Page,
    probe_type: str,
    domain: str,
    button_action: str,
) -> None:
    base_url, stub = mobile_web_server
    page_errors: list[str] = []
    chromium_page.on("pageerror", lambda error: page_errors.append(str(error)))
    _open_messages(chromium_page, base_url)
    _probe_card(chromium_page, domain).locator(f'[data-probe="{button_action}"]').click()
    assert stub.post_received[probe_type].wait(timeout=2)

    chromium_page.locator(".messages-close").click()
    chromium_page.get_by_role("button", name="查看消息").click()
    rebuilt = _probe_card(chromium_page, domain)
    expect(rebuilt).to_have_attribute("aria-busy", "true")

    stub.release(probe_type, status=500)
    expect(rebuilt).to_be_visible()
    expect(rebuilt).to_have_attribute("aria-busy", "false")
    _assert_all_probe_actions_disabled(rebuilt, False)
    assert stub.post_counts[probe_type] == 1
    assert page_errors == []


def test_mobile_liked_delight_converges_after_click_reload_and_stream(
    mobile_web_server: tuple[str, MobileWebStub],
    chromium_page: Page,
) -> None:
    base_url, stub = mobile_web_server
    chromium_page.goto(f"{base_url}/m/#/recommend")
    like = chromium_page.locator('.delight-actions [data-delight-action="like"]')
    expect(like).to_have_attribute("aria-pressed", "false")
    expect(like).to_be_enabled()

    like.click()
    assert stub.delight_post_received.wait(timeout=2)
    assert len(stub.delight_posts) == 1
    submitted = dict(stub.delight_posts[0])
    request_id = submitted.pop("request_id", "")
    assert str(UUID(str(request_id))) == request_id
    assert submitted == {
        "bvid": "BV1DELIGHTLIKED",
        "response": "like",
        "title": "会让你意外喜欢的一条",
        "message": "",
    }
    _assert_liked_delight(chromium_page)

    chromium_page.reload()
    _assert_liked_delight(chromium_page)

    with stub.lock:
        stub.delight["state"] = "pending"
        stub.delight["response_message"] = ""
    chromium_page.reload()
    like = chromium_page.locator('.delight-actions [data-delight-action="like"]')
    expect(like).to_have_attribute("aria-pressed", "false")
    expect(like).to_be_enabled()
    expect(chromium_page.locator(".delight-result-state")).to_have_count(0)
    chromium_page.wait_for_function("() => (window.__fakeSockets || []).length > 0")
    chromium_page.evaluate(
        """() => window.__emitRuntimeEvent({
          type: "delight.liked",
          data: { bvid: "BV1DELIGHTLIKED", message: "好，这类多来点。" },
        })"""
    )
    _assert_liked_delight(chromium_page)


def test_mobile_failed_like_restores_unselected_actions(
    mobile_web_server: tuple[str, MobileWebStub],
    chromium_page: Page,
) -> None:
    base_url, stub = mobile_web_server
    stub.delight_response_status = 500
    chromium_page.goto(f"{base_url}/m/#/recommend")
    like = chromium_page.locator('.delight-actions [data-delight-action="like"]')

    like.click()
    assert stub.delight_post_received.wait(timeout=2)
    expect(like).to_have_attribute("aria-pressed", "false")
    expect(like).to_be_enabled()
    expect(chromium_page.locator(".delight-result-state")).to_have_count(0)
    expect(chromium_page.locator(".delight-actions")).to_be_visible()


def _delight_tray_box(page: Page) -> dict[str, float]:
    tray = page.locator(".delight-tray")
    expect(tray).to_be_visible()
    box = tray.bounding_box()
    assert box is not None
    return box


def _drag_delight_tray(page: Page, dx: float) -> None:
    """Press on the tray body and release `dx` px away (0 = a plain tap)."""

    box = _delight_tray_box(page)
    start_x = box["x"] + box["width"] / 2
    start_y = box["y"] + box["height"] / 2
    page.mouse.move(start_x, start_y)
    page.mouse.down()
    if dx:
        page.mouse.move(start_x + dx, start_y, steps=5)
    page.mouse.up()


def test_mobile_delight_tap_on_card_body_opens_content(
    mobile_web_server: tuple[str, MobileWebStub],
    chromium_page: Page,
) -> None:
    """issue #126: the surprise card opens on a whole-card tap, like normal cards."""

    base_url, stub = mobile_web_server
    chromium_page.goto(f"{base_url}/m/#/recommend")
    expect(chromium_page.locator(".delight-tray")).to_be_visible()

    _drag_delight_tray(chromium_page, dx=0)

    chromium_page.wait_for_function("() => (window.__openedUrls || []).length > 0")
    assert chromium_page.evaluate("() => window.__openedUrls") == [
        "https://www.bilibili.com/video/BV1DELIGHTLIKED"
    ]
    assert [post["response"] for post in stub.delight_posts] == ["view"]


def test_mobile_delight_close_persists_dismiss_before_removing_card(
    mobile_web_server: tuple[str, MobileWebStub],
    chromium_page: Page,
) -> None:
    base_url, stub = mobile_web_server
    chromium_page.goto(f"{base_url}/m/#/recommend")
    close = chromium_page.get_by_role("button", name="看过了，不再推荐")
    expect(close).to_be_visible()

    close.click()

    assert stub.delight_post_received.wait(timeout=2)
    assert [post["response"] for post in stub.delight_posts] == ["dismiss"]
    expect(chromium_page.locator(".delight-tray")).to_have_count(0)


def test_mobile_delight_close_failure_keeps_card_for_retry(
    mobile_web_server: tuple[str, MobileWebStub],
    chromium_page: Page,
) -> None:
    base_url, stub = mobile_web_server
    stub.delight_response_status = 500
    chromium_page.goto(f"{base_url}/m/#/recommend")
    close = chromium_page.get_by_role("button", name="看过了，不再推荐")

    close.click()

    assert stub.delight_post_received.wait(timeout=2)
    assert [post["response"] for post in stub.delight_posts] == ["dismiss"]
    expect(chromium_page.locator(".delight-tray")).to_be_visible()
    expect(chromium_page.get_by_role("button", name="操作失败，请重试")).to_be_enabled()


def test_mobile_delight_drag_past_dead_zone_does_not_open_content(
    mobile_web_server: tuple[str, MobileWebStub],
    chromium_page: Page,
) -> None:
    """A finger drag must never be mistaken for a tap (误触 guard for the 10px dead zone)."""

    base_url, stub = mobile_web_server
    chromium_page.goto(f"{base_url}/m/#/recommend")
    expect(chromium_page.locator(".delight-tray")).to_be_visible()

    _drag_delight_tray(chromium_page, dx=-30)

    chromium_page.wait_for_timeout(300)
    assert stub.delight_posts == []
    assert chromium_page.evaluate("() => window.__openedUrls") == []


def test_mobile_delight_feedback_button_does_not_also_open_content(
    mobile_web_server: tuple[str, MobileWebStub],
    chromium_page: Page,
) -> None:
    """Tapping a feedback action must not bubble into the new whole-card open."""

    base_url, stub = mobile_web_server
    chromium_page.goto(f"{base_url}/m/#/recommend")
    chromium_page.locator('.delight-actions [data-delight-action="like"]').click()

    assert stub.delight_post_received.wait(timeout=2)
    assert [post["response"] for post in stub.delight_posts] == ["like"]
    assert chromium_page.evaluate("() => window.__openedUrls") == []


def test_mobile_delight_refill_event_replaces_consumed_queue_without_reload(
    mobile_web_server: tuple[str, MobileWebStub],
    chromium_page: Page,
) -> None:
    base_url, stub = mobile_web_server
    chromium_page.goto(f"{base_url}/m/#/recommend")
    expect(chromium_page.locator(".delight-title")).to_contain_text("会让你意外喜欢的一条")
    with stub.lock:
        stub.delight.update(bvid="BVnew", title="自动补位的新内容")
    chromium_page.evaluate("() => window.__emitRuntimeEvent({type: 'delight.refreshed'})")
    expect(chromium_page.locator(".delight-title")).to_contain_text("自动补位的新内容")


def test_mobile_delight_refill_preserves_composer_until_blur(
    mobile_web_server: tuple[str, MobileWebStub],
    chromium_page: Page,
) -> None:
    base_url, stub = mobile_web_server
    chromium_page.goto(f"{base_url}/m/#/recommend")
    chromium_page.locator('[data-delight-action="chat"]').click()
    composer = chromium_page.locator(".delight-composer-input")
    composer.fill("正在输入的草稿")
    with stub.lock:
        stub.delight.update(bvid="BVnew", title="稍后显示的新内容")
    chromium_page.evaluate("() => window.__emitRuntimeEvent({type: 'delight.refreshed'})")
    expect(composer).to_be_focused()
    expect(composer).to_have_value("正在输入的草稿")
    with chromium_page.expect_response("**/api/delight/pending-batch"):
        composer.evaluate("element => element.blur()")
    expect(chromium_page.locator(".delight-title")).to_contain_text("稍后显示的新内容")


def test_mobile_late_initial_delight_read_cannot_erase_a_new_refill(
    mobile_web_server: tuple[str, MobileWebStub],
    chromium_page: Page,
) -> None:
    base_url, stub = mobile_web_server
    held: list[Any] = []

    def hold_first(route: Any) -> None:
        if not held:
            held.append(route)
        else:
            route.continue_()

    chromium_page.route("**/api/delight/pending-batch", hold_first)
    with chromium_page.expect_request("**/api/delight/pending-batch"):
        chromium_page.goto(f"{base_url}/m/#/recommend")
    with stub.lock:
        stub.delight.update(bvid="BVnew", title="先返回的新补位")
    chromium_page.evaluate("() => window.__emitRuntimeEvent({type: 'delight.refreshed'})")
    expect(chromium_page.locator(".delight-title")).to_contain_text("先返回的新补位")
    with chromium_page.expect_response("**/api/delight/pending-batch") as late:
        held[0].fulfill(status=200, content_type="application/json", body='{"items": []}')
    late.value.finished()
    # Let the completed stale body's promise chain run before asserting that
    # it cannot undo the newer committed queue already visible in the page.
    chromium_page.wait_for_timeout(200)
    expect(chromium_page.locator(".delight-title")).to_contain_text("先返回的新补位")


def test_mobile_same_delight_keeps_saved_draft_after_deferred_refresh(
    mobile_web_server: tuple[str, MobileWebStub],
    chromium_page: Page,
) -> None:
    base_url, stub = mobile_web_server
    chromium_page.goto(f"{base_url}/m/#/recommend")
    chromium_page.locator('[data-delight-action="chat"]').click()
    composer = chromium_page.locator(".delight-composer-input")
    composer.fill("下次还要继续写的草稿")
    with stub.lock:
        stub.delight["title"] = "同一条内容的新标题"
    chromium_page.evaluate("() => window.__emitRuntimeEvent({type: 'delight.refreshed'})")
    with chromium_page.expect_response("**/api/delight/pending-batch"):
        composer.evaluate("element => element.blur()")
    expect(chromium_page.locator(".delight-title")).to_contain_text("同一条内容的新标题")
    chromium_page.locator('[data-delight-action="chat"]').click()
    expect(chromium_page.locator(".delight-composer-input")).to_have_value("下次还要继续写的草稿")
