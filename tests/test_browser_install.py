"""Matching browser runtime detection and frozen-compatible installation."""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING

from openbiliclaw.sources import browser_install

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_browser_status_requires_matching_executable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "chromium"
    monkeypatch.setattr(browser_install, "_driver_command", lambda: ("bundled-node", "/app/cli.js"))
    monkeypatch.setattr(
        browser_install.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            [], 0, json.dumps({"executable_path": str(executable)})
        ),
    )
    assert browser_install.browser_runtime_status()["installed"] is False
    executable.touch()
    assert browser_install.browser_runtime_status()["installed"] is True


def test_browser_status_reports_missing_module(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(browser_install, "_driver_command", lambda: None)
    assert browser_install.browser_runtime_status()["installed"] is False
    assert "[browser]" in browser_install.browser_runtime_status()["detail"]


async def test_browser_installer_uses_bundled_driver_not_python(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, ...]] = []

    class Process:
        async def wait(self) -> int:
            return 0

    async def spawn(*args: str, **kwargs: object) -> Process:
        captured.append(args)
        return Process()

    monkeypatch.setattr(
        browser_install, "_driver_command", lambda: ("/bundle/node", "/bundle/cli.js")
    )
    monkeypatch.setattr(browser_install.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(browser_install, "browser_runtime_status", lambda: {"installed": True})
    assert (await browser_install.install_browser_runtime(with_system_deps=True))["installed"]
    assert captured == [("/bundle/node", "/bundle/cli.js", "install", "--with-deps", "chromium")]


async def test_browser_installer_redacts_subprocess_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Process:
        async def wait(self) -> int:
            return 1

    async def spawn(*args: str, **kwargs: object) -> Process:
        return Process()

    monkeypatch.setattr(
        browser_install, "_driver_command", lambda: ("/bundle/node", "/bundle/cli.js")
    )
    monkeypatch.setattr(browser_install.asyncio, "create_subprocess_exec", spawn)
    result = await browser_install.install_browser_runtime()
    assert result["installed"] is False
    assert result["detail"]
