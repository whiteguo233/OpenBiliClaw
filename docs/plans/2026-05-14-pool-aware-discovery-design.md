# Pool-Aware Discovery Design

## Goal

让 discovery 在补货时同时考虑用户画像和当前推荐池分布，先做一个轻量闭环：减少重复补货和事后 suppress，不重写推荐排序，不引入强制 topic 配比。

## Problem

当前系统已经有多层池子保护：

- runtime 会按平台族配额维护 B 站 / 小红书 / 抖音比例。
- `ExploreStrategy` 会读取已覆盖的 `topic_group`，避免继续探索拥挤方向。
- `ContentDiscoveryEngine` 会在单轮结果内压缩 topic / style / source 重复。
- DB 会在 refresh tick 中 suppress 超过 topic/source/franchise 上限的候选。

问题在于这些控制大多发生在 discovery 之后。`SearchStrategy`、`TrendingStrategy`、`RelatedChainStrategy` 仍主要围绕用户画像找内容，不知道当前池子已经在哪些 topic/style/franchise 上饱和。结果是系统会先花搜索和 LLM 成本拿到一批同质候选，再由后置 trim/suppress 丢掉。

## Recommended Approach

新增一个 `PoolDistributionSnapshot`，作为 discovery 的软信号。它描述当前 fresh pool 的结构，而不是决定推荐出口排序。

第一版只做三件事：

1. runtime 在触发 discovery 前构建 snapshot，并传给 `ContentDiscoveryEngine.discover()`。
2. `SearchStrategy` 和 `ExploreStrategy` 在 query/domain prompt 中使用 snapshot，绕开饱和方向，优先补画像允许的欠覆盖轴。
3. `ContentDiscoveryEngine` 在合并排序后、缓存前，用 snapshot 对明显拥挤 topic/style/franchise 做软惩罚，让欠覆盖方向更容易入池。

## Data Model

新增模块：`src/openbiliclaw/discovery/pool_snapshot.py`。

核心结构：

```python
@dataclass(frozen=True)
class PoolDistributionSnapshot:
    pool_target_count: int
    pool_available_count: int
    source_targets: dict[str, int]
    source_counts: dict[str, int]
    source_deficits: dict[str, int]
    saturated_topics: tuple[str, ...] = ()
    saturated_styles: tuple[str, ...] = ()
    saturated_franchises: tuple[str, ...] = ()
    undercovered_axes: tuple[str, ...] = ()

    def to_prompt_hints(self) -> dict[str, object]: ...
```

第一版的 `undercovered_axes` 是软字段：可以先由 prompt 根据画像和 saturated topics 生成，也可以为空。为空时系统只避免重复，不强行发明新方向。

## Snapshot Sources

`Database` 新增一个轻量统计接口，复用 `content_cache`：

- `pool_available_count`: 当前可推荐 fresh pool 数量。
- `source_counts`: 平台族计数，复用 `count_pool_candidates_by_source()`。
- `topic_group_counts`: fresh、未推荐、非 dislike、非空 `topic_group` 的 top N。
- `style_counts`: fresh、未推荐、非 dislike、非空 `style_key` 的 top N。
- `franchise_counts`: fresh、未推荐、非 dislike、非空 `franchise_key` 的 top N。

饱和判断保持保守：

- `topic_group`: 数量超过 `max(8, pool_target_count // 20)`，或在 top N 中明显高于中位数。
- `style_key`: 数量超过 `max(12, pool_target_count // 8)`。
- `franchise_key`: 沿用现有 pool-wide franchise quota 附近的阈值。

这些阈值只影响软惩罚和 prompt hints，不直接 suppress。

## Data Flow

```text
ContinuousRefreshController
  -> build_pool_distribution_snapshot()
  -> discovery_engine.discover(..., pool_snapshot=snapshot)

ContentDiscoveryEngine
  -> passes snapshot to strategies that accept it
  -> merge / normalize / compress current results
  -> apply pool-aware soft rerank
  -> cache results

SearchStrategy
  -> build_search_queries_prompt(profile_summary, pool_hints)

ExploreStrategy
  -> keep existing covered_topic_groups
  -> also accept pool_hints for saturated_styles/franchises later

RecommendationEngine
  -> unchanged
```

## Behavior Example

当前池子：

```text
B站: 480/480
小红书: 40/60
抖音: 25/60

topic_group:
AI 编程: 78
原神: 42
汽车改装: 36
人物纪录: 8
审美体验: 6

style_key:
practical_guide: high
deep_dive: high
story_doc: low
visual_showcase: low
```

旧行为会继续生成 `AI Agent 教程`、`大模型 原理`、`原神 攻略`，再由后置 cap 丢掉一部分。

新行为会把 prompt hints 传给 search：

```json
{
  "avoid_topics": ["AI 编程", "原神", "汽车改装"],
  "avoid_styles": ["practical_guide", "deep_dive"],
  "prefer_axes": ["人物纪录", "审美体验", "生活方式"]
}
```

query 更可能变成：

```text
独立开发者 日常 vlog
城市空间 改造 纪录片
设计师 工作流 观察
科技产品 审美体验
```

如果本轮仍发现高分 `AI 编程` 内容，它不会被硬删，只会被降一点排序权重。足够高分的内容仍然可以进池。

## Error Handling

- snapshot 构建失败时，记录 debug/warning，discovery 继续走旧逻辑。
- strategy 不支持 `pool_snapshot` 参数时，engine 自动按旧签名调用，避免破坏测试 double 和外部 adapter。
- LLM 不返回 undercovered axes 时，不做强制补空白，只使用 saturated hints。
- embedding 不可用时，不做语义近似匹配，只做 exact-normalized label 匹配。

## Testing

新增和修改测试重点：

- snapshot 统计能从 `content_cache` 生成 saturated topics/styles/franchises。
- runtime 调用 discovery 时传递 snapshot，且 snapshot 构建失败不阻塞 refresh。
- `ContentDiscoveryEngine` 能把 snapshot 传给支持参数的 strategy，同时兼容旧 strategy。
- `SearchStrategy` prompt 包含 saturated topics 和 undercovered axes。
- pool-aware rerank 中，饱和 topic 的高分候选会被轻微降权，欠覆盖候选会更容易进入前 N，但极高分饱和候选仍可保留。

## Non-Goals

- 不改变 `RecommendationEngine.serve()` 的推荐排序。
- 不做严格 topic 配额表。
- 不要求所有策略第一版都理解 snapshot。
- 不把低相关内容为了“补空白”强行塞进池子。
