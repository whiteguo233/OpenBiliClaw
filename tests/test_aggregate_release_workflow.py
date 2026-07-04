"""Regression checks for the user-facing aggregate GitHub release."""

from __future__ import annotations

import os
import subprocess
import textwrap
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def read_text(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def project_version() -> str:
    pyproject = tomllib.loads(read_text("pyproject.toml"))
    return str(pyproject["project"]["version"])


def test_aggregate_release_helper_updates_latest_release() -> None:
    script = read_text(".github/scripts/sync-aggregate-release.sh")

    assert "openbiliclaw-v" in script
    assert "pyproject.toml" in script
    assert "backend-v" in script
    assert "extension-v" in script
    assert "desktop-v" in script
    assert "gh release create" in script
    assert "gh release edit" in script
    assert "gh release upload" in script
    assert "--latest" in script
    assert "--clobber" in script


def test_aggregate_release_helper_prunes_stale_package_assets() -> None:
    script = read_text(".github/scripts/sync-aggregate-release.sh")

    assert "prune_existing_package_assets" in script
    assert "delete_existing_package_asset" in script
    assert "gh release delete-asset" in script
    assert 'grep -qi "not found"' in script
    assert "openbiliclaw-extension-v*.zip" in script
    assert "OpenBiliClaw-macos-v*.dmg" in script
    assert "OpenBiliClaw-windows-*-Setup.exe" in script


def test_aggregate_release_helper_only_lists_signed_firefox_xpi_when_asset_exists() -> None:
    script = read_text(".github/scripts/sync-aggregate-release.sh")

    expected_xpi_name_assignment = (
        'firefox_xpi_asset_name="openbiliclaw-extension-v${extension_version}-firefox.xpi"'
    )
    assert expected_xpi_name_assignment in script
    assert "asset_name_seen \"$firefox_xpi_asset_name\"" in script
    assert "No signed Firefox extension XPI is available yet." in script
    assert (
        "firefox_signed_asset_line=\"\\`openbiliclaw-extension-v${extension_version}-firefox.xpi\\`\""
        not in script
    )


def test_aggregate_release_helper_does_not_backfill_previous_channel_assets(
    tmp_path: Path,
) -> None:
    version = project_version()
    stale_version = "0.0.1"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh_log = tmp_path / "gh.log"
    fake_gh = bin_dir / "gh"
    fake_gh.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail

            printf '%s\\n' "$*" >> "$GH_LOG"

            if [ "$1" = "release" ] && [ "$2" = "list" ]; then
              printf 'extension-v%s\\n' "$STALE_VERSION"
              printf 'desktop-v%s\\n' "$STALE_VERSION"
              exit 0
            fi

            if [ "$1" = "release" ] && [ "$2" = "view" ]; then
              tag="$3"
              case "$tag" in
                openbiliclaw-v*)
                  exit 0
                  ;;
                extension-v"$PROJECT_VERSION"|desktop-v"$PROJECT_VERSION")
                  exit 1
                  ;;
                *)
                  exit 0
                  ;;
              esac
            fi

            if [ "$1" = "release" ] && [ "$2" = "download" ]; then
              tag="$3"
              target_dir=""
              pattern=""
              while [ "$#" -gt 0 ]; do
                case "$1" in
                  --dir)
                    target_dir="$2"
                    shift 2
                    ;;
                  --pattern)
                    pattern="$2"
                    shift 2
                    ;;
                  *)
                    shift
                    ;;
                esac
              done
              mkdir -p "$target_dir"
              case "$tag:$pattern" in
                extension-v"$STALE_VERSION":openbiliclaw-extension-v*.zip)
                  touch "$target_dir/openbiliclaw-extension-v$STALE_VERSION.zip"
                  exit 0
                  ;;
                desktop-v"$STALE_VERSION":*.dmg)
                  touch "$target_dir/OpenBiliClaw-macos-v$STALE_VERSION-arm64.dmg"
                  exit 0
                  ;;
                desktop-v"$STALE_VERSION":*.exe)
                  touch "$target_dir/OpenBiliClaw-windows-$STALE_VERSION-Setup.exe"
                  exit 0
                  ;;
                *)
                  exit 1
                  ;;
              esac
            fi

            if [ "$1" = "release" ] && [ "$2" = "edit" ]; then
              exit 0
            fi

            if [ "$1" = "release" ] && [ "$2" = "upload" ]; then
              exit 0
            fi

            echo "unexpected gh command: $*" >&2
            exit 1
            """
        ),
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "CHANNEL": "backend",
            "GH_LOG": str(gh_log),
            "GITHUB_REPOSITORY": "whiteguo233/OpenBiliClaw",
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
            "PROJECT_VERSION": version,
            "RELEASE_TAG": f"backend-v{version}",
            "STALE_VERSION": stale_version,
        }
    )

    result = subprocess.run(
        ["bash", ".github/scripts/sync-aggregate-release.sh"],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    commands = gh_log.read_text(encoding="utf-8")
    assert f"release download extension-v{stale_version}" not in commands
    assert f"release download desktop-v{stale_version}" not in commands
    assert f"openbiliclaw-extension-v{stale_version}.zip" not in commands


def test_release_channels_sync_assets_to_aggregate_release() -> None:
    extension = read_text(".github/workflows/release-extension.yml")
    desktop = read_text(".github/workflows/release-desktop.yml")
    backend = read_text(".github/workflows/release-backend.yml")

    assert ".github/scripts/sync-aggregate-release.sh" in extension
    assert "CHANNEL: extension" in extension
    assert "release-artifacts/openbiliclaw-extension-v*.zip" in extension

    assert ".github/scripts/sync-aggregate-release.sh" in desktop
    assert "CHANNEL: desktop" in desktop
    assert "release-artifacts/*.dmg release-artifacts/*.exe" in desktop

    assert ".github/scripts/sync-aggregate-release.sh" in backend
    assert "contents: write" in backend
    assert "CHANNEL: backend" in backend


def test_user_docs_explain_aggregate_release_entrypoint() -> None:
    docs = {
        "README.md": read_text("README.md"),
        "README_EN.md": read_text("README_EN.md"),
        "docs/index.md": read_text("docs/index.md"),
        "docs/modules/extension.md": read_text("docs/modules/extension.md"),
        "docs/modules/runtime.md": read_text("docs/modules/runtime.md"),
    }

    for relative_path, content in docs.items():
        assert "openbiliclaw-v*" in content, f"{relative_path} must mention the aggregate tag"

    assert "聚合" in docs["README.md"]
    assert "aggregate" in docs["README_EN.md"].lower()
    assert "Latest Release" in docs["docs/index.md"]
