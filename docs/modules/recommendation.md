# 推荐引擎

> 从 discovery 缓存中挑出最值得推的内容，并逐步生成像朋友一样的推荐表达。

runtime 使用公开 `drain_pending_expression_copy(profile, limit<=60, max_extra_requests=6)` 作为 copy-only 入口：pending 达 8 条立即执行，1–7 条从首次通知起固定等待最多 3 秒；provider 每批最多 30 条、fan-out 2，malformed/partial batch 默认最多额外拆分请求 6 次。分类完成只通过 `set_copy_pending_callback()` 通知，不 inline 等待 copy。OpenClaw direct one-shot 是受限例外，调用 `limit=4, max_extra_requests=0`，持久化首 batch 的有效 subset 并将剩余行留作下一请求。参数来自 2026-07-12 生产日志校准。

## 概述

`recommendation/` 包负责把已经发现并评分过的内容，转成真正准备展示给用户的推荐结果。

推荐卡与 delight 的收藏 / 稍后再看动作在插件 side panel、桌面 Web 与移动 Web 中统一保留 canonical `item_key/source_platform/content_id/content_url/content_type`，调用平台中立 `/api/saved/{list_kind}`。是否顺带创建原生同步任务只由后端 `[saved_sync].auto_sync_enabled` 判断，默认 `false`；前端不复制平台路由或自行绕过开关。URL fallback 保持空 `content_id`，不会把 recommendation row ID 或 namespaced legacy ID 当原始内容 ID，也不会把 X / 知乎文本强制写成 video。前端只对本地保存做 optimistic update；busy/version 状态按 `list_kind:item_key` 隔离，平台同步状态由保存列表和 durable task polling 展示，失败不撤销本地已保存态。插件与桌面 Web 的这些保存 toggle 在 coarse pointer 下提供至少 44×44 的触控目标，pressed tooltip / aria-label 与真实状态同步。

当前模块包含：

- **RecommendationEngine** — 推荐排序、朋友式表达和推荐历史更新入口
- **Recommendation** — 单条推荐结果
- **PersonalTopic** — 后续个性化主题分组的占位结构

## 已实现功能

| 任务 | 状态 | 说明 |
|------|------|------|
| v0.3.191 视觉 embedding pipeline 完整契约 | ✅ | P1/P3 共享质心但与 P1 cover bonus 分开开关；质心、关键帧、弹幕状态绑定 embedding fingerprint / 维度，关键帧再绑定采样签名；瞬时失败保持可重试，成功空结果才推进完成状态。预热使用配置 limit 和当前 servable pool，跨平台 bonus 以 0 为固定点分段归一化，离线 A/B 脚本复用生产公式 |
| 6.1 推荐排序 | ✅ | 从 `content_cache` 选未推荐内容、按分数排序、以 canonical `item_key` 写入推荐历史 |
| 6.2 朋友式推荐表达 | ✅ | 用 LLM 生成朋友式推荐理由和个性化 topic，并在 CLI 中真实展示；`recommendation.*` 的分类和短文案调用在未显式指定时统一用 `reasoning_effort=""`，避免 DeepSeek thinking 拉长文案回填 |
| 6.3 推荐持久化 | ✅ | 推荐记录已补齐展示状态、结构化反馈字段和反馈更新时间 |
| 候选排序统一 | ✅ | freshly discovered 与 cache backfill 现在共享同一套 tier / relevance / recency 排序口径 |
| 9.1 反馈处理 | ✅ | CLI、本地 API、插件 popup 与移动 Web 已统一写回推荐反馈与 `feedback` 事件；推荐点击会携带 `content_id / content_url / source_platform`，跨源内容不会被记成 B 站点击；推荐反馈事件同样保留候选真实 `source_platform`，旧记录缺来源时兼容回退 `bilibili` |
| 9.2 画像更新 | ✅ | 反馈累计到阈值后会自动触发偏好层重分析与画像重建 |
| Bangumi 目录卡片 | ✅ | 推荐与惊喜 DTO 透传 `rating_score / rating_count / source_rank`；桌面、移动与扩展统一显示评分、评分人数和排名，且不把目录评分冒充点赞/评论 |
| 微博文字卡与真实互动 | ✅ | `source_platform="weibo"` / `content_type="post"` 使用无封面文字卡；DTO 透传真实 `reads_count → view_count`、点赞、评论和 `reposts_count → share_count`。没有 favorite / danmaku 字段时保持 0 并隐藏，不用热搜热度或转发数冒充阅读量 |
| Issue #91 卡片反馈双轴匹配 | ✅ | 卡片 like/dislike 会在 Pool Curator 中同时匹配候选的细粒度 `topic_key` 与粗粒度 `topic_group`；任一轴命中即施加一次软调整，两轴同时命中不会重复加权 |
| 体验优化：画像驱动“老B友”语气 | ✅ | 推荐文案不再固定套模板，而是根据画像 tone profile 调整信息密度、温度、梗感与直给程度；`style_key` 只影响内容切入角度，不再改写用户语气 |
| M106 候选池即时换一批 | ✅ | `content_cache` 现已作为 discovery pool 使用，popup 可秒级从池子里换一批新推荐 |
| M107 候选池容量与状态展示 | ✅ | runtime 会按 `pool_target_count` 持续补货，popup 会展示可换数量、最近补货数量和补货方向。`pool_target_count` 表示前端真实可换目标：`count_pool_candidates()` 达标后 refresh（含 force_refresh）返回 `pool_at_cap`，raw 素材库存允许高于目标并由独立 raw ceiling 控制 |
| M117 同批多样性约束 | ✅ | 同一批推荐不再只按分数直取前 N，而会对重复 topic 做限流，让一批里更容易同时出现不同方向 |
| M118 topic_key 多样性强化 | ✅ | discovery pool 现在会持久化 `topic_key`，推荐层会优先按 `topic_key` 分桶再回填，减少同一 seed chain 或同类 query 连续刷屏 |
| M119 风格多样性与快速文案增强 | ✅ | `reshuffle` 现在会同时约束 `topic_key + style_key`，并把快速 fallback 文案润色成更自然的老B友短句 |
| M120 来源上限与硬配比 | ✅ | `reshuffle` 现在会对 `topic_key + style_key + source` 同时加硬上限，小批次优先保留不同来源，10 条一批时单一来源最多 3 条 |
| M121 推荐自动续页 | ✅ | popup 与移动 Web 滚到底附近时会调用 `append` 从 discovery pool 再续 10 条，不再只能整组“换一批”；插件 / side panel 与移动 Web 的自动续页都需要用户向下滚动 / 翻页先触发一次意图门闩，后台和推荐消费后的 `refresh.pool_updated` 只刷新池子状态与可换提示，不会重拉 `/api/recommendations` 覆盖已 append 的历史卡片，也不会在加载更多哨兵仍可见时空转消耗候选池；底部「加载更多」按钮仍作为兜底，并会在插入追加卡片前预热封面 |
| PC Web 后台推荐读取边界 | ✅ | 桌面 Web 已有卡片时，切回标签页、配置应用或其它后台水合只同步 runtime / 库存状态并跳过 `/api/recommendations`；该 GET 在首屏历史较薄时可能触发 `serve()` 补池并消费候选，因此只有首屏空列表或用户明确手动刷新时才读取推荐快照。关闭“滚动到底自动加载推荐”后，滚动与后台状态刷新都不会消费候选池 |
| PC Web 自动续页滚动稳定 | ✅ | 平台 Tab 即使在用户用滚轮 / 触控板浏览到列表底部后仍持有键盘焦点，续页完成重绘 Tab 库存徽标时也只恢复焦点、不把离屏 Tab 滚回视口；推荐卡增量追加、当前 `scrollY` 与键盘可达性同时保留。 |
| Web 空失败态恢复 | ✅ | 移动与桌面 Web 会把推荐/库存读取失败与真实空结果分开：瞬时超时进入 1/2/4/8 秒、最多四次的单飞恢复；成功空数组终止推荐重试；`refresh.pool_updated` 只在当前列表仍为空且上次推荐读取失败时触发条件恢复，已有或追加卡片不会被覆盖。库存状态可由含 `pool_available_count` 的实时快照独立恢复，不再把未知状态渲染成零库存。 |
| M122 来源优先补齐 | ✅ | 推荐选片时会先补齐不同 `source`，再限制重复 `style`，避免 `explore` 把 `search/trending` 挤出同一批结果 |
| 平台定向推荐（PC Web） | ✅ | `serve / reshuffle / append`（含 `*_with_result`）新增默认空的 keyword-only `source_platform`。非空时只装载该 canonical 平台的候选、跳过跨平台保底补位，其余 curator 打分、amplification guard、embedding/MMR、topic/style/broad-topic 多样性、视觉加成、持久化与 shown 提交全部复用既有实现——平台作用域只缩小候选集合，绝不是"先生成混合批次再过滤结果"。返回前经 `_enforce_platform_scope()` 校验，发现跨平台行记 ERROR 并丢弃，不让泄漏进响应。省略该参数时调用形状与行为与引入前完全一致（对签名不确定的兼容对象也只在真的带平台时才传新关键字）。**仅 PC Web 有该交互**：移动 Web、扩展 popup / side panel 与 CLI 没有平台 Tab，继续走不带平台的兼容路径，行为不变 |
| 平台库存徽标（PC Web） | ✅ | `GET /api/recommendations/platform-availability` 返回 `{total_available, by_platform}`，来自 storage 的单次隔离快照，`total_available == sum(by_platform)` 恒成立，且与平台定向选片同一 servability 口径。读取失败返回可诊断 5xx，前端保留上一次成功快照，绝不把失败当成全零 |
| 恢复标签页读取合并 | ✅ | `GET /api/recommendations` 使用最长 1 秒的进程内快照与 `asyncio.Lock` single-flight，把浏览器恢复几十个旧标签页时的同形昂贵历史读取合并为一次；桌面 Web 本地已有卡片时恢复水合直接跳过该 GET，仅同步 runtime / 库存，避免首屏补池副作用；空列表和明确刷新仍走读取。返回前逐行复核 temporal v2 三态，并把快照 deadline 截短到最早一条内容的 deadline 或 `next_review_at`。deadline 先锚定 monotonic clock、再读取 wall clock，两个时钟采样之间的调度延迟只会缩短缓存而不会越过 hold/expiry 边界；返回值 deep-copy，reshuffle / append / feedback 会立即失效快照。逐卡 `/api/saved/{list_kind}/status` 采用同窗口有界短缓存，并在 save/remove 时按 item 失效，不改变交互一致性。 |
| M123 上游来源配额补货 | ✅ | discovery pool 低于目标值时，runtime 会按前端可换口径计算来源缺口，并用 raw-material headroom 限制请求量，减少推荐层长期面对“explore 过满、trending 过少”的偏池子 |
| M124 generate 路径丰富度修正 | ✅ | `generate_recommendations()` 现在也会先对缓存候选做来源均衡，再分阶段放宽 `topic/style/source` 约束，避免高分 `related_chain` 长时间吃掉整批名额 |
| M125 pool 预生成推荐文案 | ✅ | discovery pool 现在会异步批量预生成 `expression/topic_label`，`reshuffle/append` 只消费预生成结果，缺失时返回空而不是写统一兜底 |
| M126 源无关内容分类 | ✅ | `classify_pool_backlog()` 在 `precompute_pool_copy` 前为 legacy / recovery 未分类内容补上 `style_key` / `topic_group` / `relevance_score`，并在批量评估 prompt 中带上近期 `negative_examples`。正常来源 ingest 已改为先走 `discovery_candidates` 统一评估，推荐层不再承担外站原始候选的首评估。COALESCE 保护已分类字段不被重复入库覆盖。`_diversity_tokens` 不再 fallback `source_strategy`——推荐层只看内容特征，来源完全透明。v0.3.162+：`_rows_to_discovered` 回读全部互动字段与 `author_name`，backlog 重写不再把 favorite/comment 等七个计数清零（往返保真有回归测试）。 |
| M127 兴趣探针用户确认 | ✅ | WebSocket 推送 `interest.probe` → Chrome 通知 → popup 卡片（确认喜欢 / 暂时搁置 / 确认不喜欢 / 多聊聊）→ `POST /api/interest-probes/respond` → speculator confirm/defer/reject/chat。4h 去重冷却。推送从 `_run_refresh_plan` 移到 `run_forever` 主循环 |
| M127b 避雷探针用户确认 | ✅ | WebSocket 推送 `avoidance.probe` → popup / Web / OpenClaw 卡片（确认避雷 / 搁置避雷 / 不是雷点 / 多聊聊）→ `POST /api/avoidance-probes/respond`；确认后写入 `disliked_topics` 并清理候选池，未确认时不参与过滤 |
| Issue #147 聊聊口味 Markdown 回复 | ✅ | 主聊天和惊喜推荐 / 兴趣探针的内嵌聊天由共享安全 renderer 渲染 AI 回复中的常用 Markdown；三端支持加粗、斜体、列表、代码块、引用和安全 `http(s)` 链接，用户消息与不安全 HTML / URL 分别保持纯文本或被转义。 |
| Issue #184 AI 文案换行保留 | ✅ | 推荐理由与惊喜理由等 AI 生成文案在插件、桌面 Web 和移动 Web 统一保留换行（`white-space: pre-wrap`），不再把多行输出折叠成一整段。 |
| M128 CLI delight + probe | ✅ | `openbiliclaw delight` 手动查看惊喜推荐候选；`openbiliclaw probe` 手动列出猜测方向并交互确认/拒绝 |
| 封面视觉加成（可选，需多模态 embedding） | ✅ | `[llm.embedding].multimodal_enabled` + 支持图像的 embedding 模型开启时，「封面↔画像兴趣锚点」跨模态余弦映射为**有界、只加不减**的加成（`_VISUAL_COVER_BONUS_MAX=0.05`），**两条推荐路径一致消费**:①惊喜推荐 `precompute_delight_scores()` 对已达阈值候选加到 `delight_score`（后台，冷未命中可现抓）；②正常推荐 `serve()` 排序把加成并入 relevance 项(`_ranking_key`/`score_override`/MMR `_relevance` 同步)——`serve()` 是延迟敏感热路径,**只读预热缓存、绝不现抓封面**(`allow_fetch=False`),warm 未命中就当轮不加成。兴趣锚点每次只 embed 一次。默认关闭时两条路径的打分/排序都与旧版**逐字节一致**(加成恒 0、不改变谁入选)。**旧内容处理**:开启多模态时,入池早于开关的老候选没有封面向量——`prewarm_pool_covers`(挂在 `prewarm_pool_mmr_embeddings` 上,refresh+启动触发)按池窗口回填封面向量(幂等、只补未热的);在回填完成前,`serve()` 有**公平门**——当批次里已热封面占比 < `_VISUAL_COVER_MIN_COVERAGE`(0.6)时整批不加成,避免"新内容仅因已预热而系统性压过旧内容"。delight 侧因逐条冷补不受影响。跨模态余弦 floor/ceil 已按真实部署数据标定（`_VISUAL_COVER_SIM_FLOOR/CEIL=0.35/0.48`，per-cover max anchor cosine 的 p50/p95，834 covers；换 embedding provider/模型后按 `scripts/calibrate_visual_thresholds.py` 重测，铁律 3） |
| M129 惊喜候选自动预热与回填 | ✅ | delight 运行时统一使用动态阈值：默认底线 `0.75`，保守用户底线 `0.80`，copy-ready 候选池至少有 150 条已打 `delight_score` 且分布足够分散（总体标准差 ≥ `0.08`）时，才按 delight 分数池内 Top 10% 边界抬高阈值；`precompute_delight_scores()` 只读取 `pool_expression / pool_topic_label` 已同时生成的候选，再复用 Evo 的 `relevance_score` 生成 `delight_score`，不再额外调用 Delight LLM。条件写入会把正式文案原子同步为 `delight_reason / delight_hook`，未生成推荐词的内容不会拥有任何 delight 状态；evaluator 的 `relevance_reason` 或 topic 不能作兜底。后台会补齐新候选并修复旧版提前写入的 evaluator reason；`suppressed` 行可参与 copy-ready 回填，但不会作为 pending delight 发布 |
| 用户视觉画像加成（P1，可选，需多模态 embedding） | ✅ | `[discovery].visual_profile_enabled` 且 `[llm.embedding].multimodal_enabled` 同开时，把用户**点赞/踩过**的推荐封面聚成 k 个均值质心（`recommendation/visual_profile.py` 贪心凝聚，复用 `_normalize_topic_keys` 骨架，`DEFAULT_CLUSTER_THRESHOLD=0.50` = cover-pair p99），候选封面↔质心同模态余弦经 **margin 评分**映射为**有符号**加成（见下"margin 几何重设计"）。三点几何设计：①聚类前 `cross_clean_labels`（kNN k=3、`drop_margin=0.08`）剔除落在敌方势力范围的封面（misclick/love-hate 矛盾），**绝不翻转极性**；②聚类后 `contested_pairs` 标记 pos×neg 质心 cosine ≥ `_VISUAL_PROFILE_CONTESTED=0.45` 的 love-hate 区；③打分时 `s_pos − s_neg ≥ margin → boost`、`s_neg − s_pos ≥ margin → suppress`（负值）、`|net| < margin → gray`，contested 区门槛抬高 2×（`_VISUAL_PROFILE_CONTESTED_MARGIN=0.10`，raise-don't-mute）。独立常量 `_VISUAL_PROFILE_*`（`BOOST_MAX=0.05`/`SUPPRESS_MAX=0.08`/`MARGIN=0.05`/`NET_P95=0.114`/`NET_P5=-0.154`，**已实测标定**于 1366 候选；换 provider/模型后按 `scripts/measure_visual_profile_geometry.py` 重测，铁律 3）。**冷启动门控**：per-polarity 地板 `_VISUAL_PROFILE_MIN_FEEDBACK=8`，低于此数该极性不建质心（零质心 → bonus map `{}` → 排序逐字节不变）；per-polarity 而非 total，margin 评分支持单边冷启动（只 pos 过线 → 纯 pos boost、无 suppress）。在 `serve()` 排序上与现有封面↔文本锚点加成**并行叠加**进 `relevance_bonus`。质心构建调度与 P1 bonus 开关分离：`keyframe_enabled` 单独开启时也会构建共享质心，但 P1 cover bonus 仍只受 `visual_profile_enabled` 控制。质心存 `user_visual_clusters` 表（主库，profile-scoped，非 `embedding_cache.db`），由 `rebuild_visual_profile()` 在 `precompute_delight_scores` 同 tick **按 feedback_at 节流重建**；重建经 `get_feedback_covers` 读取**每一条**反馈封面（绕过 pool admission 的 `confidence>=min_score`，避免低置信反馈被静默丢弃→零质心）。视觉质心绑定 embedding fingerprint / dimension，模型切换会重建。热路径 `_visual_profile_bonus_map` 只读内存 + URL-keyed 封面缓存、零 API 零聚类；公平门同 `_VISUAL_COVER_MIN_COVERAGE`。默认关闭/无反馈数据时加成恒 0，排序与旧版逐字节一致 |
| 弹幕文本加成（P2，可选，**无需**多模态 embedding） | ✅ | `[discovery].danmaku_enabled` 开启时，把视频弹幕清洗成语义摘要并作为**独立排序信号**。动机：B 站候选喂给推荐的语义只有 `title` + `description`，而 description 常是"求三连"之类的无信息文本、`body_text` 在 B 站路径恒为空；弹幕是 B 站独有的高质量信号。抓取走 `comment.bilibili.com/{cid}.xml`（**无鉴权、纯 XML**，标准库 `ElementTree` 解析；`cid` 直接从已有 `/x/web-interface/view` 响应读取，**零额外请求**），经 `BilibiliAPIClient` 复用其 `trust_env=False` CN 直连策略与共享限速（铁律 1）。**清洗策略由实测推翻了直觉**：抓取 BV1LR336sEFX 的 3600 条弹幕发现**按频次聚合是完全错误的**——高频弹幕全是社区梗（难说 613×、已取餐 350×、懂你意思 310×、666 9×），语义价值为零；真正有信息量的恰恰是**低频长弹幕**（"这就是本地AI的优势，除了延迟低，还有绝对的隐私性"、"苹果上市后系统优化导致零售机强于媒体机"），全都只出现 1 次。按频次取 top-N 会精准筛掉所有有用信息、只留噪声。但单纯按长度排序也不行——刷屏会顶到最前（`保护`×30 = 76 字但只有一个词、重复句、长串标点）。**最终策略 = 压缩重复（整串周期重复 + 字符 run + 多字单元 run + 标点 run，数字豁免以免把 "5000电池" 压成 "50电池"；多字单元压缩用 lazy 量词 + 数字守卫，修复 "求你了×12"→"求你了"、"看我看我看我"→"看我" 且不误伤数字）→ 剔除停用词梗与高频项（>3 次）→ 按压缩后长度取 top-N**。摘要存 `content_cache.danmaku_text`（**绝不复用 `body_text`**——它渲染到三端卡片正文并进 5 处 LLM prompt，弹幕塞进去会把卡片变成一堆"已取餐"），结果具有 `success / no_data / transient_failure` 明确状态：只有成功空结果才推进抓取状态，HTTP/XML/embedding 失败留待下轮重试。文本嵌入按**完整摘要文本**走稳定 document embedding/cache key，`danmaku_max_chars` 不会被静默截成固定前缀；embedding fingerprint / dimension 变化时已有摘要只重嵌入、不重复抓源数据。`_danmaku_bonus_map` 用摘要向量 vs 画像兴趣锚点（text↔text 同模态）算有界加成，独立常量 `_DANMAKU_*`（floor/ceil 0.30/0.65，**PROVISIONAL/未实测**，有饱和迹象，待更多数据后按分布重标，铁律 3）。预热 `prewarm_pool_danmaku` 挂 `prewarm_pool_mmr_embeddings`、串行、best-effort。`serve()` 上与封面↔文本锚点、P1 视觉画像、P3 关键帧**四路并行叠加**；热路径只读缓存。默认关闭/无数据时加成恒 0，排序逐字节一致 |
| 视频关键帧加成（P3，可选，需多模态 embedding） | ✅ | `[discovery].keyframe_enabled` + `[llm.embedding].multimodal_enabled` 同开时，**用真实视频画面而非封面**匹配共享视觉质心；只开 keyframe 也会触发质心构建，但 P1 cover bonus 仍受 `visual_profile_enabled` 控制。封面是 UP 主手选的营销图、常标题党，不代表内容；B 站已为每个视频预生成关键帧雪碧图（进度条悬停预览），`GET /x/player/videoshot`（**无需鉴权 / 无 WBI 签名**）即可拿到——**一次 61KB 请求 = 100 帧，无需下载视频、无需 ffmpeg**，与抓一张封面同级成本。实测 30 个真实视频（5 分区、45s–5106s）**覆盖率 100%**，平均 277 帧/视频。**两个实测驱动的实现要点**：①长视频返回**多张**雪碧图（实测最多 11 张 = 1100 帧），采样必须**跨全部雪碧图全局均匀分布**，只取 `image[0]` 会让长视频只覆盖开头；②单帧尺寸**不固定**（实测 160×90 与 480×270 并存），必须从响应读 `img_x_size`/`img_y_size`。帧向量取 **max-pool**（"是否有任一帧对味"比均值更适合召回），经 **margin 评分**映射为**有符号**加成（见下"margin 几何重设计"；与 P1 同一 margin/contested/cross-clean 几何，独立常量），独立常量 `_KEYFRAME_*`（`BOOST_MAX=0.05`/`SUPPRESS_MAX=0.04`/`MARGIN=0.05`/`CONTESTED=0.45`/`CONTESTED_MARGIN=0.10`/`NET_P95=0.093`/`NET_P5=-0.081`，keyframe-vs-centroid net 分布，**已实测标定**，99 真实精灵图帧；换 provider/模型后按 `scripts/prewarm_and_measure_keyframes.py` 重测，铁律 3）。缓存键 `keyframe_embedding_cache_key(bvid, frame_idx, sampling_signature, embedding_fingerprint)` 绑定采样算法 / `keyframe_max_frames` 与 embedding fingerprint；结果显式携带稳定 sampled-slot，部分 sprite/crop/task 成功也会缓存成功槽位但保持 `partial`/retryable，绝不把后续槽位重编号。预热 `prewarm_pool_keyframes` 复用已缓存槽位，只有确认 no_data，或采样完整且每个返回槽位 embedding 成功时才写 `keyframes_fetched_at`；部分 embedding / 网络 / 解析 / 精灵图失败留 NULL 下轮重试（铁律 2）。`serve()` 上与封面↔文本锚点、P1 视觉画像、P2 弹幕**四路并行叠加**进 `relevance_bonus`；热路径只读缓存、绝不现抓。默认关闭/无质心时加成恒 0，排序逐字节一致 |
| margin 几何重设计（P1/P3 有符号评分） | ✅ | v0.3.185 的「去 neg 惩罚、纯正向」是对 neg 抵消问题的第一刀——直接砍掉 neg，安全但浪费了 neg 能说话的区域。本轮用几何方法把 neg 安全请回来，**取代纯正向也取代被搁置的 C 方案**（dislike 理由字段）。三点设计：①**聚类前 cross-cleaning**（`cross_clean_labels`，kNN k=3、`drop_margin=0.08`）剔除落在敌方势力范围的封面（misclick/love-hate 矛盾），绝不翻转极性；②**聚类后 contested 检测**（`contested_pairs`，cosine ≥ 0.45）标记 love-hate 区；③**打分时 margin 判定**：`s_pos − s_neg ≥ margin → boost`、`s_neg − s_pos ≥ margin → suppress`（负值）、`|net| < margin → gray`。一个差值自标定（s_pos/s_neg 共享同一 embedding/管线，差值无需单独 τ，0.80 教训的一般化）。contested 区**不静默而是抬高门槛 2×**（raise-don't-mute）——"质心重叠就整对灰掉"会丢掉 ~40% 的 P3 信号（39 个 clear win 被误杀），改成提高说服力门槛但保留 clear win。校准全部来自实测（`scripts/measure_visual_profile_geometry.py`，1366 候选）：2/6 pos×neg 质心对 contested（0.674/0.576 vs 0.377-0.219 有干净断点）、margin 0.05（net p75/p90 间）、boost/suppress scale 从 net p5/p95。实测：vp +107/−280、kf +4/−3（过粗灰门下 kf 0/−1）；方向验证 boost 的是 kigurumi/角色展示/MV。suppress 偏多是诚实读数：用户视觉上 dislike 多于 like。根因（反馈语义不分视觉维度）由几何方案处理，C 方案降级为未来 soul 层（非视觉 dislike 理由→回避 UP/topic）的解耦增强 |
| 跨平台公平性归一化 | ✅ | 四路 bonus 在 `serve()` 叠加成 `combined_bonus` 后、喂给 MMR 选择器前，经 `_normalize_bonus_per_platform` 按 `source_platform` 分组，**以 0 为固定点做正负两侧分段对齐**：多平台时正值按平台内 positive max 对齐到所有平台当前观测到的全局正向最大值，负值按全局负向最大幅度对齐；全局目标受 `_COMBINED_BONUS_CAP` 限制。单平台保持原始绝对幅度（超 cap 时仍限幅），0 / 缺失保持 0，弱正不会变成负惩罚。这样 Bilibili-only 信号（P2 弹幕 / P3 关键帧）不会结构性抬高 Bilibili 候选，也不会凭空把小 bonus 放大到固定 cap；`snapshot_ranking.py` 镜像同一归一化，保证离线快照与 `serve()` 一致 |
| 惊喜推荐反馈保留 | ✅ | `POST /api/delight/respond` 中 `like / chat` 记录正向学习信号并保留候选；`view` 当场保留卡片但标记已读（对齐推荐池 `shown` 语义，下次重灌不再出现）；`dislike / dismiss` 消费候选并驱动三端立即移除。`pending-batch` 重灌以 `include_liked=True` 保留已喜欢候选并下发 `state="liked"`；移动 Web、桌面 Web 与插件统一保留结果提示和完整动作组，只把 like 标为 `aria-pressed="true"` 并禁用重复提交，其它动作继续可用 |
| 惊喜推荐反馈保留与永久叉掉 | ✅ | `POST /api/delight/respond` 中 `like / chat` 记录正向学习信号并保留候选；`view` 当场保留卡片但标记已读（对齐推荐池 `shown` 语义，下次重灌不再出现）；`dislike` 消费候选并记录负偏好；`dismiss` 是三端统一的“× / 看过了，不再推荐”，同时写 `delight_notified` 与 canonical `seen_items`，立即移出当前队列并永久排除普通/惊喜两条推荐出口。桌面 Web 的 × 独立放在卡片标题栏最右侧，不再混入喜欢 / 不感兴趣 / 稍后看 / 收藏反馈组；窄屏保留至少 44×44 的触控目标。`pending-batch` 重灌以 `include_liked=True` 保留已喜欢候选并下发 `state="liked"`；只把 like 标为 `aria-pressed="true"` 并禁用重复提交，用户仍可随后叉掉 |
| 惊喜推送不打断输入（v0.3.157+） | ✅ | 桌面 Web：用户在惊喜卡聊天框互动中（composer 展开 / 输入框有焦点 / 有未发送草稿）时，`delight.candidate` 后台推送只静默入队并更新计数、不切当前卡（此前会 `setActiveDelight` 收起输入框，随后的发送还会把反馈串到换上来的新卡上）；`delight.refreshed` 队列刷新同样只同步数据，当前卡即使已被后端消费也保留引用，发送始终落在用户正对着的卡上；空闲时保持自动切到最新候选的原行为。无惊喜候选时 `renderDelightCover(null)` 只清空旧封面和背景后返回，不读取空候选或打断首页 hydration；有效但无封面的候选仍显示来源平台徽章。移动 Web：输入框聚焦时跳过推送触发的 DOM 重建（textarea 失焦 = 手机键盘收起），草稿本就实时存 state |
| issue #79 桌面惊喜文字卡收尾 | ✅ | 桌面 Web 惊喜卡保留 `body_text` 并显示最多 5 行正文预览，仅在实际溢出时提供可访问的展开/收起；无封面或封面加载失败时，左侧媒体区以正文和来源徽章渲染毛玻璃文字卡。候选切换与空队列重置折叠态，不改变标题兜底、互动指标、聊天输入保护和反馈语义。 |
| issue #126 移动惊喜卡整卡点击 | ✅ | 移动 Web 惊喜卡支持整卡点击打开内容，与下方信息流普通卡片一致；位移 <10px（`DELIGHT_DRAG_DEAD_ZONE`）视为点击，≥50px（`DELIGHT_SWIPE_THRESHOLD`）仍是左右切卡，中间区间不触发任何动作以防误触。反馈按钮 / 输入框在 `pointerdown` 阶段 stopPropagation，不会被整卡点击吸收；已反馈（`show_actions=false`）、聊天输入展开或无可用 URL 时不接管点击。桌面 Web 的普通卡片本就只有动作按钮、惊喜卡另有封面点击区，自身已一致，不在本次改动范围；插件无惊喜卡片，CLI 无此交互 |
| v0.3.0 在线 supergroup 合并 | ✅ | `_merge_topic_supergroups` serve 时基于 embedding 把 `动漫杂谈/补番/解说` 等近义 topic 合并为同一聚类，让多样化器把它们当作一个桶 |
| v0.3.0 reshuffle 性能优化 | ✅ | 三段并发：embedding `asyncio.gather` 并行（替代顺序 await）+ embedding cache key 改为 label-only（命中率 ~0% → ~100%）+ `batch_insert_recommendations` 单 transaction 插入 10 条（10 次 fsync → 1 次）。换一批 2.6s → 0.6s |
| v0.3.0 supergroup embedding 预热 | ✅ | `prewarm_supergroup_embeddings` 在每次 refresh tick 后台并行预热所有池中 topic_group 的 embedding，让 reshuffle 跑全 cache hit |
| v0.3.1 双轴 fatigue + 陡曲线 | ✅ | `PoolCurator` 同时基于 `recent_topic_keys`（细）和 `recent_topic_groups`（粗）算 fatigue 取 max，避免 `动漫杂谈/补番/解说` 等子 topic 各自不触发 fatigue。曲线 `count^1.5/len*5` 让 count=2 即触发 0.47 强抑制；`topic_fatigue` 权重 0.15 → 0.25 |
| v0.3.1 SQL per-group 候选窗口 cap | ✅ | `get_pool_candidates` 用 `ROW_NUMBER() OVER (PARTITION BY topic_group)` 把候选窗口里每个 topic_group 限到 ≤3 条；600 池子 270 个 group 的长尾真正进入候选，distinct 主题数从 ~12-15 提升到 ~18-22 |
| v0.3.44 MMR 多样化 | ✅ | `_select_diversified_batch` 引入 Maximum Marginal Relevance：`score = α*relevance - β*max_cos_to_picked`，靠 embedding 余弦把 LLM 误聚到同一 `topic_label` 伞标签下的硬核内容真正打散。每轮 unique_topics=10/10、top_topic_share≤10% |
| v0.3.45 MMR embedding 提前 warm | ✅ | `warm_mmr_embeddings` 在 discovery 入池 + `classify_pool_backlog` 落库后立即并行 warm L2 SQLite embedding cache（cache key 文本由 `_mmr_embedding_text` 静态方法做 single source of truth），serve() 用 `asyncio.gather` 并行兜底,新增 `MMR embedding fetch: coverage=N/M elapsed=Xms` 埋点。换一批 P50 双峰（0.7s / 6-10s）收敛到稳定 <1s。v0.3.124+（lever 4）：冷启动伴侣 `prewarm_pool_mmr_embeddings()` 返回 `-1`＝没东西可暖(无 embedding service / 空池，良性)、`0`＝有候选但全嵌入失败(后端不可达)、`>0`＝已暖,供启动包装器区分良性冷启动与真故障 |
| 换批默认硬去重 + 批次事件 | ✅ | 桌面 Web、移动 Web 与扩展 side panel 调用 `POST /api/recommendations/reshuffle` 时都会携带当前卡片 ID；桌面平台 Tab 还会排除该平台本会话已加载卡片。API/引擎把 `excluded_bvids` 贯穿到最终过滤，并把候选读取窗口扩大为基础窗口加排除数，避免旧卡因平台保底或 top-40 截断回流。成功换批只写一条 satisfaction-neutral、强度 `0.1` 的 `reshuffle` 批次事件，不再把整屏逐条伪装成 `dismiss`；误导性的“换一批时忽略当前”开关已移除。空响应或失败仍保留当前列表；CLI 是无持久卡片状态的单次输出，不适用列表保留语义。 |
| issue #98 CPU 排序脱离事件循环 | ✅ | `_select_diversified_batch_async()` 与 `_build_supergroup_canonical_map_async()` 通过 `asyncio.to_thread()` 执行 MMR/多样性选择和 supergroup 两两 union-find；同步纯函数仍是唯一算法实现，异步包装保持完全相同的确定性输出。MMR 日志拆分 `selector_worker_ms` 与 `event_loop_resume_delay_ms`，不再把 worker 已完成后主协程迟迟未恢复的停顿误算成算法 CPU 时间。线程主要用于保持 asyncio 响应，不承诺绕过 Python GIL 提升吞吐。 |
| issue #98 SQLite 换批热路径 | ✅ | `PoolServeSnapshot` 在独立 serve DB worker 上先把 temporal `review_due` 的 `fresh` 行转为 `temporal_review_hold`、把 `expired` 行转为 `stale`，再用短生命周期连接、单个读事务统一读取 readiness、候选窗口、平台补位、`seen_items` 和 curator 信号；两类行都不展示、不计 canonical 库存。API 不再前置重复扫描库存。推荐历史写入与 `pool_status='shown'` 在同一独立短事务中原子提交，并在事务内对最终选中行重读完整证据组，避免 snapshot→persist 竞态把刚到复审点或刚确定过期的内容返回给用户。它和后台 maintenance worker/连接彻底分离；读取、维护或精确状态收敛期间 `/api/ping` / runtime stream 仍可响应。`recommendation_request_timing` 记录 profile/snapshot/embedding/selector/resume/persist/total 阶段，详细候选与 MMR 摘要只在 DEBUG 输出；`scripts/benchmark_reshuffle_latency.py` 使用独立预热的 health 连接并发验证尾延迟，避免 HTTP/1 客户端连接池串行化污染结果。 |
| v0.3.57 pool gate on precomputed copy | ✅ | `get_pool_candidates` / `count_pool_candidates` SQL 加 `AND COALESCE(pool_expression, '') != '' AND COALESCE(pool_topic_label, '') != ''` —— 未 precompute 的 row 对 serve() 不可见,消除"discovery 完成→precompute 完成"60–90s 窗口内 popup 显示占位模板的旧 bug。`engine.py:320` 的 `_fallback_expression` 路径变成 race-window 安全网,触发即 `logger.warning("Pool gate leak: ...")` |
| v0.3.66 pool gate on classification | ✅ | `get_pool_candidates` / `count_pool_candidates` 现在同样要求 `style_key` 与 `topic_group` 非空；`get_pool_candidates_needing_copy` 也只挑已分类但缺文案的候选，避免未分类跨源内容先生成 copy 后绕过 serve 分类口径 |
| v0.3.91 servable pool count | ✅ | `count_pool_candidates()` 在读取前刷新 SQLite/WAL snapshot，并默认应用与 `get_pool_candidates()` 相同的 `max_per_topic_group=3` 候选窗口；新增 `count_pool_readiness()` 拆分 `available/raw/pending`；`serve()` 零候选 warning 会输出 `raw/servable/pending`，用于定位“池子有素材但暂不可换”的真实原因。 |
| v0.3.102 空池热路径短路 | ✅ | 真实引擎的 `/api/recommendations/reshuffle` 与 `/api/recommendations/append` 不再做重复 API 库存预查，由 `PoolServeSnapshot` 一次判定；可用池为 0、或候选被 `excluded_bvids` / `seen_items` 过滤到 0 后立即返回空结果，跳过 curator、MMR embedding 和推荐历史写入，并按 30 秒 debounce 触发补货。旧 test double / adapter 没有 `*_with_result()` 时保留 API 前置短路兼容。 |
| durable shown commit callback | ✅ | `serve_with_result()` 只在独立连接已原子提交 recommendation + shown 后返回 `ServeResult(items, pool_counts_after, timings)`，随后 detached 通知 `set_pool_inventory_commit_callback()` 注入的 sync/async hook。API 先用结果中的扣减库存更新 refill gate / 广播，不在响应关键路径重新扫描；再后台读取精确 canonical snapshot 收敛 topic-window 补位等近似差异。写失败不触发 callback，callback 自身失败只记录日志、不取消已完成提交。 |
| v0.3.x PC Web 空推荐展示 | ✅ | 桌面 Web `/web` 不再携带内置演示推荐作为初始 `state.videos`；后端 `/api/recommendations` 返回空数组时必须覆盖并清空当前卡片，和插件 side panel 的空列表语义保持一致。 |
| v0.3.x available-target pool refill | ✅ | `count_pool_available_candidates_by_source()` 按 `count_pool_candidates()` 同口径统计各平台族的真实可换数量；`count_pool_raw_material_by_source()` 统计 fresh / 非 dislike / 未推荐 / 未看过的 raw material（含 `discovery_candidates` 待评估素材）用于 raw ceiling。补池不再因为 raw/linkable B 站库存达到 300 而停在前端 246 可换，raw trim 也不会在可换未达标时把库存压回 `pool_target_count`。 |
| v0.3.x 统一 discovery 待评估池 | ✅ | 正常来源 ingest 不再直接写 `content_cache` 等推荐层分类；B 站 / XHS / 抖音 / YouTube / X / 知乎 / Reddit / Bangumi / Linux.do raw candidates 先进入 `discovery_candidates`，由 discovery pipeline 统一 batch 评估并 admission 到 `content_cache`。`classify_pool_backlog()` 只作为 legacy / recovery 路径处理已在 `content_cache` 中但缺分类的旧行。 |
| 文字来源卡片 + body_text | ✅ | X 推文 / thread、知乎回答 / 文章 / 问题、Reddit post / comment 以 `body_text` 进入推荐池；前端在 `content_type` 为文字态或 `cover_url` 为空时渲染**无封面文字卡**（显示正文而非断图），franchise / diversity / MMR 对空 `cover_url` / `duration=0` 容错；推荐解释 / 评估 builder 的 user_prompt 带上 `body_text`，system prompt 仍保持字节静态（prompt-cache 约定），新 builder 已纳入不变量测试 |
| v0.3.x 统一 discovery 待评估池 | ✅ | 正常来源 ingest 不再直接写 `content_cache` 等推荐层分类；B 站 / XHS / 抖音 / YouTube / X / 知乎 / Reddit / Bangumi / 微博 raw candidates 先进入 `discovery_candidates`，由 discovery pipeline 统一 batch 评估并 admission 到 `content_cache`。`classify_pool_backlog()` 只作为 legacy / recovery 路径处理已在 `content_cache` 中但缺分类的旧行。 |
| 文字来源卡片 + body_text | ✅ | X 推文 / thread、知乎回答 / 文章 / 问题、Reddit post / comment、微博 post 以 `body_text` 进入推荐池；前端在 `content_type` 为文字态或 `cover_url` 为空时渲染**无封面文字卡**（显示正文而非断图），franchise / diversity / MMR 对空 `cover_url` / `duration=0` 容错；推荐解释 / 评估 builder 的 user_prompt 带上 `body_text`，system prompt 仍保持字节静态（prompt-cache 约定），新 builder 已纳入不变量测试 |
| X append 文字形态保持 | ✅ | `append_recommendations()` 从 discovery pool row 还原候选时保留 `content_type/body_text`，避免 X tweet 在续页链路退回默认 `video` 并丢正文；真实浏览器 E2E 覆盖 PC Web、移动 Web 与扩展 side panel |
| Canonical 保存身份 | ✅ | 推荐、append 与 delight 输出保留同一个 `item_key/source_platform/content_id/content_url/content_type`；插件、桌面和移动保存按钮把这五项交给平台中立 `/api/saved/*`。本地保存失败才回滚按钮；平台同步失败保留本地已保存态并展示逐项状态。 |
| v0.3.91 新兴趣放大保护 | ✅ | 新确认兴趣会生成 amplification key，`PoolCurator` 用最近 24h 推荐历史计算滚动占比，超过 25% 的方向会被降权；最终批量选择还会硬限制同一新方向最多 `max(1, floor(limit * 0.25))` 条，避免刚确认的兴趣短期刷屏 |
| v0.3.91 推荐读取索引 | ✅ | `recommendations(created_at, id)`、`recommendations(bvid)`、`events(event_type, id)`、`seen_items(source_platform, content_id)` 与 `content_cache(content_id)` 在数据库初始化时自动创建索引；推荐列表 / activity feed、候选池 `NOT EXISTS` 历史排除和已看账本读取不再退化为事件全表扫描。 |
| v0.3.x 统一 admission 分数防线 | ✅ | `get_pool_candidates()` / `count_pool_candidates()` / `/api/recommendations` 历史读取都会过滤低于 `[discovery].admission_min_score` 的内容；旧低分推荐会标记为 `suppressed_low_score`，防止 observed / 插件来源脏数据继续展示。 |
| v0.3.74 recommendation JSON 容错统一 | ✅ | `RecommendationEngine` 的内容分类、单条表达和批量表达解析都改用 `llm.json_utils`。MiMo / OpenAI-compatible provider 返回 object wrapper、fenced JSON、JSONL、schema echo、pretty-printed singleton object 或 malformed `{ [ ... ] }` 时会优先提取满足字段 predicate 的真实结果；单候选推荐词请求不再因模型返回合法多行 root object 而误判 `ExpressionBatchMalformed`。Delight 预计算当前复用 Evo 结果，不再走独立 batch scorer |
| v0.3.81 批量结果按内容 ID 绑定 | ✅ | 批量推荐文案和源无关内容分类的 prompt 都带 `bvid/content_id`，解析时优先按返回 ID 写回。模型乱序、漏项或只返回部分条目时不再按数组下标把原因写到错误视频；无 ID 且数量不完整的文案批次会降级单条生成，分类批次会标记失败避免错写 |
| v0.3.x 批量文案限流保护 | ✅ | `_precompute_batch()` 遇到 LLM provider rate limit / cooldown / quota 时不再进入逐条 `_try_generate_expression()` fallback；本轮预生成计为 0，保留空 `pool_expression/topic_label` 等后续调度重试 |
| v0.3.x 批量文案错位 / 重复防护 | ✅ | 强化 v0.3.81：**多条**候选缺 `bvid/content_id` 时（不止数量不完整）一律降级逐条生成——位置匹配只对无歧义的单条批次保留，杜绝弱模型乱序导致的文案张冠李戴；新增去重闸，同一句文案被分配给多个不同 bvid 时整组丢弃（宁可不发也不发重复），根治本地小模型上下文截断时「每条理由都一样且对不上视频」 |
| v0.3.x 负反馈表达避让 | ✅ | `_recommendation_profile_summary()` 会把 `preferences.disliked_topics` 带入推荐画像摘要；单条和批量推荐表达 prompt 都要求避开这些主题 / 话术模式，候选明显命中时只能保守说明差异化理由，不得热情背书或把避雷项包装成用户偏好 |
| v0.3.x 推荐出口避雷兜底 | ✅ | `serve()` 从 discovery pool 读出候选后，会按当前 `profile.preferences.disliked_topics` 再做一次过滤：`topic_key/topic_group/pool_topic_label` 精确命中始终硬丢弃，标题、标签、简介、作者名和短正文包含 dislike term 时用于覆盖异步清池尚未完成或清池失败的窗口。若自然语言子串过滤把整个 serve 窗口杀空，则只对该窗口降级为精确 topic 硬禁用并记录 WARNING，避免“视频/内容”等泛化画像项让推荐永久为空；只要模糊过滤仍留下任一候选，原硬过滤行为完全不变。 |
| v0.3.x dislike 即时输出一致性 | ✅ | 单卡 dislike 继续同步标记 processed；已确认主题写入后，历史推荐、1 秒 API snapshot、reshuffle/append、OpenClaw fallback/新生成结果和主动通知都会在最终边界读取最新 effective dislikes。snapshot 命中同时要求 dislike digest 一致；多卡模糊全灭沿用 exact-safe 恢复，单条 push 禁止恢复。普通 dislike 不阻断 discovery 搜索，异步语义清池只优化库存。 |
| v0.3.x 画像输入上限放宽 | ✅ | `_recommendation_profile_summary()` 兴趣 tag 上限 10 → 30 → 64 → 256 且按 weight 降序排序后截断；`disliked_topics` 5 → 16 → 64 → 128（与存储上限对齐，避雷项不再截断）；`_select_relevant_interests()` 的 embedding 候选池按 weight 排序取前 256（与画像兴趣上限对齐，让头部之外的小众兴趣在语义最匹配时也能被选中；`top_k=5` 不变，故注入 prompt 的数量不变；fallback「top-K by weight」语义与实现一致） |
| v0.3.x 文案 / delight 候选 description 对齐 | ✅ | 推荐重评估和批量文案表达的候选 `description` 截断统一对齐到 400 字符（此前 200 / 300 / 280 混用），与 discovery 评估输入一致，避免中文简介在关键句中途被砍。Delight score 当前复用 Evo 结果，不再单独构造候选评分或 reason prompt；MMR 去重 embedding 文本仍保持 `[:160]/[:200]`（它是缓存 key，不动） |
| v0.3.123 推荐画像输入与 discovery 统一 | ✅ | `_recommendation_profile_summary()` 改为直接委托 discovery 的 `build_profile_summary()`，推荐与发现喂给 LLM 的是**同一份**结构化画像；推荐侧因此补齐了之前缺的字段（`values` / `cognitive_style` / `motivational_drivers` / `current_phase` / `life_stage` / `source_platform_mix` / `recent_awareness` / `mbti` / `interest_domains` 等），并随统一一起不再带 `personality_portrait` 总结。`include_active_insights` 形参移除（统一输入恒含 active_insights）；embedding 选出的相关兴趣经 `interests=` 透传 |
| v0.3.144+ 推荐画像上下文缓存前缀保护 | ✅ | 批量池文案、单条实时文案和 legacy/recovery 分类 prompt 已经携带结构化画像；调用 `LLMService.complete_structured_task()` 时会在支持路径上设置 `inject_core_memory=False`。v0.3.147+ 起这些画像 prompt 还会复用共享 `profile_prompt_layers()`：稳定 core / interests 层放前，recent 层放后，并用 `PromptLayerRenderCache` 只替换发生变化的层。Delight score 预计算不再单独调用 LLM |
| v0.3.144 推荐理由双 worker + 默认 30 | ✅ | `_drain_expression_copy()` 不再对所有待生成 batch 一次性 `gather`，而是默认 batch_size=30、用 2 个 worker 顺序领取 batch；真实 provider 并发测试显示 45 条推荐文案偶发 JSON 解析失败，因此推荐理由保持保守批量；批量解析失败会在当前 worker 内先拆半重试，半批仍失败才退到单条兜底；`_expression_lock` 仍串行化多入口，热重载 / shutdown 的 `CancelledError` 不会被当作普通 batch 失败吞掉 |
| v0.3.x demand-driven copy-ready 水位 | ✅ | `count_pool_readiness()` 区分可服务 `available`、已完成正式文案的 `copy_ready`、全部待文案 `admitted_pending_copy`，以及补齐后能进入 topic 展示窗口的 `admitted_pending_available`。正数 `scheduler.copy_ready_target_count` 同时补 `copy_ready` 水位缺口和 `pool_target_count - available` 中已有 eligible 素材可兑现的缺口，领取时 eligible topic 优先；这样 90 条 copy 水位不会在公开可换仍低于 300 时让表达与评估协调器同时休眠，也不会为已饱和 topic 排空深层 backlog。API RuntimeContext、CLI `_build_recommendation_engine()` 与 OpenClaw bootstrap 同时注入钳制后的 copy 水位和公开库存目标；设为 `0` 明确回滚到 legacy drain-all。 |
| v0.3.154 推荐 prompt diet | 🧪 | `_recommendation_profile_summary()` 现在是推荐表达与 legacy 分类的单一画像入口：先 `build_profile_summary()`，再应用 discovery 共享的 `compact_content_prompt_profile_summary()`（20 条核心上下文、48 个兴趣、32 个兴趣域 × 每域 16 个 specifics、12 条近期语境，避雷项不裁剪）。单条表达传入的 `interests=` 内容相关兴趣替换仍保留，替换后再统一 compact。96 / 16 在当前画像只节省 `0.67%` 标准请求输入 token 且严格 replay 仍失败，因此按收益验证要求实验性改为 48 / 16；长尾召回从第 49 项开始，最终是否保留取决于新的 100×3 fail-closed replay。`classify_pool_backlog()` 默认 `batch_size=30`。原定 200+100 `body_text` 截断被 Reddit 100×3 质量门否决，legacy/recovery 分类与 single/batch expression 均恢复完整正文 |
| v0.3.x XHS 自发布内容过滤 | ✅ | `get_pool_candidates` / `count_pool_candidates` / `count_pool_readiness` 及后台整理查询（evaluation / copy / delight）在 SQL 层排除已知的自发布小红书行；`_purge_self_authored_pool_items` 同时匹配 `up_name` 和 `author_name`；self_info 首次到达或变更时立即 purge 已入池内容。`RecommendationEngine` 通过 `xhs_self_info_provider` 回调从 runtime state 获取 nickname，`Database` 保持纯存储层不直接读 runtime state |
| v0.3.x serve 平台保底 | ✅ | `serve()` 装载 top-40 relevance 窗口后、排除过滤前调用 `_apply_platform_floor()`：按 `list_servable_pool_platforms()` 找出窗口内缺席但仍可服务的平台，对每个用 `get_pool_candidates_for_platform(platform, limit=5)` 补拉并按 bvid 去重扩窗（补货时记一行 INFO），避免会话早期 top-40 全是 B站 而知乎 / 小红书 / 抖音标签页长时间空置；下游 MMR / 多样化不变。单平台池（纯 B站 安装）直接跳过，行为零变化 |
### Visual prewarm API

`RecommendationEngine` 构造器接收 `keyframe_fetch_limit`、`danmaku_fetch_limit` 和
`danmaku_max_chars`；`prewarm_pool_keyframes()` / `prewarm_pool_danmaku()` 使用这些边界，
并让 transient fetch / parse / embed failure 保持 retryable。`rebuild_visual_profile()` 在
`visual_profile_enabled` 或 `keyframe_enabled` 任一开启且多模态可用时构建共享质心；P1
cover bonus 仍由前者单独控制。

## 公开 API

桌面 Web 的后台恢复、配置应用和状态水合在本地已有推荐卡片时只同步 runtime / 库存，跳过可能触发首屏 `serve()` 补池的 `GET /api/recommendations`；空列表首屏与用户明确手动刷新仍读取推荐快照。该边界不改变 `reshuffle` / `append` 的显式消费契约。

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
- 从 `content_cache` / discovery pool 取候选时会用持久化 `seen_items` 里的 `source_platform:content_id` 过滤所有已知已看内容；B 站保留 raw BVID 兼容，其他来源不会再因为没有 BVID 而漏过滤，且不受旧版 2000 条事件窗口限制
- 从 discovery pool 进入排序前，会用 `profile.preferences.disliked_topics` 做 serve-time 兜底过滤；API / OpenClaw 在序列化前还会用最新 effective snapshot 复核，覆盖 flat preference 已写但 Soul rebuild 或 in-flight serve 尚未收敛的窗口
- 排序主键先看 `candidate_tier`，再看 `relevance_score`、`last_scored_at/discovered_at`、`view_count`
- 生成结果后会写入 `recommendations` 表，避免下次重复选中
- 每条推荐都会调用 `generate_expression()` 生成 `expression` 和 `topic_label`
- 推荐表达会先从当前画像、偏好摘要、`disliked_topics` 和近期反馈推断 `ToneProfile`，再生成更贴近用户口味且避开长期雷点的“老B友”式文案；内容 `style_key` 只用于决定从人物、场景、信息点或情绪等角度切入，不再把用户语气动态调轻
- 推荐表达和推荐池分类 prompt 自身已经包含 compact 结构化 profile；`_recommendation_profile_summary()` 是单一收口点，统一应用 `compact_content_prompt_profile_summary()`，单条表达仍先把内容相关兴趣放进摘要再 compact，保护长尾兴趣。`LLMService` 会关闭额外 core memory 注入，画像按 core / life / interests / style / recent 分层渲染以稳定缓存前缀。Delight score 预计算直接复用 Evo 评分；卡片理由必须等待 `pool_expression / pool_topic_label` 完整并同步，绝不展示 evaluator 的内部判断 reason
- CLI 展示后会把对应推荐记录标记为 `presented = 1`
- `feedback` 命令会把 `feedback_type` / `feedback_note` / `feedback_at` 写回推荐记录
- 多样性回填会分阶段放宽 `style`、`source`、`topic` 约束，只有候选真的不足时才彻底兜底补满

### RecommendationEngine.reshuffle_recommendations

```python
items = await engine.reshuffle_recommendations(
    profile=profile,
    excluded_bvids=["BV1A", "BV1B"],
    limit=10,
)

# API/runtime 热路径使用同一排序实现，并额外取得提交后库存与分阶段耗时。
result = await engine.reshuffle_recommendations_with_result(
    profile=profile,
    excluded_bvids=["BV1A", "BV1B"],
    limit=10,
)

# 平台定向（PC Web 平台 Tab）：只从该 canonical 平台的候选里选片。
zhihu_only = await engine.reshuffle_recommendations(
    profile=profile,
    excluded_bvids=["BV1A"],
    limit=10,
    source_platform="zhihu",
)
```

行为说明：

- 直接从 `content_cache` discovery pool 里挑选 `fresh` 候选，不等待新一轮 discover 完成
- `excluded_bvids` 是本次换批前仍可见的内容 ID；HTTP 入口接受可选 JSON `{"excluded_bvids": [...]}`，缺省或无 body 时等价于空列表，并会去空白、去重后传入引擎
- 桌面 Web、移动 Web 和扩展 side panel 默认都会提交当前卡片作为排除集；这是换批本身的硬去重语义，不再由用户开关控制
- `source_platform` 是可选 additive 平台作用域，HTTP 入口同名字段接受别名（`xhs` → `xiaohongshu`）并在 Pydantic 边界 canonical 化；未知平台返回 422，绝不静默回退到"全部"或 B 站。省略或空字符串保持旧行为，旧客户端不受影响
- 平台作用域只缩小候选集合：跳过跨平台保底补位，其余排序、多样性、文案读取、推荐历史写入与 shown 消费全部与"全部"路径共用同一实现
- 候选读取窗口会额外加上排除项数量，平台保底补入候选后还会执行一次最终排除，确保旧卡不会被补回新批次
- `*_with_result()` 返回 `ServeResult`；`pool_counts_after` 是无需二次查询即可广播的提交后扣减快照，API 会在响应关键路径之外再发布一次精确库存快照
- 过滤掉已展示、已明确反馈和已降级的候选
- 优先按 `candidate_tier`、`relevance_score` 和最近评分时间排序
- 同一批会优先按 `topic_key` 分桶，每个 topic 先出 1 条，再按分数回填
- 同一批还会对 `style_key` 做软均摊，尽量避免连续塞满“深度专注 / 跟做学习 / 快速扫信息”中的某一种观看状态
- 同一批还会对 `source` 做硬上限，避免 `explore` 或 `related_chain` 把 10 条整批刷满；当前 10 条一批时单一来源最多 3 条
- 对刚确认/刚晋升的兴趣方向会应用 amplification guard：同批命中同一 amplification key 的候选最多 `max(1, floor(limit * 0.25))` 条；MMR 路径和非 embedding 多样化路径使用同一硬上限
- 当还没有补齐不同来源时，新的 `search / trending / related_chain` 候选会优先入选，不会先被重复 `style_key` 卡掉
- 如果高分候选前排被同一 `style_key` 占满，回填阶段会放宽风格限流，优先保证整批数量尽量补到请求上限
- 如果候选缺少 `topic_key`，才退回 `tags` 和标题/来源兜底做软限流
- 快路径现在不会现场调用 LLM，也不会再给整批卡片写同一个 fallback topic；只消费 pool 里已经预生成好的 `expression/topic_label`
- 真实引擎路径不再由 API 单独预查库存；`PoolServeSnapshot` 在同一读事务内判断空池并返回 `items=[]`，随后按 30 秒 debounce 触发后台补货。没有 `*_with_result()` 的兼容 adapter 仍使用旧 API 前置短路
- 若引擎内部发现可用池为 0，或候选被持久化已看账本过滤到 0，会直接返回空数组，跳过 curator scoring、MMR embedding 和推荐历史写入
- 候选读取必须满足统一 admission 分数门：`content_cache.relevance_score >= [discovery].admission_min_score`。API 读取历史推荐时也会过滤 `recommendations.confidence` 低于同一阈值的旧行
- 如果某条候选暂时还没预生成好推荐文案，这两个字段会保持为空，交给前端直接隐藏
- 命中候选后会在 serve DB worker 的同一独立短事务中写入 `recommendations` 并把对应池子项标为 `shown`；只有原子 commit 成功后才调 inventory callback
- API 仅在返回非空新批次时记录一条 `reshuffle` 事件，metadata 保留有界的排除 ID、返回 ID、批次大小与平台作用域；它是中性的批次导航动作，不触发逐内容 `dismiss` 或批量画像负反馈
- API 先用 `ServeResult.pool_counts_after` 发布无需扫描的扣减库存，再在响应关键路径外读取精确 runtime pool 字段并发布收敛快照；其它客户端只同步库存提示，不得因此替换当前推荐列表
- runtime 会把 discovery pool 持续补到 `pool_target_count` 个“真实可换”候选，默认目标现在是 `300`（允许配置到 `600`）；达到目标后停止 discover，等可换数掉回目标以下再补货。raw 素材库存不是 `pool_target_count` 的硬上限：当 topic window、预生成、分类或 XHS token 让 raw 与 available 之间存在折损时，raw 可增长到 `max(pool_target_count * 2, pool_target_count + 120)`，再由 raw ceiling trim 控制成本。补货和 trim 会按 `[scheduler.pool_source_shares]` 做平台级配比，默认保存 B 站 / 小红书 / 抖音 / YouTube / X / 知乎 / Reddit / Bangumi / Linux.do = 5 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1，但除 B 站外默认关闭；显式启用某个平台后才会按保存 share 获得配额。少量补货时 discovery 会收缩 LLM 评估窗口，只评估可被当前平台可换缺口和 raw headroom 吸收的过采样候选
- runtime 会把 discovery pool 持续补到 `pool_target_count` 个“真实可换”候选，默认目标现在是 `300`（允许配置到 `600`）；达到目标后停止 discover，等可换数掉回目标以下再补货。raw 素材库存不是 `pool_target_count` 的硬上限：当 topic window、预生成、分类或 XHS token 让 raw 与 available 之间存在折损时，raw 可增长到 `max(pool_target_count * 2, pool_target_count + 120)`，再由 raw ceiling trim 控制成本。补货和 trim 会按 `[scheduler.pool_source_shares]` 做平台级配比，默认保存 B 站 / 小红书 / 抖音 / YouTube / X / 知乎 / Reddit / Bangumi / 微博 = 5 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1，但除 B 站外默认关闭；显式启用某个平台后才会按保存 share 获得配额。少量补货时 discovery 会收缩 LLM 评估窗口，只评估可被当前平台可换缺口和 raw headroom 吸收的过采样候选
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
- PC Web 使用同一 `append` 契约并在续页完成后更新平台库存 Tab；若焦点仍停在已经离屏的 Tab，DOM 重绘只恢复键盘焦点而不改变当前滚动位置
- 会先排除前端已经展示过的 `excluded_bvids`
- 若 API 看到 `pool_available_count=0`，会立即返回 `items=[]` 并按 30 秒 debounce 触发一次后台补货；不会读取画像或进入推荐引擎
- 若 `excluded_bvids` 或持久化已看账本把候选清空，引擎直接返回空数组，不执行 curator、MMR embedding 或推荐历史写入
- 仍然走 discovery pool 快路径，不等待新一轮 discover 完成
- 从 pool row 还原 `DiscoveredContent` 时会保留 `item_key/content_type/body_text/content_id/content_url/source_platform`；X tweet / thread 在 append 续页里也必须继续按文字卡渲染
- 同样复用 `topic_key + style_key + source` 的多样性选择逻辑，并只读取 pool 内已预生成好的推荐文案
- 追加命中的内容也会立即写入 `recommendations` 表；池子项的 `shown` 标记与 inventory callback 异步提交
- API 层在 `append` 返回后会发布最新 `refresh.pool_updated` 池子快照，便于其它 surface 更新“还剩几条可换”；正在浏览的列表保持原样，只有用户继续滚动或主动换一批才消费更多候选
- 同样接受可选 `source_platform`；平台定向续页的每一条都必须属于该平台。当平台定向批次不足 `limit` 时，API 复用现有 `request_replenishment(..., force=True)` 唤醒后台补货——只是唤醒既有链路，不在 HTTP 请求内同步跑 discovery，也不承诺本次立即补满

### 平台库存

```python
GET /api/recommendations/platform-availability
→ {"total_available": 37, "by_platform": {"bilibili": 18, "zhihu": 7, "reddit": 0}}
```

行为说明：

- 供 PC Web 平台 Tab 显示"该平台还有几条可立即推荐"，与 `count_pool_candidates()` / `serve()` 完全同口径
- `total_available == sum(by_platform.values())` 由 storage 单次隔离快照保证；"全部" Tab 的数字取自同一份 snapshot 的 `total_available`
- 零库存平台可省略，前端对已启用但缺失的键显示 `0`
- 读取异常返回可诊断 5xx；前端保留上一次成功值，首次尚未成功读取时显示未知态，不把失败伪装成 `0`
- 该接口是只读的，刷新库存不消费候选池，也不会重载或覆盖已经 append 的推荐卡片

推荐历史、换一批、续页以及 delight 输出共享五字段身份契约：`item_key`、raw `content_id`、`source_platform`、authoritative `content_url`、`content_type`。兼容字段 `bvid` 对 B 站继续暴露 raw BV ID；跨平台关联优先使用 `item_key`。旧 recommendation 只有在 exact storage key 未命中且 raw `content_id` 在 cache 中唯一时才允许 fallback，避免不同平台同裸 ID 被任意串联。

### RecommendationEngine.precompute_pool_copy

```python
count = await engine.precompute_pool_copy(
    profile=profile,
    limit=60,
)
```

行为说明：

- 从 discovery pool 中筛出已具备 `style_key / topic_group`、但还缺 `pool_expression / pool_topic_label` 的 fresh 候选
- 低并发批量调用 `generate_expression()` 的 LLM 主链生成朋友式推荐文案；默认 batch_size=30，默认 2 个 worker 并发处理 batch，避免大 backlog 一次性创建过多 LLM 任务
- 解析批量 LLM 响应时通过共享 JSON helper 接受 `results/items/data/output` 等 wrapper、fenced JSON、JSONL、pretty-printed singleton object 和回显 schema 后的最终结果，但仍要求每条结果具备推荐表达所需字段
- 批量 prompt 会把每条候选的 `bvid/content_id` 交给 LLM；如果响应带回 ID，写库时按 ID 匹配，不信任数组顺序。响应没有 ID 且数量不完整时会降级到单条生成，避免把后续视频的文案整体前移
- 批量池文案与单条表达 prompt 保留完整 `body_text`；200+100 head/tail 方案在真实 Reddit 质量门中造成明显排序与准入回归，不能用 token 节省覆盖内容语义
- 批量调用若命中 provider 限流 / cooldown / quota，不会再逐条调用 LLM；这些候选继续保持文案空值，等待下一轮后台预生成
- 批量响应解析失败、缺少可验证 ID 或产生跨视频重复文案时，后台 drain 会在当前 worker 内递归拆半重试；只有拆到单条仍失败时才走单条表达兜底，因此默认 30 条 batch 不会因为一次弱模型输出异常直接放大成 30 个并发请求
- 批量文案和推荐池分类调用复用 prompt 内 compact profile，并在兼容的 LLMService 路径上跳过额外 core memory 注入；这些调用还会复用共享画像分层缓存，画像核心 / 兴趣不变时保持前置 prompt block 完全相同。这只改变 token / prompt-cache 形态，不改变排序、入池 gate、评分 rubric 或文案策略。Delight score 预计算已改为零 LLM 的 Evo 结果复用路径
- 批量文案并发由 `_expression_lock + expression_batch_concurrency(default=2)` 控制：多入口不会抢同一批候选，同一次 drain 内也只会有两个文案 batch 同时打 LLM；拆半重试在 worker 内串行执行，不额外创建嵌套并发任务
- `copy_ready_target_count > 0` 时，每次 drain 都在 expression lock 内按 `max(copy_ready 水位缺口, min(公开 available 缺口, admitted_pending_available))` 重新钳制领取量，并先领取补齐后能进入 topic 展示窗口的行；只有 copy-ready 水位本身不足时才继续领取深层 backlog。API、CLI 和 OpenClaw 在构造引擎时同时注入钳制后的 copy 水位与 `pool_target_count`。`copy_ready_target_count = 0` 保留旧版排空全部 pending backlog 的显式回滚语义
- 成功后把结果回写到 `content_cache.pool_expression / content_cache.pool_topic_label`
- 生成失败时不会写 profile 级统一 fallback，而是保留空值，交给 popup 隐藏
- runtime refresh 会在补货后自动触发这一步，避免 popup 的“换一批 / 继续追加”现场等待 LLM
- 即使当前没有普通推荐文案要补，runtime 启动时也会走一次 `limit=0` 的预热路径：delight scorer 只领取正式推荐文案已就绪的候选，并把达到门槛的行同步成可推送候选
- v0.3.124+（lever 2b）：文案生成逻辑抽到 copy-only 的 `_drain_expression_copy()`（不 spawn classify / delight，避免递归）；`precompute_pool_copy` 复用它（对外行为不变），而 `_safe_classify_pool_backlog`（detached classify 包装）在 classify 出新条目后会**当场 await 一次 `_drain_expression_copy`**——刚分类好的候选在同一周期就补上文案、立刻可服务，不必等下一个 60s 刷新 tick；共享 `_expression_lock` 串行化两条路径，杜绝重复花 token

### RecommendationEngine.precompute_delight_scores

```python
count = await engine.precompute_delight_scores(
    profile=profile,
    limit=30,
)
```

行为说明：

- 只从 fresh / shown / suppressed 池子里领取 `pool_expression / pool_topic_label` 已同时生成、仍需打分或修复快照的候选；推荐词未完成的行继续只属于 expression-copy backlog，`delight_score / reason / hook` 都保持未进入状态
- 直接复用 Evo 写入的 `relevance_score` 作为 `delight_score`；历史行如果保留了旧版 `delight_score` 标尺，会被重新纳入 backfill 并同步到当前 `relevance_score`
- 正式文案就绪后，低于当前 delight 阈值的候选只写分数、不写 `reason/hook`，避免下轮重复处理
- 高于阈值时，存储层只接受与当前 `pool_expression / pool_topic_label` 精确一致的 `delight_reason / delight_hook`，并在同一个条件 UPDATE 中写入分数与快照；文案缺失或读取后发生变化时整次晋级失败、留待下轮
- evaluator 的 `relevance_reason`、`topic_group`、`topic_key` 和 `style_key` 都不能成为惊喜状态或展示兜底；历史行里提前写入的 evaluator reason 或过期文案会在正式文案就绪后因快照不一致被重新领取并修正
- profile floor 或池内动态阈值升高后，旧分数已低于新门槛但仍带 `reason/hook` 的行也会重新进入 backlog，由 scorer 清空展示快照并释放普通推荐占位
- 候选出池阈值与运行时 `pending delight` 查询共用同一套口径：先取 profile 默认底线（默认 `0.75`，探索开放度较低时 `0.80`），copy-ready 候选池已打 `delight_score` 样本不少于 150 条且总体标准差不低于 `0.08` 时，再用 `max(profile floor, delight_score Top 10% boundary)` 抬高门槛；未生成推荐词的旧分数不参与校准，样本不足、分布过于同质或初始化阶段回退 profile 默认底线
- `get_pending_delight()`、pending batch、手动触发、CLI 与候选计数共用同一发布闸门：正式文案两个字段非空，且 `delight_reason / delight_hook` 分别与它们一致；因此既不会收到空字段，也不会收到旧 evaluator reason
- **与 `DelightScorer` 的关系（读代码前先看这条）**：`recommendation/delight.py` 里的 `DelightScorer`（embedding 多信号打分器）**当前不在生产链路上**——`src/` 内没有任何实例化点，生产代码只从该模块引用 `effective_delight_threshold` / `DEFAULT_DELIGHT_THRESHOLD` 两个阈值工具。线上 `delight_score` 完全由本函数复用 Evo 的 `relevance_score` 产出（为省一次 LLM 调用的有意决策，见函数 docstring）。因此改动 `DelightScorer` 内部信号（quality / novelty / exploration 等）**不会改变任何当前推荐输出**，只有把 scorer 重新接回线上时才会生效；`DelightScorer` 的单测覆盖也只保证该类自身行为，不构成对线上排序的验证。目录评分（`rating_score` / `rating_count` / `source_rank`）实际是经 `discovery/engine.py` 的 `_prompt_visible_content_fields` 在非零时进入 evaluator prompt，由 LLM 在语境中权衡后体现在 `relevance_score` 上——而不是靠打分公式里的常量

> **delight 分的唯一产出路径（v0.3.174+）**：线上 `delight_score` 只由 `precompute_delight_scores()` 复用 Evo 写入的 `relevance_score` 产出——这是为省掉每条候选一次 LLM 调用的有意决策。目录评分类信号（`rating_score` / `rating_count` / `source_rank`）通过 `discovery.engine._prompt_visible_content_fields` 在非零时进入共享 evaluator prompt，由 LLM 在语境中权衡，**而不是**在推荐层再算一遍加权公式。`recommendation/delight.py` 因此只保留阈值口径（`DEFAULT_DELIGHT_THRESHOLD` / `CONSERVATIVE_DELIGHT_THRESHOLD` / `effective_delight_threshold()`）。该模块历史上还有一个 embedding 多信号打分器 `DelightScorer`（deep_need / insight / likes / novelty / quality / exploration 加权），它从未接进上述链路、生产零调用点，已随本版删除；若将来 delight 真需要独立打分，应接进 `precompute_delight_scores()`，不要再起一条平行打分路径。

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
- `rating_score` — 来源目录评分，0 表示未知
- `rating_count` — 参与目录评分的人数，0 表示未知
- `source_rank` — 来源目录排名，0 表示未知；正数按原始序号 `#N` 展示，不使用“万/亿”计数缩写
- `body_text` — 纯文字内容主体（X 推文 / thread 全文或 `note_tweet` 长文、知乎回答 / 文章摘要、Reddit post / comment 正文、微博正文）；视频 / 图文源留空
- `content_type` — 内容形态：`video`（默认）/ `note`（小红书）/ `tweet` / `thread`（X）/ `answer` / `article` / `question`（知乎）/ `post` / `comment`（Reddit）/ `subject`（Bangumi）；微博固定使用 `post`

### 文字卡渲染（X / 知乎 / Reddit / 微博 / 无封面内容）

X、知乎、Reddit 和微博都可能返回没有封面、主要价值在正文里的候选。推荐卡前端（移动 Web `/m`、桌面 Web `/web`、扩展 side panel）在 `content_type ∈ {tweet, thread, answer, article, question, post, comment}`（或 `cover_url` 为空）时渲染**无封面文字卡**：显示 `body_text` / `title` 主体，而不是断图缩略图。`RecommendationEngine` 的 franchise / diversity / MMR 逻辑对文字内容做了容错（`cover_url` 空、`duration` 0 不报错）。LLM 侧，推荐解释 / 评估 builder 的 **user_prompt** 会带上 `body_text`（纯文字内容标题信息量低，正文才是判断依据）；严守 prompt-cache 约定——system prompt 保持字节静态，`body_text` 等 per-call 变量只进 user message，`json.dumps(..., ensure_ascii=False, indent=2, sort_keys=True)` 确定性序列化，新 builder 已纳入 `test_prompt_builder_system_messages_are_call_invariant`。

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
- `content_url` 缺失时，后端只对 B 站、YouTube、抖音、X、Reddit、Bangumi、微博构造来源感知的安全 fallback；Bangumi 使用 `https://bgm.tv/subject/<id>`，微博使用 `https://m.weibo.cn/detail/<id>`，小红书仍要求已有带 token 的 URL，避免生成不可打开的裸链接

### Recommendation Feedback

当前推荐记录会持久化以下反馈字段：

- `feedback_type`
- `feedback_note`
- `feedback_at`

推荐反馈会同时写入事件层，供后续偏好和洞察分析消费。
事件沿用推荐候选的真实 `source_platform`；旧推荐记录没有来源字段时兼容回退为
`bilibili`，避免把知乎等跨源反馈误记成 B 站行为。

桌面 Web 的 `like / dislike / dismiss` 使用 10 秒客户端提交屏障：卡片状态立即变化，撤销期间后端还没有写入；倒计时结束或页面离开时才调用 `/api/feedback`，失败则回滚卡片状态。`comment` 必须携带文本并可能产生直接学习语义，因此不进入这条延迟提交路径。

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

`POST /api/delight/respond` 支持 `view / like / dislike / chat / dismiss`。`like / chat` 只记录喜欢或对话学习信号，候选保留在队列里；`view`（看看/点开浏览）保留当场卡片的「已打开」展示，但会把候选标记为已读（`delight_notified=1`，不重置 4 小时主动推送冷却）——语义对齐推荐池的 `pool_status='shown'`：浏览过的惊喜在下次队列重灌时不再出现；`dislike` 立即移除并记录负偏好；`dismiss` 对应移动、桌面和扩展统一的“× / 看过了，不再推荐”，先把内容 canonical identity 写入 `seen_items`，再置 `delight_notified=1`，因此后续普通推荐与惊喜推荐都硬排除。

正向保留跨重灌生效：`GET /api/delight/pending-batch` 以 `include_liked=True` 调用 `get_delight_candidates`，已点喜欢（`feedback_type='like'`）的候选在 popup 重开 / `delight.refreshed` 重灌后仍保留队列位置，并以 `state="liked"` 下发供三端恢复「已喜欢」展示；`view` / `dismiss` / `dislike`（置 `delight_notified=1`）会让候选退出重灌队列。所有 delight scoring、动态阈值、计数与 pending 查询还统一叠加 `seen_items` guard，外部浏览、点赞、收藏或投币已经进入已看账本的内容不会占据惊喜栏位。WS 主动推送（`get_pending_delight`）、候选计数与 CLI 仍排除已喜欢项，避免把喜欢过的内容当新惊喜重复推送。

图形端把结果提示、动作组和 like 的可访问状态分开投影；`handled` 只保留为 `viewed / rejected` 的兼容终态标记，不再用来隐藏 liked 的动作组：

| `state` | `show_status` | `show_actions` | `like_pressed` | `like_disabled` |
| --- | --- | --- | --- | --- |
| `pending` | 有响应文案时显示 | 是 | 否 | 否 |
| `liked` | 是 | 是 | 是 | 是 |
| `viewed` | 是 | 否 | 否 | 是 |
| `rejected` | 是 | 否 | 否 | 是 |
| `chatted / chatting` | 有响应文案时显示 | 是 | 否 | 否 |

因此本地 like 成功、`pending-batch` 重灌返回 `state="liked"`，以及 `delight.liked` 实时事件都会收敛到同一 UI：状态文案保留，like 仅阻止重复提交，「看看 / 稍后再看 / 收藏 / 不感兴趣 / 聊一聊」仍可继续操作；like POST 失败则恢复未选中状态供重试。

惊喜与普通推荐互斥：被惊喜通道认领的内容（已作为惊喜送达过，或 delight 分数达动态阈值、正式 `pool_expression / pool_topic_label` 已就绪，且 `delight_reason / delight_hook` 已同步为它们的精确快照）会被 `get_pool_candidates` / `count_pool_candidates` 的 servable 闸门排除，普通推荐 serve 与「还有 N 条」计数都不会再出同一条内容。认领不接受可能来自旧版的任意非空 `reason/hook`；尚未生成正式文案或快照尚未同步的行不会被认领，前者仍可进入 expression-copy backlog，后者等待 profile-aware scorer 决定是否进入惊喜。

### PoolCurator

```python
from openbiliclaw.recommendation.curator import PoolCurator
```

`PoolCurator` 提供推荐侧的独立评分。它从候选池读取 Evaluation Agent 已持久化的语义特征，并按照一套专属权重对每条候选打分。相关性始终保持时间中性；时效分成两层：证据驱动三态 eligibility 负责可展示、待复审与确定过期的生命周期，publication bonus 只负责合格内容之间的优先级。

发布时间偏好由 `PublicationDatePreference` 提供独立覆盖层，按来源配置
（`[sources.<name>].recommendation_date_*`）。`all` 保持旧行为；其它预设或 `custom`
按用户本地自然日解析为包含式 UTC 边界。发现策略在 LLM 评估之前会直接过滤掉范围外候选，
不消耗评估预算。候选池打分保留 `weight` 语义：范围外候选的最终分数乘以
`1 - weight`，默认 `weight=0.5`；`weight=1` 时，`RecommendationEngine` 在 MMR 和最终选择前
忽略这些候选。候选不会因此从数据库或候选池删除。缺失发布时间在活动范围
下视为范围外，不会拿发现时间冒充发布时间。

数据库的有效库存读取也复用严格 eligibility：物理 `content_cache` 行保持不变，`count_pool_candidates()`、
来源配额统计、候选池 fullness 和刷新补货只把当前范围内的 B 站行计入目标。软模式仍把范围外行计入
有效库存，因为它们可以经过乘数降分后服务。

#### ScoringWeights

| 维度 | 权重 |
|------|------|
| `relevance` | 0.30 |
| `freshness` | 0.10 |
| `topic_fatigue` | 0.25 |
| `source_monotony` | 0.15 |
| `serendipity` | 0.20 |

`serendipity` 加分只对 `explore` 来源发放（满额 1.0）。其余任何 strategy —— 包括 `trending` —— 一律为 0.0：来源只是上下文，不能凭发现路径白拿 rec_score（issue #90）。

`freshness` 现在表示 publication-time temporal bonus，不再读取 `discovered_at`。只有 `breaking/current/versioned`、有效 `published_at` 且分类置信度至少 `0.60` 的候选参与：半衰期分别为 1 / 14 / 60 天，类别权重为 0.85 / 0.60 / 0.30；置信度 `>=0.80` 使用完整权重，`>=0.60 且 <0.80` 使用半量，低于 0.60 为 0。`evergreen/historical/unknown`、缺失/无效/明显未来时间全部保持中性，不会把发现时间冒充发布时间。

三态 eligibility 与 bonus 共用 `discovery.temporal` 的类别策略、置信门和发布时间解析，避免 admission、排序、serving 三套规则漂移，但两者语义独立。Agent 的 v2 证据组为 `class/confidence/reason + validity_mode/valid_until/scope/evidence/state`；代码拥有 `evaluated_at/next_review_at/policy_version/evidence_complete`。只有置信度 `>=0.80`、整组完整、`scope=core` 且 evidence 可在 Agent 实际看到的 prompt projection 中逐字 grounding 时，已过 `explicit_deadline` 或事件 `expired` / 版本 `superseded` 才返回 `expired`。deadline 证据必须明确写出日期、时刻和时区并与 `valid_until` 完全一致，终态证据必须正向明示“已结束 / 已替代”；日期-only、反向表述、`hook`、低置信、缺字段、未 grounding、无效时钟与不一致的 mode/state 均 fail-neutral，不解析 `temporal_reason` 猜截止日。

年龄曲线只负责“多久再看一次”：`breaking/current/versioned` 分别在评估后 1 / 14 / 60 天进入 `review_due`，不是内容死亡线；明确 deadline 的复核点就是 deadline。旧 v1 `breaking/current` 行跨过原 3 / 60 天窗口时也只进入 `review_due`；`versioned` 另设 120 天准入 TTL（v1 legacy 行按年龄触发复审）。`event_state=active` 与 `version_state=active` 会按类别继续安排复审；`evergreen/historical/unknown`、`scope=hook` 和证据不足内容不因固定年龄被挡。

生命周期卡口覆盖三个时机：Evaluation 完成后、写入 `content_cache` 前先做三态 admission；`review_due` 的 discovery 行重新排队，`expired` 行终态拒绝。已经入池的 `review_due` 内容会进入可逆 `temporal_review_hold`，不能展示、不计库存，并由现有 legacy/recovery evaluator 复审；DB 用 1 / 2 / 4 / 8 / 16 / 24 小时有界租约调度重复失败，完整新证据可恢复 `fresh` 并清零租约，`expired` 才转 `stale`。每次 `PoolServeSnapshot` 前动态收敛状态，最终 recommendation + shown 写事务再复核。Engine 只返回真正原子提交成功的条目，因此内容不会因为“排序低但一直刷”最终漏出。

展示快路径也使用同一资格判断：`GET /api/recommendations` / OpenClaw 读取尚未处理的 recommendation history、未读计数和主动通知候选时，会在最终 `limit` 前排除后来进入 `review_due/expired` 的条目；完整历史查询仍保留这些记录，供行为回顾与审计使用。legacy `content_cache` 补分类会原子持久化完整 temporal 证据组并立即收敛 hold/stale 状态；discovery cached-backfill 只读取仍为 `fresh` 且 disposition 为 `eligible` 的行，并完整往返 temporal 元数据，不能把旧证据洗回 `unknown`。普通 raw 重抓同样不能恢复 hold 或 stale；只有完整的新一轮复审结果能恢复。

每次 Curator 评分还会 best-effort 生成 `temporal-ranking-shadow-v1` 聚合审计：把当前含 bonus 排序与“精确减掉本次 bonus”的 no-bonus 反事实比较，记录 Top 10 / 50 / 100 的 overlap、Jaccard、同位置数，以及按时效类别、来源和年龄桶的进入/退出分布。审计发生在 MMR/多样性选择之前，不改变分数、准入或 serving；写入失败只记 WARNING。持久化内容不含 BVID、内容 ID、标题、作者、URL、query 或画像文本，保留上限为 30 天 / 5,000 轮。

#### 关键数据结构

**FeedbackSignals**：追踪用户反馈信号，包含以下字段：
- `disliked_up_mids` — 被 dislike 的 UP 主 mid 集合
- `disliked_topic_keys` — 被 dislike 的话题键集合；每条反馈同时收集候选的
  `topic_key` 与 `topic_group`
- `disliked_franchises` — 被 dislike 内容所属 franchise / IP 集合，用于同 IP 软降权
- `liked_topic_keys` — 被 like 的话题键集合；同样同时收集 `topic_key` 与 `topic_group`

候选评分会在细粒度 `topic_key` 和粗粒度 `topic_group` 两个轴上匹配这些反馈集合。
任一轴命中就施加一次对应的 like/dislike 软调整；两个轴同时命中仍只调整一次。

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
| `breaking` temporal bonus | 半衰期 1 天，类别权重 0.85 |
| `current` temporal bonus | 半衰期 14 天，类别权重 0.60 |
| `versioned` temporal bonus | 半衰期 60 天，类别权重 0.30 |
| temporal confidence 分档 | `>=0.80` 全量；`>=0.60 且 <0.80` 半量；其余中性 |
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

# 聚合比较含 bonus 排序与 no-bonus 反事实；不改变 scores/serving
audit = curator.build_temporal_ranking_shadow_audit(candidates, scores, context)
curator.record_temporal_ranking_shadow_audit(candidates, scores, context)

# 检查候选池健康状态
report: PoolHealthReport = curator.check_pool_health()
```

`score_candidates()` 以叠加覆盖层的形式返回新的分数映射，不会修改传入的候选对象。`PoolCurator` 的所有方法均不修改输入数据。shadow 的年龄桶固定为 `<=1d / 1-7d / 7-30d / 30-180d / >180d / unknown`，用于与 2026-08 历史回放口径连续比较；它只回答“bonus 改了谁的相对位置”，本身不改变 eligibility 或自动调整硬期限。

## 示例：记忆如何影响推荐结果

继续沿用一个典型场景：

- 用户最近连续看“国际时事深度解读”
- 聊天里多次表达“想把国际新闻背后的结构看明白”
- 对“浅层热点复读”内容给过 `dislike`

### 第一层影响：影响 discovery 的相关性评分

推荐模块本身主要消费的是已经入池的候选内容，但候选在进入推荐排序之前，通常已经在 discovery 阶段拿到了 `relevance_score` 和 `relevance_reason`。

这一分数会受到画像影响，因为 discovery 评分的 LLM prompt 会显式携带结构化 `profile_summary`，同时关闭额外 core memory 注入以避免同一画像重复出现。于是系统更容易把下面这类内容打高分：

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

`classify_pool_backlog()` 对旧版本遗留、人工导入或异常恢复后已经在 `content_cache` 里但尚未分类的内容做 batch 评估时，也会从事件层读取近期 negative exemplars 并作为 `negative_examples` 传给同一个 evaluator prompt，默认 batch size 为 30。正常 XHS / 抖音 / YouTube ingest 现在先进入 `discovery_candidates`，由 discovery pipeline 统一评估后才 admission 到推荐池；这条分类路径保留为 recovery 安全网。

### 第四层影响：反馈回流到下一轮推荐

当用户对推荐点 `like` / `dislike` / `comment` 时，会同时发生几件事：

1. 更新 `recommendations` 表中的反馈字段
2. 追加一条 `feedback` 事件到事件层
3. 把对应 `content_cache` 项标记为 `feedbacked`
4. 若是 `dislike`，候选池查询会直接把这条内容排除
5. 当新反馈累计到阈值后，再统一触发偏好重分析；主题一旦写入 flat preference，推荐出口立即生效，不等待完整画像重建

所以反馈的影响分成两档：

- **即时影响**：这条不喜欢的卡片同步变为不可操作；已确认主题一落入 flat preference，相关历史/缓存/换批/通知立即按最新快照过滤
- **延迟影响**：单次点踩是否上升为主题 dislike 仍由学习阈值控制；异步 Soul rebuild 和语义清池随后改善 discovery 打分与库存效率

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
8. **单卡与主题分层**：推荐反馈不会逐条推断整个主题；卡片身份同步隐藏，累计证据确认主题后再立即应用到所有推荐输出，降低误杀
9. **推荐语气跟着用户而不是内容类型变**：表达风格会根据画像和近期反馈推断 `ToneProfile`，但不会因为某条内容是轻聊天、日常或审美浏览就自动把语气调轻；`style_key` 只影响推荐理由的切入角度，避免同一个助手在不同内容之间人格漂移。
10. **缓存候选不能退化成只看播放量**：一旦从 `content_cache` 回读候选，也必须恢复 `relevance_score`、`candidate_tier` 和时间字段，保持与实时发现同一排序标准
11. **候选池先可展示，再做文案增强**：`discover` 入池时就要带 `relevance_reason`，popup “换一批”先秒级从池子里出片，`expression` 只是增强层，不再阻塞展示
12. **同批推荐需要显式做多样性约束**：高分不是唯一目标，排序后仍要对重复 topic/tag 做软限流，避免一批里全是同一类内容
13. **多样性要优先吃稳定 topic_key**：只靠 `tags` 不够稳，推荐层现在会优先使用 discovery 入池时生成的 `topic_key` 做分桶，再退回 `tags`
14. **topic 多样性还不够，要再控风格**：用户体感里的”全是很干很学术”往往不是同一 topic，而是同一种内容风格，所以 `reshuffle` 现在会同时约束 `style_key`
15. **快速换一批也要有说话味道**：快路径可以不等完整 `expression`，但不能直接退化成生硬说明句；当前 fallback 会按 `style_key` 生成更自然的短文案
16. **10 条一批必须加来源硬上限**：批量变大后，单靠 topic/style 还不够；现在 `reshuffle` 会同时控制 `source`，避免整批重新被 `explore` 或 `related_chain` 吞掉
17. **来源补齐优先于风格重复**：如果 `trending` 还没出场，就不该因为它和 `search` 同属 `social_chat` 或 `quick_scan` 而被挡在批次外；先让不同来源进来，再做风格均摊
18. **下游挑得再花，也救不了偏掉的池子**：推荐层的多样性约束只能做第二道保险；真正想让一批内容更丰富，必须让 runtime 在补货时先把各来源补到合理区间
19. **legacy 分类也要消费负反馈样本**：正常来源候选会先进入 `discovery_candidates` 统一评估；如果旧行、人工导入或异常恢复让内容已经在 `content_cache` 里但缺 `style_key/topic_group/relevance_score`，这条补评估路径也必须和 discovery batch evaluator 一样读取 `negative_examples`
20. **分类先于推荐文案**：`precompute_pool_copy` 只处理 `style_key/topic_group` 已补齐的候选。未分类内容应先走 `classify_pool_backlog`，否则文案、topic label 与后续多样性/负反馈口径可能不一致。
21. **新确认兴趣只应被轻推，不应刷屏**：探针确认是用户给出的方向许可，不是 24h 内把同一方向塞满推荐流的理由；滚动预算与同批硬上限必须同时存在，前者降低排序冲动，后者防止最终回填阶段破坏体验。
22. **文案 malformed 只追缺项且严格有界**：默认 API/daemon 路径中，成功响应的唯一 keyed 文案立即落库，缺失/重复成员共用 depth=3、最多六次额外 provider 请求的预算；永久 malformed singleton 保持 copy-pending，不再递归调用单条表达。OpenClaw one-shot 显式将该预算设为零，保留有效 subset 并把缺项留给下一请求。provider transient 原样交给 coordinator，按 15/30/60/120/300 秒退避。
23. **LLM 返回的文本字段必须先验类型再落库**：结构化响应偶尔会把整批结果塞进单个标量字段，`str()` 会把它转成 Python repr，非空校验照样通过，于是脏文案直达用户。所有会持久化的 LLM 文本(推荐文案、`relevance_reason`、`topic_group`)都走 `validated_text_field()` 判类型，非字符串按该项失败处理并 WARNING,不做静默兜底。
