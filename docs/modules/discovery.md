# 内容发现引擎

> 从用户画像出发，在 B 站上主动寻找潜在会喜欢的内容。

## 概述

`discovery/` 包负责把用户的 Soul 画像转换成“可被搜索、可被评估、可被推荐”的候选内容集合。

它解决的不是“B 站上有没有内容”，而是“面对海量内容，系统应该先替这个用户去哪里找、找到之后为什么值得留下、怎样避免候选池被单一方向刷满”。

可以把 discovery 理解成推荐前的供给层：

- `soul/` 负责理解这个人最近在意什么
- `discovery/` 负责把这种理解翻译成一批值得看的候选内容
- `recommendation/` 再从候选池里挑出这一批最该推的几条

如果没有 discovery，推荐层通常只能在一小撮现成候选里排序；有了 discovery，系统才有能力主动去“找货”，而不是被动等用户自己刷到。

当前模块包含：

- **ContentDiscoveryEngine** — 发现策略编排器，负责注册、运行、去重和汇总
- **DiscoveredContent** — 统一的候选内容数据结构
- **SearchStrategy** — 基于画像生成搜索词并调用 B 站搜索的策略
- **TrendingStrategy** — 从全站榜和相关分区榜中筛选高匹配热点内容
- **RelatedChainStrategy** — 从近期高价值视频种子出发，沿相关推荐链扩展候选内容
- **ExploreStrategy** — 推断"高相关的远域探索方向"，寻找更有陌生感但仍可解释的内容
- **PoolDistributionSnapshot** — runtime 在补池前构建的候选池分布快照，给 discovery 提供当前供给拥挤/缺口的软信号
- **SourcePolicy** — 统一读取 `sources.<platform>.enabled` 与 `[scheduler.pool_source_shares]`，生成有效平台配比；关闭的平台保留配置但不占 runtime quota
- **SourceAdapter 协议** — 多源适配层（`sources/`），在上述 4 个 B 站策略之外挂载非 B 站内容源（小红书、抖音初始化画像信号与 search / hot / feed discovery、YouTube 初始化画像信号、知乎、V2EX 等）

## 多源适配层

`sources/` 把"内容从哪里来"从"怎么挑"里彻底解耦。`ContentDiscoveryEngine` 通过 `register_adapter()` 挂载任意实现了 `SourceAdapter` 协议的源，每个源用一条 `SourceRecipe`（`source_type` + `strategy` + `config`）描述订阅，引擎在一轮 discovery 里并发驱动所有启用的 recipe。

当前已实现的 adapter：

- **BilibiliAdapter** — 把四大 B 站策略包装成 adapter 形态，对 recipe 的 `strategy` 字段分发到 `SearchStrategy` / `TrendingStrategy` / `RelatedChainStrategy` / `ExploreStrategy`。
- **WebSourceAdapter / XiaohongshuAdapter** — 通用"浏览器 + LLM 抽取"通道。走 `BrowserManager` 拿页面 `(innerText, anchors)` 快照，用 LLM 从 innerText 提取标题 / 作者 / 摘要，再用 anchor 列表按标题模糊匹配回填 `content_url` / `content_id`。
- **DyTaskQueue** — 抖音初始化画像、`fetch-douyin` smoke、search / hot / feed discovery 都走同一扩展任务桥；初始化回传发布 / 收藏 / 点赞 / 关注后转成统一行为事件，discovery 任务只保留候选结果。
- **YtTaskQueue / Takeout parser** — YouTube 初始化画像走扩展任务桥读取观看历史 / 订阅 / 点赞；Google Takeout 导入走 `youtube.takeout` 离线解析，两条入口都转成统一行为事件。`yt_tasks` 不承载 steady-state discovery。
- **YouTube discovery strategies / producer** — `yt_search` 由 LLM 从画像生成关键词后用 `scrapetube` 搜索，`yt_trending` 优先通过 YouTube InnerTube browse API 拉 trending feed，当前 `FEtrending` 失效时降级抓取公开 topic 页的 `ytInitialData` 视频，`yt_channel` 从 DB 中 YouTube follow 事件读取订阅频道并用 `scrapetube` / `yt-dlp` 拉最新视频；三者由后端 `YoutubeDiscoveryProducer` 在 YouTube 低于 quota 时独立调度，输出 `source_platform="youtube"` 的 `DiscoveredContent` 并进入 LLM 打分过滤 / 缓存路径。
- **DouyinDiscoveryService / DouyinDirectStrategy / DouyinDirectClient** — 抖音 discovery 走 opt-in 路径，服务层统一封装 search / hot / feed 三个公开来源，既可通过 `ContentDiscoveryEngine` 写入 `content_cache`，也可在 `openbiliclaw discover-douyin --no-cache` 下直接跑策略调试。
- **DouyinPluginSearchClient** — search 子来源优先复用 `dy_tasks(type="search")` 插件签名链路，结果以 `dy-plugin-search` 进入 discovery；hot 子来源优先复用 `dy_tasks(type="hot")`，由扩展后台打开 `/hot/{sentence_id}` 后签名 related API，结果以 `dy-plugin-hot-related` 进入 discovery；feed 子来源复用 `dy_tasks(type="feed")`，由扩展在后台首页签名 `/aweme/v1/web/tab/feed/`，结果以 `dy-plugin-feed` 进入 discovery。search / hot / feed discovery 任务都会用非激活 tab 执行，只有 `bootstrap_profile` 这类显式账号信号导入允许前台。每次入队前会把过期的 search / hot / feed pending discovery 任务标记为 failed，避免旧任务挡住当前 producer；`ContentDiscoveryEngine.register_strategy()` 会按 strategy name 替换旧实例，避免 `DouyinDiscoveryService(cache=True)` 多轮运行后累积多个 `douyin_direct` 并重复入队 search。`openbiliclaw search-douyin` 仍保留为独立 search smoke / 诊断命令，结果不转成 memory event。

`BrowserManager` 有两个可替换后端，由 `[sources.browser].cdp_url` 决定：

1. **CDP 后端（推荐）**：Playwright `connect_over_cdp` 连到你预先启动的 Chrome，复用真实登录 cookie。小红书这种反匿名严格的源只有这条路能稳定跑。
2. **agent-browser 后端（回退）**：匿名访问，适合不要求登录的简单页面。

启动步骤见 [`docs/modules/config.md` 的 `[sources.browser]`](./config.md#sourcesbrowser) 段落。

## 发现链路怎么工作

一次完整的 discovery，当前可以概括成 6 步：

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

   真正进入发现策略时，画像会被压缩成更容易消费的结构化摘要。比如 `SearchStrategy` 会取前几个高权重兴趣、核心特质、认知风格、deep needs、`disliked_topics`、近期觉察和来源分布来生成 query；`ExploreStrategy` 则会额外看探索开放度、价值观、当前阶段和洞察，判断这轮适不适合往陌生方向走。

   这一步的目标不是“把画像完整搬过去”，而是从画像里抽出对找内容最有用的信号。

2. **并发运行多种策略**
   `ContentDiscoveryEngine.discover()` 不会按“先 search、再 trending、再 related”串行慢慢跑，而是把当前启用的策略一起丢给 `_run_strategies()`，内部用 `asyncio.gather(..., return_exceptions=True)` 并发执行。

   这样做有两个直接好处：
   - 延迟更低，不需要等一个策略完全结束再开始下一个
   - 容错更强，单个策略失败不会把整轮 discover 拖死

   每个策略拿到的是同一个画像，但做的事情不同：
   - `SearchStrategy` 负责把画像翻译成搜索词并调用搜索接口
   - `TrendingStrategy` 负责去排行榜里挑“适合这个人”的热点
   - `RelatedChainStrategy` 负责从已有高价值种子沿相关推荐继续扩展
   - `ExploreStrategy` 负责故意往相邻但更陌生的方向试探

   这一层的核心思想是：先尽量把供给面铺开，再在后面统一收口。

3. **统一评估和合并**
   虽然四个策略的找法不同，但产出都会被转成同一个结构：`DiscoveredContent`。这样引擎后面就能用同一套逻辑处理它们。

   统一处理主要包括两件事：
   - **字段归一**：例如都整理成 `bvid / title / up_name / duration / description / source_strategy / topic_key / style_key`
   - **相关性评估**：`TrendingStrategy`、`RelatedChainStrategy`、`ExploreStrategy` 会调用 `ContentDiscoveryEngine.evaluate_content()`，把内容标题、简介、来源和画像摘要一起交给 LLM，得到 `relevance_score` 和 `relevance_reason`

   当前四个策略都会走 LLM 评估：`SearchStrategy` 在本地启发式打分后对候选统一调用 `evaluate_content()`，`TrendingStrategy`、`RelatedChainStrategy`、`ExploreStrategy` 也各自在 discover 流程中调用 `evaluate_content()`。进入批量 LLM 评估前，`ContentDiscoveryEngine.evaluate_content_batch()` 会读取 `Database.get_recent_viewed_content_keys()`，用 `source_platform:content_id` 判断最近看过的 B 站 / 小红书 / 抖音 / YouTube 候选；命中项直接记为 0 分并从 prompt 中剔除，避免为已看内容消耗 discovery token。老 BVID 也保留 raw key 兼容旧数据。只有通过 `score_threshold`（默认 0.65）的内容才会被保留。

   之后引擎会合并所有策略返回的列表，并通过 `_merge_duplicates()` 按跨源内容身份去重：B 站内容使用 `bvid`，YouTube / 小红书 / 抖音等多源内容使用 `source_platform + content_id`，缺失时再退到 URL / 标题。这样同一个视频被多个策略同时找到时，会保留 `relevance_score` 更高的那个版本，同时不会把多个非 B 站候选因为空 `bvid` 误合并。

   这一步的作用，是把”不同来源的原始线索”变成”可以比较的一组候选”。

4. **按相关性和供给层级排序**
   合并完成后，引擎会进入 `_merge_and_rank()` 做第一次统一排序。当前排序不是只看分数，而是先看候选层级，再看内容质量信号：

   - 先保 `candidate_tier == "primary"` 的主发现结果
   - 再看 `relevance_score`
   - 同分附近再参考 `view_count`
   - 如果 runtime 传入 `PoolDistributionSnapshot`，会在压缩前用 pool 饱和方向做一轮软重排：已拥挤的 topic/style/franchise 会轻微降权，手动传入的 undercovered axes 会轻微加权，但不会改写最终落库的 `relevance_score`
   - 若主发现数量不够，再进入 backfill

   backfill 的做法也不是简单“补一些随便的内容”，而是分两层：
   - 先问各个策略有没有 `create_backfill_strategy()`，如果有，就用更宽松的参数再跑一轮
   - 还不够的话，再从历史 `content_cache` 里捞尚未推荐的旧候选补位

   所以这一步实际解决的是“这轮找出来的内容，哪些应该算主力，哪些只是供给不足时的补货”。

5. **压缩重复主题和来源**
   只按分数排序还不够，因为高分内容很可能高度同质。引擎会再进入 `_compress_topic_repeats()` 做一轮轻量压缩，防止候选池被单一方向灌满。

   当前压缩主要看三个维度：
   - `topic_key`：防止同一搜索 query、同一相关推荐链、同一主题桶连着塞进来
   - `style_key`：防止全是同一种观看体感，比如一批全是 `deep_dive` 或全是 `news_brief`
   - `source_strategy`：防止 `explore`、`related_chain` 之类单一来源刷满前排

   实现上不是一刀切删掉重复内容，而是：
   - 先尽量给不同 topic、不同 source 留坑位
   - 对重复 style 和重复 source 设一个上限
   - 装不下的内容先放进 deferred 队列，后面如果还有空位再回填

   这一步决定的是候选池“看起来像不像一个活的内容池”，而不是一串只会换标题不会换方向的重复片单。

6. **写入缓存池**
   收口后的结果会通过 `_cache_results()` 写入 SQLite 的 `content_cache`。写入时不只存视频标题和 `bvid`，还会把 discovery 阶段已经得到的信号一并落下来，例如：
   - `relevance_score`
   - `relevance_reason`
   - `candidate_tier`
   - `topic_key`
   - `style_key`
   - `source_strategy`

   最近看过的内容即使被上游策略再次找到，也会用 `source_platform:content_id` 在 `_cache_results()` 写库前跳过，不再进入 `content_cache` 候选池。这样推荐层在后续 `reshuffle`、`append`、常规推荐排序时，就不必重新跑一遍 discovery，也能直接利用这些结构化信号做多样性控制和快速选片。

   换句话说，discovery 的产出不是“一次性的返回值”，而是一份会进入候选池、影响后续多轮推荐的中间资产。

这意味着 discovery 的目标不是单次找到“绝对最优的一条”，而是持续维护一个质量够高、来源够杂、还能解释为什么会命中的候选池。

## Prompt 示例：LLM 在 discovery 里具体干什么

discovery 不是“把整个找片过程都交给 LLM”。当前实现里，LLM 主要做 4 类结构化工作：

- 帮 `SearchStrategy` 生成搜索 query
- 帮 `TrendingStrategy` 挑更相关的排行榜分区
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
  "personality_portrait": "最近更像在主动搭建自己的理解框架，喜欢把复杂问题拆开看。",
  "core_traits": ["理性", "好奇", "重结构"],
  "interests": [
    {"name": "国际局势", "category": "知识", "weight": 0.92},
    {"name": "历史", "category": "知识", "weight": 0.84},
    {"name": "纪录片", "category": "影视", "weight": 0.79}
  ],
  "favorite_up_users": ["某知识区UP"],
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

- 解析 JSON 失败就放弃这轮 LLM 结果
- query 去重
- 最多取配置允许的前几条
- 如果收到 `PoolDistributionSnapshot`，会把 `to_prompt_hints()` 注入 prompt 的 `<pool_distribution_hints>`，让模型把 `avoid_topics` / `avoid_styles` / `avoid_franchises` / `prefer_axes` 当作软指导；这些信号不能覆盖画像相关性，也不能把 `source_deficits` 里的平台名当成搜索主题
- 如果 snapshot hint 构造失败，会记录异常并回退到普通 query 生成
- 如果 LLM 完全不可用，就回退到“兴趣名 / 核心特质”直接拼出的本地 query

### 2. 排行榜分区选择 prompt

`TrendingStrategy` 并不是把所有分区榜都抓一遍。它会先固定抓 `rid=0` 全站榜，再让 `build_trending_rids_prompt()` 从画像里挑 3 到 5 个更相关的分区。

示例：

```text
<task>
你要从用户画像中推断最值得关注的 B 站排行榜分区 rid。
</task>

<rules>
1. 输出必须是严格 JSON，不要附带解释。
2. 只返回 3 到 5 个最相关的分区 rid，不包含 0。
3. 如果不确定，优先选择知识、科技、影视、纪录片相关分区。
</rules>
```

如果画像明显偏“知识 + 深度 + 纪录片”，模型可能会回：

```json
{
  "rids": [36, 188, 181, 119]
}
```

然后策略层会做两件事：

- 把这些 rid 去重并裁到上限
- 无论模型选了什么，最终实际抓取时都会变成 `[0, ...selected_rids]`

也就是说，全站榜一定会看，分区榜只是补充“更像这位用户会在意的热点区域”。

### 3. 内容相关性评估 prompt

这是 discovery 里最关键的一类 prompt。`TrendingStrategy`、`RelatedChainStrategy`、`ExploreStrategy` 都会把候选内容交给 `ContentDiscoveryEngine.evaluate_content()`，后者内部调用 `build_content_evaluation_prompt()`。

它的 system prompt 重点是：

```text
<task>
你要评估一个 B 站内容与这个用户画像的匹配度。
</task>

<rules>
1. 输出必须是严格 JSON，不要附带解释。
2. score 范围必须在 0 到 1 之间。
3. reason 只写一句中文，解释为什么这个人会喜欢或不喜欢这个内容。
4. 不要只说“因为热门”或“因为看过类似的”，要结合用户画像。
</rules>
```

这时传给模型的内容是“画像摘要 + 单条内容摘要”：

```json
{
  "profile_summary": {
    "personality_portrait": "更偏好高信息密度、能把复杂问题讲透的内容。",
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
  "reason": "这条内容会对上你偏好的高信息密度和结构化解释，也正贴合你最近在意的国际议题。"
}
```

收到后，引擎还会继续做这些事：

- 把 `score` clamp 到 `0.0 ~ 1.0`
- 把 `reason` 写回 `DiscoveredContent.relevance_reason`
- 如果 JSON 非法或字段坏掉，这条评估直接按 `0.0` 处理

#### v0.3.x 负样本锚定（batch evaluator）

`ContentDiscoveryEngine._evaluate_batch` 在每次 batch 调用前会通过 `_get_negative_exemplars()` 从事件层拉一份「最近真正不喜欢」的标题列表（来自 `soul/negative_exemplars.py` 的 recency-weighted、去重、80 字截断、最多 8 条），并作为 `negative_examples=` 透传给 `build_batch_content_evaluation_prompt()`：

- 引擎实例内部 `_get_negative_exemplars` 的 exemplar 缓存形如 `(timestamp, latest_event_id, exemplars)`，命中条件是 `latest_event_id` 未变且 `< 300s`（即 5 分钟 TTL）。同一窗口内的多次 batch 共用一次 `query_events` I/O；用户新打一条负反馈后，`latest_event_id` 改变，下一次 batch 立即看到新样本。注意这是 exemplar 池本身的缓存；候选**分数**的复用则通过 `_batch_eval_cache_key` 把 `latest_event_id` 拼进 cache key 完成（见下条 batch 评分缓存说明），两套机制互相独立。
- 上游 `_get_negative_exemplars()` 与 `recent_negative_exemplars()` 都把异常吞成 `None`/`[]`，event 表为空或存储抖动都不会中断 batch；user prompt 自动退回到无 `<negative_examples>` 形态，cache prefix 不被打断。
- 拿到 exemplars 后 prompt builder 把它放在 `<source_context>` 与 `<content_batch>` 之间（系统规则 10/11 让 LLM 按话术 / 商业意图 / 标题结构层面去对照打分，而不是关键词重叠）。前置 `[soul.preference] satisfaction_filter_enabled` 未打开时，事件分类仍在跑，所以负样本池可以提前积累。batch 评分缓存 key 带最新 event id，避免负样本出现后继续复用旧分数。

所以这里的 LLM 不是“决定推荐”，而是在给候选池补一个统一、可比较的相关性分数。

### 4. 跨领域探索 prompt

`ExploreStrategy` 用的 `build_explore_domains_prompt()`，目标不是直接让模型给视频，而是让它先提出“什么陌生方向值得搜”。

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
5. why_it_might_resonate 要先解释这种陌生内容对应了用户的哪种认知需求或信息处理偏好。
6. novelty_level 范围必须在 0.4 到 0.8 之间。
7. 每个 domain 生成 1 到 2 个适合 B 站搜索的 query，不能写抽象句子。
</rules>
```

如果用户当前兴趣是“国际局势 / 历史 / 纪录片”，一个合理输出可能是：

```json
{
  "domains": [
    {
      "domain": "战争工业史",
      "why_it_might_resonate": "你不只是关心事件结果，更在意背后的系统结构和长期因果。",
      "novelty_level": 0.64,
      "queries": ["战争工业史 纪录片", "军工体系 深度讲解"]
    },
    {
      "domain": "外交谈判案例",
      "why_it_might_resonate": "这类内容能把复杂局势拆成更具体的策略和博弈过程。",
      "novelty_level": 0.58,
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

### 5. 一个完整的 prompt 调用链例子

假设用户最近明确偏好“国际局势 + 深度讲透”，一轮 discover 里可能会发生下面这条链：

1. `SearchStrategy` 先用画像摘要生成 query，如“国际局势 因果链”“中东局势 深度解析”。
2. `TrendingStrategy` 根据画像挑出更可能相关的榜单分区 rid。
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
- **TrendingStrategy**：先抓全站榜，再挑新闻、知识、纪录片相关分区，对榜单内容逐条做画像相关性评估，把“热点里真正对味”的内容留下。
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

`style_key` 不是题材，而是内容风格信号。当前文档和代码里常见的有：

- `deep_dive`：硬核解析、原理讲透、理论拆解
- `story_doc`：纪录片、故事化讲述、过程复盘
- `news_brief`：快讯、局势更新、热点锐评
- `practical_guide`：教程、入门、指南

这个字段的作用，是让下游推荐层能避免一整批都变成同一种表达密度和观看体感。

## 为什么要多策略并存

四类策略并不是互相替代，而是在解决不同的供给问题：

- **Search** 最擅长把明确兴趣翻译成可搜索的 query，命中快，解释性也强。
- **Trending** 负责从大盘热点里筛出“虽然很热，但也确实适合这个人”的内容。
- **RelatedChain** 擅长沿着已有高价值种子往下深挖，常常能找到更贴的相邻内容。
- **Explore** 则负责防止系统越来越窄，只会重复喂同一类题材。

如果只有搜索，系统会偏保守；如果只有探索，系统又容易飘。多策略并存的价值，就是在“稳定命中”和“适度意外”之间维持平衡。

## 已实现功能

| 任务 | 状态 | 说明 |
|------|------|------|
| 5.1 搜索策略 | ✅ | LLM 生成搜索词 + B 站搜索 + `bvid` 去重 + `DiscoveredContent` 映射 |
| 5.2 排行榜策略 | ✅ | 全站榜 + 相关分区榜 + LLM 评分筛选 |
| 5.3 相关推荐链策略 | ✅ | 事件种子 + 偏好/策略兜底种子 + 2 层相关推荐链 + LLM 评分过滤 |
| 5.4 跨领域探索策略 | ✅ | 远域探索领域生成 + query 搜索 + exploration bonus + prompt 级外推多样性约束 |
| 5.5 内容评估 | ✅ | `evaluate_content()` 已被四类发现策略复用（含 SearchStrategy） |
| 5.6 发现引擎编排 | ✅ | 并发执行策略 + 高分去重 + SQLite 缓存写入 |
| M120 多事件循环并发控制修复 | ✅ | `DiscoveryConcurrencyController` 现在会按当前 event loop 重新绑定 semaphore，CLI `init` 的分阶段补货不会再在第二轮触发跨 loop `RuntimeError` |
| 候选供给升级 | ✅ | 主发现不足时触发 backfill，并把相关性 / 候选层级写入缓存 |
| M118 topic_key 与池子层压缩 | ✅ | Search / Related 现在会给候选带稳定 `topic_key`，发现引擎会先压缩同 topic 重复项，再写入 discovery pool |
| M119 style_key 风格标注 | ✅ | discovery 入池时会按标题/描述轻规则补 `style_key`，为推荐层的风格多样性约束提供稳定信号 |
| M120 候选池来源交错取样 | ✅ | `get_pool_candidates()` 现在会按 `search / trending / related_chain / explore` 交错取样，避免候选窗口被单一来源刷满 |
| M122 来源优先补齐与风格误判修正 | ✅ | 池子压缩时会优先保留不同 `source` 的候选，再限制重复 `style`；同时补强 `style_key` 规则，减少硬内容误判成 `light_chat` |
| M123 按平台缺口补池子 | ✅ | runtime 在补货时会先按 `[scheduler.pool_source_shares]` 统计平台族余量；默认保存的 B 站 / 小红书 / 抖音 / YouTube share = 8 / 1 / 1 / 1，但默认只有 B 站启用，disabled 平台会从有效配比中剔除。B 站缺口会合并四个策略到一次 discover()，再用 `strategy_limits` 把同一个平台缺口分摊给各策略；小红书 / 抖音缺口分别交给对应 producer；YouTube 缺口交给 `YoutubeDiscoveryProducer` 独立 loop，按每日执行 ledger 调用 `yt_search` / `yt_trending` / `yt_channel`；超配额平台族会被压回目标内 |
| runtime 调度参数配置 | ✅ | 后台 discovery 不使用 `discovery_cron`；`ContinuousRefreshController` 从 `[scheduler]` 读取 `refresh_check_interval_seconds`、`signal_event_threshold`、`trending_refresh_hours`、`explore_refresh_hours`、`discovery_limit` 和 `proactive_push_interval_seconds`，配置热重载后重建 controller 生效 |
| M124 LLM 评估窗口控费 | ✅ | runtime 按平台自身缺口传递补货 limit；各策略在 LLM 评估前把候选窗口收缩到 `max(6, limit*2)`、上限 90，少量补货时不再把几十条候选送去评分后立刻 suppressed；batch parser 兼容 fenced JSON、回显输入后追加结果、NDJSON object 序列，避免退回 N 次单条评估 |
| v0.3.74 eval-batch JSON 容错统一 | ✅ | `_evaluate_batch` 改用 `llm.json_utils.extract_llm_json_list()`，在原 fenced / echo / JSONL 基础上统一兼容 `results/items/data/output/scores/evaluations` wrapper、MiMo malformed `{ [ ... ] }` 数组包裹和 schema echo 后最终结果；解析失败仍按原有降级路径处理，不把示例 JSON 当作真实评分 |
| v0.3.81 eval-batch 按内容 ID 绑定 | ✅ | batch 内容评估 prompt 会携带 `bvid/content_id`，解析时优先按返回 ID 写回 `score/reason/topic/style/franchise`。provider 乱序或漏项时，不再把后一条候选的 `relevance_reason` 写到前一条；无 ID 且数量不完整时降级逐条评估 |
| v0.3.x eval-batch 限流保护 | ✅ | batch LLM 调用若失败原因为 provider rate limit / cooldown / quota，不再降级到逐条 `evaluate_content()`；本批候选返回 0 分并等待下一轮补货重试，避免一次 Gemini 429 放大成整批 traceback |
| M126 explore 高风险子簇压缩 | ✅ | refresh 结束后会温和压一轮 `explore` 内部的高风险相邻簇，例如制造 / 工艺 / 材料、博弈 / 桌游 / 机制，避免单簇继续堆满 fresh pool |
| v0.3.0 trending 按 rid 交错 | ✅ | `TrendingStrategy` 拉 5 个分区排行榜后做 round-robin 交错再送 LLM 评估，避免下游 30 条 hard-cap 把 rid=0/36 的顶部全吃掉 |
| v0.3.0 explore 按 domain 交错 | ✅ | `ExploreStrategy` 同模式：按 `domain_label` round-robin 后再送评估 |
| v0.3.0 跨源跨轮 topic_group 配额 | ✅ | `Database.trim_topic_group_overflow(max_per_group)` 每 refresh tick 都跑，把任意 topic_group 在 fresh pool 占比压在 ~10%；不依赖 source，泛化了 explore-only 的 cluster cap |
| v0.3.0 deficit-source 合并并行 | ✅ | `_build_source_replenishment_plan` 把 B 站平台缺口合并到一次 `discover()` 并行 fan-out，单轮多策略混排，告别"每轮一种 source"的 60s 串行 |
| v0.3.0 share-aware trim_pool | ✅ | `trim_pool_to_target_count(source_share_quotas=...)` 用三段桶（protected / negotiable_untracked / negotiable_tracked），保证 under-quota 源不会被 score-only 修剪误伤 |
| v0.3.0 suppressed 重发现复活 | ✅ | `cache_content` UPSERT 时把 `pool_status='suppressed'` 自动复位为 `'fresh'`；slow-churning 源（trending）从此不再被旧 trim 决定终生淘汰 |
| v0.3.69 平台级来源配比 | ✅ | `_SOURCE_TARGET_SHARES` 硬编码策略配比改为配置项 `[scheduler.pool_source_shares]`；`source_policy` 会按 `[sources.xiaohongshu]` / `[sources.douyin]` / `[sources.youtube]` 的 `enabled` 生成有效配比，避免关闭源占 quota；配置页和 init 都可更新开关与比例 |
| Pool distribution snapshot | ✅ | `build_pool_distribution_snapshot()` 汇总候选池总量、平台缺口、饱和 topic/style/franchise，为后续 pool-aware discovery prompt 和 rerank 提供轻量输入 |
| v0.3.1 trim_topic_group 每 tick 触发 | ✅ | 修复"trim 只在 discover 之后跑"的盲点：`_enforce_pool_cap` 路径上每 tick 都调一次，避免 pool 满 cap 时 topic 配额永远不收敛 |
| v0.3.31 小红书来源族均衡 | ✅ | `xhs-extension-task/search/profile` 等 raw source 归并为 `xiaohongshu` 平台族参与配额，满池时会从 suppressed 高分小红书候选中复活 under-quota 库存，再按统一 cap trim 让出空间 |
| v0.3.67-0.3.69 抖音 discovery 策略边界 | ✅ | `DouyinDiscoveryService` 现在封装 search / hot / feed 三个公开来源的统一策略边界，Cookie 从环境变量覆盖或扩展同步文件解析；`discover --source douyin` 走缓存路径，`discover-douyin` 可指定关键词、子来源并用 `--no-cache --no-evaluate` 调试；作者主页 `creator` 不再作为默认公开渠道 |
| v0.3.68 抖音插件签名 search discovery | ✅ | `search-douyin` 入队 `dy_tasks(type="search")`，扩展在登录浏览器后台 tab 中用页面 acrawler 签名搜索 API 并回传 `dy_search` 候选；正式 `search` 子来源现在优先复用这条链路，以 `dy-plugin-search` 进入 discovery，不传播为画像事件 |
| v0.3.68 抖音插件 hot-related discovery | ✅ | `hot` 子来源先取 hot board 的 `sentence_id`，再入队 `dy_tasks(type="hot")`；扩展后台打开 `/hot/{sentence_id}` 并签名 related API 回传 `dy_hot` 候选，正式以 `dy-plugin-hot-related` 进入 discovery |
| v0.3.69 抖音插件首页 feed discovery | ✅ | `feed` 子来源入队 `dy_tasks(type="feed")`，扩展在后台登录首页签名 `/aweme/v1/web/tab/feed/` 并回传 `dy_feed` 候选，正式以 `dy-plugin-feed` 进入 discovery；CLI 公开来源收敛为 `search` / `hot` / `feed` |
| v0.3.69 抖音 runtime search 防重复 | ✅ | discovery engine 注册同名 strategy 时替换旧实例，避免 `douyin_direct` 在长期后台运行中累积成多个同名策略并重复创建 search 任务；扩展 search 任务单关键词 timeout 放宽到 120s，覆盖页面跳转与 acrawler 签名耗时 |
| v0.3.x discovery 画像上下文补齐 | ✅ | `build_profile_summary()` 会把 `disliked_topics`、认知风格、价值观、内在驱动力、当前阶段、life stage、MBTI、来源平台分布、近期觉察、当前洞察、质量敏感度和兴趣来源时间一起带入 discovery profile summary，让 search / trending / explore / YouTube query 生成和 batch 内容评估都能看到更完整的画像上下文 |
| SearchStrategy LLM 评估 | ✅ | `SearchStrategy` 现在默认走 `evaluate_content()` LLM 打分（`llm_evaluation=True`），不再只用本地启发式（上限 0.62），可通过 `llm_evaluation=False` 关闭 |
| 策略中间产物捕获 | ✅ | 4 个策略均支持 `last_intermediates` 属性，运行后可查看生成的搜索词、选择的分区、种子列表、探索域等中间产物 |
| Discovery 评估框架 | ✅ | `DiscoveryEvaluator` 支持 7 维质量评估（relevance / diversity / specificity / query_quality / explanation_quality / novelty / no_echo_chamber），含自动和人工两种模式 |
| Discovery 模拟场景 | ✅ | `ScenarioGenerator` + `MockBilibiliClient` + `MockMemoryManager` 可离线生成模拟 B 站内容宇宙用于评估，无需真实 API |
| Discovery 评估类型边界 | ✅ | v0.3.71 起 eval scenario / evaluator 对 LLM JSON、缓存 persona、人工反馈和 ranking pool 做显式类型守卫，`mypy strict` 可覆盖评估链路而不依赖真实 Claude / Playwright / aiohttp 安装 |
| Discovery 自动优化循环 | ✅ | SGD 风格优化循环：生成 persona → 生成 scenario → 运行发现 → 多维评估 → exploit/explore → accept/rollback |
| Discovery 人工评估脚本 | ✅ | 交互式人工评估 + 可选触发优化 |

## 公开 API

### build_profile_summary

```python
from openbiliclaw.discovery.strategies._utils import build_profile_summary

profile_summary = build_profile_summary(profile)
```

行为说明：

- 这是 discovery 各策略共享的画像摘要入口，用来把 `SoulProfile` / `OnionProfile` 压成可序列化、可注入 prompt 的 dict。
- 摘要会保留一级兴趣 `interest_domains` 和扁平兴趣 `interests`，并带上 `first_seen` / `last_seen` / `source`，让搜索词生成和内容评估能区分长期稳定兴趣、近期新增兴趣和推断来源。
- 摘要会带入 `disliked_topics[:8]`；这些是长期避雷项，和 batch evaluator 的短期 `negative_examples` 互补。
- 摘要会带入人格与决策上下文：`core_traits`、`cognitive_style`、`values`、`motivational_drivers`、`deep_needs`、`current_phase`、`life_stage`、`mbti`、`recent_awareness`、`active_insights`。
- 摘要会带入消费上下文：`style`（含 `quality_sensitivity`）、`context`、`exploration_openness`、`favorite_up_users`、`source_platform_mix` 和 `_active_speculations`。
- 摘要是 discovery 的只读输入，不会修改 profile；字段数量按 prompt 需要裁剪到前若干项，避免把整份画像无界塞进 LLM。

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
- discovery 的受控并发 controller 会按当前 `asyncio` event loop 重新创建内部 semaphore，适配 CLI 里多次 `asyncio.run(...)` 的分阶段调用
- `discover(..., strategy_limits={...})` 可让调用方限制每个 strategy 的单独拉取量；最终 `limit` 仍控制合并后的返回 / 缓存数量，`strategy_limits` 只负责避免 grouped refresh 把同一个平台缺口放大到每个策略
- `discover(..., pool_snapshot=...)` 可接收可选的 `PoolDistributionSnapshot`；引擎只会把它传给签名兼容的 primary strategy 和 backfill strategy，保留旧版 `discover(profile, limit=...)` 签名不变。
- 同一 `bvid` 若被多个策略命中，保留 `relevance_score` 更高的版本
- 主候选少于目标数量时，会依次尝试策略 backfill 和历史缓存 backfill；策略 backfill 同样会收到兼容转发的 `pool_snapshot`
- 当调用方只需要少量候选时，策略会先把送入 LLM 评估的候选窗口压到 `max(6, limit*2)`，仍保留过采样缓冲，但不再用固定 90 条窗口浪费评估调用
- batch 评估结果解析会优先选择包含 `score` 的结果数组或 object 序列；如果 provider 回显输入 JSON、包 Markdown fence、或返回 NDJSON，仍按一次 batch 处理，不再拆成 N 次单条评估
- batch prompt 和响应都带 `bvid/content_id`；只要响应里有可识别 ID，引擎会按 ID 而不是数组下标写回评分和理由。没有 ID 且结果数量不完整时会回退到单条评估，避免 LLM 漏项导致后续候选整体错位
- 如果 batch 调用失败被识别为 LLM provider 限流或 cooldown，本轮不会再触发逐条 fallback；这些候选按 0 分处理，下一轮 refresh 在 provider 恢复后重新发现 / 评估
- `SearchStrategy` / `TrendingStrategy` / `RelatedChainStrategy` / `ExploreStrategy`、YouTube 三策略和 `DouyinDirectStrategy` 在内部临时构造 evaluator 时都会透传 `database`。因此 CLI、daemon runtime、YouTube producer、Douyin producer 和 OpenClaw bootstrap 路径都能读取同一份近期 negative exemplars，避免只有外层 engine 能看到短期负反馈样本。
- 排序口径优先 `candidate_tier`，再看 `relevance_score`、`last_scored_at`、`view_count`
- 最终结果会把 `relevance_score`、`relevance_reason`、`candidate_tier` 一并写入 `content_cache`

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
        discovery_engine=engine,  # 传入时会注册 douyin_direct 并写 content_cache
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

- `cache=True` 且传入 `discovery_engine` 时，服务会注册 `DouyinDirectStrategy`，再通过 `ContentDiscoveryEngine.discover(..., strategies=["douyin_direct"])` 走统一评估、压缩和缓存写入。注册按 strategy name 替换旧实例，所以长期 runtime 每轮只会保留一个 `douyin_direct`，不会把同一个关键词重复入队成多个 search 任务。
- `cache=False` 时服务会直接执行 `DouyinDirectStrategy.discover()`，适合 CLI smoke、源接口排查和未来 API 预览，不会写入 `content_cache`。
- `sources` 公开支持 `search`、`hot`、`feed`；CLI 中 `search` 会优先走后台插件签名链路并标记为 `dy-plugin-search`，`hot` 会优先走后台插件 hot-related 链路并标记为 `dy-plugin-hot-related`，`feed` 会走后台首页推荐流插件签名链路并标记为 `dy-plugin-feed`。hot 插件任务会带总目标数，dispatcher 累计达到目标后直接 finalise；小批量请求只展开少量 hot seed，降低 `/hot/{sentence_id}` 串行跳转导致的超时概率。插件任务空 / 超时 / 失败时 search / hot 会再回退 direct-cookie search / hot，feed 仅保留 direct-cookie 诊断 fallback。插件 discovery 入队前会清理超过等待窗口的 search / hot / feed pending 任务，避免 daemon 重启或旧版本重复入队后，新任务被陈旧队列阻塞；这些清理出来的 `failed/stale_pending` 不计入每日任务预算。
- runtime `DouyinDiscoveryProducer` 每轮把 `keywords_per_run` 收窄到 1，并按当前抖音缺口动态选子来源：缺口很小时只跑 feed，较小缺口优先 hot 再 feed，缺口较大只跑 search / hot，把可用预算留给更能补池的两个来源，避免大缺口仍被低产出的 feed 拖住。runtime 构造插件客户端时还会按本轮抖音缺口动态抬高 hot 任务预算（最多 60）；CLI smoke / 手动 discovery 仍可通过 `sources` / `keywords` 显式控制搜索面。扩展侧单关键词 search timeout 和后端默认等待窗口均为 180 秒，给首页打开、搜索页跳转、MAIN-world 签名 API 和 DOM 兜底解析留足窗口。
- `DouyinDirectClient.get_hot_terms()` 会从 hot board 抽取 `sentence_id` 给插件 hot 任务使用；`get_hot_board()` 只作为 direct-cookie fallback，只有响应内直接携带 aweme 时才会产出视频。
- CLI 创建 `DouyinDirectClient` 前会先读 `OPENBILICLAW_DOUYIN_COOKIE`（或 `cookie_env` 指向的变量），再回退到扩展同步的 `data/douyin_cookie.json`；后者由 `/api/sources/dy/cookie` 写入，不镜像到 `config.toml`。
- `DouyinDirectClient` 对单次 HTTP 连接异常采用软失败：记录日志并返回空结果，让 CLI 输出本轮 0 条而不是 traceback；Cookie 或接口有效性仍以 smoke 结果为准。

### PoolDistributionSnapshot

```python
from openbiliclaw.discovery.pool_snapshot import (
    PoolDistributionSnapshot,
    build_pool_distribution_snapshot,
)

snapshot = build_pool_distribution_snapshot(
    database,
    pool_target_count=300,
    source_targets={"bilibili": 240, "xiaohongshu": 30, "douyin": 30},
)
hints = snapshot.to_prompt_hints()
```

行为说明：

- `PoolDistributionSnapshot` 是冻结 dataclass，记录 `pool_target_count`、`pool_available_count`、各平台族目标数量 / 当前数量 / 缺口，以及已饱和的 `topic_group`、`style_key`、`franchise_key`。
- 默认饱和阈值按池目标数换算：topic 为 `max(8, pool_target_count // 20)`，style 为 `max(12, pool_target_count // 8)`，franchise 固定为 10；以默认 `pool_target_count=300` 为例，topic 15 条、style 37 条、franchise 10 条即进入软避让。
- `source_deficits` 只表示平台 / 来源族缺口，例如 `bilibili`、`xiaohongshu`、`douyin`、`youtube` 距离目标配比还差多少；它和内容轴分开处理，不会被解释成“应该搜索某个平台名”。
- `to_prompt_hints()` 输出面向后续 prompt 的轻量 dict：`avoid_topics`、`avoid_styles`、`avoid_franchises`、`prefer_axes` 和 `source_deficits`。其中 `avoid_*`、`prefer_axes` 都是软信号，只影响 query 生成和引擎层软重排，不是硬过滤条件。
- 当前 runtime 构建的 snapshot 不会把平台缺口自动合成内容 `prefer_axes`；`undercovered_axes` / `prefer_axes` 保留给手动传入或未来更细的内容轴缺口判断。
- 统计口径复用候选池可见性：只看 fresh、非 dislike、未推荐、已预生成 pool copy 且可打开的候选。
- runtime refresh 会在每次 B 站 discovery 前构建 snapshot，并通过 `ContentDiscoveryEngine.discover(..., pool_snapshot=...)` 传入；构建失败只记录日志，不阻塞补货。
- 引擎层会在最终压缩前应用 snapshot 软重排：饱和 topic/style/franchise 分别轻微降权，显式 undercovered topic 轻微加权，强相关候选仍保留优先级，且调整分只用于本轮排序，不会持久化覆盖 `relevance_score`。

### Runtime pool source balance

```python
source_targets = controller._source_target_counts()
# 默认有效 [scheduler.pool_source_shares] = 8 且 pool_target=600 时：
# {
#     "bilibili": 600,
# }
# 如果显式启用 XHS / Douyin / YouTube，对应平台会按保存的 share 获得
# 独立 target，并由各自 producer 或 strategy 补池。

database.reactivate_under_quota_pool_sources(
    target=600,
    source_share_quotas=source_targets,
)
database.trim_pool_source_overflow(
    source_share_quotas=source_targets,
)
database.trim_pool_to_target_count(
    target=600,
    source_share_quotas=source_targets,
)
distribution_counts = database.get_pool_distribution_counts()
```

行为说明：

- 配额单位是“平台族”，不是 raw `content_cache.source`。B 站的 `search` / `related_chain` / `trending` / `explore` 统一计入 `bilibili`；小红书的 `xhs-extension-*` 统一计入 `xiaohongshu`；抖音的 `dy-plugin-*` / `douyin*` 统一计入 `douyin`。
- B 站缺口仍由 `ContentDiscoveryEngine.discover()` 的四个策略补齐；小红书缺口由 `XhsTaskProducer` / 浏览器插件任务链补齐；抖音缺口由 runtime `DouyinDiscoveryProducer` 调用 `DouyinDiscoveryService(cache=True)`，小缺口用 feed / hot 快速补零散名额，大缺口优先 search / hot 后台插件签名链路补池。
- 如果池子已满但 `xiaohongshu` 或 `douyin` 低于配额，`reactivate_under_quota_pool_sources()` 会优先从 `pool_status='suppressed'` 且可打开的高分小平台候选中复活一批；如果某个平台族超过配额，`trim_pool_source_overflow()` 会先把该族压回目标内，即使总池子还没有达到 `pool_target_count`。
- `trim_pool_to_target_count()` 继续负责总量硬上限：单轮 discovery 超过总池子目标时，会在平台配额保护后把总量裁回 `pool_target_count`。
- B 站补货 limit 使用 `bilibili` 平台自身缺口，而不是“总池子缺口”；例如总池子缺 57 条但 B 站只缺 5 条时，本轮 B 站 discovery 总目标只请求 5 条，并分摊为 `search=2, related_chain=1, trending=1, explore=1`，避免四个策略各自按 5 条去过采样和 LLM 评估。
- 手动 refresh 也走同一套平台缺口计划：如果 B 站已经达到平台配额，而缺口属于小红书或抖音，手动刷新不会再强行跑 B 站 discovery 后又被 source cap 立刻 suppressed。
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
    llm_evaluation=True,      # 默认开启 LLM 评估
    score_threshold=0.65,      # 评分阈值
)

items = await strategy.discover(profile, limit=20)
items = await strategy.discover(profile, limit=20, pool_snapshot=snapshot)

# 运行后可取中间产物
queries = strategy.last_intermediates.get("queries", [])
```

行为说明：

- 优先通过 `LLMService.complete_structured_task()` 生成 5 到 10 个 B 站搜索词
- 如果传入 `pool_snapshot`，会把 `to_prompt_hints()` 写入 query prompt，引导模型软避让已拥挤的 topic/style/franchise，并携带独立的 `source_deficits` 平台缺口信号；运行时快照暂不把平台名转成内容 `prefer_axes`
- `pool_snapshot` 只是可选上下文：hint 构建失败、返回非 dict 或 hint 无法序列化时会丢弃这段上下文，继续走正常 LLM query 生成，不会直接退回本地 fallback query
- LLM 返回坏 JSON 或空结果时，回退到本地兴趣标签 query
- 正常模式默认抓每个 query 的第一页；backfill 变体会放大 query 数和页数
- B 站搜索会使用独立 API client 执行，避免和其他策略共享同一请求 session；如果运行时存在有效 B 站 Cookie，独立 client 会继承该 Cookie，因为当前匿名 WBI search 容易直接返回 `v_voucher` 挑战而不给 `result`
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
    score_threshold=0.65,
    max_related_rids=4,
)

items = await strategy.discover(profile, limit=20)
```

行为说明：

- 固定拉取 `rid=0` 全站榜
- 再通过 LLM 选择 3 到 5 个相关分区榜
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

- 优先从事件层的 `view` / `favorite` / `like` 视频中挑选种子
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
    score_threshold=0.65,
)

items = await strategy.discover(profile, limit=20)
```

行为说明：

- 先让 LLM 推断 3 到 5 个“高相关但有陌生感”的远域探索方向
- 每个方向必须附 `why_it_might_resonate`、`novelty_level` 和 1 到 2 个 B 站搜索 query
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
)
```

当前 discovery 结果写入缓存时会稳定填充的字段包括：

- `bvid`
- `title`
- `up_name`
- `up_mid`
- `cover_url`
- `duration`
- `view_count`
- `description`
- `source_strategy`
- `relevance_score`
- `relevance_reason`
- `topic_key`
- `style_key`
- `candidate_tier`
- `discovered_at`
- `last_scored_at`

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
| **SearchStrategy** | 通过 `build_profile_summary()` 消费 `personality_portrait`, `core_traits[:5]`, `cognitive_style[:5]`, `values[:5]`, `motivational_drivers[:5]`, `current_phase`, `life_stage`, `interest_domains[:8]`, `interests[:10]`, `favorite_up_users[:5]`, `disliked_topics[:8]`, `deep_needs[:5]`, `style`, `context`, `exploration_openness`, `source_platform_mix`, `mbti`, `recent_awareness[:5]`, `active_insights[:5]`, `_active_speculations[:5]` | 生成搜索词、计算兴趣锚定分，并避开长期雷点 |
| **TrendingStrategy** | 同上（通过 `build_profile_summary()`） | 选择排行榜分区、评估内容相关性 |
| **RelatedChainStrategy** | `interests[:2]`, `favorite_up_users[:1]` + 全画像用于评估 | 生成偏好种子、评估相关链内容 |
| **ExploreStrategy** | 同 Search + **`exploration_openness`**（关键） | 生成跨域方向、计算探索 bonus |

**协议约定**：
- Discovery 只读取画像，不修改画像
- 画像由 `soul/` 模块维护，discovery 不关心画像是如何构建或更新的
- 如果画像为空或缺少关键字段，策略会 fallback 到默认行为（空兴趣列表、默认分区等）

### 输出：给 Recommendation 模块提供什么

Discovery 的产出是 `list[DiscoveredContent]`，写入 SQLite `content_cache` 后供推荐层消费。

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
    "trending.rid_selection":          "trending_rids_prompt",
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
2. **query 生成走结构化任务**：统一通过 `LLMService` 注入 core memory，避免各策略手拼画像上下文
3. **坏 JSON 有本地 fallback**：保证搜索策略在 LLM 不稳定时仍可运行
4. **排行榜分区先做轻量选择**：固定 `rid=0`，其余分区由 LLM 结构化选择并保留默认 fallback
5. **相关推荐链优先复用真实行为**：种子优先来自近期事件，其次才是偏好补种子和策略兜底
6. **跨领域探索强调“可解释的陌生感”**：不是越远越好，而是“主题陌生，但心理需求上说得通”
7. **评分入口集中在引擎层**：`ContentDiscoveryEngine.evaluate_content()` 统一负责把 `score/reason` 写回 `DiscoveredContent`
8. **发现引擎承担最终收口职责**：策略负责找内容，引擎负责并发调度、去重排序、分层补货和缓存写入
9. **引擎层仍不负责依赖创建**：`ContentDiscoveryEngine` 接收外部注入的 `llm_service` / `database`，策略继续显式注入 client/service
10. **补货是显式分层而不是无脑放宽**：主发现优先，backfill 只在候选不足时介入，并通过 `candidate_tier` 保留来源语义
11. **池子层先做一次轻压缩**：topic 多样性不能只在推荐层补救，发现结果在写入 `content_cache` 前也会先压一轮同 topic 重复项，防止单一 seed chain 灌满候选池
12. **风格信号先在入池时做轻标注**：`style_key` 不追求完美分类，但必须足够稳定，保证推荐层能区分“硬核解析 / 新闻快讯 / 故事纪录 / 游戏攻略”等内容风格
13. **候选窗口本身也要按来源打散**：如果 `get_pool_candidates()` 的前 30 条几乎全是 `explore`，下游再怎么多样化都很难救；因此 discovery pool 读取阶段也会做来源交错取样
14. **来源补齐优先级高于风格上限**：在 discovery 压缩时，新的 `search / trending / related_chain` 候选应优先获得一个坑位，不能先被重复的 `style_key` 卡死
15. **`style_key` 规则宁可偏粗，也不能把硬内容全掉进 `light_chat`**：芯片、显微镜、理论、哲学这类更适合 `deep_dive`；全过程、制造过程、工艺难度更适合 `story_doc`
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
