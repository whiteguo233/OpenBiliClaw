"""Popup settings categories remain visible at actual toolbar popup widths."""

import re
from pathlib import Path

import pytest

from tests.test_desktop_web_settings_save_e2e import chromium_page  # noqa: F401


@pytest.mark.parametrize("width", [320, 375, 400, 480, 600])
def test_popup_settings_categories_fit_without_horizontal_scrolling(
    chromium_page,  # noqa: F811
    width: int,
    tmp_path: Path,
) -> None:
    page = chromium_page
    html = (Path(__file__).resolve().parents[1] / "extension/popup/popup.html").read_text()
    # Render the real markup/CSS without extension/backend startup dependencies.
    html = re.sub(r"<script\b[^>]*>[\s\S]*?</script>", "", html)
    page.set_viewport_size({"width": width, "height": 600})
    page.set_content(html)
    page.locator("#settingsOverlay").evaluate("element => element.hidden = false")
    report = page.locator(".settings-tabs").evaluate("""element => ({
        width: element.clientWidth, scrollWidth: element.scrollWidth,
        clipped: [...element.children].filter(tab => {
            const box = tab.getBoundingClientRect();
            return box.left < 0 || box.right > innerWidth;
        }).map(tab => tab.textContent),
    })""")
    assert report["scrollWidth"] <= report["width"], report
    assert report["clipped"] == [], report
    for tab in page.locator(".settings-tab").all():
        tab.click(timeout=5000)
    page.screenshot(path=str(tmp_path / f"popup-{width}.png"))
