from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest

import openbiliclaw
from openbiliclaw.runtime import updater

if TYPE_CHECKING:
    from collections.abc import Mapping


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("backend-v0.3.71", (0, 3, 71)),
        ("backend-v0.3.71-rc1", None),
        ("extension-v0.3.24", None),
        ("v0.3.71", (0, 3, 71)),
        ("0.3.71", (0, 3, 71)),
        ("backend-vfoo", None),
        ("", None),
    ],
)
def test_parse_backend_version_filters_non_backend_tags(
    tag: str,
    expected: tuple[int, ...] | None,
) -> None:
    assert updater._parse_backend_version(tag) == expected


class _FakeResponse:
    def __init__(
        self,
        status_code: int,
        payload: object,
        *,
        headers: Mapping[str, str] | None = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.headers = dict(headers or {})
        self.text = text

    def json(self) -> object:
        return self._payload


class _FakeAsyncClient:
    calls: list[tuple[str, dict[str, object] | None]] = []
    init_kwargs: list[dict[str, object]] = []
    pages: dict[int, object] = {}
    responses: dict[int, _FakeResponse] = {}
    responses_by_url: dict[str, _FakeResponse] = {}
    error: Exception | None = None
    error_by_verify: dict[bool, Exception] = {}

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.verify = bool(_kwargs.get("verify", True))
        self.init_kwargs.append(dict(_kwargs))

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,  # noqa: ARG002
        params: dict[str, object] | None = None,
    ) -> _FakeResponse:
        self.calls.append((url, params))
        if self.verify in self.error_by_verify:
            raise self.error_by_verify[self.verify]
        if self.error is not None:
            raise self.error
        if url in self.responses_by_url:
            return self.responses_by_url[url]
        page = int(params.get("page", 1)) if params else 1
        if page in self.responses:
            return self.responses[page]
        return _FakeResponse(200, self.pages.get(page, []))


@pytest.fixture(autouse=True)
def _reset_fake_client(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.init_kwargs = []
    _FakeAsyncClient.pages = {}
    _FakeAsyncClient.responses = {}
    _FakeAsyncClient.responses_by_url = {}
    _FakeAsyncClient.error = None
    _FakeAsyncClient.error_by_verify = {}
    monkeypatch.setattr(updater.httpx, "AsyncClient", _FakeAsyncClient)


@pytest.mark.asyncio
async def test_fetch_latest_version_uses_tags_and_returns_highest_backend_tag() -> None:
    _FakeAsyncClient.pages = {
        1: [
            {"name": "extension-v0.3.24"},
            {"name": "backend-v0.3.71"},
            {"name": "backend-v0.3.69"},
        ],
    }

    service = updater.AutoUpdateService()
    result = await service._fetch_latest_version()

    assert result == "backend-v0.3.71"
    assert all("releases/latest" not in url for url, _params in _FakeAsyncClient.calls)


@pytest.mark.asyncio
async def test_fetch_latest_version_prefers_backend_tag_over_higher_legacy_tag() -> None:
    _FakeAsyncClient.pages = {
        1: [
            {"name": "v0.3.90"},
            {"name": "0.3.91"},
            {"name": "backend-v0.3.89"},
        ],
    }

    service = updater.AutoUpdateService()

    assert await service._fetch_latest_version() == "backend-v0.3.89"


def test_prerelease_candidates_order_by_semver_identifiers() -> None:
    """rc2 beats rc1 and a same-numbered stable beats any prerelease (audit N8)."""
    selection = updater._select_latest_candidate_from_tag_names(
        ["backend-v0.4.0-rc1", "backend-v0.4.0-rc2"],
        allow_prerelease=True,
        channel="backend",
    )
    assert selection.tag == "backend-v0.4.0-rc2"
    # Feed order must not decide the winner.
    reversed_selection = updater._select_latest_candidate_from_tag_names(
        ["backend-v0.4.0-rc2", "backend-v0.4.0-rc1"],
        allow_prerelease=True,
        channel="backend",
    )
    assert reversed_selection.tag == "backend-v0.4.0-rc2"
    stable = updater._select_latest_candidate_from_tag_names(
        ["backend-v0.4.0-rc9", "backend-v0.4.0"],
        allow_prerelease=True,
        channel="backend",
    )
    assert stable.tag == "backend-v0.4.0"
    # Numeric identifiers compare numerically: rc.10 > rc.9.
    numeric = updater._select_latest_candidate_from_tag_names(
        ["backend-v0.4.0-rc.9", "backend-v0.4.0-rc.10"],
        allow_prerelease=True,
        channel="backend",
    )
    assert numeric.tag == "backend-v0.4.0-rc.10"


def test_prerelease_version_text_keeps_prerelease_identity() -> None:
    """UI must not present backend-v0.4.0-rc1 as a stable v0.4.0 (audit N8)."""
    selection = updater._select_latest_candidate_from_tag_names(
        ["backend-v0.4.0-rc1"],
        allow_prerelease=True,
        channel="backend",
    )
    assert selection.version_text == "0.4.0-rc1"


@pytest.mark.asyncio
async def test_fetch_latest_version_ignores_prerelease_by_default() -> None:
    _FakeAsyncClient.pages = {
        1: [
            {"name": "backend-v0.3.100-rc1"},
            {"name": "backend-v0.3.99-beta.1"},
        ],
    }

    service = updater.AutoUpdateService()

    assert await service._fetch_latest_version() == ""


@pytest.mark.asyncio
async def test_fetch_latest_version_finds_backend_tag_on_later_page() -> None:
    _FakeAsyncClient.pages = {
        1: [{"name": "extension-v0.3.24"}],
        2: [{"name": "backend-v0.3.69"}],
        3: [],
    }

    service = updater.AutoUpdateService()

    assert await service._fetch_latest_version() == "backend-v0.3.69"


@pytest.mark.asyncio
async def test_fetch_latest_version_returns_empty_when_tags_have_only_extension_releases() -> None:
    _FakeAsyncClient.pages = {
        1: [{"name": "extension-v0.3.20"}, {"name": "extension-v0.3.24"}],
        2: [],
    }

    service = updater.AutoUpdateService()

    assert await service._fetch_latest_version() == ""


@pytest.mark.asyncio
async def test_fetch_latest_version_returns_empty_and_warns_on_http_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _FakeAsyncClient.error = httpx.ConnectError("network down")
    service = updater.AutoUpdateService()

    with caplog.at_level(logging.WARNING):
        assert await service._fetch_latest_version() == ""

    assert "Auto-update tag check failed" in caplog.text


@pytest.mark.asyncio
async def test_fetch_latest_version_fails_closed_on_tls_verification_error() -> None:
    # Deliberate spec invariant 3 rewrite: the updater must never retry with
    # verify=False after a certificate failure.
    _FakeAsyncClient.error_by_verify = {
        True: httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
    }
    _FakeAsyncClient.pages = {1: [{"name": "backend-v0.3.92"}]}

    service = updater.AutoUpdateService()
    backend = await service.check_now()

    assert backend["state"] == "error"
    assert backend["reason"] == "tls_verification_failed"
    assert "SSL_CERT_FILE" in str(backend["last_error"])
    assert [kwargs.get("verify", True) for kwargs in _FakeAsyncClient.init_kwargs] == [True]


@pytest.mark.asyncio
async def test_check_now_reports_no_backend_tag_for_extension_only_tags(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _FakeAsyncClient.pages = {
        1: [{"name": "extension-v0.3.20"}, {"name": "extension-v0.3.24"}],
        2: [],
    }
    monkeypatch.setattr(openbiliclaw, "__version__", "0.3.64")
    service = updater.AutoUpdateService()

    with caplog.at_level(logging.INFO):
        result = await service.check_and_update_now()

    assert result == {"checked": True, "updated": False, "reason": "no_backend_tag_yet"}
    assert "no_backend_tag_yet" in caplog.text
    assert "Already up-to-date" not in caplog.text


@pytest.mark.asyncio
async def test_manual_check_reports_update_available_when_auto_apply_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeAsyncClient.pages = {1: [{"name": "backend-v0.3.92"}], 2: []}
    monkeypatch.setattr(openbiliclaw, "__version__", "0.3.91")
    service = updater.AutoUpdateService(enabled=False)

    backend = await service.check_now()

    assert backend["state"] == "update_available"
    assert backend["auto_update_enabled"] is False
    assert backend["current_version"] == "0.3.91"
    assert backend["latest_version"] == "0.3.92"
    assert backend["latest_tag"] == "backend-v0.3.92"
    assert backend["reason"] == "none"


@pytest.mark.asyncio
async def test_manual_check_reports_github_rate_limited_when_tags_api_quota_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeAsyncClient.responses = {
        1: _FakeResponse(
            403,
            {"message": "API rate limit exceeded for 203.0.113.10."},
            headers={
                "x-ratelimit-remaining": "0",
                "x-ratelimit-reset": "1782291614",
            },
        )
    }
    monkeypatch.setattr(openbiliclaw, "__version__", "0.3.138")
    service = updater.AutoUpdateService(enabled=False)

    backend = await service.check_now()

    assert backend["state"] == "error"
    assert backend["reason"] == "github_rate_limited"
    assert backend["last_error"] == "github_rate_limited"
    assert backend["current_version"] == "0.3.138"


@pytest.mark.asyncio
async def test_manual_check_uses_atom_fallback_when_tags_api_quota_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeAsyncClient.responses = {
        1: _FakeResponse(
            403,
            {"message": "API rate limit exceeded for 203.0.113.10."},
            headers={"x-ratelimit-remaining": "0"},
        )
    }
    _FakeAsyncClient.responses_by_url = {
        updater._GITHUB_TAGS_ATOM: _FakeResponse(
            200,
            None,
            text="""
            <feed xmlns="http://www.w3.org/2005/Atom">
              <entry>
                <link rel="alternate" href="https://github.com/whiteguo233/OpenBiliClaw/releases/tag/extension-v0.3.91"/>
                <title>extension-v0.3.91</title>
              </entry>
              <entry>
                <link rel="alternate" href="https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.139"/>
                <title>backend-v0.3.139</title>
              </entry>
            </feed>
            """,
        )
    }
    monkeypatch.setattr(openbiliclaw, "__version__", "0.3.138")
    service = updater.AutoUpdateService(enabled=False)

    backend = await service.check_now()

    assert backend["state"] == "update_available"
    assert backend["reason"] == "none"
    assert backend["last_error"] == ""
    assert backend["latest_version"] == "0.3.139"
    assert backend["latest_tag"] == "backend-v0.3.139"


@pytest.mark.asyncio
async def test_atom_fallback_recovers_from_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class _TransportThenAtomClient:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, url: str, **_kwargs: object) -> _FakeResponse:
            calls.append(url)
            if url == updater._GITHUB_TAGS:
                raise httpx.ConnectError("connection refused")
            return _FakeResponse(
                200,
                None,
                text="""
                <feed xmlns="http://www.w3.org/2005/Atom">
                  <entry>
                    <link rel="alternate" href="https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.140"/>
                  </entry>
                </feed>
                """,
            )

    monkeypatch.setattr(updater.httpx, "AsyncClient", _TransportThenAtomClient)
    monkeypatch.setattr(openbiliclaw, "__version__", "0.3.139")

    backend = await updater.AutoUpdateService().check_now()

    assert backend["state"] == "update_available"
    assert backend["latest_tag"] == "backend-v0.3.140"
    assert calls == [updater._GITHUB_TAGS, updater._GITHUB_TAGS_ATOM]


@pytest.mark.asyncio
async def test_describe_empty_transport_exception_in_log_and_status(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _FakeAsyncClient.error = httpx.ReadTimeout("")
    service = updater.AutoUpdateService()

    with caplog.at_level(logging.WARNING, logger="openbiliclaw.runtime.updater"):
        backend = await service.check_now()

    assert backend["reason"] == "github_unreachable"
    assert "ReadTimeout" in str(backend["last_error"])
    assert "ReadTimeout" in caplog.text


@pytest.mark.asyncio
async def test_classify_malformed_tags_json_without_raising() -> None:
    class _MalformedResponse(_FakeResponse):
        def json(self) -> object:
            raise ValueError("malformed json")

    _FakeAsyncClient.responses = {1: _MalformedResponse(200, None)}

    backend = await updater.AutoUpdateService().check_now()

    assert backend["state"] == "error"
    assert backend["reason"] == "invalid_github_response"
    assert "malformed json" in str(backend["last_error"])


@pytest.mark.asyncio
async def test_manual_check_publishes_backend_update_available_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeAsyncClient.pages = {1: [{"name": "backend-v0.3.92"}], 2: []}
    monkeypatch.setattr(openbiliclaw, "__version__", "0.3.91")
    events: list[dict[str, object]] = []

    async def _publish(event: dict[str, object]) -> None:
        events.append(event)

    service = updater.AutoUpdateService(enabled=False, event_publisher=_publish)

    await service.check_now()

    assert events == [
        {
            "type": "backend_update_available",
            "current_version": "0.3.91",
            "latest_version": "0.3.92",
            "latest_tag": "backend-v0.3.92",
        }
    ]


@pytest.mark.asyncio
async def test_manual_check_reports_prerelease_ignored_when_only_newer_rc_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeAsyncClient.pages = {1: [{"name": "backend-v0.3.92-rc1"}], 2: []}
    monkeypatch.setattr(openbiliclaw, "__version__", "0.3.91")
    service = updater.AutoUpdateService(enabled=False)

    backend = await service.check_now()

    assert backend["state"] == "up_to_date"
    assert backend["reason"] == "prerelease_ignored"
    assert backend["latest_tag"] == ""


def test_detect_install_mode_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert updater.detect_install_mode() == "frozen"


def test_detect_install_mode_git_and_unsupported(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    assert updater.detect_install_mode() == "unsupported"
    (tmp_path / ".git").mkdir()
    assert updater.detect_install_mode() == "git"


def test_update_status_payloads_include_install_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    service = updater.AutoUpdateService(enabled=False)

    assert service.get_update_status()["install_mode"] == "git"
    assert service.get_runtime_status()["install_mode"] == "git"


@pytest.mark.parametrize(
    ("porcelain", "expected"),
    [
        ("", []),
        (" M uv.lock\n", []),
        ("?? notes.txt\n", []),
        ("A  staged.txt\n", []),
        ("?? ollama-models/model.bin\n", []),
        (" M ollama-models/model.bin\n", []),
        (" M uv.lock\n M src/openbiliclaw/cli.py\n", ["src/openbiliclaw/cli.py"]),
        ("MM uv.lock\n", []),
        ("MM src/openbiliclaw/cli.py\n", ["src/openbiliclaw/cli.py"]),
    ],
)
def test_dirty_paths_besides_uv_lock(porcelain: str, expected: list[str]) -> None:
    assert updater._dirty_paths_besides_uv_lock(porcelain) == expected


@pytest.mark.parametrize(
    ("porcelain", "expected_reason"),
    [
        ("A  src/new_file.py\n", "dirty_worktree"),
        ("M  src/changed.py\n", "dirty_worktree"),
        ("?? src/untracked.py\n", ""),
        ("A  uv.lock\n", ""),
    ],
)
@pytest.mark.asyncio
async def test_staged_changes_are_dirty_but_untracked_files_remain_clean(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    porcelain: str,
    expected_reason: str,
) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    service = updater.AutoUpdateService()

    async def _run_command(command, _root, *, timeout):
        if command == ["git", "config", "--get", "remote.origin.url"]:
            return subprocess.CompletedProcess(
                command, 0, "https://github.com/whiteguo233/OpenBiliClaw.git\n", ""
            )
        if command == ["git", "status", "--porcelain"]:
            return subprocess.CompletedProcess(command, 0, porcelain, "")
        if command == ["git", "rev-parse", "--git-dir"]:
            return subprocess.CompletedProcess(command, 0, ".git\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(service, "_run_command", _run_command)

    assert await service._check_apply_guards("backend-v0.3.140") == expected_reason


@pytest.mark.parametrize(
    ("tag", "expected_reason"),
    [
        ("extension-v0.3.171", "invalid_target_tag"),
        ("desktop-v0.3.171", "invalid_target_tag"),
        ("not-a-release", "invalid_target_tag"),
        ("backend-v0.3.171-rc1", "prerelease_not_allowed"),
    ],
)
@pytest.mark.asyncio
async def test_apply_tag_channel_validation_precedes_git_commands(
    monkeypatch: pytest.MonkeyPatch,
    tag: str,
    expected_reason: str,
) -> None:
    service = updater.AutoUpdateService(allow_prerelease=False)
    calls: list[list[str]] = []

    async def _run_command(command, _root, *, timeout):
        calls.append(list(command))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(service, "_run_command", _run_command)

    status_code, payload = await service.request_apply(tag=tag)

    assert status_code == 409
    assert payload["reason"] == expected_reason
    assert calls == []


@pytest.mark.asyncio
async def test_request_apply_allows_uv_lock_only_dirty_worktree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A worktree dirty only in uv.lock must not block updates.

    Release tags occasionally ship a stale uv.lock; the install's first
    ``uv sync`` then rewrites it, so virtually every real install is in
    this state permanently (the original dirty_worktree blocker).
    """
    (tmp_path / ".git").mkdir()
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    calls: list[list[str]] = []
    restarted = False

    async def _run_command(command, _root, *, timeout):
        calls.append(list(command))
        if command == ["git", "config", "--get", "remote.origin.url"]:
            return subprocess.CompletedProcess(
                command, 0, "https://github.com/whiteguo233/OpenBiliClaw.git\n", ""
            )
        if command == ["git", "status", "--porcelain"]:
            return subprocess.CompletedProcess(command, 0, " M uv.lock\n", "")
        if command == ["git", "rev-parse", "--git-dir"]:
            return subprocess.CompletedProcess(command, 0, ".git\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    def _restart() -> None:
        nonlocal restarted
        restarted = True

    service = updater.AutoUpdateService(enabled=False)
    monkeypatch.setattr(service, "_run_command", _run_command)
    monkeypatch.setattr(service, "_restart_process", _restart)

    status_code, payload = await service.request_apply(tag="backend-v0.3.92")
    if service._apply_task is not None:
        await asyncio.wait_for(service._apply_task, timeout=0.5)

    assert status_code == 202
    assert payload["accepted"] is True
    # The local uv.lock rewrite is dropped before the fast-forward merge.
    checkout_index = calls.index(["git", "checkout", "--", "uv.lock"])
    merge_index = calls.index(["git", "merge", "--ff-only", "backend-v0.3.92"])
    assert checkout_index < merge_index
    assert restarted is True


@pytest.mark.asyncio
async def test_request_apply_blocks_dirty_worktree_before_install_or_restart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    subprocess = pytest.importorskip("subprocess")
    for command in (
        ["git", "init"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test User"],
    ):
        subprocess.run(command, cwd=tmp_path, check=True, capture_output=True, text=True)
    (tmp_path / "README.md").write_text("initial\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/whiteguo233/OpenBiliClaw.git"],
        cwd=tmp_path,
        check=True,
    )
    (tmp_path / "README.md").write_text("dirty\n", encoding="utf-8")
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    restarted = False

    def _restart() -> None:
        nonlocal restarted
        restarted = True

    service = updater.AutoUpdateService(enabled=False)
    monkeypatch.setattr(service, "_restart_process", _restart)

    status_code, payload = await service.request_apply(tag="backend-v0.3.92")

    assert status_code == 409
    assert payload == {
        "target": "backend",
        "state": "blocked",
        "reason": "dirty_worktree",
        "accepted": False,
        "observe_via": "runtime-stream",
    }
    assert restarted is False


@pytest.mark.asyncio
async def test_request_apply_rejects_second_concurrent_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    service = updater.AutoUpdateService(enabled=False)

    async def _guard(_tag: str) -> str:
        return ""

    async def _apply(_tag: str) -> None:
        started.set()
        await release.wait()

    monkeypatch.setattr(service, "_check_apply_guards", _guard)
    monkeypatch.setattr(service, "_apply_update_to_tag", _apply)

    first_status, first_payload = await service.request_apply(tag="backend-v0.3.92")
    await asyncio.wait_for(started.wait(), timeout=0.5)
    second_status, second_payload = await service.request_apply(tag="backend-v0.3.92")
    release.set()
    if service._apply_task is not None:
        await asyncio.wait_for(service._apply_task, timeout=0.5)

    assert first_status == 202
    assert first_payload["accepted"] is True
    assert second_status == 409
    assert second_payload == {
        "target": "backend",
        "state": "applying",
        "reason": "already_applying",
        "accepted": False,
        "observe_via": "runtime-stream",
    }


@pytest.mark.asyncio
async def test_request_apply_reports_already_applying_after_service_hot_reload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    first = updater.AutoUpdateService(enabled=False)
    rebuilt = updater.AutoUpdateService(enabled=False)

    async def _guard(_tag: str) -> str:
        return ""

    async def _apply(_tag: str) -> None:
        started.set()
        await release.wait()

    for service in (first, rebuilt):
        monkeypatch.setattr(service, "_check_apply_guards", _guard)
        monkeypatch.setattr(service, "_apply_update_to_tag", _apply)

    first_status, _ = await first.request_apply(tag="backend-v0.3.92")
    await asyncio.wait_for(started.wait(), timeout=0.5)
    second_status, second_payload = await rebuilt.request_apply(tag="backend-v0.3.92")
    release.set()
    tasks = [task for task in (first._apply_task, rebuilt._apply_task) if task is not None]
    if tasks:
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=0.5)

    assert first_status == 202
    assert second_status == 409
    assert second_payload["reason"] == "already_applying"


@pytest.mark.parametrize(
    "remote_url",
    [
        "https://github.com/someone-else/OpenBiliClaw.git",
        "https://token@github.com/whiteguo233/OpenBiliClaw.git",
    ],
)
@pytest.mark.asyncio
async def test_request_apply_rejects_untrusted_and_credentialed_remotes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    remote_url: str,
) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    service = updater.AutoUpdateService(enabled=False)

    async def _run_command(command, _root, *, timeout):
        if command == ["git", "config", "--get", "remote.origin.url"]:
            return subprocess.CompletedProcess(command, 0, f"{remote_url}\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(service, "_run_command", _run_command)

    status_code, payload = await service.request_apply(tag="backend-v0.3.92")

    assert status_code == 409
    assert payload["state"] == "blocked"
    assert payload["reason"] == "untrusted_remote"


@pytest.mark.asyncio
async def test_request_apply_blocks_merge_or_rebase_in_progress(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "MERGE_HEAD").write_text("abc123\n", encoding="utf-8")
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    service = updater.AutoUpdateService(enabled=False)

    async def _run_command(command, _root, *, timeout):
        if command == ["git", "config", "--get", "remote.origin.url"]:
            return subprocess.CompletedProcess(
                command, 0, "https://github.com/whiteguo233/OpenBiliClaw.git\n", ""
            )
        if command == ["git", "status", "--porcelain"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command == ["git", "rev-parse", "--git-dir"]:
            return subprocess.CompletedProcess(command, 0, ".git\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(service, "_run_command", _run_command)

    status_code, payload = await service.request_apply(tag="backend-v0.3.92")

    assert status_code == 409
    assert payload["state"] == "blocked"
    assert payload["reason"] == "merge_or_rebase_in_progress"


@pytest.mark.asyncio
async def test_request_apply_reports_missing_tag_and_diverged_branch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    service = updater.AutoUpdateService(enabled=False)
    rev_parse_calls = 0

    async def _run_command(command, _root, *, timeout):
        nonlocal rev_parse_calls
        if command == ["git", "config", "--get", "remote.origin.url"]:
            return subprocess.CompletedProcess(
                command, 0, "https://github.com/whiteguo233/OpenBiliClaw.git\n", ""
            )
        if command == ["git", "status", "--porcelain"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command == ["git", "rev-parse", "--git-dir"]:
            return subprocess.CompletedProcess(command, 0, ".git\n", "")
        if command == ["git", "fetch", "--force", "--tags", "origin"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command == ["git", "rev-parse", "--verify", "backend-v0.3.92^{commit}"]:
            rev_parse_calls += 1
            return subprocess.CompletedProcess(
                command,
                1 if rev_parse_calls == 1 else 0,
                "abc123\n" if rev_parse_calls > 1 else "",
                "",
            )
        if command == ["git", "merge-base", "--is-ancestor", "HEAD", "backend-v0.3.92"]:
            return subprocess.CompletedProcess(command, 1, "", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(service, "_run_command", _run_command)

    missing_status, missing_payload = await service.request_apply(tag="backend-v0.3.92")
    diverged_status, diverged_payload = await service.request_apply(tag="backend-v0.3.92")

    assert missing_status == 409
    assert missing_payload["reason"] == "missing_target_tag"
    assert diverged_status == 409
    assert diverged_payload["reason"] == "branch_not_fast_forwardable"


@pytest.mark.asyncio
async def test_successful_apply_fetches_merges_syncs_and_publishes_restart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(updater.shutil, "which", lambda command: f"/tools/{command}")
    calls: list[list[str]] = []
    events: list[dict[str, object]] = []
    restarted = False

    async def _publish(event: dict[str, object]) -> None:
        events.append(event)

    async def _run_command(command, _root, *, timeout):
        calls.append(list(command))
        if command == ["git", "config", "--get", "remote.origin.url"]:
            return subprocess.CompletedProcess(
                command, 0, "https://github.com/whiteguo233/OpenBiliClaw.git\n", ""
            )
        if command == ["git", "rev-parse", "--git-dir"]:
            return subprocess.CompletedProcess(command, 0, ".git\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    def _restart() -> None:
        nonlocal restarted
        restarted = True

    service = updater.AutoUpdateService(enabled=False, event_publisher=_publish)
    monkeypatch.setattr(service, "_run_command", _run_command)
    monkeypatch.setattr(service, "_restart_process", _restart)

    status_code, payload = await service.request_apply(tag="backend-v0.3.92")
    if service._apply_task is not None:
        await asyncio.wait_for(service._apply_task, timeout=0.5)

    assert status_code == 202
    assert payload["accepted"] is True
    assert ["git", "fetch", "--force", "--tags", "origin"] in calls
    assert ["git", "merge", "--ff-only", "backend-v0.3.92"] in calls
    assert ["uv", "sync", "--no-install-project", "--inexact"] in calls
    assert restarted is True
    assert events == [{"type": "backend_restart_pending", "latest_tag": "backend-v0.3.92"}]


@pytest.mark.asyncio
async def test_run_command_uses_async_subprocess_exec(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def _unexpected_subprocess_run(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("subprocess.run must not be used inside _run_command")

    class _FakeProcess:
        returncode = 7

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"stdout text\n", b"stderr text\n"

        def kill(self) -> None:
            raise AssertionError("process should not be killed")

    async def _create_subprocess_exec(
        *args: str,
        **kwargs: object,
    ) -> _FakeProcess:
        calls.append((args, kwargs))
        return _FakeProcess()

    monkeypatch.setattr(updater.subprocess, "run", _unexpected_subprocess_run)
    monkeypatch.setattr(updater.asyncio, "create_subprocess_exec", _create_subprocess_exec)

    service = updater.AutoUpdateService(enabled=False)
    result = await service._run_command(["git", "status"], tmp_path, timeout=5)

    assert result.args == ["git", "status"]
    assert result.returncode == 7
    assert result.stdout == "stdout text\n"
    assert result.stderr == "stderr text\n"
    assert calls == [
        (
            ("git", "status"),
            {
                "cwd": tmp_path,
                "stdout": updater.subprocess.PIPE,
                "stderr": updater.subprocess.PIPE,
            },
        )
    ]


@pytest.mark.parametrize(
    ("mode", "proxy", "expected"),
    [
        ("direct", None, ["git", "fetch", "origin"]),
        ("system", None, ["git", "fetch", "origin"]),
        (
            "custom",
            "http://127.0.0.1:7890",
            ["git", "-c", "http.proxy=http://127.0.0.1:7890", "fetch", "origin"],
        ),
    ],
)
@pytest.mark.asyncio
async def test_proxy_mode_matrix_controls_git_command_only_for_custom(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    mode: str,
    proxy: str | None,
    expected: list[str],
) -> None:
    from openbiliclaw import network

    monkeypatch.setattr(network, "_outbound_mode", mode)
    monkeypatch.setattr(network, "_outbound_proxy", proxy)
    captured: list[list[str]] = []
    service = updater.AutoUpdateService()

    async def _run_command(command, _root, *, timeout):
        captured.append(list(command))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(service, "_run_command", _run_command)

    await service._run_git(["fetch", "origin"], tmp_path)

    assert captured == [expected]


@pytest.mark.parametrize("command", [["uv", "sync"], ["/venv/bin/python", "-m", "pip", "install"]])
@pytest.mark.parametrize(
    ("mode", "proxy"),
    [
        ("direct", None),
        ("system", None),
        ("custom", "http://127.0.0.1:7890"),
    ],
)
@pytest.mark.asyncio
async def test_proxy_mode_matrix_controls_dependency_environment_only_for_custom(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    mode: str,
    proxy: str | None,
    command: list[str],
) -> None:
    from openbiliclaw import network

    monkeypatch.setattr(network, "_outbound_mode", mode)
    monkeypatch.setattr(network, "_outbound_proxy", proxy)
    calls: list[dict[str, object]] = []

    class _FakeProcess:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b""

        def kill(self) -> None:
            raise AssertionError("process should not be killed")

    async def _create_subprocess_exec(*_args: str, **kwargs: object) -> _FakeProcess:
        calls.append(kwargs)
        return _FakeProcess()

    monkeypatch.setattr(updater.asyncio, "create_subprocess_exec", _create_subprocess_exec)

    await updater.AutoUpdateService()._run_command(command, tmp_path, timeout=5)

    assert len(calls) == 1
    if mode == "custom":
        env = calls[0].get("env")
        assert isinstance(env, dict)
        assert env["HTTP_PROXY"] == proxy
        assert env["HTTPS_PROXY"] == proxy
    else:
        assert "env" not in calls[0]


@pytest.mark.asyncio
async def test_apply_dependency_failure_publishes_backend_update_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(updater.shutil, "which", lambda command: f"/tools/{command}")
    events: list[dict[str, object]] = []

    async def _publish(event: dict[str, object]) -> None:
        events.append(event)

    async def _run_command(command, _root, *, timeout):
        if command == ["git", "config", "--get", "remote.origin.url"]:
            return subprocess.CompletedProcess(
                command,
                0,
                "https://github.com/whiteguo233/OpenBiliClaw.git\n",
                "",
            )
        if command == ["git", "rev-parse", "--git-dir"]:
            return subprocess.CompletedProcess(command, 0, ".git\n", "")
        if command == ["uv", "sync", "--no-install-project", "--inexact"]:
            return subprocess.CompletedProcess(command, 1, "", "dependency failed")
        return subprocess.CompletedProcess(command, 0, "", "")

    service = updater.AutoUpdateService(enabled=False, event_publisher=_publish)
    monkeypatch.setattr(service, "_run_command", _run_command)

    with caplog.at_level(logging.ERROR):
        status_code, payload = await service.request_apply(tag="backend-v0.3.92")
        if service._apply_task is not None:
            await asyncio.wait_for(service._apply_task, timeout=0.5)

    assert status_code == 202
    assert payload["accepted"] is True
    assert service.get_update_status()["state"] == "error"
    assert service.get_update_status()["reason"] == "dependency_sync_failed"
    assert service.get_update_status()["last_error"] == (
        "依赖同步失败(uv,退出码 1);请查看后端日志中的完整错误"
    )
    assert "dependency failed" in caplog.text
    assert events == [{"type": "backend_update_failed", "reason": "dependency_sync_failed"}]


@pytest.mark.parametrize(
    ("exc", "expected_reason", "detail_fragment"),
    [
        (FileNotFoundError(2, "No such file or directory", "git"), "git_unavailable", "git"),
        (
            subprocess.TimeoutExpired(["git", "status"], 30),
            "git_command_timeout",
            "timed out",
        ),
    ],
)
@pytest.mark.asyncio
async def test_classify_git_guard_execution_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    exc: Exception,
    expected_reason: str,
    detail_fragment: str,
) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    service = updater.AutoUpdateService()

    async def _run_command(command, _root, *, timeout):
        raise exc

    monkeypatch.setattr(service, "_run_command", _run_command)

    status_code, payload = await service.request_apply(tag="backend-v0.3.140")

    assert status_code == 409
    assert payload["reason"] == expected_reason
    assert detail_fragment in str(service.get_update_status()["last_error"])


@pytest.mark.asyncio
async def test_classify_uv_lock_checkout_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    service = updater.AutoUpdateService()
    calls: list[list[str]] = []

    async def _run_command(command, _root, *, timeout):
        calls.append(list(command))
        return subprocess.CompletedProcess(command, 1, "", "uv.lock checkout refused")

    monkeypatch.setattr(service, "_run_command", _run_command)

    await service._apply_update_to_tag("backend-v0.3.140")

    assert service.get_update_status()["reason"] == "worktree_prepare_failed"
    assert "uv.lock checkout refused" in str(service.get_update_status()["last_error"])
    assert calls == [["git", "checkout", "--", "uv.lock"]]


@pytest.mark.asyncio
async def test_classify_merge_index_lock_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    service = updater.AutoUpdateService()

    async def _run_command(command, _root, *, timeout):
        if command == ["git", "merge", "--ff-only", "backend-v0.3.140"]:
            return subprocess.CompletedProcess(
                command,
                128,
                "",
                "fatal: Unable to create '.git/index.lock': File exists.",
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(service, "_run_command", _run_command)

    await service._apply_update_to_tag("backend-v0.3.140")

    assert service.get_update_status()["reason"] == "git_worktree_locked"
    assert "index.lock" in str(service.get_update_status()["last_error"])


def test_install_command_uses_uv_only_when_available(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")
    monkeypatch.setattr(updater.shutil, "which", lambda command: f"/tools/{command}")

    assert updater.AutoUpdateService._detect_install_command(tmp_path) == [
        "uv",
        "sync",
        "--no-install-project",
        "--inexact",
    ]


def test_install_command_falls_back_to_running_python_when_uv_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        """\
[project]
name = "example"
version = "1.0.0"
dependencies = ["httpx>=0.27", "Pillow>=10; python_version >= '3.11'"]
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(updater.shutil, "which", lambda _command: None)
    monkeypatch.setattr(updater.sys, "executable", "/venv/bin/python")

    assert updater.AutoUpdateService._detect_install_command(tmp_path) == [
        "/venv/bin/python",
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-input",
        "httpx>=0.27",
        "Pillow>=10; python_version >= '3.11'",
    ]


@pytest.mark.parametrize(
    ("caller_runtime_key", "with_external_config"),
    [
        (None, False),
        (None, True),
        ("OPENBILICLAW_FULL_WORKER", False),
        ("OPENBILICLAW_RECOMMENDATION_SOCK", False),
        ("OPENBILICLAW_RECOMMENDATION_PORT", False),
    ],
)
def test_restart_process_keeps_env_provenance(
    monkeypatch: pytest.MonkeyPatch, caller_runtime_key: str | None, with_external_config: bool
) -> None:
    from openbiliclaw.config import Config
    from openbiliclaw.runtime.autostart import guards

    for key in tuple(os.environ):
        if key.startswith("OPENBILICLAW_") or key in {"GOOGLE_API_KEY", "GEMINI_API_KEY"}:
            monkeypatch.delenv(key)
    external_env = {
        "OPENBILICLAW_PROJECT_ROOT": "/caller/profile",
        "PATH": "/caller/bin",
    }
    config_env = {
        "OPENBILICLAW_API_AUTH_PASSWORD": "test-only-password",
        "OPENBILICLAW_LLM_DEFAULT_PROVIDER": "ollama",
        "GOOGLE_API_KEY": "test-only-key",
    }
    if with_external_config:
        external_env.update(config_env)
    runtime_env = {
        "OPENBILICLAW_FULL_WORKER": "1",
        "OPENBILICLAW_IMAGE_SERVICE_URL": "http://127.0.0.1:8421",
        "OPENBILICLAW_RECOMMENDATION_SOCK": "/tmp/recommendation.sock",
        "OPENBILICLAW_RECOMMENDATION_PORT": "8422",
        "OPENBILICLAW_RUNTIME_DATA_DIR": "/caller/profile/data",
    }
    if caller_runtime_key is not None:
        external_env[caller_runtime_key] = runtime_env[caller_runtime_key]
    for key, value in external_env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(guards, "_startup_env_keys", None)
    guards.snapshot_startup_env()
    for key, value in runtime_env.items():
        monkeypatch.setenv(key, value)
    live_env = dict(os.environ)
    restart_envs: list[dict[str, str]] = []

    def capture_exec(_executable: str, _argv: list[str], env: dict[str, str] | None = None) -> None:
        restart_envs.append(dict(os.environ if env is None else env))
        raise OSError("simulated restart failure")

    monkeypatch.setattr(updater.os, "execv", capture_exec)
    monkeypatch.setattr(updater.os, "execve", capture_exec)
    with pytest.raises(OSError, match="simulated restart failure"):
        updater.AutoUpdateService._restart_process()

    assert len(restart_envs) == 1
    restart_env = restart_envs[0]
    assert {key: restart_env.get(key) for key in external_env} == external_env
    assert not (runtime_env.keys() - external_env.keys()) & restart_env.keys()
    # A failed exec must leave the running backend's wiring intact.
    changed_keys = {
        key
        for key in live_env.keys() | os.environ.keys()
        if live_env.get(key) != os.environ.get(key)
    }
    assert not changed_keys

    # Model the replacement interpreter: only inherited inputs enter its snapshot.
    monkeypatch.setattr(os, "environ", restart_env)
    monkeypatch.setattr(guards, "_startup_env_keys", None)
    guards.snapshot_startup_env()
    expected_managed = set(config_env) if with_external_config else set()
    if caller_runtime_key is not None:
        expected_managed.add(caller_runtime_key)
    assert guards.active_env_managed_inputs(Config()) == sorted(expected_managed)


def test_restart_process_uses_module_entrypoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, list[str]]] = []
    restart_envs: list[dict[str, str]] = []
    monkeypatch.setattr(updater.sys, "executable", r"C:\OpenBiliClaw\.venv\Scripts\python.exe")
    monkeypatch.setattr(
        updater.sys,
        "argv",
        [r"C:\OpenBiliClaw\.venv\Scripts\openbiliclaw.exe", "serve-api", "--port", "8420"],
    )
    monkeypatch.setattr(
        updater.os,
        "execve",
        lambda executable, argv, env: (
            calls.append((executable, list(argv))),
            restart_envs.append(env),
        ),
    )

    updater.AutoUpdateService._restart_process()

    assert calls == [
        (
            r"C:\OpenBiliClaw\.venv\Scripts\python.exe",
            [
                r"C:\OpenBiliClaw\.venv\Scripts\python.exe",
                "-m",
                "openbiliclaw.cli",
                "serve-api",
                "--port",
                "8420",
            ],
        )
    ]
    # No startup snapshot is available: preserve even unknown inputs.
    assert len(restart_envs) == 1
    assert restart_envs[0] == dict(os.environ)


@pytest.mark.asyncio
async def test_apply_restart_failure_logs_and_publishes_backend_update_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    events: list[dict[str, object]] = []

    async def _publish(event: dict[str, object]) -> None:
        events.append(event)

    async def _run_command(command, _root, *, timeout):
        if command == ["git", "config", "--get", "remote.origin.url"]:
            return subprocess.CompletedProcess(
                command,
                0,
                "https://github.com/whiteguo233/OpenBiliClaw.git\n",
                "",
            )
        if command == ["git", "rev-parse", "--git-dir"]:
            return subprocess.CompletedProcess(command, 0, ".git\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    def _restart() -> None:
        raise RuntimeError("exec failed")

    service = updater.AutoUpdateService(enabled=False, event_publisher=_publish)
    monkeypatch.setattr(service, "_run_command", _run_command)
    monkeypatch.setattr(service, "_restart_process", _restart)

    with caplog.at_level(logging.ERROR):
        status_code, payload = await service.request_apply(tag="backend-v0.3.92")
        if service._apply_task is not None:
            await asyncio.wait_for(service._apply_task, timeout=0.5)

    assert status_code == 202
    assert payload["accepted"] is True
    assert service.get_update_status()["state"] == "error"
    assert service.get_update_status()["reason"] == "restart_failed"
    assert "Auto-update restart failed" in caplog.text
    assert events == [
        {"type": "backend_restart_pending", "latest_tag": "backend-v0.3.92"},
        {"type": "backend_update_failed", "reason": "restart_failed"},
    ]


@pytest.mark.asyncio
async def test_request_apply_refuses_frozen_install_even_with_git_checkout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A PyInstaller bundle co-located with a git checkout must not self-apply.

    entry.py points OPENBILICLAW_PROJECT_ROOT at the shared ~/OpenBiliClaw,
    which an AI / one-line install populates as a real git repo. Without the
    explicit frozen guard the bundle would fast-forward someone else's source
    and restart-loop on its own bundled (old) code.
    """
    import sys

    (tmp_path / ".git").mkdir()
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    calls: list[list[str]] = []

    async def _run_command(command, _root, *, timeout):
        calls.append(list(command))
        return subprocess.CompletedProcess(command, 0, "", "")

    service = updater.AutoUpdateService(enabled=True)
    monkeypatch.setattr(service, "_run_command", _run_command)

    status_code, payload = await service.request_apply(tag="backend-v0.3.92")

    assert status_code == 409
    assert payload["state"] == "unsupported"
    assert payload["reason"] == "unsupported_install_mode"
    # The guard short-circuits before any git command — nothing is mutated.
    assert calls == []


@pytest.mark.asyncio
async def test_check_and_update_if_due_checks_but_never_applies_on_frozen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Frozen bundles poll for new installers (even with the toggle off) but never apply."""
    import sys

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    service = updater.AutoUpdateService(enabled=False)
    check_calls: list[int] = []

    async def _fake_check_now() -> dict[str, object]:
        check_calls.append(1)
        service._state = "update_available"
        service._latest_tag = "desktop-v0.3.119"
        service._latest_remote_version = "0.3.119"
        return service.get_update_status()

    async def _fail_apply(*_args: object, **_kwargs: object) -> tuple[int, dict[str, object]]:
        raise AssertionError("request_apply must never run on a frozen install")

    monkeypatch.setattr(service, "check_now", _fake_check_now)
    monkeypatch.setattr(service, "request_apply", _fail_apply)

    result = await service.check_and_update_if_due()

    assert check_calls == [1]
    assert result["checked"] is True
    assert result["updated"] is False
    assert result["reason"] == "unsupported_install_mode"
    # The discovered installer stays visible to the status APIs.
    assert service.get_update_status()["state"] == "update_available"


@pytest.mark.asyncio
async def test_check_and_update_now_surfaces_update_without_applying_on_frozen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct check_and_update_now callers also never reach apply on frozen."""
    import sys

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    _FakeAsyncClient.pages = {1: [{"name": "desktop-v0.3.119"}], 2: []}
    monkeypatch.setattr(openbiliclaw, "__version__", "0.3.118")
    service = updater.AutoUpdateService(enabled=True)

    async def _fail_apply(*_args: object, **_kwargs: object) -> tuple[int, dict[str, object]]:
        raise AssertionError("request_apply must never run on a frozen install")

    monkeypatch.setattr(service, "request_apply", _fail_apply)

    result = await service.check_and_update_now()

    assert result["checked"] is True
    assert result["updated"] is False
    assert result["reason"] == "unsupported_install_mode"
    assert result["remote_version"] == "desktop-v0.3.119"


def test_background_loop_enabled_for_frozen_even_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """The check-reminder loop runs unconditionally on frozen bundles."""
    import sys

    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    disabled = updater.AutoUpdateService(enabled=False)
    assert disabled._background_loop_enabled() is False

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert disabled._background_loop_enabled() is True
    assert updater.AutoUpdateService(enabled=True)._background_loop_enabled() is True


@pytest.mark.parametrize(
    ("tag", "expected_version"),
    [
        ("desktop-v0.3.119", (0, 3, 119)),
        ("desktop-v0.3.119-rc1", None),  # prerelease excluded by default
        ("backend-v0.3.119", None),
        ("v0.3.119", None),  # legacy source tags are never installer candidates
        ("0.3.119", None),
        ("extension-v0.3.77", None),
        ("", None),
    ],
)
def test_parse_desktop_candidate_only_accepts_desktop_tags(
    tag: str,
    expected_version: tuple[int, ...] | None,
) -> None:
    candidate = updater._parse_desktop_candidate(tag)
    assert (candidate.version if candidate else None) == expected_version


@pytest.mark.asyncio
async def test_frozen_check_tracks_desktop_installer_tags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Frozen installs compare against desktop-v* tags, not backend-v* source tags."""
    import sys

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    # A newer backend source tag exists but no newer installer — must stay quiet.
    _FakeAsyncClient.pages = {
        1: [
            {"name": "backend-v0.3.121"},
            {"name": "desktop-v0.3.119"},
            {"name": "extension-v0.3.77"},
            {"name": "v0.3.120"},
        ],
        2: [],
    }
    monkeypatch.setattr(openbiliclaw, "__version__", "0.3.119")
    service = updater.AutoUpdateService(enabled=False)

    backend = await service.check_now()

    assert backend["state"] == "up_to_date"
    assert backend["latest_tag"] == "desktop-v0.3.119"

    # And when a newer installer exists it is surfaced as update_available.
    _FakeAsyncClient.pages = {
        1: [{"name": "desktop-v0.3.120"}, {"name": "backend-v0.3.119"}],
        2: [],
    }
    backend = await service.check_now()

    assert backend["state"] == "update_available"
    assert backend["latest_tag"] == "desktop-v0.3.120"
    assert backend["latest_version"] == "0.3.120"


def test_adopt_status_from_carries_settled_check_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A config-save rebuild keeps the freshly-fetched update status."""
    from datetime import UTC, datetime

    (tmp_path / ".git").mkdir()
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))

    old = updater.AutoUpdateService(enabled=True)
    old._state = "update_available"
    old._reason = "none"
    old._latest_tag = "backend-v0.3.118"
    old._latest_remote_version = "0.3.118"
    old._last_check_at = datetime(2026, 6, 11, 4, 43, 30, tzinfo=UTC)

    new = updater.AutoUpdateService(enabled=False)
    new.adopt_status_from(old)

    status = new.get_update_status()
    assert status["state"] == "update_available"
    assert status["latest_tag"] == "backend-v0.3.118"
    assert status["latest_version"] == "0.3.118"
    assert status["last_check_at"] == "2026-06-11T04:43:30+00:00"


def test_adopt_status_from_skips_transient_state_but_keeps_versions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """An in-flight apply on the old instance is not re-stamped onto the new one."""
    from datetime import UTC, datetime

    (tmp_path / ".git").mkdir()
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))

    old = updater.AutoUpdateService(enabled=True)
    old._state = "applying"
    old._reason = "none"
    old._latest_tag = "backend-v0.3.118"
    old._latest_remote_version = "0.3.118"
    old._last_check_at = datetime(2026, 6, 11, 4, 43, 30, tzinfo=UTC)

    new = updater.AutoUpdateService(enabled=False)
    new.adopt_status_from(old)

    status = new.get_update_status()
    # Transient "applying" is not adopted — a disabled fresh service derives
    # "disabled" — but the version/check metadata still carries forward.
    assert status["state"] == "disabled"
    assert status["latest_tag"] == "backend-v0.3.118"
    assert status["last_check_at"] == "2026-06-11T04:43:30+00:00"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/whiteguo233/OpenBiliClaw.git", "github.com/whiteguo233/openbiliclaw"),
        ("https://github.com/whiteguo233/OpenBiliClaw", "github.com/whiteguo233/openbiliclaw"),
        ("https://github.com/whiteguo233/OpenBiliClaw/", "github.com/whiteguo233/openbiliclaw"),
        ("git@github.com:whiteguo233/OpenBiliClaw.git", "github.com/whiteguo233/openbiliclaw"),
        ("git@github.com:whiteguo233/OpenBiliClaw", "github.com/whiteguo233/openbiliclaw"),
        (
            "ssh://git@github.com/whiteguo233/OpenBiliClaw.git",
            "github.com/whiteguo233/openbiliclaw",
        ),
        ("HTTPS://GitHub.com/WhiteGuo233/OpenBiliClaw.GIT", "github.com/whiteguo233/openbiliclaw"),
        # Mirror wrappers must NOT collapse to the wrapped GitHub path — a
        # mirror user allowlists the mirror URL verbatim in config instead,
        # and the canonical form is stable so it still matches itself.
        (
            "https://gh-proxy.com/https://github.com/whiteguo233/OpenBiliClaw.git",
            "gh-proxy.com/https://github.com/whiteguo233/openbiliclaw",
        ),
        ("/Users/someone/workspace/OpenBiliClaw", "/users/someone/workspace/openbiliclaw"),
        ("", ""),
    ],
)
def test_canonicalize_remote_url(url: str, expected: str) -> None:
    assert updater._canonicalize_remote_url(url) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "ssh://git@ssh.github.com:443/whiteguo233/OpenBiliClaw.git",
            "github.com/whiteguo233/openbiliclaw",
        ),
        (
            "git@ssh.github.com:whiteguo233/OpenBiliClaw.git",
            "github.com/whiteguo233/openbiliclaw",
        ),
    ],
)
def test_canonicalize_official_github_ssh_over_443_host(url: str, expected: str) -> None:
    assert updater._canonicalize_remote_url(url) == expected


@pytest.mark.asyncio
async def test_remote_guard_accepts_multiple_effective_official_urls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    service = updater.AutoUpdateService()
    calls: list[list[str]] = []

    async def _run_command(command, _root, *, timeout):
        calls.append(list(command))
        if command == ["git", "config", "--get", "remote.origin.url"]:
            return subprocess.CompletedProcess(command, 2, "", "")
        if command == ["git", "ls-remote", "--get-url", "origin"]:
            return subprocess.CompletedProcess(
                command, 0, "https://github.com/whiteguo233/OpenBiliClaw.git\n", ""
            )
        if command == ["git", "remote", "get-url", "--all", "origin"]:
            return subprocess.CompletedProcess(
                command,
                0,
                "https://github.com/whiteguo233/OpenBiliClaw.git\n"
                "git@github.com:whiteguo233/OpenBiliClaw.git\n",
                "",
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(service, "_run_command", _run_command)

    assert await service._check_apply_guards("backend-v0.3.92") == ""
    assert ["git", "ls-remote", "--get-url", "origin"] in calls
    assert ["git", "remote", "get-url", "--all", "origin"] in calls


@pytest.mark.asyncio
async def test_remote_guard_rejects_one_untrusted_url_among_multiple_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    service = updater.AutoUpdateService()

    async def _run_command(command, _root, *, timeout):
        if command == ["git", "config", "--get", "remote.origin.url"]:
            return subprocess.CompletedProcess(command, 2, "", "")
        if command == ["git", "ls-remote", "--get-url", "origin"]:
            return subprocess.CompletedProcess(
                command, 0, "https://github.com/whiteguo233/OpenBiliClaw.git\n", ""
            )
        if command == ["git", "remote", "get-url", "--all", "origin"]:
            return subprocess.CompletedProcess(
                command,
                0,
                "https://github.com/whiteguo233/OpenBiliClaw.git\n"
                "https://mirror.example/whiteguo233/OpenBiliClaw.git\n",
                "",
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(service, "_run_command", _run_command)

    assert await service._check_apply_guards("backend-v0.3.92") == "untrusted_remote"


@pytest.mark.asyncio
async def test_remote_guard_rejects_insteadof_rewritten_effective_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    service = updater.AutoUpdateService()

    async def _run_command(command, _root, *, timeout):
        if command == ["git", "config", "--get", "remote.origin.url"]:
            return subprocess.CompletedProcess(
                command, 0, "https://github.com/whiteguo233/OpenBiliClaw.git\n", ""
            )
        if command == ["git", "ls-remote", "--get-url", "origin"]:
            return subprocess.CompletedProcess(
                command,
                0,
                "https://mirror.example/https://github.com/whiteguo233/OpenBiliClaw.git\n",
                "",
            )
        if command == ["git", "remote", "get-url", "--all", "origin"]:
            return subprocess.CompletedProcess(
                command, 0, "https://github.com/whiteguo233/OpenBiliClaw.git\n", ""
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(service, "_run_command", _run_command)

    assert await service._check_apply_guards("backend-v0.3.92") == "untrusted_remote"


@pytest.mark.asyncio
async def test_remote_guard_rejects_mirror_wrapped_github_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    service = updater.AutoUpdateService()
    mirror = "https://gh-proxy.com/https://github.com/whiteguo233/OpenBiliClaw.git"

    async def _run_command(command, _root, *, timeout):
        if command == ["git", "config", "--get", "remote.origin.url"]:
            return subprocess.CompletedProcess(command, 0, f"{mirror}\n", "")
        if command == ["git", "ls-remote", "--get-url", "origin"]:
            return subprocess.CompletedProcess(command, 0, f"{mirror}\n", "")
        if command == ["git", "remote", "get-url", "--all", "origin"]:
            return subprocess.CompletedProcess(command, 0, f"{mirror}\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(service, "_run_command", _run_command)

    assert await service._check_apply_guards("backend-v0.3.92") == "untrusted_remote"


@pytest.mark.parametrize(
    "remote_url",
    [
        "https://github.com/whiteguo233/OpenBiliClaw",  # .git-less manual clone
        "git@github.com:whiteguo233/OpenBiliClaw",  # .git-less ssh clone
        "ssh://git@github.com/whiteguo233/OpenBiliClaw.git",  # explicit ssh scheme
    ],
)
@pytest.mark.asyncio
async def test_request_apply_accepts_equivalent_remote_spellings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    remote_url: str,
) -> None:
    """Clones made without the .git suffix (or via ssh://) must not be
    rejected as untrusted — exact-string matching used to block them forever."""
    (tmp_path / ".git").mkdir()
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    restarted = False

    async def _run_command(command, _root, *, timeout):
        if command == ["git", "config", "--get", "remote.origin.url"]:
            return subprocess.CompletedProcess(command, 0, f"{remote_url}\n", "")
        if command == ["git", "status", "--porcelain"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command == ["git", "rev-parse", "--git-dir"]:
            return subprocess.CompletedProcess(command, 0, ".git\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    def _restart() -> None:
        nonlocal restarted
        restarted = True

    service = updater.AutoUpdateService(enabled=False)
    monkeypatch.setattr(service, "_run_command", _run_command)
    monkeypatch.setattr(service, "_restart_process", _restart)

    status_code, payload = await service.request_apply(tag="backend-v0.3.92")
    if service._apply_task is not None:
        await asyncio.wait_for(service._apply_task, timeout=0.5)

    assert status_code == 202
    assert payload["accepted"] is True
    assert restarted is True


@pytest.mark.asyncio
async def test_request_apply_untrusted_remote_logs_url_and_sets_last_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The guard path used to be fully silent — no log line, empty last_error."""
    (tmp_path / ".git").mkdir()
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    service = updater.AutoUpdateService(enabled=False)

    async def _run_command(command, _root, *, timeout):
        if command == ["git", "config", "--get", "remote.origin.url"]:
            return subprocess.CompletedProcess(
                command, 0, "https://github.com/someone-else/OpenBiliClaw.git\n", ""
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(service, "_run_command", _run_command)

    with caplog.at_level(logging.WARNING, logger="openbiliclaw.runtime.updater"):
        status_code, payload = await service.request_apply(tag="backend-v0.3.92")

    assert status_code == 409
    # ``reason`` stays the stable machine code (frontends map it to i18n text),
    # but ``last_error`` now carries the actual rejected remote URL + fix
    # command so the status card is self-diagnosing instead of pointing users
    # at the backend log.
    assert payload["reason"] == "untrusted_remote"
    last_error = service.get_update_status()["last_error"]
    assert last_error != "untrusted_remote"
    assert "someone-else/OpenBiliClaw" in last_error
    assert "git remote set-url origin" in last_error
    assert any("someone-else/OpenBiliClaw" in record.getMessage() for record in caplog.records), (
        "the rejected remote URL must appear in the log"
    )


@pytest.mark.asyncio
async def test_request_apply_credentialed_remote_redacts_token_in_last_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """An embedded token is rejected, but must never leak into the UI detail."""
    (tmp_path / ".git").mkdir()
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    service = updater.AutoUpdateService(enabled=False)

    async def _run_command(command, _root, *, timeout):
        if command == ["git", "config", "--get", "remote.origin.url"]:
            return subprocess.CompletedProcess(
                command,
                0,
                "https://ghp_secretTOKEN@github.com/whiteguo233/OpenBiliClaw.git\n",
                "",
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(service, "_run_command", _run_command)

    status_code, payload = await service.request_apply(tag="backend-v0.3.92")

    assert status_code == 409
    assert payload["reason"] == "untrusted_remote"
    last_error = service.get_update_status()["last_error"]
    assert "ghp_secretTOKEN" not in last_error
    assert "github.com/whiteguo233/OpenBiliClaw" in last_error


@pytest.mark.asyncio
async def test_request_apply_origin_exists_without_url_suggests_set_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Origin section present but url-less — the exact reported contradiction.

    ``git config --get remote.origin.url`` returns empty while ``git remote add
    origin`` answers "already exists", so the old "运行:git remote add origin"
    hint was a dead end. The ``git remote get-url origin`` probe (exit 0, empty)
    marks origin-exists-but-blank, so the fix must be ``set-url``, never ``add``.
    """
    (tmp_path / ".git").mkdir()
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    service = updater.AutoUpdateService(enabled=False)

    async def _run_command(command, _root, *, timeout):
        if command == ["git", "config", "--get", "remote.origin.url"]:
            return subprocess.CompletedProcess(command, 1, "", "")
        if command == ["git", "remote", "get-url", "origin"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(service, "_run_command", _run_command)

    status_code, payload = await service.request_apply(tag="backend-v0.3.92")

    assert status_code == 409
    assert payload["state"] == "blocked"
    assert payload["reason"] == "origin_remote_unusable"
    last_error = service.get_update_status()["last_error"]
    assert "remote set-url origin" in last_error
    assert "remote add origin" not in last_error


@pytest.mark.asyncio
async def test_request_apply_no_origin_remote_suggests_remote_add(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Genuinely no origin — get-url errors "No such remote" → suggest add."""
    (tmp_path / ".git").mkdir()
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    service = updater.AutoUpdateService(enabled=False)

    async def _run_command(command, _root, *, timeout):
        if command == ["git", "config", "--get", "remote.origin.url"]:
            return subprocess.CompletedProcess(command, 1, "", "")
        if command == ["git", "remote", "get-url", "origin"]:
            return subprocess.CompletedProcess(command, 2, "", "error: No such remote 'origin'")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(service, "_run_command", _run_command)

    status_code, payload = await service.request_apply(tag="backend-v0.3.92")

    assert status_code == 409
    assert payload["reason"] == "origin_remote_unusable"
    last_error = service.get_update_status()["last_error"]
    assert "remote add origin" in last_error
    assert "set-url" not in last_error


@pytest.mark.asyncio
async def test_request_apply_dubious_ownership_points_at_safe_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Windows dubious ownership: config read fails with a git *fatal*, not a
    missing remote. Surface safe.directory, never the wrong "remote add" hint."""
    (tmp_path / ".git").mkdir()
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    service = updater.AutoUpdateService(enabled=False)

    fatal = (
        f"fatal: detected dubious ownership in repository at '{tmp_path}'\n"
        f"To add an exception for this directory, call:\n"
        f"\tgit config --global --add safe.directory {tmp_path}"
    )

    async def _run_command(command, _root, *, timeout):
        if command == ["git", "config", "--get", "remote.origin.url"]:
            return subprocess.CompletedProcess(command, 128, "", fatal)
        if command == ["git", "remote", "get-url", "origin"]:
            return subprocess.CompletedProcess(command, 128, "", fatal)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(service, "_run_command", _run_command)

    status_code, payload = await service.request_apply(tag="backend-v0.3.92")

    assert status_code == 409
    assert payload["reason"] == "origin_remote_unusable"
    last_error = service.get_update_status()["last_error"]
    assert "safe.directory" in last_error
    assert "remote add origin" not in last_error


@pytest.mark.parametrize(
    ("root_text", "config_error", "probe_code", "probe_error", "command_prefix"),
    [
        (
            "/Users/Jane Doe/OpenBiliClaw",
            "dubious ownership",
            128,
            "dubious ownership",
            "git config --global --add safe.directory",
        ),
        (
            r"C:\Users\Jane Doe\OpenBiliClaw",
            "dubious ownership",
            128,
            "dubious ownership",
            "git config --global --add safe.directory",
        ),
        ("/Users/Jane Doe/OpenBiliClaw", "", 2, "No such remote 'origin'", "remote add"),
        (
            r"C:\Users\Jane Doe\OpenBiliClaw",
            "",
            2,
            "No such remote 'origin'",
            "remote add",
        ),
        ("/Users/Jane Doe/OpenBiliClaw", "", 0, "", "remote set-url"),
        (r"C:\Users\Jane Doe\OpenBiliClaw", "", 0, "", "remote set-url"),
    ],
)
@pytest.mark.asyncio
async def test_apply_guidance_quotes_repository_paths_with_spaces(
    monkeypatch: pytest.MonkeyPatch,
    root_text: str,
    config_error: str,
    probe_code: int,
    probe_error: str,
    command_prefix: str,
) -> None:
    root = Path(root_text)
    service = updater.AutoUpdateService()
    config_result = subprocess.CompletedProcess(
        ["git", "config", "--get", "remote.origin.url"],
        128 if config_error else 1,
        "",
        config_error,
    )

    async def _run_command(command, _root, *, timeout):
        return subprocess.CompletedProcess(command, probe_code, "", probe_error)

    monkeypatch.setattr(service, "_run_command", _run_command)

    await service._refuse_unreadable_origin(root, config_result)

    assert command_prefix in service._guard_detail
    assert f'"{root}"' in service._guard_detail


def test_detect_install_mode_docker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Container marker wins over a co-located git checkout: the image is the
    code, so fast-forwarding a mounted checkout would not update the runtime."""
    (tmp_path / ".git").mkdir()
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("OPENBILICLAW_IN_CONTAINER", "1")
    assert updater.detect_install_mode() == "docker"


@pytest.mark.asyncio
async def test_request_apply_refuses_docker_install(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("OPENBILICLAW_IN_CONTAINER", "1")
    service = updater.AutoUpdateService(enabled=True)

    status_code, payload = await service.request_apply(tag="backend-v0.3.92")

    assert status_code == 409
    assert payload["state"] == "unsupported"
    assert payload["reason"] == "docker_install_mode"
    assert service.get_update_status()["last_error"] == "docker_install_mode"


@pytest.mark.asyncio
async def test_docker_install_polls_check_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Docker installs poll for new images regardless of the auto-apply toggle
    and never attempt an apply."""
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("OPENBILICLAW_IN_CONTAINER", "1")
    service = updater.AutoUpdateService(enabled=False)
    assert service._background_loop_enabled() is True

    applied: list[str] = []

    async def _check_now() -> dict[str, object]:
        return {
            "state": "update_available",
            "current_version": "0.3.152",
            "latest_tag": "backend-v0.3.153",
        }

    async def _request_apply(*, tag: str = ""):
        applied.append(tag)
        return 202, {}

    monkeypatch.setattr(service, "check_now", _check_now)
    monkeypatch.setattr(service, "request_apply", _request_apply)

    result = await service.check_and_update_if_due()

    assert result["checked"] is True
    assert result["updated"] is False
    assert result["reason"] == "docker_install_mode"
    assert applied == []


@pytest.mark.parametrize("component_exit", [0, 1])
async def test_free_component_is_reconciled_before_restart_and_failure_blocks_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, component_exit: int
) -> None:
    from openbiliclaw import sponsored_runtime_state

    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(
        sponsored_runtime_state,
        "current_sponsored_settings",
        lambda: sponsored_runtime_state.SponsoredRuntimeSettings(enabled=True),
    )
    service = updater.AutoUpdateService(enabled=False)
    calls: list[list[str]] = []
    restarted = []

    async def run(
        command: list[str], _root: Path, *, timeout: float = 30
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        code = component_exit if "openbiliclaw.runtime.sponsored_bundle" in command else 0
        return subprocess.CompletedProcess(command, code, "", "")

    monkeypatch.setattr(service, "_run_command", run)
    monkeypatch.setattr(service, "_detect_install_command", lambda _root: ["install-dependencies"])
    monkeypatch.setattr(service, "_restart_process", lambda: restarted.append(True))
    await service._apply_update_to_tag("backend-v0.3.233")
    install_index = calls.index(["install-dependencies"])
    component_index = next(
        i for i, c in enumerate(calls) if "openbiliclaw.runtime.sponsored_bundle" in c
    )
    assert component_index > install_index
    assert bool(restarted) is (component_exit == 0)
    if component_exit:
        assert service._reason == "sponsored_runtime_update_failed"
        assert "原组件已保留" in service._update_error
