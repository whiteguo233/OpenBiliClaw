"""Tests for the desktop entry point's data-location + migration logic.

``packaging/entry.py`` is not part of the importable package, so load it by
path (mirroring ``test_packaging_build.py``). The risky behaviour here is moving
user data out of the install directory on upgrade — cover it directly.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace

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
_real_reconcile_packaged_autostart = entry._reconcile_packaged_autostart


@pytest.fixture(autouse=True)
def _prevent_real_login_item_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Entry-point tests must never mutate the developer's real login items."""
    monkeypatch.setattr(entry, "_reconcile_packaged_autostart", lambda _config: None)


def test_reconcile_packaged_autostart_uses_shared_runtime_reconcile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.runtime import autostart

    config = SimpleNamespace()
    calls: list[object] = []
    monkeypatch.setattr(autostart, "reconcile", lambda loaded: calls.append(loaded) or None)

    _real_reconcile_packaged_autostart(config)

    assert calls == [config]


# --------------------------------------------------------------------------- #
# _user_data_root_for — unified with one-line / AI install data root
#
# Use the pure resolver with injected params: monkeypatching the real os.name to
# "nt" would make pathlib try (and fail) to build a WindowsPath on POSIX CI.
# Path comparisons stay internally consistent because both sides construct paths
# the same way on the host.
# --------------------------------------------------------------------------- #


def test_user_data_root_windows_uses_home_openbiliclaw() -> None:
    root = entry._user_data_root_for(
        "nt",
        "win32",
        Path(r"C:\Users\tester"),
        {"LOCALAPPDATA": r"C:\Users\tester\AppData\Local"},
    )

    assert root == Path(r"C:\Users\tester") / "OpenBiliClaw"


def test_user_data_root_windows_ignores_localappdata_fallback() -> None:
    root = entry._user_data_root_for("nt", "win32", Path(r"C:\Users\tester"), {})

    assert root == Path(r"C:\Users\tester") / "OpenBiliClaw"


def test_user_data_root_macos_uses_home_openbiliclaw() -> None:
    root = entry._user_data_root_for("posix", "darwin", Path("/Users/tester"), {})

    assert root == Path("/Users/tester/OpenBiliClaw")


def test_user_data_root_linux_ignores_xdg() -> None:
    root = entry._user_data_root_for(
        "posix", "linux", Path("/home/tester"), {"XDG_DATA_HOME": "/home/tester/.local/share"}
    )

    assert root == Path("/home/tester/OpenBiliClaw")


def test_user_data_root_linux_uses_home_openbiliclaw() -> None:
    root = entry._user_data_root_for("posix", "linux", Path("/home/tester"), {})

    assert root == Path("/home/tester/OpenBiliClaw")


def test_user_data_root_delegates_to_pure_resolver() -> None:
    # The thin wrapper just feeds real os/sys/home/environ to the pure resolver.
    assert entry._user_data_root() == entry._user_data_root_for(
        entry.os.name, entry.sys.platform, Path.home(), entry.os.environ
    )


# --------------------------------------------------------------------------- #
# _legacy_packaged_user_data_root_for — old desktop-package data locations
# --------------------------------------------------------------------------- #


def test_legacy_packaged_user_data_root_windows_uses_localappdata() -> None:
    root = entry._legacy_packaged_user_data_root_for(
        "nt",
        "win32",
        Path(r"C:\Users\tester"),
        {"LOCALAPPDATA": r"C:\Users\tester\AppData\Local"},
    )

    assert root == Path(r"C:\Users\tester\AppData\Local") / "OpenBiliClaw"


def test_legacy_packaged_user_data_root_macos_uses_application_support() -> None:
    root = entry._legacy_packaged_user_data_root_for(
        "posix",
        "darwin",
        Path("/Users/tester"),
        {},
    )

    assert root == Path("/Users/tester/Library/Application Support/OpenBiliClaw")


# --------------------------------------------------------------------------- #
# _resolve_runtime_paths — onedir keeps data out of the install dir
# --------------------------------------------------------------------------- #


def test_resolve_runtime_paths_dev_fallback_uses_repo_root(monkeypatch) -> None:
    monkeypatch.delenv("OPENBILICLAW_PROJECT_ROOT", raising=False)
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
    monkeypatch.delenv("OPENBILICLAW_PROJECT_ROOT", raising=False)
    install_dir = tmp_path / "Programs" / "OpenBiliClaw"
    install_dir.mkdir(parents=True)
    monkeypatch.setattr(entry.sys, "frozen", True, raising=False)
    monkeypatch.setattr(entry.sys, "executable", str(install_dir / "OpenBiliClaw"))

    project_root, bundled = entry._resolve_runtime_paths()

    # User data lives in the per-user root, NOT next to the executable.
    assert bundled == install_dir
    assert project_root == entry._user_data_root()
    assert project_root != bundled


def test_resolve_runtime_paths_honors_project_root_override(monkeypatch, tmp_path: Path) -> None:
    # An explicit OPENBILICLAW_PROJECT_ROOT relocates user data (portable installs
    # / isolated tests) while bundled resources still resolve from the package.
    override = tmp_path / "custom-data-root"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(override))

    project_root, _bundled = entry._resolve_runtime_paths()

    assert project_root == override


# --------------------------------------------------------------------------- #
# _repair_unloadable_config — desktop startup self-heals broken config files
# --------------------------------------------------------------------------- #


def test_repair_unloadable_config_reseeds_malformed_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "userdata"
    bundled = tmp_path / "bundle"
    project_root.mkdir()
    bundled.mkdir()
    bad_config = b"\xef\xbb\xbf[api]\nport = 18420\n"
    (project_root / "config.toml").write_bytes(bad_config)
    (bundled / "config.example.toml").write_text("[api]\nport = 18421\n", encoding="utf-8")
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(project_root))

    result = entry._repair_unloadable_config(project_root, bundled)

    assert result.repaired is True
    assert result.regenerated_default is True
    assert (project_root / "config.toml").read_text(encoding="utf-8") == "[api]\nport = 18421\n"
    assert (project_root / "config.toml.invalid").read_bytes() == bad_config


def test_repair_unloadable_config_reseeds_structurally_invalid_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "userdata"
    bundled = tmp_path / "bundle"
    project_root.mkdir()
    bundled.mkdir()
    (project_root / "config.toml").write_text('llm = "not-a-table"\n', encoding="utf-8")
    (bundled / "config.example.toml").write_text("[api]\nport = 18421\n", encoding="utf-8")
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(project_root))

    result = entry._repair_unloadable_config(project_root, bundled)

    assert result.repaired is True
    assert result.regenerated_default is True
    assert (project_root / "config.toml").read_text(encoding="utf-8") == "[api]\nport = 18421\n"
    assert (project_root / "config.toml.invalid").read_text(encoding="utf-8") == (
        'llm = "not-a-table"\n'
    )


def test_repair_unloadable_config_quarantines_bad_local_without_reseeding_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "userdata"
    bundled = tmp_path / "bundle"
    project_root.mkdir()
    bundled.mkdir()
    main_config = "[api]\nport = 18420\n"
    bad_local = b"{not toml}"
    (project_root / "config.toml").write_text(main_config, encoding="utf-8")
    (project_root / "config.local.toml").write_bytes(bad_local)
    (bundled / "config.example.toml").write_text("[api]\nport = 18421\n", encoding="utf-8")
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(project_root))

    result = entry._repair_unloadable_config(project_root, bundled)

    assert result.repaired is True
    assert result.regenerated_default is False
    assert (project_root / "config.toml").read_text(encoding="utf-8") == main_config
    assert not (project_root / "config.local.toml").exists()
    assert (project_root / "config.local.toml.invalid").read_bytes() == bad_local


# --------------------------------------------------------------------------- #
# _migrate_legacy_install_dir_data — relocate old in-install-dir data
# --------------------------------------------------------------------------- #


def _seed_legacy_install(install_dir: Path) -> None:
    install_dir.mkdir(parents=True, exist_ok=True)
    (install_dir / "config.toml").write_text("language = 'zh'\n", encoding="utf-8")
    (install_dir / "config.local.toml").write_text("[api]\nport = 18420\n", encoding="utf-8")
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
    assert (project_root / "config.local.toml").read_text(encoding="utf-8") == (
        "[api]\nport = 18420\n"
    )
    assert (project_root / "data" / "openbiliclaw.db").read_bytes() == original_db
    assert (project_root / "logs" / "openbiliclaw.log").exists()
    # ...and gone from the install dir (so upgrades/uninstall can't touch them).
    assert not (install_dir / "config.toml").exists()
    assert not (install_dir / "config.local.toml").exists()
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


# --------------------------------------------------------------------------- #
# _copy_legacy_packaged_user_data — old desktop package root -> unified root
# --------------------------------------------------------------------------- #


def test_copy_legacy_packaged_user_data_copies_without_removing_source(tmp_path: Path) -> None:
    old_root = tmp_path / "Library" / "Application Support" / "OpenBiliClaw"
    new_root = tmp_path / "OpenBiliClaw"
    _seed_legacy_install(old_root)
    original_db = (old_root / "data" / "openbiliclaw.db").read_bytes()

    entry._copy_legacy_packaged_user_data(old_root, new_root)

    assert (new_root / "config.toml").read_text(encoding="utf-8") == "language = 'zh'\n"
    assert (new_root / "config.local.toml").read_text(encoding="utf-8") == ("[api]\nport = 18420\n")
    assert (new_root / "data" / "openbiliclaw.db").read_bytes() == original_db
    assert (new_root / "logs" / "openbiliclaw.log").read_text(encoding="utf-8") == "hello\n"
    assert (old_root / "config.toml").exists()
    assert (old_root / "data" / "openbiliclaw.db").exists()


def test_copy_legacy_packaged_user_data_never_clobbers_existing_target(
    tmp_path: Path,
) -> None:
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    _seed_legacy_install(old_root)
    new_root.mkdir()
    (new_root / "config.toml").write_text("language = 'en'\n", encoding="utf-8")
    (new_root / "data").mkdir()
    (new_root / "data" / "openbiliclaw.db").write_bytes(b"existing")

    entry._copy_legacy_packaged_user_data(old_root, new_root)

    assert (new_root / "config.toml").read_text(encoding="utf-8") == "language = 'en'\n"
    assert (new_root / "data" / "openbiliclaw.db").read_bytes() == b"existing"
    assert (new_root / "config.local.toml").exists()


# --------------------------------------------------------------------------- #
# System-tray desktop mode gating (Windows-only, frozen-only)
# --------------------------------------------------------------------------- #


def test_should_use_tray_false_when_not_frozen() -> None:
    # The test process isn't frozen → tray mode is never selected (dev keeps its
    # foreground/console server).
    assert entry._should_use_tray() is False


def test_should_use_tray_false_on_unsupported_platform(monkeypatch) -> None:
    # Frozen but neither Windows nor macOS (e.g. Linux) → no tray, regardless of
    # whether pystray is importable. Short-circuits before the import check.
    monkeypatch.setattr(entry.sys, "frozen", True, raising=False)
    monkeypatch.setattr(entry.os, "name", "posix")
    monkeypatch.setattr(entry.sys, "platform", "linux")

    assert entry._should_use_tray() is False


def test_tray_icon_uses_canonical_web_brand_asset() -> None:
    from PIL import Image, ImageChops

    from openbiliclaw import __file__ as package_init

    icon_path = Path(package_init).resolve().parent / "web" / "icon-192.png"
    with Image.open(icon_path) as source:
        expected = source.convert("RGBA").resize((64, 64), Image.Resampling.LANCZOS)

    actual = entry._tray_icon_image()

    assert actual.mode == "RGBA"
    assert actual.size == (64, 64)
    assert ImageChops.difference(actual, expected).getbbox() is None


def test_redirect_output_to_logfile_noop_when_not_frozen(tmp_path: Path) -> None:
    # Dev (not frozen) keeps its real stdout/stderr — the redirect is a no-op and
    # must not create a log file.
    assert entry._redirect_output_to_logfile(tmp_path) is None
    assert not (tmp_path / "logs" / "desktop.log").exists()


def test_close_splash_noop_without_pyi_splash() -> None:
    # Dev / non-splash builds have no ``pyi_splash`` module — closing the splash
    # must be a silent no-op, never raise.
    entry._close_splash()  # must not raise


def test_ensure_embedding_model_async_reports_global_pull_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.config import Config
    from openbiliclaw.runtime import embedding_progress

    cfg = Config()
    cfg.llm.embedding.provider = "ollama"
    cfg.llm.embedding.model = "bge-m3"
    cfg.llm.embedding.base_url = "http://localhost:11434/v1"
    pulled: list[tuple[str, str]] = []
    running_at_thread_start: list[bool] = []

    class _InlineThread:
        def __init__(self, *, target, name=None, daemon=None) -> None:
            self._target = target

        def start(self) -> None:
            running_at_thread_start.append(embedding_progress.snapshot()["running"])
            self._target()

    async def fake_pull(base_url: str, model: str, *, on_progress=None, **_kw: object):
        pulled.append((base_url, model))
        if on_progress is not None:
            on_progress("downloading", 240 * 1024 * 1024, 568 * 1024 * 1024)
        return (True, "")

    monkeypatch.setattr(entry.threading, "Thread", _InlineThread)
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: cfg)
    monkeypatch.setattr("openbiliclaw.cli._ollama_has_model", lambda *_args: False)
    monkeypatch.setattr("openbiliclaw.llm.ollama_diagnostics.pull_ollama_model", fake_pull)

    entry._ensure_embedding_model_async()

    snap = embedding_progress.snapshot()
    assert running_at_thread_start == [True]
    assert pulled == [("http://localhost:11434/v1", "bge-m3")]
    assert snap["running"] is False
    assert snap["done"] is True
    assert snap["ok"] is True
    assert snap["model"] == "bge-m3"
    assert snap["completed"] == 240 * 1024 * 1024
    assert snap["total"] == 568 * 1024 * 1024


def test_main_source_selftest_does_not_migrate_ignored_project_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "source-checkout"
    project_root = tmp_path / "selftest-profile"
    source_data = source_root / "data"
    source_data.mkdir(parents=True)
    config_bytes = b"[api]\nport = 18420\n"
    data_bytes = b"source-only ignored data"
    (source_root / "config.toml").write_bytes(config_bytes)
    (source_data / "marker.bin").write_bytes(data_bytes)

    monkeypatch.setenv("OPENBILICLAW_SELFTEST", "1")
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(project_root))
    monkeypatch.setattr(entry.sys, "frozen", False, raising=False)
    monkeypatch.setattr(
        entry,
        "__file__",
        str(source_root / "packaging" / "entry.py"),
    )
    monkeypatch.setattr(entry, "_redirect_output_to_logfile", lambda _root: None)
    monkeypatch.setattr(entry, "_notify_starting", lambda: None)
    monkeypatch.setattr(entry, "_copy_legacy_packaged_user_data", lambda *_args: None)
    monkeypatch.setattr(entry, "_inject_bundled_ollama_on_path", lambda _resources: False)
    monkeypatch.setattr(entry, "_seed_default_config", lambda *_args: False)
    monkeypatch.setattr(
        entry,
        "_repair_unloadable_config",
        lambda *_args: entry._ConfigRepairResult(False, False),
    )
    monkeypatch.setattr(entry, "_close_splash", lambda: None)

    import openbiliclaw.api.app as api_app

    monkeypatch.setattr(api_app, "create_app", lambda: SimpleNamespace())

    entry.main()

    assert (source_root / "config.toml").read_bytes() == config_bytes
    assert (source_data / "marker.bin").read_bytes() == data_bytes
    assert not (project_root / "config.toml").exists()
    assert not (project_root / "data" / "marker.bin").exists()


def test_main_recovers_apply_journal_before_seeding_or_creating_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash after target -> backup must be recovered before normal startup."""
    from openbiliclaw.storage import migration

    project_root = tmp_path / "userdata"
    migration_root = project_root / ".openbiliclaw-migration"
    migration_root.mkdir(parents=True)
    migration_id = "c" * 32
    token = migration_id[:12]
    target_config = project_root / "config.toml"
    target_local = project_root / "config.local.toml"
    target_data = project_root / "data"
    config_backup = project_root / f"config.toml.pre-import-{token}.bak"
    local_backup = project_root / f"config.local.toml.pre-import-{token}.bak"
    data_backup = project_root / f"data.pre-import-{token}.bak"
    prepared_config = project_root / f".config.toml.import-{token}"
    prepared_data = project_root / f".data.import-{token}"
    config_backup.write_text('[api]\nhost = "127.0.0.1"\nport = 18420\n', encoding="utf-8")
    data_backup.mkdir()
    (data_backup / "restored.txt").write_text("old target data", encoding="utf-8")
    (migration_root / "apply-journal.json").write_text(
        json.dumps(
            {
                "state": "applying",
                "migration_id": migration_id,
                "target_config": str(target_config.resolve()),
                "target_local": str(target_local.resolve()),
                "target_data": str(target_data.resolve()),
                "prepared_config": str(prepared_config.resolve()),
                "prepared_data": str(prepared_data.resolve()),
                "config_backup": str(config_backup.resolve()),
                "local_backup": str(local_backup.resolve()),
                "data_backup": str(data_backup.resolve()),
                "target_config_existed": True,
                "target_local_existed": False,
                "target_data_existed": True,
                "new_config_active": False,
                "new_data_active": False,
            }
        ),
        encoding="utf-8",
    )
    assert not target_config.exists()
    assert not target_data.exists()

    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(project_root))
    monkeypatch.delenv("OPENBILICLAW_SELFTEST", raising=False)
    monkeypatch.setattr(entry.sys, "frozen", False, raising=False)
    monkeypatch.setattr(entry, "_redirect_output_to_logfile", lambda _root: None)
    monkeypatch.setattr(entry, "_notify_starting", lambda: None)
    monkeypatch.setattr(entry, "_copy_legacy_packaged_user_data", lambda *_args: None)
    monkeypatch.setattr(entry, "_inject_bundled_ollama_on_path", lambda _resources: False)
    monkeypatch.setattr(entry.atexit, "register", lambda *_args, **_kwargs: None)

    events: list[str] = []
    real_apply = migration.apply_pending_migration

    class _Guard:
        def release(self) -> None:
            events.append("release")

    def _acquire_guard(root: Path, data_dir: Path) -> _Guard:
        assert root == project_root.resolve()
        assert data_dir == target_data.resolve()
        events.append("guard")
        return _Guard()

    def _apply_pending(*, project_root: Path, locked_data_dir: Path):
        # Neither the normal data mkdir nor config seed/repair may have occupied
        # the restore targets before crash recovery gets its turn.
        assert not target_config.exists()
        assert not target_data.exists()
        events.append("apply")
        return real_apply(project_root=project_root, locked_data_dir=locked_data_dir)

    def _seed(*_args: object) -> bool:
        assert target_config.is_file()
        assert (target_data / "restored.txt").read_text(encoding="utf-8") == "old target data"
        events.append("seed")
        return False

    class _StartupObservedError(RuntimeError):
        pass

    def _repair(*_args: object) -> entry._ConfigRepairResult:
        assert target_config.is_file()
        assert (target_data / "restored.txt").is_file()
        events.append("repair")
        raise _StartupObservedError

    monkeypatch.setattr(migration, "acquire_migration_runtime_guard", _acquire_guard)
    monkeypatch.setattr(migration, "apply_pending_migration", _apply_pending)
    monkeypatch.setattr(entry, "_seed_default_config", _seed)
    monkeypatch.setattr(entry, "_repair_unloadable_config", _repair)

    with pytest.raises(_StartupObservedError):
        entry.main()

    assert events == ["guard", "apply", "seed", "repair"]
    assert not (migration_root / "apply-journal.json").exists()


def test_main_uses_configured_api_host_when_env_host_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "userdata"
    project_root.mkdir()
    (project_root / "config.toml").write_text(
        '[api]\nhost = "0.0.0.0"\nport = 19090\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(project_root))
    monkeypatch.delenv("OPENBILICLAW_HOST", raising=False)
    monkeypatch.delenv("OPENBILICLAW_PORT", raising=False)
    monkeypatch.delenv("OPENBILICLAW_SELFTEST", raising=False)
    # These packaging unit tests do not exercise the default four-process
    # backend children; keep them in legacy no-worker mode.
    monkeypatch.setenv("OPENBILICLAW_WORKER", "0")

    class _DummyProc:
        @property
        def pid(self) -> int:
            return 4242

        def poll(self) -> int | None:
            return None

        def terminate(self) -> None:
            pass

        def wait(self, timeout: float | None = None) -> None:
            pass

        def kill(self) -> None:
            pass

    monkeypatch.setattr(
        entry,
        "_spawn_backend_child",
        lambda *_args, **_kwargs: _DummyProc(),
    )
    monkeypatch.setattr(entry.sys, "frozen", False, raising=False)
    monkeypatch.setattr(entry, "_redirect_output_to_logfile", lambda _root: None)
    monkeypatch.setattr(entry, "_notify_starting", lambda: None)
    monkeypatch.setattr(entry, "_migrate_legacy_install_dir_data", lambda *_args: None)
    monkeypatch.setattr(entry, "_inject_bundled_ollama_on_path", lambda _resources: False)
    monkeypatch.setattr(entry, "_packaged_ollama_preflight", lambda: None)
    monkeypatch.setattr(entry, "_ensure_embedding_model_async", lambda: None)
    monkeypatch.setattr(entry, "_close_splash", lambda: None)
    monkeypatch.setattr(entry, "_should_use_tray", lambda: False)
    monkeypatch.setattr(entry.webbrowser, "open", lambda _url: True)

    import uvicorn

    import openbiliclaw.api.app as api_app
    from openbiliclaw.runtime import api_server

    monkeypatch.setattr(api_app, "create_app", lambda: SimpleNamespace())
    fake_listeners = [object(), object()]
    monkeypatch.setattr(
        api_server,
        "create_wildcard_listener_sockets",
        lambda host, port: fake_listeners,
    )
    monkeypatch.setattr(api_server, "close_listener_sockets", lambda listeners: None)
    seen: dict[str, object] = {}

    class _Config:
        def __init__(self, app: object, *, host: str, port: int, log_level: str) -> None:
            seen.update({"app": app, "host": host, "port": port, "log_level": log_level})

    class _Server:
        def __init__(self, config: object) -> None:
            seen["server_config"] = config

        def run(self, **kwargs: object) -> None:
            seen["ran"] = True
            seen.update(kwargs)

    monkeypatch.setattr(uvicorn, "Config", _Config)
    monkeypatch.setattr(uvicorn, "Server", _Server)

    entry.main()

    assert seen["host"] == "0.0.0.0"
    assert seen["port"] == 19090
    assert seen["ran"] is True
    assert seen["sockets"] is fake_listeners


def test_start_packaged_tailnet_uses_runtime_config_and_event_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.runtime import tailnet_supervisor

    expected = object()
    seen: dict[str, object] = {}

    def _start(config: object, port: int, *, event_callback: object) -> object:
        seen.update({"config": config, "port": port, "callback": event_callback})
        return expected

    monkeypatch.setattr(tailnet_supervisor, "start_tailnet_if_enabled", _start)
    runtime_config = SimpleNamespace(
        tailnet=SimpleNamespace(enabled=True),
        api=SimpleNamespace(auth=SimpleNamespace(enabled=True)),
    )

    assert entry._start_packaged_tailnet(runtime_config, "127.0.0.1", 18420) is expected
    assert seen == {
        "config": runtime_config,
        "port": 18420,
        "callback": entry._packaged_tailnet_event_callback,
    }


def test_start_packaged_tailnet_failure_does_not_block_local_desktop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.runtime import tailnet_supervisor

    def _fail(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("helper unavailable")

    monkeypatch.setattr(tailnet_supervisor, "start_tailnet_if_enabled", _fail)
    runtime_config = SimpleNamespace(
        tailnet=SimpleNamespace(enabled=True),
        api=SimpleNamespace(auth=SimpleNamespace(enabled=False)),
    )

    assert entry._start_packaged_tailnet(runtime_config, "0.0.0.0", 8420) is None


def test_main_opens_setup_after_repairing_unloadable_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "userdata"
    project_root.mkdir()
    (project_root / "config.toml").write_bytes(b"\xef\xbb\xbf[api]\nport = 19090\n")
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(project_root))
    monkeypatch.delenv("OPENBILICLAW_HOST", raising=False)
    monkeypatch.delenv("OPENBILICLAW_PORT", raising=False)
    monkeypatch.delenv("OPENBILICLAW_SELFTEST", raising=False)
    monkeypatch.setenv("OPENBILICLAW_WORKER", "0")

    class _DummyProc:
        @property
        def pid(self) -> int:
            return 4242

        def poll(self) -> int | None:
            return None

        def terminate(self) -> None:
            pass

        def wait(self, timeout: float | None = None) -> None:
            pass

        def kill(self) -> None:
            pass

    monkeypatch.setattr(
        entry,
        "_spawn_backend_child",
        lambda *_args, **_kwargs: _DummyProc(),
    )
    monkeypatch.setattr(entry.sys, "frozen", False, raising=False)
    monkeypatch.setattr(entry, "_redirect_output_to_logfile", lambda _root: None)
    monkeypatch.setattr(entry, "_notify_starting", lambda: None)
    monkeypatch.setattr(entry, "_copy_legacy_packaged_user_data", lambda *_args: None)
    monkeypatch.setattr(entry, "_migrate_legacy_install_dir_data", lambda *_args: None)
    monkeypatch.setattr(entry, "_inject_bundled_ollama_on_path", lambda _resources: False)
    monkeypatch.setattr(entry, "_packaged_ollama_preflight", lambda: None)
    monkeypatch.setattr(entry, "_ensure_embedding_model_async", lambda: None)
    monkeypatch.setattr(entry, "_close_splash", lambda: None)
    monkeypatch.setattr(entry, "_should_use_tray", lambda: False)
    opened: list[str] = []
    monkeypatch.setattr(entry.webbrowser, "open", lambda url: opened.append(url) or True)
    # The landing page now opens from a health-gated background thread; run it
    # inline with the probes stubbed so the assertion below stays synchronous.
    monkeypatch.setattr(entry, "_wait_for_backend_ready", lambda base: True)
    monkeypatch.setattr(entry, "_fetch_backend_initialized", lambda base: None)

    class _InlineThread:
        def __init__(self, *, target, args=(), kwargs=None, name=None, daemon=None) -> None:
            self._target = target
            self._args = args
            self._kwargs = kwargs or {}
            self._name = name

        def start(self) -> None:
            # Run the landing-page thread inline, but never execute the child
            # supervisor's watchdog loop (it would block the test forever).
            if self._name == "obc-child-supervisor":
                return
            self._target(*self._args, **self._kwargs)

        def join(self, timeout: float | None = None) -> None:
            pass

    monkeypatch.setattr(entry.threading, "Thread", _InlineThread)

    import uvicorn

    import openbiliclaw.api.app as api_app
    from openbiliclaw.runtime import api_server

    monkeypatch.setattr(api_app, "create_app", lambda: SimpleNamespace())
    fake_listeners = [object(), object()]
    monkeypatch.setattr(
        api_server,
        "create_wildcard_listener_sockets",
        lambda host, port: fake_listeners,
    )
    monkeypatch.setattr(api_server, "close_listener_sockets", lambda listeners: None)

    class _Config:
        def __init__(self, app: object, *, host: str, port: int, log_level: str) -> None:
            self.app = app
            self.host = host
            self.port = port
            self.log_level = log_level

    class _Server:
        def __init__(self, config: object) -> None:
            self.config = config

        def run(self, **kwargs: object) -> None:
            pass

    monkeypatch.setattr(uvicorn, "Config", _Config)
    monkeypatch.setattr(uvicorn, "Server", _Server)

    entry.main()

    assert opened == ["http://127.0.0.1:8420/setup/"]
    assert (project_root / "config.toml.invalid").exists()


def test_main_disables_uvicorn_access_log_in_tray_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "userdata"
    project_root.mkdir()
    (project_root / "config.toml").write_text(
        '[api]\nhost = "127.0.0.1"\nport = 19091\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(project_root))
    monkeypatch.delenv("OPENBILICLAW_HOST", raising=False)
    monkeypatch.delenv("OPENBILICLAW_PORT", raising=False)
    monkeypatch.delenv("OPENBILICLAW_SELFTEST", raising=False)
    monkeypatch.setenv("OPENBILICLAW_WORKER", "0")

    class _DummyProc:
        @property
        def pid(self) -> int:
            return 4242

        def poll(self) -> int | None:
            return None

        def terminate(self) -> None:
            pass

        def wait(self, timeout: float | None = None) -> None:
            pass

        def kill(self) -> None:
            pass

    monkeypatch.setattr(
        entry,
        "_spawn_backend_child",
        lambda *_args, **_kwargs: _DummyProc(),
    )
    monkeypatch.setattr(entry.sys, "frozen", True, raising=False)
    monkeypatch.setattr(entry, "_redirect_output_to_logfile", lambda _root: None)
    monkeypatch.setattr(entry, "_notify_starting", lambda: None)
    migration_calls: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        entry,
        "_migrate_legacy_install_dir_data",
        lambda *args: migration_calls.append(args),
    )
    monkeypatch.setattr(entry, "_inject_bundled_ollama_on_path", lambda _resources: False)
    monkeypatch.setattr(entry, "_packaged_ollama_preflight", lambda: None)
    monkeypatch.setattr(entry, "_ensure_embedding_model_async", lambda: None)
    monkeypatch.setattr(entry, "_should_use_tray", lambda: True)
    monkeypatch.setattr(entry.webbrowser, "open", lambda _url: True)

    import uvicorn

    import openbiliclaw.api.app as api_app

    monkeypatch.setattr(api_app, "create_app", lambda: SimpleNamespace())
    seen: dict[str, object] = {}

    class _Config:
        def __init__(
            self, app: object, *, host: str, port: int, log_level: str, **kwargs: object
        ) -> None:
            seen.update(
                {
                    "app": app,
                    "host": host,
                    "port": port,
                    "log_level": log_level,
                    **kwargs,
                }
            )

    class _Server:
        def __init__(self, config: object) -> None:
            seen["server_config"] = config

    monkeypatch.setattr(uvicorn, "Config", _Config)
    monkeypatch.setattr(uvicorn, "Server", _Server)
    monkeypatch.setattr(entry, "_run_server_in_tray", lambda *_args: seen.update({"tray": True}))

    entry.main()

    assert seen["tray"] is True
    assert seen["access_log"] is False
    assert len(migration_calls) == 1


def test_notify_starting_noop_when_not_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    # Not frozen (tests) → no OS notification subprocess is spawned regardless of
    # platform, so the test suite never pops a real notification.
    calls: list[object] = []
    monkeypatch.setattr(entry.subprocess, "Popen", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(entry.sys, "frozen", False, raising=False)
    entry._notify_starting()
    assert calls == []


def test_notify_starting_noop_on_non_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    # Even frozen, only macOS uses the notification path (Windows has the splash).
    calls: list[object] = []
    monkeypatch.setattr(entry.subprocess, "Popen", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(entry.sys, "frozen", True, raising=False)
    monkeypatch.setattr(entry.sys, "platform", "win32")
    entry._notify_starting()
    assert calls == []


def test_notify_starting_fires_on_frozen_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(entry.subprocess, "Popen", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(entry.sys, "frozen", True, raising=False)
    monkeypatch.setattr(entry.sys, "platform", "darwin")
    entry._notify_starting()
    assert len(calls) == 1
    argv = calls[0][0]
    assert argv[0] == "osascript"
    assert any("OpenBiliClaw" in str(part) for part in argv)


def test_redirect_output_writes_utf8_bom_on_fresh_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A fresh desktop.log gets a UTF-8 BOM so Windows zh-CN viewers detect the
    # encoding instead of guessing GBK and rendering Chinese as mojibake.
    monkeypatch.setattr(entry.sys, "frozen", True, raising=False)
    real_out, real_err = entry.sys.stdout, entry.sys.stderr
    try:
        log_path = entry._redirect_output_to_logfile(tmp_path)
        stream = entry.sys.stdout  # the redirect installed this as stdout
    finally:
        entry.sys.stdout, entry.sys.stderr = real_out, real_err
    assert log_path is not None
    stream.close()
    assert log_path.read_bytes().startswith(b"\xef\xbb\xbf")


def test_redirect_output_no_extra_bom_when_appending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Re-opening an existing (non-empty) log must NOT inject a BOM mid-file.
    monkeypatch.setattr(entry.sys, "frozen", True, raising=False)
    log = tmp_path / "logs" / "desktop.log"
    log.parent.mkdir(parents=True)
    log.write_text("existing line\n", encoding="utf-8")
    real_out, real_err = entry.sys.stdout, entry.sys.stderr
    try:
        entry._redirect_output_to_logfile(tmp_path)
        stream = entry.sys.stdout
    finally:
        entry.sys.stdout, entry.sys.stderr = real_out, real_err
    stream.close()
    data = log.read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf")
    assert b"existing line" in data


# --------------------------------------------------------------------------- #
# Single-instance lock (one running instance per data dir)
# --------------------------------------------------------------------------- #


def test_single_instance_lock_blocks_second_acquire(tmp_path: Path) -> None:
    # First launch wins the lock; a second launch on the same data dir is "busy"
    # (the OS treats the two open handles as independent even in one process), and
    # the slot frees once the first handle closes (== the owning process exits).
    status1, handle1 = entry._try_single_instance_lock(tmp_path)
    assert status1 == "acquired"
    assert handle1 is not None

    status2, handle2 = entry._try_single_instance_lock(tmp_path)
    assert status2 == "busy"
    assert handle2 is None

    handle1.close()  # owning instance exits → lock released

    status3, handle3 = entry._try_single_instance_lock(tmp_path)
    assert status3 == "acquired"
    handle3.close()


def test_single_instance_lock_separate_dirs_both_acquire(tmp_path: Path) -> None:
    # Different data dirs (portable installs / OPENBILICLAW_PROJECT_ROOT) may run
    # side by side — the lock is per data dir.
    root_a = tmp_path / "a"
    root_a.mkdir()
    root_b = tmp_path / "b"
    root_b.mkdir()

    status_a, handle_a = entry._try_single_instance_lock(root_a)
    status_b, handle_b = entry._try_single_instance_lock(root_b)

    assert status_a == "acquired"
    assert status_b == "acquired"
    handle_a.close()
    handle_b.close()


# --------------------------------------------------------------------------- #
# _view_runtime_logs — macOS "查看运行日志" tray action
#
# The old `osascript … tell application "Terminal"` needed Apple-Events
# automation permission an unsigned packaged .app is denied, so the menu item
# silently did nothing. The fix opens a .command as a document (no permission)
# and checks the return code so it can fall back to the default app.
# --------------------------------------------------------------------------- #


def test_view_runtime_logs_windows_opens_log_without_console(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(entry.os, "name", "nt")
    monkeypatch.setattr(entry.sys, "platform", "win32")
    spawned: list[object] = []
    opened: list[Path] = []
    monkeypatch.setattr(entry.subprocess, "Popen", lambda *a, **k: spawned.append((a, k)))
    monkeypatch.setattr(entry, "_open_in_default_app", lambda p: opened.append(p))
    log = tmp_path / "logs" / "desktop.log"
    log.parent.mkdir(parents=True)
    log.write_text("hi", encoding="utf-8")

    entry._view_runtime_logs(log)

    assert spawned == []
    assert opened == [log]


def test_view_runtime_logs_macos_opens_terminal_with_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(entry.os, "name", "posix")
    monkeypatch.setattr(entry.sys, "platform", "darwin")
    calls: list[list[str]] = []

    class _Result:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(
        entry.subprocess, "run", lambda cmd, *a, **k: (calls.append(cmd), _Result())[1]
    )
    log = tmp_path / "logs" / "desktop.log"
    log.parent.mkdir(parents=True)
    log.write_text("hi", encoding="utf-8")

    entry._view_runtime_logs(log)

    helper = tmp_path / "logs" / "view-logs.command"
    assert helper.exists()
    body = helper.read_text(encoding="utf-8")
    assert 'tail -n 200 -f "' in body and str(log) in body
    # Launched as a document via `open -a Terminal` (no osascript / Apple Events).
    assert calls and calls[0][:3] == ["open", "-a", "Terminal"]
    assert calls[0][-1] == str(helper)


def test_view_runtime_logs_macos_falls_back_when_terminal_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(entry.os, "name", "posix")
    monkeypatch.setattr(entry.sys, "platform", "darwin")

    class _Result:
        returncode = 1
        stderr = "boom"

    monkeypatch.setattr(entry.subprocess, "run", lambda *a, **k: _Result())
    opened: list[Path] = []
    monkeypatch.setattr(entry, "_open_in_default_app", lambda p: opened.append(p))
    log = tmp_path / "logs" / "desktop.log"
    log.parent.mkdir(parents=True)
    log.write_text("hi", encoding="utf-8")

    entry._view_runtime_logs(log)

    # Non-zero return code → fall back to opening the file in the default app.
    assert opened == [log]


# --------------------------------------------------------------------------- #
# Landing-page decision + health-gated browser open
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("seeded", "repaired", "initialized", "expected"),
    [
        (True, False, None, "/setup/"),
        (False, True, None, "/setup/"),
        (True, False, True, "/setup/"),
        # Configured relaunch that never completed init → wizard, not /web.
        (False, False, False, "/setup/"),
        (False, False, True, "/web/"),
        # Unknown init state (probe failed) keeps the /web fallback.
        (False, False, None, "/web/"),
    ],
)
def test_decide_landing_path(
    seeded: bool, repaired: bool, initialized: bool | None, expected: str
) -> None:
    assert entry._decide_landing_path(seeded, repaired, initialized) == expected


def test_fetch_backend_initialized_parses_bool_and_swallows_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import io

    class _Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            self.close()

    # The helpers go through _LOOPBACK_OPENER (proxy-env-immune), so stub the
    # opener's open method rather than urllib.request.urlopen.
    def _open_ok(url: str, timeout: float = 0):
        assert url.endswith("/api/init-status")
        return _Response(b'{"initialized": false, "running": false}')

    monkeypatch.setattr(entry._LOOPBACK_OPENER, "open", _open_ok)
    assert entry._fetch_backend_initialized("http://127.0.0.1:8420") is False

    def _open_boom(url: str, timeout: float = 0):
        raise OSError("refused")

    monkeypatch.setattr(entry._LOOPBACK_OPENER, "open", _open_boom)
    assert entry._fetch_backend_initialized("http://127.0.0.1:8420") is None

    def _open_garbage(url: str, timeout: float = 0):
        return _Response(b'{"initialized": "yes"}')

    monkeypatch.setattr(entry._LOOPBACK_OPENER, "open", _open_garbage)
    assert entry._fetch_backend_initialized("http://127.0.0.1:8420") is None


def test_open_landing_page_waits_for_health_then_routes_uninitialized_to_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(entry, "_wait_for_backend_ready", lambda base: True)
    monkeypatch.setattr(entry, "_fetch_backend_initialized", lambda base: False)
    opened: list[str] = []
    monkeypatch.setattr(entry.webbrowser, "open", lambda url: opened.append(url))

    entry._open_landing_page_when_ready("http://127.0.0.1:8420", seeded=False, repaired=False)

    assert opened == ["http://127.0.0.1:8420/setup/"]


def test_open_landing_page_still_opens_web_on_health_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(entry, "_wait_for_backend_ready", lambda base: False)
    probed: list[str] = []
    monkeypatch.setattr(entry, "_fetch_backend_initialized", lambda base: probed.append(base))
    opened: list[str] = []
    monkeypatch.setattr(entry.webbrowser, "open", lambda url: opened.append(url))

    entry._open_landing_page_when_ready("http://127.0.0.1:8420", seeded=False, repaired=False)

    # Timeout → best-effort open of /web without an init-status probe.
    assert opened == ["http://127.0.0.1:8420/web/"]
    assert probed == []


# --------------------------------------------------------------------------- #
# Installer-coordination mutex (Inno Setup AppMutex)
# --------------------------------------------------------------------------- #


def test_installer_mutex_name_matches_inno_appmutex() -> None:
    """entry.py's mutex name and the .iss AppMutex= must stay in lockstep."""
    project_root = Path(__file__).resolve().parent.parent
    iss_text = (project_root / "packaging" / "openbiliclaw.iss").read_text(encoding="utf-8")
    match = re.search(r"^AppMutex=(\S+)", iss_text, flags=re.MULTILINE)
    assert match is not None, "packaging/openbiliclaw.iss must set AppMutex="
    assert match.group(1) == entry._INSTALLER_MUTEX_NAME


def test_acquire_installer_mutex_skips_dev_and_non_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Dev runs (not frozen) never take the mutex, even on Windows.
    assert entry._acquire_installer_mutex() is None
    # Frozen but non-Windows: still None.
    monkeypatch.setattr(entry.sys, "frozen", True, raising=False)
    monkeypatch.setattr(entry.os, "name", "posix")
    assert entry._acquire_installer_mutex() is None


@pytest.mark.skipif(os.name != "nt", reason="named mutex is Windows-only")
def test_acquire_installer_mutex_frozen_windows_handles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(entry.sys, "frozen", True, raising=False)
    assert entry._acquire_installer_mutex(), "CreateMutexW must return a handle"
    # ERROR_ALREADY_EXISTS still yields a handle — parent + worker children
    # share one mutex; existence is all that matters.
    assert entry._acquire_installer_mutex()


@pytest.mark.skipif(os.name != "nt", reason="named mutex is Windows-only")
def test_acquire_installer_mutex_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    import ctypes

    def _boom(*_args: object, **_kwargs: object) -> int:
        raise OSError("CreateMutexW unavailable")

    monkeypatch.setattr(entry.sys, "frozen", True, raising=False)
    monkeypatch.setattr(ctypes.windll.kernel32, "CreateMutexW", _boom)
    assert entry._acquire_installer_mutex() is None


if __name__ == "__main__":  # pragma: no cover - convenience
    raise SystemExit(pytest.main([__file__, "-q"]))
