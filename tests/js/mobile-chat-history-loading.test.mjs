import test from "node:test";
import assert from "node:assert/strict";

import { getChatHistoryViewState } from "../../src/openbiliclaw/web/js/view-models.js";

// First-paint contract for the mobile chat view: the empty-state copy must
// only appear after the first history fetch settles; while the fetch is in
// flight the view shows a loading indicator so slow backends (E2E measured
// ~14s) do not read as lost history.

test("loading state before the first history fetch settles", () => {
  assert.equal(
    getChatHistoryViewState({ historyLoaded: false, turnCount: 0, sending: false }),
    "loading",
  );
});

test("empty state only once history settled with zero turns", () => {
  assert.equal(
    getChatHistoryViewState({ historyLoaded: true, turnCount: 0, sending: false }),
    "empty",
  );
});

test("turns render as soon as any exist, regardless of load flag", () => {
  assert.equal(
    getChatHistoryViewState({ historyLoaded: false, turnCount: 3, sending: false }),
    "turns",
  );
  assert.equal(
    getChatHistoryViewState({ historyLoaded: true, turnCount: 3, sending: false }),
    "turns",
  );
});

test("sending a first message skips both loading and empty states", () => {
  assert.equal(
    getChatHistoryViewState({ historyLoaded: false, turnCount: 0, sending: true }),
    "turns",
  );
});

test("defaults treat history as not yet loaded", () => {
  assert.equal(getChatHistoryViewState(), "loading");
});
