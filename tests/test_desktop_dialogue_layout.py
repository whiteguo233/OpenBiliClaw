from pathlib import Path

APP_CSS = Path("src/openbiliclaw/web/desktop/assets/css/app.css")
APP_JS = Path("src/openbiliclaw/web/desktop/assets/js/app.js")
INDEX_HTML = Path("src/openbiliclaw/web/desktop/index.html")


def test_desktop_dialogue_has_a_structured_pending_inbox() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")
    css = APP_CSS.read_text(encoding="utf-8")

    assert 'class="dialogue-pending-toggle-title"' in html
    assert 'class="dialogue-pending-toggle-count" id="desktopPendingCount"' in html
    assert 'class="dialogue-pending-toggle-chevron"' in html
    assert "这些判断需要你点头" not in html
    assert ".dialogue-pending {" in css
    assert "#desktopPendingConfirmations {" in css
    assert "grid-template-columns: 1fr;" in css
    assert "grid-auto-rows: max-content;" in css
    assert "padding: 0 8px 8px;" in css
    assert "overflow-y: auto;" in css
    assert "max-height: min(32vh, 300px);" in css
    assert "border-radius: 13px;" in css
    assert "#desktopPendingToggle:focus-visible" in css


def test_desktop_dialogue_cards_have_hierarchy_and_responsive_actions() -> None:
    css = APP_CSS.read_text(encoding="utf-8")

    assert ".dialogue-card {" in css
    assert "width: min(100%, 680px);" in css
    assert "padding: 14px 16px 12px;" in css
    assert ".dialogue-card-title {" in css
    assert ".dialogue-card-title { margin: 0; color: var(--fg); font-size: 16px;" in css
    assert ".dialogue-card-actions {" in css
    assert ".dialogue-card-action { min-width: 0; min-height: 32px;" in css
    assert ".dialogue-card-action.is-confirm {" in css
    assert ".dialogue-card-action.is-reject:hover {" in css
    assert ".dialogue-card-action.is-defer {" in css
    assert '.dialogue-card[data-card-state="confirmed"]' in css
    assert ".dialogue-card-actions { grid-template-columns: repeat(2, minmax(0, 1fr)); }" in css


def test_desktop_dialogue_composer_has_an_accessible_name() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert 'id="chatInput" aria-label="和阿B聊聊你的口味"' in html


def test_desktop_pending_chat_turn_keeps_a_visible_accessible_thinking_state() -> None:
    js = APP_JS.read_text(encoding="utf-8")
    css = APP_CSS.read_text(encoding="utf-8")

    assert "function desktopTurnIsWaitingForReply(turn)" in js
    assert 'status === "pending" || status === "processing"' in js
    assert "desktopChatThinkingMarkup()" in js
    assert "阿B 正在思考，等待模型回复…" in js
    assert 'role="status"' in js
    assert 'aria-live="polite"' in js
    assert 'aria-busy="true"' in js
    assert ".chat-page .chat-bubble.chat-thinking" in css
    assert "@keyframes dialogue-thinking-dot" in css


def test_desktop_dialogue_first_open_scrolls_restored_history_to_latest() -> None:
    source = APP_JS.read_text(encoding="utf-8")
    start = source.index("    function openChatPage()")
    end = source.index("    function openSettingsPage", start)
    block = source[start:end]

    assert "let hasOpenedDialogueChatPage = false;" in source
    assert "const forceBottom = !hasOpenedDialogueChatPage;" in block
    assert "renderChat({ forceBottom });" in block
    assert "hasOpenedDialogueChatPage = true;" in block


def test_desktop_silently_clears_a_context_after_its_card_settles() -> None:
    source = APP_JS.read_text(encoding="utf-8")

    assert "isTerminalCardTurn" in source
    assert "if (isTerminalCardTurn(contextTarget))" in source


def test_desktop_dialogue_history_uses_natural_rows_and_visible_scroll_affordance() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")
    css = APP_CSS.read_text(encoding="utf-8")

    assert 'id="chatLog" role="region" aria-label="口味对话记录" tabindex="0"' in html
    assert ".chat-log { display: grid; grid-auto-rows: max-content;" in css
    assert ".chat-page .chat-log {" in css
    assert "scrollbar-width: thin;" in css
    assert "overscroll-behavior: contain;" in css
    assert ".chat-page .chat-log::-webkit-scrollbar" in css
    assert ".chat-page .chat-log::-webkit-scrollbar { display: none; }" not in css


def test_desktop_dialogue_refresh_preserves_reader_scroll_and_open_evidence() -> None:
    js = APP_JS.read_text(encoding="utf-8")

    assert "function isNearScrollBottom(element)" in js
    assert "function openDialogueEvidenceTurnIds(element)" in js
    assert "function renderChatLogElement(element, markup" in js
    assert "const previousScrollTop = element.scrollTop;" in js
    assert "if (openEvidenceTurnIds.has(turnId)) details.open = true;" in js
    assert "renderChat({ forceBottom });" in js


def test_desktop_source_guide_folds_into_a_slim_disclosure() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")
    css = APP_CSS.read_text(encoding="utf-8")

    assert '<details class="chat-source-guide">' in html
    assert '<details class="chat-source-guide" open' not in html
    assert "<summary>添加网站 / RSS 来源 · 说明与示例</summary>" in html
    assert '<section class="chat-source-guide"' not in html
    assert "在这里添加网站 / RSS 来源" not in html
    assert ".chat-source-guide summary {" in css
    assert "min-height: 34px;" in css
    assert "max-height: min(220px, 30vh);" in css
    assert ".chat-source-guide[open] summary::before" in css


def test_desktop_chat_chrome_leaves_room_for_history() -> None:
    css = APP_CSS.read_text(encoding="utf-8")

    assert (
        ".chat-page .content-page-head { align-items: center; margin-bottom: var(--space-3); }"
        in css
    )
    assert ".chat-page .content-page-head .eyebrow { display: none; }" in css
    assert ".chat-page .content-page-head > div { min-width: 0; display: flex;" in css
    assert "margin-bottom: 6px;" in css  # pending inbox
    assert "padding: 6px 10px;" in css  # pending toggle
    assert ".chat-page .chat-log { min-height: 0; margin: 0 0 var(--space-2);" in css
