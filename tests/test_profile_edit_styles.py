import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _css_block(source: str, selector: str) -> str:
    match = re.search(rf"(?:^|\n)\s*{re.escape(selector)}\s*\{{[\s\S]*?\}}", source)
    assert match, f"missing CSS block for {selector}"
    return match.group(0)


def test_profile_edit_interest_tree_uses_grouped_pc_and_compact_popup_styles() -> None:
    desktop_css = (ROOT / "src/openbiliclaw/web/desktop/assets/css/app.css").read_text(
        encoding="utf-8"
    )
    popup_html = (ROOT / "extension/popup/popup.html").read_text(encoding="utf-8")

    desktop_domain = _css_block(desktop_css, ".edit-interest-domain")
    desktop_specifics = _css_block(desktop_css, ".edit-specific-list")
    popup_domain = _css_block(popup_html, ".edit-interest-domain")
    popup_specifics = _css_block(popup_html, ".edit-specific-list")
    popup_add_row = _css_block(popup_html, ".edit-specific-add-row")
    popup_add_button = _css_block(popup_html, ".edit-add-btn")

    assert "border-left: 3px solid" in desktop_domain
    assert "padding-left: 14px" in desktop_specifics
    assert "border-left: 2px solid" in desktop_specifics

    assert "border-left: 2px solid" in popup_domain
    assert "padding-left: 10px" in popup_specifics
    assert "border-left: 1px solid" in popup_specifics
    assert "margin-top: 6px" in popup_add_row
    assert "border: 0" in popup_add_button
    assert "cursor: pointer" in popup_add_button
