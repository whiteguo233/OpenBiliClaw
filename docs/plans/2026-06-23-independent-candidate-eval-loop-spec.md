# Independent Candidate Eval Loop Spec

## 背景

当前候选补货链路把 discovery、candidate drain/eval、pool precompute 放在同一条 refresh plan 执行路径里。正常路径是：

```text
refresh_if_needed
-> build refresh plan
-> discover / produce raw candidates
-> drain_pending / eval_batch
-> admit to pool
-> precompute pool copy
```

这在 refresh plan 能正常生成时延迟较低，但有一个结构性问题：当 refresh plan 因来源配额、source producer 节流、外部平台不可用等原因为空时，已有 `discovery_candidates` pending raw 不一定会继续被评估。结果表现为可用池低于目标、raw/pending 很多，但日志只看到 `enforce_pool_cap`，没有新的 `eval_batch start` 或 `Periodic precompute made`。

## 目标

- 让已有 `discovery_candidates` pending raw 拥有独立、周期性的 eval drain 入口。
- 保留 refresh plan discover 后的即时 eval 能力，避免新发现内容必须等下一轮定时任务。
- 确保所有 eval 入口串行化，不并发消费同一批 pending raw，不重复消耗 LLM。
- 让日志能解释为什么没有 eval：无 pending、pool full、profile unavailable、drain locked、LLM paused 等。
- 不改变来源配额本身的语义：来源配额仍决定是否继续 discover 新 raw。

## 非目标

- 不在本阶段重写 source share / quota 策略。
- 不取消 B 站、小红书、抖音、YouTube、X 等 producer 的既有节流策略。
- 不把 refresh plan 改成纯 produce-only 架构；这可以作为后续重构。
- 不改变 `DiscoveryCandidatePipeline` 的评分、去重、阈值、admit 规则。

## 当前问题

当前 `run_forever()` 有 `_loop_refresh()` 和 `_loop_pool_precompute()`：

- `_loop_refresh()` 会尝试 build refresh plan。plan 非空时才进入 `_run_refresh_plan()`，进而调用 `pipeline.drain_pending()`。
- `_loop_pool_precompute()` 只处理已经 admit 到 pool 的候选 copy / topic label / delight score，不会 claim/eval `discovery_candidates` raw。
- `drain_discovery_candidates_once()` 已存在，但不是独立后台 loop；它主要由 API 回调或手动路径触发。

因此当 refresh plan 为空时，pending raw 可能缺少稳定 drain 入口。

## 推荐设计：混合模式

保留 refresh plan 内的即时 eval，同时新增一个独立 eval loop 作为兜底。

```text
loop_refresh:
  build refresh plan
  discover / enqueue raw
  drain_discovery_candidates_once(reason="refresh")
  precompute if admitted

loop_candidate_eval:
  every check_interval_seconds
  if LLM work allowed and initialized:
    drain_discovery_candidates_once(reason="periodic")
    precompute if admitted
```

这样在 refresh plan 正常时，新 raw 可以被立即评估；在 refresh plan 为空或外部 source producer 卡住时，已有 pending raw 仍会被周期性处理。

## 并发模型

新增 loop 必须复用现有锁，而不是新增独立 eval 入口。

### 锁

- `RuntimeRefreshController._discovery_drain_lock`
  - 保护 controller 层面的 drain 入口。
  - 所有 controller 发起的 candidate drain 都应走同一个 helper。
- `DiscoveryCandidatePipeline._drain_lock`
  - 保护 pipeline 层面的 pending claim/eval/admit。
  - 即使某个调用绕过 controller lock，也不会并发 eval 同一批 rows。

### 行为

如果 periodic eval loop 正在跑，refresh path 刚 discover 完也尝试 drain：

```text
refresh drain sees _discovery_drain_lock locked
-> skip this drain attempt
-> periodic loop or next refresh tick continues
```

如果 refresh path 先拿到锁，periodic loop 则跳过本轮。

不排队等待，保持当前 refresh 系统的 skip-if-busy 风格，避免多个长 LLM eval 堆积。

## 数据流

### Pending raw 已存在

```text
discovery_candidates.pending
-> periodic eval loop
-> claim_discovery_candidates_for_eval
-> discovery.evaluate_batch
-> update_discovery_candidate_evaluations
-> admit accepted rows into content_cache / pool
-> precompute_pool_copy
```

### Refresh discover 新 raw

```text
refresh plan
-> produce_and_enqueue
-> drain_discovery_candidates_once(reason="refresh")
-> precompute_pool_copy
```

### Pool 已满

如果 `pool_available >= pool_target_count`，candidate eval loop 跳过，不继续消耗 LLM。

### LLM 暂停

如果 `_llm_work_allowed()` 为 false，candidate eval loop 跳过。

## 代码改动范围

### `src/openbiliclaw/runtime/refresh.py`

新增：

- `_loop_candidate_eval()`
- `_drain_discovery_candidates_and_precompute(reason: str, batch_size: int | None = None)`

调整：

- `run_forever()` 增加 `asyncio.create_task(self._loop_candidate_eval())`。
- `_run_refresh_plan()` 中直接调用 `pipeline.drain_pending()` 的地方，改为走统一 helper，或者在保留现有 produce/eval 结构时至少复用 `_discovery_drain_lock`。
- `drain_discovery_candidates_once()` 增加可选 `profile` / `reason` 参数，避免 refresh path 重复获取 profile，并让日志能说明来源。

### `src/openbiliclaw/discovery/candidate_pipeline.py`

原则上不需要改变评分逻辑。可按需补充日志：

- no pending rows
- pool full
- eval failed and claims released
- evaluated/cached/rejected/failed counts

### `src/openbiliclaw/runtime/xhs_producer.py`

本 Spec 不强制改 XHS budget 逻辑。但建议后续单独做一个小修：

- 在 claim keyword 前预检查 task budget。
- budget 为 0（无限制）时跳过 budget check。
- budget exhausted 时返回 `reason="budget_exhausted"`，不要 claim 后再 rollback。

## 日志要求

新增低噪声 debug/info 日志：

```text
candidate eval drain skipped: reason=llm_paused
candidate eval drain skipped: reason=not_initialized
candidate eval drain skipped: reason=pool_at_cap pool_available=300 target=300
candidate eval drain skipped: reason=locked
candidate eval drain skipped: reason=no_profile
candidate eval drain skipped: reason=no_pending
candidate eval drain done: reason=periodic evaluated=30 cached=12 rejected=18 failed=0 pool_available=67->79
```

当 refresh plan 为空时，补一条说明：

```text
refresh plan empty: pool_available=67 target=300 source_requested={...} source_available={...} source_raw={...}
```

这能直接解释“为什么没有开始补货/评估”。

## 测试要求

### 单元测试

1. `test_candidate_eval_loop_drains_pending_when_refresh_plan_empty`
   - 模拟 `_build_refresh_plan()` 返回空。
   - 模拟 `discovery_candidates` 有 pending rows。
   - 调用新 drain helper。
   - 断言 `evaluate_content_batch` 被调用，accepted rows admitted。

2. `test_candidate_eval_loop_skips_when_pool_at_cap`
   - `count_pool_candidates >= pool_target_count`。
   - 断言不调用 eval。

3. `test_candidate_eval_loop_serializes_with_refresh_drain`
   - 预先占用 `_discovery_drain_lock`。
   - 调用 periodic drain。
   - 断言返回 skipped/zero，不调用 eval。

4. `test_refresh_path_uses_shared_candidate_drain`
   - refresh discover 产生 pending rows。
   - 断言 drain 走统一 helper 或至少遵守同一锁。

5. `test_periodic_eval_runs_precompute_after_admit`
   - drain cached > 0。
   - 断言调用 `precompute_pool_copy`。

### 回归测试

构造以下状态：

```text
pool_available < target
B站 requested_count = 0
XHS producer no-op
discovery_candidates.pending > 0
```

期望：

```text
periodic candidate eval loop still evaluates pending candidates
pool_available increases if candidates pass threshold
```

## 验收标准

- 在 refresh plan 为空时，只要 pending raw 存在且 pool 未满，日志能看到 periodic candidate eval drain 运行。
- 不会出现两个并发 `eval_batch` 处理同一批 pending rows。
- 新 discover 的候选仍能在 refresh path 中尽快评估，不必须等待下一分钟。
- `pool_available < target` 且 pending raw 可用时，不再只重复 `enforce_pool_cap`。
- 当没有 pending raw 或 pool 已满时，日志明确说明跳过原因。

## 后续重构选项

如果第一阶段稳定，可以进一步演进为纯分层架构：

```text
source producers / refresh plan: only produce raw
candidate eval loop: only evaluate/admit raw
pool precompute loop: only precompute admitted candidates
```

这会让职责最清晰，但需要事件唤醒机制，避免 discover 后等待下一轮 eval loop 增加用户可见延迟。本阶段不做该重构。
