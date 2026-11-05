"""Build the separate sandboxed Mac App Store application with Xcode.

All Python/native dependencies are frozen at build time. Store archives never
reuse the Developer ID DMG's updater, user profile, or LaunchAgent registration.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
OUTPUT = ROOT / "dist" / "mac-app-store"
BUNDLE_ID = "com.openbiliclaw.backend"
APP_NAME = "OpenBiliClaw Backend"


def preserve_dependency_licenses(backend: Path) -> None:
    """Keep identified Python copyleft license texts and provenance with QA builds.

    This records the actual build environment; it is not a complete dependency
    license review, a source offer, or an assertion of store compatibility.
    """
    destination = backend / "Contents/Resources/ThirdPartyLicenses"
    records = []
    for name in ("bilibili-api-python", "browser-cookie3"):
        package = importlib.metadata.distribution(name)
        texts = [
            path
            for path in package.files or []
            if ".dist-info/" in str(path)
            and path.name.upper() in ("LICENSE", "LICENSE.TXT", "LICENSE.MD", "COPYING")
        ]
        if not texts:
            raise ValueError(f"Missing installed license text for {name}")
        folder = destination / name
        folder.mkdir(parents=True, exist_ok=True)
        for index, path in enumerate(texts):
            shutil.copyfile(package.locate_file(path), folder / f"LICENSE-{index + 1}.txt")
        records.append(
            {
                "name": name,
                "version": package.version,
                "declared_license": package.metadata.get("License"),
                "project_urls": package.metadata.get_all("Project-URL") or [],
                "homepage": package.metadata.get("Home-page"),
            }
        )
    (destination / "manifest.json").write_text(
        json.dumps(
            {
                "scope": "identified Python copyleft dependencies; review pending",
                "packages": records,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def desktop_builder():
    spec = importlib.util.spec_from_file_location("desktop_builder", ROOT / "packaging/build.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare_backend(ollama_bin: Path) -> Path:
    """Freeze the full shared backend and require both bundled native services."""
    desktop = desktop_builder()
    # Fail before the expensive freeze rather than ship an incomplete runtime.
    desktop._macos_ollama_runtime_files(ollama_bin)
    helper = desktop.build_tailnet_helper(output_dir=OUTPUT / "tailnet")
    env = {
        **os.environ,
        "OPENBILICLAW_BUNDLE_VERSION": desktop.read_project_version(),
        "OPENBILICLAW_BUNDLE_X": "1",
        "OPENBILICLAW_BUNDLE_REDDIT": "1",
        "OPENBILICLAW_TAILNET_HELPER_BINARY": str(helper),
    }
    frozen = OUTPUT / "frozen"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            str(ROOT / "packaging/openbiliclaw.spec"),
            "--distpath",
            str(frozen),
            "--workpath",
            str(ROOT / "build/mac-app-store"),
            "--noconfirm",
        ],
        cwd=ROOT,
        env=env,
        check=True,
    )
    desktop.apply_macos_bundle_fixes(frozen)
    desktop.bundle_ollama_binary(frozen, ollama_bin)
    app = frozen / "OpenBiliClaw.app"
    resources = app / "Contents/Resources"
    (resources / "mac-app-store.json").write_text(
        json.dumps({"channel": "mac_app_store", "bundle_id": BUNDLE_ID}) + "\n", encoding="utf-8"
    )
    return app


def archive_command(
    backend: Path,
    archive: Path,
    version: str,
    build_number: str,
    identity: str,
    team: str,
    local: bool,
    provisioning_profile: str = "",
) -> tuple[list[str], dict[str, str]]:
    """Construct a standard Xcode archive; local signing is explicitly opt-in."""
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Store version must have three numeric components")
    if not re.fullmatch(r"[1-9]\d*", build_number):
        raise ValueError("Build number must be a positive integer")
    if not (backend / "Contents/Resources/mac-app-store.json").is_file():
        raise ValueError("Backend payload is not a prepared Mac App Store build")
    if not local and not identity.startswith(
        ("Apple Distribution:", "3rd Party Mac Developer Application:")
    ):
        raise ValueError(
            "Store submission requires an Apple Distribution identity; use --local for QA"
        )
    cmd = [
        "xcodebuild",
        "-project",
        str(HERE / "OpenBiliClawStore.xcodeproj"),
        "-scheme",
        "OpenBiliClawStore",
        "-configuration",
        "Release",
        "-destination",
        "generic/platform=macOS",
        "-archivePath",
        str(archive),
        "archive",
        f"ARCHS={platform.machine()}",
        "ONLY_ACTIVE_ARCH=YES",
        f"MARKETING_VERSION={version}",
        f"CURRENT_PROJECT_VERSION={build_number}",
        f"DEVELOPMENT_TEAM={team}",
        "CODE_SIGN_STYLE=Manual",
        f"CODE_SIGN_IDENTITY={identity}",
        "CODE_SIGN_INJECT_BASE_ENTITLEMENTS=NO",
        f"OPENBILICLAW_STORE_PAYLOAD={backend}",
        f"OPENBILICLAW_BUILD_PYTHON={sys.executable}",
    ]
    if provisioning_profile:
        cmd.append(f"PROVISIONING_PROFILE_SPECIFIER={provisioning_profile}")
    return cmd, {
        **os.environ,
        "OPENBILICLAW_STORE_PAYLOAD": str(backend),
        "OPENBILICLAW_BUILD_PYTHON": sys.executable,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ollama-bin", type=Path)
    parser.add_argument(
        "--reuse-backend", action="store_true", help="Reuse this worktree's frozen store payload"
    )
    parser.add_argument("--version", default="1.0.0")
    parser.add_argument("--build-number", default="1")
    parser.add_argument("--signing-identity", required=True)
    parser.add_argument("--team", required=True)
    parser.add_argument(
        "--provisioning-profile", default="", help="Installed store profile name or UUID"
    )
    parser.add_argument("--archive-path", type=Path, default=OUTPUT / "OpenBiliClawStore.xcarchive")
    parser.add_argument(
        "--local",
        action="store_true",
        help="Allow a development/Developer ID QA archive; not uploadable",
    )
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("Mac App Store builds require macOS and Xcode")
    if args.reuse_backend:
        backend = OUTPUT / "frozen/OpenBiliClaw.app"
    else:
        if not args.ollama_bin:
            parser.error(
                "--ollama-bin must point to the official Ollama.app Contents/Resources/ollama"
            )
        backend = prepare_backend(args.ollama_bin.resolve())
    cmd, env = archive_command(
        backend,
        args.archive_path.resolve(),
        args.version,
        args.build_number,
        args.signing_identity,
        args.team,
        args.local,
        args.provisioning_profile,
    )
    preserve_dependency_licenses(backend)
    subprocess.run(cmd, cwd=ROOT, env=env, check=True)
    app = args.archive_path / "Products/Applications" / f"{APP_NAME}.app"
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    channel = "local QA (not for upload)" if args.local else "App Store candidate"
    print(f"Archive: {args.archive_path}\nChannel: {channel}")


if __name__ == "__main__":
    main()
