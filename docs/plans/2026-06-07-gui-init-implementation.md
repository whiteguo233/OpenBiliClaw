# GUI 引导初始化 — 实现计划 (Implementation Plan, Phase 1)

**Created:** 2026-06-07
**Spec:** [`docs/specs/gui-init.md`](../specs/gui-init.md)（**CONVERGED**,Codex 对抗 review R1–R7,R7 判 SHIP / 0 spec 级缺陷)
**Status:** 待执行 — **CONVERGED**（Codex plan review R1–R3;R3 判 **SHIP**,0 A 级缺陷;2 nit 已修)。A1 已实现,可从 A2 开干。
**Reviews:** R1(coverage + buildability + Codex,8 findings):6 A 级——coordinator 接线/启动 reconcile 缺任务(→新增 A3)、`task_registry` exclude 该单列(→新增 A4)、D1 在 E2 前不可独测(→ fixture 经 coordinator/DB 置 active run)、B2 低估 CLI 的 `time.sleep` 采集器 + per-event `asyncio.run` 耦合(→ B2 加去同步/去 asyncio.run 子任务)、bootstrap handler 会直接写画像需截流(→ D1 截流到 init 缓冲)+ 2 B 级(行号校正、A1 方法名对齐 `get_latest_init_run`/`update_init_run`)。全部 incorporated;任务数 11→13。 R2(复核,8 findings):R1 的 8 项全 RESOLVED,另挖 7 A + 1 B——coordinator 持旧 `runtime_controller` 引用(→ ctx 惰性取)、rejection 路径留 stuck `starting` 行(→ 廉价前置先于占坑 + 复位)、cancel 没存 task 句柄(→ `attach_task`)、bootstrap task_id 注册桥未定义(→ `on_task_enqueued` 回调 + `register_enqueued_task`)、`/api/feedback`+`/api/recommendation-click` 漏 gate、`embedding_ready` 漏前置、`unsupported_runtime` 漏 data/config 可写性、reconcile 须在 degraded 早返回前。全部 incorporated。 R3(收口):R2 的 8 项全 RESOLVED,仅 2 B nit(D1 result handoff 点明 `merge_result`、F1 清单补 embedding)已修,**判 SHIP**。收敛轨迹:R1 8(6 blocking)→ R2 8(7 A,finer)→ R3 0 A + 2 nit。
**Scope:** **Phase 1** = 后端(共享 init 流水线 + InitCoordinator + 3 端点)+ **浏览器插件**(推荐 tab CTA + 前置清单 + 进度)。**Phase 2**(网页 `/setup` 第④步 + `/web` 空状态)不在本计划,复用同一组端点后做。
**测试限制(诚实声明):** init 完整跑通需**真 B站 cookie + 真 LLM key**(本机无有效凭据)。本计划能自动验证的:CLI init 无回归、InitCoordinator 状态机/并发/崩溃恢复、前置门控、status/cancel、写者 409/skip、插件渲染逻辑;**完整画像生成的端到端需用户用真号手测**(列入 DoD)。

> 把 spec 的 §1–§5 拆成**依赖有序、每个 = 一个原子 commit** 的任务。最高风险:**B2 把 CLI init 四阶段抽成共享异步函数**(动核心 soul/发现,不能回归 `openbiliclaw init`)+ **D1 写者门控**(碰一堆既有端点/循环)。这两个测试最重、review 最细。

## 0. 总览:依赖顺序

```
A1. init_runs 表 + reconcile (§5a)  ✅ 已实现 (commit 7fc00bd)
A2. InitCoordinator 类 (§5a/b/f)                        ← A1
A3. 接 InitCoordinator 进 RuntimeContext + boot 调       ← A2  (wiring,review R1 新增)
    reconcile + 暴露 ctx.init_coordinator + 传依赖
A4. task_registry 具名取消/exclude + rebuild 豁免 init   ← (§5c/f 前置;D1+E2 都要,review R1 新增)
B1. run_init_backfill(持 _refresh_lock) (§5d)
B2. 抽 run_guided_init(含异步 bootstrap 收集,最高风险) (§1/§5e)  ← B1
C1. cached 前置探测(chat/B站/platforms) (§3)
D1. 写者门控(HTTP-409 / 后台-skip / cookie / bootstrap 截流) (§5c)  ← A3,A4
E1. GET /api/init-status (§API/§3)                      ← A3,C1
E2. POST /api/init + /cancel (§2/§5b/§5f)               ← A3,A4,B2,D1,E1
F1. 插件 UI (§4)                                         ← E1,E2
G1. 验收矩阵 ; G2. 文档同步(CLAUDE.md 强制)
```

- **关键路径**:A1 → A2 → A3 → D1 → E2 → F1;并行支线 A4、B1→B2、C1。
- **A1 已实现**;A2→A3 串行(都碰 `runtime_context.py`);A4/B1/C1 可与 A2/A3 并行起。
- **粗估**:A2≈0.5d、A3≈0.5d、A4≈0.5d、B1≈0.5d、**B2≈2.5d(重:核心重构 + 去 `time.sleep`/`asyncio.run` + CLI 回归)**、C1≈0.5d、**D1≈2d(碰多端点/循环 + bootstrap 截流)**、E1≈0.5d、E2≈1d、**F1≈1.5d(插件)**、G1≈0.5d、G2≈0.5d。合计 ~11 人日。

## 1. 任务分解

### Phase A — 状态库 + 协调器(先做)

#### A1. `init_runs` 表 + 启动 reconciliation ✅ 已实现 (commit 7fc00bd)
- **目标**:§5a 持久化状态(崩溃安全)。
- **文件**:`src/openbiliclaw/storage/database.py`
- **实际落地方法名(下游 A2/A3/E1/E2 引用这些,不是计划初稿的 `get_init_run`/`upsert_init_run`)**:
  1. 建表 `init_runs`:`run_id TEXT PK` / `status` / `stage` / `stages_json`(各 stage `pending|running|ok|warning|failed`+reason)/ `partial_success` / `started_at` / `updated_at` / `finished_at` / `error_reason` / `sequence`。
  2. `get_latest_init_run()`(读最近一条)、`update_init_run(run_id, **fields)`(**单一写者**,白名单列)、原子 `try_reserve_init_starting(run_id)`(`BEGIN IMMEDIATE` CAS,仅无 active run 时置 `starting`,供 A2 TOCTOU)。
  3. `reconcile_init_runs_on_boot()`:把残留 `starting`/`running` 改 `failed("interrupted")`。
- **测试**(`tests/test_database.py`):往返;`try_reserve` single-flight(并发只成 1 个);reconcile 把 `running`→`failed`。
- **依赖**:无。
- **commit**:`feat(storage): add init_runs table + startup reconciliation`

#### A2. `InitCoordinator`
- **目标**:§5a/§5b/§5f 状态机 + 单一状态写者 + TOCTOU 启动 + cancel + `init_active`。
- **文件**:新建 `src/openbiliclaw/runtime/init_coordinator.py`
- **步骤**:
  1. `InitCoordinator`(持 `event_hub`、`current_task`、本 run `enqueued_task_ids: set`)。**`runtime_controller` / `database` 经 `ctx` getter 惰性取、不存旧引用**——rebuild 会换实例(`runtime_context.py:627`),存死引用会用到 pre-rebuild 的(review R2 A-1)。
  2. `init_active() -> bool`(`status ∈ {starting,running}`,读库);`get_status() -> dict`(组装 §API Shape,per-stage 从 `stages_json` 还原)。
  3. `try_start(...)`:原子 `try_reserve_init_starting()`(占坑)→ 返回 run_id 或 None(已 active)。**先占坑,校验在 E2 临界区内;但 E2 的廉价前置(local gate / unsupported / already_initialized)要在占坑之前**(R2 A-2,见 E2)。
  4. **单一状态写者**:`advance_stage / mark_warning / complete / fail / cancel`——只有协调器/wrapper 写 `init_runs`(`run_guided_init` 不写,见 B2)。`sequence` 单调 + per-run 串行点(`asyncio.Lock`)保并发(stage 3/4)原子写 + publish。
  5. `attach_task(run_id, task)`(E2 把 `track()` 返回的 task 存进来,`track` 返回 task `task_registry.py:48-57`)+ `cancel_current_run(run_id)`:cancel 该 task;状态库由 wrapper 落 `cancelled`(§5f)(review R2 A-3)。
  6. `register_enqueued_task(run_id, task_id)`:B2 的 bootstrap enqueue 回调用,填 `enqueued_task_ids` 供 D1 gate(review R2 A-4)。
  7. `reconcile_on_boot()` 调 A1 `reconcile_init_runs_on_boot()`。
- **测试**(`tests/test_init_coordinator.py`):状态机迁移;`try_start` 二次调 → None;cancel 落 `cancelled`;sequence 单调;并发 advance 不乱序(mock database)。
- **依赖**:A1。
- **commit**:`feat(runtime): add InitCoordinator (persisted state machine, TOCTOU start, cancel)`

#### A3. 接 InitCoordinator 进 RuntimeContext + 启动 reconcile + 暴露 ctx（review R1 F1+F2 新增)
- **目标**:让 coordinator 真正活在 app 里——否则 E1/E2/D1 拿不到 `ctx.init_coordinator`,`reconcile` 也没人在启动时调。
- **文件**:`src/openbiliclaw/api/runtime_context.py`、`src/openbiliclaw/api/app.py`
- **步骤**:
  1. `RuntimeContext` 加 `init_coordinator` 字段(`runtime_context.py:230-261` 区);**production / degraded / injection 三条构造路径**都构造它(`:846-856` 区)。**传 `ctx`(或 getter),让 coordinator 惰性取 `runtime_controller`/`database`/`event_hub`/`task_registry`——别传死引用**(rebuild 在 `:627` 换实例,review R2 A-1);`_rebuild_components`(`:846`)后惰性 getter 天然指向新实例。
  2. app 启动调 `ctx.init_coordinator.reconcile_on_boot()` 清崩溃残留——**位置:在 `restart_background_tasks`(`app.py:1637`)之前,且在 degraded 早返回(`app.py:1635`)之前**(否则降级启动会跳过 reconcile,review R2 B-1)。
  3. handler 经 `ctx.init_coordinator` 访问(E1/E2/D1 用)。
- **测试**(`tests/test_api_app.py`):三条构造路径都有 coordinator;启动调 reconcile(mock 验);残留 `running` 启动后变 `failed`。
- **依赖**:A2。
- **commit**:`feat(runtime): wire InitCoordinator into RuntimeContext + boot reconcile`

#### A4. `task_registry` 具名取消/exclude + rebuild 豁免 init 任务（review R1 F4 新增)
- **目标**:§5c rebuild 不 cancel init 任务、§5f cancel 单个 init——现 registry 只有 `track(name, coro)` + `cancel_all(grace_seconds)`(`task_registry.py:48-85`),`rebuild_from_config` 无条件 cancel 全部(`runtime_context.py:286`),做不到。**D1 + E2 的前置**,单列。
- **文件**:`src/openbiliclaw/runtime/task_registry.py`、`src/openbiliclaw/api/runtime_context.py`
- **步骤**:
  1. `task_registry`:加 `cancel(name)`(具名停单个)+ `cancel_all(*, exclude: set[str] = frozenset())`(排除指定 task)。
  2. `rebuild_from_config`(`runtime_context.py:286`):取消时 `exclude={"guided_init"}`(init 运行中不被热重载杀掉,§5c 双保险)。
- **测试**(`tests/test_task_registry.py`):`cancel(name)` 只停目标;`cancel_all(exclude=...)` 跳过被排除;rebuild 不动 `guided_init`。
- **依赖**:无(纯扩展;语义服务 A3 + D1/E2)。
- **commit**:`feat(runtime): task_registry named cancel + exclude; exempt init task from rebuild`

### Phase B — 共享 init 流水线

#### B1. `ContinuousRefreshController.run_init_backfill`
- **目标**:§5d stage 4 经 `_refresh_lock`。
- **文件**:`src/openbiliclaw/runtime/refresh.py`
- **步骤**:新增 `async def run_init_backfill(self, profile, target_pool_count, *, fully_parallel=True)`,**自身 `async with self._refresh_lock`**(字段 `refresh.py:273`,现有获取处 `:438`/`:484`),内部复刻 CLI `_run_init_discovery_backfill_async`(`cli.py:2035-2066`)的发现逻辑(draft profile + 目标池 + 并行),写 `content_cache`。协作式取消(`CancelledError` 时 finally 释放锁)。
- **测试**(`tests/test_refresh.py`):持 `_refresh_lock`(并发 refresh 被串行);cancel 释放锁;mock discovery engine 验调用形状。
- **依赖**:无(纯加方法)。
- **commit**:`feat(runtime): add run_init_backfill holding _refresh_lock for guided init`

#### B2. 抽 `run_guided_init`(**最高风险**)
- **目标**:§1/§5e 把 CLI init 四阶段抽成 CLI/API 共用异步函数 + on_progress;内部零 `asyncio.run`;保 P3/P4 并行;无编排副作用。
- **文件**:新建 `src/openbiliclaw/runtime/init_pipeline.py`;改 `src/openbiliclaw/cli.py`
- **步骤**:
  1. `init_pipeline.py`:`@dataclass InitParams` / `InitProgress`;`async def run_guided_init(*, database, bilibili_client, memory, soul_engine, discovery_engine, runtime_controller, config, params, on_progress, on_task_enqueued=None) -> InitResult`。**bootstrap enqueue 也搬进 stage 1**(CLI/API 统一),每 enqueue 一个就 `on_task_enqueued(task_id)`——API wrapper 的实现转 `coordinator.register_enqueued_task(run_id, task_id)`(供 D1 gate,R2 A-4),CLI 实现 no-op。把 `cli.py` init 的**纯四阶段核心**搬来:① fetch(`_fetch_all_data` 等价,**纯 async,去掉 `asyncio.run`**)② `soul_engine.analyze_events(...)` ③+④ **并行** `soul_engine.build_initial_profile(...)` ‖ `runtime_controller.run_init_backfill(...)`(复刻 `_run_p3_p4_parallel` `cli.py:4576`)。每阶段开始/结束/warning 调 `on_progress`(含终态 `done`/`error`)。**不 publish、不写 init_runs**(编排副作用归 wrapper)。
  2. `cli.py init`:保留所有交互问答 + `_persist_*` + 网络/密码 setup;四阶段核心改为**唯一外层 `asyncio.run(run_guided_init(..., on_progress=<console 打印>))`**;stage 4 不再直接 `discovery_engine.discover`(`cli.py:2058`),走 `run_init_backfill`。
  3. **bootstrap 收集从同步阻塞改异步**(review R1 F5):现有 `_collect_*_bootstrap_events`(`cli.py:2279/2454/2606`)是 `time.sleep` 轮询 task 表的**同步**函数——直接搬进 async 的 `run_guided_init` 会阻塞事件循环。改为 `await asyncio.sleep` 轮询(或 `asyncio.to_thread`),由 API 路径复用(D1 把 init-owned task-result 喂这里)。
  4. **去掉 per-event `asyncio.run`**(review R1 F5):CLI init 现在每条事件 `asyncio.run(memory.propagate_event(...))`(`cli.py:4516`)——在 async 函数里**非法**。改成 `await memory.propagate_event(...)`;所有 init-domain 写都转成 await 的 async 调用。
- **测试**(`tests/test_init_pipeline.py` + 现有 `tests/test_cli.py`):四阶段顺序 + 3/4 并行(mock soul_engine/controller,断言 build_initial_profile 与 run_init_backfill 并发起);on_progress 收到 1→4 + 终态;**无 `asyncio.run` / 无 `time.sleep`**(静态/运行时断言);**`openbiliclaw init` 现有 `test_cli.py` 回归全绿**(行为/退出码/文案不变)。
- **依赖**:B1。
- **commit**:`refactor(runtime): extract run_guided_init shared pipeline from cli init`
- **⚠ 注意(最易翻车,review R1 F5)**:CLI init 的「纯四阶段」**不是现成干净的**——交互问答 / `_persist_*` / bootstrap enqueue 留 cli wrapper;但**三处隐藏耦合必须抽取时一并处理**:① 同步 `time.sleep` 采集器(`:2279/2454/2606`)→ await ② per-event `asyncio.run(propagate_event)`(`:4516`)→ await ③ `_prepare_init_runtime` 等自建资源 → 由调用方注入。内部**绝不** `asyncio.run`、保 P3/P4 并行、CLI 行为零回归。建议这一任务实现后再单独细审 diff。

### Phase C — 前置探测

#### C1. cached 前置探测(chat / B站 / platforms)
- **目标**:§3 前置清单数据源。
- **文件**:新建 `src/openbiliclaw/runtime/init_prereqs.py`(或并入 coordinator)
- **步骤**:
  1. `chat_ready(cfg) -> bool`:照 `embedding_ready` 的 live-cached probe 模式(`api/app.py:1113`),对 chat provider 做带超时 + single-flight + TTL 缓存的探测(registry 能构建 ≠ 能 call,qwen2.5 那种 404)。
  2. `bilibili_check(cfg) -> "ok"|"failed"|"checking"`:有 cookie 时 `AuthManager.validate_cookie`(`bilibili/auth.py:95`),**TTL 缓存**(成功 60s / 失败 10s),**不每 poll 打 B站**。
  3. `enabled_platforms(cfg) -> list[str]`:读 `config.sources.*.enabled`(`config.py:243-324`)。
  4. `embedding_ready`:**直接复用**既有 `/api/health` embedding cached-probe(`api/app.py:1113`),透出到 init-status `prerequisites.embedding_ready`(软前置,spec §3 / API shape;review R2 A-6)。
- **测试**(`tests/test_init_prereqs.py`):chat 探测 404 → False;B站 缓存命中不重复打(mock validate_cookie 调用计数);platforms 读取;embedding_ready 取自既有 probe。
- **依赖**:无(读 config/registry)。
- **commit**:`feat(runtime): cached init prerequisite probes (chat, bilibili, platforms, embedding)`

### Phase D — 写者门控(碰多处既有代码,**高风险**)

#### D1. `init_active` 写者门控:HTTP-409 / 后台-skip / cookie / bootstrap 放行
- **目标**:§5c。
- **文件**:`src/openbiliclaw/api/app.py`(多端点)、`src/openbiliclaw/runtime/{refresh,account_sync,...}.py`(后台循环)、`runtime_context.py`
- **步骤**:
  1. **HTTP 写端**(`init_active` → `409 init_running`,side-effect 前查、含 `_CONFIG_SAVE_LOCK` 内二次查,锁序见 §5b):`PUT /api/config`(`:5554/5969`)、`POST /api/bilibili/cookie`(`:1234`)、`POST /api/sources*`、`POST /api/profile/edit`、手动 refresh、probe/interest promote、**`POST /api/feedback`(`:3934`,写+propagate)、`POST /api/recommendation-click`(`:4019`,propagate/ingest)**(review R2 A-5,二者都写画像)。
  2. **cookie 例外**:`init_active` 时 `/api/bilibili/cookie` **先比 effective cookie**(在现有 validate `:1278` 之前)→ 同值 `200` no-op、不 validate 不 rebuild;异值 `409`。
  3. **bootstrap 截流(不止「不 drain」,review R1 F6)**:`POST /api/sources/{xhs,dy,yt}/task-result`(`:4805/4899/4958/5079/5089`)的 `task_id ∈ coordinator.enqueued_task_ids 且 run active` → 把结果路由给 init 采集器(handler 仍可 `merge_result` `app.py:4759/4941/5072` 把结果落 task 表供 B2 async 采集器读),但**跳过其后的** `_cache_xhs_notes` / `propagate_event`(**XHS `:4800` / DY `:4956` / YT `:5087` 都会直接写画像**)/ `_ingest_profile_update_events` 与 drain(`app.py:4786` / `refresh.py:1291` / `candidate_pipeline.py:122`);发现池由 stage 4 统一写。否则按无关 source 写 gate。
  4. **后台循环 skip**(不是 handler,**不返 409**):连续 refresh tick(`refresh.py`)、account_sync tick(`account_sync.py:262`)、soul pipeline tick、事件摄入 refresh,`init_active` 时跳过本 tick + log。
  5. **init 任务豁免热重载取消**:`rebuild_from_config`(`runtime_context.py:286`)取消时 `exclude={"guided_init"}`(机制由 **A4** 提供)。
- **测试**(`tests/test_api_app.py` + 后台循环单测):**fixture 直接经 `ctx.init_coordinator` / DB `try_reserve` 置 active run**(不依赖尚未存在的 `/api/init`,review R1 F3);各 HTTP 写端 init 中 `409`;同值 cookie no-op(不调 validate)/异值 409;bootstrap task-result 按 task_id 放行 → **断言无直接 memory 写、无 `propagate_event`、无 drain**;后台 tick skip;rebuild 不 cancel `guided_init`。
- **依赖**:A3、A4。
- **commit**:`feat(api,runtime): gate writers during init (HTTP 409 / background skip / bootstrap passthrough)`

### Phase E — API 端点

#### E1. `GET /api/init-status`(读端,可远程,degraded 可读)
- **目标**:§API Shape 权威进度源。
- **文件**:`api/models.py`、`api/app.py`、`api/auth.py`
- **步骤**:`InitStatusOut`(spec 字段:initialized/running/run_id/sequence/current_stage/stages[]/partial_success/can_start/can_manage/prerequisites/reason/detail);handler 从 `coordinator.get_status()` + `init_prereqs` + `is_profile_ready`(`soul/engine.py:328`)组装;`can_start = trusted_local && 硬前置满足 && !running && supported`;`_is_public()`(`auth.py:269`)+ degraded 白名单(`app.py:840`)加该 path;远程不 403、`can_manage=false`。
- **测试**(`tests/test_api_app.py`):未初始化 `initialized=false`;前置缺失 `can_start=false`+reason;running 中报 stage/sequence;degraded 可读;远程 `can_manage=false`;无敏感字段。
- **依赖**:A3、C1。
- **commit**:`feat(api): GET /api/init-status (authoritative progress, prereq checklist)`

#### E2. `POST /api/init` + `POST /api/init/cancel`(写端,仅本机)
- **目标**:§2/§5b/§5f。
- **文件**:`api/app.py`、`api/auth.py`
- **步骤**:
  1. `POST /api/init(request: Request)`:**裸 Request**。顺序(review R2 A-2:**占坑前先做廉价拒绝**,避免留 stuck `starting` 行):① `is_trusted_local` 否则 `403 local_only` → ② `await request.json()` 取 `force` → ③ **占坑前廉价前置**:`unsupported_runtime` 短路 `409`(Docker `is_running_in_container` `docker_runtime.py:176` **+ data/config 目录可写性检查**,review R2 A-7);`is_profile_ready` 且非 force → `409 already_initialized` → ④ `coordinator.try_start()` 占坑(已 active → `409 already_running`)→ ⑤ **临界区内 revalidate 前置**(chat + B站;缺 → **复位该行(idle/failed)** + `409 bilibili_not_logged_in`/`llm_not_ready`)→ ⑥ 通过则 `task = ctx.task_registry.track("guided_init", _init_wrapper_coro(run_id, ...))`(传协程)+ `coordinator.attach_task(run_id, task)`(R2 A-3)→ `202` + 初始 status。
  2. `_init_wrapper_coro`:调 `run_guided_init(..., on_progress=<publish init_progress + 落 init_runs via coordinator>)`;**wrapper 是唯一事件 publish + 状态库写者**;终态 publish `init_completed`/`init_failed`(**不调** `/api/init-completed`);catch `CancelledError`/超时 → coordinator 落 `cancelled`/`failed`;每 stage 超时。
  3. `POST /api/init/cancel(request: Request)`:本机 → `coordinator.cancel_current_run`;无运行中 → `409 not_running`。
  4. `_is_public()` + degraded 白名单加两 path。
- **测试**(`tests/test_api_app.py`,mock run_guided_init):非本机 `403`;缺前置 `409`(且**复位、不留 `starting` 行**,R2 A-2);已 active `409 already_running`;已初始化非 force `409`;force 重建;Docker / data 目录只读 → `unsupported_runtime`(R2 A-7);**cancel 真能 cancel attached task**(R2 A-3) + 无运行中 `409 not_running`;事件由 wrapper 出。
- **依赖**:A3、A4、B2、D1、E1。
- **commit**:`feat(api): POST /api/init + /api/init/cancel (local-only, background, wrapper-published)`

### Phase F — 插件 UI(Phase 1 重点)

#### F1. 推荐 tab init CTA + 前置清单 + 进度 + 设置重建
- **目标**:§4。
- **文件**:`extension/popup/popup.js`(`showRecommendationEmptyState` ~4593)、`popup-helpers.js`、`popup.html`、`popup-api.js`;建议新建 `popup-init-control.js`(DOM-agnostic 可单测)
- **步骤**:
  1. 推荐 tab `kind="uninitialized"` 空状态 → 「开始初始化」按钮 + 前置清单(B站/LLM/embedding/平台,读 `GET /api/init-status`,硬前置不满足置灰 + reason 文案 + 去补的指引);**内联平台开关**(`PUT /api/config` 顺手开,init 启动前)。
  2. 点击 → `POST /api/init` → 订阅 `/api/runtime-stream` 的 `init_progress`/`init_completed`/`init_failed` + 先拉一次 `GET /api/init-status` 补连接前进度 → 进度条;完成空状态消失、自动加载推荐。
  3. **画像/编辑空状态**(`popup.js` ~2812/3229、`popup-helpers.js`、`profile.js:108`)→ 文案改「还没初始化,去『推荐』页开始」,**不放按钮**。
  4. 设置页加「重新初始化 / 重建画像」(二次确认 → `POST /api/init {force:true}`)。
- **测试**(`extension` `npm run test` + `typecheck`):清单渲染/置灰逻辑、进度状态机(可测函数);手测全流程。
- **依赖**:E1、E2。
- **commit**:`feat(extension): guided-init CTA + prereq checklist + progress in recommend tab`

### Phase G — 验收 + 文档

#### G1. 验收矩阵 + gate
- 补缺口 + 确认 gate:
  - `pytest tests/test_init_coordinator.py tests/test_init_pipeline.py tests/test_init_prereqs.py tests/test_database.py -q`
  - `pytest tests/test_api_app.py tests/test_cli.py tests/test_refresh.py -k "init" -q`
  - `cd extension && npm run typecheck && npm run test`
- **commit**:`test(init): complete guided-init acceptance matrix`

#### G2. 文档同步(CLAUDE.md 强制)
- 新建 `docs/modules/init.md`(run_guided_init + InitCoordinator + 端点 + 写者门控);`docs/modules/extension.md`(推荐 tab init CTA);`docs/modules/cli.md`(init 改走共享流水线);`docs/changelog.md` bullet;架构图(新增 `runtime/init_pipeline` + `init_coordinator` + 后端→soul/discovery 触发边)`docs/architecture.md` / `docs/spec.md` §3 / README 中英顶部图。
- **commit**:`docs: guided-init module/changelog/architecture sync`

## 2. 验收映射(任务 → spec)

| Spec | 任务 |
|---|---|
| §1 共享流水线 | B1, B2 |
| §2 API | E1, E2 |
| §3 前置 | C1, E1 |
| §4 UI(插件) | F1 |
| §5a 状态库 | A1, A2 |
| §5a 接线 + 启动 reconcile | A3 |
| §5b TOCTOU/写者二次查 | A2, D1, E2 |
| §5c 写者门控 | A4, D1 |
| §5d run_init_backfill | B1, B2 |
| §5e run_guided_init 无编排副作用 | B2, E2 |
| §5f cancel/超时/协作取消 | A2, A4, E2 |

## 3. 风险与缓解

| 风险 | 等级 | 缓解 |
|---|---|---|
| **B2 抽 run_guided_init 回归 `openbiliclaw init`**(动核心 soul/发现) | 高 | 保留 CLI 所有交互;`test_cli.py` 全回归;保 P3/P4 并行 + 零 `asyncio.run`;code review 重点 |
| **D1 写者门控碰多处端点/循环**,漏一个 = init 被并发写污染/被 rebuild cancel | 高 | 枚举清单逐个;init 任务对 rebuild 豁免做双保险;每端点/循环单测 |
| **无法在本机 E2E 完整 init**(缺真 B站/LLM 凭据) | 中 | mock 到 `run_guided_init` 边界全测;**完整画像生成列入用户手测 DoD** |
| 协作式取消时 stage 4 持 `_refresh_lock` 不释放 → 死锁 | 中 | B1 `finally` 释放 + 取消测试 |
| 插件无法自动 E2E | 中 | DOM-agnostic 函数单测 + 手测 |

## 4. 完成定义 (DoD)

- [ ] 三条 gate 命令(2 pytest + extension)全绿。
- [ ] `mypy src/`(strict)、`ruff check src/ tests/`、`ruff format` 通过。
- [ ] `openbiliclaw init`(CLI)**零回归**(现有 `test_cli.py` 全绿、行为/退出码/文案不变)。
- [ ] spec Acceptance 勾选项有对应通过测试(除需真凭据的完整 init)。
- [ ] **用户用真 B站 号 + 真 LLM key 手测一次完整 GUI init**(插件推荐 tab → 前置清单 → 开始初始化 → 进度 → 画像生成 → 推荐出现),记录结果。
- [ ] CLAUDE.md 文档同步清单全过。
- [ ] 默认行为不变:未触发 init 时无新副作用;CLI init 路径除「stage 4 走 run_init_backfill」外等价。

## 5. 约定与边界

- 本计划仅 **Phase 1**;网页 `/setup`+`/web`(Phase 2)复用 E1/E2 端点后做。
- 不做:网页内扫码 QR、保活、init 断点续跑(失败整体重来/可 cancel)。
- prompt-cache 约定(CLAUDE.md):本特性不新增 LLM prompt builder,无关。
- Conventional Commits;Python 3.11、4 空格、100 列、类型注解齐全。
- 提交顺序:A1→A2→A3→A4→B1→B2→C1→D1→E1→E2→F1→G1→G2(**13 commit**;A1 已落;A2→A3 串行,A4/B1/C1 可并行;D1 前需 A3+A4,E2 前需 A3+A4+B2+D1+E1)。
