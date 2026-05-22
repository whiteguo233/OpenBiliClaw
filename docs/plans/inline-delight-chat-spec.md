# 内联惊喜推荐聊天 Spec

## 背景

当前移动端和插件的惊喜推荐"聊一聊"按钮直接跳转到对话 tab，丢失了上下文——用户不知道在聊哪条内容，AI 也容易混淆。应该像推荐卡片的"说说原因"一样，在惊喜推荐卡片内展开内联聊天。

## 目标

惊喜推荐的"聊一聊"按钮点击后，在卡片内部展开一个聊天 composer + 回复区域，而不是切换到对话 tab。用户可以原地和 AI 讨论这条推荐内容。

## 范围

### 移动端 Web (`src/openbiliclaw/web/`)

1. **recommend.js — renderDelightTray()**
   - "聊一聊"按钮点击后，在 delight tray 内部展开一个 composer 区域（textarea + 发送按钮）
   - 展开后 composer 获得焦点，placeholder 显示"聊聊这条推荐…"
   - 发送消息后调用 durable chat endpoint：
     - `POST /api/chat/turns`
     - `session=popup`
     - `scope=delight`
     - `subject_id=bvid`
     - `subject_title=title`
   - 提交后立即在本地追加用户气泡和 AI thinking 气泡；返回 `pending` 时通过 `GET /api/chat/turns/{turn_id}` 轮询直到 `completed` / `failed`
   - 在 composer 上方显示完整对话气泡（用户消息 + AI 回复 / thinking / 失败提示）
   - 支持多轮对话，不限制轮数
   - 关闭 composer 不清空历史，重新展开可以继续聊

2. **app.css**
   - 新增 `.delight-composer`、`.delight-chat-bubble` 等样式
   - composer 展开/收起动画

3. **状态管理**
   - 每个 delight 的聊天 UI 状态存在 delight 对象上：
     - `turns`: 当前推荐的聊天回合列表，元素至少包含 `turn_id`、`message`、`reply`、`status`、`error`
     - `composer_open`: composer 是否展开
     - `draft`: 当前输入草稿
   - `normalizeDelightCandidate()` 必须保留这些本地 UI 字段，避免局部重渲染时丢失展开态、草稿和历史
   - 初始化推荐页时，读取 `GET /api/chat/turns?session=popup&scope=delight&limit=200`，按 `subject_id` 归并到对应 delight 的 `turns`
   - `rerenderDelightOnly()` 可以局部更新，不影响推荐列表

### 插件 Popup (`extension/popup/`)

4. **popup.js — renderDelightSlot()**
   - 对齐移动端行为：点"聊一聊"在 banner body 内展开 composer
   - 插件当前已有部分 delight chat 逻辑（`composer_open`、`chat_draft`、`chat_reply`、`startChatTurn()`），但 `chat_reply` 只能表达最后一条回复，不能满足多轮气泡
   - 插件也需要升级为 per-delight `turns`：
     - 发送后追加用户气泡和 thinking 气泡
     - durable turn 完成后就地替换对应 AI 气泡
     - `chat_reply` 仅作为兼容 / last reply 字段保留，不作为权威历史
   - side panel reload 时继续通过 `fetchChatTurns({ session: "popup", scope: "delight" })` hydrate 历史，并按 `subject_id` 回填到对应 delight

### 后端

5. **无需改动** — 已有 durable chat API 支持 `scope=delight` + `subject_id` / `subject_title`
   - `POST /api/chat/turns` 创建 turn
   - `GET /api/chat/turns/{turn_id}` 查询单个 turn
   - `GET /api/chat/turns?session=popup&scope=delight` 读取历史
   - 旧 `/api/chat` 只保留给兼容入口，不用于本 spec

## 对齐插件已有行为

插件 popup 已经有 delight 内联聊天的部分实现：
- `delight.composer_open` 状态
- `chat_draft` 和 `chat_reply` 字段
- `startChatTurn()` + durable `/api/chat/turns` 调用链路

移动端应该对齐插件的 durable chat 行为模式，但不能照搬单个 `chat_reply` 的数据模型。本 spec 以 `turns` 作为移动端和插件的共同权威 UI 历史；`chat_reply` 只作为兼容字段。

## 数据流

1. 用户点击惊喜推荐"聊一聊"
2. 当前 delight 设置 `composer_open=true`，局部重渲染 delight tray / banner body，并 focus textarea
3. 用户发送消息
4. 前端生成 `turn_id`，向当前 delight 的 `turns` 追加：
   - user turn part：`message`
   - assistant turn part：`status=pending`
5. 前端调用 `POST /api/chat/turns`
6. 如果返回 `pending`，轮询 `GET /api/chat/turns/{turn_id}`；如果返回 `completed` / `failed`，直接更新对应 turn
7. 每次 turn 更新只触发 delight 局部重渲染，不重建整个推荐页
8. 用户切换左右箭头时，当前 delight 的 `turns/composer_open/draft` 留在对象上；切回来后完整恢复

## 错误处理

- 空消息不发送，只把焦点留在 textarea
- `POST /api/chat/turns` 失败时，对应 assistant 气泡标记为 failed，并保留 draft 方便重试
- 轮询超时或失败时，不清空本地历史；显示可恢复的失败提示
- 后端返回 `failed` 时展示失败气泡，不移除用户消息
- 如果某条 durable turn hydrate 回来时找不到对应 delight，只忽略该 turn，不影响推荐页渲染

## 验收标准

- [ ] 移动端惊喜推荐"聊一聊"不再跳转对话 tab
- [ ] 点击后在卡片内展开 textarea + 发送按钮
- [ ] 发送消息后显示用户气泡 + AI 回复气泡
- [ ] 支持多轮对话
- [ ] 左右箭头切换惊喜推荐时保留各自的聊天历史
- [ ] 插件 popup 的惊喜推荐聊天行为与移动端一致
- [ ] composer 展开时 font-size >= 16px（防止 iOS Safari 缩放）
- [ ] 局部更新，不触发全页面白屏刷新
- [ ] 移动端和插件都使用 `/api/chat/turns`，不使用旧 `/api/chat` 实现 delight 内联聊天
- [ ] pending turn 能在局部 UI 中显示 thinking 状态，并在完成后替换为 AI 回复
- [ ] reload 后可通过 durable turn hydrate 恢复已完成 / pending 的 delight 聊天历史

## 测试建议

- 移动端单元 / DOM 测试：
  - 点击"聊一聊"不会调用 `navigateToTab("chat")`
  - composer 展开后 textarea placeholder 为"聊聊这条推荐…"
  - textarea 计算或 CSS 规则中的 `font-size` 不小于 16px
  - 连续发送两轮后渲染两组 user / assistant 气泡
  - 左右切换不同 delight 后，各自 `turns` 不串线
  - `rerenderDelightOnly()` 不替换推荐卡列表 DOM
- 插件测试：
  - `renderDelightSlot()` 展开 composer 后不切 tab
  - `chat_reply` 不覆盖 `turns` 历史
  - `fetchChatTurns({ session: "popup", scope: "delight" })` hydrate 后按 `subject_id` 回填对应卡片
  - pending / completed / failed 三种 turn 状态都有稳定 UI
