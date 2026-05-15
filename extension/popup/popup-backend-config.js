export const DEFAULT_BACKEND_HOST = "127.0.0.1";
export const DEFAULT_BACKEND_PORT = 8420;
export const DEFAULT_BACKEND_BASE_PATH = "/api";

const STORAGE_KEY = "popup_backend_endpoint";

let inMemoryEndpoint = {
  host: DEFAULT_BACKEND_HOST,
  port: DEFAULT_BACKEND_PORT,
  basePath: DEFAULT_BACKEND_BASE_PATH,
};

function getStorageLocal() {
  return globalThis.chrome?.storage?.local ?? null;
}

function normalizePort(value) {
  if (typeof value === "number" && Number.isInteger(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number.parseInt(value.trim(), 10);
    if (Number.isInteger(parsed)) {
      return parsed;
    }
  }
  return null;
}

export function isValidBackendPort(value) {
  const port = normalizePort(value);
  return port !== null && port >= 1 && port <= 65535;
}

function sanitizeEndpoint(raw) {
  const host = String(raw?.host ?? DEFAULT_BACKEND_HOST).trim() || DEFAULT_BACKEND_HOST;
  const basePath = String(raw?.basePath ?? DEFAULT_BACKEND_BASE_PATH).trim() || DEFAULT_BACKEND_BASE_PATH;
  const port = isValidBackendPort(raw?.port) ? normalizePort(raw.port) : DEFAULT_BACKEND_PORT;
  return {
    host,
    port,
    basePath: basePath.startsWith("/") ? basePath : `/${basePath}`,
  };
}

async function storageGet(key) {
  const storage = getStorageLocal();
  if (storage == null || typeof storage.get !== "function") {
    return {};
  }
  return new Promise((resolve) => {
    try {
      storage.get(key, (items) => {
        resolve(items ?? {});
      });
    } catch {
      resolve({});
    }
  });
}

async function storageSet(items) {
  const storage = getStorageLocal();
  if (storage == null || typeof storage.set !== "function") {
    return;
  }
  await new Promise((resolve) => {
    try {
      storage.set(items, () => resolve(undefined));
    } catch {
      resolve(undefined);
    }
  });
}

export async function getBackendEndpointConfig() {
  const items = await storageGet(STORAGE_KEY);
  const endpoint = sanitizeEndpoint(items?.[STORAGE_KEY] ?? inMemoryEndpoint);
  inMemoryEndpoint = endpoint;
  return endpoint;
}

export async function getBackendBaseUrl() {
  const endpoint = await getBackendEndpointConfig();
  return `http://${endpoint.host}:${endpoint.port}${endpoint.basePath}`;
}

export async function updateBackendPort(port) {
  if (!isValidBackendPort(port)) {
    throw new Error("端口必须是 1-65535 的整数");
  }
  const current = await getBackendEndpointConfig();
  const next = sanitizeEndpoint({
    ...current,
    port: normalizePort(port),
  });
  inMemoryEndpoint = next;
  await storageSet({ [STORAGE_KEY]: next });
  return next;
}
