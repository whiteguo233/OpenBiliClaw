import type { BehaviorEvent } from "../shared/types.js";

const HIGH_FREQUENCY_TYPES = new Set(["scroll", "hover", "snapshot"]);
const STRONG_SIGNAL_TYPES = new Set([
  "comment",
  "coin",
  "favorite",
  "feedback",
  "follow",
  "like",
  "share",
  "view",
]);

function getBucket(event: BehaviorEvent): number {
  return Math.floor(event.timestamp / 1000);
}

export function buildDedupeKey(event: BehaviorEvent): string | null {
  if (!HIGH_FREQUENCY_TYPES.has(event.type)) return null;

  if (event.type === "hover") {
    const href = String(event.metadata.href ?? "");
    return `hover:${event.url}:${href}`;
  }

  return `${event.type}:${event.url}:${getBucket(event)}`;
}

/**
 * Enqueue an event into the buffer, mutating it in place.
 * Safe because the service worker is single-threaded.
 */
export function enqueueBufferedEvent(
  buffer: BehaviorEvent[],
  event: BehaviorEvent,
  maxSize: number,
): BehaviorEvent[] {
  const dedupeKey = buildDedupeKey(event);

  if (dedupeKey) {
    const existingIndex = buffer.findIndex((item) => buildDedupeKey(item) === dedupeKey);
    if (existingIndex >= 0) {
      buffer[existingIndex] = event;
      return buffer;
    }
  }

  buffer.push(event);
  if (buffer.length > maxSize) {
    buffer.shift();
  }
  return buffer;
}

export function shouldFlushImmediately(event: BehaviorEvent): boolean {
  if (
    event.type === "click" &&
    (typeof event.metadata.watch_seconds === "number" ||
      typeof event.metadata.video_duration_seconds === "number" ||
      typeof event.metadata.dwell_source === "string")
  ) {
    return true;
  }
  return STRONG_SIGNAL_TYPES.has(event.type);
}
