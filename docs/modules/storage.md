# 存储层

## 概述

`src/openbiliclaw/storage/` 负责本地 SQLite 数据库、schema 初始化、候选池计数和高频读写路径。它不理解 runtime state 或用户画像，只提供确定性的持久化 API。

本模块当前承担三类边界：

- 行为、推荐、候选池、聊天和鉴权状态的 SQLite 表结构管理。
- 推荐池 `content_cache` 的可换 / raw / pending 计数口径。
- discovery 待评估池 `discovery_candidates` 的生命周期管理。

## 已实现功能

| 功能 | 状态 | 说明 |
|------|------|------|
| SQLite schema 初始化 | ✅ | `Database.initialize()` 自动创建核心表和索引，支持旧库增量补列 / 补索引。 |
| 推荐池 readiness 计数 | ✅ | `count_pool_readiness()` 返回 `available/raw/pending/pending_eval/evaluated_pending`，供 runtime status 和补货判断使用。 |
| 来源 raw material 统计 | ✅ | `count_pool_raw_material_by_source()` 合并 `content_cache` raw rows 和 `discovery_candidates` 待评估候选，供 raw ceiling headroom 使用。 |
| discovery 待评估池 | ✅ | 新增 `discovery_candidates` 表，支持 mixed-source enqueue / claim / evaluation update / cached mark / rejection status，并持久化 `score_threshold`、`eval_attempts` 与 batch 级 `batch_eval_attempts`。 |
| discovery 状态恢复 | ✅ | 启动初始化会释放过期 `evaluating` 行；terminal 状态有 status guard，避免 stale update 改写 cached / rejected 结果。 |
| 最近已看过滤 | ✅ | 可换、raw 和评估路径复用 `source_platform:content_id` 与旧 BVID key，避免已看内容重复入池。 |
| 统一 admission 分数门 | ✅ | 推荐池读取、raw/headroom 统计、topic/franchise 分布、suppressed 复活、delight 候选和历史推荐读取都会应用统一最低分；初始化会清理旧低分 `content_cache` / `recommendations` 脏数据。 |
| 惊喜通道占位排除 | ✅ | `get_pool_candidates()` / `count_pool_candidates()` 统一排除被惊喜通道认领的行（`delight_notified=1`，或 delight 分数达阈值且 reason/hook 非空即当前惊喜队列候选），普通推荐与惊喜推荐不再重复出同一条内容；阈值镜像 `DEFAULT_DELIGHT_THRESHOLD`（0.70），由测试锁定两边一致。 |
| `style_key` 历史值迁移 | ✅ | `Database.initialize()` 会把 `content_cache` / `discovery_candidates` 中已知旧内容风格 key 迁移到新的观看模式 key；写入 `cache_content()` 和 `update_discovery_candidate_evaluations()` 时也会归一化已知旧值。 |

## 公开 API

### Discovery Candidates

```python
from openbiliclaw.discovery.candidate_pool import DiscoveryCandidateWrite

count = db.enqueue_discovery_candidates(
    [
        DiscoveryCandidateWrite(
            candidate_key="youtube:abc123",
            source_platform="youtube",
            source_strategy="yt_search",
            content_id="abc123",
            title="A YouTube deep dive",
            score_threshold=0.60,
        )
    ],
    max_pending_per_source=420,
)

rows = db.claim_discovery_candidates_for_eval(limit=30)
updated = db.update_discovery_candidate_evaluations(
    [
        {
            "candidate_id": rows[0]["id"],
            "status": "evaluated",
            "relevance_score": 0.82,
            "relevance_reason": "匹配用户最近的深度解释偏好。",
        }
    ]
)
ready = db.get_evaluated_discovery_candidates_for_admission(limit=30)
if ready:
    db.mark_discovery_candidate_cached(ready[0]["id"])

db.reset_discovery_candidates_to_pending([rows[0]["id"]], reason="temporary LLM outage")
db.reset_stale_discovery_candidate_evaluations(max_age_minutes=30)
```

行为说明：

- `enqueue_discovery_candidates()` 用 `candidate_key` 去重；重复发现只刷新 `last_seen_at`。传入 `max_pending_per_source` 时，会按来源用总行数判断 cap、删除时保护 `evaluating` 行，并优先删除 terminal rows，避免长期满池时 candidate table 无界增长。
- `claim_discovery_candidates_for_eval(limit=...)` 只领取 `pending_eval`，并按 `source_platform` round-robin 选取 mixed-source batch；运行中不会回收其他 in-flight evaluator 的 claim。
- `update_discovery_candidate_evaluations()` 将 evaluator 输出回写到候选行，常用状态为 `evaluated`；只更新仍处于 `evaluating` 的行。
- `get_evaluated_discovery_candidates_for_admission(limit=...)` 读取已完成评估但尚未写入 `content_cache` 的行，供池子从满池降回目标以下后重试 admission。
- `reset_discovery_candidates_to_pending([...], reason=..., max_attempts=5, max_batch_attempts=50, increment_attempts=True)` 释放 evaluator failure 中被 claim 的行；`increment_attempts=True` 时连续失败达到上限后进入 `failed_eval`。pipeline 对 batch 级 LLM/provider transient 会传 `increment_attempts=False`，不消耗单条候选预算，但会递增 `batch_eval_attempts`；达到较高 `max_batch_attempts` 后进入 `failed_eval`，避免永久坏 provider 让同一批候选无限 churn。
- `reset_stale_discovery_candidate_evaluations(max_age_minutes=...)` 将崩溃遗留的旧 `evaluating` 行释放回 `pending_eval`。
- `mark_discovery_candidate_cached()` / `reject_discovery_candidate(..., status=...)` 只改写 `evaluating` / `evaluated` 行；terminal rows 不会被 stale caller 复活或覆盖。常见 rejection status 包括 `rejected_low_score`、`rejected_duplicate`、`rejected_cache_admission`、`rejected_recently_viewed`、`rejected_franchise_quota`。
- `count_discovery_candidates_by_status()` 与 `count_discovery_candidates_by_source_status()` 用于诊断待评估池生命周期分布。

### Pool Readiness

```python
readiness = db.count_pool_readiness()
assert set(readiness) == {
    "available",
    "raw",
    "pending",
    "pending_eval",
    "evaluated_pending",
}

raw_by_source = db.count_pool_raw_material_by_source()
```

行为说明：

- `available` 与 `count_pool_candidates()` 保持推荐 serve 同口径。
- `raw` 包含正式池 fresh raw material 和 `discovery_candidates` 中尚未缓存的候选。
- `pending` 独立计算，不用 `raw - available` 近似，避免 recently viewed 内容被误算为待整理。
- `pending_eval` 统计 `pending_eval + evaluating`；`evaluated_pending` 统计已评估但尚未 admission 到 `content_cache` 的候选。

### Admission Cleanup

```python
db.set_admission_min_score(0.60)
db.suppress_low_score_pool_items()
db.suppress_low_confidence_recommendations()
```

行为说明：

- `set_admission_min_score()` 由 runtime 在配置加载 / 热重载时调用；storage 不直接读取 runtime state 或 `config.toml`。
- `suppress_low_score_pool_items()` 会把 `content_cache.relevance_score` 低于阈值且仍可能展示的 `fresh / shown / suppressed` 行标为 `suppressed`。
- `suppress_low_confidence_recommendations()` 会把低于阈值且尚无用户反馈的历史推荐标为 `feedback_type='suppressed_low_score'`。
- `Database.initialize()` 会用默认阈值执行一次上述清理，处理旧版本已经入池 / 入历史的低分数据。

## 配置项

存储层本身不新增独立配置。本次涉及的运行时上限仍来自：

| 配置项 | 说明 |
|--------|------|
| `scheduler.pool_target_count` | 正式可换推荐池目标；达到后 runtime 不再 discovery / drain。 |
| `[scheduler.pool_source_shares]` | 平台族配比；raw material by-source 统计用它计算 source headroom。 |
| `discovery.admission_min_score` | 统一推荐池入池最低分；runtime 会注入给 `Database`，用于池读取和旧数据清理。 |
| `storage.db_path` | SQLite 数据库路径。 |

## 设计决策

1. **待评估池和正式推荐池分离**：`discovery_candidates` 只表示“已经找到但还未成为推荐素材”，`content_cache` 才是 recommendation serve 的正式候选池。
2. **来源只影响身份和统计**：候选 dedupe key、source share 和 prompt 上下文会保留来源；喜好判断统一交给 discovery evaluator。
3. **池满时不继续消耗**：runtime 以 `count_pool_candidates()` 的真实可换数为上限判断，正式池满时不 claim / evaluate 待评估候选。
4. **评估和入池可分步恢复**：`evaluated` 表示“已经通过喜好评估但还没 admission”，不是失败终态；池子恢复容量后会优先入池。batch 级 provider transient failure 释放回 `pending_eval` 且不递增 `eval_attempts`，但会递增 `batch_eval_attempts` 作为高阈值熔断；只有调用方显式要求递增 attempts 的可归因失败才会使用常规 `eval_attempts` 预算。
5. **状态机必须防 stale caller**：`evaluating` 有过期回收，terminal rows 有 status guard，避免进程 crash 或并发 caller 让候选永久卡住或复活。
6. **pending 不是 raw 减 available**：最近已看、缺文案、缺分类、缺链接、待评估属于不同诊断含义，必须分开统计。
7. **低分清理和展示防线都在存储层落地**：admission 仍由 discovery evaluator 决定；storage 只用统一阈值阻止旧脏数据、suppressed 低分复活和未来绕过入口继续进入可展示读取路径。
8. **`style_key` 迁移只改已知旧值**：历史安装用户的本地 SQLite 里可能已有 `deep_dive`、`story_doc`、`lifestyle` 等旧内容风格 key。初始化迁移会把这些已知值物理改写为 `deep_focus`、`story_immersion`、`daily_wander` 等新观看模式；未知自定义值会原样保留，避免误删无法识别的历史数据。
