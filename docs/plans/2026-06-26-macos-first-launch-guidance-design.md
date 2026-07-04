# macOS First Launch Guidance Design

## Goal

Make the unsigned / unnotarized macOS desktop package understandable on first install, without paying for Apple Developer Program signing yet and without weakening user security controls automatically.

## Current State

The macOS desktop release is an experimental `.dmg` containing `OpenBiliClaw.app` and an `/Applications` shortcut. The app is only re-sealed with an ad-hoc signature after PyInstaller bundle mutations, so Gatekeeper still rejects direct double-click launch until the user explicitly bypasses the prompt.

The real direct-open fix remains Developer ID signing plus Apple notarization. This design covers the lower-cost interim path.

## Approach

Use the DMG itself as the first guidance surface:

- Add a visible bilingual first-launch instruction file in the DMG root.
- Add a generated visible DMG guidance image, plus a `.background/` copy for future Finder window customization.
- Keep the existing app + Applications drag-install layout.
- Put the same guidance at the top of desktop release notes and keep README instructions synchronized.

## User Flow

1. User downloads `OpenBiliClaw-macos-v*-arm64.dmg` from the aggregate release.
2. User opens the DMG and sees the app, the Applications shortcut, and a first-launch instruction file.
3. The visible guidance image tells the user to drag the app to Applications, then use right-click / Control-click -> Open for first launch.
4. If macOS still blocks the app, the instruction file points to System Settings -> Privacy & Security -> Open Anyway.
5. The terminal `xattr` fallback remains documented only as advanced troubleshooting.

## Non-Goals

- No hidden bypass of Gatekeeper.
- No unsigned `.pkg` installer migration.
- No default one-click `.command` script that removes quarantine.
- No claim that the app is signed or notarized.

## Implementation Notes

- Generate the DMG guidance image during `packaging/build.py` so CI does not need checked-in binary artwork.
- Stage a visible `首次打开提示 First Launch.png` in the DMG root and keep a copy under `.background/` for future Finder view customization.
- Stage `首次打开说明.html` / `First Launch.html` in the DMG root.
- Make `hdiutil` creation degrade gracefully if Finder view customization fails; package creation should still succeed.
- Update desktop release notes and README CN/EN wording to make the first-launch path visible before advanced troubleshooting.

## Verification

- Unit-test that macOS DMG staging includes the app bundle, Applications alias, instruction file, and background asset.
- Unit-test that the release workflow notes mention right-click / Control-click open and Privacy & Security fallback.
- Manually inspect a generated DMG on macOS when cutting a desktop release.
