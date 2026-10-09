"""Static regressions for desktop Linux.do source settings and cards."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_desktop_web_round_trips_linuxdo_source_settings() -> None:
    html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    for element_id in (
        "linuxdoEnabled",
        "linuxdoModeSearch",
        "linuxdoModeHot",
        "linuxdoModeFeed",
        "linuxdoModeCreator",
        "linuxdoModeRelated",
        "linuxdoDailySearchBudget",
        "linuxdoDailyHotBudget",
        "linuxdoDailyFeedBudget",
        "linuxdoDailyCreatorBudget",
        "linuxdoDailyRelatedBudget",
        "linuxdoRequestInterval",
        "linuxdoMinInterval",
        "linuxdoBootstrapLimit",
        "shareLinuxdo",
    ):
        assert f'id="{element_id}"' in html
        assert f'"{element_id}"' in js

    assert (
        "setCheckedValues(LINUXDO_SOURCE_MODE_FIELDS, config.sources?.linuxdo?.source_modes)" in js
    )
    assert 'source_modes: collectCheckedValues(LINUXDO_SOURCE_MODE_FIELDS, ["search"])' in js
    assert 'daily_search_budget: getIntInput("linuxdoDailySearchBudget", 0)' in js
    assert 'bootstrap_limit: getIntInput("linuxdoBootstrapLimit", 300)' in js
    assert 'linuxdo: getIntInput("shareLinuxdo", 1)' in js
    assert 'if (shares.linuxdo !== undefined) setInput("shareLinuxdo", shares.linuxdo)' in js


def test_desktop_linuxdo_uses_shared_status_contract_without_cookie_input() -> None:
    html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
    shared = (ROOT / "src/openbiliclaw/web/shared/source-status.js").read_text(encoding="utf-8")

    assert 'data-source-status="linuxdo"' in html
    assert 'data-source-credential="linuxdo"' in html
    assert 'data-source-logo="linuxdo"' in html
    assert "公开发现无需登录" in html
    assert "浏览器插件后，可增强收藏、点赞和阅读记录" in html
    assert 'id="linuxdoCookie"' not in html
    assert "SOURCE_STATUS_KEYS = SourceStatus.SOURCE_KEYS" in js
    assert 'linuxdo: "Linux.do"' in shared
    assert '"linuxdo"' in shared


def test_desktop_source_cards_are_siblings_not_nested() -> None:
    """Keep the Linux.do/V2EX cards from becoming a malformed nested DOM."""
    html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")

    linuxdo_attr = html.index('data-source-status="linuxdo"')
    linuxdo_start = html.rfind("<article", 0, linuxdo_attr)
    v2ex_start = html.index('data-source-status="v2ex"')
    # Later source cards may follow V2EX; keep this assertion scoped to the
    # adjacent pair whose accidental nesting originally caused the bug.
    source_list_end = html.index("</article>", v2ex_start) + len("</article>")
    source_list = html[linuxdo_start:source_list_end]

    assert source_list.count('<article class="source-status-row source-card"') == 2
    assert source_list.count('data-source-status="linuxdo"') == 1
    assert source_list.count('data-source-status="v2ex"') == 1
    assert source_list.index('data-source-status="linuxdo"') < source_list.index(
        'data-source-status="v2ex"'
    )
    assert source_list.index("</article>") < source_list.index(
        '<article class="source-status-row source-card" data-source-status="v2ex"'
    )
    assert 'id="shareLinuxdo"' in source_list
    assert 'id="shareV2EX"' in source_list


def test_desktop_linuxdo_recommendations_have_label_text_card_and_canonical_url() -> None:
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert '{ key: "linuxdo", label: "Linux.do" }' in js
    assert 'linuxdo: "Linux.do"' in js
    assert 'urlHostMatches(url, ["linux.do"])' in js
    assert "https://linux.do/t/${encodeURIComponent(topicId)}" in js
    assert '"post"' in js


def test_linuxdo_saved_sync_uses_the_public_platform_label() -> None:
    saved = (ROOT / "src/openbiliclaw/web/js/views/saved.js").read_text(encoding="utf-8")

    assert 'linuxdo: "Linux.do"' in saved
