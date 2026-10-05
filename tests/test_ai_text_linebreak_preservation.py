from pathlib import Path

MOBILE_CSS = Path("src/openbiliclaw/web/css/app.css")
DESKTOP_CSS = Path("src/openbiliclaw/web/desktop/assets/css/app.css")
POPUP_HTML = Path("extension/popup/popup.html")


def test_mobile_web_preserves_ai_delight_reason_linebreaks() -> None:
    css = MOBILE_CSS.read_text(encoding="utf-8")
    assert ".delight-reason {" in css
    assert ".card-expression {" in css
    assert "white-space: pre-wrap;" in css
    assert "overflow-wrap: anywhere;" in css


def test_desktop_web_preserves_ai_text_linebreaks() -> None:
    css = DESKTOP_CSS.read_text(encoding="utf-8")
    assert ".reason-text," in css
    assert "#delightReason," in css
    assert "white-space: pre-wrap;" in css


def test_popup_preserves_ai_text_linebreaks() -> None:
    html = POPUP_HTML.read_text(encoding="utf-8")
    assert ".delight-reason," in html
    assert ".delight-banner-reason," in html
    assert ".message-reason," in html
    assert ".spec-reason," in html
    assert "white-space: pre-wrap;" in html
