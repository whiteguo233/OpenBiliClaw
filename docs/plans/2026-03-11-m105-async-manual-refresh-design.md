# M105 手动刷新异步化设计

## 背景

当前 popup 的“立即刷新”按钮虽然已经能触发完整补货，但请求会同步等待一轮 `discover + recommend` 完成。实际运行时如果 LLM 限流或候选刷新较慢，popup 会长时间卡住，用户会误以为“没有刷新”。

## 目标

- “立即刷新”点击后应立即返回，不阻塞 popup
- 后端在后台执行一次完整补货
- popup 通过运行状态轮询感知“正在补货 → 刷新完成”
- 刷新失败时保留现有推荐，不清空列表

## 方案

### 后端

- 在 `ContinuousRefreshController` 中新增“手动刷新任务状态”
  - `idle`
  - `running`
  - `success`
  - `failed`
- 新增 `trigger_manual_refresh()`：
  - 若已有任务在跑，直接返回 `already_running`
  - 否则创建后台任务，异步执行 `force_refresh()`
- `get_runtime_status()` 扩展返回：
  - `manual_refresh_state`
  - `manual_refresh_message`
  - `manual_refresh_started_at`
  - `manual_refresh_finished_at`
- `POST /api/recommendations/refresh`
  - 不再等待完整刷新结束
  - 只负责触发后台任务并返回接受结果

### popup

- 点击“立即刷新”后：
  - 按钮进入 loading
  - 调用刷新接口
  - 开始短轮询 `runtime-status`
- 轮询期间：
  - 若 `manual_refresh_state = running`，显示“正在补货…”
  - 若 `success`，立即重拉推荐列表
  - 若 `failed`，显示轻提示并保留当前列表
- 推荐区继续展示旧内容，直到新内容准备好

## 数据与状态

- 运行状态先放在内存控制器中，不额外建表
- 重启服务后手动刷新状态重置为 `idle`
- 自动刷新状态与手动刷新状态分开，避免 popup 把常驻刷新误认为手动操作结果

## 风险与边界

- 第一版不做任务队列，只允许同时存在一个手动刷新任务
- 不做进度百分比，只做粗粒度状态
- 若后台任务里 LLM provider 被限流，状态会停在 `failed`，并返回短错误信息

## 验收

- 点击 popup “立即刷新”后，接口应快速返回
- popup 能看到“正在补货…”状态，而不是长时间无响应
- 刷新成功后推荐列表会重新加载
- 刷新失败时现有推荐仍保留，并出现提示
