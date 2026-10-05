import re
from pathlib import Path


def _function_body(app_js: str, name: str) -> str:
    match = re.search(
        rf"(?:async )?function {re.escape(name)}\([^)]*\) \{{(?P<body>.*?)\n    \}}",
        app_js,
        flags=re.S,
    )
    assert match is not None, f"desktop {name} function not found"
    return match.group("body")


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
    """A *replacing* hydration must clear stale local cards when the backend is empty.

    Background re-hydration (tab resume, config_reloaded, config save) deliberately
    keeps the list — see test_desktop_resume_hydration_preserves_loaded_cards.
    """
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    hydrate = re.search(
        r"async function hydrateFromBackend\([^)]*\) \{(?P<body>.*?)\n    \}",
        app_js,
        flags=re.S,
    )
    assert hydrate is not None, "desktop hydrateFromBackend not found"
    body = hydrate.group("body")
    assert (
        "const shouldReadRecommendations = "
        "shouldHydrateRecommendationList({ replaceRecommendations });" in body
    )
    assert "const recommendationsPromise = shouldReadRecommendations" in body
    assert "? readRecommendationSnapshot()" in body
    assert ": Promise.resolve(null);" in body
    assert "function applyInitialRecommendations(items)" in body
    assert "applyDesktopRecommendationSnapshot(items, { replace: replaceRecommendations });" in body
    assert "await hydrateFromBackend({ replaceRecommendations: true });" in app_js
    assert 'desktopRecommendationLoadState = "empty-success"' in app_js


def test_desktop_hydration_does_not_gate_cards_on_secondary_resources() -> None:
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
    body = _function_body(app_js, "hydrateFromBackend")

    assert "Promise.all([" not in body
    assert "recommendationsPromise.then" in body
    assert "runtimePromise.then" in body
    assert "Promise.allSettled(secondaryPromises)" in body
    assert "ENDPOINTS.ping" in body


def test_desktop_failed_chat_turn_renders_durable_error() -> None:
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert 'status === "failed"' in app_js
    assert 'turn.error || "这句还没发出去，稍后再试。"' in app_js


def test_desktop_inline_poll_checks_failed_before_stale_reply() -> None:
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    probe_start = app_js.index("async function pollInlineMessageChatTurn")
    probe_end = app_js.index("function openInlineMessageProbeChat", probe_start)
    probe_body = app_js[probe_start:probe_end]
    failed_index = probe_body.index('status === "failed"')
    completed_index = probe_body.index('status === "completed"')
    reply_index = probe_body.index(".reply")
    assert failed_index < completed_index
    assert failed_index < reply_index

    # sendChat now streams over SSE instead of polling turn status, so the
    # failed-before-completed ordering no longer applies there. It must still
    # surface a broken stream instead of leaving the thinking bubble forever.
    chat_start = app_js.index("async function sendChat")
    chat_end = app_js.index("async function refreshRecommendations", chat_start)
    chat_body = app_js[chat_start:chat_end]
    assert "streamChatTurn(" in chat_body
    assert "流式连接中断" in chat_body


def test_desktop_auth_probe_times_out_without_assuming_authentication() -> None:
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
    body = _function_body(app_js, "fetchAuthStatus")

    assert "new AbortController()" in body
    assert "controller.abort()" in body
    assert "5000" in body
    assert "authenticated: true" not in body


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
        r"async function hydrateFromBackend\([^)]*\) \{(?P<body>.*?)\n    \}",
        app_js,
        flags=re.S,
    )
    assert hydrate is not None, "desktop hydrateFromBackend not found"
    body = hydrate.group("body")
    assert "const firstRuntimeGeneration = desktopRuntimeGeneration;" in body
    assert "const secondRuntimeGeneration = desktopRuntimeGeneration;" in body
    assert "applyDesktopRuntimeSnapshot(" in body
    assert "secondRuntimeGeneration" in body


def test_desktop_pool_status_labels_pending_signals_as_discovery_context() -> None:
    """Pending runtime signals are discovery context, not unprocessed profile events."""
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
    index_html = Path("src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")

    assert "待处理 ${runtime.pending_signal_events} 条行为信号" not in app_js
    assert "已记下 ${runtime.pending_signal_events} 个新动作" in app_js
    assert "待处理行为信号" not in index_html
    assert "新动作" in index_html


def test_desktop_ignores_extension_transport_wakeup_events() -> None:
    """Wire-only task wakeups must not become raw dashboard activity copy."""
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
    body = _function_body(app_js, "handleRuntimeEvent")

    assert 'const RUNTIME_TRANSPORT_ONLY_EVENTS = new Set(["dy_task_available"])' in app_js
    assert "RUNTIME_TRANSPORT_ONLY_EVENTS.has(event.type)" in body


def test_desktop_replenished_label_describes_progress_not_success_history() -> None:
    """The card can show either net growth or pending material, not success history."""
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
    index_html = Path("src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")

    assert "补货进展" in index_html
    assert "上次成功补货" not in index_html
    assert "另有 ${runtime.pool_pending_count} 条素材" in app_js
    assert "素材已抓到，会按可换库存缺口整理" in app_js


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
    saved_sync_js = Path("src/openbiliclaw/web/desktop/assets/js/saved-sync-core.js").read_text(
        encoding="utf-8"
    )
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
    assert "OpenBiliClawSavedSync.normalizeSavedItem" in normalize_body
    assert "PLATFORM_ALIASES[explicit] || explicit" in saved_sync_js
    assert 'x: "twitter"' in saved_sync_js
    assert 'host === "x.com"' in saved_sync_js

    card_html = re.search(
        r"function recommendationCardHtml\(item, index\) \{(?P<body>.*?)\n    \}",
        app_js,
        flags=re.S,
    )
    assert card_html is not None, "desktop recommendationCardHtml not found"
    assert "recommendationMediaHtml(item, index)" in card_html.group("body")

    media_html = re.search(
        r"function recommendationMediaHtml\(item, index = 0\) \{(?P<body>.*?)\n    \}",
        app_js,
        flags=re.S,
    )
    assert media_html is not None, "desktop recommendationMediaHtml not found"
    assert "cover-text" in media_html.group("body")
    assert "coverImg(item, { eager })" in media_html.group("body")

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


def test_desktop_positive_feedback_keeps_recommendation_card_visible() -> None:
    """Desktop feedback mutates one card and defers durable writes for undo."""
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    decision = re.search(
        r"function shouldRemoveRecommendationAfterFeedback\(feedbackType\) \{(?P<body>.*?)\n    \}",
        app_js,
        flags=re.S,
    )
    assert decision is not None, "desktop feedback removal decision helper not found"
    assert 'return normalized === "dislike" || normalized === "dismiss";' in decision.group("body")

    start = app_js.index("function stageRecommendationFeedback(item, card, feedbackType)")
    end = app_js.index("\n    function finishRecommendationFeedback", start)
    body = app_js[start:end]
    assert "pendingActions.schedule(key" in body
    assert "undo.dataset.feedbackUndo = key;" in body
    assert "item.feedback_type = feedbackType;" in body
    assert "renderAll()" not in body
    assert "removeRecommendationCard" not in body
    assert 'committed: "已记录喜欢，推荐会继续保留在当前列表。"' in body
    assert "function feedbackActionKey(item)" in app_js
    assert "`recommendation:${platform}:${contentId}`" in app_js
    assert (
        'window.addEventListener("pagehide", () => { void pendingActions.flushAll(); });' in app_js
    )


def test_desktop_recommendation_hydration_filters_only_negative_feedback() -> None:
    """Hydration must not hide liked recommendations returned by another client."""
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    feedbacked = re.search(
        r"function isFeedbackedRecommendation\(item\) \{(?P<body>.*?)\n    \}",
        app_js,
        flags=re.S,
    )
    assert feedbacked is not None, "desktop feedback filter not found"
    body = feedbacked.group("body")
    assert "shouldRemoveRecommendationAfterFeedback(feedback)" in body
    assert (
        "return shouldRemoveRecommendationAfterFeedback(feedback) || "
        '(poolStatus === "feedbacked" && !feedback);'
    ) in body
    assert 'return Boolean(feedback) || poolStatus === "feedbacked";' not in body


def test_desktop_pool_update_does_not_replace_recommendation_list() -> None:
    """refresh.pool_updated is a pool-status signal, not a list refresh.

    The desktop web must not hydrate (which replaces ``state.videos``) when the
    runtime emits ``refresh.pool_updated`` / ``recommendation.reshuffled``,
    otherwise locally appended ("加载更多") cards get wiped out by the latest
    top window from ``/api/recommendations``. This mirrors the recommend.js +
    popup.js behaviour (fix 79042ce). ``config_reloaded`` still hydrates after
    the accepted config-apply terminal state; ``init_completed`` hydrates only after
    ``refreshInitStatus`` observes the initialized transition, avoiding duplicate
    fetches/toasts. Pool/header counts keep updating via the unconditional
    ``applyRuntimeStatus`` call.
    """
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    handle_runtime = _function_body(app_js, "handleRuntimeEvent")
    apply_status = _function_body(app_js, "applyConfigApplyStatus")
    safe_hydration = _function_body(app_js, "scheduleSettingsHydrationIfSafe")

    # Runtime events no longer hydrate directly: an accepted config terminal
    # state goes through the draft/editor guard first. Pool and reshuffle events
    # never enter that config-apply path, so they cannot replace the list.
    assert "scheduleBackendHydration();" not in handle_runtime
    assert 'event.type === "config_reloaded" && !configApplyEventAccepted' in handle_runtime
    assert "scheduleSettingsHydrationIfSafe();" in apply_status
    assert "scheduleBackendHydration();" in safe_hydration
    assert "refresh.pool_updated" not in apply_status
    assert "recommendation.reshuffled" not in apply_status


def test_desktop_failed_recommendation_read_schedules_empty_only_recovery() -> None:
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert "readRecommendationSnapshot" in app_js
    assert "scheduleDesktopRecommendationRecovery" in app_js
    assert "if (state.videos.length > 0)" in app_js
    assert 'desktopRecommendationLoadState = "failed"' in app_js
    assert 'desktopRecommendationLoadState = "empty-success"' in app_js


def test_desktop_runtime_failure_recovers_independently() -> None:
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert "scheduleDesktopRuntimeRecovery" in app_js
    assert "[1000, 2000, 4000, 8000]" in app_js
    assert 'desktopRuntimeLoadState = "failed"' in app_js
    assert "let desktopRuntimeGeneration = 0;" in app_js
    assert "if (requestGeneration !== desktopRuntimeGeneration) return;" in app_js


def test_desktop_runtime_failure_survives_full_render_and_is_keyboard_retryable() -> None:
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    render_pool = re.search(
        r"function renderPoolStatus\(.*?\) \{(?P<body>.*?)\n    \}",
        app_js,
        flags=re.S,
    )
    assert render_pool is not None
    assert "renderDesktopRuntimeFailure();" in render_pool.group("body")
    assert "poolAvailable.onkeydown" in app_js
    assert 'event.key === "Enter" || event.key === " "' in app_js


def test_desktop_healthy_stream_reconnect_does_not_rebuild_cards() -> None:
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert "if (recommendationRestarted) renderVideos();" in app_js
    assert "if (runtimeRestarted) renderDesktopRuntimeFailure();" in app_js


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
    assert 'projectStats: "/project-stats"' in app_js
    assert "requestJson(ENDPOINTS.projectStats" in app_js
    assert "api.github.com" not in app_js
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


def test_desktop_reshuffle_always_excludes_current_cards_without_bulk_dismiss() -> None:
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
    index_html = Path("src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    start = app_js.index("async function reshuffle()")
    end = app_js.index("\n    async function appendMore()", start)
    body = app_js[start:end]

    assert "visibleForExclusion" in body
    assert "excluded_bvids" in body
    assert "state.dismissOnReshuffle" not in app_js
    assert "dismissToggle" not in app_js
    assert "dismissVisibleRecommendationsBeforeReshuffle" not in app_js
    assert "renderReshuffleToggle" not in app_js
    assert "换一批时忽略当前" not in index_html
    assert 'id="reshuffleBtn"' in index_html
    assert 'aria-label="换一批"' in index_html


def test_desktop_platform_availability_endpoint_and_snapshot_state() -> None:
    """平台库存有独立只读接口；读取失败必须保留上一次成功的 snapshot。"""
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert 'platformAvailability: "/recommendations/platform-availability"' in app_js
    # 首次成功读取前是「未知」而不是 0。
    assert "platformAvailability: null," in app_js

    body = _function_body(app_js, "refreshPlatformAvailability")
    assert "ENDPOINTS.platformAvailability" in body
    assert "state.platformAvailability = snapshot;" in body
    # 失败分支不许触碰 snapshot —— 「失败即全零」是明确禁止的。
    failure_branch = body[body.index("} catch") :]
    assert "state.platformAvailability" not in failure_branch
    # 库存更新只重绘 Tab / 空态 / 自动续页 gate，不重建已 append 的推荐卡片。
    assert "renderFilters();" in body
    assert "maybeAutoLoadAfterPoolRefill();" in body
    assert "hydrateFromBackend" not in body
    assert "normalizeRecommendationList" not in body

    # 去抖 + 单飞（合并 pending 调用）复用既有 debounceAsync。
    assert "const schedulePlatformAvailabilityRefresh = debounceAsync(" in app_js
    assert (
        'if (event.type === "refresh.pool_updated" || event.type === "pool_status") '
        "schedulePlatformAvailabilityRefresh();"
    ) in app_js

    # 未成功读取过时是 null（未知），不是 0。
    count_fn = _function_body(app_js, "platformAvailableCount")
    assert "if (!snapshot) return null;" in count_fn


def test_desktop_platform_tabs_union_enabled_inventory_and_loaded() -> None:
    """Tab = 已启用配置 ∪ 库存>0 平台 ∪ 本会话已加载卡片，顺序稳定。"""
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    body = _function_body(app_js, "buildFilters")
    assert "configuredSourceFilterLabels()" in body
    assert "availablePlatformSlugs()" in body
    assert "state.videos" in body
    # 已知平台沿用 sourceFilterDefinitions 顺序，未知值按稳定字典序。
    assert "sourceFilterOrder.filter" in body
    assert 'a.localeCompare(b, "zh-Hans-CN")' in body
    assert 'return ["全部", ...sources, ...otherSources];' in body

    available = _function_body(app_js, "availablePlatformSlugs")
    assert "state.platformAvailability?.by_platform" in available
    assert "> 0" in available

    # 搜索词只过滤已显示的卡片，不影响 Tab 集合 / 库存数字 / 后端平台参数。
    assert "state.query" not in body
    assert "state.query" not in _function_body(app_js, "platformAvailableCount")
    assert "state.query" not in _function_body(app_js, "platformSlugForFilterLabel")

    # 当前 Tab 因配置热更新 / 库存变化消失时回退到「全部」。
    render_filters = _function_body(app_js, "renderFilters")
    assert 'if (!filters.includes(state.filter)) state.filter = "全部";' in render_filters


def test_desktop_platform_chips_render_counts_and_accessible_selected_state() -> None:
    """chip 显示紧凑库存计数，选中态不能只靠颜色表达。"""
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
    app_css = Path("src/openbiliclaw/web/desktop/assets/css/app.css").read_text(encoding="utf-8")
    index_html = Path("src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")

    body = _function_body(app_js, "renderFilters")
    assert 'btn.setAttribute("role", "tab");' in body
    assert 'btn.setAttribute("aria-selected", selected ? "true" : "false");' in body
    assert "chip-count" in body
    assert "platformAvailableCount(" in body
    # accessible name 必须带完整平台名 + 库存数。
    assert 'btn.setAttribute("aria-label"' in body
    assert "PLATFORM_COUNT_UNKNOWN_TEXT" in body
    assert "PLATFORM_COUNT_UNKNOWN_LABEL" in body
    # 切 Tab 只改视图，不发推荐请求。
    assert "ENDPOINTS.reshuffle" not in body
    assert "ENDPOINTS.append" not in body
    # 整排 chip 每次渲染都被替换，焦点必须还回去，否则方向键导航一点就断。
    assert "row.contains(document.activeElement)" in body
    assert "restored?.focus({ preventScroll: true });" in body
    assert "btn.tabIndex = selected ? 0 : -1;" in body

    switch = _function_body(app_js, "setActiveFilter")
    assert "state.filter = name;" in switch
    assert "renderAll();" in switch
    assert "requestJson" not in switch

    assert '<div class="filter-row" id="filterRow" role="tablist"' in index_html

    count_rule = re.search(r"\.filter-row \.chip \.chip-count \{[^}]*\}", app_css)
    assert count_rule is not None, "chip count style not found"
    assert "font-variant-numeric: tabular-nums" in count_rule.group(0)
    assert "min-width:" in count_rule.group(0)
    # hover 不许引发布局跳动；focus 必须可见；选中态有非颜色线索。
    assert ".filter-row .chip:hover { transform: none;" in app_css
    assert ".filter-row .chip:focus-visible { outline: none; box-shadow: var(--focus-ring); }" in (
        app_css
    )
    selected_rule = re.search(r'\.filter-row \.chip\[aria-selected="true"\] \{[^}]*\}', app_css)
    assert selected_rule is not None, "chip selected style not found"
    assert "font-weight:" in selected_rule.group(0)
