# macOS First Launch Guidance Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the unsigned macOS desktop DMG teach users how to complete the first launch without requiring an Apple Developer account.

**Architecture:** Extend `packaging/build.py` so macOS DMG staging includes a generated visible guidance image, a `.background/` copy, and a bilingual first-launch HTML file, while preserving the existing app + Applications drag-install layout. Keep Gatekeeper bypass instructions visible in release notes and README without adding hidden quarantine removal.

**Tech Stack:** Python standard library, `hdiutil`, optional Finder AppleScript, PyInstaller release workflow, pytest.

---

### Task 1: Add DMG Guidance Staging Tests

**Files:**
- Modify: `tests/test_packaging_build.py`

**Step 1: Write failing tests**

Add tests for:
- `write_macos_first_launch_guide()` writes an HTML file with right-click / Control-click and Privacy & Security instructions.
- `make_macos_dmg()` stages a visible first-launch PNG and `.background/openbiliclaw-dmg-guide.png`.
- `make_macos_dmg()` still creates the Applications symlink.

**Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/test_packaging_build.py -q
```

Expected: FAIL because the helper functions or staged files do not exist yet.

### Task 2: Implement DMG Guidance Assets

**Files:**
- Modify: `packaging/build.py`

**Step 1: Add guide writer**

Create a helper that writes `首次打开说明 First Launch.html` into the DMG staging root with bilingual first-launch instructions.

**Step 2: Add guidance image generator**

Create a helper that generates `.background/openbiliclaw-dmg-guide.png` and a visible root-level `首次打开提示 First Launch.png`.

**Step 3: Use helpers from `make_macos_dmg()`**

Call the helpers after copying `OpenBiliClaw.app` and creating the Applications symlink.

**Step 4: Run tests**

Run:

```bash
pytest tests/test_packaging_build.py -q
```

Expected: PASS.

### Task 3: Update Release Notes and README

**Files:**
- Modify: `.github/workflows/release-desktop.yml`
- Modify: `README.md`
- Modify: `README_EN.md`
- Modify: `docs/changelog.md`

**Step 1: Update release notes**

Move the first-launch guidance to the top of desktop release notes and mention that the DMG includes instructions.

**Step 2: Update README CN/EN**

Add a short note that the macOS DMG shows first-launch steps directly in the install window.

**Step 3: Update changelog**

Add a current-version bullet describing the macOS DMG first-launch guidance.

**Step 4: Run focused checks**

Run:

```bash
pytest tests/test_packaging_build.py tests/test_release_consistency.py -q
```

Expected: PASS.

### Task 4: Final Verification

**Files:**
- No new files beyond docs and packaging changes.

**Step 1: Run lint/test focus**

Run:

```bash
ruff check packaging tests/test_packaging_build.py
pytest tests/test_packaging_build.py tests/test_release_consistency.py -q
```

Expected: PASS.

**Step 2: Inspect git diff**

Run:

```bash
git diff -- packaging/build.py tests/test_packaging_build.py .github/workflows/release-desktop.yml README.md README_EN.md docs/changelog.md docs/plans/2026-06-26-macos-first-launch-guidance-design.md docs/plans/2026-06-26-macos-first-launch-guidance.md
```

Expected: changes are limited to macOS first-launch guidance.
