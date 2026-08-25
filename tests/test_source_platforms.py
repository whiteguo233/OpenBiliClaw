"""Tests for canonical source-platform family rules."""

import pytest

from openbiliclaw.runtime.linuxdo_producer import LINUXDO_SOURCE_STRATEGIES
from openbiliclaw.runtime.zhihu_producer import ZHIHU_SOURCE_STRATEGIES
from openbiliclaw.sources.linuxdo_tasks import LINUXDO_DISCOVERY_SCOPE_STRATEGIES
from openbiliclaw.sources.platforms import (
    CANONICAL_SOURCE_FAMILIES,
    infer_source_platform_from_url,
    source_family,
)
from openbiliclaw.sources.zhihu_tasks import ZHIHU_DISCOVERY_SCOPE_STRATEGIES


@pytest.mark.parametrize(
    ("platform", "source", "expected"),
    [
        ("bilibili", "search", "bilibili"),
        ("bili", "related_chain", "bilibili"),
        ("xhs", "xhs-search", "xiaohongshu"),
        ("rednote", "xiaohongshu_task", "xiaohongshu"),
        ("dy", "dy-hot", "douyin"),
        ("tiktok", "douyin_search", "douyin"),
        ("yt", "yt-search", "youtube"),
        ("x", "x-feed", "twitter"),
        ("rd", "reddit-hot", "reddit"),
        ("zh", "zhihu-creator", "zhihu"),
        ("", "zhihu_hot", "zhihu"),
        ("zhihu", "zhihu-related", "zhihu"),
        ("bgm", "bangumi-ranked", "bangumi"),
        ("linux.do", "linuxdo-hot", "linuxdo"),
        ("ldo", "linuxdo_feed", "linuxdo"),
        ("", "linuxdo-related", "linuxdo"),
        ("wb", "weibo-hot", "weibo"),
        ("v2", "v2ex-node", "v2ex"),
        ("ig", "instagram-topic", "instagram"),
    ],
)
def test_source_family_aliases(platform: str, source: str, expected: str) -> None:
    assert source_family(source, platform) == expected


def test_registry_contains_every_runtime_platform() -> None:
    assert CANONICAL_SOURCE_FAMILIES == (
        "bilibili",
        "xiaohongshu",
        "douyin",
        "youtube",
        "twitter",
        "zhihu",
        "reddit",
        "bangumi",
        "linuxdo",
        "v2ex",
        "weibo",
        "instagram",
    )


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.bilibili.com/video/BV1abc", "bilibili"),
        ("https://www.xiaohongshu.com/explore/a", "xiaohongshu"),
        ("https://www.douyin.com/video/1", "douyin"),
        ("https://youtu.be/abc", "youtube"),
        ("https://x.com/user/status/1", "twitter"),
        ("https://www.zhihu.com/question/1/answer/2", "zhihu"),
        ("https://www.reddit.com/r/python/comments/a/title", "reddit"),
        ("https://bgm.tv/subject/326", "bangumi"),
        ("https://bangumi.tv/subject/326", "bangumi"),
        ("https://linux.do/t/topic/123", "linuxdo"),
        ("https://m.weibo.cn/detail/5023456789012345", "weibo"),
        ("https://www.v2ex.com/t/123456", "v2ex"),
        ("https://www.instagram.com/p/DAb_cd-123/", "instagram"),
    ],
)
def test_url_inference_uses_registry(url: str, expected: str) -> None:
    assert infer_source_platform_from_url(url) == expected


@pytest.mark.parametrize(
    "strategy",
    [
        *ZHIHU_SOURCE_STRATEGIES.values(),
        *ZHIHU_DISCOVERY_SCOPE_STRATEGIES.values(),
    ],
)
def test_every_zhihu_strategy_resolves_without_platform(strategy: str) -> None:
    assert source_family(strategy) == "zhihu"


def test_zhihu_strategy_overrides_bilibili_cache_default() -> None:
    assert source_family("zhihu-hot", "bilibili") == "zhihu"


@pytest.mark.parametrize(
    "strategy",
    [
        *LINUXDO_SOURCE_STRATEGIES.values(),
        *LINUXDO_DISCOVERY_SCOPE_STRATEGIES.values(),
    ],
)
def test_every_linuxdo_strategy_resolves_without_platform(strategy: str) -> None:
    assert source_family(strategy) == "linuxdo"


def test_url_inference_does_not_match_registered_host_in_path() -> None:
    url = "https://example.com/https://www.zhihu.com/question/1"
    assert infer_source_platform_from_url(url) == ""
