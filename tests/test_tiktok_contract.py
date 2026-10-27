"""Executable contract checks for TikTok's full-source integration.

The discovery-only exclusions were reversed when the upgrade landed
(backend task queue, guided init, extension task pipeline, native save).
What remains excluded is intentional and frozen in
``docs/platform-source-contract.tiktok.toml``: ``engagement.danmaku``
(TikTok has no danmaku metric) and ``media.deep-link`` (canonical HTTPS
browser fallback), plus the product-wide mobile credential exclusions.
"""

import json
import tomllib
from pathlib import Path

from openbiliclaw.runtime.init_prereqs import _PLATFORM_SOURCE_FIELDS
from openbiliclaw.sources.tiktok_web import parse_tiktok_item

ROOT = Path(__file__).resolve().parents[1]


def _has_tiktok_content_script() -> bool:
    return any(
        "tiktok.com" in match
        for target in ("manifest.json", "manifest.firefox.json")
        for script in json.loads((ROOT / "extension" / target).read_text())["content_scripts"]
        for match in script["matches"]
    )


def test_tiktok_full_source_registrations() -> None:
    """The discovery-only exclusions are lifted: TikTok is a full source."""
    assert "tiktok" in _PLATFORM_SOURCE_FIELDS
    for target in ("manifest.json", "manifest.firefox.json"):
        manifest = json.loads((ROOT / "extension" / target).read_text())
        assert any(
            "tiktok.com" in match
            for script in manifest["content_scripts"]
            for match in script["matches"]
        ), target
    assert "tiktok" in (ROOT / "src/openbiliclaw/runtime/source_incremental_sync.py").read_text()
    from openbiliclaw.saved_sync.identity import is_native_save_local_only

    assert not is_native_save_local_only("tiktok")
    item = parse_tiktok_item(
        {"id": "123", "desc": "hello", "author": {"uniqueId": "example"}, "video": {}}
    )
    assert item is not None
    assert item.content_url == "https://www.tiktok.com/@example/video/123"
    assert item.danmaku_count == 0


def test_tiktok_profile_signals_are_wired() -> None:
    assert "tiktok" in _PLATFORM_SOURCE_FIELDS


def test_tiktok_profile_refresh_mode_is_incremental() -> None:
    contract = tomllib.loads((ROOT / "docs/platform-source-contract.tiktok.toml").read_text())
    assert contract["profile"] == {
        "signals": True,
        "incremental": True,
        "refresh_mode": "incremental",
    }
    assert "tiktok" in _PLATFORM_SOURCE_FIELDS
    assert "tiktok" in (ROOT / "src/openbiliclaw/runtime/source_incremental_sync.py").read_text()


def test_tiktok_extension_task_is_registered() -> None:
    """The browser task content script is registered in both manifests."""
    assert _has_tiktok_content_script()
    assert (ROOT / "extension/src/content/tiktok.ts").exists()


def test_tiktok_extension_task_marker_is_present() -> None:
    """Bootstrap tabs carry the TikTok task marker so recovery can sweep them."""
    dispatcher = (ROOT / "extension/src/background/tiktok-task-dispatcher.ts").read_text()
    assert "openbiliclaw_tiktok_task" in dispatcher
    assert "withTaskTabMarker" in dispatcher


def test_tiktok_extension_background_task_is_registered() -> None:
    """The service worker wires the TikTok dispatcher's polling and recovery."""
    assert _has_tiktok_content_script()
    assert (ROOT / "extension/src/background/tiktok-task-dispatcher.ts").exists()
    service_worker = (ROOT / "extension/src/background/service-worker.ts").read_text()
    assert "tiktok-task-dispatcher" in service_worker
    assert "ensureTiktokTaskRecovery" in service_worker


def test_tiktok_extension_early_response_is_wired() -> None:
    """Results persist the canonical body before POST and retry until ACK."""
    dispatcher = (ROOT / "extension/src/background/tiktok-task-dispatcher.ts").read_text()
    # Two-stage staged completion: the exact canonical result body is
    # persisted into the pending-result slot before the POST, and a fresh
    # worker flushes it before claiming new work.
    assert _has_tiktok_content_script()
    assert "chrome.storage.local" in dispatcher
    assert "pending" in dispatcher.lower()


def test_tiktok_surface_setup_includes_guided_init() -> None:
    shared = (ROOT / "src/openbiliclaw/web/shared/source-status.js").read_text()
    assert "tiktok: Object.freeze({ guidedInit: true })" in shared
    assert "tiktok" in _PLATFORM_SOURCE_FIELDS


def test_tiktok_engagement_danmaku_is_unavailable() -> None:
    """Registered exclusion: TikTok has no danmaku metric, ever."""
    contract = tomllib.loads((ROOT / "docs/platform-source-contract.tiktok.toml").read_text())
    assert contract["engagement"]["danmaku"] == "unavailable"
    item = parse_tiktok_item(
        {
            "id": "123",
            "desc": "hello",
            "author": {"uniqueId": "example"},
            "video": {},
            "stats": {"diggCount": 5, "shareCount": 2, "commentCount": 7},
        }
    )
    assert item is not None
    assert item.danmaku_count == 0


def test_tiktok_media_deep_link_uses_https() -> None:
    item = parse_tiktok_item({"id": "123", "desc": "title", "author": {"uniqueId": "creator"}})
    assert item is not None
    assert item.content_url == "https://www.tiktok.com/@creator/video/123"


def test_tiktok_mobile_consumption_excludes_credential_management() -> None:
    contract = tomllib.loads((ROOT / "docs/platform-source-contract.tiktok.toml").read_text())
    surfaces = contract["surfaces"]
    assert surfaces["mobile"] is True
    assert surfaces["mobile_credentials"] is False
    assert surfaces["mobile_source_settings"] is False
    assert surfaces["mobile_verify"] is False
    # Mobile intentionally has no credential read/write/probe API surface.
    mobile_api = (ROOT / "src/openbiliclaw/web/js/api.js").read_text()
    assert "/sources/credentials" not in mobile_api
    assert "/credential" not in mobile_api
    assert "/verify" not in mobile_api


def test_tiktok_contract_exclusions_are_exactly_the_frozen_two() -> None:
    """Only danmaku and deep-link remain as declared exclusions."""
    contract = tomllib.loads((ROOT / "docs/platform-source-contract.tiktok.toml").read_text())
    assert set(contract["exclusions"]) == {"engagement.danmaku", "media.deep-link"}
    assert set(contract["exclusion_tests"]) == {"engagement.danmaku", "media.deep-link"}
