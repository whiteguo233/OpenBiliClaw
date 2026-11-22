"""Static UI contracts for the issue #353 daily LLM spend cap.

The cap is only useful if the user can see and change it. These tests pin the
three surfaces that own scheduler settings (desktop web + extension popup) and
the CLI-facing runtime fields. Mobile web has no scheduler settings page by
design, so it is intentionally out of scope here.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESKTOP_HTML = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
DESKTOP_JS = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
POPUP_HTML = (ROOT / "extension/popup/popup.html").read_text(encoding="utf-8")
POPUP_JS = (ROOT / "extension/popup/popup.js").read_text(encoding="utf-8")
POPUP_HELPERS = (ROOT / "extension/popup/popup-helpers.js").read_text(encoding="utf-8")


def test_desktop_settings_render_and_save_daily_budget_controls() -> None:
    assert 'id="dailyBudgetCny"' in DESKTOP_HTML
    assert 'id="budgetOnExceed"' in DESKTOP_HTML
    assert 'id="dailyBudgetStatus"' in DESKTOP_HTML
    for mode in ("pause", "warn", "stop"):
        assert f'<option value="{mode}">' in DESKTOP_HTML

    assert '"dailyBudgetCny"' in DESKTOP_JS
    assert 'setSelect("budgetOnExceed", scheduler.budget_on_exceed || "pause")' in DESKTOP_JS
    assert 'daily_budget_cny: getFloatInput("dailyBudgetCny", 0)' in DESKTOP_JS
    assert 'budget_on_exceed: $("#budgetOnExceed")?.value || "pause"' in DESKTOP_JS
    # Runtime whitelist must carry the fields, or the status line silently
    # shows zeros (the same class of bug as the account-sync chip).
    assert "llm_daily_budget_cny: Number(merged.llm_daily_budget_cny ?? 0)" in DESKTOP_JS
    assert "llm_today_cost_cny: Number(merged.llm_today_cost_cny ?? 0)" in DESKTOP_JS
    assert "renderDailyBudgetStatus" in DESKTOP_JS
    assert "budgetExceededSummary" in DESKTOP_JS
    assert 'id="peakPolicy"' in DESKTOP_HTML
    assert 'id="peakRefillFloor"' in DESKTOP_HTML
    assert 'setSelect("peakPolicy", scheduler.peak_policy || "off")' in DESKTOP_JS
    assert 'peak_refill_floor: getIntInput("peakRefillFloor", 30)' in DESKTOP_JS
    assert 'id="peakHours"' in DESKTOP_HTML
    assert 'id="peakTimezoneOffset"' in DESKTOP_HTML
    assert 'peak_hours: $("#peakHours")?.value?.trim() || ""' in DESKTOP_JS


def test_extension_settings_render_and_save_daily_budget_controls() -> None:
    assert 'id="cfgDailyBudgetCny"' in POPUP_HTML
    assert 'id="cfgBudgetOnExceed"' in POPUP_HTML
    assert 'id="cfgDailyBudgetStatus"' in POPUP_HTML
    for mode in ("pause", "warn", "stop"):
        assert f'<option value="{mode}">' in POPUP_HTML

    assert '"cfgDailyBudgetCny"' in POPUP_JS
    assert 'setVal("cfgBudgetOnExceed", cfg.scheduler?.budget_on_exceed || "pause")' in POPUP_JS
    assert 'daily_budget_cny: getFloat("cfgDailyBudgetCny", 0)' in POPUP_JS
    assert 'budget_on_exceed: getVal("cfgBudgetOnExceed") || "pause"' in POPUP_JS
    assert "renderDailyBudgetStatus" in POPUP_JS
    # The popup normalizer is an explicit-key whitelist too; SSE events rebuild
    # state through it, so a missing key would make the status line flicker back
    # to zero on every runtime event.
    assert "llm_today_cost_cny: Number(status?.llm_today_cost_cny ?? 0)" in POPUP_HELPERS
    assert "llm_daily_budget_state: normalizeText(status?.llm_daily_budget_state)" in POPUP_HELPERS
    assert 'id="cfgPeakPolicy"' in POPUP_HTML
    assert 'id="cfgPeakRefillFloor"' in POPUP_HTML
    assert 'setVal("cfgPeakPolicy", cfg.scheduler?.peak_policy || "off")' in POPUP_JS
    assert 'peak_refill_floor: getInt("cfgPeakRefillFloor", 30)' in POPUP_JS
    assert 'id="cfgPeakHours"' in POPUP_HTML
    assert 'id="cfgPeakTimezoneOffset"' in POPUP_HTML
    assert 'peak_hours: getVal("cfgPeakHours") || ""' in POPUP_JS
