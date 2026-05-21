# 架构设计

## 系统概览

OpenBiliClaw 采用分层架构设计，从上到下依次为：

1. **用户交互层** — Chrome 浏览器插件（B 站 + 小红书 + 抖音 + YouTube 页面行为采集 · 视频停留满意度信号 · 推荐展示 · durable 对话交互 · 后台 LLM 暂停开关 · 配置离线缓存 / 降级修复 UI · xhs/dy/yt 任务调度 / 初始化画像导入 · B 站 / 抖音 Cookie 自动同步）
2. **外部集成层** — OpenClaw adapter / skill wrappers / 本地 API / Codex CLI 凭据导入等对外接入边界
3. **Agent 核心层** — 自研编排器 + Soul Engine + Discovery Engine + Recommendation Engine + Skill System
4. **多源适配层（v0.3.0+）** — `SourceAdapter` 协议下的 B 站 / 小红书 / 抖音 / YouTube / 通用 Web 源
5. **多层网状记忆存储** — Core / Episodic / Semantic / Working Memory（SQLite + 向量索引 + JSON）

详见 [项目 Spec](spec.md) 中的架构图。

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
- `event_filters` / `satisfaction_filter_enabled` — 偏好分析前只丢弃 `negative`（quick_exit / explicit_negative）事件，保留 positive / neutral / unknown 作为上下文
- `negative_exemplars` — 从事件层抽取近期 negative 标题，供 Discovery eval-batch 做负样本锚点
- `InterestSpeculator` — 兴趣推测与投机性发现
- 苏格拉底式用户对话

### Memory System (`memory/`)
- 五层网状记忆管理
- 跨层关联和双向修正
- 自我编辑和遗忘机制

### Content Discovery (`discovery/`)
- 多策略内容发现（B 站 search · trending · related_chain · explore + 小红书 `xiaohongshu` + 抖音 `douyin` + YouTube `yt_search` / `yt_trending` / `yt_channel`），按 `runtime.source_policy` 生成的平台有效配比补池；默认保存的 share 为 B 站 / 小红书 / 抖音 / YouTube = 8 / 1 / 1 / 1，但默认只启用 B 站，关闭的平台不会占候选池 quota。B 站仍在主 refresh 计划内并行 fan-out；XHS / 抖音 / YouTube 低于 quota 时分别交给独立 producer。
- 内容评估（基于用户 Soul，LLM 批量打分）；eval-batch 会读取近期 negative exemplars，按话术 / 商业意图 / 标题结构压低相似候选，评分缓存随最新事件 id 版本化
- 候选分层、去重和缓存写入；写入时 `pool_status='suppressed'` 的旧候选在重新发现时自动复活成 `'fresh'`
- v0.3.0+ 多样性栈：trending 按 rid 交错 / explore 按 domain 交错 / `_compress_topic_repeats` 单次压缩 / `trim_topic_group_overflow` 跨源跨轮配额（任意 topic_group ≤ 池子 10%）/ deficit-source 合并 + 并行 fan-out

### Sources (`sources/`) — 多源适配层 (v0.3.0+)
- `SourceAdapter` Protocol：每个内容源实现统一接口
- `bilibili_adapter` — B 站 API 直连（WBI 签名、v_voucher 自动恢复）
- `xiaohongshu_adapter` — 小红书扩展代理（被动收集 + 关键词搜索 + 创作者订阅 + `bootstrap_profile` 初始化画像任务，零后端爬取；task-result 进入 memory 前按已见 note key 跨任务去重）
- `dy_tasks` — 抖音扩展任务队列（`bootstrap_profile` 初始化画像任务；发布 / 收藏 / 点赞 / 关注信号由扩展以用户浏览器登录态抓取；任务 poll 时标记 `in_progress`，CLI 可复用近期 bootstrap；`search` 任务用于后台插件签名搜索，回传 `dy_search` 候选；`hot` 任务用于后台 `/hot/{sentence_id}` → related API，回传 `dy_hot` 候选；`feed` 任务用于后台首页 `/aweme/v1/web/tab/feed/`，回传 `dy_feed` 候选；三者分别作为 `dy-plugin-search` / `dy-plugin-hot-related` / `dy-plugin-feed` discovery 来源）
- `yt_tasks` — YouTube 扩展任务队列（`bootstrap_profile` 初始化画像任务；观看历史 / 订阅 / 点赞由扩展以用户浏览器登录态读取 DOM 并分批回传；任务 poll 时标记 `in_progress`，CLI 可复用近期 bootstrap）
- `youtube.takeout` — Google Takeout 离线导入解析器，将 YouTube 观看历史 / 订阅 / 点赞转换为统一事件
- `YoutubeDiscoveryProducer` — 后端直连的 YouTube steady-state discovery loop；在 YouTube 平台族低于 quota 时调用 `yt_search` / `yt_trending` / `yt_channel`，并用 SQLite execution ledger 控制每日执行预算
- `web_adapter` — 通用 Web（Playwright CDP + LLM 内容抽取）
- `SourceRecipe` — 源任务持久化与分发

### Recommendation Engine (`recommendation/`)
- 推荐排序与朋友式推荐表达生成；统一从候选池读取
- `PoolCurator` 五维评分（relevance · freshness · topic_fatigue · source_monotony · serendipity）
- v0.3.1 双轴 fatigue：`recent_topic_keys` (细) + `recent_topic_groups` (粗) 取 max；曲线 `count^1.5/len*5`，count=2 即触发 0.47 强抑制
- `_merge_topic_supergroups` — serve 时基于 embedding 把 `动漫杂谈/补番/解说` 等近义 topic 合并为同一聚类
- `prewarm_supergroup_embeddings` — refresh tick 后台预热所有池中 topic_group embedding，让 reshuffle 跑全 cache hit
- `batch_insert_recommendations` — 单 transaction 批量插入，避免 popup 给 10 条结果时 10 次 fsync
- 个性化专题生成

### Runtime (`runtime/`)
- 系统生命周期管理和服务编排
- 降级模式启动：生产 `create_app()` 遇到 LLM registry 配置错误时保留 `/api/health`、`/api/config`、`/api/runtime-status` 和 `/api/runtime-stream`，让 popup 设置页仍能保存修复配置；其他 API 返回 503，避免半初始化 runtime 继续跑推荐/发现链路
- 配置热重载：`RuntimeContext` 重建 registry / service / engine 时会从 `[llm.soul]` / `[llm.discovery]` / `[llm.recommendation]` / `[llm.evaluation]` 注入同一份 module override；热重载后的 speculator tick 作为 detached task 注册到 `BackgroundTaskRegistry`，不阻塞 `/api/config` 响应
- `AutoUpdateService` — 后端自动更新只查询 GitHub `/tags` 并过滤 `backend-v*`（兼容 legacy `v*` / 裸 semver），明确忽略 `extension-v*`；当前 GitHub Releases 由扩展 artifact 占用，不能用 `/releases/latest` 判断后端源码是否最新
- `ContinuousRefreshController` — 后台定时刷新候选池；按平台族配额评估 deficit，B 站缺货合并到一次 discover() 并行 fan-out，小红书缺口交给 xhs producer / 扩展任务链；抖音缺口交给 runtime `DouyinDiscoveryProducer`，通过 `DouyinDiscoveryService(cache=True)` 复用 search / hot / feed 后台插件签名链路补池；YouTube 缺口交给 `YoutubeDiscoveryProducer` 后端直连补池，主 refresh replenishment plan 不再 inline 调度 `yt_*`
- `background_llm_work_allowed()` — 共享 gate predicate；`scheduler.enabled=false` 会暂停 daemon-owned 后台 LLM / embedding 工作，`scheduler.pause_on_extension_disconnect=true` 时还要求浏览器插件 presence 在线或仍处于断开宽限窗口。该 gate 覆盖 refresh、pool precompute、soul pipeline、xhs/dy/youtube producer、proactive push、低频 account sync、startup one-shot 和 OpenClaw direct bootstrap
- `_enforce_pool_cap` 每 tick 跑 `trim_topic_group_overflow` + under-quota suppressed 候选复活 + 必要时按 share quotas 修剪过额源
- `AccountSyncService` — 历史记录、收藏夹、关注列表同步；使用历史游标 + 已见 bvid/mid 集合只把新增账号信号送进画像分析；首次成功写入账号行为并完成 preference 分析后，若 soul 画像为空，会在同一进程生命周期内最多一次触发 `build_initial_profile([])` 自动 bootstrap
- `/api/sources/{xhs,dy,yt}/task-result` — 插件 bootstrap partial / final 结果完整保留在任务表；传播到 memory / profile pipeline 前读取 `source_bootstrap_state.json`，跳过跨任务已见 note/video/item key，避免旧收藏 / 历史再次触发画像更新
- `runtime-stream` — 浏览器扩展 background 以 `client=background` 连接后，若后端本地没有 B 站 Cookie，会推送 `bilibili_cookie_sync_requested`，扩展立即通过 `/api/bilibili/cookie` 回传当前浏览器 Cookie；后端持久化 Cookie、热重载 runtime 组件，并重新启动 refresh / account sync / auto update 后台任务，避免热重载取消后台循环后小红书 / 抖音 producer 停止；重复同步相同 Cookie 时不再重建 runtime，避免打断正在等待扩展回写的抖音 discovery。若 `[sources.douyin].enabled=true` 且后端没有环境变量或 `data/douyin_cookie.json`，会推送 `douyin_cookie_sync_requested` 并通过 `/api/sources/dy/cookie` 回传抖音 Cookie。后续推荐、惊喜和画像更新仍复用同一条 WebSocket 事件流；同一连接也驱动 `PresenceTracker`，服务端 reader 会 `receive()` 检测 idle disconnect，避免浏览器断开后 presence 卡住
- `/api/image-proxy` — 移动 Web 和扩展 side panel 的推荐、惊喜和消息封面图统一走 `UI -> /api/image-proxy -> 白名单 CDN -> bounded spool -> UI`，后端在发送响应前完成 URL、redirect、Content-Type 和 10MB 实际字节校验

### Side Panel Durable Chat

插件聊天不再把主状态只放在 DOM / JS 内存里。`popup/` 对主聊天、惊喜推荐内聊和兴趣猜测内聊统一调用 `/api/chat/turns`：

1. popup 生成 `turn_id` 并 POST 消息、`scope`（`chat` / `delight` / `probe`）和可选的内容上下文。
2. 后端先把 turn 写入 SQLite `chat_turns(status='pending')`，随后用后台任务调用 Dialogue 引擎生成回复。
3. popup 通过 `/api/chat/turns/{turn_id}` 轮询，并在初始化时按 `session/scope` 重新 hydrate 历史。

这条数据流让 Chrome 在切 tab、reload 或内存压力下丢弃不可见 side panel 后，仍能恢复 pending thinking 占位、完成回复或失败状态。完成后的 delight/probe scope 会继续发布对应 cognition/runtime 事件，主聊天仍按原有受控学习链路进入画像更新。

### Init 多源画像导入

`openbiliclaw init` 的首轮信号现在由四条路径合流：

1. B 站 API 直连拉取观看历史、收藏夹和关注列表。
2. 后端在 `xhs_tasks` 表入队 `bootstrap_profile`，并在 `init --yes-xhs` / `fetch-xhs` 默认复用 6 小时内已有 bootstrap 任务，避免重复打开前台小红书 tab。浏览器插件轮询 `/api/sources/xhs/next-task` 时，后端会先把任务原子标记为 `in_progress` 并写入 `claimed_at`；15 分钟无回写才允许重新领取。插件在用户已登录的小红书页面中先打开 `/explore` 定位当前用户 profile。滚动任务会以前台 tab 触发页面内“我”入口的 anchor click，background 只等待同一 tab 完成导航；只有找不到可点击入口时才回退到直接导航。到 profile 后，插件解析 profile state / DOM 中的 `saved / liked` notes 和页面显式暴露的 `xhs_history` notes，回写 `/api/sources/xhs/task-result`。当任务显式传入 `max_scroll_rounds` 时，插件会在 profile tab 内优先探测 feed / waterfall / masonry 滚动容器做有限滚动，并先用 `status="partial"` 分批回传新增 notes，最终再用 `status="ok"` 完成任务；`scroll_wait_ms` 和 `max_stagnant_scroll_rounds` 也由任务 payload 控制，并由插件端裁剪到安全范围。
3. 后端在 `dy_tasks` 表入队 `bootstrap_profile`，由浏览器插件在用户已登录的抖音页面中依次访问发布 / 收藏 / 点赞 / 关注 scope。content script 结合 DOM 解析、MAIN-world fetch tap 和 API harvester 采集条目，按 scope 以 `status="partial"` 分批回写 `/api/sources/dy/task-result`，最终以 `ok` 完成任务。Douyin 默认需要显式 `--yes-douyin` 才进入 init；非交互式终端默认跳过，避免盲目触发风控或空 200 响应。CLI 默认复用 6 小时内近期 `bootstrap_profile`，扩展领取任务时会把 pending 标记为 `in_progress`。
4. 后端在抖音任务完成后再在 `yt_tasks` 表入队 `bootstrap_profile`，由浏览器插件在用户已登录的 YouTube 页面中依次访问 `/feed/history`、`/feed/channels`、`/playlist?list=LL`。YouTube 与抖音都会打开前台 tab，串行入队可避免多个平台同时抢浏览器焦点。YouTube 默认需要交互式确认或显式 `--yes-youtube`；非交互式终端默认跳过，`OPENBILICLAW_NO_YOUTUBE=1` 会强制跳过。CLI 默认复用 6 小时内近期 `bootstrap_profile`，扩展领取任务时会把 pending 标记为 `in_progress`。

回写后的跨源对象会转成普通事件层 payload：小红书 `saved -> favorite`、`liked -> like`、`xhs_history -> view`；抖音 `dy_post -> view`、`dy_collect -> favorite`、`dy_like -> like`、`dy_follow -> follow`；YouTube `yt_history -> view`、`yt_subscriptions -> follow`、`yt_likes -> like`。事件都带 `metadata.source_platform`。任务表保存完整原始结果，但 API 传播前会用 `source_bootstrap_state.json` 跳过跨任务已见 identity key。CLI 只短暂等待任务结果；插件未连接、未登录或页面不暴露对应数据时，初始化继续使用已有数据完成。profile 已经初始化后，后续 bootstrap task-result 新增事件还会转成 `ProfileSignal` 进入 `ProfileUpdatePipeline`，补齐跨源增量画像更新；首次 init 期间仍由 CLI 汇总事件统一生成画像，避免重复学习。

### Douyin Direct Discovery

抖音 steady-state 内容发现走 opt-in 路径：`OPENBILICLAW_DOUYIN_COOKIE` 可显式覆盖，默认则复用浏览器扩展同步到 `data/douyin_cookie.json` 的 douyin.com Cookie。后端 `DouyinDirectClient` 仍保留 direct-cookie 诊断能力；公开 discovery 子来源收敛为 `search` / `hot` / `feed`，并优先走 `DouyinPluginSearchClient` 入队 `dy_tasks(type="search"|"hot"|"feed")`。search 让扩展在登录浏览器的后台 tab 中打开搜索页，并在 MAIN world 调用页面 `byted_acrawler.frontierSign()` 签名搜索 API；hot 先取 hot board 的 `sentence_id`，扩展后台打开 `/hot/{sentence_id}`，从跳转后的 `/video/{aweme_id}` 解析 seed，再签名 `/aweme/v1/web/aweme/related/` 拉相关视频；feed 在后台首页签名 `/aweme/v1/web/tab/feed/` 拉首页推荐流。`DouyinDiscoveryService` 是这条链路的复用边界：默认把 `DouyinDirectStrategy` 注册到现有 `ContentDiscoveryEngine`，让候选继续走统一评估、去重、缓存和推荐；调试时也可以由 `openbiliclaw discover-douyin --no-cache --no-evaluate` 直接跑 strategy 预览召回。这样初始化强账号信号与后台补池请求分离，且 search / hot / feed 都能复用真实登录浏览器但不会抢用户焦点。

`openbiliclaw search-douyin` 保留为同一后台插件签名搜索链路的独立 smoke：结果只保存在任务结果里用于诊断，不进入 `content_cache`，也不参与画像重建；正式 `discover-douyin --source search` / `discover --source douyin` 会把这些候选映射为 aweme-like JSON，以 `dy-plugin-search` 进入 discovery pool。`discover-douyin --source hot` / `--source feed` 复用同一后台任务桥但没有单独 smoke 命令，候选分别以 `dy-plugin-hot-related` / `dy-plugin-feed` 进入 discovery pool。

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
- `chat_turns` 持久化 side panel durable chat turn，字段包含 `turn_id/session/scope/subject/message/status/reply/error/created_at/updated_at`

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
