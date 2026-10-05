import { apiUrl } from "../shared/backend-endpoint.ts";
import { authenticatedFetch } from "../shared/auth.ts";
import { createTaskTab } from "./task-tab.ts";
import {
  actionsForE2EPlatform,
  E2E_PLATFORM_URLS,
  NATIVE_SAVE_E2E_TARGETS,
  type E2EActionExecutionResult,
  type E2EContentExecuteMessage,
  type E2EPlatform,
  type E2EPlatformExecutionResult,
  type ExtensionE2ERuntimeEvent,
  isExtensionE2ERuntimeEvent,
} from "../shared/e2e.ts";
import type {
  NativeSaveAction,
  NativeSavePlatform,
} from "../shared/native-save.ts";

export interface NativeSaveE2EAuthorization {
  allow_state_changing: true;
  platform: NativeSavePlatform;
  action: NativeSaveAction;
  content_id: string;
  expected_target: string;
}

export interface SafeNativeSaveE2EResult {
  platform: NativeSavePlatform;
  action: NativeSaveAction;
  content_id: string;
  expected_target: string;
  task_status:
    | "pending"
    | "syncing"
    | "synced"
    | "already_synced"
    | "login_required"
    | "rate_limited"
    | "unsupported"
    | "extension_required"
    | "failed";
  error_code: string;
}

const NATIVE_SAVE_E2E_AUTHORIZATION_KEYS = new Set([
  "allow_state_changing",
  "platform",
  "action",
  "content_id",
  "expected_target",
]);
const NATIVE_SAVE_E2E_RESULT_KEYS = new Set(["task_status", "error_code"]);
const NATIVE_SAVE_E2E_STATUSES = new Set<SafeNativeSaveE2EResult["task_status"]>([
  "pending",
  "syncing",
  "synced",
  "already_synced",
  "login_required",
  "rate_limited",
  "unsupported",
  "extension_required",
  "failed",
]);
const NATIVE_SAVE_E2E_ACTIONS = new Set<NativeSaveAction>(["favorite", "watch_later"]);
const NATIVE_SAVE_E2E_ERROR_CODES: Readonly<
  Record<SafeNativeSaveE2EResult["task_status"], ReadonlySet<string>>
> = {
  pending: new Set([""]),
  syncing: new Set([""]),
  synced: new Set([""]),
  already_synced: new Set([""]),
  login_required: new Set([""]),
  rate_limited: new Set([""]),
  unsupported: new Set(["unsupported_content_type"]),
  extension_required: new Set(["extension_unavailable"]),
  failed: new Set([
    "adapter_exception",
    "adapter_timeout",
    "extension_task_timeout",
    "interrupted",
    "invalid_adapter_result",
    "item_heartbeat_failed",
    "native_confirmation_not_observed",
    "native_content_not_ready",
    "native_control_not_found",
    "native_dialog_not_opened",
    "native_request_rejected",
    "native_save_failed",
    "native_save_timeout",
    "native_target_not_found",
    "not_saved_locally",
    "sync_already_in_progress",
  ]),
};

function isExactRecord(value: unknown, keys: ReadonlySet<string>): value is Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const actualKeys = Object.keys(value);
  return actualKeys.length === keys.size && actualKeys.every((key) => keys.has(key));
}

function isPublicNativeSaveContentId(platform: NativeSavePlatform, value: unknown): value is string {
  if (typeof value !== "string" || value !== value.trim() || /[\p{C}\s]/u.test(value)) {
    return false;
  }
  const patterns: Readonly<Record<NativeSavePlatform, RegExp>> = {
    youtube: /^[A-Za-z0-9_-]{11}$/,
    xiaohongshu: /^[0-9a-f]{24}$/,
    douyin: /^[0-9]{5,30}$/,
    twitter: /^[0-9]{5,30}$/,
    zhihu: /^(?:question|answer|article):[0-9]+$/,
    reddit: /^t[13]_[a-z0-9]+$/,
  };
  return patterns[platform].test(value);
}

/** Validate the exact, non-secret authorization envelope for one real native-save write. */
export function isAuthorizedNativeSaveE2ERequest(
  value: unknown,
): value is NativeSaveE2EAuthorization {
  if (!isExactRecord(value, NATIVE_SAVE_E2E_AUTHORIZATION_KEYS)) return false;
  if (value.allow_state_changing !== true || typeof value.platform !== "string") return false;
  if (!Object.hasOwn(NATIVE_SAVE_E2E_TARGETS, value.platform)) return false;
  const platform = value.platform as NativeSavePlatform;
  if (!NATIVE_SAVE_E2E_ACTIONS.has(value.action as NativeSaveAction)) return false;
  const action = value.action as NativeSaveAction;
  if (!isPublicNativeSaveContentId(platform, value.content_id)) return false;
  return value.expected_target === NATIVE_SAVE_E2E_TARGETS[platform][action];
}

/** Build the only result shape allowed in the native-save E2E results ledger. */
export function buildSafeNativeSaveE2EResult(
  authorization: unknown,
  result: unknown,
): SafeNativeSaveE2EResult | null {
  if (!isAuthorizedNativeSaveE2ERequest(authorization)) return null;
  if (!isExactRecord(result, NATIVE_SAVE_E2E_RESULT_KEYS)) return null;
  if (!NATIVE_SAVE_E2E_STATUSES.has(result.task_status as SafeNativeSaveE2EResult["task_status"])) {
    return null;
  }
  if (
    typeof result.error_code !== "string" ||
    !NATIVE_SAVE_E2E_ERROR_CODES[
      result.task_status as SafeNativeSaveE2EResult["task_status"]
    ].has(result.error_code)
  ) {
    return null;
  }
  const taskStatus = result.task_status as SafeNativeSaveE2EResult["task_status"];
  return {
    platform: authorization.platform,
    action: authorization.action,
    content_id: authorization.content_id,
    expected_target: authorization.expected_target,
    task_status: taskStatus,
    error_code: result.error_code,
  };
}

interface E2EContentExecuteResponse {
  status: "ok" | "failed";
  actions: E2EActionExecutionResult[];
  error?: string;
}

let activeRunId: string | null = null;

type E2ECaptureFlushHook = () => Promise<void> | void;

export async function handleE2ERuntimeEvent(
  event: unknown,
  flushCapturedEvents?: E2ECaptureFlushHook,
): Promise<boolean> {
  if (!isExtensionE2ERuntimeEvent(event)) {
    return false;
  }

  const dedicatedNativeSave = event.platforms.length === 0 &&
    Object.keys(event.actions ?? {}).length === 0 &&
    event.native_save_authorization !== undefined;
  if (dedicatedNativeSave) {
    const authorization = event.native_save_authorization;
    if (!isAuthorizedNativeSaveE2ERequest(authorization)) return true;
    if (activeRunId !== null) {
      await safePostNativeSaveE2EResult(
        event,
        buildSafeNativeSaveE2EResult(authorization, {
          task_status: "failed",
          error_code: "native_save_failed",
        })!,
      );
      return true;
    }
    activeRunId = event.run_id;
    try {
      const result = await executeAuthorizedNativeSaveE2E(event, authorization);
      await safePostNativeSaveE2EResult(event, result);
    } finally {
      activeRunId = null;
    }
    return true;
  }

  const nativeSaveMutationPlatforms = requestedNativeSaveMutationPlatforms(event);
  if (nativeSaveMutationPlatforms.length > 0) {
    const error = hasValidNativeSaveE2EAuthorization(event)
      ? "native-save e2e requires durable broker execution"
      : "native-save e2e authorization required";
    await safePostE2EResult(event, buildNativeSaveRefusalResults(event, error));
    return true;
  }

  if (activeRunId !== null) {
    await safePostE2EResult(event, buildConcurrentFailureResults(event, activeRunId));
    return true;
  }

  activeRunId = event.run_id;
  const platformResults: E2EPlatformExecutionResult[] = [];
  try {
    for (const platform of event.platforms) {
      platformResults.push(await executePlatformE2ERun(event, platform));
    }
    await runFlushHook(flushCapturedEvents);
    await safePostE2EResult(event, platformResults);
  } finally {
    activeRunId = null;
  }

  return true;
}

const NATIVE_SAVE_E2E_TERMINAL_STATUSES = new Set<SafeNativeSaveE2EResult["task_status"]>([
  "synced",
  "already_synced",
  "login_required",
  "rate_limited",
  "unsupported",
  "extension_required",
  "failed",
]);

function failedNativeSaveE2EResult(
  authorization: NativeSaveE2EAuthorization,
  errorCode: "native_save_failed" | "native_save_timeout" = "native_save_failed",
): SafeNativeSaveE2EResult {
  return buildSafeNativeSaveE2EResult(authorization, {
    task_status: "failed",
    error_code: errorCode,
  })!;
}

function pendingNativeSaveE2EResult(
  authorization: NativeSaveE2EAuthorization,
  taskStatus: "pending" | "syncing" = "pending",
): SafeNativeSaveE2EResult {
  return buildSafeNativeSaveE2EResult(authorization, {
    task_status: taskStatus,
    error_code: "",
  })!;
}

interface NativeSaveE2ETaskItem {
  taskStatus: SafeNativeSaveE2EResult["task_status"];
  errorCode: string;
}

function parseNativeSaveE2ETask(
  value: unknown,
  authorization: NativeSaveE2EAuthorization,
  expectedTaskId?: string,
): { taskId: string; item: NativeSaveE2ETaskItem } | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  const response = value as Record<string, unknown>;
  if (typeof response.task_id !== "string" ||
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(
      response.task_id,
    ) ||
    (expectedTaskId !== undefined && response.task_id !== expectedTaskId) ||
    !Array.isArray(response.items) || response.items.length !== 1) {
    return null;
  }
  const rawItem = response.items[0];
  if (typeof rawItem !== "object" || rawItem === null || Array.isArray(rawItem)) return null;
  const item = rawItem as Record<string, unknown>;
  const expectedItemKey = `${authorization.platform}:${authorization.content_id}`;
  const expectedResolvedAction: NativeSaveAction = authorization.action === "watch_later" &&
      authorization.platform !== "youtube"
    ? "favorite"
    : authorization.action;
  if (item.item_key !== expectedItemKey || typeof item.status !== "string" ||
    !NATIVE_SAVE_E2E_STATUSES.has(item.status as SafeNativeSaveE2EResult["task_status"]) ||
    typeof item.error_code !== "string") {
    return null;
  }
  const taskStatus = item.status as SafeNativeSaveE2EResult["task_status"];
  const unrouted = (taskStatus === "pending" || taskStatus === "syncing") &&
    item.resolved_target === "";
  if (unrouted) {
    if (item.resolved_action !== authorization.action || item.error_code !== "") return null;
  } else if (item.resolved_action !== expectedResolvedAction ||
    item.resolved_target !== authorization.expected_target) {
    return null;
  }
  return {
    taskId: response.task_id,
    item: {
      taskStatus,
      errorCode: item.error_code,
    },
  };
}

async function readJsonResponse(response: Response): Promise<unknown> {
  if (!response.ok) throw new Error(`native-save e2e request failed: ${response.status}`);
  return response.json();
}

class NativeSaveE2ETimeoutError extends Error {}

type NativeSaveE2EFetch = (
  url: string | URL,
  init?: RequestInit,
) => Promise<Response>;

async function valueBeforeDeadline<T>(deadline: number, value: Promise<T>): Promise<T> {
  const timeoutMs = deadline - Date.now();
  if (timeoutMs <= 0) throw new NativeSaveE2ETimeoutError("native-save e2e timed out");
  let timeoutId: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      value,
      new Promise<T>((_resolve, reject) => {
        timeoutId = setTimeout(
          () => reject(new NativeSaveE2ETimeoutError("native-save e2e timed out")),
          timeoutMs,
        );
      }),
    ]);
  } finally {
    if (timeoutId !== undefined) clearTimeout(timeoutId);
  }
}

export async function authenticatedFetchBefore(
  deadline: number,
  url: string | Promise<string>,
  init: RequestInit = {},
  fetchImpl: NativeSaveE2EFetch = authenticatedFetch,
): Promise<Response> {
  const resolvedUrl = await valueBeforeDeadline(deadline, Promise.resolve(url));
  const timeoutMs = deadline - Date.now();
  if (timeoutMs <= 0) throw new NativeSaveE2ETimeoutError("native-save e2e timed out");
  const controller = new AbortController();
  let timeoutId: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      fetchImpl(resolvedUrl, { ...init, signal: controller.signal }),
      new Promise<Response>((_resolve, reject) => {
        timeoutId = setTimeout(() => {
          controller.abort();
          reject(new NativeSaveE2ETimeoutError("native-save e2e timed out"));
        }, timeoutMs);
      }),
    ]);
  } finally {
    if (timeoutId !== undefined) clearTimeout(timeoutId);
  }
}

async function sleepBefore(deadline: number, requestedMs: number): Promise<void> {
  const remainingMs = deadline - Date.now();
  if (remainingMs <= 0) return;
  await new Promise<void>((resolve) => setTimeout(resolve, Math.min(requestedMs, remainingMs)));
}

async function executeAuthorizedNativeSaveE2E(
  event: ExtensionE2ERuntimeEvent,
  authorization: NativeSaveE2EAuthorization,
): Promise<SafeNativeSaveE2EResult> {
  const deadline = event.native_save_execution_deadline_ms;
  if (typeof deadline !== "number") return pendingNativeSaveE2EResult(authorization);
  try {
    const created = parseNativeSaveE2ETask(
      await readJsonResponse(
        await authenticatedFetchBefore(
          deadline,
          apiUrl(`/saved/${authorization.action}/sync`),
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              item_keys: [`${authorization.platform}:${authorization.content_id}`],
            }),
          },
        ),
      ),
      authorization,
    );
    if (created === null) return pendingNativeSaveE2EResult(authorization);
    let task = created;
    while (true) {
      const safe = buildSafeNativeSaveE2EResult(authorization, {
        task_status: task.item.taskStatus,
        error_code: task.item.errorCode,
      });
      if (safe === null) return pendingNativeSaveE2EResult(authorization);
      if (NATIVE_SAVE_E2E_TERMINAL_STATUSES.has(safe.task_status)) return safe;
      if (Date.now() >= deadline) return safe;
      const polled = parseNativeSaveE2ETask(
        await readJsonResponse(
          await authenticatedFetchBefore(
            deadline,
            apiUrl(`/saved-sync/tasks/${created.taskId}`),
          ),
        ),
        authorization,
        created.taskId,
      );
      if (polled === null) return pendingNativeSaveE2EResult(authorization);
      task = polled;
      if (!NATIVE_SAVE_E2E_TERMINAL_STATUSES.has(task.item.taskStatus)) {
        await sleepBefore(deadline, 250);
      }
    }
  } catch {
    return pendingNativeSaveE2EResult(authorization);
  }
}

function requestedNativeSaveMutationPlatforms(event: ExtensionE2ERuntimeEvent): E2EPlatform[] {
  return event.platforms.filter((platform) =>
    actionsForE2EPlatform(event, platform).some(
      (action) => action === "favorite" || action === "bookmark",
    )
  );
}

function hasValidNativeSaveE2EAuthorization(event: ExtensionE2ERuntimeEvent): boolean {
  const mutationPlatforms = [...new Set(requestedNativeSaveMutationPlatforms(event))];
  if (mutationPlatforms.length === 0) return true;
  if (mutationPlatforms.length !== 1) return false;
  const raw = event as ExtensionE2ERuntimeEvent & { native_save_authorization?: unknown };
  const authorization = raw.native_save_authorization;
  return (
    isAuthorizedNativeSaveE2ERequest(authorization) &&
    authorization.action === "favorite" &&
    authorization.platform === mutationPlatforms[0]
  );
}

function buildNativeSaveRefusalResults(
  event: ExtensionE2ERuntimeEvent,
  error: string,
): E2EPlatformExecutionResult[] {
  return event.platforms.map((platform) => ({
    platform,
    status: "failed",
    actions: [],
    error,
  }));
}

async function runFlushHook(flushCapturedEvents: E2ECaptureFlushHook | undefined): Promise<void> {
  if (!flushCapturedEvents) return;
  try {
    await flushCapturedEvents();
  } catch (error) {
    console.warn(
      "[OpenBiliClaw] Extension E2E capture flush failed:",
      error instanceof Error ? error.message : String(error),
    );
  }
}

function buildConcurrentFailureResults(
  event: ExtensionE2ERuntimeEvent,
  currentRunId: string,
): E2EPlatformExecutionResult[] {
  return event.platforms.map((platform) => ({
    platform,
    status: "failed",
    actions: [],
    error: `e2e run already in progress: ${currentRunId}`,
  }));
}

async function executePlatformE2ERun(
  event: ExtensionE2ERuntimeEvent,
  platform: E2EPlatform,
): Promise<E2EPlatformExecutionResult> {
  try {
    const tab = await openOrReusePlatformTab(platform);
    if (typeof tab.id !== "number") {
      throw new Error(`Missing tab id for ${platform}`);
    }

    const timeoutMs = timeoutMsForEvent(event);
    const completedTab = await waitForTabComplete(tab.id, timeoutMs);
    const actions = actionsForE2EPlatform(event, platform);
    const message: E2EContentExecuteMessage = {
      action: "OBC_E2E_EXECUTE",
      runId: event.run_id,
      platform,
      actions,
      allowStateChanging: event.allow_state_changing === true,
    };
    const response = normalizeContentResponse(
      await sendMessageWithTimeout(tab.id, message, timeoutMs),
    );

    return {
      platform,
      status: response.status,
      url: completedTab.url ?? tab.url,
      actions: response.actions,
      ...(response.error ? { error: response.error } : {}),
    };
  } catch (error) {
    return {
      platform,
      status: "failed",
      actions: [],
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

async function openOrReusePlatformTab(platform: E2EPlatform): Promise<chrome.tabs.Tab> {
  const targetUrl = E2E_PLATFORM_URLS[platform];
  const targetHost = new URL(targetUrl).host;
  const tabs = await chrome.tabs.query({});
  const existing = tabs.find((tab) => sameHost(tab.url, targetHost));

  if (existing?.id !== undefined) {
    const updated = await chrome.tabs.update(existing.id, {
      active: true,
      url: targetUrl,
    });
    if (!updated) {
      throw new Error(`Missing updated tab for ${platform}`);
    }
    return updated;
  }

  return createTaskTab({ active: true, url: targetUrl });
}

async function waitForTabComplete(tabId: number, timeoutMs: number): Promise<chrome.tabs.Tab> {
  return new Promise<chrome.tabs.Tab>((resolve, reject) => {
    let settled = false;
    const listener = (updatedTabId: number, changeInfo: { status?: string }): void => {
      if (updatedTabId !== tabId || changeInfo.status !== "complete") return;
      void chrome.tabs
        .get(tabId)
        .then((tab) => {
          finish(() => resolve(tab));
        })
        .catch(() => {
          finish(() => resolve({ id: tabId, status: "complete" } as chrome.tabs.Tab));
        });
    };
    const timer = setTimeout(() => {
      finish(() => reject(new Error(`Timed out waiting for tab ${tabId} to finish loading`)));
    }, timeoutMs);
    const finish = (complete: () => void): void => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      chrome.tabs.onUpdated.removeListener(listener);
      complete();
    };

    chrome.tabs.onUpdated.addListener(listener);
    void chrome.tabs
      .get(tabId)
      .then((tab) => {
        if (tab.status === "complete") {
          finish(() => resolve(tab));
        }
      })
      .catch((error: unknown) => {
        finish(() => reject(error));
      });
  });
}

async function sendMessageWithTimeout(
  tabId: number,
  message: E2EContentExecuteMessage,
  timeoutMs: number,
): Promise<unknown> {
  return new Promise<unknown>((resolve, reject) => {
    let settled = false;
    const timer = setTimeout(() => {
      finish(() =>
        reject(new Error(`Timed out waiting for OBC_E2E_EXECUTE response from tab ${tabId}`)),
      );
    }, timeoutMs);
    const finish = (complete: () => void): void => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      complete();
    };

    void chrome.tabs
      .sendMessage(tabId, message)
      .then((response) => {
        finish(() => resolve(response));
      })
      .catch((error: unknown) => {
        finish(() => reject(error));
      });
  });
}

function sameHost(url: string | undefined, targetHost: string): boolean {
  if (!url) return false;
  try {
    return new URL(url).host === targetHost;
  } catch {
    return false;
  }
}

function normalizeContentResponse(value: unknown): E2EContentExecuteResponse {
  if (typeof value !== "object" || value === null) {
    return {
      status: "failed",
      actions: [],
      error: "Invalid OBC_E2E_EXECUTE response",
    };
  }

  const response = value as Partial<E2EContentExecuteResponse>;
  return {
    status: response.status === "ok" ? "ok" : "failed",
    actions: Array.isArray(response.actions) ? response.actions : [],
    ...(typeof response.error === "string" && response.error
      ? { error: response.error }
      : {}),
  };
}

function timeoutMsForEvent(event: ExtensionE2ERuntimeEvent): number {
  return Math.max(0.001, event.timeout_seconds ?? 45) * 1000;
}

async function postE2EResult(
  event: ExtensionE2ERuntimeEvent,
  platforms: E2EPlatformExecutionResult[],
): Promise<void> {
  const response = await authenticatedFetch(await apiUrl("/extension/e2e/result"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      run_id: event.run_id,
      token: event.token,
      platforms,
    }),
  });
  if (!response.ok) {
    throw new Error(`result POST failed: ${response.status}`);
  }
}

async function safePostE2EResult(
  event: ExtensionE2ERuntimeEvent,
  platforms: E2EPlatformExecutionResult[],
): Promise<void> {
  try {
    await postE2EResult(event, platforms);
  } catch (error) {
    console.warn(
      "[OpenBiliClaw] Extension E2E result POST failed:",
      error instanceof Error ? error.message : String(error),
    );
  }
}

async function postNativeSaveE2EResult(
  event: ExtensionE2ERuntimeEvent,
  result: SafeNativeSaveE2EResult,
): Promise<void> {
  const callbackDeadline = event.native_save_callback_deadline_ms;
  if (typeof callbackDeadline !== "number") throw new Error("native-save callback deadline missing");
  const response = await authenticatedFetchBefore(
    callbackDeadline,
    apiUrl("/extension/e2e/result"),
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        run_id: event.run_id,
        token: event.token,
        native_save_result: result,
      }),
    },
  );
  if (!response.ok) throw new Error(`native-save result POST failed: ${response.status}`);
}

async function safePostNativeSaveE2EResult(
  event: ExtensionE2ERuntimeEvent,
  result: SafeNativeSaveE2EResult,
): Promise<void> {
  try {
    await postNativeSaveE2EResult(event, result);
  } catch (error) {
    console.warn(
      "[OpenBiliClaw] Native-save E2E result POST failed:",
      error instanceof Error ? error.message : String(error),
    );
  }
}
