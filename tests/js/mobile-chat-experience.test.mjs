import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const mobile = readFileSync(new URL("../../src/openbiliclaw/web/js/views/chat.js", import.meta.url), "utf8");
const shared = readFileSync(new URL("../../src/openbiliclaw/web/shared/dialogue-confirmation.js", import.meta.url), "utf8");
function fn(name) {
  const start = mobile.search(new RegExp(`(?:async )?function ${name}\\(`));
  assert.ok(start >= 0, name);
  return mobile.slice(start, mobile.indexOf("\n}\n", start) + 2);
}

test("mobile immediately renders the sent message after existing history", async () => {
  let release;
  let visible;
  const context = vm.createContext({
    sending: false, activeSessionId: "a", currentSkillName: () => "", dialogueContextSelection: null,
    retainedDraft: "", turns: [{ turn_id: "old", scope: "chat", created_at: "2020-01-01", message: "旧消息" }],
    document: { getElementById: () => ({ value: "你好" }) }, userScrolledUp: true,
    setDialogueStatus() {}, chatSession: () => ({ session: "popup" }), agentLoopAvailable: true,
    startChatTurn: () => new Promise(resolve => { release = resolve; }), driveAgentStream: async () => {},
    render() { visible = context.OpenBiliClawDialogueConfirmation.selectDialogueTurns(context.turns); },
  });
  vm.runInContext(shared, context);
  vm.runInContext(fn("handleSend"), context);
  const request = context.handleSend();
  assert.equal(visible.at(-1).message, "你好");
  assert.equal(visible.at(-1).status, "pending");
  assert.equal(context.sending, true);
  release({});
  await request;
});

test("mobile renders the final reply before history refresh and respects the reader", async () => {
  let rendered;
  const context = vm.createContext({
    activeSessionId: "a", turns: [{ turn_id: "t", scope: "chat", message: "你好", status: "pending" }],
    streamingTurnIds: new Set(["t"]), agentRunsByTurnId: new Map([["t", { sessionId: "a" }]]),
    sending: true, userScrolledUp: true, setDialogueStatus() {},
    refreshAfterChatTurn() {}, refreshPendingConfirmations() {}, refreshApprovals() {},
    window: { setTimeout() {} }, loadHistory: async () => {},
    render() { rendered = context.window.OpenBiliClawDialogueConfirmation.renderTurnMarkup(context.turns[0]); },
  });
  vm.runInContext(shared, context);
  vm.runInContext(fn("finalizeAgentTurn") + "\n" + fn("finalizeAgentTurnSuccess"), context);
  await context.finalizeAgentTurnSuccess("t", "你好呀");
  assert.match(rendered, /你好呀/);
  assert.equal(context.userScrolledUp, true);
  assert.equal(context.sending, false);
});

test("history repaint keeps the editing textarea attached with its selection", () => {
  const removals = [];
  class Element {
    constructor(selector, children = []) {
      this.selector = selector;
      this.children = [];
      for (const child of children) this.appendChild(child);
    }
    appendChild(child) {
      if (child.parent) child.remove();
      child.parent = this;
      this.children.push(child);
    }
    remove() {
      removals.push(this);
      this.parent.children = this.parent.children.filter(child => child !== this);
      this.parent = null;
    }
    insertBefore(child, before) {
      if (child.parent) child.remove();
      this.children.splice(this.children.indexOf(before), 0, child);
      child.parent = this;
    }
    querySelector(selector) {
      for (const child of this.children) {
        if (child.selector === selector) return child;
        const nested = child.querySelector(selector);
        if (nested) return nested;
      }
      return null;
    }
  }
  const input = Object.assign(new Element("#chat-input"), {
    value: "第一行\n第二行", selectionStart: 1, selectionEnd: 5, scrollTop: 16,
  });
  const send = Object.assign(new Element("#chat-send"), { disabled: true });
  const composer = new Element(".chat-input-row", [input, send]);
  const shell = new Element(".chat-shell", [new Element("old messages"), composer]);
  const root = new Element("root", [shell]);
  const nextInput = Object.assign(new Element("#chat-input"), { value: input.value, placeholder: "聊聊" });
  const nextSend = Object.assign(new Element("#chat-send"), { disabled: false });
  const nextShell = new Element(".chat-shell", [
    new Element("header"), new Element("messages"),
    new Element(".chat-input-row", [nextInput, nextSend]), new Element("status"),
  ]);
  const context = vm.createContext({});
  vm.runInContext(fn("mountChatShell"), context);
  const actual = context.mountChatShell(root, nextShell);
  assert.equal(actual, input);
  assert.deepEqual([input.selectionStart, input.selectionEnd, input.scrollTop], [1, 5, 16]);
  assert.ok(!removals.includes(composer) && !removals.includes(input) && !removals.includes(shell));
  assert.equal(send.disabled, false);
  assert.deepEqual(shell.children.map(child => child.selector), ["header", "messages", ".chat-input-row", "status"]);
});

test("a late history refresh does not restore an obsolete scroll position", async () => {
  let release;
  class Element {}
  const messages = Object.assign(new Element(), { scrollTop: 100, scrollHeight: 2000, clientHeight: 500 });
  const context = vm.createContext({
    state: { online: true }, activeSessionId: "a", historyRefreshInFlight: false,
    historyRefreshGeneration: 0, historyLoaded: true, lastHistorySignature: null,
    HTMLElement: Element, document: { getElementById: () => messages },
    isChatMessagesNearBottom: () => false, window: { requestAnimationFrame: callback => callback() },
    fetchChatSessionDetail: () => new Promise(resolve => { release = resolve; }),
    fetchPendingConfirmations: async () => ({ count: 0, items: [] }), refreshApprovals: async () => false,
    pendingConfirmations: { count: 0, items: [] }, patchState() {},
    normalizeChatTurn: value => value, trackPendingHistoryTurn() {}, chatHistorySignature: JSON.stringify,
    agentRunsByTurnId: new Map(), dialogueContextSelection: null, validateDialogueContext: async () => {}, render() {},
  });
  vm.runInContext(fn("loadHistory"), context);
  const request = context.loadHistory();
  messages.scrollTop = 600; // the reader scrolls while the request is in flight
  release({ items: [{ turn_id: "t", status: "completed" }] });
  await request;
  assert.equal(messages.scrollTop, 600);
});

for (const sample of [
  { name: "normal focused phone", height: 844, layout: 844, focused: true, expected: false },
  { name: "keyboard shrinks layout", height: 400, layout: 400, focused: true, expected: true },
  { name: "Safari visual viewport only", height: 400, layout: 844, focused: false, expected: true },
  { name: "pinch zoom", height: 400, layout: 844, focused: true, scale: 2, expected: false },
  { name: "another tab", height: 400, layout: 844, focused: true, tab: "recommend", expected: false },
]) {
  test(`mobile viewport preserves space: ${sample.name}`, () => {
    let compact;
    const styles = new Map();
    const context = vm.createContext({
      state: { activeTab: sample.tab || "chat" },
      window: { innerHeight: sample.layout, visualViewport: { height: sample.height, scale: sample.scale || 1, offsetTop: 12 } },
      document: {
        documentElement: { clientHeight: sample.layout },
        activeElement: { matches: () => sample.focused },
        body: { classList: { toggle: (_name, value) => { compact = value; } }, style: { setProperty: (name, value) => styles.set(name, value) } },
      },
    });
    vm.runInContext(fn("syncChatViewport"), context);
    context.syncChatViewport();
    assert.equal(compact, sample.expected);
    assert.equal(styles.get("--chat-visible-height"), `${sample.height}px`);
    assert.equal(styles.get("--chat-visible-top"), "12px");
  });
}
