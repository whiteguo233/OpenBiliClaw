export const OFFLINE_BACKEND_POLL_INTERVAL_MS = 1000;

export function createOfflineBackendPoller({
  isOnline,
  checkBackendStatus,
  onOnline,
  setTimeoutImpl = globalThis.setTimeout,
  clearTimeoutImpl = globalThis.clearTimeout,
  delayMs = OFFLINE_BACKEND_POLL_INTERVAL_MS,
} = {}) {
  let timer = null;
  let inFlight = false;
  let stopped = false;

  function shouldPoll() {
    return !stopped && !(typeof isOnline === "function" && isOnline());
  }

  function clearTimer() {
    if (timer === null) return;
    clearTimeoutImpl(timer);
    timer = null;
  }

  function schedule() {
    if (!shouldPoll() || timer !== null) return;
    timer = setTimeoutImpl(() => runProbe(), delayMs);
  }

  async function runProbe() {
    timer = null;
    if (!shouldPoll()) return;
    if (inFlight) {
      schedule();
      return;
    }

    inFlight = true;
    let online = false;
    try {
      online = Boolean(await checkBackendStatus());
    } catch {
      online = false;
    } finally {
      inFlight = false;
    }

    if (!shouldPoll()) return;
    if (online) {
      clearTimer();
      await onOnline?.();
      return;
    }
    schedule();
  }

  return {
    start() {
      stopped = false;
      schedule();
    },
    stop() {
      stopped = true;
      clearTimer();
    },
  };
}
