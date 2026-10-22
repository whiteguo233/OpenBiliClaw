export const INSTAGRAM_TASK_TAB_PARAM = "openbiliclaw_instagram_task";

export interface InstagramLocationLike {
  hash?: string;
  search?: string;
}

function hasMarker(value: string | undefined): boolean {
  if (!value) return false;
  const query = value.startsWith("#") || value.startsWith("?") ? value.slice(1) : value;
  return new URLSearchParams(query).get(INSTAGRAM_TASK_TAB_PARAM) === "1";
}

/** Task tabs are isolated from passive collection and normal browsing. */
export function isInstagramTaskTabLocation(
  locationLike: InstagramLocationLike | undefined = globalThis.location,
): boolean {
  return hasMarker(locationLike?.hash) || hasMarker(locationLike?.search);
}

export function withInstagramTaskMarker(url: string): string {
  const parsed = new URL(url);
  parsed.searchParams.set(INSTAGRAM_TASK_TAB_PARAM, "1");
  const fragment = parsed.hash.startsWith("#") ? parsed.hash.slice(1) : parsed.hash;
  const fragmentParams = new URLSearchParams(fragment);
  fragmentParams.set(INSTAGRAM_TASK_TAB_PARAM, "1");
  parsed.hash = fragmentParams.toString();
  return parsed.href;
}
