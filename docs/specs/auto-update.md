# 后端与浏览器插件自动更新 SPEC

**Created:** 2026-05-31
**Updated:** 2026-05-31
**Ambiguity score:** 0.14 (gate: <= 0.20)
**Requirements:** 10 locked

## Goal

为 OpenBiliClaw 建立一套可解释、可拒绝不安全状态、符合浏览器平台限制的更新机制：

- 后端在源码安装场景下可以自动发现并应用新的 `backend-vX.Y.Z` 版本。
- 浏览器插件自动发现 `extension-vX.Y.Z` 新版本，并在当前 sideload / 开发者模式分发下明确提示用户更新。
- 未来如果插件进入 Chrome Web Store / Edge Add-ons / AMO 或签名自托管通道，同一套版本检测与 UI 可以降级为状态展示，由浏览器原生更新机制完成真正自动安装。

核心原则：**后端可以自动应用；当前分发方式下的插件不能承诺静默自替换**。插件端的首版自动更新定义为“自动检测 + 明确提示 + 下载/重装引导”，而不是绕过浏览器安全模型写入扩展目录。

## Background

仓库已拆成两条发布通道：

- 后端源码版本用 `backend-vX.Y.Z` tag 标记，`.github/workflows/release-backend.yml` 目前只校验 tag 与 `pyproject.toml` 版本一致，不发布后端桌面包。
- 浏览器插件用 `extension-vX.Y.Z` tag 发布 GitHub Release，`.github/workflows/release-extension.yml` 会上传 `openbiliclaw-extension-vX.Y.Z.zip` 和 `openbiliclaw-extension-vX.Y.Z-firefox.zip`。
- 后端已有 `src/openbiliclaw/runtime/updater.py`：周期性查询 GitHub `/tags`，过滤 `backend-v*`，忽略 `extension-v*`，发现新版本后执行 `git pull --ff-only`、依赖同步和进程重启。
- 配置已有 `scheduler.auto_update_enabled=false` 和 `scheduler.auto_update_check_interval_hours=6`，默认关闭。
- README 当前指导用户下载 extension zip 并通过开发者模式 / 临时加载安装插件。

浏览器平台限制决定插件不能按“后端 self-update”方式实现：

- Chrome 官方文档说明 Chrome 只有 Chrome Web Store 和受管自托管两种正式分发机制；Windows/macOS 上自托管安装只能通过企业策略。
- Chromium/Edge 自托管更新依赖 manifest `update_url`、更新 XML、`.crx` 包，并要求新包使用同一私钥签名；这不等于当前 zip + 开发者模式加载。
- Firefox 自分发自动更新需要签名 XPI 与 manifest `browser_specific_settings.gecko.update_url`，AMO 上架则由 Firefox 自动更新；临时加载插件没有持久自动更新语义。

## Chosen Approach

采用三层更新模型。

### Layer 1: 后端源码自动更新

后端继续以 `backend-v*` tag 为权威版本来源，但补齐安全边界：

- 只在 git 源码安装且 worktree 干净时自动应用。
- 自动应用前执行 `git fetch --tags origin`，解析目标 tag commit，并用 `git merge --ff-only <tag>` 快进当前分支到目标 tag。
- 当前分支无法快进、远端不可信、worktree dirty、运行在 Docker/只读包/非 git 环境时，不自动修改文件，只上报明确状态。
- 依赖同步沿用当前检测逻辑：存在 `uv.lock` 时执行 `uv sync`，否则执行 `python -m pip install -e .`。
- 成功后重启当前进程；如果是 systemd、Docker 或外部 supervisor 管理，首版只支持当前 `os.execv` 重启，不主动操作外部 supervisor。

### Layer 2: 插件版本自动发现与提示

插件每次打开 side panel、建立 runtime stream 或进入设置页时，把自身版本和浏览器族发送给后端。HTTP 请求使用 headers；runtime stream 由于浏览器 `WebSocket` 构造器不能设置自定义 headers，必须使用 query params 或连接建立后的第一条 client message。后端统一查询 `extension-v*` GitHub Release，并返回最新插件版本和对应资产下载地址。

当前 sideload/开发者模式安装下：

- 插件发现新版本后显示持久更新横幅和设置页状态。
- 横幅提供“打开下载页/复制安装步骤/稍后提醒”动作。
- 每个新版本只在用户关闭后静默一段时间，状态保存在 `chrome.storage.local`。
- 不尝试覆盖插件目录，不尝试自动下载后调用 `chrome.runtime.reload()` 假装完成更新。

### Layer 3: 未来原生插件自动更新通道

如果后续切到商店或签名自托管：

- Chrome / Edge 商店版本由浏览器自动更新，OpenBiliClaw 只展示当前版本、最新版本和“浏览器会自动更新”的状态。
- 企业自托管 / Linux CRX 使用 `update_url` + XML + 同一私钥签名包，只作为受管部署文档，不作为普通用户默认路径。
- Firefox AMO 或签名自分发 XPI 使用 AMO / `gecko.update_url` 更新机制，插件 UI 同样只做状态展示与故障提示。

## Requirements

1. **统一更新状态 API**
   - Current: `/api/runtime-status` 只合并已有 auto updater 字段，插件端没有独立版本状态。
   - Target: 新增 `GET /api/update-status`，返回 `backend` 与 `extension` 两个对象；`/api/runtime-status` 可保留摘要字段。
   - Acceptance: 无插件版本 metadata 时也能返回 backend 状态；带插件版本时返回 extension 当前/最新/下载 URL/状态。

2. **后端版本发现**
   - Current: `AutoUpdateService._fetch_latest_version()` 查询 `/tags` 并过滤 backend-like tag；当前实现把 `backend-v*`、legacy `v*` 和裸 semver 放进同一个候选池，且 `_parse_backend_version("backend-v0.3.100-rc1")` 会解析成 `(0, 3, 100)`。远端历史里 `v0.3.90` 与 `backend-v0.3.89` 并存时，当前 `max()` 会选择裸 `v0.3.90`，而不是 canonical backend tag。
   - Target: 保留 `backend-vX.Y.Z` 为唯一 canonical 后端 tag，并执行明确优先级：只要存在稳定 `backend-v*` 候选，就完全忽略 legacy `v*` / 裸 semver；只有没有任何稳定 `backend-v*` 时才 fallback 到 legacy tags。默认不自动应用 `-rc` / `-beta` / `-dev` 后缀。
   - Target config: 新增 `[scheduler] auto_update_allow_prerelease = false`；实现时必须同步 `config.example.toml`、`docs/modules/config.md` 和配置 API schema。
   - Acceptance: `extension-v*` 永远不会进入 backend candidate；`backend-v0.3.100-rc1` 在默认配置下不会被自动应用；当 `backend-v0.3.89` 和 `v0.3.90` 同时存在时，选择 `backend-v0.3.89`。

3. **后端安全应用**
   - Current: 发现新版本后直接 `git pull --ff-only`，没有显式检查 worktree、remote、target tag commit。
   - Target: 应用前按顺序检查：git repo、origin 指向允许的仓库、worktree clean、当前 HEAD 可快进到目标 tag、未处于 rebase/merge 中、目标 tag 对应 commit 存在。
   - Target config: 新增 `[scheduler] auto_update_allowed_remotes = ["https://github.com/whiteguo233/OpenBiliClaw.git", "git@github.com:whiteguo233/OpenBiliClaw.git"]`。unknown remote 和带 userinfo/credential 的 remote 都统一折叠为 `reason="untrusted_remote"`；不向 API 响应暴露 remote 字符串。带 userinfo/credential 的 remote 必须先硬拒绝，不能剥离凭据后再与 allowlist 匹配。
   - Acceptance: dirty worktree、diverged branch、unknown remote、missing tag 都返回 `updated=false` 和稳定 `reason`，不执行依赖同步和重启。

4. **后端应用锁与状态机**
   - Current: 后台循环可能与手动触发并发运行。
   - Target: 更新服务有单进程 async lock；状态与 reason 使用不同命名空间，状态只表达生命周期，reason 只解释原因。
   - Acceptance: 两个并发 `apply` 请求只有一个进入应用流程，另一个返回 `reason="already_applying"`。

5. **手动检查与手动应用**
   - Current: 只有后台定时检查。
   - Target: 新增 `POST /api/update/check` 和 `POST /api/update/apply`；默认受现有本机/扩展可信来源规则保护。
   - Acceptance: 设置页点击“立即检查”不需要等待后台 interval；`auto_update_enabled=false` 时仍允许用户手动检查和手动应用，但不会自动定时应用。

6. **插件版本发现**
   - Current: 插件只能靠用户看 GitHub Releases。
   - Target: 后端查询 GitHub Releases API，过滤 `extension-v*`，选择最高稳定 semver，并解析 Chrome-compatible zip 与 Firefox zip 资产 URL。GitHub tags 只能作为无资产的 status-only fallback，不能用于生成下载 URL。
   - Acceptance: GitHub latest release 即使不是插件 release，也不影响 extension latest 计算；缺少目标浏览器资产时返回 `state="asset_missing"`。

7. **设置页版本与更新 UI**
   - Current: popup/side panel 没有版本与更新入口。
   - Target: 设置页新增“版本与更新”区块：显示后端当前/最新、插件当前/最新、上次检查时间、错误；提供“自动更新”开关，绑定 `scheduler.auto_update_enabled`，缺省为关闭；提供“检查间隔（小时）”数字输入，绑定 `scheduler.auto_update_check_interval_hours`；推荐页或设置页显示插件新版本横幅。
   - Acceptance: 首次安装或缺省配置时开关显示关闭；用户打开并保存后写入 `scheduler.auto_update_enabled=true` 并刷新后端更新调度；用户关闭并保存后写入 `false`，停止后续定时自动应用；修改检查间隔并保存后刷新调度间隔；这些设置不影响“立即检查”和“立即应用”。

8. **插件安装边界文案**
   - Current: README 只说明下载 zip 和加载，未解释自动更新限制。
   - Target: 文档明确区分“后端自动应用”和“插件 sideload 自动提示”；不能写“插件会自动静默更新”。
   - Acceptance: README、README_EN、`docs/modules/extension.md`、`docs/modules/runtime.md` 均说明当前插件更新需要用户确认/重新加载。

9. **运行时事件推送**
   - Current: runtime stream 已可推状态事件，但无更新事件。
   - Target: 后端发现 `backend_update_available`、`extension_update_available`、`backend_update_failed`、`backend_restart_pending` 时推 runtime event。
   - Acceptance: 已打开 side panel 的用户无需刷新页面即可看到更新状态变化。

10. **测试覆盖**
    - Current: `tests/test_runtime_updater.py` 覆盖 backend tag filtering，extension 只有 release utils 测试。
    - Target: 新增/扩展 Python 和 extension node tests，覆盖版本解析、GitHub payload、apply guard、API schema、popup banner 和 dismiss 逻辑。
    - Acceptance: 定向验证命令通过：
      - `pytest tests/test_runtime_updater.py tests/test_api_app.py -k "update" -q`
      - `cd extension && node --test --experimental-strip-types tests/*update*.test.ts tests/release-utils.test.ts`

## API Shape

`GET /api/update-status`

```json
{
  "backend": {
    "state": "update_available",
    "auto_update_enabled": false,
    "current_version": "0.3.91",
    "latest_version": "0.3.92",
    "latest_tag": "backend-v0.3.92",
    "last_check_at": "2026-05-31T12:00:00Z",
    "last_error": "",
    "reason": "none"
  },
  "extension": {
    "state": "update_available",
    "reason": "none",
    "current_version": "0.3.61",
    "latest_version": "0.3.62",
    "latest_tag": "extension-v0.3.62",
    "browser_family": "chromium",
    "release_url": "https://github.com/whiteguo233/OpenBiliClaw/releases/tag/extension-v0.3.62",
    "asset_name": "openbiliclaw-extension-v0.3.62.zip",
    "asset_url": "https://github.com/whiteguo233/OpenBiliClaw/releases/download/extension-v0.3.62/openbiliclaw-extension-v0.3.62.zip",
    "last_check_at": "2026-05-31T12:00:00Z",
    "last_error": ""
  }
}
```

Request metadata:

- HTTP update/status requests should send `X-OpenBiliClaw-Extension-Version: <manifest.version>`.
- HTTP update/status requests should send `X-OpenBiliClaw-Extension-Family: chromium|firefox`.
- Runtime stream must use `?extension_version=<manifest.version>&extension_family=chromium|firefox` or send the same metadata as the first client message after connect; it cannot rely on custom WebSocket headers.
- Backend may also accept `extension_version` and `extension_family` query params for tests and CLI diagnostics.

Settings page config writes use the existing config API and must preserve unrelated scheduler fields. The relevant persisted fields are:

```json
{
  "scheduler": {
    "auto_update_enabled": false,
    "auto_update_check_interval_hours": 6
  }
}
```

`auto_update_enabled` defaults to `false` when absent. Saving this field must hot-reload the update scheduler: `true` enables scheduled backend check/apply, while `false` cancels or prevents future scheduled apply attempts. Manual update check and manual backend apply remain available in both states.

`POST /api/update/check`

```json
{
  "include_backend": true,
  "include_extension": true
}
```

Response: `200 OK` with the same shape as `GET /api/update-status`.

`POST /api/update/apply`

```json
{
  "target": "backend",
  "tag": "backend-v0.3.92"
}
```

Successful backend apply must not wait for `os.execv()` inside the HTTP request. It returns before process replacement:

```json
{
  "target": "backend",
  "state": "applying",
  "reason": "none",
  "accepted": true,
  "observe_via": "runtime-stream"
}
```

Status: `202 Accepted`. The backend then runs the apply flow in a tracked background task, emits `backend_restart_pending`, and may drop existing HTTP/WebSocket connections when `os.execv()` replaces the process. Clients should reconnect and call `GET /api/update-status` after the daemon is reachable again.

Apply outcomes are:

| Case | HTTP status | Response state | Response reason | `accepted` |
|------|-------------|----------------|-----------------|------------|
| Backend apply accepted | `202 Accepted` | `applying` | `none` | `true` |
| Backend apply already in progress | `409 Conflict` | `applying` | `already_applying` | `false` |
| Backend apply blocked by local state | `409 Conflict` | `blocked` | stable backend blocked reason | `false` |
| Extension apply requested in sideload channel | `409 Conflict` | `unsupported` | `extension_auto_apply_unsupported` | `false` |

Apply response `state` and `reason` are target-scoped and follow this Apply outcomes table, not the backend status state-to-reason table below. The extension apply response may use `state="unsupported"` with `reason="extension_auto_apply_unsupported"` even when `GET /api/update-status` reports `extension.state="update_available"`; the update exists, but the current extension distribution channel cannot self-apply it.

## Backend State Values

Backend `state` values:

- `unknown`: no check has completed in this process.
- `disabled`: scheduled auto-apply is disabled and no manual/status check result is available in this process.
- `checking`: a check is in flight.
- `up_to_date`: current version is at or above the selected backend candidate.
- `update_available`: a newer backend candidate exists and can be manually applied, or scheduled apply is waiting for due time.
- `applying`: backend update apply is running.
- `restart_pending`: update applied; process replacement is imminent.
- `blocked`: an update exists but local state prevents safe apply.
- `error`: network, dependency sync, git command, or restart failure.
- `unsupported`: install/runtime mode cannot be self-updated.

Backend state precedence after a check is: `unsupported`/`error`/`applying`/`restart_pending`/`blocked`/`checking` first, then `update_available`, then `up_to_date`, then `disabled`, then `unknown`. `auto_update_enabled=false` must not mask an available update; represent that as `state="update_available"` plus `auto_update_enabled=false`. If the only newer backend candidates are prereleases and `scheduler.auto_update_allow_prerelease=false`, report `state="up_to_date"` with `reason="prerelease_ignored"`.

Backend state-to-reason mapping for `GET /api/update-status` and `POST /api/update/check` backend objects:

| State | Allowed reasons |
|-------|-----------------|
| `unknown` | `none` |
| `disabled` | `none` |
| `checking` | `none` |
| `up_to_date` | `none`, `prerelease_ignored` |
| `update_available` | `none`, `not_due` |
| `applying` | `none`, `already_applying` |
| `restart_pending` | `none` |
| `blocked` | `dirty_worktree`, `untrusted_remote`, `missing_target_tag`, `branch_not_fast_forwardable`, `merge_or_rebase_in_progress` |
| `error` | `dependency_sync_failed`, `restart_failed`, `github_unreachable`, `no_backend_tag_yet` |
| `unsupported` | `unsupported_install_mode`, `unsupported_docker_runtime` |

## Backend State Reasons

Stable backend `reason` values are part of the contract and must not overlap with backend `state` values:

- `none`
- `already_applying`
- `not_due`
- `dirty_worktree`
- `unsupported_install_mode`
- `unsupported_docker_runtime`
- `untrusted_remote`
- `missing_target_tag`
- `branch_not_fast_forwardable`
- `merge_or_rebase_in_progress`
- `dependency_sync_failed`
- `restart_failed`
- `github_unreachable`
- `no_backend_tag_yet`
- `prerelease_ignored`

Apply-specific response values used by `POST /api/update/apply`:

Apply response `state` values:

- `unsupported`: requested target cannot self-apply in the current distribution channel.

Apply response `reason` values:

- `extension_auto_apply_unsupported`: `target="extension"` was requested, but the current sideload extension channel only supports update detection and user-guided reinstall.

## Extension State Values

Extension update object is always present in `GET /api/update-status`. Without extension metadata it returns:

```json
{
  "state": "unknown",
  "reason": "no_extension_metadata",
  "current_version": "",
  "latest_version": "",
  "latest_tag": "",
  "browser_family": "",
  "release_url": "",
  "asset_name": "",
  "asset_url": "",
  "last_check_at": "",
  "last_error": ""
}
```

Extension `state` values:

- `unknown`: no extension version/family was provided.
- `checking`: extension release check is in flight.
- `up_to_date`: installed extension version is at or above latest stable extension release.
- `update_available`: latest stable extension release is newer and the browser-family asset is present.
- `asset_missing`: matching release exists but the Chrome-compatible or Firefox asset is missing for this browser family.
- `error`: GitHub request or payload parsing failed.

Extension `reason` values:

- `none`
- `no_extension_metadata`
- `github_unreachable`
- `github_payload_invalid`
- `missing_browser_asset`
- `prerelease_ignored`

Extension state-to-reason mapping:

| State | Allowed reasons |
|-------|-----------------|
| `unknown` | `no_extension_metadata` |
| `checking` | `none` |
| `up_to_date` | `none`, `prerelease_ignored` |
| `update_available` | `none` |
| `asset_missing` | `missing_browser_asset` |
| `error` | `github_unreachable`, `github_payload_invalid` |

If the only newer extension releases are prereleases, report `state="up_to_date"` with `reason="prerelease_ignored"` unless a future extension prerelease opt-in is explicitly added. `asset_missing` always pairs with `reason="missing_browser_asset"` to keep state and reason namespaces distinct.

## Extension Update UX

Settings page “版本与更新” controls:

- Toggle: `自动更新`, persisted as `scheduler.auto_update_enabled`, default off.
- Numeric input: `检查间隔（小时）`, persisted as `scheduler.auto_update_check_interval_hours`, default `6`.
- Backend status row: current version, latest version, state, last check time, last error, `立即检查`, and `立即应用` when safe.
- Extension status row: current version, latest version, state, last check time, last error, and download/install actions when an update is available.

Opening the toggle should make the consequence explicit: scheduled backend updates may run `git fetch`, fast-forward to a trusted `backend-v*` tag, sync dependencies, and restart the backend process. The copy must not imply that enabling this switch silently updates the browser extension; sideloaded extensions still require user confirmation and reload.

The side panel update notice should be factual and action-oriented:

- Title: `插件有新版本`
- Body: `当前 v0.3.61，最新 v0.3.62。当前开发者模式安装需要你下载新版并重新加载。`
- Primary action: `打开下载页`
- Secondary action: `查看安装步骤`
- Tertiary action: `稍后提醒`

Dismiss state:

```json
{
  "dismissedExtensionUpdateTag": "extension-v0.3.62",
  "dismissedExtensionUpdateAt": "2026-05-31T12:00:00Z"
}
```

Default silence window: fixed 24 hours in v1; do not add a config field unless product requirements change. A newer tag must break the silence window by semver comparison, not lexical comparison.

Backend update UX is a passive settings/status row in v1, not a recommendation-page banner: show the default-off auto-update toggle, current/latest state, “立即检查”, and “立即应用” when safe. Do not add backend dismiss state unless a future design introduces proactive backend banners.

## Boundaries

**In scope:**

- Backend update status API and manual check/apply endpoints.
- Backend apply guardrails around git state, remote, target tag and concurrency.
- Extension release discovery through backend.
- Extension version metadata on update/status HTTP requests and runtime-stream query params or first client message.
- Side panel settings section, default-off auto-update toggle, and update banner.
- Runtime stream update events.
- README / module docs update.
- Python and extension tests.

**Out of scope:**

- Chrome Web Store / Edge Add-ons / AMO publication.
- Signing CRX/XPI packages.
- Managing CRX private keys or Firefox signing credentials.
- Enterprise policy installation.
- Docker image self-update. Docker users continue to run `git pull && docker compose up -d --build` or a future image release flow.
- Backend desktop package auto-update. Current policy says backend desktop packages are not published.
- Extension self-modification, writing into browser profile extension directories, or silent sideload replacement.
- Rollback after code execution. The updater may refuse unsafe states, but does not promise transactional rollback of arbitrary Python package installs.

## Constraints

- `auto_update_enabled` remains default `false`; absent config must be treated as disabled in both backend scheduler and settings UI.
- Settings UI must not enable scheduled backend apply until the user explicitly turns on the auto-update switch and saves the config.
- Automatic backend apply must not run if local files are dirty.
- Backend apply must not use GitHub `/releases/latest`.
- Extension latest discovery must filter by `extension-v*`; it must not assume the newest GitHub Release belongs to the plugin.
- Extension update UI must avoid implying the browser has already updated the plugin.
- Update status responses must not include cookies, API keys, local paths, git remotes with credentials, or command stdout/stderr that could contain secrets.
- Git command stderr may be logged locally at debug/warning level, but API responses should use stable summarized reasons.

## Acceptance Criteria

- [ ] `GET /api/update-status` returns backend status with current version even when no extension metadata is provided.
- [ ] `GET /api/update-status` with extension metadata returns extension current/latest/version comparison.
- [ ] Backend latest selection ignores `extension-v*`.
- [ ] Backend latest selection ignores prerelease suffixes by default.
- [ ] Dirty git worktree prevents backend apply.
- [ ] Diverged branch prevents backend apply.
- [ ] Unknown remote prevents backend apply with `reason="untrusted_remote"` unless it exactly matches `scheduler.auto_update_allowed_remotes`.
- [ ] Credentialed remote URLs always prevent backend apply with `reason="untrusted_remote"`; credentials are not stripped before allowlist matching.
- [ ] Successful backend apply runs fetch, fast-forward merge to target tag, dependency sync and restart.
- [ ] Concurrent apply requests are serialized; the second request returns `409 Conflict`, `state="applying"`, `reason="already_applying"`, and `accepted=false`.
- [ ] When `auto_update_enabled=false` and a newer stable backend tag exists, status reports `state="update_available"` and `auto_update_enabled=false`.
- [ ] When only newer backend prerelease tags exist and prerelease opt-in is disabled, status reports `state="up_to_date"` and `reason="prerelease_ignored"`.
- [ ] Fresh/default config shows the settings-page auto-update switch off.
- [ ] Saving the switch on writes `scheduler.auto_update_enabled=true` and hot-reloads the backend update scheduler.
- [ ] Saving the switch off writes `scheduler.auto_update_enabled=false` and prevents future scheduled backend apply.
- [ ] Saving a changed `检查间隔（小时）` writes `scheduler.auto_update_check_interval_hours` and hot-reloads the scheduler interval.
- [ ] `auto_update_enabled=false` prevents scheduled apply but not manual check or manual backend apply.
- [ ] The switch copy states it controls backend scheduled updates only and does not silently update sideloaded browser extensions.
- [ ] `POST /api/update/apply` with `target="extension"` returns `409 Conflict`, `state="unsupported"`, `reason="extension_auto_apply_unsupported"`, and `accepted=false`.
- [ ] Extension release discovery finds Chrome and Firefox assets from an `extension-v*` release.
- [ ] Invalid extension release payload reports `state="error"` and `reason="github_payload_invalid"`.
- [ ] Missing browser-specific asset returns `asset_missing`.
- [ ] Extension prerelease-only results report `state="up_to_date"` and `reason="prerelease_ignored"`.
- [ ] Side panel shows update notice when extension latest is greater than current.
- [ ] Dismissed update notice stays hidden for the same tag during the silence window.
- [ ] A newer extension tag shows the notice even if the previous tag was dismissed.
- [ ] Runtime stream emits `backend_update_available`, `extension_update_available`, `backend_update_failed`, and `backend_restart_pending`.
- [ ] README / README_EN document current plugin update limitation.
- [ ] Runtime and extension module docs describe the update contract.
- [ ] Targeted Python and extension tests pass.

## Ambiguity Report

| Dimension | Score | Min | Status | Notes |
|-----------|-------|-----|--------|-------|
| Goal Clarity | 0.92 | 0.75 | PASS | 后端自动应用、插件自动提示、未来原生通道分层明确 |
| Boundary Clarity | 0.90 | 0.70 | PASS | 当前不做商店发布、签名、自托管和插件静默替换 |
| Constraint Clarity | 0.86 | 0.65 | PASS | 浏览器平台限制、git 安全边界、默认关闭均锁定 |
| Acceptance Criteria | 0.86 | 0.70 | PASS | API、后端 guard、插件 UI、docs、tests 均有 pass/fail |
| **Ambiguity** | **0.14** | **<=0.20** | **PASS** | |

## Approach Trade-offs

| Approach | Pros | Cons | Decision |
|----------|------|------|----------|
| 商店优先，直接发布 Chrome Web Store / Edge / AMO | 插件真正自动更新，用户体验最好 | 需要账号、审核、隐私材料、发布节奏和商店政策成本；不解决当前 sideload 用户 | Future |
| 自托管 CRX/XPI `update_url` | 可控，理论上可自建更新 manifest | Chrome Windows/macOS 普通用户不可直接使用自托管安装；需要签名密钥管理；当前 zip 分发不兼容 | Future/Enterprise |
| 混合模型：后端自动应用，插件自动发现 + 引导更新 | 符合当前架构，能快速落地，不误导用户 | 插件仍需用户手动确认和重新加载 | Chosen |

## Research Notes

- Chrome extension distribution docs: https://developer.chrome.com/docs/extensions/how-to/distribute
- Chrome self-host update docs for Linux/managed cases: https://developer.chrome.com/docs/extensions/how-to/distribute/host-on-linux
- Microsoft Edge externally installed extension update docs: https://learn.microsoft.com/en-us/microsoft-edge/extensions/update/auto-update
- Firefox self-distribution docs: https://extensionworkshop.com/documentation/publish/self-distribution/
- Firefox temporary installation docs: https://extensionworkshop.com/documentation/develop/temporary-installation-in-firefox/

## Interview Log

| Round | Perspective | Question summary | Decision locked |
|-------|-------------|------------------|-----------------|
| 0 | Researcher | 当前后端是否已有更新能力 | 有 `AutoUpdateService`，但应用阶段需要补 git 安全 guard 与状态机 |
| 0 | Researcher | 当前插件发布形态 | `extension-v*` GitHub Release zip，Chrome-compatible 与 Firefox zip 分开 |
| 1 | Platform reviewer | sideload 插件能否静默自更新 | 不能作为普通用户能力承诺；只能自动发现并引导用户更新 |
| 1 | Product reviewer | 是否现在上商店或做 CRX/XPI 签名 | 不纳入首版，作为 future native channel |
| 2 | Security reviewer | 后端自动执行远端代码的保护 | 默认关闭，clean worktree、可信 remote、fast-forward-only、稳定 reason |
| 2 | UX reviewer | 已打开插件如何知道有更新 | runtime stream + update-status API + dismissible banner |

---

*Next step: convert this SPEC into an implementation plan with tests-first tasks.*
