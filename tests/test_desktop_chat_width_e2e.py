"""Chat uses the available workspace rather than an article-width container."""

from pathlib import Path

import pytest

from tests.test_desktop_web_settings_save_e2e import (  # noqa: F401
    chromium_page,
    expect,
    settings_save_server,
)


@pytest.mark.parametrize("agent_mode", [False, True])
def test_chat_fills_workspace_at_desktop_and_mobile_widths(
    settings_save_server,  # noqa: F811 - shared pytest fixture
    chromium_page,  # noqa: F811 - shared pytest fixture
    tmp_path: Path,
    agent_mode: bool,
) -> None:
    page = chromium_page
    base_url, _ = settings_save_server
    page.set_viewport_size({"width": 1920, "height": 1080})
    if agent_mode:
        page.route("**/api/chat/skills", lambda route: route.fulfill(json={"skills": []}))
    page.goto(f"{base_url}/web/")
    page.locator("#chatBtn").click()
    if agent_mode:
        expect(page.locator("#chatPage")).to_have_class("chat-page content-page has-agent-side")
    for width in (1920, 1440, 1024, 768, 375):
        page.set_viewport_size({"width": width, "height": 900})
        box = page.evaluate("""() => {
            const layout = document.querySelector('.layout');
            const style = getComputedStyle(layout);
            const chat = document.querySelector('#chatPage').getBoundingClientRect();
            return {chat: chat.width,
                    available: layout.clientWidth
                        - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight),
                    overflow: document.documentElement.scrollWidth > innerWidth};
        }""")
        assert box["chat"] >= box["available"] - 2, (width, box)
        assert not box["overflow"], (width, box)
        page.screenshot(path=str(tmp_path / f"chat-{width}.png"))
