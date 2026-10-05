"""Desktop web 推荐列表的稳定性契约（真实 chromium）。

群反馈：开着「滚动到底自动加载」浏览时列表总在乱跳，尤其是自动加载消耗完库存、
后台补货的时候，跳完还会重新排序。两个成因都在这里回归：

1. `refresh.pool_updated` 会经 refreshInitStatus 拽起一次 renderAll，而 renderVideos
   曾经是 `grid.replaceChildren(...)` —— 整表重建把用户正在看的卡片全部销毁，
   浏览器丢掉滚动锚点（跳动）、首屏之外的懒加载封面回落成占位。
2. 切走标签页再切回来会触发再水合，而后台再水合曾经仍会读取 `/api/recommendations`；
   这个 GET 在首屏库存较薄时可能顺手补池，且只返回最新 top 窗口。已有卡片时后台
   必须改为状态-only，不能让候选池被无意消费，也不能覆盖本地列表。

两条都用真实浏览器验证：卡片 DOM 节点必须原地存活，顺序、数量、滚动位置不变。
"""

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
Page = playwright_api.Page
expect = playwright_api.expect
sync_playwright = playwright_api.sync_playwright

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[1]
CARD_COUNT = 24
APPEND_COUNT = 10


def _recommendations(prefix: str, count: int) -> list[dict[str, Any]]:
    base_id = 0 if prefix == "A" else 1000
    return [
        {
            "id": base_id + index,
            "bvid": f"BV1STABLE{prefix}{index}",
            "content_id": f"BV1STABLE{prefix}{index}",
            "content_url": f"https://www.bilibili.com/video/BV1STABLE{prefix}{index}",
            "source_platform": "bilibili",
            "title": f"稳定性卡片 {prefix}-{index}",
            "up_name": f"UP {index}",
            "topic_label": "列表稳定性",
            "expression": f"第 {prefix}-{index} 张卡片的推荐理由，占位文案撑起卡片高度。",
        }
        for index in range(1, count + 1)
    ]


class StabilityStub:
    def __init__(self) -> None:
        self.recommendation_gets = 0
        self.append_received = threading.Event()
        self.append_release = threading.Event()
        self.append_release.set()


def _json_response(handler: BaseHTTPRequestHandler, payload: Any, status: int = 200) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    with suppress(BrokenPipeError):
        handler.wfile.write(body)


@pytest.fixture()
def stability_server() -> tuple[str, StabilityStub]:
    state = StabilityStub()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path.startswith("/shared/"):
                rel = path.removeprefix("/shared/")
                return self._serve_file(ROOT / "src/openbiliclaw/web/shared" / rel)
            if path in {"/web", "/web/", "/web/index.html"}:
                return self._serve_file(
                    ROOT / "src/openbiliclaw/web/desktop/index.html", "text/html"
                )
            if path.startswith("/web/assets/"):
                rel = path.removeprefix("/web/assets/")
                return self._serve_file(ROOT / "src/openbiliclaw/web/desktop/assets" / rel)
            if path == "/api/ping":
                return _json_response(self, {"ok": True})
            if path == "/api/health":
                return _json_response(self, {"ok": True, "embedding_ready": True})
            if path == "/api/auth/status":
                return _json_response(self, {"enabled": False, "authenticated": True})
            if path == "/api/recommendations":
                state.recommendation_gets += 1
                items = _recommendations("A", CARD_COUNT)
                # 第二次以后返回「后端重新排过序的最新窗口」：本地已经加载出来的
                # 列表绝不能被它顶掉重排。
                if state.recommendation_gets > 1:
                    items = list(reversed(items))
                return _json_response(self, {"items": items})
            if path == "/api/recommendations/platform-availability":
                return _json_response(
                    self, {"total_available": 40, "by_platform": {"bilibili": 40}}
                )
            if path == "/api/runtime-status":
                return _json_response(
                    self,
                    {
                        "initialized": True,
                        "pool_available_count": 40,
                        "pool_size": 40,
                        "pool_refresh_state": "idle",
                        "pool_source_shares": {"bilibili": 1.0},
                        "configured_sources": {"bilibili": {"enabled": True}},
                        "unread_count": 0,
                    },
                )
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
            if path == "/api/profile-summary":
                return _json_response(self, {"initialized": True})
            if path == "/api/activity-feed":
                return _json_response(self, {"items": [], "has_more": False, "next_cursor": ""})
            if path in {"/api/delight/pending-batch", "/api/notifications/pending"}:
                return _json_response(self, {"items": []})
            if path == "/api/chat/turns":
                return _json_response(self, {"items": []})
            if path == "/api/qr-info":
                return _json_response(self, {"lan_ip": "127.0.0.1"})
            return _json_response(self, {}, 404)

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            length = int(self.headers.get("Content-Length", "0"))
            if length:
                self.rfile.read(length)
            if path == "/api/recommendations/append":
                state.append_received.set()
                state.append_release.wait(timeout=5.0)
                return _json_response(self, {"items": _recommendations("B", APPEND_COUNT)})
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
            with suppress(BrokenPipeError):
                self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        thread.join(timeout=5)


# 桌面走的是 socket.addEventListener("message", …)，所以假 socket 必须真的登记
# 监听器再派发，否则事件根本进不了 handleRuntimeEvent（见 memory: runtime-stream
# E2E injection）。同时把 document.hidden 变成可写的，用来模拟切标签页。
_INIT_SCRIPT = """
window.__obcSockets = [];
window.WebSocket = class FakeWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 3;
  constructor(url) {
    this.url = url;
    this.readyState = 1;
    this._listeners = {};
    window.__obcSockets.push(this);
    setTimeout(() => this._emit('open', { type: 'open' }), 0);
  }
  addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); }
  removeEventListener(type, fn) {
    const list = this._listeners[type] || [];
    const index = list.indexOf(fn);
    if (index >= 0) list.splice(index, 1);
  }
  _emit(type, event) {
    for (const fn of (this._listeners[type] || []).slice()) fn(event);
    const inline = this['on' + type];
    if (typeof inline === 'function') inline(event);
  }
  send() {}
  close() { this.readyState = 3; this._emit('close', { type: 'close' }); }
};
window.__obcPushRuntime = (payload) => {
  const live = window.__obcSockets.filter((socket) => socket.readyState === 1);
  const socket = live[live.length - 1];
  if (!socket) throw new Error('no live runtime socket');
  socket._emit('message', { data: JSON.stringify(payload) });
};
let __obcHidden = false;
Object.defineProperty(document, 'hidden', { get: () => __obcHidden, configurable: true });
Object.defineProperty(document, 'visibilityState', {
  get: () => (__obcHidden ? 'hidden' : 'visible'),
  configurable: true,
});
window.__obcSetHidden = (value) => {
  __obcHidden = Boolean(value);
  document.dispatchEvent(new Event('visibilitychange'));
};
"""


@pytest.fixture()
def chromium_page() -> Page:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.add_init_script(_INIT_SCRIPT)
        yield page
        browser.close()


def _stamp_cards(page: Page) -> None:
    """给当前每张卡片打一个身份标记；标记只存在于 JS 对象上，重建即丢失。"""
    page.evaluate(
        """() => {
          document.querySelectorAll('#videoGrid .video-card:not(.is-skeleton)')
            .forEach((card, index) => { card.__obcStamp = `stamp-${index}`; });
        }"""
    )


def _card_report(page: Page) -> dict[str, Any]:
    return page.evaluate(
        """() => {
          const cards = [...document.querySelectorAll('#videoGrid .video-card:not(.is-skeleton)')];
          return {
            count: cards.length,
            stamps: cards.map((card) => card.__obcStamp || null),
            titles: cards.map((card) => card.querySelector('.video-title').textContent),
            scrollY: window.scrollY,
          };
        }"""
    )


def _load_and_append(page: Page, base_url: str, stub: StabilityStub) -> None:
    page.goto(f"{base_url}/web/")
    expect(page.locator("#videoGrid .video-card:not(.is-skeleton)")).to_have_count(
        CARD_COUNT, timeout=8000
    )
    # 滚到底触发一次自动加载，制造「本地列表比后端窗口更长」的真实状态。
    page.evaluate("() => window.scrollTo({ top: document.body.scrollHeight, behavior: 'instant' })")
    page.evaluate("() => window.dispatchEvent(new Event('scroll'))")
    assert stub.append_received.wait(timeout=6.0), "滚到底没有触发自动加载"
    expect(page.locator("#videoGrid .video-card:not(.is-skeleton)")).to_have_count(
        CARD_COUNT + APPEND_COUNT, timeout=6000
    )


def test_pool_refill_event_keeps_loaded_cards_and_scroll_position(
    stability_server: tuple[str, StabilityStub],
    chromium_page: Page,
) -> None:
    base_url, stub = stability_server
    _load_and_append(chromium_page, base_url, stub)

    _stamp_cards(chromium_page)
    before = _card_report(chromium_page)
    assert all(stamp is not None for stamp in before["stamps"])

    # 后台补货：一轮 refresh 会连发多次 pool_updated。真实后端的提交后库存事件
    # 带 pool_status_version / platform_available_counts（见 api/app.py
    # _broadcast_recommendation_pool_status）；头部库存只跟随带版本的已提交快照，
    # 不再吃无版本事件的裸 pool_available_count（normalizeRuntimeStatus 优先
    # state.platformAvailability），所以这里必须按真实线上格式注入。
    for version, available in enumerate((52, 61, 70), start=1):
        chromium_page.evaluate(
            """(payload) => window.__obcPushRuntime(payload)""",
            {
                "type": "refresh.pool_updated",
                "phase": "done",
                "pool_available_count": available,
                "platform_available_counts": {"bilibili": available},
                "pool_status_version": version,
                "message": f"候选池补充到 {available} 条",
            },
        )
        chromium_page.wait_for_timeout(250)
    chromium_page.wait_for_timeout(600)

    after = _card_report(chromium_page)
    assert after["count"] == before["count"]
    assert after["titles"] == before["titles"], "补货事件之后列表顺序变了"
    assert after["stamps"] == before["stamps"], (
        "补货事件重建了推荐卡片 DOM —— 整表重建正是列表乱跳的成因"
    )
    assert abs(after["scrollY"] - before["scrollY"]) < 2, "补货事件把滚动位置带跑了"
    # 库存数字本身仍要跟着事件走，重绘收窄不能把头部一起冻住。
    assert chromium_page.locator("#metricPool").text_content().strip() == "70"


def test_auto_append_keeps_scroll_position_when_filter_tab_retains_focus(
    stability_server: tuple[str, StabilityStub],
    chromium_page: Page,
) -> None:
    """点过平台 Tab 后用滚轮下滑不会清掉焦点；续页重绘 Tab 时必须只恢复焦点，
    不能让浏览器为离屏 Tab 自动改写 scrollY。"""
    base_url, stub = stability_server
    chromium_page.goto(f"{base_url}/web/")
    expect(chromium_page.locator("#videoGrid .video-card:not(.is-skeleton)")).to_have_count(
        CARD_COUNT, timeout=8000
    )

    active_filter = chromium_page.locator('#filterRow .chip[data-filter="全部"]')
    active_filter.click()
    expect(active_filter).to_be_focused()

    stub.append_release.clear()
    try:
        chromium_page.evaluate(
            "() => window.scrollTo({ top: document.body.scrollHeight, behavior: 'instant' })"
        )
        chromium_page.evaluate("() => window.dispatchEvent(new Event('scroll'))")
        assert stub.append_received.wait(timeout=6.0), "滚到底没有触发自动加载"
        before = chromium_page.evaluate(
            """() => ({
              scrollY: window.scrollY,
              activeFilter: document.activeElement?.dataset?.filter || '',
            })"""
        )
        assert before["activeFilter"] == "全部", "滚轮浏览不应清掉平台 Tab 的键盘焦点"

        stub.append_release.set()
        expect(chromium_page.locator("#videoGrid .video-card:not(.is-skeleton)")).to_have_count(
            CARD_COUNT + APPEND_COUNT, timeout=6000
        )
        chromium_page.wait_for_timeout(250)
        after = chromium_page.evaluate(
            """() => ({
              scrollY: window.scrollY,
              activeFilter: document.activeElement?.dataset?.filter || '',
            })"""
        )
    finally:
        stub.append_release.set()

    assert after["activeFilter"] == "全部", "重绘后仍应保留平台 Tab 的键盘焦点"
    assert abs(after["scrollY"] - before["scrollY"]) < 2, "自动续页把页面跳回了平台 Tab"


def test_tab_resume_hydration_preserves_locally_loaded_cards(
    stability_server: tuple[str, StabilityStub],
    chromium_page: Page,
) -> None:
    base_url, stub = stability_server
    _load_and_append(chromium_page, base_url, stub)
    before = _card_report(chromium_page)
    gets_before = stub.recommendation_gets

    # 切走再切回来：后台再水合会读取 runtime / 其它状态，但已有卡片时不应再读
    # /api/recommendations；它只返回最新 top 窗口且可能触发首屏补池。
    chromium_page.evaluate("() => window.__obcSetHidden(true)")
    chromium_page.wait_for_timeout(400)
    chromium_page.evaluate("() => window.__obcSetHidden(false)")
    # 再水合是异步链（ensureAuthenticated → 多个快照请求 → 应用 → 重渲染）。真实
    # 后端上实测整表替换要 7.5–10 秒才落地，早采样会漏判成通过；stub 快得多，这里
    # 留足余量即可。
    chromium_page.wait_for_timeout(4000)

    assert stub.recommendation_gets == gets_before, "后台再水合不应重新读取推荐快照"
    after = _card_report(chromium_page)
    assert after["count"] == before["count"], "再水合把本地加载出来的卡片丢了"
    assert after["titles"] == before["titles"], "再水合按后端最新排序重排了列表"


def test_disabled_autoload_does_not_consume_pool_during_background_hydration(
    stability_server: tuple[str, StabilityStub],
    chromium_page: Page,
) -> None:
    """关闭自动续页后，切回标签页和库存事件都不能触发推荐 GET。"""
    base_url, stub = stability_server
    chromium_page.add_init_script(
        "window.localStorage.setItem('openbiliclaw.webui.autoLoadOnScroll', '0')"
    )
    chromium_page.goto(f"{base_url}/web/")
    expect(chromium_page.locator("#videoGrid .video-card:not(.is-skeleton)")).to_have_count(
        CARD_COUNT, timeout=8000
    )
    chromium_page.wait_for_timeout(1500)

    _stamp_cards(chromium_page)
    before = _card_report(chromium_page)
    gets_before = stub.recommendation_gets

    # 滚到底仍不能走 append；这是开关本身的边界，避免把后续库存变化
    # 误判成用户主动加载。
    chromium_page.evaluate(
        "() => window.scrollTo({ top: document.body.scrollHeight, behavior: 'instant' })"
    )
    chromium_page.evaluate("() => window.dispatchEvent(new Event('scroll'))")
    chromium_page.wait_for_timeout(500)
    assert not stub.append_received.is_set(), "关闭自动加载后滚动仍触发了 append"

    chromium_page.evaluate(
        """() => window.__obcPushRuntime({
          type: 'refresh.pool_updated',
          pool_available_count: 17,
          message: '候选池已同步到 17 条',
        })"""
    )
    chromium_page.wait_for_timeout(700)

    # 模拟切回桌面页：后台 session 仍会读取 runtime / 其它状态，但已有
    # 卡片时必须跳过有副作用的 /api/recommendations。
    chromium_page.evaluate("() => window.__obcSetHidden(true)")
    chromium_page.wait_for_timeout(100)
    chromium_page.evaluate("() => window.__obcSetHidden(false)")
    chromium_page.wait_for_timeout(4000)

    assert stub.recommendation_gets == gets_before, "后台水合重新读取推荐并消费了候选池"
    after = _card_report(chromium_page)
    assert after["count"] == before["count"]
    assert after["titles"] == before["titles"]
    assert after["stamps"] == before["stamps"]
