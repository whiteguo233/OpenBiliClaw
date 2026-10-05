(function installSavedSyncCore(global) {
  "use strict";

  const TERMINAL_STATUSES = new Set([
    "synced", "already_synced", "login_required", "unsupported",
    "rate_limited", "extension_required", "failed",
  ]);
  const PLATFORM_ALIASES = Object.freeze({
    bili: "bilibili",
    xhs: "xiaohongshu",
    dy: "douyin",
    yt: "youtube",
    x: "twitter",
    zh: "zhihu",
    rd: "reddit",
    wb: "weibo",
    ig: "instagram",
    gh: "github",
  });

  function text(value) {
    return String(value || "").trim();
  }

  function createSavedSubmissionFence() {
    const keys = new Set();
    return {
      has(itemKey) { return keys.has(text(itemKey)); },
      claim(itemKeys) {
        const candidates = [...new Set((Array.isArray(itemKeys) ? itemKeys : []).map(text))]
          .filter(Boolean);
        if (!candidates.length || candidates.some((key) => keys.has(key))) return false;
        for (const key of candidates) keys.add(key);
        return true;
      },
      release(itemKeys) {
        for (const itemKey of Array.isArray(itemKeys) ? itemKeys : []) keys.delete(text(itemKey));
      },
    };
  }

  function inferPlatform(item) {
    const explicit = text(item?.source_platform || item?.platform).toLowerCase();
    if (explicit) return PLATFORM_ALIASES[explicit] || explicit;
    try {
      const host = new URL(text(item?.content_url || item?.url)).hostname.toLowerCase();
      if (host === "youtu.be" || host.endsWith(".youtube.com")) return "youtube";
      if (host === "x.com" || host.endsWith(".x.com") || host.endsWith(".twitter.com")) return "twitter";
      if (host.endsWith(".zhihu.com")) return "zhihu";
      if (["weibo.com", "weibo.cn", "sinaimg.cn", "sinaimg.com"].some(
        (domain) => host === domain || host.endsWith(`.${domain}`),
      )) return "weibo";
      if (host === "instagram.com" || host.endsWith(".instagram.com") || host === "instagr.am" || host.endsWith(".instagr.am")) return "instagram";
      if (host.endsWith(".bilibili.com") || host === "b23.tv") return "bilibili";
      if (["bgm.tv", "bangumi.tv"].some((domain) => host === domain || host.endsWith(`.${domain}`))) return "bangumi";
      if (host === "github.com" || host === "www.github.com") return "github";
      return "web";
    } catch {
      return "bilibili";
    }
  }

  function normalizeSavedItem(item = {}) {
    const sourcePlatform = inferPlatform(item);
    const legacyId = text(item.bvid);
    const contentId = text(item.content_id || (legacyId && !legacyId.includes(":") ? legacyId : ""));
    const contentUrl = text(item.content_url || item.url);
    const explicitType = text(item.content_type);
    return {
      ...item,
      item_key: text(item.item_key) || (contentId ? `${sourcePlatform}:${contentId}` : ""),
      source_platform: sourcePlatform,
      content_id: contentId,
      content_url: contentUrl,
      content_type: explicitType || (sourcePlatform === "bilibili" && contentId ? "video" : ""),
    };
  }

  function assertListKind(listKind) {
    if (listKind !== "favorite" && listKind !== "watch_later") {
      throw new TypeError(`Unknown saved list: ${listKind}`);
    }
    return `/saved/${listKind}`;
  }

  function createStrictSavedApi(requestJsonStrict) {
    if (typeof requestJsonStrict !== "function") throw new TypeError("requestJsonStrict is required");
    const readTimeout = 10_000;
    const writeTimeout = 12_000;
    const json = (body, timeoutMs = writeTimeout) => ({
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      timeoutMs,
    });
    return {
      save(listKind, item) {
        const normalized = normalizeSavedItem(item);
        return requestJsonStrict(assertListKind(listKind), json({
          source_platform: normalized.source_platform,
          content_id: normalized.content_id,
          content_url: normalized.content_url,
          content_type: normalized.content_type,
          title: text(normalized.title),
          author_name: text(normalized.author_name || normalized.up_name),
          cover_url: text(normalized.cover_url),
          note: text(normalized.note),
        }));
      },
      remove(listKind, itemKey) {
        return requestJsonStrict(`${assertListKind(listKind)}/remove`, json({ item_key: text(itemKey) }));
      },
      status(listKind, itemKey) {
        return requestJsonStrict(
          `${assertListKind(listKind)}/status?item_key=${encodeURIComponent(text(itemKey))}`,
          { timeoutMs: readTimeout },
        );
      },
      list(listKind, limit = 100, offset = 0) {
        return requestJsonStrict(
          `${assertListKind(listKind)}?limit=${encodeURIComponent(limit)}&offset=${encodeURIComponent(offset)}`,
          { timeoutMs: readTimeout },
        );
      },
      sync(listKind, itemKeys) {
        return requestJsonStrict(`${assertListKind(listKind)}/sync`, json({ item_keys: itemKeys }));
      },
      pollTask(taskId) {
        return requestJsonStrict(`/saved-sync/tasks/${encodeURIComponent(text(taskId))}`, {
          timeoutMs: readTimeout,
        });
      },
    };
  }

  function taskIsTerminal(task) {
    const rows = Array.isArray(task?.items) ? task.items : [];
    return rows.every((item) => TERMINAL_STATUSES.has(item?.status));
  }

  function getSavedSyncPresentation(item = {}) {
    const rawStatus = text(item.sync_status);
    const status = rawStatus || "not_started";
    const errorCode = text(item.error_code);
    const resolvedTarget = text(item.resolved_target);
    const errorMessage = text(item.error_message);
    const base = {
      not_started: ["待同步", "neutral", false],
      pending: ["待同步", "info", false],
      syncing: ["同步中", "info", false],
      synced: ["已同步", "success", false],
      already_synced: ["已同步", "success", false],
      login_required: ["需要登录", "warning", true],
      unsupported: ["仅本地保存", "neutral", false],
      rate_limited: ["同步失败", "error", true],
      extension_required: ["需要连接插件", "warning", true],
      failed: ["同步失败", "error", true],
    }[status] || ["同步失败", "error", true];
    let [label, tone, retryable] = base;
    const busy = status === "syncing" || (status === "pending" && Boolean(text(item.sync_task_id)));
    const localOnly = status === "unsupported"
      && (errorCode === "unsupported_content_type" || errorCode === "local_only_source");
    if (status === "unsupported" && errorCode === "unsupported_adapter_missing") {
      label = "待升级重试";
      tone = "warning";
      retryable = true;
    } else if (status === "unsupported" && !localOnly) {
      label = "同步暂不可用";
      tone = "warning";
      retryable = true;
    }
    const actionable = !busy && !["synced", "already_synced"].includes(status) && !localOnly;
    let detail;
    if (localOnly) {
      detail = errorCode === "local_only_source"
        ? "此来源仅支持本地收藏，不会向平台创建同步任务。"
        : "此内容类型暂不支持平台同步，仅保存在本地。";
    }
    else if (status === "unsupported" && errorCode === "unsupported_adapter_missing") {
      detail = "同步能力可能正在滚动升级，请更新后端与插件后重试。";
    } else if (status === "unsupported") detail = errorMessage || "当前同步能力暂不可用，请更新后重试。";
    else if (["synced", "already_synced"].includes(status)) detail = resolvedTarget || "平台已确认同步完成。";
    else if (busy) detail = resolvedTarget || "平台同步任务已提交，请稍候。";
    else if (status === "pending") detail = resolvedTarget || "已保存在本地，可手动同步到平台。";
    else {
      const fallback = {
        login_required: "请登录对应平台后重试。",
        rate_limited: "平台请求过于频繁，请稍后重试。",
        extension_required: "请连接已安装 OpenBiliClaw 插件的登录态浏览器后重试。",
        failed: "平台同步失败，请重试；若持续失败请检查连接或登录状态。",
      }[status];
      detail = errorMessage || resolvedTarget || fallback || "平台目标将在同步时确认";
    }
    return {
      status, label, tone, retryable, actionable, busy, localOnly, detail,
      actionLabel: busy ? "同步中…" : (retryable ? "重试同步" : "同步"),
    };
  }

  function isSavedSyncEligibleStatus(status, errorCode = "", syncTaskId = "") {
    return getSavedSyncPresentation({
      sync_status: status,
      error_code: errorCode,
      sync_task_id: syncTaskId,
    }).actionable;
  }

  function updateSavedBatchButtonState(button, pendingCount) {
    const disabled = pendingCount <= 0;
    button.hidden = disabled;
    button.disabled = disabled;
    button.setAttribute("aria-disabled", String(disabled));
    button.removeAttribute("aria-busy");
  }

  function createRetainedSavedListState() {
    let value = { items: [], total: 0, loaded: false, error: "" };
    return {
      commit(payload = {}) {
        value = {
          items: Array.isArray(payload.items) ? payload.items : [],
          total: Number(payload.total) || 0,
          loaded: true,
          error: "",
        };
      },
      fail(reason) {
        value = { ...value, error: text(reason?.message || reason || "保存列表加载失败。") };
      },
      snapshot() { return { ...value, items: [...value.items] }; },
    };
  }

  function createSavedMutationRegistry() {
    const saved = new Set();
    const busy = new Set();
    const versions = new Map();
    const composite = (listKind, itemKey) => `${text(listKind)}:${text(itemKey)}`;
    const bump = (key) => {
      const next = (versions.get(key) || 0) + 1;
      versions.set(key, next);
      return next;
    };
    return {
      isBusy(listKind, itemKey) { return busy.has(composite(listKind, itemKey)); },
      isSaved(listKind, itemKey) { return saved.has(composite(listKind, itemKey)); },
      setSaved(listKind, itemKey, value) {
        const key = composite(listKind, itemKey);
        bump(key);
        if (value) saved.add(key); else saved.delete(key);
      },
      async hydrate(listKind, itemKey, load) {
        const key = composite(listKind, itemKey);
        const version = versions.get(key) || 0;
        try {
          const result = await load(itemKey);
          if (busy.has(key) || (versions.get(key) || 0) !== version) return result;
          if (result?.saved === true) saved.add(key);
          if (result?.saved === false) saved.delete(key);
          return result;
        } catch {
          return null;
        }
      },
      async toggle(listKind, itemKey, operations) {
        const key = composite(listKind, itemKey);
        if (busy.has(key)) return false;
        const wasSaved = saved.has(key);
        busy.add(key);
        bump(key);
        if (wasSaved) saved.delete(key); else saved.add(key);
        try {
          const result = await (wasSaved ? operations.remove(itemKey) : operations.add(itemKey));
          const finalSaved = typeof result?.saved === "boolean" ? result.saved : !wasSaved;
          bump(key);
          if (finalSaved) saved.add(key); else saved.delete(key);
          return true;
        } catch (error) {
          bump(key);
          if (wasSaved) saved.add(key); else saved.delete(key);
          throw error;
        } finally {
          busy.delete(key);
        }
      },
    };
  }

  function captureSavedFocus(root, activeElement = global.document?.activeElement) {
    const listAction = text(activeElement?.dataset?.savedListAction);
    if (root && listAction) return { kind: "list", action: listAction };
    const card = activeElement?.closest?.("[data-item-key]");
    const itemKey = text(card?.dataset?.itemKey);
    const action = text(activeElement?.dataset?.savedAction);
    const cards = Array.from(root?.querySelectorAll?.("[data-item-key]") || []);
    const index = cards.indexOf(card);
    return root && itemKey && action ? { itemKey, action, index: Math.max(0, index) } : null;
  }

  function restoreSavedFocus(root, token) {
    if (!root || !token?.action) return false;
    const cards = Array.from(root.querySelectorAll?.("[data-item-key]") || []);
    const focusAction = (card) => {
      const actions = Array.from(card?.querySelectorAll?.("[data-saved-action]") || []);
      const action = actions.find((candidate) => candidate.dataset?.savedAction === token.action)
        || actions[0];
      action?.focus?.();
      return Boolean(action);
    };
    if (token.kind === "list" || token.itemKey === "__list__") {
      const sameListAction = root.querySelector?.(
        `[data-saved-list-action="${token.action}"]`,
      );
      if (sameListAction) { sameListAction.focus?.(); return true; }
      if (focusAction(cards[0])) return true;
      const heading = root.querySelector?.("[data-saved-heading]");
      if (heading) { heading.focus?.(); return true; }
      return false;
    }
    if (!token.itemKey) return false;
    let sameIndex = -1;
    for (let cardIndex = 0; cardIndex < cards.length; cardIndex += 1) {
      const card = cards[cardIndex];
      if (card.dataset?.itemKey !== token.itemKey) continue;
      sameIndex = cardIndex;
      const exact = Array.from(card.querySelectorAll?.("[data-saved-action]") || [])
        .find((action) => action.dataset?.savedAction === token.action);
      if (exact) { exact.focus?.(); return true; }
    }
    const index = sameIndex >= 0
      ? sameIndex + 1
      : Math.max(0, Math.min(Number(token.index) || 0, cards.length));
    const previousIndex = sameIndex >= 0 ? sameIndex - 1 : index - 1;
    if (focusAction(cards[index]) || focusAction(cards[previousIndex])) return true;
    const listAction = root.querySelector?.(
      '[data-saved-list-action="sync-all"], [data-saved-list-action="retry"]',
    );
    if (listAction) { listAction.focus?.(); return true; }
    const heading = root.querySelector?.("[data-saved-heading]");
    if (heading) { heading.focus?.(); return true; }
    return false;
  }

  function createDurableTaskTracker(options = {}) {
    const poll = options.poll;
    const now = options.now || Date.now;
    const isVisible = options.isVisible || (() => true);
    const schedule = options.schedule || ((run, delay) => global.setTimeout(run, delay));
    const cancel = options.cancel || ((handle) => global.clearTimeout(handle));
    const foregroundHorizonMs = Number(options.foregroundHorizonMs ?? 20_000);
    const visibleDelayMs = Number(options.visibleDelayMs ?? 750);
    const hiddenDelayMs = Number(options.hiddenDelayMs ?? 5_000);
    const active = new Map();

    function queue(entry) {
      if (!active.has(entry.taskId)) return;
      const delay = isVisible() ? visibleDelayMs : hiddenDelayMs;
      entry.timer = schedule(() => tick(entry), delay);
    }

    async function tick(entry) {
      if (!active.has(entry.taskId) || entry.polling) return;
      entry.polling = true;
      try {
        const next = await poll(entry.taskId);
        if (!active.has(entry.taskId)) return;
        if (next && typeof next === "object") entry.task = next;
        if (taskIsTerminal(entry.task)) {
          active.delete(entry.taskId);
          entry.callbacks.onTerminal?.(entry.task);
          return;
        }
        entry.callbacks.onProgress?.(entry.task);
        if (!entry.backgroundAnnounced && now() - entry.startedAt >= foregroundHorizonMs) {
          entry.backgroundAnnounced = true;
          entry.callbacks.onBackground?.(entry.task);
        }
      } catch (error) {
        if (!active.has(entry.taskId)) return;
        entry.callbacks.onPollError?.(error, entry.task);
      } finally {
        entry.polling = false;
      }
      queue(entry);
    }

    function track(initial, callbacks = {}) {
      const taskId = text(initial?.task_id);
      if (!taskId) return null;
      const existing = active.get(taskId);
      if (existing) {
        existing.callbacks = { ...existing.callbacks, ...callbacks };
        if (initial && typeof initial === "object") existing.task = initial;
        return taskId;
      }
      const entry = {
        taskId,
        task: initial,
        callbacks,
        startedAt: now(),
        backgroundAnnounced: false,
        polling: false,
        timer: null,
      };
      if (taskIsTerminal(initial)) {
        callbacks.onTerminal?.(initial);
        return taskId;
      }
      active.set(taskId, entry);
      callbacks.onProgress?.(initial);
      queue(entry);
      return taskId;
    }

    function resume(taskId) {
      const entry = active.get(text(taskId));
      if (!entry || entry.polling) return false;
      if (entry.timer != null) cancel(entry.timer);
      entry.timer = schedule(() => tick(entry), 0);
      return true;
    }

    const api = {
      has(taskId) { return active.has(text(taskId)); },
      resume,
      stop(taskId) {
        const entry = active.get(text(taskId));
        if (!entry) return false;
        if (entry.timer != null) cancel(entry.timer);
        active.delete(entry.taskId);
        return true;
      },
      track,
      resumeAll() {
        let resumed = 0;
        for (const taskId of active.keys()) if (resume(taskId)) resumed += 1;
        return resumed;
      },
      dispose() {
        for (const entry of active.values()) if (entry.timer != null) cancel(entry.timer);
        active.clear();
      },
    };
    return api;
  }

  function createSavedTaskCoordinator(options = {}) {
    const tracker = options.tracker;
    const fetchTask = options.fetchTask;
    const ownersByTask = new Map();
    const taskByItem = new Map();
    const recovering = new Map();
    let disposed = false;
    const release = (taskId) => {
      for (const itemKey of ownersByTask.get(taskId) || []) {
        if (taskByItem.get(itemKey) === taskId) taskByItem.delete(itemKey);
      }
      ownersByTask.delete(taskId);
    };
    const claim = (taskId, itemKeys) => {
      const keys = new Set(ownersByTask.get(taskId) || []);
      for (const key of (itemKeys || []).map(text).filter(Boolean)) keys.add(key);
      ownersByTask.set(taskId, keys);
      for (const key of keys) taskByItem.set(key, taskId);
    };
    const track = (task, itemKeys, callbacks = {}) => {
      const taskId = text(task?.task_id);
      if (!taskId || disposed) return null;
      claim(taskId, itemKeys);
      return tracker.track(task, {
        ...callbacks,
        onTerminal(terminalTask) {
          release(taskId);
          callbacks.onTerminal?.(terminalTask);
          options.onTerminal?.(terminalTask);
        },
        onProgress(progressTask) {
          callbacks.onProgress?.(progressTask);
          options.onProgress?.(progressTask);
        },
        onBackground(progressTask) {
          callbacks.onBackground?.(progressTask);
          options.onBackground?.(progressTask);
        },
        onPollError(error, progressTask) {
          callbacks.onPollError?.(error, progressTask);
          options.onPollError?.(error, progressTask);
        },
      });
    };
    return {
      owns(itemKey) { return taskByItem.has(text(itemKey)); },
      taskFor(itemKey) { return taskByItem.get(text(itemKey)) || ""; },
      track,
      async recover(rows, callbacks = {}) {
        if (disposed) return;
        const grouped = new Map();
        for (const row of Array.isArray(rows) ? rows : []) {
          if (!["pending", "syncing"].includes(row?.sync_status)) continue;
          const taskId = text(row?.sync_task_id);
          const itemKey = text(row?.item_key);
          if (!taskId || !itemKey) continue;
          if (!grouped.has(taskId)) grouped.set(taskId, []);
          grouped.get(taskId).push(itemKey);
        }
        await Promise.all(Array.from(grouped, async ([taskId, itemKeys]) => {
          claim(taskId, itemKeys);
          if (tracker.has(taskId)) return;
          if (recovering.has(taskId)) return recovering.get(taskId);
          const recovery = Promise.resolve()
            .then(() => fetchTask(taskId))
            .then((task) => track(task, itemKeys, callbacks))
            .catch((error) => {
              if (disposed) return;
              track({
                task_id: taskId,
                items: itemKeys.map((item_key) => ({ item_key, status: "syncing" })),
              }, itemKeys, callbacks);
              callbacks.onPollError?.(error);
            })
            .finally(() => recovering.delete(taskId));
          recovering.set(taskId, recovery);
          return recovery;
        }));
      },
      resumeAll() { return tracker.resumeAll?.() || 0; },
      dispose() {
        disposed = true;
        recovering.clear();
        ownersByTask.clear();
        taskByItem.clear();
        tracker.dispose?.();
      },
    };
  }

  const api = {
    captureSavedFocus,
    createDurableTaskTracker,
    createSavedSubmissionFence,
    createSavedTaskCoordinator,
    createRetainedSavedListState,
    createSavedMutationRegistry,
    createStrictSavedApi,
    getSavedSyncPresentation,
    isSavedSyncEligibleStatus,
    normalizeSavedItem,
    restoreSavedFocus,
    taskIsTerminal,
    updateSavedBatchButtonState,
  };
  global.OpenBiliClawSavedSync = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
