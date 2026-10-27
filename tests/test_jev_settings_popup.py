"""Popup source assets in Chromium, with Chrome APIs stubbed at their boundary."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from .test_desktop_web_settings_save_e2e import (
    chromium_page as chromium_page,
)
from .test_desktop_web_settings_save_e2e import (
    settings_save_server as settings_save_server,
)
from .test_jev_evaluation_config import _client, _configured

if TYPE_CHECKING:
    import pytest
    from playwright.sync_api import Page, Route

ROOT = Path(__file__).resolve().parents[1]


def test_popup_saves_jev_connection_through_the_real_config_api(
    settings_save_server: tuple,
    chromium_page: Page,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    base_url, _ = settings_save_server
    page = chromium_page
    page.set_viewport_size({"width": 420, "height": 900})
    config = _configured()
    config.llm.deepseek.model = "deepseek-chat"
    client = _client(monkeypatch, tmp_path, config)

    def popup_assets(route: Route) -> None:
        path = urlsplit(route.request.url).path.removeprefix("/popup/")
        file = ROOT / "extension/popup" / path
        if path.startswith("shared/"):
            file = ROOT / "src/openbiliclaw/web" / path
        if not file.is_file():
            route.fulfill(status=404, body="not found")
            return
        route.fulfill(
            content_type=mimetypes.guess_type(file.name)[0] or "text/plain", body=file.read_bytes()
        )

    def config_api(route: Route) -> None:
        if route.request.method == "PUT":
            response = client.put("/api/config", json=route.request.post_data_json)
        else:
            response = client.get("/api/config")
        route.fulfill(
            status=response.status_code, content_type="application/json", body=response.content
        )

    page.route("**/popup/**", popup_assets)
    page.route("**/api/config*", config_api)
    port = urlsplit(base_url).port
    page.add_init_script(f"""
      const fixtureStore = {{popup_backend_endpoint: {{
        scheme: 'http', host: '127.0.0.1', port: {port}
      }}}};
      window.chrome = {{
        storage: {{local: {{
          get: (keys, cb) => {{cb?.(fixtureStore); return Promise.resolve(fixtureStore);}},
          set: (values, cb) => {{
            Object.assign(fixtureStore, values); cb?.(); return Promise.resolve();
          }},
          remove: (keys, cb) => {{cb?.(); return Promise.resolve();}}
        }}, onChanged: {{addListener() {{}}}}}},
        runtime: {{id: 'isolated-popup-fixture', getURL: path => '/popup/' + path,
          getManifest: () => ({{version: '0.0.0'}}), sendMessage: async () => ({{}}),
          onMessage: {{addListener() {{}}, removeListener() {{}}}}}},
        tabs: {{create: async () => ({{}})}}
      }};
    """)
    page.goto(base_url + "/popup/popup.html")
    page.locator("#settingsGear").click()
    page.get_by_role("tab", name="高级功能").click()
    root = page.locator("#cfgJevConnectionSettings")
    root.get_by_label("启用 Jev 服务连接", exact=True).check()
    root.get_by_label("Token", exact=True).fill("synthetic-popup-token")
    page.locator("#cfgEvalScorer").select_option("jev")
    with page.expect_response(
        lambda response: response.request.method == "PUT" and response.url.endswith("/api/config")
    ) as result:
        page.locator("#settingsSave").click()
    assert result.value.status == 202, result.value.text()
    root.get_by_text("本机已保存 Token。", exact=False).wait_for()
    assert root.get_by_label("Token", exact=True).input_value() == ""
    saved = client.get("/api/config").json()
    assert saved["discovery"]["eval_scorer"] == "jev"
    assert saved["discovery"]["jev_provider"]["ready"] is True
