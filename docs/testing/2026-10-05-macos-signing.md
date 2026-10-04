# macOS Developer ID signing validation — 2026-10-05

Branch: `feat/macos-developer-id`, based on `fcb170bc` (v0.3.226).
Worktree: `../OpenBiliClaw-macos-signing`.

## Local environment

- Intel / x86_64 Mac, Xcode selected at `/Applications/Xcode.app/Contents/Developer`.
- Developer ID Application identity found for paid team `JX4KDJUY28`.
- Isolated Python 3.12 build environment with the project's dev/packaging dependencies.
- Bundled official Ollama v0.32.13; native Tailnet helper built from the repository's pinned Go toolchain/module configuration.

## Checks

| Check | Result |
| --- | --- |
| PyInstaller native x64 application build | Passed, including Ollama, X/Reddit dependencies and Tailnet helper. |
| Frozen onedir startup with isolated data directory | Exit 0, config created, `selftest OK` in desktop log; no service port bound. This precedes Developer ID signing. |
| Packaging / notarization unit tests | 58 passed in `tests/test_packaging_build.py`; includes Apple rejection, timeout, upload errors, nested code ordering and CI credential handling. |
| Extra packaging and macOS installer E2E | 7 passed; together with the 58 packaging unit tests, 65 relevant checks passed. |
| Ruff | `ruff check src/ tests/` passed; packaging script checked separately. |
| MyPy | `mypy src/`: no issues in 305 source files. |
| Shell / YAML | Installer and CI setup parse with `bash -n`; both desktop workflows and composite action parse as YAML. |
| Full pytest | `pytest`: 9774 passed, 112 skipped, 1 failed in 1275.92s. Failure: `TestBackendAPI::test_put_config_does_not_block_on_speculator` hit its 0.5-second asyncio timeout. The case passed independently on both main (5.62s test session) and this branch (4.17s) with the same environment. This looks timing-sensitive; the full run is not reported as green. New packaging cases were also run separately after being added. |
| Developer ID signing | Passed after local keychain authorization. App and nested Mach-O components use team `JX4KDJUY28`, secure timestamps and Hardened Runtime. `codesign --verify --deep --strict` passed. An interrupted signing attempt left a temporary `.cstemp` file; retrying after codesign removed that temporary file completed successfully. |
| Apple notarization / stapler / Gatekeeper | Credentials now persist in the explicit login file keychain and work across independent processes. Application ZIP submitted as `b1f983e7-4629-47eb-9090-dd03631bd171` at `2026-10-04T19:59:12.967Z`; Apple status is `In Progress`. Stapling, final DMG notarization and Gatekeeper acceptance are pending. |
| Signed app / DMG installation smoke | Signed app startup passed (exit 0, `selftest OK`), and the signed native Tailnet helper returned `self-test ok`. Final notarized DMG installation remains pending. |
| GitHub hosted CI | Not run. Repository Apple secrets are not configured. |
| arm64 / Windows / Docker | Not rebuilt locally. Changes are macOS build-only; Windows build commands and Docker/source runtime remain unchanged. |

No release/tag has been published and no installed application or user data was replaced. Do not describe this branch's package as Apple-notarized until Apple's Accepted response, stapler validation and Gatekeeper assessment are recorded.

## Credential persistence diagnosis

The setup window reported success, but both an agent-launched process and a Terminal-launched `notarytool history --keychain-profile openbiliclaw-notary` failed to find the profile. Both used the same user, Xcode binary and default login keychain, ruling out a simple Terminal-vs-agent executable or user mismatch.

Apple TN3147 documents that omitting `--keychain` uses the **data protection keychain**, not the legacy file-based default keychain. That implicit store briefly worked locally and then became unreadable. The build and CI now allow/propagate an explicit file keychain for every operation; CI stores the profile in the same temporary keychain it imports for signing. The local setup was changed to explicitly use `~/Library/Keychains/login.keychain-db` and verify reading in a new process before displaying success. Local re-provisioning into the file keychain succeeded. Multiple independent reads and the actual upload succeeded; Apple processing is pending.

Regression: `pytest tests/test_packaging_build.py -k 'explicit_keychain or temporary_keychain'` failed before the fix; after the fix, all 65 packaging/installer tests pass. No password was printed, exported, or committed. Only profile/keychain metadata and CLI status were inspected.

## First submission and unattended local continuation

- Explicit file-keychain reads remained successful across separate processes, and the real 224 MiB app ZIP upload succeeded. Apple submission: `b1f983e7-4629-47eb-9090-dd03631bd171`, created `2026-10-04T19:59:12.967Z` (2026-10-05 03:59 Asia/Shanghai).
- Apple currently reports `In Progress`; this is not a rejection or a finished notarization. Do not describe the app or DMG as notarized yet.
- The agent's local 20-minute waiter was terminated without canceling Apple's submission. A Terminal-owned continuation is running `notarytool wait` on that **same** ID, with a six-hour timeout; no duplicate app upload was made.
- Local operational scripts/logs (ignored build artifacts): `dist/notary-logs/finish-notarization.command`, `finish-notarization.py`, `verify-dmg.py`, `finish.log`. Keep the Terminal window running. If the six-hour wait ends without acceptance, rerun the same command; it resumes known submissions rather than blindly uploading again.
- Before waiting, the continuation verifies the app signature and compares its executable and CodeResources hashes with the actual uploaded ZIP. After acceptance it staples/assesses the app, creates ZIP/DMG, signs and notarizes the DMG, and validates a quarantined copy mounted from the DMG with a separate test profile. Only completed verification writes `dist/notary-logs/COMPLETE.txt` and reveals the final DMG in Finder.
- The signed bundled `llama-server --version` also passed (Darwin x86_64). No public release or GitHub secrets were changed.

Apple DTS notes that some submissions receive deeper analysis and may take longer: https://developer.apple.com/forums/thread/823348 . No completion time is promised for this submission.
