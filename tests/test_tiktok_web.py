"""Tests for the signed TikTok web API backend (all transport mocked)."""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from openbiliclaw.sources.tiktok_web import (
    TiktokRouterClient,
    TiktokWebClient,
    TiktokWebIdentity,
    TiktokWebResponse,
    parse_tiktok_item,
)


class _FakeCookies(dict):
    def set(self, name: str, value: str, domain: str = "") -> None:  # noqa: ARG002
        self[name] = value


class _FakeHttpResponse:
    def __init__(
        self,
        body: bytes = b"",
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        set_cookies: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status
        self.content = body
        self.headers = headers or {}
        self.set_cookies = set_cookies or {}


class _FakeSession:
    """Queue-driven stand-in for a curl_cffi Session."""

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.cookies = _FakeCookies()
        self.queue: list[Any] = []
        self.urls: list[str] = []

    def get(self, url: str, impersonate: str = "", timeout: float = 0.0) -> Any:
        assert impersonate == "chrome"
        self.urls.append(url)
        item = self.queue.pop(0) if self.queue else _FakeHttpResponse(b"{}")
        if isinstance(item, Exception):
            raise item
        for name, value in getattr(item, "set_cookies", {}).items():
            self.cookies[name] = value
        return item


def _json_body(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload).encode()


def _bootstrap_response() -> _FakeHttpResponse:
    return _FakeHttpResponse(
        _json_body({"itemList": []}),
        set_cookies={"msToken": "real-minted-token"},
    )


def _identity(session: _FakeSession, **overrides: Any) -> TiktokWebIdentity:
    kwargs: dict[str, Any] = {"session_factory": lambda: session, "max_attempts": 3}
    kwargs.update(overrides)
    return TiktokWebIdentity(**kwargs)


def _client(session: _FakeSession, **overrides: Any) -> TiktokWebClient:
    return TiktokWebClient(identity=_identity(session, **overrides))


def _item_struct(video_id: str = "7234567890123456789", **overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": video_id,
        "desc": "a useful tiktok #diy",
        "createTime": "1751900000",
        "author": {"uniqueId": "creator_handle", "nickname": "Creator Nick"},
        "stats": {
            "playCount": 12345,
            "diggCount": 678,
            "commentCount": 90,
            "shareCount": 12,
            "collectCount": 45,
        },
        "video": {"duration": 34000, "cover": "https://p16-sign.tiktokcdn.com/cover.jpg"},
    }
    item.update(overrides)
    return item


# ---------------------------------------------------------------------------
# itemStruct parsing
# ---------------------------------------------------------------------------


def test_parse_item_struct_maps_all_fields() -> None:
    content = parse_tiktok_item(_item_struct(), source_strategy="tiktok_feed")

    assert content is not None
    assert content.content_id == "7234567890123456789"
    assert content.item_key == "tiktok:7234567890123456789"
    assert content.source_platform == "tiktok"
    assert content.content_url == (
        "https://www.tiktok.com/@creator_handle/video/7234567890123456789"
    )
    assert content.title == "a useful tiktok #diy"
    assert content.author_name == "Creator Nick"
    assert content.cover_url == "https://p16-sign.tiktokcdn.com/cover.jpg"
    # video.duration is milliseconds in the web API: 34000ms → 34s.
    assert content.duration == 34
    assert content.view_count == 12345
    assert content.like_count == 678
    assert content.comment_count == 90
    assert content.share_count == 12
    assert content.collect_count == 45
    # createTime is epoch seconds.
    assert content.published_at.startswith("2025-")
    assert content.source_strategy == "tiktok_feed"
    assert content.source_metadata == {"tiktok_aweme_id": "7234567890123456789"}


def test_parse_item_struct_prefers_share_url_and_handles_missing_stats() -> None:
    content = parse_tiktok_item(
        _item_struct(
            shareUrl="https://www.tiktok.com/@creator_handle/video/7234567890123456789?_r=1",
            stats=None,
            video={"duration": 12},
            author={},
        )
    )

    assert content is not None
    assert content.content_url.endswith("?_r=1")
    assert content.duration == 12
    assert content.view_count == 0
    # No author → URL falls back to a generic owner segment.
    assert content.author_name == ""


def test_parse_item_struct_drops_missing_identity_or_desc() -> None:
    assert parse_tiktok_item(_item_struct(id="")) is None
    assert parse_tiktok_item(_item_struct(desc="")) is None
    assert parse_tiktok_item("not-a-dict") is None


# ---------------------------------------------------------------------------
# Identity: bootstrap, msToken, retries, cookie injection
# ---------------------------------------------------------------------------


def test_bootstrap_mints_real_ms_token() -> None:
    session = _FakeSession()
    session.queue.append(_bootstrap_response())
    identity = _identity(session)

    assert identity.ensure_bootstrapped() is True
    assert identity.ms_token() == "real-minted-token"
    # Second call is a no-op (already bootstrapped).
    assert identity.ensure_bootstrapped() is True
    assert len(session.urls) == 1
    assert "/api/recommend/item_list/" in session.urls[0]


def test_bootstrap_failure_on_gated_or_transport_error() -> None:
    session = _FakeSession()
    session.queue.append(
        _FakeHttpResponse(b"", headers={"tt_orcas_res": "1"}),
    )
    identity = _identity(session)
    assert identity.ensure_bootstrapped() is False

    session2 = _FakeSession()
    session2.queue.extend([ConnectionError("tls reset")] * 3)
    identity2 = _identity(session2)
    assert identity2.ensure_bootstrapped() is False
    assert len(session2.urls) == 3  # retried up to max_attempts


def test_signed_get_retries_transport_errors_then_succeeds() -> None:
    session = _FakeSession()
    session.queue.extend(
        [
            ConnectionError("reset 1"),
            ConnectionError("reset 2"),
            _FakeHttpResponse(b"{}"),
        ]
    )
    identity = _identity(session)

    response = identity.signed_get("/api/recommend/item_list/", identity.base_params("fyp"))

    assert response.status_code == 200
    assert len(session.urls) == 3
    # The signed URL carries the four signature parameters.
    assert "X-Dynosaur=" in session.urls[-1]
    assert "X-Gnarly=" in session.urls[-1]
    assert "X-Bogus=1" in session.urls[-1]
    assert "msToken=" in session.urls[-1]


def test_signed_get_signs_with_exact_user_agent_and_ms_token() -> None:
    session = _FakeSession()
    session.cookies["msToken"] = "real-minted-token"
    session.queue.append(_FakeHttpResponse(b"{}"))
    identity = _identity(session, user_agent="UA-EXACT/1.0")

    identity.signed_get("/api/user/detail/", identity.base_params("user"))

    url = session.urls[-1]
    # browser_version must equal the UA actually sent (possibly encoded).
    assert "UA-EXACT" in url
    assert "msToken=real-minted-token" in url


def test_signed_get_exhausts_attempts_and_raises() -> None:
    from openbiliclaw.sources.tiktok_web import TiktokWebTransportError

    session = _FakeSession()
    session.queue.extend([ConnectionError("reset")] * 3)
    identity = _identity(session)

    with pytest.raises(TiktokWebTransportError):
        identity.signed_get("/api/user/detail/", identity.base_params("user"))
    assert len(session.urls) == 3


def test_login_cookie_loaded_into_session_jar() -> None:
    session = _FakeSession()
    identity = _identity(session, login_cookie="sessionid=abc123; tt_csrf_token=xyz")

    identity.signed_get("/api/recommend/item_list/", identity.base_params("fyp"))

    assert session.cookies["sessionid"] == "abc123"
    assert session.cookies["tt_csrf_token"] == "xyz"


def test_response_gated_detection() -> None:
    assert TiktokWebResponse(200, b"", {"tt_orcas_res": "1"}).gated is True
    assert TiktokWebResponse(200, b"{}", {"tt_orcas_res": "1"}).gated is False
    assert TiktokWebResponse(200, b"", {}).gated is False


# ---------------------------------------------------------------------------
# Client: happy paths, gating, caches
# ---------------------------------------------------------------------------


async def test_get_feed_parses_items_after_bootstrap() -> None:
    session = _FakeSession()
    session.queue.extend(
        [
            _bootstrap_response(),
            _FakeHttpResponse(_json_body({"itemList": [_item_struct(), _item_struct("2")]})),
        ]
    )
    client = _client(session)

    items = await client.get_feed(limit=8)

    assert items is not None
    assert [item.content_id for item in items] == ["7234567890123456789", "2"]
    assert all(item.source_platform == "tiktok" for item in items)


async def test_gated_response_triggers_one_rebootstrap_then_recovers() -> None:
    session = _FakeSession()
    session.queue.extend(
        [
            _bootstrap_response(),
            _FakeHttpResponse(b"", headers={"tt_orcas_res": "1"}),  # first attempt gated
            _bootstrap_response(),  # re-bootstrap
            _FakeHttpResponse(_json_body({"itemList": [_item_struct()]})),
        ]
    )
    client = _client(session)

    items = await client.get_feed(limit=8)

    assert items is not None
    assert len(items) == 1
    # bootstrap + gated + re-bootstrap + retry
    assert len(session.urls) == 4


async def test_still_gated_after_rebootstrap_returns_none() -> None:
    session = _FakeSession()
    session.queue.extend(
        [
            _bootstrap_response(),
            _FakeHttpResponse(b"", headers={"tt_orcas_res": "1"}),
            _bootstrap_response(),
            _FakeHttpResponse(b"", headers={"tt_orcas_res": "1"}),
        ]
    )
    client = _client(session)

    assert await client.get_feed(limit=8) is None


async def test_bootstrap_failure_returns_none_not_empty() -> None:
    session = _FakeSession()
    session.queue.extend([ConnectionError("reset")] * 3)
    client = _client(session)

    assert await client.get_feed(limit=8) is None


async def test_get_user_videos_resolves_sec_uid_and_caches_it() -> None:
    session = _FakeSession()
    session.queue.extend(
        [
            _bootstrap_response(),
            _FakeHttpResponse(
                _json_body({"userInfo": {"user": {"secUid": "SEC", "nickname": "N"}}})
            ),
            _FakeHttpResponse(_json_body({"itemList": [_item_struct()]})),
            # Second call: no user/detail again, straight to the post list.
            _FakeHttpResponse(_json_body({"itemList": [_item_struct("2")]})),
        ]
    )
    client = _client(session)

    first = await client.get_user_videos("@creator", limit=5)
    second = await client.get_user_videos("creator", limit=5)

    assert first is not None and [c.content_id for c in first] == ["7234567890123456789"]
    assert second is not None and [c.content_id for c in second] == ["2"]
    detail_calls = [url for url in session.urls if "/api/user/detail/" in url]
    post_calls = [url for url in session.urls if "/api/post/item_list/" in url]
    assert len(detail_calls) == 1
    assert len(post_calls) == 2
    assert "secUid=SEC" in post_calls[0]


async def test_get_user_videos_unknown_user_returns_empty() -> None:
    session = _FakeSession()
    session.queue.extend(
        [
            _bootstrap_response(),
            _FakeHttpResponse(_json_body({"userInfo": {}})),
        ]
    )
    client = _client(session)

    assert await client.get_user_videos("@ghost", limit=5) == []
    assert await client.get_user_videos("not a handle", limit=5) == []


async def test_get_tag_videos_resolves_challenge_id_and_caches_it() -> None:
    session = _FakeSession()
    session.queue.extend(
        [
            _bootstrap_response(),
            _FakeHttpResponse(
                _json_body({"challengeInfo": {"challenge": {"id": "CID", "title": "cat"}}})
            ),
            _FakeHttpResponse(_json_body({"itemList": [_item_struct()]})),
            _FakeHttpResponse(_json_body({"itemList": [_item_struct("2")]})),
        ]
    )
    client = _client(session)

    first = await client.get_tag_videos("#cat", limit=10)
    second = await client.get_tag_videos("cat", limit=10)

    assert first is not None and len(first) == 1
    assert second is not None and [c.content_id for c in second] == ["2"]
    detail_calls = [url for url in session.urls if "/api/challenge/detail/" in url]
    list_calls = [url for url in session.urls if "/api/challenge/item_list/" in url]
    assert len(detail_calls) == 1
    assert len(list_calls) == 2
    assert "challengeID=CID" in list_calls[0]


async def test_search_gated_returns_empty_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = _FakeSession()
    session.queue.extend(
        [
            _bootstrap_response(),
            _FakeHttpResponse(b"", headers={"tt_orcas_res": "1"}),
            _bootstrap_response(),
            _FakeHttpResponse(b"", headers={"tt_orcas_res": "1"}),
        ]
    )
    client = _client(session)

    with caplog.at_level(logging.WARNING):
        results = await client.search_videos("cat", limit=10)

    # Search degrades to [] (never a backend-down signal): guest identities
    # are gated upstream and there is no yt-dlp search fallback.
    assert results == []
    assert any("login" in record.getMessage() for record in caplog.records)


async def test_get_author_profile_returns_user_info() -> None:
    session = _FakeSession()
    session.queue.extend(
        [
            _bootstrap_response(),
            _FakeHttpResponse(_json_body({"userInfo": {"user": {"secUid": "SEC"}, "stats": {}}})),
        ]
    )
    client = _client(session)

    profile = await client.get_author_profile("tiktok")

    assert profile == {"user": {"secUid": "SEC"}, "stats": {}}
    assert await client.get_author_profile("not a handle") == {}


# ---------------------------------------------------------------------------
# Router: mode dispatch and fallback
# ---------------------------------------------------------------------------


class _StubWeb:
    def __init__(self, results: list[Any]) -> None:
        self.results = list(results)
        self.feed_calls = 0
        self.tag_calls = 0
        self.user_calls = 0

    async def get_feed(self, *, limit: int = 12) -> Any:
        self.feed_calls += 1
        return self.results.pop(0) if self.results else []

    async def get_tag_videos(self, tag: str, *, limit: int = 15) -> Any:
        self.tag_calls += 1
        return self.results.pop(0) if self.results else []

    async def get_user_videos(self, handle: str, *, limit: int = 10) -> Any:
        self.user_calls += 1
        return self.results.pop(0) if self.results else []


class _StubYtdlp:
    def __init__(self) -> None:
        self.tag_calls = 0
        self.user_calls = 0

    async def get_tag_videos(self, tag: str, *, limit: int = 15) -> list[dict[str, Any]]:
        self.tag_calls += 1
        return [{"id": "ytdlp-tag"}]

    async def get_user_videos(self, handle: str, *, limit: int = 10) -> list[dict[str, Any]]:
        self.user_calls += 1
        return [{"id": "ytdlp-user"}]


async def test_router_auto_falls_back_to_ytdlp_on_backend_failure() -> None:
    web = _StubWeb([None])
    ytdlp = _StubYtdlp()
    router = TiktokRouterClient(web=web, ytdlp=ytdlp, mode="auto")  # type: ignore[arg-type]

    result = await router.get_tag_videos("cat", limit=5)

    assert result == [{"id": "ytdlp-tag"}]
    assert (web.tag_calls, ytdlp.tag_calls) == (1, 1)


async def test_router_auto_empty_web_result_does_not_fall_back() -> None:
    web = _StubWeb([[]])
    ytdlp = _StubYtdlp()
    router = TiktokRouterClient(web=web, ytdlp=ytdlp, mode="auto")  # type: ignore[arg-type]

    assert await router.get_tag_videos("cat", limit=5) == []
    assert ytdlp.tag_calls == 0


async def test_router_web_mode_never_falls_back() -> None:
    web = _StubWeb([None])
    ytdlp = _StubYtdlp()
    router = TiktokRouterClient(web=web, ytdlp=ytdlp, mode="web")  # type: ignore[arg-type]

    assert await router.get_user_videos("creator", limit=5) == []
    assert ytdlp.user_calls == 0


async def test_router_ytdlp_mode_never_touches_web() -> None:
    web = _StubWeb([[]])
    ytdlp = _StubYtdlp()
    router = TiktokRouterClient(web=web, ytdlp=ytdlp, mode="ytdlp")  # type: ignore[arg-type]

    assert await router.get_tag_videos("cat", limit=5) == [{"id": "ytdlp-tag"}]
    assert await router.get_feed(limit=5) is None
    assert (web.tag_calls, web.feed_calls) == (0, 0)


async def test_router_parks_web_after_consecutive_failures() -> None:
    web = _StubWeb([None, None, None, []])
    ytdlp = _StubYtdlp()
    router = TiktokRouterClient(web=web, ytdlp=ytdlp, mode="auto", max_web_failures=3)  # type: ignore[arg-type]

    await router.get_tag_videos("a", limit=5)
    await router.get_tag_videos("b", limit=5)
    await router.get_tag_videos("c", limit=5)
    # Parked: the fourth call goes straight to yt-dlp without asking the web.
    await router.get_tag_videos("d", limit=5)

    assert web.tag_calls == 3
    assert ytdlp.tag_calls == 4


async def test_router_success_resets_failure_counter() -> None:
    web = _StubWeb([None, [], None])
    ytdlp = _StubYtdlp()
    router = TiktokRouterClient(web=web, ytdlp=ytdlp, mode="auto", max_web_failures=2)  # type: ignore[arg-type]

    await router.get_tag_videos("a", limit=5)  # failure 1
    await router.get_tag_videos("b", limit=5)  # success → reset
    await router.get_tag_videos("c", limit=5)  # failure 1 again, not parked

    assert web.tag_calls == 3
    assert ytdlp.tag_calls == 2


# ---------------------------------------------------------------------------
# Optional-credential auth contract
# ---------------------------------------------------------------------------


def _auth_ctx(tmp_path: Any) -> Any:
    from openbiliclaw.api.source_auth.providers import SourceAuthContext
    from openbiliclaw.config import Config

    cfg = Config()
    cfg.data_dir = str(tmp_path)
    return SourceAuthContext(cfg=cfg, database=None)


def test_auth_tiktok_guest_mode_needs_no_credential(tmp_path: Any, monkeypatch: Any) -> None:
    from openbiliclaw.api.source_auth.providers import auth_tiktok

    monkeypatch.delenv("OPENBILICLAW_TIKTOK_COOKIE", raising=False)
    contract = auth_tiktok(_auth_ctx(tmp_path))

    assert contract.auth_required is False
    assert contract.credential == "none"
    assert contract.credential_origin == "none"
    assert contract.verify_method == "none"
    assert contract.legacy_state == "no_auth"
    assert contract.legacy_logged_in is True


def test_auth_tiktok_env_cookie_is_optional_present_credential(
    tmp_path: Any, monkeypatch: Any
) -> None:
    from openbiliclaw.api.source_auth.providers import auth_tiktok

    monkeypatch.setenv("OPENBILICLAW_TIKTOK_COOKIE", "sessionid=abc")
    contract = auth_tiktok(_auth_ctx(tmp_path))

    # Optional credential: present but never required, and honestly
    # verify_method="none" — no TikTok cookie probe exists yet.
    assert contract.auth_required is False
    assert contract.credential == "present"
    assert contract.credential_origin == "env"
    assert contract.verify_method == "none"
    assert contract.can_verify_now is False
    assert contract.legacy_state == "no_auth"


def test_auth_tiktok_data_file_cookie_origin(tmp_path: Any, monkeypatch: Any) -> None:
    from openbiliclaw.api.source_auth.providers import auth_tiktok
    from openbiliclaw.sources.tiktok_auth import TiktokCookieManager

    monkeypatch.delenv("OPENBILICLAW_TIKTOK_COOKIE", raising=False)
    TiktokCookieManager(tmp_path).set_cookie("sessionid=file-cookie", source="test")

    contract = auth_tiktok(_auth_ctx(tmp_path))

    assert contract.credential == "present"
    assert contract.credential_origin == "data_file"


# ---------------------------------------------------------------------------
# Cookie resolution helpers
# ---------------------------------------------------------------------------


def test_resolve_tiktok_cookie_env_first_then_data_file(tmp_path: Any, monkeypatch: Any) -> None:
    from openbiliclaw.sources.tiktok_auth import (
        TiktokCookieManager,
        resolve_tiktok_cookie,
    )

    monkeypatch.delenv("OPENBILICLAW_TIKTOK_COOKIE", raising=False)
    # No cookie anywhere is a legitimate guest-mode state, not an error.
    assert resolve_tiktok_cookie(data_dir=tmp_path) == ""

    TiktokCookieManager(tmp_path).set_cookie("sessionid=file", source="test")
    assert resolve_tiktok_cookie(data_dir=tmp_path) == "sessionid=file"

    monkeypatch.setenv("OPENBILICLAW_TIKTOK_COOKIE", "sessionid=env")
    assert resolve_tiktok_cookie(data_dir=tmp_path) == "sessionid=env"
