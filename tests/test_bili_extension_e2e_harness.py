"""Unit tests for the opt-in Bili extension browser E2E harness helpers."""

from __future__ import annotations

import socket
from typing import TYPE_CHECKING

import pytest

from tests.test_bili_extension_browser_e2e import (
    choose_bili_service_worker_target,
    find_free_port,
    is_bili_extension_e2e_candidate,
    resolve_chrome_executable,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_resolve_chrome_executable_accepts_explicit_path(tmp_path: Path) -> None:
    exe = tmp_path / "chrome"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")

    assert resolve_chrome_executable(str(exe)) == exe


def test_resolve_chrome_executable_rejects_missing_explicit_path(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        resolve_chrome_executable(str(tmp_path / "missing-chrome"))


def test_find_free_port_returns_bindable_loopback_port() -> None:
    port = find_free_port()

    assert isinstance(port, int)
    assert 1024 < port < 65536
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", port))


def test_choose_bili_service_worker_target_prefers_openbiliclaw_worker() -> None:
    targets = [
        {
            "type": "page",
            "url": "https://search.bilibili.com/all?keyword=x",
            "webSocketDebuggerUrl": "ws://page",
        },
        {
            "type": "service_worker",
            "url": "chrome-extension://other/service_worker.js",
            "webSocketDebuggerUrl": "ws://other",
        },
        {
            "type": "service_worker",
            "url": "chrome-extension://abc/dist/background/service-worker.js",
            "webSocketDebuggerUrl": "ws://bili",
        },
    ]

    assert choose_bili_service_worker_target(targets)["webSocketDebuggerUrl"] == "ws://bili"


def test_choose_bili_service_worker_target_errors_when_missing() -> None:
    with pytest.raises(RuntimeError):
        choose_bili_service_worker_target([{"type": "page", "url": "about:blank"}])


def test_is_bili_extension_e2e_candidate_limits_cleanup_scope() -> None:
    assert is_bili_extension_e2e_candidate(
        {
            "source_platform": "bilibili",
            "source_strategy": "bili-extension-search",
            "bvid": "BV1ii4y1G7w8",
            "created_at": "2026-06-16 10:00:00",
        },
        bvids={"BV1ii4y1G7w8"},
    )
    assert not is_bili_extension_e2e_candidate(
        {
            "source_platform": "bilibili",
            "source_strategy": "search",
            "bvid": "BV1ii4y1G7w8",
            "created_at": "2026-06-16 10:00:00",
        },
        bvids={"BV1ii4y1G7w8"},
    )
