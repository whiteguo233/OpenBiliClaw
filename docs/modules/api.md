# 后端 API

## 概述

`src/openbiliclaw/api/` 暴露本地 FastAPI 契约，并把 UI 请求编排到 durable storage、Soul、Dialogue 与 runtime。本文记录配置、迁移、推荐和对话等公开端点；通用鉴权见 [api-auth.md](api-auth.md)，初始化端点见 [init.md](init.md)。

## 初始化期间的配置探测

`POST /api/config/probe-service` 只在内存副本上应用设置页草稿并真实探测 LLM、默认链、embedding 或网络策略，不写 `config.toml`、不热重载 runtime。它因此不受 guided init 的 HTTP 写端 409 门控；初始化运行时仍可测试，LLM 请求继续经过进程级稳定 total gate。LLM 实例 / 链探测的 outer deadline 按草稿 `[llm].timeout` 取值并夹在 10–120 秒，超时以 `ok=false` 和稳定错误文案返回；图形客户端使用 125 秒预算，覆盖本地模型冷启动而不允许无界挂起。`PUT /api/config` 仍在初始化期间返回 `409 init_running`，避免替换本轮任务正在使用的组件。

视觉预热配置也属于同一事务契约：`PUT /api/config` 对 `keyframe_max_frames (1..12)`、
`keyframe_fetch_limit (1..200)`、`danmaku_fetch_limit (1..200)` 和
`danmaku_max_chars (100..2000)` 做范围校验，保存后由 RuntimeContext 透传到推荐引擎；配置文件
与 API round-trip 保持这些数值。配置字段仍默认关闭视觉 / 弹幕功能，不会改变默认排序。

Discovery 配置响应与更新白名单同时公开 `keyword_digest_grace_hours`，默认 `24`、合法范围
`0..168`。`PUT /api/config` 拒绝布尔值、非整数和越界值；合法值进入同一次 TOML 持久化与
runtime apply。`0` 是只关闭跨 digest 关键词复用的回滚值，不会关闭统一 planner 或删除历史行。

Discovery 配置模型也公开 `eval_scorer="llm|shadow|learned"`。`GET /api/config` 返回当前模式；`PUT /api/config` 会小写规范化并只接受这三个值，非法值返回 422 且不落盘。`shadow` 与 `learned` 的运行时安全语义、审计和只读 gate 见 [内容发现引擎](discovery.md)；API 不会因 gate 报告自动改配置。

账号增量配置中的 `scheduler.source_incremental_enabled` 默认返回 `false`。旧配置没有该字段时
也按关闭处理；每个来源还公开 `sources.<slug>.incremental_enabled`（默认 `false`）。只有通过
`PUT /api/config` 或 TOML 显式把总开关设为 `true`，且对应来源开关也为 `true`，runtime 才会按
`source_incremental_hours` 和逐源覆盖自动创建扩展账号任务。关闭态在 presence 检查前返回，
不会打开或切换平台标签页；关闭某来源时其 scheduler-owned 待执行任务会被标记失败，手动任务不受影响。
`scheduler.douyin_incremental_hours` 仍额外默认 `0`，省略或发送
`null` 都保持抖音关闭。总开关、来源开关与周期字段都不控制手动初始化、手动 `fetch-*` 或正常 discovery。

## 配置保存与后台应用

`PUT /api/config` 把“持久化成功”和“运行时已经切换”分成两个明确阶段。请求仍在 `_CONFIG_SAVE_LOCK` 内完成校验、`config.toml.bak` 快照、`config.toml` 写入和凭据存储，然后统一立即返回 `202 apply_state="queued"`、`apply_revision` 与已脱敏配置快照；运行时 lane 由 app-owned latest-wins 队列在后台安全应用，前端通过 `GET /api/config/apply-status` 或 runtime event 观察终态，不把 202 当作失败。

`GET /api/config` 和 `PUT /api/config` 同时公开 B 站发布日期偏好：`recommendation_date_preset`、
`recommendation_date_start`、`recommendation_date_end`、`recommendation_date_weight`。PUT 会先把
合并后的完整值交给同一策略校验器；非法日期、preset 或权重返回 HTTP 400，配置对象、磁盘文件和运行时
均保持不变。合法保存完成后，RuntimeContext 重建 `PoolCurator`，新偏好在下一次推荐请求中生效。

`general.data_dir` 不支持热切换。若保存值的 canonical 路径与当前 runtime 已打开、已持有进程级锁的 active data dir 不同，磁盘配置仍记录新值，但 202 返回 `restart_required=true`；后台只把其它字段应用到继续绑定旧 active data dir 的 `RuntimeContext`。同一次请求涉及的抖音 / X 外部凭据也读写旧 active 目录，避免在尚未持有新目录锁时写入。完整退出并重新启动、取得新 canonical data-dir lock 后才启用新路径；因此 apply status 的 `applied` 不代表目录已切换。

Phase 2 cognition rollout 在配置 API 中也是 task-scoped：`soul` GET/PUT 模型公开
`preference_prompt_view`、`awareness_prompt_view`、`insight_prompt_view` 三个
`legacy|compact-v1` 字段，默认分别为 `legacy / compact-v1 / legacy`。旧的聚合
`cognition_prompt_view` 不在响应模型或更新白名单中。热重载后 Awareness 字段只影响
`soul.awareness_confusions`；普通 `soul.awareness` 固定使用 `legacy`，其余两个值各自只影响
对应 analyzer。

后台配置应用队列为 app-owned、latest-wins：正在应用的修订不会被取消，尚未开始的多个修订会合并为最新一份；因为每次 PATCH 都基于最新已落盘配置构建，合并不会丢掉前一轮已保存字段。成功广播 `config_reloaded`；失败且没有更新修订等待时恢复最后一次已生效配置并广播 `config_reload_failed`，若已有更新修订则不回滚覆盖它，直接继续应用最新值。进程在排队期间退出也不会丢配置，下一次启动直接从已落盘 `config.toml` 构建运行时。

| 方法与路径 | 状态 | 契约 |
|---|---|---|
| `PUT /api/config` | ✅ | 持久化成功后统一返回 `202 queued`；响应新增 `apply_state`、`apply_revision`，原有 `reloaded` / `rollback_applied` / `restart_required` 保持兼容。`discovery.eval_scorer` 接受 `llm / shadow / learned` 并进入同一次持久化与热重载。改变 canonical `data_dir` 时 `restart_required=true`，新路径仅在完整重启后启用。 |
| `GET /api/config/apply-status` | ✅ | 返回 `state`、最新请求修订、最后已应用修订、消息、非敏感错误分类和更新时间；不包含配置内容或凭据。`applied` 只确认本进程可应用部分，不取消 PUT 已返回的目录重启要求。 |

guided init 不与待应用配置并行：队列为 `queued/applying` 时 `POST /api/init` 返回 `409 config_applying`；init 已开始时 `PUT /api/config` 仍返回既有 `409 init_running`。

## 本机数据迁移

桌面 Web 的「设置 → 通用 → 数据迁移」使用四条独立 API。它们在正常和 `llm_registry_unavailable` 降级态都保持可达，但不继承“已登录即可远程管理”的范围：每次请求必须由后端真实解析为 loopback transport（直接连接，或可信代理正确报告的 loopback client）并显式携带 `X-OBC-Auth: 1`；浏览器请求还必须通过 Origin / Host 同源检查，扩展 Origin 明确拒绝，无 Origin 的本机 CLI / curl 仍可使用。迁移判定独立于 `[api.auth].trust_loopback`，远端 Bearer / Cookie、局域网客户端，以及由 Caddy / TLS Proxy / 容器转发过来的非 loopback 客户端都不能借密码门禁绕过。guided init 活动期间导出 / 导入返回 `409 init_active`；状态查询与取消暂存继续可用，便于对账或撤销此前已经暂存的导入。

| 方法与路径 | 请求 | 成功响应与副作用 |
|---|---|---|
| `POST /api/migration/export` | JSON body 可选；桌面端发送 `{"frontend": {...}}`，后端只接受主题模式、色相、强调风格、自动续页和侧抽屉开关 | `200 application/vnd.openbiliclaw.backup+zip`，下载 `openbiliclaw-backup-YYYYMMDD-HHMMSS.obcbackup`。响应使用 `Cache-Control: no-store, private`；临时导出目录在文件传输结束后删除。配置保存锁忙时返回 `409 config_busy`。后端读取并合并磁盘上的 `config.toml` / `config.local.toml`（不烘焙环境变量），移除整段 `[api.auth]` 后只写包内 `config/config.toml`，不携带密码、password hash、session secret 或扩展设备 key；数据文件始终从当前进程已持锁的 active data dir 导出，尚待重启的新 `data_dir` 不会被提前读取。 |
| `POST /api/migration/import` | 原始 `.obcbackup` bytes，不是 multipart；必须带 `X-OBC-Migration-Confirm: replace-all`。可选 `X-OBC-Migration-Request-ID: <UUID>`；未提供时服务端生成 UUID，非法值返回 `400 invalid_request_id`。压缩包上限 2 GB，服务端流式写临时文件 | `202 state="staged"`，返回迁移 ID、规范化的 `request_id`、源版本、文件数、解压大小、安全 UI 偏好、被目标机设置覆盖的字段、`source_omitted_environment_variables`、导入当时的 `target_active_environment_variables` 和 `restart_required=true`。此时当前配置、SQLite 与 runtime 均未替换。 |
| `GET /api/migration/status` | 无 body | 返回 `idle`、`processing`、`staged`、`applied`、`failed` 或 `cancelled`。上传 / 校验仍在进行时，`processing` 携带规范化 `request_id`、`phase="uploading|validating"` 与 `restart_required=false`；`staged` 携带 `migration_id`、`request_id`、两类环境变量名、调整字段与 `restart_required=true`；`applied` 携带 `migration_id` 和白名单 `frontend`。响应不会暴露包内凭据。 |
| `DELETE /api/migration/pending` | 无 body | 删除尚未应用的 pending stage，返回 `cancelled=true, state="cancelled"`；没有 pending 时是 `cancelled=false, state="idle"` 的幂等空操作。它不修改当前配置或用户数据；若迁移已进入启动期 apply journal 则返回 `409`。 |

导出响应是**未加密且包含敏感信息**的 ZIP 容器；API 不宣称也不实现服务端加密。模型 / 来源凭据和平台 Cookie 会迁移，但源机器的整段 `[api.auth]` 不进入包。启动应用时会重新读取目标机最新磁盘配置，以当时整段 `api.auth` 为基线，再轮换文件 session secret、关闭扩展远程访问并清空设备 key；prepared DB 同时把 `auth_epoch` 设为来源与目标当前 epoch 最大值再加一，严格高于两者并撤销两台机器的旧 Web 会话，即使目标 session secret 由环境变量固定也不会延续。因此源包不会覆盖目标机密码门禁策略，旧会话或扩展配对也不会继续有效。

导入完整校验 manifest、成员类型 / 路径 / 大小、SHA-256、配置和 SQLite 后，才把内容发布到项目根下的私有暂存区。`request_id` 是上传结果的关联 / 对账 ID，不是服务端自动去重键；收到不确定结果时应先 `GET /api/migration/status`，不要盲目重复上传。匹配同一 `request_id` 的 `processing` 表示后端仍在上传或校验，不是失败；断连后的单次瞬时 `idle` 也不能单独作为本次请求的终局。桌面端最多强制查询 3 次，遇到 `idle/cancelled` 会间隔 500ms 再确认，匹配 request ID 的 `processing/staged` 则立即收口；每次打开「通用」还会绕过本地已加载标记重新查询。

配置、SQLite、画像、白名单 UI 偏好和其它数据都要等下一次 `openbiliclaw start`、`openbiliclaw serve-api` 或桌面包启动取得 migration runtime lock 并成功 apply 后才生效；status 的 staged / applied 响应都只可能携带白名单 `frontend`，桌面端会忽略 staged 值。`state="applied"` 后，每个浏览器会把 `migration_id` 记为本地一次性交接回执，只应用该迁移的偏好一次；之后用户修改主题或滚动设置，即使旧 applied status 仍持久存在也不会再次覆盖。详见[存储层的可移植数据迁移](storage.md#可移植数据迁移)。再次提交合法迁移包会替换尚未应用的暂存包，也可在重启前调用 `DELETE /api/migration/pending` 取消。
## V2EX 配置与来源状态

`GET /api/config` 的 `sources.v2ex` 返回启用状态、公开用户名、PAT 是否已配置、五个
discovery 分支、Node/Tab 过滤和预算；`access_token` 永远不回传明文。`PUT /api/config` 支持
保存这些字段，非空 PAT 会先以只读 `GET /api/v2/member` 校验，环境变量
`OPENBILICLAW_V2EX_TOKEN`（或 `token_env` 指定的变量）优先于配置文件。

`GET /api/sources/status` 通过统一 source-auth provider 返回 V2EX 的匿名 / PAT 状态；无 PAT
是 `auth_required=false` 的 `no_auth`，有 PAT 时可为 `unverified`、`verified`、`failed` 或
`rate_limited`。浏览器登录态是独立的布尔心跳和 observed identity，不会被 PAT 状态替代。
V2EX 任务桥提供 `POST /api/sources/v2ex/login-state`、`GET|POST /identity`、
`GET /next-task`、`POST /task-result` 和 `POST /kick`；任务结果先合并为 canonical staged
result，再经过后端身份门禁转换统一事件和账号分区 Node affinity。PAT verified、浏览器 observed、
配置 / accepted 证据不一致时账号投影暂停但公开 discovery 不停。端点不接收或保存 Cookie 值、
页面 HTML、私信或 CSRF 字段。

## V2EX 浏览器任务桥

| 方法与路径 | 状态 | 契约 |
|---|---|---|
| `POST /api/sources/v2ex/login-state` | ✅ | 只接收 `logged_in` 布尔心跳；扩展本地检查登录 Cookie，但不上传 Cookie 值 |
| `POST /api/sources/v2ex/identity` | ✅ | 默认接收页面观察到的公开用户名（`observed`）；只有显式 `accept=true` 才保存用户接受的身份（`accepted`），不会伪造 `verified` |
| `GET /api/sources/v2ex/identity` | ✅ | 本地只读解析 PAT / 浏览器 / 配置 / accepted 身份证据，返回 `resolved / identity_mismatch / unknown`、账号 bootstrap 门禁与私有 scope 可用性；不出网、不返回 token |
| `GET /api/sources/v2ex/next-task` | ✅ | 从 `v2ex_tasks` 原子领取 `bootstrap_profile`，支持四个只读 scope |
| `POST /api/sources/v2ex/task-result` | ✅ | 冻结首份 `ok / partial / empty` canonical 结果；服务端净化 DOM 字段、聚合 Reply、执行 identity gate，并只对 `scope_complete=true` 的收藏集合推进双快照 / durable retraction outbox；事件、账号 affinity 和 effect ack 完成后才终结任务 |
| `POST /api/sources/v2ex/kick` | ✅ | 请求来源任务调度；仍受来源开关、扩展在线状态和全局 bootstrap 串行准入约束 |

四个 scope 是 `public_topics`、`public_replies`、`favorite_topics` 和 `favorite_nodes`。首次完整 guided 收藏 scope 会种下账号基线；之后第一次完整快照缺失只增加 missing streak，连续第二次完整快照仍缺失才生成 `retraction(favorite|follow)`；错误 route、条目 / 页数截断、登录 / 网络 / 解析失败和身份冲突都不会推进。PAT verified identity 最多信任 6 小时，浏览器 observed identity 最多信任 72 小时；明确 PAT 拒绝与浏览器登出分别清理匹配证据。桌面设置页与 popup 会读取 `GET /identity` 展示冲突 claims，并允许用户显式接受当前浏览器账号；账号切换任务只暂存新账号证据，guided init 的 Soul Profile 提交成功后才激活该账号，旧账号事件 / Node Affinity / 收藏快照不会混入。

普通 `POST /api/events` 中，V2EX Topic 的 `content_page_exit` 只有可见阅读时间达到 30 秒、canonical HTTPS Topic URL / Topic ID / Node slug 全部一致且当前 observed 浏览器账号不与 active profile 冲突时，才按 distinct Topic 幂等增加账号分区的 `engaged_view_count`。事件先获得 durable receipt，投影失败时请求失败并由同一 `event_id` 重试修复，不会重复计数。

## 公开项目统计

| 方法与路径 | 状态 | 契约 |
|---|---|---|
| `GET /api/project-stats` | ✅ | 桌面 Web 与扩展读取 GitHub Star 数量的公开同源端点。后端通过海外网络策略请求 GitHub，持久化 12 小时缓存并使用 ETag 条件请求；遇到 403 / 429 时遵循 `Retry-After` / `X-RateLimit-Reset` 有界退避。GitHub 失败不会透传为 HTTP 错误：有缓存返回 `source="cache", stale=true`，无缓存返回 `source="unavailable", stale=true` 且省略 `github_stars`，两者均为 200。该端点不包含用户数据，在密码门禁和降级模式下保持公开。 |

## 惊喜推荐消费契约

| 方法与路径 | 状态 | 契约 |
|---|---|---|
| `POST /api/delight/respond` | ✅ | `response="dismiss"` 是三端“× / 看过了，不再推荐”的永久消费动作：服务端按 `bvid` 解析 `content_cache` 中的 canonical `source_platform/content_id`，先写 `seen_items`，再置 `delight_notified=1`；后续普通推荐与惊喜推荐均硬排除。`view` 只置惊喜已读，`dislike` 另记录负偏好，`like/chat` 继续保留当前候选。 |
| `POST /api/delight/sent` | ✅ | 仅确认主动通知已送达并维护推送冷却，不代表用户已看，不写 `seen_items`；UI 叉号不得把它作为消费路径。 |

## 推荐反馈端点

### 推荐输出与 dislike 的即时一致性

| 方法与路径 | 状态 | 契约 |
|---|---|---|
| `GET /api/recommendations` | ✅ | 只读未处理历史；1 秒 snapshot 只有在 TTL 与 effective dislike digest 都未变化时复用。加载期间 dislike 变化会按新快照重读，再在 franchise cap 前过滤。 |
| `POST /api/recommendations/reshuffle` / `append` | ✅ | serve 使用带 flat-preference overlay 的画像，完成后在 HTTP 序列化前再读一次最新 effective dislikes，关闭请求进行中的偏好竞态。 |
| `GET /api/notifications/pending` | ✅ | 单条候选在返回前按最新 dislike 复核；模糊命中时不使用多卡窗口的“全灭恢复”保护。 |
| profile edit / `POST /api/feedback` | ✅ | durable edit 或单卡反馈 projection 完成后立即失效 recommendation snapshot；单卡反馈仍由 `exclude_processed` 同步隐藏。 |

这些边界不阻止 discovery 搜索，也不等待异步语义清池或完整 Soul rebuild。

### 30 天内容历史（issue #112）

| 方法与路径 | 状态 | 契约 |
|---|---|---|
| `GET /api/content-history?category=clicked\|shown\|removed&limit=12&cursor={opaque}` | ✅ | 分页返回最近 30 天的「主动点开过 / 出现过但没点开 / 最近移除」。每类按 canonical `item_key` 去重，并以 `occurred_at DESC, source_kind DESC, source_id DESC, item_key ASC` 形成全序。首屏省略 `cursor` 和 `offset`；响应通过 `next_cursor` / `has_more` 驱动续页。`limit` 为 1–50；旧客户端仍可在不传 cursor 时单独使用非负 `offset`，越过 SQLite signed integer 范围会在参数校验阶段返回 422。端点只读本地数据、在 LLM 降级态仍可用；仍受现有 API auth middleware 保护。 |

cursor 是版本化、base64url 编码的 opaque token，调用方不得解析或拼装。服务端严格校验其完整 shape、长度和类型，并绑定请求 `category`、固定 30 天 retention、上一页完整排序位置以及首屏看到的 events / recommendations / removal 三个最大行 ID；坏格式、跨 category 重放以及 cursor 与任何显式 offset 并用都返回 422。续页因此不会因首屏后以新行追加的头部事实（event、removal 或新 recommendation）发生 OFFSET 式重复/跳项；`total` 仍统计锚定集合中当前符合该 category 的全部卡片，不是 cursor 后剩余数，`has_more` 由 `limit + 1` 判断。该 token 不是跨请求 MVCC 快照：既有行的更新/删除、保存恢复状态和滚动 30 天边界仍按续页请求时的当前投影可见。

响应每项继续保留顶层 `context` / `restored` 兼容字段；`removed` 另返回 `contexts: [{context, occurred_at, restored}]`，同一内容可同时包含 `favorite`、`watch_later`、`dismiss`、`dislike` 的各自最新事实。收藏与稍后再看分别按对应 membership 计算 restored，互不遮挡；反馈 context 的 restored 固定为 false。`clicked` / `shown` 在 SQL 投影前复用平台 registry 规范 source alias 和 item-key 前缀（包括 `x/yt/xhs`），使 alias 与 canonical 事件折叠到同一身份，且 `shown` 能正确排除已点击卡片。

三套图形界面每组默认只请求 12 条，续页只回传上一页的 `next_cursor`。历史输出会把 `content_url` 与 `cover_url` 中的协议相对 URL `//...` 统一补成 `https://...`，并只返回无凭据、无空白/控制字符且结构有效的绝对 HTTP(S) URL；非法内容链接仅在平台和内容 ID 能安全构造 canonical 链接时回退，否则返回空串，非法封面直接返回空串。封面继续交给 `/api/image-proxy`，前端使用 lazy / low-priority 图片，不在打开历史页时预热整月封面。

### 公开事件写入口的幂等 ID

以下三个公开写入口都要求调用方显式提供稳定 ID；字段会先去除首尾空白，再校验非空且最长 400 字符：

| 入口 | 必填字段 | 重试规则 |
|---|---|---|
| `POST /api/events` | 批次中每个 event 的 `event_id` | 同一个具体动作的网络重试必须复用；两个外观相同但实际独立的动作必须使用不同 ID。 |
| `POST /api/feedback` | `request_id` | 同一 recommendation/type/note 重试复用；同 ID 改 payload 返回 409。 |
| `POST /api/recommendation-click` | `request_id` | 同一 concrete click 重试复用；稳定身份优先使用 recommendation/content ID，不把会轮换的签名 URL 或重渲染标题纳入 identity。 |

ID 字段是严格 JSON string，不接受数字、布尔或其它类型的自动转换。

字段缺失、空串、纯空白或去空白后超过 400 字符均由请求模型返回 HTTP 422；此时 route handler 尚未运行，不会写 `events`、`seen_items`、recommendation feedback 投影或其它数据库状态。服务端不会为这些 HTTP 入口补随机 ID，因为响应丢失后重新生成会把一次动作变成两次 durable fact。扩展、移动 Web 与桌面 Web 会把 pending ID 持久化到动作成功；顶层 `openbiliclaw feedback` 在省略 `--request-id` 时生成并打印一个 ID，跨命令重试必须复用该输出；OpenClaw CLI/skill 则把 `request_id` 设为必填。

### 事件来源字段

`POST /api/events` 的 `source_platform` 是兼容可选字段；新插件事件会发送规范平台名，内容 ID 继续从统一的 `content_id` / `bvid` / `note_id` / `tweet_id` / `question_id` 等稳定字段注册表承接。服务端统一按“显式来源 → metadata 来源 → 规范 URL → B 站兼容默认”解析，并将结果提升到事件顶层 `source_platform`，同时保留 metadata 镜像；`source_confidence` 分别为 `exact`、`inferred` 或 `legacy_unknown`。因此旧 payload 省略来源但带有 X / YouTube 等规范 URL 时不会误归 B 站；只有没有更强证据时才使用 `legacy_unknown`。来源统计的兼容读取优先使用事件顶层字段，只有旧行顶层为空时才回退 metadata。旧数据库在首次补列时做一次保守回填；没有足够证据的历史行保持未知，不会根据标题或任务名猜测。

`POST /api/feedback` 的成功边界是 **event-first 的两次 commit**，不是跨表原子事务：先由 `EventIngressService` 把带 `request_id` 幂等键的 `feedback` event 提交到 durable ledger，再单独调用 `update_recommendation_feedback()` 提交 recommendation 展示投影。若进程或数据库故障发生在 event commit → recommendation projection 之间，本次请求会失败；客户端用同一 `request_id` 重试时，event ingress 返回 duplicate receipt，API 校验 durable row 中的 recommendation/type/note 与请求一致后重新执行投影，从而修复间隙。相同 `request_id` 携带不同反馈返回 409，不能驱动投影。之后只唤醒 event scheduler 并立即返回；HTTP 不获取 pipeline lock，也不等待 LLM。

当 `scheduler.unified_interest_line=true`（默认）时，`events` 表是 durable ingress queue：app-owned `EventProcessingScheduler` 先由 generic `profile_events` consumer 领取显式归其所有的普通行为/推荐点击，再由 `content_feedback` consumer 领取 `like/dislike/comment/dismiss` 内容反馈；二者都以 event row ID 派生稳定 signal ID，通过 `checkpointed_enqueue_batch()` 把 buffer 与各自 cursor 原子发布到同一份 `pipeline_state.json`，随后 owner 调用 `tick_if_buffered()`。只有独立周期画像维护调用 `tick()`。首次 app startup 只同步发布 owner cutover fence 并 admission 一个由 scheduler 持有的 recovery task，lifespan 不 await event scan、buffer consume 或 LLM，因而 provider 401、慢响应或永不返回都不能阻止 HTTP listener/health 就绪；scheduler 在 shutdown 负责取消并 gather 该任务。配置热重载仍先 pause+drain，再同步 recover 遗留 event，最后恢复新 runtime 后台任务，保持旧 owner 到新 owner 的顺序屏障。两条生命周期都覆盖 HTTP commit→wake、event scan→checkpoint 或 checkpoint→consume 的崩溃窗口。旧名 `FeedbackBatchScheduler` 仅为兼容 alias。

`POST /api/events` 将 raw `dislike` 规范为 `feedback`。统一线下，显式内容反馈只唤醒上述 durable cursor owner，不再同时进入 generic `signals_from_events()` / profile backfill；hypothesis / import feedback 属于其它 owner，同样不进 generic 增量路径且只由 feedback cursor 越过；retraction 仍保留 generic pipeline 的折价与 tombstone 路径。`unified_interest_line=false` 时维持旧 feedback batch 与 generic event 行为。

## 来源任务结果的两阶段完成

`POST /api/sources/{xhs,dy,yt,zhihu,reddit,linuxdo}/task-result` 的最终回调不再先把任务写成 `completed`。后端先在 `BEGIN IMMEDIATE` 中合并并冻结第一份 canonical result（含 XHS `self_info` 私有快照），任务仍保持非终态；随后只从这份持久结果重放来源事件、seen-key 和来源专属投影，全部成功后才执行不替换 `result_json` 的 terminal flip。若进程分别退出在 canonical merge→event ingress、event ingress→seen-key 或 seen-key→terminal 三个窗口，后续 callback 会忽略变化后的 body，用第一份结果补齐缺口。队列把 staged marker 视为业务 mutation 的逻辑终态：并发/迟到的 partial、final、fail、rate-limit 都不能改写它；但它继续遵守各源 claim lease，丢失非 2xx 响应后由 lease reclaim 自动触发修复（Linux.do 长任务为约 35 分钟）。seen-key 通过 `update_source_bootstrap_state()` 原子、严格落盘并按源保留最新 5,000 个身份键，失败会阻止 terminal flip；事件稳定键不含 task ID，因此 ingress 已提交但 marker 未写时的重放只返回 duplicate receipt。Reddit post/comment/subreddit/user 使用各自稳定身份，comment URL fallback 只接受含 comment id 的完整 permalink，不能把 post id 或标题误作 comment key；Linux.do 使用正整数 topic ID，canonical `content_id="topic:<id>"`。

周期任务 payload 带 `incremental=true`；六源 handler 在 guided init 外给 durable event 标记 `profile_update_owner="generic"`，在 init-owned 回调中只落事实、由阶段 2/3 统一建模。事件 ingress 成功或 duplicate receipt 后才按响应顺序 checkpoint seen key，再翻 terminal；没有 handler 直接调用画像 pipeline。扩展离线时 runtime 不创建任务，也不推进调度时间。

### Linux.do 任务与登录态端点

| 方法与路径 | 状态 | 契约 |
|---|---|---|
| `GET /api/sources/linuxdo/next-task` | ✅ | 使用 authenticated extension 请求原子领取最早的 pending Linux.do 任务；无任务返回 bodyless 204。约 35 分钟未完成的 claim 才可重领，init active 时只暴露本轮拥有的 task ID。 |
| `POST /api/sources/linuxdo/task-result` | ✅ | 接受 `task_id/status/items/scope_counts/debug`；discovery 结果只供 waiting producer，明确带 `profile_update` 或 `incremental` 的 `bootstrap_events` 才进入 durable event ingress。部分 bootstrap scope 或 discovery 分页 / 输入失败可用 `degraded` 保留已得 items；零有效 item 的失败才是 `failed`。Cookie 和原始 Linux.do 响应不属于 payload。 |
| `POST /api/sources/linuxdo/kick` | ✅ | 经 runtime-stream 广播 `linuxdo_task_available`，让在线扩展立即 poll；不直接访问 Linux.do。 |
| `POST /api/sources/linuxdo/login-state` | ✅（兼容端点） | 只接受 strict boolean `logged_in`，持久化扩展对 `_t` 存在性的观察；不接受 Cookie 字符串。公开 discovery 的 `auth_required` 仍为 false。 |

Linux.do 站点访问全部发生在真实 `linux.do` task tab 内，且只允许同源 JSON `GET`。个人 bootstrap 先以 `/session/current.json` 正面确认 username；`_t=true` 只是 source-auth 心跳，不能替代任务内身份确认。结构化错误只包含 code/status/path，不把 challenge HTML、JSON body、Cookie 或 CSRF 字段带进回调。dispatcher 在执行前把 task/tab/deadline 写入扩展 session storage；MV3 service worker 重启时先恢复 runner，仍存活的任务 tab 可把结果交给恢复后的 handler 重试后端回传，不会重跑上游 GET。完整契约见 [Linux.do 来源文档](linuxdo.md)。
周期任务 payload 带 `incremental=true`；六源 handler（含 V2EX）在 guided init 外给 durable event 标记 `profile_update_owner="generic"`，在 init-owned 回调中只落事实、由阶段 2/3 统一建模。事件 ingress 成功或 duplicate receipt 后才按响应顺序 checkpoint seen key，再翻 terminal；没有 handler 直接调用画像 pipeline。扩展离线时 runtime 不创建任务，也不推进调度时间。

## B 站与抖音浏览器任务边界

| 方法与路径 | 状态 | 契约 |
|---|---|---|
| `POST /api/sources/bili/task-result` | ✅ | B 站扩展搜索任务 payload 带 `discovery_lane="recent"` 时，结果仍以 `source_strategy="bili-extension-search"` 进入统一 evaluator，同时保存 `source_context="bili-extension-search:recent"` 和 raw lane provenance；该标记不改变相关性、阈值或 admission。 |
| `GET /api/sources/dy/next-task` | ✅ | 对 legacy discovery 的 `dy_tasks` 执行 durable source-wide single-flight：`BEGIN IMMEDIATE` 内若已有未超过 15 分钟的 `in_progress` lease，则返回 bodyless 204，不让另一个扩展 ID/Profile 领取第二条任务；过期或缺失 `claimed_at` 的 lease 可按 FIFO 重领。此门禁不涵盖先于 legacy queue 检查的 native-save job。 |

抖音的 15 分钟 stale lease 与 producer 的 15 分钟基础设施失败退避来自 2026-08-09 真实扩展 E2E：正常任务约 15–35 秒，而执行上下文丢失会耗尽 180 秒 watchdog。它们是防止重复 claim/分钟级重试风暴的工程安全值，不是抖音官方限额；任务 watchdog、lease 或调度 cadence 改动时必须重新校准。

## 封面代理与抓取状态

`GET /api/image-proxy?url=...` 先在线程中读取本地 `data/image-cache/`；命中不占网络槽并返回原始图片类型、`Cache-Control`、`nosniff` 与 `X-Image-Cache: hit`。未命中进入 app-owned `ImageFetchCoordinator`：API 前台请求和 `ContinuousRefreshController` 后台预取共用总上限 4，后台最多 3，队列有前台请求时优先放行；同一 `image_cache_key(url)` 只产生一个 upstream task。单个 HTTP waiter 取消不会取消共享抓取，>=500 失败仍会在线程中做一次“并发写入已落盘”的 cache race fallback。成功响应保留 `X-Image-Cache: miss`。

抓取继续复用统一 SSRF 边界：域名白名单、每次 redirect 重验、`image/*`、10MB 上限，以及国内 CDN 直连 / 境外 CDN 继承代理。微博封面只允许域名边界匹配的 `sinaimg.cn` / `*.sinaimg.cn`，并归入国内直连；形如 `evilsinaimg.cn` 的后缀伪装仍被拒绝。真实新浪图床在共享浏览器 UA 下要求防盗链头，因此当前 redirect 目标属于 `sinaimg.cn` 时附 `Referer: https://weibo.com/`，跳到其它白名单 CDN 后立即移除。磁盘写入使用同目录临时文件 `flush + fsync + os.replace`，失败只保留旧文件或无文件，不暴露半写结果。日志只记录 host、cache hash 前缀和错误类别，不记录签名路径/query；`GET /api/runtime-status` 公开 `image_fetch_active/waiting/inflight_keys` 与 `upstream_started/singleflight_joins/peak_active/peak_background`，这些字段只含整数，不含 URL 或 token。协调器不随 `RuntimeContext` 热重载替换；新 controller 在后台任务恢复前重绑同一实例，shutdown 先停 refresh producer 再取消协调器持有的 active/queued upstream task。

## 异常报警（LLM / Embedding 请求失败）

| 方法与路径 | 状态 | 契约 |
|---|---|---|
| `GET /api/diagnostics/alerts?since_id=&limit=` | ✅ | 只读返回进程内异常报警环形缓冲的快照：`{"alerts":[...],"summary":{"total","errors","warnings"},"generated_at"}`。每条 alert 含 `id`（单调递增）、`category`（`llm\|embedding`）、`code`（LLM：`rate_limited/auth_failed/timeout/bad_response/provider_error/all_providers_failed`；embedding：`provider_error/breaker_open`）、`severity`（`warning\|error`）、`source`（实例名或 provider/model 标签）、`message`、`first_seen/last_seen`（epoch 秒）与 `count`（合并计数）。列表按最新在前排序；`since_id` 只返回更新行（供客户端增量拉取），`limit` 服务端上限 500。数据源是顶层模块 `openbiliclaw.diagnostics_alerts` 的进程内有界缓冲：同类别/来源/错误码在 60 秒窗口内合并为一条并累加 `count`，上限 100 条；`record()` 永不抛错、不阻塞 LLM/embedding 热路径，缓冲随进程重启清空、不落盘。 |

新告警在记录的同时经 event hub 以 `{"type":"diagnostics.alert",...alert 字段}` 发布到 `/api/runtime-stream`，桌面 Web 与插件设置页在日志面板可见时无需轮询即可实时刷新；拉取失败时两端都保持现状静默重试，不打扰用户。展示面为桌面 Web 与扩展 popup 两端：移动 Web 没有日志/设置面，CLI 没有运行时 feed 展示命令，均明确不在范围。

## 降级配置恢复

`PUT /api/config` 在 `llm_registry_unavailable` 降级态下不再只写盘并要求重启。服务端会复用当前进程已经初始化的数据库、MemoryManager、事件总线、任务注册表和 LLM total gate，通过正常热重载路径原子构造完整的 LLM Registry、Soul、Discovery、Recommendation、来源客户端与 runtime controller。构造全部成功后才解除业务 API 的 503 guard，并在后台应用状态进入 `applied` 后广播 `config_reloaded`；`/setup/` 会等待该终态，插件与桌面设置页也会观察同一状态后继续。

如果核心运行时构造失败，已有 `config.toml` 会从事务备份恢复，响应为 HTTP 503、`ok=false`、`rollback_applied=true`，降级 guard 保持不变。若核心已经成功发布、只是附属后台循环重启失败，则保留已生效的新配置与健康运行时，返回 `ok=true`、`reloaded=true` 并携带 warning，避免把磁盘配置回滚成与内存运行时不一致的旧版本。只有没有可回滚旧文件且进程内激活失败的异常 bootstrap 路径，才保留 `restart_required=true` 兼容兜底。

## 小红书任务安全边界

| 方法与路径 | 状态 | 契约 |
|---|---|---|
| `GET /api/sources/xhs/next-task` | ✅ | native-save job 仍是用户显式动作；自动 discovery 的 search / creator / bootstrap 则在每次 claim 前动态检查 `sources.xiaohongshu.enabled` 与 `scheduler.enabled`。任一关闭时返回 bodyless 204，既有任务保持 pending，不会再驱动扩展打开页面。search / creator 的 `task_interval_seconds` 是目标值，后端按任务 ID 施加 ±25% 稳定抖动并持久化实际下一次时间；处于节流或平台冷却时返回 204，存在明确等待时间时附 `Retry-After`。 |
| `POST /api/sources/xhs/task-result` | ✅ | 除 `ok / partial / empty / error` 外接受 `status="rate_limited"`。legacy task 命中后终结该任务、按连续轮次持久化 `1h → 2h → 4h … → 24h` 平台冷却，并将关联 `source_keyword_id` 从 executing 无损退回 pending；同一活动冷却内的重复报告不增加轮次，native-save 结果命中同样打开平台级冷却。冷却后的正常 search / creator 完成会重置轮次，活动冷却中的晚到成功不会提前解封。search / creator 的 `empty` 仍作为可重试失败，但缺失 error 的旧插件 payload 会归一为 `xhs_empty_result`；扩展结构化 debug 只允许 pathname、页面生命周期和 route anchor 计数，不要求或存储搜索词、验证页全文或页面 state。 |
| `POST /api/sources/xhs/observed-urls` | ✅ | URL-only 与带 note metadata 两条分支都接受 `/explore/{id}`、旧 `/discovery/item/{id}` 和 `/search_result/{id}` 三种笔记路由；`/search_result?keyword=...` 搜索列表页本身不计入 accepted。metadata 继续进入 `discovery_candidates`，URL-only 继续写 observed ledger 并参与 token 回填。 |
| `GET /api/sources/status` | ✅ | 来源仍开启且冷却生效时，将小红书 legacy 状态投影为 `state="rate_limited"`、`feed_paused=true` 并显示连续触发轮次和剩余分钟；来源已关闭时不让冷却覆盖 `enabled=false` 的正交配置事实。该端点只读本地状态，不访问小红书。 |

## 知乎任务边界

| 方法与路径 | 状态 | 契约 |
|---|---|---|
| `GET /api/sources/zhihu/next-task` | ✅ | native-save job 仍是用户显式动作；自动 bootstrap / discovery 在每次 claim 前动态检查 `sources.zhihu.enabled` 与全局 scheduler / 增量总开关。来源关闭时返回 bodyless 204，scheduler-owned 的增量任务会被标记 `failed`（避免卡住其它来源的调度），手动 pending 任务保留为 `pending`，重新开启后可继续领取；扩展因此不会因已排队任务打开知乎前台页。 |

## 对话确认端点

### Turn 级上下文绑定（2026-08-01）

`chat_turns.reply_to_turn_id` 是用户 turn 指向 durable card/question 的唯一显式关系。带关系的
请求只提交 target ID；服务端在 user row INSERT 前读取 completed target 与 settlement queue 的
exact admission snapshot，生成并冻结 `payload.dialogue_binding`（`bound`、canonical context、
完整 `context_digest`）。`kind/ref/generation/title/evidence` 等客户端字段不被采信，冲突的
scope/subject 返回错误；无关系请求继续区分 `ordinary` 与 `detached`。

回复、历史关系前缀、raw dialogue event、learn envelope、engine provenance 与卡片结算都消费
同一冻结 binding。绑定 target 在 POST 前已过期、被保留、失败或不存在时不会创建 fallback user
row；相同 `turn_id` 的同一 normalized request 仍幂等，任何 relation/message 分歧返回
`turn_id_conflict`。

| 方法与路径 | 状态 | 契约 |
|---|---|---|
| `POST /api/chat/turns` | ✅ | 普通消息在 user row INSERT 前解析可选 `reply_to_turn_id`，冻结 server-owned canonical `DialogueTurnBinding`（bound/ordinary/detached）和 context digest；随后落成 `pending` 并立即返回，只向 app-owned `DurableChatReplyScheduler` 发 wake；单 worker 按 `chat_turns.rowid` 严格串行生成回复，启动会分页恢复全部 pending。provider、限流、配置、超时与取消都保持 pending 并原位有界退避，不能被后续 turn 越过；只有显式无效/空响应可终结为 failed。例外（issue #213）：连续 3 次快速失败且均被分类为 `no_provider`（模块路由 / 全局链解析不到任何 chat-capable 实例的配置型错误，重试不可能自愈）时，turn 以带修复提示的 failed 终态发布，避免无限「正在思考」并阻塞后续 turn。`scope="hypothesis"` 时服务端生成结构化卡片 payload（`type/kind/ref/title/evidence_refs/actions/state`），直接返回 `status="completed"`，不会调用 LLM worker。若双轨冷却允许，普通 durable 用户消息会先原子插入一条系统确认卡/问题，再写用户 turn；payload 的 `attached_to_turn_id` 负责重试与重启去重。 |
| `GET /api/chat/contexts/{reply_to_turn_id}` | ✅ | 只读返回 canonical context preview（target、kind/ref/generation、可读 evidence、digest）。不创建 queue job、anchor、event，也不修改 card；三端只持久化 target ID，并用 preview 校验恢复。 |
| `GET /api/chat/turns?session=<label>` | ✅ | `session` 只过滤当前 UI 可见 turn；插件、移动 Web、桌面 Web 的主聊天统一使用 `session=popup` 并读取完整 `chat/hypothesis/confusion` 可见历史，因此三端共享普通消息、确认卡和澄清问题；其它 session 仍可用于隔离集成。不同 UI 仍共享一份认知 history。列表中的每个非终态卡片只 submit `card.reconcile` 到唯一结算队列并返回本次 durable 快照；request task 不直接写 card/object/anchor。 |
| `GET /api/chat/turns/{turn_id}` | ✅ | 返回单个 durable turn。普通 turn 仍为 pending 时只幂等唤醒同一 reply worker，重复轮询不会复制 queued/in-flight/backoff 工作。若读到非终态卡片，只同步 admission `card.reconcile` 并立即返回快照。worker 会为 `applied=1` receipt 补 stable audit、跨 session projection 与 exact-generation 解锚，也会把没有对应 active anchor 的 orphan `discussing` 校正回 `pending`；因此 publication gap 的第一次 GET 可仍见旧态，queue 完成后的下一次 GET 见权威状态。 |
| `GET /api/chat/pending-confirmations` | ✅ | 读取前在 settlement worker 空闲时扫描 orphan claim：只有 `clarifying` claim 已超过 30 秒创建安全窗、ask-turn identity 未变化、且 durable turn 仍不存在时才释放；worker 正忙时跳过该次修复并直接返回 durable 快照，避免只读 UI 被长 LLM job 卡住，下一次空闲读取/open 会继续修复。随后返回 `{"count":N,"items":[...]}`；只列未结算的高优先级对象且最多 3 条：未验证假设 `confidence>=0.60`、active 疑惑 `interpretation_confidence>=0.50`。已 defer 的假设在 `deferred_until` 到期前不进入该列表，到期后自动恢复；用户主动 open 仍按“手动绕过冷却”处理。无活跃澄清时疑惑固定预留 1 席；已有全局 `clarifying` 时只保留该持有者，隐藏必然无法 claim 的其它 open 疑惑。UI 传 `?session=popup|webui` 后，若该持有者已在本 session 有 turn，则不重复显示；其它 session 仍可打开同一 ref 并获得本地 turn。`?count_only=1` 保留轻量只读响应 `{"count":N}`，供兼容客户端/诊断使用；当前 service worker 明确不调用它，工具栏角标只表达后端不可达或未初始化，待聊数字只在 popup、移动 Web 与桌面 Web 的对话入口显示。`openbiliclaw questions` 读取完整响应且不复制筛选规则。用户主动列表不套用系统冷却。 |
| `POST /api/chat/pending-confirmations/{ref}/open` | ✅ | body 为 `{"session":"popup|webui|..."}`。若唯一 settlement worker 正在处理长 LLM job 或处于原子交接，端点在任何 claim/turn 写入前返回 `503 detail.code="dialogue_busy"` 与 `Retry-After: 2`；popup、移动 Web 与桌面 Web 共享 helper，最长按安全热重载窗口自动重试并显示等待态。空闲后，假设生成 completed card；疑惑通过 required `confusion.open.sync` 进入 `clarifying`，再由 required `anchor.establish` 以 `pending_open` 建锚，不使用会超时后继续执行的 1 秒 fast path，因此不会留下“claim 已完成、turn 未创建”的半截状态。相同 `(ref,session)` 原子复用，跨 session 各自产 turn；API 不在 request task 执行 protected mutation。 |
| `POST /api/chat/cards/{turn_id}/action` | ✅ | body 为 `{"action":"confirm|reject|discuss|defer"}`。四动作分别 submit `settle.hypothesis`、`card.discuss`、`card.defer` 到唯一队列；confirm/reject 与锚定 `support/contradict/revise/answer`、普通 chat settles、legacy endpoint 共用 immutable ref winner。discuss 在 worker 内 `pending→discussing→建锚`，建锚失败立即补偿回 pending；defer 只对 pending/discussing 卡在 worker 内更新卡片/冷却，若卡由 pending-open 建锚但仍保持 `pending`，会按 origin turn 精确释放同代锚，若卡已 confirmed/rejected 则返回权威终态的 `already_settled` 且不写 cooldown。HTTP 最多等本地 job 1 秒，完成保持同步 `200`，队头阻塞返回 `202 processing` 且不会取消已入队 job。 |
| `POST /api/insights/feedback` | deprecated | 保留旧客户端响应结构和 `Deprecation: true`，内部通过共同 façade submit 同一队列，台账 `source="legacy_endpoint"`；1 秒内未完成时同样返回 HTTP `202`，不新增 legacy 专用 executor。**锚冲突返回 `409`**：当另一张卡片持有对话锚时结算会被拒绝（`outcome=stale_anchor` / `anchor_dependency_failed`），此时 `card_settlements` 与台账都没有写入，端点返回 `409` 并在 detail 里说明原因，`Deprecation` / `Link` 头仍然保留。旧行为把这种拒绝包装成 `200 {"ok":true,"matched":false}`，老客户端会误以为确认成功。 |

### 卡片 action 返回

- `outcome="applied"`：本次已由 worker 完成 event/object/derived/rebuild marker 并发布 `applied=1`。
- `outcome="already_settled"`：已存在 `applied=1` 的对象结算；返回既有 verdict 并刷新本卡片投影。
- HTTP `202` + `outcome="processing"`：本地 1 秒等待预算耗尽；入队 completion 被 shield、继续在唯一 worker 执行，不会把 `applied=0` 伪装成终态。
- `outcome="discussing"` / `"deferred"`：分别表示活锚已建立 / 当前卡片已延期。
- `state="revised"`（终态，文案「已按你的修正记下」）：修正式结算——原假设被替换、派生假设已写入。它**不是** `rejected`；把 revise 投影成否定会让刚说完「我认可修正版」的用户看到「已标记不准」。
- `outcome="stale_anchor"` / `"anchor_dependency_failed"`（`state="stale"`）：对话锚被另一张卡片占用，本次结算被拒绝，`card_settlements` 与台账均无写入。前端共享 helper 把这两个 outcome 归入 `retryable_error`：乐观态回滚到操作前的真实状态，提示用户先结束当前正在聊的那条再重试——**不得**回落到乐观终态，否则卡片会显示「已确认」而后端什么都没记。

## 一致性边界

所有生产 `dialogue.respond()` 入口（durable reply、惊喜 chat、legacy `/api/chat`、兴趣探针 chat、避雷探针 chat）共享 app-owned `DialogueExecutionCoordinator`，同一时刻最多一个 active execution。调用方拿到 lease 后才解析当前 `ctx.dialogue` 与对应 Soul speculator，并把回复后的认知、事件与状态副作用一并留在 lease 内。配置热重载先暂停 admission、排空 active execution，才发布新 runtime；等待中的请求恢复后解析新 owner。25 分钟内不能排空时不调用 rebuild，恢复旧 lane 并回滚配置。guided init 的 `resume_execution_lanes=false` 只控制 event lane，不会把独立 chat lane 留在 paused。

durable reply 的可见终态使用 `WHERE status='pending'` compare-and-swap：模型调用在进程崩溃窗口可能至少一次，但 completed/failed 只发布一次。`/api/runtime-status` 以 SQLite 的真实 pending 数暴露 `chat_reply_depth`，另有 `chat_reply_active/last_error/processed`；即使 runtime controller 降级不可用也保留 event/chat scheduler 状态，且字段不含用户消息或回复内容。

`event_lane_depth` 只表示调度器当前是否有 dirty wake（`0/1`），不是 SQLite event backlog，也不是两个 durable cursor 之后的待处理行数。真实恢复依据是 `events` 表与 `pipeline_state.json` 中各 consumer checkpoint；`event_lane_active/last_error/processed` 只描述 app-owned owner pass。

所有声明的对话结算入口只进入一个 `DialogueSettlementQueue`、一个 actual worker。confirm/reject 的顺序固定为：`INSERT OR IGNORE` 固化 immutable winner → event identity 与 event 同事务 → object → derived → rebuild marker + stable audit → `applied=1` → 跨 session projection → exact-generation 解锚。卡片 action、legacy endpoint、锚关系与无锚 chat 的 speculation/insight/confusion settles 共用这条 ref 路径；只有 `applied=1` 可生成终态投影。protected façade 校验 actual worker Task + lifecycle nonce；worker 内嵌套 settle 由该 task 直接 `_apply_*`，不会 submit/inline dispatcher。API request、active child 与跨 job detached child 均不能写或冒充队外 producer。

`card_settlements` 不再保存 claim/lease/token/`seg_*`，也没有文件锁、takeover 或恢复 scanner。rebuild marker 仍使用同目录临时文件 `flush+fsync` 后原子替换；写盘失败会使 job 失败且 receipt 保持 `applied=0`，不会提前投影卡片。后续同 ref 显式重试采用原 winner，幂等 effect 补齐缺口。

对话内结算（锚归属 `support/contradict/revise/answer`）落库在**回复完成之后**——worker 还要跑归属判断和队列 job。因此桌面 Web 在回复完成后继续按 1/2/5/5/5/5/5 秒重读对话，直到卡片进入终态或用完 ~30 秒预算（与卡片 action 的 `CARD_ACTION_POLL_DEADLINE_MS` 同量级）；只在屏幕上确有未结算卡片时才轮询。少了这步，用户说完「我认可修正版」后卡片会一直停在「正在聊这条」直到手动刷新——真机浏览器 E2E 实测 8 秒预算会漏掉。

队列本身是进程内、非 durable 的：若进程在 `202` 后重启，尚未执行的 job 可以丢失，但 durable card/receipt 不会伪终态。popup、移动 Web 与桌面 Web 对 `202 processing` 按 `1s/2s/5s`（之后保持 5s）读取 `GET /api/chat/turns/{turn_id}`，总截止 30 秒；终态立即停止，超时、读取持续失败或页面 abort 显示本地 `retryable_error`，允许刷新或重试 action。三端的 active insights/认知更新区保持只读；CLI/OpenClaw 也不消费该 HTTP action 契约。

系统抛出的两个 gate 必须同时满足：距上次全局抛出至少 12 小时，且同 ref 的 `last_asked_at` / `deferred_until` 已超过 72 小时；两者持久化在 `memory/dialogue_confirmation_state.json`。用户主动 open 明确绕过这两个时间 gate，但疑惑仍受数据库 `clarifying <= 1` 约束。附着 turn 与用户 turn 同秒时，以 `(created_at,rowid)` 保证卡/问题在前；空消息校验与既有 `turn_id` 幂等检查均发生在附着前。

## 客户端入口约束（Wave D）

popup、移动 Web 与桌面 Web 只有 durable 对话中的假设卡片保留 confirm/reject/discuss/defer 主动动作，并共享上述按需轮询 helper；同步 `200` 不启动额外轮询。三端画像/认知更新区均只读。`openbiliclaw questions` 也只发 GET 并展示列表。`POST /api/insights/feedback` 仍为旧客户端保留并转发共同队列，但新客户端不再调用它；因此“对话是唯一主动 UI 确认入口”与 legacy 兼容同时成立。

## Runtime stream 保活与重连

`GET ws://.../api/runtime-stream` 在 20 秒没有业务事件时发送 `{"type":"runtime.heartbeat","sent_at":"..."}`。心跳与普通事件共用唯一 writer，避免并发 `send_json`；鉴权撤销仍在每次发送前和 15 秒 watchdog 中 fail closed。桌面 Web 收到心跳即确认“实时连接正常”，异常 close 则显示“实时流重连中”、记录 close code/reason，并按 3 秒节奏重连；页面进入后台时仍按 visibility 生命周期主动关闭，不把该主动关闭显示成后端离线。

`dy_task_available` 等 task-available 帧用于唤醒浏览器扩展 dispatcher，不是用户活动。桌面 Web 会在运行时状态投影之前丢弃 `dy_task_available`，避免把原始 wire type 显示成首页“现在在忙”；扩展仍照常消费该事件并立即轮询任务。
