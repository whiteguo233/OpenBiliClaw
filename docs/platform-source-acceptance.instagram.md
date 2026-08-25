# Instagram 平台来源验收台账

> Integration level：`full`（匿名公开 discover + 登录态、init-only 个人事件）。静态实现不等于上游授权，也不等于真实账号 E2E；任何未实际执行的门禁保持 `NOT_RUN`。

## Provenance

- Contract：[`docs/platform-source-contract.instagram.toml`](platform-source-contract.instagram.toml)
- Worktree：`feat/instagram-source`，基线 `86843d4313a37f2d688e81cb380a226e28a8e981`
- GitHub protocol research：instagrapi `2902a3bc`、Instaloader `54346929`、gallery-dl `86047cf6`、yt-dlp `5d6b8c8c`
- Real anonymous spike：2026-08-12，logged-out `/popular/technology/` 页面及下一页 GraphQL，只读；观察到 8 个 media nodes 与 `page_info`
- Real account boundary：尚未读取用户 Instagram 登录态，未执行点赞/收藏/关注/搜索等上游写操作

## Gate ledger

| Gate | Applicability | Status | Evidence / remaining work |
| --- | --- | --- | --- |
| Frozen contract | required | PASS | Contract 已冻结 capability-specific auth、topic/creator discover、三 scope init、strict-empty 与 read-only boundary |
| GitHub implementation research | required | PASS | 固定 commit、license、endpoint/envelope/cursor 与近期 failure issue 已记录在模块文档；GPL/DMCA 代码未复制 |
| Canonical registration / audit | required | PASS | `audit_platform_source.py --check --json`：33 PASS、10 N/A、14 MANUAL、`required_missing=0` |
| Normalization / stable identity | required | PASS | Instagram source/contract tests 覆盖 media/user numeric ID、canonical URL、类型、taken_at 与 unknown engagement |
| Capability-specific auth/status | required | PASS | Auth 组合回归证明 discover anonymous、private heartbeat gating、首次 verify action；extension cookie tests 证明只上传 boolean |
| Durable task queue / first-final | required | PASS | Queue/protocol tests 覆盖 claim token、stage-first、duplicate/replay、machine-code-only diagnostics、failed+items 拒绝与 first-final |
| Guided init / event ingress | required | PASS | Bootstrap/auth regressions 覆盖 liked→like、saved→favorite、following→follow、账号 key 分区、smoke projection gate 与 partial retention |
| Formal discover / candidate pipeline | required | PASS | Producer tests 覆盖 topic/creator 正规化、预算、dedupe、owner lease、late-result adoption、绝对 cycle deadline、pool gate 与 handoff retry |
| Full repository regression | required | PASS | 冻结 worktree 使用项目完整虚拟环境执行全仓 pytest：8352 passed、60 skipped、0 failed |
| Extension isolation / MV3 recovery | required | PASS | 全量 extension suite 1423/1423；专项覆盖 task-mode、sender/tab、mutex-before-claim、deadline、partial progress、outbox 与 cold-start recovery |
| Chrome + Firefox build assets | required | PASS | Chrome 与 Firefox build 均完成，两个 asset verifier 均确认 21 个 manifest scripts/WAR assets |
| Anonymous upstream smoke | required | PASS | Logged-out topic 页面和一次下一页 GraphQL 已观察；这只证明机会性 envelope，不证明长期稳定/完整性 |
| Real installed Chrome discovery | required | NOT_RUN | 必须加载本 worktree 的新构建，记录 installed-build provenance、success/empty/HTML/challenge/429/partial |
| Real logged-in bootstrap | required | NOT_RUN | 需要用户授权的已登录测试号；验证 current-account、三 scope、第二轮幂等、零 Cookie/raw-body egress |
| MV3 kill/recover + backend restart | required | NOT_RUN | 在真实 in-progress task 中分别重启 worker/扩展/后端，必须重放同一 task/result |
| Cross-source mutex | required | NOT_RUN | 与任一长 browser task 并发，必须先 mutex 后 claim，不能留下 stranded in_progress row |
| Real LLM/profile convergence | required | NOT_RUN | 需要用户允许把真实个人事件导入隔离库并使用真实 LLM/embedding |
| Upstream mutation audit | required | NOT_RUN | 对测试账号做前后对照；不得执行 Instagram Search，like/save/follow/message 请求必须为零 |
| Meta permission / product approval | required for production | BLOCKED | Instagram/Meta 条款要求自动采集授权；本分支的技术实现与默认关闭不能替代书面许可或产品风险决定 |
| Documentation / release mutation | required | PASS | README、模块/架构/配置/CLI/隐私/商店/验收文档已同步；本任务不 bump version、不 commit/push/publish/package release |

## Verification commands

```bash
PYTHONPATH=src /Users/white/workspace/OpenBiliClaw/.venv/bin/pytest -q \
  tests/test_instagram_source.py tests/test_instagram_tasks.py \
  tests/test_instagram_contract.py \
  tests/test_instagram_wiring.py tests/test_source_bootstrap.py \
  tests/test_source_auth_contract.py tests/test_web_guided_init.py

PYTHONPATH=src /Users/white/workspace/OpenBiliClaw/.venv/bin/python \
  scripts/audit_platform_source.py \
  --contract docs/platform-source-contract.instagram.toml --check --json

cd extension
npm test
npm run typecheck
npm run build && npm run verify:assets
npm run build:firefox && npm run verify:assets:firefox
```

2026-08-12 最终静态/模拟验证：Instagram focused 87 passed；全仓 pytest 8352 passed、60 skipped；extension 1423 passed；全仓 Ruff/MyPy 与两套 build/asset PASS。真实账号、真实已安装扩展和 Meta permission 三类门禁仍保留独立 provenance，不能由这些结果替代。
