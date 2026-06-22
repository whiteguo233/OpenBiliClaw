"""Static regressions for PCWeb model service probe controls."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_desktop_web_settings_exposes_and_wires_model_probe_controls() -> None:
    html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
    css = (ROOT / "src/openbiliclaw/web/desktop/assets/css/app.css").read_text(encoding="utf-8")

    assert 'id="probeLlm"' in html
    assert 'id="probeEmbedding"' in html
    assert 'id="probeLlmStatus"' in html
    assert 'id="probeEmbeddingStatus"' in html
    assert 'aria-live="polite"' in html

    assert 'configProbe: "/config/probe-service"' in js
    assert "function probeConfigService(kind, config)" in js
    assert 'probeConfigService("llm", buildConfigUpdate())' in js
    assert 'probeConfigService("embedding", buildConfigUpdate())' in js
    assert "function renderProbeResult" in js

    assert ".settings-probe-row" in css
    assert ".settings-probe-status" in css


def test_desktop_web_settings_always_sends_deepseek_reasoning_effort() -> None:
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert 'if (deepseekReasoning || provider === "deepseek"' not in js
    assert re.search(
        r"const deepseekReasoning = getInput\(\"deepseekReasoning\"\);"
        r"\s+llm\.deepseek = \{"
        r"(?s:.*?)reasoning_effort: deepseekReasoning",
        js,
    )
