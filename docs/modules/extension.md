# 浏览器插件模块

> popup 的 LLM 总并发 placeholder/读取/保存 fallback 同步为 4；候选评估并发可设 `1..3`（默认 3、每批 30 条、最多 90 条 raw 在途），后台容量仍由后端派生。DeepSeek Reasoning 在插件与桌面 Web 都以 `medium` 为默认选项，空值明确显示为「关闭」并原样保存。

## 模块范围

`extension/` 是浏览器插件子项目（Chrome / Edge / Brave 主构建，Firefox 与 Safari 独立构建），负责：

- 在 B 站 / 小红书 / 抖音 / YouTube / X / 知乎 / Reddit / Linux.do 等支持站点采集行为事件或执行来源任务（平台无关内核 + 平台适配器）
- 在 B 站 / 小红书 / 抖音 / YouTube / X / 知乎 / Reddit / V2EX 等支持站点采集行为事件或执行来源任务（平台无关内核 + 平台适配器）
- 通过 background service worker 缓冲并上报到本地后端
- 在 side panel 中展示连接状态、推荐结果、画像和聊天入口

### 配置页 HTML 结构约束

插件与桌面 Web 的设置面板都依赖浏览器原生 HTML 解析后再由脚本切换 `hidden` 状态。桌面 Web 平台源列表中的每个来源卡（尤其 Linux.do / V2EX）必须保持同级 `<article>` 节点，并在来源列表结束前闭合；来源卡不能包住后续的来源总览或其它 `data-settings-panel`。修改来源卡片时同步运行 `tests/test_desktop_web_linuxdo_settings.py`，防止标签嵌套导致配置页只剩 tab 栏。

当前里程碑进度：

| 子模块 | 状态 | 说明 |
|------|------|------|
| 统一品牌图标 | ✅ | Chrome / Edge / Brave / Firefox manifest 使用 16 / 32 / 48 / 128px 精确尺寸图标，side panel 顶部品牌标记、普通透明 PWA 图标、专用不透明 `maskable` / Apple 主屏幕图标、32px 根 favicon、首次设置页、桌面 Web、移动 Web 和 GitHub Pages 官网统一从 `assets/brand/openbiliclaw-icon.png` 派生。源图的半透明边缘已去除旧白底消光色；扩展图标、favicon 与 maskable 图标使用满幅品牌粉底，页面头图容器也用品牌粉承接透明圆角。旧字母 `B`、CSS 圆环和官网重复的内联 SVG favicon 已移除；社交分享图、Chrome Web Store 素材与 README / 官网截图通过 `build_social_preview_assets.py`、`capture_chrome_webstore_ui.py --refresh-docs` 和既有构建脚本确定性重建。 |
| 8.1 行为采集 | ✅ | `content/kernel.ts` + `shared/platforms/*` + `service-worker.ts` 已接通统一事件链；B 站 / 小红书 / 抖音 / YouTube / X / 知乎 / Linux.do 都通过 `PlatformAdapter` 产出同一 `BehaviorEvent` 形态，平台差异只保留在 selector、内容 ID 和 action 识别中；Reddit 与 Linux.do 另有只读插件任务源；click 监听在 capture 阶段执行，scroll 同时覆盖页面和内部滚动容器 |
| 8.1 行为采集 | ✅ | `content/kernel.ts` + `shared/platforms/*` + `service-worker.ts` 已接通统一事件链；B 站 / 小红书 / 抖音 / YouTube / X / 知乎 / V2EX 都通过 `PlatformAdapter` 产出同一 `BehaviorEvent` 形态，平台差异只保留在 selector、内容 ID 和 action 识别中；Reddit 通过插件任务源接入初始化 saved/upvoted/subscribed 信号和 discovery search/hot/subreddit/related；V2EX 普通页面只采集被动阅读行为，任务页由独立 dispatcher 执行四个只读 bootstrap scope；click 监听在 capture 阶段执行，scroll 同时覆盖页面和内部滚动容器 |
| 8.2 后端 API | ✅ | Python 侧 `/api/events`、`/api/health`、`/api/recommendations` 已可联调；`/api/events` 在 soul 画像明确未初始化时只返回 `not_initialized` 拒收结果，不写 memory，首轮画像信号由 guided init 的来源任务拉取 |
| 8.3 Side Panel | ✅ | 已切到 side panel 主入口，继续复用 `popup/` 页面承载推荐 / 内容库 / 画像 / 对话四个一级 tab；内容库内用「稍后再看 / 收藏 / 历史记录」三个语义子 tab，兼容旧 `?tab=watchLater|favorites|history` 入口。历史按点开、出现未点和最近移除三组分页读取 30 天本地事实，使用 opaque cursor 续页；同一内容的多个移除 context 同卡显示，收藏和稍后再看可独立恢复，封面 lazy + low-priority 走既有代理缓存。历史读取有 12 秒截止时间；续页失败保留已有卡片并显示可访问的重试提示，坏封面显示 SVG fallback。顶部功能区提供「手机版」入口（v0.3.154 起为手机图形 + 「手机版」文字标签，与相邻图标同款白底样式），按当前插件后端地址和 HTTP/HTTPS scheme 生成 `/m/` 扫码链接；460px 以下窄宽度会把 Web、二维码、消息、设置按钮换到品牌区下一行靠右排列，避免和标题 / 状态徽标重叠；如果当前后端地址仍是 `127.0.0.1` / `localhost`，会以同一 scheme 调用轻量端点 `GET /api/qr-info`（不触发 embedding readiness probe）并读取响应中的 `lan_ip` 字段，用局域网 IP 生成二维码，提示为 info 状态；后端优先返回 RFC1918 IPv4 并排除 `198.18.x.x` 等 VPN/TUN 地址，没有可用 IPv4 时回退 ULA / global IPv6，二维码生成器会把 IPv6 literal 包进 `[]`；移动 Web 推荐页首屏先渲染 `/api/recommendations`，再异步补 runtime status / activity / delight，慢请求不会让页面无限停在 loading；聊天改走后端 durable turn，Chrome 丢弃或切 tab 后可恢复；惊喜推荐、兴趣猜测和避雷探针的内联聊天也会按 `scope=delight/probe/avoidance_probe` 恢复 pending/completed/failed turn；主聊天与移动/桌面 Web 共用 `session=popup&scope=chat`，聊天 Tab 可见且在线时约每 2.5 秒增量刷新历史，内容未变化不重绘，阅读旧消息时保留滚动位置；底部「最近发生的事」活动栏在四个一级 Tab 始终可见，聊天记录区在剩余空间内独立滚动，输入框固定在聊天区底部且会轮播想法、口味、自我描述、近期状态等多场景提示语 |
| Durable 对话失败展示 | ✅ | side panel 的主聊天在 `turn.status === "failed"` 时优先渲染后端持久化的安全 `turn.error`，不把历史遗留 `turn.reply` 误当成功；惊喜/探针内联 turn 只有 `completed` 才显示成功并移除已处理探针，`failed` 显示 `turn.error`、恢复 handled/按钮状态并保留卡片供重试。 |
| Issue #147 聊聊口味 Markdown 渲染 | ✅ | 主聊天、惊喜推荐和兴趣/避雷探针内嵌聊天复用 `web/shared/dialogue-confirmation.js` 的安全 Markdown renderer；popup、桌面 Web、移动 Web 的 AI 回复支持加粗、斜体、标题、列表、代码块、引用和 `http(s)` 链接，原始 HTML / `javascript:` 等不安全内容不会进入 DOM，用户消息仍按纯文本展示。 |
| Issue #184 AI 文案换行保留 | ✅ | 除聊天 Markdown 外，推荐理由、惊喜理由、探针理由等 AI 生成文案在三端统一保留换行（`white-space: pre-wrap`），不再把多行输出显示成一整段。 |
| 对话确认入口（Wave C/D + 单队列 cutover） | ✅ | popup、移动 Web 与桌面 Web 共用 `web/shared/dialogue-confirmation.js` 渲染 `hypothesis` 卡片、纯提问气泡和普通文字 turn：卡片提供「准 / 不准 / 聊聊 / 稍后」四动作、可展开依据与原地结算态；纯数字、UUID、事件 / note 前缀、BVID 或裸哈希等只有机器 ID 的依据会整项过滤，过滤后为空则不渲染「依据」区。桌面「待聊确认」与插件保持同一套紧凑视觉：柔和品牌色折叠条、数字徽标、轻量箭头和单列小卡片，不再额外加入说明文案或桌面仪表盘式重容器；猜测卡片仍按标题、依据、结算状态、主次动作分层，430px 以下动作改两列，深浅主题、可见 focus 与 reduced-motion 继续沿用全局设计令牌。action 先乐观更新；同步 `200` 直接采用服务端状态，`already_settled`（包括相反 verdict）覆盖本地乐观结果。收到 `202 processing` 才复用各端既有 `fetchChatTurn` 按 `1s/2s/5s`（随后 5s）读取 durable turn，30 秒总截止；终态立即停，连续读取失败、截止或页面 abort 只把本地卡片标为 `retryable_error`，允许刷新/重试，不伪造 durable 失败。三端各自持有 action AbortController，页面卸载会终止轮询。popup 与移动 Web 的「待聊确认」列表调用 `GET /api/chat/pending-confirmations`，主动打开用 `session="popup"`；桌面端镜像相同语义并用 `session="popup"`，侧栏「聊聊口味」显示待聊计数。三端对话记录和待聊列表都使用有界独立滚动，重绘保留读者位置与已展开依据；聊天可见且在线时约每 2.5 秒增量刷新历史，快照未变化不重绘；移动端动作保持两列 44px 触控目标。待聊数字只在三端对话入口显示；service worker 不请求 `?count_only=1`，也不把待聊数写入工具栏，工具栏角标只表达后端不可达或未初始化。三端的画像/认知更新区均只读，主动确认只存在于 durable 对话卡片。后端 deprecated legacy 端点继续保留，新客户端不调用。 |
| Turn binding 与三端对话 surface（2026-08-01） | ✅ | 本行 supersede 上一行 Wave C/D 的历史移动端只读描述：popup、mobile web、desktop web 共用 `web/shared/dialogue-confirmation.js` 的 context preview、context bar、reply quote、opaque-evidence 过滤与 stick-to-bottom 规则。`聊聊` 只 POST `reply_to_turn_id`，服务端 canonicalize；context GET 只读，失败保留 draft/target。三端都为历史与待聊列表提供独立有界滚动，重绘恢复已展开依据；移动端额外恢复草稿与焦点，并保留卡片 action/processing poll。卡片进入 terminal state 后，各端在下一次历史同步中静默清除已完成 context，避免把预期结算误报成英文错误；已知 context error 统一优先显示中文。移动端聊天页的回顶按钮对 360px 窄屏保留额外垂直间距，不与发送按钮相交。 |
| Runtime stream 合并刷新 | ✅ | 插件 side panel、桌面 Web 和移动 Web 对 `activity.added` / `profile_updated` 等运行时事件做 debounce 与 single-flight；`refresh.pool_updated` 只合并池子状态并刷新 header / pool chips / 底部可换提示 / 空态文案，不再重拉推荐列表，避免覆盖用户已经 append 出来的历史卡片。`dy_task_available` 是扩展 dispatcher 的 transport wake-up，桌面 Web 不把它投影成用户可见活动。插件 side panel 从离线转在线时（包括首次 `/api/ping` 瞬时失败但 `/api/runtime-stream` 随后连上的竞态）会立即调度推荐刷新；popup 离线期间会每 1 秒轻量重探测 `/api/ping`，runtime-stream 自身也固定每 1 秒重连，成功后停止轮询并切回在线刷新流程，避免后端已启动但插件仍停在“后端还没开张”的旧空态。 |
| 兴趣挑战探针 UI | ✅ | `interest.probe` 和 `speculative_interests` 会保留后端的 `probe_mode` / `challenge` metadata；profile 页确认会向 `/api/interest-probes/respond` 传 `surface="profile"`，写回为 `profile_confirmed`，而 inbox / runtime probe 卡片确认保持默认 `probe_confirmed`。插件 side panel、移动 Web 和桌面 Web 会把普通 `near` 兴趣探针与 `lateral/bridge/wildcard` 挑战探针拆成不同样式和提示：普通兴趣强调继续探索，挑战探针提示“把口味往侧边推一点”，区别于避雷探针。四个可见动作固定为「确认喜欢 / 暂时搁置 / 确认不喜欢 / 多聊聊」，分别提交 `confirm / defer / reject / chat`；用户处理同一 domain 后，三端会用 handled probe key 立即从 inbox、画像页和 runtime hydration 里隐藏该探针，避免后端旧快照/缓存再次把它展示出来。 |
| 避雷探针 UI | ✅ | popup inbox 支持 `avoidance.probe`，四个可见动作固定为「确认避雷 / 搁置避雷 / 不是雷点 / 多聊聊」，分别提交 `confirm / defer / reject / chat`；画像页显示 `speculative_avoidances` 的待确认避雷方向，确认后通过 `/api/avoidance-probes/respond` 写回后端。插件 side panel、移动 Web 和桌面 Web 会用避雷专属样式和“少看这类 / 猜错点不是”提示，区别于正向兴趣试探。移动 Web 在任一探针按钮点击后会锁住同一卡片其它动作，避免一次 active 探针被连续提交；三端也会在本地记录 handled 避雷 key，使已处理 domain 不再从 profile summary、pending probes 或 runtime stream 重复水合；消息收件箱空态不会重建 header，X 关闭入口保持可用。 |
| 封面图代理加载 | ✅ | side panel 的推荐卡片、惊喜推荐和消息封面会用当前配置的后端 origin 拼接 `/api/image-proxy?url=...`，不再直连平台 CDN，也不再设置 `referrerPolicy`。B 站搜索采集侧会拒绝懒加载 `data:` 占位图（宁可留空由后端后续摄入补真图，v0.3.162+）。 |
| 惊喜推荐平台标识 | ✅ | 消息流惊喜卡和 delight banner 均显示来源平台 chip（`platformDisplayName`，popup-helpers.js），无封面时也可见；桌面 Web 的惊喜大卡平台徽章同步改为不依赖封面存在（v0.3.162+）。 |
| X 推荐卡来源与文字卡 | ✅ | 插件 side panel、移动 Web 与桌面 Web 会把 `x` / `twitter` / `x.com` / `twitter.com` 统一归一为 `source_platform="twitter"`，标签显示 `X (Twitter)`，不再退成 Web 或 B 站；X tweet / thread 或无有效封面的推荐使用 `body_text` / title 渲染文本卡，桌面 Web 点击上报同步携带 `content_id` / `content_url` / `source_platform`。 |
| 知乎候选链接保真 | ✅ | 知乎任务 executor 对站内 API 响应做 lossless JSON 解析，把超过 JS 安全整数范围的裸整数 token 先转成字符串；归一化 discovery / 收藏 / 动态条目时也会优先从 URL 字符串解析 question / answer / article ID，再退回 JSON 字段，避免 19 位 question id 被 `Number` 舍入后拼出不可打开链接。 |
| 惊喜推荐正向保留 | ✅ | 插件 side panel、桌面 Web 和移动 Web 对惊喜推荐采用同一反馈语义：`喜欢 / 收藏 / 稍后再看 / 聊一聊` 保留候选在队列中；`去看看` 当场保留卡片但会上报 `view` 标记已读（三端统一，下次队列重灌不再出现）；`不感兴趣 / 忽略 / 关闭` 才立即移出当前队列。`popup-helpers.getDelightUiState()` 显式输出 `show_status / show_actions / like_pressed / like_disabled`，side panel 分开渲染结果和动作；已喜欢候选无论来自本地成功、队列重灌还是 `delight.liked` 实时事件，都显示结果、保留完整动作组，并只将 like 设为 `aria-pressed="true"` 与 duplicate-disabled。服务端非 pending 状态在队列合并时优先于本地 pending，失败的 like 不写入选中态。三端默认加载数量统一读取 `[scheduler].delight_queue_limit`，桌面 Web 设置页保存后插件和移动端随下一次队列拉取同步生效。 |
| 推荐点击幂等身份 | ✅ | side panel、移动 Web 与桌面 Web 的 `/recommendation-click` pending request ID 都以 `recommendation_id + content_id/bvid` 为稳定身份；只有内容没有稳定 ID 时才使用去 fragment 的规范化 URL fallback。小红书等来源的签名 token 或跳转 URL 在重渲染/响应丢失重试时可以变化，但同一 concrete click 仍复用原 request ID，不会重复学习。 |
| 公开事件 ID 完整性 | ✅ | `/api/events` 每个 event 的 `event_id`、`/api/feedback` 与 `/api/recommendation-click` 的 `request_id` 均为必填 1–400 字符。MV3 buffer 在事件创建时补 ID 并随 `obc_event_buffer` 持久化，失败回填/`not_initialized` 停车场/worker 重启继续复用；popup、移动 Web、桌面 Web 的推荐反馈、点击和 saved-card 行为命令使用 pending ID store，只有 accepted/成功响应才删除。saved-card like/dislike/comment 的 retry identity 使用 item + action + note，同一动作重试稳定，新的独立动作重新生成。 |
| Firefox 140+ 支持 | ✅ | `manifest.firefox.json` 使用 `sidebar_action` 承载同一套 popup UI，`openExtensionUi()` 按 Chrome sidePanel -> Firefox sidebarAction -> tab 降级；Firefox manifest 在构建时注入主 manifest version，并声明 AMO 所需 `data_collection_permissions`。固定使用项目自有 Gecko ID `openbiliclaw-firefox@whiteguo233.github.io`，签名凭据必须来自拥有该 ID 的 AMO 账号。GitHub Release 链路继续通过 `web-ext sign --channel=unlisted` 生成可直接安装的 signed XPI；公开商店则由独立 `Submit Firefox AMO Listed Package` workflow 使用 `amo-metadata.json`、同步后的隐私政策和同 commit 可复现源码包提交 `listed` 审核，并在结束前通过 AMO API 核验版本 channel。AMO 版本号跨 listed / unlisted 全局唯一，因此公开提审必须使用尚未用于 unlisted 签名的新扩展版本。 |
| Safari Web Extension 支持（issue #156） | ✅ | `manifest.safari.json` + `build:safari` 产出自包含 `dist-safari/`，`convert:safari` 调 Apple `safari-web-extension-converter` 生成 Xcode 工程（仅 macOS + Xcode 可用）。Safari 无 side panel / OS 通知 / `world:"MAIN"` 内容脚本，改用 `action.default_popup` 承载同一套 popup UI，去掉 `side_panel`/`sidePanel`/`notifications` 与 `world` 字段并保留 `alarms`/`scripting`/`cookies`/`storage`；service worker 对 `chrome.notifications`/`chrome.alarms` 注册加空值守卫，Safari 构建经 esbuild banner 注入 `browser → chrome` shim（Chrome/Firefox 构建不带 banner）。MAIN-world tap 改由 `content/safari-page-injector.js` 以 page-context `<script>` 桥接注入，Cookie 同步改为全量读取 + JS 域过滤以规避 Safari `cookies.getAll({domain})` 精确域差异。全链路 build → convert → xcodebuild 已验证，构建/签名/限制矩阵见 [safari-extension-build.md](../safari-extension-build.md) |
| 自动任务标签页默认静音（issue #163） | ✅ | 新增 `background/task-tab.ts` 共享助手 `createTaskTab()`：所有由后台 dispatcher 自动打开的标签页（B 站搜索兜底、小红书、抖音、知乎、Reddit、Linux.do、V2EX、微博、YouTube、原生保存任务与新开 E2E 平台页）创建后立即 `tabs.update(tabId, { muted: true })`，避免抖音等自动播放页面在无人值守时突然出声。Chrome 的 `tabs.create` 不接受 `muted`（Firefox 100+ / Safari 14+ 接受），因此统一走「先 create、再 update 静音」的跨引擎路径；静音状态跨后续 `tabs.update` 导航保持，用户可随时在标签栏手动取消静音，静音失败不影响任务执行。复用已有用户标签页（原生保存 / E2E 的 reuse 分支）保持原样，不会改用户自己标签的静音状态。 |
| 来源周期回拉逐源开关 | ✅ | 插件 side panel 与桌面 Web 的平台源配置页为每个支持周期回拉的来源增加“允许扩展周期回拉”开关，默认关闭；调度页提供总开关 `source_incremental_enabled`，后端同时检查总开关与来源开关，只有两者都开启才入队周期 bootstrap 任务。关闭某来源时其 scheduler-owned 待执行任务会被取消。 |
| 收藏夹 / 稍后再看 | ✅ | 推荐卡和 delight banner 的「时钟=稍后再看」「星星=收藏」统一把 canonical identity 交给 `/api/saved/*`；optimistic 状态只代表本地保存，平台失败不取消按下态。独立「稍后 / 收藏」页采用后端状态驱动：六个平台都保留手动同步入口；`pending` 只有在带非空 task ID 时才视为执行中并禁用重复动作，空 task ID 仍是可手动同步的本地保存；`unsupported_content_type` 才是 local-only / 无按钮，`unsupported_adapter_missing` 仅作滚动升级重试兼容；`extension_required` 显示连接已安装登录态插件的指引和重试，成功态展示真实 `resolved_target`。页面级「同步未同步内容（N）」继续保留，任务轮询完成后按平台显示成功/总数。移除只删本地 membership。「全部稍后看」只移除本地保存成功项。设置页默认关闭「保存时自动同步到对应平台」，首次开启确认账号写入警告；手动同步不受开关影响。saved/config 的单个 Abort deadline 覆盖设备会话交换与 401 强制刷新；批量同步和重试加载会在重渲染前捕获列表级焦点并优先还原到同一动作。 |
| 原生保存验证边界 | ✅（6/6 executor + 真实账号验证） | 本地 / CI / 默认 smoke 只验证默认关闭、local-first membership、列表与任务状态。六平台真实 favorite / watch-later 只能在 `allow_state_changing=true` 且 exact platform/action/public content ID/expected target 的精确命名授权同时存在时，经 production durable broker 执行。Task 10 为 trusted-local `/api/extension/e2e/run` 增加 dedicated 模式：exact envelope 与 generic actions 互斥，扩展仅提交一个 canonical item 到 `/api/saved/{action}/sync`、严格关联同一 task/item/resolved target，再用 six-field callback 回传。通用捕捉 E2E runner 即使收到有效 envelope 也固定拒绝 favorite/bookmark。Reddit / X / YouTube / 小红书 / 抖音 / 知乎六个 executor 均已接入并完成 fixture；登录态只存在于已安装扩展，job 不含账号 ID、Cookie 或账号凭据，小红书仅可携带 saved membership 已有的公开笔记导航 `xsec_token/xsec_source`，授权与结果仍只有安全字段。一般 runner 只在 tab 创建/加载阶段占用共享 mutex；XHS 手动 native-save 使用 exact route + identity/control fence，可越过后台 discovery mutex并由 single-flight poll 防重复领取。YouTube duplicate exact `OpenBiliClaw` rows 优先 checked proof，否则稳定复用一个且不删除列表；知乎按 current `收藏 / 已收藏` aria 状态同步到 `知乎收藏`，不支持命名收藏夹且永不点击初始“已收藏”；小红书支持 current `noteContainer/collect-wrapper`。完整矩阵以 runbook 为准。 |
| 原生保存不确定确认复核 | ✅ | 抖音 / 小红书 / YouTube / 知乎的 executor 在 mutation 后返回 `native_confirmation_not_observed` 时，runner 会先 abort 并 await 原 document sender，再重载 exact content URL；新 content document 必须通过 `NATIVE_SAVE_READY` 返回不同的 `document_instance_id`，随后才接收独立 `execution_id` 的 `verification_only`。平台 verifier 可只读打开必要弹窗并轮询 target 后置条件，但不创建容器、不点击目标项；知乎仅读取精确内容的全局 favorite 状态，任何复核路径都不点击“收藏”“已收藏”或“取消收藏”。明确命中后返回 `already_synced`，其它 verifier 终态或异常都保留原失败码。runner 在 `about:blank` 阶段先持久登记 task-tab owner，首次导航与 reload 都禁止普通 collector；callback 要求 2xx 并以同一 payload 有界重试，tab 删除不确定时保留 session 恢复记录。 |
| 持续补货与通知 | ✅ | 运行状态已接入 popup，service worker 会拉取高置信通知并回写发送状态 |
| 设置页源策略控制 | ✅ | side panel 设置页已按「模型 / 平台源 / 调度 / 高级功能 / 通用 / 日志」分 tab；模型 tab 直接维护 LLM v2 实例：同一 Provider 可新建多个渠道，编辑名称 / 模型 / Base URL / API Key / 协议选项，停用或删除前会检查默认链及模块链引用；实例编辑器可把当前未保存草稿发到无写入的 `/api/config/discover-models`，从该端点的 OpenAI-compatible `GET /models` 填充可编辑模型下拉，失败保留手填值；Effort 下拉是本地建议并允许任意手填。实例卡可按草稿执行 `kind="llm_instance"` 真实探测，默认链支持加入、移除、上移、下移及整链测试。首个已启用实例自动进入默认链；API Key 留空会保留已保存密钥，明确勾选才清除。模块自定义链在 side panel 只读展示并提供 PC Web 深链，保存会完整回传 `routes`，不会压扁桌面端配置。Embedding provider / 模型 / 凭据 / 探测继续留在模型 tab；新增高级功能 tab 固定包含「推荐增强 / 多模态处理 / 搜索词生成」三个 section。平台源 tab 按 Bilibili / 小红书 / 抖音 / YouTube / X / 知乎 / Reddit / 通用网页 / 候选池配比独立分块，可开关各平台 discovery，编辑各源预算和候选池占比，并按已有事件向后端请求推荐比例；调度 tab 暴露后台暂停、断开宽限、真实 refresh / probe 频率和猜测兴趣参数；日志 tab 除用单个「完整日志路径」编辑后端日志文件位置外，还内置「异常报警」区：拉取 `GET /api/diagnostics/alerts` 展示 LLM / Embedding 请求异常（限流、鉴权失败、超时、响应异常、全部实例失败、embedding 熔断），带错误/警告摘要徽标、中文错误码说明与合并计数，面板可见时每 15 秒轮询并消费 runtime-stream `diagnostics.alert` 实时刷新 |
| 海外网络模式设置 | ✅ | v0.3.165+ 通用 tab 与桌面 Web 对齐提供 `direct / system / custom` 三档和自定义代理地址；加载、保存及「测试网络」均提交同一 `network.mode + proxy` 草稿。默认直连明确忽略环境代理，只有 system 继承，custom 缺地址由后端 400 拒绝。 |
| 封面图评估设置 | ✅ | side panel 高级功能 tab 的「多模态处理」section 补齐 `[discovery]` 多模态评估控制：可开关候选封面参与 LLM 评估，并编辑图文 batch、封面最大边、JPEG 质量和图片准备超时；保存 payload 会保留已有 discovery 字段后覆盖 `multimodal_*` 参数，与桌面 Web 设置页保持同一配置面。图像 Embedding 能力是独立开关，P1/P3 依赖它而不依赖候选封面 LLM 评估。 |
| 搜索词生成模式选择器 | ✅ | popup 高级功能 tab 的「搜索词生成」section（与桌面 Web `/web` 设置页一致）新增「搜索词生成模式」下拉 `#cfgKeywordGenerationMode`，三档 经典 / 混合 / 灵感（option value `legacy` / `hybrid` / `inspiration`，两端 option 值 / 顺序 / 文案一致），并附「混合最贵」成本提示。加载从 `cfg.discovery.keyword_generation_mode` 回填（缺省 `legacy`），保存把该键写进 discovery payload（**在 `...(state.runtimeConfig?.discovery \|\| {})` 展开之后**，避免加载快照覆盖用户选值）。它由后端 `inspiration_search_enabled` / `inspiration_replace_merged_keywords` 两布尔派生（`config.toml` 仍只存两布尔），`PUT /api/config` 把 mode 翻译回两布尔并规范化，非法值 → 422。详见 [config 模块的 `keyword_generation_mode` 映射表](config.md)。 |
| 桌面 Web 设置页对齐插件 | ✅ | 桌面 Web（`/web`）设置页 `src/openbiliclaw/web/desktop/` 的可配置面与插件 side panel 拉齐：模型 tab 补 `llm.concurrency`、DeepSeek `reasoning_effort`，并提供 LLM / embedding 测试按钮（当前表单草稿 → `/api/config/probe-service`，不保存配置；v0.3.155+ 备选子页另有「测试备选 Provider」按钮走 `kind="llm_fallback"` 精确探测备选，且备选下拉会禁用与主 Provider 同名的选项并对同名旧配置显示行内警告）；平台源 tab 补完整 X(Twitter) 源块（`enabled` / `cookie_env` / 三项预算 / `request_interval` / `min_interval`）+ `GET /api/sources/x/status` 源健康提示、YouTube `min_interval_minutes`、知乎源块（`enabled` / search-hot-feed-creator-related 五项预算 / `request_interval` / `min_interval`）和候选池 X / 知乎占比；调度 tab 补 9 个真实 runtime 参数（`extension_disconnect_grace_seconds` / `refresh_check_interval_seconds` / `signal_event_threshold` / `feedback_batch_threshold` / `trending_refresh_minutes` / `explore_refresh_minutes` / `discovery_limit` / `proactive_push_interval_seconds` / `speculator_idle_interval_minutes`）；通用 tab 补局域网访问密码（`/auth/status` + `/auth/admin`，同源 loopback 视为可信本机）与开机自启（`/autostart-status` + `/autostart/apply`）。桌面 Web 同时移除了运行时不消费的 `discovery_cron` 旧字段，与插件一致；推荐页平台过滤 tab 会先按 `config.sources` / `pool_source_shares` 中启用的平台展示，再合并当前推荐卡真实来源，点击后只过滤当前已加载列表，命中为空时保留空结果状态 |
| 平台源设置页卡片化 | ✅ | 桌面 Web（`/web`）和插件 side panel 的「平台源」tab 都改成**一个来源一张卡**：卡面常驻「图标 / 名称 / 来源与接入徽章 / 最近状态」，桌面端另在卡面直接编辑候选池占比并保留启用下拉，配置区默认折叠、按需展开。展开区对十一来源使用同一套分段骨架——`接入方式`（Cookie 粘贴 / 令牌 / 插件登录态 / 公开接口四种形态共用同一容器和脱敏预览）、`发现分支与每日预算`（分支开关与该分支预算并排成表；无 `source_modes` 的来源只渲染预算列，不伪造开关）、`节流`、`<平台> 专属`（B 站 agent-browser、Reddit 命令后端、Bangumi 条目类型与初始化上限、V2EX PAT / Node/Tab 配置、微博登录态 init 任务）、`验证`。停用的来源只留卡面、不可展开，配置项仍在 DOM 中参与保存 payload。桌面端把原先独立的「来源接入状态」列表与「Cookie / 登录凭据状态」折叠区并入各自卡片（`#sourceCredentialList` 已移除，状态与凭据渲染统一从 `#sourceStatusList` 取行），候选池占比另有一张带权重条形图的总览卡与卡面双向同步；两端新增吸底保存栏，显示「已修改 N 项」并就地保存（桌面端另有「放弃修改」按最近一次后端快照回滚）。插件保存栏固定在扩展视口底部，滚动区预留操作栏与安全区高度，长表单最后一项不会被遮挡。保存按钮在无修改和请求进行中禁用；输入、LLM 草稿及建议比例等程序化修改都会解锁，成功后重新禁用，失败时保留修改供重试。所有输入框 id 与保存 payload 未变，仅重排信息架构。 |
| 统一来源接入状态 | ✅ | `GET /api/sources/status`（`SourcesStatusResponse`）只读本地配置、凭据状态、登录心跳、任务历史和来源健康表；打开设置页不会访问任一平台或执行 Reddit 命令。插件 side panel、桌面 Web 与 setup 引导页共用 `src/openbiliclaw/web/shared/source-status.js`，按 `auth_required → credential → verification → verify_method` 渲染主状态、证据徽章和可操作问题，legacy `state` 只在旧后端缺少 `auth` 时兜底。Bangumi 与 Linux.do 都是 optional-auth：纯匿名态显示「无需登录」；Linux.do 心跳明确未登录时显示「公开发现可用」，新鲜登录心跳与陈旧心跳分别显示已验证与验证已过期；Bangumi 可选令牌则用 `live_probe` 表达验证强度。这些个人增强状态不会阻断两源的匿名 discovery。 |
| 平台源设置页卡片化 | ✅ | 桌面 Web（`/web`）和插件 side panel 的「平台源」tab 都改成**一个来源一张卡**：卡面常驻「图标 / 名称 / 来源与接入徽章 / 最近状态」，桌面端另在卡面直接编辑候选池占比并保留启用下拉，配置区默认折叠、按需展开。展开区对 9 个来源使用同一套分段骨架——`接入方式`（Cookie 粘贴 / 令牌 / 插件登录态 / 公开接口四种形态共用同一容器和脱敏预览）、`发现分支与每日预算`（分支开关与该分支预算并排成表；无 `source_modes` 的来源只渲染预算列，不伪造开关）、`节流`、`<平台> 专属`（B 站 agent-browser、Reddit 命令后端、Bangumi 条目类型与初始化上限、V2EX PAT / Node/Tab 配置）、`验证`。停用的来源只留卡面、不可展开，配置项仍在 DOM 中参与保存 payload。桌面端把原先独立的「来源接入状态」列表与「Cookie / 登录凭据状态」折叠区并入各自卡片（`#sourceCredentialList` 已移除，状态与凭据渲染统一从 `#sourceStatusList` 取行），候选池占比另有一张带权重条形图的总览卡与卡面双向同步；两端新增吸底保存栏，显示「已修改 N 项」并就地保存（桌面端另有「放弃修改」按最近一次后端快照回滚）。插件保存栏固定在扩展视口底部，滚动区预留操作栏与安全区高度，长表单最后一项不会被遮挡。保存按钮在无修改和请求进行中禁用；输入、LLM 草稿及建议比例等程序化修改都会解锁，成功后重新禁用，失败时保留修改供重试。所有输入框 id 与保存 payload 未变，仅重排信息架构。 |
| 统一来源接入状态 | ✅ | `GET /api/sources/status`（`SourcesStatusResponse`）只读本地配置、Cookie 文件、登录心跳、任务历史和 X 健康表；它不访问平台，也不执行 Reddit 命令。B站三字段完整为 `ready`、不全为 `partial`；抖音本地 Cookie 存在仅为 `unverified`，不冒充实际登录成功；小红书 / 知乎以插件最近上报的布尔登录态为权威，区分从未同步 `unverified`、显式登出 `missing`、新鲜已登录 `ready`、过期已登录 `stale`，知乎仅在从未同步时回落任务历史；Reddit `rdt` 只检查本地 credential 文件，非 rdt 后端显示 `unverified`；X 沿用已持久化的真实健康结果，YouTube 为 `no_auth`；Bangumi 与 V2EX 使用匿名 / 可选 PAT 的 auth contract。插件 side panel、桌面 Web 与 setup 引导页三端加载**同一个** `src/openbiliclaw/web/shared/source-status.js`（插件由 `scripts/build.mjs` 在 build 时复制进包，因为 MV3 CSP 禁止从后端拉脚本；复制产物 `popup/shared/` 已 gitignore，勿提交），页面可见时每 30 秒只轮询本地后端；`ready` 显示「凭据已就绪」而不是「接入可用」，仅 `ok` 表示已有真实健康验证。凭据表单改由 `GET /api/sources/credentials` 的 `form` 描述符驱动：`extension_only`（小红书 / 知乎）**不渲染可粘贴输入框**（后端不存它们的 cookie），V2EX 只显示 PAT 是否存在、不回读明文。状态标签与色调现由 `item.auth` 派生（`auth_required=false` → `credential` → `verification`），legacy `state` 仅在 `auth` 缺失或畸形时兜底；`verify_method` 作为独立的证据强度维度渲染，用字形（◆ 联网证据 / ◇ 本地或间接证据 / — 无验证能力）与边框编码，不依赖颜色。 |
| 需要海外出网的来源提示 | ✅ | `SourceStatusItem` 增加 `requires_overseas_network`（平台是否在 GFW 外）与 `network_hint`（可直接渲染的文案，仅在 `[network].mode = direct` 时非空）。**清单与两份文案都只在 `sources/platforms.py` 一处**：海外桶 = bangumi / youtube / twitter / reddit，其中只有 bangumi / youtube 的出网真正由 `[network].mode` 掌管（X 走 `twitter_cli`+curl_cffi、Reddit 走 `rdt`/OpenCLI 子进程或插件，`direct` 从不清 `HTTP(S)_PROXY`，改设置修不好），因此两类给不同措辞。桌面 Web 与扩展 popup 都只做「`network_hint` 非空且该来源已启用就原样渲染」，不认识任何平台名，加新平台=改后端一行；`tests/test_source_network_hints.py` 钉死这一点 |
| 来源首页问题定位 | ✅ | 共享 `source-status.js` 新增 `describeSourceIssue()`，只把已启用来源的缺凭据、不完整、过期、失败、受阻、限流和未知契约判为问题，并保留后端 `detail` 原文；普通 `unverified` / `syncing` 不误报。桌面首页将这组「来源接入」问题与 B站/X/画像的「账号同步」问题分开命名后组合显示。微博公开 discovery 可匿名，个人 init 只在登录态 heartbeat + 任务桥 ready 时运行。 |
| 来源 Cookie 写入与秘密回读边界 | ✅ | 插件 side panel 与桌面 Web 的 B站 / 抖音 / X / Reddit 卡片仍可手动粘贴新 Cookie；`PUT /api/config` 把非空值路由到各自存储（secrets 不进来源普通配置段），X 有效 Cookie 同时解除 re-login 封锁，Reddit 缺 `reddit_session` 显式 400。读取方向改为只写秘密：两端只请求普通 `GET /api/config`，`reveal_keys=true` 即便由旧客户端传入也是 no-op，API Key/Cookie/令牌只回掩码；扩展缓存到 `chrome.storage` 的 config snapshot 因此也不含明文。`GET /api/sources/credentials` 不再宣告 copy capability，桌面端只展示脱敏状态；空字段与掩码回显仍不覆盖现有值。 |
| 开机自启动设置 | ✅ | 通用 tab 的「开机自启动」开关打开设置时读 `GET /api/autostart-status`，切换时调用 `POST /api/autostart/apply` 即时生效；`can_manage=false` 时按 `env_managed` / `shadowed` / `unsupported_*` 等 reason 禁用。若配置已关但 OS 项仍在（`enabled=false + registered=true`），桌面 Web 与插件都会按实际效果显示为开启、提示“系统自启动残留项”，用户直接关闭即可清理；该操作仍只影响后续登录，不启停当前进程。本机 Ollama 可能随启动预检一起拉起。 |
| B 站 Cookie 自动同步 | ✅ | service worker 会读取 `SESSDATA` / `bili_jct` / `DedeUserID` 三件套并推送到本地后端；后端暂未启动时切到 1 分钟重试，成功后恢复 60 分钟兜底刷新；后端 runtime-stream 也可发 `bilibili_cookie_sync_requested` 让扩展立刻回传 |
| B 站扩展搜索兜底任务 | ✅ | service worker 轮询 `/api/sources/bili/next-task` 并响应 `bili_task_available` 即时 kick；后台 tab 打开 `search.bilibili.com/all?keyword=...`，严格日期偏好还会携带 `pubtime_begin/pubtime_end`，B 站 content script 抓渲染后的搜索结果卡片，回传 `BILI_TASK_RESULT` 到 `/api/sources/bili/task-result`。该链路在后端 API search 冷却或短期 DOM fallback 降级信号存在且扩展在线时由 producer 入队，不取代 API 主路径；软偏好不向网页搜索下推硬边界。
| 抖音 Cookie 自动同步 | ✅ | service worker 会读取 douyin.com Cookie header 并推送到 `/api/sources/dy/cookie`；后端保存到 `data/douyin_cookie.json`，供 `discover --source douyin` / `discover-douyin` 在无环境变量覆盖时使用；冷启动、runtime-stream 请求和 alarm 兜底都会触发同步 |
| 小红书 / 知乎 / Linux.do 登录态同步 | ✅ | service worker 分别判断 `xiaohongshu.com` 的 `web_session`、`zhihu.com` 的 `z_c0`、`linux.do` 的 `_t` 是否存在且非空，只把 `logged_in` 布尔值推送到各自 `/login-state`；Cookie 值不上传，后端也不保存或重放这三站 Cookie。冷启动、cookie 变化、runtime-stream 请求和每小时 alarm 都可刷新布尔心跳；这里只读取浏览器 Cookie store，不打开、刷新或请求平台页面。 |
| Bangumi 身份自动识别 | ✅ | 新增 `*://*.bgm.tv/*`、`*://*.bangumi.tv/*` host permission（商店披露需同步）与两段脚本：MAIN-world `bgm-identity-bridge` 读取页面公开 `CHOBITS_UID`（>0 即已登录，登出不上报），isolated `content/bangumi.js` 只从本人专属导航区（idBadgerNeue/dock）解析 `/user/<username>`（泛化 avatar 兜底因真机实证会命中时间线路人已删除，抓不到就报空），`POST /api/sources/bangumi/identity` 经后端 `GET /v0/users/{username}` 权威比对 `id == uid` 后持久化（不一致只存 uid 并 WARNING），供 guided init/CLI 零配置识别账号（优先级：令牌 /v0/me > 显式用户名 > 扩展上报）。Bangumi 不是行为采集平台：只上报公开的 uid+username，不读 Cookie，不采集浏览行为 |
| Cookie 同步重试按平台隔离 | ✅ | B站 / 抖音 / X / Reddit / 小红书登录态 / 知乎登录态 / Linux.do 登录态各用独立 retry alarm；一个平台同步成功不会重置另一个平台的快速重试，`cookies.onChanged` debounce 也按平台隔离。Linux.do alarm 为 `openbiliclaw-cookie-sync-linuxdo`，且 payload 永远只有布尔值。旧共享 alarm 仍兼容触发一轮全量同步后清除。 |
| V2EX 推荐与设置卡 | ✅ | 桌面 Web 与 popup 均提供 V2EX source card：启用开关、PAT 脱敏输入 / 清除、五个 discovery mode、分支预算、节流、Node/Tab 配置和 `v2ex` pool share；推荐卡显示 V2EX badge、Node / 作者 / 回复数和无封面文字卡 URL。扩展新增 `*.v2ex.com` host permission，仅在用户触发初始化 / 增量任务或浏览 V2EX 页面时读取渲染后的公开 DOM；任务桥不采集 Cookie 值、页面 HTML、私信或 CSRF，不执行站内写操作；guided init 已展示 V2EX 来源选项。 |
| Cookie 同步重试按平台隔离 | ✅ | B站 / 抖音 / X / Reddit / 小红书登录态 / 知乎登录态的同步重试 alarm 拆分为 `openbiliclaw-cookie-sync-bili` / `-dy` / `-x` / `-reddit` / `-xhs` / `-zhihu` 六个独立 alarm：一个平台同步成功不再把另一平台刚排的 1/5 分钟快速重试重置回 60 分钟兜底；`cookies.onChanged` 的 debounce 也按平台独立，登录某平台只触发该平台的同步。旧共享 alarm 名（`openbiliclaw-cookie-sync`，chrome alarm 跨扩展升级持久化）兼容触发一轮全量同步后由下次 worker 启动清除 |
| 认知变化提醒 | ✅ | service worker 会提示关键认知变化，画像 tab 会显示“阿B 最近新记住了什么” |
| 认知变化历史分页 | ✅ | 画像 tab 的认知卡片支持展开详情；「阿B 最近新记住了什么」默认只展示最近 3 条，需点击「加载更多」按钮分页查看更早的变化记录（不再随页面滚动自动续页，避免该区块无限变长） |
| 认知卡片上下文澄清 | ✅ | 画像 tab 的认知卡片默认态现在固定展示“结论 + 上下文 + 状态提示”，用户可直接看出这是对哪条内容/哪轮聊天/哪组聚合信号形成的判断，以及这张卡片是否还能展开 |
| 画像多层认知展示 | ✅ | 画像 tab 现已把“你怎么处理信息 / 你在内容里长期在找什么 / 这阵子更像在经历什么”单独拆开，不再只显示一段画像 prose 加兴趣 chips |
| 画像可编辑（编辑模式） | ✅ | 画像页新增「编辑画像」开关：进入后由未截断的 `GET /api/profile/edit-state` 驱动，可增删核心特质 / 深层需求 / 价值观 / 内在驱动 / 认知风格 / 常看 UP 等 chip、增删喜欢 / 不喜欢一级领域及其二级 specifics、改写人格素描 / 人生阶段 / 当前阶段等长文、拖动滑杆调探索开放度 / 质量敏感度 / 幽默偏好 / 深度偏好等标量；文本与标量固定项显示「AI 想更新此项」漂移建议，每个改过的字段可「恢复 AI 建议」。每个控件 POST 一次 `/api/profile/edit`（确定性、抗画像重建；一级 domain 增删直接传 `target=likes/dislikes`，二级 specific 增删会额外带 `parent=<domain>`；chip 增删即时生效，长文与滑杆点「保存」生效）。插件 side panel 进入编辑时会切到 `is-profile-editing` 页面态，强制只读画像卡片退出布局、编辑面板占据原位置；移动 Web（`/m`）与桌面 Web（`/web`）同样是替换式编辑态，三端行为一致 |
| 多源行为采集（MVP） | ✅ | content script 拆成「平台无关 kernel + 平台适配器」，新增小红书适配器。manifest 覆盖 `*.xiaohongshu.com`，事件携带 `source_platform` 字段；MVP 仅采 snapshot / click / scroll / search，like/collect 延后 |
| 小红书三路由 note 兼容 | ✅ | `content/xhs/selectors.ts` 是 task、bootstrap 与被动采集的 note anchor 单一来源，同时识别 `/explore/{id}`、旧 `/discovery/item/{id}` 和搜索卡新形态 `/search_result/{id}`；搜索列表页 `/search_result?keyword=...` 因没有尾随 `/` 不会误判为笔记。URL parser、页面类型、observed-urls 与原生保存相关身份校验保持同一集合，避免通用适配器能看见卡片、任务采集器却整页返回 0 条 |
| 小红书 discover 后台执行 | ✅ | search / creator 都以 inactive tab 执行，不再抢占用户当前页面。XHS 在隐藏页不挂载笔记虚拟列表时，`xhs-token-sniffer` 从页面自己的 search API 响应中只归一化公开卡片字段，经 MAIN → isolated world replay 缓存交给 task executor；DOM 卡片继续作为 schema 漂移兜底。只有需要点击本人 profile 入口和受控滚动的 `bootstrap_profile` 保持前台；20 分钟目标间隔、每日 20 次预算和风控退避不变 |
| 小红书失效登录识别 | ✅ | `web_session` 存在不再被当作永远有效：search / creator / bootstrap 看到可见登录弹层、登录手机输入框或侧栏本人登录按钮会立即返回 `xhs_login_required` 与 content-free 路径诊断；检测遵守完整祖先可见性，隐藏控件和普通内容里的“登录”文字不命中。后端把该真实页面证据写回登录态为 false，设置页不再继续显示“凭据已就绪”，且不会把登录失效误报成空结果或平台风控 |
| 小红书封面抓取时采集 | ✅ | 修复 2026-07「没头图」：后台标签页懒加载图片永不升级，任务路径 DOM 提取只拿到 `data:` 占位符。`content/xhs/cover-harvest.ts` 先从 `__INITIAL_STATE__` 形状无关深扫按 note_id 回填真实封面 URL（DOM 两路提取一律拒收 `data:`/`blob:` 占位符），再于页面上下文抓封面字节（token 最新鲜、走用户浏览器会话），转 base64 挂 `cover_data`/`cover_content_type` 随 observed-urls 与任务结果上报，后端校验后写入 `data/image-cache/`。每批最多 12 张、单张 1MB、4s 超时，全程 best-effort——封面失败绝不阻断笔记上报 |
| 视频页停留时长采集（真实播放时长） | ✅ | `src/content/video-dwell-tracker.ts` 改为**分段累计**模型：`watch_seconds` 只累计视频真正在播放的时长（kernel 的 `play`→`beginSegment`、`pause`/`ended`→`endSegment` 驱动，绑定时已在播放则开段），暂停/闲置时间不再计入；额外报 `page_dwell_seconds`（墙钟停留，诊断用）。有时长时 clamp 到 `duration×1.5`，无时长时 ≤600s。晚渲染 `<video>`（SPA 路由后才插入播放器）有界重试挂载（500ms×20，导航取消）。kernel 在 `pushState`/`replaceState`/`popstate`/`pagehide` flush click 事件，metadata 带 `watch_seconds`/`video_duration_seconds`/`dwell_source="video_page_exit"` |
| 内容页 view + 停留采集（xhs/知乎/reddit/X） | ✅ | 此前这些平台内容页零 view/dwell。adapter 新增 `dwellPageTypes`（xhs=`["note"]`、知乎=`["answer","article","question"]`、reddit=`["post"]`、X=`["status"]`），进入内容页即发一条带 `content_id` 的 `view`（会话内同 URL 去重），并按**可见性门控**测停留（`visibilitychange` 驱动分段，隐藏标签页不计；playback 模式的视频页不受可见性影响）；flush 用 `dwell_source="content_page_exit"`、无 `video_duration_seconds`，后端 `classify_event_satisfaction` 对无时长的内容页停留按 ≥30s→positive「engaged_reading」/<5s→quick_exit/其间 neutral 分类 |
| 搜索采集（Enter + 结果页 URL） | ✅ | 除 Enter 键外，导航到搜索结果页时由 `adapter.extractSearchQuery(url)` 从结果 URL 提取关键词（覆盖点搜索按钮、点联想词），各平台按自身 `detectPageType` 模式解析（bilibili/xhs `keyword`、抖音路径段 `/search/<词>` 与 `/jingxuan/search/<词>`、youtube `search_query`、知乎/reddit/X `q`；X `/explore` 等无词搜索页返回 null 不发事件）。Enter 与 URL 两路共用 10s 归一化去重窗口，同一搜索只发一次 |
| X 取消操作采集 | ✅ | X GraphQL tap 新增 `UnfavoriteTweet`/`DeleteBookmark`/`DeleteRetweet` 三个 op → 归一为 `feedback` 事件（`feedback_type="retraction"`、`retracted_action=<like/favorite/share>`、`signal_strength=0.2`）。DOM 强信号侧：点击 `aria-pressed="true"` 的赞/收藏/关注按钮识别为撤销——tap 权威平台（X）只压制正向事件不重复发，其余平台改发 retraction。撤销事件满意度恒为 neutral，不进反馈批学习、不走强信号旁路（撤销=中和，非负偏好） |
| xhs token / 搜索响应桥（MAIN world） | ✅ | `src/main/xhs-token-sniffer.ts` 以 `world: "MAIN"`、`run_at: "document_start"` 注入 xhs 页面，观察 `window.fetch` / `XMLHttpRequest`：所有自家 API 仍只扫描 `(note_id, xsec_token)` 并经 isolated world 回填；仅 search notes 接口额外归一化最多 20 条公开卡片字段（标题、作者、封面、互动数、发布时间、tokenized URL），不转发 raw response。页面内有界 replay 缓存覆盖 content listener 晚于首个请求挂载的竞态，让后台 search 不依赖虚拟 DOM |
| 评论 / 弹幕正文采集（网络层） | ✅ | 用户亲手写的评论 / 弹幕正文首次进入画像证据链，**均在提交成功后经网络层采集**。X：GraphQL tap 的 reply `CreateTweet` 提取 `variables.tweet_text`，仅当响应无 GraphQL `errors`（业务码校验）才附带正文（既有 comment 事件发射时序不变）。B 站：MAIN-world `src/main/bili-interact-tap.ts`（`world:"MAIN"`、`document_start`、匹配 bilibili.com）观察 `POST …/x/v2/reply/add`（评论）与 `POST …/x/v2/dm/post`（弹幕），仅当 HTTP 2xx 且响应 `code===0` 才发，`postMessage`（`obc-bili-interact`）桥接到 `content/bilibili.ts` 构造 `comment` 事件（评论 `comment_kind="comment"`、弹幕 `comment_kind="danmaku"`+`signal_strength=0.6`）。正文经**双端净化**（扩展 `shared/text-sanitize.ts` 与后端 `sources/event_format.py` 各自截断 200 字符 + 剥离 Unicode category-C）。事件 URL 取当前视频页 href，后端据此提取 bvid |
| B 站强信号 interact tap（MAIN world） | ✅ | 既有 `src/main/bili-interact-tap.ts` 扩展观察 `POST …/x/web-interface/archive/like`（`like=1`→like、`like=2`→retraction）、`POST …/x/v3/fav/resource/deal`（非空 `add_media_ids`→favorite、非空 `del_media_ids`→retraction）和 `POST …/x/web-interface/coin/add`→coin；仅 HTTP 2xx 且响应 `code===0` 才经既有 `obc-bili-interact` 桥发送，解析异常、业务失败与动作字段歧义均静默丢弃，请求保持原样。`content/bilibili.ts` 继续使用当前视频页 href，retraction 对齐 kernel 形状：`feedback_type="retraction"`、`retracted_action=<like/favorite>`、`signal_strength=0.2`。端点 fixture 按 bilibili-API-collect 公开记录构造，待真机验证 |
| tap 权威按动作粒度 DOM 抑制 | ✅ | `PlatformAdapter.tapAuthoritativeActions`（动作集合）取代旧 `strongSignalSource` 粗粒度标记：kernel 在 DOM 动作发射前（正向路径与 `aria-pressed` 撤销路径统一）检查该集合，命中动作 DOM 零发射，消除「网络提交 + DOM 点击」双计与「仅打开评论区 / 转发菜单即记事件」的假动作。X 声明 `{like,favorite,share,comment,retraction}`，B 站声明 `{comment,like,favorite,coin,retraction}`（interact tap 权威；覆盖 B 站 class `on` 无 `aria-pressed` 的撤销缺口），xhs 声明 `{like,favorite,retraction}`，未声明动作与非 tap 平台不变 |
| xhs 强信号 action tap（MAIN world） | ✅ | 小红书赞 / 收藏从「按钮文案匹配、图标按钮漏采」升级为网络层确定性认定。新 MAIN-world `src/main/xhs-action-tap.ts`（`world:"MAIN"`、`document_start`、匹配 xiaohongshu.com）观察写端点 `POST …/v1/note/like`→like、`/note/dislike`→retraction(取消赞)、`/note/collect`→favorite、`/note/uncollect`→retraction(取消收藏)，仅当响应业务成功（`success===true` 或 `code===0`，不变量 7b）才发，`postMessage`（`obc-xhs-action`，与 token sniffer 的 `obc-xhs-sniffer` 隔离）桥接到 `content/xhs/action-event.ts` 构造 like/favorite/retraction 事件，事件 URL 由 note_id 拼 `…/explore/<note_id>`，与后端 `sources/identity_keys.py` 的 note 键型互通（赞与其后的撤销折价同一批事件）。retraction 归一为 `feedback`（`retracted_action=<like/favorite>`、`signal_strength=0.2`）。端点形状按公开社区文档构造，待真实端到端验证 |
| 引导初始化 CTA | ✅ | v0.3.68：推荐 tab 未初始化时不再叫用户去命令行，而是给「开始初始化」面板：① 数据来源勾选（v0.3.118+ B 站默认勾选但可取消，与小红书 / 抖音 / YouTube / X / 知乎一样可选，至少保留一个；静态渲染秒开）+ 文案提示「使用某平台前先在当前浏览器登录该平台账号，勾选会同时开启该来源」；② 「开始初始化」按钮（点击驱动校验，不在加载时空等慢预检）。点击后置「检查中…」加载态并实时拉 `GET /api/init-status` 校验前置（LLM / embedding / 所选平台登录）：一个来源都没勾 → 提示「至少勾选一个数据来源」；勾选的小红书 / 抖音 / YouTube / X / 知乎会随 `POST /api/init` 作为本轮 opt-in 生效并 best-effort 写回 `sources.<platform>.enabled=true`；勾了 B 站但未登录 → 提示登录或取消勾选（未勾 B 站时不再要求 B 站登录）；前置未通过 → 展示前置清单 + 原因、按钮复位、**不**启动；全通过才调 `POST /api/init`（带所选 `sources`），订阅 `runtime-stream` 的 `init_progress/failed/completed`（+ 3s 轮询兜底）实时更新进度，完成后自动加载推荐 / 画像。其中 LLM / embedding 为严格真实探测（各发一次真实最小请求，超时 / 失败判未就绪）。v0.3.162+：向量模型行直接消费既有 `embedding_repair_running/completed/total`、`ollama_phase`、`embedding_pull_status` 与 `embedding_check`；拉取中渲染 1–99% 进度条和 phase 文案，可修复诊断渲染对应按钮，点击调用既有 `startEmbeddingRepair()` 并轮询 init-status 刷新，不增加后端字段。v0.3.168+：面板明确说明四阶段严格串行——完整画像保存后才开始内容发现、评估和推荐文案，耗时取决于平台数与历史量、不做预估；阶段 4 的细分 note 直接来自后端。普通完成代表已有 canonical 推荐可直接浏览，部分完成则保留后台补池 warning，不再合成前端 95% 二次等待。DOM 无关逻辑在 `popup/popup-init-control.js`（单测 `tests/init-control.test.ts`）。画像 / 画像编辑空状态文案改为指向推荐页初始化。详见 [init 模块文档](init.md) |
| 重新初始化 / 重建画像入口 | ✅ | 入口收敛原则（gui-init §4）：**已初始化后重新初始化入口只在设置页**。popup 通用 tab 新增「重新初始化 / 重建画像」字段（`#cfgReinitBtn` + `#cfgReinitStatus` + `#cfgReinitResetCognition` 复选框）：打开设置时拉 `GET /api/init-status` 显示当前状态（未初始化 / 已初始化 / 进行中），进行中禁用按钮；点击先二次确认（`window.confirm`，说明现有事件 / 收藏 / 对话历史保留、旧推荐池会清空重建、会消耗较多 AI 调用；勾选「同时清空旧认知观察与洞察」时确认文案追加说明并随请求带 `reset_cognition:true`），确认后调 `POST /api/init {force:true}`（`popup-api.startInit` 既有 force 参数），成功后关闭设置覆盖层、切到「推荐」tab 并复用既有 init 进度面板（`renderInitProgress` + `_startInitProgressPoll`）展示四阶段进度。复选框带 `data-settings-ignore-dirty`，不污染配置保存栏。推荐 tab 的「开始初始化」CTA 保持首跑专属、不出现 force。 |
| 头部响应式 + Star 按钮 | ✅ | side panel 默认窄宽（<460px）下头部保持**单行**（品牌左、4 个操作图标右、垂直居中）：隐藏装饰 eyebrow、状态徽标空间不足时紧凑换到标题下、图标压 28px（`.hero-actions button` 提高优先级压过靠后的 `.webui-button{32px}`），不再把图标右浮成空一截的第二行；≥460px 维持原样。功能键图标列下一行有**常驻 GitHub Star 按钮**（`.hero-sub` 内右对齐），做成大项目常见的 GitHub-Buttons 双段样式 `[🐙 Star | 数量]`：Octocat + 「Star」+ 实时 star 数。popup 只请求同源 `/api/project-stats`，后端统一处理 GitHub 的 12 小时持久缓存、ETag 和限流退避，插件 `localStorage` 继续缓存 12 小时；完全无缓存时只显示 `[🐙 Star]`，不再从浏览器产生 GitHub 403。点击仍打开仓库（直接 star 需 GitHub 认证，官方组件亦只跳转）。响应式断点用 chrome-devtools 实测 360/400/560px，断言在 `tests/popup-layout.test.ts` |
| 桌面 Web Star 引导 | ✅ | PC Web `/web` 顶部状态区新增常驻 GitHub Star CTA，文案为「好用求 Star」，沿用插件的 GitHub-Buttons 双段视觉：Octocat + CTA 文案 + 实时 star 数。`src/openbiliclaw/web/desktop/assets/js/app.js` 直接打开项目仓库，数量只从同源 `/api/project-stats` 获取并写入 `localStorage` 缓存 12h；GitHub 403 / 429、离线或异常由后端返回旧缓存或无数量的本地 200，页面不再直接访问 `api.github.com`。`1180px` 以下收起品牌副标题、状态文案和 CTA 文案，只保留图标 + 数量，避免桌面窄宽顶栏重叠。静态回归见 `tests/test_desktop_web_pool_status.py::test_desktop_web_shows_github_star_cta` |
| xhs 初始化画像任务 | ✅ | 后端可派发 `bootstrap_profile` 任务；`/api/sources/xhs/next-task` 会先把任务原子标记为 `in_progress` 再返回给扩展，避免多个浏览器实例重复领取同一个前台拉取任务；插件先打开小红书 `/explore`，滚动任务会以前台 tab 点击页面“我”入口进入 profile，再从 profile 页 state / DOM 解析收藏、点赞和小红书页面内显式浏览记录信号；显式启用 `max_scroll_rounds` 时会有限滚动，并用 `status="partial"` 分批回传给 `/api/sources/xhs/task-result`。插件按批次裁剪，后端再按任务不可变 payload 对 partial + final 的累计 canonical 结果执行 scope 白名单与 `max_items_per_scope`，避免重试或分批结果扩大任务预算 |
| xhs 来源关闭与风控熔断 | ✅ | `/api/sources/xhs/next-task` 在领取自动发现任务前动态检查 `sources.xiaohongshu.enabled` 与全局 scheduler；关闭后，已经排队的 search / creator / bootstrap 仍保留为 pending，但扩展不会再因它们打开页面。`content/xhs/risk-control.ts` 识别可见的安全验证、操作频繁和 429 页面，executor 只回传结构化 `rate_limited` 原因、不上传页面原文；后端据此持久化 1 小时平台冷却，冷却期间包括原生保存任务在内都不再领取。用户显式触发、且未处于风控冷却的原生保存不受 discovery 开关影响。 |
| 抖音初始化画像任务 | ✅ | 后端可派发 `bootstrap_profile` 任务；插件依次访问抖音发布 / 收藏 / 喜欢 / 关注 scope，当前账号只接受 `profile/self` MAIN bridge 正面确认（或同一 tab 的已确认缓存）；`#RENDER_DATA` 仅在显式登录时作未确认候选，常驻 tap 不从被动请求 URL 提取身份。随后结合 DOM、fetch tap 与 API harvester 采集条目，并用 `partial` 分批回传给 `/api/sources/dy/task-result`；身份 / 分页不完整时终态为 `degraded`。动态 fetch-tap 注入兼容 release/unpacked 两种资源根布局，依次尝试 `dist/main/dy-fetch-tap.js` 与 `main/dy-fetch-tap.js` |
| 扩展任务并发领取保护 | ✅ | XHS / 抖音 / YouTube 的 `/next-task` claim 使用短生命周期 SQLite 连接执行 `BEGIN IMMEDIATE`，避免多个 FastAPI threadpool 请求共享同一 connection 时出现嵌套事务错误 |
| 抖音搜索任务 | ✅ | 后端可派发 `search` 任务；插件用后台 tab 先打开抖音首页，在已登录页面里模拟搜索框输入 / 点击搜索，并等待 URL 进入 `/jingxuan/search/<keyword>` 等真实搜索结果路由；任务 debug 用 `ui_triggered` 表示已提交、`search_navigation_ok` 表示已进入结果页，避免把搜索建议或登录弹窗误报成成功搜索；随后被动收集页面自身搜索响应和渲染 DOM，回传 `dy_search` 候选供 CLI smoke 和正式 `dy-plugin-search` discovery 使用；MAIN-world fetch tap 兼容 `/general/search/single/`、`/search/item/` 和新版 `/general/search/stream/` chunked JSON；runtime 会把候选写入统一待评估池。单任务默认等待 180 秒，但同一 discovery cycle 共享一份等待预算；后端超时 / 取消会把任务原子写成 `failed + wait_timeout/wait_cancelled`，不会留下 pending storm。 |
| 抖音热点任务 | ✅ | 后端可派发 `hot` 任务；hot board 的 `group_id` 会作为 `seed_aweme_id` 透传，插件优先执行带 seed 的热词；后台 tab 仍从抖音首页出发模拟热榜点击并被动收集响应 / DOM，不足时用已登录页面的 related API bridge 拉取 `dy_hot` 候选供 `dy-plugin-hot-related` discovery 使用；runtime 会把候选写入统一待评估池 |
| 抖音首页推荐流任务 | ✅ | 后端可派发 `feed` 任务；插件用后台 tab 打开已登录抖音首页，滚动推荐流触发页面加载，再被动收集 feed 响应和渲染 DOM，回传 `dy_feed` 候选供 `dy-plugin-feed` discovery 使用；runtime 会把候选写入统一待评估池 |
| YouTube 初始化画像任务 | ✅ | 后端可派发 `bootstrap_profile` 任务；插件依次访问 `/feed/history`、`/feed/channels`、`/playlist?list=LL`，从 DOM 读取观看历史 / 订阅 / 点赞并用 `partial` 分批回传给 `/api/sources/yt/task-result`。`yt/task-executor.ts` 兼容新旧卡片布局：支持 shadow-root 递归、`yt-lockup-view-model` light DOM 类名（`a.ytLockupMetadataViewModelTitle` 等）和旧 `#video-title` / `#channel-name` / `img#img` 选择器 |
| 知乎事件拉取任务 | ✅ | 后端可派发 `bootstrap_events` 任务；插件在已登录知乎页面内读取最近浏览记录、收藏夹条目和个人动态点赞 / 收藏，回传 `/api/sources/zhihu/task-result`。扩展会用 `/api/v4/me` 自动识别当前用户，收藏夹优先走 favlists API，旧 `/collections/mine` HTML 路径仅作 fallback。动态点赞和动态收藏各自独立使用单分支上限，不互相抢额度。`openbiliclaw fetch-zhihu` 默认只把该链路当事件爬取 smoke，不写 memory，也不触发画像初始化；加 `--write-memory` 可把本次抓取写入本地 memory，加 `--rebuild-profile` 会写入后触发真实画像重建。guided init 勾选知乎 / `init --yes-zhihu` 会显式收集同类任务结果并把统一事件喂给首轮画像；后端任务 payload 显式带 `profile_update=true` 时，`task-result` 新增事件会走 memory + `ProfileUpdatePipeline` 增量路径。任务 tab 使用 `openbiliclaw_zhihu_task` 标记进入静默模式，只安装 executor，不启动普通行为采集，避免 smoke / init 拉取污染 `/api/events` |
| 知乎 discovery 任务 | ✅ | 后端可派发 `search` / `hot` / `feed` / `creator` / `related` 任务；插件在同一个已登录知乎任务 tab 中调用搜索、热榜、首页推荐、作者页和问题相关接口并回传 `zhihu_search` / `zhihu_hot` / `zhihu_feed` / `zhihu_creator` / `zhihu_related` 候选。归一化器会映射 answer / article / question 的标题、作者、摘要、URL 与点赞 / 收藏 / 评论指标；runtime `ZhihuDiscoveryProducer` 把这些候选以 `zhihu-search` / `zhihu-hot` / `zhihu-feed` / `zhihu-creator` / `zhihu-related` 写入统一待评估池，`openbiliclaw discover --source zhihu` 使用正式 producer 流程，`openbiliclaw discover-zhihu*` 可用于真实端到端 smoke。插件 side panel 设置页和桌面 Web 设置页都能编辑 `[sources.zhihu].source_modes` 与各分支预算；creator / related 在没有历史种子时会使用同轮 search / hot / feed 产出的作者页和内容 URL 兜底。discovery 任务不写 `/api/events`，也不触发画像初始化 |
| 知乎来源关闭拦截 | ✅ | `/api/sources/zhihu/next-task` 在领取自动任务前动态检查 `sources.zhihu.enabled` 与全局 scheduler / 增量总开关；关闭后，已排队的自动 bootstrap / discovery 任务不会再被领取，扩展不会因此打开知乎任务页。scheduler-owned 增量任务会被清理避免卡住其它来源，手动任务保持 pending，重新开启后恢复 |
| 后端 endpoint 可配置 | ✅ | 设置页保存 `http/https`、裸 IPv4 / 主机名与 `1-65535` 端口；旧存储自动迁移为 HTTP。非 loopback endpoint 保存前请求 `scheme://host/*` 可选权限（WebExtension API 无法跨浏览器限定端口），拒绝则不改缓存或存储；实际请求固定配置端口，公网 host 强制 HTTPS，WebSocket 自动派生 WSS |
| 后台 LLM 暂停配置 | ✅ | 设置页调度区提供「停止后台 LLM 请求」「关闭浏览器后停止后台」和断开宽限秒数，推荐页不再放运行时开关；后端通过 `/api/runtime-stream` presence 判断插件是否在线，空闲连接每 20 秒收到 `runtime.heartbeat`，浏览器 idle disconnect 会被 receive-side detector 及时清掉；桌面 Web 对关闭与异常统一进入重连态，不再把正常可恢复断线显示成永久断开 |
| 配置恢复与降级模式 UI | ✅ | popup API 会缓存最近一次成功的 `/api/config` 快照；设置页打开时如果后端离线但有缓存，会用缓存填表并显示离线时间；如果后端以 `degraded=true` 返回配置，会展示 blocking issues。降级后端精确放行插件共用的 `/api/config/probe-service`、`/api/config/discover-models` 与来源比例建议，因此测试实例/整链和获取模型会使用当前表单草稿真实执行，不再被启动失败的 active registry 连带返回 503；LLM probe 的客户端 timeout 为 125 秒，对齐后端有限 120 秒冷启动窗口。业务 hydration 在修复前保持阻断。有效配置保存后后端原地重建 runtime，插件立即读取可用状态；旧后端或异常 bootstrap 返回 `restart_required=true` 时仍显示重启兜底。 |
| 配置页面板结构隔离 | ✅ | popup 设置页的模型、平台源、调度、高级功能、通用和日志面板必须是 `settingsOverlay` 的同级节点；Linux.do / V2EX 来源卡片闭合后，离线且无缓存时仍能切换到通用等静态表单，不会因隐藏的平台源面板而显示空白。`extension/tests/popup-settings.test.ts` 固定来源卡片的闭合结构。 |
| Side Panel 全局可见性与覆盖层焦点 | ✅ | 底部「最近发生的事」在推荐、内容库、画像、对话四个一级 Tab 始终可见，展开历史按 `dvh` 在 72–360px 内自适应并内部滚动。设置、消息和手机二维码使用 modal 语义；打开时背景 shell 子节点进入 `inert` 且对辅助技术隐藏，Tab 留在覆盖层内，Esc / 返回按钮关闭后恢复到原触发控件。停用来源卡面退出键盘顺序并标记 `aria-disabled=true`，其启用开关仍保留可操作。 |
| 配置后台应用回执 | ✅ | `PUT /api/config` 持久化成功后统一立即返回 `202 apply_state="queued"`；popup 显示 amber“已进入后台应用队列”，连续保存由后端合并为最新修订，不再等到 60 秒 AbortError 才给不确定提示。最终 `config_reloaded` 刷新数据，`config_reload_failed` 显示已恢复 last-good 的红色回执；`popup-api.requestJson()` 的 60s AbortController 仍作为旧后端或异常网络兼容兜底。后端安全 drain 上限仍为 25 分钟；待聊「打开」的 1/2/3/5 秒退避契约不变。 |
| OpenAI 认证方式配置 | ✅ | 设置页 OpenAI provider 区域可选择 `API Key` 或 `Codex OAuth`，保存时把 `[llm.openai].auth_mode` 纳入 `/api/config` payload；后端仍负责 Codex token 导入、域名限制和配置校验 |
| 版本与更新面板 | ✅ | 设置页调度 tab 的“版本与更新”读取 `/api/update-status` 展示后端当前 / 最新版本、状态、上次检查和错误；`github_rate_limited` / `github_unreachable` / `no_backend_tag_yet`、`dirty_worktree`、`untrusted_remote`、`branch_not_fast_forwardable` 等稳定 reason 会映射成本地化提示，避免把后端错误 key 直接露给用户。`install_mode="git"` 且发现 `backend-v*` 更新时才显示“立即应用”；`install_mode` 为空或未知时不会走自动应用分支，避免把安装方式不明的后端误当 AI / 源码安装处理。点击后若 `/api/update/apply` 被后端安全守卫以 409 拒绝，popup 会用响应体刷新状态卡并在 toast 里显示具体阻断原因，再重新读取 canonical update status，避免继续停留在旧的 `update_available` 视图。`install_mode="frozen"` 或最新 tag 为 `desktop-v*` 时只显示“前往下载新安装包”，让安装包用户去 GitHub Release 下载新安装包覆盖安装，不会误触源码快进。`popup-helpers.normalizeRuntimeStatus()` 同时保留 `/api/runtime-status` 中的 `current_version`、`latest_remote_version`、`backend_update_state` 等自动更新摘要字段，避免 runtime 状态归一化时丢失后端版本信息。 |
| 语义去重未启用提示 | ✅ | v0.3.54+ 推荐页启动时读 `/api/health.embedding_ready`，为 `false` 时在推荐列表上方显示可关闭横幅（`maybeShowEmbeddingBanner`）；「一键启用本地 Ollama」按钮 PUT `/api/config` 写入 `embedding.provider=ollama, model=bge-m3` 热加载，再复检 health，仅当 `embedding_ready` 翻 `true` 才收起横幅，否则提示去跑 `ollama serve` / `ollama pull bge-m3`。本会话内关闭后不再打扰（`sessionStorage`）。v0.3.97+ 横幅决策抽到 `popup-embedding-banner.js`（`shouldShowEmbeddingBanner`），并在面板重新可见 / 获焦时复检（`installEmbeddingBannerAutoRefresh`）——配合后端实时探活，embedding 修好后无需重开面板横幅即自动消失（此前 `maybeShowEmbeddingBanner` 仅面板打开时跑一次，常驻 side panel 会长期残留旧横幅）。v0.3.97+ 同时修复横幅**根本无法隐藏**的 CSS bug：`.embedding-banner { display: flex }` 盖过 UA `[hidden] { display: none }`（同优先级，author > UA），`banner.hidden = true` 形同虚设、横幅无视 `embedding_ready` 常驻——新增 `.embedding-banner[hidden] { display: none }` 守卫修复。v0.3.155+ 启用按钮升级为一键修复：配置写入后 health 仍未就绪时改调 `POST /api/embedding/repair`（后端分类原因并自动 `ollama pull` 缺失 / 损坏的 bge-m3），按钮轮询 `GET /api/embedding/repair` 实时显示「拉取中 N%」（关面板下载继续，横幅自动刷新兜底收尾）；`not_running` / `unsupported_provider` / 远程 403 / 旧后端 404 各有明确提示不再一律「请确认 ollama serve」。初始化清单向量模型行 hint 也改为展示 `embedding_check` / `embedding_detail` 的分类原因（`describeEmbeddingHint`，后端 detail 优先、按码文案兜底、旧后端回落原通用文案）；同批修复 popup 对硬性向量前置的矛盾展示——REASON_TEXT 补上 `embedding_not_ready`（此前独缺，被拦只显示通用「以下条件未满足」），清单向量模型行改为跟随 `embedding_required`（required → `hard:true`、标签「向量模型可用」、hint 不再说「也能初始化」），与 setup / 桌面 Web 行为对齐 |
| 跨平台行为动作采集 | ✅ | B 站、小红书、抖音、YouTube 和 X 均通过 `PlatformAdapter` 识别统一动作：`like/favorite/comment/share/follow` 等按平台能力映射；`dislike` 永远经 `normalizeActionSignal()` 规范为 `feedback` 事件，metadata 带 `feedback_type=dislike` 与 `reaction=thumbs_down`。真实 DOM 点击会从内部 `span/svg` 向上归因到最近的按钮 / 链接 / 带 `aria-label` 的动作元素，避免真实站点嵌套按钮漏识别分享、关注等强信号。后台 buffer 把 `feedback/follow/share/view` 和带 dwell metadata 的 `click` 视为即时 flush 信号，高频 `scroll/hover/snapshot` 仍缓冲去重 |
| 动作关键词守卫与点踩切换追踪（issue #205） | ✅ | 平台适配器只在「短控件标签」上匹配动作关键词：中文平台（bilibili/douyin/zhihu）对 textContent 与 aria-label/title 统一加 ≤8 字符上限（真实控件标签最长 6 字），className 按 token 匹配；英文平台（youtube/reddit）把动词锚定到真实控件——YouTube 控件 aria-label 是动词开头（"Like this video…"/“不喜欢此视频”），结果卡片则是「标题 by 作者」句式，故 aria 用动词前缀匹配、卡片文案长度封顶（12 字符，容纳 "Subscribe"）；Reddit 投票只认 aria/class 锚定的 downvote/upvote，标题里的独立词不再触发。修复前在真实 bilibili.com 与 youtube.com 上点击标题含敏感词的普通视频卡片都会被误记为点踩（#200 只限制了 textContent）。DOM 点踩无 pressed 态可读，kernel 按「同控件奇偶点击」区分点踩与取消点踩（偶数次记为 `retraction`），10 分钟无操作后重新计数，连点不再放大负信号。以上规则均在真实站点端到端 A/B 验证：bilibili 搜索页旧 3/新 0 误报，YouTube 结果页旧 3/新 0 误报，YouTube watch 页真实 dislike 控件（aria=不喜欢此视频）正向采集正常 |
| 扩展捕捉 E2E 自检 | ✅ | 本机后端可通过 `/api/extension/e2e/run` 向已安装插件投递 `extension_e2e_run`，插件打开 / 复用抖音、小红书、X 标签页并执行白名单 DOM 操作，再由后端按运行窗口校验真实 `/api/events` 入库结果。复用同域 tab 时会先归位到平台入口，避免旧 404 / modal / 图片预览页污染测试；content executor 不直接发送 `BEHAVIOR_EVENT`，确保测到的是真实捕捉链路；会改变状态的动作需要显式 `allow_state_changing=true` |
| 发现候选互动指标 | ✅ | 小红书被动采集、抖音 DOM 兜底与 MAIN-world fetch tap 会在候选 metadata 中尽量携带 `view_count` / `like_count` / `collect_count` / `comment_count` / `share_count`。指标解析使用共享 `metric-count.ts`，兼容 `1.2万`、`3k`、`1,234` 和带中文标签的文本；插件只读取页面已渲染或站内响应里已有的计数，不为补指标额外打开详情页。后端会把这些字段写入 `discovery_candidates`，进入统一 discovery evaluator。 |
| 对话历史自动定位 | ✅ | `popup/popup.js` 的 `scrollChatMessagesToBottom()` 统一处理聊天历史滚动：历史 hydrate、追加用户/助手消息、thinking 占位替换，以及从其他 tab 切回「对话」时都会把 `.chat-messages` 滚到最新 turn；切 tab 场景额外用下一帧滚动覆盖 hidden 容器恢复布局后的高度变化 |

### 初始化防假卡死反馈

popup 对 init-status / start / cancel 分别使用 45s / 60s / 15s deadline，并在 run 活跃时显示取消按钮。`popup-init-control.js` 分开跟踪 `last_heartbeat_at` 与 `progress_sequence/last_progress_at`：后台 owner 在线但当前步骤尚无结果时明确说明仍在等待，heartbeat 也停止才提示可能断开。`mode=indeterminate` 使用流动条 + 已用时，不显示虚假百分比；轮询失败保留最后进度并展示连接异常。`running` 优先于 `initialized`，阶段 3 画像已保存、阶段 4 尚未结束时不会提前跳完成页。

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
│   ├── popup-connection-poller.js # popup HTTP / runtime-stream 三态协调与离线 /api/ping 重探测
│   ├── popup-saved-sync.js
│   ├── popup-helpers.js    # popup 纯函数：runtime 状态归一化、探针 key / stale 过滤等
│   └── shared/             # ⚠️ 构建产物，已 gitignore，勿提交
│       ├── dialogue-confirmation.js # 卡片 / 待聊 / durable turn 共享语义
│       └── source-status.js         # 来源状态共享语义；均由 build.mjs 从 web/shared/ 复制
├── src/
│   ├── background/
│   │   ├── buffer.ts
│   │   ├── cookie-sync.ts     # Cookie 同步 + 小红书 / 知乎 / Linux.do 布尔登录态（重试 alarm 按平台隔离）
│   │   ├── e2e-runner.ts      # 后端驱动的真实标签页 E2E 捕捉自检 runner
│   │   ├── bili-task-dispatcher.ts # B 站搜索兜底任务轮询 / 后台 tab / 结果回传
│   │   ├── zhihu-task-dispatcher.ts # 知乎 bootstrap_events/search/hot/feed/creator/related 任务轮询 / 前后台 tab / 结果回传
│   │   ├── reddit-task-dispatcher.ts # Reddit bootstrap/search/hot/subreddit/related 任务轮询 / 后台 tab / 结果回传
│   │   ├── linuxdo-task-dispatcher.ts # Linux.do bootstrap/search/hot/feed/creator/related 任务轮询 / 隔离 tab / 结果回传
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
│   │   ├── linuxdo.ts         # Linux.do entry point；普通页挂 adapter，task tab 只挂 executor
│   │   ├── yt/
│   │   │   └── task-executor.ts # YouTube bootstrap scope DOM 解析与回传
│   │   ├── zhihu/
│   │   │   └── task-executor.ts # 知乎浏览记录 / 收藏夹 / 动态条目 / discovery 候选读取与回传；长数字 ID 按字符串保真解析
│   │   ├── reddit/
│   │   │   ├── task-mode.ts      # Reddit 任务 tab 标记识别
│   │   │   └── task-executor.ts  # Reddit 同源 JSON bootstrap/search/hot/subreddit/related 读取与归一化
│   │   ├── linuxdo/
│   │   │   ├── task-mode.ts      # Linux.do 任务 tab 稳定 query marker 隔离（兼容旧 hash）
│   │   │   └── task-executor.ts  # Linux.do 同源只读 JSON、分页、归一化与结构化错误
│   │   └── xhs/
│   │       ├── action-event.ts # xhs-action-tap 消息 → like/favorite/retraction 事件（纯，可测）
│   │       ├── bootstrap.ts   # 初始化画像任务的 state / DOM 解析 helper
│   │       ├── passive.ts     # 小红书被动 URL / note metadata 采集
│   │       ├── selectors.ts   # note-card DOM selector 单一来源（passive + bootstrap + task 共用）
│   │       └── task-executor.ts # 后台任务在页面内的执行入口
│   ├── main/
│   │   ├── bili-interact-tap.ts  # MAIN-world B站弹幕/评论 + 赞/收藏/投币/撤销 tap
│   │   ├── dy-fetch-tap.ts       # MAIN-world 抖音 fetch tap + API harvester
│   │   ├── x-graphql-tap.ts      # MAIN-world X GraphQL tap（点赞/收藏/转发/回复/撤销）
│   │   ├── xhs-action-tap.ts     # MAIN-world xhs like/dislike/collect/uncollect tap（强信号）
│   │   └── xhs-token-sniffer.ts  # MAIN-world fetch/XHR sniffer，捞 xsec_token
│   └── shared/
│       ├── backend-endpoint.ts # 共用后端 origin / apiUrl() / wsUrl() + chrome.storage 持久化 endpoint
│       ├── behavior.ts        # createBehaviorEvent / DOM snapshot kernel / isTapAuthoritativeAction
│       ├── e2e.ts             # E2E request / result / action 类型与超时常量
│       ├── text-sanitize.ts   # sanitizeUserText：评论/弹幕正文截断 200 + 剥离 category-C
│       ├── types.ts           # BehaviorEvent + PlatformAdapter（含 tapAuthoritativeActions）接口
│       └── platforms/
│           ├── bilibili.ts    # bvid 提取、卡片选择器、动作关键字
│           ├── douyin.ts      # aweme_id 提取、卡片选择器、动作关键字
│           ├── twitter.ts     # tweet_id 提取、卡片选择器、动作关键字
│           ├── xiaohongshu.ts # note_id 提取、卡片选择器
│           ├── youtube.ts     # video_id 提取、卡片选择器、动作关键字
│           ├── zhihu.ts       # question / answer / article ID 提取与动作关键字
│           ├── reddit.ts      # Reddit post/comment URL、subreddit 与动作关键字
│           └── linuxdo.ts     # Linux.do topic ID / page type / content selector
└── tests/
    ├── collector-helpers.test.ts
    ├── dist-module-specifiers.test.ts
    ├── manifest-assets.test.ts
    ├── popup-helpers.test.ts
    ├── linuxdo-task-dispatcher.test.ts
    ├── linuxdo-task-executor.test.ts
    ├── linuxdo-task-mode.test.ts
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
- **缓冲持久化（MV3 SW 回收防丢）**：事件缓冲由 `buffer.ts` 持有并以 awaited 写穿方式镜像到 `chrome.storage.local`（key `obc_event_buffer`，无 debounce——挂起的 `setTimeout` 会随 SW 一起被杀）；强信号在网络 flush 开始前就已落盘。SW 冷启动经模块级 `bufferReady()` init gate 恢复镜像事件（所有触碰缓冲的入口先 await 它），内存与镜像合计仍受 `BUFFER_MAX_SIZE` 约束，超限丢最老并记日志
- **event ID 随 durable buffer 一起恢复**：buffer 在入队/恢复时 trim 既有 `event_id`，缺失时生成一次并写回事件对象；flush 失败、全量 `not_initialized` parking、MV3 worker 回收后的重放都保留同一值。后端不再接受缺 ID 的 `/api/events` item，因此任何新增 producer 都必须在进入 buffer 前或由 buffer normalization 补齐稳定 ID
- **事件来源归属随事件持久化**：平台适配器写入 `source_platform`，内容适配器把 `content_id` / `bvid` / `note_id` 等稳定身份放入 metadata；后端按“显式来源 → metadata → 规范 URL → B 站兼容默认”统一解析，把平台、内容 ID 和 `source_confidence` 提升到 `events` 顶层列，同时保留 metadata 兼容镜像。旧插件或省略来源的事件若带有 X / YouTube 等规范 URL 会被标记为 `inferred`；真正没有足够证据的兼容事件才是 `legacy_unknown`，不会被撤回逻辑当作精确平台证据
- **`not_initialized` 停车场**：后端未初始化时整批事件不再消费即弃，而是移入 `obc_parked_events`（上限 500 条 FIFO、48h TTL），后续任一次成功 flush 会按原顺序 drain 回缓冲队首补发——浏览行为类事件（dwell/click）不再在初始化前永久丢失
- flush 成功后检查一次待发通知
- 缓冲为空时也会周期轮询高置信通知
- 每次 service worker 冷启动都会启动 B 站 / 抖音 / X / Reddit Cookie 同步，并上报小红书 `web_session`、知乎 `z_c0` 与 Linux.do `_t` 的登录布尔态；Linux.do 不上传 Cookie 值。如果已配置后端暂时不可用，会通过各平台独立 `chrome.alarms` 快速重试，成功后恢复小时兜底刷新
- 会启动知乎任务轮询；收到 runtime stream 的 `zhihu_task_available` 后立即打开带 `openbiliclaw_zhihu_task` 标记的知乎任务 tab。`bootstrap_events` 初始化 / 事件 smoke 使用前台 tab，会把浏览记录、收藏夹和个人动态条目回传到 `/api/sources/zhihu/task-result`；只有后端任务 payload 显式带 `profile_update=true` 时，新增条目才会由 API 自动写入 memory 并进入增量画像 pipeline。`search` / `hot` / `feed` / `creator` / `related` discovery 使用后台 tab，并把知乎候选分别回传为 `zhihu_search` / `zhihu_hot` / `zhihu_feed` / `zhihu_creator` / `zhihu_related`。任务 tab 不启动普通 `startCollector`，因此 CLI smoke、guided init 和 discovery 任务不会额外写入 `/api/events`
- 会启动 Reddit 任务轮询；收到 runtime stream 的 `reddit_task_available` 后立即打开 / 复用带 `openbiliclaw_reddit_task` 标记的 Reddit 任务 tab。`bootstrap_events` 会先读 `/api/me.json`，再用当前浏览器的 `reddit.com` 登录态读取 saved、upvoted 和 subscribed subreddit，回传 `reddit_saved` / `reddit_upvoted` / `reddit_subscribed` 初始化信号；`search` / `hot` / `subreddit` / `related` discovery 则读取同源 `.json` endpoint，回传 `reddit_search` / `reddit_hot` / `reddit_subreddit` / `reddit_related` 候选到 `/api/sources/reddit/task-result`。dispatcher 在 tab load 后会对 content script listener 做短重试，吸收真实页面 complete 早于 isolated script 注册的时序抖动；service worker 冷启动和热 reload 后会在顶层启动 Reddit poll alarm，避免只靠 `onInstalled/onStartup` 导致新来源不轮询
- 会启动 Linux.do 任务轮询；收到 `linuxdo_task_available` 后打开带稳定 query marker 的 `https://linux.do/?openbiliclaw_linuxdo_task=1` task tab。`bootstrap_events` 使用前台 tab 并先 GET `/session/current.json` 正面确认当前 username，再读 bookmarks / likes / read history；五种 discovery 使用后台 tab。任务模式只安装 executor，不运行普通 collector；结果只包含归一化 topic rows 与结构化错误，经 authenticated `/api/sources/linuxdo/task-result` 回传
- 扩展账号周期回拉由 `source_incremental_enabled=false` 全局默认关闭，且每个来源的 `sources.<slug>.incremental_enabled` 也默认关闭；关闭时后端不会检查 presence、预排账号任务、打开或切换标签页。只有总开关改为 `true`、对应来源开关也改为 `true`，且画像就绪、guided init 空闲、runtime-stream presence 在线时，才会按配置周期复用现有 XHS / 抖音 / YouTube / 知乎 / Reddit / Linux.do bootstrap scope。六源全局串行并继续走现有 dispatcher、登录态和 task-result 协议。这是“已安装扩展在线周期回拉”，不是后端绕过浏览器登录态的账号同步。抖音仍以 `douyin_incremental_hours=0` 逐源关闭；手动初始化 / 拉取和后台 discovery 不受影响
- 以 `client=background` 连接 `/api/runtime-stream` 后，后端先发送各浏览器登录态同步请求；Linux.do 的 `linuxdo_login_state_sync_requested` 只读取 `_t` 存在性并回传 bool。如果 `[sources.twitter].enabled=true`，还会收到 `x_cookie_sync_requested`，立即把当前 `x.com` 的 `auth_token` / `ct0` Cookie 回传到 `/api/sources/x/cookie`；B 站与抖音也保留各自 Cookie 请求。小红书 / 知乎 / Linux.do 由 startup、`cookies.onChanged` 和独立小时 alarm 兜底。同步请求只在用户配置的 OpenBiliClaw 后端与扩展间传递，不会为刷新配置页打开或请求平台页面。后端也把这条 WebSocket 作为 extension presence 信号
- 扩展账号周期回拉由 `source_incremental_enabled=false` 全局默认关闭，且每个来源的 `sources.<slug>.incremental_enabled` 也默认关闭；关闭时后端不会检查 presence、预排账号任务、打开或切换标签页。只有总开关改为 `true`、对应来源开关也改为 `true`，且画像就绪、guided init 空闲、runtime-stream presence 在线时，才会按配置周期复用现有 XHS / 抖音 / YouTube / 知乎 / Reddit / V2EX bootstrap scope。六源全局串行并继续走现有 dispatcher、登录态和 task-result 协议，不新增权限或抓取范围。这是“已安装扩展在线周期回拉”，不是后端绕过浏览器登录态的账号同步。抖音仍默认 `douyin_incremental_hours=0`
- 以 `client=background` 连接 `/api/runtime-stream` 后，后端先发送 `xhs_login_state_sync_requested` / `zhihu_login_state_sync_requested`，扩展只读取 Cookie store 并上报两个布尔心跳；如果 `[sources.twitter].enabled=true`，还会收到 `x_cookie_sync_requested`，立即把当前 `x.com` 的 `auth_token` / `ct0` Cookie 回传到 `/api/sources/x/cookie`，覆盖后端重启或 WebSocket 重连前已经发生的浏览器 Cookie 变化；如果本地缺少 B 站 Cookie，还会收到 `bilibili_cookie_sync_requested`；如果 `[sources.douyin].enabled=true` 且缺少抖音 Cookie，会收到 `douyin_cookie_sync_requested`。小红书 / 知乎另由 startup、`cookies.onChanged` 和独立小时 alarm 兜底，X 也保留 startup、变更监听和小时 alarm 兜底。所有同步请求只在本机后端与扩展间传递，不会为刷新配置页打开或请求平台页面。后端也把这条 WebSocket 作为 extension presence 信号：连接建立时允许后台 LLM 工作，最后一个连接断开后进入 `extension_disconnect_grace_seconds` 宽限；服务端 reader 会主动 `receive()` 检测 idle disconnect，避免浏览器断开后 presence 卡住
- 收到 `extension_e2e_run` 后会调用 `background/e2e-runner.ts`：按目标平台打开或复用标签页，复用时也会导航到平台稳定入口，等待页面 ready，再向 content script 发送 `OBC_E2E_EXECUTE`；runner 会先等待捕捉 buffer settle 并 flush，再把执行结果 POST 到 `/api/extension/e2e/result`，sendMessage / tab load / 整体运行都有独立超时，避免单个平台页面卡住整个后端请求
- generic event 若请求 `favorite` / `bookmark` mutation，runner 在打开 tab 前固定拒绝并且不发送 `OBC_E2E_EXECUTE`；即使同时塞入有效 envelope 也不放行。这是刻意的关联 fence：通用入口页 DOM runner 无法把授权的 `content_id` / target 绑定到将要点击的元素。只有 backend 发布的 dedicated event（空 generic platforms/actions + exact `native_save_authorization`）会调用单一 `/api/saved/{action}/sync`，轮询同一 durable task 并以 exact item/resolved action/target 关联后构造 six-field callback
- 连接 `/api/runtime-stream` 之前会先 HTTP `GET /api/ping`（2 秒超时）做一次活性探针，仅在后端可达时再 `new WebSocket(...)`。这样 fresh-install 用户先装扩展、后启动后端时，`chrome://extensions` 不会被浏览器层 WebSocket 失败计入「错误」徽标；探针失败后按固定 1 秒间隔继续重试，直到后端可达。探活不再打 `/api/health`：health 会同步等一次 embedding 实探（冷缓存可达数秒），2 秒预算下会把健康但冷启动的后端误判为掉线；`/api/ping` 返回 404（旧后端）时回退 `/api/health`（12 秒预算）
- 工具栏 badge 是只表达后端健康的三态决策表（`background/badge.ts` 纯函数，`tests/badge.test.ts` 覆盖）：后端不可达 → 浅灰 `!` + 「先运行 openbiliclaw start」title；可达但 `runtime-status.initialized=false` → 橙色 `!` + 「点击图标开始引导初始化」title；可达且已初始化 → 清空。它不再轮询 `/chat/pending-confirmations?count_only=1`、保存 pending 数字或响应 runtime-stream 做 count debounce，30 秒 event flush alarm 也不附带 badge count 刷新；待聊计数仍在 popup / desktop 对话入口内部。WS 连上时用 `/api/runtime-status`（零探针）刷新 init 态，收到 `init_completed` / `refresh.pool_updated` 事件立即清除橙标；`/api/events` 返回 200 + 全量 `not_initialized` 拒收也会点亮橙标（`flushResponseReportsUninitialized` 纯函数判定）。popup 侧 `getPopupState` 在 runtime 快照缺失且推荐为空时渲染「后端状态暂时没读到」过渡态而非 uninitialized；popup 内仍会显示「后端还没开张，先运行 `openbiliclaw start`」
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
  "platforms": ["douyin"],
  "actions": {"douyin": ["share"]},
  "timeout_seconds": 45,
  "allow_state_changing": false
}
```

默认只允许不改变账号状态或副作用极低的动作：`snapshot`、`scroll`、普通 `click` 和安全分享入口点击。`like`、`favorite`、`follow`、`comment`、X 的 `repost` 等会改变平台状态的动作，必须显式传 `allow_state_changing=true`。为了避免误测，`share` 只匹配安全分享入口的 click / share 捕捉结果，不会把 X 的转推 / repost mutation 算作普通分享成功。

Native-save 的边界更严格：`allow_state_changing=true` 单独不构成授权。Task 10 dedicated
模式还必须校验 exact `native_save_authorization` 的平台、action、public `content_id` 和
`expected_target`，并拒绝任何 generic actions。通用 `extension_e2e_run` DOM 分支不执行
native-save mutation；dedicated 分支也不会开入口 tab 或发送 `OBC_E2E_EXECUTE`，只会提交
已存在且已由 canonical URL/content type/content ID 与 production route 共同验证的 exact saved
membership 到 production sync API。后端发送比总 run timeout 少 1 秒的绝对 execution deadline，
把最后 1 秒保留给 six-field callback，避免 registry cleanup 竞态；endpoint 解析、认证刷新、
每个请求与按剩余时间截断的 poll sleep 均在相应 deadline 内。membership URL 预检与六平台
executor 使用相同的 exact host/path/query/fragment/port 规则，并同时匹配 fallback 后的
`resolved_action`。真实 broker 数据流为
`extension_native_save_jobs -> /api/sources/<slug>/next-task -> installed extension`，扩展只用
当前浏览器现有登录态执行，并在 mutation 前绑定 task/platform/item/content URL/action/target；
结果通过 authenticated `task-result` 回到后端。状态语义固定为
`synced` / `already_synced` / `login_required` / `rate_limited` /
`unsupported_content_type` / `extension_required` / `failed`，详细矩阵和安全记录格式见
[六平台 runbook](../testing/six-platform-native-save-e2e.md)。

content executor 的 selector 策略按平台收敛在 `src/content/e2e-executor.ts`：优先找当前页面可见的 action button / role button / aria-label，并排除“取消点赞”“已收藏”“Following”等反向或已激活状态；X / 小红书这类图标按钮会走平台专属 selector fallback，页面元素慢渲染时会在短窗口内重试。执行器只返回“是否点到了 DOM”，最终成功标准仍以后端是否在 `/api/events` 中看到对应平台、动作和时间窗口内的真实事件为准。

### B 站搜索兜底任务桥

`src/background/bili-task-dispatcher.ts` 会轮询后端 `/api/sources/bili/next-task`。这条链路不是 B 站 discovery 的常驻主路径：后端 `BilibiliExtensionSearchProducer` 只有在 API search 已进入冷却或 `search_dom_fallback_remaining()>0`、扩展 presence 在线、候选池低于配额且近期没有同类任务时才会入队。service worker 同时监听 runtime stream 的 `bili_task_available`，收到后立即 `pollBiliTaskNow()`，alarm 轮询作为兜底。

扩展领取到 search task 后，会打开后台 tab：

```json
{
  "task_id": "...",
  "type": "search",
  "query": "机械键盘 声音",
  "limit": 5,
  "page_size": 5,
  "order": "pubdate",
  "discovery_lane": "recent"
}
```

普通任务的 `order/discovery_lane` 省略；producer 每轮只把第一个兜底任务标为上述近期 lane，并限制为 5 条。dispatcher 对该任务导航到 `https://search.bilibili.com/all?keyword=...&order=pubdate`，普通任务仍使用原 URL；它只接受 `totalrank/pubdate` 和 `recent` 这组封闭枚举。等 tab ready 后 dispatcher 发送 `BILI_TASK_EXECUTE`；如果 Chrome 报 content script listener 暂未就绪，会在 8 秒窗口内短重试，吸收真实页面 `complete` 早于 isolated content script 注册的时序抖动。`src/content/bili/task-executor.ts` 不在 isolated world 里直连 B 站 API，也不伪造 WBI 签名；它只等待真实搜索页渲染出 `.bili-video-card` / `.video-list-item`，从 DOM 卡片里提取 `bvid`、标题、UP 主、播放数、封面、时长和简介，再用 `BILI_TASK_RESULT` 回给 service worker。service worker POST 到 `/api/sources/bili/task-result` 后，后端把结果写入 `discovery_candidates`，继续走共享 evaluator / admission，而不是由插件直接写推荐池。近期结果仍使用 `source_strategy="bili-extension-search"`，只在 `source_context` 和 raw payload 中留下 `recent` provenance。

真实联调可用两档验证：

- 手工任务：后端重启到包含该分支的代码，加载 `extension/dist/`，让 `/api/sources/bili/next-task` 返回 search task；扩展应打开后台 B 站搜索页，最终 task 状态变为 `completed`，对应候选带 `source_strategy="bili-extension-search"`。
- 自动触发 E2E：`BILI_EXTENSION_E2E=1 .venv/bin/pytest tests/test_bili_extension_browser_e2e.py -q -s`。该测试启动临时 FastAPI app + 临时 SQLite，使用 Playwright 持久上下文加载 unpacked extension，等待真实 runtime-stream presence，把进程内 `BilibiliAPIClient` 置入 search cooldown，再调用真实 `BilibiliExtensionSearchProducer` 入队；扩展必须领取任务、打开真实 B 站搜索页并回传 DOM 结果。测试不使用生产数据库，也不需要生产 debug endpoint。

### 小红书任务桥

`src/background/xhs-task-dispatcher.ts` 会轮询后端 `/api/sources/xhs/next-task`。后端返回任务前会动态检查小红书来源开关和全局 scheduler；任一关闭时，已有自动发现任务保持 `pending` 且返回 bodyless 204，扩展不会打开 search / creator / bootstrap 页面。用户主动发起的 native-save job 独立于 discovery 开关，但仍服从下述平台风控冷却。扩展先取得与抖音共享的后台任务互斥锁，再调用会原子 claim 的 `/next-task`；其它来源正在运行时不会提前把 XHS 任务领成无人执行的 `in_progress`。来源开启时，后端把 `xhs_tasks.status` 从 `pending` 原子切到 `in_progress` 并写入 `claimed_at`；claim 事务使用独立短连接执行，避免和 API 进程共享 SQLite connection 上的其他请求互相嵌套事务。partial 回写会保留 `in_progress`，最终 `ok / empty / failed` 才进入终态，15 分钟无回写的领取会重新变为可领取。这个领取态用于挡住多个扩展实例、service worker 重启或多次手动命令造成的同一 `bootstrap_profile` 前台 tab 重复打开。

search / creator 的领取节奏由后端按 `[sources.xiaohongshu].task_interval_seconds` 目标值施加 ±25% 稳定抖动后写入 SQLite 状态，不依赖 MV3 alarm 是否重启；默认目标 20 分钟，即实际窗口约 15–25 分钟。搜索预算默认每天 20 次，producer 每 20 分钟检查一次，并只把 pending + in-progress 搜索队列补到 5 条，积压满时不会 claim planner 关键词或调用 LLM。`src/content/xhs/risk-control.ts` 会在任务执行前后检查可见安全验证对话框，并仅在页面没有正常卡片时检查整页的“操作频繁 / Too Many Requests / HTTP 429”信号；普通笔记正文里的相同词不会直接触发。命中后 executor 回传 `status="rate_limited"` 与枚举原因，后端将平台级冷却和连续次数写入 `xhs_task_runtime_state`，按 `1h → 2h → 4h → 8h → 16h → 24h` 退避；同一活动冷却内重复上报不增加次数，冷却后成功完成普通 search / creator 才重置。冷却期 `/next-task` 返回 204 + `Retry-After`，正在执行的 planner 关键词回到 `pending` 且不增加 attempts，producer 也停止继续生成；扩展不会把验证页正文作为 debug 上传。20 分钟和每天 20 次都是工程安全默认值，不是小红书官方阈值；已有显式配置保持不变。

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

到 profile 页后，executor 读取 `__INITIAL_STATE__.user.notes` 分组：`[0]` 为发布，`[1]` 为收藏，`[2]` 为赞过；如果收藏 / 赞过分组尚未加载，会尝试点击对应 profile tab 等待页面自己补齐 state，再退回到已渲染 DOM 卡片解析。state 解析兼容小红书 profile noteCard 结构（`noteCard.displayTitle`、`noteCard.user.nickName`、`noteCard.cover.urlDefault`），滚动后每轮也会把 state 和 DOM 结果合并，避免只看当前可见 DOM 时漏掉已加载但被虚拟列表移出的卡片。默认任务不滚动；如果后端任务显式传入 `max_scroll_rounds > 0`，executor 会优先探测小红书实际 feed / waterfall / masonry 滚动容器，并排除 `clientHeight` 过小、`overflow-y` 不是 `auto/scroll/overlay`、以及 `channel-list` / sidebar 这类非内容侧栏；如果没有可用内容容器，会回退到窗口级小步 `wheel` / `scrollBy`，贴近用户手动前台滚动。任务会运行到达到 `max_items_per_scope`、达到滚动轮数上限，或连续五轮没有新增卡片。每个 scope 的首批和后续新增卡片会先以 `status="partial"` 回传，partial 批次也会按该 scope 剩余名额裁剪，background 等后端确认后再继续，最后用 `status="ok"` 完成任务。后端不把扩展侧裁剪当作信任边界：它会从已入队任务的不可变 payload 重读允许的 `scopes` 与 `max_items_per_scope`，只接纳声明 scope 的 note 和整数计数，再对当前结果与新批次按首次出现顺序做累计裁剪；canonical URL 只从已接纳 note 派生。已占额度的同 identity note 可补首个有效 `xsec_token` / tokenized URL 和发布时间，但不会新增条目或替换首写标题。缺失或空 scope 列表与扩展默认一致，回退为 saved / liked / xhs_history。因此 service worker 重启、partial/final 批次错位或终态重试都不能扩大任务预算。

后端可以按任务控制滚动节奏，不需要改插件常量：

| payload 字段 | 默认值 | 插件端裁剪 | 说明 |
|---|---:|---:|---|
| `scroll_wait_ms` | `1200` | `500..5000` | 每轮滚动后等待小红书瀑布流加载的时间 |
| `max_stagnant_scroll_rounds` | `5` | `1..10` | 连续多少轮没有新增卡片后停止 |

dispatcher 会把这两个字段透传给 content script；如果 `scroll_wait_ms` 拉长，background 也会同步放宽任务 timeout，最多 6 分钟。

滚动任务的 debug 会带 `scroll_candidates` 和 `tab_load_results[scope].scroll_metrics`：前者列出页面上排名靠前的滚动候选、`overflow-y`、note 数和评分；后者按每轮记录实际滚动目标、`scroll_top / scroll_height / client_height`、滚动前后位置、新增卡片数和累计卡片数。真实联调时可用它区分“页面到底了”“滚错容器了”和“页面没有暴露更深的滚动节点”。

这条链路仍不直接调用小红书 API、不读取 cookie、不接触 Chrome 浏览器历史。这里的 `xhs_history` 指“小红书网页自己明确暴露的浏览记录 / 足迹 state”，不会把普通 `/explore` 推荐流当成浏览记录；如果小红书网页没有暴露稳定入口，就返回 0 条并让初始化继续。

search / creator 任务的 note 路由与被动采集共用 `NOTE_ANCHOR_SELECTOR`，覆盖 `/explore/{id}`、`/discovery/item/{id}` 与 `/search_result/{id}`。search 与 creator 现在都以 inactive tab 打开；search 优先消费 MAIN-world bridge 从页面自身 search notes 响应归一化出的公开卡片 metadata，响应早于 isolated listener 时由同页 replay 缓存补发，因此隐藏页即使不挂载虚拟列表也能完成 discover。DOM 卡片保留为响应 schema 漂移兜底，creator 继续走既有 DOM 后台路径；需要点击本人入口和受控滚动的 `bootstrap_profile` 仍为前台。成功回执用 content-free `debug.xhs_discovery.source=search_api|rendered_dom` 标记实际数据路径。若页面出现可见登录弹层，executor 会优先返回 `xhs_login_required`，后端用这份直接页面证据覆盖仅由旧 `web_session` Cookie 推断的登录态。搜索响应 / 卡片最多等待 12 秒，仍不自动滚动且低于 dispatcher 的 30 秒 task timeout。若等待后仍为空，executor 返回 `status="empty"`、`error="xhs_empty_result"`，`debug.xhs_search_empty` 只包含 pathname（不含 query）、ready/visibility、viewport、body 子节点数和三类 anchor 计数；搜索词、页面标题/正文、href、Cookie、raw API response 与 state 内容一律不上报。后端对未带 error 的旧扩展空结果也补成同一稳定错误码，同时保持它是可重试失败而不是误判为平台风控。

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

`src/content/dy/task-executor.ts` 负责在页面内切换 scope、滚动与回传。`src/main/dy-fetch-tap.ts` 运行在 MAIN world，拦截抖音页面 fetch，并对四个账号 scope 走站内分页 API harvester：作品 / 收藏 / 喜欢使用 `max_cursor`，关注使用 `max_time`。分页前不再只等页面偶然发出带 `sec_user_id` 的请求：`#RENDER_DATA` 只有显式 `isLogin=true` 时才提供候选，随后仍必须由 MAIN-world `/aweme/v1/web/user/profile/self/` 正面确认；该端点只接受 `status_code=0` 且 `user.sec_uid` 非空，冲突时以它为准，也只有它确认的结果能在同一 tab 内缓存 / 合并并发探测。常驻 fetch / XHR wrapper 不从被动请求 URL 提取或记录 `sec_user_id`；身份消息只会由用户触发的 bootstrap 身份请求返回。该 bridge 使用页面自身已登录 fetch 上下文，不传 Cookie 值；身份响应传公开 `sec_uid`，分页响应传任务所需的解析后条目，不转发未裁剪的原始响应对象。MAIN / isolated 两侧的 `postMessage` listener 都要求 `event.source === window` 且 `event.origin === window.location.origin`；这只降低跨 frame / 页面噪声误接收，不是授权边界，同页脚本仍可发消息，因此 sentinel、request ID 与 payload 校验继续保留。

fetch tap 的安装状态保存在页面 Window 上，而不是单次 bundle closure：同一个 Douyin SPA 文档被 dispatcher、content script 或更新后的 bundle 重复注入时，仍然存活的 fetch / XHR wrapper 与 API listener 不会重复安装；如果页面 bundle 后来替换了 fetch 或 XHR，则下一次 SDK-ready 校验 / 动态重注入会只重包当前原语。API bridge 以 `type + requestId` 做 single-flight；完成后立即释放，悬挂请求以 120 秒 TTL 和 128 条上限清理，过期响应不再发回已经结束的 isolated listener。

采集到的条目通过 `postMessage` 回到 isolated world 后进入 `BootstrapItemSink` 去重，再以 `status="partial"` 分批 POST 到 `/api/sources/dy/task-result`。全部 scope 正常完成时最终状态为 `ok`；身份仍不可得、API 首屏业务状态失败、后续 HTTP / cursor 页中断、游标缺失 / 非法 / 停滞 / 成环，或 50 页安全上限耗尽时，保留已采到的有效条目并以终态 `degraded` 完成，最终 debug 同时保存各 scope 状态与稳定原因。后端将 DB 任务标记为已结束，同时把 `result_json.status="degraded"` 留给 CLI / init 展示；终态后的迟到 partial / 重试回调会被幂等忽略，不能清除降级状态或重复传播事件。CLI 最终摘要与 API init 都把它显示为“部分完成”，阶段 1 也以 `warning / douyin_degraded` 完成，但已采事件仍参与本次画像建模；近期任务去重不会复用这个降级终态，下一次会重新入队补齐分页。新增 videos 仍按既有映射转成统一事件：发布 → `view`，收藏 → `favorite`，点赞 → `like`，关注 → `follow`。扩展不再额外 POST 临时调试事件到后端；`task-result` 中已有的结构化 `debug` 字段仍随正常任务结果传递。

fetch tap 除 manifest 的 document-start 注入外，还会在 dispatcher 与 content script 的 SPA 重注入路径动态加载。Chrome/Edge 从仓库根或 release archive 加载时资源通常是 `dist/main/dy-fetch-tap.js`；部分既有 unpacked 构建把输出目录本身作为资源根，文件则是 `main/dy-fetch-tap.js`。`shared/asset-prefix.ts::runtimeAssetCandidates()` 统一生成候选，background 的 `chrome.scripting.executeScript` 与 content 的 `<script src=chrome.runtime.getURL(...)>` 都按同一顺序回退；Firefox 的空 prefix 只产生一个 `main/...`，不会重复注入。该回退只改变扩展自身文件定位，不扩大 host permission，也不触发额外平台请求。

Douyin dispatcher 会先确认当前 worker 具备 `chrome.tabs.create`，再在 `/next-task` claim 前获取跨来源 mutex，并让 alarm 与 runtime-stream kick 共用同一个 poll promise；残缺运行环境或锁忙时不 claim，并发唤醒也只认领一次。claim 后 executor 用 `accepted / declined` 握手，拒绝任务会先回传失败再释放锁。注入资源明确失败、tap 未就绪、API / UI / 导航失败且零候选时，search / hot / feed 会回传稳定 `failed` 终态；hot 不再把 `failed` 继续合并成 partial 后再写 `ok`。feed 若唯一问题是 `feed_no_observed_response`，dispatcher 会在原后台 tab 最多执行一次 `tabs.reload({bypassCache:true})` 并重试，任务预算相应为 120 秒；不会激活 tab，也不会对真实空、限流、风控、登录或注入错误重试。已获得 DOM / passive 候选时仍保留有效结果，只有链路正常且确实没有内容时才使用 `empty`。task-result 回传要求后端 2xx ACK，并对同一幂等 body 做最多 3 次有界退避重试；终态未确认时不清理本地 lifecycle，也不继续认领下一条任务。

扩展 mutex 只能覆盖同一 service worker。后端 `DyTaskQueue.next_pending()` 因此在 `BEGIN IMMEDIATE` 领取事务内额外检查全表未过期 `in_progress` lease：存在时返回 204，不把第二条任务交给另一个 unpacked 扩展 ID 或 Chrome profile；15 分钟过期 lease 仍按原协议优先重领并修复 staged result。

本地 `npm run build` 只更新磁盘上的 bundle，不会替正在运行的 Chrome service worker 热换代码；真实账号复测前需要在 `chrome://extensions` 对 unpacked OpenBiliClaw 点一次“重新加载”，或在扩展 runtime-stream 已连接时调用本地开发端点 `POST /api/extension/reload`。后端 debug 若仍只出现 `Could not load file: 'dist/main/dy-fetch-tap.js'`，说明浏览器还在运行旧单路径 bundle，不能据此判断新 fallback 失败；新版本成功状态会携带实际命中的 `ok_file=<candidate>`。

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

卡片提取同时兼容 Polymer 旧组件（`ytd-video-renderer` / `ytd-channel-renderer` 等）与 Lit 新组件（`yt-video-card-renderer` / `ytd-video-card-renderer` / `yt-lockup-view-model` / `ytd-reel-item-renderer` / `yt-channel-card-renderer` / `ytd-channel-card-renderer`），并通过 `queryIncludingShadow()` 递归查询 open shadow root 内的标题 / 链接 / 频道 / 封面；卡片内任意 `/watch` / `/shorts` 链接与 `aria-label` / `title` 兜底避免新版布局整页 0 条（issue #173）。

任务超时（issue #178）使用 `chrome.alarms` 而非 `setTimeout`：claim 后 dispatcher 把 `{task_id, deadline_at, tab_id}` 写入 `chrome.storage.session`（不可用时回退 `chrome.storage.local`），并创建 `openbiliclaw-yt-task-timeout` 一次性 alarm（`when: deadline_at`）。MV3 service worker 休眠不会丢失 alarm；alarm 触发或 worker 重启后检测到持久化的活动任务记录时，dispatcher 会回传 `task_timeout` / `service_worker_restart` 终态、关闭孤儿任务 tab 并清理 alarm 与 session 记录。后端另有 stale `in_progress` 租约回收作为第二道防线。

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

dispatcher 等待首页 ready 时会同时处理两种情况：正常的 `chrome.tabs.onUpdated(status="complete")`，以及抖音 SPA 没有再发完整 `complete` 事件的 fallback timer，避免任务卡住直到 `task_timeout`。search 任务按关键词数计算超时窗口，单关键词至少 180 秒，覆盖首页打开、DOM 搜索触发、搜索结果页路由确认、页面自身响应和 DOM 解析的真实耗时；后端 `DouyinPluginSearchClient` 默认也等 180 秒，避免插件刚开始执行 DOM 操作就被后端清成 stale。`src/content/douyin.ts` 会尝试触发页面搜索 UI、热点入口点击或推荐流滚动；search 会区分 `ui_triggered`（已提交）和 `search_navigation_ok`（URL 已进入真实搜索结果路由），防止搜索建议或登录弹窗被误判为搜索结果页。`src/main/dy-fetch-tap.ts` 作为 manifest 声明的 MAIN-world `document_start` 被动 fetch / XHR tap，把页面自己发出的 search / related / feed 响应转成候选；如果这些消息早于任务 collector，isolated content script 会按 `scope:aweme_id` 去重，最多保留 256 条 / 120 秒，并在对应任务启动时一次性 drain。feed 兼容当前页面实际发出的 `/aweme/v2/web/module/feed/`，search 兼容 `/aweme/v1/web/general/search/stream/` chunked JSON；DOM fallback 同时识别 `a[href*="/video/"]`、`div[data-aweme-id]`、非 anchor `href` 与 `video_<id>` class，并从卡片语义文本中补标题 / 作者，因此页面不再发续请求时仍可读取当前已渲染推荐。合法空响应会增加 `passive_responses_observed`，只有响应和 DOM 都未提供内容时才是 `feed_no_observed_response`。search / hot / feed discovery 不主动访问 `/search/...`、`/hot/...` 快捷 URL；search 在被动 fetch tap 和 DOM 解析不足时会调用已登录页面的 search API bridge 兜底，hot 会把 hot board 的 `group_id` 作为 `seed_aweme_id` 透传给扩展，优先执行带 seed 的热词，并在 DOM 点击 / 被动监听不足时用已登录页面的 related API bridge 拉取相关视频，feed 不主动调用 API bridge。搜索结果以 `scope="dy_search"`、热点结果以 `scope="dy_hot"`、首页推荐结果以 `scope="dy_feed"` 回写到 `dy_tasks.result_json`，不会转成初始化画像事件；content script 会在回传前按目标 scope 过滤候选，避免首页 feed 响应混入 search / hot 结果；`DouyinPluginSearchClient` 会把这些候选映射成 aweme-like JSON，分别以 `dy-plugin-search` / `dy-plugin-hot-related` / `dy-plugin-feed` 进入 `discovery_candidates` 待评估池，再由后端共享 evaluator 判定是否进入推荐池。插件任务会区分真实 `empty` 与 `timeout / failed`：基础设施失败返回空候选但保留失败终态，direct-cookie fallback 仅保留给显式诊断路径。真实 search 响应带 `search_nil_info.search_nil_item="hit_shark"` 且无 `data/aweme_list` 时按反爬失败处理，让 runtime 使用故障退避而不是分钟级重试。

search 首次首页 ready 后直接注入并提交，不再对同一 URL 做冗余 `tabs.update`；多关键词任务会为下一关键词换用新的后台首页 tab，隔离上一关键词的迟到响应与虚拟列表 DOM。dispatcher 会在提交前监听目标搜索路由；若 UI 触发真实 document navigation，新文档 ready 后用 `resume_after_navigation` 只恢复采集，若只是 SPA 路由变化，同文档 execution key 会拒绝重复执行。search API bridge 的 isolated wait 收紧为 20 秒（MAIN request 自身仍有更短 abort），避免后台标签页计时延迟把单词任务推到总看门狗边缘。

CLI 入口：

- `openbiliclaw search-douyin -k 猫 --max-items-per-keyword 10 -w 180`：真实 smoke 插件搜索召回。
- `discover-douyin --source hot --limit 3 --no-cache --no-evaluate`：真实 smoke 热榜 related 召回。
- `discover-douyin --source feed --limit 3 --no-cache --no-evaluate`：真实 smoke 首页推荐流召回。
- direct-cookie `discover-douyin --source search` 如果遇到空结果，可用 `search-douyin` 判断登录浏览器路径是否仍能拉到候选。

### Linux.do 任务桥

`src/background/linuxdo-task-dispatcher.ts` 使用 `authenticatedFetch` 轮询 `/api/sources/linuxdo/next-task`，响应 runtime-stream 的 `linuxdo_task_available`，并保留 alarm 兜底。dispatcher 在 claim 前取得共享 task mutex，避免先把任务租成 `in_progress`、随后又因其它来源正在使用任务 tab 而无人执行；mutex stale 驱逐窗口为 36 分钟。每个任务绑定唯一 task ID、tab ID 和绝对超时，只清理自己创建的 tab，并在执行前把无凭据的 `{task, tab_id, deadline_at}` 临时写入 `chrome.storage.local`，终态或超时后删除。content-script listener 若与 Discourse challenge / SPA 初始化竞态，dispatcher 会在 readiness 窗口内短间隔重试；窗口耗尽后最多重载一次原 task tab，再在同一 task ID 上重发，失败才回传 `sendMessage_failed`，不会因瞬时 listener 缺失释放租约并抢另一条任务。service worker 启动时先恢复 Linux.do runner，再建立后端 session / runtime stream 并开启 polling；恢复会向同一 task ID 重发执行消息。仅 MV3 worker 回收而 content context 仍存活时，executor 合并重复消息且只执行一次；开发热重载销毁旧 context 时会安全重放只读 GET，后端 first-final 不可变协议屏蔽迟到重复结果。

`src/content/linuxdo/task-mode.ts` 用稳定 query marker `?openbiliclaw_linuxdo_task=1` 识别任务 tab，并兼容已存在的旧 hash marker。普通 `linux.do` 页面运行 `linuxdoAdapter` 的通用 collector；任务 tab 只安装 `LINUXDO_TASK_EXECUTE` listener，不启动 collector，故 search/bootstrap 不会污染 `/api/events`。真实 Chrome E2E 发现 Discourse SPA 会在 `document_idle` 前清除旧 hash；改用 query marker 后复跑 feed，Linux.do 行为事件增量为 0。

`src/content/linuxdo/task-executor.ts` 的站点访问边界是：

- 只接受 `https://linux.do`，只用 `GET`、`credentials: "include"` 与 `Accept: application/json`；不存在站内写操作。
- discovery 五路 endpoint：`/search.json`、`/hot.json`（400/404 才回退 `/top.json?period=weekly`）、`/latest.json`、`/topics/created-by/<username>.json`；related 先读 `/t/<topic_id>.json` 的 seed 标题/首帖，再调用 `/topics/similar_to.json`，过滤 seed 自身。Discourse `suggested_topics` 是 new/unread/random 站点建议，不再冒充语义 related。
- bootstrap 三路 endpoint：先 `/session/current.json` 正面确认 `current_user.username`，再读 `/u/<me>/bookmarks.json`、`/user_actions.json?filter=1` 与 `/read.json`，分别回传 favorite / like / view。
- 生产任务的单请求默认且最多 30 秒；discovery 默认且最多 5 页，bootstrap 按 `max(5, ceil(limit / 20))` 自动扩页（300 条对应 15 页）且最多 15 页。每 scope / 输入最多 300 条、每任务输入列表最多 5 个、单响应最多 2 MiB；`request_interval_seconds` 限制在 `0..30`。content executor 另以 120 秒 / 50 页 / 20 输入作第二层绝对防御，但 dispatcher 不会执行触达这些值的后端任务。HTML challenge、错误 content type、坏 JSON 和过大响应只生成结构化错误，不回传正文。
- 默认端到端总等待为 32.5 分钟：pending 最多约 3 分钟等扩展领取；进入 `in_progress` 后按任务广度/页数/节流计算期限，最宽合法形状约 29 分钟，再留 30 秒结果余量；后端 claim lease 约 35 分钟。CLI/env 显式等待值从入队时起就是总硬上限，较小值可能截断已领取任务。
- bootstrap 有些 scope 成功、有些 scope 失败时终态是 `degraded`，保留已采 items/counts 并在 `debug.scope_errors` 报告有界错误；全部 scope 失败才是 `failed`。两者都不进入 Linux.do 默认 6 小时近期任务复用。
- discovery 某一页或多关键词 / 多 creator / 多 related 输入中途失败时同样保留此前 items，以 `degraded` + `debug.input_errors` 完成；只有零有效 item 才是 `failed`。后端 producer 仍接纳 degraded 分支的有效 topic，并显式报告本轮部分完成。
- dispatcher 在领取任务后发现非法 type / scope / 数值 payload 时会立即 POST `failed / invalid_task_payload`，不会让坏任务占满长 lease；合法任务仍按 task ID / tab ID 隔离并只关闭自己创建的 tab。
- topic row 固定为 `content_type="post"`、`content_id="topic:<positive id>"`，保留裁剪后的 title/url/author/author_url/summary/category/tags/views/like_count/reply_count/published_at；仅写上游实际提供的字段。搜索命中的 post 作者/点赞不冒充主题 OP/总赞，bootstrap 则会把同一任务、同一 topic 在 bookmarks/likes/read-history 任一路径已有的 engagement 真值补到其它 scope 的缺失字段，不额外请求详情页。

`cookie-sync.ts` 只判断 Linux.do `_t` 是否存在且非空，POST `/api/sources/linuxdo/login-state` 的 body 永远是 `{"logged_in": boolean}`。Cookie 值、其它 Cookie、CSRF 字段与原始 JSON/HTML 响应不会上传。`_t=true` 只供 optional-login source-auth 展示；个人任务仍必须通过 `/session/current.json`。

当前 Linux.do adapter/task-mode/executor/dispatcher/cookie-sync 已有专属单测，Chrome 与 Firefox bundle 均构建通过。2026-08-09 已用实际安装的 Chrome unpacked extension 和真实已登录账号完成热重载、67 条 bootstrap 事件、五种 discovery 任务、正式候选入池与无敏感字段回传 E2E；Firefox 尚未做同等真实账号安装版验收。完整后端 schema、CLI、实测数据与审核边界见 [Linux.do 来源文档](linuxdo.md)。

### `popup/`

`popup/` 目录当前承载 side panel 页面，已具备：

- guided init 来源选择新增 Bangumi 与 V2EX：Bangumi 不要求浏览器登录，选中后显示公开用户名输入，并通过 `source_options.bangumi.username` 发送；V2EX 可填写公开用户名，也可留空由真实 V2EX 页面导航栏观察账号，并通过 `source_options.v2ex.username` 发送。Bangumi-only 空用户名在客户端提示，后端仍会权威拒绝；V2EX 任务页只读采集四个 scope。混合来源空用户名允许继续。popup 把输入草稿保存在页面 state，前置检查失败或 idle 面板重渲染不会丢失；显式清空会原样送到后端，不会回退旧配置用户名。

- 后端连接状态检查：离线判定以 `/api/ping` 为准，顶部徽标区分绿色「已连接」、琥珀色「重连中」和红色「未连接」。`runtime-stream` 断开时先进入「重连中」并立即复检 `/api/ping`：HTTP 仍通则保留 API 可用状态并等待 WebSocket 自行重连，只有 ping 返回失败或抛错才进入「未连接」并启动 `popup-connection-poller.js` 每 1 秒重探测；HTTP 恢复后先回到「重连中」，流重新打开后才显示「已连接」。协调器使用 revision guard 忽略连接恢复后才返回的旧失败探活，主动切换后端地址关闭旧流也不会触发故障断线提示
- 设置页的协议（HTTP / HTTPS）、后端地址（默认 `127.0.0.1`）和端口（默认 `8420`）由 `popup-backend-config.js` 一起写入 `chrome.storage.local`。局域网 / 远程地址保存前通过 `optional_host_permissions` 请求精确 origin；公网主机名和公网 IP 不允许 HTTP。官方 Docker 公网入口使用 `docker-compose.https.yml`：协议选 HTTPS、主机填公开 DNS 名称、端口填 443，并用默认关闭的 `ext-key` 设备密钥配对。popup、service worker、任务派发、cookie 同步和调试中继都在调用时解析当前 endpoint；变更后清除旧短会话并重连。远程认证使用 `obc_extension_device_key` 换取结构化 `obc_auth_session`，普通 HTTP 发 Bearer Header，只有 runtime WebSocket 和图片代理 URL 携带短会话 query。设置页的配对状态只以服务端 `/api/auth/status` 返回的 `authenticated` 为准；本地短会话的 `expires_at` 仅用于决定复用或换票，不得覆盖服务端的撤销或 epoch 失效判决。
- 顶部手机图标会打开移动端二维码面板，二维码完全在 popup 本地生成，指向当前插件后端地址的 `/m/`；scheme 只接受已规范化的 `https`，其他值安全回落 `http`，因此 TLS / Caddy 后端会生成 `https://…/m/` 而不会把明文请求发到 TLS 端口。打开后的 `/m/` 页面已带 PWA manifest 与 iOS Web Clip 元数据，可从手机浏览器保存到主屏幕；当前不提供离线缓存，仍需手机能访问运行中的本地后端。当前 host 仍是 `127.0.0.1` / `localhost` / IPv6 loopback `::1`（含 URL 的 `[::1]` 形态）时，插件会以相同 HTTP/HTTPS scheme 通过轻量 `/api/qr-info` 读取后端探测到的局域网 IP 并替换二维码 host；公网 DNS host 原样保留。端点失败或没有有效 LAN IP 才保留 loopback URL 与警告。在 460px 以下侧边栏宽度，顶部 Web / 二维码 / 消息 / 设置按钮会换到品牌区下一行靠右排列，避免和标题 / 状态徽标重叠
- 设置页调度区的「停止后台 LLM 请求」写入 `scheduler.enabled=false`；开启后会暂停 daemon-owned 定时发现、候选池预计算和画像更新里的 LLM / embedding 调用，推荐列表不会自动补充新内容，候选池为空时可能暂时没有推荐。「关闭浏览器后停止后台」写入 `scheduler.pause_on_extension_disconnect=true`，断开宽限秒数写入 `scheduler.extension_disconnect_grace_seconds`；所有扩展窗口断开并超过宽限期后，后台 LLM / embedding 工作暂停，重新打开浏览器后恢复。手动刷新和显式 CLI / API 操作仍按用户动作执行
- 从 `/api/recommendations` 拉取推荐列表
- 从 `/api/profile-summary` 同步 `speculative_interests` 与 `speculative_avoidances`，分别渲染待确认兴趣和待确认避雷方向；正向兴趣项会保留 `probe_mode` / `challenge`，profile 页面点击“喜欢”会带 `surface="profile"`，不和 runtime inbox 的默认 probe 确认混在一起
- `/api/profile-summary.active_insights` 在 popup、桌面 Web 和移动 Web 的画像/认知更新区只展示假设、置信度、证据与既有验证态，不再渲染「准 / 不准」按钮；需要处理的假设统一从对话 tab 的「待聊确认」进入 durable 卡片。兴趣/避雷 probe 仍是推荐流内独立探针，不属于洞察确认迁移范围
- 收到 `avoidance.probe` runtime 事件后在 inbox 渲染避雷确认卡；「确认避雷 / 搁置避雷 / 不是雷点 / 多聊聊」分别以 `confirm / defer / reject / chat` 调 `/api/avoidance-probes/respond`，其中 `chat` 进入 `scope=avoidance_probe` 的 durable turn
- 高级功能 Tab 在桌面 Web 与 side panel 保持同一信息架构：固定为「候选评分模式 / 推荐增强 / 多模态处理 / 搜索词生成 / 认知循环预算」五个 section。候选评分下拉把 canonical `llm / shadow / learned` 显示为 `Agent（默认） / Shadow（校准观察） / Learned（仅相关性，实验性）`，明确 Learned 只应在人工运行质量门禁并确认通过后启用、只影响后续候选且不会重算已有推荐，保存时仍写 `discovery.eval_scorer`，不新增不兼容的 `agent` wire 值。推荐增强的 P1/P2/P3 都是排序信号加权而非过滤，P1/P3 依赖图像 Embedding、P2 只需文本 Embedding，P1 每个极性反馈不足 8 条时安全 no-op；关闭开关会保留缓存和参数、回退原排序且不影响主流程，关键帧和弹幕目前仅作用于 B 站。多模态 section 明确区分图像 Embedding 能力与候选封面参与 LLM 评估，两者相互独立；模型 provider / 模型 / 凭据 / 探测仍在模型 Tab，调度 Tab 只保留调度项。两端都在 discovery 快照展开后显式覆盖用户可编辑字段，切换模式或关闭开关不会丢其它高级参数。
- 设置页会通过 `/api/config` 读取并保存后端配置，保存后请求后端热重载；当前覆盖 LLM/embedding、B 站与通用 source 浏览器、十一来源 source 开关与 discovery 预算、Bangumi 公开用户名/五种合法条目类型/分支/节流/bootstrap 上限、Linux.do / V2EX / 微博登录态任务边界、数据目录、SQLite、调度、更新、候选池平台配比、探针与日志参数。Linux.do 卡片另展示 optional-login、五路 discovery、节流与 bootstrap 上限；Bangumi 凭据行明确显示公开只读 API。
- 设置页会通过 `/api/config` 读取并保存后端配置，保存后请求后端热重载；当前覆盖 LLM/embedding、B 站与通用 source 浏览器、十一来源 source 开关与 discovery 预算、Bangumi 公开用户名/五种合法条目类型/分支/节流/bootstrap 上限、V2EX PAT/五种 discovery 分支/Node 与 Tab 配置、微博登录态 init 任务、数据目录、SQLite、调度、更新、候选池平台配比、探针与日志参数。Bangumi 凭据行明确显示“公开只读 API，无需 Cookie/token”，V2EX 凭据行明确显示“公开只读 API，PAT 可选”，微博凭据行明确显示“公开发现匿名，个人 init 需登录态任务”。
- 成功读取 `/api/config` 后，popup API 会把配置快照写入 `chrome.storage.local["openbiliclaw.config_cache"]`。后端离线时设置页会读取缓存填表，并显示缓存时间；没有缓存时显示错误横条且不伪造默认值
- 后端返回 `degraded=true` 时，设置页会在表单顶部展示降级原因和 blocking issues；模型实例/整链测试及模型发现属于无写入恢复控制面，在 degraded 状态仍使用当前草稿执行。保存响应正常为 `reloaded=true / restart_required=false`，同一进程立即解除降级；若旧后端或异常 bootstrap 返回 `restart_required=true`，插件仍用 warning tone 提示重启，并以重启后的权威 `/api/config` 为准，不把本地表单冒充已生效配置
- 设置页的“按已有信号建议比例”会把当前页面上尚未保存的平台开关和比例一并 POST 到 `/api/config/source-share-suggestion`，按本地事件库的平台分布填入 B 站 / 小红书 / 抖音 / YouTube 占比，用户仍需点击保存才写入 `config.toml`
- 设置页保存配置时会保留后端已有的高级字段：`save_config()` 会串行化 scheduler speculation / auto-update 和 logging unmanaged cleanup 字段，避免 UI 修改常用项时把隐藏高级项写回默认值
- 设置页“版本与更新”只展示后端更新状态并调用 `/api/update-status`、`/api/update/check`、`/api/update/apply` 的 backend target；插件版本行只读取本地 manifest 版本并链接 GitHub Releases。
- 推荐 tab 现已改成“换一批”，会调用 `/api/recommendations/reshuffle` 直接从 discovery pool 秒级换出一批新推荐
- `/api/recommendations` 的 `RecommendationOut` 携带 duration、互动、发布时间和 Bangumi `rating_score / rating_count / source_rank` 元信息。popup 对推荐和惊喜卡统一采用“真实值才显示”的规则；目录评分独立于 like/comment，精确时间优先、来源相对标签兜底。
- 登录态来源只保留语义明确的发布时间：B 站 DOM 日期作为 `published_label`，小红书状态对象、抖音 `create_time`、知乎内容创建时间和 Reddit `created_utc` 作为 `published_at`；字段缺失时不写属性，不用任务执行/DOM 观察/互动时间猜测，也不额外请求详情页。回传后由后端统一规范化并进入候选池。
- 推荐 tab 滚到底时会调用 `/api/recommendations/append` 继续往下续 10 条，不会把当前这一屏直接替换掉；首次渲染、切回推荐 tab 和追加完成后也会再检查一次底部距离，避免停在底部时没有新 scroll 事件导致续页卡住
- 收到后台 `refresh.pool_updated` 时，推荐 tab 只更新池子数量、最近补货数量、方向提示和底部可换提示；移动 Web 空态也会用同一 runtime status 重新计算“还有多少可换 / 多少素材在整理”。不会调用 `/api/recommendations` 替换当前列表，用户已续页出来的历史内容会保留到下一次主动“换一批”或页面重新初始化。首次初始化推荐列表后会再读一次 `/api/runtime-status`，避免 `/api/recommendations` 从候选池 bootstrap 后仍显示 bootstrap 前库存
- popup API 现在会统一规范化推荐项，追加出来的 `cover_url` 也会被收敛成可直接加载的 `https://` 地址；推荐点击 payload 会保留 `content_id / content_url / source_platform`，因此 YouTube 等跨源卡片打开后也会被后端记成对应来源，而不是落回 B 站 BV 号语义
- 推荐、惊喜推荐和消息内封面图会通过 `popup-helpers.buildImageProxyPath()` 生成 `/api/image-proxy?url=...`，再用 `popup-backend-config.getBackendOrigin()` 拼成当前后端绝对地址；图片加载失败时保留已有 wrapper fallback，不让卡片布局塌缩
- 内容库的收藏 / 稍后再看封面同样走当前后端图片代理；真实 403、网络错误或已缓存的失败图片都会从 DOM 移除并替换为可见 SVG 占位，插件不会保留浏览器破图图标，卡片打开按钮的可访问名称保持不变
- 保存页刷新失败时保留最后一次成功的列表，错误行提供「重试加载」；全部 saved read/write/status/sync/task 请求都有 Abort timeout，且同一 deadline 从后端地址解析开始，覆盖初次设备会话交换、401 强制换票、受保护请求与响应解析，认证 fetch 接收同一 AbortSignal。每次成功加载会按 `sync_task_id` 去重恢复非终态 task，task→item ownership 把关联行显示为「同步中」并从单项 / 批量候选排除；side panel 重新可见时立即恢复查询，pagehide 清理 tracker。批量同步与重试加载会先捕获列表级焦点，重渲染后优先回到同一列表动作；卡片动作消失时再依次落到相邻卡片动作、列表动作、页面标题。「全部稍后看」按结果下标保留失败项，采用服务端 URL fallback `item_key` 更新状态，并把自动同步 task 纳入同一 ownership。coarse pointer 下推荐 / delight 保存按钮至少 44×44，sync 文案切换预留固定宽度。
- `/api/recommendations/refresh` 仍保留为后台补货入口，用于继续往候选池里持续进货
- popup 推荐卡片现在不会再把空 `expression / topic_label` 补成固定占位文案；后端预生成没完成时，这两块会直接隐藏
- popup 的收藏 / 稍后再看 toggle 统一走 `createSavedToggleRegistry()`：同一 bvid 可以被多个按钮注册，任一按钮增删成功后所有可见按钮同步 `aria-pressed` / title / 文本；旧的懒加载 `GET /api/watch-later/{bvid}` / `GET /api/favorites/{bvid}` 结果如果发生在用户点击或收藏列表加载 / 移除之后会被忽略，避免状态回跳。收藏列表中移除条目也会反向同步惊喜横幅里的收藏按钮，推荐卡稍后再看也会与惊喜横幅稍后再看同步。注册表会在每次状态同步时剪除已脱离 DOM（`isConnected === false`）的按钮，并在推荐列表 / 惊喜横幅 `replaceChildren` 后调用 `pruneDetached()`，避免按钮随重渲染在注册表里无限堆积。
- 亮色 side panel 视觉系统：顶部 hero + inline 状态徽标、胶囊 tab、统一卡片体系，整体更贴近 B 站内容产品气质
- 推荐 tab：展示内容封面、标题、作者 / UP 主、`topic_label`、朋友式推荐文案，并通过“打开内容”跳转到 `content_url`；缺少 URL 时按 `source_platform` 构造安全 fallback，Bangumi Subject 固定使用 `bgm.tv/subject/<id>`
- 如果某条内容暂时没有可用封面，卡片会回退到占位态，不影响换片和反馈
- 推荐封面不再依赖原生 `loading="lazy"`，避免内部滚动容器续页时新卡片封面偶发空白
- 底部提示区已升级为更明显的状态横条，会按成功 / 提示 / 错误切换对比度和状态点，减少“反馈发出去了但看不见”的感觉
- 修复卡片误跳转：`喜欢` / `不喜欢` / `写一句` / 输入框 / 发送按钮不再冒泡触发视频打开
- `喜欢` / `不喜欢` / `写一句` 都会调用 `/api/feedback`；桌面 Web 推荐卡片还提供「忽略」按钮（`feedback_type=dismiss`），走软移除语义：候选 `pool_status` 标 `feedbacked` 后不会再次进入发现池，但不会下调话题或作者权重。
- 上述 recommendation feedback、推荐点击和保存页内容反馈都会携带 durable pending request/event ID；响应丢失时复用，只有服务端确认 accepted/成功才清理。API 对缺失、空白或超过 400 字符的 ID 返回 422 且零写入，前端不得在 retry 时临时换一个新 ID 绕过。
- 推荐卡片里的 `写一句 -> 发出去` 现在会在按钮本地显示 `发送中... / 已发出 / 可重试` 三态，卡片底部也会同步写明这句是否真的发出去了
- 页面会读取 `/api/runtime-status`，区分“未初始化 / 正在补货 / 推荐可用”三种状态；初始化刚完成但 `initialized` 标记尚未同步时，如果已有补货中或候选池信号，不再误提示用户重新执行 init
- 桌面 Web 运行时看板的账号同步异常提示使用主题前景色与状态边界，深色主题下不再出现低对比度、难以辨认的错误文案
- 桌面 Web 惊喜推荐的知乎、Reddit 等文字卡使用主题表面色和主题前景色，classic / 深色主题不再把文字压在相近色渐变上；普通无封面文字卡也复用同一套可读性规则
- popup 打开期间现在会建立 `/api/runtime-stream` websocket 连接，底部提示条和池子状态会跟着后端事件实时变化
- popup 底部提示区已升级成可展开动态卡：默认两行显示“现在在忙什么 / 最近一次关键变化”，点 `更多` 可以展开最近历史
- 新增 `/api/activity-feed` 聚合接口，popup 会把认知更新、反馈记下了、换一批和补货结果收成同一块动态面板
- “换一批 / 继续追加”现在优先直接消费 discovery pool 里预生成好的 `expression / topic_label`；换批只有在后端返回非空新批次时才替换当前卡片，空批次会保留正在看的推荐、停止本轮自动续页并复读 runtime 库存，避免“明明有库存却被清成空页”
- 如果某条候选的预生成文案还没补好，卡片会先只展示标题、封面和 UP 信息，不会再显示统一占位话题或默认推荐理由
- 后台补货继续异步进行，不会阻塞 popup 立刻换片
- pool 状态摘要现在会区分“正在补货”“这轮找到了内容但可换库存没变”“刚补进 N 条”，不再把 refresh 进行中和上一轮净新增为 0 混成同一句
- 插件 side panel、移动 Web 和桌面 Web 统一把 `pool_available_count` 当作真实可换数量；只要 `pool_pending_count>0`，摘要都会在真实可换数之外显示“另有 N 条素材 / 素材已抓到，会按可换库存缺口整理”，不会把待评估 / 待分类 / 待文案 / 不可打开的素材数写成“可换”。`pool_pending_eval_count` 和 `pool_evaluated_pending_count` 只作为诊断与整理状态使用。插件首次 `/runtime-status` 失败后若先收到权威 `pool_status` stream 事件，会立即把库存状态提升为 initialized 并显示事件中的真实计数，不再把非零库存隐藏成未知/零。
- 推荐 tab 头部现已进一步压缩成双层内容型入口：第一层只保留 `For You`、标题和 `换一批`，第二层把池子状态收成三枚紧凑 chips，让第一张推荐卡更早进入首屏
- 推荐 tab 现在还会在头部下方展示独立的“惊喜推荐”首屏卡位：popup 启动时会主动读取 `/api/delight/pending`，runtime stream 收到新的 `delight.candidate` 也会立刻刷新这张卡
- 推荐 tab 会展示候选池摘要：
  - `当前可换`
  - `补货进展`
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
- 聊天 tab：调用 `/api/chat/turns` 创建 durable turn，后端先写入 `pending`，再后台生成回复；side panel reload 后会按 `session=popup` 读取完整 durable 对话流，再由共享 renderer 选出 `chat/hypothesis/confusion`，不能限定 `scope=chat`，否则确认卡会被隐藏
- 聊天输入框内置多场景 placeholder 轮播，提示用户可以描述自己怎么看内容、喜欢 / 讨厌什么、近期观看行为、自我状态或注意力变化；输入框 focus 时暂停轮播，blur 且内容为空时恢复。底部「最近发生的事」活动栏在聊天 tab 继续可见；聊天历史区域使用 flex 填满活动栏与输入框之间的剩余空间并独立滚动，输入框固定在聊天区底部，窄屏下仍保留可用的历史消息区域。历史记录会在 hydrate、追加新消息、替换 thinking 占位和切回聊天 tab 时自动滚到最新 turn，避免用户打开已有对话后还要手动拖到底部
- 惊喜推荐和兴趣猜测卡片内的 `聊一聊` 也会用 `scope=delight/probe` 写入 durable turn，回复完成后同步刷新对应卡片状态、画像摘要和最近动态；旧的 `/api/chat` 仍保留给兼容入口
- durable chat turn 写入 SQLite `chat_turns`，不再依赖 DOM、JS 内存或 `sessionStorage` 保留主聊天历史；惊喜推荐保留 `localStorage` UI 草稿、展开态和 per-delight `turns` 作为本地兜底，权威回复状态以后端为准
- 推荐、画像和聊天文案共享后端的 `ToneProfile`，基础风格是“老B友”，但会根据画像和近期反馈在信息密度、温度和梗感上动态调整
- 推荐、内容库、画像、对话四个一级 tab 已统一为同一套浅色卡片语言；内容库内的稍后再看、收藏、历史记录三个子 tab 按需加载并保留各自滚动位置，历史按 30 天三分类 cursor 分页

### 构建链路

- 运行时脚本不再直接把 `tsc` 的 ESM 产物交给 Chrome
- `scripts/build.mjs` 使用 `esbuild` 将各 content entry 和 `service-worker.ts` bundle 为可直接加载的单文件
- `tsc --emitDeclarationOnly` 继续负责类型声明产物
- Chrome 的 `npm run build` 只清理 / 重建 `dist/`，Firefox 的 `npm run build:firefox` 只清理 / 重建 `dist-firefox/`；Firefox 仅执行 `typecheck` 而不再把声明文件写入 Chrome 输出，因此按任意顺序连续构建都不会删除或污染另一目标的现有产物。显式 `npm run clean` 仍会同时清理两者
- 每个 target 的 bundle 完成后都会运行 manifest 资产预检，逐项确认后台脚本、content scripts 与 `web_accessible_resources` 文件真实存在；`dy-fetch-tap.js` 等动态注入资源缺失时构建立刻失败，不再留到浏览器任务执行时才报错。也可用 `npm run verify:assets` / `npm run verify:assets:firefox` 单独复查
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
- Linux.do adapter、task-tab 隔离、五路 discovery / 三路 bootstrap executor、dispatcher 校验和 `_t` 布尔登录态同步
- manifest 图标资源存在性
- Chrome / Firefox 构建目录隔离，以及两个 manifest 的后台脚本、content scripts、WAR bundle 资产预检
- Firefox manifest 的 version 注入、`sidebar_action` 降级路径、AMO 数据收集类别声明、Firefox zip 打包清理、AMO unlisted XPI 签名，以及 listed workflow 的元数据 / 隐私政策 / reviewer source / channel 核验
- popup 设置页字段与 `/api/config` schema 的基础对齐
- popup API durable chat turn：`startChatTurn()`、`fetchChatTurn()`、`fetchChatTurns()` 会分别调用 `/api/chat/turns`、`/api/chat/turns/{turn_id}` 和列表接口
- `renderDurableChatTurn(turn)`：`completed` 渲染 `turn.reply`，`failed` 渲染安全 `turn.error`，字段缺失时才使用本地固定兜底文案
- popup 连接状态稳定性：`popup-connection-poller.js` 覆盖 HTTP / runtime-stream 三态投影、失败探活才离线、旧探活 revision guard、`/api/ping` 失败后持续重探测与恢复回调；`popup-stream.js` 另覆盖主动关闭不会误触发断线通知
- popup 聊天布局：历史 hydrate 与切回聊天 tab 都会触发滚到底部，避免 hidden view 恢复后停在旧消息
- `dist/` 运行时脚本可被 Chrome 直接加载

## Release 分发

普通用户下载入口是 GitHub Latest Release 的 `openbiliclaw-vX.Y.Z` 聚合页：该页会同时展示当前后端源码 tag、最新插件 zip 和可用桌面安装包。`extension-v*` 仍是插件自动化通道 tag，用于构建、商店提交和排查发布流水线，不再要求普通用户在 Releases 列表里手动筛选。

插件内部 release 通道：

- 发布 tag：`extension-vX.Y.Z`
- Release 资产：
  - Chrome / Edge / Brave / 其他 Chromium 浏览器：`openbiliclaw-extension-vX.Y.Z.zip`
  - Firefox 140+ 临时调试 / AMO 输入：`openbiliclaw-extension-vX.Y.Z-firefox.zip`
  - Firefox 140+ 正式安装：`openbiliclaw-extension-vX.Y.Z-firefox.xpi`（仅 AMO signing 启用且凭据可用时生成）
  - Safari 18+（macOS）：`openbiliclaw-extension-vX.Y.Z-safari.dmg`（配置 Apple 凭据时为 Developer ID 签名 + notarized；未配置时自动回退为 ad-hoc 未签名实验包，需用户开启「允许未签名扩展」；`SAFARI_SIGNING_ENABLED=false` 强制 ad-hoc）
- 用户下载入口：`openbiliclaw-v*` 聚合 Latest Release；维护者需要核对构建日志时再看对应 `extension-v*` release
- Chrome / Edge / Brave 打包脚本会先删除同名旧 zip，再重新压缩 `manifest.json`、`dist/`、`icons/`、`popup/`，避免重复打包带入残留文件
- `extension-v*` GitHub Actions release workflow 会同时运行 Chrome / Firefox / Safari 打包脚本；仅当 `FIREFOX_SIGNING_ENABLED` 未关闭且 `AMO_JWT_ISSUER` / `AMO_JWT_SECRET` 可用时，才执行 `npm run sign:firefox:only` 生成 signed XPI。发布尾部调用 `.github/scripts/sync-aggregate-release.sh`，把实际存在的插件 zip / xpi / Safari dmg 同步到当前 `openbiliclaw-v*` 聚合 Latest Release，并把该聚合页重新标记为 GitHub Latest。Firefox 140+ 也可本地构建 / 临时加载：`npm run build:firefox` 生成 `dist-firefox/`，`npm run package:firefox` 生成未签名 `openbiliclaw-extension-vX.Y.Z-firefox.zip`；配置 AMO 凭据后，`npm run sign:firefox:only` 会把当前 `dist-firefox/` 提交 AMO unlisted 签名并输出可直接安装的 `openbiliclaw-extension-vX.Y.Z-firefox.xpi`。Safari CI 在 Apple 凭据齐全时自动走 `npm run package:safari -- --notarize`，否则自动回退 `npm run package:safari` 产 ad-hoc DMG；本地同样可用 `npm run package:safari` 打包；完整流程见 [safari-extension-build.md](../safari-extension-build.md)
- AMO 公开商店使用独立的手动 workflow `.github/workflows/publish-firefox-amo.yml`，不复用 `extension-v*` 的 unlisted 签名步骤。它从当前 commit 构建 Firefox 包，用 `git archive` 附带 `extension/`、共享 Web 模块、lockfile、构建说明和隐私政策，再执行 `web-ext sign --channel=listed --amo-metadata=... --upload-source-code=...`；提交后查询 authenticated versions API，只有目标版本真实显示为 `listed` 才成功。AMO 的 `eula_policy` API 对当前 developer JWT 实测返回无正文 HTTP 406，因此该字段改为提审后 best-effort 同步并发出显式 warning，不会阻断已经具备 manifest 数据类别、reviewer notes、listing 描述和随包 `docs/privacy.md` 的提审；若 Developer Hub 暴露该字段，维护者需手动回填。首次与后续公开提审都必须先准备未被任一 AMO channel 使用的新扩展版本。
- v0.3.62 起，Chrome / Firefox 发布包移除默认授予的 `http://*/*` 宽泛主机权限；当前固定权限覆盖 B站 / 小红书 / 抖音 / YouTube / X / 知乎 / Reddit / Linux.do 和 `127.0.0.1` / `localhost` 本机后端。`https://linux.do/*` 只用于同源只读任务、普通页行为 adapter 与 `_t` 布尔存在性判断，不上传 Cookie 值。局域网或远程后端通过 `optional_host_permissions` 在用户显式保存地址时请求对应 `scheme://host/*`，实际请求仍固定到配置端口。
- v0.3.64 起，Chrome / Firefox 发布包不再声明 `tabs` permission；后台任务仍可使用 `chrome.tabs.create/update/remove/onUpdated/sendMessage` 打开、导航和清理受支持平台任务页，发布包仅保留实际需要的最小 permission 集合。
- 插件更新不走后端自动更新 API：商店安装版本由 Chrome / Edge / Firefox 原生更新；GitHub Release 下载的 Chrome zip / Firefox signed XPI / Firefox 临时 zip / Safari dmg、开发者模式加载和临时加载用户按 release 页面下载新版并重新加载。

Chrome Web Store 上传自动化走官方 API v2，不使用第三方上传 action：

- 本地上传：`cd extension && npm run webstore:upload -- --zip openbiliclaw-extension-vX.Y.Z.zip`
- 本地上传并提交审核：`cd extension && npm run webstore:upload -- --zip openbiliclaw-extension-vX.Y.Z.zip --publish`
- GitHub Actions：手动运行 `Publish Chrome Web Store Package` workflow；默认只上传 zip，不提交审核，勾选 `publish` 才调用 Chrome Web Store `publish` API。若上一版仍在审核且必须用新版替换，可显式勾选 `replace_pending`；脚本仅在上传返回官方 `NOT_UPDATEABLE` 时调用 `cancelSubmission` 撤回旧审核并重试一次，默认关闭且不会吞掉其它上传错误。
- 需要在本地环境变量或 GitHub Secrets 设置：`CHROME_WEBSTORE_CLIENT_ID`、`CHROME_WEBSTORE_CLIENT_SECRET`、`CHROME_WEBSTORE_REFRESH_TOKEN`、`CHROME_WEBSTORE_PUBLISHER_ID`、`CHROME_WEBSTORE_EXTENSION_ID`。
- `CHROME_WEBSTORE_REFRESH_TOKEN` 必须由拥有该 Chrome Web Store item 管理权限的 Google 账号生成，OAuth scope 为 `https://www.googleapis.com/auth/chromewebstore`。
- Chrome Web Store 详情页文案与三张 1280×800 截图的上传顺序维护在 `docs/chrome-webstore-listing.md`。`scripts/build_chrome_webstore_demo_covers.py` 先确定性生成七条推荐 + 一个惊喜推荐使用的 8 张本地插画封面；`scripts/chrome_webstore_demo.py` 只通过固定假域名和本机 `/api/image-proxy` 返回这些素材，不读取真实配置 / 数据库；`scripts/capture_chrome_webstore_ui.py` 只允许 loopback 请求，等待封面解码后实拍桌面 Web、移动 Web 和 unpacked 插件 UI；`scripts/build_chrome_webstore_assets.py` 最终只生成 `01-seven-platform-recommendations.png`、`02-three-surfaces.png` 和 `03-truthful-status-local-data.png`。`tests/test_chrome_webstore_demo.py` / `tests/test_chrome_webstore_listing.py` 锁定封面主机、尺寸、文件顺序和 1280×800 成品合同。
- 商店文案 API bridge 使用独立 workflow `Update Chrome Web Store Listing` 和 `extension/scripts/chrome-webstore-metadata.mjs`。默认 `probe` 只读 v1.1 draft 并输出字段名、长度和 SHA-256；只有 response 明确暴露 `summary` / `description` 与 listing identity 时，显式 `apply + replace_pending + publish` 才会按「探测 → 状态 → 撤审（如需要）→ allowlist PUT → 精确 GET 回读 → v2 publish → PENDING_REVIEW 校验」执行。任何认证、schema 或回读失败都会在写入 / 提审前相应停止，日志不输出 token 或 draft 原文；该 workflow 不上传 ZIP。
- Chrome Web Store API v1.1 已弃用且只支持到 2026-10-15；其公开 `Item` resource 没有承诺详情页文案字段，因此 probe 不支持时必须回到 Developer Dashboard，不得猜测 Dashboard 私有 RPC。API v2 仍只负责包上传、状态、撤审、提审和 rollout；截图没有公开写 API，三张 PNG 仍需在 Dashboard 替换。
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
- popup 能根据 `/api/ping`（连接徽章活性，404 回退 `/api/health`）、`/api/health`（embedding / profile 就绪）与 `/api/recommendations` 切换在线、空状态与推荐列表展示；如果打开时后端尚未就绪，side panel 会离线短轮询 `/api/ping`，后端启动后自动恢复在线状态并刷新推荐
- side panel 页面反馈按钮已能经 `/api/feedback` 写回推荐表和事件层
- side panel 现已支持 `推荐 / 内容库 / 画像 / 对话` 四个一级 tab，内容库内含 `稍后再看 / 收藏 / 历史记录` 三个子项；历史与 PC Web、移动 Web 共用 30 天、三分类、cursor 分页及多 context 独立恢复语义
- side panel 聊天信号已进入后端学习链，但仍采用受控积累，不会因为单轮聊天立即重写画像
- side panel 聊天已支持 durable turn 恢复：主聊天、惊喜推荐内聊和兴趣猜测内聊在页面 reload 后会按 `turn_id` 从后端恢复 pending / completed / failed 状态
- side panel 推荐、画像和聊天回复现在共用“老B友”动态语气，不再固定成一套机械模板
- side panel 能根据 `/api/runtime-status` 切换“先初始化 / 正在补货 / 推荐可用”三态
- side panel 现在还能通过 websocket 看到“开始补候选 / 当前跑到哪个策略 / 刚补进几条新的 / 这批先换好了”这类实时运行状态
- service worker 现在会在高置信推荐出现时触发浏览器通知，并通过后端回写 `notification_sent`
- service worker 现在也会拉取认知变化通知；如果最近系统对用户形成了新的高置信理解，会发一条更克制的“阿B 又对你多看清了一点”提醒
- side panel 新版亮色布局已通过本地静态结构检查，四个一级视图与内容库三个子视图结构渲染正常
- 小红书 `bootstrap_profile` 任务已通过单元测试覆盖：dispatcher 识别任务类型并能跟随 profile URL 二次执行，executor 可从 mock `__INITIAL_STATE__` 的 saved / liked / history 分组提取 scoped notes，并能用 `partial` 批次在滚动任务中持续回传新增结果
- 抖音 `bootstrap_profile` 任务已通过扩展和后端回归覆盖：`RENDER_DATA` 只提供已显式登录的候选，`profile/self` MAIN-world bridge 对当前账号做最终权威确认（冲突时 profile 优先、未确认不缓存），API harvester 可分页拉取四个 scope 并报告后续页错误；dispatcher 的 partial 批次会在后端合并、去重并转成统一 memory 事件，身份 / 分页不完整时最终状态保持为 `degraded` 而不是伪装 `ok`
- 抖音 `search` / `hot` / `feed` 任务已通过扩展回归覆盖：dispatcher 三类 discovery 都从抖音首页启动；search 会通过首页搜索框提交并用 `search_navigation_ok` 校验是否进入真实搜索结果路由；content script 声明 search / hot 均支持 DOM interaction + passive fetch tap + active API bridge，feed 仍是 DOM interaction + passive fetch tap；fetch / XHR tap 可被动转发页面自身 search / related / feed 响应，并按目标 scope 过滤结果；`search-douyin -k 猫 --max-items-per-keyword 10 -w 180` 可用于 smoke `dy_search` 候选，`discover-douyin --source search --keyword 猫 --limit 5 --no-cache --no-evaluate` 可预览 `dy-plugin-search` 候选
- 知乎 `bootstrap_events` / `search` / `hot` / `feed` / `creator` / `related` 任务已通过扩展单测覆盖：executor 能解析浏览历史、收藏夹、个人动态点赞/收藏，并能把 search_v3、热榜、首页推荐、作者页、问题相关 mock 响应归一化为 `zhihu_*` 候选；`discover --source zhihu` 可验证正式 producer 流程，`discover-zhihu* -n 10 -w 240` 可用于分支级真实插件 smoke
- Linux.do `bootstrap_events` / `search` / `hot` / `feed` / `creator` / `related` 已通过 fixture、typecheck 与 Chrome/Firefox 构建；实际安装的 Chrome unpacked extension 已完成真实登录只读 E2E，Firefox 仍只声明构建和自动化验证完成
- V2EX 已接入后端匿名公开 discovery、可选 PAT 和扩展只读 bootstrap：`discover-v2ex*` 是只读 smoke，`discover --source v2ex` 使用正式 producer 和共享候选池；四个 bootstrap scope 的结果按 Topic 聚合回复，并在后端 identity gate 通过后写入账号分区事件和 Node affinity。executor 先校验目标 route 与 `#Main` 页面壳，错误路由不会被当成空 scope；dispatcher 只有在分页耗尽时才把 scope 标成 complete，并以渲染后的 `.page_current` 处理 V2EX“越界 URL 仍显示末页”的真实行为，避免重复末页直到上限。达到条目上限仍显式 `item_limit_reached`，达到 `max_pages_per_scope` 同样保持 partial。首次完整 guided 收藏 scope 会种下基线；后续连续第二次完整快照仍缺失才生成 retraction，重新收藏按新 generation 恢复。任务 lease、MV3 session 恢复、全局来源 mutex、idle / absolute deadline 和逐字节相同的结果重试都已落地；只有后端 2xx ACK 后才清理本地 pending payload。桌面设置页与 popup 可选择冲突身份，账号切换只在完整画像提交后激活，新旧账号事件 / Affinity / 快照互相隔离。2026-08-09 已用 `8420`、已安装开发扩展和真实登录态完成 4 / 19 / 1 / 0、24 条 canonical 事件的全 scope E2E

## 当前限制

- 行为按钮识别基于 DOM 文本、类名和 `aria-label`，不是服务端最终结果确认
- 采集范围优先覆盖首页、搜索页和视频页，未承诺所有 B 站模板完全一致
- side panel 主聊天和内联聊天回复已由后端 `chat_turns` 持久化；仍不提供完整聊天管理界面、删除能力或跨设备同步
- inline comment 采用轻量输入，不支持复杂反馈历史浏览
- side panel 视觉验证当前以静态快照 + extension 构建回归为主，仍建议结合真实后端做一次手动联调
- 六源账号周期回拉默认关闭；显式设置 `source_incremental_enabled=true` 后，还要求至少一个 background runtime-stream 连接及对应平台有效登录态。真实账号烟测必须在已安装扩展环境单独执行，不能用普通网页自动化冒充；Linux.do 已完成 Chrome 安装版真实登录只读 E2E，Firefox 同等安装版验收仍待执行
- 六源账号周期回拉默认关闭；显式设置 `source_incremental_enabled=true` 后，自动测试只覆盖 dispatcher/协议，真实账号烟测仍必须在已安装扩展环境单独执行，不能用普通网页自动化冒充
- V2EX 任务桥已接入 `*.v2ex.com` host permission、登录布尔心跳和四个只读 scope；当前仍没有 Cookie 值上报、私信读取或站内写操作。登出心跳会清除旧 observed identity；完整收藏 scope 会驱动后端双快照 retraction outbox，错误 route、条目 / 页数截断和失败 scope 不会推进缺失计数。已安装扩展 + 真实登录浏览器 E2E 已通过；自动化回归仍不能替代不同账号、隐藏主题和大收藏量账号的额外真机覆盖
- 浏览器通知当前只推送一条最高分未通知内容，不做通知中心或多条队列
- 惊喜推荐当前只维护一个首屏候选位，不做多条历史收件箱；`稍后看` / `收藏`
  已通过 `/api/saved/*` 长期持久化，只有 `忽略` 仍是当前候选队列的本地展示动作
- 认知变化通知当前只提示最重要的一条，不支持用户确认/反驳，也不会在插件里维护完整通知历史
- 聚合型认知卡片如果后端暂时拿不到可信标题，会保守显示为“基于最近几条相关内容”，不会伪造具体视频名
- “换一批”依赖 discovery pool 当前已有候选；如果候选池本身供给不足，仍可能提示“池子里这会儿还没刷出新的”，但已有推荐卡片会保留，不会被空响应清掉
- 自动续页同样依赖 discovery pool 当前已有候选；如果池子暂时不够，续页结果可能少于 10 条，甚至直接提示先等后台再补一点新的
- 池子摘要里的“最近在补”目前基于策略和候选标签做轻量聚合，属于方向提示，不是精确 taxonomy
- 小红书初始化导入是 best-effort：后端不登录、不爬取小红书，只等待插件在用户已登录浏览器里解析页面；收藏/点赞/浏览记录任一 scope 不暴露时，会跳过该 scope。普通推荐流不会被标成 `xhs_history`；受控滚动只在任务显式设置 `max_scroll_rounds` 时启用
