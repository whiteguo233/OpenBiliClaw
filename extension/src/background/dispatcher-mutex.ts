/**
 * Cross-source dispatcher mutex.
 *
 * Bootstrap import tasks can open foreground tabs. Discovery tasks
 * should run in background tabs, but all task bridges still share
 * the same service-worker lifecycle and long-running browser slots.
 * Without coordination, daemon's continuous producers can start at
 * the same moment the user runs a manual fetch, resulting in task
 * tabs racing each other and occasionally grabbing browser focus.
 *
 * This module owns the single source of truth: at any moment, **at
 * most one** dispatcher's task may hold the shared task slot.
 * Each dispatcher acquires before opening its tab and releases when
 * the task completes / fails / times out. If acquire fails (someone
 * else holds the slot), the dispatcher should bail early —
 * the alarm-driven poll will retry the task in 60s.
 *
 * Lives in its own module so both background dispatchers can share
 * one global variable inside the same service-worker process. No
 * persistence — the mutex resets when the service worker restarts,
 * which is correct: a SW restart kills any in-flight tabs anyway.
 */

let _holder: string | null = null;
let _heldSince: number = 0;

const STALE_HOLD_TIMEOUT_MS = 6 * 60 * 1000; // 6 minutes — longer than
// the longest plausible bootstrap (4 scopes × ~25s = 100s + slack).
// If something holds the mutex past this window we assume the holder
// crashed and forcibly release.

/**
 * Try to acquire the cross-source mutex for ``ownerLabel`` (e.g.
 * "xhs" or "dy"). Returns true if acquired (caller should proceed),
 * false if another dispatcher is currently holding (caller should
 * bail; their next alarm tick will retry).
 *
 * Stale holds (older than STALE_HOLD_TIMEOUT_MS) are auto-released
 * to recover from crashed dispatchers.
 */
export function tryAcquireDispatcherMutex(ownerLabel: string): boolean {
  if (_holder !== null) {
    if (Date.now() - _heldSince > STALE_HOLD_TIMEOUT_MS) {
      // Stale hold — previous owner crashed without releasing.
      // eslint-disable-next-line no-console
      console.warn(
        `[OpenBiliClaw] dispatcher-mutex: forcibly evicting stale holder ${_holder} (${
          (Date.now() - _heldSince) / 1000
        }s old)`,
      );
      _holder = null;
    } else {
      return false;
    }
  }
  _holder = ownerLabel;
  _heldSince = Date.now();
  return true;
}

/**
 * Release the mutex. Idempotent. Releasing a mutex held by someone
 * else is a no-op (logs a warning) — this prevents a buggy dispatcher
 * from yanking the slot from under a healthy peer.
 */
export function releaseDispatcherMutex(ownerLabel: string): void {
  if (_holder === null) return;
  if (_holder !== ownerLabel) {
    // eslint-disable-next-line no-console
    console.warn(
      `[OpenBiliClaw] dispatcher-mutex: ${ownerLabel} tried to release a slot held by ${_holder} — ignoring`,
    );
    return;
  }
  _holder = null;
  _heldSince = 0;
}

/** Diagnostic: who currently holds the slot, or null. */
export function dispatcherMutexHolder(): string | null {
  return _holder;
}
