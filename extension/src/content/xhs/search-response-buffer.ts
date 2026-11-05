import type { XhsSearchResponseNote } from "../../shared/xhs-search-response.js";

export const XHS_SEARCH_REPLAY_REQUEST_SOURCE = "obc-xhs-search-replay-request";

let latestNotes: XhsSearchResponseNote[] = [];

function copyNote(note: XhsSearchResponseNote): XhsSearchResponseNote {
  return { ...note, ...(note.tags ? { tags: [...note.tags] } : {}) };
}

export function recordXhsSearchResponseNotes(notes: readonly XhsSearchResponseNote[]): void {
  latestNotes = notes.slice(0, 20).map(copyNote);
}

export function readXhsSearchResponseNotes(): XhsSearchResponseNote[] {
  return latestNotes.map(copyNote);
}

export function requestXhsSearchResponseReplay(win: Window): void {
  win.postMessage({ source: XHS_SEARCH_REPLAY_REQUEST_SOURCE }, "*");
}

export function resetXhsSearchResponseNotesForTest(): void {
  latestNotes = [];
}
