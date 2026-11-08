# Desktop packaging

## Provider 结构统一（2026-11-06，应用待发布）

| 已实现能力 | 公共接口与行为 |
| --- | --- |
| 预置服务与本地向量播种 | 桌面首次配置落为普通免费实例及独立 BGE-M3 Provider；自检按实际路由判断。云端向量不触发本地权重播种；本地自定义端口/模型保持。签名组件的固定版本、归档验签与 staging 入口不变。 |

配置见 [Config](config.md#硅基流动作为普通-provider)，验证见 [本次验收](../testing/2026-11-06-unified-providers.md)。

## 免费组件构建门禁（2026-11-06）

| 已实现能力 | 合同 |
| --- | --- |
| 自动纳入Rust组件 | PyInstaller完成后必须stage当前平台的固定签名SDK；缺失、摘要不符或验证失败直接终止。Docker也移除可选跳过。 |
| 真实冻结自检 | Mac签名后和Windows安装后，`OPENBILICLAW_SELFTEST=1` 校验随包组件并运行原生 `--check-policy`；全新配置必须自动启用免费Provider。 |

公开API与调用约定：构建接口仍为 `packaging/build.py`，无需另装Rust或人工复制SDK。`_verify_packaged_sponsored_runtime(resources)` 是冻结入口内部自检；不请求上游模型、不输出凭据。打包及签名后的文件保持SDK manifest字节一致。手动 `build-installers.yml` 只生成Actions附件，正式发布仍走独立release流程。


## Phone access defaults (2026-10-26)

| Feature | Status | Contract |
| --- | --- | --- |
| Consistent listener default | Implemented | Normal config and recovery fallbacks use `0.0.0.0`; explicit configured or `OPENBILICLAW_HOST` loopback bindings are preserved. |
| Effective QR port | Implemented | The packaged entry records its effective host / port on FastAPI state before serving `/api/qr-info`, including `OPENBILICLAW_PORT` overrides. |

The bundled desktop Web offers LAN, Tailscale and public scan targets. A browser opens `/m/`; the companion App can import the same URL. No packaging dependency or signing change; details: [phone connections](../mobile-connection.md).

## Implemented features

| Feature | Status | Contract |
| --- | --- | --- |
| Ego native workflow resources | Implemented | The wheel includes `sources/ego_host.mjs` and `sources/ego_dom.mjs`. The desktop spec explicitly stages both under `openbiliclaw/sources`, beside `ego_executor.py`, so the native host's relative module import works from frozen paths. The upstream Ego Lite app is prepared automatically at macOS deployment startup, in a private managed directory outside the signed OpenBiliClaw bundle; it does not wait for the first chat. Bundling these modules does not itself establish an available host or successful website trial. |
| Background configuration recovery | Implemented | After a successful full API handoff and a settled settings queue, the shared parent supervisor replaces all backend children on its next 5-second sweep. Old processes must exit before replacements start; children retain the parent's locked data path through `backend_child_env`. Known chat-only updates preserve workers. Runtime helpers: `backend_config_generation(app)` and `ChildProcessSupervisor(config_generation_provider=...)`; no installer/dependency change. |
| macOS crypto linkage | Implemented | `validate_macos_crypto_linkage()` rejects dynamically linked OpenSSL before PyInstaller; Mac CI installs with `OPENSSL_STATIC=1` and without wheel cache, avoiding a Homebrew/Python libssl collision. |
| Verified free-model runtime | Implemented | Stage the Core-pinned native SDK after PyInstaller, preserve its independently signed bytes when signing the outer app, and verify the manifest again afterward. Frozen startup reconstructs its packaged SDK path, so it does not block login-item registration as a temporary external setting. |
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

Free-runtime packaging uses `openbiliclaw.runtime.sponsored_bundle.stage_runtime_bundle(destination)` and `verify_bundle(directory)`; the target is `Contents/Resources/sponsored` on Mac and `sponsored` under the Windows packaged root.

Public helpers in `packaging/build.py`: `validate_macos_signing`, `sign_macos_app`, `notarize_macos_artifact`, `notarize_macos_app`, `staple_macos_artifact`; `make_macos_dmg(..., notarized=True)` selects the signed installation instructions after successful app notarization.

The Ego adapter resolves `ego_host.mjs` beside its Python module and passes its file URI to Ego's native Node channel. The host then imports `./ego_dom.mjs`; preserve both file names and their directory when staging desktop data. These are application-owned modules, not workflow-supplied scripts. The wheel's existing `src/openbiliclaw` package inclusion also carries these files; no separate build option is needed.

`EgoSetupManager` in `sources/ego_setup.py` and the bounded `ego_onboarding.py` driver handle deployment preparation. A signed drag-install DMG has no post-install hook: the first actual desktop launch performs this part of deployment. After migration, the single-instance/data guards, and final configuration resolution, `packaging/entry.py` calls `create_app(prepare_ego_on_startup=True)`. The API owns one background preparation task without blocking HTTP or the UI; selftests, duplicate launches, and worker children do not opt in. The build itself never initializes a user's Ego environment.

On macOS, preparation downloads the official app, verifies its Apple signature and identity, installs it under `<active database directory>/ego-managed`, and initializes a private profile and named host. This runtime directory is outside the signed application bundle. The desktop/API/CLI service-start check skips a ready, installed environment even if offline, without redownloading or launching the host. Source installer reruns instead call explicit `sources prepare-ego`, which verifies the native handshake and may start an offline host. Failed preparation is retried at the next service start, not in a loop; the settings action and `openbiliclaw sources prepare-ego` remain explicit retry paths. Shutdown cancels and awaits the API-owned preparation task. The installer removes only `com.apple.FinderInfo` and `com.apple.ResourceFork` from its new staged copy before strict signature verification, preserving quarantine, provenance and bundle contents; it does not weaken Gatekeeper checks.

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

`tests/test_ego_packaging.py` checks the desktop spec's actual resource entries and imports the staged native host from a temporary path containing spaces and non-ASCII characters. This verifies the relative JavaScript module dependency without launching Ego or accessing a browser profile. `tests/test_packaging_entry.py` covers deployment opt-in after the migration guards and final config, plus selftest/duplicate-instance exclusions. These tests do not establish a completed PyInstaller build, signed installer, or actual Ego connection.

Official references: [Developer ID certificates](https://developer.apple.com/help/account/certificates/create-developer-id-certificates/), [distribution signing](https://developer.apple.com/documentation/xcode/creating-distribution-signed-code-for-the-mac/), [notarization](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution).

Runner reference: [GitHub hosted runner images](https://github.com/actions/runner-images). Intel uses the supported `macos-15-intel` label instead of retired `macos-13`; arm64 uses `macos-15` to avoid the announced macOS 14 retirement.
## 免费模型组件

免费方案开启时，`_ensure_embedding_model_async()`和`_seed_bundled_embedding_model()`均跳过本地BGE下载/播种，不改变保留的本地向量配置。聊天允许用户provider回退也不会改变文本向量的云端BGE路由。

打包时通过 `runtime.sponsored_bundle.stage_runtime_bundle` 纳入固定发行目录中的已验签组件。macOS主应用签名跳过组件可执行文件，保留其独立manifest中的字节摘要，并在主应用签名后再次验签；本机ad-hoc打包也采用逐层签名。新配置仅在组件可验证时启用免费方案，已有用户配置保留。详细发行与质量边界见[用户流程验收](../testing/2026-11-04-sponsored-user-flow.md)。

`Sponsored runtime installation`工作流在五种原生runner下载Core固定归档、验签、执行离线自检、激活并验证损坏更新保留旧组件；Linux x86_64/arm64另用实际Dockerfile构建并在容器执行自检。`scripts/sponsored_install_smoke.py`使用隔离数据目录，不调用模型API。

组件下载读取已保存的network策略，因此构建入口也能使用用户选择的系统或自定义代理，无需先启动API服务。

冻结入口每次启动会重新发现随包的SDK。自启动守卫仅豁免与当前可执行文件旁的 `sponsored`（Mac为 `Contents/Resources/sponsored`）一致且存在的路径；源码启动、外部SDK路径和用户provider环境变量仍按外部配置处理。Windows整包验收覆盖配置开启、旧注册项迁移和关闭清理。

Mac构建依赖必须使用静态链接的cryptography。2026-11-04的Intel runner对cryptography 50.0.2进行了源码编译，动态链接Homebrew OpenSSL；PyInstaller同时收集Python自己的旧libssl，导致已公证应用启动时报 `_SSL_get0_group_name` 缺失。两条Mac构建工作流现在以 `OPENSSL_STATIC=1` 安装依赖并禁用wheel缓存，构建入口在PyInstaller之前用otool拒绝动态libssl/libcrypto。已有环境需先执行 `OPENSSL_STATIC=1 python -m pip install --force-reinstall --no-cache-dir cryptography`；该配置只作用于构建，不改变用户配置或SDK签名。依据：[cryptography静态构建说明](https://cryptography.io/en/latest/installation/#building-cryptography-on-macos)。

安全SDK目录更新为 `sponsored-runtime-2026-11-06.1`，后端/桌面/Docker沿用固定目录强制验签暂存流程；五个平台发行、Mac旧账本兼容、公开下载与损坏包拒绝的证据见[安全验收](../testing/2026-11-06-security-metadata.md)。SDK发行不等于主应用整包已发布。

### v0.3.235 组件兼容发行

正式桌面与 Docker 构建继续自动纳入已验签 Rust SDK。本版固定 `sponsored-runtime-2026-11-08.1`，manifest 严格支持 Core 0.3.235；旧版 0.3.234 的组件不能直接复用。构建入口与配置不变，Mac 两架构组件均已签名公证，Windows 保持 manifest 验签；整包构建结果单独记入[发行验收](../testing/2026-11-08-release-v0.3.235.md)。

### Intel 已验证产物发布

`publish-intel-installer.yml` 手动接收版本、成功的 `build-installers.yml` run ID 和已复核 DMG SHA-256。先确认构建提交等于对应 desktop tag，再下载该次 Intel 产物、校验版本／架构／提交后缀与字节摘要，将同一包附到 desktop 和 aggregate release。不会重新构建，不替换已有不同摘要的资产；仅移除文件名的构建 SHA 后缀，包内签名、公证和字节保持。脚本接口是 `.github/scripts/promote-intel-installer.py verify-source|publish`，仅供发布 CI 使用，不新增用户 CLI／配置。

## Mac App Store channel (2026-11-05)

| Implemented feature | Status | Boundary |
|---|---|---|
| Separate sandboxed native host + frozen helpers | Build 5 candidate | The Apple Silicon candidate includes main at `8b760446`. Store listing `OpenBiliClaw Hub`, installed app `OpenBiliClaw`, bundle `com.openbiliclaw.backend`, container profile, host lifetime supervision, optional SMAppService login item and App Store managed updates. |
| Bundled free-model runtime | Implemented | Verify the signed upstream bundle before staging it. Place the runtime in `Contents/MacOS` for Store signing and keep the signed model policy in Resources. The host verifies nested code at launch. |
| Apple Silicon Store payload | Implemented | The embed phase thins universal code to arm64, removes Intel-only sidecars for Ollama's Intel runner, and rejects any other Mach-O without an arm64 slice. The archive smoke exercises a real embedding on the bundled runner. |
| Store app display name | Candidate | The installed app, native window, menu accessibility label and localized Quit action use `OpenBiliClaw`; the archive contains `OpenBiliClaw.app`. |
| Store availability and encryption declaration | Saved | This Mac record is mainland-China-only, verified after reopening; standard TLS/WireGuard encryption with no France distribution declared for build 3. Export-compliance warning cleared. The publisher-confirmed content-rights declaration was saved and verified after reload before final submission. No account-wide DSA or phone-app change. |
| Real Store screenshots | Submitted with build 3 | Six visually inspected 2880×1800 RGB captures from the candidate-3 QA runtime: recommendations, favorites, watch later, models, sources and phone connection. All six uploaded and verified after reload, including order and thumbnail loading. Old QR detached from this version with its library original retained. The new QR decoded correctly and its mobile page returned HTTP 200 over this Mac's LAN address. No binary change in the metadata/submission stage. |
| Identified Python license records | QA packaging | Actual license texts and version/source metadata for bilibili-api-python, browser-cookie3 and pystray; complete distribution review remains pending. |

Public build interface: `python packaging/macos_store/build.py --ollama-bin PATH --team TEAM --signing-identity ID [--local] [--version 1.0.0] [--build-number 1]`. Xcode creates `.xcarchive`; `--local` explicitly permits non-store QA signing. `archive_command()` rejects a Developer ID identity for store builds. `embed.py` relocates native tools and signs helper executables with exactly sandbox + inherit before Xcode seals the host. DMG/Windows builds remain in `packaging/build.py`. Full behavior, limitations and account handoff: [Mac App Store](../mac-app-store.md).

The Store product is `OpenBiliClaw.app` and its native executable is `Contents/MacOS/OpenBiliClaw`. `smoke.py` and `restart_smoke.py` use that installed product name. The internal backend helper and bundle identifier retain their technical identifiers.

`smoke.py APP --output DIR [--crash-host] [--models-root DIR | --test-local-embedding]` waits for real HTTP and an enabled native Open Web button before checking cleanup. `--test-local-embedding` pulls `all-minilm:22m` into the isolated profile and checks a real 384-dimensional embedding. `restart_smoke.py APP --output DIR [--port 18590]` saves a separate Ollama chat route through the real API, quits, then restarts the same isolated profile bound to IPv6. The private embedding daemon must restart and stop with its owner. The arm64 workflow runs both scripts and retains their reports with the signed QA archive. The manual workflow input `build_number` sets the archive build number.

`preserve_dependency_licenses(backend)` copies the actual installed license texts and version/project metadata for the identified bilibili-api-python, browser-cookie3 and pystray dependencies into `Resources/ThirdPartyLicenses` before signing, including `--reuse-backend` archives. `COPYING.*` and `LICENSE*` supplements are retained, including pystray’s `COPYING.LGPL`. Missing license text fails the build. The signed-App smoke checks all three records and requires the LGPL supplement for pystray. This limited record is not a complete dependency audit or corresponding-source offer; Store license compatibility remains pending.
