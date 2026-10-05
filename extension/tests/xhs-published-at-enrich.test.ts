import test from "node:test";
import assert from "node:assert/strict";

import {
  enrichNotesWithPublishedAt,
  isXhsNoteUrl,
} from "../src/content/xhs/published-at-enrich.ts";

const A = "6aa69c06000000002603b0f1";
const B = "6aa4530f0000000028001b16";
const C = "6aa936cf000000001001ec56";

test("isXhsNoteUrl only accepts exact same-family note URLs", () => {
  assert.equal(isXhsNoteUrl(`https://www.xiaohongshu.com/explore/${A}?xsec_token=x`), true);
  assert.equal(isXhsNoteUrl(`https://www.xiaohongshu.com/discovery/item/${B}`), true);
  assert.equal(isXhsNoteUrl("https://www.xiaohongshu.com/explore"), false);
  assert.equal(isXhsNoteUrl(`https://evil.example.com/explore/${A}`), false);
  assert.equal(isXhsNoteUrl(`https://xiaohongshu.com.evil.example/explore/${A}`), false);
});

test("enrichNotesWithPublishedAt respects maxNotes and skips existing/non-xhs notes", async () => {
  const notes = [
    { url: `https://www.xiaohongshu.com/explore/${A}?xsec_token=a` },
    { url: `https://www.xiaohongshu.com/explore/${B}?xsec_token=b` },
    { url: `https://www.xiaohongshu.com/explore/${C}?xsec_token=c` },
    { url: `https://evil.example.com/explore/${C}` },
    { url: `https://www.xiaohongshu.com/explore/${C}`, published_at: 123 },
  ];
  const calls: string[] = [];
  const stats = { targets: 0, attempted: 0, enriched: 0 };
  const loader = async (url: string, noteId: string): Promise<number | undefined> => {
    calls.push(noteId);
    return noteId === A ? 1789303814000 : undefined;
  };

  const enriched = await enrichNotesWithPublishedAt(notes, {
    maxNotes: 2,
    concurrency: 2,
    timeoutMs: 1000,
    loader,
    stats,
  });

  assert.equal(enriched, 1);
  assert.deepEqual(stats, { targets: 2, attempted: 2, enriched: 1 });
  assert.deepEqual(calls.sort(), [A, B].sort());
  assert.equal(notes[0].published_at, 1789303814000);
  assert.equal(notes[1].published_at, undefined);
  assert.equal(notes[2].published_at, undefined);
  assert.equal(notes[4].published_at, 123);
});

test("enrichNotesWithPublishedAt swallows loader failures", async () => {
  const notes = [{ url: `https://www.xiaohongshu.com/explore/${A}?xsec_token=a` }];
  const loader = async (): Promise<number | undefined> => {
    throw new Error("iframe blocked");
  };
  const enriched = await enrichNotesWithPublishedAt(notes, { loader });
  assert.equal(enriched, 0);
  assert.equal(notes[0].published_at, undefined);
});
