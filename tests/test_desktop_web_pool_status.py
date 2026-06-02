import re
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


def test_desktop_pool_update_does_not_replace_recommendation_list() -> None:
    """refresh.pool_updated is a pool-status signal, not a list refresh.

    The desktop web must not hydrate (which replaces ``state.videos``) when the
    runtime emits ``refresh.pool_updated`` / ``recommendation.reshuffled``,
    otherwise locally appended ("加载更多") cards get wiped out by the latest
    top window from ``/api/recommendations``. This mirrors the recommend.js +
    popup.js behaviour (fix 79042ce). Broad-reload flows (``config_reloaded`` /
    ``init_completed``) still hydrate, and the pool/header counts keep updating
    via the unconditional ``applyRuntimeStatus`` call.
    """
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    match = re.search(
        r"if \(\[[^\]]*\]\.includes\(event\.type\)\) scheduleBackendHydration\(\);",
        app_js,
    )
    assert match is not None, "desktop hydration trigger line not found"
    trigger = match.group(0)
    assert "refresh.pool_updated" not in trigger
    assert "recommendation.reshuffled" not in trigger
    assert "config_reloaded" in trigger
    assert "init_completed" in trigger
