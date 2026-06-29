# 浏览器插件模块

## 模块范围

`extension/` 是浏览器插件子项目（Chrome / Edge / Brave 主构建，Firefox 独立构建），负责：

- 在 B 站 / 小红书 / 抖音 / YouTube / X / 知乎 / Reddit 等支持站点采集行为事件或执行来源任务（平台无关内核 + 平台适配器）
- 通过 background service worker 缓冲并上报到本地后端
- 在 side panel 中展示连接状态、推荐结果、画像和聊天入口

当前里程碑进度：

| 子模块 | 状态 | 说明 |
|------|------|------|
| 8.1 行为采集 | ✅ | `content/kernel.ts` + `shared/platforms/*` + `service-worker.ts` 已接通统一事件链；B 站 / 小红书 / 抖音 / YouTube / X / 知乎都通过 `PlatformAdapter` 产出同一 `BehaviorEvent` 形态，平台差异只保留在 selector、内容 ID 和 action 识别中；Reddit 通过插件任务源接入初始化 saved/upvoted/subscribed 信号和 discovery search/hot/subreddit/related；click 监听在 capture 阶段执行，scroll 同时覆盖页面和内部滚动容器 |
| 8.2 后端 API | ✅ | Python 侧 `/api/events`、`/api/health`、`/api/recommendations` 已可联调；`/api/events` 在 soul 画像明确未初始化时只返回 `not_initialized` 拒收结果，不写 memory，首轮画像信号由 guided init 的来源任务拉取 |
| 8.3 Side Panel | ✅ | 已切到 side panel 主入口，继续复用 `popup/` 页面承载推荐 / 稍后 / 收藏 / 画像 / 对话五个 tab；顶部功能区提供移动端二维码入口，按当前插件后端地址生成 `/m/` 扫码链接；460px 以下窄宽度会把 Web、二维码、消息、设置按钮换到品牌区下一行靠右排列，避免和标题 / 状态徽标重叠；如果当前后端地址仍是 `127.0.0.1` / `localhost`，会先读 `/api/health.lan_ip` 并用局域网 IP 生成二维码，提示为 info 状态；后端会优先返回 `192.168.x.x` / `10.x.x.x` / `172.16-31.x.x` 这类真实局域网地址，排除 `198.18.x.x` 等 VPN/TUN 地址；移动 Web 推荐页首屏先渲染 `/api/recommendations`，再异步补 runtime status / activity / delight，慢请求不会让页面无限停在 loading；聊天改走后端 durable turn，Chrome 丢弃或切 tab 后可恢复；惊喜推荐、兴趣猜测和避雷探针的内联聊天也会按 `scope=delight/probe/avoidance_probe` 恢复 pending/completed/failed turn；聊天 tab 激活时隐藏底部活动栏，聊天记录区独立滚动并占满上方空间，输入框固定在底部且会轮播想法、口味、自我描述、近期状态等多场景提示语 |
| Runtime stream 合并刷新 | ✅ | 插件 side panel、桌面 Web 和移动 Web 对 `activity.added` / `profile_updated` 等运行时事件做 debounce 与 single-flight；`refresh.pool_updated` 只合并池子状态并刷新 header / pool chips / 底部可换提示 / 空态文案，不再重拉推荐列表，避免覆盖用户已经 append 出来的历史卡片。 |
| 兴趣挑战探针 UI | ✅ | `interest.probe` 和 `speculative_interests` 会保留后端的 `probe_mode` / `challenge` metadata；profile 页确认会向 `/api/interest-probes/respond` 传 `surface="profile"`，写回为 `profile_confirmed`，而 inbox / runtime probe 卡片确认保持默认 `probe_confirmed`。插件 side panel、移动 Web 和桌面 Web 会把普通 `near` 兴趣探针与 `lateral/bridge/wildcard` 挑战探针拆成不同样式和提示：普通兴趣强调继续探索，挑战探针提示“把口味往侧边推一点”，区别于避雷探针。用户对同一 domain 做确认、拒绝或探针内聊后，三端会用 handled probe key 立即从 inbox、画像页和 runtime hydration 里隐藏该探针，避免后端旧快照/缓存再次把它展示出来。 |
| 避雷探针 UI | ✅ | popup inbox 支持 `avoidance.probe`，按钮文案为「确实不喜欢 / 不是 / 多聊聊」；画像页显示 `speculative_avoidances` 的待确认避雷方向，确认后通过 `/api/avoidance-probes/respond` 写回后端。插件 side panel、移动 Web 和桌面 Web 会用避雷专属样式和“少看这类 / 猜错点不是”提示，区别于正向兴趣试探。移动 Web 在任一探针按钮点击后会锁住同一卡片其它动作，避免一次 active 探针被连续提交 confirm + reject；三端也会在本地记录 handled 避雷 key，使已处理 domain 不再从 profile summary、pending probes 或 runtime stream 重复水合；消息收件箱空态不会重建 header，X 关闭入口保持可用。 |
| 封面图代理加载 | ✅ | side panel 的推荐卡片、惊喜推荐和消息封面会用当前配置的后端 origin 拼接 `/api/image-proxy?url=...`，不再直连平台 CDN，也不再设置 `referrerPolicy`。 |
| X 推荐卡来源与文字卡 | ✅ | 插件 side panel、移动 Web 与桌面 Web 会把 `x` / `twitter` / `x.com` / `twitter.com` 统一归一为 `source_platform="twitter"`，标签显示 `X (Twitter)`，不再退成 Web 或 B 站；X tweet / thread 或无有效封面的推荐使用 `body_text` / title 渲染文本卡，桌面 Web 点击上报同步携带 `content_id` / `content_url` / `source_platform`。 |
| 知乎候选链接保真 | ✅ | 知乎任务 executor 对站内 API 响应做 lossless JSON 解析，把超过 JS 安全整数范围的裸整数 token 先转成字符串；归一化 discovery / 收藏 / 动态条目时也会优先从 URL 字符串解析 question / answer / article ID，再退回 JSON 字段，避免 19 位 question id 被 `Number` 舍入后拼出不可打开链接。 |
| 收藏夹 / 稍后再看 | ✅ | 推荐卡和 delight banner 都提供「时钟=稍后再看」「星星=收藏」两个互相独立的 SVG toggle（乐观 UI、失败回退、懒加载状态）；popup tab bar 已对齐移动 Web / 桌面 Web，提供独立「稍后」页（`viewWatchLater/watchLaterList`，`loadWatchLater` 拉 `/api/watch-later`）和「收藏」页（`viewFavorites/favoritesList`，`loadFavorites` 拉 `/api/favorites`），列表项展示 16:9 头图缩略图并支持打开 / 单条移除；列表页移除走共享的乐观绑定 `bindSavedCardRemove`——点击即从列表消失，DELETE（10s 超时）失败时卡片原位恢复、按钮变「重试」并打 console.error，不再等响应返回才动 DOM（旧实现静默吞错，DELETE 被同源慢请求排队时表现为「点了没反应」）；封面 URL 会和推荐卡一样归一化后走 `/api/image-proxy`。插件 popup 侧保存状态统一由 `popup-saved-sync.js` 管理：同一 bvid 的推荐卡、惊喜横幅按钮、保存列表移除会同步更新，且用户刚点击后的状态不会被旧的懒加载查询覆盖。详见 [收藏夹 spec](../specs/favorites.md) 与 [稍后再看 spec](../specs/watch-later.md)。 |
| 惊喜推荐正向保留 | ✅ | 插件 side panel、桌面 Web 和移动 Web 对惊喜推荐采用同一反馈语义：`喜欢 / 收藏 / 稍后再看 / 聊一聊` 保留候选在队列中；`去看看` 当场保留卡片但会上报 `view` 标记已读（三端统一，下次队列重灌不再出现）；`不感兴趣 / 忽略 / 关闭` 才立即移出当前队列。已喜欢的候选重灌后以 `state="liked"` 恢复展示。三端默认加载数量统一读取 `[scheduler].delight_queue_limit`，桌面 Web 设置页保存后插件和移动端随下一次队列拉取同步生效。 |
| Firefox 140+ 支持 | ✅ | `manifest.firefox.json` 使用 `sidebar_action` 承载同一套 popup UI，`openExtensionUi()` 按 Chrome sidePanel -> Firefox sidebarAction -> tab 降级；Firefox manifest 在构建时注入主 manifest version，并声明 AMO 所需 `data_collection_permissions`。发布链路会把 `dist-firefox/` 先打成未签名 `openbiliclaw-extension-v*-firefox.zip`（开发 / 临时加载 / AMO 输入），再通过 `web-ext sign --channel=unlisted` 生成可直接安装的 `openbiliclaw-extension-v*-firefox.xpi` |
| 持续补货与通知 | ✅ | 运行状态已接入 popup，service worker 会拉取高置信通知并回写发送状态 |
| 设置页源策略控制 | ✅ | side panel 设置页已按「模型 / 平台源 / 调度 / 通用 / 日志」分 tab；模型 tab 可设置 LLM / embedding 的显式备选 Provider，留空即不 fallback，并明确 embedding 不再跟随默认 LLM；LLM 与 embedding 默认配置旁提供「测试」按钮，按当前表单草稿调用 `/api/config/probe-service` 做无写入连通性探测并行内展示结果；平台源 tab 按 Bilibili / 小红书 / 抖音 / YouTube / X / 知乎 / Reddit / 通用网页 / 候选池配比独立分块，可开关各平台 discovery，编辑各源预算和候选池占比，并按已有事件向后端请求推荐比例；调度 tab 暴露后台暂停、断开宽限、真实 refresh / probe 频率和猜测兴趣参数；日志 tab 用单个「完整日志路径」编辑后端日志文件位置 |
| 封面图评估设置 | ✅ | side panel 调度 tab 补齐 `[discovery]` 多模态评估控制：可开关封面图评估，并编辑图文 batch、封面最大边、JPEG 质量和图片准备超时；保存 payload 会保留已有 discovery 字段后覆盖 `multimodal_*` 参数，与桌面 Web 设置页保持同一配置面。 |
| 桌面 Web 设置页对齐插件 | ✅ | 桌面 Web（`/web`）设置页 `src/openbiliclaw/web/desktop/` 的可配置面与插件 side panel 拉齐：模型 tab 补 `llm.concurrency`、DeepSeek `reasoning_effort`，并提供 LLM / embedding 测试按钮（当前表单草稿 → `/api/config/probe-service`，不保存配置）；平台源 tab 补完整 X(Twitter) 源块（`enabled` / `cookie_env` / 三项预算 / `request_interval` / `min_interval`）+ `GET /api/sources/x/status` 源健康提示、YouTube `min_interval_minutes`、知乎源块（`enabled` / search-hot-feed-creator-related 五项预算 / `request_interval` / `min_interval`）和候选池 X / 知乎占比；调度 tab 补 9 个真实 runtime 参数（`extension_disconnect_grace_seconds` / `refresh_check_interval_seconds` / `signal_event_threshold` / `feedback_batch_threshold` / `trending_refresh_hours` / `explore_refresh_hours` / `discovery_limit` / `proactive_push_interval_seconds` / `speculator_idle_interval_minutes`）；通用 tab 补局域网访问密码（`/auth/status` + `/auth/admin`，同源 loopback 视为可信本机）与开机自启（`/autostart-status` + `/autostart/apply`）。桌面 Web 同时移除了运行时不消费的 `discovery_cron` 旧字段，与插件一致；推荐页平台过滤 tab 会先按 `config.sources` / `pool_source_shares` 中启用的平台展示，再合并当前推荐卡真实来源，点击后只过滤当前已加载列表，命中为空时保留空结果状态 |
| 统一来源接入状态 | ✅ | 新后端端点 `GET /api/sources/status`（`SourcesStatusResponse`）用纯本地信号给每个来源一致的登录 / cookie 状态：B站 看 cookie 是否含 `SESSDATA`/`bili_jct`/`DedeUserID`（三字段不全时报 `partial` 黄点而非绿点；`config.toml` 镜像为空时回落读 `data/bilibili_cookie.json`，覆盖 CLI 二维码登录只写文件的场景）、抖音 看 `resolve_douyin_cookie()`、小红书 看带 `xsec_token` 缓存行的**新鲜度**（24h 窗口内有新发现行或令牌回填刷新过 `last_seen_at` 的候选行才算 `ready`，仅剩存量旧行报 `stale` 黄点，杜绝停止同步后永久绿点）、X 复用 `XSourceHealthStore`（健康态为 `ok` 时再用 `resolve_x_cookie()` 校验凭据存在，缺失降级为 `missing_cookie`，避免健康表默认行误报「cookie 有效」）、YouTube 标 `no_auth`，知乎 / Reddit 按本地任务表最近完成 / 登录失败 / 其它失败状态显示 `ready` / `missing` / `partial` / `unverified`，全程不发对外平台请求。插件 side panel 七张来源卡片（`data-source-status`）与桌面 Web 平台源 tab 顶部「来源接入状态」彩点列表都读同一端点，`renderSourcesStatus()` 渲染统一彩点 + 文案，状态行可见时每 30 秒自动重拉。诚实标注：仅 X 为实时校验的 `ok`，其余为本地 `ready`（就绪）/ `partial`（凭据不完整）/ `stale`（令牌可能过期）/ `missing`（未配置）/ `unverified`（尚无任务证明）/ `no_auth`（无需登录源） |
| 来源 Cookie 明文配置对齐 | ✅ | 插件 side panel 与桌面 Web 的抖音 / X 来源卡片对齐 B 站卡片形态：新增明文 Cookie 文本框（`GET /api/config` 的 `sources.douyin.cookie` / `sources.twitter.cookie`，默认脱敏、`reveal_keys=true` 明文），可手动粘贴覆盖；`PUT /api/config` 把非空值路由到 `data/douyin_cookie.json` / `data/x_cookie.json`（secrets 不进 `config.toml`），X 粘贴含 `auth_token`+`ct0` 的有效 Cookie 同时解除 re-login 封锁。空字段与脱敏回显（连续 `****`）不覆盖现有 Cookie（`bilibili.cookie` 同样补上该防护）；小红书（token 嗅探）/ YouTube（无需登录）维持差异化说明 |
| 开机自启动设置 | ✅ | 通用 tab 新增「开机自启动」开关：打开设置时读 `GET /api/autostart-status`，切换时调用 `POST /api/autostart/apply` 即时生效；`can_manage=false` 时按 `env_managed` / `shadowed` / `unsupported_*` 等 reason 禁用并展示行内提示。提示明确该开关只影响下次系统登录拉起后端，不启停当前进程；本机 Ollama 可能随 `start` 预检一起拉起。 |
| B 站 Cookie 自动同步 | ✅ | service worker 会读取 `SESSDATA` / `bili_jct` / `DedeUserID` 三件套并推送到本地后端；后端暂未启动时切到 1 分钟重试，成功后恢复 60 分钟兜底刷新；后端 runtime-stream 也可发 `bilibili_cookie_sync_requested` 让扩展立刻回传 |
| B 站扩展搜索兜底任务 | ✅ | service worker 轮询 `/api/sources/bili/next-task` 并响应 `bili_task_available` 即时 kick；后台 tab 打开 `search.bilibili.com/all?keyword=...`，B 站 content script 抓渲染后的搜索结果卡片，回传 `BILI_TASK_RESULT` 到 `/api/sources/bili/task-result`。该链路在后端 API search 冷却或短期 DOM fallback 降级信号存在且扩展在线时由 producer 入队，不取代 API 主路径 |
| 抖音 Cookie 自动同步 | ✅ | service worker 会读取 douyin.com Cookie header 并推送到 `/api/sources/dy/cookie`；后端保存到 `data/douyin_cookie.json`，供 `discover --source douyin` / `discover-douyin` 在无环境变量覆盖时使用；冷启动、runtime-stream 请求和 alarm 兜底都会触发同步 |
| Cookie 同步重试按平台隔离 | ✅ | B站 / 抖音 / X 的 cookie 同步重试 alarm 拆分为 `openbiliclaw-cookie-sync-bili` / `-dy` / `-x` 三个独立 alarm：一个平台同步成功不再把另一平台刚排的 1/5 分钟快速重试重置回 60 分钟兜底；`cookies.onChanged` 的 debounce 也按平台独立，登录某平台只触发该平台的同步。旧共享 alarm 名（`openbiliclaw-cookie-sync`，chrome alarm 跨扩展升级持久化）兼容触发一轮全量同步后由下次 worker 启动清除 |
| 认知变化提醒 | ✅ | service worker 会提示关键认知变化，画像 tab 会显示“阿B 最近新记住了什么” |
| 认知变化历史分页 | ✅ | 画像 tab 的认知卡片支持展开详情；「阿B 最近新记住了什么」默认只展示最近 3 条，需点击「加载更多」按钮分页查看更早的变化记录（不再随页面滚动自动续页，避免该区块无限变长） |
| 认知卡片上下文澄清 | ✅ | 画像 tab 的认知卡片默认态现在固定展示“结论 + 上下文 + 状态提示”，用户可直接看出这是对哪条内容/哪轮聊天/哪组聚合信号形成的判断，以及这张卡片是否还能展开 |
| 画像多层认知展示 | ✅ | 画像 tab 现已把“你怎么处理信息 / 你在内容里长期在找什么 / 这阵子更像在经历什么”单独拆开，不再只显示一段画像 prose 加兴趣 chips |
| 画像可编辑（编辑模式） | ✅ | 画像页新增「编辑画像」开关：进入后由未截断的 `GET /api/profile/edit-state` 驱动，可增删核心特质 / 深层需求 / 价值观 / 内在驱动 / 认知风格 / 常看 UP 等 chip、增删喜欢 / 不喜欢领域、改写人格素描 / 人生阶段 / 当前阶段等长文、拖动滑杆调探索开放度 / 质量敏感度 / 幽默偏好 / 深度偏好等标量；文本与标量固定项显示「AI 想更新此项」漂移建议，每个改过的字段可「恢复 AI 建议」。每个控件 POST 一次 `/api/profile/edit`（确定性、抗画像重建；chip 增删即时生效，长文与滑杆点「保存」生效）。插件 side panel 进入编辑时会切到 `is-profile-editing` 页面态，强制只读画像卡片退出布局、编辑面板占据原位置；移动 Web（`/m`）与桌面 Web（`/web`）同样是替换式编辑态，三端行为一致 |
| 多源行为采集（MVP） | ✅ | content script 拆成「平台无关 kernel + 平台适配器」，新增小红书适配器。manifest 覆盖 `*.xiaohongshu.com`，事件携带 `source_platform` 字段；MVP 仅采 snapshot / click / scroll / search，like/collect 延后 |
| 视频页停留时长采集 | ✅ | v0.3.x event-satisfaction：`src/content/video-dwell-tracker.ts` 用纯依赖注入的方式追踪 video page session，kernel 在 `pushState` / `replaceState` / `popstate` / `pagehide` 触发时 flush 一个 click 事件，metadata 携带 `watch_seconds` / `video_duration_seconds` / `dwell_source="video_page_exit"`，供后端 `classify_event_satisfaction` 区分 meaningful_dwell vs quick_exit。service worker 透传 metadata，不会丢字段 |
| xhs token 嗅探（MAIN world） | ✅ | `src/main/xhs-token-sniffer.ts` 以 `world: "MAIN"`、`run_at: "document_start"` 注入 xhs 页面，劫持 `window.fetch` / `XMLHttpRequest` 扫描 xhs 自家 API 响应里的 `(note_id, xsec_token)` 对子，通过 `postMessage` 桥接到 isolated world 再 `/api/sources/xhs/tokens` 回填到 `content_cache` 与 `discovery_candidates`——解决搜索页永不带 token 导致点击命中 300031 登录墙的问题 |
| 引导初始化 CTA | ✅ | v0.3.68：推荐 tab 未初始化时不再叫用户去命令行，而是给「开始初始化」面板：① 数据来源勾选（v0.3.118+ B 站默认勾选但可取消，与小红书 / 抖音 / YouTube / X / 知乎一样可选，至少保留一个；静态渲染秒开）+ 文案提示「使用某平台前先在当前浏览器登录该平台账号，勾选会同时开启该来源」；② 「开始初始化」按钮（点击驱动校验，不在加载时空等慢预检）。点击后置「检查中…」加载态并实时拉 `GET /api/init-status` 校验前置（LLM / embedding / 所选平台登录）：一个来源都没勾 → 提示「至少勾选一个数据来源」；勾选的小红书 / 抖音 / YouTube / X / 知乎会随 `POST /api/init` 作为本轮 opt-in 生效并 best-effort 写回 `sources.<platform>.enabled=true`；勾了 B 站但未登录 → 提示登录或取消勾选（未勾 B 站时不再要求 B 站登录）；前置未通过 → 展示前置清单 + 原因、按钮复位、**不**启动；全通过才调 `POST /api/init`（带所选 `sources`），订阅 `runtime-stream` 的 `init_progress/failed/completed`（+ 3s 轮询兜底）实时更新进度，完成后自动加载推荐 / 画像。其中 LLM / embedding 为严格真实探测（各发一次真实最小请求，超时 / 失败判未就绪）。DOM 无关逻辑在 `popup/popup-init-control.js`（单测 `tests/init-control.test.ts`）。画像 / 画像编辑空状态文案改为指向推荐页初始化。详见 [init 模块文档](init.md) |
| 头部响应式 + Star 按钮 | ✅ | side panel 默认窄宽（<460px）下头部保持**单行**（品牌左、4 个操作图标右、垂直居中）：隐藏装饰 eyebrow、状态徽标空间不足时紧凑换到标题下、图标压 28px（`.hero-actions button` 提高优先级压过靠后的 `.webui-button{32px}`），不再把图标右浮成空一截的第二行；≥460px 维持原样。功能键图标列下一行有**常驻 GitHub Star 按钮**（`.hero-sub` 内右对齐），做成大项目常见的 GitHub-Buttons 双段样式 `[🐙 Star | 数量]`：Octocat + 「Star」+ 实时 star 数（popup.js 拉 `api.github.com` CORS、`localStorage` 缓存 12h，失败则只显示 `[🐙 Star]`）；点击打开仓库（直接 star 需 GitHub 认证，官方组件亦只跳转）。响应式断点用 chrome-devtools 实测 360/400/560px，断言在 `tests/popup-layout.test.ts` |
| 桌面 Web Star 引导 | ✅ | PC Web `/web` 顶部状态区新增常驻 GitHub Star CTA，文案为「好用求 Star」，沿用插件的 GitHub-Buttons 双段视觉：Octocat + CTA 文案 + 实时 star 数。`src/openbiliclaw/web/desktop/assets/js/app.js` 直接打开项目仓库，并通过 `api.github.com/repos/whiteguo233/OpenBiliClaw` 拉取 star 数、写入 `localStorage` 缓存 12h；离线 / 限流时保留按钮但隐藏数量。`1180px` 以下收起品牌副标题、状态文案和 CTA 文案，只保留图标 + 数量，避免桌面窄宽顶栏重叠。静态回归见 `tests/test_desktop_web_pool_status.py::test_desktop_web_shows_github_star_cta` |
| xhs 初始化画像任务 | ✅ | 后端可派发 `bootstrap_profile` 任务；`/api/sources/xhs/next-task` 会先把任务原子标记为 `in_progress` 再返回给扩展，避免多个浏览器实例重复领取同一个前台拉取任务；插件先打开小红书 `/explore`，滚动任务会以前台 tab 点击页面“我”入口进入 profile，再从 profile 页 state / DOM 解析收藏、点赞和小红书页面内显式浏览记录信号；显式启用 `max_scroll_rounds` 时会有限滚动，并用 `status="partial"` 分批回传给 `/api/sources/xhs/task-result` |
| 抖音初始化画像任务 | ✅ | 后端可派发 `bootstrap_profile` 任务；插件依次访问抖音发布 / 收藏 / 喜欢 / 关注 scope，content script 结合 DOM、MAIN-world fetch tap 与 API harvester 采集条目，并用 `partial` 分批回传给 `/api/sources/dy/task-result` |
| 扩展任务并发领取保护 | ✅ | XHS / 抖音 / YouTube 的 `/next-task` claim 使用短生命周期 SQLite 连接执行 `BEGIN IMMEDIATE`，避免多个 FastAPI threadpool 请求共享同一 connection 时出现嵌套事务错误 |
| 抖音搜索任务 | ✅ | 后端可派发 `search` 任务；插件用后台 tab 先打开抖音首页，在已登录页面里模拟搜索框输入 / 点击搜索，并等待 URL 进入 `/jingxuan/search/<keyword>` 等真实搜索结果路由；任务 debug 用 `ui_triggered` 表示已提交、`search_navigation_ok` 表示已进入结果页，避免把搜索建议或登录弹窗误报成成功搜索；随后被动收集页面自身搜索响应和渲染 DOM，回传 `dy_search` 候选供 CLI smoke 和正式 `dy-plugin-search` discovery 使用；MAIN-world fetch tap 兼容 `/general/search/single/`、`/search/item/` 和新版 `/general/search/stream/` chunked JSON；runtime 会把候选写入统一待评估池；单关键词任务 timeout 为 180 秒 |
| 抖音热点任务 | ✅ | 后端可派发 `hot` 任务；hot board 的 `group_id` 会作为 `seed_aweme_id` 透传，插件优先执行带 seed 的热词；后台 tab 仍从抖音首页出发模拟热榜点击并被动收集响应 / DOM，不足时用已登录页面的 related API bridge 拉取 `dy_hot` 候选供 `dy-plugin-hot-related` discovery 使用；runtime 会把候选写入统一待评估池 |
| 抖音首页推荐流任务 | ✅ | 后端可派发 `feed` 任务；插件用后台 tab 打开已登录抖音首页，滚动推荐流触发页面加载，再被动收集 feed 响应和渲染 DOM，回传 `dy_feed` 候选供 `dy-plugin-feed` discovery 使用；runtime 会把候选写入统一待评估池 |
| YouTube 初始化画像任务 | ✅ | 后端可派发 `bootstrap_profile` 任务；插件依次访问 `/feed/history`、`/feed/channels`、`/playlist?list=LL`，从 DOM 读取观看历史 / 订阅 / 点赞并用 `partial` 分批回传给 `/api/sources/yt/task-result` |
| 知乎事件拉取任务 | ✅ | 后端可派发 `bootstrap_events` 任务；插件在已登录知乎页面内读取最近浏览记录、收藏夹条目和个人动态点赞 / 收藏，回传 `/api/sources/zhihu/task-result`。扩展会用 `/api/v4/me` 自动识别当前用户，收藏夹优先走 favlists API，旧 `/collections/mine` HTML 路径仅作 fallback。动态点赞和动态收藏各自独立使用单分支上限，不互相抢额度。`openbiliclaw fetch-zhihu` 默认只把该链路当事件爬取 smoke，不写 memory，也不触发画像初始化；加 `--write-memory` 可把本次抓取写入本地 memory，加 `--rebuild-profile` 会写入后触发真实画像重建。guided init 勾选知乎 / `init --yes-zhihu` 会显式收集同类任务结果并把统一事件喂给首轮画像；后端任务 payload 显式带 `profile_update=true` 时，`task-result` 新增事件会走 memory + `ProfileUpdatePipeline` 增量路径。任务 tab 使用 `openbiliclaw_zhihu_task` 标记进入静默模式，只安装 executor，不启动普通行为采集，避免 smoke / init 拉取污染 `/api/events` |
| 知乎 discovery 任务 | ✅ | 后端可派发 `search` / `hot` / `feed` / `creator` / `related` 任务；插件在同一个已登录知乎任务 tab 中调用搜索、热榜、首页推荐、作者页和问题相关接口并回传 `zhihu_search` / `zhihu_hot` / `zhihu_feed` / `zhihu_creator` / `zhihu_related` 候选。归一化器会映射 answer / article / question 的标题、作者、摘要、URL 与点赞 / 收藏 / 评论指标；runtime `ZhihuDiscoveryProducer` 把这些候选以 `zhihu-search` / `zhihu-hot` / `zhihu-feed` / `zhihu-creator` / `zhihu-related` 写入统一待评估池，`openbiliclaw discover --source zhihu` 使用正式 producer 流程，`openbiliclaw discover-zhihu*` 可用于真实端到端 smoke。插件 side panel 设置页和桌面 Web 设置页都能编辑 `[sources.zhihu].source_modes` 与各分支预算；creator / related 在没有历史种子时会使用同轮 search / hot / feed 产出的作者页和内容 URL 兜底。discovery 任务不写 `/api/events`，也不触发画像初始化 |
| 后端地址与端口可配置 | ✅ | 设置页「后端地址」接受裸 IPv4 / 主机名，「后端端口」仅接受 `1-65535` 的完整十进制整数；二者一起保存到 `chrome.storage.local`，popup / service worker / 任务派发 / cookie 同步 / 调试中继全部经 `apiUrl()`/`wsUrl()` 解析当前 origin；endpoint 变更后 service worker 通过 `chrome.storage.onChanged` 立即重连 `runtime-stream`，无需重载插件；Chrome Web Store / AMO 发布包默认只声明 `127.0.0.1` / `localhost` 后端 host 权限，局域网 / 远程后端需要带对应 host 权限的开发者构建或后续可选授权 |
| 后台 LLM 暂停配置 | ✅ | 设置页调度区提供「停止后台 LLM 请求」「关闭浏览器后停止后台」和断开宽限秒数，推荐页不再放运行时开关；后端通过 `/api/runtime-stream` presence 判断插件是否在线，浏览器 idle disconnect 会被 receive-side detector 及时清掉 |
| 配置恢复与降级模式 UI | ✅ | popup API 会缓存最近一次成功的 `/api/config` 快照；设置页打开时如果后端离线但有缓存，会用缓存填表并显示离线时间；如果后端以 `degraded=true` 返回配置，会展示 blocking issues，保存按钮切到“保存并提示重启”，配合后端降级模式修复错误配置 |
| 配置保存超时提示 | ✅ | `popup-api.requestJson()` 支持 AbortController timeout，`updateConfig()` 对 `PUT /api/config` 使用 60s 上限；超时时设置页显示 amber toast，文案只提示“保存请求可能已写入、热重载可能仍在后台进行”，不会断言配置一定已落盘 |
| OpenAI 认证方式配置 | ✅ | 设置页 OpenAI provider 区域可选择 `API Key` 或 `Codex OAuth`，保存时把 `[llm.openai].auth_mode` 纳入 `/api/config` payload；后端仍负责 Codex token 导入、域名限制和配置校验 |
| 版本与更新面板 | ✅ | 设置页调度 tab 的“版本与更新”读取 `/api/update-status` 展示后端当前 / 最新版本、状态、上次检查和错误；`github_rate_limited` / `github_unreachable` / `no_backend_tag_yet` 等稳定 reason 会映射成本地化提示，避免把后端错误 key 直接露给用户。`install_mode="git"` 且发现 `backend-v*` 更新时才显示“立即应用”；`install_mode="frozen"` 只跟踪 `desktop-v*` 安装包并显示“前往下载新安装包”，不会让安装包用户误触源码快进。`popup-helpers.normalizeRuntimeStatus()` 同时保留 `/api/runtime-status` 中的 `current_version`、`latest_remote_version`、`backend_update_state` 等自动更新摘要字段，避免 runtime 状态归一化时丢失后端版本信息。 |
| 语义去重未启用提示 | ✅ | v0.3.54+ 推荐页启动时读 `/api/health.embedding_ready`，为 `false` 时在推荐列表上方显示可关闭横幅（`maybeShowEmbeddingBanner`）；「一键启用本地 Ollama」按钮 PUT `/api/config` 写入 `embedding.provider=ollama, model=bge-m3` 热加载，再复检 health，仅当 `embedding_ready` 翻 `true` 才收起横幅，否则提示去跑 `ollama serve` / `ollama pull bge-m3`。本会话内关闭后不再打扰（`sessionStorage`）。v0.3.97+ 横幅决策抽到 `popup-embedding-banner.js`（`shouldShowEmbeddingBanner`），并在面板重新可见 / 获焦时复检（`installEmbeddingBannerAutoRefresh`）——配合后端实时探活，embedding 修好后无需重开面板横幅即自动消失（此前 `maybeShowEmbeddingBanner` 仅面板打开时跑一次，常驻 side panel 会长期残留旧横幅）。v0.3.97+ 同时修复横幅**根本无法隐藏**的 CSS bug：`.embedding-banner { display: flex }` 盖过 UA `[hidden] { display: none }`（同优先级，author > UA），`banner.hidden = true` 形同虚设、横幅无视 `embedding_ready` 常驻——新增 `.embedding-banner[hidden] { display: none }` 守卫修复 |
| 跨平台行为动作采集 | ✅ | B 站、小红书、抖音、YouTube 和 X 均通过 `PlatformAdapter` 识别统一动作：`like/favorite/comment/share/follow` 等按平台能力映射；`dislike` 永远经 `normalizeActionSignal()` 规范为 `feedback` 事件，metadata 带 `feedback_type=dislike` 与 `reaction=thumbs_down`。真实 DOM 点击会从内部 `span/svg` 向上归因到最近的按钮 / 链接 / 带 `aria-label` 的动作元素，避免真实站点嵌套按钮漏识别分享、关注等强信号。后台 buffer 把 `feedback/follow/share/view` 和带 dwell metadata 的 `click` 视为即时 flush 信号，高频 `scroll/hover/snapshot` 仍缓冲去重 |
| 扩展捕捉 E2E 自检 | ✅ | 本机后端可通过 `/api/extension/e2e/run` 向已安装插件投递 `extension_e2e_run`，插件打开 / 复用抖音、小红书、X 标签页并执行白名单 DOM 操作，再由后端按运行窗口校验真实 `/api/events` 入库结果。复用同域 tab 时会先归位到平台入口，避免旧 404 / modal / 图片预览页污染测试；content executor 不直接发送 `BEHAVIOR_EVENT`，确保测到的是真实捕捉链路；会改变状态的动作需要显式 `allow_state_changing=true` |
| 发现候选互动指标 | ✅ | 小红书被动采集、抖音 DOM 兜底与 MAIN-world fetch tap 会在候选 metadata 中尽量携带 `view_count` / `like_count` / `collect_count` / `comment_count` / `share_count`。指标解析使用共享 `metric-count.ts`，兼容 `1.2万`、`3k`、`1,234` 和带中文标签的文本；插件只读取页面已渲染或站内响应里已有的计数，不为补指标额外打开详情页。后端会把这些字段写入 `discovery_candidates`，进入统一 discovery evaluator。 |
| 对话历史自动定位 | ✅ | `popup/popup.js` 的 `scrollChatMessagesToBottom()` 统一处理聊天历史滚动：历史 hydrate、追加用户/助手消息、thinking 占位替换，以及从其他 tab 切回「对话」时都会把 `.chat-messages` 滚到最新 turn；切 tab 场景额外用下一帧滚动覆盖 hidden 容器恢复布局后的高度变化 |

## 目录结构

```text
extension/
├── manifest.json
├── manifest.firefox.json
├── package.json
├── scripts/
│   ├── build.mjs
│   ├── package.mjs
│   ├── package-firefox.mjs
│   ├── sign-firefox.mjs
│   └── chrome-webstore-upload.mjs
├── popup/
│   ├── popup.html
│   ├── popup.js
│   ├── popup-autostart-control.js
│   ├── popup-saved-sync.js
│   └── popup-helpers.js    # popup 纯函数：runtime 状态归一化、探针 key / stale 过滤等
├── src/
│   ├── background/
│   │   ├── buffer.ts
│   │   ├── cookie-sync.ts     # B 站 / 抖音 / X Cookie 自动同步到已配置后端（重试 alarm 按平台隔离）
│   │   ├── e2e-runner.ts      # 后端驱动的真实标签页 E2E 捕捉自检 runner
│   │   ├── bili-task-dispatcher.ts # B 站搜索兜底任务轮询 / 后台 tab / 结果回传
│   │   ├── zhihu-task-dispatcher.ts # 知乎 bootstrap_events/search/hot/feed/creator/related 任务轮询 / 前后台 tab / 结果回传
│   │   ├── reddit-task-dispatcher.ts # Reddit bootstrap/search/hot/subreddit/related 任务轮询 / 后台 tab / 结果回传
│   │   └── service-worker.ts
│   ├── content/
│   │   ├── e2e-executor.ts    # 只执行白名单 DOM 操作，不直接伪造行为事件
│   │   ├── kernel.ts          # 平台无关的 DOM 观察 + 事件派发
│   │   ├── metric-count.ts    # 可见计数文本解析：1.2万 / 3k / 1,234 / 带标签文本
│   │   ├── bilibili.ts        # B 站 entry point，挂载 bilibiliAdapter
│   │   ├── bili/
│   │   │   └── task-executor.ts # B 站搜索页 DOM 结果解析与 BILI_TASK_RESULT 回传
│   │   ├── douyin.ts          # 抖音 entry point，挂载 douyinAdapter、fetch tap 与 task executor
│   │   ├── dy/
│   │   │   ├── bootstrap.ts   # 抖音 bootstrap scope 结果聚合与 partial payload
│   │   │   ├── dom-extractor.ts # 抖音页面 DOM 兜底解析
│   │   │   └── task-executor.ts # 抖音后台任务在页面内的执行入口
│   │   ├── xiaohongshu.ts     # 小红书 entry point，挂载 xiaohongshuAdapter
│   │   ├── youtube.ts         # YouTube entry point，挂载 youtubeAdapter 与任务 executor
│   │   ├── zhihu.ts           # 知乎 entry point，挂载 zhihuAdapter 与任务 executor
│   │   ├── reddit.ts          # Reddit entry point，挂载 redditAdapter 与任务 executor
│   │   ├── yt/
│   │   │   └── task-executor.ts # YouTube bootstrap scope DOM 解析与回传
│   │   ├── zhihu/
│   │   │   └── task-executor.ts # 知乎浏览记录 / 收藏夹 / 动态条目 / discovery 候选读取与回传；长数字 ID 按字符串保真解析
│   │   ├── reddit/
│   │   │   ├── task-mode.ts      # Reddit 任务 tab 标记识别
│   │   │   └── task-executor.ts  # Reddit 同源 JSON bootstrap/search/hot/subreddit/related 读取与归一化
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
│       ├── e2e.ts             # E2E request / result / action 类型与超时常量
│       ├── types.ts           # BehaviorEvent + PlatformAdapter 接口
│       └── platforms/
│           ├── bilibili.ts    # bvid 提取、卡片选择器、动作关键字
│           ├── douyin.ts      # aweme_id 提取、卡片选择器、动作关键字
│           ├── twitter.ts     # tweet_id 提取、卡片选择器、动作关键字
│           ├── xiaohongshu.ts # note_id 提取、卡片选择器
│           ├── youtube.ts     # video_id 提取、卡片选择器、动作关键字
│           ├── zhihu.ts       # question / answer / article ID 提取与动作关键字
│           └── reddit.ts      # Reddit post/comment URL、subreddit 与动作关键字
└── tests/
    ├── collector-helpers.test.ts
    ├── dist-module-specifiers.test.ts
    ├── manifest-assets.test.ts
    ├── popup-helpers.test.ts
    └── service-worker-buffer.test.ts
```

## 当前能力

### `content/kernel.ts`

负责内容脚本侧采集：

- 点击与搜索
- 视频 `view` / `pause` / `seek`
- 页面快照 `snapshot`
- 滚动 `scroll`（页面滚动与内部 feed / modal 滚动容器都会捕捉）
- 卡片停留 `hover`
- 评论 / 点赞 / 投币 / 收藏 / 分享 / 关注 / 不感兴趣意图事件
- 动作点击会在 document capture 阶段先记录，再定位到最近的按钮 / 链接 / `aria-label` 节点，把外层文案交给平台 adapter 识别；真实站点里点中按钮内部图标或文字节点，或平台自己的 React handler 阻断冒泡，也能归因到外层动作

同时支持 SPA 导航感知，在 URL 变化时重新发送快照并重绑视频监听。B 站 / 抖音 / YouTube 会绑定 `<video>` 产生 `view/pause/seek` 与视频停留 click；小红书和 X 当前按页面能力跳过视频监听。

### `service-worker.ts`

负责后台缓冲与上报：

- 接收内容脚本事件
- 高频事件去重
- 强信号行为优先 flush；`feedback/follow/share/view` 以及带 `watch_seconds` / `video_duration_seconds` / `dwell_source` 的 `click` 会尽快上报
- `chrome.alarms` 周期性批量发送
- 发送失败时把事件回填到缓冲区
- flush 成功后检查一次待发通知
- 缓冲为空时也会周期轮询高置信通知
- 每次 service worker 冷启动都会启动 B 站和抖音 Cookie 同步；如果已配置后端暂时不可用，会通过 `chrome.alarms` 以 1 分钟间隔重试，成功同步后恢复为 60 分钟刷新
- 会启动知乎任务轮询；收到 runtime stream 的 `zhihu_task_available` 后立即打开带 `openbiliclaw_zhihu_task` 标记的知乎任务 tab。`bootstrap_events` 初始化 / 事件 smoke 使用前台 tab，会把浏览记录、收藏夹和个人动态条目回传到 `/api/sources/zhihu/task-result`；只有后端任务 payload 显式带 `profile_update=true` 时，新增条目才会由 API 自动写入 memory 并进入增量画像 pipeline。`search` / `hot` / `feed` / `creator` / `related` discovery 使用后台 tab，并把知乎候选分别回传为 `zhihu_search` / `zhihu_hot` / `zhihu_feed` / `zhihu_creator` / `zhihu_related`。任务 tab 不启动普通 `startCollector`，因此 CLI smoke、guided init 和 discovery 任务不会额外写入 `/api/events`
- 会启动 Reddit 任务轮询；收到 runtime stream 的 `reddit_task_available` 后立即打开 / 复用带 `openbiliclaw_reddit_task` 标记的 Reddit 任务 tab。`bootstrap_events` 会先读 `/api/me.json`，再用当前浏览器的 `reddit.com` 登录态读取 saved、upvoted 和 subscribed subreddit，回传 `reddit_saved` / `reddit_upvoted` / `reddit_subscribed` 初始化信号；`search` / `hot` / `subreddit` / `related` discovery 则读取同源 `.json` endpoint，回传 `reddit_search` / `reddit_hot` / `reddit_subreddit` / `reddit_related` 候选到 `/api/sources/reddit/task-result`。dispatcher 在 tab load 后会对 content script listener 做短重试，吸收真实页面 complete 早于 isolated script 注册的时序抖动；service worker 冷启动和热 reload 后会在顶层启动 Reddit poll alarm，避免只靠 `onInstalled/onStartup` 导致新来源不轮询
- 以 `client=background` 连接 `/api/runtime-stream` 后，如果后端发现本地缺少 B 站 Cookie，会收到 `bilibili_cookie_sync_requested`；如果 `[sources.douyin].enabled=true` 且缺少抖音 Cookie，会收到 `douyin_cookie_sync_requested`。扩展收到后会立即执行对应 Cookie POST。后端也把这条 WebSocket 作为 extension presence 信号：连接建立时允许后台 LLM 工作，最后一个连接断开后进入 `extension_disconnect_grace_seconds` 宽限；服务端 reader 会主动 `receive()` 检测 idle disconnect，避免浏览器断开后 presence 卡住
- 收到 `extension_e2e_run` 后会调用 `background/e2e-runner.ts`：按目标平台打开或复用标签页，复用时也会导航到平台稳定入口，等待页面 ready，再向 content script 发送 `OBC_E2E_EXECUTE`；runner 会先等待捕捉 buffer settle 并 flush，再把执行结果 POST 到 `/api/extension/e2e/result`，sendMessage / tab load / 整体运行都有独立超时，避免单个平台页面卡住整个后端请求
- 连接 `/api/runtime-stream` 之前会先 HTTP `GET /api/ping`（2 秒超时）做一次活性探针，仅在后端可达时再 `new WebSocket(...)`。这样 fresh-install 用户先装扩展、后启动后端时，`chrome://extensions` 不会被浏览器层 WebSocket 失败计入「错误」徽标；探针失败仍走原有的 5s → 60s 指数退避兜底重连。探活不再打 `/api/health`：health 会同步等一次 embedding 实探（冷缓存可达数秒），2 秒预算下会把健康但冷启动的后端误判为掉线；`/api/ping` 返回 404（旧后端）时回退 `/api/health`（12 秒预算）
- 后端不可达时会在扩展工具栏图标上打一个浅灰 `!` badge 作为可视提示，WebSocket 首次连上后自动清除；popup 内仍会显示「后端还没开张，先运行 `openbiliclaw start`」
- Cookie 监听器幂等注册，避免 onInstalled / onStartup / 冷启动重复挂载导致同一次登录触发多次 POST
- 点击扩展图标时优先打开 Chrome side panel；Firefox 构建会改用 `sidebar_action` 打开同一套 `popup/popup.html`
- 通知和认知提醒也会优先把用户带回插件 side panel / sidebar 上下文
- 在推荐通知之外，认知变化通知会打开带 `?tab=profile` 的插件页面，直接落到画像视图
- 惊喜推荐通知现在会打开带 `?tab=recommend&delight=<bvid>` 的插件页面，落到对应的首屏惊喜卡，而不是只把人丢回通用推荐页
- `interest.probe` 和 `avoidance.probe` 都留在 side panel inbox 内处理，不走系统级 OS toast，避免探针在浏览器外打扰用户

### 扩展捕捉 E2E 自检

这条链路用于验证“捕捉层是否真的正常”，不是给后端灌假事件：

```text
POST /api/extension/e2e/run
  -> runtime-stream: extension_e2e_run
  -> service worker e2e-runner
  -> 打开或复用 tab，并归位到平台入口
  -> content script OBC_E2E_EXECUTE
  -> 真实 DOM click / scroll / snapshot
  -> content/kernel.ts + PlatformAdapter 自然捕捉（click capture + 内部滚动容器）
  -> service worker buffer
  -> POST /api/events
  -> runner flush buffer
  -> POST /api/extension/e2e/result
  -> 后端按 run window 匹配 events 表
```

请求示例：

```json
{
  "platform": "douyin",
  "action": "share",
  "url": "https://www.douyin.com/",
  "timeout_ms": 45000,
  "allow_state_changing": false
}
```

默认只允许不改变账号状态或副作用极低的动作：`snapshot`、`scroll`、普通 `click` 和安全分享入口点击。`like`、`favorite`、`follow`、`comment`、X 的 `repost` 等会改变平台状态的动作，必须显式传 `allow_state_changing=true`。为了避免误测，`share` 只匹配安全分享入口的 click / share 捕捉结果，不会把 X 的转推 / repost mutation 算作普通分享成功。

content executor 的 selector 策略按平台收敛在 `src/content/e2e-executor.ts`：优先找当前页面可见的 action button / role button / aria-label，并排除“取消点赞”“已收藏”“Following”等反向或已激活状态；X / 小红书这类图标按钮会走平台专属 selector fallback，页面元素慢渲染时会在短窗口内重试。执行器只返回“是否点到了 DOM”，最终成功标准仍以后端是否在 `/api/events` 中看到对应平台、动作和时间窗口内的真实事件为准。

### B 站搜索兜底任务桥

`src/background/bili-task-dispatcher.ts` 会轮询后端 `/api/sources/bili/next-task`。这条链路不是 B 站 discovery 的常驻主路径：后端 `BilibiliExtensionSearchProducer` 只有在 API search 已进入冷却或 `search_dom_fallback_remaining()>0`、扩展 presence 在线、候选池低于配额且近期没有同类任务时才会入队。service worker 同时监听 runtime stream 的 `bili_task_available`，收到后立即 `pollBiliTaskNow()`，alarm 轮询作为兜底。

扩展领取到 search task 后，会打开后台 tab：

```json
{
  "task_id": "...",
  "type": "search",
  "query": "机械键盘 声音",
  "limit": 20,
  "page_size": 20
}
```

dispatcher 导航到 `https://search.bilibili.com/all?keyword=...`，等 tab ready 后发送 `BILI_TASK_EXECUTE`；如果 Chrome 报 content script listener 暂未就绪，dispatcher 会在 8 秒窗口内短重试，吸收真实页面 `complete` 早于 isolated content script 注册的时序抖动。`src/content/bili/task-executor.ts` 不在 isolated world 里直连 B 站 API，也不伪造 WBI 签名；它只等待真实搜索页渲染出 `.bili-video-card` / `.video-list-item`，从 DOM 卡片里提取 `bvid`、标题、UP 主、播放数、封面、时长和简介，再用 `BILI_TASK_RESULT` 回给 service worker。service worker POST 到 `/api/sources/bili/task-result` 后，后端把结果写入 `discovery_candidates`，继续走共享 evaluator / admission，而不是由插件直接写推荐池。

真实联调可用两档验证：

- 手工任务：后端重启到包含该分支的代码，加载 `extension/dist/`，让 `/api/sources/bili/next-task` 返回 search task；扩展应打开后台 B 站搜索页，最终 task 状态变为 `completed`，对应候选带 `source_strategy="bili-extension-search"`。
- 自动触发 E2E：`BILI_EXTENSION_E2E=1 .venv/bin/pytest tests/test_bili_extension_browser_e2e.py -q -s`。该测试启动临时 FastAPI app + 临时 SQLite，使用 Playwright 持久上下文加载 unpacked extension，等待真实 runtime-stream presence，把进程内 `BilibiliAPIClient` 置入 search cooldown，再调用真实 `BilibiliExtensionSearchProducer` 入队；扩展必须领取任务、打开真实 B 站搜索页并回传 DOM 结果。测试不使用生产数据库，也不需要生产 debug endpoint。

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

小红书 discovery notes 不再由 API 直接写入 `content_cache`。被动 URL / note metadata、search / creator 任务结果和 bootstrap 中可作为内容候选的 notes 会先写入后端 `discovery_candidates` 待评估池；后端随后调用共享 discovery evaluator 混合评估各平台候选，达标后才 admission 到推荐池。这样 XHS 与 B 站、抖音、YouTube 的“用户会不会喜欢”判断处于同一环节。

`/api/sources/xhs/observed-urls` 的返回里，`accepted` 只表示本次接收的有效小红书 URL 数，`enqueued` 表示随请求携带的 note metadata 中有多少条进入 `discovery_candidates`。入池后的喜好评估和 admission 是异步完成的，插件端不应把 `accepted` 理解成“已经可推荐”。

真实浏览器联调可用 `XHS_BROWSER_E2E=1 .venv/bin/pytest tests/test_xhs_browser_e2e.py -q -s`。默认要求后端在 `http://127.0.0.1:8420`，Chrome CDP 在 `http://[::1]:9222`；如果 9222 已被其它 Chrome 占用，可启动另一个带扩展的 Chrome 并设置 `XHS_BROWSER_E2E_CDP=http://127.0.0.1:<port>`，后端地址也可用 `XHS_BROWSER_E2E_BACKEND=...` 覆盖。

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

dispatcher 等待首页 ready 时会同时处理两种情况：正常的 `chrome.tabs.onUpdated(status="complete")`，以及抖音 SPA 没有再发完整 `complete` 事件的 fallback timer，避免任务卡住直到 `task_timeout`。search 任务按关键词数计算超时窗口，单关键词至少 180 秒，覆盖首页打开、DOM 搜索触发、搜索结果页路由确认、页面自身响应和 DOM 解析的真实耗时；后端 `DouyinPluginSearchClient` 默认也等 180 秒，避免插件刚开始执行 DOM 操作就被后端清成 stale。`src/content/douyin.ts` 会尝试触发页面搜索 UI、热点入口点击或推荐流滚动；search 会区分 `ui_triggered`（已提交）和 `search_navigation_ok`（URL 已进入真实搜索结果路由），防止搜索建议或登录弹窗被误判为搜索结果页。`src/main/dy-fetch-tap.ts` 先作为 MAIN-world 被动 fetch / XHR tap，把页面自己发出的 search / related / feed 响应转成候选，feed 兼容当前页面实际发出的 `/aweme/v2/web/module/feed/` 响应，search 兼容 `/aweme/v1/web/general/search/stream/` 返回的 chunked JSON。search / hot / feed discovery 不主动访问 `/search/...`、`/hot/...` 快捷 URL；search 在被动 fetch tap 和 DOM 解析不足时会调用已登录页面的 search API bridge 兜底，hot 会把 hot board 的 `group_id` 作为 `seed_aweme_id` 透传给扩展，优先执行带 seed 的热词，并在 DOM 点击 / 被动监听不足时用已登录页面的 related API bridge 拉取相关视频，feed 不主动调用 API bridge。搜索结果以 `scope="dy_search"`、热点结果以 `scope="dy_hot"`、首页推荐结果以 `scope="dy_feed"` 回写到 `dy_tasks.result_json`，不会转成初始化画像事件；content script 会在回传前按目标 scope 过滤候选，避免首页 feed 响应混入 search / hot 结果；`DouyinPluginSearchClient` 会把这些候选映射成 aweme-like JSON，分别以 `dy-plugin-search` / `dy-plugin-hot-related` / `dy-plugin-feed` 进入 `discovery_candidates` 待评估池，再由后端共享 evaluator 判定是否进入推荐池。插件任务空 / 超时 / 失败时默认返回空结果，direct-cookie fallback 仅保留给显式诊断路径；如果真实 search 响应带 `search_nil_info.search_nil_item="hit_shark"` 且无 `data/aweme_list`，会按抖音反爬空结果处理。

CLI 入口：

- `openbiliclaw search-douyin -k 猫 --max-items-per-keyword 10 -w 180`：真实 smoke 插件搜索召回。
- `discover-douyin --source hot --limit 3 --no-cache --no-evaluate`：真实 smoke 热榜 related 召回。
- `discover-douyin --source feed --limit 3 --no-cache --no-evaluate`：真实 smoke 首页推荐流召回。
- direct-cookie `discover-douyin --source search` 如果遇到空结果，可用 `search-douyin` 判断登录浏览器路径是否仍能拉到候选。

### `popup/`

`popup/` 目录当前承载 side panel 页面，已具备：

- 后端连接状态检查
- 设置页「后端地址」（默认 `127.0.0.1`，接受裸 IPv4 / 主机名）和「后端端口」（默认 `8420`，仅接受 `1-65535` 的完整十进制整数）由 `popup-backend-config.js` 一起写入 `chrome.storage.local`；popup 自身的 `/api/...` HTTP 请求与 `runtime-stream` WebSocket，以及 service worker / cookie 同步 / 各源任务派发都通过 `apiUrl()` / `wsUrl()` 在调用时解析当前 origin，service worker 通过 `chrome.storage.onChanged` 同步收到变更并立即重连。endpoint 不会写入后端 `config.toml`；本机改端口时用 `openbiliclaw start --port <同一端口>`。Chrome Web Store / AMO 发布包为缩短审核和降低权限提示，默认只声明 `127.0.0.1` / `localhost` 后端 host 权限；连接局域网其他机器或远程域名需要带对应 host 权限的开发者构建，或后续补充 `optional_host_permissions` 的用户授权流程
- 顶部手机图标会打开移动端二维码面板，二维码完全在 popup 本地生成，指向当前插件后端地址的 `/m/`；打开后的 `/m/` 页面已带 PWA manifest 与 iOS Web Clip 元数据，可从手机浏览器保存到主屏幕；当前不提供离线缓存，仍需手机能访问运行中的本地后端。如果当前 host 仍是 `127.0.0.1` / `localhost`，面板会提示手机通常无法访问，需要先把插件后端地址改成电脑局域网 IP；在 460px 以下侧边栏宽度，顶部 Web / 二维码 / 消息 / 设置按钮会换到品牌区下一行靠右排列，避免和标题 / 状态徽标重叠
- 设置页调度区的「停止后台 LLM 请求」写入 `scheduler.enabled=false`；开启后会暂停 daemon-owned 定时发现、候选池预计算和画像更新里的 LLM / embedding 调用，推荐列表不会自动补充新内容，候选池为空时可能暂时没有推荐。「关闭浏览器后停止后台」写入 `scheduler.pause_on_extension_disconnect=true`，断开宽限秒数写入 `scheduler.extension_disconnect_grace_seconds`；所有扩展窗口断开并超过宽限期后，后台 LLM / embedding 工作暂停，重新打开浏览器后恢复。手动刷新和显式 CLI / API 操作仍按用户动作执行
- 从 `/api/recommendations` 拉取推荐列表
- 从 `/api/profile-summary` 同步 `speculative_interests` 与 `speculative_avoidances`，分别渲染待确认兴趣和待确认避雷方向；正向兴趣项会保留 `probe_mode` / `challenge`，profile 页面点击“喜欢”会带 `surface="profile"`，不和 runtime inbox 的默认 probe 确认混在一起
- 收到 `avoidance.probe` runtime 事件后在 inbox 渲染避雷确认卡；`confirm` 调 `/api/avoidance-probes/respond` 且语义为“确实不喜欢”，`reject` 表示“不排斥”，`chat` 进入 `scope=avoidance_probe` 的 durable turn
- 设置页会通过 `/api/config` 读取并保存后端配置，保存后请求后端热重载；当前覆盖 LLM provider/key/model、LLM 显式备选 provider、DeepSeek reasoning、OpenRouter headers、embedding provider/key/model/显式备选 provider、per-module LLM override、B 站浏览器、通用 source 浏览器、Bilibili / 小红书 / 抖音 / YouTube source 开关、各源 discovery 预算、数据目录、SQLite 路径、调度、后端自动更新开关与检查间隔、候选池平台配比、真实 refresh / proactive push / speculator idle 频率、猜测兴趣和避雷探针参数、完整日志路径和日志清理参数
- 成功读取 `/api/config` 后，popup API 会把配置快照写入 `chrome.storage.local["openbiliclaw.config_cache"]`。后端离线时设置页会读取缓存填表，并显示缓存时间；没有缓存时显示错误横条且不伪造默认值
- 后端返回 `degraded=true` 时，设置页会在表单顶部展示降级原因和 blocking issues，保存按钮显示“保存并提示重启”；保存响应带 `restart_required=true` 时用 warning tone 提示用户重启 daemon
- 设置页的“按已有信号建议比例”会把当前页面上尚未保存的平台开关和比例一并 POST 到 `/api/config/source-share-suggestion`，按本地事件库的平台分布填入 B 站 / 小红书 / 抖音 / YouTube 占比，用户仍需点击保存才写入 `config.toml`
- 设置页保存配置时会保留后端已有的高级字段：`save_config()` 会串行化 scheduler speculation / auto-update 和 logging unmanaged cleanup 字段，避免 UI 修改常用项时把隐藏高级项写回默认值
- 设置页“版本与更新”只展示后端更新状态并调用 `/api/update-status`、`/api/update/check`、`/api/update/apply` 的 backend target；插件版本行只读取本地 manifest 版本并链接 GitHub Releases。
- 推荐 tab 现已改成“换一批”，会调用 `/api/recommendations/reshuffle` 直接从 discovery pool 秒级换出一批新推荐
- 推荐 tab 滚到底时会调用 `/api/recommendations/append` 继续往下续 10 条，不会把当前这一屏直接替换掉；首次渲染、切回推荐 tab 和追加完成后也会再检查一次底部距离，避免停在底部时没有新 scroll 事件导致续页卡住
- 收到后台 `refresh.pool_updated` 时，推荐 tab 只更新池子数量、最近补货数量、方向提示和底部可换提示；移动 Web 空态也会用同一 runtime status 重新计算“还有多少可换 / 多少素材在整理”。不会调用 `/api/recommendations` 替换当前列表，用户已续页出来的历史内容会保留到下一次主动“换一批”或页面重新初始化。首次初始化推荐列表后会再读一次 `/api/runtime-status`，避免 `/api/recommendations` 从候选池 bootstrap 后仍显示 bootstrap 前库存
- popup API 现在会统一规范化推荐项，追加出来的 `cover_url` 也会被收敛成可直接加载的 `https://` 地址；推荐点击 payload 会保留 `content_id / content_url / source_platform`，因此 YouTube 等跨源卡片打开后也会被后端记成对应来源，而不是落回 B 站 BV 号语义
- 推荐、惊喜推荐和消息内封面图会通过 `popup-helpers.buildImageProxyPath()` 生成 `/api/image-proxy?url=...`，再用 `popup-backend-config.getBackendOrigin()` 拼成当前后端绝对地址；图片加载失败时保留已有 wrapper fallback，不让卡片布局塌缩
- `/api/recommendations/refresh` 仍保留为后台补货入口，用于继续往候选池里持续进货
- popup 推荐卡片现在不会再把空 `expression / topic_label` 补成固定占位文案；后端预生成没完成时，这两块会直接隐藏
- popup 的收藏 / 稍后再看 toggle 统一走 `createSavedToggleRegistry()`：同一 bvid 可以被多个按钮注册，任一按钮增删成功后所有可见按钮同步 `aria-pressed` / title / 文本；旧的懒加载 `GET /api/watch-later/{bvid}` / `GET /api/favorites/{bvid}` 结果如果发生在用户点击或收藏列表加载 / 移除之后会被忽略，避免状态回跳。收藏列表中移除条目也会反向同步惊喜横幅里的收藏按钮，推荐卡稍后再看也会与惊喜横幅稍后再看同步。注册表会在每次状态同步时剪除已脱离 DOM（`isConnected === false`）的按钮，并在推荐列表 / 惊喜横幅 `replaceChildren` 后调用 `pruneDetached()`，避免按钮随重渲染在注册表里无限堆积。
- 亮色 side panel 视觉系统：顶部 hero + inline 状态徽标、胶囊 tab、统一卡片体系，整体更贴近 B 站内容产品气质
- 推荐 tab：展示内容封面、标题、作者 / UP 主、`topic_label`、朋友式推荐文案，并通过“打开内容”跳转到 `content_url`；缺少 URL 时按 `source_platform` 对 B 站 / YouTube 做安全 fallback
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
- 插件 side panel、移动 Web 和桌面 Web 统一把 `pool_available_count` 当作真实可换数量；当 `pool_available_count=0` 但 `pool_pending_count>0` 时显示“找到 N 条素材，正在整理成可换内容”，不会把待评估 / 待分类 / 待文案 / 不可打开的素材数写成“可换”。`pool_pending_eval_count` 和 `pool_evaluated_pending_count` 只作为诊断与整理状态使用。
- 推荐 tab 头部现已进一步压缩成双层内容型入口：第一层只保留 `For You`、标题和 `换一批`，第二层把池子状态收成三枚紧凑 chips，让第一张推荐卡更早进入首屏
- 推荐 tab 现在还会在头部下方展示独立的“惊喜推荐”首屏卡位：popup 启动时会主动读取 `/api/delight/pending`，runtime stream 收到新的 `delight.candidate` 也会立刻刷新这张卡
- 推荐 tab 会展示候选池摘要：
  - `当前可换`
  - `最近补进`
  - `现在在忙`
  - 三条状态仍然保留，但文案已收短成更适合 chips 的形式，例如 `还有 151 条可换 / 刚补进 6 条 / 这会儿先不补货`
  - `当前可换` 只显示真实可立即换出的数量；待整理素材会进入“素材整理 / 现在在忙”语义，不会混进可换数字
  - refresh 还在跑时，状态 chip 会优先显示 `正在补货`，不再先落成 `这轮还没补进`
  - 点击 `换一批` 时，进行中的文案会直接进入“现在在忙” chip，而不是再额外挤出一条独立状态行
- 推荐卡片现已进一步改成更偏编辑式的内容流：封面、标题、推荐理由和操作区的层级被重新拉开，头部信息不会再和首张内容卡抢视觉主角
- 惊喜推荐卡会直接展示封面、hook、标题和惊喜理由，并提供 `看看 / 喜欢 / 不感兴趣 / 聊一聊 / 稍后看` 动作
- `看看` 会打开对应内容并把这次点击保留成稳定的本地已处理态；`聊一聊` 会在卡内展开 composer，通过 durable `/api/chat/turns` 写入 `scope=delight` turn，不再强制把用户切去聊天 tab
- `聊一聊` composer 在输入框失焦（焦点离开 composer）后会自动收起回操作按钮，省得展开后没法还原；已输入的草稿保留在 `chat_draft`，下次展开自动还原，正在发送的那条由 `sendInitiated` 守卫，点「发出去」时输入框先失焦也不会被收起误伤。桌面 Web `/web` 推荐卡 / 惊喜卡、移动 Web `/m` 惊喜卡同样支持失焦自动收起
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
- 聊天输入框内置多场景 placeholder 轮播，提示用户可以描述自己怎么看内容、喜欢 / 讨厌什么、近期观看行为、自我状态或注意力变化；输入框 focus 时暂停轮播，blur 且内容为空时恢复。聊天 tab 激活时隐藏底部活动栏，聊天历史区域改为 flex 填满输入框上方空间并独立滚动，输入框固定在 side panel 底部，窄屏下也能优先展示更多历史消息。历史记录会在 hydrate、追加新消息、替换 thinking 占位和切回聊天 tab 时自动滚到最新 turn，避免用户打开已有对话后还要手动拖到底部
- 惊喜推荐和兴趣猜测卡片内的 `聊一聊` 也会用 `scope=delight/probe` 写入 durable turn，回复完成后同步刷新对应卡片状态、画像摘要和最近动态；旧的 `/api/chat` 仍保留给兼容入口
- durable chat turn 写入 SQLite `chat_turns`，不再依赖 DOM、JS 内存或 `sessionStorage` 保留主聊天历史；惊喜推荐保留 `localStorage` UI 草稿、展开态和 per-delight `turns` 作为本地兜底，权威回复状态以后端为准
- 推荐、画像和聊天文案共享后端的 `ToneProfile`，基础风格是“老B友”，但会根据画像和近期反馈在信息密度、温度和梗感上动态调整
- 推荐、稍后、收藏、画像、对话五个 tab 已统一为同一套浅色卡片语言，推荐内容被提升为侧边栏首屏视觉重心

### 构建链路

- 运行时脚本不再直接把 `tsc` 的 ESM 产物交给 Chrome
- `scripts/build.mjs` 使用 `esbuild` 将各 content entry 和 `service-worker.ts` bundle 为可直接加载的单文件
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
- B 站搜索兜底 dispatcher / DOM executor helper（URL、任务校验、BV 提取、播放量归一化、结果卡去重）
- B 站搜索兜底 opt-in 浏览器 E2E harness（默认 skip，`BILI_EXTENSION_E2E=1` 才启动真实 Chromium）
- B 站 / 抖音 Cookie 自动同步的重试闹钟和幂等监听器
- manifest 图标资源存在性
- Firefox manifest 的 version 注入、`sidebar_action` 降级路径、AMO 数据收集类别声明、Firefox zip 打包清理和 AMO unlisted XPI 签名
- popup 设置页字段与 `/api/config` schema 的基础对齐
- popup API durable chat turn：`startChatTurn()`、`fetchChatTurn()`、`fetchChatTurns()` 会分别调用 `/api/chat/turns`、`/api/chat/turns/{turn_id}` 和列表接口
- popup 聊天布局：历史 hydrate 与切回聊天 tab 都会触发滚到底部，避免 hidden view 恢复后停在旧消息
- `dist/` 运行时脚本可被 Chrome 直接加载

## Release 分发

普通用户下载入口是 GitHub Latest Release 的 `openbiliclaw-vX.Y.Z` 聚合页：该页会同时展示当前后端源码 tag、最新插件 zip 和可用桌面安装包。`extension-v*` 仍是插件自动化通道 tag，用于构建、商店提交和排查发布流水线，不再要求普通用户在 Releases 列表里手动筛选。

插件内部 release 通道：

- 发布 tag：`extension-vX.Y.Z`
- Release 资产：
  - Chrome / Edge / Brave / 其他 Chromium 浏览器：`openbiliclaw-extension-vX.Y.Z.zip`
  - Firefox 140+ 正式安装：`openbiliclaw-extension-vX.Y.Z-firefox.xpi`
  - Firefox 140+ 临时调试 / AMO 输入：`openbiliclaw-extension-vX.Y.Z-firefox.zip`
- 用户下载入口：`openbiliclaw-v*` 聚合 Latest Release；维护者需要核对构建日志时再看对应 `extension-v*` release
- Chrome / Edge / Brave 打包脚本会先删除同名旧 zip，再重新压缩 `manifest.json`、`dist/`、`icons/`、`popup/`，避免重复打包带入残留文件
- `extension-v*` GitHub Actions release workflow 会同时运行 Chrome / Firefox 两条打包脚本，并默认用 `AMO_JWT_ISSUER` / `AMO_JWT_SECRET` 执行 `npm run sign:firefox:only` 生成 signed XPI；发布尾部调用 `.github/scripts/sync-aggregate-release.sh`，把插件 zip / xpi 同步到当前 `openbiliclaw-v*` 聚合 Latest Release，并把该聚合页重新标记为 GitHub Latest。Firefox 140+ 也可本地构建 / 临时加载：`npm run build:firefox` 生成 `dist-firefox/`，`npm run package:firefox` 生成未签名 `openbiliclaw-extension-vX.Y.Z-firefox.zip`；配置 AMO 凭据后，`npm run sign:firefox:only` 会把当前 `dist-firefox/` 提交 AMO unlisted 签名并输出可直接安装的 `openbiliclaw-extension-vX.Y.Z-firefox.xpi`
- v0.3.62 起，Chrome / Firefox 发布包移除 `http://*/*` 宽泛主机权限，只保留四个受支持内容平台和 `127.0.0.1` / `localhost` 本机后端权限，避免 Chrome Web Store 把插件标为“所有网站权限”。
- v0.3.64 起，Chrome / Firefox 发布包不再声明 `tabs` permission；后台任务仍可使用 `chrome.tabs.create/update/remove/onUpdated/sendMessage` 打开、导航和清理受支持平台任务页，发布包仅保留实际需要的最小 permission 集合。
- 插件更新不走后端自动更新 API：商店安装版本由 Chrome / Edge / Firefox 原生更新；GitHub Release 下载的 Chrome zip / Firefox signed XPI、开发者模式加载和临时加载用户按 release 页面下载新版并重新加载。

Chrome Web Store 上传自动化走官方 API v2，不使用第三方上传 action：

- 本地上传：`cd extension && npm run webstore:upload -- --zip openbiliclaw-extension-vX.Y.Z.zip`
- 本地上传并提交审核：`cd extension && npm run webstore:upload -- --zip openbiliclaw-extension-vX.Y.Z.zip --publish`
- GitHub Actions：手动运行 `Publish Chrome Web Store Package` workflow；默认只上传 zip，不提交审核，勾选 `publish` 才调用 Chrome Web Store `publish` API。
- 需要在本地环境变量或 GitHub Secrets 设置：`CHROME_WEBSTORE_CLIENT_ID`、`CHROME_WEBSTORE_CLIENT_SECRET`、`CHROME_WEBSTORE_REFRESH_TOKEN`、`CHROME_WEBSTORE_PUBLISHER_ID`、`CHROME_WEBSTORE_EXTENSION_ID`。
- `CHROME_WEBSTORE_REFRESH_TOKEN` 必须由拥有该 Chrome Web Store item 管理权限的 Google 账号生成，OAuth scope 为 `https://www.googleapis.com/auth/chromewebstore`。
- Chrome Web Store 详情页文案维护在 `docs/chrome-webstore-listing.md`；提交新版本、改安装路径或改项目入口时，将其中 `Detailed Description` 纯文本块复制到 Developer Dashboard 的商店详情页，避免公开页只展示无引导的短概述。
- Chrome Web Store 隐私权政策网址可填写 `https://github.com/whiteguo233/OpenBiliClaw/blob/main/docs/privacy.md`；该文档说明插件单一用途、权限理由、数据类型、本地后端数据流和无远程代码声明。

后端源码更新仍只通过 `backend-v*` tag 标记，桌面安装包仍由 `desktop-v*` workflow 构建；两者都会同步到 `openbiliclaw-v*` 聚合 Release，避免 GitHub Releases 首页只露出某一个通道。

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
- popup 能根据 `/api/ping`（连接徽章活性，404 回退 `/api/health`）、`/api/health`（embedding / profile 就绪）与 `/api/recommendations` 切换在线、空状态与推荐列表展示
- side panel 页面反馈按钮已能经 `/api/feedback` 写回推荐表和事件层
- side panel 现已支持 `推荐 / 稍后 / 收藏 / 画像 / 对话` 五个 tab，其中稍后再看和收藏列表与 PC Web、移动 Web 的保存语义一致
- side panel 聊天信号已进入后端学习链，但仍采用受控积累，不会因为单轮聊天立即重写画像
- side panel 聊天已支持 durable turn 恢复：主聊天、惊喜推荐内聊和兴趣猜测内聊在页面 reload 后会按 `turn_id` 从后端恢复 pending / completed / failed 状态
- side panel 推荐、画像和聊天回复现在共用“老B友”动态语气，不再固定成一套机械模板
- side panel 能根据 `/api/runtime-status` 切换“先初始化 / 正在补货 / 推荐可用”三态
- side panel 现在还能通过 websocket 看到“开始补候选 / 当前跑到哪个策略 / 刚补进几条新的 / 这批先换好了”这类实时运行状态
- service worker 现在会在高置信推荐出现时触发浏览器通知，并通过后端回写 `notification_sent`
- service worker 现在也会拉取认知变化通知；如果最近系统对用户形成了新的高置信理解，会发一条更克制的“阿B 又对你多看清了一点”提醒
- side panel 新版亮色布局已通过本地静态页面快照检查，推荐 / 稍后 / 收藏 / 画像 / 对话五个视图结构渲染正常
- 小红书 `bootstrap_profile` 任务已通过单元测试覆盖：dispatcher 识别任务类型并能跟随 profile URL 二次执行，executor 可从 mock `__INITIAL_STATE__` 的 saved / liked / history 分组提取 scoped notes，并能用 `partial` 批次在滚动任务中持续回传新增结果
- 抖音 `bootstrap_profile` 任务已通过扩展和后端回归覆盖：MAIN-world API harvester 可分页拉取收藏 / 点赞，dispatcher 形态的 partial 批次会在后端合并、去重并转成统一 memory 事件
- 抖音 `search` / `hot` / `feed` 任务已通过扩展回归覆盖：dispatcher 三类 discovery 都从抖音首页启动；search 会通过首页搜索框提交并用 `search_navigation_ok` 校验是否进入真实搜索结果路由；content script 声明 search / hot 均支持 DOM interaction + passive fetch tap + active API bridge，feed 仍是 DOM interaction + passive fetch tap；fetch / XHR tap 可被动转发页面自身 search / related / feed 响应，并按目标 scope 过滤结果；`search-douyin -k 猫 --max-items-per-keyword 10 -w 180` 可用于 smoke `dy_search` 候选，`discover-douyin --source search --keyword 猫 --limit 5 --no-cache --no-evaluate` 可预览 `dy-plugin-search` 候选
- 知乎 `bootstrap_events` / `search` / `hot` / `feed` / `creator` / `related` 任务已通过扩展单测覆盖：executor 能解析浏览历史、收藏夹、个人动态点赞/收藏，并能把 search_v3、热榜、首页推荐、作者页、问题相关 mock 响应归一化为 `zhihu_*` 候选；`discover --source zhihu` 可验证正式 producer 流程，`discover-zhihu* -n 10 -w 240` 可用于分支级真实插件 smoke

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
