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
| 9.1 反馈处理 | ✅ | CLI、本地 API 与插件 popup 已统一写回推荐反馈与 `feedback` 事件 |
| 9.2 画像更新 | ✅ | 反馈累计到阈值后会自动触发偏好层重分析与画像重建 |

## 公开 API

### RecommendationEngine

```python
from openbiliclaw.recommendation.engine import RecommendationEngine

engine = RecommendationEngine(llm=llm, database=db)
items = await engine.generate_recommendations(
    discovered=None,
    profile=profile,
    limit=5,
)
```

行为说明：

- 若传入 `discovered`，优先对该批内容排序
- 若未传入 `discovered`，从 `content_cache` 中读取未推荐内容
- 排序主键是 `relevance_score`，其次是 `view_count`
- 生成结果后会写入 `recommendations` 表，避免下次重复选中
- 每条推荐都会调用 `generate_expression()` 生成 `expression` 和 `topic_label`
- CLI 展示后会把对应推荐记录标记为 `presented = 1`
- `feedback` 命令会把 `feedback_type` / `feedback_note` / `feedback_at` 写回推荐记录

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

## 设计决策

1. **先做排序闭环，再做表达生成**：先确保“选谁”稳定，再讨论“怎么说”
2. **推荐历史在选中时写入**：避免相邻批次重复选择同一内容
3. **表达生成单独落库**：排序和表达拆开，便于失败时降级到 fallback 文案
4. **`presented` 在 CLI 展示后更新**：区分“系统选中”和“用户已经看见”
5. **反馈保留当前状态**：v0.1 只保存当前反馈结果，不额外引入 feedback 历史表
6. **三端走同一反馈语义**：CLI、API 和 popup 都只写入当前反馈状态，并同步追加 `feedback` 事件
7. **反馈驱动学习延迟触发**：推荐反馈不会逐条立刻重写画像，而是累计到阈值后统一重分析，降低噪声
