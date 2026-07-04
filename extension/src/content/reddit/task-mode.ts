export const REDDIT_TASK_TAB_URL = "https://www.reddit.com/#openbiliclaw_reddit_task=1";

export function isRedditTaskTabLocation(locationLike: Location = globalThis.location): boolean {
  const href = String(locationLike.href ?? "");
  const hash = String(locationLike.hash ?? "");
  const search = String(locationLike.search ?? "");
  return (
    href.includes("openbiliclaw_reddit_task=1") ||
    hash.includes("openbiliclaw_reddit_task=1") ||
    search.includes("openbiliclaw_reddit_task=1")
  );
}
