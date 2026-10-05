import {
  isAllowedNativeSavePageUrl,
  isNativeSaveTask,
  sanitizeNativeSaveResult,
  type NativeSavePlatform,
  type NativeSaveResult,
  type NativeSaveSlug,
  type NativeSaveTask,
  type SanitizedNativeSaveOutcome,
} from "../shared/native-save.ts";
import { releaseDispatcherMutex, tryAcquireDispatcherMutex } from "./dispatcher-mutex.ts";
import { createTaskTab } from "./task-tab.ts";

const DEFAULT_TIMEOUT_MS = 240_000;
const DEFAULT_READINESS_RETRY_MS = 250;
const DEFAULT_MUTEX_RETRY_MS = 50;
const DEFAULT_RESULT_ATTEMPT_TIMEOUT_MS = 5_000;
const DEFAULT_RESULT_MAX_ATTEMPTS = 3;
const DEFAULT_RESULT_RETRY_MS = 250;
const DEFAULT_RESULT_TIMEOUT_MS = 15_000;
const NATIVE_SAVE_TAB_SESSION_KEY = "openbiliclaw_native_save_task_tab_id";
const READ_ONLY_VERIFICATION_PLATFORMS = new Set<NativeSavePlatform>([
  "douyin",
  "xiaohongshu",
  "youtube",
  "zhihu",
]);

export interface NativeSaveRunnerOptions {
  timeoutMs?: number;
  readinessRetryMs?: number;
  mutexRetryMs?: number;
  resultAttemptTimeoutMs?: number;
  resultMaxAttempts?: number;
  resultRetryMs?: number;
  resultTimeoutMs?: number;
}

interface ActiveTask {
  task: NativeSaveTask;
  tabId: number;
  platform: NativeSavePlatform;
  executionId: string;
  expectedDocumentInstanceId?: string;
  verificationOnly: boolean;
  complete: (outcome: unknown) => void;
  settled: boolean;
}

interface NativeSaveExecution {
  documentInstanceId?: string;
  outcome: SanitizedNativeSaveOutcome;
}

class NativeSaveDeadlineError extends Error {}

const activeTasks = new Map<string, ActiveTask>();
const nativeSaveTaskTabIds = new Set<number>();
let nativeSaveRecoveryPromise: Promise<void> | null = null;
let sessionRecordMutation: Promise<void> = Promise.resolve();
let executionSequence = 0;

function timeoutOutcome(): SanitizedNativeSaveOutcome {
  return sanitizeNativeSaveResult({ status: "failed", error_code: "native_save_timeout" });
}

function failedOutcome(): SanitizedNativeSaveOutcome {
  return sanitizeNativeSaveResult({ status: "failed", error_code: "native_save_failed" });
}

async function postResultWithRetry(
  postResult: (result: NativeSaveResult, signal?: AbortSignal) => Promise<void>,
  result: NativeSaveResult,
  options: NativeSaveRunnerOptions,
): Promise<boolean> {
  const deadline = Date.now() + Math.max(1, options.resultTimeoutMs ?? DEFAULT_RESULT_TIMEOUT_MS);
  const retryMs = Math.max(1, options.resultRetryMs ?? DEFAULT_RESULT_RETRY_MS);
  const attemptTimeoutMs = Math.max(
    1,
    options.resultAttemptTimeoutMs ?? DEFAULT_RESULT_ATTEMPT_TIMEOUT_MS,
  );
  const maxAttempts = Math.min(
    5,
    Math.max(1, Math.trunc(options.resultMaxAttempts ?? DEFAULT_RESULT_MAX_ATTEMPTS)),
  );
  let attempts = 0;
  while (Date.now() < deadline && attempts < maxAttempts) {
    attempts += 1;
    const controller = new AbortController();
    try {
      await beforeDeadline(
        postResult(result, controller.signal),
        Math.min(deadline, Date.now() + attemptTimeoutMs),
      );
      return true;
    } catch {
      // The exact same idempotent payload is retried within an independent callback budget.
    } finally {
      controller.abort();
    }
    if (remainingMs(deadline) > 0) {
      await delay(Math.min(retryMs, remainingMs(deadline)));
    }
  }
  return false;
}

function remainingMs(deadline: number): number {
  return Math.max(0, deadline - Date.now());
}

async function beforeDeadline<T>(promise: Promise<T>, deadline: number): Promise<T> {
  const timeoutMs = remainingMs(deadline);
  if (timeoutMs <= 0) throw new NativeSaveDeadlineError("native-save task timed out");
  let timer: ReturnType<typeof setTimeout>;
  try {
    return await Promise.race([
      promise,
      new Promise<never>((_resolve, reject) => {
        timer = setTimeout(
          () => reject(new NativeSaveDeadlineError("native-save task timed out")),
          timeoutMs,
        );
      }),
    ]);
  } finally {
    clearTimeout(timer!);
  }
}

function delay(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal?.aborted) {
      resolve();
      return;
    }
    const timer = setTimeout(finish, ms);
    function finish(): void {
      clearTimeout(timer);
      signal?.removeEventListener("abort", finish);
      resolve();
    }
    signal?.addEventListener("abort", finish, { once: true });
  });
}

async function acquireMutexBefore(
  owner: string,
  deadline: number,
  retryMs: number,
): Promise<boolean> {
  while (Date.now() < deadline) {
    if (tryAcquireDispatcherMutex(owner)) return true;
    await delay(Math.min(retryMs, remainingMs(deadline)));
  }
  return false;
}

async function waitForTabLoad(tabId: number, deadline: number): Promise<chrome.tabs.Tab> {
  let revision = 0;
  let wake: (() => void) | null = null;
  const listener = (updatedId: number): void => {
    if (updatedId !== tabId) return;
    revision += 1;
    wake?.();
  };
  let registrationAttempted = false;
  try {
    registrationAttempted = true;
    chrome.tabs.onUpdated.addListener(listener);
    while (Date.now() < deadline) {
      const observedRevision = revision;
      const tab = await beforeDeadline(chrome.tabs.get(tabId), deadline);
      if (tab.status === "complete") return tab;
      if (revision !== observedRevision) continue;
      await new Promise<void>((resolve, reject) => {
        const timeoutMs = remainingMs(deadline);
        if (timeoutMs <= 0) {
          reject(new NativeSaveDeadlineError("native-save tab load timed out"));
          return;
        }
        const timer = setTimeout(() => {
          wake = null;
          reject(new NativeSaveDeadlineError("native-save tab load timed out"));
        }, timeoutMs);
        wake = () => {
          clearTimeout(timer);
          wake = null;
          resolve();
        };
        if (revision !== observedRevision) wake();
      });
    }
    throw new NativeSaveDeadlineError("native-save tab load timed out");
  } finally {
    if (registrationAttempted) {
      for (let attempt = 0; attempt < 2; attempt += 1) {
        try {
          chrome.tabs.onUpdated.removeListener(listener);
          break;
        } catch {
          // Retry one transient failure, then let the runner continue teardown.
        }
      }
    }
  }
}

async function waitForExecutableTab(
  task: NativeSaveTask,
  tabId: number,
  deadline: number,
): Promise<chrome.tabs.Tab> {
  // Xiaohongshu keeps document-level requests open for minutes. Its content
  // executor already has bounded SPA-readiness polling, so waiting for Chrome's
  // coarse `complete` state only delays an otherwise ready, correlated control.
  return task.platform === "xiaohongshu"
    ? await beforeDeadline(chrome.tabs.get(tabId), deadline)
    : await waitForTabLoad(tabId, deadline);
}

function observedTabUrl(task: NativeSaveTask, tab: chrome.tabs.Tab): string | undefined {
  return tab.pendingUrl ?? tab.url ?? (
    task.platform === "xiaohongshu" ? taskNavigationUrl(task) : undefined
  );
}

type TabRemovalOutcome = "absent" | "failed" | "removed";

function isMissingTabError(error: unknown): boolean {
  return /(?:no tab with id|invalid tab id|tab (?:does not exist|not found))/i.test(String(error));
}

async function removeTabBestEffort(tabId: number): Promise<TabRemovalOutcome> {
  try {
    await chrome.tabs.remove(tabId);
    return "removed";
  } catch (removeError) {
    if (isMissingTabError(removeError)) return "absent";
    try {
      await chrome.tabs.get(tabId);
      return "failed";
    } catch (inspectError) {
      return isMissingTabError(inspectError) ? "absent" : "failed";
    }
  }
}

function sessionStorageArea(): chrome.storage.StorageArea | null {
  try {
    return typeof chrome === "undefined" ? null : chrome.storage?.session ?? null;
  } catch {
    return null;
  }
}

function serializedSessionMutation(mutation: () => Promise<void>): Promise<void> {
  const current = sessionRecordMutation.then(mutation, mutation);
  sessionRecordMutation = current.catch(() => {});
  return current;
}

async function recordNativeSaveTaskTab(tabId: number): Promise<void> {
  await serializedSessionMutation(async () => {
    const storage = sessionStorageArea();
    if (!storage) return;
    try {
      const stored = await storage.get(NATIVE_SAVE_TAB_SESSION_KEY);
      const value = stored[NATIVE_SAVE_TAB_SESSION_KEY];
      const tabIds = typeof value === "number"
        ? [value]
        : Array.isArray(value)
          ? value.filter((item): item is number => typeof item === "number")
          : [];
      if (!tabIds.includes(tabId)) tabIds.push(tabId);
      await storage.set({
        [NATIVE_SAVE_TAB_SESSION_KEY]: tabIds.length === 1 ? tabIds[0] : tabIds,
      });
    } catch {
      // Session persistence is an optional recovery enhancement, not an execution prerequisite.
    }
  });
}

async function clearRecordedNativeSaveTaskTab(tabId: number): Promise<void> {
  await serializedSessionMutation(async () => {
    const storage = sessionStorageArea();
    if (!storage) return;
    try {
      const stored = await storage.get(NATIVE_SAVE_TAB_SESSION_KEY);
      const value = stored[NATIVE_SAVE_TAB_SESSION_KEY];
      const tabIds = (typeof value === "number" ? [value] : Array.isArray(value) ? value : [])
        .filter((item): item is number => typeof item === "number" && item !== tabId);
      if (tabIds.length === 0) {
        await storage.remove(NATIVE_SAVE_TAB_SESSION_KEY);
      } else {
        await storage.set({
          [NATIVE_SAVE_TAB_SESSION_KEY]: tabIds.length === 1 ? tabIds[0] : tabIds,
        });
      }
    } catch {
      // A closed tab is safe even if best-effort session cleanup fails.
    }
  });
}

/** Close only the runner-owned tab recorded before a previous MV3 worker stopped. */
export async function recoverRecordedNativeSaveTaskTab(): Promise<void> {
  const storage = sessionStorageArea();
  if (!storage) return;
  let value: unknown;
  try {
    const stored = await storage.get(NATIVE_SAVE_TAB_SESSION_KEY);
    value = stored[NATIVE_SAVE_TAB_SESSION_KEY];
  } catch {
    return;
  }
  const tabIds = (typeof value === "number" ? [value] : Array.isArray(value) ? value : [])
    .filter((item): item is number => typeof item === "number" && Number.isInteger(item) && item >= 0);
  if (tabIds.length === 0) {
    try {
      await storage.remove(NATIVE_SAVE_TAB_SESSION_KEY);
    } catch {
      // Invalid runner metadata is safe to discard best-effort.
    }
    return;
  }
  const remaining: number[] = [];
  for (const tabId of new Set(tabIds)) {
    if (await removeTabBestEffort(tabId) === "failed") remaining.push(tabId);
    else nativeSaveTaskTabIds.delete(tabId);
  }
  try {
    if (remaining.length === 0) {
      await storage.remove(NATIVE_SAVE_TAB_SESSION_KEY);
    } else {
      await storage.set({
        [NATIVE_SAVE_TAB_SESSION_KEY]: remaining.length === 1 ? remaining[0] : remaining,
      });
    }
  } catch {
    // Orphan tabs are already closed; record cleanup remains best-effort.
  }
}

/** Identify runner-owned tabs before content entrypoints start passive collectors. */
export async function isNativeSaveTaskTabId(tabId: number | undefined): Promise<boolean> {
  if (tabId === undefined || !Number.isInteger(tabId) || tabId < 0) return false;
  if (nativeSaveTaskTabIds.has(tabId)) return true;
  const storage = sessionStorageArea();
  if (!storage) return false;
  try {
    const value = (await storage.get(NATIVE_SAVE_TAB_SESSION_KEY))[NATIVE_SAVE_TAB_SESSION_KEY];
    return (typeof value === "number" ? [value] : Array.isArray(value) ? value : [])
      .some((item) => item === tabId);
  } catch {
    return false;
  }
}

/** One MV3-lifetime barrier shared by startup, alarms, wakes, and direct execution. */
export function ensureNativeSaveTaskRecovery(): Promise<void> {
  nativeSaveRecoveryPromise ??= recoverRecordedNativeSaveTaskTab();
  return nativeSaveRecoveryPromise;
}

export function resetNativeSaveTaskRecoveryForTest(): void {
  nativeSaveRecoveryPromise = null;
  sessionRecordMutation = Promise.resolve();
  nativeSaveTaskTabIds.clear();
  executionSequence = 0;
}

async function createTabBeforeDeadline(
  url: string,
  deadline: number,
): Promise<chrome.tabs.Tab> {
  const creation = createTaskTab({ active: true, url });
  try {
    return await beforeDeadline(creation, deadline);
  } catch (error) {
    if (error instanceof NativeSaveDeadlineError) {
      void creation.then(
        async (lateTab) => {
          if (lateTab.id !== undefined) await removeTabBestEffort(lateTab.id);
        },
        () => {},
      );
    }
    throw error;
  }
}

function taskNavigationUrl(task: NativeSaveTask): string {
  if (task.platform === "douyin") {
    return `https://www.douyin.com/jingxuan?modal_id=${encodeURIComponent(task.content_id)}`;
  }
  if (task.platform !== "reddit") return task.content_url;
  try {
    const url = new URL(task.content_url);
    if (url.hostname === "reddit.com" || url.hostname.endsWith(".reddit.com")) {
      url.hostname = "old.reddit.com";
      return url.toString();
    }
  } catch {
    // The shared task validator already rejects malformed URLs; fail closed to the original.
  }
  return task.content_url;
}

function xiaohongshuRouteId(value: string | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.username || url.password || url.port) return null;
    if (url.hostname !== "xiaohongshu.com" && !url.hostname.endsWith(".xiaohongshu.com")) {
      return null;
    }
    return /^\/(?:explore|discovery\/item|search_result)\/([A-Za-z0-9_-]+)\/?$/
      .exec(url.pathname)?.[1] ?? null;
  } catch {
    return null;
  }
}

async function reuseExactXiaohongshuTab(
  task: NativeSaveTask,
  deadline: number,
): Promise<chrome.tabs.Tab | null> {
  if (task.platform !== "xiaohongshu") return null;
  try {
    const tabs = await beforeDeadline(chrome.tabs.query({
      url: ["https://xiaohongshu.com/*", "https://*.xiaohongshu.com/*"],
    }), deadline);
    const matches = tabs.filter((tab) =>
      tab.id !== undefined && xiaohongshuRouteId(tab.url) === task.content_id
    );
    const match = matches[0];
    if (matches.length !== 1 || match?.id === undefined) return null;
    return await beforeDeadline(chrome.tabs.update(match.id, { active: true }), deadline) ?? null;
  } catch {
    return null;
  }
}

function contentResultForActiveTask(
  message: unknown,
  sender?: chrome.runtime.MessageSender,
): boolean {
  if (typeof message !== "object" || message === null) return false;
  const result = message as Record<string, unknown>;
  const active = typeof result.task_id === "string" ? activeTasks.get(result.task_id) : undefined;
  if (!active || active.settled) return false;
  const senderUrl = typeof sender?.url === "string" ? sender.url : sender?.tab?.url;
  const documentInstanceId = typeof result.document_instance_id === "string" &&
      result.document_instance_id.length > 0 && result.document_instance_id.length <= 128
    ? result.document_instance_id
    : undefined;
  const executionMismatch = active.verificationOnly
    ? result.execution_id !== active.executionId
    : result.execution_id !== undefined && result.execution_id !== active.executionId;
  const documentMismatch = active.expectedDocumentInstanceId !== undefined &&
    documentInstanceId !== active.expectedDocumentInstanceId;
  if (
    result.type !== "NATIVE_SAVE_RESULT" ||
    result.platform !== active.platform ||
    result.task_id !== active.task.id ||
    result.item_key !== active.task.item_key ||
    executionMismatch ||
    documentMismatch ||
    sender?.tab?.id !== active.tabId ||
    !isAllowedNativeSavePageUrl(active.platform, senderUrl)
  ) {
    return false;
  }
  active.settled = true;
  active.complete(result);
  return true;
}

type SendAttemptResult =
  | { kind: "aborted" }
  | { kind: "failed" }
  | { kind: "response"; value: unknown };

function sendMessageUntilAbort(
  tabId: number,
  message: unknown,
  signal: AbortSignal,
): Promise<SendAttemptResult> {
  if (signal.aborted) return Promise.resolve({ kind: "aborted" });
  return new Promise((resolve) => {
    let settled = false;
    const finish = (result: SendAttemptResult): void => {
      if (settled) return;
      settled = true;
      signal.removeEventListener("abort", onAbort);
      resolve(result);
    };
    const onAbort = (): void => finish({ kind: "aborted" });
    signal.addEventListener("abort", onAbort, { once: true });
    void chrome.tabs.sendMessage(tabId, message).then(
      (value) => finish({ kind: "response", value }),
      () => finish({ kind: "failed" }),
    );
  });
}

export function handleNativeSaveContentResult(
  message: unknown,
  sender?: chrome.runtime.MessageSender,
): boolean {
  return contentResultForActiveTask(message, sender);
}

async function sendExecuteWhenReady(
  tabId: number,
  task: NativeSaveTask,
  deadline: number,
  retryMs: number,
  signal: AbortSignal,
  executionId: string,
  verificationOnly: boolean = false,
): Promise<void> {
  while (!signal.aborted && Date.now() < deadline) {
    const attempt = await sendMessageUntilAbort(tabId, {
      type: "NATIVE_SAVE_EXECUTE",
      task,
      execution_id: executionId,
      ...(verificationOnly ? { verification_only: true } : {}),
    }, signal);
    if (attempt.kind === "aborted") return;
    if (attempt.kind === "response" && attempt.value !== undefined) return;
    // MV3 can expose the loaded tab before its content listener is registered.
    await delay(Math.min(retryMs, remainingMs(deadline)), signal);
  }
}

async function waitForNewDocumentInstance(
  tabId: number,
  platform: NativeSavePlatform,
  previousDocumentInstanceId: string | undefined,
  deadline: number,
  retryMs: number,
): Promise<string> {
  while (Date.now() < deadline) {
    try {
      const response = await beforeDeadline(
        chrome.tabs.sendMessage(tabId, {
          type: "NATIVE_SAVE_READY",
          platform,
        }),
        Math.min(deadline, Date.now() + Math.max(1, retryMs)),
      ) as { document_instance_id?: unknown; ready?: unknown } | undefined;
      const documentInstanceId = response?.document_instance_id;
      if (
        response?.ready === true &&
        typeof documentInstanceId === "string" &&
        documentInstanceId.length > 0 &&
        documentInstanceId.length <= 128 &&
        documentInstanceId !== previousDocumentInstanceId
      ) {
        return documentInstanceId;
      }
    } catch {
      // Reload can expose the old document or no listener before the new script is ready.
    }
    await delay(Math.min(retryMs, remainingMs(deadline)));
  }
  throw new NativeSaveDeadlineError("native-save verification document did not become ready");
}

async function executeBeforeDeadline(
  task: NativeSaveTask,
  tabId: number,
  deadline: number,
  retryMs: number,
  verificationOnly: boolean = false,
  expectedDocumentInstanceId?: string,
): Promise<NativeSaveExecution> {
  const timeoutMs = remainingMs(deadline);
  if (timeoutMs <= 0) return { outcome: timeoutOutcome() };
  executionSequence += 1;
  const executionId = `${task.id}:${verificationOnly ? "verify" : "mutate"}:${executionSequence}`;
  const readinessController = new AbortController();
  let timer: ReturnType<typeof setTimeout>;
  const terminal = new Promise<unknown>((resolve) => {
    const active: ActiveTask = {
      task,
      tabId,
      platform: task.platform,
      executionId,
      expectedDocumentInstanceId,
      verificationOnly,
      complete: resolve,
      settled: false,
    };
    const complete = (outcome: unknown): void => {
      active.settled = true;
      resolve(outcome);
    };
    active.complete = complete;
    activeTasks.set(task.id, active);
    timer = setTimeout(
      () => complete({ status: "failed", error_code: "native_save_timeout" }),
      timeoutMs,
    );
  });
  const readiness = sendExecuteWhenReady(
    tabId,
    task,
    deadline,
    retryMs,
    readinessController.signal,
    executionId,
    verificationOnly,
  );
  try {
    const raw = await terminal;
    const documentInstanceId = typeof raw === "object" && raw !== null &&
        typeof (raw as { document_instance_id?: unknown }).document_instance_id === "string"
      ? (raw as { document_instance_id: string }).document_instance_id
      : undefined;
    return {
      documentInstanceId,
      outcome: sanitizeNativeSaveResult(raw),
    };
  } finally {
    readinessController.abort();
    await readiness;
    clearTimeout(timer!);
  }
}

export async function runNativeSaveTask(
  task: NativeSaveTask,
  platformSlug: NativeSaveSlug,
  postResult: (result: NativeSaveResult, signal?: AbortSignal) => Promise<void>,
  options: NativeSaveRunnerOptions = {},
): Promise<void> {
  if (!isNativeSaveTask(task) || task.platform_slug !== platformSlug) {
    throw new Error("native-save task does not match the platform slug");
  }
  await ensureNativeSaveTaskRecovery();
  const timeoutMs = Math.max(1, options.timeoutMs ?? DEFAULT_TIMEOUT_MS);
  const deadline = Date.now() + timeoutMs;

  const owner = `native-save:${platformSlug}`;
  let mutexAcquired = false;
  let runtimeListenerRegistrationAttempted = false;
  let tabId: number | null = null;
  let ownsTab = false;
  let reusableTab: chrome.tabs.Tab | null = null;
  let outcome = failedOutcome();
  const listener = (message: unknown, sender: chrome.runtime.MessageSender): void => {
    handleNativeSaveContentResult(message, sender);
  };

  try {
    reusableTab = await reuseExactXiaohongshuTab(task, deadline);
    // A user-triggered XHS save is fully correlated to the exact note route and
    // control. Let it open that route even while background discovery owns the
    // shared tab mutex; otherwise a long bootstrap can strand an already
    // claimed native-save job for minutes. Other platforms keep the mutex
    // because their page executors do not have the same exact-route boundary.
    if (reusableTab === null && task.platform !== "xiaohongshu") {
      mutexAcquired = await acquireMutexBefore(
        owner,
        deadline,
        Math.max(1, options.mutexRetryMs ?? DEFAULT_MUTEX_RETRY_MS),
      );
    }
    if (reusableTab === null && task.platform !== "xiaohongshu" && !mutexAcquired) {
      outcome = timeoutOutcome();
    } else {
      try {
        let tab: chrome.tabs.Tab;
        if (reusableTab) {
          tab = reusableTab;
          if (tab.id === undefined) throw new Error("native-save task tab has no ID");
          tabId = tab.id;
          nativeSaveTaskTabIds.add(tabId);
        } else {
          const blankTab = await createTabBeforeDeadline("about:blank", deadline);
          ownsTab = true;
          if (blankTab.id === undefined) throw new Error("native-save task tab has no ID");
          tabId = blankTab.id;
          nativeSaveTaskTabIds.add(tabId);
          await recordNativeSaveTaskTab(tabId);
          tab = await beforeDeadline(chrome.tabs.update(tabId, {
            active: true,
            url: taskNavigationUrl(task),
          }), deadline) ?? blankTab;
        }
        const loadedTab = await waitForExecutableTab(task, tabId, deadline);
        if (!isAllowedNativeSavePageUrl(task.platform, observedTabUrl(task, loadedTab))) {
          throw new Error("native-save task tab left its allow-listed platform");
        }
        if (mutexAcquired) {
          releaseDispatcherMutex(owner);
          mutexAcquired = false;
        }
        runtimeListenerRegistrationAttempted = true;
        chrome.runtime.onMessage.addListener(listener);
        const mutationExecution = await executeBeforeDeadline(
          task,
          tabId,
          deadline,
          Math.max(1, options.readinessRetryMs ?? DEFAULT_READINESS_RETRY_MS),
        );
        outcome = mutationExecution.outcome;
        if (
          READ_ONLY_VERIFICATION_PLATFORMS.has(task.platform) &&
          outcome.status === "failed" &&
          outcome.error_code === "native_confirmation_not_observed" &&
          mutationExecution.documentInstanceId !== undefined &&
          remainingMs(deadline) > 0
        ) {
          const uncertainOutcome = outcome;
          try {
            await beforeDeadline(chrome.tabs.reload(tabId), deadline);
            const verificationDocumentInstanceId = await waitForNewDocumentInstance(
              tabId,
              task.platform,
              mutationExecution.documentInstanceId,
              deadline,
              Math.max(1, options.readinessRetryMs ?? DEFAULT_READINESS_RETRY_MS),
            );
            const verificationTab = await beforeDeadline(chrome.tabs.get(tabId), deadline);
            if (!isAllowedNativeSavePageUrl(
              task.platform,
              observedTabUrl(task, verificationTab),
            )) {
              throw new Error("native-save verification tab left its allow-listed platform");
            }
            const verificationExecution = await executeBeforeDeadline(
              task,
              tabId,
              deadline,
              Math.max(1, options.readinessRetryMs ?? DEFAULT_READINESS_RETRY_MS),
              true,
              verificationDocumentInstanceId,
            );
            outcome = verificationExecution.outcome.status === "already_synced"
              ? verificationExecution.outcome
              : uncertainOutcome;
          } catch {
            outcome = uncertainOutcome;
          }
        }
      } catch (error) {
        outcome = error instanceof NativeSaveDeadlineError || Date.now() >= deadline
          ? timeoutOutcome()
          : failedOutcome();
      }
    }
    const result: NativeSaveResult = { task_id: task.id, item_key: task.item_key, ...outcome };
    if (!(await postResultWithRetry(postResult, result, options))) {
      throw new Error("native-save result was not acknowledged");
    }
  } finally {
    const active = activeTasks.get(task.id);
    if (active) {
      active.settled = true;
      activeTasks.delete(task.id);
    }
    if (runtimeListenerRegistrationAttempted) {
      for (let attempt = 0; attempt < 2; attempt += 1) {
        try {
          chrome.runtime.onMessage.removeListener(listener);
          break;
        } catch {
          // Retry one transient Chrome failure, then continue independent cleanup.
        }
      }
    }
    if (tabId !== null && ownsTab) {
      if (await removeTabBestEffort(tabId) !== "failed") {
        nativeSaveTaskTabIds.delete(tabId);
        await clearRecordedNativeSaveTaskTab(tabId);
      }
    } else if (tabId !== null) {
      nativeSaveTaskTabIds.delete(tabId);
    }
    if (mutexAcquired) {
      try {
        releaseDispatcherMutex(owner);
      } catch {
        // Do not let mutex cleanup mask the authenticated callback result.
      }
    }
  }
}
