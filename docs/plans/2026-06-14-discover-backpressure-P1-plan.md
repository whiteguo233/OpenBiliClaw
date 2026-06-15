# Discover 背压重构 · P1 实现 Plan

> Source spec: `docs/plans/2026-06-14-discover-backpressure-refactor-design.md`（Approved for P1，决策已锁定）
> Date: 2026-06-14 · Scope: **仅 P1**（5 个 search 关键词生成器统一 + 必备正确性）。非 search 子来源、trending/explore、Recommend/Soul 一律不动。

## 0. 原则与约束（贯穿所有阶段）

- **只接管 search 一路**：B站 `search`、小红书 `xhs-search`、抖音 `dy search`、YouTube `yt_search`、X `x-search`。`trending/explore/related/feed/hot/channel/creator` 及其 budget/cadence 原样不动（spec §1/§11）。
- **决策已锁**：`prefer_axes` 维持屏蔽（不做正向补缺）；§6 默认值作起步；B站 保留事件/四策略作①额外催化；trending/explore 暂不并（spec §16）。
- **feature flag**：全程挂在 `[discovery].unified_keyword_planner_enabled`（默认 **false**）后面，旧逐平台生成路径保留可回退；最后一阶段才默认开。
- **生成永远现读最新画像**；`profile_kw_digest` 是路径无关的失效优化（spec §8）。
- **prompt-cache 约定**：合并 builder 的 system 100% 静态（spec §7.2）。
- 每阶段必须独立可测、可单独合入；落地顺序按依赖。开发环境用 `.venv/bin/python`。

## 1. 依赖图（落地顺序）

```
P1.0 config ─┐
P1.1 keyword store ─┬─────────────┐
P1.2 digest ────────┤             │
P1.3 XHS/X 兜底 ─────┤             │
P1.4 合并 builder ───┘             │
                  P1.5 注入口 ──────┤
                  P1.6 planner ─────┤
                  P1.7 缺口驱动抓取 ─┤
                  P1.8 yield 端到端 ─┘
                              P1.9 cutover + flag + 成本归因 + docs
```
P1.1–P1.4 可并行；P1.5/P1.6 依赖 1–4；P1.7 依赖 1/5；P1.8 依赖 1/7；P1.9 收口。

---

## P1.0 — 配置脚手架
**目标**：§6 参数进 config，全程读 config 不硬编码。
**文件**：`src/openbiliclaw/config.py`（+ `config.example.toml`、`docs/modules/config.md`）。
**动作**：新增 `[discovery]`（或并入 `[scheduler]`）字段——`unified_keyword_planner_enabled=false`、`kw_cache_high=30`、`kw_cache_low=10`、`gen_batch=30`、`fetch_batch=5`、`history_window_size=150`、`history_window_hours=48`、`claim_lease_minutes=10`、`planner_poll_seconds=120`、`plan_ttl_hours=12`。`fetch_floor` 复用各平台现有 `min_interval`。
**验收**：dataclass 加载/规范化测试通过（config 是 dataclass，非 Pydantic，`config.py:190`）；缺省值 == §6；env override 生效；`config-show` 显示；`docs/modules/config.md` 同步。

## P1.1 — 关键词存储（表 + DAO + 单飞锁）
**目标**：`discovery_keywords` 表 + 原子领取/租约/状态机/部分唯一 + 单飞锁表（spec §5.1/§5.2）。
**文件**：`src/openbiliclaw/storage/database.py`（建表/迁移 + DAO）。
**动作**：
- 建表：字段见 spec §5.1；**部分唯一** `UNIQUE(platform,keyword,profile_kw_digest) WHERE status IN ('pending','claimed','executing')`；索引 `(platform,status,profile_kw_digest)`、`(platform,status,used_at)`。
- DAO：`insert_pending(batch)`；`claim_pending(platform, n)`（`BEGIN IMMEDIATE` `pending→claimed`+`claimed_at`）；`mark_executing/used/failed`；`reclaim_leased`（`claimed`超`claim_lease`、`executing`超任务超时→`pending`）；`history_keywords(platform, window)`（`status IN ('claimed','executing','used')` 在窗口内）；`recycle_oldest_used(platform, n)`；`expire_by_digest(platform, current_digest)`；`purge_archived`。
- 单飞锁表：`acquire/renew/release`（CAS `owner`+`locked_until`，**短事务，不跨 LLM**）。
**验收**（`tests/test_discovery_keywords.py`）：
- 并发 claim 不重复领取（模拟两 claim 同批，各拿不相交集合）。
- 租约回收把卡住的 `claimed/executing` 收回 `pending`。
- 部分唯一：在途态禁重复插；`used/expired` 不挡同词再插（回收可行）。
- `expire_by_digest` 只作废旧 digest 的 `pending`，保留 `used/executing`。
- 单飞锁：持锁期间二次 acquire 失败；`locked_until` 到期可抢；release 后可抢。

## P1.2 — `profile_kw_digest`
**目标**：路径无关、量化、覆盖慢变关键词字段的稳定 digest（spec §8）。
**文件**：`src/openbiliclaw/discovery/keyword_digest.py`（新）。
**动作**：`profile_kw_digest(profile) -> str`——对 interests top-K(名+category+**量化权重**)、interest_domains 名、disliked_topics、core_traits、values、motivational_drivers、current_phase、cognitive_style、style 粗粒度做规范化 hash；**排除** `recent_awareness`/`active_insights`；确定性排序 + `sort_keys`。
**验收**（`tests/test_keyword_digest.py`）：单事件小权重漂移 → digest 不变；新增强兴趣/避雷项变 → digest 变；字段顺序无关、可复现；不读 awareness/insights。

## P1.3 — XHS / X 确定性兜底
**目标**：给现在"失败/无兴趣返回空"的 XHS、X 补确定性兴趣名兜底（spec §3/§14，planner 依赖它）。
**文件**：`src/openbiliclaw/sources/xhs_keyword_gen.py`、`src/openbiliclaw/discovery/strategies/x.py`。
**动作**：LLM 不可用/失败/空 → 回退取 `profile.preferences.interests` 名（对齐 `douyin_direct`/`search`/`youtube` 现有兜底）。
**验收**（扩 `tests/test_xhs_keyword_gen.py`、`tests/test_x_strategies.py`）：无 LLM/LLM 抛错时两者返回兴趣名而非空；有兴趣才回退、无兴趣返回空。

## P1.4 — 合并关键词 prompt builder + 解析
**目标**：一次调用、画像发一次、按平台分块（spec §7.1/§7.2）；池子分布按平台带入。
**文件**：`src/openbiliclaw/llm/prompts.py`（pool 快照复用 `discovery/pool_snapshot.py::build_pool_distribution_snapshot`，`pool_snapshot.py:41`——不在 `_utils.py`）。
**动作**：
- `build_merged_keywords_prompt(*, profile_summary, platform_blocks)`：**静态 system**（全平台共用，含"按平台 key 输出 JSON / 不重复 recent_keywords / 避开 avoid_* / 平台原生词风格"）；user = `<profile_summary>`(一次) + `<platforms>`(仅 due 平台块：`need/recent_keywords/avoid_topics/avoid_styles/avoid_franchises`)；`json.dumps(..., ensure_ascii=False, indent=2, sort_keys=True)`。
- 解析 `{platform: [words]}`，容忍缺平台/部分。
- 每平台块的 `avoid_*` 来自 `to_prompt_hints()`（先全局、`prefer_axes` 维持空）。
**验收**（扩 `tests/test_llm_prompts.py`）：纳入 `test_prompt_builder_system_messages_are_call_invariant`（system 静态）；user 含 profile 一次 + 仅 due 平台；输出按 key 解析；缺块/坏 JSON 不崩。

## P1.5 — 策略关键词注入口（§7.4）
**目标**：让生成好的词能喂回各 search 路径（调用链一路透传 `keywords`）。
**文件**：`discovery/engine.py`(`_call_strategy_discover`)、`discovery/candidate_pipeline.py`、各 `runtime/*_producer.py`、`discovery/strategies/{search,youtube,x,douyin_direct}.py`、`sources/twitter_adapter.py`、`sources/xhs_keyword_gen.py` 调用方。
**动作**：
- B站 `SearchStrategy.discover` 加 `queries: list[str]|None`（传入则跳过内部 LLM 生成）。
- YouTube `YoutubeSearchStrategy` 加 `queries=`。
- X `XSearchStrategy` 接受多词（现 `query` 单个 → 列表）。
- 抖音：planner 写 `seed_keywords`；并修 `_keywords` **仅当 `'search' in sources`** 才取词。
- engine kwargs / `SourceRecipe.config` / producer loop / adapter 一路条件透传 `keywords`（像 `pool_snapshot`）。
- **每个 search 关键词调用点显式 flag 分支**（Codex R1 Major）：`flag on`→注入 store 的词、跳过内部 LLM 生成；`flag off`→旧内部生成**逐字不变**。5 个站点（B站 search / `xhs_producer` / `douyin_direct` / `youtube` / `x`）各自 gate；`run_forever` 的 producer loop（`refresh.py:910,1266`）照常跑，只在调用点按 flag 选路径。
**验收**：各 strategy 给定注入词 → 只搜这些、**不调 LLM**；非 search 子来源不受影响；抖音 hot/feed-only 模式不再生成 search 词；**flag-off 回归**：每个站点 flag 关闭时行为与改前逐字一致（5 站点各加 flag-off 回归测试）（扩 `tests/test_{search,x,douyin_direct}_strateg*`、`test_*_producer`）。

## P1.6 — 关键词规划器 `_loop_keyword_planner`
**目标**：缺口拉动的合并生成 + 单飞 + digest 失效 + 兜底（spec §5.2）。
**文件**：`src/openbiliclaw/runtime/refresh.py`（新 `_loop_keyword_planner`，注册进 `run_forever` ~`refresh.py:885`）、`src/openbiliclaw/api/runtime_context.py`（构造装配 ~639/659 —— **是 `api/` 下，不是 `runtime/runtime_context.py`**）。
**动作**：每 `planner_poll` 轮——
1. `due` = {缓存 pending<low **且** 真实缺口>0（**含 raw headroom + 在途**，复用现有补池口径）}；**B站 额外催化**：池低于目标 / ≥6 信号 也进 due。
2. due 非空 → 现算各平台 `avoid_*` + 取历史窗口 → 取**单飞锁**（CAS，**释放事务后**再调 LLM）→ `build_merged_keywords_prompt` → 写 `pending`（带当前 `profile_kw_digest`）补到 high → 释放锁。
3. digest 变 → 先 `expire_by_digest`。
4. 失败/缺平台块 → 该平台确定性兴趣名（P1.3 已备）。
5. **装配（Codex R1 Major）**：`ContinuousRefreshController` 无 LLM 字段（`refresh.py:225`）、runtime 也没传给它（`api/runtime_context.py:643`）。故 planner 作为**独立协作对象**在 `api/runtime_context.py`（~639/659）构造（持 `llm_service`+db+config），传入 controller；`run_forever` 加 `asyncio.create_task(self._keyword_planner.run())`。不让 controller 自己持 LLM。
**验收**（`tests/test_keyword_planner.py`）：冷启动多平台 → **一次合并调用**含所有 due；池满 → 无 due → 零调用；digest 变 → 作废旧 pending + 重生成；单飞下无并发生成；LLM 失败 → 兜底入库；B站 ≥6 信号即使缓存未见底也 due。

## P1.7 — 缺口驱动抓取 + 词生命周期（仅 `used`，yield 归 P1.8）
**目标**：各平台 search 抓取改"从 store 领词"，**三种执行形态**的 `used`/`failed`/`executing` 终态正确（spec §5.1/§11，按 Codex R1 细化——X/YT 是 fetch-only、不在 producer 同步入池）。
**文件**：各 search 抓取点（`refresh.py` B站、`x_producer.py`/`youtube_producer.py`/`douyin_producer.py`、`douyin_direct.py`）+ XHS task-result handler（`api/app.py`）。
**动作**：
- 抓取闸：距上次≥floor 且 缺口>0 且 有可 claim 词 → 原子 claim `fetch_batch` 个 → 经 P1.5 注入口抓。
- **内联评估并入池**（B站 search、抖音 plugin：抓→评估→admit 都在本调用内）：返回即 `used`；抓取异常/空→`failed`。
- **fetch-only → 交共享 pipeline 延后入池**（**X、YouTube** producer 只取 raw、不在 producer admit；`x_producer.py:190,199`）：raw 候选交 `discovery_candidates` 后即 `used`（词已被消费；admit 由 candidate_pipeline 后续做）。
- **真正异步**（**仅小红书**：扩展 out-of-band）：claim→enqueue（带 `source_keyword_id`）→词 `executing`→task-result 回调→`used`/`failed`。
- **claim 后 enqueue 被预算拒**（`sources/xhs_tasks.py:259`、`sources/dy_tasks.py:359`：claim 成功但 budget 拒、无任务生成，Codex R1 Major）→ 词 `claimed→pending` 回滚（连续超 `attempts`→`failed`）。
- **回滚前提：预算拒须有可区分信号**（Codex R2 Major）：XHS enqueue 返回 `ok=False` 可区分；但**抖音 plugin `search_aweme`（`douyin_plugin_search.py:168-186`，及 queue 预算路径 :120-125）现在预算耗尽返回 `[]`，与"真·空结果"无法区分**——会让回滚在抖音腿上静默失效。P1.7 须让 `search_aweme` 在预算拒时 surface **可区分结果**（sentinel 异常 / typed 结果联合），调用方据此区分"空"与"预算拒"再决定回滚（否则该词被误当"搜过即空"标 `used`、白烧）。
- `reclaim_leased` 定期跑。**快环不触发 planner**。**yield 不在本阶段**——全部归 P1.8 在 admit 时按 `source_keyword_id` 回填，与 `used` 解耦。
**验收**（扩 producer/集成测试）：仅在缺口+floor 满足时抓；三形态 `used`/`failed`/`executing` 流转正确；X/YT 交 pipeline 后即 `used`（**不**在 producer admit、**不**在此回填 yield）；XHS `executing→used` 仅在任务完成回调；enqueue 预算拒 → 词回 `pending`；`failed` 重试；池满即停抓；floor 生效；**非 search 子来源不受 keyword store 影响**。

## P1.8 — yield 端到端（`source_keyword_id`）
**目标**：实测产出回填 + 枯竭词退役（spec §5.3）。
**文件**：`storage/database.py`（candidate/cache 加列）、`discovery/candidate_pool.py`、各任务 payload（`sources/xhs_tasks.py:195`/`sources/dy_tasks.py:310` —— **在 `sources/` 下，不是 `runtime/`**）、`discovery/engine.py`/`x_normalize.py` 等 normalized 候选、admission 钩子（`candidate_pipeline.py`）、`api/app.py`（XHS/抖音 task-result）。
**动作**：
- 各处加 `source_keyword_id` 并透传：claim→抓取/enqueue→候选→admit。
- **yield 统一在 admit 回填**（与 `used` 解耦，覆盖三形态）：内联 admit（B站/抖音）、候选 pipeline admit（X/YT/XHS）成功 → `yield_count+=1`，按 `(source_keyword_id, content_id)` **幂等**（容忍部分/乱序/重试）。
- **抖音回流缺口**：确认/补"抖音 search 结果回流 discovery_candidates"（spec §5.3，二选一明确）。
- 连续 0 产出词 → `expired`/冷却。
**验收**（`tests/test_keyword_yield.py` + 集成）：入池内容给来源词 +yield；重试/乱序不重复计；0 产出词被退役不再轮换；抖音 search 候选带 keyword id 进池。

## P1.9 — Cutover + flag + 成本归因 + docs
**目标**：切到统一路径、保住成本可观测、文档同步。
**文件**：各 search 生成调用点（删/绕过旧逐平台 LLM 生成，走 store）、`llm/usage_recorder.py`（或 builder 调用处）、`docs/modules/{discovery,config}.md`、`docs/changelog.md`、`README` 架构图（若数据流变）。
**动作**：
- flag 打开时：5 个 search 关键词改由 planner+store 供给；旧逐平台 LLM 生成路径保留但不走（可回退）。
- **成本归因（Codex R1 Minor 校正）**：合并调用是**一次 response**，token 无法在平台间拆分（`usage_recorder.py:81` 一 response 一 caller）→ 记**单一 caller** `discovery.keyword_planner`；per-platform 可观测靠 **planner ledger/日志**（每次每平台关键词数 + 后续 yield 计数），**不冒充** token 级平台归因。
- docs：discovery 模块文档加"统一关键词 planner/背压"小节 + 架构数据流；changelog 加条目；config 文档补字段。
**验收**：flag 开 → 逐平台 search 关键词 LLM 调用塌成 planner 合并调用、**search 关键词生成总 LLM 成本较基线下降**（按 caller `discovery.keyword_planner` 验证）；planner ledger 有每平台关键词/yield 计数；flag 关 = 旧行为；`ruff`/`mypy`/全量非集成测试绿。

---

## 2. 跨阶段测试（spec §15 汇总）
- 单元：claim/lease/部分唯一/回收/expire（P1.1）；digest 量化/路径无关（P1.2）；XHS/X 兜底（P1.3）；合并 builder call-invariant+解析（P1.4）；planner 真值表（P1.6）；yield 幂等/退役（P1.8）。
- 集成：冷启动填满→停；消费→重启；池满全停零调用；限流地板；**非 search 子来源不受影响**；抖音 hot/feed-only 不生成 search 词；XHS 异步终态回写；digest 变即时换词；xhs/dy 任务队列与 store `used`/yield 跨表对账。
- 成本：`cost --by caller` 改前后调用数 + 平台归因。

## 3. 回滚
任一阶段问题 → `unified_keyword_planner_enabled=false` 即回旧逐平台生成（P1.1–P1.8 都是加法、旧路径保留到 P1.9）。表/列保留无害。

## 4. 明确不在 P1（→ P2/P3）
平台供给优势静态表 + 弃权（P2）；关键词池轮换打磨（P2）；按平台饱和粒度（P2）；动态缓存上限（缺口÷yield）/数据驱动供给（P3）；trending/explore 并入（P3）；`prefer_axes` 正向补缺（已决策屏蔽，不做）。

## 5. 验收总闸（P1 完成定义）
flag 开启下：① 5 平台 search 关键词由**一次合并调用**供给、画像发一次（成本按 caller 可见下降）；② 每轮搜的词与历史窗口不重复；③ 池满全停、消费即续；④ 三形态（内联/fetch-only/异步）终态正确、无烧词；⑤ yield 回填 + 枯竭退役生效；⑥ 非 search 子来源与既有评估/入池/限流/预算行为不变；⑦ 全量非集成测试 + ruff + mypy 绿；⑧ docs/changelog 同步。

## 6. Review log
- **R1（Codex，续接 spec 线程，2026-06-14）**：CHANGES REQUIRED。已逐条吸收——
  - Critical：X producer 是 fetch-only、无 producer 内 admit hook（`x_producer.py:190,199`）→ P1.7 把 X 从"内联 admit"改为"fetch-only→交 pipeline 延后 admit"，`used`=交付即标、yield 在 admit 回填。
  - Major：P1.7 要求 used+yield 但 yield 管线在 P1.8 → **拆分**，P1.7 只管 `used`/`failed`/`executing`，yield 全归 P1.8。
  - Major：flag-off 回滚未保证 → P1.5 每个 search 调用点加显式 flag 分支 + flag-off 回归测试。
  - Major：P1.6 `runtime/runtime_context.py` 不存在 → 改 `api/runtime_context.py`（~639/659），loop 注册在 `refresh.py:885`。
  - Major：P1.8 task payload 路径错 → `runtime/*` 改 `sources/xhs_tasks.py:195`、`sources/dy_tasks.py:310`。
  - Major：planner 缺 LLM 装配（`ContinuousRefreshController` 无 LLM 字段 `refresh.py:225`）→ P1.6 加"planner 作独立对象在 `api/runtime_context.py` 构造持 llm_service、传入 controller"。
  - Major：claim 后 enqueue 被预算拒导致词卡住 → P1.7 加 `claimed→pending` 回滚（超 attempts→failed）。
  - Minor：P1.0 config 是 dataclass 非 Pydantic；P1.4 pool 快照在 `pool_snapshot.py` 非 `_utils.py`；P1.9 成本一 response 一 caller、改为单 planner caller + ledger，不冒充平台 token 归因。
- **注**：本轮把 X/YT 由 spec §5.1/§11 的"内联"细化为"fetch-only→pipeline 延后 admit"。spec 那两节措辞建议同步（X/YT 归 fetch-only 类；`used`=交付、`yield`=admit 回填、与平台无关）——待 owner 决定是否回改 spec。
- **R2（Codex，2026-06-14）**：CHANGES REQUIRED（1 Major）。抖音 plugin `search_aweme` 预算耗尽返回 `[]`、与真空结果无法区分 → 回滚静默失效 → P1.7 加"预算拒须有可区分信号（sentinel/typed union）"要求。其余 R1 项全部确认解决、文件路径全部存在。
