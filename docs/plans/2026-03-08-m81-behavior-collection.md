# M81 Behavior Collection Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 完善浏览器插件行为采集链路，使内容脚本能够稳定采集 B 站核心行为，service worker 能缓冲并批量发送到 `/api/events`。

**Architecture:** 在 `collector.ts` 中抽出可测试的采集与标准化 helper，补齐 URL 变化、视频事件和按钮行为检测；在 `service-worker.ts` 中增加去重、节流、批量发送与失败回填。文档同步补齐 `8.1` 和插件模块说明。

**Tech Stack:** TypeScript, Chrome Extension Manifest V3, fetch, Python FastAPI backend, pytest for backend verification, manual extension smoke testing

---

### Task 1: Extract collector helpers and add testable surface

**Files:**
- Modify: `extension/src/content/collector.ts`

**Step 1: Write the failing helper usage targets**

先把这些逻辑整理成独立函数，便于后续验证和维护：

- `detectPageType(url?: string)`
- `createDOMSnapshot()`
- `extractBvid(url?: string)`
- `createEvent(type, metadata)`

目标不是先引入测试框架，而是先把现在散落在顶层的逻辑收口成稳定 helper。

**Step 2: Run typecheck-equivalent sanity pass**

Run: `sed -n '1,260p' extension/src/content/collector.ts`

Expected: 能清楚看到旧逻辑仍然散在顶层，需要重构。

**Step 3: Implement minimal refactor**

重构 `collector.ts`，不改变现有 `click/search` 行为，只先把 helper 抽出来。

**Step 4: Review diff**

Run: `git diff -- extension/src/content/collector.ts`

Expected: 仅为结构化重构，未引入新行为。

**Step 5: Commit**

```bash
git add extension/src/content/collector.ts
git commit -m "refactor: structure extension collector helpers"
```

### Task 2: Implement navigation snapshot and video events

**Files:**
- Modify: `extension/src/content/collector.ts`

**Step 1: Add failing behavior notes in code comments**

在 collector 中明确要支持：

- 初次加载发送 `snapshot`
- SPA URL 变化发送 `snapshot`
- `video` 元素上的 `play` / `pause` / `seek`

**Step 2: Implement minimal behavior**

补这些能力：

- 监听 `DOMContentLoaded` 后发送 `snapshot`
- 包装 `history.pushState` / `replaceState`，监听 `popstate`
- URL 变化时重新发 `snapshot`
- 挂载 `video` 元素监听，发送：
  - `view`
  - `pause`
  - `seek`

`metadata` 至少补：

- `bvid`
- `currentTime`
- `duration`

**Step 3: Manual static review**

Run: `rg -n "snapshot|pushState|replaceState|popstate|play|pause|seek" extension/src/content/collector.ts`

Expected: 相关监听和 helper 已就位。

**Step 4: Commit**

```bash
git add extension/src/content/collector.ts
git commit -m "feat: collect navigation and video events"
```

### Task 3: Implement click, hover, scroll, and action-button collection

**Files:**
- Modify: `extension/src/content/collector.ts`

**Step 1: Define event targeting rules**

实现并约束：

- `hover` 仅针对视频卡片/搜索卡片
- `scroll` 使用 debounce
- `comment` / `like` / `coin` / `favorite` 基于按钮文本、aria-label、类名识别点击意图

**Step 2: Implement minimal collection**

补齐：

- 视频卡片 hover 停留阈值
- scroll 停止后上报
- action button click 识别

确保 `metadata` 带：

- `targetText`
- `href`
- `actionLabel`
- `scrollRatio`

**Step 3: Review result**

Run: `rg -n "hover|scroll|comment|like|coin|favorite" extension/src/content/collector.ts`

Expected: 事件分支与识别逻辑都存在。

**Step 4: Commit**

```bash
git add extension/src/content/collector.ts
git commit -m "feat: capture interaction and action events"
```

### Task 4: Harden service worker buffering and delivery

**Files:**
- Modify: `extension/src/background/service-worker.ts`

**Step 1: Identify missing delivery guards**

本轮要补：

- 去重 key
- 高频事件节流
- `enqueueEvent()`
- flush 失败回填

**Step 2: Implement minimal buffering helpers**

增加：

- `buildDedupeKey(event)`
- `shouldEnqueueEvent(event)`
- `enqueueEvent(event)`

保留原有批量发送逻辑，但让：

- 高频 `scroll/hover/snapshot` 更克制
- 缓冲区失败时不丢事件

**Step 3: Inspect final worker flow**

Run: `sed -n '1,260p' extension/src/background/service-worker.ts`

Expected: 收到消息 -> 去重/节流 -> 入队 -> flush 的链路清晰。

**Step 4: Commit**

```bash
git add extension/src/background/service-worker.ts
git commit -m "feat: buffer and dedupe extension events"
```

### Task 5: Update extension documentation

**Files:**
- Modify: `docs/v0.1-todolist.md`
- Modify: `docs/changelog.md`
- Create: `docs/modules/extension.md`

**Step 1: Update docs**

同步：

- `docs/v0.1-todolist.md`：标记 `8.1` 已完成项
- `docs/changelog.md`：追加 `8.1 行为采集`
- `docs/modules/extension.md`：新增插件模块文档，说明：
  - `collector.ts`
  - `service-worker.ts`
  - popup 当前职责
  - 手动联调步骤

**Step 2: Review docs diff**

Run: `git diff -- docs/v0.1-todolist.md docs/changelog.md docs/modules/extension.md`

Expected: 仅包含 M81 和 extension 文档内容。

**Step 3: Commit**

```bash
git add docs/v0.1-todolist.md docs/changelog.md docs/modules/extension.md
git commit -m "docs: add extension collection guide"
```

### Task 6: Run verification and document manual smoke steps

**Files:**
- Verify: `extension/src/content/collector.ts`
- Verify: `extension/src/background/service-worker.ts`
- Verify: `docs/modules/extension.md`

**Step 1: Run Python project verification**

Run: `PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m ruff check src/ tests/`

Expected: `All checks passed!`

**Step 2: Run mypy**

Run: `PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m mypy src/`

Expected: `Success: no issues found ...`

**Step 3: Run pytest**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest -q`

Expected: All backend tests still pass.

**Step 4: Record manual smoke steps in docs**

确保 `docs/modules/extension.md` 包含：

1. `openbiliclaw start`
2. 安装 extension
3. 打开 B 站首页 / 搜索页 / 视频页
4. 观察后端 `/api/events` 或数据库 `events` 表变化

**Step 5: Prepare branch for integration**

Run:

```bash
git status --short
git log --oneline --decorate -5
```

Expected: branch ready for review or merge.
