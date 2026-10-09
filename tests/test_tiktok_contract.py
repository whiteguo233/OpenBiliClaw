"""Executable exclusions for TikTok's discovery-only contract."""

import json
import tomllib
from pathlib import Path

from openbiliclaw.runtime.init_prereqs import _PLATFORM_SOURCE_FIELDS
from openbiliclaw.sources.tiktok_web import parse_tiktok_item

ROOT = Path(__file__).resolve().parents[1]


def test_tiktok_discovery_only_exclusions() -> None:
    assert "tiktok" not in _PLATFORM_SOURCE_FIELDS
    for target in ("manifest.json", "manifest.firefox.json"):
        manifest = json.loads((ROOT / "extension" / target).read_text())
        assert not any(
            "tiktok.com" in match
            for script in manifest["content_scripts"]
            for match in script["matches"]
        )
    assert (
        "tiktok" not in (ROOT / "src/openbiliclaw/runtime/source_incremental_sync.py").read_text()
    )
    from openbiliclaw.saved_sync.identity import is_native_save_local_only

    assert is_native_save_local_only("tiktok")
    item = parse_tiktok_item(
        {"id": "123", "desc": "hello", "author": {"uniqueId": "example"}, "video": {}}
    )
    assert item is not None
    assert item.content_url == "https://www.tiktok.com/@example/video/123"
    assert item.danmaku_count == 0


def test_tiktok_profile_signals_are_excluded() -> None:
    assert "tiktok" not in _PLATFORM_SOURCE_FIELDS


def test_tiktok_profile_refresh_mode_is_none() -> None:
    assert "tiktok" not in _PLATFORM_SOURCE_FIELDS
    assert (
        "tiktok" not in (ROOT / "src/openbiliclaw/runtime/source_incremental_sync.py").read_text()
    )


def _has_tiktok_content_script() -> bool:
    return any(
        "tiktok.com" in match
        for target in ("manifest.json", "manifest.firefox.json")
        for script in json.loads((ROOT / "extension" / target).read_text())["content_scripts"]
        for match in script["matches"]
    )


def test_tiktok_extension_task_is_excluded() -> None:
    assert not _has_tiktok_content_script()


def test_tiktok_extension_task_marker_is_absent() -> None:
    assert not _has_tiktok_content_script()


def test_tiktok_extension_background_task_is_absent() -> None:
    assert not _has_tiktok_content_script()
    assert not (ROOT / "extension/src/background/tiktok-task-dispatcher.ts").exists()


def test_tiktok_extension_early_response_is_absent() -> None:
    assert not _has_tiktok_content_script()


def test_tiktok_surface_setup_excludes_guided_init() -> None:
    shared = (ROOT / "src/openbiliclaw/web/shared/source-status.js").read_text()
    assert "tiktok: Object.freeze({ guidedInit: false })" in shared
    assert "tiktok" not in _PLATFORM_SOURCE_FIELDS


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
