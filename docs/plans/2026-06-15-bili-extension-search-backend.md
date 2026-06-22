# Bili Extension Search Backend Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add the Python backend half of Bilibili extension search fallback: task queue, producer, API endpoints, candidate ingestion, refresh wiring, and tests.

**Architecture:** Mirror the XHS extension task shape, not the Douyin plugin/direct stack. The backend enqueues `bili_search` tasks only when the normal Bilibili search API is already in cooldown and an extension runtime-stream client is present; extension results return Bilibili video metadata and are normalized into `discovery_candidates` for the shared evaluator.

**Tech Stack:** Python 3.12+, FastAPI, SQLite task tables, existing `DiscoveryCandidatePipeline`, existing `KeywordFetchCoordinator`, pytest.

---

### Task 1: Bili Task Queue

**Files:**
- Create: `src/openbiliclaw/sources/bili_tasks.py`
- Test: `tests/test_bili_tasks.py`

**Steps:**
1. Write tests for `BiliTaskQueue` table creation, daily budget, `next_pending()` stale reclaim, result merge dedupe, complete/fail states, and `source_keyword_id_from_bili_task()`.
2. Run: `pytest tests/test_bili_tasks.py -q`; expected: fails because module is missing.
3. Implement `BiliTaskQueue` with schema matching XHS/DY (`bili_tasks`) and a Bili-specific result merge keyed by `bvid/content_id/url/title`.
4. Run the same tests and keep them green.

### Task 2: API Endpoints and Result to Candidate Helper

**Files:**
- Modify: `src/openbiliclaw/api/app.py`
- Test: `tests/test_api_bili_tasks.py`

**Steps:**
1. Write tests for:
   - `GET /api/sources/bili/next-task` returns 204 when empty and claims the oldest task.
   - `POST /api/sources/bili/task-result` rejects missing `task_id`.
   - Final `ok` result merges videos, enqueues `discovery_candidates` with `source_platform=bilibili`, `source_strategy=bili-extension-search`, and drains the candidate pipeline.
   - Failed result marks task failed and marks the keyword failed when task payload has `source_keyword_id`.
   - `POST /api/sources/bili/kick` broadcasts `bili_task_available` best-effort.
2. Run: `pytest tests/test_api_bili_tasks.py -q`; expected: fails because routes do not exist.
3. Add `_cache_bili_search_items()` helper near the source ingest helpers, using `discovered_content_to_candidate_write()`.
4. Add `/api/sources/bili/next-task`, `/task-result`, `/kick`.
5. Run endpoint tests until green.

### Task 3: Runtime Producer

**Files:**
- Create: `src/openbiliclaw/runtime/bilibili_producer.py`
- Test: `tests/test_bilibili_producer.py`

**Steps:**
1. Write producer tests for disabled/not-due/pool-full/no-profile/no-cooldown/no-presence skips, legacy generated keyword enqueue, planner-claim enqueue with `source_keyword_id`, budget rollback, and kick callback.
2. Run: `pytest tests/test_bilibili_producer.py -q`; expected: fails because module is missing.
3. Implement `BilibiliExtensionSearchProducer`:
   - gate on `enabled`, min interval, candidate pool fullness, `search_cooldown_remaining(client)>0`, and `presence.is_present(grace_seconds)`;
   - prefer `keyword_fetch.claim("bilibili")` when the unified planner is enabled;
   - otherwise generate a few search queries through the same prompt helper used by `SearchStrategy`;
   - enqueue one `bili_search` task per query with query, limit, page/page_size, source strategy, and optional `source_keyword_id`;
   - mark claimed keywords `executing`, rollback on budget refusal, and kick the extension.
4. Run producer tests until green.

### Task 4: Refresh and Runtime Context Wiring

**Files:**
- Modify: `src/openbiliclaw/runtime/refresh.py`
- Modify: `src/openbiliclaw/api/runtime_context.py`
- Test: `tests/test_refresh_runtime.py`

**Steps:**
1. Add tests proving `ContinuousRefreshController` has a Bili extension producer loop/tick, invokes it when Bilibili is under quota, and skips when at quota.
2. Add runtime-context test or extend an existing one to confirm the producer is built when `[sources.bilibili].enabled=true`.
3. Implement `bilibili_producer` field, loop, tick, `run_forever()` task, and factory wiring.
4. Run targeted tests until green.

### Task 5: Required Docs

**Files:**
- Modify: `docs/modules/bilibili.md`
- Modify: `docs/modules/runtime.md`
- Modify: `docs/changelog.md`
- Modify as needed if architecture/data-flow text changes: `docs/architecture.md`, `docs/spec.md`, `README.md`, `README_EN.md`

**Steps:**
1. Document the Bili extension search fallback endpoints, task table, and runtime producer.
2. Add a changelog entry under the current top version block.
3. Run docs-sensitive tests if present, plus the final verification commands.

### Verification

Run at minimum:

```bash
ruff format src/ tests/
ruff check src/ tests/
mypy src/
pytest tests/test_bili_tasks.py tests/test_api_bili_tasks.py tests/test_bilibili_producer.py tests/test_refresh_runtime.py -q
pytest
```
