"""Regression checks for the user-facing aggregate GitHub release."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def read_text(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def project_version() -> str:
    pyproject = tomllib.loads(read_text("pyproject.toml"))
    return str(pyproject["project"]["version"])


def release_step_script(name: str) -> str:
    workflow = read_text(".github/workflows/release-extension.yml")
    _, marker, step = workflow.partition(f"      - name: {name}\n")
    assert marker, f"missing release workflow step: {name}"
    step = step.split("\n      - name: ", 1)[0]
    _, marker, script = step.partition("        run: |\n")
    assert marker, f"missing shell script for release workflow step: {name}"
    return textwrap.dedent(script)


@pytest.mark.parametrize("release_exists", [False, True])
def test_extension_component_release_never_takes_latest(
    tmp_path: Path, release_exists: bool
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh_log = tmp_path / "gh.jsonl"
    fake_gh = bin_dir / "gh"
    fake_gh.write_text(
        f"#!{sys.executable}\n"
        + textwrap.dedent(
            """\
            import json
            import os
            import sys

            with open(os.environ["GH_LOG"], "a") as log:
                log.write(json.dumps(sys.argv[1:]) + "\\n")
            if sys.argv[1:3] == ["release", "view"]:
                sys.exit(0 if os.environ["RELEASE_EXISTS"] == "true" else 1)
            """
        ),
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
            "GH_LOG": str(gh_log),
            "GITHUB_REPOSITORY": "example/project",
            "RELEASE_TAG": "extension-v0.0.1",
            "RELEASE_EXISTS": str(release_exists).lower(),
            "FIREFOX_SIGNING_ACTIVE": "false",
            "SAFARI_PACKAGED": "false",
        }
    )
    result = subprocess.run(
        [
            "bash",
            "-euo",
            "pipefail",
            "-c",
            release_step_script("Create GitHub Release and upload extension package"),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    commands = [json.loads(line) for line in gh_log.read_text().splitlines()]
    action = "edit" if release_exists else "create"
    publish_commands = [command for command in commands if command[:2] == ["release", action]]
    assert len(publish_commands) == 1
    assert "--latest=false" in publish_commands[0]
    assert sum(command[:2] == ["release", "upload"] for command in commands) == 2


@pytest.mark.parametrize("matches_backend", [False, True])
def test_extension_aggregate_sync_requires_matching_backend_version(
    tmp_path: Path, matches_backend: bool
) -> None:
    version = project_version()
    (tmp_path / "pyproject.toml").write_text(f'[project]\nversion = "{version}"\n')
    script_dir = tmp_path / ".github" / "scripts"
    script_dir.mkdir(parents=True)
    sync_log = tmp_path / "sync.log"
    (script_dir / "sync-aggregate-release.sh").write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$RELEASE_TAG" > "$SYNC_LOG"\n'
    )
    release_tag = f"extension-v{version}" if matches_backend else "extension-v0.0.1"
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{Path(sys.executable).parent}{os.pathsep}{env['PATH']}",
            "RELEASE_TAG": release_tag,
            "SYNC_LOG": str(sync_log),
        }
    )
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", release_step_script("Update aggregate latest release")],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    if matches_backend:
        assert sync_log.read_text().strip() == release_tag
    else:
        assert not sync_log.exists()
        assert f"Standalone extension release {release_tag}" in result.stdout


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
    assert 'asset_name_seen "$firefox_xpi_asset_name"' in script
    # Fallback wording must state the XPI is absent (v0.3.153 readable copy).
    assert "no signed XPI in this release" in script
    unconditional_xpi_assignment = (
        'firefox_signed_asset_line="use '
        '\\`openbiliclaw-extension-v${extension_version}-firefox.xpi\\`"'
    )
    assert unconditional_xpi_assignment not in script


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

    # Keep this contract test hermetic. The aggregate helper also probes GHCR
    # with curl; letting that hit the real network makes the subprocess race
    # its 20-second timeout under a busy full-suite run.
    fake_curl = bin_dir / "curl"
    fake_curl.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    fake_curl.chmod(0o755)

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


def test_aggregate_release_helper_lists_safari_dmg_when_asset_exists() -> None:
    script = read_text(".github/scripts/sync-aggregate-release.sh")

    assert (
        'safari_extension_asset_name="openbiliclaw-extension-v${extension_version}-safari.dmg"'
        in script
    )
    assert 'asset_name_seen "$safari_extension_asset_name"' in script
    assert "Safari 18+ extension" in script
    assert "openbiliclaw-extension-v*-safari.dmg" in script
    # Absent fallback must never point users at a missing Safari DMG.
    assert "not available yet for this version" in script


def test_extension_release_workflow_builds_safari_dmg_with_or_without_apple_credentials() -> None:
    workflow = read_text(".github/workflows/release-extension.yml")

    assert "runs-on: macos-15" in workflow
    assert "npm run package:safari -- --notarize" in workflow
    assert "npm run package:safari" in workflow
    assert "unsigned ad-hoc Safari DMG" in workflow
    assert "openbiliclaw-extension-v${expected}-safari.dmg" in workflow
    assert "APPLE_DEVELOPER_ID_CERTIFICATE_BASE64" in workflow
    assert "APPLE_DEVELOPER_ID_CERTIFICATE_PASSWORD" in workflow
    assert "APPLE_TEAM_ID" in workflow
    assert "APPLE_NOTARY_USER" in workflow
    assert "APPLE_NOTARY_PASSWORD" in workflow
    assert "SAFARI_SIGNING_ENABLED" in workflow
    assert "extension-safari-package" in workflow


def test_release_completeness_always_checks_safari_dmg() -> None:
    workflow = read_text(".github/workflows/verify-release-completeness.yml")

    assert "openbiliclaw-extension-v${VERSION}-safari.dmg" in workflow
    assert "aggregate safari dmg" in workflow
    assert "SKIP: Safari signing is disabled" not in workflow


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
