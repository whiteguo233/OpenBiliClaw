from openbiliclaw.saved_sync.identity import (
    canonical_source_platform,
    content_storage_key,
    is_native_save_local_only,
    make_item_key,
)
from openbiliclaw.saved_sync.models import SavedItemInput


def test_canonical_source_aliases_and_cross_platform_keys() -> None:
    assert canonical_source_platform("x") == "twitter"
    assert canonical_source_platform("yt") == "youtube"
    assert make_item_key("twitter", "123") == "twitter:123"
    assert make_item_key("douyin", "123") == "douyin:123"
    assert content_storage_key("bilibili", "BV1abc") == "BV1abc"
    assert content_storage_key("twitter", "123") == "twitter:123"


def test_native_save_local_only_roster() -> None:
    # Platforms with intentionally no upstream save adapter: local saves commit
    # as unsupported/local_only_source instead of a misleading pending state.
    assert is_native_save_local_only("github") is True
    assert is_native_save_local_only("weibo") is True
    assert is_native_save_local_only("tiktok") is True
    assert is_native_save_local_only("bilibili") is False
    assert is_native_save_local_only("douyin") is False


def test_saved_item_requires_stable_identity() -> None:
    item = SavedItemInput(
        source_platform="bilibili",
        content_id="BV1abc",
        content_url="https://www.bilibili.com/video/BV1abc",
        content_type="video",
        title="demo",
    )
    assert item.item_key == "bilibili:BV1abc"
