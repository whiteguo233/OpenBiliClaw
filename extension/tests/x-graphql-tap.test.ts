/**
 * Tests for the X (Twitter) MAIN-world GraphQL tap.
 *
 * The tap wraps window.fetch / XMLHttpRequest in MAIN world and can't be
 * exercised under node --test without a DOM, but the two pure functions
 * carry all the interesting logic:
 *
 *   - classifyXResponseUrl(url) — maps a request URL to an event type by
 *     GraphQL **operation name** (the hashed queryId is treated as a
 *     wildcard, because X rotates it every ~2-4 weeks).
 *   - parseXMutation(captured) — extracts {type, tweet_id} (or
 *     {type, user_id} for follow) from a captured request/response.
 *
 * Fixtures under tests/fixtures/x/*.json are SYNTHETIC placeholders to be
 * replaced with real DevTools captures during the cookie smoke — see the
 * README in that directory.
 */

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import {
  classifyXResponseUrl,
  parseXMutation,
  installXTap,
  type CapturedXRequest,
} from "../src/main/x-graphql-tap.ts";

const FIXTURE_DIR = join(dirname(fileURLToPath(import.meta.url)), "fixtures", "x");

function loadFixture(name: string): CapturedXRequest {
  const raw = readFileSync(join(FIXTURE_DIR, name), "utf8");
  const parsed = JSON.parse(raw) as Record<string, unknown>;
  return {
    url: String(parsed.url ?? ""),
    requestBody: typeof parsed.requestBody === "string" ? parsed.requestBody : "",
    responseBody: typeof parsed.responseBody === "string" ? parsed.responseBody : "",
  };
}

// ── classifyXResponseUrl — match the operation name, ignore queryId ──────

test("classifyXResponseUrl matches by GraphQL operation name, not queryId", () => {
  assert.equal(
    classifyXResponseUrl("https://x.com/i/api/graphql/abc123/FavoriteTweet"),
    "like",
  );
  assert.equal(
    classifyXResponseUrl("https://x.com/i/api/graphql/zzz/CreateRetweet"),
    "share",
  );
  assert.equal(
    classifyXResponseUrl("https://x.com/i/api/graphql/q/CreateBookmark"),
    "favorite",
  );
});

test("classifyXResponseUrl is queryId-agnostic (rotation resilience)", () => {
  // Two different hashed ids must classify the same way.
  assert.equal(
    classifyXResponseUrl("https://x.com/i/api/graphql/lI07N6Otwv1PhnEgXILM7A/FavoriteTweet"),
    "like",
  );
  assert.equal(
    classifyXResponseUrl("https://x.com/i/api/graphql/TOTALLY-DIFFERENT-HASH/FavoriteTweet"),
    "like",
  );
});

test("classifyXResponseUrl maps CreateTweet (reply) and TweetDetail", () => {
  assert.equal(
    classifyXResponseUrl("https://x.com/i/api/graphql/q/CreateTweet"),
    "comment",
  );
  assert.equal(
    classifyXResponseUrl("https://x.com/i/api/graphql/q/TweetDetail?variables=%7B%7D"),
    "view",
  );
});

test("classifyXResponseUrl handles the REST follow path", () => {
  assert.equal(
    classifyXResponseUrl("https://x.com/i/api/1.1/friendships/create.json"),
    "follow",
  );
  // twitter.com mirror works too.
  assert.equal(
    classifyXResponseUrl("https://twitter.com/i/api/1.1/friendships/create.json"),
    "follow",
  );
});

test("classifyXResponseUrl ignores discovery timelines (not engagement)", () => {
  assert.equal(classifyXResponseUrl("https://x.com/i/api/graphql/q/HomeTimeline"), null);
  assert.equal(classifyXResponseUrl("https://x.com/i/api/graphql/q/SearchTimeline"), null);
  assert.equal(classifyXResponseUrl("https://x.com/i/api/graphql/q/UserTweets"), null);
  assert.equal(classifyXResponseUrl("https://x.com/i/api/graphql/q/HomeLatestTimeline"), null);
  assert.equal(classifyXResponseUrl(""), null);
  assert.equal(classifyXResponseUrl("https://example.com/"), null);
});

// ── parseXMutation — extract target id + event from captured bodies ──────

test("parseXMutation: FavoriteTweet → like with tweet_id", () => {
  assert.deepEqual(parseXMutation(loadFixture("favorite_tweet.json")), {
    type: "like",
    tweet_id: "1790000000000000001",
  });
});

test("parseXMutation: CreateBookmark → favorite", () => {
  const out = parseXMutation(loadFixture("create_bookmark.json"));
  assert.equal(out?.type, "favorite");
  assert.equal(out?.tweet_id, "1790000000000000002");
});

test("parseXMutation: CreateRetweet → share", () => {
  const out = parseXMutation(loadFixture("create_retweet.json"));
  assert.equal(out?.type, "share");
  assert.equal(out?.tweet_id, "1790000000000000003");
});

test("parseXMutation: reply CreateTweet → comment, carries in_reply_to id", () => {
  const out = parseXMutation(loadFixture("reply_create_tweet.json"));
  assert.equal(out?.type, "comment");
  assert.equal(out?.tweet_id, "1790000000000000004");
});

test("parseXMutation: a top-level CreateTweet without reply is NOT captured", () => {
  // A brand-new tweet (no in_reply_to_tweet_id) is the user authoring
  // content, not engaging with someone else's — drop it.
  const out = parseXMutation({
    url: "https://x.com/i/api/graphql/q/CreateTweet",
    requestBody: JSON.stringify({ variables: { tweet_text: "hello world" } }),
    responseBody: "",
  });
  assert.equal(out, null);
});

test("parseXMutation: follow → follow with user_id (no tweet_id)", () => {
  const out = parseXMutation(loadFixture("follow.json"));
  assert.equal(out?.type, "follow");
  assert.equal(out?.user_id, "44196397");
  assert.equal(out?.tweet_id, undefined);
});

test("parseXMutation: TweetDetail → view with focalTweetId from the URL", () => {
  const out = parseXMutation(loadFixture("tweet_detail.json"));
  assert.equal(out?.type, "view");
  assert.equal(out?.tweet_id, "1790000000000000006");
});

test("parseXMutation: returns null for an unrelated URL", () => {
  assert.equal(
    parseXMutation({
      url: "https://x.com/i/api/graphql/q/HomeTimeline",
      requestBody: "{}",
      responseBody: "{}",
    }),
    null,
  );
});

test("parseXMutation: tolerates malformed bodies without throwing", () => {
  const out = parseXMutation({
    url: "https://x.com/i/api/graphql/q/FavoriteTweet",
    requestBody: "not json at all",
    responseBody: "",
  });
  // No tweet_id recoverable → null rather than a crash.
  assert.equal(out, null);
});

// ── pass-through: the tap must never mutate the page's request ────────────

test("installXTap is observation-only — it never mutates the outgoing request", async () => {
  // Minimal fake window exposing fetch + XMLHttpRequest. We record the
  // exact (input, init) the page passed and assert the tap forwards them
  // byte-identical to the original fetch.
  let seenInput: unknown = null;
  let seenInit: unknown = null;
  let tapDidMutateRequest = false;

  const originalInput = "https://x.com/i/api/graphql/q/FavoriteTweet";
  const originalInit = { method: "POST", body: '{"variables":{"tweet_id":"1"}}' };

  const fakeResponse = {
    clone() {
      return {
        text: async () => '{"data":{"favorite_tweet":"Done"}}',
      };
    },
  };

  const fakeWindow = {
    location: { origin: "https://x.com" },
    fetch: async (input: unknown, init: unknown) => {
      seenInput = input;
      seenInit = init;
      return fakeResponse as unknown as Response;
    },
    XMLHttpRequest: function () {} as unknown,
    postMessage: () => {
      /* swallow */
    },
    addEventListener: () => {
      /* no-op */
    },
  };
  // Give the fake XHR a prototype with open/send so installXTap can wrap it.
  (fakeWindow.XMLHttpRequest as { prototype: Record<string, unknown> }).prototype = {
    open() {
      /* no-op */
    },
    send() {
      /* no-op */
    },
  };

  installXTap(fakeWindow as unknown as Window);

  await (fakeWindow.fetch as (i: unknown, j: unknown) => Promise<unknown>)(
    originalInput,
    originalInit,
  );

  // The tap must pass input + init straight through, unchanged.
  if (seenInput !== originalInput) tapDidMutateRequest = true;
  if (seenInit !== originalInit) tapDidMutateRequest = true;

  assert.equal(tapDidMutateRequest, false);
  assert.equal(seenInput, originalInput);
  assert.equal(seenInit, originalInit);
});
