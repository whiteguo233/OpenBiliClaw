import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { isInstagramResponseUrl, parseInstagramObservedPayload } from "../src/main/instagram-response-tap.ts";

test("creator discovery never emits private or unknown-visibility media", () => {
  for (const isPrivate of [true, undefined]) {
    const [envelope] = parseInstagramObservedPayload({ data: { user: {
      id: "42", username: "private_fixture", is_private: isPrivate,
      edge_owner_to_timeline_media: {
        edges: [{ node: { id: "123", code: "FIXTURE", user: {
          id: "42", username: "private_fixture", is_private: isPrivate,
        } } }], page_info: { has_next_page: false },
      },
    } } }, "/private_fixture/");
    assert.deepEqual(envelope.items, []);
    assert.equal(envelope.shape_valid, false);
    assert.equal(envelope.affirmative_terminal, undefined);
  }
});

test("creator privacy evidence must match both numeric identity and username", () => {
  const payload = { data: { user: { edge_owner_to_timeline_media: {
    edges: [{ node: { id: "123", code: "FIXTURE", user: {
      id: "42", username: "public_fixture",
    } } }], page_info: { has_next_page: false },
  } } } };
  for (const proof of [
    { id: "99", keys: { id: "99" }, username: "public_fixture" },
    { id: "42", keys: { id: "42" }, username: "another_creator" },
  ]) {
    const [envelope] = parseInstagramObservedPayload(payload, "/public_fixture/", proof);
    assert.deepEqual(envelope.items, []);
    assert.equal(envelope.affirmative_terminal, undefined);
  }
  const [matching] = parseInstagramObservedPayload(payload, "/public_fixture/", {
    id: "42", keys: { id: "42" }, username: "PUBLIC_FIXTURE",
  });
  assert.equal(matching.items.length, 1);
});

test("authenticated topic flattens only observed media grids and preserves cursor", () => {
  const [envelope] = parseInstagramObservedPayload({ data: { xdt_fbsearch__top_serp_graphql: {
    edges: [
      { node: { __typename: "XDTTopSerpHeaderUnit" } },
      { node: { __typename: "XDTTopSerpAccountsHCMUnit" } },
      { node: { __typename: "XDTTopSerpMediaGridUnit", items: [
        { pk: "123", code: "SYNTHETIC", media_type: 2, taken_at: 1700000000,
          user: { pk: "45", username: "fixture", is_private: false }, caption: { text: "fixture caption" }, organic_tracking_token: "must-not-cross" },
      ] } },
    ], page_info: { has_next_page: true, end_cursor: "opaque" },
  } } }, "/popular/technology/");
  assert.equal(envelope?.items.length, 1);
  assert.equal(envelope?.items[0].author_name, "fixture");
  assert.equal(envelope?.items[0].published_at, "2023-11-14T22:13:20.000Z");
  assert.equal(envelope?.cursor, "opaque");
  assert.equal(envelope?.has_next_page, true);
  assert.equal(envelope?.shape_valid, true);
  assert.equal(envelope?.collection_id, "topic:xdt_fbsearch__top_serp_graphql");
  assert.doesNotMatch(JSON.stringify(envelope), /must-not-cross/);
});

test("authenticated topic is route scoped and unknown nodes are not empty success", () => {
  const payload = { data: { xdt_fbsearch__top_serp_graphql: {
    edges: [{ node: { __typename: "NewUnknownUnit" } }], page_info: { has_next_page: false },
  } } };
  assert.deepEqual(parseInstagramObservedPayload(payload, "/direct/inbox/"), []);
  assert.deepEqual(parseInstagramObservedPayload(payload), []);
  assert.equal(parseInstagramObservedPayload(payload, "/popular/music/")[0]?.shape_valid, false);
});

test("authenticated topic bounds grids, rejects private rows and preserves anonymous parsing", () => {
  const media = { pk: "1", code: "EXAMPLE", media_type: 1, user: { is_private: false } };
  const parse = (items: unknown[]) => parseInstagramObservedPayload({ data: { xdt_fbsearch__top_serp_graphql: {
    edges: [{ node: { __typename: "XDTTopSerpMediaGridUnit", items } }], page_info: { has_next_page: false },
  } } }, "/popular/music/")[0];
  assert.equal(parse([{ ...media, user: { is_private: true } }]).shape_valid, false);
  assert.equal(parse([{ ...media, user: {} }]).shape_valid, false);
  const bounded = parse(Array.from({ length: 100 }, (_, i) => ({ ...media, pk: String(i + 1) })));
  assert.equal(bounded.items.length, 80);
  assert.equal(bounded.affirmative_terminal, undefined);
  assert.equal(bounded.has_next_page, undefined);
  assert.equal(parseInstagramObservedPayload({ data: { xig_logged_out_popular_search_media_info: {
    edges: [], page_info: { has_next_page: false },
  } } }, "/popular/music/")[0].affirmative_terminal, true);
});

test("response tap includes the real web graphql/query transport only on Instagram", () => {
  assert.equal(isInstagramResponseUrl("https://www.instagram.com/graphql/query"), true);
  assert.equal(isInstagramResponseUrl("https://www.instagram.com/graphql/query/"), true);
  assert.equal(isInstagramResponseUrl("https://www.instagram.com/api/graphql"), true);
  assert.equal(isInstagramResponseUrl("https://evil.test/graphql/query"), false);
  assert.equal(isInstagramResponseUrl("https://www.instagram.com/direct/inbox/"), false);
  assert.equal(isInstagramResponseUrl("https://www.instagram.com/async/wbloks/fetch/?appid=com.instagram.privacy.activity_center.liked_media_screen"), true);
  assert.equal(isInstagramResponseUrl("https://www.instagram.com/async/wbloks/fetch/?appid=com.instagram.privacy.activity_center.unlike"), false);
});

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
  assert.equal(envelopes[0].has_next_page, undefined, "truncated responses are not complete");
  assert.equal(envelopes[0].affirmative_terminal, undefined);
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
          edges: [{ node: { id: "9", shortcode: "CREATOR", __typename: "GraphSidecar", user: { is_private: false } } }],
          page_info: { has_next_page: true, end_cursor: "next" },
        },
      },
    },
  });
  assert.equal(envelopes[0].route, "creator");
  assert.equal(envelopes[0].items[0].content_type, "carousel");
  assert.equal(envelopes[0].has_next_page, true);
});

test("response tap recognizes the current public-profile Polaris timeline envelope", () => {
  const envelopes = parseInstagramObservedPayload({
    require: [{
      result: {
        data: {
          xig_user_by_username: {
            polaris_ordered_timeline_connection: {
              edges: [{
                cursor: "opaque-edge-cursor",
                node: {
                  __typename: "XIGPolarisVideoMedia",
                  pk: "987654321",
                  code: "POLARIS1",
                  accessibility_caption: "A public reel",
                  caption: { text: "Creator caption" },
                  seo_canonical_url: "https://www.instagram.com/reel/POLARIS1/",
                  display_uri: "https://scontent.example/polaris.jpg",
                  media_type: 2,
                  product_type: "clips",
                  user: { pk: "42", username: "public_creator", id: "42" },
                  id: "987654321",
                },
              }],
              page_info: { end_cursor: "next-page", has_next_page: true },
            },
          },
        },
      },
    }],
  }, "/public_creator/", { id: "42", keys: { pk: "42", id: "42" }, username: "public_creator" });

  assert.equal(envelopes.length, 1);
  assert.equal(envelopes[0].route, "creator");
  assert.equal(envelopes[0].collection_id, "creator:polaris_ordered_timeline_connection");
  assert.equal(envelopes[0].items.length, 1);
  assert.equal(envelopes[0].items[0].id, "987654321");
  assert.equal(envelopes[0].items[0].content_type, "reel");
  assert.equal(envelopes[0].items[0].url, "https://www.instagram.com/reel/POLARIS1/");
  assert.equal(envelopes[0].items[0].description, "Creator caption");
  assert.equal(envelopes[0].items[0].author_name, "public_creator");
  assert.equal(envelopes[0].cursor, "next-page");
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
