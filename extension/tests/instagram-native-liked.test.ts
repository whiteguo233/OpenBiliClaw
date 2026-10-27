import assert from "node:assert/strict";
import test from "node:test";

const events = Object.assign(new EventTarget(), {
  location: new URL("https://www.instagram.com/your_activity/interactions/likes/?openbiliclaw_instagram_task=1"),
  scrollTo: () => {},
});
let loadedDocumentAccount = { id: "42", username: "fixture" };
globalThis.window = events as unknown as Window & typeof globalThis;
globalThis.location = events.location as unknown as Location;
globalThis.document = {
  title: "Likes • Instagram",
  body: { innerText: "" },
  documentElement: { scrollHeight: 0 },
  querySelectorAll: (selector: string) => selector === "script[data-sjs]"
    ? [{ textContent: JSON.stringify({ __bbox: { define: [["PolarisViewer", [], {
      id: loadedDocumentAccount.id,
      data: {
        id: loadedDocumentAccount.id,
        username: loadedDocumentAccount.username,
      },
    }, 1]] } }) }]
    : [],
} as unknown as Document;
globalThis.chrome = { runtime: { sendMessage: async () => ({ ok: true }) } } as unknown as typeof chrome;
const { executeInstagramBootstrap } = await import("../src/content/instagram/task-executor.ts");
const { clearInstagramResponseBuffer, INSTAGRAM_RESPONSE_EVENT } = await import("../src/content/instagram/response-buffer.ts");
const { parseInstagramLikedBloks } = await import("../src/main/instagram-liked-bloks.ts");

function identityFetch(input: RequestInfo | URL): Promise<Response> {
  if (String(input).endsWith("web_form_data/")) return Promise.resolve(new Response(JSON.stringify({ status: "ok", form_data: { username: "fixture" } }), { headers: { "content-type": "application/json" } }));
  assert.equal(String(input), "https://www.instagram.com/");
  return Promise.resolve(new Response('<script data-sjs>{"__bbox":{"define":[["PolarisViewer",[],{"id":"42","data":{"id":"42","username":"fixture"}},1]]}}</script>', { headers: { "content-type": "text/html" } }));
}

test("nonempty Likes without new rows stops boundedly without claiming the last page", async () => {
  clearInstagramResponseBuffer();
  const envelope = parseInstagramLikedBloks({ payload: { layout: { bloks_payload: { tree: {
    on_bind: '(bk.action.map.Make, (bk.action.array.Make, "media_id", "media_code", "media_type"), (bk.action.array.Make, "123", "EXAMPLE", "1"))',
  } } } } });
  events.dispatchEvent(new CustomEvent(INSTAGRAM_RESPONSE_EVENT, { detail: JSON.stringify(envelope) }));
  let identityRequests = 0;
  const result = await executeInstagramBootstrap({ id: "fixture", claim_token: "fixture", type: "bootstrap_events",
    scopes: ["instagram_liked"], max_items_per_scope: 20, max_pages_per_scope: 20 }, async input => {
    identityRequests += 1;
    return identityFetch(input);
  }, async () => {});
  assert.ok(identityRequests <= 11, `Repeated the same snapshot through ${identityRequests} identity requests`);
  assert.equal(result.status, "partial");
  assert.equal(result.error, "instagram_liked:progress_stalled");
  assert.equal(result.items.length, 1);
  assert.equal(result.scope_complete.instagram_liked, false);
});

test("new native Likes rows reset the no-progress bound", async () => {
  clearInstagramResponseBuffer();
  function emit(id: string): void {
    const envelope = parseInstagramLikedBloks({ payload: { layout: { bloks_payload: { tree: {
      on_bind: `(bk.action.map.Make, (bk.action.array.Make, "media_id", "media_code", "media_type"), (bk.action.array.Make, "${id}", "CODE${id}", "1"))`,
    } } } } });
    events.dispatchEvent(new CustomEvent(INSTAGRAM_RESPONSE_EVENT, { detail: JSON.stringify(envelope) }));
  }
  emit("123");
  const pages: number[] = [];
  const original = chrome.runtime.sendMessage;
  chrome.runtime.sendMessage = (async (message: { action: string; data: { scope?: string; page?: number } }) => {
    if (message.action === "INSTAGRAM_TASK_PROGRESS" && message.data.scope === "instagram_liked" && message.data.page) {
      pages.push(message.data.page);
      if (message.data.page === 3) emit("456");
    }
    return { ok: true };
  }) as typeof chrome.runtime.sendMessage;
  try {
    const result = await executeInstagramBootstrap({ id: "fixture", claim_token: "fixture", type: "bootstrap_events",
      scopes: ["instagram_liked"], max_items_per_scope: 20, max_pages_per_scope: 20 }, identityFetch, async () => {});
    assert.deepEqual(result.items.map(item => item.id).sort(), ["123", "456"]);
    assert.equal(result.error, "instagram_liked:progress_stalled");
    assert.equal(result.scope_complete.instagram_liked, false);
    assert.equal(Math.max(...pages), 7);
  } finally {
    chrome.runtime.sendMessage = original;
  }
});

test("native liked 429 stops saved/following and does not become empty", async () => {
  clearInstagramResponseBuffer();
  events.dispatchEvent(new CustomEvent(INSTAGRAM_RESPONSE_EVENT, { detail: JSON.stringify({ route: "liked", items: [], error: "rate_limited", shape_valid: false }) }));
  let identityRequests = 0;
  const result = await executeInstagramBootstrap({ id: "fixture", claim_token: "fixture", type: "bootstrap_events",
    scopes: ["instagram_liked", "instagram_saved", "instagram_following"], max_items_per_scope: 10, max_pages_per_scope: 1 }, async input => {
    identityRequests += 1;
    return identityFetch(input);
  }, async () => {});
  assert.equal(result.status, "failed");
  assert.equal(result.error, "rate_limited");
  assert.deepEqual(result.scope_complete, { instagram_liked: false, instagram_saved: false, instagram_following: false });
  assert.equal(identityRequests, 2, "known buffered 429 stops after initial identity without guard probes");
});

test("native liked items retain scope but cap never implies complete history", async () => {
  clearInstagramResponseBuffer();
  const envelope = parseInstagramLikedBloks({ payload: { layout: { bloks_payload: { tree: {
    on_bind: '(bk.action.map.Make, (bk.action.array.Make, "media_id", "media_code", "media_type"), (bk.action.array.Make, "123", "EXAMPLE", "1"))',
  } } } } });
  events.dispatchEvent(new CustomEvent(INSTAGRAM_RESPONSE_EVENT, { detail: JSON.stringify(envelope) }));
  const result = await executeInstagramBootstrap({ id: "fixture", claim_token: "fixture", type: "bootstrap_events",
    scopes: ["instagram_liked"], max_items_per_scope: 1, max_pages_per_scope: 1 }, identityFetch, async () => {});
  assert.equal(result.status, "partial");
  assert.equal(result.error, "instagram_liked:item_cap_reached");
  assert.equal(result.items[0]?.scope, "instagram_liked");
  assert.equal(result.scope_complete.instagram_liked, false);
});

test("native liked discards buffered rows when the account changes during observation", async () => {
  clearInstagramResponseBuffer();
  const envelope = parseInstagramLikedBloks({ payload: { layout: { bloks_payload: { tree: {
    on_bind: '(bk.action.map.Make, (bk.action.array.Make, "media_id", "media_code", "media_type"), (bk.action.array.Make, "123", "EXAMPLE", "1"))',
  } } } } });
  events.dispatchEvent(new CustomEvent(INSTAGRAM_RESPONSE_EVENT, { detail: JSON.stringify(envelope) }));
  let currentAccount = { id: "42", username: "first" };
  let viewerRequests = 0;
  loadedDocumentAccount = currentAccount;
  const result = await executeInstagramBootstrap(
    { id: "fixture", claim_token: "fixture", type: "bootstrap_events",
      scopes: ["instagram_liked"], max_items_per_scope: 10, max_pages_per_scope: 1 },
    async input => {
      const url = String(input);
      if (url.endsWith("web_form_data/")) {
        return new Response(JSON.stringify({ status: "ok", form_data: {
          username: currentAccount.username,
        } }), { headers: { "content-type": "application/json" } });
      }
      const { id, username } = currentAccount;
      assert.equal(url, "https://www.instagram.com/");
      viewerRequests += 1;
      return new Response(`<script data-sjs>{"__bbox":{"define":[["PolarisViewer",[],{"id":"${id}","data":{"id":"${id}","username":"${username}"}},1]]}}</script>`,
        { headers: { "content-type": "text/html" } });
    }, async () => {
      if (viewerRequests >= 2) currentAccount = { id: "84", username: "second" };
    });
  loadedDocumentAccount = { id: "42", username: "fixture" };

  assert.equal(result.status, "failed");
  assert.equal(result.error, "instagram_account_changed");
  assert.equal(result.account_id, "84");
  assert.deepEqual(result.items, []);
  assert.deepEqual(result.scope_counts, { instagram_liked: 0 });
  assert.deepEqual(result.scope_complete, { instagram_liked: false });
});

test("native liked rejects an early buffer when the loaded page predates initial identity", async () => {
  clearInstagramResponseBuffer();
  const envelope = parseInstagramLikedBloks({ payload: { layout: { bloks_payload: { tree: {
    on_bind: '(bk.action.map.Make, (bk.action.array.Make, "media_id", "media_code", "media_type"), (bk.action.array.Make, "123", "EXAMPLE", "1"))',
  } } } } });
  events.dispatchEvent(new CustomEvent(INSTAGRAM_RESPONSE_EVENT, { detail: JSON.stringify(envelope) }));
  loadedDocumentAccount = { id: "42", username: "first" };
  const result = await executeInstagramBootstrap({ id: "fixture", claim_token: "fixture",
    type: "bootstrap_events", scopes: ["instagram_liked"], max_items_per_scope: 10,
    max_pages_per_scope: 1 }, async input => {
    if (String(input).endsWith("web_form_data/")) {
      return new Response(JSON.stringify({ status: "ok", form_data: { username: "second" } }),
        { headers: { "content-type": "application/json" } });
    }
    return new Response('<script data-sjs>{"__bbox":{"define":[["PolarisViewer",[],{"id":"84","data":{"id":"84","username":"second"}},1]]}}</script>',
      { headers: { "content-type": "text/html" } });
  }, async () => {});
  loadedDocumentAccount = { id: "42", username: "fixture" };

  assert.equal(result.status, "failed");
  assert.equal(result.error, "account_identity_missing");
  assert.deepEqual(result.items, []);
  assert.deepEqual(result.scope_counts, { instagram_liked: 0 });
  assert.deepEqual(result.scope_complete, { instagram_liked: false });
});

test("native Bloks empty flows through identity, durable progress and bootstrap without mobile liked GET", async () => {
  clearInstagramResponseBuffer();
  const envelope = parseInstagramLikedBloks({ payload: { layout: { bloks_payload: {
    tree: { "bk.components.Text": { text: "你没有赞过任何内容" } },
  } } } });
  events.dispatchEvent(new CustomEvent(INSTAGRAM_RESPONSE_EVENT, { detail: JSON.stringify(envelope) }));
  const urls: string[] = [];
  const result = await executeInstagramBootstrap({ id: "fixture", claim_token: "fixture",
    type: "bootstrap_events", scopes: ["instagram_liked"], max_items_per_scope: 10, max_pages_per_scope: 1 },
  async input => {
    const url = String(input); urls.push(url);
    if (url.endsWith("web_form_data/")) return new Response(JSON.stringify({ status: "ok", form_data: { username: "fixture" } }), { headers: { "content-type": "application/json" } });
    assert.equal(url, "https://www.instagram.com/");
    return new Response('<script data-sjs>{"__bbox":{"define":[["PolarisViewer",[],{"id":"42","data":{"id":"42","username":"fixture"}},1]]}}</script>', { headers: { "content-type": "text/html" } });
  }, async () => {});
  assert.equal(result.status, "empty", JSON.stringify(result));
  assert.equal(result.scope_complete.instagram_liked, true);
  assert.equal(urls.length, 5, "initial identity plus before/after/final viewer checks");
  assert.equal(urls.some(url => url.includes("/api/v1/feed/liked/")), false);
});
