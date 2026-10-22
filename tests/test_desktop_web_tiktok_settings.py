"""Static regressions for the desktop TikTok source settings card."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_desktop_web_tiktok_card_dom_and_wiring() -> None:
    html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    # Card sits between YouTube and X with its own status/credential rows (the
    # generic 测试连接 dispatch keys off data-source-status).
    assert html.index('data-source-status="youtube"') < html.index('data-source-status="tiktok"')
    assert html.index('data-source-status="tiktok"') < html.index('data-source-status="twitter"')
    assert 'data-source-credential="tiktok"' in html
    assert 'id="sourceCardBody-tiktok"' in html

    for element_id in (
        "tiktokEnabled",
        "tiktokCookie",
        "tiktokCookieEnv",
        "tiktokMode",
        "tiktokRegion",
        "tiktokTzName",
        "tiktokTags",
        "tiktokCreators",
        "tiktokDailyFeedBudget",
        "tiktokDailySearchBudget",
        "tiktokDailyTagBudget",
        "tiktokDailyUserBudget",
        "tiktokRequestInterval",
        "tiktokMinInterval",
        "shareTiktok",
    ):
        assert f'id="{element_id}"' in html, f"{element_id} missing from index.html"
        assert f'"{element_id}"' in js, f"{element_id} not wired in app.js"


def test_desktop_web_tiktok_lookup_tables_cover_cards() -> None:
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert 'tiktok: "tiktokEnabled"' in js  # SOURCE_ENABLE_SELECT_IDS
    assert 'tiktok: "shareTiktok"' in js  # SOURCE_SHARE_INPUT_IDS
    assert 'tiktok: "TikTok"' in js  # SOURCE_CARD_LABELS
    # Auto-injected 发布日期偏好 section + round-trip via the generic slug path.
    assert '"tiktok", "twitter"' in js  # DESKTOP_SOURCE_DATE_SLUGS entry


def test_desktop_web_tiktok_config_round_trip_fields() -> None:
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    # populateForm fills from the config snapshot.
    assert (
        'setSelect("tiktokEnabled", config.sources?.tiktok?.enabled === true ? "on" : "off")' in js
    )
    assert 'setCookieOverrideInput("tiktokCookie", config.sources?.tiktok?.cookie, " TikTok")' in js
    assert 'setSelect("tiktokMode", config.sources?.tiktok?.mode || "auto")' in js
    assert 'setInput("tiktokRegion", config.sources?.tiktok?.region)' in js
    assert 'setInput("tiktokTzName", config.sources?.tiktok?.tz_name)' in js
    assert 'setInput("tiktokDailyFeedBudget", config.sources?.tiktok?.daily_feed_budget)' in js

    # buildConfigUpdate submits the full block; empty-field fallbacks mirror
    # the backend dataclass defaults (feed/search 3, tag/user 0).
    assert 'mode: getInput("tiktokMode") || "auto"' in js
    assert 'daily_feed_budget: getIntInput("tiktokDailyFeedBudget", 3)' in js
    assert 'daily_search_budget: getIntInput("tiktokDailySearchBudget", 3)' in js
    assert 'daily_tag_budget: getIntInput("tiktokDailyTagBudget", 0)' in js
    assert 'daily_user_budget: getIntInput("tiktokDailyUserBudget", 0)' in js
    assert 'request_interval_seconds: getIntInput("tiktokRequestInterval", 2)' in js
    assert 'min_interval_minutes: getIntInput("tiktokMinInterval", 3)' in js
    assert '...sourceDateFieldsForUpdate("tiktok")' in js


def test_desktop_web_save_never_drops_tiktok_pool_share() -> None:
    """Regression: pool_source_shares is rebuilt key-by-key on save, so any
    source missing from that literal silently loses its user-configured
    share. tiktok must appear in the save payload, the load回填, and the
    share-suggestion enabled_sources map."""
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert 'tiktok: getIntInput("shareTiktok", 1)' in js
    assert 'setInput("shareTiktok", scheduler.pool_source_shares?.tiktok)' in js
    assert 'tiktok: $("#tiktokEnabled").value === "on"' in js
    assert 'if (shares.tiktok !== undefined) setInput("shareTiktok", shares.tiktok);' in js


def test_desktop_web_tiktok_filter_tab_and_badge_color() -> None:
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
    css = (ROOT / "src/openbiliclaw/web/css/app.css").read_text(encoding="utf-8")

    # Static filter tab (no dynamic-fallback gap when enabled but empty).
    assert '{ key: "tiktok", label: "TikTok" }' in js
    assert '.card-source[data-source="tiktok"]' in css
