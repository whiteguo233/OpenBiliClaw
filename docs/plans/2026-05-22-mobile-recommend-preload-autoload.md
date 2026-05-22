# Mobile Recommend Preload And Autoload Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add mobile recommendation cover preloading and bottom auto-append without changing backend APIs.

**Architecture:** Add small pure view-model helpers for cover preload URL selection and image loading strategy, then wire them into `recommend.js`. Reuse the current `handleAppend()` path for both button clicks and automatic bottom loading.

**Tech Stack:** Vanilla JS ES modules, DOM `IntersectionObserver`, browser `Image`, pytest-driven Node checks.

---

### Task 1: View-Model Regression Tests

**Files:**
- Modify: `tests/test_mobile_web_view_models.py`
- Modify: `src/openbiliclaw/web/js/view-models.js`

**Step 1: Write the failing test**

Add tests that import `getRecommendationCoverPreloadUrls()` and `getRecommendationImageLoadingAttrs()` from `view-models.js`. Check that preload URL selection deduplicates proxy URLs and that the first two cards are eager/high priority while later cards are lazy/auto.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_mobile_web_view_models.py::TestMobileWebViewModels::test_recommendation_cover_preload_helpers -q`

Expected: FAIL because the helper exports do not exist.

**Step 3: Write minimal implementation**

Add the two pure helpers near the existing cover helpers in `view-models.js`.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_mobile_web_view_models.py::TestMobileWebViewModels::test_recommendation_cover_preload_helpers -q`

Expected: PASS.

### Task 2: Recommendation View Wiring Tests

**Files:**
- Modify: `tests/test_mobile_web_view_models.py`
- Modify: `src/openbiliclaw/web/js/views/recommend.js`

**Step 1: Write the failing test**

Add a static regression test that requires `recommend.js` to import the new helpers, define a cover prewarm helper using `new Image()`, use eager/fetchpriority card attributes, and wire `IntersectionObserver` to `.load-more-row`.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_mobile_web_view_models.py::TestMobileWebViewModels::test_mobile_recommendation_view_preloads_and_auto_appends -q`

Expected: FAIL because the view has not been wired yet.

**Step 3: Write minimal implementation**

Update `recommend.js` to:

- import the new helpers
- render cards with loading/fetchpriority attributes based on index
- prewarm covers after full render and append
- observe `.load-more-row` and call `handleAppend()` when near bottom

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_mobile_web_view_models.py::TestMobileWebViewModels::test_mobile_recommendation_view_preloads_and_auto_appends -q`

Expected: PASS.

### Task 3: Documentation

**Files:**
- Modify: `docs/changelog.md`
- Modify: `docs/mobile-web-spec.md`
- Modify: `docs/modules/recommendation.md`

**Step 1: Update docs**

Record the mobile web behavior change in the current changelog block, mobile web spec, and recommendation module feature table.

**Step 2: Run focused verification**

Run: `pytest tests/test_mobile_web_view_models.py -q`

Expected: PASS.

### Task 4: Final Verification

**Files:**
- No new files.

**Step 1: Run formatter/lint/test checks**

Run:

```bash
ruff format tests/test_mobile_web_view_models.py
ruff check tests/test_mobile_web_view_models.py
pytest tests/test_mobile_web_view_models.py -q
```

Expected: all commands exit 0.
