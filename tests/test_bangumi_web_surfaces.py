"""Static contracts for Bangumi settings and recommendation surfaces."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def shared_source_status_js() -> str:
    """The status-row renderer the three surfaces share.

    Desktop, the extension popup and the setup wizard all load this module, so
    per-source rendering logic lives here rather than being hand-copied into
    each bundle. Tests that assert "the frontend renders field X" have to look
    here too, or they pin the behaviour to whichever copy existed first.
    """
    return (ROOT / "src/openbiliclaw/web/shared/source-status.js").read_text(encoding="utf-8")


def test_desktop_round_trips_bangumi_settings() -> None:
    html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    for element_id in (
        "bangumiEnabled",
        "bangumiUsername",
        "bangumiModeSearch",
        "bangumiModeRanked",
        "bangumiModeLatest",
        "bangumiTypeAnime",
        "bangumiTypeBook",
        "bangumiTypeGame",
        "bangumiTypeMusic",
        "bangumiTypeReal",
        "bangumiDailySearchBudget",
        "bangumiDailyRankedBudget",
        "bangumiDailyLatestBudget",
        "bangumiRequestInterval",
        "bangumiMinInterval",
        "bangumiBootstrapLimit",
        "shareBangumi",
    ):
        assert f'id="{element_id}"' in html
        assert f'"{element_id}"' in js

    assert 'data-source-status="bangumi"' in html
    assert 'data-source-credential="bangumi"' in html
    assert "config.sources?.bangumi?.source_modes" in js
    assert "config.sources?.bangumi?.subject_types" in js
    assert 'bangumi: getIntInput("shareBangumi", 1)' in js
    assert 'if (shares.bangumi !== undefined) setInput("shareBangumi", shares.bangumi)' in js
    assert "initBangumiUsernameTouched" in js
    assert "formatCountCn(item.source_rank)" not in js
    assert "segments.push(`排名 #${sourceRank}`)" in js
    for field in ("rating_score", "rating_count", "source_rank"):
        assert js.count(f"{field}: Number(item?.{field}") >= 2


def test_mobile_recognizes_bangumi_identity_and_catalog_metrics() -> None:
    js = (ROOT / "src/openbiliclaw/web/js/view-models.js").read_text(encoding="utf-8")
    css = (ROOT / "src/openbiliclaw/web/css/app.css").read_text(encoding="utf-8")
    saved = (ROOT / "src/openbiliclaw/web/js/views/saved.js").read_text(encoding="utf-8")

    assert 'bangumi: "Bangumi"' in js
    assert 'bgm: "bangumi"' in js
    assert '["bgm.tv", "bangumi.tv"]' in js
    assert "https://bgm.tv/subject/" in js
    assert "formatCountCn(item.source_rank)" not in js
    assert "segments.push(`排名 #${sourceRank}`)" in js
    for field in ("rating_score", "rating_count", "source_rank"):
        assert js.count(f"{field}: Number(item?.{field}") >= 2
    assert '.card-source[data-source="bangumi"]' in css
    assert 'bangumi: "Bangumi"' in saved


def test_desktop_guided_init_username_omit_and_warnings() -> None:
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    # F3: only send an explicit username when deliberately edited or explicitly
    # cleared after a successful prefill — never erase a configured value with an
    # empty, never-prefilled field.
    assert "initBangumiUsernamePrefilled" in js
    assert '(bangumiUsername !== "" || state.initBangumiUsernamePrefilled)' in js
    assert 'selected.includes("bangumi") && (sendBangumiUsername || bangumiToken)' in js
    # F4: consume and surface the 202 warnings instead of a bare "已开始".
    assert "started?.warnings" in js
    assert 'showToast(startWarnings.length ? startWarnings.join(" ")' in js


def test_desktop_exposes_bangumi_access_token_input() -> None:
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    # Optional personal access token input (password type), toggled with the
    # bangumi checkbox and sent only when the user typed one.
    assert 'id="initBangumiToken"' in js
    assert 'type="password"' in js
    assert "https://next.bgm.tv/demo/access-token" in js
    assert "if (bangumiToken) bangumi.access_token = bangumiToken;" in js
    assert "if (sendBangumiUsername) bangumi.username = bangumiUsername;" in js
    # Error-code mapping for token rejection surfaces the real cause.
    assert "invalid_bangumi_access_token" in js
    assert "bangumi_token_check_failed" in js


def test_desktop_exposes_bangumi_clear_token_and_rejected_status() -> None:
    html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    # C: an explicit "clear token" control that sends access_token:"".
    assert 'id="bangumiClearToken"' in html
    assert '"bangumiClearToken"' in js
    assert 'document.getElementById("bangumiClearToken")?.checked' in js
    assert '{ access_token: "" }' in js
    # A: the rejected token_state renders an actionable warning badge.
    #
    # The check lives wherever the status row is rendered, which is now the
    # shared module the three surfaces load rather than this bundle. Asserting
    # the literal in ``app.js`` pinned the test to one file's copy of the logic
    # — exactly the duplication the shared module removed — so search both.
    rendering_sources = js + shared_source_status_js()
    assert 'token_state) === "rejected"' in rendering_sources
    assert "令牌已失效" in rendering_sources


def test_setup_guided_init_username_omit_and_warnings() -> None:
    html = (ROOT / "src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")

    # F3: same omit-vs-clear contract on the packaged setup surface.
    assert "initBangumiUsernamePrefilled" in html
    assert (
        'initBangumiUsernameTouched && (bangumiUsername !== "" || initBangumiUsernamePrefilled)'
        in html
    )
    assert 'selected.includes("bangumi") && (sendBangumiUsername || bangumiToken)' in html
    # F4: read the 202 body and render warnings via setInitReason (safe text).
    assert "startBody.warnings" in html
    assert 'setInitReason(startWarnings.join(" "), "warn")' in html


def test_web_surfaces_do_not_reimplement_the_bangumi_admission_check() -> None:
    """The backend owns the three-tier account ladder; no surface may copy it.

    setup, desktop web and the extension popup each carried a byte-identical
    pre-flight guard that refused to POST /api/init for a Bangumi-only run
    without a typed username or token. None of the three could see the third
    tier (the identity the extension reports from a logged-in bgm.tv page), so
    a zero-config Bangumi-only run was unreachable from every GUI surface —
    the guard was originally believed to be absent from the popup, but it was
    there too, only spelled with a different variable name.

    The backend answers 409 ``no_profile_signal_sources`` when all three tiers
    are genuinely missing, and each surface renders that reply instead.
    """
    surfaces = {
        "setup": ROOT / "src/openbiliclaw/web/setup/index.html",
        "desktop": ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js",
        "popup": ROOT / "extension/popup/popup.js",
    }
    for name, path in surfaces.items():
        source = path.read_text(encoding="utf-8")
        # Both spellings: setup/desktop used `selected`, popup `selectedSources`.
        assert 'selected[0] === "bangumi"' not in source, name
        assert 'selectedSources[0] === "bangumi"' not in source, name

    # The popup hardcoded the rejection sentence inline instead of going
    # through its reason map, so deleting the guard has to take the copy with
    # it — otherwise the stale "username or token" wording survives.
    popup = surfaces["popup"].read_text(encoding="utf-8")
    assert "请填写个人令牌（推荐）或公开用户名以读取收藏。" not in popup


def test_popup_renders_the_backend_rejection_for_a_failed_init_start() -> None:
    """Deleting the guard must not turn a rejected start into silence.

    The popup now lets the request reach the backend, so the 409 reply is the
    only feedback the user gets. Behaviour of the mapping itself is covered by
    extension/tests/init-control.test.ts; this only pins the wiring, which is
    what a future refactor of the click handler could quietly drop.
    """
    popup = (ROOT / "extension/popup/popup.js").read_text(encoding="utf-8")
    assert "_setInitReason(describeInitStartError(error))" in popup


def test_shared_profile_signal_rejection_fallback_is_platform_neutral() -> None:
    """A shared reason cannot assume the selected source was Bangumi.

    Source-specific guidance, including Bangumi's three account tiers, now
    comes from the backend detail. Actual rendering is covered by
    test_web_guided_init_e2e.py and extension/tests/init-control.test.ts.
    With no usable detail, every surface must retain a neutral recovery hint.
    """
    reason_maps = {
        "setup": ROOT / "src/openbiliclaw/web/setup/index.html",
        "desktop": ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js",
        "popup": ROOT / "extension/popup/popup-init-control.js",
    }
    for name, path in reason_maps.items():
        source = path.read_text(encoding="utf-8")
        marker = "no_profile_signal_sources:"
        assert marker in source, name
        copy = source.split(marker, 1)[1].lstrip().split('"', 2)[1]
        assert copy == "所选来源暂时无法提供画像信号，请检查对应账号、凭据及扩展连接后重试。", name


def test_every_token_field_explains_the_three_ways_to_supply_an_account() -> None:
    """The token/username/extension choice is explained wherever it is asked.

    Five screens ask for a Bangumi token and each stores its own copy, so the
    wording drifts (rule 5). The setup wizard is where a new user meets the
    field first and it used to say only "约 1 年有效" — nothing about what the
    token buys, and nothing about the fact that leaving both fields blank is a
    valid path when the browser is already logged into bgm.tv. That silence is
    what made the admission bug plausible in the first place.

    Each screen must name all three ways in, and the trade-off that picks
    between them: the token reads private collections, the public username
    does not, and the extension-read account may be unverified.
    """
    screens = {
        "setup init": ROOT / "src/openbiliclaw/web/setup/index.html",
        "desktop init": ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js",
        "desktop settings": ROOT / "src/openbiliclaw/web/desktop/index.html",
        "popup init": ROOT / "extension/popup/popup.js",
        "popup settings": ROOT / "extension/popup/popup.html",
    }
    doc_anchor = (
        "https://github.com/whiteguo233/OpenBiliClaw/blob/main/"
        "docs/modules/bangumi.md#获取-bangumi-个人令牌"
    )
    for name, path in screens.items():
        source = path.read_text(encoding="utf-8")
        marker = "Bangumi 账号三选一"
        assert marker in source, f"{name} lost the three-way explanation"
        copy = source[source.index(marker) : source.index(marker) + 420]
        assert "个人令牌" in copy, name  # tier 1
        assert "私密收藏" in copy, name  # ...and why it is the most complete
        assert "公开用户名" in copy, name  # tier 2
        assert "公开收藏" in copy, name  # ...and its narrower reach
        assert "bgm.tv" in copy, name  # tier 3
        assert "未经校验" in copy, name  # ...and its caveat
        # Step-by-step token instructions live in the doc, not in the UI: the
        # generation page is somebody else's site and would go stale here.
        assert doc_anchor in source, f"{name} lost the token how-to link"
        assert "https://next.bgm.tv/demo/access-token" in source, name

    # The linked anchor has to exist, or every screen deep-links to nothing.
    module_doc = (ROOT / "docs/modules/bangumi.md").read_text(encoding="utf-8")
    assert "\n## 获取 Bangumi 个人令牌\n" in module_doc


def test_token_how_to_doc_covers_prerequisites_validity_and_expiry() -> None:
    """The doc the UI defers to must actually answer what the UI stopped saying."""
    doc = (ROOT / "docs/modules/bangumi.md").read_text(encoding="utf-8")
    section = doc[doc.index("## 获取 Bangumi 个人令牌") :]
    section = section[: section.index("\n## ", 1)] if "\n## " in section[1:] else section
    assert "登录" in section  # must be signed in to bgm.tv first
    assert "https://next.bgm.tv/demo/access-token" in section
    assert "1 年" in section  # validity
    assert "视同密码" in section  # handling
    assert "token_state" in section and "rejected" in section  # expiry signal
    assert "令牌已失效" in section  # ...as the status area words it
    assert "bgm.tv 实际页面为准" in section  # external page may change


def test_setup_exposes_anonymous_bangumi_bootstrap() -> None:
    html = (ROOT / "src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")

    # Bangumi has to be offered as an init source. The roster used to be a
    # hand-written literal here; it is now derived from the shared module's
    # SOURCE_KEYS, which is the point — one roster, not one per surface. So
    # assert the derivation is wired *and* that it actually yields Bangumi,
    # rather than pinning the test to a literal that duplication would restore.
    assert "SourceStatus.INIT_SOURCE_KEYS.map(" in html, (
        "init roster must derive from the shared capability roster"
    )
    assert '"bangumi"' in shared_source_status_js(), "shared roster must carry Bangumi"
    assert 'bangumiInput.id = "initBangumiUsername"' in html
    assert "Bangumi \u4f7f\u7528\u516c\u5f00 API，\u4e0d\u9700\u767b\u5f55" in html
    assert 'if (selected.includes("bangumi") && (sendBangumiUsername || bangumiToken))' in html
    assert "if (sendBangumiUsername) bangumi.username = bangumiUsername;" in html
    assert "if (bangumiToken) bangumi.access_token = bangumiToken;" in html
    assert "no_profile_signal_sources" in html
    # Optional personal access token input + generation link.
    assert 'bangumiTokenInput.id = "initBangumiToken"' in html
    assert "https://next.bgm.tv/demo/access-token" in html
    assert "invalid_bangumi_access_token" in html
    assert (
        'let initBangumiUsername = "", initBangumiUsernameTouched = false, '
        "initBangumiUsernamePrefilled = false;" in html
    )
    assert "bangumiInput.value = initBangumiUsername;" in html
