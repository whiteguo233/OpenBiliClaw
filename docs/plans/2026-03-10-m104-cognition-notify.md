# M104 关键认知变动提醒 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在反馈刷新、聊天学习和画像更新之后生成“关键认知变化”，并通过插件通知与 popup 画像页展示给用户。

**Architecture:** 后端新增 `cognition_updates.json` 作为轻量认知变化队列；`SoulEngine` 在关键认知变化出现时生成 update，API 暴露 pending/seen 查询，插件 service worker 负责通知，popup 画像页显示最近 1~3 条认知变化。通知优先级低于已有推荐通知，并受 6 小时冷却控制。

**Tech Stack:** Python 3.11+, FastAPI, SQLite + JSON state files, Chrome Extension Manifest V3, Node test runner, TypeScript

---

### Task 1: 为记忆层增加 `cognition_updates.json`

**Files:**
- Modify: `src/openbiliclaw/memory/manager.py`
- Test: `tests/test_memory_manager.py`

**Step 1: Write the failing test**

在 `tests/test_memory_manager.py` 增加：
- 默认返回空列表
- 保存后可读回
- 标记某条 update 为已通知后状态保留

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_memory_manager.py -k cognition -v`

**Step 3: Write minimal implementation**

在 `MemoryManager` 中新增：
- `load_cognition_updates()`
- `save_cognition_updates(updates)`

路径固定为 `data/memory/cognition_updates.json`。

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_memory_manager.py -k cognition -v`

**Step 5: Commit**

```bash
git add tests/test_memory_manager.py src/openbiliclaw/memory/manager.py
git commit -m "feat: add cognition update storage"
```

### Task 2: 在 SoulEngine 中生成关键认知变化

**Files:**
- Modify: `src/openbiliclaw/soul/engine.py`
- Test: `tests/test_soul_engine.py`

**Step 1: Write the failing test**

补 3 组测试：
- 新高权重兴趣 -> 生成 `interest_added`
- 新厌恶主题 -> 生成 `dislike_added`
- 画像显著变化 -> 生成 `profile_shift`

同时补“不够显著不生成”的反例。

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_soul_engine.py -k cognition -v`

**Step 3: Write minimal implementation**

在 `SoulEngine` 中新增内部 helper：
- `_build_cognition_updates_from_preference_change(...)`
- `_build_cognition_updates_from_profile_shift(...)`
- `_append_cognition_updates(...)`

只用简单启发式阈值：
- 新兴趣权重 `>= 0.75`
- 新增 `disliked_topics`
- 核心特质/深层需求/摘要发生明显变化

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_soul_engine.py -k cognition -v`

**Step 5: Commit**

```bash
git add tests/test_soul_engine.py src/openbiliclaw/soul/engine.py
git commit -m "feat: derive cognition updates from profile changes"
```

### Task 3: 暴露认知变化 API

**Files:**
- Modify: `src/openbiliclaw/api/models.py`
- Modify: `src/openbiliclaw/api/app.py`
- Test: `tests/test_api_app.py`

**Step 1: Write the failing test**

在 `tests/test_api_app.py` 增加：
- `GET /api/cognition-updates/pending`
- `POST /api/cognition-updates/seen`
- `GET /api/profile-summary` 包含最近认知变化

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_api_app.py -k cognition -v`

**Step 3: Write minimal implementation**

新增模型：
- `CognitionUpdateOut`
- `PendingCognitionUpdateResponse`
- `CognitionUpdateSeenIn`
- `CognitionUpdateSeenResponse`

新增路由：
- `GET /api/cognition-updates/pending`
- `POST /api/cognition-updates/seen`

并扩展 `ProfileSummaryResponse`，携带 `recent_cognition_updates: list[str]`

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_api_app.py -k cognition -v`

**Step 5: Commit**

```bash
git add tests/test_api_app.py src/openbiliclaw/api/models.py src/openbiliclaw/api/app.py
git commit -m "feat: add cognition update api"
```

### Task 4: 扩展插件通知通道

**Files:**
- Modify: `extension/src/background/notifications.ts`
- Modify: `extension/src/background/service-worker.ts`
- Test: `extension/tests/notifications.test.ts`

**Step 1: Write the failing test**

新增测试：
- 认知通知 ID 构造
- 认知通知文案 fallback
- service worker 优先推荐通知，推荐通知为空时再查认知通知

**Step 2: Run test to verify it fails**

Run: `npm test -- --test-name-pattern cognition`

**Step 3: Write minimal implementation**

在通知 helper 里新增认知通知类型：
- `buildCognitionNotificationId`
- `buildCognitionNotificationOptions`

在 `service-worker.ts` 中：
- 推荐通知检查后，再检查认知通知
- 发出后调用 `/api/cognition-updates/seen`

**Step 4: Run test to verify it passes**

Run: `npm test -- --test-name-pattern cognition`

**Step 5: Commit**

```bash
git add extension/src/background/notifications.ts extension/src/background/service-worker.ts extension/tests/notifications.test.ts
git commit -m "feat: notify cognition updates in extension"
```

### Task 5: popup 画像页展示“最近记住了什么”

**Files:**
- Modify: `extension/popup/popup.html`
- Modify: `extension/popup/popup.js`
- Modify: `extension/popup/popup-helpers.js`
- Test: `extension/tests/popup-helpers.test.ts`
- Test: `extension/tests/popup-layout.test.ts`

**Step 1: Write the failing test**

补测试覆盖：
- `profile-summary` 的最近认知变化 fallback
- 画像 tab 出现“阿B 最近新记住了什么”区块
- 无认知变化时用低打扰空态

**Step 2: Run test to verify it fails**

Run: `npm test -- --test-name-pattern "记住了|cognition|profile"`

**Step 3: Write minimal implementation**

在画像 tab 中新增只读块：
- 标题：`阿B 最近新记住了什么`
- 内容：最近 1~3 条 `summary`

保留当前亮色 UI，不新增确认按钮。

**Step 4: Run test to verify it passes**

Run: `npm test -- --test-name-pattern "记住了|cognition|profile"`

**Step 5: Commit**

```bash
git add extension/popup/popup.html extension/popup/popup.js extension/popup/popup-helpers.js extension/tests/popup-helpers.test.ts extension/tests/popup-layout.test.ts
git commit -m "feat: show recent cognition updates in popup"
```

### Task 6: 全量验证与文档更新

**Files:**
- Modify: `docs/modules/memory.md`
- Modify: `docs/modules/soul.md`
- Modify: `docs/modules/extension.md`
- Modify: `docs/changelog.md`
- Modify: `docs/v0.1-todolist.md`

**Step 1: Update docs**

记录：
- 认知变化 JSON 状态
- 生成规则
- API 接口
- 插件通知与 popup 展示

**Step 2: Run full verification**

Run:

```bash
PIP_CONFIG_FILE=/dev/null PYTHONPATH=src /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m ruff check src/ tests/
PIP_CONFIG_FILE=/dev/null PYTHONPATH=src /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m mypy src/
PIP_CONFIG_FILE=/dev/null PYTHONPATH=src /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest -q
npm test
npm run typecheck
npm run build
```

Expected:
- Python checks pass
- extension tests / typecheck / build pass

**Step 3: Final commit**

```bash
git add docs/modules/memory.md docs/modules/soul.md docs/modules/extension.md docs/changelog.md docs/v0.1-todolist.md
git commit -m "docs: record cognition update notifications"
```
