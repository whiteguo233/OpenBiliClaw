import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

import desktopCore from "../../src/openbiliclaw/web/desktop/assets/js/chat-agent-core.js";
await import("../../src/openbiliclaw/web/shared/agent-chat.js");
globalThis.location = { protocol: "http:", host: "test.local" };
const mobile = await import("../../src/openbiliclaw/web/js/api.js");
const popup = await import("../../extension/popup/popup-api.js");
const desktopSource = readFileSync(new URL("../../src/openbiliclaw/web/desktop/assets/js/app.js", import.meta.url), "utf8");

function desktopFunction(name) {
  const start = desktopSource.search(new RegExp(`    (?:async )?function ${name}\\(`));
  assert.ok(start >= 0, name);
  return desktopSource.slice(start, desktopSource.indexOf("\n    }\n", start) + 6);
}

function streamFetch(frames) {
  return async () => new Response(frames.join(""), {
    headers: { "Content-Type": "text/event-stream" },
  });
}

function frame(type, data) {
  return `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`;
}

for (const [surface, api] of [["mobile", mobile], ["popup", popup]]) {
  test(`${surface}: EOF before done cannot report a completed agent turn`, async () => {
    const previous = globalThis.fetch;
    globalThis.fetch = streamFetch([frame("final", { text: "尚未持久化的答复" })]);
    try {
      await assert.rejects(api.streamAgentChatTurn({ message: "你好" }), /中断/);
    } finally { globalThis.fetch = previous; }
  });

  test(`${surface}: done acknowledges the completed agent turn`, async () => {
    const previous = globalThis.fetch;
    globalThis.fetch = streamFetch([frame("done", { reply: "已完成", turn_id: "t1" })]);
    try {
      const done = await api.streamAgentChatTurn({ message: "你好" });
      assert.equal(done.reply, "已完成");
    } finally { globalThis.fetch = previous; }
  });
}

test("desktop: EOF before done cannot finalize the agent stream", async () => {
  const context = vm.createContext({
    chatAgentCore: desktopCore, TextDecoder,
    fetch: streamFetch([frame("final", { text: "尚未持久化的答复" })]),
    getApiBase: () => "/api", DEFAULT_API_BASE: "/api",
    ENDPOINTS: { chatAgentStream: "/chat/agent/stream" }, SHARED_CHAT_SESSION: "popup",
    handleAgentStreamEvent(name, _data, live) { if (name === "done") live.finished = true; },
  });
  vm.runInContext(desktopFunction("streamAgentChatTurn"), context);
  await assert.rejects(context.streamAgentChatTurn({
    turnId: "t1", message: "你好", live: { finished: false },
  }), /中断/);
});
