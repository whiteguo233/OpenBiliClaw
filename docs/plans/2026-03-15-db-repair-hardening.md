# Database Repair And Hardening Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an explicit `openbiliclaw db-repair` recovery flow that preserves as much SQLite data as possible, while also hardening runtime database access so lock storms are less likely to evolve into corruption again.

**Architecture:** Introduce a dedicated storage-maintenance module responsible for integrity checks, backups, repair/rebuild, and backup rotation. Then tighten runtime database access by sharing one `Database` instance in high-traffic runtime paths and routing all writes through the same guarded write helper.

**Tech Stack:** Python 3.14, sqlite3, Typer CLI, pytest, existing runtime/storage stack

---

### Task 1: Add storage maintenance primitives for integrity check and backups

**Files:**
- Create: `src/openbiliclaw/storage/maintenance.py`
- Test: `tests/test_storage.py`

**Step 1: Write the failing test**

Add storage tests that expect a maintenance helper to:

- report a healthy SQLite file as healthy
- create timestamped backups for `.db` and optional `.db-wal`
- rotate old backups according to a simple retention rule

Use temporary directories and small throwaway SQLite files.

**Step 2: Run test to verify it fails**

Run: `/Users/white/workspace/OpenBiliClaw/.venv/bin/pytest tests/test_storage.py -q`

Expected: FAIL because `maintenance.py` and the new helpers do not exist.

**Step 3: Write minimal implementation**

Implement the smallest maintenance API that can:

- inspect a SQLite file with `PRAGMA integrity_check`
- create timestamped cold backups
- delete backups outside the retention window

Keep it file-system based and deterministic; no CLI wiring yet.

**Step 4: Run test to verify it passes**

Run: `/Users/white/workspace/OpenBiliClaw/.venv/bin/pytest tests/test_storage.py -q`

Expected: PASS

**Step 5: Commit**

```bash
git add src/openbiliclaw/storage/maintenance.py tests/test_storage.py
git commit -m "feat: add sqlite maintenance helpers"
```

### Task 2: Add repair/rebuild flow that preserves the original database on failure

**Files:**
- Modify: `src/openbiliclaw/storage/maintenance.py`
- Test: `tests/test_storage.py`

**Step 1: Write the failing test**

Add tests that expect the repair flow to:

- refuse to run if the database file is currently in use
- leave healthy databases untouched
- create a repaired database path when recovery succeeds
- keep the original database intact when recovery fails

Use a deliberately malformed file fixture for the unhappy path and a valid SQLite database for the healthy path.

**Step 2: Run test to verify it fails**

Run: `/Users/white/workspace/OpenBiliClaw/.venv/bin/pytest tests/test_storage.py -q`

Expected: FAIL because no repair orchestration exists yet.

**Step 3: Write minimal implementation**

Extend the maintenance module with a repair orchestration API that:

- checks for active file holders
- always creates backups before repair attempts
- tries to rebuild into a fresh file
- verifies the rebuilt file
- never overwrites the original database unless the rebuilt file passes validation

**Step 4: Run test to verify it passes**

Run: `/Users/white/workspace/OpenBiliClaw/.venv/bin/pytest tests/test_storage.py -q`

Expected: PASS

**Step 5: Commit**

```bash
git add src/openbiliclaw/storage/maintenance.py tests/test_storage.py
git commit -m "feat: add sqlite repair workflow"
```

### Task 3: Harden the Database wrapper with consistent write execution and health hooks

**Files:**
- Modify: `src/openbiliclaw/storage/database.py`
- Test: `tests/test_storage.py`

**Step 1: Write the failing test**

Add tests that expect:

- every write-style method to route through the guarded write helper
- lock retry behavior to apply consistently to update paths, not just inserts
- the database wrapper to expose a lightweight health-check hook usable by runtime/CLI code

Target methods like:

- `update_recommendation_content`
- `update_recommendation_feedback`
- `mark_recommendations_presented`

**Step 2: Run test to verify it fails**

Run: `/Users/white/workspace/OpenBiliClaw/.venv/bin/pytest tests/test_storage.py -q`

Expected: FAIL because several write paths still use raw `conn.execute(...); commit()`.

**Step 3: Write minimal implementation**

Refactor the Database wrapper so that:

- all writes use one guarded helper
- health checks are exposed without duplicating sqlite plumbing
- behavior stays otherwise unchanged

Do not add unrelated schema or query changes.

**Step 4: Run test to verify it passes**

Run: `/Users/white/workspace/OpenBiliClaw/.venv/bin/pytest tests/test_storage.py -q`

Expected: PASS

**Step 5: Commit**

```bash
git add src/openbiliclaw/storage/database.py tests/test_storage.py
git commit -m "fix: unify sqlite write paths"
```

### Task 4: Share runtime database instances in high-traffic builders

**Files:**
- Modify: `src/openbiliclaw/memory/manager.py`
- Modify: `src/openbiliclaw/api/app.py`
- Modify: `src/openbiliclaw/cli.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_api_app.py`

**Step 1: Write the failing test**

Add focused tests that expect runtime construction paths to avoid spinning up redundant database instances where one can be shared.

At minimum, cover:

- API app bootstrap path
- CLI builders used by start/recommend/discover where feasible

The tests can use instrumented fake/stub database objects to count initialization calls.

**Step 2: Run test to verify it fails**

Run: `/Users/white/workspace/OpenBiliClaw/.venv/bin/pytest tests/test_api_app.py tests/test_cli.py -q`

Expected: FAIL because the current builders create multiple `Database(...)` objects independently.

**Step 3: Write minimal implementation**

Refactor the high-traffic runtime builders so they can share the same initialized database object instead of reconnecting repeatedly in the same process.

Keep the public CLI/API behavior unchanged.

**Step 4: Run test to verify it passes**

Run: `/Users/white/workspace/OpenBiliClaw/.venv/bin/pytest tests/test_api_app.py tests/test_cli.py -q`

Expected: PASS

**Step 5: Commit**

```bash
git add src/openbiliclaw/memory/manager.py src/openbiliclaw/api/app.py src/openbiliclaw/cli.py tests/test_api_app.py tests/test_cli.py
git commit -m "refactor: share runtime sqlite instances"
```

### Task 5: Add `openbiliclaw db-repair` and scheduled cold backups

**Files:**
- Modify: `src/openbiliclaw/cli.py`
- Modify: `src/openbiliclaw/storage/maintenance.py`
- Test: `tests/test_cli.py`

**Step 1: Write the failing test**

Add CLI tests that expect:

- `openbiliclaw db-repair` to report “database healthy” when no repair is needed
- `openbiliclaw db-repair` to refuse when the database is in use
- `openbiliclaw db-repair` to report backup and repair outcomes clearly
- startup-adjacent maintenance logic to create scheduled cold backups only when the backup interval has elapsed

**Step 2: Run test to verify it fails**

Run: `/Users/white/workspace/OpenBiliClaw/.venv/bin/pytest tests/test_cli.py -q`

Expected: FAIL because the command and backup scheduling do not exist.

**Step 3: Write minimal implementation**

Add:

- a new CLI command `db-repair`
- cold-backup scheduling logic with conservative defaults
- backup rotation invocation after backup creation

Do not auto-repair in `start`; only surface diagnostics and the explicit repair command.

**Step 4: Run test to verify it passes**

Run: `/Users/white/workspace/OpenBiliClaw/.venv/bin/pytest tests/test_cli.py -q`

Expected: PASS

**Step 5: Commit**

```bash
git add src/openbiliclaw/cli.py src/openbiliclaw/storage/maintenance.py tests/test_cli.py
git commit -m "feat: add db repair command"
```

### Task 6: Update docs and run final verification

**Files:**
- Modify: `docs/modules/cli.md`
- Modify: `docs/modules/config.md` (if backup settings are added)
- Modify: `docs/architecture.md` (if runtime database sharing needs documenting)
- Modify: `docs/changelog.md`

**Step 1: Update docs**

Document:

- the `db-repair` command
- backup behavior and retention
- runtime database hardening changes

**Step 2: Run backend verification**

Run: `/Users/white/workspace/OpenBiliClaw/.venv/bin/pytest tests/test_storage.py tests/test_cli.py tests/test_api_app.py tests/test_memory_manager.py -q`

Expected: PASS

**Step 3: Run wider regression if needed**

Run: `/Users/white/workspace/OpenBiliClaw/.venv/bin/pytest tests/test_recommendation_engine.py -q`

Expected: PASS

**Step 4: Commit**

```bash
git add docs/modules/cli.md docs/modules/config.md docs/architecture.md docs/changelog.md
git commit -m "docs: record sqlite repair and backup flow"
```
