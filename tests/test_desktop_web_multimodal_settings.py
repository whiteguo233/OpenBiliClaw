"""Static regressions for multimodal discovery-evaluation settings."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_desktop_web_settings_wires_multimodal_discovery_controls() -> None:
    html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    for element_id in (
        "multimodalEvaluationEnabled",
        "multimodalBatchSize",
        "multimodalImageMaxPx",
        "multimodalImageQuality",
        "multimodalImageTimeout",
        "multimodalEvaluationStatus",
    ):
        assert f'id="{element_id}"' in html

    assert "const discovery = config.discovery || {}" in js
    assert 'setSelect("multimodalEvaluationEnabled"' in js
    assert 'setInput("multimodalBatchSize", discovery.multimodal_batch_size)' in js
    assert "discovery: {" in js
    assert "multimodal_evaluation_enabled:" in js
    assert "multimodal_batch_size: getIntInput(\"multimodalBatchSize\", 8)" in js
    assert "multimodal_image_max_px: getIntInput(\"multimodalImageMaxPx\", 384)" in js
