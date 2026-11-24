"""Release readiness uses the versioned consumer and signed archives, not tag names."""

import hashlib
import json
from pathlib import Path

import pytest
from scripts import verify_release_sdk as gate

from openbiliclaw.runtime import sponsored_bundle as bundle
from tests.test_sponsored_bundle import archive, signed_bundle


@pytest.mark.parametrize("scenario", ["compatible", "previous-core", "manifest-pin", "archive-pin"])
def test_signed_archive_matches_releasing_core(tmp_path: Path, scenario: str) -> None:
    directory = tmp_path / "bundle"
    platform = "linux-arm64"
    catalog, key = signed_bundle(directory, platform=platform)
    path = directory / "manifest.json"
    if scenario == "previous-core":
        manifest = json.loads(path.read_bytes())
        manifest["core_min"] = manifest["core_max"] = "0.0.1"
        path.write_text(json.dumps(manifest))
        (directory / "manifest.sig").write_bytes(key.sign(path.read_bytes()))
    artifact = archive(directory, tmp_path / "package.tar.gz")
    entry = {
        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "manifest_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    if scenario.endswith("-pin"):
        entry["sha256" if scenario == "archive-pin" else "manifest_sha256"] = "0" * 64
    if scenario == "compatible":
        gate.check_archive(artifact, entry, catalog, platform)
    else:
        with pytest.raises(bundle.BundleError):
            gate.check_archive(artifact, entry, catalog, platform)


def test_release_gate_rejects_partial_catalog() -> None:
    with pytest.raises(bundle.BundleError, match="platform"):
        gate.verify_catalog({"releases": {}}, None)


@pytest.mark.parametrize("stale_windows", [False, True])
def test_offline_release_candidate_requires_every_signed_platform(
    tmp_path: Path, stale_windows: bool
) -> None:
    catalog, key = signed_bundle(tmp_path / "issuer")
    for platform in gate.PLATFORMS:
        folder = tmp_path / platform
        signed_bundle(folder, platform=platform, key=key)
        manifest = folder / "manifest.json"
        body = json.loads(manifest.read_bytes())
        if platform.startswith("macos-"):
            body.update(platform_signing="macos-notarized", notarization_id="fixture")
        if platform.startswith("windows-"):
            binary = "obc-sponsored-runtime"
            (folder / binary).rename(folder / (binary + ".exe"))
            body["files"][binary + ".exe"] = body["files"].pop(binary)
            if stale_windows:
                body["core_min"] = body["core_max"] = "0.0.1"
        manifest.write_text(json.dumps(body))
        (folder / "manifest.sig").write_bytes(key.sign(manifest.read_bytes()))
        name = f"obc-sponsored-runtime-{platform}.tar.gz"
        artifact = archive(folder, tmp_path / name)
        catalog["releases"][platform] = {
            "url": "https://github.com/whiteguo233/OpenBiliClaw/releases/download/sponsored-runtime-fixture/"
            + name,
            "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        }
    if stale_windows:
        with pytest.raises(bundle.BundleError, match="windows-x86_64"):
            gate.verify_catalog(catalog, tmp_path)
    else:
        gate.verify_catalog(catalog, tmp_path)


@pytest.mark.parametrize(
    ("channel", "before"),
    [
        ("backend", "Update aggregate latest release"),
        ("docker", "Resolve version and tags"),
        ("desktop", "Download built installers"),
        ("extension", "Update aggregate latest release"),
    ],
)
def test_release_channels_verify_before_promotion(channel: str, before: str) -> None:
    root = Path(__file__).resolve().parents[1]
    workflow = (root / f".github/workflows/release-{channel}.yml").read_text()
    gate_step = "uses: ./.github/actions/verify-free-runtime"
    assert workflow.index(gate_step) < workflow.index(f"- name: {before}")
    assert ("extension-tag:" in workflow[workflow.index(gate_step) :]) == (channel == "extension")
