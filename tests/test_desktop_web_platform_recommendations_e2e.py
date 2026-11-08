"""PC Web 平台定向推荐的真实浏览器契约（设计文档 §9.4）。

用真实 chromium 驱动 /web/，后端 stub 提供混合首屏 + 可读的平台库存快照，验证：

1. Tab 集合 / 顺序 / 库存数字（含「已启用但库存键缺失 → 显示 0」）；
2. 切换平台只改视图，不发推荐请求；
3. 知乎 Tab 下 换一批 / 加载更多 的 POST body 都带 ``source_platform="zhihu"``；
4. 平台请求返回后可见卡全部为知乎；
5. 切回 B 站时本会话原有的 B 站卡片仍在（平台定向换一批只替换该平台）；
6. 「全部」请求不带平台参数（旧契约形状不变）；
7. 键盘 focus / selected state / 无水平溢出。

自动续页在本用例里被 localStorage 关掉：这里要断言的是「手动动作的请求体」，
滚动自动续页的库存 gate 由 tests/test_desktop_web_load_more.py 的静态契约覆盖。
"""

from __future__ import annotations

import json
import mimetypes
import threading
import time
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

playwright_api = pytest.importorskip("playwright.sync_api")
Page = playwright_api.Page
expect = playwright_api.expect
sync_playwright = playwright_api.sync_playwright

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[1]

# reddit 已启用但在 by_platform 里缺席 → 前端必须渲染 0，而不是把它藏起来。
# youtube 未启用但库存 > 0 → 必须出 Tab（并集规则的第 2 条）。
AVAILABILITY = {
    "total_available": 29,
    "by_platform": {"bilibili": 18, "youtube": 4, "zhihu": 7},
}
ENABLED_SOURCES = {
    "bilibili": {"enabled": True},
    "zhihu": {"enabled": True},
    "reddit": {"enabled": True},
}


def _card(platform: str, batch: str, index: int) -> dict[str, Any]:
    content_id = f"{platform.upper()}-{batch}{index}"
    hosts = {
        "bilibili": f"https://www.bilibili.com/video/{content_id}",
        "zhihu": f"https://www.zhihu.com/question/{batch}/answer/{index}",
    }
    return {
        "id": abs(hash(content_id)) % 100000,
        "bvid": content_id,
        "content_id": content_id,
        "content_url": hosts.get(platform, f"https://example.test/{platform}/{content_id}"),
        "source_platform": platform,
        "content_type": "video" if platform == "bilibili" else "answer",
        "title": f"{platform} 批次{batch} 第{index}条",
        "body_text": f"{platform} 批次{batch} 第{index}条正文占位。",
        "up_name": f"UP-{platform}-{index}",
        "topic_label": "平台定向",
        "expression": f"{platform} 批次{batch} 第{index}条的推荐理由占位文案。",
    }


def _first_page() -> list[dict[str, Any]]:
    """混合首屏：B 站 3 条 + 知乎 2 条，交错排列。"""
    return [
        _card("bilibili", "A", 1),
        _card("zhihu", "A", 1),
        _card("bilibili", "A", 2),
        _card("zhihu", "A", 2),
        _card("bilibili", "A", 3),
    ]


class PlatformStub:
    def __init__(self) -> None:
        self.reshuffle_posts: list[dict[str, Any]] = []
        self.append_posts: list[dict[str, Any]] = []
        self.availability_reads = 0
        self.config_failures_remaining = 0
        self.lock = threading.Lock()


def _json_response(handler: BaseHTTPRequestHandler, payload: Any, status: int = 200) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    with suppress(BrokenPipeError):
        handler.wfile.write(body)


def _build_platform_app(config_failures: int = 0) -> Iterator[tuple[str, PlatformStub]]:
    state = PlatformStub()
    state.config_failures_remaining = max(0, int(config_failures))

    class Handler(BaseHTTPRequestHandler):
        # HTTP/1.0 + Connection: close: 水合时会并行发起十几条请求，keep-alive
        # 连接复用与 ThreadingHTTPServer 的关闭竞态会让其中几条偶发
        # "TypeError: Failed to fetch"（浏览器侧连接重置）。短连接让每条请求
        # 独立建连，测试桩的响应时序稳定。
        protocol_version = "HTTP/1.0"

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in {"/web", "/web/", "/web/index.html"}:
                return self._serve_file(
                    ROOT / "src/openbiliclaw/web/desktop/index.html", "text/html"
                )
            if path.startswith("/web/assets/"):
                rel = path.removeprefix("/web/assets/")
                return self._serve_file(ROOT / "src/openbiliclaw/web/desktop/assets" / rel)
            if path.startswith("/shared/"):
                # Serve the same shared settings modules as the real app. A
                # missing module throws during config hydration and prevents
                # the recovered platform snapshot from reaching the filters.
                shared_root = ROOT / "src/openbiliclaw/web/shared"
                asset = (shared_root / path.removeprefix("/shared/")).resolve()
                if not asset.is_relative_to(shared_root):
                    return _json_response(self, {"error": "not_found"}, 404)
                return self._serve_file(asset)
            if path == "/api/ping":
                return _json_response(self, {"ok": True})
            if path == "/api/health":
                return _json_response(self, {"ok": True, "embedding_ready": True})
            if path == "/api/auth/status":
                return _json_response(self, {"enabled": False, "authenticated": True})
            if path == "/api/recommendations":
                return _json_response(self, {"items": _first_page()})
            if path == "/api/recommendations/platform-availability":
                with state.lock:
                    state.availability_reads += 1
                return _json_response(self, AVAILABILITY)
            if path == "/api/runtime-status":
                return _json_response(
                    self,
                    {
                        "initialized": True,
                        "pool_available_count": AVAILABILITY["total_available"],
                        "pool_size": 40,
                        "pool_refresh_state": "idle",
                        "pool_source_shares": {"bilibili": 0.6, "zhihu": 0.4},
                        "configured_sources": ENABLED_SOURCES,
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
                            "enabled_platforms": ["bilibili", "zhihu", "reddit"],
                        },
                    },
                )
            if path == "/api/config":
                if state.config_failures_remaining > 0:
                    state.config_failures_remaining -= 1
                    return _json_response(self, {"error": "config_flaky"}, 503)
                return _json_response(
                    self,
                    {
                        "config": {
                            "sources": ENABLED_SOURCES,
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
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                payload = {}
            platform = str(payload.get("source_platform") or "")
            if path == "/api/recommendations/reshuffle":
                with state.lock:
                    state.reshuffle_posts.append(payload)
                    batch = f"R{len(state.reshuffle_posts)}"
                items = (
                    [_card(platform, batch, index) for index in range(1, 4)]
                    if platform
                    else [
                        _card("bilibili", batch, 1),
                        _card("zhihu", batch, 1),
                        _card("bilibili", batch, 2),
                    ]
                )
                return _json_response(self, {"items": items})
            if path == "/api/recommendations/append":
                with state.lock:
                    state.append_posts.append(payload)
                    batch = f"P{len(state.append_posts)}"
                items = (
                    [_card(platform, batch, index) for index in range(1, 3)]
                    if platform
                    else [_card("bilibili", batch, 1), _card("zhihu", batch, 1)]
                )
                return _json_response(self, {"items": items})
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
def platform_server() -> Iterator[tuple[str, PlatformStub]]:
    yield from _build_platform_app()


@pytest.fixture()
def flaky_config_server() -> Iterator[tuple[str, PlatformStub]]:
    """Startup /api/config fails twice, then succeeds (retry recovery path)."""
    yield from _build_platform_app(config_failures=2)


@pytest.fixture()
def chromium_page() -> Iterator[Page]:
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
        except Exception:  # pragma: no cover - depends on the local browser install
            browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.add_init_script(
            """
            try {
              window.localStorage.setItem('openbiliclaw.webui.autoLoadOnScroll', '0');
            } catch (e) {}
            window.WebSocket = class FakeWebSocket {
              static OPEN = 1;
              constructor() {
                this.readyState = FakeWebSocket.OPEN;
                setTimeout(() => {
                  if (typeof this.onopen === 'function') this.onopen({type:'open'});
                }, 0);
              }
              addEventListener() {}
              removeEventListener() {}
              close() { this.readyState = 3; }
            };
            """
        )
        yield page
        browser.close()


def _wait_for(predicate: Callable[[], bool], timeout: float = 6.0, message: str = "") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(message or "condition was not met in time")


def _chips(page: Page) -> list[dict[str, str]]:
    return page.evaluate(
        """() => Array.from(document.querySelectorAll('#filterRow .chip')).map((chip) => ({
          filter: chip.dataset.filter || '',
          platform: chip.dataset.platform || '',
          count: chip.querySelector('.chip-count')?.textContent || '',
          selected: chip.getAttribute('aria-selected') || '',
          label: chip.getAttribute('aria-label') || '',
        }))"""
    )


def _visible_cards(page: Page) -> list[dict[str, str]]:
    return page.evaluate(
        """() => Array.from(
          document.querySelectorAll('#videoGrid .video-card:not(.is-skeleton)')
        ).map((card) => ({
          bvid: card.dataset.bvid || '',
          platform: card.querySelector('.platform')?.dataset.platform || '',
        }))"""
    )


def _click_chip(page: Page, label: str) -> None:
    page.locator(f'#filterRow .chip[data-filter="{label}"]').click()


def test_platform_tabs_scope_recommendation_requests_in_chromium(
    platform_server: tuple[str, PlatformStub],
    chromium_page: Page,
) -> None:
    base_url, stub = platform_server
    chromium_page.goto(f"{base_url}/web/")

    cards = chromium_page.locator("#videoGrid .video-card:not(.is-skeleton)")
    expect(cards).to_have_count(5, timeout=8000)

    # ── 1. Tab 集合 / 顺序 / 库存数字 ──────────────────────────────────────
    _wait_for(
        lambda: all(chip["count"] not in {"", "—"} for chip in _chips(chromium_page)),
        message="库存快照读取后 chip 仍停留在未知态",
    )
    # /api/config 与库存快照并行读取；配置快照若瞬断，前端有界重试后 Tab 集合
    # 收敛（已启用但零库存的平台只由配置快照带来）。断言前先等 Tab 并集完整。
    _wait_for(
        lambda: (
            {chip["filter"] for chip in _chips(chromium_page)}
            == {"全部", "B 站", "YouTube", "知乎", "Reddit"}
        ),
        message="已启用平台 Tab 集合未收敛",
    )
    chips = _chips(chromium_page)
    assert [chip["filter"] for chip in chips] == ["全部", "B 站", "YouTube", "知乎", "Reddit"]
    assert {chip["filter"]: chip["count"] for chip in chips} == {
        "全部": "29",
        "B 站": "18",
        "YouTube": "4",
        "知乎": "7",
        # 已启用但 by_platform 里没有这个键 → 显示 0，不是未知也不是隐藏。
        "Reddit": "0",
    }
    assert [chip["platform"] for chip in chips] == ["", "bilibili", "youtube", "zhihu", "reddit"]
    zhihu_chip = next(chip for chip in chips if chip["filter"] == "知乎")
    assert "知乎" in zhihu_chip["label"]
    assert "7" in zhihu_chip["label"]
    assert [chip["selected"] for chip in chips] == ["true", "false", "false", "false", "false"]

    bilibili_first_page = [
        card["bvid"] for card in _visible_cards(chromium_page) if card["platform"] == "bilibili"
    ]
    assert bilibili_first_page == ["BILIBILI-A1", "BILIBILI-A2", "BILIBILI-A3"]

    # ── 2. 切换平台只改视图，不发推荐请求 ────────────────────────────────
    _click_chip(chromium_page, "知乎")
    expect(cards).to_have_count(2, timeout=4000)
    time.sleep(0.4)
    with stub.lock:
        assert stub.reshuffle_posts == []
        assert stub.append_posts == []
    assert {card["platform"] for card in _visible_cards(chromium_page)} == {"zhihu"}

    # ── 3. 换一批：请求体带 canonical 平台，可见卡全部为知乎 ──────────────
    chromium_page.locator("#reshuffleBtn").click()
    _wait_for(lambda: len(stub.reshuffle_posts) == 1, message="平台定向换一批没有发出请求")
    reshuffle_body = stub.reshuffle_posts[0]
    assert reshuffle_body.get("source_platform") == "zhihu"
    # 排除集是「该平台本会话已加载内容」，不含其它平台的卡片。
    assert sorted(reshuffle_body.get("excluded_bvids", [])) == ["ZHIHU-A1", "ZHIHU-A2"]
    expect(cards).to_have_count(3, timeout=4000)
    assert {card["platform"] for card in _visible_cards(chromium_page)} == {"zhihu"}

    # ── 4. 加载更多：同样携带请求开始时的平台 ────────────────────────────
    chromium_page.locator("#loadMoreBtn").click()
    _wait_for(lambda: len(stub.append_posts) == 1, message="平台定向加载更多没有发出请求")
    append_body = stub.append_posts[0]
    assert append_body.get("source_platform") == "zhihu"
    assert "BILIBILI-A1" in append_body.get("excluded_bvids", [])
    expect(cards).to_have_count(5, timeout=4000)
    assert {card["platform"] for card in _visible_cards(chromium_page)} == {"zhihu"}

    # ── 5. 切回 B 站：本会话原有的 B 站卡片必须还在 ──────────────────────
    _click_chip(chromium_page, "B 站")
    expect(cards).to_have_count(3, timeout=4000)
    retained = _visible_cards(chromium_page)
    assert {card["platform"] for card in retained} == {"bilibili"}
    assert [card["bvid"] for card in retained] == bilibili_first_page

    # ── 6. 「全部」：兼容路径不带平台参数 ────────────────────────────────
    _click_chip(chromium_page, "全部")
    chromium_page.locator("#reshuffleBtn").click()
    _wait_for(lambda: len(stub.reshuffle_posts) == 2, message="「全部」换一批没有发出请求")
    global_body = stub.reshuffle_posts[1]
    assert not global_body.get("source_platform"), global_body
    assert {card["platform"] for card in _visible_cards(chromium_page)} == {"bilibili", "zhihu"}


def test_enabled_zero_stock_platform_survives_config_snapshot_retry(
    flaky_config_server: tuple[str, PlatformStub],
    chromium_page: Page,
) -> None:
    """已启用但零库存的平台在首次 /api/config 失败后仍必须出现（Tab 并集规则）。

    水合时 /api/config 前两次返回 503：筛选行先按库存快照渲染（B 站 / 知乎 /
    YouTube），Reddit 只能由配置快照带来。有界重试成功后 Reddit Tab 必须补上，
    且计数显示 0 —— 而不是被永久藏起来。
    """
    base_url, stub = flaky_config_server
    page_errors: list[str] = []
    chromium_page.on("pageerror", lambda error: page_errors.append(str(error)))
    chromium_page.goto(f"{base_url}/web/")
    expect(chromium_page.locator("#videoGrid .video-card:not(.is-skeleton)")).to_have_count(
        5, timeout=8000
    )

    _wait_for(
        lambda: (
            {chip["filter"] for chip in _chips(chromium_page)}
            == {"全部", "B 站", "YouTube", "知乎", "Reddit"}
        ),
        timeout=15.0,
        message="配置快照重试成功后仍缺少已启用平台 Tab",
    )
    assert stub.config_failures_remaining == 0
    counts = {chip["filter"]: chip["count"] for chip in _chips(chromium_page)}
    assert counts.get("Reddit") == "0"
    assert not page_errors


def test_platform_tabs_keyboard_focus_selection_and_no_horizontal_overflow(
    platform_server: tuple[str, PlatformStub],
    chromium_page: Page,
) -> None:
    base_url, _stub = platform_server
    chromium_page.goto(f"{base_url}/web/")
    expect(chromium_page.locator("#videoGrid .video-card:not(.is-skeleton)")).to_have_count(
        5, timeout=8000
    )
    _wait_for(
        lambda: all(chip["count"] not in {"", "—"} for chip in _chips(chromium_page)),
        message="库存快照读取后 chip 仍停留在未知态",
    )
    # /api/config 与库存快照并行读取；配置快照若瞬断，前端有界重试后 Tab 集合
    # 收敛（已启用但零库存的平台只由配置快照带来）。断言前先等 Tab 并集完整。
    _wait_for(
        lambda: (
            {chip["filter"] for chip in _chips(chromium_page)}
            == {"全部", "B 站", "YouTube", "知乎", "Reddit"}
        ),
        message="已启用平台 Tab 集合未收敛",
    )

    row = chromium_page.locator("#filterRow")
    assert row.get_attribute("role") == "tablist"

    # tablist 的 roving tabindex：只有选中项在 Tab 序列里。
    assert chromium_page.evaluate(
        """() => Array.from(document.querySelectorAll('#filterRow .chip')).map((c) => c.tabIndex)"""
    ) == [0, -1, -1, -1, -1]

    _click_chip(chromium_page, "全部")
    chromium_page.keyboard.press("ArrowRight")
    focused = chromium_page.evaluate(
        """() => {
          const el = document.activeElement;
          return {
            filter: el?.dataset?.filter || '',
            selected: el?.getAttribute('aria-selected') || '',
            focusVisible: Boolean(el?.matches?.(':focus-visible')),
            boxShadow: el ? getComputedStyle(el).boxShadow : '',
          };
        }"""
    )
    assert focused["filter"] == "B 站"
    assert focused["selected"] == "true"
    assert focused["focusVisible"] is True
    assert focused["boxShadow"] not in {"", "none"}

    # focus ring 必须叠在选中态描边之上（同特异度靠顺序），否则选中的那个 Tab
    # 上键盘焦点就消失了 —— 失焦后 box-shadow 必须真的变短。
    blurred_box_shadow = chromium_page.evaluate(
        """() => {
          const el = document.activeElement;
          el.blur();
          return getComputedStyle(el).boxShadow;
        }"""
    )
    assert blurred_box_shadow != focused["boxShadow"]
    assert len(focused["boxShadow"]) > len(blurred_box_shadow)
    assert "inset" in blurred_box_shadow

    # 选中态不能只靠颜色：字重也变化。
    weights = chromium_page.evaluate(
        """() => Array.from(document.querySelectorAll('#filterRow .chip')).map((c) => ({
          filter: c.dataset.filter,
          selected: c.getAttribute('aria-selected'),
          weight: Number(getComputedStyle(c).fontWeight),
        }))"""
    )
    selected_weight = next(c["weight"] for c in weights if c["selected"] == "true")
    unselected_weight = next(c["weight"] for c in weights if c["selected"] == "false")
    assert selected_weight > unselected_weight

    # 计数徽标用 tabular numerals，且不引发页面级水平溢出。
    assert (
        chromium_page.evaluate(
            """() => getComputedStyle(
              document.querySelector('#filterRow .chip .chip-count')
            ).fontVariantNumeric"""
        )
        == "tabular-nums"
    )
    assert chromium_page.evaluate(
        """() => document.documentElement.scrollWidth <= window.innerWidth + 1"""
    )
    assert chromium_page.evaluate(
        """() => {
          const row = document.getElementById('filterRow');
          return row.getBoundingClientRect().width <= window.innerWidth + 1;
        }"""
    )
