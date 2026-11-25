"""Verify five locally sealed archives; push only those bytes to a new SDK input branch."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from openbiliclaw.runtime import sponsored_bundle as sdk

if __package__:
    from . import verify_release_sdk as gate
else:
    import verify_release_sdk as gate


def handoff(archives: Path, issued: dict, core_sha: str, runtime_sha: str, *, push: bool) -> dict:
    """Create an orphan data commit. Never trust a key or executable supplied by the branch."""
    import re

    if not all(re.fullmatch(r"[a-f0-9]{40}", sha) for sha in (core_sha, runtime_sha)):
        raise ValueError("two full reviewed source commits are required")
    trust = json.loads(sdk.CATALOG.read_bytes())
    if trust["public_key_hex"] != issued["public_key_hex"] or issued["schema_version"] != 1:
        raise ValueError("issued catalog does not match the Core trust root")
    with tempfile.TemporaryDirectory(prefix="obc-sdk-upload-") as tmp:
        directory = Path(tmp)
        names = [f"obc-sponsored-runtime-{p}.tar.gz" for p in gate.PLATFORMS]
        for name in names:
            src = archives / name
            if src.is_symlink() or not src.is_file() or src.stat().st_size > sdk.MAX_ARCHIVE_BYTES:
                raise ValueError(f"invalid SDK input: {name}")
            shutil.copyfile(src, directory / name)
        gate.verify_catalog(issued, directory, require_reusable=True)
        for name, platform in zip(names, gate.PLATFORMS, strict=True):
            with tarfile.open(directory / name, "r:gz") as archive:
                stream = archive.extractfile("manifest.json")
                assert stream is not None  # already unpacked and verified by Core
                manifest = json.load(stream)
            allowed = {"manifest-only"}
            if platform.startswith("macos-"):
                allowed = {"macos-notarized"}
            elif platform.startswith("windows-"):
                allowed.add("windows-authenticode")
            if (
                manifest.get("platform_signing") not in allowed
                or manifest.get("core_sha") != core_sha
                or manifest.get("runtime_sha") != runtime_sha
            ):
                raise ValueError(f"{platform}: wrong signing state or build provenance")
        # Hash the exact archive bytes into Git; no checkout, clean filters, hooks, LFS,
        # private source, build cache, or release-policy signing credentials in this repo.
        repo = directory / "objects.git"
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(
            GIT_AUTHOR_NAME="OpenBiliClaw SDK",
            GIT_COMMITTER_NAME="OpenBiliClaw SDK",
            GIT_AUTHOR_EMAIL="sdk@openbiliclaw.invalid",
            GIT_COMMITTER_EMAIL="sdk@openbiliclaw.invalid",
            GIT_AUTHOR_DATE="2000-01-01T00:00:00Z",
            GIT_COMMITTER_DATE="2000-01-01T00:00:00Z",
        )

        def git(*args: str, data: bytes | None = None) -> str:
            command = ["git", "-c", "core.hooksPath=" + os.devnull, *args]
            return subprocess.check_output(command, cwd=repo, env=env, input=data).decode().strip()

        repo.mkdir()
        git("init", "--bare", "--quiet", "--object-format=sha1")
        records = []
        for name in sorted(names):
            oid = git("hash-object", "-w", "--no-filters", str(directory / name))
            records.append(f"100644 blob {oid}\t{name}\n")
        tree = git("mktree", data="".join(records).encode())
        commit = git(
            "commit-tree", tree, "-m", f"Sealed SDKs from {runtime_sha} for Core {core_sha}"
        )
        branch = f"refs/heads/sdk-input/{commit}"
        cat = {"schema_version": 1, "public_key_hex": trust["public_key_hex"], "releases": {}}
        for platform, name in zip(gate.PLATFORMS, names, strict=True):
            entry = issued["releases"][platform]
            cat["releases"][platform] = {
                "url": f"https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/{commit}/{name}",
                "sha256": entry["sha256"],
                "manifest_sha256": entry["manifest_sha256"],
            }
        gate.verify_catalog(cat, directory, require_reusable=True)
        if push:
            remote = "git@github.com:whiteguo233/OpenBiliClaw.git"
            git("push", remote, f"{commit}:{branch}")  # never force / overwrite
            if git("ls-remote", remote, branch).split()[0] != commit:
                raise ValueError("uploaded branch did not resolve to the verified commit")
        return cat


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archives", required=True, type=Path)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--core-sha", required=True)
    parser.add_argument("--runtime-sha", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--push", action="store_true", help="default: verify and preview offline")
    a = parser.parse_args()
    if a.output.exists():
        sys.exit("choose a new output path")
    result = handoff(
        a.archives, json.loads(a.catalog.read_bytes()), a.core_sha, a.runtime_sha, push=a.push
    )
    with a.output.open("x") as file:
        file.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
