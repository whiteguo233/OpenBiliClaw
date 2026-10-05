from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest


def _load_build_module():
    project_root = Path(__file__).resolve().parent.parent
    module_path = project_root / "packaging" / "build.py"
    spec = importlib.util.spec_from_file_location("openbiliclaw_packaging_build", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_module = _load_build_module()


def test_make_archive_name_includes_platform_and_version() -> None:
    assert build_module.make_archive_name("v0.1.1", "macos") == "OpenBiliClaw-macos-v0.1.1.zip"


def test_make_archive_name_strips_backend_release_channel_prefix() -> None:
    assert (
        build_module.make_archive_name("backend-v0.1.3", "windows")
        == "OpenBiliClaw-windows-v0.1.3.zip"
    )


def test_make_bundle_version_strips_backend_release_channel_prefix() -> None:
    assert build_module.make_bundle_version("backend-v0.1.3") == "0.1.3"


def test_windows_file_version_tuple_uses_numeric_prefix() -> None:
    assert build_module.make_windows_file_version_tuple("0.3.103") == (0, 3, 103, 0)
    assert build_module.make_windows_file_version_tuple("v0.3.103.deadbee") == (0, 3, 103, 0)
    assert build_module.make_windows_file_version_tuple("0.3.103-rc1") == (0, 3, 103, 0)


def test_write_windows_version_file_includes_file_and_product_versions(tmp_path: Path) -> None:
    version_file = build_module.write_windows_version_file(
        tmp_path / "version_info.txt",
        version="0.3.103.deadbee",
    )

    text = version_file.read_text(encoding="utf-8")

    assert "filevers=(0, 3, 103, 0)" in text
    assert "prodvers=(0, 3, 103, 0)" in text
    assert "StringStruct('FileVersion', '0.3.103.deadbee')" in text
    assert "StringStruct('ProductVersion', '0.3.103.deadbee')" in text
    assert "StringStruct('OriginalFilename', 'OpenBiliClaw.exe')" in text


def test_inno_installer_sets_numeric_file_version_resource() -> None:
    script = (Path(__file__).resolve().parent.parent / "packaging" / "openbiliclaw.iss").read_text(
        encoding="utf-8"
    )

    assert "#define MyAppVersionInfoVersion" in script
    assert "VersionInfoVersion={#MyAppVersionInfoVersion}" in script
    assert "VersionInfoProductVersion={#MyAppVersionInfoVersion}" in script


def test_inno_installer_silent_upgrade_hands_off_to_fresh_executable() -> None:
    """Silent installs/upgrades must still hand off to the freshly installed exe.

    Interactive installs are now an opt-in Finish-page checkbox (covered by
    tests/test_installer_script.py); /SILENT and /VERYSILENT have no wizard, so
    the unconditional handoff v0.3.182 added must survive on that path.
    """
    script = (Path(__file__).resolve().parent.parent / "packaging" / "openbiliclaw.iss").read_text(
        encoding="utf-8"
    )
    run_entry = next(
        line
        for line in script.splitlines()
        if line.startswith('Filename: "{app}\\{#MyAppExeName}"') and "skipifnotsilent" in line
    )

    assert 'WorkingDir: "{app}"' in run_entry
    assert "Flags: nowait" in run_entry
    assert "postinstall" not in run_entry
    assert "skipifsilent" not in run_entry
    assert "RestartApplications=no" in script
    assert "StopRunningInstance;" in script


def test_pyinstaller_spec_uses_windows_version_file_env() -> None:
    spec = (Path(__file__).resolve().parent.parent / "packaging" / "openbiliclaw.spec").read_text(
        encoding="utf-8"
    )

    assert "OPENBILICLAW_WINDOWS_VERSION_FILE" in spec
    assert "version=version_file" in spec


def test_pyinstaller_spec_uses_platform_brand_icons() -> None:
    root = Path(__file__).resolve().parent.parent
    spec = (root / "packaging" / "openbiliclaw.spec").read_text(encoding="utf-8")

    assert 'project_root / "packaging" / "icon.ico"' in spec
    assert 'project_root / "packaging" / "icon.icns"' in spec
    assert "icon=str(application_icon) if application_icon is not None else None" in spec
    assert (root / "packaging" / "icon.ico").is_file()
    assert (root / "packaging" / "icon.icns").is_file()


def test_build_pyinstaller_install_command_falls_back_to_uv_when_pip_missing() -> None:
    assert build_module.build_pyinstaller_install_command(
        pip_available=False,
        uv_executable="/usr/local/bin/uv",
    ) == ["/usr/local/bin/uv", "pip", "install", "pyinstaller"]


def test_build_reddit_dependency_install_command_uses_default_dependency_spec() -> None:
    cmd = build_module.build_reddit_dependency_install_command(pip_available=True)

    assert cmd[:4] == [build_module.sys.executable, "-m", "pip", "install"]
    assert cmd[4].startswith("rdt-cli>=")


def test_default_runtime_dependency_enables_httpx_socks_proxy_support() -> None:
    requirement = build_module.project_dependency_spec("httpx")

    assert requirement.startswith("httpx[socks]>=")


def test_pyinstaller_spec_collects_reddit_dependency() -> None:
    spec = (Path(__file__).resolve().parent.parent / "packaging" / "openbiliclaw.spec").read_text(
        encoding="utf-8"
    )

    assert "OPENBILICLAW_BUNDLE_REDDIT" in spec
    assert "rdt_cli" in spec
    assert "browser_cookie3" in spec
    assert "_reddit_hiddenimports" in spec


def test_tailnet_helper_filename_is_platform_specific() -> None:
    assert build_module.tailnet_helper_filename("Darwin") == "openbiliclaw-tailnet-helper"
    assert build_module.tailnet_helper_filename("Windows") == "openbiliclaw-tailnet-helper.exe"


def test_build_tailnet_helper_uses_reproducible_pure_go_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "go.mod").write_text("module example.invalid/helper\n", encoding="utf-8")
    (source / "build-tags.txt").write_text("ts_omit_logtail,ts_omit_webclient\n", encoding="utf-8")
    captured: dict[str, object] = {}
    calls: list[list[str]] = []

    def fake_check_call(command, *, cwd, env):
        calls.append(command)
        if "-o" in command:
            captured.update(command=command, cwd=cwd, env=env)
            Path(command[command.index("-o") + 1]).write_bytes(b"helper")

    monkeypatch.setattr(build_module, "TAILNET_HELPER_SOURCE_DIR", source)
    monkeypatch.setattr(build_module.subprocess, "check_call", fake_check_call)
    output = build_module.build_tailnet_helper(
        output_dir=tmp_path / "out",
        go_executable="/toolchain/go",
        platform_name="Darwin",
    )

    assert output.name == "openbiliclaw-tailnet-helper"
    assert captured["cwd"] == str(source)
    assert calls[0] == [
        "/toolchain/go",
        "build",
        "-trimpath",
        "-tags=ts_omit_logtail,ts_omit_webclient",
        "-ldflags=-s -w",
        "-o",
        str(output),
        ".",
    ]
    assert calls[1] == [str(output), "--self-test"]
    assert calls[2] == [
        sys.executable,
        str(build_module.TAILNET_NOTICES_SCRIPT),
        "--check",
    ]
    assert captured["env"]["CGO_ENABLED"] == "0"


def test_pyinstaller_spec_bundles_tailnet_helper_binary() -> None:
    spec = (Path(__file__).resolve().parent.parent / "packaging" / "openbiliclaw.spec").read_text(
        encoding="utf-8"
    )

    assert "OPENBILICLAW_TAILNET_HELPER_BINARY" in spec
    assert "_tailnet_binaries" in spec
    assert 'project_root / "LICENSE"' in spec
    assert "THIRD_PARTY_NOTICES.md" in spec


def test_pyinstaller_spec_collects_httpx_socks_dependency() -> None:
    spec = (Path(__file__).resolve().parent.parent / "packaging" / "openbiliclaw.spec").read_text(
        encoding="utf-8"
    )

    assert '"socksio"' in spec


def test_pyinstaller_spec_collects_websocket_protocol_dependency() -> None:
    spec = (Path(__file__).resolve().parent.parent / "packaging" / "openbiliclaw.spec").read_text(
        encoding="utf-8"
    )

    assert '"websockets"' in spec
    assert '"uvicorn.protocols.websockets.websockets_impl"' in spec


def test_find_packaged_root_prefers_app_bundle_on_macos(tmp_path: Path) -> None:
    app_bundle = tmp_path / "OpenBiliClaw.app"
    app_bundle.mkdir()
    package_dir = tmp_path / "OpenBiliClaw"
    package_dir.mkdir()

    resolved = build_module.find_packaged_root(tmp_path, platform_name="Darwin")

    assert resolved == app_bundle


def test_create_archive_writes_zip_with_packaged_root_contents(tmp_path: Path) -> None:
    packaged_root = tmp_path / "OpenBiliClaw"
    packaged_root.mkdir()
    (packaged_root / "config.example.toml").write_text("language = 'zh'\n", encoding="utf-8")

    archive_path = build_module.create_archive(
        packaged_root=packaged_root,
        output_dir=tmp_path / "release",
        version="v0.1.1",
        target="windows",
    )

    assert archive_path.name == "OpenBiliClaw-windows-v0.1.1.zip"
    assert archive_path.exists()

    with zipfile.ZipFile(archive_path) as archive:
        assert "OpenBiliClaw/config.example.toml" in archive.namelist()


def test_write_macos_first_launch_guide_explains_gatekeeper_paths(tmp_path: Path) -> None:
    guide = build_module.write_macos_first_launch_guide(tmp_path)

    text = guide.read_text(encoding="utf-8")

    assert guide.name == build_module.MACOS_FIRST_LAUNCH_GUIDE_NAME
    assert "Control-click" in text
    assert "无法验证开发者" in text
    assert "System Settings" in text
    assert "Privacy & Security" in text
    assert "仍要打开" in text
    assert "已损坏" in text
    assert "xattr -dr com.apple.quarantine" in text
    assert "codesign --force" not in text
    assert build_module.MACOS_INSTALLER_COMMAND_NAME in text
    assert "退出旧版本、原子替换" in text
    assert "atomically replaces" in text


def test_macos_installer_command_performs_verified_version_handoff() -> None:
    helper = Path(__file__).resolve().parent.parent / "packaging" / "install_macos.command"
    text = helper.read_text(encoding="utf-8")

    assert helper.stat().st_mode & 0o111
    assert "OPENBILICLAW_INSTALL_BUNDLE_ID" in text
    assert "OPENBILICLAW_INSTALL_APP_PROCESS_PATTERN" in text
    assert "OPENBILICLAW_INSTALL_BUNDLED_RUNTIME_PATTERN" in text
    assert "OPENBILICLAW_INSTALL_GRACEFUL_ATTEMPTS" in text
    assert "OPENBILICLAW_INSTALL_LAUNCH_ATTEMPTS" in text
    assert "com.openbiliclaw.desktop" in text
    assert "/usr/bin/ditto --rsrc --extattr --acl" in text
    assert "/usr/bin/codesign --verify --deep --strict" in text
    assert "CFBundleShortVersionString" in text
    assert "stop_old_instance" in text
    assert "stop_bundled_runtime" in text
    assert '/usr/bin/open -n "${TARGET_APP}"' in text
    assert "target_app_pids" in text
    assert "rollback_install" in text
    assert "BACKUP_ACTIVE" in text
    assert "INSTALL_COMPLETE" in text
    assert "run_privileged /bin/chmod 0755" in text
    assert "xattr -d" not in text
    assert "xattr -dr" not in text

    zsh = shutil.which("zsh")
    if zsh is not None:
        subprocess.run([zsh, "-n", str(helper)], check=True)


def test_make_macos_dmg_stages_first_launch_guidance(tmp_path: Path, monkeypatch) -> None:
    app_bundle = tmp_path / "OpenBiliClaw.app"
    app_bundle.mkdir()
    stage = tmp_path / "stage"
    saw_guidance = False

    def fake_mkdtemp(*, prefix: str) -> str:
        assert prefix == "obc-dmg-"
        stage.mkdir()
        return str(stage)

    def fake_check_call(cmd: list[str], **_: object) -> None:
        assert cmd[0] == "ditto"
        shutil.copytree(cmd[1], cmd[2])

    def fake_run(
        cmd: list[str],
        *,
        stdout: object,
        stderr: object,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal saw_guidance
        assert stdout is subprocess.DEVNULL
        assert stderr is subprocess.PIPE
        assert text is True
        assert (stage / "OpenBiliClaw.app").is_dir()
        assert (stage / "Applications").is_symlink()
        installer = stage / build_module.MACOS_INSTALLER_COMMAND_NAME
        assert installer.is_file()
        assert installer.stat().st_mode & 0o111
        assert (stage / build_module.MACOS_FIRST_LAUNCH_GUIDE_NAME).is_file()
        assert (stage / build_module.MACOS_FIRST_LAUNCH_IMAGE_NAME).is_file()
        assert (stage / ".background" / "openbiliclaw-dmg-guide.png").is_file()
        saw_guidance = True
        Path(cmd[-1]).write_text("fake dmg\n", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(build_module.subprocess, "check_call", fake_check_call)
    monkeypatch.setattr(build_module.subprocess, "run", fake_run)

    dmg = build_module.make_macos_dmg(
        app_bundle=app_bundle,
        output_dir=tmp_path / "release",
        version="v0.3.145-arm64",
    )

    assert saw_guidance is True
    assert dmg.name == "OpenBiliClaw-macos-v0.3.145-arm64.dmg"
    assert dmg.exists()


def test_find_ollama_binary_prefers_explicit_path(tmp_path: Path, monkeypatch) -> None:
    fake = tmp_path / "ollama"
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.delenv("OPENBILICLAW_OLLAMA_BIN", raising=False)

    assert build_module.find_ollama_binary(str(fake)) == fake.resolve()


def test_find_ollama_binary_uses_env_when_no_explicit(tmp_path: Path, monkeypatch) -> None:
    fake = tmp_path / "ollama"
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("OPENBILICLAW_OLLAMA_BIN", str(fake))

    assert build_module.find_ollama_binary() == fake.resolve()


def test_find_ollama_binary_returns_none_when_absent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OPENBILICLAW_OLLAMA_BIN", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))  # empty dir → ollama not on PATH

    assert build_module.find_ollama_binary("/nonexistent/ollama") is None


def test_bundle_ollama_binary_copies_into_onedir_with_sibling_lib(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    (src_dir / "lib" / "ollama").mkdir(parents=True)
    ollama = src_dir / "ollama"
    ollama.write_text("binary\n", encoding="utf-8")
    (src_dir / "lib" / "ollama" / "runner").write_text("r\n", encoding="utf-8")

    dist = tmp_path / "dist"
    (dist / "OpenBiliClaw").mkdir(parents=True)

    written = build_module.bundle_ollama_binary(dist, ollama, platform_name="Windows")

    dest = dist / "OpenBiliClaw" / "ollama.exe"
    assert dest in written
    assert dest.exists()
    # Windows ollama needs its runner libs carried along.
    assert (dist / "OpenBiliClaw" / "lib" / "ollama" / "runner").exists()


def test_bundle_ollama_binary_targets_app_resources_on_macos(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    src = src_dir / "ollama"
    src.write_text("bin\n", encoding="utf-8")
    llama_server = src_dir / "llama-server"
    llama_server.write_text("runner\n", encoding="utf-8")
    llama_quantize = src_dir / "llama-quantize"
    llama_quantize.write_text("quantize\n", encoding="utf-8")
    (src_dir / "libllama-server-impl.dylib").write_text("impl\n", encoding="utf-8")
    (src_dir / "libggml.dylib").write_text("ggml\n", encoding="utf-8")
    (src_dir / "libggml-cpu-x64.so").write_text("cpu\n", encoding="utf-8")
    mlx_dir = src_dir / "mlx_metal_v3"
    mlx_dir.mkdir()
    (mlx_dir / "kernels.metallib").write_text("metal\n", encoding="utf-8")
    dist = tmp_path / "dist"
    (dist / "OpenBiliClaw").mkdir(parents=True)
    (dist / "OpenBiliClaw.app" / "Contents" / "Resources").mkdir(parents=True)

    written = build_module.bundle_ollama_binary(dist, src, platform_name="Darwin")

    assert (dist / "OpenBiliClaw" / "ollama") in written
    assert (dist / "OpenBiliClaw.app" / "Contents" / "Resources" / "ollama") in written
    assert (dist / "OpenBiliClaw" / "llama-server") in written
    assert (dist / "OpenBiliClaw.app" / "Contents" / "Resources" / "llama-server") in written
    assert (dist / "OpenBiliClaw" / "libllama-server-impl.dylib") in written
    assert (
        dist / "OpenBiliClaw.app" / "Contents" / "Resources" / "libllama-server-impl.dylib"
    ) in written
    assert (dist / "OpenBiliClaw" / "llama-server").exists()
    assert (dist / "OpenBiliClaw.app" / "Contents" / "Resources" / "llama-server").exists()
    assert (dist / "OpenBiliClaw" / "llama-quantize").exists()
    assert (dist / "OpenBiliClaw.app" / "Contents" / "Resources" / "llama-quantize").exists()
    assert (dist / "OpenBiliClaw" / "libggml.dylib").exists()
    assert (dist / "OpenBiliClaw.app" / "Contents" / "Resources" / "libggml-cpu-x64.so").exists()
    assert (dist / "OpenBiliClaw" / "mlx_metal_v3" / "kernels.metallib").exists()
    assert (
        dist / "OpenBiliClaw.app" / "Contents" / "Resources" / "mlx_metal_v3" / "kernels.metallib"
    ).exists()


def test_bundle_ollama_binary_rejects_incomplete_macos_runtime(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    src = src_dir / "ollama"
    src.write_text("bin\n", encoding="utf-8")
    (src_dir / "llama-server").write_text("runner\n", encoding="utf-8")
    dist = tmp_path / "dist"
    (dist / "OpenBiliClaw.app" / "Contents" / "Resources").mkdir(parents=True)

    with pytest.raises(RuntimeError, match="libllama-server-impl.dylib"):
        build_module.bundle_ollama_binary(dist, src, platform_name="Darwin")


def test_repair_macos_ad_hoc_signature_signs_then_verifies(tmp_path: Path, monkeypatch) -> None:
    app_bundle = tmp_path / "OpenBiliClaw.app"
    app_bundle.mkdir()
    calls: list[list[str]] = []

    monkeypatch.setattr(
        build_module.shutil,
        "which",
        lambda name: "/usr/bin/codesign" if name == "codesign" else None,
    )
    monkeypatch.setattr(build_module.subprocess, "check_call", lambda cmd: calls.append(cmd))

    build_module.repair_macos_ad_hoc_signature(app_bundle)

    assert calls == [
        ["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(app_bundle)],
        [
            "/usr/bin/codesign",
            "--verify",
            "--deep",
            "--strict",
            "--verbose=2",
            str(app_bundle),
        ],
    ]


def test_macos_build_repairs_signature_after_bundle_mutations_before_archives() -> None:
    source = (Path(__file__).resolve().parent.parent / "packaging" / "build.py").read_text(
        encoding="utf-8"
    )
    build_block = source[source.index("def build(") : source.index("def main()")]

    sign_index = build_block.index("repair_macos_ad_hoc_signature(app_bundle)")

    assert sign_index > build_block.index("bundle_ollama_binary(")
    assert sign_index < build_block.index("create_archive(")
    assert sign_index < build_block.index("make_macos_dmg(")


def test_desktop_release_workflow_uses_official_macos_ollama_bundle() -> None:
    workflow = (
        Path(__file__).resolve().parent.parent / ".github" / "workflows" / "release-desktop.yml"
    ).read_text(encoding="utf-8")

    assert "Ollama-darwin.zip" in workflow
    assert "OPENBILICLAW_OLLAMA_BIN" in workflow
    assert "Contents/Resources/llama-server" in workflow
    assert "Contents/Resources/libllama-server-impl.dylib" in workflow
    assert "brew install ollama" not in workflow


def test_desktop_release_workflow_mentions_macos_installer_and_first_launch_guide() -> None:
    workflow = (
        Path(__file__).resolve().parent.parent / ".github" / "workflows" / "release-desktop.yml"
    ).read_text(encoding="utf-8")

    assert "一键安装并启动助手与首次打开说明" in workflow
    assert build_module.MACOS_INSTALLER_COMMAND_NAME in workflow
    assert "macOS 安全阻挡" in workflow
    assert "Control-click" in workflow
    assert "Privacy & Security" in workflow
    assert "xattr -dr com.apple.quarantine" in workflow
    assert "codesign --force" not in workflow


@pytest.mark.parametrize(
    "workflow_name",
    ["release-desktop.yml", "build-installers.yml"],
)
def test_macos_packaging_workflows_run_installer_handoff_e2e(workflow_name: str) -> None:
    workflow = (
        Path(__file__).resolve().parent.parent / ".github" / "workflows" / workflow_name
    ).read_text(encoding="utf-8")

    assert 'pip install -e ".[packaging]" "pytest>=8"' in workflow
    assert "python -m pytest -q tests/test_macos_installer_e2e.py" in workflow


@pytest.mark.parametrize(
    "workflow_name",
    ["release-desktop.yml", "build-installers.yml"],
)
def test_packaging_workflows_prefetch_tailnet_modules_before_every_build(
    workflow_name: str,
) -> None:
    """Every PyInstaller job must warm the offline Go module cache first.

    packaging/build.py invokes scripts/generate_tailnet_notices.py with
    GOPROXY=off, so a job that only sets up Go fails with "module lookup
    disabled by GOPROXY=off" before PyInstaller even starts.
    """
    lines = (
        (Path(__file__).resolve().parent.parent / ".github" / "workflows" / workflow_name)
        .read_text(encoding="utf-8")
        .splitlines()
    )

    builds = [index for index, line in enumerate(lines) if "packaging/build.py" in line]
    prefetches = [
        index
        for index, line in enumerate(lines)
        if "generate_tailnet_notices.py --prefetch" in line
    ]

    assert builds, f"{workflow_name} no longer invokes packaging/build.py"
    remaining = list(prefetches)
    for build in builds:
        earlier = [index for index in remaining if index < build]
        assert earlier, (
            f"{workflow_name}: packaging/build.py at line {build + 1} has no "
            "preceding Tailnet module prefetch step"
        )
        remaining.remove(earlier[-1])


def test_manual_installer_workflow_uses_official_macos_ollama_bundle() -> None:
    workflow = (
        Path(__file__).resolve().parent.parent / ".github" / "workflows" / "build-installers.yml"
    ).read_text(encoding="utf-8")

    assert "Ollama-darwin.zip" in workflow
    assert "OPENBILICLAW_OLLAMA_BIN" in workflow
    assert "Contents/Resources/llama-server" in workflow
    assert "Contents/Resources/libllama-server-impl.dylib" in workflow
    assert "brew install ollama" not in workflow
