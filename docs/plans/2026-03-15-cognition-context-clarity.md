# Cognition Context Clarity Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make each cognition card in the popup profile tab explain its default-state context and clearly signal whether it can expand, so users can tell what content or recent signals the judgment came from before opening details.

**Architecture:** Extend the soul-layer cognition card payload with `context_line`, `source_label`, and `expand_hint`, expose those fields through `/api/profile-summary`, normalize them in popup helpers with conservative fallbacks for legacy cards, and render profile cognition cards as either explicit expandable buttons or explicit summary-only cards.

**Tech Stack:** Python 3.14, FastAPI, vanilla JS popup UI, Node test runner, pytest

---

### Task 1: Add context-aware cognition card fields to the API contract

**Files:**
- Modify: `src/openbiliclaw/api/models.py`
- Modify: `src/openbiliclaw/api/app.py`
- Test: `tests/test_api_app.py`

**Step 1: Write the failing test**

Add API tests that expect `/api/profile-summary` to return cognition cards with:

- `context_line`
- `source_label`
- `expand_hint`

The tests should cover both structured cards and legacy summary-only records, and verify that:

- structured cards preserve explicit values
- legacy records get stable fallback values
- pagination still works with the new fields present

**Step 2: Run test to verify it fails**

Run: `/Users/white/workspace/OpenBiliClaw/.venv/bin/pytest tests/test_api_app.py -q`

Expected: FAIL because the response model and endpoint mapping do not currently expose the new context fields.

**Step 3: Write minimal implementation**

Update the API models and profile summary endpoint mapping so cognition cards expose:

- `context_line`
- `source_label`
- `expand_hint`

Keep backward compatibility by deriving defaults for legacy records and preserving current pagination metadata.

**Step 4: Run test to verify it passes**

Run: `/Users/white/workspace/OpenBiliClaw/.venv/bin/pytest tests/test_api_app.py -q`

Expected: PASS

**Step 5: Commit**

```bash
git add src/openbiliclaw/api/models.py src/openbiliclaw/api/app.py tests/test_api_app.py
git commit -m "feat: expose cognition card context fields"
```

### Task 2: Generate explicit context lines and expand hints in the soul layer

**Files:**
- Modify: `src/openbiliclaw/soul/engine.py`
- Test: `tests/test_soul_engine.py`

**Step 1: Write the failing test**

Add focused tests for:

- single comment feedback cards with content titles
- single dislike feedback cards with content titles or topic fallback
- dialogue-triggered cards with chat context fallback
- aggregate cognition cards that can list representative titles/topics
- aggregate cognition cards that must fall back to `基于最近几条相关内容`

Each test should assert the concrete `context_line`, `source_label`, and `expand_hint`, not just field presence.

**Step 2: Run test to verify it fails**

Run: `/Users/white/workspace/OpenBiliClaw/.venv/bin/pytest tests/test_soul_engine.py -q`

Expected: FAIL because the engine does not yet emit explicit context fields or source labels.

**Step 3: Write minimal implementation**

Extend the cognition builders in `SoulEngine` to:

- emit `context_line` for immediate and aggregate updates
- emit human-readable `source_label`
- emit `expand_hint` based on whether the card has supporting detail
- prefer concrete titles or representative titles/topics when they are trustworthy
- fall back conservatively to `基于最近几条相关内容`

**Step 4: Run test to verify it passes**

Run: `/Users/white/workspace/OpenBiliClaw/.venv/bin/pytest tests/test_soul_engine.py -q`

Expected: PASS

**Step 5: Commit**

```bash
git add src/openbiliclaw/soul/engine.py tests/test_soul_engine.py
git commit -m "feat: generate cognition context lines"
```

### Task 3: Normalize context fields and summary-only fallbacks in popup helpers

**Files:**
- Modify: `extension/popup/popup-helpers.js`
- Test: `extension/tests/popup-helpers.test.ts`

**Step 1: Write the failing test**

Add helper tests covering:

- structured cards with explicit `context_line`, `source_label`, and `expand_hint`
- legacy summary-only cards that need fallback context and summary-only state
- cards with details but no explicit `expand_hint`
- cards with no details and no explicit `expand_hint`

The tests should verify the frontend normalization shape used by the popup renderer.

**Step 2: Run test to verify it fails**

Run: `npm test -- --test-name-pattern "cognition"`

Expected: FAIL because popup helpers do not currently normalize these fields or produce explicit card-state copy.

**Step 3: Write minimal implementation**

Normalize cognition cards into one stable frontend shape that includes:

- `contextLine`
- `sourceLabel`
- `expandHint`
- `expandable`
- `expandLabel`

Preserve legacy compatibility by:

- deriving `expandHint` from detail fields when absent
- defaulting missing context to `基于最近几条相关内容`
- defaulting missing source labels to existing safe labels

**Step 4: Run test to verify it passes**

Run: `npm test -- --test-name-pattern "cognition"`

Expected: PASS

**Step 5: Commit**

```bash
git add extension/popup/popup-helpers.js extension/tests/popup-helpers.test.ts
git commit -m "feat: normalize cognition context metadata"
```

### Task 4: Render context-first cards with explicit expandable vs summary-only states

**Files:**
- Modify: `extension/popup/popup.js`
- Modify: `extension/popup/popup.html`
- Test: `extension/tests/popup-copy.test.ts`
- Test: `extension/tests/popup-scroll.test.ts`

**Step 1: Write the failing test**

Add UI tests that expect:

- every card default state shows a context line
- expandable cards render `展开` before opening and `收起` after opening
- summary-only cards render `仅结论` and are not styled or announced like buttons
- source label appears as compact meta, not as the only context
- existing pagination behavior remains intact after the visual changes

**Step 2: Run test to verify it fails**

Run: `npm test`

Expected: FAIL because the popup still relies on vague default meta and does not explicitly distinguish summary-only cards.

**Step 3: Write minimal implementation**

Update the popup rendering so profile cognition cards use the normalized context metadata:

- `summary`
- `contextLine`
- compact `sourceLabel`
- explicit `expandLabel`

Render expandable cards as buttons with arrow/state affordances, and render summary-only cards as static cards with a visible `仅结论` marker.

**Step 4: Run test to verify it passes**

Run: `npm test`

Expected: PASS

**Step 5: Commit**

```bash
git add extension/popup/popup.js extension/popup/popup.html extension/tests/popup-copy.test.ts extension/tests/popup-scroll.test.ts
git commit -m "feat: clarify cognition card expand states"
```

### Task 5: Update docs and run full verification

**Files:**
- Modify: `docs/modules/soul.md`
- Modify: `docs/modules/extension.md`
- Modify: `docs/changelog.md`

**Step 1: Update docs**

Document the new cognition card fields, the fallback context rules, and the popup’s explicit expandable vs summary-only presentation.

**Step 2: Run backend verification**

Run: `/Users/white/workspace/OpenBiliClaw/.venv/bin/pytest tests/test_api_app.py tests/test_soul_engine.py tests/test_memory_manager.py -q`

Expected: PASS

**Step 3: Run extension verification**

Run: `npm test`

Expected: PASS

Run: `npm run build`

Expected: PASS

**Step 4: Commit**

```bash
git add docs/modules/soul.md docs/modules/extension.md docs/changelog.md
git commit -m "docs: record cognition context clarity"
```
