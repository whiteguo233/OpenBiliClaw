"""Tests for logging setup."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from openbiliclaw.config import Config, LoggingConfig
from openbiliclaw.logging_setup import configure_logging


def test_configure_logging_creates_log_directory_and_file(tmp_path: Path) -> None:
    config = Config(
        logging=LoggingConfig(
            level="INFO",
            file_level="DEBUG",
            directory=str(tmp_path / "logs"),
            filename="openbiliclaw.log",
        )
    )

    configure_logging(config)
    logger = logging.getLogger("openbiliclaw.test")
    logger.info("hello from logging test")

    log_file = tmp_path / "logs" / "openbiliclaw.log"
    assert log_file.exists()
    assert "hello from logging test" in log_file.read_text(encoding="utf-8")


def test_configure_logging_replaces_existing_handlers(tmp_path: Path) -> None:
    config = Config(
        logging=LoggingConfig(
            level="INFO",
            file_level="DEBUG",
            directory=str(tmp_path / "logs"),
            filename="openbiliclaw.log",
        )
    )

    configure_logging(config)
    first_count = len(logging.getLogger().handlers)

    configure_logging(config)
    second_count = len(logging.getLogger().handlers)

    assert first_count == second_count


def test_configure_logging_quiets_noisy_http_libraries(tmp_path: Path) -> None:
    config = Config(
        logging=LoggingConfig(
            level="INFO",
            file_level="DEBUG",
            directory=str(tmp_path / "logs"),
            filename="openbiliclaw.log",
        )
    )

    logging.getLogger("httpx").setLevel(logging.NOTSET)
    logging.getLogger("httpcore").setLevel(logging.NOTSET)

    configure_logging(config)

    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING


def test_configure_logging_uses_rotating_handler_when_enabled(tmp_path: Path) -> None:
    config = Config(
        logging=LoggingConfig(
            directory=str(tmp_path / "logs"),
            filename="app.log",
            max_file_size_mb=5,
            backup_count=2,
        )
    )

    configure_logging(config)
    file_handlers = [h for h in logging.getLogger().handlers if isinstance(h, logging.FileHandler)]

    assert len(file_handlers) == 1
    handler = file_handlers[0]
    assert isinstance(handler, RotatingFileHandler)
    assert handler.maxBytes == 5 * 1024 * 1024
    assert handler.backupCount == 2


def test_configure_logging_disables_rotation_when_size_is_zero(tmp_path: Path) -> None:
    config = Config(
        logging=LoggingConfig(
            directory=str(tmp_path / "logs"),
            filename="app.log",
            max_file_size_mb=0,
            backup_count=3,
        )
    )

    configure_logging(config)
    file_handlers = [h for h in logging.getLogger().handlers if isinstance(h, logging.FileHandler)]

    assert len(file_handlers) == 1
    handler = file_handlers[0]
    assert not isinstance(handler, RotatingFileHandler)


def test_rotating_file_handler_preserves_exception_traceback(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    config = Config(
        logging=LoggingConfig(
            level="INFO",
            file_level="DEBUG",
            directory=str(log_dir),
            filename="app.log",
            max_file_size_mb=5,
            backup_count=1,
        )
    )

    configure_logging(config)
    try:
        raise ValueError("sentinel")
    except ValueError:
        logging.getLogger("openbiliclaw.test").exception("sentinel exception")

    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.FileHandler):
            handler.flush()

    text = (log_dir / "app.log").read_text(encoding="utf-8")
    assert "sentinel exception" in text
    assert "Traceback (most recent call last)" in text
    assert "ValueError: sentinel" in text


def test_plain_file_handler_preserves_exception_traceback(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    config = Config(
        logging=LoggingConfig(
            level="INFO",
            file_level="DEBUG",
            directory=str(log_dir),
            filename="app.log",
            max_file_size_mb=0,
            backup_count=1,
        )
    )

    configure_logging(config)
    try:
        raise ValueError("sentinel")
    except ValueError:
        logging.getLogger("openbiliclaw.test").exception("sentinel exception")

    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.FileHandler):
            handler.flush()

    text = (log_dir / "app.log").read_text(encoding="utf-8")
    assert "sentinel exception" in text
    assert "Traceback (most recent call last)" in text
    assert "ValueError: sentinel" in text


def test_configure_logging_rotates_oversized_existing_file(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_file = log_dir / "app.log"
    # Seed a 3 MB file so the 1 MB budget triggers cleanup on startup.
    log_file.write_bytes(b"A" * (3 * 1024 * 1024))

    config = Config(
        logging=LoggingConfig(
            directory=str(log_dir),
            filename="app.log",
            max_file_size_mb=1,
            backup_count=1,
        )
    )

    configure_logging(config)
    logging.getLogger("openbiliclaw.test").info("fresh line")

    assert (log_dir / "app.log.1").exists(), "oversized file should be rotated to .1"
    assert log_file.exists()
    # Fresh file should be tiny — the pre-existing 3 MB went into .1
    assert log_file.stat().st_size < 100 * 1024


def test_configure_logging_leaves_small_existing_file_intact(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_file = log_dir / "app.log"
    log_file.write_text("earlier session line\n", encoding="utf-8")
    original_size = log_file.stat().st_size

    config = Config(
        logging=LoggingConfig(
            directory=str(log_dir),
            filename="app.log",
            max_file_size_mb=10,
            backup_count=1,
        )
    )

    configure_logging(config)

    assert not (log_dir / "app.log.1").exists()
    # Existing content preserved — startup should only rotate if oversized.
    assert log_file.stat().st_size >= original_size


# ---------------------------------------------------------------------------
# v0.3.30+: unmanaged-logs sweep (truncate huge files, delete stale,
# enforce aggregate dir budget)


def test_sweep_truncates_oversized_unmanaged_files(tmp_path: Path) -> None:
    """A non-managed *.log file over unmanaged_truncate_mb must be
    truncated to ~0 bytes on configure_logging startup."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    # 5 MB unmanaged file
    huge = log_dir / "backend-restart.log"
    huge.write_bytes(b"x" * (5 * 1024 * 1024))
    # 1 KB managed (untouched)
    managed = log_dir / "app.log"
    managed.write_bytes(b"keep me\n")

    config = Config(
        logging=LoggingConfig(
            directory=str(log_dir),
            filename="app.log",
            max_file_size_mb=10,
            backup_count=1,
            unmanaged_truncate_mb=1,  # very low to force truncation
            unmanaged_max_age_days=0,  # disable age policy
            aggregate_budget_mb=0,  # disable aggregate policy
        )
    )

    configure_logging(config)

    assert huge.exists()
    # Truncate leaves a small marker line, NOT the original 5 MB
    assert huge.stat().st_size < 1024
    # Managed file untouched (small enough not to rotate)
    assert managed.exists()


def test_sweep_deletes_stale_unmanaged_files(tmp_path: Path) -> None:
    """Files older than unmanaged_max_age_days days must be removed."""
    import os
    import time

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    stale = log_dir / "init-run.log"
    stale.write_bytes(b"old run\n")
    fresh = log_dir / "freshish.log"
    fresh.write_bytes(b"fresh\n")
    # Set stale mtime 60 days ago
    sixty_days_ago = time.time() - 60 * 86400
    os.utime(stale, (sixty_days_ago, sixty_days_ago))

    config = Config(
        logging=LoggingConfig(
            directory=str(log_dir),
            filename="app.log",
            max_file_size_mb=10,
            unmanaged_max_age_days=30,
            unmanaged_truncate_mb=0,
            aggregate_budget_mb=0,
        )
    )

    configure_logging(config)

    assert not stale.exists()  # deleted
    assert fresh.exists()  # kept


def test_sweep_enforces_aggregate_dir_budget(tmp_path: Path) -> None:
    """When total dir size exceeds aggregate_budget_mb, oldest unmanaged
    files are deleted until under budget. Managed files are kept
    regardless."""
    import os
    import time

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    # Managed file occupies most of the budget
    managed = log_dir / "app.log"
    managed.write_bytes(b"x" * (3 * 1024 * 1024))  # 3 MB
    # Two unmanaged files, oldest first
    old = log_dir / "old-extra.log"
    old.write_bytes(b"x" * (2 * 1024 * 1024))  # 2 MB
    new = log_dir / "new-extra.log"
    new.write_bytes(b"x" * (2 * 1024 * 1024))  # 2 MB
    # Force ordering — make `old` strictly older than `new`
    long_ago = time.time() - 10000
    recent = time.time()
    os.utime(old, (long_ago, long_ago))
    os.utime(new, (recent, recent))

    config = Config(
        logging=LoggingConfig(
            directory=str(log_dir),
            filename="app.log",
            max_file_size_mb=100,  # don't rotate the 3 MB managed file
            aggregate_budget_mb=5,  # 3 MB managed + 2 MB allowed extra
            unmanaged_truncate_mb=0,
            unmanaged_max_age_days=0,
        )
    )

    configure_logging(config)

    assert managed.exists()  # managed kept regardless
    assert not old.exists()  # oldest unmanaged evicted
    assert new.exists()  # newer unmanaged still fits


def test_sweep_skipped_when_flag_disabled(tmp_path: Path) -> None:
    """Passing sweep_unmanaged=False keeps unmanaged files intact —
    used by the logs-prune CLI's dry-run path."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    huge = log_dir / "backend-restart.log"
    huge.write_bytes(b"x" * (5 * 1024 * 1024))

    config = Config(
        logging=LoggingConfig(
            directory=str(log_dir),
            filename="app.log",
            max_file_size_mb=10,
            unmanaged_truncate_mb=1,
            unmanaged_max_age_days=0,
            aggregate_budget_mb=0,
        )
    )

    configure_logging(config, sweep_unmanaged=False)

    # Untouched
    assert huge.stat().st_size == 5 * 1024 * 1024
