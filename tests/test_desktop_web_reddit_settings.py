"""Static regressions for desktop Reddit source settings."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_desktop_web_round_trips_reddit_source_settings() -> None:
    html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    for element_id in (
        "redditEnabled",
        "redditBackend",
        "redditModeSearch",
        "redditModeHot",
        "redditModeSubreddit",
        "redditModeRelated",
        "redditDailySearchBudget",
        "redditDailyHotBudget",
        "redditDailySubredditBudget",
        "redditDailyRelatedBudget",
        "redditRequestInterval",
        "redditMinInterval",
        "shareReddit",
    ):
        assert f'id="{element_id}"' in html
        assert f'"{element_id}"' in js

    assert "setRedditSourceModes(config.sources?.reddit?.source_modes)" in js
    assert "source_modes: collectRedditSourceModes()" in js
    assert 'backend: getInput("redditBackend") || "rdt"' in js
    assert 'daily_search_budget: getIntInput("redditDailySearchBudget", 300)' in js
    assert 'reddit: getIntInput("shareReddit", 1)' in js


def test_desktop_reddit_source_status_and_credentials_are_rendered() -> None:
    html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    shared = (ROOT / "src/openbiliclaw/web/shared/source-status.js").read_text(encoding="utf-8")

    assert 'data-source-status="reddit"' in html
    assert 'data-source-credential="reddit"' in html
    assert '"redditEnabled"' in js
    # The roster lives once, in the module all three settings surfaces load;
    # this page had two identical copies of it and the side panel a third.
    assert (
        'const SOURCE_KEYS = Object.freeze([\n    "bilibili", "xiaohongshu", '
        '"douyin", "weibo", "youtube", "twitter", "github", "zhihu", "reddit",\n'
    ) in shared
    # New sources extend the one shared roster; they do not create another
    # platform list in either settings bundle.
    roster = shared.split("]);", 1)[0]
    assert '"bangumi"' in roster
    assert '"linuxdo"' in roster
    # V2EX is in the one roster too; source insertion order is not a feature.
    assert '"v2ex"' in roster
    assert "SOURCE_STATUS_KEYS = SourceStatus.SOURCE_KEYS" in js
    assert 'reddit: $("#redditEnabled").value === "on"' in js
    assert 'if (shares.reddit !== undefined) setInput("shareReddit", shares.reddit)' in js
