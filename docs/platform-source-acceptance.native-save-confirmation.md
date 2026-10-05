# YouTube / 知乎原生保存确认验收报告

> 本报告只覆盖 `native-save` 能力增量，不把既有 discovery、profile、init 或推荐能力重复算作本次交付。适用性只写 `required / N/A`，执行状态只写 `PASS / FAIL / NOT_RUN / BLOCKED`。真实账号写入只有在取得精确的当次授权后才执行。

## 范围与 provenance

- Integration level: `capability-increment`
- Contracts: `docs/platform-source-contract.youtube-native-save-confirmation.toml`；`docs/platform-source-contract.zhihu-native-save-confirmation.toml`
- Worktree / base commit: `/Users/white/workspace/OpenBiliClaw/.worktrees/fix-native-save-confirmation` / `bbb699932d278554387313afbde64f5edc4a3974`
- Branch: `fix/native-save-confirmation`
- Python import / CLI: `PYTHONPATH=$PWD/src` 下 `$PWD/src/openbiliclaw/__init__.py` / `/Users/white/workspace/OpenBiliClaw/.venv/bin/python`
- Backend bind / data / config root: `PASS`；隔离服务仅监听 `127.0.0.1:18420`，数据根 `/tmp/obc-native-save-e2e.jPH94X/data`，未复用现有 `8420` 的数据或配置
- Browser / extension / version: `PASS`；复用用户当前已连接的 Chrome 扩展，`0.3.213`，通过现有热加载链路切换到本 worktree 构建
- Build roots: Chrome `$PWD/extension`；Firefox `$PWD/extension/dist-firefox`
- Build SHA-256: Chrome aggregate `662934e06d74f953e8225afb6ccb52ddaf14b7ee918a7cf32bc3fbb79de91942`；Firefox aggregate `e9a0a7d3c697382c4d7d7869ffe511ea065c7d1dfcb762e4357fdfde6751c049`
- Existing user changes preserved: `yes`；未覆盖、清理或提交工作区既有改动；原扩展构建已逐文件恢复、`8420` 热加载 `delivered=true`，隔离 `18420` 已停止

## Gate ledger

| Gate | Applicability | Status | Evidence | Remaining risk |
| --- | --- | --- | --- | --- |
| Scope/worktree provenance | required | PASS | 独立 worktree、分支与 base SHA 已记录 | 尚未提交；用户未授权 commit/push |
| Frozen source contracts | required | PASS | 两份 capability contract；自动 audit 各 `MISSING=0`，metrics 6/6 | audit 只证明 wiring，不替代语义/E2E |
| Historical precedent + repair review | required | PASS | 对照六平台 runner、YouTube/知乎 executor、DB callback 与真实 E2E runbook | 无 |
| Real upstream spike + redacted fixtures | required | PASS | 既有真实页面实现 + YouTube/知乎 DOM fixture；本次补 delayed-confirmation 与安全 opener fixture | 页面未来变更仍需真账号验证 |
| Canonical registry / identity / storage | required | PASS | `youtube:<11-char-id>`、`zhihu:<typed-id>`；durable job canonical terminal replay 测试 | 无 |
| Transport / normalizer / error taxonomy | required | PASS | exact execution/document correlation、AbortSignal、有界 callback retry、固定错误 allowlist | 无 |
| Shared capability/auth readiness prerequisite | required | PASS | native-save 明确为 login-required；知乎 heartbeat 只作 readiness hint；两平台真账号 UI readiness 已核对 | 无 |
| Auth / credential / account resolution | required | PASS | 知乎真实页可读取“已收藏”；YouTube 真页存在账号头像且无登录入口 | 不读取账号标识/Cookie |
| Browser task / MV3 recovery | required | PASS | runner-owned tab、session recovery、fresh document READY 回归测试；真实热加载 `delivered=true`；最终 Chrome open tab count 为 0 | 用户原构建与 `8420` endpoint 已恢复 |
| Bootstrap / event / init | N/A | PASS | 契约排除测试；能力增量不改 bootstrap/event/init | 无 |
| Post-init incremental lifecycle | N/A | PASS | 契约排除测试；不改 incremental lifecycle | 无 |
| Formal discover / keyword dual-track / admission | N/A | PASS | 契约排除测试；不改 discover | 无 |
| Eval / publication time / recommendation | N/A | PASS | 契约排除测试；native-save 不读取/合成 publication time | 无 |
| Config / API / status convergence | required | PASS | backend callback exact replay 200、changed terminal 409；API 回归 39/39；隔离后端真请求完成 | 无 |
| Setup surface | N/A | PASS | 两份 contract `setup=false` + direct node assertions | 无 |
| Desktop surface | N/A | PASS | 既有 shared saved surface 不变；本次仅任务终态确认 | 无 |
| Mobile surface | N/A | PASS | 既有 shared saved surface 不变；本次仅任务终态确认 | 无 |
| Extension popup surface | N/A | PASS | 不新增 popup 交互；复用现有扩展热加载与 backend endpoint 配置，测试后恢复 `8420` | 无 |
| Mobile credential management | N/A | PASS | native-save 使用浏览器内登录态，不向移动端收集凭据；direct contract test | 无 |
| Image delivery | N/A | PASS | 不读取或传输图片；direct contract test | 无 |
| Image proxy DNS / redirect / SSRF boundary | N/A | PASS | 不使用图片代理；direct contract test | 无 |
| Mobile deep link | N/A | PASS | 不改 deep link；direct contract test | 无 |
| Native save | required | PASS | mutation uncertainty 只允许 fresh-document read-only verifier；知乎全局开关零点击复核；YouTube 新版内层 menuitem 状态/点击点已真页验证 | 无 |
| Focused + full backend verification | required | PASS | focused API 39/39、contract 33/33、CLI 258/258；修复完成后全量 8655 passed / 99 skipped | 无；仅有既有只读 DMG cleanup warning |
| Chrome + Firefox tests/build/assets | required | PASS | 1444/1444 extension tests、typecheck、双浏览器 build、各 19 assets | 无 |
| Safe real E2E | required | PASS | 隔离 backend/data + 当前 Chrome 扩展；YouTube 未登录时稳定 `login_required`、登录后身份 UI 通过，task-mode passive event delta 为 0 | 无 |
| State-changing E2E | required | PASS | 知乎 `already_synced`；YouTube favorite `already_synced`；YouTube watch-later `synced` | 三次均使用独立新鲜五字段授权 |
| Documentation / release-readiness | required | PASS | changelog、模块文档、中英文 README、架构/规格图、E2E runbook、两份 contract 与本报告实绩同步 | 无 |
| Commit/version/tag/push/publish mutations | N/A | PASS | 用户未请求；未执行 | 无 |

## Transport、身份与数据契约

- Primary / fallback owner: `extension / none`
- Auth comparison: 公开 discovery 的匿名能力不构成本次 native-save 认证；写入与确认都要求同一浏览器当前登录态。知乎 `z_c0` heartbeat 仅为布尔 readiness，不能证明目标收藏关系。
- Account resolution and mismatch behavior: 不导出 Cookie 或账号标识；执行 tab 缺登录 UI 证据时返回稳定 `login_required`，不得写入。
- Stable identity / URL / dedupe: YouTube 精确 11 位视频 ID + watch/shorts/youtu.be URL；知乎精确 `question:<id>` / `answer:<id>` / `article:<id>` + 同 ID URL；durable ledger 以平台、ID、action 去重。
- Upstream envelope / pagination / empty / partial / rate-limit: 每任务一个公开 item、一个精确目标，不分页、不声明 partial；未观察到正向 membership 时保持 `native_confirmation_not_observed`。
- Terminal evidence: mutation document 的精确 checked/selected 证据，或终止并等待旧 sender 后，在不同 document instance 的只读 verifier 中得到 `already_synced`。

## 命令与自动验证

| Command | Exit | Summary / artifact |
| --- | ---: | --- |
| `scripts/audit_platform_source.py --contract docs/platform-source-contract.youtube-native-save-confirmation.toml --check --json` | 0 | `registration_check_passed=true`，`MISSING=0`，`PASS=26`，`N/A=17`，`MANUAL=11` |
| `scripts/audit_platform_source.py --contract docs/platform-source-contract.zhihu-native-save-confirmation.toml --check --json` | 0 | `registration_check_passed=true`，`MISSING=0`，`PASS=27`，`N/A=16`，`MANUAL=11` |
| `pytest -q tests/test_native_save_confirmation_contract.py` | 0 | 33 passed；含 capability exclusions，no skip/xfail |
| `pytest -q tests/test_extension_native_save_api.py` | 0 | 39 passed |
| `pytest -q tests/test_cli.py` | 0 | 258 passed |
| full backend `pytest -q` | 0 | 8655 passed / 99 skipped / 5136 warnings，1049.47s；warning 为既有只读 DMG cleanup |
| `ruff check src tests` | 0 | passed |
| `mypy src` | 0 | success，263 source files |
| `npm test` | 0 | 1444/1444 passed |
| `npm run typecheck` | 0 | passed |
| Chrome `npm run build && npm run verify:assets` | 0 | 19/19 assets；aggregate SHA-256 见 provenance |
| Firefox `npm run build:firefox && npm run verify:assets:firefox` | 0 | 19/19 assets；aggregate SHA-256 见 provenance |
| package/install provenance | 0 | build roots 与 `0.3.213` 已记录；未 package/release；复用并最终恢复用户现有 Chrome 扩展 |

## 真实 E2E

| Scenario | Applicability | Status | Counts / DB sample / idempotency | Diagnostic / artifact |
| --- | --- | --- | --- | --- |
| Isolated backend/config/data | required | PASS | `127.0.0.1:18420`；临时 data root；auto-sync/scheduler off；health PASS | 未触碰现有 `8420` 的数据/配置 |
| Authenticated / verified identity | required | PASS | 知乎真页可读取保存状态；YouTube 真页存在账号头像且无登录入口 | 不输出账号标识/Cookie |
| Rejected/expired credential | N/A | PASS | executor fixture 覆盖 `login_required` | 不对真账号破坏登录态 |
| Empty vs no-observer vs partial vs rate-limit | required | PASS | fixture/runner 回归覆盖；atomic task 无 partial | 真环境不主动注入故障 |
| Duplicate/retry/crash recovery | required | PASS | exact callback replay 单测 PASS；首次 YouTube 不确定终态未盲重试，修复热加载并取得新授权后 favorite 返回 `already_synced` | callback retry 不触发第二次平台 mutation |
| Task-mode passive event delta | required | PASS | 全部真实任务后 `events=0`；saved-items 保持 3；仅 task/item/job ledger 由 0 增至 6 | 无被动采集副作用 |
| YouTube favorite / watch-later | required | PASS | selector 修复并重新授权后，favorite `S6TIVzqTmu8` → `already_synced`；watch-later `IEpTT48oW5Y` → `synced` | exact targets 分别为 `OpenBiliClaw` / `YouTube Watch Later` |
| 知乎 favorite | required | PASS | 修复版对 `answer:2020230143994512605` 真请求返回 `already_synced`；页面仍为一个精确回答容器内唯一“已收藏”控件 | 初始已收藏路径零点击；不声明旧命名收藏夹 |
| Extension popup surface and actions | N/A | PASS | 只设置隔离 backend endpoint，不验收新 UI | 安装时仍需用户操作确认 |

- LLM provider / model / route: `N/A`；native-save E2E 不调用 LLM。
- Task-mode passive event delta: `PASS`；`events` 仍为 0。
- Smoke projection deltas: `PASS`；`saved_items` 保持 3，`native_save_tasks/items/jobs` 仅按六次实际请求从 0 增至 6；最终 task/job 状态同为 2 `already_synced`、2 `failed`、1 `login_required`、1 `synced`。
- State-changing upstream actions actually run: 旧知乎首轮由“收藏”变为“已收藏”，修复版随后零点击 `already_synced`；YouTube 首轮错误 selector 未写入，修复并重新授权后 favorite 为 `already_synced`、watch-later 为 `synced`。未执行任何取消收藏、移除 playlist 或清理平台保存的动作。
- Environment cleanup: 原扩展 dist 与备份逐文件一致，`8420` reload `delivered=true`；隔离 PID 与 `127.0.0.1:18420` listener 已停止；Chrome 临时任务 tab 数为 0。

## 最终结论

- Verdict: `PASS (capability increment)`
- Required rows not PASS: `none`。
- Intentional exclusions: bootstrap/profile/incremental/discover/eval/recommendation/setup/mobile credential/image/proxy/deep-link；均由两份 capability contract 与 `tests/test_native_save_confirmation_contract.py` 直接断言。
- Deferred work / blockers: `none`。
- Release mutations performed: `none`。
