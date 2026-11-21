"""Windows Beta setup and native transport fixtures, with a real lock test on Windows."""

import asyncio
import json
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openbiliclaw.proc import no_window_kwargs
from openbiliclaw.sources import ego_setup, ego_windows
from openbiliclaw.sources.ego_executor import _open_windows_rpc
from openbiliclaw.sources.workflow_executors import BrowserWorkflowExecutionError


@pytest.fixture
def windows_setup(monkeypatch, tmp_path):
    for name in ego_setup._EXTERNAL_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(ego_setup, "sys", SimpleNamespace(platform="win32"))
    installation = tmp_path / "installed Beta"
    installation.mkdir()
    cli, app = (installation / name for name in ("ego-browser.exe", "ego.exe"))
    cli.touch()
    app.touch()
    monkeypatch.setattr(ego_windows, "installation", lambda: (cli, app))
    manager = ego_setup.EgoSetupManager(tmp_path / "data")
    return manager


def test_installed_beta_paths_are_absolute_and_separate(tmp_path, monkeypatch):
    cli, app = (tmp_path / name for name in ("ego-browser.exe", "ego.exe"))
    cli.touch()
    app.touch()
    monkeypatch.setenv("OPENBILICLAW_EGO_WINDOWS_CLI", str(cli))
    monkeypatch.setenv("OPENBILICLAW_EGO_WINDOWS_APP", str(app))
    assert ego_windows.installation() == (cli.resolve(), app.resolve())
    monkeypatch.setenv("OPENBILICLAW_EGO_WINDOWS_APP", str(cli))
    assert ego_windows.installation() is None


def test_silent_install_resolves_registered_version_before_onboarding(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENBILICLAW_EGO_WINDOWS_CLI", raising=False)
    monkeypatch.delenv("OPENBILICLAW_EGO_WINDOWS_APP", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(ego_windows.shutil, "which", lambda _: None)
    application = tmp_path / "Application"
    app = application / "ego.exe"
    cli = application / "0.6.3.17" / "ego-browser.exe"
    cli.parent.mkdir(parents=True)
    app.touch()
    cli.touch()
    stale_cli = application / "0.6.3.99" / "ego-browser.exe"
    stale_cli.parent.mkdir()
    stale_cli.touch()
    monkeypatch.setattr(ego_windows, "_registered_versions", lambda: [(application, "0.6.3.17")])

    assert ego_windows.installation() == (cli.resolve(), app.resolve())
    monkeypatch.setenv("OPENBILICLAW_EGO_WINDOWS_CLI", str(application / "absent-cli.exe"))
    assert ego_windows.installation() is None
    monkeypatch.delenv("OPENBILICLAW_EGO_WINDOWS_CLI")
    monkeypatch.setenv("OPENBILICLAW_EGO_WINDOWS_APP", str(application / "absent-app.exe"))
    assert ego_windows.installation() is None


async def test_windows_missing_beta_remains_read_only(windows_setup, monkeypatch):
    monkeypatch.setattr(ego_windows, "installation", lambda: None)
    manager = ego_setup.EgoSetupManager(windows_setup.root.parent)
    status = manager.status()
    assert status["platform_supported"] and not status["installed"] and not status["available"]
    assert status["setup_mode"] == "manual_install"
    assert await manager.prepare() == status
    assert not manager.root.exists()


async def test_windows_connects_signed_beta_without_touching_macos_paths(
    windows_setup, monkeypatch
):
    manager = windows_setup
    launch = AsyncMock(return_value=SimpleNamespace(pid=921, returncode=None))
    command = AsyncMock(
        side_effect=["OBC_EGO_SIGNED\n", 'OBC_EGO_READY:{"version":"0.6.2.7","profile":true}']
    )
    monkeypatch.setattr(ego_setup, "_command", command)
    monkeypatch.setattr(ego_setup.asyncio, "create_subprocess_exec", launch)
    monkeypatch.setattr(ego_windows, "browser_process", lambda *_: 123456)
    monkeypatch.setattr(ego_setup, "complete_onboarding", AsyncMock(side_effect=AssertionError))
    manager.root.mkdir(parents=True)

    await manager._connect_host()
    assert manager.status()["available"]
    assert manager.status()["setup_state"] == "ready"
    args = launch.await_args.args
    assert args[0] == str(manager.app)
    assert f"--user-data-dir={manager.profile_dir}" in args
    assert "--headless=new" in args and "--no-startup-window" not in args
    assert args[-1] == "about:blank"
    assert not any("--remote-debugging" in arg for arg in args)
    assert command.await_args_list[0].args[1:4] == (
        "-NoProfile",
        "-NonInteractive",
        "-EncodedCommand",
    )
    assert "verifyWindowsHost(921," in command.await_args.args[-1]
    assert command.await_args.args[1] == f"--ego-server-name={manager.server_name}"


async def test_windows_refuses_unverified_binaries_before_launch(windows_setup, monkeypatch):
    launch = AsyncMock()
    monkeypatch.setattr(ego_setup, "_command", AsyncMock(return_value="unverified"))
    monkeypatch.setattr(ego_setup.asyncio, "create_subprocess_exec", launch)
    windows_setup.root.mkdir(parents=True)
    with pytest.raises(ego_setup.EgoSetupError, match="signature_unverified"):
        await windows_setup._launch(debugging=False)
    launch.assert_not_awaited()


def test_windows_does_not_terminate_recycled_pid(windows_setup, monkeypatch):
    manager = windows_setup
    manager.root.mkdir(parents=True)
    manager._write(pid=921, process_created=123, ready_pid=921)
    inspect = Mock(return_value=987)
    monkeypatch.setattr(ego_windows, "browser_process", inspect)
    assert manager._pid() is None
    manager._stop_windows(0)
    assert all(not call.kwargs for call in inspect.call_args_list)


@pytest.mark.skipif(sys.platform != "win32", reason="needs real Windows file locking")
def test_windows_kernel_leases_exclude_reaper_until_shared_handles_close(tmp_path):
    path = tmp_path / "leases.lock"
    with path.open("a+b") as shared, path.open("a+b") as another, path.open("a+b") as exclusive:
        ego_windows.lock_file(shared, shared=True, blocking=False)
        ego_windows.lock_file(another, shared=True, blocking=False)
        with pytest.raises(BlockingIOError):
            ego_windows.lock_file(exclusive, shared=False, blocking=False)
    with path.open("a+b") as exclusive, path.open("rb") as shared_reader:
        ego_windows.lock_file(exclusive, shared=False, blocking=False)
        with pytest.raises(BlockingIOError):
            ego_windows.lock_file(shared_reader, shared=True, blocking=False)


@pytest.mark.skipif(sys.platform != "win32", reason="needs the Windows process API")
def test_windows_process_handle_refuses_wrong_image_and_creation_time(tmp_path):
    image = Path(sys.executable).resolve()
    with subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(20)"], **no_window_kwargs()
    ) as child:
        try:
            assert ego_windows.browser_process(child.pid, tmp_path / "bogus.exe") is None
            birth = ego_windows.browser_process(child.pid, image)
            assert birth is not None
            assert ego_windows.browser_process(child.pid, image, stop_at=birth + 1) is None
            assert child.poll() is None
            assert ego_windows.browser_process(child.pid, image, stop_at=birth) == birth
            child.wait(5)
        finally:
            if child.poll() is None:
                child.kill()


async def test_windows_rpc_authenticates_server_before_disclosing_client_proof():
    finished = asyncio.Event()

    async def impersonator(reader, writer):
        try:
            assert "hello" in json.loads(await reader.readline())
            writer.write(b'{"server":"bad"}\n')
            await writer.drain()
            assert await reader.read() == b""
        finally:
            finished.set()
            writer.close()

    async with await asyncio.start_server(impersonator, "127.0.0.1", 0) as listener:
        with pytest.raises(BrowserWorkflowExecutionError, match="required browser command"):
            await _open_windows_rpc(listener.sockets[0].getsockname()[1], secrets.token_hex(32))
        await asyncio.wait_for(finished.wait(), 3)


@pytest.mark.parametrize("identity", ["owned", "wrong_parent", "wrong_image"])
async def test_windows_rpc_integrates_with_fixed_node_host(tmp_path, identity):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the native host fixture")
    token = secrets.token_hex(32)
    root = Path(__file__).resolve().parents[1]
    host = root / "src/openbiliclaw/sources/ego_host.mjs"
    fixture = root / "tests/fixtures/ego_windows_transport.mjs"
    ready = tmp_path / "ready.json"
    process = await asyncio.create_subprocess_exec(
        node,
        str(fixture),
        str(host),
        str(ready),
        token,
        identity,
    )
    try:
        async with asyncio.timeout(5):
            while not ready.exists():
                await asyncio.sleep(0.02)
            result = json.loads(ready.read_text())
            if identity != "owned":
                assert result == {"error": "ego_windows_host_identity"}
                assert await process.wait() == 0
                return

            # An unauthenticated or wrong-key client cannot send commands.
            reader, writer = await asyncio.open_connection("127.0.0.1", result["port"])
            writer.write(b'{"id":1,"operation":"close"}\n')
            await writer.drain()
            assert await reader.read() == b""
            writer.close()
            await writer.wait_closed()
            with pytest.raises(BrowserWorkflowExecutionError):
                await _open_windows_rpc(result["port"], secrets.token_hex(32))

            reader, writer = await _open_windows_rpc(result["port"], token)
            for request in ({"id": 1, "operation": "screenshot"}, {"id": 2, "operation": "close"}):
                writer.write((json.dumps(request) + "\n").encode())
                await writer.drain()
                assert json.loads(await reader.readline()) == {
                    "id": request["id"],
                    "result": "fixture-png" if request["id"] == 1 else True,
                }
            writer.close()
            await writer.wait_closed()
            assert await process.wait() == 0
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
