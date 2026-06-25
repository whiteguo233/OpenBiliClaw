import re
from pathlib import Path


def test_desktop_web_starts_with_empty_recommendation_list() -> None:
    """Desktop web must not show built-in demo cards as real recommendations."""
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    match = re.search(
        r"\n\s+videos:\s*(?P<value>\[[\s\S]*?\])\s*,\n\s+messages:",
        app_js,
    )
    assert match is not None, "desktop initial videos state not found"
    assert match.group("value").strip() == "[]"
    assert "为什么说回县城你也躺不平" not in app_js
    assert "Concrete, light and silence" not in app_js


def test_desktop_backend_hydration_clears_empty_recommendations() -> None:
    """An empty backend recommendation response must clear stale local cards."""
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    hydrate = re.search(
        r"async function hydrateFromBackend\(\) \{(?P<body>.*?)\n    \}",
        app_js,
        flags=re.S,
    )
    assert hydrate is not None, "desktop hydrateFromBackend not found"
    body = hydrate.group("body")
    assert "const recommendationItems = Array.isArray(recs) ? recs : asArray(recs?.items);" in body
    assert "state.videos = normalizeRecommendationList(recommendationItems);" in body
    assert "if (recommendationItems.length) state.videos" not in body


def test_desktop_pool_status_shows_available_count() -> None:
    """Desktop web UI displays pool_available_count for inventory status."""
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
    index_html = Path("src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")

    assert "pool_available_count" in app_js
    assert "还有 ${runtime.pool_available_count} 条可换" in app_js
    assert "暂无可换库存" in app_js
    assert "当前可换库存" in index_html
    assert "当前可换" in index_html


def test_desktop_hydration_refetches_runtime_after_recommendation_bootstrap() -> None:
    """GET /recommendations may bootstrap-serve, so runtime is refreshed afterwards."""
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    hydrate = re.search(
        r"async function hydrateFromBackend\(\) \{(?P<body>.*?)\n    \}",
        app_js,
        flags=re.S,
    )
    assert hydrate is not None, "desktop hydrateFromBackend not found"
    body = hydrate.group("body")
    assert (
        "await requestJson(ENDPOINTS.runtimeStatus).catch(() => runtime?.status || runtime)"
        in body
    )
    assert "applyRuntimeStatus(effectiveRuntime?.status || effectiveRuntime);" in body


def test_desktop_pool_status_labels_pending_signals_as_discovery_context() -> None:
    """Pending runtime signals are discovery context, not unprocessed profile events."""
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
    index_html = Path("src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")

    assert "待处理 ${runtime.pending_signal_events} 条行为信号" not in app_js
    assert "已记下 ${runtime.pending_signal_events} 个新动作" in app_js
    assert "待处理行为信号" not in index_html
    assert "新动作" in index_html


def test_desktop_replenished_label_distinguishes_previous_success_from_current_status() -> None:
    """The replenish count is historical, so its label must not read as this round."""
    index_html = Path("src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")

    assert "上次成功补货" in index_html
    assert "最近补货" not in index_html


def test_desktop_source_metric_uses_configured_source_count() -> None:
    """Desktop web UI should use configured sources, not visible cards."""
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert "function configuredSourceCount()" in app_js
    assert 'Object.prototype.hasOwnProperty.call(value, "enabled")' in app_js
    assert "pool_source_shares" in app_js
    assert "state.runtimeStatus?.pool_source_count" not in app_js
    assert "currentRecommendationSourceCount" not in app_js


def test_desktop_recommendation_filters_include_enabled_sources() -> None:
    """Recommendation source tabs come from enabled config, not only visible cards."""
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert "const sourceFilterDefinitions = [" in app_js
    assert '{ key: "twitter", label: "X (Twitter)" }' in app_js
    assert 'twitter: "X (Twitter)"' in app_js

    build_filters = re.search(
        r"function buildFilters\(\) \{(?P<body>.*?)\n    \}",
        app_js,
        flags=re.S,
    )
    assert build_filters is not None, "desktop buildFilters not found"
    body = build_filters.group("body")
    assert "configuredSourceFilterLabels()" in body
    assert "state.videos" in body
    assert "sourceFilterOrder.filter" in body

    filtered_videos = re.search(
        r"function filteredVideos\(\) \{(?P<body>.*?)\n    \}",
        app_js,
        flags=re.S,
    )
    assert filtered_videos is not None, "desktop filteredVideos not found"
    assert "platformName(item.platform)" in filtered_videos.group("body")


def test_desktop_renders_x_recommendations_as_text_cards() -> None:
    """Desktop web should not render text-only X tweets as empty/broken covers."""
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
    app_css = Path("src/openbiliclaw/web/desktop/assets/css/app.css").read_text(encoding="utf-8")

    normalize_recommendation = re.search(
        r"function normalizeRecommendation\(item\) \{(?P<body>.*?)\n    \}",
        app_js,
        flags=re.S,
    )
    assert normalize_recommendation is not None, "desktop normalizeRecommendation not found"
    normalize_body = normalize_recommendation.group("body")
    assert "content_type" in normalize_body
    assert "body_text" in normalize_body
    assert "normalizeSourcePlatform" in normalize_body

    render_videos = re.search(
        r"function renderVideos\(\) \{(?P<body>.*?)\n    \}",
        app_js,
        flags=re.S,
    )
    assert render_videos is not None, "desktop renderVideos not found"
    render_body = render_videos.group("body")
    assert "recommendationMediaHtml(item)" in render_body

    media_html = re.search(
        r"function recommendationMediaHtml\(item\) \{(?P<body>.*?)\n    \}",
        app_js,
        flags=re.S,
    )
    assert media_html is not None, "desktop recommendationMediaHtml not found"
    assert "cover-text" in media_html.group("body")
    assert "coverImg(item)" in media_html.group("body")

    cover_class = re.search(
        r"function recommendationCoverClass\(item\) \{(?P<body>.*?)\n    \}",
        app_js,
        flags=re.S,
    )
    assert cover_class is not None, "desktop recommendationCoverClass not found"
    assert "is-text-card" in cover_class.group("body")
    assert "tweet" in app_js

    assert ".cover.is-text-card" in app_css
    assert ".cover-text" in app_css


def test_desktop_click_payload_keeps_x_source_metadata() -> None:
    """Desktop click reporting must not rely on backend URL guessing for X."""
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    click_fn = re.search(
        r"function trackRecommendationClick\(item\) \{(?P<body>.*?)\n    \}",
        app_js,
        flags=re.S,
    )
    assert click_fn is not None, "desktop trackRecommendationClick not found"
    body = click_fn.group("body")
    assert "content_id" in body
    assert "content_url" in body
    assert "source_platform" in body


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
