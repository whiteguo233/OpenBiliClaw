"""Portable user-data export and staged cross-machine restore.

The running API never replaces its live SQLite database or memory files.  An
import is fully validated into a private staging directory and is applied only
by the next server process after it acquires the migration runtime lock.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import stat
import tempfile
import threading
import time
import tomllib
import unicodedata
import uuid
import zipfile
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, BinaryIO, cast

from openbiliclaw import __version__

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from openbiliclaw.config import Config

logger = logging.getLogger(__name__)

MIGRATION_FORMAT = "openbiliclaw-user-data"
MIGRATION_FORMAT_VERSION = 1
MIGRATION_ARCHIVE_SUFFIX = ".obcbackup"
MAX_MIGRATION_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
MAX_MIGRATION_FILES = 20_000
MAX_MIGRATION_FILE_BYTES = 4 * 1024 * 1024 * 1024
MAX_MIGRATION_UNCOMPRESSED_BYTES = 8 * 1024 * 1024 * 1024
MAX_MIGRATION_PATH_DEPTH = 16
MAX_MIGRATION_PATH_LENGTH = 512
_MAX_MANIFEST_BYTES = 16 * 1024 * 1024
_MAX_STATE_JSON_BYTES = 2 * 1024 * 1024
_MAX_FRONTEND_SETTINGS_BYTES = 64 * 1024
_MIGRATION_DIRNAME = ".openbiliclaw-migration"
_PENDING_MARKER = "pending.json"
_STATUS_FILE = "status.json"
_APPLY_JOURNAL = "apply-journal.json"
_RUNTIME_LOCK = "runtime.lock"
_COPY_CHUNK_BYTES = 1024 * 1024
_STABLE_COPY_ATTEMPTS = 4
_MAX_MIGRATION_SCAN_ENTRIES = MAX_MIGRATION_FILES * 5
_MIGRATION_LOCK = threading.RLock()
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ENV_NAME_RE = re.compile(r"^OPENBILICLAW_[A-Z0-9_]{1,128}$")
_EXTERNAL_MIGRATION_ENV_NAMES = frozenset(
    {
        # Gemini supports these standard provider variables in addition to the
        # generic OPENBILICLAW_* override namespace.
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        # System proxy / trust settings change outbound behavior without being
        # represented in portable config. Record names only, never values.
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
    }
)
_VERSION_RE = re.compile(r"^(?=.{1,64}$)(?=.*\d)[0-9A-Za-z][0-9A-Za-z._+-]*$")
_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
)

# These are derived, historical, or tied to one operating-system installation.
# The image cache is deliberately *not* excluded: some signed XHS image URLs
# cannot be fetched again after they expire, so the cached bytes are user data.
_EXCLUDED_DATA_ROOTS = frozenset(
    {"backups", "bin", "cache", "eval", "autostart", "certs", "tailnet"}
)
_EXCLUDED_DATA_FILES = frozenset({"embedding_cache.db"})
_TRANSIENT_SUFFIXES = ("-wal", "-shm", ".lock", ".tmp", ".part")
_PRESERVED_TARGET_ROOTS = ("certs", "autostart", "tailnet")
_TAILNET_HELPER_BASENAME = (
    "openbiliclaw-tailnet-helper.exe" if os.name == "nt" else "openbiliclaw-tailnet-helper"
)
_PROJECT_CODE_ROOTS = frozenset(
    {
        ".git",
        ".github",
        ".venv",
        ".worktrees",
        "docs",
        "extension",
        "logs",
        "packaging",
        "scripts",
        "src",
        "tests",
        "venv",
    }
)
_ALLOWED_CONFIG_ARCHIVE_PATHS = frozenset({"config/config.toml", "config/config.local.toml"})
_ALLOWED_FRONTEND_ARCHIVE_PATH = "frontend/settings.json"


class MigrationError(ValueError):
    """A user-facing migration validation or staging failure."""

    def __init__(self, message: str, *, code: str = "invalid_migration") -> None:
        super().__init__(message)
        self.code = code


def _is_migration_environment_name(name: str) -> bool:
    """Return whether *name* can affect portable OpenBiliClaw behavior."""
    return bool(_ENV_NAME_RE.fullmatch(name)) or name in _EXTERNAL_MIGRATION_ENV_NAMES


def _active_migration_environment_names() -> list[str]:
    """Return a bounded, value-free inventory of active migration-relevant env vars."""
    return sorted(
        name for name, value in os.environ.items() if value and _is_migration_environment_name(name)
    )[:256]


@dataclass(frozen=True)
class MigrationExport:
    """A completed archive ready to stream to the browser."""

    path: Path
    filename: str
    file_count: int
    uncompressed_bytes: int
    contains_secrets: bool = True


@dataclass(frozen=True)
class StagedMigration:
    """A validated import queued for the next exclusive server start."""

    migration_id: str
    source_version: str
    file_count: int
    uncompressed_bytes: int
    frontend_settings: dict[str, object]
    adjusted_fields: tuple[str, ...]
    source_omitted_environment_variables: tuple[str, ...]
    target_active_environment_variables: tuple[str, ...]
    request_id: str = ""
    restart_required: bool = True


@dataclass(frozen=True)
class MigrationApplyResult:
    """Last startup-side apply outcome."""

    state: str
    migration_id: str = ""
    source_version: str = ""
    applied_at: str = ""
    message: str = ""
    config_backup: str = ""
    data_backup: str = ""
    frontend_settings: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class _ApplyJournalPaths:
    """Validated filesystem targets derived from one apply journal."""

    target_config: Path
    target_local: Path
    target_data: Path
    prepared_config: Path
    prepared_data: Path
    config_backup: Path
    local_backup: Path
    data_backup: Path
    failed_config: Path
    failed_data: Path


@dataclass(frozen=True)
class _AppliedGenerationReceipt:
    """Private durable proof used to deduplicate a resurrected marker."""

    migration_id: str
    target_config: Path
    target_data: Path
    config_sha256: str
    auth_epoch: int


class _BoundedArchiveFile:
    """Seekable ZIP output that refuses to grow beyond the download limit."""

    def __init__(self, path: Path, max_bytes: int) -> None:
        self._handle = path.open("w+b")
        self._max_bytes = max_bytes

    def write(self, data: bytes) -> int:
        if self._handle.tell() + len(data) > self._max_bytes:
            raise MigrationError("迁移包超过 2 GB 下载上限。", code="archive_too_large")
        return self._handle.write(data)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._handle, name)

    def __enter__(self) -> _BoundedArchiveFile:
        return self

    def __exit__(self, *_args: object) -> None:
        self._handle.close()


class MigrationRuntimeGuard:
    """Lifetime OS lock proving that no supported backend process is active."""

    def __init__(self, handles: list[tuple[BinaryIO, Path]]) -> None:
        self._handles = handles
        self.paths = tuple(path for _handle, path in handles)
        self.path = self.paths[0] if self.paths else Path()

    def release(self) -> None:
        handles = self._handles
        if not handles:
            return
        self._handles = []
        for handle, _path in reversed(handles):
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt_module = cast("Any", msvcrt)
                    handle.seek(0)
                    msvcrt_module.locking(handle.fileno(), msvcrt_module.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()

    def acquire_data_dir(self, data_dir: Path) -> bool:
        """Extend this live guard to another canonical runtime data directory."""
        canonical_data = Path(data_dir).expanduser().resolve()
        lock_path = canonical_data.parent / f".{canonical_data.name}.openbiliclaw-runtime.lock"
        if lock_path in self.paths:
            return True
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = _try_acquire_runtime_lock(lock_path)
        if handle is None:
            return False
        self._handles.append((handle, lock_path))
        self.paths = tuple(path for _handle, path in self._handles)
        self.path = self.paths[0] if self.paths else Path()
        return True

    def __enter__(self) -> MigrationRuntimeGuard:
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


def acquire_migration_runtime_guard(
    project_root: Path,
    data_dir: Path | None = None,
) -> MigrationRuntimeGuard | None:
    """Try to hold project and canonical-data locks for the caller's lifetime."""
    project_root = Path(project_root).expanduser().resolve()
    migration_root = _migration_root(project_root)
    migration_root.mkdir(parents=True, exist_ok=True)
    try:
        migration_root_stat = migration_root.lstat()
    except OSError:
        return None
    if stat.S_ISLNK(migration_root_stat.st_mode) or not stat.S_ISDIR(migration_root_stat.st_mode):
        return None
    _chmod_private(migration_root, directory=True)
    paths = [migration_root / _RUNTIME_LOCK]
    if data_dir is not None:
        canonical_data = Path(data_dir).expanduser().resolve()
        data_lock = canonical_data.parent / f".{canonical_data.name}.openbiliclaw-runtime.lock"
        if data_lock not in paths:
            paths.append(data_lock)
    handles: list[tuple[BinaryIO, Path]] = []
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = _try_acquire_runtime_lock(path)
        if handle is None:
            MigrationRuntimeGuard(handles).release()
            return None
        handles.append((handle, path))
    return MigrationRuntimeGuard(handles)


def migration_recovery_data_dir(*, project_root: Path | None = None) -> Path | None:
    """Return the journal-bound data directory that startup must lock first."""
    root = _resolve_project_root(project_root)
    migration_root = _migration_root(root)
    journal_path = migration_root / _APPLY_JOURNAL
    if journal_path.is_file():
        journal = _read_json_file(journal_path)
        if not isinstance(journal, dict):
            raise MigrationError(
                "迁移恢复日志已损坏；为避免覆盖数据，后端拒绝继续启动。",
                code="corrupt_apply_journal",
            )
        return _validated_apply_journal_paths(migration_root, journal).target_data
    marker = _read_json_file(migration_root / _PENDING_MARKER)
    status = _read_json_file(migration_root / _STATUS_FILE)
    if (
        isinstance(marker, dict)
        and isinstance(status, dict)
        and status.get("state") == "applied"
        and status.get("migration_id") == marker.get("migration_id")
    ):
        return _validated_applied_generation_receipt(migration_root, status).target_data
    return None


def _windows_runtime_lock(handle: BinaryIO, msvcrt_module: Any) -> bool:
    """Apply the Windows one-byte ``msvcrt.locking`` runtime lock.

    锁已被别的实例持有时,Windows 在上锁前的 ``read`` 这一步就会直接抛
    ``PermissionError``——所以整个 seek/read/write/lock 序列都按失败即
    关闭句柄并返回 False 处理,让调用方走「已有实例在运行」的优雅分支
    而不是带回溯崩溃。返回 False 时 ``handle`` 已被关闭。
    """
    try:
        handle.seek(0)
        if handle.read(1) == b"":
            handle.seek(0)
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        msvcrt_module.locking(handle.fileno(), msvcrt_module.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return False
    return True


def _try_acquire_runtime_lock(path: Path) -> BinaryIO | None:
    """Acquire one non-blocking OS file lock, returning its live handle."""
    try:
        existing = path.lstat()
    except FileNotFoundError:
        existing = None
    if existing is not None and (
        stat.S_ISLNK(existing.st_mode) or not stat.S_ISREG(existing.st_mode)
    ):
        return None

    flags = os.O_RDWR | os.O_CREAT
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOINHERIT", 0)
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError:
        # A symlink inserted between lstat() and open() is rejected by
        # O_NOFOLLOW on POSIX. Windows lacks that flag, so an unsafe final path
        # is also rejected by the post-open lstat/fstat identity check below.
        with suppress(OSError):
            current = path.lstat()
            if stat.S_ISLNK(current.st_mode) or not stat.S_ISREG(current.st_mode):
                return None
        raise
    try:
        opened = os.fstat(descriptor)
        current = path.lstat()
        same_inode = (
            not opened.st_ino
            or not current.st_ino
            or (opened.st_dev == current.st_dev and opened.st_ino == current.st_ino)
        )
        if (
            not stat.S_ISREG(opened.st_mode)
            or stat.S_ISLNK(current.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or not same_inode
        ):
            os.close(descriptor)
            return None
        with suppress(OSError, AttributeError):
            os.fchmod(descriptor, 0o600)
        handle = os.fdopen(descriptor, "r+b")
    except Exception:
        with suppress(OSError):
            os.close(descriptor)
        raise
    try:
        if os.name == "nt":
            import msvcrt

            if not _windows_runtime_lock(handle, cast("Any", msvcrt)):
                return None
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                handle.close()
                return None
    except Exception:
        handle.close()
        raise
    return handle


def create_migration_archive(
    config: Config,
    frontend_settings: Mapping[str, object] | None = None,
    *,
    project_root: Path | None = None,
) -> MigrationExport:
    """Create a consistent, checksummed ZIP archive of portable user state."""
    root = _resolve_project_root(project_root)
    data_dir = _config_data_path(root, config)
    _validate_migration_data_dir(root, data_dir)
    frontend = normalize_frontend_settings(frontend_settings)
    exported_at = datetime.now(UTC)
    temp_root = Path(tempfile.mkdtemp(prefix="openbiliclaw-export-"))
    _chmod_private(temp_root, directory=True)
    snapshot_root = temp_root / "snapshot"
    snapshot_root.mkdir(mode=0o700)

    entries: list[dict[str, object]] = []
    uncompressed_bytes = 0

    def register_entry(path: Path, archive_name: str, *, kind: str) -> None:
        nonlocal uncompressed_bytes
        if len(entries) >= MAX_MIGRATION_FILES:
            raise MigrationError("可迁移文件数量超过 20000 个。", code="too_many_entries")
        entry = _entry_metadata(path, archive_name, kind=kind)
        size = cast("int", entry["size"])
        if size > MAX_MIGRATION_FILE_BYTES:
            raise MigrationError(f"可迁移文件过大：{archive_name}", code="entry_too_large")
        if uncompressed_bytes + size > MAX_MIGRATION_UNCOMPRESSED_BYTES:
            raise MigrationError("可迁移数据超过 8 GB。", code="archive_expands_too_large")
        entries.append(entry)
        uncompressed_bytes += size

    omitted: list[str] = [
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
        "api-auth-password-and-signing-material",
        "external-cli-credentials",
        "environment-variable-values",
    ]
    try:
        # Flatten config.toml + config.local.toml from disk without consulting
        # environment overrides. The destination keeps its own API trust boundary,
        # so source password hashes, signing secrets, and paired-device keys never
        # need to enter the portable archive at all.
        from openbiliclaw.config import ApiAuthConfig, save_config

        portable_config = _load_project_config(root, consult_environment=False)
        portable_config.api.auth = ApiAuthConfig()
        config_destination = snapshot_root / "config/config.toml"
        config_destination.parent.mkdir(parents=True, exist_ok=True)
        save_config(
            portable_config,
            config_destination,
            autostart_authoritative=True,
            preserve_override_provenance=False,
            include_api_auth=False,
        )
        _chmod_private(config_destination)
        register_entry(config_destination, "config/config.toml", kind="config")

        if data_dir.is_dir():
            for source, relative in _iter_portable_data_files(data_dir):
                # Reserve one final slot for frontend/settings.json and reject
                # source sizes before allocating an equally large temp copy.
                if len(entries) >= MAX_MIGRATION_FILES - 1:
                    raise MigrationError(
                        "可迁移文件数量超过 20000 个。",
                        code="too_many_entries",
                    )
                source_size = source.stat().st_size
                if source_size > MAX_MIGRATION_FILE_BYTES:
                    raise MigrationError(
                        f"可迁移文件过大：{relative.as_posix()}",
                        code="entry_too_large",
                    )
                remaining_bytes = MAX_MIGRATION_UNCOMPRESSED_BYTES - uncompressed_bytes
                if source_size > remaining_bytes:
                    raise MigrationError(
                        "可迁移数据超过 8 GB。",
                        code="archive_expands_too_large",
                    )
                archive_name = PurePosixPath("data", *relative.parts).as_posix()
                try:
                    archive_name = _validate_logical_payload_path(archive_name)
                except MigrationError as exc:
                    raise MigrationError(
                        f"数据文件名无法跨平台迁移：{relative.as_posix()}",
                        code="unsupported_source_path",
                    ) from exc
                destination = snapshot_root / Path(*PurePosixPath(archive_name).parts)
                if _looks_like_sqlite(source):
                    _snapshot_sqlite(
                        source,
                        destination,
                        max_bytes=min(MAX_MIGRATION_FILE_BYTES, remaining_bytes),
                    )
                    kind = "sqlite"
                else:
                    _copy_stable_private_file(
                        source,
                        destination,
                        max_bytes=min(MAX_MIGRATION_FILE_BYTES, remaining_bytes),
                    )
                    kind = "credential" if source.name.endswith("_cookie.json") else "data"
                register_entry(destination, archive_name, kind=kind)

        frontend_path = snapshot_root / _ALLOWED_FRONTEND_ARCHIVE_PATH
        _write_private_json(frontend_path, frontend)
        register_entry(frontend_path, _ALLOWED_FRONTEND_ARCHIVE_PATH, kind="frontend")

        portable_names = [_portable_path_key(cast("str", entry["path"])) for entry in entries]
        if len(portable_names) != len(set(portable_names)):
            raise MigrationError(
                "可迁移数据包含跨平台冲突路径（大小写或 Unicode 规范化重复）。",
                code="duplicate_entry",
            )
        archive_entries = sorted(entries, key=lambda item: str(item["path"]))
        manifest = {
            "format": MIGRATION_FORMAT,
            "format_version": MIGRATION_FORMAT_VERSION,
            "source_version": __version__,
            "exported_at": exported_at.isoformat(),
            "contains_secrets": True,
            "encrypted": False,
            "entries": archive_entries,
            "omitted": omitted,
            "source_omitted_environment_variables": _active_migration_environment_names(),
        }
        manifest_path = snapshot_root / "manifest.json"
        _write_private_json(manifest_path, manifest)
        if manifest_path.stat().st_size > _MAX_MANIFEST_BYTES:
            raise MigrationError("迁移文件清单过大。", code="manifest_too_large")

        timestamp = exported_at.strftime("%Y%m%d-%H%M%S")
        filename = f"openbiliclaw-backup-{timestamp}{MIGRATION_ARCHIVE_SUFFIX}"
        archive_path = temp_root / filename
        with (
            _BoundedArchiveFile(
                archive_path,
                MAX_MIGRATION_ARCHIVE_BYTES,
            ) as archive_output,
            zipfile.ZipFile(
                cast("BinaryIO", archive_output),
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=6,
                allowZip64=True,
            ) as archive,
        ):
            archive.write(manifest_path, "manifest.json")
            for entry in archive_entries:
                archive_name = str(entry["path"])
                archive.write(
                    snapshot_root / Path(*PurePosixPath(archive_name).parts), archive_name
                )
        if archive_path.stat().st_size > MAX_MIGRATION_ARCHIVE_BYTES:
            raise MigrationError("迁移包超过 2 GB 下载上限。", code="archive_too_large")
        # The snapshot can be much larger than its compressed archive. It is no
        # longer needed once ZIP finalization succeeds, so never retain both for
        # the lifetime of a slow browser download.
        shutil.rmtree(snapshot_root)
        _chmod_private(archive_path)
        return MigrationExport(
            path=archive_path,
            filename=filename,
            file_count=len(entries),
            uncompressed_bytes=uncompressed_bytes,
        )
    except Exception:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise


def stage_migration_archive(
    archive_path: Path,
    current_config: Config,
    *,
    project_root: Path | None = None,
    request_id: str = "",
) -> StagedMigration:
    """Validate *archive_path* completely, then atomically publish pending state."""
    root = _resolve_project_root(project_root)
    _validate_target_config_files(root)
    target_data_dir = _config_data_path(root, current_config)
    _validate_migration_data_dir(root, target_data_dir)
    if request_id and re.fullmatch(r"[0-9a-f]{32}", request_id) is None:
        raise MigrationError("迁移请求 ID 无效。", code="invalid_request_id")
    archive_path = Path(archive_path)
    try:
        archive_size = archive_path.stat().st_size
    except OSError as exc:
        raise MigrationError("无法读取迁移包。", code="archive_unreadable") from exc
    if archive_size <= 0 or archive_size > MAX_MIGRATION_ARCHIVE_BYTES:
        raise MigrationError("迁移包为空或超过 2 GB 上传上限。", code="archive_too_large")

    with _MIGRATION_LOCK:
        migration_root = _migration_root(root)
        migration_root.mkdir(parents=True, exist_ok=True)
        _chmod_private(migration_root, directory=True)
        _reconcile_orphan_stage_directories(migration_root)
        migration_id = uuid.uuid4().hex
        incoming = migration_root / f"incoming-{migration_id}"
        incoming.mkdir(mode=0o700)
        pending_dir: Path | None = None
        marker_published = False
        try:
            manifest, infos = _read_and_validate_manifest(archive_path)
            source_version = str(manifest.get("source_version", "")).strip()
            if _version_tuple(source_version) > _version_tuple(__version__):
                raise MigrationError(
                    f"迁移包来自更高版本 {source_version}；请先把当前机器升级到同版本或更高版本。",
                    code="newer_source_version",
                )
            _extract_verified_entries(archive_path, incoming, manifest, infos)
            frontend = _load_frontend_settings(incoming)
            raw_source_environment = manifest.get(
                "source_omitted_environment_variables",
                manifest.get("active_environment_variables", []),
            )
            source_environment = (
                raw_source_environment if isinstance(raw_source_environment, list) else []
            )
            source_environment_names = sorted(
                {
                    name
                    for name in source_environment
                    if isinstance(name, str) and _is_migration_environment_name(name)
                }
            )[:256]
            target_environment_names = _active_migration_environment_names()
            candidate, adjusted_fields = _normalize_imported_config(
                incoming,
                current_config,
            )
            _validate_imported_databases(incoming / "data")

            normalized_config = incoming / "normalized-config.toml"
            from openbiliclaw.config import save_config

            save_config(candidate, normalized_config, autostart_authoritative=True)
            _chmod_private(normalized_config)

            stage_seal = _stage_tree_seal(incoming)
            pending_dir = migration_root / f"pending-{migration_id}"
            os.replace(incoming, pending_dir)
            marker = {
                "format_version": MIGRATION_FORMAT_VERSION,
                "migration_id": migration_id,
                "source_version": source_version,
                "request_id": request_id,
                "created_at": datetime.now(UTC).isoformat(),
                "stage_dir": pending_dir.name,
                "target_config": str((root / "config.toml").resolve()),
                "target_config_local": str((root / "config.local.toml").resolve()),
                "target_data_dir": str(target_data_dir),
                "adjusted_fields": list(adjusted_fields),
                "source_omitted_environment_variables": source_environment_names,
                "target_active_environment_variables": target_environment_names,
                "frontend_settings": frontend,
                "stage_seal": stage_seal,
            }
            old_marker = _read_json_file(migration_root / _PENDING_MARKER)
            _atomic_write_json(migration_root / _PENDING_MARKER, marker)
            marker_published = True
            if isinstance(old_marker, dict):
                old_stage = _safe_stage_path(migration_root, old_marker.get("stage_dir"))
                if old_stage is not None and old_stage != pending_dir:
                    shutil.rmtree(old_stage, ignore_errors=True)

            raw_entries = manifest.get("entries", [])
            manifest_entries = raw_entries if isinstance(raw_entries, list) else []
            return StagedMigration(
                migration_id=migration_id,
                source_version=source_version,
                file_count=len(manifest_entries),
                uncompressed_bytes=sum(
                    size
                    for entry in manifest_entries
                    if isinstance(entry, dict)
                    for size in [entry.get("size", 0)]
                    if isinstance(size, int) and not isinstance(size, bool)
                ),
                frontend_settings=frontend,
                adjusted_fields=tuple(adjusted_fields),
                source_omitted_environment_variables=tuple(source_environment_names),
                target_active_environment_variables=tuple(target_environment_names),
                request_id=request_id,
            )
        except MigrationError:
            shutil.rmtree(incoming, ignore_errors=True)
            if pending_dir is not None and not marker_published:
                shutil.rmtree(pending_dir, ignore_errors=True)
            raise
        except (OSError, ValueError, zipfile.BadZipFile, sqlite3.DatabaseError) as exc:
            shutil.rmtree(incoming, ignore_errors=True)
            if pending_dir is not None and not marker_published:
                shutil.rmtree(pending_dir, ignore_errors=True)
            raise MigrationError(
                f"迁移包校验失败：{exc}", code="archive_validation_failed"
            ) from exc


def cancel_pending_migration(*, project_root: Path | None = None) -> bool:
    """Cancel a staged import without touching current config or user data."""
    root = _resolve_project_root(project_root)
    migration_root = _migration_root(root)
    marker_path = migration_root / _PENDING_MARKER
    with _MIGRATION_LOCK:
        if migration_root.is_dir():
            _reconcile_orphan_stage_directories(migration_root)
        if not marker_path.exists():
            return False
        marker = _read_json_file(marker_path)
        if not isinstance(marker, dict):
            raise MigrationError("待应用迁移标记已损坏。", code="corrupt_pending_marker")
        if (migration_root / _APPLY_JOURNAL).exists():
            raise MigrationError("迁移正在应用，不能取消。", code="apply_in_progress")
        stage_dir = _safe_stage_path(migration_root, marker.get("stage_dir"))
        if stage_dir is not None and stage_dir.exists():
            shutil.rmtree(stage_dir)
        marker_path.unlink(missing_ok=True)
        result = MigrationApplyResult(
            state="cancelled",
            migration_id=str(marker.get("migration_id", "")),
            source_version=str(marker.get("source_version", "")),
            applied_at=datetime.now(UTC).isoformat(),
            message="待导入迁移包已取消，当前数据未改动。",
        )
        _atomic_write_json(migration_root / _STATUS_FILE, asdict(result))
        _fsync_directory(migration_root)
        return True


def apply_pending_migration(
    *,
    project_root: Path | None = None,
    locked_data_dir: Path | None = None,
) -> MigrationApplyResult | None:
    """Apply one validated pending import while the caller holds the runtime lock."""
    root = _resolve_project_root(project_root)
    migration_root = _migration_root(root)
    marker_path = migration_root / _PENDING_MARKER
    with _MIGRATION_LOCK:
        if migration_root.is_dir():
            _reconcile_orphan_stage_directories(migration_root)
        recovered = _recover_interrupted_apply(
            migration_root,
            locked_data_dir=locked_data_dir,
        )
        if recovered is not None:
            return recovered
        if not marker_path.is_file():
            _reconcile_orphan_stage_directories(migration_root)
            return None
        marker = _read_json_file(marker_path)
        if not isinstance(marker, dict):
            result = _record_apply_failure(migration_root, "", "待应用迁移标记已损坏。")
            marker_path.unlink(missing_ok=True)
            _reconcile_orphan_stage_directories(migration_root)
            return result
        migration_id = str(marker.get("migration_id", ""))
        source_version = str(marker.get("source_version", ""))
        stage_dir = _safe_stage_path(migration_root, marker.get("stage_dir"))
        if stage_dir is not None and stage_dir.name != f"pending-{migration_id}":
            raise MigrationError(
                "待应用迁移标记与暂存目录不匹配。",
                code="corrupt_pending_marker",
            )
        last_status = _read_json_file(migration_root / _STATUS_FILE)
        if (
            isinstance(last_status, dict)
            and last_status.get("state") == "applied"
            and last_status.get("migration_id") == migration_id
        ):
            receipt = _validated_applied_generation_receipt(migration_root, last_status)
            if (
                locked_data_dir is not None
                and receipt.target_data != Path(locked_data_dir).expanduser().resolve()
            ):
                raise MigrationError(
                    "已应用迁移的数据目录未被当前进程锁定；后端拒绝清理恢复标记。",
                    code="migration_lock_mismatch",
                )
            _verify_active_generation(receipt)
            if stage_dir is None:
                raise MigrationError(
                    "已应用迁移的残留暂存路径无效。",
                    code="corrupt_pending_marker",
                )
            if stage_dir.exists():
                shutil.rmtree(stage_dir)
            marker_path.unlink(missing_ok=True)
            _fsync_directory(migration_root)
            return MigrationApplyResult(
                state="applied",
                migration_id=migration_id,
                source_version=str(last_status.get("source_version", source_version)),
                applied_at=str(last_status.get("applied_at", "")),
                message=str(
                    last_status.get(
                        "message",
                        "迁移数据已应用；已清理重复恢复标记。",
                    )
                ),
                config_backup=str(last_status.get("config_backup", "")),
                data_backup=str(last_status.get("data_backup", "")),
                frontend_settings=normalize_frontend_settings(
                    last_status.get("frontend_settings")
                    if isinstance(last_status.get("frontend_settings"), dict)
                    else {}
                ),
            )
        if stage_dir is None or not stage_dir.is_dir():
            result = _record_apply_failure(
                migration_root,
                migration_id,
                "待应用迁移目录不存在或不安全。",
                source_version=source_version,
            )
            if stage_dir is not None and stage_dir.exists():
                shutil.rmtree(stage_dir)
            marker_path.unlink(missing_ok=True)
            _reconcile_orphan_stage_directories(migration_root)
            return result

        prepared_config: Path | None = None
        prepared_data: Path | None = None
        journal_path = migration_root / _APPLY_JOURNAL
        try:
            _verify_stage_tree_seal(stage_dir, marker.get("stage_seal"))
            _validate_target_config_files(root)
            target_config = _validated_target_path(
                marker.get("target_config"), root / "config.toml"
            )
            target_local = _validated_target_path(
                marker.get("target_config_local"), root / "config.local.toml"
            )
            # The destination may legitimately change its password, listener,
            # proxy, or data_dir after staging. Re-read both current layers now:
            # the effective config selects the directory protected by the startup
            # lock, while the disk-only config is what we persist without baking
            # environment override values into config.toml.
            current_disk_config = _load_project_config(root, consult_environment=False)
            current_effective_config = _load_project_config(root, consult_environment=True)
            target_data = _config_data_path(root, current_effective_config)
            if locked_data_dir is not None and target_data != Path(locked_data_dir).resolve():
                raise MigrationError(
                    "data_dir 在取得启动锁后发生变化，已拒绝应用迁移。",
                    code="unsafe_target",
                )
            _validate_migration_data_dir(root, target_data)
            if target_config.is_symlink() or target_local.is_symlink() or target_data.is_symlink():
                raise MigrationError("目标配置或数据目录不能是符号链接。", code="unsafe_target")
            staged_config = stage_dir / "normalized-config.toml"
            staged_data = stage_dir / "data"
            if not staged_config.is_file():
                raise MigrationError("暂存配置缺失。", code="staging_incomplete")
            staged_data.mkdir(exist_ok=True)
            _validate_imported_databases(staged_data)
            candidate = _load_config_file(staged_config)
            candidate, _adjusted = _preserve_target_machine_config(
                candidate,
                current_disk_config,
            )

            target_config.parent.mkdir(parents=True, exist_ok=True)
            target_data.parent.mkdir(parents=True, exist_ok=True)
            token = migration_id[:12] or uuid.uuid4().hex[:12]
            prepared_config = target_config.with_name(f".{target_config.name}.import-{token}")
            prepared_data = target_data.with_name(f".{target_data.name}.import-{token}")
            config_backup = target_config.with_name(f"{target_config.name}.pre-import-{token}.bak")
            local_backup = target_local.with_name(f"{target_local.name}.pre-import-{token}.bak")
            data_backup = target_data.with_name(f"{target_data.name}.pre-import-{token}.bak")
            failed_config = target_config.with_name(f"{target_config.name}.failed-import-{token}")
            failed_data = target_data.with_name(f"{target_data.name}.failed-import-{token}")
            _validate_distinct_apply_paths(
                target_config,
                target_local,
                target_data,
                prepared_config,
                prepared_data,
                config_backup,
                local_backup,
                data_backup,
                failed_config,
                failed_data,
            )

            frontend = normalize_frontend_settings(
                marker.get("frontend_settings")
                if isinstance(marker.get("frontend_settings"), dict)
                else {}
            )
            journal: dict[str, object] = {
                "state": "preparing",
                "migration_id": migration_id,
                "source_version": source_version,
                "stage_dir": stage_dir.name,
                "target_config": str(target_config),
                "target_local": str(target_local),
                "target_data": str(target_data),
                "prepared_config": str(prepared_config),
                "prepared_data": str(prepared_data),
                "config_backup": str(config_backup),
                "local_backup": str(local_backup),
                "data_backup": str(data_backup),
                "frontend_settings": frontend,
                "target_config_existed": target_config.exists(),
                "target_local_existed": target_local.exists(),
                "target_data_existed": target_data.exists(),
                "config_moved": False,
                "local_moved": False,
                "data_moved": False,
                "new_data_active": False,
                "new_config_active": False,
            }
            transaction_artifacts = (
                config_backup,
                local_backup,
                data_backup,
                failed_config,
                failed_data,
            )
            if any(os.path.lexists(path) for path in transaction_artifacts):
                raise MigrationError(
                    "迁移事务文件名已被占用；为避免覆盖回滚副本，后端拒绝应用。",
                    code="transaction_artifact_collision",
                )
            _delete_migration_artifact(prepared_config)
            _delete_migration_artifact(prepared_data)
            _fsync_directory(prepared_config.parent)
            _fsync_directory(prepared_data.parent)
            # Publish planned temp paths before copying potentially gigabytes so
            # disk-full, permission failures, or a killed process cannot orphan a
            # private partial tree with no recovery record.
            _atomic_write_json(journal_path, journal)
            from openbiliclaw.config import save_config

            save_config(
                candidate,
                prepared_config,
                autostart_authoritative=True,
                preserve_override_provenance=False,
            )
            _chmod_private(prepared_config)
            shutil.copytree(staged_data, prepared_data)
            _privatize_tree(prepared_data)
            tailnet_helper_preserved = _preserve_target_machine_data(target_data, prepared_data)
            active_auth_epoch = _smoke_test_prepared_database(prepared_data, target_data)
            _fsync_file(prepared_config)
            _fsync_tree(prepared_data)
            _fsync_directory(target_config.parent)
            _fsync_directory(target_data.parent)

            journal["active_config_sha256"] = _sha256_file(prepared_config)
            journal["active_auth_epoch"] = active_auth_epoch
            journal["state"] = "applying"
            _atomic_write_json(journal_path, journal)

            if target_config.exists():
                os.replace(target_config, config_backup)
                _fsync_directory(target_config.parent)
                journal["config_moved"] = True
                _atomic_write_json(journal_path, journal)
            if target_local.exists():
                os.replace(target_local, local_backup)
                _fsync_directory(target_local.parent)
                journal["local_moved"] = True
                _atomic_write_json(journal_path, journal)
            if target_data.exists():
                os.replace(target_data, data_backup)
                _fsync_directory(target_data.parent)
                journal["data_moved"] = True
                _atomic_write_json(journal_path, journal)

            os.replace(prepared_data, target_data)
            _fsync_directory(target_data.parent)
            journal["new_data_active"] = True
            _atomic_write_json(journal_path, journal)
            os.replace(prepared_config, target_config)
            _fsync_directory(target_config.parent)
            journal["new_config_active"] = True
            _atomic_write_json(journal_path, journal)

            _validate_imported_databases(target_data)
            _chmod_private(target_config)
            _privatize_tree(target_data)
            _restore_tailnet_helper_mode(
                target_data,
                trusted_copy=tailnet_helper_preserved,
            )
            applied_at = datetime.now(UTC).isoformat()
            result = MigrationApplyResult(
                state="applied",
                migration_id=migration_id,
                source_version=source_version,
                applied_at=applied_at,
                message="迁移数据已应用；旧配置和数据保留为回滚副本。",
                config_backup=str(config_backup) if config_backup.exists() else "",
                data_backup=str(data_backup) if data_backup.exists() else "",
                frontend_settings=frontend,
            )
            _atomic_write_json(
                migration_root / _STATUS_FILE,
                _applied_status_payload(
                    result,
                    target_config=target_config,
                    target_data=target_data,
                    config_sha256=cast("str", journal["active_config_sha256"]),
                    auth_epoch=active_auth_epoch,
                ),
            )
            journal["state"] = "committed"
            journal["applied_at"] = applied_at
            _atomic_write_json(journal_path, journal)
            shutil.rmtree(stage_dir)
            marker_path.unlink(missing_ok=True)
            _fsync_directory(migration_root)
            journal_path.unlink(missing_ok=True)
            _fsync_directory(migration_root)
            _prune_old_migration_artifacts(
                target_config=target_config,
                target_local=target_local,
                target_data=target_data,
                keep={config_backup, local_backup, data_backup},
            )
            return result
        except Exception as exc:  # rollback must cover every filesystem failure
            logger.exception("Failed to apply pending migration")
            recovered = _recover_interrupted_apply(
                migration_root,
                locked_data_dir=locked_data_dir,
            )
            if recovered is not None:
                return recovered
            if prepared_config is not None:
                prepared_config.unlink(missing_ok=True)
            if prepared_data is not None:
                shutil.rmtree(prepared_data, ignore_errors=True)
            if stage_dir.exists():
                shutil.rmtree(stage_dir)
            marker_path.unlink(missing_ok=True)
            journal_path.unlink(missing_ok=True)
            _fsync_directory(migration_root)
            return _record_apply_failure(
                migration_root,
                migration_id,
                f"迁移应用失败，已恢复原数据：{exc}",
                source_version=source_version,
            )


def migration_status(*, project_root: Path | None = None) -> dict[str, object]:
    """Return pending/last-apply state without exposing archive secrets."""
    root = _resolve_project_root(project_root)
    migration_root = _migration_root(root)
    marker = _read_json_file(migration_root / _PENDING_MARKER)
    if isinstance(marker, dict):
        raw_adjusted = marker.get("adjusted_fields", [])
        raw_source_environment = marker.get("source_omitted_environment_variables", [])
        raw_target_environment = marker.get("target_active_environment_variables", [])
        raw_frontend = marker.get("frontend_settings", {})
        return {
            "state": "staged",
            "migration_id": str(marker.get("migration_id", "")),
            "request_id": str(marker.get("request_id", "")),
            "source_version": str(marker.get("source_version", "")),
            "created_at": str(marker.get("created_at", "")),
            "restart_required": True,
            "adjusted_fields": list(raw_adjusted) if isinstance(raw_adjusted, list) else [],
            "source_omitted_environment_variables": (
                list(raw_source_environment) if isinstance(raw_source_environment, list) else []
            ),
            "target_active_environment_variables": (
                list(raw_target_environment) if isinstance(raw_target_environment, list) else []
            ),
            "frontend": (
                normalize_frontend_settings(raw_frontend) if isinstance(raw_frontend, dict) else {}
            ),
        }
    status = _read_json_file(migration_root / _STATUS_FILE)
    if isinstance(status, dict):
        result = {key: value for key, value in status.items() if not key.startswith("_")}
        raw_frontend = result.pop("frontend_settings", {})
        result["frontend"] = (
            normalize_frontend_settings(raw_frontend) if isinstance(raw_frontend, dict) else {}
        )
        result["restart_required"] = False
        return result
    return {"state": "idle", "restart_required": False, "frontend": {}}


def normalize_frontend_settings(
    value: Mapping[str, object] | None,
) -> dict[str, object]:
    """Allowlist portable browser preferences; never include endpoint/session state."""
    raw = dict(value or {})
    result: dict[str, object] = {}
    theme = raw.get("theme_mode")
    if isinstance(theme, str) and theme in {"auto", "light", "dark"}:
        result["theme_mode"] = theme
    hue = raw.get("theme_hue")
    if isinstance(hue, int) and not isinstance(hue, bool) and 0 <= hue <= 360:
        result["theme_hue"] = hue
    accent = raw.get("accent_style")
    if isinstance(accent, str) and accent in {"modern", "classic"}:
        result["accent_style"] = accent
    for field_name in ("auto_load_on_scroll", "side_drawer_open"):
        if isinstance(raw.get(field_name), bool):
            result[field_name] = raw[field_name]
    return result


def _migration_root(project_root: Path) -> Path:
    return project_root / _MIGRATION_DIRNAME


def _resolve_project_root(value: Path | None) -> Path:
    if value is not None:
        return Path(value).expanduser().resolve()
    from openbiliclaw.config import _project_root

    return _project_root().resolve()


def _iter_portable_data_files(data_dir: Path) -> Iterator[tuple[Path, Path]]:
    pending: list[tuple[Path, Path]] = [(data_dir, Path())]
    scanned = 0
    while pending:
        directory, relative_directory = pending.pop()
        with os.scandir(directory) as children:
            for child in children:
                scanned += 1
                if scanned > _MAX_MIGRATION_SCAN_ENTRIES:
                    raise MigrationError(
                        "数据目录项目过多，已停止导出扫描。",
                        code="too_many_entries",
                    )
                relative = relative_directory / child.name
                if child.is_symlink():
                    continue
                if child.is_dir(follow_symlinks=False):
                    if (
                        not relative_directory.parts
                        and child.name.casefold() in _EXCLUDED_DATA_ROOTS
                    ):
                        continue
                    pending.append((Path(child.path), relative))
                    continue
                folded_name = child.name.casefold()
                if folded_name in _EXCLUDED_DATA_FILES or folded_name.endswith(_TRANSIENT_SUFFIXES):
                    continue
                if ".broken." in child.name or ".repaired." in child.name:
                    continue
                if child.is_file(follow_symlinks=False):
                    yield Path(child.path), relative


def _looks_like_sqlite(path: Path) -> bool:
    return path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}


def _snapshot_sqlite(
    source: Path,
    destination: Path,
    *,
    max_bytes: int = MAX_MIGRATION_FILE_BYTES,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    source_connection = sqlite3.connect(
        _sqlite_readonly_uri(source),
        uri=True,
        timeout=30.0,
    )
    destination_connection: sqlite3.Connection | None = None
    try:
        source_connection.execute("PRAGMA busy_timeout = 30000")
        page_size = int(source_connection.execute("PRAGMA page_size").fetchone()[0])
        page_count = int(source_connection.execute("PRAGMA page_count").fetchone()[0])
        if page_size * page_count > max_bytes:
            raise MigrationError(
                f"SQLite 快照超过迁移上限：{source.name}",
                code="entry_too_large",
            )
        destination_connection = sqlite3.connect(str(destination), timeout=30.0)
        try:

            def enforce_size(_status: int, _remaining: int, total: int) -> None:
                if total * page_size > max_bytes:
                    raise MigrationError(
                        f"SQLite 快照超过迁移上限：{source.name}",
                        code="entry_too_large",
                    )

            source_connection.backup(
                destination_connection,
                pages=1024,
                progress=enforce_size,
            )
        except Exception:
            destination_connection.close()
            raise
        destination_connection.commit()
        destination_connection.execute("PRAGMA journal_mode = DELETE")
        verdict = destination_connection.execute("PRAGMA integrity_check").fetchone()
        if verdict is None or str(verdict[0]).lower() != "ok":
            raise MigrationError(
                f"SQLite 快照完整性检查失败：{source.name}", code="sqlite_integrity_failed"
            )
    finally:
        if destination_connection is not None:
            with suppress(Exception):
                destination_connection.close()
        source_connection.close()
    if destination.stat().st_size > max_bytes:
        raise MigrationError(
            f"SQLite 快照超过迁移上限：{source.name}",
            code="entry_too_large",
        )
    _chmod_private(destination)


def _load_project_config(project_root: Path, *, consult_environment: bool) -> Config:
    """Load this project's two disk layers with explicit env semantics."""
    from openbiliclaw.config import _apply_env_overrides, _build_config, _deep_merge

    raw: dict[str, Any] = {}
    for filename in ("config.toml", "config.local.toml"):
        path = project_root / filename
        if path.is_symlink():
            raise MigrationError(
                f"{filename} 是符号链接，不能安全迁移。",
                code="unsafe_config_path",
            )
        if not path.is_file():
            continue
        try:
            with path.open("rb") as handle:
                parsed = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise MigrationError(
                f"目标机 {filename} 无法解析。",
                code="invalid_target_config",
            ) from exc
        raw = _deep_merge(raw, parsed)
    if consult_environment:
        raw = _apply_env_overrides(raw)
    try:
        return _build_config(raw, consult_environment=consult_environment)
    except (TypeError, ValueError) as exc:
        raise MigrationError("目标机当前配置无效。", code="invalid_target_config") from exc


def _stat_signature(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _copy_file_bounded(source: Path, destination: Path, *, max_bytes: int) -> None:
    copied = 0
    with source.open("rb") as source_handle, destination.open("xb") as destination_handle:
        while chunk := source_handle.read(_COPY_CHUNK_BYTES):
            copied += len(chunk)
            if copied > max_bytes:
                raise MigrationError(
                    f"可迁移文件超过大小上限：{source.name}",
                    code="entry_too_large",
                )
            destination_handle.write(chunk)


def _copy_stable_private_file(
    source: Path,
    destination: Path,
    *,
    max_bytes: int = MAX_MIGRATION_FILE_BYTES,
) -> None:
    """Copy a live non-SQLite file only after proving one stable generation."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(_STABLE_COPY_ATTEMPTS):
        temp_path = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.snapshot.tmp")
        try:
            before = source.stat()
            if not stat.S_ISREG(before.st_mode):
                raise MigrationError(
                    f"可迁移数据不是普通文件：{source.name}",
                    code="unsafe_source_file",
                )
            if before.st_size > max_bytes:
                raise MigrationError(
                    f"可迁移文件超过大小上限：{source.name}",
                    code="entry_too_large",
                )
            _copy_file_bounded(source, temp_path, max_bytes=max_bytes)
            after_copy = source.stat()
            stable = _stat_signature(before) == _stat_signature(after_copy)
            if stable and source.suffix.lower() == ".json":
                copied_digest = _sha256_file(temp_path)
                try:
                    with temp_path.open("r", encoding="utf-8-sig") as handle:
                        json.load(handle)
                except (OSError, UnicodeDecodeError, ValueError) as exc:
                    after_parse = source.stat()
                    if _stat_signature(after_copy) != _stat_signature(after_parse):
                        stable = False
                    else:
                        raise MigrationError(
                            f"JSON 数据文件无法解析：{source.name}",
                            code="invalid_source_json",
                        ) from exc
                if stable:
                    source_digest = _sha256_file(source)
                    after_hash = source.stat()
                    stable = (
                        _stat_signature(after_copy) == _stat_signature(after_hash)
                        and copied_digest == source_digest
                    )
            if stable:
                _chmod_private(temp_path)
                os.replace(temp_path, destination)
                return
        except FileNotFoundError:
            # Atomic writers may replace a path between stat and copy. Retry a
            # bounded number of times instead of exporting a mixed generation.
            pass
        finally:
            temp_path.unlink(missing_ok=True)
        if attempt + 1 < _STABLE_COPY_ATTEMPTS:
            time.sleep(0.025 * (attempt + 1))
    raise MigrationError(
        f"导出时文件持续变化，无法取得一致快照：{source.name}",
        code="unstable_source_file",
    )


def _entry_metadata(path: Path, archive_name: str, *, kind: str) -> dict[str, object]:
    return {
        "path": archive_name,
        "kind": kind,
        "size": path.stat().st_size,
        "sha256": _sha256_file(path),
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_COPY_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _stage_tree_seal(root: Path) -> dict[str, object]:
    """Return a deterministic digest for every staged regular file."""
    entries: list[dict[str, object]] = []
    total_bytes = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise MigrationError("暂存目录不能包含符号链接。", code="staging_tampered")
        if path.is_dir():
            continue
        if not path.is_file():
            raise MigrationError("暂存目录包含特殊文件。", code="staging_tampered")
        before = path.stat()
        digest = _sha256_file(path)
        after = path.stat()
        if _stat_signature(before) != _stat_signature(after):
            raise MigrationError("暂存文件在校验时发生变化。", code="staging_tampered")
        relative = path.relative_to(root).as_posix()
        entries.append({"path": relative, "size": after.st_size, "sha256": digest})
        total_bytes += after.st_size
        if len(entries) > MAX_MIGRATION_FILES or total_bytes > MAX_MIGRATION_UNCOMPRESSED_BYTES:
            raise MigrationError("暂存迁移数据超出安全上限。", code="staging_tampered")
    encoded = json.dumps(entries, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return {
        "sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        "file_count": len(entries),
        "total_bytes": total_bytes,
    }


def _verify_stage_tree_seal(root: Path, expected: object) -> None:
    if not isinstance(expected, dict):
        raise MigrationError("暂存迁移缺少完整性封印。", code="staging_tampered")
    actual = _stage_tree_seal(root)
    if actual != expected:
        raise MigrationError(
            "暂存迁移内容在重启前发生变化，已拒绝应用。",
            code="staging_tampered",
        )


def _copy_private_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    _chmod_private(destination)


def _write_private_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _chmod_private(path)


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _chmod_private(Path(temp_name))
        os.replace(temp_name, path)
        _fsync_directory(path.parent)
    finally:
        Path(temp_name).unlink(missing_ok=True)


def _read_json_file(path: Path) -> object:
    try:
        if not path.is_file() or path.stat().st_size > _MAX_STATE_JSON_BYTES:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_and_validate_manifest(
    archive_path: Path,
) -> tuple[dict[str, object], dict[str, zipfile.ZipInfo]]:
    try:
        archive = zipfile.ZipFile(archive_path)
    except zipfile.BadZipFile as exc:
        raise MigrationError("文件不是有效的 OpenBiliClaw 迁移包。", code="bad_zip") from exc
    with archive:
        infos: dict[str, zipfile.ZipInfo] = {}
        portable_info_names: set[str] = set()
        total_size = 0
        archive_infos = archive.infolist()
        if len(archive_infos) > MAX_MIGRATION_FILES + 1:
            raise MigrationError("迁移包文件数量过多。", code="too_many_entries")
        for info in archive_infos:
            name = _validate_archive_member(info)
            if info.is_dir():
                raise MigrationError(f"迁移包不能包含目录 entry：{name}", code="directory_entry")
            if name in infos:
                raise MigrationError(f"迁移包含重复文件：{name}", code="duplicate_entry")
            portable_name = _portable_path_key(name)
            if portable_name in portable_info_names:
                raise MigrationError(f"迁移包含跨平台冲突路径：{name}", code="duplicate_entry")
            portable_info_names.add(portable_name)
            infos[name] = info
            total_size += int(info.file_size)
            if len(infos) > MAX_MIGRATION_FILES + 1:
                raise MigrationError("迁移包文件数量过多。", code="too_many_entries")
            if info.file_size > MAX_MIGRATION_FILE_BYTES:
                raise MigrationError(f"迁移包内文件过大：{name}", code="entry_too_large")
            if total_size > MAX_MIGRATION_UNCOMPRESSED_BYTES + _MAX_MANIFEST_BYTES:
                raise MigrationError("迁移包解压后体积过大。", code="archive_expands_too_large")
        manifest_info = infos.get("manifest.json")
        if manifest_info is None or manifest_info.file_size > _MAX_MANIFEST_BYTES:
            raise MigrationError("迁移包缺少有效 manifest.json。", code="missing_manifest")
        try:
            manifest = json.loads(archive.read(manifest_info).decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RuntimeError) as exc:
            raise MigrationError("manifest.json 无法解析。", code="invalid_manifest") from exc
    if not isinstance(manifest, dict):
        raise MigrationError("manifest.json 顶层必须是对象。", code="invalid_manifest")
    if manifest.get("format") != MIGRATION_FORMAT:
        raise MigrationError("不是 OpenBiliClaw 用户数据迁移包。", code="wrong_format")
    if manifest.get("format_version") != MIGRATION_FORMAT_VERSION:
        raise MigrationError("暂不支持这个迁移包格式版本。", code="unsupported_format")
    source_version = manifest.get("source_version")
    if not isinstance(source_version, str) or _VERSION_RE.fullmatch(source_version) is None:
        raise MigrationError("迁移包来源版本无效。", code="invalid_source_version")
    if manifest.get("encrypted") is not False:
        raise MigrationError("当前版本不支持加密迁移包。", code="unsupported_encryption")

    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise MigrationError("manifest entries 无效。", code="invalid_manifest")
    if len(entries) > MAX_MIGRATION_FILES:
        raise MigrationError("manifest 文件数量过多。", code="too_many_entries")
    manifest_entries: dict[str, dict[str, object]] = {}
    portable_manifest_names: set[str] = set()
    for raw_entry in entries:
        if not isinstance(raw_entry, dict):
            raise MigrationError("manifest entry 无效。", code="invalid_manifest")
        name = _validate_logical_payload_path(raw_entry.get("path"))
        if name in manifest_entries:
            raise MigrationError(f"manifest 含重复路径：{name}", code="duplicate_entry")
        portable_name = _portable_path_key(name)
        if portable_name in portable_manifest_names:
            raise MigrationError(f"manifest 含跨平台冲突路径：{name}", code="duplicate_entry")
        portable_manifest_names.add(portable_name)
        size = raw_entry.get("size")
        digest = raw_entry.get("sha256")
        if (
            not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
            or size > MAX_MIGRATION_FILE_BYTES
            or not isinstance(digest, str)
            or _SHA256_RE.fullmatch(digest) is None
        ):
            raise MigrationError(f"manifest 文件元数据无效：{name}", code="invalid_manifest")
        manifest_entries[name] = raw_entry
    if "config/config.toml" not in manifest_entries:
        raise MigrationError("迁移包缺少 config.toml。", code="missing_config")
    expected = {"manifest.json", *manifest_entries}
    if set(infos) != expected:
        extra = sorted(set(infos) - expected)
        missing = sorted(expected - set(infos))
        detail = f"未知文件 {extra}" if extra else f"缺少文件 {missing}"
        raise MigrationError(f"迁移包文件清单不一致：{detail}", code="entry_list_mismatch")
    for name, entry in manifest_entries.items():
        if infos[name].file_size != entry["size"]:
            raise MigrationError(f"文件大小与 manifest 不一致：{name}", code="size_mismatch")
    return manifest, infos


def _validate_archive_member(info: zipfile.ZipInfo) -> str:
    name = info.filename
    _validate_safe_relative_name(name.rstrip("/"))
    if info.flag_bits & 0x1:
        raise MigrationError(f"不支持加密 ZIP entry：{name}", code="encrypted_entry")
    if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
        raise MigrationError(f"迁移包使用不支持的压缩算法：{name}", code="unsupported_compression")
    mode = (info.external_attr >> 16) & 0xFFFF
    if mode and stat.S_ISLNK(mode):
        raise MigrationError(f"迁移包不能包含符号链接：{name}", code="symlink_entry")
    if mode and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
        raise MigrationError(f"迁移包包含特殊文件：{name}", code="special_entry")
    return name.rstrip("/")


def _portable_path_key(value: str) -> str:
    """Normalize a logical path for case-insensitive/canonical filesystems."""
    return unicodedata.normalize("NFC", value).casefold()


def _validate_logical_payload_path(value: object) -> str:
    if not isinstance(value, str):
        raise MigrationError("manifest 路径必须是字符串。", code="invalid_manifest")
    name = _validate_safe_relative_name(value)
    if name in _ALLOWED_CONFIG_ARCHIVE_PATHS or name == _ALLOWED_FRONTEND_ARCHIVE_PATH:
        return name
    if name.startswith("data/") and len(PurePosixPath(name).parts) >= 2:
        relative = Path(*PurePosixPath(name).parts[1:])
        root_name = relative.parts[0].casefold()
        leaf_name = relative.name.casefold()
        if root_name in _EXCLUDED_DATA_ROOTS or leaf_name in _EXCLUDED_DATA_FILES:
            raise MigrationError(f"迁移包包含不允许的数据项：{name}", code="disallowed_entry")
        if leaf_name.endswith(_TRANSIENT_SUFFIXES):
            raise MigrationError(f"迁移包包含临时文件：{name}", code="disallowed_entry")
        return name
    raise MigrationError(f"迁移包包含未知路径：{name}", code="disallowed_entry")


def _validate_safe_relative_name(value: str) -> str:
    if not value or len(value) > MAX_MIGRATION_PATH_LENGTH:
        raise MigrationError("迁移包路径为空或过长。", code="unsafe_path")
    if "\\" in value or "\x00" in value or any(ord(char) < 32 for char in value):
        raise MigrationError(f"迁移包路径含非法字符：{value!r}", code="unsafe_path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise MigrationError(f"迁移包路径不安全：{value}", code="unsafe_path")
    unsafe_windows_part = any(
        any(char in '<>:"|?*' for char in part)
        or part.rstrip(" .") != part
        or part.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES
        for part in path.parts
    )
    if len(path.parts) > MAX_MIGRATION_PATH_DEPTH or unsafe_windows_part:
        raise MigrationError(f"迁移包路径不安全：{value}", code="unsafe_path")
    return path.as_posix()


def _extract_verified_entries(
    archive_path: Path,
    destination_root: Path,
    manifest: Mapping[str, object],
    infos: Mapping[str, zipfile.ZipInfo],
) -> None:
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise MigrationError("manifest entries 无效。", code="invalid_manifest")
    total_written = 0
    with zipfile.ZipFile(archive_path) as archive:
        for entry in entries:
            if not isinstance(entry, dict):
                raise MigrationError("manifest entry 无效。", code="invalid_manifest")
            name = str(entry["path"])
            target = destination_root / Path(*PurePosixPath(name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            written = 0
            try:
                with archive.open(infos[name]) as source, target.open("xb") as destination:
                    while chunk := source.read(_COPY_CHUNK_BYTES):
                        written += len(chunk)
                        total_written += len(chunk)
                        if (
                            written > int(entry["size"])
                            or total_written > MAX_MIGRATION_UNCOMPRESSED_BYTES
                        ):
                            raise MigrationError(
                                f"迁移包解压体积超出声明：{name}", code="archive_expands_too_large"
                            )
                        digest.update(chunk)
                        destination.write(chunk)
            except (EOFError, RuntimeError, zipfile.BadZipFile) as exc:
                raise MigrationError(f"迁移包文件损坏：{name}", code="corrupt_entry") from exc
            _chmod_private(target)
            if written != entry["size"] or digest.hexdigest() != entry["sha256"]:
                raise MigrationError(f"迁移包哈希校验失败：{name}", code="checksum_mismatch")


def _load_frontend_settings(stage_root: Path) -> dict[str, object]:
    path = stage_root / _ALLOWED_FRONTEND_ARCHIVE_PATH
    if not path.is_file():
        return {}
    if path.stat().st_size > _MAX_FRONTEND_SETTINGS_BYTES:
        raise MigrationError("前端设置文件过大。", code="invalid_frontend_settings")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise MigrationError("前端设置文件无效。", code="invalid_frontend_settings") from exc
    if not isinstance(value, dict):
        raise MigrationError("前端设置文件无效。", code="invalid_frontend_settings")
    return normalize_frontend_settings(value)


def _normalize_imported_config(
    stage_root: Path,
    current: Config,
) -> tuple[Config, tuple[str, ...]]:
    from openbiliclaw.config import _build_config, _deep_merge

    raw: dict[str, Any] = {}
    for name in ("config.toml", "config.local.toml"):
        path = stage_root / "config" / name
        if not path.is_file():
            continue
        try:
            with path.open("rb") as handle:
                parsed = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise MigrationError(f"{name} 无法解析。", code="invalid_config") from exc
        raw = _deep_merge(raw, parsed)
    try:
        candidate = _build_config(raw, consult_environment=False)
    except (TypeError, ValueError) as exc:
        raise MigrationError(f"导入配置无效：{exc}", code="invalid_config") from exc
    return _preserve_target_machine_config(candidate, current)


def _load_config_file(path: Path) -> Config:
    from openbiliclaw.config import _build_config

    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
        return _build_config(raw, consult_environment=False)
    except (OSError, TypeError, ValueError, tomllib.TOMLDecodeError) as exc:
        raise MigrationError("暂存配置无法解析。", code="invalid_config") from exc


def _preserve_target_machine_config(
    candidate: Config,
    current: Config,
) -> tuple[Config, tuple[str, ...]]:
    from openbiliclaw.config import _collect_config_issues

    # Keep machine identity and filesystem/network wiring from the destination.
    # Portable service credentials, models, source switches, and schedules are
    # imported. The Web password/trust boundary remains destination-owned; only
    # its session signing secret and paired extension devices are intentionally
    # revoked so neither machine's old sessions survive the move.
    candidate.data_dir = current.data_dir
    candidate.storage = copy.deepcopy(current.storage)
    candidate.api.host = current.api.host
    candidate.api.port = current.api.port
    candidate.api.auth = copy.deepcopy(current.api.auth)
    candidate.logging.directory = current.logging.directory
    candidate.logging.filename = current.logging.filename
    candidate.network = copy.deepcopy(current.network)
    candidate.tls_proxy = copy.deepcopy(current.tls_proxy)
    candidate.tailnet = copy.deepcopy(current.tailnet)
    candidate.autostart = copy.deepcopy(current.autostart)
    candidate.sources.browser_cdp_url = current.sources.browser_cdp_url
    candidate.bilibili.proxy = current.bilibili.proxy
    candidate.bilibili.browser_executable = current.bilibili.browser_executable
    candidate.api.auth.session_secret = secrets.token_urlsafe(32)
    candidate.api.auth.extension_access_enabled = False
    candidate.api.auth.extension_access_keys = []
    blocking = [
        issue for issue in _collect_config_issues(candidate) if issue.severity == "blocking"
    ]
    if blocking:
        detail = "；".join(f"{issue.field}: {issue.message}" for issue in blocking[:8])
        raise MigrationError(f"导入配置无法启动：{detail}", code="invalid_config")
    adjusted = (
        "data_dir",
        "storage.db_path",
        "api.host",
        "api.port",
        "api.auth",
        "logging.directory",
        "logging.filename",
        "network",
        "tls_proxy",
        "tailnet",
        "autostart",
        "sources.browser_cdp_url",
        "bilibili.proxy",
        "bilibili.browser_executable",
        "api.auth.session_secret",
        "api.auth.extension_access",
    )
    return candidate, adjusted


def _config_data_path(project_root: Path, config: Config) -> Path:
    path = Path(config.data_dir).expanduser()
    if not path.is_absolute():
        path = project_root / path
    path = Path(os.path.abspath(path))
    if path.is_symlink():
        raise MigrationError("data_dir 不能是符号链接。", code="unsafe_data_dir")
    return path.resolve()


def _validate_target_config_files(project_root: Path) -> None:
    if any((project_root / name).is_symlink() for name in ("config.toml", "config.local.toml")):
        raise MigrationError("目标配置文件不能是符号链接。", code="unsafe_target")


def _validate_imported_databases(data_root: Path) -> None:
    if not data_root.is_dir():
        return
    for path in data_root.rglob("*"):
        if not path.is_file() or not _looks_like_sqlite(path):
            continue
        try:
            connection = sqlite3.connect(_sqlite_readonly_uri(path), uri=True, timeout=30.0)
            try:
                verdict = connection.execute("PRAGMA integrity_check").fetchone()
            finally:
                connection.close()
        except sqlite3.DatabaseError as exc:
            raise MigrationError(
                f"数据库完整性检查失败：{path.name}", code="sqlite_integrity_failed"
            ) from exc
        if verdict is None or str(verdict[0]).lower() != "ok":
            raise MigrationError(
                f"数据库完整性检查失败：{path.name}", code="sqlite_integrity_failed"
            )


def _safe_stage_path(migration_root: Path, value: object) -> Path | None:
    if not isinstance(value, str) or not re.fullmatch(r"pending-[0-9a-f]{32}", value):
        return None
    candidate = migration_root / value
    try:
        if candidate.resolve().parent != migration_root.resolve() or candidate.is_symlink():
            return None
    except OSError:
        return None
    return candidate


def _reconcile_orphan_stage_directories(migration_root: Path) -> None:
    """Delete strictly named staging trees not referenced by marker or journal."""
    keep: set[Path] = set()
    for metadata_name in (_PENDING_MARKER, _APPLY_JOURNAL):
        metadata = _read_json_file(migration_root / metadata_name)
        if not isinstance(metadata, dict):
            continue
        stage = _safe_stage_path(migration_root, metadata.get("stage_dir"))
        if stage is not None:
            keep.add(stage)

    removed = False
    try:
        candidates = list(migration_root.iterdir())
    except FileNotFoundError:
        return
    for candidate in candidates:
        if not re.fullmatch(r"(?:incoming|pending)-[0-9a-f]{32}", candidate.name):
            continue
        if candidate in keep:
            continue
        try:
            if candidate.is_dir() and not candidate.is_symlink():
                shutil.rmtree(candidate)
            else:
                candidate.unlink(missing_ok=True)
            removed = True
        except OSError as exc:
            raise MigrationError(
                "无法清理上一次迁移遗留的敏感暂存副本。",
                code="orphan_cleanup_failed",
            ) from exc
    if removed:
        _fsync_directory(migration_root)


def _validated_target_path(value: object, expected: Path) -> Path:
    candidate = _validated_absolute_path(value)
    if candidate != expected.resolve():
        raise MigrationError("迁移目标配置路径不匹配。", code="unsafe_target")
    return candidate


def _validated_absolute_path(value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise MigrationError("迁移目标路径无效。", code="unsafe_target")
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        raise MigrationError("迁移目标必须是绝对路径。", code="unsafe_target")
    return candidate.resolve()


def _validate_distinct_apply_paths(*paths: Path) -> None:
    """Reject a data_dir name that aliases any transaction artifact role."""
    canonical = [path.resolve() for path in paths]
    portable = [_portable_path_key(path.as_posix()) for path in canonical]
    if len(set(portable)) != len(portable):
        raise MigrationError(
            "data_dir 与迁移事务文件名冲突，不能安全替换。",
            code="unsafe_data_dir",
        )


def _validated_apply_journal_paths(
    migration_root: Path,
    journal: Mapping[str, object],
) -> _ApplyJournalPaths:
    """Bind every journal path to the names this migration could create."""
    project_root = migration_root.parent.resolve()
    migration_id = journal.get("migration_id")
    if not isinstance(migration_id, str) or re.fullmatch(r"[0-9a-f]{32}", migration_id) is None:
        raise MigrationError("迁移恢复日志的 ID 无效。", code="corrupt_apply_journal")
    token = migration_id[:12]
    _validate_target_config_files(project_root)
    target_config = _validated_target_path(
        journal.get("target_config"), project_root / "config.toml"
    )
    target_local = _validated_target_path(
        journal.get("target_local"),
        project_root / "config.local.toml",
    )
    raw_target_data = journal.get("target_data")
    if isinstance(raw_target_data, str) and Path(raw_target_data).is_symlink():
        raise MigrationError("迁移目标不能是符号链接。", code="corrupt_apply_journal")
    target_data = _validated_absolute_path(raw_target_data)
    _validate_migration_data_dir(project_root, target_data)

    expected = {
        "prepared_config": target_config.with_name(f".{target_config.name}.import-{token}"),
        "prepared_data": target_data.with_name(f".{target_data.name}.import-{token}"),
        "config_backup": target_config.with_name(f"{target_config.name}.pre-import-{token}.bak"),
        "local_backup": target_local.with_name(f"{target_local.name}.pre-import-{token}.bak"),
        "data_backup": target_data.with_name(f"{target_data.name}.pre-import-{token}.bak"),
    }
    validated: dict[str, Path] = {}
    for field_name, expected_path in expected.items():
        raw_candidate = journal.get(field_name)
        if isinstance(raw_candidate, str) and Path(raw_candidate).is_symlink():
            raise MigrationError(
                "迁移恢复日志指向符号链接；后端拒绝继续启动。",
                code="corrupt_apply_journal",
            )
        candidate = _validated_absolute_path(raw_candidate)
        if candidate != expected_path:
            raise MigrationError(
                "迁移恢复日志包含越界路径；后端拒绝继续启动。",
                code="corrupt_apply_journal",
            )
        validated[field_name] = candidate
    result = _ApplyJournalPaths(
        target_config=target_config,
        target_local=target_local,
        target_data=target_data,
        prepared_config=validated["prepared_config"],
        prepared_data=validated["prepared_data"],
        config_backup=validated["config_backup"],
        local_backup=validated["local_backup"],
        data_backup=validated["data_backup"],
        failed_config=target_config.with_name(f"{target_config.name}.failed-import-{token}"),
        failed_data=target_data.with_name(f"{target_data.name}.failed-import-{token}"),
    )
    _validate_distinct_apply_paths(
        result.target_config,
        result.target_local,
        result.target_data,
        result.prepared_config,
        result.prepared_data,
        result.config_backup,
        result.local_backup,
        result.data_backup,
        result.failed_config,
        result.failed_data,
    )
    return result


def _validated_applied_generation_receipt(
    migration_root: Path,
    status: Mapping[str, object],
) -> _AppliedGenerationReceipt:
    project_root = migration_root.parent.resolve()
    migration_id = status.get("migration_id")
    digest = status.get("_active_config_sha256")
    epoch = status.get("_active_auth_epoch")
    if (
        status.get("state") != "applied"
        or not isinstance(migration_id, str)
        or re.fullmatch(r"[0-9a-f]{32}", migration_id) is None
        or not isinstance(digest, str)
        or _SHA256_RE.fullmatch(digest) is None
        or not isinstance(epoch, int)
        or isinstance(epoch, bool)
        or epoch < 1
    ):
        raise MigrationError(
            "已应用迁移的持久回执无效；后端拒绝继续启动。",
            code="corrupt_apply_receipt",
        )
    target_config = _validated_target_path(
        status.get("_target_config"),
        project_root / "config.toml",
    )
    raw_target_data = status.get("_target_data")
    if isinstance(raw_target_data, str) and Path(raw_target_data).is_symlink():
        raise MigrationError("迁移回执的数据目录不能是符号链接。", code="corrupt_apply_receipt")
    target_data = _validated_absolute_path(raw_target_data)
    _validate_migration_data_dir(project_root, target_data)
    return _AppliedGenerationReceipt(
        migration_id=migration_id,
        target_config=target_config,
        target_data=target_data,
        config_sha256=digest,
        auth_epoch=epoch,
    )


def _verify_active_generation(receipt: _AppliedGenerationReceipt) -> None:
    """Fail closed unless live config and DB are the receipt's exact generation."""
    if not receipt.target_config.is_file() or receipt.target_config.is_symlink():
        raise MigrationError("已应用迁移的活动配置缺失。", code="apply_generation_mismatch")
    target_local = receipt.target_config.with_name("config.local.toml")
    if target_local.exists() or target_local.is_symlink():
        raise MigrationError(
            "已应用迁移仍存在旧的本机配置覆盖层。",
            code="apply_generation_mismatch",
        )
    _load_config_file(receipt.target_config)
    if not _is_openbiliclaw_data_directory(receipt.target_data):
        raise MigrationError("已应用迁移的活动数据不完整。", code="apply_generation_mismatch")
    if (
        _sha256_file(receipt.target_config) != receipt.config_sha256
        or _read_auth_epoch(receipt.target_data / "openbiliclaw.db") != receipt.auth_epoch
    ):
        raise MigrationError(
            "已应用迁移的活动代际与持久回执不一致；后端拒绝误报成功。",
            code="apply_generation_mismatch",
        )


def _applied_status_payload(
    result: MigrationApplyResult,
    *,
    target_config: Path,
    target_data: Path,
    config_sha256: str,
    auth_epoch: int,
) -> dict[str, object]:
    payload = asdict(result)
    payload.update(
        {
            "_target_config": str(target_config),
            "_target_data": str(target_data),
            "_active_config_sha256": config_sha256,
            "_active_auth_epoch": auth_epoch,
        }
    )
    return payload


def _validate_migration_data_dir(project_root: Path, data_dir: Path) -> None:
    root = project_root.resolve()
    target = data_dir.resolve()
    migration_root = _migration_root(root).resolve()
    if (
        target in (root, migration_root)
        or root.is_relative_to(target)
        or target.is_relative_to(migration_root)
        or migration_root.is_relative_to(target)
    ):
        raise MigrationError(
            "data_dir 与项目目录或迁移状态目录重叠，不能安全替换。",
            code="unsafe_data_dir",
        )
    if target.is_relative_to(root):
        relative = target.relative_to(root)
        if relative.parts and relative.parts[0] in _PROJECT_CODE_ROOTS:
            raise MigrationError(
                "data_dir 指向项目代码目录，不能安全替换。",
                code="unsafe_data_dir",
            )
    if target.exists() and not target.is_dir():
        raise MigrationError("data_dir 不是目录。", code="unsafe_data_dir")
    if target.is_dir():
        try:
            nonempty = next(target.iterdir(), None) is not None
        except OSError as exc:
            raise MigrationError("无法检查 data_dir。", code="unsafe_data_dir") from exc
        if nonempty and not _is_openbiliclaw_data_directory(target):
            raise MigrationError(
                "data_dir 看起来不是 OpenBiliClaw 数据目录，已拒绝整目录替换。",
                code="unsafe_data_dir",
            )


def _sqlite_readonly_uri(path: Path) -> str:
    """Return an encoded SQLite URI whose query cannot be changed by a filename."""
    return f"{path.resolve().as_uri()}?mode=ro"


def _is_openbiliclaw_data_directory(path: Path) -> bool:
    """Require a healthy database carrying OpenBiliClaw's core schema."""
    database_path = path / "openbiliclaw.db"
    if not database_path.is_file() or database_path.is_symlink():
        return False
    try:
        connection = sqlite3.connect(
            _sqlite_readonly_uri(database_path),
            uri=True,
            timeout=30.0,
        )
        try:
            verdict = connection.execute("PRAGMA integrity_check").fetchone()
            rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        finally:
            connection.close()
    except (OSError, sqlite3.DatabaseError):
        return False
    tables = {str(row[0]) for row in rows}
    return (
        verdict is not None
        and str(verdict[0]).lower() == "ok"
        and {"events", "schema_version"}.issubset(tables)
    )


def _preserve_target_machine_data(target_data: Path, prepared_data: Path) -> bool:
    """Preserve destination-bound state and report whether its helper was copied."""
    if not target_data.is_dir():
        return False
    for name in _PRESERVED_TARGET_ROOTS:
        source = target_data / name
        destination = prepared_data / name
        if not source.is_dir() or source.is_symlink() or destination.exists():
            continue
        # Never dereference machine-local links while preserving state. Besides
        # escaping the data root, a link could make an import copy an unbounded
        # external tree. Preserve links as links during the copy, detect them
        # before chmod/rglob can follow them, and fail the staged transaction.
        shutil.copytree(source, destination, symlinks=True)
        if _tree_contains_symlink(destination):
            shutil.rmtree(destination)
            raise MigrationError(
                f"目标机器的 data/{name} 包含符号链接；为避免复制目录外数据，迁移已拒绝。",
                code="unsafe_target_state",
            )
        _privatize_tree(destination)

    # ``data/bin`` is never portable: accepting a helper from an archive would
    # turn import into a cross-platform code-execution surface. Preserve only
    # the exact, native helper already trusted by the destination machine.
    source_bin = target_data / "bin"
    source_helper = source_bin / _TAILNET_HELPER_BASENAME
    destination_bin = prepared_data / "bin"
    destination_helper = destination_bin / _TAILNET_HELPER_BASENAME
    helper_mode = 0
    with suppress(OSError):
        helper_mode = source_helper.lstat().st_mode
    helper_is_trusted = stat.S_ISREG(helper_mode) and (
        os.name == "nt"
        or (
            bool(helper_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
            and os.access(source_helper, os.X_OK)
        )
    )
    if (
        source_bin.is_dir()
        and not source_bin.is_symlink()
        and helper_is_trusted
        and not destination_bin.exists()
        and not destination_bin.is_symlink()
    ):
        destination_bin.mkdir(mode=0o700)
        shutil.copy2(source_helper, destination_helper)
        _chmod_private(destination_helper)
        _restore_tailnet_helper_mode(prepared_data, trusted_copy=True)
        return True
    return False


def _tree_contains_symlink(root: Path) -> bool:
    """Return whether a copied tree contains any link without following it."""
    for current, directory_names, file_names in os.walk(root, followlinks=False):
        current_path = Path(current)
        if any((current_path / name).is_symlink() for name in directory_names):
            return True
        if any((current_path / name).is_symlink() for name in file_names):
            return True
    return False


def _read_auth_epoch(database_path: Path) -> int:
    if not database_path.is_file():
        return 0
    try:
        connection = sqlite3.connect(_sqlite_readonly_uri(database_path), uri=True, timeout=30.0)
        try:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'auth_state'"
            ).fetchone()
            if table is None:
                return 0
            row = connection.execute(
                "SELECT value FROM auth_state WHERE key = 'auth_epoch'"
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return 0
        value = int(row[0])
        if value < 0:
            raise ValueError("negative auth epoch")
        return value
    except (OSError, sqlite3.DatabaseError, TypeError, ValueError) as exc:
        raise MigrationError(
            "目标或来源数据库的认证撤销状态已损坏。",
            code="invalid_auth_epoch",
        ) from exc


def _smoke_test_prepared_database(prepared_data: Path, target_data: Path) -> int:
    """Run current schema initialization on the copy before any live rename."""
    database_path = prepared_data / "openbiliclaw.db"
    target_epoch = _read_auth_epoch(target_data / "openbiliclaw.db")
    replacement_epoch = 0
    from openbiliclaw.storage.database import Database

    database = Database(database_path)
    try:
        database.initialize()
        source_epoch = database.get_auth_epoch()
        # Session signing secrets may be environment-managed and impossible to
        # rotate on disk. Tokens with an epoch at or above the DB epoch are
        # accepted, so revocation must be monotonic rather than random: advance
        # strictly beyond both machines' durable epochs. The normal startup
        # reconcile records the destination password fingerprint again.
        replacement_epoch = max(source_epoch, target_epoch) + 1
        connection = database.open_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR REPLACE INTO auth_state (key, value) VALUES ('auth_epoch', ?)",
                (str(replacement_epoch),),
            )
            connection.execute("DELETE FROM auth_state WHERE key = 'password_fingerprint'")
            connection.commit()
        finally:
            connection.close()
    except Exception as exc:
        raise MigrationError(
            "迁移数据库与当前版本不兼容。",
            code="incompatible_database",
        ) from exc
    finally:
        with suppress(Exception):
            database.close()
    try:
        connection = sqlite3.connect(str(database_path), timeout=30.0)
        try:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.execute("PRAGMA journal_mode = DELETE")
            connection.commit()
        finally:
            connection.close()
        database_path.with_name(f"{database_path.name}-wal").unlink(missing_ok=True)
        database_path.with_name(f"{database_path.name}-shm").unlink(missing_ok=True)
        _validate_imported_databases(prepared_data)
    except (OSError, sqlite3.DatabaseError) as exc:
        raise MigrationError(
            "迁移数据库在兼容性检查后无法封存。",
            code="incompatible_database",
        ) from exc
    return replacement_epoch


def _remove_migration_artifact(path: Path) -> None:
    try:
        _delete_migration_artifact(path)
    except OSError:
        logger.warning("Unable to prune old migration artifact: %s", path, exc_info=True)


def _delete_migration_artifact(path: Path) -> None:
    """Strictly remove one already-validated migration artifact path."""
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _prune_old_migration_artifacts(
    *,
    target_config: Path,
    target_local: Path,
    target_data: Path,
    keep: set[Path],
) -> None:
    """Keep only this successful import's rollback copy for each target."""
    protected = {target_config, target_local, target_data, *keep}
    specifications = (
        (target_config.parent, rf"{re.escape(target_config.name)}\.pre-import-[0-9a-f]{{12}}\.bak"),
        (target_config.parent, rf"{re.escape(target_config.name)}\.failed-import-[0-9a-f]{{12}}"),
        (target_local.parent, rf"{re.escape(target_local.name)}\.pre-import-[0-9a-f]{{12}}\.bak"),
        (target_local.parent, rf"{re.escape(target_local.name)}\.failed-import-[0-9a-f]{{12}}"),
        (target_data.parent, rf"{re.escape(target_data.name)}\.pre-import-[0-9a-f]{{12}}\.bak"),
        (target_data.parent, rf"{re.escape(target_data.name)}\.failed-import-[0-9a-f]{{12}}"),
        (target_config.parent, rf"\.{re.escape(target_config.name)}\.import-[0-9a-f]{{12}}"),
        (target_data.parent, rf"\.{re.escape(target_data.name)}\.import-[0-9a-f]{{12}}"),
    )
    for parent, name_pattern in specifications:
        with suppress(OSError):
            for path in parent.iterdir():
                if re.fullmatch(name_pattern, path.name) is None:
                    continue
                # A user may legitimately choose a data_dir whose basename
                # resembles our backup namespace. Never prune any active target,
                # even when its name exactly matches the generated token shape.
                if path not in protected:
                    _remove_migration_artifact(path)


def _privatize_tree(root: Path) -> None:
    if not root.exists():
        return
    _chmod_private(root, directory=True)
    for path in root.rglob("*"):
        _chmod_private(path, directory=path.is_dir())


def _restore_tailnet_helper_mode(data_root: Path, *, trusted_copy: bool) -> None:
    """Restore execute permission only on the trusted target helper copy."""
    if os.name == "nt" or not trusted_copy:
        return
    helper = data_root / "bin" / _TAILNET_HELPER_BASENAME
    if helper.is_file() and not helper.is_symlink():
        with suppress(OSError):
            helper.chmod(0o700)


def _fsync_file(path: Path) -> None:
    try:
        with path.open("rb") as handle:
            os.fsync(handle.fileno())
    except OSError:
        # Some platforms/filesystems do not expose fsync for every file type.
        pass


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _fsync_tree(root: Path) -> None:
    directories = [root]
    for path in root.rglob("*"):
        if path.is_dir():
            directories.append(path)
        elif path.is_file():
            _fsync_file(path)
    for directory in reversed(directories):
        _fsync_directory(directory)


def _chmod_private(path: Path, *, directory: bool = False) -> None:
    with suppress(OSError):
        path.chmod(0o700 if directory else 0o600)


def _recover_interrupted_apply(
    migration_root: Path,
    *,
    locked_data_dir: Path | None = None,
) -> MigrationApplyResult | None:
    journal_path = migration_root / _APPLY_JOURNAL
    if not journal_path.exists():
        return None
    journal = _read_json_file(journal_path)
    if not isinstance(journal, dict):
        raise MigrationError(
            "迁移恢复日志已损坏；为避免覆盖数据，后端拒绝继续启动。",
            code="corrupt_apply_journal",
        )
    paths = _validated_apply_journal_paths(migration_root, journal)
    journal_state = journal.get("state")
    if journal_state not in {"preparing", "applying", "rolling_back", "committed"}:
        raise MigrationError(
            "迁移恢复日志状态无效；后端拒绝继续启动。",
            code="corrupt_apply_journal",
        )
    if (
        locked_data_dir is not None
        and paths.target_data != Path(locked_data_dir).expanduser().resolve()
    ):
        raise MigrationError(
            "上一次迁移的数据目录未被当前进程锁定；为避免与另一后端并发写入，后端拒绝恢复。",
            code="migration_lock_mismatch",
        )
    if journal_state == "preparing":
        try:
            paths.prepared_config.unlink(missing_ok=True)
            if paths.prepared_data.exists():
                shutil.rmtree(paths.prepared_data)
            _fsync_directory(paths.prepared_config.parent)
            _fsync_directory(paths.prepared_data.parent)
            journal_path.unlink(missing_ok=True)
            _fsync_directory(migration_root)
        except Exception as exc:
            raise MigrationError(
                "上一次迁移准备阶段中断且临时数据清理失败；后端拒绝继续启动。",
                code="apply_recovery_failed",
            ) from exc
        return None
    if journal_state == "committed":
        expected_config_digest = journal.get("active_config_sha256")
        expected_auth_epoch = journal.get("active_auth_epoch")
        if (
            not isinstance(expected_config_digest, str)
            or _SHA256_RE.fullmatch(expected_config_digest) is None
            or not isinstance(expected_auth_epoch, int)
            or isinstance(expected_auth_epoch, bool)
            or expected_auth_epoch < 1
        ):
            raise MigrationError(
                "已提交迁移缺少活动代际证明；后端拒绝继续启动。",
                code="corrupt_apply_journal",
            )
        _verify_active_generation(
            _AppliedGenerationReceipt(
                migration_id=str(journal.get("migration_id", "")),
                target_config=paths.target_config,
                target_data=paths.target_data,
                config_sha256=expected_config_digest,
                auth_epoch=expected_auth_epoch,
            )
        )
        migration_id = str(journal.get("migration_id", ""))
        source_version = str(journal.get("source_version", ""))
        status = _read_json_file(migration_root / _STATUS_FILE)
        config_backup_receipt = str(journal.get("config_backup", ""))
        data_backup_receipt = str(journal.get("data_backup", ""))
        result = MigrationApplyResult(
            state="applied",
            migration_id=migration_id,
            source_version=source_version,
            applied_at=(
                str(status.get("applied_at", ""))
                if isinstance(status, dict) and status.get("migration_id") == migration_id
                else str(journal.get("applied_at", "")) or datetime.now(UTC).isoformat()
            ),
            message="迁移数据已应用；旧配置和数据保留为回滚副本。",
            config_backup=(
                config_backup_receipt
                if config_backup_receipt and Path(config_backup_receipt).exists()
                else ""
            ),
            data_backup=(
                data_backup_receipt
                if data_backup_receipt and Path(data_backup_receipt).exists()
                else ""
            ),
            frontend_settings=normalize_frontend_settings(
                journal.get("frontend_settings")
                if isinstance(journal.get("frontend_settings"), dict)
                else {}
            ),
        )
        _atomic_write_json(
            migration_root / _STATUS_FILE,
            _applied_status_payload(
                result,
                target_config=paths.target_config,
                target_data=paths.target_data,
                config_sha256=expected_config_digest,
                auth_epoch=expected_auth_epoch,
            ),
        )
        marker_path = migration_root / _PENDING_MARKER
        marker = _read_json_file(marker_path)
        stage_dir = _safe_stage_path(migration_root, journal.get("stage_dir"))
        if isinstance(marker, dict) and marker.get("migration_id") == migration_id:
            marker_stage = _safe_stage_path(migration_root, marker.get("stage_dir"))
            if marker_stage is not None:
                stage_dir = marker_stage
        try:
            if stage_dir is not None and stage_dir.exists():
                shutil.rmtree(stage_dir)
            if isinstance(marker, dict) and marker.get("migration_id") == migration_id:
                marker_path.unlink(missing_ok=True)
            _fsync_directory(migration_root)
            journal_path.unlink(missing_ok=True)
        except OSError as exc:
            raise MigrationError(
                "迁移已应用，但敏感暂存副本尚未清理；后端拒绝继续启动。",
                code="apply_recovery_failed",
            ) from exc
        _fsync_directory(migration_root)
        with suppress(Exception):
            _prune_old_migration_artifacts(
                target_config=paths.target_config,
                target_local=paths.target_local,
                target_data=paths.target_data,
                keep={
                    paths.config_backup,
                    paths.local_backup,
                    paths.data_backup,
                },
            )
        return result
    try:

        def journal_boolean(field_name: str) -> bool:
            value = journal.get(field_name)
            if not isinstance(value, bool):
                raise MigrationError(
                    "迁移恢复日志字段无效；后端拒绝继续启动。",
                    code="corrupt_apply_journal",
                )
            return value

        config_was_present = journal_boolean("target_config_existed")
        local_was_present = journal_boolean("target_local_existed")
        data_was_present = journal_boolean("target_data_existed")

        if journal_state != "rolling_back":
            # Filesystem replacement can complete immediately before the next
            # journal update. Freeze the inference into a rollback-specific
            # state *before* mutating anything so a second crash never mistakes
            # a restored old target for the imported target.
            inferred_new_config = journal_boolean("new_config_active") or (
                not paths.prepared_config.exists()
                and paths.target_config.exists()
                and (not config_was_present or paths.config_backup.exists())
            )
            inferred_new_data = journal_boolean("new_data_active") or (
                not paths.prepared_data.exists()
                and paths.target_data.exists()
                and (not data_was_present or paths.data_backup.exists())
            )
            journal.update(
                {
                    "state": "rolling_back",
                    "rollback_new_config_active": inferred_new_config,
                    "rollback_new_data_active": inferred_new_data,
                    "rollback_config_quarantined": not inferred_new_config,
                    "rollback_data_quarantined": not inferred_new_data,
                    "rollback_config_restored": False,
                    "rollback_local_restored": False,
                    "rollback_data_restored": False,
                }
            )
            _atomic_write_json(journal_path, journal)
        else:
            for field_name in (
                "rollback_new_config_active",
                "rollback_new_data_active",
                "rollback_config_quarantined",
                "rollback_data_quarantined",
                "rollback_config_restored",
                "rollback_local_restored",
                "rollback_data_restored",
            ):
                journal_boolean(field_name)

        def record_step(field_name: str) -> None:
            journal[field_name] = True
            _atomic_write_json(journal_path, journal)

        if not bool(journal.get("rollback_config_quarantined")):
            if paths.failed_config.exists():
                if paths.target_config.exists():
                    raise MigrationError(
                        "配置回滚状态冲突；后端拒绝继续启动。",
                        code="apply_recovery_failed",
                    )
            elif paths.target_config.exists():
                os.replace(paths.target_config, paths.failed_config)
                _fsync_directory(paths.target_config.parent)
            record_step("rollback_config_quarantined")

        if not bool(journal.get("rollback_data_quarantined")):
            if paths.failed_data.exists():
                if paths.target_data.exists():
                    raise MigrationError(
                        "数据回滚状态冲突；后端拒绝继续启动。",
                        code="apply_recovery_failed",
                    )
            elif paths.target_data.exists():
                os.replace(paths.target_data, paths.failed_data)
                _fsync_directory(paths.target_data.parent)
            record_step("rollback_data_quarantined")

        if not bool(journal.get("rollback_config_restored")):
            if config_was_present:
                if paths.config_backup.exists():
                    if paths.target_config.exists():
                        raise MigrationError(
                            "配置回滚目标被占用；后端拒绝继续启动。",
                            code="apply_recovery_failed",
                        )
                    os.replace(paths.config_backup, paths.target_config)
                    _fsync_directory(paths.target_config.parent)
                elif not paths.target_config.exists():
                    raise MigrationError(
                        "配置回滚副本缺失；后端拒绝继续启动。",
                        code="apply_recovery_failed",
                    )
            elif paths.target_config.exists():
                raise MigrationError(
                    "配置回滚目标状态异常；后端拒绝继续启动。",
                    code="apply_recovery_failed",
                )
            record_step("rollback_config_restored")

        if not bool(journal.get("rollback_local_restored")):
            if local_was_present:
                if paths.local_backup.exists():
                    if paths.target_local.exists():
                        raise MigrationError(
                            "本机配置回滚目标被占用；后端拒绝继续启动。",
                            code="apply_recovery_failed",
                        )
                    os.replace(paths.local_backup, paths.target_local)
                    _fsync_directory(paths.target_local.parent)
                elif not paths.target_local.exists():
                    raise MigrationError(
                        "本机配置回滚副本缺失；后端拒绝继续启动。",
                        code="apply_recovery_failed",
                    )
            elif paths.target_local.exists():
                raise MigrationError(
                    "本机配置回滚目标状态异常；后端拒绝继续启动。",
                    code="apply_recovery_failed",
                )
            record_step("rollback_local_restored")

        if not bool(journal.get("rollback_data_restored")):
            if data_was_present:
                if paths.data_backup.exists():
                    if paths.target_data.exists():
                        raise MigrationError(
                            "数据回滚目标被占用；后端拒绝继续启动。",
                            code="apply_recovery_failed",
                        )
                    os.replace(paths.data_backup, paths.target_data)
                    _fsync_directory(paths.target_data.parent)
                elif not paths.target_data.exists():
                    raise MigrationError(
                        "数据回滚副本缺失；后端拒绝继续启动。",
                        code="apply_recovery_failed",
                    )
            elif paths.target_data.exists():
                raise MigrationError(
                    "数据回滚目标状态异常；后端拒绝继续启动。",
                    code="apply_recovery_failed",
                )
            record_step("rollback_data_restored")

        paths.prepared_config.unlink(missing_ok=True)
        if paths.prepared_data.exists():
            shutil.rmtree(paths.prepared_data)
        _delete_migration_artifact(paths.failed_config)
        _delete_migration_artifact(paths.failed_data)
        prior_status = _read_json_file(migration_root / _STATUS_FILE)
        if (
            isinstance(prior_status, dict)
            and prior_status.get("state") == "applied"
            and prior_status.get("migration_id") == journal.get("migration_id")
        ):
            rolled_back = MigrationApplyResult(
                state="failed",
                migration_id=str(journal.get("migration_id", "")),
                source_version=str(journal.get("source_version", "")),
                applied_at=datetime.now(UTC).isoformat(),
                message="迁移在提交确认前中断，已恢复原数据；待导入项可安全重试。",
            )
            _atomic_write_json(migration_root / _STATUS_FILE, asdict(rolled_back))
    except Exception as exc:
        logger.exception("Failed to recover an interrupted migration apply")
        raise MigrationError(
            "上一次迁移中断且自动回滚失败；为避免覆盖数据，后端拒绝继续启动。",
            code="apply_recovery_failed",
        ) from exc
    _fsync_directory(paths.target_config.parent)
    _fsync_directory(paths.target_data.parent)
    _fsync_directory(migration_root)
    journal_path.unlink(missing_ok=True)
    _fsync_directory(migration_root)
    return None


def _record_apply_failure(
    migration_root: Path,
    migration_id: str,
    message: str,
    *,
    source_version: str = "",
) -> MigrationApplyResult:
    result = MigrationApplyResult(
        state="failed",
        migration_id=migration_id,
        source_version=source_version,
        applied_at=datetime.now(UTC).isoformat(),
        message=message,
    )
    _atomic_write_json(migration_root / _STATUS_FILE, asdict(result))
    return result


def _version_tuple(value: str) -> tuple[int, ...]:
    numbers = [int(part) for part in re.findall(r"\d+", value)]
    return tuple(numbers or [0])
