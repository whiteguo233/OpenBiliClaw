# 内容发现引擎

> 从用户画像出发，在 B 站、小红书、抖音、YouTube、X、知乎、Reddit、Bangumi、Linux.do、V2EX、微博和通用 Web 等来源主动寻找潜在会喜欢的内容。

API daemon 的候选 admission 成功后只同步调用轻量 expression `notify()`，不会 inline 或 await 文案 provider；generation-owned coordinator 按 durable `admitted_pending_copy` 连续补齐文案，因此评估 worker 可立即继续补位。OpenClaw direct one-shot 没有 daemon owner：它在 admission commit 后 await `expression-copy(limit=4, max_extra_requests=0)`，首 batch 的有效 subset 立即进入 canonical pool，剩余 pending-copy 留给下一次 operation；若首 batch 全部无效，本次可服务池仍可能为空，但不会留下 copy/provider task。`drain_pending()` 会随原有 metrics 返回结构化 post-admission copy receipt；refresh 仅在本轮没有 callback owner 时执行 copy 兜底，避免同一 durable admission 被 controller 收尾再次调用。

## 概述

`discovery/` 包负责把用户的 Soul 画像转换成“可被搜索、可被评估、可被推荐”的候选内容集合。

它解决的不是“某个平台上有没有内容”，而是“面对跨平台海量内容，系统应该先替这个用户去哪里找、找到之后为什么值得留下、怎样避免候选池被单一平台或单一方向刷满”。

可以把 discovery 理解成推荐前的供给层：

- `soul/` 负责理解这个人最近在意什么
- `discovery/` 负责把这种理解翻译成一批值得看的候选内容
- `recommendation/` 再从候选池里挑出这一批最该推的几条

如果没有 discovery，推荐层通常只能在一小撮现成候选里排序；有了 discovery，系统才有能力主动去“找货”，而不是被动等用户自己刷到。

当前模块包含：

- **ContentDiscoveryEngine** — 发现策略编排器，负责注册、运行、去重、批量评估和缓存收口；也提供只拉原始候选的 `produce_candidates()`
- **批量评估输出预算** — 文本与多模态 evaluator 每个 provider 请求最多声明 4096 个输出 tokens；该值覆盖 30 条结构化评分的生产观测范围，同时避免兼容服务按过大的声明上限预占额度并误触发 `insufficient_quota`
- **证据驱动时效三态** — 单条与批量 evaluator 都把候选已有的 `published_at` 和精确 UTC `evaluated_at` 交给 LLM，但 `relevance_score` 只表达内容与画像的相关性，不再因新旧加减分；同一次调用原子输出 `temporal_class / confidence / reason`，以及 `validity_mode / valid_until / scope / evidence / state`。`evidence` 必须逐字来自 prompt 可见正文；本地 grounding 与策略层只在高置信、完整、`core` 的明确 deadline 或 terminal state 上 hard expire，其余年龄到点仅进入可逆复审。代码生成 `evaluated_at / next_review_at / policy_version / evidence_complete`，无效、缺失、标题钩子和低置信结果 fail-neutral
- **B 站近期供给 lane** — 生产 composition 在 `SearchStrategy` 的既有 per-strategy 请求预算内预留 1 个 `order="pubdate"` 请求，最多取 5 条，并与普通相关性搜索按 query/lane 交错进入小候选窗口。它只补近期供给，不改 `source_strategy`、相关性、admission 阈值或来源配额；`discovery_lane="recent"` 只作为可回放 provenance 写入待评估池。发布日期偏好为严格模式时，普通和近期搜索请求都会额外下推包含式 Unix 秒边界 `pubtime_begin/pubtime_end`；软模式不下推，仍保留范围外候选供推荐层降权。
- **渠道短任务不继承 thinking** — B 站 / 小红书 / 抖音 / YouTube / X 关键词生成、通用 Web 抽取、单条 / 批量候选评分经 `LLMService` 时，未指定的 `reasoning_effort` 默认解析为 `""`；不受旧 DeepSeek 全局 `max` 配置影响，也不改动 Soul / 画像的深度任务
- **DiscoveryCandidatePipeline** — 统一候选待评估池的生产 / 入队 / 混源 batch 评估 / 入推荐池 admission 编排器
- **admission.effective_admission_threshold()** — 评估、缓存收口和数据库展示出口共享的纯准入策略；精确 `explore=0.58` 是唯一低于全局门槛的例外
- **DiscoveryCandidateWrite / discovery_candidates** — 原始候选的持久化队列结构，所有来源先落到 `pending_eval`，再由统一 evaluator claim
- **DiscoveredContent** — 统一的候选内容数据结构
- **danmaku.py** — 弹幕文本清洗模块（P2）：纯函数，无 I/O。`collapse_repeats` 压缩整串周期重复（`保护`×30 → `保护`）、字符 run（`还哈哈哈…` → `还哈哈`）与标点 run，**数字豁免**（否则 "5000电池" 会被压成 "500电池"，实测踩到）；`condense_danmaku` 剔除停用词梗与高频项（>3 次）后**按压缩后长度**取 top-N 拼成摘要，并把稳定的 ` | ` 分隔符计入 `max_chars` 预算；条目始终完整，超预算条目跳过而不截断。**选取按长度而非频次是实测结论**：高频弹幕（难说 613×、已取餐 350×）语义价值为零，有信息量的是只出现一次的长弹幕——按频次取会精准筛掉所有有用信息。摘要为空时由上层保留成功空结果与瞬时失败的区别：成功空结果可推进 source 状态，HTTP/XML/embedding 失败不写完成戳；HTTP 200 但根元素不是 B 站 `<i>`（例如 HTML challenge）也是可重试失败（铁律 2）
- **keyframes.py** — 视频关键帧模块（P3）：从 B 站预生成的 videoshot 雪碧图取关键帧，`GET /x/player/videoshot`（无鉴权、无 WBI 签名）拿网格几何与雪碧图 URL，雪碧图经 `runtime.image_cache.get_or_fetch_cover_bytes()` 下载（`hdslb.com` 已在白名单且属 CN 直连），PIL 按 `img_x_size`/`img_y_size`（**必须从响应读，实测 160×90 与 480×270 并存**）切分。`select_frame_positions` **跨全部雪碧图**均匀采样并跳过片头片尾（长视频实测返回最多 11 张雪碧图 = 1100 帧，只取 `image[0]` 会让长视频只覆盖开头）；只下载真正含采样帧的那几张雪碧图。`fetch_keyframes()` 返回带 `success / partial / no_data / transient_failure` 的 `KeyframeFetchResult`，partial 结果额外携带稳定 sampled-slot，避免成功的后续帧重编号；网络、HTTP、解析、精灵图失败不会伪装成空视频。预热可缓存成功槽位但只在确认 no-data，或完整采样且所有槽位 embedding 成功后推进状态
- **multimodal.py** — 封面图准备模块：复用后端封面磁盘缓存与图片白名单抓取边界；`prepare_cover_image_input` 压缩为 JPEG data URL 交给支持图像输入的 evaluator，`prepare_cover_bytes_for_embedding` 压缩为 JPEG bytes 供 `EmbeddingService.embed_image()`（封面 image-only 向量预热与 delight 视觉加成共用同一抓取/压缩路径，不重复下载）
- **SearchStrategy** — 基于画像生成搜索词并调用 B 站搜索的策略
- **TrendingStrategy** — 从全站榜和相关分区榜中筛选高匹配热点内容
- **RelatedChainStrategy** — 从近期高价值视频种子出发，沿相关推荐链扩展候选内容
- **ExploreStrategy** — 消费统一 `KeywordPlanner` 生成的 B 站探索 query 候选池，寻找更有陌生感但仍可解释的内容；planner 关闭时保留旧的现场 domain 生成回退路径
- **PoolDistributionSnapshot** — runtime 在补池前构建的候选池分布快照，给 discovery 提供当前供给拥挤/缺口的软信号
- **inspiration.py / inspiration_provider.py** — keyword inspiration 轴库流程：可选从 like 二级兴趣构建 coverage-aware 候选窗口，再按 `inspiration_interest_sample_size` 抽样进入本轮 `select`；`OnionProfile.interest.likes[].specifics` 优先，一级 domain 只在缺少 specifics 时作为低特异性兜底，并按 parent 计数、production selection ledger、历史 coverage 和 axis saturation 降权。planner 在装配与近期 query-family 过滤后，只把真正留下关键词的 realized interests 写入 `discovery_interest_selection_ledger`；真实运行用 production scope，`keyword-inspiration-dry-run` / `keyword-inspiration-preview` 用 preview scope，因此 preview 可连续验证轮转但不污染正式抽样。随后流程固定为 `select → probe → ground → single-call → assemble → writeback`：`build_grounding_probes()` 先从 `discovery_inspiration_axis` 取 active 轴，再用二级兴趣标签、本地 pooled terms 生成确定性探针；search provider 链（默认 local cache / 已启用平台源 / Bing RSS / Exa / You.com）只做 grounding，不写 `discovery_candidates` 或推荐池，并受 `inspiration_max_probe_searches_per_stage`、`inspiration_platforms_per_probe`、`inspiration_search_pages_per_probe` 和 `inspiration_riskcontrolled_probe_budget` 约束；其中 Exa / You.com / Serply 直连 HTTP 后端与 mcporter 子进程均遵循进程级 `[network]` 出口策略（v0.3.209 起修复：此前硬编码 `trust_env=false`，custom 代理下 api.exa.ai 从国内直连超时），Bing RSS 属国内可达服务保持恒直连。`discovery.keyword_inspiration` 现在是 regular/shared inspiration stage 的唯一 LLM 调用：一次接收 platform guides、已选兴趣、轴库、fresh evidence 和 allocation targets，返回 `{axes[], keywords[]}`；输出截断时解析器会 salvage 已完整返回的 axes / keywords，LLM 失败时才进入两级确定性 fallback：先复用现有轴库 / 轴 example terms 生成 `MaterializeCandidate`，仍缺 coverage 时由 `materialize_platform_keywords()` 做 `deterministic_fill`。装配按兴趣 breadth 优先、同兴趣 axis depth 后补，再按 `platform_style_score()` 软分排序；平台 style mismatch 不再硬拒绝，错误兴趣归因、脚本不匹配、轴不足或目标平台缺词会进入 telemetry。新轴可通过 `upsert_inspiration_axes()` 写回轴库，生产运行会 `bump_usage=True`，preview 只有显式 `--persist-axes` 才持久化轴且不写关键词池。coverage join 统一走 `_normalize_match_text()`，画像整理产生兴趣重命名 mapping 时会同步迁移 `discovery_keywords.source_interest` 和 `discovery_interest_selection_ledger.source_interest`。
- **runtime/inspiration_pipeline.py（`InspirationKeywordPipeline`，Phase 2 Part D）** — 上述 ①–⑥ 编排（选兴趣 / 取轴 / probe / ground / 单次调用 / coverage-first 装配 / 回写 / 回填 tick）从 ~4000 行的 `KeywordPlanner` god-file 抽成独立类，行为逐字不变。构造注入 db、llm、inspiration provider、discovery-config 视图 + Task 4 的 `InspirationBreadthParams` 内部视图、clock、可选 embedding 服务，并持有一个 `host` 反向引用只用于与 merged-keyword 路径共享的少数 planner 基础设施（`_history` / `_insert` / `_avoid_hints` / `_supply_hints` / `_load_profile`）；`KeywordPlanner` 保留 `_run_inspiration_stage` / `_run_shared_inspiration_stage` / `preview_inspiration_keywords` / `_selected_inspiration_interests` 四个签名不变的兼容委托转发到 pipeline。
- **轴库 yield 学习闭环 + 生命周期代谢（Phase 2 Part A/B）** — 轴库从"能复用"变成"会学习"：**production** inspiration stage 在②取轴之前先跑一次 `backfill_inspiration_axis_yield()` + `apply_inspiration_axis_lifecycle()`（纯 SQL、零 LLM），让本轮选轴立刻看到新成绩；节流为全库 `MAX(yield_backfilled_at)` 距 now 不足 6 小时（`_AXIS_BACKFILL_MIN_INTERVAL_HOURS`）则跳过，**preview 永不触发**（与 preview 不 bump usage 同一原则：观测不改变被观测系统）。回填是 trailing-window 全量重算（SET，幂等），`yield_score=(admissions+0.3)/(window_uses+1)` Laplace 平滑（常数 0.3=探索 prior，未使用轴回填后恰为 0.3）；排序地板改为**条件式**——只有从未被消费过（`window_uses==0`）的轴才享 `max(yield_score,0.3)` prior，有过消费记录的轴按真实分排、低分立刻下沉（修 Phase-1 无条件 max 把坏轴捞回地板的 bug）。生命周期：active → `stale`（time_sensitive 过期）/ `retired`（`window_uses>=5` 且回填后 `yield_score<0.08`，retired 不被 upsert 复活）→ 陈旧 90 天物理 purge；stage telemetry 带 `axis_backfill`（ran / skipped + staled / retired / purged 计数）。
- **embedding 近邻轴合并（Phase 2 Part E，可选、可裁）** — ⑥ upsert 前在 pipeline 层（async）用注入的 embedding 服务把"新轴 → 应并入的既有 same-interest active 轴"解析出来：`cosine >= 0.92`（`_AXIS_EMBEDDING_MERGE_THRESHOLD`）时新轴改带既有轴的 `axis_id`（evidence / example_terms 合并），交给保持同步、零 I/O 的 `upsert_inspiration_axes()` UPDATE 既有行、不新建，省一个 cap 名额。**降级契约（硬要求）**：无 embedding 服务不算降级直接透传；服务超时 / 抛错 / 空向量 → 无损回退 Phase-1 字符串规范化行为、`report["axis_embedding_degraded"]=true`、stage 从不被阻塞。该合并只影响轴库存储去重，不改动 materialization 覆盖装配。
- **多平台丰富度修复（Phase 2.1，三管齐下）** — 真机发现平台越多，单次调用的 48 槽越摊薄，`core_concept` 会退化成"话题名 + 平台后缀"（`新游推荐 盘点`）而非具体锚点（`士官长 登陆PS5`）。三处协同补救：**(F1，产出侧)** `_INSPIRATION_AXIS_KEYWORD_SYSTEM_PROMPT` 新增一条静态规则，要求 `core_concept` 锚定 `fresh_evidence` 里的具体实体 / 事件 / 作品 / 人物 / 机制、禁止复述 interest 或 axis_label，无锚点时才可退回话题级（仍 100% 静态、过 byte-identical cache）；**(F1.5，择优侧，主)** `materialize_platform_keywords` 的 `_choose_materialize_candidate` 排序键加 `is_specific` 信号——`(需要新轴, is_specific, style_score, -index)`，具体候选同槽位内压过泛化候选，两者同类时才退回 style_score 排序。`is_specific(core_concept, interest, axis_label)` 是**纯确定性剥离残留判定**：把归一化 `core_concept` 里的 interest span、axis_label span、泛化 / 风格 marker（复用 `_PLATFORM_STYLE_MARKERS` + 若干话题填充词）**按最长优先做子串移除**（非空格 token——中文常无空格，`新游推荐盘点` 也要判为复述），去残留空白 / 标点后剩非空即 `True`；确定性补位候选天然 `is_specific=False`。`restatement_rate(keywords)` 是配套观测指标。**(F2，token 侧)** 单次调用 `max_tokens` 随槽位放大：`slots = len(selected_interests) * len(target_platforms)`，`max_tokens = min(16384, 8192 + max(0, slots-12) * 256)`（阈值 12 = 3 平台 × 4 兴趣舒适点；6 平台 24 槽→11264，8×6 48 槽→ceil 16384），并把请求值写进 `llm_telemetry.max_tokens_requested`；provider 因 max_tokens 报错时**降回 8192 floor 有界重试一次**（仅 max_tokens 相关错误、仅当请求高于 floor 才触发，非 Phase-1 禁的 salvage-repair），仍失败才走确定性 fallback——"一轮 ≤1 次成功生成调用"不变式保持。**观测**：`RealizedKeyword.metadata` 现在额外带 `core_concept` / `decoration`（确定性补位带模板 core + 空 decoration），preview 报告显式回写 `report["metadata_by_platform"]`；这两键只观测、不改最终 keyword 文本与入池。
- **跨域 explore 通道走轴库富链路（Phase 2.3，默认开 coexist）** — B 站 explore 词从旧的把 merged `explore_domains` 拍平（`_explore_domain_queries`）升级为走同一条轴库富链路（`_run_explore_inspiration_stage`），与 regular 通道并存、互不影响。**种子 = merged call 现成的 `explore_domains`（跨域话题，非 like 二级兴趣、非历史旧域）**：把每个 domain 当作 `seed_interest` 喂进 E0 参数化后的 `_run_inspiration_axis_pipeline`，不新增独立跨域发现 LLM 调用。`_INSPIRATION_AXIS_KEYWORD_SYSTEM_PROMPT` 增一条**静态** explore 规则：带 `explore_request` 时 `core_concept` 锚定"未覆盖但相关"的跨域具体实体、避开 `explore_request.avoid_covered`（仍过 byte-identical cache）；F1/F1.5/F2/F3 全继承（具体性 prompt、`is_specific` 择优、动态 max_tokens、core/decoration 观测），explore 词同样具体、跨轴、可核 `restatement_rate`。**舒适区扩张闭环（复用 Phase 2 回填、零新逻辑）**：新生成的轴打 `source='explore'`（`new_axis_source` 参数，`axis_id` 由 interest+label 稳定派生），Phase 2 `backfill_inspiration_axis_yield()` 按 `axis_id` 归因（source 无关，cohort 过滤不排除 `keyword_kind='explore'` 行），高产 explore 轴 yield 上升；下一轮用 `list_inspiration_axes_by_source('explore', min_yield=…)` 把它们作为**匹配当前域的 `existing_axes`** 喂回（只丰富当前域、绝不当种子——否则 `source_interest` 会漂成旧域）。**AC2 机制保证（R3 钳制）**：装配前丢弃 `interest`（归一化匹配）不在当前 domains 种子集里的候选，保证每个 explore 词 `source_interest ∈ 当前域`。**默认开 + 降级不裸奔**：`inspiration_search_enabled=true` 且 explore 到期 → 走富链路（`mark_explore_planned` 照常）；富生成 degraded（`report["explore_degraded"]=true` / 空 ledger / 抛错）→ **降级回旧 `_explore_domain_queries` 拍平**（merged domains 是现成真数据，explore 池仍补货、非空），`planner.last_explore_inspiration_degraded=true`。**无双重 explore**：走新链路就不走旧拍平（除降级）。**预算诚实**：explore 到期的 coexist 轮比不到期轮**只多一次** explore 富生成 LLM 调用，regular 合并通道调用数不变。inspiration 关闭时该通道逐字回退旧拍平行为；`replace` 模式的 explore 路径不变（Non-Goal）。
- **SourcePolicy** — 统一读取 `sources.<platform>.enabled` 与 `[scheduler.pool_source_shares]`，生成有效平台配比；关闭的平台保留配置但不占 runtime quota
- **sources.platforms** — 九个平台族的唯一可枚举注册表；discovery 已看过滤、pool 配额统计、已看事件身份和 URL host 推断统一复用别名 / strategy 前缀规则，`linuxdo/linux.do/l站` 与 `linux.do/t/*` 归入同一来源族
- **SourceAdapter 协议** — 多源适配层（`sources/`），在上述 4 个 B 站策略之外挂载非 B 站内容源（小红书、抖音、YouTube、X、知乎、Reddit、Bangumi、Linux.do、V2EX 等）
- **sources.platforms** — 九个平台族的唯一可枚举注册表；discovery 已看过滤、pool 配额统计、已看事件身份和 URL host 推断统一复用别名 / strategy 前缀规则，`bangumi/bgm` 与 `bgm.tv/subject/*`、`v2ex/v2` 与 `v2ex.com/t/*` 分别归入同一来源族
- **SourceAdapter 协议** — 多源适配层（`sources/`），在上述 4 个 B 站策略之外挂载非 B 站内容源（小红书、抖音、YouTube、X、知乎、Reddit、Bangumi、V2EX 等）

## 来源定向回填与抖音任务预算

`DiscoveryStrategy.source_platform` 是一次策略运行的权威平台身份；历史四个 B 站策略默认返回
`bilibili`，YouTube 与抖音策略显式覆盖。主策略不足时，`ContentDiscoveryEngine` 只从本轮策略
声明的平台回填 `content_cache`，并把平台条件交给 storage 在 SQL `LIMIT` 与来源平衡之前执行。
旧库中空 `source_platform` 仍按 B 站兼容，不能让 `discover --source bilibili` 在 B 站无结果时
混入 Reddit、YouTube 等其它来源。

抖音插件任务的 `wait_seconds` 是**整轮 wall-clock 预算**，不是 search / hot / feed / creator
每个分支各自重新领取一份预算。首个分支耗尽预算后，未执行分支直接记为 `timeout`，不再继续
入队。单任务到期会原子转成 `failed + wait_timeout`；上层 deadline 或进程取消会尽力转成
`failed + wait_cancelled`，且不会覆盖已经由扩展完成的终态。因此任一次 CLI、daemon 或 Agent
Bridge 调用结束后，都不应留下由该调用创建的永久 `pending/in_progress` 任务。

## Dislike 与搜索边界（2026-08-07）

普通 `disliked_topics` 是候选评估与推荐输出约束，不是抓取授权列表。SearchStrategy、统一关键词 planner 和各来源
producer 仍可发送同主题 query；同一个搜索词可能返回安全的相邻内容，且抓取日志本身不代表内容会被推荐。

内容评估继续读取画像中的最新 dislikes 并降低命中项分数，新增 dislike 仍会异步执行 exact + embedding recall +
LLM precision 清池以减少库存浪费。最终“不展示”的正确性由 recommendation 输出层负责，不能依赖一次性清池
是否已经完成。普通 dislike 不过期 planner keyword，不撤销已排队来源任务，也不增加请求前 LLM gate。

## 多源适配层

`sources/` 把"内容从哪里来"从"怎么挑"里彻底解耦。`ContentDiscoveryEngine` 通过 `register_adapter()` 挂载任意实现了 `SourceAdapter` 协议的源，每个源用一条 `SourceRecipe`（`source_type` + `strategy` + `config`）描述订阅，引擎在一轮 discovery 里并发驱动所有启用的 recipe。

当前已实现的 adapter：

- **BilibiliAdapter** — 把四大 B 站策略包装成 adapter 形态，对 recipe 的 `strategy` 字段分发到 `SearchStrategy` / `TrendingStrategy` / `RelatedChainStrategy` / `ExploreStrategy`。
- **WebSourceAdapter / XiaohongshuAdapter** — 通用"浏览器 + LLM 抽取"通道。走 `BrowserManager` 拿页面 `(innerText, anchors)` 快照，用 LLM 从 innerText 提取标题 / 作者 / 摘要，再用 anchor 列表按标题模糊匹配回填 `content_url` / `content_id`。
- **DyTaskQueue** — 抖音初始化画像、`fetch-douyin` smoke、search / hot / feed discovery 都走同一扩展任务桥；初始化回传发布 / 收藏 / 点赞 / 关注后转成统一行为事件，discovery 任务只保留候选结果。
- **YtTaskQueue / Takeout parser** — YouTube 初始化画像走扩展任务桥读取观看历史 / 订阅 / 点赞；Google Takeout 导入走 `youtube.takeout` 离线解析，两条入口都转成统一行为事件。`yt_tasks` 不承载 steady-state discovery。
- **YouTube discovery strategies / producer** — `yt_search` 由 LLM 从画像生成关键词后用 `scrapetube` 搜索，`yt_trending` 优先通过 YouTube InnerTube browse API 拉 trending feed，当前 `FEtrending` 失效时降级抓取公开 topic 页的 `ytInitialData` 视频，`yt_channel` 从 DB 中 YouTube follow 事件读取订阅频道并用 `scrapetube` / `yt-dlp` 拉最新视频；三者由后端 `YoutubeDiscoveryProducer` 在 YouTube 低于 quota 时独立调度，输出 `source_platform="youtube"` 的 `DiscoveredContent` 并入 `discovery_candidates`，再由统一候选 pipeline 评估 / 入池。v0.3.165 起 yt-dlp、scrapetube、InnerTube POST 与 HTML fallback 全部服从 `[network].mode`，避免某条 fallback 偷偷继承失效环境代理。
- **DouyinDiscoveryService / DouyinDirectStrategy / DouyinDirectClient** — 抖音 discovery 走 opt-in 路径，服务层统一封装 search / hot / feed 三个公开来源；runtime 路径只拉原始候选并入 `discovery_candidates`，调试时仍可在 `openbiliclaw discover-douyin --no-cache` 下直接跑策略预览。三条分支现在分别报告 `used / empty / timeout / failed / budget_exhausted`，某个 search 关键词或分支失败不会再中止同轮 hot / feed，之前已取到的候选也会保留。v0.3.153+：`DouyinDirectClient` 默认 HTTP 客户端 `trust_env=False`，不继承环境 / 系统代理（抖音是国内域名，代理出口易触发风控，与 B站 直连策略一致）。runtime `DouyinDiscoveryProducer` 每轮 claim 数与实际搜索数对齐，并按每个关键词的真实终态分别结算，不再把同批未执行或失败词一起标成 used。
- **ZhihuDiscoveryProducer** — 知乎 discovery 走浏览器插件登录态任务；runtime 和 `openbiliclaw discover --source zhihu` 都按 `pool_source_shares.zhihu` 缺口 / 手动 `--limit` 与 `[sources.zhihu].source_modes` 入队 `zhihu_tasks(type="search"|"hot"|"feed"|"creator"|"related")`。`search` claim 统一关键词；`hot` 拉热榜；`feed` 拉首页推荐；`creator` 优先用最近知乎任务中的作者主页作种子，没有历史种子时从同轮 search / hot / feed 候选的作者页兜底；`related` 优先用最近知乎候选 URL 作扩展种子，没有历史种子时从同轮已返回内容 URL 兜底。扩展回传 `zhihu_*` 后转换为 `DiscoveredContent` 进入统一待评估池。
- **RedditTaskQueue / RedditDiscoveryProducer** — Reddit discovery 默认走 `rdt-cli` 登录态命令后端；runtime 和 `openbiliclaw discover --source reddit` 都按 `pool_source_shares.reddit` 缺口 / 手动 `--limit` 与 `[sources.reddit].source_modes` 触发 `search` / `hot` / `subreddit` / `related`。`search` 使用统一关键词，关键词池为空时回退画像兴趣；`hot` 默认拉 `r/all`；`subreddit` 优先用最近 Reddit 候选中的 subreddit 作种子；`related` 优先用最近 Reddit 内容 URL 作扩展种子。显式 `backend="extension"`，或 `rdt` / `opencli` 命令后端不可用时，会入队 `reddit_tasks(type="search"|"hot"|"subreddit"|"related")`，由扩展在已登录 `reddit.com` 会话里读取同源 JSON endpoint。候选回传后统一转换为 `DiscoveredContent` 进入待评估池。Reddit producer 是 fetch-only：只入 `discovery_candidates`，不在同一 CLI / runtime producer 调用里同步等待 LLM 评估。
- **BangumiClient / BangumiDiscoveryProducer** — 第八个正式内容来源，固定直连官方 `api.bgm.tv/v0` 匿名只读接口。`search` claim 统一关键词，`ranked`/`latest` 按 subject type 和持久化 cursor 浏览；三分支使用 UTC 日条目预算、最小调度间隔与 `429 Retry-After` cooldown。Subject 经 `bangumi_subject_to_content()` 归一为 `content_type="subject"`，只写 `discovery_candidates`，不 inline 等待 LLM；显式公开 username 的收藏另供 guided init 使用。完整契约见 [Bangumi 来源文档](bangumi.md)。
- **LinuxdoTaskQueue / LinuxdoDiscoveryProducer** — Linux.do 通过已安装扩展在真实 `linux.do` task tab 内发起同源 JSON `GET`，后端不持有 Cookie。`search` claim 统一关键词，`hot` 读取 `/hot.json`（仅 400/404 回退 weekly top），`feed` 读取 latest，`creator` / `related` 从最近或同轮作者/topic seed 扩展。五分支统一归一为 `content_type="post"`、`content_id="topic:<id>"` 和 `source_strategy="linuxdo-<mode>"`，只写 `discovery_candidates`；`_t` 仅上报登录布尔，Cookie 与原始响应不上传。完整契约见 [Linux.do 来源文档](linuxdo.md)。
- **BangumiClient / BangumiDiscoveryProducer** — Bangumi 内容来源，固定直连官方 `api.bgm.tv/v0` 匿名只读接口。`search` claim 统一关键词，`ranked`/`latest` 按 subject type 和持久化 cursor 浏览；三分支使用 UTC 日条目预算、最小调度间隔与 `429 Retry-After` cooldown。Subject 经 `bangumi_subject_to_content()` 归一为 `content_type="subject"`，只写 `discovery_candidates`，不 inline 等待 LLM；显式公开 username 的收藏另供 guided init 使用。完整契约见 [Bangumi 来源文档](bangumi.md)。
- **V2EXClient / V2EXDiscoveryProducer** — V2EX 公开 discovery 来源，固定使用匿名 JSON API / Node、Tab Feed，PAT 存在时增强 API 2.0。`search`、`node`、`tab`、`hot`、`latest` 五个分支共享关键词 claim、Node/Tab 配置、每日预算、最小间隔和 `429` cooldown；producer 会按配置上限补不完整 Topic 详情，并在 PAT 可用时读取 Reply 第一页生成确定性讨论摘要，`v2ex_topic_to_content()` 仍只把 Topic 归一为 `content_type="topic"`，Reply 不独立入池。正式 search 优先复用已配置的 Exa / You provider 做 `site:v2ex.com/t` 召回，再用官方 Topic API 补正文；没有外部 provider 或合法结果时才退回 latest/hot 的有界本地匹配。扩展 bootstrap 通过 staged task-result 进入统一事件层，用户 Reply 按 Topic 聚合；账号分区 `v2ex_node_affinity` 已实现确定性意图折扣、时间衰减与 Node 召回，完整收藏快照通过双确认 durable outbox 生成 retraction / restore，账号切换只在完整画像提交后激活。真实登录四 scope、五路公开请求和真实 LLM evaluator/admission E2E 均已通过。完整契约见 [V2EX 来源文档](v2ex.md)。
- **DouyinPluginSearchClient** — search 子来源复用 `dy_tasks(type="search")` 插件 DOM-first 链路，结果以 `dy-plugin-search` 进入 discovery；扩展会从抖音首页搜索框输入关键词并点击搜索，任务 debug 用 `ui_triggered` 记录是否提交、`search_navigation_ok` 记录是否进入 `/jingxuan/search/<keyword>` 等真实搜索结果路由。MAIN-world tap 会兼容抖音搜索页当前的 `/general/search/stream/` chunked JSON 响应；v0.3.174 起 search 采集**被动优先**：触发真实 UI 搜索后滚动真实结果容器（`pickSearchScrollTarget` 从 `/video/` 锚点向上找可滚动祖先，找不到回退 window 滚动）驱动页面自发翻页，页面发出的带签名 `search/single` 响应由被动 tap 收割；滚动循环自适应（上限 10 轮、去重计数连续 2 轮无增长即停、每轮 250ms 轮询最多 3s），task debug 带 `passive_items_harvested` / `scroll_rounds`。当页面自身响应和 DOM 解析都没有候选时，search 会调用已登录页面的 search API bridge 兜底，但真实响应若带 `search_nil_info.search_nil_item="hit_shark"` 且无候选，仍按抖音反爬空结果处理。hot 子来源复用 `dy_tasks(type="hot")`，后端从 hot board 抽取 `sentence_id` 和可用的 `group_id -> seed_aweme_id`，扩展优先执行带 seed 的热词；后台 tab 从 `https://www.douyin.com/` 首页出发点击热榜 / 热点入口和目标热词，DOM / 被动监听不足时用已登录页面的 related API bridge 拉取相关视频，结果以 `dy-plugin-hot-related` 进入 discovery。feed 子来源复用 `dy_tasks(type="feed")`，由扩展在首页推荐流滚动触发加载，结果以 `dy-plugin-feed` 进入 discovery。search / hot / feed discovery 任务都会用非激活 tab 执行；content script 会按 `dy_search` / `dy_hot` / `dy_feed` 目标 scope 过滤候选，避免首页推荐流响应污染 search / hot 结果。只有 `bootstrap_profile` 这类显式账号信号导入允许前台。每次入队前会把过期的 search / hot / feed pending discovery 任务标记为 failed，避免旧任务挡住当前 producer；`ContentDiscoveryEngine.register_strategy()` 会按 strategy name 替换旧实例，避免 `DouyinDiscoveryService(cache=True)` 多轮运行后累积多个 `douyin_direct` 并重复入队 search。`openbiliclaw search-douyin` 仍保留为独立 search smoke / 诊断命令，结果不转成 memory event。v0.3.174 起 hot 子来源做**热词轮换**：客户端用进程内 `sentence_id → 单调时钟` 表（TTL 6 小时 `_HOT_SEED_REUSE_TTL_SECONDS`）过滤近期已用热词后再截断到种子数，全部近期时回退陈旧词（宁可陈旧不空手），避免连续 hot 轮反复挑同一个 top 热词只出被 dedupe 吸收的重复。扩展 `harvestSearchViaApi` 的每次 `w.fetch` 加 `AbortController` + 15s 超时快速失败（`search/single` 缺 a_bogus 会永久挂连接，仅 search 路径，`harvestHotRelatedViaApi` 不动）。
- **Douyin 插件稳定性护栏** — dispatcher 先确认 worker 具备 tab 执行能力，再在调用会原子 claim 的 `/next-task` 前取得跨来源 mutex；alarm 与 runtime-stream kick 共用 single-flight poll，executor 用 `accepted / declined` 握手，避免残缺环境、锁忙或双轮询把任务遗留在 `in_progress`。task-result 要求 2xx ACK，并对相同 body 做最多 3 次有界退避重试，未确认终态不清理 lifecycle。MAIN-world fetch / XHR tap 从 `document_start` 安装，Window 级状态使重复注入保持幂等，并在页面替换网络原语后重新包装；isolated listener 把早于任务 collector 的归一化 search / hot / feed 消息按 scope 有界缓存、一次性 drain。DOM fallback 覆盖当前 `/jingxuan` 的 `div[data-aweme-id]` / 非 anchor `href` / `video_<id>` 卡片，不再只依赖 `/video/` anchor。合法空响应与完全没观察到响应分开计数；仅当 passive 与 DOM 都无内容时，后者才触发同一后台 tab 一次 bypass-cache reload，仍未观察到才失败，真实空、限流 / 风控 / 登录 / 注入错误不重试。相同 `type + requestId` 的并发 API bridge 请求只执行一次，并有 TTL / 数量上限清理。Chrome / Firefox 构建各自清理输出并校验 manifest 引用资产；daemon 还以前置 extension presence 和 `15m` 失败 / `60m` 预算退避抑制 pending 任务风暴。
- **XAdapter（`sources/twitter_adapter.py`）+ 三策略** — X (Twitter) 是第六个内容源，`source_type="twitter"`、显示标签 `"X"`。发现走**服务端 cookie 重放**（对标抖音 direct，但用 `twitter-cli` 取代 XBogus 签名），`XAdapter.fetch(recipe, profile, limit)` 是真实实现（不是 XHS 那种 stub），按 `recipe.strategy` 分发到三个 `discovery/strategies/x.py` 策略：

  - **XSearchStrategy**（`strategy="search"`）— 复用 `xhs_keyword_gen` 思路，从 Soul 画像生成关键词，调 `XClient.search()`。
  - **XForYouStrategy**（`strategy="feed"`）— 拉推荐流 For-You（`XClient.for_you()`），由 X 算法决定相关性；最高曝光，producer 压到很低的每日频次并在连续失败后自动暂停。
  - **XCreatorStrategy**（`strategy="creator"`）— 用户精选的账号订阅，对 `x_creator_subscriptions` 里到期的 handle 调 `XClient.user_tweets()`。

  三个策略产出都经 `discovery.x_normalize.normalize_tweet()` 转成 `source_platform="twitter"` 的 `DiscoveredContent`（`content_type ∈ {tweet, thread}`、`body_text` 带推文 / `note_tweet` 长文全文），入 `discovery_candidates` 待评估池，再由统一候选 pipeline 评估 / 入池。候选池会把 `x` / `twitter` 归一到同一个 `twitter` 平台 key，避免配额、统计和前端过滤被拆成两类。后台调度见 [runtime 模块的 `XDiscoveryProducer`](./runtime.md#xdiscoveryproducer)；行为采集（用户在 x.com 上自己的点赞 / 收藏 / 回复）走浏览器扩展 MAIN-world tap，与 discovery 通路独立。
- **XClient（`sources/x_client.py`）+ x_normalize** — `XClient` 封装默认运行时依赖 `twitter-cli`（Apache-2.0，自带 `curl_cffi` TLS 指纹），全程只读，`enabled=true` 且真正 fetch 时才 lazy import（`enabled=false` 路径绝不 import）。同步方法用 `asyncio.to_thread` 包成 async；`probe()` 通过 `fetch_me()` 读取当前认证账户，供来源设置页的 `live_probe`「测试连接」使用；`search` / `for_you` / `user_tweets` 服务于发现,`likes` / `bookmarks`(读当前登录用户自己的点赞 / 收藏 timeline,`likes` 先 `fetch_me()` 解析 user_id)服务于 `init` 偏好回填——X 无扩展 bootstrap 任务,故 likes/bookmarks 与 B站 收藏一样在 `run_guided_init` 里服务端直拉、本轮直接入 `events`(`like` / `favorite`)。底层 `TwitterAPIError` / `AuthenticationError` 映射为 `XMissingCookieError` / `XAuthError`(401) / `XBlockedError`(403) / `XRateLimitError`(429)，供源健康状态机分流退避。re-login 类状态（`missing_cookie` / `expired_cookie` / `blocked`）无定时恢复（`is_ready()` 会一直 park 住 producer，永远等不到能翻回 `ok` 的那次成功），唯一解封路径是扩展同步到新有效 cookie 时 `/api/sources/x/cookie` 调 `XSourceHealthStore.clear_relogin_block()`；`rate_limited` 的时间冷却不受 cookie 影响、不被清除。`x_normalize.normalize_tweet()` 直接从 `twitter_cli.serialization.tweet_to_dict` 的结构映射字段（库已做 GraphQL 拆包），tombstone / 不可用推文返回 `None`；多条连推或带 `1/` 线程标记的头条 → `content_type="thread"`，否则 `"tweet"`。**`x-client-transaction-id` 自建**：X 的 `SearchTimeline`（发现搜索用）强制要求该请求头、缺失即裸 `HTTP 404`（`likes` / `bookmarks` 端点不要求，故只有搜索受影响），而 `twitter-cli` 用匿名主页引导该头的路径已被 X 新版 `x-web` logged-out 外壳打破（不再内联 `ondemand.s`）。`XClient._client()` 因此在 twitter_cli 唯一构建点自建生成器：用已登录 cookie 拉 `https://x.com/home`（仍带完整 `client-web` 包）构造 `ClientTransaction` 注入底层 client，按实例缓存复用、复用 twitter-cli 共享 `curl_cffi` 会话保持 TLS/代理一致；best-effort，失败只 WARNING 并回退到搜索 404、不影响其它通路。

`BrowserManager` 有两个可替换后端，由 `[sources.browser].cdp_url` 决定：

1. **CDP 后端（推荐）**：Playwright `connect_over_cdp` 连到你预先启动的 Chrome，复用真实登录 cookie。小红书这种反匿名严格的源只有这条路能稳定跑。
2. **agent-browser 后端（回退）**：匿名访问，适合不要求登录的简单页面。

启动步骤见 [`docs/modules/config.md` 的 `[sources.browser]`](./config.md#sourcesbrowser) 段落。

## 发现链路怎么工作

一次完整的 runtime discovery，当前可以概括成 7 步：

1. **读取画像**
   discovery 的起点通常是一个 `SoulProfile`。这里面不只是“用户喜欢什么标签”，还包括：
   - 核心兴趣及其权重
   - 兴趣的来源、首次/最近出现时间，以及一级领域 + 二级细项
   - 长期避雷项 `disliked_topics`
   - 认知风格、价值观、内在驱动力、当前阶段和 life stage
   - MBTI 画像（类型、维度强度、置信度、推断来源）
   - 喜欢的内容风格、时长倾向、质量敏感度和观看上下文
   - 喜欢的 UP 主
   - 深层需求，例如“想把问题看透”“想获得秩序感”
   - 来源平台分布、近期觉察和当前洞察
   - `exploration_openness`，也就是系统能不能适当推远一点

   真正进入发现策略时，画像会被压缩成更容易消费的结构化摘要。query / domain / keyword planner 生成使用 `build_query_generation_profile_summary()`，只保留稳定的高权重兴趣、兴趣域、核心特质、认知风格、价值观、动机、deep needs、`disliked_topics`、观看风格和探索开放度；flat `interests` 从最多 128 个候选里选出 64 个。如果 embedding cache 已有向量，选择器会用 MMR 风格优先覆盖更多语义簇，并降低贴近 `disliked_topics` 的 interest；`disliked_topics` 自身也做 embedding 多样性去重。选择器会预计算 dislike 相似度并增量维护“距已选最近相似度”，避免真实 bge-m3 向量命中时在 prompt 构建阶段重复计算大量 cosine。没有 cached embedding 时保持原来的权重顺序，不在 prompt 构建热路径新增 embedding 请求。近期觉察、当前洞察、session context、兴趣时间戳和来源 provenance 不进入这类 prompt，避免高频状态把 `discovery.search.queries` / `discovery.explore.queries` / `discovery.keyword_planner` 固定输入撑大。`[discovery].inspiration_search_enabled=true` 时，planner 会读取 `discovery_interest_selection_ledger`、`discovery_keywords.source_interest`、`discovery_candidates.raw_payload/source_keyword_id/topic_group` 和 `content_cache.topic_group/pool_topic_label` 构建 coverage snapshot，join 前统一用 `_normalize_match_text()` 折叠大小写 / 空白漂移：某个二级兴趣最近被抽中过、生成过的词越多、raw candidate 数量 / 占比越高、raw candidate dominant content type 越集中、最终入池占比越高，下一轮抽样概率越低；如果该兴趣的 active inspiration 轴近期全部被用过，也会作为 axis saturation penalty 降权。随后 planner 从 like 二级兴趣中抽样；`OnionProfile.interest.likes` 会先展开 specifics，一级 domain 只作为缺少 specifics 时的兜底，且同一 parent 已入选越多后续候选越会被降权。选中后立刻写 selection ledger，并按 `select → probe → ground → single-call → assemble → writeback` 执行 inspiration 轴流程：先从 `discovery_inspiration_axis` 取 active 轴，再用 `build_grounding_probes()` 确定性生成搜索探针；local cache 和平台源 / Exa / You.com 只提供 fresh evidence，不写候选池。单个 stage 最多执行 `inspiration_max_probe_searches_per_stage` 次外部 probe 搜索，平台源每个 probe 最多扇出 `inspiration_platforms_per_probe` 个来源，每个 probe 可由 `inspiration_search_pages_per_probe` 控制翻页 / 扩大结果量，B 站 / 抖音 / X 等 risk-controlled 来源受 `inspiration_riskcontrolled_probe_budget` 和 cooldown / 限流约束。`discovery.keyword_inspiration` 是该阶段唯一 LLM 调用，输入包含 platform guides、已选兴趣、既有轴、fresh evidence 和 allocation targets，输出 `{axes[], keywords[]}`；输出被截断时会 salvage 已完整的 axes / keywords。LLM 失败时不重试 repair，而是两级确定性 fallback：优先复用轴库 / example terms 生成候选，仍缺 interest × platform 覆盖时由 `materialize_platform_keywords()` 产生 `deterministic_fill`，无法补齐的槽位进入 `coverage_shortfall`。装配按 interest × axis × platform 覆盖优先，再按 `platform_style_score()` 软分排序；平台 style mismatch 不再硬拒绝，脚本不匹配只记录为 telemetry。关键词会携带 `grounding_source`、`source_interest`、`axis_label` 等溯源 metadata 写入 `discovery_keywords`；新轴可写回 `discovery_inspiration_axis`，生产运行会 bump usage，preview 只有显式 `--persist-axes` 才持久化轴且不写关键词池。新配置默认以混合模式运行：search-backed inspiration flow 与旧 merged planner 并行；成本敏感时可在设置页切回经典模式。如果同时打开 `[discovery].inspiration_replace_merged_keywords=true`，due 平台会跳过旧 `discovery.keyword_planner` merged call，只用 search-backed inspiration flow 产词，且 B 站 explore 到期时会额外写入 `keyword_kind="explore"` 的探索词池。replace 开启前应先跑 `keyword-inspiration-report` 的 cohort 门禁。`TrendingStrategy` 的排行榜分区 rid 不再走 LLM，而是本地确定性洗牌轮转覆盖；内容评估仍使用自己的 eval profile 压缩口径，保留近期负样本和必要语境。

   当前 selection ledger 的提交点已经从“选中后立即写”后移到 assemble 与近期 query-family 过滤之后：只有真正留下关键词的 realized interests 才会记账；装配先横向覆盖兴趣，再纵向补同兴趣第二轴，错误兴趣归因和尾缀换皮会在写关键词池前被拦截。

   这一步的目标不是“把画像完整搬过去”，而是从画像里抽出对找内容最有用的信号。

2. **并发运行多种策略**
   runtime 正常补池会通过 `ContentDiscoveryEngine.produce_candidates()` 拉原始候选；兼容路径仍可直接调用 `discover()`。两者都不会按“先 search、再 trending、再 related”串行慢慢跑，而是把当前启用的策略一起丢给 `_run_strategies()`，内部用 `asyncio.gather(..., return_exceptions=True)` 并发执行。

   这样做有两个直接好处：
   - 延迟更低，不需要等一个策略完全结束再开始下一个
   - 容错更强，单个策略失败不会把整轮 discover 拖死

   每个策略拿到的是同一个画像，但做的事情不同：
   - `SearchStrategy` 负责把画像翻译成搜索词并调用搜索接口
   - `TrendingStrategy` 负责去排行榜里挑“适合这个人”的热点
   - `RelatedChainStrategy` 负责从已有高价值种子沿相关推荐继续扩展
   - `ExploreStrategy` 负责消费 planner 预生成的探索 query，故意往相邻但更陌生的方向试探

   这一层的核心思想是：先尽量把供给面铺开，再在后面统一收口。

3. **统一入待评估池**
   虽然四个 B 站策略、小红书被动 / 任务结果、抖音 search / hot / feed、YouTube search / trending / channel、知乎插件任务、Reddit rdt / 插件任务、Bangumi search / ranked / latest 官方 API 与微博匿名 search / hot-as-seed / creator 的找法不同，但产出都会被转成同一个结构：`DiscoveredContent`，再由 `DiscoveryCandidatePipeline.enqueue_candidates()` 写入 SQLite `discovery_candidates`。

   入队阶段只做字段归一和身份去重，不做最终“用户会不会喜欢”的判断。`candidate_key` 会优先使用 `source_platform:content_id`，没有 ID 时退到规范化 URL，再退到标题 + 作者 hash。重复发现不会插入第二行，只刷新 `last_seen_at`。

   这一步的作用，是把不同来源的原始线索先汇入同一个 `pending_eval` 队列；从这里往后，来源差异只作为 prompt 上下文和配额统计信号存在，不再决定一套单独评估流程。

4. **混源连续评估**
   `DiscoveryCandidatePipeline` 提供 `claim_batch → evaluate_claim → complete_claim / release_claim` 四阶段 API。`CandidateEvalCoordinator` 是 API runtime 唯一 claim owner，默认 3 个、每个最多 30 条 worker；worker 只跑 LLM，落库与 admission 串行，任一完成即补位，全局最多 90 条在途。调度库存严格使用 `available + admitted_pending_available + evaluated_pending_admission`，普通 `pending_eval/evaluating` raw 不计入；其中 `evaluated_pending_admission` 只统计 temporal disposition 仍为 `eligible` 的 `evaluated` 行，raw `evaluated_waiting_total` 只用来触发生命周期清扫。每个完成 batch 先按 claim-token 所有权把相关性与完整 temporal v2 证据组原子落库，再计算 `eligible/review_due/expired`：明确 deadline 已过或 `state=expired/superseded` 的高置信 grounded core 证据才终态为 `rejected_temporal_stale`；到复审时钟的候选回到 `pending_eval`，并按逐行 1 / 2 / 4 / 8 / 16 / 24 小时 not-before 租约退避，未到期行不参与 claim 和库存投影。持久化后 admission 会重读 durable row，以数据库最终证据和状态为准；只有仍为 `evaluated` 的行可入池，malformed / 中性复审留下的 `pending_eval` 不会被同批内存结果洗白。其余达标行按当时 `target - available - admitted_pending_available` headroom 入池，超过 headroom 的合格行保留为 durable `evaluated`。API 成功 admission 同步发出轻量 `on_admitted(count)` 通知，不等待文案工作。OpenClaw direct one-shot 则把同一 pipeline 的首轮 source / inline evaluation / copy 都限制为 ≤4，使用 post-admission callback 在 DB commit 后 await `expression-copy(limit=4, max_extra_requests=0)`；首 batch 的有效 subset 可服务，未复制行保留 pending-copy 以供下一次 operation 重试，且 callback 失败不会回滚已经 durable 的 admission。每批唯一 `claim_token` 防止旧 worker 覆盖新 claim；worker completion 直接驱动补位，60 秒 safety wake 也会复检 review-due / expired 的 `evaluated` 行，即使当前没有 admission headroom。API daemon 读取 `[scheduler].eval_min_batch_size`（默认 15）和 `eval_max_wait_seconds`（默认 90 秒）：零散 raw 候选会先等待凑批，达到最小批量或最长等待后才 claim；协调器会按剩余等待时间唤醒，不会固定睡满 safety wake。手动 CLI producer 是一次性进程，无法把内存等待跨进程保存，因此固定 `1 / 0` 立即 drain 已入队候选。平台请求间隔、raw ceiling、来源配比和 admission 阈值均未改变。

   当协调器低于目标且已经没有 `pending_eval` 时，API runtime 会调用 `ContinuousRefreshController.supply_candidates_once()`：按每个平台族的真实份额缺口立即并行 tick B 站扩展兜底、小红书、抖音、YouTube、X、知乎、Reddit、Bangumi、Linux.do、微博 producer，再执行既有 B 站 refresh plan，不必等各 producer 的下一次 60 秒周期。周期 loop 与这条需求驱动路径共用 per-source lock，同一平台已有 fetch 在途时返回 `in_flight`，不会重复抓取；不同平台仍可并行。补池生产性只认真实 `inserted/enqueued`（旧 direct producer 才回退 `cached/discovered`），`_run_refresh_plan()` 即使执行了策略，只要全部命中 durable duplicate、`supply_inserted_count=0`，就仍是无产出。连续无产出按 30/60/120/300/600 秒退避；任一 pipeline 真正入队会立即清零阶梯。这个修复只改变供给调度与背压，不降低统一 admission 阈值。

   进入批量 LLM 评估前，`evaluate_content_batch()` 会读取 `Database.get_seen_content_keys()`，并通过 `sources.platforms.normalize_source_platform()` 生成统一的 `source_platform:content_id`，判断所有已知已看过的 B 站 / 小红书 / 抖音 / YouTube / X / 知乎 / Reddit / Bangumi / Linux.do / 微博候选；命中项直接记为 0 分并从 prompt 中剔除，避免为已看内容消耗 discovery token。身份来自持久化 `seen_items`、不再受最近 2000 条事件窗口限制；老 BVID 也保留 raw key 兼容旧数据。

   evaluator 使用的是 `compact_content_prompt_profile_summary(build_profile_summary(profile))`，不是完整无限画像：核心 traits / cognitive style / values / motivational drivers / deep needs 各保留前 20 条，扁平 `interests` 实验性保留前 48 条，`interest_domains` 保留前 32 个域且每域最多 16 个 specifics，`recent_awareness` 和 `active_insights` 各取最新 12 条（insight evidence 最多 8 条，speculative interests 最多 12 条），并剔除 recent 条目中的 timestamp / session 等易变字段；`disliked_topics` 不做额外裁剪，继续按存储上限完整进入 prompt。为了不让真正成熟画像的长尾兴趣被 compact block 挤掉，每个候选 item 还可附带自己的 `related_interests`：recall pool 只取**画像块之外**的长尾兴趣（权重排名 49..256——前 48 名已在 compact block 里，重复召回只浪费 token；画像不超过 48 个兴趣时该字段恒为空、零开销），按候选标题 / 摘要 embedding 与兴趣 embedding 的相似度和兴趣权重加权排序（当前实现为 `similarity*0.7 + weight*0.3`），取最多 3 个兴趣名字符串。这个字段挂在 `content_items` / 单条 `content_summary` 上，不进入 profile block；`_evaluation_profile_digest()` 同时 digest compact summary 和 recall pool 的精确 `(name, category, weight)`，权重尾数变化也会失效缓存。没有 embedding service 是明确的 production no-recall 模式；若已配置 embedding 却发生异常、空向量、非数值 / 非有限向量或维度不一致，本轮仍可按 compact profile 给分，但该候选不会写入 normal eval cache，服务恢复后会重新做 recall 与 LLM 评估。96 / 16 在真实标准请求只节省 `0.67%` 输入 token 且严格 replay 仍以 `18% > 16%` flip-rate 失败；48 / 16 在当前 93 项画像的分层 profile block 确定性缩短 `26.02%`、极限 fixture 缩短 `67.82%`，但只有新的 100×3 replay 通过后才能作为 landing 配置。

   之后 evaluator 会按 `[discovery].eval_prefilter_mode` 运行 embedding 预过滤。默认 `shadow` 只计算低相似 would-filter 集合并打 `prefilter-shadow` 日志，所有候选仍进入 LLM，便于统计误杀；`enforce` 才会把非 `explore` 且与 top-256 recall-visible 兴趣标签 + 32 个 compact 兴趣域都低相似的候选写入低分评估缓存并从 LLM batch 中剔除，避免在长尾兴趣召回发生前误杀第 65–256 名兴趣。余弦相似度会夹在 `0..1` 后再映射预筛分数，不会产生负相关性分数。若单批会被过滤超过 50%，会 fail-open 退回全量 LLM 评估，避免坏 embedding 状态掐断供给。

   evaluator 的候选输入不再只依赖标题：各来源会尽量映射 `view_count`、`like_count`、`favorite_count` / `collect_count`、`comment_count` / `reply_count`、`share_count`、`danmaku_count`、`retweet_count`、`bookmark_count`、`tags`、`body_text` 等字段。目录型来源的 `rating_score / rating_count / source_rank` 只有真实非零值才进入 prompt；普通视频不会因统一数据结构而多发三个恒为 0 的键。对知乎 / X 这类 text-first 来源，如果 `description` 与 `body_text` 是同一段文本或只是正文前缀，prompt 会省略重复的 `description`；single 与 batch eval 都保留完整 `body_text`。曾实现的 200+100 head/tail 截断在 Reddit 100×3 真实回放中造成 18% admission flip、Spearman 中位数降至 0.192，因此已完整回滚。除 `explore` 外，各发现路径和平台都只提供上下文，不能设置基础分、自动加分、降低门槛或替明显不匹配的候选编造画像关联；单条与 batch evaluator 使用同一规则。

   `evaluate_content()` / `evaluate_content_batch()` 共用 4096 条进程内 LRU。v5 key 隔离 temporal v2 原子证据契约之前的 v4 结果，并覆盖 evaluator schema、实际 prompt-visible content digest（含完整正文 / 去重后的简介、权威 `published_at`、互动指标、标签、平台 / 类型 / strategy 与 effective source context）、UTC 评估小时桶、compact profile + 精确 recall pool digest、negative-examples 内容 digest、embedding namespace 和 prefilter mode，不再使用 Python `id(profile)` 或全局最新事件水位。sparse/row member identity 使用 canonical prompt-visible digest，因此 URL、BVID、content_id 这类没有发送给模型的运行身份变化不会 false miss；显式 `production` rollback 继续使用 global identity。这样等值画像对象可以命中；正文、发布时间、指标、上下文、负例、跨小时、长尾权重或 embedding 模型变化都会 miss。混合平台、混合 content type、没有显式 context 且 strategy 不同的 sparse batch，以及实际尝试读取封面 bytes 的多模态 batch 会整体绕过 normal per-item cache，避免 partial hit 改写外层 prompt defaults 或复用不同图片。cache entry 原子保存归一化相关性与整组 temporal v2 字段；运行期 `temporal_evaluated` marker 只在本轮取得**完整且非中性**的证据组时置位并允许覆盖既有分类，显式 `unknown`、缺字段或 malformed 结果都保持 marker 为 false，不能把此前的强证据洗成中性，也不能把不同轮次的 mode/deadline/state/evidence/时钟拼接。franchise / style 这类依赖同批兄弟项的 cap 在 cache hit 后按稳定 caller grouping 重放，`enforce` 预筛导致的压缩边界也走同一路径，因此 cold / warm 结果一致。LLM prompt 侧，batch evaluator 会把 compact 结构化画像拆成 `<profile_core>` / `<profile_life_context>` / `<profile_interests>` / `<profile_style_context>` / `<profile_recent_context>` 五层，并用 `PromptLayerRenderCache` 按层 digest 复用渲染后的 JSON block；近期觉察变化只会更新后置 recent 层，画像核心和兴趣层保持 byte-stable 前缀。批量 eval、单条 eval 和搜索 / 排行 / 跨域 / 多平台关键词生成调用 `LLMService` 时，会在服务支持的情况下关闭额外 core memory 注入，因为这些 prompt 已经携带结构化画像。

   batch evaluator 的 LLM 输出优先使用顶层 JSON object：`{"results": [...]}`，以匹配 OpenAI-compatible provider 的 `json_object` 约束；sparse production 每项必须原样带回请求内 `id`。多成员禁止按位置猜配，重复、未知、缺失 local ID 都进入 bounded member repair。显式 `production` rollback 的解析器仍兼容旧的根数组、fenced JSON、JSONL，以及 provider 偶发返回的 `{"content_id": {"score": ...}}` 映射对象。成功响应里只有部分 member 缺失 / malformed 时，只重试缺失成员：最多递归 3 层、最多追加 6 个请求；若整组都缺失才二分，否则把缺失 subset 原样重试。预算耗尽的成员标成 `evaluation_response_missing` 交给 pipeline 释放整批 claim，**不会**展开为 N 次单条请求。provider transport / rate-limit / cooldown / quota 等调用异常原样向上传递，也不会被当成 0 分质量观测。

   当 `[discovery].multimodal_evaluation_enabled=true` 且当前 evaluation 路由支持图像输入时，带 `cover_url` 的候选会额外准备封面图：`discovery.multimodal.prepare_cover_image_inputs()` 走 `runtime.image_cache.get_or_fetch_cover_bytes()`，先查本地 `data/image-cache/`，命中则直接复用缓存图，未命中才按同一 SSRF / 白名单 / redirect / 大小限制边界抓取并写回缓存。小红书 token 图因此优先使用预取或 UI 代理已经落盘的副本，不依赖评估时原 CDN token 仍有效；随后再按 `multimodal_image_max_px` 与 `multimodal_image_quality` 压成 JPEG data URL。准备成功的候选会在 sparse `content_batch` 里带 `cover_image_ref="cover:<id>"`，LLM user message 中每张图前也会插入同样的 request-local `cover:<id>` 文字锚点；图片 bytes、MIME 和顺序不变，全局 content ID 不进入 candidate/image anchor。没有 `cover_image_ref` 的候选只按文本字段判断。该 batch 会使用更小的 `multimodal_batch_size`；如果模型不支持图像或图片准备失败，自动退回文本 + 互动指标评估。显式 `production` rollback 才保留历史 `cover:<content_id>` 锚点。

   评估结果会一次性回写到 `discovery_candidates`：temporal v2 证据明确 `expired` 的行变成 `rejected_temporal_stale`，`review_due` 行回到 `pending_eval`，低分变成 `rejected_low_score`，其余待 admission 的达标且 `eligible` 行才进入 `evaluated`；全局 franchise 入池配额命中时会变成 `rejected_franchise_quota`。准入阈值由 `discovery.admission.effective_admission_threshold()` 统一计算：候选行或 raw payload 的 `score_threshold` 只能抬高门槛；所有非 `explore` 来源至少使用 `[discovery].admission_min_score`（默认 `0.60`）；只有精确的 `source_strategy="explore"` 使用现有 `0.58` 例外，`explore-*` 等近似字符串不享受特权。`raw_payload.admission_policy="observed"` 只表示来源是用户观察 / 插件采集，不再降低 admission 阈值；B 站扩展搜索、小红书 observed、抖音 / YouTube / X 等任意来源都必须经过同一 evaluator 分数门。低分 observed 内容仍保留在 `discovery_candidates` 里作为学习 / 诊断信号，但不会写入 `content_cache` 的正式可推荐池。

   provider / LLM batch 级 transient 异常、空 scores、短 scores 或长 scores 都会释放回 `pending_eval` 后续重试，不消耗单条候选的 `eval_attempts`，避免一次短暂 provider outage 把整批内容永久打成 `failed_eval`；同时会递增独立的 `batch_eval_attempts`，高阈值熔断后才进入 `failed_eval`，避免永久坏 provider 无限 churn。batch prompt 明确要求不要因为平台不同而随意抬高或压低分数，只能按内容与用户画像匹配度打分。

   **Reason 契约（v0.3.171 减肥）**：批量 / 单条评估 prompt 明确 reason 仅供内部诊断，不是用户文案；`score < 0.5` 写空串，`score >= 0.5` 写不超过 30 个 Unicode code points 的精炼中文。`normalize_evaluation_reason()` 在 runtime 再执行同一契约：缺失 / `None` 归一为空串，其它非字符串按 malformed member 重试，高分先 strip 再截 30 code points，低分和被 franchise / style cap 淘汰的条目强制清空。对象、eval cache 与候选持久化只接收归一化后的值。0.5 是 baked 进静态 system prompt 的常量；所有 admission 路径仍高于它，所以减掉低分诊断不会改变准入。推荐表达与 delight 文案由独立生成链产生，不展示 `relevance_reason`。

   **候选 sparse JSON 已上线，row-wire 继续拒绝**：batch evaluator 默认使用 `sparse-json` canonical envelope，每次请求分配 `0..N-1` local ID，只保留 title / author 与非空正文、简介、权威 `published_at`、时长、互动、标签、目录指标、tail recall 和真实图片引用；URL、全局内容 ID、重复作者/来源字段、平台指标别名和零值不进入模型 wire。同平台/类型提到 batch defaults，mixed batch 才逐项携带；只有 effective context 精确等于 `explore` 才标 `mode=explore`。顶层 `evaluation_context` 另携带精确 UTC `evaluated_at`；二者只用于 `temporal_*` 判断，不改写 relevance。输出仍为严格 JSON，并要求每个成员完整携带模型拥有的八个 temporal v2 字段；多成员必须按 local ID 绑定，缺失/重复/未知成员走 bounded repair，禁止按位置误绑。真实 100×3 中 sparse 相对历史 production 的 prompt/total 中位分别节省 `27.99% / 24.05%` 并通过全部门，因此继续作为默认；sparse landing 曾把 cache namespace 从 v3 升至 v4，temporal v2 原子证据契约升至 v5，本次无条件 state 证据契约再升至 v6。显式 `evaluation_candidate_transport="production"` 保留 pretty JSON/global-ID 回滚；`row-wire-v1` 因冻结列协议不能承载 `published_at`，编码时会明确拒绝，而且相对 sparse 的 prompt 中位只省 `2.20%`、未达锁定 `5%`，故仍严格限制为 replay-only。

5. **按相关性、时效三态、供给层级和池子上限入推荐池**
   通过相关性阈值且 temporal disposition 为 `eligible` 的候选会先调用 `ContentDiscoveryEngine.normalize_evaluated_results()` 复用 discovery 旧路径的 topic_group / topic_key embedding normalization，再交给 `cache_evaluated_results()` 复用既有 `_cache_results()` 入库逻辑，写入正式推荐池 `content_cache`。`_cache_results()` 在真正写库前会再次调用同一 admission + temporal policy，任何兼容 / 手动调用即使绕过候选队列，也不能把未评估、缺分、低分、待复审或明确过期内容写入可服务池；`review_due` 使用可逆 hold/requeue，只有 `expired` 才终态拒绝。`Database.cache_content()` 的缺省分数为 `0.0`，不再凭空赋予 `0.60`。数据库的普通取池、缓存回填、平台补位、文案预计算、池配额统计、历史推荐和 delight 出口同样使用来源感知的准入条件，所以 `explore=0.58` 能真实展示，而任意非 `explore=0.58` 仍被拒绝。写入前会检查 `count_pool_candidates()`；如果 `pool_available_count >= pool_target_count`，pipeline 直接停止 drain，runtime 也不会继续 discovery。因此“推荐池到了上限就不 discovery”的边界仍以正式可换池为准。成功 admission 的 item 会保存在 pipeline 的 `last_admitted_items` 中，供 runtime 更新 `recent_pool_topics`。

   如果评估后 admission 途中正式池达到上限，剩余通过阈值的候选会保留在 `evaluated`。每次 admission tick（包括没有 headroom 的安全清扫）都会按当前时间复检：`eligible` 等待池子掉回目标以下后优先重试，`review_due` 回到 `pending_eval` 重新评估，只有 grounded deadline 已过或 terminal state 明确的 `expired` 才终态化为 `rejected_temporal_stale`。单次持久清扫最多处理 500 条；即使积压更多，canonical 计数与读取也会立即过滤全部 review-due / expired `evaluated`，因此未落生命周期状态的尾部同样不占 projected、来源配额或 raw maintenance 名额，后续 tick 再分批补齐审计状态。旧 v1 `breaking/current` 行跨过 3 / 60 天时只走 `review_due`，不会仅因年龄被终态化。

   **份额感知入池（v0.3.181+，spec 2026-07-20）**：runtime 把 controller 的 `_source_target_counts`（family→target）注入 pipeline 后，`_admit_until_full` 两轮录取——第一轮只录「该源当前 available < 自身份额」的欠份额行（按 family 本地增量维护快照，来源归族一律用 `sources.platforms.source_family`，B 站四策略归 `bilibili` 族），第二轮才让被推迟的超份额行填满剩余全局坑位（可用性兜底，绝不 reject 超份额行、保持 `evaluated`）。`get_evaluated_discovery_candidates_for_admission(preferred_source_platforms=…)` 把欠份额来源排到 FIFO 窗口前面，防止超份额积压霸占取行窗口。未注入策略（旧测试 / OpenClaw one-shot）时行为与全局-cap-only FIFO 逐字节一致。配合 runtime 的 `_rebalance_pool_shares()` 温和退坑，超份额来源（如 reddit 169/25）不再顶满全局池饿死 bangumi 等欠份额来源。pipeline 另暴露 `pool_full_for_source(source_family)`：注入份额策略且该源低于自身份额时即使全局满也返回 False，供各 producer 的内部 pool 闸放行欠份额来源生产（否则「全局满→不生产→无 evaluated 供给→rebalance 不触发→永远满」的死结无法打破，见 [runtime 模块的 producer 内部闸](./runtime.md)）；未注入策略时等同 `pool_full()`。

   候选队列表本身按来源保留上限，默认上限为 `max(pool_target_count*2, pool_target_count+120, 600)`；入队时会把 `evaluating` 行纳入 cap 计数，但删除时保护 in-flight 行，并优先清理 terminal rows。这样正式池长期满时仍不继续消耗 discovery / LLM，同时不会让外部 observed / producer 队列无限增长，即使 `pool_target_count <= 0` 也保留 600 条的兜底上限。

   `evaluating` claim 的崩溃回收：`Database.initialize()` 回收旧租约，进程首个 pipeline 回收重启孤儿；正常热重载则由父 `refresh_loop` 等待 coordinator 取消 worker 并按 token 归还未完成行，再构造新 runtime。stale sweep 同时清空 `claim_token`，终态提交也清空 token / claimed_at。

   引擎缓存收口仍会按跨源内容身份去重：B 站内容使用 `bvid`，YouTube / 小红书 / 抖音等多源内容使用 `source_platform + content_id`，缺失时再退到 URL / 标题。这样同一个视频被多个策略同时找到时，会保留可入池的一条版本，同时不会把多个非 B 站候选因为空 `bvid` 误合并。

   直接调用 `ContentDiscoveryEngine.discover()` 的 CLI / 测试 / fallback 路径仍保留 inline 评估、排序、压缩和缓存能力，并受缓存写入防线保护；API runtime 与 OpenClaw 兼容 runtime 都会构造同一个 `DiscoveryCandidatePipeline` 实例交给 refresh controller、抖音 producer 和 YouTube producer，正常后台补池统一走待评估池。

6. **按相关性和供给层级排序**
   进入 `content_cache` 前后，引擎仍会复用 `_merge_and_rank()` / `_compress_topic_repeats()` 的排序与压缩口径。当前排序不是只看分数，而是先看候选层级，再看内容质量信号：

   - 先保 `candidate_tier == "primary"` 的主发现结果
   - 再看 `relevance_score`
   - 同分附近再参考 `view_count`
   - 如果 runtime 传入 `PoolDistributionSnapshot`，会在压缩前用 pool 饱和方向做一轮软重排：已拥挤的 topic/style/franchise 会轻微降权，手动传入的 undercovered axes 会轻微加权，但不会改写最终落库的 `relevance_score`
   - 若主发现数量不够，再进入 backfill

   backfill 的做法也不是简单“补一些随便的内容”，而是分两层：
   - 先问各个策略有没有 `create_backfill_strategy()`，如果有，就用更宽松的参数再跑一轮
   - 还不够的话，再从历史 `content_cache` 里捞尚未推荐的旧候选补位

   所以这一步实际解决的是“这轮找出来的内容，哪些应该算主力，哪些只是供给不足时的补货”。

   压缩重复主题和来源也不是一刀切删掉重复内容，而是：
   - 先尽量给不同 topic、不同 source 留坑位
   - 对重复 style 和重复 source 设一个上限
   - 装不下的内容先放进 deferred 队列，后面如果还有空位再回填

   这一步决定的是候选池“看起来像不像一个活的内容池”，而不是一串只会换标题不会换方向的重复片单。

7. **写入缓存池并交给推荐层整理**
   收口后的结果会通过 `_cache_results()` 写入 SQLite 的 `content_cache`。写入时不只存视频标题和 `bvid`，还会把 discovery 阶段已经得到的信号一并落下来，例如：
   - `relevance_score`
   - `relevance_reason`
   - `candidate_tier`
   - `topic_key`
   - `style_key`
   - `source_strategy`

   最近看过的内容即使被上游策略再次找到，也会用 `source_platform:content_id` 在 `_cache_results()` 写库前跳过，不再进入 `content_cache` 候选池。后续 `recommendation/` 的分类、文案预生成、MMR、多样性选择和 `reshuffle/append` 都只消费这个正式推荐池。

   换句话说，discovery 的产出不是“一次性的返回值”，而是一份会进入候选池、影响后续多轮推荐的中间资产。

这意味着 discovery 的目标不是单次找到“绝对最优的一条”，而是持续维护一个质量够高、来源够杂、还能解释为什么会命中的候选池。

### 兼容的直接 discover 收口

`ContentDiscoveryEngine.discover()` 仍保留直接收口路径，用于 CLI、离线评估、旧调用方和没有注入 `DiscoveryCandidatePipeline` 的 fallback。该路径会把策略结果 inline 评估、合并、排序、压缩并写入 `content_cache`：

1. **压缩重复主题和来源**
   只按分数排序还不够，因为高分内容很可能高度同质。引擎会再进入 `_compress_topic_repeats()` 做一轮轻量压缩，防止候选池被单一方向灌满。

   当前压缩主要看三个维度：
   - `topic_key`：防止同一搜索 query、同一相关推荐链、同一主题桶连着塞进来
   - `style_key`：防止全是同一种观看体感，比如一批全是 `deep_focus` 或全是 `quick_scan`
   - `source_strategy`：防止 `explore`、`related_chain` 之类单一来源刷满前排

   实现上不是一刀切删掉重复内容，而是：
   - 先尽量给不同 topic、不同 source 留坑位
   - 对重复 style 和重复 source 设一个上限
   - 装不下的内容先放进 deferred 队列，后面如果还有空位再回填

   这一步决定的是候选池“看起来像不像一个活的内容池”，而不是一串只会换标题不会换方向的重复片单。

2. **写入缓存池**
   收口后的结果会通过 `_cache_results()` 写入 SQLite 的 `content_cache`。写入时不只存视频标题和 `bvid`，还会把 discovery 阶段已经得到的信号一并落下来，例如：
   - `relevance_score`
   - `relevance_reason`
   - `candidate_tier`
   - `topic_key`
   - `style_key`
   - `source_strategy`

   最近看过的内容即使被上游策略再次找到，也会用 `source_platform:content_id` 在 `_cache_results()` 写库前跳过，不再进入 `content_cache` 候选池。这样推荐层在后续 `reshuffle`、`append`、常规推荐排序时，就不必重新跑一遍 discovery，也能直接利用这些结构化信号做多样性控制和快速选片。

   换句话说，discovery 的产出不是“一次性的返回值”，而是一份会进入候选池、影响后续多轮推荐的中间资产。

## Prompt 示例：LLM 在 discovery 里具体干什么

discovery 不是“把整个找片过程都交给 LLM”。当前实现里，LLM 主要做 4 类结构化工作：

- 帮 `SearchStrategy` 生成搜索 query
- 帮引擎评估“这条内容和这个人像不像对味”
- 帮 `ExploreStrategy` 生成陌生但合理的探索方向

它们有一个共同点：**都要求返回严格 JSON**。这样下游逻辑才能稳定解析，而不是靠自然语言瞎猜。

### 1. 搜索词生成 prompt

这一类 prompt 来自 `build_search_queries_prompt()`。它的任务很克制，不让模型长篇分析，只让它产出可以直接拿去搜 B 站的短 query。

示例：

```text
<task>
你要为 B 站内容发现生成一组可搜索的关键词组合。
</task>

<rules>
1. 输出必须是严格 JSON，不要附带解释。
2. query 必须是适合 B 站搜索的短词或短组合，不要写成长句。
3. 优先组合“兴趣主题 + 内容风格/需求”，避免过泛的词。
4. queries 数量控制在 5 到 10 个。
</rules>
```

给模型的 `user_input` 会长这样：

```json
{
  "core_traits": ["理性", "好奇", "重结构"],
  "interests": [
    {"name": "国际局势", "category": "知识", "weight": 0.92},
    {"name": "历史", "category": "知识", "weight": 0.84},
    {"name": "纪录片", "category": "影视", "weight": 0.79}
  ],
  "deep_needs": ["建立判断确定性", "看清事件背后的结构"]
}
```

理想输出通常是这种风格：

```json
{
  "queries": [
    "国际局势 因果链",
    "历史事件 深度解析",
    "纪录片 结构讲解",
    "地缘政治 长视频",
    "国际新闻 背后逻辑"
  ]
}
```

落地时 `SearchStrategy` 还会再做一层保护：

- query 生成使用稳定 compact profile summary，不额外注入 core memory；对 DeepSeek / OpenAI-compatible 结构化生成显式关闭 thinking，并把输出预算收口到短 JSON 级别
- 成功解析出的 query 会按 `profile_kw_digest + pool hints digest` 在进程内缓存约 6 小时；同画像、同池子分布提示的重复 refresh / CLI 调用会直接复用，不再重复打 `discovery.search.queries`
- 解析 JSON 失败就放弃这轮 LLM 结果
- query 去重
- 最多取配置允许的前几条
- 如果收到 `PoolDistributionSnapshot`，会把 `to_prompt_hints()` 注入 prompt 的 `<pool_distribution_hints>`，让模型把 `avoid_topics` / `avoid_styles` / `avoid_franchises` / `prefer_axes` 当作软指导；`avoid_styles` 会先归一化为封闭观看模式 key，这些信号不能覆盖画像相关性，也不能把 `source_deficits` 里的平台名当成搜索主题
- 如果 snapshot hint 构造失败，会记录异常并回退到普通 query 生成
- 如果 LLM 完全不可用，就回退到“兴趣名 / 核心特质”直接拼出的本地 query

### 2. 排行榜分区本地轮转

`TrendingStrategy` 并不是把所有分区榜都抓一遍，也不再为“选 4 个分区”调用 LLM。它会先固定抓 `rid=0` 全站榜，再从内置的非 0 分区 rid 集合中按 `profile_kw_digest + cycle + rid` 做确定性洗牌，每轮最多取 `max_related_rids` 个分区。当前默认 `max_related_rids=4`；一轮候选分区没取完前不会重复，轮末不足 4 个就只取剩余分区，下一次再进入新一轮洗牌。

也就是说，全站榜一定会看，分区榜负责用零 token 成本覆盖更多热门区域。真正的个性化筛选留给后面的内容 evaluator：榜单候选会按 rid round-robin 交错，再由统一 evaluator 判断是否和用户画像匹配。

### 3. 内容相关性评估 prompt

这是 discovery 里最关键的一类 prompt。runtime 的统一待评估池会把 B 站 / 小红书 / 抖音 / YouTube / X / 知乎候选交给 `ContentDiscoveryEngine.evaluate_content_batch()`；直接 discover 兼容路径仍可逐条调用 `evaluate_content()`。

它的 system prompt 重点是：

```text
<task>
你要评估一个内容与这个用户画像的匹配度。
</task>

<rules>
1. 输出必须是严格 JSON，不要附带解释。
2. score 范围必须在 0 到 1 之间。
3. reason 仅供内部诊断：低于 0.5 写空串，其余写不超过 30 个 Unicode 字符的中文依据。
4. 不要只说“因为热门”或“因为看过类似的”，要结合用户画像。
</rules>
```

这时传给模型的内容是“画像摘要 + 单条内容摘要”：

```json
{
  "profile_summary": {
    "core_traits": ["理性", "重结构"],
    "cognitive_style": ["喜欢结构化拆解", "先看证据再下判断"],
    "values": ["真实", "自主"],
    "motivational_drivers": ["理解底层逻辑", "减少噪声"],
    "current_phase": "重新整理信息源",
    "life_stage": "工作稳定期",
    "mbti": {
      "type": "INTJ",
      "confidence": 0.76,
      "dimensions": {"EI": {"pole": "I", "strength": 0.8}},
      "inferred_from": ["长期观看模式"]
    },
    "deep_needs": ["建立判断确定性"],
    "interest_domains": [
      {
        "domain": "国际局势",
        "weight": 0.92,
        "specifics": ["中东局势"],
        "first_seen": "2026-01-01",
        "last_seen": "2026-05-01",
        "source": "behavior"
      }
    ],
    "interests": [
      {
        "name": "国际局势",
        "category": "知识",
        "weight": 0.92,
        "first_seen": "2026-01-01",
        "last_seen": "2026-05-01",
        "source": "behavior"
      },
      {"name": "历史", "category": "知识", "weight": 0.84}
    ],
    "disliked_topics": ["标题党", "低质混剪"],
    "style": {
      "preferred_duration": "long",
      "preferred_pace": "moderate",
      "quality_sensitivity": 0.82,
      "humor_preference": 0.2,
      "depth_preference": 0.9
    },
    "source_platform_mix": {"bilibili": 0.7, "youtube": 0.3},
    "recent_awareness": [
      {
        "date": "2026-05-17",
        "observation": "最近避开标题党内容。",
        "trend": "更偏向可信来源。",
        "emotion_guess": "可能在降噪。"
      }
    ],
    "active_insights": [
      {
        "hypothesis": "用户最近在主动收敛信息源。",
        "evidence": ["连续 dislike 低质混剪"],
        "confidence": 0.83,
        "validated": true
      }
    ]
  },
  "content_summary": {
    "title": "20分钟讲透中东局势的历史成因",
    "up_name": "知识区UP",
    "description": "从殖民历史、宗教结构到现代地缘关系，梳理冲突演化。",
    "duration": 1250,
    "view_count": 820000,
    "source_strategy": "trending"
  }
}
```

理想返回值会像这样：

```json
{
  "score": 0.86,
  "reason": "契合结构化解释偏好和近期国际议题"
}
```

收到后，引擎还会继续做这些事：

- 把 `score` clamp 到 `0.0 ~ 1.0`
- 先按分数归一化 `reason`，再写回 `DiscoveredContent.relevance_reason`
- 单条 JSON 非法会返回 `0.0`；batch 缺失 / 坏 member 进入有界 subset retry，耗尽后由 pipeline 释放整批 claim

#### v0.3.x 负样本锚定（batch evaluator）

`ContentDiscoveryEngine._evaluate_batch` 在每次 batch 调用前会通过 `_get_negative_exemplars()` 从事件层拉一份「最近真正不喜欢」的标题列表（来自 `soul/negative_exemplars.py` 的 recency-weighted、去重、80 字截断、最多 16 条），并作为 `negative_examples=` 透传给 `build_batch_content_evaluation_prompt()`：

- 引擎实例内部 `_get_negative_exemplars` 的 exemplar 缓存形如 `(timestamp, latest_event_id, exemplars)`，命中条件是 `latest_event_id` 未变且 `< 300s`（即 5 分钟 TTL）。同一窗口内的多次 batch 共用一次 `query_events` I/O；用户新打一条负反馈后，`latest_event_id` 改变，下一次 batch 立即看到新样本。注意这是 exemplar 池本身的缓存；候选**分数** key 使用实际 prompt-visible exemplars 的确定性 digest，不使用事件水位，所以无关事件不会误伤命中，而负例内容真实变化一定 miss。
- 上游 `_get_negative_exemplars()` 与 `recent_negative_exemplars()` 都把异常吞成 `None`/`[]`，event 表为空或存储抖动都不会中断 batch；user prompt 自动退回到无 `<negative_examples>` 形态，cache prefix 不被打断。
- 拿到 exemplars 后 prompt builder 把它放在 `<source_context>` 与 `<content_batch>` 之间（系统规则 10/11 让 LLM 按话术 / 商业意图 / 标题结构层面去对照打分，而不是关键词重叠）。前置 `[soul.preference] satisfaction_filter_enabled` 未打开时，事件分类仍在跑，所以负样本池可以提前积累。batch 评分缓存 key 带 exemplars 内容 digest，避免负样本变化后继续复用旧分数。

所以这里的 LLM 不是“决定推荐”，而是在给候选池补一个统一、可比较的相关性分数。

### 4. 跨领域探索 prompt

`ExploreStrategy` 的优先 query 来源是统一 `KeywordPlanner` 写入的 `discovery_keywords(keyword_kind="explore")`。当 `[discovery].unified_keyword_planner_enabled=true` 时，它会从这个 explore 候选池 claim query 并直接搜索；池里没有可 claim query 时，本轮 explore 返回空，避免重新触发旧的 `discovery.explore.queries` LLM。只有 planner 关闭或没有注入 `KeywordFetchCoordinator` 的兼容路径，才会回到旧的 `build_explore_domains_prompt()`：现场让模型提出“什么陌生方向值得搜”。这条旧路径同样使用 compact 画像，不额外注入 core memory；真实环境下 DeepSeek / OpenAI-compatible 的 thinking 会把短 JSON 任务放大成几千 completion tokens，所以 `discovery.explore.queries` 显式关闭 thinking，并把 `max_tokens` 收口到 `2048`。

示例：

```text
<task>
你要为这个用户设计 3 到 5 个“高相关但有陌生感”的跨领域探索方向。
</task>

<rules>
1. 输出必须是严格 JSON，不要附带解释。
2. domain 不能直接重复用户现有高权重兴趣词。
3. domains 至少覆盖 3 类不同内容方向，不要都落在同一个抽象轴上。
4. 同一母题的换皮变体最多只能保留 1 个，例如“博弈论 / 桌游机制 / 纳什均衡 / 策略模型”不能同时出现。
5. 输出保持短 JSON，每个 domain 只允许 `domain`、`novelty_level`、`queries` 三个字段。
6. novelty_level 范围必须在 0.65 到 0.95 之间。
7. 每个 domain 生成 2 到 3 个适合 B 站搜索的 query，不能写抽象句子。
</rules>
```

如果用户当前兴趣是“国际局势 / 历史 / 纪录片”，一个合理输出可能是：

```json
{
  "domains": [
    {
      "domain": "战争工业史",
      "novelty_level": 0.72,
      "queries": ["战争工业史 纪录片", "军工体系 深度讲解"]
    },
    {
      "domain": "外交谈判案例",
      "novelty_level": 0.74,
      "queries": ["外交谈判 案例解析", "国际博弈 深度解读"]
    }
  ]
}
```

现在这层 prompt 还会主动约束“外推多样性”：

- 结果至少横跨 3 类不同内容方向，而不是围着一个相邻主题连续换词
- 至少 2 个方向要明确锚定用户前 5 个高权重兴趣，优先做“核心兴趣的近邻扩展”而不是直接漂去远域
- 最多只允许 1 个完全不直接提及核心兴趣词的远邻方向
- 同一母题的近义变体只能保留 1 个，避免 `博弈论 / 桌游机制 / 策略模型` 一类方向同时灌进池子
- `why_it_might_resonate` 必须先回到用户的认知需求和信息处理方式，而不是只按题材表面相似来联想

但模型返回后，`ExploreStrategy` 不会无脑全收。它还会继续做过滤：

- 去掉与当前高权重兴趣完全重复的 `domain`，但允许“纪录片幕后 / Fate 世界观扩展”这类近邻方向保留
- 先把能直接锚定核心兴趣的方向排到前面；如果锚定方向已经够了，远邻方向最多只留 1 个
- 清洗 query，去重并裁到上限
- 先搜索这些 query，再把搜到的视频重新送去做内容相关性评估
- 最终把评分和 `novelty_level` 组合成探索后的 `relevance_score`，对没有直接兴趣锚点的远邻方向再加一层轻量距离惩罚

所以 explore 的关键不是“随机拓圈”，而是“先提出可解释的新方向，再验证这些方向里的具体视频值不值得进池”。

成功生成的 domain 会按 `profile_kw_digest + covered_topic_groups + max_domains + queries_per_domain` 在进程内缓存约 6 小时；如果画像和当前池子已覆盖 topic 没有变化，下一轮 explore 会复用这些 domain，不再重复调用 `discovery.explore.queries`。

### 5. 一个完整的 prompt 调用链例子

假设用户最近明确偏好“国际局势 + 深度讲透”，一轮 discover 里可能会发生下面这条链：

1. `SearchStrategy` 先用画像摘要生成 query，如“国际局势 因果链”“中东局势 深度解析”。
2. `TrendingStrategy` 固定抓全站榜，并按本地洗牌轮转抓取若干非 0 榜单分区 rid。
3. 搜索结果、榜单结果、相关推荐结果被映射成统一的 `DiscoveredContent`。
4. `evaluate_content()` 再逐条问模型：“这条视频和这个人画像匹配度多少，为什么？”
5. `ExploreStrategy` 补一些相邻但更陌生的方向，比如“战争工业史”“外交谈判案例”。
6. 所有结果统一合并、排序、压缩后写入 `content_cache`。

这里 LLM 真正提供的是 3 种能力：

- 把画像翻译成“可执行查询”
- 把候选翻译成“可比较分数”
- 把兴趣边界翻译成“可解释探索方向”

而抓数据、去重、压缩、补货、落库这些稳定性工作，仍然是代码在做，不是 LLM 在做。

## 典型场景示例

下面用一个更具体的例子说明 discovery 在做什么。

假设用户最近的画像大致是：

- 最近连续看“国际局势深度解读”“历史结构分析”“纪录片式知识内容”
- 聊天里明确说过“我想把新闻背后的因果链看明白”
- 对“标题党快讯”“浅层复读热点”给过 `dislike`
- `exploration_openness` 中等偏高，说明可以接受一点陌生但合理的新方向

这时四类策略可能分别产出：

- **SearchStrategy**：生成诸如“国际局势 因果链”“历史事件 深度解析”“中东局势 纪录片式讲解”的搜索词，从搜索结果里拿到一批初始候选。
- **TrendingStrategy**：先抓全站榜，再按本地轮转覆盖新闻、知识、纪录片、生活、游戏等分区，对榜单内容逐条做画像相关性评估，把“热点里真正对味”的内容留下。
- **RelatedChainStrategy**：从用户最近明确喜欢过的一条深度解读视频出发，沿相关推荐继续挖相邻内容，找到“同主题但更细分”的延展视频。
- **ExploreStrategy**：推断用户也许会对“地缘政治纪录片”“战争工业史”“外交博弈案例拆解”这类稍远但心理需求相通的方向感兴趣，再去搜索并评估。

最终进入池子的结果，不一定全是“国际新闻”四个字直接相关的内容，也可能包括：

- 一条解释某次历史冲突长期结构成因的纪录片
- 一条拆解现代外交策略的长视频
- 一条从产业链视角解释战争背后资源竞争的知识向内容

这些内容的共同点不是表面标签相同，而是都满足了画像里那条更深的需求：**用户想看见事件背后的结构，而不是只接收结果本身。**

## 关键概念

### primary 与 backfill

- `primary` 是主发现结果，代表这轮策略正常跑出来、相关性更强的候选。
- `backfill` 是补货结果。当主发现数量不够时，系统会放宽部分策略参数，或从历史缓存中补一些仍然可用的候选，避免候选池太空。

它的意义不是“降低质量”，而是让系统在供给不足时仍然有内容可推，同时把“这是主发现还是补货”保留下来，供后续排序使用。

### topic_key

`topic_key` 用来表示“这条内容大致属于哪个主题桶”。

例如：

- 搜索词是“中东局势 因果链”时，搜索策略可能直接把这个 query 归一化成一个 `topic_key`
- 相关推荐链从某个 seed 视频扩出来时，会把整条链绑定到同一个 `topic_key`

这样做的目的，是让引擎能识别“这些片虽然标题不同，但其实是在讲同一个方向”，从而在入池时先压掉部分重复项。

### style_key

`style_key` 不是题材，而是用户消费内容时的观看状态信号。题材和开放分类继续交给 `topic_group` / 标签 / embedding；`style_key` 固定为封闭的观看模式词表：

- `deep_focus`：深度专注，原理、结构、系统分析
- `quick_scan`：快速扫信息，热点、更新、短知识
- `hands_on`：跟做学习，教程、攻略、实操步骤
- `decision_support`：辅助决策，测评、盘点、对比
- `story_immersion`：叙事沉浸，纪录片、人物、事件复盘
- `opinion_sparring`：观点碰撞，评论、立场、辩论
- `social_chat`：陪聊 / 对谈，闲聊、访谈、播客感
- `daily_wander`：日常漫游，vlog、生活流、低目标浏览
- `mood_release`：情绪释放，搞笑、整活、吐槽、二创
- `aesthetic_browse`：审美浏览，视觉、混剪、空镜、展示
- `ambient_companion`：背景陪伴，背景音乐、白噪音、长陪伴
- `live_pulse`：现场脉冲，直播切片、现场、赛事高光
- `curiosity_spark`：新鲜猎奇，奇怪事实、冷门切口、意外发现

这个字段的作用，是让下游推荐层能避免一整批都占用同一种注意力状态。

## 为什么要多策略并存

四类策略并不是互相替代，而是在解决不同的供给问题：

- **Search** 最擅长把明确兴趣翻译成可搜索的 query，命中快，解释性也强。
- **Trending** 负责从大盘热点里筛出“虽然很热，但也确实适合这个人”的内容。
- **RelatedChain** 擅长沿着已有高价值种子往下深挖，常常能找到更贴的相邻内容。
- **Explore** 则负责防止系统越来越窄，只会重复喂同一类题材。

如果只有搜索，系统会偏保守；如果只有探索，系统又容易飘。多策略并存的价值，就是在“稳定命中”和“适度意外”之间维持平衡。

## 统一关键词 planner / 背压（v0.3.124 起默认开启）

> Discover 背压重构 P1。挂在 `[discovery].unified_keyword_planner_enabled` 后面，**v0.3.124 起默认 `true`**——各平台 search 关键词走统一规划器 + 关键词存储；设为 `false` 可逐字回退到各自旧的逐平台 LLM 生成路径（旧路径保留、回退无副作用）。

此前多个 search 关键词生成器（B 站 `search`、小红书 `xhs-search`、抖音 `search`、YouTube `yt_search`、X `x-search`、知乎 `zhihu-search`、Reddit `reddit-search`、Bangumi `bangumi-search`、Linux.do `linuxdo-search`、V2EX `v2ex-search`、微博 `weibo-search`）各自独立调 LLM、各发一份画像。统一 planner 把它们收敛成一套「双缓冲 + 缺口拉动」的背压模型，接管各平台 **search 关键词**，并接管 B 站 `ExploreStrategy` 的 `keyword_kind="explore"` query cache 生成；`trending / related / hot / feed / channel / creator / subreddit / ranked / latest` 及各自的 budget/cadence **原样不动**。关键词的 legacy / hybrid / inspiration 生成模式只决定词从哪里产生，不关闭 V2EX 正式 Search 的已配置 Exa / You 召回；外部 provider 不可用时，V2EX 才在 latest/hot 上做精确优先、核心词受限放宽的匿名 fallback。微博 `hot` 只把公开热搜词变成本轮查询种子，不会把热搜条目本身写成候选。

**关键词存储**（`storage/database.py`，表 `discovery_keywords` + `discovery_keyword_yield` + CAS 单飞锁 `discovery_planner_lock`）是生成侧的缓存 / 历史 / yield 账本。状态机：`pending → claimed → (内联) used / failed` 或 `→ (异步) executing → used / failed`；任意在途态可经租约回收 / 预算回滚回到 `pending`，小红书任务遇到平台安全验证时也会从 `executing` 无损回到 `pending` 且不增加 attempts。画像 digest 变化时，planner 会先原子整理 `regular/pending`：当前 digest 优先保留；旧 digest 中创建未超过 `keyword_digest_grace_hours`、不命中由当前库存饱和度产生的平台 `avoid_topics`、且未超过动态高水位的词继续可领取，并保留原 digest 与全部生成溯源；过龄、供给饱和、重复或超额才变成 `expired`。普通 user dislike 不参与关键词过期或撤销。设宽限为 `0` 即恢复旧版硬过期。`claimed/executing`、终态行和 `keyword_kind="explore"` 均不参与整理。`keyword_kind` 区分 `regular` 与 `explore`：普通 search 只 claim `regular`，老 B 站 `ExploreStrategy` 在 planner 开启时只 claim `explore`。在途四元组 `(platform, keyword, profile_kw_digest, keyword_kind)` 部分唯一；已经实际搜索后因零产出或全量重复而变成 `expired` 的词保留 `used_at`，在 `history_window_hours` 内继续参与近期词冷却；宽限保留的 pending 也进入生成历史，避免库存尚在时又生成同族关键词。

**生成（planner loop）**：`runtime/keyword_planner.py::KeywordPlanner` 作为独立后台对象（在 `api/runtime_context.py` 构造、持 `llm_service`+db+config，由 refresh controller 的 `run_forever` 拉起），每 `planner_poll_seconds` 轮一次：

1. 算 `due` = 缓存 `pending` 低于 `kw_cache_low` **且** 真实缺口 > 0（复用 controller 的补池口径，含 raw headroom + 在途）；B 站额外催化（池低于目标 / ≥ `signal_event_threshold` 信号）也进 due。
2. due 非空 → 现读最新画像 → 取**短事务单飞锁并在调用 LLM 前释放** → `build_merged_keywords_prompt`（一次合并调用、compact 画像只发一份并按 profile layer 稳定前置、按平台分块、静态 system 命中 prompt-cache；调用时关闭 thinking 和额外 core memory 注入）→ 解析 `{platform: [...]}` → 按当前 `profile_kw_digest` 写 `regular/pending` 补到 `kw_cache_high`。如果同轮带 `<explore_domains>`，其 `queries` 写成 `keyword_kind="explore"`，供 `ExploreStrategy` 后续 claim。启用 inspiration-only 实验模式时，due 平台跳过这次 merged call，改为 coverage snapshot → like 二级兴趣抽样 → 轴库 + 确定性 probe → search provider grounding（默认 local cache / 平台源 / Bing RSS / Exa / You.com）→ 单次 `discovery.keyword_inspiration` 生成 `{axes[], keywords[]}` → 剔除不属于本轮选择或 core 明确命中另一个画像兴趣的错误归因词 → `materialize_platform_keywords()` 按「兴趣 breadth 优先、同兴趣第二轴 depth 后补」的 round-robin 装配 → 各平台 `regular/pending` 池。selection ledger 只记录经过装配与近期过滤后确有关键词留下的 realized interests，未分到平台配额或只产出重复词的兴趣不提前降权；如果 B 站 explore 到期且有补货空间，同轮使用同一批 selected interests / grounding / 单次 LLM 输出填充 B 站 `explore/pending` 词，成功写入后推进 explore 计划时间。
3. LLM 失败 / 缺某平台块 → 该平台回退确定性权重排序兴趣名；写入前按大小写、空白与标点归一化，强制剔除近期已消费词及同批重复，并把只在旧 query 尾部增加「复盘 / 解析 / 分析 / 教程 / 盘点 / 测评」等通用形式词的结果视为同一 query family。仍无新词（稀疏画像）时，只允许回收超过 `history_window_hours` 且曾有真实 yield 的旧 `used` 词，不立即重放刚搜索过的零产出词。

**缺口驱动抓取 + 三种执行形态**（`runtime/keyword_fetch.py::KeywordFetchCoordinator`，每个 search 抓取点显式 flag 分支，flag-off 行为逐字不变）：距上次 ≥ 各平台自身 `min_interval`、缺口 > 0、且 store 有可领词 → 原子 `claim` `fetch_batch` 个 → 经 P1.5 注入口（`queries` / `keyword_ids`）喂进搜索：

- **内联评估并入池**（B 站 search、抖音 plugin）：抓 → 评估 → admit 都在本调用内。B 站沿用整轮结算；抖音按关键词分别结算：有结果为 `used`，真实空结果为 `failed`，插件 timeout / failed 无损退回 `pending` 且不增加 attempts，预算耗尽的当前词与尚未执行词回滚，已经成功的前序词仍保留。
- **fetch-only → 交共享 pipeline 延后入池**（X、YouTube）：producer 只取 raw 候选交 `discovery_candidates`，交付即 `used`（admit 由 `DiscoveryCandidatePipeline` 后续做）。
- **真正异步**（仅小红书，扩展 out-of-band）：`claim` → 入队带 `source_keyword_id` 的 xhs 任务 → 词 `executing` → task-result 回调标 `used`/`failed`。`claim` 后被预算拒（XHS enqueue `ok=False` / 抖音 `search_aweme` 抛 `DouyinBudgetExhausted`）→ 词 `claimed → pending` 回滚。抖音插件 timeout / failed 与 XHS `rate_limited` 都按瞬时平台故障无损退回 pending、attempts 不变；SQLite 平台冷却期间 XHS producer 不再 claim / 生成新词，来源关闭时 `/next-task` 也不会消费已排队任务。

**yield / 重复率端到端**：候选全程透传 `source_keyword_id`，入池（`_cache_results` 这唯一 admission 收口）按 `(source_keyword_id, content_id)` **幂等**回填 `yield_count`（与 `used` 解耦，覆盖三形态）；连续 0 产出且过保护期的 `used` 词退役为 `expired`。候选预过滤还会按 keyword 汇总 `known_candidate / known_cache / duplicate_in_batch`：某词本轮返回的所有身份都已存在时立即标记 `expired` 并补 `used_at`，让真实重复结果直接驱动下一轮换词，而不是等画像变化。

**成本可观测**：合并调用是**一次 response**，token 无法在平台间拆分 → 统一记单一 caller `discovery.keyword_planner`（`openbiliclaw cost --by caller` 可见 search 关键词总成本随合并而塌缩）。per-platform 归因不靠冒充 token 拆分，而靠 planner 每轮 emit 的结构化 ledger（`{platform: {generated, yield}}`，`generated` 取本轮产词数、`yield` 取 `keyword_yield_total(platform)` 的累计 admit 产出），落在 `keyword planner cycle ledger` 日志行、并存于 `KeywordPlanner.last_cycle_ledger`。digest 整理另以聚合计数 `current/reused/expired_aged/expired_blocked/expired_excess` 暴露在 `last_digest_grace_ledger` 和日志中，不记录关键词、画像或 digest 正文。

**生成结果复用与轮换**：一次成功的 merged keyword JSON 会按 `profile_kw_digest + 平台需求块 + recent_keywords + 池子避让 / prefer hints + gen_batch` 缓存在进程内，TTL 使用 `[discovery].plan_ttl_hours`。同一批尚未消费时仍可复用，避免重叠 planner pass 重复调用；一旦关键词被实际搜索并进入近期历史，cache key 就变化，稳定画像也会重新生成下一批，而不会在整个 TTL 内回放旧 JSON。

**兴趣丰富度**：planner prompt 使用与 B 站 search / trending / explore 相同的 compact query profile。`interests` 不是单纯权重前 64，而是从最多 128 个候选中结合 cached embedding 多样性、类别覆盖和 `disliked_topics` 距离选出，避免多平台首批关键词都围着同一强兴趣或同义标签转；embedding cache 未命中时不额外调用 provider，按权重顺序降级。

**P2 打磨（供给优势 / 弃权 / 轮换）**：合并 prompt 的静态 system 里加了**平台供给优势表**（B站 学习区/梗、小红书 生活/美妆、抖音 娱乐/热点、YouTube 英文长内容、X 实时/英文、知乎中文问答/深度回答/经验复盘、Reddit subreddit 经验讨论/技术问答/开源项目、Bangumi ACG 作品名/作者或监督/题材/目录检索、Linux.do 中文技术社区主题/故障排查/开源实践），让模型把用户兴趣映射到各平台真正有货的方向；并允许**弃权**——某平台供给与用户兴趣不匹配时可少出或返回 `[]`。planner 区分「弃权」与「调用失败」：合并调用**成功**但某平台返回空 = 故意弃权（**不**回退确定性兴趣名、本轮跳过）；**整次调用失败**才对所有 due 平台回退兴趣名。轮换上 `claim_keywords` 严格 FIFO（最旧 pending 先出）；非弃权平台仍低于低水位时，`recycle_oldest_used` 只补充超过近期窗口的历史有效词，近期词优先留给下一轮新生成。
**P2 打磨（供给优势 / 弃权 / 轮换）**：合并 prompt 的静态 system 里加了**平台供给优势表**（B站 学习区/梗、小红书 生活/美妆、抖音 娱乐/热点、YouTube 英文长内容、X 实时/英文、知乎中文问答/深度回答/经验复盘、Reddit subreddit 经验讨论/技术问答/开源项目、Bangumi ACG 作品名/作者或监督/题材/目录检索、微博中文公共话题/实时讨论/创作者博文），让模型把用户兴趣映射到各平台真正有货的方向；并允许**弃权**——某平台供给与用户兴趣不匹配时可少出或返回 `[]`。planner 区分「弃权」与「调用失败」：合并调用**成功**但某平台返回空 = 故意弃权（**不**回退确定性兴趣名、本轮跳过）；**整次调用失败**才对所有 due 平台回退兴趣名。轮换上 `claim_keywords` 严格 FIFO（最旧 pending 先出）；非弃权平台仍低于低水位时，`recycle_oldest_used` 只补充超过近期窗口的历史有效词，近期词优先留给下一轮新生成。

**P3 自适应（per-platform 饱和避让 + 动态缓存水位）**：避让从「全局一份」细化到**逐平台**——`_avoid_hints(profile)` 用新增的 `Database.get_pool_topic_counts_by_platform()`（与 servable 同口径）算出**每个平台自己池里**已饱和的 `topic_group`（阈值 `max(5, 本平台池量//5)`、取 top-12），写进该平台的合并 prompt 分块；某平台池量不足 floor 10 时回退到全局热门 topic 避让。若正式池整体还是空的，planner 会先用 `build_cold_start_pool_snapshot(profile)` 生成 `cold_start=true` hints：高权重兴趣进入各平台 `avoid_topics` 软预算，次级兴趣 / 兴趣域进入 `prefer_axes`，merged prompt 规则要求每个平台首批关键词保留少量强兴趣入口但至少一半覆盖其它画像相关方向。这样「小红书池里美妆已满」只压小红书的美妆词、不误伤 B站；而真正冷启动时，各平台不会同时把第一批关键词全部押在同一个强兴趣上。缓存高水位也从静态 `kw_cache_high` 改为**按平台产出动态**：`_target_high(platform)` 用 `ceil(本平台缺口 / 平均单词产出)` 估算需要囤多少词，`平均产出 = keyword_yield_total(platform) / used_keyword_count(platform)`（需 ≥10 个 `used` 样本才采信），夹在 `[max(1, kw_cache_low + fetch_batch), kw_cache_high*3]`；样本不足 / 无缺口 / 平均产出为 0 时回退静态 `kw_cache_high`。高产平台少囤词、低产平台多囤词，缓存深度随真实 admit 产出自适应。v0.3.124 起默认开启、flag-off 逐字回退。

**P3.3 数据驱动供给优势**：P2.1 的 `<supply_advantage>` 是**静态先验**（B站擅长学习区、小红书擅长美妆…）；P3.3 在它之上叠一层**这个用户的真实 admit 历史**。新增 `Database.get_admitted_topic_counts_by_platform()`——口径与 P3.1 的「当前可服务池」**不同**：它统计每个平台**历来入过缓存**（非 dislike、可链接、不限是否已服务/已看）的 `topic_group`，反映「这个平台为该用户实际产出过哪些主题」。`KeywordPlanner._supply_hints()` 取各平台 top-8（阈值 `max(3, 本平台入池量//10)`、入池量不足 floor 10 则空），并**减去该平台当前的 `avoid_topics`**——所以「擅长但当前饱和」的主题只留在避让里，绝不同时出现在「主推」和「避让」。结果作为每平台 `supply_hint` 写进合并 prompt 分块（静态 system 描述该字段语义、`<supply_advantage>` 表保持不变，prompt-cache 不破）；冷启动无历史时该字段为空、模型只依据静态表。意义：用户若在某平台稳定看某偏门主题（如抖音上的硬核科普），planner 会学到并优先把相关兴趣往该方向映射，而非死守平台刻板印象。

**合并调用的 ask 收口 + 动态 max_tokens**（真实模型端到端验证补强）：合并生成是全系统输出最大的一次调用（每个 due 平台 × 至多 `gen_batch` 个词同在一个 JSON）。两处保证不被截断：① 给模型的每平台 `need` **收口到 `gen_batch`**——P3.2 的动态水位可达 `kw_cache_high×3`，但解析每平台只保留 `gen_batch`，若按 80 去要、只留 30，既浪费模型输出又把排在 JSON 靠后的平台顶向截断；现在「要多少＝留多少」。② 合并调用的 `max_tokens` 不再用固定默认，而是**按本轮实际要词量动态算**：`max(4096, sum(收口后 need) × 48 + 1024)`，随平台数 / `gen_batch` 自适应、留足余量（`max_tokens` 是天花板、按真实输出计费，放大几乎零成本）。否则靠后的平台会被截断、退回兴趣名兜底（实测 deepseek 下 5 平台 ×30 词若限额过小，youtube/twitter 会退化成裸兴趣名；修复后各平台均满额、英文平台正确出英文）。

**默认开启 / 如何回退**：v0.3.124 起 `[discovery].unified_keyword_planner_enabled` 默认 `true`，无需配置即生效（其余 `kw_cache_high/low`、`gen_batch`、`fetch_batch`、`history_window_*`、`claim_lease_minutes`、`planner_poll_seconds`、`plan_ttl_hours`、`keyword_digest_grace_hours` 用 §6 默认即可，详见 `docs/modules/config.md`）。要回退旧逐平台生成，把总开关设为 `false`；只回退跨 digest 复用则把 `keyword_digest_grace_hours=0`。两者均在重载后生效。端到端正确性由 `tests/test_keyword_backpressure_e2e.py` 在 flag-on 下覆盖，回退路径由 producer / planner 的 flag-off 与 grace=0 测试覆盖。

## 已实现功能

| 任务 | 状态 | 说明 |
|------|------|------|
| 5.1 搜索策略 | ✅ | compact 画像生成搜索词（关闭 thinking / core-memory 注入）+ 6 小时 query 缓存 + B 站搜索 + `bvid` 去重 + `DiscoveredContent` 映射 |
| B 站近期供给 lane | ✅ | 生产 composition 在既有 SearchStrategy 请求预算内预留 1 个 `pubdate` 请求、最多 5 条；普通/近期结果交错进入评估窗口，并以 `search:recent` + raw provenance 留痕。它不改变 relevance、admission、来源策略或配额。 |
| 5.2 排行榜策略 | ✅ | 全站榜 + 非 0 分区 rid 本地洗牌轮转覆盖 + LLM 评分筛选 |
| 5.3 相关推荐链策略 | ✅ | 事件种子 + 偏好/策略兜底种子 + 2 层相关推荐链 + LLM 评分过滤 |
| 5.4 跨领域探索策略 | ✅ | compact 画像生成短 JSON 探索 domain + 按 covered topic 缓存 + query 搜索 + exploration bonus + prompt 级外推多样性约束 |
| 5.5 内容评估 | ✅ | `evaluate_content()` 已被四类发现策略复用（含 SearchStrategy） |
| evaluator prefilter shadow 证据门 | ✅ | 默认仍为 `shadow`：每个未命中评分缓存的候选先生成不含标题、URL、正文或原始画像的审计决策，LLM 返回后用随机 decision id 回填原始模型分数和统一 admission 阈值结果；provider / parse 失败在产品评分路径可保守结算为 0，但审计行保持 unjoined，绝不把合成 0 伪装成 eventual LLM score。required-interest 集合与实际 embedding 输入使用同一份按权重排序的 top-256；无 embedding service 也使用固定域分隔 digest namespace 落下可连接证据，`profile_interests_missing` 与缺失/异常向量一并作为 degraded fail-open 进入 gate。审计写失败同样 fail-open。即使显式配置 `enforce`，当前批无法完整生成并持久化每条决策证据时也整批保留 LLM 路径；`scripts/evaluate_prefilter_shadow_gate.py` 只读冻结 retained cohort 并计算 §6.4 gate，从不自动切换 `enforce`。 |
| 5.6 发现引擎编排 | ✅ | 并发执行策略 + 高分去重 + 直接 discover 缓存收口；runtime 正常路径通过待评估池 admission 到 SQLite 推荐池 |
| 统一待评估候选池 | ✅ | B 站、XHS、抖音、YouTube、X、知乎、Reddit、Bangumi、Linux.do 的原始候选先写入 `discovery_candidates(pending_eval)`；API runtime 先用 supply fill loop 按 `pending_eval + evaluating` 补足有效水位，入库前过滤历史候选和已缓存内容，再由唯一 `CandidateEvalCoordinator` 按 `[scheduler].eval_min_batch_size / eval_max_wait_seconds`（默认 15 / 90 秒）凑批，以最多 3×30 连续评估并串行 commit/admission；手动 CLI 固定 `1 / 0` 立即 drain。各路径共享 tokenized claim、统一阈值与 projected-inventory 规则。 |
| 统一待评估候选池 | ✅ | B 站、XHS、抖音、YouTube、X、知乎、Reddit、Bangumi、微博的原始候选先写入 `discovery_candidates(pending_eval)`；API runtime 先用 supply fill loop 按 `pending_eval + evaluating` 补足有效水位，入库前过滤历史候选和已缓存内容，再由唯一 `CandidateEvalCoordinator` 按 `[scheduler].eval_min_batch_size / eval_max_wait_seconds`（默认 15 / 90 秒）凑批，以最多 3×30 连续评估并串行 commit/admission；手动 CLI 固定 `1 / 0` 立即 drain。各路径共享 tokenized claim、统一阈值与 projected-inventory 规则。 |
| 小红书自动任务领取门与风控背压 | ✅ | 自动 search / creator / bootstrap 在 API claim 时再次检查小红书来源开关与全局 scheduler，关闭后旧 pending 任务不会触发浏览器页面；search / creator 默认以 20 分钟为中心做 ±25% 稳定抖动并持久化下一次领取时间。搜索每日默认 20 次，producer 只把 pending + in-progress 搜索队列补到 5 条。扩展识别安全验证 / 操作频繁 / 429 后，后端按连续轮次打开 1/2/4…小时（24 小时封顶）平台冷却，停止 producer 与所有 XHS task claim，并把关联 planner 关键词无损退回 pending；同一冷却内重复报告不加轮次，冷却后的正常任务成功才重置。 |
| M120 多事件循环并发控制修复 | ✅ | `DiscoveryConcurrencyController` 现在会按当前 event loop 重新绑定 semaphore，CLI `init` 的分阶段补货不会再在第二轮触发跨 loop `RuntimeError` |
| 候选供给升级 | ✅ | 主发现不足时触发 backfill，并把相关性 / 候选层级写入缓存 |
| M118 topic_key 与池子层压缩 | ✅ | Search / Related 现在会给候选带稳定 `topic_key`，发现引擎会先压缩同 topic 重复项，再写入 discovery pool |
| M119 style_key 风格标注 | ✅ | discovery 入池时会按标题/描述轻规则补 `style_key`，为推荐层的风格多样性约束提供稳定信号 |
| M120 候选池来源交错取样 | ✅ | `get_pool_candidates()` 现在会按 `search / trending / related_chain / explore` 交错取样，避免候选窗口被单一来源刷满 |
| M122 来源优先补齐与观看模式误判修正 | ✅ | 池子压缩时会优先保留不同 `source` 的候选，再限制重复 `style`；同时补强 `style_key` 规则，减少深度内容误判成轻聊天模式 |
| M123 按平台缺口补池子 | ✅ | runtime 在补货时先按 `[scheduler.pool_source_shares]` 统计平台族余量；当前 B 站默认 share 为 5，小红书 / 抖音 / YouTube / X / 知乎 / Reddit / Bangumi / Linux.do 各为 1，但默认只有 B 站启用，disabled 平台从有效配比中剔除。各源缺口交给对应 producer，统一进入 `discovery_candidates` batch 评估；超 raw-ceiling 配额的平台族才被压回 raw 配额内 |
| M123 按平台缺口补池子 | ✅ | runtime 在补货时会先按 `[scheduler.pool_source_shares]` 统计平台族余量；当前默认保存的 B 站 / 小红书 / 抖音 / YouTube / X / 知乎 / Reddit / Bangumi / 微博 share = 5 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1，但默认只有 B 站启用，disabled 平台会从有效配比中剔除。B 站缺口会按前端真实可换来源数计算，并用 raw-material headroom 夹住请求量；其余来源分别交给对应 producer。所有来源再统一进入 `discovery_candidates` batch 评估；超 raw-ceiling 配额的平台族才会被压回 raw 配额内 |
| runtime 调度参数配置 | ✅ | 后台 discovery 不使用 `discovery_cron`；`ContinuousRefreshController` 从 `[scheduler]` 读取 `refresh_check_interval_seconds`、`signal_event_threshold`、`trending_refresh_minutes`、`explore_refresh_minutes`、`discovery_limit` 和 `proactive_push_interval_seconds`，配置热重载后重建 controller 生效 |
| M124 LLM 评估窗口控费 | ✅ | runtime 按平台自身缺口传递补货 limit；各策略在 LLM 评估前把候选窗口收缩到 `max(6, limit*2)`、上限 90，少量补货时不再把几十条候选送去评分后立刻 suppressed；batch evaluator 优先要求 `{"results":[...]}` 以贴合 `json_object` provider，parser 兼容 fenced JSON、回显输入后追加结果、NDJSON object 序列和按内容 ID 映射的 object。成功响应里的缺失 / malformed member 只做有界 subset/split retry（深度 3、额外请求 6），不再展开逐条调用；provider 异常直接传播 |
| v0.3.74 eval-batch JSON 容错统一 | ✅ | `_evaluate_batch` 改用 `llm.json_utils.extract_llm_json_list()`，在原 fenced / echo / JSONL 基础上统一兼容 `results/items/data/output/scores/evaluations` wrapper、MiMo malformed `{ [ ... ] }` 数组包裹和 schema echo 后最终结果；解析失败仍按原有降级路径处理，不把示例 JSON 当作真实评分 |
| v0.3.147 eval 画像分层 prompt cache | ✅ | `_evaluate_batch` 构造 prompt 时通过共享 `profile_prompt_layers()` 把 `build_profile_summary()` 输出按稳定性拆为 core / life_context / interests / style_context / recent_context 五层；`ContentDiscoveryEngine` 实例内的 `PromptLayerRenderCache` 按层 digest 复用渲染文本，画像某一层变动时只替换该层，帮助 provider prompt-cache 命中更长稳定前缀 |
| v0.3.81 eval-batch 按内容 ID 绑定 | ✅ | batch 内容评估 prompt 会携带 `bvid/content_id`，解析时优先按返回 ID 写回 `score/reason/topic/style/franchise`。provider 乱序或漏项时，不再把后一条候选的 `relevance_reason` 写到前一条；无 ID 且数量不完整时进入 split-retry 路径，而不是整批直接逐条评估 |
| eval-batch 互动指标与封面图输入 | ✅ | `DiscoveredContent` / `discovery_candidates` / `content_cache` 透传观看、点赞、收藏、评论、分享、弹幕、转推、书签等指标；batch prompt 会带 `tags/body_text` 和互动指标，但画像摘要会先压缩到高权重兴趣、最新 awareness / insight 与完整避雷项。`[discovery].multimodal_evaluation_enabled=true` 且 evaluation 模型支持图像时，封面图经运行时图片缓存命中或白名单抓取后压缩为 image input 一并评估，并用 `cover_image_ref="cover:<content_id>"` 和图片前置文字锚点稳定绑定候选，自动使用更小 batch |
| 封面 image-only embedding 预热 | ✅ | 入池后除 MMR 文本 embedding 预热外，当 `[llm.embedding].multimodal_enabled=true` 且 embedding model 支持图像（如 `gemini-embedding-2` 或 `dashscope`/`qwen3-vl-embedding`）时，`_warm_cover_embeddings` 用 `prepare_cover_bytes_for_embedding` + `embed_image` 预热封面向量，并以 `image_embedding_cache_key_for_url(cover_url)` 为键落缓存，供 recommendation `precompute_delight_scores` 的封面视觉加成按 URL 命中（与 vision eval 开关独立；纯文本 embedding 自动跳过、best-effort 不抛错） |
| 多平台发布时间元数据 | ✅ | Bilibili、小红书、抖音、YouTube、X、知乎、Reddit、Bangumi 和 Linux.do 仅从语义明确的来源字段提取发布时间；`published_at` 统一规范为 UTC RFC 3339，只有相对时间时写 `published_label`。两字段贯穿 `DiscoveredContent` → `discovery_candidates` → `content_cache`，重新发现时空值分别保留已有非空值；缺失/异常值不影响候选入队。旧缓存不联网回填，也不以发现时间、任务时间、互动时间或推荐生成时间代替发布时间。 |
| 多平台发布时间元数据 | ✅ | Bilibili、小红书、抖音、YouTube、X、知乎、Reddit 和 Bangumi 仅从语义明确的来源字段提取发布时间；`published_at` 统一规范为 UTC RFC 3339，只有相对时间时写 `published_label`。两字段贯穿 `DiscoveredContent` → `discovery_candidates` → `content_cache`，重新发现时空值分别保留已有非空值；缺失/异常值不影响候选入队。旧缓存不联网回填，也不以发现时间、任务时间、互动时间或推荐生成时间代替发布时间。 |
| Evaluation Agent 证据驱动时效三态 | ✅ | evaluator 在同一请求中原子输出 `class/confidence/reason + validity_mode/valid_until/scope/evidence/state`，相关性分数保持时间中性；代码补齐评估/复审时钟、policy version 与 completeness。策略只在 `confidence>=0.80`、完整、`scope=core` 且 evidence 逐字 grounded 时，按已过明确 deadline 或 `expired/superseded` 状态 hard expire；其它内容 fail-neutral。1 / 14 / 120 天只是三类内容的复审节奏，旧 3 / 60 天行也只触发复审。`review_due` 回到待评估队列或进入可逆 `temporal_review_hold`，复审可恢复；`expired` 才进入 `rejected_temporal_stale` / `stale`。 |
| v0.3.x eval-batch 限流保护 | ✅ | batch LLM 调用若失败原因为 provider rate limit / cooldown / quota，不再降级到逐条 `evaluate_content()`，也不把候选当 0 分拒绝；runtime 待评估池会把本批 claim 释放回 `pending_eval`，待 provider 恢复后继续评估，避免一次 Gemini 429 放大成逐条请求或误淘汰整批候选 |
| v0.3.144 eval 双 worker + 默认 45 | ✅ | `DiscoveryCandidatePipeline.drain_pending()` 文本 batch 默认 45，默认一次最多领取 `batch_size * 2` 个候选（90 条，仍 clamp evaluator hard cap），`ContentDiscoveryEngine.evaluate_content_batch()` 默认用 2 个 worker 跑 LLM batch；多模态 eval 继续使用独立小 batch；外层 drain lock 和全局 LLM semaphore 仍负责多入口 / provider 级并发控制 |
| v0.3.154 eval prompt diet + replay gate | 🧪 | 内容评估画像统一走 `compact_content_prompt_profile_summary()`：20 条核心上下文、实验性 48 个兴趣、32 个兴趣域 × 每域 16 个 specifics、12 条近期觉察 / 洞察，避雷项不裁剪；每条候选另带最多 3 个 `related_interests` 长尾兴趣召回（来自画像块外的 49..256 recall pool，挂在 item 上而不是 profile block），digest 覆盖 compact summary + 精确 recall pool。64 / 12、增长后画像上的 80 / 16 和 96 / 16 最终 clean-commit replay 均未通过未放宽的相对门；其中 96 / 16 仅节省 `0.67%` 标准请求输入 token，因此改为有实际收益的 48 / 16 做新一轮验收。另一个 Reddit 100×3 replay 否决了 200+100 正文截断，故 single/batch eval 保留完整 `body_text`。`scripts/run_profile_diet_ab.py` v2 从只读真实 DB 精确抽取最近 `evaluated/cached/rejected_low_score` 生产混合候选，加载 overrides + active speculations 后冻结 effective profile / 负例 / 候选快照，交替运行至少 3 组 A/A 与 A/B。回放按生产 claim 以 30 条、`source_context=mixed` 评分，temperature 固定 0 以隔离采样噪声（artifact 同时披露生产默认 0.7），输出上限保持生产 4096；逐 run 校验实际 provider/instance/model，严格审计 embedding/recall，任一缺失响应、route / snapshot 漂移或基础设施降级都阻断最终门。明确的瞬时 provider rate limit 会按 65 / 130 / 260 / 520 秒对同一 chunk 最多重试四次，并先恢复候选评估字段；402/余额/计费错误不重试，恢复事件仍进入 route audit，schedule 写入 artifact。artifact schema v3 保留 raw paired scores、digests、usage 和全部 blocking reasons，不写正文、完整画像或原始内容/图片 ID。2026-07-18 旧 PASS 已作废，必须以当前 rebase 后脚本的独立 artifact 为准 |
| evaluator JSON minify replay | ❌ | `ad4ba670` 上的正式 100×3 回放已逐 wire attempt 计入 46 次 `json_object` 空内容 fallback，并恢复 3 次瞬时限流。B 的 prompt 字符/字节减少 `25.05% / 20.81%`，实际 attempt 配对中位 prompt/total token 节省 `13.57% / 11.29%`，三轮总计节省 `15.72% / 13.16%`；repair 与 topic/style/franchise 分类门通过。但 admission delta 中位 `-6pp` 低于 A/A 相对 floor `-4pp`，两个干净重复的 provider cache ratio 还下降 `32.20pp / 31.98pp`，完整 usage/cache 证据也被一次恢复限流阻断。因此不放宽门、不启用生产 compact JSON，保留 replay-only seam 与 artifact v3 诊断；artifact 不含 prompt、标题、正文、原始内容/图片 ID 或 secret。 |
| evaluator reason-off replay | ❌ | Replay-only `reason-off` 臂已在 `c6327506` 真实跑完 100 条 × 3 轮：B 完全省略成功评估结果的 `reason` 字段且生产默认始终未变。B 的 reason field count 为 0，但 prompt / completion / total token 相对当前 reason diet 分别增加 `30.89% / 38.88% / 31.70%`；首轮有对称的 provider error，排除该轮后两个零错误重复仍多耗 `7.18% / 25.97%` total token，且 B 比 A 需要更多/更大的 member-repair。Spearman 中位数 `0.709700`、flip 中位数 `5%`、`topic_group/style_key/franchise_key` 隐私安全 agreement/fill gate 均通过；准入差值中位数 `-5pp` 低于相对 floor `+2pp`，最终 gate 失败。因此完全关闭 reason 被否决，生产保留当前低分空串/高分 30 字的 reason diet；`evaluation_response_missing` 故障哨兵继续保留。下游 style/franchise cap-drop 未在该 replay 中复刻，artifact 明确标为未测。 |
| B 站 search 风控冷却 | ✅ | `BilibiliAPIClient.search()` 连续 `v_voucher` 重试耗尽或 412 后会设置共享 cooldown；Search / Explore / RelatedChain 的搜索路径在冷却期直接跳过，不再继续生成 query/domain 或逐 query 撞风控 |
| M126 explore 高风险子簇压缩 | ✅ | refresh 结束后会温和压一轮 `explore` 内部的高风险相邻簇，例如制造 / 工艺 / 材料、博弈 / 桌游 / 机制，避免单簇继续堆满 fresh pool |
| v0.3.0 trending 按 rid 交错 | ✅ | `TrendingStrategy` 拉 5 个分区排行榜后做 round-robin 交错再送 LLM 评估，避免下游 30 条 hard-cap 把 rid=0/36 的顶部全吃掉 |
| v0.3.0 explore 按 domain 交错 | ✅ | `ExploreStrategy` 同模式：按 `domain_label` round-robin 后再送评估 |
| v0.3.0 跨源跨轮 topic_group 配额 | ✅ | `Database.trim_topic_group_overflow(max_per_group)` 每 refresh tick 都跑，把任意 topic_group 在 fresh pool 占比压在 ~10%；不依赖 source，泛化了 explore-only 的 cluster cap |
| v0.3.0 deficit-source 合并并行 | ✅ | `_build_source_replenishment_plan` 把 B 站平台缺口合并到一次 `discover()` 并行 fan-out，单轮多策略混排，告别"每轮一种 source"的 60s 串行 |
| v0.3.0 share-aware trim_pool | ✅ | `trim_pool_to_target_count(source_share_quotas=...)` 用三段桶（protected / negotiable_untracked / negotiable_tracked），保证 under-quota 源不会被 score-only 修剪误伤 |
| v0.3.0 suppressed 重发现复活 | ✅ | `cache_content` UPSERT 时把 `pool_status='suppressed'` 自动复位为 `'fresh'`；slow-churning 源（trending）从此不再被旧 trim 决定终生淘汰 |
| v0.3.69 平台级来源配比 | ✅ | `_SOURCE_TARGET_SHARES` 硬编码策略配比改为配置项 `[scheduler.pool_source_shares]`；`source_policy` 会按已注册平台的 `sources.<platform>.enabled` 生成有效配比，避免关闭源占 quota；配置页可更新开关与比例。历史版本曾排除微博 init；当前微博由登录态 heartbeat + 同源只读任务导入个人事件 |
| Pool distribution snapshot | ✅ | `build_pool_distribution_snapshot()` 汇总候选池总量、平台缺口、饱和 topic/style/franchise，为后续 pool-aware discovery prompt 和 rerank 提供轻量输入 |
| Cold-start pool snapshot | ✅ | `build_cold_start_pool_snapshot()` 在 init 首轮空池和统一 keyword planner 空池时生成 synthetic hints：把画像最高权重兴趣作为 `avoid_topics` 软预算，把次级兴趣 / 兴趣域作为 `prefer_axes`，避免第一批 discovery query / 跨平台 keywords 全部集中在同一强 topic |
| v0.3.1 trim_topic_group 每 tick 触发 | ✅ | 修复"trim 只在 discover 之后跑"的盲点：`_enforce_pool_cap` 路径上每 tick 都调一次，避免 pool 满 cap 时 topic 配额永远不收敛 |
| v0.3.31 小红书来源族均衡 | ✅ | `xhs-extension-task/search/profile` 等 raw source 归并为 `xiaohongshu` 平台族参与配额，满池时会从 suppressed 高分小红书候选中复活 under-quota 库存，再按统一 cap trim 让出空间 |
| v0.3.67-0.3.69 抖音 discovery 策略边界 | ✅ | `DouyinDiscoveryService` 现在封装 search / hot / feed 三个公开来源的统一策略边界，Cookie 从环境变量覆盖或扩展同步文件解析；`discover --source douyin` 走缓存路径，`discover-douyin` 可指定关键词、子来源并用 `--no-cache --no-evaluate` 调试；作者主页 `creator` 不再作为默认公开渠道 |
| v0.3.68 抖音插件 search discovery | ✅ | `search-douyin` 入队 `dy_tasks(type="search")`；扩展后台 tab 从抖音首页出发，模拟真实 DOM 搜索框输入 / 点击搜索，并用 `search_navigation_ok` 校验 URL 已进入真实搜索结果路由，再被动收集页面响应和渲染 DOM 回传 `dy_search` 候选；fetch tap 覆盖 `/general/search/single/`、`/search/item/` 和 `/general/search/stream/` chunked JSON；正式 `search` 子来源复用这条链路，以 `dy-plugin-search` 进入 discovery，不传播为画像事件 |
| v0.3.68 抖音插件 hot-related discovery | ✅ | `hot` 子来源先取 hot board 的 `sentence_id` 和可用 `group_id`，`group_id` 作为 `seed_aweme_id` 透传给扩展；扩展后台 tab 从抖音首页出发点击热榜 / 热点入口与目标热词，并在 DOM / 被动监听不足时用 related API bridge 按 seed 拉相关视频，正式以 `dy-plugin-hot-related` 进入 discovery |
| v0.3.69 抖音插件首页 feed discovery | ✅ | `feed` 子来源入队 `dy_tasks(type="feed")`，扩展在后台登录首页滚动推荐流触发页面加载，并被动监听 feed 响应 / 解析 DOM 回传 `dy_feed` 候选，正式以 `dy-plugin-feed` 进入 discovery；CLI 公开来源收敛为 `search` / `hot` / `feed` |
| v0.3.69 抖音 runtime search 防重复 | ✅ | discovery engine 注册同名 strategy 时替换旧实例，避免 `douyin_direct` 在长期后台运行中累积成多个同名策略并重复创建 search 任务；扩展 search 任务单关键词 timeout 放宽到 180s，覆盖首页打开、DOM 触发、页面响应和 DOM 解析耗时 |
| v0.3.x discovery 画像上下文补齐 | ✅ | `build_profile_summary()` 会把 `disliked_topics`、认知风格、价值观、内在驱动力、当前阶段、life stage、MBTI、来源平台分布、近期觉察、当前洞察、质量敏感度和兴趣来源时间一起带入 discovery profile summary，让 search / trending / explore / YouTube query 生成和 batch 内容评估都能看到更完整的画像上下文 |
| v0.3.x 画像 / 评估输入上限放宽 | ✅ | 画像摘要扁平兴趣 tag 上限 10 → 30 → 64 → 256（一级域 8 → 128），且兴趣域 / 兴趣 tag 一律按 weight 降序排序后再截断（域 tag 先于 specifics 填充，保证每个高权重域至少有 tag 级曝光）；`disliked_topics` 8 → 16 → 64 → 128（与存储上限对齐，避雷项不再截断）；batch 评估 payload 的 `description` 截断 200 → 400 字符；负例锚定上限 8 → 16（见 soul 模块）。64 上限与计划中的 12h LLM 画像整理任务配套（整理卡 64 边界做去重合并） |
| v0.3.x 画像输出移除 UP 主维度 | ✅ | `build_profile_summary()` 不再输出 `favorite_up_users`，`build_search_queries_prompt` 同步删除配套的「favorite_up_users 仅供背景参考」规则——避免模型从创作者名反推内容兴趣。`RelatedChainStrategy` 仍直接读 `preferences.favorite_up_users[:1]` 作种子，`/api/profile-summary` 用户视图不受影响 |
| v0.3.123 统一 profile prompt 输入 + 移除人格素描 | ✅ | `build_profile_summary()` 成为各来源唯一的结构化画像输入：发现（search / trending / explore / 内容评估）与推荐（评估 / 文案 / 理由）共用同一份字段；不再输出 `personality_portrait` 那段总结性叙事（结构化字段已承载同样信号，且 prose 里的比喻会带偏 query / 文案生成）。人格素描仍照常生成并在画像页展示，只是不再进任何 LLM prompt。新增可选 `interests=` 形参，供推荐侧传入 embedding 选出的内容相关兴趣 |
| v0.3.123 X/小红书/抖音关键词生成并入统一画像 + 字段上限 30 | ✅ | X (`strategies/x.py`)、小红书 (`sources/xhs_keyword_gen.py`)、抖音 (`strategies/douyin_direct.py`) 的搜索关键词生成统一改为吃完整 `build_profile_summary`（与 B站 / YouTube 关键词生成一致、带 `disliked_topics` 避雷）。X / 小红书取消原 top-15 兴趣元组截断；抖音从确定性取兴趣名升级为 LLM 生成（即设计里 deferred 的 `dy_explore`），**无 llm_service / 调用失败 / 空返回**时回退确定性兴趣名、`seed_keywords` 仍最优先。各自保留平台风格静态 system prompt。**内容评估**环节所有平台共用 `build_profile_summary`。同时 `build_profile_summary` 中 `cognitive_style` / `values` / `motivational_drivers` / `deep_needs` 等原 `[:5]` → `[:30]`、`recent_awareness` / `active_insights` 窗口 `[-5:]` → `[-30:]`、每域 specifics（`_SPECIFICS_PER_DOMAIN`）`5` → `30` |
| profile-views Wave B：序列化器迁至 soul | ✅ | `build_profile_summary` / `compact_content_prompt_profile_summary` / `build_query_generation_profile_summary` 三个画像序列化器已从 `discovery/strategies/_utils.py` 原样迁入 `soul/profile_views.py`（机械搬家、零行为变化，字节对拍见 `tests/test_profile_views.py`）。`_utils` 现为**向后兼容 re-export 层**：所有历史 `from openbiliclaw.discovery.strategies._utils import ...` 路径（含 `_CONTENT_PROMPT_*` 常量、`normalize_match_text`、`cached_embedding_lookup` 依赖的 `_coerce_query_embedding_vector`）保持不断。新增携带画像的 prompt 应直接从 `soul/profile_views.py` import，不再经 `_utils` |
| X (Twitter) 服务端 discovery | ✅ | 第六个内容源 `source_platform="twitter"`（标签 `"X"`）。`XAdapter` 服务端 cookie 重放（`XClient` 封装默认运行时依赖 `twitter-cli`，lazy import + 只读），分发 `search`（画像关键词）/ `feed`（For-You）/ `creator`（账号订阅）三策略，经 `x_normalize.normalize_tweet()` 转 `DiscoveredContent`（`content_type ∈ {tweet, thread}` + `body_text` 全文）入统一候选池；后台由 `XDiscoveryProducer` 按预算 + 源健康调度 |
| 知乎插件 discovery | ✅ | 第七个内容源 `source_platform="zhihu"`（标签「知乎」）。`ZhihuDiscoveryProducer` 通过 `zhihu_tasks` 唤醒扩展在已登录知乎页面拉取搜索、热榜、首页推荐、作者页和相关内容，`zhihu_discovery_items_to_contents()` 将 answer / article / question 映射为文字候选（`content_type` 透传、`content_id` 带类型前缀防止数字碰撞、互动指标随候选进入 evaluator），再以 `zhihu-search` / `zhihu-hot` / `zhihu-feed` / `zhihu-creator` / `zhihu-related` 写入统一待评估池；creator / related 冷启动时可用同轮候选里的作者页和内容 URL 作种子；`discover-zhihu*` 命令可作为真实插件 E2E smoke |
| Bangumi 官方 API discovery | ✅ | 第八个正式平台族 `source_platform="bangumi"`（`bgm` alias）。`BangumiDiscoveryProducer` 以官方匿名 API 执行 `search / ranked / latest`，共享关键词 planner 和待评估池；browse 类型起点跨轮持久化，单轮小 limit 不会饿死后置类型，搜索中途限流会保留已完成关键词的候选。条目评分、评分人数、排名使用独立通用字段，不冒充 like/comment。公开用户名可选用于 guided init，首版无 OAuth/Cookie/站内写操作 |
| Linux.do 扩展同源 discovery | ✅（真实 Chrome E2E） | 第九个平台族 `source_platform="linuxdo"`（`linux.do` / `l站` alias）。`LinuxdoDiscoveryProducer` 通过扩展只读执行 search / hot / feed / creator / related，canonical topic identity 为 `topic:<id>`；`_t` 仅布尔、Cookie/原始响应不上传，五分支共享关键词 planner、候选池与 evaluator。2026-08-09 已验证 search/hot/feed/creator 返回 topic、related 合法 empty、正式候选评估与入池 |
| Bangumi 官方 API discovery | ✅ | Bangumi 平台族 `source_platform="bangumi"`（`bgm` alias）。`BangumiDiscoveryProducer` 以官方匿名 API 执行 `search / ranked / latest`，共享关键词 planner 和待评估池；browse 类型起点跨轮持久化，单轮小 limit 不会饿死后置类型，搜索中途限流会保留已完成关键词的候选。条目评分、评分人数、排名使用独立通用字段，不冒充 like/comment。公开用户名可选用于 guided init，首版无 OAuth/Cookie/站内写操作 |
| V2EX 官方 API / Feed discovery | ✅ | 第九个正式平台族 `source_platform="v2ex"`。`V2EXDiscoveryProducer` 执行 `search / node / tab / hot / latest`，共享关键词 planner、Node/Tab 配置、分支预算和 rate-limit cooldown；PAT 只增强 API 2.0，401/403 自动降级匿名。Topic 归一为 `content_type="topic"` 文字卡，Reply 不独立入池；扩展四个只读 bootstrap scope 已进入 guided init，Reply 按 Topic 聚合，Node affinity 可作为 Node 召回 fallback |
| X 文字候选 body_text / content_type | ✅ | `DiscoveredContent` 增设 `body_text`（推文 / `note_tweet` 长文全文）+ `content_type`（`video`/`note`/`tweet`/`thread`，复用候选池既有 shape 字段，不新造 `media_type`）；两处 `content_type` 硬编码（`candidate_pool` write + 引擎候选 dict）改为优先取 `item.content_type`，全链路（enqueue → claim → admission → cache → API）透传，保证文字 / thread 候选正确流过 pending 评估；候选池 source key 同步把 `x` / `twitter` 归一为 `twitter`，避免 X 文字内容在 quota / pool 状态里分裂 |
| SearchStrategy LLM 评估 | ✅ | `SearchStrategy` 现在默认走 `evaluate_content()` LLM 打分（`llm_evaluation=True`），不再只用本地启发式（上限 0.62），可通过 `llm_evaluation=False` 关闭 |
| 策略中间产物捕获 | ✅ | 4 个策略均支持 `last_intermediates` 属性，运行后可查看生成的搜索词、选择的分区、种子列表、探索域等中间产物 |
| Discovery 评估框架 | ✅ | `DiscoveryEvaluator` 支持 7 维质量评估（relevance / diversity / specificity / query_quality / explanation_quality / novelty / no_echo_chamber），含自动和人工两种模式 |
| Discovery 模拟场景 | ✅ | `ScenarioGenerator` + `MockBilibiliClient` + `MockMemoryManager` 可离线生成模拟 B 站内容宇宙用于评估，无需真实 API |
| Discovery 评估类型边界 | ✅ | v0.3.71 起 eval scenario / evaluator 对 LLM JSON、缓存 persona、人工反馈和 ranking pool 做显式类型守卫，`mypy strict` 可覆盖评估链路而不依赖真实 Claude / Playwright / aiohttp 安装 |
| Discovery 自动优化循环 | ✅ | SGD 风格优化循环：生成 persona → 生成 scenario → 运行发现 → 多维评估 → exploit/explore → accept/rollback |
| Discovery 人工评估脚本 | ✅ | 交互式人工评估 + 可选触发优化 |
| P1 统一关键词 planner / 背压（v0.3.124 起默认开） | ✅ | `[discovery].unified_keyword_planner_enabled`（v0.3.124 起默认 `true`）后面的双缓冲 + 缺口拉动背压：`discovery_keywords` 存储（pending→claimed→used/failed/executing 状态机 + 部分唯一 + 租约回收 + CAS 单飞锁）+ `KeywordPlanner`（一次合并 LLM 调用、画像发一份、按平台分块、digest 失效、稀疏回收、同画像/同池子需求块按 `plan_ttl_hours` 复用生成结果）+ `KeywordFetchCoordinator`（缺口驱动 claim + 三执行形态：内联 admit / fetch-only 交 pipeline / 异步插件任务）+ `source_keyword_id` 幂等 yield 回填 + 0 产出退役。接管 B站 / 小红书 / 抖音 / YouTube / X / 知乎 / Reddit / Bangumi / Linux.do 九个平台 search 关键词，`trending/explore/related/hot/feed/subreddit/ranked/latest` 不动；flag-off 逐字回退旧逐平台生成。flag-on 时 B 站主 refresh 若暂时 claim 不到关键词，会跳过本轮 `search` 子策略而不是回落到旧 `discovery.search.queries` caller。成本记单一 caller `discovery.keyword_planner`，per-platform 靠 planner 每轮 `cycle ledger`（`{platform: {generated, yield}}`）观测。E2E：`tests/test_keyword_backpressure_e2e.py` |
| P1 统一关键词 planner / 背压（v0.3.124 起默认开） | ✅ | `[discovery].unified_keyword_planner_enabled`（v0.3.124 起默认 `true`）后面的双缓冲 + 缺口拉动背压：`discovery_keywords` 存储（pending→claimed→used/failed/executing 状态机 + 部分唯一 + 租约回收 + CAS 单飞锁）+ `KeywordPlanner`（一次合并 LLM 调用、画像发一份、按平台分块、digest 失效、稀疏回收、同画像/同池子需求块按 `plan_ttl_hours` 复用生成结果）+ `KeywordFetchCoordinator`（缺口驱动 claim + 三执行形态：内联 admit / fetch-only 交 pipeline / 异步插件任务）+ `source_keyword_id` 幂等 yield 回填 + 0 产出退役。接管 B站 / 小红书 / 抖音 / YouTube / X / 知乎 / Reddit / Bangumi / V2EX 九个平台 search 关键词，`trending/explore/related/hot/feed/subreddit/ranked/latest` 不动；flag-off 逐字回退旧逐平台生成。flag-on 时 B 站主 refresh 若暂时 claim 不到关键词，会跳过本轮 `search` 子策略而不是回落到旧 `discovery.search.queries` caller。成本记单一 caller `discovery.keyword_planner`，per-platform 靠 planner 每轮 `cycle ledger`（`{platform: {generated, yield}`）观测。V2EX node/tab/hot/latest 不需要额外 LLM 关键词。E2E：`tests/test_keyword_backpressure_e2e.py` |
| QualityGate 主动质量过滤器（PR #52） | ✅ | `ContentDiscoveryEngine` 新增 `bilibili_client` / `quality_gate` 注入；`discover()` 在 `_cache_results` 前调用 `quality_gate_check_items()`：`reject` 模式硬拦截（不进候选池），`penalize` 模式把 `candidate_tier` 降为 `backfill`。`QualityGateChecker` 支持标题 Clickbait 正则、播放量下限、UP 主粉丝数 / 等级 / 企业认证（`get_up_card`），并支持 `allowlist_mids` 白名单。仅作用于 Bilibili 内容。`DiscoveredContent` 新增 `up_level` / `follower_count` 字段。 |

### Visual enrichment API

`fetch_keyframes()` 返回 `KeyframeFetchResult`，其 `status` 明确区分成功、确认无数据和瞬时
失败；`keyframe_sampling_signature(max_frames)` 将算法版本与帧数写进 cache provenance。
推荐预热只在安全成功条件下写完成状态，便于下一轮重试失败的 source / sprite / embedding。

## 公开 API

### Temporal v2 证据与三态策略

```python
from datetime import UTC, datetime

from openbiliclaw.discovery.temporal import (
    evaluate_temporal_eligibility,
    ground_temporal_evaluation,
    parse_temporal_evaluation,
    schedule_temporal_evaluation,
)

temporal = parse_temporal_evaluation(model_payload)
temporal = ground_temporal_evaluation(temporal, content_text=prompt_visible_content)
temporal = schedule_temporal_evaluation(
    temporal,
    evaluated_at="2026-08-13T00:00:00Z",
)
decision = evaluate_temporal_eligibility(
    temporal_class=temporal.temporal_class,
    temporal_confidence=temporal.temporal_confidence,
    published_at=content.published_at,
    temporal_validity_mode=temporal.temporal_validity_mode,
    temporal_valid_until=temporal.temporal_valid_until,
    temporal_scope=temporal.temporal_scope,
    temporal_evidence=temporal.temporal_evidence,
    temporal_state=temporal.temporal_state,
    temporal_next_review_at=temporal.temporal_next_review_at,
    temporal_evaluated_at=temporal.temporal_evaluated_at,
    temporal_policy_version=temporal.temporal_policy_version,
    evidence_complete=temporal.evidence_complete,
    now=datetime.now(UTC),
)
```

- `parse_temporal_evaluation()` 把八个模型字段作为一个原子契约校验；缺字段、非法枚举或不一致 mode/state 会整体降成中性值，且不会读取模型提供的 policy / 时钟。
- `ground_temporal_evaluation()` 要求 deadline / event / version evidence 经 NFKC、大小写和空白归一后仍是 Agent 实际看到的 prompt projection（包括 batch 的 400 字 description 上限）中的逐字子串；deadline 必须同时写出日期、具体时刻和时区，并与 `valid_until` 表示同一瞬间，日期-only、无时区或时刻不一致都不能 hard expire。事件 / 版本终态还必须有明确的“已结束 / 已替代”正向语义；“尚未结束 / 仍受支持”等反向证据会降成 `freshness_only + state=unknown`。
- `schedule_temporal_evaluation()` 写入 code-owned `evaluated_at/next_review_at`：deadline 使用明确截止点，其余 `breaking/current/versioned` 使用 1 / 14 / 120 天复审节奏；hook、常青、历史、未知和已终态内容不生成周期复审时钟。
- `evaluate_temporal_eligibility()` 是 admission、storage 与 final serve 共用的纯函数，返回 `TemporalEligibilityDecision(disposition="eligible|review_due|expired")`；旧 v1 调用仍兼容，但 3 / 60 天只返回 `review_due`。`temporal_bonus_component()` 保持独立，只计算 publication ranking bonus。

### build_profile_summary

```python
from openbiliclaw.discovery.strategies._utils import build_profile_summary

profile_summary = build_profile_summary(profile)
```

行为说明：

- 这是 discovery 各策略共享的画像摘要入口，用来把 `SoulProfile` / `OnionProfile` 压成可序列化、可注入 prompt 的 dict。
- 摘要会保留一级兴趣 `interest_domains`（前 128 个域，每域最多 5 个 specifics）和扁平兴趣 `interests`（最多 256 条），并带上 `first_seen` / `last_seen` / `source`，让搜索词生成和内容评估能区分长期稳定兴趣、近期新增兴趣和推断来源。两个列表都按 weight 降序排序后再截断，强兴趣不会被列表顺序挤掉；扁平 tag 先填所有域 tag，剩余名额按 specifics **自身权重全局排序**填充（不设每域配额——此前伞形大域 200+ specifics 只露 top-5，0.8 权重的细分兴趣反而不可见），域级多样性由域 tag 和 `interest_domains` 区保证。
- 摘要会带入 `disliked_topics[:128]`（与 `_DISLIKED_TOPICS_STORE_CAP` 对齐，存储的避雷项全部可见）；这些是长期避雷项，和 batch evaluator 的短期 `negative_examples` 互补。
- 摘要会带入人格与决策上下文：`core_traits`、`cognitive_style`、`values`、`motivational_drivers`、`deep_needs`、`current_phase`、`life_stage`、`mbti`、`recent_awareness`、`active_insights`（觉察 / 洞察窗口按时间旧→新存储，摘要取**最新** 5 条——v0.3.121 及之前误取最旧 5 条）。
- 摘要会带入消费上下文：`style`（含 `quality_sensitivity`）、`context`、`exploration_openness`、`source_platform_mix` 和 `_active_speculations`。
- 摘要**不再带入** `favorite_up_users`：「常看某创作者」≠「对该创作者内容类型感兴趣」，它只会诱导模型从创作者名反推兴趣方向。用户的 UP 主清单仍保留在 `/api/profile-summary`（用户自己的可视 / 可编辑视图）并直接给 `RelatedChainStrategy` 当种子，只是不进 LLM 画像输出。
- 摘要是 discovery 的只读输入，不会修改 profile；字段数量按 prompt 需要裁剪到前若干项，避免把整份画像无界塞进 LLM。

### materialize_platform_keywords

```python
from openbiliclaw.discovery.inspiration import (
    AllocationTarget,
    MaterializeCandidate,
    materialize_platform_keywords,
)

keywords, telemetry = materialize_platform_keywords(
    [
        MaterializeCandidate(
            interest="game design",
            axis_label="mechanics",
            platform="youtube",
            core_concept="game design combat tuning",
            decoration="designer interview",
            recency_sensitivity="high",
            origin="llm",
        )
    ],
    {"game design": AllocationTarget(platforms=("youtube",), min_axes=1)},
)
```

行为说明：

- 这是 inspiration 轴化重构的纯装配函数，不调用 LLM、不写数据库。
- 硬门只包含 dedup、URL、长度和平台脚本兼容；平台检索语法由 `platform_style_score()` 作为软排序信号，不再硬拒绝候选。
- 分配按 interest × axis × platform 覆盖优先，同槽位内再按 **`is_specific` 具体性优先、`platform_style_score()` 软分次之**排序（Phase 2.1 F1.5：具体锚点候选压过复述话题名的泛化候选）；候选不足时可用 axis `example_terms` 做 `origin="deterministic_fill"` 的确定性补位（`is_specific=False`）。
- 无法补位的槽位会在 telemetry 中返回 `coverage_shortfall`，脚本不匹配会记录 `reason="script_mismatch"`，不会硬塞跨脚本垃圾词。
- 每个 `RealizedKeyword.metadata` 携带 `source_interest` / `source_domain` / `axis_label` / `axis_id` / `origin` / `recency_sensitivity` / `platform_style_score` / `normalized_keyword`，以及 Phase 2.1 新增的观测键 `core_concept` / `decoration`（来自对应 `MaterializeCandidate`，只观测、不改 `keyword` 文本）；`is_specific()` 与 `restatement_rate()` 是配套的确定性纯函数（可无 LLM 单测）。

### ContentDiscoveryEngine

```python
from openbiliclaw.discovery.engine import ContentDiscoveryEngine
from openbiliclaw.discovery.strategies.strategies import SearchStrategy

engine = ContentDiscoveryEngine(
    database=db,
    target_primary_count=12,
    backfill_target_count=18,
)
engine.register_strategy(
    SearchStrategy(
        llm_service=service,
        bilibili_client=bilibili_client,
        database=db,
    )
)

results = await engine.discover(profile)
assert results[0].source_strategy == "search"

score = await engine.evaluate_content(results[0], profile)
assert 0.0 <= score <= 1.0
```

行为说明：

- `discover()` 现在会并发执行多个已注册 strategy
- `produce_candidates()` 使用同一套策略并发 / 去重逻辑，但会临时关闭支持该开关的策略内 LLM evaluation，用于 runtime 先拉原始候选再入 `discovery_candidates`
- `cache_evaluated_results()` 暴露 `_cache_results()` 的受控入口，供 `DiscoveryCandidatePipeline` 把已经统一评估过的候选写入正式推荐池
- discovery 的受控并发 controller 会按当前 `asyncio` event loop 重新创建内部 semaphore，适配 CLI 里多次 `asyncio.run(...)` 的分阶段调用
- `discover(..., strategy_limits={...})` 可让调用方限制每个 strategy 的单独拉取量；最终 `limit` 仍控制合并后的返回 / 缓存数量，`strategy_limits` 只负责避免 grouped refresh 把同一个平台缺口放大到每个策略
- `discover(..., pool_snapshot=...)` 可接收可选的 `PoolDistributionSnapshot`；引擎只会把它传给签名兼容的 primary strategy 和 backfill strategy，保留旧版 `discover(profile, limit=...)` 签名不变。
- 同一 `bvid` 若被多个策略命中，保留 `relevance_score` 更高的版本
- 主候选少于目标数量时，会依次尝试策略 backfill 和历史缓存 backfill；策略 backfill 同样会收到兼容转发的 `pool_snapshot`
- 当调用方只需要少量候选时，策略会先把送入 LLM 评估的候选窗口压到 `max(6, limit*2)`，仍保留过采样缓冲，但不再用固定 90 条窗口浪费评估调用
- batch 评估结果解析会优先选择包含 `score` 的结果数组或 object 序列；如果 provider 回显输入 JSON、包 Markdown fence、或返回 NDJSON，仍按一次 batch 处理，不会因为包裹格式异常直接拆成 N 次单条评估
- batch prompt 和响应都带 `bvid/content_id`；只要响应里有可识别 ID，引擎会按 ID 而不是数组下标写回评分和理由。缺失 / malformed member 只重试缺失 subset；全组缺失才二分，最多深度 3 / 额外 6 请求，绝不退化成 N 次单条风暴
- batch provider 调用异常不会返回全 0 分或触发逐条 fallback；异常向上传递给 `DiscoveryCandidatePipeline`，由 pipeline 释放本批 claim 回 `pending_eval`，下一轮在 provider 恢复后继续评估原候选
- `eval_prefilter_mode="shadow"` 会把每条决策写入有界的 `evaluator_prefilter_shadow_audit`，并在 batch diversity cap 之前回填原始 LLM score；持久字段只有 candidate hash、平台/上下文类别、相似度/阈值、explore/保护标志、embedding/profile digest 和 admission 结果。审计或 embedding 异常只会让候选继续走 LLM，并使 gate 保持关闭；代码不会把通过的报告自动转成 `enforce` 配置。
- `SearchStrategy` / `TrendingStrategy` / `RelatedChainStrategy` / `ExploreStrategy`、YouTube 三策略和 `DouyinDirectStrategy` 在内部临时构造 evaluator 时都会透传 `database`。因此 CLI、daemon runtime、YouTube producer、Douyin producer 和 OpenClaw bootstrap 路径都能读取同一份近期 negative exemplars，避免只有外层 engine 能看到短期负反馈样本。
- 排序口径优先 `candidate_tier`，再看 `relevance_score`、`last_scored_at`、`view_count`
- 最终结果会把 `relevance_score`、`relevance_reason`、`candidate_tier` 一并写入 `content_cache`

### DiscoveryCandidatePipeline

```python
from openbiliclaw.discovery.candidate_pipeline import DiscoveryCandidatePipeline

pipeline = DiscoveryCandidatePipeline(
    database=db,
    discovery_engine=engine,
    pool_target_count=300,
)

produced = await pipeline.produce_and_enqueue(
    profile=profile,
    strategies=["search", "trending", "related_chain", "explore"],
    limit=30,
)
supply = await pipeline.ensure_pending_supply(
    profile=profile,
    strategies=["search", "trending", "related_chain", "explore"],
    limit=30,
    target_pending=30,
)
drained = await pipeline.drain_pending(profile=profile, batch_size=45)
```

行为说明：

- `enqueue_candidates()` 把任意来源的 `DiscoveredContent` 规范化为 `DiscoveryCandidateWrite`，并在入库前过滤同批重复、历史 `discovery_candidates` 和已经进入 `content_cache` 的内容，再通过 `Database.enqueue_discovery_candidates()` 写入 `discovery_candidates`。
- `produce_and_enqueue()` 负责单次 raw 生产：用 `ContentDiscoveryEngine.produce_candidates()` 拉 raw candidates，再入待评估池。
- `ensure_pending_supply()` 是 runtime 主补货路径：按 `pending_eval + evaluating` 水位循环生产 raw candidates，直到达到目标 batch、池子已满、没有新候选或达到尝试 / 时间预算；目标 batch 会受 `[scheduler].eval_min_batch_size`（默认 15）下限约束，它看实际 inserted 数，不再把 raw fetched 数当成 Evo 可评估供给。
- `drain_pending()` 是统一 evaluator：从 `pending_eval` mixed-source batch claim，文本 batch 默认 45，并默认最多领取两个 `batch_size` 的候选交给 `evaluate_content_batch()` 以 2 个 worker 并发处理；完成 topic normalization 后将低分、重复、cache admission fallback、franchise quota 和已缓存候选写回不同 lifecycle status；batch 级 transient 或 runtime hot-reload / shutdown cancellation 会释放 claim 回 `pending_eval` 并递增高阈值 `batch_eval_attempts`，避免候选长期卡在 `evaluating`。
- `drain_pending()` 会读取 evaluator 的 `_EVALUATE_BATCH_HARD_CAP` 并 clamp claim size，避免配置把 batch_size 调到 evaluator hard-cap 之上时，尾部候选被当作 0 分低相关永久拒绝。
- API runtime 构造 pipeline 时会读取配置的 `eval_min_batch_size`、`eval_max_wait_seconds`，并启用 `candidate_fetch_oversample=4`；少于最小批量的 `pending_eval` 会先等待，超时才跑小 batch，协调器按 `eval_ready_in_seconds()` 返回的剩余时间唤醒。即使 refresh 缺口算法给出的 `batch_size` 小于最小批量，pipeline 也会把 claim size 抬到该下限（仍受 evaluator hard cap 约束）。CLI 手动 producer pipeline 固定 `min=1 / wait=0` 立即 drain，因为一次性进程不能可靠承载跨命令的内存计时。主 B 站 refresh 会先通过 `ensure_pending_supply()` 把待评估队列补到有效水位，再由 evaluator 消费，降低重复 discovery 让 evaluator 只拿到 1-3 条的概率。
- `drain_pending()` 自带共享 async lock；`ContinuousRefreshController.drain_discovery_candidates_once()` 与周期 `_loop_candidate_eval()` 也会在 controller 层串行化外部触发。所有入口都会先检查 `count_pool_candidates() >= pool_target_count`；正式可换推荐池满时不再评估 / 入池。周期 loop 在 admission 后会触发 `precompute_pool_copy()`，把刚入 `content_cache` 但缺文案的候选整理成可换库存。
- 普通入池兜底阈值是 `[discovery].admission_min_score=0.60`；候选行可携带更严格的 strategy 阈值，explore 默认 `0.58`（backfill 最低 `0.55`）作为探索鼓励，普通 backfill 最低不低于 `0.60`。来源 / 平台标签不参与降阈值。

更直白地说，`ContentDiscoveryEngine` 负责最后的“收口”：

- 策略关心“我能找到什么”
- 引擎关心“这些结果如何合并成一个可消费的候选池”

因此真正影响推荐体验稳定性的，往往不是单个策略够不够聪明，而是引擎层的并发、去重、压缩和补货逻辑是否可靠。

### DouyinDiscoveryService

```python
from openbiliclaw.discovery.douyin import (
    DouyinDiscoveryOptions,
    DouyinDiscoveryService,
)
from openbiliclaw.sources.douyin_direct import DouyinDirectClient
from openbiliclaw.sources.douyin_plugin_search import DouyinPluginSearchClient

async with DouyinDirectClient(cookie=cookie) as direct_client:
    client = DouyinPluginSearchClient(
        database=database,
        direct_client=direct_client,
    )
    service = DouyinDiscoveryService(
        client=client,
        discovery_engine=engine,  # 直接 discover 调用时可复用旧缓存收口路径
        database=database,        # 可选；未传时会从 discovery_engine._database 兜底
    )
    result = await service.discover(
        profile,
        DouyinDiscoveryOptions(
            sources=("search", "hot", "feed"),
            keywords=("机械键盘",),
            limit=20,
            cache=True,
        ),
    )

assert result.cached is True
assert result.source_counts.get("dy-plugin-search", 0) >= 0
assert result.source_counts.get("dy-plugin-hot-related", 0) >= 0
assert result.source_counts.get("dy-plugin-feed", 0) >= 0
```

行为说明：

- `cache=True` 且传入 `discovery_engine` 时，服务会注册 `DouyinDirectStrategy`，再通过 `ContentDiscoveryEngine.discover(..., strategies=["douyin_direct"])` 走直接评估、压缩和缓存写入。注册按 strategy name 替换旧实例，避免同一个关键词重复入队成多个 search 任务。
- daemon runtime 注入 `DiscoveryCandidatePipeline` 后会改用 `cache=False, evaluate=False` 拉抖音 raw candidates，再统一写入 `discovery_candidates` 并由共享 evaluator 入池；这条路径不会让抖音自己先写 `content_cache`。
- `cache=False` 时服务会直接执行 `DouyinDirectStrategy.discover()`，适合 CLI smoke、源接口排查和未来 API 预览，不会写入 `content_cache`。
- `sources` 公开支持 `search`、`hot`、`feed`；CLI 中 `search` 会走后台插件 DOM-first 链路并标记为 `dy-plugin-search`，`hot` 会走后台插件 hot-related DOM-first 链路并标记为 `dy-plugin-hot-related`，`feed` 会走后台首页推荐流 DOM-first 链路并标记为 `dy-plugin-feed`。dispatcher 给三类 discovery 任务打开的页面固定为抖音首页；content script 通过真实 DOM 操作触发页面加载，再被动收集页面自身 fetch/XHR 响应和 DOM 卡片，feed 覆盖真实页面使用的 `/aweme/v2/web/module/feed/`，search 覆盖新版 `/aweme/v1/web/general/search/stream/` chunked JSON。hot 插件任务会带总目标数和可选 `seed_aweme_id`，dispatcher 优先执行带 seed 的 hot item，累计达到目标后直接 finalise；小批量请求会展开一个小窗口，避免榜首没有 `group_id` 时完全无 seed。插件任务的空结果、超时、失败和预算耗尽会保持不同终态，不再都伪装成 0 条；只有显式传 `allow_direct_fallback=True` 的诊断客户端才会启用 direct-cookie fallback。插件 discovery 入队前会清理超过等待窗口的 search / hot / feed pending 任务，避免 daemon 重启或旧版本重复入队后，新任务被陈旧队列阻塞；这些清理出来的 `failed/stale_pending` 不计入每日任务预算。
- `dy_tasks` 领取在 SQLite `BEGIN IMMEDIATE` 内保持 source-wide single-flight：任一 15 分钟租约内的 `in_progress` 存在时，`/api/sources/dy/next-task` 不再发第二条 pending；过期任务仍优先被重领。该边界补足不同 unpacked 扩展 ID / Chrome profile 之间无法共享 service-worker mutex 的缺口。
- runtime `DouyinDiscoveryProducer` 每轮把 `keywords_per_run` 收窄到 1，并按当前抖音缺口动态选子来源：缺口很小时只跑 feed，较小缺口优先 hot 再 feed，缺口较大固定保留 search，并在 hot / feed 间逐轮轮换，避免 feed 在长期大缺口下永远饥饿。统一关键词池暂时为空时，search 回退画像兴趣词，hot / feed 仍独立执行；单个 search 的预算或插件故障也不会阻断另外两条分支。runtime 构造插件客户端时还会按本轮抖音缺口动态抬高 hot 任务预算（最多 60）；CLI smoke / 手动 discovery 仍可通过 `sources` / `keywords` 显式控制搜索面。扩展侧单关键词 search timeout 和后端默认等待窗口均为 180 秒，给首页打开、DOM 交互、页面自身响应和 DOM 解析留足窗口；搜索 UI 若触发整页导航，dispatcher 会在新 document 恢复采集，而不是让旧 content promise 静默等到超时。
- `DouyinDirectClient.get_hot_terms()` 会从 hot board 抽取 `sentence_id`，并保留可用 `group_id` / `seed_aweme_id` 给插件 hot 任务使用；`get_hot_board()` 只作为显式 direct-cookie 诊断 fallback，只有响应内直接携带 aweme 时才会产出视频。
- CLI 创建 `DouyinDirectClient` 前会先读 `OPENBILICLAW_DOUYIN_COOKIE`（或 `cookie_env` 指向的变量），再回退到扩展同步的 `data/douyin_cookie.json`；后者由 `/api/sources/dy/cookie` 写入，不镜像到 `config.toml`。
- `DouyinDirectClient` 对单次 HTTP 连接异常采用软失败：记录日志并返回空结果，让 CLI 输出本轮 0 条而不是 traceback；Cookie 或接口有效性仍以 smoke 结果为准。

### PoolDistributionSnapshot

```python
from openbiliclaw.discovery.pool_snapshot import (
    PoolDistributionSnapshot,
    build_cold_start_pool_snapshot,
    build_pool_distribution_snapshot,
)

snapshot = build_pool_distribution_snapshot(
    database,
    pool_target_count=300,
    source_targets={"bilibili": 240, "xiaohongshu": 30, "douyin": 30},
)
hints = snapshot.to_prompt_hints()

cold_start_snapshot = build_cold_start_pool_snapshot(
    profile,
    pool_target_count=30,
    source_targets={"bilibili": 30},
)
```

行为说明：

- `PoolDistributionSnapshot` 是冻结 dataclass，记录 `pool_target_count`、`pool_available_count`、各平台族目标数量 / 当前数量 / 缺口、`cold_start` 标记，以及已饱和的 `topic_group`、`style_key`、`franchise_key`；其中 `pool_available_count` 使用 recommendation serve 同口径的默认每 `topic_group` 最多 3 条候选窗口。
- 默认饱和阈值按池目标数换算：topic 为 `max(8, pool_target_count // 20)`，style 为 `max(12, pool_target_count // 8)`，franchise 固定为 10；以默认 `pool_target_count=300` 为例，topic 15 条、style 37 条、franchise 10 条即进入软避让。
- `source_deficits` 只表示平台 / 来源族缺口，例如 `bilibili`、`xiaohongshu`、`douyin`、`youtube`、`twitter`、`zhihu` 距离目标配比还差多少；它和内容轴分开处理，不会被解释成“应该搜索某个平台名”。
- `to_prompt_hints()` 输出面向后续 prompt 的轻量 dict：`cold_start`、`avoid_topics`、`avoid_styles`、`avoid_franchises`、`prefer_axes` 和 `source_deficits`。其中 `avoid_styles` 会把旧缓存 key 合并到新观看模式（如 `deep_dive` → `deep_focus`），`avoid_*`、`prefer_axes` 都是软信号，只影响 query 生成和引擎层软重排，不是硬过滤条件。
- `build_cold_start_pool_snapshot()` 用于没有真实池子分布可参考的 init 首轮和统一 keyword planner 空池首批关键词。它会读取 `build_profile_summary(profile)`，把权重最高且 `weight>=0.88` 的兴趣（最多 2 个；否则取 top 1）放入 `saturated_topics` / `avoid_topics`，把剩余兴趣名和一级兴趣域放入 `undercovered_axes` / `prefer_axes`，并标记 `cold_start=true`。这里的 `avoid_topics` 不是用户避雷项，只是“不要让首批 query / keywords 被强兴趣占满”的预算约束。
- 当前 runtime 构建的 snapshot 不会把平台缺口自动合成内容 `prefer_axes`；`undercovered_axes` / `prefer_axes` 保留给手动传入或未来更细的内容轴缺口判断。
- 统计口径复用候选池可见性：只看 fresh、非 dislike、未推荐、已预生成 pool copy 且可打开的候选；`pool_available_count` 额外复用 serve 候选窗口，避免拥挤主题把补货状态误判为可换库存充足。
- runtime refresh 会在每次 B 站 discovery 前构建 snapshot，并通过 `ContentDiscoveryEngine.discover(..., pool_snapshot=...)` 传入；init 空池首轮和统一 keyword planner 空池时使用 cold-start snapshot，池子已有内容后改用真实 pool snapshot；构建失败只记录日志，不阻塞补货。
- 引擎层会在最终压缩前应用 snapshot 软重排：饱和 topic/style/franchise 分别轻微降权，显式 undercovered topic 轻微加权，强相关候选仍保留优先级，且调整分只用于本轮排序，不会持久化覆盖 `relevance_score`。

### Runtime pool source balance

```python
source_targets = controller._source_target_counts()
raw_source_targets = controller._raw_source_target_counts()
# 默认有效 [scheduler.pool_source_shares] = 5 且 pool_target=600 时：
# {
#     "bilibili": 600,
# }
# raw_source_targets 会使用 raw ceiling=max(target*2, target+120)，
# 即默认 B 站 raw ceiling quota = 1200。
# 如果显式启用 XHS / Douyin / YouTube / X / Zhihu，对应平台会按保存的 share 获得
# 独立 target，并由各自 producer 或 strategy 补池。

database.reactivate_under_quota_pool_sources(
    target=600,
    source_share_quotas=source_targets,
    raw_source_share_quotas=raw_source_targets,
)
database.trim_pool_source_overflow(
    source_share_quotas=raw_source_targets,
)
database.trim_pool_to_target_count(
    target=controller._raw_material_ceiling(),
    source_share_quotas=raw_source_targets,
)
distribution_counts = database.get_pool_distribution_counts()
```

行为说明：

- 配额单位是“平台族”，不是 raw `content_cache.source`。B 站的 `search` / `related_chain` / `trending` / `explore` 统一计入 `bilibili`；小红书的 `xhs-extension-*` 统一计入 `xiaohongshu`；抖音的 `dy-plugin-*` / `douyin*` 统一计入 `douyin`；知乎的 `zhihu-search` / `zhihu-hot` / `zhihu-feed` / `zhihu-creator` / `zhihu-related` 统一计入 `zhihu`；Reddit 的 `reddit-search` / `reddit-hot` / `reddit-subreddit` / `reddit-related` 统一计入 `reddit`；Bangumi 的 `bangumi-search` / `bangumi-ranked` / `bangumi-latest` 统一计入 `bangumi`；Linux.do 的五个 `linuxdo-*` strategy 统一计入 `linuxdo`。
- B 站缺口仍由 `ContentDiscoveryEngine.discover()` 的四个策略补齐；小红书缺口由 `XhsTaskProducer` / 浏览器插件任务链补齐；抖音缺口由 runtime `DouyinDiscoveryProducer` 调用 `DouyinDiscoveryService(cache=False, evaluate=False)` 拉 raw candidates，小缺口用 feed / hot 快速补零散名额，大缺口用 search + hot/feed 轮换并统一写入待评估池；YouTube 与 X 由各自后台 producer 补齐；知乎缺口由 runtime `ZhihuDiscoveryProducer` 按 `source_modes` 入队插件 search / hot / feed / creator / related 任务补齐；Reddit 缺口由 runtime `RedditDiscoveryProducer` 默认调用 rdt-cli，命令后端不可用时入队插件 fallback；Bangumi 缺口由 `BangumiDiscoveryProducer` 以官方匿名 API 的 search/ranked/latest 补齐；Linux.do 缺口由 `LinuxdoDiscoveryProducer` 入队五路同源只读插件任务。所有分支仍只写统一待评估池。X 的 twitter-cli 与 Reddit 的 rdt-cli / OpenCLI 统一遵循 `[network].mode`：`system` 继承环境及系统代理，`custom` 显式注入指定地址，`direct` 清除 CLI 代理环境；浏览器 fallback 仍跟随浏览器网络设置。
- 配额单位是“平台族”，不是 raw `content_cache.source`。B 站的 `search` / `related_chain` / `trending` / `explore` 统一计入 `bilibili`；小红书的 `xhs-extension-*` 统一计入 `xiaohongshu`；抖音的 `dy-plugin-*` / `douyin*` 统一计入 `douyin`；知乎的 `zhihu-search` / `zhihu-hot` / `zhihu-feed` / `zhihu-creator` / `zhihu-related` 统一计入 `zhihu`；Reddit 的 `reddit-search` / `reddit-hot` / `reddit-subreddit` / `reddit-related` 统一计入 `reddit`；Bangumi 的 `bangumi-search` / `bangumi-ranked` / `bangumi-latest` 统一计入 `bangumi`；微博的 `weibo-search` / `weibo-hot` / `weibo-creator` 统一计入 `weibo`。
- B 站缺口仍由 `ContentDiscoveryEngine.discover()` 的四个策略补齐；小红书缺口由 `XhsTaskProducer` / 浏览器插件任务链补齐；抖音缺口由 runtime `DouyinDiscoveryProducer` 调用 `DouyinDiscoveryService(cache=False, evaluate=False)` 拉 raw candidates，小缺口用 feed / hot 快速补零散名额，大缺口用 search + hot/feed 轮换并统一写入待评估池；YouTube 与 X 由各自后台 producer 补齐；知乎缺口由 runtime `ZhihuDiscoveryProducer` 按 `source_modes` 入队插件 search / hot / feed / creator / related 任务补齐；Reddit 缺口由 runtime `RedditDiscoveryProducer` 默认调用 rdt-cli，命令后端不可用时入队插件 fallback；Bangumi 缺口由 `BangumiDiscoveryProducer` 以官方匿名 API 的 search/ranked/latest 补齐；微博缺口由 `WeiboDiscoveryProducer` 以项目自有匿名 client 的 search/hot-as-seed/creator 补齐，两者都只写统一待评估池。X 的 twitter-cli 与 Reddit 的 rdt-cli / OpenCLI 统一遵循 `[network].mode`：`system` 继承环境及系统代理，`custom` 显式注入指定地址，`direct` 清除 CLI 代理环境；浏览器 fallback 仍跟随浏览器网络设置。微博固定 `trust_env=false` 国内直连，不受该海外代理配置影响。
- 如果池子可换数未满但可选平台低于可换配额，`reactivate_under_quota_pool_sources()` 会优先从 `pool_status='suppressed'` 且可打开的高分小平台候选中复活一批，但会同时检查 raw ceiling headroom，避免待评估 / 未整理 raw material 已经占满对应 raw 配额时继续复活。
- `trim_pool_source_overflow()` 和 `trim_pool_to_target_count()` 使用 raw ceiling 配额，而不是前端可换目标；当 `pool_available < pool_target_count` 时，runtime 会跳过 source overflow trim，避免低可用池继续 suppress 当前可换候选；总 raw ceiling 仍由 `trim_pool_to_target_count()` 执行，trim 会先丢 non-linkable、再丢 non-ready，最后才按 relevance / recency 排序，避免为了保留高分 pending 行而删掉可打开候选。
- B 站补货缺口使用 `count_pool_available_candidates_by_source()`，它与 `count_pool_candidates()` 同口径应用预生成 / 分类 / linkability / 最近看过过滤和全局 topic window；raw headroom 使用 `count_pool_raw_material_by_source()`，包含 `content_cache` 未整理素材和 `discovery_candidates` 待评估 / 已评估未入池素材，但同样排除最近看过和已推荐内容。raw headroom 只限制正常请求规模，不再在可用池低于目标时把补货缺口硬压成 0；raw ceiling 的最终约束由每 tick / post-refresh trim 执行。
- B 站补货 limit 使用 `bilibili` 平台自身缺口，而不是“总池子缺口”；例如总池子缺 70 条但 B 站只缺 5 条时，本轮 B 站 discovery 总目标只请求 5 条。小缺口阶段会优先分给 `search=3, related_chain=2`，`trending/explore=0`，避免高成本生成器为几个库存缺口重复跑。
- 后台定时 refresh 不再只要 `pool_available_count < pool_target_count` 就跑 discovery；可换池仍高于约 90% 目标水位时只维护/发布状态，等库存真正低于水位后再补货。用户手动 refresh 仍走显式补货路径。
- 如果 B 站 search 已进入 `v_voucher` / `412` cooldown，本轮 Search / Explore / RelatedChain 内部的搜索分支会直接跳过；Trending 和 RelatedChain 的相关推荐 API 仍可继续提供候选，不会因为 search 风控把整轮 B 站 discovery 卡死。
- 手动 refresh 也走同一套平台缺口计划：如果 B 站已经达到平台配额，而缺口属于小红书或抖音，手动刷新不会再强行跑 B 站 discovery 后又被 source cap 立刻 suppressed。
- 候选协调器缺少 raw work 时会调用 quota-aware `supply_candidates_once()`，即时唤醒所有欠份额且已配置的 producer，再跑 B 站 refresh；只有真实新入队 / 新插入才重置补货退避，单纯“策略执行过”或全部重复不会冒充成功。
- 小红书 producer 会把小红书平台缺口传给关键词生成：只缺 2 条时只生成 2 个搜索关键词，不再固定生成 5 个关键词再让插件慢慢消化。
- 小红书候选必须带可打开的 `xsec_token` URL 才计入可用池子；裸 URL 仍不会参与候选池计数或复活。
- `Database.get_pool_distribution_counts()` 按同一可见性口径返回 `topic_group`、`style_key`、`franchise_key` 计数，供 `PoolDistributionSnapshot` 判断哪些方向已接近饱和。
- pool snapshot 是 discovery 的输入上下文，不改变后续 recommendation serving 的读取路径；推荐层仍然从 `content_cache` 中消费已入池、已预生成文案的候选。

### SearchStrategy

```python
from openbiliclaw.discovery.strategies.strategies import SearchStrategy

strategy = SearchStrategy(
    llm_service=service,
    bilibili_client=bilibili_client,
    queries_per_run=8,
    page_size=10,
    max_pages=1,
    recent_lane_queries_per_run=1,  # 生产 composition：预算内 1 个 pubdate 请求
    recent_lane_page_size=5,        # 最多取 5 条近期供给
    llm_evaluation=True,      # 默认开启 LLM 评估
    score_threshold=0.60,      # 普通入池阈值
)

items = await strategy.discover(profile, limit=20)
items = await strategy.discover(profile, limit=20, pool_snapshot=snapshot)

# 运行后可取中间产物
queries = strategy.last_intermediates.get("queries", [])
```

行为说明：

- 优先通过 `LLMService.complete_structured_task()` 生成 5 到 10 个 B 站搜索词
- 成功解析的搜索词会按 `profile_kw_digest + pool hints digest` 缓存约 6 小时；同画像同池子提示的重复调用不会再次消耗 `discovery.search.queries`
- 如果传入 `pool_snapshot`，会把 `to_prompt_hints()` 写入 query prompt，引导模型软避让已拥挤的 topic/style/franchise，并携带独立的 `source_deficits` 平台缺口信号；运行时快照暂不把平台名转成内容 `prefer_axes`。当 `cold_start=true` 时，prompt 会明确 `avoid_topics` 是高权重兴趣的首批预算保护，不是厌恶项：这些主题整组最多直接占 2 个 query，至少一半 query 应覆盖 `prefer_axes`、较低权重兴趣或一级兴趣域的其它切面，同时保留少量强兴趣入口
- `pool_snapshot` 只是可选上下文：hint 构建失败、返回非 dict 或 hint 无法序列化时会丢弃这段上下文，继续走正常 LLM query 生成，不会直接退回本地 fallback query
- LLM 返回坏 JSON 或空结果时，回退到本地兴趣标签 query
- 正常模式默认抓每个 query 的第一页；backfill 变体会放大 query 数和页数
- API daemon、CLI 与 OpenClaw 的生产 composition 会从每个策略原有搜索预算中预留 1 次 `order="pubdate"` 请求，页大小固定为 5；若预算不足以同时保留至少一个完整普通 query，就不启用近期 lane。`SearchStrategy` 数据类本身默认关闭该 lane，保持第三方/测试 composition 兼容
- 普通结果与近期结果按 query/lane round-robin 交错，近期候选不会因追加在列表尾部而被小型 LLM 窗口全部裁掉；全局仍按 `bvid` 去重，重复的新内容不占第二个候选位
- 近期候选继续使用 `source_strategy="search"`，只在 `discovery_candidates.source_context="search:recent"` 与 `raw_payload.discovery_lane="recent"` 留下 retrieval provenance；该字段不参与 evaluator relevance、admission 或推荐侧 source fatigue
- B 站搜索会使用独立 API client 执行，避免和其他策略共享同一请求 session；如果运行时存在有效 B 站 Cookie，独立 client 会继承该 Cookie，因为当前匿名 WBI search 容易直接返回 `v_voucher` 挑战而不给 `result`
- 如果进程级 B 站 search cooldown 仍在生效，策略会在 LLM query 生成前返回空结果，并把 `last_intermediates.skipped` 标为 `search_cooldown`，避免冷却期继续消耗 LLM token
- 对多个 query 的搜索结果按 `bvid` 去重
- 将结果映射为 `DiscoveredContent`
- 高权重兴趣如果同时命中 query、标题或简介，会拿到更高的起始锚定分，避免核心兴趣搜索长期被宽泛 `explore` 候选压住
- 会把 query 派生的 `topic_key` 一起写入候选，供后续池子压缩和推荐分桶使用
- `llm_evaluation=True` 时（默认），搜索结果会统一过 `evaluate_content()` 做 LLM 打分，只保留高于 `score_threshold` 的候选
- `llm_evaluation=False` 时退回到纯本地启发式打分，适合测试或低成本运行

适合的场景：

- 用户兴趣已经比较明确，系统需要快速补一批“方向对、解释清楚”的候选
- 系统刚完成画像更新，需要把新的偏好尽快翻译成可执行 query

### TrendingStrategy

```python
from openbiliclaw.discovery.strategies.strategies import TrendingStrategy

strategy = TrendingStrategy(
    bilibili_client=bilibili_client,
    llm_service=service,
    score_threshold=0.60,
    max_related_rids=4,
)

items = await strategy.discover(profile, limit=20)
```

行为说明：

- 固定拉取 `rid=0` 全站榜
- 再通过本地确定性洗牌轮转选择最多 `max_related_rids` 个非 0 分区榜；覆盖完一轮后再重新洗牌
- 对每条榜单内容执行 LLM 相关性评估
- 只保留高于阈值的结果

适合的场景：

- 用户并不排斥热门内容，但只想看与自己当前兴趣真正相关的热点
- 需要给候选池补入一些“新鲜、当下、全站正在发酵”的内容

### RelatedChainStrategy

```python
from openbiliclaw.discovery.strategies.strategies import RelatedChainStrategy

strategy = RelatedChainStrategy(
    bilibili_client=bilibili_client,
    llm_service=service,
    memory_manager=memory_manager,
    search_strategy=search_strategy,
    trending_strategy=trending_strategy,
    max_seeds=5,
    max_depth=2,
)

items = await strategy.discover(profile, limit=20)
```

行为说明：

- 优先从事件层的明确正反馈视频中挑选种子：`favorite` / `like` / `coin` / `share` / positive feedback 优先，`view` 只作为后备或在 `inferred_satisfaction=positive` 时提高优先级
- 种子不足时，会先用偏好线索补种子，再回退到 Search/Trending 的高分结果
- 对每个种子调用 `get_related_videos()`，沿相关推荐链最多扩展 2 层
- 全局按 `bvid` 去重，并排除原始种子本身
- 所有候选统一复用 `evaluate_content()` 打分并按阈值过滤
- 每条相关推荐会继承 seed chain 对应的 `topic_key`，避免同一条相关推荐链在池子和推荐批次里刷满

适合的场景：

- 用户已经通过真实观看行为暴露出高价值种子
- 希望从“我刚喜欢过的这条片”继续往下挖，不想每次都从公共热点重新开始

### ExploreStrategy

```python
from openbiliclaw.discovery.strategies.strategies import ExploreStrategy

strategy = ExploreStrategy(
    llm_service=service,
    bilibili_client=bilibili_client,
    score_threshold=0.58,
)

items = await strategy.discover(profile, limit=20)
```

行为说明：

- 先让 LLM 推断 3 到 5 个“高相关但有陌生感”的远域探索方向
- domain 生成强制使用短 JSON：每个方向只含 `domain`、`novelty_level` 和 2 到 3 个 B 站搜索 query，调用预算限制为 2048 tokens
- 生成出的 domain 会按 `profile_kw_digest + covered_topic_groups + max_domains / queries_per_domain` 缓存约 6 小时；池子覆盖面没变时复用，不重复消耗 `discovery.explore.queries`
- 会过滤掉与当前高权重兴趣完全重复的领域，但允许“核心兴趣的近邻扩展”保留下来
- 有足够锚定方向时，只允许最多 1 个完全不直接提及核心兴趣词的远邻方向进入搜索
- 搜索结果统一走 `evaluate_content()`，再叠加 `exploration_bonus`
- 没有直接兴趣锚点的远邻方向，会在最终 `relevance_score` 上吃一个轻量距离惩罚
- 最终保留“相关性足够高，同时比常规策略更有意外感”的内容

适合的场景：

- 用户已经在一个兴趣泡泡里待太久，系统需要主动找一点边界外但仍能说得通的内容
- 推荐层连续几轮都太像，候选池需要新的题材血液

### DiscoveredContent

```python
from openbiliclaw.discovery.engine import DiscoveredContent

item = DiscoveredContent(
    bvid="BV1xx",
    title="纪录片讲透系列",
    up_name="知识区UP",
    source_strategy="search",
    published_at="2026-07-08T06:30:00Z",
    published_label="约一个月前",
    temporal_class="versioned",
    temporal_confidence=0.91,
    temporal_reason="内容依赖具体软件版本",
    temporal_validity_mode="version_state",
    temporal_valid_until="",
    temporal_scope="core",
    temporal_evidence="本教程适用于 Foo 4.x",
    temporal_state="active",
    temporal_evaluated_at="2026-08-13T00:00:00Z",  # code-owned
    temporal_next_review_at="2026-12-11T00:00:00Z",  # code-owned, 120-day review
    temporal_policy_version="v2",  # code-owned
    temporal_evidence_complete=True,  # code-owned validation result
)
```

当前 discovery 结果写入缓存时会稳定填充的字段包括：

- `bvid`
- `item_key` — 由 `make_item_key(source_platform, content_id, content_url)` 派生的平台 canonical identity；相同裸 `content_id` 在不同平台不会冲突
- `title`
- `up_name`
- `up_mid`
- `cover_url`
- `duration`
- `view_count`
- `rating_score` — 目录评分，合法范围 `0..10`；0 表示未知
- `rating_count` — 目录评分人数；不是评论数
- `source_rank` — 来源目录排名；正数才展示
- `published_at`：可信精确时间，规范为 UTC RFC 3339 `YYYY-MM-DDTHH:MM:SSZ`
- `published_label`：来源仅提供相对发布时间时的清洗后纯文本，最长 64 字符
- `temporal_class` / `temporal_confidence` / `temporal_reason`：Agent 对 `breaking/current/versioned/evergreen/historical/unknown` 的语义分类、可信度和诊断理由；`unknown` 是独立安全缺省，不等同于常青内容
- `temporal_validity_mode` / `temporal_valid_until` / `temporal_scope` / `temporal_evidence` / `temporal_state`：同一轮 Agent 必须完整给出的原子证据组；mode 为 `none/explicit_deadline/event_state/version_state/freshness_only`，scope 为 `none/core/hook`，evidence 是 prompt-visible 正文的逐字摘录，state 为 `unknown/active/expired/superseded`
- `temporal_evaluated_at` / `temporal_next_review_at` / `temporal_policy_version` / `temporal_evidence_complete`：只由代码生成的评估时钟、下次复审时钟、策略版本与整组验证标记；1 / 14 / 120 天只安排 `breaking/current/versioned` 复审，不能单独证明内容过期
- `description`
- `source_strategy`
- `discovery_lane` — 只描述检索通道；当前唯一合法值为 `recent`，为空表示普通通道，不改变来源策略或评分语义
- `relevance_score`
- `relevance_reason`
- `topic_key`
- `style_key`
- `candidate_tier`
- `discovered_at`
- `last_scored_at`
- `body_text` — 纯文字内容主体（推文 / thread 全文或 `note_tweet` 长文）；视频源留空。X 是首个以文字为主的来源，模型为此增设该字段
- `content_type` — 内容形态，复用候选池既有 shape 字段：`"video"`（默认）/ `"note"`（小红书）/ `"tweet"` / `"thread"`（X）/ `"subject"`（Bangumi）/ `"post"`（Linux.do）。`to_cache_kwargs()` 透传正文、形态与目录评分字段，并经 storage migration 补齐旧库列；候选转换优先取 `item.content_type`，保证文字和条目候选不被强标成 `video`
- `content_type` — 内容形态，复用候选池既有 shape 字段：`"video"`（默认）/ `"note"`（小红书）/ `"tweet"` / `"thread"`（X）/ `"subject"`（Bangumi）/ `"post"`（微博）。`to_cache_kwargs()` 透传正文、形态与目录评分字段，并经 storage migration 补齐旧库列；候选转换优先取 `item.content_type`，保证文字和条目候选不被强标成 `video`

缓存写入使用 `content_storage_key()`：B 站继续以 raw BV ID 作为 `content_cache.bvid`，其它平台使用 canonical `item_key`；原始平台 ID 始终保留在 `content_id`。只有 B 站内容会从 raw `content_id/bvid` 生成兼容视频 URL，非 B 站内容以来源提供的 `content_url` 为准。

## 示例：一轮 discover 之后会发生什么

假设这轮 `discover(profile, limit=12)` 的初始结果里有这些候选：

- `search` 找到 5 条，其中 2 条其实都在讲同一主题
- `trending` 找到 3 条，其中 1 条和 `search` 命中了同一个 `bvid`
- `related_chain` 找到 4 条，但其中 2 条都来自同一条 seed chain
- `explore` 找到 4 条，方向新，但有 2 条风格都偏同一种纪录片叙事

引擎不会直接把这 16 条原样塞进池子，而是会依次做：

1. 对重复 `bvid` 保留分数更高的版本。
2. 优先保留 `primary` 候选，再考虑补货候选。
3. 根据 `topic_key` 压掉同主题重复项。
4. 根据 `style_key` 和 `source_strategy` 再做一轮轻量均衡。
5. 把收口后的结果写进 `content_cache`。

所以最后用户看到的推荐之所以“不那么像复制粘贴”，很大程度上不是因为 LLM 临场发挥，而是因为 discovery 在更早一层就把候选池整理过了。

## 模块边界与外部协议

Discovery 模块不是独立运行的，它和上下游模块之间有清晰的输入输出边界。

### 输入：从 Soul 模块消费什么

Discovery 的起点是一个 `SoulProfile`（或 `OnionProfile` 转换而来的兼容对象）。每个策略从画像里取不同的切面：

| 策略 | 消费的画像字段 | 用途 |
|------|-------------|------|
| **SearchStrategy** | query 生成通过 `build_query_generation_profile_summary()` 消费稳定画像摘要：核心特质 / 认知风格 / 价值观 / 动机 / deep needs 各前 8，`interest_domains[:16]`，从最多 128 个候选中按权重 + cached embedding 多样性选出的 `interests[:64]`，多样性去重后的 `disliked_topics[:64]`，style、探索开放度和 MBTI。近期觉察、active insights、时间戳、source provenance 和 session context 不进入 query prompt；内容评估仍走 eval profile。 | 生成搜索词、计算兴趣锚定分，并避开长期雷点 |
| **TrendingStrategy** | 同上（通过 `build_profile_summary()`） | 评估排行榜内容相关性；排行榜分区选择为本地轮转 |
| **RelatedChainStrategy** | `interests[:2]`, `favorite_up_users[:1]` + 全画像用于评估 | 生成偏好种子、评估相关链内容 |
| **ExploreStrategy** | 同 Search + **`exploration_openness`**（关键） | 生成跨域方向、计算探索 bonus |

**协议约定**：
- Discovery 只读取画像，不修改画像
- 画像由 `soul/` 模块维护，discovery 不关心画像是如何构建或更新的
- 如果画像为空或缺少关键字段，策略会 fallback 到默认行为（空兴趣列表、默认分区等）

### 输出：给 Recommendation 模块提供什么

Discovery 的 raw 产出是 `list[DiscoveredContent]`。runtime 正常补货链路会先把它们写入 SQLite `discovery_candidates`，再由 `DiscoveryCandidatePipeline` 混源 batch 评估、过滤和 admission 到 `content_cache`，供推荐层消费。

`ContentDiscoveryEngine.discover()` 仍保留直接评估并写入 `content_cache` 的兼容路径，用于手动调用、旧测试和没有 candidate pipeline 的 fallback。

```
DiscoveredContent
├── bvid, title, up_name, up_mid      # B 站内容标识
├── cover_url, duration, view_count     # 展示元数据
├── relevance_score (0.0-1.0)          # LLM 评估的相关度
├── relevance_reason                    # 自然语言推荐理由
├── source_strategy                     # 来源策略（search/trending/related_chain/explore）
├── topic_key, style_key               # 多样性控制信号
├── candidate_tier                      # primary / backfill
└── discovered_at, last_scored_at      # 时间戳
```

**协议约定**：
- 推荐层可以信赖 `relevance_score` 和 `relevance_reason` 已经被填充
- 推荐层可以用 `topic_key` + `style_key` + `source_strategy` 做多样性控制
- Discovery 不做最终的推荐排序和文案生成，那是 `recommendation/` 的职责

### 外部依赖：B 站 API 和 LLM

Discovery 策略通过 Protocol 接口消费外部服务，不直接依赖具体实现：

| Protocol | 方法 | 实现者 |
|----------|------|--------|
| `SupportsSearchClient` | `search(keyword, page, page_size, order)` | `BilibiliAPIClient` / `MockBilibiliClient` |
| `SupportsRankingClient` | `get_ranking(rid)` | `BilibiliAPIClient` / `MockBilibiliClient` |
| `SupportsRelatedClient` | `get_related_videos(bvid)` + `search(...)` | `BilibiliAPIClient` / `MockBilibiliClient` |
| `SupportsMemoryManager` | `query_events(event_types, limit, ...)` | `MemoryManager` / `MockMemoryManager` |
| `SupportsStructuredTask` | `complete_structured_task(...)` | `LLMService` (任意 provider) |

这种显式 Protocol 设计意味着：
- 测试可以用 mock 替代真实服务
- 评估循环可以用 `MockBilibiliClient` 离线运行
- 新增 B 站数据源只需实现对应 Protocol

### 中间产物：给评估系统提供什么

每个策略运行后会在 `last_intermediates` 中暴露内部决策产物：

| 策略 | `last_intermediates` 内容 |
|------|--------------------------|
| SearchStrategy | `{"queries": ["纪录片 原理", "摄影 构图", ...]}` |
| TrendingStrategy | `{"rids": [0, 36, 188, ...]}` |
| RelatedChainStrategy | `{"seeds": [("BV...", "topic_key"), ...]}` |
| ExploreStrategy | `{"domains": [{"domain": "...", "novelty_level": 0.62, ...}, ...]}` |

评估系统通过这些中间产物可以独立评估搜索词质量、分区选择合理性、种子选择质量和探索方向创造性，而不只是看最终结果。

## 评估与优化体系

Discovery 模块有一套与 Soul 模块平行的评估优化框架，支持自动 SGD 循环和人工评估两种模式。

### 为什么 Discovery 的评估和 Soul 不一样

Soul 评估有明确的 ground truth：一个预定义的 `OnionProfile`，可以逐字段对比。Discovery 不行——没有一组"绝对正确的推荐视频"。所以 Discovery 的评估是**多维质量打分**，而不是结构对比。

### 7 维评估体系

| 维度 | 权重 | 打分方式 | 适用策略 |
|------|------|---------|---------|
| `relevance` | 0.30 | LLM judge: 内容是否真正匹配画像 | 全部 |
| `diversity` | 0.15 | 算法: topic/style 的 Shannon 熵 | 全部 |
| `specificity` | 0.15 | LLM judge: 结果是否个性化而非泛热门 | 全部 |
| `query_quality` | 0.10 | LLM judge: 搜索词/域的创造性和针对性 | search, explore |
| `explanation_quality` | 0.10 | 算法: relevance_reason 的完整度 | trending, related, explore |
| `novelty` | 0.10 | 算法: 不在已知兴趣中的比例 | explore, trending |
| `no_echo_chamber` | 0.10 | 算法: topic 集中度惩罚 | 全部 |

### Prompt 归因映射

评估系统能把"哪个维度分低"归因到"应该改哪个 prompt"：

```python
DISCOVERY_FIELD_TO_PARAM = {
    "search.query_quality":            "search_queries_prompt",
    "search.relevance":                "search_queries_prompt",
    "trending.relevance":              "content_evaluation_prompt",
    "explore.query_quality":           "explore_domains_prompt",
    "explore.novelty":                 "explore_domains_prompt",
    "explore.relevance":               "content_evaluation_prompt",
    "related_chain.relevance":         "content_evaluation_prompt",
    "related_chain.explanation_quality":"content_evaluation_prompt",
    ...
}
```

### 模拟内容宇宙

评估循环不能调用真实 B 站 API。`ScenarioGenerator` 会为每个 persona 生成一个模拟的 B 站内容宇宙：

- **60 条模拟视频**（~30% 高相关 / ~30% 中相关 / ~20% 低相关 / ~20% 噪音）
- **搜索索引**：按标题/标签关键词建立倒排，搜索词质量真正影响搜索结果
- **排行榜分组**：按分区 rid 组织
- **相关视频图**：每条视频关联 3-5 条相关视频
- **行为事件**：5-8 条模拟观看/点赞事件供 RelatedChain 选种子

`MockBilibiliClient` 满足所有策略的 Protocol 接口，搜索时会做关键词模糊匹配而不是返回固定列表。

### 自动优化循环

```text
for each epoch:
    1. 生成/加载 persona (复用 soul 的 PersonaPool)
    2. 生成/加载 scenario (ScenarioPool 缓存)
    3. 用 MockBilibiliClient 运行 4 个策略
    4. DiscoveryEvaluator 做 7 维评估
    5. 最差维度 → FIELD_TO_PARAM → 定位到具体 prompt
    6. Exploit (修最差的 prompt) 或 Explore (随机扰动)
    7. Apply → 验证 → Accept 或 Rollback
    8. Early stopping (patience >= 3)
```

运行方式：

```bash
.venv/bin/python scripts/run_discovery_auto_optimize.py \
    --rounds 10 --batch 3 --explore-rate 0.2 --patience 3
```

### 人工评估

```bash
.venv/bin/python scripts/run_discovery_eval.py --mock
```

会逐策略展示发现结果和中间产物，人工对每个维度打 0-1 分，生成 `DiscoveryEvalReport`，可选触发一轮优化。

### 评估系统文件清单

| 文件 | 职责 |
|------|------|
| `eval/discovery_evaluator.py` | 7 维评估器 + FIELD_TO_PARAM + 算法/LLM 打分函数 |
| `eval/discovery_scenario.py` | ScenarioGenerator + MockBilibiliClient + MockMemoryManager + ScenarioPool |
| `eval/discovery_optimizer.py` | Discovery 专属参数注册表 + `create_discovery_optimizer()` 工厂 |
| `eval/agents.py` | `run_discovery_optimizer_agent()` — 发现系统专用优化 agent |
| `scripts/run_discovery_auto_optimize.py` | SGD 自动优化循环 |
| `scripts/run_discovery_eval.py` | 人工评估交互脚本 |

## 设计决策

1. **策略显式注入依赖**：`SearchStrategy` 不自己构建 LLM 或 API client，便于测试和后续编排
2. **query 生成走结构化任务**：各策略统一把 `build_profile_summary()` 的结构化画像放进 user prompt，并在 `LLMService` 支持时关闭额外 core memory 注入；这样 query / explore domain 生成能看到同一份画像，同时 system prompt 保持静态、provider prompt-cache 前缀稳定
3. **坏 JSON 有本地 fallback**：保证搜索策略在 LLM 不稳定时仍可运行
4. **排行榜分区本地轮转覆盖**：固定 `rid=0`，其余分区按确定性洗牌轮转取样，覆盖完一轮后再进入下一轮，不再为 rid 选择消耗 LLM token
5. **相关推荐链优先复用真实行为**：种子优先来自近期事件，其次才是偏好补种子和策略兜底
6. **跨领域探索强调“可解释的陌生感”**：不是越远越好，而是“主题陌生，但心理需求上说得通”
7. **评分入口集中在引擎层**：`ContentDiscoveryEngine.evaluate_content()` 统一负责把 `score/reason` 写回 `DiscoveredContent`
8. **发现引擎承担最终收口职责**：策略负责找内容，引擎负责并发调度、去重排序、分层补货和缓存写入
9. **引擎层仍不负责依赖创建**：`ContentDiscoveryEngine` 接收外部注入的 `llm_service` / `database`，策略继续显式注入 client/service
10. **补货是显式分层而不是无脑放宽**：主发现优先，backfill 只在候选不足时介入，并通过 `candidate_tier` 保留来源语义
11. **池子层先做一次轻压缩**：topic 多样性不能只在推荐层补救，发现结果在写入 `content_cache` 前也会先压一轮同 topic 重复项，防止单一 seed chain 灌满候选池
12. **观看状态先在入池时做轻标注**：`style_key` 不追求题材完备，但必须足够稳定，保证推荐层能区分“深度专注 / 快速扫信息 / 跟做学习 / 叙事沉浸”等观看模式
13. **候选窗口本身也要按来源打散**：如果 `get_pool_candidates()` 的前 30 条几乎全是 `explore`，下游再怎么多样化都很难救；因此 discovery pool 读取阶段也会做来源交错取样
14. **来源补齐优先级高于风格上限**：在 discovery 压缩时，新的 `search / trending / related_chain` 候选应优先获得一个坑位，不能先被重复的 `style_key` 卡死
15. **`style_key` 规则宁可偏粗，也不能混淆题材和体感**：芯片、显微镜、理论、哲学这类更适合 `deep_focus`；全过程、制造过程、人物事件复盘更适合 `story_immersion`
16. **补货要看来源缺口，不只看池子总量**：如果池子总数够了但 `trending` 或 `xiaohongshu` 一直接近 0、`explore` 却超标，体感仍会单一；runtime refresh 现在按来源族配额评估缺口，B 站策略只补 B 站缺口，小红书缺口交给 xhs producer / 扩展任务链
17. **`explore` 也要控内部子簇，不只控总量**：即使 `explore` 总数没超标，制造 / 工艺 / 材料、博弈 / 桌游 / 机制这类相邻方向也可能在内部堆成一大簇；refresh 现在会把过量部分温和压到非 `fresh`，避免”可换窗口只剩一个味”
18. **四个策略统一走 LLM 评估**：`SearchStrategy` 不再只用本地启发式打分，默认也走 `evaluate_content()`；这让评估系统可以统一优化 `content_evaluation_prompt` 对全部策略生效
19. **策略暴露中间产物**：每个策略的 `last_intermediates` 让评估系统能独立评估搜索词质量、分区选择、种子选择和探索方向，而不只是看最终结果列表
20. **评估用多维质量打分而不是对比 ground truth**：Discovery 没有”正确答案”，所以评估的是结果集在 relevance / diversity / specificity / novelty 等 7 个维度的质量
21. **模拟内容宇宙做模糊匹配，不是固定列表**：`MockBilibiliClient` 的搜索基于关键词倒排 + 模糊匹配，搜索词质量真正影响返回结果，评估才有意义
22. **评估归因到 prompt 级别**：`DISCOVERY_FIELD_TO_PARAM` 映射维度到具体 prompt，优化器可以定向修改最影响评分的那个 prompt，而不是盲目调所有
23. **PromptOptimizer 参数化复用**：不为 discovery 写新的 optimizer，而是让 `PromptOptimizer` 接受不同的参数注册表和白名单，soul 和 discovery 共享 apply/commit/rollback 机制
24. **长期避雷项必须进入发现前置上下文**：近期 negative exemplars 只能覆盖短期样本，`disliked_topics` 才代表稳定画像里的长期避让；因此 discovery 的共享 `profile_summary` 必须显式携带它，供 query 生成和内容评估共同消费
25. **画像摘要要保留决策上下文而不是只传兴趣标签**：Search / Trending / Explore 都在问“什么内容适合这个人”，只传兴趣名会让模型退化成关键词扩写；因此 `build_profile_summary()` 同步携带认知风格、价值观、当前阶段、MBTI、近期觉察、当前洞察、来源分布和兴趣来源时间，但仍按前若干项裁剪，避免无界 prompt 膨胀
26. **评估批次保留成功 sibling**：keyed partial payload 先写入有效评分，只对缺失 ID 使用共享 depth=3 / 六次额外请求预算；预算耗尽仍未解析的候选标记 `evaluation_response_missing`，不以零分拒绝，而是在 claim token 仍匹配时无 attempt 增量回到 `pending_eval`。
