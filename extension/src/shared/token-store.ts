/**
 * OpenBiliClaw — auth token cache.
 *
 * Reads/writes the session token in chrome.storage.local and keeps an
 * in-memory cache so synchronous callers (e.g. apiUrl/wsUrl) don't need
 * to await storage.  This module imports NOTHING from the rest of the
 * extension — it is the leaf of the dependency chain and intentionally
 * avoids any circular-import risk.
 */

const AUTH_TOKEN_KEY = "obc_auth_token";

let cachedToken: string | null = null;
let tokenLoaded = false;

interface ChromeStorageLike {
  get?: (key: string | string[], cb: (items: Record<string, unknown>) => void) => void;
  set?: (items: Record<string, unknown>, cb?: () => void) => void;
  remove?: (key: string | string[], cb?: () => void) => void;
}

function getStorage(): ChromeStorageLike | null {
  try {
    const c = (globalThis as { chrome?: { storage?: { local?: ChromeStorageLike } } }).chrome;
    return c?.storage?.local ?? null;
  } catch {
    return null;
  }
}

export async function ensureTokenLoaded(): Promise<string | null> {
  if (tokenLoaded) return cachedToken;
  const storage = getStorage();
  if (!storage?.get) return null;
  return new Promise((resolve) => {
    storage.get!([AUTH_TOKEN_KEY], (items) => {
      const v = items?.[AUTH_TOKEN_KEY];
      cachedToken = typeof v === "string" && v.trim() ? v.trim() : null;
      tokenLoaded = true;
      resolve(cachedToken);
    });
  });
}

/** Synchronous read — returns the cached token (may be null before first load). */
export function getToken(): string | null {
  return cachedToken;
}

export async function setToken(token: string): Promise<void> {
  cachedToken = token;
  tokenLoaded = true;
  const storage = getStorage();
  if (storage?.set) {
    await new Promise<void>((resolve) => {
      storage.set!({ [AUTH_TOKEN_KEY]: token }, () => resolve());
    });
  }
}

export async function clearToken(): Promise<void> {
  cachedToken = null;
  tokenLoaded = true;
  const storage = getStorage();
  if (storage?.remove) {
    await new Promise<void>((resolve) => {
      storage.remove!(AUTH_TOKEN_KEY, () => resolve());
    });
  }
}
