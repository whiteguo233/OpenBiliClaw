"""Isolated Bilibili account plumbing for the Blue Fish Lab.

The fish must never borrow the owner's credentials. Everything here is
rooted at ``<data_dir>/fish/`` so its cookie file, its ``AuthManager`` and
its API client are separate objects from the main account's.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from openbiliclaw.bilibili.auth import AuthManager

from .config import fish_home

if TYPE_CHECKING:
    from pathlib import Path

    from openbiliclaw.bilibili.api import NavInfo

logger = logging.getLogger(__name__)


class _SupportsNav(Protocol):
    async def get_nav_info(self) -> NavInfo: ...

    async def close(self) -> None: ...


_ClientFactory = Callable[[str], _SupportsNav]


@dataclass(frozen=True)
class FishAccountStatus:
    """Login state of the fish account."""

    has_cookie: bool
    authenticated: bool
    mid: int = 0
    uname: str = ""
    message: str = ""
    # False when the fish turns out to be logged in as the owner's own
    # account — the lab refuses to run in that case.
    isolated: bool = True


class FishAccount:
    """Owns the fish cookie and validates it against Bilibili."""

    def __init__(
        self,
        data_dir: Path,
        *,
        proxy: str = "",
        client_factory: _ClientFactory | None = None,
    ) -> None:
        self._home = fish_home(data_dir)
        self._proxy = proxy or None
        self._auth = AuthManager(self._home, proxy=self._proxy)
        self._client_factory = client_factory or self._default_client_factory

    @property
    def home(self) -> Path:
        """The lab's private directory."""
        return self._home

    @property
    def cookie_path(self) -> Path:
        """Where the fish cookie is stored (never the owner's file)."""
        return self._home / "bilibili_cookie.json"

    def load_cookie(self) -> str:
        """Return the saved fish cookie, or an empty string."""
        return self._auth.load_cookie().strip()

    def save_cookie(self, cookie: str) -> str:
        """Persist a fish cookie after trimming it."""
        normalized = str(cookie).strip()
        if not normalized:
            raise ValueError("Cookie 不能为空")
        self._auth.set_cookie(normalized)
        return normalized

    def clear_cookie(self) -> None:
        """Remove the fish cookie."""
        self._auth.clear_cookie()

    def is_same_cookie(self, other_cookie: str) -> bool:
        """Return whether ``other_cookie`` is the fish cookie verbatim."""
        mine = self.load_cookie()
        return bool(mine) and mine == str(other_cookie).strip()

    async def status(self, *, main_mid: int = 0, main_cookie: str = "") -> FishAccountStatus:
        """Validate the fish cookie and check it is not the owner's account."""
        cookie = self.load_cookie()
        if not cookie:
            return FishAccountStatus(
                has_cookie=False,
                authenticated=False,
                message="还没给大肥鱼登录：它需要自己的 B 站账号。",
            )
        client = self._client_factory(cookie)
        try:
            nav = await client.get_nav_info()
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI verbatim
            logger.warning("Blue Fish cookie validation failed: %s", exc)
            return FishAccountStatus(
                has_cookie=True,
                authenticated=False,
                message=f"大肥鱼的 Cookie 校验失败：{exc}",
            )
        finally:
            await client.close()
        if not nav.is_login:
            return FishAccountStatus(
                has_cookie=True,
                authenticated=False,
                message="大肥鱼的 Cookie 已失效，请重新登录。",
            )
        same_cookie = bool(main_cookie) and cookie == str(main_cookie).strip()
        isolated = not (main_mid and int(nav.mid) == int(main_mid)) and not same_cookie
        message = (
            "大肥鱼已登录。" if isolated else "这个账号就是你自己的账号 —— 大肥鱼必须是独立的小号。"
        )
        return FishAccountStatus(
            has_cookie=True,
            authenticated=True,
            mid=int(nav.mid),
            uname=str(nav.uname),
            message=message,
            isolated=isolated,
        )

    def _default_client_factory(self, cookie: str) -> _SupportsNav:
        from openbiliclaw.bilibili.api import BilibiliAPIClient

        return BilibiliAPIClient(cookie=cookie, proxy=self._proxy)
