# 浏览器插件模块

## 模块范围

`extension/` 是 Chrome 插件子项目，负责：

- 在 B 站页面采集行为事件
- 通过 background service worker 缓冲并上报到本地后端
- 在 side panel 中展示连接状态、推荐结果、画像和聊天入口

当前里程碑进度：

| 子模块 | 状态 | 说明 |
|------|------|------|
| 8.1 行为采集 | ✅ | `collector.ts` + `service-worker.ts` 已接通真实事件链 |
| 8.2 后端 API | ✅ | Python 侧 `/api/events`、`/api/health`、`/api/recommendations` 已可联调 |
| 8.3 Side Panel | ✅ | 已切到 side panel 主入口，继续复用 `popup/` 页面承载推荐 / 画像 / 聊天三 tab |
| 持续补货与通知 | ✅ | 运行状态已接入 popup，service worker 会拉取高置信通知并回写发送状态 |
| 认知变化提醒 | ✅ | service worker 会提示关键认知变化，画像 tab 会显示“阿B 最近新记住了什么” |
| 认知变化历史分页 | ✅ | 画像 tab 的认知卡片支持展开详情，并可下拉或点击按钮继续查看更早的变化记录 |
| 认知卡片上下文澄清 | ✅ | 画像 tab 的认知卡片默认态现在固定展示“结论 + 上下文 + 状态提示”，用户可直接看出这是对哪条内容/哪轮聊天/哪组聚合信号形成的判断，以及这张卡片是否还能展开 |
| 画像多层认知展示 | ✅ | 画像 tab 现已把“你怎么处理信息 / 你在内容里长期在找什么 / 这阵子更像在经历什么”单独拆开，不再只显示一段画像 prose 加兴趣 chips |

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
- 点击扩展图标时优先打开 side panel
- 通知和认知提醒也会优先把用户带回插件 side panel 上下文
- 在推荐通知之外，认知变化通知会打开带 `?tab=profile` 的插件页面，直接落到画像视图

### `popup/`

`popup/` 目录当前承载 side panel 页面，已具备：

- 后端连接状态检查
- 从 `/api/recommendations` 拉取推荐列表
- 推荐 tab 现已改成“换一批”，会调用 `/api/recommendations/reshuffle` 直接从 discovery pool 秒级换出一批新推荐
- 推荐 tab 滚到底时会调用 `/api/recommendations/append` 继续往下续 10 条，不会把当前这一屏直接替换掉
- popup API 现在会统一规范化推荐项，追加出来的 `cover_url` 也会被收敛成可直接加载的 `https://` 地址
- `/api/recommendations/refresh` 仍保留为后台补货入口，用于继续往候选池里持续进货
- popup 推荐卡片现在不会再把空 `expression / topic_label` 补成固定占位文案；后端预生成没完成时，这两块会直接隐藏
- 亮色 side panel 视觉系统：顶部 hero + inline 状态徽标、胶囊 tab、统一卡片体系，整体更贴近 B 站内容产品气质
- 推荐 tab：展示视频封面、标题、UP 主、`topic_label`、朋友式推荐文案，并通过“打开视频”明确跳转到对应 B 站视频页
- 如果某条内容暂时没有可用封面，卡片会回退到占位态，不影响换片和反馈
- 推荐封面不再依赖原生 `loading="lazy"`，避免内部滚动容器续页时新卡片封面偶发空白
- 底部提示区已升级为更明显的状态横条，会按成功 / 提示 / 错误切换对比度和状态点，减少“反馈发出去了但看不见”的感觉
- 修复卡片误跳转：`喜欢` / `不喜欢` / `写一句` / 输入框 / 发送按钮不再冒泡触发视频打开
- `喜欢` / `不喜欢` / `写一句` 都会调用 `/api/feedback`
- 推荐卡片里的 `写一句 -> 发出去` 现在会在按钮本地显示 `发送中... / 已发出 / 可重试` 三态，卡片底部也会同步写明这句是否真的发出去了
- 页面会读取 `/api/runtime-status`，区分“未初始化 / 正在补货 / 推荐可用”三种状态
- popup 打开期间现在会建立 `/api/runtime-stream` websocket 连接，底部提示条和池子状态会跟着后端事件实时变化
- popup 底部提示区已升级成可展开动态卡：默认两行显示“现在在忙什么 / 最近一次关键变化”，点 `更多` 可以展开最近历史
- 新增 `/api/activity-feed` 聚合接口，popup 会把认知更新、反馈记下了、换一批和补货结果收成同一块动态面板
- “换一批 / 继续追加”现在优先直接消费 discovery pool 里预生成好的 `expression / topic_label`
- 如果某条候选的预生成文案还没补好，卡片会先只展示标题、封面和 UP 信息，不会再显示统一占位话题或默认推荐理由
- 后台补货继续异步进行，不会阻塞 popup 立刻换片
- pool 状态摘要现在会区分“正在补货”“这轮找到了内容但可换库存没变”“刚补进 N 条”，不再把 refresh 进行中和上一轮净新增为 0 混成同一句
- 推荐 tab 头部现已进一步压缩成双层内容型入口：第一层只保留 `For You`、标题和 `换一批`，第二层把池子状态收成三枚紧凑 chips，让第一张推荐卡更早进入首屏
- 推荐 tab 会展示候选池摘要：
  - `当前可换`
  - `最近补进`
  - `现在在忙`
  - 三条状态仍然保留，但文案已收短成更适合 chips 的形式，例如 `还有 151 条可换 / 刚补进 6 条 / 这会儿先不补货`
  - refresh 还在跑时，状态 chip 会优先显示 `正在补货`，不再先落成 `这轮还没补进`
  - 点击 `换一批` 时，进行中的文案会直接进入“现在在忙” chip，而不是再额外挤出一条独立状态行
- 推荐卡片现已进一步改成更偏编辑式的内容流：封面、标题、推荐理由和操作区的层级被重新拉开，头部信息不会再和首张内容卡抢视觉主角
- 画像 tab：调用 `/api/profile-summary` 展示轻量人格画像、核心特质、深层需求、更完整的近期兴趣关键词，以及单独的“最近明显会避开”分组
- 画像 tab 现在还会单独展示 `cognitive_style / motivational_drivers / current_phase` 三层认知摘要，让“这会儿的你”更像对用户的理解，而不是兴趣标签润色
- 画像 tab 会额外展示“阿B 最近新记住了什么”，让用户能看到最近几次高置信度认知变化
- 这块已经从单行列表升级为可展开认知卡片：默认只看一句总结，展开后可看“这对画像的影响 / 为什么这么判断 / 这次依据”
- 评论类认知卡片会带上对应内容标题，例如“阿B 刚记下了你对《某条视频》的评论”，不再缺少上下文
- 默认态现在固定显示：
  - 结论
  - `来自：《某条内容》` / `来自最近这轮聊天：…` / `基于最近主题：…` / `基于最近几条相关内容`
  - 以及 `展开 / 收起 / 仅结论` 这类显式状态提示，不再让用户猜能不能点开
- `/api/profile-summary` 现已支持 `limit / cursor` 分页参数，并返回 `has_more_cognition_updates / next_cognition_cursor`
- popup 首屏先展示 3 条认知卡片；滚动到画像列表底部时会自动续页，底部也保留“加载更多 / 重试加载”按钮作为兜底
- 推荐里提交 `dislike` 或 `说说原因` 后，这块会即时刷新，不再必须等到反馈批处理阈值满足
- 聊天或推荐反馈成功后，如果 side panel 已经看过画像摘要，popup 会强制重拉 `/api/profile-summary`，让“阿B 最近新记住了什么”尽快同步到当前视图
- 聊天 tab：调用 `/api/chat`，在 side panel 内和“阿B”进行轻量多轮对话；对话会记录为 `dialogue` 事件，并在高置信度重复出现时参与后续画像更新
- 推荐、画像和聊天文案共享后端的 `ToneProfile`，基础风格是“老B友”，但会根据画像和近期反馈在信息密度、温度和梗感上动态调整
- 推荐、画像、聊天三个 tab 已统一为同一套浅色卡片语言，推荐内容被提升为侧边栏首屏视觉重心

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
- side panel 页面反馈按钮已能经 `/api/feedback` 写回推荐表和事件层
- side panel 现已支持 `推荐 / 我的画像 / 和阿B聊聊` 三个 tab，并已接通画像摘要与聊天接口
- side panel 聊天信号已进入后端学习链，但仍采用受控积累，不会因为单轮聊天立即重写画像
- side panel 推荐、画像和聊天回复现在共用“老B友”动态语气，不再固定成一套机械模板
- side panel 能根据 `/api/runtime-status` 切换“先初始化 / 正在补货 / 推荐可用”三态
- side panel 现在还能通过 websocket 看到“开始补候选 / 当前跑到哪个策略 / 刚补进几条新的 / 这批先换好了”这类实时运行状态
- service worker 现在会在高置信推荐出现时触发浏览器通知，并通过后端回写 `notification_sent`
- service worker 现在也会拉取认知变化通知；如果最近系统对用户形成了新的高置信理解，会发一条更克制的“阿B 又对你多看清了一点”提醒
- side panel 新版亮色布局已通过本地静态页面快照检查，推荐 / 画像 / 聊天三个视图结构渲染正常

## 当前限制

- 行为按钮识别基于 DOM 文本、类名和 `aria-label`，不是服务端最终结果确认
- 采集范围优先覆盖首页、搜索页和视频页，未承诺所有 B 站模板完全一致
- side panel chat 会话只保留在当前打开周期内，不做本地持久化
- inline comment 采用轻量输入，不支持复杂反馈历史浏览
- side panel 视觉验证当前以静态快照 + extension 构建回归为主，仍建议结合真实后端做一次手动联调
- 浏览器通知当前只推送一条最高分未通知内容，不做通知中心或多条队列
- 认知变化通知当前只提示最重要的一条，不支持用户确认/反驳，也不会在插件里维护完整通知历史
- 聚合型认知卡片如果后端暂时拿不到可信标题，会保守显示为“基于最近几条相关内容”，不会伪造具体视频名
- “换一批”依赖 discovery pool 当前已有候选；如果候选池本身供给不足，仍可能提示“池子里这会儿还没刷出新的”
- 自动续页同样依赖 discovery pool 当前已有候选；如果池子暂时不够，续页结果可能少于 10 条，甚至直接提示先等后台再补一点新的
- 池子摘要里的“最近在补”目前基于策略和候选标签做轻量聚合，属于方向提示，不是精确 taxonomy
