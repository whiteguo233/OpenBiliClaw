---
created: 2026-06-15
title: 洞察"软作废"通路未接线（疑似死代码）
area: soul
files:
  - src/openbiliclaw/soul/engine.py:627
  - src/openbiliclaw/soul/insight_analyzer.py:76
  - src/openbiliclaw/api/app.py:2434
  - src/openbiliclaw/discovery/strategies/_utils.py:406
  - tests/test_soul_engine.py:308
---

## Problem

`SoulEngine.update_from_feedback`（engine.py:627）实现了对洞察假设（InsightHypothesis）的反馈验证：
- 负反馈（signal ∈ reject/dislike/deny）→ `validated=False` + `confidence` 压到 ≤0.35
- 正反馈（signal ∈ confirm/like/support）→ `validated=True` + `confidence` 抬到 ≥0.75

这是设计中洞察的"软作废"机制（不删假设，只压低置信度，靠 delight 的 `0.5+confidence*0.5` 加权让其近乎失效）。

**但实查发现它没有生效：**
1. 全仓 grep `update_from_feedback` 只有定义（engine.py:627）和单测（tests/test_soul_engine.py:308/328），**没有任何生产调用方**。真实 `/api/feedback` 走的是 `record_immediate_feedback_cognition` + `process_feedback_batch_if_needed`，二者都不碰洞察假设。
2. `validated` 字段不参与任何过滤，只在 app.py:2434（API 输出）和 discovery/_utils.py:406（prompt 上下文）当展示字段。没有任何 `if validated` 排除逻辑。

**后果：** 洞察实际只增不减——`merge_insights`（insight_analyzer.py:76）的 confidence 取 max（单调不降），而唯一能压低 confidence 的路径未接线。洞察缺有效失效机制（既无 GC 又无置信度衰减），是 soul 模块失效机制最弱的一环。

## Solution

先 `git blame` 看 `update_from_feedback` 是否曾接过线、或为哪个未完成的"假设确认 UI"预留，再二选一：
- **补接线**：把 `/api/feedback`（或一个新的针对性"这条洞察准不准"反馈入口）路由到 `update_from_feedback`，让用户反馈真正校准假设置信度；同时考虑让低 `validated`/低 confidence 的假设在 delight/discovery 消费侧被降权或过滤。
- **删除**：若确认是无主遗留，移除该方法 + 对应单测，避免误导。

关联 todo：[2026-06-15-cognition-cursor-incremental-read] —— 两者都指向觉察/洞察链缺乏生命周期管理。

## Resolution (2026-06-15)

补接线（非删除）。新增 `POST /api/insights/feedback`（`InsightFeedbackIn`/`InsightFeedbackResponse`，app.py），把插件洞察卡片的确认/推翻路由进 `update_from_feedback`。后者改为返回 `{matched, hypothesis, signal, validated, confidence}` 供端点回传（原返回 `None`、无生产调用方，故不破坏既有单测）。端到端验收：`tests/test_api_insight_feedback.py`（reject→confidence≤0.35+validated False 且落库；confirm→≥0.75+True；未知假设 matched False；坏 signal / 空 hypothesis → 422）。soul.md + changelog 已同步。未做：把 `validated`/低置信度用作 delight/discovery 的硬过滤（仍只靠置信度加权降权），如需更强失效再开后续 todo。
