"""Tests for the boot-splash image generator (packaging/make_splash.py).

``make_splash`` renders the PNG PyInstaller shows while the packaged app starts.
Verify it produces a valid PNG of the expected size regardless of which fonts
the host happens to have (CJK or ASCII fallback)."""

from __future__ import annotations

import importlib.util
import struct
from pathlib import Path


def _load_module():
    root = Path(__file__).resolve().parent.parent
    path = root / "packaging" / "make_splash.py"
    spec = importlib.util.spec_from_file_location("obc_make_splash", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


make_splash_mod = _load_module()


def test_make_splash_writes_valid_png(tmp_path: Path) -> None:
    out = tmp_path / "splash.png"
    result = make_splash_mod.make_splash(out)

    assert result == out
    assert out.exists()
    # PNG magic number.
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_make_splash_has_expected_dimensions(tmp_path: Path) -> None:
    from PIL import Image

    out = make_splash_mod.make_splash(tmp_path / "splash.png")
    with Image.open(out) as img:
        assert img.size == (make_splash_mod._W, make_splash_mod._H)


def test_make_splash_creates_parent_dirs(tmp_path: Path) -> None:
    # The spec writes into project_root/build/, which may not exist yet.
    nested = tmp_path / "build" / "deep" / "splash.png"
    make_splash_mod.make_splash(nested)
    assert nested.exists()
    # Sanity: width WORD in the IHDR chunk matches _W (PNG stores it big-endian
    # at byte offset 16).
    width = struct.unpack(">I", nested.read_bytes()[16:20])[0]
    assert width == make_splash_mod._W
