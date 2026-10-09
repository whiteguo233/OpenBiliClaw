"""Persisted TikTok cookie helpers for the web API backend.

A TikTok login cookie is *optional*: the web backend runs anonymously on a
minted guest identity (see ``tiktok_web.py``). When present, the cookie
enables attempts at surfaces the guest identity cannot reach (keyword search).
Session verification does not prove search availability or higher quotas.
Resolution mirrors the Douyin/X pattern:
environment variable first, ``data/tiktok_cookie.json`` as the fallback.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

TIKTOK_COOKIE_ENV = "OPENBILICLAW_TIKTOK_COOKIE"


@dataclass(frozen=True)
class TiktokCookieRecord:
    """Stored TikTok Cookie header plus lightweight provenance."""

    cookie: str
    source: str = "unknown"


class TiktokCookieManager:
    """Store the user's TikTok Cookie header outside config.toml."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._cookie_path = data_dir / "tiktok_cookie.json"

    @property
    def cookie_path(self) -> Path:
        return self._cookie_path

    def set_cookie(self, cookie: str, *, source: str = "unknown") -> None:
        normalized = cookie.strip()
        self._data_dir.mkdir(parents=True, exist_ok=True)
        with open(self._cookie_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "cookie": normalized,
                    "source": source.strip() or "unknown",
                },
                f,
                ensure_ascii=False,
            )

    def load_cookie(self) -> str:
        if not self._cookie_path.exists():
            return ""
        with open(self._cookie_path, encoding="utf-8") as f:
            payload = json.load(f)
        if not isinstance(payload, dict):
            return ""
        return str(payload.get("cookie", "") or "").strip()

    def load_record(self) -> TiktokCookieRecord | None:
        if not self._cookie_path.exists():
            return None
        with open(self._cookie_path, encoding="utf-8") as f:
            payload = json.load(f)
        if not isinstance(payload, dict):
            return None
        cookie = str(payload.get("cookie", "") or "").strip()
        if not cookie:
            return None
        return TiktokCookieRecord(
            cookie=cookie,
            source=str(payload.get("source", "") or "unknown").strip() or "unknown",
        )

    def clear_cookie(self) -> None:
        if self._cookie_path.exists():
            self._cookie_path.unlink()


def resolve_tiktok_cookie(
    *,
    data_dir: Path,
    cookie_env: str = TIKTOK_COOKIE_ENV,
) -> str:
    """Resolve the optional TikTok login cookie for the web API backend.

    The environment variable remains the explicit override; the data file is
    the fallback. An empty result is a legitimate state — guest identity
    mode — never an error.
    """
    env_cookie = os.environ.get(cookie_env, "").strip()
    if env_cookie:
        return env_cookie
    return TiktokCookieManager(data_dir).load_cookie()
