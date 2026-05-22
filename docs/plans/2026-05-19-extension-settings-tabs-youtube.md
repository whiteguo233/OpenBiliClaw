# Extension Settings Tabs And YouTube Config Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Split the extension settings overlay into focused tabs and expose YouTube source configuration at the same level as the other platform sources.

**Architecture:** Add YouTube source fields to the Python config dataclass, TOML persistence, API models, config update path, and YouTube strategy construction. Reorganize the existing popup settings DOM into tab panels without unmounting fields, then wire the new YouTube fields through `populateForm()` and `collectForm()`.

**Tech Stack:** Python dataclasses / FastAPI / pytest for backend config/API/runtime wiring; existing extension HTML/CSS/vanilla JS and Node test runner for popup UI.

---

### Task 1: Backend YouTube Config Surface

**Files:**
- Modify: `src/openbiliclaw/config.py`
- Modify: `src/openbiliclaw/api/models.py`
- Modify: `src/openbiliclaw/api/app.py`
- Test: `tests/test_config.py`
- Test: `tests/test_api_app.py`

**Step 1: Write failing tests**

Add assertions that:
- `Config().sources.youtube` defaults to `enabled=False`, `daily_search_budget=6`, `daily_trending_budget=50`, `daily_channel_budget=10`, `request_interval_seconds=2`.
- `load_config()` reads those fields from `[sources.youtube]`.
- `save_config()` writes those fields back.
- `GET /api/config` exposes those fields.
- `PUT /api/config` updates those fields.

**Step 2: Run tests to verify failure**

```bash
pytest tests/test_config.py tests/test_api_app.py -k "youtube and config" -q
```

Expected: fail because `YoutubeSourceConfig` and `YoutubeSourceConfigOut` do not have the new fields.

**Step 3: Implement minimal backend config/API**

- Add the four fields to `YoutubeSourceConfig`.
- Read them in `_build_config()`.
- Write them in `_render_config_toml()`.
- Add them to `YoutubeSourceConfigOut`.
- Populate them in `GET /api/config`.
- Update them in the `PUT /api/config` sources update block.

**Step 4: Run tests to verify pass**

Run the same pytest command and confirm pass.

### Task 2: Wire YouTube Config Into Strategy Construction

**Files:**
- Modify: `src/openbiliclaw/api/runtime_context.py`
- Test: `tests/test_refresh_runtime.py` or existing runtime-context API tests

**Step 1: Write failing test**

Add a test that monkeypatches the YouTube strategy classes / client in `RuntimeContext.rebuild_from_config()` and asserts:
- `YoutubeSearchStrategy.queries_per_run` receives `cfg.sources.youtube.daily_search_budget`.
- `YoutubeTrendingStrategy.fetch_limit` receives `cfg.sources.youtube.daily_trending_budget`.
- `YoutubeChannelStrategy.max_channels` receives `cfg.sources.youtube.daily_channel_budget`.

**Step 2: Run test to verify failure**

```bash
pytest tests/test_refresh_runtime.py tests/test_api_app.py -k "youtube_strategy_config" -q
```

Expected: fail because runtime construction always uses strategy defaults.

**Step 3: Implement strategy wiring**

Read `yt_cfg = new_config.sources.youtube` in `RuntimeContext.rebuild_from_config()` and pass:
- `queries_per_run=int(getattr(yt_cfg, "daily_search_budget", 6))`
- `fetch_limit=int(getattr(yt_cfg, "daily_trending_budget", 50))`
- `max_channels=int(getattr(yt_cfg, "daily_channel_budget", 10))`

Do not add request sleeping in this task; `request_interval_seconds` is persisted and surfaced for parity / future throttling.

**Step 4: Run test to verify pass**

Run the same pytest command and confirm pass.

### Task 3: Popup Settings Tabs And YouTube Fields

**Files:**
- Modify: `extension/popup/popup.html`
- Modify: `extension/popup/popup.js`
- Test: `extension/tests/popup-settings.test.ts`
- Test: `extension/tests/popup-layout.test.ts`

**Step 1: Write failing tests**

Add static tests that assert:
- The settings overlay includes `role="tablist"` and five tab buttons: `模型`, `平台源`, `调度`, `通用`, `日志`.
- Settings sections are grouped with `data-settings-panel`.
- Inactive settings panels are hidden by CSS.
- YouTube field IDs exist in HTML and are referenced in JS:
  `cfgYoutubeDailySearchBudget`, `cfgYoutubeDailyTrendingBudget`, `cfgYoutubeDailyChannelBudget`, `cfgYoutubeRequestInterval`.
- `populateForm()` reads those fields from `cfg.sources.youtube`.
- `collectForm()` sends those fields under `sources.youtube`.

**Step 2: Run tests to verify failure**

```bash
cd extension && npm test -- tests/popup-settings.test.ts tests/popup-layout.test.ts
```

Expected: fail because the tab markup and YouTube fields are missing.

**Step 3: Implement popup HTML/CSS/JS**

- Add `.settings-tabs`, `.settings-tab`, `.settings-panel`, and hidden inactive CSS.
- Wrap existing sections in five `.settings-panel` containers.
- Move pool share fields into the platform-source tab so source enable switches and source shares live together.
- Add YouTube fields beside `cfgYoutubeEnabled`.
- Add tab binding in `bindSettings()` that toggles `aria-selected`, `.is-active`, and `hidden`.
- Wire YouTube fields in `populateForm()` and `collectForm()`.

**Step 4: Run tests to verify pass**

Run the same npm test command and confirm pass.

### Task 4: Documentation And Examples

**Files:**
- Modify: `config.example.toml`
- Modify: `docs/modules/config.md`
- Modify: `docs/modules/extension.md`
- Modify: `docs/changelog.md`

**Step 1: Write / update docs checks if needed**

Use existing tests if they cover config examples; otherwise rely on targeted grep and markdown review.

**Step 2: Update docs**

- Add YouTube fields to `config.example.toml`.
- Update `[sources.youtube]` in `docs/modules/config.md`.
- Update settings page capability table in `docs/modules/extension.md`.
- Add one current-version changelog bullet.

**Step 3: Verify docs-sensitive tests**

```bash
pytest tests/test_config.py tests/test_install_contract_docs.py -q
```

Expected: pass.

### Task 5: Final Verification

**Files:** all touched files.

**Step 1: Run extension checks**

```bash
cd extension && npm test
cd extension && npm run typecheck
```

**Step 2: Run targeted backend checks**

```bash
pytest tests/test_config.py tests/test_api_app.py tests/test_refresh_runtime.py -q
```

**Step 3: Run lint/type checks if time allows**

```bash
ruff check src/ tests/
mypy src/
```

**Step 4: Review diff**

```bash
git diff -- src/openbiliclaw/config.py src/openbiliclaw/api/models.py src/openbiliclaw/api/app.py src/openbiliclaw/api/runtime_context.py extension/popup/popup.html extension/popup/popup.js config.example.toml docs/modules/config.md docs/modules/extension.md docs/changelog.md
```
