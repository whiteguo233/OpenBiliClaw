# V2EX 来源模块

## 当前状态

V2EX 已接入公开 discovery、可选 PAT、浏览器只读 bootstrap 和 guided init 主链：

- 公开取数使用匿名 JSON API、JSON Feed 和 RSS/XML Feed；
- 可选 PAT 使用 Bearer 认证访问 API 2.0，并可通过 `/member` 做 live probe；
- `search`、`node`、`tab`、`hot`、`latest` 五个分支统一转换为 `DiscoveredContent`，进入共享 `discovery_candidates` / evaluator / admission；
- 桌面 Web、移动 Web 推荐卡和插件 popup 推荐卡都识别 `v2ex:<topic_id>` 与无封面文字卡；
- 扩展任务支持 `public_topics`、`public_replies`、`favorite_topics`、`favorite_nodes` 四个只读 scope，结果通过 staged task-result 进入统一事件入口；
- Reply 按 Topic 聚合后生成 `discussion_reply`，不作为独立推荐项；初始化事件会投影到 `v2ex_node_affinity`，Node producer 可用真实 Node 偏好补充召回；
- `V2EXTaskQueue` 已纳入多来源 bootstrap 串行准入和增量调度；扩展会为每个 scope 给出保守的 `scope_complete` 证据，达到页数上限、登录失败或解析失败都不能冒充完整快照；
- 收藏 Topic / Node 使用账号隔离的完整快照账本：第一次完整快照缺失只增加计数，连续第二次仍缺失才通过 durable outbox 生成 `retraction(favorite|follow)`；重新收藏会生成下一代正向事件，任务重领不会重复产生 effect；
- 身份由后端按 PAT verified → 浏览器 observed → 配置 / 用户 accepted 的阶梯统一解析；证据冲突时账号画像、Node Affinity 和收藏快照暂停，匿名公开 discovery 继续工作；增量 seen key、快照和 Node Affinity 均按账号隔离；
- V2EX Client 和扩展任务均只读，不发帖、不回复、不感谢、不收藏、不关注 Node；真实匿名 smoke 已覆盖 Hot / Latest / Node / Tab / Search、Atom 字段解析、旧 Topic 详情和正式 producer 文字卡入队；HTTP body 先按解码后字节做上限检查，再移除已消费的 `Content-Encoding` / `Content-Length` / `Transfer-Encoding`，避免 gzip 响应在重新物化时被二次解码。

2026-08-10 已在 `8420` 真实后端、已安装开发扩展和真实登录账号完成最终只读 E2E：最终构建通过 `/api/extension/reload` 热更新并确认 delivered，四个 scope 在约 9 秒内返回发布 4、讨论 Topic 19、收藏主题 1、收藏 Node 0，24 条 canonical 事件全部通过稳定 ID / URL / source / satisfaction 断言，四个 `scope_complete` 均为 `true`。`smoke_only` 后真实库的 V2EX event、seen、收藏快照和 Node Affinity 增量均为 0；同一批事件在隔离库首次写入 24 条、第二次 24 条全部判重。五路公开请求再次通过（Search / Tab / Hot / Latest 各 3 条，Node 5 条）；使用用户真实 LLM / Embedding 配置的隔离正式 Node producer 发现 3、入待评估池 3、评估 3、准入缓存 3，只产生 1 条脱敏 LLM usage 记录，临时库退出即删除。桌面、移动 Web 与 popup 的构建截图均验证 V2EX 紧凑文字卡，不再为无封面 Topic 预留 16:9 空白媒体区。

身份冲突卡片已在桌面设置页和插件 popup 提供交互式账号选择；选择新浏览器账号后必须重新执行完整 guided init。新账号事件先以 inactive 投影暂存，只有 Soul Profile 构建提交成功后才原子切换 active identity；旧账号事件、Node Affinity 和收藏快照继续按账号保留，不会混入当前画像。Node Affinity 已实现首版确定性意图折扣、180 天半衰期、收藏 Node 的衰减下限，以及投入阅读 Topic 的幂等计数。公开 discovery 不依赖登录态；PAT 与浏览器登录态保持为两条独立能力。

官方协议依据：[V2EX API 2.0](https://global.v2ex.com/help/api) 和 [V2EX 官方 RSS / JSON Feed](https://blog.v2ex.com/rss/)。

## 来源契约

| 字段 | 约定 |
|---|---|
| slug | `v2ex` |
| 推荐单元 | Topic；Reply 不作为独立候选 |
| 稳定 ID | `v2ex:<topic_id>` |
| `content_type` | `topic` |
| 作者 | `author_name` |
| 分类 | Node slug / Node title 写入 `tags` |
| 策略 | `v2ex-search`、`v2ex-node`、`v2ex-tab`、`v2ex-hot`、`v2ex-latest` |
| 鉴权 | 匿名可用；PAT 可选、可验证；浏览器登录态只读 |
| 默认状态 | `enabled = false` |
| 默认来源比例 | `bilibili = 5`、`v2ex = 1` |
| 写操作 | 禁止 |

缺失的浏览量、点赞、收藏、分享和评论计数保持为 0，不把 Node 信息冒充为互动指标。V2EX Reply 数映射到 `reply_count`。

## 公开取数与降级

`V2EXClient` 按以下顺序提供能力：

1. 无 PAT 时读取匿名 `/api/topics/hot.json`、`/api/topics/latest.json`、Topic 详情和 Node JSON Feed；
2. Tab 优先读取 `/feed/tab/<tab>.json`，JSON Feed 不可用时回退 XML；
3. 用户主题读取 `/feed/member/<username>.xml`；
4. 有 PAT 时，Node / Topic 详情和 Node Topics 使用 `/api/v2` 的 Bearer 接口；PAT 被 401/403 拒绝时，producer 丢弃 PAT 并继续匿名 discovery；
5. 429 记录 `X-Rate-Limit-*` / `Retry-After` 并写入来源冷却，网络和 5xx 使用有限重试。

API 2.0 的正式响应按 `{"success": true, "result": ...}` 解包，失败 envelope 归一成安全错误，不透传服务端消息；旧 `/api/topics/show.json` 的单 Topic 列表响应取首个合法对象。Atom Feed 同时支持嵌套 `<author><name>`、`<content>` / `<summary>` 和 entry `id`，避免 Tab 文字卡丢作者、正文或 Topic ID。`X-Rate-Limit-Reset` 按 Unix epoch 计算冷却，而不是误当作“剩余秒数”。流式客户端读取的是 httpx 已解码字节，因此重新构造有界响应前会剥离内容编码和传输长度头；真实 gzip Node Feed 与回归测试共同覆盖该边界。

producer 在共享 evaluator 之前做有界确定性增强：只对当前 run 中字段不完整的前 `detail_fetch_limit` 条 Topic 请求详情；配置 PAT 时，再对最多 `reply_enrichment_limit` 条有回复 Topic 读取 API 2.0 第一页，选取最多 3 条非空回复生成讨论摘要。该摘要只附着于 Topic，Reply 仍不成为独立候选，也不会逐条调用 LLM；无 PAT、额度耗尽或单条增强失败时保留已有 Topic 字段继续入池。

V2EX 官方 API 表没有完整全文搜索端点。正式 `search` 优先复用用户已配置的外部搜索 provider（`[discovery].inspiration_search_backends` 中的 `bing_rss` / `exa` / `you` / `serply` / `parallel` / `keenable`），发送 `site:v2ex.com/t <query>` 做只读召回，解析 Topic ID 后再通过官方 Topic 详情补全并进入同一 normalizer。该召回配置与关键词的 legacy / hybrid / inspiration 生成模式相互独立：关闭 inspiration 生成不会顺带关闭正式 V2EX Search。外部 provider 未配置、失败或没有合法 Topic 时，才使用 latest/hot 的有界本地匹配。

latest/hot fallback 先保留完整 query 的精确命中；对统一 planner 产生的多段自然长词，再提取最多 8 个去重、非通用核心词做受限放宽，按“整句命中 → 命中核心词数量 → 命中字符数”排序后截断。没有核心词的泛化 query 不会扩召回，所有结果仍经过共享 evaluator 和 admission。fallback 不是页面爬虫，也不声称覆盖全站历史搜索。`keyword-inspiration-dry-run` / `keyword-inspiration-preview --platform v2ex` 在来源启用时会复用同一只读客户端做平台 grounding，并在命令结束时关闭连接。

## 浏览器 bootstrap

扩展只在用户选择 V2EX 初始化或增量同步时执行任务；普通 V2EX 页面只安装被动阅读事件采集器。任务页通过 URL marker 与普通页面隔离，读取渲染后的公开 DOM 行，不上传 Cookie、页面 HTML、请求头、CSRF/once、私信或密码：

| scope | 页面 | 事件投影 |
|---|---|---|
| `public_topics` | `/member/<username>` | `publish` |
| `public_replies` | `/member/<username>/replies` | 按 `topic_id` 聚合为 `discussion_reply` |
| `favorite_topics` | `/my/topics` | `favorite` |
| `favorite_nodes` | `/my/nodes` | `follow` |

每条结果只保留稳定 Topic/Node 标识、标题、URL、作者、Node、时间和有限回复摘录。回复最多保留每个 Topic 的 3 条代表性文本；同一 Topic 多页重复结果由后端任务结果合并和事件 key 去重。发布与参与讨论的满意度为 `unknown`，收藏主题为正向，收藏 Node 为 `follow`，避免把“参与讨论”误判为喜欢主楼观点。

浏览器登录心跳只向后端发送 `logged_in` 布尔值；可见页面中的用户名作为 `observed` 身份证据单独上报。显式登出心跳会清除旧 observed username，浏览器证据最多保留 72 小时。PAT 只有在 `/api/v2/member` 真实通过后才形成 `verified` 身份，且持久身份只保存公开 username 与当前 PAT 的单向 fingerprint，不保存 PAT；该声明最多信任 6 小时，匹配 fingerprint 的明确 401/403 会清除旧声明并阻止旧成功回退，网络失败不会伪装成令牌失效。配置或用户确认的 username 是 `accepted` 证据。

后端解析顺序为 PAT verified → 浏览器 observed → 当前配置 → 上次用户 accepted。所有非空证据按大小写不敏感比较；若指向不同账号，状态为 `identity_mismatch`，任务仍完成 canonical staged 保存，但不会把任何账号行为、Node Affinity 或收藏快照投影进画像。`GET /api/sources/v2ex/identity` 返回当前各证据来源与门禁结果；`GET /api/sources/status` 的 V2EX detail 同时给出冲突摘要，公开 discovery 不会因此停用。

扩展在每个 scope 开始解析前同时校验目标 route 和 V2EX `#Main` 内容壳；导航到首页、错误账号页或页面结构缺失时返回 `unexpected_page` / `parse_error`，不会把无关卡片当初始化数据。当前真实 member replies 页面把一页回复放在同一个 `.box` 中，并用相邻的 `.dock_area` + `.inner` 配对；executor 只从当前回复的前置 metadata 或旧版同一 `.cell` 读取 Topic 链接，禁止回退到共享 `.box`，避免把整页回复误归到第一个 Topic。真实页面还会把越界 `?p=3` 渲染为末页 `p=2` 而不改地址栏；executor 因此以渲染后的 `.page_current` 和后续页链接为权威，末页有数据也可直接给出耗尽证据，不会重复抓末页直到 `max_pages_per_scope`。达到条目上限时仍会用同页额外一行证明是否截断；仍有未接纳行则明确 `partial` / `item_limit_reached`，达到最大页数同样不能冒充完整快照。

## Node Affinity 与调度

`v2ex_node_affinity` 以 resolved username 分区，记录 scope、Node 和去重后的证据。原始权重为收藏 Node `3.0`、收藏 Topic `1.6`、发布 Topic `1.2`、参与讨论 Topic `0.8`、投入阅读 Topic `0.3`；同一 Topic 的多条回复和重复阅读投影都只计一次。有效排序使用 `log1p`、180 天时间半衰期和首版确定性意图折扣，交易、求职 / 租房等临时需求、自我推广与闲聊 Node 不会被等同于稳定兴趣；显式收藏 Node 在未撤回前保留 75% 的衰减下限。收藏撤回 / 恢复以 effect key 幂等更新当前计数和分数。

V2EX Topic 内容页的可见停留达到 30 秒时，扩展只从当前 Topic 的 `#Main .box .header` 读取匹配的 `/go/<node>`，事件获得数据库 receipt 后才投影 `engaged_view_count`。Node、Topic、域名和当前浏览器账号都必须与 active profile 一致；快速退出、跨账号观察或伪造 URL 不进入 Node Affinity。Node producer 未配置 allowlist 时最多读取 `max_profile_nodes` 个当前 active identity 的高分 Node，并公平轮转分页；全局的 search / node / tab / hot / latest 配比继续承担相邻探索，当前没有伪造一张不存在的 V2EX Node 邻接图。

guided init 通过来源选择器启用 V2EX，默认读取配置中的 `bootstrap_*_limit` 和 `bootstrap_max_pages_per_scope`，等待扩展完成任务后把结果并入统一 `events` / Soul Profile。任务失败或部分 scope 不可用时保留已成功的 scope，并返回分 scope 状态，不把缺失数据当成“用户没有行为”。

增量调度默认由 `scheduler.source_incremental_enabled=false` 全局关闭；显式开启后，`scheduler.v2ex_incremental_hours` 才控制 V2EX 周期，并在扩展在线时复用同一任务队列。首次 guided / 非增量任务的完整收藏 scope 也会种下账号基线，避免“初始化后、第一次增量前取消收藏”的对象永远不可知。之后只有扩展证明 `favorite_topics` / `favorite_nodes` 完整翻页时，后端才比较集合；第一次缺失写 `missing_streak=1`，连续第二次完整快照仍缺失时写 durable pending effect 并生成弱证据 `feedback/retraction`，事件入口接受后才 ack effect。页数截断、登录失败、身份冲突、网络或解析失败不会推进缺失计数。

## 配置

```toml
[sources.v2ex]
enabled = false
username = ""
access_token = ""
token_env = "OPENBILICLAW_V2EX_TOKEN"
source_modes = ["search", "node", "tab", "hot", "latest"]
tab_modes = ["tech", "creative", "qna"]
node_allowlist = []
node_blocklist = ["sandbox"]
node_downweight = ["promotions", "jobs", "deals"]
daily_search_budget = 120
daily_node_budget = 180
daily_tab_budget = 80
daily_hot_budget = 40
daily_latest_budget = 40
request_interval_seconds = 2
min_interval_minutes = 5
detail_fetch_limit = 15
reply_enrichment_limit = 10
max_topic_chars = 6000
max_reply_digest_chars = 1200
bootstrap_topics_limit = 100
bootstrap_replies_limit = 300
bootstrap_favorites_limit = 300
bootstrap_max_pages_per_scope = 20
```

PAT 读取优先级为 `OPENBILICLAW_V2EX_TOKEN` → `access_token`。PAT 不会通过 `GET /api/config` 返回明文；设置页只显示是否已配置，输入为空表示保持原值，显式“清除 PAT”才会清空。

## CLI 与 API

只读 smoke：

```bash
openbiliclaw fetch-v2ex --force --wait-seconds 300
openbiliclaw discover-v2ex "agent"
openbiliclaw discover-v2ex-node programmer
openbiliclaw discover-v2ex-tab tech
openbiliclaw discover-v2ex-hot
openbiliclaw discover-v2ex-latest
```

`fetch-v2ex` 使用真实安装扩展与当前浏览器登录态执行四个 bootstrap scope，并显式设置 `smoke_only=true`：结果会通过 staged task-result 保存并转换为 canonical 事件供 smoke 断言，身份 / 登录心跳仍可更新，但命令不写 memory、Node Affinity、收藏快照或 Soul，也不调用 LLM。默认等待 300 秒；`--force` 跳过 6 小时任务复用，`--username` 可固定公开账号路径。任一 scope 截断时返回部分成功并明确禁止完整快照推断。

正式来源补货需要先启用 `[sources.v2ex].enabled`：

```bash
openbiliclaw discover --source v2ex --limit 20
openbiliclaw discover --source v2ex --limit 20 --force
openbiliclaw init --yes-v2ex
```

扩展任务桥接端点为：

| API | 说明 |
|---|---|
| `POST /api/sources/v2ex/credential` | 统一凭据写入形态；V2EX 仅接受 `kind=login_state` 的布尔心跳，不接收 Cookie 值 |
| `POST /api/sources/v2ex/login-state` | 兼容旧扩展的 deprecated 布尔心跳入口 |
| `POST /api/sources/v2ex/identity` | 默认保存页面观察用户名；显式 `accept=true` 保存用户接受的身份 |
| `GET /api/sources/v2ex/identity` | 返回 PAT / 浏览器 / 配置 / accepted 证据、冲突状态与 bootstrap 门禁 |
| `GET /api/sources/v2ex/next-task` | 领取只读 bootstrap 任务 |
| `POST /api/sources/v2ex/task-result` | 提交分 scope 结果并完成 staged task |
| `POST /api/sources/v2ex/kick` | 请求立即调度任务 |

正式命令只把 raw Topic 写入共享待评估池；LLM 评分、准入、文案和推荐池写入沿用通用 discovery pipeline。

## 公开 Python API

| API | 说明 |
|---|---|
| `V2EXClient` | 只读 HTTP client，负责匿名 API / Feed / API 2.0、解码后响应上限、gzip 头归一、限流和错误归一化 |
| `V2EXPage` | 统一的分页 / Feed 行容器 |
| `V2EXAPIError` | `unauthorized`、`rate_limited`、`not_found`、`schema_changed` 等稳定错误 |
| `v2ex_topic_to_content()` | 将 Topic 行映射到 `DiscoveredContent` |
| `V2EXDiscoveryProducer` | 按来源开关、分支预算、节流和来源份额生产候选 |
| `V2EXTaskQueue` | 领取、合并、暂存和完成浏览器 bootstrap 任务 |
| `V2EXFavoriteSnapshotStore` | 比较完整收藏快照并维护 crash-safe retraction / restore outbox |
| `V2EXIdentityResolution` / `resolve_v2ex_identity_state()` | 无网络解析身份阶梯、冲突和私有 bootstrap 能力 |
| `V2EXNodeAffinityStore` | 按 resolved identity 保存 Node 证据、快照 effect 并提供召回排序 |

后续补齐项必须沿 [平台来源接入契约](../platform-source-integration.md) 扩展，并同步更新隐私说明、商店文案、真实浏览器 E2E 和发布检查清单。
