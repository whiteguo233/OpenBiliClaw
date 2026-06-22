#!/usr/bin/env python3
"""Flip a Windows PE executable's subsystem from Console (3) to GUI (2).

Why: the bundled Ollama spawns its model runner (``llama-server.exe``) as a
*console-subsystem* process, which pops a console window on Windows even when our
``ollama serve`` is launched with ``CREATE_NO_WINDOW`` — Ollama gives the runner
its own new console, which our flags can't reach. A GUI-subsystem executable
*never* allocates a console (no window, no ``conhost.exe``), regardless of how it
is spawned. Flipping the runner (and ``ollama.exe`` itself, belt-and-suspenders)
to GUI eliminates the flashing windows while leaving everything else intact:
the PE subsystem does not affect inherited stdout/stderr pipes (so Ollama still
captures the runner's logs) or networking (the runner still serves its port).

Usage (packaging/build-installers.yml, Windows job):
    python packaging/patch_pe_subsystem.py dist/OpenBiliClaw/ollama.exe ...

Patches each given file in place. Safe + idempotent: validates the MZ/PE headers
and only rewrites when the subsystem is exactly Console (so a non-PE file, or an
already-GUI exe, is left untouched).
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

_SUBSYSTEM_CONSOLE = 3
_SUBSYSTEM_GUI = 2
# Subsystem is a WORD at offset 68 within the Optional Header, for BOTH PE32 and
# PE32+ (the fields up to it are the same total size). Absolute offset =
# e_lfanew + 4 ("PE\0\0") + 20 (COFF header) + 68.
_SUBSYSTEM_REL_OFFSET = 4 + 20 + 68


def patch_pe_subsystem(path: Path) -> str:
    """Flip ``path``'s PE subsystem Console→GUI in place; return a status word."""
    data = bytearray(path.read_bytes())
    if data[:2] != b"MZ":
        return "skip (not a PE/MZ file)"
    if len(data) < 0x40:
        return "skip (truncated)"
    pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
    if pe_offset + _SUBSYSTEM_REL_OFFSET + 2 > len(data):
        return "skip (header out of range)"
    if data[pe_offset : pe_offset + 4] != b"PE\x00\x00":
        return "skip (no PE signature)"
    off = pe_offset + _SUBSYSTEM_REL_OFFSET
    (subsystem,) = struct.unpack_from("<H", data, off)
    if subsystem == _SUBSYSTEM_GUI:
        return "already GUI"
    if subsystem != _SUBSYSTEM_CONSOLE:
        return f"skip (subsystem={subsystem})"
    struct.pack_into("<H", data, off, _SUBSYSTEM_GUI)
    path.write_bytes(data)
    return "console -> GUI"


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: patch_pe_subsystem.py <exe> [<exe> ...]")
        return 2
    for arg in argv:
        path = Path(arg)
        status = patch_pe_subsystem(path) if path.is_file() else "skip (not found)"
        print(f"[patch-pe] {path}: {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
