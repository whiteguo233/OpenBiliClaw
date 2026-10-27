"""Real mobile UI using the real config API and an isolated config file."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .test_jev_evaluation_config import _client, _configured
from .test_mobile_web_probe_delight_e2e import (
    chromium_page as chromium_page,
)
from .test_mobile_web_probe_delight_e2e import (
    mobile_web_server as mobile_web_server,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest
    from playwright.sync_api import Page, Route


def test_mobile_can_configure_jev_and_reload_without_exposing_saved_token(
    mobile_web_server: tuple,
    chromium_page: Page,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    base_url, _ = mobile_web_server
    client = _client(monkeypatch, tmp_path, _configured())

    def config_api(route: Route) -> None:
        request = route.request
        if request.method == "PUT":
            response = client.put("/api/config", json=request.post_data_json)
        else:
            response = client.get("/api/config")
        route.fulfill(
            status=response.status_code, content_type="application/json", body=response.content
        )

    page = chromium_page
    page.route("**/api/config*", config_api)
    page.goto(f"{base_url}/m/#/recommend")
    page.get_by_role("button", name="打开保存与候选评估设置").click()
    root = page.locator("#mobileJevConnectionSettings")
    root.get_by_label("启用 Jev 服务连接", exact=True).check()
    root.get_by_label("Token", exact=True).fill("synthetic-mobile-token")
    root.get_by_label("API URL（完整接口地址）", exact=True).fill(
        "https://jev.example.test/v1/systemone"
    )
    root.get_by_label("固定模型 ID", exact=True).fill("jev-1.14.0")
    page.locator("#mobile-eval-scorer").select_option("jev")
    page.get_by_role("button", name="保存设置", exact=True).click()
    page.locator(".mobile-settings-status").filter(has_text="设置已保存").wait_for()
    assert root.get_by_label("Token", exact=True).input_value() == ""
    assert "synthetic-mobile-token" not in page.locator("body").inner_text()
    page.get_by_role("button", name="关闭设置", exact=True).click()
    page.get_by_role("button", name="打开保存与候选评估设置").click()
    page.locator(".mobile-settings-status").filter(has_text="设置已加载").wait_for()
    assert page.locator("#mobile-eval-scorer").input_value() == "jev"
    assert root.get_by_label("固定模型 ID", exact=True).input_value() == "jev-1.14.0"
    assert (
        root.get_by_label("API URL（完整接口地址）", exact=True).input_value()
        == "https://jev.example.test/v1/systemone"
    )
    assert root.get_by_label("Token", exact=True).input_value() == ""
