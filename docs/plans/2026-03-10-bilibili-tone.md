# B站语气动态化 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 让推荐文案、画像描述和聊天回复统一转为更像 B 站老朋友的语气，并根据用户画像动态调整表达风格。

**Architecture:** 新增共享 `tone profile` helper，从画像、偏好和近期反馈中推断语气维度，再统一注入推荐、画像、聊天 prompt。这样语气既有稳定人格基线，也会随着系统理解逐步演化。

**Tech Stack:** Python 3.11, dataclasses, Ruff, MyPy, pytest

---

### Task 1: 为 tone profile 写失败测试并实现最小推断器

**Files:**
- Create: `src/openbiliclaw/soul/tone.py`
- Create: `tests/test_tone_profile.py`

**Step 1: Write the failing test**

在 `tests/test_tone_profile.py` 写：
```python
def test_build_tone_profile_prefers_dense_for_high_information_profile() -> None:
    profile = SoulProfile(
        personality_portrait="偏好高信息密度、会主动把问题想透的人。",
        core_traits=["理性", "克制"],
        values=["真实"],
        life_stage="持续积累",
        deep_needs=["理解复杂问题"],
    )

    tone = build_tone_profile(profile=profile, preference_summary={}, recent_feedback=[])

    assert tone["density"] == "dense"
```

再补一条：
```python
def test_build_tone_profile_increases_playfulness_for_open_explorer() -> None:
    profile = SoulProfile(...)
    preference_summary = {"exploration_openness": 0.9}

    tone = build_tone_profile(...)

    assert tone["playfulness"] in {"medium", "high"}
```

**Step 2: Run test to verify it fails**

Run:
```bash
pytest tests/test_tone_profile.py -q
```
Expected: FAIL because `tone.py` does not exist.

**Step 3: Write minimal implementation**

在 `src/openbiliclaw/soul/tone.py`：
- 定义 `build_tone_profile(profile, preference_summary, recent_feedback)`
- 返回四维 tone profile：`density / warmth / playfulness / directness`
- 缺少上下文时返回中性默认值

**Step 4: Run test to verify it passes**

Run:
```bash
pytest tests/test_tone_profile.py -q
```
Expected: PASS.

**Step 5: Commit**

```bash
git add src/openbiliclaw/soul/tone.py tests/test_tone_profile.py
git commit -m "feat: add tone profile builder"
```

### Task 2: 收口推荐、画像、聊天 prompt 的失败测试

**Files:**
- Modify: `src/openbiliclaw/llm/prompts.py`
- Modify: `tests/test_llm_prompts.py`

**Step 1: Write the failing test**

在 `tests/test_llm_prompts.py` 增加：
```python
def test_socratic_dialogue_prompt_mentions_bilibili_old_friend_tone() -> None:
    messages = build_socratic_dialogue_prompt(
        user_message="我最近总在看国际新闻",
        core_memory_text="## 用户画像\n偏好深度内容",
        history=[],
        tone_profile={
            "density": "dense",
            "warmth": "warm",
            "playfulness": "medium",
            "directness": "balanced",
        },
    )

    assert "老B友" in messages[0]["content"]
```

再补推荐/画像 prompt 的风格断言。

**Step 2: Run test to verify it fails**

Run:
```bash
pytest tests/test_llm_prompts.py -q
```
Expected: FAIL because prompt builders do not yet accept `tone_profile`.

**Step 3: Write minimal implementation**

在 `src/openbiliclaw/llm/prompts.py`：
- 给推荐、画像、聊天相关 prompt builder 增加 `tone_profile` 参数
- 把系统提示改成：
  - 基础风格是“老B友”
  - 明确避免 AI 解释腔、咨询报告腔、客服腔
  - 根据四维 tone profile 调整信息密度、温度、梗感和直给程度

**Step 4: Run test to verify it passes**

Run:
```bash
pytest tests/test_llm_prompts.py -q
```
Expected: PASS.

**Step 5: Commit**

```bash
git add src/openbiliclaw/llm/prompts.py tests/test_llm_prompts.py
git commit -m "feat: add bilibili tone controls to prompts"
```

### Task 3: 把 tone profile 接入推荐、画像、聊天调用链

**Files:**
- Modify: `src/openbiliclaw/recommendation/engine.py`
- Modify: `src/openbiliclaw/soul/profile_builder.py`
- Modify: `src/openbiliclaw/soul/dialogue.py`
- Modify: `tests/test_recommendation_engine.py`
- Modify: `tests/test_api_app.py`

**Step 1: Write the failing test**

在 `tests/test_recommendation_engine.py` 增加：
```python
async def test_generate_expression_uses_tone_profile(monkeypatch):
    ...
    assert "老B友" in captured_system_prompt
```

在聊天链路测试中断言会传入 tone profile。

**Step 2: Run test to verify it fails**

Run:
```bash
pytest tests/test_recommendation_engine.py tests/test_api_app.py -q
```
Expected: FAIL because callers do not yet compute tone profile.

**Step 3: Write minimal implementation**

- `recommendation/engine.py` 在生成 `expression/topic_label` 前构造 tone profile
- `profile_builder.py` 在画像描述 prompt 中注入 tone profile
- `dialogue.py` 在聊天 prompt 中注入 tone profile

**Step 4: Run test to verify it passes**

Run:
```bash
pytest tests/test_recommendation_engine.py tests/test_api_app.py -q
```
Expected: PASS.

**Step 5: Commit**

```bash
git add src/openbiliclaw/recommendation/engine.py src/openbiliclaw/soul/profile_builder.py src/openbiliclaw/soul/dialogue.py tests/test_recommendation_engine.py tests/test_api_app.py
git commit -m "feat: apply adaptive tone across recommendation and dialogue"
```

### Task 4: 更新文档并全量验证

**Files:**
- Modify: `docs/modules/soul.md`
- Modify: `docs/modules/recommendation.md`
- Modify: `docs/modules/llm.md`
- Modify: `docs/modules/extension.md`
- Modify: `docs/changelog.md`

**Step 1: Update docs**

- 在 `docs/modules/soul.md` 说明聊天和画像语气不再是固定模板，而是受 tone profile 控制
- 在 `docs/modules/recommendation.md` 说明朋友式推荐文案改为 B 站老朋友语气，并会随画像动态调整
- 在 `docs/modules/llm.md` 说明 prompt 层新增 tone profile 注入
- 在 `docs/modules/extension.md` 说明 popup “我的画像”和“和阿B聊聊”的内容语气会随长期理解变化
- 在 `docs/changelog.md` 追加本次语气系统改造记录

**Step 2: Run full verification**

Run:
```bash
ruff check src/ tests/
mypy src/
pytest -q
```
Expected: all pass.

**Step 3: Commit**

```bash
git add docs/modules/soul.md docs/modules/recommendation.md docs/modules/llm.md docs/modules/extension.md docs/changelog.md
git commit -m "docs: document adaptive bilibili tone system"
```
