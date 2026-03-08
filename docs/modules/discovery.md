# 内容发现引擎

> 从用户画像出发，在 B 站上主动寻找潜在会喜欢的内容。

## 概述

`discovery/` 包负责把用户的 Soul 画像转换成“可被搜索、可被评估、可被推荐”的候选内容集合。

当前模块包含：

- **ContentDiscoveryEngine** — 发现策略编排器，负责注册、运行、去重和汇总
- **DiscoveredContent** — 统一的候选内容数据结构
- **SearchStrategy** — 基于画像生成搜索词并调用 B 站搜索的策略
- **TrendingStrategy** — 从全站榜和相关分区榜中筛选高匹配热点内容
- **RelatedChainStrategy** — 从近期高价值视频种子出发，沿相关推荐链扩展候选内容
- **ExploreStrategy** — 推断“高相关的远域探索方向”，寻找更有陌生感但仍可解释的内容

后续会在这个模块继续补：

- 多策略并行编排与缓存写入

## 已实现功能

| 任务 | 状态 | 说明 |
|------|------|------|
| 5.1 搜索策略 | ✅ | LLM 生成搜索词 + B 站搜索 + `bvid` 去重 + `DiscoveredContent` 映射 |
| 5.2 排行榜策略 | ✅ | 全站榜 + 相关分区榜 + LLM 评分筛选 |
| 5.3 相关推荐链策略 | ✅ | 事件种子 + 偏好/策略兜底种子 + 2 层相关推荐链 + LLM 评分过滤 |
| 5.4 跨领域探索策略 | ✅ | 远域探索领域生成 + query 搜索 + exploration bonus |
| 5.5 内容评估 | 🔄 | `evaluate_content()` 已实现，待更多策略复用 |
| 5.6 发现引擎编排 | 🔄 | 已能运行 Search/Trending/RelatedChain/Explore，待补并行和缓存 |

## 公开 API

### ContentDiscoveryEngine

```python
from openbiliclaw.discovery.engine import ContentDiscoveryEngine
from openbiliclaw.discovery.strategies.strategies import SearchStrategy

engine = ContentDiscoveryEngine()
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

### SearchStrategy

```python
from openbiliclaw.discovery.strategies.strategies import SearchStrategy

strategy = SearchStrategy(
    llm_service=service,
    bilibili_client=bilibili_client,
    queries_per_run=8,
    page_size=10,
)

items = await strategy.discover(profile, limit=20)
```

行为说明：

- 优先通过 `LLMService.complete_structured_task()` 生成 5 到 10 个 B 站搜索词
- LLM 返回坏 JSON 或空结果时，回退到本地兴趣标签 query
- 对多个 query 的搜索结果按 `bvid` 去重
- 将结果映射为 `DiscoveredContent`

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

当前 `5.1` 已稳定填充的字段包括：

- `bvid`
- `title`
- `up_name`
- `up_mid`
- `cover_url`
- `duration`
- `view_count`
- `description`
- `source_strategy`

## 设计决策

1. **策略显式注入依赖**：`SearchStrategy` 不自己构建 LLM 或 API client，便于测试和后续编排
2. **query 生成走结构化任务**：统一通过 `LLMService` 注入 core memory，避免各策略手拼画像上下文
3. **坏 JSON 有本地 fallback**：保证搜索策略在 LLM 不稳定时仍可运行
4. **排行榜分区先做轻量选择**：固定 `rid=0`，其余分区由 LLM 结构化选择并保留默认 fallback
5. **相关推荐链优先复用真实行为**：种子优先来自近期事件，其次才是偏好补种子和策略兜底
6. **跨领域探索强调“可解释的陌生感”**：不是越远越好，而是“主题陌生，但心理需求上说得通”
7. **评分入口集中在引擎层**：`ContentDiscoveryEngine.evaluate_content()` 统一负责把 `score/reason` 写回 `DiscoveredContent`
8. **引擎层仍不负责依赖创建**：`ContentDiscoveryEngine` 接收外部注入的 `llm_service`，策略继续显式注入 client/service
