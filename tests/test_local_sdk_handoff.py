"""Exercise the public upload seam against a real, local bare Git repository."""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from scripts import handoff_local_sdk as upload

from tests.test_sponsored_bundle import archive, signed_bundle


def test_only_verified_archives_enter_the_immutable_input_branch(
    tmp_path: Path, monkeypatch
) -> None:
    core, rust = "c" * 40, "d" * 40
    sdk = upload.sdk
    lock = json.loads(sdk.CATALOG.with_name("sponsored_sdk_lock.json").read_bytes())
    trusted, key = signed_bundle(tmp_path / "issuer")
    pin = tmp_path / "trusted.json"
    pin.write_text(json.dumps(trusted))
    monkeypatch.setattr(sdk, "CATALOG", pin)
    issued = dict(trusted, releases={})
    files = tmp_path / "archives"
    files.mkdir()
    (files / "private-canary.txt").write_text("must never enter Git")
    for name in upload.gate.PLATFORMS:
        folder = tmp_path / name
        signed_bundle(folder, platform=name, key=key)
        mpath = folder / "manifest.json"
        m = json.loads(mpath.read_bytes())
        del m["core_min"], m["core_max"]
        m.update(
            core_sha=core,
            runtime_sha=rust,
            platform_signing="manifest-only",
            core_compatibility=dict(lock, format=2, client_sha256=upload.gate.client_digest()),
        )
        if name.startswith("macos"):
            m.update(platform_signing="macos-notarized", notarization_id="test-only")
        if name.startswith("windows"):
            exe = "obc-sponsored-runtime"
            (folder / exe).rename(folder / (exe + ".exe"))
            m["files"][exe + ".exe"] = m["files"].pop(exe)
        pp = folder / "sponsored-policy.json"
        policy = json.loads(pp.read_bytes())
        policy["keys"] = [
            {"not_before": "2020-01-01T00:00:00Z", "not_after": "2099-01-01T00:00:00Z"}
        ]
        pp.write_text(json.dumps(policy))
        (folder / "sponsored-policy.sig").write_bytes(key.sign(pp.read_bytes()))
        for n in [pp.name, "sponsored-policy.sig"]:
            m["files"][n] = hashlib.sha256((folder / n).read_bytes()).hexdigest()
        mpath.write_text(json.dumps(m))
        (folder / "manifest.sig").write_bytes(key.sign(mpath.read_bytes()))
        tar = archive(folder, files / f"obc-sponsored-runtime-{name}.tar.gz")
        issued["releases"][name] = dict(
            url="https://github.com/whiteguo233/OpenBiliClaw/releases/download/sponsored-runtime-local-staging/"
            + tar.name,
            sha256=hashlib.sha256(tar.read_bytes()).hexdigest(),
            manifest_sha256=hashlib.sha256(mpath.read_bytes()).hexdigest(),
        )
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
    with pytest.raises(ValueError, match="provenance"):
        upload.handoff(files, issued, core, "f" * 40, push=True, remote=str(bare))

    def git(*args):
        return subprocess.check_output(["git", "-C", str(bare), *args], text=True).strip()

    assert git("for-each-ref") == ""
    preview = upload.handoff(files, issued, core, rust, push=False)
    assert git("for-each-ref") == ""
    result = upload.handoff(files, issued, core, rust, push=True, remote=str(bare))
    assert (
        result == preview == upload.handoff(files, issued, core, rust, push=True, remote=str(bare))
    )
    commit = git("for-each-ref", "--format=%(objectname)")
    assert git("for-each-ref", "--format=%(refname)") == "refs/heads/sdk-input/" + commit
    assert git("rev-list", "--count", commit) == "1"
    assert git("ls-tree", "--name-only", commit).splitlines() == sorted(
        x.name for x in files.glob("*.tar.gz")
    )
    for entry in result["releases"].values():
        assert f"/{commit}/" in entry["url"]
    modified = next(files.glob("*.tar.gz"))
    modified.write_bytes(b"tampered fixture")
    with pytest.raises(sdk.BundleError, match="digest mismatch"):
        upload.handoff(files, issued, core, rust, push=True, remote=str(bare))
    assert git("for-each-ref", "--format=%(objectname)") == commit
