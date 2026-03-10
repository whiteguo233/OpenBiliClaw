import test from "node:test";
import assert from "node:assert/strict";

import { refreshRecommendations } from "../popup/popup-api.js";

test("refreshRecommendations posts to refresh endpoint", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return {
          ok: true,
          accepted: true,
          state: "running",
          reason: "started",
        };
      },
    };
  };

  const result = await refreshRecommendations();

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/recommendations/refresh");
  assert.equal(calls[0].options.method, "POST");
  assert.deepEqual(result, {
    ok: true,
    accepted: true,
    state: "running",
    reason: "started",
  });
});
