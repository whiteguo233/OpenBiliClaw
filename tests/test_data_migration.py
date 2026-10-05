from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

import openbiliclaw.storage.migration as migration
from openbiliclaw.config import Config, load_config, save_config
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from collections.abc import Callable


def _clear_openbiliclaw_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(os.environ):
        if name.startswith("OPENBILICLAW_"):
            monkeypatch.delenv(name, raising=False)


def _write_file(path: Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


def _source_config(root: Path) -> Config:
    config = Config(data_dir=str(root / "data"))
    config.language = "en"
    config.llm.deepseek.api_key = "source-deepseek-key"
    config.llm.deepseek.model = "deepseek-chat"
    config.bilibili.cookie = "source-base-cookie"
    config.bilibili.proxy = "http://source-machine-proxy:8080"
    config.bilibili.browser_executable = "/Applications/Source Browser.app/Contents/MacOS/browser"
    config.scheduler.discovery_limit = 42
    config.storage.db_path = "source-machine/custom.sqlite3"
    config.api.host = "0.0.0.0"
    config.api.port = 18420
    config.api.auth.enabled = True
    config.api.auth.password_hash = "source-api-password-hash-must-not-export"
    config.api.auth.session_secret = "source-session-secret-which-must-not-survive"
    config.api.auth.session_ttl_hours = 12
    config.api.auth.trust_loopback = True
    config.api.auth.trusted_proxies = ["10.0.0.1"]
    config.api.auth.allowed_bearer_origins = ["https://source.example"]
    config.api.auth.extension_access_enabled = True
    config.api.auth.extension_access_keys = ["source-extension-pairing"]
    config.api.auth.extension_token_ttl_hours = 12
    config.logging.directory = "source-logs"
    config.logging.filename = "source.log"
    config.network.mode = "direct"
    config.tls_proxy.enabled = True
    config.tls_proxy.port = 10443
    config.tls_proxy.cert_dir = "source-certs"
    config.tls_proxy.san_names = ["source.example"]
    config.tailnet.enabled = True
    config.tailnet.hostname = "source-openbiliclaw-host"
    config.autostart.enabled = True
    config.autostart.manage_ollama = True
    config.sources.browser_cdp_url = "http://127.0.0.1:19222"
    save_config(config, root / "config.toml", autostart_authoritative=True)
    _write_file(
        root / "config.local.toml",
        '[bilibili]\ncookie = "source-local-cookie"\n',
    )
    return config


def _populate_source_data(config: Config) -> sqlite3.Connection:
    data_dir = config.data_path
    data_dir.mkdir(parents=True, exist_ok=True)
    initialized = Database(data_dir / "openbiliclaw.db")
    initialized.initialize()
    initialized.close()
    connection = sqlite3.connect(data_dir / "openbiliclaw.db")
    assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.execute("CREATE TABLE portable_records (value TEXT NOT NULL)")
    connection.commit()
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.execute("INSERT INTO portable_records VALUES ('committed-in-wal')")
    connection.commit()

    _write_file(data_dir / "memory" / "preference.json", '{"topic":"music"}\n')
    _write_file(data_dir / "bilibili_cookie.json", '{"SESSDATA":"portable-cookie"}\n')
    _write_file(data_dir / "image-cache" / "signed" / "cover.webp", b"signed-cover")

    _write_file(data_dir / "backups" / "historical.db", b"derived-backup")
    _write_file(data_dir / "cache" / "scratch.bin", b"derived-cache")
    _write_file(data_dir / "eval" / "report.json", "{}")
    _write_file(data_dir / "autostart" / "source.unit", "source service")
    _write_file(data_dir / "certs" / "source.pem", "source certificate")
    _write_file(data_dir / "tailnet" / "source-identity.json", '{"node":"source"}\n')
    source_helper = data_dir / "bin" / migration._TAILNET_HELPER_BASENAME
    _write_file(source_helper, b"source helper must never migrate")
    if os.name != "nt":
        source_helper.chmod(0o700)
    _write_file(data_dir / "embedding_cache.db", b"derived-embedding-cache")
    _write_file(data_dir / "unfinished.part", b"partial")
    _write_file(config.data_path.parent / "logs" / "runtime.log", "runtime log")
    return connection


def _export_source(
    root: Path,
    *,
    frontend_settings: dict[str, object] | None = None,
) -> tuple[migration.MigrationExport, sqlite3.Connection]:
    config = _source_config(root)
    connection = _populate_source_data(config)
    exported = migration.create_migration_archive(
        config,
        frontend_settings=frontend_settings,
        project_root=root,
    )
    return exported, connection


def _target_config(root: Path) -> Config:
    config = Config(data_dir=str(root / "target-data"))
    config.llm.deepseek.api_key = "target-deepseek-key"
    config.llm.deepseek.model = "deepseek-chat"
    config.bilibili.cookie = "target-cookie"
    config.bilibili.proxy = "http://target-machine-proxy:8080"
    config.bilibili.browser_executable = r"C:\Program Files\Target Browser\browser.exe"
    config.storage.db_path = "target-machine/local.sqlite3"
    config.api.host = "127.0.0.1"
    config.api.port = 29420
    config.api.auth.enabled = True
    config.api.auth.password_hash = "target-api-password-hash"
    config.api.auth.session_secret = "target-session-secret-which-must-also-be-revoked"
    config.api.auth.session_ttl_hours = 36
    config.api.auth.trust_loopback = False
    config.api.auth.trusted_proxies = ["127.0.0.1"]
    config.api.auth.allowed_bearer_origins = ["https://target.example"]
    config.api.auth.extension_access_enabled = True
    config.api.auth.extension_access_keys = ["target-extension-pairing"]
    config.api.auth.extension_token_ttl_hours = 48
    config.logging.directory = "target-logs"
    config.logging.filename = "target.log"
    config.network.mode = "custom"
    config.network.proxy = "socks5://127.0.0.1:1080"
    config.tls_proxy.enabled = False
    config.tls_proxy.port = 20443
    config.tls_proxy.cert_dir = str(config.data_path / "certs")
    config.tls_proxy.san_names = ["target.example"]
    config.tailnet.enabled = False
    config.tailnet.hostname = "target-openbiliclaw-host"
    config.autostart.enabled = False
    config.autostart.manage_ollama = False
    config.sources.browser_cdp_url = "http://127.0.0.1:29222"
    save_config(config, root / "config.toml", autostart_authoritative=True)
    _write_file(root / "config.local.toml", '[bilibili]\ncookie = "target-local-cookie"\n')
    return config


def _populate_target_data(config: Config) -> None:
    data_dir = config.data_path
    data_dir.mkdir(parents=True, exist_ok=True)
    initialized = Database(data_dir / "openbiliclaw.db")
    initialized.initialize()
    initialized.close()
    with sqlite3.connect(data_dir / "openbiliclaw.db") as connection:
        connection.execute("CREATE TABLE target_records (value TEXT NOT NULL)")
        connection.execute("INSERT INTO target_records VALUES ('old-target-row')")
    _write_file(data_dir / "old-only.txt", "old target data")
    _write_file(data_dir / "certs" / "device.pem", "target certificate")
    _write_file(data_dir / "autostart" / "openbiliclaw.service", "target service")
    _write_file(data_dir / "tailnet" / "target-identity.json", '{"node":"target"}\n')
    target_helper = data_dir / "bin" / migration._TAILNET_HELPER_BASENAME
    _write_file(target_helper, b"trusted target helper")
    if os.name != "nt":
        target_helper.chmod(0o700)


def _rewrite_manifest(
    source: Path,
    destination: Path,
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    with zipfile.ZipFile(source) as archive:
        entries = [(info, archive.read(info)) for info in archive.infolist()]
    payloads = {info.filename: payload for info, payload in entries}
    manifest = json.loads(payloads["manifest.json"])
    assert isinstance(manifest, dict)
    mutate(manifest)
    payloads["manifest.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for info, _payload in entries:
            archive.writestr(info, payloads[info.filename])


def _append_archive_entry(source: Path, destination: Path, name: str, payload: bytes) -> None:
    with zipfile.ZipFile(source) as archive:
        entries = [(info, archive.read(info)) for info in archive.infolist()]
    payloads = {info.filename: value for info, value in entries}
    manifest = json.loads(payloads["manifest.json"])
    manifest["entries"].append(
        {
            "kind": "data",
            "path": name,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
        }
    )
    payloads["manifest.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for info, _value in entries:
            archive.writestr(info, payloads[info.filename])
        added = zipfile.ZipInfo(name)
        added.create_system = 3
        added.external_attr = (stat.S_IFREG | 0o600) << 16
        archive.writestr(added, payload)


def test_portable_data_scan_excludes_case_variants_of_machine_roots(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_file(data_dir / "TaIlNeT" / "identity.key", "must not migrate")
    _write_file(data_dir / "BIN" / "helper", "must not migrate")
    _write_file(data_dir / "portable" / "preference.json", "{}")

    portable = {
        relative.as_posix() for _source, relative in migration._iter_portable_data_files(data_dir)
    }

    assert portable == {"portable/preference.json"}


def test_preserve_target_helper_rejects_symlink(tmp_path: Path) -> None:
    target_data = tmp_path / "target"
    prepared_data = tmp_path / "prepared"
    target_bin = target_data / "bin"
    target_bin.mkdir(parents=True)
    prepared_data.mkdir()
    outside = tmp_path / "outside-helper"
    outside.write_bytes(b"untrusted")
    try:
        (target_bin / migration._TAILNET_HELPER_BASENAME).symlink_to(outside)
    except OSError as exc:  # pragma: no cover - Windows policy dependent
        pytest.skip(f"file symlinks unavailable: {exc}")

    migration._preserve_target_machine_data(target_data, prepared_data)

    assert not (prepared_data / "bin").exists()


@pytest.mark.skipif(os.name == "nt", reason="Windows does not use executable mode bits")
def test_preserve_target_helper_rejects_non_executable_file(tmp_path: Path) -> None:
    target_data = tmp_path / "target"
    prepared_data = tmp_path / "prepared"
    helper = target_data / "bin" / migration._TAILNET_HELPER_BASENAME
    _write_file(helper, b"not an executable helper")
    helper.chmod(0o600)
    prepared_data.mkdir()

    preserved = migration._preserve_target_machine_data(target_data, prepared_data)

    assert preserved is False
    assert not (prepared_data / "bin").exists()


@pytest.mark.parametrize("preserved_root", migration._PRESERVED_TARGET_ROOTS)
def test_preserve_target_machine_data_rejects_nested_symlink(
    tmp_path: Path,
    preserved_root: str,
) -> None:
    target_data = tmp_path / "target"
    prepared_data = tmp_path / "prepared"
    nested = target_data / preserved_root / "nested"
    nested.mkdir(parents=True)
    prepared_data.mkdir()
    outside = tmp_path / f"outside-{preserved_root}.txt"
    outside.write_text("must not be copied", encoding="utf-8")
    try:
        (nested / "escape").symlink_to(outside)
    except OSError as exc:  # pragma: no cover - Windows policy dependent
        pytest.skip(f"file symlinks unavailable: {exc}")

    with pytest.raises(migration.MigrationError) as raised:
        migration._preserve_target_machine_data(target_data, prepared_data)

    assert raised.value.code == "unsafe_target_state"
    assert not (prepared_data / preserved_root).exists()


def test_export_snapshots_committed_wal_and_enforces_portable_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_openbiliclaw_environment(monkeypatch)
    monkeypatch.setenv("OPENBILICLAW_TEST_EXPORT_SECRET", "must-not-be-exported")
    monkeypatch.setenv("GOOGLE_API_KEY", "provider-secret-must-not-be-exported")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy-secret-must-not-be-exported")
    source_root = tmp_path / "source"
    exported, source_connection = _export_source(
        source_root,
        frontend_settings={
            "theme_mode": "dark",
            "theme_hue": 312,
            "auto_load_on_scroll": True,
            "backend_host": "https://must-not-migrate.example",
            "session_token": "must-not-migrate",
        },
    )
    try:
        assert not (exported.path.parent / "snapshot").exists()
        wal_path = source_root / "data" / "openbiliclaw.db-wal"
        assert wal_path.is_file()
        assert wal_path.stat().st_size > 0

        with zipfile.ZipFile(exported.path) as archive:
            names = set(archive.namelist())
            manifest = json.loads(archive.read("manifest.json"))
            frontend = json.loads(archive.read("frontend/settings.json"))
            portable_config_toml = archive.read("config/config.toml").decode("utf-8")
            unpacked_archive = b"\n".join(
                archive.read(name) for name in archive.namelist() if not name.endswith("/")
            )
            snapshot_path = tmp_path / "exported.sqlite3"
            snapshot_path.write_bytes(archive.read("data/openbiliclaw.db"))

        assert {
            "manifest.json",
            "config/config.toml",
            "data/openbiliclaw.db",
            "data/memory/preference.json",
            "data/bilibili_cookie.json",
            "data/image-cache/signed/cover.webp",
            "frontend/settings.json",
        } <= names
        assert "config/config.local.toml" not in names
        assert not any(
            name.startswith(
                (
                    "data/backups/",
                    "data/cache/",
                    "data/eval/",
                    "data/autostart/",
                    "data/certs/",
                    "data/bin/",
                    "data/tailnet/",
                )
            )
            for name in names
        )
        assert "data/embedding_cache.db" not in names
        assert "data/openbiliclaw.db-wal" not in names
        assert "data/unfinished.part" not in names
        assert not any(name.startswith("logs/") for name in names)

        with sqlite3.connect(snapshot_path) as snapshot:
            assert snapshot.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert snapshot.execute("SELECT value FROM portable_records").fetchall() == [
                ("committed-in-wal",)
            ]

        assert frontend == {
            "auto_load_on_scroll": True,
            "theme_hue": 312,
            "theme_mode": "dark",
        }
        assert manifest["contains_secrets"] is True
        assert manifest["encrypted"] is False
        assert "OPENBILICLAW_TEST_EXPORT_SECRET" in manifest["source_omitted_environment_variables"]
        assert "GOOGLE_API_KEY" in manifest["source_omitted_environment_variables"]
        assert "HTTPS_PROXY" in manifest["source_omitted_environment_variables"]
        assert b"provider-secret-must-not-be-exported" not in unpacked_archive
        assert b"proxy-secret-must-not-be-exported" not in unpacked_archive
        assert b"source-api-password-hash-must-not-export" not in unpacked_archive
        assert b"source-session-secret-which-must-not-survive" not in unpacked_archive
        assert b"source-extension-pairing" not in unpacked_archive
        assert "[api.auth]" not in portable_config_toml
        assert "password_hash" not in portable_config_toml
        assert "session_secret" not in portable_config_toml
        assert "extension_access_keys" not in portable_config_toml
        assert "must-not-be-exported" not in exported.path.read_bytes().decode(
            "latin-1", errors="ignore"
        )
        assert {
            "logs",
            "data/backups",
            "data/embedding_cache.db",
            "data/cache",
            "data/eval",
            "data/certs",
            "data/autostart",
            "data/bin",
            "data/tailnet",
            "browser-and-extension-sessions",
            "external-cli-credentials",
            "environment-variable-values",
        } <= set(manifest["omitted"])
    finally:
        source_connection.close()
        shutil.rmtree(exported.path.parent, ignore_errors=True)


def test_import_preserves_target_machine_fields_and_certificates_and_rotates_sessions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_openbiliclaw_environment(monkeypatch)
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    exported, source_connection = _export_source(
        source_root,
        frontend_settings={"theme_mode": "light", "side_drawer_open": True},
    )
    source_connection.close()
    target = _target_config(target_root)
    _populate_target_data(target)
    old_target_session = target.api.auth.session_secret
    try:
        staged = migration.stage_migration_archive(
            exported.path,
            target,
            project_root=target_root,
        )

        assert staged.restart_required is True
        assert staged.frontend_settings == {
            "side_drawer_open": True,
            "theme_mode": "light",
        }
        assert "api.auth.session_secret" in staged.adjusted_fields
        assert (target.data_path / "old-only.txt").read_text(encoding="utf-8") == (
            "old target data"
        )

        result = migration.apply_pending_migration(project_root=target_root)

        assert result is not None
        assert result.state == "applied"
        restored = load_config(target_root / "config.toml")
        assert restored.language == "en"
        assert restored.llm.deepseek.api_key == "source-deepseek-key"
        assert restored.bilibili.cookie == "source-local-cookie"
        assert restored.scheduler.discovery_limit == 42

        assert restored.data_dir == target.data_dir
        assert restored.storage == target.storage
        assert restored.api.host == target.api.host
        assert restored.api.port == target.api.port
        assert restored.logging.directory == target.logging.directory
        assert restored.logging.filename == target.logging.filename
        assert restored.network == target.network
        assert restored.tls_proxy == target.tls_proxy
        assert restored.tailnet == target.tailnet
        assert restored.autostart == target.autostart
        assert restored.sources.browser_cdp_url == target.sources.browser_cdp_url
        assert restored.bilibili.proxy == target.bilibili.proxy
        assert restored.bilibili.browser_executable == target.bilibili.browser_executable

        assert restored.api.auth.enabled is target.api.auth.enabled
        assert restored.api.auth.password_hash == target.api.auth.password_hash
        assert restored.api.auth.session_ttl_hours == target.api.auth.session_ttl_hours
        assert restored.api.auth.trust_loopback is target.api.auth.trust_loopback
        assert restored.api.auth.trusted_proxies == target.api.auth.trusted_proxies
        assert restored.api.auth.allowed_bearer_origins == target.api.auth.allowed_bearer_origins
        assert (
            restored.api.auth.extension_token_ttl_hours == target.api.auth.extension_token_ttl_hours
        )
        assert restored.api.auth.session_secret
        assert restored.api.auth.session_secret != old_target_session
        assert restored.api.auth.session_secret != "source-session-secret-which-must-not-survive"
        assert restored.api.auth.extension_access_enabled is False
        assert restored.api.auth.extension_access_keys == []

        assert (target.data_path / "memory" / "preference.json").is_file()
        assert (target.data_path / "image-cache" / "signed" / "cover.webp").read_bytes() == (
            b"signed-cover"
        )
        assert (target.data_path / "certs" / "device.pem").read_text(encoding="utf-8") == (
            "target certificate"
        )
        assert (target.data_path / "autostart" / "openbiliclaw.service").read_text(
            encoding="utf-8"
        ) == "target service"
        assert (target.data_path / "tailnet" / "target-identity.json").read_text(
            encoding="utf-8"
        ) == '{"node":"target"}\n'
        assert not (target.data_path / "certs" / "source.pem").exists()
        assert not (target.data_path / "tailnet" / "source-identity.json").exists()
        restored_helper = target.data_path / "bin" / migration._TAILNET_HELPER_BASENAME
        assert restored_helper.read_bytes() == b"trusted target helper"
        if os.name != "nt":
            assert restored_helper.stat().st_mode & 0o777 == 0o700

        with sqlite3.connect(target.data_path / "openbiliclaw.db") as connection:
            assert connection.execute("SELECT value FROM portable_records").fetchall() == [
                ("committed-in-wal",)
            ]
            auth_epoch = connection.execute(
                "SELECT value FROM auth_state WHERE key = 'auth_epoch'"
            ).fetchone()
            assert auth_epoch is not None and int(auth_epoch[0]) > 0
            assert (
                connection.execute(
                    "SELECT value FROM auth_state WHERE key = 'password_fingerprint'"
                ).fetchone()
                is None
            )

        assert Path(result.config_backup).is_file()
        assert result.frontend_settings == {
            "side_drawer_open": True,
            "theme_mode": "light",
        }
        data_backup = Path(result.data_backup)
        assert (data_backup / "old-only.txt").read_text(encoding="utf-8") == "old target data"
        assert len(list(target_root.glob("config.local.toml.pre-import-*.bak"))) == 1
        assert not (target_root / ".openbiliclaw-migration" / "pending.json").exists()
        status = migration.migration_status(project_root=target_root)
        assert status["state"] == "applied"
        assert status["frontend"] == result.frontend_settings
    finally:
        shutil.rmtree(exported.path.parent, ignore_errors=True)


def test_stage_rejects_bad_hash_and_zip_path_traversal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_openbiliclaw_environment(monkeypatch)
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    exported, source_connection = _export_source(source_root)
    source_connection.close()
    target = _target_config(target_root)
    try:
        bad_hash = tmp_path / "bad-hash.obcbackup"

        def corrupt_hash(manifest: dict[str, Any]) -> None:
            entries = manifest["entries"]
            assert isinstance(entries, list)
            entry = next(item for item in entries if item["path"] == "frontend/settings.json")
            entry["sha256"] = "0" * 64

        _rewrite_manifest(exported.path, bad_hash, corrupt_hash)
        with pytest.raises(migration.MigrationError) as checksum_error:
            migration.stage_migration_archive(bad_hash, target, project_root=target_root)
        assert checksum_error.value.code == "checksum_mismatch"

        traversal = tmp_path / "path-traversal.obcbackup"
        shutil.copyfile(exported.path, traversal)
        with zipfile.ZipFile(traversal, "a") as archive:
            archive.writestr("../escaped.txt", b"escape attempt")
        with pytest.raises(migration.MigrationError) as traversal_error:
            migration.stage_migration_archive(traversal, target, project_root=target_root)
        assert traversal_error.value.code == "unsafe_path"
        assert not (tmp_path / "escaped.txt").exists()
        assert not (target_root / ".openbiliclaw-migration" / "pending.json").exists()
    finally:
        shutil.rmtree(exported.path.parent, ignore_errors=True)


def test_stage_rejects_case_variants_of_machine_local_data_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_openbiliclaw_environment(monkeypatch)
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    exported, source_connection = _export_source(source_root)
    source_connection.close()
    target = _target_config(target_root)
    try:
        for index, name in enumerate(("data/TaIlNeT/node.key", "data/BIN/helper")):
            malicious = tmp_path / f"case-variant-{index}.obcbackup"
            _append_archive_entry(exported.path, malicious, name, b"must be rejected")

            with pytest.raises(migration.MigrationError) as error:
                migration.stage_migration_archive(malicious, target, project_root=target_root)

            assert error.value.code == "disallowed_entry"
            assert not (target_root / ".openbiliclaw-migration" / "pending.json").exists()
    finally:
        shutil.rmtree(exported.path.parent, ignore_errors=True)


def test_stage_accepts_legacy_environment_manifest_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_openbiliclaw_environment(monkeypatch)
    monkeypatch.setenv("OPENBILICLAW_LEGACY_SOURCE_ONLY", "not-in-archive")
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    exported, source_connection = _export_source(source_root)
    source_connection.close()
    target = _target_config(target_root)
    legacy_archive = tmp_path / "legacy-env-key.obcbackup"

    def use_legacy_environment_key(manifest: dict[str, Any]) -> None:
        manifest["active_environment_variables"] = manifest.pop(
            "source_omitted_environment_variables"
        )

    try:
        _rewrite_manifest(exported.path, legacy_archive, use_legacy_environment_key)
        staged = migration.stage_migration_archive(
            legacy_archive,
            target,
            project_root=target_root,
        )
        assert "OPENBILICLAW_LEGACY_SOURCE_ONLY" in (staged.source_omitted_environment_variables)
    finally:
        shutil.rmtree(exported.path.parent, ignore_errors=True)


def test_migration_rejects_project_code_and_unrelated_nonempty_data_directories(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    unrelated = tmp_path / "documents"
    _write_file(unrelated / "personal.txt", "not OpenBiliClaw data")
    weak_memory_sentinel = tmp_path / "documents-with-memory"
    _write_file(weak_memory_sentinel / "memory" / "notes.json", "{}")
    weak_certs_sentinel = tmp_path / "documents-with-certs"
    _write_file(weak_certs_sentinel / "certs" / "personal.pem", "private")

    for unsafe_data_dir in (
        project_root,
        project_root / "src",
        project_root / ".openbiliclaw-migration",
        unrelated,
        weak_memory_sentinel,
        weak_certs_sentinel,
    ):
        config = Config(data_dir=str(unsafe_data_dir))
        with pytest.raises(migration.MigrationError) as error:
            migration.create_migration_archive(config, project_root=project_root)
        assert error.value.code == "unsafe_data_dir"

    upload = tmp_path / "placeholder.obcbackup"
    upload.write_bytes(b"not reached")
    unsafe_target = Config(data_dir=str(project_root))
    with pytest.raises(migration.MigrationError) as error:
        migration.stage_migration_archive(upload, unsafe_target, project_root=project_root)
    assert error.value.code == "unsafe_data_dir"


@pytest.mark.parametrize(
    "generated_path",
    [
        "custom-user-state.pre-import-abcdef123456.bak/old.txt",
        "custom-user-state.failed-import-abcdef123456/new.txt",
        ".custom-user-state.import-abcdef123456/prepared.txt",
    ],
)
def test_gitignore_covers_custom_data_directory_migration_artifacts(
    generated_path: str,
) -> None:
    repository = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["git", "check-ignore", "--quiet", "--no-index", generated_path],
        cwd=repository,
        check=False,
    )
    assert result.returncode == 0, f"migration artifact is not ignored: {generated_path}"


def test_apply_replace_failure_rolls_back_all_active_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_openbiliclaw_environment(monkeypatch)
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    exported, source_connection = _export_source(source_root)
    source_connection.close()
    target = _target_config(target_root)
    _populate_target_data(target)
    old_config = (target_root / "config.toml").read_bytes()
    old_local = (target_root / "config.local.toml").read_bytes()
    migration.stage_migration_archive(exported.path, target, project_root=target_root)

    real_replace = os.replace
    target_config_path = (target_root / "config.toml").resolve()

    def fail_new_config_activation(source: str | Path, destination: str | Path) -> None:
        source_path = Path(source)
        destination_path = Path(destination)
        if destination_path.resolve() == target_config_path and source_path.name.startswith(
            ".config.toml.import-"
        ):
            raise OSError("injected config activation failure")
        real_replace(source, destination)

    monkeypatch.setattr(migration.os, "replace", fail_new_config_activation)
    try:
        result = migration.apply_pending_migration(project_root=target_root)

        assert result is not None
        assert result.state == "failed"
        assert "已恢复原数据" in result.message
        assert (target_root / "config.toml").read_bytes() == old_config
        assert (target_root / "config.local.toml").read_bytes() == old_local
        assert (target.data_path / "old-only.txt").read_text(encoding="utf-8") == (
            "old target data"
        )
        with sqlite3.connect(target.data_path / "openbiliclaw.db") as connection:
            assert connection.execute("SELECT value FROM target_records").fetchall() == [
                ("old-target-row",)
            ]
        migration_root = target_root / ".openbiliclaw-migration"
        assert not (migration_root / "pending.json").exists()
        assert not (migration_root / "apply-journal.json").exists()
        assert migration.migration_status(project_root=target_root)["state"] == "failed"
    finally:
        shutil.rmtree(exported.path.parent, ignore_errors=True)


def test_committed_apply_journal_is_finalized_without_rolling_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_openbiliclaw_environment(monkeypatch)
    root = tmp_path / "target"
    target = _target_config(root)
    _populate_target_data(target)
    with sqlite3.connect(target.data_path / "openbiliclaw.db") as connection:
        connection.execute(
            "INSERT OR REPLACE INTO auth_state (key, value) VALUES ('auth_epoch', '1')"
        )
    active_config = (root / "config.toml").read_bytes()
    migration_id = "a" * 32
    migration_root = root / ".openbiliclaw-migration"
    stage_dir = migration_root / f"pending-{migration_id}"
    _write_file(stage_dir / "staged.txt", "staged")
    config_backup = root / "config.toml.pre-import-aaaaaaaaaaaa.bak"
    local_backup = root / "config.local.toml.pre-import-aaaaaaaaaaaa.bak"
    data_backup = root / "target-data.pre-import-aaaaaaaaaaaa.bak"
    prepared_config = root / ".config.toml.import-aaaaaaaaaaaa"
    prepared_data = root / ".target-data.import-aaaaaaaaaaaa"
    os.replace(root / "config.local.toml", local_backup)
    applied_at = "2026-08-09T12:00:00+00:00"
    _write_file(
        migration_root / "pending.json",
        json.dumps(
            {
                "migration_id": migration_id,
                "source_version": "0.3.200",
                "stage_dir": stage_dir.name,
            }
        ),
    )
    _write_file(
        migration_root / "apply-journal.json",
        json.dumps(
            {
                "state": "committed",
                "migration_id": migration_id,
                "source_version": "0.3.200",
                "applied_at": applied_at,
                "target_config": str((root / "config.toml").resolve()),
                "target_local": str((root / "config.local.toml").resolve()),
                "target_data": str(target.data_path.resolve()),
                "prepared_config": str(prepared_config.resolve()),
                "prepared_data": str(prepared_data.resolve()),
                "config_backup": str(config_backup),
                "local_backup": str(local_backup),
                "data_backup": str(data_backup),
                "active_config_sha256": migration._sha256_file(root / "config.toml"),
                "active_auth_epoch": 1,
            }
        ),
    )

    result = migration.apply_pending_migration(project_root=root)

    assert result is not None
    assert result.state == "applied"
    assert result.migration_id == migration_id
    assert result.applied_at == applied_at
    assert (root / "config.toml").read_bytes() == active_config
    assert (target.data_path / "old-only.txt").read_text(encoding="utf-8") == "old target data"
    assert not (migration_root / "pending.json").exists()
    assert not (migration_root / "apply-journal.json").exists()
    assert not stage_dir.exists()
    status = json.loads((migration_root / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "applied"
    assert status["migration_id"] == migration_id


def test_runtime_guard_fences_a_second_backend_and_releases_cleanly(tmp_path: Path) -> None:
    first = migration.acquire_migration_runtime_guard(tmp_path)
    assert first is not None
    try:
        assert migration.acquire_migration_runtime_guard(tmp_path) is None
    finally:
        first.release()

    reacquired = migration.acquire_migration_runtime_guard(tmp_path)
    assert reacquired is not None
    reacquired.release()


def test_interrupted_apply_infers_completed_replaces_before_journal_flags(
    tmp_path: Path,
) -> None:
    root = tmp_path / "target"
    migration_root = root / ".openbiliclaw-migration"
    target_config = root / "config.toml"
    target_local = root / "config.local.toml"
    target_data = root / "data"
    migration_id = "b" * 32
    token = migration_id[:12]
    config_backup = root / f"config.toml.pre-import-{token}.bak"
    local_backup = root / f"config.local.toml.pre-import-{token}.bak"
    data_backup = root / f"data.pre-import-{token}.bak"
    prepared_config = root / f".config.toml.import-{token}"
    prepared_data = root / f".data.import-{token}"

    _write_file(target_config, "new config")
    _write_file(target_data / "new.txt", "new data")
    initialized = Database(target_data / "openbiliclaw.db")
    initialized.initialize()
    initialized.close()
    _write_file(config_backup, "old config")
    _write_file(local_backup, "old local")
    _write_file(data_backup / "old.txt", "old data")
    initialized = Database(data_backup / "openbiliclaw.db")
    initialized.initialize()
    initialized.close()
    _write_file(
        migration_root / "apply-journal.json",
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
                "target_local_existed": True,
                "target_data_existed": True,
                "config_moved": False,
                "local_moved": False,
                "data_moved": False,
                "new_config_active": False,
                "new_data_active": False,
            }
        ),
    )

    assert migration.apply_pending_migration(project_root=root) is None

    assert target_config.read_text(encoding="utf-8") == "old config"
    assert target_local.read_text(encoding="utf-8") == "old local"
    assert (target_data / "old.txt").read_text(encoding="utf-8") == "old data"
    assert not (root / f"config.toml.failed-import-{token}").exists()
    assert not (root / f"data.failed-import-{token}").exists()
    assert not (migration_root / "apply-journal.json").exists()
