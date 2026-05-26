from pathlib import Path


def test_desktop_pool_status_shows_available_count() -> None:
    """Desktop web UI displays pool_available_count for inventory status."""
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert "pool_available_count" in app_js
    assert "还有 ${runtime.pool_available_count} 条可换" in app_js
    assert "暂无可换库存" in app_js


def test_desktop_source_metric_uses_configured_source_count() -> None:
    """Desktop web UI should use configured sources, not visible cards."""
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert "function configuredSourceCount()" in app_js
    assert 'Object.prototype.hasOwnProperty.call(value, "enabled")' in app_js
    assert "pool_source_shares" in app_js
    assert "state.runtimeStatus?.pool_source_count" not in app_js
    assert "currentRecommendationSourceCount" not in app_js
