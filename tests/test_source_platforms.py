"""Tests for canonical source-platform family rules."""

import pytest

from openbiliclaw.runtime.linuxdo_producer import LINUXDO_SOURCE_STRATEGIES
from openbiliclaw.runtime.zhihu_producer import ZHIHU_SOURCE_STRATEGIES
from openbiliclaw.sources.linuxdo_tasks import LINUXDO_DISCOVERY_SCOPE_STRATEGIES
from openbiliclaw.sources.platforms import (
    CANONICAL_SOURCE_FAMILIES,
    SOURCE_CONFIDENCE_EXACT,
    SOURCE_CONFIDENCE_INFERRED,
    SOURCE_CONFIDENCE_LEGACY_UNKNOWN,
    constrain_source_confidence,
    extract_source_content_id,
    infer_source_platform_from_url,
    resolve_source_attribution,
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
        ("dy", "douyin_search", "douyin"),
        ("tiktok", "tiktok_tag", "tiktok"),
        ("tt", "tiktok-user", "tiktok"),
        ("", "tiktok_user", "tiktok"),
        ("yt", "yt-search", "youtube"),
        ("x", "x-feed", "twitter"),
        ("gh", "github-search", "github"),
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
        "tiktok",
        "twitter",
        "github",
        "zhihu",
        "reddit",
        "bangumi",
        "linuxdo",
        "v2ex",
        "weibo",
    )


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.bilibili.com/video/BV1abc", "bilibili"),
        ("https://www.xiaohongshu.com/explore/a", "xiaohongshu"),
        ("https://www.douyin.com/video/1", "douyin"),
        ("https://www.tiktok.com/@creator/video/7234567890123456789", "tiktok"),
        ("https://vm.tiktok.com/ZNabcde/", "tiktok"),
        ("https://youtu.be/abc", "youtube"),
        ("https://x.com/user/status/1", "twitter"),
        ("https://github.com/openai/openai-python", "github"),
        ("https://www.zhihu.com/question/1/answer/2", "zhihu"),
        ("https://www.reddit.com/r/python/comments/a/title", "reddit"),
        ("https://bgm.tv/subject/326", "bangumi"),
        ("https://bangumi.tv/subject/326", "bangumi"),
        ("https://linux.do/t/topic/123", "linuxdo"),
        ("https://m.weibo.cn/detail/5023456789012345", "weibo"),
        ("https://www.v2ex.com/t/123456", "v2ex"),
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


def test_source_attribution_prefers_explicit_metadata_then_url() -> None:
    assert resolve_source_attribution(
        explicit_platform="x",
        metadata_platform="youtube",
        url="https://www.bilibili.com/video/BV1",
    ) == ("twitter", SOURCE_CONFIDENCE_EXACT)
    assert resolve_source_attribution(
        metadata_platform="yt",
        url="https://www.bilibili.com/video/BV1",
    ) == ("youtube", SOURCE_CONFIDENCE_EXACT)
    assert resolve_source_attribution(url="https://x.com/user/status/1") == (
        "twitter",
        SOURCE_CONFIDENCE_INFERRED,
    )
    assert resolve_source_attribution(legacy_platform="bilibili") == (
        "bilibili",
        SOURCE_CONFIDENCE_LEGACY_UNKNOWN,
    )


def test_source_attribution_keeps_unknown_slug_but_not_exact() -> None:
    assert resolve_source_attribution(explicit_platform="threads") == (
        "threads",
        SOURCE_CONFIDENCE_LEGACY_UNKNOWN,
    )
    assert resolve_source_attribution(
        explicit_platform="threads",
        metadata_platform="youtube",
        url="https://www.bilibili.com/video/BV1",
    ) == ("threads", SOURCE_CONFIDENCE_LEGACY_UNKNOWN)


def test_constrain_source_confidence_never_upgrades_evidence() -> None:
    assert (
        constrain_source_confidence(
            SOURCE_CONFIDENCE_EXACT,
            SOURCE_CONFIDENCE_INFERRED,
        )
        == SOURCE_CONFIDENCE_INFERRED
    )
    assert (
        constrain_source_confidence(
            SOURCE_CONFIDENCE_LEGACY_UNKNOWN,
            SOURCE_CONFIDENCE_EXACT,
        )
        == SOURCE_CONFIDENCE_LEGACY_UNKNOWN
    )
    assert constrain_source_confidence("", SOURCE_CONFIDENCE_INFERRED) == SOURCE_CONFIDENCE_INFERRED
    assert (
        constrain_source_confidence("invalid", SOURCE_CONFIDENCE_EXACT) == SOURCE_CONFIDENCE_EXACT
    )


def test_source_content_id_extraction_uses_stable_metadata_keys() -> None:
    assert extract_source_content_id({"note_id": "", "content_id": "note-42"}) == "note-42"
    assert extract_source_content_id({"content_id": "topic:4242", "topic_id": 4242}) == "topic:4242"
    assert extract_source_content_id({"topic_id": 4242}) == "4242"
    assert extract_source_content_id({"bvid": "BV1TEST", "title": "视频"}) == "BV1TEST"
    assert extract_source_content_id({"content_id": None}) == ""
    assert extract_source_content_id({"repository_id": 307213173}) == "307213173"
