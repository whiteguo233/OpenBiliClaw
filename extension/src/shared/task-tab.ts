/**
 * Shared helpers for identifying tabs opened by OpenBiliClaw background task
 * dispatchers.
 *
 * Task tabs are automatic browser work (discovery, bootstrap, native-save).
 * Passive behavior collection must not treat page loads, scrolls, or clicks
 * in these tabs as the user's own browsing actions.
 */

export const TASK_TAB_MARKERS: readonly string[] = [
  "openbiliclaw_xhs_task=1",
  "openbiliclaw_dy_task=1",
  "openbiliclaw_bili_task=1",
  "openbiliclaw_yt_task=1",
  "openbiliclaw_zhihu_task=1",
  "openbiliclaw_v2ex_task=1",
  "openbiliclaw_reddit_task=1",
  "openbiliclaw_linuxdo_task=1",
  "openbiliclaw_weibo_task=1",
];

export function isTaskTabUrl(rawUrl: string | undefined | null): boolean {
  if (!rawUrl) return false;
  try {
    const url = new URL(rawUrl);
    const searchAndHash = `${url.search}&${url.hash}`;
    return TASK_TAB_MARKERS.some((marker) => searchAndHash.includes(marker));
  } catch {
    return TASK_TAB_MARKERS.some((marker) => rawUrl.includes(marker));
  }
}

export function withTaskTabMarker(rawUrl: string, marker: string): string {
  const queryMarker = `${marker}=1`;
  if (rawUrl.includes(queryMarker)) return rawUrl;
  const hashIndex = rawUrl.indexOf("#");
  const base = hashIndex >= 0 ? rawUrl.slice(0, hashIndex) : rawUrl;
  const hash = hashIndex >= 0 ? rawUrl.slice(hashIndex) : "";
  const separator = base.includes("?") ? "&" : "?";
  return `${base}${separator}${queryMarker}${hash}`;
}
