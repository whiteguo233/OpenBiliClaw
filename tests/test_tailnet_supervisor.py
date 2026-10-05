from __future__ import annotations

import io
import json
import os
import stat
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from openbiliclaw.runtime import tailnet_supervisor as supervisor_module

_TEST_AUTH_CREDENTIAL = "tskey-" + "auth-privatebootstrap"
_TEST_OAUTH_CREDENTIAL = "tskey-" + "client-privatebootstrap"


@dataclass
class _TailnetSettings:
    enabled: bool = True
    hostname: str = "openbiliclaw-test"


@dataclass
class _Config:
    data_path: Path
    tailnet: _TailnetSettings


def _make_executable(path: Path, contents: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _make_fake_helper(tmp_path: Path) -> Path:
    source = r"""#!/usr/bin/env python3
import json
import os
import pathlib
import sys
import time


def is_auth_key_name(name):
    normalized = name.upper().replace("-", "_")
    return (
        "AUTH_KEY" in normalized
        or "AUTHKEY" in normalized.replace("_", "")
        or "CLIENT_SECRET" in normalized
    )


bootstrap_line = sys.stdin.readline()
bootstrap = json.loads(bootstrap_line)
capture = {
    "argv": sys.argv[1:],
    "bootstrap": bootstrap,
    "auth_env": {
        name: value for name, value in os.environ.items() if is_auth_key_name(name)
    },
}
pathlib.Path(os.environ["FAKE_CAPTURE"]).write_text(json.dumps(capture), encoding="utf-8")
print(json.dumps({"protocol": 1, "event": "starting"}), flush=True)
print(os.environ.get("FAKE_STDERR", "fake helper diagnostic"), file=sys.stderr, flush=True)

mode = os.environ.get("FAKE_MODE", "hold")
if mode == "crash":
    time.sleep(0.15)
    raise SystemExit(int(os.environ.get("FAKE_EXIT_CODE", "17")))

ready_event = {
    "protocol": 1,
    "event": "ready",
    "dns_name": "openbiliclaw-test.example.ts.net",
    "ips": ["100.64.0.8"],
    "port": 8420,
}
if os.environ.get("FAKE_EVENT_MESSAGE"):
    ready_event["message"] = os.environ["FAKE_EVENT_MESSAGE"]
ready_delay = float(os.environ.get("FAKE_READY_DELAY", "0"))
if ready_delay > 0:
    time.sleep(ready_delay)
print(json.dumps(ready_event), flush=True)
if mode == "exit":
    print(json.dumps({"protocol": 1, "event": "stopped"}), flush=True)
    raise SystemExit(0)

sys.stdin.read()
print(json.dumps({"protocol": 1, "event": "stopped", "message": "stdin EOF"}), flush=True)
"""
    return _make_executable(tmp_path / supervisor_module.tailnet_helper_basename(), source)


def _wait_for(predicate: Any, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not reached before timeout")


def _configure_fake_helper(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    mode: str = "hold",
) -> tuple[_Config, Path]:
    helper = _make_fake_helper(tmp_path / "helper")
    capture = tmp_path / "capture.json"
    monkeypatch.setenv("OPENBILICLAW_TAILNET_HELPER", str(helper))
    monkeypatch.setenv("FAKE_CAPTURE", str(capture))
    monkeypatch.setenv("FAKE_MODE", mode)
    return _Config(tmp_path / "data", _TailnetSettings()), capture


def test_start_tailnet_if_enabled_is_a_noop_when_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _Config(tmp_path / "data", _TailnetSettings(enabled=False))

    def unexpected_discovery(_config: object) -> Path:
        raise AssertionError("disabled tailnet must not discover or spawn a helper")

    monkeypatch.setattr(supervisor_module, "find_tailnet_helper", unexpected_discovery)

    assert supervisor_module.start_tailnet_if_enabled(config, 8420) is None
    assert not (config.data_path / "tailnet").exists()


def test_helper_discovery_uses_documented_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    basename = supervisor_module.tailnet_helper_basename()
    override = _make_executable(tmp_path / "override" / basename)
    bundle = _make_executable(tmp_path / "bundle" / basename)
    config = _Config(tmp_path / "data", _TailnetSettings())
    data_helper = _make_executable(config.data_path / "bin" / basename)
    repository = tmp_path / "repository"
    build_helper = _make_executable(repository / "build" / "tailnet" / basename)
    path_helper = _make_executable(tmp_path / "path" / basename)

    monkeypatch.setenv("OPENBILICLAW_TAILNET_HELPER", str(override))
    monkeypatch.setattr(supervisor_module.sys, "_MEIPASS", str(bundle.parent), raising=False)
    monkeypatch.setattr(supervisor_module, "_repository_root", lambda: repository)
    monkeypatch.setattr(supervisor_module.shutil, "which", lambda _name: str(path_helper))

    assert supervisor_module.find_tailnet_helper(config) == override.resolve()
    monkeypatch.delenv("OPENBILICLAW_TAILNET_HELPER")
    assert supervisor_module.find_tailnet_helper(config) == bundle.resolve()
    bundle.unlink()
    assert supervisor_module.find_tailnet_helper(config) == data_helper.resolve()
    data_helper.unlink()
    assert supervisor_module.find_tailnet_helper(config) == build_helper.resolve()
    build_helper.unlink()
    assert supervisor_module.find_tailnet_helper(config) == path_helper.resolve()


def test_missing_helper_raises_a_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _Config(tmp_path / "data", _TailnetSettings())
    monkeypatch.delenv("OPENBILICLAW_TAILNET_HELPER", raising=False)
    monkeypatch.delattr(supervisor_module.sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(supervisor_module, "_repository_root", lambda: tmp_path / "repository")
    monkeypatch.setattr(supervisor_module.shutil, "which", lambda _name: None)

    with pytest.raises(
        supervisor_module.TailnetHelperNotFoundError,
        match="Could not find openbiliclaw-tailnet-helper",
    ):
        supervisor_module.start_tailnet_if_enabled(config, 8420)


def test_macos_before_12_is_rejected_without_spawning_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _Config(tmp_path / "data", _TailnetSettings())
    monkeypatch.setattr(supervisor_module.sys, "platform", "darwin")
    monkeypatch.setattr(supervisor_module.platform, "mac_ver", lambda: ("11.7.10", (), ""))

    with pytest.raises(supervisor_module.TailnetSupervisorError, match="macOS 12 or newer"):
        supervisor_module.find_tailnet_helper(config)


def test_data_bin_symlink_is_not_used_for_helper_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    basename = supervisor_module.tailnet_helper_basename()
    config = _Config(tmp_path / "data", _TailnetSettings())
    outside = tmp_path / "outside"
    outside_helper = _make_executable(outside / basename)
    config.data_path.mkdir()
    try:
        (config.data_path / "bin").symlink_to(outside, target_is_directory=True)
    except OSError as exc:  # pragma: no cover - Windows policy dependent
        pytest.skip(f"directory symlinks unavailable: {exc}")
    repository = tmp_path / "repository"
    repository_helper = _make_executable(repository / "build" / "tailnet" / basename)
    monkeypatch.delenv("OPENBILICLAW_TAILNET_HELPER", raising=False)
    monkeypatch.delattr(supervisor_module.sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(supervisor_module, "_repository_root", lambda: repository)
    monkeypatch.setattr(supervisor_module.shutil, "which", lambda _name: None)

    assert outside_helper.is_file()
    assert supervisor_module.find_tailnet_helper(config) == repository_helper.resolve()


def test_state_directory_symlink_is_rejected_before_status_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper = _make_executable(tmp_path / "helper" / supervisor_module.tailnet_helper_basename())
    config = _Config(tmp_path / "data", _TailnetSettings())
    outside = tmp_path / "outside"
    outside.mkdir()
    config.data_path.mkdir()
    try:
        (config.data_path / "tailnet").symlink_to(outside, target_is_directory=True)
    except OSError as exc:  # pragma: no cover - Windows policy dependent
        pytest.skip(f"directory symlinks unavailable: {exc}")
    monkeypatch.setenv("OPENBILICLAW_TAILNET_HELPER", str(helper))

    with pytest.raises(supervisor_module.TailnetSupervisorError, match="file or symlink"):
        supervisor_module.TailnetSupervisor(config, 8420).start()

    assert not (outside / "status.json").exists()


def test_build_helper_self_tests_then_atomically_installs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    source = repository / "cmd" / "openbiliclaw-tailnet"
    source.mkdir(parents=True)
    (source / "go.mod").write_text("module example.invalid/tailnet\n", encoding="utf-8")
    (source / "build-tags.txt").write_text("ts_omit_logtail,ts_omit_webclient\n", encoding="utf-8")
    config = _Config(tmp_path / "data", _TailnetSettings())
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command: list[str], **kwargs: object):
        calls.append((command, kwargs))
        if command[0] == "/toolchain/go":
            candidate = Path(command[command.index("-o") + 1])
            candidate.write_text("new helper", encoding="utf-8")
            return supervisor_module.subprocess.CompletedProcess(command, 0, "", "")
        return supervisor_module.subprocess.CompletedProcess(
            command,
            0,
            '{"protocol":1,"event":"stopped","message":"self-test ok"}\n',
            "",
        )

    monkeypatch.setattr(supervisor_module, "_repository_root", lambda: repository)
    monkeypatch.setattr(supervisor_module.shutil, "which", lambda _name: "/toolchain/go")
    monkeypatch.setattr(supervisor_module.subprocess, "run", fake_run)

    installed = supervisor_module.build_tailnet_helper(config)

    assert installed == config.data_path / "bin" / supervisor_module.tailnet_helper_basename()
    assert installed.read_text(encoding="utf-8") == "new helper"
    build_command, build_kwargs = calls[0]
    assert build_command[:5] == [
        "/toolchain/go",
        "build",
        "-trimpath",
        "-tags=ts_omit_logtail,ts_omit_webclient",
        "-ldflags=-s -w",
    ]
    assert build_command[-1] == "."
    assert Path(build_command[build_command.index("-o") + 1]) != installed
    assert build_kwargs["cwd"] == source
    assert build_kwargs["env"]["CGO_ENABLED"] == "0"
    assert calls[1][0][1:] == ["--self-test"]
    if os.name != "nt":
        assert stat.S_IMODE(installed.stat().st_mode) == 0o700
    assert list(installed.parent.glob(".openbiliclaw-tailnet-helper-*")) == []


def test_build_helper_rejects_data_bin_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    source = repository / "cmd" / "openbiliclaw-tailnet"
    source.mkdir(parents=True)
    (source / "go.mod").write_text("module example.invalid/tailnet\n", encoding="utf-8")
    (source / "build-tags.txt").write_text("ts_omit_logtail,ts_omit_webclient\n", encoding="utf-8")
    config = _Config(tmp_path / "data", _TailnetSettings())
    outside = tmp_path / "outside"
    outside.mkdir()
    config.data_path.mkdir()
    try:
        (config.data_path / "bin").symlink_to(outside, target_is_directory=True)
    except OSError as exc:  # pragma: no cover - Windows policy dependent
        pytest.skip(f"directory symlinks unavailable: {exc}")
    monkeypatch.setattr(supervisor_module, "_repository_root", lambda: repository)
    monkeypatch.setattr(supervisor_module.shutil, "which", lambda _name: "/toolchain/go")

    with pytest.raises(supervisor_module.TailnetSupervisorError, match="file or symlink"):
        supervisor_module.build_tailnet_helper(config)

    assert list(outside.iterdir()) == []


def test_build_helper_failure_does_not_replace_an_existing_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    source = repository / "cmd" / "openbiliclaw-tailnet"
    source.mkdir(parents=True)
    (source / "go.mod").write_text("module example.invalid/tailnet\n", encoding="utf-8")
    (source / "build-tags.txt").write_text("ts_omit_logtail,ts_omit_webclient\n", encoding="utf-8")
    config = _Config(tmp_path / "data", _TailnetSettings())
    installed = _make_executable(
        config.data_path / "bin" / supervisor_module.tailnet_helper_basename(),
        "old helper",
    )

    def fake_run(command: list[str], **_kwargs: object):
        if command[0] == "/toolchain/go":
            Path(command[command.index("-o") + 1]).write_text("broken candidate", encoding="utf-8")
            return supervisor_module.subprocess.CompletedProcess(command, 0, "", "")
        return supervisor_module.subprocess.CompletedProcess(command, 9, "", "self-test failed")

    monkeypatch.setattr(supervisor_module, "_repository_root", lambda: repository)
    monkeypatch.setattr(supervisor_module.shutil, "which", lambda _name: "/toolchain/go")
    monkeypatch.setattr(supervisor_module.subprocess, "run", fake_run)

    with pytest.raises(supervisor_module.TailnetSupervisorError, match="self-test failed"):
        supervisor_module.build_tailnet_helper(config)

    assert installed.read_text(encoding="utf-8") == "old helper"
    assert list(installed.parent.glob(".openbiliclaw-tailnet-helper-*")) == []


def test_build_helper_requires_go_without_touching_an_existing_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    source = repository / "cmd" / "openbiliclaw-tailnet"
    source.mkdir(parents=True)
    (source / "go.mod").write_text("module example.invalid/tailnet\n", encoding="utf-8")
    (source / "build-tags.txt").write_text("ts_omit_logtail,ts_omit_webclient\n", encoding="utf-8")
    config = _Config(tmp_path / "data", _TailnetSettings())
    installed = _make_executable(
        config.data_path / "bin" / supervisor_module.tailnet_helper_basename(),
        "old helper",
    )
    monkeypatch.setattr(supervisor_module, "_repository_root", lambda: repository)
    monkeypatch.setattr(supervisor_module.shutil, "which", lambda _name: None)

    with pytest.raises(supervisor_module.TailnetSupervisorError, match="Go is required"):
        supervisor_module.build_tailnet_helper(config)

    assert installed.read_text(encoding="utf-8") == "old helper"


def test_start_sends_secret_only_over_stdin_and_keeps_pipe_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, capture_path = _configure_fake_helper(monkeypatch, tmp_path)
    auth_key = _TEST_AUTH_CREDENTIAL
    monkeypatch.setenv("OPENBILICLAW_TAILNET_AUTH_KEY", auth_key)
    monkeypatch.setenv("TS_AUTHKEY", "legacy-ts-secret")
    monkeypatch.setenv("TAILSCALE_AUTH_KEY", "another-secret")
    monkeypatch.setenv("TS_CLIENT_SECRET", "test-ambient-oauth-secret")

    ready = threading.Event()
    events: list[supervisor_module.TailnetEvent] = []

    def on_event(event: supervisor_module.TailnetEvent) -> None:
        events.append(event)
        if event.get("event") == "ready":
            ready.set()

    supervisor = supervisor_module.start_tailnet_if_enabled(config, 8420, event_callback=on_event)
    assert supervisor is not None
    assert ready.wait(3.0)
    captured = json.loads(capture_path.read_text(encoding="utf-8"))

    assert captured["argv"] == [
        "--state-dir",
        str(config.data_path / "tailnet"),
        "--hostname",
        "openbiliclaw-test",
        "--listen-port",
        "8420",
        "--backend-port",
        "8420",
    ]
    assert captured["bootstrap"] == {
        "protocol": 1,
        "auth_key": auth_key,
        "advertise_tags": [],
    }
    assert captured["auth_env"] == {}
    assert auth_key not in json.dumps(captured["argv"])
    assert supervisor.process is not None
    assert supervisor.process.poll() is None
    assert not supervisor.process.stdin.closed
    assert any(event.get("event") == "ready" for event in events)

    supervisor.stop()
    assert supervisor.wait(1.0) == 0


def test_staged_oauth_secret_is_private_consumed_and_tagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, capture_path = _configure_fake_helper(monkeypatch, tmp_path)
    secret = _TEST_OAUTH_CREDENTIAL
    staged = supervisor_module.stage_tailnet_bootstrap(
        config,
        secret,
        ["tag:openbiliclaw"],
    )
    assert staged.is_file()
    if os.name != "nt":
        assert stat.S_IMODE(staged.stat().st_mode) == 0o600

    ready = threading.Event()
    supervisor = supervisor_module.TailnetSupervisor(
        config,
        8420,
        event_callback=lambda event: ready.set() if event.get("event") == "ready" else None,
    ).start()
    assert ready.wait(3.0)

    captured = json.loads(capture_path.read_text(encoding="utf-8"))
    assert captured["bootstrap"] == {
        "protocol": 1,
        "auth_key": secret,
        "advertise_tags": ["tag:openbiliclaw"],
    }
    assert captured["auth_env"] == {}
    assert not staged.exists()
    assert not supervisor_module.tailnet_bootstrap_staged(config)

    supervisor.stop()


def test_stage_oauth_secret_requires_valid_tag(tmp_path: Path) -> None:
    config = _Config(data_path=tmp_path / "data", tailnet=_TailnetSettings())
    with pytest.raises(supervisor_module.TailnetSupervisorError, match="at least one"):
        supervisor_module.stage_tailnet_bootstrap(
            config,
            _TEST_OAUTH_CREDENTIAL,
        )
    with pytest.raises(supervisor_module.TailnetSupervisorError, match="Invalid Tailnet tag"):
        supervisor_module.stage_tailnet_bootstrap(
            config,
            _TEST_OAUTH_CREDENTIAL,
            ["tag:1invalid"],
        )


def test_staged_auth_key_does_not_require_tags(tmp_path: Path) -> None:
    config = _Config(data_path=tmp_path / "data", tailnet=_TailnetSettings())
    staged = supervisor_module.stage_tailnet_bootstrap(
        config,
        _TEST_AUTH_CREDENTIAL,
    )
    assert supervisor_module.tailnet_bootstrap_staged(config)
    supervisor_module.clear_tailnet_bootstrap(config)
    assert not staged.exists()


def test_private_child_environment_materializes_system_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "http_proxy",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
        "NO_PROXY",
        "no_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        supervisor_module,
        "getproxies",
        lambda: {
            "http": "http://127.0.0.1:7897",
            "https": "http://127.0.0.1:7897",
            "no": "internal.example",
        },
    )

    environment, _, _ = supervisor_module._private_child_environment()

    assert environment["HTTP_PROXY"] == "http://127.0.0.1:7897"
    assert environment["HTTPS_PROXY"] == "http://127.0.0.1:7897"
    assert environment["http_proxy"] == "http://127.0.0.1:7897"
    assert environment["https_proxy"] == "http://127.0.0.1:7897"
    no_proxy = environment["NO_PROXY"].split(",")
    assert "internal.example" in no_proxy
    assert "localhost" in no_proxy
    assert "127.0.0.1" in no_proxy
    assert "::1" in no_proxy
    assert environment["no_proxy"] == environment["NO_PROXY"]


def test_private_child_environment_preserves_explicit_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://explicit:8080")
    monkeypatch.setenv("NO_PROXY", "corp.internal")
    monkeypatch.setattr(
        supervisor_module,
        "getproxies",
        lambda: {
            "http": "http://system-http:8080",
            "https": "http://system-https:8080",
        },
    )

    environment, _, _ = supervisor_module._private_child_environment()

    assert environment["HTTPS_PROXY"] == "http://explicit:8080"
    assert "corp.internal" in environment["NO_PROXY"]
    assert "localhost" in environment["NO_PROXY"]


def test_staged_bootstrap_is_deleted_only_after_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, capture_path = _configure_fake_helper(monkeypatch, tmp_path)
    monkeypatch.setenv("FAKE_READY_DELAY", "0.3")
    staged = supervisor_module.stage_tailnet_bootstrap(
        config, _TEST_OAUTH_CREDENTIAL, ["tag:openbiliclaw"]
    )
    ready = threading.Event()

    supervisor = supervisor_module.TailnetSupervisor(
        config,
        8420,
        event_callback=lambda event: ready.set() if event.get("event") == "ready" else None,
    ).start()

    _wait_for(lambda: (supervisor.last_event or {}).get("event") == "starting")
    assert staged.exists()
    assert supervisor_module.tailnet_bootstrap_staged(config)

    assert ready.wait(3.0)
    assert not staged.exists()
    assert not supervisor_module.tailnet_bootstrap_staged(config)
    captured = json.loads(capture_path.read_text(encoding="utf-8"))
    assert captured["bootstrap"]["auth_key"] == _TEST_OAUTH_CREDENTIAL
    supervisor.stop()


def test_events_are_persisted_atomically_with_private_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _capture_path = _configure_fake_helper(monkeypatch, tmp_path)
    ready = threading.Event()

    def on_event(event: supervisor_module.TailnetEvent) -> None:
        if event.get("event") == "ready":
            ready.set()

    supervisor = supervisor_module.TailnetSupervisor(config, 8420, event_callback=on_event).start()
    assert ready.wait(3.0)

    status = json.loads(supervisor.status_path.read_text(encoding="utf-8"))
    assert status == {
        "dns_name": "openbiliclaw-test.example.ts.net",
        "event": "ready",
        "ips": ["100.64.0.8"],
        "port": 8420,
        "protocol": 1,
    }
    if os.name != "nt":
        assert stat.S_IMODE(config.data_path.joinpath("tailnet").stat().st_mode) == 0o700
        assert stat.S_IMODE(supervisor.status_path.stat().st_mode) == 0o600
    assert list((config.data_path / "tailnet").glob(".status-*.tmp")) == []

    supervisor.stop()
    final_status = json.loads(supervisor.status_path.read_text(encoding="utf-8"))
    assert final_status["event"] == "stopped"
    assert final_status["message"] == "stdin EOF"


def test_stop_closes_stdin_and_allows_a_graceful_helper_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _capture_path = _configure_fake_helper(monkeypatch, tmp_path)
    supervisor = supervisor_module.TailnetSupervisor(config, 8420).start()
    _wait_for(lambda: (supervisor.last_event or {}).get("event") == "ready")

    process = supervisor.process
    assert process is not None
    supervisor.stop(grace_timeout=1.0)

    assert process.returncode == 0
    assert process.stdin.closed
    assert supervisor.wait(0.1) == 0
    assert (supervisor.last_event or {}).get("event") == "stopped"


def test_nonzero_exit_is_reported_without_leaking_the_auth_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _capture_path = _configure_fake_helper(monkeypatch, tmp_path, mode="crash")
    auth_key = _TEST_AUTH_CREDENTIAL
    monkeypatch.setenv("OPENBILICLAW_TAILNET_AUTH_KEY", auth_key)
    monkeypatch.setenv("FAKE_STDERR", f"crashed after reading {auth_key}")
    monkeypatch.setenv("FAKE_EVENT_MESSAGE", f"received {auth_key}")
    monkeypatch.setenv("FAKE_EXIT_CODE", "23")

    supervisor = supervisor_module.TailnetSupervisor(config, 8420).start()
    with pytest.raises(supervisor_module.TailnetHelperExitedError) as raised:
        supervisor.wait(3.0)

    assert raised.value.returncode == 23
    assert auth_key not in str(raised.value)
    assert "[redacted]" in str(raised.value)
    status_text = supervisor.status_path.read_text(encoding="utf-8")
    assert auth_key not in status_text
    assert json.loads(status_text)["code"] == "helper_exited"


def test_spawn_applies_no_window_kwargs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helper = _make_executable(tmp_path / supervisor_module.tailnet_helper_basename())
    config = _Config(tmp_path / "data", _TailnetSettings())
    monkeypatch.setenv("OPENBILICLAW_TAILNET_HELPER", str(helper))
    captured: dict[str, object] = {}

    class _WritableInput(io.StringIO):
        pass

    class _FakeProcess:
        def __init__(self) -> None:
            self.stdin = _WritableInput()
            self.stdout = io.StringIO('{"protocol":1,"event":"stopped"}\n')
            self.stderr = io.StringIO("")
            self.returncode: int | None = None

        def wait(self) -> int:
            self.returncode = 0
            return 0

        def poll(self) -> int | None:
            return self.returncode

        def terminate(self) -> None:
            self.returncode = 0

        def kill(self) -> None:
            self.returncode = -9

    def fake_popen(arguments: list[str], **kwargs: object) -> _FakeProcess:
        captured["arguments"] = arguments
        captured.update(kwargs)
        return _FakeProcess()

    monkeypatch.setattr(
        supervisor_module, "no_window_kwargs", lambda: {"creationflags": 0x08000000}
    )
    monkeypatch.setattr(supervisor_module.subprocess, "Popen", fake_popen)

    supervisor = supervisor_module.TailnetSupervisor(config, 8420).start()
    assert supervisor.wait(1.0) == 0
    assert captured["creationflags"] == 0x08000000
