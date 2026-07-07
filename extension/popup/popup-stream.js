import { getBackendBaseUrl } from "./popup-backend-config.js";

const DEFAULT_BACKEND_URL = "http://127.0.0.1:8420/api";
const AUTH_TOKEN_KEY = "obc_auth_token";

export function createRuntimeStreamUrl(backendUrl = DEFAULT_BACKEND_URL, token = null) {
  const base = backendUrl.replace(/\/$/, "");
  let wsUrl;
  if (base.startsWith("https://")) {
    wsUrl = `${base.replace("https://", "wss://")}/runtime-stream`;
  } else {
    wsUrl = `${base.replace("http://", "ws://")}/runtime-stream`;
  }
  if (token) {
    wsUrl += `?token=${encodeURIComponent(token)}`;
  }
  return wsUrl;
}

/** Read the cached auth token from chrome.storage.local (if available). */
async function readStoredToken() {
  try {
    const storage = globalThis.chrome?.storage?.local;
    if (!storage?.get) return null;
    return await new Promise((resolve) => {
      storage.get([AUTH_TOKEN_KEY], (items) => {
        const v = items?.[AUTH_TOKEN_KEY];
        resolve(typeof v === "string" && v.trim() ? v.trim() : null);
      });
    });
  } catch {
    return null;
  }
}

export function createRuntimeStreamClient({
  // ``backendUrl`` stays as a test-only override. Production callers
  // omit it and ``resolveBackendUrl`` reads the configured endpoint at
  // each (re)connect, so a settings-page port change rebinds the WS to
  // the new origin without a full popup reload.
  backendUrl = null,
  resolveBackendUrl = getBackendBaseUrl,
  WebSocketImpl = globalThis.WebSocket,
  reconnectDelayMs = 1000,
  onEvent = () => {},
  onConnect = () => {},
  onDisconnect = () => {},
} = {}) {
  let socket = null;
  let reconnectTimer = null;
  let stopped = false;
  let wasConnected = false;

  function scheduleReconnect() {
    if (stopped || reconnectTimer != null) {
      return;
    }
    reconnectTimer = globalThis.setTimeout(() => {
      reconnectTimer = null;
      connect();
    }, reconnectDelayMs);
  }

  function attachSocket(nextSocket) {
    socket = nextSocket;
    socket.onopen = () => {
      wasConnected = true;
      onConnect();
    };
    socket.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        onEvent(payload);
      } catch {
        // Ignore malformed payloads and keep the stream alive.
      }
    };
    socket.onclose = () => {
      socket = null;
      if (wasConnected) {
        wasConnected = false;
        onDisconnect();
      }
      scheduleReconnect();
    };
  }

  function connect() {
    if (stopped || typeof WebSocketImpl !== "function") {
      return;
    }
    if (backendUrl != null) {
      // Synchronous path preserves tests that drive the client with an
      // explicit backendUrl and a fake WebSocket constructor — they
      // expect socket creation on the same tick as connect().
      attachSocket(new WebSocketImpl(createRuntimeStreamUrl(backendUrl)));
      return;
    }
    void (async () => {
      let resolved;
      try {
        resolved = await resolveBackendUrl();
      } catch {
        scheduleReconnect();
        return;
      }
      const token = await readStoredToken();
      if (stopped) return;
      attachSocket(new WebSocketImpl(createRuntimeStreamUrl(resolved, token)));
    })();
  }

  function disconnect() {
    stopped = true;
    if (reconnectTimer != null) {
      globalThis.clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    socket?.close?.();
    socket = null;
  }

  return {
    connect,
    disconnect,
  };
}
