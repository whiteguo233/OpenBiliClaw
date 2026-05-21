"""Central logging initialization for OpenBiliClaw."""

from __future__ import annotations

import logging
import time
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING

from rich.logging import RichHandler

if TYPE_CHECKING:
    from pathlib import Path

    from openbiliclaw.config import Config

logger = logging.getLogger(__name__)
_NOISY_HTTP_LOGGERS = ("httpx", "httpcore")


def _coerce_level(level_name: str) -> int:
    """Convert a level name to a logging level."""
    level = logging.getLevelName(level_name.upper())
    if isinstance(level, int):
        return level
    return logging.INFO


def _build_file_handler(
    log_file: object,  # Path, but typed loose to avoid import
    *,
    max_file_size_mb: int,
    backup_count: int,
    level: int,
) -> logging.Handler:
    """Return a rotating file handler when rotation is enabled, else a plain one.

    Rotation triggers when the active file reaches ``max_file_size_mb`` MB; at
    that point ``RotatingFileHandler`` moves it to ``<name>.1`` (older backups
    shift to ``.2``, ``.3``, ...) and older-than-``backup_count`` copies are
    deleted. Setting ``backup_count=1`` caps total disk usage at roughly
    ``2 * max_file_size_mb`` MB.
    """
    from pathlib import Path

    log_path = Path(str(log_file))

    if max_file_size_mb <= 0 or backup_count < 1:
        handler: logging.Handler = logging.FileHandler(log_path, encoding="utf-8")
    else:
        handler = RotatingFileHandler(
            log_path,
            maxBytes=max_file_size_mb * 1024 * 1024,
            backupCount=backup_count,
            encoding="utf-8",
        )

    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    return handler


def _enforce_size_budget_once(log_file: object, max_file_size_mb: int) -> None:
    """Truncate an oversized log on startup so we don't resume 7 GB files.

    ``RotatingFileHandler`` only rotates on *new* writes, so an already-oversized
    file keeps growing until the next rollover boundary. On startup we proactively
    rotate once if the existing file is already over budget — this is the
    "清理超过 1G 的历史日志" behavior the user asked for.
    """
    from pathlib import Path

    if max_file_size_mb <= 0:
        return

    log_path = Path(str(log_file))
    if not log_path.exists():
        return

    try:
        size = log_path.stat().st_size
    except OSError:
        return

    if size <= max_file_size_mb * 1024 * 1024:
        return

    # Preserve at most one "before cleanup" snapshot so debugging is still
    # possible, then delete further backups. Matches RotatingFileHandler naming
    # (<name>.1 is the freshest backup).
    snapshot = log_path.with_name(log_path.name + ".1")
    try:
        if snapshot.exists():
            snapshot.unlink()
        log_path.rename(snapshot)
    except OSError:
        # Fall back to truncation if rename fails (e.g. cross-device).
        try:
            log_path.unlink()
        except OSError:
            return


def _is_managed_log(path: Path, managed_filename: str) -> bool:
    """True iff ``path`` is the rotation-managed file or one of its backups.

    Managed = ``<filename>`` exactly OR ``<filename>.N`` where N is digits.
    Anything else (e.g. ``backend-restart.log``, ``init-run.log``) is
    unmanaged — created by external scripts or one-off tools, so we treat
    it under the unmanaged-cleanup policy.
    """
    name = path.name
    if name == managed_filename:
        return True
    prefix = managed_filename + "."
    if name.startswith(prefix):
        suffix = name[len(prefix) :]
        return suffix.isdigit()
    return False


def _sweep_unmanaged_logs(
    log_dir: Path,
    *,
    managed_filename: str,
    aggregate_budget_mb: int,
    unmanaged_truncate_mb: int,
    unmanaged_max_age_days: int,
) -> None:
    """Cleanup ``logs/`` files we don't control via RotatingFileHandler.

    Three policies, applied in order:

    1. **Truncate huge unmanaged files** — if any ``*.log`` file (not the
       managed one) exceeds ``unmanaged_truncate_mb`` MB, truncate it to
       0 bytes. Catches things like ``backend-restart.log`` (script
       stdout redirect), ``openbiliclaw-restart.log``, etc. Truncation
       (not deletion) so live tail-ers don't lose their fd.
    2. **Delete stale unmanaged files** — anything older than
       ``unmanaged_max_age_days`` days gets removed entirely. Old
       one-shot logs from past install / debug sessions.
    3. **Cap aggregate dir size** — total bytes in ``logs/`` (managed +
       unmanaged) summed up. If over ``aggregate_budget_mb`` MB, delete
       oldest unmanaged files until under budget. Managed files are
       kept regardless (RotatingFileHandler is in charge of those).

    Each delete / truncate emits an INFO log so users see what got
    cleaned. All errors are swallowed — startup must not abort because
    of cleanup hiccups.
    """
    if not log_dir.exists() or not log_dir.is_dir():
        return

    try:
        entries = [(p, p.stat()) for p in log_dir.iterdir() if p.is_file()]
    except OSError:
        return

    now = time.time()
    age_cutoff = now - unmanaged_max_age_days * 86400 if unmanaged_max_age_days > 0 else 0.0

    # Pass 1: truncate huge unmanaged files
    truncate_bytes = unmanaged_truncate_mb * 1024 * 1024
    for path, st in entries:
        if _is_managed_log(path, managed_filename):
            continue
        if unmanaged_truncate_mb > 0 and st.st_size >= truncate_bytes:
            try:
                size_mb = st.st_size / (1024 * 1024)
                with path.open("w", encoding="utf-8") as f:
                    f.write(
                        f"# truncated {time.strftime('%Y-%m-%d %H:%M:%S')} "
                        f"— was {size_mb:.0f} MB, threshold "
                        f"{unmanaged_truncate_mb} MB\n"
                    )
                logger.info(
                    "[log-cleanup] truncated %s (was %.0f MB)",
                    path.name,
                    size_mb,
                )
            except OSError as exc:
                logger.debug("Failed to truncate %s: %s", path, exc)

    # Pass 2: delete stale unmanaged files (re-stat after truncate)
    if unmanaged_max_age_days > 0:
        for path in [p for p, _ in entries]:
            if _is_managed_log(path, managed_filename):
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            if st.st_mtime < age_cutoff:
                try:
                    path.unlink()
                    logger.info(
                        "[log-cleanup] deleted stale %s (mtime %s)",
                        path.name,
                        time.strftime("%Y-%m-%d", time.localtime(st.st_mtime)),
                    )
                except OSError as exc:
                    logger.debug("Failed to unlink %s: %s", path, exc)

    # Pass 3: enforce aggregate budget by removing oldest unmanaged files
    if aggregate_budget_mb <= 0:
        return
    budget_bytes = aggregate_budget_mb * 1024 * 1024
    try:
        current_entries = [(p, p.stat()) for p in log_dir.iterdir() if p.is_file()]
    except OSError:
        return
    total = sum(st.st_size for _, st in current_entries)
    if total <= budget_bytes:
        return
    # Sort unmanaged by mtime ASC (oldest first) and trim until in budget
    unmanaged = sorted(
        [(p, st) for p, st in current_entries if not _is_managed_log(p, managed_filename)],
        key=lambda item: item[1].st_mtime,
    )
    for path, st in unmanaged:
        if total <= budget_bytes:
            break
        try:
            path.unlink()
            total -= st.st_size
            logger.info(
                "[log-cleanup] deleted %s (%.0f MB) to enforce %d MB budget",
                path.name,
                st.st_size / (1024 * 1024),
                aggregate_budget_mb,
            )
        except OSError as exc:
            logger.debug("Failed to unlink %s: %s", path, exc)


def configure_logging(
    config: Config,
    console_level_override: str | None = None,
    *,
    sweep_unmanaged: bool = True,
) -> None:
    """Configure root logging for console and file output.

    ``sweep_unmanaged=False`` skips the v0.3.30+ ``logs/`` directory
    cleanup pass — used by the ``logs-prune`` CLI command which runs
    its own dry-run-aware cleanup and shouldn't be ambushed by the
    auto-sweep inside the global Typer callback.
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
        handler.close()

    console_level = _coerce_level(console_level_override or config.logging.level)
    file_level = _coerce_level(config.logging.file_level)

    console_handler = RichHandler(rich_tracebacks=True, show_path=False)
    console_handler.setLevel(console_level)
    console_handler.setFormatter(logging.Formatter("%(message)s"))

    log_file = config.logging.file_path
    log_file.parent.mkdir(parents=True, exist_ok=True)

    _enforce_size_budget_once(log_file, config.logging.max_file_size_mb)
    # v0.3.30+: also sweep unmanaged files in the same logs dir.
    # Catches stdout-redirect logs from start scripts, stale one-off
    # bootstrap logs, and the aggregate-size budget.
    if sweep_unmanaged:
        _sweep_unmanaged_logs(
            config.logging.directory_path,
            managed_filename=config.logging.filename,
            aggregate_budget_mb=config.logging.aggregate_budget_mb,
            unmanaged_truncate_mb=config.logging.unmanaged_truncate_mb,
            unmanaged_max_age_days=config.logging.unmanaged_max_age_days,
        )
    file_handler = _build_file_handler(
        log_file,
        max_file_size_mb=config.logging.max_file_size_mb,
        backup_count=config.logging.backup_count,
        level=file_level,
    )

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    for logger_name in _NOISY_HTTP_LOGGERS:
        logging.getLogger(logger_name).setLevel(logging.WARNING)
