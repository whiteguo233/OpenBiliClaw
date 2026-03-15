# Chat Input UX Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix the popup chat textarea clipping and add fast keyboard submit with `Enter`.

**Architecture:** Keep the chat form structure intact. Add a small keyboard helper in `popup-helpers.js`, bind it in `popup.js`, and narrow the CSS fix to the chat textarea so other textareas are unaffected.

**Tech Stack:** Vanilla JS, static HTML/CSS, Node test runner

---

### Task 1: Lock the expected chat UI contract

**Files:**
- Modify: `extension/tests/chat-layout.test.ts`
- Modify: `extension/tests/popup-helpers.test.ts`
- Test: `extension/tests/chat-layout.test.ts`
- Test: `extension/tests/popup-helpers.test.ts`

**Step 1: Write the failing tests**

- Assert the chat textarea keeps its own padding and line-height.
- Assert `Enter` submits, `Shift+Enter` does not, and composition does not submit.

**Step 2: Run test to verify it fails**

Run: `npm test -- chat-layout.test.ts popup-helpers.test.ts`

**Step 3: Write minimal implementation**

- Add the CSS contract for `.chat-input`.
- Add a helper for deciding whether the keypress should submit.

**Step 4: Run test to verify it passes**

Run: `npm test -- chat-layout.test.ts popup-helpers.test.ts`

**Step 5: Commit**

```bash
git add extension/tests/chat-layout.test.ts extension/tests/popup-helpers.test.ts extension/popup/popup.html extension/popup/popup-helpers.js extension/popup/popup.js
git commit -m "fix: improve popup chat input behavior"
```

### Task 2: Wire keyboard submission into the popup chat form

**Files:**
- Modify: `extension/popup/popup-helpers.js`
- Modify: `extension/popup/popup.js`
- Test: `extension/tests/popup-helpers.test.ts`

**Step 1: Write the failing test**

- Use a small pure helper instead of DOM-heavy test scaffolding.

**Step 2: Run test to verify it fails**

Run: `npm test -- popup-helpers.test.ts`

**Step 3: Write minimal implementation**

- Export a helper that returns `true` only for plain `Enter`.
- Bind `keydown` on the textarea and trigger `requestSubmit()`.

**Step 4: Run test to verify it passes**

Run: `npm test -- popup-helpers.test.ts`

**Step 5: Commit**

```bash
git add extension/popup/popup-helpers.js extension/popup/popup.js extension/tests/popup-helpers.test.ts
git commit -m "fix: support enter-to-send in popup chat"
```

### Task 3: Verify the full chat flow

**Files:**
- Modify: `extension/tests/chat-layout.test.ts`
- Test: `extension/tests/chat-layout.test.ts`
- Test: `extension/tests/popup-copy.test.ts`

**Step 1: Run targeted tests**

Run: `npm test -- chat-layout.test.ts popup-helpers.test.ts popup-copy.test.ts`

**Step 2: Manual verification**

- Open `extension/popup/popup.html`
- Switch to `Chat`
- Type a short message and confirm the text is not clipped
- Press `Shift+Enter` and confirm newline remains in the textarea
- Press `Enter` and confirm the form submits

**Step 3: Commit**

```bash
git add docs/plans/2026-03-15-chat-input-ux-design.md docs/plans/2026-03-15-chat-input-ux.md
git commit -m "docs: capture popup chat input ux plan"
```
