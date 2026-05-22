/**
 * OpenBiliClaw popup — configurable backend endpoint.
 *
 * Mirrors src/shared/backend-endpoint.ts for popup modules (which load
 * straight from popup/ as native JS, not via the esbuild bundle). Both
 * sides read & write the same chrome.storage.local key, so a change in
 * the popup is picked up by the service worker via chrome.storage.onChanged.
 *
 * Default endpoint is 127.0.0.1:8420. Users can override the host to reach a
 * LAN daemon, or override the port to dodge local port conflicts on Windows
 * (Hyper-V / WSL / Docker reserve random local ports — 18080, 19090, 13000
 * are common safe choices).
 */

export const DEFAULT_BACKEND_HOST = "127.0.0.1";
export const DEFAULT_BACKEND_PORT = 8420;
export const BACKEND_ENDPOINT_STORAGE_KEY = "popup_backend_endpoint";

const DEFAULT_ENDPOINT = {
  host: DEFAULT_BACKEND_HOST,
  port: DEFAULT_BACKEND_PORT,
};

let cached = { ...DEFAULT_ENDPOINT };
let initialized = false;
let initPromise = null;
let storageListenerInstalled = false;
const subscribers = new Set();

function getStorageLocal() {
  try {
    return globalThis.chrome?.storage?.local ?? null;
  } catch {
    return null;
  }
}

function getStorageOnChanged() {
  try {
    return globalThis.chrome?.storage?.onChanged ?? null;
  } catch {
    return null;
  }
}

function parseBackendPort(value) {
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

export function isValidBackendPort(value) {
  return parseBackendPort(value) !== null;
}

function coercePort(value) {
  return parseBackendPort(value) ?? DEFAULT_BACKEND_PORT;
}

function sanitizeEndpoint(raw) {
  if (typeof raw !== "object" || raw === null) {
    return { ...DEFAULT_ENDPOINT };
  }
  const hostRaw = typeof raw.host === "string" ? raw.host.trim() : "";
  return {
    host: hostRaw || DEFAULT_BACKEND_HOST,
    port: coercePort(raw.port),
  };
}

async function loadFromStorage() {
  const storage = getStorageLocal();
  if (typeof storage?.get !== "function") {
    return { ...cached };
  }
  return new Promise((resolve) => {
    try {
      storage.get(BACKEND_ENDPOINT_STORAGE_KEY, (items) => {
        const stored = items?.[BACKEND_ENDPOINT_STORAGE_KEY];
        resolve(stored === undefined ? { ...cached } : sanitizeEndpoint(stored));
      });
    } catch {
      resolve({ ...cached });
    }
  });
}

function installStorageChangeListener() {
  if (storageListenerInstalled) return;
  const onChanged = getStorageOnChanged();
  if (typeof onChanged?.addListener !== "function") return;
  try {
    onChanged.addListener((changes, area) => {
      if (area !== "local") return;
      const change = changes?.[BACKEND_ENDPOINT_STORAGE_KEY];
      if (!change) return;
      const next = sanitizeEndpoint(change.newValue);
      cached = next;
      initialized = true;
      for (const cb of subscribers) {
        try {
          cb(next);
        } catch {
          // Ignore subscriber failures so peers still get notified.
        }
      }
    });
    storageListenerInstalled = true;
  } catch {
    // chrome.storage.onChanged unavailable (tests).
  }
}

async function ensureLoaded() {
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

export async function getBackendEndpointConfig() {
  return ensureLoaded();
}

export async function getBackendOrigin() {
  const ep = await ensureLoaded();
  return `http://${ep.host}:${ep.port}`;
}

export async function getBackendBaseUrl() {
  const ep = await ensureLoaded();
  return `http://${ep.host}:${ep.port}/api`;
}

export async function getBackendWsBaseUrl() {
  const ep = await ensureLoaded();
  return `ws://${ep.host}:${ep.port}/api`;
}

export function isValidBackendHost(value) {
  if (typeof value !== "string") return false;
  const trimmed = value.trim();
  if (trimmed === "" || trimmed === "localhost") return true;
  if (/^(\d{1,3}\.){3}\d{1,3}$/.test(trimmed)) {
    return trimmed.split(".").every((p) => {
      const n = Number(p);
      return n >= 0 && n <= 255;
    });
  }
  if (/^[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?)*$/.test(trimmed)) {
    return true;
  }
  return false;
}

export async function updateBackendEndpoint(host, port) {
  if (!isValidBackendPort(port)) {
    throw new Error("端口必须是 1-65535 的整数");
  }
  const hostStr = typeof host === "string" ? host.trim() : "";
  if (hostStr !== "" && !isValidBackendHost(hostStr)) {
    throw new Error("后端地址必须是有效的 IP 地址或主机名");
  }
  const endpoint = {
    host: hostStr || DEFAULT_BACKEND_HOST,
    port: coercePort(port),
  };
  cached = endpoint;
  initialized = true;
  const storage = getStorageLocal();
  if (typeof storage?.set === "function") {
    await new Promise((resolve) => {
      try {
        storage.set({ [BACKEND_ENDPOINT_STORAGE_KEY]: endpoint }, () => resolve(undefined));
      } catch {
        resolve(undefined);
      }
    });
  }
  for (const cb of subscribers) {
    try {
      cb(endpoint);
    } catch {
      // ignore
    }
  }
  return endpoint;
}

export async function updateBackendPort(value) {
  if (!isValidBackendPort(value)) {
    throw new Error("端口必须是 1-65535 的整数");
  }
  const port = coercePort(value);
  const endpoint = { host: cached.host || DEFAULT_BACKEND_HOST, port };
  cached = endpoint;
  initialized = true;
  const storage = getStorageLocal();
  if (typeof storage?.set === "function") {
    await new Promise((resolve) => {
      try {
        storage.set({ [BACKEND_ENDPOINT_STORAGE_KEY]: endpoint }, () => resolve(undefined));
      } catch {
        resolve(undefined);
      }
    });
  }
  // Same context's onChanged does not fire for its own writes; notify
  // local subscribers synchronously.
  for (const cb of subscribers) {
    try {
      cb(endpoint);
    } catch {
      // ignore
    }
  }
  return endpoint;
}

export function onBackendEndpointChange(callback) {
  subscribers.add(callback);
  installStorageChangeListener();
  void ensureLoaded();
  return () => {
    subscribers.delete(callback);
  };
}

/**
 * Test-only: reset module state so a test can stub a fresh
 * chrome.storage.local without inheriting the previous test's cache.
 */
export function __resetBackendEndpointForTests() {
  cached = { ...DEFAULT_ENDPOINT };
  initialized = false;
  initPromise = null;
  storageListenerInstalled = false;
  subscribers.clear();
}
