/**
 * OpenBiliClaw — configurable local-backend endpoint.
 *
 * The popup settings page can override the backend host and port
 * (default 127.0.0.1:8420), so the extension can talk to either the local
 * daemon or a daemon exposed on the LAN. Every fetch and WebSocket in the
 * extension goes through this module so a single source of truth resolves
 * the current host + port at call time.
 *
 * Storage:
 *   chrome.storage.local key ``popup_backend_endpoint`` =
 *     { host: "127.0.0.1", port: 8420 }
 *
 * Both the popup (popup-backend-config.js) and the service worker / content
 * scripts read & write this same key. Each context subscribes to
 * chrome.storage.onChanged so an endpoint change in the popup is picked up
 * by the service worker immediately (it can then reopen its runtime-stream
 * WebSocket against the new origin).
 */

export const DEFAULT_BACKEND_HOST = "127.0.0.1";
export const DEFAULT_BACKEND_PORT = 8420;
export const BACKEND_ENDPOINT_STORAGE_KEY = "popup_backend_endpoint";

export interface BackendEndpoint {
  host: string;
  port: number;
}

const DEFAULT_ENDPOINT: BackendEndpoint = {
  host: DEFAULT_BACKEND_HOST,
  port: DEFAULT_BACKEND_PORT,
};

let cached: BackendEndpoint = { ...DEFAULT_ENDPOINT };
let initialized = false;
let initPromise: Promise<BackendEndpoint> | null = null;
let storageListenerInstalled = false;
const subscribers = new Set<(endpoint: BackendEndpoint) => void>();

interface ChromeStorageLocalLike {
  get?: (
    key: string,
    callback: (items: Record<string, unknown>) => void,
  ) => void;
  set?: (items: Record<string, unknown>, callback?: () => void) => void;
}

interface ChromeStorageOnChangedLike {
  addListener?: (
    callback: (
      changes: Record<string, { newValue?: unknown; oldValue?: unknown }>,
      areaName: string,
    ) => void,
  ) => void;
}

function getStorageLocal(): ChromeStorageLocalLike | null {
  try {
    const chromeApi = (globalThis as { chrome?: { storage?: { local?: ChromeStorageLocalLike } } })
      .chrome;
    return chromeApi?.storage?.local ?? null;
  } catch {
    return null;
  }
}

function getStorageOnChanged(): ChromeStorageOnChangedLike | null {
  try {
    const chromeApi = (
      globalThis as {
        chrome?: { storage?: { onChanged?: ChromeStorageOnChangedLike } };
      }
    ).chrome;
    return chromeApi?.storage?.onChanged ?? null;
  } catch {
    return null;
  }
}

function parseBackendPort(value: unknown): number | null {
  if (typeof value === "number" && Number.isInteger(value)) {
    return value >= 1 && value <= 65535 ? value : null;
  }
  if (typeof value === "string" && value.trim() !== "") {
    const trimmed = value.trim();
    if (!/^[0-9]+$/.test(trimmed)) {
      return null;
    }
    const parsed = Number(trimmed);
    return Number.isInteger(parsed) && parsed >= 1 && parsed <= 65535 ? parsed : null;
  }
  return null;
}

export function isValidBackendPort(value: unknown): boolean {
  return parseBackendPort(value) !== null;
}

function coercePort(value: unknown): number {
  return parseBackendPort(value) ?? DEFAULT_BACKEND_PORT;
}

function sanitizeEndpoint(raw: unknown): BackendEndpoint {
  if (typeof raw !== "object" || raw === null) {
    return { ...DEFAULT_ENDPOINT };
  }
  const obj = raw as Record<string, unknown>;
  const hostRaw = typeof obj.host === "string" ? obj.host.trim() : "";
  return {
    host: hostRaw || DEFAULT_BACKEND_HOST,
    port: coercePort(obj.port),
  };
}

async function loadFromStorage(): Promise<BackendEndpoint> {
  const storage = getStorageLocal();
  if (!storage?.get) {
    return { ...cached };
  }
  return new Promise<BackendEndpoint>((resolve) => {
    try {
      storage.get?.(BACKEND_ENDPOINT_STORAGE_KEY, (items) => {
        const stored = items?.[BACKEND_ENDPOINT_STORAGE_KEY];
        resolve(stored === undefined ? { ...cached } : sanitizeEndpoint(stored));
      });
    } catch {
      resolve({ ...cached });
    }
  });
}

function installStorageChangeListener(): void {
  if (storageListenerInstalled) return;
  const onChanged = getStorageOnChanged();
  if (!onChanged?.addListener) return;
  try {
    onChanged.addListener((changes, area) => {
      if (area !== "local") return;
      const change = changes[BACKEND_ENDPOINT_STORAGE_KEY];
      if (!change) return;
      const next = sanitizeEndpoint(change.newValue);
      cached = next;
      initialized = true;
      for (const cb of subscribers) {
        try {
          cb(next);
        } catch {
          // Subscriber failures must not break peer subscribers.
        }
      }
    });
    storageListenerInstalled = true;
  } catch {
    // chrome.storage.onChanged not available in this context (e.g. tests).
  }
}

async function ensureLoaded(): Promise<BackendEndpoint> {
  if (initialized) return cached;
  if (initPromise) return initPromise;
  initPromise = (async () => {
    const endpoint = await loadFromStorage();
    cached = endpoint;
    initialized = true;
    installStorageChangeListener();
    return endpoint;
  })();
  return initPromise;
}

export async function getBackendEndpoint(): Promise<BackendEndpoint> {
  return ensureLoaded();
}

export async function getBackendOrigin(): Promise<string> {
  const ep = await ensureLoaded();
  return `http://${ep.host}:${ep.port}`;
}

export async function apiUrl(path: string): Promise<string> {
  const ep = await ensureLoaded();
  const suffix = path.startsWith("/") ? path : `/${path}`;
  return `http://${ep.host}:${ep.port}/api${suffix}`;
}

export async function wsUrl(path: string): Promise<string> {
  const ep = await ensureLoaded();
  const suffix = path.startsWith("/") ? path : `/${path}`;
  return `ws://${ep.host}:${ep.port}/api${suffix}`;
}

export function isValidBackendHost(value: unknown): boolean {
  if (typeof value !== "string") return false;
  const trimmed = value.trim();
  if (trimmed === "" || trimmed === "localhost") return true;
  // IPv4
  if (/^(\d{1,3}\.){3}\d{1,3}$/.test(trimmed)) {
    return trimmed.split(".").every((p) => {
      const n = Number(p);
      return n >= 0 && n <= 255;
    });
  }
  // Hostname (simple check)
  if (/^[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?)*$/.test(trimmed)) {
    return true;
  }
  return false;
}

export async function updateBackendEndpoint(
  host: unknown,
  port: unknown,
): Promise<BackendEndpoint> {
  if (!isValidBackendPort(port)) {
    throw new Error("Backend port must be an integer between 1 and 65535");
  }
  const hostStr = typeof host === "string" ? host.trim() : "";
  if (hostStr !== "" && !isValidBackendHost(hostStr)) {
    throw new Error("Backend host must be a valid IP address or hostname");
  }
  const endpoint: BackendEndpoint = {
    host: hostStr || DEFAULT_BACKEND_HOST,
    port: coercePort(port),
  };
  cached = endpoint;
  initialized = true;
  const storage = getStorageLocal();
  if (storage?.set) {
    await new Promise<void>((resolve) => {
      try {
        storage.set?.({ [BACKEND_ENDPOINT_STORAGE_KEY]: endpoint }, () => resolve());
      } catch {
        resolve();
      }
    });
  }
  for (const cb of subscribers) {
    try {
      cb(endpoint);
    } catch {
      // ignore subscriber failures
    }
  }
  return endpoint;
}

export async function updateBackendPort(value: unknown): Promise<BackendEndpoint> {
  if (!isValidBackendPort(value)) {
    throw new Error("Backend port must be an integer between 1 and 65535");
  }
  const port = coercePort(value);
  const endpoint: BackendEndpoint = { host: cached.host || DEFAULT_BACKEND_HOST, port };
  cached = endpoint;
  initialized = true;
  const storage = getStorageLocal();
  if (storage?.set) {
    await new Promise<void>((resolve) => {
      try {
        storage.set?.({ [BACKEND_ENDPOINT_STORAGE_KEY]: endpoint }, () => resolve());
      } catch {
        resolve();
      }
    });
  }
  // chrome.storage.onChanged will fan out to other contexts (e.g. the
  // service worker), but the local context still needs to notify its own
  // subscribers synchronously — onChanged does not fire in the writer.
  for (const cb of subscribers) {
    try {
      cb(endpoint);
    } catch {
      // ignore subscriber failures
    }
  }
  return endpoint;
}

export function onBackendEndpointChange(
  callback: (endpoint: BackendEndpoint) => void,
): () => void {
  subscribers.add(callback);
  // Lazy-install once any context cares about changes.
  installStorageChangeListener();
  // Prime cache so the listener has the right baseline.
  void ensureLoaded();
  return () => {
    subscribers.delete(callback);
  };
}

/**
 * Test-only: reset internal cache and subscribers between tests so each
 * test can stub a fresh chrome.storage.local.
 */
export function __resetBackendEndpointForTests(): void {
  cached = { ...DEFAULT_ENDPOINT };
  initialized = false;
  initPromise = null;
  storageListenerInstalled = false;
  subscribers.clear();
}
