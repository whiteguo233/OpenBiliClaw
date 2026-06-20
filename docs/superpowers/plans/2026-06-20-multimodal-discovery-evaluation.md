# Multimodal Discovery Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans when executing this plan task-by-task. Production code changes must follow superpowers:test-driven-development: write or update a failing test first, run it, then implement the smallest change needed to pass.

**Goal:** Feed richer source engagement metrics into discovery evaluation and add an optional cover-aware multimodal evaluation path with safe text-only fallback for non-vision models.

**Architecture:** `DiscoveredContent` stays the normalized source-agnostic candidate shape. Source adapters populate common metric fields opportunistically. SQLite persists those fields in `content_cache` and `discovery_candidates`. The evaluator serializes metrics/tags into text prompts, and when configured plus supported, prepares compressed cover images through the existing image-cache security boundary and sends them through a provider-neutral multimodal task method.

**Tech Stack:** Python 3.11+ package with pytest/ruff/mypy, SQLite storage, FastAPI config API, vanilla desktop Web settings, TypeScript Chrome extension with node:test/tsc.

**Status:** Implemented in `feat/multimodal-discovery-eval`. Final checks passed for ruff lint, mypy, full Python pytest, focused E2E coverage, extension tests, and extension typecheck. The optional `twitter-cli`, Python Playwright, and `websocket-client` runtime extras were installed locally in `.venv` to exercise full/X/browser E2E paths. Global `ruff format --check src/ tests/` still reports repository-wide pre-existing formatter drift.

---

### Task 1: Baseline And Metric Model Round Trip

**Files:**
- Modify: `tests/test_discovery_candidate_store.py` or nearest candidate storage tests
- Modify: `tests/test_storage.py`
- Modify: `src/openbiliclaw/discovery/engine.py`
- Modify: `src/openbiliclaw/discovery/candidate_pool.py`
- Modify: `src/openbiliclaw/storage/database.py`

- [x] **Step 1: Run baseline tests**

Run Python and extension baselines before implementation:

```bash
.venv/bin/pytest
cd extension && npm test
```

- [x] **Step 2: Write failing storage/model tests**

Add tests proving `DiscoveredContent.to_cache_kwargs()`, candidate enqueue, row hydration, and existing-row defaults carry:

```python
favorite_count
collect_count
comment_count
share_count
danmaku_count
reply_count
retweet_count
bookmark_count
```

- [x] **Step 3: Implement metric fields and SQLite migrations**

Add dataclass fields, candidate write fields, schema columns, migration helpers, insert/update mappings, and row hydration defaults. Keep all new columns defaulting to `0`.

- [x] **Step 4: Verify focused tests**

Run:

```bash
.venv/bin/pytest tests/test_storage.py tests/test_discovery_candidate_store.py -q
```

### Task 2: Source Normalizers Populate Metrics

**Files:**
- Modify: `tests/test_douyin_direct.py`
- Modify: `tests/test_douyin_plugin_search.py`
- Modify: `tests/test_x_normalize.py`
- Modify: YouTube/Bilibili/API ingest focused tests found in the repo
- Modify: `src/openbiliclaw/discovery/strategies/search.py`
- Modify: `src/openbiliclaw/discovery/strategies/trending.py`
- Modify: `src/openbiliclaw/discovery/strategies/related_chain.py`
- Modify: `src/openbiliclaw/sources/douyin_direct.py`
- Modify: `src/openbiliclaw/sources/douyin_plugin_search.py`
- Modify: `src/openbiliclaw/discovery/x_normalize.py`
- Modify: `src/openbiliclaw/youtube/client.py`
- Modify: `src/openbiliclaw/api/app.py`

- [x] **Step 1: Write failing normalizer tests**

Cover Bilibili `stat` fields, Douyin `collect/comment/share`, YouTube optional `like/comment`, X replies/retweets/bookmarks, and API ingest for Bili/XHS extension payloads.

- [x] **Step 2: Implement tolerant metric mapping**

Map only fields already present in list/plugin payloads. Missing or malformed values stay `0`.

- [x] **Step 3: Verify focused tests**

Run the relevant normalizer/API focused tests found in Step 1.

### Task 3: Extension Card Metrics

**Files:**
- Modify: `extension/tests/*.test.ts`
- Modify: `extension/src/content/xhs/passive.ts`
- Modify: `extension/src/content/dy/dom-extractor.ts`
- Modify: extension type definitions used by discovery payloads

- [x] **Step 1: Write failing parser and payload tests**

Test compact count parsing for `1.2万`, `3k`, `1,234`, `赞 42`, and empty/unknown values. Test XHS and Douyin card metadata includes visible metric fields when available.

- [x] **Step 2: Implement shared/tolerant parser and DOM extraction**

Parse visible chips only. Do not navigate into detail pages for metrics.

- [x] **Step 3: Verify extension tests**

Run:

```bash
cd extension && npm test
cd extension && npm run typecheck
```

### Task 4: Text Evaluator Input Contract

**Files:**
- Modify: `tests/test_llm_prompts.py`
- Modify: `tests/test_discovery_engine.py`
- Modify: `src/openbiliclaw/llm/prompts.py`
- Modify: `src/openbiliclaw/discovery/engine.py`

- [x] **Step 1: Write failing evaluator tests**

Assert batch prompt items include tags and all common metrics. Assert single-item fallback evaluation includes `body_text`, tags, and metrics.

- [x] **Step 2: Implement prompt serialization and guidance**

Add engagement metrics as supporting context while preserving source-agnostic JSON and existing scoring behavior.

- [x] **Step 3: Verify prompt/evaluator tests**

Run:

```bash
.venv/bin/pytest tests/test_llm_prompts.py tests/test_discovery_engine.py -q
```

### Task 5: Multimodal Config, Capability Fallback, And Batch Sizing

**Files:**
- Modify: config/API/settings tests found in the repo
- Modify: `src/openbiliclaw/config.py`
- Modify: `src/openbiliclaw/api/models.py`
- Modify: `src/openbiliclaw/api/app.py`
- Modify: `src/openbiliclaw/discovery/engine.py`
- Modify: LLM service/provider protocol files as needed
- Modify: `src/openbiliclaw/web/desktop/index.html`
- Modify: `src/openbiliclaw/web/desktop/assets/js/app.js`

- [x] **Step 1: Write failing config and batch-size tests**

Assert config defaults/validation, `/api/config` round-trip, desktop settings payload, effective multimodal batch size, and non-vision fallback issue state.

- [x] **Step 2: Implement config fields and UI toggle**

Expose `multimodal_evaluation_enabled`, `multimodal_batch_size`, image max edge, quality, and timeout with validation.

- [x] **Step 3: Implement provider-neutral fallback hooks**

Add capability detection. When enabled but unsupported, keep text-only evaluation and expose a clear runtime/config issue.

- [x] **Step 4: Verify focused tests**

Run config/API/settings/discovery focused tests.

### Task 6: Cover Image Preparation And Multimodal LLM Path

**Files:**
- Add/Modify: evaluator image helper module/tests as appropriate
- Modify: `src/openbiliclaw/discovery/engine.py`
- Modify: `src/openbiliclaw/llm/service.py` and provider implementations as needed

- [x] **Step 1: Write failing image helper and fake-provider tests**

Assert allowed cover URLs download through the existing image fetch boundary, resize/compress to configured limits, attach stable image IDs, skip failures, and retry text-only when a fake provider rejects images.

- [x] **Step 2: Implement bounded cover preparation**

Use existing image-cache validation where possible. Do not persist compressed image bytes in SQLite.

- [x] **Step 3: Implement multimodal task call**

Expose structured multimodal input at the LLM service boundary instead of leaking provider-specific payloads into discovery.

- [x] **Step 4: Verify focused tests**

Run image/evaluator/provider tests.

### Task 7: Documentation And Full Verification

**Files:**
- Modify: `docs/modules/discovery.md`
- Modify: `docs/modules/config.md`
- Modify: `docs/modules/extension.md`
- Modify: `docs/changelog.md`

- [x] **Step 1: Update required docs**

Document evaluator input schema, config fields, extension metric payloads, and changelog entry.

- [x] **Step 2: Run final checks**

Run:

```bash
ruff format src/ tests/
ruff check src/ tests/
mypy src/
.venv/bin/pytest
cd extension && npm test
cd extension && npm run typecheck
```

- [x] **Step 3: Review diff**

Run:

```bash
git status --short
git diff --stat
```

Confirm the implementation branch contains only scoped changes.
