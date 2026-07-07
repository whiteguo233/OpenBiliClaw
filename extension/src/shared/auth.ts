/**
 * OpenBiliClaw — auth login + autoLogin.
 *
 * Calls POST /api/auth/login with the stored password to obtain a session
 * token (bearer mode), then caches it via token-store.ts.  The password is
 * also persisted so autoLogin can work across service-worker restarts.
 *
 * Dependency chain: auth.ts → backend-endpoint.ts → token-store.ts → (none)
 * No circular imports.
 */

import { apiUrl } from "./backend-endpoint.js";
import { ensureTokenLoaded, getToken, setToken } from "./token-store.js";

const AUTH_PASSWORD_KEY = "obc_auth_password";

interface ChromeStorageLike {
  get?: (key: string | string[], cb: (items: Record<string, unknown>) => void) => void;
  set?: (items: Record<string, unknown>, cb?: () => void) => void;
}

function getStorage(): ChromeStorageLike | null {
  try {
    const c = (globalThis as { chrome?: { storage?: { local?: ChromeStorageLike } } }).chrome;
    return c?.storage?.local ?? null;
  } catch {
    return null;
  }
}

export async function login(password: string): Promise<string | null> {
  const url = await apiUrl("/auth/login");
  try {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    if (!resp.ok) return null;
    const data = (await resp.json()) as { token?: string; ok?: boolean };
    if (!data.ok || !data.token) return null;
    await setToken(data.token);
    await savePassword(password);
    return data.token;
  } catch {
    return null;
  }
}

export async function autoLogin(): Promise<boolean> {
  const existing = await ensureTokenLoaded();
  if (existing) return true;
  const password = await getStoredPassword();
  if (!password) return false;
  return (await login(password)) !== null;
}

export async function savePassword(password: string): Promise<void> {
  const storage = getStorage();
  if (storage?.set) {
    await new Promise<void>((resolve) => {
      storage.set!({ [AUTH_PASSWORD_KEY]: password }, () => resolve());
    });
  }
}

export async function getStoredPassword(): Promise<string | null> {
  const storage = getStorage();
  if (!storage?.get) return null;
  return new Promise((resolve) => {
    storage.get!([AUTH_PASSWORD_KEY], (items) => {
      const v = items?.[AUTH_PASSWORD_KEY];
      resolve(typeof v === "string" && v ? v : null);
    });
  });
}

export { getToken } from "./token-store.js";
