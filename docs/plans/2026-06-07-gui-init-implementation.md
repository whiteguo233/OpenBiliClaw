# GUI 引导初始化 — 实现计划 (Implementation Plan, Phase 1)

**Created:** 2026-06-07
**Spec:** [`docs/specs/gui-init.md`](../specs/gui-init.md)（**CONVERGED**,Codex 对抗 review R1–R7,R7 判 SHIP / 0 spec 级缺陷)
**Status:** 待执行
**Scope:** **Phase 1** = 后端(共享 init 流水线 + InitCoordinator + 3 端点)+ **浏览器插件**(推荐 tab CTA + 前置清单 + 进度)。**Phase 2**(网页 `/setup` 第④步 + `/web` 空状态)不在本计划,复用同一组端点后做。
**测试限制(诚实声明):** init 完整跑通需**真 B站 cookie + 真 LLM key**(本机无有效凭据)。本计划能自动验证的:CLI init 无回归、InitCoordinator 状态机/并发/崩溃恢复、前置门控、status/cancel、写者 409/skip、插件渲染逻辑;**完整画像生成的端到端需用户用真号手测**(列入 DoD)。

> 把 spec 的 §1–§5 拆成**依赖有序、每个 = 一个原子 commit** 的任务。最高风险:**B2 把 CLI init 四阶段抽成共享异步函数**(动核心 soul/发现,不能回归 `openbiliclaw init`)+ **D1 写者门控**(碰一堆既有端点/循环)。这两个测试最重、review 最细。

## 0. 总览:依赖顺序

```
A1. init_runs 表 + 启动 reconciliation ──> A2. InitCoordinator ──┬─> D1. 写者门控
   (§5a)                                     (§5a/b/f)            │   (§5c)
B1. run_init_backfill(持 _refresh_lock) ──> B2. run_guided_init   ├─> E1. GET /api/init-status ──┐
   (§5d)                                     抽取(§1/§5e)         │   (§3 探测 ← C1)            ├─> F1. 插件 UI
C1. 前置探测(cached chat/B站/platforms) ───────────────────────┘                              │   (§4)
   (§3)                                                          └─> E2. POST /api/init + /cancel ┘
                                                                      (§2/§5b/§5f) ← A2,B2,D1,E1
G1. 验收矩阵 ; G2. 文档同步(CLAUDE.md 强制)
```

- **关键路径**:A1 → A2 → D1 → E2 → F1;并行支线 B1→B2、C1。
- **A1 是唯一根**(状态库),A2 依赖它;B1/C1 可与 A 并行起。
- **粗估**:A1≈0.5d、A2≈1d、B1≈0.5d、**B2≈2d(重:核心重构 + CLI 回归)**、C1≈0.5d、**D1≈1.5d(碰多处端点/循环)**、E1≈0.5d、E2≈1d、**F1≈1.5d(插件)**、G1≈0.5d、G2≈0.5d。合计 ~10 人日。

## 1. 任务分解

### Phase A — 状态库 + 协调器(先做)

#### A1. `init_runs` 表 + 启动 reconciliation
- **目标**:§5a 持久化状态(崩溃安全)。
- **文件**:`src/openbiliclaw/storage/database.py`
- **步骤**:
  1. 建表 `init_runs`(照该文件既有 schema 风格 `:69/:319`):`run_id TEXT PK` / `status TEXT`(`idle|starting|running|completed|failed|cancelled`)/ `stage INTEGER` / `stages_json TEXT`(各 stage `pending|running|ok|warning|failed`+reason)/ `partial_success INTEGER` / `started_at` / `updated_at` / `finished_at` / `error_reason TEXT` / `sequence INTEGER`。单行(或最近一条为准)。
  2. 读写方法:`get_init_run()`、`upsert_init_run(...)`、原子 `try_reserve_init_starting()`(条件写:仅当无 active run 时置 `starting`,供 A2 TOCTOU)。
  3. `reconcile_init_on_boot()`:启动时把残留 `starting`/`running` 改 `failed("interrupted")`。
- **测试**(`tests/test_database.py`):往返;`try_reserve` single-flight(并发只成 1 个);reconcile 把 `running`→`failed`。
- **依赖**:无。
- **commit**:`feat(storage): add init_runs table + startup reconciliation`

#### A2. `InitCoordinator`
- **目标**:§5a/§5b/§5f 状态机 + 单一状态写者 + TOCTOU 启动 + cancel + `init_active`。
- **文件**:新建 `src/openbiliclaw/runtime/init_coordinator.py`
- **步骤**:
  1. `InitCoordinator`(持 `database`、`event_hub`、`current_task`、本 run `enqueued_task_ids: set`)。
  2. `init_active() -> bool`(`status ∈ {starting,running}`,读库);`get_status() -> dict`(组装 §API Shape,per-stage 从 `stages_json` 还原)。
  3. `try_start(...)`:原子 `try_reserve_init_starting()`(占坑)→ 返回 run_id 或 None(已 active)。**先占坑,校验在 E2 临界区内**。
  4. **单一状态写者**:`advance_stage / mark_warning / complete / fail / cancel`——只有协调器/wrapper 写 `init_runs`(`run_guided_init` 不写,见 B2)。`sequence` 单调 + per-run 串行点(`asyncio.Lock`)保并发(stage 3/4)原子写 + publish。
  5. `cancel_current_run(run_id)`:cancel `current_task`;`finally` 落 `cancelled`(由协调器)。
  6. `reconcile_on_boot()` 调 A1。
- **测试**(`tests/test_init_coordinator.py`):状态机迁移;`try_start` 二次调 → None;cancel 落 `cancelled`;sequence 单调;并发 advance 不乱序(mock database)。
- **依赖**:A1。
- **commit**:`feat(runtime): add InitCoordinator (persisted state machine, TOCTOU start, cancel)`

### Phase B — 共享 init 流水线

#### B1. `ContinuousRefreshController.run_init_backfill`
- **目标**:§5d stage 4 经 `_refresh_lock`。
- **文件**:`src/openbiliclaw/runtime/refresh.py`
- **步骤**:新增 `async def run_init_backfill(self, profile, target_pool_count, *, fully_parallel=True)`,**自身 `async with self._refresh_lock`**(`:408`),内部复刻 CLI `_run_init_discovery_backfill_async`(`cli.py:2035-2066`)的发现逻辑(draft profile + 目标池 + 并行),写 `content_cache`。协作式取消(`CancelledError` 时 finally 释放锁)。
- **测试**(`tests/test_refresh.py`):持 `_refresh_lock`(并发 refresh 被串行);cancel 释放锁;mock discovery engine 验调用形状。
- **依赖**:无(纯加方法)。
- **commit**:`feat(runtime): add run_init_backfill holding _refresh_lock for guided init`

#### B2. 抽 `run_guided_init`(**最高风险**)
- **目标**:§1/§5e 把 CLI init 四阶段抽成 CLI/API 共用异步函数 + on_progress;内部零 `asyncio.run`;保 P3/P4 并行;无编排副作用。
- **文件**:新建 `src/openbiliclaw/runtime/init_pipeline.py`;改 `src/openbiliclaw/cli.py`
- **步骤**:
  1. `init_pipeline.py`:`@dataclass InitParams` / `InitProgress`;`async def run_guided_init(*, database, bilibili_client, memory, soul_engine, discovery_engine, runtime_controller, config, params, on_progress) -> InitResult`。把 `cli.py` init 的**纯四阶段核心**搬来:① fetch(`_fetch_all_data` 等价,**纯 async,去掉 `asyncio.run`**)② `soul_engine.analyze_events(...)` ③+④ **并行** `soul_engine.build_initial_profile(...)` ‖ `runtime_controller.run_init_backfill(...)`(复刻 `_run_p3_p4_parallel` `cli.py:4564`)。每阶段开始/结束/warning 调 `on_progress`(含终态 `done`/`error`)。**不 publish、不写 init_runs**(编排副作用归 wrapper)。
  2. `cli.py init`:保留所有交互问答 + `_persist_*` + 网络/密码 setup;四阶段核心改为**唯一外层 `asyncio.run(run_guided_init(..., on_progress=<console 打印>))`**;stage 4 不再直接 `discovery_engine.discover`(`cli.py:2058`),走 `run_init_backfill`。
  3. bootstrap 收集(xhs/dy/yt `_collect_*_bootstrap_events`)作为 stage 1 的一部分留在 `run_guided_init`,供 API 路径复用(D1 把 init-owned task-result 喂这里)。
- **测试**(`tests/test_init_pipeline.py` + 现有 `tests/test_cli.py`):`run_guided_init` 四阶段顺序 + 3/4 并行(mock soul_engine/controller,断言 build_initial_profile 与 run_init_backfill 并发起);on_progress 收到 1→4 + 终态;**无 `asyncio.run`**(可静态/运行时断言);**`openbiliclaw init` 现有 `test_cli.py` 回归全绿**(行为/退出码/文案不变)。
- **依赖**:B1。
- **commit**:`refactor(runtime): extract run_guided_init shared pipeline from cli init`
- **⚠ 注意**:内部**绝不** `asyncio.run`;**保 P3/P4 并行**;CLI 行为零回归(这是最易翻车处)。

### Phase C — 前置探测

#### C1. cached 前置探测(chat / B站 / platforms)
- **目标**:§3 前置清单数据源。
- **文件**:新建 `src/openbiliclaw/runtime/init_prereqs.py`(或并入 coordinator)
- **步骤**:
  1. `chat_ready(cfg) -> bool`:照 `embedding_ready` 的 live-cached probe 模式(`api/app.py:1113`),对 chat provider 做带超时 + single-flight + TTL 缓存的探测(registry 能构建 ≠ 能 call,qwen2.5 那种 404)。
  2. `bilibili_check(cfg) -> "ok"|"failed"|"checking"`:有 cookie 时 `AuthManager.validate_cookie`(`bilibili/auth.py:95`),**TTL 缓存**(成功 60s / 失败 10s),**不每 poll 打 B站**。
  3. `enabled_platforms(cfg) -> list[str]`:读 `config.sources.*.enabled`(`config.py:243-324`)。
- **测试**(`tests/test_init_prereqs.py`):chat 探测 404 → False;B站 缓存命中不重复打(mock validate_cookie 调用计数);platforms 读取。
- **依赖**:无(读 config/registry)。
- **commit**:`feat(runtime): cached init prerequisite probes (chat, bilibili, platforms)`

### Phase D — 写者门控(碰多处既有代码,**高风险**)

#### D1. `init_active` 写者门控:HTTP-409 / 后台-skip / cookie / bootstrap 放行
- **目标**:§5c。
- **文件**:`src/openbiliclaw/api/app.py`(多端点)、`src/openbiliclaw/runtime/{refresh,account_sync,...}.py`(后台循环)、`runtime_context.py`
- **步骤**:
  1. **HTTP 写端**(`init_active` → `409 init_running`,side-effect 前查、含 `_CONFIG_SAVE_LOCK` 内二次查,锁序见 §5b):`PUT /api/config`(`:5554/5969`)、`POST /api/bilibili/cookie`(`:1234`)、`POST /api/sources*`、`POST /api/profile/edit`、手动 refresh、probe/interest promote。
  2. **cookie 例外**:`init_active` 时 `/api/bilibili/cookie` **先比 effective cookie**(在现有 validate `:1278` 之前)→ 同值 `200` no-op、不 validate 不 rebuild;异值 `409`。
  3. **bootstrap 放行**:`POST /api/sources/{xhs,dy,yt}/task-result`(`:4805/4899/4958/5079/5089`)的 `task_id ∈ coordinator.enqueued_task_ids 且 run active` → 放行,且**喂进 init 收集缓冲、不触发 live drain**(`:4784` drain / `refresh.py:1291` / `candidate_pipeline.py:122`),发现池由 stage 4 统一写;否则按无关写 gate。
  4. **后台循环 skip**(不是 handler,**不返 409**):连续 refresh tick(`refresh.py`)、account_sync tick(`account_sync.py:262`)、soul pipeline tick、事件摄入 refresh,`init_active` 时跳过本 tick + log。
  5. **init 任务豁免热重载取消**:`rebuild_from_config`(`runtime_context.py:286`)取消 tracked tasks 时排除 `coordinator.current_task`(`exclude=` 或 registry 具名豁免,需扩展 `task_registry.py`)。
- **测试**(`tests/test_api_app.py` + 后台循环单测):各 HTTP 写端 init 中 `409`;同值 cookie no-op(不调 validate)/异值 409;bootstrap task-result 按 task_id 放行 + 不触发 live drain;后台 tick skip;rebuild 不 cancel init 任务。
- **依赖**:A2。
- **commit**:`feat(api,runtime): gate writers during init (HTTP 409 / background skip / bootstrap passthrough)`

### Phase E — API 端点

#### E1. `GET /api/init-status`(读端,可远程,degraded 可读)
- **目标**:§API Shape 权威进度源。
- **文件**:`api/models.py`、`api/app.py`、`api/auth.py`
- **步骤**:`InitStatusOut`(spec 字段:initialized/running/run_id/sequence/current_stage/stages[]/partial_success/can_start/can_manage/prerequisites/reason/detail);handler 从 `coordinator.get_status()` + `init_prereqs` + `is_profile_ready`(`soul/engine.py:328`)组装;`can_start = trusted_local && 硬前置满足 && !running && supported`;`_is_public()`(`auth.py:269`)+ degraded 白名单(`app.py:840`)加该 path;远程不 403、`can_manage=false`。
- **测试**(`tests/test_api_app.py`):未初始化 `initialized=false`;前置缺失 `can_start=false`+reason;running 中报 stage/sequence;degraded 可读;远程 `can_manage=false`;无敏感字段。
- **依赖**:A2、C1。
- **commit**:`feat(api): GET /api/init-status (authoritative progress, prereq checklist)`

#### E2. `POST /api/init` + `POST /api/init/cancel`(写端,仅本机)
- **目标**:§2/§5b/§5f。
- **文件**:`api/app.py`、`api/auth.py`
- **步骤**:
  1. `POST /api/init(request: Request)`:**裸 Request**,**第一步** `is_trusted_local` 否则 `403 local_only` → 再 `await request.json()` 取 `force` → `coordinator.try_start()`(占坑;已 active → `409 already_running`)→ **临界区内 revalidate 前置**(chat + B站;缺 → 回 idle + `409 bilibili_not_logged_in`/`llm_not_ready`)→ 已初始化且非 force → `409 already_initialized` → 通过则 `ctx.task_registry.track("guided_init", _init_wrapper_coro(run_id, ...))`(**传协程**)→ `202` + 初始 status。`unsupported_runtime`(Docker,`is_running_in_container`)短路。
  2. `_init_wrapper_coro`:调 `run_guided_init(..., on_progress=<publish init_progress + 落 init_runs via coordinator>)`;**wrapper 是唯一事件 publish + 状态库写者**;终态 publish `init_completed`/`init_failed`(**不调** `/api/init-completed`);catch `CancelledError`/超时 → coordinator 落 `cancelled`/`failed`;每 stage 超时。
  3. `POST /api/init/cancel(request: Request)`:本机 → `coordinator.cancel_current_run`;无运行中 → `409 not_running`。
  4. `_is_public()` + degraded 白名单加两 path。
- **测试**(`tests/test_api_app.py`,mock run_guided_init):非本机 `403`;缺前置 `409`;已 active `409 already_running`;已初始化非 force `409`;force 重建;Docker `unsupported_runtime`;cancel 中止 + 无运行中 `409 not_running`;事件由 wrapper 出。
- **依赖**:A2、B2、D1、E1。
- **commit**:`feat(api): POST /api/init + /api/init/cancel (local-only, background, wrapper-published)`

### Phase F — 插件 UI(Phase 1 重点)

#### F1. 推荐 tab init CTA + 前置清单 + 进度 + 设置重建
- **目标**:§4。
- **文件**:`extension/popup/popup.js`(`showRecommendationEmptyState` ~4593)、`popup-helpers.js`、`popup.html`、`popup-api.js`;建议新建 `popup-init-control.js`(DOM-agnostic 可单测)
- **步骤**:
  1. 推荐 tab `kind="uninitialized"` 空状态 → 「开始初始化」按钮 + 前置清单(B站/LLM/平台,读 `GET /api/init-status`,硬前置不满足置灰 + reason 文案 + 去补的指引);**内联平台开关**(`PUT /api/config` 顺手开,init 启动前)。
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
| §5b TOCTOU/写者二次查 | A2, D1, E2 |
| §5c 写者门控 | D1 |
| §5d run_init_backfill | B1, B2 |
| §5e run_guided_init 无编排副作用 | B2, E2 |
| §5f cancel/超时/协作取消 | A2, E2 |

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
- 提交顺序:A1→A2→B1→B2→C1→D1→E1→E2→F1→G1→G2(**11 commit**;A1 先,B/C 可与 A 并行,E2 前需 A2+B2+D1+E1 就绪)。
