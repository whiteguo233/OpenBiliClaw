"""Publish an existing verified Intel installer without rebuilding or replacing assets."""

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def validate_source(run: dict, expected_sha: str) -> None:
    """Require a successful installer build of the immutable desktop source tag."""
    if (
        run.get("head_sha") != expected_sha
        or run.get("conclusion") != "success"
        or run.get("path") != ".github/workflows/build-installers.yml"
    ):
        raise ValueError("source run must be a successful installer build of the release commit")


def verify_asset(root: Path, version: str, digest: str, source_sha: str) -> Path:
    """Accept one Intel DMG with the reviewed version, commit suffix and exact bytes."""
    candidates = list(root.glob("*.dmg"))
    if len(candidates) != 1:
        raise ValueError("expected exactly one Intel DMG")
    path = candidates[0]
    match = re.fullmatch(
        rf"OpenBiliClaw-macos-v{re.escape(version)}(?:\.([0-9a-f]{{7,40}}))?-x64\.dmg",
        path.name,
    )
    if (
        not match
        or path.is_symlink()
        or not path.is_file()
        or (match[1] and not source_sha.startswith(match[1]))
    ):
        raise ValueError("installer name does not match the reviewed release")
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != digest:
        raise ValueError("installer SHA-256 differs from the reviewed artifact")
    canonical = root / f"OpenBiliClaw-macos-v{version}-x64.dmg"
    if canonical != path:
        path.rename(canonical)
    return canonical


def api(path: str) -> dict:
    return json.loads(subprocess.check_output(["gh", "api", path], text=True))


def main() -> None:
    version, run_id, digest = (os.environ[k] for k in ("VERSION", "BUILD_RUN_ID", "ASSET_SHA256"))
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("invalid version")
    if not re.fullmatch(r"[0-9]+", run_id) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("invalid build run or SHA-256")
    repo = os.environ["GITHUB_REPOSITORY"]
    base = f"repos/{repo}"
    source_sha = api(f"{base}/commits/desktop-v{version}")["sha"]
    validate_source(api(f"{base}/actions/runs/{run_id}"), source_sha)
    if sys.argv[1:] == ["verify-source"]:
        print(f"Verified source {source_sha}")
        return
    if sys.argv[1:] != ["publish"]:
        raise ValueError("expected verify-source or publish")
    asset = verify_asset(Path("intel-artifact"), version, digest, source_sha)
    for tag in (f"desktop-v{version}", f"openbiliclaw-v{version}"):
        release = api(f"{base}/releases/tags/{tag}")
        if release.get("draft"):
            raise ValueError("target release must already be published")
        existing = next((a for a in release["assets"] if a["name"] == asset.name), None)
        if existing:
            if existing.get("digest") != "sha256:" + digest or existing.get("state") != "uploaded":
                raise ValueError("refusing to replace a different or incomplete release asset")
        else:
            subprocess.run(["gh", "release", "upload", tag, str(asset), "--repo", repo], check=True)
        print(json.dumps({"release": tag, "asset": asset.name, "sha256": digest}))


if __name__ == "__main__":
    main()
