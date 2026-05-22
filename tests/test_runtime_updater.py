from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx
import pytest

import openbiliclaw
from openbiliclaw.runtime import updater

if TYPE_CHECKING:
    from collections.abc import Mapping


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("backend-v0.3.71", (0, 3, 71)),
        ("backend-v0.3.71-rc1", (0, 3, 71)),
        ("extension-v0.3.24", None),
        ("v0.3.71", (0, 3, 71)),
        ("0.3.71", (0, 3, 71)),
        ("backend-vfoo", None),
        ("", None),
    ],
)
def test_parse_backend_version_filters_non_backend_tags(
    tag: str,
    expected: tuple[int, ...] | None,
) -> None:
    assert updater._parse_backend_version(tag) == expected


class _FakeResponse:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        return self._payload


class _FakeAsyncClient:
    calls: list[tuple[str, dict[str, object] | None]] = []
    pages: dict[int, object] = {}
    error: Exception | None = None

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,  # noqa: ARG002
        params: dict[str, object] | None = None,
    ) -> _FakeResponse:
        self.calls.append((url, params))
        if self.error is not None:
            raise self.error
        page = int(params.get("page", 1)) if params else 1
        return _FakeResponse(200, self.pages.get(page, []))


@pytest.fixture(autouse=True)
def _reset_fake_client(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.pages = {}
    _FakeAsyncClient.error = None
    monkeypatch.setattr(updater.httpx, "AsyncClient", _FakeAsyncClient)


@pytest.mark.asyncio
async def test_fetch_latest_version_uses_tags_and_returns_highest_backend_tag() -> None:
    _FakeAsyncClient.pages = {
        1: [
            {"name": "extension-v0.3.24"},
            {"name": "backend-v0.3.71"},
            {"name": "backend-v0.3.69"},
        ],
    }

    service = updater.AutoUpdateService()
    result = await service._fetch_latest_version()

    assert result == "backend-v0.3.71"
    assert all("releases/latest" not in url for url, _params in _FakeAsyncClient.calls)


@pytest.mark.asyncio
async def test_fetch_latest_version_finds_backend_tag_on_later_page() -> None:
    _FakeAsyncClient.pages = {
        1: [{"name": "extension-v0.3.24"}],
        2: [{"name": "backend-v0.3.69"}],
        3: [],
    }

    service = updater.AutoUpdateService()

    assert await service._fetch_latest_version() == "backend-v0.3.69"


@pytest.mark.asyncio
async def test_fetch_latest_version_returns_empty_when_tags_have_only_extension_releases() -> None:
    _FakeAsyncClient.pages = {
        1: [{"name": "extension-v0.3.20"}, {"name": "extension-v0.3.24"}],
        2: [],
    }

    service = updater.AutoUpdateService()

    assert await service._fetch_latest_version() == ""


@pytest.mark.asyncio
async def test_fetch_latest_version_returns_empty_and_warns_on_http_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _FakeAsyncClient.error = httpx.ConnectError("network down")
    service = updater.AutoUpdateService()

    with caplog.at_level(logging.WARNING):
        assert await service._fetch_latest_version() == ""

    assert "Auto-update tag check failed" in caplog.text


@pytest.mark.asyncio
async def test_check_now_reports_no_backend_tag_for_extension_only_tags(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _FakeAsyncClient.pages = {
        1: [{"name": "extension-v0.3.20"}, {"name": "extension-v0.3.24"}],
        2: [],
    }
    monkeypatch.setattr(openbiliclaw, "__version__", "0.3.64")
    service = updater.AutoUpdateService()

    with caplog.at_level(logging.INFO):
        result = await service.check_and_update_now()

    assert result == {"checked": True, "updated": False, "reason": "no_backend_tag_yet"}
    assert "no_backend_tag_yet" in caplog.text
    assert "Already up-to-date" not in caplog.text
