import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

const extensionFile = (path: string) => readFileSync(resolve(path), "utf8");
const projectFile = (path: string) => readFileSync(resolve("..", path), "utf8");

test("popup wires pending list, card actions, and the shared renderer into the durable chat flow", () => {
  const api = extensionFile("popup/popup-api.js");
  const popup = extensionFile("popup/popup.js");
  const html = extensionFile("popup/popup.html");

  assert.match(api, /export async function fetchPendingConfirmations/);
  assert.match(api, /export async function openPendingConfirmation/);
  assert.match(api, /export async function actOnChatCard/);
  assert.match(popup, /OpenBiliClawDialogueConfirmation/);
  assert.match(popup, /selectDialogueTurns/);
  assert.match(popup, /executeCardAction/);
  assert.match(popup, /isTerminalCardTurn/);
  assert.match(popup, /isTerminalCardTurn\(dialogueTurnsById\.get/);
  assert.match(popup, /fetchTurn[\s\S]*fetchChatTurn/);
  assert.match(popup, /dialogueCardActionAbortController\.signal/);
  assert.match(popup, /session:\s*CHAT_SESSION/);
  assert.match(html, /id="chatPendingToggle"/);
  assert.match(html, /id="chatPendingList"/);
  assert.match(html, /shared\/dialogue-confirmation\.js/);
});

test("toolbar badge does not poll or count pending confirmations", () => {
  const serviceWorker = extensionFile("src/background/service-worker.ts");
  const badge = extensionFile("src/background/badge.ts");

  assert.equal(
    (serviceWorker.match(/chrome\.alarms\.create\(/g) ?? []).length,
    1,
    "the existing event flush alarm remains the only alarm created here",
  );
  assert.match(serviceWorker, /BUFFER_FLUSH_INTERVAL\s*=\s*30_000/);
  assert.doesNotMatch(serviceWorker, /pending-confirmations/);
  assert.doesNotMatch(serviceWorker, /pendingConfirmationCount/);
  assert.doesNotMatch(serviceWorker, /PendingBadgeRefreshScheduler/);
  assert.doesNotMatch(badge, /BADGE_TITLE_PENDING|BADGE_COLOR_PENDING/);
  assert.match(badge, /computeActionBadge\([\s\S]*reachable[\s\S]*uninitialized/);
});

test("popup keeps its internal pending-confirmation count and list API", () => {
  const api = extensionFile("popup/popup-api.js");
  const popup = extensionFile("popup/popup.js");

  assert.match(api, /fetchPendingConfirmations/);
  assert.match(api, /chat\/pending-confirmations/);
  assert.match(popup, /chatPendingTabCount/);
  assert.match(popup, /pendingConfirmations/);
});

test("all visible clients hydrate and reconnect the pending-confirmation badge", () => {
  const popup = extensionFile("popup/popup.js");
  const desktop = projectFile("src/openbiliclaw/web/desktop/assets/js/app.js");
  const mobileApp = projectFile("src/openbiliclaw/web/js/app.js");
  const mobileChat = projectFile("src/openbiliclaw/web/js/views/chat.js");
  const mobileState = projectFile("src/openbiliclaw/web/js/state.js");
  const mobileCss = projectFile("src/openbiliclaw/web/css/app.css");

  assert.match(popup, /await refreshPendingConfirmations\(\)/);
  assert.match(
    popup,
    /onConnect\(\)[\s\S]*?scheduleDialogueConfirmationRefresh\(\)/,
    "the popup must heal an empty startup count when its stream reconnects",
  );
  assert.match(
    popup,
    /onOnline:\s*async \(\) =>[\s\S]*?scheduleDialogueConfirmationRefresh\(\)/,
    "the popup HTTP recovery path must also heal the count",
  );

  assert.match(
    desktop,
    /const pendingConfirmationsPromise = refreshDesktopPendingConfirmations\(\);[\s\S]*const recommendationsPromise = shouldReadRecommendations\s*\? readRecommendationSnapshot\(\)/,
    "desktop must start the badge request before the recommendation-card request fan-out",
  );
  assert.match(desktop, /const secondaryPromises = \[\s*pendingConfirmationsPromise,/);
  assert.match(desktop, /function handleRuntimeEvent\(event\)[\s\S]*scheduleDesktopPendingConfirmationRefresh\(\)/);
  assert.match(desktop, /socket\.addEventListener\("open"[\s\S]*scheduleDesktopPendingConfirmationRefresh\(\)/);

  assert.match(mobileState, /pendingConfirmationCount:\s*0/);
  assert.match(mobileApp, /class="tab-count-badge"/);
  assert.match(mobileApp, /refreshChatPendingConfirmations\(\{ renderNow: false \}\)/);
  assert.match(mobileChat, /export async function refreshPendingConfirmations/);
  assert.match(mobileChat, /patchState\(\{ pendingConfirmationCount:/);
  assert.match(mobileChat, /export function onStreamEvent[\s\S]*pendingConfirmationRefreshTimer/);
  assert.match(mobileCss, /\.tab-count-badge\s*\{/);
  assert.match(mobileCss, /\.tab-count-badge\[hidden\]/);
});

test("desktop mirrors popup semantics with the shared chat session and a visible pending count", () => {
  const app = projectFile("src/openbiliclaw/web/desktop/assets/js/app.js");
  const html = projectFile("src/openbiliclaw/web/desktop/index.html");

  assert.match(app, /pendingConfirmations:\s*"\/chat\/pending-confirmations"/);
  assert.match(app, /OpenBiliClawDialogueConfirmation/);
  assert.match(app, /executeCardAction/);
  assert.match(app, /fetchTurn[\s\S]*ENDPOINTS\.chatTurns/);
  assert.match(app, /dialogueCardActionAbortController\.signal/);
  assert.match(app, /SHARED_CHAT_SESSION\s*=\s*"popup"/);
  assert.match(app, /session:\s*SHARED_CHAT_SESSION/);
  assert.match(html, /id="chatPendingCountBadge"/);
  assert.match(html, /id="desktopPendingConfirmations"/);
  assert.match(html, /\/shared\/dialogue-confirmation\.js/);
});

test("probe chats use the shared main-dialogue history on popup and desktop", () => {
  const popup = extensionFile("popup/popup.js");
  const desktop = projectFile("src/openbiliclaw/web/desktop/assets/js/app.js");
  const shared = projectFile("src/openbiliclaw/web/shared/dialogue-confirmation.js");

  assert.match(shared, /"probe"/);
  assert.match(shared, /"avoidance_probe"/);
  assert.match(popup, /isDialogueReplyTurn\(turn\)/);
  assert.match(desktop, /chatTurns\}\?session=.*&limit=100/);
  assert.match(desktop, /function applyChatSnapshot\(snapshot\)[\s\S]*applyDialogueChatSnapshot\(snapshot\)/);
});

test("popup and desktop toast honestly when anchor refusal becomes retryable_error", () => {
  // Shared helper maps stale_anchor → retryable_error with reason; both
  // surfaces must surface that reason instead of the success "已确认" branch.
  const popup = extensionFile("popup/popup.js");
  const desktop = projectFile("src/openbiliclaw/web/desktop/assets/js/app.js");
  const shared = projectFile("src/openbiliclaw/web/shared/dialogue-confirmation.js");

  assert.match(shared, /ANCHOR_REFUSAL_OUTCOMES/);
  assert.match(shared, /stale_anchor/);
  assert.match(shared, /anchor_dependency_failed/);
  assert.match(shared, /retryableCardResult\(original, action, outcome/);

  for (const [name, source] of [
    ["popup", popup],
    ["desktop", desktop],
  ] as const) {
    assert.match(
      source,
      /stale_anchor[\s\S]*anchor_dependency_failed|anchor_dependency_failed[\s\S]*stale_anchor/,
      `${name} must branch on both anchor-refusal reasons`,
    );
    assert.match(
      source,
      /这条暂时结算不了：你正在聊另一条，先把那条聊完或结束再试/,
      `${name} must show the honest anchor-refusal copy`,
    );
  }
});

test("mobile active insights stay read-only and point to the mobile dialogue entry", () => {
  const profile = projectFile("src/openbiliclaw/web/js/views/profile.js");

  assert.doesNotMatch(profile, /submitInsightFeedback/);
  assert.doesNotMatch(profile, /bindInsightActions/);
  assert.doesNotMatch(profile, /data-insight-idx/);
  assert.match(profile, /insight-readonly/);
  assert.match(profile, /请在「聊聊口味」的待聊确认入口处理/);
});

test("popup and desktop cognition insights are read-only while the legacy endpoint remains", () => {
  const popup = extensionFile("popup/popup.js");
  const popupApi = extensionFile("popup/popup-api.js");
  const popupHtml = extensionFile("popup/popup.html");
  const desktop = projectFile("src/openbiliclaw/web/desktop/assets/js/app.js");
  const desktopCss = projectFile("src/openbiliclaw/web/desktop/assets/css/app.css");
  const backend = projectFile("src/openbiliclaw/api/app.py");

  assert.doesNotMatch(popup, /submitInsightFeedback/);
  assert.doesNotMatch(popup, /handleInsightFeedback/);
  assert.doesNotMatch(popup, /insight-action-btn/);
  assert.doesNotMatch(popupApi, /submitInsightFeedback/);
  assert.doesNotMatch(popupHtml, /\.insight-actions/);
  assert.match(popup, /洞察区只读；请在对话的待聊确认入口继续/);

  assert.doesNotMatch(desktop, /data-insight-action/);
  assert.doesNotMatch(desktop, /bindInsightActions/);
  assert.doesNotMatch(desktop, /respondInsightFeedback/);
  assert.doesNotMatch(desktopCss, /\.insight-actions/);
  assert.match(desktop, /洞察区只读；请在对话的待聊确认入口继续/);

  assert.match(backend, /@app\.post\("\/api\/insights\/feedback"/);
});

test("popup chat-tab red dot is opt-in and off by default", () => {
  const popup = extensionFile("popup/popup.js");
  const html = extensionFile("popup/popup.html");

  // Quick switch at the top of the 「对话」 tab.
  assert.match(html, /id="chatPendingBadgeToggle" type="checkbox"/);
  assert.match(html, /id="chatPendingBadgeToggleText"[^>]*>标签红点/);
  assert.ok(
    html.indexOf('id="chatPendingBadgeToggle"') < html.indexOf('id="chatPendingList"'),
    "the quick switch sits above the pending-confirmation list",
  );

  assert.match(popup, /CHAT_PENDING_BADGE_STORAGE_KEY = "openbiliclaw\.popup\.showChatPendingBadge"/);
  assert.match(popup, /let showChatPendingBadge = false;/);
  assert.match(popup, /localStorage\.getItem\(CHAT_PENDING_BADGE_STORAGE_KEY\) === "1"/);
  assert.match(
    popup,
    /elements\.chatPendingTabCount\.hidden = !showChatPendingBadge \|\| count <= 0;/,
  );
  assert.match(popup, /elements\.chatPendingBadgeToggle\.addEventListener\("change"/);
});
