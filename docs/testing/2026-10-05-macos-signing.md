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
| GitHub hosted CI | Passed on code commit `48f46ecc` (run `37305641917`); signed arm64/x64 desktop builds passed on `57a02a48` (run `37337056080`, attempt 2). The intervening commit only updates documentation. All five Apple repository secrets are configured. |
| arm64 / Windows / Docker | arm64 and x64 lean installers built and verified on hosted runners. Windows HKCU E2E passed in CI; no new Windows installer or Docker image was built for this signing-only change. |

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
- Scope of this initial local acceptance: Intel x64 lean build. Hosted arm64/x64 acceptance is recorded below. No public release, GitHub secrets, existing application installation or real user data were changed.

## Hosted workflow enforcement follow-up

- Both desktop workflows now require Developer ID signing and notarization, including manual builds; the former optional toggle cannot downgrade a job to ad-hoc signing. Credential validation runs immediately after checkout.
- Updated runners to `macos-15` (arm64) and `macos-15-intel` (x64). The manual workflow supports `macos_only=true` for verification of both architectures.
- Increased each Apple submission wait to 90 minutes; timeouts still fail closed and preserve submission IDs.
- Packaging regression suite: 65 passed in 17.72s. Ruff, workflow YAML parsing, embedded Python compilation and signing setup shell syntax passed.
- GitHub CI run `37305641917` passed: 9796 tests passed, 111 skipped; Windows HKCU checks passed (4 passed, 1 skipped); web guided-init E2E passed (39 tests); Firefox smoke build, Ruff and MyPy passed. The local follow-up full run was interrupted before completion and is not counted as a passing run.
- Hosted signed installer acceptance and repository credential provisioning completed as recorded below.

## Hosted signed installer acceptance — 2026-10-06

[Build Desktop Installers run 37337056080](https://github.com/whiteguo233/OpenBiliClaw/actions/runs/37337056080), attempt 2, completed successfully on commit `57a02a48` with `macos_only=true`. Both native lean builds passed Developer ID import, credential validation, app/DMG notarization, stapler validation, Gatekeeper assessment (`source=Notarized Developer ID`), isolated frozen startup and the bundled Tailnet self-test. Installer archives and diagnostic logs were uploaded, and temporary runner keychains were removed.

| Architecture | App submission (Accepted) | DMG submission (Accepted) |
| --- | --- | --- |
| arm64 | `d4319ef0-4d2a-4219-b383-c4e5a4c29416` | `43428aa6-09a4-4ad2-a1cb-98744537e69f` |
| x64 | `d677139f-af51-439d-8d87-f6eebb1684e8` | `8f4c4567-5312-49f4-84ab-0ff6fe5f1b85` |

Download the `openbiliclaw-macos-installer-arm64` or `openbiliclaw-macos-installer-x64` Actions artifact from the run. Each contains a DMG and app ZIP named with `v0.3.226.57a02a4` and the architecture. Artifact archives are 496,798,824 bytes (arm64) and 499,492,321 bytes (x64).

All five required repository secrets are now configured. Only the selected Developer ID identity was exported; its randomly protected P12 and the existing notarization password were sent directly to GitHub Secrets without writing secret files or printing values. The initial attempt started before the last secret was saved and correctly failed at credential preflight; attempt 2 used the complete configuration.

The prior complete CI run `37305641917` passed on the same source/workflow code; `57a02a48` changes documentation only. The repeat CI run `37336677965` also passed the main test, lint/type, Windows and Firefox jobs; at acceptance-record time its Web job was still downloading Playwright. The earlier Web E2E passed all 39 tests. No application/runtime code changed after those checks.

This acceptance covers hosted lean installers for both architectures. Release lean/with-embedding variants share the mandatory signing action and packaging path; no new public release/tag or with-embedding artifact was created during this validation.
