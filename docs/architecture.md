# 架构设计

## 系统概览

OpenBiliClaw 采用分层架构设计，从上到下依次为：

1. **用户交互层** — Chrome 浏览器插件（B 站 + 小红书 + 抖音 + YouTube + X (Twitter) 页面行为采集 · 视频停留满意度信号 · 推荐展示与真实可换库存状态 · 文字卡（推文 / thread）· 正向兴趣 / 避雷探针确认 · durable 对话交互 · 后台 LLM 暂停开关 · 开机自启动开关 · 配置离线缓存 / 降级修复 UI · xhs/dy/yt 任务调度 / 初始化画像导入 · B 站 / 抖音 / X Cookie 自动同步）+ 移动 Web（`/m`）+ 桌面 Web（`/web`）。所有 `/api/*` 前置一道**可选密码门禁**（HTTP 中间件，见下方「API Auth Gateway」）：本机 / 扩展默认免登录，局域网 / 远程设备需密码。
2. **外部集成层** — OpenClaw adapter / skill wrappers / 本地 API / Codex CLI 凭据导入等对外接入边界
3. **Agent 核心层** — 自研编排器 + Soul Engine + Discovery Engine + Recommendation Engine + Skill System
4. **多源适配层（v0.3.0+）** — `SourceAdapter` 协议下的 B 站 / 小红书 / 抖音 / YouTube / X (Twitter) / 通用 Web 源
5. **多层网状记忆存储** — Core / Episodic / Semantic / Working Memory（SQLite + 向量索引 + JSON）

详见 [项目 Spec](spec.md) 中的架构图。模块级可视化图放在 `docs/diagrams/`：

- [Soul 模块架构与流程图](diagrams/soul-architecture.html)
- [Recommendation 模块架构与流程图](diagrams/recommendation-architecture.html)
- [Web HTML 模块架构与流程图](diagrams/web-architecture.html)
- [Discovery 模块架构图](diagrams/discovery-architecture.html)

## 模块职责

### Agent Orchestrator (`agent/`)
- 任务调度和策略决策
- 多步推理和自省优化
- Skill 注册、发现和调度

### Integrations (`integrations/`)
- 对外系统接入边界
- adapter bootstrap、DTO 裁剪和异常翻译
- 将现有 runtime / engine 能力暴露为 OpenClaw 可调用 skill
- 提供 JSON CLI bridge，供仓库内真实 OpenClaw skill pack 调用

### User Soul Engine (`soul/`)
- 行为数据分析和画像构建
- 五层灵魂模型（事件→偏好→觉察→洞察→灵魂）
- 用户画像覆盖层（`overrides.py`）：用户手动编辑存独立 `profile_overrides.json`，在读收口 `get_profile()` 与镜像收口 `sync_profile_files()` 叠加到 AI 画像之上（有效画像 = AI ⊕ 覆盖），画像重建不覆盖用户编辑；删 / 拉黑经有效 dislikes 影响 discovery / recommendation / delight 硬过滤（Phase 1 后端；编辑 UI 见 Phase 2/3）
- `event_filters` / `satisfaction_filter_enabled` — 偏好分析前只丢弃 `negative`（quick_exit / explicit_negative）事件，保留 positive / neutral / unknown 作为上下文
- `negative_exemplars` — 从事件层抽取近期 negative 标题，供 Discovery eval-batch 做负样本锚点
- `InterestSpeculator` — 兴趣推测与投机性发现
- `AvoidanceSpeculator` — 不喜欢领域探针；未确认前只展示给用户确认，不进入推荐过滤，确认后通过共享 dislike writeback 写入 `disliked_topics` 并清理候选池
- 苏格拉底式用户对话

### Memory System (`memory/`)
- 五层网状记忆管理
- 跨层关联和双向修正
- 自我编辑和遗忘机制

### Content Discovery (`discovery/`)
- 多策略内容发现（B 站 search · trending · related_chain · explore + 小红书 `xiaohongshu` + 抖音 `douyin` + YouTube `yt_search` / `yt_trending` / `yt_channel` + X (Twitter) `search` / `feed`(For-You) / `creator`(账号订阅)），按 `runtime.source_policy` 生成的平台有效配比补池；默认保存的 share 为 B 站 / 小红书 / 抖音 / YouTube / X = 5 / 1 / 1 / 1 / 1，但默认只启用 B 站，关闭的平台不会占候选池 quota。B 站仍在主 refresh 计划内并行 fan-out；XHS / 抖音 / YouTube / X 低于可换 quota 时分别交给独立 producer；补货请求还会受 raw-material ceiling headroom 约束，避免不可服务库存已满时继续消耗 LLM / discovery。
- `DiscoveredContent` 全形态：新增 `body_text`（推文 / thread 全文）+ `content_type`（`video`/`note`/`tweet`/`thread`，复用候选池既有 shape 字段），让 X 这类文字为主的来源能正确流过统一待评估池并渲染成文字卡。
- 统一待评估池：各来源 raw candidates 先持久化到 `discovery_candidates(status='pending_eval')`；`DiscoveryCandidatePipeline` 按来源混合 claim batch，再调用 `ContentDiscoveryEngine.evaluate_content_batch()` 结合 Soul 画像、来源上下文和近期 negative exemplars 做统一 LLM 打分。
- 候选分层、去重和缓存写入：达标候选通过 `cache_evaluated_results()` admission 到正式推荐池 `content_cache`；写入时 `pool_status='suppressed'` 的旧候选在重新发现时自动复活成 `'fresh'`。`content_cache` 是 recommendation serve 的唯一正式池，`discovery_candidates` 是 discovery 阶段的待评估 / 已评估队列。
- v0.3.0+ 多样性栈：trending 按 rid 交错 / explore 按 domain 交错 / `_compress_topic_repeats` 单次压缩 / `trim_topic_group_overflow` 跨源跨轮配额（任意 topic_group ≤ 池子 10%）/ deficit-source 合并 + 并行 fan-out

### Sources (`sources/`) — 多源适配层 (v0.3.0+)
- `SourceAdapter` Protocol：每个内容源实现统一接口
- `bilibili_adapter` — B 站 API 直连（WBI 签名、v_voucher 自动恢复）
- `xiaohongshu_adapter` — 小红书扩展代理（被动收集 + 关键词搜索 + 创作者订阅 + `bootstrap_profile` 初始化画像任务，零后端爬取；task-result 进入 memory 前按已见 note key 跨任务去重）
- `dy_tasks` — 抖音扩展任务队列（`bootstrap_profile` 初始化画像任务；发布 / 收藏 / 点赞 / 关注信号由扩展以用户浏览器登录态抓取；任务 poll 时标记 `in_progress`，CLI 可复用近期 bootstrap；`search` 任务用于后台插件签名搜索，回传 `dy_search` 候选；`hot` 任务用于后台 `/hot/{sentence_id}` → related API，回传 `dy_hot` 候选；`feed` 任务用于后台首页 `/aweme/v1/web/tab/feed/`，回传 `dy_feed` 候选；三者分别作为 `dy-plugin-search` / `dy-plugin-hot-related` / `dy-plugin-feed` discovery 来源）
- `yt_tasks` — YouTube 扩展任务队列（`bootstrap_profile` 初始化画像任务；观看历史 / 订阅 / 点赞由扩展以用户浏览器登录态读取 DOM 并分批回传；任务 poll 时标记 `in_progress`，CLI 可复用近期 bootstrap）
- `youtube.takeout` — Google Takeout 离线导入解析器，将 YouTube 观看历史 / 订阅 / 点赞转换为统一事件
- `YoutubeDiscoveryProducer` — 后端直连的 YouTube steady-state discovery loop；在 YouTube 平台族低于 quota 时调用 `yt_search` / `yt_trending` / `yt_channel`，并用 SQLite execution ledger 控制每日执行预算
- `twitter_adapter` — X (Twitter) 服务端 cookie 重放（`source_type="twitter"`，标签 `"X"`）；`XAdapter.fetch()` 是真实实现（非 stub），按 recipe 分发到 `discovery/strategies/x.py` 的 `XSearchStrategy`（画像关键词）/ `XForYouStrategy`（推荐流 For-You）/ `XCreatorStrategy`（账号订阅）。配套 `x_client.py` 的 `XClient`（封装可选 extra `openbiliclaw[x]` 的 `twitter-cli`，lazy import + 只读 + 类型化错误）、`discovery/x_normalize.py`（tweet → `DiscoveredContent`）、`x_tasks.py`（`x_creator_subscriptions` CRUD）、`storage/x_health.py`（源健康状态机）
- `web_adapter` — 通用 Web（Playwright CDP + LLM 内容抽取）
- `SourceRecipe` — 源任务持久化与分发

### Recommendation Engine (`recommendation/`)
- 推荐排序与朋友式推荐表达生成；统一从候选池读取
- `/api/recommendation-click` 会保留 `content_id / content_url / source_platform`：插件、移动 Web 或桌面 Web 打开推荐内容后，后端把点击写成对应来源的统一事件和 `recommendation_click` 强画像信号；只传 `recommendation_id` 时会从 `recommendations + content_cache` 回填跨源字段，避免 YouTube / 抖音等 ID 被套成 B 站 URL。
- `PoolCurator` 五维评分（relevance · freshness · topic_fatigue · source_monotony · serendipity）
- v0.3.1 双轴 fatigue：`recent_topic_keys` (细) + `recent_topic_groups` (粗) 取 max；曲线 `count^1.5/len*5`，count=2 即触发 0.47 强抑制
- 新兴趣 amplification guard：刚确认的探针兴趣会用 domain/specific/topic key 形成 guard，`PoolCurator` 做 24h rolling budget 软降权，最终批选择做 `max(1, floor(limit*0.25))` 硬上限
- `_merge_topic_supergroups` — serve 时基于 embedding 把 `动漫杂谈/补番/解说` 等近义 topic 合并为同一聚类
- `prewarm_supergroup_embeddings` — refresh tick 后台预热所有池中 topic_group embedding，让 reshuffle 跑全 cache hit
- `batch_insert_recommendations` — 单 transaction 批量插入，避免 popup 给 10 条结果时 10 次 fsync
- 个性化专题生成

### Runtime (`runtime/`)
- 系统生命周期管理和服务编排
- 降级模式启动：生产 `create_app()` 遇到 LLM registry 配置错误时保留 `/api/health`、`/api/config`、`/api/runtime-status` 和 `/api/runtime-stream`，让 popup 设置页仍能保存修复配置；其他 API 返回 503，避免半初始化 runtime 继续跑推荐/发现链路
- 配置热重载：`RuntimeContext` 重建 registry / service / engine 时会从 `[llm.soul]` / `[llm.discovery]` / `[llm.recommendation]` / `[llm.evaluation]` 注入同一份 module override；热重载后的正向兴趣和避雷 speculator tick 都作为 detached task 注册到 `BackgroundTaskRegistry`，分别读取 `probe_feedback_history` / `avoidance_probe_feedback_history`，不阻塞 `/api/config` 响应
- `AutoUpdateService` — 后端自动更新只查询 GitHub `/tags` 并过滤 `backend-v*`（兼容 legacy `v*` / 裸 semver），明确忽略 `extension-v*`；当前 GitHub Releases 由扩展 artifact 占用，不能用 `/releases/latest` 判断后端源码是否最新
- `runtime.autostart` — 当前用户作用域开机自启动 manager：macOS LaunchAgent、Windows HKCU Run + `.pyw`、Linux XDG autostart；API / CLI / 插件设置页通过 `GET /api/autostart-status` 与 `POST /api/autostart/apply` 管理，带 env-managed / `config.local.toml` shadow guard，并用开启「先写 config 后注册 OS」、关闭「先注销 OS 后写 config」的方向化事务避免崩溃残留
- `runtime.ollama_supervisor` — `start` 启动前复用的 Ollama 预检 helper；从 chat / embedding / fallback 配置判断是否需要 Ollama，归一化 endpoint 并剥离 `/v1`，仅在默认本机 `localhost:11434` 缺 daemon 时尝试后台拉起 `ollama serve`
- `ContinuousRefreshController` — 后台定时刷新候选池；按平台族的前端可换配额评估 deficit，B 站缺货合并到一次 raw candidate production 并行 fan-out，小红书缺口交给 xhs producer / 扩展任务链；抖音缺口交给 runtime `DouyinDiscoveryProducer`，通过 `DouyinDiscoveryService(cache=False, evaluate=False)` 复用 search / hot / feed 后台插件签名链路补 raw candidates；YouTube 缺口交给 `YoutubeDiscoveryProducer` 后端直连补 raw candidates，主 refresh replenishment plan 不再 inline 调度 `yt_*`。cap enforcement 使用独立 raw-material ceiling（默认 `max(target*2, target+120)`）修剪 raw 库存，而不是把 raw 行数压成前端可换目标。
- `/api/runtime-status` / `runtime-stream` — 对插件、移动 Web 和桌面 Web 发布同一套候选池库存口径：`pool_available_count` 只表示当前可立即被 `serve()` 消费的内容，`pool_raw_count` 表示基础 fresh 素材加待评估 raw candidates，`pool_pending_count` 表示已有素材但仍缺评估、文案、分类、可跳转链接或仍在近期已看窗口内。`pool_pending_eval_count` / `pool_evaluated_pending_count` 分别拆出待 LLM 评估和已评估待 admission 的数量。前端只把 available 显示为“可换”，pending 显示为“正在整理”；后台补池的 source deficit 也使用 available-by-source，而 raw trim / headroom 使用 all-raw-material by-source。
- `_publish_probe_if_available` — proactive push 循环中的探针仲裁器；从正向兴趣和避雷探针池中每轮最多选一条，正向探针事件携带 `probe_mode/challenge`，普通 `near` 和挑战探针使用独立 active 额度；只投递 `active` 候选，且只有推送到订阅者后才通过原子 runtime state 更新记录 domain / axis / distance history，避免后台旧快照覆盖用户刚处理的探针反馈
- `background_llm_work_allowed()` — 共享 gate predicate；`scheduler.enabled=false` 会暂停 daemon-owned 后台 LLM / embedding 工作，`scheduler.pause_on_extension_disconnect=true` 时还要求浏览器插件 presence 在线或仍处于断开宽限窗口。该 gate 覆盖 refresh、pool precompute、soul pipeline、xhs/dy/youtube producer、proactive push、低频 account sync、startup one-shot 和 OpenClaw direct bootstrap；guided init 活跃时（`InitCoordinator.init_active()`）也返回 False，一处暂停所有后台循环，让 init 的显式 analyze / build / backfill 独占（init 自身直调 `soul_engine` / `run_init_backfill`，不查该 gate）
- `_enforce_pool_cap` 每 tick 跑 `trim_topic_group_overflow` + under-quota suppressed 候选复活 + 必要时按 share quotas 修剪过额源
- `InitCoordinator`（`runtime/init_coordinator.py`）— 图形化引导初始化的生命周期所有者：`init_runs` 持久化状态机 + 单写者进度事件（`_write_lock` 串行化，保证并行 stage 3/4 的 `sequence` 不丢更新）+ `BEGIN IMMEDIATE` 单飞 + 启动 reconcile（崩溃残留判失败）+ 协作取消 + bootstrap task 归属（供写者门控放行 init 自己的 task-result）。配套 `ContinuousRefreshController.run_init_backfill`（持 `_refresh_lock` 的发现补池）+ `InitPrereqs`（TTL 缓存的 chat / B站 / 平台前置探测）。共享流水线 `cli.run_guided_init` 详见 [init 模块文档](modules/init.md)
- `AccountSyncService` — 历史记录、收藏夹、关注列表同步；使用历史游标 + 已见 bvid/mid 集合只把新增账号信号送进画像分析；首次成功写入账号行为并完成 preference 分析后，若 soul 画像为空，会在同一进程生命周期内最多一次触发 `build_initial_profile([])` 自动 bootstrap
- `/api/sources/{xhs,dy,yt}/task-result` — 插件 bootstrap partial / final 结果完整保留在任务表；传播到 memory / profile pipeline 前读取 `source_bootstrap_state.json`，跳过跨任务已见 note/video/item key，避免旧收藏 / 历史再次触发画像更新
- `runtime-stream` — 浏览器扩展 background 以 `client=background` 连接后，若后端本地没有 B 站 Cookie，会推送 `bilibili_cookie_sync_requested`，扩展立即通过 `/api/bilibili/cookie` 回传当前浏览器 Cookie；后端持久化 Cookie、热重载 runtime 组件，并重新启动 refresh / account sync / auto update 后台任务，避免热重载取消后台循环后小红书 / 抖音 producer 停止；重复同步相同 Cookie 时不再重建 runtime，避免打断正在等待扩展回写的抖音 discovery。若 `[sources.douyin].enabled=true` 且后端没有环境变量或 `data/douyin_cookie.json`，会推送 `douyin_cookie_sync_requested` 并通过 `/api/sources/dy/cookie` 回传抖音 Cookie。后续推荐、惊喜、画像更新和探针确认仍复用同一条 WebSocket 事件流；`interest.probe` / `avoidance.probe` 只有实际进入至少一个 stream 订阅者队列后才写入对应 domain / axis 冷却状态，正向 probe 还会写入 `probed_distance_bands`，并在 payload 里暴露 `probe_mode/challenge`；正向和负向 probe 通过 `last_probe_kind` 每轮最多投递一条；同一连接也驱动 `PresenceTracker`，服务端 reader 会 `receive()` 检测 idle disconnect，避免浏览器断开后 presence 卡住
- `/api/image-proxy` — 移动 Web 和扩展 side panel 的推荐、惊喜和消息封面图统一走 `UI -> /api/image-proxy -> 白名单 CDN -> bounded spool -> UI`，后端在发送响应前完成 URL、redirect、Content-Type 和 10MB 实际字节校验

### API Auth Gateway (`auth_core.py` + `api/auth.py`)

- 局域网 / 远程访问的**可选密码门禁**。`create_app()` 在 degraded-mode guard 之后用 `@app.middleware("http")` 注册鉴权中间件（更外层、最先执行），挡所有 `/api/*`（含 `/api/runtime-stream` WS 与 `/api/image-proxy`）；`/api/health`、`/api/auth/*` 与静态壳（`/`、`/m`、`/web`）保持公开。
- `auth_core.py` 纯标准库：scrypt 密码哈希、HMAC 无状态签名 token、稳定密码指纹、反向代理 `X-Forwarded-For`（受信代理从右向左解析、fail-closed）与 Origin / scheme 归一化（CSRF `Origin==Host`、WS Origin、Bearer 裁定、`Secure` cookie 复用同一实现）。
- 默认凭据是 HttpOnly cookie `obc_session`（同源 fetch/img/WS 自动携带，前端不持有 token）；跨源限时 Bearer 为允许列表内逃生通道。改密 / 登出所有设备 / 轮换密钥经 SQLite `auth_state` 表的单调 `auth_epoch` 真正撤销所有设备；`session_secret` / `password_hash` 永不经 `GET /api/config` 返回。详见 [API Auth 模块](modules/api-auth.md)。

### Side Panel Durable Chat

插件聊天不再把主状态只放在 DOM / JS 内存里。`popup/` 对主聊天、惊喜推荐内聊和兴趣猜测内聊统一调用 `/api/chat/turns`：

1. popup 生成 `turn_id` 并 POST 消息、`scope`（`chat` / `delight` / `probe` / `avoidance_probe`）和可选的内容上下文。
2. 后端先把 turn 写入 SQLite `chat_turns(status='pending')`，随后用后台任务调用 Dialogue 引擎生成回复。
3. popup 通过 `/api/chat/turns/{turn_id}` 轮询，并在初始化时按 `session/scope` 重新 hydrate 历史。

这条数据流让 Chrome 在切 tab、reload 或内存压力下丢弃不可见 side panel 后，仍能恢复 pending thinking 占位、完成回复或失败状态。完成后的 delight/probe/avoidance_probe scope 会继续发布对应 cognition/runtime 事件，主聊天仍按原有受控学习链路进入画像更新。

### Init 多源画像导入

`openbiliclaw init` 的首轮信号现在由四条路径合流：

1. B 站 API 直连拉取观看历史、收藏夹和关注列表。
2. 后端在 `xhs_tasks` 表入队 `bootstrap_profile`，并在 `init --yes-xhs` / `fetch-xhs` 默认复用 6 小时内已有 bootstrap 任务，避免重复打开前台小红书 tab。浏览器插件轮询 `/api/sources/xhs/next-task` 时，后端会先把任务原子标记为 `in_progress` 并写入 `claimed_at`；15 分钟无回写才允许重新领取。插件在用户已登录的小红书页面中先打开 `/explore` 定位当前用户 profile。滚动任务会以前台 tab 触发页面内“我”入口的 anchor click，background 只等待同一 tab 完成导航；只有找不到可点击入口时才回退到直接导航。到 profile 后，插件解析 profile state / DOM 中的 `saved / liked` notes 和页面显式暴露的 `xhs_history` notes，回写 `/api/sources/xhs/task-result`。当任务显式传入 `max_scroll_rounds` 时，插件会在 profile tab 内优先探测 feed / waterfall / masonry 滚动容器做有限滚动，并先用 `status="partial"` 分批回传新增 notes，最终再用 `status="ok"` 完成任务；`scroll_wait_ms` 和 `max_stagnant_scroll_rounds` 也由任务 payload 控制，并由插件端裁剪到安全范围。
3. 后端在 `dy_tasks` 表入队 `bootstrap_profile`，由浏览器插件在用户已登录的抖音页面中依次访问发布 / 收藏 / 点赞 / 关注 scope。content script 结合 DOM 解析、MAIN-world fetch tap 和 API harvester 采集条目，按 scope 以 `status="partial"` 分批回写 `/api/sources/dy/task-result`，最终以 `ok` 完成任务。Douyin 默认需要显式 `--yes-douyin` 才进入 init；非交互式终端默认跳过，避免盲目触发风控或空 200 响应。CLI 默认复用 6 小时内近期 `bootstrap_profile`，扩展领取任务时会把 pending 标记为 `in_progress`。
4. 后端在抖音任务完成后再在 `yt_tasks` 表入队 `bootstrap_profile`，由浏览器插件在用户已登录的 YouTube 页面中依次访问 `/feed/history`、`/feed/channels`、`/playlist?list=LL`。YouTube 与抖音都会打开前台 tab，串行入队可避免多个平台同时抢浏览器焦点。YouTube 默认需要交互式确认或显式 `--yes-youtube`；非交互式终端默认跳过，`OPENBILICLAW_NO_YOUTUBE=1` 会强制跳过。CLI 默认复用 6 小时内近期 `bootstrap_profile`，扩展领取任务时会把 pending 标记为 `in_progress`。

回写后的跨源对象会转成普通事件层 payload：小红书 `saved -> favorite`、`liked -> like`、`xhs_history -> view`；抖音 `dy_post -> view`、`dy_collect -> favorite`、`dy_like -> like`、`dy_follow -> follow`；YouTube `yt_history -> view`、`yt_subscriptions -> follow`、`yt_likes -> like`。事件都带 `metadata.source_platform`。任务表保存完整原始结果，但 API 传播前会用 `source_bootstrap_state.json` 跳过跨任务已见 identity key。CLI 只短暂等待任务结果；插件未连接、未登录或页面不暴露对应数据时，初始化继续使用已有数据完成。profile 已经初始化后，后续 bootstrap task-result 新增事件还会转成 `ProfileSignal` 进入 `ProfileUpdatePipeline`，补齐跨源增量画像更新；首次 init 期间仍由汇总事件统一生成画像，避免重复学习。

v0.3.102+：上述四阶段（拉取 + 入库 / 分析偏好 / 生成画像 ‖ 发现补池）抽成共享异步流水线 `cli.run_guided_init`，CLI 与后端 API 复用同一份逻辑——CLI 用单次 `asyncio.run(run_guided_init(...))` 驱动，后端在服务事件循环里直接 `await`，互不嵌套 loop；唯一与路径相关的发现补池步骤以 `discover_backfill` 注入（CLI 一次性引擎 / API 持 `_refresh_lock` 的 `controller.run_init_backfill`）。图形化入口包括插件「推荐」tab、安装包首启 `/setup/` 第 3 步和桌面 Web `/web` 未初始化推荐区，都会渲染前置清单 +「开始初始化」按钮，`POST /api/init`（仅本机）经 `InitCoordinator`（`init_runs` 持久化状态机 + 单写者进度事件 + `BEGIN IMMEDIATE` 单飞 + 崩溃 reconcile + 协作取消）后台跑 wrapper，进度走 `runtime-stream` 的 `init_progress/completed/failed`，`GET /api/init-status` 给权威进度 + 前置检查（B站 / LLM / embedding / 平台）。init 活跃期间写者门控：`background_llm_work_allowed()` 一处暂停所有后台 LLM 循环，画像 / 配置 / 反馈 / 手动 refresh / 探针 / source 配方等 HTTP 写端返回 `409 init_running`，`/api/bilibili/cookie` 静默 no-op、`/api/sources/*/task-result` 放行，init 任务豁免热重载取消。详见 [init 模块文档](modules/init.md)。

### Douyin Direct Discovery

抖音 steady-state 内容发现走 opt-in 路径：`OPENBILICLAW_DOUYIN_COOKIE` 可显式覆盖，默认则复用浏览器扩展同步到 `data/douyin_cookie.json` 的 douyin.com Cookie。后端 `DouyinDirectClient` 仍保留 direct-cookie 诊断能力；公开 discovery 子来源收敛为 `search` / `hot` / `feed`，并优先走 `DouyinPluginSearchClient` 入队 `dy_tasks(type="search"|"hot"|"feed")`。search 让扩展在登录浏览器的后台 tab 中打开搜索页，并在 MAIN world 调用页面 `byted_acrawler.frontierSign()` 签名搜索 API；hot 先取 hot board 的 `sentence_id`，扩展后台打开 `/hot/{sentence_id}`，从跳转后的 `/video/{aweme_id}` 解析 seed，再签名 `/aweme/v1/web/aweme/related/` 拉相关视频；feed 在后台首页签名 `/aweme/v1/web/tab/feed/` 拉首页推荐流。`DouyinDiscoveryService` 是这条链路的复用边界：runtime 正常路径拉 raw candidates 后写入 `discovery_candidates`，再由共享 evaluator 入正式推荐池；调试时也可以由 `openbiliclaw discover-douyin --no-cache --no-evaluate` 直接跑 strategy 预览召回。这样初始化强账号信号与后台补池请求分离，且 search / hot / feed 都能复用真实登录浏览器但不会抢用户焦点。

`openbiliclaw search-douyin` 保留为同一后台插件签名搜索链路的独立 smoke：结果只保存在任务结果里用于诊断，不进入 `content_cache`，也不参与画像重建；正式 runtime discovery 会把这些候选映射为 aweme-like JSON，以 `dy-plugin-search` / `dy-plugin-hot-related` / `dy-plugin-feed` 进入 `discovery_candidates` 待评估池。`discover-douyin --source hot` / `--source feed` 复用同一后台任务桥但没有单独 smoke 命令。

### X (Twitter) Discovery & Capture

X 是第六个内容源，分两条独立通路：

1. **发现（服务端 cookie 重放）** —— 对标抖音 direct，但用 `twitter-cli`（可选 extra `openbiliclaw[x]`，Apache-2.0，自带 `curl_cffi` TLS 指纹）取代 XBogus 签名。浏览器扩展 `cookie-sync.ts` 的 x.com 分支把用户真实 `auth_token` + `ct0` 经 `POST /api/sources/x/cookie` 同步落盘 `data/x_cookie.json`（可被 `OPENBILICLAW_X_COOKIE` 覆盖）。后端 `XDiscoveryProducer` 在 X 平台族低于 quota 且源健康就绪时，按预算调度 `search`（Soul 画像关键词）/ `feed`（推荐流 For-You，最高曝光、压到很低频次并在连续失败后自动暂停）/ `creator`（`x_creator_subscriptions` 账号订阅）三个策略，经 `XClient`（全程只读，lazy import，`enabled=false` 绝不 import）拉推文，`normalize_tweet()` 转成 `source_platform="twitter"` 的 `DiscoveredContent`（`content_type ∈ {tweet, thread}` + `body_text` 全文），enqueue 进统一 `discovery_candidates` 待评估池，由共享混源 evaluator 入正式池。源健康状态机（`storage/x_health.py`）持久化 `ok` / `missing_cookie` / `expired_cookie`(401) / `blocked`(403) / `rate_limited`(429)，按 code 分别退避，经 `GET /api/sources/x/status` 暴露到设置页。

2. **行为采集（扩展 MAIN-world tap）** —— 在用户自己的 x.com 登录态下被动偷听互动 GraphQL mutation：点赞 → `like`、收藏 → `favorite`、回复 → `comment`（三者作强正信号，已在全局正信号集内，零全局改动），转推 → `share`、关注 → `follow`、点开 → `view`（作 context-tier，v1 不进全局正信号集）。事件经 `POST /api/events` 进 Soul 画像，与 discovery 通路完全独立、互不去重。后端 `event_format` 只加 `SOURCE_TWITTER = "twitter"` + `_PLATFORM_LABELS["twitter"]="X"`，不改全局评分集。

### LLM Providers (`llm/`)
- 统一的多模型接口（OpenAI / Claude / Gemini / DeepSeek / Ollama / OpenRouter）
- `codex_auth.py` 提供实验性的 Codex CLI ChatGPT OAuth 凭据导入和刷新；`[llm.openai].auth_mode="codex_oauth"` 时仍注册为 `openai` provider，只替换认证来源，并限制 `base_url` 为 OpenAI 官方 API 域名
- Provider 注册和切换；`LLMRegistry.complete()` 保留默认 fallback 链，`complete_provider()` 用于 per-module override 的精确 provider 调用，不会在指定 provider 错误时静默 spill 到 default
- `LLMService` 通过内置 caller bucket 路由 `[llm.soul]` / `[llm.discovery]` / `[llm.recommendation]` / `[llm.evaluation]`，覆盖 `recommendation.delight_score`、`discovery.evaluate*`、`eval.*`、`sources.xhs.*` 等实际 caller；`model` 覆盖作为 per-call 参数传给 provider，不修改 provider 默认模型
- 结构化输出共享解析：`llm/json_utils.py` 为 discovery eval-batch、recommendation copy、delight scorer、soul awareness/insight/profile/speculator 提供统一 JSON 容错，兼容 MiMo / OpenAI-compatible wrapper、fenced JSON、JSONL、schema echo 和 malformed `{ [ ... ] }`
- v0.3.0+ embedding 兜底：`OllamaProvider.embed()` 走原生 `/api/embeddings`，配 `bge-m3` 模型可在 Mac/Win/Linux CPU 跑相似度计算，不需额外 API Key
- `EmbeddingService` L1 内存 + L2 SQLite 双层缓存；`embedding.provider="ollama"` 且 embedding 凭据为空时直接使用本地 Ollama 默认地址，不再产生向后兼容 warning

### Storage (`storage/`)
- SQLite 数据库管理
- 冷备份、完整性检查与显式修复
- 候选质量信号持久化与数据迁移；`events` 行写入 `inferred_satisfaction` / `satisfaction_reason`，支持 `query_events(satisfaction_modes=...)`
- v0.3.1 `get_pool_candidates` 用 `ROW_NUMBER() OVER (PARTITION BY topic_group)` 把每个 topic_group 在候选窗口里限到 ≤3 条，保证长尾 group 真正进得到候选窗口
- `discovery_candidates` 持久化所有来源 raw candidates 的 lifecycle：`pending_eval`、`evaluating`、`evaluated`、`cached`、`rejected_low_score`、`rejected_duplicate`、`rejected_cache_admission`、`rejected_recently_viewed`、`rejected_franchise_quota`、`failed_eval`。
- `count_pool_available_candidates_by_source()` 与 `count_pool_candidates()` 保持前端可见口径一致；`count_pool_raw_material_by_source()` 统计 fresh / 非 dislike / 未推荐 / 未看过的 `content_cache` raw material，并合并 `discovery_candidates` 中待评估 / 已评估未缓存的 raw material，供 runtime raw ceiling headroom 和 trim 使用。
- `chat_turns` 持久化 side panel durable chat turn，字段包含 `turn_id/session/scope/subject/message/status/reply/error/created_at/updated_at`；`scope` 支持 `chat`、`delight`、`probe` 和 `avoidance_probe`
- `auth_state(key, value)` 单行表持久化局域网密码门禁的撤销纪元 `auth_epoch` 与稳定密码指纹 `password_fingerprint`（非会话表，仅全局计数 + 指纹）；跨进程事务原子自增，验签实时读

## 运行时数据库约束

本地 API 与 CLI 的高频运行路径现在遵循两条约束：

1. **同进程共享单个 SQLite 实例**
   `MemoryManager`、`RecommendationEngine`、`ContentDiscoveryEngine` 会优先复用同一个 `Database`，避免一轮运行里多次 `Database(...).initialize()` 争锁。
2. **启动前先检查、运行中按周期冷备**
   `openbiliclaw start` 会在启动前检查数据库完整性；若健康且超过默认 24 小时未备份，会先生成一份冷备到 `data/backups/`。

数据库修复不在启动路径里自动执行，高风险恢复统一通过 `openbiliclaw db-repair` 触发。

## 对外集成约束

当前 OpenClaw 接入遵循两条边界：

1. **外部集成只通过 adapter 调用内核**
   OpenClaw 不直接访问 SQLite、memory JSON 或内部 engine 组合细节。
2. **skill 只是协议包装，不是业务主链**
   学习、推荐、反馈回流仍由 `runtime/`、`soul/`、`recommendation/` 等模块负责，`integrations/openclaw/skill.py` 只负责对外暴露稳定 handler。
3. **真实 OpenClaw 技能发现走仓库根目录 `skills/`**
   当前仓库通过 `skills/openbiliclaw-adapter/SKILL.md` 提供真实 workspace skill，再由 skill 内部调用 adapter CLI bridge。
