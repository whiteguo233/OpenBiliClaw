import test from "node:test";
import assert from "node:assert/strict";

import {
  detectYoutubePageType,
  extractYoutubeVideoId,
  youtubeAdapter,
} from "../src/shared/platforms/youtube.ts";

test("youtubeAdapter exposes source identity and video selector", () => {
  assert.equal(youtubeAdapter.sourcePlatform, "youtube");
  assert.equal(youtubeAdapter.videoSelector, "video");
});

test("detectYoutubePageType classifies common YouTube pages", () => {
  assert.equal(detectYoutubePageType("https://www.youtube.com/watch?v=dQw4w9WgXcQ"), "video");
  assert.equal(detectYoutubePageType("https://www.youtube.com/results?search_query=cat"), "search");
  assert.equal(detectYoutubePageType("https://www.youtube.com/@openai"), "channel");
  assert.equal(detectYoutubePageType("https://www.youtube.com/"), "home");
});

test("extractYoutubeVideoId reads watch, shorts and youtu.be URLs", () => {
  assert.equal(
    extractYoutubeVideoId("https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
    "dQw4w9WgXcQ",
  );
  assert.equal(
    extractYoutubeVideoId("https://www.youtube.com/shorts/dQw4w9WgXcQ"),
    "dQw4w9WgXcQ",
  );
  assert.equal(extractYoutubeVideoId("https://youtu.be/dQw4w9WgXcQ"), "dQw4w9WgXcQ");
  assert.equal(extractYoutubeVideoId("https://www.youtube.com/results?search_query=cat"), null);
});

test("youtubeAdapter infers unified action types", () => {
  assert.equal(youtubeAdapter.inferActionType({ text: "Like", ariaLabel: "Like this video", className: "" }), "like");
  assert.equal(youtubeAdapter.inferActionType({ text: "", ariaLabel: "Dislike this video", className: "" }), "dislike");
  assert.equal(youtubeAdapter.inferActionType({ text: "Share", ariaLabel: null, className: "" }), "share");
  assert.equal(youtubeAdapter.inferActionType({ text: "Subscribe", ariaLabel: null, className: "" }), "follow");
  assert.equal(youtubeAdapter.inferActionType({ text: "Comment", ariaLabel: null, className: "" }), "comment");
  assert.equal(youtubeAdapter.inferActionType({ text: "Save", ariaLabel: null, className: "" }), "favorite");
});

test("youtubeAdapter metadata carries video_id", () => {
  assert.deepEqual(
    youtubeAdapter.buildEventMetadata("https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
    { video_id: "dQw4w9WgXcQ" },
  );
});
