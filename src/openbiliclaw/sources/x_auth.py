"""Persisted X (Twitter) cookie helpers for server-side cookie-replay discovery.

Mirrors :mod:`openbiliclaw.sources.douyin_auth`: the browser extension keeps
``data/x_cookie.json`` fresh; secrets never land in config.toml.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

# Server-side cookie replay needs BOTH the session token (auth_token) and the
# CSRF token (ct0) — twitter-cli 401s immediately without either.
X_REQUIRED_COOKIE_NAMES = ("auth_token", "ct0")

_X_COOKIE_FILENAME = "x_cookie.json"


class XCookieManager:
    """Store the user's X (Twitter) Cookie header outside config.toml."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._cookie_path = data_dir / _X_COOKIE_FILENAME

    @property
    def cookie_path(self) -> Path:
        return self._cookie_path

    def set_cookie(self, cookie: str, *, source: str = "unknown") -> None:
        normalized = cookie.strip()
        self._data_dir.mkdir(parents=True, exist_ok=True)
        with open(self._cookie_path, "w", encoding="utf-8") as f:
            json.dump(
                {"cookie": normalized, "source": source.strip() or "unknown"},
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


def resolve_x_cookie(*, data_dir: Path, cookie_env: str = "OPENBILICLAW_X_COOKIE") -> str:
    """Resolve the X (Twitter) Cookie header for server-side discovery.

    The environment variable is the explicit override for debugging, while the
    browser extension keeps ``data/x_cookie.json`` fresh for normal use. Env
    always wins over the persisted file (mirrors ``resolve_douyin_cookie``).
    """
    env_cookie = os.environ.get(cookie_env, "").strip()
    if env_cookie:
        return env_cookie
    return XCookieManager(data_dir).load_cookie()
