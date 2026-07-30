# 架构设计

## 系统概览

OpenBiliClaw 采用分层架构设计，从上到下依次为：

```text
interactive (dialogue / config probe) ──────────────┐
                                                    ├─ runtime total gate (default 4) ─ ordered instance chain ─ adapter
background ─ background admission (default 3) ──────┘
             ├─ refill: expression > evaluation > supply
             │  ├─ supply includes explore queries / source extraction while low
             │  └─ while queued: guarantee 2, may borrow all 3
             │     expression owner: 8 immediate / 3s fixed tail / 60 drain / 30×2 provider
             └─ maintenance: at most 1 while refill waits;
                parked when canonical available = 0

guided init: signals → preferences → full profile commit
                                  → discovery → evaluation → copy → canonical pool ready
                                  → terminal → runtime schedules optional probes

config UI draft → /api/config/discover-models → exact instance GET /models
                → editable model list + local effort advisory (no config write)

reshuffle HTTP → PoolServeSnapshot → serve DB worker / isolated read connection
               → unchanged MMR selector → isolated short recommendation+shown transaction
  optional source_platform (PC Web tabs only, additive):
    canonical platform → platform-scoped candidate rows, no cross-platform floor
                       → same curator / MMR / diversity / persistence path
platform-availability HTTP → isolated read snapshot of the canonical available set
                           → {total_available, by_platform}, total == sum(by_platform)
background refresh → maintenance DB worker / isolated connection
                   → ≤50 mutations per transaction → commit/yield/retry next batch
```

1. **用户交互层** — Chrome 浏览器插件（B 站 + 小红书 + 抖音 + YouTube + X (Twitter) + 知乎通过统一 `PlatformAdapter` 做页面行为采集，Reddit 通过 rdt-cli 做默认 discovery、插件保留 bootstrap 初始化信号和命令后端 fallback 登录态任务源，click 在 capture 阶段记录、scroll 覆盖内部 feed 容器 · 视频停留满意度信号 · 推荐展示与真实可换库存状态 · 文字卡（推文 / thread / 知乎回答 / Reddit 帖子）· 正向兴趣 / 避雷探针确认 · durable 对话交互 · 后台 LLM 暂停开关 · 开机自启动开关 · 配置离线缓存 / 降级修复 UI · bili/xhs/dy/yt/zhihu/reddit 任务调度 / 初始化画像导入 / 多路 discovery · B 站 / 抖音 / X Cookie 自动同步 · 本机扩展驱动 E2E 捕捉自检）+ 移动 Web（`/m`）+ 桌面 Web（`/web`）。所有 `/api/*` 前置一道**可选密码门禁**（HTTP 中间件，见下方「API Auth Gateway」）：本机 / 扩展默认免登录，局域网 / 远程设备需密码。
2. **外部集成层** — OpenClaw adapter / skill wrappers / 本地 API / Codex CLI 凭据导入等对外接入边界
3. **Agent 核心层** — 自研编排器 + Soul Engine + Discovery Engine + Recommendation Engine + Skill System
4. **LLM 实例路由层** — `config / Web UI -> [llm.instances.<id>] -> 全局或分模块有序实例链 -> LLMRegistry -> Provider adapter`。实例 ID 是路由、健康与 cooldown 身份，adapter 类型只是协议实现，因此同类型的多个 Base URL / token / model 可以同时存在。模块默认继承全局链；自定义链只在链内降级，耗尽后不越界。配置界面的另一条只读支路是 `draft -> /api/config/discover-models -> exact instance GET /models`，只返回模型 ID 与本地 Effort 建议，不写盘、不改链。
5. **多源适配层（v0.3.0+）** — `SourceAdapter` 协议下的 B 站 / 小红书 / 抖音 / YouTube / X (Twitter) / 知乎 / Reddit / Bangumi / 通用 Web 源；`sources.platforms` 注册表统一八个平台族的别名、strategy 与 URL host 身份。Bangumi 默认使用官方匿名只读 API，可选个人令牌（Bearer）读取私密收藏、令牌失效自动降级匿名；扩展仅在 `bgm.tv` / `bangumi.tv` 上提供账号身份自动识别（非任务桥、无行为采集）。
6. **保存同步编排层（API/runtime + B 站 adapter + 三个图形化保存界面 + CLI 配置可见）** — canonical saved identity + normalized membership / native state + `/api/saved/*` + capability router + local-first `SavedSyncService` + `BilibiliNativeSaveAdapter`；六平台扩展保存 adapter 已按能力/目标矩阵注册，经稳定的 `ExtensionNativeSaveBroker` 入队，完整 broker flow 为 `extension_native_save_jobs -> /api/sources/<slug>/next-task -> installed extension`（具体 source 前缀为 `/api/sources/{xhs,dy,yt,x,zhihu,reddit}`），再由 authenticated `task-result` 回传安全状态。trusted-local `/api/extension/e2e/run` 的 dedicated native-save 模式只接受与 generic actions 互斥的 exact authorization，提交一个 canonical item 到同一 saved-sync/broker flow，并只回传六字段结果；通用 DOM runner 永不执行 favorite/bookmark。历史 `unsupported_adapter_missing` 行可重新同步，但真正的 `unsupported_content_type` 保持终态。YouTube favorite 与知乎 favorite 使用 exact `OpenBiliClaw`，YouTube watch-later 使用 `YouTube Watch Later`，其余平台回退原生收藏/书签/Saved；Bilibili favorite/watch-later 使用 direct adapter。2026-07-14 已在自动同步关闭、手动同步触发下完成七平台两类动作真实账号验证，终态均为 `synced/already_synced`；插件、移动 Web 与桌面 Web 共享 `item_key`，以 bounded request、retained list、per-key mutation fence、reload task recovery / item ownership 和 visibility-aware durable tracker 呈现同步状态；CLI 只通过 `config-show` 展示默认关闭的自动同步配置，不提供保存 / 同步动作命令
7. **多层网状记忆存储** — Core / Episodic / Semantic / Working Memory（SQLite + 向量索引 + JSON）

海外出口另有一条显式路由边界：`config / Web UI -> [network].mode -> openbiliclaw.network -> 每个 LLM 实例 endpoint / YouTube / X twitter-cli / Reddit rdt-cli·OpenCLI / Bangumi / updater / Codex OAuth`。默认 `system` 继承环境 / OS 代理（CLI 会收到物化后的代理环境变量；海外服务在国内直连必然超时，而这是开箱默认值；没配代理时等价于直连），`direct` 对 SDK 注入 `trust_env=False` 并从 CLI 环境剥离代理变量，`custom` 注入指定 URL；LLM 链中每个实例按自己的 Base URL 独立裁决国内直连或海外代理。X / Reddit 的浏览器扩展 fallback 仍跟随浏览器网络设置。B站 / 抖音 / Ollama / 国内 CDN 客户端不读取该边界。

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

### Saved Sync (`saved_sync/`)
- `NativeSaveRouter` 根据 adapter capability 确定 favorite / watch-later 路由；watch-later 仅在平台不支持原生动作且支持 favorite 时回退
- `SavedSyncService` 在任何平台 I/O 前提交本地 membership；每次自动 / 手动触发都在独立 `native_save_tasks` / `native_save_task_items` ledger 留下 durable UUID 快照，再对其中 live 项执行同步
- `ExtensionNativeSaveBroker` 已提供六个非 B 站平台的 sanitized job foundation：canonical item/route 经 allow-listed default-port HTTPS URL 清洗后进入独立 `extension_native_save_jobs`，默认剥离 query；YouTube 只保留唯一非空 `v`，小红书带 query 时必须保留唯一非空 `xsec_token`、可选唯一非空 `xsec_source`；authority 规范为无默认端口、无尾点 hostname。active row 用独立短连接事务原子复用；broker poll、lease 检查、native task/item heartbeat 与 terminal persistence 同样使用线程卸载的独立短连接并有界重试 SQLite lock，durable terminal state 在完成竞态中优先。pending dispatch 超时持久化 `extension_required`，claimed lease 超时固定失败且不重放。FastAPI exact source endpoints 先查 broker，再保留原 discovery/bootstrap queue；owned result 不会 fall through。扩展侧已有 `NATIVE_SAVE_EXECUTE` / `NATIVE_SAVE_RESULT` 共享 contract、256 项 recent outcome replay cache 与 active-tab task runner；一般 runner 与 legacy dispatcher 共用 global mutex 保护 tab 创建/加载，加载完成即释放；XHS 手动 native-save 因 exact tokenized route + identity/control fence 可越过后台 discovery mutex，且 alarm/runtime wake poll single-flight。六个平台 executor 已接入各自 source dispatcher；所有领取入口先等待共享 MV3 recovery barrier，用只含所有 runner-owned tab ID 的可选 session record 定点恢复 orphan。YouTube duplicate exact playlist 优先 checked proof，否则稳定复用一个；知乎适配 current `Favlists-item` 并把 exact content control、新打开 dialog 与 `OpenBiliClaw` row 绑定同一最近 identity fence；小红书适配 current `noteContainer/collect-wrapper`。2026-07-14 六个平台 favorite + watch-later/fallback 真实账号终态均为 `synced/already_synced`
- 同平台逐项串行、不同平台组可并行；路由缺失写 `unsupported/unsupported_adapter_missing` 并可在 adapter 到位后重试，平台返回的 `unsupported_content_type` 仍是 local-only 终态；adapter 异常写安全的 `failed`，均不回滚本地保存
- `BilibiliNativeSaveAdapter` 是首个生产 adapter：favorite 精确复用/创建 `OpenBiliClaw`（仅同一个 client 实例/title 在锁内重查并单飞，不覆盖跨 client/process），watch-later 写 B 站稍后再看；BV → aid 先走 application-aware GET 并要求非 bool 正整数，`BilibiliAPIClient` 在任何请求前校验 `SESSDATA + bili_jct`；GET/POST HTTP 412/429 共用脱敏映射，favorite duplicate 由 resource-deal 专项异常标记而非 adapter action 猜测
- `/api/saved/{list_kind}` 提供严格 canonical save/list/remove/status/sync，`/api/saved-sync/tasks/{uuid}` 从 task ledger 轮询逐项结果；零项已知任务返回 200、未知 UUID 返回 404，缺失 membership 固定返回 `failed/not_saved_locally`，旧 B 站端点只做 local-only 兼容
- `RuntimeContext` 在 B 站 client 热重载时先取消 registry inflight，再原子重建 router/service；registry 只拥有顶层 sync runner。六平台 broker job 若仍为 pending，取消会安全写成 `cancelled`；若扩展已 claim 为 `in_progress`，broker 会继续等待 durable 终态并把所有权交给 service-owned watchdog，使 240 秒 service deadline、360 秒扩展执行 lease 和热重载都不会把同一次平台写入误记为 `interrupted` 或触发重放。插件 side panel、桌面 Web、移动 Web 和 CLI 配置输出已经接入同一默认关闭配置与状态契约
- 六平台 production adapter、runtime broker 与 extension executor 已 6/6 接线；三个图形界面只解释后端 `sync_status/sync_task_id/resolved_target/error_code`：`unsupported_content_type` local-only，`unsupported_adapter_missing` 可滚动升级重试，`pending + 非空 sync_task_id` / `syncing` 禁止重复提交。真实登录态平台写入仍必须逐平台显式授权，fixture 不能替代授权 E2E

### User Soul Engine (`soul/`)
- 行为数据分析和画像构建
- 五层灵魂模型（事件→偏好→觉察→洞察→灵魂）
- 分类词表（`taxonomy.py`）：偏好层一级分类收敛到固定 `CATEGORY_VOCAB`，`PreferenceAnalyzer` 在写入前用精确命中 / embedding 最近邻 /「其他」兜底解析，避免自由文本分类污染长期画像。
- 分类迁移与画像整理：`CategoryMigrator` 通过 `profile-consolidate --migrate-categories` 把存量自由分类迁到固定词表；`ProfileConsolidator` 的 12h 整理流程按 `(name, category)` 处理同名异义主题，支持 LLM 用 `{name, category}` 精确引用成员。
- 用户画像覆盖层（`overrides.py`）：用户手动编辑存独立 `profile_overrides.json`，在读收口 `get_profile()` 与镜像收口 `sync_profile_files()` 叠加到 AI 画像之上（有效画像 = AI ⊕ 覆盖），画像重建不覆盖用户编辑；删 / 拉黑经有效 dislikes 影响 discovery / recommendation / delight 硬过滤（Phase 1 后端；编辑 UI 见 Phase 2/3）
- `event_filters` / `satisfaction_filter_enabled` — 偏好分析前只丢弃 `negative`（quick_exit / explicit_negative）事件，保留 positive / neutral / unknown 作为上下文
- `negative_exemplars` — 从事件层抽取近期 negative 标题，供 Discovery eval-batch 做负样本锚点
- `/api/events` — 浏览器插件统一行为入口；批次内逐条写入，raw `dislike` 规范为 `feedback`，未知事件进入响应 `rejected` 明细而不是让整批 500，避免插件重试造成已写入事件重复。若 soul 画像明确未初始化，普通行为事件返回 `not_initialized` 拒收且不写 memory；首轮画像信号只由点击「开始初始化」后的 guided init 来源任务拉取。profile ready 后，accepted 事件会在落 memory 后通过 `signals_from_events()` 进入 `ProfileUpdatePipeline.ingest_batch()`，并会先用 `last_profile_pipeline_event_id` 补喂旧 discovery-pending 事件，再通过 `request_replenishment(reason="event_ingest")` 排队补货需求；`pending_signal_events` 只是 discovery refresh 水位，不代表画像待处理队列。
- `/api/feedback` — 推荐卡主动反馈入口；桌面 Web 的 `like/dislike/dismiss` 先经过客户端 10 秒 pending-action 屏障，撤销时不会发出写请求，倒计时结束或 `pagehide` keepalive flush 后才进入 API；失败时客户端回滚。API 写 recommendation 反馈字段和 memory `feedback` 事件后，不再每条反馈直接启动画像重分析，而是交给 runtime `FeedbackBatchScheduler` 做短窗口合并，再由 `SoulEngine.process_feedback_batch_if_needed()` 单飞读取反馈游标。评论和探针聊天不走客户端屏障；进入 LLM 偏好分析前会剥离插件原始大字段，只保留偏好相关 metadata。
- `InterestSpeculator` — 兴趣推测与投机性发现
- `AvoidanceSpeculator` — 不喜欢领域探针；未确认前只展示给用户确认，不进入推荐过滤，确认后通过共享 dislike writeback 写入 `disliked_topics` 并清理候选池
- 苏格拉底式用户对话；回复后的用户主动学习链用 task-local bypass 跳过 background admission（仍经过 total gate），所以空库存也能学习。若真正新增长期避雷项，偏好落盘即启动共享 dislike writeback：精确清池先执行，语义精判与完整画像重建并行，把匹配候选标成 `purged_by_dislike`，不阻塞回复

对话链路的失败边界是端到端一致的：

```text
Web / CLI / OpenClaw
        │ dialogue request
        ▼
SocraticDialogue ── success ──> user+agent history ──> background learning
                                      (bypass background admission; keep total gate)
                                                       └─ new dislike ──> shared pool purge
        │
        └─ failure/timeout ──> rollback provisional history
                              └─> boundary-safe error / failed durable turn
```

Web durable turn 只在成功回复后记录认知并发布成功事件；失败行的 `reply` 为空、`error` 为安全分类文案。桌面 Web 首屏的推荐读取、runtime 读取与 health/profile/activity/config 等次级 hydration 保持三个独立分支，任一慢请求不阻塞其余分支渲染。

### Memory System (`memory/`)
- 五层网状记忆管理
- 跨层关联和双向修正
- 自我编辑和遗忘机制
- `view` 事件在与事件行相同的 SQLite 事务内 upsert canonical `seen_items(source_platform:content_id)`；旧库按游标回填全部历史 view，不再用“最近 2000 条”扫描充当推荐去重。`reshuffle` 只记录一次强度 `0.1`、satisfaction-neutral 的批次导航事实，不把当前十张卡伪装成十条负反馈。

### Content Discovery (`discovery/`)
- 多策略内容发现（B 站 search · trending · related_chain · explore + 小红书 `xiaohongshu` + 抖音 `douyin` + YouTube `yt_search` / `yt_trending` / `yt_channel` + X (Twitter) `search` / `feed`(For-You) / `creator`(账号订阅) + 知乎 `search` / `hot` / `feed` / `creator` / `related` 插件任务 + Reddit `search` / `hot` / `subreddit` / `related` rdt-cli 默认命令后端 / 插件 fallback + Bangumi `search` / `ranked` / `latest` 官方匿名 API），按 `runtime.source_policy` 生成的平台有效配比补池；默认保存的 share 为 B 站 / 小红书 / 抖音 / YouTube / X / 知乎 / Reddit / Bangumi = 5 / 1 / 1 / 1 / 1 / 1 / 1 / 1，但默认只启用 B 站，关闭的平台不会占候选池 quota。B 站仍在主 refresh 计划内并行 fan-out；当 B 站 API search 处于冷却且扩展在线时，`BilibiliExtensionSearchProducer` 会作为兜底入队 `bili_tasks` 搜索任务；XHS / 抖音 / YouTube / X / 知乎 / Reddit / Bangumi 低于可换 quota 时分别交给独立 producer；补货请求还会受 raw-material ceiling headroom 约束，避免不可服务库存已满时继续消耗 LLM / discovery。Bangumi producer 使用逐日条目预算、类型 cursor、最小间隔与持久化 `Retry-After` cooldown，只 enqueue raw candidate。统一 `KeywordPlanner` 是生成侧：它只写 `discovery_keywords` query cache，不抓内容；当 `explore_refresh_hours` 到期 / 即将到期且 B 站有补货空间时，会在已有 merged keyword 调用中追加 `explore_domains`，把返回的探索 query 写入 `keyword_kind="explore"` 的 B 站关键词池，成功插入后推进 `last_explore_refresh_at`，后续由 `ExploreStrategy` claim / fetch / candidate pipeline 评估；普通 B 站与 Bangumi search 只 claim `keyword_kind="regular"`。
- Query inspiration cache 是关键词生成侧的可选基础设施：`[discovery].inspiration_search_enabled=true` 时，`KeywordPlanner` 会先读取 keyword / pool coverage snapshot，并统一归一化兴趣标签 join；随后从 like 二级兴趣中按覆盖缺口抽样，调用 `discovery.keyword_brainstorm` 生成带 `kind_fit` 的搜索 probe branch（解析失败时由 `discovery.keyword_brainstorm.repair` 修成标准 branch），再通过 search provider 链（默认已启用平台源 → Exa → You.com free MCP，由 `[discovery].inspiration_search_backends` 控制）grounding 具体实体 / 社区词 / 讨论点。grounding 有 stage 级搜索预算、平台源扇出预算、每 probe 页数预算和 B 站 / 抖音 / X 等风险源预算；regular + explore 同轮触发时共享一次 brainstorm / grounding stage，再按 kind 分流给 curator。`platform_sources` 只复用已启用同步 / bridge 来源（B站 / YouTube / X / Reddit / Bangumi；抖音 direct client；小红书 / 知乎 bridge 可用时）的搜索结果作为灵感 evidence，不写 `discovery_candidates` 或推荐池；Bangumi grounding 复用同一个匿名只读 client 与请求节流，返回 Subject 标题 / URL / 摘要。随后经 `discovery.keyword_inspiration` 做 Profile Curator / Detail Expander，并优先产出按平台 keyed 的 `platform_keywords`，再把 `inspiration_id -> expansion_id -> platform keyword` 溯源链写入 storage；curator 输入会复用旧 merged keyword planner 的平台供给优势，并附带每个平台的 query_style / recent / avoid / prefer / supply_hint 回压信号、选中二级兴趣、brainstorm 分支、搜索 grounding 记录和 coverage constraints。系统侧会过滤原样证据标题、URL、过长 query、明显平台语言不匹配和平台检索语法不匹配的词，用 grounding hint 校正疑似挂错的 `source_interest`，并为未覆盖兴趣保留 slot 后触发 bounded repair；repair 仍缺词时用 deterministic platform-native backfill 按平台模板补齐，保证 inspiration-only 模式仍按平台原生搜索风格产词，且不会让高频兴趣或单一 lens 吃完整批。admission 后的 keyword yield 会回填到 inspiration / expansion 计数。默认关闭以避免默认增加搜索 / LLM 成本；实验开关 `inspiration_replace_merged_keywords=true` 会让 due 平台跳过旧 merged keyword planner，只通过 inspiration flow 填充各平台 `regular` 关键词池，并在 B 站 explore 到期时额外填充 `keyword_kind="explore"` 的探索词池；开 replace 前由 `keyword-inspiration-report` 按 cohort 门禁判定。
- 轴库学习闭环 + 编排抽取（Phase 2，`runtime/inspiration_pipeline.py::InspirationKeywordPipeline`）：上述 ①–⑥ inspiration 编排从 `KeywordPlanner` god-file 抽成独立 pipeline（行为逐字不变，planner 保留四个签名不变的兼容委托 + 一个 `host` 反向引用共享 `_history`/`_insert`/`_avoid_hints`/`_supply_hints`/`_load_profile`）。轴库从"能复用"升级为"会学习"：production stage 在取轴前先跑一次纯 SQL 的 `backfill_inspiration_axis_yield()`（trailing-window 全量重算 / 幂等 / Laplace 平滑）+ `apply_inspiration_axis_lifecycle()`（active→stale/retired→90 天 purge），6 小时节流、preview 永不触发；排序有效分改为条件式 prior 地板（只保护从未消费过的轴，坏轴按真实分下沉）。config 收敛：13 个 `inspiration_*` 旋钮压到 4 个（enabled / replace / backends / `inspiration_breadth` 档位），其余由档位派生成内部常量，删除键经 diagnostics 通道给出移除提示。可选 embedding 近邻轴合并在 pipeline 层（async）解析"新轴→应并入的既有 axis_id"（cosine≥0.92）后交给同步零 I/O 的 `upsert_inspiration_axes()`，服务不可用 / 超时无损降级回字符串行为并标 `axis_embedding_degraded`。Phase 2.3 起，B 站**跨域 explore 通道也走这条 pipeline**（默认开 coexist）：以 merged call 现成的 `explore_domains` 为种子跑 `_run_explore_inspiration_stage`，产 `source='explore'` 的轴 + `keyword_kind='explore'` 词，复用 Phase 2 按 `axis_id` 的 yield 回填 + `list_inspiration_axes_by_source('explore')` 构成舒适区扩张闭环；富生成 degraded 时无损降级回旧 `_explore_domain_queries` 拍平（explore 池不裸奔），到期轮仅多一次 explore 富生成调用，regular 通道不变，`replace` 模式 explore 路径不变。
- `DiscoveredContent` 全形态：`body_text` 支持推文 / thread / 知乎回答摘要全文 / Reddit selftext 或评论正文 / Bangumi 条目简介，`content_type` 支持 `video/note/tweet/thread/answer/article/question/post/comment/subject`，让文字和目录型来源正确流过统一待评估池并渲染对应卡片。新增通用目录指标 `rating_score/rating_count/source_rank`，与其它元数据贯穿待评估池、正式缓存、推荐/惊喜 API 和三端卡片；评分不冒充 like/comment。
- 统一发布时间契约：Bilibili、小红书、抖音、YouTube、X、知乎、Reddit 和 Bangumi 的当前来源 payload 只在存在语义明确字段时生成 `published_at`（UTC RFC 3339）或 `published_label`（清洗后的来源相对文本）。字段与时长/互动元数据一起走 `source normalizer -> DiscoveredContent -> discovery_candidates -> content_cache -> recommendation/delight API`；缺失值不阻断候选，重新发现的空值不覆盖已有非空值，旧缓存不联网回填，也不从 `discovered_at`、任务时间、互动时间或推荐时间猜测。
- 统一待评估池：`source adapters -> discovery_candidates -> tokenized claim -> 最多 3 个 LLM-only worker -> 串行 commit/admission -> content_cache -> expression copy -> servable pool`。API daemon 任一 30 条 worker 完成即补位，总在途不超过 90；串行 lane 先持久化全部 token-owned 评分，再按 `target - available - admitted_pending_copy` admission，超额结果保留为 `evaluated`。OpenClaw one-shot 不启动这些 daemon owner：`recommend(refresh_if_needed=True)` 的首轮 source supply 与 inline claim 固定 ≤4（fetch oversample=1、min eval batch=4、inline evaluator=1），随后请求再补下一批，并在 admission commit 后 await ≤4 durable expression copy、禁用本次 split retry；首 batch 的有效 subset 立即成为 canonical pool，未完成行保持 pending，不会留下 notify-only coordinator 或 detached provider task。projected 只计 `available + admitted_pending_copy + evaluated_pending_admission`，不计 raw pending/evaluating；完成 / 释放匹配 `id + status + claim_token`，60 秒只作 API safety wake。
- 候选分层、去重和缓存写入：`discovery.admission` 定义贯穿候选评估、缓存写入与数据库展示的唯一准入策略——非 `explore` 至少使用全局门槛，精确 `explore` 唯一使用 `0.58`。达标候选通过 `cache_evaluated_results()` admission 到正式推荐池 `content_cache`，`_cache_results()` 写前再次 fail closed，数据库取池 / 回填 / delight 等出口再执行同一来源感知条件；写入时 `pool_status='suppressed'` 的旧候选只有在新分数达标时自动复活成 `'fresh'`。`DiscoveredContent.item_key` 由共享 identity helper 派生；B 站缓存仍使用 raw BV storage key，其它平台使用 namespaced key，原始 ID 独立保留在 `content_id`。非空 `item_key` 由 partial unique index 保护，空串仅容纳不知道该 additive 列的旧写入器；当前初始化会补全空 identity、合并 canonical 冲突并恢复 partial unique。`content_cache` 是 recommendation serve 的唯一正式池，`discovery_candidates` 是 discovery 阶段的待评估 / 已评估队列。
- v0.3.0+ 多样性栈：trending 固定 `rid=0` + 非 0 rid 本地洗牌轮转覆盖，并按 rid 交错 / explore 按 domain 交错 / `_compress_topic_repeats` 单次压缩 / `trim_topic_group_overflow` 跨源跨轮配额（任意 topic_group ≤ 池子 10%）/ deficit-source 合并 + 并行 fan-out

### Sources (`sources/`) — 多源适配层 (v0.3.0+)
- `SourceAdapter` Protocol：每个内容源实现统一接口
- `platforms.py` — Bilibili / 小红书 / 抖音 / YouTube / X / 知乎 / Reddit / Bangumi 八个平台族的唯一可枚举注册表；Storage pool accounting、view-event identity、API URL host 推断、Discovery 已看过滤和 runtime 平台常量都委托该表，避免跨模块别名漂移；Bangumi URL 同时识别 `bgm.tv` 与 `bangumi.tv`
- `bilibili_adapter` — B 站 API 直连（WBI 签名、v_voucher 自动恢复）；`bili_tasks` + `/api/sources/bili/*` 提供搜索冷却时的扩展 DOM 搜索兜底，回传结果进入 `discovery_candidates`
- `xiaohongshu_adapter` — 小红书扩展代理（被动收集 + 关键词搜索 + 创作者订阅 + `bootstrap_profile` 初始化画像任务，零后端爬取；task-result 进入 memory 前按已见 note key 跨任务去重）。强信号赞 / 收藏由 MAIN-world `xhs-action-tap`（`obc-xhs-action`，与 token sniffer 隔离）在 like/dislike/collect/uncollect 写端点业务成功后网络层认定，adapter 声明 `tapAuthoritativeActions:{like,favorite,retraction}` 让 kernel 抑制对应 DOM 发射，事件 URL 拼 `…/explore/<note_id>` 与后端 `sources/identity_keys` note 键型互通（支持赞→撤销折价）
- `dy_tasks` — 抖音扩展任务队列（`bootstrap_profile` 初始化画像任务；发布 / 收藏 / 点赞 / 关注信号由扩展以用户浏览器登录态抓取；任务 poll 时标记 `in_progress`，CLI 可复用近期 bootstrap；`search` / `hot` / `feed` discovery 任务统一从 `https://www.douyin.com/` 首页开始，由 content script 模拟真实 DOM 操作触发搜索、热榜或推荐流加载，再被动收集页面自身发出的响应和已渲染 DOM；hot board 的 `group_id` 会作为 `seed_aweme_id` 透传，DOM / 被动监听不足时用已登录页面 related API bridge 拉取热点相关候选；三者分别回传 `dy_search` / `dy_hot` / `dy_feed`，并作为 `dy-plugin-search` / `dy-plugin-hot-related` / `dy-plugin-feed` discovery 来源）
- `yt_tasks` — YouTube 扩展任务队列（`bootstrap_profile` 初始化画像任务；观看历史 / 订阅 / 点赞由扩展以用户浏览器登录态读取 DOM 并分批回传；任务 poll 时标记 `in_progress`，CLI 可复用近期 bootstrap）
- `youtube.takeout` — Google Takeout 离线导入解析器，将 YouTube 观看历史 / 订阅 / 点赞转换为统一事件
- `YoutubeDiscoveryProducer` — 后端直连的 YouTube steady-state discovery loop；在 YouTube 平台族低于 quota 时调用 `yt_search` / `yt_trending` / `yt_channel`，并用 SQLite execution ledger 控制每日执行预算
- `twitter_adapter` — X (Twitter) 服务端 cookie 重放（`source_type="twitter"`，标签 `"X"`）；`XAdapter.fetch()` 是真实实现（非 stub），按 recipe 分发到 `discovery/strategies/x.py` 的 `XSearchStrategy`（画像关键词）/ `XForYouStrategy`（推荐流 For-You）/ `XCreatorStrategy`（账号订阅）。配套 `x_client.py` 的 `XClient`（封装默认运行时依赖 `twitter-cli`，lazy import + 只读 + 类型化错误；`openbiliclaw[x]` 仅作为兼容旧脚本的安装别名保留）、`discovery/x_normalize.py`（tweet → `DiscoveredContent`）、`x_tasks.py`（`x_creator_subscriptions` CRUD）、`storage/x_health.py`（源健康状态机）
- `zhihu_tasks` — 知乎扩展任务队列（`bootstrap_events` 事件 smoke + `search` / `hot` / `feed` / `creator` / `related` discovery）；插件在已登录知乎 tab 中读取浏览历史 / 收藏夹 / 动态点赞收藏，或调用 discovery 接口回传 `zhihu_*` 候选；`runtime.zhihu_producer.ZhihuDiscoveryProducer` 在知乎平台族低于 quota 时按 `source_modes` 入队任务，结果经 `sources.zhihu_tasks.zhihu_discovery_items_to_contents()` 写入 `discovery_candidates`
- `reddit_tasks` — Reddit 扩展任务队列（`bootstrap_events` 初始化信号 + fallback / 显式 `search` / `hot` / `subreddit` / `related` discovery）；插件在已登录 Reddit tab 中读取 saved / upvoted / subscribed 或同源 `.json` endpoint 回传 `reddit_*` 结果；`runtime.reddit_producer.RedditDiscoveryProducer` 在 Reddit 平台族低于 quota 时默认用 rdt-cli 按 `source_modes` 抓 discovery 候选，命令后端不可用或显式 `backend="extension"` 时入队插件 discovery 任务，结果经 `sources.reddit_tasks.reddit_items_to_contents()` 写入 `discovery_candidates`，producer 自身 fetch-only，不同步等待 LLM 评估
- `sources.bangumi_client` / `runtime.bangumi_producer` — 固定官方 `api.bgm.tv/v0` 的匿名只读 client 与 fetch-only producer；search 复用统一关键词，ranked/latest 维护按条目类型 cursor，三分支按 UTC 日条目预算和最小间隔执行，`429 Retry-After` 落 `bangumi_discovery_state` cooldown。Subject 归一化后写 `discovery_candidates`，公开用户名的收藏只在显式 guided init/fetch smoke 中转为事件；默认匿名，可选个人令牌（Bearer）读取私密收藏，令牌被拒（401/403）自动降级匿名；没有扩展 task queue、无 Cookie、无站内写方法。扩展在 `bgm.tv` / `bangumi.tv` 上仅上报公开 uid + 用户名做账号身份识别（`POST /api/sources/bangumi/identity`，含 uid↔用户名交叉校验），不采集浏览行为、不上传令牌
- `web_adapter` — 通用 Web（Playwright CDP + LLM 内容抽取）
- `SourceRecipe` — 源任务持久化与分发

### Recommendation Engine (`recommendation/`)
- 推荐排序与朋友式推荐表达生成；统一从候选池读取
- 惊喜推荐复用普通推荐的 copy-ready 状态门：`pool_expression / pool_topic_label` 未同时生成时，候选只留在 expression-copy backlog，既不进入惊喜打分查询，也不能写入任何 `delight_*` 状态。正式文案就绪后才复用 Evo 的 `relevance_score` 打分，并由条件写入原子同步 `delight_reason / delight_hook`；pending API、CLI 与 runtime stream 继续校验精确快照。evaluator 的内部 `relevance_reason` 永不作为惊喜状态或 UI 推荐理由；旧版错写快照在正式文案就绪后由后台 backfill 修复。普通推荐只在高分行已被 profile-aware 惊喜打分并同步快照后让出该行。
- 推荐列表、换批、pending delight 单条/批量及 runtime delight 事件都增量透传 `published_at` / `published_label`。桌面 Web、移动 Web、扩展 popup 与 CLI 按同一规则消费：精确时间优先并转本地相对日期，来源标签兜底，双空值不渲染；API 层不重写相对时间。
- Bangumi 目录指标 `rating_score / rating_count / source_rank` 与 `favorite_count` 贯穿 subject normalizer → `DiscoveredContent` → `discovery_candidates` → `content_cache` → recommendation/delight API → 三端。评分人数不是评论数、评分不是点赞；无真实值时保持 0 并整段隐藏。
- 推荐、delight 与保存列表出口共享 `item_key / content_id / source_platform / content_url / content_type` 身份契约；`content_cache.item_key` 对非空 canonical identity 使用 partial unique index，并用独立普通索引支持 lookup，`recommendations.item_key` 引用同一 identity。插件 side panel、桌面 Web 与移动 Web 的卡片先 POST `/api/saved/{list_kind}`，保存页再用 `/sync` + durable task poll 做显式平台写入；默认关闭的 `saved_sync.auto_sync_enabled` 只决定本地保存后是否创建后台任务。手动同步对当前 adapter 支持且未处于已同步 / 同步中的项始终可用；仅 `unsupported_adapter_missing` 可在 adapter 注册后重新进入单项/批量快照，`unsupported_content_type` 等真实能力限制继续显示为仅本地保存。本地 `/remove` 永不反向删除平台记录。
- `/api/recommendation-click` 会保留 `content_id / content_url / source_platform`：插件、移动 Web 或桌面 Web 打开推荐内容后，后端把点击写成对应来源的统一事件和 `recommendation_click` 强画像信号；只传 `recommendation_id` 时会从 `recommendations + content_cache` 回填跨源字段，避免 YouTube / 抖音等 ID 被套成 B 站 URL。
- `PoolCurator` 五维评分（relevance · freshness · topic_fatigue · source_monotony · serendipity）
- v0.3.1 双轴 fatigue：`recent_topic_keys` (细) + `recent_topic_groups` (粗) 取 max；曲线 `count^1.5/len*5`，count=2 即触发 0.47 强抑制
- 新兴趣 amplification guard：刚确认的探针兴趣会用 domain/specific/topic key 形成 guard，`PoolCurator` 做 24h rolling budget 软降权，最终批选择做 `max(1, floor(limit*0.25))` 硬上限
- `_merge_topic_supergroups` — serve 时基于 embedding 把 `动漫杂谈/补番/解说` 等近义 topic 合并为同一聚类
- `prewarm_supergroup_embeddings` — refresh tick 后台预热所有池中 topic_group embedding，让 reshuffle 跑全 cache hit
- `PoolServeSnapshot` — 专属 serve DB worker 在一个只读事务内统一读取 readiness、候选窗口、平台补位、持久化 `seen_items` 和 curator 信号；MMR/多样性纯函数与排序规则不变
- `serve_with_result()` — 返回 items、提交后扣减库存与分阶段耗时；推荐历史和 shown 在独立短事务中原子提交，API 先广播结果库存，再 detached 精确收敛
- 换批是默认硬去重动作：桌面 Web、移动 Web 与扩展 side panel 都提交当前卡片 ID，后端继续叠加推荐历史和 `seen_items`；成功响应只写一条 `reshuffle` 批次事件。桌面端不再暴露“换一批时忽略当前”开关，也不会逐卡提交 `dismiss`。CLI 没有持久卡片列表，只复用后两层去重。
- 平台定向作用域（PC Web 平台 Tab）：`serve / reshuffle / append` 的可选 `source_platform` 让 snapshot 只装载该 canonical 平台的候选并跳过跨平台保底补位，其后的 curator、MMR、多样性、文案、持久化与 shown 提交完全复用同一实现；返回前校验并丢弃跨平台泄漏行（记 ERROR）。数据流为 `PC Web tab → POST {reshuffle,append}.source_platform → RecommendationEngine → Storage 平台候选`，配套只读 `GET /api/recommendations/platform-availability` 提供 Tab 库存徽标。库存与选片共用同一份 canonical available 行集合，`total_available == sum(by_platform)`。移动 Web、扩展与 CLI 无平台 Tab，继续走不带平台的兼容路径
- 个性化专题生成

### Runtime (`runtime/`)
- 系统生命周期管理和服务编排
- 降级模式启动：生产 `create_app()` 遇到 LLM registry 配置错误时保留 `/api/ping`、`/api/health`、`/api/qr-info`、`/api/config`、`/api/runtime-status`、`/api/runtime-stream`，并精确放行 `/`、`/web`、`/setup`、`/m` 静态恢复 surface 及其资源；`/api/ping` 仅在降级时附带 reason / issues，桌面 Web 以此先行识别恢复态、停止业务 hydration，再读取配置并自动打开模型设置，展示修复与重启指引。其他业务 API 返回 503，避免半初始化 runtime 继续跑推荐/发现链路
- 配置热重载：`RuntimeContext` 重建 registry / service / engine 时会注入同一份 `[llm.instances]`、`default_chain` 与 `[llm.routes.*]`；热重载后的正向兴趣和避雷 speculator tick 都作为 detached task 注册到 `BackgroundTaskRegistry`，分别读取 `probe_feedback_history` / `avoidance_probe_feedback_history`，不阻塞 `/api/config` 响应
- `AutoUpdateService` — 后端自动更新只查询 GitHub `/tags` 并过滤 `backend-v*`（兼容 legacy `v*` / 裸 semver），明确忽略 `extension-v*`；当前 GitHub Releases 由扩展 artifact 占用，不能用 `/releases/latest` 判断后端源码是否最新
- `runtime.autostart` — 当前用户作用域开机自启动 manager：macOS LaunchAgent、Windows HKCU Run + `.pyw`、Linux XDG autostart；API / CLI / 插件设置页通过 `GET /api/autostart-status` 与 `POST /api/autostart/apply` 管理，带 env-managed / `config.local.toml` shadow guard，并用开启「先写 config 后注册 OS」、关闭「先注销 OS 后写 config」的方向化事务避免崩溃残留
- `runtime.ollama_supervisor` — `start` 启动前复用的 Ollama 预检 helper；从所有启用的 chat 实例和独立 embedding 配置判断是否需要 Ollama，归一化 endpoint 并剥离 `/v1`，仅在默认本机 `localhost:11434` 缺 daemon 时尝试后台拉起 `ollama serve`。桌面 macOS 安装包的随包 runtime 必须来自官方 `Ollama.app`，并携带 `ollama + llama-server + lib*.dylib/.so + mlx_metal_*`，打包阶段拒绝 Homebrew 单主程序或缺关键动态库的 runtime，避免 embedding runtime 半可用；图形化 init 在 embedding provider 已配置时还会复用真实 probe 作为硬前置，防止首轮画像在本地向量服务 500 时悄悄降级。
- `ContinuousRefreshController` — 管理补货、来源 producer 与 API daemon 的 `CandidateEvalCoordinator` 子任务；幂等 `run_startup_maintenance()` 是 host 暴露服务前的统一零 LLM 库存恢复边界。API daemon 的 `run_forever()` 先调用它再启动 delight/candidate/background loops，pipeline 的单次 enqueue callback 是 coordinator 唯一即时唤醒；OpenClaw direct bootstrap 不运行该 loop，因此不 attach dormant candidate / expression coordinator，而将 `recommend(refresh_if_needed=True)` 的首轮 source/evaluation 限为 4（fetch oversample=1、min eval batch=4、inline evaluator=1），在 commit 后同步 drain ≤4 expression copy、禁用本次 split retry。库存维护使用独立单线程 worker/连接，每事务最多 50 行、每 tick 最多 8 批，批间释放 SQLite 写锁并让出 event loop；75ms 锁冲突直接延后。fresh history 为空时该 operation 直接 serve 首 batch 已复制的 canonical subset；其 one-shot callback 不创建 prewarm/provider background task，剩余 pending 由后续请求续补。热重载的新 controller 也先恢复；同一 controller 后续进入 loop 不重复维护。
- `FeedbackBatchScheduler` — API 侧推荐反馈合并器；`/api/feedback` 只标记 dirty 并启动一次 debounce 后台任务，burst 内多条反馈 coalesce 成一次 feedback batch，批处理中又收到新反馈时补跑下一轮。Soul 层 single-flight 负责兜底其它入口的并发保护。
- `/api/runtime-status` / `runtime-stream` — 对插件、移动 Web 和桌面 Web 发布同一套候选池库存口径：`pool_available_count` 只表示当前可立即被 `serve()` 消费的内容，`pool_raw_count` 表示基础 fresh 素材加待评估 raw candidates，`pool_pending_count` 表示已有素材但仍缺评估、文案、分类或可跳转链接；命中持久化 `seen_items` 的素材不算 pending。`pool_pending_eval_count` / `pool_evaluated_pending_count` 分别拆出待 LLM 评估和已评估待 admission 的数量；`pending_signal_events` 只表示 discovery refresh 游标后的新动作数量，用于下一次统一补货判断，不会由事件入口直接执行 refresh。前端只把 available 显示为“可换”，pending 显示为“正在整理”；后台补池的 source deficit 也使用 available-by-source，而 raw trim / headroom 使用 all-raw-material by-source。推荐读取、换一批和续页消费候选池后会立即广播新的 `refresh.pool_updated` 快照，使其它已打开客户端收敛到扣减后的库存，而不重载推荐列表。
- `_publish_probe_if_available` — proactive push 循环中的探针仲裁器；从正向兴趣和避雷探针池中每轮最多选一条，正向探针事件携带 `probe_mode/challenge`，普通 `near` 和挑战探针使用独立 active 额度；只投递 `active` 候选，且只有推送到订阅者后才通过原子 runtime state 更新记录 domain / axis / distance history，避免后台旧快照覆盖用户刚处理的探针反馈
- `background_llm_work_allowed()` — 共享 gate predicate；`scheduler.enabled=false` 会暂停 daemon-owned 后台 LLM / embedding 工作，`scheduler.pause_on_extension_disconnect=true` 时还要求浏览器插件 presence 在线或仍处于断开宽限窗口。该 gate 覆盖 refresh、candidate eval、pool precompute、soul pipeline、xhs/dy/youtube/zhihu producer、proactive push、低频 account sync、startup one-shot 和 OpenClaw direct bootstrap；首个完整画像尚未落盘或 guided init 活跃时（`InitCoordinator.init_active()`）也返回 False，一处暂停所有后台循环，防止 account sync 在用户点击初始化前抢先分析/重复落库，让 init 的显式 analyze / build / backfill 独占（init 自身直调 `soul_engine` / `run_init_backfill`，不查该 gate）。阶段 2/3 另以 task-local scope 绕过空库存 maintenance admission，但仍受 total gate；阶段 4 不继承该 scope，靠 supply / evaluation / expression 正常补货优先级完成
- `_enforce_pool_cap` 每 tick 最多进入 8 次 bounded `maintain_pool_inventory(max_mutations=50)`：每个短连接 `BEGIN IMMEDIATE` 内按 canonical readiness + `seen_items`/链接守卫恢复合格 `suppressed` 历史行并保护新 canonical available，再统一规划 stale / explore / topic / source / 跨表 raw ceiling victims。source/topic 可延期，`evaluating` / token-owned candidate 不可裁，未领取 victim terminalize 为 `trimmed_capacity`，不变量失败整批回滚。恢复使用内存 source/topic 计数，消除逐行 window-function 重扫；`has_more` 驱动下一批。普通 tick 的 readiness fingerprint 未变化时跳过 ranked 扫描，10 分钟安全巡检和 force/post-refresh 路径仍强制运行。BEGIN 锁冲突短等待后延后，不制造零值 result
- `InitCoordinator`（`runtime/init_coordinator.py`）— 图形化引导初始化的生命周期所有者：`init_runs` 持久化状态机 + 单写者进度事件（`_write_lock` 串行化心跳 / 进度 / 取消 / 终态写入，首个终态后拒绝全部迟到写）+ `BEGIN IMMEDIATE` 单飞 + 启动 reconcile（崩溃残留判失败）+ 协作取消 + bootstrap task 归属（供写者门控放行 init 自己的 task-result）。流水线以阶段 3 的完整画像落盘为严格屏障，之后阶段 4 才能使用该画像；阶段 2 的 chunk fan-out 对齐 `[llm].concurrency`，默认墙钟按 300 秒/并发波次 + 固定 300 秒恢复预留伸缩，真实完成数、已用时与本轮上限持续落状态，临时 429 有界重试但余额不足立即失败；同波硬失败会取消并 drain sibling，reasoning-only length 仅对该 chunk 提升一次输出预算。配套 `ContinuousRefreshController.run_init_backfill` 持 `_refresh_lock` 串行执行发现、评估、表达 drain 和 canonical pool 校验，普通完成即代表至少一条推荐可浏览。`InitPrereqs` 提供 TTL 缓存的 chat / B站 / 平台前置探测；v0.3.118+ B 站登录只在本轮勾选 B 站时才是硬前置，`/api/init-status` 继续下发状态但不再全局阻塞 `can_start`。共享流水线 `cli.run_guided_init` 详见 [init 模块文档](modules/init.md)
- `AccountSyncService` — B 站历史记录、收藏夹、关注列表同步，以及可选的 X likes/bookmarks 定时增量（`resolve_x_cookie` 有 cookie 时装配）；daemon `sync_if_due()` 只有完整画像已存在且 guided init 不活跃时才通过共享 gate，使用历史游标 + 已见 bvid/mid/tweet-ID 集合只把新增账号信号送进画像更新，新增事件先经 48h 跨源去重（扩展已实时上报的同一行为不双计，查询排除自身来源防自压制）；画像就绪后走 `ProfileUpdatePipeline` 增量管线而非直接整层重算 preference，显式运维调用 `sync_now()` 保留旧的空画像 auto-bootstrap 兼容路径（普通首次启动的唯一首版画像 owner 是 guided init）；画像分析受 360 秒墙钟上限保护；来源拉取与画像故障除 raw `last_sync_error` 外，还把最多 8 条安全 `{stage,kind}` 写入 `last_sync_issues`，经 runtime-status 暴露为 `last_account_sync_issues` 并由后端合成平台 / 环节 / 原因 / 下一步文案，桌面 Web 不解析或展示原始 provider 异常
- `/api/sources/{xhs,dy,yt,zhihu,reddit}/task-result` — 插件 bootstrap / search partial / final 结果完整保留在任务表；XHS / 抖音 / YouTube 传播到 memory / profile pipeline 前读取 `source_bootstrap_state.json`，跳过跨任务已见 note/video/item key，避免旧收藏 / 历史再次触发画像更新；知乎 `task-result` 自身不直接写 memory，`fetch-zhihu` 保持 smoke，guided init 会显式收集完成的 `bootstrap_events` 结果并在 init pipeline 内持久化 / 建模；知乎 search / hot / feed / creator / related 只转换为 discovery raw candidate；Reddit search / hot / subreddit / related 同样只转换为 discovery raw candidate
- `runtime-stream` — 浏览器扩展 background 以 `client=background` 连接后，后端先推送 `xhs_login_state_sync_requested` / `zhihu_login_state_sync_requested`，扩展只读取本地浏览器 Cookie store 中 `web_session` / `z_c0` 是否存在，并分别向登录态端点回传布尔值；这一步不打开、刷新或请求平台页面。若后端本地没有 B 站 Cookie，还会推送 `bilibili_cookie_sync_requested`，扩展立即通过 `/api/bilibili/cookie` 回传当前浏览器 Cookie；后端持久化 Cookie、热重载 runtime 组件，并重新启动 refresh / account sync / auto update 后台任务，避免热重载取消后台循环后小红书 / 抖音 producer 停止；重复同步相同 Cookie 时不再重建 runtime，避免打断正在等待扩展回写的抖音 discovery。B 站扩展搜索兜底任务入队后会通过同一 stream 广播 `bili_task_available` 唤醒扩展 poll，扩展在后台打开真实 B 站搜索页、抓渲染后的 DOM 结果并 POST 回 `/api/sources/bili/task-result`；知乎事件 / discovery 任务入队后会广播 `zhihu_task_available`，扩展打开带 `openbiliclaw_zhihu_task` 标记的已登录知乎任务 tab 并回写 `/api/sources/zhihu/task-result`，其中 `bootstrap_events` 初始化 / 事件 smoke 使用前台 tab，search / hot / feed / creator / related discovery 使用后台 tab；Reddit bootstrap、命令后端 fallback 和显式 `backend="extension"` 的 discovery 任务入队后会广播 `reddit_task_available`，扩展打开带 `openbiliclaw_reddit_task` 标记的已登录 Reddit 任务 tab 并回写 `/api/sources/reddit/task-result`，其中 `bootstrap_events` 读取 saved / upvoted / subscribed，search / hot / subreddit / related discovery 读取同源 `.json` endpoint；默认 Reddit discovery 在 rdt-cli ready 时不走 stream，而由命令后端完成。本机 `/api/extension/e2e/run` 也复用同一 stream 投递 `extension_e2e_run`，让已安装扩展打开 / 复用真实抖音、小红书、X 标签页执行白名单 DOM 操作；复用同域 tab 时先导航回平台稳定入口，事件仍由 content collector 自然进入 `/api/events`，runner flush buffer 后再由后端匹配。若 `[sources.douyin].enabled=true` 且后端没有环境变量或 `data/douyin_cookie.json`，会推送 `douyin_cookie_sync_requested` 并通过 `/api/sources/dy/cookie` 回传抖音 Cookie。后续推荐、惊喜、画像更新和探针确认仍复用同一条 WebSocket 事件流；`interest.probe` / `avoidance.probe` 只有实际进入至少一个 stream 订阅者队列后才写入对应 domain / axis 冷却状态，正向 probe 还会写入 `probed_distance_bands`，并在 payload 里暴露 `probe_mode/challenge`；正向和负向 probe 通过 `last_probe_kind` 每轮最多投递一条；同一连接也驱动 `PresenceTracker`，服务端 reader 会 `receive()` 检测 idle disconnect，避免浏览器断开后 presence 卡住
- `/api/image-proxy` — 移动 Web 和扩展 side panel 的推荐、惊喜和消息封面图统一走 `UI -> /api/image-proxy -> 白名单 CDN -> bounded spool -> UI`，后端在发送响应前完成 URL、redirect、Content-Type 和 10MB 实际字节校验

### API Auth Gateway (`auth_core.py` + `api/auth.py`)

- 局域网 / 远程访问的**可选密码门禁**。`create_app()` 在 degraded-mode guard 之后用 `@app.middleware("http")` 注册鉴权中间件（更外层、最先执行），挡所有 `/api/*`（含 `/api/runtime-stream` WS 与 `/api/image-proxy`）；`/api/health`、`/api/qr-info`、`/api/auth/*` 与静态壳（`/`、`/m`、`/web`）保持公开。桌面 / 插件二维码只通过 `/api/qr-info` 取 `lan_ip`，避免扫码入口触发 `/api/health` 的 embedding readiness probe。
- `auth_core.py` 纯标准库：scrypt 密码哈希、HMAC 无状态签名 token、稳定密码指纹、反向代理 `X-Forwarded-For`（受信代理从右向左解析、fail-closed）与 Origin / scheme 归一化（CSRF `Origin==Host`、WS Origin、Bearer 裁定、`Secure` cookie 复用同一实现）。
- 默认凭据是 HttpOnly cookie `obc_session`（同源 fetch/img/WS 自动携带，前端不持有 token）；跨源限时 Bearer 为允许列表内逃生通道。改密 / 登出所有设备 / 轮换密钥经 SQLite `auth_state` 表的单调 `auth_epoch` 真正撤销所有设备；`session_secret` / `password_hash` 永不经 `GET /api/config` 返回。详见 [API Auth 模块](modules/api-auth.md)。
- 远程浏览器扩展认证默认关闭：`ext-key generate` 只把设备密钥 SHA-256 摘要写入配置，`ext-key enable` 后 `/api/auth/extension-token` 才可用。扩展用长期设备密钥换取最长 168 小时的短会话；普通 HTTP 走 `Authorization: Bearer`，只有 WebSocket 和 `/api/image-proxy` 因浏览器接口限制使用短会话 query。撤销任一设备密钥会提升全局 `auth_epoch`，立即失效所有现有会话。远程扩展不依赖可伪造的 Origin 或 Docker 网关信任。

### Side Panel Durable Chat

插件聊天不再把主状态只放在 DOM / JS 内存里。`popup/` 对主聊天、惊喜推荐内聊和兴趣猜测内聊统一调用 `/api/chat/turns`：

1. popup 生成 `turn_id` 并 POST 消息、`scope`（`chat` / `delight` / `probe` / `avoidance_probe`）和可选的内容上下文。
2. 后端先把 turn 写入 SQLite `chat_turns(status='pending')`，随后用后台任务调用 Dialogue 引擎生成回复。
3. popup 通过 `/api/chat/turns/{turn_id}` 轮询，并在初始化时按 `session/scope` 重新 hydrate 历史。

这条数据流让 Chrome 在切 tab、reload 或内存压力下丢弃不可见 side panel 后，仍能恢复 pending thinking 占位、完成回复或失败状态。完成后的 delight/probe/avoidance_probe scope 会继续发布对应 cognition/runtime 事件，主聊天仍按原有受控学习链路进入画像更新。

### Init 多源画像导入

`openbiliclaw init` 的首轮信号由本轮勾选的数据来源合流。B 站与小红书 / 抖音 / YouTube / X / 知乎 / Reddit / Bangumi 都可选；至少保留一个来源。Bangumi 只有在用户显式提供公开 username 时读取公开收藏并作为画像信号：Bangumi-only 缺 username 在预约 run 前拒绝，混合来源缺 username 则明确 warning 并仅启用 discovery。所有实际画像来源都没有信号时以 `empty_signals` 失败。

1. B 站 API 直连拉取观看历史、收藏夹和关注列表（仅当本轮选择 B 站；`--no-bilibili` / `OPENBILICLAW_NO_BILIBILI=1` 会跳过并持久化关闭 B 站源）。
2. 后端在 `xhs_tasks` 表入队 `bootstrap_profile`，并在 `init --yes-xhs` / `fetch-xhs` 默认复用 6 小时内已有 bootstrap 任务，避免重复打开前台小红书 tab。浏览器插件轮询 `/api/sources/xhs/next-task` 时，后端会先把任务原子标记为 `in_progress` 并写入 `claimed_at`；15 分钟无回写才允许重新领取。插件在用户已登录的小红书页面中先打开 `/explore` 定位当前用户 profile。滚动任务会以前台 tab 触发页面内“我”入口的 anchor click，background 只等待同一 tab 完成导航；只有找不到可点击入口时才回退到直接导航。到 profile 后，插件解析 profile state / DOM 中的 `saved / liked` notes 和页面显式暴露的 `xhs_history` notes，回写 `/api/sources/xhs/task-result`。当任务显式传入 `max_scroll_rounds` 时，插件会在 profile tab 内优先探测 feed / waterfall / masonry 滚动容器做有限滚动，并先用 `status="partial"` 分批回传新增 notes，最终再用 `status="ok"` 完成任务；`scroll_wait_ms` 和 `max_stagnant_scroll_rounds` 也由任务 payload 控制，并由插件端裁剪到安全范围。
3. 后端在 `dy_tasks` 表入队 `bootstrap_profile`，由浏览器插件在用户已登录的抖音页面中依次访问发布 / 收藏 / 点赞 / 关注 scope。content script 结合 DOM 解析、MAIN-world fetch tap 和 API harvester 采集条目，按 scope 以 `status="partial"` 分批回写 `/api/sources/dy/task-result`，最终以 `ok` 完成任务。Douyin 默认需要显式 `--yes-douyin` 才进入 init；非交互式终端默认跳过，避免盲目触发风控或空 200 响应。CLI 默认复用 6 小时内近期 `bootstrap_profile`，扩展领取任务时会把 pending 标记为 `in_progress`。
4. 后端在抖音任务完成后再在 `yt_tasks` 表入队 `bootstrap_profile`，由浏览器插件在用户已登录的 YouTube 页面中依次访问 `/feed/history`、`/feed/channels`、`/playlist?list=LL`。YouTube 与抖音都会打开前台 tab，串行入队可避免多个平台同时抢浏览器焦点。YouTube 默认需要交互式确认或显式 `--yes-youtube`；非交互式终端默认跳过，`OPENBILICLAW_NO_YOUTUBE=1` 会强制跳过。CLI 默认复用 6 小时内近期 `bootstrap_profile`，扩展领取任务时会把 pending 标记为 `in_progress`。
5. 后端在 `zhihu_tasks` 表入队 `bootstrap_events`，由浏览器插件在用户已登录的知乎页面中读取最近浏览记录、收藏夹条目、个人动态点赞和个人动态收藏。`fetch-zhihu` 使用同一任务类型但只做 smoke；guided init 选中知乎时会显式收集任务结果并把事件写入本轮 profile inputs。知乎默认需要交互式确认或显式 `--yes-zhihu`；非交互式终端默认跳过，`OPENBILICLAW_NO_ZHIHU=1` 会强制跳过。CLI 默认复用 6 小时内近期 `bootstrap_events`，动态点赞和动态收藏各自独立使用单分支上限。
6. 后端在 `reddit_tasks` 表入队 `bootstrap_events`，由浏览器插件在用户已登录的 Reddit 页面中先读取 `/api/me.json` 识别当前用户，再读取 saved、upvoted 和 subscribed subreddit。`fetch-reddit --mode bootstrap` 使用同一任务类型但只做事件 smoke；guided init 选中 Reddit 时会显式收集任务结果并把事件写入本轮 profile inputs。Reddit 默认需要交互式确认或显式 `--yes-reddit`；非交互式终端默认跳过，`OPENBILICLAW_NO_REDDIT=1` 会强制跳过。CLI 默认复用 6 小时内近期 `bootstrap_events`，三个分支各自独立使用单分支上限 300。

回写后的跨源对象会转成普通事件层 payload：小红书 `saved -> favorite`、`liked -> like`、`xhs_history -> view`；抖音 `dy_post -> view`、`dy_collect -> favorite`、`dy_like -> like`、`dy_follow -> follow`；YouTube `yt_history -> view`、`yt_subscriptions -> follow`、`yt_likes -> like`；知乎 `zhihu_read_history -> view`、`zhihu_collection -> favorite`、`zhihu_activity_like -> like`、`zhihu_activity_favorite -> favorite`；Reddit `reddit_saved -> favorite`、`reddit_upvoted -> like`、`reddit_subscribed -> follow`；Bangumi 公开收藏的 `wish/done/doing/on_hold/dropped` 分别映射为带强度的 `favorite/view/favorite/view/feedback(dislike)`，显式评分 `>=8` 覆盖为 `like`、`1..4` 覆盖为 `feedback(dislike)`；X 点赞 / 收藏也会作为 `twitter` history 行进入画像构建输入，保证 X-only 初始化有画像素材。事件都带 `metadata.source_platform`。任务表保存完整原始结果；XHS / 抖音 / YouTube API 传播前会用 `source_bootstrap_state.json` 跳过跨任务已见 identity key，知乎 / Reddit 则由 guided init 汇总后统一持久化，Bangumi 只有显式公开 username 时才在本轮读取并持久化，避免 smoke 命令误触发画像。CLI 只短暂等待任务结果；插件未连接、未登录、页面不暴露对应数据或 Bangumi 公开收藏为空时，初始化会使用已拿到的其它来源继续，但若所有所选来源都为 0 信号则失败。profile 已经初始化后，后续 XHS / 抖音 / YouTube bootstrap task-result 新增事件还会转成 `ProfileSignal` 进入 `ProfileUpdatePipeline`，补齐跨源增量画像更新；首次 init 期间仍由汇总事件统一生成画像，避免重复学习。

v0.3.102+：上述四阶段（拉取 + 入库 / 分析偏好 / 生成并保存完整画像 → 生成首轮可用推荐）抽成共享异步流水线 `cli.run_guided_init`，CLI 与后端 API 复用同一份逻辑——CLI 用单次 `asyncio.run(run_guided_init(...))` 驱动，后端在服务事件循环里直接 `await`，互不嵌套 loop；阶段 3 是严格提交屏障，阶段 4 使用其返回的完整画像，再按「发现 → 个性化评估 → 推荐表达 → canonical 可用性校验」闭环。唯一与路径相关的补池步骤以 `discover_backfill` 注入（CLI 一次性引擎 / API 持 `_refresh_lock` 的 `controller.run_init_backfill`）。图形化入口包括插件「推荐」tab、安装包首启 `/setup/` 第 3 步和桌面 Web `/web` 未初始化推荐区，都会渲染来源选择 + 前置清单 +「开始初始化」按钮，`POST /api/init`（仅本机）经 `InitCoordinator`（`init_runs` 持久化状态机 + 单写者进度事件 + `BEGIN IMMEDIATE` 单飞 + 崩溃 reconcile + 协作取消）后台跑 wrapper，进度走 `runtime-stream` 的 `init_progress/completed/failed`，`GET /api/init-status` 给权威进度 + 前置检查（LLM / embedding / 平台登录状态；B 站仅在选中时阻塞）。init 活跃期间写者门控：`background_llm_work_allowed()` 一处暂停所有后台 LLM 循环，画像 / 配置 / 反馈 / 手动 refresh / 探针 / source 配方等 HTTP 写端返回 `409 init_running`，`/api/bilibili/cookie` 静默 no-op、`/api/sources/*/task-result` 放行，init 任务豁免热重载取消。普通完成是可浏览推荐已就绪的后端权威终态；部分完成允许前端进入应用并由恢复后的后台补池。后台恢复后的同步命令适配器（当前为 Reddit `rdt/opencli`）必须经 worker thread 执行；完成态 `init-status` 只读探针缓存，避免后台预热 / 外部命令反过来冻结终态页面。详见 [init 模块文档](modules/init.md)。

### Douyin DOM-First Discovery

抖音 steady-state 内容发现走 opt-in 路径：`OPENBILICLAW_DOUYIN_COOKIE` 可显式覆盖，默认则复用浏览器扩展同步到 `data/douyin_cookie.json` 的 douyin.com Cookie。后端 `DouyinDirectClient` 仍保留 direct-cookie 诊断能力，但默认 discovery 子来源已收敛为插件执行的 `search` / `hot` / `feed`：后端只入队 `dy_tasks(type="search"|"hot"|"feed")`，扩展后台 tab 一律先打开 `https://www.douyin.com/`，再由 content script 模拟真实 DOM 操作触发页面加载。

search 会聚焦页面搜索框、输入关键词并触发搜索；hot 会从首页可见入口进入热榜 / 热点卡并点击目标热词，同时使用 hot board 的 `group_id` 作为 related seed；feed 保持在首页推荐流并滚动。三条链路都不再主动跳 `/search/...`、`/hot/...` 等快捷 URL；search / feed 只被动监听页面自己发出的 fetch/XHR 响应并解析已渲染 DOM，hot 则在 DOM / 被动监听不足时用已登录页面的 related API bridge 按 `seed_aweme_id` 拉取 `dy_hot` 候选。`DouyinDiscoveryService` 是这条链路的复用边界：runtime 正常路径拉 raw candidates 后写入 `discovery_candidates`，再由共享 evaluator 入正式推荐池；调试时也可以由 `openbiliclaw discover-douyin --no-cache --no-evaluate` 直接跑 strategy 预览召回。这样初始化强账号信号与后台补池请求分离，且 search / hot / feed 都能复用真实登录浏览器但不会抢用户焦点。

`openbiliclaw search-douyin` 保留为同一插件 DOM-first 搜索链路的独立 smoke：结果只保存在任务结果里用于诊断，不进入 `content_cache`，也不参与画像重建；正式 runtime discovery 会把这些候选映射为 aweme-like JSON，以 `dy-plugin-search` / `dy-plugin-hot-related` / `dy-plugin-feed` 进入 `discovery_candidates` 待评估池。插件任务为空、超时或失败时默认返回空结果；只有显式构造 `DouyinPluginSearchClient(allow_direct_fallback=True)` 的诊断代码才会启用 direct-cookie fallback。

### X (Twitter) Discovery & Capture

X 是第六个内容源，分两条独立通路：

1. **发现（服务端 cookie 重放）** —— 对标抖音 direct，但用默认运行时依赖 `twitter-cli`（Apache-2.0，自带 `curl_cffi` TLS 指纹；`openbiliclaw[x]` 仅保留为兼容安装别名）取代 XBogus 签名。浏览器扩展 `cookie-sync.ts` 的 x.com 分支把用户真实 `auth_token` + `ct0` 经 `POST /api/sources/x/cookie` 同步落盘 `data/x_cookie.json`（可被 `OPENBILICLAW_X_COOKIE` 覆盖）。后端 `XDiscoveryProducer` 在 X 平台族低于 quota 且源健康就绪时，按预算调度 `search`（Soul 画像关键词）/ `feed`（推荐流 For-You，最高曝光、压到很低频次并在连续失败后自动暂停）/ `creator`（`x_creator_subscriptions` 账号订阅）三个策略，经 `XClient`（全程只读，lazy import，`enabled=false` 绝不 import）拉推文，`normalize_tweet()` 转成 `source_platform="twitter"` 的 `DiscoveredContent`（`content_type ∈ {tweet, thread}` + `body_text` 全文），enqueue 进统一 `discovery_candidates` 待评估池，由共享混源 evaluator 入正式池。源健康状态机（`storage/x_health.py`）持久化 `ok` / `missing_cookie` / `expired_cookie`(401) / `blocked`(403) / `rate_limited`(429)，按 code 分别退避，经 `GET /api/sources/x/status` 暴露到设置页；账号侧 likes / bookmarks 增量同步复用同一健康 store，冷却或登录阻断时不再从旁路出网，首个失败也会在同轮取消第二条请求。

2. **行为采集（扩展 MAIN-world tap + generic collector）** —— 在用户自己的 x.com 登录态下被动偷听互动 GraphQL mutation：点赞 → `like`、收藏 → `favorite`、回复 → `comment`，转推 → `share`、关注 → `follow`、点开 → `view`；generic collector 同时记录 click / scroll / search / hover / snapshot 上下文。事件经 `POST /api/events` 进 Soul 画像，与 discovery 通路完全独立、互不去重。`share/follow/view` 会即时 flush 以降低延迟，但在偏好语义上仍由后端 satisfaction / analyzer 判断，不等同于全局强正反馈。

### Zhihu Discovery & Event Smoke

知乎是第七个内容源，当前明确分成三条轻量通路：

1. **事件 smoke（不进画像）** —— `openbiliclaw fetch-zhihu` 入队 `zhihu_tasks(type="bootstrap_events")`，扩展在已登录知乎 tab 内读取最近浏览、收藏夹、动态点赞和动态收藏，回传后只转换并打印统一事件计数。该命令不写 memory、不触发初始画像或增量画像更新，用于验证真实登录态可取到哪些强信号。
2. **guided init 信号（进首版画像）** —— CLI / 插件 / 桌面 Web / `/setup/` 勾选知乎或传 `init --yes-zhihu` 时复用 `bootstrap_events` 任务结果，把浏览 / 收藏 / 点赞 / 动态收藏转换为统一 `zhihu` 事件，与其它所选来源一起进入 `analyze_events()` / `build_initial_profile()`，并 best-effort 写回 `[sources.zhihu].enabled=true`。
3. **多路 discovery（进待评估池）** —— `ZhihuDiscoveryProducer` 在 `[sources.zhihu].enabled=true` 且知乎平台族低于 quota 时，按 `source_modes` 入队 `zhihu_tasks(type="search"|"hot"|"feed"|"creator"|"related")` 并通过 `zhihu_task_available` 唤醒扩展。`search` 从统一关键词 planner claim `PLATFORM_ZHIHU` 关键词并拉 `search_v3`；`hot` 拉热榜；`feed` 拉首页推荐；`creator` 优先用最近知乎任务里的作者主页作种子，没有历史种子时使用同轮 search / hot / feed 返回的作者页；`related` 优先用最近知乎候选 URL 作扩展种子，没有历史种子时使用同轮已返回内容 URL。后端映射为 `source_platform="zhihu"`、`source_strategy ∈ {zhihu-search, zhihu-hot, zhihu-feed, zhihu-creator, zhihu-related}`、`content_type ∈ {answer, article, question}` 的 `DiscoveredContent`，写入 `discovery_candidates(pending_eval)`，由共享 evaluator 决定是否进入推荐池。`openbiliclaw discover-zhihu*` 是这条链路的手动 E2E smoke。

知乎任务 tab 同样带 `openbiliclaw_zhihu_task` 标记，content script 在任务模式下只跑 executor，不启动普通行为采集，因此 discovery smoke 和事件 smoke 都不会污染 `/api/events`。

### LLM Providers (`llm/`)
- 统一的多模型接口（OpenAI / Claude / Gemini / DeepSeek / Ollama / OpenRouter）
- `[llm.instances.<id>]` 为每个端点保存独立 `provider_type` / Base URL / token / model；registry 以实例 ID 注册，同一个 adapter 可实例化多次。`default_chain` 可包含任意数量实例，失败与限流 cooldown 都只影响当前实例
- `LLMService` 通过 caller bucket 选择 `[llm.routes.soul/discovery/recommendation/evaluation]`：默认继承全局链，`inherit=false` 时执行模块自己的完整链并严格禁止 spill 到全局链。旧 provider/model override 会投影为等价实例或派生实例
- `LLMRegistry.complete_chain()` 执行有序链，`complete_provider()` 精确探测一个实例；响应携带最终 `instance_id`。Ollama chat 实例必须显式配置 model，仅有服务地址或独立 `bge-m3` embedding 不会注册 chat、更不会猜 `llama3`
- `/api/config/discover-models` 在配置内存副本上构建精确实例并调用 OpenAI-compatible `GET /models`，供 PC Web、插件与 setup 的可编辑模型下拉使用；该支路不保存配置。协议没有 Effort capability 枚举，返回的 Effort 仅是本地 advisory
- `codex_auth.py` 提供实验性的 Codex CLI ChatGPT OAuth 凭据导入和刷新；OpenAI 实例设置 `auth_mode="codex_oauth"` 时只替换认证来源，并限制 `base_url` 为 OpenAI 官方 API 域名
- DeepSeek 的连通性探针显式关闭 thinking；普通请求的 reasoning effort 是 request-local 参数，不修改共享 adapter 状态。每个 DeepSeek 实例的 `base_url` 分别进入 SDK 和 endpoint 代理裁决
- 结构化输出共享解析：`llm/json_utils.py` 为 discovery eval-batch、recommendation copy/classify、soul awareness/insight/profile/speculator 提供统一 JSON 容错，兼容 MiMo / OpenAI-compatible wrapper、fenced JSON、JSONL、schema echo 和 malformed `{ [ ... ] }`
- v0.3.0+ embedding 兜底：`OllamaProvider.embed()` 走原生 `/api/embeddings`，配 `bge-m3` 模型可在 Mac/Win/Linux CPU 跑相似度计算，不需额外 API Key
- `EmbeddingService` L1 内存 + L2 SQLite 双层缓存；`embedding.provider="ollama"` 且 embedding 凭据为空时直接使用本地 Ollama 默认地址，不再产生向后兼容 warning
- `DashScopeEmbeddingProvider`（`provider="dashscope"`，阿里百炼原生 multimodal-embedding API，仅 embedding）加入 embedding provider 家族，其 `embed()` 文本向量与 openai/gemini/ollama 一样接入既有文本 embedding 消费方；出站走 `network.httpx_kwargs_for_endpoint(base_url)`——dashscope.aliyuncs.com 属国内 endpoint，即使 `[network].mode` 切到 system/custom 也强制直连（对齐 v0.3.167）。可选 `[llm.embedding].multimodal_enabled` + 多模态模型（`gemini-embedding-2` / `qwen3-vl-embedding`）时启用**封面视觉链路**：discovery 入池预热封面向量（按 URL 派生键），Recommendation 两条路径一致消费「封面↔兴趣锚点」跨模态余弦的有界正向加成——惊喜 `precompute_delight_scores`(加到 delight_score) 与正常 `serve()` 排序(并入 relevance 项;热路径只读缓存、不现抓)。跨模态 floor/ceil 已按真实部署数据标定（`_VISUAL_COVER_SIM_FLOOR/CEIL=0.35/0.48`，per-cover max anchor cosine 的 p50/p95，834 covers）。默认关闭、纯文本零成本、只加不减、默认路径逐字节一致
- 弹幕文本（P2，`[discovery].danmaku_enabled`，**无需多模态**）：`BilibiliAPIClient.get_danmaku_texts` 走 `comment.bilibili.com/{cid}.xml`（无鉴权纯 XML，`cid` 从已有 `/x/web-interface/view` 响应零成本读取），复用该 client 的 `trust_env=False` CN 直连与共享限速；`discovery/danmaku.py` 的 `condense_danmaku` 压缩刷屏（含多字重复单元压缩，数字豁免）、剔除高频梗、**按压缩后长度**（非频次，实测结论）取长弹幕成摘要，存 `content_cache.danmaku_text`（与 `body_text` 严格隔离——后者渲染到三端卡片正文），`danmaku_fetched_at` 空结果也打戳保证幂等。`prewarm_pool_danmaku` 挂 `prewarm_pool_mmr_embeddings` 串行预热并按摘要文本嵌入（沿用文本键空间，不碰 `_mmr_embedding_text`）；`serve()` 的 `_danmaku_bonus_map` 只读缓存，用摘要向量 vs 兴趣锚点算 text↔text 有界加成，与其余三路并行叠加。`_DANMAKU_SIM_FLOOR/CEIL=0.30/0.65` 仍为 PROVISIONAL/未实测，待更多数据后按分布重标（铁律 3）
- 视频关键帧（P3，`[discovery].keyframe_enabled` 叠加 `multimodal_enabled`）：`discovery/keyframes.py` 从 B 站预生成的 videoshot 雪碧图取关键帧（`GET /x/player/videoshot`，无鉴权；雪碧图经 `runtime/image_cache` 下载——`hdslb.com` 已在白名单且属 CN 直连，铁律 1），PIL 按网格切分、**跨全部雪碧图**均匀采样并跳过片头片尾，各帧独立 `embed_image` 存 `keyframe_embedding_cache_key(bvid, idx)`。`prewarm_pool_keyframes` 挂在 `prewarm_pool_mmr_embeddings` 上串行预热并在 `content_cache.keyframes_fetched_at` 打戳——**仅当结果确定时打戳**：`frames==[]`（视频确无 videoshot 数据，永久）或 `embedded>0`（至少一帧嵌入成功）；`frames` 非空但 `embedded==0`（embed 后端临时故障）**不打戳**，留 NULL 下轮重试，避免临时故障被持久化为"已完成"而永久排除 top 相关性候选（铁律 2）。`serve()` 的 `_keyframe_bonus_map` 只读缓存，用**帧向量 max-pool** 对 P1 质心算**纯正向**有界加成，与前两路并行叠加。`_KEYFRAME_SIM_FLOOR/CEIL=0.40/0.64` 已按真实精灵图帧实测标定（keyframe-vs-centroid pos best sim 的 p50/p95，99 候选）。成本与抓一张封面同级（一次请求 100 帧，无需下载视频/ffmpeg），实测覆盖率 100%
- 用户视觉画像（P1，`[discovery].visual_profile_enabled` 叠加 `multimodal_enabled`）：在上述跨模态加成之外新增**独立并行**信号——`rebuild_visual_profile()` 从 `recommendations` 反馈（like/dislike/save）取封面（经 `get_feedback_covers` 绕过 pool admission，确保读到**每一条**反馈封面，不被 confidence≥min_score 静默丢弃）、复用 URL-keyed 缓存嵌入、`recommendation/visual_profile.py` 贪心凝聚成 k 个**均值质心**（多峰口味，`DEFAULT_CLUSTER_THRESHOLD=0.50`，cover-pair p99=0.497）存入主库新表 `user_visual_clusters`（profile-scoped，非 `embedding_cache.db`），在 `precompute_delight_scores` 同 tick 按 `feedback_at` vs `updated_at` 节流重建；`serve()` 热路径 `_visual_profile_bonus_map` 只读内存质心 + URL-keyed 封面缓存、零 API 零聚类，候选封面↔质心同模态余弦映射为**纯正向有界加成**，独立常量 `_VISUAL_PROFILE_*`（floor/ceil 0.31/0.61，candidate-vs-centroid p50/p95，已实测标定），与跨模态加成并行叠加进 `relevance_bonus`。默认关闭/无反馈时加成恒 0、排序逐字节一致
- **正向加成（去 neg 惩罚）**：P1/P3 原为「正向加成 − 负向惩罚」，但真实数据表明 liked/disliked 封面在视觉空间重叠（二元反馈分不出视觉品味 vs 题材/节奏/UP 风格的不喜欢），neg 质心 sim 与 pos 几乎持平（P1 neg p50 0.440 > pos 0.405；P3 neg p50 0.395 ≈ pos 0.397），惩罚抵消了过半候选的正向信号。已改为**纯正向**：匹配 liked 质心即加分，匹配 disliked 质心不再扣分。neg 质心仍构建/持久化，留作未来条件惩罚的快速恢复。
- **跨平台公平性归一化**：四路 bonus 在 `serve()` 叠加成 `combined_bonus` 后、喂给 MMR 选择器前，按 `source_platform` 分组做 min-max 归一化到 `[0, _COMBINED_BONUS_CAP]`（四路 cap 之和 = 0.20）。修复 Bilibili-only 信号（P2 弹幕 / P3 关键帧）结构性抬高 Bilibili 候选、挤压 bangumi/xhs 的问题——缺信号的平台只丢**平台内**区分度，不丢**跨平台**高度（实测：bangumi combined max 0.092→0.200 追平 bilibili，top-25 bangumi 0→3）。combined_bonus 为空（全信号关）时 no-op，排序逐字节一致。

### Storage (`storage/`)
- SQLite 数据库管理
- 冷备份、完整性检查与显式修复
- 候选质量信号持久化与数据迁移；`events` 行写入 `inferred_satisfaction` / `satisfaction_reason`，支持 `query_events(satisfaction_modes=...)`
- `seen_items` 是 discovery / recommendation 共用的无界已看身份账本；`insert_event` 与 `insert_events_batch` 同事务维护，`seen_items_backfill_state` 让升级回填幂等且增量
- v0.3.1 `get_pool_candidates` 用 `ROW_NUMBER() OVER (PARTITION BY topic_group)` 把每个 topic_group 在候选窗口里限到 ≤3 条，保证长尾 group 真正进得到候选窗口
- `discovery_candidates` 持久化所有来源 raw candidates 的 lifecycle：`pending_eval`、`evaluating`、`evaluated`、`cached`、`rejected_low_score`、`rejected_duplicate`、`rejected_cache_admission`、`rejected_recently_viewed`、`rejected_franchise_quota`、`failed_eval`、`trimmed_capacity`；容量 victim 保留 terminal 行和 `eval_error` 原因，不做物理删除。
- `discovery_inspiration_probe_cache` / `discovery_inspiration_expansion_cache` 持久化 query inspiration 搜索探针、横向扩展、curator 判断和 yield 反馈；`discovery_interest_selection_ledger` 记录二级兴趣抽中事件，让兴趣被抽到后立即进入冷却而不必等待 keyword yield；`discovery_keywords` 可携带 aspect / inspiration / expansion / angle 元数据，但不改变原有 in-flight 去重键。`KeywordPlanner` 的 inspiration-only 分支会从 selection ledger / keyword / raw candidate / admitted pool 构建二级兴趣 coverage snapshot，经过 brainstorm → provider-chain grounding → curator → deterministic quota / explore validation → bounded repair 后写入各平台关键词池；`keyword-inspiration-dry-run` 复用同一路径但跳过关键词写库，并使用独立 preview selection scope 做真实请求诊断。
- `count_pool_available_candidates_by_source()` 与 `count_pool_candidates()` 保持前端可见口径一致；`count_pool_raw_material_by_source()` 统计 fresh / 非 dislike / 未推荐 / 未命中 `seen_items` 的 `content_cache` raw material，并合并 `discovery_candidates` 中待评估 / 已评估未缓存的 raw material，供 runtime raw ceiling headroom 和 trim 使用。两类来源统计及已看身份都通过 `sources.platforms` 归一，`zhihu-*` 等 strategy 可覆盖旧缓存的 Bilibili 默认平台。
- `maintain_pool_inventory()` 是 runtime 唯一 destructive maintenance 边界：`canonical available -> recover eligible suppressed -> protected IDs -> stale/explore/topic/source plans -> cross-table raw plan -> invariant validation -> commit`；恢复复用 canonical readiness，仅额外要求 `recommended_at IS NULL`，并按来源缺口、相关度、评分时间和稳定 ID 排序。每批最多修改 50 行，维护查询、持久化已看身份与动态 delight 阈值都接受同一显式 isolated connection；专属 worker 与 serve worker 分队列，绝不把共享 `Database.conn` 直接扔进 `to_thread()` 并发事务。
- `load_pool_serve_snapshot_async()` / `persist_pool_serve_async()` 是交互读写边界：前者在一个一致只读事务中聚合推荐所需状态，后者在短 `BEGIN IMMEDIATE` 中原子写 recommendation + shown；交互锁等待按 8×250ms 有界，维护锁等待固定 75ms。
- `chat_turns` 持久化 side panel durable chat turn，字段包含 `turn_id/session/scope/subject/message/status/reply/error/created_at/updated_at`；`scope` 支持 `chat`、`delight`、`probe` 和 `avoidance_probe`
- `auth_state(key, value)` 单行表持久化局域网密码门禁的撤销纪元 `auth_epoch` 与稳定密码指纹 `password_fingerprint`（非会话表，仅全局计数 + 指纹）；跨进程事务原子自增，验签实时读

## 运行时数据库约束

本地 API 与 CLI 的高频运行路径现在遵循两条约束：

1. **同进程共享一个 Database facade，延迟敏感任务使用隔离连接**
   `MemoryManager`、`RecommendationEngine`、`ContentDiscoveryEngine` 会优先复用同一个 `Database` 对象，避免一轮运行里多次 `Database(...).initialize()` 争锁；推荐 serve 与后台 pool maintenance 分别由 facade 拥有的单线程 worker 创建短生命周期连接，不能并发复用 `Database.conn`。
2. **启动前先检查、运行中按周期冷备**
   `openbiliclaw start` 会在启动前检查数据库完整性；若健康且超过默认 24 小时未备份，会先生成一份冷备到 `data/backups/`。

数据库修复不在启动路径里自动执行，高风险恢复统一通过 `openbiliclaw db-repair` 触发。

## 对外集成约束

当前 OpenClaw 接入遵循两条边界：

1. **外部集成只通过 adapter 调用内核**
   OpenClaw 不直接访问 SQLite、memory JSON 或内部 engine 组合细节。Direct bootstrap 会在 adapter 暴露 Soul/recommendation operation 前调用 controller 的幂等 startup maintenance，避免绕过 daemon `run_forever()` 的恢复顺序；其 inline admission 在返回前同步补齐 durable copy，而不假设未启动的 daemon owner 会在稍后处理。
2. **skill 只是协议包装，不是业务主链**
   学习、推荐、反馈回流仍由 `runtime/`、`soul/`、`recommendation/` 等模块负责，`integrations/openclaw/skill.py` 只负责对外暴露稳定 handler。
3. **真实 OpenClaw 技能发现走仓库根目录 `skills/`**
   当前仓库通过 `skills/openbiliclaw-adapter/SKILL.md` 提供真实 workspace skill，再由 skill 内部调用 adapter CLI bridge。
