# Zhihu Multi-Source Discovery Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add Zhihu hot, feed, creator, and related discovery sources alongside existing search.

**Architecture:** Extend the existing plugin-backed `zhihu_tasks` queue and `ZhihuDiscoveryProducer`. The extension executes all source fetches inside zhihu.com with browser login state and returns normalized items for the shared candidate pool.

**Tech Stack:** Python 3.14, FastAPI, SQLite, Typer, TypeScript MV3 extension, node:test, pytest, Ruff, MyPy.

---

### Task 1: Backend Conversion And Producer Tests

**Files:**
- Modify: `tests/test_zhihu_tasks.py`
- Modify: `tests/test_zhihu_producer.py`
- Modify: `src/openbiliclaw/sources/zhihu_tasks.py`
- Modify: `src/openbiliclaw/runtime/zhihu_producer.py`

**Steps:**
1. Add failing tests that map `zhihu_hot`, `zhihu_feed`, `zhihu_creator`, and `zhihu_related` items to `DiscoveredContent`.
2. Add failing producer tests proving search uses keyword claims while hot/feed/creator/related enqueue independent task types and source counts.
3. Implement minimal conversion and producer task scheduling.
4. Run `pytest tests/test_zhihu_tasks.py tests/test_zhihu_producer.py -q --tb=short`.

### Task 2: Extension Task Execution

**Files:**
- Modify: `extension/tests/zhihu-task-executor.test.ts`
- Modify: `extension/tests/zhihu-task-mode.test.ts`
- Modify: `extension/src/content/zhihu/task-executor.ts`
- Modify: `extension/src/background/zhihu-task-dispatcher.ts`

**Steps:**
1. Add failing tests for new task types and normalized source strategies.
2. Implement `hot`, `feed`, `creator`, and `related` fetch helpers with conservative caps.
3. Run `cd extension && node --test --experimental-strip-types tests/zhihu-task-executor.test.ts tests/zhihu-task-mode.test.ts`.

### Task 3: CLI, Config, And UI

**Files:**
- Modify: `src/openbiliclaw/config.py`
- Modify: `src/openbiliclaw/api/models.py`
- Modify: `src/openbiliclaw/api/app.py`
- Modify: `src/openbiliclaw/cli.py`
- Modify: `extension/popup/popup.html`
- Modify: `extension/popup/popup.js`
- Modify: `src/openbiliclaw/web/desktop/index.html`
- Modify: `src/openbiliclaw/web/desktop/assets/js/app.js`

**Steps:**
1. Add config fields for source modes and per-strategy budgets.
2. Add CLI options to trigger each Zhihu source directly for real E2E smoke testing.
3. Expose settings in popup and desktop config pages.
4. Run focused CLI/API/config tests.

### Task 4: Docs And Final E2E

**Files:**
- Modify: `config.example.toml`
- Modify: `docs/modules/config.md`
- Modify: `docs/modules/cli.md`
- Modify: `docs/modules/discovery.md`
- Modify: `docs/modules/extension.md`
- Modify: `docs/architecture.md`
- Modify: `docs/spec.md`
- Modify: `README.md`
- Modify: `README_EN.md`
- Modify: `docs/changelog.md`

**Steps:**
1. Document new source modes, config fields, and CLI smoke commands.
2. Run lint/type/test/build verification.
3. Sync built extension to the installed unpacked extension directory.
4. Hot-reload extension through the backend endpoint.
5. Run real CLI E2E commands for `search`, `hot`, `feed`, `creator`, and `related`.
