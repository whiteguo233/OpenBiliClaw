from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _contract(platform: str) -> dict[str, Any]:
    path = ROOT / f"docs/platform-source-contract.{platform}-native-save-confirmation.toml"
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _is_excluded(platform: str, capability: str) -> bool:
    contract = _contract(platform)
    return (
        contract["integration_level"] == "capability-increment"
        and contract["media"]["native_save"] is True
        and capability in contract["exclusions"]
    )


def test_youtube_native_save_contract_excludes_formal_discover() -> None:
    assert _is_excluded("youtube", "discover.formal")


def test_youtube_native_save_contract_excludes_search_integration() -> None:
    assert _is_excluded("youtube", "search.integration")


def test_youtube_native_save_contract_excludes_profile_signals() -> None:
    assert _is_excluded("youtube", "profile.signals")


def test_youtube_native_save_contract_excludes_profile_incremental() -> None:
    assert _is_excluded("youtube", "profile.incremental")


def test_youtube_native_save_contract_excludes_profile_refresh_mode() -> None:
    assert _is_excluded("youtube", "profile.refresh-mode")


def test_youtube_native_save_contract_excludes_task_marker() -> None:
    assert _is_excluded("youtube", "extension.task-marker")


def test_youtube_native_save_contract_excludes_early_response() -> None:
    assert _is_excluded("youtube", "extension.early-response")


def test_youtube_native_save_contract_excludes_cookie_sync() -> None:
    assert _is_excluded("youtube", "extension.cookie-sync")


def test_youtube_native_save_contract_excludes_setup_surface() -> None:
    assert _is_excluded("youtube", "surface.setup")


def test_youtube_native_save_contract_excludes_media_image() -> None:
    assert _is_excluded("youtube", "media.image")


def test_youtube_native_save_contract_excludes_media_deep_link() -> None:
    assert _is_excluded("youtube", "media.deep-link")


def test_youtube_native_save_contract_excludes_engagement_view() -> None:
    assert _is_excluded("youtube", "engagement.view")


def test_youtube_native_save_contract_excludes_engagement_like() -> None:
    assert _is_excluded("youtube", "engagement.like")


def test_youtube_native_save_contract_excludes_engagement_favorite() -> None:
    assert _is_excluded("youtube", "engagement.favorite")


def test_youtube_native_save_contract_excludes_engagement_comment() -> None:
    assert _is_excluded("youtube", "engagement.comment")


def test_youtube_native_save_contract_excludes_engagement_share() -> None:
    assert _is_excluded("youtube", "engagement.share")


def test_youtube_native_save_contract_excludes_engagement_danmaku() -> None:
    assert _is_excluded("youtube", "engagement.danmaku")


def test_zhihu_native_save_contract_excludes_formal_discover() -> None:
    assert _is_excluded("zhihu", "discover.formal")


def test_zhihu_native_save_contract_excludes_search_integration() -> None:
    assert _is_excluded("zhihu", "search.integration")


def test_zhihu_native_save_contract_excludes_profile_signals() -> None:
    assert _is_excluded("zhihu", "profile.signals")


def test_zhihu_native_save_contract_excludes_profile_incremental() -> None:
    assert _is_excluded("zhihu", "profile.incremental")


def test_zhihu_native_save_contract_excludes_profile_refresh_mode() -> None:
    assert _is_excluded("zhihu", "profile.refresh-mode")


def test_zhihu_native_save_contract_excludes_task_marker() -> None:
    assert _is_excluded("zhihu", "extension.task-marker")


def test_zhihu_native_save_contract_excludes_early_response() -> None:
    assert _is_excluded("zhihu", "extension.early-response")


def test_zhihu_native_save_contract_excludes_setup_surface() -> None:
    assert _is_excluded("zhihu", "surface.setup")


def test_zhihu_native_save_contract_excludes_media_image() -> None:
    assert _is_excluded("zhihu", "media.image")


def test_zhihu_native_save_contract_excludes_media_deep_link() -> None:
    assert _is_excluded("zhihu", "media.deep-link")


def test_zhihu_native_save_contract_excludes_engagement_view() -> None:
    assert _is_excluded("zhihu", "engagement.view")


def test_zhihu_native_save_contract_excludes_engagement_like() -> None:
    assert _is_excluded("zhihu", "engagement.like")


def test_zhihu_native_save_contract_excludes_engagement_favorite() -> None:
    assert _is_excluded("zhihu", "engagement.favorite")


def test_zhihu_native_save_contract_excludes_engagement_comment() -> None:
    assert _is_excluded("zhihu", "engagement.comment")


def test_zhihu_native_save_contract_excludes_engagement_share() -> None:
    assert _is_excluded("zhihu", "engagement.share")


def test_zhihu_native_save_contract_excludes_engagement_danmaku() -> None:
    assert _is_excluded("zhihu", "engagement.danmaku")
