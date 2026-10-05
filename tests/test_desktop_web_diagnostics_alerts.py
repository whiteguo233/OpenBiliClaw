"""桌面 Web 设置页「异常报警」区的结构契约测试。

覆盖 index.html 挂载点、app.js 的拉取/轮询/实时刷新接线、CSS 样式，
以及后端 `/api/diagnostics/alerts` 端点注册。
"""

from pathlib import Path

INDEX_HTML = Path("src/openbiliclaw/web/desktop/index.html")
APP_JS = Path("src/openbiliclaw/web/desktop/assets/js/app.js")
APP_CSS = Path("src/openbiliclaw/web/desktop/assets/css/app.css")
API_APP = Path("src/openbiliclaw/api/app.py")


def test_settings_logging_panel_mounts_diagnostics_alerts_section() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")

    for element_id in (
        "diagnosticsAlerts",
        "diagnosticsAlertsSummary",
        "refreshDiagnosticsAlertsBtn",
        "diagnosticsAlertsEmpty",
        "diagnosticsAlertList",
    ):
        assert f'id="{element_id}"' in html, f"index.html must expose #{element_id}"

    # 空态文案、实时区域语义与初始隐藏的列表。
    assert "暂无异常报警" in html
    assert 'aria-live="polite"' in html
    assert 'id="diagnosticsAlertList" hidden' in html


def test_app_js_fetches_polls_and_renders_alerts_safely() -> None:
    app_js = APP_JS.read_text(encoding="utf-8")

    # requestJson 会自动拼接 "/api" base，路径绝不能再带前缀
    # （曾真实复现过 /api/api/diagnostics/alerts → 404）。
    assert 'requestJson("/diagnostics/alerts?limit=50"' in app_js
    assert 'requestJson("/api/' not in app_js

    # 面板可见时启动 10s 轮询，切走即停止；后台标签页不请求。
    assert 'panelName === "logging") startDiagnosticsAlertFeed();' in app_js
    assert "else stopDiagnosticsAlertFeed();" in app_js
    assert "DIAGNOSTICS_ALERT_POLL_MS = 10000" in app_js
    assert "if (document.hidden) return;" in app_js

    # 实时流：收到 diagnostics.alert 且日志面板可见时立即刷新，不做无谓请求。
    stream_handler = app_js.split('event.type === "diagnostics.alert"', 1)[1]
    stream_body = stream_handler.split("}", 1)[0]
    assert '[data-settings-panel="logging"]' in stream_body
    assert "refreshDiagnosticsAlerts()" in stream_body

    # 渲染必须经 escapeHtml（message/source 来自 provider 异常文本）。
    render_fn = (
        APP_JS.read_text(encoding="utf-8")
        .split("function renderDiagnosticsAlerts(payload)", 1)[1]
        .split("\n    }", 1)[0]
    )
    assert 'escapeHtml(String(alert.message || ""))' in render_fn
    assert "escapeHtml(source)" in render_fn or "escapeHtml(categoryLabel)" in render_fn

    # 手动刷新按钮经 safeBind 接线。
    assert "#refreshDiagnosticsAlertsBtn" in app_js
    assert '"#refreshDiagnosticsAlertsBtn", "click"' in app_js


def test_styles_cover_both_severity_levels() -> None:
    css = APP_CSS.read_text(encoding="utf-8")

    for selector in (
        ".diag-alerts {",
        ".diag-alert-list {",
        '.diag-alert-item[data-severity="error"]',
        '.diag-alert-item[data-severity="warning"] .diag-alert-badge',
        ".diag-alert-badge",
        ".diag-alert-message",
        ".diag-alert-meta",
    ):
        assert selector in css, f"app.css must style {selector.strip()}"


def test_backend_registers_diagnostics_alerts_endpoint() -> None:
    app_py = API_APP.read_text(encoding="utf-8")

    assert '@app.get("/api/diagnostics/alerts")' in app_py
    assert "get_diagnostics_alert_buffer().snapshot(since_id=since_id, limit=limit)" in app_py
