"""Static regressions for desktop Zhihu source settings."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_desktop_web_round_trips_zhihu_source_modes() -> None:
    html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    for element_id in (
        "zhihuModeSearch",
        "zhihuModeHot",
        "zhihuModeFeed",
        "zhihuModeCreator",
        "zhihuModeRelated",
    ):
        assert f'id="{element_id}"' in html
        assert f'"{element_id}"' in js

    assert "setZhihuSourceModes(config.sources?.zhihu?.source_modes)" in js
    assert "source_modes: collectZhihuSourceModes()" in js


def test_desktop_source_status_rows_separate_source_and_access_state() -> None:
    html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")

    assert 'id="sourceStatusList"' in html
    for source_key in (
        "bilibili",
        "xiaohongshu",
        "douyin",
        "youtube",
        "twitter",
        "zhihu",
    ):
        assert f'data-source-status="{source_key}"' in html

    assert 'class="source-source-badge"' in html
    assert 'class="source-access-badge"' in html
    assert "来源：" in html
    assert "接入：" in html


def test_desktop_source_status_js_has_pending_and_unsaved_states() -> None:
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert 'unverified: { tone: "pending"' in js
    assert "状态待验证" in js
    assert "SOURCE_ENABLE_SELECT_IDS" in js
    assert "source-row-unsaved" in js
    assert "保存后生效" in js


def test_desktop_cookie_fields_are_override_only() -> None:
    html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    for element_id in ("biliCookie", "douyinCookie", "twitterCookie"):
        assert f'id="{element_id}"' in html
        assert f'setCookieOverrideInput("{element_id}"' in js

    assert 'setInput("biliCookie", config.bilibili?.cookie)' not in js
    assert 'setInput("douyinCookie", config.sources?.douyin?.cookie)' not in js
    assert 'setInput("twitterCookie", config.sources?.twitter?.cookie)' not in js
    assert "留空保存不会覆盖" in js
    assert "需要更换时粘贴新的 Cookie" in js


def test_desktop_current_credentials_render_in_collapsed_panels() -> None:
    html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert 'id="sourceCredentialList"' in html
    for source_key in (
        "bilibili",
        "xiaohongshu",
        "douyin",
        "youtube",
        "twitter",
        "zhihu",
    ):
        assert f'data-source-credential="{source_key}"' in html

    assert "/sources/credentials?reveal_keys=true" in js
    assert "CURRENT_CREDENTIAL_KEYS" in js
    assert "renderSourceCredentials" in js
    assert "source-credential-value" in html
