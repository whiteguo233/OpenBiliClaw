# Instagram 来源

> Instagram 接入采用“匿名公开发现 + 浏览器登录态初始化”的 capability-specific 模型。来源默认关闭；Cookie、请求头和原始响应都留在浏览器内，后端只接收经过字段白名单归一化的媒体、账号和布尔登录心跳。

首版会把第一次成功初始化的数字账号绑定为画像身份。浏览器切换到另一 Instagram 账号时任务会以 `instagram_account_switch_not_supported` 失败；当前没有“替换账号”入口，因为现有 Soul/偏好层无法按单个平台账号安全反演旧信号。不要手工清绑定后混入第二个账号；需要切号应使用独立数据目录重新初始化。

## 能力边界

| 能力 | 当前行为 |
| --- | --- |
| 内容发现 | `topic` 读取直接 `/popular/<topic>/` 或可用的 hashtag-recent 公共分页；`creator` 读取明确用户名的公开时间线 |
| 任意关键词搜索 | 不支持。公开 top-search 主要返回账号/标签，登录私有 SERP 可能写 Recent Searches，首版不把它伪装成匿名内容搜索 |
| 初始化画像 | 可选导入本人近期点赞、收藏和关注；分别映射为 `like`、`favorite`、`follow` |
| 身份 | 每个个人任务都必须从同源 `accounts/current_user` 响应取得当前数字账号 ID；Cookie 存在只表示 readiness |
| 增量 | 首版为 `init-only`，不周期性打开 Instagram，也不声称账号全历史完整 |
| 行为采集 | 不监听普通浏览，不把 Feed/Explore 曝光当 view 或正反馈 |
| 平台写入 | 不点赞、不收藏、不关注、不评论、不发消息；OpenBiliClaw 收藏仍是 local-only |

官方 Instagram API 当前只覆盖 Business/Creator 账号，无法取得普通账号的 liked/saved/following 对象清单。因此个人初始化只能在用户已经登录的 Instagram 页面内执行只读任务。Instagram Terms 与 Meta Automated Data Collection Terms 仍要求相应授权；本适配器默认关闭，部署者必须自行确认使用权限。安全隔离降低凭据风险，不构成 Meta 授权。

## 数据流

```text
Profile terms / explicit creator seed
                 │
                 ▼
      instagram_tasks (discover)
                 │  atomic claim + claim_token
                 ▼
  background Instagram task tab
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

任务领取遵循 durable browser-task 协议：扩展先完成 MV3 恢复和跨来源 mutex 准入，再领取任务；任务 ID、tab、游标、accepted rows、idle/absolute deadline 与待确认结果写入 `chrome.storage.session`。最终 POST 的同一 JSON 字节会重试到后端返回 2xx；后端以 claim token、staged canonical payload 和 first-final-wins 保证重放幂等。producer 重启恢复会扫完全部 owner ledger，再依据去重后的候选上限决定交付；后面的 unfinished/failed owner 会阻止创建新 suffix，未在本轮实际交付的 terminal rows 不会被误标 consumed。

个人任务的 canonical stage 还保存服务端 heartbeat evidence time。登录状态投影使用同一 SQLite 事务做 compare-and-set：崩溃发生在 stage 与投影之间时，重放可以补齐 verdict；若浏览器在 stage 后已观察到更新的登录/退出心跳，旧任务不会反向覆盖它。

## GitHub 调研与 clean-room 边界

接入前固定 commit 调研了社区实现，只使用可观察的 endpoint、envelope、cursor 和错误行为，自行实现解析器和任务协议：

- [`subzeroid/instagrapi@2902a3bc`](https://github.com/subzeroid/instagrapi/tree/2902a3bc9a5b822d0c7ed6d6f734231dd9a5258b)（MIT）：交叉核对 `feed/liked/`、`feed/saved/posts/`、`friendships/<id>/following/`、`next_max_id`，以及 challenge/rate-limit 分类。没有引入移动设备模拟、密码登录、自动 2FA 或 challenge resolver。
- [`instaloader/instaloader@54346929`](https://github.com/instaloader/instaloader/tree/543469296ec61e49c4e1d34edac39f043b16a5f1)（MIT）：交叉核对 creator/hashtag 的 `edges + page_info` 分页和 session 失效风险；没有硬编码其旧 query hash/doc ID。
- [`mikf/gallery-dl@86047cf6`](https://github.com/mikf/gallery-dl/tree/86047cf67a12bdb6ff1085774f8ad9fc347e8da9)（GPL-2.0-only）：仅用于协议事实交叉验证，代码未复制、改写或作为依赖引入。
- [`yt-dlp/yt-dlp@5d6b8c8c`](https://github.com/yt-dlp/yt-dlp/tree/5d6b8c8cd19785c3086ae3a9ec618c45e25eb3bc)（Unlicense）：只参考公开 post/reel 的登录墙与假空分类；其 profile extractor 标记为不可用，未作为正式发现路径。

调研同时确认：Instagram 的 Web top-search 不能证明存在匿名任意 keyword-media API；登录私有 `fbsearch/top_serp` 也不是本接入的公开路径。首版只承诺 `topic` 与 `creator`，不声称全局热门、完整 hashtag 或全站搜索。

## 初始化事件

三个 scope 独立分页、独立计数、独立完整性：

| Scope | 同源只读路径 | 事件 | 完整性规则 |
| --- | --- | --- | --- |
| `instagram_liked` | `GET /api/v1/feed/liked/` | `like` | 首请求不发送空 `max_id`；官方 UI 只承诺最近 300，达到 cap 永远不是全历史完整 |
| `instagram_saved` | `GET /api/v1/feed/saved/posts/` | `favorite` | 只有合法 envelope 且 `more_available=false` / cursor 缺失才结束 |
| `instagram_following` | `GET /api/v1/friendships/<current_id>/following/` | `follow` | 数字账号 ID 来自同一任务内的 current-user 响应；默认低 cap，避免大列表触发 challenge |

媒体以数字 `pk/id` 为稳定身份；`code` 与真实内容类型一起保存 canonical `/p/` 或 `/reel/` URL。关注账号使用独立 user namespace，不能伪装成内容。`taken_at` 是唯一 publication-time 来源；缺失时保持未知，不用任务时间代替。

结果只有在观察到 2xx、MIME/JSON 合法、route-specific shape 合法且 cursor 明确终止时才允许 `empty` 或 `scope_complete=true`。登录墙、checkpoint、`challenge_required`、`feedback_required`、200 HTML、401/403/429、schema 漂移、response tap 未观察到、cursor 重复、cap 或 deadline 都是 partial/failed；已经接收的 rows 保留，但不做缺失推断和 retraction。

## 公开发现

`topic` 任务直接打开一个公开 topic/hashtag URL，不在 Instagram Search 输入框中键入查询，因此不会主动写 Recent Searches。真实浏览器 spike 已观察到 `data.xig_logged_out_popular_search_media_info.edges` 与 `page_info.{has_next_page,end_cursor}`；该 envelope 是机会性 Web 契约，不是官方稳定 API。部署遇到结构漂移时必须 fail closed，而不是将空 HTML 或登录重定向当成零结果。

`creator` 任务只使用明确 username seed，读取公开 profile/timeline。它不猜测账号、不使用登录私有 related-account API，也不对每个 media 做无界 N+1 detail 请求。两种模式的 opaque cursor 原样保留；重复 cursor、`has_next_page=true` 却缺 cursor、非空 envelope 中全部节点解析失败，都进入 degraded/partial。

归一化字段包括：稳定 media ID、真实 canonical URL、`post/reel/carousel` 类型、caption/title、作者 ID/username、可用封面和权威 `taken_at`。由于所选响应分支不能一致提供同语义的互动指标，首版不映射 view/like/comment/share aggregate，也不以 0 冒充已知值。

## 鉴权、隐私与错误分类

- `discover` 是 anonymous capability；`profile/bootstrap` 是 login-required；`cookie-sync` 是 optional readiness。
- 扩展只检查 `sessionid` 是否存在并发送布尔心跳，不发送 value。`csrftoken`、`ds_user_id` 和 DOM 用户名都不作为已验证身份。
- 后端以 current account 数字 ID 派生不可逆 `sha256:` account key，并按 account + scope 分区事件；切号时拒绝混入旧账号。
- 401/403/login redirect → `login_required`；checkpoint/challenge/recaptcha/feedback → `challenge_required`；429/`rate_limit_error` → `rate_limited`；HTML/invalid JSON/schema drift 分开记录。
- 第一次 challenge/429 即停止该 lane，不自动解 challenge、不自动登录、不模拟移动设备、不发 home-feed seen telemetry。

## 配置与控制面

来源默认关闭。配置以实现中的 `SourcesConfig.instagram` 为准，至少包含 enabled、discover modes、分支日预算、每任务 item/page cap 与 producer cadence。候选 pool share 只在来源启用时参与有效份额。关闭状态会阻止后台 discover 生产和领取已有 discover backlog，但用户显式执行 `fetch-instagram` 或 guided init 后已经准入的 `bootstrap_events` 仍可由扩展领取，避免显式任务永久占用全局 bootstrap admission。

控制面复用平台中立 API，并增加以下任务/心跳端点：

| 端点 | 行为 |
| --- | --- |
| `GET /api/sources/instagram/next-task` | 扩展领取一个带 claim token、mode/scopes 与后端冻结 cap 的任务 |
| `POST /api/sources/instagram/task-result` | 接收 credential-free canonical result，stage 后投影候选或事件 |
| `POST /api/sources/instagram/kick` | 广播 `instagram_task_available`，唤醒已连接扩展 |
| `POST /api/sources/instagram/credential` | 只接受 `kind=login_state` 布尔值 |

`GET /api/sources/status` 分别展示 capability readiness：没有 heartbeat 时公开 discover 仍可排队，但个人 init 显示需要登录；heartbeat 只说明浏览器最近观察到 session cookie，不代表任务已成功解析账号。

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
