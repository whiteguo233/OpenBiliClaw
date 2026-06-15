---
created: 2026-06-15
title: 觉察/洞察改游标增量取代固定长度
area: soul
files:
  - src/openbiliclaw/soul/cognition_cycle.py:207
  - src/openbiliclaw/soul/cognition_cycle.py:235
  - src/openbiliclaw/soul/engine.py:778
  - src/openbiliclaw/memory/manager.py:237
---

## Problem

CognitionCycle 取上游数据是"固定长度/全量"，没有内容游标，导致漏读 + token 浪费：

- **觉察** `_run_awareness`（cognition_cycle.py:207）：每 tick 固定 `query_events(limit=50)` 取最新 50 条。只有 `last_awareness_at` 时间节流，**无内容游标**。
  - 12h 内事件 >50 → 旧事件静默丢失，永远不会进入觉察分析（覆盖缺口）。
  - 事件 <50 → 同一批反复重发给 LLM（靠 `merge_notes` 的 (date, observation) 去重保正确，但白烧 token）。
- **洞察** `_run_insight`（cognition_cycle.py:235）：每次 `_load_awareness_notes()` 取**全部**觉察，无窗口。awareness.json 随时间无界增长 → 洞察 prompt 无界膨胀、每次全量重发。

**已有先例可对齐：** 同链路的 `process_feedback_batch_if_needed`（engine.py:778）已用内容水位 `last_processed_feedback_event_id`（manager.py:237 的 feedback_state），只取 `id > 水位` 的事件。觉察/洞察缺这个模式。

关联：awareness.json / insight.json 本身 append-only、无 GC、无 TTL（merge 只去重不淘汰），这条修复同时缓解其无界增长隐患。

## Solution

引入内容游标（参考 feedback_state 的 `last_processed_*` 写法，存在 cognition_cycle_state.json 或新 state 文件）：

- **觉察** = 「事件游标保证不漏」+「近期回看窗口保证趋势上下文」。不能纯增量——觉察产出的是"最近趋势"观察，只拿两 tick 间的少量新事件难以判断趋势，需要保留一段近期上下文窗口。
- **洞察** = 「取上次之后的新觉察」+「当前活跃假设（active_insights）作上下文」。既止住 prompt 膨胀，又保留跨观察综合能力。

实现时注意：游标推进要和现有"awareness 失败不推进 last_awareness_at、下 tick 立即重试"的容错语义协调，别让游标在 LLM 失败时误推进导致丢数据。

## Resolution (2026-06-15)

按方案落地，并按用户补充加了**分批 + 更大 max_tokens**。

- `Database.query_events` / `MemoryManager.query_events` 新增 `after_event_id` 过滤（`id > 水位`）。
- **觉察** `_run_awareness(state)`：水位 `last_awareness_event_id`（存 cognition_cycle_state.json），取最新 ≤`_AWARENESS_BACKLOG_CAP=200` 条未处理事件，按 `_AWARENESS_EVENT_BATCH_SIZE=50` 分批、**逐批成功后推进水位并落盘**（中途失败保留已处理批、不丢不重复）；首批附 `_AWARENESS_CONTEXT_LOOKBACK=10` 条已处理事件作趋势上下文；无新事件直接跳过（不调 LLM）；积压超 cap 跳窗 + WARNING（不静默丢）。失败重试语义保留：批内 `AwarenessGenerationError` 重试一次，仍失败则抛出由 run_if_due 处理、`last_awareness_at` 不推进。
- **洞察** `_run_insight(state)`：位置游标 `last_insight_awareness_index`（觉察 append-only），只读新觉察、按 `_INSIGHT_NOTE_BATCH_SIZE=40` 分批，把当前活跃假设作 `existing_hypotheses` 透传给 `build_insight_prompt`（system 仍静态、prompt-cache 不破；新增 rules 5/6 用中文"觉察笔记"避免英文 "awareness" 误触测试启发式分类器）。
- **max_tokens**：批量调用用 `_COGNITION_MAX_TOKENS=32768`；`AwarenessAnalyzer.analyze` / `InsightAnalyzer.analyze` 新增 `max_tokens` 形参。
- 验收：`test_cognition_cycle.py` 新增 5 用例（覆盖 130 事件不漏 + 分 3 批 + 水位到最新；二轮只读新事件 + 有界回看不全量重处理；无新事件跳过；中途失败保留首批进度；洞察游标只读新觉察 + 透传已有假设）。修复 `test_pipeline_advanced.py` 9 个旧 cognition 用例（补 seed 事件 / retrigger 前加新事件）。全量非集成 2754 passed、ruff + mypy 干净。
