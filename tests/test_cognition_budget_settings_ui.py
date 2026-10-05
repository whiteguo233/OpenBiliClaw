"""Static UI contracts for the issue #169 cognition-budget settings."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESKTOP_HTML = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
DESKTOP_JS = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
POPUP_HTML = (ROOT / "extension/popup/popup.html").read_text(encoding="utf-8")
POPUP_JS = (ROOT / "extension/popup/popup.js").read_text(encoding="utf-8")


def test_desktop_settings_render_and_save_cognition_budget_controls() -> None:
    for element_id, minimum, maximum, default in (
        ("awarenessEventBatchSize", "10", "900", "300"),
        ("insightNoteBatchSize", "10", "450", "150"),
        ("cognitionMaxTokens", "1024", "128000", "32768"),
    ):
        assert f'id="{element_id}"' in DESKTOP_HTML
        assert f'min="{minimum}"' in DESKTOP_HTML
        assert f'max="{maximum}"' in DESKTOP_HTML
        assert f'placeholder="{default}"' in DESKTOP_HTML

    assert "const soul = config.soul || {}" in DESKTOP_JS
    assert (
        'setInput("awarenessEventBatchSize", soul.awareness_event_batch_size ?? 300)' in DESKTOP_JS
    )
    assert 'setInput("insightNoteBatchSize", soul.insight_note_batch_size ?? 150)' in DESKTOP_JS
    assert 'setInput("cognitionMaxTokens", soul.cognition_max_tokens ?? 32768)' in DESKTOP_JS
    assert "soul: {" in DESKTOP_JS
    assert 'awareness_event_batch_size: getIntInput("awarenessEventBatchSize", 300)' in DESKTOP_JS
    assert 'insight_note_batch_size: getIntInput("insightNoteBatchSize", 150)' in DESKTOP_JS
    assert 'cognition_max_tokens: getIntInput("cognitionMaxTokens", 32768)' in DESKTOP_JS


def test_extension_settings_render_and_save_cognition_budget_controls() -> None:
    for element_id, minimum, maximum, default in (
        ("cfgAwarenessEventBatchSize", "10", "900", "300"),
        ("cfgInsightNoteBatchSize", "10", "450", "150"),
        ("cfgCognitionMaxTokens", "1024", "128000", "32768"),
    ):
        assert f'id="{element_id}"' in POPUP_HTML
        assert f'min="{minimum}"' in POPUP_HTML
        assert f'max="{maximum}"' in POPUP_HTML
        assert f'placeholder="{default}"' in POPUP_HTML

    assert "cfg.soul?.awareness_event_batch_size ?? 300" in POPUP_JS
    assert "cfg.soul?.insight_note_batch_size ?? 150" in POPUP_JS
    assert "cfg.soul?.cognition_max_tokens ?? 32768" in POPUP_JS
    assert "soul: {" in POPUP_JS
    assert 'awareness_event_batch_size: getInt("cfgAwarenessEventBatchSize", 300)' in POPUP_JS
    assert 'insight_note_batch_size: getInt("cfgInsightNoteBatchSize", 150)' in POPUP_JS
    assert 'cognition_max_tokens: getInt("cfgCognitionMaxTokens", 32768)' in POPUP_JS


def test_desktop_settings_render_and_save_reply_tone_controls() -> None:
    assert 'id="replyStyle"' in DESKTOP_HTML
    assert 'id="dialogueTonePrompt"' in DESKTOP_HTML
    assert 'id="testToneBtn"' in DESKTOP_HTML
    assert 'id="testToneResult"' in DESKTOP_HTML
    assert 'id="replyStyle" type="text" maxlength="200"' in DESKTOP_HTML
    assert 'id="dialogueTonePrompt" rows="4" maxlength="1000"' in DESKTOP_HTML

    assert 'setInput("replyStyle", soul.reply_style ?? "")' in DESKTOP_JS
    assert 'setInput("dialogueTonePrompt", soul.dialogue_tone_prompt ?? "")' in DESKTOP_JS
    assert 'reply_style: getInput("replyStyle")' in DESKTOP_JS
    assert 'dialogue_tone_prompt: getInput("dialogueTonePrompt")' in DESKTOP_JS
    assert 'chat: "/chat"' in DESKTOP_JS
    assert 'safeBind("#testToneBtn", "click"' in DESKTOP_JS


def test_extension_settings_render_and_save_reply_tone_controls() -> None:
    assert 'id="cfgReplyStyle"' in POPUP_HTML
    assert 'id="cfgDialogueTonePrompt"' in POPUP_HTML
    assert 'id="cfgTestTone"' in POPUP_HTML
    assert 'id="cfgTestToneResult"' in POPUP_HTML
    assert 'id="cfgReplyStyle" type="text" maxlength="200"' in POPUP_HTML
    assert 'id="cfgDialogueTonePrompt" rows="3" maxlength="1000"' in POPUP_HTML

    assert 'cfg.soul?.reply_style ?? ""' in POPUP_JS
    assert 'cfg.soul?.dialogue_tone_prompt ?? ""' in POPUP_JS
    assert 'reply_style: getVal("cfgReplyStyle")' in POPUP_JS
    assert 'dialogue_tone_prompt: getVal("cfgDialogueTonePrompt")' in POPUP_JS
    assert 'sendChatMessage("用一两句话聊聊你现在的心情")' in POPUP_JS
