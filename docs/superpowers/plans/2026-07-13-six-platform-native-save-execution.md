# Six-Platform Native Save Execution Implementation Plan

> **Current-state correction (2026-08-31):** real Chrome verification found that Zhihu now exposes
> an item-level global `收藏 / 已收藏` toggle instead of the named-collection dialog assumed by this
> plan. Production target is now `知乎收藏`, `supports_named_collection=false`, and an initially
> `已收藏` control plus every verification-only path must remain zero-click.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make favorite and watch-later sync write to the real logged-in YouTube, Xiaohongshu, Douyin, X/Twitter, Zhihu, and Reddit accounts through the installed extension, while preserving OpenBiliClaw's local-first, default-off, manually retryable contract.

**Architecture:** The existing `SavedSyncService` and `NativeSaveRouter` remain authoritative. Parameterized extension-backed adapters submit a sanitized, durable `native_save` job to an `ExtensionNativeSaveBroker`; each platform's existing `/api/sources/<slug>/{next-task,task-result,kick}` channel multiplexes that job with its existing discovery/bootstrap work. Existing dispatchers recognize the discriminated task and hand it to a shared browser runner, while platform-specific content executors perform an idempotent same-origin request or visible-control action in the user's logged-in tab and return one correlated safe result.

**Tech Stack:** Python 3.11+, FastAPI/Pydantic, SQLite, asyncio, Chrome MV3 extension, TypeScript, vanilla DOM APIs, pytest, node:test, Ruff, MyPy.

## Global Constraints

- Local membership is committed before any platform mutation; a platform failure never rolls it back.
- `[saved_sync].auto_sync_enabled` stays default `false`; manual sync remains explicit and ignores that switch.
- Favorite always targets the platform-native favorite/save/bookmark. Watch later uses the native watch-later target only on YouTube and falls back to favorite everywhere else in this scope.
- The exact named target is `OpenBiliClaw` for YouTube favorite. Zhihu favorite/watch-later fallback targets the global `知乎收藏` state and does not claim a named container.
- The native-save backend path never receives or persists a Cookie, OAuth token, CSRF token, raw HTML, raw response body, or tokenized URL. Extension errors are reduced to allow-listed codes and control-character-free messages before POST and again before persistence.
- Every extension backend call uses `authenticatedFetch`; endpoint paths remain exactly `/api/sources/<slug>/{next-task,task-result,kick}`, where slugs are `yt`, `xhs`, `dy`, `x`, `zhihu`, and `reddit`.
- Existing discovery/bootstrap task types and result schemas remain backward compatible. A native-save result is accepted only when both `task_id` and `item_key` match the durable job.
- A callback after cancellation, lease loss, or completion is rejected idempotently and cannot overwrite a newer attempt.
- One platform's account writes serialize through the existing cross-source dispatcher mutex; different backend platform groups may wait concurrently.
- Do not automatically retry a platform response whose write outcome is uncertain. Only an unclaimed job may safely become `extension_required`; claimed timeout becomes terminal `failed` until explicit user retry.
- `unsupported_adapter_missing` is retryable after adapter registration. `unsupported_content_type` remains terminal/local-only. Do not make every `unsupported` row retryable.
- Existing Bilibili direct adapter and API behavior remain unchanged.
- State-changing real E2E is opt-in per named public test item and platform. Unit/integration test defaults are non-mutating.
- This plan updates popup, desktop web, mobile web, and CLI/config documentation; it adds no CLI mutation command.
- No version bump, tag, release, package upload, or marketplace publication is in scope.

---

## File Structure

New backend files:

- `src/openbiliclaw/saved_sync/extension_broker.py` — durable job submission, claim, correlation, wait, wake, and safe result translation.
- `src/openbiliclaw/saved_sync/adapters/extension.py` — reusable adapter implementation and six explicit capability definitions.
- `tests/test_extension_native_save_broker.py` — persistence, lease, timeout, correlation, and secret-redaction tests.
- `tests/test_extension_native_save_api.py` — multiplexed source-channel contract tests.
- `tests/test_saved_sync_extension_adapters.py` — exact capability/route/target matrix and runtime registration tests.

New extension files:

- `extension/src/shared/native-save.ts` — discriminated job/result types, validators, safe-code normalization.
- `extension/src/background/native-save-task-runner.ts` — active-tab lifecycle, mutex, message retry, timeout, result forwarding.
- `extension/src/background/x-task-dispatcher.ts` — X's first backend task dispatcher using the same exact source channel.
- `extension/src/content/native-save/runtime.ts` — common content-script message listener and result fence.
- `extension/src/content/native-save/reddit.ts`
- `extension/src/content/native-save/x.ts`
- `extension/src/content/native-save/youtube.ts`
- `extension/src/content/native-save/xiaohongshu.ts`
- `extension/src/content/native-save/douyin.ts`
- `extension/src/content/native-save/zhihu.ts` — platform-owned mutation and confirmation logic.
- `extension/tests/native-save-shared.test.ts`
- `extension/tests/native-save-task-runner.test.ts`
- `extension/tests/{reddit,x,youtube,xhs,dy,zhihu}-native-save.test.ts` — executor fixtures and status mapping.

Existing ownership remains:

- `src/openbiliclaw/storage/database.py` owns schema and all SQLite DAO operations.
- `src/openbiliclaw/api/app.py` multiplexes jobs into existing platform endpoints; no second task channel is added.
- `src/openbiliclaw/api/runtime_context.py` owns stable broker construction and hot-reload adapter registration.
- Existing platform task dispatchers retain polling and delegate only the `native_save` branch to the shared runner.
- Existing content entrypoints install exactly one platform-specific native-save listener.

---

### Task 1: Durable Extension Native-Save Job Ledger

**Files:**
- Create: `src/openbiliclaw/saved_sync/extension_broker.py`
- Modify: `src/openbiliclaw/storage/database.py:7431-7620`
- Create: `tests/test_extension_native_save_broker.py`

**Interfaces:**
- Produce `ExtensionNativeSaveJob`, `ExtensionNativeSaveResultIn`, and `ExtensionNativeSaveBroker`.
- Produce `Database.create_or_reuse_extension_native_save_job(job: ExtensionNativeSaveJob) -> dict[str, Any]`, atomically reusing an active row for the same platform/item/requested action after a backend reload.
- Produce `Database.claim_extension_native_save_job(platform_slug, lease_seconds) -> dict[str, Any] | None`.
- Produce `Database.complete_extension_native_save_job(job_id, item_key, status, error_code, error_message) -> bool`.
- Produce `Database.cancel_unclaimed_extension_native_save_job(job_id) -> bool` and `Database.get_extension_native_save_job(job_id) -> dict[str, Any] | None`.
- Produce `Database.expire_stale_extension_native_save_jobs(platform_slug: str, lease_seconds: float) -> int`; claimed timeouts are completed as `failed/extension_task_timeout`, never replayed.

- [ ] **Step 1: Write RED storage and broker tests**

```python
async def test_broker_persists_only_safe_job_fields(database: Database) -> None:
    broker = ExtensionNativeSaveBroker(database, wake_platform=AsyncMock())
    job_id = broker.enqueue(
        SavedItemInput(
            source_platform="reddit",
            content_id="t3_abc",
            content_url="https://www.reddit.com/r/test/comments/abc/demo/",
            content_type="post",
            title="not persisted in extension job",
        ),
        NativeSaveRoute("favorite", "favorite", "Reddit Saved"),
    )
    row = database.get_extension_native_save_job(job_id)
    assert set(row) >= {"job_id", "platform", "platform_slug", "item_key", "content_id",
                        "content_url", "content_type", "requested_action",
                        "resolved_action", "target_label", "status"}
    assert "title" not in row


def test_callback_requires_job_and_item_correlation(database: Database) -> None:
    job = make_claimed_job(database, platform="twitter", item_key="twitter:123")
    assert not database.complete_extension_native_save_job(
        job["job_id"], "twitter:999", "synced", "", ""
    )
    assert database.get_extension_native_save_job(job["job_id"])["status"] == "in_progress"
```

Also cover duplicate completion, unknown status/code, control characters, token-query stripping, job restoration after a new broker instance, atomic reuse of the same active platform/item/action, unclaimed dispatch timeout -> `extension_required`, claimed execution timeout -> `failed/extension_task_timeout`, and no automatic replay after an uncertain claim.

- [ ] **Step 2: Run focused tests and verify missing symbols**

Run: `.venv/bin/pytest tests/test_extension_native_save_broker.py -q`

Expected: FAIL during collection because `extension_broker` and DAO methods do not exist.

- [ ] **Step 3: Add the schema and transactional DAO**

Add this table in `_ensure_saved_sync_tables()`:

```sql
CREATE TABLE IF NOT EXISTS extension_native_save_jobs (
    job_id            TEXT PRIMARY KEY,
    platform          TEXT NOT NULL,
    platform_slug     TEXT NOT NULL,
    item_key          TEXT NOT NULL,
    content_id        TEXT NOT NULL,
    content_url       TEXT NOT NULL,
    content_type      TEXT NOT NULL,
    requested_action  TEXT NOT NULL CHECK(requested_action IN ('favorite', 'watch_later')),
    resolved_action   TEXT NOT NULL CHECK(resolved_action IN ('favorite', 'watch_later')),
    target_label      TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pending'
                      CHECK(status IN ('pending', 'in_progress', 'synced',
                        'already_synced', 'login_required', 'rate_limited',
                        'unsupported', 'failed', 'extension_required', 'cancelled')),
    claimed_at        TIMESTAMP,
    completed_at      TIMESTAMP,
    last_error_code   TEXT NOT NULL DEFAULT '',
    last_error_message TEXT NOT NULL DEFAULT '',
    created_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_extension_native_save_jobs_claim
    ON extension_native_save_jobs(platform_slug, status, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_extension_native_save_jobs_active_item
    ON extension_native_save_jobs(platform, item_key, requested_action)
    WHERE status IN ('pending', 'in_progress');
```

Validate UUIDs, canonical platform/slug pairs, allow-listed platform hosts, item keys, status allow-lists, code length 128, message length 512, and target length 256 before SQL. Canonicalize URLs before persistence: strip fragments and tracking/token query parameters (including `xsec_token`), and retain only identity-bearing query fields such as YouTube `v`. Return copied dicts, never raw mutable rows.

- [ ] **Step 4: Implement broker enqueue/wait/result translation**

```python
class ExtensionNativeSaveBroker:
    def enqueue(self, item: SavedItemInput, route: NativeSaveRoute) -> str:
        job = self._job_from_item(item, route)
        row = self._database.create_or_reuse_extension_native_save_job(job)
        return str(row["job_id"])

    async def save(self, item: SavedItemInput, route: NativeSaveRoute) -> NativeSaveResult:
        job_id = self.enqueue(item, route)
        await self._wake_platform(self._platform_slug(item.platform))
        row = await self._wait_for_terminal(job_id)
        return self._native_result_from_row(row)

    def claim_next(self, platform_slug: str) -> ExtensionNativeSaveJob | None:
        row = self._database.claim_extension_native_save_job(
            platform_slug, self._execution_deadline_seconds
        )
        return self._job_from_row(row) if row is not None else None

    def submit_result(self, result: ExtensionNativeSaveResultIn) -> bool:
        return self._database.complete_extension_native_save_job(
            result.task_id, result.item_key, result.status,
            result.error_code, result.error_message,
        )
```

`save()` must enqueue, publish `<slug>_task_available`, poll durable state, and return an existing `NativeSaveResult`. Constructor-injected dispatch/execution deadlines make tests deterministic. Production defaults must be commented as derived from the existing immediate kick plus 60-second alarm fallback and current per-platform dispatcher timeout caps, satisfying the repository threshold-provenance rule.

- [ ] **Step 5: Run tests, lint, types, and commit**

Run:

```bash
.venv/bin/pytest tests/test_extension_native_save_broker.py tests/test_saved_sync_storage.py -q
.venv/bin/ruff check src/openbiliclaw/saved_sync/extension_broker.py src/openbiliclaw/storage/database.py tests/test_extension_native_save_broker.py
.venv/bin/mypy src/openbiliclaw/saved_sync/extension_broker.py
```

Expected: all PASS.

```bash
git add src/openbiliclaw/saved_sync/extension_broker.py src/openbiliclaw/storage/database.py tests/test_extension_native_save_broker.py
git commit -m "feat: add durable extension native save broker"
```

---

### Task 2: Multiplex Native Jobs Through Exact Source Endpoints

**Files:**
- Modify: `src/openbiliclaw/api/models.py`
- Modify: `src/openbiliclaw/api/app.py:7660-7705,8453-8885`
- Create: `tests/test_extension_native_save_api.py`
- Modify: `tests/test_api_auth.py`

**Interfaces:**
- Produce HTTP task discriminator `type: "native_save"` with `id`, `item_key`, `platform`, `content_id`, `content_url`, `content_type`, `requested_action`, `resolved_action`, and `target_label`.
- Accept result fields `task_id`, `item_key`, `status`, `error_code`, `error_message`.
- Add X endpoints `/api/sources/x/next-task`, `/api/sources/x/task-result`, and `/api/sources/x/kick`.

- [ ] **Step 1: Write RED endpoint multiplexing tests**

```python
def test_reddit_next_task_serves_native_job_without_breaking_discovery(client, broker) -> None:
    native_id = enqueue_native_job(broker, "reddit", "reddit:t3_abc")
    response = client.get("/api/sources/reddit/next-task")
    assert response.json() == {
        "id": native_id,
        "type": "native_save",
        "item_key": "reddit:t3_abc",
        "platform": "reddit",
        "platform_slug": "reddit",
        "content_id": "t3_abc",
        "content_url": "https://www.reddit.com/r/test/comments/abc/demo/",
        "content_type": "post",
        "requested_action": "favorite",
        "resolved_action": "favorite",
        "target_label": "Reddit Saved",
    }


def test_native_result_cannot_fall_through_to_discovery_queue(client, broker) -> None:
    job = claim_native_job(broker, "zhihu", "zhihu:answer:123")
    response = client.post("/api/sources/zhihu/task-result", json={
        "task_id": job.job_id,
        "item_key": job.item_key,
        "status": "already_synced",
        "error_code": "",
        "error_message": "",
    })
    assert response.json() == {"ok": True}
```

Cover all six slugs, native-job priority, 204, an existing discovery task still round-tripping unchanged, wrong item key -> 409, unknown native job -> existing handler only when its queue owns the ID, malformed result -> 422, late callback -> 409, and remote unauthenticated POST rejection for X plus existing slugs.

- [ ] **Step 2: Run and verify X route/native discrimination failures**

Run: `.venv/bin/pytest tests/test_extension_native_save_api.py tests/test_api_auth.py -q`

Expected: FAIL because X routes and native result discrimination are absent.

- [ ] **Step 3: Add shared route-local helpers and preserve existing branches**

In `create_app()`, add `_claim_extension_native_task(slug: str)`,
`_is_extension_native_job(task_id: str)`,
`_submit_extension_native_result(payload: dict[str, Any])`, and
`_kick_source_task(slug: str)` helpers. The first three delegate only to the
stable broker; the kick helper publishes one `${slug}_task_available` event
and returns `{"ok": True}` even when no runtime-stream client is connected.

Each existing `next-task` checks the broker first, then calls its unchanged queue. Each `task-result` checks broker ownership by `task_id`; owned IDs never enter legacy merge/fail code. Add only the X variant with no legacy queue. Kick events use `${slug}_task_available` and remain best effort.

- [ ] **Step 4: Run focused and regression tests, then commit**

```bash
.venv/bin/pytest tests/test_extension_native_save_api.py tests/test_api_auth.py tests/test_bili_extension_e2e_harness.py -q
.venv/bin/ruff check src/openbiliclaw/api/app.py src/openbiliclaw/api/models.py tests/test_extension_native_save_api.py
.venv/bin/mypy src/openbiliclaw/api
git add src/openbiliclaw/api/app.py src/openbiliclaw/api/models.py tests/test_extension_native_save_api.py tests/test_api_auth.py
git commit -m "feat: multiplex native saves through source tasks"
```

Expected: all PASS; Bilibili routes remain unchanged.

---

### Task 3: Six Extension-Backed Adapters And Legacy Eligibility

**Files:**
- Create: `src/openbiliclaw/saved_sync/adapters/extension.py`
- Modify: `src/openbiliclaw/saved_sync/adapters/__init__.py`
- Modify: `src/openbiliclaw/saved_sync/service.py:274-310`
- Modify: `src/openbiliclaw/storage/database.py:8180-8390`
- Modify: `src/openbiliclaw/api/runtime_context.py:245-305,380-430`
- Create: `tests/test_saved_sync_extension_adapters.py`
- Modify: `tests/test_saved_sync_service.py`
- Modify: `tests/test_saved_sync_storage.py`
- Modify: `tests/test_saved_sync_api.py`

**Exact adapter matrix:**

| platform | slug | favorite target | native watch later | named collection |
| --- | --- | --- | --- | --- |
| `youtube` | `yt` | `OpenBiliClaw` | `YouTube Watch Later` | yes |
| `xiaohongshu` | `xhs` | `小红书收藏` | no; favorite fallback | no |
| `douyin` | `dy` | `抖音收藏` | no; favorite fallback | no |
| `twitter` | `x` | `X Bookmarks` | no; favorite fallback | no |
| `zhihu` | `zhihu` | `知乎收藏` | no; favorite fallback | no |
| `reddit` | `reddit` | `Reddit Saved` | no; favorite fallback | no |

- [ ] **Step 1: Write RED matrix, broker delegation, and migration tests**

```python
@pytest.mark.parametrize(("platform", "intent", "resolved", "target"), [
    ("youtube", "favorite", "favorite", "OpenBiliClaw"),
    ("youtube", "watch_later", "watch_later", "YouTube Watch Later"),
    ("twitter", "watch_later", "favorite", "X Bookmarks"),
    ("zhihu", "watch_later", "favorite", "知乎收藏"),
    ("reddit", "favorite", "favorite", "Reddit Saved"),
])
def test_extension_adapter_route_matrix(platform, intent, resolved, target, broker) -> None:
    router = NativeSaveRouter(build_extension_native_save_adapters(broker))
    adapter, route = router.route(platform, intent)
    assert adapter.capability.requires_extension is True
    assert route.resolved_action == resolved
    assert route.resolved_target == target


def test_pre_adapter_unsupported_becomes_retryable_after_registration(database) -> None:
    seed_native_state(database, status="unsupported", error_code="unsupported")
    database.migrate_legacy_native_save_unsupported()
    row = get_state(database)
    assert row["last_error_code"] == "unsupported_adapter_missing"
    assert create_manual_snapshot(database, row["item_key"])["status"] == "pending"
```

Also prove `unsupported_content_type` remains terminal, explicit manual retry of a missing-adapter row is live, bulk selection includes it once, runtime hot reload keeps the stable broker but replaces the service/router, and Bilibili remains the direct adapter.

- [ ] **Step 2: Run tests and confirm missing adapters/migration failure**

Run: `.venv/bin/pytest tests/test_saved_sync_extension_adapters.py tests/test_saved_sync_service.py tests/test_saved_sync_storage.py tests/test_saved_sync_api.py -q`

Expected: FAIL on missing adapter factory and legacy eligibility.

- [ ] **Step 3: Implement one typed adapter plus six explicit definitions**

```python
@dataclass(frozen=True, slots=True)
class ExtensionAdapterDefinition:
    platform: str
    platform_slug: str
    favorite_target: str
    watch_later_target: str = ""
    supports_named_collection: bool = False


class ExtensionNativeSaveAdapter:
    @property
    def capability(self) -> NativeSaveCapability:
        return NativeSaveCapability(
            platform=self._definition.platform,
            supports_favorite=True,
            supports_watch_later=bool(self._definition.watch_later_target),
            supports_named_collection=self._definition.supports_named_collection,
            requires_extension=True,
        )

    def target_label(self, action: NativeSaveAction) -> str:
        if action == "watch_later" and self._definition.watch_later_target:
            return self._definition.watch_later_target
        return self._definition.favorite_target

    async def save(self, item: SavedItemInput, route: NativeSaveRoute) -> NativeSaveResult:
        return await self._broker.save(item, route)
```

Export `build_extension_native_save_adapters(broker)`, returning the six adapters in the table's order as an immutable tuple. Validate that the item platform matches the definition before enqueue.

- [ ] **Step 4: Distinguish router absence from content limitations**

Change the service's router-missing result to `unsupported/unsupported_adapter_missing`. Make snapshot eligibility conditional on that exact error code, not on `unsupported` status alone. Add an idempotent named migration that rewrites only historical rows for the six new canonical platforms where `status='unsupported' AND last_error_code IN ('', 'unsupported')` to `unsupported_adapter_missing`; Bilibili, unknown platforms, and executor-returned `unsupported_content_type` rows are never rewritten.

- [ ] **Step 5: Register the stable broker and adapters across runtime rebuilds**

Add `extension_native_save_broker` as a stable `RuntimeContext` component. Build it once from the stable database/event hub, register its six adapters in degraded/local construction and on every config rebuild, and append `BilibiliNativeSaveAdapter` only when the Bilibili client is available. Broker wake-up publishes through the existing event hub.

- [ ] **Step 6: Verify and commit**

```bash
.venv/bin/pytest tests/test_saved_sync_extension_adapters.py tests/test_saved_sync_service.py tests/test_saved_sync_storage.py tests/test_saved_sync_api.py -q
.venv/bin/ruff check src/openbiliclaw/saved_sync src/openbiliclaw/api/runtime_context.py src/openbiliclaw/storage/database.py tests/test_saved_sync_extension_adapters.py
.venv/bin/mypy src/openbiliclaw/saved_sync src/openbiliclaw/api/runtime_context.py
git add src/openbiliclaw/saved_sync src/openbiliclaw/storage/database.py src/openbiliclaw/api/runtime_context.py tests/test_saved_sync_extension_adapters.py tests/test_saved_sync_service.py tests/test_saved_sync_storage.py tests/test_saved_sync_api.py
git commit -m "feat: register six extension native save adapters"
```

Expected: all PASS.

---

### Task 4: Shared Extension Contract And Browser Task Runner

**Files:**
- Create: `extension/src/shared/native-save.ts`
- Create: `extension/src/background/native-save-task-runner.ts`
- Create: `extension/src/content/native-save/runtime.ts`
- Create: `extension/tests/native-save-shared.test.ts`
- Create: `extension/tests/native-save-task-runner.test.ts`
- Modify: `extension/tests/helpers/chrome-mock.ts`

**Interfaces:**
- Produce `NativeSaveTask`, `NativeSaveResult`, `NativeSaveStatus`, `isNativeSaveTask()`, and `sanitizeNativeSaveResult()`.
- Produce `runNativeSaveTask(task, platformSlug, postResult) -> Promise<void>` and `handleNativeSaveContentResult(message) -> boolean`.
- Produce `installNativeSaveExecutor(platform, executor) -> void`.

- [ ] **Step 1: Write RED pure-contract and runner tests**

```typescript
test("accepts only correlated sanitized native-save tasks", () => {
  assert.equal(isNativeSaveTask(validTask), true);
  assert.equal(isNativeSaveTask({ ...validTask, content_url: "javascript:alert(1)" }), false);
  assert.equal(isNativeSaveTask({ ...validTask, platform: "reddit", platform_slug: "x" }), false);
});

test("runner opens the exact content URL and forwards one result", async () => {
  await runNativeSaveTask(task, "reddit", postResult);
  assert.equal(chrome.tabs.created[0].url, task.content_url);
  emitContentResult({ task_id: task.id, item_key: task.item_key, status: "synced" });
  assert.equal(postResult.mock.calls.length, 1);
});
```

Cover login/unsupported/rate-limit mappings, mismatched source tab, mismatched ID/key, duplicate result, content-script readiness retry, tab close, timeout, mutex release, and safe message truncation/redaction.

- [ ] **Step 2: Run and verify missing modules**

Run: `cd extension && node --test --experimental-strip-types --test-name-pattern='native save' tests/*.test.ts`

Expected: FAIL with module-not-found errors.

- [ ] **Step 3: Implement discriminated contracts and common content runtime**

```typescript
export type NativeSaveStatus =
  | "synced" | "already_synced" | "login_required"
  | "rate_limited" | "unsupported" | "failed";

export interface NativeSaveTask {
  id: string;
  type: "native_save";
  platform: NativeSavePlatform;
  platform_slug: NativeSaveSlug;
  item_key: string;
  content_id: string;
  content_url: string;
  content_type: string;
  requested_action: "favorite" | "watch_later";
  resolved_action: "favorite" | "watch_later";
  target_label: string;
}
```

The content runtime accepts only `NATIVE_SAVE_EXECUTE`, checks `location.hostname` against the task platform, executes once per task ID, and emits `NATIVE_SAVE_RESULT` with the same `task_id` and `item_key`.

- [ ] **Step 4: Implement the active-tab runner**

Acquire the existing dispatcher mutex, create an active tab at the allow-listed job URL, wait for completion, retry the execute message until the content listener responds, enforce one terminal result, POST through the caller's `authenticatedFetch` closure, close the task tab, and release the mutex in `finally`. Timeout reports `failed/native_save_timeout`; it never retries the mutation.

- [ ] **Step 5: Verify extension base and commit**

```bash
cd extension
node --test --experimental-strip-types --test-name-pattern='native save' tests/*.test.ts
npm run typecheck
npm run build
git add src/shared/native-save.ts src/background/native-save-task-runner.ts src/content/native-save/runtime.ts tests/native-save-shared.test.ts tests/native-save-task-runner.test.ts tests/helpers/chrome-mock.ts
git commit -m "feat: add extension native save task runtime"
```

Expected: all PASS and production bundles contain no separate unauthenticated backend fetch.

---

### Task 5: Reddit Saved And X Bookmarks

**Files:**
- Create: `extension/src/content/native-save/reddit.ts`
- Create: `extension/src/content/native-save/x.ts`
- Create: `extension/src/background/x-task-dispatcher.ts`
- Modify: `extension/src/content/reddit.ts`
- Modify: `extension/src/content/x.ts`
- Modify: `extension/src/background/reddit-task-dispatcher.ts`
- Modify: `extension/src/background/service-worker.ts:210-270,504-515`
- Create: `extension/tests/reddit-native-save.test.ts`
- Create: `extension/tests/x-native-save.test.ts`
- Modify: `extension/tests/reddit-task-dispatcher.test.ts`
- Create: `extension/tests/x-task-dispatcher.test.ts`
- Modify: `extension/tests/service-worker-stream.test.ts`

- [ ] **Step 1: Write RED executor fixtures and dispatcher-union tests**

Reddit fixtures cover post `t3_*` and comment `t1_*`, logged-out redirect, already-Saved control, `/api/save` success, 429, unsupported subreddit/user identity, request rejection followed by visible Save control, and missing confirmation.

X fixtures cover status IDs, logged-out state, `data-testid="bookmark"`, `data-testid="removeBookmark"`, rate-limit/risk-control response, unsupported user/list identity, and missing post-action confirmation.

```typescript
test("watch-later fallback uses the same Reddit Saved mutation", async () => {
  const result = await saveReddit({ ...task, requested_action: "watch_later",
    resolved_action: "favorite", target_label: "Reddit Saved" }, env);
  assert.equal(result.status, "synced");
  assert.equal(env.saveRequests.length, 1);
});
```

- [ ] **Step 2: Run and verify RED**

```bash
cd extension
node --test --experimental-strip-types --test-name-pattern='Reddit native save|X native save|native_save union' tests/*.test.ts
```

Expected: FAIL because executors and X dispatcher are absent.

- [ ] **Step 3: Implement Reddit idempotent save**

Extract Reddit fullname from the canonical ID/URL. Detect login first. Prefer same-origin `POST /api/save` only when the page exposes the required request token through an existing DOM/form value; send `credentials: "include"` and form encoding. If that stable request prerequisite is absent or rejected before a confirmed write, use the visible Save control. Re-query for the Unsave state to confirm. Map 429 to `rate_limited`, supported-ID rejection to `failed`, and non-post/comment to `unsupported/unsupported_content_type`.

- [ ] **Step 4: Implement X visible-control bookmark**

Validate a numeric tweet ID and navigate only to `/i/status/<id>` or the canonical status URL. Return `already_synced` when `removeBookmark` is present. Click the exact tweet's `bookmark` control and wait for `removeBookmark`; do not depend on a static GraphQL query ID. Logged-out pages return `login_required`; explicit rate-limit UI returns `rate_limited`; other identity types return `unsupported_content_type`.

- [ ] **Step 5: Wire dispatchers and service-worker kick handling**

Extend Reddit's task union with `NativeSaveTask` before legacy validation/execution. Add X polling at `/sources/x/next-task`, result POST, alarm, and `x_task_available` immediate wake. Both delegate `native_save` to the shared runner; legacy Reddit execution stays byte-for-byte behaviorally compatible.

- [ ] **Step 6: Verify and commit**

```bash
cd extension
node --test --experimental-strip-types --test-name-pattern='Reddit native save|X native save|reddit task|x task|service worker stream' tests/*.test.ts
npm run typecheck
npm run build
git add src/content/native-save/reddit.ts src/content/native-save/x.ts src/background/x-task-dispatcher.ts src/content/reddit.ts src/content/x.ts src/background/reddit-task-dispatcher.ts src/background/service-worker.ts tests/reddit-native-save.test.ts tests/x-native-save.test.ts tests/reddit-task-dispatcher.test.ts tests/x-task-dispatcher.test.ts tests/service-worker-stream.test.ts
git commit -m "feat: sync Reddit Saved and X Bookmarks"
```

Expected: all PASS.

---

### Task 6: YouTube Playlist And Native Watch Later

**Files:**
- Create: `extension/src/content/native-save/youtube.ts`
- Modify: `extension/src/content/youtube.ts`
- Modify: `extension/src/background/yt-task-dispatcher.ts`
- Create: `extension/tests/youtube-native-save.test.ts`
- Create: `extension/tests/yt-task-dispatcher.test.ts`

- [ ] **Step 1: Write RED YouTube dialog fixtures**

Cover video IDs from watch/shorts URLs, signed-out avatar/menu state, exact `OpenBiliClaw` playlist present/absent, same-name case mismatch, creation then re-query, existing membership, native Watch Later checkbox, unavailable/private video, and quota/rate-limit toast.

```typescript
test("favorite creates exact OpenBiliClaw playlist then confirms membership", async () => {
  const result = await saveYouTube(favoriteTask, fixtureWithNoNamedPlaylist());
  assert.equal(result.status, "synced");
  assert.deepEqual(actions, ["open-save-dialog", "new-playlist", "OpenBiliClaw", "confirm"]);
});

test("watch later never routes through OpenBiliClaw playlist", async () => {
  const result = await saveYouTube(watchLaterTask, fixtureWithWatchLater());
  assert.equal(result.status, "already_synced");
  assert.equal(namedPlaylistLookups, 0);
});
```

- [ ] **Step 2: Run and verify RED**

Run: `cd extension && node --test --experimental-strip-types --test-name-pattern='YouTube native save|yt task' tests/*.test.ts`

Expected: FAIL because `youtube.ts` native executor and union branch are absent.

- [ ] **Step 3: Implement exact-target dialog state machine**

Open the visible Save dialog using stable renderer attributes plus localized accessible-label candidates. For favorite, match playlist title by exact Unicode string `OpenBiliClaw`; if absent, create it, close/reopen the dialog, and re-query before selecting. For watch later, select only the platform Watch Later row. Confirm checked membership after action; return `already_synced` if checked before action. Never create or select a fallback playlist on creation failure.

- [ ] **Step 4: Wire the YT dispatcher union and verify**

`isValidYtTask` accepts the existing `bootstrap_profile` shape or `isNativeSaveTask`. `executeTask` delegates native tasks before reading bootstrap-only fields. The native branch uses the existing authenticated result POST.

```bash
cd extension
node --test --experimental-strip-types --test-name-pattern='YouTube native save|yt task' tests/*.test.ts
npm run typecheck
npm run build
git add src/content/native-save/youtube.ts src/content/youtube.ts src/background/yt-task-dispatcher.ts tests/youtube-native-save.test.ts tests/yt-task-dispatcher.test.ts
git commit -m "feat: sync YouTube saved targets"
```

Expected: all PASS.

---

### Task 7: Xiaohongshu And Douyin Native Favorites

**Files:**
- Create: `extension/src/content/native-save/xiaohongshu.ts`
- Create: `extension/src/content/native-save/douyin.ts`
- Modify: `extension/src/content/xiaohongshu.ts`
- Modify: `extension/src/content/douyin.ts`
- Modify: `extension/src/background/xhs-task-dispatcher.ts`
- Modify: `extension/src/background/dy-task-dispatcher.ts`
- Create: `extension/tests/xhs-native-save.test.ts`
- Create: `extension/tests/dy-native-save.test.ts`
- Modify: `extension/tests/xhs-task-dispatcher.test.ts`
- Modify: `extension/tests/dy-task-dispatcher.test.ts`

- [ ] **Step 1: Write RED platform fixtures**

For each platform cover canonical content-ID extraction, login modal, unsaved/saved controls, request-confirmed success where a stable page request contract is observed, visible-control fallback, duplicate save, risk-control/429, deleted content, unsupported creator/profile identity, and watch-later resolved to favorite.

- [ ] **Step 2: Run and verify RED**

Run: `cd extension && node --test --experimental-strip-types --test-name-pattern='XHS native save|Douyin native save|xhs task|dy task' tests/*.test.ts`

Expected: FAIL because native executors/unions are absent.

- [ ] **Step 3: Implement Xiaohongshu favorite confirmation**

Accept only note/video identities supported by the platform page. Detect a logged-out login overlay before mutation. Use the existing token/state bridge only inside the page and never copy a token into a result. Prefer a same-origin collection mutation only when the live page exposes its complete stable request contract; otherwise click the note's 收藏 control. Confirm the control changes to 已收藏 (or equivalent selected state). Map risk-control UI/response to `rate_limited` and unsupported identity to `unsupported_content_type`.

- [ ] **Step 4: Implement Douyin favorite confirmation**

Accept only aweme/video IDs. Preserve the existing MAIN-world tap as observation-only. Use same-origin favorite mutation only when its full live request contract is available; otherwise click the exact video's 收藏 control. Confirm selected/count state without treating a count change alone as success. Login overlay -> `login_required`; risk-control -> `rate_limited`; profile/creator IDs -> `unsupported_content_type`.

- [ ] **Step 5: Wire both dispatcher unions and verify**

Branch on `isNativeSaveTask` before bootstrap/search-specific timeout, scope, or payload parsing. Both native branches use the shared runner and their existing authenticated result POST; existing debug/bootstrap behavior is unchanged.

```bash
cd extension
node --test --experimental-strip-types --test-name-pattern='XHS native save|Douyin native save|xhs task|dy task' tests/*.test.ts
npm run typecheck
npm run build
git add src/content/native-save/xiaohongshu.ts src/content/native-save/douyin.ts src/content/xiaohongshu.ts src/content/douyin.ts src/background/xhs-task-dispatcher.ts src/background/dy-task-dispatcher.ts tests/xhs-native-save.test.ts tests/dy-native-save.test.ts tests/xhs-task-dispatcher.test.ts tests/dy-task-dispatcher.test.ts
git commit -m "feat: sync Xiaohongshu and Douyin favorites"
```

Expected: all PASS.

---

### Task 8: Zhihu Exact `OpenBiliClaw` Collection

**Files:**
- Create: `extension/src/content/native-save/zhihu.ts`
- Modify: `extension/src/content/zhihu.ts`
- Modify: `extension/src/background/zhihu-task-dispatcher.ts`
- Create: `extension/tests/zhihu-native-save.test.ts`
- Modify: `extension/tests/zhihu-task-dispatcher.test.ts`

- [ ] **Step 1: Write RED typed-identity and collection fixtures**

Cover `question:<id>`, `answer:<id>`, and `article:<id>`; wrong extra-colon identities; logged out; exact collection already containing item; exact collection absent; collection creation plus re-query; same-name case mismatch; create failure; 429/risk control; and both intents resolving to the same collection.

- [ ] **Step 2: Run and verify RED**

Run: `cd extension && node --test --experimental-strip-types --test-name-pattern='Zhihu native save|zhihu task' tests/*.test.ts`

Expected: FAIL because the native executor/union is absent.

- [ ] **Step 3: Implement exact collection state machine**

Parse the typed identity without weakening the backend's fail-closed colon validation. Open the platform collection dialog for the actual question/answer/article control. Match exact title `OpenBiliClaw`; create it if absent, then close/reopen and re-query before selecting. Confirm membership, return `already_synced` for an existing check, and never write another collection after a create/re-query mismatch. Use safe status/error mappings only.

- [ ] **Step 4: Wire dispatcher union, verify, and commit**

```bash
cd extension
node --test --experimental-strip-types --test-name-pattern='Zhihu native save|zhihu task' tests/*.test.ts
npm run typecheck
npm run build
git add src/content/native-save/zhihu.ts src/content/zhihu.ts src/background/zhihu-task-dispatcher.ts tests/zhihu-native-save.test.ts tests/zhihu-task-dispatcher.test.ts
git commit -m "feat: sync Zhihu OpenBiliClaw collection"
```

Expected: all PASS.

---

### Task 9: Truthful Saved-State UX On All Graphical Surfaces

**Files:**
- Modify: `extension/popup/popup.js`
- Modify: `extension/tests/popup-saved-sync.test.ts`
- Modify: `extension/tests/popup-saved-surfaces-e2e.test.ts`
- Modify: `src/openbiliclaw/web/desktop/assets/js/saved-sync-core.js`
- Modify: `src/openbiliclaw/web/desktop/assets/js/app.js`
- Modify: `src/openbiliclaw/web/js/app.js`
- Modify: `tests/test_saved_sync_frontend_contract.py`
- Modify: `tests/test_mobile_web_view_models.py`
- Modify: `tests/test_docs_saved_sync.py`

- [ ] **Step 1: Write RED surface-contract tests**

Assert all six platforms render manual sync controls rather than permanent “仅本地保存”; `extension_required` renders connection guidance and retry; `unsupported_content_type` alone renders local-only/no invalid sync button; pending/syncing disable duplicate submission; successful fallback shows its truthful resolved target; and auto-sync setting remains off unless explicitly enabled.

- [ ] **Step 2: Run and verify old unsupported copy fails**

```bash
.venv/bin/pytest tests/test_saved_sync_frontend_contract.py tests/test_mobile_web_view_models.py tests/test_docs_saved_sync.py -q
cd extension && node --test --experimental-strip-types --test-name-pattern='saved sync|saved surfaces' tests/*.test.ts
```

Expected: at least one FAIL because non-Bilibili platforms are still treated as adapterless/local-only.

- [ ] **Step 3: Update shared state interpretation, not platform routing**

Render from backend `sync_status`, `resolved_target`, and `error_code`. Do not duplicate the adapter matrix in UI code. Treat `unsupported_adapter_missing` as retryable only during rolling-upgrade compatibility; once runtime adapters are present normal rows use pending/extension-required. Treat `unsupported_content_type` as local-only.

- [ ] **Step 4: Verify and commit**

```bash
.venv/bin/pytest tests/test_saved_sync_frontend_contract.py tests/test_mobile_web_view_models.py tests/test_docs_saved_sync.py -q
cd extension && node --test --experimental-strip-types --test-name-pattern='saved sync|saved surfaces' tests/*.test.ts
git add extension/popup/popup.js extension/tests/popup-saved-sync.test.ts extension/tests/popup-saved-surfaces-e2e.test.ts src/openbiliclaw/web/desktop/assets/js/saved-sync-core.js src/openbiliclaw/web/desktop/assets/js/app.js src/openbiliclaw/web/js/app.js tests/test_saved_sync_frontend_contract.py tests/test_mobile_web_view_models.py tests/test_docs_saved_sync.py
git commit -m "feat: expose cross-platform native sync states"
```

Expected: all PASS.

---

### Task 10: Documentation, Architecture, And Safe E2E Harness

**Files:**
- Modify: `docs/modules/saved-sync.md`
- Modify: `docs/modules/extension.md`
- Modify: `docs/modules/config.md`
- Modify: `docs/platform-source-integration.md`
- Modify: `docs/architecture.md`
- Modify: `docs/spec.md`
- Modify: `README.md`
- Modify: `README_EN.md`
- Modify: `docs/changelog.md`
- Modify: `docs/index.md`
- Create: `docs/testing/six-platform-native-save-e2e.md`
- Create: `tests/test_six_platform_native_save_e2e_harness.py`
- Modify: `tests/test_docs_saved_sync.py`
- Modify: `extension/src/background/e2e-runner.ts`
- Modify: `extension/tests/e2e-runner.test.ts`

- [ ] **Step 1: Write RED documentation and authorization-harness tests**

Assert the six-row mapping, default-off setting, manual trigger, extension login source, no local-delete propagation, status/error semantics, broker flow in all required architecture diagrams, and the exact-title containers. Harness tests must prove state-changing native-save actions are rejected unless `allow_state_changing=true` and the request names platform, action, public content ID, and expected target.

- [ ] **Step 2: Run and verify missing coverage**

Run: `.venv/bin/pytest tests/test_docs_saved_sync.py tests/test_six_platform_native_save_e2e_harness.py -q`

Expected: FAIL on missing six-platform docs/harness.

- [ ] **Step 3: Add a non-secret, explicitly authorized E2E runbook/harness**

The harness records only:

```json
{
  "platform": "reddit",
  "action": "favorite",
  "content_id": "t3_public",
  "expected_target": "Reddit Saved",
  "task_status": "synced",
  "error_code": ""
}
```

Never record account identifiers, cookies, tokens, HTML, or response bodies. Each run validates: auto-sync off local save; manual favorite; manual watch-later mapping; auto-sync only after explicit toggle/consent; duplicate -> `already_synced`. Cleanup removes local memberships only and explicitly warns that platform records remain.

- [ ] **Step 4: Update mandatory documentation set**

Document public API/task contracts in `saved-sync.md`, extension executor/credential boundary in `extension.md`, unchanged default in `config.md`, platform integration checklist in `platform-source-integration.md`, and the broker/extension data path in all four required architecture locations (`docs/architecture.md`, `docs/spec.md`, README CN/EN). Add one PR bullet under the current changelog version; do not create a release header or version highlight.

- [ ] **Step 5: Verify and commit**

```bash
.venv/bin/pytest tests/test_docs_saved_sync.py tests/test_six_platform_native_save_e2e_harness.py -q
cd extension && node --test --experimental-strip-types --test-name-pattern='e2e runner' tests/*.test.ts
git add docs README.md README_EN.md tests/test_docs_saved_sync.py tests/test_six_platform_native_save_e2e_harness.py extension/src/background/e2e-runner.ts extension/tests/e2e-runner.test.ts
git commit -m "docs: document six-platform native save execution"
```

Expected: all PASS.

---

### Task 11: Authorized Real-Account Verification And Full Quality Gate

**Files:**
- Modify only if real verification exposes a defect: the smallest owning source/test/doc file from Tasks 1-10.
- Record safe results in: `docs/testing/six-platform-native-save-e2e.md` under a dated results section.

- [ ] **Step 1: Run the complete non-mutating quality gate first**

```bash
.venv/bin/ruff format --check src/ tests/
.venv/bin/ruff check src/ tests/
.venv/bin/mypy src/
.venv/bin/pytest
.venv/bin/pytest --cov=openbiliclaw
.venv/bin/openbiliclaw config-show
cd extension
npm test
npm run typecheck
npm run build
npm run build:firefox
```

Expected: all PASS; coverage remains at least 70%; `config-show` reports
`收藏自动同步  关闭` and does not expose a platform-account mutation command.

- [ ] **Step 2: Verify install-mode artifacts without publishing**

Run repository-provided git/editable, Docker, and desktop install smoke commands documented in `docs/agent-install.md` and `docs/docker-deployment.md`. Load the freshly built unpacked extension, not source TypeScript or a stale store build. Expected: daemon authentication succeeds and all six dispatchers receive 204 when idle.

- [ ] **Step 3: Obtain current named authorization before any account write**

For each platform, require an authorization record naming the selected public content ID, favorite/watch-later action, and expected target. If authorization is absent, stop that platform at local-only verification and report it as not yet real-write verified; never infer consent from prior tests.

- [ ] **Step 4: Execute the seven-platform matrix in the installed extension browser**

Verify Bilibili regression plus the six new platforms. For each new platform:

1. With auto-sync off, save locally and confirm no platform write/job mutation.
2. Trigger manual favorite and wait for terminal target/status.
3. Trigger manual watch later and verify native Watch Later only on YouTube, favorite fallback elsewhere.
4. Enable auto-sync with explicit consent, save a second named item, and verify terminal state.
5. Repeat a save and verify `already_synced` without a duplicate container/item.
6. Remove the local membership and verify the platform save remains.

Record only safe fields allowed by Task 10. Do not delete real platform saves unless the user separately authorizes cleanup.

- [ ] **Step 5: Repair any discovered defect with RED/GREEN evidence**

Before changing code, add the smallest fixture/test reproducing the real failure. Use `superpowers:systematic-debugging`; rerun the affected platform suite and then the full gates. Commit each repair separately with a diagnosis-specific Conventional Commit message.

- [ ] **Step 6: Final review and completion commit**

Run:

```bash
git diff --check
git status --short
.venv/bin/pytest
cd extension && npm test && npm run typecheck && npm run build
```

Then use `superpowers:requesting-code-review`, resolve verified findings with `superpowers:receiving-code-review`, rerun the full quality gate, and commit the safe dated E2E results:

```bash
git add docs/testing/six-platform-native-save-e2e.md
git commit -m "test: verify six-platform native saves"
```

Expected final state: only intentionally ignored local dependency links may remain untracked; all account-write claims are backed by current real terminal results.
