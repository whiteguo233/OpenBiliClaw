import assert from "node:assert/strict";
import test from "node:test";

test("mobile source DELETE accepts 204 without JSON parsing and preserves request policy", async (t) => {
  const originalLocation = globalThis.location;
  globalThis.location = new URL("https://claw.example/m/");
  t.after(() => {
    if (originalLocation === undefined) delete globalThis.location;
    else globalThis.location = originalLocation;
  });
  const { requestJson } = await import("../../src/openbiliclaw/web/js/api.js");
  const response = new Response(null, { status: 204 });
  const json = t.mock.method(response, "json", async () => {
    throw new Error("204 has no JSON body");
  });
  const fetchMock = t.mock.method(globalThis, "fetch", async () => response);

  const result = await requestJson("/custom-sources/site-1", {
    method: "DELETE", timeoutMs: 30_000, headers: { "X-Test": "preserved" },
  });

  assert.equal(result, null);
  assert.equal(json.mock.callCount(), 0);
  assert.equal(fetchMock.mock.callCount(), 1);
  const [url, options] = fetchMock.mock.calls[0].arguments;
  assert.equal(url, "https://claw.example/api/custom-sources/site-1");
  assert.equal(options.method, "DELETE");
  assert.equal(options.credentials, "same-origin");
  assert.equal(options.headers["X-OBC-Auth"], "1");
  assert.equal(options.headers["X-Test"], "preserved");
  assert.ok(options.signal instanceof AbortSignal);
});
