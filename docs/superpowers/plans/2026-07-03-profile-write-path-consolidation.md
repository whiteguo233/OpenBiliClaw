# Profile Write Path Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** `docs/plans/2026-07-03-profile-write-path-consolidation-spec.md` — read it before starting; it holds the writer inventory, failure scenarios, and design rationale that this plan assumes.

**Goal:** Make profile mutation a mechanism instead of a convention: atomic persistence for every memory-layer/state file (spec Phase 1, MUST), a single serialized soul-layer write path `ProfileStore` migrating all nine writers (spec Phase 2, MUST), structured mutation history with snapshot restore (spec Phase 3, RECOMMENDED), and unification of the feedback/dialogue learning path into the pipeline (spec Phase 4, RECOMMENDED).

**Architecture:** Promote the existing `json_state.py` atomic writer to the shared persistence primitive. Add `soul/profile_store.py` as the sole owner of soul/preference-layer mutations with a compute-outside/apply-inside-the-lock contract (LLM calls never run under the lock; mutation closures are synchronous and re-apply deltas to a freshly loaded profile). Layer updaters split into async `compute_*` (LLM) and sync `apply_*` functions. History and snapshots hang off the store's single commit point. Onion-layer semantics, thresholds, prompt contracts, and override read-time overlay are unchanged.

**Tech Stack:** Python 3.11+, asyncio, SQLite, existing `MemoryManager` JSON layers, Typer CLI, pytest (asyncio_mode=auto), Ruff, MyPy strict.

**Out of scope:** spec Phases 5–6 (cursor migration to SQLite, `PeriodicJob` abstraction) — OPTIONAL tier, not planned here.

**Shippable checkpoints:** Task 4 (Phase 1 PR), Task 10 (Phase 2 PR), Task 12 (Phase 3 PR), Task 14 (Phase 4 PR). Each checkpoint ends with the full validation block and the CLAUDE.md pre-merge doc checklist.

---

## Phase 1 — Atomic persistence (MUST)

### Task 1: Public atomic write helpers

**Files:**
- Modify: `src/openbiliclaw/memory/json_state.py`
- Test: `tests/test_atomic_persistence.py` (new)

- [ ] **Step 1: Write failing tests**

New test file covering the public helpers that don't exist yet:

1. `test_write_json_atomic_survives_interrupted_replace` — write an initial payload, then patch `os.replace` to raise `OSError`; call `write_json_atomic` with a new payload; assert the target file still parses as the ORIGINAL payload and no `.tmp` file remains in the directory.
2. `test_write_json_atomic_utf8_roundtrip` — payload with Chinese + emoji round-trips with `ensure_ascii=False`.
3. `test_write_text_atomic_survives_interrupted_replace` — same interruption contract for a text (markdown) payload.

- [ ] **Step 2: Verify red**

```bash
uv run --extra dev pytest tests/test_atomic_persistence.py -q
```

Expected: FAIL — `write_json_atomic` / `write_text_atomic` are not importable.

- [ ] **Step 3: Implement**

In `json_state.py`: rename `_atomic_write_json` to public `write_json_atomic` (keep a module-private alias so `update_json_state` keeps working), and add `write_text_atomic(path: Path, content: str)` with the same tempfile → fsync → `os.replace` → cleanup contract. Document the durability contract (old-or-new, never truncated) in both docstrings. Keep the existing fsync behavior.

- [ ] **Step 4: Verify green**

Run the targeted tests, then `uv run --extra dev mypy src/openbiliclaw/memory/json_state.py`.

### Task 2: Route all memory-package writes through the helpers

**Files:**
- Modify: `src/openbiliclaw/memory/manager.py`
- Modify: `src/openbiliclaw/soul/profile_renderer.py`
- Test: `tests/test_atomic_persistence.py`

- [ ] **Step 1: Write failing tests**

1. `test_memory_layer_save_is_atomic` — build a `MemoryLayer`, save once, patch `os.replace` to raise, save mutated data; assert the file on disk still holds the first payload. Also assert `_loaded_mtime` is refreshed after a successful save (stat AFTER replace).
2. `test_sync_profile_files_is_atomic` — same interruption contract for `soul_profile.json` and `soul_profile.md` outputs.

- [ ] **Step 2: Verify red**

```bash
uv run --extra dev pytest tests/test_atomic_persistence.py -q
```

Expected: new tests FAIL — `MemoryLayer.save` (`manager.py:100-101`) and the renderer write directly.

- [ ] **Step 3: Implement**

Convert to the helpers:

- `MemoryLayer.save()` (`manager.py:91`) — keep UTF-8 / `ensure_ascii=False` / `indent=2` semantics.
- Every bare `json.dump` in `manager.py`: `save_feedback_state:264`, `save_account_sync_state:322`, `save_source_bootstrap_state:344`, `save_insight_candidates:532`, `save_cognition_updates:548`, `save_profile_overrides:581`, and the generic `save()` at `:101`.
- `soul/profile_renderer.py`: `sync_profile_files` outputs via `write_json_atomic` / `write_text_atomic`. Leave `append_changelog` as append-mode (append is not a whole-file rewrite; out of scope).

- [ ] **Step 4: Verify green**

Targeted tests, then run the full existing memory/soul test files to catch byte-format regressions (the helper adds a trailing newline — update any test asserting exact file bytes):

```bash
uv run --extra dev pytest tests/test_atomic_persistence.py tests -q -k "memory or profile"
```

### Task 3: Persistence inventory enforcement test

**Files:**
- Test: `tests/test_atomic_persistence.py`

- [ ] **Step 1: Write the enforcement test (red first)**

`test_no_bare_json_writes_in_memory_package` — walk the source of `src/openbiliclaw/memory/` and `src/openbiliclaw/soul/profile_renderer.py`; assert no call site opens a file in `"w"` mode and calls `json.dump` on it outside `json_state.py` itself (AST-based check preferred over regex; mirror the style of `tests/test_llm_prompts.py`'s convention test). If Task 2 missed a site, this test finds it — fix the site, not the test.

- [ ] **Step 2: Verify green**

```bash
uv run --extra dev pytest tests/test_atomic_persistence.py -q
```

### Task 4: D7 doc/comment cleanup + Phase 1 checkpoint

**Files:**
- Modify: `src/openbiliclaw/memory/manager.py`
- Modify: `docs/modules/soul.md`
- Modify: `docs/modules/memory.md` (if present; else the module doc covering `memory/`)
- Modify: `docs/changelog.md`

- [ ] **Step 1: Fix the lying docstring and TODOs**

`propagate_event` (`manager.py:822-859`): rewrite the docstring to state the actual contract — "persists the event row only; upward propagation is explicit via the profile pipeline (`api/app.py` → `ProfileUpdatePipeline`)". Delete the three stale `# TODO` comments at `manager.py:856-858`.

- [ ] **Step 2: Fix soul.md §2**

In the "画像更新逻辑详解" §2 behavior-event section: state explicitly that `analyze_events()` is init/`rebuild-profile` only, and the post-init incremental path is `ProfileUpdatePipeline`. One clarifying paragraph, no restructure.

- [ ] **Step 3: Doc sync + changelog**

Update the memory module doc's persistence description (atomic writes), add a `docs/changelog.md` bullet under the current version block.

- [ ] **Step 4: Phase 1 validation block**

```bash
uv run --extra dev pytest -q
uv run --extra dev ruff check src/openbiliclaw/memory/ src/openbiliclaw/soul/profile_renderer.py tests/test_atomic_persistence.py
uv run --extra dev ruff format --check src/ tests/
uv run --extra dev mypy src/openbiliclaw/memory/ src/openbiliclaw/soul/profile_renderer.py
git diff --check
```

## Phase 2 — ProfileStore serialized write path (MUST)

### Task 5: ProfileStore core

**Files:**
- Create: `src/openbiliclaw/soul/profile_store.py`
- Test: `tests/test_profile_store.py` (new)

- [ ] **Step 1: Write failing tests**

1. `test_mutate_applies_closure_to_fresh_profile_and_persists` — mutate sets a field; soul layer on disk reflects it; outcome reports `changed=True` and the `source` tag.
2. `test_mutate_rejects_coroutine_closure` — passing an `async def` closure raises `TypeError` immediately.
3. `test_mutate_refuses_empty_overwrite` — with a non-empty profile, a closure that empties it is rejected (no save) unless `allow_empty_overwrite=True`; a rejected attempt is visible in the outcome.
4. `test_mutate_serializes_concurrent_writers` — two concurrent `mutate` calls both commit; both fields survive.
5. `test_snapshot_returns_defensive_copy` — mutating a snapshot does not affect the store.
6. `test_mutate_can_update_preference_layer` — `layers=("soul", "preference")` closure receives/commits both under one lock.

- [ ] **Step 2: Verify red**

```bash
uv run --extra dev pytest tests/test_profile_store.py -q
```

- [ ] **Step 3: Implement**

`ProfileStore` per spec §2: `asyncio.Lock`; `snapshot()`; `async mutate(source, fn, *, layers=("soul",), allow_empty_overwrite=False)` with the critical-section contract (load fresh → apply sync closure → validate → save layers atomically → `sync_profile_files` → changelog render → notify — the save/notify path reuses `MemoryManager` so WS `profile_updated` keeps firing from one place). `MutationResult` (what the closure returns: `changed`, `summary`, `changed_fields`) and `MutationOutcome` dataclasses. Set a module-level `contextvars.ContextVar` (`_IN_PROFILE_STORE`) around the critical section — Task 6 wires the guard. Warn (log) when lock wait exceeds 5s. Construct the store in `SoulEngine.__init__` / runtime context so pipeline and engine share one instance.

- [ ] **Step 4: Verify green**

Targeted tests + `uv run --extra dev mypy src/openbiliclaw/soul/profile_store.py`.

### Task 6: Off-path write guard + lost-update regression test

**Files:**
- Modify: `src/openbiliclaw/memory/manager.py`
- Test: `tests/test_profile_store.py`

- [ ] **Step 1: Write failing tests**

1. `test_soul_layer_save_outside_store_raises_in_tests` — calling `get_layer("soul").save()` without the store contextvar set raises `RuntimeError` when the strict flag is on (pytest sets it via fixture/env, e.g. `OPENBILICLAW_STRICT_PROFILE_WRITES=1`); production default is a `logger.warning`.
2. `test_lost_update_is_prevented` — the spec's核心 regression: writer A takes `snapshot()`, awaits (simulated LLM `asyncio.sleep`), writer B commits a field via `mutate`, writer A then commits its delta via `mutate`; assert B's field survives on disk. Also add the inverse control test documenting that the OLD pattern (save a stale whole-document snapshot) would have clobbered it — implemented against the store only, no legacy path resurrection.

- [ ] **Step 2: Verify red, then implement**

Guard in `MemoryLayer.save()` for layer names `{"soul", "preference"}`: if `_IN_PROFILE_STORE` unset → warning, or raise under the strict flag. `init`/bootstrap paths must go through the store too (Task 8) — until they do, the guard stays warning-only by default so nothing breaks mid-migration.

- [ ] **Step 3: Verify green**

```bash
uv run --extra dev pytest tests/test_profile_store.py -q
```

### Task 7: Split layer updaters into compute/apply and migrate the pipeline

**Files:**
- Modify: `src/openbiliclaw/soul/layer_updaters.py`
- Modify: `src/openbiliclaw/soul/pipeline.py`
- Test: existing pipeline/updater tests + `tests/test_profile_store.py`

- [ ] **Step 1: Write failing test**

`test_pipeline_layer_update_commits_via_store` — run `_update_layer` for a SURFACE batch against a store with the strict guard on; assert commit succeeds (i.e. pipeline no longer calls `soul_layer.save()` directly) and the outcome carries `source="pipeline.surface"`.

- [ ] **Step 2: Verify red**

```bash
uv run --extra dev pytest tests/test_profile_store.py::test_pipeline_layer_update_commits_via_store -q
```

- [ ] **Step 3: Implement the split**

Per updater in `layer_updaters.py` (dispatcher `update_layer:71`):

- `_update_surface:102` — pure computation: becomes a sync `apply_surface(profile, signals)` executed wholly inside the closure.
- `_update_interest:149` — `compute_interest_delta` (async: `PreferenceAnalyzer.analyze_events` on drained signals) + `apply_interest_delta` (sync: write flat preference, `populate_from_flat_preference`, dislike pool purge marker, speculative seed injection) committed with `layers=("soul", "preference")`.
- `_update_role:281`, `_update_values:369`, `_update_core:482` — `compute_*_delta` (async LLM delta prompt against a `snapshot()`) + `apply_*_delta` (sync; preserves existing diff protection: `changed=False` → no-op).
- `regenerate_portrait:610` — compute portrait text outside; closure assigns the field. Source `pipeline.portrait`.

`pipeline._update_layer` (`pipeline.py:828-869`): drop `_load_profile`/`_save_profile`; compute outside, `await store.mutate(f"pipeline.{layer.value}", apply_closure)`. **Preserve the re-buffer-on-failure behavior** (`pipeline.py:854-855`) for failures in either the compute or the mutate step. `_record_changelog` moves into the store's commit (delete the pipeline-local call once parity is confirmed).

- [ ] **Step 4: Verify green**

```bash
uv run --extra dev pytest tests -q -k "pipeline or layer_updater or profile_store"
```

### Task 8: Migrate SoulEngine writers

**Files:**
- Modify: `src/openbiliclaw/soul/engine.py`
- Test: existing engine tests + `tests/test_profile_store.py`

- [ ] **Step 1: Write failing test**

`test_engine_writers_commit_via_store` — with the strict guard on, exercise: `build_initial_profile` (source `engine.init_build`), `update_from_feedback`'s `_sync_insight_to_soul_snapshot` (`engine.insight_feedback`), `learn_from_dialogue` significant-rebuild path (`engine.dialogue`), `process_feedback_batch_if_needed` rebuild path (`engine.feedback_batch`). Mock LLM calls; assert no guard violation and correct source tags in outcomes.

- [ ] **Step 2: Verify red, then implement**

Convert the four writers (`engine.py:341`, `:814`, `:924`, `:1026`): LLM/`ProfileBuilder.build` stays outside; the closure applies the built profile / snapshot sync to fresh state. Full-rebuild closures replace the whole profile (parity with today) — the store's serialization is what makes that safe now. Preference-layer replacement in `analyze_events` / feedback batch goes through `layers=("soul", "preference")` (or preference-only mutate where soul is untouched). `apply_user_edit` stays overrides-only (no store involvement) — assert that in the test.

- [ ] **Step 3: Verify green**

```bash
uv run --extra dev pytest tests -q -k "engine or feedback or profile_store"
```

### Task 9: Migrate peripheral writers

**Files:**
- Modify: `src/openbiliclaw/soul/cognition_cycle.py`
- Modify: `src/openbiliclaw/soul/consolidator.py`
- Modify: `src/openbiliclaw/soul/dislike_writeback.py`
- Modify: `src/openbiliclaw/api/app.py`
- Test: existing tests for each module

- [ ] **Step 1: Write failing test, verify red**

`test_peripheral_writers_commit_via_store` — with strict guard on: `CognitionCycle._sync_to_profile` (`cognition_cycle.py:388`, source `cognition.sync`), consolidation apply (`consolidator.py:120`, `consolidation.apply`), `dislike_writeback` (`dislike_writeback.py:129`, `dislike.writeback`), probe promotion `merge_confirmed_interest` (`app.py:4618`, `probe.promotion`).

- [ ] **Step 2: Implement**

All four are already sync mutations against loaded state → straightforward closure conversion. Consolidation's override remap continues writing `profile_overrides.json` directly (overrides are outside the store by design).

- [ ] **Step 3: Flip the guard default**

With all nine writers migrated, make the strict guard the default under pytest (fixture in `conftest.py`) and keep production at warning for one release. Run the FULL suite to smoke out any writer the inventory missed:

```bash
uv run --extra dev pytest -q
```

### Task 10: Static enforcement + Phase 2 checkpoint

**Files:**
- Test: `tests/test_profile_store.py`
- Modify: `docs/modules/soul.md`, `docs/modules/soul-pipeline-architecture.md`, `docs/changelog.md`, `docs/architecture.md`

- [ ] **Step 1: Static writer-inventory test**

`test_no_soul_layer_saves_outside_profile_store` — AST/regex scan of `src/` asserting no `get_layer("soul")`/`get_layer("preference")` result has `.save()` invoked outside `soul/profile_store.py` (allow `memory/manager.py` internals invoked BY the store). Mirrors the prompt-cache convention test pattern.

- [ ] **Step 2: Docs**

soul.md gains a "ProfileStore 写路径" subsection (writer table with source tags, the compute-outside/apply-inside contract, empty-overwrite guard); pipeline-architecture doc and `docs/architecture.md` soul-layer description updated; changelog bullet. Update the spec's acceptance checklist boxes for Phases 1–2.

- [ ] **Step 3: Phase 2 validation block**

```bash
uv run --extra dev pytest -q
uv run --extra dev ruff check src/openbiliclaw/soul/ src/openbiliclaw/memory/ src/openbiliclaw/api/app.py tests/test_profile_store.py
uv run --extra dev mypy src/openbiliclaw/soul/ src/openbiliclaw/memory/
git diff --check
```

## Phase 3 — Structured history + restore (RECOMMENDED)

### Task 11: History JSONL + daily snapshots at the commit point

**Files:**
- Modify: `src/openbiliclaw/soul/profile_store.py`
- Test: `tests/test_profile_store.py`

- [ ] **Step 1: Write failing tests**

1. `test_committed_mutation_appends_history_record` — record schema per spec §3 (`seq`, `ts`, `source`, `summary`, `changed_fields`, `digest_before`, `digest_after`); rejected empty-overwrite attempts append a `rejected` record.
2. `test_history_rotation` — exceeding the cap (~2000 records / 5 MB) rotates to `.1`.
3. `test_first_commit_of_day_writes_snapshot` — `profile_snapshots/YYYY-MM-DD.json` created once per day; retention prunes past 14 days.
4. `test_history_failure_does_not_block_commit` — patch the history append to raise; the profile save still succeeds.

- [ ] **Step 2: Verify red, implement, verify green**

History/snapshot writes use the Phase 1 atomic helpers; append via `open(..., "a")` with one `json.dumps` line per record. Digests = sha256 of the deterministic serialization (`sort_keys=True`).

```bash
uv run --extra dev pytest tests/test_profile_store.py -q
```

### Task 12: `profile-history` / `profile-restore` CLI + Phase 3 checkpoint

**Files:**
- Modify: `src/openbiliclaw/cli.py`
- Modify: `docs/modules/cli.md`, `docs/modules/soul.md`, `docs/changelog.md`
- Test: `tests/test_cli.py` (or the existing CLI test file)

- [ ] **Step 1: Write failing tests**

1. `profile-history --limit 5 --source pipeline.interest` prints matching records.
2. `profile-restore --date <d> --dry-run` prints a field-level diff and writes nothing.
3. `profile-restore --date <d>` restores THROUGH the store (source `manual.revert`, appears in history; empty-overwrite guard still applies).

- [ ] **Step 2: Verify red, implement, verify green**

Typer commands wrapping store APIs (`read_history`, `restore_snapshot`). Restore acquires the store lock with a timeout and a clear "daemon busy" message on failure.

- [ ] **Step 3: Phase 3 validation block + docs**

cli.md gains both commands; changelog bullet; run:

```bash
uv run --extra dev pytest -q && uv run --extra dev ruff check src/openbiliclaw/ tests/ && uv run --extra dev mypy src/openbiliclaw/
```

## Phase 4 — Feedback/dialogue unification (RECOMMENDED)

### Task 13: Feedback becomes a pipeline path with a significance stage

**Files:**
- Modify: `src/openbiliclaw/api/app.py`
- Modify: `src/openbiliclaw/soul/pipeline.py`
- Modify: `src/openbiliclaw/cli.py` (the `feedback` command)
- Test: `tests/test_soul_pipeline.py` / feedback tests

- [ ] **Step 1: Write failing tests**

1. `test_api_feedback_ingests_pipeline_signal` — `POST /api/feedback` produces a `FEEDBACK` signal in the pipeline (immediate cognition card unchanged).
2. `test_interest_update_with_feedback_triggers_significance_check` — after an INTEREST update whose drained batch contains feedback signals, `_preference_changed_significantly` gates a full rebuild committed as `pipeline.rebuild`.
3. `test_feedback_burst_llm_call_budget` — a 10-feedback burst produces **no more** preference-analysis LLM calls than today's debounced batch path (count mocked LLM invocations; this is the spec's hard parity line — if it fails, add a FEEDBACK-scoped debounce inside the pipeline rather than reviving the scheduler).

- [ ] **Step 2: Verify red, implement, verify green**

Route `/api/feedback` and CLI `feedback` (`cli.py:8359-8419`) through pipeline ingest; implement the significance stage as a post-INTEREST hook in `pipeline._update_layer`'s result handling; full rebuild = compute outside (ProfileBuilder) + `store.mutate("pipeline.rebuild", …)`.

```bash
uv run --extra dev pytest tests -q -k "feedback or pipeline"
```

### Task 14: Retire the parallel engine + Phase 4 checkpoint

**Files:**
- Modify: `src/openbiliclaw/soul/engine.py`
- Delete/empty: `src/openbiliclaw/runtime/feedback_scheduler.py`
- Modify: `src/openbiliclaw/memory/manager.py` (feedback_state load/save removal)
- Modify: `docs/modules/soul.md`, `docs/modules/cli.md` (if CLI text changes), `docs/changelog.md`, `docs/architecture.md`
- Test: full suite

- [ ] **Step 1: Convert then remove**

First commit: `process_feedback_batch_if_needed` and `learn_from_dialogue`'s preference re-analysis become thin deprecated wrappers delegating to pipeline ingestion (dialogue insights already flow as `DIALOGUE_INSIGHT` signals). Second commit, after `/api/feedback`, CLI, and chat paths are verified: delete `FeedbackBatchScheduler`, `_feedback_batch_lock`, `_schedule_post_feedback_tasks` wiring in `app.py`, and `feedback_state.json` load/save (`manager.py:235-264`) — confirm nothing else reads the cursor before deleting.

- [ ] **Step 2: Behavior-parity verification**

- Feedback still updates INTEREST immediately (strong-signal min_signals=1).
- Significance gate still triggers full rebuild on high-weight interest shifts (reuse existing `_preference_changed_significantly` tests, now exercised via the pipeline).
- `test_feedback_burst_llm_call_budget` still green.

- [ ] **Step 3: Phase 4 validation block + docs**

soul.md's two-tier feedback section (`soul.md:445-507`) rewritten to describe the single pipeline path; architecture diagrams checked for the removed scheduler box; changelog bullet; spec acceptance boxes for Phase 4 ticked. Then:

```bash
uv run --extra dev pytest -q
uv run --extra dev ruff check src/ tests/
uv run --extra dev mypy src/
git diff --check
```

## Self-Review

- Spec coverage: all MUST items (atomic persistence, ProfileStore + nine-writer migration, guard/enforcement, D7 doc fixes) and all RECOMMENDED items (history/snapshots/restore CLI, feedback unification with the LLM-budget parity line) map to tasks; OPTIONAL Phases 5–6 are explicitly out of scope.
- Ordering: guard stays warning-only until Task 9 flips it, so the suite never breaks mid-migration; Phase 4 tasks depend only on Phase 2 artifacts, matching the spec's dependency note.
- Placeholder scan: no TBD/TODO steps; every task names concrete files, symbols, and line anchors from the verified writer inventory.
- Convention check: tests follow `test_<behavior>` naming, asyncio_mode=auto (no manual marks), enforcement tests mirror the existing prompt-cache convention test pattern, and every checkpoint carries the CLAUDE.md doc-sync obligations.
