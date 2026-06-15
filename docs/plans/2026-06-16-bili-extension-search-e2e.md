# Bili Extension Search Phase 3 E2E Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a repeatable real-browser E2E harness for the Bilibili extension-search fallback from producer gating through rendered-page result POST.

**Architecture:** Keep the production API surface unchanged. The harness starts a temporary FastAPI app and SQLite database, launches Chromium with the unpacked MV3 extension, waits for real runtime-stream presence, forces the in-process Bilibili API client into search cooldown, invokes the real `BilibiliExtensionSearchProducer`, and verifies the extension claims the task, opens a real Bilibili search page, scrapes DOM cards, and posts results back to the temporary backend.

**Tech Stack:** Python integration test, FastAPI/Uvicorn, SQLite, Chrome DevTools Protocol via `websocket-client`, Chrome/Chromium with unpacked extension, existing Bili task queue and producer.

---

### Task 1: E2E Helper Tests

**Files:**
- Create: `tests/test_bili_extension_e2e_harness.py`
- Create: `tests/test_bili_extension_browser_e2e.py`

**Steps:**
1. Write failing unit tests for pure helpers: Chrome executable resolution, free-port allocation, CDP service-worker target selection, and Bili result cleanup predicates.
2. Run: `.venv/bin/pytest tests/test_bili_extension_e2e_harness.py -q`
3. Expected: fails because helper functions do not exist.
4. Implement minimal helper functions in `tests/test_bili_extension_browser_e2e.py` so the helper tests pass.
5. Re-run the helper tests.

### Task 2: Opt-In Real Browser Harness

**Files:**
- Modify: `tests/test_bili_extension_browser_e2e.py`

**Steps:**
1. Add `pytestmark = skipif(os.environ.get("BILI_EXTENSION_E2E") != "1")`.
2. Build a temporary `Database`, `create_app(database=..., soul_engine=...)`, and Uvicorn server thread.
3. Launch Chromium with `--disable-extensions-except=<repo>/extension` and `--load-extension=<repo>/extension`.
4. Connect to the extension service-worker target over CDP.
5. Set `popup_backend_endpoint` in `chrome.storage.local` and wait for backend `PresenceTracker.active_count > 0`.

### Task 3: Producer Gate to Extension Result

**Files:**
- Modify: `tests/test_bili_extension_browser_e2e.py`

**Steps:**
1. Set `BilibiliAPIClient._search_cooldown_until = time.monotonic() + 180`.
2. Construct `BilibiliExtensionSearchProducer` with the temporary task queue, real presence tracker, fake LLM/soul objects, and a kick publisher using the temporary app event hub.
3. Call `produce_if_due(keywords=["机械键盘 声音"], limit=1)`.
4. Assert exactly one task was enqueued.
5. Poll `bili_tasks` until status is `completed`.
6. Assert `result_json.videos` contains at least one item with a `BV...` id and title.
7. Clean up the temporary browser/server and reset Bilibili cooldown class state.

### Task 4: Docs and Verification

**Files:**
- Modify: `docs/modules/extension.md`
- Modify: `docs/modules/bilibili.md`
- Modify: `docs/changelog.md`

**Steps:**
1. Document `BILI_EXTENSION_E2E=1 .venv/bin/pytest tests/test_bili_extension_browser_e2e.py -q -s`.
2. Mention that the harness uses a temporary database and does not require a production debug endpoint.
3. Run focused helper tests.
4. Run the real E2E locally with `BILI_EXTENSION_E2E=1`.
5. Run existing extension/backend verification commands before committing.
