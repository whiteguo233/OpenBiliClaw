# Runtime Module

每个 **API daemon** runtime generation 只拥有一个 expression copy coordinator：8 条立即、尾批固定 3 秒、单轮最多 60，零进展退避 15 秒，60 秒仅作 safety wake。停止 generation 会取消 collector、gate waiter 与运行中的 provider callback；状态接口暴露 pending/state/deadline/last completed/error。OpenClaw direct composition 不启动 daemon loop，故不创建该 coordinator；它在 inline admission 后同步 drain 最多 4 条 durable copy，且不在同一交互请求内做 split retry：有效 subset 立即入 canonical pool，剩余行保持 pending 供下次请求处理，返回前没有遗留 provider/copy task。参数来自 2026-07-12 生产日志校准。

> API runtime 启动时创建一套共享 LLM gate 并注入主服务、Soul 与 refresh；在任何 provider 工作可启动前用 canonical database available 初始化 `healthy/refill/empty`，后续 controller readiness、原子维护、推荐池状态与候选 snapshot 持续同步。runtime status 暴露 total/background 以及 refill/maintenance active、waiting 和 inventory state。

gate 属于 `RuntimeContext` 的稳定部分：热重载构造成功后在同一对象上调整 total/background，因此旧 HTTP/对话学习调用与新服务仍竞争同一个真实总上限。

`create_app()` 的显式依赖注入路径会先核对 Soul 内部 service 与 runtime controller 的 gate：单侧提供时采用该对象并补齐另一侧，两侧同对象时直接采用，两侧不同则立即抛出清晰错误；旧测试 double 都未暴露 gate 时才按配置创建一套新的共享对象。采用外部 gate 时不会先按配置静默改写其容量，后续正常热重载仍在该对象上显式 reconfigure。

同一核对还覆盖显式 `dialogue` 的 declared/service gate、recommendation service，以及 runtime controller / account-sync 内可见的 Soul、recommendation、discovery service；任何非空身份冲突都在写回前失败。真实 `SocraticDialogue` 的显式 service 与 `_build_service()` fallback 最终都引用 context gate；没有相关属性的旧 double 继续兼容。

实现读取真实引擎字段：Soul / Dialogue / Discovery 为 `_llm_service`，Recommendation 为 `_llm`，AccountSync 为 `soul_engine`；参数化结构测试会在这些类改名但注入审计未同步时失败。

## 概述

`src/openbiliclaw/runtime/` 负责后端 daemon 的长期运行能力：后台刷新、账号同步、扩展在线账号信号周期回拉、反馈批学习调度、运行时事件流、浏览器插件 presence gate、自动更新和任务生命周期管理。FastAPI 启动后会通过 `RuntimeContext` 持有这些 runtime 服务，配置热重载时重建可替换组件。

## 已实现功能

| 功能 | 状态 | 说明 |
|------|------|------|
| Windows 后台子进程不再弹控制台窗口 | ✅ | 新增 `openbiliclaw/proc.py::no_window_kwargs()`：Windows 下返回 `creationflags=CREATE_NO_WINDOW`，其它平台返回空 dict，可无条件 splat。后端自行发起的全部外部命令都已接入——Reddit 的 `rdt` / `opencli`（含状态探测与每个关键词一条的发现命令）、自动更新的 `git`、`agent-browser` 版本探测与命令、mcporter 灵感检索、SQLite 修复用的 `sqlite3` / `lsof`、托管 ollama 退出用的 `taskkill`。用户在终端主动敲的命令（`openbiliclaw` CLI、`codex login`、dev 用 optimizer）保持原样，它们本来就有自己的控制台且需要交互提示。`tests/test_proc_no_window.py` 用 AST 扫描 `src/` 全量守住这条约定：任何新的 `subprocess.run` / `Popen` / `create_subprocess_exec` 漏掉该 kwarg 即测试失败（`os.name == "nt"` 分支的 else 支路自动豁免）。第三方库自己起的孙进程不在该 flag 覆盖范围内，已知的一处是 rdt-cli 凭据超 TTL 时的 `uv run --with browser-cookie3`，靠 `_RDT_CREDENTIAL_TTL_SECONDS` 比 rdt 阈值提前 6 小时判过期来规避（见 `sources/reddit_tasks.py` 常量注释）。X 的 twitter-cli 与 YouTube 的 yt-dlp 均为进程内库调用，不起子进程。 |
| 桌面应用统一品牌图标 | ✅ | Windows 安装包 / EXE 使用多尺寸 `packaging/icon.ico`，macOS `.app` 使用完整 iconset 生成的 `packaging/icon.icns`；系统托盘与 macOS 菜单栏从随包 Web 资源 `openbiliclaw/web/icon-192.png` 加载同一官方图标。Windows PyInstaller 启动页直接复用最新品牌源图，并以 560×280 深色渐变卡片展示启动状态、活动进度轨及从 `pyproject.toml` 读取的当前版本号；CJK 字体不可用时保持英文降级。桌面容器与托盘保留去白边后的透明外缘；浏览器标签使用独立 32px 满幅品牌粉 favicon 并以版本 URL 规避旧缓存，页面头图用品牌粉背景承接透明圆角。 |
| 对话结算单队列生命周期（Wave 1–3） | ✅ | 每个 API runtime generation 只安装一个 `dialogue_settlement_queue`、一个 exhaustive typed dispatcher 与一个 actual worker。11 个 kind 的生产入口已全部 cutover：queued dialogue learning/settles、卡片 confirm/reject/discuss/defer、pending-open/anchor、GET reconcile、probe/confusion reply、confusion open/replay 与 legacy façade 都只 submit；guard 已装到 protected mutator façade，request task 与 inherited-context child 均 fail closed。`card.reconcile` 只在 worker 内补 applied receipt 的 stable audit/projection/exact-generation publication，或把无活锚 orphan discussion 恢复为 pending；没有 restart scanner。队列 self-owned、非 durable、不进入 `BackgroundTaskRegistry`，`create_app()` 注入真实 queued dialogue时采用同一实例。卡片 action 只 shield 等待 1 秒：本地完成返回 200，队头阻塞返回 202 而 job 继续；进程重启丢失未执行 job 后由 action retry/GET reconcile 重新 admission。热重载先保持 old queue 可受理并 drain，最迟等待 25 分钟；队列真正空闲后才无 await 原子切到 paused，再 exact revoke old permit → start/register new → publish new → shutdown old，避免等待期间丢弃用户点击。构造/注册失败只在 permit 单槽为空时用 fresh nonce 恢复 old。CLI/OpenClaw 不属于该 runtime，显式使用 `legacy_direct`，不享受 queue/receipt/guard 保证。上线只观察 queue depth/oldest age、action wait、202 比例与 retry；连续 7 天 `202 >1%` 或 p95 `>5s` 才另开分析，不预埋第二队列。 |
| Durable reply binding admission（2026-08-01） | ✅ | 带 `reply_to_turn_id` 的 API 请求在首次 user row INSERT 前同步解析 completed card/question 与 queue logical snapshot；capture 与 insert 之间无 `await`，后续 A→B replacement 只能让 A 在学习时 stale-drop，不能改绑 B。`_complete_durable_chat_turn()` 从 row 恢复 binding，bound/ordinary/detached 分别保持 exact、普通和禁止 object settle 的语义；队列仍是进程内非 durable。 |
| Durable reply lane 与 app-stable dialogue lease | ✅ | `DialogueExecutionCoordinator` 由 FastAPI app 持有，不随 `RuntimeContext` 热重载替换；durable worker、惊喜 chat、legacy chat、兴趣/避雷探针共享 max-active=1 lease，并在 admission 后才解析当前 dialogue/speculator。`DurableChatReplyScheduler` 只有一个 worker，按 SQLite rowid 恢复/执行全部 pending，瞬态失败原位指数退避以阻止后序 overtaking；shutdown/cancel 不写 failed，重启继续。热重载先 pause admission + drain active，再发布新 context；超时自动 resume old 且 rebuild 零调用。`runtime-status` 的 `chat_reply_depth` 读取 durable pending count，另暴露 active/last_error/processed，不含消息内容。该 reply lane 与 `DialogueSettlementQueue` 正交：前者拥有模型回复/可见 CAS，后者拥有回复后的 typed learning/settlement。 |
| App-owned 配置应用队列 | ✅ | 设置保存先事务性落盘，`PUT /api/config` 统一返回 202，把修订交给 latest-wins 后台队列。正在应用的修订安全 drain，未开始的连续保存合并为最新配置；所有 runtime rebuild 共享同一 app-local reload lock。最终成功更新 last-good 后再广播 `config_reloaded`；最新修订失败恢复 last-good、还原 process proxy 与内存 runtime，并广播 `config_reload_failed`，旧失败遇到更新修订时不覆盖新文件。shutdown 取消内存 worker，但已落盘配置由下次启动接管。 |
| App-stable 封面抓取 lane | ✅ | `runtime.image_fetch.ImageFetchCoordinator` 由 FastAPI app 持有，`/api/image-proxy` 前台 miss 与 `ContinuousRefreshController` 预取共用 Condition priority gate：总 active≤4、background≤3，始终为前台保留一槽且 queued 前台优先。按归一化 `image_cache_key` singleflight；waiter 用 `shield`，取消单个请求不取消 owned upstream，前台加入 queued background 同 key 时会提升优先级并采用更新的签名 URL。cache glob/read/write 全在线程执行，命中不进 gate；写入为同目录 tmp+fsync+replace。热重载新 controller 重绑同一实例；shutdown 先停 producer，再取消 active/queued owned task。状态只暴露整数计数与峰值，不含 URL/token。 |
| 桌面安装包升级进程交接 | ✅ | Windows Inno Setup 在覆盖文件前通过 Restart Manager + `taskkill /T /F` 结束旧版进程树，安装成功后 `[Run]` 无条件从 `{app}\OpenBiliClaw.exe` 启动刚写入的新版本，交互与静默升级一致。macOS DMG 因系统模型没有安装完成回调，根目录提供显式 `安装并启动 Install OpenBiliClaw.command`：先用 `ditto` 完整暂存并校验 bundle version + code signature，再发起有界的优雅退出请求（连 `osascript` 等待本身也受窗口约束；超时 TERM/KILL，且只清理由旧 OpenBiliClaw.app 拉起的内置 Ollama）、同卷备份后原子替换 `/Applications/OpenBiliClaw.app`，二次校验失败或中断会恢复旧 app，成功后 `open -n` 精确启动安装路径并确认进程出现。助手不自动删除 quarantine；Gatekeeper 决策仍由 macOS 和用户完成。传统拖拽保持兼容，但需手动退出、替换、重开。macOS-only E2E 使用两个 ad-hoc 签名最小 app 和真实只读挂载 DMG，覆盖旧 PID 退出、版本/签名更新、新 PID 启动与暂存清理。 |
| 扩展原生保存共享 runtime | ✅（6/6 executor + 真实账号验证） | 扩展已定义与后端一致的 `native_save` task/result allow-list、canonical HTTPS URL 规则、`NATIVE_SAVE_EXECUTE` / `NATIVE_SAVE_RESULT` 消息契约和 active-tab runner，并统一经过共享 MV3 recovery barrier。一般 runner 与 legacy dispatcher 共用 `globalThis` mutex 保护 tab 创建/加载，加载完成即释放；XHS 手动 native-save 使用 exact tokenized route + identity/control fence，可在没有精确复用页时越过后台 discovery mutex，且 alarm/runtime wake poll single-flight。执行中的任务按 task/tab 独立分桶，仍各自在单一绝对 deadline 内严格校验 final tab 与 sender URL、tab/ID/item/platform。`chrome.storage.session` 可同时记录多个 runner-owned tab，MV3 recovery 只关闭这些 ID；content 用 256 项 bounded outcome-promise cache。YouTube duplicate exact playlist 优先 checked proof，否则稳定复用一个；知乎 typed `question/answer/article` identity 适配 current `Favlists-item`；小红书适配 current `noteContainer/collect-wrapper`。2026-07-14 六个平台 favorite 与 watch-later/fallback 真实终态均为 `synced/already_synced`。 |
| 扩展原生保存 broker 与 adapter 注册 | ✅（6/6 executor + 真实账号验证） | `RuntimeContext.extension_native_save_broker` 是热重载不替换的 test-injectable 稳定实例；local/degraded construction 与 config rebuild 都注册六平台 adapter，service/router 会替换而 broker 不变。wake best-effort 发布 `<slug>_task_available`。broker poll/lease、native task/item heartbeat 与 terminal persistence 使用线程卸载的独立短连接并有界重试 SQLite lock；durable terminal state 在 heartbeat completion race 中优先。开发用 `/api/extension/reload` 返回 `delivered`，可确认至少一个 runtime-stream 订阅者收到热重载事件。 |
| 统一补货请求入口 | ✅ | `ContinuousRefreshController.request_replenishment(reason, force=False)` 收束补货触发：普通事件和反馈只排队 reason；初始化完成、用户手动刷新或推荐刷新后低库存用 `force=True` 进入手动补货。 |
| 后台刷新控制 | ✅ | `ContinuousRefreshController` 按 scheduler 配置补充候选池，并通过 source policy 计算各平台有效配比；后台定时 refresh 使用约 90% 的可换池低水位，库存只是略低于 `pool_target_count` 时不跑 discovery。async refresh / force-refresh / post-refresh 的库存维护统一进入 database-owned 专属单线程 maintenance worker，并使用独立短生命周期连接；每个事务最多修改 50 行、每 tick 最多 8 批，批间提交释放写锁并 `await asyncio.sleep(0)`。后台连接只等写锁 75ms，碰到交互写入就延后，不继承进程共享连接的 30 秒等待。普通定时 tick 的 readiness fingerprint 未变化时跳过 ranked 重维护，但每 10 分钟仍强制安全巡检；手动 refresh 和 post-refresh 永远强制扫描。注入 `DiscoveryCandidatePipeline` 后，B 站主补货会在现有 `_refresh_lock` 内按 `pending_eval + evaluating` 水位循环生产 raw candidates，直到待评估供给接近目标 batch 或达到预算；小缺口阶段先给 `search + related_chain` 配额，延后 `trending/explore`。统一关键词 planner 开启但 B 站关键词 store 暂空时，本轮只剔除 `search` 子策略，保留其它 B 站策略，避免回落到旧 `discovery.search.queries` LLM 生成。v0.3.149+ 当 `explore_refresh_minutes` 到期或距到期不足一个 refresh tick，且 B 站平台族仍有补货空间时，controller 会允许 `KeywordPlanner` 在同一轮 merged keyword LLM 调用里请求 `explore_domains`，成功写入 B 站 `keyword_kind="explore"` query cache 后同步推进 `last_explore_refresh_at`；后续 `ExploreStrategy` 从该 explore 池 claim query。空计划全量诊断按 `pool_available` 指纹节流：首次、指纹变化或距上次满 300 秒输出 INFO 并执行 source/readiness 重统计，其余调用只输出带累计抑制数的单行 DEBUG；300 秒与补货冷却封顶同数量级，保证每个枯竭事件至少有一条完整诊断。**份额公平（v0.3.181+，spec 2026-07-20）**：`_source_requested_count` 的生产缺口按各源自身份额口径计算——全局池满不再把欠份额来源的缺口清零（旧 `min(自身缺口, 全局缺口)` 会让 reddit 等超份额来源顶满全局池后饿死 bangumi），缺口只受各源 raw ceiling 钳制；入池经 `DiscoveryCandidatePipeline` 两轮录取（欠份额行先入、超份额行仅作可用性兜底，见 discovery.md）；drain tick 在 admission 前调用 `_rebalance_pool_shares()`，当全局池满且欠份额来源有 `evaluated` 供给等待时，从最超份额来源退坑每 tick ≤3 行（最低分最老、置 `pool_status='stale'`），空闲 tick 无退坑；每源 `available/target/deficit` 摘要仅在变化时打一条 INFO。退坑候选集含**不在份额表里的来源族**（用户禁用某来源后其存量行，按 target=0 计为全额超额，`available_by_source` 各 key 先经 `source_family` 归族后再算超额），否则这些「无主占坑者」会永久挤占其他来源份额（D8）。退坑触发所看的「欠份额来源等待供给」（`_waiting_supply_by_family` → `count_admission_waiting_discovery_candidates_by_source`）计入 `pending_eval`+`evaluating`+`evaluated` 全部非终态,而非仅 `evaluated`——否则占坑者钉满池、coordinator 空转不评估、欠份额来源永远到不了 `evaluated`,退坑永不触发(D9 第三层鸡生蛋);demote 后即使该行评估失败,第二轮兜底 admission 也会补回坑,全局 ≤target 不破。评估 claim(`claim_discovery_candidates_for_eval`)也份额感知:pipeline `claim_batch` 传 `_under_share_platforms()`,欠份额在册来源的 pending 行优先 claim(窗口 `CASE` 前置 + 两层 round-robin),避免把评估算力烧在 admission 注定不收的超份额积压上。**Producer 内部闸份额感知（Phase 5，E2E 发现的死结）**：`bilibili/douyin/youtube/zhihu/bangumi/reddit` 六个 producer 的 `_candidate_pool_full()` 原本调全局 `pool_full()`——全局满时欠份额来源永不生产 → 无 `evaluated` 供给 → rebalance 永不触发 → 池永远满（`reason=pool_full` 冤枉 bangumi）。现统一经 `runtime/pool_gate.candidate_pool_full_for_source(pipeline, family, …)` 调 pipeline 新增的 `pool_full_for_source(family)`：欠份额来源即使全局满也返回 False（放行生产），已达/超份额或旧 pipeline 无该方法时保守回退全局 `pool_full()`。`xhs`/`x` 为 fetch-only 无此内部闸。**再平衡/摘要挂载点（D7）**：`_rebalance_pool_shares()` 与 `_log_source_deficit_summary()` 原挂在 `_drain_discovery_candidates_and_precompute`（由 `_loop_candidate_eval` 驱动），但生产装配用 `CandidateEvalCoordinator.run_forever()` 替换了该 loop，导致其成为死代码。现两者收敛到 controller 的 `run_pool_share_maintenance()` 单入口：legacy drain 调它一次，`CandidateEvalCoordinator` 经新增 `pre_admit_hook`（`run_forever` 每 tick 在 `_admit_evaluated` 前调用、`runtime_context` 以 `getattr` 守卫注入 `controller.run_pool_share_maintenance`）调它一次。两装配互斥（`run_forever()` XOR `_loop_candidate_eval()`；manual/inline drain 在 coordinator 存在时提前 notify 返回），故单轮至多一次退坑。 |
| 扩展在线账号信号周期回拉 | ✅ | `runtime.source_incremental_sync.SourceIncrementalSync.tick()` 是 `ContinuousRefreshController` 内独立的 60 秒决策 loop。`scheduler.source_incremental_enabled` 默认 `false`，关闭时在 presence 前返回，因而不会自动创建任务或触碰浏览器；旧配置缺字段也按关闭处理。显式开启后才要求 `scheduler.enabled`、完整画像、guided init 非活跃和 `PresenceTracker.is_present()`，再按持久 round-robin 在 XHS / 抖音 / YouTube / 知乎 / Reddit / Linux.do 复用现有 bootstrap scope；全局周期默认 24 小时，抖音逐源仍默认 `0`。六表 `pending/in_progress` 是全局 active 权威，进程内 decision lock 覆盖热重载旧 `to_thread`，SQLite `BEGIN IMMEDIATE` admission transaction 则把六表扫描与 insert 原子串行到跨 facade / 进程。崩溃收养按任务 `created_at` 补齐 attempt/cursor；只有新建且非空 task id 才写 `last_attempt_at/cursor/active_task` 并经当前 `EventHub` 唤醒扩展。预算耗尽会在同 tick 安全尝试下一到期源，异常/复用不推进时间；只有 evidence-backed `ok/empty` 完整终态写 `last_success_at` 并推进 cadence，failed/degraded 清理 active 后立即可重试。guided init 成功会同时种下首次 attempt/success，避免初始化后立刻重复拉取；失败不种。手动 init / fetch 和 discovery 不受该开关影响。已停用来源的 `pending/in_progress` 任务不会占用全局 active 槽，避免卡住其它来源；scheduler-owned 增量任务会在对应 `/next-task` 领取前被清理，手动任务保持 `pending` 等待重新启用。 |
| 跨 digest 关键词库存整理 | ✅ | `KeywordPlanner.run_once()` 在计算平台缺口前，只把由当前库存饱和度产生的平台 `avoid_topics` 交给 storage 原子整理 `regular/pending`；普通 user dislike 不参与关键词撤销。默认 24 小时内的安全旧 digest 行可继续计入水位和被 producer claim，原 digest/生成溯源不改；过龄、供给饱和、重复和超过动态高水位的行过期。整理或 all-digest count 能力缺失、抛错或返回畸形账本时，该平台立即退回旧的硬过期 + exact-digest count，不能因部分升级错误压住生成。`keyword_digest_grace_hours=0` 是独立回滚。 |
| 启动优先的有界库存维护 | ✅ | `run_startup_maintenance()` 是每个 controller 幂等的 host 启动钩子：API daemon 的 `run_forever()` 与 OpenClaw direct bootstrap 都先调用它，才允许暴露 LLM operation 或启动后台 loop。启动前同步路径也按最多 50 行一批循环、单 tick 最多 8 批；更大积压由后续 tick 继续收敛。每批先零 LLM 恢复历史 `suppressed` 结果，再统一其它维护；storage 只保留能净增 canonical available 的恢复，故 `has_more` 不会因同一批恢复/裁剪状态振荡而永久为真。 |
| Canonical 库存驱动的补货 admission | ✅ | `ContinuousRefreshController._pool_readiness_counts()`、原子维护结果、文案完成后的池状态和 API/OpenClaw candidate snapshot 都把 durable available 同步到共享 gate。低库存且 refill 排队时新 admission 保证两个 refill 后台槽并可借满三个；无 refill 时 maintenance 借用空槽；库存为零只 park 新 maintenance，不抢占已在 provider 中的请求。 |
| B 站扩展搜索兜底 producer | ✅ | `BilibiliExtensionSearchProducer` 在 B 站平台族低于 quota、`BilibiliAPIClient.search_cooldown_remaining()>0`、扩展 presence 在线且候选池未满时入队 `bili_tasks(type="search")`；扩展回传后仍进入 `DiscoveryCandidatePipeline` 统一评估。兜底关键词生成 prompt 已携带结构化画像，调用 `LLMService` 时会在支持路径上关闭额外 core memory 注入，且 `runtime.bilibili_extension_search.*` 未指定 effort 时默认关闭 thinking；统一关键词 planner 会把画像按 core / life / interests / style / recent 分层渲染，保护 prompt-cache 前缀。 |
| 扩展在线账号信号周期回拉 | ✅ | `runtime.source_incremental_sync.SourceIncrementalSync.tick()` 是 `ContinuousRefreshController` 内独立的 60 秒决策 loop。`scheduler.source_incremental_enabled` 默认 `false`，关闭时在 presence 前返回，因而不会自动创建任务或触碰浏览器；旧配置缺字段也按关闭处理。显式开启后才要求 `scheduler.enabled`、完整画像、guided init 非活跃和 `PresenceTracker.is_present()`，再按持久 round-robin 在 XHS / 抖音 / YouTube / 知乎 / Reddit / V2EX 复用现有 bootstrap scope；全局周期默认 24 小时，抖音逐源仍默认 `0`。六表 `pending/in_progress` 是全局 active 权威，进程内 decision lock 覆盖热重载旧 `to_thread`，SQLite `BEGIN IMMEDIATE` admission transaction 则把六表扫描与 insert 原子串行到跨 facade / 进程。崩溃收养按任务 `created_at` 补齐 attempt/cursor；只有新建且非空 task id 才写 `last_attempt_at/cursor/active_task` 并经当前 `EventHub` 唤醒扩展。预算耗尽会在同 tick 安全尝试下一到期源，异常/复用不推进时间。初始化成功会种下首次 attempt，失败不种；手动 init / fetch 和 discovery 不受该开关影响。已停用来源的 `pending/in_progress` 任务不会占用全局 active 槽，避免卡住其它来源；scheduler-owned 增量任务会在对应 `/next-task` 领取前被清理，手动任务保持 `pending` 等待重新启用。 |
| 跨 digest 关键词库存整理 | ✅ | `KeywordPlanner.run_once()` 在计算平台缺口前，只把由当前库存饱和度产生的平台 `avoid_topics` 交给 storage 原子整理 `regular/pending`；普通 user dislike 不参与关键词撤销。默认 24 小时内的安全旧 digest 行可继续计入水位和被 producer claim，原 digest/生成溯源不改；过龄、供给饱和、重复和超过动态高水位的行过期。整理或 all-digest count 能力缺失、抛错或返回畸形账本时，该平台立即退回旧的硬过期 + exact-digest count，不能因部分升级错误压住生成。`keyword_digest_grace_hours=0` 是独立回滚。 |
| 启动优先的有界库存维护 | ✅ | `run_startup_maintenance()` 是每个 controller 幂等的 host 启动钩子：API daemon 的 `run_forever()` 与 OpenClaw direct bootstrap 都先调用它，才允许暴露 LLM operation 或启动后台 loop。启动前同步路径也按最多 50 行一批循环、单 tick 最多 8 批；更大积压由后续 tick 继续收敛。每批先零 LLM 恢复历史 `suppressed` 结果，再统一其它维护；storage 只保留能净增 canonical available 的恢复，故 `has_more` 不会因同一批恢复/裁剪状态振荡而永久为真。 |
| Canonical 库存驱动的补货 admission | ✅ | `ContinuousRefreshController._pool_readiness_counts()`、原子维护结果、文案完成后的池状态和 API/OpenClaw candidate snapshot 都把 durable available 同步到共享 gate。低库存且 refill 排队时新 admission 保证两个 refill 后台槽并可借满三个；无 refill 时 maintenance 借用空槽；库存为零只 park 新 maintenance，不抢占已在 provider 中的请求。 |
| 连续候选评估协调器 | ✅ | API daemon 的 `CandidateEvalCoordinator` 是 live runtime 内唯一 claim owner：配置与构造器均硬限制为最多 3 个、每批最多 30 条，即最多 90 条 raw 在途；任一 worker 完成即补位。主协调任务串行持久化 token-owned 评分与整组 temporal v2 证据，再按 copy-aware headroom admission。B 站 refresh、抖音、YouTube、知乎、X、Reddit 和 Bangumi 都通过共享 `DiscoveryCandidatePipeline` enqueue；其单次 `on_candidates_enqueued` 回调立即唤醒协调器，managed refresh / producer 绝不再同步 `drain_pending()` 另领 claim。OpenClaw direct adapter 不启动 daemon loop，因此不挂接 dormant candidate / expression coordinator；其 `recommend(refresh_if_needed=True)` 的 controller refresh 专用首轮 source/evaluation wave 固定为 4（fetch oversample=1、min eval batch=4、inline evaluator=1），随后请求再补下一批。其 `on_candidates_admitted` callback 在 admission 的 DB commit 后 await `expression-copy(limit=4, max_extra_requests=0)`：首 batch 的有效 subset 先成为 canonical available，剩余行 durable pending 给下一请求，避免在 45 秒交互窗口内递归拆分。pipeline 把 callback ownership 作为 receipt 随 drain result 返回，controller 只有没有 owner 时才做 refresh 收尾 copy，故同一 admission 不会重复回调。独立/CLI 与 API daemon 保留原兼容路径（包括默认 split retry 与 API 的 60 条 copy drain）。projected 等于 `available + admitted_pending_available + evaluated_pending_admission`：admitted 只计当前 topic 展示窗口有空位、补齐文案后能净增公开库存的行，evaluated 只计 disposition 为 `eligible` 的子集；raw `evaluated_waiting_total` 另行触发 no-headroom 生命周期清扫，全部 pending-copy 仍保留给表达水位诊断；raw pending/evaluating 不计入。这样同 topic 深层 backlog 不会把池子虚报为已满，coordinator 会继续寻找新 topic；超出 headroom 的达标结果保留为 durable `evaluated`。有效 worker 为 `min(candidate_eval_concurrency, max(1, llm.concurrency-1))`，60 秒 safety wake 同时复检等待行：`review_due` 回到 `pending_eval`，`expired` 才终态拒绝。raw 断供时 quota-aware `supply_candidates_once()` 会即时 tick 全部欠份额 producer 并再跑 B 站 refresh；同平台周期 / 即时 tick 由 per-source lock 去重。确认无产出的补货按 30/60/120/300/600 秒冷却并进入 `supply_cooldown`；30 秒约为空转实测 0.3–0.5 秒的 60–100 倍，600 秒封顶保证源恢复后十分钟内自愈。生产性优先读取 `supply_productive` / 真实新增计数，`refreshed=True` 只作旧 callback fallback；全部 duplicate 不会清零退避。任意 `notify()` 可穿透当前窗口一次，`startup`、`manual_*`、`config_*` 和真实 `candidate_enqueued:*` 重置供给阶梯；候选入队不会误解除 provider pause。首次登顶每个枯竭事件只记一条 WARNING。 |
| temporal v2 复审生命周期 | ✅ | `breaking/current/versioned` 的 1 / 14 / 120 天只生成 code-owned `next_review_at`，不是死亡线；旧 v1 3 / 60 天同样只触发 `review_due`。候选队列复用现有 evaluator 重新 claim，正式池的 `temporal_review_hold` 也由既有 backlog drain 复审；DB 对两类失败复审均使用逐行 1 / 2 / 4 / 8 / 16 / 24 小时有界 not-before 租约，候选租约未到时不 claim、也不计 raw/projected/source cap，避免每分钟轮询放大 provider 故障或饿死后续行。评估提交后 admission 重读 durable row，仅最终状态仍为 `evaluated` 才能入池。hold 不展示、不计 canonical 库存，完整新证据可恢复 `fresh` 并清零租约。明确 grounded deadline 或 terminal state 才进入 `expired`，最终 serve 与 API 快照再次复核。 |
| B 站扩展搜索兜底 producer | ✅ | `BilibiliExtensionSearchProducer` 在 B 站平台族低于 quota、`BilibiliAPIClient.search_cooldown_remaining()>0`、扩展 presence 在线且候选池未满时入队 `bili_tasks(type="search")`；扩展回传后仍进入 `DiscoveryCandidatePipeline` 统一评估。每轮第 1 个任务使用有界 `order="pubdate" / discovery_lane="recent" / page_size=5`，其余任务保持普通相关性排序；同一个关键词只建一个任务，不复制 keyword lifecycle。兜底关键词生成 prompt 已携带结构化画像，调用 `LLMService` 时会在支持路径上关闭额外 core memory 注入，且 `runtime.bilibili_extension_search.*` 未指定 effort 时默认关闭 thinking；统一关键词 planner 会把画像按 core / life / interests / style / recent 分层渲染，保护 prompt-cache 前缀。 |
| 候选池文案预计算状态同步 | ✅ | 独立 `_loop_pool_precompute()` 将 fresh 候选补齐 `pool_expression` / `pool_topic_label` 后，会同步更新 `last_replenished_count` 并推送 `refresh.pool_updated`；推荐文案 batch 默认 30 条、2 个 worker 并发生成，但仍受 `_expression_lock` 串行化多入口，避免重复消费同一批候选。推荐消费通过专属 serve DB worker 的独立短事务把推荐历史与 `pool_status='shown'` 原子提交；提交后 callback 直接携带 `pool_counts_after` 更新 gate 和事件订阅者，不再在响应关键路径同步重读共享连接。多次热重载后旧 engine 的迟到 callback 仍解析当前 controller/target；写或 subscriber 失败不伪报提交成功。 |
| Copy-ready 高水位补货 | ✅ | API runtime 的 `ExpressionCopyCoordinator.pending_count_provider` 读取推荐引擎的持久化双缺口：正数 `copy_ready_target_count` 补 `max(copy_ready 水位缺口, min(pool available 缺口, admitted_pending_available))`，并优先领取能进入每 topic 三条展示窗口的 pending；公开目标达到后仍只维持 copy 水位，不排空同 topic 深层 backlog。`0` 保留 legacy drain-all。serve 消费或暂无 ready 卡、feedback、delight 消费与 maintenance mutation 只同步发送唤醒，provider 工作继续由后台 coordinator 执行；引擎在 `_expression_lock` 内再读缺口，避免并发通知过量回填。 |
| 候选池真实可换计数 | ✅ | `pool_available_count` 现在只表示后端当前可立即 `serve()` 的候选，并按默认每 `topic_group` 最多 3 条的候选窗口计数；runtime status / runtime stream 另带 `pool_raw_count`、`pool_pending_count`、`pool_pending_eval_count`、`pool_evaluated_pending_count` 区分素材库存、待评估和已评估待入池内容。换批先广播 `ServeResult.pool_counts_after`，精确 canonical 状态再由独立 serve worker 后台读取并收敛；runtime status 查询和 pool-status 发布同样不触碰维护连接，成熟库扫描不会同步阻塞事件循环。维护 INFO 日志只保留批次汇总，阶段明细与候选摘要下沉到 DEBUG，避免大对象格式化和终端日志锁放大尾延迟。 |
| embedding 后台预热 | ✅ | refresh 完成前只保证候选入池与文案可用；`prewarm_supergroup_embeddings()` / `prewarm_pool_mmr_embeddings()` 作为后台 task 运行，慢本地 embedding 后端不会占住 refresh lock 或让界面长时间停在“正在补货”。v0.3.124+（lever 4）：`prewarm_pool_mmr_embeddings()` 返回值区分良性冷启动与真故障——`-1`（无 embedding service / 空池，没东西可暖）让启动重试包装器 `_safe_prewarm_pool_mmr_embeddings` 平静跳过(不再每次装机刷 5 行 `warmed=0 — retry`)，`0`（有候选但全嵌入失败＝后端不可达）才重试到底并在放弃时打 WARNING 点名 embedding 后端不可达、MMR 降级。v0.3.148+ search / trending / explore / `KeywordPlanner` 的 query profile summary 也只通过 `EmbeddingService.lookup_cached()` 读取已缓存向量来保持 interest / dislike 多样性；缺缓存时按权重顺序降级，绝不在查询生成热路径新发 embedding 请求。 |
| 视觉 / 弹幕 prewarm wiring | ✅ | `RecommendationEngine`、CLI、RuntimeContext 和 OpenClaw 均透传 `keyframe_fetch_limit` / `danmaku_fetch_limit`；prewarm 只领取当前可服务池，结果区分成功空与瞬时失败，provenance 或 sampling 变化自动重建/重嵌入 |
| YouTube 后台 discovery producer | ✅ | `YoutubeDiscoveryProducer` 独立运行 `yt_search` / `yt_trending` / `yt_channel`，只在 YouTube 平台族低于 quota 时由 `_loop_youtube_producer()` tick，按每日 ledger 和 `min_interval_minutes` 控制执行。 |
| X 后台 discovery producer | ✅ | `XDiscoveryProducer.produce_if_due()` 在 X 平台族低于 quota 且源健康就绪时，由独立 loop tick 触发 `search` / `feed`（For-You）/ `creator`（账号订阅）三个策略；按 `daily_*_budget` / `min_interval_minutes` / `request_interval_seconds` 节流，For-You 压到很低的每日频次并在连续失败后自动暂停。只 enqueue raw candidates 进 `discovery_candidates`，不写 `content_cache`、不调评估器。`enabled=false` 时是 no-op，不 import `twitter_cli`。 |
| Reddit 后台 discovery producer | ✅ | `RedditDiscoveryProducer.produce_if_due()` 在 Reddit 平台族低于 quota 且 `[sources.reddit].enabled=true` 时，默认通过随 OpenBiliClaw 安装的 `rdt-cli` 登录态命令后端触发 `search` / `hot` / `subreddit` / `related` 四个分支；已连接插件会同步 `reddit_session` 到 rdt-cli credential store，命令后端不可用或未登录时 fallback 到已安装浏览器插件的真实 `reddit.com` 登录态任务。四个分支各自有独立 daily budget，默认每类 300。producer 只 enqueue raw candidates 到 `discovery_candidates`，不写 `content_cache`、不同步跑 LLM 评估，正式 admission 由共享 evaluator 异步完成。 |
| Bangumi 后台 discovery producer | ✅ | `BangumiDiscoveryProducer.produce_if_due()` 在 Bangumi 平台族低于 quota 且启用时，调用官方匿名 API 执行 `search / ranked / latest`。它共享关键词 claim/use/fail 生命周期，按 UTC 日条目预算、类型 cursor、最小间隔与持久化 `429 Retry-After` cooldown 调度；429 rollback 在途/未执行关键词。browse 的非零旧 cursor 若因 total 缩小触发 `invalid_request`，先持久化归零再有界重试一次。跨 mode 去重并应用最终 limit 后才按保留候选的 strategy 扣预算，重复/截断条目不占额度。只 enqueue raw candidates。`RuntimeContext` 在 generation 构建/热重载时拥有并关闭 `BangumiClient`，GET 状态页通过独立本地查询读取 cooldown/run ledger，不构造 producer。显式 CLI discover 只服从来源开关，不服从 daemon scheduler 总开关。 |
| Linux.do 后台 discovery producer | ✅（真实 Chrome E2E） | `LinuxdoDiscoveryProducer.produce_if_due()` 在 Linux.do 平台族低于 quota 且启用时，按 source_modes 入队 search / hot / feed / creator / related 任务。真实站点访问只发生在扩展的 `linux.do` 同源标签页，全部为 JSON GET；候选只 enqueue raw rows。搜索复用统一 keyword claim，结构化 rate-limit/network/timeout/access/login 失败会回滚 claim；creator/related 从近期和同轮结果取种子。2026-08-09 已完成安装版热更新、五路任务与正式候选管线 E2E。 |
| V2EX 后台 discovery producer | ✅ | `V2EXDiscoveryProducer.produce_if_due()` 在 V2EX 平台族低于 quota 且启用时，按 `search / node / tab / hot / latest` 调用匿名 API / Feed；共享关键词 claim、Node/Tab 配置、分支日预算、最小间隔和持久化 rate-limit cooldown。PAT 存在时由 `V2EXClient` 使用 API 2.0，401/403 自动降级匿名。Topic 经 `v2ex_topic_to_content()` 转为文字卡后只 enqueue raw candidates，LLM 评估由共享 coordinator 执行；Node producer 未配置 allowlist 时可使用 `v2ex_node_affinity` 排序。 |
| X 源健康状态机 | ✅ | `storage/x_health.py` 的 `XSourceHealthStore` 持久化 `ok` / `missing_cookie` / `expired_cookie`(401) / `blocked`(403) / `rate_limited`(429) 五态；按 code 分别退避，429 带 `cooldown_until` 自愈，401/403/missing 须等用户重新登录 x.com 才恢复；连续 For-You 失败触发 `feed_allowed()=false` 自动暂停。状态经 `GET /api/sources/x/status` 暴露到插件设置页。 |
| 运行时频率配置 | ✅ | `refresh_check_interval_seconds`、行为触发阈值、trending / explore 间隔、单轮发现上限、惊喜队列加载数量、主动推送间隔和 speculator idle tick 都从 `[scheduler]` 读取，配置热重载后重建 runtime 生效。 |
| 惊喜永久消费 | ✅ | `mark_delight_sent()` 仍只表示通知已送达；新增 `mark_delight_seen()` 供用户主动叉掉时委托 storage 写 canonical `seen_items` 并置 `delight_notified`，随后更新主动惊喜冷却。两种动作分开，避免“通知出现过”被误当成“用户看过”。 |
| Durable 对话失败原子性 | ✅ | `/api/chat/turns` 只把显式无效/空回复持久化为 `status="failed", reply="", error=<安全分类文案>`；provider、限流、配置、超时、service 瞬态失败与 shutdown cancellation 都保持 `pending` 并在队头原位有界退避。真实回复以 `WHERE status='pending'` completion CAS 发布；重复 completion、迟到 failure 与重启重放不能覆盖首个可见终态。回复完成后的 11-kind learning/settlement 由独立 `DialogueSettlementQueue` 处理，不计入 SQLite reply backlog。 |
| Durable event consumer 调度 | ✅ | app-owned `EventProcessingScheduler` 同时调度 generic 与 content-feedback owner；`FeedbackBatchScheduler` 仅是兼容 import/injection alias。所有 event ingress 只 commit+wake，5 秒 debounce 合并 burst；单 owner pass 先运行 `SoulEngine.process_profile_events_if_needed()`，再运行 `process_feedback_batch_if_needed()`。两者都通过 `checkpointed_enqueue_batch()` 把 buffer+cursor 原子发布到同一 `pipeline_state.json`，随后调用 `tick_if_buffered()`；只有独立周期画像维护调用 `tick()`。5 秒 periodic scan 与 startup recover 覆盖 commit 后丢 wake；处理中又有新 event 会补跑下一轮。热重载 pause+drain 旧 owner，重绑 resolver 后先 recover 再恢复周期扫描。`event_lane_depth` 只是 dirty wake 的 `0/1`，不是 SQLite backlog。 |
| 浏览器 presence / 初始化 gate | ✅ | `background_llm_work_allowed()` 结合 `scheduler.enabled` 与 `pause_on_extension_disconnect` 控制 daemon-owned 后台 LLM / embedding 工作；首个完整画像尚未落盘或 guided init 活跃时也一律返回 false，防止 account-sync 在用户点击初始化前先导入、分析并重复写入同一批 bootstrap 历史。guided init 自身绕过该 gate。 |
| Runtime event stream | ✅ | `/api/runtime-stream` 向扩展推送状态、Cookie sync 请求、配置重载、候选池快照和 presence 事件；background 连接时会请求小红书 / 知乎立即回传一次本地 Cookie 存在性的布尔心跳，X 启用时还会请求当前完整 Cookie，均不打开或请求平台页面。`RuntimeEventHub.publish()` 会返回是否至少有一个订阅者接收，供一次性事件判断是否真正投递。 |
| WebSocket 运行时依赖 | ✅ | 默认安装显式携带 `websockets>=13`，PyInstaller spec 显式收集 `uvicorn.protocols.websockets.websockets_impl` 与 `websockets`，避免源码 / Docker / 桌面包只安装裸 `uvicorn` 时 `/api/runtime-stream` 缺协议实现。 |
| Activity feed 状态摘要 | ✅ | `/api/activity-feed` 聚合认知更新、反馈、推荐池补货和 live summary；未初始化且还没有推荐 / 可换池 / 补货产物时，普通 `/api/events` 不会新写入 pending signals，旧的 `pending_signal_events` 也不会抢占初始化提示。初始化后 pending 文案统一为“已记下 N 个新动作，下一轮补货会拿来参考”，表示 discovery refresh 水位，不表示画像待处理队列。 |
| 桌面 Web 推荐卡链接与元信息 | ✅ | `/web` 推荐卡、稍后再看 / 收藏卡、消息抽屉内容和惊喜推荐封面都使用真实 `<a href target="_blank" rel="noopener noreferrer">`；点击上报同时绑定 `click` 与中键 `auxclick`，但不阻止浏览器原生中键 / Ctrl 或 Cmd 点击 / 右键菜单行为。`RecommendationOut` 增量暴露 `duration`、`view_count`、`like_count`、`comment_count`、`share_count`、`danmaku_count`、`up_mid`、发布时间与目录指标 `rating_score / rating_count / source_rank`；桌面卡片展示视频时长、真实互动和 Bangumi 评分 / 评分人数 / 排名，字段为 0 或缺失时整段隐藏，不在无数据卡片上显示空元信息。微博只有真实 `reads_count` 才展示 view，转发数走 share，favorite/danmaku 缺失不占位；URL 缺失时回退 `https://m.weibo.cn/detail/<id>`。Bangumi 缺 URL 时回退 `https://bgm.tv/subject/<id>`；B 站且 `up_mid>0` 时 UP 主名跳到 `space.bilibili.com`，其它平台保持纯文本。 |
| 桌面 Web 首屏渐进水合 | ✅ | 首屏以 `/api/ping` 判断连接，推荐与 runtime 状态各自返回即各自渲染；health / init / profile / activity / config 等次级读取不会挡住推荐卡。推荐消费后仍独立复读 runtime 库存，失败沿用 1/2/4/8 秒的资源级恢复。 |
| 三端待聊确认角标同步 | ✅ | 插件 popup、桌面 Web 与移动 Web 都在首屏主动读取 `chat/pending-confirmations`，并在后端恢复、runtime stream 重连或收到事件时去抖刷新；桌面端在推荐卡 saved-status 请求扇出前先发角标请求，移动端底部对话 Tab 显示与 PC/插件一致的数字角标及无障碍计数。浏览器工具栏 badge 仍只表达后端健康状态，不混入待聊数量。 |
| 桌面标签页后台节流 | ✅ | 桌面 Web 以 `startDesktopBackendSession()` 作为 hydrate + runtime WebSocket 的可见性边界：隐藏状态启动零业务请求，进入后台即关闭 stream 并清理推荐/runtime/库存/activity/init 恢复定时器；恢复前台时按 15 秒 snapshot freshness 单飞 hydrate，再只建一个 CONNECTING/OPEN socket。后端 `/api/recommendations` 另用 1 秒 cache + `asyncio.Lock` 合并旧版/已加载标签页同时恢复造成的昂贵读取，并对 per-card saved status 做同窗口短缓存；reshuffle/append/feedback/save/remove 均失效对应缓存。 |
| 桌面 Web 封面请求优先级 | ✅ | 桌面推荐仅前 4 张封面使用 `eager/high`，后续封面使用 `lazy/low`；Delight 保持 `eager/high`。 |
| 桌面 Web 动效与布局稳定 | ✅ | 根滚动启用 `scrollbar-gutter: stable`，避免内容变长时顶栏横向抖动；消息 / 活动 / 手机二维码抽屉关闭进入 `.is-closing` 退出动画，快速开关会取消未完成 close；六个主分区切换使用短 `page-enter` 淡入。新增动效统一受 `prefers-reduced-motion: reduce` 保护。 |
| 桌面 Web 暗色模式 | ✅ | `/web` 支持 `auto` / `light` / `dark` 三态主题，顶栏按钮和设置页分段控件共享 `obc.theme` 本地键；`auto` 不写 `data-theme`，交给 `prefers-color-scheme`，手动浅色 / 深色写入 `:root[data-theme=...]`。暗色实现只覆盖 CSS token（暖暗背景、前景、边框、语义色、overlay、shadow），不为单个组件分叉硬编码颜色；`<meta name="color-scheme" content="light dark">` 让原生控件和滚动条跟随主题。 |
| 推荐/惊喜发布时间出口 | ✅ | `RecommendationOut`、`PendingDelightOut`、推荐列表/换批、pending delight 单条/批量、手动 delight 与 proactive runtime 事件均增量返回 `published_at` / `published_label`（默认空字符串）。API 不把发现时间或推荐生成时间改写为发布时间；桌面 Web 与移动 Web 精确时间优先、相对标签兜底、缺失隐藏，精确时间按本地时区显示并提供完整时间 tooltip。 |
| 桌面 Web 推荐列表增量渲染 | ✅ | `renderVideos` 不再整表 `grid.replaceChildren(...)`，改由 `syncRecommendationCards` 按 recommendation key 做增量对账：markup 未变且节点仍在网格上就原样复用 DOM，只对差集增删并用 `insertBefore` 就地调整顺序，骨架占位始终留在真实卡片之后。整表重建会让浏览器丢掉滚动锚点（列表跳动）、首屏之外的懒加载封面回落成占位、展开的推荐理由与收藏 / 稍后再看的 `aria-pressed` 复位，因此这条渲染路径必须对「列表没变」保持幂等。配套：`refreshInitStatus` 的重绘统一经 `initStatusRenderOptions()` 判定，网格已装真实卡片且不需要退回引导门时传 `renderAll({ preserveVideos: true })`——补货一轮会连发多次 `refresh.pool_updated`，而该事件经 init-status 支路会拽起重绘。 |
| 桌面 Web 再水合不换列表 | ✅ | `hydrateFromBackend({ replaceRecommendations })` 默认 `false`：`/api/recommendations` 只返回最新 top 窗口，后台再水合（切回标签页、`config_reloaded`、保存配置、初始化完成）整表覆盖会把用户滚动加载出来的卡片丢掉并按后端最新排序重排。只有首屏引导（`startDesktopBackendSession({ forceHydrate: true })`）与手动刷新会传 `true`；`replace=false` 时 `applyDesktopRecommendationSnapshot` 仅在列表为空时装填。 |
| 桌面 Web 滚动自动加载 | ✅ | 首页推荐列表底部 sentinel 通过 `IntersectionObserver` 触发 `/api/recommendations/append`，默认开启并保留手动“继续追加”按钮。触发判定收敛在 `autoLoadBlockReason`（`shouldAutoLoadMore` 为其布尔包装）：需满足单飞、首页可见、列表有非骨架卡、加载按钮可见，并且当前 Tab 有可推库存——“全部”读取 `platform-availability.total_available`，具体平台读取同一 snapshot 的 `by_platform[slug]`；首次快照仍未知时才回退到兼容的全局 `pool_available_count`。最后才校验距上次自动加载至少 8 秒。当前平台见底时自动续页暂停但手动按钮仍可用，避免全局尚有其它平台库存时在零库存 Tab 上反复空请求。**冷却自愈（issue #115）**：当唯一拦路项是冷却且哨兵仍在视口（用户停在底部、无更多滚动/相交事件）时，`armAutoLoadCooldownRecheck` 安排一次冷却到点后的一次性复查，避免停在底部时明明有货却卡到手动滚动/点按钮才继续；页面被新内容撑高、哨兵移出视口后自然停下。设置页开关写入 `openbiliclaw.webui.autoLoadOnScroll`，关闭或重建 observer 时断开并 `clearAutoLoadCooldownRecheck`。 |
| 桌面 Web 画像编辑即时反馈 | ✅ | 画像编辑的 chip 删除和添加按钮在请求 `/api/profile/edit` 前立即禁用并标记 `.is-pending`，让慢后端下也有置灰反馈；`state.profileEditState` 仍只接受服务端响应，成功 / 失败都通过重新渲染清除 pending 或恢复 chip，避免为低频编辑面引入复杂乐观回滚。 |
| 桌面 Web 可撤销即时反馈 | ✅ | 普通推荐卡和正向/避雷探针的非聊天动作先更新本地 UI，再由共享 pending-action coordinator 保留 10 秒提交屏障；点击撤销会取消定时器且不发 API 写请求，提交失败恢复原状态，`pagehide` 会以 keepalive 立即结清未提交动作。探针聊天和推荐评论需要服务端回复或文本语义，保持直接提交，不伪装成可撤销动作。 |
| 桌面 Web 探针反馈文案 | ✅ | 消息抽屉与画像页的正向/避雷探针共用一个 domain-aware feedback helper；inline 结果与 toast 使用同一条文本，明确显示经折叠空白且最长 24 字符的探针主题（超长以省略号收束），并通过 `textContent` / `showToast(text)` 写入，避免把主题插入 HTML。 |
| 桌面 Web 对话等待反馈 | ✅ | 「聊聊口味」发送消息后立即显示「阿B 正在思考，等待模型回复…」状态；服务端创建 durable `pending/processing` turn 并触发历史刷新后，该状态继续由真实 turn 生命周期渲染，不会被刷新覆盖成只剩用户消息。完成或失败时原位替换为终态内容；等待气泡使用 `role=status`、polite live region、`aria-busy` 和受 reduced-motion 保护的三点动效。 |
| 探针聊天跨界面历史对齐 | ✅ | 从消息里的「多聊聊」创建的 durable `scope=probe` / `scope=avoidance_probe` turn 与普通 `scope=chat` 一样进入插件、桌面 Web、移动 Web 的主对话历史；共享 renderer 保留统一时间顺序，pending / processing 继续按真实 turn 状态刷新，`scope=delight` 仍由推荐卡独立管理。 |
| 三端 probe 反馈语义 | ✅ | 桌面、移动和插件的兴趣/避雷 probe 统一使用 confirm/defer/reject/chat 语义，所有操作均有可见文字；推荐区不新增画像或对话纠偏引导入口。 |
| 四端换批语义 | ✅ | 桌面 Web、移动 Web 与扩展 side panel 都把当前卡片 ID 作为 `excluded_bvids` 提交换批；后端负责默认硬去重并在成功时只写一条中性的 `reshuffle` 批次事件，不批量提交逐卡 `dismiss`。桌面端已删除“换一批时忽略当前”开关；CLI 无持久卡片列表，继续由推荐历史与 `seen_items` 去重。 |
| 桌面 Web 前端偏好键 | ✅ | `/web` 的纯前端偏好继续走 `storageGet` / `storageSet`，不写 `config.toml`：`obc.theme` 保存主题三态，`openbiliclaw.webui.autoLoadOnScroll` 保存滚动自动加载开关；设置页保存状态行回显主题与滚动自动加载状态。 |
| 扩展捕捉 E2E 控制事件 | ✅ | local-only `/api/extension/e2e/run` 会通过 runtime stream 投递 `extension_e2e_run`，要求已安装扩展在真实平台页执行白名单 DOM 操作；`/api/extension/e2e/result` 回收插件执行结果，后端再按运行窗口匹配 `/api/events` 中自然捕捉到的事件。 |
| 兴趣探针投递保护 | ✅ | `interest.probe` 只有成功投递到 runtime stream 后才写入 `probed_domains` / `probed_axes` / `probed_distance_bands` 冷却状态；事件 payload 会带 `probe_mode` 与 `challenge`，前端离线时不会消耗 active probe。普通 `near` 探针与挑战探针使用独立 active 额度，运行时选择时仍统一仲裁。 |
| 避雷探针投递与仲裁 | ✅ | `avoidance.probe` 与 `interest.probe` 共用 proactive push 循环；每轮最多投递一个 probe，并用 `last_probe_kind` 在正向/负向都有候选时轮流选择，避免探针频率翻倍。 |
| 图片代理 API | ✅ | `/api/image-proxy` 为移动 Web 和浏览器插件代理白名单 CDN 封面图，逐跳校验 redirect，并在返回前完成类型和 10MB 大小校验；成功封面写入 `data/image-cache/`（小红书 token 归一化），并按「已消费且未保存」定期清理、保护无法重抓的封面；多模态 discovery 评估也复用同一缓存，命中时不再重新请求 CDN。新浪图床请求按当前 redirect host 附 `Referer: https://weibo.com/`，跳往其它 CDN 时立即移除，满足真实防盗链且不跨域泄漏。 |
| 自动更新 | ✅ | `AutoUpdateService` 检查 backend git tag，支持 `/api/update-status`、`/api/runtime-status` 更新摘要、手动 check/apply、跨配置热重载存活的进程级 apply 锁、可信 remote / dirty worktree / fast-forward guard，并通过 runtime stream 推送后端更新事件。dirty worktree guard 把 staged 修改 / 新增视为脏，同时继续豁免 `uv.lock`、未跟踪文件和本地 `ollama-models/`；apply 前会重置 `uv.lock` 再快进。git 命令通过 `asyncio.create_subprocess_exec` 执行，避免 Windows 长时间运行后线程池 `subprocess.run` 卡死或异常返回；tag fetch 使用 `git fetch --force --tags origin`，避免本地旧 tag 被远端重打后卡在 `would clobber existing tag`。`[network].mode=custom` 时 git 显式使用 `-c http.proxy=<url>`，uv/pip 显式叠加 `HTTP_PROXY/HTTPS_PROXY`；`direct/system` 的继承行为保持不变。**依赖同步按 daemon 的真实工具能力选择**：`uv.lock` 存在且 PATH 可解析 `uv` 时运行 `uv sync --no-install-project --inexact`；没有 `uv` 的官方 pip/venv 安装从 `pyproject.toml` 读取 runtime requirements，交给当前 `sys.executable -m pip`。两条路径都只同步依赖、保留 editable 项目与用户 extras，避免 Windows 后端运行时重装/替换被锁定的 console entry；完成后统一用 `python -m openbiliclaw.cli <原参数>` 跨平台重启。依赖工具缺失、300 秒超时和非零退出会把完整诊断写入本地日志，并在 `last_error` 留下工具/退出码/真实错误摘要。GitHub tags API 的 403/429 或传输异常会尝试 GitHub tags Atom feed 兜底；TLS 校验失败绝不以 `verify=False` 降级，直接上报 `tls_verification_failed` 并提示配置可信 CA。`detect_install_mode()` 上报 `frozen / docker / git / unsupported` 安装形态，桌面 Web 与扩展 popup 据此禁用非 git 安装的自动应用控件。**可信 remote 校验 git 实际使用的全部地址**：同时读取 `git ls-remote --get-url origin` 与 `git remote get-url --all origin`，任一地址不在 allowlist 即拒绝；`url.insteadOf` 改写后的非可信主机不能借原配置地址放行，也绝不自动改写用户 git 配置。规范化大小写与可选 `.git` 后缀，并把 GitHub 官方 `ssh.github.com[:443]` / scp 形态等价为 `github.com`；镜像/代理包装 URL 不会自动折算成官方地址。**守卫拒绝不再静默**：每条 guard 拒绝都 `logger.warning` 写明细，并把真实原因写入 `last_error`；修复命令中的仓库路径始终带双引号，含空格的 Windows / POSIX 路径可直接复制。apply 在任何 git 变更前验证 backend tag 通道与 prerelease 策略，`extension-v*` / `desktop-v*` / 畸形 tag 一律拒绝；候选排序按 SemVer §11 处理 prerelease：同号 stable 胜过任何 prerelease、`rc.10 > rc.9 > rc1`，且 UI 展示保留 prerelease 后缀（不再把 `0.4.0-rc1` 显示为 `0.4.0`）。**展示面范围**：桌面 Web 支持检查 / 应用并在 error 状态优先显示 `last_error`，扩展 popup 展示状态且禁用非 git 自动应用；移动 Web 更新面板与 CLI update 命令明确不在当前功能范围。**冻结守卫**：冻结包与 Docker 只运行 check-only 提醒循环，分别引导下载新版安装包或执行 `docker compose pull && docker compose up -d`。降级模式（LLM 注册表不可用）仍放行 update-status / check / apply，便于拉取修复版本恢复。 |
| 开机自启动管理 | ✅ | `runtime.autostart` 提供 macOS LaunchAgent、Windows HKCU Run、Linux XDG autostart 三套当前用户作用域 manager；Windows 源码安装使用 `pythonw + .pyw`，冻结桌面包直接注册 `OpenBiliClaw.exe` 并兼容识别旧双路径项。`reconcile()` 由 CLI 与桌面包入口共用，`/api/autostart-status`、`/api/autostart/apply`、`openbiliclaw autostart` 和设置页共用 env / shadow guard 与方向化 enable/disable 事务。 |
| API 双栈监听 | ✅ | `runtime.api_server` 在默认 `0.0.0.0` 配置下显式创建 IPv4 `0.0.0.0` 与 `IPV6_V6ONLY` 的 IPv6 `[::]` listener，并交给同一个 uvicorn server；CLI、Docker 命令入口与 Windows/macOS 桌面包共用，系统不支持 IPv6 或 IPv6 bind 失败时记录 warning 并保留 IPv4。 |
| Ollama 启动预检与生命周期 | ✅ | `runtime.ollama_supervisor` 统一提供 `ollama_required()`、endpoint 归一化、loopback 判定和 `_ollama_is_running()` / `_ollama_start_serve_background()`；`start` 仅在默认 `localhost:11434` 需要本机 Ollama 时尝试后台拉起，远端 / 自定义端口不强行 `serve`。托管启动会给子进程默认传入 `OLLAMA_KEEP_ALIVE=24h`（若用户已设置则保留用户值），减少 `bge-m3` / `llama-server` 在 UI 请求间隔中卸载再冷启动。Windows 模型路径编码故障自愈使用 `ollama_models_relocation_candidate()` 选 `%PROGRAMDATA%\OpenBiliClaw\ollama-models`（路径含非 ASCII 时放弃自动迁移），目录存在即视作 `managed_models_dir()` 持久迁移标记；后续托管启动用 `env.setdefault("OLLAMA_MODELS", managed_models_dir)`，显式用户环境变量优先。`restart_managed_ollama_with_models_dir()` 只重启本进程管理的 Ollama；若检测到外部启动的 daemon（运行中但没有 `_managed_proc`）则返回 `external_ollama`，避免杀掉用户自己开的官方 App / 服务。`_ollama_start_serve_background()` 现在记录**亲手拉起**的 `Popen` 句柄（复用外部已运行实例时句柄留空），`stop_managed_ollama()` 据此在退出时停掉整棵进程树（Windows `taskkill /T`、类 Unix 进程组 `SIGTERM`），对外部托管的 Ollama 一律不动 —— 桌面托盘「退出」经此调用，clean quit 不再遗留孤儿 `ollama serve` / `llama-server` runner。macOS 桌面包构建必须使用官方 `Ollama.app/Contents/Resources/ollama`，并同时打入同目录 `llama-server`、`llama-*`、`lib*.dylib`、`lib*.so` 和 `mlx_metal_*`；如果只发现 Homebrew 风格单独主程序或缺关键动态库，打包会失败，避免随包 daemon `/api/version` 正常但真实 embedding 500。v0.3.206+：当 `OPENBILICLAW_PROJECT_ROOT` 已设置（桌面包入口），托管 `ollama serve` 的 stdout/stderr 改写入 `<project>/logs/ollama-managed.log`（stderr 合并到 stdout，`stop_managed_ollama()` 负责关闭句柄），让 `llama-server` 崩溃日志可查；未设置时保持 DEVNULL（CLI / dev / 测试）。 |
| Embedding 初始化进度单例 | ✅ | `runtime.embedding_progress` 是进程全局、线程安全的无依赖状态源，供桌面包首启自动拉取、guided init 自动拉取、API 一键修复和 Ollama supervisor 共享。各生产路径调用 `mark_pull_running()` / `report_pull()` / `mark_pull_done()`，`/api/init-status` 再把它合并到 `embedding_check="repairing"`、`embedding_repair_*` 和 `embedding_pull_status`；`_ollama_start_serve_background()` 同步报告 `ollama_phase` 为 `starting` / `ready` / `down`。`reset()` 仅供测试隔离进程级状态。 |
| 账号同步 | ✅ | `AccountSyncService` 同步 B 站账号历史、收藏和关注等信号；历史按 `view_at + 同秒 bvid 集合` 增量导入，收藏 / 关注只把新增 ID 转成画像事件，避免重放旧信号；但每轮会把**完整收藏快照**经 `Database.mark_items_seen("bilibili", bvids)` 直接写进 `seen_items`（2026-07-26+）——只认新增的话，装 OpenBiliClaw 之前收藏的内容永远进不了去重、会被当新内容推回；快照标记幂等且不产生事件，因此不会重复计入偏好信号，失败只 debug 日志不影响本轮同步。每文件夹收藏上限与总预算一致（500，与 init 同口径），否则大收藏夹的尾部永远覆盖不到。收藏事件与 init 同口径携带播放量 / 发布时间 / 时长，并按 `favorite_item_is_dead()` 丢弃失效视频。新增事件先经 48h 跨源去重（扩展已上报的同一行为不再双计，`exclude_source="account_sync"` 防自压制），画像就绪后走 `ProfileUpdatePipeline` 增量管线而非整层重算；画像分析（管线及回退路径）默认受 360 秒墙钟上限保护，超时会取消并记录可见原因，不会把账号同步循环永久占住，文案明确模型服务 6 分钟未返回、Base URL / 模型名 / 网络 / 代理 / 响应过慢等常见原因，并经 `/api/init-status.detail` 同步给三端。每轮失败会持久化有界的 `last_sync_issues=[{stage,kind}]`，按 B 站历史 / 收藏 / 关注、X 点赞 / 书签和画像分析精确定位，并由 runtime-status 统一生成安全中文文案与严重度供桌面 Web 展示。X 来源启用且 Cookie 存在时同周期增量拉取 likes/bookmarks（tweet ID 集合去重，首轮从 events 表播种），并与 discovery producer 共用 `XSourceHealthStore`：冷却/登录失效/403 时不出网，首个 429 后不再紧接第二个请求。 |
| 多源 bootstrap 去重 | ✅ | `/api/sources/{xhs,dy,yt,zhihu,reddit,linuxdo}/task-result` 会用原子的 `source_bootstrap_state.json` 过滤跨任务旧 identity key；每源按响应顺序保留最新 5,000 个，任务首份 canonical 结果仍完整保留，只有新增项进入 durable event / profile owner 路径。 |
| 扩展任务 claim / 复用 | ✅ | XHS / 抖音 / YouTube / 知乎 / Reddit / Linux.do bootstrap 任务在扩展 poll 时用短生命周期 SQLite 连接标记 `in_progress`，CLI 默认复用 6 小时内近期可复用任务，避免重复打开任务 tab 全量扫描。Linux.do 的 `failed/degraded` 明确不参与复用：已取到的 degraded 事件保留，下次仍新建任务补齐失败 scope。周期调度、CLI fetch 与 guided init 共用 enqueue decision core：进程锁防线程交叠，SQLite `BEGIN IMMEDIATE` 把六表全局 active scan 与选中来源 insert 置于同一事务；`force` 只绕过近期终态复用，不能制造并行账号任务。 |
| 多源 bootstrap 去重 | ✅ | `/api/sources/{xhs,dy,yt,zhihu,reddit,v2ex}/task-result` 会用原子的 `source_bootstrap_state.json` 过滤跨任务旧 identity key；每源按响应顺序保留最新 5,000 个，任务首份 canonical 结果仍完整保留，只有新增项进入 durable event / profile owner 路径。 |
| 扩展任务 claim / 复用 | ✅ | XHS / 抖音 / YouTube / 知乎 / Reddit / V2EX bootstrap 任务在扩展 poll 时用短生命周期 SQLite 连接标记 `in_progress`，CLI 默认复用 6 小时内近期任务，避免重复打开前台 tab 全量扫描，也避免 FastAPI 并发 poll 在共享 connection 上嵌套事务。周期调度、CLI fetch 与 guided init 共用 enqueue decision core：进程锁防线程交叠，SQLite `BEGIN IMMEDIATE` 把六表全局 active scan 与选中来源 insert 置于同一事务；`force` 只绕过近期终态复用，不能制造并行账号任务。 |
| XHS 自动发现停止门与平台熔断 | ✅ | XHS `/next-task` 每次领取 legacy search / creator / bootstrap 前现读热重载后的来源开关和全局 scheduler；关闭时旧任务保持 pending、扩展只得到 204，不再打开自动抓取页面。search / creator 默认以 20 分钟为中心做 ±25% 稳定抖动，领取水位与平台冷却写入 `xhs_task_runtime_state`，跨后端 / MV3 重启生效；搜索默认每天 20 条，producer 每 20 分钟检查且只把 pending + in-progress 队列补到 5 条。安全验证、操作频繁或 429 触发 `1h → 2h → 4h … → 24h` 连续轮次冷却，同一冷却内重复报告不加轮次，冷却后的正常任务成功才重置；producer 同步停产，`/api/sources/status` 显示 `rate_limited/feed_paused`、连续轮次与剩余时间。明确由用户发起的 native-save 独立于 discovery 开关，但不会越过安全冷却。 |
| Soul 画像自动 bootstrap | ✅ | `AccountSyncService` 首次成功写入账号行为并完成 `analyze_events()` 后，若 soul 画像仍为空，会自动调用 `build_initial_profile([])`；每进程生命周期最多尝试一次。 |
| 降级模式启动 | ✅ | 生产 `create_app()` 遇到 `RegistryBuildError` 时构造 degraded `RuntimeContext`，保留 provider-free ping、健康检查、配置读取/保存、runtime status、runtime stream、`/` → `/web`、`/web`、`/setup`、`/m` 四个静态恢复 surface 及 `/favicon.ico`。恢复控制面还精确放行 `POST /api/config/probe-service`、`POST /api/config/discover-models` 与 GET/POST 来源比例建议：前两者只从用户提交的草稿临时建 provider，并继续经过稳定的 total LLM gate，不依赖启动失败的 active registry。桌面端先用 ping 识别恢复态并停止业务 hydration，再读取配置、自动打开模型设置并展示中文修复指引；推荐、发现、画像等业务 API 在修复前继续返回 503。有效配置保存后直接在该上下文原子构造完整 runtime、解除 guard 并启动后台任务，不要求进程重启。 |
| 配置热重载 LLM override | ✅ | `RuntimeContext._rebuild_components()` 从 config 构造 `module_overrides`，同时注入主 `LLMService` 与 `SoulEngine` 内部 service；热重载后的正向兴趣和避雷 speculator tick 都 detached 到 `BackgroundTaskRegistry`，不阻塞 `/api/config` 响应。 |
| 海外网络策略热更新 | ✅ | FastAPI 启动与 `PUT /api/config` 成功落盘后都会先把 `[network].mode + proxy` 镜像到 `openbiliclaw.network`，再构造 / 重建 LLM、YouTube、Bangumi、更新和 Codex OAuth 客户端；`POST /api/config/probe-service kind=network_proxy` 不落盘，按当前草稿的 direct/system/custom 策略真实发起 204 探测（草稿只带 `proxy`、不带 `mode` 时按缺键判定：非空 `proxy` 仍是 `custom`，空则 `system`）。Docker 启动器仅在容器内检测到代理变量且用户未显式选模式时补 `OPENBILICLAW_NETWORK_MODE=system`。微博 client 固定 `trust_env=false` 国内直连，不进入这套海外 client rebuild。 |
| 原生保存 service 热重载 | ✅ | `saved_sync_service` 是可替换组件：每次构造新 `BilibiliAPIClient` 时同步创建 router + 六平台 extension adapters + `BilibiliNativeSaveAdapter` + `SavedSyncService`。重载先取消旧 registry inflight；所有新组件构造成功后才原子发布，任一构造失败保留完整旧组件与稳定 broker。 |
| 原生保存 local-first 入口 | ✅ | 自动和手动同步都复用 `SavedSyncService.create_sync_task()` / `run_sync_task()`；`POST /api/saved/{list_kind}` 先提交本地 membership。`unsupported_adapter_missing` 可重试，`unsupported_content_type` 与显式 `local_only_source` 为终态；微博 membership 当场落 local-only 且不创建 task，纯 local-only `/sync` 在 ledger 写入前拒绝。已接线的六个平台 executor 都可能因扩展离线进入 `extension_required`。 |
| 桌面包 SOCKS 代理兼容 | ✅ | 默认运行依赖使用 `httpx[socks]`，PyInstaller spec 显式收集 `socksio`；用户系统配置 `ALL_PROXY` / `HTTPS_PROXY=socks5://...` 时，冻结桌面包创建 OpenAI / 兼容 LLM 客户端不会因缺少可选 SOCKS 运行时依赖而在启动阶段崩溃。 |
| 运行时图像处理依赖 | ✅ | 默认安装显式携带 `Pillow>=10.0`，因为 `discovery.multimodal` 的封面压缩路径直接 import `PIL`；不再依赖 B 站 SDK 或打包 extra 的传递依赖碰巧提供 Pillow。 |
| 运行日志降噪 | ✅ | 全局 logging 初始化会把 `httpx` / `httpcore` / `openai` / `openai._base_client` logger 提升到 WARNING，避免文件日志在 DEBUG 模式下被连接细节和完整 LLM 请求体刷屏；业务模块仍按 `logging.file_level` 输出。 |

- 桌面侧栏是 flex 行内项：按钮的 `aria-expanded` 与侧栏的 `aria-hidden` 同步，内容宽度随
  312px 侧栏平滑让渡。Delight 以主内容实际 inline-size 响应，而非只看 viewport。
- Delight 的响应式布局用 `.delight` 网格的 `grid-template-areas`（thumb/body/actions/status）分级：
  宽栏操作行贴正文列下方；`@container desktop-main` ≤940px 时缩略图与正文仍并排、操作行下沉为跨整卡
  宽度独立一行（去看看/聊一聊靠右、反馈图标靠左，issue #115 修复标题与按钮被裁）；≤560px 整卡竖直堆叠，
  ≤430px 保留窄屏内联输入框。操作行与状态行是 `.delight` 直接子节点，能脱离正文列约束跨整卡展开。
- Delight 拖拽 10px 才进入拖动态，50px 才切换卡片；滚动自动加载仍使用 50px
  root margin。前者避免点击抖动，后两者分别控制明确切换与接近视口时加载。

### Visual prewarm wiring API

RuntimeContext 重建 RecommendationEngine 时透传视觉开关、帧数、两个 fetch limit 和摘要
长度；后台 task 通过现有 BackgroundTaskRegistry 管理画像 single-flight rebuild。配置热重载
后新 embedding provenance 会重新筛选待处理池。

## 公开 API

### Durable dialogue execution lane

```python
from openbiliclaw.runtime.dialogue_reply_scheduler import (
    DialogueExecutionCoordinator,
    DurableChatReplyScheduler,
    TerminalChatReplyError,
)

async with coordinator.lease():
    ...  # admission 后解析当前 RuntimeContext owner

scheduler.schedule(turn_id)  # 幂等 wake；pending SQLite row 才是权威事实
await scheduler.start()       # 分页恢复全部 pending
await scheduler.close()       # 取消内存 worker，数据库仍保持 pending
```

`pause_and_drain(timeout=...)` 只有在 active lease 结束后才返回；timeout/cancel 会先恢复 admission 再抛出。scheduler 只把 `TerminalChatReplyError` 交给 failed CAS，普通异常与 `CancelledError` 不改变 durable pending 事实。`status_payload()` 返回队列健康摘要，不返回 turn ID、message 或 reply。

扩展共享原生保存基础（6/6 executor 已接、fixture 全覆盖，并于 2026-07-14 完成 favorite + watch-later/fallback 真实账号验证）：

```typescript
isNativeSaveTask(payload)
sanitizeNativeSaveResult(result)
runNativeSaveTask(task, platformSlug, authenticatedPostResult)
installNativeSaveExecutor(platform, executor)
createXBrowserEnvironment(root?, currentUrl?)
createYouTubeBrowserEnvironment(root?, currentUrl?)
createXiaohongshuBrowserEnvironment(root?, currentUrl?)
createDouyinBrowserEnvironment(root?, currentUrl?)
```

runner 只通过调用方注入的已认证 closure 回传结果；它自身不创建后端 fetch。busy mutex 只覆盖 tab create/load，加载完成立即释放；每个 executor 仍从调用起使用自己的 absolute deadline，timeout 固定回传 `failed/native_save_timeout`，迟到的 tab-create success 也会被回收。所有 listener/tab/mutex cleanup 独立 guarded。content 的 once fence 仅保证当前 256 项 recent outcome window（含 in-flight）内不重复执行，不是永久 task ledger。

```python
from openbiliclaw.runtime.updater import AutoUpdateService

service = AutoUpdateService(enabled=False, check_interval_hours=6)
backend = await service.check_now()
status_code, apply_payload = await service.request_apply(tag="backend-v0.3.92")
```

核心调用：

- `check_now()`：立即检查 GitHub tags，只刷新后端更新状态，不自动应用。
- `request_apply(tag="backend-vX.Y.Z")`：先检查安装形态为 `git`（`frozen` / 其他以 `unsupported_install_mode` 拒绝、`docker` 以 `docker_install_mode` 拒绝——见下）、git repo、可信 `origin`（按 `_canonicalize_remote_url` 规范化比较：大小写不敏感、`.git` 后缀可选、`https://` 与 `git@…:` / `ssh://` 等价；镜像包装 URL 不折算，需显式加入 allowlist）、worktree clean（仅 `uv.lock` 改动豁免——发布 tag 携带过期 lock 时安装侧 `uv sync` 必然改写它，不能因此永久阻塞更新）、未 merge/rebase、目标 tag 存在且当前 HEAD 可 fast-forward，再返回 `202/applying` 并在后台执行 `git checkout -- uv.lock`、`git merge --ff-only <tag>`、按可用工具做 dependency-only 同步和 `python -m openbiliclaw.cli` 重启。任何守卫拒绝都会 `logger.warning` 写明细（含实际 remote URL / 脏文件列表）并把原因写入 `last_error`；依赖同步失败也会记录真实命令输出，并只向状态 API 暴露安全摘要。
- `check_and_update_if_due()` / `check_and_update_now()`：供后台调度使用；只有 `scheduler.auto_update_enabled=true` 时才会定时自动应用。冻结桌面包与 Docker 容器走 check-only 分支：**无论开关状态**都按间隔检查（`_background_loop_enabled()` 对 frozen / docker 恒真）——frozen 跟踪 `desktop-v*` 安装包 tag，docker 跟踪 `backend-v*`（镜像随后端版本发布），发现新版置 `update_available` 并推 `backend_update_available` 事件提醒用户下载新安装包 / 拉取新镜像，但永不进入 apply——`request_apply` 的非 git 守卫独立兜底，后台循环不可能 fast-forward 共享目录里的 git 检出。
- `adopt_status_from(other)`：配置保存触发热重载、本服务被重建时，由 `rebuild_from_config` 调用以携带上一实例的检查结果（版本 / tag / 上次检查时间总是携带；`update_available` / `up_to_date` / `blocked` 等已结算状态也携带，瞬态 `checking` / `applying` 不携带）。否则设置页状态行会从「发现新版本」回退到「尚未检查更新」直到下个检查周期。
- `detect_install_mode()`（模块级函数）：上报安装形态——`frozen`（PyInstaller 桌面包，结构上无法 git 自更新）、`docker`（容器内运行，代码烧在镜像里；经 `docker_runtime.is_running_in_container()` 判定：`OPENBILICLAW_IN_CONTAINER` 环境变量（Dockerfile 已内置）或 `/.dockerenv` / `/run/.containerenv` 标记）、`git`（installer / agent / dev 克隆）、`unsupported`（其他）。**安全守卫**：冻结桌面包可能与 AI / 一键安装共用 `~/OpenBiliClaw` 目录（`entry.py` 把 `OPENBILICLAW_PROJECT_ROOT` 指向它，目录里是真实 git 检出），此时磁盘上有 `.git` 但仍必须拒绝自更新——否则会改写他人源码 + venv 而冻结包重启后仍跑捆绑旧码，形成无限重启循环。故 apply 路径显式判 `install_mode == "git"`，不只依赖 `.git` 是否存在；`docker` 判定优先于 `git`，容器里即便挂载了 git 检出也不会误入自更新路径（快进检出改不了运行中的镜像代码）。
- **更新通道**：git 安装与 Docker 容器跟踪 `backend-v*` 源码 tag（legacy `v*` / 裸 semver 兜底；GHCR 镜像随 backend tag 发布，同一版本号）；冻结桌面包跟踪 `desktop-v*` 安装包 tag（`_parse_desktop_candidate`，无 legacy 兜底——两类 tag 不总是同步发布，桌面用户只关心有没有新安装包）。`_fetch_latest_candidate(channel=...)` 按 `check_now` 里的安装形态选通道。
- `get_update_status()`：返回 `/api/update-status` 使用的 backend 状态对象，含 `install_mode`。
- `get_runtime_status()`：返回 `/api/runtime-status` 合并用的自动更新摘要，包含当前版本、最新远端版本、上次检查、错误、状态原因和 `install_mode`。

### ContinuousRefreshController

```python
controller.candidate_eval_coordinator.notify("candidate_enqueued:bilibili")
```

核心调用：

- `request_replenishment(reason=..., force=False)`：补货请求的统一入口。`force=False` 只记录触发原因，等待定时 `refresh_if_needed()` 统一检查池子缺口；`force=True` 用于初始化完成、用户手动刷新和推荐刷新后低库存路径，会启动手动补货并消费已排队的 reason。
- `supply_candidates_once(reason=...)`：候选评估器没有 raw work 时的需求驱动入口。它按平台族缺口并行 tick B 站扩展兜底、小红书、抖音、YouTube、X、知乎、Reddit、Bangumi、Linux.do producer，再执行既有 B 站 refresh plan；同平台周期 tick 与即时 tick 共用 per-source lock，避免重复 fetch。返回 `producer_results`、`supply_progress_count` 和 `supply_productive`，其中候选 pipeline 的 `inserted/enqueued` 是权威生产性，全部 durable duplicate 即使策略执行过也仍为无产出。
- `supply_candidates_once(reason=...)`：候选评估器没有 raw work 时的需求驱动入口。它按平台族缺口并行 tick B 站扩展兜底、小红书、抖音、YouTube、X、知乎、Reddit、Bangumi、微博 producer，再执行既有 B 站 refresh plan；同平台周期 tick 与即时 tick 共用 per-source lock，避免重复 fetch。返回 `producer_results`、`supply_progress_count` 和 `supply_productive`，其中候选 pipeline 的 `inserted/enqueued` 是权威生产性，全部 durable duplicate 即使策略执行过也仍为无产出。
- `notify_expression_copy_pending(reason)`：向 runtime-owned `ExpressionCopyCoordinator` 发送同步、best-effort 唤醒；方法本身不调 LLM。coordinator 的 pending provider 读取 `RecommendationEngine.count_pending_expression_copy_demand()`，所以唤醒只会消费当前 copy-ready 水位缺口。
- `refresh_if_needed()` / `force_refresh()`：按 pool available 缺口、source share 和 raw-material headroom 构建补货计划；如果正式可换池已经达到 `pool_target_count`，返回 `pool_at_cap` 并跳过 discovery。后台 `refresh_if_needed()` 还会应用约 90% 的 replenishment low-watermark：略低于 target 时只维护状态，不触发 discovery；`force_refresh()` 是显式用户动作，仍按 source 缺口尝试补货。注入 `DiscoveryCandidatePipeline` 后，refresh 会优先调用 `ensure_pending_supply()`，按实际新增 `pending_eval` 数补足 Evo 供给，而不是只跑一次 discover；API daemon 已有 coordinator 时，pipeline 的一次 enqueue callback 立即唤醒唯一 owner，refresh 不会再同步 `drain_pending()`，从而保持 durable `evaluating <= 3×30`。没有 coordinator 的 composition 可选 `one_shot_inline_eval_limit`；OpenClaw bootstrap 将它固定为 4，使这次 refresh 的 source supply 与 inline drain 都不超过 4，fetch oversample=1、min eval batch=4、inline evaluator=1，后续 OpenClaw 请求再补下一批。该值是 integration 内部策略，不是 `config.toml` 字段；API runtime 不设置它，仍保持 4× supply oversample 与 coordinator worker 波次。完整 B 站四策略补货在小缺口阶段只给 `search + related_chain` 配额，`trending/explore` 到更深缺口再跑。当待评估水位已足够时不会再 claim B 站搜索关键词，避免空跑关键词被误标失败；当统一关键词 planner 已启用但 B 站关键词 store 暂空时，会从本轮策略组移除 `search`，而不是传 `queries=None` 触发旧 `discovery.search.queries`。池子低于 target、`_build_source_replenishment_plan()` 为空、但 candidate 管线没有 `pending_eval/evaluating` 在途工作时，`_build_refresh_plan` 会回落到按 `trending_refresh_minutes` / `explore_refresh_minutes` 的 B 站周期计划，让健康超份额来源在其它来源不可用或节流时继续补全局库存；仍为空时才按 `pool_available` 指纹节流输出完整诊断（首次、指纹变化或距上次完整诊断至少 300 秒打 INFO，包含 `pool_available/raw/pending/suppressed/source_available/source_raw/source_targets/raw_targets/requested_by_source`；窗口内重复调用只打带累计抑制数的 DEBUG，且不再执行三组重统计）。
- `drain_discovery_candidates_once(..., reason=...)`：runtime 已有 coordinator 时退化为耐久 `notify(reason)`，不再创建一次性 drain task；没有 coordinator 的 CLI / 兼容 runtime 仍通过相同 staged pipeline 执行一次 drain。
- `run_init_backfill(profile, target_pool_count, *, fully_parallel=True, progress_callback=None)`：图形化引导初始化（gui-init）stage 4 的首轮可用推荐闭环。持 `_refresh_lock` 与连续 refresh 串行，绝不与之争 `content_cache`；发现后在同一锁窗口内同步 drain `RecommendationEngine` 的待生成表达候选，更新 gate 库存并校验 `count_pool_candidates()>0`，raw / evaluated 行本身不算成功。`progress_callback` 依次报告发现、表达、校验和 ready；`async with` 在 `CancelledError` 时释放锁。不查 `_llm_work_allowed()`，因此 init 期间后台门控暂停不会自锁 init 自己的补池。
- `_pool_count_payload()`：统一生成 runtime status / runtime stream 的池子字段，包含 pending eval 与 evaluated pending 拆分。
- `_update_llm_inventory_state(available)`：把 canonical durable available 与 `pool_target_count` 同步到共享 gate；不接受 Task 6 的 projected/transient count。
- `_enforce_pool_cap()` / `_enforce_pool_cap_async()`：两条路径把 target、跨表 raw ceiling、available/raw source quotas、topic/explore cap、stale age 与 XHS 本人昵称传给同一 bounded storage 入口。异步路径使用专属 maintenance worker + 独立连接，每批 `max_mutations=50`、最多 8 批并在批间让出事件循环；短 `busy_timeout` 冲突会读取独立 readiness 并把维护延后。成功返回最后一批 `result.at_target`，post-snapshot rollback 时记录 ERROR 并按事务前 availability 决策。storage 的恢复阶段先排除已满 topic，并把 suppressed 恢复严格限制在 raw headroom 内；raw 已满时先裁剪，protected/token-owned excess 无 victim 时直接以 `has_more=False` 收敛。排名窗口试探若没有形成 canonical available 净增长会在同一事务撤销，因此稳定指纹不会在每个 tick 内跑满 8 批。每批汇总日志包含恢复、stale/explore/topic/source/raw、写入、锁等待、总耗时、修改行数和 `has_more`。

`run_startup_maintenance()` 把生命周期固定为“原子维护/历史恢复 → 暴露服务或启动后台工作”，并用 controller 内部完成标记避免同一 host lifecycle 重复维护。API daemon / 热重载由 `run_forever()` 先调用该钩子，再执行 delight、candidate 与 background loops；OpenClaw 不运行 `run_forever()`，因此 direct bootstrap 在返回 adapter services 前同步调用同一钩子，并保持 one-shot inline candidate evaluation。`_enforce_pool_cap()` 每次先清空 success signal，只有拿到 `rolled_back=False` 的 `PoolMaintenanceResult` 才置为成功；snapshot/DB 异常即使被 fallback bool 吞掉、或事务返回 rollback，都不会完成 startup 标记，后续 host 调用仍会重试。

`_run_refresh_plan()` 在 durable admission 与文案完成后只调用这一个入口；不再组合 `trim_topic_group_overflow()`、`trim_explore_cluster_overflow()`、`evict_stale_pool_items()`、source trim 或 raw trim，因此不会留下“前半段已提交、后半段才发现库存归零”的中间状态。旧数据库 trim 方法仍保留给兼容测试和手动工具。

### CandidateEvalCoordinator

- `notify(reason)`：generation 递增并 set event；等待前会重读 durable snapshot，避免 clear/wait 边界丢唤醒。任意 reason 都清当前 supply cooldown，允许下一迭代立即探测一次；精确 `startup`、`manual_*`、`config_*` 和真实 `candidate_enqueued:*` 会重置无产出阶梯与 starvation WARNING latch，但候选入队不会解除“无 provider / 鉴权失败”的 paused 状态。
- `run_forever()` / `stop()`：管理 claim owner、worker task map、串行完成 lane、退避和取消清理；停止时按 token 释放所有未完成 claim。补货 mapping 优先读取显式 `supply_productive` / `supply_progress_count` / `supply_inserted_count`，再兼容通用 `inserted/enqueued/cached/discovered`；只要显式计数为 0，即使 `refreshed=True` 也进入 30/60/120/300/600 秒阶梯。只有没有新字段的旧 mapping 才回退 `refreshed`，非 mapping 兼容结果仍保守视为有产出。冷却全部复用主循环的 activity wait，不新增常驻定时任务。
- `post_commit_callback`：首次成功缓存后立即启动，后续完成批次在任务运行期间只标记一次 rerun；它与 worker 并行，不阻塞第四批即时补位，停止时由 coordinator 统一取消并 gather。
- `status_payload()`：返回 `candidate_eval_state/workers/in_flight/pending/backoff_until/supply_streak/supply_cooldown_until/last_error/last_batch_seconds/last_cached/last_rejected`，由 runtime status 与 pool event 合并发布；真实键名均带 `candidate_eval_` 前缀，其中新增键为 `candidate_eval_supply_streak` 和 `candidate_eval_supply_cooldown_until`，后者与既有 backoff 一样使用 monotonic deadline 语义。
- `on_admitted(count)`：同步、返回 `None` 的轻量通知接口；协调器不 await 文案工作，因此 admission 通知不会占住串行 commit lane。Task 7 的文案协调器通过该接口接入，本任务不改变其微批状态机。
- `candidate_evaluation_owned_by_coordinator`：仅 API daemon 在 coordinator attach 后，对会同步 drain 的 Douyin / YouTube / Zhihu producer 置为 `True`。B 站 refresh、X、Reddit、Bangumi 与微博同样走共享 pipeline；X 不再直接写数据库，因而所有 managed source 都经 pipeline 的一次 callback `notify("candidate_enqueued:pipeline")` 立即唤醒 coordinator，且不得再调用 `drain_pending()`。OpenClaw direct adapter 不启动该 owner，故其 producer 保持 `False` 并走有 90 条硬上限的 inline drain；独立/CLI 也保持此兼容路径。

### EventProcessingScheduler（兼容别名 `FeedbackBatchScheduler`）

```python
from openbiliclaw.runtime.feedback_scheduler import EventProcessingScheduler

scheduler = EventProcessingScheduler(soul_engine, debounce_seconds=5.0)
scheduler.schedule()
```

核心调用：

- `schedule()`：把 dirty wake 置为 `1`；若没有活跃任务，创建一个后台任务，等待 debounce 后在同一 owner pass 依次调用 generic 与 content-feedback consumer。wake 不携带事实，真实 backlog 仍在 SQLite / pipeline checkpoint。
- `recover()`：同步跑完一次恢复 pass；只供热重载等已经 pause+drain、必须把旧 owner 遗留工作清空后才能 rebind 的顺序屏障使用。
- `start_background_recovery()` / `start_periodic()`：首次 FastAPI startup 只 admission 一个 scheduler-owned recovery task，不 await consumer 或 LLM；之后每 5 秒做丢 wake 安全扫描。
- `drain()`：测试辅助，等待当前调度任务结束。
- `close()`：关闭 API 时取消并 gather 还没跑完的调度任务；startup 暴露的 recovery task 只是同一 owned task 的观测引用，不形成第二个 owner。

调度语义：

- 多个 `/api/events`、`/api/feedback` 与 source/account-sync wake 在 debounce 窗口内合并成一次 owner pass。
- owner pass 执行中再次收到 wake，会把 dirty 标志重新置位；当前处理结束后再等待一个 debounce 窗口并补跑一次。
- Soul 层的两个 consumer 各自保留 single-flight lock 和 durable cursor；调度器拥有执行时序，不拥有事实。
- **统一兴趣线 owner**：generic consumer 领取显式 `profile_update_owner="generic"` 行；content-feedback consumer 领取 `like/dislike/comment/dismiss` 且非 import 的反馈。每批以稳定 event-derived ID 调用 `checkpointed_enqueue_batch()`，同一次原子状态替换同时发布 buffer+cursor，然后 `tick_if_buffered()` 仅在存在恢复信号时消费。hypothesis/import feedback 只越过 feedback cursor，retraction 在 generic cursor 前投影。首次 API startup 的严格顺序是 `owner cutover fence → local scheduler admission → HTTP lifespan 返回`；真正的 event scan / checkpoint / consume 在 scheduler-owned task 中继续，即使 provider 401、pending buffer 的 LLM 或永不返回调用已经开始，也不延迟 listener 与 `/api/health`。这里不靠给 `_process_once()` 加短 timeout 或粗暴 cancel 来伪造快速启动，durable cursor/buffer 语义保持完整。shutdown 通过 `close()` 回收；配置热重载则仍执行 `pause_and_drain → recover()`，待旧 owner 真正清空后才 rebind/restart。仅显式设置 `scheduler.unified_interest_line=false` 时，内容反馈 consumer 回到旧反馈批路径。

### InitCoordinator + InitPrereqs（引导初始化）

`InitCoordinator`（`runtime/init_coordinator.py`，惰性挂在 `RuntimeContext.init_coordinator`）是图形化引导初始化的生命周期所有者：`init_runs` 持久化状态机、单写者进度事件（`_write_lock` 串行化心跳 / 进度 / 取消 / 终态写入）、`BEGIN IMMEDIATE` 单飞预定、启动 `reconcile_on_boot()`（崩溃残留 `starting/running` 判失败）、协作取消、bootstrap task 归属（供写者门控放行 init 自己的 task-result）。阶段 3 完整画像落盘后，阶段 4 才进入 `ContinuousRefreshController.run_init_backfill()`；该方法持 `_refresh_lock` 贯穿发现、评估、首批推荐表达 drain 与 canonical 可用性校验，防止连续 refresh 插入同一初始化事务窗口。`InitPrereqs`（`runtime/init_prereqs.py`）提供 TTL 缓存 + 单飞的 `chat_ready()` / `bilibili_check()` / `enabled_platforms()` 前置探测。共享流水线 `cli.run_guided_init`、`/api/init*` 端点和 init 期间写者门控详见 [init 模块文档](init.md)。

### Embedding Progress

```python
from openbiliclaw.runtime import embedding_progress

embedding_progress.mark_pull_running("bge-m3")
embedding_progress.report_pull("downloading", completed=240_000_000, total=568_000_000)
snapshot = embedding_progress.snapshot()
embedding_progress.mark_pull_done(ok=True, error="")

embedding_progress.report_ollama_phase("starting")
phase = embedding_progress.ollama_phase()

# 仅测试隔离：清空拉取态并把 Ollama phase 置回 ready
embedding_progress.reset()
```

`snapshot()` 返回 `{running, model, completed, total, status_text, done, ok, error, started_monotonic}`。`reset()` 会同时清空拉取状态并把 `_ollama_phase` 置为 `ready`，因此仅用于测试前后隔离；生产调度失败的回滚必须用 `mark_pull_done(False, error)`，以保留真实 Ollama phase。该模块不能 import API / config / registry，避免桌面入口、API app 和 supervisor 之间形成循环；所有环境判断仍留在调用方。`/api/embedding/repair` 的 `not_running` 自愈也复用 `runtime.ollama_supervisor.ollama_required()`、`is_loopback()` 与 `_is_default_ollama_endpoint()`，只在 `autostart.manage_ollama=true` 且 endpoint 是默认 loopback `11434` 时尝试拉起托管 Ollama。

### Degraded RuntimeContext

`build_runtime_context()` 仍然保持严格：LLM registry 无法构建时直接抛出 `RegistryBuildError`，方便测试和 CLI 调用方快速失败。FastAPI 生产入口 `create_app()` 会单独捕获这个错误并调用 `build_degraded_runtime_context()`。

降级模式下可用接口：

- `GET /`、`GET /web[/...]`、`GET /setup[/...]`、`GET /m[/...]` 与 `GET /favicon.ico`：静态恢复界面及其 CSS / JS / 图片继续可达；根路径复刻 `packaging/entry.py` 的落点规则——降级模式或画像未初始化（`is_profile_ready()` 明确为 False）时 302 到 `/setup/`，就绪或探测结果未知时 302 到 `/web`（SPA 引导初始化卡片兜底），setup 静态目录缺失时始终回落 `/web`。桌面端从配置响应或 runtime-stream 识别 `degraded` 后自动进入模型设置，展示 blocking issue，并提示补齐 Provider 配置；保存成功后同一进程原地恢复。静态路径使用精确 segment 边界放行，不会把 `/webhook` 一类无关前缀误纳入白名单。
- `GET /api/ping`：继续作为不访问数据库和模型 Provider 的快速 liveness probe；正常模式仍只返回原有 `status` / `service`，降级模式额外返回 `degraded=true`、`degraded_reason` 和 issues。桌面端先请求它；一旦确认降级，只读取 `/api/config` 并停止推荐、画像、平台源等业务 hydration，避免预期中的 503 控制台噪声与推荐重试。
- `GET /api/health`：返回 `status="degraded"`、`reason="llm_registry_unavailable"` 和 blocking issues；当 `SoulEngine` 可用时会额外返回可选字段 `profile_ready`，表示 soul 画像是否已生成。v0.3.95+ 额外返回 `embedding_ready`（bool）。v0.3.137+ 该同一 live probe 也被 `/api/init-status` 复用：若 `[llm.embedding].provider` 已配置，初始化前置清单会下发 `embedding_required=true`，`can_start` 与 `POST /api/init` 都必须等真实 probe 通过；provider 留空则可降级初始化。v0.3.97+ 这是一次**实时探活**而非「服务是否构建」：经 `EmbeddingService.probe()` 绕过缓存真打一次 provider，探测缓存保存 `ready / failed / timed_out` 原始三态而非调用方布尔值，并由 `_EMBEDDING_PROBE_TIMEOUT_SECONDS`（默认 15s）上限兜住。普通 `/api/health` 仅把 loopback Ollama 的 `timed_out` 解释为冷加载中的乐观可用，避免外部 Homebrew / 官方 Ollama 默认 5 分钟卸载后让插件横幅误报停服；远程 Ollama 或非 Ollama provider 超时仍为 `false`。成功沿用 `_EMBEDDING_READY_TTL_SECONDS`（默认 30s），明确失败与超时使用 8s 短 TTL 重探；single-flight 锁继续让并发 health/init 共享同一次真实 probe，但各入口独立解释结果。provider 现已 404/500（如 `bge-m3` 没拉、Ollama 停了、随包缺 `llama-server`）、返回空向量或抛出异常仍会如实报 `false`，修好后下次探活即翻 `true`；服务对象不存在仍 `false`，老/无 `probe()` 的服务回退「构建即就绪」。`false` 表示语义去重 / MMR 多样性降级（可能刷到换皮重复内容），插件 popup 据此显示「一键启用本地 Ollama」横幅。
- `GET /api/config`：返回完整配置、`degraded=true` 和同一组 issues。
- `PUT /api/config`：验证并保存修复配置，随后从降级上下文原地构建完整 runtime；成功返回 `reloaded=true / restart_required=false` 并立即解除业务 API 的 503 guard。核心构造失败会回滚配置并保持降级。
- `POST /api/config/probe-service` 与 `POST /api/config/discover-models`：把未保存的 `config.llm` 草稿应用到内存副本，分别做一次真实目标实例探测或 OpenAI-compatible `GET /models`；不读取失败的 active registry、不写盘，LLM 探测仍经过该进程稳定的 total gate。这样 `/setup/`、桌面 Web 与插件能够先验证 replacement endpoint，再保存恢复配置。
- `GET|POST /api/config/source-share-suggestion`：只使用当前配置、表单开关与本地事件计数生成建议比例，降级时同样可用。
- `GET /api/runtime-status` 与 `/api/runtime-stream`：用于 popup 展示降级状态；stream 会先发送 `{type:"degraded", ...}` 并保持连接。

除上述静态恢复界面和 allow-list 接口外，其他 API 在降级模式下返回 503，避免在缺少 LLM registry、数据库/运行时组件不完整时继续执行推荐、发现或画像链路。

### Runtime Status Pool Counts

`GET /api/runtime-status` 和 runtime stream 中的池子字段语义如下：

- `pool_available_count`：真实可换数量，只统计 fresh、未 dislike、未进入推荐历史、未近期看过、已有 `pool_expression` / `pool_topic_label`、已有 `style_key` / `topic_group` 且来源可打开的候选，并按默认每 `topic_group` 最多 3 条的候选窗口计数。
- `pool_raw_count`：fresh、未 dislike、未进入推荐历史的 `content_cache` 素材库存 + `discovery_candidates` 中尚未缓存的 raw candidates，用于诊断池子里是否还有原料。
- `pool_pending_count`：未命中持久化 `seen_items`、但仍缺文案 / 分类 / 可打开链接等 readiness 条件的 `content_cache` 素材数，加上待评估 / 已评估待入池候选；不会用 `raw - available` 近似，避免把已看内容误算为待整理。
- `pool_pending_eval_count`：`discovery_candidates.status IN ('pending_eval', 'evaluating')` 的数量，表示已经找到但还没完成统一 LLM 评估的内容。
- `pool_evaluated_pending_count`：`discovery_candidates.status='evaluated'` 中 temporal disposition 仍为 `eligible` 的数量，表示可继续 admission 到 `content_cache` 的内容；`review_due` / `expired` durable waiter 由独立 raw waiting 信号触发生命周期清扫，前者重新排队、后者终态拒绝。
- `last_discovered_count`：最近一轮 refresh 新入队的 raw candidates 数；已评估待入池候选的 retry / admission 不会冒充“新发现”。
- `pending_signal_events`：`discovery_runtime.last_processed_event_id` 之后新增的 discovery-trigger 行为事件数，只用于判断是否触发 `search + related_chain`，不表示画像 pipeline backlog。普通 `/api/events` 只提交 durable row 并 wake；generic `profile_events` consumer 与 `content_feedback` consumer 分别按自己保存在 `pipeline_state.json` 的 cursor 扫描归属事件，通过 `checkpointed_enqueue_batch()` 原子发布 buffer+cursor 后调用 `tick_if_buffered()`，完全不推进 discovery 水位。补货执行由已排队的 replenishment reason、周期 `tick()` 或用户刷新后的低库存检查统一触发。
- `recent_pool_topics`：最近一轮实际 admission 到推荐池的内容主题；retry-only admission 可以更新该字段，但不会增加 `last_discovered_count`。

前端凡是显示“可换”都必须只读取 `pool_available_count`。`pool_pending_count` / `pool_pending_eval_count` / `pool_evaluated_pending_count` 只能用于“正在整理成可换内容”等辅助文案和诊断。

`refresh.pool_updated` 不只来自后台补货和文案预计算。`GET /api/recommendations` 在无历史推荐时会从池子 bootstrap；`reshuffle` / `append` 则在 recommendation + shown 原子提交后，先用 `ServeResult.pool_counts_after` 直接发布扣减快照，不做响应内重复扫描，再 detached 读取一次精确 canonical readiness 处理 per-topic 窗口补位等差异。已打开的插件、移动 Web 和桌面 Web 应用该快照刷新库存数字、底部可换提示和空态文案，但不得因此重拉 `/api/recommendations` 替换当前列表。

### Activity Feed

`GET /api/activity-feed` 返回 popup、移动 Web 和桌面 Web 共用的轻量动态摘要：

- `live_summary`：当前 runtime 摘要；优先显示手动补货中的 `manual_refresh_message`，否则根据 discovery signal 水位或可换池库存生成短文案。
- `headline`：最新动态条目的摘要；没有动态条目时回退到 `live_summary`。
- `items`：认知更新、反馈记录和推荐池补货等最近动态。

首启 / setup 阶段要优先保护初始化入口：当 `initialized=false`，且 `recommendation_count`、`pool_available_count`、`pool_pending_count`、`last_replenished_count`、`last_discovered_count` 都为 0 时，普通 `/api/events` 会以 `not_initialized` 拒收，不会写入 memory 或制造新的 `pending_signal_events`；`live_summary` 也会提示用户点击「开始初始化」，不会因为历史残留 pending signal 显示“已记下 N 个新动作”。一旦已有推荐或候选池产物，上述 pending signal 文案会按初始化后的正常运行状态展示。这里的 `pending_signal_events` 是 discovery refresh 触发水位，不是画像待处理队列；画像增量由 app-owned event scheduler 在 HTTP commit+wake 之后异步运行 generic/content-feedback 两个 durable consumer，buffer 与 cursor 在同一 pipeline snapshot 中提交，再由 pipeline / cognition cycle 按各自节奏更新。事件入口不会同步执行补货，只通过 `request_replenishment(reason="event_ingest")` 排队，交给定时 tick 或用户刷新后的低库存检查统一处理。

### Runtime Status Update Fields

`GET /api/runtime-status` 会保留自动更新摘要字段，供插件和 Web 前端在统一 runtime 状态对象中读取：

- `auto_update_enabled`：当前后台定时自动更新是否开启；关闭时仍允许手动检查和手动 apply。
- `install_mode`：安装形态（`frozen` / `docker` / `git` / `unsupported`）。桌面 Web 设置页在非 `git` 时禁用自动更新开关，并按形态提示升级方式（frozen → 下载新安装包，docker → `docker compose pull`）。
- `current_version`：本地后端版本。
- `latest_remote_version`：最近一次检查得到的后端远端版本。
- `last_update_check_at`：最近一次检查时间。
- `last_update_error`：最近一次检查或 apply 的稳定错误原因。
- `backend_update_state` / `backend_update_reason`：更新状态和原因，语义与 `/api/update-status.backend.state/reason` 对齐。

### RuntimeEventHub

`RuntimeEventHub.publish(event)` 会把事件 fan-out 到当前 `/api/runtime-stream` 订阅者队列，并返回布尔值：

- `True`：至少一个订阅者队列接收了事件。
- `False`：当前没有订阅者，或所有订阅者队列都未接收事件。

`ContinuousRefreshController._publish_probe_if_available()` 使用这个返回值保护主动探针：只有 `interest.probe` 或 `avoidance.probe` 实际进入至少一个 runtime stream 后，才会把本次 domain / axis / probe distance 写入 `discovery_runtime.json` 的短期去重状态，并更新 `last_probe_kind`。这些写入走 `MemoryManager.update_discovery_runtime_state()` 的原子读改写，和 API 反馈历史、短期探索 buffer 合并，避免后台循环用旧状态覆盖用户刚点击过的探针反馈。普通状态事件仍可忽略返回值。

主动探针仲裁规则：

- 每轮 proactive push 最多发布一条 probe；惊喜推荐仍走独立 `delight.candidate` 逻辑。单条 pending、批量 rehydrate 与 runtime 事件统一透传 canonical `item_key`、raw `content_id`、`source_platform`、`content_url`、`content_type`。
- 正向和负向都有候选时，根据上一次成功投递的 `last_probe_kind` 反向优先，形成 `interest -> avoidance -> interest` 的轮转。
- 发布失败（例如没有订阅者）时不写 `last_probe_kind`，也不消耗 `probed_domains` / `probed_avoidance_domains`。
- runtime 只会投递 `status="active"` 的正向/负向探针；已经确认、拒绝或过期的旧候选即使仍残留在某次内存快照中，也不会再次进入 `interest.probe` / `avoidance.probe` 事件流。
- `interest.probe` 正向探针还会记录 `probed_distance_bands`，并在下一次选择时优先尝试没在冷却窗口内问过的 `near/lateral/bridge/wildcard` 档位。
- `interest.probe` runtime event 暴露 `probe_mode` 和 `challenge`，移动 Web、桌面 Web、插件 inbox 与 OpenClaw 都可以把挑战探针和普通确认区分开；`near` 普通池最多 5 条，`lateral/bridge/wildcard` 挑战池另有 3 条 active 额度。
- `avoidance.probe` 选取会避开近期 `probed_avoidance_domains` / `probed_avoidance_axes`，并读取 `avoidance_probe_feedback_history` 中用户否认过的方向。

### Extension E2E API

`POST /api/extension/e2e/run` 是本机 trusted-local 调试端点，用来验证已安装扩展的真实捕捉链路。它不会直接写事件，也不会让后端伪造采集结果；后端只发布一次 `extension_e2e_run` runtime event，并等待扩展回传执行结果。

典型响应字段：

- `run_id`：本轮运行 ID，贯穿 runtime event、插件 result 和后端匹配。
- `token`：一次性结果回传 token，仅用于 `/api/extension/e2e/result` 鉴权。
- `observed`：后端在运行窗口内从 `events` 表匹配到的真实捕捉事件。
- `matched`：`observed` 是否满足本轮平台 / 动作要求。

约束：

- 端点只允许可信本机调用；局域网或远程请求会被拒绝。
- 同一后端进程一次只允许一个 E2E run，避免多个真实浏览器标签页互相污染匹配窗口。
- 如果 `RuntimeEventHub.publish()` 返回 `False`，端点会快速失败为 `extension_runtime_unavailable`，不空等超时。
- 默认禁止会改变平台状态的动作；调用方必须显式设置 `allow_state_changing=true` 才能执行 `like/favorite/follow/comment/repost` 这类操作。

### Image Proxy API

`GET /api/image-proxy?url=<encoded_url>` 只代理明确白名单内的 HTTP(S) 图片 URL，用于移动 Web `/m/` 和浏览器插件 side panel 的推荐、惊喜推荐和消息封面图。白名单按域名边界匹配，当前包含 `hdslb.com`、`xhscdn.com`、`pstatp.com`、`douyinpic.com`、`douyinvod.com`、`ytimg.com`、`ggpht.com` 和微博图片 CDN `sinaimg.cn`，会拒绝非 HTTP(S)、缺 hostname、userinfo、非白名单域名及 `evilsinaimg.cn` 一类后缀伪装。真实 `wx*.sinaimg.cn` 在浏览器 UA 下要求微博 Referer；抓取器只对当前目标 host 为 `sinaimg.cn` 或其子域的请求附 `Referer: https://weibo.com/`，并在每一跳 redirect 后重新计算，因而不会把该头转发给其它白名单 CDN。

代理不使用自动跳转；`301/302/303/307/308` 最多手动跟随 3 次，每一跳都会重新校验目标 URL。上游响应必须是 2xx 且 `Content-Type` 为 `image/*`。若 `Content-Length` 超过 10MB 会立即返回 413；缺失或伪造长度时，响应体会先流式写入 `SpooledTemporaryFile(max_size=1MB)`，实际读取超过 10MB 同样返回 413，避免在下游响应头已发送后才发现超限。

成功响应会带 `Cache-Control: public, max-age=86400` 和 `X-Content-Type-Options: nosniff`，并写入本地图片缓存。缓存回退只用于上游网络失败、超时或 5xx 类上游错误；URL / redirect 白名单失败、非图片 Content-Type、超过 10MB 等校验类错误会保留 403 / 400 / 413 等明确状态，不会被统一折叠成 502。该接口按本地单用户后端设计，默认只应暴露在 `127.0.0.1` 或用户可信局域网；若用 `--host 0.0.0.0` 对外监听，应在反向代理层自行加访问控制。

### Boot Autostart API

```python
from openbiliclaw.runtime import autostart

state = autostart.status()
autostart.register(config)
autostart.unregister()
warning = autostart.reconcile(config)
```

核心对象：

- `AutostartStatus(supported, registered, platform, mechanism, reason, detail)`：API、CLI 和插件 UI 共享的状态模型。`mechanism` 固定为 `launchd` / `windows_run` / `xdg_autostart` / `none`。
- `build_launch_spec(config)`：生成登录项执行命令，固定为当前 Python 解释器执行 `-m openbiliclaw.cli start`，并注入 `OPENBILICLAW_PROJECT_ROOT`；如果能找到 `ollama`，会把其目录加入登录项 `PATH`。
- `active_env_managed_inputs(config)`：检测会在桌面登录会话里丢失的环境变量来源（`OPENBILICLAW_*`、provider API key env、抖音 Cookie env），用于拒绝开启自启动。
- `autostart_shadowed(intended)`：写后 reload effective config，检测 `config.local.toml` 或环境变量是否覆盖了 `[autostart].enabled`。
- `reconcile(config)`：让配置 intent 与当前用户 OS 登录项对账；`enabled=false` 时移除残留项，`enabled=true` 且登录项缺失时在 env guard 通过后补注册。CLI daemon 与冻结桌面入口使用同一实现，失败返回可记录的警告而不阻断启动。

公开接口：

- `GET /api/autostart-status`：远程可读、降级模式可读，返回固定字段集；只展示 `enabled`、`registered`、`supported`、`can_manage`、`reason` 等状态，不包含 Cookie / API Key 等敏感配置。`enabled=false + registered=true` 会明确返回“系统自启动残留项”，供设置页提供一键关闭清理。
- `POST /api/autostart/apply {"enabled": bool}`：本机 trusted-local 可写；非本机返回 `403 local_only`，不支持平台返回 `409 unsupported_*`，env / shadow 命中返回 `409`。开启时先写 config 后注册 OS，关闭时先注销 OS 后写 config，并在失败时尽量回滚 OS 与 config 到操作前状态。

平台实现都只写当前用户作用域：

- macOS：`~/Library/LaunchAgents/com.openbiliclaw.daemon.plist`，不执行 `launchctl bootstrap`，下次登录由 launchd 读取。
- Windows：`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`。源码安装写 `pythonw.exe + data/autostart/openbiliclaw-autostart.pyw`；PyInstaller 冻结桌面包只写 `OpenBiliClaw.exe`。旧版 `"OpenBiliClaw.exe" "...autostart.pyw"` 即使脚本已丢失仍按有效启动项识别，保证状态与清理不会假阴性。
- Linux：`~/.config/autostart/openbiliclaw.desktop`，使用 XDG autostart。

Windows 回归不是只 mock `winreg`：`tests/test_windows_autostart_e2e.py` 在
`windows-latest` 使用真实 `HKCU\...\Run`，编译并启动一个真实 PE 来证明缺失
`.pyw` 时首个 exe 仍会运行，并覆盖直注册 / 注销、旧双路径迁移、关闭状态无条件
清理损坏项。安装包流水线还会把 `OPENBILICLAW_FROZEN_EXE` 指向刚构建的真实
`dist\OpenBiliClaw\OpenBiliClaw.exe`，以 self-test 模式执行“开启注册 → 旧项升级
→ 关闭清理”完整生命周期；fixture 会备份并恢复 runner 原有同名 Run 值。

#### 封面磁盘缓存与清理

成功抓取的封面以 `sha256(归一化 URL)` 为键写入 `data/image-cache/`（键与清理逻辑集中在 `openbiliclaw.runtime.image_cache`，由 `api.app` 复用，保证单一真源）。小红书 `sns-webpic-qc.xhscdn.com/{timestamp}/{token}/{path}` 这类带轮换 token 的 URL 会先剥掉 `{timestamp}/{token}` 前缀再算键，因此 token 过期重新生成后仍命中同一份缓存——这是小红书封面在签名失效后仍能展示的关键。

`cleanup_image_cache` 负责按消费状态清理：启动时全量执行一次，运行时由 `RefreshRuntime._loop_image_cache_cleanup` 每 6 小时增量执行。清理规则为「已消费且未保存」——`content_cache.pool_status` 属于 `shown / feedbacked / stale / purged_by_dislike`、且 bvid 不在 `favorites` / `watch_later`（经 `Database.iter_cover_lifecycle` 联表判定）的封面会被删除；`fresh` / `suppressed`（待展示 / 可能复活）以及任一被收藏或加入稍后再看的封面始终保留。B 站等 URL 稳定、可随时重抓的来源安全释放空间（实测可回收数百 MB）；而带过期 token、删除后无法重抓的小红书封面默认受保护不删（缓存是其唯一副本），可用 `protect_unrefetchable=False` 关闭。无任何 `content_cache` 行引用、且文件超过 30 天的孤儿封面会作为增长上限兜底被移除（降级模式下数据库不可用时仅执行这条规则）。

#### 发现即缓存（封面预取）

白名单 / redirect / 大小 / 类型校验的抓取核心 `fetch_cover_bytes` 是唯一真源；失败抛 `CoverFetchError`（携带 400/403/413/502/504），proxy 路由再映射回对应 HTTP 状态。v0.3.153+：抓取按主机分流代理——国内 CDN（hdslb / xhscdn / pstatp / douyinpic / douyinvod / sinaimg）恒直连（`trust_env=False`，代理出口 IP 易被风控，与 B站 登录探测同因），境外 CDN（ytimg / ggpht）保持继承环境 / 系统代理，需要代理才能拉 YouTube 封面的用户不受影响。`get_or_fetch_cover_bytes` 是多模态 discovery evaluator 的兼容缓存优先入口；磁盘读写同样卸载到线程，因此小红书已缓存头图即使原 CDN token 过期，也能继续参与封面图评估。

API daemon 中，proxy miss 与 refresh prefetch 进一步共用 app-owned `ImageFetchCoordinator`。Condition gate 保证总 upstream active≤4、background≤3；前台请求可占保留槽，任一前台排队时 background 不得抢刚释放的槽。按 `image_cache_key(url)` singleflight，所有 waiter `shield` shared task；一个 HTTP request 取消不会取消其它 waiter/owned upstream。前台加入尚未启动的 background 同 key 时会把它提升为 foreground，并用前台携带的更新签名 URL 抓取。cache hit 在 gate 外；同步 glob/read/write 用 `asyncio.to_thread`，落盘使用同目录临时文件 `flush+fsync+os.replace`，所以观察者只会看到旧文件或完整新文件。

`RefreshRuntime._loop_cover_prefetch` 每 60 秒做一次「发现即缓存」：在线程中从 `Database.iter_servable_cover_urls` 取最近 12 小时内、仍可展示（`fresh / shown / suppressed` 或已保存）的封面（最新优先），`select_prefetch_targets` 按 cache key 去重、过滤非白名单和已缓存项、把**无法重抓的小红书封面排在最前**，每轮最多提交 40 张到共享 background lane，最多 3 张并行。这修复了此前封面只在「展示时」才懒加载、而小红书签名 token 早已过期导致 502 破图的问题——预取趁 token 新鲜时就把图落盘；最近窗口也避免对 token 已死的旧内容反复重试。预取按 `content_cache.cover_url` 原始值（可能是 `//` 或 `http://`）归一化后再抓，落盘 key 与 proxy 查找一致，故预取的封面 proxy 能直接命中。热重载后新 controller 在恢复 loop 前重绑同一 app-owned coordinator；关闭时先停 refresh producer，再取消 active/queued owned task。

#### 小红书封面：扩展抓取时采集 URL 与字节（2026-07「没头图」修复）

2026-07 用户实报「小红书内容都没头图」，日志复盘定位到两个叠加缺陷：**(1) 后台标签页懒加载图片永不升级**——搜索/创作者任务在后台标签页刮取，卡片 `<img>` 永远停在内联 `data:` 占位符上，DOM 提取拿不到真实封面 URL（受影响后端对 hdslb / douyinpic 抓取全部正常、却从未尝试过一次 xhscdn 抓取，即它手里从没有过可抓的 URL）；(2) 即使有 URL，服务端预取也是一场与轮换 token 过期、与本机到 CDN 出网状况的赛跑。

修复（`extension/src/content/xhs/cover-harvest.ts`）：任务执行器先用 `backfillCoverUrlsFromState` 从 `__INITIAL_STATE__` 做**形状无关**的深扫（循环安全、深度/节点数有界，按 note_id 命中对象后取 cover 路径），把占位符/空 cover_url 回填成真实 CDN URL；DOM 提取（passive 与任务两路）一律拒收 `data:`/`blob:` 占位符。随后 `attachCoverData` 在页面上下文抓封面字节（此刻 token 最新鲜、走用户自己的浏览器会话），转 base64 挂 `cover_data`/`cover_content_type` 随既有 observed-urls / 任务结果通道上报；后端 `_cache_xhs_notes` 将非 http(s) 的 cover_url 归一为空，并经 `save_extension_cover`（白名单 / `image/*` / 1MB 上限 / base64 校验，坏封面绝不阻断笔记入库）把字节写入同一 `data/image-cache/`——缓存 key 本就剥离轮换 token，serve 零改动全走缓存命中。已知候选去重跳过的笔记也会先存封面，用户重新刷到旧笔记时可就地治愈无封面的存量行。服务端预取继续保留（对拿得到 URL、出网正常的环境仍是第二道保险）。

同期补齐了封面链路的可观测性（此前 `image_cache` 模块零日志，这次定位只能靠「慢取日志里 xhscdn 完全缺席、其它 CN CDN 都在」反推）：`fetch_cover_bytes` 的上游非 2xx / 超时 / 网络错误按 host 限频打 WARNING（首次立即、之后每 host 每 10 分钟至多一条并携带抑制计数），错误 detail 携带真实上游状态码；`/api/image-proxy` 的失败/慢 miss DEBUG 与扩展 cover reject 都只记录 host + cache hash 前缀 + 安全错误类别，不记录签名 path/query 或可能回显 URL 的 `httpx` exception repr；`_cache_xhs_notes` 每批打 INFO 汇报缓存的扩展采集封面数。`/api/runtime-status` 另公开 active/waiting/inflight、upstream started、singleflight joins 与 total/background 峰值，全部是整数。

### AccountSyncService

```python
from openbiliclaw.runtime.account_sync import AccountSyncService

service = AccountSyncService(
    memory_manager=memory,
    bilibili_client=bilibili_client,
    soul_engine=soul_engine,
    database=database,      # 可选：跨源去重
    x_client=x_client,      # 可选：X 定时增量（resolve_x_cookie 有 cookie 时装配）
    x_health_store=x_health_store,  # 可选：与 X discovery 共用的退避/登录健康状态
    profile_analysis_timeout_seconds=360.0,
)
result = await service.sync_now()
status = service.get_runtime_status()
# status["last_account_sync_issues"] ==
# [{"stage": "bilibili_favorites", "kind": "api_error"}]
# status["last_account_sync_message"] ==
# "本轮账号同步：B 站收藏夹未同步（B 站接口返回异常）。..."
```

`sync_now()` 会拉取最近一批 B 站历史、收藏夹和关注列表，只有新增信号会进入 durable event ingress 与画像更新：

- 历史记录：使用 `last_history_view_at`、`last_history_bvid` 和 `history_bvids_at_last_view_at` 跳过已经处理过的同秒历史项。
- 收藏夹：使用稳定排序后的 `favorite_signature` 和 `favorite_bvids`，签名变化时只导入新增 bvid。扫描上限对齐 init（`max_folders=200`），并带 `max_total_items=500` 预算封顶请求数（最坏约 26 次请求），覆盖第 11 个及以后收藏夹的新收藏。
- 关注列表：使用 `following_signature` 和 `following_mids`，签名变化时只导入新增 mid。改为翻页循环（`page_size=100`，页满续翻，短页停，硬上限 `following_max_pages=5` → 500 人），覆盖第 2 页及以后的新关注；中途某页失败保留已拉页面、记 `_record_stage_error`（auth-expired 优先级不变）、照常打时间戳。

**跨源去重**：注入 `database` 后，构造事件前会经 `_dedup_cross_source` 过滤——48 小时窗口（`_CROSS_SOURCE_DEDUP_WINDOW_HOURS`）内 events 表已有同键事件（view/favorite 按 bvid、follow 按 mid、X 按 tweet ID）则跳过，避免扩展实时上报与账号拉取对同一次行为双写双计。查询恒带 `exclude_source="account_sync"`：自己上一轮写的行不参与压制，只被历史 API 观察到的窗口内重看仍会正常发事件。游标照常前进，去重不阻塞水位线；丢弃数量记 INFO 日志。

**画像更新路径**：composition root 注入 `event_ingress` 后，所有新增事实先经 `EventIngressService.accept_batch(producer="account-sync")` 一批提交；每项的稳定 digest 在入口处加 producer namespace，重放返回 durable receipt。画像已就绪时写入 `profile_update_owner="generic"`，后续由 app-owned generic cursor consumer 通过 `checkpointed_enqueue_batch()` 推进，不在账号同步请求内直调 pipeline/LLM。画像未就绪或 readiness 不可判定时，事件仍先落 durable ingress，再走兼容 `analyze_events` 路径；只有明确未就绪才尝试首次 bootstrap。未注入 ingress 的第三方旧 embedder 才回退逐条 `memory.propagate_event()`。

| 条件 | 动作 |
| --- | --- |
| ingress 已注入 + 画像就绪 | durable batch commit + generic owner；HTTP/sync 返回不等待画像学习 |
| ingress 已注入 + 画像未就绪 | durable batch commit + `analyze_events` + `_auto_bootstrap_soul_profile` |
| ingress 已注入 + readiness 缺失 / 抛错 | durable batch commit + `analyze_events`，保守跳过 bootstrap |
| 未注入 ingress（兼容 embedder） | 逐条 `propagate_event()` 后按同一 readiness 规则走 legacy analyzer |

**错误可见化**：每个拉取阶段的异常都会 `logger.warning`，同时写入两个兼容层级。旧字段 `last_sync_error_kind` 继续保留整轮最高优先级摘要（`auth_expired` / `x_auth_expired` / `x_blocked` / `x_rate_limited` / `error` / 空）；新字段 `last_sync_issues` 保存最多 8 个去重后的稳定 `{stage,kind}`，stage 覆盖 `bilibili_history` / `bilibili_favorites` / `bilibili_following`、`x_preferences` / `x_likes` / `x_bookmarks` 与 `profile_analysis`。B 站阶段可区分 `auth_expired` / `rate_limited` / `timeout` / `network` / `api_error` / `unexpected_error`，X 保留平台专属的登录、403 与限流分类，画像分析则经 `classify_llm_failure_kind` 与安全诊断区分 `no_provider` / `model_not_found` / `auth_failed` / `quota_exhausted` / `rate_limited` / `timeout` / `connection` / `ssl` / `server_error` / `invalid_response` / `moderation` / `unexpected_error`。

`get_runtime_status()` 经 `/api/runtime-status` 下发 `last_account_sync_error_kind`、`last_account_sync_issues`、仅供诊断的原始 `last_account_sync_error`，以及后端统一计算的 `last_account_sync_message` / `last_account_sync_severity`。多阶段失败会按「域 + 原因」合并成逐项中文说明；全部属于登录生命周期或限流时为 warning，混入网络、接口、画像配置等故障时为 error。文案只在确实可自行恢复时承诺下一轮重试，需用户修复 Cookie、API Key、模型名或连接时给出对应动作；X-only 问题明确说明 B 站等来源不受影响。旧状态文件没有 issue 列表时继续回退旧 kind/detail 文案，畸形或未知 issue 会在 MemoryManager 与 runtime 边界归一化。桌面 Web 消费这些后端成品字段选择 warning/error 样式；扩展 popup / 移动 Web / CLI 目前不展示此横幅。

桌面首页同时读取本地-only `/api/sources/status`，把它明确命名为「来源接入」后与上面的「账号同步」组合展示：九个平台中已启用来源的缺凭据、不完整、过期、失败、受阻、限流或未知状态会点名平台并原样显示后端 `detail`；正常的 `unverified` / `syncing` 不进入故障横幅。这样扩展心跳、公开源与命令行凭据状态都可定位，但不会被冒充为 `AccountSyncService` 的同步阶段。

**X 定时增量**：仅在 `[sources.twitter].enabled=true` 且 `sources.x_auth.resolve_x_cookie` 解析到 Cookie 时，两处装配点才构造 `XClient`；同一 6h 周期内在 B 站各阶段之后拉取 likes / bookmarks（各上限 200，`_X_FETCH_LIMIT`），分别映射为 `like` / `favorite` 事件（`source_platform="twitter"`）。去重用状态集合 `x_like_ids` / `x_bookmark_ids`（归一化 tweet ID，取 URL 的 `/status/<id>` 尾段，兼容 `x.com/i/status/<id>` 与 `x.com/<handle>/status/<id>` 两种形态，集合上限 2000 保最新）；集合为空的首轮从 events 表已持久化的 X 事件播种，init 之后、首轮之前新增的 like 仍会正常发事件。API runtime 把 discovery producer 的同一 `XSourceHealthStore` 注入账号同步；OpenClaw 构造与 Cookie 指纹绑定的同类 store。每轮出网前先查 `is_ready()`，已在 429 cooldown、缺/过期 Cookie 或 403 block 时直接跳过；likes 首个失败会 `record_error()` 并在 store 变为不可用后取消 bookmarks，成功则 `record_success()`。X 子路径失败只记入 errors + WARN，不影响 B 站同步，反之亦然。

手动 `openbiliclaw fetch-x` 使用同一健康表：每个实际执行的 likes/bookmarks 请求分别 `record_success(strategy=...)` 或 `record_error(...)`，并用当前真实发请求 Cookie 的指纹绑定证据。`--dry-run` 只禁止 memory 写入，不禁止这份请求健康证据；因此一次真实成功 smoke 后 `/api/sources/status` 可以立即显示 X 已由「请求反馈」验证，而不是必须等待 daemon 的下一轮 discovery。

daemon 的 `sync_if_due()` 还受共享 `background_llm_work_allowed()` 约束：完整画像尚未生成时不会开始首次 account-sync，因此不会在 `/setup` 点击「开始初始化」之前先写入、分析同一批 B 站 bootstrap 历史；画像落盘且 guided init 终态后才恢复增量同步。显式运维调用 `sync_now()` 仍保留原语义。

`analyze_events()` 失败时，`sync_now()` 会把安全原因写入 `last_sync_error`（`画像分析失败：<原因>`，供 `/api/init-status` 与账号同步状态读取），并细分模型未配置 / 不存在 / 鉴权失败 / 额度用尽 / 限流 / 超时 / 连接失败 / SSL 证书失败 / 服务器错误 / 无效响应 / 内容合规拒绝；无法识别则显式记为 `profile_analysis_error + unexpected_error`。它**不推进任何游标、不打 `last_account_sync_at` 时间戳**——整个 tick 回滚，下一次允许执行的 `sync_if_due` tick 重试同一批事件，也不会被 `sync_interval_hours` 节流锁死，随后重新抛出交给 `run_forever` 分类记日志；若画像之前已有来源拉取失败，fresh-state 写入仍保留本轮那些 issue，横幅不会只剩最后一个画像错误。Issue #113 收口后，`profile_analysis_timeout_seconds` 默认 360 秒（受控调用可传 `<=0` 关闭），到期会取消 Soul/provider coroutine，并写 `profile_analysis_timeout + {stage:"profile_analysis", kind:"timeout"}` 与固定安全排查 detail；`GET /api/init-status` 已真正消费这条错误，三端会优先显示 detail。外部 `CancelledError` 继承自 `BaseException`，不会被失败捕获，因此热重载 / 重启打断的取消语义不变。

### DouyinDiscoveryProducer

`DouyinDiscoveryProducer.produce_if_due()` 是 daemon 与 `openbiliclaw discover --source douyin` 共用的正式入口。手动 CLI 构造时传 `enabled_override=True`，只绕过后台 scheduler 总开关；来源 enabled/mode、最小间隔、每日预算和候选池上限仍照常执行。producer 通过 `KeywordFetchCoordinator` claim 统一关键词，用 `DouyinDiscoveryService(cache=False, evaluate=False)` 拉 raw candidates，再交 `DiscoveryCandidatePipeline` 写入 `discovery_candidates(pending_eval)`。

API runtime 会把共享 `PresenceTracker` 和 `extension_disconnect_grace_seconds` 注入 producer；插件不在线且不在宽限期时直接返回 `extension_absent`，不 claim 关键词、不创建 `dy_tasks`、也不推进执行时间。显式 CLI / debug 构造保持 `presence=None`，允许用户主动发起一次真实 smoke。

每次实际尝试都会写进程内 cadence：真实空结果按配置的 `min_interval_minutes` 节流，timeout / infrastructure error 至少退避 15 分钟，预算耗尽至少退避 60 分钟；有候选的 productive 轮次仍写共享 producer ledger，使最小间隔跨重启生效。这样 extension 离线、任务超时或空页面不会在 candidate coordinator 的每个补货 tick 上形成任务风暴。

search / hot / feed 每条分支都有结构化终态 `used / empty / timeout / failed / budget_exhausted`。search 按关键词分别结算：有候选才 `mark_used`，真实空结果 `mark_failed`，插件 timeout / failed 调 `requeue_transient()` 无损退回 pending 且不增加 attempts，预算耗尽的当前词与未执行词 rollback；前序已成功候选不会被整轮异常丢弃。统一关键词池为空时不再跳过整轮，search 可回退画像兴趣词，hot / feed 仍独立执行。大缺口固定包含 search，并在 hot / feed 间逐轮轮换，避免 feed 长期饥饿；小缺口仍优先 feed / hot。

API daemon 构造 producer 时会注入共享 extension presence：扩展离线且超过 `extension_disconnect_grace_seconds` 后，`produce_if_due()` 在创建浏览器任务前直接返回 `extension_absent`，不会先入队再等待 180 秒；显式 CLI / smoke 未注入 presence，仍可用于人工诊断。每次真正尝试都会记录进程内 cadence：真实空结果遵循来源 `min_interval_minutes`，`timeout / failed` 使用 15 分钟基础设施退避，预算耗尽使用 60 分钟退避；有产出的轮次仍写共享 productive ledger。该分层同时避免离线 pending 堆积和在线故障时的分钟级重试风暴。

### YoutubeDiscoveryProducer

```python
from openbiliclaw.runtime.youtube_producer import YoutubeDiscoveryProducer

result = await producer.produce_if_due(limit=20)
```

`produce_if_due()` 返回 `{"discovered": int, "reason": str, ...}`。注入 `DiscoveryCandidatePipeline` 时，`discovered` 表示本轮已入待评估池或已被 drain 处理的候选量；未注入时沿用直接 `ContentDiscoveryEngine.discover()` 缓存路径。常见 `reason`：

- `ok`：至少完成了一轮可运行策略；结果已通过候选 pipeline 或直接 discovery 路径进入统一评估 / 缓存链路。
- `throttled`：距离上次执行未达到 `min_interval_minutes`。
- `budget_exhausted`：当天 `yt_search` / `yt_trending` / `yt_channel` 的执行 ledger 已耗尽。
- `disabled` / `no_profile` / `error`：分别表示配置关闭、画像不可用或所有策略失败。

### XDiscoveryProducer

```python
from openbiliclaw.runtime.x_producer import XDiscoveryProducer

result = await producer.produce_if_due(limit=20)
```

X (Twitter) 的 steady-state discovery 走服务端 cookie 重放（对标抖音 direct，但用 `twitter-cli` 取代 XBogus 签名）。`produce_if_due()` 在 `[sources.twitter].enabled=true`、X 平台族低于 quota、源健康就绪、距上次执行已过 `min_interval_minutes` 时，依次跑三个策略：

- `search`：从 Soul 画像生成关键词，调 `XClient.search()`。
- `feed`：拉推荐流 For-You（`XClient.for_you()`）。这是最高曝光、最易被注意的行为，被压到很低的每日频次，并在连续失败后由 `XSourceHealthStore.feed_allowed()` 自动暂停。
- `creator`：对 `x_creator_subscriptions` 里到期的订阅逐个调 `XClient.user_tweets(handle)`，按 `creator_refresh_hours` 控制刷新节奏。

每条推文经 `discovery.x_normalize.normalize_tweet()` 映射为 `DiscoveredContent`（`content_type ∈ {tweet, thread}`、`body_text` 带全文），API runtime 通过共享 `DiscoveryCandidatePipeline.enqueue_candidates()` 写入 `discovery_candidates` 待评估池；pipeline 的单次 callback 会立即唤醒 coordinator，不再等 60 秒 safety wake，也不会双重通知。producer **只 fetch，不写 `content_cache`、不调评估器**，由共享混源 evaluator 完成 admission；脱离 API 的 isolated caller 仍可使用 direct-database fallback。runtime 的平台族统计会把 `x` / `x-*` / `twitter` 归一到 `twitter`，避免 X 配额、过滤 tab 和 pool 状态被拆成不同来源。每个策略 run 都把成功 / 失败结果回写 `XSourceHealthStore`（成功 `record_success()`，失败 `record_error(exc)` 按 401/403/429 落对应健康态）。预算护栏：`daily_search_budget` / `daily_feed_budget` / `daily_creator_budget`（`0` = 不设上限）+ 两次请求间 `request_interval_seconds` 间隔。`enabled=false` 时整条路径 no-op，绝不 import `twitter_cli` / `curl_cffi`。

X 客户端 `XClient`（`sources/x_client.py`）封装默认运行时依赖 `twitter-cli`，全程只读，方法用 `asyncio.to_thread` 包成 async；底层 `TwitterAPIError` / `AuthenticationError` 映射为 `XMissingCookieError` / `XAuthError`(401) / `XBlockedError`(403) / `XRateLimitError`(429)，供源健康状态机分流退避。`openbiliclaw[x]` 仍保留为兼容旧脚本的安装别名。

### RedditDiscoveryProducer

```python
from openbiliclaw.runtime.reddit_producer import RedditDiscoveryProducer

result = await producer.produce_if_due(limit=20)
```

Reddit 的 steady-state discovery 默认走 `rdt-cli` 登录态命令后端。`produce_if_due()` 在 `[sources.reddit].enabled=true`、Reddit 平台族低于 quota、距上次执行已过 `min_interval_minutes` 时，按 `[sources.reddit].source_modes` 调度四类分支：

- `search`：优先 claim 统一关键词 store；关键词池为空时回退 Soul 画像兴趣。
- `hot`：默认拉 `r/all` 的热门内容，也可由 smoke 命令传指定 subreddit。
- `subreddit`：优先复用近期 Reddit 结果里的 subreddit；没有历史种子时回退画像兴趣。
- `related`：优先复用近期 Reddit 内容 URL 或同轮 search / hot / subreddit 结果作相关扩展。

默认 `backend="rdt"`：producer 先检查 `rdt-cli` 命令和 `~/.config/rdt-cli/credential.json`，避免状态探测隐式触发浏览器 Cookie 提取；已连接插件会通过 `/api/sources/reddit/cookie` 把 `reddit_session` 写入该 credential store，凭据存在时再跑 `rdt status --json`，并用 `rdt search --json` / `rdt all --json` / `rdt sub <name> --json` / `rdt read <id> --json` 拉取候选。显式 `backend="extension"`，或命令后端状态不是 `ready` 且后端可写入 `reddit_tasks` 时，后端会改入队插件任务，唤醒真实 `reddit.com` 登录态 tab 并通过同源 `.json` endpoint 读取 posts / comments，再 POST `/api/sources/reddit/task-result` 回写。init 期 `bootstrap_events` 仍固定使用插件读取 saved / upvoted / subscribed。每条内容经 `reddit_items_to_contents()` 映射为 `DiscoveredContent(source_platform="reddit", source_strategy="reddit-<mode>")`，posts / comments 会保留 `body_text` 与 `content_type ∈ {"post", "comment"}`，前端因此按无封面文字卡展示。

`rdt/opencli` 的状态探测与实际抓取都是同步命令边界，`produce_if_due()` 必须用 `asyncio.to_thread()` 执行两者。真实初始化完成后后台任务恢复时，`rdt status` / 抓取可能等待 15 秒或更久；若直接在 API loop 执行，会让 `/api/ping`、`/api/init-status` 和 runtime WebSocket 同时无响应，看起来像初始化终态锁死。worker thread 只隔离等待，不改变命令超时、预算、fallback 或入池语义。

producer **只 fetch，不写 `content_cache`、不同步调用 evaluator**。注入 `DiscoveryCandidatePipeline` 时，候选只进入 `discovery_candidates(pending_eval)`，后续由共享混源 evaluator 批量评分、admission 和文案预生成。这样 `openbiliclaw discover --source reddit` 的真实插件 E2E 只验证 Reddit 取数和入池，不会被本地 LLM 评估时延拖到超时。预算护栏是 `daily_search_budget` / `daily_hot_budget` / `daily_subreddit_budget` / `daily_related_budget` 四个独立 ledger，默认每类 300；`0` 表示不设上限，负数表示禁用该分支。

### LinuxdoDiscoveryProducer

`LinuxdoDiscoveryProducer` 是 Linux.do daemon、`openbiliclaw discover --source linuxdo` 与 `openbiliclaw discover-linuxdo` 的共同入口。它本身不访问 Linux.do：每种 enabled mode 入队一条 `linuxdo_tasks` 记录，通过 `linuxdo_task_available` 唤醒扩展，等待 canonical task result，再把 topic rows 转为 `DiscoveredContent(source_platform="linuxdo", content_type="post")` 并写入 `pending_eval`。

- `search` 使用统一 `KeywordFetchCoordinator`，只有真实候选产出才 mark used；rate-limit、network、timeout、access-blocked、login-required 等临时失败会 rollback/requeue claim。
- `hot` / `feed` 无需种子；`creator` 读取最近 `author_url`，`related` 读取最近 topic URL，没有历史时用同轮已返回 row 补种子。
- daily budget、`min_interval_minutes`、候选池 source share 与 source 开关都在入队前生效；daemon 自动运行另受 scheduler 总开关约束，显式 `discover-linuxdo` / `discover --source linuxdo` 以 `enabled_override=True` 只绕过该后台总开关。`0` daily budget 表示不设每日上限。
- 扩展离线或任务超时只让本轮无产出，不会绕过扩展改成后端 Cookie replay。默认端到端总等待为 32.5 分钟：pending 最多约 3 分钟等扩展领取；进入 `in_progress` 后按任务形状给予最多约 29 分钟执行和额外 30 秒结果余量。显式 CLI/env 等待值是从入队开始计算的总硬上限，较小值可能截断已领取任务。claim lease 约 35 分钟，共享 dispatcher mutex 的 stale 窗口为 36 分钟。扩展在执行前把 task/tab/deadline 写入 session storage，service worker 重启时先恢复 runner 再启动 polling；存活任务 tab 的结果可由恢复后的 handler 回传。后端 canonical staged result 在回调响应丢失后仍可幂等重放。guided init Stage-1 基础预算为 30 分钟；默认预算下 Linux.do-only 至少给 32.5 分钟，Linux.do 与其它来源并选时给 62.5 分钟，显式 override 不扩。

真实站点请求由扩展在 `linux.do` task tab 以 `GET` + `credentials: include` 发起。生产 payload 的单请求超时默认且最多 30 秒；discovery 默认且最多 5 页，bootstrap 按每页 20 条和 limit 自动扩页（300 条为 15 页）且最多 15 页，输入列表最多 5 个、每分支最多 300 条、单响应最多 2 MiB，request interval 限 `0..30` 秒。content executor 的 120 秒 / 50 页 / 20 输入只是第二层绝对防御，合法后端任务不会触达。错误只回传 code/status/path；领取后的非法 payload 会立即终结为 `failed / invalid_task_payload`，不占满长 lease。bootstrap 部分 scope 失败时保留已采事件并终结为 `degraded`，不记为完整运行也不进入 6 小时复用；discovery 分页或多输入中途失败也保留已有 topic 并以 `degraded` 进入候选管线，零有效 item 才是 `failed`。`_t` 的布尔心跳和本 producer 正交，公开 discovery 不要求登录。详见 [Linux.do 来源文档](linuxdo.md)。
### V2EXDiscoveryProducer

```python
from openbiliclaw.runtime.v2ex_producer import V2EXDiscoveryProducer

result = await producer.produce_if_due(limit=20)
```

V2EX producer 是公开只读 discovery 的正式 runtime / CLI 入口。它按 `[sources.v2ex].source_modes` 轮转 `search / node / tab / hot / latest`，使用统一关键词 planner、Node/Tab 配置、分支预算和持久化节流。`search` 优先复用已配置 Exa / You provider 发送 `site:v2ex.com/t` 查询并用官方 Topic 详情补全；provider 无结果或失败时回退官方 latest/hot 有界匹配。PAT 只增强 API 2.0 访问，401/403 会清除本轮 PAT、对应已验证身份并继续匿名；所有 Topic 通过共享 `DiscoveryCandidatePipeline` 入 `discovery_candidates(pending_eval)`，不在 producer 内联调用 evaluator。每日预算只按共享池全局去重 / 预筛后真正保留的候选扣费，HTTP 请求、详情增强和已知重复不扣。producer 会在共享评估前用 `detail_fetch_limit` 有界补齐不完整 Topic，并仅在 PAT 可用时用 `reply_enrichment_limit` 读取 Reply 第一页生成确定性讨论摘要；`max_topic_chars` / `max_reply_digest_chars` 在 normalizer 边界裁剪，Reply 不单独入池。浏览器 bootstrap 由 `V2EXTaskQueue` 负责领取和 staged 完成，事件入口聚合用户自己的 Reply；runtime 只读取 active profile identity 的 Node Affinity，不能被刚观察到的另一账号替换。`v2ex_incremental_hours` 排队增量任务，首次完整 guided 快照种下收藏基线，后续完整 scope 由 `V2EXFavoriteSnapshotStore` 执行连续两次缺失确认并通过 durable effect 生成 retraction / restore。

### BilibiliExtensionSearchProducer

```python
from openbiliclaw.runtime.bilibili_producer import BilibiliExtensionSearchProducer

result = await producer.produce_if_due(limit=5)
```

B 站扩展搜索 producer 是 API 搜索的兜底，不是常驻主发现路径。`produce_if_due()` 只在以下条件同时满足时入队：

- `[sources.bilibili].enabled=true` 且 `[scheduler].enabled=true`。
- B 站 API search 正在进程级冷却中（`search_cooldown_remaining()>0`）。
- 浏览器扩展 presence 在线或仍处于 `extension_disconnect_grace_seconds` 宽限窗口。
- B 站平台族低于 source share quota，且 `DiscoveryCandidatePipeline.pool_full()` 为 false。
- `bili_tasks` 中近期没有 pending / in-progress / completed search 任务，避免同一冷却窗口反复打开搜索页。

统一关键词 planner 开启时，producer 会通过 `KeywordFetchCoordinator` claim B 站 regular 关键词并把 `source_keyword_id` 写进任务 payload；每轮最多把第 1 个任务标为 `order="pubdate" / discovery_lane="recent"`，并把该任务的 `page_size/limit` 收到 5，其余任务保持普通相关性排序，因此不会为同一个关键词复制任务或额外消耗 keyword claim。扩展收到 `bili_task_available` 后打开对应真实 B 站搜索页并抓渲染后的 DOM 卡片，`/api/sources/bili/task-result` 再把视频转换成 `source_platform="bilibili"`、`source_strategy="bili-extension-search"` 的 raw candidates；近期任务额外保存 `source_context="bili-extension-search:recent"` 和 raw lane provenance，然后触发同一候选 drain。terminal `ok` 会把关键词标记 used，失败或空结果标记 failed。关键词合并 prompt 复用共享画像分层缓存，画像核心和兴趣层没变时不会重新渲染前置 profile block。若 refresh 口径判断 explore 已到期 / 即将到期，且 B 站还有 real deficit，本轮 prompt 会额外带 `<explore_domains>`；返回的 domain queries 会作为探索性 B 站 pending keywords 写入 `keyword_kind="explore"` 池，供 `ExploreStrategy` claim 消费。只有实际插入了 query 才会把 runtime state 的 `last_explore_refresh_at` 推进，避免空响应浪费 explore 周期。

### Source Bootstrap Task Results

XHS / 抖音 / YouTube / 知乎 / Reddit / Linux.do 的插件任务桥保留两层去重：

- 单任务内：`merge_result()` 合并 partial / final payload 时按 scope + 平台原生 ID / URL / title 去重，只把本次新增项返回给 API 传播。XHS 只接纳任务不可变 `scopes` 白名单内的 note 与整数计数；缺失 / 空列表按扩展默认回退为 saved / liked / xhs_history。`scope_counts` 以扩展报告值和已合并 scope-aware canonical 行数两者较大值为准，但最终受 `max_items_per_scope` 约束：两个互不重叠的 partial 可在预算内从各报 `saved=5` 累积为 `saved=10`；达到上限后先到的 canonical 行优先，后续同行、计数与未被 note 接纳的裸 URL 都被裁掉。已接纳同 identity note 的发布时间与首个有效 tokenized URL 可继续补齐，不占新额度。同一笔记同时出现在 saved/liked 仍保留为两个不同强信号，各自占用所属 scope 的预算。
- 跨任务：API 在传播 bootstrap 事件前读取 `source_bootstrap_state.json`，跳过六个平台已经进入 durable event ingress 的 identity key；seen-key checkpoint 使用原子 read-modify-write，按扩展响应顺序为每源保留最新 5,000 个。这样 `fetch-*`、`init`、周期回拉或近期任务复用重复返回同一批收藏 / 历史时，不会再次插入行为事实或触发画像更新。六个平台的 final callback 都先冻结首份 canonical result，随后依次修复 event ingress、seen state 和 terminal flip；任一步失败都保持 staged/nonterminal，供租约重领后从冻结结果重放。Linux.do scope 严格映射为 bookmarks→favorite、likes→like、read_history→view。
XHS / 抖音 / YouTube / 知乎 / Reddit / V2EX 的插件任务桥保留两层去重：

- 单任务内：`merge_result()` 合并 partial / final payload 时按 scope + 平台原生 ID / URL / title 去重，只把本次新增项返回给 API 传播。XHS 只接纳任务不可变 `scopes` 白名单内的 note 与整数计数；缺失 / 空列表按扩展默认回退为 saved / liked / xhs_history。`scope_counts` 以扩展报告值和已合并 scope-aware canonical 行数两者较大值为准，但最终受 `max_items_per_scope` 约束：两个互不重叠的 partial 可在预算内从各报 `saved=5` 累积为 `saved=10`；达到上限后先到的 canonical 行优先，后续同行、计数与未被 note 接纳的裸 URL 都被裁掉。已接纳同 identity note 的发布时间与首个有效 tokenized URL 可继续补齐，不占新额度。同一笔记同时出现在 saved/liked 仍保留为两个不同强信号，各自占用所属 scope 的预算。
- 跨任务：API 在传播 bootstrap 事件前读取 `source_bootstrap_state.json`，跳过六个平台已经进入 durable event ingress 的 identity key；seen-key checkpoint 使用原子 read-modify-write，按扩展响应顺序为每源保留最新 5,000 个。这样 `fetch-*`、`init`、周期回拉或近期任务复用重复返回同一批收藏 / 历史时，不会再次插入行为事实或触发画像更新。六个平台的 final callback 都先冻结首份 canonical result，随后依次修复 event ingress、seen state 和 terminal flip；任一步失败都保持 staged/nonterminal，供租约重领后从冻结结果重放。V2EX 的 key 额外带 resolved username 前缀，后端净化任务字段、执行 identity mismatch gate，并对账号分区 Node evidence / 收藏 snapshot effect 做幂等记录。

## 配置项

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `scheduler.auto_update_enabled` | `false` | 是否启用后台自动更新检查。 |
| `scheduler.auto_update_check_interval_hours` | `6` | 自动更新检查间隔。 |
| `scheduler.auto_update_allow_prerelease` | `false` | 是否允许 `backend-vX.Y.Z-rc/beta/dev` 预发布 tag 进入候选。 |
| `scheduler.auto_update_allowed_remotes` | OpenBiliClaw GitHub HTTPS / SSH | 允许自动更新快进的 `origin` allowlist；按规范化形式比较（`.git` 后缀可选、HTTPS/SSH 拼法等价、大小写不敏感），带凭据 URL 或未匹配的 remote（含镜像包装 URL——镜像用户把镜像地址加进来即可）会被拒绝。 |
| `scheduler.enabled` | `true` | 后台 LLM / embedding 总开关。 |
| `scheduler.pause_on_extension_disconnect` | `false` | 浏览器插件断开后是否暂停后台 LLM / embedding 工作。 |
| `scheduler.extension_disconnect_grace_seconds` | `90` | 插件断开后的宽限秒数。 |
| `scheduler.pause_during_peak_hours` | `false` | 高峰时段错峰：开启后 daemon-owned 后台 LLM 调用在 `peak_hours`（北京时间）窗口内暂停，落到更便宜的空闲时段（DeepSeek 空闲半价）。交互流量（对话 / sentiment / 配置探测）永不错峰；补货在池子跌破 `peak_refill_floor` 时仍允许紧急补货，避免白天断供；维护流量（画像合并、speculation、proactive push）完全推迟。 |
| `scheduler.peak_hours` | `"09:00-12:00,14:00-18:00"` | 高峰窗口规格 `"HH:MM-HH:MM,HH:MM-HH:MM"`，北京时间（UTC+8）。默认与 DeepSeek 官方高峰一致。 |
| `scheduler.peak_refill_floor` | `30` | 高峰时段补货的紧急水位：servable 池子可用数等于或低于该值时补货绕过高峰暂停照常执行；`0` 表示高峰完全禁止补货。 |
| `scheduler.refresh_check_interval_seconds` | `60` | `ContinuousRefreshController` 主循环轮询间隔。 |
| `scheduler.signal_event_threshold` | `6` | 累计多少条 discovery-trigger 新行为事件后触发 `search + related_chain`；该计数只表示 discovery refresh 水位，不表示画像待处理队列。 |
| `scheduler.trending_refresh_minutes` | `3` | `trending` 策略最小刷新间隔（分钟）。v0.3.186 起单位由小时改为分钟；旧键 `trending_refresh_hours` 读取时按 ×60 换算。 |
| `scheduler.explore_refresh_minutes` | `3` | `explore` 策略最小刷新间隔；统一关键词 planner 会复用这条 refresh plan 时钟，在到期或距到期不足一个 `refresh_check_interval_seconds` 且 B 站有补货空间时，把探索 query 生成合并进当轮关键词调用。 |
| `scheduler.discovery_limit` | `30` | 单轮 discovery wave 候选上限，最大 `60`。 |
| `scheduler.delight_queue_limit` | `20` | 惊喜推荐队列默认加载数量；桌面 Web、移动 Web 和浏览器插件默认共享，范围 `1..100`。 |
| `scheduler.proactive_push_interval_seconds` | `120` | 主动推荐 / probe 推送循环间隔。 |
| `scheduler.speculator_idle_interval_minutes` | `30` | 画像 pipeline 空闲时检查猜测兴趣生命周期的间隔。 |
| `scheduler.avoidance_speculation_interval_minutes` | `10` | 不喜欢领域探针生成间隔。 |
| `scheduler.avoidance_speculation_ttl_days` | `3` | 不喜欢领域探针存活天数。 |
| `scheduler.avoidance_speculation_cooldown_days` | `7` | 不喜欢领域探针被否认或过期后的冷却天数。 |
| `scheduler.avoidance_speculation_confirmation_threshold` | `3` | 自动确认不喜欢领域所需显式负向信号数。 |
| `scheduler.avoidance_speculation_max_active` | `5` | 最多同时活跃的不喜欢领域探针数。 |
| `autostart.enabled` | `false` | 是否期望登录系统后自动拉起 `openbiliclaw start`。 |
| `autostart.manage_ollama` | `true` | `start` 是否在需要本机默认 Ollama 时尝试后台拉起 `ollama serve`。 |

## 设计决策

### Auto-update release contract

后端自动更新只认 backend source tag：

- backend 源码更新发布为 git tag：`backend-vX.Y.Z`，这是唯一 canonical 后端 tag。
- legacy 安装仍 fallback 兼容 `vX.Y.Z` 和裸 semver `X.Y.Z`，但只在没有稳定 `backend-v*` 候选时使用；远端同时存在 `backend-v0.3.89` 和 `v0.3.90` 时选择 `backend-v0.3.89`。
- 浏览器扩展 release 使用 `extension-vX.Y.Z`，必须被后端自动更新忽略。
- GitHub `/releases/latest` 是面向用户的 `openbiliclaw-v*` 聚合发布页，会同时挂最新插件 zip、桌面安装包和后端源码入口；它不是后端自动更新的 canonical source。`AutoUpdateService._fetch_latest_version()` 直接查询 `/tags`，分页过滤 backend tag 后选择最高版本。GitHub tag API 默认保留 TLS 校验；仅遇到证书校验类错误时降级重试一次，兜底 Windows 打包环境缺证书链的问题；REST API quota 耗尽的 403/429 会先读 `https://github.com/whiteguo233/OpenBiliClaw/tags.atom` 兜底，仍失败才单独返回 `github_rate_limited`，避免和 DNS / 断网 / GitHub 不可达混在一起。
- 默认忽略 prerelease；若只有更新的 `backend-vX.Y.Z-rc/beta/dev`，状态上报 `up_to_date` + `prerelease_ignored`。
- 浏览器插件更新不由 `AutoUpdateService` 管理：Chrome Web Store / Edge Add-ons / AMO 版本交给浏览器原生更新，GitHub zip / sideload 用户按插件 release 文档手动下载和重新加载。
- **版本 bump 必须重新 lock**：发布提交除 `pyproject.toml` / `openbiliclaw.__version__` 外必须同步运行 `uv lock`（或 `uv sync`）并提交 `uv.lock`。tag 携带过期 lock 时，安装侧首次 `uv sync` 会改写 `uv.lock` 把 worktree 弄脏，历史上曾让所有 git 安装的自动更新永久卡在 `dirty_worktree`。`tests/test_release_consistency.py` 断言三处版本一致；updater 守卫额外豁免 `uv.lock`、未跟踪文件、纯 index-only 条目和本地 `ollama-models/` 作为存量安装兜底，仍会阻止已跟踪文件的工作区修改。

这样可以避免后端 `0.3.64` 把 `extension-v0.3.24` 解析成 `(0,)` 并误报 "Already up-to-date"。

### Config recovery boundary

热重载取消边界：`BackgroundTaskRegistry.cancel_all()/cancel()` 与 `restart_background_tasks()` 都用 `asyncio.wait(..., timeout=1.5)`，而不是会继续等待 coroutine 真正结束的 `wait_for(gather(...))`。第三方 provider / loop 若吞掉 `CancelledError`，配置保存仍在 deadline 内返回；未退出任务继续留在 registry 和 `app.state` 中供后续关闭重试，并且不会启动同名 refresh / account-sync / auto-update loop，避免旧新 runtime 同时写库。正常协作取消的任务仍在新组件发布前完成清理。

对话结算 worker 不在上述 registry 内，由 `RuntimeContext.rebuild_from_config()`
先保持 admission 开放并 drain old；25 分钟 drain 超时不再被吞掉：runtime 恢复
仍在位且尚未撤权的 old queue 继续接单，把异常交给配置事务回滚，且不会调用
`cancel_all`、构造或安装 new runtime。drain 成功后才在无 `await` 临界段暂停，并
依次 exact revoke old `(task, nonce)`、取消 registry 任务、构造并启动 new queue、
等待 new permit 已注册，再发布 new generation 并 shutdown old。new 构造/注册失败
时，old 只能用 fresh nonce 重新授权后 resume；old 的迟到 `finally`
compare-and-clear 旧 tuple，不能清除 new permit。进程 shutdown 的同类超时仍会在
`finally` 强制取消 worker。

配置恢复是 runtime 和 API 的交界：`/api/config` 写盘前先校验新配置可构建 LLM registry，写入后无论正常还是降级模式都调用 `RuntimeContext.rebuild_from_config()` 与 `restart_background_tasks()`。降级上下文的 stable 层已经包含 database、MemoryManager、event hub、task registry 与 total LLM gate；`rebuild_from_config()` 先在局部变量中构造全部 swappable 组件，全部成功才原子发布。API 随后清除 `ctx` 与 `app.state` 的 degraded 镜像、重绑启动时捕获空 `soul_engine` 的 feedback scheduler，下一请求立即绕过 degraded middleware。核心热重载失败会恢复 `config.toml.bak`、保留 guard 并把 `rollback_applied` 返回给调用方；核心已发布而仅后台任务启动失败时保留有效 runtime 和磁盘配置，避免健康新 runtime 与旧坏配置形成反向 split-brain。

热重载成功后，所有可替换 LLM 入口都会拿到同一份 `module_overrides_from_config(config)`。稳定 gate 的 proposed target/inventory 直到全部新组件构造成功并完成 atomic swap 后才更新；晚期构造失败保留旧 target/state，不会让仍在运行的旧 runtime 提前进入新配置的 refill 模式：

- 主 runtime 的 discovery / recommendation / XHS producer 共用 `ctx.llm_service`。
- SoulEngine 内部的 preference / awareness / insight / profile_builder / speculator / dialogue_insight 使用同一份 override。
- SocraticDialogue fallback 若未显式注入 `llm_service`，会继承 `SoulEngine._module_overrides` 再构造 `LLMService`。

`restart_background_tasks()` 在启动后置 one-shot 时通过 `_safe_post_reload_speculate()` 分别调度正向兴趣 speculator 和避雷 speculator，不会 await 两者的 `force_tick()`。正向路径读取 `probe_feedback_history`，避雷路径读取 `avoidance_probe_feedback_history`，让热重载后的首次生成继续避开近期已否认方向。这保证 popup 保存配置的 HTTP 响应不被一次画像猜测卡住；调度本身写 debug 日志，helper 内部吞掉异常，下一轮正常调度仍会继续。

同一后置 one-shot 还通过 `_safe_post_reload_precompute()` 调度一次 `precompute_pool_copy(profile=...)`（v0.3.124+，lever 2a）：`rebuild_from_config()` 的 `cancel_all` 会连带取消正在跑的 classify_pool_backlog / 文案预计算 / delight 评分，若不补一脚，冷启动期反复保存配置的用户会看到候选池迟迟不填（每次保存都把进度清零、最坏要等到下一个 `refresh_check_interval_seconds` tick）。`precompute_pool_copy` 内部会 detached 再启 classify 与 delight，因此一次调用即在新引擎上重启整条 classify→文案→delight drain；其自带的 `_expression_lock` 保证与 refresh loop 周期 drain 不抢同批，刷新轮询仍是兜底。helper 吞掉异常、不影响 `/api/config` 响应。

刷新调度不使用 `scheduler.discovery_cron`。该字段仅保留为旧配置兼容；实际触发由 `refresh_check_interval_seconds` 轮询、候选池低水位（约 `pool_target_count * 0.9`）、`signal_event_threshold`、`trending_refresh_minutes`、`explore_refresh_minutes` 和 `discovery_limit` 共同决定。`KeywordPlanner` 的探索 query piggyback 不另起时钟：它只读取 controller 暴露的 explore 到期 / 即将到期口径，并在成功插入 B 站 query cache 后由 controller 更新同一个 `last_explore_refresh_at`。

`ContinuousRefreshController.run_forever()` 当前并行启动 refresh、`CandidateEvalCoordinator`、pool precompute、soul pipeline、各来源 producer（含匿名微博 producer）和 proactive push 等 loop。即时断供补货与周期 loop 共用 per-source lock；微博分支因此不会被同一 tick 重复执行。协调器 worker 只执行 LLM evaluation，不持有 SQLite drain lock；claim、完成提交、重试 admission 与补位由单一协调任务管理。限流按 15/30/60/120/300 秒退避（尊重更长 `Retry-After`），缺 provider / 鉴权失败暂停后只接受精确 `startup` 或 `config_*` / `manual_*` 唤醒，连续 3 个成功但零缓存 batch 触发 60/120/300 秒无进展退避和一次补货。热重载只取消 registry 中的父 `refresh_loop`；父任务 gather 协调器子任务、子任务归还所有未完成 token 后，`RuntimeContext` 才构造新 runtime。

Expression copy 与 candidate evaluation 对 rate-limit、timeout、connection、5xx 使用同一条 15/30/60/120/300 秒 transient ladder；provider 提供更长 `Retry-After` 时优先采用。鉴权失败或无 provider 进入 `paused`，只由 startup、manual_* 或 config_* 通知恢复；成功但零写入至少等待 15 秒，避免 malformed singleton 紧循环。
