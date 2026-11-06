"""Store packaging contracts, independent of Xcode/signing availability in CI."""

import importlib.util
import json
import plistlib
import struct
from email.message import Message
from pathlib import Path
from types import SimpleNamespace

import pytest

STORE = Path(__file__).resolve().parents[1] / "packaging/macos_store"


def load_file(name):
    spec = importlib.util.spec_from_file_location("store_" + name, STORE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = load_file("build")
embed = load_file("embed")


def test_store_keeps_all_copyleft_texts_including_lgpl_supplement(tmp_path, monkeypatch):
    distributions = {}
    for name in ("bilibili-api-python", "browser-cookie3", "pystray"):
        metadata = Message()
        metadata["License"] = "LGPLv3" if name == "pystray" else "GPL-3.0-or-later"
        files = [Path(f"{name}.dist-info/COPYING")]
        if name == "pystray":
            files.append(Path(f"{name}.dist-info/COPYING.LGPL"))
        for file in files:
            (tmp_path / file).parent.mkdir(exist_ok=True)
            (tmp_path / file).write_text(f"License text: {file.name}")
        distributions[name] = SimpleNamespace(
            files=files, metadata=metadata, version="1.2.3", locate_file=lambda p: tmp_path / p
        )
    monkeypatch.setattr(builder.importlib.metadata, "distribution", distributions.__getitem__)

    builder.preserve_dependency_licenses(tmp_path / "backend")

    notices = tmp_path / "backend/Contents/Resources/ThirdPartyLicenses"
    manifest = json.loads((notices / "manifest.json").read_text())
    assert {p["name"] for p in manifest["packages"]} == set(distributions)
    assert {p.read_text() for p in (notices / "pystray").glob("LICENSE-*.txt")} == {
        "License text: COPYING",
        "License text: COPYING.LGPL",
    }


def test_store_export_rejects_developer_id_before_running_xcode(tmp_path):
    marker = tmp_path / "backend/Contents/Resources/mac-app-store.json"
    marker.parent.mkdir(parents=True)
    marker.write_text("{}")
    args = (tmp_path / "backend", tmp_path / "test.xcarchive", "1.0.0", "1")
    with pytest.raises(ValueError, match="Apple Distribution"):
        builder.archive_command(*args, "Developer ID Application: Example", "TEAM", False)
    command, env = builder.archive_command(*args, "Developer ID Application: Example", "TEAM", True)
    assert "archive" in command
    assert "-archivePath" in command
    assert env["OPENBILICLAW_STORE_PAYLOAD"] == str(tmp_path / "backend")


def test_helper_inherits_only_the_host_sandbox():
    host = plistlib.loads((STORE / "App.entitlements").read_bytes())
    helper = plistlib.loads((STORE / "Helper.entitlements").read_bytes())
    assert host == {
        "com.apple.security.app-sandbox": True,
        "com.apple.security.network.client": True,
        "com.apple.security.network.server": True,
    }
    assert helper == {"com.apple.security.app-sandbox": True, "com.apple.security.inherit": True}


def test_native_tools_leave_resources_but_remain_discoverable(tmp_path):
    contents = tmp_path / "Contents"
    resources, frameworks, macos = (
        contents / name for name in ("Resources", "Frameworks", "MacOS")
    )
    for folder in (resources, frameworks, macos):
        folder.mkdir(parents=True)

    def header(kind):
        return b"\xcf\xfa\xed\xfe" + struct.pack("<III", 0, 0, kind)

    (resources / "ollama").write_bytes(header(2))
    (resources / "libllama.dylib").write_bytes(header(6))
    (frameworks / "openbiliclaw-tailnet-helper").write_bytes(header(2))
    (resources / "openbiliclaw-tailnet-helper").symlink_to(
        "../Frameworks/openbiliclaw-tailnet-helper"
    )
    embed.relocate_native_tools(contents)
    assert embed.macho_kind(macos / "ollama") == 2
    assert embed.macho_kind(frameworks / "libllama.dylib") == 6
    assert (resources / "ollama").resolve() == macos / "ollama"
    assert (macos / "libllama.dylib").resolve() == frameworks / "libllama.dylib"
    assert (
        resources / "openbiliclaw-tailnet-helper"
    ).resolve() == macos / "openbiliclaw-tailnet-helper"
    assert all(embed.macho_kind(path) is None for path in resources.iterdir())
