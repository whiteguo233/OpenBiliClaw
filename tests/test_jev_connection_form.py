"""Public shared form contract, including destination/token and narrow layouts."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from .test_desktop_web_settings_save_e2e import chromium_page as chromium_page

if TYPE_CHECKING:
    from playwright.sync_api import Page

ROOT = Path(__file__).resolve().parents[1]


def test_probe_uses_draft_and_discards_success_after_edit(chromium_page: Page) -> None:
    page = chromium_page
    page.set_content('<div id="connection"></div>')
    page.add_script_tag(path=str(ROOT / "src/openbiliclaw/web/shared/jev-settings.js"))
    page.evaluate("""() => {
      window.form = OpenBiliClawJevSettings.mount(document.querySelector('#connection'), null, {
        probe: config => {
          window.sentDraft = config;
          return new Promise(resolve => window.finishProbe = resolve);
        }
      });
      form.load({enabled:true, api_key_configured:true, credential_source:'config',
        base_url_supported:true, endpoint_url:'https://relay.example/v1/systemone',
        model:'jev-1.14.0'});
    }""")
    button = page.get_by_role("button", name="测试连接与评估", exact=True)
    assert button.count() == 1
    page.get_by_label("Token", exact=True).fill("synthetic-draft-token")
    button.click()
    assert button.is_disabled()
    assert page.evaluate("sentDraft.decision.providers.jev.api_key") == "synthetic-draft-token"
    page.get_by_label("固定模型 ID", exact=True).fill("different-fixed")
    page.evaluate("finishProbe({ok:true,message:'previous-result'})")
    page.locator('[data-jev="probe-status"]').filter(has_text="配置已修改，请重新测试").wait_for()
    assert "previous-result" not in page.locator("body").inner_text()
    assert "synthetic-draft-token" not in page.locator("body").inner_text()


@pytest.mark.parametrize("width", [375, 768, 1024, 1440])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_shared_form_preserves_equivalent_destination_and_gates_new_target(
    chromium_page: Page, width: int, theme: str
) -> None:
    page = chromium_page
    page.set_viewport_size({"width": width, "height": 1000})
    page.set_content(
        '<body style="margin:16px"><select aria-label="评估器" id="eval">'
        '<option value="llm">Agent</option><option value="jev">Jev</option></select>'
        '<div id="connection"></div></body>'
    )
    page.add_style_tag(path=str(ROOT / "src/openbiliclaw/web/shared/jev-settings.css"))
    page.add_script_tag(path=str(ROOT / "src/openbiliclaw/web/shared/jev-settings.js"))
    page.evaluate(
        """theme => {
      document.body.style.background = theme === 'dark' ? '#171717' : '#fafafa';
      document.body.style.color = theme === 'dark' ? '#fafafa' : '#171717';
      window.fixtureForm = OpenBiliClawJevSettings.mount(
        document.querySelector('#connection'), document.querySelector('#eval'));
      fixtureForm.load({enabled: true, api_key_configured: true,
        credential_source: 'config', base_url_supported: true,
        endpoint_url: 'https://relay.example/api/v1/systemone', model: 'jev-1.14.0'});
    }""",
        theme,
    )
    address = page.get_by_label("API Base URL", exact=True)
    assert address.input_value() == "https://relay.example/api"
    mode = page.get_by_label("接口地址类型", exact=True)
    mode.select_option("endpoint")
    assert (
        page.get_by_label("API URL（完整接口地址）", exact=True).input_value()
        == "https://relay.example/api/v1/systemone"
    )
    assert not page.locator('#eval option[value="jev"]').is_disabled()
    mode.select_option("base")
    assert page.evaluate("fixtureForm.collect()") == {}
    address.fill("https://different.example/api/v1")
    assert page.locator('#eval option[value="jev"]').is_disabled()
    page.get_by_label("Token", exact=True).fill("synthetic-fresh-token")
    assert not page.locator('#eval option[value="jev"]').is_disabled()
    collected = page.evaluate("fixtureForm.collect()")
    settings = collected["decision"]["providers"]["jev"]
    assert settings["base_url"] == "https://different.example/api/v1"
    assert "endpoint_url" not in settings
    assert settings["api_key"] == "synthetic-fresh-token"
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    if width == 375:
        directory = ROOT / "output/playwright"
        directory.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(directory / f"jev-base-url-{theme}-375.png"), full_page=True)
