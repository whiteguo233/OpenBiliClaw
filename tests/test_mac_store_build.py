"""Store packaging contracts, independent of Xcode/signing availability in CI."""

import importlib.util
import plistlib
import struct
from pathlib import Path

import pytest

STORE = Path(__file__).resolve().parents[1] / "packaging/macos_store"


def load_file(name):
    spec = importlib.util.spec_from_file_location("store_" + name, STORE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = load_file("build")
embed = load_file("embed")


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
