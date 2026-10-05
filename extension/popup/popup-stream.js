import { getBackendBaseUrl } from "./popup-backend-config.js";
import { readPopupSessionToken } from "./popup-device-auth.js";

const DEFAULT_BACKEND_URL = "http://127.0.0.1:8420/api";

const POOL_SNAPSHOT_FIELDS = [
  "pool_available_count", "pool_raw_count", "pool_pending_count",
  "last_replenished_count", "last_discovered_count",
];

function eventUpdatesRuntimeLifecycle(event) {
  return ["refresh.started", "refresh.strategy", "refresh.failed", "refresh.pool_updated"].includes(event?.type)
    || typeof event?.discovery_failure_message === "string";
}

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

export function createRuntimeStreamClient({
  // ``backendUrl`` stays as a test-only override. Production callers
  // omit it and ``resolveBackendUrl`` reads the configured endpoint at
  // each (re)connect, so a settings-page port change rebinds the WS to
  // the new origin without a full popup reload.
  backendUrl = null,
  resolveBackendUrl = getBackendBaseUrl,
  resolveSessionToken = readPopupSessionToken,
  WebSocketImpl = globalThis.WebSocket,
  reconnectDelayMs = 1000,
  onEvent = () => {},
  onConnect = () => {},
  onDisconnect = () => {},
  fetchStatus = null,
  onStatusSnapshot = () => {},
} = {}) {
  let socket = null;
  let reconnectTimer = null;
  let stopped = false;
  let wasConnected = false;
  let statusGeneration = 0;
  let pendingStatus = null;

  async function reconcileStatus(connectedSocket) {
    if (typeof fetchStatus !== "function") return;
    const generation = statusGeneration;
    const pending = { socket: connectedSocket, pool: {} };
    pendingStatus = pending;
    try {
      const status = await fetchStatus();
      if (!stopped && socket === connectedSocket && pendingStatus === pending && generation === statusGeneration) {
        // Count-only events cannot supply a missed terminal state. Preserve
        // their newer fields while filling in the lifecycle from HTTP.
        onStatusSnapshot(Object.keys(pending.pool).length ? { ...status, ...pending.pool } : status);
      }
    } catch {
      // Keep the current UI on a failed catch-up; the live stream remains active.
    } finally {
      if (pendingStatus === pending) pendingStatus = null;
    }
  }

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
    statusGeneration += 1;
    socket.onopen = () => {
      if (stopped || socket !== nextSocket) return;
      wasConnected = true;
      onConnect();
      // HTTP startup reads can precede subscription and miss a terminal event.
      // Reconcile status only; the consumer must preserve already-visible cards.
      void reconcileStatus(nextSocket);
    };
    socket.onmessage = (event) => {
      if (stopped || socket !== nextSocket) return;
      try {
        const payload = JSON.parse(event.data);
        // Heartbeats/activity/transport wake-ups do not supersede the snapshot.
        if (eventUpdatesRuntimeLifecycle(payload)) statusGeneration += 1;
        if (pendingStatus?.socket === nextSocket) {
          for (const key of POOL_SNAPSHOT_FIELDS) {
            if (typeof payload?.[key] === "number") pendingStatus.pool[key] = payload[key];
          }
          if (typeof payload?.pool_available_count === "number") {
            pendingStatus.pool.initialized = true;
            if (payload.pool_available_count > 0) pendingStatus.pool.discovery_failure_message = "";
          }
          if (Array.isArray(payload?.recent_pool_topics)) {
            pendingStatus.pool.recent_pool_topics = [...payload.recent_pool_topics];
          }
        }
        onEvent(payload);
      } catch {
        // Ignore malformed payloads and keep the stream alive.
      }
    };
    socket.onclose = () => {
      if (socket !== nextSocket) return;
      statusGeneration += 1;
      pendingStatus = null;
      socket = null;
      if (wasConnected) {
        wasConnected = false;
        if (!stopped) {
          onDisconnect();
        }
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
      const token = await resolveSessionToken();
      if (stopped) return;
      attachSocket(new WebSocketImpl(createRuntimeStreamUrl(resolved, token)));
    })();
  }

  function disconnect() {
    stopped = true;
    statusGeneration += 1;
    pendingStatus = null;
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
