# 9.2 画像更新设计

## 背景

`9.1` 已经补齐了反馈采集闭环：

- CLI、API、popup 都能写入推荐反馈
- 反馈会更新 `recommendations` 表
- 反馈会追加到事件层，事件类型为 `feedback`

但当前反馈仍然只是“被记录”，还没有真正反向影响偏好层和画像层。`9.2` 的目标就是把反馈从被动记录，提升为主动学习信号。

## 目标

实现一条轻量但真正可用的反馈驱动更新链：

1. 收集到 N 条新反馈后，触发偏好层重新分析
2. 当偏好变化超过阈值时，触发灵魂层更新
3. 将更新后的偏好和画像持久化

## 设计原则

### 同步阈值触发

不引入后台任务系统。每次反馈成功写入后，同步检查是否达到反馈阈值。达到后，直接在当前请求/命令内完成更新。

### 复用现有偏好分析器

不新增独立的“反馈分析器”。反馈事件仍然作为事件批次的一种，继续交给 `PreferenceAnalyzer.analyze_events()`。这样可以复用现有 prompt、合并逻辑和衰减逻辑。

### 最小状态持久化

新增 `feedback_state.json`，记录：

- `last_processed_feedback_event_id`
- `last_feedback_reanalyzed_at`

不把运行状态混进 `preference.json` 或 `soul.json`。

### 变化阈值可解释

使用简单启发式来判断偏好变化是否足以触发画像重建，而不是复杂相似度模型。

## 更新流程

每次反馈成功写入后，执行：

1. 查询 `id > last_processed_feedback_event_id` 的 `feedback` 事件
2. 若数量 `< N`，直接返回
3. 若数量 `>= N`：
   - 读取当前偏好层快照
   - 调 `PreferenceAnalyzer.analyze_events()` 生成新偏好层
   - 对比旧偏好和新偏好
   - 若变化明显，则重建 `SoulProfile`
   - 保存新的 `preference.json`
   - 若有重建，则保存新的 `soul.json`
   - 更新 `feedback_state.json`

## 阈值设计

默认阈值：

- `feedback_reanalysis_threshold = 3`

偏好变化判定满足任一条件即视为明显变化：

1. 高权重兴趣标签新增/移除数量 `>= 2`
2. 任一高权重兴趣的权重变化绝对值 `>= 0.2`
3. `disliked_topics` 新增 `>= 1`

这个规则足够简单、易测试，也能覆盖“不喜欢几条之后方向明显偏移”的验收场景。

## 数据来源

反馈事件会带：

- `recommendation_id`
- `bvid`
- `feedback_type`
- `feedback_note`

这些信息足够作为高置信度偏好信号。

## 接口设计

### SoulEngine

新增统一入口：

- `process_feedback_batch_if_needed()`

职责：

- 检查未处理 feedback 事件数量
- 达阈值时重跑偏好层
- 判断是否需要重建画像
- 返回一个结构化结果，供 CLI/API 记录日志或提示

建议返回：

```python
{
  "triggered": True,
  "feedback_count": 3,
  "preference_updated": True,
  "profile_rebuilt": True,
}
```

### MemoryManager

新增：

- `load_feedback_state()`
- `save_feedback_state(state)`

对应文件：

- `data/memory/feedback_state.json`

## 错误处理

- 若偏好重分析失败：
  - 不覆盖旧偏好
  - 不推进 `last_processed_feedback_event_id`
- 若画像重建失败：
  - 保留已更新的偏好
  - 旧画像不覆盖
  - 仍可推进反馈状态，避免无限重复触发同一批失败
- 若没有画像：
  - 只更新偏好，不强求重建

## 测试策略

### SoulEngine

- 新反馈数量不足阈值时不触发更新
- 达阈值时会更新偏好
- 变化不足阈值时不重建画像
- 变化明显时重建画像并落盘

### MemoryManager

- `feedback_state.json` 正常读写
- 默认状态在文件缺失时可安全回退

### API / CLI

- 反馈成功后会调用反馈批次检查
- 不影响原有反馈成功路径

## 文档更新

完成后同步更新：

- `docs/v0.1-todolist.md`
- `docs/modules/memory.md`
- `docs/modules/soul.md`
- `docs/modules/recommendation.md`
- `docs/changelog.md`
