"""Static regressions for desktop Linux.do source settings and cards."""

import re
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
    """Keep the Linux.do/V2EX/Web cards from becoming a malformed nested DOM."""
    html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")

    starts = [
        match.start()
        for match in re.finditer('<article class="source-status-row source-card"', html)
    ]
    for previous, current in zip(starts, starts[1:], strict=False):
        assert html.index("</article>", previous) < current

    previous_end = -1
    for source, share_input in (
        ("linuxdo", "shareLinuxdo"),
        ("v2ex", "shareV2EX"),
        ("web", "shareWeb"),
    ):
        start = html.index(
            f'<article class="source-status-row source-card" data-source-status="{source}"'
        )
        end = html.index("</article>", start) + len("</article>")
        card = html[start:end]
        # Main may insert other platform cards between these sources. Assert
        # structure and each source's own input without freezing the roster.
        assert start > previous_end
        assert card.count("<article") == 1
        assert card.count(f'data-source-status="{source}"') == 1
        assert f'id="{share_input}"' in card
        previous_end = end


def test_desktop_linuxdo_recommendations_have_label_text_card_and_canonical_url() -> None:
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert '{ key: "linuxdo", label: "Linux.do" }' in js
    assert 'linuxdo: "Linux.do"' in js
    assert 'urlHostMatches(url, ["linux.do"])' in js
    assert "https://linux.do/t/${encodeURIComponent(topicId)}" in js
    assert '"post"' in js


def test_desktop_web_recipe_manager_hides_legacy_browser_extract_rows() -> None:
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert '["feed", "sitemap", "listing"].includes' in js
    assert "item?.config?.plan_version" in js


def test_linuxdo_saved_sync_uses_the_public_platform_label() -> None:
    saved = (ROOT / "src/openbiliclaw/web/js/views/saved.js").read_text(encoding="utf-8")

    assert 'linuxdo: "Linux.do"' in saved
