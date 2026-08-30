"""Static regressions for advanced discovery settings."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_desktop_web_settings_wires_multimodal_discovery_controls() -> None:
    html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    for element_id in (
        "candidateEvalConcurrency",
        "multimodalEvaluationEnabled",
        "multimodalBatchSize",
        "multimodalImageMaxPx",
        "multimodalImageQuality",
        "multimodalImageTimeout",
        "multimodalEvaluationStatus",
    ):
        assert f'id="{element_id}"' in html

    assert (
        'id="candidateEvalConcurrency" type="number" min="1" max="3" step="1" placeholder="3"'
        in html
    )
    assert "const discovery = config.discovery || {}" in js
    assert 'setInput("candidateEvalConcurrency", discovery.candidate_eval_concurrency ?? 3)' in js
    assert "multimodalEvaluation.checked = discovery.multimodal_evaluation_enabled === true" in js
    assert 'setInput("multimodalBatchSize", discovery.multimodal_batch_size ?? 8)' in js
    assert "discovery: {" in js
    assert 'candidate_eval_concurrency: getIntInput("candidateEvalConcurrency", 3)' in js
    assert 'setInput("llmConcurrency", llm.concurrency ?? 3)' in js
    assert 'concurrency: getIntInput("llmConcurrency", 3)' in js
    assert "multimodal_evaluation_enabled:" in js
    assert 'multimodal_batch_size: getIntInput("multimodalBatchSize", 8)' in js
    assert 'multimodal_image_max_px: getIntInput("multimodalImageMaxPx", 384)' in js


def test_desktop_web_advanced_panel_round_trips_evaluator_mode() -> None:
    html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    control = re.search(r'<select id="evalScorer"[\s\S]*?</select>', html)
    assert control is not None
    control_html = control.group(0)
    assert 'aria-describedby="evalScorerHint"' in control_html
    assert '<option value="llm" selected>Agent（默认）</option>' in control_html
    assert '<option value="shadow">Shadow（校准观察）</option>' in control_html
    assert '<option value="learned">Learned（仅相关性，实验性）</option>' in control_html
    assert 'id="evalScorerHint"' in html
    assert "人工运行质量门禁并确认通过" in html
    assert "切换不会重算已有推荐" in html
    assert 'setSelect("evalScorer", discovery.eval_scorer || "llm")' in js
    assert 'eval_scorer: $("#evalScorer")?.value || "llm"' in js


def test_desktop_advanced_panel_owns_all_moved_controls_and_recommendation_fields() -> None:
    html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    advanced = re.search(
        r'<div id="settingsPanelAdvanced"[\s\S]*?</div>\s*\n\s*<div id="settingsPanelGeneral"',
        html,
    )
    assert advanced is not None
    advanced_html = advanced.group(0)
    models_html = html[
        html.index('id="settingsPanelModels"') : html.index('id="settingsPanelSources"')
    ]
    scheduler_html = html[
        html.index('id="settingsPanelScheduler"') : html.index('id="settingsPanelAdvanced"')
    ]

    for element_id in (
        "evalScorer",
        "visualProfileEnabled",
        "keyframeEnabled",
        "keyframeMaxFrames",
        "keyframeFetchLimit",
        "danmakuEnabled",
        "danmakuFetchLimit",
        "danmakuMaxChars",
        "embeddingMultimodalEnabled",
        "candidateEvalConcurrency",
        "multimodalEvaluationEnabled",
        "multimodalBatchSize",
        "multimodalImageMaxPx",
        "multimodalImageQuality",
        "multimodalImageTimeout",
        "keywordGenerationMode",
        "awarenessEventBatchSize",
        "insightNoteBatchSize",
        "cognitionMaxTokens",
    ):
        assert advanced_html.count(f'id="{element_id}"') == 1
        assert html.count(f'id="{element_id}"') == 1

    for element_id in (
        "visualProfileEnabled",
        "keyframeEnabled",
        "embeddingMultimodalEnabled",
        "multimodalEvaluationEnabled",
    ):
        control = re.search(rf'id="{element_id}"[^>]*>', advanced_html)
        assert control is not None
        assert "checked" not in control.group(0)

    assert 'id="embeddingMultimodalEnabled"' not in models_html
    for element_id in (
        "evalScorer",
        "keywordGenerationMode",
        "candidateEvalConcurrency",
        "multimodalEvaluationEnabled",
        "multimodalBatchSize",
        "multimodalImageMaxPx",
        "multimodalImageQuality",
        "multimodalImageTimeout",
    ):
        assert f'id="{element_id}"' not in scheduler_html

    for element_id, minimum, maximum, default in (
        ("keyframeMaxFrames", "1", "12", "4"),
        ("keyframeFetchLimit", "1", "200", "50"),
        ("danmakuFetchLimit", "1", "200", "50"),
        ("danmakuMaxChars", "100", "2000", "500"),
        ("awarenessEventBatchSize", "10", "900", "300"),
        ("insightNoteBatchSize", "10", "450", "150"),
        ("cognitionMaxTokens", "1024", "128000", "32768"),
    ):
        control = re.search(rf'id="{element_id}"[^>]*>', advanced_html)
        assert control is not None
        assert f'min="{minimum}"' in control.group(0)
        assert f'max="{maximum}"' in control.group(0)
        assert f'placeholder="{default}"' in control.group(0)

    assert advanced_html.count("<section") == 5
    assert advanced_html.count("候选评分模式") >= 1
    assert advanced_html.count("推荐增强") >= 1
    assert advanced_html.count("多模态处理") >= 1
    assert advanced_html.count("搜索词生成") >= 1
    assert advanced_html.count("认知循环预算") >= 1


def test_desktop_settings_tabs_have_tabpanel_contract_and_advanced_registration() -> None:
    html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
    css = (ROOT / "src/openbiliclaw/web/desktop/assets/css/app.css").read_text(encoding="utf-8")

    tab_names = ("Models", "Sources", "Scheduler", "Advanced", "General", "Frontend", "Logging")
    assert len(re.findall(r'class="settings-tab(?:\s|")', html)) == 7
    for name in tab_names:
        tab_start = html.index(f'id="settingsTab{name}"')
        tab_html = html[tab_start : html.index("</button>", tab_start) + len("</button>")]
        assert f'id="settingsTab{name}"' in tab_html
        assert 'role="tab"' in tab_html
        assert 'aria-selected="true"' in tab_html or 'aria-selected="false"' in tab_html
        assert f'aria-controls="settingsPanel{name}"' in html
        panel_start = html.index(f'id="settingsPanel{name}"')
        panel_html = html[panel_start : html.index(">", panel_start) + 1]
        assert f'id="settingsPanel{name}"' in panel_html
        assert 'role="tabpanel"' in panel_html
        assert f'aria-labelledby="settingsTab{name}"' in html

    assert '"advanced"' in js
    assert 'tab.setAttribute("aria-selected", isActive ? "true" : "false")' in js
    assert "tab.tabIndex = isActive ? 0 : -1" in js
    assert 'tab.addEventListener("click"' in js
    assert ".settings-tab:focus-visible" in css
    assert "grid-template-columns: repeat(7, minmax(0, 1fr))" in css
    assert "grid-template-columns: repeat(7, minmax(92px, 1fr))" in css
    assert "overflow-x: auto" in css


def test_desktop_advanced_discovery_fields_load_and_save_after_snapshot_spread() -> None:
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
    spread = "...(state.config?.discovery || {})"
    assert spread in js
    spread_index = js.index(spread)

    for load_snippet in (
        'setSelect("evalScorer", discovery.eval_scorer || "llm")',
        "visualProfile.checked = discovery.visual_profile_enabled === true",
        "keyframe.checked = discovery.keyframe_enabled === true",
        'setInput("keyframeMaxFrames", discovery.keyframe_max_frames ?? 4)',
        'setInput("keyframeFetchLimit", discovery.keyframe_fetch_limit ?? 50)',
        "danmaku.checked = discovery.danmaku_enabled === true",
        'setInput("danmakuFetchLimit", discovery.danmaku_fetch_limit ?? 50)',
        'setInput("danmakuMaxChars", discovery.danmaku_max_chars ?? 500)',
    ):
        assert load_snippet in js

    for save_snippet in (
        'eval_scorer: $("#evalScorer")?.value || "llm"',
        'visual_profile_enabled: $("#visualProfileEnabled")?.checked === true',
        'keyframe_enabled: $("#keyframeEnabled")?.checked === true',
        'keyframe_max_frames: getIntInput("keyframeMaxFrames", 4)',
        'keyframe_fetch_limit: getIntInput("keyframeFetchLimit", 50)',
        'danmaku_enabled: $("#danmakuEnabled")?.checked === true',
        'danmaku_fetch_limit: getIntInput("danmakuFetchLimit", 50)',
        'danmaku_max_chars: getIntInput("danmakuMaxChars", 500)',
    ):
        assert save_snippet in js
        assert spread_index < js.index(save_snippet)


def test_desktop_settings_save_requires_dirty_state_and_guards_reentry() -> None:
    html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    save_button = re.search(r'id="settingsSaveBtn"[^>]*>', html)
    assert save_button is not None
    assert 'type="submit"' in save_button.group(0)
    assert "disabled" in save_button.group(0)

    assert "let settingsSaveInFlight = false;" in js
    assert "if (save) save.disabled = settingsSaveInFlight || count === 0;" in js
    assert "if (settingsSaveInFlight || settingsDirtyFields.size === 0)" in js
    assert "settingsSaveInFlight = true;" in js
    assert "settingsSaveInFlight = false;" in js
    assert "submitBtn.disabled = false" not in js
    # Chain reorder/add, instance draft save/delete and suggested shares mutate
    # form state programmatically, so they must also unlock the save button.
    assert js.count("markSettingsDirty();") >= 6
