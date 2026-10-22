"""Executable exclusions and privacy boundaries for the Instagram contract."""

from __future__ import annotations

import tomllib
from pathlib import Path

from openbiliclaw.api.source_auth.forms import build_credential_form
from openbiliclaw.api.source_auth.write import CREDENTIAL_SPECS
from openbiliclaw.config import Config
from openbiliclaw.saved_sync.identity import is_native_save_local_only
from openbiliclaw.sources.instagram import instagram_media_to_content
from openbiliclaw.sources.instagram_tasks import (
    INSTAGRAM_BOOTSTRAP_SCOPES,
    instagram_account_key,
    instagram_bootstrap_item_key,
    is_instagram_account_key,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = tomllib.loads(
    (ROOT / "docs/platform-source-contract.instagram.toml").read_text(encoding="utf-8")
)


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_instagram_profile_refresh_is_init_only() -> None:
    assert CONTRACT["integration_level"] == "full"
    assert CONTRACT["profile"] == {
        "signals": True,
        "incremental": False,
        "refresh_mode": "init-only",
    }
    assert "instagram_incremental" not in _read(
        "src/openbiliclaw/runtime/source_incremental_sync.py"
    )
    assert "instagram_incremental_hours" not in _read("src/openbiliclaw/config.py")


def test_instagram_search_integration_is_excluded() -> None:
    assert CONTRACT["discover"]["modes"] == ["topic", "creator"]
    assert "search" not in CONTRACT["discover"]["modes"]
    executor = _read("extension/src/content/instagram/task-executor.ts")
    assert "/popular/" in executor
    assert "topsearch" not in executor
    # Passive response provenance is allowed, active private search replay is not.
    assert "fbsearch/" not in executor
    assert "topic:xdt_fbsearch__top_serp_graphql" in executor


def test_instagram_normalization_does_not_fabricate_engagement() -> None:
    assert set(CONTRACT["engagement"].values()) == {"unavailable"}
    content = instagram_media_to_content(
        {
            "id": "3712345678901234567",
            "code": "DAb_cd-123",
            "content_type": "post",
            "description": "Public caption",
            "view_count": 10,
            "like_count": 20,
            "favorite_count": 30,
            "comment_count": 40,
            "share_count": 50,
            "danmaku_count": 60,
        }
    )

    assert content is not None
    assert content.engagement_available == []
    assert (
        content.view_count,
        content.like_count,
        content.favorite_count,
        content.comment_count,
        content.share_count,
        content.danmaku_count,
    ) == (0, 0, 0, 0, 0, 0)


def test_instagram_mobile_uses_https_fallback() -> None:
    assert CONTRACT["media"]["deep_link"] == "browser-fallback"
    mobile_view_model = _read("src/openbiliclaw/web/js/view-models.js")
    app_launch = _read("src/openbiliclaw/web/js/app-launch.js")
    assert 'platform === "instagram"' in mobile_view_model
    assert "instagram://" not in mobile_view_model
    assert "instagram://" not in app_launch


def test_instagram_cover_contract_matches_shared_proxy_delivery() -> None:
    """Proxy delivery must not silently skip the audit's DNS/SSRF gate."""
    from openbiliclaw.runtime.image_cache import ALLOWED_IMAGE_HOST_SUFFIXES

    assert CONTRACT["media"]["image"] == "proxy"
    assert set(CONTRACT["media"]["image_hosts"]) <= set(ALLOWED_IMAGE_HOST_SUFFIXES)
    for path in (
        "src/openbiliclaw/web/desktop/assets/js/app.js",
        "src/openbiliclaw/web/js/view-models.js",
        "extension/popup/popup-helpers.js",
    ):
        assert "/image-proxy?url=${encodeURIComponent(" in _read(path)


def test_instagram_has_no_native_save_adapter() -> None:
    assert CONTRACT["media"]["native_save"] is False
    assert is_native_save_local_only("instagram") is True
    assert is_native_save_local_only("ig") is True
    adapters = _read("src/openbiliclaw/saved_sync/adapters/extension.py")
    assert 'ExtensionAdapterDefinition("instagram"' not in adapters


def test_instagram_auth_is_capability_specific_and_cookie_value_stays_browser_owned() -> None:
    assert CONTRACT["auth"]["mode"] == "capability-specific"
    assert CONTRACT["auth"]["capability_modes"] == {
        "discover": "optional-credential",
        "profile": "login-required",
        "bootstrap": "login-required",
        "cookie-sync": "optional-credential",
    }
    spec = CREDENTIAL_SPECS["instagram"]
    form = build_credential_form("instagram", cfg=Config())
    assert spec.kinds == ("login_state",)
    assert form.kind == "extension_only"
    assert form.required_keys == []


def test_instagram_personal_identity_is_opaque_and_scope_partitioned() -> None:
    account_key = instagram_account_key("25025320")
    assert is_instagram_account_key(account_key)
    assert "25025320" not in account_key
    assert set(INSTAGRAM_BOOTSTRAP_SCOPES) == {
        "instagram_liked",
        "instagram_saved",
        "instagram_following",
    }
    item = {
        "scope": "instagram_saved",
        "id": "3712345678901234567",
        "content_type": "post",
        "url": "https://www.instagram.com/p/DAb_cd-123/",
    }
    key = instagram_bootstrap_item_key(item, account_key=account_key)
    assert key.startswith(f"{account_key}:instagram_saved:")
