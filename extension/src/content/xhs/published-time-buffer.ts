/**
 * Buffer of exact `(note_id, published_at)` times reported by the MAIN-world
 * XHS API sniffer (for example `user_posted` profile responses). The task
 * executor drains it when assembling creator results.
 */

import type { XhsPublishedTime } from "../../shared/xhs-published-at.js";

let latestTimes: XhsPublishedTime[] = [];

export function recordXhsPublishedTimes(times: readonly XhsPublishedTime[]): void {
  latestTimes = times
    .filter((item) => item && item.note_id && Number.isFinite(item.published_at))
    .slice(0, 200)
    .map((item) => ({ note_id: item.note_id, published_at: item.published_at }));
}

export function readXhsPublishedTimes(): XhsPublishedTime[] {
  return latestTimes.map((item) => ({ ...item }));
}

export function resetXhsPublishedTimesForTest(): void {
  latestTimes = [];
}
