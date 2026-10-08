import { ASSET_PREFIX } from "../shared/asset-prefix.ts";

/** Keep optional Instagram injection out of the install-time permission surface. */
const ORIGIN = "https://*.instagram.com/*";
const SCRIPTS: chrome.scripting.RegisteredContentScript[] = [
  {
    id: "openbiliclaw-instagram-isolated",
    matches: [ORIGIN],
    js: [`${ASSET_PREFIX}content/instagram.js`],
    runAt: "document_start",
    persistAcrossSessions: true,
  },
  {
    id: "openbiliclaw-instagram-main",
    matches: [ORIGIN],
    js: [`${ASSET_PREFIX}main/instagram-response-tap.js`],
    runAt: "document_start",
    world: "MAIN" as chrome.scripting.ExecutionWorld,
    persistAcrossSessions: true,
  },
];

export type InstagramScriptApi = Pick<typeof chrome, "permissions" | "scripting">;

/** Serialize grant/revoke/startup reconciliation, including grants during startup. */
export function createInstagramScriptSync(api: InstagramScriptApi): () => Promise<void> {
  let pending = Promise.resolve();
  return () => {
    pending = pending.catch(() => {}).then(async () => {
      // Safari builds do not ship Instagram; unsupported scripting APIs are inert.
      if (!api.scripting?.getRegisteredContentScripts) return;
      const granted = await api.permissions.contains({ origins: [ORIGIN] });
      const registered = await api.scripting.getRegisteredContentScripts({
        ids: SCRIPTS.map((script) => script.id),
      });
      if (!granted) {
        if (registered.length) {
          await api.scripting.unregisterContentScripts({ ids: registered.map((s) => s.id) });
        }
        return;
      }
      // Register the isolated receiver before the MAIN-world producer. Existing
      // registrations are updated on extension upgrades without duplicate IDs.
      for (const script of SCRIPTS) {
        if (registered.some((s) => s.id === script.id)) {
          await api.scripting.updateContentScripts([script]);
        } else {
          await api.scripting.registerContentScripts([script]);
        }
      }
    });
    return pending;
  };
}

let sync: (() => Promise<void>) | undefined;
export function syncInstagramContentScripts(): Promise<void> {
  sync ??= createInstagramScriptSync(chrome);
  return sync();
}

/** Install listeners synchronously so MV3 permission events can wake the worker. */
export function startInstagramContentScripts(): void {
  const reconcile = () => {
    void syncInstagramContentScripts().catch((error: unknown) => {
      console.warn("[openbiliclaw] Instagram script registration failed", error);
    });
  };
  chrome.permissions?.onAdded?.addListener(reconcile);
  chrome.permissions?.onRemoved?.addListener(reconcile);
  reconcile();
}
