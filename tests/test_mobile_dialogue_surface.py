"""Static regressions for the mobile structured taste-dialogue surface."""

from __future__ import annotations

from pathlib import Path

INDEX_HTML = Path("src/openbiliclaw/web/index.html")
APP_CSS = Path("src/openbiliclaw/web/css/app.css")
API_JS = Path("src/openbiliclaw/web/js/api.js")
CHAT_JS = Path("src/openbiliclaw/web/js/views/chat.js")
PROFILE_JS = Path("src/openbiliclaw/web/js/views/profile.js")


def test_mobile_loads_shared_dialogue_renderer_before_the_module_app() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")

    shared = html.index('<script src="../shared/dialogue-confirmation.js"></script>')
    module = html.index('<script type="module" src="js/app.js"></script>')
    assert shared < module


def test_mobile_wires_pending_confirmation_and_card_action_requests() -> None:
    api = API_JS.read_text(encoding="utf-8")
    chat = CHAT_JS.read_text(encoding="utf-8")

    assert "export async function fetchPendingConfirmations" in api
    assert "/chat/pending-confirmations?" in api
    assert "export async function openPendingConfirmation" in api
    assert "export async function actOnChatCard" in api
    assert "executePendingConfirmationOpen" in chat
    assert "executeCardAction" in chat
    assert "isTerminalCardTurn" in chat
    assert "if (isTerminalCardTurn(contextTarget))" in chat
    assert 'fetchChatTurns({ session: "popup", limit: 100 })' in chat
    assert 'renderTurnMarkup(turn, { surface: "desktop" })' in chat
    assert "selectDialogueTurns(turns)" in chat


def test_mobile_dialogue_keeps_reader_state_across_live_renders() -> None:
    chat = CHAT_JS.read_text(encoding="utf-8")

    assert "function isNearChatBottom(element)" in chat
    assert "function openEvidenceTurnIds(element)" in chat
    assert "const previousScrollTop = previousMessages?.scrollTop || 0;" in chat
    assert "if (openEvidence.has(turnId)) details.open = true;" in chat
    assert "textarea.value = previousDraft;" in chat
    assert "textarea.focus({ preventScroll: true })" in chat


def test_mobile_long_lists_have_bounded_independent_scrollers() -> None:
    css = APP_CSS.read_text(encoding="utf-8")

    assert ".chat-pending {" in css
    assert "max-height: min(30dvh, 240px);" in css
    assert ".chat-pending-list {" in css
    assert "grid-auto-rows: max-content;" in css
    assert "overscroll-behavior: contain;" in css
    assert ".chat-messages:focus-visible" in css
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in css
    assert ".dialogue-card-action {" in css
    assert "min-height: 44px;" in css


def test_mobile_profile_points_to_its_own_dialogue_confirmation_entry() -> None:
    profile = PROFILE_JS.read_text(encoding="utf-8")

    assert "请在「聊聊口味」的待聊确认入口处理" in profile
    assert "请在插件或桌面端的对话入口确认" not in profile
