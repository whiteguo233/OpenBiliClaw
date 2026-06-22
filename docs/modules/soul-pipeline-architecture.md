# OpenBiliClaw 用户画像管道架构文档

## 概述

OpenBiliClaw 采用**五层记忆网络 + 五层洋葱模型**的双层架构，将原始行为数据转化为深层的分层用户理解。本文档描述了灵魂引擎（Soul Engine）中各核心模块的职责、接口边界和优化空间。

---

## 模块清单与接口规范

### 1. 引擎控制层（Engine）

**职责：** 协调各层分析器，驱动整个画像更新流程

**位置：** `src/openbiliclaw/soul/engine.py`

**输入：**
- 原始行为事件列表 `list[dict[str, Any]]`（view、like、comment 等）
- 用户历史数据 `list[dict[str, Any]]`（首次初始化时的 B 站观看历史）
- 用户反馈 `dict[str, Any]`（对insight的确认/否定）
- 对话文本 `(user_message: str, assistant_reply: str)`

**输出：**
- 更新后的五层记忆（Preference -> Awareness -> Insight -> Soul）
- OnionProfile 对象（持久化为 JSON）
- 操作结果 `dict[str, object]`（含更新统计）

**依赖：**
- PreferenceAnalyzer（偏好提取）
- ProfileBuilder（初始画像生成）
- AwarenessAnalyzer（日观察笔记）
- DialogueInsightAnalyzer（对话洞察）
- InsightAnalyzer（假设生成）
- ProfileUpdatePipeline（增量更新管道）
- InterestSpeculator（猜测兴趣）
- MemoryManager（五层持久化）

**被依赖：**
- 上层应用（对话/推荐系统）

**可优化点：**
- `learn_from_dialogue` 的 insight 候选合并逻辑基于字符串匹配，可改为语义相似度
- 缺少对优先级的动态调整（例如用户高频反馈应提升某些信号的权重）
- 缺少"画像漂移检测"（user profile drift），难以察觉长期转变

---

### 2. 偏好分析层（PreferenceAnalyzer）

**职责：** 从事件序列中提取结构化的偏好信号，执行衰减和合并逻辑

**位置：** `src/openbiliclaw/soul/preference_analyzer.py`

**输入：**
- 事件列表 `list[dict[str, object]]`（event_type、title、category、tags 等）
- 现有偏好 `dict[str, object]`（interests、style、context、disliked_topics 等）
- 时间戳（用于衰减计算）

**输出：**
- 更新后的偏好字典 `dict[str, object]`，包含：
  - `interests`：加权兴趣标签列表，按权重降序排列
  - `style`：ContentStyle（时长、节奏、质量敏感度、幽默度、深度度）
  - `context`：ContextMode（工作日/周末模式、时段偏好、session类型）
  - `disliked_topics`：排斥的内容类型
  - `favorite_up_users`：常看UP主
  - `exploration_openness`：探索新领域的开放度（0-1）
  - `cognitive_style`：认知风格（从LLM提取，不纳入PreferenceLayer）
  - `speculative_interests`：由LLM推测的待验证兴趣方向

**调用方式：**
```python
updated = await analyzer.analyze_events(
    events=raw_events,
    existing_preference=current_pref
)
```

**依赖：**
- LLMService（通过 `complete_structured_task` 调用LLM）
- Prompt: `build_preference_analysis_prompt`

**被依赖：**
- SoulEngine（初始化和更新）
- ProfileUpdatePipeline（Interest层更新）
- Layer Updaters（Interest层更新的实现）

**实现细节：**
- **权重衰减：** 使用指数衰减 `weight * (decay_factor_per_week ** weeks)`，默认周衰减率 0.9
- **兴趣合并：** 按 `(name, category)` 元组去重，新事件中的权重与历史最大值取max后再更新
- **UP主提取：** 优先使用LLM输出的完整频道名，仅当新事件无UP主时才保留历史
- **Dislike推断：** 聚合新旧dislike，结合负面信号（跳过、快退、低完播率）和兴趣反面推断
- **Speculative种子：** 保留LLM输出以供Speculator消费

**可优化点：**
- 衰减因子、最小权重阈值目前为硬编码，可改为可配置
- 兴趣合并的"最大值"策略容易保留噪音，可改为加权移动平均
- 缺少"兴趣聚类"，相同大类的细分标签可能大幅重复
- `speculative_interests` 仅在新分析时生成，可增加周期性重新推断

---

### 3. 初始画像生成（ProfileBuilder）

**职责：** 从历史数据 + 偏好 + 观察笔记 + insight，一次性生成或重生成Soul层

**位置：** `src/openbiliclaw/soul/profile_builder.py`

**输入：**
- 历史摘要 `dict[str, object]`（标题列表、作者列表、观看计数）
- 偏好摘要 `dict[str, object]`（interests、style、context等，由MemoryManager提供）
- 近期观察笔记 `list[dict[str, object]]`（AwarenessNote）
- 活跃insight `list[dict[str, object]]`（InsightHypothesis）
- 音调档案 `ToneProfile`（可选，用于调整表述风格）

**输出：**
- SoulProfile 对象（遗留模型），转换为 OnionProfile

**输出字段：**
- `personality_portrait`：目标 150-260 字、后端校验容忍 120-500 字的自然语言人格描述
- `core_traits`：3-6条核心特质
- `cognitive_style`：认知风格列表
- `motivational_drivers`：内在驱动力
- `current_phase`：当前人生阶段（非空）
- `values`：价值观列表
- `life_stage`：人生阶段标签
- `deep_needs`：深层心理需求
- `mbti`：MBTI推断及置信度

**调用方式：**
```python
profile = await builder.build(
    history=history_list,
    preference=pref_dict,
    awareness_notes=notes_list,
    active_insights=insights_list
)
```

**依赖：**
- LLMService（`complete_structured_task`）
- Prompt: `build_soul_profile_prompt`

**被依赖：**
- SoulEngine（初始化 + 人格重生成）
- ProfileUpdatePipeline（Portrait层更新）

**验证规则：**
- `personality_portrait` 长度在 120-500 字符之间
- `current_phase` 缺失时会补保守占位，后续画像更新可再细化
- `core_traits`, `cognitive_style`, `motivational_drivers`, `values`, `deep_needs` 缺失或轻微格式不符时会归一化为空列表或单元素列表

**可优化点：**
- 目前仅在Core/Values层变化时触发重生成，可考虑定期更新（月度重述）
- 历史摘要仅取前20个标题+前10个作者，信息压缩度高，可改为加权采样
- 无法针对新发现的特质进行"增量编辑"

---

### 4. 数据模型（Profile）

**职责：** 定义所有用户理解数据的结构，支持序列化/反序列化

**位置：** `src/openbiliclaw/soul/profile.py`

**洋葱模型（OnionProfile）五层架构（从内到外）：**

| 层 | 字段 | 变化频率 |
|----|------|---------|
| **CoreLayer** | `core_traits`, `deep_needs`, `mbti` | 极低 |
| **ValuesLayer** | `values`, `motivational_drivers` | 低 |
| **InterestLayer** | `likes`(树形), `dislikes`, `favorite_up_users` | 中 |
| **RoleLayer** | `life_stage`, `current_phase` | 中低 |
| **SurfaceLayer** | `cognitive_style`, `style`, `context`, `exploration_openness` | 高 |

**序列化方法：**
- `to_dict()` -> JSON可序列化的字典
- `from_dict()` -> 从JSON反构造
- `from_legacy()` -> 从遗留SoulProfile升级
- `to_llm_context(*, include_portrait=True)` -> 格式化为LLM输入文本；兴趣 / 规避探测器传 `include_portrait=False` 略过 `personality_portrait` 那段叙事（eval / persona 渲染保留默认，画像总结是 persona 真值）
- `populate_from_flat_preference()` -> 从偏好层填充Interest和Surface

---

### 5. 增量更新管道（ProfileUpdatePipeline）

**职责：** 将所有类型的输入信号分类、缓冲、按层触发更新

**位置：** `src/openbiliclaw/soul/pipeline.py`

**输入：**
- ProfileSignal 对象
  - `signal_type`：BEHAVIOR_EVENT, ENGAGEMENT_EVENT, FEEDBACK, DIALOGUE_INSIGHT, DIALOGUE_TURN, ACCOUNT_SNAPSHOT
  - `source`：信号来源
  - `payload`：原始数据
  - `confidence`：置信度（0-1）

**输出：**
- IngestResult: `signals_accepted`, `layers_buffered`, `layers_updated`

**阈值配置：**

| 层 | min_signals | min_interval | max_buffer |
|----|------------|-------------|------------|
| SURFACE | 3 | 5min | 200 |
| INTEREST | 3 | 10min | 200 |
| ROLE | 5 | 24h | 50 |
| VALUES | 5 | 24h | 50 |
| CORE | 8 | 48h | 30 |

**调用流程：**
```python
result = await pipeline.ingest(signal)       # 单个信号
result = await pipeline.ingest_batch(signals) # 批量信号
result = await pipeline.tick()               # 周期检查
result = await pipeline.flush(layers=...)    # 强制刷新
```

**可优化点：**
- 未实现信号去重
- 没有优先级队列
- PORTRAIT触发时机可改为"检测到实质变化时"

---

### 6. 按层更新逻辑（LayerUpdaters）

**职责：** 实现每一层的具体更新策略

**位置：** `src/openbiliclaw/soul/layer_updaters.py`

| 层 | 更新方式 | 实现状态 |
|----|---------|---------|
| **SURFACE** | 纯计算（depth_preference） | 已实现 |
| **INTEREST** | LLM（PreferenceAnalyzer） | 已实现 |
| **ROLE** | LLM Delta（`_update_role`，基于 `build_role_delta_prompt`，处理信号证据并应用 diff-protection） | 已实现 |
| **VALUES** | LLM Delta（`_update_values`，add/remove 各最多 1 条/周期，注入完整画像上下文） | 已实现 |
| **CORE** | LLM Delta（`_update_core`，基于 `build_core_delta_prompt`，更新 traits/needs/MBTI，强 diff-protection） | 已实现 |

**输出格式（LayerUpdateResult）：**
```python
layer: OnionLayer
changed: bool
changes: list[str]         # 中文变化描述
signals_consumed: int
trigger: str               # 更新触发原因
evidence: str              # 佐证信息
timestamp: str             # ISO时间戳
```

**可优化点：**
- Surface 层仅调整 depth_preference 一个参数
- Role/Values/Core 已有完整 LLM Delta 实现，但各层触发阈值较高（CORE 需 8+ 信号、48h 间隔），初期数据稀疏时更新频率有限

---

### 7. 猜测兴趣系统（InterestSpeculator）

**职责：** 主动生成用户可能感兴趣但未接触的领域，通过事件匹配验证和晋升

**位置：** `src/openbiliclaw/soul/speculator.py`

**生命周期：**
```
Generate(LLM) -> Active -> Observe(关键字匹配) -> {Promote | Reject + Cooldown}
```

**数据模型 SpeculativeInterest：**
- `domain`, `category`, `reason`, `confidence`
- `ttl_days`, `confirmation_count`, `confirmation_threshold`(默认3)
- `status`: "active" | "promoted" | "rejected"

**TTL 说明：** `SpeculativeInterest` 数据类的 `ttl_days` 字段默认值为 14（反序列化旧数据时的兜底值），但 `InterestSpeculator` 类在创建新猜测兴趣时会使用配置中的 `default_ttl_days`（默认 3 天）。实际运行中，新产生的猜测兴趣 TTL 均为 3 天（来自配置 `scheduler.speculation_ttl_days`），14 天仅作为反序列化无 `ttl_days` 字段的历史数据时的向后兼容默认值。

**可优化点：**
- 关键字匹配无语义理解，易误匹配或漏匹配
- 确认阈值和冷却期固定

---

### 8. 提示词构建（Prompts）

**职责：** 统一管理所有LLM任务的提示词模板

**位置：** `src/openbiliclaw/llm/prompts.py`

**关键函数及输出 schema：**

| 函数 | 用途 | 输出 |
|------|------|------|
| `build_preference_analysis_prompt` | 偏好提取 | interests, style, context, disliked_topics, favorite_up_users, cognitive_style |
| `build_soul_profile_prompt` | 初始画像 | personality_portrait, core_traits, values, life_stage, deep_needs, mbti |
| `build_speculation_generation_prompt` | 猜测兴趣 | speculations: [{domain, category, reason, confidence}] |
| `build_awareness_prompt` | 日观察笔记 | [{date, observation, trend, emotion_guess}] |
| `build_insight_prompt` | 假设生成 | [{hypothesis, evidence, confidence}] |
| `build_recommendation_expression_prompt` | 推荐表达 | {expression, topic_label} |
| `build_content_evaluation_prompt` | 内容评估 | {score, reason} |
| `build_socratic_dialogue_prompt` | 对话生成 | 多轮消息 |
| `build_explore_domains_prompt` | 跨域探索 | [{domain, novelty_level, why_it_might_resonate}] |

---

### 9. 记忆管理（MemoryManager）

**职责：** 协调五层记忆网络和四种记忆类型

**位置：** `src/openbiliclaw/memory/manager.py`

**五层记忆：** Event(SQLite) -> Preference(JSON) -> Awareness(JSON) -> Insight(JSON) -> Soul(JSON)

**四种记忆类型：**
- **核心记忆：** Soul总结 + Preference摘要，始终注入LLM上下文
- **情节记忆：** 特定交互事件，支持会话级回放
- **语义记忆：** 用户事实性知识，支持知识查询
- **工作记忆：** 当前会话状态，仅内存

**主要接口：**
```python
layer = memory.get_layer(name)       # "soul", "preference", "awareness", "insight", "event"
core_mem = memory.get_core_memory()  # soul_summary + preference_summary
prompt = memory.render_core_memory_prompt()
memory.sync_profile_files(profile)   # 输出 soul_profile.json + .md
```

---

## 数据流全景图

```
用户行为事件 (view/like/skip/search)
         |
         v
ProfileUpdatePipeline.ingest()
  1. classify_signal() --- 按信号类型分类到目标层
  2. LayerBuffer       --- 按层缓冲
  3. is_ready()        --- 阈值检查
         |
    +----+----+
    v         v
SURFACE    INTEREST -----> PreferenceAnalyzer (LLM)
(纯计算)   (LLM分析)         |
    |         |              v
    +----+----+     populate_from_flat_preference()
         |                   |
         v                   v
  ROLE/VALUES/CORE     OnionProfile 更新
  (LLM Delta, 已实现)        |
         |                   v
         +----> PORTRAIT 触发 (Core/Values 变化时)
                     |
                     v
              MemoryManager 保存
              soul.json + soul_profile.md

---- 侧路：Speculator ----
observe(events) -> tick(generate LLM) -> promote_ready() -> interest.likes
```

---

## 已知瓶颈（评估数据）

| 字段 | 当前分数 | 根因 |
|------|---------|------|
| interest.dislikes | 0.00 | 模拟事件缺少负面信号（已修复事件生成 prompt） |
| interest.favorite_up_users | 0.00-0.19 | 模拟事件缺少 up_name（已修复）；LLM 幻觉 UP 主名 |
| surface.cognitive_style | 0.00 | prompt 输出格式与评估期望不对齐（已优化） |
| role.life_stage | 0.35 | ROLE 层已有完整 LLM 更新器，但需积累 5+ 信号且间隔 24h 才触发；证据不足时置信度偏低属预期行为 |
| values.motivational_drivers | 0.08 | VALUES 层已有完整 LLM 更新器，但每周期最多 add/remove 1 条，需充足证据才会更新；初期数据稀疏时分数偏低属预期行为 |
| core.mbti | 低 | 空规则压制 + ground truth 不一致 |
