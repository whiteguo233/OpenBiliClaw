# Config Settings Alignment Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `config.toml`, `/api/config`, and the extension settings UI stay aligned without dropping advanced configuration on save.

**Architecture:** Treat `openbiliclaw.config.Config` as the source of truth. First fix persistence and API serialization/update coverage, then extend popup fields to consume that API. Keep the settings UI grouped into common and advanced sections rather than introducing a new settings framework.

**Tech Stack:** Python dataclasses/Pydantic/FastAPI, pytest, Chrome extension popup HTML/vanilla JS, Node built-in test runner.

---

### Task 1: Preserve All Persisted Config Fields

**Files:**
- Modify: `tests/test_config.py`
- Modify: `src/openbiliclaw/config.py`

**Step 1: Write the failing test**

Add a test that sets non-default values for:

- `scheduler.speculation_*`
- `scheduler.auto_update_enabled`
- `scheduler.auto_update_check_interval_hours`
- `logging.aggregate_budget_mb`
- `logging.unmanaged_truncate_mb`
- `logging.unmanaged_max_age_days`

Save with `save_config()`, reload with `load_config()`, and assert every value survived.

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::test_save_config_round_trips_advanced_scheduler_and_logging_fields -q`

Expected: FAIL showing the missing fields reverted to defaults.

**Step 3: Write minimal implementation**

Update `_render_config_toml()` in `src/openbiliclaw/config.py` so it writes:

- all scheduler speculation fields
- both scheduler auto-update fields
- all logging unmanaged cleanup fields

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py::test_save_config_round_trips_advanced_scheduler_and_logging_fields -q`

Expected: PASS.

### Task 2: Expand Config API Shape And Update Handling

**Files:**
- Modify: `tests/test_api_app.py`
- Modify: `src/openbiliclaw/api/models.py`
- Modify: `src/openbiliclaw/api/app.py`

**Step 1: Write failing tests**

Add tests that verify:

- `GET /api/config` includes `sources.browser`, `sources.xiaohongshu`, `sources.douyin`, scheduler source shares/speculation/auto-update interval, logging rotation/unmanaged fields, and `llm.deepseek.reasoning_effort`.
- `PUT /api/config` can update the same fields plus Bilibili browser fields, storage path, OpenRouter headers, and per-module LLM overrides.

**Step 2: Run tests to verify they fail**

Run the new targeted tests with `uv run pytest tests/test_api_app.py::<test_name> -q`.

Expected: FAIL because the response omits `sources`/advanced fields and update handler ignores them.

**Step 3: Write minimal implementation**

Add Pydantic output models for `SourcesConfigOut`, `XiaohongshuSourceConfigOut`, `DouyinSourceConfigOut`, and `SourcesBrowserConfigOut`. Add advanced fields to existing LLM provider, scheduler, and logging output models. Update `_config_to_response()` and `update_config()` to serialize and apply those fields.

**Step 4: Run targeted API tests**

Run: `uv run pytest tests/test_api_app.py::<new_test_1> tests/test_api_app.py::<new_test_2> -q`

Expected: PASS.

### Task 3: Extend Popup Settings Form

**Files:**
- Modify: `extension/tests/popup-api.test.ts`
- Add or modify: `extension/tests/popup-settings.test.ts`
- Modify: `extension/popup/popup.html`
- Modify: `extension/popup/popup.js`

**Step 1: Write failing tests**

Add extension tests that verify:

- The expanded config payload sent by `updateConfig()` preserves nested advanced fields.
- `popup.html` contains field IDs for module LLM overrides, sources, source shares, Bilibili browser, storage, scheduler advanced, and logging advanced fields.

**Step 2: Run tests to verify failure**

Run: `npm test -- popup-settings.test.ts` if supported, otherwise `npm test`.

Expected: FAIL because the HTML lacks the new field IDs.

**Step 3: Implement popup fields**

Add fields grouped under the existing settings overlay:

- LLM advanced: DeepSeek reasoning, OpenRouter headers, per-module provider/model overrides
- Bilibili browser: executable, headed
- Sources: browser CDP/headed, Xiaohongshu budgets, Douyin enable/budgets/cookie env
- General/storage: `data_dir`, `storage.db_path`
- Scheduler advanced: account sync, auto-update interval, pool source shares, speculation settings
- Logging advanced: file level, directory, filename, rotation, unmanaged cleanup

Update `populateForm()` and `collectForm()` accordingly.

**Step 4: Run extension tests**

Run: `npm test`

Expected: PASS.

### Task 4: Update Docs And Verify

**Files:**
- Modify: `docs/modules/config.md`
- Modify: `docs/modules/extension.md`
- Modify: `docs/changelog.md`

**Step 1: Update docs**

Document the expanded plugin settings coverage and correct config defaults that drifted.

**Step 2: Run verification**

Run:

```bash
uv run pytest tests/test_config.py tests/test_api_app.py -q
uv run ruff check src/openbiliclaw/config.py src/openbiliclaw/api/models.py src/openbiliclaw/api/app.py tests/test_config.py tests/test_api_app.py
npm test
npm run typecheck
git diff --check
```

Expected: targeted Python tests pass, extension tests/typecheck pass, diff whitespace check passes. Full repository pytest/ruff may still have unrelated historical failures; report them separately if rerun.
