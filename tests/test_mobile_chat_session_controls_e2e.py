"""Browser regressions for mobile session editing during background refreshes."""

from pathlib import Path
from typing import Any

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[1]


def test_mobile_rename_survives_refresh_without_leaking_into_another_session() -> None:
    source = (ROOT / "src/openbiliclaw/web/js/views/chat.js").read_text(encoding="utf-8")
    start = source.index("function ensureAgentOverlayHost()")
    end = source.index("// Keep the live composer attached", start)
    with playwright_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.set_content('<html><body><main id="app"></main></body></html>')
            page.evaluate(
                """() => {
                  window.esc = text => String(text).replaceAll('"', '&quot;');
                  window.chatSessions = [
                    {session_id: 'a', title: '原名称 A'},
                    {session_id: 'b', title: '原名称 B'},
                  ];
                  window.activeSessionId = 'a';
                  window.sessionRenameId = 'a';
                  window.sessionsDrawerOpen = true;
                  window.skillsSheetOpen = false;
                  window.tasksOverlayOpen = false;
                  window.saved = [];
                  window.handleRenameSession = (id, title) => saved.push({id, title});
                }"""
            )
            page.add_script_tag(content=source[start:end])
            page.evaluate("renderAgentOverlays()")
            editor = page.locator(".agent-session-rename-input")
            editor.fill("未保存的新名称")
            editor.evaluate("el => { el.focus(); el.setSelectionRange(1, 4); }")
            # The same production redraw runs when another client's update arrives.
            page.evaluate("chatSessions[0].title = '另一端的名称'; renderAgentOverlays()")
            assert editor.input_value() == "未保存的新名称"
            selection: dict[str, Any] = editor.evaluate(
                "el => ({focused: document.activeElement === el, "
                "start: el.selectionStart, end: el.selectionEnd})"
            )
            assert selection == {"focused": True, "start": 1, "end": 4}
            page.locator('[data-session-rename-submit="a"]').click()
            assert page.evaluate("saved") == [{"id": "a", "title": "未保存的新名称"}]
            page.locator('[data-session-rename="b"]').click()
            assert editor.input_value() == "原名称 B"
            assert editor.evaluate("el => document.activeElement === el")
        finally:
            browser.close()
