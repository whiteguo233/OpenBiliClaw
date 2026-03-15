# Profile Groups Density Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make popup profile groups feel fuller by returning more items per existing group and adding an explicit `disliked_topics` section.

**Architecture:** Keep the current profile-summary endpoint and popup layout structure, but widen the returned counts for traits/needs/interests and expose `disliked_topics` from the existing preference layer. The popup continues to render chip groups, with one new dislike group added below interests.

**Tech Stack:** Python, FastAPI, JavaScript, Node test runner, pytest, Markdown docs

---

### Task 1: Lock the API payload with failing tests

**Files:**
- Modify: `tests/test_api_app.py`
- Modify: `src/openbiliclaw/api/models.py`
- Modify: `src/openbiliclaw/api/app.py`

**Step 1: Write the failing test**

Update the profile-summary API test so the fake profile contains more than five interests and some `disliked_topics`, then assert the response now returns:
- `core_traits` up to `6`
- `deep_needs` up to `5`
- `top_interests` up to `8`
- `disliked_topics` up to `5`

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_api_app.py::TestBackendAPI::test_profile_summary_endpoint_returns_profile_and_cognition_updates -q`

Expected: FAIL because `disliked_topics` is not in the schema and the endpoint still truncates to smaller counts.

**Step 3: Write minimal implementation**

Add `disliked_topics` to `ProfileSummaryResponse` and update `/api/profile-summary` to read it from `profile.preferences.disliked_topics`, while widening the existing slice limits.

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_api_app.py::TestBackendAPI::test_profile_summary_endpoint_returns_profile_and_cognition_updates -q`

Expected: PASS

### Task 2: Lock popup normalization and layout with failing tests

**Files:**
- Modify: `extension/tests/popup-helpers.test.ts`
- Modify: `extension/tests/popup-layout.test.ts`
- Modify: `extension/popup/popup-helpers.js`
- Modify: `extension/popup/popup.html`
- Modify: `extension/popup/popup.js`

**Step 1: Write the failing tests**

Add helper coverage asserting `normalizeProfileSummary()` preserves `disliked_topics`, and layout coverage asserting the popup markup includes a dedicated `profileDislikes` chip list with the new section heading.

**Step 2: Run tests to verify they fail**

Run: `npm test -- popup-helpers.test.ts popup-layout.test.ts`

Expected: FAIL because the helper drops the field and the markup does not include the new section.

**Step 3: Write minimal implementation**

Normalize `disliked_topics`, wire a new DOM element `profileDislikes`, and render it with the same chip-list pattern and conservative empty-state copy.

**Step 4: Run tests to verify they pass**

Run: `npm test -- popup-helpers.test.ts popup-layout.test.ts`

Expected: PASS

### Task 3: Update docs

**Files:**
- Modify: `docs/modules/extension.md`
- Modify: `docs/changelog.md`

**Step 1: Update module/changelog docs**

Document that the profile tab now shows fuller chip groups and adds an explicit dislike group sourced from stable preference memory.

**Step 2: Verify the docs diff is scoped**

Run: `git diff -- docs/modules/extension.md docs/changelog.md`

Expected: Only profile-group density notes.

### Task 4: Run focused verification

**Files:**
- Verify only

**Step 1: Run backend verification**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_api_app.py -q`

Expected: PASS

**Step 2: Run popup verification**

Run: `cd extension && npm test -- popup-helpers.test.ts popup-layout.test.ts popup-api.test.ts chat-layout.test.ts`

Expected: PASS

**Step 3: Commit**

```bash
git add src/openbiliclaw/api/app.py src/openbiliclaw/api/models.py extension/popup/popup-helpers.js extension/popup/popup.html extension/popup/popup.js tests/test_api_app.py extension/tests/popup-helpers.test.ts extension/tests/popup-layout.test.ts docs/modules/extension.md docs/changelog.md docs/plans/2026-03-16-profile-groups-density-design.md docs/plans/2026-03-16-profile-groups-density.md
git commit -m "feat: enrich popup profile groups"
```
