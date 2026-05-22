"""Static regression tests for the mobile web delight tray layout."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECOMMEND_JS = ROOT / "src/openbiliclaw/web/js/views/recommend.js"
APP_CSS = ROOT / "src/openbiliclaw/web/css/app.css"


def _css_block(css: str, selector: str) -> str:
    match = re.search(rf"{re.escape(selector)}\s*\{{[\s\S]*?\}}", css)
    return match.group(0) if match else ""


def test_mobile_delight_tray_uses_featured_reason_wrap() -> None:
    """The surprise recommendation tray should look distinct from normal cards."""

    js = RECOMMEND_JS.read_text()
    css = APP_CSS.read_text()

    assert 'class="delight-feature-copy"' in js
    assert 'class="delight-reason-label"' in js
    assert 'id="delight-later"' in js
    assert "\\u7A0D\\u540E\\u770B" in js or "稍后看" in js
    assert 'class="delight-result-state"' in js

    tray_block = _css_block(css, ".delight-tray")
    tag_block = _css_block(css, ".delight-tag")
    wrap_block = _css_block(css, ".delight-reason-wrap")
    reason_block = _css_block(css, ".delight-reason")
    thumb_block = _css_block(css, ".delight-thumb")
    later_block = _css_block(css, ".delight-later-btn")

    assert "linear-gradient" in tray_block
    assert "linear-gradient" in tag_block
    assert "flow-root" in wrap_block
    assert "max-height" not in reason_block
    assert "overflow: hidden" not in reason_block
    assert "float: left" in thumb_block
    assert "position: absolute" in later_block


def test_mobile_delight_inline_chat_uses_shared_session_helper() -> None:
    """Inline delight chat must use the same mobile chat session contract as chat.js."""

    js = RECOMMEND_JS.read_text()

    assert "getMobileChatSession" in js
    assert 'session: "mobile"' not in js
    assert 'fetchChatTurns({ session: "mobile"' not in js


def test_mobile_delight_actions_stay_hidden_for_permanent_handled_states() -> None:
    """Viewed/liked/rejected delights should not keep generic action buttons visible."""

    js = RECOMMEND_JS.read_text()

    assert 'class="delight-result-state"' in js
    assert "always rendered" not in js
    assert "} else {\n    // Action buttons" in js
