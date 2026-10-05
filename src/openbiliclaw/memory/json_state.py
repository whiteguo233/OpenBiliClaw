from __future__ import annotations

import json
import os
import random
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

T = TypeVar("T")
_MISSING = object()
_LOCKS: dict[Path, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _process_lock(path: Path) -> threading.RLock:
    key = path.resolve()
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    lock_path = path.with_suffix(path.suffix + ".lock")
    with exclusive_file_lock(lock_path) as acquired:
        if not acquired:  # pragma: no cover - blocking mode always acquires
            raise RuntimeError(f"failed to acquire blocking lock: {lock_path}")
        yield


@contextmanager
def exclusive_file_lock(lock_path: Path, *, blocking: bool = True) -> Iterator[bool]:
    """Hold an OS-level exclusive lock on ``lock_path``.

    Yields whether the lock was acquired; in non-blocking mode a caller that
    loses the race gets ``False`` instead of waiting. The lock is released by
    the kernel if the process dies, so a crash cannot strand it.

    The lock file itself is never deleted or replaced — an unlinked path would
    let two processes hold locks on different inodes for the same logical
    resource.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+b") as handle:
        if os.name == "nt":
            import msvcrt

            msvcrt_module = cast("Any", msvcrt)
            handle.seek(0)
            mode = msvcrt_module.LK_LOCK if blocking else msvcrt_module.LK_NBLCK
            try:
                msvcrt_module.locking(handle.fileno(), mode, 1)
            except OSError:
                if blocking:
                    raise
                yield False
                return
            try:
                yield True
            finally:
                handle.seek(0)
                msvcrt_module.locking(handle.fileno(), msvcrt_module.LK_UNLCK, 1)
        else:
            import fcntl

            flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
            try:
                fcntl.flock(handle.fileno(), flags)
            except OSError:
                if blocking:
                    raise
                yield False
                return
            try:
                yield True
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_json(path: Path) -> object:
    if not path.exists():
        return _MISSING
    try:
        with open(path, encoding="utf-8") as file:
            return json.load(file)
    except (OSError, ValueError):
        return _MISSING


_ATOMIC_WRITE_MAX_ATTEMPTS = 5
_ATOMIC_WRITE_BASE_DELAY_SECONDS = 0.01


def _replace_with_retry(tmp_name: str, path: Path) -> None:
    """Replace ``path`` with ``tmp_name``, retrying Windows sharing violations.

    Windows can briefly reject ``os.replace`` with ``PermissionError`` when
    multiple writers race on the same destination (antivirus/indexer scans and
    other transient file locks can also cause this).  Retry with exponential
    backoff plus random jitter so concurrent writers stagger instead of failing
    the update outright.
    """
    for attempt in range(_ATOMIC_WRITE_MAX_ATTEMPTS):
        try:
            os.replace(tmp_name, path)
            return
        except PermissionError:
            if attempt == _ATOMIC_WRITE_MAX_ATTEMPTS - 1:
                raise
            delay = _ATOMIC_WRITE_BASE_DELAY_SECONDS * (2**attempt) * random.uniform(1.0, 3.0)
            time.sleep(delay)


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        _replace_with_retry(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def update_json_state(
    path: Path,
    *,
    default_factory: Callable[[], T],
    normalize: Callable[[Any], T],
    serialize: Callable[[T], object],
    mutate: Callable[[T], T | None],
) -> T:
    path = Path(path)
    with _process_lock(path), _file_lock(path):
        raw = _read_json(path)
        state = default_factory() if raw is _MISSING else normalize(raw)
        result = mutate(state)
        next_state = state if result is None else result
        _atomic_write_json(path, serialize(next_state))
        return next_state


def read_json_state(
    path: Path,
    *,
    default_factory: Callable[[], T],
    normalize: Callable[[Any], T],
) -> T:
    """Read one normalized JSON state under the same locks as its writers."""
    path = Path(path)
    with _process_lock(path), _file_lock(path):
        raw = _read_json(path)
        return default_factory() if raw is _MISSING else normalize(raw)
