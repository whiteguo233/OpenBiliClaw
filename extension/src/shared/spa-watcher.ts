/**
 * SPA navigation watcher.
 *
 * Single-page apps (Bilibili, YouTube, Douyin, XHS) change the URL via
 * history.pushState / replaceState without a full page load. Content
 * scripts that bind once at DOMContentLoaded miss every subsequent
 * navigation. This module centralizes the "wrap history + listen for
 * popstate" dance so the actual content scripts stay short.
 *
 * The factory is decoupled from the global `window` / `history` /
 * `document` references so it can be exercised in node:test without
 * jsdom. Callers pass an environment shim; the default boot helper at
 * the bottom of this file uses the real globals.
 */

/**
 * The minimal browser-history surface we need.
 *
 * We accept this as a parameter rather than reaching for `history`
 * directly so tests can pass a fake.
 */
export interface HistoryLike {
  pushState(data: unknown, unused: string, url?: string | null): void;
  replaceState(data: unknown, unused: string, url?: string | null): void;
}

/**
 * The window event surface we need: add/remove `popstate` listeners,
 * and read the current URL.
 */
export interface SpaWatcherEnv {
  history: HistoryLike;
  addEventListener: (type: "popstate", listener: () => void) => void;
  removeEventListener?: (type: "popstate", listener: () => void) => void;
  getCurrentUrl: () => string;
  /**
   * Optional scheduler. Defaults to queueMicrotask in the real browser
   * environment so the callback fires after pushState's caller returns
   * (URL is already updated). Tests pass a synchronous shim so we don't
   * have to await microtasks.
   */
  schedule?: (cb: () => void) => void;
}

export interface SpaWatcher {
  /** Currently-known URL — updated each time the callback fires. */
  readonly lastUrl: string;
  /**
   * Manually trigger a URL check (e.g. on initial boot, or after a
   * navigation event the watcher didn't observe). Fires the callback
   * only if the URL has changed since the last firing.
   */
  notify(): void;
  /**
   * Restore the original history methods. Tests rely on this; in real
   * usage the watcher is installed for the page's lifetime and never
   * uninstalled, so callers can ignore the returned function.
   */
  uninstall(): void;
}

/**
 * Install an SPA watcher into the given environment.
 *
 * The callback fires once on first install (so the caller doesn't need
 * to handle the bootstrap case separately) and then on every observed
 * URL change. Identical-URL pushes are de-duped.
 */
export function createSpaWatcher(
  env: SpaWatcherEnv,
  onChange: (newUrl: string) => void,
): SpaWatcher {
  const schedule = env.schedule ?? ((cb) => queueMicrotask(cb));

  let lastUrl = env.getCurrentUrl();

  function notify(): void {
    const url = env.getCurrentUrl();
    if (url === lastUrl) return;
    lastUrl = url;
    onChange(url);
  }

  // popstate fires on back/forward
  const popListener = (): void => { notify(); };
  env.addEventListener("popstate", popListener);

  // Wrap pushState / replaceState. We deliberately do NOT touch the
  // prototype — we mutate the specific history instance the env hands
  // us, so two watchers on the same env compose (each sees the other's
  // wrapped version) rather than fighting.
  //
  // Stash the unbound originals so uninstall() restores byte-for-byte
  // the references the caller passed in (===-equal), not a .bind()
  // copy of them.
  const origPushUnbound = env.history.pushState;
  const origReplaceUnbound = env.history.replaceState;
  const origPushBound = origPushUnbound.bind(env.history);
  const origReplaceBound = origReplaceUnbound.bind(env.history);
  env.history.pushState = function (...args: Parameters<HistoryLike["pushState"]>) {
    const ret = origPushBound(...args);
    schedule(notify);
    return ret;
  };
  env.history.replaceState = function (...args: Parameters<HistoryLike["replaceState"]>) {
    const ret = origReplaceBound(...args);
    schedule(notify);
    return ret;
  };

  // Fire once on install so the caller's initial state is correct.
  onChange(lastUrl);

  return {
    get lastUrl() { return lastUrl; },
    notify,
    uninstall() {
      env.history.pushState = origPushUnbound;
      env.history.replaceState = origReplaceUnbound;
      env.removeEventListener?.("popstate", popListener);
    },
  };
}

/**
 * Convenience wrapper for content scripts: install the watcher against
 * the real `window` / `history`. Returns the watcher so the caller can
 * call `uninstall()` in unusual teardown scenarios.
 */
export function installSpaWatcher(onChange: (newUrl: string) => void): SpaWatcher {
  return createSpaWatcher(
    {
      history,
      addEventListener: (t, l) => window.addEventListener(t, l),
      removeEventListener: (t, l) => window.removeEventListener(t, l),
      getCurrentUrl: () => window.location.href,
    },
    onChange,
  );
}
