import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { parseInstagramObservedPayload } from "../src/main/instagram-response-tap.ts";

test("response tap parses the observed public-topic envelope into bounded normalized rows", () => {
  const edges = Array.from({ length: 100 }, (_, index) => ({
    node: {
      id: String(index + 1),
      code: `CODE${index + 1}`,
      __typename: index % 2 ? "XDTGraphVideo" : "XDTGraphImage",
      caption: { text: `Caption ${index + 1}` },
      display_uri: `https://scontent.example/${index + 1}.jpg`,
      user: { id: "42", username: "creator" },
      cookie: "must-not-cross",
      raw_body: "must-not-cross",
    },
  }));
  const envelopes = parseInstagramObservedPayload({
    data: {
      xig_logged_out_popular_search_media_info: {
        edges,
        page_info: {
          has_next_page: false,
          end_cursor: "opaque-cursor",
        },
      },
    },
  });
  assert.equal(envelopes.length, 1);
  assert.equal(envelopes[0].route, "topic");
  assert.equal(envelopes[0].collection_id, "topic:xig_logged_out_popular_search_media_info");
  assert.equal(envelopes[0].items.length, 80);
  assert.equal(envelopes[0].observed_count, 80);
  assert.equal(envelopes[0].rejected_count, 0);
  assert.equal(envelopes[0].shape_valid, true);
  assert.equal(envelopes[0].cursor, "opaque-cursor");
  assert.equal(envelopes[0].has_next_page, false);
  assert.equal(envelopes[0].affirmative_terminal, true);
  assert.equal(envelopes[0].items[0].url, "https://www.instagram.com/p/CODE1/");
  assert.doesNotMatch(JSON.stringify(envelopes), /must-not-cross|cookie|raw_body/);
});

test("response tap reports a non-empty collection whose rows all fail normalization", () => {
  const [envelope] = parseInstagramObservedPayload({
    data: {
      edge_hashtag_to_media: {
        edges: [{ node: { id: "missing-code" } }, { node: { shortcode: "missing-id" } }],
        page_info: { has_next_page: false },
      },
    },
  });
  assert.equal(envelope.observed_count, 2);
  assert.equal(envelope.rejected_count, 2);
  assert.equal(envelope.shape_valid, false);
  assert.deepEqual(envelope.items, []);
});

test("response tap recognizes creator timeline envelope and ignores unrelated JSON", () => {
  assert.deepEqual(parseInstagramObservedPayload({ status: "ok", items: [{ id: "raw" }] }), []);
  const envelopes = parseInstagramObservedPayload({
    data: {
      user: {
        edge_owner_to_timeline_media: {
          edges: [{ node: { id: "9", shortcode: "CREATOR", __typename: "GraphSidecar" } }],
          page_info: { has_next_page: true, end_cursor: "next" },
        },
      },
    },
  });
  assert.equal(envelopes[0].route, "creator");
  assert.equal(envelopes[0].items[0].content_type, "carousel");
  assert.equal(envelopes[0].has_next_page, true);
});

test("MAIN tap installs bounded replay only inside a marked task tab", async () => {
  const source = await readFile(new URL("../src/main/instagram-response-tap.ts", import.meta.url), "utf8");
  assert.match(source, /if \(isInstagramTaskTabLocation\(\)\)/);
  assert.match(source, /MAX_REPLAY_ENVELOPES = 16/);
  assert.match(source, /MAX_RESPONSE_BYTES = 4 \* 1024 \* 1024/);
  assert.match(source, /INSTAGRAM_REPLAY_EVENT/);
  assert.match(source, /replay\.shift\(\)/);
  assert.doesNotMatch(source, /document\.cookie|Authorization|Cookie:/);
});
