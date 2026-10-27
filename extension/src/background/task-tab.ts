/**
 * Shared helpers for tabs the extension opens automatically (issue #163).
 *
 * Background task tabs (Douyin, Xiaohongshu, Bilibili, Zhihu, Reddit, ...)
 * are created without any user gesture. Platforms that autoplay media in
 * such tabs would otherwise startle a user who left the computer on, so
 * every task tab starts as a blank page and is muted before navigation.
 *
 * Chrome does not accept ``muted`` in ``tabs.create`` (unlike Firefox 100+
 * and Safari 14+), so create an inert ``about:blank`` tab, await
 * ``tabs.update(tabId, { muted: true })``, then load the target URL.
 * Loading the website first leaves a race with autoplay. A mute failure
 * closes the blank tab and rejects instead of starting an audible task.
 * The tab stays muted across
 * subsequent ``tabs.update`` URL navigations, and the user can unmute it
 * from the tab bar whenever they want — silencing never blocks playback
 * or content extraction.
 */

/** Create a task tab without loading its website until muting succeeds. */
export async function createTaskTab(
  createProperties: chrome.tabs.CreateProperties,
): Promise<chrome.tabs.Tab> {
  const tab = await chrome.tabs.create({ ...createProperties, url: "about:blank" });
  if (tab.id === undefined) throw new Error("task_tab_id_unknown");
  try {
    const mutedTab = await chrome.tabs.update(tab.id, { muted: true });
    if (!mutedTab) throw new Error("task_tab_mute_unconfirmed");
    if (createProperties.url && createProperties.url !== "about:blank") {
      const navigatedTab = await chrome.tabs.update(tab.id, { url: createProperties.url });
      if (!navigatedTab) throw new Error("task_tab_navigation_unconfirmed");
      return navigatedTab;
    }
    return mutedTab;
  } catch (error) {
    try {
      await chrome.tabs.remove(tab.id);
    } catch {
      // The browser may already have closed the task tab.
    }
    throw error;
  }
}
