"use strict";
(() => {
  // src/background/buffer.ts
  var HIGH_FREQUENCY_TYPES = /* @__PURE__ */ new Set(["scroll", "hover", "snapshot"]);
  var STRONG_SIGNAL_TYPES = /* @__PURE__ */ new Set(["comment", "coin", "favorite", "like", "feedback"]);
  function getBucket(event) {
    return Math.floor(event.timestamp / 1e3);
  }
  function buildDedupeKey(event) {
    if (!HIGH_FREQUENCY_TYPES.has(event.type)) return null;
    if (event.type === "hover") {
      const href = String(event.metadata.href ?? "");
      return `hover:${event.url}:${href}`;
    }
    return `${event.type}:${event.url}:${getBucket(event)}`;
  }
  function enqueueBufferedEvent(buffer, event, maxSize) {
    const dedupeKey = buildDedupeKey(event);
    if (dedupeKey) {
      const existingIndex = buffer.findIndex((item) => buildDedupeKey(item) === dedupeKey);
      if (existingIndex >= 0) {
        buffer[existingIndex] = event;
        return buffer;
      }
    }
    buffer.push(event);
    if (buffer.length > maxSize) {
      buffer.shift();
    }
    return buffer;
  }
  function shouldFlushImmediately(event) {
    return STRONG_SIGNAL_TYPES.has(event.type);
  }

  // src/shared/backend-endpoint.ts
  var DEFAULT_BACKEND_HOST = "127.0.0.1";
  var DEFAULT_BACKEND_PORT = 8420;
  var BACKEND_ENDPOINT_STORAGE_KEY = "popup_backend_endpoint";
  var DEFAULT_ENDPOINT = {
    host: DEFAULT_BACKEND_HOST,
    port: DEFAULT_BACKEND_PORT
  };
  var cached = { ...DEFAULT_ENDPOINT };
  var initialized = false;
  var initPromise = null;
  var storageListenerInstalled = false;
  var subscribers = /* @__PURE__ */ new Set();
  function getStorageLocal() {
    try {
      const chromeApi = globalThis.chrome;
      return chromeApi?.storage?.local ?? null;
    } catch {
      return null;
    }
  }
  function getStorageOnChanged() {
    try {
      const chromeApi = globalThis.chrome;
      return chromeApi?.storage?.onChanged ?? null;
    } catch {
      return null;
    }
  }
  function parseBackendPort(value) {
    if (typeof value === "number" && Number.isInteger(value)) {
      return value >= 1 && value <= 65535 ? value : null;
    }
    if (typeof value === "string" && value.trim() !== "") {
      const trimmed = value.trim();
      if (!/^[0-9]+$/.test(trimmed)) {
        return null;
      }
      const parsed = Number(trimmed);
      return Number.isInteger(parsed) && parsed >= 1 && parsed <= 65535 ? parsed : null;
    }
    return null;
  }
  function coercePort(value) {
    return parseBackendPort(value) ?? DEFAULT_BACKEND_PORT;
  }
  function sanitizeEndpoint(raw) {
    if (typeof raw !== "object" || raw === null) {
      return { ...DEFAULT_ENDPOINT };
    }
    const obj = raw;
    const hostRaw = typeof obj.host === "string" ? obj.host.trim() : "";
    return {
      host: hostRaw || DEFAULT_BACKEND_HOST,
      port: coercePort(obj.port)
    };
  }
  async function loadFromStorage() {
    const storage = getStorageLocal();
    if (!storage?.get) {
      return { ...cached };
    }
    return new Promise((resolve) => {
      try {
        storage.get?.(BACKEND_ENDPOINT_STORAGE_KEY, (items) => {
          const stored = items?.[BACKEND_ENDPOINT_STORAGE_KEY];
          resolve(stored === void 0 ? { ...cached } : sanitizeEndpoint(stored));
        });
      } catch {
        resolve({ ...cached });
      }
    });
  }
  function installStorageChangeListener() {
    if (storageListenerInstalled) return;
    const onChanged = getStorageOnChanged();
    if (!onChanged?.addListener) return;
    try {
      onChanged.addListener((changes, area) => {
        if (area !== "local") return;
        const change = changes[BACKEND_ENDPOINT_STORAGE_KEY];
        if (!change) return;
        const next = sanitizeEndpoint(change.newValue);
        cached = next;
        initialized = true;
        for (const cb of subscribers) {
          try {
            cb(next);
          } catch {
          }
        }
      });
      storageListenerInstalled = true;
    } catch {
    }
  }
  async function ensureLoaded() {
    if (initialized) return cached;
    if (initPromise) return initPromise;
    initPromise = (async () => {
      const endpoint = await loadFromStorage();
      cached = endpoint;
      initialized = true;
      installStorageChangeListener();
      return endpoint;
    })();
    return initPromise;
  }
  async function apiUrl(path) {
    const ep = await ensureLoaded();
    const suffix = path.startsWith("/") ? path : `/${path}`;
    return `http://${ep.host}:${ep.port}/api${suffix}`;
  }
  async function wsUrl(path) {
    const ep = await ensureLoaded();
    const suffix = path.startsWith("/") ? path : `/${path}`;
    return `ws://${ep.host}:${ep.port}/api${suffix}`;
  }
  function onBackendEndpointChange(callback) {
    subscribers.add(callback);
    installStorageChangeListener();
    void ensureLoaded();
    return () => {
      subscribers.delete(callback);
    };
  }

  // src/background/xhs-task-dispatcher.ts
  var _MUTEX_STALE_MS = 6 * 60 * 1e3;
  function tryAcquireDispatcherMutex(label) {
    const g = globalThis;
    if (g.__OBC_DISPATCHER_MUTEX_HOLDER__) {
      if (Date.now() - (g.__OBC_DISPATCHER_MUTEX_HELD_SINCE__ ?? 0) > _MUTEX_STALE_MS) {
        g.__OBC_DISPATCHER_MUTEX_HOLDER__ = void 0;
      } else {
        return false;
      }
    }
    g.__OBC_DISPATCHER_MUTEX_HOLDER__ = label;
    g.__OBC_DISPATCHER_MUTEX_HELD_SINCE__ = Date.now();
    return true;
  }
  function releaseDispatcherMutex(label) {
    const g = globalThis;
    if (g.__OBC_DISPATCHER_MUTEX_HOLDER__ === label) {
      g.__OBC_DISPATCHER_MUTEX_HOLDER__ = void 0;
      g.__OBC_DISPATCHER_MUTEX_HELD_SINCE__ = void 0;
    }
  }
  var DEFAULT_POLL_INTERVAL_MS = 45e3;
  var TASK_TIMEOUT_MS = 3e4;
  var BOOTSTRAP_SCROLL_TIMEOUT_PER_ROUND_MS = 3e3;
  var BOOTSTRAP_MAX_TASK_TIMEOUT_MS = 18e4;
  var BOOTSTRAP_MAX_EXTENDED_TASK_TIMEOUT_MS = 36e4;
  var MIN_BOOTSTRAP_SCROLL_WAIT_MS = 500;
  var MAX_BOOTSTRAP_SCROLL_WAIT_MS = 5e3;
  var BOOTSTRAP_CLICKED_NAVIGATION_FALLBACK_MS = 2500;
  var POLL_ALARM_NAME = "openbiliclaw-xhs-task-poll";
  var taskInFlight = false;
  var taskTabId = null;
  var ownsTaskTab = false;
  var taskTimeoutId = null;
  var currentTaskId = null;
  var currentTask = null;
  var bootstrapNavigationCount = 0;
  var bootstrapDebugSteps = [];
  var taskUpdateListener = null;
  var taskNavigationFallbackId = null;
  function buildTaskUrl(task) {
    if (task.type === "search" && task.keyword) {
      return `https://www.xiaohongshu.com/search_result?keyword=${encodeURIComponent(task.keyword)}`;
    }
    if (task.type === "creator" && task.creator_url) {
      return task.creator_url;
    }
    if (task.type === "bootstrap_profile") {
      return "https://www.xiaohongshu.com/explore";
    }
    return null;
  }
  function isValidTask(task) {
    if (typeof task !== "object" || task === null) return false;
    const t = task;
    if (typeof t.id !== "string" || !t.id) return false;
    if (t.type !== "search" && t.type !== "creator" && t.type !== "bootstrap_profile") {
      return false;
    }
    return true;
  }
  function computeTaskTimeoutMs(task) {
    if (task.type !== "bootstrap_profile") return TASK_TIMEOUT_MS;
    const rounds = typeof task.max_scroll_rounds === "number" && Number.isFinite(task.max_scroll_rounds) ? Math.max(0, Math.floor(task.max_scroll_rounds)) : 0;
    if (typeof task.scroll_wait_ms === "number" && Number.isFinite(task.scroll_wait_ms)) {
      const scrollWaitMs = Math.min(
        Math.max(Math.floor(task.scroll_wait_ms), MIN_BOOTSTRAP_SCROLL_WAIT_MS),
        MAX_BOOTSTRAP_SCROLL_WAIT_MS
      );
      return Math.min(
        Math.max(TASK_TIMEOUT_MS, TASK_TIMEOUT_MS + rounds * (scrollWaitMs + 500) * 2),
        BOOTSTRAP_MAX_EXTENDED_TASK_TIMEOUT_MS
      );
    }
    return Math.min(
      Math.max(TASK_TIMEOUT_MS, TASK_TIMEOUT_MS + rounds * BOOTSTRAP_SCROLL_TIMEOUT_PER_ROUND_MS),
      BOOTSTRAP_MAX_TASK_TIMEOUT_MS
    );
  }
  function shouldActivateBeforeExecute(task) {
    if (task.type !== "bootstrap_profile") return false;
    return bootstrapNavigationCount > 0;
  }
  function buildExecuteMessageData(task) {
    const data = { task_id: task.id, type: task.type };
    if (task.scopes !== void 0) data.scopes = task.scopes;
    if (task.max_items_per_scope !== void 0) {
      data.max_items_per_scope = task.max_items_per_scope;
    }
    if (task.max_scroll_rounds !== void 0) data.max_scroll_rounds = task.max_scroll_rounds;
    if (task.scroll_wait_ms !== void 0) data.scroll_wait_ms = task.scroll_wait_ms;
    if (task.max_stagnant_scroll_rounds !== void 0) {
      data.max_stagnant_scroll_rounds = task.max_stagnant_scroll_rounds;
    }
    return data;
  }
  function isRecord(value) {
    return typeof value === "object" && value !== null && !Array.isArray(value);
  }
  function extractBootstrapDebugSteps(debug) {
    if (!isRecord(debug)) return [];
    const bootstrap = debug.xhs_bootstrap;
    if (!isRecord(bootstrap)) return [];
    const steps = bootstrap.steps;
    return Array.isArray(steps) ? steps : [];
  }
  function mergeBootstrapDebugIntoResult(result) {
    const resultSteps = extractBootstrapDebugSteps(result.debug);
    const steps = [...bootstrapDebugSteps, ...resultSteps];
    if (steps.length === 0) return result;
    const debug = isRecord(result.debug) ? { ...result.debug } : {};
    const bootstrap = isRecord(debug.xhs_bootstrap) ? { ...debug.xhs_bootstrap } : {};
    bootstrap.steps = steps;
    debug.xhs_bootstrap = bootstrap;
    return { ...result, debug };
  }
  function bootstrapClickedNextUrl(result) {
    const steps = extractBootstrapDebugSteps(result.debug);
    const last = steps[steps.length - 1];
    return isRecord(last) && last.next_url_clicked === true;
  }
  async function fetchNextTask() {
    try {
      const response = await fetch(await apiUrl("/sources/xhs/next-task"), { method: "GET" });
      if (response.status === 204) return null;
      if (!response.ok) return null;
      const payload = await response.json();
      return isValidTask(payload) ? payload : null;
    } catch {
      return null;
    }
  }
  async function reportTaskResult(result) {
    try {
      await fetch(await apiUrl("/sources/xhs/task-result"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(result)
      });
    } catch {
    }
  }
  function cleanupTask() {
    if (taskTimeoutId !== null) {
      clearTimeout(taskTimeoutId);
      taskTimeoutId = null;
    }
    if (taskUpdateListener !== null) {
      chrome.tabs.onUpdated.removeListener(taskUpdateListener);
      taskUpdateListener = null;
    }
    if (taskNavigationFallbackId !== null) {
      clearTimeout(taskNavigationFallbackId);
      taskNavigationFallbackId = null;
    }
    if (taskTabId !== null && ownsTaskTab) {
      void chrome.tabs.remove(taskTabId).catch(() => {
      });
    }
    taskTabId = null;
    ownsTaskTab = false;
    currentTaskId = null;
    currentTask = null;
    bootstrapNavigationCount = 0;
    bootstrapDebugSteps = [];
    taskInFlight = false;
    releaseDispatcherMutex("xhs");
  }
  function armTaskTimeout(task) {
    if (taskTimeoutId !== null) {
      clearTimeout(taskTimeoutId);
      taskTimeoutId = null;
    }
    taskTimeoutId = setTimeout(() => {
      if (currentTaskId === task.id) {
        void reportTaskResult({ task_id: task.id, urls: [], status: "error", error: "timeout" });
        cleanupTask();
      }
    }, computeTaskTimeoutMs(task));
  }
  async function sendExecuteMessageToTab(tabId, task) {
    if (shouldActivateBeforeExecute(task)) {
      await chrome.tabs.update(tabId, { active: true });
    }
    await chrome.tabs.sendMessage(tabId, {
      action: "XHS_TASK_EXECUTE",
      data: buildExecuteMessageData(task)
    });
  }
  function handleExecuteMessageFailure(task) {
    if (currentTaskId !== task.id) return;
    void reportTaskResult({
      task_id: task.id,
      urls: [],
      status: "error",
      error: "sendMessage_failed"
    });
    cleanupTask();
  }
  function clearNavigationFallback() {
    if (taskNavigationFallbackId !== null) {
      clearTimeout(taskNavigationFallbackId);
      taskNavigationFallbackId = null;
    }
  }
  function armClickedNavigationFallback(task, tabId) {
    clearNavigationFallback();
    taskNavigationFallbackId = setTimeout(() => {
      taskNavigationFallbackId = null;
      if (currentTaskId !== task.id || taskTabId !== tabId) return;
      if (taskUpdateListener !== null) {
        chrome.tabs.onUpdated.removeListener(taskUpdateListener);
        taskUpdateListener = null;
      }
      void sendExecuteMessageToTab(tabId, task).catch(() => handleExecuteMessageFailure(task));
    }, BOOTSTRAP_CLICKED_NAVIGATION_FALLBACK_MS);
  }
  function armTaskLoadListener(task) {
    if (taskUpdateListener !== null) {
      chrome.tabs.onUpdated.removeListener(taskUpdateListener);
      taskUpdateListener = null;
    }
    const listener = (updatedTabId, changeInfo) => {
      if (updatedTabId !== taskTabId || changeInfo.status !== "complete") return;
      if (currentTaskId !== task.id) return;
      chrome.tabs.onUpdated.removeListener(listener);
      if (taskUpdateListener === listener) taskUpdateListener = null;
      clearNavigationFallback();
      void sendExecuteMessageToTab(updatedTabId, task).catch(
        () => handleExecuteMessageFailure(task)
      );
    };
    taskUpdateListener = listener;
    chrome.tabs.onUpdated.addListener(listener);
  }
  async function executeTask(task) {
    if (taskInFlight) return;
    if (!tryAcquireDispatcherMutex("xhs")) return;
    taskInFlight = true;
    currentTaskId = task.id;
    currentTask = task;
    const url = buildTaskUrl(task);
    if (!url) {
      await reportTaskResult({ task_id: task.id, urls: [], status: "error", error: "no_url" });
      cleanupTask();
      return;
    }
    try {
      const tab = await chrome.tabs.create({
        url,
        active: task.type === "bootstrap_profile"
      });
      taskTabId = tab.id ?? null;
      ownsTaskTab = taskTabId !== null;
    } catch {
      await reportTaskResult({ task_id: task.id, urls: [], status: "error", error: "tab_create_failed" });
      cleanupTask();
      return;
    }
    armTaskLoadListener(task);
    armTaskTimeout(task);
  }
  async function handleTaskResult(result) {
    if (!taskInFlight || result.task_id !== currentTaskId) return;
    if (currentTask?.type === "bootstrap_profile" && result.status === "partial") {
      await reportTaskResult(result);
      return;
    }
    if (currentTask?.type === "bootstrap_profile" && result.next_url && taskTabId !== null && bootstrapNavigationCount < 2) {
      const task = currentTask;
      const tabId = taskTabId;
      const clickedNextUrl = bootstrapClickedNextUrl(result);
      bootstrapDebugSteps.push(...extractBootstrapDebugSteps(result.debug));
      bootstrapNavigationCount += 1;
      armTaskLoadListener(task);
      armTaskTimeout(task);
      if (clickedNextUrl) {
        armClickedNavigationFallback(task, tabId);
        return;
      }
      chrome.tabs.update(tabId, { url: result.next_url }).catch(() => {
        if (currentTaskId !== task.id) return;
        void reportTaskResult({
          task_id: task.id,
          urls: [],
          status: "error",
          error: "tab_update_failed"
        });
        cleanupTask();
      });
      return;
    }
    await reportTaskResult(mergeBootstrapDebugIntoResult(result));
    cleanupTask();
  }
  async function pollOnce() {
    if (taskInFlight) return;
    const task = await fetchNextTask();
    if (!task) return;
    await executeTask(task);
  }
  function startXhsTaskPolling(intervalMs = DEFAULT_POLL_INTERVAL_MS) {
    chrome.alarms.create(POLL_ALARM_NAME, {
      periodInMinutes: intervalMs / 6e4
    });
  }
  function handleXhsTaskAlarm(alarmName) {
    if (alarmName !== POLL_ALARM_NAME) return;
    void pollOnce();
  }
  function pollXhsTaskNow() {
    void pollOnce();
  }

  // src/background/dy-task-dispatcher.ts
  var _MUTEX_STALE_MS2 = 6 * 60 * 1e3;
  function tryAcquireDispatcherMutex2(label) {
    const g = globalThis;
    if (g.__OBC_DISPATCHER_MUTEX_HOLDER__) {
      if (Date.now() - (g.__OBC_DISPATCHER_MUTEX_HELD_SINCE__ ?? 0) > _MUTEX_STALE_MS2) {
        g.__OBC_DISPATCHER_MUTEX_HOLDER__ = void 0;
      } else {
        return false;
      }
    }
    g.__OBC_DISPATCHER_MUTEX_HOLDER__ = label;
    g.__OBC_DISPATCHER_MUTEX_HELD_SINCE__ = Date.now();
    return true;
  }
  function releaseDispatcherMutex2(label) {
    const g = globalThis;
    if (g.__OBC_DISPATCHER_MUTEX_HOLDER__ === label) {
      g.__OBC_DISPATCHER_MUTEX_HOLDER__ = void 0;
      g.__OBC_DISPATCHER_MUTEX_HELD_SINCE__ = void 0;
    }
  }
  function debugLog(event, data) {
    void (async () => {
      try {
        await fetch(await apiUrl("/sources/_debug/log"), {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ source: "dy", event, data: data ?? null })
        });
      } catch {
      }
    })();
  }
  var DEFAULT_POLL_INTERVAL_MS2 = 6e4;
  var TASK_TIMEOUT_MS2 = 3e4;
  var SEARCH_TASK_TIMEOUT_MS = 18e4;
  var BOOTSTRAP_PER_ROUND_TIMEOUT_MS = 3e3;
  var BOOTSTRAP_MAX_TASK_TIMEOUT_MS2 = 36e4;
  var POLL_ALARM_NAME2 = "openbiliclaw-dy-task-poll";
  var KNOWN_SCOPES = [
    "dy_post",
    "dy_collect",
    "dy_like",
    "dy_follow"
  ];
  var taskInFlight2 = false;
  var taskTabId2 = null;
  var ownsTaskTab2 = false;
  var taskTimeoutId2 = null;
  var currentTask2 = null;
  var progress = null;
  var searchProgress = null;
  var hotProgress = null;
  var feedProgress = null;
  function isValidDyTask(task) {
    if (typeof task !== "object" || task === null) return false;
    const t = task;
    if (typeof t.id !== "string" || !t.id) return false;
    if (t.type === "search") {
      if (!Array.isArray(t.keywords)) return false;
      return t.keywords.some((keyword) => typeof keyword === "string" && keyword.trim());
    }
    if (t.type === "hot") {
      if (!Array.isArray(t.hot_items)) return false;
      return t.hot_items.some((item) => {
        if (!item || typeof item !== "object") return false;
        const row = item;
        return typeof row.sentence_id === "string" && Boolean(row.sentence_id.trim());
      });
    }
    if (t.type === "feed") {
      if (t.max_items === void 0) return true;
      return typeof t.max_items === "number" && Number.isFinite(t.max_items) && t.max_items > 0;
    }
    if (t.type !== "bootstrap_profile") return false;
    if (t.scopes !== void 0) {
      if (!Array.isArray(t.scopes)) return false;
      for (const s of t.scopes) {
        if (!KNOWN_SCOPES.includes(s)) return false;
      }
    }
    return true;
  }
  function computeDyTaskTimeoutMs(task) {
    if (task.type === "search") {
      const keywordCount = Array.isArray(task.keywords) && task.keywords.length > 0 ? task.keywords.length : 1;
      return Math.min(
        Math.max(SEARCH_TASK_TIMEOUT_MS, keywordCount * SEARCH_TASK_TIMEOUT_MS),
        BOOTSTRAP_MAX_TASK_TIMEOUT_MS2
      );
    }
    if (task.type === "hot") {
      const hotCount = Array.isArray(task.hot_items) && task.hot_items.length > 0 ? task.hot_items.length : 1;
      return Math.min(
        Math.max(TASK_TIMEOUT_MS2, TASK_TIMEOUT_MS2 + hotCount * 7e4),
        BOOTSTRAP_MAX_TASK_TIMEOUT_MS2
      );
    }
    if (task.type === "feed") {
      return Math.min(Math.max(TASK_TIMEOUT_MS2, 6e4), BOOTSTRAP_MAX_TASK_TIMEOUT_MS2);
    }
    const scopeCount = Array.isArray(task.scopes) && task.scopes.length > 0 ? task.scopes.length : 4;
    const rounds = typeof task.max_scroll_rounds === "number" && Number.isFinite(task.max_scroll_rounds) ? Math.max(0, Math.floor(task.max_scroll_rounds)) : 0;
    const scrollBudget = scopeCount * rounds * BOOTSTRAP_PER_ROUND_TIMEOUT_MS;
    return Math.min(
      Math.max(TASK_TIMEOUT_MS2, TASK_TIMEOUT_MS2 + scrollBudget),
      BOOTSTRAP_MAX_TASK_TIMEOUT_MS2
    );
  }
  function shouldFinalizeHotTask({
    accumulatedCount,
    maxItemsTotal,
    currentHotIndex,
    hotItemCount
  }) {
    return accumulatedCount >= maxItemsTotal || currentHotIndex + 1 >= hotItemCount;
  }
  function shouldOpenDyTaskActive(task) {
    return task.type === "bootstrap_profile";
  }
  async function fetchNextTask2() {
    try {
      const resp = await fetch(await apiUrl("/sources/dy/next-task"));
      if (resp.status === 204) return null;
      if (!resp.ok) return null;
      const payload = await resp.json();
      return isValidDyTask(payload) ? payload : null;
    } catch {
      return null;
    }
  }
  async function postTaskResult(result) {
    try {
      await fetch(await apiUrl("/sources/dy/task-result"), {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(result)
      });
    } catch {
    }
  }
  function cleanupTask2() {
    if (taskTimeoutId2 !== null) {
      clearTimeout(taskTimeoutId2);
      taskTimeoutId2 = null;
    }
    if (ownsTaskTab2 && taskTabId2 !== null) {
      try {
        chrome.tabs.remove(taskTabId2);
      } catch {
      }
    }
    taskTabId2 = null;
    ownsTaskTab2 = false;
    currentTask2 = null;
    progress = null;
    searchProgress = null;
    hotProgress = null;
    feedProgress = null;
    taskInFlight2 = false;
    releaseDispatcherMutex2("dy");
  }
  function emptyScopeCounts() {
    return { dy_post: 0, dy_collect: 0, dy_like: 0, dy_follow: 0 };
  }
  function armTaskTimeout2(task) {
    const timeoutMs = computeDyTaskTimeoutMs(task);
    taskTimeoutId2 = setTimeout(async () => {
      await postTaskResult({
        task_id: task.id,
        status: "failed",
        error: "task_timeout"
      });
      cleanupTask2();
    }, timeoutMs);
  }
  function onTabReady(tabId, callback, options = {}) {
    let completed = false;
    let fallbackTimer = null;
    const listener = (updatedId, info) => {
      if (updatedId !== tabId) return;
      if (info.status !== "complete") return;
      runOnce();
    };
    const runOnce = () => {
      if (completed) return;
      completed = true;
      if (fallbackTimer !== null) {
        clearTimeout(fallbackTimer);
        fallbackTimer = null;
      }
      chrome.tabs.onUpdated.removeListener(listener);
      callback();
    };
    chrome.tabs.onUpdated.addListener(listener);
    if (typeof options.fallbackMs === "number" && Number.isFinite(options.fallbackMs) && options.fallbackMs >= 0) {
      fallbackTimer = setTimeout(runOnce, options.fallbackMs);
    }
    void chrome.tabs.get(tabId).then((tab) => {
      if (tab.status === "complete") runOnce();
    }).catch(() => {
    });
  }
  var _lastInjectStatus = "not_attempted";
  function sendScopeExecuteMessage() {
    if (!progress || !taskTabId2) {
      debugLog("sendScopeExecute:no_progress_or_tab", {
        hasProgress: !!progress,
        taskTabId: taskTabId2
      });
      return;
    }
    const scope = progress.scopes[progress.current_scope_idx];
    if (!scope) {
      debugLog("sendScopeExecute:no_scope_at_idx", {
        idx: progress.current_scope_idx
      });
      return;
    }
    debugLog("sendScopeExecute:start", { scope, idx: progress.current_scope_idx });
    void chrome.tabs.sendMessage(taskTabId2, {
      action: "DY_SCOPE_EXECUTE",
      data: {
        task_id: progress.task_id,
        scope,
        max_items_per_scope: progress.max_items_per_scope,
        max_scroll_rounds: progress.max_scroll_rounds,
        max_stagnant_scroll_rounds: progress.max_stagnant_scroll_rounds,
        debug_inject_status: _lastInjectStatus
      }
    }).catch((err) => {
      debugLog("sendScopeExecute:sendMessage_failed", { error: String(err) });
      void handleDyScopeResult({
        task_id: progress.task_id,
        scope,
        items: [],
        scope_count: 0,
        status: "failed",
        error: "sendMessage_failed"
      });
    });
  }
  function buildSearchPageUrl(keyword) {
    return `https://www.douyin.com/search/${encodeURIComponent(keyword)}?type=video`;
  }
  function buildHotPageUrl(sentenceId) {
    return `https://www.douyin.com/hot/${encodeURIComponent(sentenceId)}`;
  }
  function sendSearchExecuteMessage() {
    if (!searchProgress || !taskTabId2) return;
    const keyword = searchProgress.keywords[searchProgress.current_keyword_idx];
    if (!keyword) return;
    void chrome.tabs.sendMessage(taskTabId2, {
      action: "DY_SEARCH_EXECUTE",
      data: {
        task_id: searchProgress.task_id,
        keyword,
        max_items: searchProgress.max_items_per_keyword,
        debug_inject_status: _lastInjectStatus
      }
    }).catch((err) => {
      void handleDySearchResult({
        task_id: searchProgress.task_id,
        keyword,
        items: [],
        scope_count: searchProgress.accumulated_count,
        status: "failed",
        error: `sendMessage_failed: ${String(err)}`
      });
    });
  }
  function sendHotExecuteMessage() {
    if (!hotProgress || !taskTabId2) return;
    const hotItem = hotProgress.hot_items[hotProgress.current_hot_idx];
    if (!hotItem) return;
    void chrome.tabs.sendMessage(taskTabId2, {
      action: "DY_HOT_EXECUTE",
      data: {
        task_id: hotProgress.task_id,
        sentence_id: hotItem.sentence_id,
        word: hotItem.word ?? "",
        max_items: hotProgress.max_items_per_hot,
        debug_inject_status: _lastInjectStatus
      }
    }).catch((err) => {
      void handleDyHotResult({
        task_id: hotProgress.task_id,
        sentence_id: hotItem.sentence_id,
        word: hotItem.word ?? "",
        items: [],
        scope_count: hotProgress.accumulated_count,
        status: "failed",
        error: `sendMessage_failed: ${String(err)}`
      });
    });
  }
  function sendFeedExecuteMessage() {
    if (!feedProgress || !taskTabId2) return;
    void chrome.tabs.sendMessage(taskTabId2, {
      action: "DY_FEED_EXECUTE",
      data: {
        task_id: feedProgress.task_id,
        max_items: feedProgress.max_items,
        debug_inject_status: _lastInjectStatus
      }
    }).catch((err) => {
      void handleDyFeedResult({
        task_id: feedProgress.task_id,
        items: [],
        scope_count: feedProgress.accumulated_count,
        status: "failed",
        error: `sendMessage_failed: ${String(err)}`
      });
    });
  }
  function navigateToCurrentSearch() {
    if (!searchProgress || taskTabId2 === null) return;
    const keyword = searchProgress.keywords[searchProgress.current_keyword_idx];
    if (!keyword) return;
    chrome.tabs.update(taskTabId2, { url: buildSearchPageUrl(keyword) }, () => {
      onTabReady(taskTabId2, () => {
        void injectFetchTapInto(taskTabId2).then(() => {
          debugLog("executeSearchTask:inject_done", { inject_status: _lastInjectStatus });
          sendSearchExecuteMessage();
        });
      }, { fallbackMs: 8e3 });
    });
  }
  function navigateToCurrentHot() {
    if (!hotProgress || taskTabId2 === null) return;
    const hotItem = hotProgress.hot_items[hotProgress.current_hot_idx];
    if (!hotItem) return;
    chrome.tabs.update(taskTabId2, { url: buildHotPageUrl(hotItem.sentence_id) }, () => {
      onTabReady(taskTabId2, () => {
        void injectFetchTapInto(taskTabId2).then(() => {
          debugLog("executeHotTask:inject_done", { inject_status: _lastInjectStatus });
          sendHotExecuteMessage();
        });
      }, { fallbackMs: 1e4 });
    });
  }
  function navigateToFeed() {
    if (!feedProgress || taskTabId2 === null) return;
    chrome.tabs.update(taskTabId2, { url: "https://www.douyin.com/" }, () => {
      onTabReady(taskTabId2, () => {
        void injectFetchTapInto(taskTabId2).then(() => {
          debugLog("executeFeedTask:inject_done", { inject_status: _lastInjectStatus });
          sendFeedExecuteMessage();
        });
      }, { fallbackMs: 8e3 });
    });
  }
  function navigateToCurrentScope() {
    if (!progress || taskTabId2 === null) return;
    sendScopeExecuteMessage();
  }
  async function injectFetchTapInto(tabId) {
    if (typeof chrome === "undefined" || !chrome.scripting) {
      _lastInjectStatus = "scripting_api_missing";
      return;
    }
    try {
      const result = await chrome.scripting.executeScript({
        target: { tabId, allFrames: false },
        files: ["dist/main/dy-fetch-tap.js"],
        world: "MAIN"
      });
      _lastInjectStatus = `ok_results=${Array.isArray(result) ? result.length : "n/a"}`;
    } catch (err) {
      _lastInjectStatus = `error: ${String(err).slice(0, 120)}`;
    }
  }
  function normalizeHotTaskItems(items) {
    const seen = /* @__PURE__ */ new Set();
    const result = [];
    for (const item of items ?? []) {
      const sentenceId = String(item?.sentence_id ?? "").trim();
      if (!sentenceId || seen.has(sentenceId)) continue;
      seen.add(sentenceId);
      result.push({
        sentence_id: sentenceId,
        word: String(item.word ?? "").trim(),
        hot_value: item.hot_value
      });
    }
    return result;
  }
  async function executeTask2(task) {
    debugLog("executeTask:start", { task_id: task.id, taskInFlight: taskInFlight2 });
    if (taskInFlight2) {
      debugLog("executeTask:already_in_flight");
      return;
    }
    const mutexAcquired = tryAcquireDispatcherMutex2("dy");
    debugLog("executeTask:mutex", { acquired: mutexAcquired });
    if (!mutexAcquired) return;
    taskInFlight2 = true;
    currentTask2 = task;
    if (task.type === "search") {
      const keywords = (task.keywords ?? []).map((keyword) => String(keyword).trim()).filter((keyword, index, all) => keyword && all.indexOf(keyword) === index);
      searchProgress = {
        task_id: task.id,
        keywords,
        current_keyword_idx: 0,
        accumulated_count: 0,
        max_items_per_keyword: Math.max(1, Math.floor(task.max_items_per_keyword ?? 20))
      };
      let tab2;
      try {
        tab2 = await chrome.tabs.create({
          url: "https://www.douyin.com/",
          active: shouldOpenDyTaskActive(task)
        });
        debugLog("executeSearchTask:tab_created", { tabId: tab2.id, keywords: keywords.length });
      } catch (err) {
        await postTaskResult({
          task_id: task.id,
          status: "failed",
          error: "tab_create_failed"
        });
        cleanupTask2();
        return;
      }
      taskTabId2 = tab2.id ?? null;
      ownsTaskTab2 = true;
      armTaskTimeout2(task);
      if (taskTabId2 === null || keywords.length === 0) {
        await postTaskResult({
          task_id: task.id,
          status: "failed",
          error: taskTabId2 === null ? "tab_id_unknown" : "missing_keywords"
        });
        cleanupTask2();
        return;
      }
      onTabReady(taskTabId2, () => {
        navigateToCurrentSearch();
      }, { fallbackMs: 5e3 });
      return;
    }
    if (task.type === "hot") {
      const hotItems = normalizeHotTaskItems(task.hot_items);
      const maxItemsTotal = Math.max(1, Math.floor(task.max_items ?? task.max_items_per_hot ?? 20));
      hotProgress = {
        task_id: task.id,
        hot_items: hotItems,
        current_hot_idx: 0,
        accumulated_count: 0,
        max_items_per_hot: Math.max(
          1,
          Math.min(maxItemsTotal, Math.floor(task.max_items_per_hot ?? maxItemsTotal))
        ),
        max_items_total: maxItemsTotal
      };
      let tab2;
      try {
        tab2 = await chrome.tabs.create({
          url: "https://www.douyin.com/",
          active: shouldOpenDyTaskActive(task)
        });
        debugLog("executeHotTask:tab_created", { tabId: tab2.id, hot_count: hotItems.length });
      } catch (err) {
        await postTaskResult({
          task_id: task.id,
          status: "failed",
          error: "tab_create_failed"
        });
        cleanupTask2();
        return;
      }
      taskTabId2 = tab2.id ?? null;
      ownsTaskTab2 = true;
      armTaskTimeout2(task);
      if (taskTabId2 === null || hotItems.length === 0) {
        await postTaskResult({
          task_id: task.id,
          status: "failed",
          error: taskTabId2 === null ? "tab_id_unknown" : "missing_hot_items"
        });
        cleanupTask2();
        return;
      }
      onTabReady(taskTabId2, () => {
        navigateToCurrentHot();
      }, { fallbackMs: 5e3 });
      return;
    }
    if (task.type === "feed") {
      feedProgress = {
        task_id: task.id,
        accumulated_count: 0,
        max_items: Math.max(1, Math.floor(task.max_items ?? 20))
      };
      let tab2;
      try {
        tab2 = await chrome.tabs.create({
          url: "https://www.douyin.com/",
          active: shouldOpenDyTaskActive(task)
        });
        debugLog("executeFeedTask:tab_created", { tabId: tab2.id });
      } catch (err) {
        await postTaskResult({
          task_id: task.id,
          status: "failed",
          error: "tab_create_failed"
        });
        cleanupTask2();
        return;
      }
      taskTabId2 = tab2.id ?? null;
      ownsTaskTab2 = true;
      armTaskTimeout2(task);
      if (taskTabId2 === null) {
        await postTaskResult({
          task_id: task.id,
          status: "failed",
          error: "tab_id_unknown"
        });
        cleanupTask2();
        return;
      }
      onTabReady(taskTabId2, () => {
        navigateToFeed();
      }, { fallbackMs: 5e3 });
      return;
    }
    const scopes = task.scopes && task.scopes.length > 0 ? task.scopes : ["dy_post", "dy_collect", "dy_like", "dy_follow"];
    progress = {
      task_id: task.id,
      scopes,
      current_scope_idx: 0,
      accumulated_counts: emptyScopeCounts(),
      max_items_per_scope: task.max_items_per_scope ?? 300,
      max_scroll_rounds: task.max_scroll_rounds ?? 15,
      max_stagnant_scroll_rounds: task.max_stagnant_scroll_rounds ?? 5
    };
    let tab;
    try {
      tab = await chrome.tabs.create({
        url: "https://www.douyin.com/",
        active: shouldOpenDyTaskActive(task)
      });
      debugLog("executeTask:tab_created", { tabId: tab.id });
    } catch (err) {
      debugLog("executeTask:tab_create_failed", { error: String(err) });
      await postTaskResult({
        task_id: task.id,
        status: "failed",
        error: "tab_create_failed"
      });
      cleanupTask2();
      return;
    }
    taskTabId2 = tab.id ?? null;
    ownsTaskTab2 = true;
    armTaskTimeout2(task);
    if (taskTabId2 === null) {
      await postTaskResult({
        task_id: task.id,
        status: "failed",
        error: "tab_id_unknown"
      });
      cleanupTask2();
      return;
    }
    onTabReady(taskTabId2, () => {
      debugLog("executeTask:tab_ready", { tabId: taskTabId2 });
      void injectFetchTapInto(taskTabId2).then(() => {
        debugLog("executeTask:inject_done", { inject_status: _lastInjectStatus });
        sendScopeExecuteMessage();
      });
    });
  }
  async function handleDyScopeResult(result) {
    debugLog("handleDyScopeResult", {
      scope: result.scope,
      status: result.status,
      items_count: result.items.length,
      scope_count: result.scope_count,
      debug: result.debug
    });
    if (!progress || result.task_id !== progress.task_id) return;
    const expectedScope = progress.scopes[progress.current_scope_idx];
    if (result.scope !== expectedScope) return;
    progress.accumulated_counts[result.scope] = result.scope_count;
    await postTaskResult({
      task_id: progress.task_id,
      status: "partial",
      videos: result.items,
      scope_counts: { ...progress.accumulated_counts },
      debug: {
        scope: result.scope,
        scope_status: result.status,
        ...result.debug ?? {}
      }
    });
    progress.current_scope_idx += 1;
    if (progress.current_scope_idx < progress.scopes.length) {
      navigateToCurrentScope();
      return;
    }
    await postTaskResult({
      task_id: progress.task_id,
      status: "ok",
      videos: [],
      scope_counts: { ...progress.accumulated_counts }
    });
    cleanupTask2();
  }
  async function handleDySearchResult(result) {
    if (!searchProgress || result.task_id !== searchProgress.task_id) return;
    const expectedKeyword = searchProgress.keywords[searchProgress.current_keyword_idx];
    if (result.keyword !== expectedKeyword) return;
    if (result.status === "failed") {
      await postTaskResult({
        task_id: searchProgress.task_id,
        status: "failed",
        error: result.error || "search_failed",
        debug: result.debug
      });
      cleanupTask2();
      return;
    }
    searchProgress.accumulated_count += result.items.length;
    await postTaskResult({
      task_id: searchProgress.task_id,
      status: "partial",
      videos: result.items,
      scope_counts: { dy_search: searchProgress.accumulated_count },
      debug: {
        keyword: result.keyword,
        keyword_status: result.status,
        ...result.debug ?? {}
      }
    });
    searchProgress.current_keyword_idx += 1;
    if (searchProgress.current_keyword_idx < searchProgress.keywords.length) {
      navigateToCurrentSearch();
      return;
    }
    await postTaskResult({
      task_id: searchProgress.task_id,
      status: "ok",
      videos: [],
      scope_counts: { dy_search: searchProgress.accumulated_count }
    });
    cleanupTask2();
  }
  async function handleDyHotResult(result) {
    if (!hotProgress || result.task_id !== hotProgress.task_id) return;
    const expected = hotProgress.hot_items[hotProgress.current_hot_idx];
    if (!expected || result.sentence_id !== expected.sentence_id) return;
    hotProgress.accumulated_count += result.items.length;
    await postTaskResult({
      task_id: hotProgress.task_id,
      status: "partial",
      videos: result.items,
      scope_counts: { dy_hot: hotProgress.accumulated_count },
      debug: {
        sentence_id: result.sentence_id,
        word: result.word,
        hot_status: result.status,
        ...result.debug ?? {},
        ...result.error ? { error: result.error } : {}
      }
    });
    if (shouldFinalizeHotTask({
      accumulatedCount: hotProgress.accumulated_count,
      maxItemsTotal: hotProgress.max_items_total,
      currentHotIndex: hotProgress.current_hot_idx,
      hotItemCount: hotProgress.hot_items.length
    })) {
      await postTaskResult({
        task_id: hotProgress.task_id,
        status: "ok",
        videos: [],
        scope_counts: { dy_hot: hotProgress.accumulated_count }
      });
      cleanupTask2();
      return;
    }
    hotProgress.current_hot_idx += 1;
    if (hotProgress.current_hot_idx < hotProgress.hot_items.length) {
      navigateToCurrentHot();
      return;
    }
    await postTaskResult({
      task_id: hotProgress.task_id,
      status: "ok",
      videos: [],
      scope_counts: { dy_hot: hotProgress.accumulated_count }
    });
    cleanupTask2();
  }
  async function handleDyFeedResult(result) {
    if (!feedProgress || result.task_id !== feedProgress.task_id) return;
    if (result.status === "failed") {
      await postTaskResult({
        task_id: feedProgress.task_id,
        status: "failed",
        error: result.error || "feed_failed",
        debug: result.debug
      });
      cleanupTask2();
      return;
    }
    feedProgress.accumulated_count += result.items.length;
    await postTaskResult({
      task_id: feedProgress.task_id,
      status: "partial",
      videos: result.items,
      scope_counts: { dy_feed: feedProgress.accumulated_count },
      debug: {
        feed_status: result.status,
        ...result.debug ?? {}
      }
    });
    await postTaskResult({
      task_id: feedProgress.task_id,
      status: "ok",
      videos: [],
      scope_counts: { dy_feed: feedProgress.accumulated_count }
    });
    cleanupTask2();
  }
  async function handleTaskResult2(result) {
    if (!currentTask2 || result.task_id !== currentTask2.id) return;
    await postTaskResult(result);
    if (result.status === "partial") return;
    cleanupTask2();
  }
  async function pollNextTask() {
    if (taskInFlight2) return;
    const task = await fetchNextTask2();
    if (!task) return;
    await executeTask2(task);
  }
  function startDyTaskPolling() {
    if (typeof chrome === "undefined" || !chrome.alarms) return;
    chrome.alarms.create(POLL_ALARM_NAME2, {
      periodInMinutes: DEFAULT_POLL_INTERVAL_MS2 / 6e4
    });
  }
  function handleDyTaskAlarm(alarmName) {
    if (alarmName === POLL_ALARM_NAME2) {
      void pollNextTask();
    }
  }
  function pollDyTaskNow() {
    void pollNextTask();
  }
  var handleDyTaskResult = handleTaskResult2;
  var handleDySearchTaskResult = handleDySearchResult;
  var handleDyHotTaskResult = handleDyHotResult;
  var handleDyFeedTaskResult = handleDyFeedResult;

  // src/content/yt/task-executor.ts
  var YT_SCOPE_URLS = {
    yt_history: "https://www.youtube.com/feed/history",
    yt_subscriptions: "https://www.youtube.com/feed/channels",
    yt_likes: "https://www.youtube.com/playlist?list=LL"
  };

  // src/background/yt-task-dispatcher.ts
  var _MUTEX_STALE_MS3 = 6 * 60 * 1e3;
  function tryAcquireDispatcherMutex3(label) {
    const g = globalThis;
    if (g.__OBC_DISPATCHER_MUTEX_HOLDER__) {
      if (Date.now() - (g.__OBC_DISPATCHER_MUTEX_HELD_SINCE__ ?? 0) > _MUTEX_STALE_MS3) {
        g.__OBC_DISPATCHER_MUTEX_HOLDER__ = void 0;
      } else {
        return false;
      }
    }
    g.__OBC_DISPATCHER_MUTEX_HOLDER__ = label;
    g.__OBC_DISPATCHER_MUTEX_HELD_SINCE__ = Date.now();
    return true;
  }
  function releaseDispatcherMutex3(label) {
    const g = globalThis;
    if (g.__OBC_DISPATCHER_MUTEX_HOLDER__ === label) {
      g.__OBC_DISPATCHER_MUTEX_HOLDER__ = void 0;
      g.__OBC_DISPATCHER_MUTEX_HELD_SINCE__ = void 0;
    }
  }
  var DEFAULT_POLL_INTERVAL_MS3 = 6e4;
  var POLL_ALARM_NAME3 = "openbiliclaw-yt-task-poll";
  var BASE_TIMEOUT_MS = 3e4;
  var PER_ROUND_MS = 3e3;
  var MAX_TIMEOUT_MS = 36e4;
  var DEFAULT_SCOPES = [
    "yt_history",
    "yt_subscriptions",
    "yt_likes"
  ];
  function isValidYtTask(task) {
    if (typeof task !== "object" || task === null) return false;
    const t = task;
    if (typeof t.id !== "string" || !t.id) return false;
    if (t.type !== "bootstrap_profile") return false;
    if (t.scopes !== void 0) {
      if (!Array.isArray(t.scopes)) return false;
      for (const s of t.scopes) {
        if (!DEFAULT_SCOPES.includes(s)) return false;
      }
    }
    return true;
  }
  function computeYtTaskTimeoutMs(task) {
    const scopeCount = Array.isArray(task.scopes) && task.scopes.length > 0 ? task.scopes.length : DEFAULT_SCOPES.length;
    const rounds = typeof task.max_scroll_rounds === "number" && Number.isFinite(task.max_scroll_rounds) ? Math.max(0, Math.floor(task.max_scroll_rounds)) : 10;
    const scrollBudget = scopeCount * rounds * PER_ROUND_MS;
    return Math.min(Math.max(BASE_TIMEOUT_MS, BASE_TIMEOUT_MS + scrollBudget), MAX_TIMEOUT_MS);
  }
  var taskInFlight3 = false;
  var taskTabId3 = null;
  var taskTimeoutId3 = null;
  var currentTask3 = null;
  var progress2 = null;
  async function fetchNextTask3() {
    try {
      const resp = await fetch(await apiUrl("/sources/yt/next-task"));
      if (resp.status === 204) return null;
      if (!resp.ok) return null;
      const payload = await resp.json();
      return isValidYtTask(payload) ? payload : null;
    } catch {
      return null;
    }
  }
  async function postTaskResult2(result) {
    try {
      await fetch(await apiUrl("/sources/yt/task-result"), {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(result)
      });
    } catch {
    }
  }
  function cleanupTask3() {
    if (taskTimeoutId3 !== null) {
      clearTimeout(taskTimeoutId3);
      taskTimeoutId3 = null;
    }
    if (taskTabId3 !== null) {
      try {
        chrome.tabs.remove(taskTabId3);
      } catch {
      }
    }
    taskTabId3 = null;
    currentTask3 = null;
    progress2 = null;
    taskInFlight3 = false;
    releaseDispatcherMutex3("yt");
  }
  function armTaskTimeout3(task) {
    const ms = computeYtTaskTimeoutMs(task);
    taskTimeoutId3 = setTimeout(async () => {
      await postTaskResult2({ task_id: task.id, status: "failed", error: "task_timeout" });
      cleanupTask3();
    }, ms);
  }
  function onTabReady2(tabId, callback, options = {}) {
    let completed = false;
    let fallbackTimer = null;
    const runOnce = () => {
      if (completed) return;
      completed = true;
      if (fallbackTimer !== null) {
        clearTimeout(fallbackTimer);
        fallbackTimer = null;
      }
      chrome.tabs.onUpdated.removeListener(listener);
      callback();
    };
    const listener = (updatedId, info) => {
      if (updatedId !== tabId || info.status !== "complete") return;
      runOnce();
    };
    chrome.tabs.onUpdated.addListener(listener);
    if (typeof options.fallbackMs === "number" && Number.isFinite(options.fallbackMs)) {
      fallbackTimer = setTimeout(runOnce, options.fallbackMs);
    }
    void chrome.tabs.get(tabId).then((tab) => {
      if (tab.status === "complete") runOnce();
    }).catch(() => {
    });
  }
  function sendScopeExecuteMessage2() {
    if (!progress2 || taskTabId3 === null) return;
    const scope = progress2.scopes[progress2.current_scope_idx];
    if (!scope) return;
    void chrome.tabs.sendMessage(taskTabId3, {
      action: "YT_SCOPE_EXECUTE",
      data: {
        task_id: progress2.task_id,
        scope,
        max_items_per_scope: progress2.max_items_per_scope,
        max_scroll_rounds: progress2.max_scroll_rounds
      }
    }).catch(() => {
      void handleYtScopeResult({
        task_id: progress2.task_id,
        scope,
        items: [],
        scope_count: 0,
        status: "failed",
        error: "sendMessage_failed"
      });
    });
  }
  function navigateToCurrentScope2() {
    if (!progress2 || taskTabId3 === null) return;
    const scope = progress2.scopes[progress2.current_scope_idx];
    if (!scope) return;
    const url = YT_SCOPE_URLS[scope];
    chrome.tabs.update(taskTabId3, { url }, () => {
      onTabReady2(taskTabId3, sendScopeExecuteMessage2, { fallbackMs: 1e4 });
    });
  }
  async function executeTask3(task) {
    if (taskInFlight3) return;
    if (!tryAcquireDispatcherMutex3("yt")) return;
    taskInFlight3 = true;
    currentTask3 = task;
    const scopes = task.scopes && task.scopes.length > 0 ? task.scopes : [...DEFAULT_SCOPES];
    progress2 = {
      task_id: task.id,
      scopes,
      current_scope_idx: 0,
      accumulated_counts: {},
      max_items_per_scope: task.max_items_per_scope ?? 300,
      max_scroll_rounds: task.max_scroll_rounds ?? 10
    };
    const firstUrl = YT_SCOPE_URLS[scopes[0]];
    let tab;
    try {
      tab = await chrome.tabs.create({ url: firstUrl, active: true });
    } catch {
      await postTaskResult2({ task_id: task.id, status: "failed", error: "tab_create_failed" });
      cleanupTask3();
      return;
    }
    taskTabId3 = tab.id ?? null;
    if (taskTabId3 === null) {
      await postTaskResult2({ task_id: task.id, status: "failed", error: "tab_id_unknown" });
      cleanupTask3();
      return;
    }
    armTaskTimeout3(task);
    onTabReady2(taskTabId3, sendScopeExecuteMessage2, { fallbackMs: 12e3 });
  }
  async function handleYtScopeResult(result) {
    if (!progress2 || result.task_id !== progress2.task_id) return;
    const expectedScope = progress2.scopes[progress2.current_scope_idx];
    if (result.scope !== expectedScope) return;
    progress2.accumulated_counts[result.scope] = result.scope_count;
    await postTaskResult2({
      task_id: progress2.task_id,
      status: "partial",
      items: result.items,
      scope_counts: { ...progress2.accumulated_counts },
      debug: {
        scope: result.scope,
        scope_status: result.status,
        ...result.debug ?? {}
      }
    });
    progress2.current_scope_idx += 1;
    if (progress2.current_scope_idx < progress2.scopes.length) {
      navigateToCurrentScope2();
      return;
    }
    await postTaskResult2({
      task_id: progress2.task_id,
      status: "ok",
      items: [],
      scope_counts: { ...progress2.accumulated_counts }
    });
    cleanupTask3();
  }
  async function pollNextTask2() {
    if (taskInFlight3) return;
    const task = await fetchNextTask3();
    if (!task) return;
    await executeTask3(task);
  }
  function startYtTaskPolling() {
    if (typeof chrome === "undefined" || !chrome.alarms) return;
    chrome.alarms.create(POLL_ALARM_NAME3, { periodInMinutes: DEFAULT_POLL_INTERVAL_MS3 / 6e4 });
  }
  function handleYtTaskAlarm(alarmName) {
    if (alarmName === POLL_ALARM_NAME3) {
      void pollNextTask2();
    }
  }
  function pollYtTaskNow() {
    void pollNextTask2();
  }

  // src/background/notifications.ts
  var NOTIFICATION_PREFIX = "openbiliclaw-recommendation:";
  var COGNITION_NOTIFICATION_PREFIX = "openbiliclaw-cognition:";
  var DELIGHT_NOTIFICATION_PREFIX = "openbiliclaw-delight:";
  function parseNotificationBvid(notificationId) {
    if (!notificationId.startsWith(NOTIFICATION_PREFIX)) {
      return "";
    }
    return notificationId.slice(NOTIFICATION_PREFIX.length);
  }
  function parseCognitionUpdateId(notificationId) {
    if (!notificationId.startsWith(COGNITION_NOTIFICATION_PREFIX)) {
      return "";
    }
    return notificationId.slice(COGNITION_NOTIFICATION_PREFIX.length);
  }
  function parseDelightBvid(notificationId) {
    if (!notificationId.startsWith(DELIGHT_NOTIFICATION_PREFIX)) {
      return "";
    }
    return notificationId.slice(DELIGHT_NOTIFICATION_PREFIX.length);
  }
  function buildExtensionUiUrl(tab = "recommend", { delightBvid = "" } = {}) {
    const params = new URLSearchParams({ tab });
    if (delightBvid) {
      params.set("delight", delightBvid);
    }
    const path = `popup/popup.html?${params.toString()}`;
    if (typeof chrome !== "undefined" && chrome.runtime && typeof chrome.runtime.getURL === "function") {
      return chrome.runtime.getURL(path);
    }
    return `chrome-extension://__EXTENSION_ID__/${path}`;
  }
  async function openExtensionUi(chromeApi, {
    windowId,
    tab = "recommend",
    delightBvid = ""
  } = {}) {
    if (typeof windowId === "number" && chromeApi.sidePanel?.open) {
      await chromeApi.sidePanel.open({ windowId });
      return "sidePanel";
    }
    try {
      const globalObj = globalThis;
      const browserApi = globalObj.browser;
      if (browserApi?.sidebarAction?.open) {
        await browserApi.sidebarAction.open();
        return "sidebarPanel";
      }
    } catch {
    }
    await chromeApi.tabs?.create({ url: buildExtensionUiUrl(tab, { delightBvid }) });
    return "tab";
  }

  // src/background/cookie-sync.ts
  var COOKIE_SYNC_ALARM = "openbiliclaw-cookie-sync";
  var COOKIE_SYNC_DEBOUNCE_MS = 2e3;
  var COOKIE_SYNC_REFRESH_MINUTES = 60;
  var COOKIE_SYNC_RETRY_MINUTES = 1;
  var COOKIE_SYNC_VALIDATION_NETWORK_RETRY_MINUTES = 5;
  var COOKIE_SYNC_COOKIE_INVALID_RETRY_MINUTES = 60;
  var REQUIRED_COOKIE_NAMES = ["SESSDATA", "bili_jct", "DedeUserID"];
  var DOUYIN_AUTH_SIGNAL_COOKIE_NAMES = [
    "msToken",
    "sessionid",
    "sessionid_ss",
    "sid_guard",
    "sid_tt",
    "uid_tt",
    "uid_tt_ss",
    "passport_assist_user",
    "passport_mfa_token",
    "passport_csrf_token",
    "odin_tt"
  ];
  var IMPORTANT_DOUYIN_COOKIE_NAMES = [
    "msToken",
    "ttwid",
    "sessionid",
    "sid_guard",
    "sid_tt",
    "uid_tt",
    "passport_csrf_token",
    "passport_auth_status",
    "odin_tt"
  ];
  var debounceTimer = null;
  var cookieSyncStarted = false;
  function getChromeApi() {
    if (typeof chrome === "undefined") {
      return null;
    }
    return chrome;
  }
  function scheduleCookieSyncAlarm(minutes) {
    const chromeApi = getChromeApi();
    if (!chromeApi?.alarms?.create) return;
    chromeApi.alarms.create(COOKIE_SYNC_ALARM, {
      delayInMinutes: minutes,
      periodInMinutes: minutes
    });
  }
  function scheduleHourlyCookieSync() {
    const chromeApi = getChromeApi();
    if (!chromeApi?.alarms?.create) return;
    chromeApi.alarms.create(COOKIE_SYNC_ALARM, {
      periodInMinutes: COOKIE_SYNC_REFRESH_MINUTES
    });
  }
  async function readBilibiliCookieHeader() {
    const chromeApi = getChromeApi();
    if (!chromeApi?.cookies?.getAll) {
      return null;
    }
    const cookies = await chromeApi.cookies.getAll({ domain: "bilibili.com" });
    const have = new Set(cookies.map((c) => c.name));
    for (const required of REQUIRED_COOKIE_NAMES) {
      if (!have.has(required)) {
        return null;
      }
    }
    return cookies.map((c) => `${c.name}=${c.value}`).join("; ");
  }
  async function readDouyinCookieHeader() {
    const chromeApi = getChromeApi();
    if (!chromeApi?.cookies?.getAll) {
      return null;
    }
    const cookies = (await chromeApi.cookies.getAll({ domain: "douyin.com" })).filter(
      (cookie) => cookie.name && cookie.value
    );
    const have = new Set(cookies.map((c) => c.name));
    if (!DOUYIN_AUTH_SIGNAL_COOKIE_NAMES.some((name) => have.has(name))) {
      return null;
    }
    return cookies.map((c) => `${c.name}=${c.value}`).join("; ");
  }
  async function syncBilibiliCookieToBackend(source = "extension") {
    const cookieHeader = await readBilibiliCookieHeader();
    if (!cookieHeader) {
      return false;
    }
    try {
      const response = await fetch(await apiUrl("/bilibili/cookie"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          cookie: cookieHeader,
          source,
          validate_with_bilibili: true
        })
      });
      if (!response.ok) {
        console.warn(`[openbiliclaw] cookie sync HTTP ${response.status}`);
        scheduleCookieSyncAlarm(COOKIE_SYNC_RETRY_MINUTES);
        return false;
      }
      const result = await response.json();
      if (result.ok && result.authenticated) {
        console.log(
          `[openbiliclaw] cookie synced via ${source}` + (result.username ? ` (logged in as ${result.username})` : "")
        );
        scheduleHourlyCookieSync();
        return true;
      }
      const errorCode = String(result.error_code || "").toLowerCase();
      const message = String(result.message || "");
      if (errorCode === "validation_network") {
        console.warn(
          `[openbiliclaw] cookie validation network-failed (${source}): ${message} \u2014 retry in ${COOKIE_SYNC_VALIDATION_NETWORK_RETRY_MINUTES}min`
        );
        scheduleCookieSyncAlarm(COOKIE_SYNC_VALIDATION_NETWORK_RETRY_MINUTES);
      } else if (errorCode === "cookie_invalid") {
        console.warn(
          `[openbiliclaw] cookie invalid / expired (${source}): ${message} \u2014 waiting for next bilibili.com login (or hourly retry)`
        );
        scheduleCookieSyncAlarm(COOKIE_SYNC_COOKIE_INVALID_RETRY_MINUTES);
      } else {
        console.warn(
          `[openbiliclaw] cookie sync rejected (${source}): code=${errorCode || "(unset)"} message=${message} \u2014 retry in 5min`
        );
        scheduleCookieSyncAlarm(COOKIE_SYNC_VALIDATION_NETWORK_RETRY_MINUTES);
      }
      return false;
    } catch (err) {
      console.warn("[openbiliclaw] cookie sync failed:", err);
      scheduleCookieSyncAlarm(COOKIE_SYNC_RETRY_MINUTES);
      return false;
    }
  }
  async function syncDouyinCookieToBackend(source = "extension") {
    const cookieHeader = await readDouyinCookieHeader();
    if (!cookieHeader) {
      return false;
    }
    try {
      const response = await fetch(await apiUrl("/sources/dy/cookie"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          cookie: cookieHeader,
          source
        })
      });
      if (!response.ok) {
        console.warn(`[openbiliclaw] douyin cookie sync HTTP ${response.status}`);
        scheduleCookieSyncAlarm(COOKIE_SYNC_RETRY_MINUTES);
        return false;
      }
      const result = await response.json();
      if (result.ok && result.has_cookie) {
        console.log(`[openbiliclaw] douyin cookie synced via ${source}`);
        scheduleHourlyCookieSync();
        return true;
      }
      const message = String(result.message || "");
      console.warn(`[openbiliclaw] douyin cookie sync rejected (${source}): ${message}`);
      scheduleCookieSyncAlarm(COOKIE_SYNC_VALIDATION_NETWORK_RETRY_MINUTES);
      return false;
    } catch (err) {
      console.warn("[openbiliclaw] douyin cookie sync failed:", err);
      scheduleCookieSyncAlarm(COOKIE_SYNC_RETRY_MINUTES);
      return false;
    }
  }
  function handleCookieSyncRuntimeEvent(event) {
    const eventType = String(event.type ?? "");
    if (eventType === "bilibili_cookie_sync_requested") {
      void syncBilibiliCookieToBackend("runtime-stream-request");
      return true;
    }
    if (eventType === "douyin_cookie_sync_requested") {
      void syncDouyinCookieToBackend("runtime-stream-request");
      return true;
    }
    return false;
  }
  function scheduleCookieSync(source) {
    if (debounceTimer !== null) {
      clearTimeout(debounceTimer);
    }
    debounceTimer = setTimeout(() => {
      debounceTimer = null;
      void syncBilibiliCookieToBackend(source);
      void syncDouyinCookieToBackend(source);
    }, COOKIE_SYNC_DEBOUNCE_MS);
  }
  function startCookieSync() {
    const chromeApi = getChromeApi();
    if (!chromeApi?.cookies?.onChanged) {
      return;
    }
    if (cookieSyncStarted) {
      return;
    }
    cookieSyncStarted = true;
    void syncBilibiliCookieToBackend("startup");
    void syncDouyinCookieToBackend("startup");
    chromeApi.cookies.onChanged.addListener((changeInfo) => {
      const domain = (changeInfo.cookie.domain || "").toLowerCase();
      if (domain.endsWith("bilibili.com")) {
        if (!REQUIRED_COOKIE_NAMES.includes(changeInfo.cookie.name)) {
          return;
        }
        scheduleCookieSync(changeInfo.removed ? "logout" : "cookies-onchange");
        return;
      }
      if (domain.endsWith("douyin.com")) {
        if (!IMPORTANT_DOUYIN_COOKIE_NAMES.includes(changeInfo.cookie.name)) {
          return;
        }
        scheduleCookieSync(changeInfo.removed ? "douyin-logout" : "douyin-cookies-onchange");
      }
    });
    scheduleHourlyCookieSync();
  }
  function handleCookieSyncAlarm(alarmName) {
    if (alarmName !== COOKIE_SYNC_ALARM) {
      return false;
    }
    void syncBilibiliCookieToBackend("hourly-alarm");
    void syncDouyinCookieToBackend("hourly-alarm");
    return true;
  }

  // src/background/service-worker.ts
  var eventBuffer = [];
  var BUFFER_FLUSH_INTERVAL = 3e4;
  var BUFFER_MAX_SIZE = 50;
  var FLUSH_ALARM_NAME = "openbiliclaw-flush-events";
  var HEALTH_PROBE_TIMEOUT_MS = 2e3;
  var WS_RECONNECT_BASE_DELAY = 5e3;
  var WS_RECONNECT_MAX_DELAY = 6e4;
  var wsReconnectDelay = WS_RECONNECT_BASE_DELAY;
  async function acknowledgeNotificationSent(bvid) {
    if (!bvid) return;
    await fetch(await apiUrl("/notifications/sent"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ bvid })
    });
  }
  async function fetchPendingNotification() {
    const response = await fetch(await apiUrl("/notifications/pending"), { method: "GET" });
    if (!response.ok) {
      throw new Error(`pending notifications failed: ${response.status}`);
    }
    const payload = await response.json();
    return payload.item ?? null;
  }
  async function fetchPendingCognitionUpdate() {
    const response = await fetch(await apiUrl("/cognition-updates/pending"), { method: "GET" });
    if (!response.ok) {
      throw new Error(`pending cognition updates failed: ${response.status}`);
    }
    const payload = await response.json();
    return payload.item ?? null;
  }
  async function acknowledgeCognitionUpdateSeen(id) {
    if (!id) return;
    await fetch(await apiUrl("/cognition-updates/seen"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id })
    });
  }
  async function acknowledgeDelightSent(bvid) {
    if (!bvid) return;
    await fetch(await apiUrl("/delight/sent"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ bvid })
    });
  }
  async function checkPendingNotification() {
    try {
      const item = await fetchPendingNotification();
      if (item?.bvid) {
        await acknowledgeNotificationSent(item.bvid);
        return;
      }
      const cognition = await fetchPendingCognitionUpdate();
      if (cognition?.id) {
        await acknowledgeCognitionUpdateSeen(cognition.id);
      }
    } catch (err) {
      console.warn(
        "[OpenBiliClaw] Pending notification ack failed:",
        err instanceof Error ? err.message : String(err)
      );
    }
  }
  var runtimeSocket = null;
  var wsReconnectTimer = null;
  var runtimeConnectInFlight = false;
  function handleRuntimeEvent(event) {
    if (handleCookieSyncRuntimeEvent(event)) return;
    const eventType = String(event.type ?? "");
    if (eventType === "xhs_task_available") {
      pollXhsTaskNow();
      return;
    }
    if (eventType === "dy_task_available") {
      pollDyTaskNow();
      return;
    }
    if (eventType === "yt_task_available") {
      pollYtTaskNow();
      return;
    }
    if (eventType === "extension_reload") {
      if (chrome?.runtime?.reload) {
        console.debug("[OpenBiliClaw] runtime-stream \u2192 chrome.runtime.reload()");
        chrome.runtime.reload();
      }
      return;
    }
    if (eventType === "interest.probe") {
      return;
    }
    if (eventType !== "delight.candidate") return;
    const bvid = String(event.bvid ?? "");
    if (!bvid) return;
    void acknowledgeDelightSent(bvid);
  }
  async function isBackendAlive() {
    try {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), HEALTH_PROBE_TIMEOUT_MS);
      try {
        const resp = await fetch(await apiUrl("/health"), {
          method: "GET",
          signal: ctrl.signal
        });
        return resp.ok;
      } finally {
        clearTimeout(timer);
      }
    } catch {
      return false;
    }
  }
  function setBackendBadge(reachable) {
    try {
      if (reachable) {
        void chrome.action.setBadgeText({ text: "" });
      } else {
        void chrome.action.setBadgeText({ text: "!" });
        void chrome.action.setBadgeBackgroundColor({ color: "#9CA3AF" });
      }
    } catch {
    }
  }
  async function connectRuntimeStream() {
    if (runtimeSocket !== null || runtimeConnectInFlight) return;
    runtimeConnectInFlight = true;
    try {
      if (!await isBackendAlive()) {
        setBackendBadge(false);
        scheduleWsReconnect();
        return;
      }
      try {
        const url = await wsUrl("/runtime-stream?client=background");
        runtimeSocket = new WebSocket(url);
      } catch {
        setBackendBadge(false);
        scheduleWsReconnect();
        return;
      }
      runtimeSocket.onopen = () => {
        wsReconnectDelay = WS_RECONNECT_BASE_DELAY;
        setBackendBadge(true);
      };
      runtimeSocket.onmessage = (msg) => {
        try {
          const payload = JSON.parse(String(msg.data));
          handleRuntimeEvent(payload);
        } catch {
        }
      };
      runtimeSocket.onclose = () => {
        runtimeSocket = null;
        scheduleWsReconnect();
      };
      runtimeSocket.onerror = () => {
        runtimeSocket?.close();
      };
    } finally {
      runtimeConnectInFlight = false;
    }
  }
  function scheduleWsReconnect() {
    if (wsReconnectTimer !== null) return;
    const delay = wsReconnectDelay;
    wsReconnectTimer = setTimeout(() => {
      wsReconnectTimer = null;
      void connectRuntimeStream();
    }, delay);
    wsReconnectDelay = Math.min(wsReconnectDelay * 2, WS_RECONNECT_MAX_DELAY);
  }
  async function flushEvents() {
    if (eventBuffer.length === 0) return;
    const events = [...eventBuffer];
    eventBuffer = [];
    try {
      const response = await fetch(await apiUrl("/events"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ events })
      });
      if (!response.ok) {
        console.warn("[OpenBiliClaw] Backend returned", response.status);
        eventBuffer.unshift(...events);
        return;
      }
      await checkPendingNotification();
    } catch {
      console.warn("[OpenBiliClaw] Backend not available, buffering events");
      eventBuffer.unshift(...events);
    }
  }
  function ensureFlushAlarm() {
    chrome.alarms.create(FLUSH_ALARM_NAME, {
      periodInMinutes: BUFFER_FLUSH_INTERVAL / 6e4
    });
  }
  chrome.runtime.onInstalled.addListener(() => {
    ensureFlushAlarm();
    void connectRuntimeStream();
    startXhsTaskPolling();
    startDyTaskPolling();
    startYtTaskPolling();
    startCookieSync();
  });
  chrome.runtime.onStartup.addListener(() => {
    ensureFlushAlarm();
    void connectRuntimeStream();
    startXhsTaskPolling();
    startDyTaskPolling();
    startYtTaskPolling();
    startCookieSync();
  });
  chrome.action.onClicked.addListener((tab) => {
    void openExtensionUi(chrome, {
      windowId: tab.windowId,
      tab: "recommend"
    });
  });
  async function postXhsObservedUrls(payload) {
    try {
      await fetch(await apiUrl("/sources/xhs/observed-urls"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
    } catch {
    }
  }
  async function postXhsTokens(payload) {
    if (!payload?.pairs || payload.pairs.length === 0) return;
    try {
      await fetch(await apiUrl("/sources/xhs/tokens"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
    } catch {
    }
  }
  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message.action === "XHS_URLS_OBSERVED") {
      void postXhsObservedUrls(message.data);
      return;
    }
    if (message.action === "XHS_TOKENS_OBSERVED") {
      void postXhsTokens(
        message.data
      );
      return;
    }
    if (message.action === "XHS_TASK_RESULT") {
      void handleTaskResult(message.data).then(() => {
        sendResponse({ ok: true });
      }).catch((error) => {
        sendResponse({ ok: false, error: String(error) });
      });
      return true;
    }
    if (message.action === "DY_TASK_RESULT") {
      void handleDyTaskResult(message.data).then(() => {
        sendResponse({ ok: true });
      }).catch((error) => {
        sendResponse({ ok: false, error: String(error) });
      });
      return true;
    }
    if (message.action === "DY_SCOPE_RESULT") {
      void handleDyScopeResult(message.data).then(() => {
        sendResponse({ ok: true });
      }).catch((error) => {
        sendResponse({ ok: false, error: String(error) });
      });
      return true;
    }
    if (message.action === "DY_SEARCH_RESULT") {
      void handleDySearchTaskResult(message.data).then(() => {
        sendResponse({ ok: true });
      }).catch((error) => {
        sendResponse({ ok: false, error: String(error) });
      });
      return true;
    }
    if (message.action === "DY_HOT_RESULT") {
      void handleDyHotTaskResult(message.data).then(() => {
        sendResponse({ ok: true });
      }).catch((error) => {
        sendResponse({ ok: false, error: String(error) });
      });
      return true;
    }
    if (message.action === "DY_FEED_RESULT") {
      void handleDyFeedTaskResult(message.data).then(() => {
        sendResponse({ ok: true });
      }).catch((error) => {
        sendResponse({ ok: false, error: String(error) });
      });
      return true;
    }
    if (message.action === "YT_SCOPE_RESULT") {
      void handleYtScopeResult(message.data).then(() => {
        sendResponse({ ok: true });
      }).catch((error) => {
        sendResponse({ ok: false, error: String(error) });
      });
      return true;
    }
    if (message.action !== "BEHAVIOR_EVENT") return;
    eventBuffer = enqueueBufferedEvent(eventBuffer, message.data, BUFFER_MAX_SIZE);
    if (eventBuffer.length >= BUFFER_MAX_SIZE || shouldFlushImmediately(message.data)) {
      void flushEvents();
    }
  });
  chrome.alarms.onAlarm.addListener((alarm) => {
    handleXhsTaskAlarm(alarm.name);
    handleDyTaskAlarm(alarm.name);
    handleYtTaskAlarm(alarm.name);
    if (handleCookieSyncAlarm(alarm.name)) {
      return;
    }
    if (alarm.name === FLUSH_ALARM_NAME) {
      if (eventBuffer.length > 0) {
        void flushEvents();
        return;
      }
      void checkPendingNotification();
    }
  });
  chrome.notifications.onClicked.addListener((notificationId) => {
    if (notificationId.startsWith("openbiliclaw-probe:")) {
      void openExtensionUi(chrome, { tab: "profile" });
      void chrome.notifications.clear(notificationId);
      return;
    }
    const bvid = parseNotificationBvid(notificationId);
    if (bvid) {
      void openExtensionUi(chrome, { tab: "recommend" });
      void chrome.notifications.clear(notificationId);
      return;
    }
    const delightBvid = parseDelightBvid(notificationId);
    if (delightBvid) {
      void openExtensionUi(chrome, { tab: "recommend", delightBvid });
      void chrome.notifications.clear(notificationId);
      return;
    }
    const cognitionId = parseCognitionUpdateId(notificationId);
    if (!cognitionId) {
      return;
    }
    void openExtensionUi(chrome, { tab: "profile" });
    void chrome.notifications.clear(notificationId);
  });
  ensureFlushAlarm();
  void connectRuntimeStream();
  startCookieSync();
  onBackendEndpointChange(() => {
    try {
      runtimeSocket?.close();
    } catch {
    }
    runtimeSocket = null;
    wsReconnectDelay = WS_RECONNECT_BASE_DELAY;
    if (wsReconnectTimer !== null) {
      clearTimeout(wsReconnectTimer);
      wsReconnectTimer = null;
    }
    void connectRuntimeStream();
  });
  console.log("[OpenBiliClaw] Service worker initialized");
})();
//# sourceMappingURL=service-worker.js.map
