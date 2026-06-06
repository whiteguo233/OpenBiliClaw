# GUI 引导初始化 SPEC

**Created:** 2026-06-06
**Status:** **CONVERGED**（Codex 对抗 review R1–R7;R7 判 **SHIP**,0 spec 级缺陷;轨迹:R1 4 根本 blocker → R2 2 → R4 9 → R5 5 → R6 4 二阶边界 → R7 0)。可进入实现计划。
**Scope:** 打包应用(.dmg/.exe)与浏览器插件用户的「首次初始化」改为 GUI 按钮触发 + 前置检查引导,不再要求终端跑 `openbiliclaw init`
**Depends on / 关联:** `feat/desktop-installer-wizard` 分支(`/setup` 向导、`packaging/entry.py`);与 `docs/plans/2026-06-06-human-install-wizard-design.md`(命令行一句话安装向导)**互补不重叠**——那条线管 CLI 安装路径,本 spec 管 GUI/打包/插件路径。

## Goal

让没有终端的用户(双击装了 `.dmg`/`.exe`,或只用浏览器插件)能**在界面上一键完成首次初始化**:

- 未初始化时,UI 显示一个明确的「开始初始化」入口,而不是「先跑 `openbiliclaw init`」这种命令行指引。
- 点初始化前,**清楚列出前置条件并实时显示是否满足**:① B站 已登录 ② LLM provider 已配好 ③(可选)想接入的平台已在配置里开启。前置没满足时按钮置灰,并给出去哪儿补的指引。
- 初始化是个 2–5 分钟的四阶段重操作,UI 要**显示分阶段进度**,完成后自动进入主界面。
- 复用既有 `openbiliclaw init` 的核心流水线,**不复制**灵魂引擎/发现池逻辑。

核心原则:**GUI init 只负责「把 CLI init 已有的四阶段流水线搬到界面上触发 + 可视化」**,不新增画像/发现算法,不改 CLI 既有交互安装行为。

## Background（已核对源码）

**CLI `init` 现状**(`src/openbiliclaw/cli.py:4147` `init` 命令):四阶段,`console` 打印进度,预计 2–5 分钟:

1. **1/4 拉数据**(`asyncio.run(_fetch_all_data())`,约 4360 行):`client.get_user_history` / `get_all_favorites` / `get_following`(B站,需 cookie),可选 xhs/dy/yt bootstrap 入队。历史为空则 `raise typer.Exit(1)`(4361)。
2. **2/4 分析偏好**:`await soul_engine.analyze_events(events, event_chunk_size=200)`(`soul/engine.py:215`,async)。
3. **3/4 生成画像 + 4/4 发现首轮池**(并行,`asyncio.run(_run_p3_p4_parallel())`,约 4609):`soul_engine.build_initial_profile(history)`(`soul/engine.py:257`,async,返回 `OnionProfile`)+ `_run_init_discovery_backfill_async(draft_profile, target_pool_count=...)`。
- 构建对象:`_prepare_init_runtime()`(`cli.py:2011`)、`_build_bilibili_client()`、`_build_memory_manager()`、`_build_soul_engine()`。常量:历史 300 / 收藏 300 / 关注 100(`cli.py:168-170`)。**注:本 spec 的行号在 R1 校正了几处明显错位(见 Interview Log),实现计划须对全部引用再 grep 核一遍。**
- **混入大量 CLI 专属交互**:网络绑定 `_ask_network_binding`、`_maybe_setup_password_in_init`、收藏/关注上限、xhs/dy/yt 的 y/n、`_persist_init_source_enabled_flags`。**这些是安装期设置,不属于 init 核心四阶段**,GUI 路径不复刻(网络绑定/密码已由 `/setup` 或设置页单独管)。

**没有触发 init 的 API**:只有 `POST /api/init-completed`(`api/app.py:1416`),它**广播 `init_completed` 事件 _并_ 触发 `runtime_controller.trigger_manual_refresh`**(`~1429/1435`),**不执行 init**——GUI init 故意不复用它(避免 stage 4 已发现后再 manual refresh 重复发现)。

**后台任务模式**(本 spec 直接复用):`@app.on_event("startup")`(`api/app.py:1613`)→ `await ctx.restart_background_tasks(app)`(`api/runtime_context.py:636`)→ `task_registry.track(name, coro)`(**registry 内部自建 task**,~651/658/665)。`ContinuousRefreshController.run_forever`(`runtime/refresh.py`)是常驻循环;**一次性 init 任务照此 `track(name, coro)`**(registry 自建 task,不另 `create_task`)。

**WebSocket 进度通道**(复用):`@app.websocket("/api/runtime-stream")`(`api/app.py:1466`);`RuntimeEventHub.publish(event: dict) -> bool`(`runtime/events.py:27`),事件是任意 `dict`,带 `type` 字段。Web/插件已在订阅它。

**「已初始化」判定**:`/api/health` 的 `profile_ready`(`api/models.py:39`)由 `soul_engine.is_profile_ready()`(`soul/engine.py:328` → `bool(memory.get_layer("soul").data)`)算出。`/api/profile-summary` 与 `/api/profile/edit-state` 都有 `initialized` 字段(profile 缺失时为 `False`,`api/app.py:1665`/`1966`)。

**前置信号现状**:
- B站 登录:无专门 health 字段;靠 `config.bilibili.cookie`(`config.py:182`)是否非空 + 运行时 `resolve_runtime_cookie()`(`api/app.py:1559`)。缺 cookie 时已会广播 `{"type":"bilibili_cookie_sync_requested"}`。
- LLM 可用:LLM registry 在启动时构建(`api/runtime_context.py:327`),失败 `RegistryBuildError` 进 degraded;无 health 字段直显(故本 spec 改用 chat 探测,见 §3)。
- 平台开启:`config.sources.{bilibili,xiaohongshu,douyin,youtube}.enabled`(`config.py:243-324`),经 `/api/config` 的 `SourcesConfigOut`(`api/models.py:731`,各 `.enabled: bool`)透出。

**本机特权端点先例**(本 spec 照抄):`gate.is_trusted_local(request)`(`api/app.py:681` → `auth.py:135`),非本机返回 `403 local_only`;`_is_public()` 白名单(`auth.py:269`)让端点绕过密码中间件;degraded 白名单(`api/app.py:840`)。`autostart-status`(读,可远程)/ `autostart/apply`(写,仅本机)是直接模板。

**插件未初始化空状态**(`extension/popup/popup.js`):画像卡(~2812 `"画像还没攒起来"` / 2813 `"先跑一遍 openbiliclaw init，再回来看看。"`)、编辑面板(~3229)、推荐 tab(`showRecommendationEmptyState`,kind `"uninitialized"`,~4593 `"还没完成初始化"` + 4594 hint)。API 走 `requestJson(path)`(`popup-api.js:50`),backend URL 由 `getBackendBaseUrl()` 解析。

## Chosen Approach

**抽共享异步流水线 + 进度回调,GUI/CLI 双调用。**

### 1. 共享 init 流水线（去交互、可回调进度）

新增 `src/openbiliclaw/runtime/init_pipeline.py`:

```python
@dataclass
class InitParams:
    bilibili_favorite_limit: int = 300
    bilibili_follow_limit: int = 100
    history_limit: int = 300
    include_xhs: bool = False
    include_douyin: bool = False
    include_youtube: bool = False
    pool_target_count: int = <现有 _INIT_POOL_TARGET_COUNT>

@dataclass
class InitProgress:
    stage: int           # 1..4
    total: int = 4
    label: str           # "拉取数据" / "分析偏好" / "生成画像" / "发现内容池"
    detail: str = ""
    done: bool = False
    error: str | None = None

async def run_guided_init(
    *, database, bilibili_client, memory, soul_engine, discovery_engine,
    runtime_controller, config, params: InitParams,
    on_progress: Callable[[InitProgress], Awaitable[None]],
) -> InitResult: ...
```

- 把 CLI init 的**四阶段核心**(fetch → `analyze_events` → `build_initial_profile` → discovery backfill)搬进 `run_guided_init`;每阶段开始/结束调 `on_progress`。
- **纯异步,内部绝不调 `asyncio.run`**(API 在运行中的事件循环里,嵌套 `asyncio.run` 会直接报 "cannot be called from a running event loop");CLI 保留**唯一一层**外层 `asyncio.run` 包装。签名必须带 `database` / `discovery_engine` / `runtime_controller`(stage 4 经它走 `_refresh_lock`),不能只复用 CLI 那套自建资源(否则要么漏掉必要 setup,要么嵌套事件循环)——见 §5。
- `cli.py init` 改为:保留它所有交互问答 + `_persist_*`,然后用「打印到 `console`」的 `on_progress` 调 `run_guided_init`(行为不变,只是核心挪位 + 加回调)。
- API 用「`event_hub.publish` 成 `init_progress` 事件 + 落库 status」的 `on_progress` 调同一个 `run_guided_init`。
- **迁移不裸搬**:`cli.py` 的既有测试与调用点同步更新;保证 `openbiliclaw init` 行为、退出码、进度文案不回归。

### 2. 后端 API（读端可远程 / 写端仅本机，照 autostart 先例）

- `GET /api/init-status`(读,可远程,degraded 可读,进 `_is_public`):返回是否已初始化、是否正在跑、当前阶段、前置清单、能否开始。
- `POST /api/init`(写,仅本机,进 `_is_public` 由 handler 自判 `is_trusted_local`,degraded 白名单):
  - 校验**硬前置**:B站 cookie 存在 + LLM 可用。不满足 → `409` + 稳定 `reason`。
  - 已在跑 → `409 already_running`(单实例由 **InitCoordinator 持久化状态**守卫,见 §5b,非内存 flag)。
  - 已初始化 → 默认 `409 already_initialized`,除非 body `{force: true}`(重建画像,显式确认 + 保留 override)。
  - 通过 → 置 `running`(落库)+ `ctx.task_registry.track("guided_init", guided_init_coro(...))`(传协程,registry 自建 task),立刻 `202` + 初始 status;**权威态读 `GET /api/init-status`**。
- `POST /api/init/cancel`(写,仅本机):中止运行中 init(逃生口,见 §5f)。
- 进度事件(`/api/runtime-stream`,仅通知):`{"type":"init_progress","run_id":...,"sequence":n,"stage":1..4,...}` → `{"type":"init_completed"}` / `{"type":"init_failed","reason":...}`(**由 API wrapper publish**;`run_guided_init` 只回调 `on_progress`,见 §5e)。
- **并发协调全见 §5(InitCoordinator)**:活后端写者门控、stage 4 经 `_refresh_lock`、状态落库 + 崩溃恢复。

### 3. 前置检查清单（用户明确要的「开始前要做什么」)

`GET /api/init-status` 把三类前置算好回传,UI 直接渲染 ✓/✗ + 指引:

| 前置 | 硬/软 | 判定来源 | 不满足时指引 |
|------|------|---------|-------------|
| B站 已登录 | **硬** | **真实校验 + TTL 缓存**:有 cookie 时调 `AuthManager.validate_cookie`(`bilibili/auth.py:95`)确认有效,**结果缓存**(成功 60s / 失败 10s / `checking` 态)——`validate_cookie` 走 ~30s 超时且可能被限频,**绝不每次 poll 都打 B站** | 装浏览器扩展自动同步 / 去设置贴 cookie |
| LLM 已配好 | **硬** | **chat 可用性探测 + 缓存**:registry 能构建只是必要非充分(qwen2.5 那种 call 时才 404,刚踩过)。照 `embedding_ready` 的 live-cached probe 模式做一个带超时 + single-flight 的 chat 探测;字段名用 `llm_ready`(真探测)而非「registry 构建成功」 | 去 `/setup` 或设置页选 provider + 填 key |
| Embedding 就绪 | 软 | 既有 `embedding_ready` 缓存探测 | 打包版默认随包 ollama,一般已就绪 |
| 想接入的平台已开启 | 软 | `config.sources.*.enabled` | 前置面板内联开关顺手开(init 启动前) |

硬前置都满足且未在跑且本机 → `can_start=true`,按钮可点;否则置灰 + 按 reason 文案解释。

### 4. UI 落点（插件优先;CTA 收敛到推荐 tab）

**入口收敛原则**(用户定):未初始化时,**只有「推荐」tab 出现「开始初始化」主入口**(用户落地的主页);「画像」「编辑」等其它空状态只显示「未初始化,去推荐页开始」的**被动提示,不重复按钮**。**已初始化后,重新初始化入口只在「设置」里**(默认拦 `already_initialized`,设置页提供「重建画像」+ 二次确认)。

- **Phase 1 — 浏览器插件(优先)**:
  - 「推荐」tab 未初始化空状态(`popup.js` ~4593 `showRecommendationEmptyState` kind `"uninitialized"`):换成「开始初始化」按钮 + 前置清单面板(B站 ✓/✗、LLM ✓/✗、平台开关)。
  - 前置面板**内联平台开关**(xhs/dy/yt):直接 `PUT /api/config` 顺手开,不跳设置页。
  - B站 登录:插件本就在 bilibili.com 自动同步 cookie,该前置一般天然满足;未满足时提示「先在 bilibili.com 登录」。
  - 按钮调 `GET /api/init-status` 渲染、`POST /api/init` 触发、订阅既有 `/api/runtime-stream` 显示阶段进度;完成后空状态消失、自动加载推荐。
  - 「画像」「编辑」空状态(`popup.js` ~2812/3229):文案由「先跑 openbiliclaw init」改为「还没初始化,去『推荐』页开始」,**不放按钮**。
  - 设置页新增「重新初始化 / 重建画像」入口(带二次确认,调 `POST /api/init {force:true}`)。
- **Phase 2 — 网页(later)**:`/setup` 向导加第 ④ 步「初始化」(前置清单 + 按钮 + 进度 → 跳 `/web`);`/web` 推荐区未初始化空状态同插件做法。纯网页用户(没装插件)若要连 B站,再考虑网页内扫码(QR API,本期不做)。

### 5. 并发与活后端安全 / InitCoordinator（Codex R1+R2 补强 — 核心)

CLI init 独占进程;API init 跑在**活后端**里,后台有连续 refresh、account sync、soul pipeline、事件摄入、**扩展周期同步 cookie** 在并发写 soul/preference/`content_cache`。引入一个 **`InitCoordinator`** 统一协调:

**(a) 持久化状态存储(崩溃安全)** — 新增 SQLite 表 `init_runs`(`storage/database.py`,照该文件既有 schema 风格),列:`run_id` / `status`(`idle|starting|running|completed|failed|cancelled`)/ `stage`(0–4)/ **`stages_json`(各 stage 的 `pending|running|ok|warning|failed` + reason,**崩溃/重连后由它重建 API shape 的 per-stage `status` 与 `warning`**,否则只存 `stage` 无法还原)** / `partial_success` / `started_at` / `updated_at` / `finished_at` / `error_reason` / `sequence`(单调)。阶段切换在 `finally` 落库。**启动 reconciliation**:boot 时把残留 `starting`/`running` 一律改判 `failed`(`error_reason="interrupted"`)——无进程能跨重启存活,杜绝卡死 `running=true`。

**(b) TOCTOU-safe 启动序 + 写者二次检查** — `POST /api/init`:① `is_trusted_local` gate(见 API Shape)→ ② **原子**把 `status` 从非 active 置 `starting`(single-flight,已 active → `409 already_running`)→ ③ 同一临界区内 revalidate 前置(B站 cookie + chat 探测)→ ④ 通过则 `ctx.task_registry.track("guided_init", guided_init_coro(...))`(**传协程,别再 `asyncio.create_task` 包一层**——`track` 自己建 task,见 `runtime_context.py:651` 用法)并置 `running`,否则回 `idle` + `409`。**但「先占坑」还不够**:已过校验、side-effect 未执行的 in-flight 写者(cookie 写在 rebuild 前 `app.py:1332`、config 写在 `_CONFIG_SAVE_LOCK` 内 `app.py:5944/5969`)仍可能 rebuild-cancel init。**故每个热重载写者在真正 side-effect 前(含 `_CONFIG_SAVE_LOCK` 内)必须再查一次 `init_active`**。锁顺序固定:先查 `init_active` → 再取 `_CONFIG_SAVE_LOCK` / `_refresh_lock`,不反向嵌套,避免死锁。

**(c) 写者门控**(`init_active = status ∈ {starting,running}`):
- **HTTP 写端 → `409 init_running`**:`PUT /api/config`、`POST /api/bilibili/cookie`(扩展自动周期同步会 rebuild-cancel init)、**与本 init run 无关的** `POST /api/sources*` 写、`POST /api/profile/edit`、手动 refresh、probe/interest promote。**两个关键例外**:
  - **放行 init 自己的 bootstrap 任务结果(绑定 run + 喂 init,不走 live drain)**:stage 1 enqueue xhs/dy/yt bootstrap 任务,结果经 `POST /api/sources/{xhs,dy,yt}/task-result`(`app.py:4805/4899/4958/5079/5089`)回来。payload 只带 `task_id` 不带 `run_id`(`app.py:4735`、`extension/src/background/xhs-task-dispatcher.ts`),故 **`InitCoordinator` 记下本 run enqueue 的 `task_id` 集合**,仅当 `task_id ∈ 该集合 且 该 run 仍 active` 才放行;**否则按无关 source 写处理(gate/忽略)——杜绝 cancel/重启后迟到的旧 task 污染新状态**(cancel/reconciliation 时退役该集合)。
  - **不与 stage 4 抢 `content_cache`**:正常 task-result 会 schedule drain(`app.py:4784`)→ `refresh.py:1291` **不持 `_refresh_lock` 直接 drain** → `candidate_pipeline.py:122` admit 进 `content_cache`,会和 §5d stage 4(持 `_refresh_lock`)竞争。故 `init_active` 时 init-owned task-result **只把结果喂进 init 的 stage 1 收集缓冲(复刻 CLI `_collect_*_bootstrap_events`)、不触发 live drain/admission**;发现池统一由 stage 4 经 `_refresh_lock` 写。
  - **cookie 同值 no-op(先比对后校验)**:`init_active` 时 `/api/bilibili/cookie` **先比 effective cookie 再说**(现有 handler 是「先 validate 后比对」`app.py:1278-1343`,init 期要反过来):同 cookie 直接 `200`、**不 validate 不 rebuild**(扩展重复同步不打断 init);不同 cookie → `409 init_running`。
- **后台循环 → 跳过本 tick + log**(它们是循环不是 handler,**不能返回 409**):连续 refresh tick、account_sync tick、soul pipeline tick、事件摄入 refresh,在 `init_active` 时检 coordinator flag 直接 skip。
- **统一封装 + 防御纵深**:soul/preference 写经 `InitCoordinator` 的运行期写锁/包装,**枚举全部写者**(含 source-task 摄入 `app.py:4804/4956/5088`、推荐点击信号、probe 提升),不留漏网;**且** init 后台任务对 `rebuild_from_config` 的取消逻辑(`runtime_context.py:286`)**豁免**——即便某热重载写者漏网,也不把 init 任务 cancel 掉(双保险)。

**(d) Stage 4 发现** — **新增** `ContinuousRefreshController.run_init_backfill(profile, target_pool_count, *, fully_parallel=True)`,**自身持 `_refresh_lock`**,复刻 CLI backfill(draft profile + 目标池 + 并行)。现有 `refresh_after_init()` 只做阈值 `refresh_if_needed`,**复刻不了**,故新建。**CLI init 的 stage 4 也改走 `run_init_backfill`**(替换 `cli.py:2058` 直接 `discovery_engine.discover`),CLI/API 走同一持锁路径,锁纪律一致。**不引入单独的 refresh「暂停」态**:连续 refresh tick 在 `init_active` 时自然 skip(见 c),stage 4 持 `_refresh_lock` 串行化;init 崩溃时 `init_active` 经 (a) 启动 reconciliation 自动清除,refresh 自然恢复,无「暂停卡死」。

**(e) `run_guided_init` 纯异步 + 无*编排*副作用** — 全程 API 事件循环内,内部零 `asyncio.run`;**它当然会做领域写**(拉数据 / 写 preference / soul / 发现池——那本就是它的活),但**不做编排副作用**:不自己 publish 事件、不写 `init_runs` 状态库、`on_progress` 之外不旁路。完成/失败经**终态 `on_progress`**(`done=True` / `error=...`)上报,**由调用方 wrapper 落地**——API wrapper 把进度/终态翻成 `init_progress`/`init_completed`/`init_failed` 事件 + 落 `init_runs`;CLI wrapper 打印 console。**stages 3+4 保持并行**(复刻 CLI `_run_p3_p4_parallel`,`cli.py:4564/4576`),勿退化成串行。**并行进度契约**:`stages[]` 为权威(3、4 可同时 `running`);标量 `current_stage` = 仍在跑的**最小** stage 号;两并发阶段的进度 / `sequence` / `init_runs` 写经 InitCoordinator 内**单一 per-run 串行点**原子更新,杜绝并发写状态库与 `sequence` 乱序。CLI 保留唯一外层 `asyncio.run` 包装(见 §1)。

**(f) 取消 + 超时 + 协作式取消(配置锁逃生口)** — 机制:`InitCoordinator` 持 `current_task` + `cancel_current_run(run_id)`;`POST /api/init/cancel`(仅本机)调它。**分工**(避免与 §5e「`run_guided_init` 不写状态库」打架):`run_guided_init` 只**协作式响应 `asyncio.CancelledError`**——确保它持有的锁(如 stage 4 的 `_refresh_lock`,在 `run_init_backfill` 自己的 `finally`)被释放,然后 **re-raise,不碰状态库**;**由 API wrapper / `InitCoordinator` 捕获 `CancelledError`/超时,在 `finally` 把 `init_runs` 落 `cancelled`/`failed`**(状态库单一写者)。每 stage 超时即 cancel,走同一路径。热重载豁免(§5c/§5b 的「双保险」)走**同一 `current_task` 句柄**实现——`rebuild_from_config(exclude=current_task)` 或给 `BackgroundTaskRegistry` 加具名豁免/取消语义(`task_registry.py` 现仅有 hot-reload 全量取消,需扩展)。否则「init 中途锁 `PUT /api/config` + v1 不重试」会让用户既改不了配置也退不出。

## API Shape

`GET /api/init-status`(读端,可远程,无敏感字段)。**这是进度的权威来源**——刚打开的 UI 先拉一次取当前态(含历史进度),再订阅 WebSocket 增量;`RuntimeEventHub` 不重放,连接前的事件只能从这里补。

```json
{
  "initialized": false,
  "running": false,
  "run_id": null,
  "sequence": 0,
  "current_stage": 0,
  "total_stages": 4,
  "stages": [
    {"n": 1, "label": "拉取数据", "status": "pending"},
    {"n": 2, "label": "分析偏好", "status": "pending"},
    {"n": 3, "label": "生成画像", "status": "pending"},
    {"n": 4, "label": "发现内容池", "status": "pending"}
  ],
  "partial_success": false,
  "can_start": false,
  "can_manage": true,
  "prerequisites": {
    "bilibili_logged_in": false,
    "bilibili_check": "checking",
    "llm_ready": true,
    "embedding_ready": true,
    "enabled_platforms": ["bilibili"]
  },
  "reason": "bilibili_not_logged_in",
  "detail": "还没检测到 B站 登录"
}
```

`POST /api/init(request: Request)`(写端,仅本机;body 可选 `{"force": false}`)。**用裸 `request: Request`(不用 Pydantic body 绑定)**:handler **第一步** `gate.is_trusted_local(request)`,**再** `await request.json()` 手动解析——任何副作用之前。非本机 `403 local_only`。进 `_is_public` 是**有意**的(handler 自身即 gate);CSRF 在 auth 之后,公开白名单同时绕过 CSRF,**本机 gate 是唯一防线**,必须在最前(照 autostart 本机写端先例 `app.py:5214`)。

| 场景 | HTTP | reason |
|------|------|--------|
| 受理,开始后台 init | `202` | `none` |
| 非本机来源 | `403` | `local_only` |
| 缺 B站 登录 | `409` | `bilibili_not_logged_in` |
| 缺 LLM(chat 探测失败) | `409` | `llm_not_ready` |
| 已在跑 | `409` | `already_running` |
| 已初始化且未 force | `409` | `already_initialized` |
| Docker/只读/不支持 | `409` | `unsupported_runtime` |

`POST /api/init/cancel(request: Request)`(写端,仅本机):中止运行中的 init(置 `cancelled`,清 `init_active`),无运行中 → `409 not_running`。给被 init 锁住 `PUT /api/config` 的用户一个逃生口。

进度(WebSocket `/api/runtime-stream`,**仅通知,不权威**):`{"type":"init_progress","run_id":...,"sequence":n,"stage":1..4,"label":...,"detail":...}` → `{"type":"init_completed"}` 或 `{"type":"init_failed","reason":...}`。

**不复用 `POST /api/init-completed` 的副作用**:该端点除广播事件外还会 `runtime_controller.trigger_manual_refresh`(`app.py` ~1435);GUI init 的 stage 4 已经做过发现,再触发 manual refresh 会**重复发现**。所以由 **API wrapper** 在终态 publish `init_completed`(`run_guided_init` 只回调 `on_progress`,见 §5e),**不调**旧端点。

## Status Values & Reasons（契约的一部分）

稳定 `reason`:`none` / `local_only` / `bilibili_not_logged_in` / `llm_not_ready` / `already_running` / `already_initialized` / `unsupported_runtime` / `init_failed` / `init_running`(并发写/配置操作被拒)/ `discovery_partial`(stage 4 发现部分失败,非整体失败)/ `not_running`(cancel 时无运行中)。
`current_stage`:`0`(未开始)/`1`拉数据/`2`分析/`3`画像/`4`发现池。
每个 stage 的 `status`:`pending` / `running` / `ok` / `warning`(部分成功,如收藏/关注拉取失败但历史够)/ `failed`。
`partial_success`:任一 stage 为 `warning`,或画像已生成但发现池「部分完成」时为 `true`(此时 `initialized=true`,推荐池后续靠常规 refresh 补)。
`bilibili_check`:`ok` / `failed` / `checking`(缓存未命中、正在校验)。

## Boundaries

**In scope** — 分两期(见 §4):
- **Phase 1(本期)**:共享 init 流水线抽取 + 进度回调;后端 InitCoordinator + `GET /api/init-status` + `POST /api/init` + `POST /api/init/cancel`(本机后台任务 + 进度事件 + 前置校验 + 并发协调);**插件**「推荐」tab CTA + 前置清单 + 进度、画像/编辑被动提示、设置页重建入口;文档/测试同步。
- **Phase 2(后续)**:网页 `/setup` 第④步 + `/web` 空状态(复用同一组后端端点)。

**Out of scope:**
- CLI 一句话安装向导(归 `human-install-wizard` 那条线)。
- 画像/发现算法本身(仅复用)。
- 网络绑定(0.0.0.0)、登录密码设置——GUI init 不再问,沿用 `/setup`/设置页/既有配置。
- 自动重试/断点续跑 init(v1 失败就整体重来,给清晰错误)。
- 远程触发 init(仅本机)。
- Docker 运行时的 GUI init(`unsupported_runtime`;容器用 CLI/编排)。

## Constraints

- `POST /api/init` **仅本机**(`is_trusted_local`),非本机 `403 local_only`;`GET /api/init-status` 可远程只读、不返回 cookie/key。
- **单实例**:同时只允许一个 init 后台任务;重复 `POST` 返回 `already_running`。
- **硬前置门控**:无 B站 cookie 或 LLM 不可用时不启动 init(避免 CLI 里「历史为空 Exit(1)」式的中途失败,改为前置拦截 + 明确 reason)。
- init 失败**不得**让后端崩;发 `init_failed` 事件 + status 回到可重试态。
- 进度事件不含敏感字段;错误只给稳定 reason,详细 stderr 仅本地日志。
- 两端点都进 `_is_public` + degraded 白名单(否则核心未起来时被 401/503,用户没法初始化)。
- `run_guided_init` 抽取**不得回归** `openbiliclaw init` 的现有行为/退出码/文案;CLI 既有测试纳入更新范围。
- 复用既有 `init_completed` 事件类型,不另造完成信号(Web/插件已监听);但 GUI init **自己 publish**,不调带 manual-refresh 副作用的 `/api/init-completed` 端点。
- **init 运行中锁写(分流,见 §5c)**:`init_active` 时——**HTTP 写端**(`PUT /api/config`、source 开关、`POST /api/profile/edit`、手动 refresh、cookie 异值)返回 `409 init_running`;**后台循环**(连续 refresh / account-sync / soul pipeline tick / 事件摄入)**跳过本 tick + log**(它们不是 HTTP handler,**不能**返回 409)。例外:init 自己的 bootstrap task-result 按 `task_id ∈ 本 run enqueue 集合` 放行(非 run_id)、同值 cookie no-op。⇒ 平台开关必须 init 前设好。
- **Stage 4 经 `_refresh_lock`**:绝不直接 `discovery_engine.discover`;init 期间让位连续 refresh,二者不并发写 `content_cache`。
- **Docker/不可写**:复用 `docker_runtime.is_running_in_container()` 判 Docker,另查 data/config 可写性,不支持 `unsupported_runtime`。
- **外部探测带 TTL 缓存**:chat 可用性 + B站 `validate_cookie` 都缓存(B站 成功 60s/失败 10s),`GET /api/init-status` 不在每次 poll 同步打外部服务。
- **`force` 重建策略**:重算 soul + preference,**保留用户手动 override**(`memory/manager.py` overrides 层),不动 `content_cache`;UI 二次确认 + 明示「会重算画像」。
- **i18n**:API 只回稳定 reason code,CN/EN 文案由 UI 拥有(插件现硬编码中文,`config.language` 存在)。
- **部分失败语义**:画像成功但发现池失败 ⇒ stage 4 `status:"warning"` + `reason:"discovery_partial"`、`initialized=true`+`partial_success=true`(**区别于** stages 1–3 硬失败的整体 `init_failed`);B站 收藏/关注拉取失败是 `warning`(历史够即继续);**历史为空才整体 `init_failed`**。任何整体失败发 `init_failed` + 落库 + 不崩、可重试 / 可 cancel。

## Acceptance Criteria

- [ ] 未初始化时:插件「推荐」tab 显示「开始初始化」按钮 + 前置清单;画像/编辑空状态只被动提示「去推荐页开始」(无按钮);设置页有「重建画像」入口;**全程不再出现「openbiliclaw init」命令行文案**。
- [ ] 前置未满足(无 B站 / 无 LLM)时按钮置灰,`reason`/`detail` 文案正确;补齐后(配置后)清单实时变绿、按钮可点。
- [ ] 点「开始初始化」→ `POST /api/init` 返回 `202` → WebSocket 依次收到 `init_progress` stage 1..4 → `init_completed`;UI 进度条推进、完成跳 `/web`。
- [ ] init 跑通后 `/api/health` `profile_ready=true`、`/api/profile-summary` `initialized=true`、插件空状态消失。
- [ ] 已在跑时再次 `POST /api/init` 返回 `409 already_running`;已初始化且未 `force` 返回 `409 already_initialized`。
- [ ] 非本机 `POST /api/init` 返回 `403 local_only`;`GET /api/init-status` 远程可读、`can_manage=false`、不含敏感字段。
- [ ] init 失败发 `init_failed`、后端不崩、可重试;Docker 运行时 `unsupported_runtime`。
- [ ] `openbiliclaw init`(CLI)行为/退出码/进度文案无回归;`run_guided_init` 被 CLI 与 API 共用,无逻辑复制,内部零 `asyncio.run`。
- [ ] init 运行中 `PUT /api/config` / source 开关 / profile edit / 手动 refresh 被拒 `409 init_running`,init 任务不被热重载 cancel。
- [ ] stage 4 发现经 `_refresh_lock`,与连续 refresh 不重复写 `content_cache`;init 期间连续 refresh 让位。
- [ ] 服务器在 init 中途重启 / 热重载后,`GET /api/init-status` 报 `running=false` + 上次 partial/failed,不卡死 `running=true`。
- [ ] 刚连上的 UI 先拉 `GET /api/init-status` 拿当前 stage + `sequence` 再订阅 WS,不漏连接前进度。
- [ ] 失效 B站 cookie 不致 `GET /api/init-status` 每次打 B站(命中 TTL 缓存);LLM chat 探测失败 ⇒ `llm_ready=false` 且 `POST /api/init` 拒 `llm_not_ready`。
- [ ] 画像成功但发现池失败 ⇒ `initialized=true`+`partial_success=true`;B站 历史为空 ⇒ 整体 `failed` + 明确文案;init 失败后端不崩、可重试。
- [ ] grep 全部 extension/web 源(`extension/popup/popup.js`、`extension/popup/popup-helpers.js`、`extension/popup/popup.html`、`src/openbiliclaw/web/js/views/profile.js`、`src/openbiliclaw/web/desktop/assets/js/app.js`)无残留「openbiliclaw init / 先跑 / 先运行」面向用户文案。
- [ ] Docker 运行时 `POST /api/init` 返回 `unsupported_runtime`(复用 `is_running_in_container()`)。
- [ ] **TOCTOU**:`POST /api/init` 先原子置 `starting` 再在临界区内 revalidate 前置;并发第二个 `POST` 得 `409 already_running`。
- [ ] **cookie 门控**:init 运行中 `POST /api/bilibili/cookie` 收到**同一** cookie → no-op `200`(不 cancel init);收到**不同** cookie → `409 init_running`。
- [ ] **后台循环**在 `init_active` 时 skip 本 tick 并 log(不是返回 409);HTTP 写端才返回 `409 init_running`。
- [ ] stage 4 走**新增** `ContinuousRefreshController.run_init_backfill(...)`(持 `_refresh_lock`),不直接 `discovery_engine.discover`。
- [ ] `init_runs` 表落库往返正确;**启动 reconciliation** 把残留 `starting/running` 改判 `failed("interrupted")`,`GET /api/init-status` 不报卡死的 `running=true`。
- [ ] `POST /api/init/cancel` 中止运行中 init(置 `cancelled`、清 `init_active`、解锁 config);无运行中 → `409 not_running`。
- [ ] 定向 Python 测试 + 插件测试通过;文档同步。

## Resolved Decisions（2026-06-06,用户已拍板)

1. **重新初始化 + 入口收敛**:默认拦截 `409 already_initialized`,需 `force:true` 才重建;**重建入口只在设置页**(带二次确认)。未初始化的「开始初始化」CTA **只出现在「推荐」tab**;画像/编辑等其它空状态只被动提示「去推荐页开始」,**不重复按钮**。
2. **平台开关**:**做进 init 前置面板**——xhs/dy/yt 内联开关,直接 `PUT /api/config` 顺手开,不跳设置页。
3. **B站 登录判定**:**真实校验**——有 cookie 时调 `AuthManager.validate_cookie`(`bilibili/auth.py:95`)问一次 B站确认有效,不只看非空。
4. **优先级**:**插件优先(Phase 1)**;网页 `/setup` + `/web` 放 Phase 2。
5. **B站 连接方式**:插件优先 ⇒ 由插件既有「在 bilibili.com 自动同步 cookie」机制满足,**本期不做网页内扫码登录(QR API)**,留待将来纯网页路径。

## Docs to Sync（实现 PR 必带）

- 本文件 `docs/specs/gui-init.md`。
- `docs/modules/`(新建 `init.md` 或并入 `runtime.md`):`run_guided_init` 共享流水线 + 后台任务 + 两端点。
- `docs/modules/extension.md`:插件三处空状态改 GUI init。
- `docs/changelog.md`:当前版本块加 bullet。
- 架构同步(新增 `runtime/init_pipeline.py` + 后端→soul/discovery 触发边):`docs/architecture.md`、`docs/spec.md` §3 图、`README.md`/`README_EN.md` 顶部图。
- 若改 `[sources].*.enabled` 写入路径 → `docs/modules/config.md`。

## Interview Log

- 2026-06-06:用户装了打包应用,UI 提示「还没完成初始化,先运行 openbiliclaw init」。诉求:GUI 入口 + 初始化按钮 + 开始前清楚列出前置(各平台登录、LLM provider 配置、想要的平台开启)。核对源码确认:init 是 CLI-only 的四阶段重操作,无触发 API。决议:抽共享异步流水线 + 进度回调,新增 `GET /api/init-status`(可远程读)+ `POST /api/init`(仅本机后台任务),UI 落 `/setup` 末步 + `/web` 空状态 + 插件三处。与 CLI 一句话安装向导(`human-install-wizard`)互补不重叠。
- 2026-06-06(用户拍板 5 灰区):默认拦重复 init(force 重建,入口只在设置)、CTA 只在推荐 tab、平台开关内联前置面板、B站 真校验、插件优先、不做扫码 QR。
- 2026-06-06(review R1 / Codex 对抗审查 → 判 **BLOCKED**,4 blocker + 7 major + 4 minor + 2 nit,全部采纳):核心命门——CLI init 独占进程,API init 跑在**活后端**里,与连续 refresh / account sync / soul pipeline / 事件摄入并发写同一份画像与 `content_cache`。修订:① 新增 §5「并发与活后端安全」(init 模式 + 运行期写锁、stage 4 经 `_refresh_lock`、运行中 `PUT /api/config` 等拒 `409 init_running` 防热重载 cancel、状态落库 + 崩溃恢复);② `run_guided_init` 补 `database`/`discovery_engine`/`runtime_controller` 依赖、内部零 `asyncio.run`;③ 前置探测带 TTL 缓存(chat 真探测 + B站 `validate_cookie` 缓存,不每 poll 打外部);④ `GET /api/init-status` 升为进度**权威源**(`run_id`/`sequence`/per-stage status/`partial_success`),WS 仅通知;⑤ `POST /api/init` 副作用前显式 `is_trusted_local`(公开白名单绕过 CSRF,本机 gate 是唯一防线);⑥ GUI init 自 publish 完成事件,不复用带 manual-refresh 副作用的 `/api/init-completed`;⑦ 补部分失败语义、Docker `is_running_in_container`、i18n reason-code、`force` 保留 override;⑧ UI 验收 grep 全源去 init 文案;⑨ 校正错位行号(`_prepare_init_runtime` `cli.py:2011`、`config.bilibili.cookie` `config.py:182`、LLM 构建 `runtime_context.py:327`)。
- 2026-06-06(review R2 / Codex 复审 → 仍判 **BLOCKED**:R1 的 4 RESOLVED + 多项 PARTIAL,另挖 2 新 blocker + 8 major,全部采纳):R1 并发修复只做一半,升级为完整定义的 **InitCoordinator**。修订:① §5 重写为 InitCoordinator——(a) 新增 `init_runs` SQLite 表 + 启动 reconciliation(残留 `starting/running` 改判 `failed`),(b) TOCTOU-safe 启动序「先原子占 `starting` → 临界区内 revalidate 前置 → 起任务」,(c) 写者门控分流:**HTTP 写端 409 / 后台循环 skip-tick**(后台循环不能返 409),且**纳入 `/api/bilibili/cookie`**(扩展自动同步会 rebuild-cancel init;同 cookie no-op、异 cookie 409),枚举全部画像写者(source-task/点击/probe),(d) **新增** `ContinuousRefreshController.run_init_backfill(profile,target,fully_parallel)` 持 `_refresh_lock`(现有 `refresh_after_init` 复刻不了),不单独做 refresh「暂停」(靠 init_active skip + 崩溃后 reconciliation 自愈),(f) 新增 `POST /api/init/cancel` + stage 超时(配置锁逃生口);② `POST /api/init(request: Request)` 裸 Request、gate 在 body 解析前;③ 部分失败精化(stage 4 失败 = `warning`+`discovery_partial`,区别 `init_failed`);④ 校正 `app.py` degraded 白名单 840、autostart gate 5214;⑤ Acceptance grep 路径用 repo 实际前缀 + 增 TOCTOU/cookie-noop/后台 skip/run_init_backfill/reconciliation/cancel 验收项。Codex 审计:除上述两处行号外其余引用 VALID。
- 2026-06-07(review R3 / Codex fresh run **跑偏**):该轮误把「spec 有没有被实现成代码」当评审标准,全部判 PARTIAL「未实现」+ blocker「GUI init 仍是 spec」——对实现前的 spec 是废话,**判为 misframe、不采纳其框架**;但其两个真实代码观察已并入(CLI `cli.py:2058` 直接 discover 绕控制器锁 → CLI stage 4 也走 `run_init_backfill`;`runtime_context.py:286` rebuild 取消任务 → init 任务对 rebuild 豁免)。
- 2026-06-07(review R4 / Codex **纠正框架后**复审 → 判 **REVISE**,9 项真设计缺陷,全部采纳):① `task_registry.track` 形状错(传协程,别 `asyncio.create_task` 再包)② cancel/豁免只定行为没定机制 → 补 `InitCoordinator.current_task`+`cancel_current_run`+`rebuild(exclude=)` ③ Background 对 `/api/init-completed` 漏了 manual-refresh 副作用 → 修 ④ 完成事件 publish 归属矛盾 → 统一「`run_guided_init` 只 on_progress、API wrapper publish + 落库」⑤ gate `POST /api/sources*` 会卡死 init 自己的 xhs/dy/yt bootstrap task-result → 按 `run_id` 放行 ⑥ TOCTOU 还有在途写者漏洞 → side-effect 前(含 `_CONFIG_SAVE_LOCK` 内)二次查 `init_active` + 固定锁顺序 ⑦ 同值 cookie no-op 要「先比对后校验」(现 handler 反了)⑧ 丢了 CLI 的 P3/P4 并行 → 明确保留 ⑨ 后台循环「409」与「不能 409」自相矛盾 → 分流 HTTP-409 / 后台-skip。
- 2026-06-07(review R5 / Codex 纠正框架复审 → 判 **REVISE**,R4 的 9 项 8 RESOLVED + 1 PARTIAL,另 5 项更细缺陷,全部采纳):① Background 残留 `track(asyncio.create_task)` 错句(§2/§5 改了漏了 Background)→ 修 ② 取消时状态库写者矛盾(§5e 说 run_guided_init 不写库、§5f 又说它 finally 落 cancelled)→ 明确 **wrapper/Coordinator 单一写状态库**、`run_guided_init` 只释放锁 re-raise ③ `init_runs` 无 per-stage 状态列、崩溃后还原不出 `warning` → 加 `stages_json` ④ source task-result 的 `run_id` 关联无定义(扩展只回 `task_id`)→ 改为 **Coordinator 记下本 run enqueue 的 `task_id` 集合放行**,不改扩展协议 ⑤ §5e「无副作用」自相矛盾(它本就做领域写)→ 改「无*编排*副作用」。收敛趋势:R1 4 根本 blocker → R2 2 → R4 9 设计缺陷 → R5 5 项细节一致性/完整性。
- 2026-06-07(review R6 → 判 **REVISE**,R5 的 5 项 4 RESOLVED + 1 PARTIAL,另 4 项,全部采纳):① Constraints 漏改 `run_id`→`task_id ∈ enqueue 集合` ② cancel/重启后迟到的 init-owned task-result 会污染新状态 → 绑定 run 生命周期、退役集合、拒迟到 ③ 并行 stage 3/4 进度契约未定 → `stages[]` 权威 + `current_stage`=最小在跑 + 单一串行点原子写 ④ init-owned task-result 的 drain 绕过 `_refresh_lock` 与 stage 4 抢 `content_cache` → init 期只喂 init 收集缓冲、不触发 live drain。findings 2/4 是正确性风险(迟到写、cache race),均为前轮「放行 bootstrap + 3/4 并行」修复的二阶交互。
- 2026-06-07(review R7 → 判 **SHIP / CONVERGED**):R6 的 4 项全 RESOLVED;**无 (A) spec 级缺陷**,仅 2 个 (B) nit(Background `track` 残留「+ create_task」措辞、Boundaries 未标 Phase 1/2 范围)已修。7 轮对抗收敛完成,spec 判定通过,可进入实现计划。后续灰区(`force` override 细节、stage 超时具体值、纯网页扫码 QR)留实现计划/Phase 2 处理。
