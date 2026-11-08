import re
from pathlib import Path

APP_JS = Path("src/openbiliclaw/web/desktop/assets/js/app.js")
APP_CSS = Path("src/openbiliclaw/web/desktop/assets/css/app.css")
INDEX_HTML = Path("src/openbiliclaw/web/desktop/index.html")
SAVED_SYNC_CORE = Path("src/openbiliclaw/web/desktop/assets/js/saved-sync-core.js")


def test_main_recommendation_card_cover_is_a_real_link_when_url_exists() -> None:
    app_js = APP_JS.read_text(encoding="utf-8")

    assert '<a class="cover${recommendationCoverClass(item)}"' in app_js
    assert 'href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer"' in app_js
    assert '<button class="cover${recommendationCoverClass(item)}"' in app_js
    assert "后端没有返回可打开链接；点击信号会在后台记录。" in app_js


def test_public_web_cards_use_direct_urls_and_truthful_metadata() -> None:
    app_js = APP_JS.read_text(encoding="utf-8")

    assert '{ key: "web", label: "Web" }' in app_js
    assert 'web: "Web"' in app_js
    assert 'web: "web"' in app_js
    assert "if (item.content_url) return item.content_url;" in app_js
    assert 'if (platform === "web") return "";' in app_js
    assert 'if (normalizeSourcePlatform(item) === "web") return "";' in app_js
    assert '"web-feed": "网页订阅（RSS/Atom/JSON Feed）"' in app_js
    assert '"web-sitemap": "网页站点地图（Sitemap）"' in app_js
    assert '"web-listing": "网页列表页"' in app_js
    assert "topic: formatSourceStrategyLabel(" in app_js


def test_desktop_settings_can_manage_bounded_public_web_recipes() -> None:
    app_js = APP_JS.read_text(encoding="utf-8")
    app_css = APP_CSS.read_text(encoding="utf-8")
    index_html = INDEX_HTML.read_text(encoding="utf-8")

    assert 'data-source-status="web"' in index_html
    assert '<div id="webRecipeForm"' in index_html
    assert '<form id="webRecipeStandaloneForm" hidden></form>' in index_html
    assert '<form id="webRecipeForm"' not in index_html
    assert 'id="webRecipeStrategy"' in index_html
    assert 'id="webRecipeItemSelector"' in index_html
    assert "仅访问你批准的公开 origin，遵守 robots.txt" in index_html
    assert "不带 Cookie、不登录，也不执行网页脚本" in index_html
    assert 'sources: "/sources"' in app_js
    assert 'source_type: "web"' in app_js
    assert 'control.setAttribute("form", form.id);' in app_js
    assert 'el.closest("#webRecipeForm")' in app_js
    assert 'method: "POST"' in app_js
    assert 'method: "PUT"' in app_js
    assert 'method: "DELETE"' in app_js
    assert 'if (typeof details.detail === "string") return details.detail;' in app_js
    assert 'web: "webEnabled"' in app_js
    assert 'web: "shareWeb"' in app_js
    assert 'request_timeout_seconds: getIntInput("webRequestTimeout", 15)' in app_js
    assert 'max_response_bytes: getIntInput("webMaxResponseBytes", 2097152)' in app_js
    assert 'web: getIntInput("shareWeb", 1)' in app_js
    assert 'web: $("#webEnabled").value === "on"' in app_js
    assert 'if (shares.web !== undefined) setInput("shareWeb", shares.web);' in app_js
    assert ".web-recipe-item" in app_css
    assert ".web-recipe-listing-fields[hidden]" in app_css


def test_recommendation_click_tracking_uses_click_and_auxclick_without_window_open() -> None:
    app_js = APP_JS.read_text(encoding="utf-8")

    open_match = re.search(
        r"function openRecommendation\(item, card\) \{(?P<body>.*?)\n    \}",
        app_js,
        flags=re.S,
    )
    assert open_match is not None, "openRecommendation function not found"
    open_body = open_match.group("body")

    # Recommendation card opening is anchor-native: openRecommendation only
    # tracks, never window.open()s. (The delight 去看看 button IS a plain button
    # and legitimately window.open()s — see the delight test below — so this
    # guard is scoped to openRecommendation, not the whole file.)
    assert "window.open" not in open_body
    assert "preventDefault" not in open_body
    assert "trackRecommendationClick(item);" in open_body
    assert 'cover.addEventListener("click", () => openRecommendation(item, card));' in app_js
    assert re.search(
        r'cover\.addEventListener\("auxclick", \(event\) => \{\s*'
        r"if \(event\.button === 1\) openRecommendation\(item, card\);",
        app_js,
    )


def test_saved_message_and_delight_content_opens_use_anchor_semantics() -> None:
    app_js = APP_JS.read_text(encoding="utf-8")

    assert '<a class="cover${savedCoverClass}"' in app_js
    saved_anchor = (
        '<a class="cover${savedCoverClass}" data-platform='
        '"${escapeHtml(item.source_platform || item.platform || "bilibili")}"'
    )
    assert saved_anchor in app_js
    assert 'data-notification-msg="view" href="${escapeHtml(msg.content_url)}"' in app_js
    assert "function ensureDelightThumbAnchor()" in app_js
    assert 'document.createElement("a")' in app_js
    assert "function delightContentUrl(delight)" in app_js
    assert 'window.open(msg.content_url, "_blank", "noopener,noreferrer")' not in app_js


def test_delight_view_button_actually_opens_the_content() -> None:
    """The 去看看 button on the delight card is a plain <button> (only the cover
    thumbnail is an <a>), so its click must open the URL from JS. It previously
    only tracked + toasted 「已打开」 without opening anything (field report
    2026-07-07). The cover anchor keeps opening natively, so respondDelight
    only window.open()s when the caller asks (openUrl), avoiding a double-open.
    """
    app_js = APP_JS.read_text(encoding="utf-8")

    # respondDelight takes an openUrl flag and opens in the view branch.
    assert "async function respondDelight(delight, response, el = null, openUrl = false)" in app_js
    view_match = re.search(
        r'if \(response === "view"\) \{(?P<body>.*?)\n        return;\n      \}',
        app_js,
        flags=re.S,
    )
    assert view_match is not None, "delight view branch not found"
    view_body = view_match.group("body")
    assert 'if (openUrl && url) window.open(url, "_blank", "noopener,noreferrer");' in view_body

    # The 去看看 button ([data-delight] handler) passes openUrl=true for view.
    assert 'await respondDelight(state.delight, response, null, response === "view");' in app_js

    # The cover thumbnail triggers must NOT pass openUrl (they navigate via the
    # native <a href>), so there's no double-open.
    assert 'respondDelight(state.delight, "view"));' in app_js
    assert 'if (event.button === 1) respondDelight(state.delight, "view");' in app_js


def test_delight_close_button_is_labeled_as_permanent_seen_action() -> None:
    app_js = APP_JS.read_text(encoding="utf-8")
    app_css = APP_CSS.read_text(encoding="utf-8")
    index_html = INDEX_HTML.read_text(encoding="utf-8")

    assert (
        'class="delight-dismiss-btn" data-delight="dismiss" type="button" '
        'aria-label="看过了，不再推荐" title="看过了，不再推荐"'
    ) in index_html
    delight_meta = index_html.index('<div class="delight-meta">')
    delight_copy = index_html.index('<div class="delight-copy">', delight_meta)
    delight_dismiss = index_html.index('class="delight-dismiss-btn"', delight_meta)
    feedback_actions = index_html.index('<div class="delight-feedback-actions card-feedback-icons"')
    status_line = index_html.index('<p class="status-line"', feedback_actions)
    assert delight_meta < delight_dismiss < delight_copy
    assert 'data-delight="dismiss"' not in index_html[feedback_actions:status_line]
    assert ".delight-dismiss-btn {" in app_css
    assert ".delight-dismiss-btn:focus-visible" in app_css
    assert ".delight-dismiss-btn:disabled" in app_css
    assert "flex-basis: 44px; width: 44px; height: 44px; min-height: 44px;" in app_css
    assert "已标为看过，不再推荐" in app_js
    assert 'if (response === "dismiss" && feedbackResult == null)' in app_js
    assert "这次还没记上，请再试一次" in app_js


def test_cover_css_resets_anchor_defaults() -> None:
    app_css = APP_CSS.read_text(encoding="utf-8")

    cover_match = re.search(r"\.cover \{(?P<body>.*?)\}", app_css, flags=re.S)
    assert cover_match is not None, ".cover rule not found"
    cover_body = cover_match.group("body")

    assert "display: block;" in cover_body
    assert "text-decoration: none;" in cover_body
    assert "color: inherit;" in cover_body


def test_saved_pages_render_manual_sync_without_platform_routing() -> None:
    app_js = APP_JS.read_text(encoding="utf-8")
    app_css = APP_CSS.read_text(encoding="utf-8")
    core = SAVED_SYNC_CORE.read_text(encoding="utf-8")

    assert "function runDesktopSavedSync" in app_js
    assert "function summarizeDesktopSavedTask" in app_js
    assert "请连接已安装 OpenBiliClaw 插件的登录态浏览器后重试。" in core
    assert "removeDesktopSavedItem(listKind, item.item_key)" in app_js
    assert "switch (item.source_platform" not in app_js
    assert ".saved-sync-chip" in app_css
    assert "min-height: 44px" in app_css


def test_unsupported_saved_items_are_truthful_local_only_and_not_sync_eligible() -> None:
    app_js = APP_JS.read_text(encoding="utf-8")
    core = SAVED_SYNC_CORE.read_text(encoding="utf-8")

    assert 'unsupported: ["仅本地保存", "neutral", false]' in app_js
    assert 'errorCode === "unsupported_content_type"' in core
    assert 'errorCode === "unsupported_adapter_missing"' in core
    assert (
        "window.OpenBiliClawSavedSync.isSavedSyncEligibleStatus(\n        item.sync_status,"
    ) in app_js
