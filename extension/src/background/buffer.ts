import type { BehaviorEvent } from "../shared/types.js";

const HIGH_FREQUENCY_TYPES = new Set(["scroll", "hover", "snapshot"]);
const STRONG_SIGNAL_TYPES = new Set(["comment", "coin", "favorite", "like"]);

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

export function enqueueBufferedEvent(
  buffer: BehaviorEvent[],
  event: BehaviorEvent,
  maxSize: number,
): BehaviorEvent[] {
  const next = [...buffer];
  const dedupeKey = buildDedupeKey(event);

  if (dedupeKey) {
    const existingIndex = next.findIndex((item) => buildDedupeKey(item) === dedupeKey);
    if (existingIndex >= 0) {
      next[existingIndex] = event;
      return next;
    }
  }

  next.push(event);
  if (next.length > maxSize) {
    next.shift();
  }
  return next;
}

export function shouldFlushImmediately(event: BehaviorEvent): boolean {
  return STRONG_SIGNAL_TYPES.has(event.type);
}
