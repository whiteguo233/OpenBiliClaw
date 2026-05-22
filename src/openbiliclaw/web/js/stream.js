/**
 * WebSocket runtime-stream client for mobile web.
 * Mirrors extension popup-stream.js without Chrome dependencies.
 */

function buildWsUrl() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/api/runtime-stream`;
}

export function createStreamClient({
  reconnectDelayMs = 2000,
  maxReconnectDelayMs = 30_000,
  onEvent = () => {},
  onConnect = () => {},
  onDisconnect = () => {},
} = {}) {
  let socket = null;
  let reconnectTimer = null;
  let stopped = false;
  let wasConnected = false;
  let currentDelay = reconnectDelayMs;

  function scheduleReconnect() {
    if (stopped || reconnectTimer != null) return;
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      connect();
    }, currentDelay);
    currentDelay = Math.min(Math.floor(currentDelay * 2), maxReconnectDelayMs);
  }

  function connect() {
    if (stopped) return;
    try {
      socket = new WebSocket(buildWsUrl());
    } catch {
      scheduleReconnect();
      return;
    }
    socket.onopen = () => {
      wasConnected = true;
      currentDelay = reconnectDelayMs;
      onConnect();
    };
    socket.onmessage = (event) => {
      try { onEvent(JSON.parse(event.data)); } catch { /* ignore */ }
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

  function disconnect() {
    stopped = true;
    if (reconnectTimer != null) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    socket?.close?.();
    socket = null;
  }

  return { connect, disconnect };
}
