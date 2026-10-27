"""Regression tests for background child stdio redirection (pythonw fix).

Root cause: on Windows the desktop bundle runs ``cli start`` under
``pythonw.exe`` (no console). Python defaults to ``close_fds=True`` on
Windows, so children spawned without explicit stdout/stderr get no standard
handles at all — ``sys.stdout``/``sys.stderr`` are ``None`` and the child
dies silently on its first write.
"""

import io
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from openbiliclaw import cli as cli_module
from openbiliclaw import config as config_module
from openbiliclaw.config import Config
from openbiliclaw.storage.migration import _windows_runtime_lock


def _config_with_log_dir(log_dir: Path) -> Config:
    config = Config(data_dir="/nonexistent-data")
    config.logging.directory = str(log_dir)
    return config


def test_spawn_background_child_redirects_stdio_to_log_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_dir = tmp_path / "logs"
    captured: dict[str, Any] = {}
    sentinel = object()

    def fake_popen(*args: Any, **kwargs: Any) -> Any:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return sentinel

    monkeypatch.setattr(config_module, "load_config", lambda: _config_with_log_dir(log_dir))
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    env = {"FOO": "bar"}
    result = cli_module._spawn_background_child("worker", "openbiliclaw.worker", env)

    assert result is sentinel
    assert log_dir.is_dir()
    argv = captured["args"][0]
    assert argv == [sys.executable, "-m", "openbiliclaw.worker"]
    kwargs = captured["kwargs"]
    assert kwargs["cwd"] == os.getcwd()
    assert kwargs["env"] is env
    assert kwargs["stderr"] is subprocess.STDOUT
    stdout = kwargs["stdout"]
    assert Path(stdout.name) == log_dir / "child-worker.log"
    stdout.close()


def test_spawn_background_child_uses_name_in_log_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_dir = tmp_path / "logs"
    captured: dict[str, Any] = {}

    def fake_popen(*args: Any, **kwargs: Any) -> Any:
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(config_module, "load_config", lambda: _config_with_log_dir(log_dir))
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    cli_module._spawn_background_child("image-service", "openbiliclaw.image_service", {})

    stdout = captured["kwargs"]["stdout"]
    assert Path(stdout.name).name == "child-image-service.log"
    stdout.close()


def test_spawn_background_child_closes_log_file_when_popen_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_dir = tmp_path / "logs"
    opened: list[Any] = []
    real_open = open

    def tracking_open(*args: Any, **kwargs: Any) -> Any:
        handle = real_open(*args, **kwargs)
        opened.append(handle)
        return handle

    def failing_popen(*args: Any, **kwargs: Any) -> Any:
        raise OSError("spawn failed")

    monkeypatch.setattr(config_module, "load_config", lambda: _config_with_log_dir(log_dir))
    monkeypatch.setattr(subprocess, "Popen", failing_popen)
    monkeypatch.setattr("builtins.open", tracking_open)

    with pytest.raises(OSError, match="spawn failed"):
        cli_module._spawn_background_child("worker", "openbiliclaw.worker", {})

    assert opened and all(handle.closed for handle in opened)


class _LockingRecorder:
    """Fake ``msvcrt`` module recording locking calls."""

    LK_NBLCK = 1

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[int, int, int]] = []

    def locking(self, fd: int, mode: int, nbytes: int) -> None:
        self.calls.append((fd, mode, nbytes))
        if self.fail:
            raise OSError("locked by another instance")


class _BytesHandle(io.BytesIO):
    def fileno(self) -> int:
        return 42


class _PermissionErrorOnReadHandle(_BytesHandle):
    def read(self, size: int = -1) -> bytes:
        raise PermissionError("locked by another instance")


def test_windows_runtime_lock_success_marks_and_locks() -> None:
    handle = _BytesHandle()
    msvcrt = _LockingRecorder()

    assert _windows_runtime_lock(handle, msvcrt) is True
    assert not handle.closed
    assert msvcrt.calls == [(42, msvcrt.LK_NBLCK, 1)]
    handle.seek(0)
    assert handle.read() == b"\0"


def test_windows_runtime_lock_read_permission_error_returns_false() -> None:
    handle = _PermissionErrorOnReadHandle()
    msvcrt = _LockingRecorder()

    assert _windows_runtime_lock(handle, msvcrt) is False
    assert handle.closed
    assert msvcrt.calls == []


def test_windows_runtime_lock_locking_failure_returns_false() -> None:
    handle = _BytesHandle(b"\0")
    msvcrt = _LockingRecorder(fail=True)

    assert _windows_runtime_lock(handle, msvcrt) is False
    assert handle.closed
    assert msvcrt.calls == [(42, msvcrt.LK_NBLCK, 1)]
