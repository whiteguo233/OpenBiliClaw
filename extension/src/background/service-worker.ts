/**
 * OpenBiliClaw — Background Service Worker
 *
 * Receives behavior events from content scripts,
 * buffers them, and forwards to the backend API.
 */

import { enqueueBufferedEvent, shouldFlushImmediately } from "./buffer.js";
import type { BehaviorEvent } from "../shared/types.js";

let eventBuffer: BehaviorEvent[] = [];
const BUFFER_FLUSH_INTERVAL = 30_000;
const BUFFER_MAX_SIZE = 50;
const FLUSH_ALARM_NAME = "openbiliclaw-flush-events";
const BACKEND_URL = "http://localhost:8420/api/events";

async function flushEvents(): Promise<void> {
  if (eventBuffer.length === 0) return;

  const events = [...eventBuffer];
  eventBuffer = [];

  try {
    const response = await fetch(BACKEND_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ events }),
    });

    if (!response.ok) {
      console.warn("[OpenBiliClaw] Backend returned", response.status);
      eventBuffer.unshift(...events);
    }
  } catch {
    console.warn("[OpenBiliClaw] Backend not available, buffering events");
    eventBuffer.unshift(...events);
  }
}

function ensureFlushAlarm(): void {
  chrome.alarms.create(FLUSH_ALARM_NAME, {
    periodInMinutes: BUFFER_FLUSH_INTERVAL / 60_000,
  });
}

chrome.runtime.onInstalled.addListener(() => {
  ensureFlushAlarm();
});

chrome.runtime.onStartup.addListener(() => {
  ensureFlushAlarm();
});

chrome.runtime.onMessage.addListener((message) => {
  if (message.action !== "BEHAVIOR_EVENT") return;

  eventBuffer = enqueueBufferedEvent(eventBuffer, message.data as BehaviorEvent, BUFFER_MAX_SIZE);

  if (eventBuffer.length >= BUFFER_MAX_SIZE || shouldFlushImmediately(message.data as BehaviorEvent)) {
    void flushEvents();
  }
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === FLUSH_ALARM_NAME) {
    void flushEvents();
  }
});

ensureFlushAlarm();

console.log("[OpenBiliClaw] Service worker initialized");
