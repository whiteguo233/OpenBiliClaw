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
import re
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


def build_x_extra_install_command(*, pip_available: bool | None = None) -> list[str]:
    """Return the command that ensures the X bundle dependency is installed.

    Desktop bundles ship X (Twitter) discovery by default (spec §8 = always
    bundle), so ``twitter-cli`` + its ``curl_cffi`` native binaries must be
    present in the build interpreter for PyInstaller to collect them. We install
    the project's own backwards-compatible ``x`` extra rather than naming the
    dependency here, so there is a single source of truth for the pin.
    """
    resolved_pip_available = (
        pip_available if pip_available is not None else importlib.util.find_spec("pip") is not None
    )
    target = f"{PROJECT_ROOT}[x]"
    if resolved_pip_available:
        return [sys.executable, "-m", "pip", "install", target]
    uv = shutil.which("uv")
    if uv:
        return [uv, "pip", "install", target]
    return [sys.executable, "-m", "pip", "install", target]


def ensure_x_extra() -> bool:
    """Ensure ``twitter_cli`` (and its ``curl_cffi``) are importable for bundling.

    Returns ``True`` once the X discovery dependency is available in the build
    interpreter — either because it was already installed or because we just
    installed the backwards-compatible ``openbiliclaw[x]`` extra. The PyInstaller spec only collects
    ``twitter_cli`` / ``curl_cffi`` when ``OPENBILICLAW_BUNDLE_X=1`` (set by
    :func:`build`), so a failed install degrades to an X-free bundle instead of
    breaking the whole build.
    """
    if importlib.util.find_spec("twitter_cli") is not None:
        return True
    install_cmd = build_x_extra_install_command()
    print("[build] Installing default X dependency (twitter-cli) for desktop bundle ...")
    try:
        subprocess.check_call(install_cmd)
    except subprocess.CalledProcessError as exc:
        print(
            f"[build] WARNING: could not install the X dependency ({exc}); "
            "the bundle will ship without X (Twitter) discovery"
        )
        return False
    return importlib.util.find_spec("twitter_cli") is not None


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
        pip_available if pip_available is not None else importlib.util.find_spec("pip") is not None
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


def make_windows_file_version_tuple(version: str) -> tuple[int, int, int, int]:
    """Return a numeric Windows PE file version tuple from a display version.

    Windows VERSIONINFO requires integer fields. Release labels may contain a
    channel prefix, architecture suffix, prerelease marker, or commit stamp, so
    use the leading semantic version and zero-fill the fourth component.
    """
    normalized = make_bundle_version(version)
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?", normalized)
    if match is None:
        raise ValueError(f"cannot derive Windows file version from {version!r}")
    parts = [int(part) if part is not None else 0 for part in match.groups()]
    return (parts[0], parts[1], parts[2], parts[3])


def _version_string_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def write_windows_version_file(path: Path, *, version: str) -> Path:
    """Write a PyInstaller VERSIONINFO resource file for OpenBiliClaw.exe."""
    file_version = make_windows_file_version_tuple(version)
    file_version_text = ", ".join(str(part) for part in file_version)
    display_version = _version_string_literal(version)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({file_version_text}),
    prodvers=({file_version_text}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0),
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [
          StringStruct('CompanyName', 'OpenBiliClaw Contributors'),
          StringStruct('FileDescription', 'OpenBiliClaw'),
          StringStruct('FileVersion', '{display_version}'),
          StringStruct('InternalName', 'OpenBiliClaw'),
          StringStruct('OriginalFilename', 'OpenBiliClaw.exe'),
          StringStruct('ProductName', 'OpenBiliClaw'),
          StringStruct('ProductVersion', '{display_version}'),
        ],
      ),
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])]),
  ],
)
""",
        encoding="utf-8",
    )
    return path


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


def make_macos_dmg(*, app_bundle: Path, output_dir: Path, version: str) -> Path:
    """Build a drag-to-Applications ``.dmg`` from the ``.app`` bundle (macOS only).

    Uses ``ditto`` (bundle-faithful copy that preserves the in-bundle symlinks)
    into a staging dir with an ``/Applications`` shortcut, then ``hdiutil`` to a
    compressed UDZO image — the conventional macOS drag-install experience.
    """
    import tempfile
    import time

    output_dir.mkdir(parents=True, exist_ok=True)
    dmg_name = make_archive_name(version, "macos").removesuffix(".zip") + ".dmg"
    dmg_path = output_dir / dmg_name
    if dmg_path.exists():
        dmg_path.unlink()

    stage = Path(tempfile.mkdtemp(prefix="obc-dmg-"))
    try:
        subprocess.check_call(["ditto", str(app_bundle), str(stage / app_bundle.name)])
        (stage / "Applications").symlink_to("/Applications")
        hdiutil_cmd = [
            "hdiutil",
            "create",
            "-volname",
            "OpenBiliClaw",
            "-srcfolder",
            str(stage),
            "-ov",
            "-format",
            "UDZO",
            str(dmg_path),
        ]
        # hdiutil is flaky on CI runners — it intermittently fails with "Resource
        # busy" / diskimages-helper races (seen on the GitHub macOS runners).
        # Retry a few times, capturing stderr so a real (non-transient) failure
        # surfaces its message instead of a bare exit code.
        last_err = ""
        for attempt in range(1, 4):
            result = subprocess.run(
                hdiutil_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
            )
            if result.returncode == 0:
                last_err = ""
                break
            last_err = (result.stderr or "").strip()
            print(
                f"[build] hdiutil create failed "
                f"(attempt {attempt}/3, rc={result.returncode}): {last_err}"
            )
            dmg_path.unlink(missing_ok=True)
            time.sleep(3 * attempt)
        if last_err:
            raise subprocess.CalledProcessError(1, hdiutil_cmd, stderr=last_err)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return dmg_path


def find_ollama_binary(explicit: str | None = None) -> Path | None:
    """Locate an ollama executable to bundle (explicit > env > PATH)."""
    candidates = [
        explicit,
        os.environ.get("OPENBILICLAW_OLLAMA_BIN"),
        shutil.which("ollama"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).resolve()
        if path.is_file():
            return path
    return None


def _macos_ollama_runtime_files(ollama_bin: Path) -> list[Path]:
    """Return macOS Ollama runtime sidecars required beside ``ollama``."""
    resources = ollama_bin.parent
    required = [
        resources / "llama-server",
        resources / "libllama-server-impl.dylib",
    ]
    missing = [path.name for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(
            "macOS Ollama runtime is incomplete: missing "
            f"{', '.join(missing)} beside {ollama_bin}. Use the official "
            "Ollama.app Resources/ollama, not a Homebrew-only ollama binary."
        )

    runtime_files: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path) -> None:
        if path == ollama_bin or not path.is_file() or path in seen:
            return
        seen.add(path)
        runtime_files.append(path)

    for path in required:
        add(path)
    for pattern in ("lib*.dylib", "lib*.so", "llama-*"):
        for path in sorted(resources.glob(pattern)):
            add(path)
    return runtime_files


def _macos_ollama_runtime_dirs(ollama_bin: Path) -> list[Path]:
    """Return macOS Ollama runtime data directories to keep beside ``ollama``."""
    return sorted(path for path in ollama_bin.parent.glob("mlx_metal_*") if path.is_dir())


def bundle_ollama_binary(
    dist_dir: Path,
    ollama_bin: Path,
    platform_name: str | None = None,
) -> list[Path]:
    """Copy the Ollama runtime into the packaged outputs.

    Ships a self-contained local-embedding runtime so the app does not depend on
    a user-installed ollama (the fragile brew/winget step). Placed where
    ``entry.py`` resolves ``bundled_resources``: next to the exe for the onedir
    layout, and ``Contents/Resources`` for the macOS ``.app`` bundle.
    """
    resolved = platform_name or platform.system()
    exe_name = "ollama.exe" if resolved == "Windows" else "ollama"
    targets: list[Path] = []

    onedir = dist_dir / "OpenBiliClaw"
    if onedir.is_dir():
        targets.append(onedir / exe_name)
    if resolved == "Darwin":
        app_resources = dist_dir / "OpenBiliClaw.app" / "Contents" / "Resources"
        if app_resources.is_dir():
            targets.append(app_resources / exe_name)

    # Windows ollama is not a single self-contained binary like macOS — it ships
    # ``ollama.exe`` plus a sibling ``lib/`` of inference runners. Carry that dir
    # along (CPU runner is enough for bge-m3 embedding) so the bundled exe works.
    #
    # macOS 0.30.x is no longer reliably single-binary either: Homebrew's formula
    # can expose an ``ollama`` executable that starts /api/version but fails every
    # GGUF embedding because the model runner is missing. The official .app ships
    # ``llama-server`` plus dynamic libraries and Metal assets beside
    # ``Contents/Resources/ollama``; require and bundle that runtime as a unit so
    # the release never contains a half-working daemon.
    sidecar_files: list[Path] = []
    sidecar_dirs: list[Path] = []
    if resolved == "Darwin":
        sidecar_files = _macos_ollama_runtime_files(ollama_bin)
        sidecar_dirs = _macos_ollama_runtime_dirs(ollama_bin)

    sibling_lib = ollama_bin.parent / "lib"
    written: list[Path] = []
    for dest in targets:
        shutil.copy2(ollama_bin, dest)
        os.chmod(dest, 0o755)
        written.append(dest)
        for sidecar in sidecar_files:
            sidecar_dest = dest.parent / sidecar.name
            shutil.copy2(sidecar, sidecar_dest)
            if sidecar.name.startswith("llama-"):
                os.chmod(sidecar_dest, 0o755)
            written.append(sidecar_dest)
        for sidecar_dir in sidecar_dirs:
            sidecar_dir_dest = dest.parent / sidecar_dir.name
            if sidecar_dir_dest.exists() or sidecar_dir_dest.is_symlink():
                if sidecar_dir_dest.is_dir() and not sidecar_dir_dest.is_symlink():
                    shutil.rmtree(sidecar_dir_dest)
                else:
                    sidecar_dir_dest.unlink()
            shutil.copytree(sidecar_dir, sidecar_dir_dest)
            written.append(sidecar_dir_dest)
        if sibling_lib.is_dir():
            dest_lib = dest.parent / "lib"
            if not dest_lib.exists():
                shutil.copytree(sibling_lib, dest_lib)
    return written


def repair_macos_ad_hoc_signature(app_bundle: Path) -> None:
    """Re-seal a macOS app bundle with an ad-hoc signature after local mutations.

    PyInstaller may leave a macOS bundle ad-hoc signed. The build script then adds
    resources such as bundled Ollama, which invalidates the sealed resources and
    makes Gatekeeper report the app as damaged. Without a Developer ID account we
    still cannot notarize, but a final ad-hoc signature keeps the bundle internally
    consistent for users who explicitly bypass Gatekeeper.
    """
    if not app_bundle.exists():
        raise FileNotFoundError(f"macOS app bundle not found: {app_bundle}")

    codesign = shutil.which("codesign")
    if codesign is None:
        raise RuntimeError("codesign is required to finalize macOS app bundles")

    print("[build] Re-sealing macOS .app with an ad-hoc signature ...")
    subprocess.check_call([codesign, "--force", "--deep", "--sign", "-", str(app_bundle)])
    subprocess.check_call(
        [codesign, "--verify", "--deep", "--strict", "--verbose=2", str(app_bundle)]
    )


def build(
    *,
    archive_version: str | None = None,
    bundle_ollama: bool = True,
    ollama_bin: str | None = None,
    bundle_x: bool = True,
) -> None:
    """Run PyInstaller."""
    ensure_pyinstaller()
    bundle_version = make_bundle_version(archive_version or read_project_version())

    # X (Twitter) discovery is bundled by default (spec §8 = always-bundle).
    # Install the X dependency alias so PyInstaller can statically see twitter_cli,
    # then tell the spec (via OPENBILICLAW_BUNDLE_X) to collect twitter_cli +
    # curl_cffi — including curl_cffi's per-OS·arch native binaries (libcurl /
    # the _wrapper extension) which the lazy `import twitter_cli` path would
    # otherwise hide from the analyzer.
    bundle_x_resolved = bundle_x and ensure_x_extra()

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        str(SPEC_FILE),
        "--distpath",
        str(DIST_DIR),
        "--workpath",
        str(PROJECT_ROOT / "build"),
        "--noconfirm",
    ]
    print(f"[build] Running: {' '.join(cmd)}")
    env = os.environ.copy()
    env["OPENBILICLAW_BUNDLE_VERSION"] = bundle_version
    env["OPENBILICLAW_BUNDLE_X"] = "1" if bundle_x_resolved else "0"
    if platform.system() == "Windows":
        version_file = write_windows_version_file(
            PROJECT_ROOT / "build" / "openbiliclaw_version_info.txt",
            version=bundle_version,
        )
        env["OPENBILICLAW_WINDOWS_VERSION_FILE"] = str(version_file)
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

        # Bundle the local-embedding runtime (ollama) so the app ships a
        # working bge-m3 path without a separate brew/winget install. Must
        # happen before archive creation so the binary lands in the zip.
        if bundle_ollama:
            resolved_ollama = find_ollama_binary(ollama_bin)
            if resolved_ollama is not None:
                written = bundle_ollama_binary(DIST_DIR, resolved_ollama)
                size_mb = resolved_ollama.stat().st_size // (1024 * 1024)
                print(
                    f"[build] Bundled ollama ({resolved_ollama}, ~{size_mb}MB) "
                    f"into {len(written)} target(s)"
                )
            else:
                print(
                    "[build] WARNING: no ollama binary found (set OPENBILICLAW_OLLAMA_BIN "
                    "or --ollama-bin); packaged app will fall back to a user-installed ollama"
                )

        if platform.system() == "Darwin":
            app_bundle = DIST_DIR / "OpenBiliClaw.app"
            if app_bundle.exists():
                repair_macos_ad_hoc_signature(app_bundle)

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
            if platform.system() == "Darwin":
                app_bundle = DIST_DIR / "OpenBiliClaw.app"
                if app_bundle.exists() and shutil.which("hdiutil"):
                    dmg_path = make_macos_dmg(
                        app_bundle=app_bundle,
                        output_dir=RELEASE_DIR,
                        version=archive_version,
                    )
                    print(f"  Release installer: {dmg_path}")
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
    parser.add_argument(
        "--no-bundle-ollama",
        action="store_true",
        help="Do not bundle the ollama binary (smaller build; needs user-installed ollama)",
    )
    parser.add_argument(
        "--ollama-bin",
        help="Path to the ollama executable to bundle (default: $OPENBILICLAW_OLLAMA_BIN or PATH)",
    )
    parser.add_argument(
        "--no-bundle-x",
        action="store_true",
        help="Do not bundle the X (Twitter) discovery dependency (twitter-cli + curl_cffi)",
    )
    args = parser.parse_args()

    if args.clean:
        clean()
    build(
        archive_version=args.archive_version,
        bundle_ollama=not args.no_bundle_ollama,
        ollama_bin=args.ollama_bin,
        bundle_x=not args.no_bundle_x,
    )


if __name__ == "__main__":
    main()
