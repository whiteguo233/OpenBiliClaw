"""Real-browser layout contracts for the desktop taste-dialogue surface."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")
Page = playwright_api.Page
sync_playwright = playwright_api.sync_playwright

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[1]
APP_CSS = ROOT / "src/openbiliclaw/web/desktop/assets/css/app.css"
APP_JS = ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js"


def _pending_item(index: int) -> str:
    return f"""
      <article class="dialogue-pending-item">
        <div class="dialogue-pending-copy">
          <span class="dialogue-pending-kind">有点疑惑</span>
          <strong>第 {index} 条待澄清的口味判断，标题故意较长以覆盖真实换行高度</strong>
          <span class="dialogue-pending-confidence">67%</span>
        </div>
        <button type="button">打开</button>
      </article>
    """


def _dialogue_card(index: int) -> str:
    return f"""
      <article class="dialogue-card" data-dialogue-turn-id="turn-{index}" data-card-state="pending">
        <p class="dialogue-card-kicker">阿B 的猜测</p>
        <h3 class="dialogue-card-title">
          第 {index} 张较长的口味觉察卡片：卡片增多以后，标题、依据和操作仍须保持完整。
        </h3>
        <details class="dialogue-evidence" open>
          <summary>依据（3）</summary>
          <ul>
            <li>最近多次浏览同一主题的长内容。</li>
            <li>收藏过带有实际演示和边界分析的内容。</li>
            <li>对只追热点、没有验证过程的内容很快退出。</li>
          </ul>
        </details>
        <div class="dialogue-card-actions">
          <button class="dialogue-card-action is-confirm" type="button">准</button>
          <button class="dialogue-card-action is-reject" type="button">不准</button>
          <button class="dialogue-card-action is-discuss" type="button">聊聊</button>
          <button class="dialogue-card-action is-defer" type="button">稍后</button>
        </div>
      </article>
    """


def _dialogue_fixture_html() -> str:
    css = APP_CSS.read_text(encoding="utf-8")
    pending = "".join(_pending_item(index) for index in range(1, 11))
    cards = "".join(_dialogue_card(index) for index in range(1, 10))
    return f"""<!doctype html>
    <html lang="zh-CN" style="--topbar-height: 75px">
      <head><meta charset="utf-8"><style>{css}</style></head>
      <body class="chat-page-open">
        <div class="app-shell">
          <header class="topbar" style="height:75px"></header>
          <div class="app-body">
            <main class="layout">
              <section class="chat-page content-page" id="chatPage">
                <div class="content-page-head">
                  <div>
                    <p class="eyebrow">Dialogue</p>
                    <h2>聊聊口味</h2>
                    <p class="video-meta">告诉阿B你的偏好、边界和最近想看的方向。</p>
                  </div>
                </div>
                <div class="dialogue-pending">
                  <button id="desktopPendingToggle" type="button" aria-expanded="true">
                    <span class="dialogue-pending-toggle-title">待聊确认</span>
                  </button>
                  <div id="desktopPendingConfirmations">{pending}</div>
                </div>
                <div class="chat-log" id="chatLog" tabindex="0">{cards}</div>
                <form class="chat-input" id="chatForm">
                  <input aria-label="和阿B聊聊你的口味">
                  <button class="pill-btn primary" type="submit">发送</button>
                </form>
              </section>
            </main>
          </div>
        </div>
      </body>
    </html>"""


def _production_scroll_helpers() -> str:
    source = APP_JS.read_text(encoding="utf-8")
    start = source.index("    function isNearScrollBottom(element)")
    end = source.index("    function renderDesktopPendingConfirmations()", start)
    return f"const CHAT_SCROLL_BOTTOM_TOLERANCE_PX = 48;\n{source[start:end]}"


@pytest.fixture()
def chromium_page() -> Page:
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
        except Exception:
            browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        yield page
        browser.close()


def _scroll_report(page: Page, selector: str) -> dict[str, Any]:
    return page.locator(selector).evaluate(
        """element => ({
          clientHeight: element.clientHeight,
          scrollHeight: element.scrollHeight,
          scrollTop: element.scrollTop,
          top: element.getBoundingClientRect().top,
          bottom: element.getBoundingClientRect().bottom,
        })"""
    )


@pytest.mark.parametrize(("width", "height"), [(375, 667), (768, 720), (1440, 1000)])
def test_chat_toast_does_not_cover_composer(chromium_page: Page, width: int, height: int) -> None:
    chromium_page.set_viewport_size({"width": width, "height": height})
    chromium_page.set_content(_dialogue_fixture_html(), wait_until="domcontentloaded")
    # Keep a notice present, as hovering a real toast pauses its expiry. Use the
    # production classes/CSS and hit testing so this catches blocked clicks.
    chromium_page.evaluate(
        """() => {
          const container = document.createElement('div');
          container.className = 'toast-container';
          container.innerHTML = '<div class="toast-item" style="bottom:0">请稍后重试。</div>';
          document.body.appendChild(container);
          document.querySelector('#chatForm').addEventListener('submit', e => {
            e.preventDefault();
            window.submissions = (window.submissions || 0) + 1;
          });
        }"""
    )
    toast = chromium_page.locator(".toast-item").bounding_box()
    composer = chromium_page.locator("#chatForm").bounding_box()
    assert toast is not None and composer is not None
    assert toast["y"] + toast["height"] <= composer["y"], "toast covers chat composer"
    chromium_page.get_by_role("button", name="发送", exact=True).click(timeout=1000)
    assert chromium_page.evaluate("window.submissions") == 1


@pytest.mark.parametrize(
    ("width", "height"),
    [(375, 667), (768, 720), (1024, 768), (1440, 900)],
)
def test_many_dialogue_cards_keep_natural_height_and_scroll(
    chromium_page: Page,
    width: int,
    height: int,
) -> None:
    chromium_page.set_viewport_size({"width": width, "height": height})
    chromium_page.set_content(_dialogue_fixture_html(), wait_until="domcontentloaded")

    card_metrics = chromium_page.locator("#chatLog .dialogue-card").evaluate_all(
        """cards => cards.map(card => ({
          height: card.getBoundingClientRect().height,
          scrollHeight: card.scrollHeight,
        }))"""
    )
    assert len(card_metrics) == 9
    assert all(metric["height"] > 240 for metric in card_metrics)
    assert all(metric["height"] + 1 >= metric["scrollHeight"] for metric in card_metrics)

    before = _scroll_report(chromium_page, "#chatLog")
    assert before["scrollHeight"] > before["clientHeight"] * 4
    chromium_page.locator("#chatLog").hover()
    chromium_page.mouse.wheel(0, 700)
    # 滚轮滚动在 headless Chromium 里由合成器异步落地（实测 80ms 固定等待在
    # 并行负载下会早采样到 scrollTop=0，滚动随后才到）；必须等到 scrollTop
    # 真正前进再采样，不能把时序当成布局契约。
    chromium_page.wait_for_function(
        "(threshold) => document.querySelector('#chatLog').scrollTop > threshold",
        arg=before["scrollTop"],
        timeout=5000,
    )
    after = _scroll_report(chromium_page, "#chatLog")
    assert after["scrollTop"] > before["scrollTop"]

    composer = chromium_page.locator("#chatForm").bounding_box()
    assert composer is not None
    assert composer["y"] + composer["height"] <= height
    assert composer["y"] >= after["bottom"]


def test_pending_inbox_is_bounded_and_independently_scrollable(chromium_page: Page) -> None:
    chromium_page.set_content(_dialogue_fixture_html(), wait_until="domcontentloaded")

    panel = _scroll_report(chromium_page, "#desktopPendingConfirmations")
    assert panel["scrollHeight"] > panel["clientHeight"]
    chromium_page.locator("#desktopPendingConfirmations").hover()
    chromium_page.mouse.wheel(0, 500)
    # 与 #chatLog 相同：滚轮滚动由合成器异步落地，固定等待会早采样，
    # 必须等到 scrollTop 真正前进再断言。
    chromium_page.wait_for_function(
        "(threshold) => document.querySelector('#desktopPendingConfirmations')"
        ".scrollTop > threshold",
        arg=panel["scrollTop"],
        timeout=5000,
    )
    after = _scroll_report(chromium_page, "#desktopPendingConfirmations")
    assert after["scrollTop"] > panel["scrollTop"]

    pending_box = chromium_page.locator(".dialogue-pending").bounding_box()
    composer_box = chromium_page.locator("#chatForm").bounding_box()
    assert pending_box is not None and composer_box is not None
    assert pending_box["height"] <= 302
    assert pending_box["y"] + pending_box["height"] < composer_box["y"]


def test_chat_rerender_keeps_reader_position_and_open_evidence(chromium_page: Page) -> None:
    rows = "".join(
        f"""
        <article data-dialogue-turn-id="turn-{index}" style="height:150px">
          <details class="dialogue-evidence"{(" open" if index == 2 else "")}>
            <summary>依据</summary><p>第 {index} 条依据</p>
          </details>
        </article>
        """
        for index in range(1, 7)
    )
    chromium_page.set_content(
        f"""
        <div id="log" style="height:220px; overflow-y:auto; display:grid;
             grid-auto-rows:max-content; gap:12px">
          {rows}
        </div>
        """
    )
    chromium_page.add_script_tag(content=_production_scroll_helpers())

    report = chromium_page.locator("#log").evaluate(
        """element => {
          element.scrollTop = 170;
          const before = element.scrollTop;
          const markup = element.innerHTML + `
            <article data-dialogue-turn-id="turn-7" style="height:150px">
              <details class="dialogue-evidence"><summary>依据</summary></details>
            </article>`;
          renderChatLogElement(element, markup);
          return {
            before,
            after: element.scrollTop,
            evidenceOpen: element.querySelector('[data-dialogue-turn-id="turn-2"] details').open,
          };
        }"""
    )
    assert report == {"before": 170, "after": 170, "evidenceOpen": True}

    bottom_report = chromium_page.locator("#log").evaluate(
        """element => {
          element.scrollTop = element.scrollHeight;
          const markup = element.innerHTML + `
            <article data-dialogue-turn-id="turn-8" style="height:150px"></article>`;
          renderChatLogElement(element, markup);
          return {
            distanceFromBottom: element.scrollHeight - element.clientHeight - element.scrollTop,
          };
        }"""
    )
    assert bottom_report["distanceFromBottom"] == 0
