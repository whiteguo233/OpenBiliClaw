# Discovery Candidate Supply Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ensure Discover fills the raw candidate queue with enough unseen candidates before Evo drains it.

**Architecture:** Add a bounded supply loop to `DiscoveryCandidatePipeline`, keep it inside the existing refresh lock through `ContinuousRefreshController`, and add database helpers for pre-enqueue known filtering. Related-chain seed selection will prefer explicit positive signals before falling back to plain views.

**Tech Stack:** Python, SQLite-backed `Database`, pytest async tests, existing runtime refresh scheduler.

---

### Task 1: Candidate Supply Loop

**Files:**
- Modify: `src/openbiliclaw/discovery/candidate_pipeline.py`
- Modify: `src/openbiliclaw/storage/database.py`
- Test: `tests/test_discovery_candidate_pipeline.py`

**Steps:**
1. Add failing tests for `ensure_pending_supply()` retrying when known candidates are filtered.
2. Add database helper methods to read existing candidate keys and cached BVIDs.
3. Add pre-enqueue known filtering and supply-loop diagnostics.
4. Run the targeted pipeline tests.

### Task 2: Refresh Integration

**Files:**
- Modify: `src/openbiliclaw/runtime/refresh.py`
- Test: `tests/test_refresh_runtime.py`

**Steps:**
1. Add a failing test that refresh calls the supply loop when the pipeline supports it.
2. Fall back to `produce_and_enqueue()` for older test doubles or external implementations.
3. Keep the current drain lock behavior unchanged.
4. Run targeted refresh tests.

### Task 3: Related Seed Quality

**Files:**
- Modify: `src/openbiliclaw/discovery/strategies/related_chain.py`
- Test: `tests/test_related_chain_strategy.py`

**Steps:**
1. Add a failing test showing positive events outrank plain views as related seeds.
2. Rank event seed candidates by explicit positive signal, then recent positive view, then plain view fallback.
3. Keep title-prefix dedupe and existing preference/search fallback.
4. Run related-chain tests.

### Task 4: Docs And Verification

**Files:**
- Modify: `docs/modules/discovery.md`
- Modify: `docs/modules/runtime.md`
- Modify: `docs/changelog.md`

**Steps:**
1. Document the supply loop and related seed behavior.
2. Run `ruff check`, targeted pytest, and broader pytest if time allows.
3. Review git diff for unrelated changes.

