"""Shared pytest fixtures for the OpenBiliClaw test suite."""

from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.bilibili import search_backoff
from openbiliclaw.bilibili.api import BilibiliAPIClient

if TYPE_CHECKING:
    from pathlib import Path


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
