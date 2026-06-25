# Profile Consolidation Inventory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make profile consolidation maintain a bounded active-like inventory by archiving low-value tail interests after duplicate merging.

**Architecture:** Extend `ProfileConsolidator` with configurable inventory targets, over-cap full-boundary behavior, and safe archival into `preference["archived_interests"]`. Extend `PreferenceAnalyzer.merge_preferences()` so new evidence can reactivate archived likes. Wire the config through existing SoulEngine construction paths and document the new behavior.

**Tech Stack:** Python 3.11+, dataclasses, JSON preference layer, Typer CLI, pytest, Ruff, MyPy.

---

### Task 1: Inventory Report And Over-Cap Run Semantics

**Files:**
- Modify: `src/openbiliclaw/soul/consolidator.py`
- Test: `tests/test_profile_consolidator.py`

- [ ] Add failing tests:
  - `test_run_if_due_does_not_skip_unchanged_digest_when_likes_over_target`
  - `test_over_cap_run_uses_full_like_boundary_for_tail_duplicates`
- [ ] Implement new `ProfileConsolidator` constructor fields:
  - `like_target_upper: int = 512`
  - `like_target_soft: int = 450`
  - `archive_enabled: bool = True`
- [ ] Make `run_if_due()` skip unchanged digest only when active likes are not over target.
- [ ] Make over-cap runs use all active likes as their boundary for clustering.
- [ ] Verify targeted tests pass.

### Task 2: Safe Archival

**Files:**
- Modify: `src/openbiliclaw/soul/consolidator.py`
- Test: `tests/test_profile_consolidator.py`

- [ ] Add failing tests:
  - `test_consolidation_archives_low_weight_tail_to_target`
  - `test_consolidation_does_not_archive_user_protected_likes`
  - `test_consolidation_reports_when_protected_inventory_exceeds_target`
  - `test_revert_restores_archived_interests`
- [ ] Add report fields: targets, archived interest names, protected interest names, inventory reason.
- [ ] Add protected-name extraction from `ProfileOverrides.interest_edits["likes"]`.
- [ ] Archive lowest-value unprotected likes until `min(soft, upper)` is reached.
- [ ] Persist `archived_interests` and include it in run records / revert.
- [ ] Verify targeted tests pass.

### Task 3: Archived Interest Reactivation

**Files:**
- Modify: `src/openbiliclaw/soul/preference_analyzer.py`
- Test: `tests/test_preference_analyzer.py`

- [ ] Add failing test `test_merge_preferences_reactivates_matching_archived_interest`.
- [ ] Preserve unmatched `archived_interests` in merged preference state.
- [ ] Remove a matching archived item when new evidence reactivates it into active interests.
- [ ] Verify targeted tests pass.

### Task 4: Config Wiring And CLI Reporting

**Files:**
- Modify: `src/openbiliclaw/config.py`
- Modify: `src/openbiliclaw/soul/engine.py`
- Modify: `src/openbiliclaw/cli.py`
- Modify: `src/openbiliclaw/api/runtime_context.py`
- Modify: `src/openbiliclaw/integrations/openclaw/bootstrap.py`
- Modify: `config.example.toml`
- Test: `tests/test_profile_consolidator.py`

- [ ] Add scheduler dataclass fields and normalization.
- [ ] Pass config into every `SoulEngine` construction path.
- [ ] Pass config into CLI-created `ProfileConsolidator`.
- [ ] Print archive count and inventory reason in `profile-consolidate`.
- [ ] Verify targeted config and consolidator tests pass.

### Task 5: Documentation And Verification

**Files:**
- Modify: `docs/modules/soul.md`
- Modify: `docs/modules/config.md`
- Modify: `docs/modules/cli.md`
- Modify: `docs/changelog.md`

- [ ] Document active vs archived interest inventory.
- [ ] Document new config fields.
- [ ] Document CLI dry-run reporting.
- [ ] Run:
  - `.venv/bin/python -m pytest tests/test_profile_consolidator.py tests/test_preference_analyzer.py -q`
  - `.venv/bin/python -m ruff check src/openbiliclaw/soul/consolidator.py src/openbiliclaw/soul/preference_analyzer.py src/openbiliclaw/config.py src/openbiliclaw/soul/engine.py src/openbiliclaw/cli.py src/openbiliclaw/api/runtime_context.py src/openbiliclaw/integrations/openclaw/bootstrap.py tests/test_profile_consolidator.py tests/test_preference_analyzer.py`
  - `.venv/bin/python -m mypy src/openbiliclaw/soul/consolidator.py src/openbiliclaw/soul/preference_analyzer.py src/openbiliclaw/config.py src/openbiliclaw/soul/engine.py`

## Self-Review

- Spec coverage: all requirements map to Tasks 1-5.
- Placeholder scan: no TBD/TODO/fill-in placeholders.
- Type consistency: `archived_interests` is a preference JSON list of interest dicts matching active interest shape.
