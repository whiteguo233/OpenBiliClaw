/**
 * Shared helpers for tabs the extension opens automatically (issue #163).
 *
 * Background task tabs (Douyin, Xiaohongshu, Bilibili, Zhihu, Reddit, ...)
 * are created without any user gesture. Platforms that autoplay media in
 * such tabs would otherwise startle a user who left the computer on, so
 * every task tab is muted immediately after creation.
 *
 * Chrome does not accept ``muted`` in ``tabs.create`` (unlike Firefox 100+
 * and Safari 14+), so the portable path is create first, then
 * ``tabs.update(tabId, { muted: true })``. The tab stays muted across
 * subsequent ``tabs.update`` URL navigations, and the user can unmute it
 * from the tab bar whenever they want — silencing never blocks playback
 * or content extraction.
 */

/** Create an auto-opened task tab and mute it before handing it back. */
export async function createTaskTab(
  createProperties: chrome.tabs.CreateProperties,
): Promise<chrome.tabs.Tab> {
  const tab = await chrome.tabs.create(createProperties);
  if (tab.id !== undefined) {
    try {
      await chrome.tabs.update(tab.id, { muted: true });
    } catch {
      // Engines without tab muting still have a fully usable task tab;
      // the only consequence is that audio is not silenced there.
    }
  }
  return tab;
}
