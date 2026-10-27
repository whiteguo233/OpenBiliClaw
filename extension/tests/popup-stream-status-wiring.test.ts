import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { createRuntimeStreamClient } from "../popup/popup-stream.js";
import { getPopupState } from "../popup/popup-helpers.js";

// Execute the real popup connection callback, replacing only network/render
// boundaries. The full installed-extension DOM flow is covered by live QA.
const popupSource = readFileSync(new URL("../popup/popup.js", import.meta.url), "utf8");
const connectSource = popupSource.match(/function connectRuntimeStream\(\) \{[\s\S]*?\n\}/)?.[0];
assert.ok(connectSource);

for (const hasCards of [false, true]) {
  test(`first stream catch-up updates popup status and ${hasCards ? "preserves existing cards" : "renders terminal empty feedback"}`, async () => {
    const card = { id: "instagram:test", title: "existing card" };
    const cards = hasCards ? [card] : [];
    const state = {
      online: true,
      recommendations: cards,
      runtimeStatus: { initialized: true, manual_refresh_state: "running" },
    };
    let socket: { onopen?: () => void } = {};
    let fetched = 0;
    let poolRenders = 0;
    let readyHints = 0;
    let expensiveReloads = 0;
    const emptyStates: string[] = [];
    const terminal = { initialized: true, manual_refresh_state: "failed", manual_refresh_message: "内容发现请求失败" };
    const context = {
      state,
      runtimeStreamClient: null,
      fetchRuntimeStatus: async () => { fetched += 1; return terminal; },
      getPopupState,
      renderPoolStatus: () => { poolRenders += 1; },
      renderRecommendationState: (value: { kind: string }) => { emptyStates.push(value.kind); },
      renderReadyRecommendationHint: () => { readyHints += 1; },
      backendConnectionCoordinator: { markStreamConnected: () => ({ reconnected: false }) },
      scheduleRecommendationsRefresh: () => { expensiveReloads += 1; },
      createRuntimeStreamClient: (options: object) => createRuntimeStreamClient({
        ...options,
        backendUrl: "http://127.0.0.1:8420/api",
        WebSocketImpl: class { constructor() { socket = this; } close() {} } as never,
      }),
    };
    vm.runInNewContext(`${connectSource}\nconnectRuntimeStream();`, context);
    socket.onopen?.();
    await new Promise((resolve) => setTimeout(resolve, 0));

    assert.equal(fetched, 1, "actual popup supplies the authoritative status fetch");
    assert.equal(state.runtimeStatus, terminal);
    assert.equal(poolRenders, 1);
    assert.equal(expensiveReloads, 0);
    assert.equal(state.recommendations, cards, "status-only catch-up preserves list identity");
    assert.deepEqual(emptyStates, hasCards ? [] : ["discovery_failed"]);
    assert.equal(readyHints, hasCards ? 1 : 0);
    if (hasCards) assert.equal(state.recommendations[0], card, "existing card identity stays intact");
    (context.runtimeStreamClient as { disconnect(): void } | null)?.disconnect();
  });
}
