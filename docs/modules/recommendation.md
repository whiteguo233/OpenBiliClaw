# 推荐引擎

> 从 discovery 缓存中挑出最值得推的内容，并逐步生成像朋友一样的推荐表达。

## 概述

`recommendation/` 包负责把已经发现并评分过的内容，转成真正准备展示给用户的推荐结果。

当前模块包含：

- **RecommendationEngine** — 推荐排序、朋友式表达和推荐历史更新入口
- **Recommendation** — 单条推荐结果
- **PersonalTopic** — 后续个性化主题分组的占位结构

## 已实现功能

| 任务 | 状态 | 说明 |
|------|------|------|
| 6.1 推荐排序 | ✅ | 从 `content_cache` 选未推荐内容、按分数排序、写入推荐历史 |
| 6.2 朋友式推荐表达 | ✅ | 用 LLM 生成朋友式推荐理由和个性化 topic，并在 CLI 中真实展示 |
| 6.3 推荐持久化 | ✅ | 推荐记录已补齐展示状态、结构化反馈字段和反馈更新时间 |
| 候选排序统一 | ✅ | freshly discovered 与 cache backfill 现在共享同一套 tier / relevance / recency 排序口径 |
| 9.1 反馈处理 | ✅ | CLI、本地 API 与插件 popup 已统一写回推荐反馈与 `feedback` 事件 |
| 9.2 画像更新 | ✅ | 反馈累计到阈值后会自动触发偏好层重分析与画像重建 |
| 体验优化：动态“老B友”语气 | ✅ | 推荐文案不再固定套模板，而是根据画像、偏好和近期反馈动态调整信息密度、温度、梗感与直给程度 |
| M106 候选池即时换一批 | ✅ | `content_cache` 现已作为 discovery pool 使用，popup 可秒级从池子里换一批新推荐 |
| M107 候选池容量与状态展示 | ✅ | runtime 会按 `pool_target_count` 持续补货，popup 会展示可换数量、最近补货数量和补货方向。`pool_target_count` 同时作为硬上限：pool ≥ 目标时 refresh（含 force_refresh）直接返回 `pool_at_cap` 不再 discover，溢出部分会按分数 / 时间 / explore 优先顺序降为 `suppressed` |
| M117 同批多样性约束 | ✅ | 同一批推荐不再只按分数直取前 N，而会对重复 topic 做限流，让一批里更容易同时出现不同方向 |
| M118 topic_key 多样性强化 | ✅ | discovery pool 现在会持久化 `topic_key`，推荐层会优先按 `topic_key` 分桶再回填，减少同一 seed chain 或同类 query 连续刷屏 |
| M119 风格多样性与快速文案增强 | ✅ | `reshuffle` 现在会同时约束 `topic_key + style_key`，并把快速 fallback 文案润色成更自然的老B友短句 |
| M120 来源上限与硬配比 | ✅ | `reshuffle` 现在会对 `topic_key + style_key + source` 同时加硬上限，小批次优先保留不同来源，10 条一批时单一来源最多 3 条 |
| M121 推荐自动续页 | ✅ | popup 滚到底时现在会调用 `append` 从 discovery pool 再续 10 条，不再只能整组“换一批” |
| M122 来源优先补齐 | ✅ | 推荐选片时会先补齐不同 `source`，再限制重复 `style`，避免 `explore` 把 `search/trending` 挤出同一批结果 |
| M123 上游来源配额补货 | ✅ | discovery pool 低于目标值时，runtime 会先补足来源缺口，减少推荐层长期面对“explore 过满、trending 过少”的偏池子 |
| M124 generate 路径丰富度修正 | ✅ | `generate_recommendations()` 现在也会先对缓存候选做来源均衡，再分阶段放宽 `topic/style/source` 约束，避免高分 `related_chain` 长时间吃掉整批名额 |
| M125 pool 预生成推荐文案 | ✅ | discovery pool 现在会异步批量预生成 `expression/topic_label`，`reshuffle/append` 只消费预生成结果，缺失时返回空而不是写统一兜底 |
| M126 源无关内容分类 | ✅ | `classify_pool_backlog()` 在 `precompute_pool_copy` 前自动为未分类内容（XHS 等）补上 `style_key` / `topic_group` / `relevance_score`。COALESCE 保护已分类字段不被重复入库覆盖。`_diversity_tokens` 不再 fallback `source_strategy`——推荐层只看内容特征，来源完全透明 |
| M127 兴趣探针用户确认 | ✅ | WebSocket 推送 `interest.probe` → Chrome 通知 → popup 卡片（是 / 不是 / 多聊聊）→ `POST /api/interest-probes/respond` → speculator confirm/reject/chat。4h 去重冷却。推送从 `_run_refresh_plan` 移到 `run_forever` 主循环 |
| M128 CLI delight + probe | ✅ | `openbiliclaw delight` 手动查看惊喜推荐候选；`openbiliclaw probe` 手动列出猜测方向并交互确认/拒绝 |
| M129 惊喜候选自动预热与回填 | ✅ | delight 运行时统一使用共享阈值（默认 `0.70`，保守用户 `0.80`）；后台启动会自动补齐高分但缺 `reason/hook` 的候选，`suppressed` 高分库存也允许作为惊喜推荐入口 |
| v0.3.0 在线 supergroup 合并 | ✅ | `_merge_topic_supergroups` serve 时基于 embedding 把 `动漫杂谈/补番/解说` 等近义 topic 合并为同一聚类，让多样化器把它们当作一个桶 |
| v0.3.0 reshuffle 性能优化 | ✅ | 三段并发：embedding `asyncio.gather` 并行（替代顺序 await）+ embedding cache key 改为 label-only（命中率 ~0% → ~100%）+ `batch_insert_recommendations` 单 transaction 插入 10 条（10 次 fsync → 1 次）。换一批 2.6s → 0.6s |
| v0.3.0 supergroup embedding 预热 | ✅ | `prewarm_supergroup_embeddings` 在每次 refresh tick 后台并行预热所有池中 topic_group 的 embedding，让 reshuffle 跑全 cache hit |
| v0.3.1 双轴 fatigue + 陡曲线 | ✅ | `PoolCurator` 同时基于 `recent_topic_keys`（细）和 `recent_topic_groups`（粗）算 fatigue 取 max，避免 `动漫杂谈/补番/解说` 等子 topic 各自不触发 fatigue。曲线 `count^1.5/len*5` 让 count=2 即触发 0.47 强抑制；`topic_fatigue` 权重 0.15 → 0.25 |
| v0.3.1 SQL per-group 候选窗口 cap | ✅ | `get_pool_candidates` 用 `ROW_NUMBER() OVER (PARTITION BY topic_group)` 把候选窗口里每个 topic_group 限到 ≤3 条；600 池子 270 个 group 的长尾真正进入候选，distinct 主题数从 ~12-15 提升到 ~18-22 |
| v0.3.44 MMR 多样化 | ✅ | `_select_diversified_batch` 引入 Maximum Marginal Relevance：`score = α*relevance - β*max_cos_to_picked`，靠 embedding 余弦把 LLM 误聚到同一 `topic_label` 伞标签下的硬核内容真正打散。每轮 unique_topics=10/10、top_topic_share≤10% |
| v0.3.45 MMR embedding 提前 warm | ✅ | `warm_mmr_embeddings` 在 discovery 入池 + `classify_pool_backlog` 落库后立即并行 warm L2 SQLite embedding cache（cache key 文本由 `_mmr_embedding_text` 静态方法做 single source of truth），serve() 用 `asyncio.gather` 并行兜底,新增 `MMR embedding fetch: coverage=N/M elapsed=Xms` 埋点。换一批 P50 双峰（0.7s / 6-10s）收敛到稳定 <1s |
| v0.3.57 pool gate on precomputed copy | ✅ | `get_pool_candidates` / `count_pool_candidates` SQL 加 `AND COALESCE(pool_expression, '') != '' AND COALESCE(pool_topic_label, '') != ''` —— 未 precompute 的 row 对 serve() 不可见,消除"discovery 完成→precompute 完成"60–90s 窗口内 popup 显示占位模板的旧 bug。`engine.py:320` 的 `_fallback_expression` 路径变成 race-window 安全网,触发即 `logger.warning("Pool gate leak: ...")` |

## 公开 API

### RecommendationEngine

```python
from openbiliclaw.recommendation.engine import RecommendationEngine

engine = RecommendationEngine(llm=llm, database=db)
# v0.3.63+: 可选注入 BackgroundTaskRegistry,让 detached 协程
# (precompute_pool_copy 派生的 classify / delight 任务) 在
# config 热重载时被 cancel_all 统一回收。
# engine = RecommendationEngine(llm=llm, database=db, task_registry=ctx.task_registry)
items = await engine.generate_recommendations(
    discovered=None,
    profile=profile,
    limit=5,
)
```

行为说明：

- 若传入 `discovered`，优先对该批内容排序
- 若未传入 `discovered`，从 `content_cache` 中读取未推荐内容
- 从 `content_cache` 读取时，也会先做一轮来源均衡，避免前排高分缓存把候选窗口压成单一来源
- 排序主键先看 `candidate_tier`，再看 `relevance_score`、`last_scored_at/discovered_at`、`view_count`
- 生成结果后会写入 `recommendations` 表，避免下次重复选中
- 每条推荐都会调用 `generate_expression()` 生成 `expression` 和 `topic_label`
- 推荐表达会先从当前画像、偏好摘要和近期反馈推断 `ToneProfile`，再生成更贴近用户口味的“老B友”式文案
- CLI 展示后会把对应推荐记录标记为 `presented = 1`
- `feedback` 命令会把 `feedback_type` / `feedback_note` / `feedback_at` 写回推荐记录
- 多样性回填会分阶段放宽 `style`、`source`、`topic` 约束，只有候选真的不足时才彻底兜底补满

### RecommendationEngine.reshuffle_recommendations

```python
items = await engine.reshuffle_recommendations(
    profile=profile,
    limit=10,
)
```

行为说明：

- 直接从 `content_cache` discovery pool 里挑选 `fresh` 候选，不等待新一轮 discover 完成
- 过滤掉已展示、已明确反馈和已降级的候选
- 优先按 `candidate_tier`、`relevance_score` 和最近评分时间排序
- 同一批会优先按 `topic_key` 分桶，每个 topic 先出 1 条，再按分数回填
- 同一批还会对 `style_key` 做软均摊，尽量避免连续塞满“硬核解析 / 游戏攻略 / 新闻快讯”中的某一类
- 同一批还会对 `source` 做硬上限，避免 `explore` 或 `related_chain` 把 10 条整批刷满；当前 10 条一批时单一来源最多 3 条
- 当还没有补齐不同来源时，新的 `search / trending / related_chain` 候选会优先入选，不会先被重复 `style_key` 卡掉
- 如果高分候选前排被同一 `style_key` 占满，回填阶段会放宽风格限流，优先保证整批数量尽量补到请求上限
- 如果候选缺少 `topic_key`，才退回 `tags` 和标题/来源兜底做软限流
- 快路径现在不会现场调用 LLM，也不会再给整批卡片写同一个 fallback topic；只消费 pool 里已经预生成好的 `expression/topic_label`
- 如果某条候选暂时还没预生成好推荐文案，这两个字段会保持为空，交给前端直接隐藏
- 命中候选后会立即写入 `recommendations` 表，并把对应池子项标记为 `shown`
- runtime 会把 discovery pool 持续补到 `pool_target_count` 附近，默认目标现在是 `600`（上限 `600`）；达到目标后停止 discover，等池子掉回目标以下再补货，保证 popup 连续“换一批”和自动续页时尽量随时有货，同时避免无谓的远端调用。补货和 trim 会按 `[scheduler.pool_source_shares]` 做平台级硬配比，默认 B 站 / 小红书 / 抖音 = 8 / 1 / 1（600 池子约 480 / 60 / 60），单个平台族超过配额时会被先压回目标内；少量补货时 discovery 会收缩 LLM 评估窗口，只评估可被当前平台缺口吸收的过采样候选
- runtime 补货在调用 discovery 前会构建候选池分布 snapshot，把当前来源缺口和饱和方向作为可选上下文传给兼容的 discovery strategy
- pool-aware discovery 只改变上游补货时的 query 软指导和入池前软重排；`reshuffle` 的服务路径、候选过滤、文案 gating、推荐记录写入和多样性选择逻辑保持不变
- refresh 结束后还会顺手压一轮 `explore` 的高风险相邻子簇，避免制造 / 工艺 / 材料、博弈 / 桌游 / 机制这类方向把剩余可换窗口挤成单一口味

### RecommendationEngine.append_recommendations

```python
items = await engine.append_recommendations(
    profile=profile,
    excluded_bvids=["BV1A", "BV1B"],
    limit=10,
)
```

行为说明：

- 用于 popup 推荐流的续页，不会清空当前列表
- 会先排除前端已经展示过的 `excluded_bvids`
- 仍然走 discovery pool 快路径，不等待新一轮 discover 完成
- 同样复用 `topic_key + style_key + source` 的多样性选择逻辑，并只读取 pool 内已预生成好的推荐文案
- 追加命中的内容也会立即写入 `recommendations` 表，并把对应池子项标记为 `shown`

### RecommendationEngine.precompute_pool_copy

```python
count = await engine.precompute_pool_copy(
    profile=profile,
    limit=60,
)
```

行为说明：

- 从 discovery pool 中筛出还缺 `pool_expression / pool_topic_label` 的 fresh 候选
- 低并发批量调用 `generate_expression()` 的 LLM 主链生成朋友式推荐文案
- 成功后把结果回写到 `content_cache.pool_expression / content_cache.pool_topic_label`
- 生成失败时不会写 profile 级统一 fallback，而是保留空值，交给 popup 隐藏
- runtime refresh 会在补货后自动触发这一步，避免 popup 的“换一批 / 继续追加”现场等待 LLM
- 即使当前没有普通推荐文案要补，runtime 启动时也会走一次 `limit=0` 的预热路径，把高分 delight backlog 补成可直接推送的候选

### RecommendationEngine.precompute_delight_scores

```python
count = await engine.precompute_delight_scores(
    profile=profile,
    limit=30,
)
```

行为说明：

- 对 fresh / suppressed 池子里还没打分的候选补 `delight_score`
- 对已经高分但缺 `delight_reason / delight_hook` 的 backlog 候选补齐文案，而不是永远卡在“只有分数没有解释”
- 候选出池阈值与运行时 `pending delight` 查询共用同一套口径：默认 `0.70`，探索开放度较低时自动提高到 `0.80`
- `get_pending_delight()` 只会暴露文案已就绪的候选，避免前端收到空 `reason/hook`

### Recommendation

```python
Recommendation(
    content=content,
    recommendation_id=12,
    expression="这条会对上你最近那股想把问题想透的劲头。",
    topic_label="你最近那股想把问题想透的劲头",
    confidence=0.87,
    presented=False,
)
```

当前稳定填充的字段包括：

- `recommendation_id`
- `content`
- `expression`
- `topic_label`
- `confidence`
- `presented`
- `feedback`

其中 `content` 当前稳定可读字段包括：

- `bvid`
- `title`
- `up_name`
- `cover_url`
- `relevance_score`
- `relevance_reason`

### Recommendation Feedback

当前推荐记录会持久化以下反馈字段：

- `feedback_type`
- `feedback_note`
- `feedback_at`

推荐反馈会同时写入事件层，供后续偏好和洞察分析消费。

### Unified Feedback Entry

当前支持三种反馈信号：

- `like`
- `dislike`
- `comment`

统一入口包括：

- CLI：`openbiliclaw feedback <id> <like|dislike|comment> [--note ...]`
- API：`POST /api/feedback`
- 插件 popup：卡片上的 `喜欢` / `不喜欢` / `写一句`

### PoolCurator

```python
from openbiliclaw.recommendation.curator import PoolCurator
```

`PoolCurator` 提供推荐侧的独立评分，不依赖 Discovery 的结果。它从候选池中读取内容，按照一套专属权重对每条候选打分，供上层调用方叠加使用。

#### ScoringWeights

| 维度 | 权重 |
|------|------|
| `relevance` | 0.30 |
| `freshness` | 0.20 |
| `topic_fatigue` | 0.15 |
| `source_monotony` | 0.15 |
| `serendipity` | 0.20 |

#### 关键数据结构

**FeedbackSignals**：追踪用户反馈信号，包含三个字段：
- `disliked_up_mids` — 被 dislike 的 UP 主 mid 集合
- `disliked_topic_keys` — 被 dislike 的话题键集合
- `liked_topic_keys` — 被 like 的话题键集合

**ScoringContext**：评分时的上下文快照，包含：
- `recent_topic_keys` — 近期已推荐话题键列表
- `recent_sources` — 近期已推荐来源列表
- `feedback` — `FeedbackSignals` 实例

#### 常量

| 常量 | 值 |
|------|----|
| 新鲜度半衰期 | 3 天 |
| dislike UP 主惩罚 | 0.20 |
| dislike 话题惩罚 | 0.10 |
| like 话题加成 | 0.05 |
| 候选池低水位阈值 | 50 |

#### 公开 API

```python
# 从当前数据库状态构建评分上下文
context: ScoringContext = curator.build_context()

# 对候选列表评分，返回 bvid → rec_score 的映射（不修改输入）
scores: dict[str, float] = curator.score_candidates(candidates, context)

# 检查候选池健康状态
report: PoolHealthReport = curator.check_pool_health()
```

`score_candidates()` 以叠加覆盖层的形式返回新的分数映射，不会修改传入的候选对象。`PoolCurator` 的所有方法均不修改输入数据。

## 示例：记忆如何影响推荐结果

继续沿用一个典型场景：

- 用户最近连续看“国际时事深度解读”
- 聊天里多次表达“想把国际新闻背后的结构看明白”
- 对“浅层热点复读”内容给过 `dislike`

### 第一层影响：影响 discovery 的相关性评分

推荐模块本身主要消费的是已经入池的候选内容，但候选在进入推荐排序之前，通常已经在 discovery 阶段拿到了 `relevance_score` 和 `relevance_reason`。

这一分数会受到记忆影响，因为 discovery 评分的 LLM 调用会自动带上 core memory。于是系统更容易把下面这类内容打高分：

- 解释国际事件因果链的长视频
- 结构清晰、信息密度高的深度内容
- 与用户当前高权重兴趣一致的知识类题材

同时，已经形成的 `disliked_topics` 会让浅层、重复、标题党式内容更难获得高分。

### 第二层影响：影响最终排序

进入 `RecommendationEngine` 后，当前稳定排序口径是：

1. `candidate_tier`
2. `relevance_score`
3. `last_scored_at / discovered_at`
4. `view_count`

这意味着记忆对推荐排序的主要作用，不是最后一步临时硬改，而是**先通过画像和偏好改变 `relevance_score`，再由排序器稳定消费这份分数**。

换句话说：

- 如果系统已经记住你最近更偏“国际时事 + 深度解释”，这类内容会在 discovery 阶段先被打高分
- 到 recommendation 阶段，它们会自然排到更前面

### 第三层影响：影响推荐表达方式

推荐文案不是只看内容标题。`generate_expression()` 会结合：

- `SoulProfile`
- 偏好摘要
- 语气派生层 `ToneProfile`

来决定怎么说这条推荐。

例如在上面的场景里，推荐理由更可能是：

- “这条会对上你最近那股想把问题想透的劲头。”

而不是泛泛地说：

- “这是一条热门国际新闻视频。”

### 第四层影响：反馈回流到下一轮推荐

当用户对推荐点 `like` / `dislike` / `comment` 时，会同时发生几件事：

1. 更新 `recommendations` 表中的反馈字段
2. 追加一条 `feedback` 事件到事件层
3. 把对应 `content_cache` 项标记为 `feedbacked`
4. 若是 `dislike`，候选池查询会直接把这条内容排除
5. 当新反馈累计到阈值后，再统一触发偏好重分析和画像更新

所以反馈的影响分成两档：

- **即时影响**：这条不喜欢的内容会立刻更难再次出现
- **延迟影响**：累计反馈足够后，系统才会真正改偏好层和画像，进而改变后续 discovery 打分与推荐排序

### 一个简化后的因果链

`行为/聊天/反馈` → `事件层` → `偏好更新` → `必要时重建画像` → `discovery relevance_score 变化` → `recommendation 排序变化` → `新反馈继续回流`

## 设计决策

1. **先做排序闭环，再做表达生成**：先确保“选谁”稳定，再讨论“怎么说”
2. **推荐历史在选中时写入**：避免相邻批次重复选择同一内容
3. **表达生成单独落库**：排序和表达拆开，便于失败时降级到 fallback 文案
4. **`presented` 在 CLI 展示后更新**：区分“系统选中”和“用户已经看见”
5. **反馈保留当前状态**：v0.1 只保存当前反馈结果，不额外引入 feedback 历史表
6. **三端走同一反馈语义**：CLI、API 和 popup 都只写入当前反馈状态，并同步追加 `feedback` 事件
7. **先平衡候选，再放宽约束**：优先通过来源均衡和分阶段回填守住一批内容的丰富度，而不是靠最后一步无条件补满
8. **反馈驱动学习延迟触发**：推荐反馈不会逐条立刻重写画像，而是累计到阈值后统一重分析，降低噪声
9. **推荐语气跟着用户变**：表达风格不只看内容匹配度，还会根据画像和近期反馈动态调节“老B友”程度，尽量减少机械解释感
10. **缓存候选不能退化成只看播放量**：一旦从 `content_cache` 回读候选，也必须恢复 `relevance_score`、`candidate_tier` 和时间字段，保持与实时发现同一排序标准
11. **候选池先可展示，再做文案增强**：`discover` 入池时就要带 `relevance_reason`，popup “换一批”先秒级从池子里出片，`expression` 只是增强层，不再阻塞展示
12. **同批推荐需要显式做多样性约束**：高分不是唯一目标，排序后仍要对重复 topic/tag 做软限流，避免一批里全是同一类内容
13. **多样性要优先吃稳定 topic_key**：只靠 `tags` 不够稳，推荐层现在会优先使用 discovery 入池时生成的 `topic_key` 做分桶，再退回 `tags`
14. **topic 多样性还不够，要再控风格**：用户体感里的”全是很干很学术”往往不是同一 topic，而是同一种内容风格，所以 `reshuffle` 现在会同时约束 `style_key`
15. **快速换一批也要有说话味道**：快路径可以不等完整 `expression`，但不能直接退化成生硬说明句；当前 fallback 会按 `style_key` 生成更自然的短文案
16. **10 条一批必须加来源硬上限**：批量变大后，单靠 topic/style 还不够；现在 `reshuffle` 会同时控制 `source`，避免整批重新被 `explore` 或 `related_chain` 吞掉
17. **来源补齐优先于风格重复**：如果 `trending` 还没出场，就不该因为它和 `search` 同属 `light_chat` 而被挡在批次外；先让不同来源进来，再做风格均摊
18. **下游挑得再花，也救不了偏掉的池子**：推荐层的多样性约束只能做第二道保险；真正想让一批内容更丰富，必须让 runtime 在补货时先把各来源补到合理区间
