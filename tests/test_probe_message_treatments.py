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
