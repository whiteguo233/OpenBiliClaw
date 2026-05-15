# Pool-Aware Discovery Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make discovery use a lightweight recommendation-pool distribution snapshot so replenishment avoids saturated directions and fills related gaps with less post-trim waste.

**Architecture:** Add a `PoolDistributionSnapshot` data model plus a database-backed builder. Runtime passes the snapshot into `ContentDiscoveryEngine.discover()`, the engine forwards it to compatible strategies and applies a soft pool-aware rerank before caching. First implementation targets `SearchStrategy` prompt guidance and engine-level rerank; recommendation serving remains unchanged.

**Tech Stack:** Python dataclasses, SQLite, existing discovery/runtime/recommendation modules, pytest, Ruff, MyPy.

---

### Task 1: Add Pool Snapshot Model And Database Stats

**Files:**
- Create: `src/openbiliclaw/discovery/pool_snapshot.py`
- Modify: `src/openbiliclaw/storage/database.py`
- Test: `tests/test_pool_snapshot.py`

**Step 1: Write failing model and DB tests**

Create `tests/test_pool_snapshot.py` with:

```python
from openbiliclaw.discovery.pool_snapshot import build_pool_distribution_snapshot
from openbiliclaw.storage.database import Database


def test_build_pool_snapshot_marks_saturated_topics_and_styles(tmp_path):
    db = Database(tmp_path / "test.db")
    for index in range(12):
        db.cache_content(
            f"BVai{index}",
            title=f"AI item {index}",
            topic_group="AI 编程",
            style_key="deep_dive",
            franchise_key="",
            source="search",
            relevance_score=0.8,
            pool_expression="x",
            pool_topic_label="x",
        )
    for index in range(3):
        db.cache_content(
            f"BVdoc{index}",
            title=f"doc item {index}",
            topic_group="人物纪录",
            style_key="story_doc",
            source="search",
            relevance_score=0.75,
            pool_expression="x",
            pool_topic_label="x",
        )

    snapshot = build_pool_distribution_snapshot(
        db,
        pool_target_count=60,
        source_targets={"bilibili": 48, "xiaohongshu": 6, "douyin": 6},
    )

    assert snapshot.pool_available_count == 15
    assert "AI 编程" in snapshot.saturated_topics
    assert "deep_dive" in snapshot.saturated_styles
    assert snapshot.source_deficits["bilibili"] == 33
```

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_pool_snapshot.py -q`

Expected: FAIL because `openbiliclaw.discovery.pool_snapshot` does not exist.

**Step 3: Implement snapshot module**

Add `PoolDistributionSnapshot` and `build_pool_distribution_snapshot()`:

```python
@dataclass(frozen=True)
class PoolDistributionSnapshot:
    pool_target_count: int
    pool_available_count: int
    source_targets: dict[str, int]
    source_counts: dict[str, int]
    source_deficits: dict[str, int]
    saturated_topics: tuple[str, ...] = ()
    saturated_styles: tuple[str, ...] = ()
    saturated_franchises: tuple[str, ...] = ()
    undercovered_axes: tuple[str, ...] = ()

    def to_prompt_hints(self) -> dict[str, object]:
        return {
            "avoid_topics": list(self.saturated_topics[:12]),
            "avoid_styles": list(self.saturated_styles[:8]),
            "avoid_franchises": list(self.saturated_franchises[:8]),
            "prefer_axes": list(self.undercovered_axes[:8]),
            "source_deficits": dict(self.source_deficits),
        }
```

In `Database`, add `get_pool_distribution_counts()` returning top counts for `topic_group`, `style_key`, and `franchise_key` among fresh, non-disliked, unrecommended rows. Keep linkability checks aligned with existing pool methods where source URL matters.

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_pool_snapshot.py -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add src/openbiliclaw/discovery/pool_snapshot.py src/openbiliclaw/storage/database.py tests/test_pool_snapshot.py
git commit -m "feat: add pool distribution snapshot"
```

### Task 2: Pass Snapshot Through Runtime And Discovery Engine

**Files:**
- Modify: `src/openbiliclaw/runtime/refresh.py`
- Modify: `src/openbiliclaw/discovery/engine.py`
- Test: `tests/test_refresh_runtime.py`
- Test: `tests/test_discovery_engine.py`

**Step 1: Write failing runtime test**

Add a test that builds `ContinuousRefreshController` with a fake discovery engine accepting `pool_snapshot`, sets `pool_count` below target, runs `_run_refresh_plan()`, and asserts a non-None snapshot was passed.

**Step 2: Write failing engine compatibility tests**

Add two tests:

```python
async def test_discovery_engine_passes_pool_snapshot_to_supported_strategy(...):
    ...


async def test_discovery_engine_keeps_legacy_strategy_signature(...):
    ...
```

The first fake strategy has `discover(self, profile, limit=20, *, pool_snapshot=None)`.
The second fake strategy keeps `discover(self, profile, limit=20)`.

**Step 3: Run tests to verify failure**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_refresh_runtime.py::test_run_refresh_plan_passes_pool_snapshot tests/test_discovery_engine.py::test_discovery_engine_passes_pool_snapshot_to_supported_strategy tests/test_discovery_engine.py::test_discovery_engine_keeps_legacy_strategy_signature -q
```

Expected: FAIL because no snapshot parameter is wired.

**Step 4: Implement runtime snapshot creation**

In `refresh.py`, import `build_pool_distribution_snapshot`. Before each discovery call in `_run_refresh_plan()`, build:

```python
pool_snapshot = build_pool_distribution_snapshot(
    self.database,
    pool_target_count=self.pool_target_count,
    source_targets=self._source_target_counts(),
)
```

Pass `pool_snapshot=pool_snapshot` only if the discovery callable accepts it, mirroring `_call_accepts_strategy_limits()`.

**Step 5: Implement discovery engine forwarding**

Extend `ContentDiscoveryEngine.discover(..., pool_snapshot=None)` and `_run_strategies(..., pool_snapshot=None)`.

Add a helper:

```python
async def _call_strategy_discover(strategy, profile, *, limit, pool_snapshot):
    if _strategy_accepts_pool_snapshot(strategy.discover):
        return await strategy.discover(profile, limit=limit, pool_snapshot=pool_snapshot)
    return await strategy.discover(profile, limit=limit)
```

Use it in both fully-parallel and phased execution paths.

**Step 6: Run tests to verify pass**

Run the same targeted tests.

Expected: PASS.

**Step 7: Commit**

```bash
git add src/openbiliclaw/runtime/refresh.py src/openbiliclaw/discovery/engine.py tests/test_refresh_runtime.py tests/test_discovery_engine.py
git commit -m "feat: pass pool snapshot into discovery"
```

### Task 3: Add Pool-Aware Search Query Guidance

**Files:**
- Modify: `src/openbiliclaw/llm/prompts.py`
- Modify: `src/openbiliclaw/discovery/strategies/search.py`
- Test: `tests/test_llm_prompts.py`
- Test: `tests/test_search_strategy.py`

**Step 1: Write failing prompt test**

Add a test that calls:

```python
messages = build_search_queries_prompt(
    profile_summary={"interests": [{"name": "AI", "weight": 0.9}]},
    pool_hints={
        "avoid_topics": ["AI 编程", "原神"],
        "prefer_axes": ["人物纪录", "审美体验"],
        "avoid_styles": ["deep_dive"],
    },
)
```

Assert the user prompt contains `<pool_distribution_hints>`, `AI 编程`, and `人物纪录`.

**Step 2: Write failing strategy test**

Construct `SearchStrategy`, call `discover(..., pool_snapshot=snapshot)`, and assert the fake LLM received a prompt containing `pool_distribution_hints`.

**Step 3: Run tests to verify failure**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_llm_prompts.py::test_search_prompt_includes_pool_distribution_hints tests/test_search_strategy.py::test_search_strategy_passes_pool_snapshot_to_query_prompt -q
```

Expected: FAIL because prompt and strategy signatures do not accept hints.

**Step 4: Update prompt builder**

Change `build_search_queries_prompt()` to accept `pool_hints: dict[str, object] | None = None`.

Add system rule:

```text
如果 user 消息包含 <pool_distribution_hints>，这些是当前推荐池已经拥挤或欠覆盖的方向。
avoid_topics / avoid_styles 是软避让信号；prefer_axes 是优先补货方向。
不要为了避让而生成与用户画像无关的 query。
```

Append hints to the user prompt only when non-empty.

**Step 5: Update SearchStrategy**

Change:

```python
async def discover(self, profile, limit=20, *, pool_snapshot=None)
```

and:

```python
queries = await self._generate_queries(profile, pool_snapshot=pool_snapshot)
```

Inside `_generate_queries`, pass `pool_snapshot.to_prompt_hints()` when present.

**Step 6: Run tests to verify pass**

Run the targeted prompt/search tests.

Expected: PASS.

**Step 7: Commit**

```bash
git add src/openbiliclaw/llm/prompts.py src/openbiliclaw/discovery/strategies/search.py tests/test_llm_prompts.py tests/test_search_strategy.py
git commit -m "feat: guide search discovery with pool hints"
```

### Task 4: Add Pool-Aware Soft Rerank Before Caching

**Files:**
- Modify: `src/openbiliclaw/discovery/engine.py`
- Test: `tests/test_discovery_engine.py`

**Step 1: Write failing rerank test**

Add a test with three results:

```python
sat = DiscoveredContent(bvid="BVsat", title="AI", topic_group="AI 编程", style_key="deep_dive", relevance_score=0.82)
gap = DiscoveredContent(bvid="BVgap", title="纪录", topic_group="人物纪录", style_key="story_doc", relevance_score=0.79)
strong = DiscoveredContent(bvid="BVstrong", title="AI high", topic_group="AI 编程", relevance_score=0.96)
```

With snapshot saturated topic `AI 编程`, limit 2 should keep `BVstrong` and `BVgap`, not `BVsat`.

**Step 2: Run test to verify failure**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_discovery_engine.py::test_pool_snapshot_soft_rerank_prefers_undercovered_topics_without_dropping_strong_matches -q`

Expected: FAIL because current sorting only uses relevance/source/topic compression.

**Step 3: Implement soft rerank**

Add `_apply_pool_snapshot_rerank(results, pool_snapshot)` after `_compress_topic_repeats()` and before `_cache_results()`.

Suggested scoring:

```python
adjusted = item.relevance_score
if topic in saturated_topics:
    adjusted -= 0.08
if style in saturated_styles:
    adjusted -= 0.04
if franchise in saturated_franchises:
    adjusted -= 0.10
if topic in undercovered_axes:
    adjusted += 0.04
```

Use adjusted score only for ordering, not persistence. Keep a floor rule: items with raw score >= 0.92 should not be pushed below lower-quality items solely due to saturation.

**Step 4: Run test to verify pass**

Run the targeted rerank test.

Expected: PASS.

**Step 5: Commit**

```bash
git add src/openbiliclaw/discovery/engine.py tests/test_discovery_engine.py
git commit -m "feat: rerank discovery results by pool saturation"
```

### Task 5: Docs And Regression Verification

**Files:**
- Modify: `docs/modules/discovery.md`
- Modify: `docs/modules/recommendation.md`
- Modify: `docs/changelog.md`
- Optional Modify: `docs/modules/config.md` only if new config fields are added.

**Step 1: Update docs**

Document:

- `PoolDistributionSnapshot`.
- Which fields are soft signals.
- Runtime passes snapshot to discovery.
- Search query generation consumes pool hints.
- Recommendation serving remains unchanged.

**Step 2: Run targeted tests**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_pool_snapshot.py tests/test_discovery_engine.py tests/test_search_strategy.py tests/test_llm_prompts.py tests/test_refresh_runtime.py -q
```

Expected: PASS.

**Step 3: Run quality checks**

Run:

```bash
ruff format src/ tests/
ruff check src/ tests/
mypy src/
pytest
```

Expected: PASS. If full `pytest` is too slow or blocked by external integration assumptions, record the exact failing/blocked tests and keep targeted tests green.

**Step 4: Commit**

```bash
git add docs/modules/discovery.md docs/modules/recommendation.md docs/changelog.md
git commit -m "docs: document pool-aware discovery"
```
