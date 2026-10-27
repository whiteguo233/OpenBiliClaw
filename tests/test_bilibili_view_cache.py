"""Tests for the process-wide /view payload cache on BilibiliAPIClient."""

from __future__ import annotations

import httpx
import pytest

from openbiliclaw.bilibili.api import BilibiliAPIClient, BilibiliAPIError


def _view_payload(bvid: str, *, aid: int = 1) -> dict[str, object]:
    return {
        "code": 0,
        "data": {"bvid": bvid, "aid": aid, "cid": 10, "title": f"title-{bvid}"},
    }


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


class _CountingViewClient:
    """Fake transport that counts /view requests and can fail on demand."""

    def __init__(self, bvid: str, *, fail_first: bool = False) -> None:
        self.payload = _view_payload(bvid)
        self.fail_first = fail_first
        self.calls = 0

    async def get(self, url: str, params: object = None, headers: object = None) -> _FakeResponse:
        self.calls += 1
        if self.fail_first:
            self.fail_first = False
            raise httpx.ConnectError("boom")
        return _FakeResponse(self.payload)

    async def aclose(self) -> None:
        return None


async def test_same_bvid_fetches_view_data_once() -> None:
    client = BilibiliAPIClient()
    transport = _CountingViewClient("BV1aa")
    client._client = transport  # type: ignore[assignment]

    first = await client.get_video_info("BV1aa")
    second = await client.get_video_info("BV1aa")

    assert transport.calls == 1
    assert first.title == second.title == "title-BV1aa"


async def test_different_bvids_fetch_separately() -> None:
    client = BilibiliAPIClient()
    transport = _CountingViewClient("BV1aa")
    client._client = transport  # type: ignore[assignment]

    await client.get_video_view_data("BV1aa")
    await client.get_video_view_data("BV1bb")

    assert transport.calls == 2


async def test_cache_scoped_by_login_identity() -> None:
    anon = BilibiliAPIClient()
    anon_transport = _CountingViewClient("BV1aa")
    anon._client = anon_transport  # type: ignore[assignment]
    authed = BilibiliAPIClient(cookie="SESSDATA=secret")
    authed_transport = _CountingViewClient("BV1aa")
    authed._client = authed_transport  # type: ignore[assignment]

    await anon.get_video_view_data("BV1aa")
    await authed.get_video_view_data("BV1aa")

    assert anon_transport.calls == 1
    assert authed_transport.calls == 1


async def test_expired_entry_refetched(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(BilibiliAPIClient, "_VIEW_DATA_CACHE_TTL_SECONDS", 0.0)
    client = BilibiliAPIClient()
    transport = _CountingViewClient("BV1aa")
    client._client = transport  # type: ignore[assignment]

    await client.get_video_view_data("BV1aa")
    await client.get_video_view_data("BV1aa")

    assert transport.calls == 2


async def test_failed_fetch_not_cached() -> None:
    client = BilibiliAPIClient()
    transport = _CountingViewClient("BV1aa", fail_first=True)
    client._client = transport  # type: ignore[assignment]

    with pytest.raises(BilibiliAPIError):
        await client.get_video_view_data("BV1aa")
    data = await client.get_video_view_data("BV1aa")

    assert transport.calls == 2
    assert data["bvid"] == "BV1aa"


async def test_lru_eviction_removes_least_recently_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(BilibiliAPIClient, "_VIEW_DATA_CACHE_MAX_ENTRIES", 2)
    client = BilibiliAPIClient()
    transport = _CountingViewClient("BV1aa")
    client._client = transport  # type: ignore[assignment]

    await client.get_video_view_data("BV1aa")  # cache: A
    await client.get_video_view_data("BV1bb")  # cache: A, B
    await client.get_video_view_data("BV1aa")  # hit; cache order: B, A
    await client.get_video_view_data("BV1cc")  # evicts B; cache: A, C
    await client.get_video_view_data("BV1bb")  # miss → refetch

    assert transport.calls == 4
