"""Tests for discovery source policy helpers."""

from openbiliclaw.config import Config
from openbiliclaw.runtime.source_policy import (
    DEFAULT_POOL_SOURCE_SHARES,
    effective_pool_source_shares,
    source_enabled_map,
    suggest_pool_source_shares,
)


def test_source_enabled_map_keeps_bilibili_core_enabled() -> None:
    config = Config()
    config.sources.xiaohongshu.enabled = False
    config.sources.douyin.enabled = False
    config.sources.youtube.enabled = False

    assert source_enabled_map(config) == {
        "bilibili": True,
        "xiaohongshu": False,
        "douyin": False,
        "youtube": False,
    }


def test_effective_pool_source_shares_drop_disabled_optional_sources() -> None:
    config = Config()
    config.scheduler.pool_source_shares = {
        "bilibili": 8,
        "xiaohongshu": 3,
        "douyin": 2,
        "youtube": 1,
    }
    config.sources.xiaohongshu.enabled = False
    config.sources.douyin.enabled = False
    config.sources.youtube.enabled = False

    assert effective_pool_source_shares(config) == {"bilibili": 8}
    assert config.scheduler.pool_source_shares["xiaohongshu"] == 3


def test_effective_pool_source_shares_keep_enabled_youtube() -> None:
    config = Config()
    config.scheduler.pool_source_shares = {
        "bilibili": 6,
        "xiaohongshu": 1,
        "douyin": 1,
        "youtube": 2,
    }
    config.sources.xiaohongshu.enabled = False
    config.sources.douyin.enabled = False
    config.sources.youtube.enabled = True

    assert effective_pool_source_shares(config) == {
        "bilibili": 6,
        "youtube": 2,
    }


def test_effective_pool_source_shares_fall_back_to_defaults() -> None:
    config = Config()
    config.scheduler.pool_source_shares = {}
    config.sources.xiaohongshu.enabled = False
    config.sources.douyin.enabled = True
    config.sources.youtube.enabled = False

    assert DEFAULT_POOL_SOURCE_SHARES["youtube"] == 1
    assert effective_pool_source_shares(config) == {
        "bilibili": 8,
        "douyin": 1,
    }


def test_suggest_pool_source_shares_uses_damped_event_counts() -> None:
    suggestion = suggest_pool_source_shares(
        {"bilibili": 900, "xiaohongshu": 100, "douyin": 9, "youtube": 400},
        enabled_sources={
            "bilibili": True,
            "xiaohongshu": True,
            "douyin": True,
            "youtube": True,
        },
    )

    assert suggestion == {
        "bilibili": 8,
        "xiaohongshu": 3,
        "douyin": 1,
        "youtube": 5,
    }


def test_suggest_pool_source_shares_ignores_disabled_sources() -> None:
    suggestion = suggest_pool_source_shares(
        {"bilibili": 9, "xiaohongshu": 900, "douyin": 900, "youtube": 900},
        enabled_sources={
            "bilibili": True,
            "xiaohongshu": False,
            "douyin": False,
            "youtube": False,
        },
    )

    assert suggestion == {"bilibili": 8}


def test_suggest_pool_source_shares_falls_back_when_counts_empty() -> None:
    suggestion = suggest_pool_source_shares(
        {},
        enabled_sources={
            "bilibili": True,
            "xiaohongshu": True,
            "douyin": False,
            "youtube": True,
        },
        configured_shares={
            "bilibili": 7,
            "xiaohongshu": 2,
            "douyin": 2,
            "youtube": 3,
        },
    )

    assert suggestion == {
        "bilibili": 7,
        "xiaohongshu": 2,
        "youtube": 3,
    }
