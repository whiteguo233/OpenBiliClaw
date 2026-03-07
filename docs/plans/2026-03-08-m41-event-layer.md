# M4.1 事件层 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the event layer on top of SQLite so `MemoryManager` can persist, query, and summarize behavioral events.

**Architecture:** `Database` becomes the single source of truth for event storage and filtering, while `MemoryManager` exposes a higher-level API that delegates writes and reads to SQLite. Existing JSON-backed preference/soul layers remain unchanged.

**Tech Stack:** Python 3.14, sqlite3, pytest, mypy, ruff

---

### Task 1: Extend Database event queries and stats

**Files:**
- Modify: `src/openbiliclaw/storage/database.py`
- Test: `tests/test_storage.py`

**Step 1: Write the failing tests**

Add tests covering:

```python
def test_query_events_supports_type_keyword_and_time_filters() -> None:
    ...

def test_count_events_by_type_returns_grouped_counts() -> None:
    ...
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_storage.py -q`
Expected: FAIL because `query_events()` and `count_events_by_type()` do not exist.

**Step 3: Write minimal implementation**

- Add `query_events(...)`
- Add `count_events_by_type(...)`
- Keep SQL simple and parameterized

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_storage.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_storage.py src/openbiliclaw/storage/database.py
git commit -m "feat: add event query and stats APIs"
```

### Task 2: Route event layer through MemoryManager

**Files:**
- Modify: `src/openbiliclaw/memory/manager.py`
- Test: `tests/test_memory_manager.py`

**Step 1: Write the failing tests**

Add tests covering:

```python
def test_initialize_sets_up_database(tmp_path: Path) -> None:
    ...

@pytest.mark.asyncio
async def test_propagate_event_persists_to_sqlite(tmp_path: Path) -> None:
    ...

def test_query_events_and_stats_delegate_to_database(tmp_path: Path) -> None:
    ...
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_memory_manager.py -q`
Expected: FAIL because `MemoryManager` does not initialize a database or expose query/stat APIs.

**Step 3: Write minimal implementation**

- Construct `Database` from `data_dir / "openbiliclaw.db"` or configured path-compatible location
- Initialize database in `MemoryManager.initialize()`
- Implement `propagate_event()`
- Add `query_events()` and `get_event_stats()`

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_memory_manager.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_memory_manager.py src/openbiliclaw/memory/manager.py
git commit -m "feat: wire memory manager event layer to sqlite"
```

### Task 3: Run full verification

**Files:**
- Modify: none unless verification exposes issues

**Step 1: Run lint**

Run: `ruff check src/ tests/`
Expected: PASS

**Step 2: Run type check**

Run: `mypy src/`
Expected: PASS

**Step 3: Run tests**

Run: `pytest -q`
Expected: PASS

**Step 4: Commit final fixes if needed**

```bash
git add <files>
git commit -m "fix: polish event layer verification issues"
```
