"""Tests for the desktop entry point's data-location + migration logic.

``packaging/entry.py`` is not part of the importable package, so load it by
path (mirroring ``test_packaging_build.py``). The risky behaviour here is moving
user data out of the install directory on upgrade — cover it directly.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_entry_module():
    project_root = Path(__file__).resolve().parent.parent
    module_path = project_root / "packaging" / "entry.py"
    spec = importlib.util.spec_from_file_location("openbiliclaw_packaging_entry", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


entry = _load_entry_module()


# --------------------------------------------------------------------------- #
# _user_data_root_for — per-OS conventional location, independent of install dir
#
# Use the pure resolver with injected params: monkeypatching the real os.name to
# "nt" would make pathlib try (and fail) to build a WindowsPath on POSIX CI.
# Path comparisons stay internally consistent because both sides construct paths
# the same way on the host.
# --------------------------------------------------------------------------- #


def test_user_data_root_windows_uses_localappdata() -> None:
    root = entry._user_data_root_for(
        "nt",
        "win32",
        Path(r"C:\Users\tester"),
        {"LOCALAPPDATA": r"C:\Users\tester\AppData\Local"},
    )

    assert root == Path(r"C:\Users\tester\AppData\Local") / "OpenBiliClaw"


def test_user_data_root_windows_falls_back_when_localappdata_missing() -> None:
    root = entry._user_data_root_for("nt", "win32", Path(r"C:\Users\tester"), {})

    assert root == Path(r"C:\Users\tester") / "AppData" / "Local" / "OpenBiliClaw"


def test_user_data_root_macos_uses_application_support() -> None:
    root = entry._user_data_root_for("posix", "darwin", Path("/Users/tester"), {})

    assert root == Path("/Users/tester/Library/Application Support/OpenBiliClaw")


def test_user_data_root_linux_prefers_xdg() -> None:
    root = entry._user_data_root_for(
        "posix", "linux", Path("/home/tester"), {"XDG_DATA_HOME": "/home/tester/.local/share"}
    )

    assert root == Path("/home/tester/.local/share/OpenBiliClaw")


def test_user_data_root_linux_falls_back_without_xdg() -> None:
    root = entry._user_data_root_for("posix", "linux", Path("/home/tester"), {})

    assert root == Path("/home/tester/.local/share/OpenBiliClaw")


def test_user_data_root_delegates_to_pure_resolver() -> None:
    # The thin wrapper just feeds real os/sys/home/environ to the pure resolver.
    assert entry._user_data_root() == entry._user_data_root_for(
        entry.os.name, entry.sys.platform, Path.home(), entry.os.environ
    )


# --------------------------------------------------------------------------- #
# _resolve_runtime_paths — onedir keeps data out of the install dir
# --------------------------------------------------------------------------- #


def test_resolve_runtime_paths_dev_fallback_uses_repo_root() -> None:
    project_root, bundled = entry._resolve_runtime_paths()

    repo_root = Path(entry.__file__).resolve().parent.parent
    assert project_root == repo_root
    assert bundled == repo_root


def test_resolve_runtime_paths_onedir_splits_data_from_install_dir(
    monkeypatch, tmp_path: Path
) -> None:
    # Simulate a frozen onedir launch. We can't force os.name="nt" on POSIX CI
    # (breaks pathlib), so assert the *split* property — data root is separate
    # from the install dir — using whatever _user_data_root() the host returns.
    install_dir = tmp_path / "Programs" / "OpenBiliClaw"
    install_dir.mkdir(parents=True)
    monkeypatch.setattr(entry.sys, "frozen", True, raising=False)
    monkeypatch.setattr(entry.sys, "executable", str(install_dir / "OpenBiliClaw"))

    project_root, bundled = entry._resolve_runtime_paths()

    # User data lives in the per-user root, NOT next to the executable.
    assert bundled == install_dir
    assert project_root == entry._user_data_root()
    assert project_root != bundled


# --------------------------------------------------------------------------- #
# _migrate_legacy_install_dir_data — relocate old in-install-dir data
# --------------------------------------------------------------------------- #


def _seed_legacy_install(install_dir: Path) -> None:
    install_dir.mkdir(parents=True, exist_ok=True)
    (install_dir / "config.toml").write_text("language = 'zh'\n", encoding="utf-8")
    (install_dir / "data").mkdir()
    (install_dir / "data" / "openbiliclaw.db").write_bytes(b"SQLite format 3\x00payload")
    (install_dir / "logs").mkdir()
    (install_dir / "logs" / "openbiliclaw.log").write_text("hello\n", encoding="utf-8")


def test_migrate_moves_config_data_and_logs(tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    project_root = tmp_path / "userdata"
    _seed_legacy_install(install_dir)
    original_db = (install_dir / "data" / "openbiliclaw.db").read_bytes()

    entry._migrate_legacy_install_dir_data(install_dir, project_root)

    # Moved into the new root with contents intact...
    assert (project_root / "config.toml").read_text(encoding="utf-8") == "language = 'zh'\n"
    assert (project_root / "data" / "openbiliclaw.db").read_bytes() == original_db
    assert (project_root / "logs" / "openbiliclaw.log").exists()
    # ...and gone from the install dir (so upgrades/uninstall can't touch them).
    assert not (install_dir / "config.toml").exists()
    assert not (install_dir / "data").exists()
    assert not (install_dir / "logs").exists()


def test_migrate_skips_when_new_root_already_has_config(tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    project_root = tmp_path / "userdata"
    _seed_legacy_install(install_dir)
    project_root.mkdir()
    (project_root / "config.toml").write_text("language = 'en'\n", encoding="utf-8")

    entry._migrate_legacy_install_dir_data(install_dir, project_root)

    # Existing config preserved; nothing clobbered or pulled from the install dir.
    assert (project_root / "config.toml").read_text(encoding="utf-8") == "language = 'en'\n"
    assert not (project_root / "data").exists()
    assert (install_dir / "config.toml").exists()  # left untouched


def test_migrate_skips_when_new_root_already_has_database(tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    project_root = tmp_path / "userdata"
    _seed_legacy_install(install_dir)
    (project_root / "data").mkdir(parents=True)
    (project_root / "data" / "openbiliclaw.db").write_bytes(b"existing")

    entry._migrate_legacy_install_dir_data(install_dir, project_root)

    assert (project_root / "data" / "openbiliclaw.db").read_bytes() == b"existing"
    assert not (project_root / "config.toml").exists()


def test_migrate_does_not_clobber_partial_destination(tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    project_root = tmp_path / "userdata"
    _seed_legacy_install(install_dir)
    # A logs/ already exists in the new root (but no config/db → migration runs).
    (project_root / "logs").mkdir(parents=True)
    (project_root / "logs" / "keep.log").write_text("keep\n", encoding="utf-8")

    entry._migrate_legacy_install_dir_data(install_dir, project_root)

    # config + data migrated; the pre-existing logs/ is left as-is (not overwritten).
    assert (project_root / "config.toml").exists()
    assert (project_root / "data" / "openbiliclaw.db").exists()
    assert (project_root / "logs" / "keep.log").read_text(encoding="utf-8") == "keep\n"
    assert (install_dir / "logs").exists()  # not moved (destination existed)


def test_migrate_noop_when_install_dir_equals_project_root(tmp_path: Path) -> None:
    install_dir = tmp_path / "same"
    _seed_legacy_install(install_dir)

    entry._migrate_legacy_install_dir_data(install_dir, install_dir)

    # Dev / same-dir layout: everything stays put, no nesting.
    assert (install_dir / "config.toml").exists()
    assert (install_dir / "data" / "openbiliclaw.db").exists()
    assert not (install_dir / "data" / "data").exists()


def test_migrate_noop_when_nothing_legacy(tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    project_root = tmp_path / "userdata"

    entry._migrate_legacy_install_dir_data(install_dir, project_root)

    # Fresh install: no legacy data, new root not even created by migration.
    assert not project_root.exists()


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    project_root = tmp_path / "userdata"
    _seed_legacy_install(install_dir)

    entry._migrate_legacy_install_dir_data(install_dir, project_root)
    db_after_first = (project_root / "data" / "openbiliclaw.db").read_bytes()
    # Second run (now the install dir is empty) must be a clean no-op.
    entry._migrate_legacy_install_dir_data(install_dir, project_root)

    assert (project_root / "data" / "openbiliclaw.db").read_bytes() == db_after_first


def test_migrate_survives_unmovable_entry(tmp_path: Path, monkeypatch) -> None:
    install_dir = tmp_path / "install"
    project_root = tmp_path / "userdata"
    _seed_legacy_install(install_dir)

    real_move = entry.shutil.move

    def _flaky_move(src: str, dst: str):
        if src.endswith("logs"):
            raise OSError("simulated lock")
        return real_move(src, dst)

    monkeypatch.setattr(entry.shutil, "move", _flaky_move)

    # Must not raise — a failed move degrades to leaving that entry behind.
    entry._migrate_legacy_install_dir_data(install_dir, project_root)

    assert (project_root / "config.toml").exists()
    assert (project_root / "data" / "openbiliclaw.db").exists()
    assert (install_dir / "logs").exists()  # the one that failed to move


if __name__ == "__main__":  # pragma: no cover - convenience
    raise SystemExit(pytest.main([__file__, "-q"]))
