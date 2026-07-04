# Dynamic Delight Threshold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make proactive delight recommendations use the current formal candidate pool's Top 10% score boundary, with the existing 0.70/0.80 thresholds as profile-aware floors and startup fallbacks.

**Architecture:** Storage owns the percentile calculation because it owns `content_cache` and delight queue predicates. Runtime/API/CLI ask storage for the effective dynamic threshold after deciding the profile-aware floor. The regular recommendation feed uses the same dynamic threshold in its delight-claim guard so delight queue and regular feed exclusion stay in sync.

**Tech Stack:** Python, SQLite, pytest, Ruff, MyPy.

---

### Task 1: Storage Dynamic Threshold

**Files:**
- Modify: `src/openbiliclaw/storage/database.py`
- Test: `tests/test_delight_scorer.py`

- [ ] **Step 1: Write failing tests**

Add tests near the existing delight storage tests:

```python
def test_database_dynamic_delight_threshold_uses_top_ten_percent_boundary(tmp_path: Path) -> None:
    database = _make_database(tmp_path)
    for index in range(40):
        score = 0.50 + (index * 0.01)
        bvid = f"BV1DYN{index:02d}"
        database.cache_content(bvid, title=bvid, relevance_score=score)
        database.conn.execute(
            """
            UPDATE content_cache
            SET pool_status = 'fresh',
                pool_expression = 'copy',
                pool_topic_label = 'topic',
                style_key = 'deep_focus',
                topic_group = 'group'
            WHERE bvid = ?
            """,
            (bvid,),
        )
    database.conn.commit()

    threshold = database.dynamic_delight_threshold(default_threshold=0.70)

    assert threshold == pytest.approx(0.86)
```

```python
def test_database_dynamic_delight_threshold_falls_back_when_pool_is_small(tmp_path: Path) -> None:
    database = _make_database(tmp_path)
    for index in range(19):
        database.cache_content(f"BV1SMALL{index:02d}", title="small", relevance_score=0.95)

    assert database.dynamic_delight_threshold(default_threshold=0.70) == pytest.approx(0.70)
```

```python
def test_database_dynamic_delight_threshold_never_drops_below_default(tmp_path: Path) -> None:
    database = _make_database(tmp_path)
    for index in range(40):
        database.cache_content(f"BV1LOW{index:02d}", title="low", relevance_score=0.40)

    assert database.dynamic_delight_threshold(default_threshold=0.70) == pytest.approx(0.70)
```

- [ ] **Step 2: Run RED tests**

Run:

```bash
pytest tests/test_delight_scorer.py::test_database_dynamic_delight_threshold_uses_top_ten_percent_boundary tests/test_delight_scorer.py::test_database_dynamic_delight_threshold_falls_back_when_pool_is_small tests/test_delight_scorer.py::test_database_dynamic_delight_threshold_never_drops_below_default -q
```

Expected: fail because `Database.dynamic_delight_threshold` does not exist.

- [ ] **Step 3: Implement storage helper**

Add constants and method in `database.py`:

```python
_DELIGHT_DYNAMIC_TOP_FRACTION = 0.10
_DELIGHT_DYNAMIC_MIN_SAMPLE_SIZE = 20
```

```python
def dynamic_delight_threshold(self, *, default_threshold: float = _DELIGHT_CLAIM_MIN_SCORE) -> float:
    floor = _normalize_score(default_threshold)
    cursor = self.conn.execute(
        """
        SELECT COALESCE(relevance_score, 0.0) AS score
        FROM content_cache
        WHERE COALESCE(pool_status, 'fresh') IN ('fresh', 'shown')
          AND COALESCE(feedback_type, '') != 'dislike'
          AND COALESCE(relevance_score, 0.0) > 0.0
        ORDER BY score DESC
        """
    )
    scores = [float(row["score"]) for row in cursor.fetchall()]
    if len(scores) < _DELIGHT_DYNAMIC_MIN_SAMPLE_SIZE:
        return floor
    boundary_index = max(1, math.ceil(len(scores) * _DELIGHT_DYNAMIC_TOP_FRACTION)) - 1
    return max(floor, min(1.0, max(0.0, scores[boundary_index])))
```

- [ ] **Step 4: Run GREEN tests**

Run the same pytest command. Expected: pass.

### Task 2: Dynamic Claim Guard and Queue Predicates

**Files:**
- Modify: `src/openbiliclaw/storage/database.py`
- Test: `tests/test_delight_scorer.py`

- [ ] **Step 1: Write failing test**

Add a test that a 0.72 delight is not claimed when the dynamic threshold is 0.88:

```python
def test_pool_candidates_use_dynamic_delight_claim_threshold(tmp_path: Path) -> None:
    database = _make_database(tmp_path)
    for index in range(40):
        score = 0.50 + (index * 0.01)
        bvid = f"BV1BASE{index:02d}"
        database.cache_content(bvid, title=bvid, relevance_score=score)
        database.conn.execute(
            """
            UPDATE content_cache
            SET pool_expression = 'copy',
                pool_topic_label = 'topic',
                style_key = 'deep_focus',
                topic_group = 'base'
            WHERE bvid = ?
            """,
            (bvid,),
        )
    database.cache_content("BV1MID", title="mid delight", relevance_score=0.72)
    database.conn.execute(
        """
        UPDATE content_cache
        SET pool_expression = 'copy',
            pool_topic_label = 'topic',
            style_key = 'deep_focus',
            topic_group = 'mid'
        WHERE bvid = 'BV1MID'
        """
    )
    database.update_delight_score(
        "BV1MID",
        delight_score=0.72,
        delight_reason="ready",
        delight_hook="hook",
    )
    database.conn.commit()

    rows = database.get_pool_candidates(limit=50, max_per_topic_group=0)

    assert "BV1MID" in [row["bvid"] for row in rows]
```

- [ ] **Step 2: Run RED test**

Run:

```bash
pytest tests/test_delight_scorer.py::test_pool_candidates_use_dynamic_delight_claim_threshold -q
```

Expected: fail because the fixed 0.70 guard excludes `BV1MID`.

- [ ] **Step 3: Implement parameterized guard**

Replace `_DELIGHT_CLAIM_GUARD_SQL` with:

```python
def _delight_claim_guard_sql() -> str:
    return """
                  AND NOT (
                    COALESCE(delight_notified, 0) = 1
                    OR (
                      COALESCE(delight_score, 0.0) >= ?
                      AND COALESCE(delight_reason, '') != ''
                      AND COALESCE(delight_hook, '') != ''
                    )
                  )
"""
```

In `get_pool_candidates()` and `_load_available_pool_candidate_rows()`, compute:

```python
delight_threshold = self.dynamic_delight_threshold(default_threshold=_DELIGHT_CLAIM_MIN_SCORE)
delight_guard_sql = _delight_claim_guard_sql()
```

and insert `delight_threshold` into SQL params immediately after `guard_params`.

- [ ] **Step 4: Run GREEN test**

Run the RED test. Expected: pass.

### Task 3: Runtime/API/CLI Use Dynamic Threshold

**Files:**
- Modify: `src/openbiliclaw/runtime/refresh.py`
- Modify: `src/openbiliclaw/api/app.py`
- Modify: `src/openbiliclaw/cli.py`
- Test: `tests/test_refresh_runtime.py`

- [ ] **Step 1: Write failing runtime test**

Update `test_refresh_controller_uses_shared_delight_threshold_for_runtime_queries` to use a fake database method:

```python
def dynamic_delight_threshold(self, *, default_threshold: float) -> float:
    self.dynamic_default_thresholds.append(default_threshold)
    return 0.88
```

Assert:

```python
assert database.dynamic_default_thresholds == [DEFAULT_DELIGHT_THRESHOLD, DEFAULT_DELIGHT_THRESHOLD]
assert database.count_delight_thresholds == [0.88]
assert database.get_delight_thresholds == [0.88]
```

- [ ] **Step 2: Run RED test**

Run:

```bash
pytest tests/test_refresh_runtime.py::test_refresh_controller_uses_shared_delight_threshold_for_runtime_queries -q
```

Expected: fail because runtime still passes `DEFAULT_DELIGHT_THRESHOLD`.

- [ ] **Step 3: Implement runtime helper**

Add `_profile_delight_default_threshold()` and `_dynamic_delight_threshold()` on `ContinuousRefreshController`.

`_profile_delight_default_threshold()` reads `memory_manager.get_layer("preference").data["exploration_openness"]`, falls back to `0.5`, and calls `effective_delight_threshold()`.

`_dynamic_delight_threshold()` calls `database.dynamic_delight_threshold(default_threshold=profile_default)` when available, otherwise returns the profile default.

Use `_dynamic_delight_threshold()` in:

- `get_pending_delight()`
- `_safe_count_delight_candidates()`
- runtime status pending delight count

- [ ] **Step 4: Update API/CLI direct database calls**

In API routes that directly call `get_delight_candidates`, prefer `ctx.runtime_controller._dynamic_delight_threshold()` if available; fall back to `DEFAULT_DELIGHT_THRESHOLD`.

In `openbiliclaw delight`, compute:

```python
default_threshold = effective_delight_threshold(profile.preferences.exploration_openness)
threshold = database.dynamic_delight_threshold(default_threshold=default_threshold)
candidate = database.get_delight_candidate(min_delight_score=threshold)
```

- [ ] **Step 5: Run GREEN test**

Run the runtime test. Expected: pass.

### Task 4: Recommendation Precompute Uses Dynamic Threshold

**Files:**
- Modify: `src/openbiliclaw/recommendation/engine.py`
- Test: `tests/test_recommendation_engine.py`

- [ ] **Step 1: Write failing test**

Add a fake database method used by `precompute_delight_scores`:

```python
def dynamic_delight_threshold(self, *, default_threshold: float) -> float:
    self.default_thresholds.append(default_threshold)
    return 0.88
```

Assert that `get_pool_candidates_needing_delight_score` receives `min_delight_score_for_reason=0.88`.

- [ ] **Step 2: Run RED test**

Run the new test. Expected: fail because `precompute_delight_scores` uses only `effective_delight_threshold()`.

- [ ] **Step 3: Implement**

In `precompute_delight_scores`, after computing the profile-aware default, call `self._database.dynamic_delight_threshold(default_threshold=default_threshold)` when available and use that as `effective_threshold`.

- [ ] **Step 4: Run GREEN test**

Run the new test. Expected: pass.

### Task 5: Documentation and Verification

**Files:**
- Modify: `docs/modules/recommendation.md`
- Modify: `docs/modules/storage.md`
- Modify: `docs/changelog.md`
- Modify: `docs/diagrams/recommendation-architecture.html`

- [ ] **Step 1: Update docs**

Replace fixed-threshold language with the dynamic rule:

```text
默认 0.70；探索开放度低时底线 0.80；正式候选池样本足够时取 max(profile floor, pool Top 10% boundary)。
```

- [ ] **Step 2: Run targeted tests**

Run:

```bash
pytest tests/test_delight_scorer.py tests/test_refresh_runtime.py::test_refresh_controller_uses_shared_delight_threshold_for_runtime_queries tests/test_recommendation_engine.py -q
```

- [ ] **Step 3: Run lint/type checks for touched Python paths**

Run:

```bash
ruff check src/openbiliclaw/recommendation/delight.py src/openbiliclaw/recommendation/engine.py src/openbiliclaw/runtime/refresh.py src/openbiliclaw/api/app.py src/openbiliclaw/cli.py src/openbiliclaw/storage/database.py tests/test_delight_scorer.py tests/test_refresh_runtime.py tests/test_recommendation_engine.py
mypy src/
```

- [ ] **Step 4: Commit implementation**

Commit only touched implementation/docs files, leaving unrelated untracked files untouched.
