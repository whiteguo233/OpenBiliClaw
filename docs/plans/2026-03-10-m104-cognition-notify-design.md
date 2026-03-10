# M104 关键认知变动提醒设计

## 目标

当系统对用户的关键认知出现明确变化时，通过插件发出一条克制但及时的提醒，同时在 popup“我的画像”里展示“阿B 最近新记住了什么”。

## 范围

第一版只跟踪 3 类关键认知变化：

- `interest_added`
  - 新增高权重兴趣，权重至少 `0.75`
- `dislike_added`
  - 新增明确避雷方向，或由连续 dislike/comment 汇聚出的稳定厌恶主题
- `profile_shift`
  - 画像总结、核心特质或深层需求发生显著变化

不通知低置信度的聊天碎片，也不把所有普通偏好波动都上升成系统通知。

## 方案选择

### 方案 A：任何画像/偏好变化都直接通知

- 实现简单
- 但通知会非常吵，聊天和反馈里的小波动也会频繁打断用户

### 方案 B：聚合后的关键认知变化通知

- 后端先生成 `cognition_updates`
- 只有高置信、跨阈值、且冷却期已过时才通知
- 一次只推送 1 条最重要的变化

这是本轮采用的方案。

### 方案 C：只在 popup 展示认知变化，不发系统通知

- 风险最低
- 但不满足“插件提醒”的目标

## 数据设计

### `data/memory/cognition_updates.json`

新增一个轻量状态文件，存储待提醒和已提醒的关键认知变化。

单条记录结构：

```json
{
  "id": "cog-20260310-001",
  "kind": "interest_added",
  "summary": "阿B 现在更确定你会吃“讲透来龙去脉”的内容。",
  "confidence": 0.88,
  "source": "feedback",
  "created_at": "2026-03-10T18:30:00",
  "notified": false,
  "seen_at": ""
}
```

说明：

- `source` 允许 `feedback`、`chat`、`profile_refresh`
- `notified` 表示是否已经发过系统通知
- `seen_at` 用于插件确认送达或 popup 中标记已读

## 触发规则

### 生成 `cognition update`

只在以下场景生成：

- 偏好层更新后，出现新的高权重兴趣
- 偏好层更新后，出现新的 `disliked_topics`
- 画像刷新后，核心特质/深层需求/人格摘要出现明显变化

### 通知门槛

仅在同时满足以下条件时发系统通知：

- 有 `notified = false` 的 update
- 距离上次认知通知至少 `6` 小时
- 当前没有更高优先级的推荐通知待发

第一版一次最多发送 1 条认知变化通知。

## 后端接口

新增：

- `GET /api/cognition-updates/pending`
  - 返回当前最值得提醒的一条认知变化
- `POST /api/cognition-updates/seen`
  - 由插件在通知发出后回写已提醒状态

扩展：

- `GET /api/profile-summary`
  - 增加最近 `1~3` 条认知变化摘要，供 popup“我的画像”展示

## 插件行为

### Service Worker

- 在推荐通知检查之后，再检查认知变化通知
- 只有当前没有待发推荐通知时，才尝试发认知通知
- 点击通知可打开 popup 或直接打开对应页面；第一版保持只清通知并聚焦插件体验即可

### Popup

- “我的画像”tab 增加一个只读区块：
  - `阿B 最近新记住了什么`
- 展示最近 `1~3` 条认知变化摘要
- 第一版不提供“对 / 不对”确认按钮，只展示系统最近学到的内容

## 降噪策略

- 不通知低置信度聊天候选
- 同类认知变化在冷却期内只更新内部状态，不重复提醒
- `summary` 文案继续复用当前“老B友”语气，不写成系统报告

## 测试策略

- `MemoryManager`
  - `cognition_updates.json` 读写与过滤
- `SoulEngine`
  - 偏好/画像变化会生成正确的 update
  - 轻微变化不会生成 update
- `API`
  - pending/seen 接口行为正确
  - `profile-summary` 能带最近认知变化
- `Extension`
  - 通知 helper 能构造认知通知
  - popup 能展示“最近记住了什么”

## 文档更新

完成后同步更新：

- `docs/modules/memory.md`
- `docs/modules/soul.md`
- `docs/modules/extension.md`
- `docs/changelog.md`
- `docs/v0.1-todolist.md`
