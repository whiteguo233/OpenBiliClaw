export type E2EPlatform = "douyin" | "xiaohongshu" | "twitter";

export type E2EAction =
  | "snapshot"
  | "scroll"
  | "click"
  | "like"
  | "favorite"
  | "share"
  | "follow"
  | "repost"
  | "bookmark";

export type E2EActionStatus = "ok" | "skipped" | "failed";

export interface ExtensionE2ERuntimeEvent {
  type: "extension_e2e_run";
  source?: string;
  run_id: string;
  token: string;
  platforms: E2EPlatform[];
  actions?: Partial<Record<E2EPlatform, E2EAction[]>>;
  allow_state_changing?: boolean;
  timeout_seconds?: number;
}

export interface E2EActionExecutionResult {
  action: E2EAction;
  status: E2EActionStatus;
  detail?: string;
  executed?: boolean;
  selector?: string;
  error?: string;
}

export interface E2EPlatformExecutionResult {
  platform: E2EPlatform;
  status: "ok" | "failed";
  url?: string;
  actions: E2EActionExecutionResult[];
  detail?: string;
  error?: string;
}

export interface E2EContentExecuteMessage {
  action: "OBC_E2E_EXECUTE";
  runId: string;
  platform: E2EPlatform;
  actions: E2EAction[];
  allowStateChanging: boolean;
}

export const E2E_PLATFORM_URLS: Record<E2EPlatform, string> = {
  douyin: "https://www.douyin.com/",
  xiaohongshu: "https://www.xiaohongshu.com/explore",
  twitter: "https://x.com/home",
};

export const E2E_STATE_CHANGING_ACTIONS = new Set<E2EAction>([
  "like",
  "favorite",
  "follow",
  "repost",
  "bookmark",
]);

const E2E_PLATFORMS = new Set<E2EPlatform>(["douyin", "xiaohongshu", "twitter"]);
const E2E_ACTIONS = new Set<E2EAction>([
  "snapshot",
  "scroll",
  "click",
  "like",
  "favorite",
  "share",
  "follow",
  "repost",
  "bookmark",
]);
const E2E_DEFAULT_ACTIONS: Record<E2EPlatform, E2EAction[]> = {
  douyin: ["snapshot", "scroll", "click", "share"],
  xiaohongshu: ["snapshot", "scroll", "click", "share"],
  twitter: ["snapshot", "scroll", "click", "share"],
};

function isE2EPlatform(value: unknown): value is E2EPlatform {
  return typeof value === "string" && E2E_PLATFORMS.has(value as E2EPlatform);
}

function isE2EAction(value: unknown): value is E2EAction {
  return typeof value === "string" && E2E_ACTIONS.has(value as E2EAction);
}

function hasValidActionsMap(value: unknown): boolean {
  if (value === undefined) return true;
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  for (const [platform, actions] of Object.entries(value)) {
    if (!isE2EPlatform(platform)) return false;
    if (!Array.isArray(actions)) return false;
    if (!actions.every(isE2EAction)) return false;
  }
  return true;
}

export function isExtensionE2ERuntimeEvent(value: unknown): value is ExtensionE2ERuntimeEvent {
  if (typeof value !== "object" || value === null) return false;
  const event = value as Partial<ExtensionE2ERuntimeEvent>;
  if (event.type !== "extension_e2e_run") return false;
  if (typeof event.run_id !== "string" || event.run_id.trim() === "") return false;
  if (typeof event.token !== "string" || event.token.trim() === "") return false;
  if (!Array.isArray(event.platforms) || event.platforms.length === 0) return false;
  if (!event.platforms.every(isE2EPlatform)) return false;
  if (!hasValidActionsMap(event.actions)) return false;
  if (
    event.allow_state_changing !== undefined &&
    typeof event.allow_state_changing !== "boolean"
  ) {
    return false;
  }
  if (
    event.timeout_seconds !== undefined &&
    (typeof event.timeout_seconds !== "number" ||
      !Number.isFinite(event.timeout_seconds) ||
      event.timeout_seconds <= 0)
  ) {
    return false;
  }
  return true;
}

export function actionsForE2EPlatform(
  event: ExtensionE2ERuntimeEvent,
  platform: E2EPlatform,
): E2EAction[] {
  const requested = event.actions?.[platform];
  const actions = Array.isArray(requested) && requested.length > 0
    ? requested
    : E2E_DEFAULT_ACTIONS[platform];
  return [...new Set(actions)];
}

export function isActionAllowed(action: E2EAction, allowStateChanging: boolean): boolean {
  return allowStateChanging || !E2E_STATE_CHANGING_ACTIONS.has(action);
}
