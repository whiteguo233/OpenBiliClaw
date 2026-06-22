"""Regression checks for the user-facing aggregate GitHub release."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def read_text(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


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
