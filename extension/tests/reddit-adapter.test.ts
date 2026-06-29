import test from "node:test";
import assert from "node:assert/strict";

import {
  buildRedditTargetMetadata,
  detectRedditPageType,
  extractRedditContentId,
  extractRedditSubreddit,
  inferRedditActionType,
  redditAdapter,
} from "../src/shared/platforms/reddit.ts";

test("detectRedditPageType classifies post, subreddit, search and home pages", () => {
  assert.equal(
    detectRedditPageType("https://www.reddit.com/r/LocalLLaMA/comments/abc123/title/"),
    "post",
  );
  assert.equal(detectRedditPageType("https://www.reddit.com/r/LocalLLaMA/"), "subreddit");
  assert.equal(detectRedditPageType("https://www.reddit.com/search/?q=agents"), "search");
  assert.equal(detectRedditPageType("https://www.reddit.com/"), "home");
});

test("extractRedditContentId returns canonical fullname-style ids", () => {
  assert.equal(
    extractRedditContentId("https://www.reddit.com/r/LocalLLaMA/comments/abc123/title/"),
    "t3_abc123",
  );
  assert.equal(extractRedditContentId("https://redd.it/abc123"), "t3_abc123");
});

test("extractRedditSubreddit returns subreddit names from post and community URLs", () => {
  assert.equal(
    extractRedditSubreddit("https://www.reddit.com/r/LocalLLaMA/comments/abc123/title/"),
    "LocalLLaMA",
  );
  assert.equal(extractRedditSubreddit("https://www.reddit.com/r/MachineLearning/"), "MachineLearning");
});

test("inferRedditActionType maps Reddit action controls to unified events", () => {
  assert.equal(inferRedditActionType({ text: "", ariaLabel: "Upvote", className: "" }), "like");
  assert.equal(inferRedditActionType({ text: "Save", ariaLabel: "", className: "" }), "favorite");
  assert.equal(inferRedditActionType({ text: "32 comments", ariaLabel: "", className: "" }), "comment");
  assert.equal(inferRedditActionType({ text: "Share", ariaLabel: "", className: "" }), "share");
});

test("redditAdapter stamps source and content metadata", () => {
  assert.equal(redditAdapter.sourcePlatform, "reddit");
  assert.deepEqual(
    redditAdapter.buildEventMetadata("https://www.reddit.com/r/LocalLLaMA/comments/abc123/title/"),
    { content_id: "t3_abc123", post_id: "abc123", subreddit: "LocalLLaMA" },
  );
});

test("buildRedditTargetMetadata extracts post identity from a feed card link", () => {
  const anchor = {
    href: "https://www.reddit.com/r/LocalLLaMA/comments/abc123/local_first_agents/",
    getAttribute(name: string) {
      return name === "href" ? this.href : null;
    },
  } as unknown as Element;
  const card = {
    getAttribute() {
      return null;
    },
    querySelector(selector: string) {
      return selector.includes("/comments/") ? anchor : null;
    },
  } as unknown as Element;
  const target = {
    getAttribute() {
      return null;
    },
    closest(selector: string) {
      if (selector.includes("shreddit-post") || selector.includes("post-container")) return card;
      return null;
    },
  } as unknown as Element;

  assert.deepEqual(
    buildRedditTargetMetadata(target, "https://www.reddit.com/r/LocalLLaMA/"),
    {
      content_id: "t3_abc123",
      post_id: "abc123",
      subreddit: "LocalLLaMA",
      target_url: "https://www.reddit.com/r/LocalLLaMA/comments/abc123/local_first_agents/",
    },
  );
});

test("buildRedditTargetMetadata falls back to card post-id attributes", () => {
  const card = {
    getAttribute(name: string) {
      return (
        {
          "post-id": "def456",
          subreddit: "ArtificialInteligence",
        } as Record<string, string>
      )[name] ?? null;
    },
    querySelector() {
      return null;
    },
  } as unknown as Element;
  const target = {
    getAttribute() {
      return null;
    },
    closest(selector: string) {
      if (selector.includes("shreddit-post") || selector.includes("post-container")) return card;
      return null;
    },
  } as unknown as Element;

  assert.deepEqual(
    buildRedditTargetMetadata(target, "https://www.reddit.com/r/ArtificialInteligence/"),
    {
      content_id: "t3_def456",
      post_id: "def456",
      subreddit: "ArtificialInteligence",
    },
  );
});
