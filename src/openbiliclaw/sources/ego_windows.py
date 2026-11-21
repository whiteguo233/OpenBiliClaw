"""Windows bindings for the app-owned Ego browser and file leases."""

from __future__ import annotations

import ctypes
import os
import shutil
from contextlib import suppress
from ctypes import wintypes
from importlib import import_module
from pathlib import Path
from typing import Any, BinaryIO

# Typeshed exposes these ctypes members only when checked on Windows.
_win_ctypes: Any = ctypes


def installation() -> tuple[Path, Path] | None:
    """Find the installed Beta CLI and browser without starting either one."""
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    active = Path.home() / ".local" / "share" / "ego" / "active_version_dir"
    cli_override = os.environ.get("OPENBILICLAW_EGO_WINDOWS_CLI")
    app_override = os.environ.get("OPENBILICLAW_EGO_WINDOWS_APP")
    cli_candidates = [active / "ego-browser.exe"]
    if path_cli := shutil.which("ego-browser.exe"):
        cli_candidates.append(Path(path_cli))
    cli = _installed_file([Path(cli_override)] if cli_override else cli_candidates)
    if cli is None:
        return None

    roots = [local]
    roots.extend(
        Path(value)
        for name in ("ProgramFiles", "ProgramFiles(x86)")
        if (value := os.environ.get(name))
    )
    app_candidates = [
        directory / name
        for directory in (cli.parent, cli.parent.parent, active)
        for name in ("ego.exe", "ego lite.exe", "egolite.exe")
    ]
    app_candidates.extend(
        directory / "ego.exe"
        for root in roots
        for directory in (
            root / "Citro Labs" / "ego" / "Application",
            root / "ego" / "Application",
            root / "Ego Lite" / "Application",
        )
    )
    app_candidates.extend(_app_paths())
    app = _installed_file([Path(app_override)] if app_override else app_candidates)
    return (cli, app) if app is not None and app != cli else None


def _installed_file(candidates: list[Path]) -> Path | None:
    for path in candidates:
        with suppress(OSError, ValueError):
            if path.is_absolute() and path.suffix.lower() == ".exe" and path.is_file():
                return path.resolve(strict=True)
    return None


def _app_paths() -> list[Path]:
    if os.name != "nt":
        return []
    winreg: Any = import_module("winreg")

    result = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for exe in ("ego.exe", "egolite.exe", "ego lite.exe"):
            with (
                suppress(OSError),
                winreg.OpenKey(
                    hive, rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{exe}"
                ) as key,
            ):
                value, _ = winreg.QueryValueEx(key, "")
                if isinstance(value, str):
                    result.append(Path(os.path.expandvars(value.strip('"'))))
    return result


class _Overlapped(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_size_t),
        ("InternalHigh", ctypes.c_size_t),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]


def _kernel() -> Any:
    return _win_ctypes.WinDLL("kernel32", use_last_error=True)


def lock_file(handle: BinaryIO, *, shared: bool, blocking: bool) -> None:
    """Take a shared/exclusive byte-range lock released when the file closes."""
    msvcrt: Any = import_module("msvcrt")

    lock = _kernel().LockFileEx
    lock.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_Overlapped),
    ]
    lock.restype = wintypes.BOOL
    flags = (0 if shared else 2) | (0 if blocking else 1)
    if not lock(msvcrt.get_osfhandle(handle.fileno()), flags, 0, 1, 0, ctypes.byref(_Overlapped())):
        error = _win_ctypes.get_last_error()
        if error == 33:  # ERROR_LOCK_VIOLATION
            raise BlockingIOError("Ego file is locked")
        raise _win_ctypes.WinError(error)


def browser_process(pid: int, image: Path, *, stop_at: int | None = None) -> int | None:
    """Read a live process birth timestamp or stop the verified Windows handle.

    Checking and stopping through one handle prevents killing a recycled PID.
    The timestamp is the kernel's creation FILETIME, persisted beside the PID.
    """
    kernel = _kernel()
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    kernel.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]

    access = 0x00101000  # SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION
    handle = kernel.OpenProcess(access | (1 if stop_at is not None else 0), False, pid)
    if not handle:
        return None
    try:
        if kernel.WaitForSingleObject(handle, 0) != 258:  # WAIT_TIMEOUT: still running
            return None
        buffer = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buffer))
        if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return None
        if os.path.normcase(buffer.value) != os.path.normcase(str(image)):
            return None
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
            return None
        started = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        if stop_at is None:
            return started
        if started != stop_at:
            return None
        if not kernel.TerminateProcess(handle, 1) and kernel.WaitForSingleObject(handle, 0) == 258:
            raise OSError("Ego host termination denied")
        return started
    finally:
        kernel.CloseHandle(handle)
