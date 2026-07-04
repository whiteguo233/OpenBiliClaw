# Discovery Inspiration Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the production hardening around the new secondary-interest inspiration flow: fuller coverage feedback, repair generation, hard must-cover behavior, explore-specific validation, and a dry-run/debug surface for real request inspection.

**Architecture:** Keep the existing `KeywordPlanner` as the orchestration boundary. Add small deterministic helpers for quota/coverage/debug shaping, extend the storage coverage snapshot with candidate-level distribution, add a bounded repair pass through the existing discovery LLM route, and expose a CLI dry-run command that reuses the planner instead of duplicating query logic.

**Tech Stack:** Python 3.12, SQLite, existing `KeywordPlanner`, existing `LLMService.complete_structured_task()`, Typer CLI, pytest, Ruff, MyPy.

---

### Task 1: Candidate-Level Coverage Snapshot

**Files:**
- Modify: `src/openbiliclaw/storage/database.py`
- Test: `tests/test_discovery_inspiration.py`

- [ ] **Step 1: Write failing test**

Add a test that inserts `discovery_candidates` rows carrying `metadata.source_interest`, `metadata.content_type`, and `source_platform`, then asserts `get_keyword_interest_coverage_snapshot()` returns `candidate_count`, `candidate_share`, dominant candidate platform, and dominant candidate content type for that secondary interest.

- [ ] **Step 2: Verify red**

Run:

```bash
uv run --extra dev pytest tests/test_discovery_inspiration.py::test_keyword_interest_coverage_snapshot_counts_candidate_distribution -q
```

Expected: FAIL because the snapshot currently only includes keyword and admitted pool counts.

- [ ] **Step 3: Implement minimal DAO extension**

Extend `get_keyword_interest_coverage_snapshot()` to aggregate `discovery_candidates.metadata.source_interest`, falling back to `topic_group` / `pool_topic_label`-like metadata when present, and compute candidate distribution shares.

- [ ] **Step 4: Verify green**

Run the targeted test.

### Task 2: Hard Quotas, Must-Cover, And Explore Validation

**Files:**
- Modify: `src/openbiliclaw/runtime/keyword_planner.py`
- Test: `tests/test_keyword_planner.py`

- [ ] **Step 1: Write failing tests**

Add tests that:

1. Undercovered selected interests in `coverage_constraints.must_cover` survive insertion when the curator returns at least one keyword for them.
2. `query_kind="explore"` rejects expansions whose `lens_family` is not adjacent/exploration-oriented, while `regular` keeps normal lenses.

- [ ] **Step 2: Verify red**

Run:

```bash
uv run --extra dev pytest tests/test_keyword_planner.py::test_inspiration_stage_prioritizes_must_cover_interests_before_extra_keywords tests/test_keyword_planner.py::test_inspiration_stage_filters_non_lateral_lenses_for_explore -q
```

Expected: FAIL because insertion currently preserves curator order and does not apply explore-specific hard validation.

- [ ] **Step 3: Implement deterministic validation**

Before platform insertion, order expansions so must-cover interests with valid platform keywords are considered first, then apply per-interest/per-lens caps. For `explore`, accept only lateral lens families such as adjacent hobby, one-hop exploration, community language, creator/expert, or practical tutorial.

- [ ] **Step 4: Verify green**

Run the targeted tests.

### Task 3: Bounded Curator Repair Pass

**Files:**
- Modify: `src/openbiliclaw/runtime/keyword_planner.py`
- Test: `tests/test_keyword_planner.py`

- [ ] **Step 1: Write failing test**

Add a test where the first curator response overproduces one interest, trimming leaves a platform below its need, and a second `discovery.keyword_inspiration.repair` response fills an undercovered interest.

- [ ] **Step 2: Verify red**

Run:

```bash
uv run --extra dev pytest tests/test_keyword_planner.py::test_inspiration_stage_repairs_trimmed_platform_shortfall -q
```

Expected: FAIL because no repair caller exists.

- [ ] **Step 3: Implement repair**

After first-pass insertion planning, if a platform still has remaining capacity and must-cover interests are missing, send one bounded repair prompt with selected interests, accepted keywords, rejected reasons, platform guide, and grounding records. Parse the same expansion schema and run it through the same deterministic validator.

- [ ] **Step 4: Verify green**

Run the targeted test.

### Task 4: Planner Dry-Run Debug Surface

**Files:**
- Modify: `src/openbiliclaw/runtime/keyword_planner.py`
- Modify: `src/openbiliclaw/cli.py`
- Test: `tests/test_keyword_planner.py`

- [ ] **Step 1: Write failing test**

Add a planner-level dry-run test that returns selected interests, brainstorm branches, grounding records, generated platform keywords, and rejected reasons without inserting keywords.

- [ ] **Step 2: Verify red**

Run:

```bash
uv run --extra dev pytest tests/test_keyword_planner.py::test_inspiration_dry_run_reports_intermediate_keywords_without_inserting -q
```

Expected: FAIL because there is no dry-run API.

- [ ] **Step 3: Implement planner dry-run and CLI command**

Add `KeywordPlanner.preview_inspiration_keywords()` and `openbiliclaw keyword-inspiration-dry-run` with platform/kind/limit flags. The command loads local config, profile, planner, provider, and prints a JSON report.

- [ ] **Step 4: Verify green**

Run the targeted test, then run CLI help.

### Task 5: Docs And Real Request Smoke

**Files:**
- Modify: `docs/modules/discovery.md`
- Modify: `docs/modules/storage.md`
- Modify: `docs/modules/cli.md`
- Modify: `docs/changelog.md`

- [ ] **Step 1: Update docs**

Document candidate-level coverage, repair pass, explore validation, and the dry-run command.

- [ ] **Step 2: Run validation**

Run:

```bash
uv run --extra dev pytest tests/test_discovery_inspiration.py tests/test_keyword_planner.py tests/test_llm_module_routing_e2e.py -q
uv run --extra dev ruff check src/openbiliclaw/discovery/inspiration.py src/openbiliclaw/runtime/keyword_planner.py src/openbiliclaw/storage/database.py src/openbiliclaw/cli.py tests/test_discovery_inspiration.py tests/test_keyword_planner.py
uv run --extra dev mypy src/openbiliclaw/discovery/inspiration.py src/openbiliclaw/runtime/keyword_planner.py src/openbiliclaw/storage/database.py src/openbiliclaw/cli.py
git diff --check
```

- [ ] **Step 3: Run real dry-run if credentials are present**

Run the dry-run command against local `config.toml` and report the generated keyword list. If credentials or external providers fail, report the exact boundary and keep the automated validation result.

## Self-Review

- Spec coverage: addresses the remaining gaps named by the user: true request inspection, richer coverage feedback, repair, hard must-cover behavior, explore-specific validation, and a debug surface.
- Placeholder scan: no TBD/TODO instructions are left.
- Type consistency: all new public APIs are on `KeywordPlanner` or `Database`, and CLI uses the planner rather than duplicating generation logic.
