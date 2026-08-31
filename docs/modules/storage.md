# 存储层

## 概述

`src/openbiliclaw/storage/` 负责本地 SQLite 数据库、schema 初始化、候选池计数和高频读写路径。它不理解 runtime state 或用户画像，只提供确定性的持久化 API。

本模块当前承担六类边界：

- 行为、推荐、候选池、聊天和鉴权状态的 SQLite 表结构管理。
- 推荐池 `content_cache` 的可换 / raw / pending 计数口径。
- discovery 待评估池 `discovery_candidates` 的生命周期管理。
- evaluator prefilter、learned-vs-LLM、推荐时效排序的 privacy-safe shadow 审计，以及正式推荐池的时效 eligibility 持久化守卫。
- 跨平台收藏 / 稍后再看的 canonical 本地 membership、元数据快照、native sync 状态和独立任务快照持久化。
- 事件来源归属的规范化持久化：平台、稳定内容 ID 与归属置信度；旧事件只做可证明的回填，无法确认时保留 `legacy_unknown`。
- 事件入口幂等回执，以及小红书 / 抖音 / YouTube / 知乎 / Reddit / Linux.do 来源任务首个终态结果的 crash-safe staging。
- 事件入口幂等回执，以及小红书 / 抖音 / YouTube / 知乎 / Reddit 来源任务首个终态结果的 crash-safe staging。
- 跨机器迁移包的一致快照、严格校验、私有暂存和重启期 journaled replace / rollback。

## 可移植数据迁移

`storage/migration.py` 把运行中的用户状态导出为格式版本 1 的 `openbiliclaw-user-data` 包，文件扩展名为 `.obcbackup`。容器本质是标准 ZIP，**没有密码和加密**；`manifest.json` 明确写入 `contains_secrets=true`、`encrypted=false`，并为每个允许成员记录路径、类型、字节数与 SHA-256。导出 SQLite 时不直接复制可能正在写入的主文件，而是以只读 URI 打开源库、用 SQLite online backup API 生成一致快照并执行 `PRAGMA integrity_check`；其它文件复制前后会核对 stat / digest，持续变化时拒绝生成伪一致快照。目录遍历会在进入前剪枝排除根、采用独立扫描上限，并在复制前、复制流中和 ZIP 写入时逐层执行容量限制；ZIP 完成后立即删除最多 8 GB 的未压缩 snapshot，只在一次受全程互斥保护的下载期间保留最多 2 GB 的 archive。

### 导出范围

| 包含 | 刻意排除 |
|---|---|
| 磁盘 `config.toml` + `config.local.toml` 合并、移除整段 `[api.auth]` 后生成的单份 `config/config.toml`；主数据库和数据目录中的其它可迁移 SQLite；Soul / memory / runtime 用户状态文件；`*_cookie.json` 等平台登录凭据；`data/image-cache/`；白名单桌面偏好 | 来源配置的分层 provenance 与整段 `[api.auth]`（含密码 / hash、session secret、proxy / Origin 策略与设备 key）；`logs/`；`data/backups/`、`data/cache/`、`data/eval/`、`data/embedding_cache.db`；`data/certs/`、`data/autostart/`；WAL / SHM / lock / temp；OpenBiliClaw Web / 扩展访问会话；外部 CLI 凭据；环境变量**值** |

图片缓存是刻意包含的用户状态：部分平台的签名图片 URL 会过期，迁移后无法可靠重新下载。导出配置仍来自磁盘 `config.toml` + `config.local.toml`，但数据快照路径固定为当前进程已取得 canonical lock 的 active data dir；在线保存但尚未重启启用的新 `data_dir` 不会成为本次数据来源。manifest 的 `source_omitted_environment_variables` 只记录源机导出时有值、会影响运行结果的环境变量**名称**（`OPENBILICLAW_*`、Gemini 标准 Key、系统代理 / CA），不记录 value；暂存时另采集目标进程当时有值的名称为 `target_active_environment_variables`。前者提示目标机重新提供来源依赖，后者是目标环境可能覆盖导入文件的暂存时快照；实际应用仍以重启时环境为准，两者都不表示值已迁移。前端文件只允许 `theme_mode`、`theme_hue`、`accent_style`、`auto_load_on_scroll`、`side_drawer_open`；后端 endpoint、Bearer / session、通知与缓存状态不进入包。

### L2 embedding 缓存（`data/embedding_cache.db`）

该文件是**可重建派生缓存**，刻意排除在 `.obcbackup` 导出之外（issue #153 整改后）：

- **存储格式**：向量以版本化 little-endian float32 BLOB 存储（`OBLV` 头 + dtype/dimension），4096 维约 16 KiB/行；旧 JSON 行（`encoding=0`）与降级回写的 mixed-format 行仍可透明读取，读取按内容自适应解码，单行损坏降级为 miss。
- **Schema**：`embedding_cache` 表含 `encoding` / `dimension` / `created_at` / `last_accessed_at` 元数据列，`embedding_cache_meta` 记录 `schema_version` / 最近维护报告。旧 schema 打开时自动升级并保留行。
- **生命周期**：`EmbeddingService` 构造时注册 active provenance namespace 并做一次/进程/库的运行时准备（JSON→BLOB 迁移 + 容量维护）；配置 `[llm.embedding].cache_max_bytes` 后按高低水位淘汰（非 active namespace → active 最旧行）。物理回收（WAL checkpoint + `VACUUM INTO` + 原子替换）由 CLI `embedding-cache-clean` 显式执行，daemon 不自动替换文件。

### 校验与暂存

`stage_migration_archive()` 在任何 active 配置或数据库被替换前完成全部验证：

- 压缩包不超过 2 GB，成员不超过 20,000 个，目录扫描项目不超过 100,000 个，单成员不超过 4 GB，总解压大小不超过 8 GB；manifest 不超过 16 MB，前端偏好文件不超过 64 KB，成员路径不超过 512 字符 / 16 层；
- ZIP 成员必须是 manifest 精确列出的普通文件，拒绝绝对路径、`..`、过深 / 过长路径、符号链接、设备 / 特殊文件、重复成员和加密成员；
- 格式版本必须等于当前版本，源 OpenBiliClaw 版本不能高于目标运行版本；
- 每个文件的实际大小和 SHA-256 必须匹配，TOML 必须能构建无 blocking issue 的 `Config`，每个识别为 SQLite 的文件必须通过 `integrity_check`。

校验通过后才把内容发布到项目根的 `.openbiliclaw-migration/pending-<uuid>/`，记录整个暂存树的 seal 并原子更新 `pending.json`；启动应用前会重新核对该 seal，暂存内容被改动就拒绝替换。运行中的 `Database`、`MemoryManager`、配置对象和浏览器偏好保持不变，HTTP 最终返回 `202 staged + migration_id + request_id + restart_required`。`request_id` 是 UUID 规范化后的上传关联 ID；持久化 `migration_status()` 会在 staged 状态回传它，而 HTTP status 路由还会在上传 / 校验进行中叠加 `state="processing"`、同一 `request_id` 与 `phase="uploading|validating"`。这让客户端在响应超时 / 断线后区分“仍在处理”和已暂存，但 request ID 不是服务端自动去重键。桌面端不会用一次瞬时 `idle` 终结不确定请求：它最多强制查询 3 次，对 `idle/cancelled` 间隔 500ms 再确认，匹配 ID 的 `processing/staged` 立即收口。如果再次导入另一个合法包，新的 pending marker 取代旧包，旧暂存目录被清理；`cancel_pending_migration()` 可在应用开始前删除 pending，且不接触 active 数据。

### 重启应用与回滚

`openbiliclaw start`、`openbiliclaw serve-api` 和桌面打包入口在读取业务数据库前，同时尝试持有项目根 `.openbiliclaw-migration/runtime.lock` 与 canonical 数据目录同级的 `.<data-dir-name>.openbiliclaw-runtime.lock`。这样同一项目、或不同项目但共享同一数据目录的受支持后端，都不能并行越过应用边界。锁覆盖整个进程生命周期，因此在线 `PUT /api/config` 改变 `data_dir` 只能持久化并返回 `restart_required=true`；当前 runtime 与外部凭据写继续使用已锁住的 active 目录，完整重启取得新目录锁后才切换。存在 pending 时，启动器先在目标配置 / 数据目录旁准备新副本，再通过 `apply-journal.json` 记录每一步并执行同目录 `os.replace`：

1. 目标 `config.toml`、`config.local.toml` 与数据目录按存在情况移为 `*.pre-import-<migration-id 前 12 位>.bak`；
2. 在 prepared 主库上运行当前 schema 初始化 smoke；读取来源 prepared DB 与目标 active DB 的当前 `auth_epoch`，写入 `max(来源, 目标) + 1` 并删除来源 `password_fingerprint`。新 epoch 严格高于两者，使会话撤销不依赖 session secret 是否被环境变量固定；随后启用导入数据与规范化配置并再次校验 SQLite；
3. 成功后在 `status.json` 保存不对 API 暴露的活动代际回执（目标路径、配置 SHA-256、严格递增的 DB auth epoch），再删除 pending / journal / 暂存目录；若不支持目录 fsync 的文件系统在断电后复活同一 marker，启动端必须先锁回执中的数据目录并精确验证代际，只清理重复 marker、绝不重放。任何一步失败则按可重复执行的 `rolling_back` journal 恢复原配置和数据，并记录 `failed` 状态；提交确认前出现的 premature applied 回执会先被降为 failed，避免恢复后自锁。

本次成功应用产生的 `pre-import` 回滚副本会保留；完成提交后会清理更早迁移遗留的同类回滚 / failed / prepared artifact，使每个目标只保留本次可恢复副本。启动应用会重新读取目标机的磁盘配置与有效数据路径；`data/certs/` 与 `data/autostart/` 会从应用时目标目录复制到新数据目录，路径、监听端口、日志、网络 / TLS / 自启动和 CDP 等机器专属配置也使用此时目标机现值。整段 `api.auth` 先以目标机应用时的最新磁盘值为基线，来源包既不含也不覆盖任何 auth 字段；随后轮换磁盘 session secret，并把扩展远程访问 key 清空且关闭。prepared DB 的严格递增 `auth_epoch` 同时高于来源与目标当前 epoch，会独立撤销两台机器的旧 Web 会话，即使 `OPENBILICLAW_API_AUTH_SESSION_SECRET` 仍由目标环境固定也不例外；正常启动 reconcile 再记录目标凭据 fingerprint。这样保留目标机的门禁 / 密码 / proxy / Origin 策略，但不保留旧会话和设备配对。白名单桌面偏好也只在这次 apply 成功后由设置页生效，不在上传暂存时提前切换；浏览器按 `migration_id` 只接收一次，同一 applied status 后续不会覆盖用户的新选择。

### 公开 Python API

| API | 作用 |
|---|---|
| `create_migration_archive(config, frontend_settings, project_root=...)` | 创建在线一致、带清单与校验和的临时导出包；调用方负责在响应完成后删除临时目录。 |
| `stage_migration_archive(path, current_config, project_root=..., request_id=...)` | 验证并发布唯一 pending 导入，记录规范化请求关联 ID，不修改 active runtime。 |
| `cancel_pending_migration(project_root=...)` | 删除尚未应用的 pending；没有 pending 时返回 `False`，不修改 active 配置或数据。 |
| `acquire_migration_runtime_guard(project_root, data_dir=None)` | 非阻塞取得项目锁，并在给出 `data_dir` 时同时取得 canonical 数据目录锁；任一冲突即返回 `None`。 |
| `apply_pending_migration(project_root=...)` | 只应在持有 runtime guard 时调用；应用或恢复一次 pending migration。 |
| `migration_status(project_root=...)` | 返回 `idle/staged/applied/failed/cancelled` 的非敏感状态；staged 含 `request_id` 和两类环境变量名供对账。 |

## 已实现功能

| 功能 | 状态 | 说明 |
|------|------|------|
| 观看完播判定（2026-07-27+） | ✅ | `events.inferred_satisfaction` 现在也覆盖 `view`：`sources/event_format._classify_view_completion` **只判正向**——完播 ≥`_FINISHED_WATCH_MIN_RATIO`（0.8）且观看 ≥15 秒记 `positive/finished_watch`，其余保持 `unknown/fallback`。低完播刻意不判负（自动播放 / 误点 / 预告 / 重看进度重置都长这样），否则会污染 `recent_negative_exemplars` 并影响内容评估。阈值校准见常量注释；改动 `watch_seconds` 来源后需重新校准 |
| SQLite schema 初始化 | ✅ | `Database.initialize()` 自动创建核心表和索引，支持旧库增量补列 / 补索引；成熟库会自动补 `recommendations(bvid)` 与 `events(event_type, id DESC)` 热路径索引，并创建 `seen_items` canonical 已看账本。旧库初始化时按游标增量回填全部历史「已消费」事件（`view` / `favorite` / `like` / `coin`），不受旧版 2000 条窗口限制；类型集扩大时按 `scanned_event_types_version` 自动倒回重扫一次。 |
| 视觉 / 弹幕 provenance 迁移 | ✅ | 旧 `content_cache` 自动补 keyframe/danmaku fingerprint、维度和采样签名列；旧 `user_visual_clusters` 补 provenance，另建单行 profile state。keyframe/danmaku selector 只对已有向量（`keyframe_count > 0` / `danmaku_text` 非空）响应 provider/model namespace、维度变化；确认 source no-data 的行不会因 namespace 或采样签名变化反复抓取。请求或已存维度为 0 都表示未知，不当作已证实不兼容；只有两个正维度实际不同才重排 |
| 初始化运行租约 | ✅ | `init_runs` 同时持久化 `sequence/updated_at`（owner heartbeat）与 `progress_sequence/progress_at`（有效业务进展）。旧库自动补列并从 `updated_at` 回填；预约新 run 时两套时钟一起重置，运行期 orphan reconcile 可安全释放没有 owner 的 `starting/running` 行。 |
| 初始化事件批量落库 | ✅ | `insert_events_batch()` 复用单事件规范化逻辑，在独立短连接的一次事务中写完阶段 1 的 B站 / X / 知乎 / Reddit / Linux.do / Bangumi 事件；失败整体回滚，避免数百次 commit 拉长初始化和扩大半写状态窗口。 |
| Database facade 跨线程连接隔离 | ✅ | 长生命周期 `Database` 不再把同一条 `check_same_thread=False` 连接同时交给 API event-loop、status reader 与后台 worker；初始化线程保留 primary，其它调用线程各自缓存一条 WAL connection，同一普通 facade 方法在主线程/worker 都保持既有 `foreign_keys=OFF` 语义，只有显式 `open_connection()` 的原子短事务继续 `foreign_keys=ON`。并发写由 SQLite WAL + `busy_timeout` 串行，不用会卡住 event loop 的 process-wide mutex；`_execute_write()` / `_execute_many_write()` 在任何 `OperationalError` 后先 rollback 清理隐式事务，再决定 lock retry 或原样抛出。线程连接会长期复用，因此绕过 helper 的 direct DML 也必须在每次成功 execute 后 commit（即使 `rowcount=0`，SQLite 仍可能已开启隐式写事务），异常则 rollback；XHS token backfill 与 self-info purge 已按该契约收口。`close()` 先排空 facade 自有 worker，再关闭 registry 内全部连接。 |
| 实时事件并发写隔离 | ✅ | `insert_event()` 不再让 API、账号同步和后台任务跨线程共享 process-wide SQLite 隐式事务；每条事件使用独立短连接，把 event、`seen_items` 与 backfill cursor 在同一事务提交，锁冲突按既有有界策略重试，退出必关闭连接。这样不会再由其它线程的 commit/rollback 触发 `cannot commit - no transaction is active`。 |
| Durable event ingress 回执 | ✅ | `events.ingest_key TEXT NOT NULL DEFAULT ''` 由旧库迁移幂等补列；`idx_events_ingest_key_unique` 只约束 `ingest_key <> ''`，因此无幂等键的 legacy/internal direct 写入仍保持 append-only。公开 HTTP 边界更严格：`/api/events` 每项 `event_id`、`/api/feedback` 与 `/api/recommendation-click` 的 `request_id` 都先 trim，再要求 1–400 字符；缺失/空白/超长在 route 前 422，不能产生 event、`seen_items` 或 recommendation 投影。CLI feedback 省略 ID 时生成并回显，OpenClaw CLI/skill 必填；这些边界不会把空 key 传到 storage。`EventIngressService` 把非空客户端键规范为 `producer:client_key`（总长 ≤512），逐项拒绝非法输入，再由 `MemoryManager.persist_events_with_receipts()` / `Database.insert_events_with_receipts()` 在一个独立短连接事务中提交全部合法项、同步 `seen_items` 与 cursor，并按原输入位置返回稳定 `event_id / inserted / duplicate`。并发重放只有首写成功，后续回执指回首写行；commit 后的 owner wake 只是延迟提示，失败不会撤销 durable fact。 |
| 六来源 staged canonical task result | ✅ | `XhsTaskQueue` / `DyTaskQueue` / `YtTaskQueue` / `ZhihuTaskQueue` / `RedditTaskQueue` / `LinuxdoTaskQueue` 共用 `sources.task_result_protocol`：`stage_final_result()` 在 `BEGIN IMMEDIATE` 下把首个 final callback 合并进 `result_json` 并写 `_openbiliclaw_terminal_status`，此时数据库 `status` 刻意保持非终态，普通 claim lease 仍可在请求 5xx / 进程崩溃后回收。marker 已存在即返回冻结结果，晚到 partial / final / failure（以及 XHS rate-limit）均不得改写。XHS `bootstrap_profile` 还会从该任务的不可变 `payload_json` 重读允许的 `scopes` 与 `max_items_per_scope`，在每次 partial/final/直接完成/风控失败合并时过滤未声明 scope 和非整数计数、按 scope 裁剪累计 canonical notes，并只从已接纳 note 派生 URL，防止分批回传扩大任务预算；同 identity duplicate 仍可只补发布时间与首个有效 tokenized URL，不新增 canonical 行。调用方只从冻结行重放 durable event ingress、来源投影与严格 bootstrap seen-key checkpoint；全部成功后 `complete_staged_result()` 才原子翻成 `completed`，且不替换 `result_json`。这些步骤是可重放的多次短事务，不宣称跨表原子；任一点崩溃都由下一次 lease reclaim 从首个 canonical result 修复。 |
| 六来源 staged canonical task result | ✅ | `XhsTaskQueue` / `DyTaskQueue` / `YtTaskQueue` / `ZhihuTaskQueue` / `RedditTaskQueue` / `V2EXTaskQueue` 共用或复用 `sources.task_result_protocol`：`stage_final_result()` 在 `BEGIN IMMEDIATE` 下把首个 final callback 合并进 `result_json` 并写 `_openbiliclaw_terminal_status`，此时数据库 `status` 刻意保持非终态，普通 claim lease 仍可在请求 5xx / 进程崩溃后回收。marker 已存在即返回冻结结果，晚到 partial / final / failure（以及 XHS rate-limit）均不得改写。XHS `bootstrap_profile` 还会从该任务的不可变 `payload_json` 重读允许的 `scopes` 与 `max_items_per_scope`，在每次 partial/final/直接完成/风控失败合并时过滤未声明 scope 和非整数计数、按 scope 裁剪累计 canonical notes，并只从已接纳 note 派生 URL，防止分批回传扩大任务预算；同 identity duplicate 仍可只补发布时间与首个有效 tokenized URL，不新增 canonical 行。调用方只从冻结行重放 durable event ingress、来源投影与严格 bootstrap seen-key checkpoint；全部成功后 `complete_staged_result()` 才原子翻成 `completed`，且不替换 `result_json`。这些步骤是可重放的多次短事务，不宣称跨表原子；任一点崩溃都由下一次 lease reclaim 从首个 canonical result 修复。 |
| 画像更新台账（`profile_update_ledger`，v0.3.174+） | ✅ | 认知画像流水线 Phase 0 的**只追加审计表**。`insert_profile_ledger(*, write_point, source, before_summary, after_summary, diff, source_refs, outcome, turn_id, gate_verdict, held_id, error, effect_key='')` 在动作结束后追加一行（`outcome=success\|failed`，`source_refs` JSON 编码）；空 `effect_key` 保持普通 append，结算 worker 传入固定形状 `dialogue:<ref-sha256>:ledger` / `dialogue:<ref-sha256>:derived:<content-sha256>` 时由 partial unique index + `INSERT OR IGNORE` 保证 observer effect 至多一行。`query_profile_ledger(*, days=30, write_point='', limit=200)` 按时间窗 + 写点过滤返回（newest-first，`source_refs` 解码回列表，并带回 `effect_key`）。其余字段：`write_point`、`source`、before/after 摘要、`diff`（≤2000 字符）、`turn_id`、`gate_verdict`、`held_id`。fresh schema + 旧库 `_ensure_profile_update_ledger_table()` 会幂等补列和索引。写点挂钩清单见 `docs/modules/soul.md`。 |
| Durable chat payload + 单 worker 对象结算收据（v0.3.182+） | ✅ | `chat_turns.payload` 承载结构化卡片/疑惑提问，列表以 `(created_at,rowid)` 稳定排序。`create_chat_confirmation_turn()` 在 `BEGIN IMMEDIATE` 内先查 `attached_to_turn_id`、再查 `(ref,session)`、最后插入 completed turn，保证并发 open 与“卡片先于用户消息”crash gap 均不重复；跨 session 各自产 turn。卡片 discussion payload 只保存 `state`：worker 直接执行 `pending→discussing`，建锚失败补偿回 `pending`，GET 提交的 reconcile 会校正无活锚 orphan；没有 `attempt_token/discussing_at`、三段 discuss CAS 或 stale scanner。`card_settlements` 只保存 hypothesis/confusion/speculation 的 immutable winner `payload`、`applied/result`、稳定 `event_id` 与时间戳，不再保存 claim lease/token 或三段 CAS。`_migrate_card_settlements_to_wave_2()` 以 table rebuild 保留最早表与旧 claim 表的 winner；旧 `seg_event=1` 或 `applied=1` 仅在 migration 中映射为已记录 event identity，runtime schema 不再暴露这些列。`record_card_settlement_event_once()` 在一个 `BEGIN IMMEDIATE` 事务内插入 event 并标记 receipt；`complete_card_settlement()` 无 token，`project_applied_card_settlement()` 仍只消费 `applied=1` 并批量刷新所有 session。`applied=1` 是对象语义终点：显式同 ref retry 只补跑 ledger observer、projection 与精确 generation 解锚，不再重做 object/derived/rebuild。进程内 `DialogueSettlementQueue` 不落这层 job/inbox 表，重启恢复依赖显式 action 重试或 GET reconcile。 |
| Turn relation + immutable binding（2026-08-01） | ✅ | `chat_turns.reply_to_turn_id TEXT NOT NULL DEFAULT ''` 与普通索引采用 additive、幂等迁移；旧行保持空 relation。API 在 capture canonical target 后同步写 user row，`payload.dialogue_binding` 只保存 server-owned `bound/ordinary/detached` binding 与完整 digest；客户端同名事实不会直接写入。context preview 是只读查询，retry 比较已存 normalized request，避免同 turn id 改 target、message 或 generation。 |
| Durable reply CAS 与完整恢复扫描 | ✅ | `complete_chat_turn()` / `fail_chat_turn()` 都以 `WHERE status='pending'` compare-and-swap 并返回是否真正改变一行，重复 completion、迟到 failure 与崩溃后重放不能覆盖首个可见终态。`list_pending_chat_turn_page(after_rowid, limit)` 以不可变 rowid 分页，startup 不再静默截断 1000 条；`count_pending_chat_turns()` 提供 runtime status 的真实 backlog。取消、provider/config/timeout/rate-limit 等瞬态错误不调用 failure CAS，row 保持 pending 供同一 app worker 或下次进程恢复。 |
| 态势门控 shadow 采数 + 代理折价（v0.3.176+） | ✅ | 认知画像流水线 Phase 3 / Wave B 遗留。`posture_gate_shadow_stats()` 从 `profile_update_ledger` 汇总 enforce save-time 校验所需的 shadow 判定统计：最早**有效**判定时间（`gate_verdict IN (shadow_accept, shadow_downgrade, shadow_reject)`，不含 `shadow_error`）+ 近 14 天 / 近 7 天有效判定数。`discount_events_by_confusion(evidence_refs)` 对可解析为事件 id 的 `evidence_refs` 关联 events 行 patch `metadata.discounted_by_confusion=true` + `signal_strength` 折至 0.2（`sources/event_format.apply_confusion_discount`，幂等）；非 id ref 跳过，不删行、不改其它列。供疑惑 `proxy_behavior` 出口调用。 |
| 疑惑对象表 + 归属重放队列（`confusions`，v0.3.182+） | ✅ | 原有状态、72h ask 冷却、partial unique `clarifying≤1` 与 `held_updates` 保持；新增幂等迁移列 `replay_queue TEXT NOT NULL DEFAULT '[]'`。`release_orphan_confusion_claim()` 同时要求 status/`expected_ask_turn_id` 匹配、claim 的 `updated_at` 已超过调用方给定最小年龄、且同一原子 UPDATE 内 `NOT EXISTS(chat_turns.turn_id)`，才把真 crash-gap 孤儿恢复为 open 并清除未实际提问的 `ask_turn_id/asked_at`；活跃 claim→create 窗口和已有 live turn 都不会被回收。`enqueue_confusion_replay()` 在独立 `BEGIN IMMEDIATE` 中去重入尾、上限 5、返回最旧逐出项；`pop_confusion_replay_head(expected_id=...)` 只允许精确队头出队（顺序 fencing）；`clear_confusion_replay_queue()` 原子返回并清空。`list_pending_confusion_dialogue_replays()` **优先扫描任意 status 的非空 replay_queue**（覆盖 resolve commit→pop 之间崩溃后已 terminal 的行），剩余额度再查 `clarifying` 且 completed、晚于 `asked_at`、尚无 turn payload receipt 的 classification gap。`mark_confusion_dialogue_replay_processed()` 可在 turn 仍 pending 时先写幂等 receipt，完成后扫描不会重复计数。 |
| Retraction 事件折价（离线重读面） | ✅ | `mark_positive_events_retracted(identity_urls, retracted_action, *, retraction_at)` 在独立短连接的一次事务中，对 `event_type == retracted_action` 且 URL 归一化 identity key（`sources/identity_keys.dedup_key`：tweet_id / bvid / mid / xhs note_id）命中、**且事件时间早于 `retraction_at`** 的行 patch `metadata.retracted=true` + `signal_strength=min(现值,0.2)`；事件时间不可得的行保守不标。无时间窗口（identity key 全局唯一），覆盖 `openbiliclaw init` 全量重建与 12h 认知整理等重读 events 表路径；不删行、不改 `event_type`/`url`。`latest_retraction_time_for(url, action)` 返回该 identity + action 最新的已存 retraction 事件时间，供落库路径对账迟到正向事件（account_sync 回填）。`retracted_action` 越界返回 0/None。 |
| 推荐池 readiness 计数 | ✅ | `count_pool_readiness()` 返回 `available/copy_ready/raw/pending/admitted_pending_copy/admitted_pending_available/pending_eval/evaluated_pending`。其中 `admitted_pending_copy` 只统计已通过 admission、已完成 style/topic 分类、链接可用且尚缺 expression/topic label 的 canonical 行；`admitted_pending_available` 是它的严格子集，只保留当前 `topic_group` 三条展示窗口仍有空位、补齐文案后能让 canonical `available` 净增的行。公开加载与计数都先应用 durable seen / linkability gate，再做 topic cap；已看或不可打开的高分行不会占住三条窗口。两者都复用 recommendation、`seen_items`、self-XHS 与 delight guards。 |
| 规范化保存存储 | ✅ | `saved_items` 以 canonical key 保存跨平台元数据快照，`saved_memberships` 独立表达收藏 / 稍后看归属，`native_save_states` 持久化当前逐项同步状态；`native_save_tasks` / `native_save_task_items` 独立持久化每次请求的 UUID、不可变成员集合和 task-scoped 结果。旧 `watch_later` / `favorites` 由带 marker 的单次事务迁移导入。 |
| 30 天内容历史投影（issue #112） | ✅ | `list_content_history_page()` 从 `events` 中 recommendation-owned click、`recommendations` 中展示与 dismiss/dislike 反馈以及 `saved_item_removals` 投影三类历史；`list_content_history()` 保留为 offset 兼容包装。连接注册的 deterministic UDF 直接委托 `sources.platforms.normalize_source_platform()`，在 SQL 投影前同时规范 source alias 与 item-key 前缀；缺平台的 legacy click 才显式默认 Bilibili。legacy recommendation 先用 indexed scalar lookup 计算 canonical key 和 hydration bvid，再以主键等值 hydrate，避免成熟库上的 `LEFT JOIN ... OR` 全扫；`shown` 把已点击 recommendation ID 与 item key 各自 materialize 为一次性 `NOT IN` lookup set，避免对每条候选相关扫描 click 集合。每页的 entries、全量 total、`limit + 1` 和 source max-id anchors 在同一 SQLite read snapshot 中读取；keyset 只在按 item 去重后应用，排序以时间、来源类型、来源行 ID 和 item key 完整打破并列。anchors 隔离首屏之后追加的事实，但既有行更新/删除、membership restored 与滚动 retention 仍是当前投影，不承诺跨请求 MVCC snapshot。`removed` 先按 `(item_key, context)` 选择各 context 最新事实，再按 item 聚成一张卡；favorite / watch_later 先 materialize canonical membership lookup，再独立关联 exact list identity，Python 按固定兼容顺序组装 `contexts`，不依赖 SQLite 3.44 才支持的 aggregate `ORDER BY`。API 投影固定忽略 30 天前快照；物理行由数据库初始化及后续 membership 删除时 opportunistic prune，并非独立周期维护或“到点即删”的承诺。曝光与点击继续使用各自权威表，不复制成第二份日志。 |
| 扩展原生保存 job ledger、terminal replay 与旧状态迁移 | ✅ | `extension_native_save_jobs` 保存脱敏后的六平台扩展任务；task URL 只允许平台 HTTPS host 与默认端口，输出移除 fragment/非导航 query（YouTube 仅保留身份参数 `v`；小红书仅保留打开公开笔记所需的单值非空 `xsec_token/xsec_source`，其它 key 仍剥离）。partial unique index 保证 `(platform, item_key, requested_action)` 只有一个 pending/in-progress row。`complete_extension_native_save_job()` 首次冻结 canonical terminal；若 callback 的 task/slug/item/status/error code/后端规范化 message 全部相同，则把丢失 2xx 后的重放视为幂等 ACK，任何字段变化仍拒绝。命名迁移只把六个 canonical 平台的旧 `unsupported`/空 error code 改为 `unsupported_adapter_missing`，绝不改 Bilibili、未知平台或 `unsupported_content_type`。 |
| 推荐链路 canonical identity | ✅ | `content_cache.item_key` 对**非空值**使用 partial unique index（`WHERE item_key != ''`），另有普通 lookup index，`recommendations.item_key` 使用普通索引；空值只作为 v0.3.166 及更早写入器不知道 additive 列时的兼容窗口，避免旧桌面版与新版源码共用数据库后第二条补池候选开始持续触发 `UNIQUE constraint failed`。当前版本初始化会临时移除旧全量 guard，按平台 + raw `content_id` 回填空 identity，并在恢复 partial unique 前确定性合并 canonical 重复行（优先 canonical storage key、填补非空元数据、重定向 recommendation 引用）。若 loser 仍被旧 `watch_later` / `favorites` 引用，consolidation 会先为真实 legacy schema 补 additive `item_key` 并写入 canonical key；后续 normalized saved migration 在 exact `bvid` 不存在时用该稳定键 join keeper，既保留 membership，也不绕过 Task 2 的单次 marker / no-resurrection 语义。B 站 `bvid` 主键保持 raw BV 兼容，非 B 站 `bvid` 存储键使用 namespaced identity，API 继续从独立字段输出 raw ID 与 authoritative URL。 |
| 推荐历史避雷证据字段 | ✅ | `get_recommendations()` 在既有 DTO 字段外返回 `description/tags/topic_key/topic_group/pool_topic_label`，供 API 与 OpenClaw 在历史行离开 storage 后按最新 effective dislikes 做与 serve 一致的最终过滤；`exclude_processed=true` 继续同步排除已反馈单卡。 |
| 来源 raw material 统计 | ✅ | `count_pool_raw_material_by_source()` 合并 `content_cache` raw rows 和 `discovery_candidates` 待评估候选，供 raw ceiling headroom 使用。 |
| 有界库存维护与历史恢复 | ✅ | `maintain_pool_inventory(max_mutations=50)` 在独立短连接 `BEGIN IMMEDIATE` 中先恢复仍合格且能净增 canonical available 的历史 `suppressed` 结果，再统一 stale / explore / topic / source / raw 维护；恢复数受当批 `raw_ceiling - raw_before` headroom 约束，raw 已满或超限时先裁剪、绝不继续恢复。单事务最多修改 50 行，只有确有 deferred victim，或裁剪释放 headroom 后仍可继续恢复时才返回 `has_more=True`；protected/token-owned excess 无可裁剪 victim 时以稳定 WARNING 结束，不再形成恢复/裁剪振荡。已满 topic 不参与恢复，排名窗口试探失败会在同一事务还原。维护连接只等写锁 75ms，交互写入优先；每批仍保护 canonical available 底线并在不变量失败时整体回滚。 |
| 换批读写隔离与时效复核 | ✅ | `PoolServeSnapshot` 在专属单线程 serve worker 上先用只读 preflight 计算 temporal v2 三态；命中 `review_due` / `expired` 时才在短写事务中分别持久化为 `temporal_review_hold` / `stale`，再用一次只读事务统一读取 readiness、候选、平台补位、持久化已看账本和 curator 信号。同一快照只物化一次 `seen_items`，并按账本最新 event id 缓存结果，写入新浏览事件后自动失效。`persist_pool_serve_async()` 用另一条短事务重读最终选中行和完整证据组，只有仍为 `fresh` 且 disposition 为 `eligible` 的条目才原子写入 recommendation + shown，并返回实际 committed/stale/skipped BVID。serve 与 maintenance 使用不同 executor、不同 SQLite 连接，不把共享 `Database.conn` 直接跨线程并发访问。 |
| 十一平台来源族归一化 | ✅ | `sources.platforms` 以可枚举规则统一 Bilibili、小红书、抖音、YouTube、X、知乎、Reddit、Bangumi、Linux.do、V2EX、微博的别名、策略前缀和 URL host；pool accounting、已看身份与 URL 推断共用同一口径。 |
| 八平台来源族归一化 | ✅ | `sources.platforms` 以可枚举规则统一 Bilibili、小红书、抖音、YouTube、X、知乎、Reddit、Bangumi 的别名、策略前缀和 URL host；pool accounting、已看身份与 URL 推断共用同一口径。 |
| 来源定向历史缓存读取 | ✅ | `get_unrecommended_content(limit, source_platforms=...)` 在 SQL 平衡与 `LIMIT` 之前按平台过滤，供 source-scoped discovery backfill 使用；空 `source_platform` 的 legacy 行只按 B 站处理，不能跨源补进 B 站 / YouTube / 抖音定向运行。 |
| discovery 待评估池 | ✅ | `discovery_candidates` 支持 mixed-source enqueue / claim / evaluation / admission，并持久化 `claim_token`、`score_threshold`、`eval_attempts` 与 batch 级 `batch_eval_attempts`；stale-sensitive 完成和释放都匹配 `id + status + claim_token`。 |
| evaluator prefilter shadow 审计 | ✅ | `evaluator_prefilter_shadow_audit` 用随机 decision id 连接预过滤决策与最终原始 LLM score / admission 结果；只保存 identity hash、类别、数值和 digest，不保存标题、URL、正文、prompt、画像文本或 provider response。每次 insert 同时执行 30 天和 20,000 行双重 retention；任何写入/回填失败由 discovery fail-open，并以 incomplete telemetry 阻断 enforce gate。 |
| evaluator learned scorer 对照审计 | ✅ | `evaluator_learned_scorer_shadow_audit` 只接受完整 learned/LLM 对照：随机 32 位 hex decision id、SHA-256 candidate/features digest、有限枚举的平台 / 上下文类、有限 `[0,1]` 分数及一致的 admission 结果；不保存标题、正文、URL、作者、画像或 provider 响应。每次写入执行 30 天 / 20,000 行 retention；不完整或隐私边界外记录在 storage 边界直接拒绝。 |
| 推荐时效排序 shadow 审计 | ✅ | `temporal_ranking_shadow_audit` 只保存一次候选窗口的总数、时间覆盖、bonus 资格数，以及 class/source/age bucket 和 Top10/50/100 before/after 聚合；不保存任何候选 identity 或内容文本。每次写入执行 30 天 / 5,000 行双重 retention，失败不影响推荐。 |
| 推荐池 temporal v2 三态 | ✅ | discovery admission、canonical pool 读取、等待扫描、snapshot 清退与最终 serve 写事务共用 `discovery.temporal` 的 `eligible/review_due/expired` 纯策略。只有置信度 `>=0.80`、完整、`scope=core`、逐字 grounded 的明确 deadline 已过或 `state=expired/superseded` 才 hard expire；1 / 14 / 60 天及旧 v1 3 / 60 天只触发复审，`versioned` 另设 120 天准入 TTL。`review_due` discovery 行回到 `pending_eval`，已入池行进入 `pool_status='temporal_review_hold'`；`expired` 行才进入 `rejected_temporal_stale` / `stale`。单次生命周期清扫最多持久化 500 条，但所有 canonical 读取和计数先排除整批 review-due / expired 行。 |
| 时效展示与 backfill 防绕过 | ✅ | `get_recommendations(exclude_processed=True)`、未读计数与主动通知在最终 limit 前过滤后来进入 `review_due/expired` 的历史推荐，默认历史读取保持完整；`get_unrecommended_content()` 只返回 fresh/non-dislike/temporal-eligible 行。cached-backfill 完整往返证据组，普通 raw 重抓、旧缓存、`unknown/0` 或 malformed 结果不能局部洗字段，也不能把 hold/stale 行复活。readiness、pending-copy、topic/franchise/source 统计与 delight count/backlog 同样排除 `temporal_review_hold` / temporal stale，不让不可展示库存继续占配额或计算预算。 |
| discovery 历史候选查询 | ✅ | `get_existing_discovery_candidate_keys()` 与 `get_existing_content_cache_ids()` 支持 pipeline 在 enqueue 前过滤历史候选和已缓存内容，避免重复 raw 占住 Evo 前供给窗口。 |
| discovery 状态恢复 | ✅ | 启动初始化会释放过期 `evaluating` 行；terminal 状态有 status guard，避免 stale update 改写 cached / rejected 结果。 |
| discovery keyword store | ✅ | `discovery_keywords` 用 `keyword_kind` 区分常规 search 词与 explore 词；默认 `regular`，`explore` 词只供 `ExploreStrategy` 专用 claim，不会被普通 B 站 search 消费。`history_keywords()` 把带 `used_at` 的零产出/全重复 `expired` 词保留在近期冷却内，`recycle_oldest_used(min_age_hours=...)` 只回收超过窗口的历史词；`retire_duplicate_only_keywords()` 接收候选预过滤的真实重复反馈并立即退役无新身份的词。`requeue_keyword_after_transient_failure()` 仍可把 claimed / executing 词无损退回 pending、清理执行时间且不增加 attempts，供平台风控等瞬时故障重试。 |
| XHS task runtime state | ✅ | `XhsTaskQueue` 幂等创建单行 `xhs_task_runtime_state`，持久化 `next_claim_at / cooldown_until / cooldown_reason / rate_limit_strikes`；旧库会 additive 补 `rate_limit_strikes=0`。search / creator claim 在独立短连接事务里按任务 ID 计算稳定 ±25% 抖动并推进节流水位；`active_task_count()` 给 producer 提供 pending + in-progress 积压门。`record_rate_limit()` 让独立风控轮次按 1/2/4…小时指数退避、24 小时封顶，同一活动冷却内的重复报告复用当前 strike，并可在同一事务中终结触发风控的 legacy task；活动冷却结束后，成功完成一条 search / creator 才清零 strikes，晚到成功不能取消其它任务刚打开的冷却。该状态独立于单个任务，因此能跨 FastAPI 与 MV3 service-worker 重启、多个浏览器 profile 生效。 |
| discovery inspiration cache | ✅ | 新增 `discovery_inspiration_probe_cache`、`discovery_inspiration_expansion_cache`、`discovery_inspiration_axis` 与 `discovery_interest_selection_ledger`，持久化搜索探针证据、可复用 inspiration 轴、旧横向扩展缓存、yield 反馈计数和二级兴趣抽中事件；`search_local_inspiration_evidence()` 从 `content_cache` 抽取 local-first grounding evidence；`upsert_inspiration_axes()` / `list_inspiration_axes()` 管理轴库复用和轮转；`backfill_inspiration_axis_yield()` 用 trailing-window 全量重算（SET，幂等）把轴的 `yield_score` 从恒 0 变成由真实 `admissions` / `window_uses` 驱动，`apply_inspiration_axis_lifecycle()` 落库 stale / retired 状态迁移并物理清理 90 天陈旧行；`list_inspiration_axes_by_source()` 按 `source`（非 interest_label）过滤 + `min_yield` 高产筛 + 镜像生命周期排序，供跨域 explore 通道复用 `source='explore'` 的高产轴；`get_keyword_interest_coverage_snapshot()` 归一化汇总 keyword / raw candidate / admitted pool 覆盖和 recent selection count，用于下轮二级兴趣抽样降权；`get_keyword_cohort_stats()` 输出 inspiration / merged cohort 对比、local-first stub 字段和 replace 门禁指标。 |
| keyword interest label migration | ✅ | `migrate_keyword_interest_labels()` 根据画像整理产生的重命名 mapping 迁移 `discovery_keywords.source_interest` 和 `discovery_interest_selection_ledger.source_interest`，降低画像标签漂移造成的 coverage / selection cooldown 死桶。 |
| 持久化已看去重 | ✅ | `seen_items(item_key)` 保存所有已知 `view / favorite / like / coin` 的 canonical `source_platform:content_id`，B 站同时向旧调用方暴露 raw BVID。单条 `insert_event()` 与批量 `insert_events_batch()` 都通过各自独立短连接在事件事务内同步 upsert 账本；初始化会按 `seen_items_backfill_state` 增量回填旧事件。可换、raw、评估、平台库存与 delight 打分/计数/出队路径读取这份无界账本，`get_recent_viewed_*` 仅作为兼容别名，不再代表“最近 N 条”。`mark_delight_seen()` 供三端“× / 看过了”先写 canonical ledger、再消费惊喜状态。 |
| 统一 admission 分数门 | ✅ | 推荐池读取、raw/headroom 统计、topic/franchise 分布、suppressed 复活、delight 候选和历史推荐读取都会应用统一最低分；初始化会清理旧低分 `content_cache` / `recommendations` 脏数据。 |
| 惊喜文案就绪门与通道占位排除 | ✅ | `get_pool_candidates_needing_delight_score()` 只领取 `pool_expression / pool_topic_label` 已同时生成且未命中 `seen_items` 的行；`update_delight_score()` 再以条件写入拒绝未就绪或非正式文案快照，因此推荐词生成前不会形成任何 delight 状态。`get_delight_candidates()` / `count_delight_candidates()` 继续只返回精确同步快照并硬排除已看身份，evaluator 的内部 `relevance_reason` 不能进入惊喜状态或展示出口。`get_pool_candidates()` / `count_pool_candidates()` 统一排除已送达惊喜，或 delight 分数达动态阈值且正式文案快照已同步的行。动态阈值自身也不再把已看历史算进样本；默认底线为 `0.75`，copy-ready 样本不少于 150 条时按 Top 10% 边界抬高，样本不足或边界未超过底线时回退底线——高分同质池仍使用 Top 10% 边界，避免普通推荐池被惊喜占位清空（issue #220）；backfill 会修复旧版 reason/hook 快照。 |
| 平台定向候选读取 | ✅ | `get_pool_candidates_for_platform(platform, limit=5)` 直接从 canonical available 集合（`_load_available_pool_candidate_rows_on(..., full_rows=True)`）取整行，与 `count_pool_candidates()` 共用 servability / `seen_items` / linkability / delight / topic-window 守卫与排序，因此 `strict_platform_candidates(p) ⊆ available_candidates_for(p)` 恒成立。平台过滤用 `_pool_source_family(source, source_platform)` 在 Python 侧归类而非裸列比较——`source_platform` 为空的 legacy 行要靠 `zhihu-hot` / `xhs-extension-task` 之类策略前缀才能归族，裸列比较会漏掉它们并破坏 `total == sum(by_platform)`。别名（`xhs`/`zh`）先 canonical 化，空值沿用 bilibili 默认。同时服务 serve 窗口的平台保底与 PC Web 的平台定向推荐请求；`list_servable_pool_platforms()` 返回当前可服务候选的去重平台 token（同口径守卫）。 |
| 平台库存快照 | ✅ | `load_pool_platform_availability()` / `load_pool_platform_availability_async()` 在独立短连接的单次只读事务里物化一次 canonical available 集合，返回 `PoolPlatformAvailability(total_available, by_platform)`。两个数字出自同一份行集合，`total_available == sum(by_platform.values())` 是结构性成立而非两次独立查询的巧合（后者可能观察到不同 WAL 状态）。零库存平台不出现在 `by_platform` 中，由调用方按已启用来源补 `0`。 |
| `style_key` 历史值迁移 | ✅ | `Database.initialize()` 会把 `content_cache` / `discovery_candidates` 中已知旧内容风格 key 迁移到新的观看模式 key；写入 `cache_content()` 和 `update_discovery_candidate_evaluations()` 时也会归一化已知旧值。 |
| 封面粘性保护 | ✅ | `cache_content()` upsert 对 `cover_url` 用 `COALESCE(NULLIF(excluded,''), 现值)`——带空封面的重摄入（如互动数据刷新、事件驱动 related-chain）不再抹掉已有好封面，与 `author_name` / `body_text` 同一保护策略（v0.3.162+）。 |
| 保存内容封面生命周期 | ✅ | `iter_cover_lifecycle()` / `iter_servable_cover_urls()` 以 `content_cache.item_key` 关联 normalized `saved_memberships`，跨平台本地保存内容不会因缺少 legacy BVID 行而被漏预取或误清理；旧 `favorites` / `watch_later` 仍作为兼容 fallback。`saved_memberships(item_key)` 独立索引支持该关联。 |

### Visual enrichment persistence API

`Database.get_candidates_needing_keyframes()` / `get_candidates_needing_danmaku()` 复用当前
fresh servable pool predicate，并可按 embedding fingerprint、正维度和 sampling signature 重新
入队。确认 no-data 的 keyframe/danmaku 行不会因 provider/model 或采样算法变化重抓；已有向量
才会因 fingerprint / sampling 变化重嵌入。维度 0 是未知值，只有当前与已存维度都为正且不同才
重排。`user_visual_clusters`、keyframe 状态和 danmaku 状态均保留 provenance；旧库初始化会
增量补列，旧的无 provenance 行不会被当作当前 embedding namespace 的完成结果。

## 公开 API

### Durable event ingress 回执

```python
receipts = db.insert_events_with_receipts(
    [
        {
            "event_type": "click",
            "ingest_key": "web:request-7",
            "metadata": {},
        }
    ]
)

first = receipts[0]
# EventInsertResult(event_id=..., event_type="click",
#                   ingest_key="web:request-7", inserted=True)
assert first.duplicate is False
```

`Database.insert_events_with_receipts()` 假定调用方已经完成 producer namespace；面向 API / 扩展 / account sync 的入口应调用 `EventIngressService.accept()` / `accept_batch()`，由它验证 `producer`、补 `producer:` 前缀，并返回保留输入位置与逐项 rejection 的 `EventIngressReceipt`。同批非法项在写事务前被剔除；剩余合法项要么在一次事务内全部提交，要么全部回滚。重复键返回数据库中首写行的稳定 id 与 event type，不采用重放请求中变化后的字段。

数据库保留空 `ingest_key` 只为旧库迁移、历史 append-only 调用与不暴露重试语义的内部 direct writer；它不是公开客户端的“可选幂等”契约。新增用户动作入口必须在进入 storage 前取得稳定非空 ID，并为同一动作的响应丢失/网络重试复用该 ID。

### 事件来源归属

`events` 表为每条行为事件保留三列：`source_platform`（规范平台名）、`content_id`（平台内容稳定 ID，可为空）和 `source_confidence`（`exact` / `inferred` / `legacy_unknown`）。新 producer 的显式平台字段优先，其次读取 metadata 中的来源字段，最后从规范 URL 推断；旧调用方仍可省略来源，只有没有 URL 等更强证据时才使用 B 站兼容默认并标记为 `legacy_unknown`，不会把兼容默认误当成精确归属。`content_id`、`bvid`、`note_id`、`tweet_id`、`question_id` 等稳定身份键由 `sources.platforms.CONTENT_ID_METADATA_KEYS` 统一注册；该清单按优先级解析，每条事件只取第一个有效字段，缺失时才回退 URL / BVID，不会把多个候选字段展开成多个内容身份。seen ledger 与事件写入不再维护两套字段列表。旧 `metadata.source_platform` 与 `metadata` 内的身份字段继续保留，供已有画像与回看逻辑兼容使用。

`Database.initialize()` 会在启动时幂等补列和 `(source_platform, content_id)` 索引，并只从明确 metadata 或规范 URL 回填旧行。无法证明的平台保持空值 / `legacy_unknown`，不会凭标题、采集任务或账号信息猜测。该字段集合是后续按平台撤回数据的基础，但本 PR 不删除事件、不实现撤回动作，也不新增账号 ID 或采集任务 ID。

### 六来源任务结果 staging

```python
canonical = xhs_queue.stage_final_result(
    task_id,
    terminal_status="ok",
    notes=notes,
)

# 只从 canonical 执行幂等的 event / source / seen-key 投影。
xhs_queue.complete_staged_result(task_id)
```

上述协议适用于 `XhsTaskQueue`、`DyTaskQueue`、`YtTaskQueue`、`ZhihuTaskQueue`、`RedditTaskQueue` 与 `LinuxdoTaskQueue`。`stage_final_result()` 的首写 winner 是逻辑终态，但仍可由原 claim lease 回收；`complete_staged_result()` 只允许存在 staged marker 的非失败任务翻成 `completed`。XHS bootstrap 的 `complete()`、`merge_result_with_enrichment()`、`stage_final_result()` 与 `record_rate_limit()` 都必须使用任务行保存的同一 scope policy，不能信任 callback 自报的 scope 或剩余额度。业务层不得在 staging 前自行扩大 canonical 结果，也不得在 stage 与 complete 之间信任重试 callback 的新字段或先翻 terminal 再做事件 / seen-key 投影，否则会破坏预算或失去崩溃自动修复入口。
上述协议适用于 `XhsTaskQueue`、`DyTaskQueue`、`YtTaskQueue`、`ZhihuTaskQueue`、`RedditTaskQueue` 与 `V2EXTaskQueue`。`stage_final_result()` 的首写 winner 是逻辑终态，但仍可由原 claim lease 回收；`complete_staged_result()` 只允许存在 staged marker 的非失败任务翻成 `completed`。XHS bootstrap 的 `complete()`、`merge_result_with_enrichment()`、`stage_final_result()` 与 `record_rate_limit()` 都必须使用任务行保存的同一 scope policy，不能信任 callback 自报的 scope 或剩余额度；V2EX 结果还会在服务端净化字段、按 Topic 聚合 Reply，并在 resolved identity 门禁通过后写入账号分区的 Node affinity。完整收藏 scope 会先写 snapshot run / item / pending effect，durable event ingress 与 affinity 接受后才 ack effect；任务重领只重放首份 canonical result 和同一 effect key。业务层不得在 staging 前自行扩大 canonical 结果，也不得在 stage 与 complete 之间信任重试 callback 的新字段或先翻 terminal 再做事件 / seen-key 投影，否则会破坏预算或失去崩溃自动修复入口。

### Durable chat reply 状态

```python
changed = db.complete_chat_turn(turn_id, reply=reply)  # pending -> completed CAS
failed = db.fail_chat_turn(turn_id, error=safe_error)  # pending -> failed CAS
page = db.list_pending_chat_turn_page(after_rowid=0, limit=500)
depth = db.count_pending_chat_turns()
```

两个终态写都只允许 pending 行变化并返回 `bool`；调用方在 `False` 时重读行，已有终态视为幂等完成，仍为 pending 则按持久化瞬态故障重试。恢复 page 按 rowid 严格升序，不能用 `created_at` 单独排序（SQLite 时间戳可能同秒）。

### 对象结算 winner 收据

```python
created = db.try_create_card_settlement(
    ref=ref,
    verdict=verdict,
    turn_id=turn_id,
    payload=frozen_winner_payload,
)
winner = db.get_card_settlement(ref)
db.record_card_settlement_event_once(ref=ref, event=event)
db.complete_card_settlement(ref=ref, result=result)
```

同 ref 的 `INSERT OR IGNORE` 只产生一个 winner；retry 始终读取该行的 payload。事件 key 由内部对规范化 ref 做 SHA-256 后构造，调用方不能传 SQL 列名或 effect 名。对象、派生与 rebuild marker 的故障恢复幂等由 Soul worker 在 Wave 2 apply 层负责；SQLite 不再承担第二 executor 的 claim/lease/fencing。结算 audit key 同样只接受上述固定 hash 形状，拒绝 raw ref、控制字符或任意调用方 key。

### 对话锚与疑惑重放持久化

```python
dropped = db.enqueue_confusion_replay(confusion_id, classifier_output, max_items=5)
head_applied = db.pop_confusion_replay_head(confusion_id, expected_id=turn_id)
pending = db.list_pending_confusion_dialogue_replays(limit=50)
db.mark_confusion_dialogue_replay_processed(turn_id)
released = db.release_orphan_confusion_claim(
    confusion_id,
    expected_ask_turn_id=missing_turn_id,
    minimum_age_seconds=30.0,
)
```

入队、队头出队与清空都使用独立短连接的 `BEGIN IMMEDIATE`，不会发生
read-modify-write 丢更新。`pop_confusion_replay_head()` 的 `expected_id` 是 FIFO
fencing token：T2 不能越过 T1。orphan claim 释放同时校验 claim identity、
`julianday(updated_at)` 超龄和 `NOT EXISTS(chat_turns.turn_id)`；默认 runtime 使用
30 秒最小年龄，避免 recovery 撞进本地 claim→create 活跃窗口，也不会把已经补建成功
或被 retarget 的提问回滚。完成回复的恢复查询只读 `scope='confusion'`，并用
`julianday` 比较 ask receipt；畸形 legacy payload 按未处理对待，不会让
`json_extract` 使扫描失败。

### 持久化已看身份

```python
seen_keys = db.get_seen_content_keys()  # source_platform:content_id
seen_bvids = db.get_seen_bvids()        # B 站兼容集合
```

`seen_items` 是 discovery 与 recommendation 的已看硬去重来源。它记录首次/最近事件 ID
与时间；再次观看只更新同一 canonical 行。升级旧库时会增量回填所有历史事件，
因此第 2001 条以前的已看内容也不会重新进入候选。

派生它的事件类型由 `_SEEN_ITEM_EVENT_TYPES` 决定：2026-07-26 起为 `view` /
`favorite` / `like` / `coin`——收藏、点赞、投币同样证明用户消费过这条内容，此前只认
`view`，于是用户明确收藏过的视频照样能被当新内容推荐。`follow` 不在其中（关注 UP
不等于看过某条内容）。这个集合每次扩大都要同步抬 `_SEEN_ITEM_EVENT_TYPES_VERSION`：
老库的回填游标停在最新事件上，不倒回就永远扫不到新纳入的类型；`initialize()` 比对
`seen_items_backfill_state.scanned_event_types_version` 后会自动倒回重扫一次。
注意倒回只能救回**带身份**的旧事件：2026-07-26 之前 init 写下的 `favorite` 行没有 `bvid` / url，回填扫到也认不出是哪条内容（实测老库倒回后 `seen_items` 数量不变）。这些收藏由 `mark_items_seen(source_platform, content_ids)` 补：account sync 每 6 小时拿到完整收藏快照后直接把 bvid upsert 进账本（不产生事件，因此不会重复计入偏好信号）。快照行 `first_event_id = 0`（没有单一事件产生它），冲突时保留既有真实事件的溯源与时间；由于 seen 状态缓存键是 `MAX(last_event_id)`，快照写入必须显式失效缓存，否则新身份要等下一条真实事件才可见。

旧的
`get_recent_viewed_content_keys()` / `get_recent_viewed_bvids()` 保留为兼容 API，
但返回同一个无界账本。

### 事件回看（跨源去重用）

```python
urls = db.recent_event_urls(
    ["view", "favorite"],
    within_hours=48,
    exclude_source="account_sync",  # 跳过 metadata.source 等于该值的行
    limit=2000,
)
```

`recent_event_urls()` 是 `query_events()` 的薄封装，返回窗口内指定类型事件的非空 `url` 集合；`exclude_source` 逐行解析 `metadata` JSON 过滤来源。`AccountSyncService` 用它做扩展 ↔ 账号拉取的跨源去重（键提取现统一到共享 `sources/identity_keys.py`——bvid / mid / tweet ID / xhs note_id）。

### Retraction 事件折价（离线重读面）

```python
marked = db.mark_positive_events_retracted(
    ["https://x.com/u/status/123"],  # retraction 事件的 identity url(s)
    "like",                          # retracted_action ∈ {like,favorite,share,follow}
    retraction_at=retraction_time,   # 只标注事件时间早于它的行；时间不可得保守不标
)
retraction_time = db.latest_retraction_time_for("https://x.com/i/status/123", "like")
```

`mark_positive_events_retracted()` 是 retraction 双面折价机制的离线重读面（内存面在 `ProfileUpdatePipeline.ingest_batch()`）：把用户撤销的正向证据在 events 表原地标注为 `retracted` 并把 `signal_strength` 折到 0.2，供 `openbiliclaw init` 全量重建、12h 认知整理等重读路径消费。identity key 全局唯一，无时间窗口，撤销数月前的 like 也能命中；只 patch metadata，不删行、不改其它列。`latest_retraction_time_for()` 供 `MemoryManager.propagate_event/propagate_events` 在落库迟到正向事件时按 identity key 对账已存 retraction。

### Guided Init 状态与事件

```python
db.insert_events_batch(events)
run = db.get_latest_init_run()
db.update_init_run(
    run_id,
    sequence=next_sequence,
    progress_sequence=next_progress_sequence,
    progress_at=now,
)
```

`insert_events_batch()` 使用独立连接 + 单事务，避免共享连接跨 `to_thread`；`update_init_run()` 只接受白名单字段。heartbeat 写只更新 `sequence/updated_at`，substantive 写才更新 progress 字段，这一语义由 `InitCoordinator` 统一维护。

### Source Families

```python
from openbiliclaw.sources.platforms import (
    CANONICAL_SOURCE_FAMILIES,
    infer_source_platform_from_url,
    normalize_source_platform,
    source_family,
)

family = source_family("zhihu-creator", "")  # "zhihu"
platform = normalize_source_platform("zh")  # "zhihu"
url_platform = infer_source_platform_from_url(
    "https://www.zhihu.com/question/1/answer/2"
)  # "zhihu"
```

`CANONICAL_SOURCE_FAMILIES` 固定按 `bilibili / xiaohongshu / douyin / youtube / twitter / zhihu / reddit / bangumi / linuxdo / v2ex / weibo` 枚举。别名归一包括 `bili`、`xhs/rednote`、`dy/tiktok`、`yt`、`x`、`zh/知乎`、`rd`、`bgm`、`linux.do`、`v2ex` 与 `wb/微博`；strategy 归类使用 B 站精确 key 与其他平台前缀，URL 推断只匹配解析后的精确 host 或其子域，不扫描整条 URL 子串。数据库保留 `_pool_source_family()`、`_normalize_source_platform_key()` 私有兼容入口，但两者均委托该规则表。

### Saved Memberships And Native State

```python
from openbiliclaw.saved_sync.models import SavedItemInput

item = SavedItemInput(
    source_platform="youtube",
    content_id="video-123",
    content_url="https://www.youtube.com/watch?v=video-123",
    title="Example",
)
membership = db.upsert_saved_membership("favorite", item, note="稍后整理")
native = db.ensure_native_save_state("favorite", item.item_key, "favorite")
current = db.get_saved_membership("favorite", item.item_key)
rows = db.list_saved_memberships("favorite", limit=50, offset=0)

task_rows = db.create_native_sync_task_snapshot(
    "favorite", [item.item_key], "task-id", "manual_selected"
)
if db.claim_native_sync_task_runner("task-id", "runner-id") and db.claim_native_save_item(
    "favorite", item.item_key, "task-id", "runner-id", "execution-id"
):
    db.update_native_save_claim_route(
        "favorite", item.item_key, "task-id", "execution-id",
        "favorite", "OpenBiliClaw",
    )
    db.heartbeat_native_save_claim(
        "favorite", item.item_key, "task-id", "execution-id"
    )
    db.heartbeat_native_sync_task("task-id", "runner-id")
task_rows = db.list_native_sync_task_items("task-id")
removed = db.remove_saved_membership("favorite", item.item_key)
```

存储契约：

- `saved_items.item_key` 是平台 canonical identity；不同平台可安全复用相同裸 `content_id`。
- `content_cache` 与 `recommendations` 用同一 canonical `item_key` 做跨源关联；新推荐写入会随历史记录持久化该键，读取不再依赖可能跨平台碰撞的裸 ID。
- `saved_memberships` 以 `(list_kind, item_key)` 为主键，同一内容可同时属于 `favorite` 与 `watch_later`。无 `native_save_states` 行时，membership 查询返回 `sync_status="pending"`。
- 封面预取和清理读取以 `content_cache.item_key → saved_memberships.item_key` 判断是否已保存，不依赖 legacy 表是否有同 BVID 行；初始化会为反向关联补 `saved_memberships(item_key)` 索引，并保留旧表 join 作为兼容 fallback。
- `native_save_states` 以同一联合键引用 membership；状态写入在启用外键的事务内先验证本地 membership，未本地保存的 key 会抛出 `ValueError`，不会留下 orphan state。所有 DAO 写入只接受显式 `NativeSaveStatus` 集合；新建表还有等价 `CHECK`。`ensure_native_save_state()` 使用 `INSERT OR IGNORE` 并在同一事务返回 effective row，任何已存在的 pending / claimed / syncing / retryable / terminal 状态都不会被本地重复保存降级或清空 owner。兼容用 `upsert_native_save_state()` 只能插入 / 刷新无 owner 的 pending 或写允许的 terminal 快照：传入未知 / 带空白状态、`execution_id`、`status='syncing'`、带 `task_id` 的 pending，覆盖已有 active owner，或把 terminal 降回 pending 都会拒绝；它不能建立 / 改写 task ownership。`complete_native_save_claim()` 只接受 terminal 状态，`pending/syncing/unknown` 不会清空 execution owner。
- `native_save_tasks` 以 UUID 为主键；`native_save_task_items` 以 `(task_id, item_key)` 为主键并保存请求顺序、requested/resolved action、target、status/error 与 `is_live`。task/item 集合不引用 membership，因此本地删除后轮询快照仍存在。`create_native_sync_task_snapshot()` 在一个 `BEGIN IMMEDIATE` 中写 task/items 并领取 eligible 的 live owner；缺失、terminal、已有 owner 与零 eligible 都形成可查询快照。
- `extension_native_save_jobs` 是与 native task ledger 分离的浏览器执行 ledger。每个 mutation 使用独立短连接覆盖完整事务，避免六 source 线程在 process-wide connection 嵌套 `BEGIN IMMEDIATE`；`create_or_reuse_extension_native_save_job()` 原子复用 active row，并把显式默认 443 / 尾点 host 规范为无端口标准 hostname，拒绝其它 port、凭证或跨平台 host。查询参数默认全部移除；YouTube 只保留唯一非空 `v`，小红书带 query 时必须保留唯一非空 `xsec_token`、可选唯一非空 `xsec_source`，重复、空值或只有 source 均拒绝入队。`claim_extension_native_save_job()` 按平台领取最老 pending job；`owns_extension_native_save_job(job_id)` 检查全局 namespace，传 `platform_slug` 时进一步限制 exact source；`complete_extension_native_save_job()` 只接受匹配 platform slug + job UUID + item key 的 in-progress row；`mark_unclaimed_extension_native_save_job_extension_required()` 与 `cancel_unclaimed_extension_native_save_job()` 只更新 pending；`expire_stale_extension_native_save_jobs()` 把不确定的 claimed write 固定完成为 `failed/extension_task_timeout`，绝不重放。所有读取返回新的 `dict` copy。
- 扩展 ledger 的 URL 只接受六平台 allow-listed HTTPS host，去 fragment、token 与 tracking query；YouTube 仅保留身份字段 `v`。结果 code 使用显式集合，result message 只从后端 status/code 映射生成，拒绝 Unicode category-C 输入，因此 Cookie、token、HTML 或平台响应正文不会进入 SQLite。
- 当前 task ledger 采用数据库生命周期保留：已返回任务没有 TTL、容量上限或自动删除，只有 starter 注册失败且未返回的 ledger 会回滚删除。未来若引入 bounded pruning，必须先定义轮询保留窗口、容量阈值以及 active/recent task 保护；该策略当前延期，不能假定存在后台清理。
- `claim_native_sync_task()` 保留为底层兼容 owner 入口；生产 service 使用上述快照 DAO 原子建立 ledger 与 ownership。执行前 `claim_native_sync_task_runner(task_id, runner_id)` 原子取得唯一 runner lease；fresh 的其它 runner 返回 `False`，stale lease 才允许接管。task heartbeat、item claim 与 pending release 都要求 runner token 匹配。runner 正常 / 取消退出释放余项；崩溃由 poll / manual-create 在 5 分钟后回收。所有 task / runner 边界拒绝空白 ID，公开 runner ID 还拒绝 `__openbiliclaw_` 保留前缀。
- `claim_native_save_item()` 还要求当前 `task_runner_id` 匹配，用 `execution_id` 原子执行 `pending → syncing`；`update_native_save_claim_route()`、`heartbeat_native_save_claim()`、`complete_native_save_claim()` 要求 `(list_kind, item_key, task_id, execution_id, status='syncing')` owner 完整匹配，旧 worker 无法刷新或完成新 owner。task 与 item heartbeat 使用 `open_connection()` 的独立短连接，不占用 process-wide write connection；service 在线程中调用并对 transient SQLite lock 做有界退避。`reconcile_stale_native_save_claims(task_id)` 供轮询恢复一个已知 task；`reconcile_stale_native_save_claims_for_list(list_kind, item_keys)` 供普通手动创建在 eligibility selection 前恢复匹配的崩溃遗留项。两者只把超过 5 分钟无 item heartbeat 的 `syncing` 写成 `failed/interrupted`。
- `list_native_sync_eligible()` 是只读诊断 / selection 视图；`list_native_save_states_by_task()` 只用于 live runner 工作集，durable polling 必须使用 `native_sync_task_exists()` + `list_native_sync_task_items()`。claim、route、complete、membership 删除和 stale/cancel recovery 都在同一事务同步更新 task item 快照。
- 初始化只在 `saved_sync_migrations` 缺少 `legacy_saved_tables_v1` 时迁移旧表。迁移用当时的 `content_cache` 恢复平台、内容 ID 与元数据；身份字段不完整时按兼容语义回落 `bilibili:<legacy bvid>`。解析出的 canonical key 同时写入旧 `watch_later.item_key` / `favorites.item_key`，之后的状态和删除不再依赖可变或可清理的 `content_cache`。marker 在两个列表都复制成功后写入，避免已删除的 normalized membership 下次启动复活；`legacy_saved_item_keys_v2` 只为此前已迁移数据库补稳定关联，不重新导入 membership。
- 旧 `add/remove/list/count/status` Bilibili wrappers 继续维护兼容表及其 stable `item_key` link，但用户可见读取以 normalized membership 为准。状态 / 移除 wrapper 会优先匹配 Bilibili key，否则只在裸 `content_id` 唯一对应一个 normalized membership 时解析跨平台 key；移除时按旧行已持久化的 `item_key` 同步清理迁移来源行。多个非 Bilibili 平台共享该裸 ID 时状态返回 `False`、移除也返回 `False`，不删除任何一侧。
- 平台 adapter、platform-neutral HTTP API，以及插件 side panel / 桌面 Web / 移动 Web
  保存与同步 UI 已接入同一 normalized store。Bilibili 保持 direct adapter，六平台已注册 extension-backed adapter；
  stable runtime broker wiring 也已完成。其它来源的本地 membership 仍可正常保存、列出和删除，
  手动同步会进入 durable extension job ledger。六平台 extension executors 已 6/6 接线并通过 fixture；
  2026-07-14 逐平台授权真实回归的 favorite 与 watch-later/fallback 均为 `synced/already_synced`。

`native_save_states` 完整字段如下：

| 字段 | 语义 |
|------|------|
| `list_kind`, `item_key` | 联合主键，同时外键引用 `saved_memberships`。 |
| `requested_action` | 用户请求的 `favorite` / `watch_later`。 |
| `resolved_action`, `resolved_target` | capability router 决定且由 execution owner fence 写入的平台动作 / 目标。 |
| `status` | `pending`、`syncing` 或逐次尝试的 terminal 状态。 |
| `task_id` | 当前 live batch owner ID；空串表示尚未被任务领取。durable polling 的 UUID 与结果位于独立 task ledger。 |
| `execution_id` | 单次 adapter 调用 owner token；仅 `syncing` 生命周期非空。 |
| `task_claimed_at` | task 领取时间，供“已领取但 runner 未启动”保护窗判断。 |
| `task_started_at` | runner 首次开始时间；非空后不会走 never-started 回收。 |
| `task_heartbeat_at` | batch runner 最近心跳；保护尚未逐项 claim 的后排 pending，崩溃后作为 5 分钟回收租约。 |
| `task_runner_id` | 当前唯一 batch runner token。升级时，已有 `task_id + task_started_at` 的 active 旧行写入保留 legacy sentinel，并在缺 heartbeat 时补 fresh lease，防 rolling upgrade 立即抢走旧 runner；lease stale 后新 runner才可接管。 |
| `last_error_code`, `last_error_message` | 安全归一化错误，不存平台响应正文或异常正文。 |
| `last_attempt_at` | execution claim / heartbeat 最近时间，供 5 分钟 stale 判定。 |
| `synced_at` | 最近一次 `synced` / `already_synced` 完成时间。 |

其父表字段：`saved_items(item_key, source_platform, content_id, content_url, content_type, title, author_name, cover_url, created_at, updated_at)` 保存 canonical 内容快照；`saved_memberships(list_kind, item_key, note, added_at)` 保存本地列表归属；`saved_sync_migrations(name, applied_at)` 保存 legacy migration marker。旧 `watch_later` / `favorites` 继续保留 `bvid, added_at, note, item_key` 兼容字段。

### Discovery Candidates

```python
from openbiliclaw.discovery.candidate_pool import DiscoveryCandidateWrite

count = db.enqueue_discovery_candidates(
    [
        DiscoveryCandidateWrite(
            candidate_key="bangumi:326",
            source_platform="bangumi",
            source_strategy="bangumi-ranked",
            content_id="326",
            title="攻壳机动队 S.A.C. 2nd GIG",
            content_type="subject",
            rating_score=9.2,
            rating_count=9959,
            source_rank=1,
            score_threshold=0.60,
        )
    ],
    max_pending_per_source=420,
)

rows = db.claim_discovery_candidates_for_eval(limit=30, claim_token="batch-a")
updated_ids = db.persist_claimed_discovery_candidate_evaluations(
    [
        {
            "candidate_id": rows[0]["id"],
            "status": "evaluated",
            "relevance_score": 0.82,
            "relevance_reason": "匹配用户最近的深度解释偏好。",
            "temporal_class": "evergreen",
            "temporal_confidence": 0.93,
            "temporal_reason": "作品解析的核心价值不依赖当前事件。",
            "temporal_validity_mode": "none",
            "temporal_valid_until": "",
            "temporal_scope": "none",
            "temporal_evidence": "",
            "temporal_state": "unknown",
            "temporal_evaluated_at": "2026-08-13T00:00:00Z",  # code-owned
            "temporal_next_review_at": "",  # code-owned
            "temporal_policy_version": "v2",  # code-owned
            "temporal_evidence_complete": True,  # code-owned validation
        }
    ],
    claim_token="batch-a",
)
ready = db.get_evaluated_discovery_candidates_for_admission(limit=30)
if ready:
    db.mark_discovery_candidate_cached(ready[0]["id"])

# `review_due` 分支使用同一队列复审，不计作 evaluator failure：
# db.requeue_discovery_candidate_for_temporal_review(rows[0]["id"], reason)

db.reset_claimed_discovery_candidates_to_pending(
    [rows[0]["id"]], claim_token="batch-a", reason="temporary LLM outage"
)
db.reset_stale_discovery_candidate_evaluations(max_age_minutes=30)
known_candidate_keys = db.get_existing_discovery_candidate_keys(["bangumi:326"])
known_content_ids = db.get_existing_content_cache_ids(["BV1xx411c7mD"])
cached_bilibili = db.get_unrecommended_content(
    limit=30,
    source_platforms=["bilibili"],
)
```

行为说明：

- `enqueue_discovery_candidates()` 用 `candidate_key` 去重；重复发现刷新 `last_seen_at` 与来源目录指标，不生成第二行。传入 `max_pending_per_source` 时，cap 统计 active `pending_eval/evaluating` 与 disposition 仍为 `eligible` 的 `evaluated`；生命周期扫描会把 `review_due` 重新排到 `pending_eval`，只有 `expired` 才终态化为 `rejected_temporal_stale`。超额且未领取的 active 行进入 `trimmed_capacity`，保留 `source_raw_ceiling:<family>` 审计原因，terminal history 不占 cap，`evaluating` / 非空 token 永不成为 victim。
- `rating_score / rating_count / source_rank` 是 additive 目录指标列，同时存在于 `discovery_candidates` 与 `content_cache`；旧数据库启动时自动补列。重复候选会刷新这些上游目录值，claim → evaluation → admission → cache round-trip 不丢失；评分不会写进 like/comment 字段。
- temporal v2 在 `discovery_candidates` 与 `content_cache` 同时保存 Agent 原子字段 `temporal_class / temporal_confidence / temporal_reason / temporal_validity_mode / temporal_valid_until / temporal_scope / temporal_evidence / temporal_state`，以及代码拥有的 `temporal_evaluated_at / temporal_next_review_at / temporal_policy_version / temporal_evidence_complete`。两表另有本地调度字段 `temporal_review_attempts / temporal_review_retry_at`，不属于 Agent 证据：候选复审和正式池复审都按 1 / 2 / 4 / 8 / 16 / 24 小时有界退避；未到 `retry_at` 的 candidate 不可 claim，也不计 raw、projected 或来源容量。若模型执行时间超过 claim 时租约，失败 / 中性结果与 orphan/release 出口会从落库时重新续到未来，不能立刻热循环。旧库 additive 补为中性 v1 缺省；v2 parser 会整组校验 class/mode/scope/state 组合、UTC 时钟与逐字 evidence，非法类别、非有限置信度、字段缺失或未 grounding 全部 fail-neutral；`freshness_only` 的 evidence 同样必须逐字存在，否则整组降为中性且不生成复审时钟。`cache_content()` 只允许权威复审证据原子覆盖已有强分类：必须完整、非中性、置信度 `>=0.80`，且为 grounded `scope=core`，或高置信 `evergreen/historical + mode=none` 的耐久结论；显式 `unknown`、低置信、hook-only、malformed、未 grounding、旧缓存或 raw 重抓都不能洗掉强证据，也不能从不同轮结果拼接字段。已带强证据的同 identity 发布时间采用保守的不前移合并，`discovery_candidates` 一旦被 claim 也冻结本轮 publication snapshot。候选评估持久化后，pipeline 重读 durable row，只有最终状态仍为 `evaluated` 才可 admission；旧 `suppressed` 或 `temporal_review_hold` 行只有在写锁内收到上述权威复审证据且 disposition 为 `eligible` 时才能恢复为 `fresh`；成功收敛后复审 attempts/lease 一并清零。
- `get_pool_candidates_needing_evaluation(limit=..., now=...)` 把 legacy 缺分类行和到期的 `temporal_review_hold` 合并进同一补分类窗口。选中 hold 时在同一个 `BEGIN IMMEDIATE` 中领取下一次复审租约：间隔按 1 / 2 / 4 / 8 / 16 / 24 小时有界退避，并优先 attempts 少、到期早的行，避免 provider 连续失败让同一条 hold 每分钟重试并饿死后续行；raw 重抓不清 lease，只有完整非中性复审结果才能清零。
- `claim_discovery_candidates_for_eval(limit=..., claim_token=...)` 原子领取 `pending_eval`，按来源 round-robin 混合取样，并把同一 token 写到整批；不传 token 时自动生成，兼容单次 CLI drain。
- `persist_claimed_discovery_candidate_evaluations(..., claim_token=...)` 返回实际更新的 ID 集合，只接受仍为 `evaluating` 且 token 匹配的行；完成后清空 token / claimed_at。`reset_claimed_discovery_candidates_to_pending()` 使用相同所有权条件，因此旧 worker 不能覆盖或释放重新领取的行。
- `requeue_discovery_candidate_for_temporal_review(candidate_id, reason="")` 以 CAS 方式把仍为 `evaluating/evaluated` 的单行恢复为 `pending_eval` 并清理 claim；三态 admission 与等待扫描在 `review_due` 时复用它，保留完整 temporal 证据作为复审审计输入，不把复审当失败 attempt。
- `get_evaluated_discovery_candidates_for_admission(limit=..., preferred_source_platforms=None)` 读取已完成评估但尚未写入 `content_cache` 且 disposition 为 `eligible` 的行，供池子从满池降回目标以下后重试 admission；pipeline 每轮读取前会把 `review_due` 等待行重排到 `pending_eval`、把 `expired` 行终态化。v0.3.181+（份额公平 spec 2026-07-20）：传入 `preferred_source_platforms` 时用 `CASE WHEN source_platform IN (…) THEN 0 ELSE 1 END` 把欠份额来源排到 FIFO 前面，防止超份额积压霸占取行窗口；缺省不传时排序与旧 `evaluated_at ASC` 逐字节一致。
- `count_evaluated_discovery_candidates_by_source()`（v0.3.181+）按 family 统计 disposition 为 `eligible` 的 `status='evaluated'` 待入池供给。
- `count_admission_waiting_discovery_candidates_by_source()`（v0.3.181+，Phase 8）按 family 统计全部 `pending_eval/evaluating` 与 temporal-eligible 的 `evaluated`。份额再平衡的「欠份额来源是否有供给等待」判定用它而非仅 `evaluated`——占坑者钉满池时欠份额来源根本到不了 `evaluated`，只认 `evaluated` 会让退坑永不触发；`review_due` waiter 会由独立扫描重排后再作为 pending 供给，`expired` waiter 不参与 projected 或份额供给并被终态化。
- `claim_discovery_candidates_for_eval(limit=..., claim_token=..., preferred_source_platforms=None)`（`preferred_source_platforms` 为 v0.3.181+ Phase 8 新增）：窥探窗口 ORDER BY 前置 `CASE WHEN source_platform IN (…) THEN 0 ELSE 1 END` 把欠份额来源拉进窗口,选择改两层 round-robin(先抽干 preferred 来源再抽其余);缺省不传时单层 round-robin 与旧行为逐字节一致。避免超份额积压霸占评估算力(评估注定入不了池的行是纯 token 浪费)。
- `demote_lowest_ranked_pool_rows(source_family=..., limit=...)`（v0.3.181+）把某来源族在正式池内 `relevance_score` 最低、`last_scored_at` 最老的至多 `limit` 条 `fresh`、未推荐、未 dislike 行置为既有 `pool_status='stale'`（不新增枚举），返回退坑行数；供份额温和再平衡使用，质量最高的行始终保留。
- `reset_discovery_candidates_to_pending([...], reason=..., max_attempts=5, max_batch_attempts=50, increment_attempts=True)` 释放 evaluator failure 中被 claim 的行；`increment_attempts=True` 时连续失败达到上限后进入 `failed_eval`。pipeline 对 batch 级 LLM/provider transient 会传 `increment_attempts=False`，不消耗单条候选预算，但会递增 `batch_eval_attempts`；达到较高 `max_batch_attempts` 后进入 `failed_eval`，避免永久坏 provider 让同一批候选无限 churn。
- `reset_stale_discovery_candidate_evaluations(max_age_minutes=...)` 将崩溃遗留的旧 `evaluating` 行释放回 `pending_eval`。
- `mark_discovery_candidate_cached()` / `reject_discovery_candidate(..., status=...)` 只改写 `evaluating` / `evaluated` 行；terminal rows 不会被 stale caller 复活或覆盖。常见 rejection status 包括 `rejected_low_score`、`rejected_duplicate`、`rejected_cache_admission`、`rejected_temporal_stale`、`rejected_recently_viewed`、`rejected_franchise_quota`。
- `count_discovery_candidates_by_status()` 与 `count_discovery_candidates_by_source_status()` 用于诊断待评估池生命周期分布。
- `count_pool_readiness()["evaluated_pending"]` 只统计 `discovery_candidates(status='evaluated')` 中 disposition 为 `eligible` 的子集，用于 projected inventory；raw `evaluated_waiting_total` 由 coordinator 单独读取，只负责让 review-due/expired-only 或 no-headroom 队列继续触发生命周期清扫。`admitted_pending_copy` 与 `get_pool_candidates_needing_copy()` 共用 `_load_admitted_pending_copy_rows_on()`，不会用宽泛的 `pending` 差值推算。`admitted_pending_available` 再把 unrestricted `copy_ready` 按每 topic 三条窗口占位，表达调度可用 `eligible_available_first=True` 先领取能立即增加公开库存的行，再按 copy-ready 水位需要领取深层 backlog。

### Evaluator Prefilter Shadow Audit

```python
inserted = db.record_prefilter_shadow_decisions(decision_records)
updated = db.complete_prefilter_shadow_decisions(outcome_records)
rows = db.query_prefilter_shadow_audit()
counts = db.prefilter_shadow_audit_counts()
```

- `record_prefilter_shadow_decisions()` 在落库前拒绝非 SHA-256 candidate identity、未净化平台/上下文、非 digest namespace/profile 和非法数值，避免调用方把原始候选或画像误写进审计表。
- `complete_prefilter_shadow_decisions()` 只按随机 `decision_id` 回填一次，并校验 `admission_result == (llm_score >= admission_threshold)`；进程在两步之间退出，或 provider / parse 没有产生 production-valid raw score，都会留下 incomplete row，gate 的 telemetry coverage 因而不能静默达到 100%。产品路径为兼容性生成的 synthetic 0 不进入该表。
- retention 常量来自 Phase 2 多日 shadow 校准窗口：30 天保证真实波次可跨日分层，20,000 行 ceiling 在 evaluator 90 条 hard cap 下仍覆盖 220 轮以上，同时为长期 daemon 提供与流量无关的硬上界。
- `query_prefilter_shadow_audit()` 只返回 privacy-safe 列；只读 gate 命令在 SQLite read transaction 中冻结当前最大 audit id，输出聚合 count/recall/strata/fail-open 结果，不初始化数据库、不调用 provider、也不写配置。
- `get_existing_discovery_candidate_keys(keys)` 返回任意 lifecycle status 下已经出现过的 `candidate_key`；`get_existing_content_cache_ids(ids)` 返回已经进入正式 `content_cache` 的 BVID / `content_id`。两者用于 `DiscoveryCandidatePipeline` 在 enqueue 前过滤历史重复，而不是等 SQLite `INSERT OR IGNORE` 静默吞掉后才发现供给不足。
- `get_unrecommended_content(limit, source_platforms=None)` 缺省保留跨源兼容读取；传平台集合时先在 SQL 中筛选再取平衡窗口，避免大量高分其它来源占满 `limit * 5` 窗口后把目标来源饿死。空 legacy 平台值只在目标包含 B 站时可见。

### Evaluator Learned Scorer Audit

```python
inserted = db.record_learned_scorer_shadow_audit(comparison_records)
rows = db.query_learned_scorer_shadow_audit(limit=20_000)
```

- `record_learned_scorer_shadow_audit()` 在 SQL 前调用完整 privacy-safe 校验；缺失 learned/LLM 分数、NaN/Inf、非法 hash / 类别 / decision id、越界阈值或与阈值不一致的 admission 结果都会整体拒绝。
- 表列使用 `NOT NULL` 保存完整对照；引擎只在完整 LLM 成员存在时构造记录，因此 provider / parse 失败不会把 synthetic 0 伪装成标签。
- 每次写入后裁剪 30 天前和超过 20,000 行的记录。`query_learned_scorer_shadow_audit()` 只返回有界隐私安全字段，供诊断与只读 gate 使用。

### Temporal Ranking Shadow Audit

```python
audit_id = db.record_temporal_ranking_shadow_audit(audit.to_storage_record())
rows = db.query_temporal_ranking_shadow_audit(limit=100)
```

- `temporal_ranking_shadow_audit` 使用固定策略版本 `temporal-ranking-shadow-v1`。顶层 count map 必须精确合计到候选总数，TopK 只接受 10 / 50 / 100 及固定 before/after/entered/exited schema；非法、非有限或不一致输入直接拒绝。
- 表中没有 BVID、`item_key`、内容 ID、标题、作者、URL、query、理由或画像字段。来源先净化为短枚举 token，年龄只保留 `<=1d / 1-7d / 7-30d / 30-180d / >180d / unknown` 桶，无法从记录反查单条候选。
- 每次 insert 后清理 30 天前记录，并只保留最新 5,000 行。RecommendationEngine 把它作为 best-effort observer；写库或校验失败只记录 WARNING，排序、MMR、admission 和 serving 均继续使用已经算出的分数。

### Bangumi Producer Ledger

`bangumi_discovery_runs` 按 mode 记录每轮消费 units、发现数、reason 与稳定 error code，用于 UTC 日预算、本地状态和最小调度间隔；`partial` 行表示本轮后续请求失败但此前候选仍被保留，和 `ok/empty` 一样按最终实际保留数扣预算。`bangumi_discovery_state` 保存 `ranked/latest` 按 subject type 的 cursor/total、每个 mode 的持久化类型轮转起点，以及 `cooldown_until`。两张表属于正常 schema 初始化，不由只读状态接口临时建表。`GET /api/sources/status` 只读这些本地行，打开设置页不会访问 Bangumi。

`source_producer_runs` 是抖音 / YouTube / X / 知乎 / Reddit / Linux.do 六个 producer 共用的**节流地板账本**（`platform` / `discovered` / `created_at`），取代这些来源原先记在进程内的 `_last_run_at`；XHS 与 Bangumi 分别继续使用自己的持久 runtime state / run ledger。共享账本只写入 `discovered > 0` 的轮次，因此同时解决两个反向缺陷：落库让地板在后端重启后依然有效（实测 Reddit 曾有 5/55 轮穿透 60 分钟地板），只记产出让零产出的轮次不再白白锁死一个完整 `min_interval_minutes`。读写收口在 `runtime/producer_cadence.py`；未接数据库构造的 producer 回落到进程内时间戳。
`source_producer_runs` 是多来源共用的**节流地板账本**（`platform` / `discovered` / `created_at`），取代抖音 / YouTube / X / 知乎 / Reddit 原先记在进程内的 `_last_run_at`。只写入 `discovered > 0` 的轮次，因此同时解决两个反向缺陷：落库让地板在后端重启后依然有效（实测 Reddit 曾有 5/55 轮穿透 60 分钟地板），只记产出让零产出的轮次不再白白锁死一个完整 `min_interval_minutes`。V2EX 的 `v2ex_discovery_runs` / `v2ex_discovery_state` 另存分支预算、来源 cursor、Topic/Reply 增强请求 units 和 rate-limit cooldown；微博的发现与 bootstrap 状态另存 `weibo_discovery_runs` / `weibo_discovery_state` / `weibo_tasks`；读写收口在各自 producer 与 task queue。未接数据库构造的 producer 回落到进程内时间戳。

V2EX bootstrap 另使用 `v2ex_tasks` 保存可领取、分批合并和 staged 完成状态。`v2ex_favorite_snapshot_items` 以 `(username_key, scope, item_key)` 保存当前收藏代次和连续缺失计数，`v2ex_favorite_snapshot_runs` 保证同一任务 / scope 的集合比较只执行一次，`v2ex_favorite_snapshot_effects` 是 `retract|restore` durable outbox；首次 init 的完整 scope 会种基线，此后仍只有扩展 canonical debug 中对应 `scope_complete=true` 才允许推进缺失。`v2ex_affinity_evidence`、`v2ex_node_affinity` 和 `v2ex_affinity_snapshot_effects` 按 resolved username 分区并以 item/effect key 保证重放幂等；投入阅读使用 `engaged_view:topic:<id>`，所以同一 Topic 重复阅读只增加一次 `engaged_view_count`。`auth_state` 只保存布尔登录心跳、公开 observed/accepted username、当前 active profile username，以及已验证 PAT username + 当前 PAT 的单向 fingerprint；不保存 PAT 或 Cookie。账号切换先把新账号导入事件标为 inactive，完整 Soul 构建提交后由 `activate_v2ex_profile_identity()` 原子切换账号 scoped 事件 owner；未携带 `source_identity` 的本地反馈 / 被动阅读事实保持可用，不会被误归旧账号。PAT / 浏览器身份读取分别受 6 小时 / 72 小时 freshness 约束，明确 PAT 拒绝和浏览器登出会删除匹配声明，网络错误不会。任务结果服务端会再次净化，不把 HTML、headers 或未知字段落盘。

### Discovery Keywords

```python
db.insert_pending_keywords("bilibili", ["AI 科普"], digest)
db.insert_pending_keywords(
    "bilibili",
    ["城市 声音 采样 纪录片"],
    digest,
    keyword_kind="explore",
    metadata_by_keyword={
        "城市 声音 采样 纪录片": {
            "aspect_id": "interest:field-recording",
            "inspiration_backend": "exa",
            "inspiration_id": "urban-soundscape",
            "expansion_id": "ambient-documentary",
            "angle_id": "craft-analysis",
            "grounding_source": "local_cache",
            "generation_reason": "从搜索预览里的城市声音采样横向扩展。",
        }
    },
)

regular = db.claim_keywords("bilibili", 5)
explore = db.claim_keywords("bilibili", 5, keyword_kind="explore")
grace_ledger = db.reconcile_pending_keyword_digests(
    "bilibili",
    current_digest,
    grace_hours=24,
    max_pending=30,
    blocked_terms=["明确避雷主题"],
)
pending_all_digests = db.count_pending_keywords_all_digests("bilibili")
coverage = db.get_keyword_interest_coverage_snapshot()
db.record_keyword_interest_selection(["独立游戏叙事"], query_kind="regular")
stats = db.get_keyword_cohort_stats(window_days=14)
evidence = db.search_local_inspiration_evidence("独立游戏 机制", limit=5, lookback_days=365)
db.migrate_keyword_interest_labels({"AI 工具": "AI 工程化"})
```

行为说明：

- `keyword_kind="regular"` 是默认值，供普通平台 search / producer 消费。
- `keyword_kind="explore"` 是 `KeywordPlanner` 写入的 B 站探索 query 候选池，只有 `ExploreStrategy` 的 planner-backed 分支会 claim。
- 在途唯一约束包含 `(platform, keyword, profile_kw_digest, keyword_kind)`；同一个 query 可分别作为 regular 与 explore 生命周期存在，互不抢占。
- `history_keywords()` 与 `recycle_oldest_used()` 也默认只读 `regular` 池；需要查看 / 回收探索池时必须显式传 `keyword_kind="explore"`。
- `reconcile_pending_keyword_digests()` 在短 `BEGIN IMMEDIATE` 事务内整理指定平台和 kind 的 pending 库存：当前 digest 行优先；旧 digest 行按“宽限时间 → blocked term → 归一化重复 → 总 cap”顺序保留或过期。保留行不重写 `profile_kw_digest`、创建时间或 inspiration/axis/interest 等溯源；只触碰 `pending`，不会复活或改写 `claimed/executing/used/failed/expired`。返回值只有 `current/reused/expired_aged/expired_blocked/expired_excess` 聚合计数。
- `count_pending_keywords_all_digests()` 是完成上述整理后的水位口径；旧的 `count_pending_keywords(platform, digest)` 保留给 grace=0 与能力缺失时的硬过期回退。`history_keywords(..., include_pending=True)` 只由成功完成 grace 整理的 regular planner 使用，防止尚可领取的旧库存被同族新词重复生成；默认仍为 `False`。
- `pending → claimed → used/failed/executing` 状态机保持不变；租约回收和失败回滚对两类 keyword 都生效。平台瞬时故障可通过 `requeue_keyword_after_transient_failure(keyword_id)` 把 claimed / executing 原子退回 pending，且不消耗 attempts；当前 XHS `rate_limited` 回调使用该入口。
- `metadata_by_keyword` 是可选溯源字段，不参与唯一约束；同一个 in-flight query 的去重仍只看 `(platform, keyword, profile_kw_digest, keyword_kind)`。当前支持记录 `aspect_id`、`inspiration_backend`、`inspiration_id`、`inspiration_terms`、`expansion_id`、`angle_id`、`query_kind`、`source_domain`、`source_interest`、`grounding_source`、`generation_reason` 和 `normalized_keyword` 等字段，供 query 丰富度诊断和后续反馈学习使用。
- `search_local_inspiration_evidence(query, limit=..., lookback_days=...)` 是 local-first inspiration grounding 的 Phase 1 DAO：它只读 `content_cache`，用 CJK 2-gram / token overlap 做相关性筛选，并在 B站 legacy 行缺少 `content_url` 时用 `bvid` 合成视频 URL；返回值只作为灵感 evidence，不写候选池。
- `record_keyword_interest_selection(labels, query_kind=..., selection_scope=...)` 在灵感词完成装配与近期 query-family 过滤后，只为真正留下关键词的 realized interests 写入 selection ledger；被平台配额挤掉、错误归因或只生成重复词的兴趣不会被虚假降权。production 运行使用 `selection_scope="production"`，`keyword-inspiration-dry-run` 使用独立的 `preview` scope，因此多次 dry-run 可以验证冷却轮转，但不会污染正式运行的抽样状态。写入时会清理 30 天前的 selection ledger 行，coverage snapshot 默认只统计最近 14 天。
- `get_keyword_interest_coverage_snapshot()` 返回以 `source_interest` / `pool_topic_label` / `topic_group` 为 key 的 coverage bucket，包括 `interest_selection_count`、`generated_keyword_count`、`selected_keyword_count`、`yield_count`、`candidate_count`、`candidate_share`、`admitted_count`、`admitted_share`、候选 dominant platform / content type 和入池 dominant content type 信息。join 前会统一走 `_normalize_match_text()` 折叠大小写和空白漂移，但输出仍保留可读 display label。`KeywordPlanner` 用它降低已抽中过、已生成过很多词、raw candidate 高频或最终入池占比高的二级兴趣下一轮被抽中的概率；只在 raw candidate 层高频、但尚未 admit 的兴趣也会提前被识别出来。
- `migrate_keyword_interest_labels(mapping)` 会按同一归一化规则匹配现有 keyword `source_interest` 和 selection ledger `source_interest`，把画像整理后的旧标签迁到新标签；`ProfileConsolidator` 在 `--apply` 时记录被迁移行，`--revert` 会按行恢复，避免简单反向 mapping 误伤原本就叫新标签的 keyword / selection 记录。
- `get_keyword_cohort_stats(window_days=14)` 按 `inspiration_id` 溯源把窗口内关键词分为 `inspiration` 与 `merged` 两组，输出 generated / claimed / claimed_rate、yield-attributed admissions、admissions_per_claimed_keyword、mean_delight、distinct_topics 和 topic_diversity_per_100_admissions；同时输出 `interest_selection.production/preview` 的 total、distinct、by_source_interest、by_query_kind 和 last_selected_at，用于诊断抽样轮转；机械 replace gate 在样本不足、准入率低于 `0.8x`、delight 低于 `0.95x` 或 topic 多样性没有严格更高时均不允许开启 replace。

### Discovery Inspiration Cache

```python
from datetime import UTC, datetime

from openbiliclaw.discovery.inspiration import AxisRow

db.upsert_inspiration_axes(
    [
        AxisRow(
            interest_label="独立游戏叙事",
            axis_label="环境叙事",
            axis_kind="method",
            example_terms=("碎片化线索", "空间讲故事"),
            evidence_refs=("https://example.test/a",),
            yield_score=0.42,
        )
    ],
    bump_usage=False,
)

axes = db.list_inspiration_axes(
    ["独立游戏叙事", "动画制作"],
    limit=4,
    now=datetime.now(UTC),
)

db.upsert_discovery_inspiration_seed(
    platform="bilibili",
    profile_kw_digest=digest,
    aspect_id="interest:game-design",
    query_kind="explore",
    probe_backend="exa",
    freshness_digest="2026-W27",
    seed_query="独立游戏 叙事设计",
    inspiration_id="environmental-narrative",
    source_terms=["环境叙事"],
    evidence_titles=["叙事游戏如何设计碎片化线索"],
    evidence_urls=["https://example.test/a"],
)

db.upsert_discovery_inspiration_expansion(
    platform="bilibili",
    profile_kw_digest=digest,
    aspect_id="interest:game-design",
    query_kind="explore",
    inspiration_id="environmental-narrative",
    expansion_id="fragmented-clues",
    relation="adjacent-mechanic",
    text="碎片化线索",
    curator_decision="keep",
    curator_score=0.86,
)

seeds = db.list_discovery_inspiration_seeds("bilibili", digest)
expansions = db.list_discovery_inspiration_expansions("bilibili", digest)
db.increment_discovery_inspiration_yield(
    "bilibili",
    digest,
    aspect_id="interest:game-design",
    query_kind="explore",
    probe_backend="exa",
    freshness_digest="2026-W27",
    seed_query="独立游戏 叙事设计",
    inspiration_id="environmental-narrative",
)
```

行为说明：

- `discovery_inspiration_axis` 记录可复用的 inspiration 轴库，字段包含 `axis_id`、`interest_label` / `interest_id`、`axis_label`、`axis_kind`、`example_terms`、`evidence_refs`、`source`、`time_sensitive`、`freshness_ttl_days`、`yield_score`、`admissions`、`use_count`、`status`、`created_at`、`last_used_at`、`last_refreshed_at`，以及 Phase 2 通过容错 `ALTER TABLE ... ADD COLUMN` 迁移补上的 `window_uses`（trailing window 内被实际消费的关键词行数，成绩公式与退休阈值的分母）和 `yield_backfilled_at`（上次 yield 回填时间戳，用于节流）；索引 `idx_discovery_inspiration_axis_interest(interest_label, status)` 支持按兴趣快速取 active 轴。注意 `window_uses` 与选取簿记 `use_count`（该轴被喂给 LLM 的次数，多样性 tie-break 用）分工不同：成绩与生命周期一律用 `window_uses`。
- `upsert_inspiration_axes(axes, bump_usage=True)` 会按 `axis_id` 插入或合并：`example_terms` / `evidence_refs` 做 JSON 数组合并，`yield_score` / `admissions` 取历史与新值的较大值；`bump_usage=True` 时递增 `use_count` 并刷新 `last_used_at`，preview 只想持久化轴库时可传 `False`。合并进 `status='retired'` 行时只更新证据、**不复活状态**（防坏轴借尸还魂）；合并进 `stale` 行时允许被新鲜 upsert 复活（不对称是有意的：话题可以回来）。该 DAO 保持**同步、零 I/O**，embedding 近邻合并的目标解析在 pipeline 层完成后才把规范化轴交给它。
- 每个 `interest_label` 最多保留 16 条 `status='active'` 轴；超过上限时按有效分（`window_uses>0` 的轴用真实 `yield_score`，从未被消费过的轴才用 `max(yield_score, 0.3)` 探索 prior 地板）、`last_refreshed_at`、`use_count`、`axis_kind` 和 `axis_label` 排序保留前 16 条，其余标为 `stale`。
- `list_inspiration_axes(interest_labels, limit, now)` 只返回 active 且未过 `freshness_ttl_days` 的轴，并按每个兴趣独立排序：`freshness × 有效分` 优先（有效分同上——消费过的轴按真实 `yield_score` 排序，低分立刻下沉；未消费轴用 prior 0.3 地板），之后依次用 `last_refreshed_at` 较新、`use_count` 较低、`axis_kind` 排名和 `axis_label` 做 tie-break；`limit` 是每个兴趣的返回上限，不是全局总量。
- `list_inspiration_axes_by_source(source, *, min_yield=0.0, limit, now)`（Phase 2.3）按 **`source` 过滤（不按 interest_label）** 返回一条全局排序列表，供跨域 explore 通道复用自己那一族 `source='explore'` 的高产轴。生命周期镜像 `list_inspiration_axes`：`status='active'`、复用**同一个** `_axis_is_time_expired(row, now)` 抑制过期时效轴、复用**同一个** `_axis_list_sort_key` 排序（不复制排序逻辑）；额外用 SQL `yield_score >= min_yield` 按**原始** `yield_score`（回填后的真实成绩，非 prior 地板值）做高产筛，`limit` 是全局上限。explore 轴的 `interest_label` 是跨域话题、不匹配任何 like 兴趣，所以只能靠 source 捞出；配合 Phase 2 按 `axis_id` 的 yield 回填即构成舒适区扩张闭环。
- `backfill_inspiration_axis_yield(*, window_days=30, now)`（Phase 2）是 **trailing-window 全量重算（SET 语义），幂等按构造**——同一数据跑两遍全表字节相同，无水位线。它聚合 window 内 inspiration cohort 的 `discovery_keywords` 行：归属只有当 `angle_id` 在轴表真实存在时才直接用，否则回退 `derive_inspiration_axis_id(source_interest, angle_label)` 现场重导（存在性校验防 legacy `angle_id==angle_label` 恰好带 `axis:` 前缀的误判）；`window_uses = COUNT(status ∈ {claimed, executing, used, failed})`（离开过 pending 即算消费，`pending`/`expired` 不算），`admissions = SUM(yield_count)`，然后 SET `yield_score = (admissions + 0.3) / (window_uses + 1.0)`（Laplace 平滑，常数 0.3 刻意等于探索 prior，未使用轴回填后 score 恰为 0.3）与 `yield_backfilled_at = now`；无 window 行的轴 SET 为 `0 / 0 / 0.3`。
- `apply_inspiration_axis_lifecycle(*, now)`（Phase 2，回填后同 tick 调用）执行三条确定性迁移并返回 `{"staled", "retired", "purged"}` telemetry：`time_sensitive=1` 且超 `freshness_ttl_days` 的 active 轴 → `status='stale'`（真正落库，不再只读取时过滤）；`window_uses >= 5` 且回填后 `yield_score < 0.08` 的 active 轴 → `status='retired'`（给过 5 次消费机会仍近乎零产出，如 0.3/6≈0.05）；`status IN ('stale','retired')` 且 `last_refreshed_at` 早于 90 天的行物理 DELETE。阈值全为模块级常量（`>=5` / `<0.08` / 90 天），`now` 注入可单测。
- `discovery_inspiration_probe_cache` 以 `(platform, profile_kw_digest, aspect_id, query_kind, probe_backend, freshness_digest, seed_query, inspiration_id)` 为主键；同一个搜索探针的证据可以刷新，但 `selected_count` / `yielded_count` 不会被 upsert 清零。
- `discovery_inspiration_expansion_cache` 以 `(platform, profile_kw_digest, aspect_id, query_kind, inspiration_id, expansion_id)` 为主键，记录 hop、relation、detail axes、curator decision / score / feedback、status 和 yield 计数。
- 这些表由可选 `KeywordPlanner` inspiration stage 写入：轴库复用和 fresh grounding evidence 共同进入单次 `discovery.keyword_inspiration` 轴 + keyword 调用；旧 probe / expansion cache 仍保留历史证据和 yield 诊断。`increment_keyword_yield()` 在记录新的 `(keyword_id, content_id)` yield 后，会 best-effort 回填对应 inspiration / expansion 的 `yielded_count`，重复 content 不会 double-count。

### Pool Readiness

```python
readiness = db.count_pool_readiness()
assert set(readiness) == {
    "available",
    "raw",
    "pending",
    "pending_eval",
    "evaluated_pending",
}

raw_by_source = db.count_pool_raw_material_by_source()
```

行为说明：

- `available` 与 `count_pool_candidates()` 保持推荐 serve 同口径。
- `raw` 包含正式池 fresh raw material 和 `discovery_candidates` 中尚未缓存的候选。
- `pending` 独立计算，不用 `raw - available` 近似，避免 `seen_items` 已命中的内容被误算为待整理。
- `pending_eval` 统计当前可领取的 `pending_eval` 与已在途 `evaluating`；尚未到 `temporal_review_retry_at` 的 deferred review 不计入。`evaluated_pending` 只统计已评估、尚未 admission 且 temporal disposition 为 `eligible` 的候选。`review_due` / `expired` 的 durable evaluated 行由 raw waiting 信号继续驱动清扫，但不计入 projected inventory；前者会重新排队，后者才进入拒绝终态。

### Delight Readiness

```python
rows = db.get_delight_candidates(min_delight_score=0.75, limit=20)
count = db.count_delight_candidates(min_delight_score=0.75)
backlog = db.get_pool_candidates_needing_delight_score(
    limit=30,
    min_delight_score_for_reason=0.75,
)
```

行为说明：

- `get_delight_candidates()` 与 `count_delight_candidates()` 使用相同的 copy-ready 条件：`pool_expression / pool_topic_label` 必须非空，且 `delight_reason / delight_hook` 必须是它们的同步快照。
- evaluator 的 `relevance_reason` 只是评估诊断字段；即使旧数据已把它写进 `delight_reason`，快照不一致的行也不会被任何 pending/API/runtime 出口读取。
- `get_pool_candidates_needing_delight_score()` 只领取正式文案已就绪的行；未生成推荐词的内容不会开始 delight 打分。`update_delight_score()` 返回是否真正写入，并在 SQL 层要求文案非空；带 `reason/hook` 的写入还必须与当前正式快照逐项一致，从查询到写入之间文案发生变化时会整体拒绝。profile floor 或动态阈值升高时，copy-ready 且已低于新门槛、仍带旧 `reason/hook` 的行会被领取并清空。
- 动态阈值样本只统计 copy-ready 的已打分行，旧版在推荐词生成前留下的分数不再影响 Top 10% 校准。
- 普通推荐的 delight claim guard 以“已送达，或达阈值且正式文案快照已精确同步”为准。任意非空 evaluator reason 不能占位，尚未生成文案的行仍留给 expression-copy backlog，copy-ready 但尚未同步的行也保留给 profile-aware scorer 作最终门槛判断。

### Platform Availability

```python
snapshot = await db.load_pool_platform_availability_async()
assert snapshot.total_available == sum(snapshot.by_platform.values())

rows = db.get_pool_candidates_for_platform("zhihu", limit=10)
assert len(rows) <= snapshot.by_platform.get("zhihu", 0)
```

行为说明：

- `total_available` 与 `count_pool_candidates()` 同口径（fresh、非 dislike、达 admission floor、文案与分类齐全、链接可用、未被 delight 认领、未进推荐历史、未命中持久化已看账本，并遵循当前 topic window）。
- `by_platform` 用 `_pool_source_family()` 归类，兼容 `source_platform` 为空的 legacy `source_strategy` 前缀行。
- 计数与平台定向选片来自**同一份** canonical 行集合，不存在「Tab 显示有货但选片器取不到」的分叉。这里的必然推论是：被全局 topic window 挤出的行既不计数也不可选——它本来就不是库存。
- 读取走独立短连接，不把进程共享连接交给线程；读取失败向上抛出，由 API 转成可诊断 5xx，绝不返回全零。

### Atomic Pool Maintenance

```python
result = db.maintain_pool_inventory(
    target=600,
    raw_ceiling=1200,
    source_share_quotas={"bilibili": 480, "zhihu": 120},
    raw_source_share_quotas={"bilibili": 960, "zhihu": 240},
    recover_suppressed=True,
    max_mutations=50,
)
```

`PoolMaintenanceResult` 是 immutable 观测快照，记录维护前后 available/raw、保护量、各层 trim、来源拆分、延期 quota、不收敛 raw excess、`mutation_count/has_more`、锁等待与各阶段耗时、rollback 原因。事务先通过连接感知的 `_load_available_pool_candidate_rows_on()` 读取唯一 canonical servability SQL，按现有 serve 排序保护 `min(available_before, target)`；topic/source/stale/explore 配额不能牺牲保护行，超额记入 deferred 字段。runtime 每 tick 最多执行 8 个 50 行批次，每批提交后释放写锁并让出事件循环；更大积压留给后续 tick。普通 tick 在 readiness fingerprint 不变时跳过重扫描，每 10 分钟做一次安全巡检以覆盖时间驱动 stale 和同计数组成变化。

当 canonical available 低于 target 且 `recover_suppressed=True`（默认）时，同一事务会在 victim planning 前复用已经付费完成评分和文案的历史行。候选必须仍为 `pool_status='suppressed'`、`recommended_at IS NULL`、未出现在 `recommendations`、未近期看过、非 dislike / shown / `purged_by_dislike`、达到统一 admission floor，且 expression / topic label / style / topic group 完整、链接可打开、不是 XHS 本人内容，也未被当前 delight guard 认领。恢复排序先看当前 source family 缺口，再按 relevance、`last_scored_at` 和 BVID；source quota 只影响顺序，不阻止其它来源填满全局缺口。恢复循环在内存维护 source/topic/available 计数，已占满公开三席窗口的 topic 直接跳过；批量试探结束只做一次 canonical available 复查，只有“可见恢复数 = available 净增长”的一一对应结果才保留，低排名未进入 SQL topic window 或仅置换既有行的试探会在同一事务还原为 `suppressed`。这样既不退回逐行 window-function 扫描，也不会让下一 bounded batch 把同一批行裁掉后再次恢复。新恢复且 canonical 可用的 ID 在本批后续 trim 中受保护；重复维护最终成为 no-op，`recovered_suppressed` 只统计本批真正增加可用库存的净恢复数。

若 `BEGIN IMMEDIATE` 遇到交互 writer，75ms 后抛出 `PoolMaintenanceDeferredError`，runtime 把本轮维护延后而不是等待进程默认的 30 秒。其它 canonical snapshot 读取在 `available_before` 建立前失败时抛出 `PoolMaintenanceSnapshotUnavailableError`，不会返回伪造的零库存结果。只有 snapshot 已取得后的 victim/invariant 失败才返回 `rolled_back=True`，其中 `available_before` 保留真实事务前值。

raw ceiling 同时统计 `content_cache` 与 `discovery_candidates` 的 active raw material，但会先从容量口径排除 disposition 为 `review_due/expired` 的 `evaluated`；这些 waiter 由生命周期扫描分别重新排队或终态化，不会迫使新 `pending_eval` 为它们让位。其余 victim 顺序为 unready content → 未领取 `pending_eval` → 未领取且仍 eligible 的 `evaluated` → 非保护 ready reserve。候选不删除，而是 terminalize 为 `trimmed_capacity`，`eval_error='pool_raw_ceiling'`；`evaluating` 与任意 token-owned 行永不裁剪。若保护行加 active claim 已超过 ceiling，保留所有权与可用库存、报告 `untrimmed_raw_excess` 并记录一次稳定 WARNING；当完整 raw plan 已无 victim 时 `has_more=False`，等 claim 完成或库存指纹变化后再维护。提交前重新计算 canonical available，必须满足 `available_after >= min(available_before, target)`，否则整笔 `BEGIN IMMEDIATE` 回滚。

### Recommendation Serve Snapshot

```python
snapshot = await db.load_pool_serve_snapshot_async(limit=40)
# 平台定向请求：只装载该 canonical 平台的候选，且不做跨平台保底补位
scoped = await db.load_pool_serve_snapshot_async(limit=40, source_platform="zhihu")
persisted = await db.persist_pool_serve_async(recommendation_rows, selected_bvids)
retired = db.retire_temporally_stale_pool_items()
```

这些操作由 database-owned serve executor 串行执行，并为每次操作创建/关闭独立连接。snapshot 先调用幂等的兼容方法 `retire_temporally_stale_pool_items()`；尽管保留旧方法名，它现在执行完整三态迁移。常见的“全部 eligible”路径只做只读 preflight，不取得写锁；发现 due item 后才在短 `BEGIN IMMEDIATE` 中重读、复判，把 `review_due` 的 `fresh` 行改为 `temporal_review_hold`，把 `expired` 行改为 `stale`。随后显式只读事务提供一致的 readiness、候选窗口、平台补位、`seen_items` 与 curator/feedback rows。写锁暂不可用时 snapshot 仍以同一纯策略在内存中排除两类行，不会因持久化迁移延迟而展示。

persist 不再假定 snapshot 后状态不会变化：同一个 `BEGIN IMMEDIATE` 会按最终 `selected_bvids` 重读 `pool_status` 与完整 temporal 证据组，重新计算三态；只有仍为 `fresh`、尚未生成推荐且仍为 `eligible` 的行才写入推荐历史并标为 shown。竞态中变成 `review_due` 的行原子写为 `temporal_review_hold` 并计入 skipped，变成 `expired` 的行写为 `stale` 并进入 `temporally_stale_bvids`。`PoolServePersistResult` 返回 `recommendation_ids + committed_bvids + temporally_stale_bvids + skipped_bvids`，RecommendationEngine 只把真正提交成功的条目返回给调用方，并把并发消费的 skipped 行从本轮库存估算扣除，从而关闭复审/过期边界与其它进程状态变化造成的 TOCTOU 窗口。旧 adapter 只返回 recommendation IDs 时保留兼容行为。

`source_platform` 为可选 canonical 平台作用域：非空时候选行改由 `_available_platform_rows_on()` 装载，并**跳过平台保底补位**——给平台定向批次补进别的平台，正是该作用域要防的泄漏。`readiness` 始终保持全池口径（它是 API 上报与补货判断依据的库存），排序、持久化与 shown 提交全部与跨平台路径共用。省略该参数时调用形状与行为与引入前一致。

平台定向窗口同样经过 `_balance_pool_rows()` 的 topic 轮转再截断，与 `get_pool_candidates()` 一致。若按 relevance 直接截断，少数 topic_group 会占满整个候选窗口（实测同样 12 条窗口只覆盖 4 个组 vs 轮转后的 12 个组），下游 MMR / 多样性拿到的选择面比混合流窄得多——平台 Tab 会仅仅因为候选装载方式不同而显得比「全部」重复。后台维护有自己的 executor/连接，因此阻塞 maintenance worker 不会把交互读排在同一队列后面。交互写锁每次最多等待 250ms，沿既有 8 次有界重试最坏约 2.14s，保留 HTTP 3s 尾延迟预算。

### Admission Cleanup

```python
db.set_admission_min_score(0.60)
db.suppress_low_score_pool_items()
db.suppress_low_confidence_recommendations()
```

行为说明：

- `set_admission_min_score()` 由 runtime 在配置加载 / 热重载时调用；storage 不直接读取 runtime state 或 `config.toml`。
- `suppress_low_score_pool_items()` 会把 `content_cache.relevance_score` 低于阈值且仍可能展示的 `fresh / shown / suppressed` 行标为 `suppressed`。
- `suppress_low_confidence_recommendations()` 会把低于阈值且尚无用户反馈的历史推荐标为 `feedback_type='suppressed_low_score'`。
- `Database.initialize()` 会用默认阈值执行一次上述清理，处理旧版本已经入池 / 入历史的低分数据。

## 配置项

存储层本身不新增独立配置。本次涉及的运行时上限仍来自：

| 配置项 | 说明 |
|--------|------|
| `scheduler.pool_target_count` | 正式可换推荐池目标；达到后 runtime 不再 discovery / drain。 |
| `[scheduler.pool_source_shares]` | 平台族配比；raw material by-source 统计用它计算 source headroom。 |
| `discovery.admission_min_score` | 统一推荐池入池最低分；runtime 会注入给 `Database`，用于池读取和旧数据清理。 |
| `storage.db_path` | SQLite 数据库路径。 |

## 设计决策

1. **待评估池和正式推荐池分离**：`discovery_candidates` 只表示“已经找到但还未成为推荐素材”，`content_cache` 才是 recommendation serve 的正式候选池。
2. **时效判断发生在 Agent 看过正文之后**：原始候选必须先入 `discovery_candidates`，Evaluation Agent 才能判断时效到底影响核心价值还是仅是标题钩子；完整证据组落库后、写入 `content_cache` 前执行三态 eligibility。这样既不会在 raw 抓取阶段误杀，也能让有确定证据的过期内容永远不进入可服务池。
3. **入池判断不是永久通行证，复审也不是死亡判决**：`breaking/current/versioned` 的 1 / 14 / 60 天只安排复审，旧 v1 3 / 60 天也只进入 `review_due`，`versioned` 的 120 天准入 TTL 同样是复审而非死亡线。canonical 读取 fail-safe 排除、serve snapshot 状态迁移和最终写事务复核共用同一纯策略；`temporal_review_hold` 可由完整新评估恢复，只有 grounded deadline / terminal state 才永久 stale。排序 bonus 不能代替这些正确性边界。
4. **来源只影响身份和统计**：候选 dedupe key、source share 和 prompt 上下文会保留来源；喜好判断统一交给 discovery evaluator。
5. **池满时不继续消耗**：runtime 以 `count_pool_candidates()` 的真实可换数为上限判断，正式池满时不 claim / evaluate 待评估候选。
6. **评估和入池可分步恢复**：`evaluated` 表示“已经完成喜好与时效评估但还没 admission”，不是失败终态；池子恢复容量后会优先重试入池，并在那一刻重新计算 temporal eligibility。到复审点会回 `pending_eval`，已入池 hold 行也复用现有评估链；batch 级 provider transient failure 释放回 `pending_eval` 且不递增 `eval_attempts`，但会递增 `batch_eval_attempts` 作为高阈值熔断；只有调用方显式要求递增 attempts 的可归因失败才会使用常规 `eval_attempts` 预算。
7. **状态机必须防 stale caller**：`evaluating` 有过期回收，terminal rows 有 status guard，避免进程 crash 或并发 caller 让候选永久卡住或复活。
8. **pending 不是 raw 减 available**：持久化已看、缺文案、缺分类、缺链接、待评估属于不同诊断含义，必须分开统计。
9. **低分、待复审和确定过期都在正式推荐边界落地**：相关性 admission 与 temporal eligibility 由 discovery 的结构化结果决定；storage 复用确定性规则，阻止旧脏数据、suppressed 低分复活、raw 覆盖证据和未来绕过入口继续进入可展示读取路径。
10. **keyword kind 是用途隔离，不是平台隔离**：`regular` 和 `explore` 共享同一张 `discovery_keywords` 表与生命周期，便于复用 claim / lease / yield 基础设施；但默认 claim / history / recycle 只读 `regular`，避免探索 query 被普通 search 提前消费或被常规补货历史污染。
11. **`style_key` 迁移只改已知旧值**：历史安装用户的本地 SQLite 里可能已有 `deep_dive`、`story_doc`、`lifestyle` 等旧内容风格 key。初始化迁移会把这些已知值物理改写为 `deep_focus`、`story_immersion`、`daily_wander` 等新观看模式；未知自定义值会原样保留，避免误删无法识别的历史数据。
