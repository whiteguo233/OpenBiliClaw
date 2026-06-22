from __future__ import annotations

import json
import os
import tempfile
import threading
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
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+b") as handle:
        if os.name == "nt":
            import msvcrt

            msvcrt_module = cast("Any", msvcrt)
            handle.seek(0)
            msvcrt_module.locking(handle.fileno(), msvcrt_module.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt_module.locking(handle.fileno(), msvcrt_module.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
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


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp_name, path)
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
