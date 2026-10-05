import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../popup/popup.js", import.meta.url), "utf8");
function popupFunction(name: string) {
  const start = source.search(new RegExp(`(?:async )?function ${name}\\(`));
  assert.ok(start >= 0, name);
  return source.slice(start, source.indexOf("\n}\n", start) + 2);
}

function harness({ switchDuringCreate = false, dropStream = false } = {}) {
  class Element {
    value = "请解释二分查找";
    textContent = "";
    disabled = false;
    listeners = new Map<string, (event: unknown) => unknown>();
    addEventListener(name: string, fn: (event: unknown) => unknown) { this.listeners.set(name, fn); }
    focus() {}
  }
  const calls: string[] = [];
  const timers = new Map<number, () => unknown>();
  let timerId = 0;
  const turn = { turn_id: "created-1", session_id: "a", session: "popup", scope: "chat", status: "pending", message: "请解释二分查找" };
  const elements = { chatForm: new Element(), chatInput: new Element(), chatSendButton: new Element() };
  const context = vm.createContext({
    HTMLFormElement: Element, HTMLTextAreaElement: Element, HTMLButtonElement: Element,
    elements, state: { online: true }, popupPersonaSaving: new Set(), popupChatSessionId: "a",
    popupStreamingTurnIds: new Set(), activeChatPolls: new Map(), agentLoopAvailable: true,
    CHAT_SESSION: "popup", CHAT_POLL_INTERVAL_MS: 1200, CHAT_POLL_DEADLINE_MS: 180000,
    chatPlaceholderTimer: null, retainedChatDraft: "", dialogueContextSelection: null,
    currentPopupSkill() { return "taste-companion"; }, createClientTurnId() { return turn.turn_id; },
    appendChatMessage() {}, appendChatThinkingPlaceholder() { return null; },
    setHint() {}, setChatStatus() {}, getSubmissionProgressMessage() { return ""; },
    renderChatTurn() {}, async refreshAfterChatTurn() {},
    async startChatTurn() {
      calls.push("create");
      if (switchDuringCreate) context.popupChatSessionId = "b";
      return turn;
    },
    async fetchChatTurn() { calls.push("get"); return { ...turn, status: "completed", reply: "durable answer" }; },
    async popupDriveAgentStream(row: typeof turn) {
      assert.equal(row.session_id, "a");
      calls.push("agent-stream");
      if (dropStream) throw new Error("connection reset");
    },
    async streamChatTurn() { calls.push("legacy-stream"); },
    window: {
      setInterval() { return 1; },
      setTimeout(fn: () => unknown) { const id = ++timerId; timers.set(id, fn); return id; },
      clearTimeout(id: number) { timers.delete(id); },
    },
  });
  for (const name of ["pollChatTurnUntilSettled", "bindChat"]) vm.runInContext(popupFunction(name), context);
  context.bindChat();
  return {
    context, turn, calls, timers,
    async submit() {
      await elements.chatForm.listeners.get("submit")!({ preventDefault() {} });
      await new Promise(resolve => setImmediate(resolve));
    },
  };
}

test("popup submit streams the just-created turn before any GET can wake the fallback worker", async () => {
  const h = harness();
  await h.submit();
  assert.deepEqual(h.calls, ["create", "agent-stream"]);
});

test("a submission whose session changed still starts its own stream without an eager GET", async () => {
  const h = harness({ switchDuringCreate: true });
  await h.submit();
  assert.deepEqual(h.calls, ["create", "agent-stream"]);
});

test("after a dropped stream popup polls the durable result instead of running the legacy model path", async () => {
  const h = harness({ dropStream: true });
  await h.submit();
  assert.deepEqual(h.calls, ["create", "agent-stream"]);
  assert.equal(h.timers.size, 1, "a dropped connection must schedule recovery");
  await [...h.timers.values()][0]();
  assert.deepEqual(h.calls, ["create", "agent-stream", "get"]);
  assert.equal(h.context.activeChatPolls.size, 0);
});
