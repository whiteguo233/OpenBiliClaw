# 后端自动更新 SPEC

**Created:** 2026-05-31
**Updated:** 2026-05-31
**Ambiguity score:** 0.10 (gate: <= 0.20)
**Requirements:** 8 locked

## Goal

为 OpenBiliClaw 后端建立一套可解释、可手动触发、默认关闭、能拒绝不安全状态的源码自动更新机制：

- 后端在 git 源码安装场景下可以自动发现新的 `backend-vX.Y.Z` 版本。
- 用户显式开启后，后端可以在安全条件满足时自动快进到目标 tag、同步依赖并重启当前进程。
- 用户没有开启自动更新时，仍可在设置页手动检查和手动应用后端更新。
- 浏览器插件不再纳入 OpenBiliClaw 自建自动更新系统；Chrome Web Store / Edge Add-ons / AMO 版本交给浏览器原生更新通道，GitHub zip / sideload 用户按文档手动更新。

核心原则：**自动更新只控制后端源码；浏览器插件由浏览器分发平台或用户手动安装流程负责。**

## Background

仓库已拆成两条发布通道：

- 后端源码版本用 `backend-vX.Y.Z` tag 标记，`.github/workflows/release-backend.yml` 目前只校验 tag 与 `pyproject.toml` 版本一致，不发布后端桌面包。
- 浏览器插件用 `extension-vX.Y.Z` tag 发布 GitHub Release，`.github/workflows/release-extension.yml` 会上传 `openbiliclaw-extension-vX.Y.Z.zip` 和 `openbiliclaw-extension-vX.Y.Z-firefox.zip`。
- Chrome Web Store 上传已独立为手动 workflow：`.github/workflows/publish-chrome-webstore.yml`。它可以用 GitHub Secrets 上传 Chrome-compatible zip，并在显式 `publish=true` 时提交审核。
- 后端已有 `src/openbiliclaw/runtime/updater.py`：周期性查询 GitHub `/tags`，过滤 `backend-v*`，忽略 `extension-v*`，REST API 限流时通过 GitHub tags Atom feed 兜底，发现新版本后执行 `git pull --ff-only`、依赖同步和进程重启。
- 配置已有 `scheduler.auto_update_enabled=false` 和 `scheduler.auto_update_check_interval_hours=6`，默认关闭。

插件更新不再由后端查询 `extension-v*` release 或由 side panel 维护更新横幅：

- Chrome Web Store / Edge Add-ons / AMO 安装的扩展由浏览器原生机制自动检查和安装更新。
- GitHub zip、开发者模式加载、Firefox 临时加载等 sideload 场景没有可靠的静默自替换语义，继续按 README / release 文档手动下载和重新加载。
- 后端自动更新 API 不需要返回插件 latest、插件下载资产、插件 dismiss 状态，也不提供 `target="extension"` 的 apply 流程。
- 为兼容未来或旧客户端，后端更新 API 必须忽略任何扩展版本 / 浏览器族 headers 或 query params；这些 metadata 不参与响应形状、状态机或更新判断。

## Chosen Approach

采用单层后端更新模型。

### 后端源码自动更新

后端继续以 `backend-v*` tag 为权威版本来源，但补齐安全边界：

- 只在 git 源码安装且 worktree 干净时自动应用。
- 自动应用前执行 `git fetch --force --tags origin`，解析目标 tag commit，并用 `git merge --ff-only <tag>` 快进当前分支到目标 tag。
- 当前分支无法快进、远端不可信、worktree dirty、运行在 Docker/只读包/非 git 环境时，不自动修改文件，只上报明确状态。
- 依赖同步沿用当前检测逻辑：存在 `uv.lock` 时执行 `uv sync`，否则执行 `python -m pip install -e .`。
- 成功后重启当前进程；如果是 systemd、Docker 或外部 supervisor 管理，首版只支持当前 `os.execv` 重启，不主动操作外部 supervisor。

### 插件更新边界

OpenBiliClaw 后端和插件 UI 不实现插件自动更新检测。插件更新由安装渠道负责：

- Chrome / Edge 商店版本由浏览器自动更新。
- Firefox AMO 版本由 Firefox 自动更新。
- GitHub Release zip / sideload 用户从 release 页面下载新版并按浏览器要求重新加载。

设置页可以展示当前插件版本和一个固定的 GitHub Releases 链接，作为 sideload fallback 和发布历史入口。v1 不按安装来源分流到 Chrome Web Store / Edge Add-ons / AMO，不做 latest 比较、不解析 extension release 资产、不维护更新横幅或关闭提醒状态。

## Requirements

1. **后端更新状态 API**
   - Current: `/api/runtime-status` 只合并已有 auto updater 字段。
   - Target: 新增 `GET /api/update-status`，返回单一 `backend` 对象；`/api/runtime-status` 可保留摘要字段。
   - Acceptance: 响应不依赖任何插件 metadata，固定返回 `backend` 对象且不包含 `extension` 对象；旧客户端发送的扩展版本 / 浏览器族 headers 或 query params 必须被忽略，不能改变响应或报错。

2. **后端版本发现**
   - Current: `AutoUpdateService._fetch_latest_version()` 查询 `/tags` 并过滤 backend-like tag；REST API quota 耗尽时会读取 GitHub tags Atom feed 继续选择同一批 release tag。当前实现把 `backend-v*`、legacy `v*` 和裸 semver 放进同一个候选池，且 `_parse_backend_version("backend-v0.3.100-rc1")` 会解析成 `(0, 3, 100)`。远端历史里 `v0.3.90` 与 `backend-v0.3.89` 并存时，当前 `max()` 会选择裸 `v0.3.90`，而不是 canonical backend tag。
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
   - Acceptance: 设置页点击“立即检查”不需要等待后台 interval；`auto_update_enabled=false` 时仍允许用户手动检查和手动应用，但不会自动定时应用；`POST /api/update/apply` 的 `target` 只允许 `"backend"`，其他 target 返回验证错误且不会进入 apply 流程。

6. **设置页后端版本与更新 UI**
   - Current: popup/side panel 没有专门的后端版本与更新入口。
   - Target: 设置页新增“版本与更新”区块：显示后端当前/最新、上次检查时间、错误；提供“自动更新”开关，绑定 `scheduler.auto_update_enabled`，缺省为关闭；提供“检查间隔（小时）”数字输入，绑定 `scheduler.auto_update_check_interval_hours`；提供“立即检查”和安全时的“立即应用”。
   - Acceptance: 首次安装或缺省配置时开关显示关闭；用户打开并保存后写入 `scheduler.auto_update_enabled=true` 并刷新后端更新调度；用户关闭并保存后写入 `false`，停止后续定时自动应用；修改检查间隔并保存后刷新调度间隔；这些设置不影响“立即检查”和“立即应用”。

7. **运行时事件推送**
   - Current: runtime stream 已可推状态事件，但无后端更新事件。
   - Target: 后端发现 `backend_update_available`、`backend_update_failed`、`backend_restart_pending` 时推 runtime event。
   - Acceptance: 已打开 side panel / Web 的用户无需刷新页面即可看到后端更新状态变化。

8. **测试覆盖**
   - Current: `tests/test_runtime_updater.py` 覆盖 backend tag filtering。
   - Target: 新增/扩展 Python tests，覆盖版本解析、GitHub payload、apply guard、API schema、状态机和 runtime events。
   - Acceptance: 定向验证命令通过：
     - `pytest tests/test_runtime_updater.py tests/test_api_app.py -k "update" -q`

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
  }
}
```

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
  "include_backend": true
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
| Invalid target, including `extension` | `422 Unprocessable Entity` | n/a | validation error | n/a |

`target` is an enum with one valid v1 value: `"backend"`. `target="extension"` and any unknown target are request validation errors, not update-state transitions; they must not reuse backend `state` / `reason` values and must not enter the apply lock.

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
| `error` | `dependency_sync_failed`, `restart_failed`, `github_rate_limited`, `github_unreachable`, `no_backend_tag_yet` |
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
- `github_rate_limited`
- `github_unreachable`
- `no_backend_tag_yet`
- `prerelease_ignored`

## Settings UX

Settings page “版本与更新” controls:

- Toggle: `自动更新`, persisted as `scheduler.auto_update_enabled`, default off.
- Numeric input: `检查间隔（小时）`, persisted as `scheduler.auto_update_check_interval_hours`, default `6`.
- Backend status row: current version, latest version, state, last check time, last error, `立即检查`, and `立即应用` when safe.
- Frozen desktop installs (`install_mode="frozen"`): the toggle and interval stay disabled (they govern auto-apply, which frozen can never do), but the backend runs an unconditional check-only loop against `desktop-v*` installer tags. The status row shows "发现新版安装包 vX.Y.Z…请下载新版安装包" with a download link to the discovered `desktop-v*` release tag, `立即检查` stays available, `立即应用` stays hidden, and `backend_update_available` events additionally raise a toast reminder.
- Optional plugin version row: current extension version read locally from the extension manifest/runtime and a fixed link to the GitHub Releases page. This row must be informational only; it does not detect install channel, does not call backend update APIs, does not ask the backend to echo the extension version through config/status APIs, and does not claim to manage plugin updates. Store-installed users receive browser-native updates; the GitHub link is only a fallback / release-history entry.

Opening the toggle should make the consequence explicit: scheduled backend updates may run `git fetch`, fast-forward to a trusted `backend-v*` tag, sync dependencies, and restart the backend process. The copy must not imply that enabling this switch updates the browser extension.

Backend update UX is a passive settings/status row in v1, not a recommendation-page banner: show the default-off auto-update toggle, current/latest state, “立即检查”, and “立即应用” when safe. Do not add backend dismiss state unless a future design introduces proactive backend banners.

## Boundaries

**In scope:**

- Backend update status API and manual check/apply endpoints.
- Backend apply guardrails around git state, remote, target tag and concurrency.
- Side panel settings section and default-off auto-update toggle.
- Runtime stream backend update events.
- README / module docs update.
- Python tests.

**Out of scope:**

- Extension release discovery through backend.
- Using extension version metadata on update/status HTTP requests or runtime stream. Receiving and ignoring legacy metadata is in scope for compatibility.
- Extension update banners, dismiss windows, download asset parsing, or `target="extension"` apply support.
- Chrome Web Store / Edge Add-ons / AMO publication automation. That remains in the separate `Publish Chrome Web Store Package` workflow and release docs.
- Signing CRX/XPI packages.
- Managing CRX private keys or Firefox signing credentials.
- Enterprise policy installation.
- Docker image self-update. Docker users continue to run `git pull && docker compose up -d --build` or a future image release flow.
- Backend desktop package self-APPLY. Desktop installers are published under `desktop-v*` tags, but a frozen bundle can never self-apply (the binary is the code; `request_apply` refuses non-git installs with `unsupported_install_mode`). Frozen installs run a check-only reminder loop against `desktop-v*` installer tags and guide the user to download the new installer — see Settings UX.
- Extension self-modification, writing into browser profile extension directories, or silent sideload replacement.
- Rollback after code execution. The updater may refuse unsafe states, but does not promise transactional rollback of arbitrary Python package installs.

## Constraints

- `auto_update_enabled` remains default `false`; absent config must be treated as disabled in both backend scheduler and settings UI.
- Settings UI must not enable scheduled backend apply until the user explicitly turns on the auto-update switch and saves the config.
- Automatic backend apply must not run if local files are dirty.
- `POST /api/update/apply` must validate `target` as `"backend"` only. Extension and unknown targets are `422` validation errors, not supported update states.
- Backend apply must not use GitHub `/releases/latest`.
- Backend latest selection must ignore `extension-v*`.
- Extension version/family metadata sent by old or future clients must be ignored by backend update APIs.
- Update status responses must not include cookies, API keys, local paths, git remotes with credentials, or command stdout/stderr that could contain secrets.
- Git command stderr may be logged locally at debug/warning level, but API responses should use stable summarized reasons.
- Plugin copy must state that browser extension updates are handled by the browser/store channel or by manual sideload reloads, not by the backend auto-update toggle.

## Acceptance Criteria

- [ ] `GET /api/update-status` returns backend status with current version and no `extension` object.
- [ ] `GET /api/update-status` ignores extension version / family headers and query params sent by legacy clients.
- [ ] `POST /api/update/check` ignores extension version / family headers and query params sent by legacy clients and returns the same response shape as without them.
- [ ] `POST /api/update/apply` with `target="backend"` ignores extension version / family headers and query params sent by legacy clients and is not rejected as a malformed request because of that metadata.
- [ ] `/api/runtime-stream` accepts and discards `extension_version` / `extension_family` query params and any first-message extension metadata without changing emitted backend update events.
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
- [ ] `POST /api/update/apply` with `target="extension"` or an unknown target returns `422 Unprocessable Entity` and does not enter the apply lock.
- [ ] The switch copy states it controls backend scheduled updates only and does not update browser extensions.
- [ ] Optional plugin version row reads the current version locally from the extension manifest/runtime, links to GitHub Releases only, and does not attempt install-channel detection, backend echo, or latest-version comparison.
- [ ] Runtime stream emits `backend_update_available`, `backend_update_failed`, and `backend_restart_pending`.
- [ ] README / README_EN document browser extension updates as store-native or manual sideload reloads.
- [ ] Runtime module docs describe the backend update contract.
- [ ] Extension module docs point plugin release/store updates to the release workflow and browser store channel, not the backend auto-update API.
- [ ] Targeted Python tests pass.

## Ambiguity Report

| Dimension | Score | Min | Status | Notes |
|-----------|-------|-----|--------|-------|
| Goal Clarity | 0.94 | 0.75 | PASS | 自动更新范围明确收窄为后端源码 |
| Boundary Clarity | 0.94 | 0.70 | PASS | 插件更新、商店发布、sideload 替换均明确出界 |
| Constraint Clarity | 0.88 | 0.65 | PASS | git 安全边界、默认关闭、插件不由 toggle 管理均锁定 |
| Acceptance Criteria | 0.86 | 0.70 | PASS | API、后端 guard、UI、docs、tests 均有 pass/fail |
| **Ambiguity** | **0.10** | **<=0.20** | PASS | |

## Approach Trade-offs

| Approach | Pros | Cons | Decision |
|----------|------|------|----------|
| 后端-only 自动更新，插件交给商店/浏览器 | 范围清晰；避免维护插件 latest/dismiss/asset 逻辑；符合当前 Chrome Web Store 发布方向 | sideload 用户不会收到内置更新提示 | Chosen |
| 混合模型：后端自动应用，插件自动发现 + 引导更新 | 能照顾 GitHub zip/sideload 用户 | 增加 API/UI/测试复杂度；与商店原生自动更新重复；容易让用户误解插件也由后端更新 | Rejected |
| 自托管 CRX/XPI `update_url` | 可控，理论上可自建更新 manifest | Chrome Windows/macOS 普通用户不可直接使用自托管安装；需要签名密钥管理；当前 zip 分发不兼容 | Future/Enterprise only |

## Research Notes

- Chrome extension distribution docs: https://developer.chrome.com/docs/extensions/how-to/distribute
- Chrome self-host update docs for Linux/managed cases: https://developer.chrome.com/docs/extensions/how-to/distribute/host-on-linux
- Microsoft Edge externally installed extension update docs: https://learn.microsoft.com/en-us/microsoft-edge/extensions/update/auto-update
- Firefox self-distribution docs: https://extensionworkshop.com/documentation/publish/self-distribution/
- Firefox temporary installation docs: https://extensionworkshop.com/documentation/develop/temporary-installation-in-firefox/

## Interview Log

- 2026-05-31: 初始目标是后端与插件两端自动更新；review 后补齐 API、状态机、配置项、插件 sideload 限制。
- 2026-05-31: Chrome Web Store 上传与权限收窄流程落地后，决策改为后端-only 自动更新；插件更新交给浏览器商店原生通道，GitHub zip/sideload 作为手动 fallback。
