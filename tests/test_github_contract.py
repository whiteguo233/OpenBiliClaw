from __future__ import annotations

import tomllib
from pathlib import Path

from openbiliclaw.saved_sync.identity import is_native_save_local_only

_ROOT = Path(__file__).resolve().parents[1]
_CONTRACT = tomllib.loads(
    (_ROOT / "docs/platform-source-contract.github.toml").read_text(encoding="utf-8")
)


def test_github_profile_incremental_is_explicit_only() -> None:
    profile = _CONTRACT["profile"]
    assert profile["incremental"] is False
    assert profile["refresh_mode"] == "init-and-on-demand"


def test_github_extension_task_is_not_required() -> None:
    assert _CONTRACT["extension"]["task"] == "none"


def test_github_extension_task_marker_is_absent() -> None:
    assert _CONTRACT["extension"]["task_marker"] is False


def test_github_extension_background_is_absent() -> None:
    assert _CONTRACT["extension"]["background"] is False


def test_github_extension_early_response_is_absent() -> None:
    assert _CONTRACT["extension"]["early_response"] is False


def test_github_extension_cookie_sync_is_absent() -> None:
    assert _CONTRACT["extension"]["cookie_sync"] is False


def test_github_media_image_is_text_only() -> None:
    assert _CONTRACT["media"]["image"] == "none"


def test_github_media_deep_link_uses_browser_fallback() -> None:
    assert _CONTRACT["media"]["deep_link"] == "browser-fallback"


def test_github_media_native_save_is_read_only() -> None:
    assert _CONTRACT["media"]["native_save"] is False
    assert _CONTRACT["e2e"]["mutating_actions"] == []
    assert is_native_save_local_only("github") is True
    assert is_native_save_local_only("gh") is True


def test_github_engagement_view_is_unavailable() -> None:
    assert _CONTRACT["engagement"]["view"] == "unavailable"


def test_github_engagement_like_is_unavailable() -> None:
    assert _CONTRACT["engagement"]["like"] == "unavailable"


def test_github_engagement_comment_is_unavailable() -> None:
    assert _CONTRACT["engagement"]["comment"] == "unavailable"


def test_github_engagement_share_is_unavailable() -> None:
    assert _CONTRACT["engagement"]["share"] == "unavailable"


def test_github_engagement_danmaku_is_unavailable() -> None:
    assert _CONTRACT["engagement"]["danmaku"] == "unavailable"
