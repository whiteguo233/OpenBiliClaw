# 5.4 跨领域探索策略设计

## 背景

`SearchStrategy`、`TrendingStrategy`、`RelatedChainStrategy` 已经覆盖“基于已知偏好继续深化”的主路径，但 `OpenBiliClaw` 的目标不只是更准，还要有“发现惊喜”的能力。`5.4` 的价值不在于离当前兴趣越远越好，而在于找到那些**主题上陌生、心理需求上合理**的内容。

## 目标

- 实现一个可运行的 `ExploreStrategy`
- 让策略能从用户画像中推断 3~5 个“高相关的远域探索领域”
- 对搜索到的候选内容同时考虑相关性和探索性
- 输出的内容比 `SearchStrategy` 更有意外感，但不会变成纯噪声

## 设计原则

1. **Serendipity 优先于纯 novelty**：陌生感必须建立在“仍然解释得通”的基础上
2. **探索开放度参与加权**：用户越开放，探索 bonus 越高
3. **避免流行度偏置**：探索 query 不应退化成泛化热词
4. **复用现有评分链路**：`evaluate_content()` 继续作为主相关性评分入口

## 范围

### 包含

- 用 LLM 生成结构化探索领域和 query
- 过滤与当前兴趣过近的领域
- 调用 B 站搜索获取候选
- 计算 `relevance_score + exploration_bonus` 的组合分

### 不包含

- 复杂 taxonomy 或知识图谱
- 动态在线学习用户探索阈值
- 多轮探索会话状态

## 方案

### 1. 领域推断

LLM 输出 JSON：

```json
{
  "domains": [
    {
      "domain": "城市空间与建筑叙事",
      "why_it_might_resonate": "你偏好结构清晰、能从具体对象看见更大系统的内容，这类主题可能满足你的理解欲。",
      "novelty_level": 0.62,
      "queries": ["城市 建筑 纪录片", "空间 设计 深度讲解"]
    }
  ]
}
```

约束：

- 领域数 3~5 个
- 每个领域 1~2 个 query
- `novelty_level` 限制在 `0.4~0.8`
- query 必须是适合 B 站搜索的短语

### 2. 领域过滤

本地过滤规则：

- 如果 `domain` 与现有高权重兴趣明显同义、包含或几乎同类，则丢弃
- 如果 query 过于泛化，如“热门”“推荐”“必看”等，也丢弃
- 保留“主题距离较远，但解释理由能与 `core_traits` / `deep_needs` 对齐”的领域

### 3. 候选评分

- `relevance_score`：继续使用 `ContentDiscoveryEngine.evaluate_content()`
- `exploration_bonus`：由 `novelty_level * exploration_openness` 计算
- 最终分数：

```text
final_score = relevance_score * 0.75 + exploration_bonus * 0.25
```

这是一个保守权重，保证“相关性”仍是主导。

### 4. 失败与降级

- LLM 返回坏 JSON：回退到空结果，而不是伪造探索领域
- 单个 query 搜索失败：继续跑剩余 query
- 某个候选评分失败：按 0 分处理

## 测试策略

- 验证策略会调用 LLM 生成探索领域和 query
- 验证与当前兴趣过近的领域会被过滤
- 验证最终分数会叠加 exploration bonus
- 验证单个 query 失败不会中断整体
- 验证 `ContentDiscoveryEngine` 注册 `ExploreStrategy` 后可直接运行

## 文档更新

- `docs/v0.1-todolist.md`
- `docs/modules/discovery.md`
- `docs/changelog.md`
