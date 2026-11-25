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
        with pytest.raises(bundle.BundleError, match="legacy"):
            gate.check_archive(artifact, entry, catalog, platform, require_reusable=True)
    else:
        expected = (
            r"旧组件签名仅适用 Core 0\.0\.1" if scenario == "previous-core" else "digest mismatch"
        )
        with pytest.raises(bundle.BundleError, match=expected):
            gate.check_archive(artifact, entry, catalog, platform)


def test_release_gate_rejects_partial_catalog() -> None:
    with pytest.raises(bundle.BundleError, match="platform"):
        gate.verify_catalog({"releases": {}}, None)


@pytest.mark.parametrize(
    "change",
    [
        "version-only",
        "retired-patch-claim",
        "bounded-policy",
        "wrong-abi",
        "limits",
        "expired-grant",
        "retired-pin",
        "private-inputs",
        "core-ipc",
        "audit-lock",
        "mixed-claims",
        "tampered-binary",
    ],
)
@pytest.mark.parametrize("platform", gate.PLATFORMS)
def test_reuse_of_exact_archive_across_app_releases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str, platform: str
) -> None:
    folder = tmp_path / "issued"
    catalog, signer = signed_bundle(folder, platform=platform)
    lock = json.loads(bundle.CATALOG.with_name("sponsored_sdk_lock.json").read_bytes())
    policy_path = folder / "sponsored-policy.json"
    policy = json.loads(policy_path.read_bytes())
    policy["keys"] = [
        {
            "not_before": "2020-01-01T00:00:00Z",
            "not_after": "2020-02-01T00:00:00Z"
            if change == "expired-grant"
            else "2099-01-01T00:00:00Z",
        }
    ]
    if change == "limits":
        next(iter(policy["tasks"].values()))["limits"] = {"daily_requests": 1}
    if change == "bounded-policy":
        policy["core"]["max"] = "0.3.241"
    policy_path.write_text(json.dumps(policy))
    (folder / "sponsored-policy.sig").write_bytes(signer.sign(policy_path.read_bytes()))
    path = folder / "manifest.json"
    manifest = json.loads(path.read_bytes())
    if platform.startswith("macos-"):
        manifest.update(platform_signing="macos-notarized", notarization_id="fixture")
    elif platform.startswith("windows-"):
        binary = "obc-sponsored-runtime"
        (folder / binary).rename(folder / (binary + ".exe"))
        manifest["files"][binary + ".exe"] = manifest["files"].pop(binary)
    del manifest["core_min"], manifest["core_max"]
    manifest["core_compatibility"] = {
        **lock,
        "format": 2,
        "client_sha256": gate.client_digest(),
    }
    if change == "private-inputs":
        manifest["core_compatibility"]["rust_inputs_sha256"] = "0" * 64
    if change == "wrong-abi":
        manifest["core_compatibility"]["abi"] += 1
    if change == "retired-patch-claim":
        manifest["core_compatibility"].update(format=1, since="0.3.241")
    if change == "core-ipc":
        manifest["core_compatibility"]["client_sha256"] = "0" * 64
    if change == "audit-lock":
        altered = tmp_path / "wrong-dependencies.lock"
        altered.write_bytes(b"not the signed dependency inventory")
        monkeypatch.setattr(gate, "AUDIT_LOCK", altered)
    if change == "mixed-claims":
        manifest.update(core_min="0.3.241", core_max="0.3.242")
    for name in ["sponsored-policy.json", "sponsored-policy.sig"]:
        manifest["files"][name] = hashlib.sha256((folder / name).read_bytes()).hexdigest()
    path.write_text(json.dumps(manifest))
    (folder / "manifest.sig").write_bytes(signer.sign(path.read_bytes()))
    if change == "tampered-binary":
        next(folder.glob("obc-sponsored-runtime*")).write_bytes(b"modified after signing")
    artifact = archive(folder, tmp_path / "original-release.tar.gz")
    archive_digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    entry = {
        "sha256": archive_digest,
        "manifest_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    catalog["releases"][platform] = {**entry}
    if change == "retired-pin":
        catalog["releases"][platform]["manifest_sha256"] = "0" * 64
    if change == "version-only":
        for version in ("0.3.241", "0.3.242", "0.4.0", "1.0.0", "0.3.240", "2.0.0rc1"):
            monkeypatch.setattr(bundle, "__version__", version)
            gate.check_archive(artifact, entry, catalog, platform, require_reusable=True)
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == archive_digest
    else:
        monkeypatch.setattr(bundle, "__version__", "1.0.0")
        with pytest.raises(bundle.BundleError):
            gate.check_archive(artifact, entry, catalog, platform)
        if change not in {"core-ipc", "audit-lock"}:
            install_root = tmp_path / "install"
            monkeypatch.setattr(bundle, "platform_id", lambda: platform)
            monkeypatch.setattr(bundle, "_root", lambda: install_root)
            reason = {
                "expired-grant": "没有生效的赞助凭据",
                "limits": "任务或配额已变化",
                "bounded-policy": "策略限定了应用版本",
                "tampered-binary": "组件校验失败",
            }.get(change, "SDK 契约或目录指纹不匹配")
            # Require the verifier's rejection, not an accidental execution/OS failure.
            with pytest.raises(bundle.BundleError, match=reason):
                bundle.install_archive(artifact, catalog=catalog)
            assert not (install_root / "active").exists()


@pytest.mark.parametrize("delivery", ["release", "commit"])
@pytest.mark.parametrize("stale_windows", [False, True])
def test_offline_release_candidate_requires_every_signed_platform(
    tmp_path: Path, stale_windows: bool, delivery: str
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
        prefix = (
            "https://github.com/whiteguo233/OpenBiliClaw/releases/download/sponsored-runtime-fixture/"
            if delivery == "release"
            else "https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/" + "a" * 40 + "/"
        )
        catalog["releases"][platform] = {
            "url": prefix + name,
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
