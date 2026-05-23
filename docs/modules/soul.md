# 灵魂引擎

> 用户深度理解核心 — 从行为数据到人格画像的推理引擎。

## 概述

`soul/` 包实现了用户理解的核心逻辑，包括：

- **SoulEngine** — 编排器，从事件出发驱动各层分析
- **PreferenceAnalyzer** — LLM 驱动的偏好提取和合并
- **AwarenessAnalyzer** — 基于近期事件生成结构化觉察笔记
- **InsightAnalyzer** — 基于觉察、偏好和画像生成洞察假设
- **DialogueInsightAnalyzer** — 从聊天中提取候选长期理解信号
- **ToneProfile** — 从画像、偏好和近期反馈推断语气风格，用于推荐、画像总结和对话
- **SocraticDialogue** — 苏格拉底式用户对话，通过追问深化理解
- **SoulProfile** — 用户灵魂画像数据结构

## 已实现功能

| 任务 | 状态 | 说明 |
|------|------|------|
| OnionProfile 五层重构 | ✅ | 将 SoulProfile 重构为五层洋葱模型（CoreLayer → ValuesLayer → InterestLayer → RoleLayer → SurfaceLayer） |
| MBTI 人格类型 | ✅ | Core 层新增 MBTI 类型与维度强度（E/I, S/N, T/F, J/P），支持置信度标注 |
| 树形兴趣结构 | ✅ | InterestLayer 改为领域树结构 (domain → specifics)，支持”国际时事 → 中东局势 / 欧洲政治”的多层级兴趣 |
| 双存储（JSON + Markdown） | ✅ | soul_profile.json 存储结构化数据，soul_profile.md 提供人类可读镜像 |
| 画像变更日志 | ✅ | 新增 soul_changelog.md 记录每次画像更新的时间、来源、变化摘要和影响 |
| 向后兼容垫片属性 | ✅ | OnionProfile 提供 `core_traits / deep_needs / cognitive_style / motivational_drivers / values` 等垫片属性，兼容旧代码渐进迁移 |
| 自动格式迁移 | ✅ | `from_legacy()` 支持将 v1 flat SoulProfile 自动迁移到 v2 OnionProfile，SoulEngine 透明处理版本升级 |
| SoulEngine.analyze_events() | ✅ | 事件 → PreferenceAnalyzer → 偏好层更新 |
| SoulEngine module overrides | ✅ | 构造时可接收 `module_overrides` 并注入内部 `LLMService`，确保 preference / awareness / insight / profile_builder / speculator / dialogue_insight 都遵循 `[llm.soul]` 路由 |
| PreferenceAnalyzer | ✅ | LLM structured extraction + 合并 + 衰减；v0.3.x `satisfaction_filter_enabled=True` 默认开启，构 prompt 前会丢掉 `quick_exit` 等被动 negative 事件，保留 positive + neutral + unknown / NULL；显式 `dislike` / `thumbs_down` 负反馈会保留为 disliked_topics / 风格避让证据；偏好分析调用前有 prompt 预算保护，超长 chunk 会递归二分，单条超长事件会 compact，`n_keep >= n_ctx` / `context length` 等上下文错误会用更小 chunk 重试 |
| filter_events_by_satisfaction | ✅ | `soul/event_filters.py` 中的纯函数，按 `inferred_satisfaction` 过滤事件，`"unknown"` 同时匹配缺失 / `None`，使 pre-migration 老行可被显式 opt-in 保留 |
| recent_negative_exemplars | ✅ | `soul/negative_exemplars.py` 中的纯函数，从事件层拉最近 negative 标题做 recency 加权（半衰期默认 14d）+ 前缀去重 + 80 字截断，最多返回 8 条 `{title, reason, age_days}`。下游消费者是 `discovery/engine.ContentDiscoveryEngine._evaluate_batch` 和 `recommendation/engine.RecommendationEngine._classify_batch`，二者都会把列表作为 `negative_examples` 透传给 batch evaluator prompt——这是 [inferred_satisfaction 信号](#) 的第二个消费方（第一个是上面的 `filter_events_by_satisfaction`） |
| SocraticDialogue.respond() | ✅ | 通过 LLMService 调用 LLM，自动注入画像 |
| ProfileBuilder | ✅ | 结构化 prompt + JSON 校验 + `OnionProfile` 构建 |
| SoulEngine.build_initial_profile() | ✅ | 从 history + preference 生成并持久化 `soul.json` |
| SoulEngine.get_profile() | ✅ | 从 soul 层读取画像，未初始化时抛明确异常 |
| AwarenessAnalyzer | ✅ | 近期事件 → `AwarenessNote` 列表，支持同日去重；解析 LLM 响应时复用 `llm.json_utils.extract_llm_json_list()`，兼容 `results/items/notes/data/observations/recent_observations/latest/latest_observations` 等 object-wrapped array、reasoning 模型 bare singular-note dict、wrapper-key 下单 note、fenced JSON、JSONL 和 MiMo malformed `{ [ ... ] }`；prompt 按画像 → 偏好 → 近期事件排序以保留缓存前缀，并把近期 `dislike` / `thumbs_down` / negative 事件视为“最近开始避开 X”的保守观察信号 |
| InsightAnalyzer | ✅ | 觉察 + 偏好 + 画像 → `InsightHypothesis` 列表，支持假设合并；解析 LLM 响应时复用共享 JSON helper，能兼容 object wrapper、schema echo 后最终结果和 MiMo malformed array root |
| CognitionCycle | ✅ | 半日节流生成 awareness + insight 并同步到 `OnionProfile`；仅在 preference 与 soul 都为空的早期初始化状态跳过，已有任一层时仍会运行，避免已初始化画像因 preference 暂空而长期不产出觉察；awareness 失败时单次重试（间隔 2s），仍失败则记 WARNING 且**不推进** `last_awareness_at`，下一 tick 立即重试而不是空等 12h |
| SoulEngine.generate_awareness_note() | ✅ | 生成并持久化 `awareness.json` |
| SoulEngine.generate_insight() | ✅ | 生成并持久化 `insight.json` |
| SoulEngine.update_from_feedback() | ✅ | feedback 事件落库，并更新匹配洞察状态 |
| SoulEngine.process_feedback_batch_if_needed() | ✅ | 达到反馈阈值后重分析偏好，并在变化明显时重建画像 |
| SoulEngine.record_immediate_feedback_cognition() | ✅ | 单条 `dislike/comment` 可即时写入结构化 cognition card，供插件画像页展示；评论类更新会带上对应内容标题，避免脱离上下文 |
| DialogueInsightAnalyzer | ✅ | 从聊天轮次提取 `goal/value/interest/dislike/state` 候选信号 |
| SoulEngine.learn_from_dialogue() | ✅ | 聊天落 `dialogue` 事件、累计 insight candidate；单条 `interest/value/goal/dislike` 聊天信号到中高置信度时会先写入轻量 cognition update，达阈值后再驱动偏好/画像更新 |
| 兴趣探针聊天情绪判断 | ✅ | `/api/interest-probes/respond` 的 chat 分支会先让对话引擎回复，再用非 JSON 的单词分类 LLM 调用判断 `positive / negative / neutral`，失败时回退关键词，避免把标量分类请求错误发送成 `json_object` |
| 账户同步事件分析 | ✅ | 后台低频同步导入的 `view/favorite/follow` 事件会复用 `analyze_events()` 进入偏好与画像链 |
| 小红书初始化画像信号 | ✅ | `openbiliclaw init` 会把插件解析到的小红书 `saved/liked/xhs_history` 转成 `favorite/like/view` 事件，并与 B 站历史、收藏、关注一起进入 `analyze_events()` 和初始画像 history |
| 抖音初始化画像信号 | ✅ | `openbiliclaw init --yes-douyin` 会把插件解析到的抖音 `dy_post/dy_collect/dy_like/dy_follow` 转成 `view/favorite/like/follow` 事件，并进入偏好分析和初始画像 history |
| 小红书 / 抖音增量画像事件 | ✅ | profile 已存在时，`/api/sources/xhs/task-result` 和 `/api/sources/dy/task-result` 的 bootstrap 新增事件会在落 memory 后进入 `ProfileUpdatePipeline`，参与后续分层画像更新 |
| ToneProfile | ✅ | 从 `OnionProfile`、偏好摘要和近期反馈推断 `density/warmth/playfulness/directness`，统一驱动推荐、画像和聊天语气 |
| Cognition updates | ✅ | 在反馈刷新和聊天学习后生成 `interest_added / dislike_added / profile_shift` 结构化 cognition card，包含 `summary / context_line / source_label / expand_hint / impact / reasoning / evidence / source / created_at`，供插件提醒与画像页展开展示；即时反馈和聊天会尽量指出具体内容或本轮聊天，聚合判断则保守回退到”基于最近几条相关内容” |
| Layered profile cognition | ✅ | `OnionProfile` 新增 MBTI / Values / Interest 等分层，画像生成会同时消费 `history + preference + awareness + insights`，避免把兴趣 topic 堆成整段画像 |
| 猜测兴趣系统 | ✅ | `InterestSpeculator` 定期通过 LLM 过采样生成猜测兴趣方向，并按 `[scheduler]` 的 generation interval、TTL、cooldown、确认阈值和上限运行；通过事件确认后转正为正式兴趣，未确认则拒绝并冷却 |
| ROLE/VALUES/CORE 增量更新器 | ✅ | `_update_role`（`build_role_delta_prompt`，基于信号证据 + LLM diff-protection）、`_update_values`（LLM delta，每周期最多 add/remove 1 条，注入完整画像上下文）、`_update_core`（`build_core_delta_prompt`，更新 traits/needs/MBTI，强 diff-protection）均已完整实现 |
| v0.3.74 Soul 结构化 JSON 容错统一 | ✅ | ProfileBuilder、PreferenceAnalyzer、DialogueInsightAnalyzer、AwarenessAnalyzer、InsightAnalyzer、LayerUpdaters 和 InterestSpeculator 都收敛到 `llm.json_utils`，每个任务用 predicate 约束自己需要的 schema；MiMo / 非 OpenAI wrapper 不再只修 awareness 一处 |

## 猜测兴趣系统 (Speculative Interest Lifecycle)

系统会主动探索用户可能感兴趣但尚未接触的领域。通过心理学桥接推理，从已有兴趣模式中推断新方向。

### 生命周期

```
生成 (Generate) — LLM 根据画像猜测 3-5 个新方向（每 10min / init / 启动时）
    ↓  受活跃猜测数上限限制，到达上限则跳过
活跃 (Active) — 每次事件 ingest 做关键词匹配观测
    ├→ confirmation_count >= threshold → 转正 (Promote)
    │    创建 InterestDomain(source="speculated", weight=0.3)
    │    合并入 OnionProfile.interest.likes
    └→ TTL 到期未确认 → 拒绝 (Reject)
         加入冷却列表 (cooldown_days=7)
         冷却期间不再猜测该方向
```

### 数据结构

- **SpeculativeInterest**: domain, category, reason(心理学桥接), experience_mode, entry_load, confidence, ttl_days, confirmation_count/threshold, status
- **CooldownEntry**: 被拒绝的方向 + 冷却到期时间
- **SpeculativeState**: 活跃猜测 + 冷却列表，存储在 `data/memory/speculative_state.json`

### 两个猜测来源

1. **周期性生成**（默认每 10min）：专用 prompt `build_speculation_generation_prompt()` 深度推理，并额外标注 `experience_mode` / `entry_load`。Init 和进程启动时强制触发一次
2. **偏好分析附带**：`PreferenceAnalyzer` 每次分析事件时产出 `speculative_interests`，作为种子注入

### Active Pool 多样性

- generation 不再把 LLM 返回的前几条候选直接塞进 active pool，而是先过一层本地 balanced selector
- selector 会把既有 active pool 也作为选择上下文，优先补缺失的 `experience_mode` / `entry_load`，再按 confidence / weight 补齐剩余槽位
- 当模型没有提供足够丰富的候选时，会自动降级回普通排序，不阻塞 speculative 生成

### Probe Novelty Guard

- LLM 生成候选和 `PreferenceAnalyzer` seed 注入都会经过 `ProbeNoveltyGuard`
- guard 会收集画像 `interest.likes[*].domain`、画像 `specifics[*].name`、active speculation、cooldown speculation、近期 probe history 和显式负向 probe feedback
- 第一版使用规范化字符串和中文 bigram overlap 做本地判重，不引入 embedding 成本
- 与已有画像 domain / specific、active / cooldown、近期 `probed_domains`、`probe_feedback_history` 中 reject / chat_negative 记录明显重复的候选会被丢弃；候选 specifics 若部分重复，会先移除重复细项，剩余不足 2 条时丢弃候选

### 配置项

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `scheduler.speculation_interval_minutes` | 10 | 生成间隔（分钟） |
| `scheduler.speculation_ttl_days` | 3 | 猜测存活期。注意：`SpeculativeInterest` 数据类本身的 `ttl_days` 字段默认值为 14，仅作为反序列化不含该字段的历史数据时的兜底值；实际新产生的猜测兴趣均使用此配置项的 3 天 |
| `scheduler.speculation_cooldown_days` | 7 | 拒绝后冷却期 |
| `scheduler.speculation_confirmation_threshold` | 3 | 转正所需确认数 |
| `scheduler.speculation_max_active` | 5 | 最大活跃猜测数 |
| `scheduler.speculation_max_primary_interests` | 15 | 活跃猜测一级上限；不再把已确认兴趣计入，避免画像丰富后探针系统永久停摆 |
| `scheduler.speculation_max_secondary_interests` | 60 | 活跃猜测二级上限；不再把已确认细项计入，避免画像丰富后探针系统永久停摆 |
| `scheduler.speculator_idle_interval_minutes` | 30 | `ProfileUpdatePipeline` 空闲时检查猜测兴趣生命周期的间隔；`speculation_interval_minutes` 仍作为 speculator 内部生成间隔 gate |

### 触发时机

| 场景 | 方法 | 说明 |
|------|------|------|
| 定时 | `tick()` via Pipeline | 空闲 pipeline 默认每 30min 检查一次猜测兴趣生命周期；真正生成新猜测还受 `speculation_interval_minutes` 默认 10min gate 和兴趣上限约束 |
| Init | `force_tick()` via `build_initial_profile()` | 画像初始化后立即生成猜测 |
| 进程启动 | `force_tick()` via `startup_refresh_loop()` | API 启动时确保有活跃猜测 |
| 偏好分析 | `ingest_seeds()` via `_update_interest()` | PreferenceAnalyzer 附带的推测兴趣注入 |

`force_tick()` 忽略间隔计时器，但仍尊重活跃猜测上限和 `max_active`。

### 兴趣上限机制

当活跃猜测达到上限时，跳过生成。已确认兴趣不再计入生成上限，否则画像越丰富越容易让探针系统永久停摆：

| 级别 | 计算方式 | 上限 |
|------|---------|------|
| 一级 | 活跃猜测数 | 15 |
| 二级 | 活跃猜测数 | 60 |

### Pipeline 集成

- `ingest_batch()` 时调用 `speculator.observe()` 做轻量级关键词匹配
- `tick()` 时调用 `speculator.tick()` 处理过期/转正/生成
- 转正后自动创建 `InterestDomain` 并记录 changelog

### Discovery 集成

- `SoulEngine.get_profile()` 自动将活跃猜测附加到 `profile._active_speculations`
- `build_profile_summary()` 读取 `_active_speculations` 并包含在画像摘要中
- `SearchStrategy` / `ExploreStrategy` / `TrendingStrategy` 均可在 LLM prompt 中看到猜测兴趣

### API 集成

- `GET /api/profile` 返回 `speculative_interests` 字段（`SpeculativeInterestOut` 列表）
- 从 `speculative_state.json` 直接加载，最多返回 6 条活跃猜测

### Probe 选择

- runtime push 和 OpenClaw `get_next_probe()` 共用同一套 probe selection 规则
- `confirmation_count` 仍然是第一优先级；当验证压力相同，会优先选择最近没推过的 `experience_mode + entry_load` 组合
- probe 去重状态写入并持久化到 `discovery_runtime_state["probed_domains"]` 和 `discovery_runtime_state["probed_axes"]`
- `/api/interest-probes/respond` 会把 confirm / reject / chat sentiment 写入 `discovery_runtime_state["probe_feedback_history"]`；chat sentiment 是 `positive / negative / neutral` 标量判断，走普通文本 LLM 调用而不是 structured JSON 模式，失败时使用关键词兜底；后续生成会降低 reject / chat_negative 体验轴的入池优先级，选择会跳过明显重复的 domain，并在同等压力下避开负向反馈过的体验轴
- runtime push 与 OpenClaw `get_next_probe()` 成功选择后都会记录本次 domain / axis，连续调用不会重复返回同一条 active probe

### 关键文件

- `src/openbiliclaw/soul/speculator.py` — 核心引擎（生成/观测/转正/过期/force_tick）
- `src/openbiliclaw/llm/prompts.py` — `build_speculation_generation_prompt()`
- `tests/test_speculator.py` — speculative lifecycle / novelty / probe selection 单元测试

## 画像更新逻辑详解

当前实现里，“画像更新”不是一次单点写文件，而是一条分层链路：

`事件/Event` → `偏好/Preference` → `觉察/Awareness` → `洞察/Insight` → `画像/SoulProfile`

但这条链路并不是每次都从底层一路跑到顶层。系统会根据信号类型、强度和累计程度，决定这次更新只停在偏好层，还是继续推进到 `SoulProfile` 重建。

### 如果只看最终 `SoulProfile` 本身，可以把它读成 3 个层次

很多人会把“画像”理解成一段自然语言描述，但当前 `SoulProfile` 实际上至少包含 3 层信息：

1. **总述层**
   这是最像“人物小传”的部分，回答“这个人大致是什么样的人”。
   主要字段：
   - `personality_portrait`
   - `core_traits`

2. **解释层**
   这是画像真正变得立体的部分，回答“他是怎么理解世界的、在被什么驱动、最近处于什么阶段”。
   主要字段：
   - `cognitive_style`
   - `motivational_drivers`
   - `current_phase`
   - `values`
   - `life_stage`
   - `deep_needs`

3. **上下文层**
   这层不是为了给用户直接读“人格总结”，而是为了让后续 LLM 和产品逻辑知道这个画像最近是基于什么上下文形成的。
   主要字段：
   - `preferences`
   - `recent_awareness`
   - `active_insights`

可以把它理解成：

- **总述层**：你是谁
- **解释层**：你为什么会这样
- **上下文层**：最近哪些证据在支撑这个判断

### 一个简单例子

如果系统最近对你的理解是“你不满足于知道结果，更想把结构看明白”，那么在 `SoulProfile` 里可能会长成这样：

- `personality_portrait`
  “这是一个会主动追问复杂问题底层逻辑的人，不太满足于结论本身，更在意因果链和结构感。”
- `core_traits`
  `["理性", "重结构", "谨慎"]`
- `cognitive_style`
  `["会先找框架", "喜欢把问题讲透", "对证据比较敏感"]`
- `motivational_drivers`
  `["建立判断确定性", "持续扩展理解边界"]`
- `current_phase`
  “最近更像在一边吸收高密度信息，一边整理自己的判断框架。”
- `preferences.top_interests`
  `国际时事 / 历史 / 纪录片`
- `recent_awareness`
  “最近连续浏览高信息密度国际议题内容”
- `active_insights`
  “用户可能在通过深度内容建立更稳定的判断框架”

所以最终画像并不只是那段 `personality_portrait`，而是一整组“总述 + 解释 + 上下文”的组合。

### 先说结论：哪些东西会真的影响画像

当前会进入画像更新链路的主要有 4 类信号：

- **行为事件**：`view / search / favorite / like / follow` 等，通常先更新偏好层
- **推荐反馈**：`like / dislike / comment`，会先记事件，再按批量阈值决定是否重分析偏好和重建画像
- **聊天信号**：用户在对话里明确表达的 `interest / dislike / goal / value / state`
- **人工生成的中间理解**：`awareness` 和 `insight` 不直接改偏好，但会在画像重建时作为输入材料参与描述

真正持久化到“你是谁”的，是 `soul.json`；但驱动它变化的，不只是 `soul/` 自己，还包括 `memory/` 中的事件、反馈状态、聊天候选和认知更新文件。

### 1. 初始化画像：第一次把人“立起来”

首次初始化时，走的是 `SoulEngine.build_initial_profile(history)`：

1. 先读取已有 `preference` 层。
2. `openbiliclaw init` 已经先把 B 站历史 / 收藏 / 关注，以及显式启用的小红书 / 抖音 bootstrap signals 汇总成事件批次，调用 `analyze_events()` 更新偏好层。
3. 再加载历史 `awareness_notes` 和 `active_insights`。
4. `ProfileBuilder.build()` 把 `history_summary + preference_summary + awareness + insights` 一起送给 LLM。
5. LLM 返回结构化 JSON，必须包含：
   - `personality_portrait`
   - `core_traits`
   - `cognitive_style`
   - `motivational_drivers`
   - `current_phase`
   - `values`
   - `life_stage`
   - `deep_needs`
6. `ProfileBuilder` 校验字段完整性和画像长度，成功后才写入 `soul.json`。

小红书 bootstrap signals 的来源是浏览器插件在小红书页面中解析出的 notes，不是后端爬虫，也不是 Chrome 浏览器历史。scope 映射为：

| 小红书 scope | 事件类型 | 用途 |
|-------------|----------|------|
| `saved` | `favorite` | 高强度收藏/想回看信号 |
| `liked` | `like` | 中高强度偏好信号 |
| `xhs_history` | `view` | 小红书页面明确暴露时的浏览/足迹 state，强度较弱；普通推荐流不计入 |

抖音 bootstrap signals 的来源是浏览器插件在抖音页面中解析出的 videos / creators，不是后端爬虫，也不读取 Chrome 浏览器历史。scope 映射为：

| 抖音 scope | 事件类型 | 用途 |
|-----------|----------|------|
| `dy_post` | `view` | 用户自己发布内容，作为弱口味信号 |
| `dy_collect` | `favorite` | 收藏/想回看信号，强度最高 |
| `dy_like` | `like` | 中高强度偏好信号 |
| `dy_follow` | `follow` | 对创作者长期内容的兴趣信号 |

这里有两个重要约束：

- `personality_portrait` prompt 目标为 150-260 字，后端校验容忍 120-500 字；超出范围认为画像无效
- 如果 LLM 返回坏 JSON 或空内容，旧画像不会被覆盖；初始化大批量 history 触发风控 / 坏 JSON 时会移除原始标题和 context，用结构化偏好、来源分布、觉察和洞察重试一次
- 辅助字段（如 `motivational_drivers`、`values`、`deep_needs`）缺失或轻微格式不符时会补空值并记录 warning，避免真实 provider 少吐一个列表字段导致首次初始化失败

所以初始化不是“随便生成一段描述”，而是一次严格结构化的建档。

### 2. 行为事件路径：大多数变化先停在偏好层

日常行为事件先由 `MemoryManager.propagate_event()` 写入 SQLite 事件层。它当前只负责**落事实**，不会自动一路向上刷新五层。

真正的偏好更新由 `SoulEngine.analyze_events(events)` 触发：

1. 读取当前 `preference` 层。
2. 调用 `PreferenceAnalyzer.analyze_events()`。
3. 里面会用 `build_preference_analysis_prompt()` 把：
   - 本批 `events`
   - `existing_preference`
   一起发给 LLM，提取结构化偏好。
4. 返回结果会进入 `merge_preferences()`，与旧偏好合并。
5. 合并后的偏好写回 `preference.json`。

初始化这类大批量事件会按分片并发分析。偏好分析还会在每次 LLM 调用前检查 prompt 体积：`event_chunk_size` 只是第一层按条数粗分片；如果某个 chunk 的 `system_instruction + user_input` 超过本地保守预算，`PreferenceAnalyzer` 会继续递归二分该 chunk。若单条事件本身过长，会只保留 `event_type / title / context / inferred_satisfaction / satisfaction_reason` 和 `metadata.source_platform / up_name / bvid / feedback_type` 等偏好提取关键字段，截断长文本并丢弃 `raw_context`、字幕、评论、原始 payload 等大字段。compact 后仍超预算的单条事件会被跳过并记录 warning，其他事件继续参与合并。

若某个分片被 LLM 风控拒绝或返回非 JSON，`PreferenceAnalyzer` 仍会递归拆小该分片；最终只有仍失败的单条事件会被跳过。若 provider 返回明确的 context-window 错误（例如 `n_keep >= n_ctx`、`context length`、`prompt is too long`），偏好分析会按同一套拆分 / compact 逻辑重试；认证、网络、限流、模型不存在等非上下文错误仍会让调用失败，避免把服务不可用伪装成成功。

`satisfaction_filter_enabled` 默认开启后，偏好分析会先把 `quick_exit` 等被动 negative 事件从 prompt 中移除，避免误把标题党点击学成兴趣。显式负反馈不走这条丢弃路径：`feedback_type=dislike` 或 `reaction=thumbs_down` 会保留在 prompt 里，但只能贡献 `disliked_topics`、风格避让或置信度下调，不能贡献正向 `interests` / `favorite_up_users`。

这一层真正做的不是“生成画像”，而是把近期行为压缩成结构化偏好状态，例如：

- `interests`
- `style.preferred_duration / depth_preference / humor_preference`
- `context.session_type`
- `exploration_openness`
- `disliked_topics`
- `favorite_up_users`

#### 偏好层合并规则

`PreferenceAnalyzer.merge_preferences()` 当前有几条很具体的规则：

- 兴趣按 `(name, category)` 作为唯一键合并
- 老兴趣会先做时间衰减：`weight × 0.9^weeks`
- 衰减后若低于 `0.05`，该兴趣会被丢弃
- 同名兴趣再次出现时：
  - `first_seen` 保留最早值
  - `last_seen` 更新到现在
  - `weight` 取旧值和新值的较大者
- `favorite_up_users` 和 `disliked_topics` 走集合并集，不会丢历史值
- `style/context` 先继承默认值，再叠加旧状态，再叠加新状态

这意味着行为事件对画像的第一影响，通常不是直接改 `personality_portrait`，而是先慢慢把偏好层往一个更稳定的方向推。

小红书 / 抖音插件任务还有一条增量路径：当 `soul_engine.is_profile_ready()` 已经为真时，bootstrap task-result 新增的事件会先写入 memory，再通过 `signals_from_events()` 转成 `ProfileSignal` 进入 `ProfileUpdatePipeline.ingest_batch()`。首次 init 期间不会走这条增量更新，避免同一批初始化事件同时被 `analyze_events()` 和 pipeline 重复学习。

### 3. 推荐反馈路径：分成“即时记住”和“批量学习”两档

推荐反馈是当前画像更新里最细的一条链。它不是每点一次 `like/dislike` 都立刻重建画像，而是分成两层处理。

#### 第一层：即时认知更新，不重建画像

`record_immediate_feedback_cognition()` 处理的是单条强反馈，目的是让系统“先记住这件事”，但不马上改整张画像。

当前支持：

- `comment` 且有文字：写入一条 `profile_shift` 风格的 cognition card
- `dislike`：写入一条 `dislike_added`
- `like`：写入一条 `interest_added`

它会生成这些字段并写进 `cognition_updates.json`：

- `summary`
- `context_line`
- `impact`
- `reasoning`
- `evidence`
- `source = "feedback"`
- `source_label = "推荐反馈"`
- `confidence`

这条路径的特征是：

- 很快，适合 UI 立刻展示“阿B 刚记住了什么”
- 会去重，避免同一 summary 重复写
- **不会**直接触发偏好重分析
- **不会**直接重建 `SoulProfile`

所以单条反馈的主要作用，是先形成一条“认知变化记录”，而不是立刻把人格描述大改一遍。

#### 第二层：批量学习，必要时重建画像

真正会动到偏好层和画像的是 `process_feedback_batch_if_needed()`：

1. 读取 `feedback_state.json` 中的 `last_processed_feedback_event_id`
2. 从事件层找出这个游标之后的新 `feedback` 事件
3. 如果新增反馈少于 `3` 条，直接返回，不做重分析
4. 达到阈值后，调用 `PreferenceAnalyzer.analyze_events()` 用这批反馈重跑偏好提取
5. 偏好写回 `preference.json`
6. 再比较“这次偏好变化是否足够明显”
7. 如果明显，才调用 `ProfileBuilder.build()` 重建画像并写回 `soul.json`
8. 同时生成聚合层的 cognition updates
9. 最后更新 `feedback_state.json` 的游标和处理时间

#### 什么叫“变化明显”

当前 `_preference_changed_significantly()` 的判定很明确：

- 只看 `weight >= 0.6` 的高权重兴趣
- 如果旧偏好里没有高权重兴趣，而新偏好有，算明显变化
- 如果高权重兴趣集合的增删差异达到 `2` 个以上，算明显变化
- 如果同一个高权重兴趣的权重变化绝对值 `>= 0.2`，算明显变化
- 如果新增了至少 `1` 个 `disliked_topics`，算明显变化

只有满足这些条件，系统才会认为“这不是局部波动，而是值得重写画像的变化”。

### 4. 聊天学习路径：先记候选，再看是否够格进入长期画像

聊天信号的处理路径是 `learn_from_dialogue()`，它比反馈更保守，因为聊天里更容易出现一次性情绪或随口表达。

完整链路如下：

1. 先把这轮对话写成一条 `dialogue` 事件进事件层。
2. 调用 `DialogueInsightAnalyzer.extract()`。
3. LLM 从这轮对话里提取候选信号，限定在：
   - `interest`
   - `dislike`
   - `goal`
   - `value`
   - `state`
4. 每条候选都带：
   - `content`
   - `confidence`
   - `evidence`
5. 候选先和历史 `insight_candidates.json` 合并，不直接写进偏好层。

#### 候选如何合并

`_merge_insight_candidates()` 会按 `kind + content` 合并：

- 新候选首次出现时，创建一条记录
- 重复出现时：
  - `occurrences + 1`
  - `confidence` 取更高值
  - `evidence` 更新为最新非空值
  - `updated_at` 刷新

所以聊天学习不是“听见一次就信”，而是把聊天信号当作待确认的长期候选。

#### 哪些聊天候选会立刻出现在画像页上

有一条更轻的 UI 路径：`_record_immediate_dialogue_cognition()`。

如果候选满足即时展示条件，就会先生成一张 cognition card：

- `goal / dislike / interest / value` 要求 `confidence >= 0.8`
- `state` 更保守，要求 `confidence >= 0.9`

这一步只影响 `cognition_updates.json`，不等于正式改画像。

#### 哪些聊天候选会真正进入偏好层

要进入长期学习，候选必须满足 `_candidate_ready_for_learning()`：

- `applied == False`
- `confidence >= 0.8`
- `occurrences >= 2`

也就是说，**同一个方向至少重复出现两次，而且置信度足够高**，才会被转成一条 `dialogue_insight` 事件，再送进 `PreferenceAnalyzer.analyze_events()`。

之后的流程和反馈批量学习相同：

1. 用这些合格候选更新偏好层
2. 比较偏好是否显著变化
3. 只有显著变化时才重建 `SoulProfile`
4. 生成 cognition updates
5. 把这些候选标记为 `applied = True`

### 5. 觉察层与洞察层：不直接触发重建，但会影响下次画像重建长什么样

`generate_awareness_note()` 和 `generate_insight()` 本身不做“显著变化判定”，也不直接调用重建画像。

它们的作用更像是**给下一次画像重建准备解释材料**：

- `AwarenessAnalyzer` 从最近事件里生成保守的观察笔记
- `InsightAnalyzer` 从 `awareness + preference + soul_profile` 里生成解释性假设

这些结果分别写进：

- `awareness.json`
- `insight.json`

当下一次 `build_initial_profile()` 或后续重建画像时，`ProfileBuilder.build()` 会把：

- `history_summary`
- `preference_summary`
- `recent_awareness`
- `active_insights`

一起喂给 LLM。

所以可以把它们理解为：**觉察层和洞察层不是更新闸门，而是画像重建时的“叙述素材层”**。它们决定画像写得是否更像“这个人怎么理解世界”，而不是只像一堆兴趣标签。

### 6. 画像重建时，LLM 实际拿到什么

真正重建画像时，走的是 `ProfileBuilder.build()` + `build_soul_profile_prompt()`。

system prompt 的核心约束是：

- 只能根据给定材料推断
- 必须输出严格 JSON
- 人格描述目标 150-260 字，后端校验容忍 120-500 字
- 先写“怎么处理信息”，再写“长期在找什么”，最后写“最近处于什么阶段”
- 不要把兴趣 topic 堆成画像主体

输入则包括四块：

- `history_summary`
- `preference_summary`
- `recent_awareness`
- `active_insights`

这意味着当前画像重建不是只看最近 3 条反馈，也不是只看几句聊天，而是把：

- 长期历史
- 最近行为聚合出的偏好
- 近期观察
- 解释性假设

一起当作“重新描述这个人”的上下文。

### 7. 认知变化是怎么生成的

除了 `soul.json` 本身，系统还会生成一条独立的“你最近被记住了什么”的轨迹，这就是 `cognition_updates.json`。

聚合路径的 cognition update 由 `_build_cognition_updates()` 生成，主要有三类：

- `interest_added`
  触发条件：新出现的兴趣不在旧偏好里，且 `weight >= 0.75`
- `dislike_added`
  触发条件：新出现的 `disliked_topics` 不在旧偏好里
- `profile_shift`
  触发条件：`_profile_shifted(previous_profile, current_profile)` 为真，也就是画像文本或关键列表字段发生变化

这些 update 会附带：

- `summary`
- `context_line`
- `impact`
- `reasoning`
- `evidence`
- `source` / `source_label`
- `confidence`

这层的定位很重要：它不是替代画像，而是补一条“这次为什么变了”的可读解释，方便前端展示最近的认知变化。

### 8. 哪些文件会被更新

一次完整的“画像相关更新”可能涉及这些文件：

- `data/memory/preference.json`
  保存结构化偏好层
- `data/memory/soul.json`
  保存最终画像
- `data/memory/awareness.json`
  保存近期观察
- `data/memory/insight.json`
  保存解释性假设
- `data/memory/feedback_state.json`
  保存反馈批处理游标
- `data/memory/insight_candidates.json`
  保存聊天候选长期信号
- `data/memory/cognition_updates.json`
  保存“最近记住了什么”的结构化变化记录

这也说明：当前画像更新是一个“主数据 + 中间状态 + 可解释回显”并存的体系，不是单文件覆盖。

### 9. 一个完整例子：从一句话到画像变化

假设你最近连续发生这些事情：

1. 看了 3 条“国际局势深度解读”
2. 搜索了“国际新闻 因果链”
3. 聊天里说“我想把国际新闻背后的结构看明白”
4. 对一条“浅层热点复读”点了 `dislike`
5. 又在另一轮聊天里再次提到“我现在更想看讲透逻辑的内容”

系统大致会这样处理：

1. `view/search/dialogue/feedback` 先全部落入事件层。
2. `analyze_events()` 把观看和搜索提炼成偏好层，例如：
   - `国际局势`
   - `历史`
   - 更高的 `depth_preference`
3. 单次 `dislike` 先生成一条即时 cognition card，告诉你“这类内容被记成避雷方向了”。
4. 第一轮聊天会生成一个候选 `goal` 或 `interest`，但因为只出现一次，还不会正式写进偏好层。
5. 第二轮相似聊天出现后，候选的 `occurrences` 到了 2，且 `confidence >= 0.8`，于是进入长期学习。
6. 聊天候选和反馈批量一起推动偏好层出现显著变化，例如：
   - 高权重兴趣新增/强化
   - `disliked_topics` 新增了“浅层热点复读”
7. `_preference_changed_significantly()` 返回真，触发画像重建。
8. 重建时，LLM 会同时看到：
   - 历史标题摘要
   - 当前偏好层
   - 近期 awareness
   - active insights
9. 新 `soul.json` 可能不只是说“喜欢国际新闻”，而会写成：
   - “这个人会主动追问复杂事件背后的结构，更偏好能把因果链讲透的高信息密度内容”
10. 同时生成一条或多条 cognition updates，告诉前端：
   - 新兴趣更明确了
   - 新避雷方向出现了
   - 画像整体发生了一次可见转向

### 10. 当前实现的边界

为了避免画像抖动过快，当前实现刻意保守：

- `propagate_event()` 只落事件，不自动全链路刷新
- 单条反馈只做即时认知记录，不直接重建画像
- 聊天信号必须高置信且重复出现，才能进入长期学习
- 画像重建必须跨过“显著变化阈值”
- `awareness` 和 `insight` 会影响画像内容，但不会独立触发重建

换句话说，系统当前追求的是：**先把“你最近说了什么、做了什么”记稳，再在足够证据累计后，谨慎地改写“你是谁”**。

## 公开 API

### SoulEngine

```python
from openbiliclaw.soul.engine import SoulEngine
from openbiliclaw.llm.service import module_overrides_from_config

engine = SoulEngine(
    llm=registry,
    memory=memory_manager,
    module_overrides=module_overrides_from_config(config),
)

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

updates = memory_manager.load_cognition_updates()
# [
#   {
#     "kind": "interest_added",
#     "summary": "阿B 刚记下了你对《这视频讲透了中东局势》的评论。",
#     "context_line": "来自：《这视频讲透了中东局势》",
#     "impact": "画像里“喜欢高信息密度、有人文关怀的内容”这条偏好会更明确。",
#     "reasoning": "这次反馈不只是喜欢/不喜欢，而是主动说清了你在意的内容气质。",
#     "evidence": "你评论《这视频讲透了中东局势》时说：这个很好看，有创意，我很喜欢，还有一些不油腻的人文关怀",
#     "source": "feedback",
#     "source_label": "推荐反馈",
#     "expand_hint": "expandable",
#     "created_at": "2026-03-15T10:30:00",
#     "notified": False,
#     ...
#   }
# ]
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

analyzer = PreferenceAnalyzer(
    registry=llm_registry,
    max_prompt_chars=24_000,  # 默认值：发送 LLM 前的保守 prompt 字符预算
)
updated_pref = await analyzer.analyze_events(
    events=[...],
    existing_preference=current_pref,
    event_chunk_size=200,  # 可选：先按条数粗分片，再由 prompt 预算继续拆
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

### OnionProfile（五层洋葱模型）

```python
from openbiliclaw.soul.profile import (
    OnionProfile,
    CoreLayer,
    ValuesLayer,
    InterestLayer,
    RoleLayer,
    SurfaceLayer,
    MBTI,
    MBTIDimension,
)

# OnionProfile 包含五个内嵌层，从内到外：
# 1. CoreLayer - 最稳定的核心特质与深层需求
# 2. ValuesLayer - 价值观与内在驱动力
# 3. InterestLayer - 树形兴趣结构（domain → specifics）
# 4. RoleLayer - 生活阶段与当前处境
# 5. SurfaceLayer - 可观察的认知风格与内容偏好

profile = OnionProfile(
    core=CoreLayer(
        core_traits=["理性", "重结构"],
        deep_needs=["建立判断确定性"],
        mbti=MBTI(
            type="INTJ",
            dimensions={
                "EI": MBTIDimension(pole="I", strength=0.85),
                "SN": MBTIDimension(pole="N", strength=0.78),
                "TF": MBTIDimension(pole="T", strength=0.81),
                "JP": MBTIDimension(pole="J", strength=0.72),
            },
            confidence=0.72,
        ),
    ),
    values_layer=ValuesLayer(
        values=["理解本质", "逻辑严谨"],
        motivational_drivers=["追求确定性", "建立框架"],
    ),
    interest=InterestLayer(
        likes=[
            InterestDomain(
                domain="国际时事",
                weight=0.88,
                specifics=[
                    InterestSpecific(name="中东局势", weight=0.85),
                    InterestSpecific(name="欧洲政治", weight=0.80),
                    InterestSpecific(name="经济动向", weight=0.75),
                ],
            ),
            InterestDomain(
                domain="历史",
                weight=0.82,
                specifics=[
                    InterestSpecific(name="冷战历史", weight=0.80),
                ],
            ),
        ],
        dislikes=[
            InterestDomain(domain="浅层热点复读", weight=0.9),
            InterestDomain(domain="标题党", weight=0.85),
        ],
        favorite_up_users=["小约翰可汗", "不知所云"],
    ),
    role=RoleLayer(
        life_stage="职业早期，追求知识深度",
        current_phase="最近在系统地补齐国际事务背景知识",
    ),
    surface=SurfaceLayer(
        cognitive_style=[
            "会先找框架",
            "喜欢把问题讲透",
            "对证据比较敏感",
        ],
        exploration_openness=0.65,
    ),
    personality_portrait="这是一个会主动追问复杂问题底层逻辑的人...",
)

# 向后兼容垫片属性（支持旧代码渐进迁移）
assert profile.core_traits == profile.core.core_traits
assert profile.deep_needs == profile.core.deep_needs
assert profile.values == profile.values_layer.values
assert profile.motivational_drivers == profile.values_layer.motivational_drivers
assert profile.cognitive_style == profile.surface.cognitive_style
assert profile.life_stage == profile.role.life_stage
assert profile.current_phase == profile.role.current_phase

# 自动迁移：从旧版 SoulProfile (v1) 转换到新 OnionProfile (v2)
legacy_soul = SoulProfile.from_dict(old_v1_data)
onion = OnionProfile.from_legacy(legacy_soul)
assert onion.version == 2
assert onion.core_traits == legacy_soul.core_traits
```

### ProfileBuilder / OnionProfile 构建

```python
from openbiliclaw.soul.profile_builder import ProfileBuilder

builder = ProfileBuilder(registry=llm_registry)
profile = await builder.build(
    history=[
        {"title": "AI 工具实测", "author": "科技UP主"},
        {"title": "效率系统分享", "author": "知识UP主"},
    ],
    preference=current_pref,
    awareness_notes=[
        {
            "date": "2026-03-20",
            "observation": "最近更常停在高信息密度内容里。",
            "trend": "明显更偏向讲透结构而不是只看结论。",
        }
    ],
    active_insights=[
        {
            "hypothesis": "用户可能在通过深度内容建立判断确定性。",
            "confidence": 0.71,
        }
    ],
)
# 返回 OnionProfile，自动填充五层结构

assert 120 <= len(profile.personality_portrait) <= 500
assert len(profile.core_traits) >= 3
assert profile.core.mbti.type  # MBTI 现已包含
assert profile.values_layer.motivational_drivers
assert profile.role.current_phase
assert profile.interest.likes  # 树形兴趣结构
```

```python
profile = await engine.build_initial_profile(history=[...])
loaded = await engine.get_profile()
assert loaded.core.core_traits == profile.core.core_traits
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
# 兼容模型把数组包在 {"results": [...]} / {"items": [...]} 等对象里的 JSON mode 输出

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

### ToneProfile

```python
from openbiliclaw.soul.tone import build_tone_profile

tone = build_tone_profile(
    profile=current_profile,
    preference_summary=memory.get_core_memory()["preference_summary"],
    recent_feedback=[
        {"feedback_type": "dislike", "feedback_note": "太油了"},
        {"feedback_type": "dislike", "feedback_note": "话有点满"},
    ],
)
# {
#   "density": "dense",
#   "warmth": "companion",
#   "playfulness": "medium",
#   "directness": "soft",
# }
```

## 设计决策

1. **偏好提取用 json_mode**：确保 LLM 返回结构化 JSON，便于程序处理
2. **标量分类不用 json_mode**：兴趣探针聊天情绪只需要 `positive / negative / neutral` 单词，走普通文本调用；只有真正返回 JSON 的任务才启用 structured task
3. **对话错误优雅降级**：LLM 调用失败时返回友好中文提示，不崩溃
4. **`_build_service()` 回退**：未注入 LLMService 时从 SoulEngine 自动构建
5. **历史格式转换**：`agent` → `assistant` 角色映射，适配 OpenAI 消息格式
6. **画像生成独立为 `ProfileBuilder`**：避免把 prompt/JSON 校验逻辑塞进 `SoulEngine`
7. **认知变化解释由 soul 层生成**：`impact / reasoning / evidence` 都在后端认知链路里一次性产出，前端只负责展示，不在 UI 层脑补推理
8. **默认态上下文也由 soul 层负责**：`context_line / source_label / expand_hint` 由后端统一生成，保证“这是对哪条内容或哪组信号的判断”与详情口径一致
9. **评论型认知必须带内容上下文**：用户对“这条内容”的评论如果不带标题，认知卡片会失去可读性，因此即时反馈路径优先把标题写进 `summary`、`context_line` 和 `evidence`
10. **聚合判断宁可保守也不伪造对象**：拿不到可信标题时，回退为“基于最近几条相关内容”，避免看起来丰富但实际不准
11. **灵魂层失败不覆盖旧画像**：坏 JSON、空响应、缺字段时直接报错，已有 `soul.json` 保留
12. **觉察层保守去重**：同日 observation 标准化后相同则跳过，避免流水账堆积
13. **洞察层按假设文本合并**：相同 hypothesis 合并 evidence，confidence 取较高值
14. **验证状态只由代码更新**：LLM 只生成 hypothesis/evidence/confidence，`validated` 不信任模型输出
15. **反馈达到阈值后再学习**：默认累计 3 条新反馈才触发偏好重分析，避免单次噪声反馈频繁扰动画像
16. **画像重建走显著变化阈值**：只有高权重兴趣明显变化或新增 `disliked_topics` 时才重建 `SoulProfile`
17. **聊天信号受控生效**：聊天先落 `dialogue` 事件和 `insight_candidates.json`，只有高置信度且重复出现的候选才会进入偏好更新
18. **语气不单独持久化**：`ToneProfile` 是从画像、偏好和近期反馈实时推断出的派生层，避免把易调参的表达风格绑死在 `soul.json`
19. **“老B友”是基础人格，不是固定模板**：聊天、推荐和画像总结共用同一套语气维度，但会随着用户画像和近期反馈在信息密度、温度、梗感和直给程度上细调
20. **认知变化只在关键时刻生成**：只有新增高权重兴趣、明确避雷方向或画像明显转向时，才会形成 `cognition update`，避免把普通波动都做成提醒
21. **账户同步只补事件，不单独改画像**：history / favorites / following 统一先转成事件，再复用现有偏好分析与画像更新链，避免出现第二套理解逻辑
22. **画像先写“怎么理解世界”，再写“看了什么”**：`personality_portrait` 必须先围绕认知风格、驱动力和当前阶段组织，兴趣 topic 最多只作为少量证据出现，避免退化成偏好标签润色稿
