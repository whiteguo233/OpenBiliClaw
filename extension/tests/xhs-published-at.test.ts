import test from "node:test";
import assert from "node:assert/strict";

import {
  extractNoteIdFromUrl,
  extractPublishedAtFromState,
  extractPublishedTimesFromPayload,
  mergePublishedTimes,
} from "../src/shared/xhs-published-at.ts";

const NOTE_ID = "6aa69c06000000002603b0f1";
const OTHER_ID = "6aa4530f0000000028001b16";
const STATE = {
  note: {
    noteDetailMap: {
      [NOTE_ID]: {
        currentTime: 1789998316770,
        note: { noteId: NOTE_ID, time: 1789303814000, lastUpdateTime: 1789303916000 },
      },
      [OTHER_ID]: { note: { noteId: OTHER_ID, time: 1789183829000 } },
    },
  },
};

test("extractNoteIdFromUrl handles explore and discovery URLs", () => {
  assert.equal(
    extractNoteIdFromUrl(`https://www.xiaohongshu.com/explore/${NOTE_ID}?xsec_token=x`),
    NOTE_ID,
  );
  assert.equal(
    extractNoteIdFromUrl(`https://www.xiaohongshu.com/discovery/item/${NOTE_ID}`),
    NOTE_ID,
  );
  assert.equal(extractNoteIdFromUrl("https://www.xiaohongshu.com/explore"), "");
});

test("extractPublishedTimesFromPayload reads user_posted time and nested note_card time", () => {
  const payload = {
    data: {
      notes: [
        { note_id: NOTE_ID, time: 1789303814000 },
        { note_id: OTHER_ID, note_card: { time: 1751909764000 } },
        { note_id: "not-a-note-id", time: 1789303814000 },
        { note_id: "6aa936cf000000001001ec56", note_card: { title: "no time" } },
      ],
    },
  };
  assert.deepEqual(extractPublishedTimesFromPayload(payload), [
    { note_id: NOTE_ID, published_at: 1789303814000 },
    { note_id: OTHER_ID, published_at: 1751909764000 },
  ]);
});

test("extractPublishedAtFromState reads noteDetailMap time", () => {
  assert.equal(extractPublishedAtFromState(STATE, NOTE_ID), 1789303814000);
  assert.equal(extractPublishedAtFromState(STATE, OTHER_ID), 1789183829000);
  assert.equal(extractPublishedAtFromState(STATE, "000000000000000000000000"), undefined);
  assert.equal(extractPublishedAtFromState(null, NOTE_ID), undefined);
  assert.equal(extractPublishedAtFromState({ note: {} }, NOTE_ID), undefined);
});

test("mergePublishedTimes fills notes by note_id or URL and skips existing values", () => {
  const notes = [
    { note_id: NOTE_ID, published_at: undefined as number | undefined },
    { url: `https://www.xiaohongshu.com/explore/${OTHER_ID}` },
    { note_id: "6aa936cf000000001001ec56", published_at: 1 },
  ];
  const merged = mergePublishedTimes(notes, [
    { note_id: NOTE_ID, published_at: 1789303814000 },
    { note_id: OTHER_ID, published_at: 1789183829000 },
    { note_id: "6aa936cf000000001001ec56", published_at: 2 },
  ]);
  assert.equal(merged, 2);
  assert.equal(notes[0].published_at, 1789303814000);
  assert.equal(notes[1].published_at, 1789183829000);
  assert.equal(notes[2].published_at, 1);
});
