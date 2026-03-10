export const NOTIFICATION_PREFIX = "openbiliclaw-recommendation:";
export const COGNITION_NOTIFICATION_PREFIX = "openbiliclaw-cognition:";

export type PendingNotification = {
  recommendation_id: number;
  bvid: string;
  title: string;
  reason: string;
};

export type PendingCognitionUpdate = {
  id: string;
  kind: string;
  summary: string;
};

export function buildNotificationId(bvid: string): string {
  return `${NOTIFICATION_PREFIX}${bvid}`;
}

export function buildCognitionNotificationId(updateId: string): string {
  return `${COGNITION_NOTIFICATION_PREFIX}${updateId}`;
}

export function parseNotificationBvid(notificationId: string): string {
  if (!notificationId.startsWith(NOTIFICATION_PREFIX)) {
    return "";
  }
  return notificationId.slice(NOTIFICATION_PREFIX.length);
}

export function parseCognitionUpdateId(notificationId: string): string {
  if (!notificationId.startsWith(COGNITION_NOTIFICATION_PREFIX)) {
    return "";
  }
  return notificationId.slice(COGNITION_NOTIFICATION_PREFIX.length);
}

export function buildChromeNotificationOptions(
  item: PendingNotification | PendingCognitionUpdate,
): chrome.notifications.NotificationCreateOptions {
  if ("summary" in item) {
    return {
      type: "basic",
      iconUrl: "icons/icon128.png",
      title: "阿B 又对你多看清了一点",
      message: item.summary || "阿B 刚记住了一个新的偏好变化。",
      priority: 2,
    };
  }
  return {
    type: "basic",
    iconUrl: "icons/icon128.png",
    title: item.title || "阿B 给你补到一条新内容",
    message: item.reason || "这条大概率会对你的胃口。",
    priority: 2,
  };
}

export function buildProfileNotificationUrl(): string {
  if (
    typeof chrome !== "undefined" &&
    chrome.runtime &&
    typeof chrome.runtime.getURL === "function"
  ) {
    return chrome.runtime.getURL("popup/popup.html?tab=profile");
  }
  return "chrome-extension://__EXTENSION_ID__/popup/popup.html?tab=profile";
}
