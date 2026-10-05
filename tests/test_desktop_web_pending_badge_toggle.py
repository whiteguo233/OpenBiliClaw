import re
from pathlib import Path

APP_JS = Path("src/openbiliclaw/web/desktop/assets/js/app.js")
INDEX_HTML = Path("src/openbiliclaw/web/desktop/index.html")


def _function_body(js: str, name: str) -> str:
    match = re.search(rf"function {name}\([^)]*\) \{{(?P<body>.*?)\n    \}}", js, flags=re.S)
    assert match is not None, f"{name} function not found"
    return match.group("body")


def test_index_declares_pending_chat_count_switches_default_off() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")

    # Frontend settings row keeps its place but now defaults off.
    assert 'id="showPendingChatCountSetting" type="checkbox">' in html
    assert 'id="showPendingChatCountSettingText">关闭' in html
    assert "显示待聊未读数" in html
    panel_start = html.index('id="settingsPanelFrontend"')
    assert (
        panel_start
        < html.index('id="showPendingChatCountSetting"')
        < html.index("settings-note-inline", panel_start)
    )

    # A second quick switch lives at the top of the 「聊聊口味」 tab.
    assert 'id="chatPendingBadgeToggle" type="checkbox">' in html
    assert 'id="chatPendingBadgeToggleText">标签红点' in html
    chat_page_start = html.index('id="chatPage"')
    assert (
        chat_page_start
        < html.index('id="chatPendingBadgeToggle"')
        < html.index('id="chatLog"', chat_page_start)
    )


def test_pending_chat_count_setting_defaults_off_and_uses_frontend_storage() -> None:
    js = APP_JS.read_text(encoding="utf-8")
    restore_frontend = _function_body(js, "restoreFrontendSettings")
    persist_frontend = _function_body(js, "persistFrontendSettings")

    assert 'const SHOW_PENDING_CHAT_COUNT_KEY = "openbiliclaw.webui.showPendingChatCount";' in js
    assert 'state.showPendingChatCount = storageGet(SHOW_PENDING_CHAT_COUNT_KEY) === "1";' in js
    assert "renderShowPendingChatCountToggle();" in restore_frontend
    assert (
        'storageSet(SHOW_PENDING_CHAT_COUNT_KEY, state.showPendingChatCount ? "1" : "0");'
        in persist_frontend
    )


def test_pending_chat_count_badge_is_hidden_when_setting_off() -> None:
    js = APP_JS.read_text(encoding="utf-8")
    render = _function_body(js, "renderDesktopPendingConfirmations")

    assert "if (state.showPendingChatCount) {" in render
    assert 'updateSavedBadge("chatPendingCountBadge", count);' in render
    assert 'badge.setAttribute("hidden", "");' in render


def test_pending_chat_count_switches_share_one_preference() -> None:
    js = APP_JS.read_text(encoding="utf-8")
    setter = _function_body(js, "setShowPendingChatCount")
    render = _function_body(js, "renderShowPendingChatCountToggle")

    # The settings row and the chat-tab quick switch mirror each other.
    assert '"#showPendingChatCountSetting"' in render
    assert '"#chatPendingBadgeToggle"' in render
    assert "renderShowPendingChatCountToggle();" in setter
    assert "renderDesktopPendingConfirmations();" in setter
    assert 'safeBind("#chatPendingBadgeToggle", "change"' in js
