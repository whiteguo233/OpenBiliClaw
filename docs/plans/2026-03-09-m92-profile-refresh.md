# M92 Profile Refresh Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在收到足够多的新反馈后自动重分析偏好层，并在偏好变化明显时自动更新灵魂画像。

**Architecture:** 继续复用事件层与 `PreferenceAnalyzer`，新增最小 `feedback_state.json` 追踪未处理反馈。反馈成功后同步检查阈值，达到后触发偏好层更新，并按简单启发式决定是否重建画像。

**Tech Stack:** Python, SQLite, JSON layer storage, existing SoulEngine / MemoryManager / PreferenceAnalyzer

---

### Task 1: Add failing tests for feedback-triggered profile refresh

**Files:**
- Modify: `tests/test_soul_engine.py`
- Modify: `tests/test_memory_manager.py`
- Modify: `tests/test_api_app.py`
- Modify: `tests/test_cli.py`

**Step 1: Add failing `SoulEngine` tests**

新增测试覆盖：

- 新反馈数少于阈值时不触发更新
- 达阈值时更新偏好层
- 偏好变化不足阈值时不重建画像
- 偏好变化明显时重建画像

**Step 2: Add failing `MemoryManager` tests**

新增：

- `feedback_state.json` 可读写
- 文件不存在时有默认状态

**Step 3: Add failing trigger-path tests**

新增：

- API `/api/feedback` 成功后会调用反馈批次检查
- CLI `feedback` 成功后会调用反馈批次检查

**Step 4: Run focused tests**

Run:

```bash
PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_soul_engine.py tests/test_memory_manager.py tests/test_api_app.py tests/test_cli.py -q
```

Expected: FAIL because feedback batch processing does not exist yet.

**Step 5: Commit**

```bash
git add tests/test_soul_engine.py tests/test_memory_manager.py tests/test_api_app.py tests/test_cli.py
git commit -m "test: cover feedback-driven profile refresh"
```

### Task 2: Implement feedback state persistence in MemoryManager

**Files:**
- Modify: `src/openbiliclaw/memory/manager.py`
- Modify: `tests/test_memory_manager.py`

**Step 1: Add feedback state helpers**

在 `MemoryManager` 中新增：

- `load_feedback_state()`
- `save_feedback_state(state)`

默认结构：

```python
{
  "last_processed_feedback_event_id": 0,
  "last_feedback_reanalyzed_at": "",
}
```

**Step 2: Store state under memory dir**

文件路径：

- `data/memory/feedback_state.json`

**Step 3: Run focused tests**

Run:

```bash
PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_memory_manager.py -q
```

Expected: PASS

**Step 4: Commit**

```bash
git add src/openbiliclaw/memory/manager.py tests/test_memory_manager.py
git commit -m "feat: persist feedback processing state"
```

### Task 3: Implement `SoulEngine.process_feedback_batch_if_needed()`

**Files:**
- Modify: `src/openbiliclaw/soul/engine.py`
- Modify: `tests/test_soul_engine.py`

**Step 1: Add feedback batch loader**

在 `SoulEngine` 中查询：

- `event_type = "feedback"`
- `id > last_processed_feedback_event_id`

**Step 2: Add threshold check**

默认阈值：

- `3`

若未达到阈值，返回：

```python
{"triggered": False, "feedback_count": X, "preference_updated": False, "profile_rebuilt": False}
```

**Step 3: Re-run preference analysis**

达到阈值时：

- 读取旧偏好
- 调 `analyze_events(new_feedback_events)`
- 保存新偏好

**Step 4: Compare preference delta**

实现一个私有 helper，用简单启发式判断变化是否显著：

- 高权重标签新增/移除 `>= 2`
- 或高权重标签权重变化 `>= 0.2`
- 或新增 `disliked_topics`

**Step 5: Rebuild profile if needed**

若变化显著：

- 调 `build_initial_profile(history=[])` 的等价路径不合适
- 直接复用 `ProfileBuilder.build(history=[], preference=new_preference)` 构建新画像
- 持久化到 `soul` 层

**Step 6: Update feedback state**

成功后推进：

- `last_processed_feedback_event_id`
- `last_feedback_reanalyzed_at`

**Step 7: Run focused tests**

Run:

```bash
PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_soul_engine.py -q
```

Expected: PASS

**Step 8: Commit**

```bash
git add src/openbiliclaw/soul/engine.py tests/test_soul_engine.py
git commit -m "feat: refresh profile after feedback threshold"
```

### Task 4: Trigger feedback refresh from API and CLI

**Files:**
- Modify: `src/openbiliclaw/api/app.py`
- Modify: `src/openbiliclaw/cli.py`
- Modify: `tests/test_api_app.py`
- Modify: `tests/test_cli.py`

**Step 1: Wire API trigger**

在 `/api/feedback` 成功写入后调用：

- `SoulEngine.process_feedback_batch_if_needed()`

**Step 2: Wire CLI trigger**

在 `feedback` 命令成功写入后调用同一路径。

**Step 3: Keep failure semantics stable**

- 即使反馈后续重分析失败，反馈本身仍然算成功
- 只记录日志，不把反馈提交变成失败

**Step 4: Run focused tests**

Run:

```bash
PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_api_app.py tests/test_cli.py -q
```

Expected: PASS

**Step 5: Commit**

```bash
git add src/openbiliclaw/api/app.py src/openbiliclaw/cli.py tests/test_api_app.py tests/test_cli.py
git commit -m "feat: trigger profile refresh after feedback"
```

### Task 5: Update docs for 9.2

**Files:**
- Modify: `docs/v0.1-todolist.md`
- Modify: `docs/modules/memory.md`
- Modify: `docs/modules/soul.md`
- Modify: `docs/modules/recommendation.md`
- Modify: `docs/changelog.md`

**Step 1: Mark 9.2 complete**

在 `docs/v0.1-todolist.md` 中把 `9.2` 三项标记为完成。

**Step 2: Update module docs**

- `memory.md`：增加 `feedback_state.json`
- `soul.md`：增加反馈阈值触发更新说明
- `recommendation.md`：补反馈从“记录”到“驱动更新”的说明

**Step 3: Update changelog**

新增 `9.2` 记录。

**Step 4: Review docs diff**

Run:

```bash
git diff -- docs/v0.1-todolist.md docs/modules/memory.md docs/modules/soul.md docs/modules/recommendation.md docs/changelog.md
```

Expected: only `9.2`-related changes.

**Step 5: Commit**

```bash
git add docs/v0.1-todolist.md docs/modules/memory.md docs/modules/soul.md docs/modules/recommendation.md docs/changelog.md
git commit -m "docs: update feedback refresh workflow"
```

### Task 6: Run full verification

**Files:**
- Verify: `src/openbiliclaw/memory/manager.py`
- Verify: `src/openbiliclaw/soul/engine.py`
- Verify: `src/openbiliclaw/api/app.py`
- Verify: `src/openbiliclaw/cli.py`

**Step 1: Run Python verification**

Run:

```bash
PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m ruff check src/ tests/
PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m mypy src/
PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest -q
```

Expected:

- Ruff clean
- mypy clean
- pytest all pass

**Step 2: Sanity-check extension still builds**

Run:

```bash
cd extension
npm test
npm run typecheck
npm run build
```

Expected: PASS

**Step 3: Manual validation checklist**

记录联调步骤：

- 连续提交 3 条 dislike/comment 反馈
- 确认 `feedback_state.json` 更新
- 确认 `preference.json` 改变
- 若变化明显，确认 `soul.json` 更新时间变化
