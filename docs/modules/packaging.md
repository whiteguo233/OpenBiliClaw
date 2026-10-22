# Desktop packaging

## Implemented features

| Feature | Status | Contract |
| --- | --- | --- |
| Ego native workflow resources | Implemented | The wheel includes `sources/ego_host.mjs` and `sources/ego_dom.mjs`. The desktop spec explicitly stages both under `openbiliclaw/sources`, beside `ego_executor.py`, so the native host's relative module import works from frozen paths. The upstream Ego Lite app is downloaded and verified at first website preparation on macOS, into a private managed directory outside the signed OpenBiliClaw bundle. Bundling these modules does not itself establish an available host or successful website trial. |
| Background configuration recovery | Implemented | After a successful full API handoff and a settled settings queue, the shared parent supervisor replaces all backend children on its next 5-second sweep. Old processes must exit before replacements start; children retain the parent's locked data path through `backend_child_env`. Known chat-only updates preserve workers. Runtime helpers: `backend_config_generation(app)` and `ChildProcessSupervisor(config_generation_provider=...)`; no installer/dependency change. |
| macOS Developer ID | Implemented | Explicitly sign all nested Mach-O binaries and code bundles inside-out with secure timestamps and Hardened Runtime, then seal the app. |
| Apple notarization | Implemented | Submit app ZIP, require `Accepted`, staple and assess app; create final ZIP/DMG, sign DMG, notarize, staple and assess DMG. Errors stop the build. |
| Local experimental builds | Retained outside hosted CI | Without either signing option, retain ad-hoc signing and the existing installation helper/first-launch guidance. Partial signing configuration is an error. |
| Signed installation | Implemented | Notarized DMG contains app, Applications link, and bilingual drag-install instructions; no shell installer or Gatekeeper bypass instructions. Quit the old app before replacing it. Aggregate release notes use the same signed installation instructions. |
| CI keychain | Implemented | Desktop workflows require signing and share a temporary keychain action; credentials match the Safari secret names. Delete the temporary keychain even after failure. |
| Windows bundled Ollama integrity | Implemented | The staged `ollama.exe` and `lib/ollama` runners ship byte-identical to the upstream signed zip. Never rewrite PE headers after staging: the removed `patch_pe_subsystem.py` flipped the Subsystem byte, which invalidated Ollama's Authenticode signature and got the installer flagged by Kaspersky (#281). Windowless operation relies on upstream spawning the runner with `CREATE_NO_WINDOW` (ollama PR #8668) plus our supervisor launching `ollama serve` with `CREATE_NO_WINDOW`, not on binary patching. Both Windows workflows run a `Get-AuthenticodeSignature` gate after staging and before Inno compile — any staged exe whose signature is not `Valid` fails the build; `tests/test_github_workflows.py` locks the step ordering. PyInstaller UPX compression is disabled (`upx=False` in `packaging/openbiliclaw.spec`) because UPX-packed bootloaders are flagged by AV heuristics far more often. |

## Public build interface

`python packaging/build.py --archive-version vX.Y.Z-x64` (use `-arm64` for an Apple Silicon native build) accepts:

- `--macos-signing-identity 'Developer ID Application: Name (TEAMID)'`, or `APPLE_SIGNING_IDENTITY`.
- `--macos-notary-profile openbiliclaw-notary`, or `APPLE_NOTARY_PROFILE`.
- `--macos-notary-keychain /path/to/signing.keychain-db`, or `APPLE_NOTARY_KEYCHAIN`: use the same explicit file keychain for saving, preflight, submit, wait and log.

Both must be supplied on macOS. Bundle version metadata uses numeric `X.Y.Z`; architecture, commit and variant labels remain in the artifact filenames. These are build settings, not `config.toml` fields or application CLI commands. Configure a local profile with `xcrun notarytool store-credentials openbiliclaw-notary --keychain "$HOME/Library/Keychains/login.keychain-db"`; use your **paid team's Team ID**, Apple ID and an app-specific password, stored in the macOS Keychain. Xcode login and an Apple Development certificate alone are insufficient. Without `--keychain`, Apple uses the data protection keychain, which was observed to become unreadable shortly after a successful save on the local build host. For automation, explicitly pass the same file keychain to both store and every subsequent operation; CI now uses its temporary signing keychain. Verify with `notarytool history --keychain-profile PROFILE --keychain PATH` in a new process before announcing success.

All resource mutations (Ollama, Tailnet, model seed, compatibility links) must finish before `sign_macos_app`. Never alter the app after signing/stapling. Signing enumerates real files without following symbolic links; data and model files are sealed as resources, not signed as executable code. No blanket Hardened Runtime exceptions are enabled.

Public helpers in `packaging/build.py`: `validate_macos_signing`, `sign_macos_app`, `notarize_macos_artifact`, `notarize_macos_app`, `staple_macos_artifact`; `make_macos_dmg(..., notarized=True)` selects the signed installation instructions after successful app notarization.

The Ego adapter resolves `ego_host.mjs` beside its Python module and passes its file URI to Ego's native Node channel. The host then imports `./ego_dom.mjs`; preserve both file names and their directory when staging desktop data. These are application-owned modules, not workflow-supplied scripts. The wheel's existing `src/openbiliclaw` package inclusion also carries these files; no separate build option is needed.

`EgoSetupManager` in `sources/ego_setup.py` and the bounded `ego_onboarding.py` driver handle runtime preparation. On macOS, the first ordinary website preparation automatically downloads the official app, verifies its Apple signature and identity, installs it under `<active database directory>/ego-managed`, and initializes a private profile and named host. This runtime directory is outside the signed application bundle. The optional settings action and `openbiliclaw sources prepare-ego` perform the same preparation early or retry a failure; packaging does not launch or initialize Ego. The installer removes only `com.apple.FinderInfo` and `com.apple.ResourceFork` from its new staged copy before strict signature verification, preserving quarantine, provenance and bundle contents; it does not weaken Gatekeeper checks.

Complete official downloads are cached as `installer.dmg`. Missing bundle/CLI or invalid signing identity invalidates only this regular managed cache, so the next preparation can download again; it never retries in a loop. System trust refusals, timeouts, cancellation and ambiguous mount failures preserve the cache and report their failure. The CLI is checked before moving the staged bundle into its final installation path.

Initialization uses a temporary loopback CDP endpoint only for the known official onboarding page, skips personal-data import, and disables the default-browser, Dock and crash-report options. It then restarts the owned host without the debugging port and verifies the native channel. Actual website workflows use that native channel. An initialized host restarts on demand; read-only status never starts it. Any explicit `OPENBILICLAW_EGO_BROWSER`, `OPENBILICLAW_EGO_SERVER_NAME` or `OPENBILICLAW_EGO_PROFILE` value opts out of managed preparation. Windows and Linux/Docker native Ego execution remain unsupported; HTTP feeds do not require Ego. See [configuration](config.md#ego-lite-执行宿主) and [installation guidance](../agent-install.md#dynamic-website-discovery-browser-runtime).

## CI configuration

Set these GitHub Actions repository secrets (same names used by Safari):

| Secret | Value |
| --- | --- |
| `APPLE_DEVELOPER_ID_CERTIFICATE_BASE64` | Base64 export of the Developer ID Application certificate **and private key** in a password-protected `.p12`. |
| `APPLE_DEVELOPER_ID_CERTIFICATE_PASSWORD` | Password protecting that export. |
| `APPLE_TEAM_ID` | Paid developer team identifier. |
| `APPLE_NOTARY_USER` | Apple ID. |
| `APPLE_NOTARY_PASSWORD` | Apple app-specific password. |

This repository has all five secrets configured; hosted arm64/x64 lean builds passed on 2026-10-06 (see [acceptance record](../testing/2026-10-05-macos-signing.md#hosted-signed-installer-acceptance--2026-10-06)). Both desktop GitHub Actions workflows **always require all five secrets**. Missing credentials, an invalid identity, failed notarization or failed verification stop the macOS job; there is no hosted ad-hoc fallback. The former `MACOS_SIGNING_ENABLED` variable is no longer read, including a stale `false` value. Local Xcode login does **not** configure GitHub runners. The action does not modify Safari's signing selection.

`release-desktop.yml` handles lean and with-embedding arm64 releases; `build-installers.yml` handles native manual arm64/x64 builds on `macos-15` / `macos-15-intel`. Use the manual `macos_only=true` input to validate both Mac architectures without starting Windows; do not combine it with `windows_only=true`. Signing credentials are validated before dependencies and runtimes are downloaded. Both workflows smoke-test the frozen macOS backend with an isolated profile and run the bundled Tailnet helper self-test after packaging. Windows signing is outside the signing workflow change. Signing itself adds no runtime dependencies, source-installer or Docker steps; managed Ego preparation is the separate runtime flow described above.

## Diagnostics and verification

`dist/notary-logs/` holds submission ID, final status and Apple diagnostic JSON, also uploaded by CI on failure. A wait timeout (90 minutes per submission) does not imply rejection. Use `xcrun notarytool info ID --keychain-profile PROFILE --keychain PATH` or `wait ID --keychain-profile PROFILE --keychain PATH` before retrying; do not blindly resubmit. For an accepted timed-out submission, use `notarytool log ID --keychain-profile PROFILE PATH`, then staple/validate the exact submitted app or DMG. Rebuilds change signatures and may require another submission.

Automated checks cover nested-code ordering, symlink/data exclusion, incomplete credentials, Apple rejection/timeouts and signed DMG contents. Release acceptance also requires real `codesign --verify --deep --strict`, `stapler validate`, `spctl --assess`, and a frozen application startup with an isolated data directory. Gatekeeper verification must not clear quarantine. A first internet-download confirmation is normal.

`tests/test_ego_packaging.py` checks the desktop spec's actual resource entries and imports the staged native host from a temporary path containing spaces and non-ASCII characters. This verifies the relative JavaScript module dependency without launching Ego or accessing a browser profile. It does not replace a signed desktop build or an actual Ego connection test.

Official references: [Developer ID certificates](https://developer.apple.com/help/account/certificates/create-developer-id-certificates/), [distribution signing](https://developer.apple.com/documentation/xcode/creating-distribution-signed-code-for-the-mac/), [notarization](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution).

Runner reference: [GitHub hosted runner images](https://github.com/actions/runner-images). Intel uses the supported `macos-15-intel` label instead of retired `macos-13`; arm64 uses `macos-15` to avoid the announced macOS 14 retirement.
