/**
 * Tests for the TikTok universal-data reader (login state + viewer identity).
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  extractTiktokLoginStateFromState,
  extractTiktokViewerFromState,
  parseTiktokUniversalData,
  readTiktokUniversalData,
} from "../src/content/tt/bootstrap.ts";

const SIGNED_IN_PAYLOAD = JSON.stringify({
  __DEFAULT_SCOPE__: {
    "webapp.user-context": {
      isLoggedIn: true,
      user: {
        uniqueId: "demo.creator",
        secUid: "MS4wLjABAAAA-demo",
        nickname: "Demo Creator",
      },
    },
  },
});

test("parseTiktokUniversalData tolerates malformed input", () => {
  assert.equal(parseTiktokUniversalData(""), null);
  assert.equal(parseTiktokUniversalData("not json"), null);
  assert.equal(parseTiktokUniversalData("%7Bbroken"), null);
});

test("readTiktokUniversalData resolves a signed-in viewer", () => {
  const { loggedIn, viewer } = readTiktokUniversalData(SIGNED_IN_PAYLOAD);
  assert.equal(loggedIn, true);
  assert.deepEqual(viewer, { handle: "demo.creator", secUid: "MS4wLjABAAAA-demo" });
});

test("extractTiktokViewerFromState accepts alternate canonical paths", () => {
  const alt = {
    "webapp.user-context": {
      isLoggedIn: true,
      user: { unique_id: "alt.creator", sec_uid: "SEC-1" },
    },
  };
  assert.deepEqual(extractTiktokViewerFromState(alt), {
    handle: "alt.creator",
    secUid: "SEC-1",
  });
});

test("extractTiktokViewerFromState returns empty for unknown shapes", () => {
  assert.deepEqual(extractTiktokViewerFromState({ totally: "different" }), {
    handle: "",
    secUid: "",
  });
  assert.deepEqual(extractTiktokViewerFromState(null), { handle: "", secUid: "" });
});

test("extractTiktokLoginStateFromState fails closed without an explicit true", () => {
  // A user object alone is an identity claim, not login proof.
  const claimOnly = {
    __DEFAULT_SCOPE__: {
      "webapp.user-context": { user: { uniqueId: "ghost" } },
    },
  };
  assert.equal(extractTiktokLoginStateFromState(claimOnly), false);
  // Explicit false wins over a stray true elsewhere.
  const contradictory = {
    __DEFAULT_SCOPE__: { "webapp.user-context": { isLoggedIn: true } },
    "webapp.user-context": { isLoggedIn: false },
  };
  assert.equal(extractTiktokLoginStateFromState(contradictory), false);
  assert.equal(extractTiktokLoginStateFromState(null), false);
});


test("viewed-author SSR state never establishes viewer or login", () => {
  const author = { __DEFAULT_SCOPE__: { "webapp.user": {
    isLogin: true, userInfo: { uniqueId: "someone.else", secUid: "author-id" },
  } } };
  assert.deepEqual(extractTiktokViewerFromState(author), { handle: "", secUid: "" });
  assert.equal(extractTiktokLoginStateFromState(author), false);
});

test("live app-context session user is an identity claim, never independent login proof", () => {
  const app = { __DEFAULT_SCOPE__: { "webapp.app-context": {
    user: { uniqueId: "me.user", secUid: "session-sec", nickName: "Display name" },
  } } };
  const parsed = readTiktokUniversalData(JSON.stringify(app));
  assert.deepEqual(parsed.viewer, { handle: "me.user", secUid: "session-sec" });
  assert.equal(parsed.loggedIn, false);
  assert.equal(parsed.explicitLoggedOut, false);
  assert.equal(readTiktokUniversalData(JSON.stringify({ ...app,
    "webapp.user-context": { isLoggedIn: false },
  })).explicitLoggedOut, true);
});
