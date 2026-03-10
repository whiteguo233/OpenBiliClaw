/**
 * OpenBiliClaw — Background Service Worker
 *
 * Receives behavior events from content scripts,
 * buffers them, and forwards to the backend API.
 */

import { enqueueBufferedEvent, shouldFlushImmediately } from "./buffer.js";
import {
  buildChromeNotificationOptions,
  buildCognitionNotificationId,
  buildNotificationId,
  buildProfileNotificationUrl,
  parseNotificationBvid,
  parseCognitionUpdateId,
} from "./notifications.js";
import type { BehaviorEvent } from "../shared/types.js";

let eventBuffer: BehaviorEvent[] = [];
const BUFFER_FLUSH_INTERVAL = 30_000;
const BUFFER_MAX_SIZE = 50;
const FLUSH_ALARM_NAME = "openbiliclaw-flush-events";
const BACKEND_URL = "http://localhost:8420/api/events";
const NOTIFICATION_POLL_URL = "http://127.0.0.1:8420/api/notifications/pending";
const NOTIFICATION_ACK_URL = "http://127.0.0.1:8420/api/notifications/sent";
const COGNITION_POLL_URL = "http://127.0.0.1:8420/api/cognition-updates/pending";
const COGNITION_ACK_URL = "http://127.0.0.1:8420/api/cognition-updates/seen";
type PendingNotification = import("./notifications.js").PendingNotification;
type PendingCognitionUpdate = import("./notifications.js").PendingCognitionUpdate;

async function acknowledgeNotificationSent(bvid: string): Promise<void> {
  if (!bvid) return;
  await fetch(NOTIFICATION_ACK_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bvid }),
  });
}

async function fetchPendingNotification(): Promise<PendingNotification | null> {
  const response = await fetch(NOTIFICATION_POLL_URL, { method: "GET" });
  if (!response.ok) {
    throw new Error(`pending notifications failed: ${response.status}`);
  }
  const payload = (await response.json()) as { item?: PendingNotification | null };
  return payload.item ?? null;
}

async function fetchPendingCognitionUpdate(): Promise<PendingCognitionUpdate | null> {
  const response = await fetch(COGNITION_POLL_URL, { method: "GET" });
  if (!response.ok) {
    throw new Error(`pending cognition updates failed: ${response.status}`);
  }
  const payload = (await response.json()) as { item?: PendingCognitionUpdate | null };
  return payload.item ?? null;
}

async function acknowledgeCognitionUpdateSeen(id: string): Promise<void> {
  if (!id) return;
  await fetch(COGNITION_ACK_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id }),
  });
}

async function checkPendingNotification(): Promise<void> {
  try {
    const item = await fetchPendingNotification();
    if (item?.bvid) {
      await chrome.notifications.create(
        buildNotificationId(item.bvid),
        buildChromeNotificationOptions(item),
      );
      await acknowledgeNotificationSent(item.bvid);
      return;
    }
    const cognition = await fetchPendingCognitionUpdate();
    if (!cognition?.id) {
      return;
    }
    await chrome.notifications.create(
      buildCognitionNotificationId(cognition.id),
      buildChromeNotificationOptions(cognition),
    );
    await acknowledgeCognitionUpdateSeen(cognition.id);
  } catch {
    console.warn("[OpenBiliClaw] Pending notification check failed");
  }
}

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
      return;
    }
    await checkPendingNotification();
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
    if (eventBuffer.length > 0) {
      void flushEvents();
      return;
    }
    void checkPendingNotification();
  }
});

chrome.notifications.onClicked.addListener((notificationId) => {
  const bvid = parseNotificationBvid(notificationId);
  if (bvid) {
    void chrome.tabs.create({ url: `https://www.bilibili.com/video/${bvid}` });
    void chrome.notifications.clear(notificationId);
    return;
  }
  const cognitionId = parseCognitionUpdateId(notificationId);
  if (!cognitionId) {
    return;
  }
  void chrome.tabs.create({ url: buildProfileNotificationUrl() });
  void chrome.notifications.clear(notificationId);
});

ensureFlushAlarm();

console.log("[OpenBiliClaw] Service worker initialized");
