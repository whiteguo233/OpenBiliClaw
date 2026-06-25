"""Tests for Bilibili cookie authentication management."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from openbiliclaw.bilibili.api import BilibiliAPIError, NavInfo
from openbiliclaw.bilibili.auth import AuthManager, resolve_runtime_cookie


class FakeNavClient:
    """Minimal fake nav client for auth tests."""

    def __init__(
        self,
        *,
        nav_info: NavInfo | None = None,
        error: Exception | None = None,
    ) -> None:
        self.nav_info = nav_info
        self.error = error
        self.closed = False

    async def get_nav_info(self) -> NavInfo:
        if self.error is not None:
            raise self.error
        return self.nav_info or NavInfo(is_login=False)

    async def close(self) -> None:
        self.closed = True


def test_auth_manager_persists_and_loads_cookie(tmp_path: Path) -> None:
    manager = AuthManager(tmp_path)

    manager.set_cookie("  SESSDATA=abc123; bili_jct=xyz  ")

    reloaded = AuthManager(tmp_path)
    assert reloaded.load_cookie() == "SESSDATA=abc123; bili_jct=xyz"


@pytest.mark.asyncio
async def test_validate_cookie_returns_authenticated_status(tmp_path: Path) -> None:
    fake_client = FakeNavClient(
        nav_info=NavInfo(is_login=True, uname="alice", mid=10086),
    )
    manager = AuthManager(
        tmp_path,
        api_client_factory=lambda cookie: fake_client,
    )

    status = await manager.validate_cookie("SESSDATA=abc123")

    assert status.has_cookie is True
    assert status.authenticated is True
    assert status.username == "alice"
    assert status.user_id == 10086
    assert fake_client.closed is True


@pytest.mark.asyncio
async def test_validate_cookie_returns_failure_reason(tmp_path: Path) -> None:
    fake_client = FakeNavClient(error=BilibiliAPIError("cookie 已过期"))
    manager = AuthManager(
        tmp_path,
        api_client_factory=lambda cookie: fake_client,
    )

    status = await manager.validate_cookie("SESSDATA=expired")

    assert status.has_cookie is True
    assert status.authenticated is False
    assert "已过期" in status.message
    assert fake_client.closed is True


@pytest.mark.asyncio
async def test_get_status_reports_missing_cookie(tmp_path: Path) -> None:
    manager = AuthManager(tmp_path)

    status = await manager.get_status()

    assert status.has_cookie is False
    assert status.authenticated is False
    assert "未配置" in status.message


def test_resolve_runtime_cookie_prefers_config_value(tmp_path: Path) -> None:
    manager = AuthManager(tmp_path)
    manager.set_cookie("SESSDATA=saved_cookie")

    assert (
        resolve_runtime_cookie(data_dir=tmp_path, configured_cookie="SESSDATA=config_cookie")
        == "SESSDATA=config_cookie"
    )


def test_resolve_runtime_cookie_falls_back_to_saved_cookie(tmp_path: Path) -> None:
    manager = AuthManager(tmp_path)
    manager.set_cookie("SESSDATA=saved_cookie")

    assert (
        resolve_runtime_cookie(data_dir=tmp_path, configured_cookie="") == "SESSDATA=saved_cookie"
    )
