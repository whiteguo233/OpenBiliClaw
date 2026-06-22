# Douyin DOM-First Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Douyin `search`, `hot`, and `feed` discovery use browser DOM interactions first, with only passive response observation and no default backend direct-cookie fallback.

**Architecture:** The browser extension remains the executor for the three discovery routes. The background dispatcher opens Douyin home and sends execution messages without shortcut URL jumps; the content script triggers DOM flows and collects DOM/passive fetch-tap results; the Python wrapper stops falling back to direct-cookie when plugin results are empty unless explicitly configured.

**Tech Stack:** TypeScript Chrome extension with `node:test`, Python async client tests with `pytest`, SQLite-backed `DyTaskQueue`.

**Status:** Completed on 2026-06-18. Real-browser E2E also found and fixed passive XHR forwarding for feed and target-scope filtering for search / hot / feed results.

---

### Task 1: Lock Python Fallback Behavior

**Files:**
- Modify: `tests/test_douyin_plugin_search.py`
- Modify: `src/openbiliclaw/sources/douyin_plugin_search.py`

- [x] **Step 1: Write failing tests**

Add tests asserting default plugin-empty results do not call direct fallback:

```python
@pytest.mark.asyncio
async def test_plugin_search_client_does_not_fallback_to_direct_on_empty_task_by_default(
    database: Database,
) -> None:
    fallback = _FallbackClient()
    queue = DyTaskQueue(database)
    client = DouyinPluginSearchClient(
        database=database,
        direct_client=fallback,
        wait_seconds=2,
        poll_interval_seconds=0.01,
        kick=lambda: None,
    )

    async def complete_empty_task() -> None:
        for _ in range(100):
            task = queue.next_pending()
            if task:
                queue.merge_result(str(task["id"]), videos=[], scope_counts={"dy_search": 0}, complete=True)
                return
            await asyncio.sleep(0.01)
        raise AssertionError("search task was not enqueued")

    result, _ = await asyncio.gather(client.search_aweme("猫", limit=5), complete_empty_task())

    assert fallback.keywords == []
    assert result == []
```

Also add equivalent assertions for `get_hot_board()` and `get_recommend_feed()`.

- [x] **Step 2: Run failing tests**

Run:

```bash
pytest tests/test_douyin_plugin_search.py -q
```

Expected: the new tests fail because direct fallback is still called.

- [x] **Step 3: Implement fallback switch**

Add `allow_direct_fallback: bool = False` to `DouyinPluginSearchClient.__init__`, store it, and guard the existing fallback calls:

```python
if self._allow_direct_fallback:
    logger.info("douyin plugin search empty; falling back to direct-cookie search")
    return await self._direct_client.search_aweme(keyword, limit=limit)
return []
```

Apply the same pattern to hot and feed.

- [x] **Step 4: Run tests**

Run:

```bash
pytest tests/test_douyin_plugin_search.py -q
```

Expected: PASS.

### Task 2: Stop Dispatcher Shortcut Navigation

**Files:**
- Modify: `extension/tests/dy-task-dispatcher.test.ts`
- Modify: `extension/src/background/dy-task-dispatcher.ts`

- [x] **Step 1: Write failing tests**

Add tests that inspect exported navigation helpers and assert search/hot/feed start from home:

```ts
test("discovery task page URLs stay on douyin home", () => {
  assert.equal(buildDyDiscoveryPageUrl("search", "猫"), "https://www.douyin.com/");
  assert.equal(buildDyDiscoveryPageUrl("hot", "2495363"), "https://www.douyin.com/");
  assert.equal(buildDyDiscoveryPageUrl("feed"), "https://www.douyin.com/");
});
```

- [x] **Step 2: Run failing tests**

Run:

```bash
cd extension && npm test -- tests/dy-task-dispatcher.test.ts
```

Expected: FAIL because `buildDyDiscoveryPageUrl` is not defined.

- [x] **Step 3: Implement helper and use it**

Export `buildDyDiscoveryPageUrl()` returning `https://www.douyin.com/`, replace `buildSearchPageUrl()` and `buildHotPageUrl()` usage with homepage navigation, then send `DY_SEARCH_EXECUTE` / `DY_HOT_EXECUTE` after inject.

- [x] **Step 4: Run tests**

Run:

```bash
cd extension && npm test -- tests/dy-task-dispatcher.test.ts
```

Expected: PASS.

### Task 3: Make Content Script DOM-First

**Files:**
- Modify: `extension/tests/dy-content-script.test.ts`
- Modify: `extension/src/content/douyin.ts`

- [x] **Step 1: Write failing tests**

Export a small policy helper and test that active bridge use is disabled for discovery:

```ts
test("douyin discovery execution policy is dom first", () => {
  assert.deepEqual(douyinDiscoveryExecutionPolicy(), {
    search: { activeApiBridge: false, passiveFetchTap: true, domInteraction: true },
    hot: { activeApiBridge: false, passiveFetchTap: true, domInteraction: true },
    feed: { activeApiBridge: false, passiveFetchTap: true, domInteraction: true },
  });
});
```

- [x] **Step 2: Run failing tests**

Run:

```bash
cd extension && npm test -- tests/dy-content-script.test.ts
```

Expected: FAIL because `douyinDiscoveryExecutionPolicy` is not defined.

- [x] **Step 3: Implement DOM-first policy and remove active bridge calls**

Add `douyinDiscoveryExecutionPolicy()`. In `runSearch`, remove `harvestSearchViaApiBridge()` and rely on `triggerSearchUi()`, passive fetch-tap messages, DOM extraction, and scrolling. In `runHot`, use DOM entry/click helpers and remove `harvestHotRelatedViaApiBridge()`. In `runFeed`, remove `harvestFeedViaApiBridge()` and rely on homepage DOM extraction, passive messages, and scrolling.

- [x] **Step 4: Run tests**

Run:

```bash
cd extension && npm test -- tests/dy-content-script.test.ts
```

Expected: PASS.

### Task 4: Documentation

**Files:**
- Modify: `docs/changelog.md`
- Modify: `docs/modules/discovery.md`
- Modify: `README.md`

- [x] **Step 1: Update docs**

Document that Douyin discovery now uses DOM-first browser execution with passive response observation and no default direct-cookie fallback for `search/hot/feed`.

- [x] **Step 2: Run docs-adjacent checks**

Run:

```bash
rg -n "抖音|douyin|direct-cookie|plugin" docs/modules/discovery.md docs/changelog.md README.md
```

Expected: Updated text reflects DOM-first behavior.

### Task 5: Full Verification

- [x] **Step 1: Run focused Python tests**

```bash
pytest tests/test_douyin_plugin_search.py tests/test_cli.py -k "douyin" -q
```

- [x] **Step 2: Run focused extension tests**

```bash
cd extension && npm test -- tests/dy-task-dispatcher.test.ts tests/dy-content-script.test.ts tests/dy-fetch-tap.test.ts
```

- [x] **Step 3: Run type/lint smoke**

```bash
ruff check src/ tests/
cd extension && npm run typecheck
```
