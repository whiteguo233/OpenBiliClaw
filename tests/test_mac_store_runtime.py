"""Store builds must not escape their profile or mutate other install channels."""

import sys
from unittest.mock import AsyncMock

import pytest

from openbiliclaw.runtime import autostart, updater


def test_store_channel_requires_a_frozen_bundle_marker(tmp_path, monkeypatch):
    from openbiliclaw.runtime.distribution import is_mac_app_store

    executable = tmp_path / "Store.app/Contents/MacOS/OpenBiliClawBackend"
    marker = executable.parent.parent / "Resources/mac-app-store.json"
    marker.parent.mkdir(parents=True)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert not is_mac_app_store()
    marker.write_text("{}")
    assert is_mac_app_store()
    monkeypatch.setattr(sys, "frozen", False)
    assert not is_mac_app_store()


def test_store_profile_rejects_an_external_or_symlinked_override(tmp_path):
    from openbiliclaw.runtime.distribution import store_profile_root

    home = tmp_path / "Container/Data"
    home.mkdir(parents=True)
    default = home / "Library/Application Support/OpenBiliClaw"
    assert store_profile_root(home, {}) == default
    assert store_profile_root(home, {"OPENBILICLAW_PROJECT_ROOT": str(tmp_path)}) == default
    link = home / "escape"
    link.symlink_to(tmp_path, target_is_directory=True)
    assert store_profile_root(home, {"OPENBILICLAW_PROJECT_ROOT": str(link)}) == default
    isolated = default / "Testing"
    assert store_profile_root(home, {"OPENBILICLAW_PROJECT_ROOT": str(isolated)}) == isolated


def test_store_autostart_never_uses_launch_agents(monkeypatch):
    monkeypatch.setattr(autostart, "is_mac_app_store", lambda: True)
    assert autostart.get_manager() is None
    state = autostart.status()
    assert state.reason == "managed_by_store_app"
    assert not state.supported


@pytest.mark.asyncio
async def test_store_updates_do_not_check_or_apply_github_releases(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "is_mac_app_store", lambda: True)
    service = updater.AutoUpdateService(enabled=True)
    fetch = AsyncMock(side_effect=AssertionError("store must not poll GitHub installers"))
    monkeypatch.setattr(service, "_fetch_latest_candidate", fetch)
    assert updater.detect_install_mode() == "mac_app_store"
    status = await service.check_now()
    assert status["reason"] == "app_store_managed"
    assert status["auto_update_enabled"] is False
    assert status["latest_tag"] == ""
    runtime = service.get_runtime_status()
    assert runtime["latest_remote_version"] == ""
    assert runtime["last_update_check_at"] == ""
    assert runtime["last_update_error"] == ""
    result = await service.check_and_update_if_due()
    assert result["reason"] == "app_store_managed"
    code, applied = await service.request_apply(tag="backend-v9.0.0")
    assert code == 409
    assert applied["reason"] == "app_store_managed"
    fetch.assert_not_awaited()


@pytest.mark.skipif(sys.platform == "win32", reason="Mac/POSIX lifetime pipe")
def test_host_pipe_eof_reaps_uvicorn_and_its_worker(tmp_path):
    """Real Uvicorn signal replay must not bypass the parent's cleanup block."""
    import subprocess

    script = tmp_path / "lifetime.py"
    script.write_text("""import subprocess, sys, uvicorn
from fastapi import FastAPI
from openbiliclaw.runtime.distribution import start_store_parent_watchdog
worker = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(120)'])
server = uvicorn.Server(uvicorn.Config(FastAPI(), host='127.0.0.1', port=0, log_level='error'))
start_store_parent_watchdog(lambda: setattr(server, 'should_exit', True))
print(worker.pid, flush=True)
try:
    server.run()
finally:
    worker.terminate()
    worker.wait(timeout=5)
    print('REAPED', flush=True)
""")
    proc = subprocess.Popen(
        [sys.executable, str(script)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    worker_pid = int(proc.stdout.readline())
    proc.stdin.close()
    try:
        assert proc.wait(timeout=15) == 0
        assert "REAPED" in proc.stdout.read()
        import os

        with pytest.raises(ProcessLookupError):
            os.kill(worker_pid, 0)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        import contextlib

        with contextlib.suppress(ProcessLookupError):
            os.kill(worker_pid, 15)


def test_store_static_mime_types_do_not_read_system_configuration(monkeypatch):
    import mimetypes

    from openbiliclaw.runtime.distribution import prepare_store_python

    for name in (
        "knownfiles",
        "inited",
        "_db",
        "types_map",
        "suffix_map",
        "encodings_map",
        "common_types",
    ):
        monkeypatch.setattr(mimetypes, name, getattr(mimetypes, name))

    def deny_system_read(*args, **kwargs):
        raise PermissionError("App Sandbox denies /etc/apache2/mime.types")

    monkeypatch.setattr(mimetypes.MimeTypes, "read", deny_system_read)
    prepare_store_python()
    assert mimetypes.guess_type("index.html")[0] == "text/html"
    assert mimetypes.guess_type("app.js")[0] in ("text/javascript", "application/javascript")
    assert mimetypes.guess_type("icon.svg")[0] == "image/svg+xml"
