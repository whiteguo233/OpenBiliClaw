# 架构设计

## 系统概览

OpenBiliClaw 采用分层架构设计，从上到下依次为：

```text
LAN clients ─ HTTP（默认）────────────→ IPv4 0.0.0.0 + IPv6 [::] listeners → one uvicorn / FastAPI app
public clients ─ HTTPS（可选）→ Caddy :443 ─ shared-loopback HTTP ─────────────────────────────┤
trusted LAN ─ HTTPS（可选）──→ TLS Proxy :8443 ─ loopback/Compose HTTP ───────────────────────┘

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

config recovery control plane (normal or degraded; business APIs stay gated)
                ├─ draft → /api/config/probe-service → temporary registry → total gate
                └─ draft → /api/config/discover-models → exact instance GET /models
                          → editable model list + local effort advisory (no config write)
Agent hosts (OpenClaw / Hermes / WorkBuddy)
        → capabilities(agent-bridge/v2) + JSON CLI / skill descriptors
        → integrations.agent alias / integrations.openclaw compatibility adapter
        → existing runtime / soul / recommendation / saved_sync owners

migration control plane (real loopback; browser calls also require same origin)
                ├─ export → config minus api.auth + online SQLite snapshot + portable files → checksummed plaintext .obcbackup
                ├─ import(request_id) → bounded validation → private pending stage ↔ status / cancel
                └─ next exclusive start → journaled config/data replace → applied | rollback
                                           applied status → browser applies allowlisted preferences
interest updates: HTTP/source event → durable commit + wake (no pipeline/LLM wait)
                  ├─ profile_events cursor(generic owner) ──────┐
                  ├─ content_feedback cursor(priority owner) ──┴→ atomic buffer+cursor checkpoint
                  │                                               → owner tick_if_buffered → INTEREST
                  │  periodic profile maintenance only → tick
                  └─ dialogue → typed settlement worker → learning
                  legacy feedback batch retired; unified_interest_line=false is rollback only

dislike output boundary:
  exact card feedback commit → processed projection + recommendation snapshot invalidation
  confirmed topic durable write → SoulEngine effective snapshot (flat preference + Soul + overrides)
                                → evaluation / serve profile sees it before Soul rebuild
                                → history cache digest + reshuffle/append/OpenClaw/push final recheck
  discovery search remains broad; detached exact/semantic pool purge is inventory optimization

XHS hidden search tab → MAIN search-response normalizer → isolated replay/DOM fallback → task final
XHS/DY/YT/Zhihu/Reddit/Linux.do/V2EX task final: canonical staged result (source caps / fields enforced)
                                                 → durable event receipt → account-scoped bounded seen-key → terminal flip
                                 stale lease reclaim replays first write; staged row rejects late mutation
V2EX public API/Feed → bounded Topic detail + optional PAT Reply digest → raw Topic candidate
V2EX canonical identity: PAT verified > browser observed > config/accepted
                       ├─ mismatch/unknown → pause account projection; public discovery stays live
                       └─ resolved → identity-scoped seen key + Node affinity
                                  → complete favorite scope → 2-miss snapshot ledger
                                  → durable retract/restore effect → event + affinity → effect ack
extension-online periodic re-pull: explicit opt-in → presence + profile/init/config gates → persisted round-robin
                                 → one active bootstrap across six task tables → EventHub → extension
Douyin source supply: daemon presence gate (explicit manual call bypasses it)
                     → one shared plugin-cycle wait budget → terminal dy_task → pending_eval
                     absent → zero enqueue; timeout/error/budget → bounded retry floor

cover images: UI proxy foreground ───┐
              refresh prefetch bg ───┴→ app-stable ImageFetchCoordinator(total 4 / bg 3, fg priority)
                                      → cache-key singleflight → whitelist fetch → atomic disk cache

30-day content history: click events + recommendation rows + saved_item_removals
                     → canonical platform/item identity → latest-per-item/context projection
                     → /api/content-history(clicked|shown|removed, opaque cursor; legacy offset)
                     → source max-id anchors + total/page in one read snapshot
                     → popup / desktop / mobile lazy covers through the same image cache
  cursor continuation excludes later inserts, but mutable rows/restored/retention remain live

dialogue entries → app-stable execution lease(max active 1; reload pause/drain)
  durable dialogue → confirmation entry(pending list / cards)
                 → chat_turn(reply_to_turn_id + payload + fixed turn time)
                 → server-frozen DialogueTurnBinding → pending SQLite
                 → rowid-ordered durable reply worker → SocraticDialogue(queued)
                   → visible completion CAS; transient/cancel → pending + bounded in-place retry
                   explicit invalid → failed CAS
  direct chat/probes → same lease through response + ctx-dependent side effects
  post-reply learning/object settlement (independent of reply backlog)
                 → typed settlement queue[all 11 declared kinds] → one actual worker + guard
                 → pending≤3 → user open(no cooldown) | system 12h+object 72h
                   → busy worker: 503 dialogue_busy + Retry-After → UI bounded auto-retry
                   → active clarifying: current holder only; session-local turn dedupe
                   → confirmation INSERT → attached user INSERT (created_at,rowid)
                 → one context digest → prompt/history/event/learn/settlement provenance
                 → anchor snapshot(kind + ref + generation) → existing insight extraction
                 → kind×relation matrix ┐
                 → hypothesis card action ┴→ frozen snapshot → worker-only apply
                   action: local completion≤1s → 200
                         | blocked head → 202 processing → popup/mobile/desktop GET 1/2/5s, ≤30s
                   confusion object failure → replay_queue(max 5, head-fenced) → 12h recovery
                   → lightweight ref winner receipt
                   → event → object → derived → rebuild-marker → applied
                   → stable audit / cross-session projection / exact-generation release

config hot-reload → accepting drain old settlement worker → atomic pause → exact revoke old permit
                  → start/register new → publish new → stop old
                  └─ 25m timeout before pause/revoke: old stayed accepting + abort
                     new start failure: fresh nonce reauthorize old + resume

reshuffle HTTP → temporal review-hold / expiry retirement → PoolServeSnapshot → serve DB worker / isolated read connection
               → latest effective-dislike final check → HTTP serialization
               → unchanged MMR selector → final temporal recheck + short recommendation+shown transaction
  optional source_platform (PC Web tabs only, additive):
    canonical platform → platform-scoped candidate rows, no cross-platform floor
                       → same curator / MMR / diversity / persistence path
platform-availability HTTP → isolated read snapshot of the canonical available set
                           → {total_available, by_platform}, total == sum(by_platform)
background refresh → maintenance DB worker / isolated connection
                   → ≤50 mutations per transaction → commit/yield/retry next batch
candidate raw-empty → quota-aware supply wave → under-share platform producers + Bili refresh
                    → inserted/enqueued progress → reset, or 30/60/120/300/600s backoff

manual `discover --source douyin` → same Douyin producer as daemon
                                 → unified keyword lifecycle → plugin search/hot/feed
                                 → discovery_candidates(pending_eval)
source-scoped cache backfill → strategy source_platform set → SQL filter before balance/LIMIT
                             → only that platform can supplement an underfilled run

candidate evaluation → effective profile view + exact tail-recall pool + negative exemplars
                     → prompt-visible content/context digest + embedding namespace
                     → normal eval LRU lookup ─hit─→ relevance + atomic temporal evidence → caller-group diversity caps
                     └─miss─→ complete recall → LLM batch → time-neutral relevance + temporal v2 evidence group
                               → discovery_candidates → eligible/review_due/expired → content_cache/hold/stale
                               → publication bonus → pre-serve retirement → final atomic eligibility recheck
                               embedding/recall degraded ───────────────→ no normal-cache write
```

1. **用户交互层** — Chrome / Firefox 插件负责受支持站点的普通行为采集、登录态只读任务与侧边栏；Linux.do / V2EX / 微博使用隔离任务 tab，微博普通页面不做行为采集。插件与移动 Web（`/m`）、桌面 Web（`/web`）共用本地 API；可选密码门禁保护局域网 / 远程访问。
2. **外部集成层** — OpenClaw adapter / skill wrappers / 本地 API / Codex CLI 凭据导入等对外接入边界
3. **Agent 核心层** — 自研编排器 + Soul Engine + Discovery Engine + Recommendation Engine + Skill System；抖音手动 discovery 与 daemon 共用正式 producer、统一关键词生命周期和待评估候选链，debug-only `discover-douyin` 才直接调用源服务
4. **LLM 实例路由层** — `config / Web UI -> [llm.instances.<id>] -> 全局或分模块有序实例链 -> LLMRegistry -> Provider adapter`。实例 ID 是路由、健康与 cooldown 身份，adapter 类型只是协议实现，因此同类型的多个 Base URL / token / model 可以同时存在。模块默认继承全局链；自定义链只在链内降级，耗尽后不越界。配置界面另有两条无写入恢复支路：`draft -> /api/config/probe-service -> temporary registry -> stable total gate` 做目标实例/链真实探测，`draft -> /api/config/discover-models -> exact instance GET /models` 只返回模型 ID 与本地 Effort 建议。两者在 active registry 启动失败的 degraded 状态仍精确放行，但不改变配置、不放开业务 API。
   配置写入的实际切换走独立控制流：`UI -> PUT /api/config -> config.toml + .bak -> 202 queued/apply_revision -> app-owned latest-wins queue -> RuntimeContext rebuild -> apply-status/config_reloaded`；失败从 last-good 同时恢复磁盘、proxy 与内存 runtime，再发 `config_reload_failed`。`data_dir` 是例外：新 canonical 路径只持久化并返回 `restart_required=true`，本进程的 rebuild 与外部凭据写仍绑定已锁住的 active data dir，完整重启取得新目录锁后才切换。
   发布日期偏好按来源配置（`[sources.<name>].recommendation_date_*`），默认 `all`。发现策略在 LLM 评估前硬过滤范围外候选；`RuntimeContext -> Database effective inventory -> PoolCurator -> RecommendationEngine` 继续保留 `weight` 语义（软降分或推荐出口严格忽略）。严格模式还把 UTC 边界下推到 B 站 API / 扩展网页搜索。
   跨机器迁移走第三条、与热重载隔离的控制流：`本机桌面设置 -> export .obcbackup -> 新机器 import(request_id) -> processing(uploading|validating) -> pending -> status 对账 / cancel -> 重启`。导出数据固定来自本进程已锁住的 active data dir；导入端点从不替换 live `Database` / `MemoryManager` 或 UI 偏好。断连后的桌面端保留 request ID，最多强制查 3 次并对 `idle/cancelled` 间隔 500ms 再确认；每次打开「通用」也强制查询。只有下一进程先同时取得项目与 canonical data-dir runtime lock，才通过 journaled replace 激活配置和数据；成功回执用配置 SHA-256 + 严格递增 DB epoch 绑定活动代际，使断电后复活的旧 marker 只能被验证、清理而不能重放。status 报告 `applied` 后，桌面端按 `migration_id` 在每个浏览器只应用一次白名单偏好，避免旧 status 覆盖用户后续修改。来源包会移除整段 `[api.auth]`，机器专属路径 / 网络字段和目标机整段 `api.auth` 继续作为基线；应用时再轮换文件 session secret、把数据库 auth epoch 严格提升到来源 / 目标当前值之上，并关闭 / 清空扩展设备访问。
5. **多源适配层（v0.3.0+）** — `sources.platforms` 统一注册 B 站 / 小红书 / 抖音 / YouTube / X / 知乎 / Reddit / Linux.do / Bangumi / V2EX / 微博的平台别名、strategy 与 URL 身份。Linux.do 通过同源只读插件任务接入公开 discovery 与个人信号；Bangumi 使用官方匿名 API 与可选令牌；V2EX 使用官方匿名 API / Feed、可选 PAT 与四类账号分区 bootstrap；微博公开 discovery 由后端匿名直连，个人初始化由同源扩展任务读取收藏、关注和 mentions。所有来源统一归一化后进入候选评估链，不执行站内写操作。
6. **保存同步编排层（API/runtime + B 站 adapter + 三个图形化保存界面 + CLI 配置可见）** — canonical saved identity + normalized membership / native state + `/api/saved/*` + capability router + local-first `SavedSyncService` + `BilibiliNativeSaveAdapter`；六平台扩展保存 adapter 已按能力/目标矩阵注册，经稳定的 `ExtensionNativeSaveBroker` 入队，完整 broker flow 为 `extension_native_save_jobs -> /api/sources/<slug>/next-task -> installed extension`（具体 source 前缀为 `/api/sources/{xhs,dy,yt,x,zhihu,reddit}`），再由 authenticated `task-result` 回传安全状态。trusted-local `/api/extension/e2e/run` 的 dedicated native-save 模式只接受与 generic actions 互斥的 exact authorization，提交一个 canonical item 到同一 saved-sync/broker flow，并只回传六字段结果；通用 DOM runner 永不执行 favorite/bookmark。历史 `unsupported_adapter_missing` 行可重新同步，但真正的 `unsupported_content_type` 保持终态。YouTube favorite 与知乎 favorite 使用 exact `OpenBiliClaw`，YouTube watch-later 使用 `YouTube Watch Later`，其余平台回退原生收藏/书签/Saved；Bilibili favorite/watch-later 使用 direct adapter。2026-07-14 已在自动同步关闭、手动同步触发下完成七平台两类动作真实账号验证，终态均为 `synced/already_synced`；插件、移动 Web 与桌面 Web 共享 `item_key`，以 bounded request、retained list、per-key mutation fence、reload task recovery / item ownership 和 visibility-aware durable tracker 呈现同步状态；CLI 只通过 `config-show` 展示默认关闭的自动同步配置，不提供保存 / 同步动作命令
7. **多层网状记忆存储** — Core / Episodic / Semantic / Working Memory（SQLite + 向量索引 + JSON）

### Candidate evaluation 的可复现边界

评估 cache 的 v5 key 覆盖实际 prompt-visible 候选字段、effective source context、compact
profile 与精确 tail-recall pool、negative-examples 内容、embedding namespace、prefilter mode
和 schema version。混合平台 / 不稳定隐式 context batch 与实际 vision attempt 不进入 normal
per-item cache；embedding 异常、空 / 非有限向量或维度漂移时，本轮结果也不写 normal cache，
恢复后必须重新 recall + evaluate。cache 原子保存逐项相关性与 temporal v2 证据组，不能跨轮
拼接 mode/state/evidence/时钟；franchise/style cap 在稳定的 caller grouping 上重放，因此
full/partial hit 和 `enforce` 预筛的 cold/warm 边界一致。

`reason` 是内部诊断字段：single/batch 共用 runtime normalizer，低于 0.5 或被 batch cap
淘汰时为空，高分 strip 后最多 30 个 Unicode code points；非字符串不做 `str()` 宽松转换，
而是进入 malformed-member retry。对象、LRU 与候选持久化看到的都是归一化结果。

`scripts/run_profile_diet_ab.py` 是 landing evidence harness，不是生产请求入口。它从只读 DB
冻结候选、effective profile（含 overrides / active speculations）和负例，按生产的 30 条
claim grouping 与 `source_context=mixed` 跑 repeated A/A + A/B；每个 logical run 记录实际
provider/instance/model，并对 embedding、recall、route 与 snapshot fail closed。Replay 固定
temperature 0 用于隔离采样噪声，artifact 同时披露生产默认 0.7；因此
该结果是 model-visible diet 的受控相对门，不替代合入后的 48 小时生产观测。

HTTPS 有两个互斥的**可选传输边缘**，都不是新的业务 API 层。公网域名的 Docker 部署叠加
`docker-compose.https.yml`：Caddy 在 `:443` 自动终止受信 TLS，与后端共享 network namespace，
只经 `127.0.0.1:8420` 转发 REST / WebSocket；宿主机 `8420` 同时收紧为 loopback，Uvicorn 只
信任该 loopback hop 的 forwarded headers。可信 LAN / self-managed 部署则使用
`[tls_proxy].enabled=true` 的 `serve-api` 或 Docker `tls` profile 建立 `:8443 → :8420` 路径；
它精确校验 HTTPS Origin 与 Host、兼容 Chrome/Firefox 扩展 Origin、转发 WebSocket、给 TLS
cookie 补 `Secure`，并把已验证的 Web Origin 做最小 `https→http` 适配。证书生成依赖可选
`cryptography`，转发主体使用 Python 标准库。默认 HTTP 仍直接进入 FastAPI。

海外出口另有一条显式路由边界：`config / Web UI -> [network].mode -> openbiliclaw.network -> 每个 LLM 实例 endpoint / YouTube / X twitter-cli / Reddit rdt-cli·OpenCLI / Bangumi / updater / Codex OAuth`。默认 `system` 继承环境 / OS 代理（CLI 会收到物化后的代理环境变量；海外服务在国内直连必然超时，而这是开箱默认值；没配代理时等价于直连），`direct` 对 SDK 注入 `trust_env=False` 并从 CLI 环境剥离代理变量，`custom` 注入指定 URL；LLM 链中每个实例按自己的 Base URL 独立裁决国内直连或海外代理。X / Reddit 的浏览器扩展 fallback 仍跟随浏览器网络设置。B站 / 抖音 / V2EX / Ollama / 国内 CDN 客户端不读取该边界。

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
- 将现有 runtime / engine 能力暴露为协议中立 Agent skill；OpenClaw 前缀仅为兼容命名
- `agent.py` 提供 Hermes / WorkBuddy 等宿主的稳定 Python 别名，`openclaw/` 保留历史路径
- `capabilities.py` 输出 `agent-bridge/v2`、宿主兼容名和完整 descriptor manifest
- 提供 JSON CLI bridge，供仓库内 workspace skill 和其他本地 Agent 宿主调用

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
- Phase 2 cognition prompt 边界由 `CognitionEventViewV1` + `CognitionProfileViewV1` 提供 provider-independent 的确定性投影，并按 stable soul / preference → volatile cognition → current batch 排序。生产配置逐 task 选择视图：只有真实 SenseTime gate 通过的 `soul.awareness_confusions` 默认 compact-v1；Preference、Insight 与 plain `soul.awareness` 均保持 legacy，不能由一个聚合开关整体切换。
- Phase 3 继续保持 legacy/compact 选择不变，只收紧 analyzer 送入 builder 的请求集合：Preference 自动预算路径从每个剩余 offset 按独立 chunk 的真实 prompt shape 二分找最大前缀，偏斜的大事件仍交给既有单条 compact recovery。Insight 的 Phase 3 固定 `latest-20 ∪ latest-20 judged/validated` 视图现保留为异常回退/历史 control；生产视图最多 40 条，按 latest-8、judged-8、当前 awareness/profile 相关性 16、重要性/多样性 8 四路选择并加权补位。同状态近重复只竞争 prompt 槽，不同确认状态分别保留，随后仍与完整 durable hypothesis ledger 合并并保存。因此 token 优化边界位于 `CognitionCycle/PreferenceAnalyzer → prompts.py` 之间，不改变 MemoryManager 的持久层、system/output contract、max-token ceiling 或 provider 路由。
- 认知画像流水线（`soul/ledger.py` + `soul/dialogue_anchor.py` + `soul/confusion.py` + `soul/posture_gate.py`）：兴趣层的事件驱动写入已经收敛为一条 `ProfileUpdatePipeline → INTEREST` 路径，行为、对话与 feedback 都是管线信号，其中 feedback 带优先级阈值；旧反馈批默认退役，仅 `unified_interest_line=false` 回退时恢复。其上叠加统一审计与一致性纪律。**单锚**由 queue admission 冻结成带 kind/ref/generation 的 persisted/reserved/failed/absent snapshot；worker 只做 exact validation，绝不把受理时的 generation 0 升级成执行时出现的未来锚。**对象结算**在 `SoulEngine` 中拆成公开 `submit_*` 与 worker-only `_apply_*`；11 个 declared kind 的生产入口已全部接入一个 in-memory queue/actual worker，锚 relation 和普通 chat settles 在当前 learn worker task 内直接 apply，不递归排队或 inline dispatch。guard 校验 actual worker Task + lifecycle nonce，不存在 child 临时授权；request task、active child 与跨 job detached child 均不能进入 protected façade 或冒充队外 producer。`card_settlements` 只保存 immutable winner、result、stable event identity 与 `applied`，数据库级文件锁、5 分钟 lease、claim token、三段 CAS、discussion attempt token 与恢复 scanner 已删除。apply 顺序固定为 event → object → derived → rebuild marker → applied → projection → exact-generation anchor release；前四类 effect 可幂等重放，`applied=1` 后的显式 retry 只补 ledger observer / projection / anchor publication。结算与 revise-derived 台账使用稳定 hash effect key，首次 audit 写失败不阻断业务，恢复后补写不重复。列表/单 turn GET 只 submit `card.reconcile`，由 worker 补 publication 或修复无活锚 orphan discussion。队列 job 不落盘；重启后由 action retry/GET reconcile 重新 admission，不增加 scanner/job table。疑惑锚对象段仍先入 `confusions.replay_queue`（FIFO 5、精确队头、四类解锚清空台账），12h cycle 只枚举并提交专属 attribution replay。疑惑 topic 冻结、held 重放与代理证据折价不变。**态势门控**继续只覆盖深层对话候选与 soul 整份重建；VALUES/CORE 管线层已退役，三模式仍为 off/shadow/enforce。
- 分类词表（`taxonomy.py`）：偏好层一级分类收敛到固定 `CATEGORY_VOCAB`，`PreferenceAnalyzer` 在写入前用精确命中 / embedding 最近邻 /「其他」兜底解析，避免自由文本分类污染长期画像。
- 分类迁移与画像整理：`CategoryMigrator` 通过 `profile-consolidate --migrate-categories` 把存量自由分类迁到固定词表；`ProfileConsolidator` 的 12h 整理流程按 `(name, category)` 处理同名异义主题，支持 LLM 用 `{name, category}` 精确引用成员。
- 用户画像覆盖层（`overrides.py`）：用户手动编辑存独立 `profile_overrides.json`，在读收口 `get_profile()` 与镜像收口 `sync_profile_files()` 叠加到 AI 画像之上（有效画像 = AI ⊕ 覆盖），画像重建不覆盖用户编辑；删 / 拉黑经有效 dislikes 影响 discovery / recommendation / delight 硬过滤（Phase 1 后端；编辑 UI 见 Phase 2/3）
- `event_filters` / `satisfaction_filter_enabled` — 偏好分析前只丢弃 `negative`（quick_exit / explicit_negative）事件，保留 positive / neutral / unknown 作为上下文
- `negative_exemplars` — 从事件层抽取近期 negative 标题，供 Discovery eval-batch 做负样本锚点
- 三个公开事件 ID 字段使用严格 JSON string：数字、布尔或其它非字符串不会被 Pydantic 自动转换，
  与缺失、空白、超长输入一样在 route 前 422 且零写入。
- `/api/events` — 浏览器插件统一行为入口；每个 event 必须携带 trim 后 1–400 字符的 `event_id`，缺失/空白/超长时整个请求在 handler 前 422、零写入。批次内验证后由 `EventIngressService` 在一个 durable transaction 中提交所有有效行与 receipt，raw `dislike` 规范为 `feedback`，未知事件进入响应 `rejected` 明细而不是让整批 500。若 soul 画像明确未初始化，普通行为事件返回 `not_initialized` 拒收且不写 memory；首轮画像信号只由 guided init 来源任务拉取。profile ready 后 HTTP 只做 commit+wake，不同步调用 pipeline/LLM：app-owned `EventProcessingScheduler` 让 `profile_events` generic consumer 与 `content_feedback` consumer 按各自 cursor 扫描显式 owner，使用 event-row 稳定 signal ID，并通过 `checkpointed_enqueue_batch()` 将 buffer+cursor 一次原子发布到 `pipeline_state.json`，随后 owner 调 `tick_if_buffered()`。只有独立周期画像维护调用 `tick()`。retraction 投影属于 generic claim，在 cursor 前完成；hypothesis/import feedback 由其它 owner 处理或只越过 feedback cursor。首次 startup 只同步完成 owner cutover fence 与 scheduler task admission，lifespan 随即返回；consumer/LLM 在 owned background task 中恢复，5 秒安全扫描继续兜底。shutdown 取消并 gather，热重载则保留同步 pause/drain/recover/rebind 屏障。三者共同覆盖 commit→wake、scan→checkpoint、checkpoint→consume 崩溃窗口。`pending_signal_events` 只保留 discovery refresh 水位，不代表画像 backlog。
- `/api/feedback` — 推荐卡主动反馈入口；trim 后 1–400 字符的 `request_id` 必填，缺失/空白/超长在 handler 前 422 且不写 event/投影。桌面 Web 的 `like/dislike/dismiss` 先经过客户端 10 秒 pending-action 屏障，撤销时不会发出写请求，倒计时结束或 `pagehide` keepalive flush 后才进入 API；失败时客户端回滚。API 的成功边界是 event-first 的两个独立 commit：先按 `request_id` 提交 durable `feedback` event，再更新 recommendation 展示投影；投影失败时请求失败，同一 `request_id` 重试会校验首写 payload 并补投影，冲突 payload 返回 409。之后只 wake 并立即返回，不获取 pipeline lock、不等待 LLM。`/api/recommendation-click` 使用相同必填 `request_id` 边界和 409 首写冲突语义。`EventProcessingScheduler` 是 generic 与内容反馈 owner 的 app-owned 调度器；旧名 `FeedbackBatchScheduler` 仅为兼容 alias。内容反馈 owner 把显式 `like/dislike/comment/dismiss` 且非 import 的行转成稳定 signal，通过 `checkpointed_enqueue_batch()` 同时发布 buffer+cursor，再调用 `tick_if_buffered()`；其它 feedback namespace 只越过。首次 startup 发布 owner-v2 cutover fence 后只 admission background recovery；配置热重载才同步 recover。`ProfileUpdatePipeline` 的 buffer mutation / layer drain 由 `_ingest_lock` 串行，成功 drain 在非 buffer maintenance 前保存。仅 `unified_interest_line=false` 回退时恢复旧批的游标读取与全量偏好分析。评论和探针聊天不走客户端屏障；进入 LLM 偏好分析前会剥离插件原始大字段，只保留偏好相关 metadata。
- `InterestSpeculator` — 兴趣推测与投机性发现
- `AvoidanceSpeculator` — 不喜欢领域探针；未确认前只展示给用户确认，不进入推荐过滤，确认后通过共享 dislike writeback 写入 `disliked_topics` 并清理候选池
- 苏格拉底式用户对话；API runtime 显式使用 `queued`，成功回复后同步提交 typed `learn` 到唯一 `DialogueSettlementQueue`，worker 在线内直接 await 学习；同一队列还拥有卡片动作、锚、普通 settles、探针/疑惑与 legacy façade。CLI/OpenClaw 两处显式使用 `legacy_direct`，保持既有 detached direct learning 且位于 queue/guard 外。两条学习链都用 task-local bypass 跳过 background admission（仍经过 total gate），所以空库存也能学习。若真正新增长期避雷项，偏好落盘即启动共享 dislike writeback：精确清池先执行，语义精判与完整画像重建并行，把匹配候选标成 `purged_by_dislike`，不阻塞回复

对话链路的失败边界是端到端一致的：

```text
Web/API durable → rowid-ordered reply worker → app-stable dialogue lease → SocraticDialogue(queued) → user+agent history
                  └─ all declared settlement entries → DialogueSettlementQueue → one worker
card action → await local job ≤1s → 200 | 202 processing → popup/mobile/desktop poll ≤30s
direct delight/legacy/probe/avoidance chat ─────────┘ (same lease through post-reply effects)
CLI/OpenClaw → SocraticDialogue(legacy_direct) → user+agent history
               └─ detached direct learning (outside queue/guard)
learning → bypass background admission; keep total gate
         └─ new dislike → shared pool purge
transient/provider/timeout/cancel → rollback provisional history → durable pending + bounded retry
explicit invalid/empty response → rollback provisional history → safe error / failed CAS

桌面 Web 首屏：推荐 hydration │ runtime hydration │ health/profile/activity/config 次级 hydration
桌面后台恢复（已有卡片）：跳过可能补池的推荐 GET │ 只同步 runtime / 库存状态
```

Web durable turn 只在成功 completion CAS 后交接认知与成功事件；瞬态失败行保持 `pending/reply=""/error=""`，只有显式 terminal 行持久化安全分类文案。热重载在发布新 context 前暂停并排空 app-stable lease；排队请求随后才解析新 dialogue 与 Soul owner，超时则恢复旧 owner。桌面 Web 首屏的推荐读取、runtime 读取与 health/profile/activity/config 等次级 hydration 保持三个独立分支，任一慢请求不阻塞其余分支渲染；后台恢复 / 配置水合先检查本地推荐列表，已有卡片时跳过可能触发 `serve()` 补池的 `/api/recommendations`，只合并 runtime / 库存状态，空列表或明确手动刷新才读取推荐快照。

### Memory System (`memory/`)
- 五层网状记忆管理
- 跨层关联和双向修正
- 自我编辑和遗忘机制
- 「已消费」事件（`view` / `favorite` / `like` / `coin`，2026-07-26 起不再只有 `view`）在与事件行相同的 SQLite 事务内 upsert canonical `seen_items(source_platform:content_id)`；旧库按游标回填全部历史，类型集扩大时按 `scanned_event_types_version` 自动倒回重扫一次，不再用“最近 2000 条”扫描充当推荐去重。另有两条**非事件**入口：account sync 每轮把完整 B 站收藏快照经 `Database.mark_items_seen()` 直接写入账本；三端惊喜卡“× / 看过了”经 `Database.mark_delight_seen()` 先写 canonical ledger、再置 `delight_notified`。二者都幂等且不产生偏好事件，因此不会重复计入学习信号。普通推荐与 delight 动态阈值、打分 backlog、计数、pending 出口统一硬过滤这份账本。`reshuffle` 只记录一次强度 `0.1`、satisfaction-neutral 的批次导航事实，不把当前十张卡伪装成十条负反馈。

### Content Discovery (`discovery/`)
- 多策略内容发现覆盖十一平台：B 站四策略、小红书、抖音、YouTube、X、知乎、Reddit、Linux.do 五路同源任务、Bangumi 官方 API、V2EX 五路官方 API / Feed 与微博三路匿名后端。`runtime.source_policy` 以默认 `5:1:1:1:1:1:1:1:1:1:1` 比例补池，关闭来源不占 quota；各来源 producer 仅在可换 quota 和 raw-material ceiling 允许时写入统一待评估池。Linux.do 公开 discovery 不要求登录；V2EX 使用分支预算、Node Affinity 与 rate-limit cooldown；微博预算按最终保留候选计数。统一 `KeywordPlanner` 只生产并管理关键词生命周期，实际抓取、去重与候选入池仍由来源 producer 负责。
- 统一关键词水位在 due 计算前经过 digest-grace 整理：`KeywordPlanner → Database.reconcile_pending_keyword_digests()` 在短事务中保留当前 digest 和宽限内安全的旧 `regular/pending`（原 digest/生成溯源不变），过龄、避雷、重复或超 cap 才过期；随后 `count_pending_keywords_all_digests()` 决定是否仍需 LLM 生成，retained pending 同时进入 history。整理失败或 DAO 不可用会退回 `expire_pending_by_digest()` + exact-digest count；grace=0 是显式旧行为。`claimed/executing/terminal` 与 explore 通道不经过这条迁移。
- XHS 自动发现的停止与风控链路是 `config source/scheduler gate → /api/sources/xhs/next-task → xhs_task_runtime_state → extension dispatcher → task executor risk detector → rate_limited result → persistent cooldown / keyword requeue`。关闭来源只暂停 legacy discovery claim，不删除排队计划；扩展因此不再打开 search / creator / bootstrap 页面，重新开启后可恢复。可见安全验证、操作频繁或 429 会把 `rate_limit_strikes` 推进到下一个独立轮次，并打开 `1h → 2h → 4h … → 24h` 平台级冷却，阻断所有 XHS task claim（包括 native-save）并停止 producer；同一活动冷却内的重复报告不加轮次，冷却后的正常 search / creator 成功才重置。关联 planner 关键词从 executing 回到 pending、不增加 attempts。明确的用户 native-save 与 discovery 开关正交，但仍不能越过安全冷却。
- Query inspiration cache 是关键词生成侧的可选基础设施：`[discovery].inspiration_search_enabled=true` 时，`KeywordPlanner` 会先读取 keyword / pool coverage snapshot，并统一归一化兴趣标签 join；随后从 like 二级兴趣中按覆盖缺口抽样，调用 `discovery.keyword_brainstorm` 生成带 `kind_fit` 的搜索 probe branch（解析失败时由 `discovery.keyword_brainstorm.repair` 修成标准 branch），再通过 search provider 链（默认已启用平台源 → Exa → You.com free MCP，由 `[discovery].inspiration_search_backends` 控制）grounding 具体实体 / 社区词 / 讨论点。grounding 有 stage 级搜索预算、平台源扇出预算、每 probe 页数预算和 B 站 / 抖音 / X 等风险源预算；regular + explore 同轮触发时共享一次 brainstorm / grounding stage，再按 kind 分流给 curator。`platform_sources` 只复用已启用同步 / bridge 来源（B站 / YouTube / X / Reddit / Bangumi；抖音 direct client；小红书 / 知乎 bridge 可用时）的搜索结果作为灵感 evidence，不写 `discovery_candidates` 或推荐池；Bangumi grounding 复用同一个匿名只读 client 与请求节流，返回 Subject 标题 / URL / 摘要。随后经 `discovery.keyword_inspiration` 做 Profile Curator / Detail Expander，并优先产出按平台 keyed 的 `platform_keywords`，再把 `inspiration_id -> expansion_id -> platform keyword` 溯源链写入 storage；curator 输入会复用旧 merged keyword planner 的平台供给优势，并附带每个平台的 query_style / recent / avoid / prefer / supply_hint 回压信号、选中二级兴趣、brainstorm 分支、搜索 grounding 记录和 coverage constraints。系统侧会过滤原样证据标题、URL、过长 query、明显平台语言不匹配和平台检索语法不匹配的词，用 grounding hint 校正疑似挂错的 `source_interest`，并为未覆盖兴趣保留 slot 后触发 bounded repair；repair 仍缺词时用 deterministic platform-native backfill 按平台模板补齐，保证 inspiration-only 模式仍按平台原生搜索风格产词，且不会让高频兴趣或单一 lens 吃完整批。admission 后的 keyword yield 会回填到 inspiration / expansion 计数。新配置默认以混合模式开启（`inspiration_search_enabled=true`、`inspiration_replace_merged_keywords=false`），与旧 merged planner 并行；成本敏感时可在设置页切回经典模式。实验开关 `inspiration_replace_merged_keywords=true` 会让 due 平台跳过旧 merged keyword planner，只通过 inspiration flow 填充各平台 `regular` 关键词池，并在 B 站 explore 到期时额外填充 `keyword_kind="explore"` 的探索词池；开 replace 前由 `keyword-inspiration-report` 按 cohort 门禁判定。
- 轴库学习闭环 + 编排抽取（Phase 2，`runtime/inspiration_pipeline.py::InspirationKeywordPipeline`）：上述 ①–⑥ inspiration 编排从 `KeywordPlanner` god-file 抽成独立 pipeline（行为逐字不变，planner 保留四个签名不变的兼容委托 + 一个 `host` 反向引用共享 `_history`/`_insert`/`_avoid_hints`/`_supply_hints`/`_load_profile`）。轴库从"能复用"升级为"会学习"：production stage 在取轴前先跑一次纯 SQL 的 `backfill_inspiration_axis_yield()`（trailing-window 全量重算 / 幂等 / Laplace 平滑）+ `apply_inspiration_axis_lifecycle()`（active→stale/retired→90 天 purge），6 小时节流、preview 永不触发；排序有效分改为条件式 prior 地板（只保护从未消费过的轴，坏轴按真实分下沉）。config 收敛：13 个 `inspiration_*` 旋钮压到 4 个（enabled / replace / backends / `inspiration_breadth` 档位），其余由档位派生成内部常量，删除键经 diagnostics 通道给出移除提示。可选 embedding 近邻轴合并在 pipeline 层（async）解析"新轴→应并入的既有 axis_id"（cosine≥0.92）后交给同步零 I/O 的 `upsert_inspiration_axes()`，服务不可用 / 超时无损降级回字符串行为并标 `axis_embedding_degraded`。Phase 2.3 起，B 站**跨域 explore 通道也走这条 pipeline**（默认开 coexist）：以 merged call 现成的 `explore_domains` 为种子跑 `_run_explore_inspiration_stage`，产 `source='explore'` 的轴 + `keyword_kind='explore'` 词，复用 Phase 2 按 `axis_id` 的 yield 回填 + `list_inspiration_axes_by_source('explore')` 构成舒适区扩张闭环；富生成 degraded 时无损降级回旧 `_explore_domain_queries` 拍平（explore 池不裸奔），到期轮仅多一次 explore 富生成调用，regular 通道不变，`replace` 模式 explore 路径不变。
- `DiscoveredContent` 全形态：`body_text` 支持推文 / thread / 知乎回答摘要全文 / Reddit selftext 或评论正文 / Linux.do topic 摘要 / Bangumi 条目简介，`content_type` 支持 `video/note/tweet/thread/answer/article/question/post/comment/subject`，让文字和目录型来源正确流过统一待评估池并渲染对应卡片。新增通用目录指标 `rating_score/rating_count/source_rank`，与其它元数据贯穿待评估池、正式缓存、推荐/惊喜 API 和三端卡片；评分不冒充 like/comment。
- B 站近期供给 lane：API daemon、CLI 与 OpenClaw 的 `SearchStrategy` 在既有 per-strategy 搜索预算内预留 1 个 `order=pubdate` 请求、最多 5 条；普通/近期 bucket 按 query 交错，保证小评估窗口能看到少量近期供给。API search 冷却时，`BilibiliExtensionSearchProducer` 同样只把每轮第一个任务标为 `pubdate/recent/5`。两路都保留原 `source_strategy` 和 admission，仅在 `discovery_candidates.source_context` 与 raw payload 记录 lane provenance。
- 统一发布时间与证据驱动时效契约：Bilibili、小红书、抖音、YouTube、X、知乎、Reddit 和 Bangumi 的当前来源 payload 只在存在语义明确字段时生成 `published_at`（UTC RFC 3339）或 `published_label`（清洗后的来源相对文本）。字段与时长/互动元数据一起走 `source normalizer -> DiscoveredContent -> discovery_candidates -> candidate evaluation prompt (published_at + exact UTC evaluated_at) -> time-neutral relevance + temporal evidence group -> eligible/review_due/expired -> content_cache -> recommendation publication bonus -> pre-serve retirement -> final atomic recheck -> aggregate no-bonus shadow audit`。Agent 原子输出 `temporal_class/confidence/reason`，以及 `validity_mode`（`none/explicit_deadline/event_state/version_state/freshness_only`）、`valid_until`、`scope`（`none/core/hook`）、正文逐字 `evidence` 和 `state`（`unknown/active/expired/superseded`）；代码拥有 `evaluated_at/next_review_at/policy_version/evidence_complete`，storage 重新计算复审时钟，不接受模型或调用方伪造生命周期时间。只有置信度 `>=0.80`、证据组完整、作用于核心价值且逐字锚定 Agent 实际看到的 prompt projection 时，已过明确 deadline 或 `expired/superseded` 状态才会 hard expire；deadline 还必须逐字包含日期、时刻和时区且与 `valid_until` 为同一瞬间，终态证据必须正向明示结束 / 替代。日期-only、反向证据、标题钩子、低置信、缺字段、未 grounding 和不一致组合均 fail-neutral。`breaking/current/versioned` 的 1 / 14 / 60 天只决定何时复审，不是内容死亡线；旧 v1 行的 3 / 60 天也只转换成 `review_due`，`versioned` v1 行另有 120 天准入 TTL。单条 / 批量 eval cache 同时绑定发布时间摘要与独立评估小时桶，后补时间或跨小时会重评；缺失值不阻断候选，重新发现的空值不覆盖已有非空值，旧缓存不联网回填，也不从 `discovered_at`、任务时间、互动时间或推荐时间猜测。shadow 只保存 Top10/50/100 与 class/source/age 聚合，本身不调整 serving policy。
- 统一待评估池：`source adapters -> discovery_candidates -> tokenized claim -> 最多 3 个 LLM-only worker -> 串行 commit -> relevance + temporal tri-state admission -> content_cache -> expression copy -> servable pool`。`CandidateEvalCoordinator` 是 API runtime 唯一 claim owner，按 `[scheduler].eval_min_batch_size / eval_max_wait_seconds`（默认 15 / 90 秒）凑批；每个 worker 最多 30 条，任一完成即补位，总在途不超过 90。worker 只运行 LLM；串行 lane 先持久化全部 token-owned 评分，再按 `target - available - admitted_pending_available` admission，超额合格结果保留为 `evaluated`。明确过期的 discovery 行终态为 `rejected_temporal_stale`；到复审点的行回到 `pending_eval`，并使用逐行 `1 / 2 / 4 / 8 / 16 / 24` 小时 not-before 租约退避；租约未到的行不 claim，也不计 raw、projected 或来源容量。commit 后 admission 必须重读 durable row，只有数据库最终状态仍为 `evaluated` 才能入池，复审失败留下的 `pending_eval` 不能被同批内存结果绕过。已在 `content_cache` 的条目进入 `pool_status='temporal_review_hold'`，不展示、不计 canonical 库存，并由现有评估链复审后恢复 `fresh` 或转 `stale`。每次生命周期清扫最多持久化 500 条，但 readiness / raw / source cap 读取立即排除所有 hard-expired 或 review-due waiter。完整 temporal 证据组只能整组覆盖，`unknown`、malformed、未 grounding、旧缓存或 raw 重抓不能洗掉字段或让 hold/stale 内容复活。`admitted_pending_available` 是全部 admitted pending-copy 中补齐文案后能进入当前 topic 三条展示窗口的子集；projected 只计 `available + admitted_pending_available + evaluated_pending_admission`，同 topic 深层 backlog 不再虚报为公开库存。手动 CLI 固定 `1 / 0` 立即 drain。`[discovery].eval_prefilter_mode` 默认 shadow 只记录 would-filter，enforce 仅在 recall-visible 兴趣与 compact domains 均低相似时缓存 0–1 分并跳过 LLM，单批过滤过高 fail-open。OpenClaw one-shot 不启动 daemon owner：首轮 source supply / inline claim ≤4（oversample=1、min batch=4、inline evaluator=1），admission 后 await ≤4 durable expression copy 且禁用本次 split retry；有效 subset 立即成为 canonical pool，未完成行保持 pending，不留 detached provider task。complete/release 必须匹配 `id + status + claim_token`，60 秒仅作 safety wake。
- evaluator wire 默认使用 canonical compact `sparse-json` envelope 与请求内 `0..N-1` local ID；严格按 local ID 绑定结果和图片锚点，URL、全局 ID、重复字段及空/零可选字段不进入模型 wire。`published_at` 仍按候选保留，exact UTC `evaluated_at` 仍在顶层 evaluation context；输出同时带时间中性的相关性与完整 temporal v2 证据组，cache namespace 为 v6（active / terminal 状态证据必须是无条件的当前事实）。显式 `production` 保留历史 pretty-JSON/global-ID rollback，`row-wire-v1` 只作历史 replay seam，遇到新增 `published_at` 会显式拒绝而非静默丢字段。
- 候选分层、去重和缓存写入：`discovery.admission` 定义贯穿候选评估、缓存写入与数据库展示的唯一相关性准入策略——非 `explore` 至少使用全局门槛，精确 `explore` 唯一使用 `0.58`；`discovery.temporal` 独立提供三态生命周期。达标且 `eligible` 的候选通过 `cache_evaluated_results()` admission 到正式推荐池 `content_cache`，`_cache_results()` 写前再次 fail closed，数据库取池 / 回填 / delight 等出口再执行同一来源感知条件；`review_due` 进入可逆 hold，`expired` 进入终态。写入时 `pool_status='suppressed'` 的旧候选只有在新分数达标且完整复审证据仍 eligible 时才自动复活成 `'fresh'`，`temporal_review_hold` 也只有新一轮完整复审能恢复。`DiscoveredContent.item_key` 由共享 identity helper 派生；B 站缓存仍使用 raw BV storage key，其它平台使用 namespaced key，原始 ID 独立保留在 `content_id`。非空 `item_key` 由 partial unique index 保护，空串仅容纳不知道该 additive 列的旧写入器；当前初始化会补全空 identity、合并 canonical 冲突并恢复 partial unique。`content_cache` 是 recommendation serve 的唯一正式池，`discovery_candidates` 是 discovery 阶段的待评估 / 已评估队列。

- 可选 learned 相关性校准（`eval_scorer`）：`[discovery].eval_scorer` 默认 `llm`，既有 LLM 评估 / 缓存不变；`shadow` 并跑 `LearnedRelevanceScorer` 与完整 LLM evaluator，产品 relevance 仍取 LLM；人工通过只读 gate 后才显式配置 `learned`，并且只有完整 LLM 成员已产生 temporal / topic / style / franchise 元数据、本批完整审计成功落库时，learned 分数才覆盖 relevance。两种校准模式都绕过 normal eval cache 且仍调用 LLM，本版本不把它宣传为降本路径。scorer 不可用、异常、长度 / 数值 / embedding 维度 / features digest 非法或审计失败均 fail-open 到 LLM。learned-vs-LLM 审计以 privacy-safe 字段写入 `evaluator_learned_scorer_shadow_audit`（30 天 / 2 万行有界，不落候选文本 / URL / 画像）；gate 要求 ≥100 完整对照、telemetry coverage=1、至少一个 LLM admission、Spearman≥0.5、admission delta≤2%、admitted coverage≥90%，缺失指标失败，并且只输出报告、不自动切换。
- v0.3.0+ 多样性栈：trending 固定 `rid=0` + 非 0 rid 本地洗牌轮转覆盖，并按 rid 交错 / explore 按 domain 交错 / `_compress_topic_repeats` 单次压缩 / `trim_topic_group_overflow` 跨源跨轮配额（任意 topic_group ≤ 池子 10%）/ deficit-source 合并 + 并行 fan-out

### Sources (`sources/`) — 多源适配层 (v0.3.0+)
- `SourceAdapter` Protocol：每个内容源实现统一接口
- `platforms.py` — Bilibili / 小红书 / 抖音 / YouTube / X / 知乎 / Reddit / Linux.do / Bangumi / V2EX / 微博十一个平台族的唯一可枚举注册表；Storage pool accounting、事件 identity、URL host 推断、已看过滤和 runtime 常量都委托该表，避免跨模块别名漂移
- `weibo_tasks.py` — 微博 init-only 浏览器任务队列、账号绑定、scope 去重与收藏 / 关注 / mentions 到画像事件的转换；扩展只回传规范化、无 Cookie 的结果
- `bilibili_adapter` — B 站 API 直连（WBI 签名、v_voucher 自动恢复）；主 search 在既有预算内提供 1×5 的 `pubdate` 近期 lane。`bili_tasks` + `/api/sources/bili/*` 提供搜索冷却时的扩展 DOM 搜索兜底，并复用同一有界近期 lane；回传结果进入 `discovery_candidates`
- `xiaohongshu_adapter` — 小红书扩展代理（被动收集 + 关键词搜索 + 创作者订阅 + `bootstrap_profile` 初始化画像任务，零后端爬取；task-result 进入 memory 前按已见 note key 跨任务去重）。search / creator 均在 inactive tab 执行；search 通过 MAIN-world bridge 只归一化页面自身 search API 的公开卡片字段，经同页 replay 缓存送入 isolated executor，DOM 作为 schema 漂移兜底，因此隐藏页不挂载虚拟列表也不会抢占用户当前页面；只有需要点击本人入口和受控滚动的 bootstrap 保持前台。legacy task claim 受动态来源开关、全局 scheduler、持久化抖动间隔和平台冷却四层门控；`xhs_task_runtime_state.rate_limit_strikes` 让独立风控轮次按 1/2/4…小时退避（24 小时封顶），同一活动冷却内不重复加 strike，冷却后的正常自动任务成功才重置。扩展 `risk-control.ts` 只上报结构化安全验证 / 操作频繁 / 429 结果，不上传页面全文。强信号赞 / 收藏由 MAIN-world `xhs-action-tap`（`obc-xhs-action`，与 token sniffer 隔离）在 like/dislike/collect/uncollect 写端点业务成功后网络层认定，adapter 声明 `tapAuthoritativeActions:{like,favorite,retraction}` 让 kernel 抑制对应 DOM 发射，事件 URL 拼 `…/explore/<note_id>` 与后端 `sources/identity_keys` note 键型互通（支持赞→撤销折价）
- `dy_tasks` — 抖音扩展任务队列（`bootstrap_profile` 初始化画像任务；发布 / 收藏 / 点赞 / 关注信号由扩展以用户浏览器登录态抓取，身份或分页不完整时任务行仍终结为 `completed`，同时在 `result_json.status="degraded"` 保留不完整语义和已经采到的 partial；完成 / 失败终态不可被迟到 partial 或重试回调覆盖；任务 poll 时标记 `in_progress`，CLI 可复用近期正常 / 在途 bootstrap，但不会复用已 `degraded` 的 completed 结果；`search` / `hot` / `feed` discovery 任务统一从 `https://www.douyin.com/` 首页开始，由 content script 模拟真实 DOM 操作触发搜索、热榜或推荐流加载，再被动收集页面自身发出的响应和已渲染 DOM；hot board 的 `group_id` 会作为 `seed_aweme_id` 透传，DOM / 被动监听不足时用已登录页面 related API bridge 拉取热点相关候选；三者分别回传 `dy_search` / `dy_hot` / `dy_feed`，并作为 `dy-plugin-search` / `dy-plugin-hot-related` / `dy-plugin-feed` discovery 来源）
- `yt_tasks` — YouTube 扩展任务队列（`bootstrap_profile` 初始化画像任务；观看历史 / 订阅 / 点赞由扩展以用户浏览器登录态读取 DOM 并分批回传；任务 poll 时标记 `in_progress`，CLI 可复用近期 bootstrap）
- `youtube.takeout` — Google Takeout 离线导入解析器，将 YouTube 观看历史 / 订阅 / 点赞转换为统一事件
- `YoutubeDiscoveryProducer` — 后端直连的 YouTube steady-state discovery loop；在 YouTube 平台族低于 quota 时调用 `yt_search` / `yt_trending` / `yt_channel`，并用 SQLite execution ledger 控制每日执行预算
- `twitter_adapter` — X (Twitter) 服务端 cookie 重放（`source_type="twitter"`，标签 `"X"`）；`XAdapter.fetch()` 是真实实现（非 stub），按 recipe 分发到 `discovery/strategies/x.py` 的 `XSearchStrategy`（画像关键词）/ `XForYouStrategy`（推荐流 For-You）/ `XCreatorStrategy`（账号订阅）。配套 `x_client.py` 的 `XClient`（封装默认运行时依赖 `twitter-cli`，lazy import + 只读 + 类型化错误；`openbiliclaw[x]` 仅作为兼容旧脚本的安装别名保留）、`discovery/x_normalize.py`（tweet → `DiscoveredContent`）、`x_tasks.py`（`x_creator_subscriptions` CRUD）、`storage/x_health.py`（源健康状态机）
- `zhihu_tasks` — 知乎扩展任务队列（`bootstrap_events` 事件 smoke + `search` / `hot` / `feed` / `creator` / `related` discovery）；插件在已登录知乎 tab 中读取浏览历史 / 收藏夹 / 动态点赞收藏，或调用 discovery 接口回传 `zhihu_*` 候选；`runtime.zhihu_producer.ZhihuDiscoveryProducer` 在知乎平台族低于 quota 时按 `source_modes` 入队任务，结果经 `sources.zhihu_tasks.zhihu_discovery_items_to_contents()` 写入 `discovery_candidates`
- `reddit_tasks` — Reddit 扩展任务队列（`bootstrap_events` 初始化信号 + fallback / 显式 `search` / `hot` / `subreddit` / `related` discovery）；插件在已登录 Reddit tab 中读取 saved / upvoted / subscribed 或同源 `.json` endpoint 回传 `reddit_*` 结果；`runtime.reddit_producer.RedditDiscoveryProducer` 在 Reddit 平台族低于 quota 时默认用 rdt-cli 按 `source_modes` 抓 discovery 候选，命令后端不可用或显式 `backend="extension"` 时入队插件 discovery 任务，结果经 `sources.reddit_tasks.reddit_items_to_contents()` 写入 `discovery_candidates`，producer 自身 fetch-only，不同步等待 LLM 评估
- `linuxdo_tasks` — Linux.do 扩展任务队列（`bootstrap_events` 的书签 / 点赞 / 阅读记录，以及 `search` / `hot` / `feed` / `creator` / `related` discovery）；`runtime.linuxdo_producer.LinuxdoDiscoveryProducer` 只负责 claim 关键词/seed、入队和归一化结果，插件在隔离的真实 `linux.do` tab 内执行同源 GET。个人 bootstrap 以 `/session/current.json` 的正面身份为门槛，`_t` 仅作为登录布尔且值不上传；结果只含规范化字段或结构化错误，Cookie、CSRF、原始响应和 challenge HTML 一律不进入后端
- `sources.bangumi_client` / `runtime.bangumi_producer` — 固定官方 `api.bgm.tv/v0` 的匿名只读 client 与 fetch-only producer；search 复用统一关键词，ranked/latest 维护按条目类型 cursor，三分支按 UTC 日条目预算和最小间隔执行，`429 Retry-After` 落 `bangumi_discovery_state` cooldown。Subject 归一化后写 `discovery_candidates`，公开用户名的收藏只在显式 guided init/fetch smoke 中转为事件；默认匿名，可选个人令牌（Bearer）读取私密收藏，令牌被拒（401/403）自动降级匿名；没有扩展 task queue、无 Cookie、无站内写方法。扩展在 `bgm.tv` / `bangumi.tv` 上仅上报公开 uid + 用户名做账号身份识别（`POST /api/sources/bangumi/identity`，含 uid↔用户名交叉校验），不采集浏览行为、不上传令牌
- `sources.weibo_client` / `sources.weibo` / `runtime.weibo_producer` / `sources.weibo_tasks` — 项目自有 `httpx` 匿名公开 client、fail-closed normalizer、fetch-only producer 与 init-only 账号任务队列。client 固定 `trust_env=false` 国内直连，为移动 H5 search / creator 在内存申请短期 visitor `SUB`；hot endpoint 只产 query seed，随后搜索并只接纳真实 `content_type="post"`。个人收藏、关注和 mentions 由同源扩展任务读取，账号 key 和 scope 去重后进入画像事件；search / hot / creator 复用统一关键词、份额 pool gate、UTC 日预算、成功 cadence、持久化 `429 Retry-After` cooldown 和共享 candidate pipeline；schema drift / visitor reject / 429 都是 typed 终态。后端不接收用户 Cookie，普通微博页不做行为采集，不提供 native-save 或站内写方法
- `web_adapter` — 通用 Web（Playwright CDP + LLM 内容抽取）
- `SourceRecipe` — 源任务持久化与分发

### Recommendation Engine (`recommendation/`)
- 推荐排序与朋友式推荐表达生成；统一从候选池读取
- 文案生成与 admitted backlog 解耦：canonical `copy_ready` 使用全部 serve gate 但不套 topic 展示窗口。正数水位生成量为 `max(copy_ready_target-copy_ready, min(pool_target-available, admitted_pending_available))`，再受全部 pending 与单批上限钳制；锁内先领取能净增 topic 展示窗口的行，公开目标达到后只维持 copy-ready 水位。serve/feedback/delight/maintenance 只发非阻塞 refill 通知，provider 工作由 coordinator 承担并在 expression lock 内复核缺口。`0` 是 legacy drain-all 回滚，任何模式都不放松非空文案硬门。
- 惊喜推荐复用普通推荐的 copy-ready 与 canonical `seen_items` 状态门：`pool_expression / pool_topic_label` 未同时生成，或身份已经看过时，候选不进入惊喜打分、动态阈值样本、计数或 pending 出口。正式文案就绪后才复用 Evo 的 `relevance_score` 打分，并由条件写入原子同步 `delight_reason / delight_hook`；pending API、CLI 与 runtime stream 继续校验精确快照。evaluator 的内部 `relevance_reason` 永不作为惊喜状态或 UI 推荐理由；旧版错写快照在正式文案就绪后由后台 backfill 修复。普通推荐只在高分行已被 profile-aware 惊喜打分并同步快照后让出该行。移动 Web、桌面 Web 与插件的“×”统一调用 `dismiss`：它不是临时隐藏，而是把 canonical identity 写入 `seen_items` 后永久消费该惊喜。
- 推荐列表、换批、pending delight 单条/批量及 runtime delight 事件都增量透传 `published_at` / `published_label`。桌面 Web、移动 Web、扩展 popup 与 CLI 按同一规则消费：精确时间优先并转本地相对日期，来源标签兜底，双空值不渲染；API 层不重写相对时间。
- Bangumi 目录指标 `rating_score / rating_count / source_rank` 与 `favorite_count` 贯穿 subject normalizer → `DiscoveredContent` → `discovery_candidates` → `content_cache` → recommendation/delight API → 三端。评分人数不是评论数、评分不是点赞；无真实值时保持 0 并整段隐藏。
- 推荐、delight 与保存列表出口共享 `item_key / content_id / source_platform / content_url / content_type` 身份契约；`content_cache.item_key` 对非空 canonical identity 使用 partial unique index，并用独立普通索引支持 lookup，`recommendations.item_key` 引用同一 identity。插件 side panel、桌面 Web 与移动 Web 的卡片先 POST `/api/saved/{list_kind}`，保存页再用 `/sync` + durable task poll 做显式平台写入；默认关闭的 `saved_sync.auto_sync_enabled` 只决定本地保存后是否创建后台任务。手动同步对当前 adapter 支持且未处于已同步 / 同步中的项始终可用；仅 `unsupported_adapter_missing` 可在 adapter 注册后重新进入单项/批量快照，`unsupported_content_type` 等真实能力限制继续显示为仅本地保存。本地 `/remove` 永不反向删除平台记录。
- `/api/feedback` 的 event ledger 与 recommendation 展示字段不是跨表原子写：先按 `request_id` commit durable feedback event，再以独立 commit 更新 recommendation projection。第二步失败时响应失败；同 `request_id` 重试会命中 duplicate event、核对 durable payload 后重做 projection，修复 commit gap；冲突 payload 返回 409。画像学习由上述 content-feedback durable consumer 异步领取，不扩大 HTTP 成功边界。
- `/api/recommendation-click` 会保留 `content_id / content_url / source_platform`：插件、移动 Web 或桌面 Web 打开推荐内容后，后端把点击写成对应来源的统一事件和 `recommendation_click` 强画像信号；只传 `recommendation_id` 时会从 `recommendations + content_cache` 回填跨源字段，避免 YouTube / 抖音等 ID 被套成 B 站 URL。
- `PoolCurator` 五维评分（relevance · publication temporal bonus · topic_fatigue · source_monotony · serendipity）；第二维只奖励发布时间明确且高置信的 `breaking/current/versioned`，常青、历史、未知或缺时间内容为中性，完全不读取 `discovered_at`。三态 temporal eligibility 独立于评分：年龄只能触发复审，grounded deadline / terminal state 才能 hard expire；它与 bonus 共用 `discovery.temporal` 的类别策略、置信门和时间解析。每次评分 best-effort 写入 aggregate-only Top10/50/100 no-bonus shadow，观察排序 churn 与 class/source/age 偏移，不改变 eligibility、分数、MMR 或 serving
- v0.3.1 双轴 fatigue：`recent_topic_keys` (细) + `recent_topic_groups` (粗) 取 max；曲线 `count^1.5/len*5`，count=2 即触发 0.47 强抑制
- 新兴趣 amplification guard：刚确认的探针兴趣会用 domain/specific/topic key 形成 guard，`PoolCurator` 做 24h rolling budget 软降权，最终批选择做 `max(1, floor(limit*0.25))` 硬上限
- `_merge_topic_supergroups` — serve 时基于 embedding 把 `动漫杂谈/补番/解说` 等近义 topic 合并为同一聚类
- `prewarm_supergroup_embeddings` — refresh tick 后台预热所有池中 topic_group embedding，让 reshuffle 跑全 cache hit
- `PoolServeSnapshot` — 专属 serve DB worker 先清退已超过 temporal eligibility 的 fresh 行，再在一个只读事务内统一读取 readiness、候选窗口、平台补位、持久化 `seen_items` 和 curator 信号；最终 persist 的同一写事务再次复核，Engine 只返回实际提交条目。MMR/多样性纯函数与排序规则不变
- 推荐历史快路径 — 默认历史查询保留已过期记录；仅面向“尚待展示”的 API/OpenClaw actionable 读取、未读计数与主动通知复用 temporal eligibility，并在 limit 前过滤。legacy pool 补分类写回 temporal 元数据后立即退役过期行，cached-backfill 只读取 fresh/eligible 行且不得丢 temporal 字段
- `serve_with_result()` — 返回 items、提交后扣减库存与分阶段耗时；推荐历史和 shown 在独立短事务中原子提交，API 先广播结果库存，再 detached 精确收敛
- 换批是默认硬去重动作：桌面 Web、移动 Web 与扩展 side panel 都提交当前卡片 ID，后端继续叠加推荐历史和 `seen_items`；成功响应只写一条 `reshuffle` 批次事件。桌面端不再暴露“换一批时忽略当前”开关，也不会逐卡提交 `dismiss`。CLI 没有持久卡片列表，只复用后两层去重。
- 平台定向作用域（PC Web 平台 Tab）：`serve / reshuffle / append` 的可选 `source_platform` 让 snapshot 只装载该 canonical 平台的候选并跳过跨平台保底补位，其后的 curator、MMR、多样性、文案、持久化与 shown 提交完全复用同一实现；返回前校验并丢弃跨平台泄漏行（记 ERROR）。数据流为 `PC Web tab → POST {reshuffle,append}.source_platform → RecommendationEngine → Storage 平台候选`，配套只读 `GET /api/recommendations/platform-availability` 提供 Tab 库存徽标。Tab 集合取“启用配置 ∪ 正库存 ∪ 已加载卡片”；首屏 `/api/config` 瞬断时按 1s / 2s / 4s / 8s 有界恢复，页签转入后台会取消重试定时器，恢复可见后重新水合并收敛。库存与选片共用同一份 canonical available 行集合，`total_available == sum(by_platform)`。移动 Web、扩展与 CLI 无平台 Tab，继续走不带平台的兼容路径。桌面保存页徽标另走首屏 `saved list → total → sidebar badge` 水合链，完整列表刷新用独立 generation fence 保证旧响应不能覆盖新状态。
- 个性化专题生成

### Runtime (`runtime/`)
- 系统生命周期管理和服务编排
- `runtime.dialogue_reply_scheduler` — FastAPI app 生命周期内稳定的对话执行 lease 与 durable reply 单 worker。它不属于可替换 `RuntimeContext`：所有 production reply 在 lease admission 后动态解析当前 dialogue/Soul owner，热重载 pause+drain 后才发布新 context；durable turn 以 SQLite pending row 为权威队列，严格 rowid 顺序、瞬态原位退避、startup 全量分页恢复，visible terminal 用 pending CAS 发布一次。它与 reply 后的 `DialogueSettlementQueue` 分属两条独立 lane。
- `runtime.image_fetch` — FastAPI app 生命周期内稳定的封面抓取 coordinator。proxy foreground miss 与 refresh background prefetch 共用 total 4 / background 3 的 Condition priority gate，按归一化 cache key singleflight；前台加入 queued background 同 key 会 promotion 到前台并采用更新签名 URL。waiter cancellation 不取消 owned upstream；磁盘 I/O 在线程中完成，cache hit 不占网络槽，落盘使用 same-dir tmp+fsync+replace。热重载只给新 controller 重绑同一实例，shutdown 先停 producer 再关闭 lane。
- 降级模式启动：生产 `create_app()` 遇到 LLM registry 配置错误时保留 `/api/ping`、`/api/health`、`/api/qr-info`、`/api/config`、`/api/runtime-status`、`/api/runtime-stream`，精确放行 `/api/config/probe-service`、`/api/config/discover-models`、来源比例建议及 `/`、`/web`、`/setup`、`/m` 静态恢复 surface 与资源；草稿 probe 从提交配置临时建 registry 并经过稳定 total gate，不依赖失败的 active registry。`/api/ping` 仅在降级时附带 reason / issues，桌面 Web 以此先行识别恢复态、停止业务 hydration，再读取配置并自动打开模型设置。修复配置写盘后复用 degraded context 的 stable 层原子构造完整 swappable runtime、同步解除 503 guard 并启动后台任务，无需重启；构造失败则回滚并继续保持恢复态。其他业务 API 在修复前返回 503，避免半初始化 runtime 继续跑推荐/发现链路
- 配置热重载：`RuntimeContext` 重建 registry / service / engine 时会注入同一份 `[llm.instances]`、`default_chain` 与 `[llm.routes.*]`。设置保存先事务性落盘；受保护对话 / 结算 / event lane 空闲时同步切换，忙时返回 202 并交给 app-owned latest-wins 配置应用队列，所有 rebuild 共享单一 handoff lock。队列成功/失败通过 runtime stream 回执，最新失败恢复 last-good，已有更新修订时不会被旧 rollback 覆盖。`data_dir` 不进入本进程热切换：路径变化时排队配置副本被固定回 active data dir、202 标记需要重启，避免绕开进程已持有的 canonical data-dir lock；其它字段仍可正常应用。热重载后的正向兴趣和避雷 speculator tick 都作为 detached task 注册到 `BackgroundTaskRegistry`，分别读取 `probe_feedback_history` / `avoidance_probe_feedback_history`，不阻塞配置响应
- `AutoUpdateService` — 后端自动更新只查询 GitHub `/tags` 并过滤 `backend-v*`（兼容 legacy `v*` / 裸 semver），明确忽略 `extension-v*`；当前 GitHub Releases 由扩展 artifact 占用，不能用 `/releases/latest` 判断后端源码是否最新
- `GitHubStarCountService` — 桌面 Web / 扩展只读公开 `/api/project-stats`；服务端经 `[network].mode` 海外网络策略访问 GitHub repo metadata，持久化 12 小时 count + ETag，403 / 429 按响应头退避，网络或上游错误只投影 stale cache / unavailable 的本地 200，避免每个浏览器实例匿名直连并制造失败资源日志
- `runtime.autostart` — 当前用户作用域开机自启动 manager：macOS LaunchAgent、Windows HKCU Run（源码 `pythonw + .pyw` / 冻结包直接 `OpenBiliClaw.exe`，兼容旧双路径项）、Linux XDG autostart；`reconcile()` 由 CLI 与冻结桌面入口共用，API / CLI / 插件设置页通过 `GET /api/autostart-status` 与 `POST /api/autostart/apply` 管理，带 env-managed / `config.local.toml` shadow guard，并用开启「先写 config 后注册 OS」、关闭「先注销 OS 后写 config」的方向化事务避免崩溃残留
- `runtime.ollama_supervisor` — `start` 启动前复用的 Ollama 预检 helper；从所有启用的 chat 实例和独立 embedding 配置判断是否需要 Ollama，归一化 endpoint 并剥离 `/v1`，仅在默认本机 `localhost:11434` 缺 daemon 时尝试后台拉起 `ollama serve`。桌面 macOS 安装包的随包 runtime 必须来自官方 `Ollama.app`，并携带 `ollama + llama-server + lib*.dylib/.so + mlx_metal_*`，打包阶段拒绝 Homebrew 单主程序或缺关键动态库的 runtime，避免 embedding runtime 半可用；图形化 init 在 embedding provider 已配置时还会复用真实 probe 作为硬前置，防止首轮画像在本地向量服务 500 时悄悄降级。
- `ContinuousRefreshController` — 管理补货、来源 producer 与 API daemon 的 `CandidateEvalCoordinator` 子任务；幂等 `run_startup_maintenance()` 是 host 暴露服务前的统一零 LLM 库存恢复边界。API daemon 的 `run_forever()` 先调用它再启动 delight/candidate/background loops，pipeline 的单次 enqueue callback 是 coordinator 唯一即时唤醒；OpenClaw direct bootstrap 不运行该 loop，因此不 attach dormant candidate / expression coordinator，而将 `recommend(refresh_if_needed=True)` 的首轮 source/evaluation 限为 4（fetch oversample=1、min eval batch=4、inline evaluator=1），在 commit 后同步 drain ≤4 expression copy、禁用本次 split retry。库存维护使用独立单线程 worker/连接，每事务最多 50 行、每 tick 最多 8 批，批间释放 SQLite 写锁并让出 event loop；75ms 锁冲突直接延后。fresh history 为空时该 operation 直接 serve 首 batch 已复制的 canonical subset；其 one-shot callback 不创建 prewarm/provider background task，剩余 pending 由后续请求续补。热重载的新 controller 也先恢复；同一 controller 后续进入 loop 不重复维护。
- `runtime.source_incremental_sync.SourceIncrementalSync` — controller 内独立的扩展在线账号信号回拉决策器。它不走 `_llm_work_allowed()`；`scheduler.source_incremental_enabled` 默认 `false`，关闭时在 presence 检查前直接返回，旧配置没有该字段也不会自动入队。显式开启后才继续要求 `scheduler.enabled`、画像 ready、init inactive 与 `PresenceTracker.is_present(grace)`，并按全局 24 小时 / 逐源覆盖运行；抖音逐源仍默认 `0`。XHS→抖音→YouTube→知乎→Reddit→Linux.do 的 round-robin cursor 与 attempt/active 投影写在原子 `source_bootstrap_state.json`，六张任务表的任一 bootstrap `pending/in_progress` 都阻止新建；进程内 decision lock 覆盖热重载取消后仍在跑的 worker thread，而 runtime / CLI fetch / guided init 共用的 SQLite `BEGIN IMMEDIATE` admission transaction 把六表 active scan 与真实 insert 收进同一写事务，跨 `Database` facade / 进程也不能同时创建，`force` 同样不绕过。崩溃窗口收养会把任务 `created_at` 同步为 attempt/cursor，终态后仍等待完整周期。仅 `created=true` 且 id 非空时 stamp + EventHub kick；明确预算耗尽可继续下一到期源，异常不推进。guided init 先持久预定 run，再做来源 opt-in 热重载；证据充分结果 seed attempt，失败留给 post-init 首轮自愈。手动 init / fetch 与 discovery 不受总开关影响；该链路复用已安装扩展登录态，不承诺 browser-free pull。
- `runtime.source_incremental_sync.SourceIncrementalSync` — controller 内独立的扩展在线账号信号回拉决策器。它不走 `_llm_work_allowed()`；`scheduler.source_incremental_enabled` 默认 `false`，关闭时在 presence 检查前直接返回，旧配置没有该字段也不会自动入队。显式开启后才继续要求 `scheduler.enabled`、画像 ready、init inactive 与 `PresenceTracker.is_present(grace)`，并按全局 24 小时 / 逐源覆盖运行；抖音逐源仍默认 `0`。XHS→抖音→YouTube→知乎→Reddit→V2EX 的 round-robin cursor 与 attempt/active 投影写在原子 `source_bootstrap_state.json`，六张任务表的任一 bootstrap `pending/in_progress` 都阻止新建；进程内 decision lock 覆盖热重载取消后仍在跑的 worker thread，而 runtime / CLI fetch / guided init 共用的 SQLite `BEGIN IMMEDIATE` admission transaction 把六表 active scan 与真实 insert 收进同一写事务，跨 `Database` facade / 进程也不能同时创建，`force` 同样不绕过。崩溃窗口收养会把任务 `created_at` 同步为 attempt/cursor，终态后仍等待完整周期。仅 `created=true` 且 id 非空时 stamp + EventHub kick；明确预算耗尽可继续下一到期源，异常不推进。guided init 先持久预定 run，再做来源 opt-in 热重载；证据充分结果 seed attempt，失败留给 post-init 首轮自愈。手动 init / fetch 与 discovery 不受总开关影响；该链路复用已安装扩展登录态，不承诺 browser-free pull。
- `EventProcessingScheduler` — app-owned generic/content-feedback 调度器，`FeedbackBatchScheduler` 只保留兼容别名。HTTP/source 入口只落 durable event + wake；两个 consumer 以 stable-ID 和各自 cursor 调 `checkpointed_enqueue_batch()`，把 buffer+cursor 原子发布到同一 `pipeline_state.json`，再用 `tick_if_buffered()` 消费。首次 app startup 的 fence 与 task admission 是同步准备边界，真正 recovery 不被 lifespan await；因此 pending buffer 的 provider 401/慢调用/永不返回不能挡住 listener。scheduler owns task，shutdown cancel+gather；热重载仍同步 pause+drain+recover 后再 rebind。5 秒 periodic scan、dirty 补跑与 owner cutover 覆盖丢 wake / 重启。独立周期画像维护才调用完整 `tick()`；旧 feedback batch 只在 `unified_interest_line=false` 回退时执行。
- `/api/runtime-status` / `runtime-stream` — 对插件、移动 Web 和桌面 Web 发布同一套候选池库存口径：`pool_available_count` 只表示当前可立即被 `serve()` 消费的内容，`pool_raw_count` 表示基础 fresh 素材加待评估 raw candidates，`pool_pending_count` 表示已有素材但仍缺评估、文案、分类或可跳转链接；命中持久化 `seen_items` 的素材不算 pending。`pool_pending_eval_count` / `pool_evaluated_pending_count` 分别拆出待 LLM 评估和已评估待 admission 的数量；`pending_signal_events` 只表示 discovery refresh 游标后的新动作数量，用于下一次统一补货判断，不会由事件入口直接执行 refresh。`event_lane_depth` 只表示 dirty wake 的 `0/1`，不是 SQLite backlog。前端只把 available 显示为“可换”，pending 显示为“正在整理”；后台补池的 source deficit 也使用 available-by-source，而 raw trim / headroom 使用 all-raw-material by-source。推荐读取、换一批和续页消费候选池后会立即广播新的 `refresh.pool_updated` 快照，使其它已打开客户端收敛到扣减后的库存，而不重载推荐列表。业务事件空闲 20 秒时 stream 由同一 writer 发送 `runtime.heartbeat`，避免代理/浏览器把健康 idle socket 清掉；桌面 close 状态明确为 reconnecting，正常 visibility 后台关闭不等于 daemon 离线。
- `Desktop Web recommendation hydration` — 首屏或空列表才读取 `/api/recommendations`；已有卡片时切回标签页、配置应用和其它后台水合只同步 runtime / 库存状态，跳过可能调用 `serve()` 补池的推荐 GET。明确手动刷新仍允许替换列表，滚动自动续页仍只由前端开关和用户滚动 gate 控制。
- `_publish_probe_if_available` — proactive push 循环中的探针仲裁器；从正向兴趣和避雷探针池中每轮最多选一条，正向探针事件携带 `probe_mode/challenge`，普通 `near` 和挑战探针使用独立 active 额度；只投递 `active` 候选，且只有推送到订阅者后才通过原子 runtime state 更新记录 domain / axis / distance history，避免后台旧快照覆盖用户刚处理的探针反馈
- `background_llm_work_allowed()` — 共享 gate predicate；`scheduler.enabled=false` 会暂停 daemon-owned 后台 LLM / embedding 工作，`scheduler.pause_on_extension_disconnect=true` 时还要求浏览器插件 presence 在线或仍处于断开宽限窗口。`ContinuousRefreshController` 还会叠加 `scheduler.llm_budget_max_calls / llm_budget_window_seconds` 自设配额：后台 LLM 请求计数（`LLMConcurrencyGate.background_call_count()`）达到窗口上限后自动暂停，直到窗口滚动或用户调大 / 清零上限（手动 CLI / API 不受影响）。该 gate 覆盖 refresh、candidate eval、pool precompute、soul pipeline、xhs/dy/youtube/zhihu producer、proactive push、低频 account sync、startup one-shot 和 OpenClaw direct bootstrap；首个完整画像尚未落盘或 guided init 活跃时（`InitCoordinator.init_active()`）也返回 False，一处暂停所有后台循环，防止 account sync 在用户点击初始化前抢先分析/重复落库，让 init 的显式 analyze / build / backfill 独占（init 自身直调 `soul_engine` / `run_init_backfill`，不查该 gate）。阶段 2/3 另以 task-local scope 绕过空库存 maintenance admission，但仍受 total gate；阶段 4 不继承该 scope，靠 supply / evaluation / expression 正常补货优先级完成
- `_enforce_pool_cap` 每 tick 最多进入 8 次 bounded `maintain_pool_inventory(max_mutations=50)`：每个短连接 `BEGIN IMMEDIATE` 内按 canonical readiness + `seen_items`/链接守卫恢复合格 `suppressed` 历史行并保护新 canonical available，再统一规划 stale / explore / topic / source / 跨表 raw ceiling victims。source/topic 可延期，`evaluating` / token-owned candidate 不可裁，未领取 victim terminalize 为 `trimmed_capacity`，不变量失败整批回滚。恢复使用内存 source/topic 计数，消除逐行 window-function 重扫；`has_more` 驱动下一批。普通 tick 的 readiness fingerprint 未变化时跳过 ranked 扫描，10 分钟安全巡检和 force/post-refresh 路径仍强制运行。BEGIN 锁冲突短等待后延后，不制造零值 result
- `InitCoordinator`（`runtime/init_coordinator.py`）— 图形化引导初始化的生命周期所有者：`init_runs` 持久化状态机 + 单写者进度事件（`_write_lock` 串行化心跳 / 进度 / 取消 / 终态写入，首个终态后拒绝全部迟到写）+ `BEGIN IMMEDIATE` 单飞 + 启动 reconcile（崩溃残留判失败）+ 协作取消 + bootstrap task 归属（供写者门控放行 init 自己的 task-result）。流水线以阶段 3 的完整画像落盘为严格屏障，之后阶段 4 才能使用该画像；阶段 2 的 chunk fan-out 对齐 `[llm].concurrency`，默认墙钟按 300 秒/并发波次 + 固定 300 秒恢复预留伸缩，真实完成数、已用时与本轮上限持续落状态，临时 429 有界重试但余额不足立即失败；同波硬失败会取消并 drain sibling，reasoning-only length 仅对该 chunk 提升一次输出预算。配套 `ContinuousRefreshController.run_init_backfill` 持 `_refresh_lock` 串行执行发现、评估、表达 drain 和 canonical pool 校验，普通完成即代表至少一条推荐可浏览。`InitPrereqs` 提供 TTL 缓存的 chat / B站 / 平台前置探测；v0.3.118+ B 站登录只在本轮勾选 B 站时才是硬前置，`/api/init-status` 继续下发状态但不再全局阻塞 `can_start`。共享流水线 `cli.run_guided_init` 详见 [init 模块文档](modules/init.md)
- `AccountSyncService` — B 站历史记录、收藏夹、关注列表同步，以及可选的 X likes/bookmarks 定时增量（`resolve_x_cookie` 有 cookie 时装配）；daemon `sync_if_due()` 只有完整画像已存在且 guided init 不活跃时才通过共享 gate，使用历史游标 + 已见 bvid/mid/tweet-ID 集合只接收新增账号事实，新增事件先经 48h 跨源去重。生产 composition 注入 `EventIngressService` 后，画像就绪态只做 batch durable commit + wake，后续由 generic cursor owner 进入 `ProfileUpdatePipeline`；不在 account-sync request 内 await pipeline/LLM。未注入 ingress 的第三方旧 embedder 保留兼容路径；显式运维调用 `sync_now()` 也保留空画像 auto-bootstrap。
- `/api/sources/{xhs,dy,yt,zhihu,reddit,linuxdo}/task-result` — 插件 bootstrap / search partial / final 结果完整保留在任务表。六源崩溃安全两阶段完成先冻结首个 canonical final，再从冻结行重放 durable event、原子有界 seen-key 与来源投影，最后 terminal flip；首个 final 后的 partial/final/fail/rate-limit 都不能改写 staged row，丢响应后由普通 lease reclaim 修复。周期任务带 `incremental=true`，init 外事件归 generic owner；init-owned 结果只落事实并由阶段 2/3 统一建模。XHS / 抖音 / YouTube / 知乎 / Reddit / Linux.do 都用 `source_bootstrap_state.json` 跳过跨任务已见 stable identity；Linux.do canonical identity 为 `topic:<topic_id>`，Reddit post/comment/subreddit/user 分型，comment URL fallback 不会把 post id/title 当 comment id。discovery 类型仍只转 raw candidate。
- `runtime-stream` — 浏览器扩展 background 以 `client=background` 连接后，后端先推送 `xhs_login_state_sync_requested` / `zhihu_login_state_sync_requested`，扩展只读取本地浏览器 Cookie store 中 `web_session` / `z_c0` 是否存在，并分别向登录态端点回传布尔值；这一步不打开、刷新或请求平台页面。若后端本地没有 B 站 Cookie，还会推送 `bilibili_cookie_sync_requested`，扩展立即通过 `/api/bilibili/cookie` 回传当前浏览器 Cookie；后端持久化 Cookie、热重载 runtime 组件，并重新启动 refresh / account sync / auto update 后台任务，避免热重载取消后台循环后小红书 / 抖音 producer 停止；重复同步相同 Cookie 时不再重建 runtime，避免打断正在等待扩展回写的抖音 discovery。B 站扩展搜索兜底任务入队后会通过同一 stream 广播 `bili_task_available` 唤醒扩展 poll，扩展在后台打开真实 B 站搜索页、抓渲染后的 DOM 结果并 POST 回 `/api/sources/bili/task-result`；六源周期账号回拉也只在该 background presence 在线时入队并用对应 `<source>_task_available` 唤醒已有 dispatcher。知乎事件 / discovery 任务入队后会广播 `zhihu_task_available`，扩展打开带 `openbiliclaw_zhihu_task` 标记的已登录知乎任务 tab 并回写 `/api/sources/zhihu/task-result`，其中 `bootstrap_events` 初始化 / 周期回拉 / 事件 smoke 使用前台 tab，search / hot / feed / creator / related discovery 使用后台 tab；Reddit bootstrap、命令后端 fallback 和显式 `backend="extension"` 的 discovery 任务入队后会广播 `reddit_task_available`，扩展打开带 `openbiliclaw_reddit_task` 标记的已登录 Reddit 任务 tab 并回写 `/api/sources/reddit/task-result`，其中 `bootstrap_events` 读取 saved / upvoted / subscribed，search / hot / subreddit / related discovery 读取同源 `.json` endpoint；默认 Reddit discovery 在 rdt-cli ready 时不走 stream，而由命令后端完成。本机 `/api/extension/e2e/run` 也复用同一 stream 投递 `extension_e2e_run`，让已安装扩展打开 / 复用真实抖音、小红书、X 标签页执行白名单 DOM 操作；复用同域 tab 时先导航回平台稳定入口，事件仍由 content collector 自然进入 `/api/events`，runner flush buffer 后再由后端匹配。若 `[sources.douyin].enabled=true` 且后端没有环境变量或 `data/douyin_cookie.json`，会推送 `douyin_cookie_sync_requested` 并通过 `/api/sources/dy/cookie` 回传抖音 Cookie。后续推荐、惊喜、画像更新和探针确认仍复用同一条 WebSocket 事件流；`interest.probe` / `avoidance.probe` 只有实际进入至少一个 stream 订阅者队列后才写入对应 domain / axis 冷却状态，正向 probe 还会写入 `probed_distance_bands`，并在 payload 里暴露 `probe_mode/challenge`；正向和负向 probe 通过 `last_probe_kind` 每轮最多投递一条；同一连接也驱动 `PresenceTracker`，服务端 reader 会 `receive()` 检测 idle disconnect，避免浏览器断开后 presence 卡住
- `runtime-stream` 的 Linux.do 任务唤醒 — `linuxdo_task_available` 唤醒独立 dispatcher；它通过 authenticated fetch 领取 `/api/sources/linuxdo/next-task`，为每个任务创建隔离的真实 `linux.do` tab，并在超时或完成后仅关闭自己创建的 tab。content script 只有在 URL 携带任务标记时运行 executor，全部上游网络请求均为同源 GET；最终只向 `/api/sources/linuxdo/task-result` 回传归一化 topics / counts 或结构化错误。`_t` 值、其他 Cookie、CSRF 数据、原始 JSON/HTML 与 challenge 页面不进入 stream 或任务结果。
Linux.do 默认端到端总等待为 32.5 分钟：pending 最多约 3 分钟等在线扩展领取；`in_progress` 后按页数、输入宽度与节流计算最多约 29 分钟执行窗口，再留 30 秒结果余量，claim lease 约 35 分钟，共享 dispatcher mutex 的 stale 驱逐窗口为 36 分钟。显式 CLI/env 等待值是从入队开始计算的总硬上限，较小值可能截断已领取任务。dispatcher 在执行前把 task/tab/deadline 写入 session storage；MV3 worker 重启时先恢复 runner 再启动 polling，存活任务 tab 的回传由恢复后的 handler 接住，不重跑站点 GET。guided init Stage-1 基础预算仍为 30 分钟；默认预算下 Linux.do-only 至少给 32.5 分钟，Linux.do 与其它来源并选时给 62.5 分钟，显式 override 不扩。bootstrap 部分 scope、discovery 分页或多输入中途失败且已有有效 items 时冻结为 `degraded`；个人事件继续投影、公开 topic 继续入候选管线，但 bootstrap `failed/degraded` 均不进入 6 小时任务复用。
- `/api/sources/{xhs,dy,yt,zhihu,reddit,v2ex}/task-result` — 插件 bootstrap / search partial / final 结果完整保留在任务表。六源崩溃安全两阶段完成先冻结首个 canonical final，再从冻结行重放 durable event、原子有界 seen-key 与来源投影，最后 terminal flip；首个 final 后的 partial/final/fail/rate-limit 都不能改写 staged row，丢响应后由普通 lease reclaim 修复。周期任务带 `incremental=true`，init 外事件归 generic owner；init-owned 结果只落事实并由阶段 2/3 统一建模。XHS / 抖音 / YouTube / 知乎 / Reddit / V2EX 都用稳定 identity key 跳过跨任务已见事件；V2EX 额外按 Topic 聚合 Reply 并写入 Node affinity。discovery 类型仍只转 raw candidate。
- `runtime-stream` — 浏览器扩展 background 以 `client=background` 连接后，后端先推送 `xhs_login_state_sync_requested` / `zhihu_login_state_sync_requested`，扩展只读取本地浏览器 Cookie store 中 `web_session` / `z_c0` 是否存在，并分别向登录态端点回传布尔值；V2EX 只请求名为 A2 的 Cookie 并检查对象是否存在，代码不访问其 `value`，向 `/api/sources/v2ex/login-state` 只回传 `logged_in` 布尔值，不保存或上传 Cookie 值；这一步不打开、刷新或请求平台页面。若后端本地没有 B 站 Cookie，还会推送 `bilibili_cookie_sync_requested`，扩展立即通过 `/api/bilibili/cookie` 回传当前浏览器 Cookie；后端持久化 Cookie、热重载 runtime 组件，并重新启动 refresh / account sync / auto update 后台任务，避免热重载取消后台循环后小红书 / 抖音 producer 停止；重复同步相同 Cookie 时不再重建 runtime，避免打断正在等待扩展回写的抖音 discovery。B 站扩展搜索兜底任务入队后会通过同一 stream 广播 `bili_task_available` 唤醒扩展 poll，扩展在后台打开真实 B 站搜索页、抓渲染后的 DOM 结果并 POST 回 `/api/sources/bili/task-result`；六源周期账号回拉也只在该 background presence 在线时入队并用对应 `<source>_task_available` 唤醒已有 dispatcher。V2EX 任务页带 `openbiliclaw_v2ex_task` marker，扩展读取 member/replies/favorites/nodes 页的渲染 DOM 后回写 `/api/sources/v2ex/task-result`；知乎事件 / discovery 任务入队后会广播 `zhihu_task_available`，扩展打开带 `openbiliclaw_zhihu_task` 标记的已登录知乎任务 tab 并回写 `/api/sources/zhihu/task-result`，其中 `bootstrap_events` 初始化 / 周期回拉 / 事件 smoke 使用前台 tab，search / hot / feed / creator / related discovery 使用后台 tab；Reddit bootstrap、命令后端 fallback 和显式 `backend="extension"` 的 discovery 任务入队后会广播 `reddit_task_available`，扩展打开带 `openbiliclaw_reddit_task` 标记的已登录 Reddit 任务 tab 并回写 `/api/sources/reddit/task-result`，其中 `bootstrap_events` 读取 saved / upvoted / subscribed，search / hot / subreddit / related discovery 读取同源 `.json` endpoint；默认 Reddit discovery 在 rdt-cli ready 时不走 stream，而由命令后端完成。本机 `/api/extension/e2e/run` 也复用同一 stream 投递 `extension_e2e_run`，让已安装扩展打开 / 复用真实抖音、小红书、X 标签页执行白名单 DOM 操作；复用同域 tab 时先导航回平台稳定入口，事件仍由 content collector 自然进入 `/api/events`，runner flush buffer 后再由后端匹配。若 `[sources.douyin].enabled=true` 且后端没有环境变量或 `data/douyin_cookie.json`，会推送 `douyin_cookie_sync_requested` 并通过 `/api/sources/dy/cookie` 回传抖音 Cookie。后续推荐、惊喜、画像更新和探针确认仍复用同一条 WebSocket 事件流；`interest.probe` / `avoidance.probe` 只有实际进入至少一个 stream 订阅者队列后才写入对应 domain / axis 冷却状态，正向 probe 还会写入 `probed_distance_bands`，并在 payload 里暴露 `probe_mode/challenge`；正向和负向 probe 通过 `last_probe_kind` 每轮最多投递一条；同一连接也驱动 `PresenceTracker`，服务端 reader 会 `receive()` 检测 idle disconnect，避免浏览器断开后 presence 卡住
- `runtime-stream` 的 X 身份恢复 — `[sources.twitter].enabled=true` 时每次 background stream 建连都会推送 `x_cookie_sync_requested`，扩展立即把当前 `x.com` Cookie 回传 `/api/sources/x/cookie`；这与启动、Cookie change listener 和小时 alarm 兜底并存，不打开或刷新平台页。
- `/api/image-proxy` — 移动 Web 和扩展 side panel 的推荐、惊喜和消息封面图统一走 `UI → cache-first app-owned coordinator → 白名单 CDN → atomic disk cache → UI`。命中在线程中读取且不占抓取槽；miss 与 refresh 预取共享 total 4 / background 3、前台优先和 cache-key singleflight。后端在响应前完成 URL、每跳 redirect、Content-Type 和 10MB 实际字节校验，保持 `X-Image-Cache: hit|miss`；网络日志只含 host/cache hash/error kind，不含签名 URL/token

### TLS Proxy (`tls_proxy.py`)

- 默认关闭的传输边缘；`create_tls_proxy_server()` 在返回前完成证书/SAN 校验、SSL context
  加载和 socket bind，CLI 只在成功后创建 `serve_forever` daemon thread。
- 插件 endpoint 设为 HTTPS 后，手机版二维码及其 `/api/qr-info` LAN-IP 探测沿用同一 scheme；
  未知 scheme 只会安全回落 HTTP，不会把固定明文请求误发到 TLS listener。
- 请求侧解析 authority 后比较 Web Origin 与 Host 的 host+port；响应侧保留重复 header、过滤
  hop-by-hop header，并给 `Set-Cookie` 补 `Secure`。WebSocket 在 101 后进入双向 byte relay。
- 自动证书始终包含 localhost/127.0.0.1，远程 IP/hostname 必须显式配置；已有证书缺 SAN
  时拒绝启动且绝不覆盖。详见 [TLS Proxy 模块](modules/tls-proxy.md)。

### Public HTTPS Gateway (`docker-compose.https.yml`)

- 默认不参与普通 Compose；用户显式把 overlay 叠加到源码或预构建 compose 后，固定版本 Caddy
  才启动，并按 `OPENBILICLAW_DOMAIN` 自动申请、续期公网证书。
- Caddy 使用 `network_mode: service:openbiliclaw-backend` 与后端共享 loopback；overlay 通过
  `!override` 把宿主机 `8420` 改为仅 `127.0.0.1` 可达，并发布 TCP `80/443`、UDP `443`。
- `FORWARDED_ALLOW_IPS=127.0.0.1` 让 Uvicorn 只接受该 hop 的 client/scheme 转发头；外部 HTTPS
  scheme 因此贯穿 auth Origin、Secure cookie 和 WSS 契约。Caddy 直接反代 HTTP/1.1 与
  WebSocket，不修改 FastAPI 路由。
- Caddy data/config 使用独立 named volumes。公网部署必须另开 Web 密码门禁，远程扩展必须
  使用默认关闭的设备密钥认证；Caddy 启动脚本在 `/api/auth/status` 明确返回
  `enabled=true` 前不绑定公网端口，首次配置 fail closed。详见 [HTTPS 部署](https-deployment.md)。

### API Auth Gateway (`auth_core.py` + `api/auth.py`)

- 局域网 / 远程访问的**可选密码门禁**。`create_app()` 在 degraded-mode guard 之后用 `@app.middleware("http")` 注册鉴权中间件（更外层、最先执行），挡所有 `/api/*`（含 `/api/runtime-stream` WS 与 `/api/image-proxy`）；`/api/health`、`/api/qr-info`、`/api/auth/*` 与静态壳（`/`、`/m`、`/web`）保持公开。桌面 / 插件二维码只通过 `/api/qr-info` 取 `lan_ip`，避免扫码入口触发 `/api/health` 的 embedding readiness probe。
- `auth_core.py` 纯标准库：scrypt 密码哈希、HMAC 无状态签名 token、稳定密码指纹、反向代理 `X-Forwarded-For`（受信代理从右向左解析、fail-closed）与 Origin / scheme 归一化（CSRF `Origin==Host`、WS Origin、Bearer 裁定、`Secure` cookie 复用同一实现）。
- 默认凭据是 HttpOnly cookie `obc_session`（同源 fetch/img/WS 自动携带，前端不持有 token）；跨源限时 Bearer 为允许列表内逃生通道。改密 / 登出所有设备 / 轮换密钥经 SQLite `auth_state` 表的单调 `auth_epoch` 真正撤销所有设备；`session_secret` / `password_hash` 永不经 `GET /api/config` 返回。详见 [API Auth 模块](modules/api-auth.md)。
- 远程浏览器扩展认证默认关闭：`ext-key generate` 只把设备密钥 SHA-256 摘要写入配置，`ext-key enable` 后 `/api/auth/extension-token` 才可用。扩展用长期设备密钥换取最长 168 小时的短会话；普通 HTTP 走 `Authorization: Bearer`，只有 WebSocket 和 `/api/image-proxy` 因浏览器接口限制使用短会话 query。撤销任一设备密钥会提升全局 `auth_epoch`，立即失效所有现有会话。远程扩展不依赖可伪造的 Origin 或 Docker 网关信任。

### Side Panel Durable Chat

插件聊天不再把主状态只放在 DOM / JS 内存里。`popup/` 对主聊天、惊喜推荐内聊和兴趣猜测内聊统一调用 `/api/chat/turns`：

1. popup 生成 `turn_id` 并 POST 消息、`scope`（`chat` / `delight` / `probe` / `avoidance_probe` / `confusion`）和可选内容上下文。非空校验与既有 turn 幂等检查后，若全局 12h + 对象 72h gate 都允许，后端先写带 `attached_to_turn_id` 的系统确认 turn，再写用户 `pending` turn 并交给 Dialogue worker；两行以 `(created_at,rowid)` 确定顺序。
2. 待聊 API 把未结算高优先级假设/open 疑惑裁到最多 3 条，并提供 `count_only`。用户主动 open 不查时间冷却；同 `(ref,session)` 在单个 `BEGIN IMMEDIATE` 内复用，跨 session 各自产 turn；疑惑仍受 `clarifying <= 1`。popup、移动 Web 与桌面 Web 只有这里生成的 durable 卡片保留主动假设动作，三处认知更新区只读；CLI `questions` 仅 GET 同一列表。
3. `scope="hypothesis"` 是结构卡片分支：创建时直接写 `completed` payload，不启动 LLM worker。confirm/reject、legacy、discuss/defer 与 reconciliation 均只 submit frozen-snapshot worker executor；旧 discuss attempt-token/CAS/scanner 已删除。action 最多 shield 等本地 job 1 秒：完成保持 200，队头阻塞返回 202 且 job 继续。
4. popup、移动 Web 与桌面 Web 对 202 才通过 `/api/chat/turns/{turn_id}` 按 1/2/5 秒轮询，30 秒截止后显示可刷新/重试；同步 200 不多发 GET。主聊天统一使用 `session=popup`，三端初始化按该 session hydrate `chat/hypothesis/confusion/probe/avoidance_probe` 可见历史，再由共享 renderer 只把 `delight` 留在推荐卡自己的 contextual history；消息里的「多聊聊」因此与插件、桌面 Web、移动 Web 主对话对齐。确认卡「聊聊」只提交 `reply_to_turn_id`，context preview 只读，服务端在 user INSERT 前冻结 canonical binding；首次进入恢复历史时滚到最新消息，之后聊天可见且在线时约每 2.5 秒检查共享历史，只有快照变化才重绘，并保留用户正在阅读的滚动位置；移动/桌面的全局回顶控件在聊天页避开 composer。Dialogue prompt 仍只回灌所有 session 的 completed `chat/hypothesis/confusion`，探针独立走自己的上下文与结算路径；三端都可从聊天确认入口处理卡片 action，画像/认知更新区仍保持只读。

历史消息在 prompt 中使用创建时固定的 `[MM-DD HH:mm]` 本地绝对时间，当前时间只进本轮 user 尾段。confusion 回复的 durable 完成观察者只写 cognition/runtime 展示信息，不结算对象；结算和失败重放均由带 generation 快照的串行学习锚处理器负责。

### Init 多源画像导入

`openbiliclaw init` 的首轮信号由本轮勾选的数据来源合流。B 站与小红书 / 抖音 / YouTube / X / 知乎 / Reddit / Linux.do / Bangumi / V2EX / 微博都可选；至少保留一个来源。Linux.do 公开 discovery 登录可选，但书签 / 点赞 / 阅读记录 bootstrap 要求扩展从 `/session/current.json` 正面确认当前账号。微博公开 discovery 匿名可用，但收藏 / 关注 / mentions bootstrap 要求微博同源任务页先确认登录和 uid；没有 heartbeat 时微博不能作为唯一画像信号来源。Bangumi 只有在用户显式提供公开 username 时读取公开收藏并作为画像信号，V2EX 可由显式 username 或浏览器任务页观察到的账号读取四个只读 scope。所有实际画像来源都没有信号时以 `empty_signals` 失败。

1. B 站 API 直连拉取观看历史、收藏夹和关注列表（仅当本轮选择 B 站；`--no-bilibili` / `OPENBILICLAW_NO_BILIBILI=1` 会跳过并持久化关闭 B 站源）。
2. 后端在 `xhs_tasks` 表入队 `bootstrap_profile`，并在 `init --yes-xhs` / `fetch-xhs` 默认复用 6 小时内已有 bootstrap 任务，避免重复打开前台小红书 tab。浏览器插件轮询 `/api/sources/xhs/next-task` 时，后端会先把任务原子标记为 `in_progress` 并写入 `claimed_at`；15 分钟无回写才允许重新领取。插件在用户已登录的小红书页面中先打开 `/explore` 定位当前用户 profile。滚动任务会以前台 tab 触发页面内“我”入口的 anchor click，background 只等待同一 tab 完成导航；只有找不到可点击入口时才回退到直接导航。到 profile 后，插件解析 profile state / DOM 中的 `saved / liked` notes 和页面显式暴露的 `xhs_history` notes，回写 `/api/sources/xhs/task-result`。当任务显式传入 `max_scroll_rounds` 时，插件会在 profile tab 内优先探测 feed / waterfall / masonry 滚动容器做有限滚动，并先用 `status="partial"` 分批回传新增 notes，最终再用 `status="ok"` 完成任务；`scroll_wait_ms` 和 `max_stagnant_scroll_rounds` 也由任务 payload 控制，并由插件端裁剪到安全范围。后端从任务行不可变 payload 重读允许的 `scopes` 与 `max_items_per_scope`，只接纳声明 scope 的 canonical notes / 整数计数并从接纳 notes 派生 URL，扩展重启或重试不会扩大任务预算。
3. 后端在 `dy_tasks` 表入队 `bootstrap_profile`，由浏览器插件在用户已登录的抖音页面中依次访问发布 / 收藏 / 点赞 / 关注 scope。content script 结合 DOM 解析、MAIN-world fetch tap 和 API harvester 采集条目；当前账号 `sec_uid` 只接受同一 tab 已由 `profile/self` 正面确认的缓存，或由同源只读 `profile/self` MAIN-world bridge 当场确认的结果。`#RENDER_DATA` 只有显式 `isLogin=true` 时才作为未确认候选，不能单独成为分页身份；与 `profile/self` 冲突时以后者为准。常驻 fetch / XHR tap 不再从被动请求 URL 提取或记录 `sec_user_id`，避免浏览他人主页时把他人公开 ID 送入诊断日志。条目按 scope 以 `status="partial"` 分批回写 `/api/sources/dy/task-result`；四个 scope 都完整时以 `ok` 完成，缺少身份或分页中断时保留 partial 并以终态 `degraded` 完成。Douyin 默认需要显式 `--yes-douyin` 才进入 init；非交互式终端默认跳过，避免盲目触发风控或空 200 响应。CLI 默认复用 6 小时内近期正常 / 在途 `bootstrap_profile`，但已 `degraded` 的 completed 结果会重新入队，以便下一次重试补齐分页；扩展领取任务时会把 pending 标记为 `in_progress`。
4. 后端在抖音任务完成后再在 `yt_tasks` 表入队 `bootstrap_profile`，由浏览器插件在用户已登录的 YouTube 页面中依次访问 `/feed/history`、`/feed/channels`、`/playlist?list=LL`。YouTube 与抖音都会打开前台 tab，串行入队可避免多个平台同时抢浏览器焦点。YouTube 默认需要交互式确认或显式 `--yes-youtube`；非交互式终端默认跳过，`OPENBILICLAW_NO_YOUTUBE=1` 会强制跳过。CLI 默认复用 6 小时内近期 `bootstrap_profile`，扩展领取任务时会把 pending 标记为 `in_progress`。
5. 后端在 `zhihu_tasks` 表入队 `bootstrap_events`，由浏览器插件在用户已登录的知乎页面中读取最近浏览记录、收藏夹条目、个人动态点赞和个人动态收藏。`fetch-zhihu` 使用同一任务类型但只做 smoke；guided init 选中知乎时会显式收集任务结果并把事件写入本轮 profile inputs。知乎默认需要交互式确认或显式 `--yes-zhihu`；非交互式终端默认跳过，`OPENBILICLAW_NO_ZHIHU=1` 会强制跳过。CLI 默认复用 6 小时内近期 `bootstrap_events`，动态点赞和动态收藏各自独立使用单分支上限。
6. 后端在 `reddit_tasks` 表入队 `bootstrap_events`，由浏览器插件在用户已登录的 Reddit 页面中先读取 `/api/me.json` 识别当前用户，再读取 saved、upvoted 和 subscribed subreddit。`fetch-reddit --mode bootstrap` 使用同一任务类型但只做事件 smoke；guided init 选中 Reddit 时会显式收集任务结果并把事件写入本轮 profile inputs。Reddit 默认需要交互式确认或显式 `--yes-reddit`；非交互式终端默认跳过，`OPENBILICLAW_NO_REDDIT=1` 会强制跳过。CLI 默认复用 6 小时内近期 `bootstrap_events`，三个分支各自独立使用单分支上限 300。
7. 后端在 `linuxdo_tasks` 表入队 `bootstrap_events`，由浏览器插件在真实 Linux.do 任务 tab 内依次读取书签、点赞和阅读记录。插件所有上游请求均为同源 GET，后端只接收归一化事件项；`_t` 只作登录布尔，其值、其他 Cookie 和原始响应不上传。`fetch-linuxdo` 默认只做 smoke；guided init 选中 Linux.do 时把三类结果纳入 profile inputs。Linux.do 默认需要交互式确认或显式 `--yes-linuxdo`；非交互式终端默认跳过，`OPENBILICLAW_NO_LINUXDO=1` 会强制跳过。

回写后的跨源对象会转成普通事件层 payload：小红书 `saved -> favorite`、`liked -> like`、`xhs_history -> view`；抖音 `dy_post -> view`、`dy_collect -> favorite`、`dy_like -> like`、`dy_follow -> follow`；YouTube `yt_history -> view`、`yt_subscriptions -> follow`、`yt_likes -> like`；知乎 `zhihu_read_history -> view`、`zhihu_collection -> favorite`、`zhihu_activity_like -> like`、`zhihu_activity_favorite -> favorite`；Reddit `reddit_saved -> favorite`、`reddit_upvoted -> like`、`reddit_subscribed -> follow`；Linux.do `linuxdo_bookmarks -> favorite`、`linuxdo_likes -> like`、`linuxdo_read_history -> view`；Bangumi 公开收藏与 X 账号信号也按各自映射进入初始化输入。事件都带 `metadata.source_platform`。任务表冻结首份 canonical 结果；六个扩展账号来源都在 durable ingress 后用原子 `source_bootstrap_state.json` 跳过跨任务已见 identity key，每源保留最新 5,000 个。profile 已初始化后，周期结果的新行为事实由 generic owner 异步进入 `ProfileUpdatePipeline`；首次 init 的 owner 标记保持为空，仍由汇总事件统一建模，避免重复学习。
7. 后端在 `v2ex_tasks` 表入队 `bootstrap_profile`，由扩展在目标 member / replies / favorite topics / favorite nodes 页面读取四个只读 scope。executor 必须同时确认 route 与 `#Main` 页面壳；条目达到上限时 dispatcher 再取一页证明是否耗尽，错误路由、仍有数据、达到最大页数、登录或解析失败都保持 partial。后端按 PAT / 浏览器 / 配置 / accepted 身份阶梯门控投影；首次完整收藏 scope 种下账号快照基线，之后连续两次完整快照确认缺失才生成本地 retraction，任何步骤都不向 V2EX 写入。

回写后的跨源对象会转成普通事件层 payload：小红书 `saved -> favorite`、`liked -> like`、`xhs_history -> view`；抖音 `dy_post -> view`、`dy_collect -> favorite`、`dy_like -> like`、`dy_follow -> follow`；YouTube `yt_history -> view`、`yt_subscriptions -> follow`、`yt_likes -> like`；知乎 `zhihu_read_history -> view`、`zhihu_collection -> favorite`、`zhihu_activity_like -> like`、`zhihu_activity_favorite -> favorite`；Reddit `reddit_saved -> favorite`、`reddit_upvoted -> like`、`reddit_subscribed -> follow`；V2EX `publish -> publish`、聚合回复 `discussion_reply -> discussion_reply`、收藏主题 `favorite -> favorite`、收藏 Node `follow -> follow`；Bangumi 公开收藏与 X 账号信号也按各自映射进入初始化输入。事件都带 `metadata.source_platform`。任务表冻结首份 canonical 结果；六个扩展账号来源都在 durable ingress 后用原子 `source_bootstrap_state.json` 跳过跨任务已见 identity key，每源保留最新 5,000 个。profile 已初始化后，周期结果的新行为事实由 generic owner 异步进入 `ProfileUpdatePipeline`；首次 init 的 owner 标记保持为空，仍由汇总事件统一建模，避免重复学习。

v0.3.102+：上述四阶段（拉取 + 入库 / 分析偏好 / 生成并保存完整画像 → 生成首轮可用推荐）抽成共享异步流水线 `cli.run_guided_init`，CLI 与后端 API 复用同一份逻辑——CLI 用单次 `asyncio.run(run_guided_init(...))` 驱动，后端在服务事件循环里直接 `await`，互不嵌套 loop；阶段 3 是严格提交屏障，阶段 4 使用其返回的完整画像，再按「发现 → 个性化评估 → 推荐表达 → canonical 可用性校验」闭环。唯一与路径相关的补池步骤以 `discover_backfill` 注入（CLI 一次性引擎 / API 持 `_refresh_lock` 的 `controller.run_init_backfill`）。图形化入口包括插件「推荐」tab、安装包首启 `/setup/` 第 3 步和桌面 Web `/web` 未初始化推荐区，都会渲染来源选择 + 前置清单 +「开始初始化」按钮，`POST /api/init`（仅本机）经 `InitCoordinator`（`init_runs` 持久化状态机 + 单写者进度事件 + `BEGIN IMMEDIATE` 单飞 + 崩溃 reconcile + 协作取消）后台跑 wrapper，进度走 `runtime-stream` 的 `init_progress/completed/failed`，`GET /api/init-status` 给权威进度 + 前置检查（LLM / embedding / 平台登录状态；B 站仅在选中时阻塞）。init 活跃期间写者门控：`background_llm_work_allowed()` 一处暂停所有后台 LLM 循环，画像 / 配置 / 反馈 / 手动 refresh / 兴趣探针 / source 配方等 HTTP 写端返回 `409 init_running`，`/api/bilibili/cookie` 静默 no-op、`/api/sources/*/task-result` 放行，init 任务豁免热重载取消；无写入的 `POST /api/config/probe-service` 是精确例外，LLM / 默认链 / embedding / 网络测试在初始化期间仍可调用，LLM 请求继续受稳定 total gate 约束。普通完成是可浏览推荐已就绪的后端权威终态；部分完成允许前端进入应用并由恢复后的后台补池。后台恢复后的同步命令适配器（当前为 Reddit `rdt/opencli`）必须经 worker thread 执行；完成态 `init-status` 只读探针缓存，避免后台预热 / 外部命令反过来冻结终态页面。详见 [init 模块文档](modules/init.md)。

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

### Linux.do Extension-Backed Read-Only Source

Linux.do 复用知乎 / Reddit 的任务队列、staged final、lease reclaim、候选管线和增量画像协议，但网络边界更窄：扩展只在带任务标记的真实 `https://linux.do/*` tab 内执行同源 GET，不做发帖、点赞、收藏、关注、编辑或任何其他状态变更。

```mermaid
flowchart LR
    P[LinuxdoDiscoveryProducer / guided init] --> Q[(linuxdo_tasks)]
    Q --> K[/api/sources/linuxdo/kick]
    K --> D[extension dispatcher]
    D --> T[isolated linux.do task tab]
    T --> G[same-origin GET executor]
    G --> N[normalized topics / counts<br/>or structured error]
    N --> R[/api/sources/linuxdo/task-result]
    R --> B{task type}
    B -->|bootstrap_events| E[favorite / like / view events]
    B -->|search / hot / feed / creator / related| C[DiscoveredContent<br/>topic:id · post]
    C --> A[discovery_candidates pending_eval]
```

公开 search / hot / feed / creator / related discovery 不强制登录。个人书签、点赞、阅读记录必须先由 `GET /session/current.json` 返回 `current_user.username`；Cookie `_t` 仅作为登录布尔提示，Cookie 值本身不会离开浏览器。任务结果只包含白名单归一化字段（包括 `topic_id`、标题、URL、作者、分类、tags、互动数与发布时间）或结构化错误；Cookie、CSRF、原始 JSON/HTML 和挑战页均不上传。生产任务的单请求超时默认且最多 30 秒；discovery 默认且最多 5 页，bootstrap 按每页 20 条和 limit 自动扩页（300 条为 15 页）且最多 15 页，输入列表最多 5 个、每分支最多 300 项、单响应最多 2 MiB。content executor 另以 120 秒 / 50 页 / 20 输入作第二层绝对防御，dispatcher 不允许合法后端任务触达。部分 bootstrap scope 失败时只保留有效条目并标为 `degraded`，不当成完整成功。

`https://linux.do/*` host permission 的唯一理由是：浏览器扩展必须在用户真实站点会话中发起这些同源只读请求；后端不能也不会接收 Cookie 后重放。自动化测试已覆盖解析、分页、cap、超时、错误映射、dispatcher 与任务 tab 隔离；2026-08-09 已用真实已登录 Chrome unpacked extension 验证 bootstrap、五路 discovery 和候选入池。E2E 同时发现 hash marker 会被 Discourse SPA 清除，任务 tab 现改用稳定 query marker，修复后真实 feed 任务的 Linux.do 行为事件增量为 0。

微博的个人 bootstrap 采用同一类隔离任务边界，但不是普通行为采集：`weibo_tasks` → `/api/sources/weibo/next-task` → 带 `openbiliclaw_weibo_task=1` 的 `m.weibo.cn` tab → 同源 `/api/config` / `/api/account/getuid` 与收藏、关注、mentions GET → `/api/sources/weibo/task-result`。`SUBP + ALF` 只在扩展本地作为登录布尔提示，游客 `SUB` 不算凭据；任务结果必须带正 uid，后端按账号与 scope 去重后才进入 profile event ingress。失败/登录墙/挑战页/部分 scope 不会伪装成 healthy empty，已采 rows 仍留在 staged result 中。

### LLM Providers (`llm/`)
- 统一的多模型接口（OpenAI / Claude / Gemini / DeepSeek / Ollama / OpenRouter / OrcaRouter）
- `[llm.instances.<id>]` 为每个端点保存独立 `provider_type` / Base URL / token / model；registry 以实例 ID 注册，同一个 adapter 可实例化多次。`default_chain` 可包含任意数量实例，失败与限流 cooldown 都只影响当前实例
- `LLMService` 通过 caller bucket 选择 `[llm.routes.soul/discovery/recommendation/evaluation]`：默认继承全局链，`inherit=false` 时执行模块自己的完整链并严格禁止 spill 到全局链。旧 provider/model override 会投影为等价实例或派生实例
- `LLMRegistry.complete_chain()` 执行有序链，`complete_provider()` 精确探测一个实例；响应携带最终 `instance_id`。Ollama chat 实例必须显式配置 model，仅有服务地址或独立 `bge-m3` embedding 不会注册 chat、更不会猜 `llama3`
- 异常报警（未发布）：`LLMRegistry` 与 `EmbeddingService` 的每个失败点调用顶层 `diagnostics_alerts.record_diagnostics_alert()` 写入进程内有界环形缓冲；API 层经 `GET /api/diagnostics/alerts` 暴露快照，并把新告警以 `type="diagnostics.alert"` 推到 runtime-stream。记录永不抛错、不改变 fallback / 熔断行为
- `/api/config/discover-models` 在配置内存副本上构建精确实例并调用 OpenAI-compatible `GET /models`，供 PC Web、插件与 setup 的可编辑模型下拉使用；该支路不保存配置。协议没有 Effort capability 枚举，返回的 Effort 仅是本地 advisory
- `codex_auth.py` 提供实验性的 Codex CLI ChatGPT OAuth 凭据导入、刷新与能力探测状态持久化；OpenAI 实例设置 `auth_mode="codex_oauth"` 时构造独立的 `CodexChatGPTProvider`，请求发往官方 Codex 传输端点 `https://chatgpt.com/backend-api/codex/responses`（SSE Responses 流），并限制 `base_url` 只能为官方 Codex 域名
- DeepSeek 的连通性探针显式关闭 thinking；普通请求的 reasoning effort 是 request-local 参数，不修改共享 adapter 状态。每个 DeepSeek 实例的 `base_url` 分别进入 SDK 和 endpoint 代理裁决
- 结构化输出共享解析：`llm/json_utils.py` 为 discovery eval-batch、recommendation copy/classify、soul awareness/insight/profile/speculator 提供统一 JSON 容错，兼容 MiMo / OpenAI-compatible wrapper、fenced JSON、JSONL、schema echo 和 malformed `{ [ ... ] }`
- v0.3.0+ embedding 兜底：`OllamaProvider.embed()` 走原生 `/api/embeddings`，配 `bge-m3` 模型可在 Mac/Win/Linux CPU 跑相似度计算，不需额外 API Key
- `EmbeddingService` L1 内存 + L2 SQLite 双层缓存；`embedding.provider="ollama"` 且 embedding 凭据为空时直接使用本地 Ollama 默认地址，不再产生向后兼容 warning
- `DashScopeEmbeddingProvider`（`provider="dashscope"`，阿里百炼原生 multimodal-embedding API，仅 embedding）加入 embedding provider 家族，其 `embed()` 文本向量与 openai/gemini/ollama 一样接入既有文本 embedding 消费方；出站走 `network.httpx_kwargs_for_endpoint(base_url)`——dashscope.aliyuncs.com 属国内 endpoint，即使 `[network].mode` 切到 system/custom 也强制直连（对齐 v0.3.167）。可选 `[llm.embedding].multimodal_enabled` + 多模态模型（`gemini-embedding-2` / `qwen3-vl-embedding`）时启用**封面视觉链路**：discovery 入池预热封面向量（按 URL 派生键），Recommendation 两条路径一致消费「封面↔兴趣锚点」跨模态余弦的有界正向加成——惊喜 `precompute_delight_scores`(加到 delight_score) 与正常 `serve()` 排序(并入 relevance 项;热路径只读缓存、不现抓)。跨模态 floor/ceil 已按真实部署数据标定（`_VISUAL_COVER_SIM_FLOOR/CEIL=0.35/0.48`，per-cover max anchor cosine 的 p50/p95，834 covers）。默认关闭、纯文本零成本、只加不减、默认路径逐字节一致
- 弹幕文本（P2，`[discovery].danmaku_enabled`，**无需多模态**）：`BilibiliAPIClient.get_danmaku_texts_result()` 走 `comment.bilibili.com/{cid}.xml` 并返回 `success / no_data / transient_failure`；摘要仍存独立的 `content_cache.danmaku_text`，预热按完整摘要走 document embedding。只有确认空结果才推进 source 状态，HTTP/XML/embedding 失败保留原因并在下一轮重试；HTTP 200 但根元素不是 B 站 `<i>`（例如 HTML challenge）也属于 transient failure；已有摘要在 fingerprint / 维度变化时只重嵌入，不重复抓取。
- 视频关键帧（P3，`[discovery].keyframe_enabled` 叠加 `multimodal_enabled`）：`fetch_keyframes()` 从 B 站 videoshot 雪碧图跨全部 sprite 全局采样，返回带 `success / partial / no_data / transient_failure` 的 `KeyframeFetchResult`。每个返回帧携带稳定 sampled-slot，部分 sprite/crop/task 成功时成功槽位可安全缓存但结果仍 retryable，不会把后续槽位重编号。缓存键绑定 embedding fingerprint、采样算法版本和 `keyframe_max_frames`；只有确认无 videoshot 数据，或采样完整且所有返回槽位 embedding 成功才推进 `keyframes_fetched_at`，HTTP、解析、精灵图和空向量失败留待重试。P3 使用与 P1 共用质心，但 P1 cover bonus 仍由 `visual_profile_enabled` 单独控制。
- 用户视觉画像（P1，`[discovery].visual_profile_enabled` 叠加 `multimodal_enabled`）：`rebuild_visual_profile()` 从每条推荐反馈的封面构建质心，按 `feedback_at` 节流并通过 BackgroundTaskRegistry single-flight 调度；质心绑定 provider/endpoint/model fingerprint 与维度，维度 0 表示未知，只有已知正维度不同时才触发重建。只开 `keyframe_enabled` 也会构建该共享质心，但 P1 热路径 bonus 仍保持关闭。
- **margin 几何重设计（P1/P3 有符号评分）**：v0.3.185 的「去 neg 惩罚、纯正向」是对 neg 抵消问题的第一刀——安全但浪费了 neg 能说话的区域。本轮用几何方法把 neg 安全请回来，取代纯正向也取代被搁置的 C 方案（dislike 理由字段）。三点设计：①**聚类前 cross-cleaning**（`cross_clean_labels`，kNN k=3、`drop_margin=0.08`）剔除落在敌方势力范围的封面（misclick/love-hate 矛盾），绝不翻转极性；②**聚类后 contested 检测**（`contested_pairs`，cosine ≥ `_VISUAL_PROFILE_CONTESTED=0.45`）标记 love-hate 区；③**打分时 margin 判定**：`s_pos − s_neg ≥ margin → boost`、`s_neg − s_pos ≥ margin → suppress`（负值）、`|net| < margin → gray`。一个差值自标定（s_pos/s_neg 共享同一 embedding/管线，差值无需单独 τ，0.80 教训的一般化）。contested 区**不静默而是抬高门槛 2×**（raise-don't-mute）——"质心重叠就整对灰掉"会丢掉 ~40% 的 P3 信号，改成提高说服力门槛但保留 clear win。校准全部来自实测（`scripts/measure_visual_profile_geometry.py`，1366 候选），换 provider/模型后重测（铁律 3）。实测：vp +107/−280、kf +4/−3；方向验证 boost 的是 kigurumi/角色展示/MV。根因（反馈语义不分视觉维度）由几何方案处理，C 方案降级为未来 soul 层的解耦增强
- **跨平台公平性归一化**：四路 bonus 在 `serve()` 叠加后按 `source_platform` 分组，以 0 为固定点做正负两侧分段对齐：多平台时正值按平台内 positive max 对齐到当前所有平台观测到的全局正向最大值，负值按全局负向最大幅度对齐，目标受 `_COMBINED_BONUS_CAP` 限制；单平台保持原始幅度（超 cap 时限幅）。0 / 缺失 / all-zero 保持 0，弱正向不会凭空变负；`combined_bonus` 为空时 no-op，排序逐字节一致。

#### 本轮视觉 / 弹幕预热契约（覆盖上文历史归一化描述）

以上视觉链路的持久化契约以当前实现为准：视觉质心的重建调度独立于 P1 cover bonus 开关，
所以 `keyframe_enabled` 单独开启时也会构建共享质心；P1 bonus 仍只由
`visual_profile_enabled` 控制。反馈更新时间负责节流，后台任务通过 registry / single-flight
合并并发重建。质心、关键帧、弹幕 embedding 均绑定 provider / endpoint / model fingerprint 与维度；维度 0 是
未知值，只有两个已知正维度不同才触发维度重建。关键帧还绑定采样算法和 `keyframe_max_frames`；
确认 no-data 且没有向量的 keyframe/danmaku 行不会因 namespace 或采样变化重抓。

关键帧和弹幕源结果明确区分 `success`、`partial`、`no_data`、`transient_failure`。关键帧只有确认
无数据，或采样完整且每个返回槽位 embedding 成功才推进完成状态；HTTP、解析、精灵图、部分
embedding 和空向量失败留待下轮重试，成功槽位会复用。已有弹幕摘要
在模型切换时优先直接重嵌入，不重复抓取源数据。弹幕摘要使用完整 `danmaku_max_chars`，不截断
固定前缀。预热查询复用当前 fresh / servable pool predicate 和配置的 fetch limit。

跨平台归一化以 0 为固定点；多平台正负两侧分别对齐到当前全局观测极值并受组合 cap 限制，
单平台不放大绝对幅度，0 和缺失保持 0。因此弱正向不会被转换成负惩罚，离线报告与生产评分
共享同一实现。

### Storage (`storage/`)
- SQLite 数据库管理
- 可移植数据迁移：SQLite online backup、去除 `[api.auth]` 的配置快照、`.obcbackup` manifest / SHA-256、安全 ZIP 解包、request ID 对账、pending 取消、启动期 runtime lock、journaled replace 与失败回滚；包为未加密敏感文件，明确排除机器证书 / 自启动、日志、派生缓存和 OpenBiliClaw Web / 扩展访问身份
- `Database` facade 按调用线程分配 WAL connection：初始化/event-loop 线程保留 primary，status、对话锚与其它 worker 不再同时 step 同一条 `check_same_thread=False` 连接；普通 facade 连接保持 legacy foreign-key PRAGMA，只有 `open_connection()` 显式短事务开启外键约束。写锁仍交给 SQLite `busy_timeout` 串行，失败事务 rollback 后才 lock retry；direct DML 即使零行命中也必须 commit，异常必须 rollback，避免永久线程连接遗留 writer lock。关闭时先 drain 自有 worker、再回收全部 thread-affine connections。
- 冷备份、完整性检查与显式修复
- 候选质量信号持久化与数据迁移；`events` 行写入 `inferred_satisfaction` / `satisfaction_reason`，支持 `query_events(satisfaction_modes=...)`
- 事件来源归属持久化；`events.source_platform` / `content_id` / `source_confidence` 为后续按平台撤回提供 durable 边界，旧行只按 metadata / 规范 URL 保守回填，未知归属保持 `legacy_unknown`
- `seen_items` 是 discovery / recommendation 共用的无界已看身份账本；`insert_event` 与 `insert_events_batch` 同事务维护，`seen_items_backfill_state` 让升级回填幂等且增量
- `get_pool_candidates` 与 canonical availability loader 共用每个 `topic_group` ≤3 条的窗口：先执行 durable seen / linkability gate，再按 topic 内 relevance / score time / view / bvid 排名选前三，最后恢复全局 candidate-tier / relevance 顺序。这样长尾 group 能进入窗口，已看或不可打开的高分行也不会先占掉公开槽位
- `discovery_candidates` 持久化所有来源 raw candidates 的 lifecycle：`pending_eval`、`evaluating`、`evaluated`、`cached`、`rejected_low_score`、`rejected_duplicate`、`rejected_cache_admission`、`rejected_temporal_stale`、`rejected_recently_viewed`、`rejected_franchise_quota`、`failed_eval`、`trimmed_capacity`；容量 victim 与时效拒绝都保留 terminal 行和 `eval_error` 原因，不做物理删除。
- evaluator embedding prefilter 默认保持 shadow。`discovery.prefilter_audit` 先把不含候选文本/URL/画像正文的 decision（identity hash、平台/上下文 class、相似度阈值、explore/保护位、embedding/profile digest）写入 `evaluator_prefilter_shadow_audit`，provider 返回后按随机 decision id 回填 diversity cap 之前的原始 LLM score 与统一 admission 判定。表按 30 天和 20,000 行双重有界；required-interest 和 embedding 输入共享权重排序后的 top-256。embedding 或 telemetry 任一异常都把候选留在 LLM 路径：显式 `enforce` 也只有在本批每条决策证据完整落库后才允许剔除，否则整批 fail-open；§6.4 gate 会因 coverage/fail-open 证据不足保持关闭。只读 gate 只报告，不写 `eval_prefilter_mode`。
- `discovery_inspiration_probe_cache` / `discovery_inspiration_expansion_cache` 持久化 query inspiration 搜索探针、横向扩展、curator 判断和 yield 反馈；`discovery_interest_selection_ledger` 记录二级兴趣抽中事件，让兴趣被抽到后立即进入冷却而不必等待 keyword yield；`discovery_keywords` 可携带 aspect / inspiration / expansion / angle 元数据，但不改变原有 in-flight 去重键。`KeywordPlanner` 的 inspiration-only 分支会从 selection ledger / keyword / raw candidate / admitted pool 构建二级兴趣 coverage snapshot，经过 brainstorm → provider-chain grounding → curator → deterministic quota / explore validation → bounded repair 后写入各平台关键词池；`keyword-inspiration-dry-run` 复用同一路径但跳过关键词写库，并使用独立 preview selection scope 做真实请求诊断。
- `count_pool_available_candidates_by_source()` 与 `count_pool_candidates()` 保持前端可见口径一致；`count_pool_raw_material_by_source()` 统计 fresh / 非 dislike / 未推荐 / 未命中 `seen_items` 的 `content_cache` raw material，并合并 `discovery_candidates` 中待评估 / 已评估未缓存的 raw material，供 runtime raw ceiling headroom 和 trim 使用。两类来源统计及已看身份都通过 `sources.platforms` 归一，`zhihu-*` 等 strategy 可覆盖旧缓存的 Bilibili 默认平台。
- `maintain_pool_inventory()` 是 runtime 唯一 destructive maintenance 边界：`canonical available -> recover eligible suppressed -> protected IDs -> stale/explore/topic/source plans -> cross-table raw plan -> invariant validation -> commit`；恢复复用 canonical readiness，仅额外要求 `recommended_at IS NULL`，并按来源缺口、相关度、评分时间和稳定 ID 排序。每批最多修改 50 行，维护查询、持久化已看身份与动态 delight 阈值都接受同一显式 isolated connection；专属 worker 与 serve worker 分队列，绝不把共享 `Database.conn` 直接扔进 `to_thread()` 并发事务。
- `load_pool_serve_snapshot_async()` / `persist_pool_serve_async()` 是交互读写边界：前者在一个一致只读事务中聚合推荐所需状态，后者在短 `BEGIN IMMEDIATE` 中原子写 recommendation + shown；交互锁等待按 8×250ms 有界，维护锁等待固定 75ms。
- `chat_turns` 持久化 durable turn，字段含 `payload` JSON 与创建/更新时间；列表以 `(created_at,rowid)` 稳定排序。确认入口在 `BEGIN IMMEDIATE` 内依次按 `attached_to_turn_id`、`(ref,session)` 查重再插入 completed turn，因此并发 open 与卡片先落库的 crash gap 都不重复。discussion 只使用 payload `state`，不存在 `attempt_token/discussing_at` 或 stale scanner。`card_settlements` 是轻量 ref winner receipt：`INSERT OR IGNORE` 固化 `verdict/turn_id/payload`，`result/applied` 划定对象语义终态，`event_id` 与事件 INSERT 同一 SQLite 事务。表中不再有 lease、claim token 或 `seg_*`；非 SQLite mandatory effect 只由单 worker 执行，stable-key/set-upsert 覆盖全部故障点。`confusions.replay_queue` 提供上限 5 的归属 FIFO、精确队头出队与 completed reply receipt 扫描；它是对象归属数据，不是 settlement job inbox。
- `auth_state(key, value)` 单行表持久化局域网密码门禁的撤销纪元 `auth_epoch` 与稳定密码指纹 `password_fingerprint`（非会话表，仅全局计数 + 指纹）；跨进程事务原子自增，验签实时读

## 运行时数据库约束

本地 API 与 CLI 的高频运行路径现在遵循两条约束：

1. **同进程共享一个 Database facade，每个调用线程使用自己的连接**
   `MemoryManager`、`RecommendationEngine`、`ContentDiscoveryEngine` 会优先复用同一个 `Database` 对象，避免一轮运行里多次 `Database(...).initialize()` 争锁；facade 的 primary 只归初始化线程，其它线程从 registry 取得各自连接，不能并发 step 同一 SQLite connection。推荐 serve 与后台 pool maintenance 仍分别使用 facade 自有单线程 worker 和显式隔离事务。
2. **启动前先检查、运行中按周期冷备**
   `openbiliclaw start` 会在启动前检查数据库完整性；若健康且超过默认 24 小时未备份，会先生成一份冷备到 `data/backups/`。

数据库修复不在启动路径里自动执行，高风险恢复统一通过 `openbiliclaw db-repair` 触发。

## 对外集成约束

当前 Agent Bridge 接入遵循以下边界：

1. **外部集成只通过 adapter 调用内核**
   Agent 宿主不直接访问 SQLite、memory JSON 或内部 engine 组合细节。Direct bootstrap 会在 adapter 暴露 Soul/recommendation operation 前调用 controller 的幂等 startup maintenance，避免绕过 daemon `run_forever()` 的恢复顺序；其 inline admission 在返回前同步补齐 durable copy，而不假设未启动的 daemon owner 会在稍后处理。
2. **skill 只是协议包装，不是业务主链**
   学习、推荐、反馈回流仍由 `runtime/`、`soul/`、`recommendation/` 等模块负责，`integrations/openclaw/skill.py` 只负责对外暴露稳定 handler；新功能必须同时进入 operation、descriptor、CLI（若适合）和 capability manifest。
3. **宿主发现走能力协商 + 仓库根目录 `skills/`**
   当前仓库通过 `skills/openbiliclaw-adapter/SKILL.md` 提供真实 workspace skill，再由 skill 内部调用 adapter CLI bridge；`capabilities` 是避免宿主继续使用旧能力子集的权威入口。
