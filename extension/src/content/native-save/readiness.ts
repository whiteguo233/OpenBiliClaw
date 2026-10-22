/** Wait for a login-dependent SPA to expose its exact native-save surface. */
const INITIAL_READY_INTERVAL_MS = 250;

export async function waitForNativeSaveReadiness(
  ready: () => boolean | Promise<boolean>,
  sleep: (ms: number) => Promise<void>,
  timeoutMs = 10_000,
): Promise<boolean> {
  // Default remains 10 seconds for existing platforms. TikTok's live desktop
  // navigation hydrated more than 12 seconds after document complete.
  const budget = Number.isFinite(timeoutMs) ? Math.max(250, Math.min(30_000, timeoutMs)) : 10_000;
  const attempts = Math.ceil(budget / INITIAL_READY_INTERVAL_MS);
  const deadline = Date.now() + budget;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    if (await ready()) return true;
    const remaining = deadline - Date.now();
    if (remaining <= 0) break;
    if (attempt + 1 < attempts) await sleep(Math.min(INITIAL_READY_INTERVAL_MS, remaining));
  }
  return false;
}
