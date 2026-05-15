# Source Policy Controls Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a unified source policy so platform discovery switches and pool ratios are honored consistently by init, API runtime, OpenClaw, scheduler replenishment, and popup settings.

**Architecture:** Introduce a shared helper module for enabled-source and source-share decisions. Keep Bilibili as the always-enabled core source, treat Xiaohongshu/Douyin/YouTube as optional switches, preserve configured ratios for disabled sources, and pass only effective shares to runtime controllers.

**Tech Stack:** Python dataclasses/config/FastAPI/pytest, existing extension popup JavaScript and static extension tests.

---

### Task 1: Add Shared Source Policy Helpers

**Files:**
- Create: `src/openbiliclaw/runtime/source_policy.py`
- Test: `tests/test_source_policy.py`

**Step 1: Write failing tests**

Add tests for:
- Bilibili is always enabled.
- Disabled optional sources are removed from effective shares but preserved in configured input.
- Enabled YouTube is kept when configured.
- Empty/invalid shares fall back to defaults.
- Event counts produce damped integer ratio suggestions for enabled sources.

**Step 2: Run tests to verify they fail**

Run:

```bash
uv run --extra dev python -m pytest tests/test_source_policy.py -q
```

Expected: fail because `openbiliclaw.runtime.source_policy` does not exist.

**Step 3: Implement minimal helper**

Create `source_policy.py` with:
- `SOURCE_ORDER = ("bilibili", "xiaohongshu", "douyin", "youtube")`
- `DEFAULT_POOL_SOURCE_SHARES = {"bilibili": 8, "xiaohongshu": 1, "douyin": 1, "youtube": 1}`
- `source_enabled_map(config) -> dict[str, bool]`
- `effective_pool_source_shares(config) -> dict[str, int]`
- `suggest_pool_source_shares(event_counts, enabled_sources=None, configured_shares=None) -> dict[str, int]`

Use sqrt or log dampening, minimum 1 for enabled optional sources with events, and default fallback when counts are empty.

**Step 4: Run tests to verify pass**

Run the same pytest command and confirm pass.

**Step 5: Commit**

```bash
git add src/openbiliclaw/runtime/source_policy.py tests/test_source_policy.py
git commit -m "feat: add source policy helper"
```

---

### Task 2: Wire Effective Shares Into Runtime and OpenClaw

**Files:**
- Modify: `src/openbiliclaw/api/runtime_context.py`
- Modify: `src/openbiliclaw/integrations/openclaw/bootstrap.py`
- Modify: `src/openbiliclaw/runtime/refresh.py`
- Test: `tests/test_refresh_runtime.py`
- Test: `tests/test_openclaw_adapter.py`

**Step 1: Write failing tests**

Add/adjust tests proving:
- `RuntimeContext` helper returns `{"bilibili": 8}` when all optional sources are disabled.
- OpenClaw bootstrap passes effective shares and no longer strands disabled XHS/Douyin shares.
- Enabled YouTube share creates a replenishment plan when YouTube strategies are registered.
- Enabled source without a producer logs a warning rather than silently skipping.

**Step 2: Run focused tests to verify failure**

```bash
uv run --extra dev python -m pytest tests/test_refresh_runtime.py tests/test_openclaw_adapter.py -k "source_target_counts or source_shares or youtube or openclaw" -q
```

Expected: at least OpenClaw/YouTube plan tests fail before code changes.

**Step 3: Implement wiring**

- Replace private `_pool_source_shares_from_config` logic with `effective_pool_source_shares`.
- Import and use the helper in OpenClaw bootstrap.
- Include YouTube strategy names in `_build_source_replenishment_plan()` when quota exists.
- Keep warning behavior for enabled sources with no available replenishment.

**Step 4: Run focused tests to verify pass**

Run the same pytest command and confirm pass.

**Step 5: Commit**

```bash
git add src/openbiliclaw/api/runtime_context.py src/openbiliclaw/integrations/openclaw/bootstrap.py src/openbiliclaw/runtime/refresh.py tests/test_refresh_runtime.py tests/test_openclaw_adapter.py
git commit -m "fix: apply source policy to refresh runtimes"
```

---

### Task 3: Expose Source Switches and Ratio Suggestions in API

**Files:**
- Modify: `src/openbiliclaw/api/models.py`
- Modify: `src/openbiliclaw/api/app.py`
- Test: `tests/test_api_app.py`

**Step 1: Write failing tests**

Add API tests that:
- `GET /api/config` includes `sources.xiaohongshu.enabled`, `sources.youtube.enabled`, and `scheduler.pool_source_shares.youtube`.
- `PUT /api/config` updates XHS/YT enabled flags and four source shares.
- `GET /api/config/source-share-suggestion` returns event counts and suggested shares based on stored events.

**Step 2: Run tests to verify failure**

```bash
uv run --extra dev python -m pytest tests/test_api_app.py -k "config" -q
```

Expected: new fields/endpoint assertions fail.

**Step 3: Implement API**

- Add `enabled` to XHS output/update model.
- Add YouTube source config output/update model.
- Include YouTube in sources response.
- Update config write path for XHS/YT enabled.
- Add a source-share suggestion response model and endpoint using database event counts plus `suggest_pool_source_shares`.

**Step 4: Run tests to verify pass**

Run the same pytest command and confirm pass.

**Step 5: Commit**

```bash
git add src/openbiliclaw/api/models.py src/openbiliclaw/api/app.py tests/test_api_app.py
git commit -m "feat: expose source policy config API"
```

---

### Task 4: Update Init Source Decisions and Ratio Prompt

**Files:**
- Modify: `src/openbiliclaw/cli.py`
- Test: `tests/test_cli.py`

**Step 1: Write failing tests**

Add/adjust CLI tests proving:
- `init --no-douyin --no-youtube` persists `sources.douyin.enabled=false` and `sources.youtube.enabled=false`.
- `init --yes-youtube` persists YouTube enabled.
- Interactive init can accept suggested source shares.
- Interactive init can enter manual source shares.

**Step 2: Run focused CLI tests to verify failure**

```bash
uv run --extra dev python -m pytest tests/test_cli.py -k "init and (douyin or youtube or source_share)" -q
```

Expected: new persistence/prompt tests fail.

**Step 3: Implement CLI flow**

- Persist Douyin and YouTube `enabled` flags alongside XHS.
- Collect source event counts from init-imported event batches.
- Show suggested shares after source event collection.
- Accept a yes/no confirmation; if no, parse manual ratios.
- Save updated `scheduler.pool_source_shares`.

**Step 4: Run tests to verify pass**

Run the same focused CLI command and confirm pass.

**Step 5: Commit**

```bash
git add src/openbiliclaw/cli.py tests/test_cli.py
git commit -m "feat: configure source shares during init"
```

---

### Task 5: Add Popup Controls

**Files:**
- Modify: `extension/popup/popup.html`
- Modify: `extension/popup/popup.js`
- Modify: `extension/popup/popup-api.js`
- Test: extension popup tests under `extension/` or repository tests that validate popup markup/helpers

**Step 1: Write failing tests**

Add tests/static checks that:
- Popup settings includes XHS enabled, YouTube enabled, YouTube share, and source-share suggestion button IDs.
- `collectForm()` sends XHS/YT enabled and YouTube share.
- Suggestion button calls the new API and fills share inputs.

**Step 2: Run extension tests to verify failure**

Use the repo's existing extension test command or focused pytest/static tests discovered in this task.

**Step 3: Implement popup changes**

- Add switches for XHS and YouTube.
- Add YouTube share input.
- Add source-share suggestion button.
- Add `fetchSourceShareSuggestion()` API wrapper.
- Fill ratio inputs from suggestion; user still clicks Save to persist.

**Step 4: Run extension tests to verify pass**

Run the same focused extension tests and confirm pass.

**Step 5: Commit**

```bash
git add extension/popup/popup.html extension/popup/popup.js extension/popup/popup-api.js <test-files>
git commit -m "feat: add source policy popup controls"
```

---

### Task 6: Update Docs and Final Verification

**Files:**
- Modify: `docs/modules/config.md`
- Modify: `docs/modules/discovery.md`
- Modify: `docs/modules/extension.md`
- Modify: `docs/architecture.md`
- Modify: `docs/spec.md`
- Modify: `README.md`
- Modify: `README_EN.md`
- Modify: `docs/changelog.md`

**Step 1: Update docs**

Document:
- Source enabled switches.
- Effective share behavior for disabled sources.
- Init ratio suggestion flow.
- Popup source policy controls.
- YouTube source share behavior.
- Updated data-flow diagrams where source policy affects runtime discovery.

**Step 2: Run formatting/lint/tests**

Run:

```bash
uv run --extra dev ruff check src/openbiliclaw/runtime/source_policy.py src/openbiliclaw/api/runtime_context.py src/openbiliclaw/integrations/openclaw/bootstrap.py src/openbiliclaw/runtime/refresh.py src/openbiliclaw/api/models.py src/openbiliclaw/api/app.py src/openbiliclaw/cli.py tests/test_source_policy.py tests/test_refresh_runtime.py tests/test_openclaw_adapter.py tests/test_api_app.py tests/test_cli.py
uv run --extra dev python -m pytest tests/test_source_policy.py tests/test_refresh_runtime.py tests/test_openclaw_adapter.py tests/test_api_app.py tests/test_cli.py -k "source_policy or source_shares or pool_source_shares or config or init" -q
```

Expected: targeted lint and tests pass. If broader repo lint/test still fails because of unrelated existing debt, record the exact failure scope.

**Step 3: Commit docs**

```bash
git add docs/modules/config.md docs/modules/discovery.md docs/modules/extension.md docs/architecture.md docs/spec.md README.md README_EN.md docs/changelog.md
git commit -m "docs: describe source policy controls"
```

**Step 4: Finish branch**

Use `superpowers:verification-before-completion`, then merge to `main` and push if verification supports it.
