from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.bilibili import browser as browser_module
from openbiliclaw.bilibili.browser import BilibiliBrowser, BrowserCommandError

if TYPE_CHECKING:
    from pathlib import Path


class _FakeProcess:
    def __init__(
        self,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        self.returncode = returncode
        self._stdout = stdout.encode("utf-8")
        self._stderr = stderr.encode("utf-8")

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr


def test_is_available_accepts_explicit_executable_path(tmp_path: Path) -> None:
    executable = tmp_path / "agent-browser"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)

    browser = BilibiliBrowser(executable=str(executable))

    assert browser.is_available is True


def test_install_hint_mentions_official_setup() -> None:
    hint = BilibiliBrowser.get_install_hint()

    assert "npm install -g agent-browser" in hint
    assert "agent-browser install" in hint


def test_is_available_does_not_treat_apachebench_ab_as_agent_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_which(name: str) -> str | None:
        if name == "agent-browser":
            return None
        if name == "ab":
            return "/usr/sbin/ab"
        return None

    monkeypatch.setattr("openbiliclaw.bilibili.browser.shutil.which", fake_which)

    browser = BilibiliBrowser()

    assert browser.executable == "agent-browser"
    assert browser.is_available is False


def test_is_available_rejects_non_runnable_agent_browser_shim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "openbiliclaw.bilibili.browser.shutil.which",
        lambda name: "/Users/white/.volta/bin/agent-browser" if name == "agent-browser" else None,
    )

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["agent-browser", "--version"],
            returncode=126,
            stdout="",
            stderr="Volta error",
        )

    monkeypatch.setattr(
        browser_module,
        "subprocess",
        SimpleNamespace(run=fake_run),
        raising=False,
    )

    browser = BilibiliBrowser()

    assert browser.executable == "/Users/white/.volta/bin/agent-browser"
    assert browser.is_available is False


@pytest.mark.asyncio
async def test_navigate_uses_official_open_command(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []

    async def fake_exec(*cmd: str, **kwargs: object) -> _FakeProcess:
        calls.append(tuple(cmd))
        return _FakeProcess(stdout='{"success": true}')

    monkeypatch.setattr(
        "openbiliclaw.bilibili.browser.asyncio.create_subprocess_exec",
        fake_exec,
    )

    browser = BilibiliBrowser(executable="agent-browser", headed=True)

    await browser.navigate("https://www.bilibili.com")

    assert len(calls) == 1
    assert calls[0][0] == "agent-browser"
    assert calls[0][1] == "--session"
    assert calls[0][2]
    assert calls[0][3:] == ("open", "https://www.bilibili.com", "--headed")


@pytest.mark.asyncio
async def test_get_page_content_uses_open_then_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []
    snapshot_payload = json.dumps(
        {
            "success": True,
            "data": {
                "snapshot": '- heading "Example Domain" [ref=e1]',
            },
        }
    )

    async def fake_exec(*cmd: str, **kwargs: object) -> _FakeProcess:
        calls.append(tuple(cmd))
        if cmd[1] == "open":
            return _FakeProcess(stdout='{"success": true}')
        return _FakeProcess(stdout=snapshot_payload)

    monkeypatch.setattr(
        "openbiliclaw.bilibili.browser.asyncio.create_subprocess_exec",
        fake_exec,
    )

    browser = BilibiliBrowser(executable="agent-browser")

    content = await browser.get_page_content("https://example.com")

    assert content == '- heading "Example Domain" [ref=e1]'
    assert len(calls) == 2
    assert calls[0][0] == "agent-browser"
    assert calls[0][1] == "--session"
    assert calls[1][0] == "agent-browser"
    assert calls[1][1] == "--session"
    assert calls[0][2] == calls[1][2]
    assert calls[0][3:] == ("open", "https://example.com")
    assert calls[1][3:] == ("snapshot", "-i", "--json")


@pytest.mark.asyncio
async def test_navigate_retries_once_on_err_aborted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    responses = [
        _FakeProcess(
            returncode=1,
            stderr="page.goto: net::ERR_ABORTED at https://www.bilibili.com/",
        ),
        _FakeProcess(stdout='{"success": true}'),
    ]

    async def fake_exec(*cmd: str, **kwargs: object) -> _FakeProcess:
        calls.append(tuple(cmd))
        return responses.pop(0)

    monkeypatch.setattr(
        "openbiliclaw.bilibili.browser.asyncio.create_subprocess_exec",
        fake_exec,
    )

    browser = BilibiliBrowser(executable="agent-browser")

    await browser.navigate("https://www.bilibili.com")

    assert len(calls) == 2
    assert calls[0][2] == calls[1][2]
    assert calls[0][3:] == ("open", "https://www.bilibili.com")
    assert calls[1][3:] == ("open", "https://www.bilibili.com")


@pytest.mark.asyncio
async def test_navigate_raises_clear_error_on_cli_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_exec(*cmd: str, **kwargs: object) -> _FakeProcess:
        return _FakeProcess(returncode=1, stderr="browser failed")

    monkeypatch.setattr(
        "openbiliclaw.bilibili.browser.asyncio.create_subprocess_exec",
        fake_exec,
    )

    browser = BilibiliBrowser(executable="agent-browser")

    with pytest.raises(BrowserCommandError, match="browser failed"):
        await browser.navigate("https://example.com")
