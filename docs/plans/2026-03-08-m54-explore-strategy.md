# 5.4 跨领域探索策略 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 实现 `ExploreStrategy`，从画像中推断“高相关的远域探索领域”，搜索候选并按相关性与探索性组合评分。

**Architecture:** `ExploreStrategy` 通过结构化 prompt 生成探索领域和 query，本地过滤过近领域后调用 B 站搜索，再统一复用 `evaluate_content()` 做相关性评估，并叠加基于 `novelty_level` 与 `exploration_openness` 的 exploration bonus。

**Tech Stack:** Python 3.13, asyncio, pytest, mypy, Ruff, existing LLMService, discovery engine, Bilibili search client.

---

### Task 1: 为探索领域生成与过滤写失败测试

**Files:**
- Create: `tests/test_explore_strategy.py`
- Modify: `src/openbiliclaw/discovery/strategies/strategies.py`
- Modify: `src/openbiliclaw/llm/prompts.py`

**Step 1: Write the failing test**

新增测试，验证 `ExploreStrategy` 会调用 LLM 生成探索领域，并过滤与当前兴趣过近的 domain。

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_explore_strategy.py::test_explore_strategy_generates_and_filters_domains -q`

Expected: FAIL because `ExploreStrategy` is still a stub.

**Step 3: Write minimal implementation**

实现结构化领域生成、解析和近似兴趣过滤。

**Step 4: Run test to verify it passes**

Run the same pytest command and verify PASS.

**Step 5: Commit**

```bash
git add tests/test_explore_strategy.py src/openbiliclaw/discovery/strategies/strategies.py src/openbiliclaw/llm/prompts.py
git commit -m "feat: generate exploration domains"
```

### Task 2: 为搜索执行和 bonus 评分写失败测试

**Files:**
- Modify: `tests/test_explore_strategy.py`
- Modify: `src/openbiliclaw/discovery/strategies/strategies.py`

**Step 1: Write the failing test**

新增测试，验证搜索结果会调用 `evaluate_content()`，并在最终分数中叠加 exploration bonus。

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_explore_strategy.py::test_explore_strategy_applies_exploration_bonus -q`

Expected: FAIL because bonus scoring path is not implemented.

**Step 3: Write minimal implementation**

接通搜索执行、候选映射和组合评分逻辑。

**Step 4: Run test to verify it passes**

Run the same pytest command and verify PASS.

**Step 5: Commit**

```bash
git add tests/test_explore_strategy.py src/openbiliclaw/discovery/strategies/strategies.py
git commit -m "feat: score explore strategy results"
```

### Task 3: 为失败容错写失败测试

**Files:**
- Modify: `tests/test_explore_strategy.py`
- Modify: `src/openbiliclaw/discovery/strategies/strategies.py`

**Step 1: Write the failing test**

新增测试，验证坏 JSON、单个 query 搜索失败、空 query 都不会让整个策略崩掉。

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_explore_strategy.py::test_explore_strategy_tolerates_partial_failures -q`

Expected: FAIL because error handling and fallback are incomplete.

**Step 3: Write minimal implementation**

补齐 JSON 解析保护、query 清洗和部分失败容错。

**Step 4: Run test to verify it passes**

Run the same pytest command and verify PASS.

**Step 5: Commit**

```bash
git add tests/test_explore_strategy.py src/openbiliclaw/discovery/strategies/strategies.py
git commit -m "feat: harden explore strategy failures"
```

### Task 4: 回归 DiscoveryEngine 集成

**Files:**
- Modify: `tests/test_discovery_engine.py`
- Modify: `src/openbiliclaw/discovery/strategies/strategies.py`

**Step 1: Write the failing test**

新增测试，验证注册 `ExploreStrategy` 后 `ContentDiscoveryEngine.discover()` 能直接返回探索结果。

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_discovery_engine.py::test_discovery_engine_runs_explore_strategy -q`

Expected: FAIL because the strategy is not yet fully wired.

**Step 3: Write minimal implementation**

补齐必要适配，确保 engine 集成路径完整。

**Step 4: Run test to verify it passes**

Run the same pytest command and verify PASS.

**Step 5: Commit**

```bash
git add tests/test_discovery_engine.py src/openbiliclaw/discovery/strategies/strategies.py
git commit -m "feat: wire explore strategy into discovery engine"
```

### Task 5: 更新文档

**Files:**
- Modify: `docs/v0.1-todolist.md`
- Modify: `docs/modules/discovery.md`
- Modify: `docs/changelog.md`

**Step 1: Update task status**

把 `5.4` checklist 更新为完成，并补探索策略的目标、评分结构和运行边界。

**Step 2: Verify docs**

Run: `rg -n "5\\.4|ExploreStrategy|跨领域探索" docs/v0.1-todolist.md docs/modules/discovery.md docs/changelog.md`

Expected: updated references appear in all three files.

**Step 3: Commit**

```bash
git add docs/v0.1-todolist.md docs/modules/discovery.md docs/changelog.md
git commit -m "docs: update explore strategy docs"
```

### Task 6: 全量验证

**Files:**
- Verify only

**Step 1: Run Ruff**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m ruff check src/ tests/`

Expected: `All checks passed!`

**Step 2: Run mypy**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m mypy src/`

Expected: `Success: no issues found ...`

**Step 3: Run pytest**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest -q`

Expected: full suite passes.
