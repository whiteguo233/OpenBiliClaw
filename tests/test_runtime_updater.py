from __future__ import annotations

import asyncio
import logging
import subprocess
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
async def test_fetch_latest_version_retries_without_tls_verify_on_cert_error() -> None:
    _FakeAsyncClient.error_by_verify = {
        True: httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
    }
    _FakeAsyncClient.pages = {1: [{"name": "backend-v0.3.92"}]}

    service = updater.AutoUpdateService()

    assert await service._fetch_latest_version() == "backend-v0.3.92"
    assert [kwargs.get("verify", True) for kwargs in _FakeAsyncClient.init_kwargs] == [
        True,
        False,
    ]


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
    assert ["uv", "sync"] in calls
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


@pytest.mark.asyncio
async def test_apply_dependency_failure_publishes_backend_update_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
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
        if command == ["uv", "sync"]:
            return subprocess.CompletedProcess(command, 1, "", "dependency failed")
        return subprocess.CompletedProcess(command, 0, "", "")

    service = updater.AutoUpdateService(enabled=False, event_publisher=_publish)
    monkeypatch.setattr(service, "_run_command", _run_command)

    status_code, payload = await service.request_apply(tag="backend-v0.3.92")
    if service._apply_task is not None:
        await asyncio.wait_for(service._apply_task, timeout=0.5)

    assert status_code == 202
    assert payload["accepted"] is True
    assert service.get_update_status()["state"] == "error"
    assert service.get_update_status()["reason"] == "dependency_sync_failed"
    assert events == [{"type": "backend_update_failed", "reason": "dependency_sync_failed"}]


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
