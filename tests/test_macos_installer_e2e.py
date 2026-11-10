from __future__ import annotations

import importlib.util
import json
import os
import plistlib
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable


pytestmark = pytest.mark.skipif(
    sys.platform != "darwin",
    reason="The installer handoff E2E requires macOS disk images and app bundles.",
)


def _load_build_module():
    project_root = Path(__file__).resolve().parent.parent
    module_path = project_root / "packaging" / "build.py"
    spec = importlib.util.spec_from_file_location(
        "openbiliclaw_macos_installer_e2e_build",
        module_path,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_module = _load_build_module()


def _wait_for(predicate: Callable[[], bool], *, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return predicate()


def _pids_for_executable(executable: Path) -> set[int]:
    result = subprocess.run(
        ["/usr/bin/pgrep", "-f", re.escape(str(executable))],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode not in {0, 1}:
        raise RuntimeError(f"pgrep failed: {result.stderr.strip()}")
    return {int(line) for line in result.stdout.splitlines() if line.strip()}


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _stop_test_app(executable: Path) -> None:
    pids = _pids_for_executable(executable)
    for pid in pids:
        with suppress(ProcessLookupError):
            os.kill(pid, signal.SIGTERM)
    if _wait_for(lambda: not _pids_for_executable(executable), timeout=3.0):
        return
    for pid in _pids_for_executable(executable):
        with suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)
    _wait_for(lambda: not _pids_for_executable(executable), timeout=3.0)


def _make_signed_test_app(
    app: Path,
    *,
    bundle_id: str,
    version: str,
    launch_marker: Path,
) -> None:
    contents = app / "Contents"
    macos = contents / "MacOS"
    macos.mkdir(parents=True)
    executable = macos / "OpenBiliClaw"
    source = app.parent / f"openbiliclaw-{version}.c"
    source.write_text(
        f"""
#include <signal.h>
#include <stdio.h>
#include <unistd.h>

static volatile sig_atomic_t keep_running = 1;

static void stop_process(int signal_number) {{
    (void)signal_number;
    keep_running = 0;
}}

int main(void) {{
    FILE *marker = fopen({json.dumps(str(launch_marker))}, "w");
    if (marker == NULL) {{
        return 2;
    }}
    if (fputs({json.dumps(version + chr(10))}, marker) == EOF) {{
        fclose(marker);
        return 3;
    }}
    if (fclose(marker) != 0) {{
        return 4;
    }}

    signal(SIGTERM, stop_process);
    signal(SIGINT, stop_process);
    while (keep_running) {{
        sleep(1);
    }}
    return 0;
}}
""".lstrip(),
        encoding="utf-8",
    )
    subprocess.run(
        [
            "/usr/bin/clang",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(source),
            "-o",
            str(executable),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    with (contents / "Info.plist").open("wb") as plist_file:
        plistlib.dump(
            {
                "CFBundleDisplayName": "OpenBiliClaw Installer E2E",
                "CFBundleExecutable": "OpenBiliClaw",
                "CFBundleIdentifier": bundle_id,
                "CFBundleName": "OpenBiliClaw",
                "CFBundlePackageType": "APPL",
                "CFBundleShortVersionString": version,
                "CFBundleVersion": version,
                "LSUIElement": True,
            },
            plist_file,
        )
    subprocess.run(
        ["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(app)],
        check=True,
        capture_output=True,
        text=True,
    )


def test_mounted_dmg_installer_replaces_old_app_and_launches_new_version(
    tmp_path: Path,
) -> None:
    required_commands = ("clang", "codesign", "ditto", "hdiutil", "open", "pgrep", "zsh")
    missing = [command for command in required_commands if shutil.which(command) is None]
    if missing:
        pytest.skip(f"Required macOS commands are unavailable: {', '.join(missing)}")

    bundle_id = f"com.openbiliclaw.installere2e.{uuid.uuid4().hex}"
    applications = tmp_path / "Applications"
    target_app = applications / "OpenBiliClaw.app"
    source_app = tmp_path / "source" / "OpenBiliClaw.app"
    old_marker = tmp_path / "old-version-launched.txt"
    new_marker = tmp_path / "new-version-launched.txt"
    target_executable = target_app / "Contents" / "MacOS" / "OpenBiliClaw"
    mount_point = tmp_path / "mounted-dmg"
    mounted = False

    applications.mkdir()
    source_app.parent.mkdir()
    _make_signed_test_app(
        target_app,
        bundle_id=bundle_id,
        version="1.0.0",
        launch_marker=old_marker,
    )
    _make_signed_test_app(
        source_app,
        bundle_id=bundle_id,
        version="2.0.0",
        launch_marker=new_marker,
    )

    try:
        subprocess.run(
            ["/usr/bin/open", "-n", str(target_app)],
            check=True,
            capture_output=True,
            text=True,
        )
        assert _wait_for(old_marker.exists), "The old test app did not launch."
        assert _wait_for(lambda: bool(_pids_for_executable(target_executable)))
        old_pids = _pids_for_executable(target_executable)

        dmg = build_module.make_macos_dmg(
            app_bundle=source_app,
            output_dir=tmp_path / "release",
            version="v2.0.0-installer-e2e",
        )
        mount_point.mkdir()
        attach = subprocess.run(
            [
                "/usr/bin/hdiutil",
                "attach",
                "-readonly",
                "-nobrowse",
                "-mountpoint",
                str(mount_point),
                str(dmg),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert attach.returncode == 0, attach.stderr
        mounted = True

        installer = mount_point / build_module.MACOS_INSTALLER_COMMAND_NAME
        assert installer.is_file()
        assert installer.stat().st_mode & 0o111
        environment = os.environ.copy()
        environment.update(
            {
                "OPENBILICLAW_INSTALL_TARGET_APP": str(target_app),
                "OPENBILICLAW_INSTALL_BUNDLE_ID": bundle_id,
                "OPENBILICLAW_INSTALL_APP_PROCESS_PATTERN": re.escape(str(target_executable)),
                "OPENBILICLAW_INSTALL_BUNDLED_RUNTIME_PATTERN": (
                    "[/]openbiliclaw-installer-e2e-runtime-never-matches[.]invalid"
                ),
                "OPENBILICLAW_INSTALL_GRACEFUL_ATTEMPTS": "2",
                "OPENBILICLAW_INSTALL_TERM_ATTEMPTS": "10",
                "OPENBILICLAW_INSTALL_KILL_ATTEMPTS": "10",
                "OPENBILICLAW_INSTALL_LAUNCH_ATTEMPTS": "30",
            }
        )
        installed = subprocess.run(
            [str(installer)],
            cwd=mount_point,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        diagnostics = (
            f"stdout:\n{installed.stdout}\n"
            f"stderr:\n{installed.stderr}\n"
            f"return code: {installed.returncode}"
        )
        assert installed.returncode == 0, diagnostics
        assert "Done: v2.0.0 is running from" in installed.stdout, diagnostics

        assert _wait_for(new_marker.exists), diagnostics
        assert new_marker.read_text(encoding="utf-8").strip() == "2.0.0"
        assert all(not _pid_is_alive(pid) for pid in old_pids)
        new_pids = _pids_for_executable(target_executable)
        assert new_pids
        assert old_pids.isdisjoint(new_pids)

        with (target_app / "Contents" / "Info.plist").open("rb") as plist_file:
            installed_plist = plistlib.load(plist_file)
        assert installed_plist["CFBundleShortVersionString"] == "2.0.0"
        subprocess.run(
            [
                "/usr/bin/codesign",
                "--verify",
                "--deep",
                "--strict",
                str(target_app),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        assert not list(applications.glob(".openbiliclaw-install.*"))
    finally:
        _stop_test_app(target_executable)
        if mounted:
            detach = subprocess.run(
                ["/usr/bin/hdiutil", "detach", str(mount_point)],
                check=False,
                capture_output=True,
                text=True,
            )
            if detach.returncode != 0:
                subprocess.run(
                    ["/usr/bin/hdiutil", "detach", "-force", str(mount_point)],
                    check=False,
                    capture_output=True,
                    text=True,
                )


def test_notarized_dmg_mounts_with_finder_drag_layout(tmp_path: Path) -> None:
    """Finder must get its layout from the actual read-only distribution volume."""
    ds_store = pytest.importorskip("ds_store")
    mac_alias = pytest.importorskip("mac_alias")
    app = tmp_path / "OpenBiliClaw.app"
    _make_signed_test_app(
        app,
        bundle_id=f"com.openbiliclaw.dmg-layout.{uuid.uuid4().hex}",
        version="1.0.0",
        launch_marker=tmp_path / "unused-marker",
    )
    dmg = build_module.make_macos_dmg(
        app_bundle=app, output_dir=tmp_path / "release", version="v1.0.0", notarized=True
    )
    mount = tmp_path / "mounted"
    mount.mkdir()
    subprocess.run(
        ["hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint", str(mount), str(dmg)],
        check=True,
        capture_output=True,
        timeout=60,
    )
    try:
        with ds_store.DSStore.open(str(mount / ".DS_Store"), "r") as store:
            assert store["."]["icvl"] == (b"type", b"icnv")
            icon_view = store["."]["icvp"]
            assert icon_view["backgroundType"] == 2
            assert icon_view["arrangeBy"] == "none"
            background = mac_alias.Alias.from_bytes(icon_view["backgroundImageAlias"])
            assert (mount / background.target.filename).is_file()
            app_x, app_y = store[app.name]["Iloc"]
            applications_x, applications_y = store["Applications"]["Iloc"]
            assert app_x < applications_x
            assert app_y == applications_y

        assert {entry.name for entry in mount.iterdir() if not entry.name.startswith(".")} == {
            app.name,
            "Applications",
        }
        assert (mount / "Applications").readlink() == Path("/Applications")
        subprocess.run(
            ["codesign", "--verify", "--deep", "--strict", str(mount / app.name)],
            check=True,
            capture_output=True,
            timeout=30,
        )
    finally:
        subprocess.run(
            ["hdiutil", "detach", str(mount)], check=True, capture_output=True, timeout=60
        )
