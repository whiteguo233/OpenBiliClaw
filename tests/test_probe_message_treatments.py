"""Static regressions for distinct probe treatments across web surfaces."""

from pathlib import Path


def test_mobile_web_probe_cards_have_type_specific_copy_and_styles() -> None:
    chat_js = Path("src/openbiliclaw/web/js/views/chat.js").read_text()
    app_css = Path("src/openbiliclaw/web/css/app.css").read_text()

    assert "is-interest-probe" in chat_js
    assert "is-challenge-probe" in chat_js
    assert "is-avoidance-probe" in chat_js
    assert "message-card-prompt" in chat_js
    assert "想继续探索" in chat_js
    assert "挑战方向" in chat_js
    assert "想少看这类" in chat_js
    assert ".message-card.is-interest-probe" in app_css
    assert ".message-card.is-challenge-probe" in app_css
    assert ".message-card.is-avoidance-probe" in app_css
    assert ".message-card-prompt" in app_css


def test_desktop_web_probe_cards_have_type_specific_copy_and_styles() -> None:
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text()
    app_css = Path("src/openbiliclaw/web/desktop/assets/css/app.css").read_text()

    assert "is-interest-probe" in app_js
    assert "is-challenge-probe" in app_js
    assert "is-avoidance-probe" in app_js
    assert "message-note probe-kind-copy" in app_js
    assert "想继续探索" in app_js
    assert "挑战方向" in app_js
    assert "想少看这类" in app_js
    assert ".message-item.is-interest-probe" in app_css
    assert ".message-item.is-challenge-probe" in app_css
    assert ".message-item.is-avoidance-probe" in app_css
    assert ".probe-kind-copy" in app_css


def test_desktop_probe_chat_expands_inline_in_message_card() -> None:
    """Desktop Inbox probe chat must stay in the card instead of opening chat view."""
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text()
    app_css = Path("src/openbiliclaw/web/desktop/assets/css/app.css").read_text()

    assert "function openInlineMessageProbeChat(msg, el)" in app_js

    chat_branch = app_js.split('if (response === "chat") {', 1)[1].split("return;", 1)[0]
    assert "openInlineMessageProbeChat(msg, el);" in chat_branch
    assert "openMessageChat(msg);" not in chat_branch
    inline_helper = app_js.split("function openInlineMessageProbeChat(msg, el)", 1)[1].split(
        "async function respondProbe",
        1,
    )[0]
    assert "handledProbeKeys" not in inline_helper

    assert "inline-chat-area" in app_js
    assert "inline-chat-input" in app_js
    assert "inline-chat-reply" in app_js
    assert "pollInlineMessageChatTurn" in app_js
    assert 'scope: isAvoidance ? "avoidance_probe" : "probe"' in app_js

    assert ".message-item .inline-chat-area" in app_css
    assert ".message-item .inline-chat-input" in app_css
    assert ".message-item .inline-chat-reply" in app_css
