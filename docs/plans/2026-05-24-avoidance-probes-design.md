# 不喜欢领域探针设计

## 背景

当前项目已经有正向兴趣探针：系统主动猜测用户可能喜欢的新方向，通过 `interest.probe` 在移动端 Web、PC Web、插件和 OpenClaw 中询问用户，并根据 `confirm / reject / chat` 反馈更新画像。

缺口在负向边界：系统已经能从显式 `dislike`、`thumbs_down`、负向聊天和 `disliked_topics` 中学习避雷方向，但缺少主动确认机制。用户可能并不是讨厌某个大领域，而是讨厌其中某种低质形态、节奏、表达方式或内容姿态。需要一套与兴趣探针一致的“负向探针”，主动确认用户不喜欢什么。

## 目标

1. 新增“不喜欢领域探针”，主动探索用户可能想避开的方向。
2. 负向探针最多同时 active 5 条，不占正向兴趣探针的 active 5 条配额。
3. 确认方式与当前兴趣探针一致：`confirm / reject / chat`。
4. 移动端 Web、PC Web、插件、OpenClaw 都能看到并操作。
5. 未确认的负向探针不参与推荐过滤，只有用户确认后才写入避雷画像。
6. 尽量复用现有探针的状态机、去重、runtime 投递、聊天情绪判断和 UI 交互模式，但不高风险重构现有正向兴趣探针。

## 非目标

1. 不把当前正向 `InterestSpeculator` 立刻泛化为统一 polarity engine。
2. 不使用 embedding 做全语义历史去重，首版沿用现有本地字符串和中文 bigram guard。
3. 不把“忽略探针”视为负反馈。
4. 不让未确认的负向探针影响 discovery query、recommendation ranking 或 pool purge。

## 推荐方案

采用独立负向状态机，复用现有 probe 工具函数和交互模式。

新增 `AvoidanceSpeculator`，与 `InterestSpeculator` 并行运行。它拥有独立 active/cooldown 状态文件、独立 runtime history 和独立 API/event 类型。这样可以避免改动当前稳定的正向兴趣探针状态结构，同时让前端和 OpenClaw 能按同一套确认体验处理。

未来如果两套探针稳定后重复代码过多，再把公共生命周期逻辑抽成共享基类或 polarity-aware engine。

## 核心模型

新增模型建议：

```python
@dataclass
class SpeculativeAvoidance:
    domain: str
    category: str
    reason: str
    source_mode: str  # negative_signal | positive_boundary | style_boundary
    experience_mode: str
    entry_load: str
    confidence: float
    weight: float
    created_at: str
    ttl_days: int
    confirmation_count: int
    confirmation_threshold: int
    status: str  # active | confirmed | promoted | rejected
    confirming_events: list[str]
    specifics: list[SpeculativeSpecific]
```

状态文件：

- `memory/avoidance_state.json`

runtime state 新增字段：

- `probed_avoidance_domains`: `{normalized_domain: iso_timestamp}`
- `probed_avoidance_axes`: `{experience_mode|entry_load: iso_timestamp}`
- `avoidance_probe_feedback_history`: 最近 100 条显式负向探针反馈
- `last_probe_kind`: `"interest" | "avoidance" | ""`，用于主动推送循环在正向/负向探针都可用时轮流投递，避免同一轮同时打扰用户

默认配置：

- `avoidance_speculation_interval_minutes = 10`
- `avoidance_speculation_ttl_days = 3`
- `avoidance_speculation_cooldown_days = 7`
- `avoidance_speculation_confirmation_threshold = 3`
- `avoidance_speculation_max_active = 5`

## 候选来源

负向探针生成包含三类来源：

1. `negative_signal`
   - 从显式 `dislike`、`thumbs_down`、负向聊天、现有 `disliked_topics` 延展相邻避雷方向。
   - 每条候选必须能在 `reason` 中引用已有负向证据。

2. `positive_boundary`
   - 从正向兴趣反推边界。
   - 例：用户喜欢“高信息密度深度解释”，可确认是否避开“浅层热点复读”。
   - 不能直接把正向兴趣本身当成讨厌对象。

3. `style_boundary`
   - 从画像风格推测内容形态边界。
   - 例：质量敏感、节奏偏好、时长偏好、低容忍标题党等。
   - 只能生成内容形态、质量、节奏、表达方式层面的边界，不生成敏感人格判断。

## 反馈语义

负向探针沿用 `confirm / reject / chat`，但含义是负向语义：

- `confirm`: 用户确认“确实不喜欢 / 需要避开这个方向”
  - 以 `preference_layer.data["disliked_topics"]` 作为持久化源头：默认写有效 specifics；specifics 列表为空时才兜底写 domain，避免把“讨厌某个子形态”扩大成“讨厌整个领域”
  - 通过 `OnionProfile.populate_from_flat_preference(preference_layer.data)` 同步并保存 `soul` layer，让 `OnionProfile.interest.dislikes` 与 flat preference 保持一致
  - 触发 cognition update
  - 触发现有 dislike pool purge 和后续推荐避让
  - 从 active 移除并后台补位

- `reject`: 用户否认“不是，我并不排斥这个方向”
  - 写入 feedback history
  - 进入 cooldown
  - 不写入画像，不触发过滤

- `chat`: 多聊聊
  - 进入带负向探针上下文的对话
  - sentiment 分类改为避雷语义：
    - 用户表达“对，这类我不喜欢”则等价 confirm-like
    - 用户表达“不是，我其实可以看”则等价 reject-like
    - 中立则只记录观察，不改画像

## API 和事件

新增 API：

- `GET /api/avoidance-probes/pending`
- `POST /api/avoidance-probes/respond`
- `POST /api/avoidance-probes/trigger`

`respond` 请求：

```json
{
  "domain": "浅层热点复读",
  "response": "confirm",
  "message": ""
}
```

runtime 事件：

- `avoidance.probe`
- `avoidance.confirmed`
- `avoidance.rejected`
- `avoidance.chat`

`avoidance.probe` payload：

```json
{
  "type": "avoidance.probe",
  "phase": "ready",
  "message": "有一个可能的避雷方向想确认",
  "domain": "浅层热点复读",
  "category": "内容风格",
  "reason": "你最近对快餐式热点内容有负向反馈，同时更偏好因果链清晰的解释。",
  "confidence": 0.46,
  "weight": 0.46,
  "source_mode": "negative_signal",
  "experience_mode": "knowledge",
  "entry_load": "light",
  "specifics": ["标题党热点解读", "无信息增量复读", "情绪化站队剪辑"],
  "question": "我猜「浅层热点复读」可能是你想避开的方向，这个判断准吗？"
}
```

## 后端流程

生成流程：

1. Soul profile 初始化后触发 `AvoidanceSpeculator.force_tick()`。
2. Soul pipeline idle tick 或画像层更新后触发 `AvoidanceSpeculator.tick()`。
3. preference 分析产生新的 dislike 或相关 signals 后，可以作为 avoidance seed 注入。
4. 生成前做本地 quality gate 和 novelty guard。
5. active 数量达到 5 时停止生成。

Prompt builder 必须遵守项目的 prompt-cache 约定：system prompt 使用模块级静态常量，所有 profile / 状态 / count 等变量进入 user message，并加入现有 prompt invariant 测试。

观察 / 自动确认流程：

1. `AvoidanceSpeculator.observe()` 只消费显式负向证据：`feedback_type=dislike`、`reaction=thumbs_down`、`event_type=dislike` 或明确的负向 probe chat。`quick_exit` 等被动 negative 不计入，避免把误点/快速退出当成避雷确认。这个门槛有意严于 preference 层 dislike 抽取：preference 可以吸收弱负向信号，avoidance 自动确认必须只基于高确信证据。
2. 当显式负向事件文本匹配 active avoidance 的 domain 或 specifics 时，`confirmation_count += 1`。
3. 达到 `confirmation_threshold` 后进入 promote，但 `AvoidanceSpeculator` 保持无跨模块写入副作用：只更新自己的 `avoidance_state.json`，不直接修改 `disliked_topics` / `soul` layer / 候选池，只返回 `tick_result.promoted`。
4. `ProfileUpdatePipeline` 在 avoidance tick 后检查 `tick_result.promoted`，调用统一的 dislike writeback helper，写入 `disliked_topics`、同步 `soul` layer、执行候选池 purge。API confirm 也调用同一个 helper。
5. 用户点击 `confirm` 是强确认，会直接把该 avoidance 标记为 `confirmed` 并进入同一条 promote/writeback helper；因此 threshold 对自动确认有意义，对显式确认只是状态机兼容字段。

投递流程：

1. `ContinuousRefreshController._loop_proactive_push()` 继续作为主动推送入口。
2. 主动推送循环每轮最多投递一个 probe。正向 `interest.probe` 与负向 `avoidance.probe` 都有候选时，依据 `last_probe_kind` 轮流选择，避免新增负向探针后把用户每轮收到的 probe 数量翻倍。
3. 选择候选时避开近期 `probed_avoidance_domains` 和 `probed_avoidance_axes`。
4. 只有 runtime stream 实际投递成功后，才写入 `probed_avoidance_domains` / `probed_avoidance_axes`。

确认流程：

1. API 先记录 `avoidance_probe_feedback_history`。
2. `confirm` 将 probe 转为 confirmed，并调用统一的 dislike writeback helper 写入 flat preference 的 `disliked_topics`。写入 topic 采用上面的保守语义：specifics 优先，specifics 为空时才兜底写 domain。这是避雷画像的持久化源头；不要只 mutate `await soul_engine.get_profile()` 返回的快照。
3. helper 基于更新后的 flat preference 调用 `OnionProfile.populate_from_flat_preference()` 并保存 `soul` layer，让 `ProfileSummaryResponse.dislikes` 可见且不会在下次 profile rebuild 时丢失。
4. helper 触发 pool purge，清除已缓存的相关候选。
5. 发布 `avoidance.confirmed`，前端刷新画像和 cognition updates。
6. 后台 tick 补位，不阻塞响应。

## 去重和质量门

去重覆盖：

- 现有 `disliked_topics`
- `OnionProfile.interest.dislikes[*].domain`
- `OnionProfile.interest.dislikes[*].specifics[*].name`
- active avoidance probes
- cooldown avoidance probes
- 近期 `probed_avoidance_domains`
- `avoidance_probe_feedback_history` 中用户已否认的方向
- 正向 likes 的高权重 domain 与 specifics，避免直接问“你是不是讨厌你喜欢的东西”；这条必须在 `AvoidanceNoveltyGuard.from_profile_and_state()` 中硬过滤，不能只依赖 prompt

质量门：

- `domain` 不能为空，且不能与已有 dislike 完全重复。
- 每条必须有 2-4 个 `specifics`。
- 过滤重复 specifics 后少于 2 条则丢弃候选。
- `reason` 至少 20 个字符。
- `confidence` 默认允许范围 0.3-0.6，低于最小阈值丢弃。
- 每条必须有合法 `source_mode`。
- active pool 尽量覆盖不同 `source_mode`，避免 5 条都集中在同一种“标题党/浅层内容”。

## UI 设计

移动端 Web：

- 消息 overlay 同时展示 `interest.probe` 和 `avoidance.probe`。
- 负向探针按钮：
  - `确实不喜欢`
  - `不是`
  - `多聊聊`
- Profile 页在“推测性兴趣”旁新增“待确认避雷方向”。
- 多聊聊进入 `scope="avoidance_probe"` 的上下文聊天。

PC Web：

- desktop message inbox 支持 `avoidance.probe`。
- 画像页显示 active avoidance probes。
- runtime stream 收到 `avoidance.confirmed / avoidance.rejected / avoidance.chat` 后刷新 profile summary。

插件：

- popup 消息 inbox 支持 `avoidance.probe`。
- probe card 根据 `type` 渲染不同标题和按钮文案。
- scoped chat 区分正向 probe 和负向 probe，避免聊天记录混在一起。
- 插件画像页显示 active avoidance probes。

## OpenClaw

本期做完整闭环：

- 新增 skill/tool: `openbiliclaw_next_avoidance_probe`
- 新增 skill/tool: `openbiliclaw_respond_avoidance_probe`
- CLI 新增：
  - `next-avoidance-probe`
  - `respond-avoidance-probe`
- `listen` 订阅事件加入 `avoidance.probe`
- schema 新增 `AvoidanceProbeItem` / `AvoidanceProbeResponse`

问题模板：

```text
我猜「{domain}」可能是你想避开的方向（比如：{specifics}）——{reason}。这个判断准吗？
```

## 测试计划

单元测试：

- `tests/test_avoidance_speculator.py`
  - load/save state
  - TTL/cooldown
  - confirm/reject lifecycle
  - novelty guard
  - source mode diversity
  - active cap = 5

API 测试：

- `GET /api/avoidance-probes/pending` 返回 active probes。
- `POST /api/avoidance-probes/respond confirm` 按 specifics 优先的保守语义写入 `disliked_topics` 和 profile dislikes。
- `reject` 不写画像。
- `chat` 正向避雷语义会确认，否认语义会 reject-like。
- 未确认 avoidance probe 不触发 recommendation filtering。

runtime 测试：

- `avoidance.probe` 只有成功投递到 runtime stream 后才记录 history。
- 短期 domain/axis 去重生效。
- 正向和负向 active cap 互不影响。

OpenClaw 测试：

- `next_avoidance_probe` 返回最低确认压力候选。
- 连续调用会记录 history 并避免重复。
- `respond_avoidance_probe confirm/reject/chat` 调用后端状态更新。
- `listen` 能接收 `avoidance.probe`。

前端测试：

- 移动端消息 overlay 能渲染并操作 `avoidance.probe`。
- PC Web inbox/profile 能渲染并操作。
- 插件 popup inbox/profile 能渲染并操作。
- 两类 probe 的 scoped chat 不混淆。

## 文档更新

实现 PR 需要同步更新：

- `docs/modules/soul.md`
- `docs/modules/runtime.md`
- `docs/modules/memory.md`
- `docs/modules/integrations.md`
- `docs/modules/cli.md`
- `docs/modules/config.md`
- `docs/changelog.md`

如果实现新增跨模块数据流或架构图节点，还需要更新：

- `docs/architecture.md`
- `docs/spec.md`
- `README.md`
- `README_EN.md`

## 风险和缓解

风险：负向探针误伤推荐。

缓解：未确认 probe 不参与过滤，只有 confirm 后才进入 `disliked_topics`。

风险：正向兴趣和负向避雷互相冲突。

缓解：生成时对照 likes，禁止直接把高权重喜欢 domain 作为避雷 domain，只允许确认具体低质边界。

风险：复制状态机导致维护成本上升。

缓解：首版保持隔离降低回归风险；实现时把明显纯函数复用，后续再抽共享生命周期。

风险：前端两类 probe 文案混淆。

缓解：payload 带明确 `type`，前端根据 `interest.probe` / `avoidance.probe` 使用不同标题、按钮和状态文案。
