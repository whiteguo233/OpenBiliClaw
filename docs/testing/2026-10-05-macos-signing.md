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
| Packaging / notarization unit tests | 56 passed in `tests/test_packaging_build.py`; includes Apple rejection, timeout, upload errors, nested code ordering and CI credential handling. |
| Extra packaging and macOS installer E2E | 7 passed; together with the 56 packaging unit tests, 63 relevant checks passed. |
| Ruff | `ruff check src/ tests/` passed; packaging script checked separately. |
| MyPy | `mypy src/`: no issues in 305 source files. |
| Shell / YAML | Installer and CI setup parse with `bash -n`; both desktop workflows and composite action parse as YAML. |
| Full pytest | `pytest`: 9774 passed, 112 skipped, 1 failed in 1275.92s. Failure: `TestBackendAPI::test_put_config_does_not_block_on_speculator` hit its 0.5-second asyncio timeout. The case passed independently on both main (5.62s test session) and this branch (4.17s) with the same environment. This looks timing-sensitive; the full run is not reported as green. New packaging cases were also run separately after being added. |
| Developer ID signing | Passed after local keychain authorization. App and nested Mach-O components use team `JX4KDJUY28`, secure timestamps and Hardened Runtime. `codesign --verify --deep --strict` passed. An interrupted signing attempt left a temporary `.cstemp` file; retrying after codesign removed that temporary file completed successfully. |
| Apple notarization / stapler / Gatekeeper | Not submitted yet. Profile validation briefly returned `No submission history`, but the subsequent upload failed with `No Keychain password item found for profile: openbiliclaw-notary`; no submission ID was issued. Credential persistence remains to be resolved. |
| Signed app / DMG installation smoke | Signed app startup passed (exit 0, `selftest OK`), and the signed native Tailnet helper returned `self-test ok`. Final notarized DMG installation remains pending. |
| GitHub hosted CI | Not run. Repository Apple secrets are not configured. |
| arm64 / Windows / Docker | Not rebuilt locally. Changes are macOS build-only; Windows build commands and Docker/source runtime remain unchanged. |

No release/tag has been published and no installed application or user data was replaced. Do not describe this branch's package as Apple-notarized until Apple's Accepted response, stapler validation and Gatekeeper assessment are recorded.
