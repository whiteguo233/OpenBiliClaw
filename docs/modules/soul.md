# 灵魂引擎

> 用户深度理解核心 — 从行为数据到人格画像的推理引擎。

## 概述

`soul/` 包实现了用户理解的核心逻辑，包括：

- **SoulEngine** — 编排器，从事件出发驱动各层分析
- **PreferenceAnalyzer** — LLM 驱动的偏好提取和合并
- **AwarenessAnalyzer** — 基于近期事件生成结构化觉察笔记
- **InsightAnalyzer** — 基于觉察、偏好和画像生成洞察假设
- **DialogueInsightAnalyzer** — 从聊天中提取候选长期理解信号
- **SocraticDialogue** — 苏格拉底式用户对话，通过追问深化理解
- **SoulProfile** — 用户灵魂画像数据结构

## 已实现功能

| 任务 | 状态 | 说明 |
|------|------|------|
| SoulEngine.analyze_events() | ✅ | 事件 → PreferenceAnalyzer → 偏好层更新 |
| PreferenceAnalyzer | ✅ | LLM structured extraction + 合并 + 衰减 |
| SocraticDialogue.respond() | ✅ | 通过 LLMService 调用 LLM，自动注入画像 |
| ProfileBuilder | ✅ | 结构化 prompt + JSON 校验 + `SoulProfile` 构建 |
| SoulEngine.build_initial_profile() | ✅ | 从 history + preference 生成并持久化 `soul.json` |
| SoulEngine.get_profile() | ✅ | 从 soul 层读取画像，未初始化时抛明确异常 |
| AwarenessAnalyzer | ✅ | 近期事件 → `AwarenessNote` 列表，支持同日去重 |
| InsightAnalyzer | ✅ | 觉察 + 偏好 + 画像 → `InsightHypothesis` 列表，支持假设合并 |
| SoulEngine.generate_awareness_note() | ✅ | 生成并持久化 `awareness.json` |
| SoulEngine.generate_insight() | ✅ | 生成并持久化 `insight.json` |
| SoulEngine.update_from_feedback() | ✅ | feedback 事件落库，并更新匹配洞察状态 |
| SoulEngine.process_feedback_batch_if_needed() | ✅ | 达到反馈阈值后重分析偏好，并在变化明显时重建画像 |
| DialogueInsightAnalyzer | ✅ | 从聊天轮次提取 `goal/value/interest/dislike/state` 候选信号 |
| SoulEngine.learn_from_dialogue() | ✅ | 聊天落 `dialogue` 事件、累计 insight candidate，并在达阈值时驱动偏好/画像更新 |

## 公开 API

### SoulEngine

```python
from openbiliclaw.soul.engine import SoulEngine

engine = SoulEngine(llm=registry, memory=memory_manager)

# 分析事件批次 → 更新偏好层
await engine.analyze_events([
    {"event_type": "view", "title": "世界史解说"},
    {"event_type": "search", "title": "纪录片推荐"},
])
# 执行后 memory_manager.get_layer("preference").data 已更新并持久化

result = await engine.process_feedback_batch_if_needed()
# {
#   "triggered": True,
#   "feedback_count": 3,
#   "preference_updated": True,
#   "profile_rebuilt": True,
# }

learning = await engine.learn_from_dialogue(
    user_message="我最近更想把国际新闻背后的结构看明白。",
    assistant_reply="听起来你在追求一种能把复杂事件看清楚的框架。",
    session="cli",
)
# {
#   "event_logged": True,
#   "candidate_count": 1,
#   "preference_updated": False,
#   "profile_rebuilt": False,
# }
```

### SocraticDialogue

```python
from openbiliclaw.soul.dialogue import SocraticDialogue

dialogue = SocraticDialogue(
    llm=None,
    soul_engine=engine,
    llm_service=service,
    session="cli",
)

reply = await dialogue.respond("我最近很喜欢看讲得很透的纪录片")
# reply: "我猜你喜欢的是那种能慢慢展开逻辑的讲述方式..."

print(dialogue.history)  # [DialogueTurn(role="user", ...), DialogueTurn(role="agent", ...)]
dialogue.clear_history()
```

### PreferenceAnalyzer

```python
from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

analyzer = PreferenceAnalyzer(registry=llm_registry)
updated_pref = await analyzer.analyze_events(
    events=[...],
    existing_preference=current_pref,
)
# 返回:
# {
#   "interests": [{"name": "历史", "category": "知识", "weight": 0.82, ...}],
#   "style": {"preferred_duration": "long", "depth_preference": 0.91},
#   "exploration_openness": 0.66,
#   "favorite_up_users": ["小约翰可汗"],
#   "disliked_topics": ["低质标题党"],
# }
```

### ProfileBuilder / SoulProfile

```python
from openbiliclaw.soul.profile_builder import ProfileBuilder

builder = ProfileBuilder(registry=llm_registry)
profile = await builder.build(
    history=[
        {"title": "AI 工具实测", "author": "科技UP主"},
        {"title": "效率系统分享", "author": "知识UP主"},
    ],
    preference=current_pref,
)

assert len(profile.personality_portrait) >= 200
assert 3 <= len(profile.core_traits) <= 5
```

```python
profile = await engine.build_initial_profile(history=[...])
loaded = await engine.get_profile()
assert loaded.core_traits == profile.core_traits
```

### AwarenessAnalyzer / InsightAnalyzer

```python
from openbiliclaw.soul.awareness_analyzer import AwarenessAnalyzer
from openbiliclaw.soul.insight_analyzer import InsightAnalyzer

awareness = AwarenessAnalyzer(registry=llm_registry)
notes = await awareness.analyze(
    events=recent_events,
    preference=current_pref,
    soul_profile=current_soul,
)

insight = InsightAnalyzer(registry=llm_registry)
hypotheses = await insight.analyze(
    awareness_notes=notes,
    preference=current_pref,
    soul_profile=current_soul,
)
```

### DialogueInsightAnalyzer

```python
from openbiliclaw.soul.dialogue_insight_analyzer import DialogueInsightAnalyzer

analyzer = DialogueInsightAnalyzer(registry=llm_service)
candidates = await analyzer.extract(
    user_message="我其实更想知道国际事件背后的因果链。",
    assistant_reply="你像是在找一种更稳定的理解框架。",
    core_memory=memory.get_core_memory(),
)
# [
#   {
#     "kind": "goal",
#     "content": "想更系统地理解国际局势",
#     "confidence": 0.84,
#     "evidence": "用户明确表达想看清背后的因果链。"
#   }
# ]
```

## 设计决策

1. **偏好提取用 json_mode**：确保 LLM 返回结构化 JSON，便于程序处理
2. **对话错误优雅降级**：LLM 调用失败时返回友好中文提示，不崩溃
3. **`_build_service()` 回退**：未注入 LLMService 时从 SoulEngine 自动构建
4. **历史格式转换**：`agent` → `assistant` 角色映射，适配 OpenAI 消息格式
5. **画像生成独立为 `ProfileBuilder`**：避免把 prompt/JSON 校验逻辑塞进 `SoulEngine`
6. **灵魂层失败不覆盖旧画像**：坏 JSON、空响应、缺字段时直接报错，已有 `soul.json` 保留
7. **觉察层保守去重**：同日 observation 标准化后相同则跳过，避免流水账堆积
8. **洞察层按假设文本合并**：相同 hypothesis 合并 evidence，confidence 取较高值
9. **验证状态只由代码更新**：LLM 只生成 hypothesis/evidence/confidence，`validated` 不信任模型输出
10. **反馈达到阈值后再学习**：默认累计 3 条新反馈才触发偏好重分析，避免单次噪声反馈频繁扰动画像
11. **画像重建走显著变化阈值**：只有高权重兴趣明显变化或新增 `disliked_topics` 时才重建 `SoulProfile`
12. **聊天信号受控生效**：聊天先落 `dialogue` 事件和 `insight_candidates.json`，只有高置信度且重复出现的候选才会进入偏好更新
