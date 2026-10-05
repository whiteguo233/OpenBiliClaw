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
| Apple notarization / stapler / Gatekeeper | Passed. Apple accepted both the app ZIP and final DMG. App and DMG tickets were stapled and validated; Gatekeeper reports `accepted`, `source=Notarized Developer ID`. Submission IDs are recorded below. |
| Signed app / DMG installation smoke | Passed. Mounted final DMG read-only, copied app into an isolated Applications directory, added quarantine, verified signature/ticket/Gatekeeper, and ran startup with a separate data profile (exit 0, config created, `selftest OK`). Bundled Tailnet self-test and llama-server version also passed. |
| GitHub hosted CI | Not run. Repository Apple secrets are not configured. |
| arm64 / Windows / Docker | Not rebuilt locally. Changes are macOS build-only; Windows build commands and Docker/source runtime remain unchanged. |

The local Intel x64 package is Apple-notarized and installation acceptance passed. No release/tag has been published and no installed application or user data was replaced.

## Credential persistence diagnosis

The setup window reported success, but both an agent-launched process and a Terminal-launched `notarytool history --keychain-profile openbiliclaw-notary` failed to find the profile. Both used the same user, Xcode binary and default login keychain, ruling out a simple Terminal-vs-agent executable or user mismatch.

Apple TN3147 documents that omitting `--keychain` uses the **data protection keychain**, not the legacy file-based default keychain. That implicit store briefly worked locally and then became unreadable. The build and CI now allow/propagate an explicit file keychain for every operation; CI stores the profile in the same temporary keychain it imports for signing. The local setup was changed to explicitly use `~/Library/Keychains/login.keychain-db` and verify reading in a new process before displaying success. Local re-provisioning into the file keychain succeeded. Multiple independent reads, uploads, and both completed notarizations succeeded.

Regression: `pytest tests/test_packaging_build.py -k 'explicit_keychain or temporary_keychain'` failed before the fix; after the fix, all 65 packaging/installer tests pass. No password was printed, exported, or committed. Only profile/keychain metadata and CLI status were inspected.

## Completed notarization and installation acceptance

Completed locally on 2026-10-05 around 04:57 Asia/Shanghai.

- App ZIP: `b1f983e7-4629-47eb-9090-dd03631bd171`, created `2026-10-04T19:59:12.967Z`, final status `Accepted`.
- DMG: `bbbc3722-36d4-45b2-9b54-5a3cfeff0faa`, final status `Accepted`.
- Final installer: `dist/release/OpenBiliClaw-macos-v0.3.226-x64.dmg` (253 MiB). Final app ZIP is also available in `dist/release/` (226 MiB).
- Final DMG SHA-256: `5d9ec8588c5df369aec4c4b990fd0c0ddbb2539e11991d415cf21eb20448d78c`.
- Strict code-signature verification, app and DMG stapler validation, and Gatekeeper assessments passed. The DMG includes the drag-to-Applications guide and no unsigned command launcher.
- A quarantined app copy from the mounted DMG passed startup with an isolated profile, native Tailnet self-test, and bundled llama-server execution. Log markers: `QUARANTINED_APP_SELFTEST=PASS`, `DMG_ACCEPTANCE=PASS`.
- The Terminal-owned continuation resumed the original app submission without uploading it again, verified local app hashes against the uploaded archive, then completed app stapling, archive creation, DMG signing/notarization/stapling and installation acceptance.
- Ignored operational evidence remains under `dist/notary-logs/`: app/DMG status and diagnostic JSON, `finish.log`, verification scripts and `COMPLETE.txt`. The continuation has finished; no waiting process is required.
- Scope: local Intel x64 lean build. Apple Silicon and hosted CI still require their own builds. No public release, GitHub secrets, existing application installation or real user data were changed.
