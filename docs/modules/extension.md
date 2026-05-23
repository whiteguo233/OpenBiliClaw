# 浏览器插件模块

## 模块范围

`extension/` 是浏览器插件子项目（Chrome / Edge / Brave 主构建，Firefox 独立构建），负责：

- 在 B 站 / 小红书 / 抖音 / YouTube 等支持的站点采集行为事件（平台无关内核 + 平台适配器）
- 通过 background service worker 缓冲并上报到本地后端
- 在 side panel 中展示连接状态、推荐结果、画像和聊天入口

当前里程碑进度：

| 子模块 | 状态 | 说明 |
|------|------|------|
| 8.1 行为采集 | ✅ | `collector.ts` + `service-worker.ts` 已接通真实事件链 |
| 8.2 后端 API | ✅ | Python 侧 `/api/events`、`/api/health`、`/api/recommendations` 已可联调 |
| 8.3 Side Panel | ✅ | 已切到 side panel 主入口，继续复用 `popup/` 页面承载推荐 / 画像 / 聊天三 tab；顶部功能区提供移动端二维码入口，按当前插件后端地址生成 `/m/` 扫码链接；如果当前后端地址仍是 `127.0.0.1` / `localhost`，会先读 `/api/health.lan_ip` 并用局域网 IP 生成二维码，提示为 info 状态；后端会优先返回 `192.168.x.x` / `10.x.x.x` / `172.16-31.x.x` 这类真实局域网地址，排除 `198.18.x.x` 等 VPN/TUN 地址；聊天改走后端 durable turn，Chrome 丢弃或切 tab 后可恢复；惊喜推荐和兴趣猜测的内联聊天也会按 `scope=delight/probe` 恢复 pending/completed/failed turn；聊天 tab 激活时隐藏底部活动栏，聊天记录区独立滚动并占满上方空间，输入框固定在底部且会轮播想法、口味、自我描述、近期状态等多场景提示语 |
| Runtime stream 合并刷新 | ✅ | 插件 side panel、桌面 Web 和移动 Web 对 `refresh.pool_updated` / `activity.added` / `profile_updated` 等运行时事件做 debounce 与 single-flight；补货事件密集时合并请求，移动推荐页只重拉推荐列表和 header，不再回到全量 `loadData()`。 |
| 封面图代理加载 | ✅ | side panel 的推荐卡片、惊喜推荐和消息封面会用当前配置的后端 origin 拼接 `/api/image-proxy?url=...`，不再直连平台 CDN，也不再设置 `referrerPolicy`。 |
| Firefox 140+ 支持 | ✅ | `manifest.firefox.json` 使用 `sidebar_action` 承载同一套 popup UI，`openExtensionUi()` 按 Chrome sidePanel -> Firefox sidebarAction -> tab 降级；Firefox manifest 在构建时注入主 manifest version，并声明 AMO 所需 `data_collection_permissions` |
| 持续补货与通知 | ✅ | 运行状态已接入 popup，service worker 会拉取高置信通知并回写发送状态 |
| 设置页源策略控制 | ✅ | side panel 设置页已按「模型 / 平台源 / 调度 / 通用 / 日志」分 tab；模型 tab 可设置 LLM / embedding 的显式备选 Provider，留空即不 fallback，并明确 embedding 不再跟随默认 LLM；平台源 tab 按 Bilibili / 小红书 / 抖音 / YouTube / 通用网页 / 候选池配比独立分块，可开关四个平台 discovery，编辑各源预算和候选池占比，并按已有事件向后端请求推荐比例；调度 tab 暴露后台暂停、断开宽限、真实 refresh / probe 频率和猜测兴趣参数；日志 tab 用单个「完整日志路径」编辑后端日志文件位置 |
| B 站 Cookie 自动同步 | ✅ | service worker 会读取 `SESSDATA` / `bili_jct` / `DedeUserID` 三件套并推送到本地后端；后端暂未启动时切到 1 分钟重试，成功后恢复 60 分钟兜底刷新；后端 runtime-stream 也可发 `bilibili_cookie_sync_requested` 让扩展立刻回传 |
| 抖音 Cookie 自动同步 | ✅ | service worker 会读取 douyin.com Cookie header 并推送到 `/api/sources/dy/cookie`；后端保存到 `data/douyin_cookie.json`，供 `discover --source douyin` / `discover-douyin` 在无环境变量覆盖时使用；冷启动、runtime-stream 请求和 alarm 兜底都会触发同步 |
| 认知变化提醒 | ✅ | service worker 会提示关键认知变化，画像 tab 会显示“阿B 最近新记住了什么” |
| 认知变化历史分页 | ✅ | 画像 tab 的认知卡片支持展开详情，并可下拉或点击按钮继续查看更早的变化记录 |
| 认知卡片上下文澄清 | ✅ | 画像 tab 的认知卡片默认态现在固定展示“结论 + 上下文 + 状态提示”，用户可直接看出这是对哪条内容/哪轮聊天/哪组聚合信号形成的判断，以及这张卡片是否还能展开 |
| 画像多层认知展示 | ✅ | 画像 tab 现已把“你怎么处理信息 / 你在内容里长期在找什么 / 这阵子更像在经历什么”单独拆开，不再只显示一段画像 prose 加兴趣 chips |
| 多源行为采集（MVP） | ✅ | content script 拆成「平台无关 kernel + 平台适配器」，新增小红书适配器。manifest 覆盖 `*.xiaohongshu.com`，事件携带 `source_platform` 字段；MVP 仅采 snapshot / click / scroll / search，like/collect 延后 |
| 视频页停留时长采集 | ✅ | v0.3.x event-satisfaction：`src/content/video-dwell-tracker.ts` 用纯依赖注入的方式追踪 video page session，kernel 在 `pushState` / `replaceState` / `popstate` / `pagehide` 触发时 flush 一个 click 事件，metadata 携带 `watch_seconds` / `video_duration_seconds` / `dwell_source="video_page_exit"`，供后端 `classify_event_satisfaction` 区分 meaningful_dwell vs quick_exit。service worker 透传 metadata，不会丢字段 |
| xhs token 嗅探（MAIN world） | ✅ | `src/main/xhs-token-sniffer.ts` 以 `world: "MAIN"`、`run_at: "document_start"` 注入 xhs 页面，劫持 `window.fetch` / `XMLHttpRequest` 扫描 xhs 自家 API 响应里的 `(note_id, xsec_token)` 对子，通过 `postMessage` 桥接到 isolated world 再 `/api/sources/xhs/tokens` 回填——解决搜索页永不带 token 导致点击命中 300031 登录墙的问题 |
| xhs 初始化画像任务 | ✅ | 后端可派发 `bootstrap_profile` 任务；`/api/sources/xhs/next-task` 会先把任务原子标记为 `in_progress` 再返回给扩展，避免多个浏览器实例重复领取同一个前台拉取任务；插件先打开小红书 `/explore`，滚动任务会以前台 tab 点击页面“我”入口进入 profile，再从 profile 页 state / DOM 解析收藏、点赞和小红书页面内显式浏览记录信号；显式启用 `max_scroll_rounds` 时会有限滚动，并用 `status="partial"` 分批回传给 `/api/sources/xhs/task-result` |
| 抖音初始化画像任务 | ✅ | 后端可派发 `bootstrap_profile` 任务；插件依次访问抖音发布 / 收藏 / 喜欢 / 关注 scope，content script 结合 DOM、MAIN-world fetch tap 与 API harvester 采集条目，并用 `partial` 分批回传给 `/api/sources/dy/task-result` |
| 扩展任务并发领取保护 | ✅ | XHS / 抖音 / YouTube 的 `/next-task` claim 使用短生命周期 SQLite 连接执行 `BEGIN IMMEDIATE`，避免多个 FastAPI threadpool 请求共享同一 connection 时出现嵌套事务错误 |
| 抖音搜索任务 | ✅ | 后端可派发 `search` 任务；插件用后台 tab 在已登录抖音会话中执行关键词搜索，MAIN-world search bridge 调用页面 `byted_acrawler.frontierSign()` 签名搜索 API，回传 `dy_search` 候选供 CLI smoke 和正式 `dy-plugin-search` discovery 使用；单关键词任务 timeout 为 180 秒 |
| 抖音热点任务 | ✅ | 后端可派发 `hot` 任务；插件用后台 tab 打开 `/hot/{sentence_id}`，从跳转后的 `/video/{aweme_id}` 取 seed aweme，并通过 MAIN-world related bridge 签名 `/aweme/v1/web/aweme/related/`，回传 `dy_hot` 候选供 `dy-plugin-hot-related` discovery 使用 |
| 抖音首页推荐流任务 | ✅ | 后端可派发 `feed` 任务；插件用后台 tab 在已登录抖音首页通过 MAIN-world feed bridge 签名 `/aweme/v1/web/tab/feed/`，回传 `dy_feed` 候选供 `dy-plugin-feed` discovery 使用 |
| YouTube 初始化画像任务 | ✅ | 后端可派发 `bootstrap_profile` 任务；插件依次访问 `/feed/history`、`/feed/channels`、`/playlist?list=LL`，从 DOM 读取观看历史 / 订阅 / 点赞并用 `partial` 分批回传给 `/api/sources/yt/task-result` |
| 后端地址与端口可配置 | ✅ | 设置页「后端地址」接受裸 IPv4 / 主机名，「后端端口」仅接受 `1-65535` 的完整十进制整数；二者一起保存到 `chrome.storage.local`，popup / service worker / 任务派发 / cookie 同步 / 调试中继全部经 `apiUrl()`/`wsUrl()` 解析当前 origin；endpoint 变更后 service worker 通过 `chrome.storage.onChanged` 立即重连 `runtime-stream`，无需重载插件 |
| 后台 LLM 暂停配置 | ✅ | 设置页调度区提供「停止后台 LLM 请求」「关闭浏览器后停止后台」和断开宽限秒数，推荐页不再放运行时开关；后端通过 `/api/runtime-stream` presence 判断插件是否在线，浏览器 idle disconnect 会被 receive-side detector 及时清掉 |
| 配置恢复与降级模式 UI | ✅ | popup API 会缓存最近一次成功的 `/api/config` 快照；设置页打开时如果后端离线但有缓存，会用缓存填表并显示离线时间；如果后端以 `degraded=true` 返回配置，会展示 blocking issues，保存按钮切到“保存并提示重启”，配合后端降级模式修复错误配置 |
| 配置保存超时提示 | ✅ | `popup-api.requestJson()` 支持 AbortController timeout，`updateConfig()` 对 `PUT /api/config` 使用 60s 上限；超时时设置页显示 amber toast，文案只提示“保存请求可能已写入、热重载可能仍在后台进行”，不会断言配置一定已落盘 |
| OpenAI 认证方式配置 | ✅ | 设置页 OpenAI provider 区域可选择 `API Key` 或 `Codex OAuth`，保存时把 `[llm.openai].auth_mode` 纳入 `/api/config` payload；后端仍负责 Codex token 导入、域名限制和配置校验 |
| B 站负反馈动作采集 | ✅ | B 站 content script 会把“不感兴趣 / 不喜欢 / 减少此类推荐 / dislike”等控件识别为 `dislike` 动作，并经 `normalizeActionSignal()` 规范化为 `feedback` 事件，metadata 带 `feedback_type=dislike` 与 `reaction=thumbs_down`；后台 buffer 把 `feedback` 视为强信号即时 flush |

## 目录结构

```text
extension/
├── manifest.json
├── manifest.firefox.json
├── package.json
├── scripts/
│   ├── build.mjs
│   ├── package.mjs
│   └── package-firefox.mjs
├── popup/
│   ├── popup.html
│   ├── popup.js
│   └── popup-helpers.js
├── src/
│   ├── background/
│   │   ├── buffer.ts
│   │   ├── cookie-sync.ts     # B 站 / 抖音 Cookie 自动同步到已配置后端
│   │   └── service-worker.ts
│   ├── content/
│   │   ├── kernel.ts          # 平台无关的 DOM 观察 + 事件派发
│   │   ├── bilibili.ts        # B 站 entry point，挂载 bilibiliAdapter
│   │   ├── douyin.ts          # 抖音 entry point，挂载 fetch tap 与 task executor
│   │   ├── dy/
│   │   │   ├── bootstrap.ts   # 抖音 bootstrap scope 结果聚合与 partial payload
│   │   │   ├── dom-extractor.ts # 抖音页面 DOM 兜底解析
│   │   │   └── task-executor.ts # 抖音后台任务在页面内的执行入口
│   │   ├── xiaohongshu.ts     # 小红书 entry point，挂载 xiaohongshuAdapter
│   │   ├── youtube.ts         # YouTube entry point，挂载任务 executor
│   │   ├── yt/
│   │   │   └── task-executor.ts # YouTube bootstrap scope DOM 解析与回传
│   │   └── xhs/
│   │       ├── bootstrap.ts   # 初始化画像任务的 state / DOM 解析 helper
│   │       ├── passive.ts     # 小红书被动 URL / note metadata 采集
│   │       └── task-executor.ts # 后台任务在页面内的执行入口
│   ├── main/
│   │   ├── dy-fetch-tap.ts       # MAIN-world 抖音 fetch tap + API harvester
│   │   └── xhs-token-sniffer.ts  # MAIN-world fetch/XHR sniffer，捞 xsec_token
│   └── shared/
│       ├── backend-endpoint.ts # 共用后端 origin / apiUrl() / wsUrl() + chrome.storage 持久化 endpoint
│       ├── behavior.ts        # createBehaviorEvent / DOM snapshot kernel
│       ├── types.ts           # BehaviorEvent + PlatformAdapter 接口
│       └── platforms/
│           ├── bilibili.ts    # bvid 提取、卡片选择器、动作关键字
│           └── xiaohongshu.ts # note_id 提取、卡片选择器
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
- 评论 / 点赞 / 投币 / 收藏 / 不感兴趣意图事件

同时支持 B 站 SPA 导航感知，在 URL 变化时重新发送快照并重绑视频监听。

### `service-worker.ts`

负责后台缓冲与上报：

- 接收内容脚本事件
- 高频事件去重
- 强信号行为优先 flush；`feedback` 事件也属于强信号，会尽快上报
- `chrome.alarms` 周期性批量发送
- 发送失败时把事件回填到缓冲区
- flush 成功后检查一次待发通知
- 缓冲为空时也会周期轮询高置信通知
- 每次 service worker 冷启动都会启动 B 站和抖音 Cookie 同步；如果已配置后端暂时不可用，会通过 `chrome.alarms` 以 1 分钟间隔重试，成功同步后恢复为 60 分钟刷新
- 以 `client=background` 连接 `/api/runtime-stream` 后，如果后端发现本地缺少 B 站 Cookie，会收到 `bilibili_cookie_sync_requested`；如果 `[sources.douyin].enabled=true` 且缺少抖音 Cookie，会收到 `douyin_cookie_sync_requested`。扩展收到后会立即执行对应 Cookie POST。后端也把这条 WebSocket 作为 extension presence 信号：连接建立时允许后台 LLM 工作，最后一个连接断开后进入 `extension_disconnect_grace_seconds` 宽限；服务端 reader 会主动 `receive()` 检测 idle disconnect，避免浏览器断开后 presence 卡住
- 连接 `/api/runtime-stream` 之前会先 HTTP `GET /api/health`（2 秒超时）做一次健康探针，仅在后端可达时再 `new WebSocket(...)`。这样 fresh-install 用户先装扩展、后启动后端时，`chrome://extensions` 不会被浏览器层 WebSocket 失败计入「错误」徽标；健康探针失败仍走原有的 5s → 60s 指数退避兜底重连
- 后端不可达时会在扩展工具栏图标上打一个浅灰 `!` badge 作为可视提示，WebSocket 首次连上后自动清除；popup 内仍会显示「后端还没开张，先运行 `openbiliclaw start`」
- Cookie 监听器幂等注册，避免 onInstalled / onStartup / 冷启动重复挂载导致同一次登录触发多次 POST
- 点击扩展图标时优先打开 Chrome side panel；Firefox 构建会改用 `sidebar_action` 打开同一套 `popup/popup.html`
- 通知和认知提醒也会优先把用户带回插件 side panel / sidebar 上下文
- 在推荐通知之外，认知变化通知会打开带 `?tab=profile` 的插件页面，直接落到画像视图
- 惊喜推荐通知现在会打开带 `?tab=recommend&delight=<bvid>` 的插件页面，落到对应的首屏惊喜卡，而不是只把人丢回通用推荐页

### 小红书任务桥

`src/background/xhs-task-dispatcher.ts` 会轮询后端 `/api/sources/xhs/next-task`。后端返回任务前会把 `xhs_tasks.status` 从 `pending` 原子切到 `in_progress` 并写入 `claimed_at`；claim 事务使用独立短连接执行，避免和 API 进程共享 SQLite connection 上的其他请求互相嵌套事务。partial 回写会保留 `in_progress`，最终 `ok / empty / failed` 才进入终态，15 分钟无回写的领取会重新变为可领取。这个领取态用于挡住多个扩展实例、service worker 重启或多次手动命令造成的同一 `bootstrap_profile` 前台 tab 重复打开。

当收到 `bootstrap_profile` 时，它会先打开 `https://www.xiaohongshu.com/explore`；默认用非激活 tab，若任务显式启用了 `max_scroll_rounds > 0` 则打开前台 tab，方便页面自己处理 profile 点击和后续滚动。dispatcher 会向 content script 发送：

```json
{
  "task_id": "...",
  "type": "bootstrap_profile",
  "scopes": ["saved", "liked", "xhs_history"],
  "max_items_per_scope": 20,
  "max_scroll_rounds": 0,
  "scroll_wait_ms": 1200,
  "max_stagnant_scroll_rounds": 5
}
```

`src/content/xhs/task-executor.ts` 会调用 `bootstrap.ts` 解析小红书页面已经渲染出的 state。若当前页不是个人主页，executor 会只从可信入口找当前登录用户的 profile URL：优先使用小红书导航栏“我”的链接，其次使用 `__INITIAL_STATE__.user.loggedIn=true` 时的 `userInfo.userId`。滚动任务找到导航栏“我”时，会先把 `next_url_clicked=true` 的中间结果回传，然后在页面内触发 anchor click；background 收到后不会直接 `tabs.update(profileUrl)`，而是等待同一 tab 自己导航完成并再次执行任务，SPA 没有发出完整 load 事件时会短暂 fallback 到同 tab 重发。到达 profile 后，executor 会继续等待小红书 React 页面出现 profile state、收藏/赞过 tab 文案或 note 卡片，避免浏览器 load complete 早于页面内容渲染时误判为空。只有找不到可点击入口、但能从 state 推出 profile URL 时，background 才会在同一 tab 直接导航到 profile 页。

到 profile 页后，executor 读取 `__INITIAL_STATE__.user.notes` 分组：`[0]` 为发布，`[1]` 为收藏，`[2]` 为赞过；如果收藏 / 赞过分组尚未加载，会尝试点击对应 profile tab 等待页面自己补齐 state，再退回到已渲染 DOM 卡片解析。state 解析兼容小红书 profile noteCard 结构（`noteCard.displayTitle`、`noteCard.user.nickName`、`noteCard.cover.urlDefault`），滚动后每轮也会把 state 和 DOM 结果合并，避免只看当前可见 DOM 时漏掉已加载但被虚拟列表移出的卡片。默认任务不滚动；如果后端任务显式传入 `max_scroll_rounds > 0`，executor 会优先探测小红书实际 feed / waterfall / masonry 滚动容器，并排除 `clientHeight` 过小、`overflow-y` 不是 `auto/scroll/overlay`、以及 `channel-list` / sidebar 这类非内容侧栏；如果没有可用内容容器，会回退到窗口级小步 `wheel` / `scrollBy`，贴近用户手动前台滚动。任务会运行到达到 `max_items_per_scope`、达到滚动轮数上限，或连续五轮没有新增卡片。每个 scope 的首批和后续新增卡片会先以 `status="partial"` 回传，partial 批次也会按该 scope 剩余名额裁剪，background 等后端确认后再继续，最后用 `status="ok"` 完成任务。

后端可以按任务控制滚动节奏，不需要改插件常量：

| payload 字段 | 默认值 | 插件端裁剪 | 说明 |
|---|---:|---:|---|
| `scroll_wait_ms` | `1200` | `500..5000` | 每轮滚动后等待小红书瀑布流加载的时间 |
| `max_stagnant_scroll_rounds` | `5` | `1..10` | 连续多少轮没有新增卡片后停止 |

dispatcher 会把这两个字段透传给 content script；如果 `scroll_wait_ms` 拉长，background 也会同步放宽任务 timeout，最多 6 分钟。

滚动任务的 debug 会带 `scroll_candidates` 和 `tab_load_results[scope].scroll_metrics`：前者列出页面上排名靠前的滚动候选、`overflow-y`、note 数和评分；后者按每轮记录实际滚动目标、`scroll_top / scroll_height / client_height`、滚动前后位置、新增卡片数和累计卡片数。真实联调时可用它区分“页面到底了”“滚错容器了”和“页面没有暴露更深的滚动节点”。

这条链路仍不直接调用小红书 API、不读取 cookie、不接触 Chrome 浏览器历史。这里的 `xhs_history` 指“小红书网页自己明确暴露的浏览记录 / 足迹 state”，不会把普通 `/explore` 推荐流当成浏览记录；如果小红书网页没有暴露稳定入口，就返回 0 条并让初始化继续。

#### v0.3.10 self_info 全路径捕获

**任意** XHS 页面只要登录,`window.__INITIAL_STATE__.user.userInfo` 就带 self user_id + nickname。v0.3.10 起把抽取从只在 bootstrap_profile 任务里发生,扩到三条入池路径全覆盖:

| 路径 | 文件 | 行为 |
|------|------|------|
| 被动采集(任意 XHS 页) | `src/content/xiaohongshu.ts:runPassiveCollection` + `src/content/xhs/passive.ts` | 读 state,scrape-time `filterSelfAuthoredNotes` 把 `note.author === self.nickname` 的卡片直接 drop;observation 里塞 `self_info` 给后端 |
| search / creator 任务 | `src/content/xhs/task-executor.ts:executeTaskInPage` 非 bootstrap 分支 | 同上,`TaskResultPayload.self_info` 带回 |
| bootstrap_profile 任务 | `src/content/xhs/task-executor.ts:executeBootstrapTaskInPage` | 既有路径不变,debug 里仍嵌 `xhs_bootstrap.steps[*].self_info` 兼容老后端 |

后端 v0.3.57 的 `_extract_self_info_from_payload` 优先读顶层 `self_info`,fallback 到旧的 nested 位置,**新旧扩展+新旧后端的四种组合都不破**(老扩展配老后端不动;新扩展配老后端会 500——升级窗口期短暂)。这把"用户自己发的笔记进推荐池"问题(屎屎/自家165㎡大五房等)从 race condition 治成确定性过滤。

### 抖音任务桥

`src/background/dy-task-dispatcher.ts` 会轮询后端 `/api/sources/dy/next-task`。抖音 `bootstrap_profile` 属于显式账号信号导入，会打开前台抖音页面；`search` / `hot` / `feed` discovery 属于后台补池任务，统一用 `chrome.tabs.create({active:false})`，不抢用户焦点。当收到 `bootstrap_profile` 时，dispatcher 会按任务 payload 依次执行：

```json
{
  "task_id": "...",
  "type": "bootstrap_profile",
  "scopes": ["dy_post", "dy_collect", "dy_like", "dy_follow"],
  "max_items_per_scope": 300,
  "max_scroll_rounds": 15
}
```

`src/content/dy/task-executor.ts` 负责在页面内切换 scope、滚动与回传。`src/main/dy-fetch-tap.ts` 运行在 MAIN world，拦截抖音页面 fetch，并对收藏 / 喜欢 scope 走站内 API harvester：`/aweme/v1/web/aweme/favorite/` 对应 `dy_collect`，`/aweme/v1/web/aweme/like/` 对应 `dy_like`。采集到的条目通过 `postMessage` 回到 isolated world 后进入 `BootstrapItemSink` 去重，再以 `status="partial"` 分批 POST 到 `/api/sources/dy/task-result`；最终 scope 跑完后用 `ok` 完成任务。后端会把新增 videos 转成统一事件：发布 → `view`，收藏 → `favorite`，点赞 → `like`，关注 → `follow`。

CLI 侧分两层使用这条链路：

- `openbiliclaw init --yes-douyin` 会把任务结果加入初始化事件集合，进入 `analyze_events()` 和 `build_initial_profile()`。
- `openbiliclaw fetch-douyin` 只做单源 smoke / 补拉；事件由 daemon 在接收 partial 时写入 memory，CLI 自身不会再传播一次，也不会隐式触发画像重建。

### YouTube 任务桥

`src/background/yt-task-dispatcher.ts` 会轮询后端 `/api/sources/yt/next-task`。当收到 `bootstrap_profile` 时，dispatcher 会打开一个前台 YouTube tab，并按任务 payload 串行执行：

```json
{
  "task_id": "...",
  "type": "bootstrap_profile",
  "scopes": ["yt_history", "yt_subscriptions", "yt_likes"],
  "max_items_per_scope": 300,
  "max_scroll_rounds": 10
}
```

`src/content/yt/task-executor.ts` 负责在页面内滚动并读取 DOM。`yt_history` 对应 `/feed/history`，`yt_subscriptions` 对应 `/feed/channels`，`yt_likes` 对应 `/playlist?list=LL`。每个 scope 完成后，background 以 `partial` 回传新增 items 和 scope counts，最后以 `ok` 完成任务。后端会把新增 items 转成统一事件：观看历史 → `view`，订阅 → `follow`，点赞 → `like`。

CLI 侧分两层使用这条链路：

- `openbiliclaw init --yes-youtube` 会在抖音 collect 完成后才入队 YouTube，避免两个前台 tab 任务同时抢浏览器焦点，并把结果加入 `analyze_events()` 和 `build_initial_profile()`。
- `openbiliclaw fetch-youtube` 只做单源 smoke / 补拉，不隐式触发画像重建。

抖音 dispatcher 收到 `search` 时，会先在后台打开抖音首页，再为每个关键词打开抖音搜索页并发送 `DY_SEARCH_EXECUTE`：

```json
{
  "task_id": "...",
  "type": "search",
  "keywords": ["猫", "机械键盘"],
  "max_items_per_keyword": 20
}
```

dispatcher 等待首页、搜索页和热点页 ready 时会同时处理两种情况：正常的 `chrome.tabs.onUpdated(status="complete")`，以及抖音 SPA 已经跳到目标页但没有再发完整 `complete` 事件的 fallback timer，避免任务卡住直到 `task_timeout`。search 任务按关键词数计算超时窗口，单关键词至少 180 秒，覆盖首页打开、搜索页跳转、MAIN-world acrawler 签名 API 和 DOM 兜底解析的真实耗时；后端 `DouyinPluginSearchClient` 默认也等 180 秒，避免插件刚开始执行 search bridge 就被后端清成 stale。`src/content/douyin.ts` 会尝试触发页面搜索 UI、监听页面自身搜索响应，并通过 `src/main/dy-fetch-tap.ts` 的 MAIN-world search API bridge 兜底拉取 `/aweme/v1/web/general/search/single/`。这个 bridge 会补齐浏览器参数，并调用页面 `byted_acrawler.frontierSign()` 生成 `X-Bogus` 后用 `credentials: "include"` 请求，避免简化直连接口命中 `antispam_check / hit_shark` 软空。热点任务复用同一 MAIN-world 签名能力：后台打开 `/hot/{sentence_id}` 后，content script 从当前 `/video/{aweme_id}` 解析 seed aweme，再请求 `/aweme/v1/web/aweme/related/` 拉相关视频；dispatcher 会按任务总目标数累计，达到目标后不再继续打开后续 hot seed。feed 任务同样复用 MAIN-world 签名能力，在后台首页请求 `/aweme/v1/web/tab/feed/` 拉推荐流。搜索结果以 `scope="dy_search"`、热点结果以 `scope="dy_hot"`、首页推荐结果以 `scope="dy_feed"` 回写到 `dy_tasks.result_json`，不会转成初始化画像事件；`DouyinPluginSearchClient` 会把这些候选映射成 aweme-like JSON，分别以 `dy-plugin-search` / `dy-plugin-hot-related` / `dy-plugin-feed` 进入 discovery。

CLI 入口：

- `openbiliclaw search-douyin -k 猫 --max-items-per-keyword 10 -w 180`：真实 smoke 插件搜索召回。
- `discover-douyin --source hot --limit 3 --no-cache --no-evaluate`：真实 smoke 热榜 related 召回。
- `discover-douyin --source feed --limit 3 --no-cache --no-evaluate`：真实 smoke 首页推荐流召回。
- direct-cookie `discover-douyin --source search` 如果遇到空结果，可用 `search-douyin` 判断登录浏览器路径是否仍能拉到候选。

### `popup/`

`popup/` 目录当前承载 side panel 页面，已具备：

- 后端连接状态检查
- 设置页「后端地址」（默认 `127.0.0.1`，接受裸 IPv4 / 主机名）和「后端端口」（默认 `8420`，仅接受 `1-65535` 的完整十进制整数）由 `popup-backend-config.js` 一起写入 `chrome.storage.local`；popup 自身的 `/api/...` HTTP 请求与 `runtime-stream` WebSocket，以及 service worker / cookie 同步 / 各源任务派发都通过 `apiUrl()` / `wsUrl()` 在调用时解析当前 origin，service worker 通过 `chrome.storage.onChanged` 同步收到变更并立即重连。endpoint 不会写入后端 `config.toml`；本机改端口时用 `openbiliclaw start --port <同一端口>`，连接局域网其他机器时让后端用 `openbiliclaw start --host 0.0.0.0 --port <同一端口>` 监听，并在插件设置页填写该机器的局域网 IP
- 顶部手机图标会打开移动端二维码面板，二维码完全在 popup 本地生成，指向当前插件后端地址的 `/m/`；如果当前 host 仍是 `127.0.0.1` / `localhost`，面板会提示手机通常无法访问，需要先把插件后端地址改成电脑局域网 IP
- 设置页调度区的「停止后台 LLM 请求」写入 `scheduler.enabled=false`；开启后会暂停 daemon-owned 定时发现、候选池预计算和画像更新里的 LLM / embedding 调用，推荐列表不会自动补充新内容，候选池为空时可能暂时没有推荐。「关闭浏览器后停止后台」写入 `scheduler.pause_on_extension_disconnect=true`，断开宽限秒数写入 `scheduler.extension_disconnect_grace_seconds`；所有扩展窗口断开并超过宽限期后，后台 LLM / embedding 工作暂停，重新打开浏览器后恢复。手动刷新和显式 CLI / API 操作仍按用户动作执行
- 从 `/api/recommendations` 拉取推荐列表
- 设置页会通过 `/api/config` 读取并保存后端配置，保存后请求后端热重载；当前覆盖 LLM provider/key/model、LLM 显式备选 provider、DeepSeek reasoning、OpenRouter headers、embedding provider/key/model/显式备选 provider、per-module LLM override、B 站浏览器、通用 source 浏览器、Bilibili / 小红书 / 抖音 / YouTube source 开关、各源 discovery 预算、数据目录、SQLite 路径、调度、自动更新、候选池平台配比、真实 refresh / proactive push / speculator idle 频率、猜测兴趣参数、完整日志路径和日志清理参数
- 成功读取 `/api/config` 后，popup API 会把配置快照写入 `chrome.storage.local["openbiliclaw.config_cache"]`。后端离线时设置页会读取缓存填表，并显示缓存时间；没有缓存时显示错误横条且不伪造默认值
- 后端返回 `degraded=true` 时，设置页会在表单顶部展示降级原因和 blocking issues，保存按钮显示“保存并提示重启”；保存响应带 `restart_required=true` 时用 warning tone 提示用户重启 daemon
- 设置页的“按已有信号建议比例”会把当前页面上尚未保存的平台开关和比例一并 POST 到 `/api/config/source-share-suggestion`，按本地事件库的平台分布填入 B 站 / 小红书 / 抖音 / YouTube 占比，用户仍需点击保存才写入 `config.toml`
- 设置页保存配置时会保留后端已有的高级字段：`save_config()` 会串行化 scheduler speculation / auto-update 和 logging unmanaged cleanup 字段，避免 UI 修改常用项时把隐藏高级项写回默认值
- 推荐 tab 现已改成“换一批”，会调用 `/api/recommendations/reshuffle` 直接从 discovery pool 秒级换出一批新推荐
- 推荐 tab 滚到底时会调用 `/api/recommendations/append` 继续往下续 10 条，不会把当前这一屏直接替换掉；首次渲染、切回推荐 tab 和追加完成后也会再检查一次底部距离，避免停在底部时没有新 scroll 事件导致续页卡住
- popup API 现在会统一规范化推荐项，追加出来的 `cover_url` 也会被收敛成可直接加载的 `https://` 地址
- 推荐、惊喜推荐和消息内封面图会通过 `popup-helpers.buildImageProxyPath()` 生成 `/api/image-proxy?url=...`，再用 `popup-backend-config.getBackendOrigin()` 拼成当前后端绝对地址；图片加载失败时保留已有 wrapper fallback，不让卡片布局塌缩
- `/api/recommendations/refresh` 仍保留为后台补货入口，用于继续往候选池里持续进货
- popup 推荐卡片现在不会再把空 `expression / topic_label` 补成固定占位文案；后端预生成没完成时，这两块会直接隐藏
- 亮色 side panel 视觉系统：顶部 hero + inline 状态徽标、胶囊 tab、统一卡片体系，整体更贴近 B 站内容产品气质
- 推荐 tab：展示视频封面、标题、UP 主、`topic_label`、朋友式推荐文案，并通过“打开视频”明确跳转到对应 B 站视频页
- 如果某条内容暂时没有可用封面，卡片会回退到占位态，不影响换片和反馈
- 推荐封面不再依赖原生 `loading="lazy"`，避免内部滚动容器续页时新卡片封面偶发空白
- 底部提示区已升级为更明显的状态横条，会按成功 / 提示 / 错误切换对比度和状态点，减少“反馈发出去了但看不见”的感觉
- 修复卡片误跳转：`喜欢` / `不喜欢` / `写一句` / 输入框 / 发送按钮不再冒泡触发视频打开
- `喜欢` / `不喜欢` / `写一句` 都会调用 `/api/feedback`；桌面 Web 推荐卡片还提供「忽略」按钮（`feedback_type=dismiss`），走软移除语义：候选 `pool_status` 标 `feedbacked` 后不会再次进入发现池，但不会下调话题或作者权重。
- 推荐卡片里的 `写一句 -> 发出去` 现在会在按钮本地显示 `发送中... / 已发出 / 可重试` 三态，卡片底部也会同步写明这句是否真的发出去了
- 页面会读取 `/api/runtime-status`，区分“未初始化 / 正在补货 / 推荐可用”三种状态；初始化刚完成但 `initialized` 标记尚未同步时，如果已有补货中或候选池信号，不再误提示用户重新执行 init
- popup 打开期间现在会建立 `/api/runtime-stream` websocket 连接，底部提示条和池子状态会跟着后端事件实时变化
- popup 底部提示区已升级成可展开动态卡：默认两行显示“现在在忙什么 / 最近一次关键变化”，点 `更多` 可以展开最近历史
- 新增 `/api/activity-feed` 聚合接口，popup 会把认知更新、反馈记下了、换一批和补货结果收成同一块动态面板
- “换一批 / 继续追加”现在优先直接消费 discovery pool 里预生成好的 `expression / topic_label`
- 如果某条候选的预生成文案还没补好，卡片会先只展示标题、封面和 UP 信息，不会再显示统一占位话题或默认推荐理由
- 后台补货继续异步进行，不会阻塞 popup 立刻换片
- pool 状态摘要现在会区分“正在补货”“这轮找到了内容但可换库存没变”“刚补进 N 条”，不再把 refresh 进行中和上一轮净新增为 0 混成同一句
- 推荐 tab 头部现已进一步压缩成双层内容型入口：第一层只保留 `For You`、标题和 `换一批`，第二层把池子状态收成三枚紧凑 chips，让第一张推荐卡更早进入首屏
- 推荐 tab 现在还会在头部下方展示独立的“惊喜推荐”首屏卡位：popup 启动时会主动读取 `/api/delight/pending`，runtime stream 收到新的 `delight.candidate` 也会立刻刷新这张卡
- 推荐 tab 会展示候选池摘要：
  - `当前可换`
  - `最近补进`
  - `现在在忙`
  - 三条状态仍然保留，但文案已收短成更适合 chips 的形式，例如 `还有 151 条可换 / 刚补进 6 条 / 这会儿先不补货`
  - refresh 还在跑时，状态 chip 会优先显示 `正在补货`，不再先落成 `这轮还没补进`
  - 点击 `换一批` 时，进行中的文案会直接进入“现在在忙” chip，而不是再额外挤出一条独立状态行
- 推荐卡片现已进一步改成更偏编辑式的内容流：封面、标题、推荐理由和操作区的层级被重新拉开，头部信息不会再和首张内容卡抢视觉主角
- 惊喜推荐卡会直接展示封面、hook、标题和惊喜理由，并提供 `看看 / 喜欢 / 不感兴趣 / 聊一聊 / 稍后看` 动作
- `看看` 会打开对应内容并把这次点击保留成稳定的本地已处理态；`聊一聊` 会在卡内展开 composer，通过 durable `/api/chat/turns` 写入 `scope=delight` turn，不再强制把用户切去聊天 tab
- 惊喜推荐内聊使用 per-delight `turns` 作为权威 UI 历史，提交后乐观追加用户气泡和 thinking 气泡，后端完成后就地替换为 AI 回复；`chat_reply` 仅保留为兼容 last reply 字段
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
- 聊天 tab：调用 `/api/chat/turns` 创建 durable turn，后端先写入 `pending`，再后台生成回复；side panel reload 后会从 `/api/chat/turns?scope=chat` 重新 hydrate 用户消息、thinking 占位和已完成回复
- 聊天输入框内置多场景 placeholder 轮播，提示用户可以描述自己怎么看内容、喜欢 / 讨厌什么、近期观看行为、自我状态或注意力变化；输入框 focus 时暂停轮播，blur 且内容为空时恢复。聊天 tab 激活时隐藏底部活动栏，聊天历史区域改为 flex 填满输入框上方空间并独立滚动，输入框固定在 side panel 底部，窄屏下也能优先展示更多历史消息
- 惊喜推荐和兴趣猜测卡片内的 `聊一聊` 也会用 `scope=delight/probe` 写入 durable turn，回复完成后同步刷新对应卡片状态、画像摘要和最近动态；旧的 `/api/chat` 仍保留给兼容入口
- durable chat turn 写入 SQLite `chat_turns`，不再依赖 DOM、JS 内存或 `sessionStorage` 保留主聊天历史；惊喜推荐保留 `localStorage` UI 草稿、展开态和 per-delight `turns` 作为本地兜底，权威回复状态以后端为准
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
- B 站 / 抖音 Cookie 自动同步的重试闹钟和幂等监听器
- manifest 图标资源存在性
- Firefox manifest 的 version 注入、`sidebar_action` 降级路径、AMO 数据收集类别声明和 Firefox zip 打包清理
- popup 设置页字段与 `/api/config` schema 的基础对齐
- popup API durable chat turn：`startChatTurn()`、`fetchChatTurn()`、`fetchChatTurns()` 会分别调用 `/api/chat/turns`、`/api/chat/turns/{turn_id}` 和列表接口
- `dist/` 运行时脚本可被 Chrome 直接加载

## Release 分发

插件现在走独立 release 通道：

- 发布 tag：`extension-vX.Y.Z`
- Release 资产：
  - Chrome / Edge / Brave / 其他 Chromium 浏览器：`openbiliclaw-extension-vX.Y.Z.zip`
  - Firefox 140+：`openbiliclaw-extension-vX.Y.Z-firefox.zip`
- 下载入口：GitHub Releases 页面中查找最新的 `extension-v*` release
- Chrome / Edge / Brave 打包脚本会先删除同名旧 zip，再重新压缩 `manifest.json`、`dist/`、`icons/`、`popup/`，避免重复打包带入残留文件
- `extension-v*` GitHub Actions release workflow 会同时运行 Chrome / Firefox 两条打包脚本并上传两个 zip；Firefox 140+ 也可本地构建 / 临时加载：`npm run build:firefox` 生成 `dist-firefox/`，`npm run package:firefox` 生成 `openbiliclaw-extension-vX.Y.Z-firefox.zip`；Firefox 打包脚本同样会先删除同名旧 zip

后端桌面包不走 GitHub Release 分发；后端源码更新只通过 `backend-v*` tag 标记，浏览器插件的 GitHub Release 保持为唯一下载包通道。

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
- side panel 聊天已支持 durable turn 恢复：主聊天、惊喜推荐内聊和兴趣猜测内聊在页面 reload 后会按 `turn_id` 从后端恢复 pending / completed / failed 状态
- side panel 推荐、画像和聊天回复现在共用“老B友”动态语气，不再固定成一套机械模板
- side panel 能根据 `/api/runtime-status` 切换“先初始化 / 正在补货 / 推荐可用”三态
- side panel 现在还能通过 websocket 看到“开始补候选 / 当前跑到哪个策略 / 刚补进几条新的 / 这批先换好了”这类实时运行状态
- service worker 现在会在高置信推荐出现时触发浏览器通知，并通过后端回写 `notification_sent`
- service worker 现在也会拉取认知变化通知；如果最近系统对用户形成了新的高置信理解，会发一条更克制的“阿B 又对你多看清了一点”提醒
- side panel 新版亮色布局已通过本地静态页面快照检查，推荐 / 画像 / 聊天三个视图结构渲染正常
- 小红书 `bootstrap_profile` 任务已通过单元测试覆盖：dispatcher 识别任务类型并能跟随 profile URL 二次执行，executor 可从 mock `__INITIAL_STATE__` 的 saved / liked / history 分组提取 scoped notes，并能用 `partial` 批次在滚动任务中持续回传新增结果
- 抖音 `bootstrap_profile` 任务已通过扩展和后端回归覆盖：MAIN-world API harvester 可分页拉取收藏 / 点赞，dispatcher 形态的 partial 批次会在后端合并、去重并转成统一 memory 事件
- 抖音 `search` / `hot` / `feed` 任务已通过扩展回归覆盖：MAIN-world search bridge 会调用页面 acrawler 签名搜索 URL，hot-related bridge 会签名 related URL，feed bridge 会签名 `/aweme/v1/web/tab/feed/`；search 单关键词 timeout 至少 120 秒；`search-douyin -k 猫 --max-items-per-keyword 10 -w 180` 可拉到 10 条 `dy_search` 候选，`discover-douyin --source search --keyword 猫 --limit 5 --no-cache --no-evaluate` 可拉到 5 条 `dy-plugin-search` 候选

## 当前限制

- 行为按钮识别基于 DOM 文本、类名和 `aria-label`，不是服务端最终结果确认
- 采集范围优先覆盖首页、搜索页和视频页，未承诺所有 B 站模板完全一致
- side panel 主聊天和内联聊天回复已由后端 `chat_turns` 持久化；仍不提供完整聊天管理界面、删除能力或跨设备同步
- inline comment 采用轻量输入，不支持复杂反馈历史浏览
- side panel 视觉验证当前以静态快照 + extension 构建回归为主，仍建议结合真实后端做一次手动联调
- 浏览器通知当前只推送一条最高分未通知内容，不做通知中心或多条队列
- 惊喜推荐当前只维护一个首屏候选位，不做多条轮播或历史收件箱；`稍后看` 只在当前 popup 会话里隐藏，不做长期持久化
- 认知变化通知当前只提示最重要的一条，不支持用户确认/反驳，也不会在插件里维护完整通知历史
- 聚合型认知卡片如果后端暂时拿不到可信标题，会保守显示为“基于最近几条相关内容”，不会伪造具体视频名
- “换一批”依赖 discovery pool 当前已有候选；如果候选池本身供给不足，仍可能提示“池子里这会儿还没刷出新的”
- 自动续页同样依赖 discovery pool 当前已有候选；如果池子暂时不够，续页结果可能少于 10 条，甚至直接提示先等后台再补一点新的
- 池子摘要里的“最近在补”目前基于策略和候选标签做轻量聚合，属于方向提示，不是精确 taxonomy
- 小红书初始化导入是 best-effort：后端不登录、不爬取小红书，只等待插件在用户已登录浏览器里解析页面；收藏/点赞/浏览记录任一 scope 不暴露时，会跳过该 scope。普通推荐流不会被标成 `xhs_history`；受控滚动只在任务显式设置 `max_scroll_rounds` 时启用
