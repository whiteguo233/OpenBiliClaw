/** Shared, non-secret Instagram identity helpers. */

export const INSTAGRAM_HOSTS = ["instagram.com", "www.instagram.com"] as const;
export const INSTAGRAM_RESPONSE_EVENT = "openbiliclaw:instagram-response";
export const INSTAGRAM_REPLAY_EVENT = "openbiliclaw:instagram-response-replay-request";
export type InstagramContentType = "post" | "reel" | "carousel" | "user";

export function isInstagramHost(hostname: string): boolean {
  const host = hostname.trim().toLowerCase().replace(/^\.+|\.+$/g, "");
  return host === "instagram.com" || host.endsWith(".instagram.com");
}

export function instagramCanonicalUrl(
  contentType: InstagramContentType,
  codeOrUsername: string,
): string {
  const value = encodeURIComponent(codeOrUsername.trim());
  if (!value) return "https://www.instagram.com/";
  if (contentType === "user") return `https://www.instagram.com/${value}/`;
  return `https://www.instagram.com/${contentType === "reel" ? "reel" : "p"}/${value}/`;
}

export function instagramMediaIdentity(value: unknown): string {
  if (typeof value === "number" && Number.isFinite(value)) return String(Math.trunc(value));
  if (typeof value !== "string") return "";
  const normalized = value.trim();
  return /^[0-9]+(?:_[0-9]+)?$/.test(normalized) ? normalized.split("_", 1)[0] : "";
}
