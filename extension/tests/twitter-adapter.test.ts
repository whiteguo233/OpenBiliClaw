/**
 * Tests for the X (Twitter) platform adapter.
 *
 * Mirrors the xiaohongshu / bilibili adapter tests: URL → content id,
 * page-type detection, action inference, and event metadata shape.
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  twitterAdapter,
  detectTwitterPageType,
  extractTweetId,
} from "../src/shared/platforms/twitter.ts";

test("twitterAdapter exposes the correct source identity", () => {
  assert.equal(twitterAdapter.sourcePlatform, "twitter");
});

test("extractContentId pulls the tweet id from a status URL", () => {
  assert.equal(
    twitterAdapter.extractContentId("https://x.com/h/status/1790000000000000001"),
    "1790000000000000001",
  );
  assert.equal(
    extractTweetId("https://twitter.com/someone/status/1234567890"),
    "1234567890",
  );
  // /status/<id>/photo/1 and other suffixes still resolve to the id.
  assert.equal(
    extractTweetId("https://x.com/h/status/1790000000000000009/photo/1"),
    "1790000000000000009",
  );
});

test("extractContentId returns null for non-status URLs", () => {
  assert.equal(twitterAdapter.extractContentId("https://x.com/home"), null);
  assert.equal(twitterAdapter.extractContentId("https://x.com/someone"), null);
  assert.equal(twitterAdapter.extractContentId("https://x.com/search?q=rust"), null);
});

test("detectTwitterPageType classifies home / status / profile / search", () => {
  assert.equal(detectTwitterPageType("https://x.com/home"), "home");
  assert.equal(detectTwitterPageType("https://x.com/"), "home");
  assert.equal(
    detectTwitterPageType("https://x.com/h/status/1790000000000000001"),
    "status",
  );
  assert.equal(detectTwitterPageType("https://x.com/search?q=rust"), "search");
  assert.equal(detectTwitterPageType("https://x.com/explore"), "search");
  // A bare handle path is a profile.
  assert.equal(detectTwitterPageType("https://x.com/someone"), "profile");
});

test("inferActionType maps engagement aria-labels", () => {
  assert.equal(
    twitterAdapter.inferActionType({ text: "Like", ariaLabel: "Like", className: "" }),
    "like",
  );
  assert.equal(
    twitterAdapter.inferActionType({ text: "", ariaLabel: "Bookmark", className: "" }),
    "favorite",
  );
  assert.equal(
    twitterAdapter.inferActionType({ text: "Repost", ariaLabel: "Repost", className: "" }),
    "share",
  );
  assert.equal(
    twitterAdapter.inferActionType({ text: "", ariaLabel: "Share", className: "" }),
    "share",
  );
  assert.equal(
    twitterAdapter.inferActionType({ text: "Reply", ariaLabel: "Reply", className: "" }),
    "comment",
  );
  assert.equal(
    twitterAdapter.inferActionType({ text: "Nothing", ariaLabel: null, className: "" }),
    null,
  );
});

test("buildEventMetadata returns the tweet_id", () => {
  assert.deepEqual(
    twitterAdapter.buildEventMetadata("https://x.com/h/status/1790000000000000001"),
    { tweet_id: "1790000000000000001" },
  );
  assert.deepEqual(twitterAdapter.buildEventMetadata("https://x.com/home"), {
    tweet_id: null,
  });
});

test("adapter exposes card + search-input selectors and no video selector", () => {
  assert.equal(typeof twitterAdapter.cardSelector, "string");
  assert.ok(twitterAdapter.cardSelector.length > 0);
  assert.equal(typeof twitterAdapter.searchInputSelector, "string");
  assert.ok(twitterAdapter.searchInputSelector.length > 0);
  assert.equal(twitterAdapter.videoSelector, null);
});
