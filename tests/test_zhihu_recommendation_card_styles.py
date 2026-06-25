"""Static regressions for Zhihu recommendation card source styling."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_zhihu_recommendation_cards_have_source_specific_styles() -> None:
    desktop_css = (ROOT / "src/openbiliclaw/web/desktop/assets/css/app.css").read_text(
        encoding="utf-8"
    )
    mobile_css = (ROOT / "src/openbiliclaw/web/css/app.css").read_text(encoding="utf-8")
    popup_html = (ROOT / "extension/popup/popup.html").read_text(encoding="utf-8")

    assert '.cover[data-platform="zhihu"]' in desktop_css
    assert '.card-source[data-source="zhihu"]' in mobile_css
    assert ".source-platform-zhihu" in popup_html
    assert "padding: var(--space-3) calc(var(--space-3) + 76px)" in desktop_css
    assert "padding: 12px 96px 12px 14px" in mobile_css
    assert "padding: 12px 74px 34px 14px" in popup_html
