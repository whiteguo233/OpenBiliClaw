# OpenBiliClaw 隐私权政策

生效日期：2026-05-31
更新日期：2026-10-03

OpenBiliClaw 是一个本地优先的跨平台内容发现 AI Agent。浏览器插件的单一用途是：在用户访问 Bilibili、小红书、抖音、YouTube、X、知乎、Reddit、Linux.do 等受支持内容平台时，采集用户授权范围内的浏览、互动和内容信号，发送到用户自己配置的 OpenBiliClaw 后端，用于构建个人兴趣画像、改进内容推荐、同步收藏 / 稍后再看状态和展示本地通知。
OpenBiliClaw 是一个本地优先的跨平台内容发现 AI Agent。浏览器插件的单一用途是：在用户访问 Bilibili、小红书、抖音、YouTube、V2EX 等受支持内容平台时，采集用户授权范围内的浏览、互动和内容信号，发送到用户自己配置的 OpenBiliClaw 后端，用于构建个人兴趣画像、改进内容推荐、同步收藏 / 稍后再看状态和展示本地通知。

Instagram 是默认关闭的实验来源：普通 Instagram 页面不做被动行为采集；只有用户启用公开发现或显式选择初始化时，插件才在隔离任务 tab 中执行有界同源只读任务。安全隔离不代表 Meta 授权，部署者仍需自行确认自动采集权限。

直接 topic 页可以复用浏览器现有登录会话，被动解析页面自然返回的公开媒体；登录响应要求作者明确非私密，只上报规范化字段及 `authenticated_topic_observed` 布尔证据，不上传原始追踪字段。匿名路径保留；这不代表匿名路径始终可用，也不代表已审计页面自身的 Recent Searches 前后状态。

本政策说明 OpenBiliClaw 浏览器插件与本地后端如何处理数据。插件不会把数据发送到 OpenBiliClaw 开发者运营的服务器；Chrome Web Store / AMO 发布包默认只声明本机后端权限，数据流向通常是用户本机运行的 OpenBiliClaw 后端。

## 处理的数据类型

插件可能处理以下数据，具体取决于用户启用的平台、登录状态和后端配置：

| 类型 | 示例 | 用途 |
|------|------|------|
| 账号与身份相关信息 | 平台用户 ID、昵称、头像、Bilibili / 抖音等站点 cookie 或会话字段；Linux.do 只单独上报登录布尔，`/session/current.json` 确认的当前用户名只在任务 tab 内用于构造个人只读路径 | 让本地后端以用户已登录身份读取用户授权可见的数据，完成初始化画像、历史 / 收藏 / 点赞同步和内容任务 |
| 身份验证信息 | 受支持站点的登录 cookie、CSRF token 或页面内请求所需 token；Linux.do `_t` 只在浏览器内转换成布尔，其值不上传 | 仅用于用户配置的本地后端访问对应平台，不用于 OpenBiliClaw 开发者服务器 |
| 网络记录 | 受支持平台的页面 URL、页面标题、访问时间、视频 / 笔记 / 页面 ID | 识别用户正在浏览的内容，生成行为事件和推荐上下文 |
| 用户活动 | 点击、搜索、滚动、停留时长、观看时长、喜欢 / 不喜欢、收藏、稍后再看、关注等行为 | 构建和更新兴趣画像，改进推荐排序，过滤不感兴趣内容 |
| 网站内容 | 页面上可见的文字、封面图 URL、视频 / 笔记标题、作者、标签、描述、链接、统计信息等元数据 | 理解内容主题、生成候选池、去重和解释推荐理由 |
| V2EX 只读任务字段 | Topic / Node 的公开 ID、标题、URL、作者、Node、时间、回复摘录，以及布尔登录状态 | 执行用户主动触发的四个 bootstrap scope、聚合讨论事件和生成 Node 偏好；不用于站内写操作 |
| Instagram 只读任务字段 | 公开 media / user ID、canonical URL、caption、作者、封面、权威发布时间；初始化时的 liked / saved / following scope、数字 current-account ID 与 `sessionid` 存在布尔值 | 公开 topic / creator 发现和用户显式触发的近期点赞、收藏、关注初始化；不用于普通浏览追踪或站内写操作 |
| 个人通讯 | 用户在 OpenBiliClaw 插件侧边栏聊天框中主动输入的消息；以及用户在受支持平台上**成功提交**的评论正文与 B 站弹幕正文（提交成功后经网络层采集，仅送本机后端） | 与用户配置的本地后端聊天接口交互，帮助查看画像、推荐和设置；用户亲手写的评论 / 弹幕是最强的兴趣表达之一，用于更准确地构建兴趣画像 |
| 本地配置与 UI 状态 | 后端地址、已关闭提示、插件设置、缓存的后端配置、任务状态 | 保持插件连接、本地偏好和界面状态 |
| 用户主动创建的迁移包 | 文件配置中的模型 / 来源 API Key 与 token、平台 Cookie、本地 SQLite、画像 / 记忆、历史、图片缓存和白名单桌面偏好；不包含源机整段 API auth（密码 / hash、session secret、设备 key 等） | 仅在用户从本机桌面配置页明确选择导出时，生成供用户自行搬到另一台机器的 `.obcbackup` |

插件不以收集健康信息、财务和付款信息、精确位置数据为目的。如果用户正在访问的网页内容本身包含敏感信息，插件只会在受支持站点和用户启用功能范围内把它作为页面内容信号处理。

## 数据如何使用

OpenBiliClaw 使用上述数据来：

- 识别用户浏览的内容和平台来源。
- 生成、更新和展示用户兴趣画像。
- 生成个性化推荐、解释推荐理由，并根据反馈调整后续推荐。
- 同步收藏、稍后再看、点赞、关注、历史记录等用户主动启用或平台任务需要的数据。
- 让本地后端执行多平台初始化画像、内容发现、任务调度和通知。
- 保存插件设置、连接状态和用户明确关闭过的提示。

这些用途均围绕插件的单一用途：为用户自己的 OpenBiliClaw 后端提供内容理解、画像和推荐所需的数据。

## 数据发送到哪里

插件会把数据发送到用户配置的 OpenBiliClaw 后端地址。Chrome Web Store / AMO 发布包默认只声明本机后端权限，通常是：

- `http://127.0.0.1/...`
- `http://localhost/...`

如果用户在设置中明确配置局域网或自托管后端，插件会请求该 `scheme://host/*` 的可选权限，并使用默认关闭的设备密钥认证连接。WebExtension 权限模型无法跨浏览器限定端口，实际网络请求仍固定到用户配置的端口。

OpenBiliClaw 插件本身不会把数据发送到 OpenBiliClaw 开发者拥有或运营的远程服务器，也不会内置第三方分析、广告或遥测端点。

数据迁移请求只允许后端验证为本机 loopback 的调用；浏览器请求还必须具备同源意图并明确拒绝扩展 Origin，无 Origin 的本机 CLI / curl 调用仍可通过同一显式请求头契约。导出文件由本机后端返回给本机客户端，导入文件只上传到用户自己的本机后端；OpenBiliClaw 开发者不会接收迁移包。用户后续主动把文件复制到 U 盘、局域网、聊天工具或云盘时，相应传输与保留由用户和所选服务控制。

如果用户在后端中配置了云端大模型、embedding 服务或其他第三方 API，后端可能会把完成画像、总结、推荐解释或语义处理所需的内容发送给这些用户配置的服务提供方。这属于用户后端配置产生的数据流，不是插件直接调用远程代码或遥测服务。用户应同时查看所选服务提供方的隐私政策。

## 本地存储与保留

海外封面缓存未命中且项目网络策略实际选用代理时，后端通过同一代理向 Cloudflare 的固定 `https://1.1.1.1/dns-query` 查询公开 CDN 域名的 A/AAAA 地址，再校验为公网 IP 并固定连接，防止重绑定访问内网。Cloudflare 可见 CDN 域名、查询时间与代理出口地址；不会收到 Instagram 账号、Cookie、封面 URL 的路径/签名参数或画像。DNS 查询不跟随重定向，失败不绕过安全检查。国内 CDN 和 `network.mode=direct` 使用本机 DNS，不走此 DoH 服务；缓存命中不触发查询。这是封面安全传输依赖，不是统计或遥测。

插件会使用浏览器的本地扩展存储保存设置、连接状态、缓存配置和 UI 状态。OpenBiliClaw 后端会在用户本机或自托管环境中保存配置文件、SQLite 数据库、日志和画像文件。

Instagram 执行中的任务会在扩展 `storage.local` 临时保存有界 task / claim、任务 tab、deadline、分页进度、已归一化内容与待确认的 exact-result outbox，以便浏览器或扩展重启后恢复。后端确认或明确终态拒绝后清理；离线待重试时会继续保留，卸载扩展会清除这些扩展数据。此状态不含 Instagram Cookie、请求头、CSRF、原始 JSON/HTML 或挑战页正文；旧 `storage.session` 状态只迁移一次。

用户主动导出的 `.obcbackup` 是**未加密的敏感 ZIP 文件**，可能包含模型 / 来源 API Key、平台 Cookie、画像和浏览 / 推荐历史。包内 manifest 带成员大小与 SHA-256，只用于完整性校验，不提供保密性。导出会合并磁盘 `config.toml` / `config.local.toml`、移除整段 `[api.auth]`，再写成包内单份可移植配置；因此源机的登录密码 / hash、session secret、设备访问 key 及其它 auth 策略不会进入包。导出同时刻意排除日志、旧备份、embedding / 评测 / 临时缓存、证书、自启动文件、OpenBiliClaw Web / 扩展访问会话、外部 CLI 凭据和环境变量值；平台登录 Cookie 则属于明确包含的可移植敏感数据。manifest 的 `source_omitted_environment_variables` 只记录源机当时有值、会影响 OpenBiliClaw 的环境变量名称，包括 `OPENBILICLAW_*`、Gemini 标准 Key 变量及系统代理 / CA 变量；导入端另返回 `target_active_environment_variables`，提示目标环境当前仍可能覆盖导入文件。两个列表都不包含变量值。用户应只在可信设备间传递，并及时删除不再需要的副本。

导入后原配置和数据会按存在情况保留为本机 `pre-import-*.bak` 回滚副本，不会自动上传；下一次成功迁移会清理更早的同类迁移副本。目标机整段 `api.auth` 是导入基线，因此其门禁、密码、proxy 和 Origin 策略不会被源包覆盖；应用时会轮换文件中的会话签名密钥，并把 prepared DB 的会话撤销 epoch 设为来源与目标当前值最大值再加一、移除来源 password fingerprint，再清空、关闭扩展远程访问配对。这样即使 session secret 由目标环境固定，来源 / 目标旧 Web 会话与设备 key 也不会继续有效。导入暂存可在重启前取消，取消只删除私有 pending 副本，不改当前配置或用户数据。

用户可以通过以下方式控制或删除数据：

- 在浏览器中移除 OpenBiliClaw 插件，或清除插件站点 / 扩展数据。
- 停止或删除本地 OpenBiliClaw 后端的数据目录。
- 在插件设置中修改或清空后端地址。
- 在后端配置中关闭对应平台 source、调度任务、自动刷新或第三方模型服务。
- 退出对应内容平台账号或清除浏览器 cookie，阻止插件继续同步该平台登录态。

## 权限说明

插件请求的权限用于完成本地推荐和平台任务，不用于广告、售卖数据或无关追踪：

| 权限 | 用途 |
|------|------|
| `activeTab` | 在用户当前打开的受支持平台页面中识别内容并执行必要的页面动作 |
| `alarms` | 定时重试 cookie 同步、后端连接检查和任务轮询 |
| `cookies` | 读取用户已登录站点的必要 cookie，并按各来源最小化契约使用；Linux.do `_t` 与 Instagram `sessionid` 都只判断是否存在并上报布尔值，不同步 Cookie value |
| `notifications` | 显示来自本地后端的推荐、任务或状态通知 |
| `scripting` | 在受支持平台页面注入内容脚本，采集页面内容和行为信号 |
| `sidePanel` | 提供 OpenBiliClaw 侧边栏界面 |
| `storage` | 保存插件设置、本地 UI 状态和后端连接信息 |
| 主机权限 | 限定在 Bilibili、小红书、抖音、YouTube、X、知乎、Reddit、Linux.do、Bangumi 以及本机 OpenBiliClaw 后端之间处理必要数据 |
| 主机权限 | 限定在 Bilibili、小红书、抖音、YouTube、V2EX 以及本机 OpenBiliClaw 后端之间读写必要数据 |

`https://*.instagram.com/*` 主机权限只用于带任务标记的隔离 tab。公开任务读取有界 topic/hashtag 或明确 creator 页面；初始化先匹配同源账户表单用户名与新鲜 `PolarisViewer` 的用户名和双 ID，再以 GET 读取 saved/following，被动观察原生 Likes 页只读 Bloks 响应。扩展不重放 Bloks POST、不执行其表达式或 Select/Unlike 动作。插件只上报 `sessionid` 存在性；Cookie value、Authorization/CSRF、请求头、原始 JSON/HTML/Bloks、挑战正文和完整历史不会上传。MAIN 桥只发送有界白名单 rows、cursor 与终态证据。扩展不提交 Instagram Search，不执行 like/save/follow/comment/message，不自动处理安全验证；页面自身的上游状态变化仍需独立审计。普通 Instagram 页面不启动 behavior collector。

`http://127.0.0.1/*` 和 `http://localhost/*` 用于连接用户自己的本机 OpenBiliClaw 后端。内容采集脚本只声明在受支持内容平台上运行；发布包不声明 `http://*/*`、`https://*/*` 或 `<all_urls>` 这类所有网站权限。

抖音初始化任务需要识别当前登录账号，才能读取该账号公开可见的发布、收藏、点赞和关注分页。插件可从页面公开的 `#RENDER_DATA` 读取显式登录状态与候选 `sec_uid`，但不会把它直接当成最终身份；同页 MAIN world 会以 `credentials: "include"` 调用抖音同源只读 `/aweme/v1/web/user/profile/self/` 接口，只有该接口正面确认的公开 `sec_uid` 才用于分页与同 tab 缓存，冲突时也以它为准。常驻 fetch / XHR tap 不从页面被动请求 URL 提取或记录 `sec_user_id`；只有用户触发 bootstrap 后，页面消息桥才会传递经 `profile/self` 确认的公开 `sec_uid`、请求关联字段和为任务解析出的分页条目。桥不会传递 Cookie、CSRF token，也不会把未裁剪的原始接口响应对象直接转发。两侧 listener 的同窗口同源检查用于减少误接收，不把同页消息误称为可信授权边界。`sec_uid` 仅用于本次用户授权的抖音 bootstrap 分页，结果仍只发送给用户配置的 OpenBiliClaw 后端。

小红书 discover 搜索任务会在后台标签页运行。由于小红书在隐藏标签中可能不挂载搜索结果列表，插件的同页 MAIN world 桥会观察页面自身发出的搜索接口响应，只归一化最多 20 条与页面卡片等价的公开字段（笔记 ID / 链接、标题、作者、封面 URL、发布时间与互动计数）以及既有的内容访问 token，并把这些字段交给该标签页的 isolated task executor；不会修改请求、转发原始响应、读取 Cookie 值或采集搜索结果正文。归一化结果只用于完成用户本地后端下发的 discover 任务，发送目标仍是用户配置的 OpenBiliClaw 后端；DOM 卡片提取保留为接口结构变化时的兜底。

`https://linux.do/*` 主机权限有两个限定用途：在普通页面运行统一行为 adapter；以及在扩展自己创建、带任务标记的隔离 tab 内执行 Linux.do 同源只读任务。任务只允许 GET，支持公开 search / hot / feed / creator / related discovery，以及已登录账号的 bookmarks / likes / read history bootstrap；不会发帖、点赞、收藏、关注、编辑或执行任何其他站内状态变更。个人任务先通过 `GET /session/current.json` 正面确认当前账号，`_t` Cookie 只用于判断“是否可能已登录”，插件发送给后端的始终只是 `logged_in: true|false`。Cookie 值、其他 Cookie、CSRF 数据、原始 JSON/HTML、挑战页正文均不会上传；任务结果只包含归一化后的 topic ID、标题、URL、作者、分类、tags、发布时间、互动统计、scope 计数或结构化错误。为从 MV3 worker 回收或开发热重载中恢复，扩展会在 `chrome.storage.local` 临时保存无凭据的 task payload、runner tab ID 与绝对截止时间；其中可能包含搜索词或公开 topic / creator URL，但不含 Cookie、账号 token 或站点原始响应，并会在任务终态、超时或无效恢复时删除。普通行为采集与任务 executor 互斥：任务 tab 不产生普通浏览事件。

`*://*.bgm.tv/*` 和 `*://*.bangumi.tv/*` 的主机权限仅用于**账号身份识别**：读取页面公开的用户 uid（`CHOBITS_UID`）与导航栏用户名，实现零配置识别你的 Bangumi 账号，供画像初始化时读取你的公开收藏。在这两个站点上插件不读取 Cookie、不采集浏览行为、也不上传任何个人令牌；Bangumi 内容本身由本地后端通过官方匿名只读 API 获取。

`*://*.v2ex.com/*` 的主机权限用于 V2EX 普通页面的只读 Topic / Node 阅读事件，以及用户主动触发的 `bootstrap_profile` / 增量任务。任务页只读取渲染后的公开 DOM：`/member/<username>` 的本人主题、`/member/<username>/replies` 的本人回复、`/my/topics` 的收藏主题和 `/my/nodes` 的收藏 Node。插件会在本地检查 V2EX 的 A2 Cookie 是否存在，但只向用户配置的后端发送 `logged_in` 布尔值，不发送 Cookie 值；可见页面中的用户名作为 `observed` 身份证据单独上报，登出心跳会清除旧 observed username，浏览器证据最多保留 72 小时。V2EX 任务结果只包含经过限制的公开字段（Topic/Node 标识、标题、URL、作者、Node、时间和最多三条代表性回复摘录），不包含页面 HTML、请求头、CSRF/once、私信、密码或浏览器完整历史。扩展只有在确认 route、页面壳和 scope 完整翻页时才上报 complete；本地后端对收藏 Topic / Node 采用连续两次完整快照缺失确认，生成的取消收藏 / 取消关注只作为本地 `retraction` 画像事件，不会向 V2EX 发请求。PAT 验证身份只保存公开 username 与当前 PAT 的单向 fingerprint，最多信任 6 小时；匹配 PAT 被明确拒绝时清除该声明，网络失败不清除。身份冲突时暂停账号数据合并；PAT 原文仍只存在于用户选择的环境变量 / 配置凭据位置。OpenBiliClaw 对 V2EX 严格只读，不发帖、不回复、不感谢、不收藏、不取消收藏、不关注 Node。

## 远程代码

OpenBiliClaw 浏览器插件不使用远程代码。插件运行所需的 JavaScript 会打包在扩展程序包内，不会通过远程 `<script>`、远程模块、远程 Wasm 或 `eval()` 下载并执行第三方代码。

## 数据共享、出售与其他用途

OpenBiliClaw 开发者不会出售用户数据，也不会为了与插件单一用途无关的目的向第三方传输用户数据。OpenBiliClaw 不会为确定信用度、贷款、广告画像或跨站广告追踪而使用或转移用户数据。

用户自行配置的本地后端、第三方模型服务、代理服务或自托管环境由用户控制；这些服务的数据处理规则以用户自己的配置和相应服务提供方政策为准。

## 联系方式

如需报告隐私问题、请求更正说明或提交安全相关反馈，请通过 GitHub Issues 联系项目维护者：

<https://github.com/whiteguo233/OpenBiliClaw/issues>
