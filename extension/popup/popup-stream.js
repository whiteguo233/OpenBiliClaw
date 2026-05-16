import { getBackendBaseUrl } from "./popup-backend-config.js";

const DEFAULT_BACKEND_URL = "http://127.0.0.1:8420/api";

export function createRuntimeStreamUrl(backendUrl = DEFAULT_BACKEND_URL) {
  const base = backendUrl.replace(/\/$/, "");
  if (base.startsWith("https://")) {
    return `${base.replace("https://", "wss://")}/runtime-stream`;
  }
  return `${base.replace("http://", "ws://")}/runtime-stream`;
}

export function createRuntimeStreamClient({
  backendUrl = null,
  getBackendUrl = getBackendBaseUrl,
  WebSocketImpl = globalThis.WebSocket,
  reconnectDelayMs = 2000,
  // v0.3.14+: cap reconnect delay so popup doesn't flood console with
  // 70+ "ERR_CONNECTION_REFUSED" lines per minute when the backend is
  // genuinely down. Starts at ``reconnectDelayMs`` and doubles per
  // failure up to ``maxReconnectDelayMs``; resets on successful connect.
  maxReconnectDelayMs = 30_000,
  onEvent = () => {},
  onConnect = () => {},
  onDisconnect = () => {},
} = {}) {
  let socket = null;
  let reconnectTimer = null;
  let stopped = false;
  let wasConnected = false;
  let currentReconnectDelay = reconnectDelayMs;

  function scheduleReconnect() {
    if (stopped || reconnectTimer != null) {
      return;
    }
    reconnectTimer = globalThis.setTimeout(() => {
      reconnectTimer = null;
      connect();
    }, currentReconnectDelay);
    // Exponential backoff capped at maxReconnectDelayMs. Reset on
    // successful onopen so a transient blip stays fast-recover.
    currentReconnectDelay = Math.min(
      Math.floor(currentReconnectDelay * 2),
      maxReconnectDelayMs,
    );
  }

  function connect() {
    if (stopped || typeof WebSocketImpl !== "function") {
      return;
    }
    void (async () => {
      const resolvedBackendUrl = backendUrl ?? await getBackendUrl();
      if (stopped) {
        return;
      }
      socket = new WebSocketImpl(createRuntimeStreamUrl(resolvedBackendUrl));
      socket.onopen = () => {
        wasConnected = true;
        currentReconnectDelay = reconnectDelayMs;
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
