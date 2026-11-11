# 后台 LLM 预算请求级准入真实链路验收（2026-11-11，issue #344）

原因：`scheduler.llm_budget_max_calls` 原先只在 `ContinuousRefreshController` 的环路入口检查、
计数在进程内存里，所以一次认知运行的多批调用、强信号早触发，以及多个进程（daemon / API /
桌面 worker / CLI）都可以各自跑过上限。

修复：`LLMConcurrencyGate.slot()` 在每次后台请求拿到并发槽后、发出前执行
`_admit_background_call()`；额度用尽抛 `LLMBudgetExhaustedError`，请求不会发送。计数落在
SQLite `background_llm_budget` 表（`llm/budget.py` 的 `BackgroundBudgetLedger`，一个
`BEGIN IMMEDIATE` 事务完成“过期重置 / 已满拒绝 / 否则 +1”），同一数据库上的所有进程共享。
认知循环在 awareness / insight 分片之间捕获该异常：保留水位、不推进 `last_awareness_at`、
在结果里给出 `budget_exhausted / budget_detail`。强信号早触发加 30 分钟最小间隔与每 12h
最多 4 次的去抖。

## 环境与方法

- 本机真实库 APFS 克隆（109,390 条事件，max event id 109,803）+ 真实画像层复制到 `/tmp`；
  真实存储不改。
- 只消费最新 2 条真实事件，`awareness_event_batch_size=1` → 两批；预算设为
  `llm_budget_max_calls=1 / llm_budget_window_seconds=3600`。
- 生产组件链路：`MemoryManager` → `LLMService`（真实 `openai_compatible` 实例 + 共享 ledger）→
  `AwarenessAnalyzer(compact-v1)` / `InsightAnalyzer` → `CognitionCycle.run_if_due()`。
- Provider：`token.sensenova.cn`；本轮 deepseek 实例恰好被上游限流，运行落到 `glm-5.2`。

## 结果

| 项 | 值 |
| --- | --- |
| 实际 Provider 调用数 | **1**（第 1 批 awareness） |
| 第 1 批 | `soul.awareness_confusions`，`glm-5.2`，25,786 input / 1,616 output tokens，估算 ¥0.0306，成功，生成 4 条觉察 |
| 第 2 批 | 预算拒绝，**没有发出请求**（`provider_calls` 仍为 1，`llm_usage` 无新增行） |
| insight | 同样在首批前被拒，未发出请求 |
| 结果标记 | `budget_exhausted=true`，`budget_detail` 含 “1/1 background calls in the last 3600s” |
| 水位 | `last_awareness_event_id` 从 109,801 推进到 109,802（只覆盖已成功的第 1 批） |
| 节流时间戳 | `last_awareness_at` 保持 2020-01-01（未推进，下个窗口从水位续跑） |
| 总耗时 | 31.65s（含 1 次真实 provider 往返） |

结论：低预算下第 2 批在**出站前**被拒，而不是发出去再失败；额度、水位和报告三者一致。

## 边界

- 单次运行、单进程验证；跨进程共享由 `background_llm_budget` 表与两个 gate 共享同一 ledger
  的回归测试覆盖，本轮没有同时拉起 daemon + CLI 两个进程做真实并发。
- 本轮 insight 的 `last_insight_at` 由测试前置状态写成当前时间，因此没有断言“未推进”；
  该行为由 `tests/test_llm_budget_admission.py` 的单测锁定。
- 预算拒绝会让本轮 awareness 提前结束，属预期；下一窗口从水位续跑由水位未推进保证。
- 原始报告含画像片段，只留本机；仓库内只提交脱敏汇总
  [summary.json](assets/issue-344-background-budget/summary.json)。

## 复现

```bash
D=/tmp/obc-344-e2e
mkdir -p "$D/data/memory"
sed 's#^data_dir = .*#data_dir = "'"$D"'/data"#' config.toml > "$D/config.toml"
cp -c data/openbiliclaw.db "$D/data/openbiliclaw.db"
for f in preference soul soul_profile profile_overrides awareness insight feedback_state; do
  cp -c "data/memory/$f.json" "$D/data/memory/$f.json" 2>/dev/null || true
done
# state: last_awareness_event_id = max(id) - 2, last_awareness_at = 2020-01-01
PYTHONPATH=<checkout>/src python <driver using LLMConcurrencyGate(background_budget_calls=1, ledger=BackgroundBudgetLedger(db))>
```
