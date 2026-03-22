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
- **ExploreStrategy** — 推断“高相关的远域探索方向”，寻找更有陌生感但仍可解释的内容

## 发现链路怎么工作

一次完整的 discovery，当前可以概括成 6 步：

1. **读取画像**
   discovery 的起点通常是一个 `SoulProfile`。这里面不只是“用户喜欢什么标签”，还包括：
   - 核心兴趣及其权重
   - 喜欢的内容风格和时长倾向
   - 喜欢的 UP 主
   - 深层需求，例如“想把问题看透”“想获得秩序感”
   - `exploration_openness`，也就是系统能不能适当推远一点

   真正进入发现策略时，画像会被压缩成更容易消费的摘要。比如 `SearchStrategy` 会取前几个高权重兴趣、核心特质和 deep needs 来生成 query；`ExploreStrategy` 则会额外看探索开放度，判断这轮适不适合往陌生方向走。

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

   之后引擎会合并所有策略返回的列表，并通过 `_merge_duplicates()` 按 `bvid` 去重。如果同一个视频被多个策略同时找到，不是“谁先回来算谁”，而是保留 `relevance_score` 更高的那个版本。

   这一步的作用，是把“不同来源的原始线索”变成“可以比较的一组候选”。

4. **按相关性和供给层级排序**
   合并完成后，引擎会进入 `_merge_and_rank()` 做第一次统一排序。当前排序不是只看分数，而是先看候选层级，再看内容质量信号：

   - 先保 `candidate_tier == "primary"` 的主发现结果
   - 再看 `relevance_score`
   - 同分附近再参考 `view_count`
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

   这样推荐层在后续 `reshuffle`、`append`、常规推荐排序时，就不必重新跑一遍 discovery，也能直接利用这些结构化信号做多样性控制和快速选片。

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
    "deep_needs": ["建立判断确定性"],
    "interests": [
      {"name": "国际局势", "category": "知识", "weight": 0.92},
      {"name": "历史", "category": "知识", "weight": 0.84}
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
- 同一母题的近义变体只能保留 1 个，避免 `博弈论 / 桌游机制 / 策略模型` 一类方向同时灌进池子
- `why_it_might_resonate` 必须先回到用户的认知需求和信息处理方式，而不是只按题材表面相似来联想

但模型返回后，`ExploreStrategy` 不会无脑全收。它还会继续做过滤：

- 去掉与当前高权重兴趣过于相似的 `domain`
- 清洗 query，去重并裁到上限
- 先搜索这些 query，再把搜到的视频重新送去做内容相关性评估
- 最终把评分和 `novelty_level` 组合成探索后的 `relevance_score`

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
| 5.5 内容评估 | ✅ | `evaluate_content()` 已被四类发现策略复用 |
| 5.6 发现引擎编排 | ✅ | 并发执行策略 + 高分去重 + SQLite 缓存写入 |
| M120 多事件循环并发控制修复 | ✅ | `DiscoveryConcurrencyController` 现在会按当前 event loop 重新绑定 semaphore，CLI `init` 的分阶段补货不会再在第二轮触发跨 loop `RuntimeError` |
| 候选供给升级 | ✅ | 主发现不足时触发 backfill，并把相关性 / 候选层级写入缓存 |
| M118 topic_key 与池子层压缩 | ✅ | Search / Related 现在会给候选带稳定 `topic_key`，发现引擎会先压缩同 topic 重复项，再写入 discovery pool |
| M119 style_key 风格标注 | ✅ | discovery 入池时会按标题/描述轻规则补 `style_key`，为推荐层的风格多样性约束提供稳定信号 |
| M120 候选池来源交错取样 | ✅ | `get_pool_candidates()` 现在会按 `search / trending / related_chain / explore` 交错取样，避免候选窗口被单一来源刷满 |
| M122 来源优先补齐与风格误判修正 | ✅ | 池子压缩时会优先保留不同 `source` 的候选，再限制重复 `style`；同时补强 `style_key` 规则，减少硬内容误判成 `light_chat` |
| M123 按来源缺口补池子 | ✅ | runtime 在补货时会先统计池子里的 `search / related_chain / trending / explore` 余量，再优先补足缺口最大的来源，不再让 `explore` 长期淹没其它来源 |
| M126 explore 高风险子簇压缩 | ✅ | refresh 结束后会温和压一轮 `explore` 内部的高风险相邻簇，例如制造 / 工艺 / 材料、博弈 / 桌游 / 机制，避免单簇继续堆满 fresh pool |

## 公开 API

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
- 同一 `bvid` 若被多个策略命中，保留 `relevance_score` 更高的版本
- 主候选少于目标数量时，会依次尝试策略 backfill 和历史缓存 backfill
- 排序口径优先 `candidate_tier`，再看 `relevance_score`、`last_scored_at`、`view_count`
- 最终结果会把 `relevance_score`、`relevance_reason`、`candidate_tier` 一并写入 `content_cache`

更直白地说，`ContentDiscoveryEngine` 负责最后的“收口”：

- 策略关心“我能找到什么”
- 引擎关心“这些结果如何合并成一个可消费的候选池”

因此真正影响推荐体验稳定性的，往往不是单个策略够不够聪明，而是引擎层的并发、去重、压缩和补货逻辑是否可靠。

### SearchStrategy

```python
from openbiliclaw.discovery.strategies.strategies import SearchStrategy

strategy = SearchStrategy(
    llm_service=service,
    bilibili_client=bilibili_client,
    queries_per_run=8,
    page_size=10,
    max_pages=1,
)

items = await strategy.discover(profile, limit=20)
```

行为说明：

- 优先通过 `LLMService.complete_structured_task()` 生成 5 到 10 个 B 站搜索词
- LLM 返回坏 JSON 或空结果时，回退到本地兴趣标签 query
- 正常模式默认抓每个 query 的第一页；backfill 变体会放大 query 数和页数
- 对多个 query 的搜索结果按 `bvid` 去重
- 将结果映射为 `DiscoveredContent`
- 会把 query 派生的 `topic_key` 一起写入候选，供后续池子压缩和推荐分桶使用

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
- 会过滤掉与当前高权重兴趣过于相似的领域
- 搜索结果统一走 `evaluate_content()`，再叠加 `exploration_bonus`
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
16. **补货要看来源缺口，不只看池子总量**：如果池子总数够了但 `trending` 一直接近 0、`explore` 却超标，体感仍会单一；runtime refresh 现在会优先补足来源缺口，再追总量
17. **`explore` 也要控内部子簇，不只控总量**：即使 `explore` 总数没超标，制造 / 工艺 / 材料、博弈 / 桌游 / 机制这类相邻方向也可能在内部堆成一大簇；refresh 现在会把过量部分温和压到非 `fresh`，避免“可换窗口只剩一个味”
