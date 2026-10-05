"""Desktop web「加载更多推荐」体验契约（issue #81）。

骨架屏占位、短批次诚实文案、候选池回升自动重试 — 静态契约测试，
与 tests/test_desktop_web_pool_status.py 同风格。
"""

import re
from pathlib import Path

APP_JS = Path("src/openbiliclaw/web/desktop/assets/js/app.js")
INDEX_HTML = Path("src/openbiliclaw/web/desktop/index.html")
APP_CSS = Path("src/openbiliclaw/web/desktop/assets/css/app.css")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function_body(source: str, name: str) -> str:
    match = re.search(
        rf"(?:async )?function {re.escape(name)}\([^)]*\) \{{(?P<body>.*?)\n    \}}",
        source,
        flags=re.S,
    )
    assert match is not None, f"desktop {name} not found"
    return match.group("body")


def test_append_more_shows_and_clears_skeletons() -> None:
    app_js = _read(APP_JS)
    body = _function_body(app_js, "appendMore")
    assert "showAppendSkeletons()" in body
    assert "removeAppendSkeletons()" in body
    # 失败路径清掉骨架后 grid 不能是空白（骨架可能顶掉了 empty-state）。
    assert "if (!grid.childElementCount) renderVideos();" in body


def test_append_more_reports_short_batches_honestly() -> None:
    app_js = _read(APP_JS)
    body = _function_body(app_js, "appendMore")
    assert "freshItems.length < APPEND_BATCH_SIZE" in body
    assert "候选池暂时见底" in body
    assert "候选池暂时没有新内容" in body
    # 自动加载开关决定重试文案，不许对关掉自动加载的用户许诺“会自动加载”。
    assert 'state.autoLoadOnScroll ? "补上后会自动加载" : "稍后可再点一次"' in body


def test_initial_grid_ships_static_skeletons() -> None:
    index_html = _read(INDEX_HTML)
    grid = re.search(
        r'<div class="card-grid" id="videoGrid">(?P<body>.*?)<div id="loadMoreSentinel"',
        index_html,
        flags=re.S,
    )
    assert grid is not None, "videoGrid not found"
    skeletons = re.findall(r'class="video-card is-skeleton"', grid.group("body"))
    assert len(skeletons) >= 4, "initial grid must ship skeleton placeholders"
    assert 'aria-hidden="true"' in grid.group("body")


def test_css_defines_skeleton_shimmer_with_reduced_motion_guard() -> None:
    app_css = _read(APP_CSS)
    assert ".video-card.is-skeleton" in app_css
    assert ".skeleton-shimmer" in app_css
    assert "@keyframes skeleton-sweep" in app_css
    assert "prefers-reduced-motion" in app_css


def test_pool_status_update_rechecks_auto_load_when_parked_at_bottom() -> None:
    app_js = _read(APP_JS)
    body = _function_body(app_js, "maybeAutoLoadAfterPoolRefill")
    # 哨兵可能已经相交但当时被库存 / 渲染 guard 拦住；状态更新后要补一次几何重检。
    assert "scheduleAutoLoadCheck();" in body
    apply_body = _function_body(app_js, "applyRuntimeStatus")
    assert "maybeAutoLoadAfterPoolRefill();" in apply_body


def test_intersect_handler_tracks_sentinel_visibility() -> None:
    app_js = _read(APP_JS)
    body = _function_body(app_js, "handleAutoLoadIntersect")
    assert "sentinelInView = entries.some((entry) => entry.isIntersecting);" in body


def test_should_auto_load_ignores_skeleton_cards() -> None:
    app_js = _read(APP_JS)
    # 判定逻辑集中在 autoLoadBlockReason，shouldAutoLoadMore 只是布尔包装。
    body = _function_body(app_js, "autoLoadBlockReason")
    assert ".video-card:not(.is-skeleton)" in body


def _reshuffle_body(app_js: str) -> str:
    start = app_js.index("async function reshuffle()")
    end = app_js.index("\n    async function appendMore()", start)
    return app_js[start:end]


def test_auto_load_gate_uses_active_platform_inventory() -> None:
    """0 库存平台不能靠全局库存把自动续页放行，否则会在该 Tab 上反复空请求。"""
    app_js = _read(APP_JS)
    body = _function_body(app_js, "autoLoadBlockReason")

    assert "activePlatformAvailableCount()" in body
    assert 'return "pool-empty";' in body
    # 平台库存未知（首次快照未成功 / 旧后端没有该接口）回退到全局库存，保持既有行为。
    assert "state.runtimeStatus?.pool_available_count > 0" in body

    # 手动「加载更多」在库存为 0 时仍然可点：它负责唤醒后端已有的补货链路。
    assert 'safeBind("#loadMoreBtn", "click", appendMore);' in app_js
    append_body = _function_body(app_js, "appendMore")
    assert "autoLoadBlockReason" not in append_body
    assert "pool_available_count" not in append_body
    assert "activePlatformAvailableCount" not in append_body


def test_scoped_reshuffle_and_append_send_canonical_platform() -> None:
    app_js = _read(APP_JS)
    reshuffle = _reshuffle_body(app_js)
    append = _function_body(app_js, "appendMore")

    for body in (reshuffle, append):
        assert "const requestPlatform = activePlatformSlug();" in body
        # 「全部」不带 source_platform：旧后端 / 旧契约的请求形状必须保持不变。
        assert "if (requestPlatform) requestBody.source_platform = requestPlatform;" in body
        assert "excluded_bvids" in body

    slug_fn = _function_body(app_js, "platformSlugForFilterLabel")
    assert 'if (!name || name === "全部") return "";' in slug_fn
    assert "sourceFilterDefinitions.find(" in slug_fn
    assert "canonicalPlatformSlug(name)" in slug_fn

    canonical = _function_body(app_js, "canonicalPlatformSlug")
    assert "platformAliases[raw] || raw" in canonical

    # 换一批 / 加载更多完成后刷新库存 snapshot。
    for body in (reshuffle, append):
        assert "schedulePlatformAvailabilityRefresh();" in body


def test_scoped_reshuffle_replaces_only_that_platform() -> None:
    app_js = _read(APP_JS)
    reshuffle = _reshuffle_body(app_js)

    assert "replacePlatformCards(state.videos, requestPlatform, fresh)" in reshuffle
    # 平台定向换一批的排除集是该平台本会话已加载内容，不是全局可见集合。
    assert "recommendationPlatformSlug(item) === requestPlatform" in reshuffle
    # 空数组保留现有卡片，不制造空屏。
    assert "if (fresh.length) {" in reshuffle

    replace = _function_body(app_js, "replacePlatformCards")
    # 只有该平台的旧卡被换掉；其它平台的卡片原样保留在结果里。
    assert "recommendationPlatformSlug(item) === platform" in replace
    assert "next.push(item);" in replace
    assert "next.push(...fresh);" in replace


def test_scoped_response_handling_uses_request_start_scope() -> None:
    """请求期间切 Tab 不能把响应写进错误批次：响应处理只读请求开始时捕获的平台。"""
    app_js = _read(APP_JS)
    reshuffle = _reshuffle_body(app_js)
    append = _function_body(app_js, "appendMore")

    for body in (reshuffle, append):
        capture = body.index("const requestPlatform = activePlatformSlug();")
        request = body.index("await requestJson")
        assert capture < request
        after_response = body[request:]
        assert "state.filter" not in after_response
        assert "activePlatformSlug()" not in after_response
        assert "filteredVideos()" not in after_response

    # 后端跨平台泄漏是契约破坏：记录 + 如实反馈，绝不静默过滤假装成功。
    leak = _function_body(app_js, "reportPlatformScopeLeak")
    assert "console.error(" in leak
    assert "showToast(" in leak
    assert "recommendationPlatformSlug(item) !== requestPlatform" in leak
    for body in (reshuffle, append):
        assert "reportPlatformScopeLeak(" in body

    # 追加按稳定 recommendation key 去重。
    assert "loadedKeys" in append
    assert "recommendationKey(item)" in append


def test_platform_empty_state_distinguishes_stock_from_starvation() -> None:
    app_js = _read(APP_JS)
    body = _function_body(app_js, "renderVideos")

    assert "activePlatformSlug()" in body
    assert "platformAvailableCount(" in body
    assert "加载更多推荐" in body
    assert "暂时没有新候选" in body
    assert "后台会继续补货" in body


def test_reshuffle_invalidates_inflight_append_responses() -> None:
    """A slow 加载更多 must not append an old batch below a fresh reshuffle.

    Regression: auto-append can be in flight when the user clicks 换一批.
    If the append response lands after the reshuffle has replaced the list,
    the old implementation appended its stale batch to the new list, so the
    page showed 第三批 on top and the old 第二批 again below.
    """
    app_js = _read(APP_JS)
    assert "let recommendationListVersion = 0;" in app_js

    reshuffle = _reshuffle_body(app_js)
    append = _function_body(app_js, "appendMore")
    snapshot = _function_body(app_js, "applyDesktopRecommendationSnapshot")

    assert "recommendationListVersion += 1;" in reshuffle
    assert "const listVersionAtRequest = recommendationListVersion;" in append
    assert "if (listVersionAtRequest !== recommendationListVersion) return;" in append
    assert "recommendationListVersion += 1;" in snapshot
