"""Contract tests for the Windows Inno Setup script's [Run] launch policy.

The installer must never start the app while the interactive wizard is still
open (regression: it used to launch unconditionally before the Finish page).
Parsing the .iss here keeps the contract enforced on every platform and every
push; the real end-to-end behavior is covered by the windows-latest installer
job (which now fails if the silent install never launches the app) and by the
local marker-app harness described in docs/changelog.md.
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ISS_PATH = PROJECT_ROOT / "packaging" / "openbiliclaw.iss"


def _run_entries() -> list[dict[str, str]]:
    """Parse [Run] section entries into {filename, description, flags} dicts."""
    text = ISS_PATH.read_text(encoding="utf-8")
    match = re.search(r"^\[Run\]\s*$(.*?)(?=^\[|\Z)", text, flags=re.MULTILINE | re.DOTALL)
    assert match is not None, "packaging/openbiliclaw.iss has no [Run] section"
    entries: list[dict[str, str]] = []
    for raw_line in match.group(1).splitlines():
        line = raw_line.strip()
        if not line.startswith("Filename:"):
            continue
        entry: dict[str, str] = {"filename": "", "description": "", "flags": ""}
        filename = re.match(r'Filename:\s*"([^"]+)"', line)
        assert filename is not None, f"unparseable [Run] entry: {line}"
        entry["filename"] = filename.group(1)
        description = re.search(r'Description:\s*"([^"]+)"', line)
        if description is not None:
            entry["description"] = description.group(1)
        flags = re.search(r"Flags:\s*([^\n]+)$", line)
        assert flags is not None, f"[Run] entry missing Flags: {line}"
        entry["flags"] = flags.group(1)
        entries.append(entry)
    return entries


def test_interactive_launch_is_a_finish_page_postinstall_checkbox() -> None:
    """Interactive installs launch only after the user clicks Finish."""
    postinstall = [e for e in _run_entries() if "postinstall" in e["flags"]]
    assert len(postinstall) == 1, "expected exactly one postinstall [Run] entry"
    entry = postinstall[0]
    assert entry["filename"] == r"{app}\{#MyAppExeName}"
    assert "nowait" in entry["flags"], "postinstall entries must not block the wizard"
    assert "skipifsilent" in entry["flags"], "postinstall entry must not run in silent mode"
    assert "skipifnotsilent" not in entry["flags"]
    assert "{cm:LaunchProgram," in entry["description"], (
        "postinstall entry needs a visible checkbox label"
    )


def test_silent_installs_still_autolaunch_the_new_binary() -> None:
    """/SILENT and /VERYSILENT upgrades must hand off to the freshly written exe.

    PrepareToInstall taskkills the running instance, so without this entry a
    silent upgrade would leave nothing running. Note that postinstall entries do
    run during silent installs (the wizard auto-clicks through the hidden
    Finished page): the two entries stay mutually exclusive purely through the
    skipifsilent / skipifnotsilent flags, so both flags are load-bearing.
    """
    silent = [e for e in _run_entries() if "skipifnotsilent" in e["flags"]]
    assert len(silent) == 1, "expected exactly one silent-only [Run] entry"
    entry = silent[0]
    assert entry["filename"] == r"{app}\{#MyAppExeName}"
    assert "nowait" in entry["flags"]
    assert "postinstall" not in entry["flags"], (
        "silent handoff must run during install, not as a Finished-page checkbox"
    )


def test_no_run_entry_launches_unconditionally() -> None:
    """No entry may run in both interactive and silent modes.

    An ungated entry is exactly the original bug: it executes right after the
    files are copied, launching the app before the wizard shows the Finish page.
    """
    entries = _run_entries()
    assert len(entries) == 2
    for entry in entries:
        gated = "skipifsilent" in entry["flags"] or "skipifnotsilent" in entry["flags"]
        assert gated, f"[Run] entry runs unconditionally: {entry}"
