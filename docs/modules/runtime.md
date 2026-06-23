# Runtime Module

## 概述

`src/openbiliclaw/runtime/` 负责后端 daemon 的长期运行能力：后台刷新、账号同步、运行时事件流、浏览器插件 presence gate、自动更新和任务生命周期管理。FastAPI 启动后会通过 `RuntimeContext` 持有这些 runtime 服务，配置热重载时重建可替换组件。

## 已实现功能

| 功能 | 状态 | 说明 |
|------|------|------|
| 后台刷新控制 | ✅ | `ContinuousRefreshController` 按 scheduler 配置补充候选池，并通过 source policy 计算各平台有效配比；注入 `DiscoveryCandidatePipeline` 后，B 站主补货先生产 raw candidates，再进入统一待评估池。 |
| 统一候选待评估池调度 | ✅ | B 站、XHS、抖音、YouTube、X discovery raw candidates 先写入 `discovery_candidates`；runtime 既会在 refresh plan 发现新 raw 后即时调用共享 drain，也会由独立 `_loop_candidate_eval()` 周期性 drain 已有 pending raw 并在 admission 后触发 `precompute_pool_copy()`。controller 层 `_discovery_drain_lock` 与 `DiscoveryCandidatePipeline` 内部 lock 串行化所有入口，producer / refresh / periodic loop 不会并发 admission；正式可换池达到 `pool_target_count` 时不会继续 discovery / drain。 |
| B 站扩展搜索兜底 producer | ✅ | `BilibiliExtensionSearchProducer` 在 B 站平台族低于 quota、`BilibiliAPIClient.search_cooldown_remaining()>0`、扩展 presence 在线且候选池未满时入队 `bili_tasks(type="search")`；扩展回传后仍进入 `DiscoveryCandidatePipeline` 统一评估。 |
| 候选池文案预计算状态同步 | ✅ | 独立 `_loop_pool_precompute()` 将 fresh 候选补齐 `pool_expression` / `pool_topic_label` 后，会同步更新 `last_replenished_count` 并推送 `refresh.pool_updated`；前端消费该事件时只刷新池子状态，不全量替换推荐列表，避免覆盖已 append 的历史内容。 |
| 候选池真实可换计数 | ✅ | `pool_available_count` 现在只表示后端当前可立即 `serve()` 的候选，并按默认每 `topic_group` 最多 3 条的候选窗口计数；runtime status / runtime stream 另带 `pool_raw_count`、`pool_pending_count`、`pool_pending_eval_count`、`pool_evaluated_pending_count` 区分素材库存、待评估和已评估待入池内容。 |
| embedding 后台预热 | ✅ | refresh 完成前只保证候选入池与文案可用；`prewarm_supergroup_embeddings()` / `prewarm_pool_mmr_embeddings()` 作为后台 task 运行，慢本地 embedding 后端不会占住 refresh lock 或让界面长时间停在“正在补货”。v0.3.124+（lever 4）：`prewarm_pool_mmr_embeddings()` 返回值区分良性冷启动与真故障——`-1`（无 embedding service / 空池，没东西可暖）让启动重试包装器 `_safe_prewarm_pool_mmr_embeddings` 平静跳过(不再每次装机刷 5 行 `warmed=0 — retry`)，`0`（有候选但全嵌入失败＝后端不可达）才重试到底并在放弃时打 WARNING 点名 embedding 后端不可达、MMR 降级。 |
| YouTube 后台 discovery producer | ✅ | `YoutubeDiscoveryProducer` 独立运行 `yt_search` / `yt_trending` / `yt_channel`，只在 YouTube 平台族低于 quota 时由 `_loop_youtube_producer()` tick，按每日 ledger 和 `min_interval_minutes` 控制执行。 |
| X 后台 discovery producer | ✅ | `XDiscoveryProducer.produce_if_due()` 在 X 平台族低于 quota 且源健康就绪时，由独立 loop tick 触发 `search` / `feed`（For-You）/ `creator`（账号订阅）三个策略；按 `daily_*_budget` / `min_interval_minutes` / `request_interval_seconds` 节流，For-You 压到很低的每日频次并在连续失败后自动暂停。只 enqueue raw candidates 进 `discovery_candidates`，不写 `content_cache`、不调评估器。`enabled=false` 时是 no-op，不 import `twitter_cli`。 |
| X 源健康状态机 | ✅ | `storage/x_health.py` 的 `XSourceHealthStore` 持久化 `ok` / `missing_cookie` / `expired_cookie`(401) / `blocked`(403) / `rate_limited`(429) 五态；按 code 分别退避，429 带 `cooldown_until` 自愈，401/403/missing 须等用户重新登录 x.com 才恢复；连续 For-You 失败触发 `feed_allowed()=false` 自动暂停。状态经 `GET /api/sources/x/status` 暴露到插件设置页。 |
| 运行时频率配置 | ✅ | `refresh_check_interval_seconds`、行为触发阈值、trending / explore 间隔、单轮发现上限、惊喜队列加载数量、主动推送间隔和 speculator idle tick 都从 `[scheduler]` 读取，配置热重载后重建 runtime 生效。 |
| 浏览器 presence gate | ✅ | `background_llm_work_allowed()` 结合 `scheduler.enabled` 与 `pause_on_extension_disconnect` 控制 daemon-owned 后台 LLM / embedding 工作。 |
| Runtime event stream | ✅ | `/api/runtime-stream` 向扩展推送状态、Cookie sync 请求、配置重载和 presence 事件；`RuntimeEventHub.publish()` 会返回是否至少有一个订阅者接收，供一次性事件判断是否真正投递。 |
| Activity feed 状态摘要 | ✅ | `/api/activity-feed` 聚合认知更新、反馈、推荐池补货和 live summary；未初始化且还没有推荐 / 可换池 / 补货产物时，普通 `/api/events` 不会新写入 pending signals，旧的 `pending_signal_events` 也不会抢占初始化提示，避免首启 setup 保存配置后被“已记下 N 个信号”误导。 |
| 扩展捕捉 E2E 控制事件 | ✅ | local-only `/api/extension/e2e/run` 会通过 runtime stream 投递 `extension_e2e_run`，要求已安装扩展在真实平台页执行白名单 DOM 操作；`/api/extension/e2e/result` 回收插件执行结果，后端再按运行窗口匹配 `/api/events` 中自然捕捉到的事件。 |
| 兴趣探针投递保护 | ✅ | `interest.probe` 只有成功投递到 runtime stream 后才写入 `probed_domains` / `probed_axes` / `probed_distance_bands` 冷却状态；事件 payload 会带 `probe_mode` 与 `challenge`，前端离线时不会消耗 active probe。普通 `near` 探针与挑战探针使用独立 active 额度，运行时选择时仍统一仲裁。 |
| 避雷探针投递与仲裁 | ✅ | `avoidance.probe` 与 `interest.probe` 共用 proactive push 循环；每轮最多投递一个 probe，并用 `last_probe_kind` 在正向/负向都有候选时轮流选择，避免探针频率翻倍。 |
| 图片代理 API | ✅ | `/api/image-proxy` 为移动 Web 和浏览器插件代理白名单 CDN 封面图，逐跳校验 redirect，并在返回前完成类型和 10MB 大小校验；成功封面写入 `data/image-cache/`（小红书 token 归一化），并按「已消费且未保存」定期清理、保护无法重抓的封面；多模态 discovery 评估也复用同一缓存，命中时不再重新请求 CDN。 |
| 自动更新 | ✅ | `AutoUpdateService` 检查 backend git tag，支持 `/api/update-status`、`/api/runtime-status` 更新摘要、手动 check/apply、apply 锁、可信 remote / dirty worktree / fast-forward guard，并通过 runtime stream 推送后端更新事件。dirty worktree guard 豁免 `uv.lock`、未跟踪文件、纯 index-only 条目和本地 `ollama-models/`；apply 前会重置 `uv.lock` 再快进。git 命令通过 `asyncio.create_subprocess_exec` 执行，避免 Windows 长时间运行后线程池 `subprocess.run` 卡死或异常返回；tag fetch 使用 `git fetch --force --tags origin`，避免本地旧 tag 被远端重打后卡在 `would clobber existing tag`。`detect_install_mode()` 上报 `frozen / git / unsupported` 安装形态，桌面冻结包据此在前端禁用自动更新开关。**冻结守卫**：apply 路径显式判 `install_mode == "git"`，冻结包即便与 git 检出共用目录也以 `unsupported_install_mode` 拒绝，杜绝无限重启循环；冻结包后台改跑 check-only 提醒循环（无论开关状态），跟踪 `desktop-v*` 安装包 tag，发现新包时设置页提示并附「前往下载新安装包」直达链接 + toast 提醒。桌面 Web 设置页提供「立即检查 / 立即应用」按钮并随 runtime stream 更新事件实时刷新状态行；配置保存重建服务时经 `adopt_status_from` 保留上次检查结果。降级模式（LLM 注册表不可用）放行 update-status / check / apply 并构建真实 `AutoUpdateService`，便于拉取修复版本恢复。 |
| 开机自启动管理 | ✅ | `runtime.autostart` 提供 macOS LaunchAgent、Windows HKCU Run + `.pyw`、Linux XDG autostart 三套当前用户作用域 manager；`/api/autostart-status`、`/api/autostart/apply`、`openbiliclaw autostart` 和插件设置页共用 env / shadow guard 与方向化 enable/disable 事务。 |
| Ollama 启动预检与生命周期 | ✅ | `runtime.ollama_supervisor` 统一提供 `ollama_required()`、endpoint 归一化、loopback 判定和 `_ollama_is_running()` / `_ollama_start_serve_background()`；`start` 仅在默认 `localhost:11434` 需要本机 Ollama 时尝试后台拉起，远端 / 自定义端口不强行 `serve`。托管启动会给子进程默认传入 `OLLAMA_KEEP_ALIVE=24h`（若用户已设置则保留用户值），减少 `bge-m3` / `llama-server` 在 UI 请求间隔中卸载再冷启动。`_ollama_start_serve_background()` 现在记录**亲手拉起**的 `Popen` 句柄（复用外部已运行实例时句柄留空），`stop_managed_ollama()` 据此在退出时停掉整棵进程树（Windows `taskkill /T`、类 Unix 进程组 `SIGTERM`），对外部托管的 Ollama 一律不动 —— 桌面托盘「退出」经此调用，clean quit 不再遗留孤儿 `ollama serve` / `llama-server` runner。macOS 桌面包构建必须使用官方 `Ollama.app/Contents/Resources/ollama`，并同时打入同目录 `llama-server`、`llama-*`、`lib*.dylib`、`lib*.so` 和 `mlx_metal_*`；如果只发现 Homebrew 风格单独主程序或缺关键动态库，打包会失败，避免随包 daemon `/api/version` 正常但真实 embedding 500。 |
| 账号同步 | ✅ | `AccountSyncService` 同步 B 站账号历史、收藏和关注等信号；历史按 `view_at + 同秒 bvid 集合` 增量导入，收藏 / 关注只把新增 ID 转成画像事件，避免重放旧信号。 |
| 多源 bootstrap 去重 | ✅ | `/api/sources/{xhs,dy,yt}/task-result` 会用 `source_bootstrap_state.json` 过滤跨任务旧 identity key；任务结果仍完整保留，只有新增项进入 memory / profile pipeline。 |
| 扩展任务 claim / 复用 | ✅ | XHS / 抖音 / YouTube bootstrap 任务在扩展 poll 时用短生命周期 SQLite 连接标记 `in_progress`，CLI 默认复用 6 小时内近期任务，避免重复打开前台 tab 全量扫描，也避免 FastAPI 并发 poll 在共享 connection 上嵌套事务。 |
| Soul 画像自动 bootstrap | ✅ | `AccountSyncService` 首次成功写入账号行为并完成 `analyze_events()` 后，若 soul 画像仍为空，会自动调用 `build_initial_profile([])`；每进程生命周期最多尝试一次。 |
| 降级模式启动 | ✅ | 生产 `create_app()` 遇到 `RegistryBuildError` 时构造 degraded `RuntimeContext`，保留健康检查、配置读取/保存、runtime status、runtime stream、`/m` 移动静态壳与 `/favicon.ico`，方便用户从 popup 或手机入口识别并修复错误配置。 |
| 配置热重载 LLM override | ✅ | `RuntimeContext._rebuild_components()` 从 config 构造 `module_overrides`，同时注入主 `LLMService` 与 `SoulEngine` 内部 service；热重载后的正向兴趣和避雷 speculator tick 都 detached 到 `BackgroundTaskRegistry`，不阻塞 `/api/config` 响应。 |
| 运行日志降噪 | ✅ | 全局 logging 初始化会把 `httpx` / `httpcore` logger 提升到 WARNING，避免文件日志在 DEBUG 模式下被连接细节刷屏；业务模块仍按 `logging.file_level` 输出。 |

## 公开 API

```python
from openbiliclaw.runtime.updater import AutoUpdateService

service = AutoUpdateService(enabled=False, check_interval_hours=6)
backend = await service.check_now()
status_code, apply_payload = await service.request_apply(tag="backend-v0.3.92")
```

核心调用：

- `check_now()`：立即检查 GitHub tags，只刷新后端更新状态，不自动应用。
- `request_apply(tag="backend-vX.Y.Z")`：先检查安装形态为 `git`（`detect_install_mode() != "git"` 直接以 `unsupported_install_mode` 拒绝——见下）、git repo、可信 `origin`、worktree clean（仅 `uv.lock` 改动豁免——发布 tag 携带过期 lock 时安装侧 `uv sync` 必然改写它，不能因此永久阻塞更新）、未 merge/rebase、目标 tag 存在且当前 HEAD 可 fast-forward，再返回 `202/applying` 并在后台执行 `git checkout -- uv.lock`、`git merge --ff-only <tag>`、依赖同步和 `os.execv` 重启。
- `check_and_update_if_due()` / `check_and_update_now()`：供后台调度使用；只有 `scheduler.auto_update_enabled=true` 时才会定时自动应用。冻结桌面包走 check-only 分支：**无论开关状态**都按间隔检查 `desktop-v*` 安装包 tag（`_background_loop_enabled()` 对 frozen 恒真），发现新包置 `update_available` 并推 `backend_update_available` 事件提醒用户下载新安装包，但永不进入 apply——`request_apply` 的非 git 守卫独立兜底，后台循环不可能 fast-forward 共享目录里的 git 检出。
- `adopt_status_from(other)`：配置保存触发热重载、本服务被重建时，由 `rebuild_from_config` 调用以携带上一实例的检查结果（版本 / tag / 上次检查时间总是携带；`update_available` / `up_to_date` / `blocked` 等已结算状态也携带，瞬态 `checking` / `applying` 不携带）。否则设置页状态行会从「发现新版本」回退到「尚未检查更新」直到下个检查周期。
- `detect_install_mode()`（模块级函数）：上报安装形态——`frozen`（PyInstaller 桌面包，结构上无法 git 自更新）、`git`（installer / agent / dev 克隆）、`unsupported`（其他）。**安全守卫**：冻结桌面包可能与 AI / 一键安装共用 `~/OpenBiliClaw` 目录（`entry.py` 把 `OPENBILICLAW_PROJECT_ROOT` 指向它，目录里是真实 git 检出），此时磁盘上有 `.git` 但仍必须拒绝自更新——否则会改写他人源码 + venv 而冻结包重启后仍跑捆绑旧码，形成无限重启循环。故 apply 路径显式判 `install_mode == "git"`，不只依赖 `.git` 是否存在。
- **更新通道**：git 安装跟踪 `backend-v*` 源码 tag（legacy `v*` / 裸 semver 兜底）；冻结桌面包跟踪 `desktop-v*` 安装包 tag（`_parse_desktop_candidate`，无 legacy 兜底——两类 tag 不总是同步发布，桌面用户只关心有没有新安装包）。`_fetch_latest_candidate(channel=...)` 按 `check_now` 里的安装形态选通道。
- `get_update_status()`：返回 `/api/update-status` 使用的 backend 状态对象，含 `install_mode`。
- `get_runtime_status()`：返回 `/api/runtime-status` 合并用的自动更新摘要，包含当前版本、最新远端版本、上次检查、错误、状态原因和 `install_mode`。

### ContinuousRefreshController

```python
result = await controller.drain_discovery_candidates_once(batch_size=30)
```

核心调用：

- `refresh_if_needed()` / `force_refresh()`：按 pool available 缺口、source share 和 raw-material headroom 构建补货计划；如果正式可换池已经达到 `pool_target_count`，返回 `pool_at_cap` 并跳过 discovery。
- `drain_discovery_candidates_once(batch_size=..., reason="manual")`：由 XHS task-result / 被动采集等外部来源入队后触发；refresh path 和 `_loop_candidate_eval()` 走同一套 controller drain helper。它会先检查 `count_pool_candidates() >= pool_target_count`，池满时直接返回 `{"evaluated": 0, "cached": 0, "rejected": 0}`。profile 未就绪或已有 drain 在跑时同样 no-op，底层 `DiscoveryCandidatePipeline.drain_pending()` 也有同一共享锁，避免 refresh / XHS / Douyin / YouTube / periodic loop 多入口并发 admission。周期 loop 的 drain 如果缓存了新候选，会立即补调 `precompute_pool_copy()` 并发布补货后的池子状态。
- `run_init_backfill(profile, target_pool_count, *, fully_parallel=True)`：图形化引导初始化（gui-init）stage 4 的发现补池。持 `_refresh_lock` 与连续 refresh 串行，绝不与之争 `content_cache`；`async with` 在 `CancelledError` 时释放锁。不查 `_llm_work_allowed()`，因此 init 期间后台门控暂停不会自锁 init 自己的补池。
- `_pool_count_payload()`：统一生成 runtime status / runtime stream 的池子字段，包含 pending eval 与 evaluated pending 拆分。

### InitCoordinator + InitPrereqs（引导初始化）

`InitCoordinator`（`runtime/init_coordinator.py`，惰性挂在 `RuntimeContext.init_coordinator`）是图形化引导初始化的生命周期所有者：`init_runs` 持久化状态机、单写者进度事件（`_write_lock` 串行化，并行 stage 3/4 的 `sequence` 不丢更新）、`BEGIN IMMEDIATE` 单飞预定、启动 `reconcile_on_boot()`（崩溃残留 `starting/running` 判失败）、协作取消、bootstrap task 归属（供写者门控放行 init 自己的 task-result）。`InitPrereqs`（`runtime/init_prereqs.py`）提供 TTL 缓存 + 单飞的 `chat_ready()` / `bilibili_check()` / `enabled_platforms()` 前置探测。共享流水线 `cli.run_guided_init`、`/api/init*` 端点和 init 期间写者门控详见 [init 模块文档](init.md)。

### Degraded RuntimeContext

`build_runtime_context()` 仍然保持严格：LLM registry 无法构建时直接抛出 `RegistryBuildError`，方便测试和 CLI 调用方快速失败。FastAPI 生产入口 `create_app()` 会单独捕获这个错误并调用 `build_degraded_runtime_context()`。

降级模式下可用接口：

- `GET /api/health`：返回 `status="degraded"`、`reason="llm_registry_unavailable"` 和 blocking issues；当 `SoulEngine` 可用时会额外返回可选字段 `profile_ready`，表示 soul 画像是否已生成。v0.3.95+ 额外返回 `embedding_ready`（bool）。v0.3.137+ 该同一 live probe 也被 `/api/init-status` 复用：若 `[llm.embedding].provider` 已配置，初始化前置清单会下发 `embedding_required=true`，`can_start` 与 `POST /api/init` 都必须等真实 probe 通过；provider 留空则可降级初始化。v0.3.97+ 这是一次**实时探活**而非「服务是否构建」：经 `EmbeddingService.probe()` 绕过缓存真打一次 provider，结果按 `_EMBEDDING_READY_TTL_SECONDS`（默认 30s）+ single-flight 锁缓存，探活本身由 `_EMBEDDING_PROBE_TIMEOUT_SECONDS`（默认 15s）上限兜住；超时在 init/checklist 语义下按未就绪处理并短 TTL 重试。于是 provider 现已 404/500（如 `bge-m3` 没拉、Ollama 停了、随包缺 `llama-server`）会如实报 `false`，修好后下次探活即翻 `true`；服务对象不存在仍 `false`，老/无 `probe()` 的服务回退「构建即就绪」。`false` 表示语义去重 / MMR 多样性降级（可能刷到换皮重复内容），插件 popup 据此显示「一键启用本地 Ollama」横幅。
- `GET /api/config`：返回完整配置、`degraded=true` 和同一组 issues。
- `PUT /api/config`：允许保存修复配置，但跳过热重载并返回 `restart_required=true`。
- `GET /api/runtime-status` 与 `/api/runtime-stream`：用于 popup 展示降级状态；stream 会先发送 `{type:"degraded", ...}` 并保持连接。

其他 API 在降级模式下返回 503，避免在缺少 LLM registry、数据库/运行时组件不完整时继续执行推荐、发现或画像链路。

### Runtime Status Pool Counts

`GET /api/runtime-status` 和 runtime stream 中的池子字段语义如下：

- `pool_available_count`：真实可换数量，只统计 fresh、未 dislike、未进入推荐历史、未近期看过、已有 `pool_expression` / `pool_topic_label`、已有 `style_key` / `topic_group` 且来源可打开的候选，并按默认每 `topic_group` 最多 3 条的候选窗口计数。
- `pool_raw_count`：fresh、未 dislike、未进入推荐历史的 `content_cache` 素材库存 + `discovery_candidates` 中尚未缓存的 raw candidates，用于诊断池子里是否还有原料。
- `pool_pending_count`：未近期看过、但仍缺文案 / 分类 / 可打开链接等 readiness 条件的 `content_cache` 素材数，加上待评估 / 已评估待入池候选；不会用 `raw - available` 近似，避免把 recently viewed 内容误算为待整理。
- `pool_pending_eval_count`：`discovery_candidates.status IN ('pending_eval', 'evaluating')` 的数量，表示已经找到但还没完成统一 LLM 评估的内容。
- `pool_evaluated_pending_count`：`discovery_candidates.status='evaluated'` 的数量，表示已经完成评估但尚未 admission 到 `content_cache` 的内容。
- `last_discovered_count`：最近一轮 refresh 新入队的 raw candidates 数；已评估待入池候选的 retry / admission 不会冒充“新发现”。
- `recent_pool_topics`：最近一轮实际 admission 到推荐池的内容主题；retry-only admission 可以更新该字段，但不会增加 `last_discovered_count`。

前端凡是显示“可换”都必须只读取 `pool_available_count`。`pool_pending_count` / `pool_pending_eval_count` / `pool_evaluated_pending_count` 只能用于“正在整理成可换内容”等辅助文案和诊断。

### Activity Feed

`GET /api/activity-feed` 返回 popup、移动 Web 和桌面 Web 共用的轻量动态摘要：

- `live_summary`：当前 runtime 摘要；优先显示手动补货中的 `manual_refresh_message`，否则根据待处理行为信号或可换池库存生成短文案。
- `headline`：最新动态条目的摘要；没有动态条目时回退到 `live_summary`。
- `items`：认知更新、反馈记录和推荐池补货等最近动态。

首启 / setup 阶段要优先保护初始化入口：当 `initialized=false`，且 `recommendation_count`、`pool_available_count`、`pool_pending_count`、`last_replenished_count`、`last_discovered_count` 都为 0 时，普通 `/api/events` 会以 `not_initialized` 拒收，不会写入 memory 或制造新的 `pending_signal_events`；`live_summary` 也会提示用户点击「开始初始化」，不会因为历史残留 pending signal 显示“已记下 N 个信号”。一旦已有推荐或候选池产物，上述 pending signal 文案会按初始化后的正常运行状态展示。

### Runtime Status Update Fields

`GET /api/runtime-status` 会保留自动更新摘要字段，供插件和 Web 前端在统一 runtime 状态对象中读取：

- `auto_update_enabled`：当前后台定时自动更新是否开启；关闭时仍允许手动检查和手动 apply。
- `install_mode`：安装形态（`frozen` / `git` / `unsupported`）。桌面 Web 设置页在非 `git` 时禁用自动更新开关并提示用安装包升级。
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

- 每轮 proactive push 最多发布一条 probe；惊喜推荐仍走独立 `delight.candidate` 逻辑。
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

`GET /api/image-proxy?url=<encoded_url>` 只代理明确白名单内的 HTTP(S) 图片 URL，用于移动 Web `/m/` 和浏览器插件 side panel 的推荐、惊喜推荐和消息封面图。白名单按域名边界匹配，当前包含 `hdslb.com`、`xhscdn.com`、`pstatp.com`、`douyinpic.com`、`douyinvod.com`、`ytimg.com` 和 `ggpht.com`，会拒绝非 HTTP(S)、缺 hostname、userinfo 和非白名单域名。

代理不使用自动跳转；`301/302/303/307/308` 最多手动跟随 3 次，每一跳都会重新校验目标 URL。上游响应必须是 2xx 且 `Content-Type` 为 `image/*`。若 `Content-Length` 超过 10MB 会立即返回 413；缺失或伪造长度时，响应体会先流式写入 `SpooledTemporaryFile(max_size=1MB)`，实际读取超过 10MB 同样返回 413，避免在下游响应头已发送后才发现超限。

成功响应会带 `Cache-Control: public, max-age=86400` 和 `X-Content-Type-Options: nosniff`，并写入本地图片缓存。缓存回退只用于上游网络失败、超时或 5xx 类上游错误；URL / redirect 白名单失败、非图片 Content-Type、超过 10MB 等校验类错误会保留 403 / 400 / 413 等明确状态，不会被统一折叠成 502。该接口按本地单用户后端设计，默认只应暴露在 `127.0.0.1` 或用户可信局域网；若用 `--host 0.0.0.0` 对外监听，应在反向代理层自行加访问控制。

### Boot Autostart API

```python
from openbiliclaw.runtime import autostart

state = autostart.status()
autostart.register(config)
autostart.unregister()
```

核心对象：

- `AutostartStatus(supported, registered, platform, mechanism, reason, detail)`：API、CLI 和插件 UI 共享的状态模型。`mechanism` 固定为 `launchd` / `windows_run` / `xdg_autostart` / `none`。
- `build_launch_spec(config)`：生成登录项执行命令，固定为当前 Python 解释器执行 `-m openbiliclaw.cli start`，并注入 `OPENBILICLAW_PROJECT_ROOT`；如果能找到 `ollama`，会把其目录加入登录项 `PATH`。
- `active_env_managed_inputs(config)`：检测会在桌面登录会话里丢失的环境变量来源（`OPENBILICLAW_*`、provider API key env、抖音 Cookie env），用于拒绝开启自启动。
- `autostart_shadowed(intended)`：写后 reload effective config，检测 `config.local.toml` 或环境变量是否覆盖了 `[autostart].enabled`。

公开接口：

- `GET /api/autostart-status`：远程可读、降级模式可读，返回固定字段集；只展示 `enabled`、`registered`、`supported`、`can_manage`、`reason` 等状态，不包含 Cookie / API Key 等敏感配置。
- `POST /api/autostart/apply {"enabled": bool}`：本机 trusted-local 可写；非本机返回 `403 local_only`，不支持平台返回 `409 unsupported_*`，env / shadow 命中返回 `409`。开启时先写 config 后注册 OS，关闭时先注销 OS 后写 config，并在失败时尽量回滚 OS 与 config 到操作前状态。

平台实现都只写当前用户作用域：

- macOS：`~/Library/LaunchAgents/com.openbiliclaw.daemon.plist`，不执行 `launchctl bootstrap`，下次登录由 launchd 读取。
- Windows：`HKCU\Software\Microsoft\Windows\CurrentVersion\Run` + `data/autostart/openbiliclaw-autostart.pyw`，优先用 `pythonw.exe`。
- Linux：`~/.config/autostart/openbiliclaw.desktop`，使用 XDG autostart。

#### 封面磁盘缓存与清理

成功抓取的封面以 `sha256(归一化 URL)` 为键写入 `data/image-cache/`（键与清理逻辑集中在 `openbiliclaw.runtime.image_cache`，由 `api.app` 复用，保证单一真源）。小红书 `sns-webpic-qc.xhscdn.com/{timestamp}/{token}/{path}` 这类带轮换 token 的 URL 会先剥掉 `{timestamp}/{token}` 前缀再算键，因此 token 过期重新生成后仍命中同一份缓存——这是小红书封面在签名失效后仍能展示的关键。

`cleanup_image_cache` 负责按消费状态清理：启动时全量执行一次，运行时由 `RefreshRuntime._loop_image_cache_cleanup` 每 6 小时增量执行。清理规则为「已消费且未保存」——`content_cache.pool_status` 属于 `shown / feedbacked / stale / purged_by_dislike`、且 bvid 不在 `favorites` / `watch_later`（经 `Database.iter_cover_lifecycle` 联表判定）的封面会被删除；`fresh` / `suppressed`（待展示 / 可能复活）以及任一被收藏或加入稍后再看的封面始终保留。B 站等 URL 稳定、可随时重抓的来源安全释放空间（实测可回收数百 MB）；而带过期 token、删除后无法重抓的小红书封面默认受保护不删（缓存是其唯一副本），可用 `protect_unrefetchable=False` 关闭。无任何 `content_cache` 行引用、且文件超过 30 天的孤儿封面会作为增长上限兜底被移除（降级模式下数据库不可用时仅执行这条规则）。

#### 发现即缓存（封面预取）

白名单 / redirect / 大小 / 类型校验的抓取核心 `fetch_cover_bytes` 是唯一真源，由 proxy 路由和预取共用；失败抛 `CoverFetchError`（携带 400/403/413/502/504），proxy 路由再映射回对应 HTTP 状态。`get_or_fetch_cover_bytes` 是缓存优先入口：先按同一白名单边界校验 URL，再读取 `data/image-cache/` 的非空文件，未命中才调用 `fetch_cover_bytes` 并写回缓存。多模态 discovery evaluator 使用这个入口，因此小红书已缓存头图即使原 CDN token 过期，也能继续参与封面图评估。

`RefreshRuntime._loop_cover_prefetch` 每 60 秒做一次「发现即缓存」：从 `Database.iter_servable_cover_urls` 取最近 12 小时内、仍可展示（`fresh / shown / suppressed` 或已保存）的封面（最新优先），`select_prefetch_targets` 过滤掉非白名单和已缓存项、把**无法重抓的小红书封面排在最前**，每轮最多抓 40 张写入缓存。这修复了此前封面只在「展示时」才懒加载、而小红书签名 token 早已过期导致 502 破图的问题——预取趁 token 新鲜时就把图落盘；最近窗口也避免对 token 已死的旧内容反复重试。预取按 `content_cache.cover_url` 原始值（可能是 `//` 或 `http://`）归一化后再抓，落盘 key 与 proxy 查找一致，故预取的封面 proxy 能直接命中。

### AccountSyncService

```python
from openbiliclaw.runtime.account_sync import AccountSyncService

service = AccountSyncService(
    memory_manager=memory,
    bilibili_client=bilibili_client,
    soul_engine=soul_engine,
)
result = await service.sync_now()
```

`sync_now()` 会拉取最近一批 B 站历史、收藏夹和关注列表，但只有新增信号会进入 `memory.propagate_event()` 与 `soul_engine.analyze_events()`：

- 历史记录：使用 `last_history_view_at`、`last_history_bvid` 和 `history_bvids_at_last_view_at` 跳过已经处理过的同秒历史项。
- 收藏夹：使用稳定排序后的 `favorite_signature` 和 `favorite_bvids`，签名变化时只导入新增 bvid。
- 关注列表：使用 `following_signature` 和 `following_mids`，签名变化时只导入新增 mid。

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

每条推文经 `discovery.x_normalize.normalize_tweet()` 映射为 `DiscoveredContent`（`content_type ∈ {tweet, thread}`、`body_text` 带全文），enqueue 进 `discovery_candidates` 待评估池——producer **只 fetch，不写 `content_cache`、不调评估器**，由共享混源 evaluator 完成 admission。runtime 的平台族统计会把 `x` / `x-*` / `twitter` 归一到 `twitter`，避免 X 配额、过滤 tab 和 pool 状态被拆成不同来源。每个策略 run 都把成功 / 失败结果回写 `XSourceHealthStore`（成功 `record_success()`，失败 `record_error(exc)` 按 401/403/429 落对应健康态）。预算护栏：`daily_search_budget` / `daily_feed_budget` / `daily_creator_budget`（`0` = 不设上限）+ 两次请求间 `request_interval_seconds` 间隔。`enabled=false` 时整条路径 no-op，绝不 import `twitter_cli` / `curl_cffi`。

X 客户端 `XClient`（`sources/x_client.py`）封装可选 extra `openbiliclaw[x]` 的 `twitter-cli`，全程只读，方法用 `asyncio.to_thread` 包成 async；底层 `TwitterAPIError` / `AuthenticationError` 映射为 `XMissingCookieError` / `XAuthError`(401) / `XBlockedError`(403) / `XRateLimitError`(429)，供源健康状态机分流退避。

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

统一关键词 planner 开启时，producer 会通过 `KeywordFetchCoordinator` claim B 站关键词并把 `source_keyword_id` 写进任务 payload；扩展收到 `bili_task_available` 后打开真实 B 站搜索页并抓渲染后的 DOM 卡片，`/api/sources/bili/task-result` 再把视频转换成 `source_platform="bilibili"`、`source_strategy="bili-extension-search"` 的 raw candidates，并触发一次候选 drain。terminal `ok` 会把关键词标记 used，失败或空结果标记 failed。

### Source Bootstrap Task Results

XHS / 抖音 / YouTube 的插件任务桥保留两层去重：

- 单任务内：`merge_result()` 合并 partial / final payload 时按 scope + 平台原生 ID / URL / title 去重，只把本次新增项返回给 API 传播。
- 跨任务：API 在传播 bootstrap 事件前读取 `source_bootstrap_state.json`，跳过已经进入事件路径的 `xhs_seen_note_keys` / `dy_seen_video_keys` / `yt_seen_item_keys`。这样 `fetch-*`、`init` 或近期任务复用重复返回同一批收藏 / 历史时，不会再次写入 memory 或触发增量画像分析。

## 配置项

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `scheduler.auto_update_enabled` | `false` | 是否启用后台自动更新检查。 |
| `scheduler.auto_update_check_interval_hours` | `6` | 自动更新检查间隔。 |
| `scheduler.auto_update_allow_prerelease` | `false` | 是否允许 `backend-vX.Y.Z-rc/beta/dev` 预发布 tag 进入候选。 |
| `scheduler.auto_update_allowed_remotes` | OpenBiliClaw GitHub HTTPS / SSH | 允许自动更新快进的 `origin` 精确 allowlist；unknown remote 或带凭据 URL 会被拒绝。 |
| `scheduler.enabled` | `true` | 后台 LLM / embedding 总开关。 |
| `scheduler.pause_on_extension_disconnect` | `false` | 浏览器插件断开后是否暂停后台 LLM / embedding 工作。 |
| `scheduler.extension_disconnect_grace_seconds` | `90` | 插件断开后的宽限秒数。 |
| `scheduler.refresh_check_interval_seconds` | `60` | `ContinuousRefreshController` 主循环轮询间隔。 |
| `scheduler.signal_event_threshold` | `6` | 累计多少条新行为事件后触发 `search + related_chain`。 |
| `scheduler.trending_refresh_hours` | `3` | `trending` 策略最小刷新间隔。 |
| `scheduler.explore_refresh_hours` | `12` | `explore` 策略最小刷新间隔。 |
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
- GitHub `/releases/latest` 是面向用户的 `openbiliclaw-v*` 聚合发布页，会同时挂最新插件 zip、桌面安装包和后端源码入口；它不是后端自动更新的 canonical source。`AutoUpdateService._fetch_latest_version()` 直接查询 `/tags`，分页过滤 backend tag 后选择最高版本。GitHub tag API 默认保留 TLS 校验；仅遇到证书校验类错误时降级重试一次，兜底 Windows 打包环境缺证书链的问题。
- 默认忽略 prerelease；若只有更新的 `backend-vX.Y.Z-rc/beta/dev`，状态上报 `up_to_date` + `prerelease_ignored`。
- 浏览器插件更新不由 `AutoUpdateService` 管理：Chrome Web Store / Edge Add-ons / AMO 版本交给浏览器原生更新，GitHub zip / sideload 用户按插件 release 文档手动下载和重新加载。
- **版本 bump 必须重新 lock**：发布提交除 `pyproject.toml` / `openbiliclaw.__version__` 外必须同步运行 `uv lock`（或 `uv sync`）并提交 `uv.lock`。tag 携带过期 lock 时，安装侧首次 `uv sync` 会改写 `uv.lock` 把 worktree 弄脏，历史上曾让所有 git 安装的自动更新永久卡在 `dirty_worktree`。`tests/test_release_consistency.py` 断言三处版本一致；updater 守卫额外豁免 `uv.lock`、未跟踪文件、纯 index-only 条目和本地 `ollama-models/` 作为存量安装兜底，仍会阻止已跟踪文件的工作区修改。

这样可以避免后端 `0.3.64` 把 `extension-v0.3.24` 解析成 `(0,)` 并误报 "Already up-to-date"。

### Config recovery boundary

配置恢复是 runtime 和 API 的交界：`/api/config` 写盘前先校验新配置可构建 LLM registry，正常模式下写入后调用 `RuntimeContext.rebuild_from_config()` 与 `restart_background_tasks()`。热重载失败会恢复 `config.toml.bak`，并把 `rollback_applied` 返回给调用方；降级模式不做热重载，保存成功后返回 `restart_required=true`，要求用户重启 daemon 让新的 registry 生效。

热重载成功后，所有可替换 LLM 入口都会拿到同一份 `module_overrides_from_config(config)`：

- 主 runtime 的 discovery / recommendation / XHS producer 共用 `ctx.llm_service`。
- SoulEngine 内部的 preference / awareness / insight / profile_builder / speculator / dialogue_insight 使用同一份 override。
- SocraticDialogue fallback 若未显式注入 `llm_service`，会继承 `SoulEngine._module_overrides` 再构造 `LLMService`。

`restart_background_tasks()` 在启动后置 one-shot 时通过 `_safe_post_reload_speculate()` 分别调度正向兴趣 speculator 和避雷 speculator，不会 await 两者的 `force_tick()`。正向路径读取 `probe_feedback_history`，避雷路径读取 `avoidance_probe_feedback_history`，让热重载后的首次生成继续避开近期已否认方向。这保证 popup 保存配置的 HTTP 响应不被一次画像猜测卡住；调度本身写 debug 日志，helper 内部吞掉异常，下一轮正常调度仍会继续。

同一后置 one-shot 还通过 `_safe_post_reload_precompute()` 调度一次 `precompute_pool_copy(profile=...)`（v0.3.124+，lever 2a）：`rebuild_from_config()` 的 `cancel_all` 会连带取消正在跑的 classify_pool_backlog / 文案预计算 / delight 评分，若不补一脚，冷启动期反复保存配置的用户会看到候选池迟迟不填（每次保存都把进度清零、最坏要等到下一个 `refresh_check_interval_seconds` tick）。`precompute_pool_copy` 内部会 detached 再启 classify 与 delight，因此一次调用即在新引擎上重启整条 classify→文案→delight drain；其自带的 `_expression_lock` 保证与 refresh loop 周期 drain 不抢同批，刷新轮询仍是兜底。helper 吞掉异常、不影响 `/api/config` 响应。

刷新调度不使用 `scheduler.discovery_cron`。该字段仅保留为旧配置兼容；实际触发由 `refresh_check_interval_seconds` 轮询、候选池缺口、`signal_event_threshold`、`trending_refresh_hours`、`explore_refresh_hours` 和 `discovery_limit` 共同决定。

`ContinuousRefreshController.run_forever()` 当前并行启动 refresh、candidate eval、pool precompute、soul pipeline、B 站扩展兜底 producer、XHS producer、Douyin producer、YouTube producer、X producer 和 proactive push 等 loop。共享的 `background_llm_work_allowed()` gate 覆盖所有 daemon-owned LLM / embedding 工作；B 站扩展兜底、YouTube / X 与 XHS / Douyin 一样会在 gate 关闭时跳过 tick。不同点是 B 站扩展兜底只在 API search 冷却期间入队浏览器搜索任务；YouTube 和 X 都不通过扩展任务队列做 steady-state discovery，而是在后端直接调用各自 strategies（X 经 `XClient` 服务端 cookie 重放）；`yt_tasks` 只保留给 bootstrap profile 导入，X 没有 init 期 bootstrap 任务。外站 producer、B 站扩展兜底和 B 站主 refresh 都会优先把 raw candidates 交给同一个 `DiscoveryCandidatePipeline`；即使 refresh plan 因来源配额为空，独立 candidate eval loop 仍会处理已有 pending raw，后续混源 batch 评估和入池逻辑一致，并由 controller drain lock + pipeline drain lock 串行化。
