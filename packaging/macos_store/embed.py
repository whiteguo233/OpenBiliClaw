"""Xcode build phase: embed frozen code inside-out with sandbox inheritance."""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def macho_kind(path: Path) -> int | None:
    """Read Mach-O filetype (2 = executable), including universal binaries."""
    if path.is_symlink() or not path.is_file():
        return None
    with path.open("rb") as stream:
        magic = stream.read(4)
        if magic in (b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"):
            stream.read(12)  # nfat_arch + first cputype + cpusubtype
            offset = int.from_bytes(stream.read(8 if magic[-1] == 0xBF else 4), "big")
            stream.seek(offset)
            magic = stream.read(4)
        endian = "<" if magic in (b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe") else ">"
        if magic not in (
            b"\xce\xfa\xed\xfe",
            b"\xcf\xfa\xed\xfe",
            b"\xfe\xed\xfa\xce",
            b"\xfe\xed\xfa\xcf",
        ):
            return None
        header = stream.read(12)
        return struct.unpack(endian + "III", header)[2] if len(header) == 12 else None


def prepare_arm64_payload(contents: Path) -> None:
    """Keep Apple Silicon code and fail on unexpected incompatible binaries."""
    resources = contents / "Resources"
    for path in sorted(contents.rglob("*")):
        if macho_kind(path) is None:
            continue
        archs = subprocess.check_output(["lipo", "-archs", str(path)], text=True).split()
        # Ollama's universal download includes shared libraries for Intel only.
        # Its arm64 llama-server and llama-quantize link those components statically.
        if (
            archs == ["x86_64"]
            and path.parent == resources
            and path.name.startswith(("libggml", "libllama", "libmtmd"))
            and path.suffix in (".dylib", ".so")
        ):
            path.unlink()
        elif "arm64" not in archs:
            raise ValueError(f"Missing arm64 in {path.relative_to(contents)}: {archs}")
        elif archs != ["arm64"]:
            with tempfile.TemporaryDirectory(dir=path.parent) as temp:
                native = Path(temp) / path.name
                subprocess.run(
                    ["lipo", str(path), "-thin", "arm64", "-output", str(native)], check=True
                )
                shutil.copymode(path, native)
                native.replace(path)


def relocate_native_tools(contents: Path) -> None:
    """Keep executable code out of Resources; preserve runtime-relative links."""
    resources, frameworks, macos = (
        contents / name for name in ("Resources", "Frameworks", "MacOS")
    )
    for path in list(resources.iterdir()):
        if path.is_symlink() or macho_kind(path) is None:
            continue
        destination = (macos if macho_kind(path) == 2 else frameworks) / path.name
        shutil.move(str(path), destination)
        path.symlink_to(os.path.relpath(destination, path.parent))
        # Ollama's runners resolve libraries next to their executable.
        if destination.parent == frameworks and not (macos / path.name).exists():
            (macos / path.name).symlink_to(os.path.relpath(destination, macos))
    helper = frameworks / "openbiliclaw-tailnet-helper"
    if helper.is_file() and not helper.is_symlink():
        destination = macos / helper.name
        shutil.move(str(helper), destination)
        helper.symlink_to(os.path.relpath(destination, frameworks))
    for path in resources.glob("mlx_metal_*"):
        link = macos / path.name
        if not link.exists():
            link.symlink_to(os.path.relpath(path, macos), target_is_directory=True)


def sign_nested(contents: Path, identity: str) -> None:
    targets = []
    for path in contents.rglob("*"):
        if path.is_symlink():
            continue
        kind = macho_kind(path)
        if kind is not None or path.suffix in (".framework", ".bundle"):
            targets.append((path, kind))
    for path, kind in sorted(targets, key=lambda item: len(item[0].parts), reverse=True):
        command = [
            "codesign",
            "--force",
            "--sign",
            identity,
            "--options",
            "runtime",
            "--timestamp=none",
        ]
        if kind == 2:
            command += ["--entitlements", str(HERE / "Helper.entitlements")]
        command.append(str(path))
        subprocess.run(command, check=True)


def main() -> None:
    payload = Path(os.environ["OPENBILICLAW_STORE_PAYLOAD"]) / "Contents"
    contents = Path(os.environ["TARGET_BUILD_DIR"]) / os.environ["CONTENTS_FOLDER_PATH"]
    for folder in ("Resources", "Frameworks"):
        destination = contents / folder
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(payload / folder, destination, symlinks=True)
    macos = contents / "MacOS"
    macos.mkdir(exist_ok=True)
    shutil.copy2(payload / "MacOS/OpenBiliClaw", macos / "OpenBiliClawBackend")
    if os.environ["ARCHS"].split() == ["arm64"]:
        prepare_arm64_payload(contents)
    relocate_native_tools(contents)
    resources = contents / "Resources"
    for language in ("en", "zh-Hans"):
        shutil.copytree(
            HERE / f"{language}.lproj", resources / f"{language}.lproj", dirs_exist_ok=True
        )
    shutil.copy2(HERE.parent / "icon.icns", resources / "StoreIcon.icns")
    # Xcode signs the native main executable and outer app after this phase.
    sign_nested(contents, os.environ.get("EXPANDED_CODE_SIGN_IDENTITY") or "-")


if __name__ == "__main__":
    main()
