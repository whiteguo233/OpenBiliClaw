# Guided Init Module

## 概述

引导初始化（guided init）让用户既能在命令行 `openbiliclaw init`、也能在浏览器插件「推荐」tab、桌面 Web（`/web`）未初始化空状态、或安装包首启 `/setup/` 向导里点「开始初始化」完成首轮建模。所有图形入口共用同一套四阶段流水线，后端再叠加进度状态机、前置检查和写者门控，保证图形化初始化在一个活跃后端上安全运行。

四阶段（与 CLI 完全一致）：

1. **拉取数据** — B站 历史 / 收藏 / 关注（`_fetch_bilibili_init_data`，v0.3.118+ 仅当 `include_bili=True`，B 站与其他来源一样可取消）+ 小红书 / 抖音 / YouTube bootstrap 信号采集（按本轮勾选来源）+ 知乎 `bootstrap_events` + Reddit `bootstrap_events` + X 点赞 / 收藏（`_fetch_x_init_data`,服务端 twitter-cli 直拉、无扩展任务,与 B站 一样在本轮直接持久化;cookie 未同步时静默跳过）→ 统一 `build_event` → `memory.propagate_event` 入库。X 点赞 → `event_type="like"`、收藏 → `event_type="favorite"`(均为显式正向信号,v0.3.118+ 同时进画像构建的 history 行,保证 X-only 初始化也有画像输入)。Reddit saved → `favorite`、upvoted → `like`、subscribed subreddit → `follow`，每个分支默认最多 300 条；Reddit-only 初始化会等待插件回传这些信号，若 0 条则走统一 `empty_signals`。
2. **分析偏好** — `soul_engine.analyze_events(...)` 分片并发；每个初始化 chunk 除了结构化偏好，也会产出少量临时 `awareness_candidates` / `insight_candidates`，本地去重合并后只作为本次画像生成上下文。
3. **生成画像** ‖ 4. **发现补池**（并行）— `soul_engine.build_initial_profile(...)` 与发现补池同时跑，画像生成会消费合并后的 preference、history summary，以及第 2 段生成的临时觉察 / 洞察候选；这些候选不写入长期 `awareness` / `insight` 层。发现用 preference-only 草稿画像预热评估；如果正式候选池还是空的，补池会先构造 `cold_start` 的 `PoolDistributionSnapshot`，把画像中最高权重兴趣作为首批 query 的软避让方向，并优先覆盖次级兴趣 / 兴趣域，避免第一批 discovery 全部集中在同一个强 topic。

## 共享流水线 `cli.run_guided_init`

| 项 | 说明 |
|---|---|
| 位置 | `src/openbiliclaw/cli.py` |
| 签名 | `async run_guided_init(*, client, memory, soul_engine, favorite_limit, follow_limit, include_bili=True, include_xhs, include_dy, include_yt, include_x=False, include_zhihu=False, include_reddit=False, target_pool_count, discover_backfill, coordinator=None, run_id=None) -> InitResult`（`include_bili=False` 时 `client` 可为 `None`） |
| 为什么是协程 | 四阶段原先内联在 `init` 命令里，被四处独立 `asyncio.run` 包着，后端无法复用（会嵌套事件循环）。合并为一个协程后，CLI 用单次 `asyncio.run(run_guided_init(...))` 驱动、API 在服务 loop 里直接 `await`。 |
| bootstrap 采集器 | 仍是同步实现（有同步调用方 + 测试），但在流水线里走 `await asyncio.to_thread(...)`，不冻结 API 事件循环；`Database` 以 `check_same_thread=False` 打开，跨线程读安全。 |
| `discover_backfill` 注入 | 唯一与运行路径相关的步骤。CLI 传 `_run_init_discovery_backfill_async`（一次性 `discovery_engine`）；API 传 `controller.run_init_backfill`（持 `_refresh_lock`，与连续 refresh 串行）。其余步骤完全共享。 |
| 进度上报 | 传入 `coordinator` / `run_id` 时，在每个 stage 边界回调 `coordinator.stage_started/stage_done`、并 `register_enqueued_task` 登记 bootstrap task id；run 生命周期（mark_running / complete / fail）留给调用方。 |
| 失败语义 | 硬失败抛 `GuidedInitError(reason)`（`empty_history`（选了 B 站但历史为空）/ `empty_signals`（所有所选画像来源 0 信号，v0.3.118+）/ `profile_failed`）：CLI 转状态面板 + 退出码 1，API 转 `coordinator.fail(reason)`。发现阶段失败是部分成功（画像已生成），记在 `InitResult.discovery_error`。 |

`InitResult` 携带 CLI summary / API wrapper 需要的全部字段（各来源事件数、scope counts、profile、discovered_count、discovery_error）。

首轮发现多样性由 `discovery.pool_snapshot.build_cold_start_pool_snapshot()` 提供：当 CLI `_run_init_discovery_backfill_async` 或 API `ContinuousRefreshController.run_init_backfill()` 看到 `count_pool_candidates()==0` 时，会生成 `cold_start=true` 的 snapshot 并传给 `ContentDiscoveryEngine.discover(..., pool_snapshot=...)`。这份 snapshot 不代表真实池子已有饱和历史；它只把权重最高的 1-2 个兴趣当作 `avoid_topics` 软约束，把剩余兴趣名和一级兴趣域放入 `prefer_axes`，让搜索词 prompt 在保留少量强兴趣命中感的同时，把首批内容面铺开。池子已有内容后，API runtime 会改用真实 `build_pool_distribution_snapshot()`；CLI 首轮 init 只做空池冷启动保护。初始化完成后的统一 keyword planner 如果遇到正式池仍为空，也会把同一套 cold-start hints 写进各平台的 merged keyword prompt，避免跨平台第一批关键词都押在同一个强兴趣上。

## 状态机 `InitCoordinator`

| 项 | 说明 |
|---|---|
| 位置 | `runtime/init_coordinator.py`；惰性挂在 `RuntimeContext.init_coordinator`（重建后仍读当前组件） |
| 持久化 | `init_runs` 表（`storage/database.py`）：`run_id / status / stage / stages_json / partial_success / error_reason / sequence / started_at / updated_at / finished_at` |
| 单飞启动 | `try_start(run_id)` → `try_reserve_init_starting`（`BEGIN IMMEDIATE` CAS）；活跃 run 存在时返回 False。TOCTOU 收口在 DB。 |
| 单写者 | `_write(...)` 在 `_write_lock` 下串行化「读 stages → 改 → 写 → 发事件」，保证并行 stage 3/4 的 `sequence` 严格递增、不丢更新。 |
| 事件 | `init_progress`（stage 起止）/ `init_completed` / `init_failed`，经 `event_hub` 推到 `runtime-stream`。 |
| 取消 | `attach_task` 记任务句柄；`cancel_current_run` 调 `task.cancel()`，wrapper 捕获 `CancelledError` 后 shield 写入 `cancelled` 终态。 |
| 启动 reconcile | `reconcile_on_boot()`（API startup 调用）把崩溃残留的 `starting/running` 行判 `failed(interrupted)`，避免 `/api/init-status` 永远报 running。 |
| bootstrap 归属 | `register_enqueued_task` / `is_owned_bootstrap_task` 给写者门控判断某 task-result 是否属于本 init run。 |

前置探测 `InitPrereqs`（`runtime/init_prereqs.py`）：`chat_ready()`（provider health，TTL 30s，超时乐观）、`bilibili_check()`（`validate_cookie`，ok 60s / fail 10s TTL）、`enabled_platforms()`；embedding readiness 复用 `/api/health` 的 `_health_embedding_ready()`，绕过缓存真实调用一次 `EmbeddingService.probe()`。全部探测都 TTL 缓存 + 单飞，避免轮询打爆。v0.3.137+：`/api/init-status.prerequisites.embedding_required` 表示 `[llm.embedding].provider` 是否已配置；已配置时 `can_start` 会硬性等待 `embedding_ready=true`，`POST /api/init` 临界区也会复验，失败返回 `409 embedding_not_ready` 并把刚预约的 run 回滚为 idle。provider 为空代表用户明确关闭 embedding，仍允许降级初始化。v0.3.118+：B 站登录不再硬性拦截 `GET /api/init-status` 的 `can_start`（是否拦截取决于客户端勾选了哪些来源，只有 `POST /api/init` 知道）——`bilibili_logged_in` 仍在 `prerequisites` 里下发，前端在勾选了 B 站时自行拦截；`POST /api/init` 也只在所选来源包含 bilibili 时做登录 409 复验。显式 `sources` 为空或没有任何合法平台 key 时返回 409 `no_sources_selected`；其余合法勾选（包括 Reddit-only）会作为本轮显式 opt-in 生效，并 best-effort 写回 `sources.<platform>.enabled=true`。

## API 端点

| 端点 | 方法 | 访问 | 说明 |
|---|---|---|---|
| `/api/init-status` | GET | 远程可读 / 降级可读 | 权威进度 + 前置清单 + `can_start`（trusted-local && 硬前置 && 非 running && supported）/ `can_manage`（trusted-local）。前置清单包含 `embedding_ready` 与 `embedding_required`；远程不 403、`can_manage=false`。 |
| `/api/init` | POST | 仅本机 | 占坑前廉价拒绝（403 local_only / 409 unsupported_runtime / 409 already_initialized）→ `try_start`（409 already_running）→ 临界区复验前置（缺则复位 idle + 409，不留 stuck `starting` 行；包括已配置 embedding provider 时的 `embedding_not_ready`）→ 后台跑 wrapper → 202 + 初始 status。可选 body `sources`（平台来源数组）：传入时按合法平台 key 直接作为本轮显式 opt-in，并 best-effort 写回 `sources.<platform>.enabled=true`；不传则用全部已开启平台（CLI / 旧客户端行为）。 |
| `/api/init/cancel` | POST | 仅本机 | 协作取消在跑的 run；无运行中 → 409 not_running。 |

`_init_wrapper`（`api/app.py`）是某次 API run 的**唯一**状态 / 事件写者：`mark_running` → `run_guided_init(coordinator=...)` → `complete(partial_success=...)`；`CancelledError` → shield `cancel`，`GuidedInitError` → `fail(reason)`，其它异常 → `fail("internal_error")`。三个 path 都在 `auth.py` 公共集 + 降级白名单。

## init 期间写者门控

防止并发写污染在跑的 init（`init_active()` 为真时）。设计原则是 **deny-by-default**：不是枚举"要拦的写端"（总会漏），而是默认拦截一切变更、只放行 init 必需的少数路径。

- **HTTP 写端（deny-by-default）**：`_init_active_write_guard` 中间件对所有 `POST/PUT/PATCH/DELETE` 返回 `409 init_running`,**除非**命中放行清单：`/api/init`、`/api/init/cancel`、`/api/bilibili/cookie`、`/api/auth/*`、以及精确 5 段匹配的 `/api/sources/<source>/{kick,task-result}`（bootstrap 协议)。
- **副作用 GET**：写者门控只拦变更方法,所以两个会写状态的"读"另行处理:`GET /api/recommendations` 的空历史 bootstrap `serve()`(写推荐行 / 标记 shown)在 init 期跳过;`GET /api/sources/*/next-task` 在 init 期只派发 init-owned 任务(`next_pending(only_ids=…)`),避免陈旧任务饿死本轮采集器。
- **后台循环**：`background_llm_work_allowed()`(account_sync / startup one-shot)+ `ContinuousRefreshController._llm_work_allowed()`(连续 refresh / soul pipeline / producer,经注入的 `init_active_check`)在 init 期一律返回 False。init 自身不受影响——它直调 `soul_engine` / `run_init_backfill`,二者都不查该 gate。安装包 `/setup/` 第一页保存 LLM 配置会额外用 `PUT /api/config {suppress_background_llm_work:true}` 暂停配置热重载后的后台 LLM 循环和 post-reload 探针 / 预热，避免用户还没在第二页确认来源就生成画像或兴趣探针；init wrapper 终态后恢复后台循环。
- **cookie 例外**：`/api/bilibili/cookie` 在 init 期间:同值 200 no-op、异值 409(均不 validate / 不 rebuild,避免换掉正在用的客户端)。
- **task-result 例外**：`/api/sources/*/task-result` 放行,但 handler 在 init 期**跳过所有发现池写**;仅对 **init-owned**(`is_owned_bootstrap_task`)结果走 propagate(经既有 bootstrap-key 去重),并跳过增量画像管线(`_ingest_profile_update_events`)——新画像由 stage 2/3 从采集事件统一构建。
- **热重载豁免**：`rebuild_from_config` 的 `cancel_all(exclude={"guided_init"})` 让 init 任务不被配置热重载取消。

> 该门控经 9 轮 Codex 对抗验收收敛(2 high + 多 medium 修复),最终 PASS。唯一已知遗留是 bootstrap-key 去重的非原子窗口(load→propagate→mark),为 **gui-init 之前就存在**的共享 task-result 行为、低概率、轻影响,列为独立硬化 follow-up。

## 图形 UI（extension / web）

推荐 tab 未初始化空状态给「开始初始化」面板：数据来源勾选（v0.3.118+ B 站默认勾选但可取消，小红书 / 抖音 / YouTube / X / 知乎 / Reddit 一样可选，至少保留一个数据来源；配「需在本浏览器登录目标平台」文案）+ 按钮（点击驱动校验：点击时拉 `/api/init-status`，一个来源都没勾 → 提示「至少勾选一个数据来源」，勾选的小红书 / 抖音 / YouTube / X / 知乎 / Reddit 会作为本轮 opt-in 并自动开启对应来源，勾了 B 站但未登录 → 提示登录或取消勾选，前置未通过 → 展示前置清单 + 原因、不启动；全通过才带所选 `sources` 启动）+ 启动后进度条，详见 [extension 模块文档](extension.md)。桌面 `/setup` 与 `/web` 会按 `embedding_required` 把向量模型显示为硬前置或可降级项。DOM 无关逻辑在 `extension/popup/popup-init-control.js`，单测在 `extension/tests/init-control.test.ts`。

桌面 Web 对齐同一套交互：安装包首启 `/setup/` 从「连接 AI → 连接 B站」后进入第 3 步「初始化画像和推荐池」。第一步把 provider、API Key、Base URL 和模型名作为普通字段保存，只热重载配置，不启动画像 / 探针 / 补池；第二步展示同款来源勾选、前置清单、`POST /api/init` 启动和 `runtime-stream`/轮询进度，用户点击「开始初始化」后才真正进入四阶段流水线。PC 侧完成态不是单纯的 `init-status.initialized=true`：`/setup/` 和 `/web` 收到 `init_completed` 后还会读取 `/api/runtime-status`，只有 `pool_available_count>0` 或已有推荐数时才进入完成 / 推荐体验；画像已生成但首批内容尚未入池时会继续停在「整理首轮内容池」进度态。用户跳过或后来直接打开 `/web` 时，推荐网格仅在 `runtime-status.initialized=false` 且没有插件同款“初始化后信号”（推荐数、候选池可用数、待整理数、最近发现 / 补货数）时渲染同款「开始初始化」面板，不再提示去命令行跑 init，也不展示示例推荐卡。

## 测试

- `tests/test_init_coordinator.py` — 协调器生命周期 / 单飞 / 并行 stage / reconcile / 取消 / 接线 / `/api/init-status` 形状 / 门控后台暂停。
- `tests/test_init_prereqs.py` — 前置探测 TTL / 乐观超时。
- `tests/test_database.py` — `init_runs` CAS / 白名单列 / reconcile。
- `tests/test_api_app.py::TestGuidedInitEndpoints` — `/api/init`、`/api/init/cancel` 守门（403 / 409 各路径、复位不留 stuck 行）+ 写者门控（events 409 / cookie no-op / task-result 放行）+ 真实 `/api/init` handler 通过 `InitCoordinator` 向 `/api/runtime-stream` 发 `init_progress` / `init_completed` 的后端契约。
- `tests/test_cli.py` — `openbiliclaw init` 全回归（共享流水线零回归）。
- `extension/tests/init-control.test.ts` — 清单 / 按钮态 / 进度状态机纯函数。
- `tests/test_web_guided_init.py` — 安装包 `/setup/` 与桌面 `/web` 未初始化空状态的 guided-init 接线静态合约。
- `tests/test_web_guided_init_e2e.py` — Playwright 驱动真实 `/setup/` 与 `/web` 页面，stub 外部 HTTP 响应来覆盖浏览器交互：成功进度、前置失败、启动冲突、终态重试、runtime-stream 静默 watchdog，PC setup / Web 等首批 `pool_available_count>0` 后才完成，以及 PC Web 与插件一致的未初始化入口判断（已有推荐 / 候选池信号时不再弹引导）；CI 的 `web-guided-init-e2e` job 安装 `[browser]` extra + Chromium 后单独运行。
- 完整真号 GUI init（插件推荐 tab → 前置清单 → 开始 → 进度 → 画像 → 推荐）列入用户手测 DoD。
