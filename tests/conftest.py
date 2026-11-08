"""Shared pytest fixtures for the OpenBiliClaw test suite."""

from __future__ import annotations

from collections import OrderedDict
from functools import wraps
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.bilibili import search_backoff
from openbiliclaw.bilibili.api import BilibiliAPIClient

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any


@pytest.fixture(autouse=True)
def _default_test_client_is_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """Model the local app origin; security tests pass an explicit peer/origin.

    Starlette's synthetic 'testclient' peer and 'testserver' Host are not
    trusted network addresses. Private API tests must exercise the same
    loopback boundary as the installed app, without disabling authentication.
    """
    from starlette.testclient import TestClient

    original = TestClient.__init__

    @wraps(original)
    def local_init(self: TestClient, *args: Any, **kwargs: Any) -> None:
        if "client" not in kwargs:
            kwargs["client"] = ("127.0.0.1", 50000)
            if len(args) < 2 and "base_url" not in kwargs:
                kwargs["base_url"] = "http://127.0.0.1:8420"
        original(self, *args, **kwargs)

    monkeypatch.setattr(TestClient, "__init__", local_init)

    original_websocket = TestClient.websocket_connect

    @wraps(original_websocket)
    def websocket_at_client_origin(self: TestClient, url: str, *args: Any, **kwargs: Any) -> Any:
        # Starlette hardcodes ws://testserver for relative URLs, ignoring
        # base_url. Use the actual test origin for HTTP and WebSocket alike.
        if url.startswith("/"):
            scheme = "wss" if self.base_url.scheme == "https" else "ws"
            url = str(self.base_url.copy_with(scheme=scheme)).rstrip("/") + url
        return original_websocket(self, url, *args, **kwargs)

    monkeypatch.setattr(TestClient, "websocket_connect", websocket_at_client_origin)


@pytest.fixture(autouse=True)
def _isolate_ollama_embedding_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runner failures in fake transports must not affect later tests."""
    from openbiliclaw.llm import ollama_embedding_runtime

    monkeypatch.setattr(ollama_embedding_runtime, "_cpu_models", set())


@pytest.fixture(autouse=True)
def _isolate_bilibili_search_backoff(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Redirect the shared search-backoff state file into a tmp dir.

    The Bilibili API client persists its search cooldown so every runtime
    process backs off together; without this isolation the suite would read
    and write the real ``data/bilibili_search_backoff.json``.
    """
    monkeypatch.setattr(
        search_backoff,
        "_state_path_override",
        tmp_path / "bilibili_search_backoff.json",
    )


@pytest.fixture(autouse=True)
def _isolate_bilibili_view_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every test an empty /view payload cache.

    The cache is a process-wide ClassVar keyed by bvid; without isolation a
    payload cached by one test's fake transport would leak into the next.
    """
    monkeypatch.setattr(BilibiliAPIClient, "_view_data_cache", OrderedDict())
