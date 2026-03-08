# 内容发现引擎

> 从用户画像出发，在 B 站上主动寻找潜在会喜欢的内容。

## 概述

`discovery/` 包负责把用户的 Soul 画像转换成“可被搜索、可被评估、可被推荐”的候选内容集合。

当前模块包含：

- **ContentDiscoveryEngine** — 发现策略编排器，负责注册、运行、去重和汇总
- **DiscoveredContent** — 统一的候选内容数据结构
- **SearchStrategy** — 基于画像生成搜索词并调用 B 站搜索的策略

后续会在这个模块继续补：

- `TrendingStrategy`
- `RelatedChainStrategy`
- 内容匹配度评估
- 多策略并行编排与缓存写入

## 已实现功能

| 任务 | 状态 | 说明 |
|------|------|------|
| 5.1 搜索策略 | ✅ | LLM 生成搜索词 + B 站搜索 + `bvid` 去重 + `DiscoveredContent` 映射 |
| 5.2 排行榜策略 | 🔲 | 未开始 |
| 5.3 相关推荐链策略 | 🔲 | 未开始 |
| 5.5 内容评估 | 🔲 | 未开始 |
| 5.6 发现引擎编排 | 🔄 | 已有基础编排骨架，待补完整策略和排序 |

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
4. **先跑通发现，不提前评分**：`5.1` 不引入内容匹配度评估，留给 `5.5`
5. **引擎层只做编排**：`ContentDiscoveryEngine` 当前只负责注册、运行、去重和汇总，不负责依赖创建
