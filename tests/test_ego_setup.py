"""Managed setup safety/lifecycle; protocol fixtures are not live Ego acceptance."""

import asyncio
import errno
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from openbiliclaw.sources.ego_onboarding import (
    EgoOnboardingError,
    complete_onboarding,
    onboarding_action,
)
from openbiliclaw.sources.ego_setup import EgoSetupError, EgoSetupManager


@pytest.fixture
def manager(tmp_path, monkeypatch):
    for name in (
        "OPENBILICLAW_EGO_BROWSER",
        "OPENBILICLAW_EGO_SERVER_NAME",
        "OPENBILICLAW_EGO_PROFILE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("openbiliclaw.sources.ego_setup.sys.platform", "darwin")
    return EgoSetupManager(tmp_path)


def snapshot(title, buttons, checkboxes=None):
    return {
        "url": "chrome://onboarding/",
        "headings": [title],
        "buttons": buttons,
        "checkboxes": checkboxes or [],
    }


@pytest.mark.parametrize("outcome", ["ready", "non_loopback", "persistent_timeout"])
async def test_launch_retries_temporary_listener_probe_timeout(manager, monkeypatch, outcome):
    from types import SimpleNamespace

    manager.root.mkdir(parents=True)
    running = False

    async def spawn(*args, **kwargs):
        nonlocal running
        running = True
        return SimpleNamespace(pid=101, returncode=None)

    attempts = 0

    async def command(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        assert args[0] == "/usr/sbin/lsof"
        assert args[args.index("-p") + 1] == "101"
        if attempts == 1 or outcome == "persistent_timeout":
            raise TimeoutError
        port = manager._state()["debug_port"]
        address = "127.0.0.1" if outcome == "ready" else "*"
        return f"p101\nf5\nn{address}:{port}\n"

    monkeypatch.setattr(manager, "_pid", lambda: 101 if running else None)
    monkeypatch.setattr("openbiliclaw.sources.ego_setup.asyncio.create_subprocess_exec", spawn)
    monkeypatch.setattr("openbiliclaw.sources.ego_setup.asyncio.sleep", AsyncMock())
    monkeypatch.setattr("openbiliclaw.sources.ego_setup._command", command)
    if outcome == "ready":
        assert await manager._launch(debugging=True) == manager._state()["debug_port"]
        assert attempts == 2
    else:
        error = (
            "ego_setup_debug_not_loopback"
            if outcome == "non_loopback"
            else "ego_setup_host_timeout"
        )
        with pytest.raises(EgoSetupError, match=error):
            await manager._launch(debugging=True)
        assert 2 <= attempts <= 80


@pytest.mark.parametrize(
    "heading,buttons,expected",
    [
        ("ego(lite) 是 AI Agent 运行网页自动化最快的浏览器", ["开始使用"], "开始使用"),
        ("你和 Agent，各有各的 Space", ["下一步"], "下一步"),
        ("和 Chrome 一样顺手，无需重新适应", ["下一步"], "下一步"),
        ("隐私优先的设计", ["下一步"], "下一步"),
        ("将数据导入 ego(lite)", ["导入数据", "稍后设置"], "稍后设置"),
        ("保持联系", ["订阅", "跳过"], "跳过"),
        ("你是如何了解到 ego(lite) 的？", ["下一步", "跳过"], "跳过"),
        ("选个喜欢的标签页布局", ["横向", "纵向", "下一步"], "下一步"),
        ("Import data to ego(lite)", ["Import data", "Set up later"], "Set up later"),
    ],
)
def test_known_onboarding_controls_skip_optional_collection(heading, buttons, expected):
    assert onboarding_action(snapshot(heading, buttons))["button"] == expected


def test_finish_requires_exact_three_options_unchecked_before_click():
    boxes = [
        {"label": label, "checked": True}
        for label in ["设为默认浏览器", "添加到程序坞", "分享崩溃报告"]
    ]
    state = snapshot("一切就绪", ["打开 ego(lite)"], boxes)
    for box in boxes:
        action = onboarding_action(state)
        assert action["checkbox"] == box["label"]
        assert "button" not in action
        box["checked"] = False
    assert onboarding_action(state) == {"step": 8, "button": "打开 ego(lite)", "complete": True}


def test_snapshot_does_not_read_import_profile_checkboxes():
    import shutil
    import subprocess

    from openbiliclaw.sources.ego_onboarding import _SNAPSHOT

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required to execute the fixed DOM privacy fixture")
    program = """
    globalThis.location={href:'chrome://onboarding/'};
    let title='将数据导入 ego(lite)',reads=0;
    globalThis.document={querySelectorAll:selector=>{
      if(selector==='h1')return [{innerText:title}];
      if(selector==='button')return [];
      if(selector==='input[type=checkbox]'){
        if(title!=='一切就绪')throw Error('private import options were read');
        reads++;return [{checked:true,closest:()=>({innerText:'分享崩溃报告'})}];
      }
      throw Error('unexpected selector');
    }};
    const code=SNAPSHOT;
    const imported=eval(code);
    title='一切就绪';const completed=eval(code);
    console.log(JSON.stringify({reads,imported:imported.checkboxes,final:completed.checkboxes}));
    """.replace("SNAPSHOT", json.dumps(_SNAPSHOT))
    result = subprocess.run([node, "-e", program], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == {
        "reads": 1,
        "imported": [],
        "final": [{"label": "分享崩溃报告", "checked": True}],
    }


@pytest.mark.parametrize(
    "state",
    [
        snapshot("New account sign-in required", ["Continue"]),
        snapshot("一切就绪", ["打开 ego(lite)"], [{"label": "new permission", "checked": True}]),
        snapshot("将数据导入 ego(lite)", ["导入数据"]),
        snapshot("隐私优先的设计", ["下一步", "下一步"]),
        {**snapshot("隐私优先的设计", ["下一步"]), "url": "https://attacker.test/"},
    ],
)
def test_unknown_or_ambiguous_setup_never_guesses(state):
    with pytest.raises(EgoOnboardingError):
        onboarding_action(state)


def test_status_and_connection_do_not_create_files_or_spawn(manager, monkeypatch):
    spawn = AsyncMock()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    assert manager.status()["setup_state"] == "idle"
    connection = manager.connection()
    assert connection["server_name"].startswith("obc-")
    assert connection["executable"].startswith(str(manager.root))
    assert not manager.root.exists()
    spawn.assert_not_called()


async def test_explicit_external_host_is_never_managed(manager, monkeypatch):
    monkeypatch.setenv("OPENBILICLAW_EGO_BROWSER", "/an/external/ego-browser")
    install = AsyncMock()
    monkeypatch.setattr(manager, "_install", install)
    assert not (await manager.prepare())["managed"]
    assert not (await manager.ensure_running())["managed"]
    assert manager.connection() == {}
    assert not manager.root.exists()
    install.assert_not_awaited()


async def test_work_does_not_initialize_unprepared_host(manager, monkeypatch):
    launch = AsyncMock()
    monkeypatch.setattr(manager, "_launch", launch)
    assert (await manager.ensure_running())["setup_state"] == "idle"
    assert not manager.root.exists()
    launch.assert_not_awaited()


async def test_unsupported_host_does_not_install(manager, monkeypatch):
    monkeypatch.setattr("openbiliclaw.sources.ego_setup.sys.platform", "linux")
    result = await manager.prepare()
    assert result["setup_state"] == "unsupported" and not result["platform_supported"]
    assert not manager.root.exists()


def fake_lifecycle(manager, monkeypatch):
    calls = []
    running = False

    async def install():
        calls.append("install")

    async def launch(*, debugging):
        nonlocal running
        calls.append("debug" if debugging else "normal")
        running = True
        manager._write(pid=100, debug_port=18508 if debugging else 0)
        return 18508 if debugging else 0

    async def stop():
        nonlocal running
        calls.append("stop")
        running = False
        manager._write(pid=None, debug_port=0)

    async def handshake():
        assert manager._state()["debug_port"] == 0
        calls.append("handshake")
        manager._write(initialized=True, ready_pid=100, setup_state="ready", phase="ready")

    monkeypatch.setattr(manager, "_install", install)
    monkeypatch.setattr(manager, "_launch", launch)
    monkeypatch.setattr(manager, "_stop", stop)
    monkeypatch.setattr(manager, "_handshake", handshake)
    monkeypatch.setattr(manager, "_pid", lambda: 100 if running else None)
    manager.executable.parent.mkdir(parents=True)
    manager.executable.touch()
    return calls


async def test_prepare_closes_temporary_debug_host_before_native_ready(manager, monkeypatch):
    calls = fake_lifecycle(manager, monkeypatch)
    complete = AsyncMock()
    monkeypatch.setattr("openbiliclaw.sources.ego_setup.complete_onboarding", complete)
    result = await manager.prepare()
    assert result["setup_state"] == "ready" and result["available"]
    assert calls == ["install", "stop", "debug", "stop", "normal", "handshake"]
    complete.assert_awaited_once_with(18508)
    assert manager._state()["debug_port"] == 0


async def test_onboarding_failure_stops_owned_host_without_native_ready(manager, monkeypatch):
    calls = fake_lifecycle(manager, monkeypatch)
    monkeypatch.setattr(
        "openbiliclaw.sources.ego_setup.complete_onboarding",
        AsyncMock(side_effect=EgoOnboardingError("ego_setup_unknown_step")),
    )
    result = await manager.prepare()
    assert result["setup_state"] == "failed" and not result["available"]
    assert result["detail"] == "ego_setup_unknown_step"
    assert "handshake" not in calls and calls[-1] == "stop"
    assert manager._state()["debug_port"] == 0


async def test_completed_ui_without_handshake_marker_can_recover(manager, monkeypatch):
    calls = fake_lifecycle(manager, monkeypatch)
    monkeypatch.setattr(
        "openbiliclaw.sources.ego_setup.complete_onboarding",
        AsyncMock(side_effect=EgoOnboardingError("ego_setup_page_unavailable")),
    )
    assert (await manager.prepare())["available"]
    assert calls[-3:] == ["stop", "normal", "handshake"]


async def test_initialized_offline_host_restarts_without_download_or_debug(manager, monkeypatch):
    calls = fake_lifecycle(manager, monkeypatch)
    manager._write(initialized=True, setup_state="ready")
    result = await manager.ensure_running()
    assert result["available"]
    assert calls == ["normal", "handshake"]


async def test_concurrent_prepare_reuses_single_install_and_host(manager, monkeypatch):
    calls = fake_lifecycle(manager, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()

    async def complete(port):
        entered.set()
        await release.wait()

    monkeypatch.setattr("openbiliclaw.sources.ego_setup.complete_onboarding", complete)
    first = asyncio.create_task(manager.prepare())
    await entered.wait()
    assert (await manager.prepare())["setup_state"] == "preparing"
    release.set()
    assert (await first)["available"]
    assert calls.count("debug") == 1 and calls.count("install") == 1


async def test_cancelled_setup_closes_temporary_host(manager, monkeypatch):
    calls = fake_lifecycle(manager, monkeypatch)
    entered = asyncio.Event()

    async def complete(port):
        entered.set()
        await asyncio.Future()

    monkeypatch.setattr("openbiliclaw.sources.ego_setup.complete_onboarding", complete)
    task = asyncio.create_task(manager.prepare())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls[-1] == "stop" and "handshake" not in calls
    assert manager.status()["setup_state"] == "failed"


async def test_handshake_requires_actual_marker_and_default_profile(manager, monkeypatch):
    manager.root.mkdir()
    manager._write(pid=100, debug_port=0)
    monkeypatch.setattr(manager, "_pid", lambda: 100)
    command = AsyncMock(return_value="Please complete onboarding first")
    monkeypatch.setattr("openbiliclaw.sources.ego_setup._command", command)
    with pytest.raises(EgoSetupError, match="native_handshake_failed"):
        await manager._handshake()
    assert not manager._state().get("initialized")
    command.return_value = "OBC_EGO_READY:" + json.dumps({"version": "0.6.0.16", "profile": True})
    await manager._handshake()
    assert manager._state()["ready_pid"] == 100
    assert command.call_args.args[0] == str(manager.executable)
    assert "--ego-server-name=" + manager.server_name in command.call_args.args


async def test_os_signature_rejection_is_not_bypassed(manager, monkeypatch):
    command = AsyncMock(side_effect=["", "TeamIdentifier=EVIL\nIdentifier=com.citrolabs.ego.lite"])
    monkeypatch.setattr("openbiliclaw.sources.ego_setup._command", command)
    with pytest.raises(EgoSetupError, match="signature_mismatch"):
        await manager._verify(manager.app)
    assert not any("xattr" in str(call) or "sudo" in str(call) for call in command.call_args_list)


def test_bundle_metadata_normalization_cannot_follow_external_symlinks(manager, tmp_path):
    manager.app.mkdir(parents=True)
    outside = tmp_path / "unrelated"
    outside.mkdir()
    (manager.app / "outside").symlink_to(outside, target_is_directory=True)
    with pytest.raises(EgoSetupError, match="unsafe_bundle_link"):
        manager._check_bundle_links(manager.app)


async def test_install_normalizes_only_finder_metadata_before_full_verification(
    manager, monkeypatch
):
    manager.root.mkdir()
    commands = []
    verified = []

    async def command(*args, **kwargs):
        commands.append(args)
        if args[:2] == ("/usr/bin/hdiutil", "attach"):
            (Path(args[args.index("-mountpoint") + 1]) / "ego lite.app").mkdir()
        if args[0] == "/usr/bin/ditto":
            staged = Path(args[2])
            relative = manager.executable.relative_to(manager.app)
            (staged / relative).parent.mkdir(parents=True)
            (staged / relative).touch()
        return ""

    async def verify(app):
        verified.append(app)
        assert [call[2] for call in commands if call[0] == "/usr/bin/xattr"] == [
            "com.apple.FinderInfo",
            "com.apple.ResourceFork",
        ]

    async def download(path):
        path.write_bytes(b"complete fixture dmg")

    monkeypatch.setattr(manager, "_download", download)
    monkeypatch.setattr(manager, "_verify", verify)
    monkeypatch.setattr("openbiliclaw.sources.ego_setup._command", command)
    await manager._install()
    assert manager.executable.is_file()
    assert len(verified) == 1
    assert all("quarantine" not in str(call) for call in commands)
    assert commands[-1][1] == "detach"


async def test_invalid_cached_signature_is_verified_then_invalidated(manager, monkeypatch):
    manager.root.mkdir()
    cache = manager.root / "installer.dmg"
    cache.write_bytes(b"existing complete dmg")
    download = AsyncMock()
    verify = AsyncMock(side_effect=EgoSetupError("ego_setup_signature_invalid"))

    async def command(*args, **kwargs):
        if args[:2] == ("/usr/bin/hdiutil", "attach"):
            (Path(args[args.index("-mountpoint") + 1]) / "ego lite.app").mkdir()
        return ""

    commands = AsyncMock(side_effect=command)
    monkeypatch.setattr(manager, "_download", download)
    monkeypatch.setattr(manager, "_verify", verify)
    monkeypatch.setattr("openbiliclaw.sources.ego_setup._command", commands)
    with pytest.raises(EgoSetupError, match="signature_invalid"):
        await manager._install()
    download.assert_not_awaited()
    verify.assert_awaited_once()
    assert str(cache) in commands.call_args_list[0].args
    assert not cache.exists()
    assert not manager.app.exists()


def installer_lifecycle(manager, monkeypatch, failure):
    """Keep prepare/install/verify real; replace only download, OS and host I/O."""
    import shutil

    attempt = 0
    running = False

    async def download(path):
        nonlocal attempt
        attempt += 1
        path.write_bytes(b"complete fixture dmg")

    async def command(*args, **kwargs):
        if args[:2] == ("/usr/bin/hdiutil", "attach"):
            if attempt == 1 and failure == "mount":
                raise EgoSetupError("ego_setup_command_failed")
            app = Path(args[args.index("-mountpoint") + 1]) / "ego lite.app"
            if not (attempt == 1 and failure == "bundle"):
                app.mkdir()
                if not (attempt == 1 and failure == "cli"):
                    cli = app / manager.executable.relative_to(manager.app)
                    cli.parent.mkdir(parents=True)
                    cli.touch()
        elif args[0] == "/usr/bin/ditto":
            shutil.copytree(args[1], args[2])
        elif args[:2] == ("/usr/bin/codesign", "--verify") and attempt == 1:
            if failure == "signature":
                raise EgoSetupError("ego_setup_command_failed")
            if failure == "timeout":
                raise TimeoutError
            if failure == "cancelled":
                raise asyncio.CancelledError
        elif args[:2] == ("/usr/bin/codesign", "-dv"):
            team = "OTHER" if attempt == 1 and failure == "identity" else "JGQLC6YQYJ"
            return f"TeamIdentifier={team}\nIdentifier=com.citrolabs.ego.lite"
        elif args[0] == "/usr/sbin/spctl" and attempt == 1 and failure == "trust":
            raise EgoSetupError("ego_setup_command_failed")
        return ""

    async def launch(*, debugging):
        nonlocal running
        running = True
        manager._write(pid=100, debug_port=18508 if debugging else 0)
        return 18508 if debugging else 0

    async def stop():
        nonlocal running
        running = False

    async def handshake():
        manager._write(initialized=True, ready_pid=100, setup_state="ready", phase="ready")

    downloader = AsyncMock(side_effect=download)
    monkeypatch.setattr(manager, "_download", downloader)
    monkeypatch.setattr("openbiliclaw.sources.ego_setup._command", command)
    monkeypatch.setattr("openbiliclaw.sources.ego_setup.complete_onboarding", AsyncMock())
    monkeypatch.setattr(manager, "_launch", launch)
    monkeypatch.setattr(manager, "_stop", stop)
    monkeypatch.setattr(manager, "_handshake", handshake)
    monkeypatch.setattr(manager, "_pid", lambda: 100 if running else None)
    return downloader


@pytest.mark.parametrize(
    "failure,detail",
    [
        ("signature", "ego_setup_signature_invalid"),
        ("identity", "ego_setup_signature_mismatch"),
        ("bundle", "ego_setup_bundle_missing"),
        ("cli", "ego_setup_cli_missing"),
    ],
)
async def test_bad_installer_next_prepare_downloads_again(manager, monkeypatch, failure, detail):
    download = installer_lifecycle(manager, monkeypatch, failure)
    first = await manager.prepare()
    assert first["setup_state"] == "failed" and first["detail"] == detail
    assert not manager.app.exists()
    assert not (manager.root / "installer.dmg").exists()
    assert download.await_count == 1  # No automatic retry loop.
    second = await manager.prepare()
    assert second["setup_state"] == "ready" and second["available"]
    assert download.await_count == 2


@pytest.mark.parametrize(
    "failure,detail",
    [
        ("trust", "ego_setup_system_trust_required"),
        ("timeout", "ego_setup_timeout"),
        ("mount", "ego_setup_mount_failed"),
        ("cancelled", "ego_setup_cancelled"),
    ],
)
async def test_uncertain_or_trust_failure_retains_installer(manager, monkeypatch, failure, detail):
    download = installer_lifecycle(manager, monkeypatch, failure)
    if failure == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            await manager.prepare()
        result = manager.status()
    else:
        result = await manager.prepare()
    assert result["setup_state"] == "failed" and result["detail"] == detail
    assert (manager.root / "installer.dmg").read_bytes() == b"complete fixture dmg"
    assert not manager.app.exists()
    assert download.await_count == 1


async def test_interrupted_download_never_becomes_installer_cache(manager, monkeypatch, tmp_path):
    manager.root.mkdir()
    partial = tmp_path / "download"
    partial.mkdir()

    async def download(path):
        path.write_bytes(b"incomplete")
        raise asyncio.CancelledError

    monkeypatch.setattr(manager, "_download", download)
    with pytest.raises(asyncio.CancelledError):
        await manager._cached_installer(partial)
    assert not (manager.root / "installer.dmg").exists()


async def test_symlink_installer_cache_is_rejected(manager, monkeypatch, tmp_path):
    manager.root.mkdir()
    unrelated = tmp_path / "unrelated.dmg"
    unrelated.write_bytes(b"do not read or replace")
    (manager.root / "installer.dmg").symlink_to(unrelated)
    download = AsyncMock()
    monkeypatch.setattr(manager, "_download", download)
    with pytest.raises(EgoSetupError, match="invalid_installer_cache"):
        await manager._cached_installer(tmp_path)
    download.assert_not_awaited()
    assert unrelated.read_bytes() == b"do not read or replace"


async def test_raw_exception_body_is_never_exposed_by_prepare(manager, monkeypatch):
    monkeypatch.setattr(manager, "_install", AsyncMock(side_effect=ValueError("secret-body")))
    monkeypatch.setattr(manager, "_stop", AsyncMock())
    result = await manager.prepare()
    assert result["detail"] == "ego_setup_failed"
    assert "secret-body" not in manager._state_path.read_text()


def test_stale_progress_is_reported_interrupted_without_writing(manager):
    manager.root.mkdir()
    manager._write(setup_state="preparing", phase="onboarding")
    before = manager._state_path.read_bytes()
    assert manager.status()["detail"] == "ego_setup_interrupted"
    assert manager._state_path.read_bytes() == before


@pytest.mark.parametrize("initially_running", [True, False])
async def test_stop_waits_for_debug_port_after_main_pid_disappears(
    manager, monkeypatch, initially_running
):
    elapsed, signals, probes = stop_fixture(manager, monkeypatch, initially_running)
    await manager._stop()
    assert elapsed[0] >= 0.3
    assert probes and probes[-1] == "closed"
    assert len(signals) == int(initially_running)
    assert manager._state()["pid"] is None
    assert manager._state()["ready_pid"] is None
    assert manager._state()["debug_port"] == 0


async def test_stop_preserves_diagnostic_until_debug_port_closes(manager, monkeypatch):
    elapsed, signals, probes = stop_fixture(manager, monkeypatch, True, port_closes=False)
    original = manager._state_path.read_bytes()
    with pytest.raises(EgoSetupError, match="ego_setup_debug_port_still_open"):
        await manager._stop()
    assert 5.99 <= elapsed[0] < 6.2
    assert probes and set(probes) == {"open"}
    assert len(signals) == 1
    assert manager._state_path.read_bytes() == original


async def test_stop_escalates_then_preserves_state_when_host_never_exits(manager, monkeypatch):
    import signal

    elapsed, signals, probes = stop_fixture(manager, monkeypatch, True, pid_exits=False)
    original = manager._state_path.read_bytes()
    with pytest.raises(EgoSetupError, match="ego_setup_host_did_not_exit"):
        await manager._stop()
    # A browser that ignores SIGTERM must not keep a renderer alive forever;
    # the verified identity is killed only after the graceful budget expires.
    assert signals == [(96976, signal.SIGTERM), (96976, signal.SIGKILL)]
    assert 8.9 <= elapsed[0] < 9.5
    assert not probes
    assert manager._state_path.read_bytes() == original


@pytest.mark.parametrize("connect_errno", [errno.ETIMEDOUT, 9999])
async def test_stop_never_treats_unconfirmed_connection_errors_as_closed(
    manager, monkeypatch, connect_errno
):
    elapsed, signals, probes = stop_fixture(manager, monkeypatch, True, connect_errno=connect_errno)
    original = manager._state_path.read_bytes()
    with pytest.raises(EgoSetupError, match="ego_setup_debug_port_still_open"):
        await manager._stop()
    assert 5.99 <= elapsed[0] < 6.2
    assert probes and set(probes) == {"unconfirmed"}
    assert len(signals) == 1
    assert manager._state_path.read_bytes() == original


def stop_fixture(
    manager, monkeypatch, initially_running, *, port_closes=True, pid_exits=True, connect_errno=None
):
    from types import SimpleNamespace

    from openbiliclaw.sources import ego_setup

    manager.root.mkdir()
    manager._write(pid=96976, ready_pid=None, debug_port=54677)
    elapsed, signals, probes = [0.0], [], []

    def pid():
        return 96976 if initially_running and (not signals or not pid_exits) else None

    async def sleep(seconds):
        elapsed[0] += seconds

    class Probe:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def settimeout(self, seconds):
            assert 0 < seconds <= 0.2

        def connect_ex(self, address):
            assert address == ("127.0.0.1", 54677)
            if connect_errno is not None:
                probes.append("unconfirmed")
                return connect_errno
            opened = not port_closes or elapsed[0] < 0.3
            probes.append("open" if opened else "closed")
            return 0 if opened else errno.ECONNREFUSED

    monkeypatch.setattr(manager, "_pid", pid)
    monkeypatch.setattr(ego_setup.os, "kill", lambda pid, sig: signals.append((pid, sig)))
    monkeypatch.setattr(ego_setup, "socket", SimpleNamespace(socket=Probe))
    monkeypatch.setattr(
        ego_setup, "time", SimpleNamespace(monotonic=lambda: elapsed[0], time=ego_setup.time.time)
    )
    monkeypatch.setattr(ego_setup.asyncio, "sleep", sleep)
    return elapsed, signals, probes


@pytest.mark.parametrize("stall", ["none", "snapshot", "action"])
async def test_native_cdp_fixture_uses_only_setup_controls_and_disables_options(monkeypatch, stall):
    import httpx
    from websockets.asyncio.server import serve

    states = [
        snapshot("ego(lite) 是 AI Agent 运行网页自动化最快的浏览器", ["开始使用"]),
        snapshot("你和 Agent，各有各的 Space", ["下一步"]),
        snapshot("和 Chrome 一样顺手，无需重新适应", ["下一步"]),
        snapshot("隐私优先的设计", ["下一步"]),
        snapshot("将数据导入 ego(lite)", ["导入数据", "稍后设置"]),
        snapshot("保持联系", ["订阅", "跳过"]),
        snapshot("你是如何了解到 ego(lite) 的？", ["下一步", "跳过"]),
        snapshot("选个喜欢的标签页布局", ["横向", "纵向", "下一步"]),
        snapshot(
            "一切就绪",
            ["打开 ego(lite)"],
            [
                {"label": label, "checked": True}
                for label in ["设为默认浏览器", "添加到程序坞", "分享崩溃报告"]
            ],
        ),
    ]
    index, snapshot_count = 0, 0
    actions = []

    async def native(socket):
        nonlocal index, snapshot_count
        async for raw in socket:
            request = json.loads(raw)
            assert request["method"] == "Runtime.evaluate"
            expression = request["params"]["expression"]
            assert "controller.completeOnboarding" not in expression
            if "headings:" in expression:
                if stall == "snapshot" and snapshot_count == 0:
                    # A read-only snapshot can time out while the native page
                    # starts rendering; no action has been dispatched yet.
                    snapshot_count += 1
                    continue
                # Native page appears before its controls have loaded.
                value = snapshot("", []) if snapshot_count == 0 else states[index]
                if snapshot_count == 0:
                    value["headings"] = []
                snapshot_count += 1
            else:
                action = json.loads(expression.rsplit("})(", 1)[1][:-1])
                actions.append(action)
                if "checkbox" in action:
                    for box in states[index]["checkboxes"]:
                        if box["label"] == action["checkbox"]:
                            box["checked"] = False
                elif not action.get("complete"):
                    index += 1
                if stall == "action":
                    # The click happened, but its receipt was lost. Retrying
                    # the action could advance a second step without observing.
                    continue
                value = True
            await socket.send(
                json.dumps({"id": request["id"], "result": {"result": {"value": value}}})
            )

    async with serve(native, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        actual_client = httpx.AsyncClient

        def client(**kwargs):
            assert kwargs["trust_env"] is False

            def target(request):
                assert str(request.url) == f"http://127.0.0.1:{port}/json/list"
                return httpx.Response(
                    200,
                    json=[
                        {
                            "type": "browser_ui",
                            "url": "chrome://onboarding/",
                            "webSocketDebuggerUrl": f"ws://127.0.0.1:{port}/devtools/page/owned",
                        },
                        {"type": "page", "url": "https://personal.example/"},
                    ],
                )

            return actual_client(transport=httpx.MockTransport(target), **kwargs)

        monkeypatch.setattr("openbiliclaw.sources.ego_onboarding.httpx.AsyncClient", client)
        if stall == "action":
            with pytest.raises(EgoOnboardingError, match="ego_setup_action_timeout"):
                await complete_onboarding(port, timeout=15)
        else:
            await complete_onboarding(port, timeout=20 if stall == "snapshot" else 15)
    if stall == "action":
        assert len(actions) == 1
        assert index == 1
        return
    assert len(actions) == 12
    assert all(not box["checked"] for box in states[-1]["checkboxes"])
    assert actions[4]["button"] == "稍后设置"
    assert actions[-1]["complete"]


async def test_one_prepare_recovers_transient_trust_timeout_without_user_restart(
    manager, monkeypatch
):
    from openbiliclaw.sources import ego_setup

    download = installer_lifecycle(manager, monkeypatch, "")
    original_command = ego_setup._command
    attempts = 0

    async def command(*args, **kwargs):
        nonlocal attempts
        if args[0] == "/usr/sbin/spctl":
            attempts += 1
            if attempts == 1:
                raise TimeoutError
        return await original_command(*args, **kwargs)

    monkeypatch.setattr(ego_setup, "_command", command)
    monkeypatch.setattr(ego_setup.asyncio, "sleep", AsyncMock())
    result = await manager.prepare()
    assert result["setup_state"] == "ready" and result["available"]
    assert attempts == 2
    assert download.await_count == 1


@pytest.mark.parametrize("step", ["/usr/bin/ditto", "/usr/bin/codesign", "/usr/sbin/spctl"])
async def test_setup_command_timeouts_are_bounded_and_retain_cache(manager, monkeypatch, step):
    from openbiliclaw.sources import ego_setup

    download = installer_lifecycle(manager, monkeypatch, "")
    original_command = ego_setup._command
    timeouts = []

    async def command(*args, **kwargs):
        if args[0] == step:
            timeouts.append(kwargs["timeout"])
            raise TimeoutError
        return await original_command(*args, **kwargs)

    monkeypatch.setattr(ego_setup, "_command", command)
    monkeypatch.setattr(ego_setup.asyncio, "sleep", AsyncMock())
    result = await manager.prepare()
    assert result["setup_state"] == "failed" and result["detail"] == "ego_setup_timeout"
    assert len(timeouts) == 3 and timeouts == [timeouts[0] * n for n in (1, 2, 3)]
    assert download.await_count == 1 and (manager.root / "installer.dmg").exists()
    assert not manager._state().get("initialized")


async def test_interrupted_download_recovers_in_one_prepare(manager, monkeypatch):
    import httpx

    download = installer_lifecycle(manager, monkeypatch, "")
    original = download.side_effect
    count = 0

    async def interrupted(path):
        nonlocal count
        count += 1
        assert not path.exists()
        if count == 1:
            path.write_bytes(b"partial")
            raise httpx.ReadTimeout("private download response")
        await original(path)

    download.side_effect = interrupted
    monkeypatch.setattr("openbiliclaw.sources.ego_setup.asyncio.sleep", AsyncMock())
    result = await manager.prepare()
    assert result["available"] and download.await_count == 2
    assert (manager.root / "installer.dmg").read_bytes() == b"complete fixture dmg"


@pytest.mark.parametrize("error", [TimeoutError(), EgoOnboardingError("ego_setup_action_timeout")])
async def test_prepare_reobserves_setup_after_stopping_uncertain_host(manager, monkeypatch, error):
    calls = fake_lifecycle(manager, monkeypatch)
    complete = AsyncMock(side_effect=[error, None])
    monkeypatch.setattr("openbiliclaw.sources.ego_setup.complete_onboarding", complete)
    monkeypatch.setattr("openbiliclaw.sources.ego_setup.asyncio.sleep", AsyncMock())
    result = await manager.prepare()
    assert result["available"] and calls.count("install") == 1
    assert calls.count("debug") == 2 and calls.count("handshake") == 1
    assert calls[calls.index("debug") + 1] == "stop"
    assert complete.await_count == 2


async def test_install_deadline_stops_recovery_and_releases_lock(manager, monkeypatch):
    fake_lifecycle(manager, monkeypatch)
    monkeypatch.setattr("openbiliclaw.sources.ego_setup._INSTALL_TIMEOUT", 0.01)
    entered = asyncio.Event()

    async def install():
        entered.set()
        await asyncio.Future()

    monkeypatch.setattr(manager, "_install", install)
    result = await manager.prepare()
    assert entered.is_set() and result["detail"] == "ego_setup_timeout"
    assert manager.status()["setup_state"] == "failed"
    # Cancellation must release the process lock for a later explicit attempt.
    monkeypatch.setattr(manager, "_install", AsyncMock())
    monkeypatch.setattr("openbiliclaw.sources.ego_setup.complete_onboarding", AsyncMock())
    monkeypatch.setattr("openbiliclaw.sources.ego_setup._INSTALL_TIMEOUT", 1)
    assert (await manager.prepare())["available"]


@pytest.mark.parametrize("mode", ["ranges", "ignored", "wrong_range", "wrong_etag", "short"])
async def test_official_download_ranges_require_exact_identity_and_complete_bytes(
    manager, monkeypatch, tmp_path, mode
):
    import httpx

    data = bytes(range(256)) * (4 * 1024 * 1024 // 256)
    etag = '"official-image"'
    requests = []
    active = 0
    concurrent = asyncio.Event()

    async def respond(request):
        nonlocal active
        requests.append(request)
        headers = {"Content-Length": str(len(data)), "Accept-Ranges": "bytes", "ETag": etag}
        if request.method == "HEAD":
            return httpx.Response(200, headers=headers)
        span = request.headers.get("Range")
        if not span:
            assert mode == "ignored"
            return httpx.Response(200, content=data, headers=headers)
        assert request.headers["If-Match"] == etag
        start, end = map(int, span.removeprefix("bytes=").split("-"))
        active += 1
        if active == 4:
            concurrent.set()
        await asyncio.wait_for(concurrent.wait(), timeout=1)
        if mode == "ignored":
            return httpx.Response(200, content=b"ignored")
        headers["Content-Range"] = f"bytes {start}-{end}/{len(data)}"
        if mode == "wrong_range":
            headers["Content-Range"] = f"bytes 0-{end}/{len(data)}"
        if mode == "wrong_etag":
            headers["ETag"] = '"changed-image"'
        body = data[start : end + 1]
        if mode == "short":
            body = body[:-1]
        return httpx.Response(206, headers=headers, content=body)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        "openbiliclaw.sources.ego_setup.httpx.AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw),
    )
    manager.root.mkdir()
    temporary = tmp_path / "transfer"
    temporary.mkdir()
    if mode in {"ranges", "ignored"}:
        cache = await manager._cached_installer(temporary)
        assert cache.read_bytes() == data and concurrent.is_set()
        assert len(requests) == (5 if mode == "ranges" else 6)
    else:
        with pytest.raises(EgoSetupError, match="download_range"):
            await manager._cached_installer(temporary)
        assert not (manager.root / "installer.dmg").exists()


async def test_failed_range_joins_other_transfers_before_partial_cleanup(
    manager, monkeypatch, tmp_path
):
    import httpx

    finished = asyncio.Event()
    started = asyncio.Event()

    async def respond(request):
        if request.headers["Range"].startswith("bytes=0-"):
            await started.wait()
            raise httpx.ReadTimeout("fixture")
        started.set()
        try:
            await asyncio.Future()
        finally:
            finished.set()

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(httpx.ReadTimeout):
            await manager._download_ranges(
                client, "https://cdn.ego.app/image", tmp_path / "partial", 100, '"same"'
            )
    assert finished.is_set()


async def test_slow_install_does_not_consume_native_connection_budget(manager, monkeypatch):
    fake_lifecycle(manager, monkeypatch)
    monkeypatch.setattr("openbiliclaw.sources.ego_setup._INSTALL_TIMEOUT", 0.3)
    monkeypatch.setattr("openbiliclaw.sources.ego_setup._CONNECT_TIMEOUT", 0.3)
    connect = manager._connect_host

    async def install():
        await asyncio.sleep(0.2)

    async def slow_connect():
        await asyncio.sleep(0.2)
        await connect()

    monkeypatch.setattr(manager, "_install", install)
    monkeypatch.setattr(manager, "_connect_host", slow_connect)
    monkeypatch.setattr("openbiliclaw.sources.ego_setup.complete_onboarding", AsyncMock())
    assert (await manager.prepare())["available"]


async def test_connection_deadline_is_bounded_and_stops_owned_host(manager, monkeypatch):
    fake_lifecycle(manager, monkeypatch)
    monkeypatch.setattr("openbiliclaw.sources.ego_setup._CONNECT_TIMEOUT", 0.01)
    monkeypatch.setattr(manager, "_install", AsyncMock())
    monkeypatch.setattr("openbiliclaw.sources.ego_setup.complete_onboarding", AsyncMock())

    async def handshake():
        await asyncio.Future()

    monkeypatch.setattr(manager, "_handshake", handshake)
    result = await manager.prepare()
    assert result["detail"] == "ego_setup_timeout"
    assert result["setup_state"] == "failed" and not result["available"]
    assert manager._state().get("pid") is None
