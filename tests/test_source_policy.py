"""Tests for discovery source policy helpers."""

from openbiliclaw.config import Config
from openbiliclaw.runtime.source_policy import (
    DEFAULT_POOL_SOURCE_SHARES,
    effective_pool_source_shares,
    source_enabled_map,
    suggest_pool_source_shares,
)


def test_source_enabled_map_reads_bilibili_switch() -> None:
    config = Config()
    config.sources.bilibili.enabled = False
    config.sources.xiaohongshu.enabled = False
    config.sources.douyin.enabled = False
    config.sources.youtube.enabled = False

    assert source_enabled_map(config) == {
        "bilibili": False,
        "xiaohongshu": False,
        "douyin": False,
        "youtube": False,
        "twitter": False,
        "zhihu": False,
        "reddit": False,
    }


def test_default_enabled_sources_make_xiaohongshu_opt_in() -> None:
    config = Config()

    assert source_enabled_map(config) == {
        "bilibili": True,
        "xiaohongshu": False,
        "douyin": False,
        "youtube": False,
        "twitter": False,
        "zhihu": False,
        "reddit": False,
    }
    assert effective_pool_source_shares(config) == {"bilibili": 5}


def test_effective_pool_source_shares_drop_disabled_bilibili() -> None:
    config = Config()
    config.sources.bilibili.enabled = False
    config.sources.xiaohongshu.enabled = True
    config.scheduler.pool_source_shares = {
        "bilibili": 8,
        "xiaohongshu": 2,
        "douyin": 1,
        "youtube": 1,
    }

    assert effective_pool_source_shares(config) == {"xiaohongshu": 2}


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


def test_effective_pool_source_shares_keep_enabled_zhihu() -> None:
    config = Config()
    config.scheduler.pool_source_shares = {
        "bilibili": 6,
        "xiaohongshu": 1,
        "douyin": 1,
        "youtube": 1,
        "twitter": 1,
        "zhihu": 2,
    }
    config.sources.xiaohongshu.enabled = False
    config.sources.douyin.enabled = False
    config.sources.youtube.enabled = False
    config.sources.twitter.enabled = False
    config.sources.zhihu.enabled = True

    assert effective_pool_source_shares(config) == {
        "bilibili": 6,
        "zhihu": 2,
    }


def test_effective_pool_source_shares_keep_enabled_reddit() -> None:
    config = Config()
    config.scheduler.pool_source_shares = {
        "bilibili": 6,
        "xiaohongshu": 1,
        "douyin": 1,
        "youtube": 1,
        "twitter": 1,
        "zhihu": 1,
        "reddit": 3,
    }
    config.sources.xiaohongshu.enabled = False
    config.sources.douyin.enabled = False
    config.sources.youtube.enabled = False
    config.sources.twitter.enabled = False
    config.sources.zhihu.enabled = False
    config.sources.reddit.enabled = True

    assert effective_pool_source_shares(config) == {
        "bilibili": 6,
        "reddit": 3,
    }


def test_effective_pool_source_shares_backfills_enabled_zhihu_default() -> None:
    config = Config()
    config.scheduler.pool_source_shares = {
        "bilibili": 6,
        "xiaohongshu": 1,
        "douyin": 1,
        "youtube": 1,
        "twitter": 1,
    }
    config.sources.xiaohongshu.enabled = False
    config.sources.douyin.enabled = False
    config.sources.youtube.enabled = False
    config.sources.twitter.enabled = False
    config.sources.zhihu.enabled = True

    assert effective_pool_source_shares(config) == {
        "bilibili": 6,
        "zhihu": 1,
    }


def test_effective_pool_source_shares_fall_back_to_defaults() -> None:
    config = Config()
    config.scheduler.pool_source_shares = {}
    config.sources.xiaohongshu.enabled = False
    config.sources.douyin.enabled = True
    config.sources.youtube.enabled = False

    assert DEFAULT_POOL_SOURCE_SHARES["youtube"] == 1
    assert effective_pool_source_shares(config) == {
        "bilibili": 5,
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
            "zhihu": True,
            "reddit": True,
        },
    )

    assert suggestion == {
        "bilibili": 5,
        "xiaohongshu": 2,
        "douyin": 1,
        "youtube": 3,
        "zhihu": 1,
        "reddit": 1,
    }


def test_suggest_pool_source_shares_ignores_disabled_sources() -> None:
    suggestion = suggest_pool_source_shares(
        {"bilibili": 9, "xiaohongshu": 900, "douyin": 900, "youtube": 900},
        enabled_sources={
            "bilibili": False,
            "xiaohongshu": False,
            "douyin": False,
            "youtube": False,
        },
    )

    assert suggestion == {}


def test_suggest_pool_source_shares_falls_back_when_counts_empty() -> None:
    suggestion = suggest_pool_source_shares(
        {},
        enabled_sources={
            "bilibili": True,
            "xiaohongshu": True,
            "douyin": False,
            "youtube": True,
            "zhihu": True,
            "reddit": True,
        },
        configured_shares={
            "bilibili": 7,
            "xiaohongshu": 2,
            "douyin": 2,
            "youtube": 3,
            "zhihu": 4,
            "reddit": 5,
        },
    )

    assert suggestion == {
        "bilibili": 7,
        "xiaohongshu": 2,
        "youtube": 3,
        "zhihu": 4,
        "reddit": 5,
    }
