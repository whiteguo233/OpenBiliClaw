import {
  isAllowedNativeSavePageUrl,
  isNativeSaveTask,
  sanitizeNativeSaveResult,
  type NativeSavePlatform,
  type NativeSaveTask,
  type SanitizedNativeSaveOutcome,
} from "../../shared/native-save.ts";

export type NativeSaveExecutor = (
  task: NativeSaveTask,
) => Promise<unknown> | unknown;

const installedPlatforms = new Set<NativeSavePlatform>();
const documentInstanceId = globalThis.crypto?.randomUUID?.() ??
  `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
// Recent, not permanent: completed outcomes leave this 256-entry window by FIFO eviction.
const MAX_RECENT_TASKS = 256;
interface CachedOutcome {
  itemKey: string;
  outcome: Promise<SanitizedNativeSaveOutcome>;
  platform: NativeSavePlatform;
  settled: boolean;
}
const recentOutcomes = new Map<string, CachedOutcome>();

function cachedOutcome(
  task: NativeSaveTask,
  executor: NativeSaveExecutor,
  cacheKey: string = task.id,
): Promise<SanitizedNativeSaveOutcome> | null {
  const existing = recentOutcomes.get(cacheKey);
  if (existing) {
    if (existing.platform !== task.platform || existing.itemKey !== task.item_key) return null;
    return existing.outcome;
  }
  if (recentOutcomes.size >= MAX_RECENT_TASKS) {
    const oldestCompleted = [...recentOutcomes].find(([, cached]) => cached.settled)?.[0];
    if (oldestCompleted === undefined) return null;
    recentOutcomes.delete(oldestCompleted);
  }
  const outcome = Promise.resolve()
    .then(() => executor(task))
    .then(
      (value) => sanitizeNativeSaveResult(value),
      () => sanitizeNativeSaveResult({ status: "failed", error_code: "native_save_failed" }),
    );
  const cached = {
    itemKey: task.item_key,
    outcome,
    platform: task.platform,
    settled: false,
  };
  recentOutcomes.set(cacheKey, cached);
  void outcome.then(() => { cached.settled = true; });
  return outcome;
}

/** Install one platform executor. Platform entrypoints are intentionally wired in later tasks. */
export function installNativeSaveExecutor(
  platform: NativeSavePlatform,
  executor: NativeSaveExecutor,
  verifier?: NativeSaveExecutor,
): void {
  if (installedPlatforms.has(platform)) return;
  installedPlatforms.add(platform);

  chrome.runtime.onMessage.addListener(async (message: unknown) => {
    if (typeof message !== "object" || message === null) return false;
    const envelope = message as {
      execution_id?: unknown;
      platform?: unknown;
      type?: unknown;
      task?: unknown;
      verification_only?: unknown;
    };
    if (envelope.type === "NATIVE_SAVE_READY") {
      if (envelope.platform !== platform || !isAllowedNativeSavePageUrl(platform, location.href)) {
        return false;
      }
      return { ready: true, document_instance_id: documentInstanceId };
    }
    if (envelope.type !== "NATIVE_SAVE_EXECUTE" || !isNativeSaveTask(envelope.task)) return false;
    if (envelope.verification_only !== undefined && typeof envelope.verification_only !== "boolean") {
      return false;
    }
    const task = envelope.task;
    if (task.platform !== platform || !isAllowedNativeSavePageUrl(platform, location.href)) {
      return false;
    }
    const verificationOnly = envelope.verification_only === true;
    if (
      envelope.execution_id !== undefined &&
      (typeof envelope.execution_id !== "string" || envelope.execution_id.length > 128)
    ) return false;
    if (verificationOnly && typeof envelope.execution_id !== "string") return false;
    if (verificationOnly && !verifier) return false;
    const outcomePromise = cachedOutcome(
      task,
      verificationOnly ? verifier! : executor,
      verificationOnly ? `${task.id}:verify` : task.id,
    );
    if (!outcomePromise) return false;
    const outcome = await outcomePromise;
    await chrome.runtime.sendMessage({
      type: "NATIVE_SAVE_RESULT",
      platform,
      task_id: task.id,
      item_key: task.item_key,
      document_instance_id: documentInstanceId,
      ...(typeof envelope.execution_id === "string"
        ? { execution_id: envelope.execution_id }
        : {}),
      ...outcome,
    });
    return true;
  });
}
