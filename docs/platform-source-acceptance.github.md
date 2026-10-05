# GitHub 平台来源验收报告

> Integration level 为 `full`。适用性只写 `required / N/A`，执行状态只写 `PASS / FAIL / NOT_RUN / BLOCKED`。本文件随实现持续更新；只有全部 required 行 PASS，最终结论才可为 `complete`。

## 范围与 provenance

- Integration level: `full`
- Contract: `docs/platform-source-contract.github.toml`
- Worktree / commit: `/Users/white/workspace/OpenBiliClaw/.worktrees/github-source` / `a54719020d7796a7364163569ec37fe4c79296e8` (baseline); evidence below applies to the current uncommitted `feat/github-source` tree
- Python import / CLI: `/Users/white/workspace/OpenBiliClaw/.worktrees/github-source/src/openbiliclaw/__init__.py` / `/Users/white/workspace/OpenBiliClaw/.venv/bin/python -m openbiliclaw.cli`
- Backend bind / data / config root: CLI smoke used the current worktree and reported zero local/LLM/upstream writes; a full loopback API + isolated-root run has not been recorded
- Browser / extension ID / version: automated extension suite, typecheck and both browser builds passed; no installed-browser GitHub action E2E recorded
- Installed build/package path + SHA-256: Chrome and Firefox local build/asset verification passed; install path and SHA-256 were not recorded, so this is not installed-build provenance
- Existing user changes preserved: yes; main worktree was clean and all feature work is isolated in `feat/github-source`

## Gate ledger

| Gate | Applicability | Status | Evidence | Remaining risk |
| --- | --- | --- | --- | --- |
| Scope/worktree provenance | required | PASS | Clean main baseline `a547190`; dedicated worktree and branch recorded above; import resolves inside worktree | Final artifact provenance still pending |
| Frozen source contract | required | PASS | `docs/platform-source-contract.github.toml` frozen before source implementation; cross-document static omission audit completed against Bangumi/V2EX precedent and the mandatory documentation roster | The audit inventory still leaves 12 semantic/E2E items MANUAL by design |
| Historical precedent + repair review | required | PASS | Bangumi, V2EX and the existing GitHub-star service were compared before implementation; an independent final read-only review found no blocking defects after the reported documentation, status, identity and inspiration-partial repairs | Review does not replace the real-E2E gates below |
| Real upstream spike + redacted fixtures | required | PASS | Redacted fixtures exist under `tests/fixtures/github/`; anonymous search/repository/starred and affirmative-empty responses were observed, with later anonymous `rate_limited`; 401 missing/bad credential also observed | Valid PAT response is still unavailable |
| Canonical registry / identity / storage | required | PASS | Registration audit reports 33 PASS / 0 missing; focused tests cover numeric repository identity, aliases, private-row rejection and canonical storage | Real migration against a long-lived user DB not run |
| Transport / normalizer / error taxonomy | required | PASS | Dedicated GitHub client/source/producer suite passed; covers JSON/content type, RFC Link pagination, 304/401/403/422/429/503, incomplete results, public-query sanitization, rejected rows, caps and Star `partial_timeout` after completed pages | Live taxonomy did not exercise every status |
| Shared capability/auth readiness prerequisite | N/A | N/A | Auth mode is anonymous-with-optional-credentials, not capability-specific | None |
| Auth / credential / account resolution | required | PASS | Source-auth tests passed for mocked valid/rejected PAT, username scope, write-only secret and rejection of `GITHUB_TOKEN` / `GH_TOKEN`; fingerprint-scoped 401 status regressions cover unavailable profile/bootstrap projection and recovery after token rotation | Valid-PAT authenticated/verified identity remains BLOCKED; this row proves implementation behavior only |
| Browser task / MV3 recovery | N/A | N/A | Exact contract/surface tests passed: no GitHub host permission, content script, task runtime, cookie sync or background asset | Installed extension still not inspected |
| Bootstrap / event / init | required | PASS | Unit/CLI/API parity tests cover public starred → `favorite`, identity mismatch, completed-page timeout preservation, partial/cap outcomes, stable init reasons and mixed-init isolation of GitHub failure | Full `account → event → profile` with real LLM remains NOT_RUN |
| Post-init incremental lifecycle | N/A | N/A | Exact exclusion test passed; source is init-and-on-demand only | No background account refresh is claimed |
| Formal discover / keyword dual-track / admission | required | PASS | Producer/CLI tests plus live search/ranked/latest smoke; inspiration axis, persisted cooldown shared by formal/inspiration and the shared public-query sanitizer passed. Rejected-only, `incomplete_results` and search-cap regressions prove inspiration keeps accepted rows as partial/degraded and never reports those outcomes as affirmative empty or complete. A real SenseNova run completed GitHub discovery→eval→admission on 2026-09-03 | No installed-browser action; real-LLM recommendation details in E2E row |
| Eval / publication time / recommendation | required | PASS | Static/unit parity covers `repository` text cards, `created_at` publication and local-only save. Real 2026-09-03 SenseNova E2E returned GitHub repository recommendations with expression copy; `recommend` surfaced 5 GitHub/text cards. Current cards visibly render owner/name, description and stars; richer source metadata is retained but not consumed by frontend JS | Installed-browser/real-browser action still NOT_RUN; topics/language/license/forks/issues/watchers are not yet rendered |
| Config / API / status convergence | required | PASS | Surface/config/source-auth/status regressions passed, including PAT rejection, rotation, write-only secret, fixed env and status aggregation limited to current `source_modes`; a fingerprint-scoped discovery 401 marks GitHub profile/bootstrap unavailable in both status APIs, and rotating the token clears that stale marker | No installed-app manual settings cycle and no valid-PAT real cycle |
| Setup surface | required | PASS | Static parity suite asserts GitHub controls, token secrecy, init reasons and request payload | Real browser action NOT_RUN |
| Desktop surface | required | PASS | Static parity suite asserts config/status/date/share/text-card/URL behavior; docs do not claim unimplemented `source_metadata` rendering | Real browser action NOT_RUN |
| Mobile surface | required | PASS | Static parity suite asserts alias/host/text-card/HTTPS URL rendering; docs do not claim unimplemented `source_metadata` rendering | Real browser action NOT_RUN |
| Extension popup surface | required | PASS | Static parity suite asserts config/status/init/text-card behavior and manifest non-expansion; visible metadata claim is limited to implemented fields | Chrome/Firefox installed-build action NOT_RUN |
| Mobile credential management | N/A | N/A | Mobile credential editing is a repository-wide intentional exclusion; mobile status/recommendation remains required | Existing global exclusion proof to record |
| Image delivery | N/A | N/A | Exact exclusion/static tests passed; repository is an intentional text card and owner avatar is not a cover | None |
| Image proxy DNS / redirect / SSRF boundary | N/A | N/A | `media.image=none`; no GitHub media host enters the proxy | None |
| Mobile deep link | N/A | N/A | Exact exclusion/static test passed; authoritative HTTPS `html_url` only | None |
| Native save | N/A | N/A | Contract exclusion plus saved-sync regression passed: repository save is terminal local-only and creates no native task | None |
| Focused + full backend verification | required | PASS | Independent GitHub focused + inspiration selection passed `113`; the documentation-audit focused subset passed `111`; related existing regressions passed `44` with `3 skipped`, source-auth GitHub selection passed `11` with `3 skipped`, API GitHub / `source_metadata` selection passed `9`; Ruff and MyPy passed. Full backend suite was run in four parallel groups on the final tree: `1357 passed/3 skipped`, `1762 passed/13 skipped`, `3022 passed/45 skipped`, `2629 passed/41 skipped` → total `8770 passed`, `102 skipped`, `0 failed` | Full suite completed clean; remaining real-E2E/installed-browser gaps are tracked separately |
| Chrome + Firefox tests/build/assets | required | PASS | Extension tests passed `1448`; typecheck passed; Chrome and Firefox builds plus asset verification passed | Installed-browser action E2E and package path/SHA-256 remain NOT_RUN |
| Safe real E2E | required | PASS | 2026-08-31 operator smoke: search/ranked/latest returned public repositories; public starred and affirmative empty were observed; all recorded smoke deltas were 0 local writes, 0 LLM calls and 0 upstream writes. A later fetch truthfully returned anonymous rate limit | No valid-PAT branch; transcript/package artifact not attached |
| State-changing E2E | N/A | N/A | Contract has no mutating actions; user requested no upstream mutation | None performed |
| Documentation / release-readiness | required | NOT_RUN | README, config, architecture/spec/index, affected module/privacy/installer/store docs, changelog and this ledger are updated; cross-precedent `comm`/`rg` omission scan, contract audit, metrics check and `git diff --check` passed | Store screenshot asset still depicts eleven sources; cannot mark release-ready before it and full/installed-browser/E2E gaps close |
| Commit/version/tag/push/publish mutations | N/A | N/A | User requested implementation only, not repository/release mutation | None performed |

## Transport、身份与数据契约

- Primary / fallback owner: backend official GitHub REST API / none
- Auth comparison: anonymous search and public starred collection work without credentials; `/user` returns 401 without a token and a deliberately invalid token returns `Bad credentials`; valid-token evidence is not available in the current environment
- Account resolution and mismatch behavior: token `/user` durable id is authoritative; an explicit public username is accepted scope but not proof of ownership; conflicting durable ids must stop bootstrap
- Stable identity / URL / dedupe: `github:repository:<numeric-id>` plus preserved `node_id`; use upstream `html_url`
- Upstream envelope / pagination / empty / partial / rate-limit: redacted fixtures exist under `tests/fixtures/github/`; live response shapes and RFC Link headers observed on 2026-08-31
- Engagement availability and publication time: repository stars map only to favorite; forks/issues/watchers are not remapped; `created_at` is authoritative publication time

## 命令与自动验证

| Command | Exit | Summary / artifact |
| --- | ---: | --- |
| `PYTHONPATH="$PWD/src" /Users/white/workspace/OpenBiliClaw/.venv/bin/python scripts/audit_platform_source.py --contract docs/platform-source-contract.github.toml --check --json` | 0 | `PASS=33`, `MISSING=0`, `N/A=14`, `MANUAL=12`; registration check passed, semantic/E2E disclaimer retained |
| `PYTHONPATH="$PWD/src" ... pytest -q tests/test_github_client.py tests/test_github_source.py tests/test_github_producer.py tests/test_github_cli.py tests/test_github_contract.py tests/test_github_surface_parity.py` | 0 | documentation-audit final rerun: `111 passed in 9.00s`; includes exact exclusions, shared public query sanitizer, mixed-init isolation, completed-page `partial_timeout`, rejected rows, caps/cursors, source-mode status and UI/static parity. Pytest also emitted non-failing cleanup warnings for an unrelated read-only mounted-DMG fixture |
| Independent GitHub focused + inspiration selection | 0 | `113 passed`; final read-only review reported no blocking defect |
| Independent rejected/valid-PAT status selection | 0 | `4 passed`; covers PAT rejection recovery, current-fingerprint marker and both status APIs |
| Independent inspiration rejected-only / incomplete / cap selection | 0 | `4 passed`; preserves accepted previews as partial/degraded and refuses false affirmative-empty / complete evidence |
| Independent desktop saved identity + GitHub UI selection | 0 | `35 passed`; `gh` and `github.com` normalize to repository identity, with current visible-field boundary retained |
| GitHub-related existing regression selection | 0 | `44 passed, 3 skipped` |
| GitHub source-auth selection | 0 | `11 passed, 3 skipped`; includes fixed-env handling, PAT rejection/fingerprint status, token rotation and bootstrap/profile unavailable projection |
| GitHub API / `source_metadata` selection | 0 | `9 passed` |
| extension test suite | 0 | `1448 passed` |
| extension typecheck | 0 | Passed |
| Chrome build + asset verification | 0 | Passed; no installed-browser action or package hash was recorded |
| Firefox build + asset verification | 0 | Passed; no installed-browser action or package hash was recorded |
| Ruff + MyPy | 0 | Both passed |
| `PYTHONPATH="$PWD/src" ... python scripts/source_contract_metrics.py --check --json` | 0 | `all_pass=true`, `6/6` |
| cross-precedent `comm` + GitHub roster/latest/env `rg` scan | 0 | Remaining V2EX/Bangumi-only files are source-specific contracts/modules, API-auth task-route docs or Safari build docs; no false GitHub created-time sort claim remains; dedicated env boundary references are intentional |
| `git diff --check -- README.md README_EN.md config.example.toml docs` | 0 | No whitespace errors in documentation/config scope |
| first focused invocation without `PYTHONPATH="$PWD/src"` | 2 | Collection imported `/Users/white/workspace/OpenBiliClaw/src`; rerun above with the documented worktree import boundary passed. This failed attempt is retained rather than hidden |
| full backend tests (split into four groups on the final tree) | 0 | `8770 passed`, `102 skipped`, `0 failed`; groups: `1357/3`, `1762/13`, `3022/45`, `2629/41` |
| real SenseNova GitHub E2E (`discover --source github --force --limit 10` + expression copy + `recommend`) | 0 | GitHub official REST search/ranked/latest → 10 discovered/enqueued → real LLM eval → 4 GitHub repos admitted → expression copy generated → GitHub recommendations returned |
| package/install provenance |  | Not requested; installed-build E2E still pending |

## 真实 E2E

| Scenario | Applicability | Status | Counts / DB sample / idempotency | Diagnostic / artifact |
| --- | --- | --- | --- | --- |
| Anonymous / stripped credential | required | PASS | search/ranked/latest returned public repositories; public starred and affirmative empty were also observed; recorded smoke deltas: local writes `0`, LLM calls `0`, upstream writes `0` | Later anonymous fetch returned typed `rate_limited`, demonstrating the error was not collapsed to empty |
| Authenticated / verified identity | required | BLOCKED | No GitHub PAT is configured in the current environment | Never print or persist a token in acceptance artifacts |
| Rejected/expired credential | required | NOT_RUN | Raw GitHub 401 bad-credential probe observed; mocked application verify/status path passed and persists token-fingerprint-scoped rejection | A live rejected credential has not traversed the complete application/status flow |
| Empty vs no-observer vs partial vs rate-limit | required | NOT_RUN | Live affirmative empty and typed anonymous rate limit observed; fixtures/unit tests cover partial, 304 unchanged, rejected rows, item/page caps, incomplete search and completed-page `partial_timeout` preservation | Full application matrix was not executed in one isolated run |
| Duplicate/retry/crash recovery | required | NOT_RUN | Unit regressions cover dedupe, partial preservation, stale cursor reset, search cap cursor rollback and no cursor advance for rejected/private rows | Process-kill durability E2E not run |
| `account → event → profile/init` | required | NOT_RUN | Public-starred fetch and canonical event conversion are covered independently | Isolated real LLM init/profile terminal not run |
| `discover → pending_eval → real LLM → recommendation` | required | PASS | 2026-09-03 isolated temp-root run: `discover --source github --force --limit 10` discovered 10 GitHub repository rows, enqueued 10, evaluated 20 through SenseNova (`deepseek-v4-flash`; 14,024→2,958 tokens), admitted 4 GitHub repos, then generated expression copy via SenseNova (`sensenova-6.8-flash-lite`) and `recommend` returned GitHub repo cards | Live GitHub starred bootstrap was rate-limited in this run, so `account→event→profile` is still separate NOT_RUN |
| Setup surface and actions | required | NOT_RUN | Pending | Pending |
| Desktop surface and actions | required | NOT_RUN | Pending | Pending |
| Mobile surface and actions | required | NOT_RUN | Pending | Pending |
| Extension popup surface and actions | required | NOT_RUN | Pending | Pending |

- LLM provider / model / route: run against SenseNova (商汤日日新): evaluation `deepseek-v4-flash`, expression copy `sensenova-6.8-flash-lite`; provider route was the configured `openai_compatible` pointing to `https://token.sensenova.cn/v1/`
- Task-mode passive event delta: N/A (no browser task)
- Smoke projection deltas: reported `0` across all frozen local/LLM/upstream write sinks for search/ranked/latest
- State-changing upstream actions actually run: `none`

## 最终结论

- Verdict: `partial`（实现与匿名只读 smoke 已有证据，但 full integration 仍不得宣称 complete）
- Required rows not PASS: GitHub starred → real LLM init/profile、installed-browser UI、完整 rejected/partial/crash E2E、documentation/release-readiness
- Intentional exclusions: post-init incremental, browser task features, image delivery, native deep link, native save, and unavailable engagement metrics as frozen in the contract
- Deferred work / blockers: authenticated/verified identity remains `BLOCKED` because no valid PAT is available; remaining required executions are `NOT_RUN`, not inferred from unit/static evidence
- Release mutations performed: none
