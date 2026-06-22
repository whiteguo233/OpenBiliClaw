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
    popup.js behaviour (fix 79042ce). ``config_reloaded`` still hydrates through
    the broad-reload path; ``init_completed`` hydrates only after
    ``refreshInitStatus`` observes the initialized transition, avoiding duplicate
    fetches/toasts. Pool/header counts keep updating via the unconditional
    ``applyRuntimeStatus`` call.
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
    assert "init_completed" not in trigger


def test_desktop_web_shows_github_star_cta() -> None:
    """Desktop web should ask happy users for a GitHub Star in the top bar."""
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
    app_css = Path("src/openbiliclaw/web/desktop/assets/css/app.css").read_text(encoding="utf-8")
    index_html = Path("src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    top_actions = re.search(r'<div class="top-actions"[\s\S]*?</div>', index_html)

    assert top_actions is not None, "desktop top actions block not found"
    assert 'id="starButton"' in top_actions.group(0)
    assert 'id="starCount"' in top_actions.group(0)
    assert "好用求 Star" in top_actions.group(0)
    assert "gh-star-left" in app_css
    assert "gh-star-count" in app_css
    assert 'STAR_REPO_URL = "https://github.com/whiteguo233/OpenBiliClaw"' in app_js
    assert "https://api.github.com/repos/${STAR_REPO_SLUG}" in app_js
    assert "openbiliclaw.webui.starCount" in app_js
    assert "bindStarButton();" in app_js


def test_desktop_delight_cover_loads_with_first_view_priority() -> None:
    """The first-view delight image should not wait for native lazy loading."""
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    match = re.search(
        r"function renderDelightCover\(delight\) \{(?P<body>.*?)\n    \}",
        app_js,
        flags=re.S,
    )
    assert match is not None, "renderDelightCover not found"
    body = match.group("body")
    assert 'image.loading = "eager";' in body
    assert 'image.fetchPriority = "high";' in body
    assert 'image.decoding = "async";' in body


def test_desktop_append_more_renders_before_cover_decode() -> None:
    """Appending recommendations must not block on cover decode/network misses."""
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    match = re.search(
        r"async function appendMore\(\) \{(?P<body>.*?)\n    \}",
        app_js,
        flags=re.S,
    )
    assert match is not None, "appendMore not found"
    body = match.group("body")
    render_index = body.index("state.videos = state.videos.concat(freshItems);")
    warm_index = body.index("warmCoverImages(freshItems")
    assert render_index < warm_index
    assert "await warmCoverImages(freshItems" not in body
    assert "void warmCoverImages(freshItems" in body
