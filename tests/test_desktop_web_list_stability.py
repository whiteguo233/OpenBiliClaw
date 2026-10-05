"""Desktop web 推荐列表稳定性的结构契约。

行为侧的真实浏览器回归在 test_desktop_web_list_stability_e2e.py（integration 标记），
这里守住三条源码级约定，防止在没跑 E2E 的改动里被悄悄改回去。
"""

import re
from pathlib import Path

APP_JS = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")


def _function_body(name: str, *, async_function: bool = False) -> str:
    prefix = "async function" if async_function else "function"
    match = re.search(
        rf"{prefix} {re.escape(name)}\([^)]*\) \{{(?P<body>.*?)\n    \}}",
        APP_JS,
        flags=re.S,
    )
    assert match is not None, f"desktop {name} not found"
    return match.group("body")


def test_recommendation_grid_is_rendered_incrementally() -> None:
    """整表 replaceChildren 会销毁用户正在看的卡片：滚动锚点丢失（列表跳动）、
    首屏之外的懒加载封面回落、展开的理由与收藏态复位。渲染必须按 key 复用节点。"""
    render_videos = _function_body("renderVideos")
    sync = _function_body("syncRecommendationCards")

    assert "grid.replaceChildren(" not in render_videos
    assert "syncRecommendationCards(items);" in render_videos
    # 复用的判据：同一个 recommendation key + markup 未变 + recommendation_id 未变
    # + 节点还挂在网格上。id 必须参与判定：卡片监听器闭包持有建卡时那个 item，
    # 而 /api/feedback 按 recommendation_id 定位，换批换出的新行必须重建卡片。
    assert "recommendationKey(item)" in sync
    assert "cached.html === html" in sync
    assert "cached.id === item.id" in sync
    assert "cached.node.parentNode === grid" in sync
    assert "createRecommendationCard(item, html)" in sync
    # 位置对账用 insertBefore 就地挪，不重建。
    assert "grid.insertBefore(node, target);" in sync
    # 加载中的骨架占位归 appendMore 管，重绘不能顺手抹掉，且必须留在真实卡片后面。
    assert 'grid.querySelectorAll(".video-card.is-skeleton")' in sync
    assert "grid.appendChild(skeleton);" in sync


def test_filter_focus_restoration_does_not_scroll_back_to_tabs() -> None:
    """平台 Tab 留着焦点时，用户仍可能已用滚轮浏览到列表底部。自动续页会重绘
    Tab 库存徽标；恢复键盘焦点不能把视口也带回已经离屏的 Tab。"""
    render_filters = _function_body("renderFilters")

    assert "restored?.focus({ preventScroll: true });" in render_filters
    assert "restored?.focus();" not in render_filters


def test_pool_events_do_not_redraw_the_recommendation_grid() -> None:
    """refresh.pool_updated 每轮补货会打好几次，它只该刷新库存 / 头部 / 侧栏。"""
    handle_runtime = _function_body("handleRuntimeEvent")
    options = _function_body("initStatusRenderOptions")
    refresh_init = _function_body("refreshInitStatus", async_function=True)
    render_all = _function_body("renderAll")

    # 库存事件仍旧只做局部刷新 + 走 init-status 支路（fix 79042ce 的约定不变）：
    # 再水合只挂在 config_reloaded 上，pool_updated 绝不能触发整表替换。
    assert "schedulePlatformAvailabilityRefresh();" in handle_runtime
    assert 'event.type === "config_reloaded" && !configApplyEventAccepted' in handle_runtime
    assert "scheduleBackendHydration();" not in handle_runtime
    apply_status = _function_body("applyConfigApplyStatus")
    assert "if (reachedTerminal)" in apply_status
    assert "scheduleSettingsHydrationIfSafe();" in apply_status
    assert "refreshConfigSnapshotOnly();" in apply_status
    assert 'event.type === "refresh.pool_updated" && Boolean(state.initStatus?.initialized)' in (
        handle_runtime
    )

    # 而 init-status 支路上的重绘一律经过 preserveVideos 判定，不再裸调 renderAll()。
    assert "renderAll();" not in refresh_init
    assert refresh_init.count("renderAll(initStatusRenderOptions())") == 4
    assert "shouldShowInitOnboarding(state.runtimeStatus)" in options
    assert ".init-onboarding" in options
    assert ".empty-state" in options
    assert 'grid.querySelector(".video-card:not(.is-skeleton)")' in options
    assert "return { preserveVideos: true };" in options
    assert "if (preserveVideos && step === renderVideos) continue;" in render_all


def test_background_rehydration_never_replaces_the_loaded_list() -> None:
    """/api/recommendations 只返回最新 top 窗口；后台再水合整表覆盖会把用户滚动
    加载出来的卡片丢掉并按后端最新排序重排。只有明确的用户动作才允许换列表。"""
    assert "async function hydrateFromBackend({ replaceRecommendations = false } = {}) {" in APP_JS
    hydrate = _function_body("hydrateFromBackend", async_function=True)
    assert (
        "applyDesktopRecommendationSnapshot(items, { replace: replaceRecommendations });" in hydrate
    )

    # 允许替换的只有首屏引导和手动刷新这两处。
    assert APP_JS.count("replaceRecommendations: true") == 1
    assert "await hydrateFromBackend({ replaceRecommendations: forceHydrate });" in _function_body(
        "startDesktopBackendSession", async_function=True
    )
    assert "await hydrateFromBackend({ replaceRecommendations: true });" in _function_body(
        "refreshRecommendations", async_function=True
    )
    # 切回标签页 / config_reloaded 走无参默认值（不替换）。保存后的 202 只查询
    # 应用状态，避免在后台热重载尚未完成时重复水合并覆盖下一轮本地编辑。
    assert "await hydrateFromBackend();" in _function_body(
        "runBackendHydration", async_function=True
    )
    assert "settingsDirtyFields.size > 0 || settingsFormHasActiveEditor()" in _function_body(
        "runBackendHydration", async_function=True
    )
    assert "void hydrateFromBackend();" not in APP_JS
    assert "if (queued) void refreshConfigApplyStatus();" in APP_JS


def test_background_rehydration_skips_side_effecting_recommendation_read_when_loaded() -> None:
    """已有卡片时后台恢复只能同步状态，不能用推荐 GET 意外补池并消费库存。"""
    gate = _function_body("shouldHydrateRecommendationList")
    hydrate = _function_body("hydrateFromBackend", async_function=True)

    assert "return replaceRecommendations || state.videos.length === 0;" in gate
    assert (
        "const shouldReadRecommendations = "
        "shouldHydrateRecommendationList({ replaceRecommendations });" in hydrate
    )
    assert "? readRecommendationSnapshot()" in hydrate
    assert ": Promise.resolve(null);" in hydrate
    assert "if (items === null) return;" in hydrate
