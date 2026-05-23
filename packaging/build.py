#!/usr/bin/env python3
"""Build script for OpenBiliClaw desktop application.

Usage:
    python packaging/build.py          # Build for current platform
    python packaging/build.py --clean  # Clean previous builds first
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import platform
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = PROJECT_ROOT / "dist"
RELEASE_DIR = DIST_DIR / "release"
PYPROJECT_FILE = PROJECT_ROOT / "pyproject.toml"
SPEC_FILE = PROJECT_ROOT / "packaging" / "openbiliclaw.spec"


def ensure_pyinstaller() -> None:
    """Ensure PyInstaller is installed."""
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("[build] Installing PyInstaller ...")
        install_cmd = build_pyinstaller_install_command()
        subprocess.check_call(install_cmd)
        if install_cmd[2:4] == ["ensurepip", "--upgrade"]:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])


def clean() -> None:
    """Remove previous build artifacts."""
    for d in [DIST_DIR, PROJECT_ROOT / "build"]:
        if d.exists():
            print(f"[build] Removing {d}")
            shutil.rmtree(d)


def read_project_version() -> str:
    """Read the project version from pyproject.toml."""
    data = tomllib.loads(PYPROJECT_FILE.read_text(encoding="utf-8"))
    version = data["project"]["version"]
    return str(version)


def build_pyinstaller_install_command(
    *,
    pip_available: bool | None = None,
    uv_executable: str | None = None,
) -> list[str]:
    """Return the best install command for PyInstaller in the current environment."""
    resolved_pip_available = (
        pip_available
        if pip_available is not None
        else importlib.util.find_spec("pip") is not None
    )
    if resolved_pip_available:
        return [sys.executable, "-m", "pip", "install", "pyinstaller"]

    resolved_uv = uv_executable if uv_executable is not None else shutil.which("uv")
    if resolved_uv:
        return [resolved_uv, "pip", "install", "pyinstaller"]

    return [sys.executable, "-m", "ensurepip", "--upgrade"]


def normalize_release_version(version: str) -> str:
    """Normalize release tags like backend-v0.1.3 to a user-facing v0.1.3."""
    if "-v" in version:
        _, _, suffix = version.rpartition("-v")
        return f"v{suffix}"
    return version if version.startswith("v") else f"v{version}"


def make_bundle_version(version: str) -> str:
    """Normalize a tag-style version for bundle metadata."""
    return normalize_release_version(version).removeprefix("v")


def detect_target(platform_name: str | None = None) -> str:
    """Map runtime platform names to archive target labels."""
    resolved = platform_name or platform.system()
    if resolved == "Darwin":
        return "macos"
    if resolved == "Windows":
        return "windows"
    return "linux"


def make_archive_name(version: str, target: str) -> str:
    """Return the versioned archive filename for a packaged backend."""
    return f"OpenBiliClaw-{target}-{normalize_release_version(version)}.zip"


def find_packaged_root(dist_dir: Path, platform_name: str | None = None) -> Path:
    """Return the packaged root directory or bundle produced by PyInstaller."""
    resolved = platform_name or platform.system()
    if resolved == "Darwin":
        app_bundle = dist_dir / "OpenBiliClaw.app"
        if app_bundle.exists():
            return app_bundle

    package_dir = dist_dir / "OpenBiliClaw"
    if package_dir.exists():
        return package_dir

    raise FileNotFoundError(f"No packaged output found under {dist_dir}")


def create_archive(
    *,
    packaged_root: Path,
    output_dir: Path,
    version: str,
    target: str,
) -> Path:
    """Create a zip archive containing the packaged backend root.

    On macOS the ``.app`` bundle contains directory symlinks (notably
    ``Contents/Frameworks/python3.X`` → ``python3__dot__X``) that must
    survive the roundtrip — otherwise the bundled interpreter fails to
    import ``_struct`` on first run.  ``shutil.make_archive('zip')``
    silently flattens symlinks into empty directories, so we shell out
    to the system ``zip`` with ``-y`` (store symbolic links as symlinks).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_name = make_archive_name(version, target)
    archive_path = output_dir / archive_name
    if archive_path.exists():
        archive_path.unlink()

    if target == "macos" and shutil.which("zip"):
        subprocess.check_call(
            ["zip", "-r", "-y", "-q", str(archive_path), packaged_root.name],
            cwd=str(packaged_root.parent),
        )
        return archive_path

    archive_base = output_dir / archive_name.removesuffix(".zip")
    return Path(
        shutil.make_archive(
            str(archive_base),
            "zip",
            root_dir=str(packaged_root.parent),
            base_dir=packaged_root.name,
        ),
    )


def apply_macos_bundle_fixes(dist_dir: Path) -> None:
    """Post-build fixups required for the macOS ``.app`` bundle.

    PyInstaller substitutes dots in bundle-internal directory names
    (``python3.13`` → ``python3__dot__13``) for code-signing reasons,
    but the Python bootloader inside the bundled interpreter still
    resolves ``lib-dynload`` through the dotted path.  Without the
    symlink the very first ``import struct`` during bootstrap fails
    with ``ModuleNotFoundError: No module named '_struct'``.

    We add a compatibility symlink alongside the dot-substituted
    directory so both names resolve to the same contents.
    """
    app_bundle = dist_dir / "OpenBiliClaw.app"
    if not app_bundle.exists():
        return

    frameworks_dir = app_bundle / "Contents" / "Frameworks"
    if not frameworks_dir.is_dir():
        return

    for entry in frameworks_dir.iterdir():
        if not entry.is_dir():
            continue
        name = entry.name
        if "__dot__" not in name:
            continue
        # e.g. python3__dot__13 → python3.13
        alias_name = name.replace("__dot__", ".")
        alias = frameworks_dir / alias_name
        if alias.exists() or alias.is_symlink():
            continue
        alias.symlink_to(entry.name)
        print(f"[build] Added .app compatibility symlink: {alias_name} -> {name}")


def build(*, archive_version: str | None = None) -> None:
    """Run PyInstaller."""
    ensure_pyinstaller()
    bundle_version = make_bundle_version(archive_version or read_project_version())
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        str(SPEC_FILE),
        "--distpath", str(DIST_DIR),
        "--workpath", str(PROJECT_ROOT / "build"),
        "--noconfirm",
    ]
    print(f"[build] Running: {' '.join(cmd)}")
    env = os.environ.copy()
    env["OPENBILICLAW_BUNDLE_VERSION"] = bundle_version
    subprocess.check_call(cmd, cwd=str(PROJECT_ROOT), env=env)

    if platform.system() == "Darwin":
        apply_macos_bundle_fixes(DIST_DIR)

    packaged_root = find_packaged_root(DIST_DIR)
    output = DIST_DIR / "OpenBiliClaw"
    if output.exists():
        # Copy config.example.toml into the output directory
        example = PROJECT_ROOT / "config.example.toml"
        if example.exists():
            shutil.copyfile(example, output / "config.example.toml")

        print()
        print("=" * 60)
        print(f"  Build complete!  {platform.system()} / {platform.machine()}")
        print(f"  Output: {packaged_root}")
        print()
        print("  To run:")
        if platform.system() == "Windows":
            print(f"    {output / 'OpenBiliClaw.exe'}")
        elif platform.system() == "Darwin":
            app_bundle = DIST_DIR / "OpenBiliClaw.app"
            if app_bundle.exists():
                print(f"    open {app_bundle}")
            else:
                print(f"    {output / 'OpenBiliClaw'}")
        else:
            print(f"    {output / 'OpenBiliClaw'}")

        if archive_version:
            target = detect_target()
            archive_path = create_archive(
                packaged_root=packaged_root,
                output_dir=RELEASE_DIR,
                version=archive_version,
                target=target,
            )
            print()
            print(f"  Release archive: {archive_path}")
        print("=" * 60)
    else:
        print("[build] WARNING: Expected output directory not found!")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build OpenBiliClaw desktop app")
    parser.add_argument("--clean", action="store_true", help="Clean previous builds first")
    parser.add_argument(
        "--archive-version",
        help="Also create a release zip using the given version tag, e.g. v0.1.1",
    )
    args = parser.parse_args()

    if args.clean:
        clean()
    build(archive_version=args.archive_version)


if __name__ == "__main__":
    main()
