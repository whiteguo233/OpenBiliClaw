import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../popup/popup.js", import.meta.url), "utf8");
const start = source.indexOf("async function handlePendingConfirmationOpen(");
assert.ok(start >= 0);
const handler = source.slice(start, source.indexOf("\n}\n", start) + 2);

function harness(failure = false) {
  const events: string[] = [];
  const state = { pendingConfirmations: { expanded: true } };
  const context = vm.createContext({
    state, CHAT_SESSION: "popup", dialogueCardActionAbortController: new AbortController(),
    elements: { chatInput: { focus() { events.push("focus"); } } },
    async executePendingConfirmationOpen() {
      if (failure) throw new Error("offline");
      return { turn_id: "card-1", payload: { type: "card" } };
    },
    renderPendingConfirmations() { events.push(`pending:${state.pendingConfirmations.expanded}`); },
    renderChatTurn() { events.push(`card:${state.pendingConfirmations.expanded}`); },
    scrollChatMessagesToBottom() { events.push("scroll"); },
    async selectDialogueContext() { events.push("context"); },
    async hydrateChatHistory() { events.push("history"); },
    async refreshPendingConfirmations() { events.push("refresh"); },
    setHint() {}, isQuestionTurn() { return false; },
  });
  vm.runInContext(handler, context);
  const button = { dataset: { confirmationRef: "ref-1" }, disabled: false, textContent: "打开" };
  return { context, button, events, state };
}

test("opening a pending card frees the popup chat viewport before rendering and scrolling it", async () => {
  const h = harness();
  await h.context.handlePendingConfirmationOpen(h.button);
  assert.equal(h.state.pendingConfirmations.expanded, false,
    "the expanded inbox can otherwise squeeze the message region to zero height");
  assert.deepEqual(h.events.slice(0, 4), ["pending:false", "card:false", "scroll", "context"]);
});

test("a failed card open keeps the pending list visible and its button retryable", async () => {
  const h = harness(true);
  await h.context.handlePendingConfirmationOpen(h.button);
  assert.equal(h.state.pendingConfirmations.expanded, true);
  assert.equal(h.button.disabled, false);
  assert.equal(h.button.textContent, "打开");
  assert.deepEqual(h.events, []);
});
