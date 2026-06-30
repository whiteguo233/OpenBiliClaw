---
name: add-platform-source
description: Use when adding, integrating, completing, testing, or releasing a new OpenBiliClaw platform/source adapter, including browser-extension logged-in sources, discover branches, guided init, config pages, recommendation cards, CLI smoke commands, and real end-to-end validation.
---

# Add Platform Source

Read `docs/platform-source-integration.md` from the repository root completely, then follow it as the authoritative checklist.

Key constraints:

- Treat a source as an end-to-end product path: backend, extension/server collection, CLI smoke, guided init, formal discover, config pages, recommendation cards, tests, docs, and release.
- For login-dependent platforms, validate with the installed extension browser that has the real login state. Do not substitute CDP/MCP/browser automation unless explicitly requested.
- Keep smoke tasks non-mutating unless the user asks for memory/profile writes through explicit flags.
- Separate safe E2E from account-mutating E2E: snapshot/scroll/click/share can be run by default; like/favorite/follow/save/upvote need explicit user permission or a test account.
- For search-capable discover sources, wire query generation and fetching together: add the platform to the unified `KeywordPlanner` generation path and merged prompt supply table/schema, then consume via `KeywordFetchCoordinator.claim(<slug>)` with `source_keyword_id` propagation and profile-keyword fallback. Claim/fetch without planner generation is incomplete; also audit already-added search sources (for example Zhihu) when docs or producers claim they use unified keywords.
- Verify user-facing surfaces, not only backend fetch: plugin settings, PC web settings, setup/init, source status, source share quota, and recommendation cards on PC/mobile/plugin must all agree with the real implementation.
- Eval/profile E2E must use the user's configured local LLM/embedding providers; do not silently substitute mocks, Ollama, or another provider.
- Do not claim completion until unit tests, extension tests/builds, and real E2E checks relevant to the source have been run.
- For releases, verify tag uniqueness, version alignment, CI/package workflows, aggregate release assets, plugin marketplace submission, and local untracked/ignored artifacts before reporting done.
