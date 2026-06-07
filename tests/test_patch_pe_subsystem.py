"""Tests for the Windows PE subsystem patcher (console -> GUI).

``packaging/patch_pe_subsystem.py`` flips bundled ollama / llama-server from a
console subsystem to GUI so they never pop a console window. Verify the patch on
crafted minimal PE buffers (no real exe needed)."""

from __future__ import annotations

import importlib.util
import struct
from pathlib import Path


def _load_module():
    root = Path(__file__).resolve().parent.parent
    path = root / "packaging" / "patch_pe_subsystem.py"
    spec = importlib.util.spec_from_file_location("obc_patch_pe_subsystem", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


patch_pe = _load_module()


def _fake_pe(subsystem: int) -> bytes:
    """A minimal MZ/PE buffer with the given Subsystem WORD at the real offset."""
    pe_off = 0x80
    data = bytearray(pe_off + patch_pe._SUBSYSTEM_REL_OFFSET + 8)
    data[0:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, pe_off)
    data[pe_off : pe_off + 4] = b"PE\x00\x00"
    struct.pack_into("<H", data, pe_off + patch_pe._SUBSYSTEM_REL_OFFSET, subsystem)
    return bytes(data)


def _read_subsystem(data: bytes) -> int:
    off = struct.unpack_from("<I", data, 0x3C)[0] + patch_pe._SUBSYSTEM_REL_OFFSET
    return struct.unpack_from("<H", data, off)[0]


def test_flips_console_subsystem_to_gui(tmp_path: Path) -> None:
    exe = tmp_path / "llama-server.exe"
    exe.write_bytes(_fake_pe(3))  # 3 = console

    assert patch_pe.patch_pe_subsystem(exe) == "console -> GUI"
    assert _read_subsystem(exe.read_bytes()) == 2  # 2 = GUI


def test_leaves_gui_subsystem_untouched(tmp_path: Path) -> None:
    exe = tmp_path / "already-gui.exe"
    original = _fake_pe(2)
    exe.write_bytes(original)

    assert patch_pe.patch_pe_subsystem(exe) == "already GUI"
    assert exe.read_bytes() == original  # byte-for-byte unchanged


def test_skips_non_pe_file(tmp_path: Path) -> None:
    junk = tmp_path / "notpe.bin"
    junk.write_bytes(b"definitely not a PE executable")

    assert "skip" in patch_pe.patch_pe_subsystem(junk)
    assert junk.read_bytes() == b"definitely not a PE executable"
