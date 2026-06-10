# X (Twitter) Source Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Each task is test-first with an atomic commit.

**Goal:** Add X (Twitter) as the sixth content source described in `docs/plans/2026-06-08-x-twitter-source-spec.md`: (A) passively capture the user's own X engagement as Soul behavior signal via an extension MAIN-world tap, and (B) discover X content through three server-side cookie-replay sources (search / For-You / account subscription) that flow into the existing unified candidate pool.

**Architecture:** X ≈ "Douyin direct, with `twitter-cli` instead of XBogus signing." Discovery runs server-side: harvested `auth_token`+`ct0` cookies → `XClient` (wraps `twitter-cli`) → normalize to `DiscoveredContent` → existing `discovery_candidates` pending pool → shared mixed-source evaluator → `content_cache`. Behavior capture runs in the browser: a MAIN-world GraphQL tap observes the user's own like/bookmark/reply mutations → `POST /api/events` (unchanged). The only cross-cutting model change is text-first content (`body_text` + reuse of the existing `content_type` shape field).

**Tech Stack:** Python 3.11+ / asyncio / SQLite / FastAPI / pydantic; `twitter-cli` (PyPI, Apache-2.0, optional extra `openbiliclaw[x]`); TypeScript Chrome MV3 extension (`node --test`); pytest / Ruff / MyPy.

---

## Source Spec

- Spec: `docs/plans/2026-06-08-x-twitter-source-spec.md`
- This plan implements that spec exactly. It does NOT re-open decisions already settled there (see Design Decisions). It does NOT change the unified candidate-pool architecture (`docs/plans/2026-06-04-unified-discovery-candidate-pool-spec.md`) — X is a new producer + a real adapter feeding the same pool.

## Design Decisions (locked by spec + its Codex review — do not regress)

- **Source identity:** internal key `"twitter"` everywhere (`source_platform` / `source_type` / config / `pool_source_shares`); display label `"X"` via `event_format._PLATFORM_LABELS`; API path prefix `/api/sources/x/`; cookie at `data/x_cookie.json` (env override `OPENBILICLAW_X_COOKIE`).
- **Discovery is server-side cookie replay** (real `XAdapter.fetch()`, like Bilibili/Douyin-direct — NOT an XHS-style stub). Behavior capture is extension-side, passive.
- **Content shape uses the existing `content_type` field, NOT a new `media_type`.** Add `content_type` to `DiscoveredContent` (default `"video"`; X uses `"tweet"`/`"thread"`). Fix BOTH hardcoded `"note" if xiaohongshu else "video"` paths (`candidate_pool.py:137` AND `engine.py:1274`).
- **`body_text` migrates through the real helpers** `_ensure_content_cache_multisource_columns()` and `_ensure_discovery_candidate_columns()` — never hand-written ALTER.
- **Event scoring (v1, zero global blast radius):** map like→`like`, bookmark→`favorite`, reply→`comment` (all already in `_EXPLICIT_POSITIVE_EVENT_TYPES`). retweet→`share` and follow→`follow` are captured as **context-tier** events only — **do NOT add them to `_EXPLICIT_POSITIVE_EVENT_TYPES` or `soul/pipeline._ENGAGEMENT_TYPES`** (that would silently change Bilibili/Douyin/YouTube follow scoring). `view`/`click` (opens) are context-only.
- **`twitter-cli` is an optional extra** `openbiliclaw[x] = ["twitter-cli>=0.8.5"]` (confirmed on PyPI, Apache-2.0, importable `twitter_cli`). **Lazy import**: never import `twitter_cli`/`curl_cffi` on the `enabled=false` path. Desktop builds include the extra (open sub-decision: always-bundle vs on-demand; default = always-bundle).
- **Prompt-cache convention:** `body_text` goes in the USER message only, deterministic `json.dumps(..., ensure_ascii=False, indent=2, sort_keys=True)`; system prompt stays byte-static; new builders join `test_prompt_builder_system_messages_are_call_invariant`.

---

### Task 1: B.0 Spike — Validate `twitter-cli` (no production code)

**Files:**
- Create (throwaway): `scratch/x_spike.py` (delete before merge; or keep under `.gitignore`)
- Update: `docs/plans/2026-06-08-x-twitter-source-spec.md` §8 (record findings)

This is exploratory, not TDD. It gates Tasks 6 & 8 only. Tasks 2–5 do not depend on it and should start in parallel.

**Step 1: Install and confirm import surface**

```bash
pip install "twitter-cli>=0.8.5"
python - <<'PY'
import twitter_cli, inspect
import twitter_cli.client as client  # confirm client/auth/graphql modules import
print("twitter_cli", getattr(twitter_cli, "__version__", "?"))
print([n for n in dir(client) if not n.startswith("_")])
PY
```

Record: can search / For-You timeline / user timeline be driven by **importing** `twitter_cli` modules (preferred), or only via the `twitter` CLI entry point (subprocess fallback)?

**Step 2: Cookie smoke (needs a real x.com `auth_token` + `ct0`)**

Drive search, For-You, and a user timeline with only the two cookies (via `TWITTER_AUTH_TOKEN`/`TWITTER_CT0` env or direct args). Capture one raw JSON response per call into `scratch/` fixtures (reused by Task 7 tests). Confirm `note_tweet` long-form, thread, retweet/quote nesting, and tombstones appear as documented.

**Step 3: Record decisions in spec §8**

- Canonical source confirmed (`jackwener/twitter-cli` vs `public-clis/twitter-cli`) and exact pinned version.
- `XClient` integration mode: in-process import (preferred) vs subprocess.
- The `curl_cffi` PyInstaller hook approach for desktop.

**Deliverable:** a short findings block appended to spec §8. No production code committed. Delete `scratch/x_spike.py` (keep only fixtures needed by tests, placed under `tests/fixtures/x/`).

---

### Task 2: Data Model — `body_text` + `content_type` Through The Pool

**Files:**
- Modify: `src/openbiliclaw/discovery/engine.py` (`DiscoveredContent`, `to_cache_kwargs`, the `engine.py:1274` candidate dict)
- Modify: `src/openbiliclaw/discovery/candidate_pool.py` (`discovered_content_to_candidate_write`)
- Modify: `src/openbiliclaw/storage/database.py` (`_ensure_content_cache_multisource_columns`, `_ensure_discovery_candidate_columns`, fresh schema, `cache_content`, enqueue/claim/admission/serialization)
- Test: `tests/test_discovery_candidate_store.py`, `tests/test_storage.py`

**Step 1: Write failing round-trip test**

In `tests/test_discovery_candidate_store.py`:

```python
def test_text_candidate_round_trips_body_text_and_content_type(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    item = DiscoveredContent(
        title="A thread on systems",
        content_id="1790000000000000001",
        content_url="https://x.com/handle/status/1790000000000000001",
        source_platform="twitter",
        source_strategy="search",
        author_name="@handle",
        content_type="thread",
        body_text="1/ long-form note_tweet body ...",
    )
    db.enqueue_discovery_candidates([discovered_content_to_candidate_write(item)])
    rows = db.claim_discovery_candidates_for_eval(limit=1)
    assert rows[0]["content_type"] == "thread"
    assert rows[0]["body_text"].startswith("1/ long-form")
    back = row_to_discovered_content(rows[0])
    assert back.content_type == "thread"
    assert back.body_text.startswith("1/ long-form")
```

In `tests/test_storage.py`, two asserts after `cache_content()` of a twitter item (`cache_content` is at `database.py:907`): (1) `content_cache` accepts and returns `body_text`/`content_type`; (2) **bvid compatibility (Codex R1 M5)** — the cached X row has `bvid == content_id` (non-Bilibili sources reuse `content_id` as `bvid`, per the unified-pool plan) and still round-trips through recommendation retrieval (`get_pool_candidates()` / serve path), so existing `bvid`-keyed joins don't drop it.

**Step 2: Run and verify failure**

```bash
pytest tests/test_discovery_candidate_store.py -k body_text -q
```

Expected: FAIL — `DiscoveredContent` has no `body_text`/`content_type`; columns missing.

**Step 3: Add dataclass fields**

In `DiscoveredContent` (`discovery/engine.py`):

```python
body_text: str = ""          # tweet/thread full text; empty for video sources
content_type: str = "video"  # shape: "video" | "note" | "tweet" | "thread"
```

Extend `to_cache_kwargs()` to pass `body_text` and `content_type`. Do not add bilibili fallbacks for these.

**Step 4: Fix BOTH content_type hardcodes**

- `discovery/candidate_pool.py:137` (`discovered_content_to_candidate_write`): `content_type=item.content_type or ("note" if platform == "xiaohongshu" else "video")`. Also carry `body_text=item.body_text` into the write + the `discovery_candidates` row.
- `discovery/engine.py:1274` (candidate dict): same `c.content_type or (...)` rule; include `body_text`.

**Step 5: Migrate columns via existing helpers**

In `storage/database.py`:
- `_ensure_content_cache_multisource_columns()`: also add `body_text TEXT DEFAULT ''` and `content_type TEXT DEFAULT 'video'`.
- `_ensure_discovery_candidate_columns()`: add `body_text TEXT DEFAULT ''` (the table already has `content_type`).
- Add both columns to the fresh `CREATE TABLE` schemas too.
- Thread `body_text`/`content_type` through `cache_content()`, `enqueue_discovery_candidates()`, `claim_discovery_candidates_for_eval()` SELECT, admission writer, and any `get_pool_candidates()`/API serialization that returns content rows.

**Step 6: Run focused tests**

```bash
pytest tests/test_discovery_candidate_store.py tests/test_storage.py -q
```

Expected: PASS.

**Step 7: Commit**

```bash
git add src/openbiliclaw/discovery/engine.py src/openbiliclaw/discovery/candidate_pool.py src/openbiliclaw/storage/database.py tests/test_discovery_candidate_store.py tests/test_storage.py
git commit -m "feat: thread body_text/content_type through discovery candidate pool"
```

---

### Task 3: Event Taxonomy For X (backend)

**Files:**
- Modify: `src/openbiliclaw/sources/event_format.py` (`SOURCE_TWITTER`, `_PLATFORM_LABELS`)
- Test: `tests/test_event_format.py` (or the existing event-format test module)

**Step 1: Write failing tests**

```python
def test_twitter_label_and_context_render() -> None:
    s = format_event_context(event_type="favorite", source_platform="twitter",
                             title="A thread on systems", author="@handle")
    assert s.startswith("在 X")  # _PLATFORM_LABELS["twitter"] == "X"

def test_twitter_engagement_scoring_v1() -> None:
    # like/bookmark(favorite)/reply(comment) are positive via existing set
    for et in ("like", "favorite", "comment"):
        cat, _ = classify_event_satisfaction({"event_type": et,
            "metadata": {"source_platform": "twitter"}})
        assert cat == "positive"
    # retweet(share)/follow are context-tier — NOT positive (no global change)
    for et in ("share", "follow"):
        cat, _ = classify_event_satisfaction({"event_type": et,
            "metadata": {"source_platform": "twitter"}})
        assert cat != "positive"
```

**Step 2: Run and verify failure**

```bash
pytest tests/test_event_format.py -k "twitter_label or twitter_engagement" -q
```

Expected: **one failing test** (label — no `"twitter"` label yet) **+ one passing regression guard** (scoring — already green; it exists to prove we never extend the global positive set). Only the label test drives Step 3.

**Step 3: Add the constant + label only**

```python
SOURCE_TWITTER = "twitter"
# ...
_PLATFORM_LABELS[SOURCE_TWITTER] = "X"
```

Do **not** touch `_EXPLICIT_POSITIVE_EVENT_TYPES`. Do **not** touch `soul/pipeline._ENGAGEMENT_TYPES`.

**Step 4: Run + commit**

```bash
pytest tests/test_event_format.py -q
git add src/openbiliclaw/sources/event_format.py tests/test_event_format.py
git commit -m "feat: register X (twitter) source label and event mapping"
```

---

### Task 4: Extension Behavior Capture (Part A)

**Files:**
- Modify: `extension/manifest.json`
- Create: `extension/src/shared/platforms/twitter.ts`
- Create: `extension/src/content/x.ts`
- Create: `extension/src/main/x-graphql-tap.ts`
- Test: `extension/tests/x-graphql-tap.test.ts`, `extension/tests/twitter-adapter.test.ts`
- Fixtures: `extension/tests/fixtures/x/*.json` (real GraphQL **mutation** request/response bodies for FavoriteTweet / CreateBookmark / CreateRetweet / reply CreateTweet / follow / TweetDetail — **captured in THIS task**: perform each action on x.com with devtools open, save the GraphQL request body + response. These write-action bodies are distinct from Task 1's read-API fixtures.)

**Step 1: Write failing extension tests** (`node --test`)

```ts
// classifyXResponseUrl matches by GraphQL operation name, not hashed queryId
assert.equal(classifyXResponseUrl("https://x.com/i/api/graphql/abc123/FavoriteTweet"), "like");
assert.equal(classifyXResponseUrl("https://x.com/i/api/graphql/zzz/CreateRetweet"), "share");
assert.equal(classifyXResponseUrl("https://x.com/i/api/graphql/q/CreateBookmark"), "favorite");
assert.equal(classifyXResponseUrl("https://x.com/i/api/graphql/q/HomeTimeline"), null); // not captured as engagement
// adapter: tweet url -> content_id
assert.equal(twitterAdapter.extractContentId("https://x.com/h/status/1790000000000000001"), "1790000000000000001");
assert.equal(twitterAdapter.sourcePlatform, "twitter");

// HARD PART (Codex R1 M3): extract target tweet_id + event from a captured GraphQL
// MUTATION, using fixtures of real request bodies / responses (not just URL parsing).
assert.deepEqual(parseXMutation(loadFixture("favorite_tweet.json")), {type: "like", tweet_id: "1790000000000000001"});
assert.equal(parseXMutation(loadFixture("create_bookmark.json")).type, "favorite");
assert.equal(parseXMutation(loadFixture("create_retweet.json")).type, "share");
assert.equal(parseXMutation(loadFixture("reply_create_tweet.json")).type, "comment");
assert.equal(parseXMutation(loadFixture("follow.json")).type, "follow");
assert.equal(parseXMutation(loadFixture("tweet_detail.json")).type, "view");
// pass-through: the tap must NOT mutate the page's outgoing request
assert.equal(tapDidMutateRequest, false);
```

**Step 2: Run and verify failure**

```bash
cd extension && npm run build && node --test dist/tests/x-graphql-tap.test.js
```

**Step 3: Implement the platform adapter** (`shared/platforms/twitter.ts`, mirror `xiaohongshu.ts`)

`sourcePlatform="twitter"`; `extractContentId` via `/status/(\d+)/`; page-type detection (home/status/profile/search); card + search-input selectors; `inferActionType`; `buildEventMetadata` → `{tweet_id}`.

**Step 4: Implement the MAIN-world tap** (`main/x-graphql-tap.ts`, mirror `dy-fetch-tap.ts` + `xhs-token-sniffer.ts`)

Wrap `window.fetch` and `XMLHttpRequest`; `classifyXResponseUrl(url)` matches operation name in `/i/api/graphql/<id>/<OperationName>`. Capture engagement mutations only (`FavoriteTweet→like`, `CreateRetweet→share`, `CreateTweet`+`in_reply_to→comment`, `CreateBookmark→favorite`, follow→`follow`) plus `TweetDetail→view`. Extract target tweet id from request variables / response; `postMessage({source:"obc-x-tap", ...})`. Depth-first JSON walk for resilience; never mutate requests.

**Step 5: Wire the content script** (`content/x.ts`)

`startCollector(twitterAdapter)` + listen for the tap's `postMessage` and forward as `BEHAVIOR_EVENT` (mirror `content/xiaohongshu.ts`).

**Step 6: Update manifest**

Add `*://*.x.com/*` and `*://*.twitter.com/*` to `host_permissions`; add two content_scripts: `dist/content/x.js` (`document_idle`) and `dist/main/x-graphql-tap.js` (`world: MAIN`, `document_start`).

**Step 7: Run + typecheck + commit**

```bash
cd extension && npm run typecheck && npm run build && node --test
git add extension/manifest.json extension/src/shared/platforms/twitter.ts extension/src/content/x.ts extension/src/main/x-graphql-tap.ts extension/tests/x-graphql-tap.test.ts extension/tests/twitter-adapter.test.ts extension/tests/fixtures/x/
git commit -m "feat(extension): capture X engagement via MAIN-world GraphQL tap"
```

---

### Task 5: Cookie Bridge (P1)

**Files:**
- Modify: `extension/src/background/cookie-sync.ts`
- Modify: `src/openbiliclaw/api/app.py` (`POST /api/sources/x/cookie`)
- Modify: `src/openbiliclaw/api/models.py` (`XCookieIn` / `XCookieResponse`)
- Test: `tests/test_api_x_cookie.py`, `extension/tests/cookie-sync.test.ts`

**Step 1: Write failing tests**

Backend: posting a cookie header containing `auth_token` + `ct0` persists `data/x_cookie.json` and returns `{ok: true, has_cookie: true}`; posting without `ct0` returns `has_cookie: false`. Extension: `readXCookieHeader()` returns null unless both `auth_token` and `ct0` are present for `x.com`. **Regression (Codex R1 minor):** also assert the existing Bilibili and Douyin cookie sync (read + sync) behave unchanged after the X branch is added — the shared `onChanged`/startup/hourly paths still fire for those domains.

**Step 2: Run and verify failure**

```bash
pytest tests/test_api_x_cookie.py -q
```

**Step 3: Implement** (mirror the douyin cookie path)

- `cookie-sync.ts`: `readXCookieHeader()` (require `auth_token`+`ct0` on `x.com`), `syncXCookieToBackend()` → `POST /api/sources/x/cookie`; hook `x.com` into `onChanged`/startup/hourly alarm (alongside bilibili/douyin).
- `api/models.py`: `XCookieIn{cookie, source}` / `XCookieResponse{ok, has_cookie, error_code?, message?}` (mirror `DouyinCookieIn/Response`).
- `api/app.py`: `POST /api/sources/x/cookie` persists to `data/x_cookie.json`; env `OPENBILICLAW_X_COOKIE` takes precedence on read.

**Step 4: Run + commit**

```bash
pytest tests/test_api_x_cookie.py -q && (cd extension && node --test)
git add src/openbiliclaw/api/app.py src/openbiliclaw/api/models.py extension/src/background/cookie-sync.ts tests/test_api_x_cookie.py extension/tests/cookie-sync.test.ts
git commit -m "feat: auto-sync x.com auth cookies to backend"
```

---

### Task 6: `XClient` + `openbiliclaw[x]` Dependency Extra (server-side, lazy-imported)

**Files:**
- Create: `src/openbiliclaw/sources/x_client.py`
- Modify: `pyproject.toml` (`[project.optional-dependencies] x = ["twitter-cli>=0.8.5"]`)
- Test: `tests/test_x_client.py`, `tests/test_packaging_extra.py`

Depends on Task 1 (integration mode + pinned version confirmed).

**Step 0: Add the optional extra (Codex R1 M2 — no other task adds the dependency)**

In `pyproject.toml`, add under `[project.optional-dependencies]`:

```toml
x = ["twitter-cli>=0.8.5"]
```

Add a metadata test (`tests/test_packaging_extra.py`) asserting the `x` extra is declared and pins `twitter-cli` (read `importlib.metadata.metadata("openbiliclaw")` Provides-Extra / Requires-Dist, or parse `pyproject.toml`). This guarantees the dependency exists for `XClient` to import. Desktop PyInstaller wiring for this extra is handled in Task 13.

**Step 1: Write failing tests** (mock `twitter_cli`; never hit the network)

```python
def test_xclient_disabled_path_does_not_import_twitter_cli(monkeypatch) -> None:
    # importing the module must not import twitter_cli at module load
    import importlib, sys
    sys.modules.pop("twitter_cli", None)
    importlib.import_module("openbiliclaw.sources.x_client")
    assert "twitter_cli" not in sys.modules  # lazy

@pytest.mark.asyncio
async def test_xclient_search_normalizes(monkeypatch) -> None:
    client = XClient(cookie="auth_token=a; ct0=b")
    monkeypatch.setattr(client, "_raw_search", _fake_raw_search)  # seam
    out = await client.search("rust async", limit=5)
    assert out and all("rest_id" in t for t in out)
```

**Step 2: Run + verify failure**

```bash
pytest tests/test_x_client.py -q
```

**Step 3: Implement `XClient`** (per spec §4.3)

```python
class XClient:
    def __init__(self, cookie: str) -> None: self._cookie = cookie  # parse auth_token/ct0 lazily
    async def search(self, query: str, *, limit: int, product: str = "Top") -> list[dict]: ...
    async def for_you(self, *, limit: int) -> list[dict]: ...
    async def user_tweets(self, handle: str, *, limit: int) -> list[dict]: ...
```

`twitter_cli` (and `curl_cffi`) imported **inside** the call methods, not at module top. Cookie injected via `TWITTER_AUTH_TOKEN`/`TWITTER_CT0` env or direct args. Prefer in-process import; subprocess fallback only if Task 1 found the client un-importable. Return raw tweet dicts (normalization is Task 7). **Raise typed errors** so Task 10's source-health machine can map them to distinct health states: `XMissingCookieError` (no cookie — raised before any import) → `missing_cookie`, `XAuthError` (401) → `expired_cookie`, `XBlockedError` (403) → `blocked`, `XRateLimitError` (429) → `rate_limited`.

**Step 4: Run + commit**

```bash
pytest tests/test_x_client.py tests/test_packaging_extra.py -q
git add src/openbiliclaw/sources/x_client.py pyproject.toml tests/test_x_client.py tests/test_packaging_extra.py
git commit -m "feat: add lazy-imported XClient + openbiliclaw[x] extra"
```

---

### Task 7: Tweet Normalization

**Files:**
- Create: `src/openbiliclaw/discovery/x_normalize.py`
- Create: `tests/test_x_normalize.py` (+ fixtures from Task 1 under `tests/fixtures/x/`)

**Step 1: Write failing fixture tests**

Assert a single tweet, a `note_tweet` long-form, a thread, a retweet/quote, and a tombstone normalize correctly: `content_id` = rest_id, `content_url` = `https://x.com/<handle>/status/<id>`, `source_platform="twitter"`, `author_name="@handle"`, `title` = truncated first line, `body_text` = full text / note_tweet, `content_type` ∈ {`tweet`,`thread`}, `view_count`/`like_count` from `legacy`, tombstones dropped (return None).

**Step 2: Run + verify failure**

```bash
pytest tests/test_x_normalize.py -q
```

**Step 3: Implement** `normalize_tweet(raw) -> DiscoveredContent | None` (port `prinsss/twitter-web-exporter` `extractDataFromResponse` unwrapping; mirror `douyin_direct.normalize_aweme_item`). Multi-tweet conversation → `content_type="thread"`.

**Step 4: Run + commit**

```bash
pytest tests/test_x_normalize.py -q
git add src/openbiliclaw/discovery/x_normalize.py tests/test_x_normalize.py tests/fixtures/x/
git commit -m "feat: normalize X tweets into DiscoveredContent"
```

---

### Task 8: `XAdapter` + Three Strategies + Registration

**Files:**
- Create: `src/openbiliclaw/sources/twitter_adapter.py`
- Create: `src/openbiliclaw/discovery/strategies/x.py`
- Modify: `src/openbiliclaw/api/runtime_context.py`
- Modify: `src/openbiliclaw/config.py` (minimal `SourcesTwitter` so registration can gate; Task 10 extends it)
- Test: `tests/test_twitter_adapter.py`, `tests/test_x_strategies.py`

Depends on Tasks 2, 6, 7. **Adds a minimal `SourcesTwitter` config model here (Codex R1 M1)** so `runtime_context` can safely gate on `[sources.twitter].enabled`; Task 10 extends that model with budgets/shares/serializer/popup.

**Step 1: Write failing tests**

```python
@pytest.mark.asyncio
async def test_xadapter_dispatches_by_strategy() -> None:
    adapter = XAdapter(client=_FakeXClient(), search=_search, feed=_feed, creator=_creator)
    items = await adapter.fetch(SourceRecipe(id="1", source_type="twitter", name="X-search",
                                             strategy="search", config={"query": "rust"}), _profile(), limit=5)
    assert items and all(i.source_platform == "twitter" for i in items)
    assert adapter.source_type == "twitter"
```

Assert each strategy calls the right `XClient` method and returns normalized `DiscoveredContent`. `XForYouStrategy` maps to `recipe.strategy == "feed"`.

**Step 2: Run + verify failure**

```bash
pytest tests/test_twitter_adapter.py tests/test_x_strategies.py -q
```

**Step 3: Implement**

- `discovery/strategies/x.py`: `XSearchStrategy` (keywords from Soul profile, reuse the `xhs_keyword_gen` approach), `XForYouStrategy`, `XCreatorStrategy` — each `XClient.<call>` → `normalize_tweet`.
- `sources/twitter_adapter.py`: `XAdapter` with `source_type="twitter"`; `fetch(recipe, profile, limit)` dispatches by `recipe.strategy` (`search`/`feed`/`creator`) — a real implementation (mirror `bilibili_adapter.py`).
- `config.py`: add a **minimal** `SourcesTwitter(enabled: bool = False, cookie_env: str = "OPENBILICLAW_X_COOKIE")` + a `twitter` field on `SourcesConfig` (default disabled). Must land **before** runtime registration so the gate exists; Task 10 extends it (budgets/shares/serializer).
- `api/runtime_context.py`: gate on `getattr(config.sources, "twitter", None)` enabled; when enabled, construct `XClient(cookie=...)` from `data/x_cookie.json`/env and register `XAdapter`; when disabled, register nothing and **do not import** `twitter_cli` (mirror the bilibili/xhs registration block, defensively).

**Step 4: Run + commit**

```bash
pytest tests/test_twitter_adapter.py tests/test_x_strategies.py -q
git add src/openbiliclaw/sources/twitter_adapter.py src/openbiliclaw/discovery/strategies/x.py src/openbiliclaw/api/runtime_context.py src/openbiliclaw/config.py tests/test_twitter_adapter.py tests/test_x_strategies.py
git commit -m "feat: add X adapter and search/for-you/creator strategies"
```

---

### Task 9: Account Subscription

**Files:**
- Modify: `src/openbiliclaw/storage/database.py` (or new `src/openbiliclaw/sources/x_tasks.py`) — `x_creator_subscriptions` table + CRUD
- Modify: `src/openbiliclaw/api/app.py` — `GET/POST/DELETE /api/sources/x/creators`
- Test: `tests/test_x_creators.py`

**Step 1: Write failing tests** (mirror the XHS creators tests)

Add/list/delete a handle; `last_fetched_at` updates; duplicate handle is idempotent.

**Step 2: Run + verify failure → Step 3: Implement**

`x_creator_subscriptions(id, handle, added_at, last_fetched_at)` (mirror `xhs_creator_subscriptions`, `xhs_tasks.py:462`). Endpoints mirror `/api/sources/xhs/creators`. No extension round-trip — the producer (Task 10) fetches each subscription server-side via `XCreatorStrategy`.

**Step 4: Run + commit**

```bash
pytest tests/test_x_creators.py -q
git add src/openbiliclaw/storage/database.py src/openbiliclaw/api/app.py tests/test_x_creators.py
git commit -m "feat: X account subscriptions (server-side fetch)"
```

---

### Task 10: Producer, Scheduling, Source Health & Config

**Files:**
- Create: `src/openbiliclaw/runtime/x_producer.py`
- Modify: `src/openbiliclaw/runtime/refresh.py` (wire the producer tick), `src/openbiliclaw/runtime/source_policy.py`
- Modify: `src/openbiliclaw/storage/database.py` (persist X source health), `src/openbiliclaw/api/app.py` + `src/openbiliclaw/api/models.py` (`GET /api/sources/x/status` exposing health)
- Modify: `config.example.toml`, `src/openbiliclaw/config.py`
- Test: `tests/test_x_producer.py`, `tests/test_x_source_health.py`, `tests/test_config.py`, `tests/test_cli.py`

**Step 1: Write failing tests**

- Producer (DB-backed, Codex R1 M4): when `enabled=false`, `produce_if_due()` is a no-op and never imports `twitter_cli`; when enabled and due, it **enqueues claimable `discovery_candidates` rows** with `source_platform="twitter"` + `content_type`/`body_text`, within `daily_*_budget`/`min_interval_minutes` + `request_interval_seconds` jitter, For-You throttled to a low daily cadence — and **asserts `content_cache` is untouched and no evaluator is invoked** (fetch-only contract).
- Refresh wiring: a `refresh.py` tick with `[sources.twitter].enabled` invokes the X producer (assert via a fake/spy) and is capacity-gated like the other producers.
- **Source health (Codex R2 MAJOR; spec §7):** map `XClient` failures → health states 401→`expired_cookie`, 403→`blocked`, 429→`rate_limited` (with cooldown), missing cookie→`missing_cookie`, success→`ok`; persisted health drives per-code backoff; repeated For-You failures auto-pause For-You; status is readable via the API. `tests/test_x_source_health.py` asserts each mapping, the cooldown/backoff, For-You auto-pause, and the status endpoint payload.
- Config round-trip: `[sources.twitter]` + `pool_source_shares.twitter` survive load → save → `config-show`; disabling `twitter` drops its quota (source_policy).

**Step 2: Run + verify failure**

```bash
pytest tests/test_x_producer.py tests/test_x_source_health.py tests/test_config.py -k "twitter or health" -q
```

**Step 3: Implement**

- `config.example.toml`: add `[sources.twitter]` (`enabled=false`, `mode="cookie"`, `cookie_env`, `daily_search_budget`, `daily_feed_budget`, `daily_creator_budget`, `request_interval_seconds`, `min_interval_minutes`) and `pool_source_shares.twitter = 1` (spec §6).
- `config.py`: **extend** the minimal `SourcesTwitter` from Task 8 with `mode`, `daily_search_budget`/`daily_feed_budget`/`daily_creator_budget`, `request_interval_seconds`, `min_interval_minutes`; add `twitter` to the default `pool_source_shares` literal; cover BOTH the TOML parser AND the serializer/`config-show` writer (Codex M6); env overrides.
- `source_policy.py`: include `twitter` in enable/quota redistribution.
- `runtime/x_producer.py`: mirror `xhs_producer`/youtube producer — soul-driven search keywords, For-You + per-subscription creator scheduling, budget/interval gating; enqueue into the pending pool (do NOT evaluate or write `content_cache` — the shared evaluator does that).
- `refresh.py`: add an `_tick_x_producer()` gated by capacity, like the other producers.
- **Source health/backoff (spec §7):** persist X health in `storage/database.py` (`ok`/`missing_cookie`/`expired_cookie`/`rate_limited`/`blocked` + cooldown timestamp); `x_producer` reads it to skip/backoff per code (401/403 → wait for re-login; 429 → cooldown) and auto-pauses For-You after N consecutive failures; expose via `GET /api/sources/x/status` (+ `api/models.py`) for the settings UI (rendered in Task 12). Relies on `XClient` raising typed errors (Task 6).

**Step 4: Run + commit**

```bash
pytest tests/test_x_producer.py tests/test_x_source_health.py tests/test_config.py tests/test_cli.py -k "twitter or config or health" -q
git add src/openbiliclaw/runtime/x_producer.py src/openbiliclaw/runtime/refresh.py src/openbiliclaw/runtime/source_policy.py src/openbiliclaw/storage/database.py src/openbiliclaw/api/app.py src/openbiliclaw/api/models.py config.example.toml src/openbiliclaw/config.py tests/test_x_producer.py tests/test_x_source_health.py tests/test_config.py tests/test_cli.py
git commit -m "feat: schedule X discovery producer with config, quotas + source health"
```

---

### Task 11: Recommendation Surface — Text Card + Prompt `body_text`

**Files:**
- Modify: `src/openbiliclaw/llm/prompts.py`
- Modify: `src/openbiliclaw/recommendation/engine.py`
- Modify: recommendation card frontends (`src/openbiliclaw/web/...` + `extension/popup/...`)
- Test: `tests/test_llm_prompts.py`, `extension/tests/popup-helpers.test.ts` (text-card render)

**Step 1: Write failing tests (prompt + frontend)**

Extend `test_prompt_builder_system_messages_are_call_invariant` to cover any builder that now reads `body_text`. Assert `body_text` appears in the USER message and the system message bytes are unchanged across two distinct inputs.

**Frontend (Codex R1 M6):** add a `popup-helpers` unit test asserting an item with `content_type ∈ {tweet,thread}` and empty `cover_url` renders a text card (shows `body_text`/title, no broken thumbnail node), backed by a required manual smoke in Task 14.

**Step 2: Run + verify failure**

```bash
pytest tests/test_llm_prompts.py -k "invariant or body_text" -q
(cd extension && npm run build && node --test dist/tests/popup-helpers.test.js)
```

**Step 3: Implement**

- `llm/prompts.py`: include `body_text` in the recommendation/evaluation builders' USER payload only, via `json.dumps(..., ensure_ascii=False, indent=2, sort_keys=True)`; keep system prompts byte-static (prompt-cache convention).
- `recommendation/engine.py`: ensure franchise/diversity/MMR tolerate text items (`cover_url` empty, `duration` 0) without errors.
- Frontends: render a "no-cover text card" when `content_type ∈ {tweet, thread}` (or `cover_url` empty) — show `body_text`/title instead of a thumbnail.

**Step 4: Run + commit**

```bash
pytest tests/test_llm_prompts.py -q && (cd extension && node --test)
git add src/openbiliclaw/llm/prompts.py src/openbiliclaw/recommendation/engine.py src/openbiliclaw/web extension/popup tests/test_llm_prompts.py extension/tests/popup-helpers.test.ts
git commit -m "feat: render X text cards and pass body_text to LLM prompts"
```

---

### Task 12: Enable Toggle + Settings UI

**Files:**
- Modify: `extension/popup/popup.js`, `extension/popup/popup.html`, `extension/popup/popup-init-control.js`
- Modify: `src/openbiliclaw/cli.py` (init prompt + `--yes-x`), `src/openbiliclaw/runtime/init_prereqs.py` if needed
- Test: `extension/tests/init-control.test.ts`, `tests/test_cli.py`

**Step 1: Write failing tests**

`INIT_SOURCE_OPTIONS` includes `{key:"twitter", label:"X", required:false}`; saving settings round-trips `sources.twitter.enabled` + `pool_source_shares.twitter`; `openbiliclaw init --yes-x` enables `[sources.twitter]`.

**Step 2: Run + verify failure → Step 3: Implement**

- `popup-init-control.js`: add the X entry to `INIT_SOURCE_OPTIONS`.
- `popup.html`: add `data-source-card="twitter"` settings card.
- `popup.js`: add `cfgTwitterEnabled`, `cfgPoolShareTwitter`, and `enabled_sources.twitter` wiring (mirror the douyin/xhs blocks).
- **Source health display (spec §7):** on the X source card, show health from `GET /api/sources/x/status` (`ok`/`expired_cookie`/`rate_limited`/`blocked`/`missing_cookie`), mirroring how other sources surface login/status.
- `cli.py`: interactive init prompt + `--yes-x` flag (mirror `--yes-xhs`).

**Step 4: Run + commit**

```bash
(cd extension && node --test) && pytest tests/test_cli.py -k "init or yes_x" -q
git add extension/popup/popup.js extension/popup/popup.html extension/popup/popup-init-control.js src/openbiliclaw/cli.py extension/tests/init-control.test.ts tests/test_cli.py
git commit -m "feat: X source enable toggle in init + settings UI"
```

---

### Task 13: Packaging Hook, Documentation & Architecture Sync

**Files:**
- Modify: `docs/modules/extension.md`, `docs/modules/discovery.md`, `docs/modules/sources.md` (or equivalent), `docs/modules/config.md`, `docs/modules/cli.md`
- Modify: `docs/architecture.md`, `docs/spec.md` §3, `README.md`, `README_EN.md`
- Modify: `docs/changelog.md`, `config.example.toml` comments
- Modify: `docs/docker-deployment.md` / `docs/agent-install.md` / `scripts/install.sh` (the new `openbiliclaw[x]` extra + `curl_cffi`)
- Modify: `packaging/build.py` (desktop build installs the `x` extra + PyInstaller hook collecting `curl_cffi` native binaries)

**Step 0: Packaging hook (Codex R1 M2):** make `packaging/build.py` install the `x` extra for desktop bundles (default = always-bundle, spec §8) and add a PyInstaller hook / `--collect-binaries curl_cffi` so the native libcurl ships per OS·arch; verify the built bundle can `import twitter_cli`. (Plain `pip install openbiliclaw` stays X-free; the extra is added to `pyproject.toml` in Task 6.)

**Step 1–4:** Per spec §11. Add X to every architecture diagram (text layers + `docs/spec.md` ASCII + README CN/EN top-of-page). Document: the MAIN-world tap + cookie bridge (extension.md), `XAdapter`/three strategies/`XClient`/`x_normalize`/`x_producer` (discovery/sources), `[sources.twitter]` (config.md), `--yes-x` (cli.md). Changelog top entry + bullet. README 📌 highlights callout (≤4 bullets, CN/EN synced) only if this ships a release.

```bash
rg -n "twitter|/api/sources/x/|openbiliclaw\[x\]|body_text|content_type" docs README.md README_EN.md
git diff --check
git add docs README.md README_EN.md config.example.toml scripts/install.sh packaging/build.py
git commit -m "feat: bundle openbiliclaw[x] in desktop build + document X source"
```

---

### Task 14: Full Verification

**Files:** No planned source changes unless a failure reveals a real issue.

**Step 1: Lint + types**

```bash
ruff format src/ tests/ && ruff check src/ tests/ && mypy src/
(cd extension && npm run typecheck)
```

**Step 2: Focused suites**

```bash
pytest tests/test_event_format.py tests/test_discovery_candidate_store.py tests/test_x_normalize.py -q
pytest tests/test_x_client.py tests/test_twitter_adapter.py tests/test_x_strategies.py -q
pytest tests/test_x_producer.py tests/test_x_creators.py tests/test_api_x_cookie.py -q
pytest tests/test_llm_prompts.py tests/test_config.py tests/test_cli.py -q
(cd extension && node --test)
```

**Step 3: Full suite**

```bash
pytest
```

**Step 4: Manual smoke** (needs a logged-in x.com session)

- `openbiliclaw config-show` shows `[sources.twitter]` after enabling.
- Log into x.com → extension auto-syncs cookie → `data/x_cookie.json` appears.
- Like/bookmark a tweet → an event reaches `/api/events` and scores positive; retweet/follow recorded as context-tier.
- With `[sources.twitter].enabled=true`, a refresh enqueues X candidates (search/For-You/creator) that evaluate in the mixed pool and surface as text cards with non-empty `content_id`/`content_url`/`source_platform="twitter"` and `content_type ∈ {tweet,thread}`.
- With `enabled=false`, no `twitter_cli` import happens and the quota is dropped.

**Step 5: Final commit if verification changed files**

```bash
git status --short
git commit -m "test: verify X (twitter) source end-to-end"
```

---

## Execution Notes

- **Lazy import is load-bearing.** Any top-level `import twitter_cli` / `import curl_cffi` breaks non-X installs (the extra may be absent). Import inside functions on the enabled path only; Task 6 has a regression test for this.
- **Do not extend the global engagement sets.** `_EXPLICIT_POSITIVE_EVENT_TYPES` (event_format) and `_ENGAGEMENT_TYPES` (soul/pipeline) must stay `{like, coin, favorite, comment}`. X retweet/follow are context-tier in v1 by design; promoting them later is a separate, cross-source-tested change.
- **`content_type`, not `media_type`.** There is no `media_type` field anywhere. Both hardcoded `"note" if xiaohongshu else "video"` sites (`candidate_pool.py:137`, `engine.py:1274`) must honor `item.content_type`.
- **Migrate via `_ensure_*_columns()` helpers**, never hand-written ALTER; add the columns to the fresh `CREATE TABLE` schemas too, or new databases will lack them.
- **Producers fetch only.** `XAdapter`/`x_producer` enqueue into `discovery_candidates`; they must not call the evaluator or write `content_cache` (the shared mixed-source evaluator owns that — per the unified-pool spec).
- **`bvid` compatibility:** when writing `content_cache` for X, keep using `content_id` as `bvid` (existing recommendation joins still depend on `bvid`), as the unified-pool plan does for non-Bilibili sources.
- **Open product decision (non-blocking):** desktop packaging includes the `openbiliclaw[x]` extra by default (always-bundle); revisit "on-demand download" only if binary size / ToS optics warrant it. Confirm with the user before the packaging task if still unsettled.
- **Canonical `twitter-cli` source** is pinned in Task 1; do not switch maintainers (`jackwener` vs `public-clis`) without re-confirming.

Plan complete. Use `superpowers:executing-plans` to implement it task-by-task.
