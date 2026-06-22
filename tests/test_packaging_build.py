from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path


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
    assert "VersionInfoProductVersion={#MyAppVersion}" in script


def test_pyinstaller_spec_uses_windows_version_file_env() -> None:
    spec = (Path(__file__).resolve().parent.parent / "packaging" / "openbiliclaw.spec").read_text(
        encoding="utf-8"
    )

    assert "OPENBILICLAW_WINDOWS_VERSION_FILE" in spec
    assert "version=version_file" in spec


def test_build_pyinstaller_install_command_falls_back_to_uv_when_pip_missing() -> None:
    assert build_module.build_pyinstaller_install_command(
        pip_available=False,
        uv_executable="/usr/local/bin/uv",
    ) == ["/usr/local/bin/uv", "pip", "install", "pyinstaller"]


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
    src = tmp_path / "ollama"
    src.write_text("bin\n", encoding="utf-8")
    dist = tmp_path / "dist"
    (dist / "OpenBiliClaw").mkdir(parents=True)
    (dist / "OpenBiliClaw.app" / "Contents" / "Resources").mkdir(parents=True)

    written = build_module.bundle_ollama_binary(dist, src, platform_name="Darwin")

    assert (dist / "OpenBiliClaw" / "ollama") in written
    assert (dist / "OpenBiliClaw.app" / "Contents" / "Resources" / "ollama") in written


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
