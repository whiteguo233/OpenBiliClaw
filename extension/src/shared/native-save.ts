export type NativeSaveStatus =
  | "synced"
  | "already_synced"
  | "login_required"
  | "rate_limited"
  | "unsupported"
  | "failed";

export type NativeSaveAction = "favorite" | "watch_later";
export type NativeSaveFailureCode =
  | "native_save_failed"
  | "native_save_timeout"
  | "native_content_not_ready"
  | "native_control_not_found"
  | "native_dialog_not_opened"
  | "native_target_not_found"
  | "native_request_rejected"
  | "native_confirmation_not_observed";
export type NativeSavePlatform =
  | "youtube"
  | "xiaohongshu"
  | "douyin"
  | "twitter"
  | "zhihu"
  | "reddit"
  | "tiktok";
export type NativeSaveSlug = "yt" | "xhs" | "dy" | "x" | "zhihu" | "reddit" | "tiktok";

export interface NativeSaveTask {
  id: string;
  type: "native_save";
  platform: NativeSavePlatform;
  platform_slug: NativeSaveSlug;
  item_key: string;
  content_id: string;
  content_url: string;
  content_type: string;
  requested_action: NativeSaveAction;
  resolved_action: NativeSaveAction;
  target_label: string;
}

export interface NativeSaveResult {
  task_id: string;
  item_key: string;
  status: NativeSaveStatus;
  error_code: string;
  error_message: string;
}

export interface SanitizedNativeSaveOutcome {
  status: NativeSaveStatus;
  error_code: string;
  error_message: string;
}

export const NATIVE_SAVE_PLATFORM_CONTRACT: Readonly<
  Record<NativeSavePlatform, { slug: NativeSaveSlug; hosts: readonly string[] }>
> = {
  youtube: { slug: "yt", hosts: ["youtube.com", "youtu.be"] },
  xiaohongshu: { slug: "xhs", hosts: ["xiaohongshu.com"] },
  douyin: { slug: "dy", hosts: ["douyin.com", "iesdouyin.com"] },
  twitter: { slug: "x", hosts: ["x.com", "twitter.com"] },
  zhihu: { slug: "zhihu", hosts: ["zhihu.com"] },
  reddit: { slug: "reddit", hosts: ["reddit.com", "redd.it"] },
  tiktok: { slug: "tiktok", hosts: ["tiktok.com"] },
};

const ACTIONS = new Set<NativeSaveAction>(["favorite", "watch_later"]);
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const TASK_KEYS = new Set([
  "id",
  "type",
  "platform",
  "platform_slug",
  "item_key",
  "content_id",
  "content_url",
  "content_type",
  "requested_action",
  "resolved_action",
  "target_label",
]);

function isSafeText(value: unknown, maxLength: number): value is string {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    value.length <= maxLength &&
    value === value.trim() &&
    !/[\p{C}]/u.test(value)
  );
}

export function isAllowedNativeSaveHostname(platform: NativeSavePlatform, hostname: string): boolean {
  const normalized = hostname.toLowerCase().replace(/\.$/, "");
  return NATIVE_SAVE_PLATFORM_CONTRACT[platform].hosts.some(
    (host) => normalized === host || normalized.endsWith(`.${host}`),
  );
}

export function isAllowedNativeSavePageUrl(
  platform: NativeSavePlatform,
  value: unknown,
): value is string {
  if (!isSafeText(value, 2048)) return false;
  try {
    const url = new URL(value);
    return (
      url.protocol === "https:" &&
      url.username === "" &&
      url.password === "" &&
      url.port === "" &&
      isAllowedNativeSaveHostname(platform, url.hostname)
    );
  } catch {
    return false;
  }
}

function isAllowedContentUrl(platform: NativeSavePlatform, value: unknown): value is string {
  if (!isAllowedNativeSavePageUrl(platform, value)) return false;
  const url = new URL(value);
  if (url.hash !== "") return false;
  if (url.search === "") return true;
  if (platform === "youtube") {
    const videos = url.searchParams.getAll("v");
    let hasOnlyVideoKeys = true;
    url.searchParams.forEach((_item, key) => {
      if (key !== "v") hasOnlyVideoKeys = false;
    });
    return hasOnlyVideoKeys
      && videos.length === 1
      && videos[0].length > 0;
  }
  if (platform === "xiaohongshu") {
    const tokens = url.searchParams.getAll("xsec_token");
    const sources = url.searchParams.getAll("xsec_source");
    let hasOnlyNavigationKeys = true;
    url.searchParams.forEach((_item, key) => {
      if (key !== "xsec_token" && key !== "xsec_source") hasOnlyNavigationKeys = false;
    });
    return hasOnlyNavigationKeys
      && tokens.length === 1
      && tokens[0].length > 0
      && sources.length <= 1
      && (sources.length === 0 || sources[0].length > 0);
  }
  return false;
}

export function isNativeSaveTask(value: unknown): value is NativeSaveTask {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const task = value as Record<string, unknown>;
  if (Object.keys(task).length !== TASK_KEYS.size || Object.keys(task).some((key) => !TASK_KEYS.has(key))) {
    return false;
  }
  if (task.type !== "native_save" || typeof task.platform !== "string") return false;
  if (!Object.hasOwn(NATIVE_SAVE_PLATFORM_CONTRACT, task.platform)) return false;
  const platform = task.platform as NativeSavePlatform;
  const contract = NATIVE_SAVE_PLATFORM_CONTRACT[platform];
  if (task.platform_slug !== contract.slug) return false;
  if (typeof task.id !== "string" || !UUID_PATTERN.test(task.id)) return false;
  if (!isSafeText(task.content_id, 512) || !isSafeText(task.item_key, 768)) return false;
  if (/\s/u.test(task.content_id)) return false;
  if (
    task.content_id.includes(":") &&
    !(platform === "zhihu" && /^(?:question|answer|article):[0-9]+$/.test(task.content_id))
  ) {
    return false;
  }
  if (task.item_key !== `${platform}:${task.content_id}`) return false;
  if (!isAllowedContentUrl(platform, task.content_url)) return false;
  if (!isSafeText(task.content_type, 128) || !isSafeText(task.target_label, 256)) return false;
  if (!ACTIONS.has(task.requested_action as NativeSaveAction)) return false;
  return ACTIONS.has(task.resolved_action as NativeSaveAction);
}

const SAFE_RESULTS: Readonly<Record<string, SanitizedNativeSaveOutcome>> = {
  "synced:": { status: "synced", error_code: "", error_message: "" },
  "already_synced:": { status: "already_synced", error_code: "", error_message: "" },
  "login_required:": {
    status: "login_required",
    error_code: "",
    error_message: "Platform login required",
  },
  "rate_limited:": {
    status: "rate_limited",
    error_code: "",
    error_message: "Platform native save rate limited",
  },
  "unsupported:unsupported_content_type": {
    status: "unsupported",
    error_code: "unsupported_content_type",
    error_message: "Content type is unsupported for platform native save",
  },
  "failed:native_save_failed": {
    status: "failed",
    error_code: "native_save_failed",
    error_message: "Platform native save failed",
  },
  "failed:native_save_timeout": {
    status: "failed",
    error_code: "native_save_timeout",
    error_message: "Platform native-save task timed out",
  },
  "failed:native_content_not_ready": {
    status: "failed",
    error_code: "native_content_not_ready",
    error_message: "Platform native-save content was not ready",
  },
  "failed:native_control_not_found": {
    status: "failed",
    error_code: "native_control_not_found",
    error_message: "Platform native-save control was not found",
  },
  "failed:native_dialog_not_opened": {
    status: "failed",
    error_code: "native_dialog_not_opened",
    error_message: "Platform native-save dialog did not open",
  },
  "failed:native_target_not_found": {
    status: "failed",
    error_code: "native_target_not_found",
    error_message: "Platform native-save target was not found",
  },
  "failed:native_request_rejected": {
    status: "failed",
    error_code: "native_request_rejected",
    error_message: "Platform native-save request was rejected",
  },
  "failed:native_confirmation_not_observed": {
    status: "failed",
    error_code: "native_confirmation_not_observed",
    error_message: "Platform native-save confirmation was not observed",
  },
};

/** Collapse executor-controlled output to the backend's fixed status/code/message allow-list. */
export function sanitizeNativeSaveResult(value: unknown): SanitizedNativeSaveOutcome {
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    const record = value as Record<string, unknown>;
    const status = typeof record.status === "string" ? record.status : "";
    const code = typeof record.error_code === "string" ? record.error_code : "";
    if (status === "failed") {
      const failedKey = `failed:${code}`;
      const safeFailure = SAFE_RESULTS[failedKey];
      if (safeFailure) return { ...safeFailure };
    }
    const safeKeyByStatus: Readonly<Record<string, string>> = {
      synced: "synced:",
      already_synced: "already_synced:",
      login_required: "login_required:",
      rate_limited: "rate_limited:",
      unsupported: "unsupported:unsupported_content_type",
      failed: "failed:native_save_failed",
    };
    const safeKey = safeKeyByStatus[status];
    if (safeKey) return { ...SAFE_RESULTS[safeKey] };
  }
  return { ...SAFE_RESULTS["failed:native_save_failed"] };
}
