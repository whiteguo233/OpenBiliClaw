"""Static contracts for the anonymous + logged-in Weibo frontend surfaces."""

from __future__ import annotations

import json
import re
from pathlib import Path

from openbiliclaw.sources.platforms import CANONICAL_SOURCE_FAMILIES

ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_shared_roster_exposes_weibo_in_guided_init_by_capability() -> None:
    shared = _read("src/openbiliclaw/web/shared/source-status.js")
    desktop = _read("src/openbiliclaw/web/desktop/assets/js/app.js")
    setup = _read("src/openbiliclaw/web/setup/index.html")
    popup_init = _read("extension/popup/popup-init-control.js")

    assert '"douyin", "weibo", "youtube"' in shared
    assert 'weibo: "微博"' in shared
    assert "weibo: Object.freeze({ guidedInit: true })" in shared
    assert "SOURCE_KEYS.filter((key) => SOURCE_CAPABILITIES[key]?.guidedInit === true)" in shared
    assert "_initSourceStatus?.INIT_SOURCE_KEYS" in desktop
    assert "SourceStatus.INIT_SOURCE_KEYS.map" in setup
    assert "_initSourceStatus?.INIT_SOURCE_KEYS" in popup_init


def test_desktop_settings_round_trip_anonymous_weibo_config_and_pool_share() -> None:
    html = _read("src/openbiliclaw/web/desktop/index.html")
    js = _read("src/openbiliclaw/web/desktop/assets/js/app.js")

    assert 'data-source-status="weibo"' in html
    assert 'data-source-credential="weibo"' in html
    for element_id in (
        "shareWeibo",
        "weiboEnabled",
        "weiboModeSearch",
        "weiboModeHot",
        "weiboModeCreator",
        "weiboDailySearchBudget",
        "weiboDailyHotBudget",
        "weiboDailyCreatorBudget",
        "weiboRequestInterval",
        "weiboMinInterval",
    ):
        assert f'id="{element_id}"' in html
        assert element_id in js

    assert "无需用户 Cookie" in html
    assert "微博" in html
    assert "guided init" in html
    assert "并非官方稳定 API" in html
    assert "weiboCookie" not in html
    assert '<option value="off" selected>停用</option>' in html
    assert 'config.sources?.weibo?.enabled === true ? "on" : "off"' in js
    assert "setWeiboSourceModes(config.sources?.weibo?.source_modes)" in js
    assert "source_modes: collectWeiboSourceModes()" in js
    assert "需搜索或热榜种子" in html
    assert 'daily_search_budget: getIntInput("weiboDailySearchBudget", 60)' in js
    assert 'daily_hot_budget: getIntInput("weiboDailyHotBudget", 10)' in js
    assert 'daily_creator_budget: getIntInput("weiboDailyCreatorBudget", 30)' in js
    assert 'request_interval_seconds: getIntInput("weiboRequestInterval", 3)' in js
    assert 'min_interval_minutes: getIntInput("weiboMinInterval", 10)' in js
    assert 'weibo: getIntInput("shareWeibo", 1)' in js
    assert 'if (shares.weibo !== undefined) setInput("shareWeibo", shares.weibo)' in js


def test_weibo_current_docs_store_metadata_and_release_boundary_are_in_sync() -> None:
    storage = _read("docs/modules/storage.md")
    diagram = _read("docs/diagrams/discovery-architecture.html")
    docker = _read("docs/docker-deployment.md")
    listing = _read("docs/chrome-webstore-listing.md")
    changelog = _read("docs/changelog.md")
    amo = json.loads(_read("extension/amo-metadata.json"))

    assert " / ".join(CANONICAL_SOURCE_FAMILIES) + "` 枚举" in storage
    assert f"{len(CANONICAL_SOURCE_FAMILIES)} 个 canonical family" in diagram
    assert "微博" in docker
    assert "微博" in listing
    assert "七平台内容发现 AI Agent" not in listing
    # The newest version section must sit above the v0.3.201 boundary; do not
    # anchor on "## 未发布" so the check survives release commits.
    newest_version_heading = re.search(r"(?m)^## v\d+\.\d+\.\d+", changelog)
    assert newest_version_heading is not None
    assert newest_version_heading.start() < changelog.index("## v0.3.201")
    assert "微博" in changelog.split("## v0.3.201", 1)[0]
    assert "Weibo" in amo["description"]["en-US"]
    assert "微博" in amo["description"]["zh-CN"]


def test_recommendation_and_saved_surfaces_recognize_weibo_identity() -> None:
    desktop_js = _read("src/openbiliclaw/web/desktop/assets/js/app.js")
    desktop_css = _read("src/openbiliclaw/web/desktop/assets/css/app.css")
    saved_core = _read("src/openbiliclaw/web/desktop/assets/js/saved-sync-core.js")
    mobile_models = _read("src/openbiliclaw/web/js/view-models.js")
    mobile_saved = _read("src/openbiliclaw/web/js/views/saved.js")
    mobile_css = _read("src/openbiliclaw/web/css/app.css")

    assert 'wb: "weibo"' in desktop_js
    assert 'weibo: "微博"' in desktop_js
    assert '"post"' in desktop_js
    assert "share_count: Number(item?.share_count ?? 0) || 0" in desktop_js
    assert "🔁 " in desktop_js
    assert "const savedCoverClass = recommendationCoverClass(item)" in desktop_js
    assert "${recommendationMediaHtml(item)}" in desktop_js
    assert '.platform[data-platform="weibo"]' in desktop_css
    assert '.source-card-logo[data-source-logo="weibo"]' in desktop_css
    assert 'wb: "weibo"' in saved_core
    for host in ("weibo.com", "weibo.cn", "sinaimg.cn", "sinaimg.com"):
        assert host in saved_core
        assert host in mobile_models
    assert 'weibo: "微博"' in mobile_models
    assert 'platform === "weibo"' in mobile_models
    assert "share_count: Number(item?.share_count ?? 0)" in mobile_models
    assert 'weibo: "微博"' in mobile_saved
    assert "PLATFORM_NAMES[it.source_platform]" in mobile_saved
    assert '.card-source[data-source="weibo"]' in mobile_css


def test_popup_is_weibo_aware_without_new_platform_permissions() -> None:
    html = _read("extension/popup/popup.html")
    js = _read("extension/popup/popup.js")
    helpers = _read("extension/popup/popup-helpers.js")
    manifest = json.loads(_read("extension/manifest.json"))

    assert 'data-source-card="weibo"' in html
    assert 'data-source-status="weibo"' in html
    assert 'id="cfgWeiboEnabled" type="checkbox"' in html
    assert "无需用户 Cookie" in html
    assert "guided init" in html
    assert 'enabled: checked("cfgWeiboEnabled")' in js
    assert 'weibo: getInt("cfgPoolShareWeibo", 1)' in js
    assert 'wb: "weibo"' in helpers
    assert 'weibo: "微博"' in helpers
    assert 'platform === "weibo"' in helpers
    assert 'wb: "weibo"' in _read("extension/popup/popup-saved-sync.js")
    assert 'weibo: "微博"' in _read("extension/popup/popup-saved-sync.js")
    assert 'className = "saved-card-platform"' in js
    assert "delight.body_text || delight.title" in js
    assert "aspect-ratio: auto" in html

    mobile_recommend = _read("src/openbiliclaw/web/js/views/recommend.js")
    assert "d.body_text || d.title" in mobile_recommend
    assert "delight-thumb is-fallback is-text-card" in mobile_recommend

    manifest_text = json.dumps(manifest, ensure_ascii=False)
    assert "weibo.com" in manifest_text.lower()
    assert "weibo.cn" in manifest_text.lower()
