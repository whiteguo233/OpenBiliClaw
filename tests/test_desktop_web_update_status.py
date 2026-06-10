"""Static regressions for the settings-page backend update-status line."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_desktop_web_settings_wires_update_status_line() -> None:
    html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert 'id="updateStatusLine"' in html

    assert 'updateStatus: "/update-status"' in js
    assert "function describeUpdateStatus" in js
    assert "function renderUpdateStatus" in js
    assert "function refreshUpdateStatus" in js

    # Frozen desktop bundles cannot self-update — toggle disabled with a hint.
    assert "install_mode" in js
    assert "toggle.disabled = unsupportedInstall" in js
    assert "桌面安装包不支持后端自动更新" in js

    # The blocking reasons users actually hit are localized.
    assert "dirty_worktree" in js
    assert "branch_not_fast_forwardable" in js

    # Status refreshes when the settings page opens AND after a config save.
    assert js.count("void refreshUpdateStatus();") >= 2
