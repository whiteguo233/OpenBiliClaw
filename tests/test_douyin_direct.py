"""Tests for Douyin direct-cookie discovery helpers."""

from __future__ import annotations

import httpx
import pytest

from openbiliclaw.sources.douyin_direct import (
    DouyinDirectAuthError,
    DouyinDirectClient,
    normalize_aweme_item,
    parse_cookie_header,
)


def test_normalize_aweme_item_maps_core_fields() -> None:
    item = {
        "aweme_id": "7123456789012345678",
        "desc": "一个测试视频",
        "author": {"nickname": "作者A", "sec_uid": "sec-1"},
        "video": {"cover": {"url_list": ["https://cover.example/a.jpg"]}, "duration": 12345},
        "statistics": {
            "digg_count": 88,
            "play_count": 999,
            "collect_count": 77,
            "comment_count": 66,
            "share_count": 55,
        },
    }

    content = normalize_aweme_item(item, source_strategy="dy-direct-search")

    assert content is not None
    assert content.bvid == "dy:7123456789012345678"
    assert content.content_id == "7123456789012345678"
    assert content.content_url == "https://www.douyin.com/video/7123456789012345678"
    assert content.source_platform == "douyin"
    assert content.source_strategy == "dy-direct-search"
    assert content.title == "一个测试视频"
    assert content.author_name == "作者A"
    assert content.up_name == "作者A"
    assert content.cover_url == "https://cover.example/a.jpg"
    assert content.duration == 12
    assert content.like_count == 88
    assert content.view_count == 999
    assert content.collect_count == 77
    assert content.comment_count == 66
    assert content.share_count == 55


def test_normalize_aweme_item_returns_none_without_aweme_id() -> None:
    assert normalize_aweme_item({"desc": "missing id"}, source_strategy="dy-direct-search") is None


def test_normalize_aweme_item_uses_fallback_fields() -> None:
    item = {
        "aweme_id": 9,
        "share_info": {"share_title": "分享标题"},
        "author": {"nickname": "作者B"},
        "video": {"origin_cover": {"url_list": ["https://cover.example/origin.jpg"]}},
        "stats": {"digg_count": "3", "play_count": "11", "collect_count": "5"},
    }

    content = normalize_aweme_item(item, source_strategy="dy-direct-hot")

    assert content is not None
    assert content.title == "分享标题"
    assert content.cover_url == "https://cover.example/origin.jpg"
    assert content.like_count == 3
    assert content.view_count == 11
    assert content.collect_count == 5


def test_parse_cookie_header_trims_pairs() -> None:
    assert parse_cookie_header(" msToken = abc ; ttwid=tw ; invalid ; empty= ") == {
        "msToken": "abc",
        "ttwid": "tw",
    }


@pytest.mark.asyncio
async def test_direct_client_rejects_missing_cookie() -> None:
    with pytest.raises(DouyinDirectAuthError):
        DouyinDirectClient(cookie="")


@pytest.mark.asyncio
async def test_direct_client_search_normalizes_aweme_info() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "/aweme/v1/web/general/search/single/" in str(request.url)
        assert "Cookie" in request.headers
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "aweme_info": {
                            "aweme_id": "1",
                            "desc": "搜索结果",
                            "author": {"nickname": "A"},
                        }
                    }
                ],
                "has_more": 0,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = DouyinDirectClient(cookie="msToken=t;", http_client=http_client)
        items = await client.search_aweme("测试", limit=10)

    assert items == [{"aweme_id": "1", "desc": "搜索结果", "author": {"nickname": "A"}}]


@pytest.mark.asyncio
async def test_direct_client_hot_board_extracts_aweme_entries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "/aweme/v1/web/hot/search/list/" in str(request.url)
        return httpx.Response(
            200,
            json={
                "data": {
                    "word_list": [
                        {
                            "word": "热点",
                            "aweme_info": {
                                "aweme_id": "2",
                                "desc": "热点视频",
                                "author": {"nickname": "B"},
                            },
                        }
                    ]
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = DouyinDirectClient(cookie="msToken=t;", http_client=http_client)
        items = await client.get_hot_board(limit=10)

    assert items == [{"aweme_id": "2", "desc": "热点视频", "author": {"nickname": "B"}}]


@pytest.mark.asyncio
async def test_direct_client_hot_terms_extracts_sentence_ids_without_awemes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "/aweme/v1/web/hot/search/list/" in str(request.url)
        return httpx.Response(
            200,
            json={
                "data": {
                    "word_list": [
                        {
                            "word": "热点词",
                            "sentence_id": "2495363",
                            "hot_value": 12345,
                        },
                        {
                            "word": "无 id",
                        },
                    ]
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = DouyinDirectClient(cookie="msToken=t;", http_client=http_client)
        terms = await client.get_hot_terms(limit=10)

    assert terms == [
        {
            "word": "热点词",
            "sentence_id": "2495363",
            "hot_value": 12345,
        }
    ]


@pytest.mark.asyncio
async def test_direct_client_creator_posts_extracts_aweme_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "/aweme/v1/web/aweme/post/" in str(request.url)
        assert "sec_user_id=sec-1" in str(request.url)
        return httpx.Response(
            200,
            json={
                "aweme_list": [{"aweme_id": "3", "desc": "作者视频", "author": {"nickname": "C"}}],
                "has_more": 0,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = DouyinDirectClient(cookie="msToken=t;", http_client=http_client)
        items = await client.get_creator_posts("sec-1", limit=10)

    assert items == [{"aweme_id": "3", "desc": "作者视频", "author": {"nickname": "C"}}]


@pytest.mark.asyncio
async def test_direct_client_network_error_returns_empty_items() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network unavailable", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = DouyinDirectClient(cookie="msToken=t;", http_client=http_client)
        items = await client.search_aweme("测试", limit=10)

    assert items == []
