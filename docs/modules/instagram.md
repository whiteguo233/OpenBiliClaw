# Instagram 来源

2026-10-03 增量：普通“换一批”已接入 Instagram producer、显式小批评估与推荐文案，不必为恢复 discovery 重做画像；网络/限流/离线等失败给出安全提示。封面共享代理新增逐 hop 公网 IP 检查与地址固定。真实验证结果及仍需账号/许可的门禁以[最新台账](../platform-source-acceptance.instagram.md)为准，不以代码通过替代 full 验收。

> Instagram 接入采用“可选登录公开内容发现 + 浏览器登录态初始化”的 capability-specific 模型。来源默认关闭；Cookie、请求头和原始响应都留在浏览器内，后端只接收经过字段白名单归一化的媒体、账号和布尔登录心跳。

> 2026-10-01：在不关闭/重启 Clash Verge 的前提下，经用户批准切换现有代理节点，真实 HTTPS 已恢复。全新隔离库通过真实 popup 四阶段初始化：2 like + 2 favorite + 1 follow → 当前配置模型画像 → 10 条 Instagram 候选 → 4 条实际推荐，采集上限仍保留 partial 警告。安装态 ACK 重放及无封面卡矩阵已有独立证据；大样本、Firefox 登录等门禁以[验收台账](../platform-source-acceptance.instagram.md)为准，不宣称 full 通过。

首版会把第一次成功初始化的数字账号绑定为画像身份。浏览器切换到另一 Instagram 账号时任务会以 `instagram_account_switch_not_supported` 失败；当前没有“替换账号”入口，因为现有 Soul/偏好层无法按单个平台账号安全反演旧信号。不要手工清绑定后混入第二个账号；需要切号应使用独立数据目录重新初始化。

## 能力边界

| 能力 | 当前行为 |
| --- | --- |
| 内容发现 | `topic` 读取直接 `/popular/<topic>/` 或可用的 hashtag-recent 公共分页；`creator` 读取明确用户名的公开时间线 |
| 匿名 creator 公开证明 | 接受原生 `data.user` / `data.xig_user_by_username` 及白名单 ScheduledServerJS/Relay SSR；路径、用户名、数字作者匹配且明确非私密才放行，不信任 viewer/建议账号或只有DOM帖子链接 |
| 手动补库/恢复 | popup、desktop、mobile 共用 `POST /api/recommendations/refresh`，实际执行 Instagram 发现、评估、文案；保留账号/任务锁、每日预算和来源配额，scheduler 可关闭 |
| 任意关键词搜索 | 不支持。公开 top-search 主要返回账号/标签，登录私有 SERP 可能写 Recent Searches，首版不把它伪装成匿名内容搜索 |
| 初始化画像 | 可选导入本人近期点赞、收藏和关注；分别映射为 `like`、`favorite`、`follow` |
| 点赞无进展终止 | 连续 3 次非空观察没有新增即保留 partial/`progress_stalled`；新增会重置计数，不宣称完整历史或删除旧信号 |
| 初始化首轮推荐 | 仅选 Instagram 时走正式 topic/creator → 小批量 flush 评估 → 推荐表达；API/CLI 来源选择一致，当前 run 在领取前拥有发现任务，不依赖 scheduler 开启 |
| 身份 | 同源账户表单用户名匹配新鲜 SSR `PolarisViewer.data.username`，且外层 `id` 与 `data.id` 为同一非零数字串；Cookie 仅表示 readiness |
| 增量 | 首版为 `init-only`，不周期性打开 Instagram，也不声称账号全历史完整 |
| 行为采集 | 不监听普通浏览，不把 Feed/Explore 曝光当 view 或正反馈 |
| 平台写入 | 不点赞、不收藏、不关注、不评论、不发消息；OpenBiliClaw 收藏仍是 local-only |

官方 Instagram API 当前只覆盖 Business/Creator 账号，无法取得普通账号的 liked/saved/following 对象清单。因此个人初始化只能在用户已经登录的 Instagram 页面内执行只读任务。Instagram Terms 与 Meta Automated Data Collection Terms 仍要求相应授权；本适配器默认关闭，部署者必须自行确认使用权限。安全隔离降低凭据风险，不构成 Meta 授权。

## 数据流

```text
Profile terms → bounded topic aliases / explicit creator seed
                 │
                 ▼
      instagram_tasks (discover)
                 │  atomic claim + claim_token
                 ▼
  background Instagram task tab ↔ local claim / progress / ACK outbox
                 │  same-origin, bounded public reads
                 ▼
  normalized media + affirmative terminal evidence
                 │
                 ▼
 discovery_candidates → shared evaluator → content_cache

Guided init (explicit opt-in)
                 │
                 ▼
 instagram_tasks (bootstrap_events)
                 │
 current account ── liked / saved / following
                 │  normalized rows only
                 ▼
 staged result → account-scoped event ingress → Soul profile
```

任务领取遵循 durable browser-task 协议：扩展先完成恢复和跨来源 mutex 准入，再领取任务；任务 ID、tab、游标、accepted rows、idle/absolute deadline 与待确认结果写入 `chrome.storage.local`，收到 ACK 或明确终态拒绝后清理。旧版 session 记录会单向迁移；完整扩展/浏览器重启清空 session 不再遗失 claim/outbox。原 tab 丢失时以 `recovery_tab_gone` 结束同一 claim，有合法 accepted rows 时保留为 partial，不新建替代任务。关闭 tab 前再次确认 HTTPS Instagram host 与 task marker，避免 ID 重用或用户导航后关闭普通页面。最终 POST 的同一 JSON 字节会重试到后端返回 2xx 或明确终态拒绝；后端以 claim token、staged canonical payload 和 first-final-wins 保证重放幂等。producer 重启恢复会扫完全部 owner ledger，再依据去重后的候选上限决定交付；除明确的单页不可用外，后面的 unfinished/failed owner 会阻止创建新 suffix，未在本轮实际交付的 terminal rows 不会被误标 consumed。

个人任务的 canonical stage 还保存服务端 heartbeat evidence time。登录状态投影使用同一 SQLite 事务做 compare-and-set：崩溃发生在 stage 与投影之间时，重放可以补齐 verdict；若浏览器在 stage 后已观察到更新的登录/退出心跳，旧任务不会反向覆盖它。

同一 bootstrap claim 的第一个已验证数字账号在 dispatcher 内冻结，恢复后的 identity/progress/final 均须与之相同。发现不同账号时，以 `instagram_account_changed` 失败终止，不合并新旧 rows，不投影任一账号的混合快照；此错误与公开作者拒绝 `creator_not_public` 均由后端接纳为合法终态，释放 lease 后才能领取后续任务。

执行器不再只在任务开头解析一次身份：saved/following 每个可继续处理的私有页请求前后、原生 Likes 每次观察前后，以及未先命中终态风险时的正常结果提交前，都用新鲜 `PolarisViewer` 复核冻结账号。一旦在检查点观察到切号（包括 username 未变但数字 ID 已变），本任务整体返回 `failed` + 0 items，已持久的中间 rows 由 dispatcher 的账号冲突终态隔离，不作为 partial 保留。对可能早于 executor listener 到达的 Likes replay buffer，浏览器必须把它绑定到已加载任务页的 `PolarisViewer` 与新鲜初始身份；加载文档证据缺失、不可解析或不一致时，在接纳任何 progress 前以 0 items 拒绝。

新增复核不放宽风险停止和节奏约束：已缓存或当页响应中的 `rate_limited` / challenge / `login_required` 会在后续身份探针前终止，身份与私有数据 GET 共用任务冻结的 `request_interval_ms`，页数、条数与绝对 deadline 上限不变。这是检查点之间的**可观测账号连续性**防护，不是 Instagram 上游的原子会话快照；如果“切出再切回”完整发生在两个复核点之间，本路径无法证明或检出。

## GitHub 调研与 clean-room 边界

接入前固定 commit 调研了社区实现，只使用可观察的 endpoint、envelope、cursor 和错误行为，自行实现解析器和任务协议：

- [`subzeroid/instagrapi@2902a3bc`](https://github.com/subzeroid/instagrapi/tree/2902a3bc9a5b822d0c7ed6d6f734231dd9a5258b)（MIT）：交叉核对 `feed/liked/`、`feed/saved/posts/`、`friendships/<id>/following/`、`next_max_id`，以及 challenge/rate-limit 分类。没有引入移动设备模拟、密码登录、自动 2FA 或 challenge resolver。
- [`instaloader/instaloader@54346929`](https://github.com/instaloader/instaloader/tree/543469296ec61e49c4e1d34edac39f043b16a5f1)（MIT）：交叉核对 creator/hashtag 的 `edges + page_info` 分页和 session 失效风险；没有硬编码其旧 query hash/doc ID。
- [`mikf/gallery-dl@86047cf6`](https://github.com/mikf/gallery-dl/tree/86047cf67a12bdb6ff1085774f8ad9fc347e8da9)（GPL-2.0-only）：仅用于协议事实交叉验证，代码未复制、改写或作为依赖引入。
- [`yt-dlp/yt-dlp@5d6b8c8c`](https://github.com/yt-dlp/yt-dlp/tree/5d6b8c8cd19785c3086ae3a9ec618c45e25eb3bc)（Unlicense）：只参考公开 post/reel 的登录墙与假空分类；其 profile extractor 标记为不可用，未作为正式发现路径。

调研同时确认：Instagram 的 Web top-search 不能证明存在匿名任意 keyword-media API。本接入不主动构造或重放 `fbsearch/top_serp`；2026-09-30 授权后的增量只被动读取直接 topic 页自然返回的公开媒体。首版只承诺 `topic` 与 `creator`，不声称全局热门、完整 hashtag 或全站搜索。

## 初始化事件

图形化/API/CLI 初始化在完整画像落盘后，使用本次选择的 Instagram 正式 producer 生成首轮推荐，不再误走旧的 B 站固定策略。发现任务的新建与恢复都登记到当前 init run，保留其它后台任务的领取隔离；候选显式 flush 一批后同步生成文案。该手动路径不要求开启 scheduler，也不放宽准入评分。没有首轮可浏览内容仍明确报告部分完成。

2026-09-30 非空真站修复：原生 Likes 网格的 `media_id` 是 `媒体ID_作者ID`，通过共享 identity helper 提取媒体主键；末行全空占位格不算坏媒体，纯 visibility/template 引用不算被拒绝的 membership。未知引用仍阻止 affirmative-empty，表达式只解码数据、不执行。授权后真实 2 like + 2 favorite + 1 follow 已准确导入，两轮事件数 0→5→5；两类媒体主动 cap=2，因此保留 partial，不宣称完整历史分页通过。

### 合成链路验收（不等于真站验收）

`scripts/instagram_synthetic_e2e.py --root <隔离项目>` 可在已有隔离配置、尚无数据库的目录中，模拟浏览器任务的规范化回传。必须同时设置 `OPENBILICLAW_PROJECT_ROOT` 指向该目录、配置 `data_dir=<隔离项目>/data` 并关闭 scheduler；脚本拒绝复用已有数据库，不复制真实账号数据，不请求 Instagram。它使用正式 next-task/task-result API 与真实 ingress/memory，验证 2 like + 2 favorite + 1 follow、partial 保留、重复回调/新任务去重、账号不一致拒绝及 smoke-only 不写事件。合成 URL 不代表真实内容。

随后可在同一隔离 root 运行 `openbiliclaw rebuild-profile --source instagram --limit 20`，实际调用当前配置 LLM；这验证的是“合成事件→真实模型画像”。2026-09-30 已通过；新画像驱动正式 discovery 得到 4 条合成候选，但 evaluator 的全部 provider 限流，候选保留为 pending_eval，不能称为推荐闭环通过。真实账号非空回拉须另用新隔离 root，不能混用合成账号绑定。

三个 scope 独立分页、独立计数、独立完整性：

| Scope | 同源只读路径 | 事件 | 完整性规则 |
| --- | --- | --- | --- |
| `instagram_liked` | 原生 `/your_activity/interactions/likes/` 页 → `/async/wbloks/fetch/` 白名单响应 | `like` | 不重放/构造 Bloks POST、不执行表达式；最近最多 300；非空有界快照一律 partial，只有严格空状态可 complete |
| `instagram_saved` | `GET /api/v1/feed/saved/posts/` | `favorite` | 只有合法 envelope 且 `more_available=false` / cursor 缺失才结束 |
| `instagram_following` | `GET /api/v1/friendships/<current_id>/following/` | `follow` | 数字账号 ID 来自同一任务内账户表单与新鲜 viewer 的一致性验证；默认低 cap |

媒体以数字 `pk/id` 为稳定身份；`code` 与真实内容类型一起保存 canonical `/p/` 或 `/reel/` URL。关注账号使用独立 user namespace，不能伪装成内容。`taken_at` 是唯一 publication-time 来源；缺失时保持未知，不用任务时间代替。

结果只有在观察到 2xx、MIME/JSON 合法、route-specific shape 合法且 cursor 明确终止时才允许 `empty` 或 `scope_complete=true`。登录墙、checkpoint、`challenge_required`、`feedback_required`、200 HTML、401/403/429、schema 漂移、response tap 未观察到、cursor 重复、cap 或 deadline 都是 partial/failed；已经接收的 rows 保留，但不做缺失推断和 retraction。

saved/following 的非空页若全部 rows 解析失败，返回 `response_rows_rejected`，不冒充空集合；好坏混合时保留有效 rows 并返回 `response_schema_degraded`，取消该 scope 的完整性证明。空数组只有在 envelope 合法且明确末页时才可 empty。

## 公开发现

`topic` 任务直接打开一个公开 topic/hashtag URL，不在 Instagram Search 输入框中键入查询，因此不会主动写 Recent Searches。真实浏览器 spike 已观察到 `data.xig_logged_out_popular_search_media_info.edges` 与 `page_info.{has_next_page,end_cursor}`；该 envelope 是机会性 Web 契约，不是官方稳定 API。部署遇到结构漂移时必须 fail closed，而不是将空 HTML 或登录重定向当成零结果。

`creator` 任务只使用明确 username seed，读取公开 profile/timeline。它不猜测账号、不使用登录私有 related-account API，也不对每个 media 做无界 N+1 detail 请求。两种模式的 opaque cursor 原样保留；重复 cursor、`has_next_page=true` 却缺 cursor、非空 envelope 中全部节点解析失败，都进入 degraded/partial。

creator 媒体必须有作者 `is_private=false`，或与页面原生 `data.user` / `data.xig_user_by_username`（含白名单 ScheduledServerJS/Relay SSR）的公开 profile 证据同时匹配数字主键和 username。主键优先为正整数 `pk`，仅缺失时用 `id`；真实匿名 Relay 的 `pk` 与 `id` 可以不同，不能把跨命名空间比较误当账号冲突。无效 `pk`、作者不匹配、viewer/推荐账号、未知或私密状态都不能提供证明。profile 证据晚于 SSR 时只重扫既有页面 JSON，不缓存原始网络响应、不额外构造请求；新的无效/未知/私密 profile 或超限扫描撤销旧缓存并返回 `creator_not_public`。公开作者页自然请求的 429/401/403 同样桥接为限流/登录失败，即便已收到首屏也须停止滚动、保留 partial 而非继续请求。

**2026-09-26 修复**：公开 popular 路由不是任意兴趣词搜索。已把已知中英文兴趣别名解析为当日匿名实测有媒体的宽主题种子 `animation / music / technology / gaming / art`，画像 fallback 在映射后去重；未知词仍只是有界候选 URL，不保证可用，也不获得准入加分。浏览器同时匹配专用错误页标题和损坏链接说明，将 HTTP 200 软 404 记为 `public_page_unavailable`，绝不记为 empty。producer 从 failed task 的 canonical result 读取具体错误，跳过这个单页失败并继续剩余种子；已取得内容和可运行的 creator 分支保留。关键词按实际 empty/不可用/retained 分别结算。登录墙、challenge、429、schema/未知错误仍停止后续请求；仅条数/页数上限和有界公开快照允许 partial 继续。

**可选登录 topic（2026-09-30）**：仅在直接 popular/hashtag 任务路径读取 `xdt_fbsearch__top_serp_graphql` 的 `XDTTopSerpMediaGridUnit.items`，要求 `user.is_private=false`；已知 header/account 单元不作为媒体，未知单元与隐私状态缺失 fail closed。结果以 `authenticated_topic_observed` 布尔字段保留来源证据，不冒充匿名结果。最多解析 80 项，截断时撤销末页证据；上游错误保留已取得内容为 partial。正式发现与有界非空点赞分别已有真实证据，但不据此证明大样本分页。详见[验收台账](../platform-source-acceptance.instagram.md)。

归一化字段包括：稳定 media ID、真实 canonical URL、`post/reel/carousel` 类型、caption/title、作者 ID/username、可用封面和权威 `taken_at`。由于所选响应分支不能一致提供同语义的互动指标，首版不映射 view/like/comment/share aggregate，也不以 0 冒充已知值。

## 鉴权、隐私与错误分类

身份解析调用 `accounts/edit/web_form_data/`，随后有界读取首页 SSR（4 MiB / 20 秒）；只解码 `data-sjs` 的 JSON named `PolarisViewer`，不执行脚本。匿名、冲突和缺失 viewer 均拒绝。MAIN response tap 同时接受 `/api/graphql` 与真实网页使用的 `/graphql/query`，仅解析白名单 media 集合；登录 topic 集合还要求路由和公开作者状态匹配，不接纳其它搜索页面集合。

Likes tap 只接受当前 Likes 任务页上 `liked_media_screen / liked_refresh / liked_next` 三个原生 appid。`instagram-liked-bloks.ts` 是有界、数据专用 AST 解码器，只读取 array/map/primitive 常量中的 media 字段，不运行动作；原始表达式不跨桥。空状态必须是已观察到的确切 Text/TextSpan，未知树和拒绝节点不是 empty。当前没有可靠的非空末页判据，所以非空最多保留 cap/page-cap partial，不承诺完整历史。429/401/403 与显式验证通过白名单错误桥中止余下 scope。

- `discover` 是 optional-credential capability，未登录仍可尝试匿名路径；`profile/bootstrap` 是 login-required；`cookie-sync` 是 optional readiness。
- 自动心跳区分明确禁用/缺少站点权限与后端配置暂不可用：后者不读取 Cookie，按 1 分钟 alarm 重试；恢复启用后只上传布尔登录提示，成功后恢复每小时刷新。明确禁用或权限缺失保持小时级检查，不持续快速重试。
- 扩展只检查 `sessionid` 是否存在并发送布尔心跳，不发送 value。`csrftoken`、`ds_user_id` 和 DOM 用户名都不作为已验证身份。
- 后端以 current account 数字 ID 派生不可逆 `sha256:` account key，并按 account + scope 分区事件；切号时拒绝混入旧账号。
- 401/403/login redirect → `login_required`；checkpoint/challenge/recaptcha/feedback → `challenge_required`；429/`rate_limit_error` → `rate_limited`；HTML/invalid JSON/schema drift 分开记录。
- API HTML 分类先剔除 script/style/template/noscript/comment，再区分表单 action 与可见诊断文本；静态资源名、meta 和普通标签属性不作为登录/验证证据。最终响应 URL 的真实 login/challenge/checkpoint 路由仍优先停止任务。JSON 只检查字段值，`checkpoint_url: null` 的字段名不构成 challenge。未知 HTML 始终是 `html_response`，不是成功或空列表。
- 第一次 challenge/429 即停止该 lane，不自动解 challenge、不自动登录、不模拟移动设备、不发 home-feed seen telemetry。

## 配置与控制面

封面由 desktop/mobile/popup 的共享 `/api/image-proxy` 路径提供，Instagram CDN 已在 allow-list；contract 明确声明 `media.image="proxy"`。共享传输逐跳检查所有解析地址为公网并固定数字目标 IP，保留 Host、TLS SNI 与证书校验；海外代理通过固定 Cloudflare DoH 获取受校验的公网答案。103 项受控网络安全/API 回归和真实 CDN 下载、popup 图片加载分别验证边界逻辑与可用性；它们不等于对公网进行攻击测试。详见台账及 runtime/隐私文档。

来源默认关闭。配置以实现中的 `SourcesConfig.instagram` 为准，至少包含 enabled、discover modes、分支日预算、每任务 item/page cap 与 producer cadence。候选 pool share 只在来源启用时参与有效份额。关闭状态会阻止后台 discover 生产和领取已有 discover backlog，但用户显式执行 `fetch-instagram` 或 guided init 后已经准入的 `bootstrap_events` 仍可由扩展领取，避免显式任务永久占用全局 bootstrap admission。

桌面 Web 的「设置 → 平台源」提供 Instagram 来源卡，与扩展设置使用同一份既有配置：启用开关、topic / creator 分支与日预算、请求间隔、调度间隔、初始化每类上限和候选池占比均可保存、热应用并回读；账号状态复用共享 capability 投影，不新增 Cookie 输入框。桌面保存配置不能代替浏览器授予扩展站点权限：首次使用仍需在扩展设置中启用并保存 Instagram、允许 `instagram.com`，导入个人事件还需在同一浏览器登录。creator 种子来自 topic 的作者，后端会为 creator 同时启用 topic。

两类日预算均以最终保留的候选条数为单位，配置加载时的低预算警告不应解释成任务次数。`min_interval_minutes` 使用持久尝试时间控制跨进程冷却；正常 `throttled` 跳过不会推进该时间或消耗候选预算。初始化后的个人事件刷新仍是 `init-only`，通用增量调度不会自动为 Instagram 入队。

扩展保存时立即显示「等待浏览器授权…」，30 秒未完成则显示明确的未保存提示、恢复按钮并保留脏字段；迟到的权限确认不会自动保存已放弃的表单。用户处理原生提示后可重试。该超时不代替授权，也不自动授予或撤销浏览器权限。

`discover --source instagram` 的 CLI 失败现在返回 exit 1，不再把 `response_envelope_unobserved`、HTML、登录、验证或限流等失败以 exit 0 交给自动化。成功、明确空结果与节流/池满/预算等正常跳过仍返回 0；无新增不等于失败，也不等于供给成功，需结合计数判断。回归从真实 CLI 经 producer 和持久队列注入脱敏浏览器终态，覆盖失败与明确空结果，不替代真站验收。

控制面复用平台中立 API，并增加以下任务/心跳端点：

| 端点 | 行为 |
| --- | --- |
| `GET /api/sources/instagram/next-task` | 扩展领取一个带 claim token、mode/scopes 与后端冻结 cap 的任务 |
| `POST /api/sources/instagram/task-result` | 接收 credential-free canonical result，stage 后投影候选或事件 |
| `POST /api/sources/instagram/kick` | 广播 `instagram_task_available`，唤醒已连接扩展 |
| `POST /api/sources/instagram/credential` | 只接受 `kind=login_state` 布尔值 |

`GET /api/sources/status` 分别展示 capability readiness：没有 heartbeat 时公开 discover 仍可排队，但个人 init 显示需要登录；heartbeat 只说明浏览器最近观察到 session cookie，不代表任务已成功解析账号。

### 页面资源连接故障的排查边界

Instagram 页面和静态资源由浏览器通过系统/浏览器代理请求，项目的海外网络模式不会替浏览器切换出口。登录心跳、首页 HTTP 200 或画像模型请求成功，都不能证明 `static.cdninstagram.com` 的 JavaScript/CSS 已加载；真实观察过的失败形态是 document=200、正文为空、脚本 `ERR_CONNECTION_CLOSED`，最终 discovery 返回 `response_envelope_unobserved` 而不是合法空结果。

诊断时应对失败页面实际引用的公共静态资源做有界请求，保留状态码、下载字节数与默认 TLS 验证结果；不保存 Cookie、headers 或完整含参数 URL。可用已有节点的独立探测区分当前出口与浏览器问题；只改项目配置、降低评分或延长等待不能修复上游 TLS 连接关闭。更换共享代理分组会影响其它海外流量，须明确确认范围，不关闭 Clash、不降低 TLS 校验、不自动改系统代理/订阅/规则。候选节点探测成功仍需真实页面与正式发现复验，不能代替端到端恢复。

## 关键文件与验证

- `docs/platform-source-contract.instagram.toml` — 冻结能力、鉴权、任务和排除契约
- `src/openbiliclaw/sources/instagram.py` — credential-free normalizer
- `src/openbiliclaw/sources/instagram_tasks.py` — durable queue、结果 staging、事件映射
- `src/openbiliclaw/runtime/instagram_producer.py` — 正式 discover producer 与候选交付
- `extension/src/background/instagram-task-dispatcher.ts` — mutex、claim、恢复、ACK outbox
- `extension/src/content/instagram/task-executor.ts` — 同源 read-only executor
- `extension/src/main/instagram-response-tap.ts` — MAIN-world 有界 response tap（若构建启用）
- `tests/test_instagram_*.py` 与 `extension/tests/instagram-*.test.ts` — contract、queue、producer、extension safety

真实登录态、真实 challenge、Chrome/Firefox 已安装构建和 Meta 使用许可均不能由静态测试证明；其状态在 [`platform-source-acceptance.instagram.md`](../platform-source-acceptance.instagram.md) 中单独记录，未执行项保持 `NOT_RUN`。
