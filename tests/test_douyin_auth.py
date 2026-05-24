"""Tests for persisted Douyin direct-cookie auth state."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from openbiliclaw.sources.douyin_auth import DouyinCookieManager, resolve_douyin_cookie

if TYPE_CHECKING:
    from pathlib import Path


def test_douyin_cookie_manager_persists_cookie_without_config(tmp_path: Path) -> None:
    manager = DouyinCookieManager(tmp_path)

    manager.set_cookie("msToken=real; ttwid=tw;", source="extension")

    payload = json.loads((tmp_path / "douyin_cookie.json").read_text(encoding="utf-8"))
    assert payload["cookie"] == "msToken=real; ttwid=tw;"
    assert payload["source"] == "extension"


def test_resolve_douyin_cookie_prefers_env_over_persisted(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manager = DouyinCookieManager(tmp_path)
    manager.set_cookie("msToken=file;")
    monkeypatch.setenv("TEST_DOUYIN_COOKIE", "msToken=env;")

    assert (
        resolve_douyin_cookie(data_dir=tmp_path, cookie_env="TEST_DOUYIN_COOKIE") == "msToken=env;"
    )


def test_resolve_douyin_cookie_falls_back_to_persisted_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manager = DouyinCookieManager(tmp_path)
    manager.set_cookie("msToken=file;")
    monkeypatch.delenv("TEST_DOUYIN_COOKIE", raising=False)

    assert (
        resolve_douyin_cookie(data_dir=tmp_path, cookie_env="TEST_DOUYIN_COOKIE") == "msToken=file;"
    )
