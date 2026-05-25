from pathlib import Path


def test_desktop_pool_status_shows_available_count() -> None:
    """Desktop web UI displays pool_available_count for inventory status."""
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text()

    assert "pool_available_count" in app_js
    assert "还有 ${runtime.pool_available_count} 条可换" in app_js
    assert "暂无可换库存" in app_js
