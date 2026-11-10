"""A release attachment must retain the reviewed build identity and exact bytes."""

import hashlib
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "intel_promotion", Path(__file__).parents[1] / ".github/scripts/promote-intel-installer.py"
)
assert spec and spec.loader
promotion = importlib.util.module_from_spec(spec)
spec.loader.exec_module(promotion)


def test_accepts_only_the_successful_installer_source():
    run = {
        "head_sha": "a" * 40,
        "conclusion": "success",
        "path": ".github/workflows/build-installers.yml",
    }
    promotion.validate_source(run, "a" * 40)
    for key, value in [("head_sha", "b" * 40), ("conclusion", "failure"), ("path", "other.yml")]:
        with pytest.raises(ValueError):
            promotion.validate_source({**run, key: value}, "a" * 40)


def test_promotion_preserves_exact_bytes_while_removing_build_suffix(tmp_path):
    content = b"reviewed signed DMG bytes"
    path = tmp_path / "OpenBiliClaw-macos-v0.3.235.aaaaaaa-x64.dmg"
    path.write_bytes(content)
    promoted = promotion.verify_asset(
        tmp_path, "0.3.235", hashlib.sha256(content).hexdigest(), "a" * 40
    )
    assert promoted.name == "OpenBiliClaw-macos-v0.3.235-x64.dmg"
    assert promoted.read_bytes() == content


@pytest.mark.parametrize(
    "name,digest",
    [
        ("OpenBiliClaw-macos-v0.3.234-x64.dmg", None),
        ("OpenBiliClaw-macos-v0.3.235.bbbbbbb-x64.dmg", None),
        ("OpenBiliClaw-macos-v0.3.235-arm64.dmg", None),
        ("OpenBiliClaw-macos-v0.3.235-x64.dmg", "0" * 64),
    ],
)
def test_rejects_wrong_version_arch_commit_or_bytes(tmp_path, name, digest):
    (tmp_path / name).write_bytes(b"content")
    with pytest.raises(ValueError):
        promotion.verify_asset(
            tmp_path, "0.3.235", digest or hashlib.sha256(b"content").hexdigest(), "a" * 40
        )
