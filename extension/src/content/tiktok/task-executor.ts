/**
 * TikTok task executor — canonical route-key module path.
 *
 * The implementation lives in `../tt/task-executor.ts` (the `tt/` tree owns
 * its own lifecycle, mirroring `dy/` isolation). This barrel exists so the
 * route-key-derived module path `content/tiktok/task-executor.ts` resolves
 * to the same executor, matching the per-source layout convention used by
 * `content/dy/` and `content/instagram/`.
 */

export * from "../tt/task-executor.ts";
