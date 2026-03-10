# Extension Sidepanel Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 把浏览器插件从 popup 主入口切到 side panel 主入口，同时保留推荐、画像、聊天和手动刷新能力。

**Architecture:** 复用现有 `extension/popup/` 页面作为侧边栏承载页，通过 manifest 切到 `side_panel.default_path`，再让 service worker 接管扩展图标点击和相关入口的打开动作。页面逻辑尽量不拆新文件，主要调整 manifest、后台打开逻辑、布局样式、测试和文档。

**Tech Stack:** Chrome Extension Manifest V3, `chrome.sidePanel`, vanilla JS, existing popup scripts, Node test runner, esbuild

---

### Task 1: 切换 manifest 到 side panel 主入口

**Files:**
- Modify: `extension/manifest.json`
- Test: `extension/tests/manifest-assets.test.ts`

**Step 1: Write the failing test**

在 `extension/tests/manifest-assets.test.ts` 增加断言：

```ts
test('manifest uses side panel instead of popup', () => {
  assert.equal(manifest.permissions.includes('sidePanel'), true);
  assert.equal(manifest.side_panel?.default_path, 'popup/popup.html');
  assert.equal('default_popup' in (manifest.action ?? {}), false);
});
```

**Step 2: Run test to verify it fails**

Run:

```bash
npm test -- --test-name-pattern="manifest uses side panel instead of popup"
```

Expected: FAIL because manifest still declares `default_popup` and has no `side_panel`.

**Step 3: Write minimal implementation**

- 在 `extension/manifest.json`：
  - `permissions` 新增 `"sidePanel"`
  - `action` 只保留 icon
  - 新增：

```json
"side_panel": {
  "default_path": "popup/popup.html"
}
```

**Step 4: Run test to verify it passes**

Run:

```bash
npm test -- --test-name-pattern="manifest uses side panel instead of popup"
```

Expected: PASS.

**Step 5: Commit**

```bash
git add extension/manifest.json extension/tests/manifest-assets.test.ts
git commit -m "feat: switch extension entry to side panel"
```

### Task 2: 让扩展图标点击打开侧边栏

**Files:**
- Modify: `extension/src/background/service-worker.ts`
- Modify: `extension/src/background/notifications.ts`
- Test: `extension/tests/notifications.test.ts`

**Step 1: Write the failing test**

在 `extension/tests/notifications.test.ts` 增加针对 side panel 打开逻辑的测试，例如：

```ts
test('openExtensionUi prefers chrome.sidePanel when available', async () => {
  // fake chrome.sidePanel.open and assert it is called
});
```

如当前 `service-worker.ts` 没有统一入口函数，先在测试里定义你期望的 helper 名称。

**Step 2: Run test to verify it fails**

Run:

```bash
npm test -- --test-name-pattern="openExtensionUi prefers chrome.sidePanel"
```

Expected: FAIL because no side panel opening path exists yet.

**Step 3: Write minimal implementation**

- 在 `service-worker.ts` 新增统一 UI 打开 helper，例如：
  - 优先 `chrome.sidePanel.open({ windowId })`
  - 如果需要，先 `chrome.tabs.query({ active: true, currentWindow: true })`
- 把扩展图标点击处理接到这个 helper
- 如通知点击或认知提醒已有打开 UI 的逻辑，统一改走同一 helper

**Step 4: Run test to verify it passes**

Run:

```bash
npm test -- --test-name-pattern="openExtensionUi prefers chrome.sidePanel"
```

Expected: PASS.

**Step 5: Commit**

```bash
git add extension/src/background/service-worker.ts extension/src/background/notifications.ts extension/tests/notifications.test.ts
git commit -m "feat: open extension ui in side panel"
```

### Task 3: 把 popup 页面调成侧边栏布局

**Files:**
- Modify: `extension/popup/popup.html`
- Modify: `extension/popup/popup.js`
- Modify: `extension/popup/popup-helpers.js`
- Test: `extension/tests/popup-layout.test.ts`
- Test: `extension/tests/popup-copy.test.ts`

**Step 1: Write the failing test**

在 `extension/tests/popup-layout.test.ts` 增加断言，约束页面不再依赖 popup 小尺寸语义，例如：

```ts
test('popup page is structured for side panel browsing', () => {
  assert.match(html, /side-panel|sidebar|panel-shell/);
});
```

如果当前测试更适合通过 class 名或布局容器验证，就明确断言新的主容器 class。

**Step 2: Run test to verify it fails**

Run:

```bash
npm test -- --test-name-pattern="structured for side panel browsing"
```

Expected: FAIL because current markup still是 popup 布局。

**Step 3: Write minimal implementation**

- 在 `popup.html` 增加更适合侧边栏的主容器语义 class
- 在 `popup.js` / `popup-helpers.js` 清理 popup 专属文案或交互假设
- 调整布局使推荐、画像、聊天区域适合更长的纵向停留
- 不新增第二套 sidepanel 页面

**Step 4: Run test to verify it passes**

Run:

```bash
npm test -- --test-name-pattern="structured for side panel browsing"
```

Expected: PASS.

**Step 5: Commit**

```bash
git add extension/popup/popup.html extension/popup/popup.js extension/popup/popup-helpers.js extension/tests/popup-layout.test.ts extension/tests/popup-copy.test.ts
git commit -m "feat: adapt popup page for side panel layout"
```

### Task 4: 完整回归 extension 构建链和文档

**Files:**
- Modify: `docs/modules/extension.md`
- Modify: `docs/changelog.md`

**Step 1: Update docs**

- 在 `docs/modules/extension.md` 把主入口从 popup 改成 side panel
- 说明当前仍复用 `popup/` 页面作为侧边栏承载页
- 在 `docs/changelog.md` 增加一条侧边栏模式交付记录

**Step 2: Run extension verification**

Run:

```bash
npm test
npm run typecheck
npm run build
```

Expected:

- tests 全绿
- typecheck 通过
- build 通过

**Step 3: Run targeted repo verification**

Run:

```bash
pytest tests/test_api_app.py tests/test_refresh_runtime.py -q
```

Expected: PASS, proving extension-facing backend hooks still work.

**Step 4: Commit**

```bash
git add docs/modules/extension.md docs/changelog.md
git commit -m "docs: document extension sidepanel mode"
```
