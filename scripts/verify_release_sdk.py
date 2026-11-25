"""Verify every pinned free-model SDK with the releasing Core; never execute a bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from openbiliclaw import __version__
from openbiliclaw.runtime import sponsored_bundle as sdk
from openbiliclaw.runtime.sponsored_client_digest import client_digest

PLATFORMS = ("linux-arm64", "linux-x86_64", "macos-arm64", "macos-x86_64", "windows-x86_64")
AUDIT_LOCK = Path(__file__).resolve().parents[1] / "packaging/sponsored-sdk/Cargo.lock"


def check_archive(
    archive: Path,
    entry: dict[str, str],
    catalog: dict[str, Any],
    platform: str,
    *,
    require_reusable: bool = False,
) -> None:
    """Validate both pins and run the production signature, version, policy and task checks."""
    if archive.stat().st_size > sdk.MAX_ARCHIVE_BYTES:
        raise sdk.BundleError("runtime archive too large")
    with archive.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != entry["sha256"]:
            raise sdk.BundleError("archive digest mismatch")
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp) / "bundle"
        sdk.unpack_bundle_archive(archive, directory)
        if (
            hashlib.sha256((directory / "manifest.json").read_bytes()).hexdigest()
            != entry["manifest_sha256"]
        ):
            raise sdk.BundleError("manifest digest mismatch")
        manifest = sdk.verify_bundle(directory, catalog=catalog, expected_platform=platform)
        if require_reusable and "core_compatibility" not in manifest:
            raise sdk.BundleError("legacy SDK has no signed build and dependency audit identity")
        # Check source on every release, including frozen-app releases. At installation
        # the exact gate-approved manifest pin binds the same binary to this Core.
        if (
            "core_compatibility" in manifest
            and manifest["core_compatibility"]["client_sha256"] != client_digest()
        ):
            raise sdk.BundleError("Core sponsored IPC sources changed; rebuild the SDK")
        if (
            "core_compatibility" in manifest
            and manifest["core_compatibility"]["cargo_lock_sha256"]
            != hashlib.sha256(AUDIT_LOCK.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        ):
            raise sdk.BundleError("Rust dependency audit lock differs from signed SDK")
        if platform.startswith("macos-") and (
            manifest.get("platform_signing") != "macos-notarized"
            or not manifest.get("notarization_id")
        ):
            raise sdk.BundleError("macOS notarization incomplete")


def verify_catalog(
    catalog: dict[str, Any], archives: Path | None, *, require_reusable: bool = False
) -> None:
    """Check the five exact public artifacts, or the corresponding local staging archives."""
    releases = catalog["releases"]
    if set(releases) != set(PLATFORMS):
        raise sdk.BundleError("expected the five supported SDK platforms")
    with tempfile.TemporaryDirectory() as tmp:
        for platform in PLATFORMS:
            name = f"obc-sponsored-runtime-{platform}.tar.gz"
            entry = releases[platform]
            url = entry["url"]
            if not re.fullmatch(
                r"https://github\.com/whiteguo233/OpenBiliClaw/releases/download/sponsored-runtime-[\w.-]+/"
                + re.escape(name),
                url,
            ):
                raise sdk.BundleError(f"{platform}: expected a pinned official release asset")
            path = (archives or Path(tmp)) / name
            if archives is None:
                subprocess.run(
                    [
                        "curl",
                        "--fail",
                        "--silent",
                        "--show-error",
                        "--location",
                        "--proto",
                        "=https",
                        "--proto-redir",
                        "=https",
                        "--max-time",
                        "90",
                        "--max-filesize",
                        str(sdk.MAX_ARCHIVE_BYTES),
                        "--output",
                        str(path),
                        url,
                    ],
                    check=True,
                )
            try:
                check_archive(path, entry, catalog, platform, require_reusable=require_reusable)
            except sdk.BundleError as exc:
                raise sdk.BundleError(f"Core {__version__}, {platform}: {exc}") from exc
            print(f"Verified Core {__version__} × {platform}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--archives", type=Path, help="offline directory containing all five pinned archives"
    )
    args = parser.parse_args()
    try:
        project = Path(__file__).resolve().parents[1] / "pyproject.toml"
        with project.open("rb") as metadata:
            if tomllib.load(metadata)["project"]["version"] != __version__:
                raise sdk.BundleError("release checkout and imported Core versions differ")
        verify_catalog(json.loads(sdk.CATALOG.read_text()), args.archives, require_reusable=True)
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(
            f"SDK release gate failed: {error}. Build, sign and pin the matching SDK before publishing."
        ) from None
