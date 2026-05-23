"""Tests for Bilibili API helpers."""

from __future__ import annotations

import asyncio
from urllib.parse import quote

import httpx
import pytest

from openbiliclaw.bilibili.api import (
    BilibiliAPIClient,
    BilibiliAPIError,
    BilibiliAuthExpiredError,
    CommentInfo,
    FavoriteFolder,
    FavoriteFolderWithItems,
    FollowingUser,
)


@pytest.fixture(autouse=True)
def _reset_bilibili_search_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(BilibiliAPIClient, "_search_cooldown_until", 0.0)
    monkeypatch.setattr(BilibiliAPIClient, "_search_cooldown_level", 0)


class FakeResponse:
    """Minimal fake HTTP response."""

    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


class FakeAsyncClient:
    """Minimal fake async HTTP client."""

    def __init__(self, payload: dict[str, object] | list[dict[str, object]]) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict[str, object] | None, dict[str, str] | None]] = []

    async def get(
        self,
        url: str,
        params: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> FakeResponse:
        self.calls.append((url, params, headers))
        payload = self.payload.pop(0) if isinstance(self.payload, list) else self.payload
        return FakeResponse(payload)

    async def aclose(self) -> None:
        return None


class RouteAsyncClient:
    """Route-aware fake async HTTP client."""

    def __init__(self, routes: dict[str, list[dict[str, object]]]) -> None:
        self.routes = {key: value.copy() for key, value in routes.items()}
        self.calls: list[tuple[str, dict[str, object] | None, dict[str, str] | None]] = []

    async def get(
        self,
        url: str,
        params: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> FakeResponse:
        self.calls.append((url, params, headers))
        for path, payloads in self.routes.items():
            if url.endswith(path):
                return FakeResponse(payloads.pop(0))
        raise AssertionError(f"Unexpected URL: {url}")

    async def aclose(self) -> None:
        return None


class ErroringAsyncClient:
    """Fake async client that raises an HTTP status error."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.calls: list[tuple[str, dict[str, object] | None, dict[str, str] | None]] = []

    async def get(
        self,
        url: str,
        params: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> FakeResponse:
        self.calls.append((url, params, headers))
        request = httpx.Request("GET", url, params=params)
        response = httpx.Response(self.status_code, request=request)
        raise httpx.HTTPStatusError(
            f"Client error '{self.status_code} Precondition Failed'",
            request=request,
            response=response,
        )

    async def aclose(self) -> None:
        return None


class NavThenErrorAsyncClient:
    """Fake client that serves nav once, then raises on search."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.calls: list[tuple[str, dict[str, object] | None, dict[str, str] | None]] = []

    async def get(
        self,
        url: str,
        params: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> FakeResponse:
        self.calls.append((url, params, headers))
        if url.endswith("/x/web-interface/nav"):
            return FakeResponse(
                {
                    "code": 0,
                    "data": {
                        "wbi_img": {
                            "img_url": "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png",
                            "sub_url": "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png",
                        }
                    },
                }
            )

        request = httpx.Request("GET", url, params=params)
        response = httpx.Response(self.status_code, request=request)
        raise httpx.HTTPStatusError(
            f"Client error '{self.status_code} Precondition Failed'",
            request=request,
            response=response,
        )

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_get_nav_info_parses_login_payload() -> None:
    client = BilibiliAPIClient(cookie="SESSDATA=abc")
    fake_http = FakeAsyncClient(
        {
            "code": 0,
            "data": {
                "isLogin": True,
                "uname": "alice",
                "mid": 10086,
            },
        }
    )
    client._client = fake_http

    nav = await client.get_nav_info()

    assert nav.is_login is True
    assert nav.uname == "alice"
    assert nav.mid == 10086
    assert fake_http.calls[0][0].endswith("/x/web-interface/nav")


@pytest.mark.asyncio
async def test_get_nav_info_raises_on_nonzero_code() -> None:
    client = BilibiliAPIClient(cookie="SESSDATA=abc")
    client._client = FakeAsyncClient({"code": -101, "message": "账号未登录"})

    with pytest.raises(BilibiliAPIError, match="账号未登录"):
        await client.get_nav_info()


@pytest.mark.asyncio
async def test_get_nav_info_surfaces_session_expired_for_code_minus_101(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = BilibiliAPIClient(cookie="SESSDATA=expired")
    client._client = FakeAsyncClient({"code": -101, "message": "账号未登录"})
    caplog.set_level("WARNING", logger="openbiliclaw.bilibili.api")

    with pytest.raises(BilibiliAuthExpiredError, match="session expired.*-101"):
        await client.get_nav_info()

    assert "re-authenticate" in caplog.text


@pytest.mark.asyncio
async def test_get_user_history_uses_cursor_pagination() -> None:
    client = BilibiliAPIClient(cookie="SESSDATA=abc")
    client._client = FakeAsyncClient(
        [
            {
                "code": 0,
                "data": {
                    "list": [{"title": "v1"}, {"title": "v2"}],
                    "cursor": {"max": 111, "view_at": 222},
                },
            },
            {
                "code": 0,
                "data": {
                    "list": [{"title": "v3"}, {"title": "v4"}],
                    "cursor": {"max": 0, "view_at": 0},
                },
            },
        ]
    )

    history = await client.get_user_history(max_items=3)

    assert len(history) == 3
    assert client._client.calls[0][1] == {"type": "archive"}
    assert client._client.calls[1][1] == {
        "type": "archive",
        "max": 111,
        "view_at": 222,
    }


@pytest.mark.asyncio
async def test_search_passes_order_parameter() -> None:
    client = BilibiliAPIClient(cookie="SESSDATA=abc")
    client._client = RouteAsyncClient(
        {
            "/x/web-interface/nav": [
                {
                    "code": 0,
                    "data": {
                        "wbi_img": {
                            "img_url": "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png",
                            "sub_url": "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png",
                        }
                    },
                }
            ],
            "/x/web-interface/wbi/search/type": [{"code": 0, "data": {"result": []}}],
        }
    )

    await client.search("纪录片", page=2, page_size=10, order="pubdate")

    assert client._client.calls[1][1] is not None
    assert client._client.calls[1][1]["keyword"] == "纪录片"
    assert client._client.calls[1][1]["search_type"] == "video"
    assert client._client.calls[1][1]["page"] == "2"
    assert client._client.calls[1][1]["page_size"] == "10"
    assert client._client.calls[1][1]["order"] == "pubdate"


@pytest.mark.asyncio
async def test_search_uses_wbi_signed_endpoint_and_search_page_headers() -> None:
    client = BilibiliAPIClient(cookie="SESSDATA=abc")
    client._client = RouteAsyncClient(
        {
            "/x/web-interface/nav": [
                {
                    "code": -101,
                    "message": "账号未登录",
                    "data": {
                        "wbi_img": {
                            "img_url": "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png",
                            "sub_url": "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png",
                        }
                    },
                }
            ],
            "/x/web-interface/wbi/search/type": [{"code": 0, "data": {"result": []}}],
        }
    )

    await client.search("纪录片", page=1, page_size=10, order="totalrank")

    assert client._client.calls[1][0].endswith("/x/web-interface/wbi/search/type")
    assert client._client.calls[1][1] is not None
    assert client._client.calls[1][1]["keyword"] == "纪录片"
    assert client._client.calls[1][1]["search_type"] == "video"
    assert client._client.calls[1][1]["web_location"] == "1430654"
    assert "wts" in client._client.calls[1][1]
    assert "w_rid" in client._client.calls[1][1]
    assert client._client.calls[1][2] == {
        "Referer": f"https://search.bilibili.com/all?keyword={quote('纪录片', safe='')}",
        "Origin": "https://search.bilibili.com",
    }


@pytest.mark.asyncio
async def test_search_returns_empty_on_412() -> None:
    client = BilibiliAPIClient(cookie="SESSDATA=abc")
    client._client = NavThenErrorAsyncClient(status_code=412)

    results = await client.search("纪录片", page=1, page_size=10, order="totalrank")

    assert results == []


@pytest.mark.asyncio
async def test_search_enters_global_cooldown_after_v_voucher_storm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    monkeypatch.setattr(BilibiliAPIClient, "_search_cooldown_until", 0.0, raising=False)
    monkeypatch.setattr(BilibiliAPIClient, "_search_cooldown_level", 0, raising=False)
    nav_payload = {
        "code": 0,
        "data": {
            "wbi_img": {
                "img_url": "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png",
                "sub_url": "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png",
            }
        },
    }
    first_client = BilibiliAPIClient(cookie="SESSDATA=abc")
    first_client._client = RouteAsyncClient(
        {
            "/x/web-interface/nav": [nav_payload, nav_payload, nav_payload],
            "/x/web-interface/wbi/search/type": [
                {"code": 0, "data": {"v_voucher": "a", "result": None}},
                {"code": 0, "data": {"v_voucher": "b", "result": None}},
                {"code": 0, "data": {"v_voucher": "c", "result": None}},
            ],
        }
    )

    assert await first_client.search("纪录片") == []

    second_client = BilibiliAPIClient(cookie="SESSDATA=abc")
    second_http = RouteAsyncClient(
        {
            "/x/web-interface/nav": [nav_payload],
            "/x/web-interface/wbi/search/type": [{"code": 0, "data": {"result": []}}],
        }
    )
    second_client._client = second_http

    assert await second_client.search("摄影") == []
    assert second_http.calls == []


@pytest.mark.asyncio
async def test_history_request_awaits_rate_limit_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    client = BilibiliAPIClient(cookie="SESSDATA=abc")
    client._client = FakeAsyncClient(
        {"code": 0, "data": {"list": [], "cursor": {"max": 0, "view_at": 0}}}
    )
    calls: list[str] = []

    async def fake_rate_limit() -> None:
        calls.append("rate-limit")

    monkeypatch.setattr(client, "_respect_rate_limit", fake_rate_limit)

    await client.get_user_history(max_items=1)

    assert calls == ["rate-limit"]


@pytest.mark.asyncio
async def test_get_favorite_folders_parses_folder_metadata() -> None:
    client = BilibiliAPIClient(cookie="SESSDATA=abc")
    client._client = RouteAsyncClient(
        {
            "/x/web-interface/nav": [
                {"code": 0, "data": {"isLogin": True, "uname": "alice", "mid": 42}}
            ],
            "/x/v3/fav/folder/created/list-all": [
                {
                    "code": 0,
                    "data": {
                        "list": [
                            {"id": 1, "title": "纪录片", "media_count": 12},
                            {"id": 2, "title": "技术", "media_count": 6},
                        ]
                    },
                }
            ],
        }
    )

    folders = await client.get_favorite_folders()

    assert folders == [
        FavoriteFolder(media_id=1, title="纪录片", media_count=12),
        FavoriteFolder(media_id=2, title="技术", media_count=6),
    ]


@pytest.mark.asyncio
async def test_get_all_favorites_respects_budget_limits() -> None:
    client = BilibiliAPIClient(cookie="SESSDATA=abc")
    client._client = RouteAsyncClient(
        {
            "/x/web-interface/nav": [
                {"code": 0, "data": {"isLogin": True, "uname": "alice", "mid": 42}}
            ],
            "/x/v3/fav/folder/created/list-all": [
                {
                    "code": 0,
                    "data": {
                        "list": [
                            {"id": 1, "title": "纪录片", "media_count": 12},
                            {"id": 2, "title": "技术", "media_count": 6},
                        ]
                    },
                }
            ],
            "/x/v3/fav/resource/list": [
                {
                    "code": 0,
                    "data": {
                        "medias": [
                            {"title": "v1"},
                            {"title": "v2"},
                            {"title": "v3"},
                        ]
                    },
                }
            ],
        }
    )

    favorites = await client.get_all_favorites(max_folders=1, max_items_per_folder=2)

    assert favorites == [
        FavoriteFolderWithItems(
            folder=FavoriteFolder(media_id=1, title="纪录片", media_count=12),
            items=[{"title": "v1"}, {"title": "v2"}],
            truncated=True,
        )
    ]


@pytest.mark.asyncio
async def test_get_following_parses_users() -> None:
    client = BilibiliAPIClient(cookie="SESSDATA=abc")
    client._client = RouteAsyncClient(
        {
            "/x/web-interface/nav": [
                {"code": 0, "data": {"isLogin": True, "uname": "alice", "mid": 42}}
            ],
            "/x/relation/followings": [
                {
                    "code": 0,
                    "data": {
                        "list": [
                            {"mid": 1, "uname": "alice", "sign": "doc lover"},
                            {"mid": 2, "uname": "bob", "sign": "tech"},
                        ]
                    },
                }
            ],
        }
    )

    users = await client.get_following(page=1, page_size=2)

    assert users == [
        FollowingUser(mid=1, uname="alice", sign="doc lover"),
        FollowingUser(mid=2, uname="bob", sign="tech"),
    ]


@pytest.mark.asyncio
async def test_get_video_comments_returns_top_n_comments() -> None:
    client = BilibiliAPIClient(cookie="SESSDATA=abc")
    client._client = RouteAsyncClient(
        {
            "/x/web-interface/view": [{"code": 0, "data": {"aid": 123, "stat": {}, "owner": {}}}],
            "/x/v2/reply/main": [
                {
                    "code": 0,
                    "data": {
                        "replies": [
                            {
                                "mid": 1,
                                "member": {"uname": "alice"},
                                "content": {"message": "第一条"},
                                "like": 11,
                            },
                            {
                                "mid": 2,
                                "member": {"uname": "bob"},
                                "content": {"message": "第二条"},
                                "like": 7,
                            },
                        ]
                    },
                }
            ],
        }
    )

    comments = await client.get_video_comments("BV1xx", limit=1)

    assert comments == [
        CommentInfo(mid=1, uname="alice", message="第一条", like_count=11),
    ]


@pytest.mark.asyncio
async def test_get_ranking_returns_empty_list_when_data_is_null() -> None:
    """Regression: B站 ``ranking/v2`` may return ``"data": null`` for empty
    regions or under rate-limiting. The client must degrade to ``[]`` instead
    of raising ``AttributeError`` on ``None.get(...)``.
    """
    client = BilibiliAPIClient(cookie="SESSDATA=abc")
    client._client = FakeAsyncClient({"code": 0, "data": None})

    items = await client.get_ranking(rid=201)

    assert items == []


@pytest.mark.asyncio
async def test_get_video_info_returns_defaults_when_data_is_null() -> None:
    """Regression: ``/x/web-interface/view`` may return ``"data": null``
    (removed/region-locked videos or rate-limiting). The client must
    degrade to a ``VideoInfo`` with zero/empty defaults instead of raising
    ``KeyError`` on hard indexing.
    """
    client = BilibiliAPIClient(cookie="SESSDATA=abc")
    client._client = FakeAsyncClient({"code": 0, "data": None})

    info = await client.get_video_info("BV1xx")

    assert info.bvid == "BV1xx"
    assert info.aid == 0
    assert info.title == ""
    assert info.up_name == ""
    assert info.view_count == 0
