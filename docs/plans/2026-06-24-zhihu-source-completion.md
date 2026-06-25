# Zhihu Source Completion Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Finish the remaining Zhihu source integration gaps so Zhihu can be selected in guided init, report meaningful settings status, and run all configured discovery branches without requiring a previous Zhihu run for creator/related seeds.

**Architecture:** Reuse the existing plugin-backed `zhihu_tasks` queue. Guided init passes an `include_zhihu` flag through CLI/API/UI, collects bootstrap task events, and feeds them into the existing event analysis/profile pipeline. Source status remains local-only and derives Zhihu readiness from recent task terminal state instead of making outbound platform requests.

**Tech Stack:** Python CLI/API/runtime tests with pytest; browser-extension popup tests with npm/vitest; static JS options for desktop Web, setup Web, and extension popup.

---

### Task 1: Guided Init Zhihu Selection

**Files:**
- Modify: `src/openbiliclaw/cli.py`
- Modify: `src/openbiliclaw/api/app.py`
- Modify: `src/openbiliclaw/web/desktop/assets/js/app.js`
- Modify: `src/openbiliclaw/web/setup/index.html`
- Modify: `extension/popup/popup-init-control.js`
- Test: `tests/test_cli.py`
- Test: `tests/test_api_app.py`
- Test: `extension/tests/init-control.test.ts`

**Steps:**
1. Add failing tests proving `include_zhihu` is accepted, persisted, and passed through API guided init.
2. Add Zhihu to guided-init source option lists in desktop Web, setup page, and extension popup.
3. Enqueue/collect Zhihu bootstrap events during init and include them in analysis/profile input.
4. Run focused pytest and extension init-control tests.

### Task 2: Source Status

**Files:**
- Modify: `src/openbiliclaw/api/app.py`
- Test: `tests/test_api_app.py`

**Steps:**
1. Add failing tests for Zhihu status when the latest task failed with `zhihu_login_required` and when a task completed recently.
2. Implement local-only task-status derivation from `zhihu_tasks`.
3. Keep no-task behavior explicit: enabled but not yet verified.

### Task 3: Discovery Cold-Start Seeds

**Files:**
- Modify: `src/openbiliclaw/runtime/zhihu_producer.py`
- Test: `tests/test_zhihu_producer.py`

**Steps:**
1. Add failing test where `creator` and `related` seed loaders are empty but earlier same-run `hot/feed/search` results contain `author_url` and `url`.
2. Derive same-run seed values from already collected items before skipping creator/related.
3. Preserve historical seed-loader behavior when it returns values.

### Task 4: Docs And Verification

**Files:**
- Modify: `docs/changelog.md`
- Modify as needed: `docs/modules/cli.md`, `docs/modules/discovery.md`, `docs/modules/config.md`, `docs/modules/extension.md`

**Steps:**
1. Update docs for guided init selection/status behavior.
2. Run focused pytest, ruff, extension tests/build, and diff checks.
