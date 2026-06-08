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
| 9.1 反馈处理 | ✅ | CLI、本地 API、插件 popup 与移动 Web 已统一写回推荐反馈与 `feedback` 事件；推荐点击会携带 `content_id / content_url / source_platform`，跨源内容不会被记成 B 站点击 |
| 9.2 画像更新 | ✅ | 反馈累计到阈值后会自动触发偏好层重分析与画像重建 |
| 体验优化：动态“老B友”语气 | ✅ | 推荐文案不再固定套模板，而是根据画像、偏好和近期反馈动态调整信息密度、温度、梗感与直给程度 |
| M106 候选池即时换一批 | ✅ | `content_cache` 现已作为 discovery pool 使用，popup 可秒级从池子里换一批新推荐 |
| M107 候选池容量与状态展示 | ✅ | runtime 会按 `pool_target_count` 持续补货，popup 会展示可换数量、最近补货数量和补货方向。`pool_target_count` 表示前端真实可换目标：`count_pool_candidates()` 达标后 refresh（含 force_refresh）返回 `pool_at_cap`，raw 素材库存允许高于目标并由独立 raw ceiling 控制 |
| M117 同批多样性约束 | ✅ | 同一批推荐不再只按分数直取前 N，而会对重复 topic 做限流，让一批里更容易同时出现不同方向 |
| M118 topic_key 多样性强化 | ✅ | discovery pool 现在会持久化 `topic_key`，推荐层会优先按 `topic_key` 分桶再回填，减少同一 seed chain 或同类 query 连续刷屏 |
| M119 风格多样性与快速文案增强 | ✅ | `reshuffle` 现在会同时约束 `topic_key + style_key`，并把快速 fallback 文案润色成更自然的老B友短句 |
| M120 来源上限与硬配比 | ✅ | `reshuffle` 现在会对 `topic_key + style_key + source` 同时加硬上限，小批次优先保留不同来源，10 条一批时单一来源最多 3 条 |
| M121 推荐自动续页 | ✅ | popup 与移动 Web 滚到底附近时会调用 `append` 从 discovery pool 再续 10 条，不再只能整组“换一批”；插件 / side panel 与移动 Web 的自动续页都需要用户向下滚动 / 翻页先触发一次意图门闩，后台 `refresh.pool_updated` 只刷新池子状态，不会重拉 `/api/recommendations` 覆盖已 append 的历史卡片，也不会在加载更多哨兵仍可见时空转消耗候选池；底部「加载更多」按钮仍作为兜底，并会在插入追加卡片前预热封面 |
| M122 来源优先补齐 | ✅ | 推荐选片时会先补齐不同 `source`，再限制重复 `style`，避免 `explore` 把 `search/trending` 挤出同一批结果 |
| M123 上游来源配额补货 | ✅ | discovery pool 低于目标值时，runtime 会按前端可换口径计算来源缺口，并用 raw-material headroom 限制请求量，减少推荐层长期面对“explore 过满、trending 过少”的偏池子 |
| M124 generate 路径丰富度修正 | ✅ | `generate_recommendations()` 现在也会先对缓存候选做来源均衡，再分阶段放宽 `topic/style/source` 约束，避免高分 `related_chain` 长时间吃掉整批名额 |
| M125 pool 预生成推荐文案 | ✅ | discovery pool 现在会异步批量预生成 `expression/topic_label`，`reshuffle/append` 只消费预生成结果，缺失时返回空而不是写统一兜底 |
| M126 源无关内容分类 | ✅ | `classify_pool_backlog()` 在 `precompute_pool_copy` 前为 legacy / recovery 未分类内容补上 `style_key` / `topic_group` / `relevance_score`，并在批量评估 prompt 中带上近期 `negative_examples`。正常来源 ingest 已改为先走 `discovery_candidates` 统一评估，推荐层不再承担外站原始候选的首评估。COALESCE 保护已分类字段不被重复入库覆盖。`_diversity_tokens` 不再 fallback `source_strategy`——推荐层只看内容特征，来源完全透明 |
| M127 兴趣探针用户确认 | ✅ | WebSocket 推送 `interest.probe` → Chrome 通知 → popup 卡片（是 / 不是 / 多聊聊）→ `POST /api/interest-probes/respond` → speculator confirm/reject/chat。4h 去重冷却。推送从 `_run_refresh_plan` 移到 `run_forever` 主循环 |
| M127b 避雷探针用户确认 | ✅ | WebSocket 推送 `avoidance.probe` → popup / Web / OpenClaw 卡片（确实不喜欢 / 不是 / 多聊聊）→ `POST /api/avoidance-probes/respond`；确认后写入 `disliked_topics` 并清理候选池，未确认时不参与过滤 |
| M128 CLI delight + probe | ✅ | `openbiliclaw delight` 手动查看惊喜推荐候选；`openbiliclaw probe` 手动列出猜测方向并交互确认/拒绝 |
| M129 惊喜候选自动预热与回填 | ✅ | delight 运行时统一使用共享阈值（默认 `0.70`，保守用户 `0.80`）；后台启动会自动补齐高分但缺 `reason/hook` 的候选，`suppressed` 高分库存也允许作为惊喜推荐入口 |
| 惊喜推荐反馈保留 | ✅ | `POST /api/delight/respond` 中 `view / like / chat` 只记录正向学习信号并保留当前卡片；`dislike / dismiss` 才消费候选并驱动三端立即移除 |
| v0.3.0 在线 supergroup 合并 | ✅ | `_merge_topic_supergroups` serve 时基于 embedding 把 `动漫杂谈/补番/解说` 等近义 topic 合并为同一聚类，让多样化器把它们当作一个桶 |
| v0.3.0 reshuffle 性能优化 | ✅ | 三段并发：embedding `asyncio.gather` 并行（替代顺序 await）+ embedding cache key 改为 label-only（命中率 ~0% → ~100%）+ `batch_insert_recommendations` 单 transaction 插入 10 条（10 次 fsync → 1 次）。换一批 2.6s → 0.6s |
| v0.3.0 supergroup embedding 预热 | ✅ | `prewarm_supergroup_embeddings` 在每次 refresh tick 后台并行预热所有池中 topic_group 的 embedding，让 reshuffle 跑全 cache hit |
| v0.3.1 双轴 fatigue + 陡曲线 | ✅ | `PoolCurator` 同时基于 `recent_topic_keys`（细）和 `recent_topic_groups`（粗）算 fatigue 取 max，避免 `动漫杂谈/补番/解说` 等子 topic 各自不触发 fatigue。曲线 `count^1.5/len*5` 让 count=2 即触发 0.47 强抑制；`topic_fatigue` 权重 0.15 → 0.25 |
| v0.3.1 SQL per-group 候选窗口 cap | ✅ | `get_pool_candidates` 用 `ROW_NUMBER() OVER (PARTITION BY topic_group)` 把候选窗口里每个 topic_group 限到 ≤3 条；600 池子 270 个 group 的长尾真正进入候选，distinct 主题数从 ~12-15 提升到 ~18-22 |
| v0.3.44 MMR 多样化 | ✅ | `_select_diversified_batch` 引入 Maximum Marginal Relevance：`score = α*relevance - β*max_cos_to_picked`，靠 embedding 余弦把 LLM 误聚到同一 `topic_label` 伞标签下的硬核内容真正打散。每轮 unique_topics=10/10、top_topic_share≤10% |
| v0.3.45 MMR embedding 提前 warm | ✅ | `warm_mmr_embeddings` 在 discovery 入池 + `classify_pool_backlog` 落库后立即并行 warm L2 SQLite embedding cache（cache key 文本由 `_mmr_embedding_text` 静态方法做 single source of truth），serve() 用 `asyncio.gather` 并行兜底,新增 `MMR embedding fetch: coverage=N/M elapsed=Xms` 埋点。换一批 P50 双峰（0.7s / 6-10s）收敛到稳定 <1s |
| v0.3.57 pool gate on precomputed copy | ✅ | `get_pool_candidates` / `count_pool_candidates` SQL 加 `AND COALESCE(pool_expression, '') != '' AND COALESCE(pool_topic_label, '') != ''` —— 未 precompute 的 row 对 serve() 不可见,消除"discovery 完成→precompute 完成"60–90s 窗口内 popup 显示占位模板的旧 bug。`engine.py:320` 的 `_fallback_expression` 路径变成 race-window 安全网,触发即 `logger.warning("Pool gate leak: ...")` |
| v0.3.66 pool gate on classification | ✅ | `get_pool_candidates` / `count_pool_candidates` 现在同样要求 `style_key` 与 `topic_group` 非空；`get_pool_candidates_needing_copy` 也只挑已分类但缺文案的候选，避免未分类跨源内容先生成 copy 后绕过 serve 分类口径 |
| v0.3.91 servable pool count | ✅ | `count_pool_candidates()` 在读取前刷新 SQLite/WAL snapshot，并默认应用与 `get_pool_candidates()` 相同的 `max_per_topic_group=3` 候选窗口；新增 `count_pool_readiness()` 拆分 `available/raw/pending`；`serve()` 零候选 warning 会输出 `raw/servable/pending`，用于定位“池子有素材但暂不可换”的真实原因。 |
| v0.3.102 空池热路径短路 | ✅ | `/api/recommendations/reshuffle` 与 `/api/recommendations/append` 在 `pool_available_count=0` 时立即返回空数组，不再读取画像或调用推荐引擎，并只按 30 秒 debounce 触发一次自动补货；`RecommendationEngine.serve()` 在可用池为 0、或候选被 `excluded_bvids` / 最近已看过滤到 0 后直接返回，跳过 curator、MMR embedding 和推荐历史写入。 |
| v0.3.x available-target pool refill | ✅ | `count_pool_available_candidates_by_source()` 按 `count_pool_candidates()` 同口径统计各平台族的真实可换数量；`count_pool_raw_material_by_source()` 统计 fresh / 非 dislike / 未推荐 / 未看过的 raw material（含 `discovery_candidates` 待评估素材）用于 raw ceiling。补池不再因为 raw/linkable B 站库存达到 300 而停在前端 246 可换，raw trim 也不会在可换未达标时把库存压回 `pool_target_count`。 |
| v0.3.x 统一 discovery 待评估池 | ✅ | 正常来源 ingest 不再直接写 `content_cache` 等推荐层分类；B 站 / XHS / 抖音 / YouTube raw candidates 先进入 `discovery_candidates`，由 discovery pipeline 统一 batch 评估并 admission 到 `content_cache`。`classify_pool_backlog()` 只作为 legacy / recovery 路径处理已在 `content_cache` 中但缺分类的旧行。 |
| v0.3.91 新兴趣放大保护 | ✅ | 新确认兴趣会生成 amplification key，`PoolCurator` 用最近 24h 推荐历史计算滚动占比，超过 25% 的方向会被降权；最终批量选择还会硬限制同一新方向最多 `max(1, floor(limit * 0.25))` 条，避免刚确认的兴趣短期刷屏 |
| v0.3.91 推荐读取索引 | ✅ | `recommendations(created_at, id)` 与 `content_cache(content_id)` 在数据库初始化时自动创建索引，`/api/recommendations` 和 activity feed 的推荐历史读取不再因 `c.bvid = r.bvid OR c.content_id = r.bvid` 退化为双表扫描。 |
| v0.3.74 recommendation/delight JSON 容错统一 | ✅ | `RecommendationEngine` 的内容分类、单条表达和批量表达解析，以及 `delight.precompute_delight_scores()` 的 batch scorer 都改用 `llm.json_utils`。MiMo / OpenAI-compatible provider 返回 object wrapper、fenced JSON、JSONL、schema echo 或 malformed `{ [ ... ] }` 时会优先提取满足字段 predicate 的真实结果 |
| v0.3.81 批量结果按内容 ID 绑定 | ✅ | 批量推荐文案和源无关内容分类的 prompt 都带 `bvid/content_id`，解析时优先按返回 ID 写回。模型乱序、漏项或只返回部分条目时不再按数组下标把原因写到错误视频；无 ID 且数量不完整的文案批次会降级单条生成，分类批次会标记失败避免错写 |
| v0.3.x 批量文案限流保护 | ✅ | `_precompute_batch()` 遇到 LLM provider rate limit / cooldown / quota 时不再进入逐条 `_try_generate_expression()` fallback；本轮预生成计为 0，保留空 `pool_expression/topic_label` 等后续调度重试 |
| v0.3.x 批量文案错位 / 重复防护 | ✅ | 强化 v0.3.81：**多条**候选缺 `bvid/content_id` 时（不止数量不完整）一律降级逐条生成——位置匹配只对无歧义的单条批次保留，杜绝弱模型乱序导致的文案张冠李戴；新增去重闸，同一句文案被分配给多个不同 bvid 时整组丢弃（宁可不发也不发重复），根治本地小模型上下文截断时「每条理由都一样且对不上视频」 |
| v0.3.x 负反馈表达避让 | ✅ | `_recommendation_profile_summary()` 会把 `preferences.disliked_topics` 带入推荐画像摘要；单条和批量推荐表达 prompt 都要求避开这些主题 / 话术模式，候选明显命中时只能保守说明差异化理由，不得热情背书或把避雷项包装成用户偏好 |
| v0.3.x XHS 自发布内容过滤 | ✅ | `get_pool_candidates` / `count_pool_candidates` / `count_pool_readiness` 及后台整理查询（evaluation / copy / delight）在 SQL 层排除已知的自发布小红书行；`_purge_self_authored_pool_items` 同时匹配 `up_name` 和 `author_name`；self_info 首次到达或变更时立即 purge 已入池内容。`RecommendationEngine` 通过 `xhs_self_info_provider` 回调从 runtime state 获取 nickname，`Database` 保持纯存储层不直接读 runtime state |

## 公开 API

### RecommendationEngine

```python
from openbiliclaw.recommendation.engine import RecommendationEngine

engine = RecommendationEngine(llm=llm, database=db)
# v0.3.63+: 可选注入 BackgroundTaskRegistry,让 detached 协程
# (precompute_pool_copy 派生的 classify / delight 任务) 在
# config 热重载时被 cancel_all 统一回收。
# engine = RecommendationEngine(llm=llm, database=db, task_registry=ctx.task_registry)
# v0.3.x+: 可选注入 xhs_self_info_provider,在 pool 读取/计数时
# 排除用户自己发布的小红书内容。
# engine = RecommendationEngine(llm=llm, database=db,
#     xhs_self_info_provider=lambda: memory.load_discovery_runtime_state().get("xhs_self_info"))
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
- 从 `content_cache` / discovery pool 取候选时会用最近 `view` 事件里的 `source_platform:content_id` 过滤已看内容；B 站保留 raw BVID 兼容，其他来源不会再因为没有 BVID 而漏过滤
- 排序主键先看 `candidate_tier`，再看 `relevance_score`、`last_scored_at/discovered_at`、`view_count`
- 生成结果后会写入 `recommendations` 表，避免下次重复选中
- 每条推荐都会调用 `generate_expression()` 生成 `expression` 和 `topic_label`
- 推荐表达会先从当前画像、偏好摘要、`disliked_topics` 和近期反馈推断 `ToneProfile`，再生成更贴近用户口味且避开长期雷点的“老B友”式文案
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
- 对刚确认/刚晋升的兴趣方向会应用 amplification guard：同批命中同一 amplification key 的候选最多 `max(1, floor(limit * 0.25))` 条；MMR 路径和非 embedding 多样化路径使用同一硬上限
- 当还没有补齐不同来源时，新的 `search / trending / related_chain` 候选会优先入选，不会先被重复 `style_key` 卡掉
- 如果高分候选前排被同一 `style_key` 占满，回填阶段会放宽风格限流，优先保证整批数量尽量补到请求上限
- 如果候选缺少 `topic_key`，才退回 `tags` 和标题/来源兜底做软限流
- 快路径现在不会现场调用 LLM，也不会再给整批卡片写同一个 fallback topic；只消费 pool 里已经预生成好的 `expression/topic_label`
- 若 API 看到 `pool_available_count=0`，`/api/recommendations/reshuffle` 会直接返回 `items=[]` 并按 30 秒 debounce 触发一次后台补货，不再为必然为空的结果读取画像或进入推荐引擎
- 若引擎内部发现可用池为 0，或候选被最近已看过滤到 0，会直接返回空数组，跳过 curator scoring、MMR embedding 和推荐历史写入
- 如果某条候选暂时还没预生成好推荐文案，这两个字段会保持为空，交给前端直接隐藏
- 命中候选后会立即写入 `recommendations` 表，并把对应池子项标记为 `shown`
- runtime 会把 discovery pool 持续补到 `pool_target_count` 个“真实可换”候选，默认目标现在是 `300`（允许配置到 `600`）；达到目标后停止 discover，等可换数掉回目标以下再补货。raw 素材库存不是 `pool_target_count` 的硬上限：当 topic window、预生成、分类或 XHS token 让 raw 与 available 之间存在折损时，raw 可增长到 `max(pool_target_count * 2, pool_target_count + 120)`，再由 raw ceiling trim 控制成本。补货和 trim 会按 `[scheduler.pool_source_shares]` 做平台级配比，默认保存 B 站 / 小红书 / 抖音 / YouTube = 8 / 1 / 1 / 1，但小红书、抖音、YouTube 默认关闭，运行时有效配比默认只有 B 站；显式启用某个平台后才会按保存 share 获得配额。少量补货时 discovery 会收缩 LLM 评估窗口，只评估可被当前平台可换缺口和 raw headroom 吸收的过采样候选
- runtime 补货在调用 discovery 前会构建候选池分布 snapshot，把当前来源缺口和饱和方向作为可选上下文传给兼容的 discovery strategy
- pool-aware discovery 只改变上游补货时的 query 软指导和入池前软重排；`reshuffle` 的服务路径、候选过滤、文案 gating、推荐记录写入和多样性选择逻辑保持不变
- `count_pool_candidates()` 是“真实可换”口径，必须与 `get_pool_candidates()` 的 fresh/readiness/viewed/linkability gates 以及默认每 `topic_group` 最多 3 条的候选窗口保持一致；`count_pool_available_candidates_by_source()` 必须与它按来源求和一致。raw ceiling 使用 `count_pool_raw_material_by_source()`，包含 `content_cache` 中未预生成 / 未分类等暂不可换素材，以及 `discovery_candidates` 中 `pending_eval/evaluating/evaluated` 的待评估素材，但排除最近看过和已推荐内容。
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

- 用于 popup 和移动 Web 推荐流的续页，不会清空当前列表
- 会先排除前端已经展示过的 `excluded_bvids`
- 若 API 看到 `pool_available_count=0`，会立即返回 `items=[]` 并按 30 秒 debounce 触发一次后台补货；不会读取画像或进入推荐引擎
- 若 `excluded_bvids` 或最近已看过滤把候选清空，引擎直接返回空数组，不执行 curator、MMR embedding 或推荐历史写入
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

- 从 discovery pool 中筛出已具备 `style_key / topic_group`、但还缺 `pool_expression / pool_topic_label` 的 fresh 候选
- 低并发批量调用 `generate_expression()` 的 LLM 主链生成朋友式推荐文案
- 解析批量 LLM 响应时通过共享 JSON helper 接受 `results/items/data/output` 等 wrapper、fenced JSON、JSONL 和回显 schema 后的最终结果，但仍要求每条结果具备推荐表达所需字段
- 批量 prompt 会把每条候选的 `bvid/content_id` 交给 LLM；如果响应带回 ID，写库时按 ID 匹配，不信任数组顺序。响应没有 ID 且数量不完整时会降级到单条生成，避免把后续视频的文案整体前移
- 批量调用若命中 provider 限流 / cooldown / quota，不会再逐条调用 LLM；这些候选继续保持文案空值，等待下一轮后台预生成
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
- 解析 batch scorer 时复用共享 JSON helper，并通过 item predicate 只接受含 `score` / `reason` / `hook` 语义的结果，避免 provider 回显 prompt schema 时误入库
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
- `content_id`
- `content_url`
- `source_platform`

### Recommendation Click API

```http
POST /api/recommendation-click
Content-Type: application/json

{
  "recommendation_id": 42,
  "bvid": "KPoJ7p9iy4Q",
  "content_id": "KPoJ7p9iy4Q",
  "content_url": "https://www.youtube.com/watch?v=KPoJ7p9iy4Q",
  "source_platform": "youtube",
  "title": "A YouTube deep dive"
}
```

行为说明：

- `bvid` 保留为推荐历史兼容字段；非 B 站内容可传同一个跨源 `content_id`
- `content_id / content_url / source_platform` 会进入持久化 click 事件和 `recommendation_click` 强画像信号
- 如果 payload 只传 `recommendation_id`，后端会从推荐记录 join `content_cache` 回填标题、作者、topic、`content_id / content_url / source_platform`
- `content_url` 缺失时，后端只对 B 站、YouTube、抖音构造安全 fallback；小红书仍要求已有带 token 的 URL，避免生成不可打开的裸链接

### Recommendation Feedback

当前推荐记录会持久化以下反馈字段：

- `feedback_type`
- `feedback_note`
- `feedback_at`

推荐反馈会同时写入事件层，供后续偏好和洞察分析消费。

### Unified Feedback Entry

当前支持四种反馈信号：

- `like`
- `dislike`
- `comment`（必须带 `note`）
- `dismiss`（v0.3.89+）：软移除单条推荐，不更新画像也不下调话题/作者权重。`update_recommendation_feedback` 会把 `content_cache.pool_status` 标记为 `feedbacked`，所以同一条候选不会再次进入发现池；前端按 `feedback_type` 非空过滤掉已忽略卡片。`/api/feedback` 不要求 dismiss 携带 `note`；事件层照常上报 `feedback` 事件，但 `inferred_satisfaction` 维持 `unknown` 以避免把单次软忽略误读为话题级负反馈。

统一入口包括：

- CLI：`openbiliclaw feedback <id> <like|dislike|comment|dismiss> [--note ...]`
- API：`POST /api/feedback`
- 插件 popup：卡片上的 `喜欢` / `不喜欢` / `写一句`
- 桌面 Web：推荐卡片底部「喜欢 / 不感兴趣 / 忽略」三连按钮，「忽略」走 `dismiss` 通道
- 移动 Web：推荐卡片反馈与惊喜推荐「喜欢 / 不感兴趣」共用后端反馈语义，惊喜推荐直接写入 `/api/delight/respond`

### Delight Feedback

`POST /api/delight/respond` 支持 `view / like / dislike / chat / dismiss`。`view / like / chat` 是正向或探索性表达，只记录点击、喜欢或对话学习信号，并让插件 side panel、桌面 Web 和移动 Web 保留当前惊喜卡片；`dislike / dismiss` 才会标记为 consumed/notified，并驱动三端立即移除该候选。

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

**FeedbackSignals**：追踪用户反馈信号，包含以下字段：
- `disliked_up_mids` — 被 dislike 的 UP 主 mid 集合
- `disliked_topic_keys` — 被 dislike 的话题键集合
- `disliked_franchises` — 被 dislike 内容所属 franchise / IP 集合，用于同 IP 软降权
- `liked_topic_keys` — 被 like 的话题键集合

**ScoringContext**：评分时的上下文快照，包含：
- `recent_topic_keys` — 近期已推荐话题键列表
- `recent_sources` — 近期已推荐来源列表
- `newly_confirmed_amplification_keys` — 刚确认兴趣及其 specifics/topic aliases 的归一化键集合
- `over_budget_amplification_keys` — 最近 24h 推荐占比已达到 25% 的新兴趣方向集合
- `feedback` — `FeedbackSignals` 实例

#### 新兴趣 Amplification Guard

兴趣探针确认、profile 页面确认、聊天强确认或短期 buffer 晋升后，推荐层可以把对应 domain / specific 归一化为 `amplification_key`。v1 匹配范围包括：

- 确认兴趣 domain
- 确认兴趣 specifics
- 候选 `topic_group`
- 候选 `topic_key`

职责划分：

- `Database.get_recent_recommendation_signals_since()` 提供最近推荐窗口，优先使用 `presented_at`，旧记录用 `created_at` 兜底。
- `PoolCurator.build_context(newly_confirmed_amplification_keys=..., rolling_window_hours=24)` 计算 24h rolling share，share `>= 0.25` 的 key 进入 `over_budget_amplification_keys` 并在评分中降权。
- `RecommendationEngine._select_diversified_batch()` 和 MMR 选择负责最终硬上限：每个新方向最多 `max(1, floor(limit * 0.25))` 条。Curator 是软降权，最终 selector 是安全阀。

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
- `disliked_topics`
- 语气派生层 `ToneProfile`

来决定怎么说这条推荐。

例如在上面的场景里，推荐理由更可能是：

- “这条会对上你最近那股想把问题想透的劲头。”

而不是泛泛地说：

- “这是一条热门国际新闻视频。”

如果候选内容明显命中 `disliked_topics`，prompt 不允许把该避雷项包装成“你一直喜欢这个”。表达层最多保守说明它与已知雷点的差异化理由，避免在已经被用户明确排斥的方向上热情背书。

`classify_pool_backlog()` 对旧版本遗留、人工导入或异常恢复后已经在 `content_cache` 里但尚未分类的内容做 batch 评估时，也会从事件层读取近期 negative exemplars 并作为 `negative_examples` 传给同一个 evaluator prompt。正常 XHS / 抖音 / YouTube ingest 现在先进入 `discovery_candidates`，由 discovery pipeline 统一评估后才 admission 到推荐池；这条分类路径保留为 recovery 安全网。

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
19. **legacy 分类也要消费负反馈样本**：正常来源候选会先进入 `discovery_candidates` 统一评估；如果旧行、人工导入或异常恢复让内容已经在 `content_cache` 里但缺 `style_key/topic_group/relevance_score`，这条补评估路径也必须和 discovery batch evaluator 一样读取 `negative_examples`
20. **分类先于推荐文案**：`precompute_pool_copy` 只处理 `style_key/topic_group` 已补齐的候选。未分类内容应先走 `classify_pool_backlog`，否则文案、topic label 与后续多样性/负反馈口径可能不一致。
21. **新确认兴趣只应被轻推，不应刷屏**：探针确认是用户给出的方向许可，不是 24h 内把同一方向塞满推荐流的理由；滚动预算与同批硬上限必须同时存在，前者降低排序冲动，后者防止最终回填阶段破坏体验。
