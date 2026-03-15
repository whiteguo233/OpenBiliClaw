# Submission Progress Visibility Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make popup chat sends and recommendation feedback show real progress stages instead of appearing stuck on a single `发送中...` state.

**Architecture:** Add a small set of pure helper functions for progress copy and runtime refresh mapping, then wire them into existing popup request flows. Chat gets a dedicated local status line, while feedback reuses each card's existing `feedback-status` line and optionally follows runtime stream events for backend refresh progress.

**Tech Stack:** Vanilla JS, static HTML/CSS, Node test runner

---

### Task 1: Lock the progress copy contract

**Files:**
- Modify: `extension/tests/popup-helpers.test.ts`
- Modify: `extension/tests/chat-layout.test.ts`
- Test: `extension/tests/popup-helpers.test.ts`
- Test: `extension/tests/chat-layout.test.ts`

**Step 1: Write the failing tests**

- Add a helper test for chat and feedback progress messages.
- Add a helper test for mapping runtime refresh events to user-facing status.
- Add a layout test for a dedicated chat status line.

**Step 2: Run test to verify it fails**

Run: `npm test -- popup-helpers.test.ts chat-layout.test.ts`

**Step 3: Write minimal implementation**

- Add pure helper functions in `popup-helpers.js`.
- Add chat status markup and CSS.

**Step 4: Run test to verify it passes**

Run: `npm test -- popup-helpers.test.ts chat-layout.test.ts`

**Step 5: Commit**

```bash
git add extension/tests/popup-helpers.test.ts extension/tests/chat-layout.test.ts extension/popup/popup-helpers.js extension/popup/popup.html
git commit -m "fix: define popup submission progress states"
```

### Task 2: Wire progress stages into chat send flow

**Files:**
- Modify: `extension/popup/popup.js`
- Modify: `extension/popup/popup.html`
- Test: `extension/tests/popup-helpers.test.ts`

**Step 1: Write the failing test**

- Reuse helper tests to lock stage copy before wiring.

**Step 2: Run test to verify it fails**

Run: `npm test -- popup-helpers.test.ts`

**Step 3: Write minimal implementation**

- Add `chatStatus` element handling.
- Show `waiting reply` immediately, escalate to a slower message on timeout, then update through `sync profile` and `sync activity`.

**Step 4: Run test to verify it passes**

Run: `npm test -- popup-helpers.test.ts`

**Step 5: Commit**

```bash
git add extension/popup/popup.js extension/popup/popup.html extension/popup/popup-helpers.js
git commit -m "fix: show chat send progress stages"
```

### Task 3: Wire progress stages into feedback flows

**Files:**
- Modify: `extension/popup/popup.js`
- Modify: `extension/popup/popup-helpers.js`
- Test: `extension/tests/popup-helpers.test.ts`

**Step 1: Write the failing test**

- Lock runtime refresh event mapping and feedback progress copy.

**Step 2: Run test to verify it fails**

Run: `npm test -- popup-helpers.test.ts`

**Step 3: Write minimal implementation**

- Update comment, like, and dislike actions to surface local progress stages.
- Add a transient bridge from runtime stream events to the active feedback status line.

**Step 4: Run test to verify it passes**

Run: `npm test -- popup-helpers.test.ts`

**Step 5: Commit**

```bash
git add extension/popup/popup.js extension/popup/popup-helpers.js extension/tests/popup-helpers.test.ts
git commit -m "fix: show feedback refresh progress in popup"
```

### Task 4: Verify end-to-end behavior

**Files:**
- Test: `extension/tests/chat-layout.test.ts`
- Test: `extension/tests/popup-helpers.test.ts`
- Test: `extension/tests/popup-copy.test.ts`

**Step 1: Run targeted tests**

Run: `npm test -- popup-helpers.test.ts chat-layout.test.ts popup-copy.test.ts popup-layout.test.ts`

**Step 2: Manual verification**

- Open the popup `Chat` tab and send a message.
- Confirm the status line progresses through reply wait and sync stages.
- Submit a recommendation feedback action and confirm the card status line updates immediately and continues through sync/background stages.

**Step 3: Commit**

```bash
git add docs/plans/2026-03-15-submission-progress-visibility-design.md docs/plans/2026-03-15-submission-progress-visibility.md
git commit -m "docs: capture popup submission progress plan"
```
