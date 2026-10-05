import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../popup/popup.js", import.meta.url), "utf8");
const shared = readFileSync(new URL("../../src/openbiliclaw/web/shared/agent-chat.js", import.meta.url), "utf8");

function popupFunction(name: string) {
  const start = source.search(new RegExp(`(?:async )?function ${name}\\(`));
  assert.ok(start >= 0, name);
  return source.slice(start, source.indexOf("\n}\n", start) + 2);
}

// Keep the actual stream callback, shared event reducer and reply renderer.
// Only the browser's element primitives and network transport are substituted.
function harness() {
  class Element {
    innerHTML = "";
    classList = { add() {}, remove() {} };
    content: Element | null = null;
    querySelector(selector: string) { return selector === ".chat-content" ? this.content : null; }
  }
  const parts = new Map<string, Element>();
  const messages = new Element();
  const put = (turnId: string, part: string, content: string) => {
    const item = new Element();
    item.content = new Element();
    item.content.innerHTML = content;
    parts.set(`${turnId}:${part}`, item);
    return item;
  };
  let emit: (name: string, data: unknown) => void = () => {};
  let finish: (result: unknown) => void = () => {};
  const stream = new Promise((resolve) => { finish = resolve; });
  const context = vm.createContext({
    HTMLElement: Element, elements: { chatMessages: messages },
    popupChatSessionId: "a", CHAT_SESSION: "popup", popupAgentRuns: new Map(),
    popupStreamingTurnIds: new Set(), activeChatPolls: new Map(), dialogueTurnsById: new Map(),
    streamAgentChatTurn(options: { onEvent: typeof emit }) { emit = options.onEvent; return stream; },
    updatePopupAgentRunDom() {}, refreshChatApprovals() {}, refreshChatSessions() {},
    popupAgentRunFor(turn: { turn_id: string }) { return context.popupAgentRuns.get(turn.turn_id); },
    popupAgentRunHasContent() { return false; },
    isCardTurn() { return false; }, isQuestionTurn() { return false; }, isAgentTaskSummaryTurn() { return false; },
    findChatTurnElement(turnId: string, part: string) { return parts.get(`${turnId}:${part}`) || null; },
    appendChatMessage(_role: string, content: string, options: { turnId: string; part: string }) {
      return put(options.turnId, options.part, content);
    },
    appendChatThinkingPlaceholder(turnId: string) { return put(turnId, "assistant", "正在想"); },
    renderMarkdown(text: string) { return text; }, scrollChatMessagesToBottom() {},
  });
  vm.runInContext(shared, context);
  Object.assign(context, context.OpenBiliClawAgentChat);
  for (const name of ["replaceChatThinkingPlaceholder", "renderChatTurn", "popupDriveAgentStream"]) {
    vm.runInContext(popupFunction(name), context);
  }
  const turn = { turn_id: "turn-a", session_id: "a", scope: "chat", status: "pending", message: "你好" };
  context.renderChatTurn(turn);
  const completed: unknown[] = [];
  const running = context.popupDriveAgentStream(turn, {
    onUpdate: context.renderChatTurn, onDone: (result: unknown) => completed.push(result),
  });
  return {
    context, turn, parts, completed, running, emit: (name: string, data: unknown) => emit(name, data),
    finish: (reply = "最终答案") => finish({ reply }),
    reply: () => parts.get("turn-a:assistant")?.content?.innerHTML,
    redraw: () => { parts.clear(); context.renderChatTurn(turn); },
  };
}

test("popup renders agent deltas before completion and retains them during history redraw", async () => {
  const h = harness();
  try {
    h.emit("delta", { step: 1, text: "你" });
    assert.equal(h.reply(), "你", "first token must replace the thinking placeholder before done");
    assert.equal(h.completed.length, 0);
    h.emit("delta", { step: 1, text: "好" });
    assert.equal(h.reply(), "你好");
    h.redraw();
    assert.equal(h.reply(), "你好", "history refresh must retain the active stream text");
  } finally { h.finish(); await h.running; }
});

test("popup separates intermediate hop text and uses the authoritative final reply", async () => {
  const h = harness();
  try {
    h.emit("delta", { step: 1, text: "先查询" });
    h.emit("thinking", { step: 1, text: "先查询" });
    h.emit("tool_call", { step: 1, tool_name: "get_profile", arguments: {} });
    h.emit("delta", { step: 2, text: "查到了" });
    assert.equal(h.reply(), "查到了", "new hop must not append to intermediate text");
    h.emit("final", { step: 2, text: "整理后的最终答案" });
    h.emit("done", { reply: "fallback reply" });
    assert.equal(h.completed.length, 0, "SSE events must not run completion side effects twice");
    h.finish("fallback reply");
    await h.running;
    assert.equal(h.reply(), "整理后的最终答案");
    assert.equal(h.completed.length, 1);
    assert.equal(h.context.popupStreamingTurnIds.size, 0);
  } finally { h.finish(); await h.running; }
});

test("popup accepts a done-only reply and does not append late deltas to it", async () => {
  const h = harness();
  try {
    h.emit("delta", { step: 1, text: "未完成的草稿" });
    h.emit("done", { reply: "后端最终答复" });
    h.emit("delta", { step: 1, text: "不应追加" });
    h.finish("后端最终答复");
    await h.running;
    assert.equal(h.reply(), "后端最终答复");
    assert.equal(h.completed.length, 1);
  } finally { h.finish(); await h.running; }
});

test("popup keeps streamed text and completion attached to their originating session", async () => {
  const h = harness();
  try {
    h.context.popupChatSessionId = "b";
    h.parts.clear();
    h.emit("delta", { step: 1, text: "只属于会话 A" });
    assert.equal(h.parts.size, 0, "late tokens must not insert bubbles into session B");
    h.context.popupChatSessionId = "a";
    h.redraw();
    assert.equal(h.reply(), "只属于会话 A", "returning to A restores its unfinished answer");
    h.context.popupChatSessionId = "b";
    h.parts.clear();
    h.finish();
    await h.running;
    assert.equal(h.parts.size, 0, "completion must not insert bubbles into session B");
  } finally { h.finish(); await h.running; }
});
