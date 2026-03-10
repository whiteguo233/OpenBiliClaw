# 浏览器插件模块

## 模块范围

`extension/` 是 Chrome 插件子项目，负责：

- 在 B 站页面采集行为事件
- 通过 background service worker 缓冲并上报到本地后端
- 在 popup 中展示连接状态和推荐结果

当前里程碑进度：

| 子模块 | 状态 | 说明 |
|------|------|------|
| 8.1 行为采集 | ✅ | `collector.ts` + `service-worker.ts` 已接通真实事件链 |
| 8.2 后端 API | ✅ | Python 侧 `/api/events`、`/api/health`、`/api/recommendations` 已可联调 |
| 8.3 Popup | ✅ | 已支持推荐 / 画像 / 聊天三 tab、popup 内直接提交反馈，并完成亮色 UI 刷新 |
| 持续补货与通知 | ✅ | 运行状态已接入 popup，service worker 会拉取高置信通知并回写发送状态 |
| 认知变化提醒 | ✅ | service worker 会提示关键认知变化，画像 tab 会显示“阿B 最近新记住了什么” |

## 目录结构

```text
extension/
├── manifest.json
├── package.json
├── popup/
│   ├── popup.html
│   ├── popup.js
│   └── popup-helpers.js
├── src/
│   ├── background/
│   │   ├── buffer.ts
│   │   └── service-worker.ts
│   ├── content/
│   │   └── collector.ts
│   └── shared/
│       ├── behavior.ts
│       └── types.ts
└── tests/
    ├── collector-helpers.test.ts
    ├── dist-module-specifiers.test.ts
    ├── manifest-assets.test.ts
    ├── popup-helpers.test.ts
    └── service-worker-buffer.test.ts
```

## 当前能力

### `collector.ts`

负责内容脚本侧采集：

- 点击与搜索
- 视频 `view` / `pause` / `seek`
- 页面快照 `snapshot`
- 滚动 `scroll`
- 卡片停留 `hover`
- 评论 / 点赞 / 投币 / 收藏意图事件

同时支持 B 站 SPA 导航感知，在 URL 变化时重新发送快照并重绑视频监听。

### `service-worker.ts`

负责后台缓冲与上报：

- 接收内容脚本事件
- 高频事件去重
- 强信号行为优先 flush
- `chrome.alarms` 周期性批量发送
- 发送失败时把事件回填到缓冲区
- flush 成功后检查一次待发通知
- 缓冲为空时也会周期轮询高置信通知
- 点击浏览器通知可直接打开对应 B 站视频
- 在推荐通知之外，认知变化通知会打开带 `?tab=profile` 的 popup 页面，直接落到画像视图

### `popup/`

popup 当前已具备：

- 后端连接状态检查
- 从 `/api/recommendations` 拉取推荐列表
- 亮色 popup 视觉系统：顶部 hero + inline 状态徽标、胶囊 tab、统一卡片体系，整体更贴近 B 站内容产品气质
- 推荐 tab：展示标题、UP 主、`topic_label`、朋友式推荐文案，并通过“打开视频”明确跳转到对应 B 站视频页
- 修复 popup 卡片误跳转：`喜欢` / `不喜欢` / `写一句` / 输入框 / 发送按钮不再冒泡触发视频打开
- `喜欢` / `不喜欢` / `写一句` 都会调用 `/api/feedback`
- popup 会读取 `/api/runtime-status`，区分“未初始化 / 正在补货 / 推荐可用”三种状态
- 画像 tab：调用 `/api/profile-summary` 展示轻量人格画像、核心特质、深层需求和偏好关键词
- 画像 tab 会额外展示“阿B 最近新记住了什么”，让用户能看到最近几次高置信度认知变化
- 聊天 tab：调用 `/api/chat`，在 popup 内和“阿B”进行轻量多轮对话；对话会记录为 `dialogue` 事件，并在高置信度重复出现时参与后续画像更新
- 推荐、画像和聊天文案共享后端的 `ToneProfile`，基础风格是“老B友”，但会根据画像和近期反馈在信息密度、温度和梗感上动态调整
- 推荐、画像、聊天三个 tab 已统一为同一套浅色卡片语言，推荐内容被提升为首屏视觉重心

### 构建链路

- 运行时脚本不再直接把 `tsc` 的 ESM 产物交给 Chrome
- `scripts/build.mjs` 使用 `esbuild` 将 `collector.ts` 和 `service-worker.ts` bundle 为可直接加载的单文件
- `tsc --emitDeclarationOnly` 继续负责类型声明产物
- 新增构建回归测试，确保 content script 不会再次产出浏览器无法执行的 `import` 语句

## 本地开发

在 `extension/` 目录下：

```bash
npm install
npm test
npm run typecheck
npm run build
```

`npm test` 现在会覆盖：

- 页面识别 / BV 提取 / 动作识别
- 缓冲去重与强信号 flush
- manifest 图标资源存在性
- `dist/` 运行时脚本可被 Chrome 直接加载

## 手动联调

1. 在项目根目录启动后端：

```bash
openbiliclaw start
```

2. 在 `extension/` 目录构建插件：

```bash
npm run build
```

3. 在 Chrome 的扩展管理页加载 `extension/` 目录
4. 打开 B 站首页、搜索页、视频页，执行点击、搜索、播放、暂停、滚动等行为
5. 观察后端 `/api/events` 写入效果，或直接查看 SQLite `events` 表

目前已通过真实联调确认：

- `collector` 能在首页和搜索页成功注入
- `service worker` 能启动并批量上报
- `/api/events` 能接收插件预检请求与事件批次
- SQLite `events` 表已能写入 `snapshot` 事件
- popup 能根据 `/api/health` 与 `/api/recommendations` 切换在线、空状态与推荐列表展示
- popup 反馈按钮已能经 `/api/feedback` 写回推荐表和事件层
- popup 现已支持 `推荐 / 我的画像 / 和阿B聊聊` 三个 tab，并已接通画像摘要与聊天接口
- popup 聊天信号已进入后端学习链，但仍采用受控积累，不会因为单轮聊天立即重写画像
- popup 推荐、画像和聊天回复现在共用“老B友”动态语气，不再固定成一套机械模板
- popup 能根据 `/api/runtime-status` 切换“先初始化 / 正在补货 / 推荐可用”三态
- service worker 现在会在高置信推荐出现时触发浏览器通知，并通过后端回写 `notification_sent`
- service worker 现在也会拉取认知变化通知；如果最近系统对用户形成了新的高置信理解，会发一条更克制的“阿B 又对你多看清了一点”提醒
- popup 新版亮色布局已通过本地静态页面快照检查，推荐 / 画像 / 聊天三个视图结构渲染正常

## 当前限制

- 行为按钮识别基于 DOM 文本、类名和 `aria-label`，不是服务端最终结果确认
- 采集范围优先覆盖首页、搜索页和视频页，未承诺所有 B 站模板完全一致
- popup chat 会话只保留在当前打开周期内，不做本地持久化
- popup comment 采用轻量 inline 输入，不支持复杂反馈历史浏览
- popup 视觉验证当前以静态快照 + extension 构建回归为主，仍建议结合真实后端做一次手动联调
- 浏览器通知当前只推送一条最高分未通知内容，不做通知中心或多条队列
- 认知变化通知当前只提示最重要的一条，不支持用户确认/反驳，也不会在插件里维护完整通知历史
