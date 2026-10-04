# Desktop packaging

## Implemented features

| Feature | Status | Contract |
| --- | --- | --- |
| macOS Developer ID | Implemented | Explicitly sign all nested Mach-O binaries and code bundles inside-out with secure timestamps and Hardened Runtime, then seal the app. |
| Apple notarization | Implemented | Submit app ZIP, require `Accepted`, staple and assess app; create final ZIP/DMG, sign DMG, notarize, staple and assess DMG. Errors stop the build. |
| Experimental builds | Retained | Without either signing option, retain ad-hoc signing and the existing installation helper/first-launch guidance. Partial signing configuration is an error. |
| Signed installation | Implemented | Notarized DMG contains app, Applications link, and bilingual drag-install instructions; no shell installer or Gatekeeper bypass instructions. Quit the old app before replacing it. |
| CI keychain | Implemented | Desktop workflows share a temporary keychain action; credentials match the Safari secret names. Delete the temporary keychain even after failure. |

## Public build interface

`python packaging/build.py --archive-version vX.Y.Z-x64` (use `-arm64` for an Apple Silicon native build) accepts:

- `--macos-signing-identity 'Developer ID Application: Name (TEAMID)'`, or `APPLE_SIGNING_IDENTITY`.
- `--macos-notary-profile openbiliclaw-notary`, or `APPLE_NOTARY_PROFILE`.

Both must be supplied on macOS. Bundle version metadata uses numeric `X.Y.Z`; architecture, commit and variant labels remain in the artifact filenames. These are build settings, not `config.toml` fields or application CLI commands. Configure a local profile with `xcrun notarytool store-credentials openbiliclaw-notary`; use your **paid team's Team ID**, Apple ID and an app-specific password, stored in the macOS Keychain. Xcode login and an Apple Development certificate alone are insufficient.

All resource mutations (Ollama, Tailnet, model seed, compatibility links) must finish before `sign_macos_app`. Never alter the app after signing/stapling. Signing enumerates real files without following symbolic links; data and model files are sealed as resources, not signed as executable code. No blanket Hardened Runtime exceptions are enabled.

Public helpers in `packaging/build.py`: `validate_macos_signing`, `sign_macos_app`, `notarize_macos_artifact`, `notarize_macos_app`, `staple_macos_artifact`; `make_macos_dmg(..., notarized=True)` selects the signed installation instructions after successful app notarization.

## CI configuration

Set these GitHub Actions repository secrets (same names used by Safari):

| Secret | Value |
| --- | --- |
| `APPLE_DEVELOPER_ID_CERTIFICATE_BASE64` | Base64 export of the Developer ID Application certificate **and private key** in a password-protected `.p12`. |
| `APPLE_DEVELOPER_ID_CERTIFICATE_PASSWORD` | Password protecting that export. |
| `APPLE_TEAM_ID` | Paid developer team identifier. |
| `APPLE_NOTARY_USER` | Apple ID. |
| `APPLE_NOTARY_PASSWORD` | Apple app-specific password. |

`MACOS_SIGNING_ENABLED=true` requires all credentials; `false` explicitly selects the experimental path. Unset auto-detects: zero credentials means experimental, a partial set is an error, a complete set enables signing. After provisioning, set `true` to prevent accidental unsigned releases. Local Xcode login does **not** configure GitHub runners. The action does not modify Safari's signing selection.

`release-desktop.yml` handles lean and with-embedding arm64 releases; `build-installers.yml` handles native manual arm64/x64 builds. Both workflows smoke-test the frozen macOS backend with an isolated profile and run the bundled Tailnet helper self-test after packaging. Windows signing is outside this change. No new runtime dependencies, source installer, Docker steps, application configuration or browser/mobile APIs are introduced.

## Diagnostics and verification

`dist/notary-logs/` holds submission ID, final status and Apple diagnostic JSON, also uploaded by CI on failure. A wait timeout (20 minutes) does not imply rejection. Use `xcrun notarytool info ID --keychain-profile PROFILE` or `wait ID` before retrying; do not blindly resubmit. For an accepted timed-out submission, use `notarytool log ID --keychain-profile PROFILE PATH`, then staple/validate the exact submitted app or DMG. Rebuilds change signatures and may require another submission.

Automated checks cover nested-code ordering, symlink/data exclusion, incomplete credentials, Apple rejection/timeouts and signed DMG contents. Release acceptance also requires real `codesign --verify --deep --strict`, `stapler validate`, `spctl --assess`, and a frozen application startup with an isolated data directory. Gatekeeper verification must not clear quarantine. A first internet-download confirmation is normal.

Official references: [Developer ID certificates](https://developer.apple.com/help/account/certificates/create-developer-id-certificates/), [distribution signing](https://developer.apple.com/documentation/xcode/creating-distribution-signed-code-for-the-mac/), [notarization](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution).
