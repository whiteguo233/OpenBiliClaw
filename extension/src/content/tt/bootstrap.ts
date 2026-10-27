/**
 * TikTok universal-data reader — pure, no side effects on import.
 *
 * TikTok web SSR-injects a `<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">`
 * element whose textContent is JSON. The logged-in viewer's identity and
 * login flag live under one of a few canonical paths inside that tree;
 * we try each in order and return the first hit. Paths are intentionally
 * narrow — a random string field is never treated as identity. If TikTok
 * reorganizes the shape we return empty results and the executor fails
 * closed (logged_out) instead of confidently reporting the wrong account.
 *
 * This supplements the DOM signed-in markers in ./dom-extractor.ts;
 * both are "observed in the task tab" evidence, never cookie presence
 * (contract [auth] identity_evidence).
 */

export const TIKTOK_UNIVERSAL_DATA_ELEMENT_ID = "__UNIVERSAL_DATA_FOR_REHYDRATION__";

/** Parse the universal-data script text. Returns null on malformed input. */
export function parseTiktokUniversalData(raw: string): unknown {
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function getNested(state: unknown, path: string[]): unknown {
  let cursor: unknown = state;
  for (const key of path) {
    if (!cursor || typeof cursor !== "object") return undefined;
    cursor = (cursor as Record<string, unknown>)[key];
  }
  return cursor;
}

function getNestedString(state: unknown, path: string[]): string {
  const value = getNested(state, path);
  return typeof value === "string" ? value : "";
}

function getNestedBool(state: unknown, path: string[]): boolean | null {
  const value = getNested(state, path);
  return typeof value === "boolean" ? value : null;
}

const USER_OBJECT_PATHS: readonly string[][] = [
  ["__DEFAULT_SCOPE__", "webapp.user-context", "user"],
  ["webapp.user-context", "user"],
  // Observed session user on the current desktop homepage and settings page.
  // This is identity evidence only; it does not supply a login flag.
  ["__DEFAULT_SCOPE__", "webapp.app-context", "user"],
  ["webapp.app-context", "user"],
];

const LOGIN_FLAG_PATHS: readonly string[][] = [
  ["__DEFAULT_SCOPE__", "webapp.user-context", "isLoggedIn"],
  ["webapp.user-context", "isLoggedIn"],
];

export interface TiktokViewerIdentity {
  /** uniqueId — the account handle without "@". */
  handle: string;
  secUid: string;
}

/**
 * Resolve the logged-in viewer's handle / secUid from the universal-data
 * tree. Returns empty fields when no canonical path resolves.
 */
export function extractTiktokViewerFromState(state: unknown): TiktokViewerIdentity {
  for (const path of USER_OBJECT_PATHS) {
    const user = getNested(state, path);
    if (!user || typeof user !== "object") continue;
    const record = user as Record<string, unknown>;
    const handle =
      (typeof record.uniqueId === "string" && record.uniqueId.trim()) ||
      (typeof record.unique_id === "string" && record.unique_id.trim()) ||
      "";
    if (!handle) continue;
    const secUid =
      (typeof record.secUid === "string" && record.secUid.trim()) ||
      (typeof record.sec_uid === "string" && record.sec_uid.trim()) ||
      "";
    return { handle, secUid };
  }
  return { handle: "", secUid: "" };
}

/**
 * Detect whether universal-data represents a logged-in session.
 * Conservative: only an explicit true flag is accepted, and any explicit
 * false fails closed — a user object can appear in device-scoped state,
 * so its presence alone is an identity claim, not login proof.
 */
export function extractTiktokLoginStateFromState(state: unknown): boolean {
  let sawExplicitTrue = false;
  for (const path of LOGIN_FLAG_PATHS) {
    const value = getNestedBool(state, path);
    if (value === false) return false;
    if (value === true) sawExplicitTrue = true;
  }
  return sawExplicitTrue;
}

/** Convenience wrapper: parse script text and apply both extractors. */
export function readTiktokUniversalData(raw: string): {
  loggedIn: boolean;
  explicitLoggedOut: boolean;
  viewer: TiktokViewerIdentity;
} {
  const state = parseTiktokUniversalData(raw);
  return {
    loggedIn: extractTiktokLoginStateFromState(state),
    explicitLoggedOut: LOGIN_FLAG_PATHS.some((path) => getNestedBool(state, path) === false),
    viewer: extractTiktokViewerFromState(state),
  };
}
