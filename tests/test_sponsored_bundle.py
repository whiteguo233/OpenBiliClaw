"""The installer establishes trust before it ever executes a downloaded file."""

from __future__ import annotations

import hashlib
import io
import json
import shlex
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from openbiliclaw import __version__
from openbiliclaw.llm.sponsored_contracts import build_unsigned_policy
from openbiliclaw.llm.sponsored_profiles import approved_registry
from openbiliclaw.runtime import sponsored_bundle as bundle


def signed_bundle(
    directory: Path, *, platform: str | None = None
) -> tuple[dict[str, Any], Ed25519PrivateKey]:
    directory.mkdir()
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    policy = build_unsigned_policy(
        approved_registry(),
        policy_version=1,
        generated_at="2026-01-01T00:00:00Z",
        expires_at="2099-01-01T00:00:00Z",
    )
    data = json.dumps(policy).encode()
    files = {
        "sponsored-policy.json": data,
        "sponsored-policy.sig": key.sign(data),
        "release-public-key.bin": public,
        "obc-sponsored-runtime": b"synthetic signed executable",
    }
    for name, content in files.items():
        (directory / name).write_bytes(content)
    manifest = {
        "protocol": 2,
        "profile": bundle.PROFILE,
        "platform": platform or bundle.platform_id(),
        "core_min": __version__,
        "core_max": __version__,
        "policy_version": 1,
        "files": {name: hashlib.sha256(content).hexdigest() for name, content in files.items()},
    }
    data = json.dumps(manifest).encode()
    (directory / "manifest.json").write_bytes(data)
    (directory / "manifest.sig").write_bytes(key.sign(data))
    return {"schema_version": 1, "public_key_hex": public.hex(), "releases": {}}, key


def archive(directory: Path, output: Path) -> Path:
    with tarfile.open(output, "w:gz") as tar:
        for path in directory.iterdir():
            tar.add(path, arcname=path.name)
    return output


def test_valid_bundle_installs_atomically_and_uses_core_trust_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "source"
    catalog, _ = signed_bundle(directory)
    monkeypatch.setattr(bundle, "_root", lambda: tmp_path / "installed")
    monkeypatch.setattr(bundle, "_catalog", lambda: catalog)
    calls = []

    def run(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        bundle.verify_bundle(Path(args[0]).parent)
        calls.append(args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(bundle.subprocess, "run", run)
    destination = bundle.install_archive(archive(directory, tmp_path / "release.tar.gz"))
    assert (destination / "manifest.json").exists()
    command = bundle.installed_runtime_command()
    assert "--public-key" not in command and str(destination) in command
    assert calls[0][-1] == "--check-policy"
    # Failed upgrades preserve the last verified active pointer.
    before = (tmp_path / "installed/active").read_text()
    (directory / "obc-sponsored-runtime").write_bytes(b"altered")
    with pytest.raises(bundle.BundleError):
        bundle.install_archive(archive(directory, tmp_path / "bad.tar.gz"))
    assert len(calls) == 1
    assert (tmp_path / "installed/active").read_text() == before
    assert bundle.installed_runtime_command() == command


@pytest.mark.parametrize("attack", ["binary", "manifest", "key", "platform", "extra", "symlink"])
def test_bundle_rejects_tampering_or_incompatible_assets(tmp_path: Path, attack: str) -> None:
    directory = tmp_path / "bundle"
    catalog, _key = signed_bundle(
        directory, platform="unknown-platform" if attack == "platform" else None
    )
    if attack == "binary":
        (directory / "obc-sponsored-runtime").write_bytes(b"changed")
    if attack == "manifest":
        (directory / "manifest.json").write_bytes(b"{}")
    if attack == "key":
        catalog["public_key_hex"] = "ff" * 32
    if attack == "extra":
        (directory / "extra.exe").write_bytes(b"extra")
    if attack == "symlink":
        (directory / "obc-sponsored-runtime").unlink()
        (directory / "obc-sponsored-runtime").symlink_to(tmp_path / "outside")
    with pytest.raises(bundle.BundleError):
        bundle.verify_bundle(directory, catalog=catalog)


def test_store_uses_only_packaged_sponsored_runtime(tmp_path: Path, monkeypatch) -> None:
    contents = tmp_path / "OpenBiliClaw.app/Contents"
    directory = contents / "Resources/sponsored"
    directory.parent.mkdir(parents=True)
    catalog, _ = signed_bundle(directory, platform="macos-arm64")
    binary = contents / "MacOS/obc-sponsored-runtime"
    binary.parent.mkdir()
    (directory / binary.name).rename(binary)
    # Store signing changes the original SDK's signature bytes. The native host
    # validates that code separately, while the policy must still verify here.
    binary.write_bytes(b"App Store signed executable")
    monkeypatch.setattr(bundle, "_catalog", lambda: catalog)
    monkeypatch.setattr(bundle, "platform_id", lambda: "macos-arm64")
    monkeypatch.setattr(bundle, "is_mac_app_store", lambda: True)
    monkeypatch.setattr(bundle.sys, "executable", str(binary.with_name("OpenBiliClawBackend")))
    monkeypatch.setenv("OPENBILICLAW_SPONSORED_BUNDLE", str(tmp_path / "untrusted"))
    monkeypatch.setattr(bundle, "_root", lambda: tmp_path / "container")

    assert shlex.split(bundle.ensure_runtime())[0] == str(binary.resolve())
    (directory / "sponsored-policy.json").write_text("{}")
    with pytest.raises(bundle.BundleUnavailableError, match="App Store"):
        bundle.ensure_runtime()
    assert not (tmp_path / "container").exists()


@pytest.mark.parametrize(
    "path,typecode",
    [
        ("../outside", tarfile.REGTYPE),
        ("/absolute", tarfile.REGTYPE),
        ("manifest.json", tarfile.SYMTYPE),
    ],
)
def test_unsafe_archive_members_never_reach_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str, typecode: bytes
) -> None:
    monkeypatch.setattr(bundle, "_root", lambda: tmp_path / "install")
    monkeypatch.setattr(
        bundle.subprocess, "run", lambda *_a, **_kw: pytest.fail("unverified execution")
    )
    target = tmp_path / "attack.tar.gz"
    with tarfile.open(target, "w:gz") as tar:
        item = tarfile.TarInfo(path)
        item.type = typecode
        item.linkname = "/tmp/outside"
        item.size = 1 if typecode == tarfile.REGTYPE else 0
        tar.addfile(item, io.BytesIO(b"x"))
    with pytest.raises(bundle.BundleError):
        bundle.install_archive(target)
    assert not (tmp_path / "install/active").exists()


def test_optional_install_only_allows_absent_release(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["bundle", "--if-available"])

    def absent() -> str:
        raise bundle.BundleUnavailableError("not published")

    monkeypatch.setattr(bundle, "ensure_runtime", absent)
    bundle.main()

    def invalid() -> str:
        raise bundle.BundleError("signature mismatch")

    monkeypatch.setattr(bundle, "ensure_runtime", invalid)
    with pytest.raises(SystemExit, match="1"):
        bundle.main()


def test_package_staging_requires_a_pinned_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bundle, "_catalog", lambda: {"releases": {}})
    monkeypatch.setattr(bundle, "installed_runtime_command", lambda: "")
    with pytest.raises(bundle.BundleUnavailableError):
        bundle.stage_runtime_bundle(tmp_path / "package")
    assert not (tmp_path / "package").exists()


def test_package_cannot_substitute_an_unpinned_local_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "local"
    catalog, _ = signed_bundle(directory)
    catalog["releases"] = {bundle.platform_id(): {"manifest_sha256": "0" * 64}}
    monkeypatch.setattr(bundle, "_catalog", lambda: catalog)
    monkeypatch.setattr(bundle, "ensure_runtime", lambda: str(directory / "obc-sponsored-runtime"))
    with pytest.raises(bundle.BundleError, match="pinned"):
        bundle.stage_runtime_bundle(tmp_path / "package")
    assert not (tmp_path / "package").exists()


def test_packaged_pinned_update_wins_over_older_active_user_bundle_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replacing the app must replace its credential generation without a download."""
    directory = tmp_path / "packaged"
    catalog, key = signed_bundle(directory)
    old = tmp_path / "state/bundles" / ("a" * 64)
    shutil.copytree(directory, old)
    manifest = json.loads((old / "manifest.json").read_bytes())
    manifest["build"] = "previous-release"
    raw = json.dumps(manifest).encode()
    (old / "manifest.json").write_bytes(raw)
    (old / "manifest.sig").write_bytes(key.sign(raw))
    (tmp_path / "state/active").write_text("a" * 64)
    catalog["releases"] = {
        bundle.platform_id(): {
            "manifest_sha256": hashlib.sha256(
                (directory / "manifest.json").read_bytes()
            ).hexdigest(),
            "url": "https://invalid.example/must-not-download",
        }
    }
    monkeypatch.setattr(bundle, "_catalog", lambda: catalog)
    monkeypatch.setattr(bundle, "_root", lambda: tmp_path / "state")
    monkeypatch.setenv("OPENBILICLAW_SPONSORED_BUNDLE", str(directory))
    assert str(directory) in bundle.installed_runtime_command()
    assert str(directory) in bundle.ensure_runtime()


def test_matching_installed_release_does_not_download_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "bundle"
    catalog, _ = signed_bundle(directory)
    catalog["releases"] = {
        bundle.platform_id(): {
            "manifest_sha256": hashlib.sha256(
                (directory / "manifest.json").read_bytes()
            ).hexdigest(),
            "url": "https://invalid.example/not-requested",
        }
    }
    monkeypatch.setattr(bundle, "_catalog", lambda: catalog)
    monkeypatch.setattr(bundle, "_root", lambda: tmp_path / "state")
    monkeypatch.setenv("OPENBILICLAW_SPONSORED_BUNDLE", str(directory))
    assert bundle.ensure_runtime() == bundle.installed_runtime_command()


@pytest.mark.parametrize("mode", ["system", "direct", "custom"])
def test_standalone_download_honors_saved_network_policy_before_app_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    import httpx

    from openbiliclaw.config import Config

    cfg = Config()
    cfg.network.mode = mode
    cfg.network.proxy = "http://127.0.0.1:17897" if mode == "custom" else ""
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: cfg)
    monkeypatch.setattr(bundle, "installed_runtime_command", lambda: "")
    monkeypatch.setattr(bundle, "_root", lambda: tmp_path / "state")
    monkeypatch.setattr(
        bundle,
        "_catalog",
        lambda: {
            "releases": {
                bundle.platform_id(): {
                    "sha256": "0" * 64,
                    "url": "https://example.invalid/runtime.tar.gz",
                }
            },
        },
    )
    captured: dict[str, Any] = {}

    class ClientReachedError(Exception):
        pass

    def client(**kwargs: Any) -> Any:
        captured.update(kwargs)
        raise ClientReachedError

    monkeypatch.setattr(httpx, "Client", client)
    with pytest.raises(ClientReachedError):
        bundle.ensure_runtime()
    assert captured["trust_env"] is (mode == "system")
    assert captured.get("proxy") == (cfg.network.proxy if mode == "custom" else None)
