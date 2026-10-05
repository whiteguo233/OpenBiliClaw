export interface TabUpdatedListener {
  (tabId: number, changeInfo: { status?: string; url?: string }): void;
}

export interface ChromeMockTab {
  id?: number;
  url?: string;
  status?: string;
}

export interface ChromeMockState {
  createdTabs: Array<{ active?: boolean; muted?: boolean; url: string }>;
  updatedTabs: Array<{ active?: boolean; muted?: boolean; tabId: number; url?: string }>;
  reloadedTabs: number[];
  sentMessages: Array<{ message: unknown; tabId: number }>;
  removedTabs: number[];
  executedScripts: Array<{ files?: string[]; tabId?: number; world?: string }>;
  fetchCalls: Array<{ body?: unknown; method?: string; url: string }>;
  queryResult: ChromeMockTab[];
  sessionStorage: Record<string, unknown>;
  runtimeSessionStorage: Record<string, unknown>;
  sessionGetImpl: (key: string) => Promise<Record<string, unknown>>;
  sessionSetImpl: (items: Record<string, unknown>) => Promise<void>;
  sessionRemoveImpl: (key: string) => Promise<void>;
  tabById: Map<number, ChromeMockTab>;
  nextCreatedTabStatus: string;
  createImpl: (opts: { active?: boolean; muted?: boolean; url: string }) => Promise<ChromeMockTab>;
  getImpl: (tabId: number) => Promise<ChromeMockTab>;
  sendMessageImpl: (tabId: number, message: unknown) => Promise<unknown>;
  removeImpl: (tabId: number) => Promise<void>;
  runtimeAddListenerImpl: (listener: (message: unknown, sender: { tab?: ChromeMockTab; url?: string }) => void) => void;
  runtimeRemoveListenerImpl: (listener: (message: unknown, sender: { tab?: ChromeMockTab; url?: string }) => void) => void;
  tabUpdatedAddListenerImpl: (listener: TabUpdatedListener) => void;
  tabUpdatedRemoveListenerImpl: (listener: TabUpdatedListener) => void;
  fetchImpl: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
  emitTabUpdated: (tabId: number, changeInfo: { status?: string; url?: string }) => void;
  emitRuntimeMessage: (message: unknown, sender?: { tab?: ChromeMockTab; url?: string }) => void;
  tabUpdatedListenerCount: () => number;
  runtimeListenerCount: () => number;
  restore: () => void;
}

export function installChromeMock(): ChromeMockState {
  const originalChrome = (globalThis as { chrome?: unknown }).chrome;
  const originalFetch = globalThis.fetch;
  const mutexGlobals = globalThis as typeof globalThis & {
    __OBC_DISPATCHER_MUTEX_HOLDER__?: string;
    __OBC_DISPATCHER_MUTEX_HELD_SINCE__?: number;
  };
  const hadMutexHolder = Object.hasOwn(mutexGlobals, "__OBC_DISPATCHER_MUTEX_HOLDER__");
  const hadMutexHeldSince = Object.hasOwn(mutexGlobals, "__OBC_DISPATCHER_MUTEX_HELD_SINCE__");
  const originalMutexHolder = mutexGlobals.__OBC_DISPATCHER_MUTEX_HOLDER__;
  const originalMutexHeldSince = mutexGlobals.__OBC_DISPATCHER_MUTEX_HELD_SINCE__;
  const listeners: TabUpdatedListener[] = [];
  const state: ChromeMockState = {
    createdTabs: [],
    updatedTabs: [],
    reloadedTabs: [],
    sentMessages: [],
    removedTabs: [],
    executedScripts: [],
    fetchCalls: [],
    queryResult: [],
    sessionStorage: {},
    runtimeSessionStorage: {},
    sessionGetImpl: async (key) => Object.hasOwn(state.sessionStorage, key)
      ? { [key]: state.sessionStorage[key] }
      : {},
    sessionSetImpl: async (items) => { Object.assign(state.sessionStorage, items); },
    sessionRemoveImpl: async (key) => { delete state.sessionStorage[key]; },
    tabById: new Map(),
    nextCreatedTabStatus: "complete",
    createImpl: async (opts) => {
      state.createdTabs.push(opts);
      const tab = { id: nextTabId++, status: state.nextCreatedTabStatus, url: opts.url };
      state.tabById.set(tab.id, tab);
      return tab;
    },
    getImpl: async (tabId) =>
      state.tabById.get(tabId) ?? { id: tabId, status: "complete" },
    sendMessageImpl: async () => ({ status: "ok", actions: [] }),
    removeImpl: async (tabId) => {
      state.removedTabs.push(tabId);
      state.tabById.delete(tabId);
    },
    runtimeAddListenerImpl: (listener) => runtimeListeners.push(listener),
    runtimeRemoveListenerImpl: (listener) => {
      const index = runtimeListeners.indexOf(listener);
      if (index >= 0) runtimeListeners.splice(index, 1);
    },
    tabUpdatedAddListenerImpl: (listener) => listeners.push(listener),
    tabUpdatedRemoveListenerImpl: (listener) => {
      const index = listeners.indexOf(listener);
      if (index >= 0) listeners.splice(index, 1);
    },
    fetchImpl: async (input, init) => {
      state.fetchCalls.push({
        url: String(input),
        method: init?.method,
        body: init?.body ? JSON.parse(String(init.body)) : undefined,
      });
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    },
    emitTabUpdated(tabId, changeInfo) {
      const current = state.tabById.get(tabId) ?? { id: tabId };
      state.tabById.set(tabId, { ...current, ...changeInfo });
      for (const listener of [...listeners]) {
        listener(tabId, changeInfo);
      }
    },
    emitRuntimeMessage(message, sender = {}) {
      for (const listener of [...runtimeListeners]) {
        listener(message, sender);
      }
    },
    tabUpdatedListenerCount: () => listeners.length,
    runtimeListenerCount: () => runtimeListeners.length,
    restore() {
      (globalThis as { chrome?: unknown }).chrome = originalChrome;
      globalThis.fetch = originalFetch;
      if (hadMutexHolder) mutexGlobals.__OBC_DISPATCHER_MUTEX_HOLDER__ = originalMutexHolder;
      else delete mutexGlobals.__OBC_DISPATCHER_MUTEX_HOLDER__;
      if (hadMutexHeldSince) {
        mutexGlobals.__OBC_DISPATCHER_MUTEX_HELD_SINCE__ = originalMutexHeldSince;
      } else {
        delete mutexGlobals.__OBC_DISPATCHER_MUTEX_HELD_SINCE__;
      }
    },
  };
  const runtimeListeners: Array<(
    message: unknown,
    sender: { tab?: ChromeMockTab; url?: string },
  ) => void> = [];

  let nextTabId = 42;

  const chromeMock = {
    storage: {
      local: {
        get(key: string, callback?: (items: Record<string, unknown>) => void) {
          const promise = state.sessionGetImpl(key);
          if (callback) void promise.then(callback);
          return promise;
        },
        async set(items: Record<string, unknown>) {
          await state.sessionSetImpl(items);
        },
        async remove(key: string) {
          await state.sessionRemoveImpl(key);
        },
      },
      session: {
        async get(key: string) {
          if (key === "openbiliclaw_linuxdo_runtime_session") {
            return Object.hasOwn(state.runtimeSessionStorage, key)
              ? { [key]: state.runtimeSessionStorage[key] }
              : {};
          }
          return state.sessionGetImpl(key);
        },
        async set(items: Record<string, unknown>) {
          const runtimeSession = items.openbiliclaw_linuxdo_runtime_session;
          if (runtimeSession !== undefined) {
            state.runtimeSessionStorage.openbiliclaw_linuxdo_runtime_session = runtimeSession;
          }
          const other = Object.fromEntries(
            Object.entries(items).filter(([key]) => key !== "openbiliclaw_linuxdo_runtime_session"),
          );
          if (Object.keys(other).length > 0) await state.sessionSetImpl(other);
        },
        async remove(key: string) {
          if (key === "openbiliclaw_linuxdo_runtime_session") {
            delete state.runtimeSessionStorage[key];
          } else {
            await state.sessionRemoveImpl(key);
          }
        },
      },
      onChanged: {
        addListener() {
          // Tests do not need storage change delivery.
        },
      },
    },
    tabs: {
      async create(opts: { active?: boolean; muted?: boolean; url: string }) {
        return state.createImpl(opts);
      },
      async query() {
        return state.queryResult;
      },
      async get(tabId: number) {
        return state.getImpl(tabId);
      },
      async update(tabId: number, opts: { active?: boolean; muted?: boolean; url?: string }) {
        state.updatedTabs.push({ tabId, ...opts });
        const current = state.tabById.get(tabId) ?? { id: tabId };
        const updated = {
          ...current,
          ...opts,
          status: current.status ?? "complete",
        };
        state.tabById.set(tabId, updated);
        return updated;
      },
      async reload(tabId: number) {
        state.reloadedTabs.push(tabId);
        const current = state.tabById.get(tabId) ?? { id: tabId };
        state.tabById.set(tabId, { ...current, status: "loading" });
      },
      async sendMessage(tabId: number, message: unknown) {
        state.sentMessages.push({ tabId, message });
        return state.sendMessageImpl(tabId, message);
      },
      async remove(tabId: number) {
        return state.removeImpl(tabId);
      },
      onUpdated: {
        addListener(listener: TabUpdatedListener) {
          state.tabUpdatedAddListenerImpl(listener);
        },
        removeListener(listener: TabUpdatedListener) {
          state.tabUpdatedRemoveListenerImpl(listener);
        },
      },
    },
    runtime: {
      onMessage: {
        addListener(listener: (message: unknown, sender: { tab?: ChromeMockTab; url?: string }) => void) {
          state.runtimeAddListenerImpl(listener);
        },
        removeListener(listener: (message: unknown, sender: { tab?: ChromeMockTab; url?: string }) => void) {
          state.runtimeRemoveListenerImpl(listener);
        },
      },
      async sendMessage(message: unknown) {
        for (const listener of [...runtimeListeners]) listener(message, {});
      },
    },
    scripting: {
      async executeScript(opts: {
        files?: string[];
        target?: { tabId?: number };
        world?: string;
      }) {
        state.executedScripts.push({
          files: opts.files,
          tabId: opts.target?.tabId,
          world: opts.world,
        });
        return [{}];
      },
    },
  };

  (globalThis as { chrome?: unknown }).chrome = chromeMock;
  globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) =>
    state.fetchImpl(input, init)) as typeof fetch;

  return state;
}
