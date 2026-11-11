"""Real-browser layout contract for the compact popup chat surface."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")
Page = playwright_api.Page
sync_playwright = playwright_api.sync_playwright

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[1]
POPUP_HTML = ROOT / "extension/popup/popup.html"


@pytest.fixture()
def chromium_page() -> Page:
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
        except Exception:
            browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 400, "height": 600})
        page.goto(POPUP_HTML.as_uri(), wait_until="domcontentloaded")
        page.wait_for_timeout(250)
        yield page
        browser.close()


def _rects(page: Page) -> dict[str, Any]:
    return page.evaluate(
        """() => {
          const rect = selector => {
            const element = document.querySelector(selector);
            if (!element) return null;
            const box = element.getBoundingClientRect();
            return { x: box.x, y: box.y, width: box.width, height: box.height };
          };
          const display = selector => {
            const element = document.querySelector(selector);
            return element ? getComputedStyle(element).display : null;
          };
          return {
            messages: rect('#chatMessages'),
            input: rect('#chatInput'),
            send: rect('#chatSendButton'),
            guide: rect('.chat-source-guide'),
            form: rect('#chatForm'),
            hero: rect('.hero'),
            tabs: rect('.tabs-shell'),
            heroSub: display('.hero-sub'),
            brandTitle: display('.brand-copy h1'),
            footerHeadline: display('.footer-headline'),
          };
        }"""
    )


def test_popup_chat_folds_chrome_and_keeps_the_composer_inline(chromium_page: Page) -> None:
    chromium_page.evaluate(
        """() => {
          for (const id of ['viewRecommend', 'viewLibrary', 'viewProfile']) {
            document.getElementById(id).hidden = true;
          }
          document.getElementById('viewChat').hidden = false;
          document.body.classList.add('chat-tab-active');
          document.getElementById('chatMessages').innerHTML = `
            <div class="dialogue-turn"><div class="chat-bubble user">你好</div></div>
            <div class="dialogue-turn">
              <div class="chat-bubble assistant">在的，说说你最近在想什么？</div>
            </div>`;
        }"""
    )
    rects = _rects(chromium_page)

    messages = rects["messages"]
    input_box = rects["input"]
    send = rects["send"]
    guide = rects["guide"]
    form = rects["form"]
    hero = rects["hero"]
    tabs = rects["tabs"]
    assert messages is not None and messages["height"] >= 120
    assert hero is not None and hero["height"] <= 45
    assert tabs is not None and tabs["height"] <= 52
    assert input_box is not None and send is not None
    assert send["x"] >= input_box["x"] + input_box["width"], "send button is not inline"
    assert send["height"] >= 44
    assert guide is not None and guide["height"] <= 36
    assert form is not None and form["height"] <= 135
    assert rects["heroSub"] == "none"
    assert rects["brandTitle"] == "none"
    assert rects["footerHeadline"] == "none"
